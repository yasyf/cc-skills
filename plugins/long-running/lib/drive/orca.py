import contextlib
import hashlib
import json
import os
import re
import shlex
import subprocess
import uuid
from pathlib import Path
from typing import IO, NamedTuple

from drive import agents, github, render, state
from drive.install import config_dir
from drive.state import DriveError

LAUNCHER = "orca-worker-launch.sh"
RECLAIMER = "orca-reclaim.py"
TOOLS = "orca-sprite-tools.sh"
MODEL = "claude-opus-5-5"
EFFORT = "xhigh"
LANE_LIMIT = 55
NOT_LANE = re.compile(r"[^a-z0-9-]+")
PLUGIN = Path(__file__).resolve().parents[2]
BUILDER = PLUGIN / "agents" / "builder.md"
LANDING = PLUGIN / "skills" / "long-running" / "reference" / "landing.md"
LANE_ENV = f"CLAUDE_CODE_AUTO_COMPACT_WINDOW=300000 {state.REMOTE_LANE_ENV}=1"
LAUNCH_SECONDS = 1800
MAIL_LIMIT = 100
FENCED = "consumer_fenced"
GONE = {
    "dispatch_not_found": "Orca no longer holds its worker",
    "Unknown environment": "Orca no longer holds its Sprite's environment",
    "selector_not_found": "Orca no longer holds its worker's terminal or worktree",
}
FINISHED = ("succeeded", "failed", "stopped", "abandoned")
START_TIMEOUT_MS = "600000"
BRIEF_HEADING = "\n# Brief\n\n"
REPORT_LINE = re.compile(r"^(?:Researched|Plan):.*$", re.MULTILINE)
REPORT_JOB = re.compile(r"\bj\d+\b")
CARRIED = "reports|asks|repro|scratch"
PATH_CHARS = r"[^\s`'\"<>()\[\]{}*,;|]+"
PATH_END = re.compile(r"[^\w/]+$")
DRIVE = PLUGIN / "bin" / "drive"
SPRITE_DRIVE = PLUGIN / "lib" / "sprite" / "drive"
SPRITE_TOOLS = f"drive={SPRITE_DRIVE}"
CALL_SECONDS = 60
ON_MAC = "on this Mac"
PUSH_FIRST = "Commit and push before any long build."
UNPUSHED = f"work it had not committed and pushed is not carried over. {PUSH_FIRST}"
RELOAD = "/reload-plugins"
SUBMIT_SECONDS = 10
TIMEOUT = "timeout"
ANSWERED_ELSEWHERE = "answer_conflict"
DISPATCH_CLOSED = "dispatch_inactive"
UNSTARTED = 75
CC_REMOTE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "cc-remote"
CC_REMOTE_WORKSPACES = CC_REMOTE / "workspaces"
CC_REMOTE_TASKS = CC_REMOTE / "orca"
SPRITE_BRIEFS = ".local/state/long-running/briefs"
CHECKOUT_LOCK = ".checkout.lock"
CHECKOUT_SECONDS = 600


class OrcaError(DriveError):
    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class Unstarted(OrcaError):
    """A launch or a bound command that refused before anything started a worker, so running it again starts it once."""


class Unfit(Unstarted):
    """A Sprite that refused the tools or the brief a worker needs, so the job's next worker starts on another."""


class LaneEnd(NamedTuple):
    why: str
    branch: str | None
    worktree: str | None
    terminal: str | None = None
    run: str | None = None
    unfit: bool = False


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


def speak_as(terminal: str | None) -> None:
    """Make each Orca call of this process and the processes it starts speak as the terminal, the coordinator's or sub-coordinator's whose Runs a watch serves, whatever started it: outside an Orca terminal Orca guesses the caller from the pane he focuses, or refuses with no_active_sender_terminal, and a watch another pane started would speak as that pane, its pane key included. None, a session in tmux, records no terminal of its own, so Orca resolves the caller as before."""
    if terminal and os.environ.get("ORCA_TERMINAL_HANDLE") != terminal:
        os.environ.pop("ORCA_PANE_KEY", None)
        os.environ["ORCA_TERMINAL_HANDLE"] = terminal


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


