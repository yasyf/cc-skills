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
import time
from collections import Counter
from collections.abc import Container, Iterator
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
SQUASH = re.compile(r" \(#(?P<number>\d+)\)$")
GITHUB_ORIGIN = re.compile(r"github\.com[:/](?P<slug>[^/\s]+/[^/\s]+?)(?:\.git)?/?$")
UNKNOWN_TO_GITHUB = re.compile(r"HTTP 4(04|22)")
REPO_VALUES = json.loads(Path(".orca.json").read_text()) if Path(".orca.json").is_file() else {}
CC_REMOTE = os.environ.get("WORKER_CC_REMOTE") or REPO_VALUES.get("ccRemote") or "cc-remote"
if "ccRemoteConfig" in REPO_VALUES:
    os.environ.setdefault("CC_REMOTE_CONFIG", str(Path(REPO_VALUES["ccRemoteConfig"]).resolve()))
STATE_HOME = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
CC_REMOTE_STATE = STATE_HOME / "cc-remote"
RESUMED = STATE_HOME / "orca-gc" / "resumed"
REMOVING = STATE_HOME / "orca-gc" / "remove.lock"
CC_REMOTE_TASK_SCHEMA = 1


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

    @classmethod
    def of(cls, row: dict) -> Terminal:
        return cls(row["handle"], row.get("agentIdentity"), row.get("title") or "", row.get("lastOutputAt") or 0, row.get("tabId"), row.get("leafId"))

    @property
    def pane(self) -> str:
        return f"{self.tab}:{self.leaf}"

    def idle_shell(self, worktree: str, now_ms: int) -> bool:
        return self.agent is None and self.title.rstrip("/").rpartition("/")[2] in ("", Path(worktree).name) and now_ms - self.last_output_ms > QUIET_MS


def idle_login_shells() -> set[str]:
    rows = [line.split(None, 4) for line in subprocess.run(["ps", "-axo", "pid=,ppid=,pgid=,tpgid=,comm="], capture_output=True, text=True).stdout.splitlines()]
    parents = {row[1] for row in rows}
    return {pid for pid, _, pgid, tpgid, command in rows if command.startswith("-") and pid == pgid == tpgid and pid not in parents}


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

    def line(self) -> str:
        said = " ".join(part for part in (self.verb, self.target, self.reason) if part)
        return f"{said}\nerror {self.target} {self.error}" if self.error else said


@dataclass(frozen=True)
class Pause:
    resumed: Path

    def held(self) -> bool:
        return not self.resumed.exists()

    def skip(self, target: str, verb: str) -> Action:
        return Action("skip", target, f"{verb}: cleanup paused until {self.resumed} exists")


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


def on_github_trunk(head: str, slug: str) -> bool:
    compared = github(f"repos/{slug}/compare/{github_trunk(slug)}...{head}?per_page=1")
    return compared is not None and compared["status"] in ("behind", "identical")


@dataclass(frozen=True)
class GitHubTips:
    slug: str

    def __contains__(self, head: str) -> bool:
        return bool(github(f"repos/{self.slug}/commits/{head}/branches-where-head"))


@dataclass(frozen=True)
class GitHubLanded:
    slug: str

    def __contains__(self, head: str) -> bool:
        pulls = github(f"repos/{self.slug}/commits/{head}/pulls") or []
        return any(pull["merged_at"] and pull["head"]["sha"] == head and pull["base"]["ref"] == github_trunk(self.slug) for pull in pulls)


@dataclass(frozen=True)
class Remote:
    heads: Container[str]
    landed: Container[str]


@dataclass(frozen=True)
class Checkout:
    path: str
    dirty: bool
    branch: str | None
    head: str
    on_trunk: bool
    repository: str
    environment: str | None = None
    off_origin: int = 0


@dataclass(frozen=True)
class Sprite:
    workspace: str
    host: str
    ssh_config: str
    root: str
    environment: str


def on(environment: str | None) -> tuple[str, ...]:
    return ("--environment", environment) if environment else ()


