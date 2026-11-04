"""The drive registry: which long-running drive, and so which ledger, a session's pull requests belong to.

    drive.py start   --ledger ID [--drive ID] [--orca-run ID] [--state-dir DIR] [--cci-drive NAME] [--program SLUG] [--register-lane NAME]
    drive.py desk    --name NAME (--terminal HANDLE [--environment NAME] [--supervisor] | --remove) [--drive ID]
    drive.py end     [--drive ID]
    drive.py context [--drive ID]
    drive.py current
    drive.py orca-run
    drive.py list    [--json]
    drive.py record  --session ID --lane NAME --cwd DIR [--drive ID] [--opened] --pr [OWNER/NAME#]N[=SHA]...
    drive.py landed  --session ID [--drive ID] --pr N...
    drive.py lane-status LANE|DISPATCH [--drive ID] [--run ID]
    drive.py digest  [--drive ID]
    drive.py worktree --lane NAME --path DIR [--session ID] [--drive ID]
    drive.py spawned --lane NAME --brief PATH [--by LANE] [--session ID] [--drive ID]
    drive.py release --cci-drive NAME --owner LANE@DRIVE --build BUILD [--release ID]
    drive.py overview [--drive ID] [--steps PATH] [--json] [--step ID] [--since TIME]    (alias plan-status)

STDLIB ONLY. One file per drive at ``$CLAUDE_CONFIG_DIR/long-running/drives/<drive>.json`` (default ``~/.claude``) names the
drive's ledger, its repository, the git common dir every checkout of that repository shares,
the checkout the drive started in, every coordinator session that has run it, its Orca run, and its state
directory, ``<scratch>/<drive>`` unless ``start --state-dir`` names another, and its ``program``:
the plan slug every worker resolves the register by, set with ``start --program`` and otherwise the
state directory's name when the entry is first written. ``<scratch>`` is ``$LONG_RUNNING_SCRATCH_ROOT``, default
``~/.claude/scratch``.
``start`` is an upsert: the resumed the coordinator of a handoff runs it again and joins ``sessions``.
Run inside an Orca terminal, it records that terminal as ``root_terminal``, the coordinator's.
``desk`` records a standing desk's Orca terminal under ``desks``, with ``--environment`` for one on a
remote host. ``--supervisor`` marks the one desk that hears of the coordinator's own prompts, by
cci records addressed to its name and one line typed at its idle prompt, and prints the
``cci watch`` command a desk that holds a wait runs instead; ``--remove`` forgets a desk. ``lr watch prompts`` reads all three.
Every registry write also writes ``<state dir>/dashboard/context.json``, the facts the drive's
live dashboard binds its cards from; ``context`` rewrites it from the registry alone.

``record`` is the capt-hook pack's entry point after a command opened or pushed pull requests.
A session belongs to a drive when its id is one of the drive's coordinator sessions, which covers every
in-process subagent and teammate, or when it carries ``CLAUDE_LONG_RUNNING_DRIVE``, which Orca workers
inherit from ``orca-launch.sh``. ``current`` also resolves the drive whose Orca run is ``ORCA_LAUNCH_RUN``, so a
desk runner that holds no session still launches workers into the drive. ``orca-run`` prints the Orca run
of the drive this session belongs to, which ``orca-launch.sh`` launches into when ``ORCA_LAUNCH_RUN`` is unset.
A session in no drive, a command run outside the drive's repository, and a pull request on another repository
are not the drive's and record nothing; a session in no drive says so on stderr. ``--opened`` also posts each
PR's ``opened`` record on the drive's cci drive under ``--lane``, for a Codex worker, whose ``ccx vcs ship``
runs no PR hook and binds no cci lane, so neither the ledger row nor the ``opened`` record exists unless it
runs ``record`` itself.

``landed`` is the pack's entry point after a ``cci post --kind landed`` or a ``gh pr close``: it settles each named
PR the drive's ledger tracks against the trunk in the drive's checkout through ``ledger.py landed``, so the row
leaves the open set in the same step that announced the landing or the close.

``lane-status`` answers what code a lane holds. It prints one line per worktree the lane owns, found by
``lanes.lane_worktrees`` from the drive's launch receipts, the lane's cci records of the last day, the
``git worktree list`` of the checkout and of every repository under ``~/.claude/worktrees``, and its PRs'
head branches in any repository, never the landing runner's own checkout from ``runner.json``. Each line
names the repository, then the path, the branch, dirty files,
commits ahead of ``origin/<trunk>``, the last commit's age, whether the branch is pushed, and its open PR
with CI state. A lane with no worktree here, such as an Orca worker on a paired remote server, falls back to
``lanes.orca_status`` over ``--run``, the drive's Orca run, or else the newest Run its coordinator terminal coordinates: one line per matching dispatch with its worktree,
environment, branch, stage, outcome, liveness, last update and heartbeat, terminal, and last message to the Run.
A ``ctx_`` lane names that dispatch directly. A lane that is an in-process teammate of the drive's coordinator sessions
also gets a line from ``lanes.teammate_status``: its agent type, model, whether it runs or sits idle, its last
activity, and when it joined. With none found it prints ``no worktree found for lane <lane>``
and exits 1.

``digest`` prints one line for the relay from its newest ``orca alive`` cci record on the drive: its pid and when it
posted. One whose record is five minutes old reads down; the command exits 1 while the relay is down or has posted no record.

``worktree`` is the pack's entry point the first time a lane runs a command in a worktree: it posts one
``state`` record ``WORKTREE <lane> <path> <branch>`` with a ``worktree`` attr, keyed
``worktree:<lane>:<path>`` so it posts once. A path outside the drive's repository posts nothing and exits 3.

``spawned`` is the pack's entry point at each worker launch, the Agent hook's, ``orca-launch.sh``'s and the desk
runner's for a Sprite: it posts one ``state`` record ``SPAWNED <lane> <brief>`` as ``--by`` (default the coordinator), with the
brief, ``--model``, ``--effort``, ``--placement``, the role (``--role``, else the brief's ``ccx: role=``) and the
worker it succeeds (``--successor-of``, else the brief's ``Replaces:`` line) as attrs. The lanes mover writes the
worker's one registry row from it, and a successor takes over the worker it names.

``release`` is the desk runner's entry point when a lane's ``release --build`` record names a build: it records
the build under ``releases`` in the registry entry of the drive whose cci drive is ``--cci-drive``, with its owner
``<lane>@<drive>``, its release id, and the time, and exits 1 when no registered drive posts to that cci drive.

``overview`` prints the owner's plan report from the progress desk's ``<state dir>/progress-desk/steps.json``: one row
per plan step and per milestone with what it delivers, its state, what blocks it, what is being done to unblock it, and
what went wrong so far, then every step and milestone left until completion with the gate it waits on, all in plain
words (``overview.py``). ``--json`` prints the same for tools, ``--step`` one row by id, label, title, or milestone
number, and ``--since`` only the rows that moved after a time, ``90m``, ``2h``, ``7:05 PM`` or ISO, saying what moved.
"""

