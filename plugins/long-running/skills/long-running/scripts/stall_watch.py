#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
"""The stall watch: one pass over a drive that finds each piece of work that stopped moving, names the lane that owns its next step and that step, and posts one `urgent` cci record per stall to its owners, and to root unless desks own it, until it clears.

    stall_watch.py --config RUNNER_JSON [--dry-run]

STDLIB ONLY. `--config` is the desk runner's config, and its `stall` object turns the watch on: the orca
desk runner reads that object every pass and starts one detached pass every `stall.every_minutes`
(default 5) once the last has exited, logging to `stall-watch.log` beside `orca.desk_inbox`. A pass reads
the drive's cci records over the last `stall.window_hours` (default 24), the landing ledger's open rows,
the holds file and the open `hold` records, the landing desk's store, the root's board through
`cc-present outcomes`, and the root session's task list. For every open PR no hold covers, at the head
its ledger row names, it finds:

    go        a root go names the head, no `QUEUED` record names the PR at it, and no hold covers a PR
              below it; the stall belongs to the first PR of its stack, from the bottom, with no go
              or with a red, conflicting or unfinished build, or whose newest ai-review run the review
              bot titles Held or Waiting for restack, else to the landing desk, and every go
              in one stack shares one stall naming the stack's bottom and that blocker; a go a
              fidelity carry moved to a new head counts from the carry grade, not the go
    tap       the owner's board tap approves the head, and no go names it
    finder    a `head` record names the head, and no finder review does
    fidelity  a `head` record names the head, and no fidelity grade does
    board     a finder ok and a fidelity ok name the head, its build is green, it waits on the owner's
              tap (merge mode every, or the landing desk records it waiting on `merge:<pr>:<sha7>`),
              the board holds no row or card for it at that head, no holds-file line leads with it, and no
              root decide pulled its row with no READY record about the PR since
    carry     the owner's board tap or a root go names an earlier head, no go or carry names this
              one, and the board holds no row for it at this head; a head that adds no new behavior,
              such as a review, CI or conflict fix, keeps his tap (rule 399b5fa) and waits on root's
              go, and only new behavior, or records that do not say, re-asks

and, across the drive:

    lane      a lane owning an in-progress task on the root's task list posted nothing past the time
              its last record promised, or for `stall.minutes.lane`, counted for a lane with no
              record yet from when its task file was last written; a lane owes a reply to an open
              ask addressed to it since its last record, or to a decide or correction that gives it
              an order as `<lane>: ...`, and stalls once the oldest is `stall.minutes.lane` old; a lane that owes no
              reply and whose open PRs are none red, conflicting, needing a restack, or waiting on
              its answer to the reviewer bot is idle when it has open PRs or its last record is a
              done or READY one
    ask       a lane's `ask` to root is open, and no root record answers it
    stale     an open PR, held or not, has kept one head and one state for `stall.minutes.stale`: in the
              merge queue, held, on a drifted graphite-base, conflicting, red, waiting on the PR below it, building,
              held or not yet reviewed by the reviewer bot, green with a go, or green with none; no
              other stall names it, a green PR whose merge row waits on the owner's tap at its head,
              and a held PR whose holds-file line names open PRs that each wait on that tap, sit in
              the merge queue, are held the same way, or are stacked on such a PR, or that names a
              board card or pick, by its block id or by a card holding the PR, the owner has not
              answered, post one `state` record to root per head instead, once while the PR stays open
              at that head, and one digest record when several post in one pass; a tap row counts from
              the earlier of its board row's write and the head's READY record; a PR that waits on the PR
              below it posts nothing itself and is listed on the stale PR its stack waits on, and a
              PR whose owning lane has no live record in the window goes to root; a changed state on
              the same head counts from the pass that saw it change; a held PR whose holds-file line
              names an open PR the owner has not tapped at its head is never stale, until that PR lands,
              is tapped, or the hold lifts; each PR in the run of PR numbers a holds-file
              line leads with counts as held here

Each kind counts once it is as old as `stall.minutes` says: go 10, tap 10, finder 30, fidelity 30,
board 15, carry 20 (one fidelity pass), lane 30, ask 15, stale 120. A PR's head is as old as the first record about the
PR whose whole text names its short sha: its `head` record, a ready report, or a finder or fidelity record. A board
stall counts from the latest of its finder ok, its fidelity ok, and its ledger row's `green_at`, the refresh that
first read the head's build green, since the board refresher puts a row up only once the build is green.
Two questions need judgment, and one headless Claude call per pass
(`stall.model`, default claude-sonnet-5-5, at low effort) answers all of them: whether a PR's new head is
a patch-identical restack of the tapped one or adds no new behavior, read from its lane's `head` records, and whether a quiet
lane's last record explains its silence and by when it promised its next state. A verdict is kept
against the records it read, so no question is asked twice; a call that fails leaves each question to
its mechanical answer.

A stall's id names its kind and subject, such as `finder:43439:abc1234` or `lane:fidelity-desk`, and it
posts once while it stands: `cci post --lane stall-watch --kind urgent --to root,<owner>@<drive>` reading
`STALL <kind> <subject>, <age> min: <what stopped> | owner <lane> | <next step>`, or `STALE <subject>, ...`
for a stale PR. A stall every owner of which is a desk, a lane named `<x>-desk` or `<x>-desk-<n>`, goes to
its owners and not to root; one the desk runner owns goes to `landing.desk_lane` when the config names one.
An id that no longer stands clears, and posts again if it stalls again, but never sooner than `REPEAT` after
its last post; a source that does not read keeps the stalls it would have cleared. `stall.board_lane` (default root) owns missing board rows. State lives in
`stall-watch.json` beside `orca.desk_inbox`, under a lock a second pass meets by exiting. Each pass also
writes `stack.json` there: one row per open PR with its head, base, the PR below it, its state and next
step in words, its owning lane and whether that lane is live, and since when the state stood; the
dashboard's model carries it as `stack` from its config's `stack` path, and `drive.py digest` prints it.
`--dry-run` prints the stalls and posts and saves nothing.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import actions
import board_rows
import cci
import drive
import ghread
import grades
import holds

SCRIPTS = Path(__file__).resolve().parent
LANE = "stall-watch"
ROOT = "root"
RUNNER = "desk-runner"
LANDING_DESK = "landing-desk"
ROOT_NAMES = frozenset({ROOT, "main"})
NOT_LANES = frozenset({*ROOT_NAMES, "team-lead", LANE, RUNNER})
PACIFIC = ZoneInfo("America/Los_Angeles")
MODEL = "claude-sonnet-5-5"
WINDOW_HOURS = 24
MINUTES = {"go": 10, "tap": 10, "finder": 30, "fidelity": 30, "board": 15, "carry": 20, "lane": 30, "ask": 15, "stale": 120}
PR_KINDS = frozenset({"go", "tap", "finder", "fidelity", "board", "carry", "stale"})
BOARD_KINDS = frozenset({"tap", "board", "carry"})
REVIEW_HELD = frozenset({"neutral", "failure"})
PROMISE_FLOOR = timedelta(minutes=10)
EXPLAINED_CEILING = 4
HEAD_RECORDS = 4
LISTED = 5
QUOTE = 160
RECORD_CHARS = 1200
COMMAND_SECONDS = 120
JUDGE_SECONDS = 300
LANDING = "desk-landing"
MERGE_WAITS = "merge_waits"
MERGE_CARDS = "merge_asks"
STATE = "stall-watch.json"
QUEUED = re.compile(r"^QUEUED\b")
TAP_WAIT = "waits on the owner's tap"
IN_QUEUE = "in the merge queue"
HELD = "held: "
OWNER_WAITS = frozenset({TAP_WAIT, IN_QUEUE})
RED = "red on "
CONFLICTS = "conflicts with its base"
BASE_DRIFT = "base drifted to "
BOT_HOLDS = "the reviewer bot holds it"
NO_VERDICT = "no reviewer bot verdict on this head"
LANE_OWES = (RED, CONFLICTS, BASE_DRIFT, BOT_HOLDS, NO_VERDICT)
PR_REF = re.compile(r"#(\d+)")
LEADING_PRS = re.compile(r"^(?:#\d+\s+)+")
SETTLED = frozenset({"done", "ready"})
READY = re.compile(r"\b(?:READY|DONE)\b")
PULLED = re.compile(r"\bpull(?:s|ed)?\b[^.]*\brows?\b", re.IGNORECASE)
ASK_WORD = re.compile(r"\b(?:card|pick)\b", re.IGNORECASE)
BLOCK_WORD = re.compile(r"[\w-]+")
MERGE_CARD = re.compile(r"^merge:(\d+):([0-9a-f]{7,40})$")
ENDED = frozenset({"done", "handoff", "stand-down", "stopped", "not-ours"})
DESK = re.compile(r"(?:.+-)?desk(?:-\d{1,2})?")
REPEAT = timedelta(minutes=30)
SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {
            "verdicts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "restack": {"type": "boolean"},
                        "fix": {"type": "boolean"},
                        "explained": {"type": "boolean"},
                        "promised": {"type": "string"},
                        "why": {"type": "string"},
                    },
                    "required": ["id", "why"],
                },
            }
        },
        "required": ["verdicts"],
    }
)
PROMPT = """You read coordination records from a multi-agent software drive and answer each question below. It is {now} UTC ({pacific} Pacific).

