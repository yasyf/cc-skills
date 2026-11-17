"""An owner session: started with `claude --bg` from the lane file, its lease read from the records Claude Code's own process keeps, and stopped only when the supervisor started it."""

import json
import os
import re
import shlex
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lane import store
from lane import session as shapes

BRIEF = Path(__file__).with_name("owner.md")
SHADOW = Path(__file__).with_name("shadow.md")
ENDED = ("stopped", "done", "failed")
TOOL_SECONDS = 600
STALLED_AFTER = timedelta(minutes=30)
OLD_DRIVE_DENY = ["Bash(drive:*)", "Bash(drive *)", "mcp__plugin_long-running_drive", "Bash(cci post:*)", "Bash(cci post *)", "mcp__plugin_cc-inbox_cci__post"]
OLD_DRIVE_CALL = r"(^|[\s;&|(/])drive\s"
SHADOW_DENY = [
    "Bash(git push:*)",
    "Bash(git commit:*)",
    "Bash(jj git push:*)",
    "Bash(gh pr merge:*)",
    "Bash(gh pr comment:*)",
    "Bash(gh pr review:*)",
    "Bash(gh pr create:*)",
    "Bash(gh pr edit:*)",
    "Bash(gh pr close:*)",
    "Bash(gh issue:*)",
    "Bash(ccx vcs ship:*)",
    "Bash(ccx vcs stack:*)",
    "Bash(cc-slack send:*)",
    "Bash(cc-slack reply:*)",
    "Bash(cc-slack dm-status:*)",
    "Bash(cc-slack react:*)",
    "Bash(cci post:*)",
    "Bash(drive:*)",
    "Bash(bk:*)",
    "Edit",
    "Write",
    "NotebookEdit",
]


def config_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def record(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def session_pid(session: str) -> int | None:
    """The pid of the process Claude Code runs a session in, found once by its session record."""
    for path in (config_dir() / "sessions").glob("*.json"):
        if path.stem.isdigit() and (found := record(path)) and found.get("sessionId") == session:
            return int(path.stem)
    return None


def process_started(pid: int) -> str | None:
    done = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, check=False, env=os.environ | {"TZ": "UTC"})
    return " ".join(done.stdout.split()) or None


def lease(owner: dict, moment: datetime, grace_seconds: int) -> str:
    """'held' while the owner's process runs and works, 'starting' inside the launch grace, else 'lapsed' with no wait: the harness process holds the lease, never the model."""
    started = store.parse(owner["started"])
    starting = "starting" if (moment - started).total_seconds() < grace_seconds else "lapsed"
    if not owner.get("session"):
        return starting
    job = record(config_dir() / "jobs" / owner["job"] / "state.json")
    if job is None or job.get("state") in ENDED:
        return "lapsed"
    pid = owner.get("pid")
    if pid is None:
        return starting
    kept = record(config_dir() / "sessions" / f"{pid}.json")
    if not kept or kept.get("sessionId") != owner["session"]:
        return "lapsed"
    try:
        os.kill(pid, 0)
    except OSError:
        return "lapsed"
    if owner.get("proc_start") and process_started(pid) != owner["proc_start"]:
        return "lapsed"
    if kept.get("status") != "idle":
        return "held"
    flight = job.get("inFlight") or {}
    if not (flight.get("tasks") or flight.get("queued")):
        return "lapsed"
    return "held" if (last := active_at(owner)) and moment - last < STALLED_AFTER else "lapsed"


def active_at(owner: dict) -> datetime | None:
    """When the owner's session last moved: the newest write to its transcript, its builders' transcripts or its background tasks' output, so an idle owner whose builders or tasks have written nothing for STALLED_AFTER holds a hung call, not work, and its lease lapses."""
    if not (found := transcript(owner["session"])):
        return None
    tasks = Path("/tmp") / f"claude-{os.getuid()}" / found.parent.name / owner["session"] / "tasks"
    paths = [found, *found.with_suffix("").glob("subagents/*.jsonl"), *(tasks.glob("*") if tasks.is_dir() else [])]
    return datetime.fromtimestamp(max(path.stat().st_mtime for path in paths), UTC)