from __future__ import annotations

from . import register_reads

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import cci
from . import lanes
from . import ledger
from . import overview
from . import ghread
from . import judge
from . import release
from .release import config_dir, version_key

SESSION_ENV = "CLAUDE_CODE_SESSION_ID"
LANE_ENV = "CLAUDE_LONG_RUNNING_LANE"
SESSION_IDENTITY = ("CCI_AGENT_ID", "CCI_LANE", SESSION_ENV, "CODEX_THREAD_ID")
DRIVE_ENV = ledger.DRIVE_ENV
ORCA_RUN_ENV = "ORCA_LAUNCH_RUN"
TERMINAL_ENV = "ORCA_TERMINAL_HANDLE"
CALLER_VERSION_ENV = "LONG_RUNNING_CALLER_VERSION"
PIN_ENV = "LONG_RUNNING_PIN"
DEADLINE_ENV = "LONG_RUNNING_DEADLINE"
REEXEC_ENV = "LONG_RUNNING_REEXEC"
REAP_SECONDS = 5
DRIVE_ID_LENGTH = 8
DASHBOARD_CONTEXT = Path("dashboard") / "context.json"
PACK = Path(__file__).resolve().parents[4] / "dashboard"
PR_SPEC = re.compile(r"^(?:(?P<repo>[\w.-]+/[\w.-]+)#)?(?P<pr>\d+)(?:=(?P<head>[0-9a-f]{7,40}))?$")
LANE_RECORDS = timedelta(days=1)
OUTSIDE_REPO = 3
CI_WORKFLOW = "long-running CI"
GITHUB_REPO = re.compile(r"^https://github\.com/(?P<repo>[\w.-]+/[\w.-]+?)(?:\.git)?/?$")
PLUGIN = Path(__file__).resolve().parents[4]
DESKS = ("orca",)
DESK_RUNNER = "desk-runner"
WATCHDOG = "drive-watchdog"
BEAT_STALE = timedelta(minutes=5)
JUDGE_WINDOW = timedelta(hours=24)
ADMITTED = "landing-admitted.json"
ROLE = re.compile(r"^ccx:(?:.* )?role=([\w-]+)", re.MULTILINE)
REPLACES = re.compile(r"^Replaces: `?([\w.-]+)`?\s*$", re.MULTILINE)
INSTALL_MARKERS = frozenset({"__pycache__", ".in_use", ".orphaned_at"})


def drives_dir() -> Path:
    return config_dir() / "long-running" / "drives"


def scratch_dir() -> Path:
    return Path(os.environ.get("LONG_RUNNING_SCRATCH_ROOT") or Path.home() / ".claude" / "scratch")


def plugin_manifest() -> dict:
    return json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text())


def installs() -> Path:
    """The directory Claude Code installs each version of this plugin under, one subdirectory per version."""
    return PLUGIN.parent


def install_bin(version: str, tool: str) -> Path:
    return installs() / version / "bin" / tool


def installed_script(name: str) -> Path:
    """The script `name` of the version the install link names, or this copy's own while the link names none, so a read of state every version shares, such as the supervisor's `lr.json`, runs the code that wrote it even from a process an older version started (coordinator #343297: 0.7.952's supervisor refused the `go_lanes` key 0.7.957 wrote, and every stall-watch pass died)."""
    scripts = Path(__file__).resolve().parents[1]
    return named / scripts.relative_to(PLUGIN) / name if (named := release.held()) else scripts / name


def release_faults(version: str) -> list[str]:
    """Each way the install of `version` differs from the release tagged for it, read against that tag fetched from the repository the marketplace entry Claude Code installed from pins, so an install cut short, copied from past its tag, or edited in place reads here before anything runs it; none when this plugin runs from no cache install."""
    if PLUGIN.parents[2].name != "cache":
        return []
    copy = installs() / version
    if not (copy / ".claude-plugin" / "plugin.json").is_file():
        return [f"{copy} holds no install of {version}"]
    marketplace = PLUGIN.parents[3] / "marketplaces" / PLUGIN.parents[1].name / ".claude-plugin" / "marketplace.json"
    source = next(entry["source"] for entry in load(marketplace)["plugins"] if entry["name"] == PLUGIN.parent.name)
    return tree_faults(copy, source["url"], f"{PLUGIN.parent.name}--v{version}", source["path"], config_dir() / "long-running" / "releases.git")