def start_meta(drive: str, worktree: str, earlier: str | None) -> str | None:
    """Start the drive's meta session in an Orca terminal of its own in the drive's worktree, by its command in Start or resume, unless the terminal an earlier drive start opened for it is still there; the new terminal's handle, or None."""
    if earlier:
        with contextlib.suppress(OrcaError):
            terminal(earlier)
            return None
    command = shlex.join(
        [
            "claude",
            "-n",
            f"{drive}-meta",
            "--model",
            MODEL,
            "--effort",
            EFFORT,
            "--permission-mode",
            "bypassPermissions",
            "--settings",
            json.dumps(agents.session_settings({}), separators=(",", ":")),
            "--channels",
            "plugin:cc-inbox@cc-inbox",
            "--",
            f"/long-running {drive} as meta: run Start or resume for that role",
        ]
    )
    return request("terminal", "create", "--worktree", f"path:{worktree}", "--title", f"{drive}-meta", "--command", command)["terminal"]["handle"]


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


def live_pid(session: str) -> int | None:
    """The pid of the Claude Code process running the session, None once it has exited, since its `~/.claude/sessions/<pid>.json` can outlive it."""
    running = parents()
    return next((pid for pid in claude_pids(session) if pid in running), None)


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


def drive_run(drive: str) -> str:
    """The drive's own Run, which no lane mails, made the first time a lane call needs it."""
    if run := state.load(drive).get("run"):
        return run
    run = new_run(f"drive {drive}: the coordinator's own Run, which no lane mails")
    with state.edit(drive) as data:
        data["run"] = run
    return run


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
        f"{carried(drive, brief)}"
        f"{BRIEF_HEADING}{brief.strip()}\n"
    )


def drive_files(drive: str, brief: str) -> dict[str, str | None]:
    """Each file of the drive's state folder the brief names, by the heading its text goes under, with that text or None when the drive keeps none: the report of each job its Researched: and Plan: lines name, every return it took as `drive job report` prints it, and each path under the folder's reports, asks, repro and scratch folders it names anywhere, absolute or from the folder; a folder it names, such as its Scratch: line's, carries nothing."""
    folder = state.drive_dir(drive)
    roots = [folder, *([Path("~") / folder.relative_to(Path.home())] if folder.is_relative_to(Path.home()) else [])]
    prefix = "|".join(re.escape(f"{root}/") for root in roots)
    named = [PATH_END.sub("", found) for found in re.findall(rf"(?<![\w./~-])(?:{prefix})?((?:{CARRIED})/{PATH_CHARS})", brief)]
    files = {f"Report of {job_id}": state.report_text(drive, job_id) or None for job_id in REPORT_JOB.findall("\n".join(REPORT_LINE.findall(brief)))}
    for name in named:
        file = folder / name
        if not file.is_dir():
            files[f"Drive file {name}"] = file.read_text() if file.is_file() else None
    return files


def carried(drive: str, brief: str) -> str:
    """Each drive file the brief names, whole under its heading, since a Sprite holds no copy of the drive to read one from."""
    return "".join(f"\n# {heading}, which your brief names\n\n{text.strip()}\n" for heading, text in drive_files(drive, brief).items() if text is not None)


def uncarried(drive: str, brief: str) -> str | None:
    """Why no lane starts on this brief: it names a drive file the drive keeps none of, which its lane would stop to ask for."""
    missing = [heading for heading, text in drive_files(drive, brief).items() if text is None]
    if not missing:
        return None
    return (
        f"its brief names {'; '.join(missing)}, which the drive keeps no text of, so its lane would stop to ask for each;"
        " keep each in the drive first, or take its name out of the brief"
    )


def brief_of(vm_brief: str) -> str:
    """The job's own brief inside the text a launch wrote to its Sprite, or the whole text of one drive job brief wrote."""
    return vm_brief.rsplit(BRIEF_HEADING, 1)[-1].strip()


