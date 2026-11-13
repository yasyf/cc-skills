import contextlib
import hashlib
import io
import json
import os
import re
import shlex
import signal
import subprocess
import tarfile
import uuid
from pathlib import Path
from typing import IO, NamedTuple

from drive import agents, github, procs, reads, render, state
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
RECLAIM_SECONDS = 300
MAIL_LIMIT = 100
FENCED = "consumer_fenced"
UNREACHABLE = "remote_runtime_unavailable"
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
REPORT_FILE = re.compile(r"reports/(j\d+)\.md")
REPRO_FILE = re.compile(r"repro/(j\d+)(?:\.\w+)?")
CARRIED = "reports|asks|repro|scratch|jobs"
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
UNFIT = 76
CC_REMOTE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "cc-remote"
CC_REMOTE_WORKSPACES = CC_REMOTE / "workspaces"
CC_REMOTE_TASKS = CC_REMOTE / "orca"
SPRITE_ANSWERS = ".local/state/long-running/answers"
SPRITE_LOG_BYTES = 20000
SPRITE_LOGS = f"""exec 2>&1
cd
worker=$(ls -t .claude/projects/*/*.jsonl | head -n 1)
for log in .cc-remote/orca/serve.log .config/orca/logs/daemon.log .config/orca/logs/orcad.trace.ndjson "$worker"; do
  echo "== tail -c {SPRITE_LOG_BYTES} ~/$log"; tail -c {SPRITE_LOG_BYTES} "$log"; echo
done
echo "== df -k ~"; df -k ~
echo "== /sys/fs/cgroup/memory.events"; cat /sys/fs/cgroup/memory.events
echo "== dmesg | tail -n 50"; dmesg | tail -n 50
"""
SPRITE_TRANSCRIPTS = 'tar -czf - -C "$HOME/.claude/projects" . || [ $? -eq 1 ]'
UNKEPT = "unkept.txt"
TRANSCRIPTS = ".transcripts"
ANSWER_BYTES = 4096
RELAY_BYTES = 64 * 1024
RELAY_ROOM = 4096
KEPT_ANSWER = "drive answer kept on this Sprite: "
DRIVE_STATUS = re.compile(r"drive exited \d+\n")
CHECKOUT_LOCK = ".checkout.lock"
CHECKOUT_SECONDS = 600
SESSION_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


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
    unreached: bool = False


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
    """Whether Orca shows this session: it runs in an Orca terminal, or in tmux, which `cci session attach` shows in one."""
    return "ORCA_TERMINAL_HANDLE" in os.environ or "TMUX" in os.environ


def own_terminal() -> str | None:
    """This session's Orca terminal, or None outside one: under tmux, which inherits the handle of whatever terminal started its server, the one that last attached to its tmux session, which cci keeps in that session's environment."""
    if "TMUX" not in os.environ:
        return os.environ.get("ORCA_TERMINAL_HANDLE")
    name, _, handle = tmux("show-environment", "ORCA_TERMINAL_HANDLE").strip().partition("=")
    return handle if name == "ORCA_TERMINAL_HANDLE" and handle else None


def speak_as_drive(drive: str) -> None:
    """Make each Orca call of this process and the processes it starts speak as the drive's own terminal, whatever started it: outside an Orca terminal Orca guesses the caller from the pane he focuses, or refuses with no_active_sender_terminal, and a process a session started would speak as that session's pane, its pane key included."""
    terminal = drive_terminal(drive)
    if os.environ.get("ORCA_TERMINAL_HANDLE") != terminal:
        os.environ.pop("ORCA_PANE_KEY", None)
        os.environ["ORCA_TERMINAL_HANDLE"] = terminal


