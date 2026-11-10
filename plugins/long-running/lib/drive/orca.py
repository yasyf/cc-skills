import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from drive.state import DriveError

LAUNCHER = "orca-worker-launch.sh"
RECLAIMER = "orca-reclaim.py"
MODEL = "claude-opus-5-5"
EFFORT = "xhigh"
LANE_LIMIT = 55
NOT_LANE = re.compile(r"[^a-z0-9-]+")
PLUGIN = Path(__file__).resolve().parents[2]
BUILDER = PLUGIN / "agents" / "builder.md"
LANDING = PLUGIN / "skills" / "long-running" / "reference" / "landing.md"
LANE_ENV = "CLAUDE_CODE_AUTO_COMPACT_WINDOW=300000 ENABLE_PROMPT_CACHING_1H=1"
LAUNCH_SECONDS = 1800
MAIL_LIMIT = "100"
CALL_SECONDS = 60


class OrcaError(DriveError):
    pass


def request(*args: str) -> dict:
    """One `orca` call: its result, or OrcaError carrying Orca's own reason."""
    verb = " ".join(args[:2])
    try:
        done = subprocess.run(
            ["orca", *args, "--json"],
            capture_output=True,
            text=True,
            timeout=CALL_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OrcaError(f"orca {verb}: {error}") from None
    try:
        reply = json.loads(done.stdout)
    except json.JSONDecodeError:
        said = " ".join((done.stderr or done.stdout).split()) or f"exited {done.returncode}"
        raise OrcaError(f"orca {verb}: {said}") from None
    if not reply.get("ok"):
        error = reply.get("error") or {}
        raise OrcaError(f"orca {verb}: {error.get('code')}: {error.get('message')}")
    return reply["result"]


def call(*args: str) -> dict:
    return request("orchestration", *args, "--full")


def own_terminal() -> str | None:
    """This session's Orca terminal, or None outside one; tmux inherits the handle of the terminal it started in."""
    return None if "TMUX" in os.environ else os.environ.get("ORCA_TERMINAL_HANDLE")


def showing(handle: str, session: str) -> bool:
    """Whether Orca has a pane for the terminal, so switching to it lands there, and its process tree holds the session."""
    terminal = request("terminal", "show", "--terminal", handle)["terminal"]
    sweep = request("diagnostics", "memory")["worktrees"]
    pty = next((pty for worktree in sweep for pty in worktree["sessions"] if pty["sessionId"] == terminal["ptyId"]), None)
    if terminal["orphaned"] or not pty or not pty["paneKey"]:
        return False
    tree = parents()
    return any(holds(tree, pty["pid"], pid) for pid in claude_pids(session))


def claude_pids(session: str) -> list[int]:
    sessions = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "sessions"
    return [int(path.stem) for path in sessions.glob("*.json") if json.loads(path.read_text())["sessionId"] == session]


def parents() -> dict[int, int]:
    listing = subprocess.run(["ps", "-axo", "pid=,ppid="], capture_output=True, text=True, check=True).stdout
    return {pid: ppid for pid, ppid in (map(int, line.split()) for line in listing.splitlines())}


def holds(tree: dict[int, int], root: int, pid: int) -> bool:
    while pid > 1 and pid != root:
        pid = tree.get(pid, 0)
    return pid == root


def new_run(objective: str) -> str:
    """A fresh Run for one lane, bound to this terminal, so only this drive's watch reads its mail."""
    return call("run-create", "--objective", objective)["run"]["id"]


def lane_name(drive: str, job_id: str) -> str:
    return NOT_LANE.sub("-", f"{drive}-{job_id}".lower()).strip("-")[-LANE_LIMIT:].lstrip("-")


def builder_body() -> str:
    _, _, body = BUILDER.read_text().split("---", 2)
    return body.strip()


def vm_brief(drive: str, job_id: str, brief: str) -> str:
    """What a remote builder reads on a Sprite, which has no copy of this plugin: its role, landing rules, brief."""
    return (
        f"Remote lane: drive {drive}, job {job_id}. You are the long-running builder below, running as an Orca "
        'worker on a Sprite, so its "On a Sprite" section applies.\n\n'
        f"{builder_body()}\n\n"
        f"# The landing reference of the long-running skill\n\n{LANDING.read_text().strip()}\n\n"
        f"# Brief\n\n{brief.strip()}\n"
    )


def tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise OrcaError(f"{name} is not on PATH; install the orca plugin (orca@skills)")
    return path


def launch(lane: str, brief: Path, checkout: Path, run: str) -> dict:
    """Start one remote builder through the orca plugin's launcher; its success line, with the prepared server."""
    try:
        done = subprocess.run(
            [tool(LAUNCHER), lane, MODEL, EFFORT, str(brief)],
            cwd=checkout,
            env={**os.environ, "WORKER_RUN": run, "WORKER_ENV": LANE_ENV},
            capture_output=True,
            text=True,
            timeout=LAUNCH_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        attempt = brief.with_suffix(".worker")
        raise OrcaError(f"{LAUNCHER} ran past {LAUNCH_SECONDS} s; its attempt is in {attempt}") from None
    lines = done.stdout.strip().splitlines()
    if done.returncode or not lines:
        said = done.stderr.strip().splitlines()
        reason = said[-1] if said else "no output"
        attempt = brief.with_suffix(".worker")
        if (attempt / "worker-start.status").exists():
            raise OrcaError(
                f"{LAUNCHER} exited {done.returncode} after worker-start ran, so the lane may be live: {reason}; "
                f"read {attempt}/worker-start.json before starting it anywhere else"
            )
        raise OrcaError(f"{LAUNCHER} exited {done.returncode} before a worker started: {reason}; attempt in {attempt}")
    receipt = json.loads(lines[-1])
    prepared = json.loads((brief.with_suffix(".worker") / "prepared.json").read_text())
    return {**receipt, "environment": prepared["environment"]}


def reclaim(run: str, dispatch: str, checkout: Path) -> str:
    done = subprocess.run(
        [tool(RECLAIMER), "--run", run, "--done", dispatch],
        cwd=checkout,
        capture_output=True,
        text=True,
        timeout=LAUNCH_SECONDS,
        check=False,
    )
    if done.returncode:
        said = done.stderr.strip().splitlines()
        raise OrcaError(f"{RECLAIMER} exited {done.returncode}: {said[-1] if said else 'no output'}")
    return done.stdout.strip()


def mail(run: str) -> list[dict]:
    """The Run's newest mail, oldest first, read without binding or consuming: this terminal binds one Run at a time, and the watch reads every lane's."""
    messages = call("inbox", "--terminal", f"run:{run}", "--limit", MAIL_LIMIT)["messages"]
    return sorted((message for message in messages if message["to_handle"] == f"run:{run}"), key=lambda message: message["sequence"])


def worker(dispatch: str) -> dict:
    return call("worker-show", "--dispatch", dispatch)


def reply(run: str, message_id: str, body: str) -> None:
    """Answer a lane's question, binding its Run first: Orca takes a coordinator's reply only on the one Run its terminal is bound to, and each launch binds another."""
    call("run-use", "--id", run)
    call("reply", "--id", message_id, "--run", run, "--body", body)


def answer(job: dict, text: str) -> str:
    """Reply to the lane's open question, or send to its Dispatch when it asked nothing."""
    if job.get("ask"):
        reply(job["run"], job["ask"], text)
        return f"answered its question {job['ask']}"
    call(
        "send",
        "--to",
        f"dispatch:{job['dispatch']}",
        "--run",
        job["run"],
        "--subject",
        "From the coordinator",
        "--body",
        text,
    )
    return f"sent to dispatch {job['dispatch']}"
