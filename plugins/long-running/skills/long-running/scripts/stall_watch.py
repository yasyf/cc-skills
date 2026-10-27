#!/usr/bin/env python3
"""The stall watch: one pass over a drive that finds each piece of work that stopped moving, names the lane that owns its next step and that step, and posts one `urgent` cci record per stall to root until it clears.

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
              or with a red, conflicting or unfinished build, else to the landing desk, and the gos
              one PR keeps out share one stall
    tap       the owner's board tap approves the head, and no go names it
    finder    a `head` record names the head, and no finder review does
    fidelity  a `head` record names the head, and no fidelity grade does
    board     a finder ok and a fidelity ok name the head, its build is green, it waits on the owner's
              tap (merge mode every, or the landing desk records it waiting on `merge:<pr>:<sha7>`),
              and the board holds no row or card for it at that head
    carry     the owner's board tap or a root go names an earlier head, no go or carry names this
              one, and the board holds no row for it at this head

and, across the drive:

    lane      a lane owning an in-progress task on the root's task list posted nothing past the time
              its last record promised, or for `stall.minutes.lane`
    ask       a lane's `ask` to root is open, and no root record answers it

Each kind counts once it is as old as `stall.minutes` says: go 10, tap 10, finder 30, fidelity 30,
board 15, carry 15, lane 30, ask 15. Two questions need judgment, and one headless Claude call per pass
(`stall.model`, default claude-sonnet-5-5, at low effort) answers all of them: whether a PR's new head is
a patch-identical restack of the tapped one, read from its lane's `head` records, and whether a quiet
lane's last record explains its silence and by when it promised its next state. A verdict is kept
against the records it read, so no question is asked twice; a call that fails leaves each question to
its mechanical answer.

A stall's id names its kind and subject, such as `finder:43439:abc1234` or `lane:fidelity-desk`, and it
posts once while it stands: `cci post --lane stall-watch --kind urgent --to root,<owner>@<drive>` reading
`STALL <kind> <subject>, <age> min: <what stopped> | owner <lane> | <next step>`. An id that no longer
stands clears, and posts again if it stalls again; a source that does not read keeps the stalls it
would have cleared. `stall.board_lane` (default root) owns missing board rows. State lives in
`stall-watch.json` beside `orca.desk_inbox`, under a lock a second pass meets by exiting. `--dry-run`
prints the stalls and posts and saves nothing.
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
import grades
import holds

SCRIPTS = Path(__file__).resolve().parent
LANE = "stall-watch"
ROOT = "root"
RUNNER = "desk-runner"
ROOT_NAMES = frozenset({ROOT, "main"})
NOT_LANES = frozenset({*ROOT_NAMES, "team-lead", LANE, RUNNER})
PACIFIC = ZoneInfo("America/Los_Angeles")
MODEL = "claude-sonnet-5-5"
WINDOW_HOURS = 24
MINUTES = {"go": 10, "tap": 10, "finder": 30, "fidelity": 30, "board": 15, "carry": 15, "lane": 30, "ask": 15}
PR_KINDS = frozenset({"go", "tap", "finder", "fidelity", "board", "carry"})
BOARD_KINDS = frozenset({"tap", "board", "carry"})
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
MERGE_CARD = re.compile(r"^merge:(\d+):([0-9a-f]{7,40})$")
ENDED = frozenset({"done", "handoff", "stand-down", "stopped", "not-ours"})
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

A question of kind "restack": the owner approved a pull request at tapped_head, and it now sits at head. The records are the lane's own `head` announcements for that PR, oldest first. Answer restack true only when they say head replays the tapped change unchanged: a rebase or restack onto a newer base that adds no commit, fix, or test of its own. An added commit, a behavior change, a conflict resolution that changes code, or records that do not say, is restack false.

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
        return f"STALL {self.kind} {self.subject}, {int((now - self.since).total_seconds() // 60)} min: {self.what} | owner {', '.join(self.owner)} | {self.step}"

    def to(self, drive_name: str) -> str:
        return ",".join(dict.fromkeys([ROOT, *(f"{lane}@{drive_name}" for lane in self.owner if lane not in (ROOT, RUNNER))]))


@dataclass(frozen=True)
class Question:
    key: str
    ask: dict
    settle: Callable[[dict | None], Stall | None]


@dataclass(frozen=True)
class Board:
    heads: set[tuple[str, str]]
    taps: dict[str, dict]

    def shows(self, pr: str, head: str) -> bool:
        return any(named == pr and grades.names_head(sha, head) for named, sha in self.heads)


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


def lanes_of(value: str | list[str]) -> list[str]:
    return [value] if isinstance(value, str) else list(value)


def root_session(drive_name: str) -> str | None:
    return next((entry["sessions"][-1] for entry in drive.drives() if drive.cci_drive(entry) == drive_name and entry.get("sessions")), None)


def task_owners(session: str) -> dict[str, list[str]]:
    tasks = drive.drives_dir().parents[1] / "tasks"
    owners: dict[str, list[str]] = {}
    for directory in (tasks / session, tasks / f"session-{session[:8]}"):
        for path in sorted(directory.glob("*.json")) if directory.is_dir() else []:
            task = json.loads(path.read_text())
            if task.get("status") == "in_progress" and task.get("owner"):
                owners.setdefault(task["owner"], []).append(str(task["id"]))
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


def successor_of(lane: str) -> re.Pattern[str]:
    return re.compile(rf"{re.escape(lane)}(?:-\d+)?")


class Pass:
    def __init__(self, shell: Shell, watch: Watch):
        self.shell = shell
        self.watch = watch
        self.now = shell.now()
        self.unread: set[str] = set()

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
        stalls, questions = self.prs(records, session)
        lane_stalls, lane_questions = self.lanes(records, session)
        stalls += lane_stalls + self.asks(records)
        questions += lane_questions
        cached = saved.get("verdicts", {})
        fresh = self.judge([question.ask | {"id": question.key} for question in questions if question.key not in cached])
        verdicts = {question.key: cached.get(question.key) or fresh.get(question.key) for question in questions}
        stalls += [stall for question in questions if (stall := question.settle(verdicts[question.key]))]
        return self.report({stall.id: stall for stall in stalls}, saved, {key: verdict for key, verdict in verdicts.items() if verdict}, dry_run)

    def report(self, standing: dict[str, Stall], saved: dict, verdicts: dict[str, dict], dry_run: bool) -> int:
        kept = {key: entry for key, entry in saved["open"].items() if key not in standing and entry["kind"] in self.unread}
        failed = 0
        for stall in sorted(standing.values(), key=lambda stall: stall.since):
            if stall.id in saved["open"]:
                kept[stall.id] = saved["open"][stall.id]
                print(f"open {stall.line(self.now)}")
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
        if not dry_run:
            staged = self.watch.state.with_name(f".{STATE}")
            staged.write_text(json.dumps({"open": kept, "verdicts": verdicts}, indent=1) + "\n")
            staged.replace(self.watch.state)
        return 1 if failed else 0

    def post(self, stall: Stall) -> Done:
        argv = ["cci", "post", "--drive", self.watch.drive, "--lane", LANE, "--kind", "urgent", "--to", stall.to(self.watch.drive), "--topic", stall.id]
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
        verdicts = {block: {row: entry for row, entry in rows.items() if entry.get("verdict")} for block, rows in (outcomes.get("interactions", {}).get("triage") or {}).items()}
        return Board(heads, board_rows.newest_by_pr(verdicts))

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

    def prs(self, records: list[dict], session: str | None) -> tuple[list[Stall], list[Question]]:
        landing = self.watch.landing
        if not landing or (rows := self.rows()) is None:
            return [], []
        board = self.board(session)
        repo, drive_name = landing["repo"], self.watch.drive
        fidelity_lanes = lanes_of(landing.get("fidelity_lane", []))
        go_lanes = [ROOT, *landing.get("go_lanes", [])]
        closed = {seq for record in records if record["lane"] in ROOT_NAMES for seq in grades.closes(record)}
        gos = [record | {"voided": set()} for record in records if record["kind"] == "go" and record["lane"] in go_lanes and record["seq"] not in closed and grades.go_heads(record)]
        reviews = [record for record in records if record["kind"] in ("review", "head")]
        fidelity = [record for record in reviews if record["kind"] == "review" and record["lane"] in fidelity_lanes and record["text"].startswith(grades.FIDELITY_GRADES)]
        gos += grades.carried_gos(gos, fidelity, repo, lambda pr, head: True)
        queued = {(pr, sha) for record in records if record["lane"] == RUNNER and record["kind"] == "report" and QUEUED.match(record["text"]) for pr, sha in grades.GO_HEAD.findall(record["text"])}
        held = holds.held(holds.read(Path(landing["holds"]).expanduser()), rows) | set(cci.held_prs(self.tail, drive_name))
        facts = self.landing_facts()
        waits = {pr: wait["key"] for pr, wait in facts.get(MERGE_WAITS, {}).items()}
        cards = set(facts.get(MERGE_CARDS, {}))
        stalls: list[Stall] = []
        questions: list[Question] = []
        waiting: dict[tuple[str, str, str], list[tuple[str, datetime]]] = {}

        def holding(pr: str) -> bool:
            return pr in held or bool(rows[pr].get("hold_until")) and actions.parse_stamp(rows[pr]["hold_until"]) > self.now

        for pr, row in sorted(rows.items(), key=lambda item: int(item[0])):
            head = row.get("head", "")
            if not head or holding(pr):
                continue
            subject = f"#{pr} at {short(head)}"
            about = [record for record in reviews if grades.about_pr(record, repo, pr)]
            announced = [record for record in about if record["kind"] == "head" and grades.names_head(grades.graded_head(record["text"]), head)]
            head_at = at(announced[0]) if announced else None
            lane = row.get("lane") or (announced[-1]["lane"] if announced else ROOT)
            if going := grades.gone_at(gos, pr, head):
                below = downstack(rows, row)
                if not any(holding(parent) for parent in below) and (waits_on := self.unqueued(pr, row, rows, below, gos, going[-1], head_at, queued, lane)):
                    waiting.setdefault(waits_on[0], []).append((pr, waits_on[1]))
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
                stalls.append(Stall(f"finder:{pr}:{short(head)}", "finder", head_at, subject, f"no finder review names this head, announced {pacific(head_at)} in #{announced[0]['seq']}", (lane,), f"run the finder (reference/finder-brief.md) on {subject} and post its review", pr))
            if not standing and fidelity_lanes and self.now - head_at >= self.watch.age("fidelity"):
                stalls.append(Stall(f"fidelity:{pr}:{short(head)}", "fidelity", head_at, subject, f"no fidelity grade names this head, announced {pacific(head_at)} in #{announced[0]['seq']}", tuple(fidelity_lanes), f"grade {subject} and post its `fidelity: ok` or `fidelity: missing` line", pr))
            if board is None:
                continue
            taps = [(sha, f"root go #{go['seq']}") for go in gos if (sha := grades.go_heads(go).get(pr))]
            if tapped:
                taps.append((tapped, "his board tap"))
            if taps:
                if not board.shows(pr, head) and self.now - head_at >= self.watch.age("carry"):
                    questions.append(self.carry(pr, head, head_at, taps[-1], about, announced[-1], fidelity_lanes))
                continue
            if row.get("test_state") != "success" or not (landing.get("merge_mode") == "every" or waits.get(pr) == f"merge:{pr}:{short(head)}"):
                continue
            finder_ok = [record for record in finders if record["grade"] == grades.FINDER_PASS]
            if not finder_ok or not standing or not all(grade["text"].startswith(grades.FIDELITY_OK) for grade in standing):
                continue
            ready = max(at(record) for record in [*finder_ok, *standing])
            if board.shows(pr, head) or self.now - ready < self.watch.age("board"):
                continue
            card = f"merge:{pr}:{short(head)}"
            told = f"; the landing desk recorded its card {card} as posted, yet the board holds none" if card in cards else ""
            stalls.append(Stall(f"board:{pr}:{short(head)}", "board", ready, subject, f"finder ok and fidelity ok since {pacific(ready)}, and the owner's board holds no row or card for it at this head{told}", (self.watch.board_lane,), f"put the merge row for {subject} on the owner's board", pr))
        for (blocker, owner, step), group in waiting.items():
            named = [f"#{pr}" for pr, _ in group]
            listed = ", ".join(named[:LISTED]) + (f" and {len(named) - LISTED} more" if len(named) > LISTED else "")
            sha = short(rows[blocker]["head"])
            stalls.append(Stall(f"go:{blocker}:{sha}", "go", min(since for _, since in group), f"#{blocker} at {sha}", f"root go{'s name' if len(named) > 1 else ' names'} {listed}, and no QUEUED record does", (owner,), step, blocker))
        return stalls, questions

    def unqueued(self, pr: str, row: dict, rows: dict[str, dict], below: list[str], gos: list[dict], go: dict, head_at: datetime | None, queued: set[tuple[str, str]], lane: str) -> tuple[tuple[str, str, str], datetime] | None:
        since = max(at(go), head_at) if head_at else at(go)
        if any(named == pr and grades.names_head(sha, row["head"]) for named, sha in queued) or self.now - since < self.watch.age("go"):
            return None
        for member in [*reversed(below), pr]:
            other = rows[member]
            named = f"#{member} at {short(other['head'])}"
            owns = lane if member == pr else other.get("lane") or ROOT
            if member != pr and not grades.gone_at(gos, member, other["head"]):
                return (member, ROOT, f"{named} has no go, and a stack enqueues from the bottom"), since
            if other.get("test_state") == "failure":
                return (member, owns, f"fix the red build of {named}; the go waits on it"), since
            if other.get("mergeable_state") == "dirty":
                return (member, owns, f"restack {named}: GitHub reads it as conflicting against its base"), since
            if other.get("test_state") != "success":
                return (member, owns, f"{named} has no green build yet ({other.get('test_state') or 'no state'}); the go waits on it"), since
        return (pr, RUNNER, f"the landing desk has not queued it: read its newest GO-UNQUEUED, BELOW-BLOCKED or BELOW-HELD line for #{pr} and the runner's heartbeat (`drive.py digest`)"), since

    def carry(self, pr: str, head: str, head_at: datetime, tap: tuple[str, str], about: list[dict], newest: dict, fidelity_lanes: list[str]) -> Question:
        tapped, how = tap
        subject = f"#{pr} at {short(head)}"
        heads = [{"seq": record["seq"], "at": record["at"], "lane": record["lane"], "text": clip(grades.full_text(record), RECORD_CHARS)} for record in about if record["kind"] == "head" and record["seq"] <= newest["seq"]][-HEAD_RECORDS:]
        what = f"{how} names {short(tapped)}, and no go or carry names this head, announced {pacific(head_at)}"
        key = f"carry:{pr}:{short(head)}"
        carry_line = f"`fidelity: ok {short(head)} carried from {short(tapped)}`"

        def settle(verdict: dict | None) -> Stall:
            if verdict is None:
                return Stall(key, "carry", head_at, subject, what, (*fidelity_lanes, ROOT), f"post {carry_line} if it is a patch-identical restack; else root re-asks the owner at {short(head)}", pr)
            if verdict.get("restack"):
                return Stall(key, "carry", head_at, subject, what, tuple(fidelity_lanes), f"post {carry_line}: {clip(verdict['why'])}", pr)
            return Stall(key, "carry", head_at, subject, what, (ROOT,), f"re-ask the owner at {short(head)}; the tap at {short(tapped)} does not carry: {clip(verdict['why'])}", pr)

        return Question(f"carry:{pr}:{short(tapped)}:{short(head)}", {"kind": "restack", "pr": pr, "tapped_head": tapped, "head": head, "records": heads}, settle)

    def lanes(self, records: list[dict], session: str | None) -> tuple[list[Stall], list[Question]]:
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
            owned = ", ".join(f"#{task}" for task in tasks)
            if last is None:
                stalls.append(Stall(f"lane:{lane}", "lane", self.now - self.watch.window, lane, f"it owns task {owned} in progress and posted nothing in {int(self.watch.window.total_seconds() // 3600)} h", (ROOT,), f"close task {owned} or relaunch {lane}"))
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
