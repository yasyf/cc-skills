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
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

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
UNLIVE = "its worker's liveness is"
GONE = {
    "dispatch_not_found": "Orca no longer holds its worker",
    "Unknown environment": "Orca no longer holds its Sprite's environment",
    "selector_not_found": "Orca no longer holds its worker's terminal or worktree",
    "peer_changed": "Orca no longer pairs with its Sprite's server",
}
FINISHED = ("succeeded", "failed", "stopped", "abandoned")
START_TIMEOUT_MS = "600000"
BRIEF_HEADING = "\n# Brief\n\n"
TAKING_OVER = "## Taking over from "
TRANSCRIPTS_FIELD = re.compile(r"^Transcripts:\n(?:- .*(?:\n|$))+", re.MULTILINE)
TRANSCRIPT_PATH = re.compile(r"^- (j\d+) (\w+)[^:\n]*: ([/~]\S*)$", re.MULTILINE)
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
CONTINUED = "You continue the job in the checkout that holds its PR branch, as it stands, uncommitted edits included."
UNFINISHED = "What its workers were given since the job's last return, which none of them returned a line on, so you carry it on:"
RELOAD = "/reload-plugins"
EXIT = "/exit"
MAIL = "You have orchestration messages: read them with `orca orchestration check`, then continue."
UNANSWERED = ("runtime_busy", "runtime_timeout", "runtime_unavailable", None)
ANSWERED_ELSEWHERE = "answer_conflict"
DISPATCH_CLOSED = "dispatch_inactive"
UNSTARTED = 75
UNFIT = 76
CC_REMOTE = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "cc-remote"
CC_REMOTE_WORKSPACES = CC_REMOTE / "workspaces"
CC_REMOTE_TASKS = CC_REMOTE / "orca"
SPRITE_ANSWERS = ".local/state/long-running/answers"
SPRITE_LOG_BYTES = 20000
HOME_VOLUME = "df -k ~"
MEMORY_EVENTS = "/sys/fs/cgroup/memory.events"
AGES = "cat /proc/uptime; ps -o etimes= -p 1"
MEMORY_LIMITS = "cat /sys/fs/cgroup/memory.high /sys/fs/cgroup/memory.peak"
STARTED_INIT = " as init process"
KERNEL_ERRORS = f"dmesg -r | grep -E '^<[0-3]>|{STARTED_INIT}'"
SPRITE_LOGS = f"""exec 2>&1
cd
worker=$(ls -t .claude/projects/*/*.jsonl | head -n 1)
for log in .cc-remote/orca/serve.log .config/orca/logs/daemon.log .config/orca/logs/orcad.trace.ndjson "$worker"; do
  echo "== tail -c {SPRITE_LOG_BYTES} ~/$log"; tail -c {SPRITE_LOG_BYTES} "$log"; echo
done
echo "== {HOME_VOLUME}"; {HOME_VOLUME}
echo "== {MEMORY_EVENTS}"; cat {MEMORY_EVENTS}
echo "== dmesg | tail -n 50"; dmesg | tail -n 50
echo "== {AGES}"; {AGES}
echo "== {MEMORY_LIMITS}"; {MEMORY_LIMITS}
echo "== {KERNEL_ERRORS}"; {KERNEL_ERRORS}
"""
SECTION = re.compile(r"^== (.*)\n", re.MULTILINE)
KERNEL_LINE = re.compile(r"^<[0-3]>\[\s*[\d.]+\] (.*)$")
GIB = 2**30
SPRITE_TRANSCRIPTS = 'tar -czf - -C "$HOME/.claude/projects" . || [ $? -eq 1 ]'
UNKEPT = "unkept.txt"
TRANSCRIPTS = ".transcripts"
RELAY_BYTES = 64 * 1024
LAUNCH_REASON_CHARS = 2000
LAUNCH_LOG = "launch.log"
ANSWER_MESSAGE_ID = "msg_" + "0" * 12
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
    live: bool = False
    ended: bool = False


class Holder(NamedTuple):
    path: Path
    branch: str
    head: str
    pr_head: str