def ci_fault(version: str) -> str:
    """Why `version` may not run a desk yet: the `CI_WORKFLOW` run on main for the commit its release tag was bumped from has not passed, read from GitHub. Merges skip CI (owner, Nov 2), so the install is where a red main stops (coordinator #327196); empty once that run passed, or for an install outside the plugin cache or from a repository off GitHub."""
    if PLUGIN.parents[2].name != "cache":
        return ""
    marketplace = PLUGIN.parents[3] / "marketplaces" / PLUGIN.parents[1].name / ".claude-plugin" / "marketplace.json"
    source = next(entry["source"] for entry in load(marketplace)["plugins"] if entry["name"] == PLUGIN.parent.name)
    if not (found := GITHUB_REPO.match(source["url"])):
        return ""
    repo, tag = found["repo"], f"{PLUGIN.parent.name}--v{version}"

    def api(path: str) -> dict:
        done = subprocess.run(ghread.read("api", path, max_age=ghread.FRESH), capture_output=True, text=True)
        if done.returncode:
            raise RuntimeError(f"gh api {path} exited {done.returncode}: {(done.stderr or done.stdout).strip()[:200]}")
        return json.loads(done.stdout)

    merged = api(f"repos/{repo}/commits/{tag}")["parents"][0]["sha"]
    runs = [run for run in api(f"repos/{repo}/actions/runs?head_sha={merged}&per_page=100")["workflow_runs"] if run["name"] == CI_WORKFLOW]
    if not runs:
        return f"no {CI_WORKFLOW} run on main has started for {merged[:10]}, the commit {tag} was bumped from"
    run = max(runs, key=lambda run: run["run_attempt"])
    if run["status"] != "completed":
        return f"the {CI_WORKFLOW} run on {merged[:10]}, the commit {tag} was bumped from, is {run['status']}: {run['html_url']}"
    if run["conclusion"] != "success":
        return f"the {CI_WORKFLOW} run on {merged[:10]}, the commit {tag} was bumped from, ended {run['conclusion']}: {run['html_url']}"
    return ""


def tree_faults(copy: Path, url: str, tag: str, source: str, releases: Path) -> list[str]:
    """Each way `copy` differs from `source` in `tag` of the repository at `url`: its file count when that differs, then each release file it lacks, each file it holds that the release does not, and each file it holds with other bytes. The tag is fetched into the bare repository `releases`, created on first use. Python's bytecode caches and the markers Claude Code keeps beside an install are not part of it."""
    if not releases.is_dir():
        subprocess.run(["git", "init", "-q", "--bare", str(releases)], check=True)
    fetched = subprocess.run(["git", "-C", str(releases), "fetch", "-q", "--depth", "1", url, f"+refs/tags/{tag}:refs/tags/{tag}"], capture_output=True, text=True)
    if fetched.returncode:
        return [f"the release tag {tag} did not fetch from {url}: {(fetched.stderr.strip().splitlines() or ['no output'])[-1]}"]
    listed = subprocess.run(["git", "-C", str(releases), "ls-tree", "-r", "-z", tag, "--", f"{source}/"], capture_output=True, text=True, check=True).stdout
    released = {}
    for row in filter(None, listed.split("\0")):
        meta, path = row.split("\t", 1)
        mode, _, sha = meta.split()
        released[path.removeprefix(f"{source}/")] = (mode, sha)
    held = {str(path.relative_to(copy)) for path in copy.rglob("*") if (path.is_symlink() or path.is_file()) and not INSTALL_MARKERS.intersection(path.relative_to(copy).parts)}
    missing = [relative for relative, (mode, _) in released.items() if not ((copy / relative).is_symlink() if mode == "120000" else (copy / relative).is_file())]
    blobs = [(relative, sha) for relative, (mode, sha) in released.items() if mode != "120000" and relative not in missing]
    hashed = subprocess.run(["git", "-C", str(releases), "hash-object", "--stdin-paths"], input="\n".join(str(copy / relative) for relative, _ in blobs), capture_output=True, text=True, check=True).stdout.split()
    return [
        *([f"the install holds {len(held)} files where the release holds {len(released)}"] if len(held) != len(released) else []),
        *(f"{relative} is missing" for relative in missing),
        *(f"{relative} is not in the release" for relative in sorted(held - released.keys())),
        *(f"{relative} differs from the release" for (relative, sha), got in zip(blobs, hashed, strict=True) if got != sha),
    ]


def behind(running: str, installed: str) -> bool:
    """Whether a runner reporting ``running``, empty for one that reports none, runs older code than the installed version."""
    return bool(installed) and (not running or version_key(running) < version_key(installed))


def run_installed(script: str, *, before_exec: Callable[[], None] = lambda: None) -> None:
    """Re-execute `script` from the installed plugin version, as the `bin/` shim does, when it is a copy of an older version than the install link names, no process of its own version started it, and `LONG_RUNNING_PIN` does not pin its version, so a path that names an old version still runs current code and never writes a line the current runner cannot read; a process that passes marks its children with its version, so a runner started before an update keeps its own scripts until it re-executes, and a runner on its pinned version keeps them for its life. Every GitHub read the script makes through ccx is charged to the script's name, every command it starts ends with it (`end_commands_with_script`), and an image that replaced an earlier one passively reaps inherited exits (`adopt_children`). Callers close explicitly owned resources through `before_exec` only when an update will actually replace the image."""
    os.environ["CCX_CALLER"] = Path(script).stem
    release.promote(PLUGIN)
    running = plugin_manifest()["version"]
    named = release.held()
    if running not in (os.environ.get(CALLER_VERSION_ENV), os.environ.get(PIN_ENV)) and named and behind(running, named.name):
        fresh = named / Path(__file__).resolve().parents[1].relative_to(PLUGIN) / Path(script).resolve().name
        before_exec()
        reexec([sys.executable, str(fresh), *sys.argv[1:]])
    os.environ[CALLER_VERSION_ENV] = running
    end_commands_with_script()
    adopt_children()