def own_quiet_since(owner: dict) -> datetime | None:
    """Since when the owner's own transcript has had no write: its builders' transcripts and tasks never count, since an owner waiting on its builders reads as busy to its session record while it reads nothing of his."""
    found = transcript(owner["session"]) if owner.get("session") else None
    return datetime.fromtimestamp(found.stat().st_mtime, UTC) if found else None


def quiet_since(owner: dict) -> datetime | None:
    """Since when the owner's session has been between turns, idle or waiting on its own builders, from its session record; None while it works or before its record exists."""
    kept = record(config_dir() / "sessions" / f"{owner['pid']}.json") if owner.get("pid") else None
    if not kept or kept.get("sessionId") != owner.get("session") or kept.get("status") == "busy":
        return None
    return datetime.fromtimestamp(kept["statusUpdatedAt"] / 1000, UTC)


def deliver(owner: dict, text: str) -> None:
    """Put text before the owner's session as a message from another of his sessions, which starts a turn in a session between turns, so words reach an owner whose hooks would only show them at a tool call it is not making: by the session name its job carries, through send."""
    job = record(config_dir() / "jobs" / owner["job"] / "state.json") if owner.get("job") else None
    if job and job.get("name"):
        send(job["name"], text)


def send(to: str, text: str) -> None:
    """A one-call `claude -p` in the owners' permission class sends text to the session or agent named to with SendMessage, in the background."""
    prompt = f'Call the SendMessage tool exactly once, with to set to "{to}" and message set to the text between the markers, verbatim. Do nothing else.\n<<<\n{text}\n>>>'
    subprocess.Popen(shapes.argv(store.DEFAULTS, "acker", store.root(), {}, ["-p"], ["--permission-mode", "bypassPermissions", "--", prompt]), cwd=store.root(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def in_flight(owner: dict) -> int:
    """How many background agents and tasks the owner's own session runs now, from its job's record."""
    job = record(config_dir() / "jobs" / owner["job"] / "state.json") if owner.get("job") else None
    flight = (job or {}).get("inFlight") or {}
    return (flight.get("tasks") or 0) + (flight.get("queued") or 0)


def located(owner: dict) -> dict:
    """The owner with the pid and process start of its session, once its session record exists."""
    if owner.get("pid") or not owner.get("session"):
        return owner
    if (pid := session_pid(owner["session"])) is None:
        return owner
    return {**owner, "pid": pid, "proc_start": process_started(pid)}


def standing(pr: dict) -> str:
    """Where one PR stands by its row alone, which every other line about it yields to: his Approve, his Hold, the tooling mark, and whether a staging test comes before its merge."""
    approval = "held by him: " + pr["held"] if pr.get("held") else f"approved by him at {pr['approved_head'][:7]}" if pr.get("approved_head") else "lands on green with no Approve, as tooling" if (pr.get("guarded") or {}).get("tooling") else "no Approve of his yet"
    premerge = (pr.get("dangerous") or {}).get("classes")
    return approval + (f"; staging before merge: {', '.join(premerge)}" if premerge else "; staging after merge" if pr.get("dangerous") else "; not judged for staging yet")


def brief(drive: str, lane: dict, woke: str) -> str:
    unanswered = store.unreplied(lane)
    asks = "\n".join(f"- {ask['at']} (ts {ask.get('ts') or 'none'}){' UNANSWERED' if not ask.get('replied') else ''}{' done' if ask.get('done') else ''}: {ask['words']}" + (f" ({ask['link']})" if ask.get("link") else "") for ask in lane.get("asks", [])[-12:])
    rulings = "\n".join(f"- {row['at']} on {row['binds']}: \"{row['words']}\"" + (f" ({row['link']})" if row.get("link") else f" (relayed by {row['relayed_by']})" if row.get("relayed_by") else "") + (f"\n  A reading, not his words: {row['reading']}" if row.get("reading") else "") for row in [*store.shared_rulings(drive), *lane.get("rulings", [])]) or "- none"
    prs = "\n".join(f"- {pr['repo']}#{pr['number']}" + (f" {pr['words']}" if pr.get("words") else "") + ": " + standing(pr) for pr in lane.get("prs", [])) or "- none yet"
    done = "\n".join(f"- {number}. " + (f"met {check['met_at']} ({check.get('how')})" if check.get("met_at") else "not met") + f": {check['words']}" for number, check in enumerate(lane.get("done", []), 1)) or "- none written yet"
    serves = lane.get("serves") or {}
    text = BRIEF.read_text().format(
        id=lane["id"],
        done=done,
        serves=f"done check {serves['check']}" if serves.get("check") else f"no done check: {serves['none']}" if serves.get("none") else "not named",
        drive=drive,
        title=lane["title"],
        path=store.lane_path(drive, lane["id"]),
        asks=asks or "- none",
        new=len(unanswered),
        rulings=rulings,
        prs=prs,
        next=lane.get("next") or "not written yet",
        woke=woke,
        log="\n".join(f"- {line}" for line in store.log_tail(drive, lane["id"], 8)) or "- nothing yet",
        promise=json.dumps(lane.get("promise")) if lane.get("promise") else "none",
        handover=lane.get("handover") or "none",
        targets=", ".join(store.config(drive)["staging_targets"]),
        lane=shlex.quote(str(store.launcher(drive))),
    )
    if not store.live(drive):
        text += "\n\n" + SHADOW.read_text().format(id=lane["id"], lane=shlex.quote(str(store.launcher(drive))))
    return text


def settings(drive: str, lane_id: str, token: str) -> dict:
    token_file = store.token_path(drive, lane_id)
    env = {"LANE_OWNER": f"{drive}/{lane_id}", "LONG_RUNNING_LANE": f"{drive}/{lane_id}", "LANE_TOKEN": token, "LANE_TOKEN_FILE": str(token_file), "LANE_NOTE_FILE": str(store.note_path(drive, lane_id)), "LANE_ASKS_FILE": str(store.asks_path(drive, lane_id)), "BASH_MAX_TIMEOUT_MS": str(TOOL_SECONDS * 1000), "MCP_TOOL_TIMEOUT": str(TOOL_SECONDS * 1000)}
    if explicit := os.environ.get("LONG_RUNNING_STATE"):
        env["LONG_RUNNING_STATE"] = explicit
    fence = '[ -z "$LANE_TOKEN_FILE" ] || { read -r held < "$LANE_TOKEN_FILE" 2>/dev/null; [ "$held" = "$LANE_TOKEN" ] || { echo "This lane has a new owner or none; you are fenced off. End your turn now." >&2; exit 2; }; }'
    shaped = {
        "env": env,
        "hooks": {
            "Stop": [{"hooks": [{"type": "command", "command": f"{shlex.quote(str(store.launcher(drive)))} hook stop"}]}],
            "PreToolUse": [
                {"matcher": "Bash|Edit|Write|NotebookEdit", "hooks": [{"type": "command", "command": fence}]},
                {"matcher": "Bash", "hooks": [{"type": "command", "command": f"{shlex.quote(str(store.launcher(drive)))} hook bash"}]},
                {"matcher": "", "hooks": [{"type": "command", "command": f'[ ! -s "$LANE_NOTE_FILE" ] && [ ! -e "$LANE_ASKS_FILE" ] || exec {shlex.quote(str(store.launcher(drive)))} hook note'}]},
            ],
        },
    }
    shaped["permissions"] = {"deny": OLD_DRIVE_DENY + ([] if store.live(drive) else SHADOW_DENY)}
    shaped["enabledPlugins"] = {key: False for key in old_drive_plugins()}
    shaped["crossSessionInbound"] = "accept"
    return shaped


def old_drive_plugins() -> list[str]:
    """Each installed long-running plugin's key, which an owner's session runs with off, so the old drive's skill, MCP tools and hooks never steer it; its lane and lane-land verbs run from the drive's launcher, not the plugin."""
    listed = record(config_dir() / "plugins" / "installed_plugins.json") or {}
    return [key for key in listed.get("plugins", {}) if key.startswith("long-running@")]


def launched(out: str) -> tuple[str, str]:
    printed = re.search(r"^backgrounded\W+([0-9a-f]{8})\b", out, re.MULTILINE)
    if not printed or not (job := record(config_dir() / "jobs" / printed[1] / "state.json")):
        raise store.Refused(f"claude --bg named no background job: {out.strip()[:300] or 'no output'}")
    return printed[1], job["sessionId"]


def cwd(drive: str, lane: dict) -> Path:
    if not lane.get("worktree"):
        raise store.Refused(f"lane {lane['id']} has no worktree for its owner to start in; set one with `lane worktree`")
    return Path(lane["worktree"])


RESUME_WITHIN = timedelta(minutes=30)
RESUME_BYTES = 400_000


def transcript(session: str) -> Path | None:
    return next((config_dir() / "projects").glob(f"*/{session}.jsonl"), None)


def resumable(lane: dict, moment: datetime) -> str | None:
    """The session of the lane's last owner when it ended its turn under RESUME_WITHIN ago and its transcript is under RESUME_BYTES, which a wake resumes in place of a fresh start to save its re-read; else None."""
    last = lane.get("last_owner") or {}
    if not last.get("session") or not last.get("ended") or moment - store.parse(last["ended"]) > RESUME_WITHIN:
        return None
    kept = transcript(last["session"])
    return last["session"] if kept and kept.stat().st_size < RESUME_BYTES else None


def launch(drive: str, lane: dict, token: str, woke: str) -> dict:
    """Start the lane's owner as its own background session, resuming its last owner's session when that is fresh and small and starting fresh when it is not or the resume fails; the job and session it runs as."""
    if session := resumable(lane, datetime.now(UTC)):
        try:
            return {**started(drive, lane, token, ["--resume", session], f"You own this lane again with a new token. {woke}\n\n{brief(drive, lane, woke)}"), "resumed": session}
        except store.Refused:
            pass
    return started(drive, lane, token, [], brief(drive, lane, woke))


def started(drive: str, lane: dict, token: str, resume: list[str], prompt: str) -> dict:
    shape = store.config(drive)
    argv = shapes.argv(shape, "owner", cwd(drive, lane), settings(drive, lane["id"], token), ["--bg", *resume], ["--permission-mode", "bypassPermissions", "-n", f"lane {drive} {lane['id']}", prompt])
    env = {key: value for key, value in os.environ.items() if key not in ("CLAUDE_CODE_SESSION_ID", "LANE_OWNER", "LONG_RUNNING_LANE", "LANE_TOKEN", "LANE_TOKEN_FILE", "LONG_RUNNING_JOB", "CLAUDE_LONG_RUNNING_LANE")}
    done = subprocess.run(argv, cwd=cwd(drive, lane), env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False, timeout=120)
    if done.returncode:
        raise store.Refused(f"claude --bg exited {done.returncode}: {(done.stderr or done.stdout).strip()[:300]}")
    job, session = launched(done.stdout)
    return {"job": job, "session": session, "launched": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}


def stop(owner: dict) -> None:
    """Stop a background job the supervisor itself started for an owner, a moment after the caller returns."""
    if job := owner.get("job"):
        subprocess.Popen(["sh", "-c", 'sleep 2; claude stop "$1"', "stop", job], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
