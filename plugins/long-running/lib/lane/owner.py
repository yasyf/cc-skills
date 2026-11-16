"""An owner session: started with `claude --bg` from the lane file, its lease read from the records Claude Code's own process keeps, and stopped only when the supervisor started it."""

import json
import os
import re
import shlex
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from lane import store

BRIEF = Path(__file__).with_name("owner.md")
SHADOW = Path(__file__).with_name("shadow.md")
BIN = Path(__file__).resolve().parents[2] / "bin" / "lane"
ENDED = ("stopped", "done", "failed")
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
    return "held" if flight.get("tasks") or flight.get("queued") else "lapsed"


def located(owner: dict) -> dict:
    """The owner with the pid and process start of its session, once its session record exists."""
    if owner.get("pid") or not owner.get("session"):
        return owner
    if (pid := session_pid(owner["session"])) is None:
        return owner
    return {**owner, "pid": pid, "proc_start": process_started(pid)}


def brief(drive: str, lane: dict, woke: str) -> str:
    unseen = [ask for ask in lane.get("asks", []) if not ask.get("seen")]
    asks = "\n".join(f"- {ask['at']}{' (new)' if not ask.get('seen') else ''}: {ask['words']}" + (f" ({ask['link']})" if ask.get("link") else "") for ask in lane.get("asks", [])[-12:])
    prs = "\n".join(f"- {pr['repo']}#{pr['number']}" + (f" held: {pr['held']}" if pr.get("held") else "") for pr in lane.get("prs", [])) or "- none yet"
    log = "\n".join(f"- {row['at']}: {row['text']}" for row in lane.get("log", [])[-8:]) or "- nothing yet"
    text = BRIEF.read_text().format(
        id=lane["id"],
        drive=drive,
        title=lane["title"],
        path=store.lane_path(drive, lane["id"]),
        asks=asks or "- none",
        new=len(unseen),
        prs=prs,
        next=lane.get("next") or "not written yet",
        woke=woke,
        log=log,
        promise=json.dumps(lane.get("promise")) if lane.get("promise") else "none",
        lane=shlex.quote(str(BIN)),
    )
    if store.config(drive)["shadow"]:
        text += "\n\n" + SHADOW.read_text().format(id=lane["id"], lane=shlex.quote(str(BIN)))
    return text


def settings(drive: str, lane_id: str, token: str) -> dict:
    token_file = store.token_path(drive, lane_id)
    env = {"LANE_OWNER": f"{drive}/{lane_id}", "LANE_TOKEN": token, "LANE_TOKEN_FILE": str(token_file)}
    if explicit := os.environ.get("LONG_RUNNING_STATE"):
        env["LONG_RUNNING_STATE"] = explicit
    fence = '[ -z "$LANE_TOKEN_FILE" ] || { read -r held < "$LANE_TOKEN_FILE" 2>/dev/null; [ "$held" = "$LANE_TOKEN" ] || { echo "This lane has a new owner or none; you are fenced off. End your turn now." >&2; exit 2; }; }'
    shaped = {
        "env": env,
        "hooks": {
            "Stop": [{"hooks": [{"type": "command", "command": f"{shlex.quote(str(BIN))} hook stop"}]}],
            "PreToolUse": [{"matcher": "Bash|Edit|Write|NotebookEdit", "hooks": [{"type": "command", "command": fence}]}],
        },
    }
    if store.config(drive)["shadow"]:
        shaped["permissions"] = {"deny": SHADOW_DENY}
    return shaped


def launched(out: str) -> tuple[str, str]:
    printed = re.search(r"^backgrounded\W+([0-9a-f]{8})\b", out, re.MULTILINE)
    if not printed or not (job := record(config_dir() / "jobs" / printed[1] / "state.json")):
        raise store.Refused(f"claude --bg named no background job: {out.strip()[:300] or 'no output'}")
    return printed[1], job["sessionId"]


def cwd(drive: str, lane: dict) -> Path:
    if lane.get("worktree"):
        return Path(lane["worktree"])
    folder = store.drive_dir(drive) / "work" / lane["id"]
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def launch(drive: str, lane: dict, token: str, woke: str) -> dict:
    """Start the lane's owner as its own background session; the job and session it runs as."""
    shape = store.config(drive)
    argv = [
        "claude",
        "--bg",
        "--model",
        shape["model"],
        "--effort",
        shape["effort"],
        "--permission-mode",
        "bypassPermissions",
        *(["--strict-mcp-config"] if shape["shadow"] else []),
        "--settings",
        json.dumps(settings(drive, lane["id"], token)),
        "-n",
        f"lane {drive} {lane['id']}",
        brief(drive, lane, woke),
    ]
    env = {key: value for key, value in os.environ.items() if key not in ("CLAUDE_CODE_SESSION_ID", "LANE_OWNER", "LANE_TOKEN", "LANE_TOKEN_FILE", "LONG_RUNNING_JOB", "CLAUDE_LONG_RUNNING_LANE")}
    done = subprocess.run(argv, cwd=cwd(drive, lane), env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False, timeout=120)
    if done.returncode:
        raise store.Refused(f"claude --bg exited {done.returncode}: {(done.stderr or done.stdout).strip()[:300]}")
    job, session = launched(done.stdout)
    return {"job": job, "session": session, "launched": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}


def stop(owner: dict) -> None:
    """Stop a background job the supervisor itself started for an owner, a moment after the caller returns."""
    if job := owner.get("job"):
        subprocess.Popen(["sh", "-c", 'sleep 2; claude stop "$1"', "stop", job], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