def drive_terminal(drive: str) -> str:
    """The drive's own Orca terminal, a shell running no agent that makes, binds and answers every lane's Run, since Orca types a Run's "You have N orchestration messages" line into the terminal coordinating it only while that terminal runs an agent: the one the drive keeps while Orca still shows its pane, else one opened in the coordinator's checkout and kept, unless another process kept a fresh one first."""
    kept = state.load(drive)["terminal"]
    with contextlib.suppress(OrcaError):
        if kept and not terminal(kept)["orphaned"]:
            return kept
    with state.edit(drive) as data:
        if data["terminal"] == kept:
            data["terminal"] = request("terminal", "create", "--worktree", f"path:{data['roles']['coordinator']['cwd']}", "--title", f"{drive}-lanes")["terminal"]["handle"]
        return data["terminal"]


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
    """Whether the terminal's process tree holds the session's Claude Code process, or a tmux client attached to the tmux session holding it, so switching to it lands there."""
    sweep = request("diagnostics", "memory")["worktrees"]
    pty = next((pty for worktree in sweep for pty in worktree["sessions"] if pty["sessionId"] == terminal["ptyId"]), None)
    if not pty:
        return False
    tree = parents()
    pids = claude_pids(session)
    held = {name for pane, name in tmux_rows("list-panes", "-a", "-F", "#{pane_pid} #{session_name}") if any(holds(tree, pane, pid) for pid in pids)}
    clients = [client for client, name in tmux_rows("list-clients", "-F", "#{client_pid} #{session_name}") if name in held]
    return any(holds(tree, pty["pid"], pid) for pid in [*pids, *clients])


def tmux_rows(*args: str) -> list[tuple[int, str]]:
    """Each pid and tmux session name a tmux list command prints."""
    rows = (line.partition(" ") for line in tmux(*args).splitlines())
    return [(int(pid), name) for pid, _, name in rows]


def tmux(*args: str) -> str:
    """What the tmux command prints, nothing when it fails or no tmux server runs."""
    try:
        ran = subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=CALL_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return "" if ran.returncode else ran.stdout


def start_meta(drive: str, worktree: str, earlier: str | None) -> str | None:
    """Start the drive's meta session under cci in the drive's worktree and show it in an Orca terminal of its own, by its lines in Start or resume, unless the terminal an earlier drive start opened for it is still there; the new terminal's handle, or None."""
    if earlier:
        with contextlib.suppress(OrcaError):
            terminal(earlier)
            return None
    register, show = (shlex.split(line) for line in agents.launch(drive, "meta", None, worktree))
    try:
        reads.run(register, Path(worktree))
    except reads.ReadError as error:
        raise OrcaError(str(error)) from None
    return request(*show[1:-1])["terminal"]["handle"]


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
    return [pid for pid, record in agents.session_records() if record.get("sessionId") == session]


def live_pid(session: str) -> int | None:
    """The pid of the Claude Code process running the session, None once it has exited, since its `~/.claude/sessions/<pid>.json` can outlive it."""
    running = parents()
    return next((pid for pid in claude_pids(session) if pid in running), None)


def parents() -> dict[int, int]:
    return {process.pid: process.ppid for process in procs.table()}


def holds(tree: dict[int, int], root: int, pid: int) -> bool:
    while pid > 1 and pid != root:
        pid = tree.get(pid, 0)
    return pid == root


def new_run(drive: str, objective: str) -> str:
    """A fresh Run, made by the drive's own terminal, which coordinates it beside the Runs it already holds."""
    speak_as_drive(drive)
    return call("run-create", "--objective", objective)["run"]["id"]


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
    """Each file of the drive's state folder the brief names, by the heading its text goes under, with that text or None when the drive keeps none: the report of each job its Researched: and Plan: lines name that has returned, every return it took as `drive job report` prints it, and each path under the folder's reports, asks, repro, scratch and jobs folders it names anywhere, absolute or from the folder, a reports/<job>.md being that job's report and a repro/<job>, with any suffix or none, the repro file drive job set --repro kept for it; a folder it names, such as its Scratch: line's, carries nothing, and a path resolving outside the folder, through `..` or a symlink, has no text, since a lane's READ names its path."""
    folder = state.drive_dir(drive)
    roots = [folder, *([Path("~") / folder.relative_to(Path.home())] if folder.is_relative_to(Path.home()) else [])]
    prefix = "|".join(re.escape(f"{root}/") for root in roots)
    named = [PATH_END.sub("", found) for found in re.findall(rf"(?<![\w./~-])(?:{prefix})?((?:{CARRIED})/{PATH_CHARS})", brief)]
    files = {f"Report of {job_id}": text for job_id in REPORT_JOB.findall("\n".join(REPORT_LINE.findall(brief))) if (text := state.report_text(drive, job_id))}
    for name in named:
        if report := REPORT_FILE.fullmatch(name):
            files[f"Report of {report[1]}"] = state.report_text(drive, report[1]) or None
            continue
        if (repro := REPRO_FILE.fullmatch(name)) and (kept := state.load(drive)["jobs"].get(repro[1], {}).get("repro")):
            name = f"repro/{Path(kept).name}"
        if not (file := folder / name).is_dir():
            files[f"Drive file {name}"] = file.read_text(errors="replace") if file.is_file() and file.resolve().is_relative_to(folder.resolve()) else None
    return files


