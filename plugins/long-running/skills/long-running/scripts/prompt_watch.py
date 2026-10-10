#!/usr/bin/env python3
"""The prompt watch: one detached reader per drive that finds every terminal stopped at a prompt and tells whoever can act.

    prompt_watch.py start --drive ID [--root-terminal HANDLE]
    prompt_watch.py run   --drive ID [--once]
    prompt_watch.py show  (--drive ID | --cci-drive NAME) [--json]

STDLIB ONLY. Every 30 seconds `run` reads the drive's registry entry and polls its terminals:
the coordinator's (`root_terminal`), each standing desk `drive.py desk` registered, and every
in-progress worker of the drive's Orca Run, local or remote. Orca's `agentWait` only says where
to look. The rendered screen decides, and each terminal lands in exactly one state:

    approval     an approval dialog is on screen: a hook's ask, a tool permission, a trust check
    question     a question picker is on screen
    stale        agentWait is set and the agent's own input box is back, so the prompt was answered
    unknown      the terminal cannot be read, or agentWait is set and the screen shows neither a
                 known dialog nor an input box
    unreachable  the remote host holding the terminal does not answer
    clear        no wait and no dialog

The coordinator and the desks get a screen read on every poll. A local worker gets one only
while its agentWait is set. A remote worker gets one on every poll, because a host running
Orca's managed server installs no agent hook and never sets agentWait. A remote terminal is
read with `--environment <server name>` from its `worker-show`.

One cci record per prompt, from lane `prompt-watch`, keyed by the dialog and its `since`:
a worker's or desk's prompt is a `blocker` to `root`. The coordinator cannot be woken by its
own prompt, so its prompt is a `blocker` to the registered supervisor desk while that desk's
own terminal reads clear or stale. It is an `ask` to `owner`, which the dashboard's needs-owner
card shows, when no supervisor is registered, when the supervisor is itself at a prompt,
unknown, or unreachable, and when the prompt is still open five minutes after the supervisor
was told. A terminal unknown or unreachable for three polls in a row is one `report` to `root`.
When a prompt leaves the screen an `unblock` record resolves each record it raised.

The watch types into no terminal and answers no prompt. It never stops, restarts, releases,
closes, or signals anything. `run` holds `<state dir>/prompt-watch/lock`, so a second one
exits at once, and it ends when the drive's registry file is gone. A poll that raises is
logged and the next one runs, so the state file ages and `show` reports the watch down.
`start` detaches a `run` and logs to `<state dir>/prompt-watch/watch.log`; with `--root-terminal` it first records the
coordinator's terminal. `show` prints the last poll from `<state dir>/prompt-watch/state.json`:
a count line, then one line per terminal that is not clear, and `PROMPT-WATCH-DOWN` when the
last poll is over two minutes old. `ledger.py summary` and the desk runner's sweep read that view.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import drive
import prompt_screen

SCRIPT = Path(__file__).resolve()
LANE = "prompt-watch"
ROOT = "root"
OWNER = "owner"
COORDINATOR = "coordinator"
SUPERVISOR = "supervisor"
DESK = "desk"
WORKER = "worker"
WORKERS = "workers"
WATCH = "watch"
DOWN = "down"
STALE = "stale"
UNKNOWN = "unknown"
UNREACHABLE = "unreachable"
CLEAR = "clear"
PROMPTS = (prompt_screen.APPROVAL, prompt_screen.QUESTION)
UNSEEN = (UNKNOWN, UNREACHABLE)
WAITS_ON = {prompt_screen.APPROVAL: "an approval", prompt_screen.QUESTION: "a question"}
UNREACHABLE_CODES = frozenset({"remote_runtime_unavailable"})
UNPARSEABLE = "unparseable"
TIMED_OUT = 124
IN_PROGRESS = "in_progress"
POLL_SECONDS = 30
ORCA_SECONDS = 20
SHOW_WORKERS = 8
UNSEEN_POLLS = 3
SUPERVISOR_WAIT = timedelta(minutes=5)
DOWN_AFTER = timedelta(minutes=2)
CCI_TEXT = 400
RECEIPTS = Path(".claude/scratch/orca-launch")
WATCH_DIR = Path("prompt-watch")
STATE_FILE = "state.json"
LOCK_FILE = "lock"
LOG_FILE = "watch.log"
TERMINAL_ENV = "ORCA_TERMINAL_HANDLE"


@dataclass
class Done:
    code: int
    out: str
    err: str


class Shell:
    """The single side-effect boundary: every subprocess and the clock pass through here."""

    def run(self, argv: list[str], env: dict[str, str] | None = None) -> Done:
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=ORCA_SECONDS, env={**os.environ, **env} if env else None)
        except subprocess.TimeoutExpired:
            return Done(TIMED_OUT, "", f"no answer in {ORCA_SECONDS}s")
        return Done(proc.returncode, proc.stdout, proc.stderr)

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


@dataclass(frozen=True)
class Subject:
    key: str
    role: str
    name: str
    terminal: str = ""
    environment: str = ""
    dispatch: str = ""
    failure: tuple[str, str] | None = None


@dataclass(frozen=True)
class Seen:
    subject: Subject
    state: str
    detail: str = ""
    since: int | None = None


def stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_stamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def watch_dir(entry: dict) -> Path:
    return Path(entry["state_dir"]) / WATCH_DIR


def read_command(subject: Subject) -> str:
    return f"orca terminal read --terminal {subject.terminal} --screen" + (f" --environment {subject.environment}" if subject.environment else "")


def unseen(error: dict) -> tuple[str, str]:
    state = UNREACHABLE if error["code"] in UNREACHABLE_CODES else UNKNOWN
    return state, f"{error['code']}: {' '.join(error.get('message', '').split())}"[:200]


class Watch:
    def __init__(self, shell: Shell, ident: str):
        self.shell = shell
        self.ident = ident

    def orca(self, *argv: str, environment: str = "", env: dict[str, str] | None = None) -> dict:
        done = self.shell.run(["orca", *argv, *(["--environment", environment] if environment else []), "--json"], env)
        try:
            return json.loads(done.out)
        except json.JSONDecodeError:
            return {"ok": False, "error": {"code": "timeout" if done.code == TIMED_OUT else UNPARSEABLE, "message": (done.out or done.err).strip()}}

    def lanes(self, run: str) -> dict[str, str]:
        named = {}
        for receipt in sorted((Path.home() / RECEIPTS / run).glob("*.json")):
            try:
                named[json.loads(receipt.read_text())["result"]["dispatchId"]] = receipt.stem
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
        return named

    def workers(self, entry: dict) -> list[Subject]:
        """Every in-progress worker of the drive's Run, or of the Run bound to the coordinator's terminal when the registry names none."""
        run, root = entry.get("orca_run"), entry.get("root_terminal")
        if not run and not root:
            return []
        scope, env = (["--run", run], None) if run else ([], {TERMINAL_ENV: root})
        found: list[dict] = []
        cursor: list[str] = []
        while True:
            listed = self.orca("orchestration", "worker-list", *scope, *cursor, env=env)
            if not listed.get("ok"):
                return [Subject(WORKERS, WORKERS, "worker-list", failure=unseen(listed["error"]))]
            result = listed["result"]
            if not run and result["scope"]["source"] != "bound":
                return []
            found += [worker for worker in result["workers"] if worker["projection"]["outcome"] == IN_PROGRESS]
            if not result["page"]["hasMore"]:
                break
            cursor = ["--cursor", result["page"]["nextCursor"]]
        lanes = self.lanes(run or result["scope"]["run"])
        return [Subject(f"dispatch:{worker['dispatchId']}", WORKER, lanes.get(worker["dispatchId"], worker["dispatchId"]), dispatch=worker["dispatchId"]) for worker in found]

    def subjects(self, entry: dict) -> list[Subject]:
        found = [Subject(root, COORDINATOR, COORDINATOR, root)] if (root := entry.get("root_terminal")) else []
        for name, desk in sorted((entry.get("desks") or {}).items()):
            found.append(Subject(desk["terminal"], SUPERVISOR if desk.get("supervisor") else DESK, name, desk["terminal"], desk.get("environment") or ""))
        return found + self.workers(entry)

    def observe(self, subject: Subject) -> Seen:
        """One terminal's state; a reply Orca shaped in a way this reader does not know makes it unknown and never ends the poll."""
        try:
            return self.read(subject)
        except (KeyError, TypeError, AttributeError) as failure:
            return Seen(subject, UNKNOWN, f"unreadable Orca reply ({type(failure).__name__}: {failure})")

    def read(self, subject: Subject) -> Seen:
        if subject.failure:
            return Seen(subject, *subject.failure)
        if subject.dispatch:
            shown = self.orca("orchestration", "worker-show", "--dispatch", subject.dispatch)
            if not shown.get("ok"):
                return Seen(subject, *unseen(shown["error"]))
            result = shown["result"]
            if not (terminal := (result.get("terminal") or {}).get("handle")):
                return Seen(subject, UNKNOWN, "worker-show names no terminal")
            subject = Subject(subject.key, subject.role, subject.name, terminal, (result.get("server") or {}).get("name") or "", subject.dispatch)
            wait = result["observation"].get("agentWait")
        else:
            shown = self.orca("terminal", "show", "--terminal", subject.terminal, environment=subject.environment)
            if not shown.get("ok"):
                return Seen(subject, *unseen(shown["error"]))
            wait = shown["result"]["terminal"].get("agentWait")
        since = (wait or {}).get("since")
        if subject.role == WORKER and not subject.environment and not wait:
            return Seen(subject, CLEAR)
        read = self.orca("terminal", "read", "--terminal", subject.terminal, "--screen", environment=subject.environment)
        if not read.get("ok"):
            return Seen(subject, *unseen(read["error"]), since)
        screen = read["result"]["terminal"]
        if screen.get("source") != "screen":
            return Seen(subject, UNKNOWN, f"no rendered screen (source {screen.get('source', 'absent')})", since)
        if found := prompt_screen.dialog(screen):
            return Seen(subject, found.kind, found.excerpt, since)
        if not wait:
            return Seen(subject, CLEAR)
        if prompt_screen.composer([line.rstrip() for line in screen.get("tail") or []]) is not None:
            return Seen(subject, STALE, f"agentWait via {wait.get('source', 'unknown')} is set and the input box is back", since)
        return Seen(subject, UNKNOWN, f"agentWait via {wait.get('source', 'unknown')} is set and the screen shows neither a known dialog nor an input box", since)

    def post(self, entry: dict, kind: str, to: str | None, name: str, text: str, resolves: int | None = None) -> int | None:
        argv = ["cci", "post", "--drive", drive.cci_drive(entry), "--lane", LANE, "--kind", kind, "--topic", f"prompt:{name}", "--json"]
        argv += ["--to", to] if to else []
        argv += ["--resolves", str(resolves)] if resolves else []
        done = self.shell.run([*argv, "--text", text if len(text) <= CCI_TEXT else text[: CCI_TEXT - 1] + "…"])
        try:
            return json.loads(done.out)["seq"] if done.code == 0 else None
        except (json.JSONDecodeError, KeyError):
            return None

    def close(self, entry: dict, row: dict) -> None:
        """Resolve each record the row's prompt raised; a record cci refused to resolve stays for the next poll."""
        left = {}
        for who, seq in (row.get("alerts") or {}).items():
            if self.post(entry, "unblock", None, row["name"], f"CLEARED {row['name']} ({row['role']}) terminal={row['terminal']}: the prompt left the screen", seq) is None:
                left[who] = seq
        row["alerts"] = left
        if not left:
            row.pop("episode", None)
            row.pop("told_at", None)

    def prompt_text(self, seen: Seen, tail: str) -> str:
        subject = seen.subject
        head = f"PROMPT {subject.name} ({subject.role}) terminal={subject.terminal} waits on {WAITS_ON[seen.state]}: "
        room = CCI_TEXT - len(head) - len(tail) - 2
        return f"{head}{seen.detail if len(seen.detail) <= room else '…' + seen.detail[1 - room :]}. {tail}"

    def alert(self, entry: dict, row: dict, seen: Seen, supervisor: Seen | None) -> None:
        alerts, subject, read = row["alerts"], seen.subject, read_command(seen.subject)
        if subject.role != COORDINATOR:
            if ROOT not in alerts and (seq := self.post(entry, "blocker", ROOT, subject.name, self.prompt_text(seen, f"Read: {read}. The watch answers nothing."))):
                alerts[ROOT] = seq
            return
        if OWNER in alerts:
            return
        ready = supervisor is not None and supervisor.state in (CLEAR, STALE)
        if SUPERVISOR not in alerts and ready:
            tail = f"Read: {read}. Answer only within what the owner already authorized, else ask the owner."
            if seq := self.post(entry, "blocker", supervisor.subject.name, subject.name, self.prompt_text(seen, tail)):
                alerts[SUPERVISOR], row["told_at"] = seq, stamp(self.shell.now())
            return
        if SUPERVISOR in alerts and ready and self.shell.now() - parse_stamp(row["told_at"]) < SUPERVISOR_WAIT:
            return
        if supervisor is None:
            why = "no supervisor desk is registered"
        elif not ready:
            why = f"supervisor {supervisor.subject.name} is {supervisor.state}"
        else:
            why = f"supervisor {supervisor.subject.name} was told {int(SUPERVISOR_WAIT.total_seconds() // 60)}m ago and it is still open"
        if seq := self.post(entry, "ask", OWNER, subject.name, self.prompt_text(seen, f"It froze the coordinator; {why}. Answer it in that terminal.")):
            alerts[OWNER] = seq

    def settle(self, entry: dict, row: dict, seen: Seen, supervisor: Seen | None) -> None:
        subject, moment = seen.subject, stamp(self.shell.now())
        if row.get("state") != seen.state:
            row["state_at"] = moment
        row |= {"role": subject.role, "name": subject.name, "terminal": subject.terminal, "environment": subject.environment, "state": seen.state, "detail": seen.detail, "since": seen.since}
        row.setdefault("alerts", {})
        if seen.state in UNSEEN:
            row["unseen"] = row.get("unseen", 0) + 1 if row.get("unseen_state") == seen.state else 1
            row["unseen_state"] = seen.state
            if row["unseen"] >= UNSEEN_POLLS and row.get("reported") != seen.state:
                text = f"{seen.state.upper()} {subject.name} ({subject.role}) terminal={subject.terminal or 'unresolved'}{f' environment={subject.environment}' if subject.environment else ''}: {seen.detail}; unread for {row['unseen']} polls, so whether it waits on a prompt is not known"
                if self.post(entry, "report", ROOT, subject.name, text):
                    row["reported"] = seen.state
            return
        row["unseen"] = 0
        row.pop("unseen_state", None)
        row.pop("reported", None)
        if seen.state not in PROMPTS:
            self.close(entry, row)
            return
        episode = hashlib.sha1(f"{seen.state}|{seen.since}|{seen.detail}".encode()).hexdigest()[:12]
        if row.get("episode") != episode:
            self.close(entry, row)
            if row["alerts"]:
                return
            row["episode"] = episode
        self.alert(entry, row, seen, supervisor)

    def poll(self) -> bool:
        if not (entry := drive.find(self.ident, None)):
            return False
        path = watch_dir(entry) / STATE_FILE
        rows = json.loads(path.read_text())["subjects"] if path.exists() else {}
        subjects = self.subjects(entry)
        with ThreadPoolExecutor(SHOW_WORKERS) as pool:
            seen = list(pool.map(self.observe, subjects))
        supervisor = next((found for found in seen if found.subject.role == SUPERVISOR), None)
        fresh = {}
        for found in seen:
            fresh[found.subject.key] = row = rows.pop(found.subject.key, {})
            self.settle(entry, row, found, supervisor)
        for key, row in rows.items():
            self.close(entry, row)
            if row["alerts"]:
                fresh[key] = row
        drive.write_atomic(path, {"drive": self.ident, "at": stamp(self.shell.now()), "pid": os.getpid(), "subjects": fresh})
        return True