def handoff(brief: str, ended: dict, job: dict, text: str | None, end: LaneEnd, left: str | None) -> str:
    """The brief of a lane taking over from one that died or finished: the job's brief, then what that lane left, what its Sprite holds that the new lane continues on, and the message it never read, when a resume brought one."""
    local = state.on_mac(job)
    if end.worktree:
        how, where = "finished", f"You continue the job in its terminal and checkout {ON_MAC if local else 'on the same Sprite'}, as it left them."
    elif left:
        how, where = "died", f"You continue the job on the same Sprite, in the checkout it left as it was, since what it holds on no remote could not be saved to GitHub: {left}. Read its git status and log before anything else."
    else:
        how, where = "died", (
            f"You take the job over {'in a fresh worktree ' + ON_MAC if local else 'on a fresh Sprite'} from its pushed branches; "
            + (f"what it held on no remote was saved for you: {ended['saved']}. Fetch each saved branch and carry its work on. {PUSH_FIRST}" if ended.get("saved") else UNPUSHED)
        )
    held = f"The lane that held this job {how} {place(job)} at {render.when(ended['at'])}: {ended['why']}. {where}"
    return taking_over(brief, f"a lane that {how}", held, end.branch, job, text and f"The coordinator's message it never read, which you act on now:\n\n{text.strip()}\n")


def agent_handoff(brief: str, job: dict, text: str | None, died: str | None) -> str:
    """The brief of an agent or lane taking over a job from an Agent on this Mac that stopped, or from one of another session, which only that session's SendMessage reaches: the job's brief, then what that agent left and the message, when one came, that it never read."""
    where = f"stopped, so it takes no message: {died}" if died else "runs where this session cannot message it"
    held = f"The agent that held this job, {job['agent']} of session {job['session']}, {where}. You take the job over from its pushed branches; {UNPUSHED}"
    holder = "an agent that stopped" if died else "an agent of another session"
    return taking_over(brief, holder, held, None, job, text and f"The message it never read, which you act on now:\n\n{text.strip()}\n")


def taking_over(brief: str, holder: str, held: str, branch: str | None, job: dict, message: str | None) -> str:
    prs = ", ".join(job["prs"]) or "none yet"
    return (
        f"{brief.strip()}\n\n"
        f"## Taking over from {holder}\n\n"
        f"{held}\n\n"
        f"Its branch: {branch or job['branch'] or 'none recorded'}\n"
        f"Its last line: {job['said'] or 'nothing yet'}\n"
        f"Its PRs: {prs}\n\n"
        f"Its last report:\n\n{(job.get('report') or 'none').strip()}\n"
        + (f"\n{message}" if message else "")
    )


def tool(name: str) -> str:
    """A script of the newest installed orca plugin, never the one this session's PATH picked up when it started."""
    plugins = json.loads((config_dir() / "plugins" / "installed_plugins.json").read_text())["plugins"]
    installs = [entry for key, entries in plugins.items() if key.startswith("orca@") for entry in entries]
    if not installs:
        raise OrcaError(f"{name} comes with the orca plugin, which is not installed; install orca@skills")
    newest = max(installs, key=lambda entry: entry.get("lastUpdated") or entry.get("installedAt") or "")
    return str(Path(newest["installPath"]) / "bin" / name)


