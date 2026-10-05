#!/usr/bin/env python3
"""The scripted desk: root commands become durable actions addressed to a lane's current Orca dispatch, the Run inbox is read by sequence, ready bottom prefixes enqueue, and deadlines are checked, all without a model turn.

    desk-runner.py relay  --config PATH --key KEY --lane LANE --text TEXT [--reply-to MSG] [--deadline-minutes N]
    desk-runner.py launch --config PATH --key KEY --lane LANE --model M --effort E --brief PATH [--owner-directed]
    desk-runner.py policy --config PATH --key KEY --landing prefix|whole --revision REV --source TEXT [--supersedes REV]
    desk-runner.py run    --config PATH --desk orca|landing [--once]
    desk-runner.py rebind --config PATH
    desk-runner.py show   --config PATH

STDLIB ONLY. Every action lives in the store `actions.py` owns: one container per lane,
`desk-lane-<lane>`, whose owner is the lane's current Orca dispatch, plus `desk-landing`
and `desk-runner`. An action id is the command's key, so a command submitted twice
yields one action. A relaunch offers the lane's container to the new dispatch, and that
dispatch's first `started` reply takes it at the next owner generation, so a stale
dispatch can never start or finish an action.

A relay is accepted, then started and completed by the lane itself: it replies
`started <key>` and `done <key>: <result>` on the action's thread. The send is its own
`send:` action, delivery evidence only. A send whose response is lost becomes
`unverifiable` and is settled from Orca's request receipt or the recipient's mailbox,
never resent blindly.

`run --desk orca` relays, launches, consumes the Run mailbox, sweeps stale mail and
prompts, and checks relay deadlines. It also tails the drive's desk inbox file and turns
each new `orca-desk: relay to <lane>[, <lane>…][ and <lane>]: <text>` line into one relay
per lane, a reply to the lane's latest open question when it has one, logged once as
`RELAYED` or `RELAY-FAILED`. Each new `orca-desk: launch <lane> [NOW] <model> <effort> brief=<absolute path>`
line is the `launch` command under its key, `NOW` meaning `--owner-directed`, refused as
`LAUNCH-FAILED` while the lane has a live dispatch or a launch in flight; every verified
launch logs `LAUNCHED` with its dispatch and terminal. Orca lets only the terminal bound to the Run call
worker-start, and it names the caller by the ORCA_TERMINAL_HANDLE this process inherited.
The runner records that terminal, its pane, the Run's coordinator and generation, and how
the binding was obtained, at start and before each pass that launches. Started from a
terminal that is not the Run's coordinator, or from no Orca terminal, it refuses to start
and prints the rebind command; a binding lost mid-run holds every launch and escalates
UNBOUND once. `rebind` runs `orca orchestration run-use` from the current terminal and
records it; `show` prints the binding first. A sol or `--owner-directed` launch starts whatever
the load; any other launch waits while the 1-minute load is above the core count, for
at most `deadlines.load_hold_minutes`, then fails into the escalations file and the Run
mailbox. `run --desk landing` gates and enqueues ready
prefixes under the accepted landing policy, verifies landings by squash, and routes
blockers and restacks. A worker's question goes to a Sonnet-low judge with the lane's
brief, which answers it or escalates it with options. Escalations append one line each
to the config's escalations file, once per cause, with Pacific times and no zone label;
a quiet pass writes nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import actions

SCRIPTS = Path(__file__).resolve().parent
PACIFIC = ZoneInfo("America/Los_Angeles")
LANE_PREFIX = "desk-lane-"
LANDING = "desk-landing"
RUNNER = "desk-runner"
UNLAUNCHED = "unlaunched"
BINDING = "binding"
STACK_ENQUEUE = ".agents/skills/submit-pr/scripts/stack-enqueue"
ENQUEUE_OUTCOMES = {0: "enqueued", 1: "blocked", 2: "unsettled", 3: "stranded"}
INACTIVE = frozenset({"completed", "failed"})
RECLAIM_NAMED = 10
MODELS = re.compile(r"opus|sonnet|fable|astra|codex|sol|claude-[\w.-]+|gpt-[\w.-]+")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
QUEUED = frozenset({"QUEUED_TO_MERGE", "WAITING_TO_MERGE", "REBASING", "MERGED"})
RETRYABLE = frozenset({"blocked", "superseded"})
SWEEP_EVERY = timedelta(minutes=5)
ORPHANED_SEND = timedelta(minutes=2)
ORPHANED_JUDGE = timedelta(minutes=5)
SEND_ATTEMPTS = 3
INBOX_PAGE_SIZE = 100
UNPARSEABLE = "unparseable"
VERDICT_LINE = re.compile(r"^#(?P<pr>\d+) (?P<verdict>[A-Z]+) (?P<sha>[0-9a-f]{0,10}) ?(?P<detail>.*)$", re.MULTILINE)
WOULD_ENQUEUE = re.compile(r"^would enqueue ((?:#\d+ ?)+) in one call$", re.MULTILINE)
STATUS_LINE = re.compile(r"^#(?P<pr>\d+) (?P<status>[A-Z_]+) ", re.MULTILINE)
ACK = re.compile(r"^(?P<verb>started|done)\b[: ]*(?P<rest>.*)$", re.DOTALL)
HELD_PR = re.compile(r"#(\d+)")
HELD_LANE = re.compile(r"\blane:(\S+)")
LAUNCHED = re.compile(r"^(?P<lane>\S+) (?P<how>ready|unsupervised) task=\S+ dispatch=(?P<dispatch>\S+) terminal=(?P<terminal>\S+) worktree=\S+$", re.MULTILINE)
LANDING_POLICIES = ("prefix", "whole")
RELAY_GRAMMAR = "orca-desk: relay to <lane>[, <lane>…][ and <lane>]: <text>"
LAUNCH_GRAMMAR = "orca-desk: launch <lane> [NOW] <model> <effort> brief=<absolute path>"
ALERT_GRAMMAR = "orca-desk: alert <slug> <link> :: <what fired>"
HOLD_GRAMMAR = "orca-desk: hold <slug> owner=<lane> :: <what is held, and on what>"
UNHOLD_GRAMMAR = "orca-desk: unhold <slug>"
INBOX_DIRECTIVE = re.compile(r"^(?:-\s+)?(?:(?P<key>R\d+)\s+(?:\([^)]*\)\s+)?)?orca-desk: (?P<verb>relay|launch|alert|hold|unhold)\b(?P<rest>.*)$")
RELAY_TO = re.compile(r"^ to (?P<lanes>[\w.-]+(?:(?:, (?:and )?| and )[\w.-]+)*): (?P<text>\S.*)$")
LANE_LIST = re.compile(r", (?:and )?| and ")
LAUNCH_SPEC = re.compile(r"^ (?P<lane>[\w.-]+)(?P<now> NOW)? (?P<model>\S+) (?P<effort>\S+) brief=(?P<brief>/\S+)$")
ALERT_SPEC = re.compile(r"^ (?P<slug>[a-z0-9][a-z0-9.-]*) (?P<link>\S+) :: (?P<what>\S.*)$")
ALERT_TEMPLATE = SCRIPTS.parent / "reference" / "alert-fix-brief.md"
MONITOR_ID = re.compile(r"(?:monitors/|\bmonitor |\bDatadog )(\d{4,})")
INCIDENT_ANNOTATION = re.compile(r"^ccx:.*\bincident=([\w.-]+)", re.MULTILINE)
HOLD_SPEC = re.compile(r"^ (?P<slug>[a-z0-9][a-z0-9.-]*) owner=(?P<owner>[\w.-]+) :: (?P<what>\S.*)$")
UNHOLD_SPEC = re.compile(r"^ (?P<slug>[a-z0-9][a-z0-9.-]*)$")
JUDGE_SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {"verdict": {"enum": ["answer", "escalate"]}, "text": {"type": "string"}},
        "required": ["verdict", "text"],
    }
)
JUDGE_PROMPT = """You answer one routine question from an Orca worker for its coordinator.
Answer only when the lane's brief below settles the question; quote the brief's rule in the answer.
When the brief does not settle it, or the answer changes scope, touches production, or needs the owner,
return verdict "escalate" with the question restated in one line and 2-4 concrete options.

<brief lane="{lane}">
{brief}
</brief>