def down_row(entry: dict, detail: str) -> dict:
    detail = f"{detail}; start it with `{SCRIPT} start --drive {entry['drive']}`"
    return {"name": entry["drive"], "role": WATCH, "state": DOWN, "detail": detail, "line": f"PROMPT-WATCH-DOWN {entry['drive']}: {detail}"}


def view(entry: dict, moment: datetime) -> dict:
    """The last poll as a count line and one row per terminal that is not clear, led by a `down` row when the watch stopped polling."""
    path = watch_dir(entry) / STATE_FILE
    if not path.exists():
        return {"drive": entry["drive"], "header": f"prompt-watch {entry['drive']} has never polled", "rows": [down_row(entry, "it has never polled")]}
    state = json.loads(path.read_text())
    age = moment - parse_stamp(state["at"])
    found = sorted(state["subjects"].values(), key=lambda row: (row["state"], row["name"]))
    counts = ", ".join(f"{sum(row['state'] == name for row in found)} {name}" for name in (*PROMPTS, STALE, UNKNOWN, UNREACHABLE, CLEAR))
    rows = [down_row(entry, f"last poll {int(age.total_seconds() // 60)}m ago, so the states below are old")] if age > DOWN_AFTER else []
    for row in found:
        if row["state"] == CLEAR:
            continue
        minutes = int((moment - parse_stamp(row["state_at"])).total_seconds() // 60)
        where = f"terminal={row['terminal'] or 'unresolved'}" + (f" environment={row['environment']}" if row["environment"] else "")
        label = f"WAITING-ON-PROMPT {row['name']} {row['role']} {row['state']}" if row["state"] in PROMPTS else f"PROMPT-{row['state'].upper()} {row['name']} {row['role']}"
        rows.append({"name": row["name"], "role": row["role"], "state": row["state"], "detail": row["detail"], "line": f"{label} {minutes}m {where}: {row['detail']}"})
    return {"drive": entry["drive"], "header": f"prompt-watch {entry['drive']} polled {int(age.total_seconds())}s ago: {counts}", "rows": rows}


def named(args: argparse.Namespace) -> dict | None:
    if args.drive:
        return drive.find(args.drive, None)
    return next((entry for entry in drive.drives() if drive.cci_drive(entry) == args.cci_drive), None)


def cmd_show(args: argparse.Namespace, shell: Shell) -> int:
    if not (entry := named(args)):
        print("{}" if args.json else f"no registered drive named {args.drive or args.cci_drive}")
        return 0
    shown = view(entry, shell.now())
    print(json.dumps(shown) if args.json else "\n".join([shown["header"], *(row["line"] for row in shown["rows"])]))
    return 0


def cmd_run(args: argparse.Namespace, shell: Shell) -> int:
    if not (entry := drive.find(args.drive, None)):
        raise SystemExit(f"no drive {args.drive} in {drive.drives_dir()}")
    watch_dir(entry).mkdir(parents=True, exist_ok=True)
    with (watch_dir(entry) / LOCK_FILE).open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"prompt-watch {args.drive}: another watch holds {lock.name}")
            return 0
        watch = Watch(shell, args.drive)
        if args.once:
            watch.poll()
            return 0
        while True:
            try:
                if not watch.poll():
                    break
            except Exception:
                traceback.print_exc()
            shell.sleep(POLL_SECONDS)
    print(f"prompt-watch {args.drive}: the drive ended")
    return 0