def checkout(drive: str, data: dict) -> Path:
    """The checkout each lane's launch, save and release runs the orca plugin's scripts in, so every Sprite claim, refill and cleanup runs the cc-remote, config and tool payload its repository's default branch pins now: the worktree of the coordinator checkout's repository holding the branch drive-<drive>, which only the drive uses, moved with one fetch to that branch's head before each, never a session's working tree at whatever commit and edits it carries. git's worktree list finds it; the first launch makes it with ccx vcs worktree add, so it sits under ccx's worktree root, where its owner's mise trusts a worktree's mise.toml as the cc-remote shim needs. It is moved with no hook, since the monorepo's post-checkout hook warms node_modules that no launch reads, under the drive's checkout lock, since lanes launch at once; DriveError naming why it was not moved, or that the head holds no .orca.json."""
    clone = data["roles"]["coordinator"]["cwd"]
    lane = f"drive-{drive}"
    try:
        branch = github.default_branch(github.checkout_repo(clone))
        with state.flocked(state.drive_dir(drive) / CHECKOUT_LOCK):
            sha = github.fetched(clone, branch)
            if not (kept := holder(clone, lane)):
                github.run(["ccx", "vcs", "worktree", "add", lane, "--jj", "none"], cwd=clone, timeout=CHECKOUT_SECONDS)
                kept = holder(clone, lane)
            github.run(["git", "-C", str(kept), "-c", "core.hooksPath=/dev/null", "-c", "checkout.workers=0", "checkout", "-q", "--force", "-B", lane, sha], timeout=CHECKOUT_SECONDS)
    except github.GithubError as error:
        raise DriveError(f"the drive's checkout, the worktree of {clone}'s repository on {lane}, was not moved to the head of its default branch, so nothing launched, saved or released from it: {error}") from None
    if not (kept / ".orca.json").is_file():
        raise DriveError(f"{kept}, the drive's checkout at {branch} {sha[:12]}, has no .orca.json, so no lane can be cloned from it")
    return kept


def holder(clone: str, branch: str) -> Path | None:
    """The worktree of clone's repository that has branch checked out, as git's worktree list names it."""
    entries = [entry.split("\n") for entry in github.run(["git", "-C", clone, "worktree", "list", "--porcelain"]).split("\n\n")]
    return next((Path(lines[0].removeprefix("worktree ")) for lines in entries if f"branch refs/heads/{branch}" in lines), None)


def launch(drive: str, lane: str, brief: Path, checkout: Path, run: str, route: str | None, workspace: str | None) -> dict:
    """Start one builder through the orca plugin's launcher, on a fresh Sprite or on the workspace's own Sprite in the checkout it holds, either way giving its `~/.local/bin` the `drive` that asks each verb of this Mac, or, on the local route, in a fresh worktree of the checkout's Orca repository on this Mac; its success line, with the Sprite's prepared server."""
    env = {**os.environ, "WORKER_RUN": run, "WORKER_ENV": LANE_ENV, "WORKER_BIND": f"{DRIVE} bound --drive {drive} {run} --"}
    if workspace:
        env["WORKER_WORKSPACE"] = workspace
    local = route == state.LOCAL_ROUTE
    env |= {"WORKER_LOCAL": "1", "WORKER_REPO": request("repo", "show", "--repo", f"path:{main_checkout(str(checkout))}")["repo"]["id"]} if local else {"WORKER_TOOLS": SPRITE_TOOLS}
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
        if unsent(brief):
            raise Unstarted(reason)
        if (attempt / "worker-start.status").exists():
            raise OrcaError(
                f"{LAUNCHER} exited {done.returncode} after worker-start ran, so the lane may be live: {reason}; "
                f"read {attempt}/worker-start.json before starting it anywhere else"
            )
        raise Unstarted(f"{LAUNCHER} exited {done.returncode} before a worker started: {reason}; attempt in {attempt}")
    receipt = json.loads(lines[-1])
    if local:
        return {**receipt, "lane": lane, "workspace": None, "environment": None}
    prepared = json.loads((brief.with_suffix(".worker") / "prepared.json").read_text())
    return {**receipt, "environment": prepared["environment"]}


def unsent(brief: Path) -> bool:
    """Whether a launch of this brief prepared a Sprite whose worker-start sent nothing, which a launch of the same brief reuses."""
    return (brief.with_suffix(".worker") / "worker-start.unsent").exists()


def reclaim(run: str, dispatch: str, checkout: Path) -> str:
    return reclaimer(checkout, "--run", run, "--done", dispatch)


def release(workspace: str, checkout: Path) -> str:
    """Destroy the Sprite a finished lane ran on, with its Orca environment and forward, through the reclaimer's own guard: OrcaError naming what it holds on no remote, why its checkout could not be read, or cc-remote's refusal, which keeps it."""
    return reclaimer(checkout, "--destroy", workspace)