class Orca:
    def call(self, *args: str) -> dict:
        orchestration = args[0] == "orchestration"
        done = subprocess.run([*ORCA, *args, *(("--full",) if orchestration else ()), "--json"], capture_output=True, text=True)
        reply = json.loads(done.stdout or done.stderr)
        if orchestration and reply.get("ok") and "_meta" not in reply:
            raise RuntimeError(f"orca {' '.join(args[:2])} printed its concise view, not the full record")
        return reply

    def workers(self, run: str) -> list[Worker]:
        rows: list[dict] = []
        cursor: list[str] = []
        while True:
            page = self.call("orchestration", "worker-list", "--run", run, "--include-remote", "--limit", "100", "--full", *cursor)["result"]
            rows += page["workers"]
            after = (page.get("page") or {}).get("nextCursor")
            if not after:
                return [Worker.of(row) for row in rows]
            cursor = ["--cursor", after]

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
        shown = self.call("terminal", "show", "--terminal", terminal, *scope)["result"]["terminal"]
        if self.now_ms() - (shown.get("lastOutputAt") or 0) <= QUIET_MS:
            return False
        screen = self.call("terminal", "read", "--terminal", terminal, "--screen", "--limit", str(SCREEN_LINES), *scope)["result"]["terminal"]
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
        result = self.call("orchestration", "worker-release", "--dispatch", dispatch, "--full")["result"]
        return result["state"], result.get("reason") or ""

    def close(self, terminal: str, environment: str | None = None) -> str | None:
        shown = self.call("terminal", "show", "--terminal", terminal, *on(environment))["result"]["terminal"]
        listed = self.terminals_in(shown["worktreePath"], environment)
        if isinstance(listed, str):
            return listed
        shared = listed is None or any(other.tab == shown.get("tabId") and other.handle != terminal for other in listed)
        done = self.call("terminal", "close", "--terminal", terminal, *(() if shared else ("--tab",)), *on(environment))
        return None if done["ok"] or done["error"]["code"] == STALE and self.presence(terminal, environment) == DISCONNECTED else done["error"]["code"]

    def remove(self, worktree: str, environment: str | None = None) -> str | None:
        done = self.call("worktree", "rm", "--worktree", f"path:{worktree}", *on(environment))
        return None if done["ok"] else ": ".join(filter(None, (done["error"]["code"], done["error"].get("message"))))

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

    def forget(self, environment: str) -> str | None:
        done = self.call("environment", "rm", "--environment", environment)
        return None if done["ok"] else ": ".join(filter(None, (done["error"]["code"], done["error"].get("message"))))

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
        argv = ["ssh", "-F", sprite.ssh_config, "-o", "BatchMode=yes", sprite.host, f"{git_c} remote get-url origin && {git_c} rev-list --count --branches --glob=refs/stash --not HEAD --remotes=origin && {git_c} worktree list --porcelain | sed -n 's/^worktree //p' | while read -r tree; do [ ! -e \"$tree\" ] || git -C \"$tree\" status --porcelain || echo unreadable; done | wc -l && {git_c} status --porcelain=v2 --branch"]
        done = subprocess.run(argv, capture_output=True, text=True)
        if done.returncode != 0:
            return f"ssh to {sprite.host} could not read {sprite.root}: {(done.stderr.strip().splitlines() or [f'exit {done.returncode}'])[-1]}"
        origin, off_origin, worktree_changes, *lines = done.stdout.splitlines()
        hub = GITHUB_ORIGIN.search(origin)
        if hub is None:
            return f"{sprite.root} has no GitHub origin to check its HEAD against"
        headers = dict(line[2:].split(" ", 1) for line in lines if line.startswith("# "))
        head, branch = headers["branch.oid"], headers["branch.head"]
        try:
            on_trunk = on_github_trunk(head, hub["slug"])
        except GitHubRefusal as refused:
            return str(refused)
        return Checkout(
            path=sprite.root,
            dirty=int(worktree_changes) > 0 or any(not line.startswith("# ") for line in lines),
            branch=None if branch == "(detached)" else branch,
            head=head,
            on_trunk=on_trunk,
            repository=hub["slug"],
            off_origin=int(off_origin),
        )

    def remote(self, slug: str) -> Remote:
        return Remote(GitHubTips(slug), GitHubLanded(slug))

    def destroy(self, sprite: Sprite) -> str | None:
        done = subprocess.run([CC_REMOTE, "destroy", sprite.workspace], capture_output=True, text=True)
        return None if done.returncode == 0 else (done.stderr.strip().splitlines() or [f"cc-remote destroy exited {done.returncode}"])[-1]


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
    if checkout.dirty:
        return "uncommitted changes"
    if checkout.off_origin:
        return f"{checkout.off_origin} commits off origin on its other branches or stashes"
    try:
        if not (checkout.on_trunk or checkout.head in remote.heads or checkout.head in remote.landed):
            return f"unpushed commits on {checkout.branch or checkout.head[:10]}"
    except GitHubRefusal as refused:
        return str(refused)
    return None


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
    if refusal := orca.forget(sprite.environment):
        return Action("destroyed", sprite.workspace, f"{action.reason}, Orca environment {sprite.environment} kept: {refusal}")
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
def one_removal() -> Iterator[None]:
    REMOVING.parent.mkdir(parents=True, exist_ok=True)
    with REMOVING.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def apply(action: Action, orca: Orca, git: Git, pause: Pause) -> Action:
    if action.verb != "keep" and pause.held():
        return pause.skip(action.target, action.verb)
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
        if action.environment is not None:
            refusal = orca.remove(action.target, action.environment)
            return Action("removed", action.target, action.reason, action.shells, action.agents) if refusal is None else Action("keep", action.target, refusal)
        with one_removal():
            refusal = orca.remove(action.target)
            if refusal is not None and refusal.startswith(UNREGISTERED) and Path(action.target).is_dir():
                if refusal := git.remove(action.target):
                    return Action("keep", action.target, f"Orca no longer tracks it and git worktree remove refused: {refusal}")
                return Action("removed", action.target, ", ".join(filter(None, (action.reason, "Orca no longer tracks it, so git worktree remove removed it"))), action.shells, action.agents)
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