def request(*args: str, seconds: float = CALL_SECONDS, caller: str | None = None) -> dict:
    """One `orca` call: its result, or OrcaError carrying Orca's own reason; with caller, it speaks as that terminal rather than as this process does."""
    verb = " ".join(args[:2])
    try:
        done = subprocess.run(
            ["orca", *args, "--json"],
            capture_output=True,
            text=True,
            timeout=seconds,
            check=False,
            env=speaking_as(caller) if caller else None,
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


def call(*args: str, caller: str | None = None) -> dict:
    return request("orchestration", *args, "--full", caller=caller)


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


def speaking_as(terminal: str) -> dict[str, str]:
    """This process's environment with every Orca call speaking as the terminal, as speak_as_drive sets it for the drive's own."""
    return {**{key: value for key, value in os.environ.items() if key != "ORCA_PANE_KEY"}, "ORCA_TERMINAL_HANDLE": terminal}


def drive_terminal(drive: str) -> str:
    """The drive's own Orca terminal, a shell running no agent that makes and answers every lane's Run, since Orca types a Run's "You have N orchestration messages" line into the terminal coordinating it only while that terminal runs an agent, by kept_terminal."""
    return kept_terminal(drive, "terminal", "lanes")


def binder_terminal(drive: str) -> str:
    """The drive's second Orca terminal running no agent, which binds with run-use each lane's Run the drive's own terminal does not coordinate, so the run-use, which unbinds every other Run its caller coordinates, never unbinds one of the drive terminal's, by kept_terminal."""
    return kept_terminal(drive, "binder", "binds")


def kept_terminal(drive: str, key: str, title: str) -> str:
    """The agentless Orca terminal the drive keeps under key: the one kept while Orca still shows its pane, else one opened in the coordinator's checkout, titled after the drive and title, and kept, unless another process kept a fresh one first."""
    kept = state.load(drive)[key]
    with contextlib.suppress(OrcaError):
        if kept and not terminal(kept)["orphaned"]:
            return kept
    with state.edit(drive) as data:
        if data[key] == kept:
            data[key] = request("terminal", "create", "--worktree", f"path:{data['roles']['coordinator']['cwd']}", "--title", f"{drive}-{title}")["terminal"]["handle"]
        return data[key]


def reload_command(terminal: str, text: str, request_id: str | None = None) -> str:
    return f"orca terminal send --terminal {terminal} --text {text} --enter" + (f" --retry-request {request_id}" if request_id else "")


def send_command(terminal: str, text: str, request_id: str) -> None:
    """Type a slash command, /reload-plugins or /exit, into a Claude Code session's terminal and submit it under request_id, which Orca binds to that text and that terminal's process, so the same send reissued after a failure that leaves open whether Orca took it replays Orca's receipt rather than typing the command twice; OrcaError when it fails, its code one of UNANSWERED when Orca never said whether it took it."""
    sent = request("terminal", "send", "--terminal", terminal, "--text", text, "--enter", "--retry-request", request_id)["send"]
    if not sent["accepted"]:
        raise OrcaError(f"orca terminal send: refused: {sent.get('refusedReason')}", "refused")


def type_prompt(terminal: str, text: str) -> None:
    """Type a one-line prompt into a Claude Code session's terminal and submit it as two writes, the text and then Enter, which Orca takes whatever it reads of the pane, since it refuses one write of both as agent_prompt_blocked on a pane it reads as showing a permission prompt, as it reads a lane's API error; a drive lane runs with every permission granted and no question tool, so no prompt shows that the Enter could answer."""
    request("terminal", "send", "--terminal", terminal, "--text", text)
    request("terminal", "send", "--terminal", terminal, "--enter")


def terminal(handle: str) -> dict:
    """Orca's record of one terminal; `orphaned` there means Orca dropped its pane, so switching to it shows nothing."""
    return request("terminal", "show", "--terminal", handle)["terminal"]


def session_terminals(sessions: list[str]) -> dict[str, list[dict]]:
    """The Orca terminals each session runs in, by one read of every terminal `orca terminal list` and every pty `orca diagnostics memory` return: each terminal row, an orphaned one included, whose pty's process tree holds the session's Claude Code process, or a tmux client attached to the tmux session holding it, so switching to it lands there; none for a session no terminal shows. Never a record, which goes stale the moment a session changes terminals."""
    listed = request("terminal", "list")
    if listed["truncated"]:
        raise OrcaError(f"orca terminal list: it listed {len(listed['terminals'])} of its {listed['totalCount']} terminals")
    ptys = {pty["sessionId"]: pty["pid"] for worktree in request("diagnostics", "memory")["worktrees"] for pty in worktree["sessions"]}
    tree = parents()
    records = agents.session_records()
    panes = tmux_rows("list-panes", "-a", "-F", "#{pane_pid} #{session_name}")
    clients = tmux_rows("list-clients", "-F", "#{client_pid} #{session_name}")

    def runs_in(session: str) -> list[dict]:
        pids = [pid for pid, record in records if record.get("sessionId") == session]
        held = {name for pane, name in panes if any(holds(tree, pane, pid) for pid in pids)}
        reached = [*pids, *(client for client, name in clients if name in held)]
        return [row for row in listed["terminals"] if row["ptyId"] in ptys and any(holds(tree, ptys[row["ptyId"]], pid) for pid in reached)]

    return {session: runs_in(session) for session in sessions}


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
    return opened(show)


def opened(show: list[str]) -> str:
    """Open the Orca terminal a Start or resume `orca terminal create` line names, split into its words; the new terminal's handle."""
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


def launch_dir(session: str) -> Path | None:
    """The folder the session's live Claude Code process started in, where its tools and the agents it starts run, as its `~/.claude/sessions/<pid>.json` names it; None once it has exited."""
    running = parents()
    return next((Path(record["cwd"]) for pid, record in agents.session_records() if record.get("sessionId") == session and pid in running), None)


def in_tmux(alias: str, session: str) -> bool:
    """Whether the tmux session cci runs a seat in under its alias holds the session's Claude Code process, so cci session run resumes it once it exits."""
    tree = parents()
    pids = claude_pids(session)
    return any(name == alias and any(holds(tree, pane, pid) for pid in pids) for pane, name in tmux_rows("list-panes", "-a", "-F", "#{pane_pid} #{session_name}"))


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
    """A fresh Run, made by the drive's own terminal, which coordinates it beside the Runs it already holds for as long as Orca keeps the Run, since run-create unbinds nothing and no call of the drive's binds that terminal with run-use."""
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
    """What a builder started as an Orca worker reads, on a Sprite with no copy of this plugin or on this Mac: its role, landing rules, brief, on a Sprite each of its Transcripts: a READ cannot serve named by sprite_transcripts."""
    on_mac = route == state.LOCAL_ROUTE
    running = (
        'on this Mac, so it reports through Orca as its "On a Sprite" section says; the rest of that section is for a Sprite'
        if on_mac
        else 'on a Sprite, so its "On a Sprite" section applies'
    )
    return (
        f"Remote lane: drive {drive}, job {job_id}. You are the long-running builder below, running as an Orca "
        f"worker {running}.\n\n"
        f"{builder_body()}\n\n"
        f"# The landing reference of the long-running skill\n\n{LANDING.read_text().strip()}\n"
        f"{BRIEF_HEADING}{(brief if on_mac else sprite_transcripts(drive, brief)).strip()}\n"
    )


def sprite_transcripts(drive: str, brief: str) -> str:
    """The brief with each Transcripts: item naming a path no READ serves, a transcript on this Mac outside the drive's folder, named instead by `drive job replies <j>`, which prints every return that job kept, since a Sprite opens no path on this Mac; a path a READ serves stays, a codex run's folder included, which a READ lists file by file."""

    def served(item: re.Match) -> str:
        return item[0] if any(text is not None for text in drive_files(drive, item[3]).values()) else f"- {item[1]} {item[2]}: drive job replies {item[1]}"

    return TRANSCRIPTS_FIELD.sub(lambda field: TRANSCRIPT_PATH.sub(served, field[0]), brief)


def drive_files(drive: str, brief: str) -> dict[str, str | None]:
    """Each file and folder of the drive's state folder the brief names, by the heading its text goes under, with that text or None when the drive keeps none: each path under the folder's reports, asks, repro, scratch and jobs folders it names anywhere, absolute or from the folder, a reports/<job>.md being that job's report as `drive job report` prints it and a repro/<job>, with any suffix or none, the repro file drive job set --repro kept for it; a folder it names, such as its Scratch: line's, lists each file under it by folder_text, a file that is not UTF-8 text or holds NUL, such as a screen recording, is a line naming its path and size, and a path resolving outside the folder, through `..` or a symlink, has no text, since a lane's READ names its path."""
    folder = state.drive_dir(drive)
    roots = [folder, *([Path("~") / folder.relative_to(Path.home())] if folder.is_relative_to(Path.home()) else [])]
    prefix = "|".join(re.escape(f"{root}/") for root in roots)
    named = [PATH_END.sub("", found) for found in re.findall(rf"(?<![\w./~-])(?:{prefix})?((?:{CARRIED})/{PATH_CHARS})", brief)]
    files: dict[str, str | None] = {}
    for name in named:
        if report := REPORT_FILE.fullmatch(name):
            files[f"Report of {report[1]}"] = state.report_text(drive, report[1]) or None
            continue
        if (repro := REPRO_FILE.fullmatch(name)) and (kept := state.load(drive)["jobs"].get(repro[1], {}).get("repro")):
            name = f"repro/{Path(kept).name}"
        file = folder / name
        inside = file.resolve().is_relative_to(folder.resolve())
        if file.is_dir():
            files[f"Drive folder {name}"] = folder_text(folder, file) if inside else None
            continue
        files[f"Drive file {name}"] = file_text(file) if file.is_file() and inside else None
    return files


def folder_text(folder: Path, named: Path) -> str:
    """Each file under a folder of the drive's, at any depth, a line each by its path from the drive's folder, which a READ of that path reads, and its size; an empty folder says so."""
    inside = folder.resolve()
    held = sorted(entry for entry in named.rglob("*") if entry.is_file() and entry.resolve().is_relative_to(inside))
    return "\n".join(f"{entry.relative_to(folder)} ({entry.stat().st_size:,} bytes)" for entry in held) or "an empty folder"


def file_text(file: Path) -> str:
    data = file.read_bytes()
    with contextlib.suppress(UnicodeDecodeError):
        if "\0" not in (text := data.decode()):
            return text
    return f"{file} is {len(data):,} bytes that are not UTF-8 text, so no read shows them"


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


def handoff(brief: str, ended: dict, saved: list[str], job: dict, report: str, text: str | None, end: LaneEnd, since: str | None, carried: list[str], holds: str, here: bool = False, unsaved: str | None = None, holder: Holder | None = None) -> str:
    """The brief of a lane taking over from one that died, finished or a `drive job end` ended: the job's brief, then what that lane left, its report as the job's history keeps it, with the cause line read off its Sprite when it died there, why the new lane continues on its Sprite when it does, each branch GitHub holds of what that lane's saves pushed, or, when it holds none, what the save its death records found, the texts its workers were given since the job's last return, the message it never read, when a resume brought one, and the drive's read of what holds the job and its PRs now. Here, a move of a job on a Sprite to this Mac, takes it over in a fresh worktree on this Mac however its lane ended. Unsaved, why a save of what that lane's Sprite held failed, names that Sprite kept and the saves of the job's earlier lanes it starts from. Holder, the worktree on this Mac that pr_holder found holding the job's PR branch, is where a lane here continues in place of a fresh worktree, its branch the one the handoff names, by held_by."""
    local = here or state.on_mac(job)
    how = "finished" if end.worktree or end.live else "ended" if end.ended else "died"
    mac = f"in the worktree that holds its PR branch {ON_MAC}" if holder else f"in a fresh worktree {ON_MAC}"
    if end.worktree and not here:
        where = f"You continue the job in its terminal and checkout {ON_MAC}, as it left them." if local else "You continue the job on the same Sprite, in the checkout it left as it was."
    elif since:
        where = f"You continue the job on the same Sprite, in the checkout it left as it was, since {since}. Read its git status and log before anything else."
    elif holder and not here:
        where = CONTINUED
    elif unsaved:
        where = (
            f"You take the job over {mac if local else 'on a fresh Sprite'} from its pushed branches; what its Sprite held on no remote"
            f" could not be saved ({unsaved}), so that Sprite is kept and none of that work reaches you. "
            + (f"The job's earlier lanes saved on GitHub {', '.join(saved)}: fetch each, compare it with its pushed branch, and carry on from the newer. {PUSH_FIRST}" if saved else PUSH_FIRST)
        )
    else:
        where = (
            f"You take the job over {mac if local else 'on a fresh Sprite'} from its pushed branches; "
            + (
                f"what it held on no remote was saved for you on GitHub as {', '.join(saved)}. Fetch each saved branch and carry its work on. {PUSH_FIRST}"
                if saved
                else f"{ended['saved']}. {PUSH_FIRST}"
                if ended.get("saved")
                else UNPUSHED
            )
        )
    if holder:
        where = f"{where} {held_by(holder)}"
    held = f"The lane that held this job {how} {place(job)} at {render.when(ended['at'])}: {state.why_died(ended['why'], ended.get('cause'))}. {where}"
    return taking_over(brief, f"a lane that {how}", held, holder.branch if holder else end.branch, job, report, text and f"The messages it never read, which you act on now:\n\n{text.strip()}\n", carried, holds)


def agent_handoff(brief: str, job: dict, report: str, text: str | None, died: str | None, carried: list[str], holds: str, holder: Holder | None = None) -> str:
    """The brief of an agent or lane taking over a job from an Agent on this Mac that stopped, or from one of another session, which only that session's SendMessage reaches: the job's brief, then what that agent left, the texts its workers were given since the job's last return, the message, when one came, that it never read, and the drive's read of what holds the job and its PRs now; a lane on this Mac continues in the worktree pr_holder found holding the job's PR branch, when one does."""
    where = f"stopped, so it takes no message: {died}" if died else "runs where this session cannot message it"
    took = f"{CONTINUED} {held_by(holder)}" if holder else f"You take the job over from its pushed branches; {UNPUSHED}"
    held = f"The agent that held this job, {job['agent']} of session {job['session']}, {where}. {took}"
    taker = "an agent that stopped" if died else "an agent of another session"
    return taking_over(brief, taker, held, holder.branch if holder else None, job, report, text and f"The message it never read, which you act on now:\n\n{text.strip()}\n", carried, holds)


def held_by(holder: Holder) -> str:
    """What a lane continuing in the worktree that holds the job's PR branch reads first: that worktree, the branch, its HEAD there and GitHub's head of the PR as the drive last read it."""
    return (
        f"The worktree {holder.path} {ON_MAC} holds the branch {holder.branch} of the job's PR, which git lets no other worktree check out, at {holder.head},"
        f" and GitHub's head of that PR, as the drive last read it, is {holder.pr_head or 'not read yet'}: work there, and read its git status and log and compare the two heads before anything else."
    )


def pr_holder(data: dict, job: dict, clone: str) -> Holder | None:
    """The worktree on this Mac, other than the main checkout of clone's repository, that holds the head branch of the job's newest open PR in that repository, by git's worktree list, where the job's next lane here continues, since git checks a branch out in one worktree at a time, so a fresh worktree could never take it; None when no worktree holds one."""
    repo, main = github.checkout_repo(clone), main_checkout(clone)
    for key in map(state.pr_key, reversed(job["prs"])):
        pr = data["prs"][key]
        if pr["state"] == "open" and pr["branch"] and key.startswith(f"{repo}#") and (path := holder(clone, pr["branch"])) and path != main:
            return Holder(path, pr["branch"], github.run(["git", "-C", str(path), "rev-parse", "HEAD"]).strip(), pr["head"])
    return None


def taking_over(brief: str, holder: str, held: str, branch: str | None, job: dict, report: str, message: str | None, carried: list[str], holds: str) -> str:
    prs = ", ".join(job["prs"]) or "none yet"
    return (
        f"{brief.strip()}\n\n"
        f"{TAKING_OVER}{holder}\n\n"
        f"{held}\n\n"
        f"Its branch: {branch or job['branch'] or 'none recorded'}\n"
        f"Its last line: {job['said'] or 'nothing yet'}\n"
        f"Its PRs: {prs}\n\n"
        f"Its last report:\n\n{(report or 'none').strip()}\n"
        + (f"\n{UNFINISHED}\n\n" + "\n\n".join(text.strip() for text in carried) + "\n" if carried else "")
        + (f"\n{message}" if message else "")
        + f"\n{holds}"
    )


def tool(name: str) -> str:
    """A script of the newest installed orca plugin, never the one this session's PATH picked up when it started."""
    plugins = json.loads((config_dir() / "plugins" / "installed_plugins.json").read_text())["plugins"]
    installs = [entry for key, entries in plugins.items() if key.startswith("orca@") for entry in entries]
    if not installs:
        raise OrcaError(f"{name} comes with the orca plugin, which is not installed; install orca@skills")
    newest = max(installs, key=lambda entry: entry.get("lastUpdated") or entry.get("installedAt") or "")
    return str(Path(newest["installPath"]) / "bin" / name)


def checkout(drive: str, clone: str) -> Path:
    """The checkout every session of the drive runs in and each lane's launch, save and release runs the orca plugin's scripts in, so each tools/ci a session or its agents run and every Sprite claim, refill and cleanup run the code and the cc-remote, config and tool payload its repository's default branch pins now: the worktree of clone's repository in the folder drive-<drive>, which only the drive uses, detached at that branch's head with one fetch at each drive start, release start and freeze and before each launch, save and release, never a branch a lane checked out there at whatever commit and edits it carries. It holds no branch: the fetch writes no refs/remotes/origin/<branch>, so a branch moved there would read to the monorepo's Stop hook as commits past that stale ref, unpushed work no session there could end its turn on. git's worktree list finds it by its folder; the first call cuts the branch drive-<drive> at that head and makes it with ccx vcs worktree add, which checks an existing branch out as it stands, running none of its trunk fetch into refs/remotes/origin/<branch>, which loses that ref's lock to any other fetch of the shared clone, so it sits under ccx's worktree root, where its owner's mise trusts a worktree's mise.toml as the cc-remote shim needs. It is moved with no hook, since the monorepo's post-checkout hook warms node_modules that no launch reads, under the drive's checkout lock, since lanes launch at once; DriveError naming why it was not moved, or that the head holds no .orca.json."""
    lane = f"drive-{drive}"
    try:
        branch = github.default_branch(github.checkout_repo(clone))
        with state.flocked(state.drive_dir(drive) / CHECKOUT_LOCK):
            sha = github.fetched(clone, branch)
            if not (kept := worktree(clone, lane)):
                github.run(["git", "-C", clone, "branch", "--force", lane, sha])
                github.run(["ccx", "vcs", "worktree", "add", lane, "--jj", "none"], cwd=clone, timeout=CHECKOUT_SECONDS)
                kept = worktree(clone, lane)
            github.run(["git", "-C", str(kept), "-c", "core.hooksPath=/dev/null", "-c", "checkout.workers=0", "checkout", "-q", "--force", "--detach", sha], timeout=CHECKOUT_SECONDS)
    except github.GithubError as error:
        raise DriveError(f"the drive's checkout, the worktree of {clone}'s repository on {lane}, was not moved to the head of its default branch, so nothing ran from it: {error}") from None
    if not (kept / ".orca.json").is_file():
        raise DriveError(f"{kept}, the drive's checkout at {branch} {sha[:12]}, has no .orca.json, so no lane can be cloned from it")
    return kept


def holder(clone: str, branch: str) -> Path | None:
    """The worktree of clone's repository that has branch checked out, as git's worktree list names it."""
    entries = [entry.split("\n") for entry in github.run(["git", "-C", clone, "worktree", "list", "--porcelain"]).split("\n\n")]
    return next((Path(lines[0].removeprefix("worktree ")) for lines in entries if f"branch refs/heads/{branch}" in lines), None)


def worktree(clone: str, folder: str) -> Path | None:
    """The worktree of clone's repository in a folder of that name, detached or on any branch, as git's worktree list names it."""
    paths = [Path(line.removeprefix("worktree ")) for line in github.run(["git", "-C", clone, "worktree", "list", "--porcelain"]).splitlines() if line.startswith("worktree ")]
    return next((path for path in paths if path.name == folder), None)


def sprite_config(checkout: Path) -> str:
    """The SHA-256 of the cc-remote config a Sprite launched from the checkout is made from, which pins the payload its pool mounts and the keys a launch exports into its terminal, read where the launcher reads it: CC_REMOTE_CONFIG, else ccRemoteConfig in the checkout's .orca.json."""
    config = os.environ.get("CC_REMOTE_CONFIG") or json.loads((checkout / ".orca.json").read_text())["ccRemoteConfig"]
    return hashlib.sha256((checkout / config).read_bytes()).hexdigest()


def launch(drive: str, lane: str, brief: Path, checkout: Path, run: str, route: str | None, workspace: str | None, session: str | None) -> dict:
    """Start one builder through the orca plugin's launcher, on a fresh Sprite or on the workspace's own Sprite in the checkout it holds, there resuming the Claude session given, either way giving its `~/.local/bin` the `drive` that asks each verb of this Mac, or, on the local route, in the worktree on this Mac the workspace names, as it stands, else in a fresh worktree of the checkout's Orca repository, with `CCI_PARENT_SESSION` set, so cci binds the lane's session to no lane and its `cci post` as `root` or `builder` goes out as its brief says, and, under the launcher's `--strict-mcp-config`, the installed Datadog plugin's servers in a file beside the brief; its success line, with the Sprite's prepared server. Each run's whole output goes to the attempt's launch.log by launch_log, and a refusal names that file last."""
    env = {**os.environ, "WORKER_RUN": run, "WORKER_ENV": LANE_ENV, "WORKER_BIND": f"{DRIVE} bound --drive {drive} {run} --"}
    if workspace:
        env["WORKER_WORKSPACE"] = workspace
    if session:
        env["WORKER_SESSION"] = session
    local = route == state.LOCAL_ROUTE
    if local:
        repo = request("repo", "show", "--repo", f"path:{main_checkout(str(checkout))}")["repo"]["id"]
        servers = brief.with_suffix(".mcp.json")
        servers.write_text(json.dumps(agents.datadog_servers()))
        env |= {"WORKER_LOCAL": "1", "WORKER_REPO": repo, "WORKER_ENV": f"{LANE_ENV} CCI_PARENT_SESSION={drive}", "WORKER_MCP_CONFIG": str(servers)}
    else:
        env["WORKER_TOOLS"] = SPRITE_TOOLS
    attempt = brief.with_suffix(".worker")
    where = ON_MAC if local else f"on Sprite {workspace} in the checkout it holds" + (f", resuming Claude session {session}" if session else "") if workspace else "on a fresh Sprite"
    state.said(lane, f"it launches the lane {where} with {LAUNCHER}, for at most {LAUNCH_SECONDS} s")
    try:
        done = grouped([tool(LAUNCHER), lane, MODEL, EFFORT, str(brief)], cwd=checkout, env=env, timeout=LAUNCH_SECONDS)
    except subprocess.TimeoutExpired as expired:
        kept = launch_log(attempt, f"ran past {LAUNCH_SECONDS} s", expired.stdout or "", expired.stderr or "")
        if (attempt / "worker-start.json").exists():
            raise OrcaError(
                f"{LAUNCHER} ran past {LAUNCH_SECONDS} s after its worker-start began, so it was killed with every process it started and the lane may be live;"
                f" read {attempt}/worker-start.json before starting it anywhere else{kept}"
            ) from None
        raise Unstarted(f"{LAUNCHER} ran past {LAUNCH_SECONDS} s before its worker-start began, so it was killed with every process it started; its attempt is in {attempt}{kept}") from None
    kept = launch_log(attempt, f"exited {done.returncode}", done.stdout, done.stderr)
    lines = done.stdout.strip().splitlines()
    if done.returncode or not lines:
        said = "; ".join(line.strip() for line in done.stderr.strip().splitlines()) or "no output"
        reason = said if len(said) <= LAUNCH_REASON_CHARS else "…" + said[-LAUNCH_REASON_CHARS:]
        if done.returncode == UNFIT:
            raise Unfit(reason + kept)
        if unsent(brief):
            raise Unstarted(reason + kept)
        if (attempt / "worker-start.status").exists():
            raise OrcaError(
                f"{LAUNCHER} exited {done.returncode} after worker-start ran, so the lane may be live: {reason}; "
                f"read {attempt}/worker-start.json before starting it anywhere else{kept}"
            )
        raise Unstarted(f"{LAUNCHER} exited {done.returncode} before a worker started: {reason}{kept}")
    receipt = json.loads(lines[-1])
    if local:
        return {**receipt, "lane": lane, "workspace": None, "environment": None}
    prepared = json.loads((brief.with_suffix(".worker") / "prepared.json").read_text())
    return {**receipt, "environment": prepared["environment"]}


def launch_log(attempt: Path, outcome: str, out: str, err: str) -> str:
    """Append one launcher run's whole stdout and stderr to its attempt's launch.log, under a line stamping when it ended and how: the clause a refusal ends with naming that file, or none when the launcher refused before it made the attempt folder, whose one line the refusal then quotes whole, since a folder holding only that log would read as an earlier attempt to the next launch."""
    if not attempt.is_dir():
        return ""
    log = attempt / LAUNCH_LOG
    with log.open("a") as kept:
        kept.write(f"== {state.stamp()} {LAUNCHER} {outcome}\n-- stdout\n{out}-- stderr\n{err}")
    return f"; the launcher's whole output is in {log}"


def unsent(brief: Path) -> bool:
    """Whether a launch of this brief prepared a Sprite whose worker-start sent nothing, which a launch of the same brief reuses."""
    return (brief.with_suffix(".worker") / "worker-start.unsent").exists()


def reclaim(run: str, dispatch: str, checkout: Path) -> str:
    return reclaimer(checkout, "--run", run, "--done", dispatch)


def release(drive: str, job_id: str, job: dict, checkout: Path) -> str:
    """Destroy the Sprite at the job's workspace, with its Orca environment and forward, once the reclaimer's --destroy has saved what it holds on no remote to GitHub as save does: what it saved and destroyed; OrcaError naming why its checkout could not be read, the save failed, what it still holds, or cc-remote's refusal, which keeps it."""
    state.said(job_id, f"it destroys Sprite {job['workspace']} with {RECLAIMER} --destroy, for at most {RECLAIM_SECONDS} s")
    return reclaimer(checkout, "--destroy", job["workspace"], lane_of(drive, job_id, job))


def holds_sprite(job: dict) -> bool:
    """Whether cc-remote still records the Sprite workspace the job's lane ran on."""
    return not state.on_mac(job) and bool(job.get("workspace")) and (CC_REMOTE_WORKSPACES / f"{job['workspace']}.json").is_file()


def save(drive: str, job_id: str, job: dict, checkout: Path) -> str | None:
    """Take what the job's Sprite holds that a fresh Sprite would not get to GitHub, by the reclaimer's own read of the checkout it keeps a Sprite for, found by the job's workspace so a Sprite Orca forgot is saved too: it commits each worktree's uncommitted changes on the Sprite and pushes each commit GitHub holds nowhere as saved/<the dead lane's name>/<its branch>, on a GitHub token lent to that one push. What the Sprite held and where it went, naming no branch, which saved reads from GitHub; that it held nothing unpushed or uncommitted; or that cc-remote records no such Sprite, so nothing on it was read or saved; None for a lane on this Mac; OrcaError naming why the Sprite could not be read or the save failed, such as a Sprite that never woke."""
    if state.on_mac(job):
        return None
    state.said(job_id, f"it saves to GitHub what Sprite {job['workspace']} holds on no remote with {RECLAIMER} --save, for at most {RECLAIM_SECONDS} s")
    return reclaimer(checkout, "--save", job["workspace"], lane_of(drive, job_id, job))


def saved(drive: str, job_id: str, job: dict, checkout: Path, every: bool = False) -> list[str]:
    """Each branch every save of the job's lane pushed, or with every, of each lane the job ran in, as GitHub holds it now under saved/<that lane>/ in the repository the drive's checkout and its Sprite lanes clone, by one read, so no text a save printed ever names a branch that is missing; none for a lane on this Mac, which saves nothing."""
    if state.on_mac(job):
        return []
    return github.saved_branches(github.checkout_repo(str(checkout)), lane_name(drive, job_id) if every else lane_of(drive, job_id, job), every)


def lane_of(drive: str, job_id: str, job: dict) -> str:
    return job.get("lane") or lane_name(drive, job_id)


def reclaimer(checkout: Path, *args: str) -> str:
    """The reclaimer's output, run by grouped; OrcaError naming its last line when it fails, or naming RECLAIM_SECONDS once it runs past them and grouped killed it with every process it started."""
    try:
        done = grouped([tool(RECLAIMER), *args], cwd=checkout, timeout=RECLAIM_SECONDS)
    except subprocess.TimeoutExpired:
        raise OrcaError(f"{RECLAIMER} {args[0]} ran past {RECLAIM_SECONDS} s, so it was killed with every process it started") from None
    if done.returncode:
        said = done.stderr.strip().splitlines()
        raise OrcaError(f"{RECLAIMER} exited {done.returncode}: {said[-1] if said else 'no output'}")
    return done.stdout.strip()


def grouped(argv: list[str], *, timeout: float, **given: object) -> subprocess.CompletedProcess[str]:
    """Run argv as a process group of its own with its output captured as text, as subprocess.run does; subprocess.TimeoutExpired carrying all it wrote once it runs past timeout seconds, after every process left in its group is killed, such as the ssh or sprite exec a launcher or the reclaimer left waiting on a Sprite that never answers, which a kill of argv's own process alone leaves running."""
    with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True, **given) as running:
        try:
            out, err = running.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            reads.signal_group(running.pid, signal.SIGKILL)
            raise subprocess.TimeoutExpired(argv, timeout, *running.communicate()) from None
    return subprocess.CompletedProcess(argv, running.returncode, out, err)