def release_detached(workspace: str, checkout: Path, log: IO[str]) -> subprocess.Popen:
    return subprocess.Popen(
        [tool(RECLAIMER), "--destroy", workspace], cwd=checkout, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
    )


def holds_sprite(job: dict) -> bool:
    """Whether cc-remote still records the Sprite workspace the job's lane ran on."""
    return not state.on_mac(job) and bool(job.get("workspace")) and (CC_REMOTE_WORKSPACES / f"{job['workspace']}.json").is_file()


def save(drive: str, job_id: str, job: dict, checkout: Path) -> str | None:
    """Take what the job's Sprite holds that a fresh Sprite would not get to GitHub, by the reclaimer's own read of the checkout it keeps a Sprite for, found by the job's workspace so a Sprite Orca forgot is saved too: it commits each worktree's uncommitted changes on the Sprite and pushes each commit GitHub holds nowhere as saved/<the dead lane's name>/<its branch>, on a GitHub token lent to that one push. What it saved and the branches it is on now; None when the Sprite holds nothing, or the lane runs on this Mac; OrcaError naming why the Sprite could not be read or the save failed, such as a Sprite that never woke."""
    return None if state.on_mac(job) else reclaimer(checkout, "--save", job["workspace"], job.get("lane") or lane_name(drive, job_id)) or None


