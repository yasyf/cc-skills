#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
"""The prompt watch: one detached reader per drive that finds every terminal stopped at a prompt and tells whoever can act.

    prompt_watch.py start --drive ID [--root-terminal HANDLE]
    prompt_watch.py run   --drive ID [--once]
    prompt_watch.py show  (--drive ID | --cci-drive NAME) [--json]
    prompt_watch.py answer (--dispatch ID | --terminal HANDLE [--environment NAME]) --by NAME [--episode KEY] (yes | no --instead TEXT)

STDLIB ONLY. Every 30 seconds `run` reads the drive's registry entry and polls its terminals:
the coordinator's (`root_terminal`), each standing desk `drive.py desk` registered, and every
in-progress worker of the drive's Orca Run, local or remote, from `worker-list --include-remote`.
Orca's `agentWait` only says where to look. The rendered screen decides, and each terminal lands in exactly one state:

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

A confirmed prompt's screen is saved owner-only under `<state dir>/prompt-watch/screens/`, as is a
screen the watch could not classify. A record, a wake, and `show` name that file, the dialog's
class, its title line, the time left on its auto-deny countdown, and the read command; none carries
the rest of the dialog's text. A record over cci's 400 characters
is clipped and its whole line rides as `--path` from `<state dir>/prompt-watch/notices/`.

One cci record per prompt, from lane `prompt-watch`, keyed by the terminal, the dialog's class,
and every row of the dialog but its auto-deny countdown. Orca rewrites `agentWait.since` every few
seconds while one dialog stays open, and the countdown ticks on every screen refresh, so neither keys anything:
a worker's or desk's prompt is a `blocker` to `root`, and one `report` to `root` naming that blocker
when the prompt is still open ten minutes later. A question on the coordinator's terminal
is the coordinator asking the owner there, so it raises no record. The coordinator cannot be
woken by its own approval, so its approval is a `blocker` to the registered supervisor desk
while that desk's own terminal reads clear or stale. In a drive started with `--slack-cards`, it is a
`needs_you` card in the owner's DM, posted with `cc-slack dm-status` into the status thread of the
drive's newest root session, when no supervisor is registered, when the supervisor is itself at a prompt, unknown, or
unreachable, and when the prompt is still open five minutes after the supervisor was told.
A supervisor that holds a foreground wait is never idle, so it keeps
`cci watch --drive <cci drive> --to <its lane> --for 0` in that wait and the record ends it,
as the root's Monitor does for `root`. A terminal unknown or unreachable for three polls in a row is one `report` to `desk-runner`,
never to root, whose window each record lands in, and three polls in a row that read it again are one `READABLE` report to the same lane. A shorter read posts nothing, so a terminal
or `worker-list` that flaps under load raises no second record for the same unread state.
When every watched terminal reads `runtime_unavailable` or `timeout`, the Orca app itself is down: no terminal
reports alone, and the `orca (runtime)` row reports once after three polls only when the last answered `worker-list`
held a worker in progress, since only a worker can wait on a prompt nobody sees. With none, the outage posts nothing.
When a prompt leaves the screen, or its terminal leaves the watch, an `unblock` record resolves
each record it raised, and an `info` card in the same thread says the owner's prompt cleared.
While `worker-list` fails, no worker is closed: each keeps its open prompt and reads unknown or
unreachable until the list answers again.

The supervisor's record comes with one wake: the record's line typed into the supervisor's own
terminal with `orca terminal send --enter`, once per prompt, and only when Orca reads that
terminal `tui-idle` and `prompt_screen.input_state` reads its screen `idle`, Claude Code's or
Codex's. A `busy` or `waiting` supervisor gets no line, the row says which, and each poll looks
again. The row keeps what the send reached: `turn_started` when Orca saw the turn start,
`input_accepted` when it only took the line. A line is never typed twice, and a send Orca
refuses, by an error or by `accepted: false`, is the owner's card at once.

An approval's record names the `answer` command that settles it. `answer` is run by whoever
decides, never by the watch: it reads the terminal's screen, refuses unless an approval dialog
is open there (and is the one `--episode` names, when given), types the number of the dialog's
first option that reads Yes or Allow, or No or Deny, as raw keys with no Enter, since Orca
refuses a text-plus-Enter prompt into a dialog with `agent_prompt_blocked`, and reads the
screen again until that dialog is gone. A denied agent ends its turn and Orca refuses every
prompt to it until one is typed, so a `no` takes `--instead`, typed and submitted as raw keys
once the dialog closes. The text is typed only once the screen shows the agent's
empty input box. It then posts one `status` message to the Run mailbox of the worker that owns
the terminal, or of the Run the terminal coordinates, naming who chose which option, the
dialog's title, and its episode, which names the watch's saved screen; the dialog's text stays
out of the mail. It finds that Run before reading the screen, and refuses before typing when
there is none.

The watch answers no prompt and types into no terminal that waits on one. It never stops,
restarts, releases, closes, or signals anything. `run` holds `<state dir>/prompt-watch/lock` and writes its own script
path there, so a second `run` of the same script exits at once. A `run` from another script, as
after a plugin update, locks `<state dir>/prompt-watch/successor` and waits on the watch's lock;
the running watch exits after the poll that finds `successor` locked, and the newer one takes
over with no process signalled. One `run` waits at a time, and one that dies leaves no lock
behind. `run` ends when the drive's registry file is gone. A poll that raises is
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
import re
import subprocess
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from long_running import cci
from long_running import drive
import prompt_screen

SCRIPT = Path(__file__).resolve()
LANE = "prompt-watch"
RUNNER = "desk-runner"
ROOT = "root"
OWNER = "owner"
SLACK_CARDS = "slack_cards"
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
RUNTIME = "runtime"
RUNTIME_DOWN = frozenset({"runtime_unavailable", "timeout"})
WAITS_ON = {prompt_screen.APPROVAL: "an approval dialog", prompt_screen.QUESTION: "a question dialog"}
REACHED = {"turn_started": "its turn started", "input_accepted": "Orca accepted it and saw no turn start"}
UNREACHABLE_CODES = frozenset({"remote_runtime_unavailable"})
UNPARSEABLE = "unparseable"
REFUSED = "input_refused"
TIMED_OUT = 124
IN_PROGRESS = "in_progress"
POLL_SECONDS = 30
ORCA_SECONDS = 20
SHOW_WORKERS = 8
UNSEEN_POLLS = 3
SUPERVISOR_WAIT = timedelta(minutes=5)
STILL_OPEN_AFTER = timedelta(minutes=10)
IDLE_MS = 1000
SUBMIT_SECONDS = 5
DOWN_AFTER = timedelta(minutes=2)
CCI_TEXT = 400
RECEIPTS = Path(".claude/scratch/orca-launch")
WATCH_DIR = Path("prompt-watch")
STATE_FILE = "state.json"
SCREENS = "screens"
NOTICES = "notices"
OWNER_ONLY = 0o600
LOCK_FILE = "lock"
SUCCESSOR_FILE = "successor"
LOG_FILE = "watch.log"
TERMINAL_ENV = "ORCA_TERMINAL_HANDLE"
OPTION = re.compile(r"^[❯›]?\s*(\d+)\.\s+(.+)$")
CHOICES = {"yes": re.compile(r"^(?:Yes|Allow)\b"), "no": re.compile(r"^(?:No|Deny)\b")}
SETTLE_SECONDS = 5
KEY_GAP_SECONDS = 1


@dataclass
class Done:
    code: int
    out: str
    err: str


class Shell:
    """The single side-effect boundary: every subprocess and the clock pass through here."""

    def run(self, argv: list[str], env: dict[str, str] | None = None) -> Done:
        try:
            proc = cci.installed(argv, lambda: subprocess.run(argv, capture_output=True, text=True, timeout=ORCA_SECONDS, env={**os.environ, **env} if env else None))
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
    failure: dict | None = None


@dataclass(frozen=True)
class Seen:
    subject: Subject
    state: str
    detail: str = ""
    since: int | None = None
    asked: str = ""
    screen: dict | None = None
    title: str = ""
    countdown: str = ""
    code: str = ""


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


def unread(subject: Subject, error: dict, since: int | None = None) -> Seen:
    return Seen(subject, *unseen(error), since, code=error["code"])


def where(row: dict) -> str:
    return f"terminal={row['terminal'] or 'unresolved'}" + (f" environment={row['environment']}" if row["environment"] else "")


def supervisor_note(row: dict) -> str:
    if wake := row.get("wake"):
        return f"; supervisor got one line and {REACHED[wake['reached']]}" if "reached" in wake else f"; the supervisor's wake failed with {wake['error']}"
    return f"; supervisor is {row['supervisor_input']}" if "supervisor_input" in row else ""


def episode_of(seen: Seen) -> str:
    return hashlib.sha1(f"{seen.subject.key}|{seen.state}|{seen.asked}".encode()).hexdigest()[:12]


def answer_command(seen: Seen, by: str) -> str:
    subject = seen.subject
    target = f"--dispatch {subject.dispatch}" if subject.dispatch else f"--terminal {subject.terminal}" + (f" --environment {subject.environment}" if subject.environment else "")
    return f"prompt_watch.py answer {target} --episode {episode_of(seen)} --by {by} yes|no"


def how_to_answer(seen: Seen, by: str) -> str:
    return f"Answer: {answer_command(seen, by)}." if seen.state == prompt_screen.APPROVAL else "The watch answers nothing."


def option(asked: str, choice: str) -> tuple[str, str] | None:
    """The number and label of the first option answering `choice`, read only from the dialog's closing option list.

    That list starts at the last row numbered 1, so numbered lines in the command or text above it never count.
    """
    numbered = [found.groups() for row in asked.split(" / ") if (found := OPTION.match(row))]
    start = max((index for index, (number, _) in enumerate(numbered) if number == "1"), default=len(numbered))
    return next(((number, label) for number, label in numbered[start:] if CHOICES[choice].match(label)), None)


def receipt_lane(receipt: Path) -> dict[str, str] | None:
    try:
        document = json.loads(receipt.read_text())
    except json.JSONDecodeError:
        return None
    try:
        return {document["result"]["dispatchId"]: receipt.stem}
    except (KeyError, TypeError):
        return {}


class Watch:
    def __init__(self, shell: Shell, ident: str):
        self.shell = shell
        self.ident = ident
        self.receipts: dict[tuple[Path, int, int, int, int, int], dict[str, str]] = {}

    def orca(self, *argv: str, environment: str = "", env: dict[str, str] | None = None) -> dict:
        done = self.shell.run(["orca", *argv, *(["--environment", environment] if environment else []), "--json"], env)
        try:
            return json.loads(done.out)
        except json.JSONDecodeError:
            return {"ok": False, "error": {"code": "timeout" if done.code == TIMED_OUT else UNPARSEABLE, "message": (done.out or done.err).strip()}}

    def lanes(self, run: str) -> dict[str, str]:
        found = {}
        for receipt in sorted((Path.home() / RECEIPTS / run).glob("*.json")):
            info = receipt.stat()
            key = (receipt, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            named = self.receipts[key] if key in self.receipts else receipt_lane(receipt)
            if named is not None:
                found[key] = named
        self.receipts = found
        return {dispatch: lane for named in found.values() for dispatch, lane in named.items()}

    def workers(self, entry: dict) -> list[Subject]:
        """Every in-progress worker of the drive's Run, or of the Run bound to the coordinator's terminal when the registry names none."""
        run, root = entry.get("orca_run"), entry.get("root_terminal")
        if not run and not root:
            return []
        scope, env = (["--run", run], None) if run else ([], {TERMINAL_ENV: root})
        found: list[dict] = []
        cursor: list[str] = []
        while True:
            listed = self.orca("orchestration", "worker-list", *scope, "--include-remote", *cursor, env=env)
            if not listed.get("ok"):
                return [Subject(WORKERS, WORKERS, "worker-list", failure=listed["error"])]
            result = listed["result"]
            if not run and result["scope"]["source"] != "bound":
                return []
            found += [worker for worker in result["workers"] if worker["projection"]["outcome"] == IN_PROGRESS]
            if not result["page"]["hasMore"]:
                break
            cursor = ["--cursor", result["page"]["nextCursor"]]
        if not found:
            return []
        lanes = self.lanes(run or result["scope"]["run"])
        return [Subject(f"dispatch:{worker['dispatchId']}", WORKER, lanes.get(worker["dispatchId"], worker["dispatchId"]), dispatch=worker["dispatchId"]) for worker in found]

    def subjects(self, entry: dict) -> list[Subject]:
        found = [Subject(root, COORDINATOR, COORDINATOR, root)] if (root := entry.get("root_terminal")) else []
        for name, desk in sorted((entry.get("desks") or {}).items()):
            found.append(Subject(desk["terminal"], SUPERVISOR if desk.get("supervisor") else DESK, name, desk["terminal"], desk.get("environment") or ""))
        return found + self.workers(entry)

    def observe(self, subject: Subject, always: bool = False) -> Seen:
        """One terminal's state; a reply Orca shaped in a way this reader does not know makes it unknown and never ends the poll."""
        try:
            return self.read(subject, always)
        except (KeyError, TypeError, AttributeError) as failure:
            return Seen(subject, UNKNOWN, f"unreadable Orca reply ({type(failure).__name__}: {failure})")

    def read(self, subject: Subject, always: bool = False) -> Seen:
        if subject.failure:
            return unread(subject, subject.failure)
        if subject.dispatch:
            shown = self.orca("orchestration", "worker-show", "--dispatch", subject.dispatch)
            if not shown.get("ok"):
                return unread(subject, shown["error"])
            result = shown["result"]
            if not (terminal := (result.get("terminal") or {}).get("handle")):
                return Seen(subject, UNKNOWN, "worker-show names no terminal")
            subject = Subject(subject.key, subject.role, subject.name, terminal, (result.get("server") or {}).get("name") or "", subject.dispatch)
            wait = result["observation"].get("agentWait")
        else:
            shown = self.orca("terminal", "show", "--terminal", subject.terminal, environment=subject.environment)
            if not shown.get("ok"):
                return unread(subject, shown["error"])
            wait = shown["result"]["terminal"].get("agentWait")
        since = (wait or {}).get("since")
        if subject.role == WORKER and not subject.environment and not wait and not always:
            return Seen(subject, CLEAR)
        read = self.orca("terminal", "read", "--terminal", subject.terminal, "--screen", environment=subject.environment)
        if not read.get("ok"):
            return unread(subject, read["error"], since)
        screen = read["result"]["terminal"]
        if screen.get("source") != "screen":
            return Seen(subject, UNKNOWN, f"no rendered screen (source {screen.get('source', 'absent')})", since)
        if found := prompt_screen.dialog(screen):
            return Seen(subject, found.kind, "", since, found.asked, screen, found.title, found.countdown)
        if not wait:
            return Seen(subject, CLEAR)
        if prompt_screen.composer([line.rstrip() for line in screen.get("tail") or []]) is not None:
            return Seen(subject, STALE, f"agentWait via {wait.get('source', 'unknown')} is set and the input box is back", since)
        return Seen(subject, UNKNOWN, f"agentWait via {wait.get('source', 'unknown')} is set and the screen shows neither a known dialog nor an input box", since, screen=screen)

    def capture(self, entry: dict, name: str, seen: Seen) -> str:
        """Save the screen a state was read from, owner-only, under the drive's state directory; no record, wake, or view carries its text."""
        path = watch_dir(entry) / SCREENS / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        subject = seen.subject
        saved = {"at": stamp(self.shell.now()), "role": subject.role, "name": subject.name, "terminal": subject.terminal, "environment": subject.environment, "state": seen.state, "since": seen.since, "asked": seen.asked, "tail": seen.screen.get("tail") or []}
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, OWNER_ONLY), "w") as out:
            out.write(json.dumps(saved, indent=2, ensure_ascii=False) + "\n")
        return str(path)

    def post(self, entry: dict, kind: str, to: str | None, name: str, text: str, resolves: int | None = None) -> int | None:
        argv = ["cci", "post", "--drive", drive.cci_drive(entry), "--lane", LANE, "--kind", kind, "--topic", f"prompt:{name}", "--json"]
        argv += ["--to", to] if to else []
        argv += ["--resolves-seq", str(resolves)] if resolves else []
        if len(text) > CCI_TEXT:
            body = watch_dir(entry) / NOTICES / f"{hashlib.sha1(text.encode()).hexdigest()[:12]}.txt"
            body.parent.mkdir(parents=True, exist_ok=True)
            body.write_text(text + "\n")
            argv += ["--path", str(body)]
            text = text[: CCI_TEXT - 1] + "…"
        done = self.shell.run([*argv, "--text", text])
        try:
            return json.loads(done.out)["seq"] if done.code == 0 else None
        except (json.JSONDecodeError, KeyError):
            return None

    def card(self, entry: dict, status: str, title: str, body: str) -> bool:
        card = {"status": status, "title": title, "body": body, "drive": drive.cci_drive(entry)}
        return self.shell.run(["cc-slack", "dm-status", "--session", entry["sessions"][-1], "--card", json.dumps(card)]).code == 0

    def close(self, entry: dict, row: dict, why: str = "the prompt left the screen") -> None:
        """Resolve each record and card the row's prompt raised; one cci or cc-slack refused stays for the next poll."""
        left = {}
        for who, seq in (row.get("alerts") or {}).items():
            cleared = f"CLEARED {row['name']} ({row['role']}) terminal={row['terminal']}: {why}"
            if who == OWNER:
                resolved = self.card(entry, "info", f"The {row['name']} terminal no longer waits on a prompt", cleared)
            else:
                resolved = self.post(entry, "unblock", None, row["name"], cleared, seq) is not None
            if not resolved:
                left[who] = seq
        row["alerts"] = left
        if not left:
            for key in ("episode", "told_at", "reminded", "wake", "capture", "supervisor_input"):
                row.pop(key, None)

    def wake(self, supervisor: Subject, text: str) -> dict:
        """Type one line into the supervisor's own terminal, only when Orca reads it `tui-idle` and its screen reads idle.

        Returns `blocked` with what the screen showed instead, or the send's outcome: `reached` is
        `turn_started` only when Orca saw the turn start, `input_accepted` when it only took the line.
        """
        where = {"environment": supervisor.environment}
        waited = self.orca("terminal", "wait", "--terminal", supervisor.terminal, "--for", "tui-idle", "--timeout-ms", str(IDLE_MS), **where)
        read = self.orca("terminal", "read", "--terminal", supervisor.terminal, "--screen", **where)
        shows = (prompt_screen.input_state(read["result"]["terminal"]) or UNKNOWN) if read.get("ok") else unseen(read["error"])[0]
        if shows != prompt_screen.IDLE or not (waited.get("ok") and waited["result"]["wait"].get("satisfied")):
            return {"blocked": prompt_screen.BUSY if shows == prompt_screen.IDLE else shows}
        sent = self.orca("terminal", "send", "--terminal", supervisor.terminal, "--text", text, "--enter", "--wait-submit", str(SUBMIT_SECONDS), **where)
        at = stamp(self.shell.now())
        if not sent.get("ok"):
            return {"at": at, "error": unseen(sent["error"])[1]}
        send = sent["result"]["send"]
        if not send.get("accepted"):
            return {"at": at, "error": f"{REFUSED}: {send.get('refusedReason') or 'Orca gave no reason'}"}
        prompt = send.get("prompt") or {}
        stages = prompt.get("stages") or []
        return {"at": at, "request": prompt.get("requestId", ""), "stages": stages, "reached": "turn_started" if "turn_started" in stages else "input_accepted"}

    def prompt_text(self, row: dict, seen: Seen, tail: str) -> str:
        subject = seen.subject
        deny = f"; auto-denies in {seen.countdown}" if seen.countdown else ""
        return f"PROMPT {subject.name} ({subject.role}) waits on {WAITS_ON[seen.state]} ({seen.title}{deny}). {tail} Screen: {row['capture']}. Read: {read_command(subject)}."

    def alert(self, entry: dict, row: dict, seen: Seen, supervisor: Seen | None) -> None:
        alerts, subject = row["alerts"], seen.subject
        if subject.role != COORDINATOR:
            if ROOT not in alerts and (seq := self.post(entry, "blocker", ROOT, subject.name, self.prompt_text(row, seen, how_to_answer(seen, ROOT)))):
                alerts[ROOT], row["told_at"] = seq, stamp(self.shell.now())
            told = parse_stamp(row.setdefault("told_at", stamp(self.shell.now())))
            if ROOT in alerts and "reminded" not in row and self.shell.now() - told >= STILL_OPEN_AFTER:
                minutes = int(STILL_OPEN_AFTER.total_seconds() // 60)
                if seq := self.post(entry, "report", ROOT, subject.name, self.prompt_text(row, seen, f"Still open {minutes}m after #{alerts[ROOT]}. {how_to_answer(seen, ROOT)}")):
                    row["reminded"] = seq
            return
        if OWNER in alerts or seen.state == prompt_screen.QUESTION:
            return
        ready = supervisor is not None and supervisor.state in (CLEAR, STALE)
        by = supervisor.subject.name if supervisor else SUPERVISOR
        text = self.prompt_text(row, seen, f"Answer only within what the owner already authorized, else ask the owner. {how_to_answer(seen, by)}")
        if SUPERVISOR not in alerts and ready and (seq := self.post(entry, "blocker", supervisor.subject.name, subject.name, text)):
            alerts[SUPERVISOR], row["told_at"] = seq, stamp(self.shell.now())
        if SUPERVISOR not in alerts and ready:
            return
        if SUPERVISOR in alerts and ready and "wake" not in row:
            sent = self.wake(supervisor.subject, text)
            if "blocked" in sent:
                row["supervisor_input"] = sent["blocked"]
            else:
                row["wake"] = sent
                row.pop("supervisor_input", None)
        failed = (row.get("wake") or {}).get("error")
        if SUPERVISOR in alerts and ready and not failed and self.shell.now() - parse_stamp(row["told_at"]) < SUPERVISOR_WAIT:
            return
        minutes = int(SUPERVISOR_WAIT.total_seconds() // 60)
        if supervisor is None:
            why = "no supervisor desk is registered"
        elif not ready:
            why = f"supervisor {supervisor.subject.name} is {supervisor.state}"
        elif failed:
            why = f"the wake to supervisor {supervisor.subject.name} failed with {failed}"
        elif "wake" not in row:
            why = f"supervisor {supervisor.subject.name} stayed {row['supervisor_input']} for {minutes}m"
        else:
            why = f"supervisor {supervisor.subject.name} got the line {minutes}m ago, {REACHED[row['wake']['reached']]}, and it is still open"
        text = self.prompt_text(row, seen, f"It froze the coordinator; {why}. Answer it in that terminal.")
        if entry.get(SLACK_CARDS) and self.card(entry, "needs_you", f"The {subject.name} terminal waits on {WAITS_ON[seen.state]}", text):
            alerts[OWNER] = row["episode"]

    def settle(self, entry: dict, row: dict, seen: Seen, supervisor: Seen | None, quiet: bool = False) -> None:
        subject, moment = seen.subject, stamp(self.shell.now())
        if row.get("state") != seen.state:
            row["state_at"] = moment
        row |= {"role": subject.role, "name": subject.name, "terminal": subject.terminal, "environment": subject.environment, "state": seen.state, "detail": seen.detail, "since": seen.since}
        row.setdefault("alerts", {})
        if seen.state in UNSEEN:
            row.pop("readable", None)
            row["unseen"] = row.get("unseen", 0) + 1 if row.get("unseen_state") == seen.state else 1
            row["unseen_state"] = seen.state
            if seen.screen is not None:
                if row["unseen"] == 1:
                    row["unread_capture"] = self.capture(entry, f"unknown-{hashlib.sha1(subject.key.encode()).hexdigest()[:12]}", seen)
                row["detail"] = f"{seen.detail}; screen saved at {row['unread_capture']}"
            if row["unseen"] >= UNSEEN_POLLS and row.get("reported") != seen.state and not quiet:
                text = f"{seen.state.upper()} {subject.name} ({subject.role}) {where(row)}: {row['detail']}; unread for {row['unseen']} polls, so whether it waits on a prompt is not known"
                if self.post(entry, "report", RUNNER, subject.name, text):
                    row["reported"] = seen.state
            return
        row["unseen"] = 0
        for key in ("unseen_state", "unread_capture"):
            row.pop(key, None)
        if "reported" in row:
            row["readable"] = row.get("readable", 0) + 1
            if row["readable"] >= UNSEEN_POLLS:
                text = f"READABLE {subject.name} ({subject.role}) {where(row)}: read for {row['readable']} polls in a row since the watch reported it {row['reported']}"
                if self.post(entry, "report", RUNNER, subject.name, text):
                    for key in ("reported", "readable"):
                        row.pop(key)
        if seen.state not in PROMPTS:
            self.close(entry, row)
            return
        episode = episode_of(seen)
        if row.get("episode") != episode:
            self.close(entry, row)
            if row["alerts"]:
                return
            row["episode"] = episode
        if "capture" not in row:
            row["capture"] = self.capture(entry, episode, seen)
        self.alert(entry, row, seen, supervisor)
        row["detail"] = f"screen saved at {row['capture']}{supervisor_note(row)}"

    def unverified(self, row: dict, unlisted: Seen) -> dict:
        """A worker nobody could list keeps its open prompt and its records; only its state says it went unread."""
        if row["state"] in UNSEEN:
            return row
        return row | {"state": unlisted.state, "state_at": stamp(self.shell.now()), "detail": f"last read {row['state']}; worker-list failed with {unlisted.detail}"}

    def poll(self) -> bool:
        if not (entry := drive.find(self.ident, None)):
            return False
        path = watch_dir(entry) / STATE_FILE
        rows = json.loads(path.read_text())["subjects"] if path.exists() else {}
        listed = sorted(row["name"] for row in rows.values() if row["role"] == WORKER)
        subjects = self.subjects(entry)
        with ThreadPoolExecutor(SHOW_WORKERS) as pool:
            seen = list(pool.map(self.observe, subjects))
        down = sorted({found.code for found in seen}) if seen and all(found.code in RUNTIME_DOWN for found in seen) else []
        supervisor = next((found for found in seen if found.subject.role == SUPERVISOR), None)
        unlisted = next((found for found in seen if found.subject.role == WORKERS), None)
        fresh = {}
        for found in seen:
            fresh[found.subject.key] = row = rows.pop(found.subject.key, {})
            self.settle(entry, row, found, supervisor, quiet=bool(down))
        runtime, orca = rows.pop(RUNTIME, {}), Subject(RUNTIME, RUNTIME, "orca")
        if down and listed:
            detail = f"Orca answers no watched terminal ({', '.join(down)}); {', '.join(listed)} were in progress at the last worker-list and may wait on a prompt"
            self.settle(entry, runtime, Seen(orca, UNKNOWN, detail), supervisor)
            fresh[RUNTIME] = runtime
        elif "reported" in runtime:
            self.settle(entry, runtime, Seen(orca, CLEAR), supervisor)
            if "reported" in runtime:
                fresh[RUNTIME] = runtime
        for key, row in rows.items():
            if unlisted and row["role"] == WORKER:
                fresh[key] = self.unverified(row, unlisted)
                continue
            if row["role"] == WORKERS and "reported" in row:
                self.settle(entry, row, Seen(Subject(key, WORKERS, row["name"]), CLEAR), supervisor)
                if "reported" in row:
                    fresh[key] = row
                continue
            self.close(entry, row, "the terminal left the watch")
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
        label = f"WAITING-ON-PROMPT {row['name']} {row['role']} {row['state']}" if row["state"] in PROMPTS else f"PROMPT-{row['state'].upper()} {row['name']} {row['role']}"
        rows.append({"name": row["name"], "role": row["role"], "state": row["state"], "detail": row["detail"], "line": f"{label} {minutes}m {where(row)}: {row['detail']}"})
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


def run_of(watch: Watch, subject: Subject) -> tuple[str, str] | None:
    """The Run whose mailbox records an answer and the dispatch it names: the worker's own, else the Run bound to the terminal."""
    if subject.dispatch:
        return watch.orca("orchestration", "worker-show", "--dispatch", subject.dispatch)["result"]["dispatch"]["runId"], subject.dispatch
    cursor: list[str] = []
    while (listed := watch.orca("orchestration", "worker-list", "--include-remote", *cursor, env={TERMINAL_ENV: subject.terminal})).get("ok"):
        result = listed["result"]
        if worker := next((worker for worker in result["workers"] if worker["agentTerminalHandle"] == subject.terminal), None):
            return worker["runId"], worker["dispatchId"]
        if result["scope"]["source"] == "bound":
            return result["scope"]["run"], ""
        if not result["page"]["hasMore"]:
            return None
        cursor = ["--cursor", result["page"]["nextCursor"]]
    return None


def typed(watch: Watch, subject: Subject, *keys: str) -> str:
    """Type each string as raw keys a second apart, outside Orca's prompt path; Orca's refusal, or '' when it took them all."""
    for index, text in enumerate(keys):
        if index:
            watch.shell.sleep(KEY_GAP_SECONDS)
        sent = watch.orca("terminal", "send", "--terminal", subject.terminal, "--text", text, environment=subject.environment)
        if not sent.get("ok") or not sent["result"]["send"].get("accepted"):
            return json.dumps(sent.get("error") or sent["result"]["send"])
    return ""


def idle(watch: Watch, subject: Subject) -> bool:
    read = watch.orca("terminal", "read", "--terminal", subject.terminal, "--screen", environment=subject.environment)
    return read.get("ok") and prompt_screen.input_state(read["result"]["terminal"]) == prompt_screen.IDLE


def cmd_answer(args: argparse.Namespace, shell: Shell) -> int:
    if (args.choice == "no") != bool(args.instead):
        raise SystemExit("--instead goes with no and only with no: a denied agent ends its turn, and Orca refuses every prompt to it until one is typed")
    watch = Watch(shell, "")
    asked_of = Subject(f"dispatch:{args.dispatch}", WORKER, args.dispatch, dispatch=args.dispatch) if args.dispatch else Subject(args.terminal, DESK, args.terminal, args.terminal, args.environment or "")
    if not (found := run_of(watch, asked_of)):
        raise SystemExit(f"terminal {args.terminal} is no worker and coordinates no Run, so no Run's mail could record the answer")
    run, dispatch = found
    seen = watch.observe(asked_of, always=True)
    subject, episode = seen.subject, episode_of(seen)
    if seen.state != prompt_screen.APPROVAL:
        raise SystemExit(f"{subject.name} shows no approval dialog: it reads {seen.state}" + (f" ({seen.detail})" if seen.detail else ""))
    if args.episode and args.episode != episode:
        raise SystemExit(f"{subject.name} shows episode {episode}, not {args.episode}: {seen.title}")
    if not (picked := option(seen.asked, args.choice)):
        raise SystemExit(f"no numbered option of {subject.name}'s dialog answers {args.choice}: {seen.asked}")
    if refused := typed(watch, subject, picked[0]):
        raise SystemExit(f"Orca refused the keys for {subject.name}: {refused}")
    after = seen
    for _ in range(SETTLE_SECONDS):
        shell.sleep(1)
        if (after := watch.observe(subject, always=True)).state in (CLEAR, STALE):
            break
    cleared = after.state in (CLEAR, STALE)
    outcome = "the dialog closed" if cleared else f"after the keys the terminal reads {after.state}" + (f" ({after.title})" if after.title else "")
    told = not args.instead
    if cleared and args.instead:
        if not idle(watch, subject):
            outcome += "; its input box is not empty and idle, so nothing was typed instead"
        elif refused := typed(watch, subject, args.instead, "\r"):
            outcome += f"; Orca refused the text to do instead: {refused}"
        else:
            told, outcome = True, outcome + f'; told it instead: "{args.instead}"'
    chose = f'{args.by} chose "{picked[0]}. {picked[1]}" for {subject.name} on {subject.terminal}'
    audit = watch.orca(
        "orchestration", "send", "--to", f"run:{run}", *(["--dispatch-id", dispatch] if dispatch else []),
        "--type", "status", "--subject", f"{args.by} answered {args.choice}: {seen.title}", "--body", f"{chose}; {outcome}. Prompt episode {episode}.",
    )
    recorded = f"recorded in run:{run}" if audit.get("ok") else f"NOT recorded in run:{run}: {json.dumps(audit.get('error'))}"
    print(f"ANSWERED {chose}; {outcome}; {recorded}")
    return 0 if cleared and told and audit.get("ok") else 1


def free(lock) -> bool:
    """Take a file's exclusive lock without waiting; False while another open file holds it."""
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def cmd_run(args: argparse.Namespace, shell: Shell) -> int:
    if not (entry := drive.find(args.drive, None)):
        raise SystemExit(f"no drive {args.drive} in {drive.drives_dir()}")
    watch_dir(entry).mkdir(parents=True, exist_ok=True)
    successor = watch_dir(entry) / SUCCESSOR_FILE
    with (watch_dir(entry) / LOCK_FILE).open("a+") as lock:
        if not free(lock):
            lock.seek(0)
            holder = lock.read().strip()
            with successor.open("a+") as queue:
                if args.once or holder == str(SCRIPT) or not free(queue):
                    print(f"prompt-watch {args.drive}: another watch holds or awaits {lock.name}")
                    return 0
                print(f"prompt-watch {args.drive}: waiting to take over from {holder or 'a watch that names no script'}", flush=True)
                fcntl.flock(lock, fcntl.LOCK_EX)
        lock.seek(0)
        lock.truncate()
        lock.write(str(SCRIPT))
        lock.flush()
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
            with successor.open("a+") as queue:
                if not free(queue):
                    print(f"prompt-watch {args.drive}: handing over to the watch that holds {successor}")
                    return 0
            shell.sleep(POLL_SECONDS)
    print(f"prompt-watch {args.drive}: the drive ended")
    return 0


def cmd_start(args: argparse.Namespace, shell: Shell) -> int:
    if not (entry := drive.find(args.drive, None)):
        raise SystemExit(f"no drive {args.drive} in {drive.drives_dir()}")
    changed = ({"root_terminal": args.root_terminal} if args.root_terminal else {}) | ({SLACK_CARDS: True} if args.slack_cards else {})
    if any(entry.get(key) != value for key, value in changed.items()):
        drive.save(entry | changed)
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
    start.add_argument("--slack-cards", action="store_true", help="also card the owner's DM when the coordinator's own prompt has no supervisor to answer it")
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
    answer = verbs.add_parser("answer", help="choose Yes or No on the approval dialog open in one terminal, and record who chose it in the Run's mail")
    target = answer.add_mutually_exclusive_group(required=True)
    target.add_argument("--dispatch", metavar="ID", help="the worker's dispatch id, ctx_...")
    target.add_argument("--terminal", metavar="HANDLE")
    answer.add_argument("--environment", metavar="NAME", help="the paired Orca server holding --terminal")
    answer.add_argument("--by", required=True, metavar="NAME", help="who decided, as the Run's mail should name them")
    answer.add_argument("--episode", metavar="KEY", help="the prompt's key from the watch's record; refuse when the dialog on screen is another")
    answer.add_argument("--instead", metavar="TEXT", help="with no: what the agent should do instead, typed and submitted once the dialog closes")
    answer.add_argument("choice", choices=sorted(CHOICES))
    answer.set_defaults(handler=cmd_answer)
    return parser


def main(argv: list[str] | None = None, shell: Shell | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args, shell or Shell())


if __name__ == "__main__":
    drive.run_installed(__file__)
    sys.exit(main())