<question id="{msg}" type="{type}">
{subject}
{body}
</question>
"""


@dataclass
class Done:
    code: int
    out: str
    err: str


class Shell:
    """The single side-effect boundary: every subprocess, the clock, and the load average pass through here."""

    def run(self, argv: list[str], stdin: str | None = None, env: dict[str, str] | None = None) -> Done:
        proc = subprocess.run(argv, input=stdin, capture_output=True, text=True, env={**os.environ, **env} if env else None)
        return Done(proc.returncode, proc.stdout, proc.stderr)

    def spawn(self, argv: list[str], out: Path, env: dict[str, str]) -> subprocess.Popen:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w") as sink:
            return subprocess.Popen(argv, stdout=sink, stderr=subprocess.STDOUT, env={**os.environ, **env}, start_new_session=True)

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def load(self) -> float:
        return os.getloadavg()[0]

    def cores(self) -> int:
        return os.cpu_count() or 1

    def env(self, name: str) -> str:
        return os.environ.get(name, "")


def brief_incidents(brief: Path) -> set[str]:
    return set(INCIDENT_ANNOTATION.findall(brief.read_text())) if brief.is_file() else set()


def pacific(moment: datetime) -> str:
    return moment.astimezone(PACIFIC).strftime("%H:%M")


def lane_container(lane: str) -> str:
    return f"{LANE_PREFIX}{lane}"


@dataclass
class Config:
    source: Path
    store: Path | None
    escalations: Path
    view: Path
    desk_inbox: Path
    run: str
    receipts: Path
    briefs_repo: str
    briefs_log: str
    launch_env: dict[str, str]
    gc: str | None
    start_minutes: int
    launch_minutes: int
    load_hold_minutes: int
    enqueue_minutes: int
    hold_minutes: int
    judge_model: str
    landing: dict | None
    alert_facts: Path | None

    @classmethod
    def load(cls, path: Path) -> Config:
        raw = json.loads(path.read_text())
        orca = raw["orca"]
        deadlines = raw.get("deadlines", {})
        alert = raw.get("alert", {})
        escalations = Path(raw["escalations"]).expanduser()
        return cls(
            source=path.expanduser().resolve(),
            store=Path(raw["store"]).expanduser() if raw.get("store") else None,
            escalations=escalations,
            view=Path(raw["view"]).expanduser(),
            desk_inbox=Path(orca["desk_inbox"]).expanduser() if orca.get("desk_inbox") else escalations.parent / "orca-desk.md",
            run=orca["run"],
            receipts=Path(orca["receipts"]).expanduser(),
            briefs_repo=str(Path(orca["briefs"]["repo"]).expanduser()),
            briefs_log=orca["briefs"]["log"],
            launch_env=orca.get("launch_env", {}),
            gc=orca.get("gc"),
            start_minutes=deadlines.get("start_minutes", 10),
            launch_minutes=deadlines.get("launch_minutes", 15),
            load_hold_minutes=deadlines.get("load_hold_minutes", 5),
            enqueue_minutes=deadlines.get("enqueue_minutes", 15),
            hold_minutes=deadlines.get("hold_minutes", 15),
            judge_model=raw.get("judge_model", "claude-sonnet-5-5"),
            landing=raw.get("landing"),
            alert_facts=Path(alert["facts"]).expanduser() if alert.get("facts") else None,
        )


class Book:
    """Generation-checked edits over the actions store; a change raising ValueError writes nothing."""

    def __init__(self, store: actions.Store, shell: Shell):
        self.store = store
        self.shell = shell

    def stamp(self) -> str:
        return actions.stamp(self.shell.now())

    def ensure(self, container: str, owner: str) -> actions.Incident:
        return self.store.create(actions.Incident(container, owner, 0, self.stamp(), {}))

    def load(self, container: str) -> actions.Incident:
        return self.store.load(container)

    def containers(self, prefix: str) -> list[str]:
        return [name for name in self.store.ids() if name.startswith(prefix)]

    def edit(self, container: str, change: Callable[[actions.Incident], object]) -> object:
        while True:
            generation = self.store.load(container).owner_generation
            try:
                with self.store.owned(container, generation) as incident:
                    return change(incident)
            except actions.StaleGeneration:
                continue

    def attempt(self, container: str, change: Callable[[actions.Incident], object]) -> bool:
        try:
            self.edit(container, change)
        except (ValueError, actions.UnknownAction):
            return False
        return True

    def as_owner(self, container: str, owner: str, change: Callable[[actions.Incident], object]) -> bool:
        """Apply `change` only while `owner` holds the container at the generation read; a transfer in between refuses it."""
        incident = self.store.load(container)
        if incident.owner != owner:
            return False
        try:
            with self.store.owned(container, incident.owner_generation) as live:
                change(live)
        except (actions.StaleGeneration, ValueError, actions.UnknownAction):
            return False
        return True

    def accept(self, container: str, action_id: str, kind: str, target: str, authority: str, deadline: datetime | None) -> tuple[actions.Action, bool]:
        def change(incident: actions.Incident) -> tuple[actions.Action, bool]:
            created = action_id not in incident.actions
            action = incident.accept(action_id, kind, target, authority, self.shell.now())
            if created and deadline:
                action.deadline = actions.stamp(deadline)
            return action, created

        return self.edit(container, change)

    def actions(self, container: str, kind: str | None = None, status: str | None = None) -> list[actions.Action]:
        return [action for action in self.store.load(container).actions.values() if (kind is None or action.kind == kind) and (status is None or action.status == status)]


@dataclass
class Dispatch:
    lane: str
    id: str
    terminal: str
    status: str
    live: bool
    wait: dict | None


class Orca:
    def __init__(self, shell: Shell, config: Config):
        self.shell = shell
        self.config = config

    def call(self, *argv: str) -> dict:
        done = self.shell.run(["orca", *argv, "--json"])
        try:
            return json.loads(done.out)
        except json.JSONDecodeError:
            return {"ok": False, "error": {"code": UNPARSEABLE, "message": (done.out or done.err).strip()[:300]}}

    def receipt(self, lane: str) -> str:
        """The lane's current dispatch, or empty while no launch has written a whole receipt."""
        path = self.config.receipts / f"{lane}.json"
        try:
            return json.loads(path.read_text())["result"]["dispatchId"]
        except (FileNotFoundError, json.JSONDecodeError, KeyError, TypeError):
            return ""

    def terminal(self, lane: str) -> str:
        path = self.config.receipts / f"{lane}.terminal"
        return path.read_text().strip() if path.is_file() else ""

    def lanes(self) -> list[str]:
        return sorted(path.stem for path in self.config.receipts.glob("*.terminal"))

    def lane_of(self, handle: str) -> str:
        return next((lane for lane in self.lanes() if self.terminal(lane) == handle), handle)

    def show(self, lane: str) -> Dispatch | None:
        dispatch = self.receipt(lane)
        shown = self.call("orchestration", "worker-show", "--dispatch", dispatch) if dispatch else {}
        if not shown.get("ok"):
            return None
        result = shown["result"]
        observation = result.get("observation") or {}
        return Dispatch(
            lane=lane,
            id=dispatch,
            terminal=(result.get("terminal") or {}).get("handle") or self.terminal(lane),
            status=result["dispatch"]["status"],
            live=observation.get("status") == "live",
            wait=observation.get("agentWait"),
        )

    def thread_message(self, terminal: str, thread: str) -> dict | None:
        listed = self.call("orchestration", "check", "--terminal", terminal, "--all")
        return next((message for message in (listed.get("result") or {}).get("messages") or [] if message.get("thread_id") == thread), None)

    def request_state(self, request: str) -> str:
        return (self.call("orchestration", "request-show", "--request", request).get("result") or {}).get("state", "absent")

    def wake(self, terminal: str, text: str) -> None:
        self.shell.run(["orca", "terminal", "send", "--terminal", terminal, "--text", text, "--enter", "--json"])


def request_id(reply: dict) -> str:
    return ((reply.get("error") or {}).get("data") or {}).get("orchestrationRequestId", "")


def thread_of(container: str, key: str) -> str:
    return f"{container}/{key}"