A question of kind "restack": the owner approved a pull request at tapped_head, and it now sits at head. The records are the lane's own `head` announcements for that PR, oldest first. Answer restack true only when they say head replays the tapped change unchanged: a rebase or restack onto a newer base that adds no commit, fix, or test of its own. Answer fix true when every commit head adds beyond the tapped change adds no new behavior of its own: a fix for a finding a reviewer, a reviewer bot, the finder, or a red CI build raised, a conflict resolution, or their tests; the owner's rule keeps his approval over any change that adds no new behavior. Answer both false when head adds new behavior, or the records do not say what it adds.

A question of kind "silence": a lane has posted nothing for minutes_silent minutes since last_record. Answer promised with the RFC 3339 UTC time by which that record says the lane's next state or result will come, such as "next state by 1:30 PM", "back in 20 minutes", or "ETA 13:05", reading a time with no zone as Pacific and a relative time from last_at; leave promised empty when it names none. Answer explained true only when the record names something the lane waits on that takes this long, such as a CI build, a release, or a long command it runs; a record that reports finished work, asks a question, or says nothing of what comes next is explained false.

Answer every question by its id, each with a one-sentence why.

Questions:
{questions}
"""


@dataclass(frozen=True)
class Done:
    code: int
    out: str
    err: str


class ReadFailed(RuntimeError):
    pass


class Shell:
    def run(self, argv: list[str], stdin: str | None = None, env: dict[str, str] | None = None, timeout: float = COMMAND_SECONDS) -> Done:
        try:
            done = subprocess.run(argv, input=stdin, capture_output=True, text=True, errors="replace", env={**os.environ, **env} if env else None, timeout=timeout)
        except subprocess.TimeoutExpired:
            return Done(124, "", f"{argv[0]} ran past {timeout:.0f}s")
        return Done(done.returncode, done.stdout, done.stderr)

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Watch:
    drive: str
    state: Path
    store: Path | None
    landing: dict | None
    minutes: dict[str, int]
    window: timedelta
    model: str
    board_lane: str

    @classmethod
    def load(cls, path: Path) -> Watch:
        raw = json.loads(path.read_text())
        stall = raw.get("stall") or {}
        return cls(
            drive=raw["drive"],
            state=Path(raw["orca"]["desk_inbox"]).expanduser().parent / STATE,
            store=Path(raw["store"]).expanduser() if raw.get("store") else None,
            landing=raw.get("landing"),
            minutes=MINUTES | stall.get("minutes", {}),
            window=timedelta(hours=stall.get("window_hours", WINDOW_HOURS)),
            model=stall.get("model", MODEL),
            board_lane=stall.get("board_lane", ROOT),
        )

    def age(self, kind: str) -> timedelta:
        return timedelta(minutes=self.minutes[kind])

    @property
    def desk_lanes(self) -> list[str]:
        return lanes_of((self.landing or {}).get("desk_lane", []))


@dataclass(frozen=True)
class Stall:
    id: str
    kind: str
    since: datetime
    subject: str
    what: str
    owner: tuple[str, ...]
    step: str
    pr: str = ""

    def line(self, now: datetime) -> str:
        word = "STALE" if self.kind == "stale" else f"STALL {self.kind}"
        return f"{word} {self.subject}, {int((now - self.since).total_seconds() // 60)} min: {self.what} | owner {', '.join(self.owner)} | {self.step}"

    def to(self, drive_name: str, desk_lanes: list[str]) -> str:
        lanes = [named for lane in self.owner for named in (desk_lanes if lane == RUNNER else [lane]) if named not in ROOT_NAMES]
        desks = lanes and all(lane == RUNNER or DESK.fullmatch(lane) for lane in self.owner)
        return ",".join(dict.fromkeys([*([] if desks else [ROOT]), *(f"{lane}@{drive_name}" for lane in lanes)]))


@dataclass(frozen=True)
class Question:
    key: str
    ask: dict
    settle: Callable[[dict | None], Stall | None]


@dataclass(frozen=True)
class Board:
    heads: set[tuple[str, str]]
    taps: dict[str, dict]
    cards: list[tuple[str, set[str], set[str]]]
    answered: set[str]
    written: list[tuple[str, str, datetime]]

    def shows(self, pr: str, head: str) -> bool:
        return any(named == pr and grades.names_head(sha, head) for named, sha in self.heads)

    def live_at(self, pr: str, head: str) -> datetime | None:
        return min((when for named, sha, when in self.written if named == pr and grades.names_head(sha, head)), default=None)

    def open_asks(self, pr: str, line: str) -> set[str]:
        words = set(BLOCK_WORD.findall(line))
        named = {choice for card, choices, _ in self.cards for choice in choices if card in words or choice in words}
        if not named and ASK_WORD.search(line):
            named = {choice for _, choices, prs in self.cards if pr in prs for choice in choices}
        return named - self.answered


def at(record: dict) -> datetime:
    return actions.parse_stamp(record["at"])


def pacific(moment: datetime) -> str:
    return moment.astimezone(PACIFIC).strftime("%-I:%M %p")


def clip(text: str, limit: int = QUOTE) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def short(sha: str) -> str:
    return sha[:7]


def block_ids(node: object) -> Iterator[str]:
    if isinstance(node, dict):
        if isinstance(node.get("id"), str):
            yield node["id"]
        for value in node.values():
            yield from block_ids(value)
    elif isinstance(node, list):
        for value in node:
            yield from block_ids(value)


def typed(node: object, kind: str) -> Iterator[dict]:
    if isinstance(node, dict):
        if node.get("type") == kind:
            yield node
        for value in node.values():
            yield from typed(value, kind)
    elif isinstance(node, list):
        for value in node:
            yield from typed(value, kind)


def lanes_of(value: str | list[str]) -> list[str]:
    return [value] if isinstance(value, str) else list(value)


def root_session(drive_name: str) -> str | None:
    return next((entry["sessions"][-1] for entry in drive.drives() if drive.cci_drive(entry) == drive_name and entry.get("sessions")), None)


def task_owners(session: str) -> dict[str, list[tuple[str, datetime]]]:
    tasks = drive.drives_dir().parents[1] / "tasks"
    owners: dict[str, list[tuple[str, datetime]]] = {}
    for directory in (tasks / session, tasks / f"session-{session[:8]}"):
        for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
            task = json.loads(path.read_text())
            if task.get("status") == "in_progress" and task.get("owner"):
                owners.setdefault(task["owner"], []).append((str(task["id"]), datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)))
    return owners


def carried_heads(head: str, carries: list[tuple[str, str]]) -> set[str]:
    reached = {head}
    while fresh := {earlier for later, earlier in carries if earlier not in reached and any(grades.names_head(later, sha) for sha in reached)}:
        reached |= fresh
    return reached


def downstack(rows: dict[str, dict], row: dict) -> list[str]:
    below: list[str] = []
    while parent := next((pr for pr, other in rows.items() if other.get("branch") and other["branch"] == row.get("base") and pr not in below), None):
        below.append(parent)
        row = rows[parent]
    return below


def stack_row(pr: str, row: dict, rows: dict[str, dict], owner: str, held: bool, going: bool, in_queue: bool, tapping: bool) -> dict:
    named = f"#{pr} at {short(row['head'])}"
    below = next((other for other, parent in rows.items() if parent.get("branch") and parent["branch"] == row.get("base")), "")
    red = ", ".join(check["name"] for check in json.loads(row.get("failing_checks") or "[]")) or "a required check"
    waits = ""
    if in_queue:
        state, step = IN_QUEUE, f"the landing desk reads why the merge queue still holds {named}"
    elif held:
        state, step = f"{HELD}{clip(row.get('hold_reason') or 'a hold record covers it')}", f"lift the hold on #{pr} or do what it waits on"
    elif row.get("base", "").startswith("graphite-base/"):
        state, step = f"{BASE_DRIFT}{row['base']}", f"resubmit {named} with `ccx vcs ship --tip-only` and post HEAD"
    elif row.get("mergeable_state") == "dirty":
        state, step = CONFLICTS, f"restack {named} and post HEAD"
    elif row.get("test_state") == "failure":
        state, step = f"{RED}{red}", f"fix the red build of {named}"
    elif below:
        state, step, waits = f"waits on #{below} below it", f"land #{below} first", below
    elif row.get("test_state") != "success":
        state, step = "its build has not finished", f"read the build of {named}"
    elif row.get("ai_review") in REVIEW_HELD:
        state, step = BOT_HOLDS, f"answer each blocking finding on #{pr} with a fix or a one-line rebuttal"
    elif row.get("ai_review") != "success":
        state, step = NO_VERDICT, f"re-request the reviewer bot's review of {named}"
    elif going:
        state, step = "green with a go, not queued", f"the landing desk enqueues {named}"
    elif tapping:
        state, step = TAP_WAIT, f"the owner taps the merge row for {named} on his board"
    else:
        state, step = "green, and no go names this head", f"root posts `go #{pr} at {short(row['head'])}` or puts its merge row on the owner's board"
    return {"pr": pr, "title": row.get("title", ""), "head": row["head"], "base": row.get("base", ""), "below": below, "waits": waits, "state": state, "next": step, "owner": owner}


def successor_of(lane: str) -> re.Pattern[str]:
    return re.compile(rf"{re.escape(lane)}(?:-\d+)?")


class Pass:
    def __init__(self, shell: Shell, watch: Watch):
        self.shell = shell
        self.watch = watch
        self.now = shell.now()
        self.unread: set[str] = set()
        self.states: dict[str, dict] = {}
        self.stack: list[dict] | None = None
        self.tap_waits: dict[str, tuple[str, str]] | None = None
        self.hold_gates: dict[str, list[str]] = {}
        self.hold_asks: dict[str, set[str]] = {}
        self.tapped: set[str] = set()

    def tail(self, argv: list[str]) -> str:
        done = self.shell.run(argv)
        if done.code:
            raise ReadFailed(f"{' '.join(argv[:2])} exited {done.code}: {(done.err or done.out).strip()[:300]}")
        return done.out

    def warn(self, kinds: frozenset[str] | set[str], why: str) -> None:
        self.unread |= kinds
        print(f"WARN {why}; the {', '.join(sorted(kinds))} stalls it would clear stay open", file=sys.stderr)

    def run(self, dry_run: bool) -> int:
        saved = json.loads(self.watch.state.read_text()) if self.watch.state.is_file() else {"open": {}, "verdicts": {}}
        records = [record for record in cci.records(self.tail, self.watch.drive, since=actions.stamp(self.now - self.watch.window)) if record["drive"] == self.watch.drive]
        if (session := root_session(self.watch.drive)) is None:
            self.warn(BOARD_KINDS | {"lane"}, f"no drive registry entry for {self.watch.drive} names a root session, so neither the board nor the task list read")
        stalls, questions, entries = self.prs(records, session)
        lane_stalls, lane_questions = self.lanes(records, session, entries)
        stalls += lane_stalls + self.asks(records)
        questions += lane_questions
        cached = saved.get("verdicts", {})
        fresh = self.judge([question.ask | {"id": question.key} for question in questions if question.key not in cached])
        verdicts = {question.key: cached.get(question.key) or fresh.get(question.key) for question in questions}
        stalls += [stall for question in questions if (stall := question.settle(verdicts[question.key]))]
        kept = {key.split(":")[1] for key, entry in saved["open"].items() if entry["kind"] in PR_KINDS & self.unread}
        stalls += self.stale(entries, saved.get("states", {}), records, kept | {stall.pr for stall in stalls if stall.pr})
        return self.report({stall.id: stall for stall in stalls}, saved, {key: verdict for key, verdict in verdicts.items() if verdict}, dry_run)

    def report(self, standing: dict[str, Stall], saved: dict, verdicts: dict[str, dict], dry_run: bool) -> int:
        kept = {key: entry for key, entry in saved["open"].items() if key not in standing and entry["kind"] in self.unread}
        last = {key: stamp for key, stamp in saved.get("last", {}).items() if self.now - actions.parse_stamp(stamp) < REPEAT}
        failed = 0
        for stall in sorted(standing.values(), key=lambda stall: stall.since):
            if stall.id in saved["open"]:
                kept[stall.id] = saved["open"][stall.id]
                print(f"open {stall.line(self.now)}")
                continue
            if stall.id in last:
                print(f"held {stall.line(self.now)}: posted at {pacific(actions.parse_stamp(last[stall.id]))}")
                continue
            print(f"new {stall.line(self.now)}")
            if dry_run:
                continue
            done = self.post(stall)
            if done.code:
                failed += 1
                print(f"cci post exited {done.code} for {stall.id}: {(done.err or done.out).strip()[:300]}", file=sys.stderr)
                continue
            kept[stall.id] = {"kind": stall.kind, "since": actions.stamp(stall.since), "posted": done.out.strip()[:200]}
            last[stall.id] = actions.stamp(self.now)
        heads = None if self.stack is None else {f"{found['pr']}:{short(found['head'])}" for found in self.stack}
        told = {key: stamp for key, stamp in saved.get("told", {}).items() if heads is None or key in heads}
        fresh = {key: wait for key, wait in (self.tap_waits or {}).items() if key not in told}
        for _, line in fresh.values():
            print(f"state {line}")
        if fresh and not dry_run:
            topic, text = (f"tap-wait:{next(iter(fresh))}", next(iter(fresh.values()))[1]) if len(fresh) == 1 else ("tap-wait:digest", f"{len(fresh)} heads wait on the owner's tap: {'; '.join(line for _, line in fresh.values())}")
            done = self.shell.run(["cci", "post", "--drive", self.watch.drive, "--lane", LANE, "--kind", "state", "--to", ROOT, "--topic", topic, *(arg for pr, _ in fresh.values() for arg in ("--pr", f"{self.watch.landing['repo']}#{pr}")), "--text", text])
            if done.code:
                failed += 1
                print(f"cci post exited {done.code} for {topic}: {(done.err or done.out).strip()[:300]}", file=sys.stderr)
            else:
                told |= dict.fromkeys(fresh, actions.stamp(self.now))
        if not dry_run:
            staged = self.watch.state.with_name(f".{STATE}")
            staged.write_text(json.dumps({"open": kept, "last": last, "verdicts": verdicts, "states": self.states, "told": told}, indent=1) + "\n")
            staged.replace(self.watch.state)
            if self.stack is not None:
                stack = self.watch.state.with_name(drive.STACK)
                staged = stack.with_name(f".{drive.STACK}")
                staged.write_text(json.dumps({"at": actions.stamp(self.now), "prs": self.stack}, indent=1) + "\n")
                staged.replace(stack)
        return 1 if failed else 0

    def post(self, stall: Stall) -> Done:
        argv = ["cci", "post", "--drive", self.watch.drive, "--lane", LANE, "--kind", "urgent", "--to", stall.to(self.watch.drive, self.watch.desk_lanes), "--topic", stall.id]
        if stall.pr:
            argv += ["--pr", f"{self.watch.landing['repo']}#{stall.pr}"]
        return self.shell.run([*argv, "--text", stall.line(self.now)])

    def judge(self, questions: list[dict]) -> dict[str, dict]:
        if not questions:
            return {}
        prompt = PROMPT.format(now=actions.stamp(self.now), pacific=pacific(self.now), questions=json.dumps(questions, indent=1))
        done = self.shell.run(["claude", "-p", "--model", self.watch.model, "--effort", "low", "--no-session-persistence", "--strict-mcp-config", "--tools", "", "--output-format", "json", "--json-schema", SCHEMA], stdin=prompt, timeout=JUDGE_SECONDS)
        try:
            payload = json.loads(done.out) if done.code == 0 else None
        except ValueError:
            payload = None
        if payload is None:
            print(f"WARN claude -p exited {done.code}: {(done.err or done.out).strip()[-300:]}; {len(questions)} question(s) take their mechanical answer", file=sys.stderr)
            return {}
        result = (payload[-1] if isinstance(payload, list) else payload).get("structured_output") or {}
        return {verdict["id"]: verdict for verdict in result.get("verdicts", [])}

    def board(self, session: str | None) -> Board | None:
        if session is None:
            return None
        done = self.shell.run(["cc-present", "outcomes", "--session", session])
        start = done.out.find("{")
        if done.code or start < 0:
            self.warn(BOARD_KINDS, f"cc-present outcomes exited {done.code}: {(done.err or done.out).strip()[:200]}")
            return None
        outcomes = json.loads(done.out[start:])
        doc = outcomes.get("doc") or {}
        heads = {(row.pr, row.head) for row in board_rows.rows_of(doc) if row.head}
        heads |= {(found[1], found[2]) for key in block_ids(doc) if (found := MERGE_CARD.match(key))}
        interactions = outcomes.get("interactions", {})
        verdicts = {block: {row: entry for row, entry in rows.items() if entry.get("verdict")} for block, rows in (interactions.get("triage") or {}).items()}
        cards = [(card["id"], {choice["id"] for choice in typed(card, "choice")}, {str(found["number"]) for found in typed(card, "pr")}) for card in typed(doc, "card")]
        answered = {block for block, choice in (interactions.get("choices") or {}).items() if choice and (choice.get("optionIds") or choice.get("other"))}
        written = [(row.pr, row.head, when) for row in board_rows.rows_of(doc) if row.head and (when := board_rows.parsed(row.text_at))]
        return Board(heads, board_rows.newest_by_pr(verdicts), cards, answered, written)

    def rows(self) -> dict[str, dict] | None:
        landing = self.watch.landing
        done = self.shell.run([sys.executable, str(SCRIPTS / "ledger.py"), "-C", str(Path(landing["checkout"]).expanduser()), "list", "--ledger", landing["ledger"], "--open", "--json"], env={drive.DRIVE_ENV: self.watch.drive})
        if done.code:
            self.warn(PR_KINDS, f"ledger.py list exited {done.code}: {(done.err or done.out).strip()[:200]}")
            return None
        return {row["pr"]: row for row in json.loads(done.out)}

    def landing_facts(self) -> dict:
        store = actions.Store(self.watch.store) if self.watch.store else None
        return store.load(LANDING).facts if store and store.path(LANDING).is_file() else {}

    def prs(self, records: list[dict], session: str | None) -> tuple[list[Stall], list[Question], list[dict]]:
        landing = self.watch.landing
        if not landing or (rows := self.rows()) is None:
            return [], [], []
        board = self.board(session)
        repo, drive_name = landing["repo"], self.watch.drive
        fidelity_lanes = lanes_of(landing.get("fidelity_lane", []))
        go_lanes = [ROOT, *landing.get("go_lanes", [])]
        closed = {seq for record in records if record["lane"] in ROOT_NAMES for seq in grades.closes(record)}
        gos = [record | {"voided": set()} for record in records if record["kind"] == "go" and record["lane"] in go_lanes and record["seq"] not in closed and grades.go_heads(record)]
        reviews = [record for record in records if record["kind"] in ("review", "head")]
        fidelity = [record for record in reviews if record["kind"] == "review" and record["lane"] in fidelity_lanes and record["text"].startswith(grades.FIDELITY_GRADES)]
        gos += grades.carried_gos(gos, fidelity, repo, lambda pr, head: True)
        queued = {(pr, sha) for record in records if (record["lane"] == RUNNER or record["lane"].startswith(LANDING_DESK)) and record["kind"] == "report" and QUEUED.match(record["text"]) for pr, sha in grades.GO_HEAD.findall(record["text"])}
        hold_lines = holds.read(Path(landing["holds"]).expanduser())
        held = holds.held(hold_lines, rows) | set(cci.held_prs(self.tail, drive_name))
        for entry in hold_lines:
            for named in PR_REF.findall(leading[0] if (leading := LEADING_PRS.match(entry.line)) else ""):
                self.hold_gates.setdefault(named, []).extend(gate for gate in PR_REF.findall(entry.line) if gate != named)
                if board:
                    self.hold_asks.setdefault(named, set()).update(board.open_asks(named, entry.line))
        self.tapped = {pr for pr, row in rows.items() if board and (tap := board.taps.get(pr) or {}).get("verdict") == "approved" and tap.get("head") and grades.names_head(tap["head"], row.get("head", ""))}
        facts = self.landing_facts()
        waits = {pr: wait["key"] for pr, wait in facts.get(MERGE_WAITS, {}).items()}
        cards = set(facts.get(MERGE_CARDS, {}))
        stalls: list[Stall] = []
        questions: list[Question] = []
        waiting: dict[str, list[tuple[str, datetime, int, tuple[str, str, str]]]] = {}
        entries: list[dict] = []

        def holding(pr: str) -> bool:
            return pr in held or bool(rows[pr].get("hold_until")) and actions.parse_stamp(rows[pr]["hold_until"]) > self.now

        for pr, row in sorted(rows.items(), key=lambda item: int(item[0])):
            head = row.get("head", "")
            if not head:
                continue
            announced = [record for record in records if grades.about_pr(record, repo, pr) and short(head) in grades.full_text(record)]
            head_at = at(announced[0]) if announced else None
            going = grades.gone_at(gos, pr, head)
            in_queue = any(named == pr and grades.names_head(sha, head) for named, sha in queued)
            lane = row.get("lane") or next((record["lane"] for record in reversed(announced) if record["kind"] == "head"), ROOT)
            tapping = board is not None and board.shows(pr, head) and not board_rows.at_head(board.taps.get(pr), "rejected", head)
            readied = [at(record) for record in announced if record["kind"] == "ready" or READY.search(record["text"])]
            tap_at = min([*readied, *([live] if (live := board.live_at(pr, head)) else [])], default=None) if tapping else None
            entries.append(stack_row(pr, row, rows, lane, holding(pr) or pr in self.hold_gates, bool(going), in_queue, tapping) | {"head_at": head_at, "tap_at": tap_at})
            if holding(pr):
                continue
            subject = f"#{pr} at {short(head)}"
            about = [record for record in reviews if grades.about_pr(record, repo, pr)]
            if going:
                below = downstack(rows, row)
                ready = min(max(at(go), actions.parse_stamp(go.get("graded_at") or go["at"])) for go in going)
                since = max(ready, head_at) if head_at else ready
                if not any(holding(parent) for parent in below) and (waits_on := self.unqueued(pr, row, rows, below, gos, since, queued, lane)):
                    waiting.setdefault(below[-1] if below else pr, []).append((pr, since, len(below), waits_on))
                continue
            tap = (board.taps.get(pr) if board else None) or {}
            tapped = tap["head"] if tap.get("verdict") == "approved" and tap.get("head") and tap.get("at") else ""
            if tapped and grades.names_head(tapped, head):
                tapped_at = actions.parse_stamp(tap["at"])
                if self.now - tapped_at >= self.watch.age("tap"):
                    stalls.append(Stall(f"tap:{pr}:{short(head)}", "tap", tapped_at, subject, f"the owner approved it on the board at {pacific(tapped_at)}, and no go names this head", tuple(go_lanes), f"post `go #{pr} at {short(head)}`", pr))
                continue
            if head_at is None:
                continue
            graded = grades.fidelity_grades_in(about, drive_name, fidelity_lanes, repo, pr)
            standing = grades.standing(graded, fidelity_lanes, head)
            reached = carried_heads(head, grades.fidelity_carries(graded))
            finders = [record for record in grades.finder_records(grades.finder_reviews_in(about, drive_name, repo, pr)) if any(grades.names_head(record["head"], sha) for sha in reached)]
            if not finders and self.now - head_at >= self.watch.age("finder"):
                stalls.append(Stall(f"finder:{pr}:{short(head)}", "finder", head_at, subject, f"no finder review names this head, first named {pacific(head_at)} in #{announced[0]['seq']}", (lane,), f"run the finder (reference/finder-brief.md) on {subject} and post its review", pr))
            if not standing and fidelity_lanes and self.now - head_at >= self.watch.age("fidelity"):
                stalls.append(Stall(f"fidelity:{pr}:{short(head)}", "fidelity", head_at, subject, f"no fidelity grade names this head, first named {pacific(head_at)} in #{announced[0]['seq']}", tuple(fidelity_lanes), f"grade {subject} and post its `fidelity: ok` or `fidelity: missing` line", pr))
            if board is None:
                continue
            taps = [(sha, f"root go #{go['seq']}") for go in gos if (sha := grades.go_heads(go).get(pr))]
            if tapped:
                taps.append((tapped, "his board tap"))
            if taps:
                if not board.shows(pr, head) and self.now - head_at >= self.watch.age("carry"):
                    questions.append(self.carry(pr, head, head_at, taps[-1], about, announced[-1], fidelity_lanes))
                continue
            if not row.get("green_at") or not (landing.get("merge_mode") == "every" or waits.get(pr) == f"merge:{pr}:{short(head)}"):
                continue
            finder_ok = [record for record in finders if record["grade"] == grades.FINDER_PASS]
            if not finder_ok or not standing or not all(grade["text"].startswith(grades.FIDELITY_OK) for grade in standing):
                continue
            ready = max(actions.parse_stamp(row["green_at"]), *(at(record) for record in [*finder_ok, *standing]))
            if board.shows(pr, head) or pr in self.hold_gates or self.now - ready < self.watch.age("board"):
                continue
            pulls = [record for record in records if record["lane"] in ROOT_NAMES and record["kind"] in ("decide", "decision") and grades.about_pr(record, repo, pr) and PULLED.search(grades.full_text(record))]
            if pulls and not any(record["seq"] > pulls[-1]["seq"] and grades.about_pr(record, repo, pr) and (record["kind"] == "ready" or READY.search(record["text"])) for record in records):
                continue
            card = f"merge:{pr}:{short(head)}"
            told = f"; the landing desk recorded its card {card} as posted, yet the board holds none" if card in cards else ""
            stalls.append(Stall(f"board:{pr}:{short(head)}", "board", ready, subject, f"finder ok, fidelity ok and a green build since {pacific(ready)}, and the owner's board holds no row or card for it at this head{told}", (self.watch.board_lane,), f"put the merge row for {subject} on the owner's board", pr))
        for bottom, group in waiting.items():
            group.sort(key=lambda entry: entry[2])
            blocker, owner, step = group[0][3]
            named = [f"#{pr}" for pr, *_ in group]
            listed = ", ".join(named[:LISTED]) + (f" and {len(named) - LISTED} more" if len(named) > LISTED else "")
            stack = f" in the stack from #{bottom}" if bottom != blocker or len(named) > 1 else ""
            sha = short(rows[blocker]["head"])
            stalls.append(Stall(f"go:{blocker}:{sha}", "go", min(since for _, since, *_ in group), f"#{blocker} at {sha}", f"root go{'s name' if len(named) > 1 else ' names'} {listed}{stack}, and no QUEUED record does", (owner,), step, blocker))
        return stalls, questions, entries

    def stale(self, entries: list[dict], saved: dict[str, dict], records: list[dict], stalled: set[str]) -> list[Stall]:
        if "stale" in self.unread:
            self.states, self.stack = saved, None
            return []
        newest: dict[str, dict] = {}
        for record in records:
            newest[record["lane"]] = record
        self.states, self.stack, self.tap_waits, standing = {}, [], {}, {}
        found_by_pr = {found["pr"]: found for found in entries}
        for found in entries:
            pr, head_at, tap_at = found["pr"], found.pop("head_at"), found.pop("tap_at")
            signature = f"{found['head']} {found['state']}"
            was = saved.get(pr)
            if was and was["signature"] == signature:
                since = actions.parse_stamp(was["since"])
            elif was and was["signature"].split(" ", 1)[0] == found["head"]:
                since = self.now
            else:
                since = head_at or self.now
            since = min(since, tap_at) if tap_at and found["state"] == TAP_WAIT else since
            self.states[pr] = {"signature": signature, "since": actions.stamp(since)}
            last = max((record for name, record in newest.items() if successor_of(found["owner"]).fullmatch(name)), key=lambda record: record["seq"], default=None)
            live = last is not None and last["kind"] not in ENDED
            stale = self.now - since >= self.watch.age("stale")
            self.stack.append(found | {"since": actions.stamp(since), "live": live, "stale": stale})
            subject = f"#{pr} at {short(found['head'])}"
            if stale and found["state"] == TAP_WAIT:
                self.tap_waits[f"{pr}:{short(found['head'])}"] = (pr, f"{subject} {TAP_WAIT} since {pacific(since)}")
            elif stale and found["state"].startswith(HELD) and (gates := self.owner_gates(pr, found_by_pr, {pr})):
                self.tap_waits[f"{pr}:{short(found['head'])}"] = (pr, f"{subject} is held on {', '.join(sorted(gates))}, each waiting on the owner's tap, his board answer, or the merge queue, since {pacific(since)}")
            elif stale and pr not in stalled and not (found["state"].startswith(HELD) and any(gate in found_by_pr and gate not in self.tapped for gate in self.hold_gates.get(pr, []))):
                standing[pr] = (found, since, live)
        rows = {found["pr"]: found for found in self.stack}
        above: dict[str, list[str]] = {}
        for pr, (found, *_) in standing.items():
            below = found["waits"]
            while below in rows and rows[below]["waits"]:
                below = rows[below]["waits"]
            if found["waits"] and below in standing:
                above.setdefault(below, []).append(f"#{pr}")
        stalls: list[Stall] = []
        for pr, (found, since, live) in standing.items():
            if found["waits"]:
                continue
            waiting = above.get(pr, [])
            what = found["state"] + (f"; {len(waiting)} above wait on it: {', '.join(waiting)}" if waiting else "")
            if not live:
                what += f"; {found['owner']} has posted nothing live in {int(self.watch.window.total_seconds() // 3600)} h"
            subject = f"#{pr} at {short(found['head'])}"
            stalls.append(Stall(f"stale:{pr}:{short(found['head'])}", "stale", since, subject, what, (found["owner"],) if live else (ROOT,), found["next"], pr))
        return stalls

    def owner_gates(self, pr: str, found_by_pr: dict[str, dict], seen: set[str]) -> set[str] | None:
        named = [gate for gate in self.hold_gates.get(pr, []) if gate in found_by_pr]
        gates = set(self.hold_asks.get(pr, ()))
        if not named and not gates:
            return None
        for gate in dict.fromkeys(named):
            if gate in seen:
                continue
            if (reached := self.waits_on_owner(gate, found_by_pr, seen | {gate})) is None:
                return None
            gates |= reached
        return gates

    def waits_on_owner(self, pr: str, found_by_pr: dict[str, dict], seen: set[str]) -> set[str] | None:
        found = found_by_pr[pr]
        if found["state"] in OWNER_WAITS:
            return {f"#{pr}"}
        if found["state"].startswith(HELD):
            return self.owner_gates(pr, found_by_pr, seen)
        if below := found["waits"]:
            return set() if below in seen else self.waits_on_owner(below, found_by_pr, seen | {below})
        return None

    def unqueued(self, pr: str, row: dict, rows: dict[str, dict], below: list[str], gos: list[dict], since: datetime, queued: set[tuple[str, str]], lane: str) -> tuple[str, str, str] | None:
        if any(named == pr and grades.names_head(sha, row["head"]) for named, sha in queued) or self.now - since < self.watch.age("go"):
            return None
        for member in [*reversed(below), pr]:
            other = rows[member]
            named = f"#{member} at {short(other['head'])}"
            owns = lane if member == pr else other.get("lane") or ROOT
            if member != pr and not grades.gone_at(gos, member, other["head"]):
                return member, ROOT, f"{named} has no go, and a stack enqueues from the bottom"
            if other.get("test_state") == "failure":
                return member, owns, f"fix the red build of {named}; the go waits on it"
            if other.get("mergeable_state") == "dirty":
                return member, owns, f"restack {named}: GitHub reads it as conflicting against its base"
            if other.get("test_state") != "success":
                return member, owns, f"{named} has no green build yet ({other.get('test_state') or 'no state'}); the go waits on it"
            if other.get("ai_review") == "neutral" and (waits := grades.review_bot_wait(self.review_title(other["head"]), owns)):
                return member, owns, f"{named}: {waits}"
        return pr, RUNNER, f"the landing desk has not queued it: read its newest GO-UNQUEUED, BELOW-BLOCKED or BELOW-HELD line for #{pr} and the runner's heartbeat (`drive.py digest`)"

    def review_title(self, head: str) -> str:
        done = self.shell.run(ghread.read("api", f"repos/{self.watch.landing['repo']}/commits/{head}/check-runs?check_name={grades.REVIEW_BOT_CHECK}", "--jq", ".check_runs | max_by(.started_at) | .output.title // \"\""))
        return done.out.strip() if done.code == 0 else ""

    def carry(self, pr: str, head: str, head_at: datetime, tap: tuple[str, str], about: list[dict], newest: dict, fidelity_lanes: list[str]) -> Question:
        tapped, how = tap
        subject = f"#{pr} at {short(head)}"
        heads = [{"seq": record["seq"], "at": record["at"], "lane": record["lane"], "text": clip(grades.full_text(record), RECORD_CHARS)} for record in about if record["kind"] == "head" and record["seq"] <= newest["seq"]][-HEAD_RECORDS:]
        what = f"{how} names {short(tapped)}, and no go or carry names this head, first named {pacific(head_at)}"
        key = f"carry:{pr}:{short(head)}"
        carry_line = f"`fidelity: ok {short(head)} carried from {short(tapped)}`"

        go_line = f"`go #{pr} at {short(head)}` naming the fix once fidelity grades it, and the row lists the fix"
        reask = f"re-ask the owner at {short(head)} unless a record shows it adds no new behavior"

        def settle(verdict: dict | None) -> Stall:
            if verdict is None:
                return Stall(key, "carry", head_at, subject, what, (*fidelity_lanes, ROOT), f"post {carry_line} if it is a patch-identical restack; if it adds no new behavior, his tap keeps (rule 399b5fa) and root posts {go_line}; else root re-asks the owner at {short(head)}", pr)
            if verdict.get("restack"):
                return Stall(key, "carry", head_at, subject, what, tuple(fidelity_lanes), f"post {carry_line}: {clip(verdict['why'])}", pr)
            if verdict.get("fix"):
                return Stall(key, "carry", head_at, subject, what, (ROOT,), f"his tap at {short(tapped)} keeps over a fix with no new behavior (rule 399b5fa): root posts {go_line}: {clip(verdict['why'])}", pr)
            return Stall(key, "carry", head_at, subject, what, (ROOT,), f"{reask}: {clip(verdict['why'])}", pr)

        return Question(f"tap-keeps:{pr}:{short(tapped)}:{short(head)}", {"kind": "restack", "pr": pr, "tapped_head": tapped, "head": head, "records": heads}, settle)

    def lanes(self, records: list[dict], session: str | None, entries: list[dict]) -> tuple[list[Stall], list[Question]]:
        if session is None:
            return [], []
        newest: dict[str, dict] = {}
        for record in records:
            newest[record["lane"]] = record
        stalls: list[Stall] = []
        questions: list[Question] = []
        for lane, tasks in sorted(task_owners(session).items()):
            if lane in NOT_LANES:
                continue
            named = successor_of(lane)
            last = max((record for name, record in newest.items() if named.fullmatch(name)), key=lambda record: record["seq"], default=None)
            prs = [found for found in entries if named.fullmatch(found["owner"])]
            owed = [
                record
                for record in records
                if record["lane"] != LANE
                and grades.owes(record, lane)
                and record.get("status") != "closed"
                and record["seq"] > (last["seq"] if last else 0)
                and any(named.fullmatch(to.split("@", 1)[0]) for to in record.get("to") or [])
            ]
            owned = ", ".join(f"#{task}" for task, _ in tasks)
            if owed:
                if self.now - at(owed[0]) >= self.watch.age("lane"):
                    listed = ", ".join(f"#{record['seq']} ({record['kind']} from {record['lane']} at {pacific(at(record))})" for record in owed[:LISTED])
                    stalls.append(Stall(f"lane:{lane}", "lane", at(owed[0]), lane, f"it owes a reply to {listed}; it owns task {owned}", (lane,), f"{lane} answers with `cci post --re <seq>`; root pings it and relaunches it if no answer comes in 10 min"))
                continue
            settled = last is not None and (last["kind"] in SETTLED or bool(READY.search(last["text"])))
            if (prs or settled) and not any(found["state"].startswith(LANE_OWES) for found in prs):
                continue
            if last is None:
                given = max(when for _, when in tasks)
                if self.now - given >= self.watch.age("lane"):
                    stalls.append(Stall(f"lane:{lane}", "lane", given, lane, f"it owns task {owned} in progress, given it by {pacific(given)}, and has posted nothing since", (ROOT,), f"close task {owned} or relaunch {lane}"))
            elif last["kind"] not in ENDED and self.now - at(last) >= PROMISE_FLOOR:
                questions.append(self.silence(lane, last, owned))
        return stalls, questions

    def silence(self, lane: str, last: dict, owned: str) -> Question:
        quiet = at(last)
        said = f"its last record, {last['kind']} #{last['seq']} at {pacific(quiet)}"

        def settle(verdict: dict | None) -> Stall | None:
            promised = actions.parse_stamp(verdict["promised"]) if verdict and verdict.get("promised") else None
            if promised:
                stalled, what = self.now >= promised, f"{said}, promised its next state by {pacific(promised)}"
            elif verdict and verdict.get("explained"):
                stalled, what = self.now - quiet >= EXPLAINED_CEILING * self.watch.age("lane"), f"{said}, said what it waits on: {clip(verdict['why'])}"
            else:
                stalled, what = self.now - quiet >= self.watch.age("lane"), f"{said}, explains no silence"
            if not stalled:
                return None
            return Stall(f"lane:{lane}", "lane", quiet, lane, f"{what}; it owns task {owned}", (lane,), f"{lane} posts its state now; root pings it and relaunches it if no answer comes in 10 min")

        return Question(f"lane:{lane}:{last['seq']}", {"kind": "silence", "lane": lane, "last_at": last["at"], "minutes_silent": int((self.now - quiet).total_seconds() // 60), "last_record": clip(grades.full_text(last), RECORD_CHARS)}, settle)

    def asks(self, records: list[dict]) -> list[Stall]:
        answered = {record["re"] for record in records if record["lane"] in ROOT_NAMES and record.get("re")}
        return [
            Stall(f"ask:{record['seq']}", "ask", at(record), f"#{record['seq']} from {record['lane']}", f"open and unanswered: {clip(record['text'])}", (ROOT,), f"answer it with `cci post --re {record['seq']}`, or close it with `--resolves {record['seq']}`")
            for record in records
            if record["kind"] == "ask"
            and record.get("status") == "open"
            and ROOT_NAMES & set(record.get("to") or [])
            and record["lane"] not in NOT_LANES
            and record["seq"] not in answered
            and self.now - at(record) >= self.watch.age("ask")
        ]


def main(argv: list[str] | None = None, shell: Shell | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stall_watch.py", description=__doc__.split("\n", 1)[0])
    parser.add_argument("--config", type=Path, required=True, help="the desk runner's runner.json")
    parser.add_argument("--dry-run", action="store_true", help="print the stalls; post and save nothing")
    args = parser.parse_args(argv)
    watch = Watch.load(args.config.expanduser().resolve())
    watch.state.parent.mkdir(parents=True, exist_ok=True)
    with watch.state.with_name(f"{STATE}.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"another stall watch pass holds {lock.name}", file=sys.stderr)
            return 0
        return Pass(shell or Shell(), watch).run(args.dry_run)


if __name__ == "__main__":
    drive.run_installed(__file__)
    sys.exit(main(sys.argv[1:]))