def mail(after: int | None) -> list[dict]:
    """Orca's mail to every Run past sequence `after`, or with none among its newest MAIL_LIMIT messages, oldest first, read without binding or consuming, so one call hears every lane whatever Run Orca filed its mail on, and none of the mail sent to a worker's own Dispatch. Orca returns a mailbox's newest rows, so a read that comes back full with none at or before `after` goes again ten times as deep."""
    limit = MAIL_LIMIT
    while len(messages := call("inbox", "--limit", str(limit))["messages"]) == limit and after is not None and min(message["sequence"] for message in messages) > after:
        limit *= 10
    return sorted((message for message in messages if message["to_handle"].startswith("run:") and (after is None or message["sequence"] > after)), key=lambda message: message["sequence"])


def newest_mail() -> list[dict]:
    """The newest MAIL_LIMIT messages to every recipient, read without binding or consuming, so one call hears every lane's newest questions."""
    return call("inbox", "--limit", str(MAIL_LIMIT))["messages"]


def mailbox(job: dict, dispatch: str) -> list[dict]:
    """The mail in one Dispatch mailbox of the job, read or not, oldest first, from the Orca that keeps it: this Mac's for a lane here, and for a lane on a Sprite that Sprite's own, by the job's environment, since this Mac's Orca only relays a Sprite lane's mail and keeps none of it; read without binding or consuming as mail reads every Run's. A message keeps its read flag there once its worker reads it, and Orca moves a reported worker's unread mail to the mailbox of the follow-up it starts to carry it."""
    environment = [] if state.on_mac(job) else ["--environment", job["environment"]]
    messages = call("inbox", *environment, "--terminal", f"dispatch:{dispatch}", "--limit", str(MAIL_LIMIT))["messages"]
    return sorted((message for message in messages if message["to_handle"] == f"dispatch:{dispatch}"), key=lambda message: message["sequence"])