def own_lane(lane: str) -> dict[str, str]:
    return dict.fromkeys(SESSION_IDENTITY, "") | {LANE_ENV: lane, "LR_LANE": lane}


def reexec(argv: list[str]) -> None:
    """Replace this process with `argv` under the same pid, marking inherited children for passive reaping by the new image."""
    os.environ[REEXEC_ENV] = str(os.getpid())
    os.execv(argv[0], argv)


def adopt_children() -> None:
    """Reap inherited children when they exit naturally, without assuming authority to terminate an earlier image's processes."""
    if os.environ.pop(REEXEC_ENV, "") != str(os.getpid()):
        return
    rows = processes()
    for child in (child for child, parent, _ in rows if parent == os.getpid()):
        threading.Thread(target=reap, args=(child,), name=f"reap-{child}", daemon=True).start()


def reap(pid: int) -> None:
    """Wait for child `pid` to exit; the `ps` that listed it is a child too, one `subprocess.run` has already reaped."""
    with suppress(ChildProcessError):
        os.waitpid(pid, 0)


def deadline_env(seconds: float) -> dict[str, str]:
    """The environment entry telling a script this process starts that it must be gone, with every command it started, in `seconds`; only the script whose parent is this process reads it, so no other descendant inherits a deadline that was never its own."""
    return {DEADLINE_ENV: f"{os.getpid()}:{time.time() + seconds:.3f}"}


def processes() -> list[tuple[int, int, int]]:
    """Each process's pid, parent pid and process group, from one `ps` snapshot."""
    return [tuple(map(int, line.split())) for line in subprocess.run(["ps", "-A", "-o", "pid=,ppid=,pgid="], capture_output=True, text=True, check=True).stdout.splitlines()]


def under(pid: int, rows: list[tuple[int, int, int]] | None = None) -> list[int]:
    """Every process under `pid` still in this process's group, at any depth, from one `ps` snapshot, `rows` when given; one that left the group, as a daemon started in a session of its own does, is not this process's to end."""
    rows = processes() if rows is None else rows
    group, found, parents = os.getpgrp(), [], {pid}
    while parents:
        parents = {child for child, parent, pgid in rows if parent in parents and pgid == group}
        found += parents
    return found


def kill(pids: list[int]) -> None:
    for pid in pids:
        with suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)


def die_with_commands(signum: int, _frame: object) -> None:
    """SIGKILL every command in flight, then die of `signum` as if no handler had run: a `ccn` hung on cc-notes swallows SIGTERM, so one left behind runs on under PID 1 (101 orphans in an hour on 2026-10-30)."""
    signal.signal(signum, signal.SIG_IGN)
    if signum == signal.SIGALRM:
        os.write(sys.stderr.fileno(), f"{Path(sys.argv[0]).name} reached the deadline its caller set and was killed with every command it started\n".encode())
    kill(under(os.getpid()))
    signal.signal(signum, signal.SIG_DFL)
    os.kill(os.getpid(), signum)


def end_commands_with_script() -> None:
    """Make SIGTERM and SIGHUP, unless the script was started ignoring them, and the deadline `deadline_env` hands it, kill the script's commands before the script dies, so a caller that kills the script never leaves its commands running."""
    for signum in (signal.SIGTERM, signal.SIGHUP):
        if signal.getsignal(signum) is signal.SIG_DFL:
            signal.signal(signum, die_with_commands)
    caller, _, deadline = os.environ.pop(DEADLINE_ENV, "").partition(":")
    if caller == str(os.getppid()):
        signal.signal(signal.SIGALRM, die_with_commands)
        signal.setitimer(signal.ITIMER_REAL, max(float(deadline) - time.time(), 0.001))


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


def common_dir(cwd: Path) -> str:
    return git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def drives(root: Path | None = None) -> list[dict]:
    return [load(path) for path in sorted((drives_dir() if root is None else root).glob("*.json"))]


def find(drive: str | None, session: str | None, root: Path | None = None) -> dict | None:
    if drive:
        if "/" in drive or "\x00" in drive:
            return None
        path = (drives_dir() if root is None else root) / f"{drive}.json"
        if not path.is_file():
            return None
        entry = load(path)
        return entry if entry["drive"] == drive else None
    for entry in drives(root):
        if session in entry["sessions"]:
            return entry
    return None


