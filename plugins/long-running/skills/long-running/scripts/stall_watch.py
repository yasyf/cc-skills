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
the open `hold` records, the landing desk's store, the board through
board-watch's records, and the root session's task list. For every open PR no hold covers, at the head
its ledger row names, it finds:

    go        a root go names the head, no `QUEUED` record names the PR at it, no hold covers a PR
              below it, and the go is older than `stall.minutes.go`; a root `decide` or `decision` whose
              text opens with a go clause is a go, such as `go #44922 at 519ea97 TAP-KEPT` (root
              #202036), and so it also settles the carry stall at that head; the stall belongs to the first PR of its stack, from the bottom, with no go
              or with a red, conflicting or unfinished build, or whose newest ai-review run the review
              bot titles Held or Waiting for restack, else to the lanes the desk runner's newest open
              blocker line for that PR and head newer than the go goes to, naming its reason, such as FINDER-PENDING,
              TAP-REQUIRED, REFUSED, BELOW-BLOCKED or GO-UNQUEUED, else to the runner when the
              `landing-stacks.json` beside the config shows the stack's worker on one step past that step's
              budget, naming the step and the command in flight, else, once the go is older than
              `stall.minutes.silent` (default 15), to the root and the landing desk as the runner's own
              defect, since an admitted head no record names was skipped silently (root #208560),
              and every go in one stack shares one stall naming the stack's bottom and that blocker;
              a PR below a go with no go of its own draws none unless its next step is root's go: one
              ungraded at its head, or one that waits on the owner's tap (merge mode `every`, a merge row
              up, or his tap at its head) waits on its own row's grade, board, carry or tap stall, never
              on root (root #220563); a head whose newest blocker is the runner's MERGE-ASK draws none,
              since the row's own tap wait reports it;
              a head whose blocker is FINDER-PENDING while the finder desk holds a pass on it queued
              or running draws none, since the desk's own FINDER-DESK-STALLED covers a stalled
              queue; a go a fidelity carry moved to a new head counts from the carry grade, not the go
              a go whose stack holds a PR a standing refusal names draws none, since that refusal's
              own stall names its lane and command
    refusal   the desk runner or a landing desk lane refused to queue a PR a root go or the owner's
              tap names, at the head the refusal names: it needs a restack, conflicts with its base,
              sits on an untracked or closed parent or lost its Graphite parent, GitHub is still
              computing its mergeability, or the runner says FOREIGN, TAP-REQUIRED, CONTRACT-MISSING,
              PARKED, UNOWNED, NOT-QUEUEABLE or ENQUEUE-*; it is an open action naming the PR, its head,
              the reason, the lane that must act (the refusal's `owner <lane>` or `<lane>: #<pr>`
              words, root for FOREIGN and TAP-REQUIRED, the landing desk for mergeability, else the
              PR's lane) and the command the refusal quotes, and it stands until that lane posts a
              `head` record naming a PR the refusal names or answers it by `--re`, a record closes it,
              a QUEUED record names the PR, or the PR moves off that head or leaves the ledger; at
              `stall.minutes.refusal` it posts to root and the lane, naming the command; a lane that
              died is the lane supervisor's to relaunch, never a stall
    red       the head's `landing.trunk_check` check, such as `buildkite/test`, has read failed for
              `stall.minutes.red`, counted from its ledger row's `red_at`, the refresh that first read
              the head's build red, held or not; it names the failed Buildkite job, its first error
              line and the build, and goes as a `blocker` to the PR's owner alone, posting again every
              30 minutes while it stands; the owner is the lane the newest root record that reassigns
              the PR names (`reassigns #1 and #2 to lane-a; #3 to lane-b`), else its ledger row's lane;
              a red no live lane owns goes to root once per head and never again; a red whose failed
              job logged only Buildkite's benign step-export or meta-data 404 probe posts nothing
              itself, and one `report` line a Pacific day names every such head to root; a `red`
              record classing the head inherited, flake or stale silences it until the head moves; a
              red trunk is the desk runner's DEV-RED, never a stall
    unclassed the head's build has read red for `stall.minutes.unclassed`, counted from `red_at`, and no
              `red` record classifies it: `cci post --kind red --pr N --text "class=<class> head=<sha>
              owner=<lane> <what was done>"`, where class is inherited, flake, real or stale and `head=`
              names the red head; a blocker to root and the PR's owner, once per PR and head
    unfixed   the newest `red` record at the head classes it real, and the PR has kept that head for
              `stall.minutes.unfixed` since; a blocker to root and the class's owner, once per PR and head
    sweep     a red head has stood `stall.minutes.sweep` and no lane named `red-sweep` or `red-sweep-<n>`
              is live; one blocker to root naming every red PR, until a red-sweep lane posts
    tap       the owner's board tap approves the head, and no go names it
    ungraded  a `head` record names the head, its build has read green for `stall.minutes.ungraded`, counted
              from its ledger row's `green_at`, and no finder review or no fidelity grade names it, whatever
              the desks hold queued; it is the finder or fidelity stall of a green head, posted to the
              graders, or the landing desk when the finder desk holds the pass, naming how long it has
              waited there; root hears every head a pass newly finds ungraded in one urgent line per pass,
              as it hears an unassigned failure, and never one line per head
    finder    a `head` record names the head, and no finder review does, and the finder desk holds
              no queued pass on the head, nor a running one younger than `stall.minutes.running`;
              a pass past that is the desk runner's to stop and requeue
    fidelity  a `head` record names the head, and no fidelity grade does, and the fidelity desk holds
              no queued grade on it, nor a running one younger than `stall.minutes.running`
    missing   the newest fidelity grade naming the head reads `fidelity: missing`, and for
              `stall.minutes.missing` (default 20) no record of a lane other than a grader or desk answers
              its seq by `--re` or names `#<seq>`, and no PR comment a bot did not write follows it; it
              goes to root and the PR's lane, naming the grader and the missing text, as a red build does
    regrade   such a grade was answered, by that record or comment, and for `stall.minutes.regrade`
              (default 15) no grade has followed; it goes to the lane that graded it, or to the runner's
              desks when the fidelity pipeline did
    board     a finder ok and a fidelity ok name the head, its build is green, it waits on the owner's
              tap (merge mode every, or the landing desk records it waiting on `merge:<pr>:<sha7>`),
              the board holds no row or card for it at that head, no open `hold` record holds it, and no
              root decide pulled its row with no READY record about the PR since
              (a fidelity grade that says one of `landing.exempt_grades`, default `pure port, no interaction`,
              `test-only or CI-only, no non-test code`, `test-only, no non-test code` and
              `CI-only, no non-test code`, or a root record about the PR that says
              `no row`, ends the stall: such a head lands on a root go with no board row)
    carry     the owner's board tap or a root go names an earlier head, no go or carry names this
              one, and the board holds no row for it at this head; a head that adds no new behavior,
              such as a review, CI or conflict fix, keeps his tap (rule 399b5fa) and waits on root's
              go, and only new behavior, or records that do not say, re-asks; a head the desk runner's
              TAP-KEPT, TAP-CARRIED or TAP-NOT-CARRIED note names draws none, since that note already
              settles the carry and names the lane that acts on it (root #224826)

and, across the drive:

    ask       a lane's `ask` to root is open, and no root record answers it
    pick      board-watch's `needs-lane:<block>` ask to root for an owner pick or write-in is open, and no
              lane is bound to it: no record resolves it or answers it by `--re`, no `claim`, no record of a
              lane other than root, a watcher or a desk runner, and no `SPAWNED` record or the brief it
              names, names the block or the ask's `#<seq>`; it goes to root alone, once per pick
    order     a root `decide` record relays an owner order ("owner ... verbatim"), Sonnet reads it as
              naming a change that ships as a new PR, no record from a lane it names or a numbered
              successor cites a PR after it (an `opened` record, or any record with a PR reference),
              and no root record closes it; it goes to root alone,
              from `stall.minutes.order` until the order is two hours old
    gate      a lane's `state` or `report` record says work waits on PR N (`waits on #N`, `waits only
              on #N`, `blocked on #N`, `when #N lands`), the desk runner or a landing desk lane posted
              N landed or QUEUED, and neither that lane nor a numbered successor of it has posted an
              `opened` or `head` record since the later of the two, a later `state` or `report` that no
              longer waits on N, or any record resolving the line; a grader desk's line opens none, since
              a desk opens no work; it posts again every hour it stands (root #218445)
    stale     an open PR, held or not, has kept one head and one state for `stall.minutes.stale`: in the
              merge queue, held, on a drifted graphite-base, conflicting, red, waiting on the PR below it, building,
              held or not yet reviewed by the reviewer bot, green with a go, or green with none; no
              other stall names it, a green PR whose merge row waits on the owner's tap at its head,
              and a held PR whose open `hold` record names open PRs that each wait on that tap, sit in
              the merge queue, are held the same way, or are stacked on such a PR, or that names a
              board card or pick, by its block id or by a card holding the PR, the owner has not
              answered, post one `state` record to root per head instead, once while the PR stays open
              at that head, and one digest record when several post in one pass; a tap row counts from
              the earlier of its board row's write and the head's READY record; a PR that waits on the PR
              below it, a red one whose `red` record classes its head inherited included, posts nothing itself and is listed on the stale PR its stack waits on, a PR in
              the merge queue or green with a go goes to the landing desk lanes, whose step it is, and
              any other PR whose owning lane has no live record in the window goes to root; a changed state on
              the same head counts from the pass that saw it change; a held PR whose open `hold` record
              names an open PR the owner has not tapped at its head is never stale, until that PR lands,
              is tapped, or the hold lifts
    shape     an in-force register row about system shape, one process, one primary and one replica,
              everything a client, N things sharing one X, has no CI test recorded through
              `rulings.py shape <key> --test`, `stall.minutes.shape` after the row was last written; one
              stall to root names every such row and stands until each has a test or is marked no shape
    desk      a desk runner that posted a `<desk> alive` record in the window posted none for
              `stall.minutes.desk`; the record is its heartbeat wherever it runs, and the stall goes
              to root with the restart command the runner's newest record carries

A lane's own liveness, whether it stopped, went silent or outgrew its context, is the lane supervisor's
(`lr/movers/lanes.py`), which relaunches it with no root action; no stall names a lane for its silence.

Each kind counts once it is as old as `stall.minutes` says: go 10, refusal 10, red 2, unclassed 20, unfixed 60, sweep 20, order 3, tap 10, ungraded 10, finder 30,
fidelity 30, running 25, board 15, carry 20 (one fidelity pass), ask 15, pick 15, gate 30, stale 120, desk 10, shape 30. A PR's head is as old as the first record about the
PR whose whole text names its short sha: its `head` record, a ready report, or a finder or fidelity record. A board
stall counts from the latest of its finder ok, its fidelity ok, and its ledger row's `green_at`, the refresh that
first read the head's build green, since the board refresher puts a row up only once the build is green.
Two questions need judgment, and one call per pass through the drive's judge library,
`long_running.judge.batch`, Sonnet at low effort, answers both: whether a PR's new head is
a patch-identical restack of the tapped one or adds no new behavior, read from its lane's `head` records, and whether a relayed owner
order names a change that ships as a new PR. The call is logged as judge kind `stall` in the drive's
`judge/calls.jsonl`, so `drive.py digest` counts it. A verdict is kept
against the records it read, so no question is asked twice; a call that fails leaves each question to
its mechanical answer.

A stall's id names its kind and subject, such as `finder:43439:abc1234` or `ask:259880`, and it
posts once while it stands: `cci post --lane stall-watch --kind urgent --to root,<owner>@<drive>` reading
`STALL <kind> <subject>, <age> min: <what stopped> | owner <lane> | <next step>`, or `STALE <subject>, ...`
for a stale PR. A stall every owner of which is a desk, a lane named `<x>-desk` or `<x>-desk-<n>`, goes to
its owners and not to root, except an `ungraded` one, which root always hears; one the desk runner owns goes
to `landing.desk_lane` when the config names one.
A `red` stall a live lane owns posts again every `REPEAT` while it stands. An id that no longer stands clears, and posts again if it stalls again, but never sooner than `REPEAT` after
its last post; a source that does not read keeps the stalls it would have cleared. `stall.board_lane`, else the first `landing.board_lane`, else root, owns missing board rows. State lives in
`stall-watch.json` beside `orca.desk_inbox`, under a lock a second pass meets by exiting. Each pass also
writes `stack.json` there: one row per open PR with its head, base, the PR below it, its state and next
step in words, its owning lane and whether that lane is live, and since when the state stood, and for a
red head a `red` object: its failing checks, `red_at`, and the newest `red` record's class, owner and
action, else class `unclassed` and the PR's lane; the dashboard's model carries it as `stack` from its
config's `stack` path, `drive.py digest` prints it, and `drive.py reds` prints its red rows.
`--dry-run` prints the stalls and posts and saves nothing.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shlex
import signal
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from functools import cached_property

from long_running import actions, judge
from long_running import board_rows
from long_running import cci
from long_running import drive
from long_running import ghread
from long_running import grades
import owner_waits

SCRIPTS = Path(__file__).resolve().parent
LANE = "stall-watch"
ROOT = "root"
RUNNER = "desk-runner"
LANDING_DESK = "landing-desk"
ROOT_NAMES = frozenset({ROOT, "main"})
NOT_LANES = frozenset({*ROOT_NAMES, "team-lead", LANE, RUNNER})
PACIFIC = ZoneInfo("America/Los_Angeles")
WINDOW_HOURS = 24
MINUTES = {"go": 10, "silent": 15, "refusal": 10, "red": 2, "unclassed": 20, "unfixed": 60, "sweep": 20, "order": 3, "tap": 10, "ungraded": 10, "finder": 10, "fidelity": 10, "missing": 20, "regrade": 15, "running": 25, "board": 15, "carry": 20, "ask": 15, "pick": 15, "gate": 10, "stale": 120, "desk": 10, "shape": 10}
DESK_KINDS = frozenset({"finder", "fidelity"})
ROOT_KINDS = frozenset({"missing", "unclassed", "unfixed"})
REPOSTED = frozenset({"red"})
POST_KINDS = {"red": "blocker", "unclassed": "blocker", "unfixed": "blocker", "sweep": "blocker", "probe": "report"}
RED_CLASSES = ("inherited", "flake", "real", "stale")
RED_FIELD = re.compile(r"\b(class|head|owner)=(\S+)")
HANDED_TO = re.compile(r"\bsuccessor ([a-z][a-z0-9]*(?:-[a-z0-9]+)+)\b")
RED_SWEEP = "red-sweep"
OWNER_ORDER = re.compile(r"\bowner\b.{0,80}\bverbatim\b", re.IGNORECASE | re.DOTALL)
ORDER_WINDOW = timedelta(hours=2)
REASSIGNS = re.compile(r"\breassign", re.IGNORECASE)
CLAUSE = re.compile(r"[;\n]|\.\s")
REASSIGNED_TO = re.compile(r"\bto\s+([a-z][a-z0-9]*(?:-[a-z0-9]+)+)\b")
QUIET = timedelta(minutes=10)
PR_KINDS = frozenset({"go", "refusal", "red", "unclassed", "unfixed", "tap", "ungraded", "finder", "fidelity", "missing", "regrade", "board", "carry", "stale"})
BOARD_KINDS = frozenset({"tap", "board", "carry"})
REVIEW_HELD = frozenset({"neutral", "failure"})
HEAD_RECORDS = 4
LISTED = 5
QUOTE = 160
RECORD_CHARS = 1200
COMMAND_SECONDS = 120
JUDGE_SECONDS = 300
JUDGE_KIND = "stall"
LANDING = "desk-landing"
FINDER_DESK = "desk-finder"
FIDELITY_DESK = "desk-fidelity"
QUEUE_LIVE = frozenset({"accepted", "started"})
FINDER_PENDING = "FINDER-PENDING"
REFUSAL_KINDS = frozenset({"blocker", "report", "note", "defect", "urgent"})
REFUSAL_LABELS = frozenset({"FOREIGN", "TAP-REQUIRED", "CONTRACT-MISSING", "PARKED", "UNOWNED", "NOT-QUEUEABLE", "ENQUEUE-STRANDED", "ENQUEUE-FAILED", "ENQUEUE-UNSETTLED"})
STACK_NOTES = ("BELOW-BLOCKED", "BELOW-HELD", "QUEUED", "REFUSED")
WARNS_ONLY = re.compile(r"\bWARNING\b|\bis not held for it\b")
REFUSAL_CAUSES = (
    ("parent", re.compile(r"\buntracked\b|\b(?:does|do) not track\b|\bnot tracked\b|\blost (?:its )?(?:recorded )?(?:Graphite )?parent\b|\bclosed (?:PR |#\d+)", re.IGNORECASE)),
    ("restack", re.compile(r"\bneeds-restack\b|\bgraphite-base/|\brestack\b", re.IGNORECASE)),
    ("conflict", re.compile(r"\bconflict", re.IGNORECASE)),
    ("mergeability", re.compile(r"\bmergeability\b|\bmergeable(?:_state)? (?:is )?unknown\b", re.IGNORECASE)),
)
REFUSAL_SUBJECT = re.compile(r"(?:\b([a-z][\w-]*):\s+)?#(\d+):?\s+(?:at\s+)?([0-9a-f]{7,40})\b")
REFUSAL_LABEL = re.compile(r"\b[A-Z][A-Z-]*[A-Z]\b")
REFUSAL_SHA = re.compile(r"\bat ([0-9a-f]{7,40})\b")
REFUSAL_COMMAND = re.compile(r"`([^`]+)`|\brun\s+([^.;]+)")
REFUSAL_OWNER = re.compile(r"\bowner lane ([a-z][\w-]*)")
RESTACK_COMMAND = "run `ccx vcs stack submit` from #{pr}'s worktree and post HEAD"
CAUSE_COMMANDS = {
    "parent": RESTACK_COMMAND,
    "restack": RESTACK_COMMAND,
    "conflict": RESTACK_COMMAND,
    "mergeability": "enqueue #{pr} again once GitHub has computed its mergeability",
    "TAP-REQUIRED": "put the merge row for #{pr} at {sha} on the owner's board",
    "CONTRACT-MISSING": "post the contract record #{pr} at {sha} lacks",
    "FOREIGN": "admit or hold the PR the batch for #{pr} would carry",
}
MERGE_ASK = "MERGE-ASK"
SILENT_STEP = "root routes the tooling lane: "
HEAD_BLOCKERS = ("GO-UNQUEUED", "REFUSED", "BELOW-BLOCKED", "BELOW-HELD", MERGE_ASK, "AWAITS-GO")
BLOCKER_LABEL = re.compile(r"\b[A-Z]+(?:-[A-Z]+)+\b")
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
PR_REF = re.compile(r"#(\d+)")
READY = re.compile(r"\b(?:READY|DONE)\b")
NO_ROW = re.compile(r"\bno row\b", re.IGNORECASE)
PULLED = re.compile(r"\bpull(?:s|ed)?\b[^.]*\brows?\b", re.IGNORECASE)
ASK_WORD = re.compile(r"\b(?:card|pick)\b", re.IGNORECASE)
BLOCK_WORD = re.compile(r"[\w-]+")
ENDED = frozenset({"done", "handoff", "stand-down", "stopped", "not-ours"})
DESK = re.compile(r"(?:.+-)?desk(?:-\d{1,2})?")
REPEAT = timedelta(minutes=30)
STACKS_FILE = "landing-stacks.json"
GATE_REPEAT = timedelta(hours=1)
WAITS_ON = re.compile(r"\b(?:waits?\s+(?:only\s+)?on|blocked\s+on)\s+#(\d+)|\bwhen\s+#(\d+)\s+lands\b", re.IGNORECASE)
LANDED = re.compile(r"^LANDED\s+#(\d+)\b")
SPAWNED = re.compile(r"^SPAWNED (?P<lane>[\w.-]+) (?P<brief>\S+)")
NEEDS_LANE = "needs-lane:"
BOARD_WATCH = board_rows.OWNER_LANE
TAP_NOTE = re.compile(r"^TAP-(?:KEPT|NOT-CARRIED|CARRIED(?:-BY-PATCH)?) #(\d+): .*?\b(?:keeps at|does not carry to|now stands at) ([0-9a-f]{7,40})\b")
LANE_NUMBER = re.compile(r"-\d+$")
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
                        "pr": {"type": "boolean"},
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

A question of kind "order": root relayed the owner's order in a decide record. Answer pr true only when the order tells a lane to make a change that ships as a new pull request, such as a revert, a fix, a port, or new code. Answer pr false when it orders only a merge, a release, a hold, a reply, a rule, or a question, or names no action.

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
            done = cci.installed(argv, lambda: subprocess.run(argv, input=stdin, capture_output=True, text=True, errors="replace", env={**os.environ, **(env or {}), **drive.deadline_env(timeout - drive.REAP_SECONDS)}, timeout=timeout))
            if done.returncode == -signal.SIGALRM:
                raise subprocess.TimeoutExpired(argv, timeout)
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
    board_lane: str
    quiet_lanes: list[str]
    stacks: Path

    @classmethod
    def load(cls, path: Path) -> Watch:
        raw = json.loads(path.read_text())
        stall = raw.get("stall") or {}
        landing = raw.get("landing") or {}
        return cls(
            drive=raw["drive"],
            state=Path(raw["orca"]["desk_inbox"]).expanduser().parent / STATE,
            store=Path(raw["store"]).expanduser() if raw.get("store") else None,
            landing=raw.get("landing"),
            minutes=MINUTES | stall.get("minutes", {}),
            window=timedelta(hours=stall.get("window_hours", WINDOW_HOURS)),
            board_lane=stall.get("board_lane") or next(iter(lanes_of(landing.get("board_lane", []))), ROOT),
            quiet_lanes=stall.get("quiet_lanes", [*landing.get("go_lanes", []), *lanes_of(landing.get("board_lane", []))]),
            stacks=path.with_name(f"{path.stem}.{STACKS_FILE}"),
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
    quiet: bool = False

    def line(self, now: datetime) -> str:
        word = "STALE" if self.kind == "stale" else f"STALL {self.kind}"
        return f"{word} {self.subject}, {int((now - self.since).total_seconds() // 60)} min: {self.what} | owner {', '.join(self.owner)} | {self.step}"

    def to(self, drive_name: str, desk_lanes: list[str], live: Callable[[str], str | None]) -> str:
        """The lanes the stall names, each as its live successor when a rotation replaced it (root #215820), with the root too unless desks own it or every lane it names is live and its step is not the root's, so root hears only what it must do or no live lane will act on (root, Oct 29 2:10 PM)."""
        lanes = [live(named) or named for lane in self.owner for named in (desk_lanes if lane == RUNNER else [lane]) if named not in ROOT_NAMES]
        desks = lanes and self.kind not in ROOT_KINDS and (self.quiet or self.kind in DESK_KINDS or all(lane == RUNNER or DESK.fullmatch(lane) for lane in self.owner) or (all(map(live, lanes)) and not self.step.startswith(f"{ROOT} ")))
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


def lanes_of(value: str | list[str]) -> list[str]:
    return [value] if isinstance(value, str) else list(value)


def carried_heads(head: str, carries: list[tuple[str, str]]) -> set[str]:
    reached = {head}
    while fresh := {earlier for later, earlier in carries if earlier not in reached and any(grades.names_head(later, sha) for sha in reached)}:
        reached |= fresh
    return reached


downstack = owner_waits.downstack


def stack_row(pr: str, row: dict, rows: dict[str, dict], owner: str, held: bool, going: bool, in_queue: bool, tapping: bool, inherited: bool) -> dict:
    named = f"#{pr} at {short(row['head'])}"
    below = next((other for other, parent in rows.items() if parent.get("branch") and parent["branch"] == row.get("base")), "")
    red = ", ".join(check["name"] for check in json.loads(row.get("failing_checks") or "[]")) or "a required check"
    waits, actor = "", owner
    if in_queue:
        state, step, actor = IN_QUEUE, f"the landing desk reads why the merge queue still holds {named}", RUNNER
    elif held:
        state, step = f"{HELD}{clip(row.get('hold_reason') or 'a hold record covers it')}", f"lift the hold on #{pr} or do what it waits on"
    elif row.get("base", "").startswith("graphite-base/"):
        state, step = f"{BASE_DRIFT}{row['base']}", f"resubmit {named} with `ccx vcs ship --tip-only` and post HEAD"
    elif row.get("mergeable_state") == "dirty":
        state, step = CONFLICTS, f"restack {named} and post HEAD"
    elif row.get("test_state") == "failure" and inherited and below:
        state, step, waits = f"{RED}{red}, inherited from #{below} below it", f"fix the red build of #{below} below it", below
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
        state, step, actor = "green with a go, not queued", f"the landing desk enqueues {named}", RUNNER
    elif tapping:
        state, step = TAP_WAIT, f"the owner taps the merge row for {named} on his board"
    else:
        state, step = "green, and no go names this head", f"root posts `go #{pr} at {short(row['head'])}` or puts its merge row on the owner's board"
    return {"pr": pr, "title": row.get("title", ""), "head": row["head"], "base": row.get("base", ""), "below": below, "waits": waits, "state": state, "next": step, "owner": owner, "actor": actor}


def red_entry(row: dict, lane: str, classes: list[dict]) -> dict:
    """The red register row of a head: its failing checks and `red_at`, with the class, owner and action of the newest of `classes` whose `head=` names the head, else class `unclassed` owned by `lane`."""
    found = {"checks": ", ".join(check["name"] for check in json.loads(row.get("failing_checks") or "[]")) or "a required check", "since": row["red_at"]}
    for record in reversed(classes):
        fields = dict(RED_FIELD.findall(record["text"]))
        if fields.get("class") in RED_CLASSES and fields.get("head") and grades.names_head(fields["head"], row["head"]):
            return found | {"class": fields["class"], "owner": fields.get("owner", lane), "classed_at": actions.stamp(at(record)), "action": " ".join(RED_FIELD.sub("", record["text"]).split())}
    return found | {"class": "unclassed", "owner": lane, "classed_at": "", "action": ""}


def blocks_head(record: dict, pr: str, head: str) -> bool:
    return (
        record["lane"] == RUNNER
        and record.get("status") != "closed"
        and (record["kind"] == "blocker" or record["text"].startswith(HEAD_BLOCKERS))
        and re.search(rf"#{pr}\b", text := grades.full_text(record)) is not None
        and any(grades.names_head(sha, head) for sha in grades.SHORT_SHA.findall(text))
    )


@dataclass(frozen=True)
class Refusal:
    record: dict
    pr: str
    sha: str
    said: str
    named: str

    @property
    def cause(self) -> str | None:
        if found := next((cause for cause, pattern in REFUSAL_CAUSES if pattern.search(self.said)), None):
            return found
        return next((label for label in REFUSAL_LABEL.findall(self.said) if label in REFUSAL_LABELS), None)

    def owner(self, lane: str) -> str:
        cause = self.cause
        if found := REFUSAL_OWNER.search(self.said):
            return found[1]
        if cause in ("FOREIGN", "TAP-REQUIRED"):
            return ROOT
        if cause == "mergeability":
            return RUNNER
        addressed = {to.split("@", 1)[0] for to in self.record.get("to") or []}
        return self.named if self.named in addressed else lane

    def command(self, head: str) -> str:
        if found := REFUSAL_COMMAND.search(self.said):
            return f"`{found[1]}`" if found[1] else f"run {found[2].strip()}"
        return CAUSE_COMMANDS.get(self.cause or "", "act on cci {num}, then post HEAD or `cci post --re {reference}`").format(pr=self.pr, sha=short(head), num=self.record["num"], reference=shlex.quote(self.record["num"]))


def refusals_in(record: dict, repo: str) -> list[Refusal]:
    """Each PR and head `record` refuses, with the words about it: the text from each `#<pr> <sha>` pair, and a `<lane>: ` before it, to the next pair; a record with no pair refuses the PR its refs or its first `#<pr>` names, at the first `at <sha>` it says."""
    text = grades.full_text(record)
    pairs = list(REFUSAL_SUBJECT.finditer(text))
    if pairs:
        return [Refusal(record, found[2], found[3], text[0 if found is pairs[0] else found.start() : following.start() if following else len(text)], found[1] or "") for found, following in zip(pairs, [*pairs[1:], None], strict=True)]
    named = [pr for pr, _ in grades.repo_prs(record, repo)] if record.get("refs", {}).get("prs") else PR_REF.findall(text)[:1]
    sha = found[1] if (found := REFUSAL_SHA.search(text)) else ""
    return [Refusal(record, pr, sha, text, "") for pr in named]


def refuses(record: dict, desks: list[str]) -> bool:
    return (
        record["kind"] in REFUSAL_KINDS
        and (record["lane"] == RUNNER or record["lane"] in desks or record["lane"].startswith(LANDING_DESK))
        and record.get("status") != "closed"
        and not record["text"].startswith(STACK_NOTES)
        and not WARNS_ONLY.search(record["text"])
    )


def cites_pr(record: dict) -> bool:
    return record["kind"] == "opened" or bool((record.get("refs") or {}).get("prs"))


def binds(record: dict, asked: dict) -> bool:
    """Whether `record` binds a lane to the owner pick board-watch's `asked` record routes: it resolves or answers it, or a claim, a lane's own record, or a spawn or its brief names the pick's block or seq."""
    if asked["seq"] in cci.resolved(record) or record.get("re") == asked["seq"]:
        return True
    names = re.compile(rf"(?<![\w-])(?:{re.escape(asked['topic'].removeprefix(NEEDS_LANE))}|{re.escape(asked['num'])})(?![\w-])")
    if record["kind"] == "state" and (spawn := SPAWNED.match(record["text"])):
        brief = Path(spawn["brief"])
        return bool(names.search(record["text"]) or brief.is_file() and names.search(brief.read_text(errors="replace")))
    return (record["kind"] == "claim" or record["lane"] not in NOT_LANES | {BOARD_WATCH}) and bool(names.search(grades.full_text(record)))


def ended(record: dict) -> bool:
    return record["kind"] in ENDED and not (record["kind"] == "handoff" and record.get("re"))


def successor_of(lane: str) -> re.Pattern[str]:
    return re.compile(rf"{re.escape(grades.SUCCESSOR_NUMBER.sub('', lane))}(?:-\d{{1,2}})*")


class Pass:
    def __init__(self, shell: Shell, watch: Watch):
        self.shell = shell
        self.watch = watch
        self.now = shell.now()
        self.unread: set[str] = set()
        self.lane_newest: dict[str, dict] = {}
        self.states: dict[str, dict] = {}
        self.stack: list[dict] | None = None
        self.tap_waits: dict[str, tuple[str, str]] | None = None
        self.hold_gates: dict[str, list[str]] = {}
        self.hold_asks: dict[str, set[str]] = {}
        self.tapped: set[str] = set()
        self.refused: set[str] = set()
        self.queues: dict[str, list[actions.Action]] = {}
        self.probe_day = ""

    def tail(self, argv: list[str]) -> str:
        done = self.shell.run(argv)
        if done.code:
            raise ReadFailed(f"{' '.join(argv[:2])} exited {done.code}: {(done.err or done.out).strip()[:300]}")
        return done.out

    def live_lane(self, lane: str) -> str | None:
        """`lane` or its numbered successor, whichever posted the newest record in the window, when that record is no end of the lane."""
        last = max((record for name, record in self.lane_newest.items() if successor_of(lane).fullmatch(name)), key=lambda record: record["seq"], default=None)
        return last["lane"] if last is not None and not ended(last) else None

    def warn(self, kinds: frozenset[str] | set[str], why: str) -> None:
        self.unread |= kinds
        print(f"WARN {why}; the {', '.join(sorted(kinds))} stalls it would clear stay open", file=sys.stderr)

    def handed_to(self, lane: str) -> str:
        """The successor `lane`'s own handoff record names, such as `successor api-lane-6`, else `lane`: a PR follows a handoff its lane wrote, never a successor guessed from a lane's name (cci #234379, root #259013)."""
        handoff = self.lane_newest.get(f"{lane}-handoff")
        return named[1] if handoff is not None and (named := HANDED_TO.search(handoff["text"])) else lane

    def run(self, dry_run: bool) -> int:
        saved = json.loads(self.watch.state.read_text()) if self.watch.state.is_file() else {"open": {}, "verdicts": {}}
        self.probe_day = saved.get("probe_day", "")
        records = [record for record in cci.records(self.tail, self.watch.drive, since=actions.stamp(self.now - self.watch.window)) if record["drive"] == self.watch.drive]
        self.lane_newest = {record["lane"]: record for record in records}
        stalls, questions, entries = self.prs(records)
        stalls += self.asks(records) + self.picks(records) + self.gates(records) + self.desks(records) + self.shapes()
        questions += self.orders(records)
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
        alarms: list[Stall] = []
        for stall in sorted(standing.values(), key=lambda stall: stall.since):
            if stall.id in saved["open"] and (stall.kind not in REPOSTED or stall.owner == (ROOT,) or stall.id in last):
                kept[stall.id] = saved["open"][stall.id]
                print(f"open {stall.line(self.now)}")
                continue
            if stall.id in last:
                print(f"held {stall.line(self.now)}: posted at {pacific(actions.parse_stamp(last[stall.id]))}")
                continue
            print(f"{'again' if stall.id in saved['open'] else 'new'} {stall.line(self.now)}")
            if dry_run:
                continue
            done = self.post(stall)
            if done.code:
                failed += 1
                print(f"cci post exited {done.code} for {stall.id}: {(done.err or done.out).strip()[:300]}", file=sys.stderr)
                continue
            kept[stall.id] = {"kind": stall.kind, "since": actions.stamp(stall.since), "posted": done.out.strip()[:200]}
            if stall.kind == "ungraded" and stall.id not in saved["open"] and not stall.to(self.watch.drive, self.watch.desk_lanes, self.live_lane).startswith(ROOT):
                alarms.append(stall)
            if stall.kind == "probe":
                self.probe_day = stall.id.split(":", 1)[1]
            last[stall.id] = actions.stamp(self.now)
        if alarms:
            listed = "; ".join(f"{stall.subject} has no {stall.id.split(':', 1)[0]} grade, green since {pacific(stall.since)}, owner {', '.join(stall.owner)}" for stall in alarms)
            done = self.shell.run(["cci", "post", "--drive", self.watch.drive, "--lane", LANE, "--kind", "urgent", "--to", ROOT, "--topic", f"ungraded:{actions.stamp(self.now)}", *(arg for pr in dict.fromkeys(stall.pr for stall in alarms) for arg in ("--pr", f"{self.watch.landing['repo']}#{pr}")), "--text", f"STALL ungraded, {len(alarms)} green head{'' if len(alarms) == 1 else 's'} past {self.watch.minutes['ungraded']} min with no grade: {listed} | each owner heard its own line"])
            if done.code:
                failed += 1
                print(f"cci post exited {done.code} for the ungraded digest: {(done.err or done.out).strip()[:300]}", file=sys.stderr)
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
            staged.write_text(json.dumps({"open": kept, "last": last, "verdicts": verdicts, "states": self.states, "told": told, "probe_day": self.probe_day}, indent=1) + "\n")
            staged.replace(self.watch.state)
            if self.stack is not None:
                stack = self.watch.state.with_name(drive.STACK)
                staged = stack.with_name(f".{drive.STACK}")
                staged.write_text(json.dumps({"at": actions.stamp(self.now), "prs": self.stack}, indent=1) + "\n")
                staged.replace(stack)
        return 1 if failed else 0

    def post(self, stall: Stall) -> Done:
        argv = ["cci", "post", "--drive", self.watch.drive, "--lane", LANE, "--kind", POST_KINDS.get(stall.kind, "urgent"), "--to", stall.to(self.watch.drive, self.watch.desk_lanes, self.live_lane), "--topic", stall.id]
        if stall.pr:
            argv += ["--pr", f"{self.watch.landing['repo']}#{stall.pr}"]
        return self.shell.run([*argv, "--text", stall.line(self.now)])

    def judge(self, questions: list[dict]) -> dict[str, dict]:
        if not questions:
            return {}
        prompt = PROMPT.format(now=actions.stamp(self.now), pacific=f"{self.now.astimezone(PACIFIC):%b %-d %-I:%M %p}", questions=json.dumps(questions, indent=1))
        answered = judge.batch(JUDGE_KIND, prompt, SCHEMA, drive=self.entry["drive"] if self.entry else None, timeout=JUDGE_SECONDS)
        if answered is None:
            print(f"WARN the judge call failed; {len(questions)} question(s) take their mechanical answer", file=sys.stderr)
            return {}
        return {verdict["id"]: verdict for verdict in answered.get("verdicts", [])}

    @cached_property
    def entry(self) -> dict | None:
        return next((entry for entry in drive.drives() if drive.cci_drive(entry) == self.watch.drive), None)

    def board(self) -> Board | None:
        """What the board shows and what the owner decided on it, from board-watch's records and the runner's board writes, so the watch reads it on any machine."""
        drive_name = self.watch.drive
        try:
            records = [record for record in [*cci.records(self.tail, drive_name, "--lane", board_rows.OWNER_LANE), *cci.records(self.tail, drive_name, "--lane", RUNNER, f"--topic={board_rows.WRITE_TOPIC}")] if record["drive"] == drive_name]
        except ReadFailed as failed:
            self.warn(BOARD_KINDS, f"the board's records did not read: {failed}")
            return None
        records.sort(key=lambda record: record["seq"])
        if (snapshot := board_rows.snapshot_of_records(records)) is None:
            self.warn(BOARD_KINDS, "board-watch has posted no board-rows snapshot")
            return None
        heads = {(pr, head) for pr, head, _ in snapshot["rows"]} | {(pr, sha) for pr, sha in snapshot["merges"]}
        cards = [(card, set(choices), set(prs)) for card, choices, prs in snapshot["cards"]]
        written = [(pr, head, when) for pr, head, at in snapshot["rows"] if (when := board_rows.parsed(at))]
        return Board(heads, board_rows.newest_by_pr(board_rows.verdicts_of_records(records)), cards, board_rows.answered_of_records(records), written)

    def rows(self) -> dict[str, dict] | None:
        landing = self.watch.landing
        done = self.shell.run([sys.executable, str(SCRIPTS / "ledger.py"), "-C", str(Path(landing["checkout"]).expanduser()), "list", "--ledger", landing["ledger"], "--open", "--json"], env={drive.DRIVE_ENV: self.watch.drive})
        if done.code:
            self.warn(PR_KINDS, f"ledger.py list exited {done.code}: {(done.err or done.out).strip()[:200]}")
            return None
        return {row["pr"]: row for row in json.loads(done.out)}

    def landing_facts(self) -> dict:
        store = actions.Store(self.watch.store)
        return store.facts(LANDING) if store.exists(LANDING) else {}

    def queued(self, desk: str, pr: str, head: str) -> actions.Action | None:
        """The queued or running pass `desk` holds on `pr` at `head`, a full pass on the PR included, read once a pass."""
        if desk not in self.queues:
            store = actions.Store(self.watch.store)
            self.queues[desk] = store.actions(desk, statuses=QUEUE_LIVE) if store.exists(desk) else []
        return next((action for action in self.queues[desk] if (job := json.loads(action.target))["pr"] == pr and (job.get("full") or grades.names_head(job["head"], head))), None)

    def desk_runs(self, desk: str) -> bool:
        return actions.Store(self.watch.store).path(desk).is_file()

    def handled(self, records: list[dict], pr: str) -> bool:
        """Whether a `stall.quiet_lanes` lane, such as the go sweep or the board refresher, posted about `pr` in the last ten minutes, so its STALE or board line spares root."""
        families = [successor_of(lane) for lane in self.watch.quiet_lanes]
        for record in reversed(records):
            if self.now - at(record) >= QUIET:
                return False
            if any(family.fullmatch(record["lane"]) for family in families) and grades.about_pr(record, self.watch.landing["repo"], pr):
                return True
        return False

    def finder_holds(self, pr: str, head: str) -> bool:
        return self.queued(FINDER_DESK, pr, head) is not None

    def overran(self, desk: str, pr: str, head: str) -> datetime | None:
        """When the pass `desk` runs on `pr` at `head` started, once it has run `stall.minutes.running`; None while it waits in the queue or runs younger."""
        job = self.queued(desk, pr, head)
        started = actions.parse_stamp(job.started_at) if job and job.status == "started" and job.started_at else None
        return started if started and self.now - started >= self.watch.age("running") else None

    def refusals(self, records: list[dict], rows: dict[str, dict], approved: set[str], lanes: dict[str, str], holding: Callable[[str], bool]) -> list[Stall]:
        """Each standing landing refusal, once per PR, head and cause, from its oldest refusal record no later act answered: a `refusal` stall at `stall.minutes.refusal`."""
        repo = self.watch.landing["repo"]
        closed = {seq for record in records for seq in grades.closes(record)}
        standing: dict[str, tuple[Refusal, str, str]] = {}
        for record in records:
            if not refuses(record, self.watch.desk_lanes) or record["seq"] in closed:
                continue
            found = refusals_in(record, repo)
            named = {refusal.pr for refusal in found}
            for refusal in found:
                row = rows.get(refusal.pr)
                if not row or refusal.pr not in approved or holding(refusal.pr) or not refusal.cause or refusal.sha and not grades.names_head(refusal.sha, row.get("head", "")):
                    continue
                owner = refusal.owner(lanes.get(refusal.pr, ROOT))
                if self.answered(records, record, refusal.pr, row["head"], named, owner):
                    continue
                self.refused.add(refusal.pr)
                key = f"{refusal.pr}:{short(row['head'])}:{refusal.cause.lower()}"
                if key not in standing:
                    standing[key] = (refusal, owner, row["head"])
        stalls = []
        for key, (refusal, owner, head) in standing.items():
            since = at(refusal.record)
            subject, command = f"#{refusal.pr} at {short(head)}", refusal.command(head)
            what = f"{refusal.record['lane']} refused to queue it at {pacific(since)} in {refusal.record['num']}: {clip(refusal.said)}; {owner} has posted no HEAD and no answer since"
            if self.now - since >= self.watch.age("refusal"):
                stalls.append(Stall(f"refusal:{key}", "refusal", since, subject, what, (owner,), f"{owner}: {command}, then post HEAD or `cci post --re {shlex.quote(refusal.record['num'])}`", refusal.pr))
        return stalls

    def answered(self, records: list[dict], refusal: dict, pr: str, head: str, named: set[str], owner: str) -> bool:
        """Whether a record after `refusal` queued `pr` at `head`, or came from `owner` or any numbered lane of its family answering it by `--re` or as a `head` record naming a PR it names."""
        family, repo = successor_of(re.sub(r"-\d{1,2}$", "", owner)), self.watch.landing["repo"]
        for record in records:
            if record["seq"] <= refusal["seq"]:
                continue
            if (record["lane"] == RUNNER or record["lane"].startswith(LANDING_DESK)) and record["kind"] == "report" and QUEUED.match(record["text"]) and any(named_pr == pr and grades.names_head(sha, head) for named_pr, sha in grades.GO_HEAD.findall(record["text"])):
                return True
            if family.fullmatch(record["lane"]) and (record.get("re") == refusal["seq"] or record["kind"] == "head" and any(grades.about_pr(record, repo, other) for other in named)):
                return True
        return False

    def prs(self, records: list[dict]) -> tuple[list[Stall], list[Question], list[dict]]:
        landing = self.watch.landing
        if not landing or (rows := self.rows()) is None:
            return [], [], []
        board = self.board()
        repo, drive_name = landing["repo"], self.watch.drive
        fidelity_lanes = lanes_of(landing.get("fidelity_lane", []))
        go_lanes = [ROOT, *landing.get("go_lanes", [])]
        exempt = landing.get("exempt_grades", [landing.get("port_phrase", grades.PORT_PHRASE), *grades.TEST_ONLY_PHRASES])
        closed = {seq for record in records if record["lane"] in ROOT_NAMES for seq in grades.closes(record) | grades.voids(record)}
        gos = [record | {"voided": set()} for record in records if record["kind"] in grades.GO_KINDS and (record["lane"] == RUNNER or grades.delegated(record["lane"], go_lanes)) and record["seq"] not in closed and grades.go_heads(record)]
        reviews = [record for record in records if record["kind"] in ("review", "head")]
        fidelity = [record for record in reviews if record["kind"] == "review" and record["lane"] in fidelity_lanes and record["text"].startswith(grades.FIDELITY_GRADES)]
        gos += grades.carried_gos(gos, fidelity, repo, lambda pr, head: True)
        queued = {(pr, sha) for record in records if (record["lane"] == RUNNER or record["lane"].startswith(LANDING_DESK)) and record["kind"] == "report" and QUEUED.match(record["text"]) for pr, sha in grades.GO_HEAD.findall(record["text"])}
        held: set[str] = set()
        for record in cci.open_holds(self.tail, drive_name):
            for named in cci.held_by(record):
                held.add(named)
                self.hold_gates.setdefault(named, []).extend(gate for gate in PR_REF.findall(record["text"]) if gate != named)
                if board:
                    self.hold_asks.setdefault(named, set()).update(board.open_asks(named, record["text"]))
        self.tapped = {pr for pr, row in rows.items() if board and (tap := board.taps.get(pr) or {}).get("verdict") == "approved" and tap.get("head") and grades.names_head(tap["head"], row.get("head", ""))}
        facts = self.landing_facts()
        waits = {pr: wait["key"] for pr, wait in facts.get(MERGE_WAITS, {}).items()}
        cards = set(facts.get(MERGE_CARDS, {}))
        stalls: list[Stall] = []
        questions: list[Question] = []
        waiting: dict[str, list[tuple[str, datetime, int, tuple[str, tuple[str, ...], str]]]] = {}
        entries: list[dict] = []
        lanes: dict[str, str] = {}
        boards: dict[str, list[Stall]] = {}
        probes: list[Stall] = []
        reassigned = self.reassignments(records)
        carried = {(found[1], found[2]) for record in records if record["lane"] == RUNNER and (found := TAP_NOTE.match(record["text"]))}
        classes = [record for record in records if record["kind"] == "red"]

        def holding(pr: str) -> bool:
            return pr in held or bool(rows[pr].get("hold_until")) and actions.parse_stamp(rows[pr]["hold_until"]) > self.now

        def awaits_tap(pr: str, head: str) -> bool:
            return waits.get(pr) == f"merge:{pr}:{short(head)}" or board is not None and board.shows(pr, head) and not board_rows.at_head(board.taps.get(pr), "rejected", head)

        def own_row(pr: str, head: str) -> bool:
            if awaits_tap(pr, head) or pr in self.tapped or landing.get("merge_mode") == "every":
                return True
            about = [record for record in reviews if grades.about_pr(record, repo, pr)]
            standing = grades.standing(grades.fidelity_grades_in(about, drive_name, fidelity_lanes, repo, pr), fidelity_lanes, head)
            finders = [record for record in grades.finder_records(grades.finder_reviews_in(about, drive_name, repo, pr)) if grades.names_head(record["head"], head)]
            return not finders or bool(fidelity_lanes) and not (standing and all(grade["text"].startswith(grades.FIDELITY_OK) for grade in standing))

        for pr, row in sorted(rows.items(), key=lambda item: int(item[0])):
            head = row.get("head", "")
            if not head:
                continue
            announced = [record for record in records if grades.about_pr(record, repo, pr) and short(head) in grades.full_text(record)]
            head_at = at(announced[0]) if announced else None
            going = grades.gone_at(gos, pr, head)
            in_queue = any(named == pr and grades.names_head(sha, head) for named, sha in queued)
            lane = lanes[pr] = self.handed_to(next((record["lane"] for record in reversed(records) if record["kind"] == "head" and record["lane"] not in NOT_LANES and grades.about_pr(record, repo, pr)), None) or reassigned.get(pr) or row.get("lane") or ROOT)
            tapping = board is not None and board.shows(pr, head) and not board_rows.at_head(board.taps.get(pr), "rejected", head)
            readied = [at(record) for record in announced if record["kind"] == "ready" or READY.search(record["text"])]
            tap_at = min([*readied, *([live] if (live := board.live_at(pr, head)) else [])], default=None) if tapping else None
            classed = red_entry(row, lane, [record for record in classes if grades.about_pr(record, repo, pr)]) if row.get("red_at") else None
            entries.append(stack_row(pr, row, rows, lane, holding(pr) or pr in self.hold_gates, bool(going), in_queue, tapping, bool(classed) and classed["class"] == "inherited") | {"head_at": head_at, "tap_at": tap_at})
            if (classed is None or classed["class"] in ("real", "unclassed")) and (red := self.red(pr, row, lane)):
                (probes if red.kind == "probe" else stalls).append(red)
            if row.get("test_state") == "failure" and classed:
                entries[-1]["red"] = classed
                if alarm := self.red_alarm(pr, row, classed):
                    stalls.append(alarm)
            if holding(pr):
                continue
            subject = f"#{pr} at {short(head)}"
            about = [record for record in reviews if grades.about_pr(record, repo, pr)]
            if going:
                below = downstack(rows, row)
                ready = min(max(at(go), actions.parse_stamp(go.get("graded_at") or go["at"])) for go in going)
                since = max(ready, head_at) if head_at else ready
                if not any(holding(parent) for parent in below) and (waits_on := self.unqueued(pr, row, rows, below, gos, since, queued, lane, records, own_row)):
                    waiting.setdefault(below[-1] if below else pr, []).append((pr, since, len(below), waits_on))
                continue
            state = grades.go_state(gos, pr, head)
            tap = (board.taps.get(pr) if board else None) or {}
            tapped = tap["head"] if tap.get("verdict") == "approved" and tap.get("head") and tap.get("at") else ""
            if tapped and grades.names_head(tapped, head):
                tapped_at = actions.parse_stamp(tap["at"])
                if self.now - tapped_at >= self.watch.age("tap"):
                    stalls.append(Stall(f"tap:{pr}:{short(head)}", "tap", tapped_at, subject, f"the owner approved it on the board at {pacific(tapped_at)}, and {state.said()}", tuple(go_lanes), f"post `go #{pr} at {short(head)}`", pr))
                continue
            if head_at is None:
                continue
            graded = grades.fidelity_grades_in(about, drive_name, fidelity_lanes, repo, pr)
            standing = grades.standing(graded, fidelity_lanes, head)
            reached = carried_heads(head, grades.fidelity_carries(graded))
            finders = [record for record in grades.finder_records(grades.finder_reviews_in(about, drive_name, repo, pr)) if any(grades.names_head(record["head"], sha) for sha in reached)]
            green = actions.parse_stamp(row["green_at"]) if row.get("green_at") else None
            ungraded = green is not None and self.now - green >= self.watch.age("ungraded")
            if not finders and ungraded:
                job = self.queued(FINDER_DESK, pr, head)
                step = f"run the finder desk's pass on {subject}, {'running' if job.status == 'started' else 'queued'} since {pacific(actions.parse_stamp(job.started_at or job.accepted_at))}, or say in `desk-runner.py show` why the queue holds it" if job else f"ask finder-desk for `finder: whole` on {subject}"
                stalls.append(Stall(f"finder:{pr}:{short(head)}", "ungraded", green, subject, f"its build has read green since {pacific(green)}, and no finder review names this head", (RUNNER if self.desk_runs(FINDER_DESK) else lane,), step, pr))
            elif not finders and self.now - head_at >= self.watch.age("finder"):
                if started := self.overran(FINDER_DESK, pr, head):
                    stalls.append(Stall(f"finder:{pr}:{short(head)}", "finder", started, subject, f"the finder desk's pass on this head has run since {pacific(started)}", (RUNNER,), f"read `desk-runner.py show`, then stop and requeue the finder pass on {subject}", pr))
                elif not self.finder_holds(pr, head) and self.desk_runs(FINDER_DESK):
                    stalls.append(Stall(f"finder:{pr}:{short(head)}", "finder", head_at, subject, f"no finder review names this head, and the finder desk holds no pass on it, first named {pacific(head_at)} in #{announced[0]['seq']}", (RUNNER,), f"read `desk-runner.py show` for why the finder desk queued no pass on {subject} from its head record", pr))
                elif not self.finder_holds(pr, head):
                    stalls.append(Stall(f"finder:{pr}:{short(head)}", "finder", head_at, subject, f"no finder review names this head, first named {pacific(head_at)} in #{announced[0]['seq']}", (lane,), f"run the finder (reference/finder-brief.md) on {subject} and post its review", pr))
            if not standing and fidelity_lanes and ungraded:
                stalls.append(Stall(f"fidelity:{pr}:{short(head)}", "ungraded", green, subject, f"its build has read green since {pacific(green)}, and no fidelity grade names this head", tuple(fidelity_lanes), f"grade {subject} and post its `fidelity: ok` or `fidelity: missing` line", pr))
            elif not standing and fidelity_lanes and self.now - head_at >= self.watch.age("fidelity"):
                if started := self.overran(FIDELITY_DESK, pr, head):
                    stalls.append(Stall(f"fidelity:{pr}:{short(head)}", "fidelity", started, subject, f"the fidelity desk's grade of this head has run since {pacific(started)}", (RUNNER,), f"read `desk-runner.py show`, then stop and requeue the fidelity grade of {subject}", pr))
                elif self.queued(FIDELITY_DESK, pr, head) is None:
                    stalls.append(Stall(f"fidelity:{pr}:{short(head)}", "fidelity", head_at, subject, f"no fidelity grade names this head, and the fidelity desk holds none queued, first named {pacific(head_at)} in #{announced[0]['seq']}", tuple(fidelity_lanes), f"grade {subject} and post its `fidelity: ok` or `fidelity: missing` line", pr))
            try:
                found = owner_waits.missing(standing, records, lambda pr=pr: self.comments(pr), fidelity_lanes)
            except ReadFailed as failed:
                self.warn({"missing", "regrade"}, str(failed))
                found = None
            if found and (stall := self.missing(pr, subject, lane, found)):
                stalls.append(stall)
            if board is None:
                continue
            taps = [(grades.go_heads(older)[pr], f"go #{older['seq']} by {older['lane']} at {grades.clock(older['at'])}")] if (older := state.older) else []
            if tapped:
                taps.append((tapped, "his board tap"))
            if taps:
                if not board.shows(pr, head) and self.now - head_at >= self.watch.age("carry") and not any(named == pr and grades.names_head(sha, head) for named, sha in carried):
                    questions.append(self.carry(pr, head, head_at, taps[-1], about, announced[-1], fidelity_lanes))
                continue
            if not row.get("green_at") or not (landing.get("merge_mode") == "every" or waits.get(pr) == f"merge:{pr}:{short(head)}"):
                continue
            finder_ok = [record for record in finders if record["grade"] == grades.FINDER_PASS]
            if not finder_ok or not standing or not all(grade["text"].startswith(grades.FIDELITY_OK) for grade in standing):
                continue
            if any(phrase.casefold() in grade["text"].casefold() for grade in standing for phrase in exempt):
                continue
            if any(record["lane"] in ROOT_NAMES and NO_ROW.search(grades.full_text(record)) and grades.about_pr(record, repo, pr) for record in records):
                continue
            ready = max(actions.parse_stamp(row["green_at"]), *(at(record) for record in [*finder_ok, *standing]))
            if board.shows(pr, head) or pr in self.hold_gates or self.now - ready < self.watch.age("board"):
                continue
            pulls = [record for record in records if record["lane"] in ROOT_NAMES and record["kind"] in ("decide", "decision") and grades.about_pr(record, repo, pr) and PULLED.search(grades.full_text(record))]
            if pulls and not any(record["seq"] > pulls[-1]["seq"] and grades.about_pr(record, repo, pr) and (record["kind"] == "ready" or READY.search(record["text"])) for record in records):
                continue
            card = f"merge:{pr}:{short(head)}"
            told = f"; the landing desk recorded its card {card} as posted, yet the board holds none" if card in cards else ""
            quiet = self.handled(records, pr)
            if not (quiet and self.watch.board_lane in ROOT_NAMES):
                boards.setdefault((downstack(rows, row) or [pr])[-1], []).append(Stall(f"board:{pr}:{short(head)}", "board", ready, subject, f"finder ok, fidelity ok and a green build since {pacific(ready)}, and the owner's board holds no row or card for it at this head{told}", (self.watch.board_lane,), f"put the merge row for {subject} on the owner's board", pr, quiet))
        stalls += self.probe_line(probes, self.probe_day)
        for group in boards.values():
            first, *rest = sorted(group, key=lambda stall: len(downstack(rows, rows[stall.pr])))
            above = f"; {', '.join(f'#{stall.pr}' for stall in rest)} above it in its stack wait on their rows too" if rest else ""
            quiet = all(stall.quiet for stall in group)
            stalls.append(Stall(first.id + (":quiet" if quiet else ""), first.kind, min(stall.since for stall in group), first.subject, first.what + above, first.owner, first.step + (f", and the rows for {', '.join(stall.subject for stall in rest)}" if rest else ""), first.pr, quiet))
        approved = {pr for go in gos for pr in grades.go_heads(go)} | {pr for pr, tap in (board.taps if board else {}).items() if tap.get("verdict") == "approved"}
        stalls += self.refusals(records, rows, approved, lanes, holding)
        for bottom, group in waiting.items():
            if any(member in self.refused for pr, *_ in group for member in [pr, *downstack(rows, rows[pr])]):
                continue
            group.sort(key=lambda entry: (entry[3][2].startswith(SILENT_STEP), entry[2]))
            blocker, owner, step = group[0][3]
            named = [f"#{pr}" for pr, *_ in group]
            listed = ", ".join(named[:LISTED]) + (f" and {len(named) - LISTED} more" if len(named) > LISTED else "")
            stack = f" in the stack from #{bottom}" if bottom != blocker or len(named) > 1 else ""
            sha = short(rows[blocker]["head"])
            first = group[0][0]
            cite = grades.go_state(gos, first, rows[first]["head"]).said()
            stalls.append(Stall(f"go:{blocker}:{sha}", "go", min(since for _, since, *_ in group), f"#{blocker} at {sha}", f"root go{'s name' if len(named) > 1 else ' names'} {listed}{stack} (#{first}: {cite}), and no QUEUED record does", owner, step, blocker))
        reds = [found for found in entries if "red" in found]
        if reds and self.live_lane(RED_SWEEP) is None and self.now - (oldest := min(actions.parse_stamp(found["red"]["since"]) for found in reds)) >= self.watch.age("sweep"):
            named = [f"#{found['pr']} ({found['red']['class']})" for found in reds]
            listed = ", ".join(named[:LISTED]) + (f" and {len(named) - LISTED} more" if len(named) > LISTED else "")
            stalls.append(Stall(f"sweep:{RED_SWEEP}", "sweep", oldest, f"{len(reds)} red PR{'' if len(reds) == 1 else 's'}", f"no live {RED_SWEEP} lane holds them: {listed}", (ROOT,), f"root spawns {RED_SWEEP} from the long-running skill's reference/red-sweep-brief.md"))
        return stalls, questions, entries

    def red_alarm(self, pr: str, row: dict, red: dict) -> Stall | None:
        """The `unclassed` stall of a red head no `red` record classifies after `stall.minutes.unclassed`, or the `unfixed` stall of one classed real whose PR has kept that head for `stall.minutes.unfixed` since."""
        sha = short(row["head"])
        subject = f"#{pr} at {sha}"
        since = actions.parse_stamp(red["since"])
        if red["class"] == "unclassed":
            if self.now - since < self.watch.age("unclassed"):
                return None
            return Stall(f"unclassed:{pr}:{sha}", "unclassed", since, subject, f"{red['checks']} red since {pacific(since)}, and no `red` record classifies it", (red["owner"],), f'classify it: `cci post --kind red --pr {pr} --text "class=<inherited|flake|real|stale> head={sha} owner=<lane> <what was done>"`', pr)
        if red["class"] != "real" or self.now - (classed := actions.parse_stamp(red["classed_at"])) < self.watch.age("unfixed"):
            return None
        return Stall(f"unfixed:{pr}:{sha}", "unfixed", classed, subject, f"{red['owner']} classed {red['checks']} a real failure at {pacific(classed)} and has pushed no new head: {clip(red['action'])}", (red["owner"],), f"root gives the fix of {subject} to a live lane unless {red['owner']} pushes it now", pr)

    def comments(self, pr: str) -> list[dict]:
        done = self.shell.run(ghread.read("pr", "view", pr, "--repo", self.watch.landing["repo"], "--json", "comments"))
        if done.code:
            raise ReadFailed(f"gh pr view {pr} exited {done.code}: {(done.err or done.out).strip()[:200]}")
        return json.loads(done.out)["comments"]

    def missing(self, pr: str, subject: str, lane: str, found: owner_waits.Missing) -> Stall | None:
        """A `fidelity: missing` grade standing at the head: unanswered for `stall.minutes.missing`, a stall to root and the PR's lane like a red build; answered by a lane record or a PR comment with no grade since for `stall.minutes.regrade`, a stall to the lane that graded it, or the runner's desks for the fidelity pipeline."""
        grade = found.grade
        graded_at = at(grade)
        said = f"fidelity grade cci {grade['num']} by {grade['lane']} reads missing since {pacific(graded_at)}: {clip(grade['text'])}"
        if found.answered_at is None:
            if self.now - graded_at < self.watch.age("missing"):
                return None
            return Stall(f"missing:{pr}:{grade['seq']}", "missing", graded_at, subject, f"{said}; nobody has answered it", (lane,), f"answer it: push the fix and post HEAD, or post `cci post --re {shlex.quote(grade['num'])}` saying why the plan holds", pr)
        if self.now - found.answered_at < self.watch.age("regrade"):
            return None
        grader = RUNNER if grade["lane"] == owner_waits.PIPELINE else grade["lane"]
        return Stall(f"regrade:{pr}:{grade['seq']}", "regrade", found.answered_at, subject, f"{said}; {found.answer} answered it at {pacific(found.answered_at)}, and no grade has followed", (grader,), f"grade {subject} again against that answer and post its `fidelity: ok` or `fidelity: missing` line", pr)

    def red(self, pr: str, row: dict, lane: str) -> Stall | None:
        """The red stall of `pr`'s head: its live owning lane's alone, reposted every `REPEAT`, or root's once per head when no live lane owns it; a red whose failed job logged only Buildkite's benign 404 probe is a `probe` stall the pass folds into one daily line."""
        required = self.watch.landing.get("trunk_check")
        failed = next((check for check in json.loads(row.get("failing_checks") or "[]") if check["name"] == required), None)
        if failed is None or not row.get("red_at") or self.now - (since := actions.parse_stamp(row["red_at"])) < self.watch.age("red"):
            return None
        subject = f"#{pr} at {short(row['head'])}"
        if failed.get("probe"):
            return Stall(f"probe:{pr}:{short(row['head'])}", "probe", since, subject, failed["url"], (lane,), "", pr)
        job = f" in job {failed['job']}" if failed.get("job") else ""
        error = f": {clip(failed['error'])}" if failed.get("error") else ""
        owned = None if lane in ROOT_NAMES or (last := self.lane_newest.get(lane)) is None or ended(last) else lane
        what = f"{required} has read failed{job} since {pacific(since)}{error} ({failed['url']})" + ("" if owned else f"; {lane} has posted nothing live in {int(self.watch.window.total_seconds() // 3600)} h")
        return Stall(f"red:{pr}:{short(row['head'])}{'' if owned else ':root'}", "red", since, subject, what, (owned,) if owned else (ROOT,), f"fix the red build of {subject} and push" if owned else f"assign the red build of {subject} to a live lane", pr)

    def probe_line(self, probes: list[Stall], posted: str) -> list[Stall]:
        """One line a day, to root, naming every red head whose failed job logged only Buildkite's benign 404 probe, so its owner reruns CI at that head."""
        day = self.now.astimezone(PACIFIC).date().isoformat()
        if not probes or posted == day:
            return []
        named = "; ".join(f"{stall.subject} ({stall.owner[0]}, {stall.what})" for stall in probes)
        return [Stall(f"probe:{day}", "probe", min(stall.since for stall in probes), f"{len(probes)} red heads", f"each failed job logged only Buildkite's benign step-export or meta-data 404 probe: {named}", (ROOT,), "each owner reruns CI at the same head; no code change")]

    @staticmethod
    def reassignments(records: list[dict]) -> dict[str, str]:
        """Each PR a root record names an owner for, to the lane the newest such record names: a reassignment such as `root reassigns #1 and #2 to lane-a; #3 to lane-b`, or an `owner=<lane>` field, which names every PR the record cites."""
        owners: dict[str, str] = {}
        for record in records:
            if record["lane"] not in ROOT_NAMES:
                continue
            if owner := dict(RED_FIELD.findall(record["text"])).get("owner"):
                owners |= dict.fromkeys({*PR_REF.findall(record["text"]), *(str(ref).rsplit("#", 1)[-1] for ref in record.get("refs", {}).get("prs", []))}, owner)
                continue
            if not REASSIGNS.search(record["text"]):
                continue
            for clause in CLAUSE.split(record["text"]):
                if lane := REASSIGNED_TO.findall(clause):
                    owners |= dict.fromkeys(PR_REF.findall(clause.rsplit(f"to {lane[-1]}", 1)[0]), lane[-1])
        return owners

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
            live = last is not None and not ended(last)
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
            quiet = self.handled(records, pr)
            if quiet and not live:
                continue
            owners = (found["actor"],) if live or found["actor"] == RUNNER else (ROOT,)
            stalls.append(Stall(f"stale:{pr}:{short(found['head'])}{':quiet' if quiet else ''}", "stale", since, subject, what, owners, found["next"], pr, quiet))
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

    def unqueued(self, pr: str, row: dict, rows: dict[str, dict], below: list[str], gos: list[dict], since: datetime, queued: set[tuple[str, str]], lane: str, records: list[dict], own_row: Callable[[str, str], bool]) -> tuple[str, tuple[str, ...], str] | None:
        """What holds a go'd `pr` out of the queue, with the lanes that own the step, or None when nothing is stuck: a PR below with no go whose next step is its own row's, a grade or the owner's tap, or a runner MERGE-ASK, is a wait its own row reports, never a stall."""
        if any(named == pr and grades.names_head(sha, row["head"]) for named, sha in queued) or self.now - since < self.watch.age("go"):
            return None
        for member in [*reversed(below), pr]:
            other = rows[member]
            named = f"#{member} at {short(other['head'])}"
            owns = lane if member == pr else other.get("lane") or ROOT
            if member != pr and not grades.gone_at(gos, member, other["head"]):
                return None if own_row(member, other["head"]) else (member, (ROOT,), f"{named}: {grades.go_state(gos, member, other['head']).said()}, and a stack enqueues from the bottom")
            if other.get("test_state") == "failure":
                return member, (owns,), f"fix the red build of {named}; the go waits on it"
            if other.get("mergeable_state") == "dirty":
                return member, (owns,), f"restack {named}: GitHub reads it as conflicting against its base"
            if other.get("test_state") != "success":
                return member, (owns,), f"{named} has no green build yet ({other.get('test_state') or 'no state'}); the go waits on it"
            if other.get("ai_review") == "neutral" and (waits := grades.review_bot_wait(self.review_title(other["head"]), owns)):
                return member, (owns,), f"{named}: {waits}"
        blocker = max((record for record in records if blocks_head(record, pr, row["head"]) and at(record) >= since), key=lambda record: record["seq"], default=None)
        if blocker is None:
            return self.stuck_step(entry, pr) if (entry := self.worker(pr, row["head"])) else self.silent(pr, row["head"], since)
        said = grades.full_text(blocker)
        reason = found[0] if (found := BLOCKER_LABEL.search(said)) else "blocked"
        if reason == MERGE_ASK or reason == FINDER_PENDING and self.finder_holds(pr, row["head"]):
            return None
        owners = tuple(dict.fromkeys(ROOT if to in ROOT_NAMES else to for to in (address.split("@", 1)[0] for address in blocker.get("to") or [ROOT])))
        return pr, owners, f"{reason}: the runner's newest blocker for #{pr} at {short(row['head'])}, cci #{blocker['seq']}: {clip(said)}"

    def worker(self, pr: str, head: str) -> dict | None:
        """The landing runner's stack worker entry that holds `pr` at `head`, from the `landing-stacks.json` the runner rewrites at every step."""
        try:
            stacks = json.loads(self.watch.stacks.read_text())["stacks"].values()
        except FileNotFoundError:
            return None
        return next((entry for entry in stacks if grades.names_head(entry["members"].get(pr, "-"), head)), None)

    def stuck_step(self, entry: dict, pr: str) -> tuple[str, tuple[str, ...], str] | None:
        """The worker step `entry` holds `pr` on past its budget; one inside its step's budget is no stall."""
        ran = self.now - actions.parse_stamp(entry["since"])
        if ran.total_seconds() <= entry["budget"]:
            return None
        command = f", running `{entry['command']}` for {entry['command_seconds']} s" if entry.get("command") else ""
        return pr, (RUNNER,), f"the landing runner's worker for the stack from #{entry['root']} has been on its {entry['step']} step for {int(ran.total_seconds() // 60)} min, past its {entry['budget'] // 60} min budget{command}"

    def silent(self, pr: str, head: str, since: datetime) -> tuple[str, tuple[str, ...], str] | None:
        """A go'd head that no runner record names and no stack worker holds `stall.minutes.silent` after its go is the runner's own defect, raised to the root and the landing desk beside the runner's `REFUSED unsettled`, so one defect cannot hide itself (owner, 5:16 AM Oct 30; root #208560)."""
        if self.now - since < self.watch.age("silent"):
            return None
        return pr, (ROOT, RUNNER), f"{SILENT_STEP}the landing runner wrote no QUEUED, MERGED or blocker record for #{pr} at {short(head)} since the go at {pacific(since)}, and no worker holds its stack, so its loop skipped an admitted head silently"

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

    def orders(self, records: list[dict]) -> list[Question]:
        """A question for each root decide record relaying an owner order, `stall.minutes.order` to `ORDER_WINDOW` old, unclosed, that no lane it names, or a numbered successor, has answered since with a record citing a PR: one Sonnet verdict says whether the order names a change that ships as a new PR, and a yes is root's urgent stall."""
        closed = {seq for record in records if record["lane"] in ROOT_NAMES for seq in grades.closes(record)}
        questions = []
        for order in records:
            text = grades.full_text(order)
            if order["lane"] not in ROOT_NAMES or order["kind"] != "decide" or order["seq"] in closed or not OWNER_ORDER.search(text):
                continue
            if not self.watch.age("order") <= self.now - at(order) < ORDER_WINDOW:
                continue
            lanes = [lane for lane in order.get("to") or [] if lane not in NOT_LANES]
            if any(record["seq"] > order["seq"] and cites_pr(record) and any(successor_of(lane).fullmatch(record["lane"]) for lane in lanes) for record in records):
                continue
            questions.append(self.order(order, text, lanes))
        return questions

    def order(self, order: dict, text: str, lanes: list[str]) -> Question:
        def settle(verdict: dict | None) -> Stall | None:
            if not verdict or not verdict.get("pr"):
                return None
            named = ", ".join(lanes) or "no lane"
            return Stall(f"order:{order['seq']}", "order", at(order), f"owner order #{order['seq']}", f"root relayed it at {pacific(at(order))} and it names a PR to open ({clip(verdict['why'])}), yet no record from {named} cites a PR since", (ROOT,), f"find why {named} has not opened the PR, and have it opened now")

        return Question(f"order:{order['seq']}", {"kind": "order", "order": clip(text, RECORD_CHARS)}, settle)

    def asks(self, records: list[dict]) -> list[Stall]:
        answered = {record["re"] for record in records if record["lane"] in ROOT_NAMES and record.get("re")}
        return [
            Stall(f"ask:{record['seq']}", "ask", at(record), f"{record['num']} from {record['lane']}", f"open and unanswered: {clip(record['text'])}", (ROOT,), f"answer it with `cci post --re {shlex.quote(record['num'])}`, or close it with `--resolves {shlex.quote(record['num'])}`")
            for record in records
            if record["kind"] == "ask"
            and record.get("status") == "open"
            and ROOT_NAMES & set(record.get("to") or [])
            and record["lane"] not in NOT_LANES
            and record["seq"] not in answered
            and not (record.get("topic") or "").startswith(NEEDS_LANE)
            and self.now - at(record) >= self.watch.age("ask")
        ]

    def picks(self, records: list[dict]) -> list[Stall]:
        """An owner pick or write-in board-watch asked root to route, `stall.minutes.pick` old, still open, and bound to no lane by a spawn, claim, lane record or answer naming it (root #225532)."""
        return [
            Stall(
                f"pick:{asked['seq']}",
                "pick",
                at(asked),
                f"owner pick {asked['topic'].removeprefix(NEEDS_LANE)}",
                f"board-watch asked root for a lane at {pacific(at(asked))} ({asked['num']}), and no spawn or claim names it: {clip(asked['text'])}",
                (ROOT,),
                f"spawn a lane whose brief names {asked['topic'].removeprefix(NEEDS_LANE)} or {asked['num']}, or have a lane post a `claim` naming it; close it with `cci post --resolves {shlex.quote(asked['num'])}`",
            )
            for asked in records
            if asked["lane"] == BOARD_WATCH
            and asked["kind"] == "ask"
            and (asked.get("topic") or "").startswith(NEEDS_LANE)
            and asked.get("status") == "open"
            and self.now - at(asked) >= self.watch.age("pick")
            and not any(record["seq"] > asked["seq"] and binds(record, asked) for record in records)
        ]


    def desks(self, records: list[dict]) -> list[Stall]:
        stalls = []
        for desk in drive.DESKS:
            beats = [record for record in records if record["lane"] == RUNNER and record["kind"] == "state" and record.get("topic") == desk and record["text"].startswith(f"{desk} alive ")]
            if not beats or self.now - at(beats[-1]) < self.watch.age("desk"):
                continue
            restart = beats[-1]["fields"].get("attrs", {}).get("restart") or f"desk-runner.py run --config <runner.json> --desk {desk}"
            stalls.append(Stall(f"desk:{desk}", "desk", at(beats[-1]), f"{desk} desk runner", f"the {desk} desk runner has posted no `{desk} alive` record since {pacific(at(beats[-1]))}", (ROOT,), f"restart it from the Run's bound terminal: `{restart}`"))
        return stalls

    def gates(self, records: list[dict]) -> list[Stall]:
        landed: dict[str, dict] = {}
        for record in records:
            if record["lane"] != RUNNER and not record["lane"].startswith(LANDING_DESK):
                continue
            if record["kind"] == "landed" and (gate := LANDED.match(record["text"])) or record["kind"] == "report" and QUEUED.match(record["text"]) and (gate := PR_REF.search(record["text"])):
                landed.setdefault(gate[1], record)
        waiting: dict[tuple[str, str], dict] = {}
        for record in records:
            if record["kind"] in ("state", "report") and record["lane"] not in NOT_LANES and not DESK.fullmatch(record["lane"]):
                for found in WAITS_ON.finditer(record["text"]):
                    waiting[(record["lane"], found[1] or found[2])] = record
        resolved = {seq for record in records for seq in cci.resolved(record)}
        stalls = []
        for (lane, pr), said in waiting.items():
            if not (gate := landed.get(pr)) or said["seq"] in resolved:
                continue
            since = max(at(said), at(gate))
            family = re.compile(rf"{re.escape(LANE_NUMBER.sub('', lane))}(?:-\d+)?")
            if self.now - since < self.watch.age("gate") or any(family.fullmatch(record["lane"]) and record["seq"] > said["seq"] and (record["kind"] in ("opened", "head") and at(record) >= since or record["kind"] in ("state", "report") and pr not in {found[1] or found[2] for found in WAITS_ON.finditer(record["text"])}) for record in records):
                continue
            hour = (self.now - since - self.watch.age("gate")) // GATE_REPEAT
            stalls.append(Stall(
                f"gate:{lane}:{pr}:{hour}", "gate", since, f"{lane} on #{pr}",
                f"{lane} said at cci #{said['seq']} that work waits on #{pr}, and {gate['lane']} posted it {'landed' if gate['kind'] == 'landed' else 'queued'} at cci #{gate['seq']}, {pacific(at(gate))}; {lane} has posted no OPENED or HEAD since",
                (lane,), f"open the work that waited on #{pr} and post OPENED or HEAD, or post why it still waits",
            ))
        return stalls

    def shapes(self) -> list[Stall]:
        if not self.entry:
            return []
        done = self.shell.run([sys.executable, str(SCRIPTS / "rulings.py"), "shapes", "--untested", "--json", "--drive", self.entry["drive"]])
        if done.code:
            self.warn({"shape"}, f"rulings.py shapes exited {done.code}: {(done.err or done.out).strip()[:300]}")
            return []
        rows = [row for row in json.loads(done.out) if self.now - actions.parse_stamp(row["updated_at"]) >= self.watch.age("shape")]
        if not rows:
            return []
        keys = sorted(row["key"] for row in rows)
        return [Stall(
            f"shape:{','.join(keys)}", "shape", min(actions.parse_stamp(row["updated_at"]) for row in rows), f"{len(keys)} shape rulings",
            f"no CI test is recorded for {', '.join(keys)}, each a ruling about how many processes, primaries or replicas run or what shares one",
            (ROOT,), f"{ROOT} orders a test that fails on today's code for each from the lane that builds it, recorded with `rulings.py shape <key> --test <repo>#<pr> <path>::<test>`, or `--not-shape <why>` for a row that names no shape",
        )]


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
