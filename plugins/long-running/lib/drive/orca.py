import json
import os
import re
import subprocess
from pathlib import Path
from typing import NamedTuple

from drive import render, state
from drive.install import config_dir
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
LANE_ENV = "CLAUDE_CODE_AUTO_COMPACT_WINDOW=300000"
LAUNCH_SECONDS = 1800
MAIL_LIMIT = 100
FENCED = "consumer_fenced"
GONE = "dispatch_not_found"
FINISHED = ("succeeded", "failed", "stopped", "abandoned")
START_TIMEOUT_MS = "600000"
BRIEF_HEADING = "\n# Brief\n\n"
DRIVE = PLUGIN / "bin" / "drive"
CALL_SECONDS = 60
ON_MAC = "on this Mac"
RELOAD = "/reload-plugins"
SUBMIT_SECONDS = 10
TIMEOUT = "timeout"
ANSWERED_ELSEWHERE = "answer_conflict"


class OrcaError(DriveError):
    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class LaneEnd(NamedTuple):
    why: str
    branch: str | None
    worktree: str | None


def request(*args: str, seconds: float = CALL_SECONDS) -> dict:
    """One `orca` call: its result, or OrcaError carrying Orca's own reason."""
    verb = " ".join(args[:2])
    try:
        done = subprocess.run(
            ["orca", *args, "--json"],
            capture_output=True,
            text=True,
            timeout=seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OrcaError(f"orca {verb}: {error}") from None
    return result_of(verb, done)


def result_of(verb: str, done: subprocess.CompletedProcess) -> dict:
    try:
        reply = json.loads(done.stdout)
    except json.JSONDecodeError:
        said = " ".join((done.stderr or done.stdout).split()) or f"exited {done.returncode}"
        raise OrcaError(f"orca {verb}: {said}") from None
    if not reply.get("ok"):
        error = reply.get("error") or {}
        raise OrcaError(f"orca {verb}: {error.get('code')}: {error.get('message')}", error.get("code"))
    return reply["result"]


def call(*args: str) -> dict:
    return request("orchestration", *args, "--full")


def in_terminal() -> bool:
    """Whether this session runs in an Orca terminal, directly or in a tmux started in one."""
    return "ORCA_TERMINAL_HANDLE" in os.environ


def own_terminal() -> str | None:
    """This session's Orca terminal, or None outside one; tmux inherits the handle of the terminal it started in."""
    return None if "TMUX" in os.environ else os.environ.get("ORCA_TERMINAL_HANDLE")


def reload_command(terminal: str) -> str:
    return f"orca terminal send --terminal {terminal} --text {RELOAD} --enter"


def reload_plugins(terminal: str, wait_ms: int) -> bool:
    """Type /reload-plugins into a Claude Code session's terminal once its turn is over, or once wait_ms pass into its busy prompt, where Claude Code holds it until the turn ends; whether the session was idle, or OrcaError when it shows a prompt the text would answer."""
    waiting = ("terminal", "wait", "--terminal", terminal, "--for", "tui-idle", "--timeout-ms", str(wait_ms))
    try:
        waited = request(*waiting, seconds=wait_ms / 1000 + CALL_SECONDS)["wait"]
    except OrcaError as error:
        if error.code != TIMEOUT:
            raise
        waited = None
    if waited and not waited["satisfied"]:
        raise OrcaError(f"orca terminal wait: it shows {waited['blockedReason']}")
    request("terminal", "send", "--terminal", terminal, "--text", RELOAD, "--enter", "--wait-submit", str(SUBMIT_SECONDS))
    return waited is not None


def terminal(handle: str) -> dict:
    """Orca's record of one terminal; `orphaned` there means Orca dropped its pane, so switching to it shows nothing."""
    return request("terminal", "show", "--terminal", handle)["terminal"]


def holds_session(terminal: dict, session: str) -> bool:
    """Whether the terminal's process tree holds the session's Claude Code process, so switching to it lands there."""
    sweep = request("diagnostics", "memory")["worktrees"]
    pty = next((pty for worktree in sweep for pty in worktree["sessions"] if pty["sessionId"] == terminal["ptyId"]), None)
    if not pty:
        return False
    tree = parents()
    return any(holds(tree, pty["pid"], pid) for pid in claude_pids(session))


def main_checkout(worktree: str) -> Path:
    """The main checkout of the worktree's repository, the path Orca registers that repository by."""
    done = subprocess.run(
        ["git", "-C", worktree, "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode:
        raise OrcaError(f"{worktree} is in no git repository Orca can show: {done.stderr.strip()}")
    return Path(done.stdout.strip()).parent


def show_worktrees(checkout: Path) -> None:
    """Have Orca list the repository's worktrees it did not make, which it otherwise hides along with their terminals."""
    request("repo", "set", "--repo", f"path:{checkout}", "--external-worktree-visibility", "show")


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
    """A fresh Run, bound to this terminal beside the Runs it already holds."""
    return call("run-create", "--objective", objective)["run"]["id"]


def bind_drive_run(drive: str) -> None:
    run = state.load(drive).get("run")
    if not run:
        run = new_run(f"drive {drive}: the coordinator's own Run, which no lane mails")
        with state.edit(drive) as data:
            data["run"] = run
    call("run-use", "--id", run)


def lane_name(drive: str, job_id: str) -> str:
    return NOT_LANE.sub("-", f"{drive}-{job_id}".lower()).strip("-")[-LANE_LIMIT:].lstrip("-")


def builder_body() -> str:
    _, _, body = BUILDER.read_text().split("---", 2)
    return body.strip()


def place(job: dict) -> str:
    """Where the job's lane runs, as a line names it."""
    return ON_MAC if state.on_mac(job) else f"on Sprite {job['workspace']}"


def vm_brief(drive: str, job_id: str, brief: str, route: str | None) -> str:
    """What a builder started as an Orca worker reads, on a Sprite with no copy of this plugin or on this Mac: its role, landing rules, brief."""
    running = (
        'on this Mac, so it reports through Orca as its "On a Sprite" section says; the rest of that section is for a Sprite'
        if route == state.LOCAL_ROUTE
        else 'on a Sprite, so its "On a Sprite" section applies'
    )
    return (
        f"Remote lane: drive {drive}, job {job_id}. You are the long-running builder below, running as an Orca "
        f"worker {running}.\n\n"
        f"{builder_body()}\n\n"
        f"# The landing reference of the long-running skill\n\n{LANDING.read_text().strip()}\n"
        f"{BRIEF_HEADING}{brief.strip()}\n"
    )


def brief_of(vm_brief: str) -> str:
    """The job's own brief inside the text a launch wrote to its Sprite."""
    return vm_brief.rsplit(BRIEF_HEADING, 1)[1].strip()


def handoff(brief: str, ended: dict, job: dict, text: str, end: LaneEnd) -> str:
    """The brief of a lane taking over from one that died or finished: the job's brief, then what that lane left and the message it never read."""
    prs = ", ".join(job["prs"]) or "none yet"
    local = state.on_mac(job)
    if end.worktree:
        how, where = "finished", f"You continue the job in its terminal and checkout {ON_MAC if local else 'on the same Sprite'}, as it left them."
    else:
        how, where = "died", (
            f"You take the job over {'in a fresh worktree ' + ON_MAC if local else 'on a fresh Sprite'} from its pushed branches; "
            "work it had not committed and pushed is not carried over. Commit and push before any long build."
        )
    return (
        f"{brief}\n\n"
        f"## Taking over from a lane that {how}\n\n"
        f"The lane that held this job {how} {place(job)} at {render.when(ended['at'])}: {ended['why']}. "
        f"{where}\n\n"
        f"Its branch: {end.branch or job['branch'] or 'none recorded'}\n"
        f"Its last line: {job['said'] or 'nothing yet'}\n"
        f"Its PRs: {prs}\n\n"
        f"Its last report:\n\n{(job['report'] or 'none').strip()}\n\n"
        f"The coordinator's message it never read, which you act on now:\n\n{text.strip()}\n"
    )


def tool(name: str) -> str:
    """A script of the newest installed orca plugin, never the one this session's PATH picked up when it started."""
    plugins = json.loads((config_dir() / "plugins" / "installed_plugins.json").read_text())["plugins"]
    installs = [entry for key, entries in plugins.items() if key.startswith("orca@") for entry in entries]
    if not installs:
        raise OrcaError(f"{name} comes with the orca plugin, which is not installed; install orca@skills")
    newest = max(installs, key=lambda entry: entry.get("lastUpdated") or entry.get("installedAt") or "")
    return str(Path(newest["installPath"]) / "bin" / name)


def launch(drive: str, lane: str, brief: Path, checkout: Path, run: str, route: str | None) -> dict:
    """Start one builder through the orca plugin's launcher, on a Sprite or, on the local route, in a fresh worktree of the checkout's Orca repository on this Mac; its success line, with the Sprite's prepared server."""
    env = {**os.environ, "WORKER_RUN": run, "WORKER_ENV": LANE_ENV, "WORKER_BIND": f"{DRIVE} bound --drive {drive} {run} --"}
    local = route == state.LOCAL_ROUTE
    if local:
        env |= {"WORKER_LOCAL": "1", "WORKER_REPO": request("repo", "show", "--repo", f"path:{main_checkout(str(checkout))}")["repo"]["id"]}
    try:
        done = subprocess.run(
            [tool(LAUNCHER), lane, MODEL, EFFORT, str(brief)],
            cwd=checkout,
            env=env,
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
        if (attempt / "worker-start.unsent").exists():
            raise OrcaError(reason)
        if (attempt / "worker-start.status").exists():
            raise OrcaError(
                f"{LAUNCHER} exited {done.returncode} after worker-start ran, so the lane may be live: {reason}; "
                f"read {attempt}/worker-start.json before starting it anywhere else"
            )
        raise OrcaError(f"{LAUNCHER} exited {done.returncode} before a worker started: {reason}; attempt in {attempt}")
    receipt = json.loads(lines[-1])
    if local:
        return {**receipt, "lane": lane, "workspace": None, "environment": None}
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


def mail(run: str, after: int) -> list[dict]:
    """The Run's mail past sequence `after`, oldest first, read without binding or consuming: this terminal binds one Run at a time, and the watch reads every lane's. Orca returns a mailbox's newest rows, so a read that comes back full with none at or before `after` goes again ten times as deep."""
    limit = MAIL_LIMIT
    while len(messages := call("inbox", "--terminal", f"run:{run}", "--limit", str(limit))["messages"]) == limit and min(message["sequence"] for message in messages) > after:
        limit *= 10
    return sorted((message for message in messages if message["to_handle"] == f"run:{run}" and message["sequence"] > after), key=lambda message: message["sequence"])


def worker(dispatch: str) -> dict:
    return call("worker-show", "--dispatch", dispatch)


def death(worker: dict) -> str | None:
    """Why a lane's worker is gone for good, from its worker-show, or None while Orca still holds it."""
    return "its worker exited" if worker["projection"]["liveness"]["verdict"] == "exited" else None


def lane_end(job: dict) -> LaneEnd | None:
    """None while the lane's worker takes messages; else why it does not, the branch its terminal was on, and, for a worker that finished while Orca holds its terminal, on its Sprite or on this Mac, the worktree its next worker starts in. OrcaError when Orca cannot tell either way."""
    try:
        shown = worker(job["dispatch"])
    except OrcaError as error:
        if GONE in str(error):
            return LaneEnd("Orca no longer holds its worker", None, None)
        raise
    branch = ((shown.get("terminal") or {}).get("branch") or "").removeprefix("refs/heads/") or None
    if why := death(shown):
        return LaneEnd(why, branch, None)
    if (verdict := shown["projection"]["liveness"]["verdict"]) != "live":
        raise OrcaError(f"orca orchestration worker-show: its worker's liveness is {verdict}")
    if (finished := shown["worker"]["state"]) in FINISHED:
        why = f"its worker {finished}, and Orca sends nothing to a finished worker {ON_MAC if state.on_mac(job) else 'on a Sprite'}"
        return LaneEnd(why, branch, shown["worker"]["startOptions"]["worktree"])
    return None


def start_after(drive: str, lane: str, brief: Path, job: dict, worktree: str) -> dict:
    """Start the job's next worker in the terminal and worktree of the one that finished, on its Sprite or on this Mac, with the brief as its Task spec: the start Orca names for a finished worker, with `--on` its paired server for a Sprite."""
    argv = [
        "orca",
        "orchestration",
        "worker-start",
        "--run",
        job["run"],
        *(() if state.on_mac(job) else ("--on", job["environment"])),
        "--worktree",
        worktree,
        "--terminal",
        job["terminal"],
        "--task-title",
        lane,
        "--spec-file",
        str(brief),
        "--timeout-ms",
        START_TIMEOUT_MS,
        "--full",
        "--json",
    ]
    receipt = result_of("orchestration worker-start", bound_command(drive, job["run"], argv))
    if receipt["state"] != "ready":
        raise OrcaError(f"orca orchestration worker-start: its worker came back {receipt['state']}, not ready: {json.dumps(receipt)}")
    fields = {field: job[field] for field in ("terminal", "workspace", "environment")}
    return {**fields, "dispatch": receipt["dispatchId"], "task": receipt["taskId"], "lane": lane}


def reply(drive: str, run: str, message_id: str, body: str) -> None:
    """Answer a lane's question on its Run: Orca takes a coordinator's reply only on the one Run its terminal is bound to, which a launch or another reply moves."""
    with state.binding(drive):
        try:
            call("run-use", "--id", run)
            call("reply", "--id", message_id, "--run", run, "--body", body)
        finally:
            bind_drive_run(drive)


def bound_command(drive: str, run: str, argv: list[str]) -> subprocess.CompletedProcess:
    """Run a command that needs the Run bound, such as a launch's worker-start, binding it again once if something outside this drive moved the binding first."""
    with state.binding(drive):
        try:
            for _ in range(2):
                call("run-use", "--id", run)
                done = subprocess.run(argv, capture_output=True, text=True, check=False)
                if FENCED not in done.stdout:
                    break
        finally:
            bind_drive_run(drive)
    return done


def answer(drive: str, job: dict, text: str) -> str:
    """Reply to the lane's open question, or send to its Dispatch when it asked nothing or Orca already holds another answer to that question, since the lane no longer waits on it."""
    if job.get("ask"):
        try:
            reply(drive, job["run"], job["ask"], text)
        except OrcaError as error:
            if error.code != ANSWERED_ELSEWHERE:
                raise
        else:
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