def write_atomic(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{os.getpid()}")
    staged.write_text(json.dumps(payload, indent=2) + "\n")
    staged.replace(path)
    return path


def program_of(entry: dict) -> str:
    return register_reads.program_of(entry)


def cci_drive(entry: dict) -> str:
    return entry.get("cci_drive") or program_of(entry)


def root_session(drive_name: str) -> str | None:
    """The newest session of the registry entry whose cci drive is `drive_name`: the drive coordinator, whose board holds the rows the owner taps."""
    return next((entry["sessions"][-1] for entry in drives() if cci_drive(entry) == drive_name and entry.get("sessions")), None)


def heartbeat(run: Callable[[list[str]], str], drive_name: str, desk: str) -> dict | None:
    """The fields of the newest `<desk> alive` record the desk's runner posted on `drive_name`, its pid, version, config, terminal and restart command, with the record's `at`; None when it posted none. The runner may run on any machine, so the record's age, never its pid, says whether it runs."""
    found = cci.records(run, drive_name, "--lane", DESK_RUNNER, "--kind=state", f"--topic={desk}", "--to", f"{WATCHDOG}@{drive_name}", "--limit", "1")
    return {**found[-1]["fields"].get("attrs", {}), "at": found[-1]["at"]} if found else None


def beating(beat: dict | None, now: datetime) -> bool:
    return beat is not None and now - datetime.fromisoformat(beat["at"]) < BEAT_STALE


def context_of(entry: dict) -> dict:
    program = program_of(entry)
    return {
        "id": entry["drive"],
        "title": program,
        "program": program,
        "repo": entry["repo"],
        "checkout": entry["checkout"],
        "ledger": entry["ledger"],
        "sessions": entry["sessions"],
        "cci_drive": cci_drive(entry),
        "orca_run": entry["orca_run"],
        "state_dir": entry["state_dir"],
        "started_at": entry["started_at"],
        "packs": {"lr": str(PACK)},
    }


def write_context(entry: dict) -> Path:
    path = Path(entry["state_dir"]) / DASHBOARD_CONTEXT
    task_facts = json.loads(path.read_text()) if path.exists() else {}
    return write_atomic(path, task_facts | context_of(entry))


def save(entry: dict) -> Path:
    write_context(entry)
    return write_atomic(drives_dir() / f"{entry['drive']}.json", entry)


def session_id() -> str:
    if not (session := os.environ.get(SESSION_ENV)):
        raise SystemExit(f"{SESSION_ENV} is unset and no drive's lane registry has a row for {LANE_ENV}; run this from the drive's coordinator session or a lane its supervisor registered")
    return session


def current_drive(env: Mapping[str, str] | None = None, root: Path | None = None, run: lanes.Run | None = None) -> str | None:
    env = os.environ if env is None else env
    if drive := env.get(DRIVE_ENV):
        return drive
    entry = find(None, env.get(SESSION_ENV), root) or registered_drive(env.get(LANE_ENV), root, run or ledger.Shell().run)
    return entry["drive"] if entry else None


def registered_drive(lane: str | None, root: Path | None, run: lanes.Run) -> dict | None:
    if not lane:
        return None
    return next((entry for entry in drives(root) if (config := Path(entry["state_dir"]) / "lr.json").is_file() and lanes.output(run, [sys.executable, str(installed_script("drive_supervisor.py")), "lanes", lane, "--config", str(config), "--json"])), None)


def orca_run_drive() -> str | None:
    run = os.environ.get(ORCA_RUN_ENV)
    return next((entry["drive"] for entry in drives() if run and entry["orca_run"] == run), None)


def cmd_start(args: argparse.Namespace, shell: ledger.Shell) -> int:
    session = session_id()
    drive = args.drive or current_drive() or session[:DRIVE_ID_LENGTH]
    if (joined := find(None, session)) and joined["drive"] != drive:
        raise SystemExit(f"this session already runs drive {joined['drive']}; end it with drive.py end before starting {drive}")
    cwd = Path.cwd()
    entry = find(drive, None) or {"drive": drive, "sessions": [], "orca_run": None, "started_at": stamp()}
    state = str(args.state_dir or entry.get("state_dir") or scratch_dir() / drive)
    entry |= {
        "ledger": args.ledger,
        "repo": ledger.origin_repo(ledger.Shell(), cwd),
        "git_common_dir": common_dir(cwd),
        "checkout": str(cwd),
        "sessions": [*entry["sessions"], session] if session not in entry["sessions"] else entry["sessions"],
        "orca_run": args.orca_run or entry["orca_run"],
        "state_dir": state,
        "program": args.program or entry.get("program") or Path(state).name,
        "cci_drive": args.cci_drive or entry.get("cci_drive"),
        "register_lane": args.register_lane or entry.get("register_lane"),
        "root_terminal": os.environ.get(TERMINAL_ENV) or entry.get("root_terminal"),
        "updated_at": stamp(),
    }
    print(f"drive {drive} on ledger {args.ledger} at {save(entry)}")
    return 0


def cmd_end(args: argparse.Namespace, shell: ledger.Shell) -> int:
    if not (drive := args.drive or current_drive()):
        raise SystemExit("this session runs no drive; pass --drive")
    path = drives_dir() / f"{drive}.json"
    if not path.exists():
        raise SystemExit(f"no drive {drive} in {drives_dir()}")
    path.unlink()
    print(f"ended drive {drive}")
    return 0


def cmd_desk(args: argparse.Namespace, shell: ledger.Shell) -> int:
    if not (drive := args.drive or current_drive()):
        raise SystemExit("this session runs no drive; pass --drive")
    if not (entry := find(drive, None)):
        raise SystemExit(f"no drive {drive} in {drives_dir()}")
    desks = dict(entry.get("desks") or {})
    if args.remove:
        if args.name not in desks:
            raise SystemExit(f"drive {drive} has no desk {args.name}")
        del desks[args.name]
    else:
        if args.supervisor:
            desks = {name: desk | {"supervisor": False} for name, desk in desks.items()}
        desks[args.name] = {"terminal": args.terminal, "environment": args.environment, "supervisor": args.supervisor}
    save(entry | {"desks": desks, "updated_at": stamp()})
    listed = [f"{name}={desk['terminal']}{' (supervisor)' if desk['supervisor'] else ''}" for name, desk in sorted(desks.items())]
    print(f"drive {drive} desks: {', '.join(listed) or 'none'}")
    if not args.remove and args.supervisor:
        print(f"{args.name} gets one typed line at its idle prompt; while it holds a wait it reads busy and hears only through: cci watch --drive {cci_drive(entry)} --to {args.name} --for 0")
    return 0


def cmd_context(args: argparse.Namespace, shell: ledger.Shell) -> int:
    if not (drive := args.drive or current_drive()):
        raise SystemExit("this session runs no drive; pass --drive")
    if not (entry := find(drive, None)):
        raise SystemExit(f"no drive {drive} in {drives_dir()}")
    print(write_context(entry))
    return 0


def cmd_current(args: argparse.Namespace, shell: ledger.Shell) -> int:
    if not (drive := current_drive() or orca_run_drive()):
        return 1
    print(drive)
    return 0


def cmd_orca_run(args: argparse.Namespace, shell: ledger.Shell) -> int:
    if not (drive := current_drive()) or not (entry := find(drive, None)) or not entry["orca_run"]:
        return 1
    print(entry["orca_run"])
    return 0


def cmd_list(args: argparse.Namespace, shell: ledger.Shell) -> int:
    entries = drives()
    if args.json:
        print(json.dumps(entries))
        return 0
    for entry in entries:
        print(f"{entry['drive']} ledger={entry['ledger']} repo={entry['repo']} checkout={entry['checkout']} sessions={len(entry['sessions'])}")
    return 0


def in_repo(cwd: Path, entry: dict) -> bool:
    try:
        return common_dir(cwd) == entry["git_common_dir"]
    except subprocess.CalledProcessError:
        return False


def cmd_record(args: argparse.Namespace, shell: ledger.Shell) -> int:
    if not (entry := find(args.drive, args.session)):
        print(
            f"no drive claims session {args.session} (lane {args.lane}, {DRIVE_ENV}={args.drive or 'unset'}); "
            f"PR {', '.join('#' + match['pr'] for match in args.pr)} not recorded",
            file=sys.stderr,
        )
        return 0
    if not in_repo(args.cwd, entry):
        return 0
    specs = [match for match in args.pr if (match["repo"] or entry["repo"]).lower() == entry["repo"].lower()]
    os.chdir(args.cwd)
    for spec in specs:
        argv = ["register", "--ledger", entry["ledger"], "--lane", args.lane, "--pr", spec["pr"]]
        argv += ["--head", spec["head"]] if spec["head"] else []
        try:
            ledger.main(argv, shell)
        except (subprocess.CalledProcessError, SystemExit, OSError) as failure:
            reason = (getattr(failure, "stderr", "") or str(failure)).strip().splitlines()[-1:] or ["no reason given"]
            print(
                f"PR #{spec['pr']} was not recorded in ledger {entry['ledger']}: {reason[0]} — run "
                f"`ledger.py {' '.join(argv)}` from {args.cwd}",
                file=sys.stderr,
            )
            return 1
        if args.opened:
            shell.run(["cci", "post", "--drive", cci_drive(entry), "--lane", args.lane, "--kind", "opened", "--pr", spec["pr"], "--text", f"opened #{spec['pr']}"])
    return 0


def cmd_release(args: argparse.Namespace, shell: ledger.Shell) -> int:
    if not (entry := next((entry for entry in drives() if cci_drive(entry) == args.cci_drive), None)):
        print(f"no registered drive posts to cci drive {args.cci_drive}; build {args.build} not recorded", file=sys.stderr)
        return 1
    entry.setdefault("releases", {})[args.build] = {"owner": args.owner, "release": args.release, "at": stamp()}
    save(entry)
    print(f"build {args.build} owned by {args.owner}")
    return 0


def cmd_landed(args: argparse.Namespace, shell: ledger.Shell) -> int:
    if not (entry := find(args.drive, args.session)):
        return 0
    argv = ["-C", entry["checkout"], "landed", "--repo", entry["repo"], "--ledger", entry["ledger"], "--checkout", entry["checkout"]]
    argv += [flag for pr in args.pr for flag in ("--pr", pr)]
    try:
        return ledger.main(argv, shell)
    except (subprocess.CalledProcessError, OSError) as failure:
        reason = (getattr(failure, "stderr", "") or str(failure)).strip().splitlines()[-1:] or ["no reason given"]
        print(f"PR {', '.join('#' + pr for pr in args.pr)} not settled in ledger {entry['ledger']}: {reason[0]} — run `ledger.py {' '.join(argv)}`", file=sys.stderr)
        return 1


def resolve(args: argparse.Namespace, shell: ledger.Shell) -> dict | None:
    drive = args.drive or current_drive(run=shell.run) or orca_run_drive()
    entry = find(drive, None if drive else getattr(args, "session", None) or session_id())
    if entry is None:
        print(f"no drive {drive} is registered" if drive else "this session belongs to no drive; name it with --drive", file=sys.stderr)
    return entry


def launch_receipts(run: str) -> Path:
    """The directory orca-launch.sh writes a Run's receipts in by default."""
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "long-running" / "orca-launch" / run


def receipt_dirs(entry: dict) -> list[Path]:
    dirs = [launch_receipts(entry["orca_run"])] if entry.get("orca_run") else []
    return dirs + sorted(Path(entry["state_dir"]).glob("receipts*")) if entry.get("state_dir") else dirs


def trunk_of(shell: ledger.Shell, checkout: str) -> str:
    ref = lanes.output(shell.run, ["git", "-C", checkout, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"]) or "origin/main"
    return ref.strip().removeprefix("origin/")


def runner_checkouts(entry: dict) -> frozenset[str]:
    """The landing runner's own checkout, from the `runner.json` beside the drive's state, which holds no lane's work."""
    config = Path(entry["state_dir"]) / "runner.json"
    checkout = json.loads(config.read_text()).get("landing", {}).get("checkout") if config.is_file() else None
    return frozenset({checkout} if checkout else ())


def cmd_lane_status(args: argparse.Namespace, shell: ledger.Shell) -> int:
    entry = find(args.drive or current_drive(run=shell.run), os.environ.get(SESSION_ENV)) if args.run else resolve(args, shell)
    if entry is None and not args.run:
        return 1
    now = datetime.now(timezone.utc)
    worktrees: list[lanes.Worktree] = []
    if entry and not args.lane.startswith(lanes.DISPATCH):
        since = (now - LANE_RECORDS).strftime("%Y-%m-%dT%H:%M:%SZ")
        drive = cci_drive(entry)
        records = [record for record in cci.records(shell.run, drive, "--lane", args.lane, since=since) if record["drive"] == drive and record["lane"] == args.lane]
        worktrees = lanes.lane_status(args.lane, records, receipt_dirs(entry), Path(entry["checkout"]), entry["repo"], trunk_of(shell, entry["checkout"]), now, shell.run, runner_checkouts(entry), held=args.held)
    run = args.run or (entry or {}).get("orca_run") or (lanes.coordinated_run(shell.run, entry["root_terminal"]) if entry and entry.get("root_terminal") else None)
    teammates = lanes.teammate_status(args.lane, (entry or {}).get("sessions") or [], config_dir(), now)
    found = [*(worktrees or lanes.orca_status(args.lane, run, now, shell.run)), *teammates]
    if not found:
        print(f"no worktree found for lane {args.lane}" + (f", no Orca dispatch in run {run}" if run else "") + ", and no in-process teammate of the drive's coordinator sessions")
        return 1
    print("\n".join(worktree.line() for worktree in found))
    return 0


def cmd_digest(args: argparse.Namespace, shell: ledger.Shell) -> int:
    if (entry := resolve(args, shell)) is None:
        return 1
    current = True
    beats = {desk: heartbeat(shell.run, cci_drive(entry), desk) for desk in DESKS}
    for desk, beat in beats.items():
        if beat is None:
            current = False
            print(f"{desk}: no {desk} alive record on cci drive {cci_drive(entry)}, so no {desk} runner has run there")
            continue
        down = not beating(beat, datetime.now(timezone.utc))
        current &= not down
        state = "down; the supervisor restarts it within a minute, so read lr-relay.log and the supervisor's log" if down else "current"
        pid = f"pid {beat['pid']} " if "pid" in beat else ""
        print(f"{desk}: {pid}last alive {beat['at']}: {state}")
    for line in judge.digest(judge.calls(Path(entry["state_dir"]), datetime.now(timezone.utc) - JUDGE_WINDOW)):
        print(line)
    return 0 if current else 1


def cmd_worktree(args: argparse.Namespace, shell: ledger.Shell) -> int:
    if (entry := resolve(args, shell)) is None:
        return 1
    top = lanes.output(shell.run, ["git", "-C", str(args.path), "rev-parse", "--show-toplevel"])
    if top is None or not in_repo(Path(top.strip()), entry):
        return OUTSIDE_REPO
    top = top.strip()
    branch = (lanes.output(shell.run, ["git", "-C", top, "branch", "--show-current"]) or "").strip() or "detached"
    shell.run(
        ["cci", "post", "--drive", cci_drive(entry), "--lane", args.lane, "--kind", "state", "--id", f"worktree:{args.lane}:{top}", "--attr", f"worktree={top}", "--attr", f"branch={branch}", "--text", f"WORKTREE {args.lane} {top} {branch}"]
    )
    return 0


def cmd_spawned(args: argparse.Namespace, shell: ledger.Shell) -> int:
    if (entry := find(args.drive or current_drive(), args.session)) is None:
        print("this launch belongs to no drive", file=sys.stderr)
        return 1
    brief = Path(args.brief)
    text = brief.read_text() if brief.is_file() else ""
    role, replaces = ROLE.search(text), REPLACES.search(text)
    attrs = {"brief": args.brief, "model": args.model, "effort": args.effort, "role": args.role or (role[1] if role else None), "placement": args.placement, "successor-of": args.successor_of or (replaces[1] if replaces else None)}
    shell.run(["cci", "post", "--drive", cci_drive(entry), "--lane", args.by, "--kind", "state", "--topic", f"spawn:{args.lane}", "--lane-ref", args.lane, *[arg for key, value in attrs.items() if value for arg in ("--attr", f"{key}={value}")], "--text", f"SPAWNED {args.lane} {args.brief}"])
    return 0


def cmd_overview(args: argparse.Namespace, shell: ledger.Shell) -> int:
    if args.steps:
        path = args.steps
    elif (entry := resolve(args, shell)) is None:
        return 1
    else:
        path = Path(entry["state_dir"]) / overview.STEPS
    if not path.is_file():
        print(f"no progress record at {path}; the progress desk writes it, or pass --steps", file=sys.stderr)
        return 1
    text, code = overview.render(path, as_json=args.json, step=args.step, after=args.since)
    print(text, file=sys.stderr if code else sys.stdout)
    return code


def pr_spec(value: str) -> re.Match:
    if not (match := PR_SPEC.match(value)):
        raise argparse.ArgumentTypeError(f"{value!r} is not [OWNER/NAME#]N[=SHA]")
    return match


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="drive.py", description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="register this session's drive, or join it after a handoff")
    start.add_argument("--ledger", required=True)
    start.add_argument("--drive", help="the drive id; defaults to the drive this session already runs, else its id's first 8 characters")
    start.add_argument("--orca-run", metavar="ID")
    start.add_argument("--state-dir", type=Path, metavar="DIR", help="the drive's scratch directory; defaults to $LONG_RUNNING_SCRATCH_ROOT/<drive>, or ~/.claude/scratch/<drive> when unset")
    start.add_argument("--cci-drive", metavar="NAME", help="the cci drive the lanes post to; defaults to the state dir's name")
    start.add_argument("--program", metavar="SLUG", help="the plan slug workers resolve the register by; defaults to the state dir's name on a new entry")
    start.add_argument("--register-lane", metavar="NAME", help="the lane that classifies owner captures; their blockers go to it instead of the coordinator")
    start.set_defaults(handler=cmd_start)

    desk = subparsers.add_parser("desk", help="record a standing desk's Orca terminal for the prompt watch, or forget one")
    desk.add_argument("--name", required=True, help="the desk's lane name; a supervisor's cci records are addressed to it")
    desk.add_argument("--drive")
    target = desk.add_mutually_exclusive_group(required=True)
    target.add_argument("--terminal", metavar="HANDLE")
    target.add_argument("--remove", action="store_true")
    desk.add_argument("--environment", metavar="NAME", help="the Orca environment of a desk on a remote host")
    desk.add_argument("--supervisor", action="store_true", help="the one desk told of the coordinator's own prompts")
    desk.set_defaults(handler=cmd_desk)

    end = subparsers.add_parser("end", help="the drive is over; its PRs stop being recorded")
    end.add_argument("--drive")
    end.set_defaults(handler=cmd_end)

    context = subparsers.add_parser("context", help="rewrite the drive dashboard's context.json from the registry")
    context.add_argument("--drive")
    context.set_defaults(handler=cmd_context)

    current = subparsers.add_parser("current", help="print the drive this session belongs to; exit 1 when none")
    current.set_defaults(handler=cmd_current)

    orca_run = subparsers.add_parser("orca-run", help="print the Orca run of the drive this session belongs to; exit 1 when none")
    orca_run.set_defaults(handler=cmd_orca_run)

    list_cmd = subparsers.add_parser("list", help="every registered drive")
    list_cmd.add_argument("--json", action="store_true")
    list_cmd.set_defaults(handler=cmd_list)

    record = subparsers.add_parser("record", help="register PRs a command opened or pushed in the drive's ledger")
    record.add_argument("--session", required=True)
    record.add_argument("--drive", help="the drive named by the session's CLAUDE_LONG_RUNNING_DRIVE")
    record.add_argument("--lane", required=True)
    record.add_argument("--cwd", required=True, type=Path)
    record.add_argument("--opened", action="store_true", help="also post each PR's opened record on the drive's cci drive under --lane")
    record.add_argument("--pr", required=True, action="append", type=pr_spec, metavar="[OWNER/NAME#]N[=SHA]")
    record.set_defaults(handler=cmd_record)

    landed = subparsers.add_parser("landed", help="settle PRs a command announced as landed or closed in the drive's ledger")
    landed.add_argument("--session", required=True)
    landed.add_argument("--drive", help="the drive named by the session's CLAUDE_LONG_RUNNING_DRIVE")
    landed.add_argument("--pr", required=True, action="append", type=ledger.pr_number)
    landed.set_defaults(handler=cmd_landed)

    lane_status = subparsers.add_parser("lane-status", help="every worktree a lane owns with its branch, dirty files, push state, and open PR, else its Orca dispatches; exit 1 when none")
    lane_status.add_argument("lane", help="a lane name, or an Orca dispatch id ctx_...")
    lane_status.add_argument("--drive")
    lane_status.add_argument("--held", action="store_true", help="only the worktrees the lane made, never the tree of a PR it merely named, for a successor's live-process check")
    lane_status.add_argument("--run", help="the Orca run to read a lane with no local worktree from; default the drive's")
    lane_status.set_defaults(handler=cmd_lane_status)

    digest = subparsers.add_parser("digest", help="each desk runner's running version beside the installed one, and each judge kind's verdicts over the last day; exit 1 when a runner is behind or has no heartbeat")
    digest.add_argument("--drive")
    digest.set_defaults(handler=cmd_digest)


    worktree = subparsers.add_parser("worktree", help="post the WORKTREE record that maps a lane to a worktree it works in")
    worktree.add_argument("--lane", required=True)
    worktree.add_argument("--path", required=True, type=Path)
    worktree.add_argument("--session")
    worktree.add_argument("--drive")
    worktree.set_defaults(handler=cmd_worktree)

    spawned = subparsers.add_parser("spawned", help="post the SPAWNED record that adds a lane to the drive's registry of lanes kept alive")
    spawned.add_argument("--lane", required=True)
    spawned.add_argument("--brief", required=True)
    spawned.add_argument("--by", default="root", help="the lane that spawned it")
    spawned.add_argument("--model", help="the model the lane runs, which its successor inherits")
    spawned.add_argument("--effort", help="the effort the lane runs at, which its successor inherits")
    spawned.add_argument("--placement", choices=("orca", "sprite", "teammate"), help="where the worker runs, which the launching path knows")
    spawned.add_argument("--role", help="the launch's role, such as fix or evidence for an incident, over the brief's ccx: role=")
    spawned.add_argument("--successor-of", help="the worker this one takes over, else the brief's Replaces: line")
    spawned.add_argument("--session")
    spawned.add_argument("--drive")
    spawned.set_defaults(handler=cmd_spawned)

    release = subparsers.add_parser("release", help="record which lane owns a release build the drive started")
    release.add_argument("--cci-drive", required=True, metavar="NAME", help="the cci drive the owning lane posts to")
    release.add_argument("--owner", required=True, metavar="LANE@DRIVE")
    release.add_argument("--build", required=True, help="the build id or URL the lane's release record names")
    release.add_argument("--release", default="", metavar="ID", help="the release id, such as releases/<date>/<n>")
    release.set_defaults(handler=cmd_release)

    plan = subparsers.add_parser("overview", aliases=["plan-status"], help="the plan's steps and milestones in plain words, and what is left until completion")
    plan.add_argument("--drive")
    plan.add_argument("--steps", type=Path, metavar="PATH", help="the progress record to read; default <state dir>/progress-desk/steps.json")
    plan.add_argument("--json", action="store_true")
    plan.add_argument("--step", metavar="ID", help="one row: a step id, label or title, or a milestone number")
    plan.add_argument("--since", metavar="TIME", help="only rows that moved after 90m, 2h, 1d, a Pacific clock such as 7:05 PM, or an ISO time")
    plan.set_defaults(handler=cmd_overview)


    return parser


def main(argv: list[str] | None = None, shell: ledger.Shell | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args, shell or ledger.Shell())