def unread_notice(message: dict) -> bool:
    """Whether the message is Orca's notice that its mail did not reach a finished worker, which says nothing of the lane's work, since the watch reads that mail itself in the mailbox of the follow-up Orca started to carry it: an escalation whose payload names a reason and, unlike each of Orca's stop escalations, no stopReason."""
    if message["type"] != "escalation":
        return False
    payload = json.loads(message["payload"] or "{}")
    return "reason" in payload and "stopReason" not in payload


def worker(dispatch: str) -> dict:
    return call("worker-show", "--dispatch", dispatch)


def follow_up(dispatch: str) -> dict | None:
    """The worker-show of a Dispatch Orca started as the follow-up that carries the mail a worker had not read when it reported, in that worker's terminal, its followUpOf naming that worker's Dispatch and its worker's state failed when that start failed; None for any other Dispatch, or one Orca no longer holds. OrcaError when Orca cannot tell."""
    try:
        shown = worker(dispatch)
    except OrcaError as error:
        if gone(error):
            return None
        raise
    return shown if shown["dispatch"].get("followUpOf") else None


def placement(worker: dict) -> dict:
    """Where a worker-show's worker runs, as a job records it: on the Sprite of its paired server when Orca names its host remote, else on this Mac."""
    if (worker["projection"].get("host") or {}).get("kind") != "remote":
        return {"route": state.LOCAL_ROUTE, "workspace": None, "environment": None}
    return {"route": None, "workspace": worker["server"]["name"], "environment": worker["server"]["name"]}