def carried(drive: str, brief: str) -> str:
    """Each drive file the brief names, whole under its heading, since a Sprite holds no copy of the drive to read one from, but a lane's transcript keep_transcripts kept, which runs to megabytes and the lane reads with READ."""
    return "".join(
        f"\n# {heading}, which your brief names\n\n{text.strip()}\n" for heading, text in drive_files(drive, brief).items() if text is not None and f"{TRANSCRIPTS}/" not in heading
    )


def uncarried(drive: str, job_id: str, brief: str) -> str | None:
    """Why no lane starts on this brief: it names a drive file the drive keeps none of, which its lane would stop to ask for."""
    missing = [heading for heading, text in drive_files(drive, brief).items() if text is None]
    if not missing:
        return None
    return (
        f"its brief names {'; '.join(missing)}, which the drive keeps no text of, so its lane would stop to ask for each;"
        f" launch it on a brief file that leaves each out: drive job launch {job_id} <file>"
    )


def brief_of(vm_brief: str) -> str:
    """The job's own brief inside the text a launch wrote to its Sprite, or the whole text of one drive job brief wrote."""
    return vm_brief.rsplit(BRIEF_HEADING, 1)[-1].strip()


def handoff(brief: str, ended: dict, saved: list[str], job: dict, text: str | None, end: LaneEnd, since: str | None, here: bool = False) -> str:
    """The brief of a lane taking over from one that died or finished: the job's brief, then what that lane left, why the new lane continues on its Sprite when it does, each branch GitHub holds of what that lane's saves pushed, and the message it never read, when a resume brought one. Here, a move of a job on a Sprite to this Mac, takes it over in a fresh worktree on this Mac however its lane ended."""
    local = here or state.on_mac(job)
    how = "finished" if end.worktree else "died"
    if end.worktree and not here:
        where = f"You continue the job in its terminal and checkout {ON_MAC}, as it left them." if local else "You continue the job on the same Sprite, in the checkout it left as it was."
    elif since:
        where = f"You continue the job on the same Sprite, in the checkout it left as it was, since {since}. Read its git status and log before anything else."
    else:
        where = (
            f"You take the job over {'in a fresh worktree ' + ON_MAC if local else 'on a fresh Sprite'} from its pushed branches; "
            + (f"what it held on no remote was saved for you on GitHub as {', '.join(saved)}. Fetch each saved branch and carry its work on. {PUSH_FIRST}" if saved else UNPUSHED)
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


def launch(drive: str, lane: str, brief: Path, checkout: Path, run: str, route: str | None, workspace: str | None, session: str | None) -> dict:
    """Start one builder through the orca plugin's launcher, on a fresh Sprite or on the workspace's own Sprite in the checkout it holds, there resuming the Claude session given, either way giving its `~/.local/bin` the `drive` that asks each verb of this Mac, or, on the local route, in a fresh worktree of the checkout's Orca repository on this Mac; its success line, with the Sprite's prepared server."""
    env = {**os.environ, "WORKER_RUN": run, "WORKER_ENV": LANE_ENV, "WORKER_BIND": f"{DRIVE} bound --drive {drive} {run} --"}
    if workspace:
        env["WORKER_WORKSPACE"] = workspace
    if session:
        env["WORKER_SESSION"] = session
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
        if done.returncode == UNFIT:
            raise Unfit(reason)
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


def release(drive: str, job_id: str, job: dict, checkout: Path) -> str:
    """Destroy the Sprite at the job's workspace, with its Orca environment and forward, once the reclaimer's --destroy has saved what it holds on no remote to GitHub as save does: what it saved and destroyed; OrcaError naming why its checkout could not be read, the save failed, what it still holds, or cc-remote's refusal, which keeps it."""
    return reclaimer(checkout, "--destroy", job["workspace"], lane_of(drive, job_id, job))


def release_detached(drive: str, job_id: str, job: dict, checkout: Path, log: IO[str]) -> subprocess.Popen:
    return reclaimer_detached(checkout, log, "--destroy", job["workspace"], lane_of(drive, job_id, job))


def reclaim_detached(run: str, dispatch: str, checkout: Path, log: IO[str]) -> subprocess.Popen:
    return reclaimer_detached(checkout, log, "--run", run, "--done", dispatch)


def reclaimer_detached(checkout: Path, log: IO[str], *args: str) -> subprocess.Popen:
    return subprocess.Popen([tool(RECLAIMER), *args], cwd=checkout, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)


def holds_sprite(job: dict) -> bool:
    """Whether cc-remote still records the Sprite workspace the job's lane ran on."""
    return not state.on_mac(job) and bool(job.get("workspace")) and (CC_REMOTE_WORKSPACES / f"{job['workspace']}.json").is_file()


def save(drive: str, job_id: str, job: dict, checkout: Path) -> str | None:
    """Take what the job's Sprite holds that a fresh Sprite would not get to GitHub, by the reclaimer's own read of the checkout it keeps a Sprite for, found by the job's workspace so a Sprite Orca forgot is saved too: it commits each worktree's uncommitted changes on the Sprite and pushes each commit GitHub holds nowhere as saved/<the dead lane's name>/<its branch>, on a GitHub token lent to that one push. What the Sprite held and where it went, naming no branch, which saved reads from GitHub; None when the Sprite holds nothing, or the lane runs on this Mac; OrcaError naming why the Sprite could not be read or the save failed, such as a Sprite that never woke."""
    return None if state.on_mac(job) else reclaimer(checkout, "--save", job["workspace"], lane_of(drive, job_id, job)) or None


def saved(drive: str, job_id: str, job: dict, checkout: Path) -> list[str]:
    """Each branch every save of the job's lane pushed, as GitHub holds it now under saved/<that lane>/ in the repository the drive's checkout and its Sprite lanes clone, by one read, so no text a save printed ever names a branch that is missing; none for a lane on this Mac, which saves nothing."""
    return [] if state.on_mac(job) else github.saved_branches(github.checkout_repo(str(checkout)), lane_of(drive, job_id, job))


def lane_of(drive: str, job_id: str, job: dict) -> str:
    return job.get("lane") or lane_name(drive, job_id)


def reclaimer(checkout: Path, *args: str) -> str:
    """The reclaimer's output, run as a process group of its own; OrcaError naming its last line when it fails, or naming RECLAIM_SECONDS once it runs past them, after every process left in its group, such as an ssh to a Sprite that never answers, is killed."""
    with subprocess.Popen([tool(RECLAIMER), *args], cwd=checkout, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True) as running:
        try:
            out, err = running.communicate(timeout=RECLAIM_SECONDS)
        except subprocess.TimeoutExpired:
            reads.signal_group(running.pid, signal.SIGKILL)
            running.communicate()
            raise OrcaError(f"{RECLAIMER} {args[0]} ran past {RECLAIM_SECONDS} s, so it was killed with every process it started") from None
    if running.returncode:
        said = err.strip().splitlines()
        raise OrcaError(f"{RECLAIMER} exited {running.returncode}: {said[-1] if said else 'no output'}")
    return out.strip()


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
    """The worker an earlier launch of this brief started and the drive never recorded, as a job records it, when the attempt the launcher keeps for the brief holds a ready worker-start receipt, which the launcher refuses to launch the brief again past; else None, as when Orca no longer reaches that worker, which leaves its attempt to the launcher."""
    attempt = brief.with_suffix(".worker")
    status = attempt / "worker-start.status"
    if not status.is_file() or status.read_text().strip() != "0":
        return None
    started = json.loads((attempt / "worker-start.json").read_text()).get("result") or {}
    if started.get("state") != "ready":
        return None
    try:
        return receipt(started["dispatchId"])
    except OrcaError as error:
        if gone(error):
            return None
        raise


def death(worker: dict) -> str | None:
    """Why a lane's worker is gone for good, from its worker-show, or None while Orca still holds it."""
    return "its worker exited" if worker["projection"]["liveness"]["verdict"] == "exited" else None


def gone(error: OrcaError) -> str | None:
    """Why a worker-show refusal means Orca never reaches the lane again: it dropped the Dispatch, or the environment of its Sprite, which ending the lane removes; None when it may reach it yet."""
    return next((why for refusal, why in GONE.items() if refusal in str(error)), None)


def lane_end(drive: str, job: dict) -> LaneEnd | None:
    """None while the lane's worker takes messages; else why it does not, the branch its terminal was on, and, for a worker that finished while Orca holds its terminal connected, on its Sprite or on this Mac, the worktree, terminal and Run it finished in, where a next worker on this Mac starts, read from Orca, since a dispatch recorded by hand records neither. A finished worker is read before its liveness, which Orca cannot always tell once the worker is done, and one whose terminal is no longer connected ends as a dead one does. A lane whose Sprite Orca cannot reach is read again after woken wakes that Sprite. OrcaError when Orca cannot tell either way."""
    try:
        return shown_end(job, worker(job["dispatch"]))
    except OrcaError as error:
        if why := gone(error):
            return LaneEnd(why, None, None)
        if UNREACHABLE not in str(error):
            raise
        return woken(drive, job, f"Orca could not reach its Sprite: {error}")


def woken(drive: str, job: dict, unreached: str) -> LaneEnd | None:
    """Wake the Sprite of a lane Orca cannot reach with the reclaimer's --wake from the drive's checkout, which reopens its forward and adopts the Orca runtime a cold boot started, then read the lane again: None when its worker takes messages again, its end when its worker finished with its terminal held, and else an end marked unreached, which restarts the lane on that Sprite in the checkout it left, where the launcher's prepare --existing wakes it when this wake did not."""
    try:
        reclaimer(checkout(drive, state.load(drive)), "--wake", job["workspace"])
        end = shown_end(job, worker(job["dispatch"]))
    except OrcaError as error:
        return LaneEnd(f"{unreached}, and its wake read {error}", None, None, unreached=True)
    if end is None or end.worktree:
        return end
    return end._replace(why=f"{unreached}, and once it woke {end.why}", unreached=True)


def shown_end(job: dict, shown: dict) -> LaneEnd | None:
    branch = ((shown.get("terminal") or {}).get("branch") or "").removeprefix("refs/heads/") or None
    if why := death(shown):
        return LaneEnd(why, branch, None)
    if (finished := shown["worker"]["state"]) in FINISHED:
        if not shown["terminal"]["connected"]:
            return LaneEnd(f"its worker {finished}, and its terminal {shown['terminal']['handle']} is no longer connected", branch, None)
        why = f"its worker {finished}, and Orca sends nothing to a finished worker {ON_MAC if state.on_mac(job) else 'on a Sprite'}"
        return LaneEnd(why, branch, shown["worker"]["startOptions"]["worktree"], shown["terminal"]["handle"], shown["dispatch"]["runId"])
    if (verdict := shown["projection"]["liveness"]["verdict"]) != "live":
        raise OrcaError(f"orca orchestration worker-show: its worker's liveness is {verdict}")
    return None


def start_after(drive: str, lane: str, brief: Path, end: LaneEnd) -> dict:
    """Start the job's next worker on this Mac in the terminal and worktree of the one that finished, on its Run, with a Task spec that names the brief where the worker reads it: the start Orca names for a finished worker. Orca keeps every Task spec until its database is pruned, and a brief runs to hundreds of kilobytes, so the spec carries only where the brief is, as the launcher's does."""
    spec = f"Lane '{lane}'. Your full brief is the file {brief}: read it in full with the Read tool before anything else and execute it exactly."
    argv = [
        "orca",
        "orchestration",
        "worker-start",
        "--run",
        end.run,
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
        "workspace": None,
        "environment": None,
        "dispatch": receipt["dispatchId"],
        "task": receipt["taskId"],
        "lane": lane,
    }


def place_tools(workspace: str) -> None:
    """Put on the workspace's Sprite the tools a launch puts there, through the orca plugin's own step on the task cc-remote keeps for that Sprite: the released ccx and cc-notes, and each WORKER_TOOLS file, this plugin's `drive` that asks each verb of this Mac; Unfit naming the step's refusal, since no worker starts without them and a live one lacks them."""
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
        raise Unfit(f"{TOOLS} exited {done.returncode} on {workspace}: {said[-1] if said else 'no output'}")


def sprite_answer(answer: str, job: dict, message_id: str) -> str:
    """What a Sprite lane's question gets through Orca: the answer whole when it fits in ANSWER_BYTES, else a line naming the file on the lane's Sprite that holds it, after the `drive exited` line a DRIVE answer opens with, so a lane's drive that reads no such line still exits with the verb's status. Orca keeps a reply four times over, in the message, its question, its relay item and its receipt, so a long answer costs the database four times its size; one the Sprite cannot take goes whole."""
    text = answer.encode()
    if state.on_mac(job) or len(text) <= ANSWER_BYTES:
        return answer
    try:
        path = put_on_sprite(job["workspace"], SPRITE_ANSWERS, f"{message_id}.txt", text)
    except (OrcaError, OSError, subprocess.TimeoutExpired):
        return answer
    opening = status[0] if (status := DRIVE_STATUS.match(answer)) else ""
    return f"{opening}{KEPT_ANSWER}{path} ({len(text)} bytes, SHA-256 {hashlib.sha256(text).hexdigest()}); read it in full\n"


def relayable(answer: str, question: str) -> str:
    """The answer whole when one Orca relay message carries it, else as much of its head as fits, over a line saying how many bytes it cut and that the same question asked again gets the whole answer as a file on the lane's Sprite, as sprite_answer writes it. Orca refuses a relay message past RELAY_BYTES of JSON, where each quote, backslash or newline escapes to two bytes, and RELAY_ROOM keeps room for the ids, keys and subject it wraps an answer in and for that line."""
    room = RELAY_BYTES - RELAY_ROOM
    kept, size = len(answer), relay_bytes(answer)
    if size <= room:
        return answer
    while size > room:
        kept = kept * room // size
        size = relay_bytes(answer[:kept])
    asked = re.sub(r"^DRIVE\b", "drive", question.partition("\n")[0])
    total = len(answer.encode())
    return f"{answer[:kept]}\n[cut {total - len(answer[:kept].encode()):,} of {total:,} bytes, past the 64 KiB one Orca relay message carries: ask `{asked}` again and its whole answer comes as a file on this Sprite]\n"


def relay_bytes(text: str) -> int:
    return len(json.dumps(text, ensure_ascii=False).encode())


def put_on_sprite(workspace: str, folder: str, name: str, text: bytes) -> str:
    """Write the text to the folder under the home of the workspace's Sprite over the ssh forward cc-remote keeps for it, through stdin, since a handoff outgrows one command line; the path it landed at, or Unfit when the Sprite did not keep exactly these bytes."""
    target = f'"$HOME/{folder}/{name}"'
    done = on_sprite(workspace, f'mkdir -p "$HOME/{folder}" && cat >{target} && sha256sum {target}', text, LAUNCH_SECONDS)
    said = done.stdout.decode().split()
    if done.returncode or len(said) != 2 or said[0] != hashlib.sha256(text).hexdigest():
        why = done.stderr.decode().strip().splitlines()
        raise Unfit(f"writing {folder}/{name} onto {workspace} exited {done.returncode}: {why[-1] if why else ' '.join(said) or 'no output'}")
    return said[1]


def take_off_sprite(workspace: str, paths: list[str], into: Path) -> None:
    """Copy each file at its absolute path on the workspace's Sprite to that path under the folder here, in one tar stream over the ssh forward cc-remote keeps for it; Unfit naming the first file the Sprite could not send."""
    done = on_sprite(workspace, shlex.join(["tar", "-C", "/", "-cf", "-", "--", *(path.lstrip("/") for path in paths)]), b"", CALL_SECONDS)
    if done.returncode:
        why = done.stderr.decode().strip().splitlines()
        raise Unfit(f"copying {', '.join(paths)} off {workspace} exited {done.returncode}: {why[0] if why else 'no output'}")
    with tarfile.open(fileobj=io.BytesIO(done.stdout)) as held:
        held.extractall(into, filter="data")


def on_sprite(workspace: str, command: str, given: bytes, seconds: float) -> subprocess.CompletedProcess[bytes]:
    """Run the command on the workspace's Sprite over the ssh forward cc-remote keeps for it, the given bytes on its stdin."""
    forward = json.loads((CC_REMOTE_TASKS / f"{workspace}.json").read_text())["forward"]
    return subprocess.run(
        ["ssh", "-F", forward["config"], "-o", "BatchMode=yes", "-o", "ConnectTimeout=30", forward["host"], command],
        input=given,
        capture_output=True,
        timeout=seconds,
        check=False,
    )


def session_of(workspace: str, dispatch: str) -> str | None:
    """The Claude session the worker on this dispatch ran on the workspace's Sprite, found by the dispatch id Orca's preamble wrote into its transcript, the newest when several hold it; None when the Sprite cannot be read or holds no such transcript."""
    try:
        done = on_sprite(workspace, f"cd && grep -l -F -e {shlex.quote(dispatch)} .claude/projects/*/*.jsonl | xargs -r ls -t | head -n 1", b"", CALL_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return None
    found = Path(done.stdout.decode(errors="replace").strip()).stem
    return found if SESSION_ID.fullmatch(found) else None


def resumed(ended: dict, text: str | None) -> str:
    """The brief of a lane's own Claude session resumed on its Sprite after its worker died there: why it died, what it resumes on, the dispatch it reports on now, and the coordinator's message it never read, when one came."""
    return (
        f"Your lane died at {render.when(ended['at'])}: {ended['why']}. This is your own Claude session, resumed on Sprite {ended['workspace']} in the checkout"
        " you left, so a tool call that was running then never finished. Read its git status and log, then carry the job on. Report on the task and dispatch"
        " the Orca preamble above this brief names, never on your earlier ones.\n"
        + (f"\nThe coordinator's message you never read, which you act on now:\n\n{text.strip()}\n" if text else "")
    )


def death_logs(drive: str, job_id: str, dispatch: str) -> Path:
    """The file in the drive's folder that keeps what keep_logs read from the Sprite of the job's lane that died on this dispatch."""
    return state.drive_dir(drive) / "jobs" / f"{job_id}.{dispatch}.death.txt"


def keep_logs(drive: str, job_id: str, job: dict, dispatch: str) -> None:
    """Copy to death_logs, once a death, what the Sprite of the job's lane that died on this dispatch holds on why its Orca runtime went down, before a relaunch or an end destroys that Sprite: the last SPRITE_LOG_BYTES of the runtime's serve.log, daemon.log and orcad trace and of the worker's Claude Code transcript, the free space on its home volume, its cgroup's memory.events and the kernel's last lines; a Sprite ssh cannot read leaves the file saying why."""
    kept = death_logs(drive, job_id, dispatch)
    if kept.exists():
        return
    try:
        done = on_sprite(job["workspace"], "sh -s", SPRITE_LOGS.encode(), CALL_SECONDS)
        said = done.stdout.decode(errors="replace") + (f"ssh exited {done.returncode}: {done.stderr.decode(errors='replace').strip()}\n" if done.returncode else "")
    except (OSError, subprocess.TimeoutExpired) as error:
        said = f"{job['workspace']} could not be read: {error}\n"
    kept.write_text(said)


def transcripts(drive: str, job_id: str, dispatch: str) -> Path:
    """The folder in the drive's folder that keeps what keep_transcripts copied from the Sprite of the job's lane on this dispatch."""
    return state.drive_dir(drive) / "jobs" / f"{job_id}.{dispatch}{TRANSCRIPTS}"


def keep_transcripts(drive: str, job_id: str, job: dict, dispatch: str) -> Path | None:
    """Copy whole to transcripts, before an end or a relaunch can destroy it, the ~/.claude/projects folder of the Sprite of the job's lane on this dispatch, every Claude Code transcript it holds, the worker's and any earlier lane's on that Sprite; a file the live worker wrote while tar read it, which GNU tar exits 1 for, is still kept. The worker's own transcript, the newest, or None when none was copied, a Sprite ssh cannot read leaving UNKEPT there saying why."""
    kept = transcripts(drive, job_id, dispatch)
    kept.mkdir(parents=True, exist_ok=True)
    try:
        done = on_sprite(job["workspace"], SPRITE_TRANSCRIPTS, b"", CALL_SECONDS)
        if not done.returncode:
            with tarfile.open(fileobj=io.BytesIO(done.stdout), mode="r:gz") as archive:
                archive.extractall(kept, filter="data")
            (kept / UNKEPT).unlink(missing_ok=True)
            return max(kept.glob("*/*.jsonl"), key=lambda path: path.stat().st_mtime, default=None)
        why = f"ssh exited {done.returncode}: {done.stderr.decode(errors='replace').strip()}"
    except (OSError, subprocess.TimeoutExpired, tarfile.TarError) as error:
        why = f"{job['workspace']} could not be read: {error}"
    (kept / UNKEPT).write_text(f"{why}\n")
    return None


def reply(drive: str, run: str, message_id: str, body: str, request: str | None = None) -> None:
    """Answer a lane's question on its Run as the drive's own terminal, then mark the Run's unread mail handled, since Orca's reply leaves the question it answers unread and the watch reads mail by sequence, never by Orca's read mark; one that Orca refuses only leaves Orca's count high. Orca takes a coordinator's reply only on the one Run its terminal is bound to, which a launch or another reply moves; under a request id, a retry Orca already applied is replayed, never applied twice."""
    speak_as_drive(drive)
    with state.binding(drive):
        call("run-use", "--id", run)
        call("reply", "--id", message_id, "--run", run, "--body", body, *retried(request))
        with contextlib.suppress(OrcaError):
            call("check", "--run", run)


def retried(request: str | None) -> tuple[str, ...]:
    return ("--retry-request", request) if request else ()


def bound_command(drive: str, run: str, argv: list[str]) -> subprocess.CompletedProcess:
    """Run a command that needs the Run bound, such as a launch's worker-start, as the drive's own terminal, binding it again once if something outside this drive moved the binding first. A worker-start holds the binding only until Orca takes its request, which request-show then reads pending or done, since Orca checks the binding once, as it admits the start, and its wait for the worker to be ready runs on outside the lock; any other command holds it to its end. Unstarted when it refuses before anything the command starts, a fenced run included, since Orca starts nothing it fenced."""
    argv, request = requested(argv)
    try:
        speak_as_drive(drive)
    except OrcaError as error:
        raise Unstarted(str(error), error.code) from None
    with state.binding(drive):
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


def answer(drive: str, job: dict, text: str, request: str) -> str:
    """Reply to the lane's open question, or send to its Dispatch when it asked nothing or Orca already holds another answer to that question, since the lane no longer waits on it, under the request id the text was held with, so a try Orca applied before its answer was lost is replayed; the send takes an id made from that one, since Orca refuses one id reused for another verb as request_mismatch."""
    if job.get("ask"):
        try:
            reply(drive, job["run"], job["ask"], text, request)
        except OrcaError as error:
            if error.code != ANSWERED_ELSEWHERE:
                raise
        else:
            return f"answered its question {job['ask']}"
    return send(drive, job, "From the coordinator", text, str(uuid.uuid5(uuid.UUID(request), "send")))


def send(drive: str, job: dict, subject: str, text: str, request: str | None = None) -> str:
    """Send the text to the lane's Dispatch as a message, as the drive's own terminal, which neither answers nor clears a question it asked."""
    speak_as_drive(drive)
    call("send", "--to", f"dispatch:{job['dispatch']}", "--run", job["run"], "--subject", subject, "--body", text, *retried(request))
    return f"sent to dispatch {job['dispatch']}"


def takes_messages(worker: dict) -> bool:
    """Whether a worker-show's worker is alive and unfinished, so a message to its Dispatch reaches it."""
    return worker["worker"]["state"] not in FINISHED and worker["projection"]["liveness"]["verdict"] == "live"