def reclaimer(checkout: Path, *args: str) -> str:
    done = subprocess.run(
        [tool(RECLAIMER), *args],
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


def placement(worker: dict) -> dict:
    """Where a worker-show's worker runs, as a job records it: on the Sprite of its paired server when Orca names its host remote, else on this Mac."""
    if (worker["projection"].get("host") or {}).get("kind") != "remote":
        return {"route": state.LOCAL_ROUTE, "workspace": None, "environment": None}
    return {"route": None, "workspace": worker["server"]["name"], "environment": worker["server"]["name"]}


def run_of(worker: dict) -> dict:
    """The Run and task a worker-show's dispatch belongs to and its terminal, which a job records beside that dispatch, so it never keeps a Run its dispatch is not on."""
    return {"run": worker["dispatch"]["runId"], "task": worker["dispatch"]["taskId"], "terminal": worker["terminal"]["handle"]}


def task_title(run: str, task: str) -> str:
    """The title of a Task on a Run, which a launch names after the lane it starts."""
    return next(found["title"] for found in request("orchestration", "task-list", "--run", run)["tasks"] if found["id"] == task)


def receipt(dispatch: str) -> dict:
    """What a job records of the worker on a dispatch, as Orca shows it: the dispatch, its Run, task and terminal, the lane its task is titled after, and where it runs."""
    shown = worker(dispatch)
    held = run_of(shown)
    return {"dispatch": dispatch, **held, "lane": task_title(held["run"], held["task"]), **placement(shown)}


def adopted(brief: Path) -> dict | None:
    """The worker an earlier launch of this brief started and the drive never recorded, as a job records it, when the attempt the launcher keeps for the brief holds a ready worker-start receipt, which the launcher refuses to launch the brief again past; else None."""
    attempt = brief.with_suffix(".worker")
    status = attempt / "worker-start.status"
    if not status.is_file() or status.read_text().strip() != "0":
        return None
    started = json.loads((attempt / "worker-start.json").read_text()).get("result") or {}
    if started.get("state") != "ready":
        return None
    return receipt(started["dispatchId"])


def death(worker: dict) -> str | None:
    """Why a lane's worker is gone for good, from its worker-show, or None while Orca still holds it."""
    return "its worker exited" if worker["projection"]["liveness"]["verdict"] == "exited" else None


def gone(error: OrcaError) -> str | None:
    """Why a worker-show refusal means Orca never reaches the lane again: it dropped the Dispatch, or the environment of its Sprite, which ending the lane removes; None when it may reach it yet."""
    return next((why for refusal, why in GONE.items() if refusal in str(error)), None)


def lane_end(job: dict) -> LaneEnd | None:
    """None while the lane's worker takes messages; else why it does not, the branch its terminal was on, and, for a worker that finished while Orca holds its terminal, on its Sprite or on this Mac, the worktree, terminal and Run its next worker starts on, read from Orca, since a dispatch recorded by hand records neither. OrcaError when Orca cannot tell either way."""
    try:
        shown = worker(job["dispatch"])
    except OrcaError as error:
        if why := gone(error):
            return LaneEnd(why, None, None)
        raise
    branch = ((shown.get("terminal") or {}).get("branch") or "").removeprefix("refs/heads/") or None
    if why := death(shown):
        return LaneEnd(why, branch, None)
    if (verdict := shown["projection"]["liveness"]["verdict"]) != "live":
        raise OrcaError(f"orca orchestration worker-show: its worker's liveness is {verdict}")
    if (finished := shown["worker"]["state"]) in FINISHED:
        why = f"its worker {finished}, and Orca sends nothing to a finished worker {ON_MAC if state.on_mac(job) else 'on a Sprite'}"
        return LaneEnd(why, branch, shown["worker"]["startOptions"]["worktree"], shown["terminal"]["handle"], shown["dispatch"]["runId"])
    return None


def start_after(drive: str, lane: str, brief: Path, job: dict, end: LaneEnd) -> dict:
    """Start the job's next worker in the terminal and worktree of the one that finished, on its Run, on its Sprite or on this Mac, with a Task spec that names the brief where the worker reads it: the start Orca names for a finished worker, with `--on` its paired server for a Sprite, whose `~/.local/bin` first takes the tools a launch gives it, this plugin's `drive` among them, since the Sprite may have been launched before they changed. Orca keeps every Task spec until its database is pruned, and a brief runs to hundreds of kilobytes, so the spec carries only where the brief is, as the launcher's does."""
    if state.on_mac(job):
        spec = f"Lane '{lane}'. Your full brief is the file {brief}: read it in full with the Read tool before anything else and execute it exactly."
    else:
        place_tools(job["workspace"])
        text = brief.read_bytes()
        spec = (
            f"Lane {lane}: read the whole brief at {place_brief(job['workspace'], lane, text)}, verify its SHA-256 is "
            f"{hashlib.sha256(text).hexdigest()} and its size is {len(text)} bytes, then follow it exactly."
        )
    argv = [
        "orca",
        "orchestration",
        "worker-start",
        "--run",
        end.run,
        *(() if state.on_mac(job) else ("--on", job["environment"])),
        "--worktree",
        end.worktree,
        "--terminal",
        end.terminal,
        "--task-title",
        lane,
        "--spec",
        spec,
        "--timeout-ms",
        START_TIMEOUT_MS,
        "--full",
        "--json",
    ]
    receipt = result_of("orchestration worker-start", bound_command(drive, end.run, argv))
    if receipt["state"] != "ready":
        raise OrcaError(f"orca orchestration worker-start: its worker came back {receipt['state']}, not ready: {json.dumps(receipt)}")
    return {
        "run": end.run,
        "terminal": end.terminal,
        "workspace": job["workspace"],
        "environment": job["environment"],
        "dispatch": receipt["dispatchId"],
        "task": receipt["taskId"],
        "lane": lane,
    }


def place_tools(workspace: str) -> None:
    """Put on the workspace's Sprite the tools a launch puts there, through the orca plugin's own step on the task cc-remote keeps for that Sprite: the released ccx and cc-notes, and each WORKER_TOOLS file, this plugin's `drive` that asks each verb of this Mac; Unfit naming the step's refusal, since no worker starts without them."""
    done = subprocess.run(
        [tool(TOOLS), str(CC_REMOTE_TASKS / f"{workspace}.json")],
        env={**os.environ, "WORKER_TOOLS": SPRITE_TOOLS},
        capture_output=True,
        text=True,
        timeout=LAUNCH_SECONDS,
        check=False,
    )
    if done.returncode:
        said = done.stderr.strip().splitlines()
        raise Unfit(f"{TOOLS} exited {done.returncode} on {workspace}, so no worker started: {said[-1] if said else 'no output'}")


def place_brief(workspace: str, lane: str, text: bytes) -> str:
    """Write the brief onto the workspace's Sprite over the ssh forward cc-remote keeps for it, through stdin, since a handoff outgrows one command line; the path it landed at, or Unfit when the Sprite did not keep exactly these bytes."""
    forward = json.loads((CC_REMOTE_TASKS / f"{workspace}.json").read_text())["forward"]
    target = f'"$HOME/{SPRITE_BRIEFS}/{lane}.md"'
    done = subprocess.run(
        [
            "ssh",
            "-F",
            forward["config"],
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=30",
            forward["host"],
            f'mkdir -p "$HOME/{SPRITE_BRIEFS}" && cat >{target} && sha256sum {target}',
        ],
        input=text,
        capture_output=True,
        timeout=LAUNCH_SECONDS,
        check=False,
    )
    said = done.stdout.decode().split()
    if done.returncode or len(said) != 2 or said[0] != hashlib.sha256(text).hexdigest():
        why = done.stderr.decode().strip().splitlines()
        raise Unfit(f"writing the brief onto {workspace} exited {done.returncode}, so no worker started: {why[-1] if why else ' '.join(said) or 'no output'}")
    return said[1]


def reply(drive: str, run: str, message_id: str, body: str) -> None:
    """Answer a lane's question on its Run: Orca takes a coordinator's reply only on the one Run its terminal is bound to, which a launch or another reply moves."""
    with state.binding(drive):
        home = drive_run(drive)
        try:
            call("run-use", "--id", run)
            call("reply", "--id", message_id, "--run", run, "--body", body)
        finally:
            call("run-use", "--id", home)


def bound_command(drive: str, run: str, argv: list[str]) -> subprocess.CompletedProcess:
    """Run a command that needs the Run bound, such as a launch's worker-start, binding it again once if something outside this drive moved the binding first, then bind the drive's own Run, read before the command runs. A worker-start holds the binding only until Orca takes its request, which request-show then reads pending or done, since Orca checks the binding once, as it admits the start, and its wait for the worker to be ready runs on outside the lock; any other command holds it to its end. Unstarted when it refuses before anything the command starts, a fenced run included, since Orca starts nothing it fenced."""
    argv, request = requested(argv)
    with state.binding(drive):
        try:
            home = drive_run(drive)
        except DriveError as error:
            raise Unstarted(str(error)) from None
        try:
            for _ in range(2):
                try:
                    call("run-use", "--id", run)
                except OrcaError as error:
                    raise Unstarted(str(error), error.code) from None
                command = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if request and accepted(command, request):
                    break
                done = completed(argv, command)
                if FENCED not in done.stdout:
                    return done
            else:
                return done
        finally:
            call("run-use", "--id", home)
    return completed(argv, command)


def requested(argv: list[str]) -> tuple[list[str], str | None]:
    """A worker-start's argv with the request id Orca keeps its receipt under, the one it names with --retry-request or a fresh one, and that id; any other command as it is, with none."""
    if "worker-start" not in argv:
        return argv, None
    if "--retry-request" in argv:
        return argv, argv[argv.index("--retry-request") + 1]
    request = str(uuid.uuid4())
    return [*argv, "--retry-request", request], request


def accepted(command: subprocess.Popen, request: str) -> bool:
    """Whether Orca took the request while its worker-start still runs, asking request-show until it reads pending or completed; False once the process ends first, as a fenced start does, or once request-show refuses, so the start holds the binding to its end."""
    while command.poll() is None:
        try:
            if call("request-show", "--request", request)["state"] != "absent":
                return True
        except OrcaError:
            return False
    return False


def completed(argv: list[str], command: subprocess.Popen) -> subprocess.CompletedProcess:
    stdout, stderr = command.communicate()
    return subprocess.CompletedProcess(argv, command.returncode, stdout, stderr)


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
    return send(job, "From the coordinator", text)


def send(job: dict, subject: str, text: str) -> str:
    """Send the text to the lane's Dispatch as a message, which neither answers nor clears a question it asked."""
    call("send", "--to", f"dispatch:{job['dispatch']}", "--run", job["run"], "--subject", subject, "--body", text)
    return f"sent to dispatch {job['dispatch']}"