def run_of(worker: dict) -> dict:
    """The Run and task a worker-show's dispatch belongs to and the terminal Orca recorded for its worker, which a job records beside that dispatch, so it never keeps a Run its dispatch is not on."""
    return {"run": worker["dispatch"]["runId"], "task": worker["dispatch"]["taskId"], "terminal": worker["worker"]["agentTerminalHandle"]}


def task_title(run: str, task: str) -> str:
    """The title of a Task on a Run, which a launch names after the lane it starts."""
    return next(found["title"] for found in request("orchestration", "task-list", "--run", run)["tasks"] if found["id"] == task)


def receipt(dispatch: str) -> dict:
    """What a job records of the worker on a dispatch, as Orca shows it: the dispatch, its Run, task and terminal, the lane its task is titled after, and where it runs, with no cc-remote config, which Orca does not show, so a finished worker's next starts on a fresh Sprite."""
    shown = worker(dispatch)
    held = run_of(shown)
    return {"dispatch": dispatch, **held, "lane": task_title(held["run"], held["task"]), **placement(shown), "sprite_config": None}


def adopted(brief: Path) -> dict | None:
    """The worker an earlier launch of this brief started and the drive never recorded, as a job records it, when the attempt the launcher keeps for the brief holds a ready worker-start receipt, which the launcher refuses to launch the brief again past; else None, as when Orca no longer reaches that worker, which leaves its attempt to the launcher."""
    if not (started := kept_start(brief)):
        return None
    try:
        return receipt(started["dispatchId"])
    except OrcaError as error:
        if gone(error):
            return None
        raise



def kept_start(brief: Path) -> dict | None:
    """The ready worker-start receipt the launcher keeps in the attempt of this brief, None when that attempt started no worker or there is none."""
    attempt = brief.with_suffix(".worker")
    status = attempt / "worker-start.status"
    if not status.is_file() or status.read_text().strip() != "0":
        return None
    started = json.loads((attempt / "worker-start.json").read_text()).get("result") or {}
    return started if started.get("state") == "ready" else None

def death(worker: dict) -> str | None:
    """Why a lane's worker is gone for good, from its worker-show, or None while Orca still holds it."""
    return "its worker exited" if worker["projection"]["liveness"]["verdict"] == "exited" else None