def gc(run: str, dispatches: set[str], dry_run: bool, orca: Orca, git: Git, cc_remote: CcRemote, pause: Pause, out=sys.stdout, ending: frozenset[str] = frozenset()) -> Counter:
    if not dry_run and pause.held():
        skipped = pause.skip(run, "gc")
        print(skipped.line(), file=out, flush=True)
        return Counter({skipped.verb: 1})
    down = {worker.dispatch for worker in orca.workers(run) if worker.host_gone} if dry_run and ending else frozenset()
    stops = [stop(dispatch, dry_run, down, orca, pause, out) for dispatch in sorted(ending)]
    ended = {action.target: detail for action, detail in stops if detail is not None}
    scope = dispatches | ending
    workers = orca.workers(run)
    settled = {worker.dispatch for worker in workers if worker.settled or worker.dispatch in ended}
    live_terminals = {worker.terminal for worker in workers if worker.dispatch not in settled and worker.terminal}
    live_worktrees = {(worker.environment, worker.worktree) for worker in workers if worker.dispatch not in settled}
    live_workspaces = {worker.workspace for worker in workers if worker.dispatch not in settled}
    finished = [worker for worker in workers if worker.dispatch in settled and (not scope or worker.dispatch in scope)]
    sprites = cc_remote.sprites() if finished else {}
    hosted = {sprite: worker for worker in finished if (sprite := sprites.get((worker.workspace, worker.terminal)))}
    finished = [worker for worker in finished if worker not in hosted.values()]
    sprite_checkouts = {workspace for workspace, _ in sprites}

    def report(done: Action) -> Action:
        print(("would " if dry_run and done.verb != "keep" else "") + done.line(), file=out, flush=True)
        return done

    def act(action: Action | None) -> Action | None:
        if action is None:
            return None
        return report(action if dry_run else apply(action, orca, git, pause))

    def reap_sprite(sprite: Sprite, worker: Worker) -> Action:
        action = sprite_action(worker, sprite, live_workspaces, ended, orca, cc_remote)
        return action if dry_run else reap(action, worker, sprite, ended, orca, cc_remote, pause)

    unplaced = [report(Action("keep", worker.dispatch, "Orca records no workspace for it")) for worker in finished if worker.workspace is None]
    with ThreadPoolExecutor(PARALLEL) as pool:
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
        worktrees = [report(Action("keep", path, checkout)) for path, checkout in read.items() if isinstance(checkout, str)]
        worktrees += [report(Action("keep", path, checkout)) for (_, path), checkout in zip(paired, reached) if isinstance(checkout, str)]
        worktrees += pool.map(act, [Action("drop", path, "its directory is gone") for path in gone])
        repositories = sorted({checkout.repository for checkout in checkouts})
        remotes = dict(zip(repositories, pool.map(git.remote, repositories)))
        worktrees += pool.map(lambda checkout: act(worktree_action(checkout, orca.terminals_in(checkout.path, checkout.environment) or [], closing, remotes[checkout.repository], orca.now_ms())), checkouts)
        stopped = {agent.target for done in worktrees if done.verb in ("remove", "removed") for agent in done.agents}
        terminals += pool.map(act, [action for target, action in closing.items() if target not in stopped and action.verb == "close"])
        reaped = list(pool.map(lambda pair: report(reap_sprite(*pair)), sorted(hosted.items(), key=lambda pair: pair[0].workspace)))
    return Counter(done.verb for done in [action for action, _ in stops] + unplaced + terminals + worktrees + reaped if done)


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
        "A settled dispatch whose worktree and terminal a cc-remote Sprite task records, with the workspace still recorded, has its Sprite destroyed "
        "with cc-remote destroy, WORKER_CC_REMOTE, else ccRemote in the working directory's .orca.json, else cc-remote on PATH, under the CC_REMOTE_CONFIG it inherits, else ccRemoteConfig in that .orca.json, which refuses a machine without that workspace's label, and its Orca environment removed, once no unsettled dispatch "
        "shares the workspace, its terminal reads idle twice (skipped under --done), and its checkout, read over ssh, is clean in every worktree, holds no commit off origin on another branch or stash, and has its HEAD on origin "
        "or landed, which GitHub answers for the Sprite's own origin from any working directory; when GitHub cannot answer, such as under a rate limit, the Sprite is kept and named. A removal Orca answers with an error, such as runtime_unavailable when the runtime drops the connection, still counts as removed when "
        "the worktree's directory is gone afterwards, and the Orca error follows on its own error line. A worktree Orca answers selector_not_found for, because it "
        "no longer tracks it, is removed with git worktree remove after the same checks, and kept and named when git refuses. Cleanup is paused until $XDG_STATE_HOME/orca-gc/resumed, default ~/.local/state/orca-gc/resumed, "
        "exists: a paused pass changes nothing, and a pause set mid-pass is read before each candidate and again right before its stop, release, "
        "terminal close, worktree removal, or Sprite destroy, which it skips and names, while an operation already started runs to completion. "
        "--dry-run reads through a pause. Prints one line per action, skipped action, or kept resource, then the tally."
    )
    parser.add_argument("--run", required=True, help="the Orca Run id")
    parser.add_argument("--dispatch", action="append", default=[], metavar="CTX", help="only this settled dispatch; repeatable")
    parser.add_argument("--done", action="append", default=[], metavar="CTX", help="worker-stop this dispatch, close its terminal, and reclaim its worktree; repeatable")
    parser.add_argument("--dry-run", action="store_true", help="print what gc would do and change nothing")
    args = parser.parse_args()
    tally = gc(args.run, set(args.dispatch), args.dry_run, Orca(), Git(), CcRemote(), Pause(RESUMED), ending=frozenset(args.done))
    print(" ".join(f"{verb}={count}" for verb, count in sorted(tally.items())) or "nothing to reclaim")
    return 0


if __name__ == "__main__":
    sys.exit(main())
