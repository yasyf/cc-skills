#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import functools
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable, Container, Iterable, Iterator
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

ORCA = ("lr", "orca") if shutil.which("lr") else ("orca",)
SETTLED = {"completed", "failed"}
EXTERNAL = "external_terminal"
RELEASED = {"released", "already_released"}
CLOSED_BY_STOP = "closed_agent_terminal"
FENCED = "dispatch_inactive"
RUNTIME_DOWN = "remote_runtime_unavailable"
HOST_UNAVAILABLE = "host_unavailable"
HOST_DOWN = "its host is unavailable"
ABANDONED_DOWN = f"{HOST_DOWN}, so its worker could keep running if the host comes back"
UNREGISTERED = "selector_not_found"
STALE = "terminal_handle_stale"
CONNECTED = "connected"
DISCONNECTED = "disconnected"
STALE_HANDLE = "stale handle"
IDLE_PROBE_MS = 1500
QUIET_MS = 10 * 60 * 1000
NOT_IDLE = "not idle: busy, prompt typed or asking, or output in the last 10 minutes"
SCREEN_LINES = 40
PROMPT_WINDOW = 8
BORDER = re.compile(r"^[─━\s]*$")
BUSY = ("esc to interrupt", "ctrl+c to interrupt", "Running…", "background terminal running")
CLAUDE_PROMPT = "❯"
CODEX_PROMPT = "› Ask Codex to do anything"
DONE_MARKER = re.compile(r"^✻ .* · done ")
PARALLEL = 4
HOLDER_SCRIPTS = ("*.sh", "*.zsh", "*.py")
UNSCANNED_CWDS = (Path.home() / "Library", Path("/private/var/folders"))
HOOK_DAEMON = "capt-hookd serve"
HOOK_WORKER = "-m captain_hook.worker"
PANE_KEY = re.compile(r"\bORCA_PANE_KEY=(\S+)")
TERMINAL_KEY = re.compile(r"\bORCA_TERMINAL_HANDLE=(\S+)")
SQUASH = re.compile(r" \(#(?P<number>\d+)\)$")
GITHUB_ORIGIN = re.compile(r"github\.com[:/](?P<slug>[^/\s]+/[^/\s]+?)(?:\.git)?/?$")
UNKNOWN_TO_GITHUB = re.compile(r"HTTP 4(04|22)")
UNREADABLE = "unreadable"
REPO_VALUES = json.loads(Path(".orca.json").read_text()) if Path(".orca.json").is_file() else {}
CC_REMOTE = os.environ.get("WORKER_CC_REMOTE") or REPO_VALUES.get("ccRemote") or "cc-remote"
if "ccRemoteConfig" in REPO_VALUES:
    os.environ.setdefault("CC_REMOTE_CONFIG", str(Path(REPO_VALUES["ccRemoteConfig"]).resolve()))
STATE_HOME = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
CC_REMOTE_STATE = STATE_HOME / "cc-remote"
RESUMED = STATE_HOME / "orca-gc" / "resumed"
FILES_PAUSED = STATE_HOME / "orca-gc" / "files-paused"
REMOVING = STATE_HOME / "orca-gc" / "remove.lock"
SWEPT = STATE_HOME / "orca-gc"
BACKOFF = STATE_HOME / "orca-gc" / "backoff.json"
SWEPT_BATCH = 40
REMOVALS_PER_PASS = 3
CLOSES_PER_PASS = 12
SHELL_QUIET_MS = 3 * 24 * 60 * 60 * 1000
SLOW_READ_S = 15.0
PASS_ORCA_S = 180.0
CALL_TIMEOUT_S = 60
SSH_CONNECT_S = 30
BACKOFF_FIRST_S = 15 * 60
BACKOFF_MOST_S = 4 * 60 * 60
MUTATIONS = {("terminal", "close"), ("worktree", "rm"), *(("orchestration", verb) for verb in ("worker-stop", "worker-abandon", "worker-release"))}
SKIP_RUNS = "orca.reclaimSkipRun"
UNBOUND_RUN = "run_unbound"
DEFERRED_REMOVAL = "this pass spent its removals or another reclaim holds the removal lock; a later pass takes it"
DEFERRED_CLOSE = "this pass spent its closes; a later pass takes it"
REMOVING_HERE = threading.Lock()
CC_REMOTE_TASK_SCHEMA = 1
FULL_HOME_KIB = 65536
FREE_FULL_HOME = f'[ "$(df -Pk "$HOME" | awk \'NR == 2 {{ print $4 }}\')" -ge {FULL_HOME_KIB} ] || rm -rf "$HOME/.cache"'


@dataclass(frozen=True)
class Worker:
    dispatch: str
    status: str
    terminal_state: str
    ownership: str | None
    retained_reason: str | None
    terminal: str | None
    workspace: str | None
    worktree: str | None
    environment: str | None = None
    host_gone: bool = False
    run: str | None = None

    @classmethod
    def of(cls, row: dict) -> Worker:
        resource = row.get("resource") or {}
        workspace = resource.get("worktreeId") or (row["projection"]["workspace"] or {}).get("id")
        host = row["projection"].get("host") or {}
        return cls(
            dispatch=row["dispatchId"],
            status=row["dispatchStatus"],
            terminal_state=row["terminalState"],
            ownership=resource.get("ownershipState"),
            retained_reason=resource.get("retainedReason"),
            terminal=row.get("agentTerminalHandle"),
            workspace=workspace,
            worktree=(workspace or "").partition("::")[2] or None,
            environment=host["id"] if host.get("kind") == "remote" else None,
            host_gone=(row["projection"].get("liveness") or {}).get("reason") == HOST_UNAVAILABLE,
            run=row.get("runId"),
        )

    @property
    def settled(self) -> bool:
        return self.status in SETTLED


@dataclass(frozen=True)
class Terminal:
    handle: str
    agent: str | None
    title: str
    last_output_ms: int
    tab: str | None = None
    leaf: str | None = None
    worktree: str | None = None

    @classmethod
    def of(cls, row: dict) -> Terminal:
        return cls(row["handle"], row.get("agentIdentity"), row.get("title") or "", row.get("lastOutputAt") or 0, row.get("tabId"), row.get("leafId"), row.get("worktreePath"))

    @property
    def pane(self) -> str:
        return f"{self.tab}:{self.leaf}"

    def idle_shell(self, worktree: str, now_ms: int) -> bool:
        return self.agent is None and self.title.rstrip("/").rpartition("/")[2] in ("", Path(worktree).name) and now_ms - self.last_output_ms > QUIET_MS


def idle_login_shells() -> set[str]:
    rows = [line.split(None, 4) for line in subprocess.run(["ps", "-axo", "pid=,ppid=,pgid=,tpgid=,comm="], capture_output=True, text=True).stdout.splitlines()]
    parents = {row[1] for row in rows}
    return {pid for pid, _, pgid, tpgid, command in rows if command.startswith("-") and pid == pgid == tpgid and pid not in parents}


def empty_shells() -> set[str]:
    childless = idle_login_shells()
    quiet_since = time.time() - SHELL_QUIET_MS / 1000
    rows = [line.split() for line in subprocess.run(["ps", "-axo", "pid=,tty="], capture_output=True, text=True).stdout.splitlines()]
    return {pid for pid, tty in rows if pid in childless and tty != "??" and (device := Path("/dev") / tty).exists() and max(device.stat().st_mtime, device.stat().st_atime) < quiet_since}


def terminal_processes() -> dict[str, list[str]]:
    listed = subprocess.run(["ps", "-axE", "-ww", "-o", "pid=,command="], capture_output=True, text=True).stdout
    bearers: dict[str, list[str]] = {}
    for pid, command in (line.split(None, 1) for line in listed.splitlines() if len(line.split(None, 1)) == 2):
        if key := TERMINAL_KEY.search(command):
            bearers.setdefault(key[1], []).append(pid)
    return bearers