def gone(error: OrcaError) -> str | None:
    """Why a worker-show refusal means Orca never reaches the lane again: it dropped the Dispatch, or the environment of its Sprite, which ending the lane removes; None when it may reach it yet."""
    return next((why for refusal, why in GONE.items() if refusal in str(error)), None)


def lane_end(drive: str, job_id: str, job: dict, wake: bool = True) -> LaneEnd | None:
    """None while the lane's worker takes messages; else why it does not, the branch its terminal was on, and, for a worker that finished, on its Sprite or on this Mac, the worktree, terminal and Run it finished in, where a next worker on this Mac starts, read from Orca, since a dispatch recorded by hand records neither. A finished worker is read before its liveness, which Orca cannot always tell once the worker is done, and its terminal is the one Orca recorded for it, since a worker-show that cannot tie that terminal to the worker's process shows none; one whose terminal Orca shows no longer connected ends as a dead one does. A lane whose Sprite Orca cannot reach is read again after woken wakes that Sprite; without wake, as a move to this Mac reads it, that lane, or one whose worker Orca cannot show live, ends unreached at once, its Sprite left as it is. A lane on this Mac that Orca cannot tell is live takes messages while typed finds its terminal. A lane a `drive job end` ended is read from the end its job records, never from Orca, since that end stopped its worker and destroyed its Sprite, which Orca then reads as a lane it no longer holds or pairs with. OrcaError when Orca cannot tell either way."""
    if state.lane_ended(job):
        return LaneEnd(f"the drive job end of {state.lane_death(job, 'dispatch')['ended']['at']} ended it", None, None, ended=True)
    try:
        return shown_end(job, worker(job["dispatch"]))
    except OrcaError as error:
        if why := gone(error):
            return LaneEnd(why, None, None)
        if not wake and out_of_reach(error):
            return LaneEnd(f"Orca could not reach its Sprite: {error}", None, None, unreached=True)
        if UNREACHABLE not in str(error):
            raise
        return woken(drive, job_id, job, f"Orca could not reach its Sprite: {error}")


def out_of_reach(error: OrcaError) -> bool:
    """Whether a worker-show refusal says Orca cannot reach the lane's Sprite or cannot show its worker live, rather than that the lane ended or Orca failed on this Mac."""
    return UNREACHABLE in str(error) or UNLIVE in str(error)


def locked_out(drive: str, job_id: str, job: dict) -> str | None:
    """Why a resume of the job is refused now rather than queued for its lock: another drive verb holds that lock while Orca cannot reach the job's lane on its Sprite, as when a resume wakes a Sprite that does not answer, naming that verb by state.holders and Orca's refusal; None for a job on no Sprite lane, one whose lock is free, or one whose lane Orca reaches."""
    if not (job.get("where") == "remote" and job.get("dispatch") and state.job_lock_held(drive, job_id)):
        return None
    try:
        worker(job["dispatch"])
    except OrcaError as error:
        if out_of_reach(error):
            return f"another drive verb holds its lock ({state.holders(state.job_lock_path(drive, job_id))}) while Orca cannot reach its lane on its Sprite ({error})"
    return None


def moved_here(job_id: str) -> str:
    """The local launch that reaches a job locked_out names a reason for: it moves the job to a fresh worktree on this Mac, the verb holding the lock standing down for it at its next step, and its handoff carries every text the job holds."""
    return f"drive job launch {job_id} --local moves it to a fresh worktree {ON_MAC} now, the verb holding its lock standing down at its next step, and its handoff carries every text the job holds"


def woken(drive: str, job_id: str, job: dict, unreached: str) -> LaneEnd | None:
    """Wake the Sprite of a lane Orca cannot reach with the reclaimer's --wake from the drive's checkout, which reopens its forward and adopts the Orca runtime a cold boot started, then read the lane again, saying each step by state.said: None when its worker takes messages again, its end when its worker finished with its terminal held, and else what its Sprite says over cc-remote's ssh: the refusal again when something listens on the port Orca's runtime serves on there, since Orca then fails on this Mac and no lane restarts for it, and otherwise an unfit end, which restarts the lane on a fresh Sprite, by his pick "Wake and restart in place: on an unreachable lane the watch wakes its Sprite and restarts the lane there, else on a fresh Sprite": when it answers with nothing listening there, as when a full disk crash-loops that runtime, which saves the lane first, and when it does not answer, as a Sprite whose memory thrashes until its sshd sends no banner does, which restart asks nothing more and keeps, since every later step on it, a launcher's prepare --existing there included, waits on that same silence."""
    workspace = job["workspace"]
    state.said(job_id, f"Orca cannot reach its lane, so it wakes Sprite {workspace} with {RECLAIMER} --wake, for at most {RECLAIM_SECONDS} s")
    try:
        reclaimer(checkout(drive, state.load(drive)["roles"]["coordinator"]["cwd"]), "--wake", workspace)
        end = shown_end(job, worker(job["dispatch"]))
    except OrcaError as error:
        unreached = f"{unreached}, and its wake read {error}"
        state.said(job_id, f"its wake read {error}; it asks Sprite {workspace} over ssh what listens on the port Orca's runtime serves on there, for at most {CALL_SECONDS} s")
        match runtime_listening(workspace):
            case True:
                raise
            case False:
                return LaneEnd(f"{unreached}, and its Sprite has nothing listening on the port Orca's runtime serves on there", None, None, unfit=True)
            case silent:
                return LaneEnd(f"{unreached}, and its Sprite did not answer: {silent}", None, None, unfit=True)
    if end is None or end.worktree:
        return end
    return end._replace(why=f"{unreached}, and once it woke {end.why}", unreached=True)


def shown_end(job: dict, shown: dict) -> LaneEnd | None:
    observed = shown.get("terminal")
    branch = ((observed or {}).get("branch") or "").removeprefix("refs/heads/") or None
    if why := death(shown):
        return LaneEnd(why, branch, None)
    if (finished := shown["worker"]["state"]) in FINISHED:
        terminal = shown["worker"]["agentTerminalHandle"]
        if observed and not observed["connected"]:
            return LaneEnd(f"its worker {finished}, and its terminal {terminal} is no longer connected", branch, None)
        why = f"its worker {finished}, and Orca sends nothing to a finished worker {ON_MAC if state.on_mac(job) else 'on a Sprite'}"
        return LaneEnd(why, branch, shown["worker"]["startOptions"]["worktree"], terminal, shown["dispatch"]["runId"])
    if (verdict := shown["projection"]["liveness"]["verdict"]) != "live" and not typed(job, shown):
        raise OrcaError(f"orca orchestration worker-show: {UNLIVE} {verdict}")
    return None


def typed(job: dict, shown: dict) -> str | None:
    """The terminal of a lane on this Mac that Orca shows as its own unfinished worker in a terminal it can write to, which the drive types into to reach it whatever liveness Orca reads, since Orca reads none once an Orca restart restores the pane's hook status unconfirmed or the pane sat 30 minutes with no hook event; None for any other lane."""
    observation = shown.get("observation") or {}
    reachable = observation.get("status") == "live" and observation.get("exactWorker") and (shown.get("terminal") or {}).get("writable")
    if not state.on_mac(job) or shown["worker"]["state"] in FINISHED or not reachable:
        return None
    return shown["worker"]["agentTerminalHandle"]


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
        "sprite_config": None,
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
    """What a Sprite lane's question gets through Orca: the answer whole when one Orca relay message carries it, else a line naming the file on the lane's Sprite that holds it, after its exit_line, so a lane's drive that reads no such line still exits with the verb's status. Orca keeps a reply past what its relay carries as the question's answer and never relays it; OrcaError when the Sprite cannot take the file within CALL_SECONDS."""
    text = answer.encode()
    if state.on_mac(job) or relay_bytes(message_id, answer) <= RELAY_BYTES:
        return answer
    try:
        path = put_on_sprite(job["workspace"], SPRITE_ANSWERS, f"{message_id}.txt", text)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OrcaError(f"writing {SPRITE_ANSWERS}/{message_id}.txt onto {job['workspace']}: {error}") from None
    return f"{exit_line(answer)}{KEPT_ANSWER}{path} ({len(text)} bytes, SHA-256 {hashlib.sha256(text).hexdigest()}); read it in full\n"