class Runner:
    def __init__(self, shell: Shell, config: Config, store: actions.Store):
        self.shell = shell
        self.config = config
        self.book = Book(store, shell)
        self.orca = Orca(shell, config)
        self.launching: dict[str, subprocess.Popen] = {}
        self.pass_binding: dict | None = None
        self.book.ensure(RUNNER, RUNNER)

    def now(self) -> datetime:
        return self.shell.now()

    def escalate(self, key: str, kind: str, about: str, text: str) -> None:
        """Record one escalation per key; `flush` appends it to the escalations file."""
        self.record(f"escalation:{key}", f"{kind} {key} {about}: {' '.join(text.split())}")

    def record(self, action_id: str, line: str) -> None:
        self.book.accept(RUNNER, action_id, "escalation", line, "runner", None)

    def flush(self) -> None:
        for action in self.book.actions(RUNNER, kind="escalation", status="accepted"):
            self.config.escalations.parent.mkdir(parents=True, exist_ok=True)
            with self.config.escalations.open("a") as out:
                out.write(f"{pacific(actions.parse_stamp(action.accepted_at))} {action.target}\n")
            self.book.attempt(RUNNER, lambda incident, key=action.action_id: (incident.start(key, self.now()), incident.complete(key, {"at": self.book.stamp()})))

    def binding(self, via: str = "") -> dict:
        """Ask Orca whether this process's terminal coordinates the Run, and record the answer, with how it was obtained, in the runner's state."""
        terminal = self.shell.env("ORCA_TERMINAL_HANDLE")
        prior = self.book.load(RUNNER).facts.get(BINDING) or {}
        current = self.orca.call("orchestration", "run-current") if terminal else {}
        held = ((self.orca.call("orchestration", "run-show", "--id", self.config.run).get("result") or {}).get("run")) or {}
        coordinates = ((current.get("result") or {}).get("run") or {}).get("id") or ""
        state = {
            "terminal": terminal,
            "pane": self.shell.env("ORCA_PANE_KEY"),
            "run": self.config.run,
            "bound": bool(terminal) and coordinates == self.config.run,
            "coordinator": held.get("coordinator_handle") or "",
            "generation": held.get("consumer_generation"),
            "at": self.book.stamp(),
        }
        if not terminal:
            state["why"] = "no ORCA_TERMINAL_HANDLE: the runner was not started from an Orca terminal"
        elif not current.get("ok"):
            error = current.get("error") or {}
            state["why"] = f"orca orchestration run-current failed: {error.get('code')}: {error.get('message')}"
        elif not state["bound"]:
            state["why"] = f"terminal {terminal} coordinates {coordinates or 'no Run'}; Orca binds {self.config.run} to {state['coordinator'] or 'no terminal'} at generation {state['generation']}"
        if via:
            state["via"] = via
        elif state["bound"] and prior.get("bound") and (prior.get("terminal"), prior.get("generation")) == (terminal, state["generation"]):
            state["via"] = prior.get("via", "")
        elif state["bound"]:
            state["via"] = f"ORCA_TERMINAL_HANDLE inherited from the shell that started the runner, already bound at generation {state['generation']}"
        self.book.edit(RUNNER, lambda incident: incident.facts.update({BINDING: state}))
        return state

    def rebind_command(self) -> str:
        return f"desk-runner.py rebind --config {self.config.source}"

    def unbound(self, state: dict) -> str:
        return f"desk-runner cannot start workers on {self.config.run}: {state['why']}. From the coordinator's Orca terminal run `{self.rebind_command()}`, and start the runner from that terminal"

    def binding_line(self, state: dict | None) -> str:
        if not state:
            return f"binding: unchecked; `{self.rebind_command()}` binds this terminal"
        checked = pacific(actions.parse_stamp(state["at"]))
        if not state["bound"]:
            return f"binding: UNBOUND {state['why']}; checked {checked}; fix: `{self.rebind_command()}` from the coordinator's terminal"
        return f"binding: terminal {state['terminal']} pane {state['pane'] or 'unknown'} coordinates {state['run']} at generation {state['generation']}, via {state['via']}; checked {checked}"

    def bound_for_launch(self) -> bool:
        """Check the binding once per delivery pass that launches; an unbound runner holds its launches and escalates once per binding state."""
        if self.pass_binding is None:
            self.pass_binding = self.binding()
            state = self.pass_binding
            if not state["bound"]:
                self.escalate(f"unbound:{state['terminal']}:{state['coordinator']}:{state['generation']}", "UNBOUND", "runner", f"{self.unbound(state)}; launches wait until it is bound")
        return self.pass_binding["bound"]

    def rebind(self) -> int:
        if not self.shell.env("ORCA_TERMINAL_HANDLE"):
            print(f"desk-runner rebind: no ORCA_TERMINAL_HANDLE; run `{self.rebind_command()}` from the coordinator's Orca terminal", file=sys.stderr)
            return 3
        previous = ((self.orca.call("orchestration", "run-show", "--id", self.config.run).get("result") or {}).get("run")) or {}
        used = self.orca.call("orchestration", "run-use", "--id", self.config.run)
        if not used.get("ok"):
            error = used.get("error") or {}
            print(f"desk-runner rebind: orca orchestration run-use failed: {error.get('code')}: {error.get('message')}", file=sys.stderr)
            return 1
        state = self.binding(via=f"orca orchestration run-use by desk-runner rebind at {pacific(self.now())}, replacing {previous.get('coordinator_handle') or 'no terminal'} at generation {previous.get('consumer_generation')}")
        print(self.binding_line(state))
        return 0 if state["bound"] else 1

    def lane(self, lane: str) -> str:
        container = lane_container(lane)
        self.book.ensure(container, self.orca.receipt(lane) or UNLAUNCHED)
        return container

    def accept_relay(self, key: str, lane: str, text: str, reply_to: str, minutes: int) -> tuple[actions.Action, bool]:
        target = json.dumps({"text": text, "reply_to": reply_to})
        return self.book.accept(self.lane(lane), key, "reply" if reply_to else "relay", target, key, self.now() + timedelta(minutes=minutes))

    def read_inbox(self) -> None:
        """Act on each complete line appended to the desk inbox since the saved byte offset; a first read starts at the end, so history never replays."""
        path = self.config.desk_inbox
        fact = f"desk-inbox:{path}"
        cursor = self.book.load(RUNNER).facts.get(fact)
        if cursor is None:
            start = path.stat().st_size if path.is_file() else 0
            self.book.edit(RUNNER, lambda incident: incident.facts.update({fact: start}))
            return
        if not path.is_file():
            return
        with path.open("rb") as inbox:
            inbox.seek(cursor)
            appended = inbox.read()
        offset = cursor
        for raw in appended.splitlines(keepends=True):
            if not raw.endswith(b"\n"):
                break
            self.inbox_line(offset, raw.decode().strip())
            offset += len(raw)
            self.book.edit(RUNNER, lambda incident, at=offset: incident.facts.update({fact: at}))

    def inbox_line(self, offset: int, line: str) -> None:
        directive = INBOX_DIRECTIVE.match(line)
        if not directive:
            return
        key = directive["key"] or f"inbox@{offset}"
        verbs = {"relay": self.relay_line, "launch": self.launch_line, "alert": self.alert_line, "hold": self.hold_line, "unhold": self.unhold_line}
        verbs[directive["verb"]](offset, key, directive["rest"])

    def relay_line(self, offset: int, key: str, rest: str) -> None:
        parsed = RELAY_TO.match(rest)
        if not parsed or "; relay " in parsed["text"]:
            self.record(f"escalation:relay:{offset}", f"RELAY-FAILED {key} inbox: one relay per line, in the form `{RELAY_GRAMMAR}`; nothing was relayed")
            return
        for lane in LANE_LIST.split(parsed["lanes"]):
            self.relay_to(offset, key, lane, parsed["text"])

    def relay_to(self, offset: int, key: str, lane: str, text: str) -> None:
        """Accept what `relay` accepts for one lane: a reply to the current dispatch's latest open question, else a plain relay."""
        log = f"escalation:relay:{offset}:{lane}"
        dispatch = self.orca.show(lane)
        if not dispatch or dispatch.status in INACTIVE:
            self.record(log, f"RELAY-FAILED {key} {lane}: no live dispatch{f' ({dispatch.id} is {dispatch.status})' if dispatch else ''}")
            return
        question = self.open_question(dispatch)
        action, created = self.accept_relay(key, lane, text, question, self.config.start_minutes)
        if not created and json.loads(action.target)["text"] != text:
            self.record(log, f"RELAY-FAILED {key} {lane}: {key} already holds a different relay to {lane} ({action.status})")
            return
        self.record(log, f"RELAYED {key} {lane}: {f'reply to question {question} of' if question else 'relay to'} dispatch {dispatch.id}")

    def launch_line(self, offset: int, key: str, rest: str) -> None:
        """Accept what `launch` accepts, `NOW` meaning `--owner-directed`, once per key and only for a lane with no live dispatch and no launch in flight."""
        log = f"escalation:launch:{offset}"
        spec = LAUNCH_SPEC.match(rest)
        if not spec:
            self.record(log, f"LAUNCH-FAILED {key} inbox: one launch per line, in the form `{LAUNCH_GRAMMAR}`; nothing was launched")
            return
        lane, brief = spec["lane"], Path(spec["brief"]).resolve()
        if refusal := self.launch_refusal(key, lane, spec["model"], spec["effort"], brief):
            self.record(log, f"LAUNCH-FAILED {key} {lane}: {refusal}; nothing was launched")
            return
        self.accept_launch(key, lane, spec["model"], spec["effort"], str(brief), bool(spec["now"]))

    def alert_line(self, offset: int, key: str, rest: str) -> None:
        """Launch the alert's sol fix lane on a brief freshly attached to the briefs log from the template, or relay a repeat to the lane already on it; the root ratifies from the INCIDENT line."""
        log = f"escalation:alert:{offset}"
        spec = ALERT_SPEC.match(rest)
        if not spec:
            self.record(log, f"ALERT-FAILED {key} inbox: one alert per line, in the form `{ALERT_GRAMMAR}`; nothing was launched")
            return
        slug, link, what = spec["slug"], spec["link"], spec["what"]
        lane = f"{slug}-fix"
        dispatch = self.orca.show(lane)
        if dispatch and dispatch.status not in INACTIVE:
            self.relay_to(offset, f"{key}:again", lane, f"The alert fired again at {pacific(self.now())}: {what} {link}")
            return
        monitors = set(MONITOR_ID.findall(f"{link} {what}"))
        if monitors and (peer := self.incident_lane(monitors, lane, offset)):
            self.record(log, f"LAUNCH-SKIPPED {key} {lane}: duplicate of {peer}, already on monitor {', '.join(sorted(monitors))}")
            return
        if refusal := self.launch_refusal(key, lane, "sol", "xhigh", ALERT_TEMPLATE):
            self.record(log, f"INCIDENT {slug} again at {pacific(self.now())}: {what} {link} | {lane} not launched: {refusal}")
            return
        facts = self.config.alert_facts.read_text().strip() if self.config.alert_facts else "none recorded for this drive"
        incident = "".join(f" incident={monitor}" for monitor in sorted(monitors)[:1])
        text = ALERT_TEMPLATE.read_text().format(lane=lane, incident=incident, slug=slug, link=link, what=what, onset=pacific(self.now()), repo=self.config.briefs_repo, log=self.config.briefs_log, facts=facts)
        if failure := self.attach(f"{lane}.full.md", text, f"INCIDENT {slug}: fix brief for {lane}: {what} {link}"):
            self.record(log, f"INCIDENT {slug} {pacific(self.now())}: {what} {link} | {lane} not launched: the brief did not attach: {failure}")
            return
        brief = self.attachment(f"{lane}.full.md")
        if brief is None:
            self.record(log, f"INCIDENT {slug} {pacific(self.now())}: {what} {link} | {lane} not launched: the attached brief has no path")
            return
        self.accept_launch(key, lane, "sol", "xhigh", str(brief), True)
        self.record(log, f"INCIDENT {slug} {pacific(self.now())}: {what} {link} | fix lane {lane} launching on sol xhigh, brief {brief}; root: ratify, spawn the evidence and incident-doc lanes, fence the target, start comms in the affected account channels and #outage")

    def holds(self, slug: str) -> list[actions.Action]:
        return [action for action in self.book.actions(RUNNER, kind="hold", status="accepted") if json.loads(action.target)["slug"] == slug]

    def hold_line(self, offset: int, key: str, rest: str) -> None:
        """Start the clock on an urgent hold; `aged_holds` turns one older than `deadlines.hold_minutes` into a DECIDE line for the root."""
        spec = HOLD_SPEC.match(rest)
        if not spec:
            self.record(f"escalation:hold:{offset}", f"HOLD-FAILED {key} inbox: one hold per line, in the form `{HOLD_GRAMMAR}`; no clock started")
            return
        if self.holds(spec["slug"]):
            return
        target = json.dumps({"slug": spec["slug"], "owner": spec["owner"], "what": spec["what"]})
        self.book.accept(RUNNER, f"hold:{spec['slug']}@{self.book.stamp()}", "hold", target, key, self.now() + timedelta(minutes=self.config.hold_minutes))

    def unhold_line(self, offset: int, key: str, rest: str) -> None:
        spec = UNHOLD_SPEC.match(rest)
        if not spec:
            self.record(f"escalation:unhold:{offset}", f"HOLD-FAILED {key} inbox: lift a hold in the form `{UNHOLD_GRAMMAR}`")
            return
        for action in self.holds(spec["slug"]):
            self.book.edit(RUNNER, lambda incident, held=action.action_id: incident.verify(held, {"at": self.book.stamp(), "by": key}))

    def aged_holds(self) -> None:
        moment = self.now()
        for action in self.book.actions(RUNNER, kind="hold", status="accepted"):
            if not action.overdue(moment):
                continue
            held = json.loads(action.target)
            minutes = int((moment - actions.parse_stamp(action.accepted_at)).total_seconds() // 60)
            self.record(
                f"escalation:aged:{action.action_id}",
                f"DECIDE hold:{held['slug']} {held['owner']}: held {minutes} min: {held['what']} | the root decides it now with {held['owner']}, ahead of any open owner question on another subject",
            )

    def incident_lane(self, monitors: set[str], lane: str, offset: int) -> str | None:
        """The lane already on one of these monitors: a launch in flight or the live dispatch of a launch whose brief's `ccx: incident=` names one, or a launch line later in the desk inbox naming one that this pass has yet to read."""
        for container in self.book.containers(LANE_PREFIX):
            other = container.removeprefix(LANE_PREFIX)
            launches = [action for action in self.book.actions(container, kind="launch") if brief_incidents(Path(json.loads(action.target)["brief"])) & monitors]
            if other == lane or not launches:
                continue
            dispatch = self.orca.show(other)
            live = dispatch.id if dispatch and dispatch.status not in INACTIVE else None
            if any(action.status in ("accepted", "started") or (live and action.dispatch_id == live) for action in launches):
                return other
        inbox = self.config.desk_inbox
        position = 0
        for raw in inbox.read_bytes().splitlines(keepends=True) if inbox.is_file() else []:
            start, position = position, position + len(raw)
            directive = INBOX_DIRECTIVE.match(raw.decode().strip())
            if start <= offset or not directive or directive["verb"] != "launch" or not (spec := LAUNCH_SPEC.match(directive["rest"])) or spec["lane"] == lane:
                continue
            if brief_incidents(Path(spec["brief"])) & monitors:
                return spec["lane"]
        return None

    def launch_refusal(self, key: str, lane: str, model: str, effort: str, brief: Path) -> str:
        try:
            launch_model(model)
        except argparse.ArgumentTypeError as error:
            return str(error)
        if effort not in EFFORTS:
            return f"{effort} is not an effort: {', '.join(EFFORTS)}"
        if not brief.is_file():
            return f"brief {brief} is not a file"
        container = lane_container(lane)
        if container in self.book.store.ids():
            if held := self.book.load(container).actions.get(key):
                return f"{key} already holds a {held.kind} for {lane} ({held.status})"
            if flight := next((action for action in self.book.actions(container, kind="launch") if action.status in ("accepted", "started")), None):
                return f"launch {flight.action_id} for {lane} is {flight.status}"
        dispatch = self.orca.show(lane)
        if dispatch and dispatch.status not in INACTIVE:
            return f"dispatch {dispatch.id} is {dispatch.status}; relay to it instead"
        return ""

    def open_question(self, dispatch: Dispatch) -> str:
        """The newest question the dispatch asked the Run that no message on the dispatch's terminal answers, or empty."""
        asked = (self.orca.call("orchestration", "check", "--terminal", f"run:{self.config.run}", "--all", "--types", "question").get("result") or {}).get("messages") or []
        mine = [message for message in asked if message.get("from_handle") in (f"dispatch:{dispatch.id}", dispatch.terminal)]
        if not mine:
            return ""
        received = (self.orca.call("orchestration", "check", "--terminal", dispatch.terminal, "--all").get("result") or {}).get("messages") or []
        answered = {message.get("thread_id") for message in received}
        return next((message["id"] for message in sorted(mine, key=lambda message: message["created_at"], reverse=True) if message["id"] not in answered), "")

    def accept_launch(self, key: str, lane: str, model: str, effort: str, brief: str, owner_directed: bool) -> tuple[actions.Action, bool]:
        urgent = owner_directed or model == "sol"
        target = json.dumps({"model": model, "effort": effort, "brief": brief, "prior": self.orca.receipt(lane), "urgent": urgent})
        return self.book.accept(self.lane(lane), key, "launch", target, key, self.now() + timedelta(minutes=self.config.launch_minutes))

    def landing_policy(self) -> actions.Action | None:
        self.book.ensure(LANDING, LANDING)
        verified = self.book.actions(LANDING, kind="policy", status="verified")
        superseded = {json.loads(action.target)["supersedes"] for action in verified}
        return next((action for action in verified if json.loads(action.target)["revision"] not in superseded), None)

    def accept_policy(self, key: str, rule: str, revision: str, source: str, supersedes: str) -> tuple[actions.Action, bool]:
        current = self.landing_policy()
        target = json.dumps({"rule": rule, "revision": revision, "supersedes": supersedes})
        action, created = self.book.accept(LANDING, key, "policy", target, source, None)
        if not created:
            return action, created
        if current and supersedes != json.loads(current.target)["revision"]:
            held = json.loads(current.target)
            reason = f"names {supersedes or 'no'} predecessor; the accepted landing policy is {held['rule']} at {held['revision']} ({current.authority_ref})"
            self.book.attempt(LANDING, lambda incident: incident.fail(key, reason))
            self.escalate(key, "STALE-POLICY", "landing", f"{rule} at {revision} from {source} rejected: {reason}")
        else:
            self.book.attempt(LANDING, lambda incident: incident.verify(key, {"at": self.book.stamp(), "supersedes": supersedes}))
        return self.book.load(LANDING).actions[key], created

    def transfer(self, lane: str) -> None:
        """Offer the container to the lane's receipt dispatch when it moved; ownership changes only on that dispatch's ack."""
        container = self.lane(lane)
        incident = self.book.load(container)
        dispatch = self.orca.receipt(lane)
        if dispatch and dispatch not in (incident.owner, incident.pending_owner):
            try:
                self.book.store.transfer(container, incident.owner_generation, dispatch)
            except actions.StaleGeneration:
                return

    def deliver(self) -> None:
        self.pass_binding = None
        for container in self.book.containers(LANE_PREFIX):
            lane = container.removeprefix(LANE_PREFIX)
            self.transfer(lane)
            pending = self.book.actions(container, status="accepted")
            relays = [action for action in pending if action.kind in ("relay", "reply")]
            for action in pending:
                if action.kind == "launch":
                    self.launch(container, lane, action)
            if relays and (dispatch := self.orca.show(lane)) and dispatch.status not in INACTIVE:
                for action in relays:
                    self.send(container, dispatch, action)
            self.reconcile_sends(container, lane)

    def sends(self, container: str, key: str, dispatch: str) -> list[actions.Action]:
        prefix = f"send:{key}:{dispatch}"
        return [action for action in self.book.actions(container, kind="send") if action.action_id.split("#")[0] == prefix]

    def send(self, container: str, dispatch: Dispatch, action: actions.Action) -> None:
        tries = self.sends(container, action.action_id, dispatch.id)
        if any(attempt.status != "failed" for attempt in tries):
            return
        if len(tries) >= SEND_ATTEMPTS:
            self.escalate(f"{container}/send:{action.action_id}:{dispatch.id}", "SEND-FAILED", dispatch.lane, f"{action.authority_ref} to {dispatch.id} failed {len(tries)} times: {tries[-1].reason}")
            return
        send_id = f"send:{action.action_id}:{dispatch.id}#{len(tries) + 1}"
        self.book.accept(container, send_id, "send", dispatch.id, action.action_id, None)
        if not self.book.attempt(container, lambda incident: incident.start(send_id, self.now(), dispatch_id=dispatch.id)):
            return
        self.post(container, dispatch, action, send_id, "")

    def post(self, container: str, dispatch: Dispatch, action: actions.Action, send_id: str, retry: str) -> None:
        spec = json.loads(action.target)
        thread = thread_of(container, action.action_id)
        if action.kind == "reply":
            argv = ["orchestration", "reply", "--id", spec["reply_to"], "--body", f"{spec['text']}\n\n({action.authority_ref})"]
        else:
            body = f"{spec['text']}\n\nReply on thread {thread}: subject `started {action.action_id}` before acting, then `done {action.action_id}: <result>`."
            argv = [
                "orchestration", "send", "--to", f"dispatch:{dispatch.id}", "--type", "dispatch", "--subject", f"{action.authority_ref}: act {action.action_id}",
                "--body", body, "--thread-id", thread, "--payload", json.dumps({"action": thread}),
            ]
        reply = self.orca.call(*argv, *(["--retry-request", retry] if retry else []))
        if reply.get("ok"):
            message = ((reply.get("result") or {}).get("message") or {}).get("id", "sent")
            self.book.attempt(container, lambda incident: incident.complete(send_id, {"message": message, "at": self.book.stamp()}))
            if action.kind == "reply":
                self.book.attempt(container, lambda incident: (incident.start(action.action_id, self.now()), incident.complete(action.action_id, {"message": message, "at": self.book.stamp()})))
            else:
                self.orca.wake(dispatch.terminal, f"{action.authority_ref}: read Orca message on thread {thread} now")
        elif (request := request_id(reply)) or reply["error"]["code"] == UNPARSEABLE:
            self.book.attempt(container, lambda incident: lost(incident, send_id, {"request_id": request}))
        else:
            self.book.attempt(container, lambda incident: incident.fail(send_id, json.dumps(reply.get("error"))[:300]))

    def reconcile_sends(self, container: str, lane: str) -> None:
        """Settle every send whose response was lost from Orca's request receipt or the recipient's mailbox; absent both, escalate once and never resend."""
        cutoff = self.now() - ORPHANED_SEND
        for send in self.book.actions(container, kind="send", status="started"):
            if actions.parse_stamp(send.started_at) < cutoff:
                self.book.attempt(container, lambda incident, key=send.action_id: incident.lose(key))
        for send in self.book.actions(container, kind="send", status="unverifiable"):
            action = self.book.load(container).actions[send.authority_ref]
            request = (send.response or {}).get("request_id", "")
            state = self.orca.request_state(request) if request else "absent"
            if state == "pending":
                continue
            dispatch = self.orca.show(lane)
            if state == "completed" and dispatch and dispatch.id == send.target:
                self.post(container, dispatch, action, send.action_id, request)
                continue
            terminal = dispatch.terminal if dispatch and dispatch.id == send.target else ""
            found = self.orca.thread_message(terminal, thread_of(container, action.action_id)) if terminal else None
            if found:
                self.book.attempt(container, lambda incident, key=send.action_id: incident.complete(key, {"message": found["id"], "at": found.get("created_at", self.book.stamp()), "reconciled": True}))
            else:
                self.escalate(f"{container}/{send.action_id}", "UNVERIFIABLE", lane, f"{action.authority_ref} send to {send.target} lost its response; Orca holds no receipt and no message on its thread; it was not resent")

    def launch_log(self, container: str, key: str) -> Path:
        return (self.config.store or actions.incidents_dir()).parent / "desk-runner-launches" / f"{container}-{key}.out"

    def launch(self, container: str, lane: str, action: actions.Action) -> None:
        """Start orca-launch.sh detached, so a readiness wait never holds a relay; `reap` records its printed line."""
        spec = json.loads(action.target)
        if not self.bound_for_launch():
            return
        if held := self.load_hold(action):
            if self.now() - actions.parse_stamp(action.accepted_at) >= timedelta(minutes=self.config.load_hold_minutes):
                self.expire_launch(container, lane, action, held)
            return
        if not self.book.attempt(container, lambda incident: incident.start(action.action_id, self.now(), deadline=actions.parse_stamp(action.deadline))):
            return
        argv = [str(SCRIPTS / "orca-launch.sh"), lane, spec["model"], spec["effort"], spec["brief"]]
        self.launching[f"{container}/{action.action_id}"] = self.shell.spawn(argv, self.launch_log(container, action.action_id), self.config.launch_env)

    def load_hold(self, action: actions.Action) -> str:
        """Why an accepted launch is waiting on load, or empty when it may start; sol and owner-directed launches never wait."""
        load, cores = self.shell.load(), self.shell.cores()
        if json.loads(action.target)["urgent"] or load <= cores:
            return ""
        return f"load {load:.0f} above {cores} cores"

    def expire_launch(self, container: str, lane: str, action: actions.Action, held: str) -> None:
        key = action.action_id
        text = f"{key} launch failed after {self.config.load_hold_minutes}m held, {held}; submit it under a new key with --owner-directed to start it now"
        if not self.book.attempt(container, lambda incident: incident.fail(key, text)):
            return
        self.escalate(f"{container}/{key}", "LAUNCH-HELD", lane, text)
        self.orca.call("orchestration", "send", "--to", f"run:{self.config.run}", "--run", self.config.run, "--type", "status", "--subject", f"LAUNCH-HELD {lane}", "--body", text)

    def reap(self) -> None:
        """Settle each started launch from its printed line; a launch with neither a line nor a new receipt is unverifiable, never relaunched."""
        for container in self.book.containers(LANE_PREFIX):
            lane = container.removeprefix(LANE_PREFIX)
            for action in self.book.actions(container, kind="launch", status="started"):
                process = self.launching.get(f"{container}/{action.action_id}")
                if process and process.poll() is None:
                    continue
                self.launching.pop(f"{container}/{action.action_id}", None)
                self.settle_launch(container, lane, action)

    def settle_launch(self, container: str, lane: str, action: actions.Action) -> None:
        log = self.launch_log(container, action.action_id)
        out = log.read_text().strip() if log.is_file() else ""
        launched = LAUNCHED.search(out)
        prior = json.loads(action.target)["prior"]
        receipt = self.orca.receipt(lane)
        key = action.action_id
        if launched and launched["lane"] == lane:
            proof = {"line": launched.group(0), "dispatch": launched["dispatch"], "at": self.book.stamp()}
        elif not out and receipt and receipt != prior:
            proof = {"line": f"receipt dispatch {receipt}", "dispatch": receipt, "at": self.book.stamp()}
        elif out:
            reason = next((line for line in reversed(out.splitlines()) if line.startswith(f"{lane} failed ")), " ".join(out.split())[:300])
            self.book.attempt(container, lambda incident: incident.fail(key, reason))
            self.escalate(f"{container}/{key}", "LAUNCH-FAILED", lane, reason)
            return
        else:
            self.book.attempt(container, lambda incident: incident.lose(key))
            self.escalate(f"{container}/{key}", "UNVERIFIABLE", lane, "launch outcome unknown: no launch line and no new receipt; it was not relaunched")
            return
        self.book.attempt(container, lambda incident: (incident.complete(key, proof, dispatch_id=proof["dispatch"]), incident.verify(key, proof)))
        self.record(f"escalation:{container}/{key}:launched", f"LAUNCHED {key} {lane}: dispatch {proof['dispatch']} terminal {launched['terminal'] if launched else self.orca.terminal(lane)}")
        self.transfer(lane)
        if launched and launched["how"] == "unsupervised":
            self.escalate(f"{container}/{key}", "UNSUPERVISED", lane, f"launched without Orca supervision: {proof['line']}")

    def check(self) -> bool:
        """Read the Run past the persisted cursor; False while no cursor exists, so nothing dispatches before the first read sets one."""
        key = f"inbox:{self.config.run}"
        cursor = self.book.load(RUNNER).facts.get(key)
        limit = INBOX_PAGE_SIZE
        while True:
            reply = self.orca.call("orchestration", "inbox", "--terminal", f"run:{self.config.run}", "--limit", str(limit))
            if not reply["ok"]:
                self.escalate(f"orca-inbox:{actions.stamp(self.now())[:15]}", "ORCA-INBOX", "runner", json.dumps(reply["error"]))
                return cursor is not None
            messages = reply["result"]["messages"]
            if cursor is None:
                newest = max((message["sequence"] for message in messages), default=0)
                self.book.edit(RUNNER, lambda incident: incident.facts.update({key: newest}))
                return True
            if len(messages) < limit or messages[-1]["sequence"] <= cursor + 1:
                break
            limit *= 2
        for message in reversed(messages):
            if message["sequence"] <= cursor:
                continue
            if message["type"] != "heartbeat":
                self.message(message | {"lane": self.orca.lane_of(message["from_handle"])})
            cursor = message["sequence"]
            self.book.edit(RUNNER, lambda incident: incident.facts.update({key: cursor}))
        return True

    def message(self, message: dict) -> None:
        decoded = json.loads(message.get("payload") or "{}")
        payload = decoded if isinstance(decoded, dict) else {}
        lane = message.get("lane") or self.orca.lane_of(message.get("from_handle", ""))
        sender = payload.get("dispatchId") or (self.orca.receipt(lane) if self.orca.terminal(lane) == message.get("from_handle") else "")
        subject = message.get("subject") or ""
        acked = ACK.match(subject)
        thread = message.get("thread_id") or ""
        if acked and "/" in thread:
            container, key = thread.split("/", 1)
            result = acked["rest"].removeprefix(key).lstrip(": ").strip()
            self.acknowledge(container, key, sender, acked["verb"], result or (message.get("body") or "").strip(), message["id"])
        elif message["type"] in ("question", "escalation"):
            self.judge(message, lane)
        elif message["type"] == "worker_done":
            self.escalate(message["id"], "OUTCOME", lane, f"worker_done {payload.get('outcome', '?')} dispatch={sender}: {message.get('subject', '')}")
        elif message["type"] == "status" and subject.lower().startswith(("fix-live:", "mechanism:")):
            kind = subject.split(":", 1)[0].upper()
            body = " ".join((message.get("body") or "").split())[:300]
            self.escalate(message["id"], kind, lane, f"{subject}: {body}")
        elif message["type"] in ("decision_gate", "handoff"):
            self.escalate(message["id"], message["type"].upper(), lane, f"{message.get('subject', '')}: {(message.get('body') or '')[:200]}")

    def acknowledge(self, container: str, key: str, sender: str, verb: str, text: str, message: str) -> None:
        """Apply a lane's started/done reply only from the dispatch that owns the container; a pending dispatch's first ack takes ownership first."""
        if container not in self.book.store.ids() or key not in self.book.load(container).actions:
            return
        incident = self.book.load(container)
        if sender and sender == incident.pending_owner:
            self.book.store.ack(container, sender)
        moved = verb == "started" and self.book.as_owner(container, sender, lambda live: live.start(key, self.now(), dispatch_id=sender))
        if verb == "done":
            response = {"text": text[:500], "message": message, "at": self.book.stamp()}
            moved = self.book.as_owner(container, sender, lambda live: finish(live, key, response, sender))
        if not moved and sender and sender != self.book.load(container).owner:
            self.stand_down(container, key, sender, message)

    def stand_down(self, container: str, key: str, sender: str, message: str) -> None:
        incident = self.book.load(container)
        notice = f"stand-down:{key}:{sender}"
        text = f"Do not execute {key}: dispatch {incident.owner} owns {container} at generation {incident.owner_generation}."
        action, created = self.book.accept(container, notice, "reply", json.dumps({"text": text, "reply_to": message}), notice, None)
        dispatch = self.orca.show(container.removeprefix(LANE_PREFIX))
        if created and dispatch:
            self.send(container, Dispatch(dispatch.lane, sender, dispatch.terminal, dispatch.status, dispatch.live, None), action)

    def brief_for(self, lane: str) -> Path | None:
        container = lane_container(lane)
        launched = [json.loads(action.target)["brief"] for action in self.book.actions(container, kind="launch", status="verified")] if container in self.book.store.ids() else []
        candidates = [Path(path) for path in launched[-1:]] + [self.attachment(f"{lane}.full.md"), self.attachment(f"{lane}.md")]
        return next((path for path in candidates if path and path.is_file()), None)

    def attachment(self, name: str) -> Path | None:
        done = self.shell.run(["ccn", "-R", self.config.briefs_repo, "attachment", "path", self.config.briefs_log, name])
        return Path(done.out.strip()) if done.code == 0 else None

    def attach(self, name: str, text: str, entry: str) -> str:
        with tempfile.TemporaryDirectory() as staging:
            path = Path(staging) / name
            path.write_text(text)
            done = self.shell.run(["ccn", "-R", self.config.briefs_repo, "log", "append", self.config.briefs_log, "--entry", entry, "--attach", str(path), "--replace"])
        if done.code == 0:
            return ""
        return (done.err or done.out).strip()[-300:] or f"ccn exited {done.code}"

    def judge(self, message: dict, lane: str) -> None:
        """One Sonnet-low call per question id: an answer the brief settles is replied to the question, anything else escalates with options."""
        key = f"judge:{message['id']}"
        question = f"{message.get('subject', '')}: {(message.get('body') or '')[:300]}"
        _, created = self.book.accept(RUNNER, key, "judge", json.dumps({"msg": message["id"], "question": question}), lane, None)
        if not created:
            return
        self.book.attempt(RUNNER, lambda incident: incident.start(key, self.now()))
        brief = self.brief_for(lane)
        if not brief:
            self.book.attempt(RUNNER, lambda incident: incident.complete(key, {"verdict": "escalate", "text": f"no brief file for {lane}", "at": self.book.stamp()}))
            self.resume_judges()
            return
        prompt = JUDGE_PROMPT.format(lane=lane, brief=brief.read_text(), msg=message["id"], type=message["type"], subject=message.get("subject", ""), body=message.get("body", ""))
        argv = ["claude", "-p", "--model", self.config.judge_model, "--effort", "low", "--no-session-persistence", "--strict-mcp-config", "--tools", "", "--output-format", "json", "--json-schema", JUDGE_SCHEMA]
        verdict = judge_verdict(self.shell.run(argv, stdin=prompt))
        self.book.attempt(RUNNER, lambda incident: incident.complete(key, {**verdict, "at": self.book.stamp()}))
        self.resume_judges()

    def resume_judges(self) -> None:
        """Carry every judged question to its reply or escalation, so a restart between the verdict and its follow-up loses nothing."""
        for judged in self.book.actions(RUNNER, kind="judge"):
            spec = json.loads(judged.target)
            lane = judged.authority_ref
            if judged.status == "started" and actions.parse_stamp(judged.started_at) < self.now() - ORPHANED_JUDGE:
                self.escalate(spec["msg"], "DECIDE", lane, f"{spec['question']} (the judge never returned)")
            elif judged.status == "completed" and judged.response["verdict"] == "answer":
                reply, created = self.accept_relay(f"answer:{spec['msg']}", lane, judged.response["text"], spec["msg"], self.config.start_minutes)
                if created and (dispatch := self.orca.show(lane)) and dispatch.status not in INACTIVE:
                    self.send(lane_container(lane), dispatch, reply)
            elif judged.status == "completed":
                self.escalate(spec["msg"], "DECIDE", lane, f"{spec['question']} | {judged.response['text']}")

    def sweep(self) -> None:
        """Unread mail on a live dispatch gets one wake; mail a settled dispatch never read, a prompt, or a dispatch that is not live escalates once."""
        done = self.shell.run([str(SCRIPTS / "orca-check.sh"), "--stale"], env={"ORCA_CHECK_STATE": str(self.config.receipts), "ORCA_LAUNCH_RUN": self.config.run})
        for line in done.out.splitlines():
            parts = line.split()
            if len(parts) < 5 or parts[0] != "STALE":
                continue
            lane, status, item = parts[1], parts[3], parts[4]
            if status != "unread":
                self.escalate(f"stale:{item}", "STALE-MAIL", lane, f"{item} unread by a {status} dispatch")
                continue
            _, created = self.book.accept(RUNNER, f"wake:{item}", "wake", item, lane, None)
            if created and (terminal := self.orca.terminal(lane)):
                self.orca.wake(terminal, f"unread Orca message {item}; read it now")
        hour = actions.stamp(self.now())[:13]
        named = {action.action_id for action in self.book.actions(RUNNER, kind="reclaim")}
        settled: list[Dispatch] = []
        for lane in self.orca.lanes():
            dispatch = self.orca.show(lane)
            if not dispatch:
                continue
            if dispatch.status in INACTIVE:
                if f"reclaim:{dispatch.id}" not in named:
                    settled.append(dispatch)
                continue
            if dispatch.wait:
                self.escalate(f"prompt:{dispatch.id}:{dispatch.wait.get('since', '')}", "PROMPT", lane, f"dispatch={dispatch.id} terminal={dispatch.terminal} parked on {dispatch.wait.get('reason', 'a prompt')}")
            elif not dispatch.live:
                self.escalate(f"liveness:{dispatch.id}:{hour}", "LIVENESS", lane, f"dispatch={dispatch.id} terminal={dispatch.terminal} is not live; resume it in place, never relaunch on this alone")
        if settled:
            self.reclaim(settled)

    def reclaim(self, settled: list[Dispatch]) -> None:
        named = " ".join(f"{dispatch.lane}={dispatch.id}:{dispatch.terminal}" for dispatch in settled[:RECLAIM_NAMED])
        more = f" and {len(settled) - RECLAIM_NAMED} more" if len(settled) > RECLAIM_NAMED else ""
        scope = "".join(f" --dispatch {dispatch.id}" for dispatch in settled) if len(settled) <= RECLAIM_NAMED else ""
        step = f"run {self.config.gc} --run {self.config.run}{scope}" if self.config.gc else "close each idle terminal and remove each finished worktree under R195"
        self.escalate(f"reclaim:{settled[0].id}", "RECLAIM", "runner", f"{len(settled)} settled dispatch(es) still hold their terminal: {named}{more}; {step}")
        for dispatch in settled:
            self.book.accept(RUNNER, f"reclaim:{dispatch.id}", "reclaim", dispatch.terminal, dispatch.lane, None)

    def overdue(self, container: str, about: str) -> None:
        moment = self.now()
        for action in self.book.load(container).actions.values():
            if action.kind not in ("relay", "reply", "launch", "enqueue") or not action.overdue(moment) or action.status not in ("accepted", "started"):
                continue
            if action.kind in ("relay", "reply") and action.status == "started":
                continue
            sent = [send for send in self.book.actions(container, kind="send") if send.authority_ref == action.action_id]
            missing = {
                "relay": f"delivered to {sent[-1].target}, no started reply" if sent and sent[-1].status == "completed" else "never delivered: no live dispatch" if not sent else f"send {sent[-1].status}",
                "reply": "never delivered: no live dispatch" if not sent else f"send {sent[-1].status}",
                "launch": "launch never finished" if action.status == "started" else "launch never started",
                "enqueue": "stack-enqueue never returned" if action.status == "started" else "enqueue never ran",
            }[action.kind]
            self.escalate(f"deadline:{container}/{action.action_id}:{action.status}", "DEADLINE", about, f"{action.authority_ref} {action.kind}: {missing}; accepted {pacific(actions.parse_stamp(action.accepted_at))}")

    def deadlines_orca(self) -> None:
        for container in self.book.containers(LANE_PREFIX):
            self.overdue(container, container.removeprefix(LANE_PREFIX))

    def render(self) -> str:
        lines = [f"# desk-runner {pacific(self.now())}", self.binding_line(self.book.load(RUNNER).facts.get(BINDING))]
        if policy := self.landing_policy():
            held = json.loads(policy.target)
            lines.append(f"landing policy: {held['rule']} at {held['revision']} ({policy.authority_ref})")
        for container in [*self.book.containers(LANE_PREFIX), LANDING]:
            incident = self.book.load(container)
            lines.append(f"## {container} owner {incident.owner} generation {incident.owner_generation}{f' pending {incident.pending_owner}' if incident.pending_owner else ''}")
            for action in sorted(incident.actions.values(), key=lambda action: action.accepted_at):
                if action.kind in ("send", "policy"):
                    continue
                at = [f"accepted {pacific(actions.parse_stamp(action.accepted_at))}"]
                at += [f"started {pacific(actions.parse_stamp(action.started_at))}"] if action.started_at else []
                at += [f"{action.status} {pacific(actions.parse_stamp(receipt['at']))}"] if (receipt := action.verification_receipt or action.response) and "at" in receipt else []
                status = action.status
                if action.kind == "launch" and status == "accepted" and (held := self.load_hold(action)):
                    status = f"HELD {held} for {int((self.now() - actions.parse_stamp(action.accepted_at)).total_seconds() // 60)}m"
                lines.append(f"- {action.action_id} {action.kind} [{status}] {' '.join(at)}")
        return "\n".join(lines) + "\n"

    def write_view(self) -> None:
        text = self.render()
        current = self.config.view.read_text() if self.config.view.is_file() else ""
        if current.split("\n", 1)[1:] != text.split("\n", 1)[1:]:
            self.config.view.parent.mkdir(parents=True, exist_ok=True)
            self.config.view.write_text(text)


def lost(incident: actions.Incident, key: str, response: dict) -> None:
    incident.lose(key)
    incident.action(key).response = response


def finish(incident: actions.Incident, key: str, response: dict, dispatch: str) -> None:
    if incident.action(key).status == "accepted":
        incident.start(key, actions.parse_stamp(response["at"]), dispatch_id=dispatch)
    incident.complete(key, response, dispatch_id=dispatch)


def enqueue_key(prefix: list[str], verdicts: dict) -> str:
    return "enqueue:" + ",".join(f"{pr}@{verdicts[pr]['sha'] if pr in verdicts else ''}" for pr in prefix)


def judge_verdict(done: Done) -> dict:
    if done.code != 0:
        return {"verdict": "escalate", "text": f"the judge failed: {(done.err or done.out).strip()[-200:]}"}
    payload = json.loads(done.out)
    return (payload[-1] if isinstance(payload, list) else payload)["structured_output"]


class Landing:
    """The ready-prefix workflow: gate every tracked stack read-only, enqueue each ready prefix once per set of heads, verify by squash, and route the restack above it."""

    def __init__(self, runner: Runner, config: dict):
        self.runner = runner
        self.shell = runner.shell
        self.book = runner.book
        self.repo = config["repo"]
        self.ledger = config["ledger"]
        self.checkout = Path(config["checkout"]).expanduser()
        self.holds = Path(config["holds"]).expanduser()
        self.bus = config.get("bus", "")
        self.book.ensure(LANDING, LANDING)

    def ledger_py(self, *argv: str) -> Done:
        return self.shell.run([sys.executable, str(SCRIPTS / "ledger.py"), "-C", str(self.checkout), *argv])

    def rows(self) -> dict[str, dict]:
        done = self.ledger_py("list", "--ledger", self.ledger, "--json")
        return {row["pr"]: row for row in json.loads(done.out)} if done.code == 0 else {}

    def held(self, rows: dict[str, dict]) -> list[str]:
        text = self.holds.read_text() if self.holds.is_file() else ""
        lanes = set(HELD_LANE.findall(text))
        numbers = set(HELD_PR.findall(text)) | {pr for pr, row in rows.items() if (row.get("lane") in lanes and row.get("state", "open") == "open") or row.get("rules_blocked")}
        return sorted(numbers, key=int)

    def enqueue_argv(self, tip: str, held: list[str], check: bool) -> list[str]:
        policy = self.runner.landing_policy()
        whole = ["--whole"] if policy and json.loads(policy.target)["rule"] == "whole" else []
        return [str(self.checkout / STACK_ENQUEUE), tip, *(["--check"] if check else []), *whole, *(["--hold", *held] if held else [])]

    def tips(self, rows: dict[str, dict]) -> list[str]:
        tracked = {pr: row for pr, row in rows.items() if row.get("state", "open") == "open" and (row.get("reported_head") or row.get("registered"))}
        bases = {row.get("base") for row in tracked.values()}
        return sorted((pr for pr, row in tracked.items() if row.get("branch") not in bases), key=int)

    def gate(self, rows: dict[str, dict], held: list[str]) -> None:
        tips = self.tips(rows)
        with ThreadPoolExecutor(max(1, len(tips))) as pool:
            gated = dict(zip(tips, pool.map(lambda tip: self.shell.run(self.enqueue_argv(tip, held, check=True)), tips), strict=True))
        for tip, done in gated.items():
            verdicts = {match["pr"]: match for match in VERDICT_LINE.finditer(done.out)}
            if (would := WOULD_ENQUEUE.search(done.out)) and self.reviewed(prefix := [number.lstrip("#") for number in would.group(1).split()], verdicts, rows):
                self.accept(tip, prefix, verdicts)
            for pr, match in verdicts.items():
                if match["verdict"] == "BLOCKED" and pr in rows and "held" not in match["detail"].split("; "):
                    self.route_blocker(pr, match["sha"], match["detail"], rows[pr], tip, held)

    @staticmethod
    def reviewed(prefix: list[str], verdicts: dict[str, re.Match], rows: dict[str, dict]) -> bool:
        return all(pr in verdicts and verdicts[pr]["sha"] and rows.get(pr, {}).get("head", "").startswith(verdicts[pr]["sha"]) for pr in prefix)

    def accept(self, tip: str, prefix: list[str], verdicts: dict[str, re.Match]) -> None:
        """One enqueue per exact set of prefix heads; a fresh attempt only after every earlier one enqueued nothing."""
        base = enqueue_key(prefix, verdicts)
        attempts = [action for action in self.book.actions(LANDING, kind="enqueue") if action.action_id.split("#")[0] == base]
        if any(action.status != "completed" or action.response["outcome"] not in RETRYABLE for action in attempts):
            return
        policy = self.runner.landing_policy()
        authority = f"{policy.authority_ref} {json.loads(policy.target)['revision']}" if policy else ""
        self.book.accept(LANDING, f"{base}#{len(attempts) + 1}", "enqueue", ",".join(prefix), authority, self.runner.now() + timedelta(minutes=self.runner.config.enqueue_minutes))

    def enqueue(self) -> None:
        accepted = self.book.actions(LANDING, kind="enqueue", status="accepted")
        with ThreadPoolExecutor(max(1, len(accepted))) as pool:
            list(pool.map(self.enqueue_one, accepted))

    def enqueue_one(self, action: actions.Action) -> None:
        key = action.action_id
        if not self.book.attempt(LANDING, lambda incident: incident.start(key, self.runner.now(), deadline=actions.parse_stamp(action.deadline))):
            return
        prefix = action.target.split(",")
        held = self.held(self.rows())
        fresh = self.shell.run(self.enqueue_argv(prefix[-1], held, check=True)).out
        would = WOULD_ENQUEUE.search(fresh)
        current = enqueue_key([number.lstrip("#") for number in would.group(1).split()], {match["pr"]: match for match in VERDICT_LINE.finditer(fresh)}) if would else ""
        if current != key.split("#")[0]:
            self.book.attempt(LANDING, lambda incident: incident.complete(key, {"outcome": "superseded", "out": fresh[-800:], "at": self.book.stamp()}))
            return
        done = self.shell.run(self.enqueue_argv(prefix[-1], held, check=False))
        outcome = ENQUEUE_OUTCOMES.get(done.code, "failed")
        if outcome == "enqueued" and "enqueue #" not in done.out:
            outcome = "noop"
        self.book.attempt(LANDING, lambda incident: incident.complete(key, {"outcome": outcome, "out": done.out[-1500:], "at": self.book.stamp()}))
        if outcome in ("stranded", "failed", "unsettled"):
            last = (done.out or done.err).strip().splitlines()
            self.runner.escalate(key, f"ENQUEUE-{outcome.upper()}", f"#{prefix[-1]}", last[-1] if last else f"exit {done.code}")

    def reconcile(self) -> None:
        """An enqueue whose response was lost: Graphite's own status decides; a partial queue is unverifiable and nothing is re-enqueued."""
        cutoff = self.runner.now() - timedelta(minutes=self.runner.config.enqueue_minutes)
        for action in self.book.actions(LANDING, kind="enqueue", status="started"):
            if actions.parse_stamp(action.started_at) < cutoff:
                self.book.attempt(LANDING, lambda incident, key=action.action_id: incident.lose(key))
        for action in self.book.actions(LANDING, kind="enqueue", status="unverifiable"):
            prefix = action.target.split(",")
            done = self.shell.run([str(self.checkout / STACK_ENQUEUE), *prefix, "--status"])
            statuses = {match["pr"]: match["status"] for match in STATUS_LINE.finditer(done.out)}
            if done.code != 0 or set(statuses) != set(prefix):
                continue
            queued = [pr for pr in prefix if statuses[pr] in QUEUED]
            key = action.action_id
            if len(queued) == len(prefix):
                self.book.attempt(LANDING, lambda incident: incident.complete(key, {"outcome": "enqueued", "out": "reconciled from Graphite status", "at": self.book.stamp()}))
            else:
                self.runner.escalate(key, "UNVERIFIABLE", f"#{prefix[-1]}", f"Graphite holds {', '.join('#' + pr for pr in queued) or 'none'} of {', '.join('#' + pr for pr in prefix)}; nothing was re-enqueued")

    def verify(self, rows: dict[str, dict]) -> None:
        for action in self.book.actions(LANDING, kind="enqueue", status="completed"):
            prefix = action.target.split(",")
            if action.response["outcome"] not in ("enqueued", "noop") or not all(rows.get(pr, {}).get("state") == "landed" for pr in prefix):
                continue
            receipt = {"landed": {pr: rows[pr].get("landed_sha", "") for pr in prefix}, "at": self.book.stamp()}
            self.book.attempt(LANDING, lambda incident, key=action.action_id: incident.verify(key, receipt))
        for action in self.book.actions(LANDING, kind="enqueue", status="verified"):
            top = action.target.split(",")[-1]
            self.restack(top, rows)

    def restack(self, landed_pr: str, rows: dict[str, dict]) -> None:
        landed = rows.get(landed_pr, {})
        for pr, row in sorted(rows.items(), key=lambda item: int(item[0])):
            if row.get("state", "open") != "open" or row.get("base") != landed.get("branch"):
                continue
            text = f"#{landed_pr} landed as {landed.get('landed_sha', '')[:10]}; #{pr} at {row.get('head', '')[:10]} sits on its deleted branch. Restack now: `ccx vcs stack submit` from your worktree."
            self.route(f"restack:{pr}:{landed_pr}", row.get("lane", ""), text, pr)

    def route_blocker(self, pr: str, sha: str, blocker: str, row: dict, tip: str, held: list[str]) -> None:
        """Tell the lane once per head and blocker, re-reading the gate just before the send so an approval or fix that already arrived is never asked for."""
        key = f"blocker:{pr}:{sha}:{hashlib.sha1(blocker.encode()).hexdigest()[:8]}"
        if self.routed(key, row.get("lane", "")):
            return
        fresh = {match["pr"]: match for match in VERDICT_LINE.finditer(self.shell.run(self.enqueue_argv(tip, held, check=True)).out)}
        current = fresh.get(pr)
        if current and current["verdict"] == "BLOCKED" and current["detail"] == blocker:
            self.route(key, row.get("lane", ""), f"#{pr} {sha}: {blocker}", pr)

    def routed(self, key: str, lane: str) -> bool:
        container = lane_container(lane)
        return key in self.book.load(LANDING).actions or (container in self.book.store.ids() and key in self.book.load(container).actions)

    def route(self, key: str, lane: str, text: str, pr: str) -> None:
        if self.routed(key, lane):
            return
        if not lane:
            self.runner.escalate(key, "UNOWNED", f"#{pr}", text)
        elif self.runner.orca.receipt(lane):
            relay, _ = self.runner.accept_relay(key, lane, text, "", self.runner.config.start_minutes)
            if (dispatch := self.runner.orca.show(lane)) and dispatch.status not in INACTIVE:
                self.runner.send(lane_container(lane), dispatch, relay)
        elif self.bus:
            self.book.accept(LANDING, key, "bus", json.dumps({"lane": lane, "pr": pr, "text": text}), key, None)
            self.book.attempt(LANDING, lambda incident: incident.start(key, self.runner.now()))
            done = self.shell.run([sys.executable, str(SCRIPTS / "bus.py"), "post", "--bus", self.bus, "--from", "desk-runner", "--kind", "blocker", "--topic", pr, "--to", lane, "--text", text])
            if done.code == 0:
                self.book.attempt(LANDING, lambda incident: incident.complete(key, {"posted": done.out.strip()[:200], "at": self.book.stamp()}))
            else:
                self.book.attempt(LANDING, lambda incident: incident.fail(key, (done.err or done.out).strip()[:300]))
        else:
            self.runner.escalate(key, "ROUTE", lane, text)

    def verify_restacks(self, rows: dict[str, dict]) -> None:
        """A restack route is verified once its PR's head moves or the PR lands."""
        routes = [(LANDING, action) for action in self.book.actions(LANDING, kind="bus")]
        routes += [(name, action) for name in self.book.containers(LANE_PREFIX) for action in self.book.actions(name, kind="relay")]
        for container, action in routes:
            if not action.action_id.startswith("restack:") or action.status in ("verified", "failed"):
                continue
            _, pr, landed_pr = action.action_id.split(":")
            row = rows.get(pr, {})
            if row and (row.get("state", "open") != "open" or row.get("base") != rows.get(landed_pr, {}).get("branch")):
                receipt = {"head": row.get("head", ""), "state": row.get("state", ""), "at": self.book.stamp()}
                self.book.attempt(container, lambda incident, key=action.action_id: incident.verify(key, receipt))

    def run(self) -> None:
        self.ledger_py("refresh", "--repo", self.repo, "--ledger", self.ledger)
        self.ledger_py("reconcile", "--repo", self.repo, "--ledger", self.ledger, "--checkout", str(self.checkout))
        rows = self.rows()
        if not rows:
            return
        self.reconcile()
        self.verify(rows)
        self.verify_restacks(rows)
        self.gate(rows, self.held(rows))
        self.enqueue()
        self.runner.overdue(LANDING, "landing")


def seed_policy(runner: Runner) -> None:
    landing = runner.config.landing
    if landing and not runner.landing_policy():
        policy = landing["policy"]
        runner.accept_policy(f"policy:{policy['revision']}", policy["rule"], policy["revision"], policy["source"], "")


def run_orca(runner: Runner, once: bool) -> int:
    state = runner.binding()
    if not state["bound"]:
        runner.escalate(f"unbound:{state['terminal']}:{state['coordinator']}:{state['generation']}", "UNBOUND", "runner", f"{runner.unbound(state)}; the runner refused to start")
        runner.flush()
        print(runner.unbound(state), file=sys.stderr)
        return 3
    swept = datetime.min.replace(tzinfo=timezone.utc)
    while True:
        if runner.check():
            runner.reap()
            runner.resume_judges()
            runner.read_inbox()
            runner.deliver()
            if runner.now() - swept >= SWEEP_EVERY:
                runner.sweep()
                swept = runner.now()
        runner.deadlines_orca()
        runner.aged_holds()
        runner.flush()
        runner.write_view()
        if once:
            return 0
        runner.shell.sleep(10)


def run_landing(runner: Runner, once: bool) -> int:
    seed_policy(runner)
    landing = Landing(runner, runner.config.landing)
    interval = runner.config.landing.get("interval_seconds", 180)
    while True:
        started = runner.now()
        landing.run()
        runner.flush()
        if once:
            return 0
        runner.shell.sleep(max(0.0, interval - (runner.now() - started).total_seconds()))


def launch_model(model: str) -> str:
    if not MODELS.fullmatch(model):
        raise argparse.ArgumentTypeError(f"{model} is not a model orca-launch.sh starts: opus, sonnet, fable, claude-*, astra, codex, sol, or gpt-*")
    return model


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="desk-runner.py", description=__doc__.split("\n", 1)[0])
    verbs = parser.add_subparsers(dest="verb", required=True)
    relay = verbs.add_parser("relay")
    relay.add_argument("--key", required=True)
    relay.add_argument("--lane", required=True)
    relay.add_argument("--text", required=True)
    relay.add_argument("--reply-to", default="")
    relay.add_argument("--deadline-minutes", type=int)
    launch = verbs.add_parser("launch")
    launch.add_argument("--key", required=True)
    launch.add_argument("--lane", required=True)
    launch.add_argument("--model", required=True, type=launch_model)
    launch.add_argument("--effort", required=True, choices=EFFORTS)
    launch.add_argument("--brief", required=True, type=Path)
    launch.add_argument("--owner-directed", action="store_true")
    policy = verbs.add_parser("policy")
    policy.add_argument("--key", required=True)
    policy.add_argument("--landing", choices=LANDING_POLICIES, required=True)
    policy.add_argument("--revision", required=True)
    policy.add_argument("--source", required=True)
    policy.add_argument("--supersedes", default="")
    loop = verbs.add_parser("run")
    loop.add_argument("--desk", choices=("orca", "landing"), required=True)
    loop.add_argument("--once", action="store_true")
    rebind = verbs.add_parser("rebind")
    show = verbs.add_parser("show")
    for sub in (relay, launch, policy, loop, rebind, show):
        sub.add_argument("--config", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None, shell: Shell | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = Config.load(args.config)
    runner = Runner(shell or Shell(), config, actions.Store(config.store))
    if args.verb == "run":
        return (run_orca if args.desk == "orca" else run_landing)(runner, args.once)
    if args.verb == "show":
        sys.stdout.write(runner.render())
        return 0
    if args.verb == "rebind":
        return runner.rebind()
    if args.verb == "relay":
        action, created = runner.accept_relay(args.key, args.lane, args.text, args.reply_to, args.deadline_minutes or config.start_minutes)
    elif args.verb == "launch":
        action, created = runner.accept_launch(args.key, args.lane, args.model, args.effort, str(args.brief.expanduser().resolve()), args.owner_directed)
    else:
        action, created = runner.accept_policy(args.key, args.landing, args.revision, args.source, args.supersedes)
    runner.flush()
    print(f"{action.action_id} {action.kind} {action.status}{'' if created else ' (already accepted)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