def cmd_start(args: argparse.Namespace, shell: Shell) -> int:
    if not (entry := drive.find(args.drive, None)):
        raise SystemExit(f"no drive {args.drive} in {drive.drives_dir()}")
    if args.root_terminal and entry.get("root_terminal") != args.root_terminal:
        drive.save(entry | {"root_terminal": args.root_terminal})
    log = watch_dir(entry) / LOG_FILE
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as sink:
        child = subprocess.Popen([sys.executable, str(SCRIPT), "run", "--drive", args.drive], stdin=subprocess.DEVNULL, stdout=sink, stderr=sink, start_new_session=True)
    print(f"prompt-watch {args.drive} pid {child.pid} log {log}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="prompt_watch.py", description=__doc__.splitlines()[0])
    verbs = parser.add_subparsers(dest="verb", required=True)
    start = verbs.add_parser("start", help="detach a watch for the drive; a second one exits at once")
    start.add_argument("--drive", required=True)
    start.add_argument("--root-terminal", metavar="HANDLE", help="record the coordinator's Orca terminal in the registry first")
    start.set_defaults(handler=cmd_start)
    run = verbs.add_parser("run", help="poll in the foreground until the drive ends")
    run.add_argument("--drive", required=True)
    run.add_argument("--once", action="store_true")
    run.set_defaults(handler=cmd_run)
    show = verbs.add_parser("show", help="print the last poll")
    which = show.add_mutually_exclusive_group(required=True)
    which.add_argument("--drive")
    which.add_argument("--cci-drive", metavar="NAME")
    show.add_argument("--json", action="store_true")
    show.set_defaults(handler=cmd_show)
    return parser


def main(argv: list[str] | None = None, shell: Shell | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args, shell or Shell())


if __name__ == "__main__":
    sys.exit(main())