def exit_line(answer: str) -> str:
    """The `drive exited` line a DRIVE answer opens with, or nothing for any other answer."""
    return status[0] if (status := DRIVE_STATUS.match(answer)) else ""


def relay_bytes(message_id: str, answer: str) -> int:
    """The bytes of the relay message Orca builds to carry this answer to the question's Sprite, counted as Orca counts them before it refuses one past RELAY_BYTES: the UTF-8 length of `JSON.stringify({questionId, answerMessageId, body})`, its answer message's id always `msg_` and 12 hex digits."""
    return len(json.dumps({"questionId": message_id, "answerMessageId": ANSWER_MESSAGE_ID, "body": answer}, ensure_ascii=False, separators=(",", ":")).encode())


def put_on_sprite(workspace: str, folder: str, name: str, text: bytes) -> str:
    """Write the text to the folder under the home of the workspace's Sprite over the ssh forward cc-remote keeps for it, through stdin, since an answer outgrows one command line; the path it landed at, or Unfit when the Sprite did not keep exactly these bytes, and subprocess.TimeoutExpired past CALL_SECONDS, so a Sprite that takes the connection and never answers holds its lane's answer a minute, never half an hour."""
    target = f'"$HOME/{folder}/{name}"'
    done = on_sprite(workspace, f'mkdir -p "$HOME/{folder}" && cat >{target} && sha256sum {target}', text, CALL_SECONDS)
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


def runtime_listening(workspace: str) -> bool | str:
    """Whether anything on the workspace's Sprite listens on the port its cc-remote task runs Orca's runtime on, asked over cc-remote's ssh forward, never through that runtime; else why it got no answer: cc-remote records no task for it, its ssh exited non-zero, or it ran past CALL_SECONDS."""
    try:
        port = json.loads((CC_REMOTE_TASKS / f"{workspace}.json").read_text())["port"]
    except OSError:
        return "cc-remote records no task for it"
    try:
        done = on_sprite(workspace, f"ss -Hltn 'sport = :{port}'", b"", CALL_SECONDS)
    except subprocess.TimeoutExpired:
        return f"ssh to it ran past {CALL_SECONDS} s"
    if done.returncode:
        why = done.stderr.decode(errors="replace").strip().splitlines()
        return f"ssh exited {done.returncode}: {why[-1] if why else 'no output'}"
    return bool(done.stdout.strip())


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


def session_of(job_id: str, workspace: str, dispatch: str) -> str | None:
    """The Claude session the worker on this dispatch ran on the workspace's Sprite, found by the dispatch id Orca's preamble wrote into its transcript, the newest when several hold it; None when the Sprite cannot be read or holds no such transcript."""
    state.said(job_id, f"it asks Sprite {workspace} over ssh for the Claude session its worker ran, for at most {CALL_SECONDS} s")
    try:
        done = on_sprite(workspace, f"cd && grep -l -F -e {shlex.quote(dispatch)} .claude/projects/*/*.jsonl | xargs -r ls -t | head -n 1", b"", CALL_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return None
    found = Path(done.stdout.decode(errors="replace").strip()).stem
    return found if SESSION_ID.fullmatch(found) else None


def resumed(ended: dict, text: str | None, holds: str) -> str:
    """The brief of a lane's own Claude session resumed on its Sprite after its worker died there: why it died, with the cause line read off its Sprite, what it resumes on, the dispatch it reports on now, the messages it never read, when any came, naming no sender, since they can be his answers as well as the coordinator's, and the drive's read of what holds the job and its PRs now."""
    return (
        f"Your lane died at {render.when(ended['at'])}: {state.why_died(ended['why'], ended.get('cause'))}. This is your own Claude session, resumed on Sprite {ended['workspace']} in the checkout"
        " you left, so a tool call that was running then never finished. Read its git status and log, then carry the job on. Report on the task and dispatch"
        " the Orca preamble above this brief names, never on your earlier ones.\n"
        + (f"\nThe messages you never read, which you act on now:\n\n{text.strip()}\n" if text else "")
        + f"\n{holds}"
    )


def death_logs(drive: str, job_id: str, dispatch: str) -> Path:
    """The file in the drive's folder that keeps what keep_logs read from the Sprite of the job's lane that died on this dispatch."""
    return state.drive_dir(drive) / "jobs" / f"{job_id}.{dispatch}.death.txt"


def keep_logs(drive: str, job_id: str, job: dict, dispatch: str) -> tuple[str, bool, bool, bool]:
    """Copy to death_logs, once a death, what the Sprite of the job's lane that died or finished on this dispatch holds on why its Orca runtime went down, before a relaunch reuses that Sprite or a relaunch or an end destroys it: the last SPRITE_LOG_BYTES of the runtime's serve.log, daemon.log and orcad trace and of the worker's Claude Code transcript, the free space on its home volume, its cgroup's memory.events, the kernel's last lines, the ages of its VM and its pid 1, its cgroup's memory.high and memory.peak, and every kernel line at error level or worse; a Sprite ssh cannot read leaves the file saying why. The cause line those copied logs give, whether that Sprite faulted and whether it failed on its own, by cause, and whether it answered, which a read that ended before the kernel's lines says it did not."""
    kept = death_logs(drive, job_id, dispatch)
    if not kept.exists():
        state.said(job_id, f"it copies the logs of Sprite {job['workspace']} over ssh, for at most {CALL_SECONDS} s")
        try:
            done = on_sprite(job["workspace"], "sh -s", SPRITE_LOGS.encode(), CALL_SECONDS)
            said = done.stdout.decode(errors="replace") + (f"ssh exited {done.returncode}: {done.stderr.decode(errors='replace').strip()}\n" if done.returncode else "")
        except (OSError, subprocess.TimeoutExpired) as error:
            said = f"{job['workspace']} could not be read: {error}\n"
        kept.write_text(said)
    text = kept.read_text()
    return *cause(text), f"== {KERNEL_ERRORS}\n" in text


def cause(said: str) -> tuple[str, bool, bool]:
    """One line on why a lane's Sprite failed, from what keep_logs copied off it, and whether that Sprite faulted, which a kernel line at error level or worse after init or a full home volume says, so no worker runs on it again: each kernel line at error level or worse logged after the kernel started init, since a Sprite's boot logs one before init on every Sprite, counted with the first and the last quoted; memory.high, when its cgroup went over it, with memory.peak; the OOM killer's kills; a full home volume; and how long its VM and its pid 1 had run, which shows whether the VM rebooted or only its processes restarted. That Sprite failed on its own, so its death counts against no job, when its kernel logged an error line after init while the OOM killer killed nothing and its home volume was not full, as when an nbd0 I/O error remounts its root disk read-only, since a job's own load shows as an OOM kill, which the kernel logs at error level too, or a full home volume. A Sprite whose read ended before the kernel's lines gives why that read ended, no fault and no failure of its own."""
    parts = SECTION.split(said)
    read = dict(zip(parts[1::2], parts[2::2], strict=True))
    if KERNEL_ERRORS not in read:
        _, exited, rest = said.rpartition("ssh exited ")
        return f"its Sprite did not answer: {exited + ' '.join(rest.split()) if exited else said.strip().splitlines()[-1]}", False, False
    lines = read[KERNEL_ERRORS].splitlines()
    booted = next((index + 1 for index, line in enumerate(lines) if line.endswith(STARTED_INIT)), 0)
    errors = [found[1] for line in lines[booted:] if (found := KERNEL_LINE.match(line))]
    shown = [
        f'the kernel logged {len(errors)} error lines after it started init, the first "{errors[0]}" and the last "{errors[-1]}"'
        if len(errors) > 1
        else f'the kernel logged one error line after it started init, "{errors[0]}"'
        if errors
        else "the kernel logged no error line after it started init"
    ]
    events = dict(line.split() for line in read[MEMORY_EVENTS].splitlines())
    high, peak = read[MEMORY_LIMITS].split()
    if events["high"] != "0":
        shown.append(f"its memory went over memory.high, {int(high) / GIB:.1f} GiB, {events['high']} times and peaked at {int(peak) / GIB:.1f} GiB")
    if killed := events["oom_kill"] != "0":
        shown.append(f"the OOM killer killed {events['oom_kill']} of its processes")
    if full := read[HOME_VOLUME].split()[-2] == "100%":
        shown.append("its home volume was full")
    vm, _, pid1 = read[AGES].split()
    shown.append(f"when read, its VM had been up {int(float(vm) // 60)} minutes, its init process {int(pid1) // 60} minutes")
    return f"its Sprite showed: {'; '.join(shown)}", bool(errors) or full, bool(errors) and not (killed or full)


def transcripts(drive: str, job_id: str, dispatch: str) -> Path:
    """The folder in the drive's folder that keeps what keep_transcripts copied from the Sprite of the job's lane on this dispatch."""
    return state.drive_dir(drive) / "jobs" / f"{job_id}.{dispatch}{TRANSCRIPTS}"


def keep_transcripts(drive: str, job_id: str, job: dict, dispatch: str) -> Path | None:
    """Copy whole to transcripts, before an end or a relaunch can destroy it, the ~/.claude/projects folder of the Sprite of the job's lane on this dispatch, every Claude Code transcript it holds, the worker's and any earlier lane's on that Sprite; a file the live worker wrote while tar read it, which GNU tar exits 1 for, is still kept. The worker's own transcript, the newest, or None when none was copied, a Sprite ssh cannot read leaving UNKEPT there saying why."""
    kept = transcripts(drive, job_id, dispatch)
    kept.mkdir(parents=True, exist_ok=True)
    state.said(job_id, f"it copies the Claude Code transcripts of Sprite {job['workspace']} over ssh, for at most {CALL_SECONDS} s")
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
    """Answer a lane's question on its Run in one reply as the drive's own terminal, which coordinates every Run new_run made, so no reply waits on another; a Run Orca fences because that terminal does not coordinate it, such as one another terminal made, is answered by binder_terminal once binder binds it there. Under a request id, a retry Orca already applied is replayed, never applied twice."""
    answering = ("reply", "--id", message_id, "--run", run, "--body", body, *retried(request))
    speak_as_drive(drive)
    try:
        call(*answering)
    except OrcaError as error:
        if error.code != FENCED:
            raise
        with binder(drive, run) as terminal:
            call(*answering, caller=terminal)


@contextlib.contextmanager
def binder(drive: str, run: str) -> Iterator[str]:
    """Bind the Run to the drive's binder_terminal with run-use and hold the drive's binding lock for the calls made inside as that terminal, whose handle it gives, since a run-use moves the binder's binding off whatever Run it held."""
    terminal = binder_terminal(drive)
    with state.binding(drive):
        call("run-use", "--id", run, caller=terminal)
        yield terminal


def retried(request: str | None) -> tuple[str, ...]:
    return ("--retry-request", request) if request else ()


def bound_command(drive: str, run: str, argv: list[str]) -> subprocess.CompletedProcess:
    """Run a command on a lane's Run, such as a launch's worker-start, as the drive's own terminal, which coordinates the Run from its run-create, so it runs at once; one Orca fences because that terminal does not coordinate the Run runs again as binder_terminal once binder binds the Run there. A worker-start holds that bind only until Orca takes its request, which request-show then reads pending or done, since Orca checks the binding once, as it admits the start, and its wait for the worker to be ready runs on outside the lock; any other command holds it to its end. Unstarted when it refuses before anything the command starts, a fenced run included, since Orca starts nothing it fenced."""
    argv, request = requested(argv)
    try:
        speak_as_drive(drive)
    except OrcaError as error:
        raise Unstarted(str(error), error.code) from None
    done = subprocess.run(argv, capture_output=True, text=True, check=False)
    if FENCED not in done.stdout:
        return done
    try:
        with binder(drive, run) as terminal:
            command = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=speaking_as(terminal))
            if not (request and accepted(command, request, terminal)):
                return completed(argv, command)
    except OrcaError as error:
        raise Unstarted(str(error), error.code) from None
    return completed(argv, command)