def hook_workers() -> set[str]:
    rows = [line.split(None, 2) for line in subprocess.run(["ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True).stdout.splitlines()]
    daemons = {pid for pid, _, command in rows if command.endswith(HOOK_DAEMON)}
    return {pid for pid, ppid, command in rows if ppid in daemons and command.endswith(HOOK_WORKER)}


def pane_processes(pids: set[str], panes: frozenset[str]) -> set[str]:
    if not panes:
        return set()
    listed = subprocess.run(["ps", "-E", "-ww", "-o", "pid=,tty=,command=", "-p", ",".join(sorted(pids))], capture_output=True, text=True).stdout
    return {pid for pid, tty, command in (line.split(None, 2) for line in listed.splitlines()) if tty != "??" and (key := PANE_KEY.search(command)) and key[1] in panes}


def idle_screen(tail: list[str]) -> bool:
    if any(marker in line for line in tail for marker in BUSY):
        return False
    lines = [line.strip() for line in tail if not BORDER.match(line)]
    window = len(lines) - PROMPT_WINDOW
    prompt = next((index for index in range(len(lines) - 1, max(window, 0) - 1, -1) if lines[index] == CLAUDE_PROMPT or lines[index].startswith(CODEX_PROMPT)), None)
    if prompt is None:
        return False
    said = [line for line in lines[:prompt] if not DONE_MARKER.match(line)]
    return not said or not said[-1].endswith("?")


@dataclass(frozen=True)
class Action:
    verb: str
    target: str
    reason: str = ""
    shells: frozenset[str] = frozenset()
    agents: frozenset[Action] = frozenset()
    error: str = ""
    environment: str | None = None
    deferred: bool = False

    def line(self) -> str:
        said = " ".join(part for part in (self.verb, self.target, self.reason) if part)
        return f"{said}\nerror {self.target} {self.error}" if self.error else said


@dataclass(frozen=True)
class Pause:
    resumed: Path
    files: Path

    def held(self) -> bool:
        return not self.resumed.exists()

    def files_held(self) -> bool:
        return self.files.exists()

    def skip_files(self, target: str) -> Action:
        return Action("skip", target, f"remove: worktree file deletion paused while {self.files} exists")

    def skip(self, target: str, verb: str) -> Action:
        return Action("skip", target, f"{verb}: cleanup paused until {self.resumed} exists")


class OrcaSlow(Exception):
    pass


class RemovalDeferred(Exception):
    pass


class Budget:
    def __init__(self, closes: int = CLOSES_PER_PASS, removals: int = REMOVALS_PER_PASS):
        self.left = {"close": closes, "removal": removals}
        self.lock = threading.Lock()

    def spent(self, kind: str) -> bool:
        with self.lock:
            return self.left[kind] <= 0

    def take(self, kind: str) -> bool:
        with self.lock:
            if self.left[kind] <= 0:
                return False
            self.left[kind] -= 1
            return True


@dataclass(frozen=True)
class Swept:
    path: Path

    def read(self) -> list[str]:
        return json.loads(self.path.read_text()) if self.path.is_file() else []

    def record(self, dispatches: list[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(".lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            order = [dispatch for dispatch in self.read() if dispatch not in dispatches]
            staged = self.path.with_suffix(".tmp")
            staged.write_text(json.dumps(order + dispatches))
            staged.replace(self.path)


@dataclass(frozen=True)
class Backoff:
    path: Path

    def read(self) -> dict:
        return json.loads(self.path.read_text()) if self.path.is_file() else {}

    def waiting_until(self, now: float) -> float | None:
        until = self.read().get("until", 0)
        return until if until > now else None

    def extend(self, now: float) -> int:
        delay = min(max(self.read().get("delay", 0) * 2, BACKOFF_FIRST_S), BACKOFF_MOST_S)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"until": now + delay, "delay": delay}))
        return delay

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


PACING = Backoff(BACKOFF)


class GitHubRefusal(Exception):
    @classmethod
    def of(cls, done: subprocess.CompletedProcess[str]) -> GitHubRefusal:
        return cls(f"GitHub could not answer: {(done.stderr.strip().splitlines() or [f'gh exited {done.returncode}'])[-1]}")


@dataclass(frozen=True)
class Landed:
    origin: str
    squashed: frozenset[int]

    def __contains__(self, head: str) -> bool:
        argv = ["gh", "pr", "list", "--repo", self.origin, "--state", "closed", "--search", head, "--json", "number,headRefOid"]
        done = subprocess.run(argv, capture_output=True, text=True)
        if done.returncode != 0:
            raise GitHubRefusal.of(done)
        found = json.loads(done.stdout)
        return any(pull["number"] in self.squashed and pull["headRefOid"] == head for pull in found)


def github(path: str):
    done = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    if done.returncode == 0:
        return json.loads(done.stdout)
    if UNKNOWN_TO_GITHUB.search(done.stderr):
        return None
    raise GitHubRefusal.of(done)


@functools.cache
def github_trunk(slug: str) -> str:
    return github(f"repos/{slug}")["default_branch"]


def github_compare(commit: str, slug: str) -> dict | None:
    return github(f"repos/{slug}/compare/{github_trunk(slug)}...{commit}?per_page=1")


def on_github_trunk(head: str, slug: str) -> bool:
    compared = github_compare(head, slug)
    return compared is not None and compared["status"] in ("behind", "identical")


@dataclass(frozen=True)
class GitHubTips:
    slug: str

    def __contains__(self, head: str) -> bool:
        return bool(github(f"repos/{self.slug}/commits/{head}/branches-where-head"))


@dataclass(frozen=True)
class GitHubPullHeads:
    slug: str

    def __contains__(self, head: str) -> bool:
        return any(pull["head"]["sha"] == head for pull in github(f"repos/{self.slug}/commits/{head}/pulls") or [])


@dataclass(frozen=True)
class GitHubTrunk:
    slug: str

    def __contains__(self, head: str) -> bool:
        return on_github_trunk(head, self.slug)


@dataclass(frozen=True)
class Remote:
    heads: Container[str]
    landed: Container[str]
    trunk: Container[str] = frozenset()

    def holds(self, commit: str) -> bool:
        return commit in self.heads or commit in self.landed or commit in self.trunk


@dataclass(frozen=True)
class Checkout:
    path: str
    dirty: bool
    branch: str | None
    head: str
    on_trunk: bool
    repository: str
    environment: str | None = None
    off_origin: tuple[tuple[str, str], ...] = ()
    changed: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Sprite:
    workspace: str
    host: str
    ssh_config: str
    root: str
    environment: str


def on(environment: str | None) -> tuple[str, ...]:
    return ("--environment", environment) if environment else ()


def error_of(reply: dict) -> str:
    return ": ".join(filter(None, (reply["error"]["code"], reply["error"].get("message"))))


class Orca:
    bounded = False
    slowed: str | None = None
    spent = 0.0
    spending = threading.Lock()

    def call(self, *args: str) -> dict:
        if self.slowed:
            raise OrcaSlow(self.slowed)
        orchestration = args[0] == "orchestration"
        started = time.monotonic()
        try:
            done = subprocess.run([*ORCA, *args, *(("--full",) if orchestration else ()), "--json"], capture_output=True, text=True, timeout=CALL_TIMEOUT_S if self.bounded else None)
        except subprocess.TimeoutExpired:
            self.slowed = f"orca {' '.join(args[:2])} gave no answer in {CALL_TIMEOUT_S} s"
            raise OrcaSlow(self.slowed) from None
        took = time.monotonic() - started
        if self.bounded and tuple(args[:2]) not in MUTATIONS:
            with self.spending:
                self.spent += took
                if took > SLOW_READ_S:
                    self.slowed = f"orca {' '.join(args[:2])} took {took:.1f} s, over the {SLOW_READ_S:.0f} s a read may take"
                elif self.spent > PASS_ORCA_S:
                    self.slowed = f"this pass spent {self.spent:.0f} s on Orca reads, over its {PASS_ORCA_S:.0f} s"
        reply = json.loads(done.stdout or done.stderr)
        if orchestration and reply.get("ok") and "_meta" not in reply:
            raise RuntimeError(f"orca {' '.join(args[:2])} printed its concise view, not the full record")
        return reply

    def result(self, *args: str) -> dict:
        reply = self.call(*args)
        if not reply["ok"]:
            raise RuntimeError(f"orca {' '.join(args[:2])} {error_of(reply)}")
        return reply["result"]

    def environments(self) -> set[str]:
        return {environment["id"] for environment in self.result("environment", "list")["environments"]}

    def workers(self, run: str) -> list[Worker]:
        rows: list[dict] = []
        cursor: list[str] = []
        remote = () if self.bounded else ("--include-remote",)
        while True:
            page = self.result("orchestration", "worker-list", "--run", run, *remote, "--limit", "100", "--full", *cursor)
            rows += page["workers"]
            after = (page.get("page") or {}).get("nextCursor")
            if not after:
                return [Worker.of(row) for row in rows]
            cursor = ["--cursor", after]

    def runs(self) -> list[dict]:
        runs: list[dict] = []
        cursor: list[str] = []
        while True:
            page = self.result("orchestration", "run-list", "--limit", "100", *cursor)
            runs += page["runs"]
            if not page.get("nextCursor"):
                return runs
            cursor = ["--cursor", page["nextCursor"]]

    def terminals(self) -> list[Terminal]:
        listed = self.result("terminal", "list", "--limit", "1000")
        if listed.get("truncated"):
            raise RuntimeError("orca terminal list truncated its answer at 1000 terminals")
        return [Terminal.of(row) for row in listed["terminals"] if row["connected"]]

    def idle(self, terminal: str, environment: str | None = None) -> bool | None:
        scope = on(environment)
        shown = self.call("terminal", "show", "--terminal", terminal, *scope)
        if not shown["ok"]:
            return None if shown["error"]["code"] == STALE else False
        if not shown["result"]["terminal"]["connected"]:
            return None
        probe = self.call("terminal", "wait", "--terminal", terminal, "--for", "tui-idle", "--timeout-ms", str(IDLE_PROBE_MS), *scope)
        if not probe["ok"]:
            return None if probe["error"]["code"] == STALE else False
        if not probe["result"]["wait"]["satisfied"]:
            return False
        shown = self.result("terminal", "show", "--terminal", terminal, *scope)["terminal"]
        if self.now_ms() - (shown.get("lastOutputAt") or 0) <= QUIET_MS:
            return False
        screen = self.result("terminal", "read", "--terminal", terminal, "--screen", "--limit", str(SCREEN_LINES), *scope)["terminal"]
        return idle_screen(screen["tail"])

    def presence(self, terminal: str, environment: str | None = None) -> str:
        shown = self.call("terminal", "show", "--terminal", terminal, *on(environment))
        if not shown["ok"]:
            return shown["error"]["code"]
        return CONNECTED if shown["result"]["terminal"]["connected"] else DISCONNECTED

    def terminals_in(self, worktree: str, environment: str | None = None) -> list[Terminal] | str | None:
        listed = self.call("terminal", "list", "--worktree", f"path:{worktree}", "--limit", "1000", *on(environment))
        if not listed["ok"]:
            return None if listed["error"]["code"] == UNREGISTERED else f"orca terminal list {listed['error']['code']}"
        return [Terminal.of(row) for row in listed["result"]["terminals"] if row["connected"]]

    def stop(self, dispatch: str) -> tuple[str | None, str]:
        return self.fence("worker-stop", dispatch)

    def abandon(self, dispatch: str) -> tuple[str | None, str]:
        return self.fence("worker-abandon", dispatch)

    def fence(self, verb: str, dispatch: str) -> tuple[str | None, str]:
        done = self.call("orchestration", verb, "--dispatch", dispatch, "--full")
        if not done["ok"]:
            return None, done["error"]["code"]
        return done["result"]["state"], done["result"]["processAction"]

    def release(self, dispatch: str) -> tuple[str, str]:
        result = self.result("orchestration", "worker-release", "--dispatch", dispatch, "--full")
        return result["state"], result.get("reason") or ""

    def close(self, terminal: str, environment: str | None = None) -> str | None:
        shown = self.result("terminal", "show", "--terminal", terminal, *on(environment))["terminal"]
        listed = self.terminals_in(shown["worktreePath"], environment)
        if isinstance(listed, str):
            return listed
        shared = listed is None or any(other.tab == shown.get("tabId") and other.handle != terminal for other in listed)
        done = self.call("terminal", "close", "--terminal", terminal, *(() if shared else ("--tab",)), *on(environment))
        return None if done["ok"] or done["error"]["code"] == STALE and self.presence(terminal, environment) == DISCONNECTED else done["error"]["code"]

    def remove(self, worktree: str, environment: str | None = None) -> str | None:
        done = self.call("worktree", "rm", "--worktree", f"path:{worktree}", *on(environment))
        return None if done["ok"] else error_of(done)

    def checkout(self, worktree: str, environment: str, git: Git) -> Checkout | str | None:
        shown = self.call("worktree", "show", "--worktree", f"path:{worktree}", "--environment", environment)
        if not shown["ok"]:
            return None if shown["error"]["code"] == UNREGISTERED else f"orca worktree show {shown['error']['code']}"
        tree = shown["result"]["worktree"]
        repository = git.repository()
        return Checkout(
            path=worktree,
            dirty=False,
            branch=(tree.get("branch") or "").removeprefix("refs/heads/") or None,
            head=tree["head"],
            on_trunk=git.on_trunk(tree["head"], repository),
            repository=repository,
            environment=environment,
        )

    def registered(self, worktree: str) -> bool:
        shown = self.call("worktree", "show", "--worktree", f"path:{worktree}")
        return shown["ok"] or shown["error"]["code"] != UNREGISTERED

    def now_ms(self) -> int:
        return int(time.time() * 1000)

    def holder(self, worktree: str, panes: frozenset[str]) -> str | None:
        pids = set(subprocess.run(["lsof", "-t", "+D", worktree], capture_output=True, text=True).stdout.split())
        if pids and pids - idle_login_shells() - hook_workers() - pane_processes(pids, panes):
            return "a live process has a file or its cwd open inside"
        if worktree in subprocess.run(["ps", "-axo", "command="], capture_output=True, text=True).stdout:
            return "a live process names it on its command line"
        listed = subprocess.run(["lsof", "-a", "-d", "cwd", "-Fn"], capture_output=True, text=True).stdout
        name = Path(worktree).name
        cwds = {Path(line[1:]) for line in listed.splitlines() if line.startswith("n/")}
        for cwd in {cwd for cwd in cwds if not cwd.is_relative_to(worktree) and not any(cwd.is_relative_to(root) for root in UNSCANNED_CWDS)}:
            for script in (found for pattern in HOLDER_SCRIPTS for found in cwd.glob(pattern)):
                try:
                    if name in script.read_text(errors="ignore"):
                        return f"live process script {script} names it"
                except OSError:
                    continue
        return None


class Git:
    def run(self, *args: str, cwd: str | Path | None = None, check: bool = False) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", *args], capture_output=True, text=True, cwd=cwd, check=check)

    def checkout(self, path: str) -> Checkout | str | None:
        if not Path(path).is_dir():
            return None
        found = self.run("rev-parse", "--path-format=absolute", "--git-common-dir", "HEAD", cwd=path)
        if found.returncode != 0:
            return f"git cannot read it: {(found.stderr.strip().splitlines() or [f'exit {found.returncode}'])[-1]}"
        repository, head = found.stdout.splitlines()
        return Checkout(
            path=path,
            dirty=bool(self.run("status", "--porcelain", cwd=path, check=True).stdout.strip()),
            branch=self.run("symbolic-ref", "--short", "-q", "HEAD", cwd=path).stdout.strip() or None,
            head=head,
            on_trunk=self.on_trunk(head, repository),
            repository=repository,
        )

    def repository(self) -> str:
        return self.run("rev-parse", "--path-format=absolute", "--git-common-dir", check=True).stdout.strip()

    def remove(self, worktree: str) -> str | None:
        repository = self.run("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=worktree, check=True).stdout.strip()
        done = self.run("worktree", "remove", worktree, cwd=repository)
        return None if done.returncode == 0 else (done.stderr.strip().splitlines() or [f"git worktree remove exited {done.returncode}"])[-1]

    @functools.cache
    def advertised(self, repository: str) -> tuple[str, frozenset[str]]:
        refs = [line.split("\t") for line in self.run("ls-remote", "--symref", "origin", "HEAD", "refs/heads/*", cwd=repository, check=True).stdout.splitlines()]
        trunk = next(ref for ref, name in refs if name == "HEAD" and ref.startswith("ref: ")).removeprefix("ref: refs/heads/")
        return trunk, frozenset(ref for ref, name in refs if name.startswith("refs/heads/"))

    def on_trunk(self, head: str, repository: str) -> bool:
        trunk, _ = self.advertised(repository)
        return self.run("merge-base", "--is-ancestor", head, f"origin/{trunk}", cwd=repository).returncode == 0

    def remote(self, repository: str) -> Remote:
        trunk, heads = self.advertised(repository)
        self.run("fetch", "--quiet", "origin", trunk, cwd=repository)
        subjects = self.run("log", f"origin/{trunk}", "--since=30.days", "--format=%s", cwd=repository).stdout.splitlines()
        squashed = frozenset(int(match["number"]) for subject in subjects if (match := SQUASH.search(subject)))
        origin = self.run("remote", "get-url", "origin", cwd=repository, check=True).stdout.strip()
        return Remote(heads=heads, landed=Landed(origin, squashed))


class CcRemote:
    def __init__(self, state: Path = CC_REMOTE_STATE):
        self.state = state

    def sprites(self) -> dict[tuple[str, str], Sprite]:
        hosted: dict[tuple[str, str], Sprite] = {}
        for path in sorted((self.state / "orca").glob("*.json")):
            task = json.loads(path.read_text())
            if task.get("schemaVersion") != CC_REMOTE_TASK_SCHEMA or task.get("provider") != "sprites" or not all(task.get(key) for key in ("forward", "worktreeId", "terminal")):
                continue
            if not (self.state / "workspaces" / f"{task['workspace']}.json").is_file():
                continue
            sprite = Sprite(task["workspace"], task["forward"]["host"], task["forward"]["config"], task["projectRoot"], task["environmentId"])
            hosted[(task["worktreeId"], task["terminal"])] = sprite
        return hosted

    def checkout(self, sprite: Sprite) -> Checkout | str:
        git_c = f"git -C {shlex.quote(sprite.root)}"
        off_origin = f"{{ {git_c} for-each-ref --format='%(refname) %(refname:short) %(objectname)' refs/heads refs/stash | while read -r ref name tip; do [ -z \"$({git_c} rev-list -1 \"$ref\" --not HEAD --remotes=origin)\" ] || printf '%s:%s:%s ' \"$name\" \"$tip\" \"$({git_c} rev-list --first-parent --max-parents=0 \"$ref\" --not HEAD --remotes=origin)\"; done; echo; }}"
        changed = f"{{ {git_c} worktree list --porcelain | sed -n 's/^worktree //p' | while read -r tree; do [ ! -e \"$tree\" ] || {{ if listed=$(git -C \"$tree\" status --porcelain); then files=$(printf %s \"$listed\" | grep -c '^'); else files={UNREADABLE}; fi; [ \"$files\" = 0 ] || printf '%s %s\\t' \"$files\" \"$tree\"; }}; done; echo; }}"
        done = self.ssh(sprite, f"{git_c} remote get-url origin && {off_origin} && {changed} && {git_c} status --porcelain=v2 --branch")
        if done.returncode != 0:
            return f"ssh to {sprite.host} could not read {sprite.root}: {(done.stderr.strip().splitlines() or [f'exit {done.returncode}'])[-1]}"
        origin, off_origin, changed, *lines = done.stdout.splitlines()
        hub = GITHUB_ORIGIN.search(origin)
        if hub is None:
            return f"{sprite.root} has no GitHub origin to check its HEAD against"
        headers = dict(line[2:].split(" ", 1) for line in lines if line.startswith("# "))
        head, branch = headers["branch.oid"], headers["branch.head"]
        try:
            on_trunk = on_github_trunk(head, hub["slug"])
            own = tuple((name, tip) for name, tip, root in (ref.split(":") for ref in off_origin.split()) if not root or github_compare(root, hub["slug"]))
        except GitHubRefusal as refused:
            return str(refused)
        return Checkout(
            path=sprite.root,
            dirty=bool(changed) or any(not line.startswith("# ") for line in lines),
            branch=None if branch == "(detached)" else branch,
            head=head,
            on_trunk=on_trunk,
            repository=hub["slug"],
            off_origin=own,
            changed=tuple(tree.partition(" ")[::2] for tree in changed.split("\t") if tree),
        )

    def ssh(self, sprite: Sprite, script: str, given: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["ssh", "-F", sprite.ssh_config, "-o", "BatchMode=yes", "-o", f"ConnectTimeout={SSH_CONNECT_S}", sprite.host, script], input=given, capture_output=True, text=True)

    def remote(self, slug: str) -> Remote:
        return Remote(GitHubTips(slug), GitHubPullHeads(slug), GitHubTrunk(slug))

    def destroy(self, sprite: Sprite) -> str | None:
        done = subprocess.run([CC_REMOTE, "destroy", sprite.workspace], capture_output=True, text=True)
        return None if done.returncode == 0 else (done.stderr.strip().splitlines() or [f"cc-remote destroy exited {done.returncode}"])[-1]

    def wake(self, workspace: str) -> str | None:
        done = subprocess.run([CC_REMOTE, "orca", "reconnect", workspace], capture_output=True, text=True)
        return None if done.returncode == 0 else (done.stderr.strip().splitlines() or [f"cc-remote orca reconnect exited {done.returncode}"])[-1]


def current(worker: Worker, orca: Orca) -> str | Action | None:
    presence = orca.presence(worker.terminal, worker.environment)
    if presence == DISCONNECTED:
        return None
    if presence == STALE:
        return Action("keep", worker.terminal, STALE_HANDLE)
    if presence == RUNTIME_DOWN:
        return Action("keep", worker.terminal, HOST_DOWN)
    return worker.terminal


def terminal_action(worker: Worker, live: set[str], orca: Orca, ended: dict[str, str]) -> Action | None:
    if worker.terminal_state == "released" or not worker.terminal:
        return None
    if worker.dispatch in ended:
        if ended[worker.dispatch] == CLOSED_BY_STOP:
            return None
        if worker.ownership == "owned" and worker.settled and worker.retained_reason is None:
            return Action("release", worker.dispatch)
        terminal = current(worker, orca)
        if not isinstance(terminal, str):
            return terminal
        return Action("end", terminal, f"dispatch {worker.dispatch} is done", environment=worker.environment)
    if worker.retained_reason not in (None, EXTERNAL):
        return Action("keep", worker.terminal, f"retained: {worker.retained_reason}")
    if worker.ownership == "owned":
        return Action("release", worker.dispatch)
    if worker.retained_reason != EXTERNAL and worker.environment is None:
        return Action("keep", worker.terminal, f"Orca records the terminal as {worker.ownership}")
    if worker.terminal in live:
        return Action("keep", worker.terminal, "serves an unsettled dispatch")
    terminal = current(worker, orca)
    if not isinstance(terminal, str):
        return terminal
    idle = orca.idle(terminal, worker.environment)
    if idle is None:
        return None
    if not idle:
        return Action("keep", terminal, NOT_IDLE)
    return Action("close", terminal, f"dispatch {worker.dispatch}", environment=worker.environment)


def unsaved(checkout: Checkout, remote: Remote) -> str | None:
    return next(holdings(checkout, remote), None)


def holdings(checkout: Checkout, remote: Remote) -> Iterator[str]:
    if checkout.dirty:
        yield uncommitted(checkout.changed)
    try:
        if off := [name for name, tip in checkout.off_origin if not remote.holds(tip)]:
            yield f"commits on no remote on {', '.join(off)}"
        if not (checkout.on_trunk or remote.holds(checkout.head)):
            yield f"unpushed commits on {checkout.branch or checkout.head[:10]}"
    except GitHubRefusal as refused:
        yield str(refused)


def uncommitted(changed: Iterable[tuple[str, str]]) -> str:
    counts = {UNREADABLE: "git cannot read it", "1": "1 file"}
    trees = ", ".join(f"{tree} ({counts.get(files, f'{files} files')})" for files, tree in changed)
    return f"uncommitted changes in {trees}" if trees else "uncommitted changes"


def sprite_of(workspace: str, cc_remote: CcRemote) -> Sprite | None:
    return next((sprite for sprite in cc_remote.sprites().values() if sprite.workspace == workspace), None)


def held(workspace: str, cc_remote: CcRemote) -> str | None:
    if (sprite := sprite_of(workspace, cc_remote)) is None:
        return None
    checkout = cc_remote.checkout(sprite)
    return checkout if isinstance(checkout, str) else "; ".join(holdings(checkout, cc_remote.remote(checkout.repository))) or None


class Unsaved(Exception):
    pass


def destroy(workspace: str, lane: str, cc_remote: CcRemote) -> str | None:
    if (sprite := sprite_of(workspace, cc_remote)) is None:
        return None
    saved = save(workspace, lane, cc_remote)
    checkout = cc_remote.checkout(sprite)
    if isinstance(checkout, str):
        raise Unsaved(checkout)
    if holding := unsaved(checkout, cc_remote.remote(checkout.repository)):
        raise Unsaved(holding)
    if refusal := cc_remote.destroy(sprite):
        raise Unsaved(refusal)
    return "; ".join(filter(None, [saved, f"destroyed {workspace}"]))


def save(workspace: str, lane: str, cc_remote: CcRemote) -> str | None:
    if (sprite := sprite_of(workspace, cc_remote)) is None:
        return None
    checkout = cc_remote.checkout(sprite)
    if isinstance(checkout, str):
        raise Unsaved(checkout)
    remote = cc_remote.remote(checkout.repository)
    try:
        refs = {name: tip for name, tip in checkout.off_origin if not remote.holds(tip)}
        if not (checkout.on_trunk or remote.holds(checkout.head)):
            refs.setdefault(checkout.branch or checkout.head[:10], checkout.head)
    except GitHubRefusal as refused:
        raise Unsaved(str(refused)) from None
    readable = [(files, tree) for files, tree in checkout.changed if files != UNREADABLE]
    left = [tree for files, tree in checkout.changed if files == UNREADABLE]
    kept = f"{', '.join(left)} left on {sprite.workspace}, as git cannot read it" if left else None
    if not (refs or readable):
        return kept
    done = cc_remote.ssh(sprite, save_script(sprite, checkout.repository, lane, refs, [tree for _, tree in readable]), f"{github_token()}\n")
    if done.returncode != 0:
        raise Unsaved(f"the save on {sprite.host} failed: {'; '.join(filter(None, map(str.strip, done.stderr.splitlines()))) or f'exit {done.returncode}'}")
    held = "; ".join([*([uncommitted(readable)] if readable else []), *([f"commits on no remote on {', '.join(refs)}"] if refs else [])])
    return "; ".join(filter(None, [f"{held}, saved to {checkout.repository} under saved/{lane}/", kept]))


def save_script(sprite: Sprite, slug: str, lane: str, refs: dict[str, str], trees: list[str]) -> str:
    prefix = f"saved/{lane}/"
    message = f"wip: what {lane} left uncommitted on Sprite {sprite.workspace}, saved for its next lane"
    helper = '!f() { echo username=x-access-token; echo "password=$SAVE_TOKEN"; }; f'
    lines = ["set -e", "IFS= read -r SAVE_TOKEN", "export SAVE_TOKEN GIT_TERMINAL_PROMPT=0", FREE_FULL_HOME, f"cd {shlex.quote(sprite.root)}"]
    lines += [f"git update-ref {shlex.quote('refs/heads/' + (name if name.startswith(prefix) else prefix + name))} {tip}" for name, tip in refs.items()]
    for tree in map(shlex.quote, trees):
        lines += [
            f"git -C {tree} add -A",
            f"git -C {tree} -c core.hooksPath=/dev/null commit -q --no-verify -m {shlex.quote(message)}",
            f'git update-ref "refs/heads/{prefix}$(git -C {tree} symbolic-ref --short -q HEAD || git -C {tree} rev-parse --short=10 HEAD)" "$(git -C {tree} rev-parse HEAD)"',
        ]
    lines.append(f"git -c credential.helper= -c {shlex.quote('credential.helper=' + helper)} push --quiet https://github.com/{slug}.git 'refs/heads/{prefix}*:refs/heads/{prefix}*'")
    return "\n".join(lines)


def github_token() -> str:
    return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True).stdout.strip()


def sprite_action(worker: Worker, sprite: Sprite, live_workspaces: set[str], ended: dict[str, str], orca: Orca, cc_remote: CcRemote) -> Action:
    if worker.workspace in live_workspaces:
        return Action("keep", sprite.workspace, "serves an unsettled dispatch")
    if worker.dispatch not in ended and (idle := orca.idle(worker.terminal, sprite.environment)) is not True:
        return Action("keep", sprite.workspace, f"terminal {worker.terminal} is {NOT_IDLE}" if idle is False else f"terminal {worker.terminal} is unreadable or disconnected, so its idleness is unknown")
    checkout = cc_remote.checkout(sprite)
    if isinstance(checkout, str):
        return Action("keep", sprite.workspace, checkout)
    if held := unsaved(checkout, cc_remote.remote(checkout.repository)):
        return Action("keep", sprite.workspace, held)
    return Action("destroy", sprite.workspace, f"dispatch {worker.dispatch}")


def reap(action: Action, worker: Worker, sprite: Sprite, ended: dict[str, str], orca: Orca, cc_remote: CcRemote, pause: Pause) -> Action:
    if action.verb != "destroy":
        return action
    if pause.held():
        return pause.skip(action.target, action.verb)
    if worker.dispatch not in ended and orca.idle(worker.terminal, sprite.environment) is not True:
        return Action("keep", sprite.workspace, "changed since the first read")
    if pause.held():
        return pause.skip(action.target, action.verb)
    if refusal := cc_remote.destroy(sprite):
        return Action("keep", sprite.workspace, refusal)
    return Action("destroyed", sprite.workspace, action.reason)


def worktree_action(checkout: Checkout, terminals: list[Terminal] | str, closing: dict[str, Action], remote: Remote, now_ms: int) -> Action:
    if isinstance(terminals, str):
        return Action("keep", checkout.path, terminals)
    agents = [terminal for terminal in terminals if terminal.agent and terminal.handle not in closing]
    if agents:
        return Action("keep", checkout.path, f"{len(agents)} agent terminal(s) no settled dispatch owns")
    busy = [terminal for terminal in terminals if terminal.handle not in closing and not terminal.idle_shell(checkout.path, now_ms)]
    if busy:
        return Action("keep", checkout.path, f"shell {busy[0].handle} is in use: {busy[0].title}")
    if held := unsaved(checkout, remote):
        return Action("keep", checkout.path, held)
    stopping = frozenset(closing[terminal.handle] for terminal in terminals if terminal.handle in closing)
    reason = f"stopping {', '.join(sorted(action.target for action in stopping))}" if stopping else ""
    return Action("remove", checkout.path, reason, frozenset(terminal.handle for terminal in terminals), stopping, environment=checkout.environment)


@contextmanager
def one_removal(budget: Budget | None) -> Iterator[None]:
    with REMOVING_HERE:
        if budget is not None and budget.spent("removal"):
            raise RemovalDeferred
        REMOVING.parent.mkdir(parents=True, exist_ok=True)
        with REMOVING.open("w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | (fcntl.LOCK_NB if budget is not None else 0))
            except BlockingIOError:
                raise RemovalDeferred from None
            if budget is not None:
                budget.take("removal")
            yield


def deferred(target: str, reason: str) -> Action:
    return Action("keep", target, reason, deferred=True)


def apply(action: Action, orca: Orca, git: Git, pause: Pause, budget: Budget | None = None) -> Action:
    if action.verb != "keep" and pause.held():
        return pause.skip(action.target, action.verb)
    if action.verb == "remove" and pause.files_held():
        return pause.skip_files(action.target)
    if action.verb in ("end", "close") and budget is not None and not budget.take("close"):
        return deferred(action.target, DEFERRED_CLOSE)
    if action.verb == "remove" and action.environment is None and budget is not None and budget.spent("removal"):
        return deferred(action.target, DEFERRED_REMOVAL)
    if action.verb == "release":
        state, reason = orca.release(action.target)
        return Action("released", action.target) if state in RELEASED else Action("keep", action.target, f"worker-release {state} {reason}".strip())
    if action.verb == "end":
        refusal = orca.close(action.target, action.environment)
        return Action("closed", action.target, action.reason) if refusal is None else Action("keep", action.target, refusal)
    if action.verb == "close":
        if not orca.idle(action.target, action.environment):
            return Action("keep", action.target, "changed since the first read")
        if pause.held():
            return pause.skip(action.target, action.verb)
        refusal = orca.close(action.target, action.environment)
        return Action("closed", action.target, action.reason) if refusal is None else Action("keep", action.target, refusal)
    if action.verb == "remove":
        now = orca.terminals_in(action.target, action.environment) or []
        if isinstance(now, str):
            return Action("keep", action.target, now)
        stopping = {agent.target for agent in action.agents}
        shells = [terminal for terminal in now if terminal.handle not in stopping]
        woke = any(agent.verb == "close" and orca.idle(agent.target, agent.environment) is False for agent in action.agents)
        if {terminal.handle for terminal in now} - action.shells or woke or not all(terminal.idle_shell(action.target, orca.now_ms()) for terminal in shells):
            return Action("keep", action.target, "changed since the first read")
        if action.environment is None and (held := orca.holder(action.target, frozenset(terminal.pane for terminal in now if terminal.handle in stopping))):
            return Action("keep", action.target, held)
        if pause.held():
            return pause.skip(action.target, action.verb)
        if pause.files_held():
            return pause.skip_files(action.target)
        if action.environment is not None:
            refusal = orca.remove(action.target, action.environment)
            return Action("removed", action.target, action.reason, action.shells, action.agents) if refusal is None else Action("keep", action.target, refusal)
        try:
            with one_removal(budget):
                if pause.files_held():
                    return pause.skip_files(action.target)
                refusal = orca.remove(action.target)
                if refusal is not None and refusal.startswith(UNREGISTERED) and Path(action.target).is_dir():
                    if pause.files_held():
                        return pause.skip_files(action.target)
                    if refusal := git.remove(action.target):
                        return Action("keep", action.target, f"Orca no longer tracks it and git worktree remove refused: {refusal}")
                    return Action("removed", action.target, ", ".join(filter(None, (action.reason, "Orca no longer tracks it, so git worktree remove removed it"))), action.shells, action.agents)
        except RemovalDeferred:
            return deferred(action.target, DEFERRED_REMOVAL)
        if refusal is not None and Path(action.target).is_dir():
            return Action("keep", action.target, refusal)
        error = f"orca worktree rm answered {refusal}, but its directory is gone" if refusal else ""
        return Action("removed", action.target, action.reason, action.shells, action.agents, error)
    if action.verb == "drop":
        refusal = orca.remove(action.target)
        return Action("dropped", action.target, action.reason) if refusal is None or refusal.startswith(UNREGISTERED) else Action("keep", action.target, refusal)
    return action


def stop(dispatch: str, dry_run: bool, down: Container[str], orca: Orca, pause: Pause, out) -> tuple[Action, str | None]:
    if dry_run:
        plan = Action("abandon", dispatch, ABANDONED_DOWN) if dispatch in down else Action("stop", dispatch)
        print(f"would {plan.line()}", file=out, flush=True)
        return plan, ""
    if pause.held():
        skipped = pause.skip(dispatch, "stop")
        print(skipped.line(), file=out, flush=True)
        return skipped, None
    verb, (state, detail) = "worker-stop", orca.stop(dispatch)
    host_down = state is None and detail == RUNTIME_DOWN
    if state is None and detail in (FENCED, RUNTIME_DOWN):
        verb, (state, detail) = "worker-abandon", orca.abandon(dispatch)
    if state is None:
        done = Action("keep", dispatch, f"{verb} {detail}")
    else:
        done = Action("abandoned", dispatch, ABANDONED_DOWN) if host_down else Action("stopped", dispatch, state)
    print(done.line(), file=out, flush=True)
    return done, None if state is None else detail


def gc(
    run: str | None,
    dispatches: set[str],
    dry_run: bool,
    orca: Orca,
    git: Git,
    cc_remote: CcRemote,
    pause: Pause,
    out=sys.stdout,
    ending: frozenset[str] = frozenset(),
    budget: Budget | None = None,
    record: Callable[[list[str]], None] | None = None,
    listed: list[Worker] | None = None,
) -> Counter:
    if not dry_run and pause.held():
        skipped = pause.skip(run, "gc")
        print(skipped.line(), file=out, flush=True)
        return Counter({skipped.verb: 1})
    down = {worker.dispatch for worker in orca.workers(run) if worker.host_gone} if dry_run and ending else frozenset()
    stops = [stop(dispatch, dry_run, down, orca, pause, out) for dispatch in sorted(ending)]
    ended = {action.target: detail for action, detail in stops if detail is not None}
    scope = dispatches | ending
    workers = listed if listed is not None else orca.workers(run)
    settled = {worker.dispatch for worker in workers if worker.settled or worker.dispatch in ended}
    live_terminals = {worker.terminal for worker in workers if worker.dispatch not in settled and worker.terminal}
    live_worktrees = {(worker.environment, worker.worktree) for worker in workers if worker.dispatch not in settled}
    live_workspaces = {worker.workspace for worker in workers if worker.dispatch not in settled}
    finished = [worker for worker in workers if worker.dispatch in settled and (not scope or worker.dispatch in scope)]
    sprites = cc_remote.sprites() if finished else {}
    hosted = {sprite: worker for worker in finished if (sprite := sprites.get((worker.workspace, worker.terminal)))}
    finished = [worker for worker in finished if worker not in hosted.values()]
    known = orca.environments() if any(worker.environment for worker in finished) else set()
    gone = [worker for worker in finished if worker.environment and worker.environment not in known]
    finished = [worker for worker in finished if worker not in gone]
    sprite_checkouts = {workspace for workspace, _ in sprites}

    def report(done: Action) -> Action:
        print(("would " if dry_run and done.verb not in ("keep", "gone") else "") + done.line(), file=out, flush=True)
        return done

    def act(action: Action | None) -> Action | None:
        if action is None:
            return None
        return report(action if dry_run else apply(action, orca, git, pause, budget))

    batch = [worker for worker in finished + list(hosted.values()) if worker.dispatch in dispatches] if record else []
    postponed: set[str] = set()
    recording = threading.Lock()

    def settle(done: Action | None, owns: Callable[[Worker], bool]) -> Action | None:
        if record is None or done is None or pause.held():
            return done
        with recording:
            mine = [worker.dispatch for worker in batch if owns(worker)]
            if done.deferred:
                postponed.update(mine)
            elif swept := [dispatch for dispatch in mine if dispatch not in postponed]:
                record(swept)
        return done

    def closes_after(worker: Worker, done: Action) -> bool:
        pending = closing.get(worker.terminal)
        stopped_here = done.verb in ("remove", "removed") and worker.terminal in {agent.target for agent in done.agents}
        return pending is not None and pending.verb == "close" and not stopped_here

    def in_tree(path: str, environment: str | None, done: Action | None) -> Action | None:
        return settle(done, lambda worker: (worker.environment, worker.worktree) == (environment, path) and (done.deferred or not closes_after(worker, done)))

    def reap_sprite(sprite: Sprite, worker: Worker) -> Action:
        action = sprite_action(worker, sprite, live_workspaces, ended, orca, cc_remote)
        return action if dry_run else reap(action, worker, sprite, ended, orca, cc_remote, pause)

    unplaced = [report(Action("gone", worker.dispatch, f"Orca no longer holds its environment {worker.environment}")) for worker in gone]
    unplaced += [report(Action("keep", worker.dispatch, "Orca records no workspace for it")) for worker in finished if worker.workspace is None]
    with ThreadPoolExecutor(PARALLEL) as pool:
        try:
            verdicts = [action for action in pool.map(lambda worker: terminal_action(worker, live_terminals, orca, ended), finished) if action]
            closing = {action.target: action for action in verdicts if action.verb in ("close", "end")}
            terminals = list(pool.map(act, [action for action in verdicts if action.target not in closing or action.verb == "end"]))
            trees = {(worker.environment, worker.worktree) for worker in finished if worker.worktree and worker.workspace not in sprite_checkouts} - live_worktrees
            candidates = sorted(path for environment, path in trees if environment is None)
            paired = sorted((environment, path) for environment, path in trees if environment is not None)
            read = dict(zip(candidates, pool.map(git.checkout, candidates)))
            reached = list(pool.map(lambda tree: orca.checkout(tree[1], tree[0], git), paired))
            checkouts = [checkout for checkout in [*read.values(), *reached] if isinstance(checkout, Checkout)]
            missing = [path for path, checkout in read.items() if checkout is None]
            gone = [path for path, registered in zip(missing, pool.map(orca.registered, missing)) if registered]
            worktrees = [in_tree(path, None, report(Action("keep", path, checkout))) for path, checkout in read.items() if isinstance(checkout, str)]
            worktrees += [in_tree(path, environment, report(Action("keep", path, checkout))) for (environment, path), checkout in zip(paired, reached) if isinstance(checkout, str)]
            worktrees += pool.map(lambda path: in_tree(path, None, act(Action("drop", path, "its directory is gone"))), gone)
            repositories = sorted({checkout.repository for checkout in checkouts})
            remotes = dict(zip(repositories, pool.map(git.remote, repositories)))
            worktrees += pool.map(
                lambda checkout: in_tree(checkout.path, checkout.environment, act(worktree_action(checkout, orca.terminals_in(checkout.path, checkout.environment) or [], closing, remotes[checkout.repository], orca.now_ms()))),
                checkouts,
            )
            stopped = {agent.target for done in worktrees if done.verb in ("remove", "removed") for agent in done.agents}
            closes = [action for target, action in closing.items() if target not in stopped and action.verb == "close"]
            terminals += pool.map(lambda action: settle(act(action), lambda worker: worker.terminal == action.target), closes)
            reaped = list(pool.map(lambda pair: settle(report(reap_sprite(*pair)), lambda worker: worker is pair[1]), sorted(hosted.items(), key=lambda pair: pair[0].workspace)))
        except OrcaSlow:
            pool.shutdown(cancel_futures=True)
            raise
    settle(Action("swept", run or "every Run"), lambda worker: True)
    return Counter(done.verb for done in [action for action, _ in stops] + unplaced + terminals + worktrees + reaped if done)


def in_turn(ids: list[str], swept: list[str]) -> list[str]:
    position = {key: index for index, key in enumerate(swept)}
    ids = list(dict.fromkeys(ids))
    return [key for key in ids if key not in position] + sorted((key for key in ids if key in position), key=position.__getitem__)


def due(workers: list[Worker], swept: list[str], open_handles: set[str]) -> list[str]:
    leftover = [worker.dispatch for worker in workers if worker.settled and ((worker.terminal_state != "released" and worker.terminal in open_handles) or (worker.environment is None and worker.worktree and Path(worker.worktree).is_dir()))]
    return in_turn(leftover, swept)[:SWEPT_BATCH]


def eligible_runs(orca: Orca, terminals: list[Terminal]) -> list[str]:
    configured = set(subprocess.run(["git", "config", "--global", "--get-all", SKIP_RUNS], capture_output=True, text=True).stdout.split())
    open_handles = {terminal.handle for terminal in terminals}
    return [run["id"] for run in orca.runs() if not (run["id"] == UNBOUND_RUN or run.get("legacy") or run.get("coordinator_handle") in open_handles or run["id"] in configured)]


def shells(terminals: list[Terminal], workers: list[Worker], dry_run: bool, orca: Orca, git: Git, pause: Pause, budget: Budget, out) -> Counter:
    if not dry_run and pause.held():
        return Counter()
    now = orca.now_ms()
    live = [worker for worker in workers if not worker.settled]
    live_terminals = {worker.terminal for worker in live}
    live_worktrees = {worker.worktree for worker in live if worker.environment is None}
    quiet = [
        terminal
        for terminal in terminals
        if terminal.agent is None and terminal.worktree and terminal.handle not in live_terminals and terminal.worktree not in live_worktrees and now - terminal.last_output_ms > SHELL_QUIET_MS
    ]
    if not quiet:
        return Counter()
    bearers = terminal_processes()
    empty = empty_shells()
    agents = {terminal.worktree for terminal in terminals if terminal.agent or len(bearers.get(terminal.handle, [])) > 1}
    tally: Counter = Counter()
    for terminal in quiet:
        pids = bearers.get(terminal.handle, [])
        if len(pids) != 1 or pids[0] not in empty or terminal.worktree in agents:
            continue
        action = Action("end", terminal.handle, f"empty shell, quiet three days, in {terminal.worktree}")
        if not dry_run:
            if budget.spent("close"):
                break
            action = action if pids[0] in empty_shells() else Action("keep", terminal.handle, "changed since the first read")
            action = apply(action, orca, git, pause, budget)
        print(("would " if dry_run else "") + action.line(), file=out, flush=True)
        tally[action.verb] += 1
    return tally


def sweep(run: str | None, with_shells: bool, dry_run: bool, orca: Orca, git: Git, cc_remote: CcRemote, pause: Pause, out=sys.stdout, backoff: Backoff = PACING) -> Counter:
    if not dry_run and (until := backoff.waiting_until(time.time())):
        print(f"backing off: Orca was slow, so no pass runs before {time.strftime('%H:%M', time.localtime(until))}", file=out, flush=True)
        return Counter()
    orca.bounded = True
    budget = Budget()
    rotation = Swept(SWEPT / "runs.json")
    tally: Counter = Counter()
    try:
        terminals = orca.terminals()
        turn = [run] if run else in_turn(eligible_runs(orca, terminals), rotation.read())[:1]
        if not turn:
            print("no Run to sweep", file=out, flush=True)
            return tally
        workers = orca.workers(turn[0])
        if with_shells:
            tally += shells(terminals, workers, dry_run, orca, git, pause, budget, out)
        ledger = Swept(SWEPT / f"swept-{turn[0]}.json")
        if batch := due(workers, ledger.read(), {terminal.handle for terminal in terminals}):
            tally += gc(turn[0], set(batch), dry_run, orca, git, cc_remote, pause, out, budget=budget, record=None if dry_run else ledger.record, listed=workers)
        if orca.slowed:
            raise OrcaSlow(orca.slowed)
        if not run and not dry_run:
            rotation.record(turn)
    except OrcaSlow as slow:
        waits = "" if dry_run else f"; the next pass waits {backoff.extend(time.time()) // 60} minutes"
        print(f"stopped: {slow}{waits}", file=out, flush=True)
        return tally + Counter({"stopped": 1})
    if not dry_run:
        backoff.clear()
    return tally


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reclaim what an Orca Run's settled dispatches left behind. A settled dispatch whose terminal Orca owns is released "
        "with worker-release; one whose terminal a launch script created, which Orca retains as external_terminal and never closes, has its "
        "tab closed once its screen reads idle. A recorded terminal handle Orca shows as disconnected is already closed; one Orca calls stale is kept and named. "
        "A settled dispatch's worktree is removed from Orca and git with orca worktree rm "
        "when no unsettled dispatch, no other agent, and no busy shell is left in it, its tree is clean, its HEAD is on its own repository's origin or landed there, "
        "whether or not its PR is still open, and, read right before the removal, no live process other than a login shell idle at its prompt, a process attached to a terminal the removal stops, or a capt-hookd worker has a file or "
        "its cwd inside it, names it on its command line, or runs from a directory whose shell or Python script names it; the removal stops its login shells "
        "and the idle terminals gc would close, so only terminals in a kept worktree are closed tab by tab through the renderer. A settled dispatch's worktree "
        "whose directory is gone has its Orca registration dropped; one git cannot read is kept and named. A settled dispatch Orca records no "
        "workspace for, such as one that failed before its worktree resolved, is kept and named. --done ends a lane the root has decided is done, settled or not: "
        "worker-stop fences the dispatch, or worker-abandon settles one Orca already fenced, whose worker-stop reads dispatch_inactive, or one on a host Orca cannot reach, "
        "whose worker-stop reads remote_runtime_unavailable and whose worker could keep running if the host comes back; --dry-run predicts that abandon when Orca reads the "
        "host as unavailable; its terminal is closed "
        "without the idle check before its worktree is reclaimed by the same rules. "
        "A settled dispatch on a paired Orca server, such as a dev box a launch script started it on, gets the same terminal close and worktree "
        "removal through that server's --environment; its tree's HEAD is read from Orca, its cleanliness is left to orca worktree rm, which runs "
        "git worktree remove without --force and so refuses modified or untracked files, and no Mac process scan applies. "
        "A settled dispatch on a paired server whose environment orca environment list no longer names, such as a Sprite cc-remote destroy already took down, "
        "has nothing left to reclaim, and is named gone with no terminal close or worktree read. "
        "A settled dispatch whose worktree and terminal a cc-remote Sprite task records, with the workspace still recorded, has its Sprite destroyed "
        "with cc-remote destroy, WORKER_CC_REMOTE, else ccRemote in the working directory's .orca.json, else cc-remote on PATH, under the CC_REMOTE_CONFIG it inherits, else ccRemoteConfig in that .orca.json, which refuses a machine without that workspace's label and removes its Orca environment, once no unsettled dispatch "
        "shares the workspace, its terminal reads idle twice (skipped under --done), and its checkout, read over ssh, is clean in every worktree, holds no other branch or stash with commits on no remote, whose tip GitHub holds neither as a branch's head, an open or merged PR's head, whatever its base, nor in its default branch, and has its HEAD on origin "
        "or landed, which GitHub answers for the Sprite's own origin from any working directory; when GitHub cannot answer, such as under a rate limit, the Sprite is kept and named. A removal Orca answers with an error, such as runtime_unavailable when the runtime drops the connection, still counts as removed when "
        "the worktree's directory is gone afterwards, and the Orca error follows on its own error line. A worktree Orca answers selector_not_found for, because it "
        "no longer tracks it, is removed with git worktree remove after the same checks, and kept and named when git refuses. Cleanup is paused until $XDG_STATE_HOME/orca-gc/resumed, default ~/.local/state/orca-gc/resumed, "
        "exists: a paused pass changes nothing, and a pause set mid-pass is read before each candidate and again right before its stop, release, "
        "terminal close, worktree removal, or Sprite destroy, which it skips and names, while an operation already started runs to completion. "
        "While $XDG_STATE_HOME/orca-gc/files-paused exists, every worktree removal, on this Mac or a paired server, is skipped and named, read before each candidate and again right before its removal, "
        "while stops, releases, terminal closes, gone worktrees dropped from Orca and Sprite destroys go ahead. "
        "--dry-run reads through a pause. "
        "--held reads, and changes nothing: it prints what the Sprite a cc-remote task records under one workspace name holds that a fresh Sprite would not get, "
        "by the same checkout read that keeps a Sprite, naming each of them: every worktree with uncommitted changes and its count of changed files, every branch "
        "or stash with commits on no remote, and an unpushed HEAD; or the reason the read failed, or nothing. A branch whose first-parent history begins at a root commit "
        "GitHub's default branch shares no history with, such as the cc-notes-source-live-v2 branch cc-notes pins its own records on, is no lane's work and counts for nothing. It needs no Orca, so it reads a Sprite whose dispatch "
        "or environment Orca has dropped. "
        "--save WORKSPACE LANE takes what --held would name to GitHub, so a fresh Sprite gets it: one ssh to that Sprite first deletes ~/.cache when its home has "
        f"under {FULL_HOME_KIB // 1024} MiB free, the line cc-remote's runtime names out of disk space at, since git can write no commit on a full disk, then commits each worktree's "
        "uncommitted changes there with --no-verify and no hooks, names each commit, branch or stash GitHub holds nowhere, by a branch head, an open or merged PR's "
        "head or its default branch, as the local branch saved/LANE/<its name>, a worktree's commit by its own branch or short sha, and pushes "
        "refs/heads/saved/LANE/* to github.com over https on the token gh auth token prints, which it reads from stdin into that one shell's "
        "environment and hands git through a credential helper naming only the variable, so nothing writes it to the Sprite's disk or history. "
        "It prints what it saved and the repository and saved/LANE/ it went under, naming no branch, since GitHub's own refs say which exist, or nothing when the Sprite holds nothing, and exits 1 naming "
        "why when the read or the save fails, which leaves each commit it made on its saved/LANE branch, still read as held. "
        "--destroy WORKSPACE LANE first takes what --held would name to GitHub as --save WORKSPACE LANE does, then destroys the Sprite a cc-remote task records under that workspace name "
        "with cc-remote destroy, which closes its forward and removes its Orca environment, once the same checkout read, made again after the save, finds it holds nothing --held would name. "
        "It needs no Orca Run or dispatch and no idle terminal, so it reaches a Sprite whose dispatch Orca has dropped, and the pause does not hold it. It prints what it saved, as --save does, "
        "and the workspace it destroyed, or nothing when no task records a Sprite under that name, and exits 1 naming why the read or the save failed, what the Sprite still holds, "
        "or cc-remote's refusal. "
        "--wake WORKSPACE wakes the Sprite a cc-remote task records under that workspace name with cc-remote orca reconnect, which reopens its forward, waits for the Orca runtime "
        "a cold boot starts and adopts it, so Orca reaches the Sprite again; it starts no worker. It prints the workspace it woke, or exits 1 naming cc-remote's refusal. "
        "--new is the recurring pass. Without --run it takes one Run a pass, those no pass has swept first, then the one swept longest ago, and skips a Run whose "
        f"coordinator terminal is open, a legacy Run, the unbound Run, and each Run git config --global --get-all {SKIP_RUNS} names. "
        f"In that Run it sweeps up to {SWEPT_BATCH} settled dispatches with an open terminal or a worktree still on disk, those no pass has swept first, newest first, "
        "then those swept longest ago, recording each one under $XDG_STATE_HOME/orca-gc as its last step finishes, so a pass killed partway does not repeat its work. "
        f"A --new pass closes at most {CLOSES_PER_PASS} terminals and removes at most {REMOVALS_PER_PASS} local worktrees, and never waits on the removal lock; "
        "what it defers stays first in line for the next pass. It reads only this Mac's workers, not paired servers', which take Orca seconds a page to gather, "
        "so it leaves paired-server and Sprite dispatches to a hand pass. Each Orca call gets "
        f"{CALL_TIMEOUT_S} s, and once a read takes over {SLOW_READ_S:.0f} s, or the pass's reads add up to over {PASS_ORCA_S:.0f} s, the pass makes no further Orca call and the next pass waits, "
        f"{BACKOFF_FIRST_S // 60} minutes at first, doubling to {BACKOFF_MOST_S // 3600} hours, until a pass finishes with Orca answering in time. "
        "--shells also closes an Orca shell terminal with no agent in a worktree that holds no agent, whose one process is a login shell with no child, "
        "and whose terminal printed nothing and whose tty was neither read nor written for three days, read again right before the close. "
        "Prints one line per action, skipped action, or kept resource as it happens, then the tally."
    )
    parser.add_argument("--run", help="the Orca Run id; --new without it takes the next Run in turn")
    parser.add_argument("--dispatch", action="append", default=[], metavar="CTX", help="only this settled dispatch; repeatable")
    parser.add_argument("--done", action="append", default=[], metavar="CTX", help="worker-stop this dispatch, close its terminal, and reclaim its worktree; repeatable")
    parser.add_argument("--held", metavar="WORKSPACE", help="print what this cc-remote workspace's Sprite holds on no remote, or nothing, and change nothing")
    parser.add_argument("--save", nargs=2, metavar=("WORKSPACE", "LANE"), help="commit and push what this cc-remote workspace's Sprite holds on no remote to saved/LANE/<name> branches on GitHub, and print what it saved")
    parser.add_argument("--destroy", nargs=2, metavar=("WORKSPACE", "LANE"), help="save what this cc-remote workspace's Sprite holds on no remote as --save does, then destroy it and its Orca environment, and print both")
    parser.add_argument("--wake", metavar="WORKSPACE", help="wake this cc-remote workspace's Sprite and reconnect Orca to the runtime it runs, starting no worker")
    parser.add_argument("--new", action="store_true", help="the recurring pass: a bounded batch of settled dispatches, capped closes and removals, backing off when Orca is slow")
    parser.add_argument("--shells", action="store_true", help="with --new, also close empty Orca shells quiet for three days")
    parser.add_argument("--dry-run", action="store_true", help="print what gc would do and change nothing")
    args = parser.parse_args()
    if args.new and (args.dispatch or args.done):
        parser.error("--new picks its own dispatches; drop --dispatch and --done")
    if args.shells and not args.new:
        parser.error("--shells runs only with --new")
    if (args.held or args.save or args.destroy or args.wake) and (args.run or args.new or args.dispatch or args.done or args.dry_run or sum(map(bool, (args.held, args.save, args.destroy, args.wake))) > 1):
        parser.error("--held, --save, --destroy and --wake each take one Sprite by its workspace name; use one, and drop --run, --new, --dispatch, --done and --dry-run")
    if args.held:
        print(held(args.held, CcRemote()) or "")
        return 0
    if args.save:
        try:
            print(save(*args.save, CcRemote()) or "")
        except Unsaved as refused:
            print(refused, file=sys.stderr)
            return 1
        return 0
    if args.destroy:
        try:
            print(destroy(*args.destroy, CcRemote()) or "")
        except Unsaved as refused:
            print(refused, file=sys.stderr)
            return 1
        return 0
    if args.wake:
        if refusal := CcRemote().wake(args.wake):
            print(refusal, file=sys.stderr)
            return 1
        print(f"woke {args.wake}")
        return 0
    if not args.new and not args.run:
        parser.error("--run is required without --new, --held, --save, --destroy or --wake")
    if args.new:
        tally = sweep(args.run, args.shells, args.dry_run, Orca(), Git(), CcRemote(), Pause(RESUMED, FILES_PAUSED))
    else:
        tally = gc(args.run, set(args.dispatch), args.dry_run, Orca(), Git(), CcRemote(), Pause(RESUMED, FILES_PAUSED), ending=frozenset(args.done))
    print(" ".join(f"{verb}={count}" for verb, count in sorted(tally.items())) or "nothing to reclaim")
    return 0


if __name__ == "__main__":
    sys.exit(main())