def requested(argv: list[str]) -> tuple[list[str], str | None]:
    """A worker-start's argv with the request id Orca keeps its receipt under, the one it names with --retry-request or a fresh one, and that id; any other command as it is, with none."""
    if "worker-start" not in argv:
        return argv, None
    if "--retry-request" in argv:
        return argv, argv[argv.index("--retry-request") + 1]
    request = str(uuid.uuid4())
    return [*argv, "--retry-request", request], request


def accepted(command: subprocess.Popen, request: str, caller: str) -> bool:
    """Whether Orca took the request while its worker-start still runs, asking request-show as the terminal that made it, which keeps the receipt under that caller, until it reads pending or completed; False once the process ends first, as a fenced start does, or once request-show refuses, so the start holds the binding to its end."""
    while command.poll() is None:
        try:
            if call("request-show", "--request", request, caller=caller)["state"] != "absent":
                return True
        except OrcaError:
            return False
    return False


def completed(argv: list[str], command: subprocess.Popen) -> subprocess.CompletedProcess:
    stdout, stderr = command.communicate()
    return subprocess.CompletedProcess(argv, command.returncode, stdout, stderr)


def answer(drive: str, job: dict, kept: dict) -> tuple[str, bool]:
    """Reply to the lane's open question with the held text, or send it to its Dispatch under the subject state.sent_by gives its sender, His answer for his and From the coordinator for the coordinator's, when it asked nothing or Orca already holds another answer to that question, since the lane no longer waits on it, under the request id the text was held with, so a try Orca applied before its answer was lost is replayed; the send takes an id made from that one, since Orca refuses one id reused for another verb as request_mismatch. What came of it, and whether a Dispatch mailbox queues it until its worker reads it, as each send does and no answer its question took does."""
    if job.get("ask"):
        try:
            reply(drive, job["run"], job["ask"], kept["words"], kept["request"])
        except OrcaError as error:
            if error.code != ANSWERED_ELSEWHERE:
                raise
        else:
            return woke(job, f"answered its question {job['ask']}"), False
    return send(drive, job, state.sent_by(kept["sender"]), kept["words"], str(uuid.uuid5(uuid.UUID(kept["request"]), "send"))), True


def send(drive: str, job: dict, subject: str, text: str, request: str | None = None) -> str:
    """Send the text to the lane's Dispatch as a message, as the drive's own terminal, which neither answers nor clears a question it asked; what came of it: queued in that Dispatch's mailbox, where its live worker reads it at its next check, and carried by the job's next worker when this one ends with it unread, which the watch's read of an ended worker's mail holds it for."""
    speak_as_drive(drive)
    call("send", "--to", f"dispatch:{job['dispatch']}", "--run", job["run"], "--subject", subject, "--body", text, *retried(request))
    queued = woke(job, f"queued in dispatch {job['dispatch']} for its live worker, read at its next check")
    return f"{queued}; its next worker carries it if this one ends with it unread"


def woke(job: dict, how: str) -> str:
    """How a message reached the lane, once a lane on this Mac that Orca cannot tell is live has had MAIL typed into its terminal, since Orca may hold a message for such a worker without delivering it."""
    if not state.on_mac(job) or (shown := worker(job["dispatch"]))["projection"]["liveness"]["verdict"] == "live" or not (terminal := typed(job, shown)):
        return how
    type_prompt(terminal, MAIL)
    return f"{how}, and typed into its terminal {terminal} that it has mail"


def takes_messages(job: dict, worker: dict) -> bool:
    """Whether a worker-show's worker is unfinished and reachable: alive, so a message to its Dispatch reaches it, or on this Mac in a terminal the drive types into."""
    return typed(job, worker) is not None or (worker["worker"]["state"] not in FINISHED and worker["projection"]["liveness"]["verdict"] == "live")
