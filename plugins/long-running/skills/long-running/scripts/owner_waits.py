#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
"""What waits on the owner on the drive root's board, and what a PR that is not there really waits on.

    owner_waits.py --drive D [--json]
    owner_waits.py --drive D --pr N [--pr N]... [--json]
    cci owner-waits --drive D [--pr N]... [--json]

STDLIB ONLY. Reads the root's live board with `cc-present outcomes --all --session <root>`, the root
being the drive's newest session in the drive registry. With no `--pr` it prints one line per board
row that awaits his tap at the head it names, and one per pick he has not answered:

    tap <block> #<pr> at <head>, up since <time>
    pick <card> <choice>: <card title>, up since round <n>

or `Nothing waits on your tap or pick.` With `--pr` it reads the landing ledger's open rows, from the
`runner.json` beside the drive's state, and the drive's cci records about each PR, and prints one line
per PR: `#<pr> at <head>: waits on your tap on <block>, up since <time>` when a row at the PR's
ledger head awaits him, else `#<pr> at <head>: not waiting on you: <state>`, where the state says
which board row it has or that it has none, a red build, a conflict, a `fidelity: missing` grade
with the time it was posted and whether a lane record or a PR comment answered it since and no
grade followed, a missing grade, an open finder major, and an open parent it is stacked on.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from long_running import board_rows
from long_running import cci
from long_running import drive
from long_running import ghread
from long_running import grades

PACIFIC = ZoneInfo("America/Los_Angeles")
NOTHING = "Nothing waits on your tap or pick."
BOT_AUTHOR = "[bot]"
QUIET_LANES = frozenset({"desk-runner", "stall-watch", "board-watch", "finder-desk"})
ROOT_NAMES = frozenset({"root", "main"})
PIPELINE = "fidelity-pipeline"
TITLE_CHARS = 120
SNAPSHOT_HOURS = "24h"


@dataclass(frozen=True)
class Wait:
    kind: str
    block: str
    subject: str
    head: str
    since: str
    title: str = ""
    pr: str = ""

    def line(self) -> str:
        if self.kind == "tap":
            return f"tap {self.block} #{self.pr} at {self.head[:7]}, up since {self.since}"
        return f"pick {self.block} {self.subject}: {self.title}, up since {self.since}"


@dataclass(frozen=True)
class Row:
    block: str
    pr: str
    head: str


@dataclass(frozen=True)
class Voice:
    you: str
    your: str


OWNER = Voice("you", "your")
RUNNER = Voice("the owner", "the owner's")


@dataclass(frozen=True)
class Board:
    rows: list[Row]
    taps_by_pr: dict[str, dict]
    picks: list[Wait]
    shown: dict[str, datetime]

    def decided(self, row: Row) -> str:
        entry = self.taps_by_pr.get(row.pr)
        return next((verdict for verdict in ("approved", "rejected") if board_rows.at_head(entry, verdict, row.head)), "")

    def taps(self) -> list[Wait]:
        return [Wait("tap", row.block, f"#{row.pr}", row.head, self.since(f"row:{row.pr}:{row.head[:7]}"), pr=row.pr) for row in self.rows if row.head and not self.decided(row)]

    def since(self, key: str) -> str:
        return pacific(self.shown[key]) if key in self.shown else "just now"

    def waits(self) -> list[Wait]:
        return [*self.taps(), *self.picks]

    def tap_at(self, pr: str, head: str) -> Wait | None:
        return next((wait for wait in self.taps() if wait.pr == pr and grades.names_head(wait.head, head)), None)

    def rows_for(self, pr: str) -> list[Row]:
        return [row for row in self.rows if row.pr == pr and row.head]


@dataclass(frozen=True)
class Missing:
    grade: dict
    answer: str
    answered_at: datetime | None


def board_of(outcomes: dict, snapshots: list[dict]) -> Board:
    """The rows, verdicts and open picks of the board `cc-present outcomes --all` printed, a pick being a choice block of a card he picked no option on and wrote nothing into, each dated by the board-watch snapshots that first showed it."""
    doc = outcomes.get("doc") or {}
    interactions = outcomes.get("interactions") or {}
    picked = interactions.get("choices") or {}
    shown = first_shown(snapshots)
    picks = [
        Wait("pick", card["id"], choice["id"], "", pacific(shown[key]) if (key := f"card:{card['id']}") in shown else "just now", clip(card.get("title") or choice.get("prompt") or ""))
        for card in board_rows.typed(doc, "card")
        for choice in board_rows.typed(card, "choice")
        if not ((answer := picked.get(choice["id"]) or {}).get("optionIds") or answer.get("other"))
    ]
    rows = [Row(row.block, row.pr, row.head) for row in board_rows.rows_of(doc)]
    return Board(rows, board_rows.newest_by_pr(interactions.get("triage") or {}), picks, shown)


def board_of_records(records: list[dict]) -> Board | None:
    """The board as board-watch's records show it on any machine, from its newest `board-rows` snapshot, its `owner` taps and picks, and the runner's carries; None while it has posted no snapshot. A snapshot names no row's block, so each row's block reads empty."""
    if (snapshot := board_rows.snapshot_of_records(records)) is None:
        return None
    shown = first_shown([record for record in records if record.get("topic") == board_rows.SNAPSHOT_TOPIC])
    answered = board_rows.answered_of_records(records)
    picks = [Wait("pick", card, choice, "", pacific(shown[key]) if (key := f"card:{card}") in shown else "just now", card) for card, choices, _ in snapshot["cards"] for choice in choices if choice not in answered]
    return Board([Row("", pr, head) for pr, head, *_ in snapshot["rows"]], board_rows.newest_by_pr(board_rows.verdicts_of_records(records)), picks, shown)


def first_shown(snapshots: list[dict]) -> dict[str, datetime]:
    """When each row, as `row:<pr>:<head7>`, and each card, as `card:<id>`, of the newest board-watch snapshot went up: the first of the unbroken run of snapshots that show it."""
    shown: dict[str, datetime] = {}
    for record in sorted(snapshots, key=lambda record: record["seq"]):
        board = json.loads(board_rows.attrs(record).get("board") or "{}")
        keys = {f"row:{pr}:{head[:7]}" for pr, head, *_ in board.get("rows", [])} | {f"card:{card}" for card, *_ in board.get("cards", [])}
        shown = {key: shown.get(key, at(record)) for key in keys}
    return shown


def lanes_of(value: str | list[str]) -> list[str]:
    return [value] if isinstance(value, str) else list(value)


def clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= TITLE_CHARS else text[: TITLE_CHARS - 1].rstrip() + "…"


def pacific(moment: datetime) -> str:
    return moment.astimezone(PACIFIC).strftime("%-I:%M %p")


def pacific_text(stamp: str) -> str:
    return pacific(when) if stamp and (when := board_rows.parsed(stamp.replace("Z", "+00:00"))) else "an unrecorded time"


def at(record: dict) -> datetime:
    return datetime.fromisoformat(record["at"].replace("Z", "+00:00"))


def missing(standing: list[dict], records: list[dict], comments: Callable[[], list[dict]], fidelity_lanes: list[str]) -> Missing | None:
    """The newest `fidelity: missing` grade among the lanes' `standing` grades of a head that still holds it, as the landing runner reads them: no root record closes it, and a desk's ok outranks the pipeline's missing; with its first answer after it, a record of a lane other than a grader or desk that answers its seq by `--re` or names `#<seq>`, or a PR comment no bot wrote. `comments` is read only when such a grade stands."""
    desk_ok = any(grade["lane"] != PIPELINE and grade["text"].startswith(grades.FIDELITY_OK) for grade in standing)
    closed = {seq for record in records if record["lane"] in ROOT_NAMES for seq in grades.closes(record)}
    grade = max((grade for grade in standing if grade["text"].startswith(grades.FIDELITY_MISSING) and grade["seq"] not in closed and not (desk_ok and grade["lane"] == PIPELINE)), key=lambda record: record["seq"], default=None)
    if grade is None:
        return None
    graded_at = at(grade)
    answers = [
        (at(record), f"{record['lane']} in cci #{record['seq']}")
        for record in records
        if record["seq"] > grade["seq"] and not grades.delegated(record["lane"], fidelity_lanes) and record["lane"] not in QUIET_LANES and (record.get("re") == grade["seq"] or re.search(rf"#{grade['seq']}\b", record["text"]))
    ]
    answers += [
        (posted, f"a PR comment by {comment['author']['login']}")
        for comment in comments()
        if not (comment.get("author") or {}).get("login", "").endswith(BOT_AUTHOR) and (posted := datetime.fromisoformat(comment["createdAt"].replace("Z", "+00:00"))) > graded_at
    ]
    first = min(answers, default=None, key=lambda answer: answer[0])
    return Missing(grade, first[1] if first else "", first[0] if first else None)


def missing_words(found: Missing) -> str:
    grade = found.grade
    said = f"its fidelity grade by {grade['lane']} reads missing since {pacific(at(grade))} (cci #{grade['seq']})"
    if found.answered_at is None:
        return f"{said}, unanswered"
    return f"{said}; {found.answer} answered it at {pacific(found.answered_at)} and no grade has followed"


def state(pr: str, rows: dict[str, dict], board: Board, records: list[dict], comments: Callable[[], list[dict]], *, repo: str, drive_name: str, fidelity_lanes: list[str], voice: Voice = OWNER) -> str:
    """One line on what `pr` waits on: the owner's tap when a row at its ledger head awaits it, else every fact that holds it, in plain words addressed as `voice` says."""
    row = rows.get(pr)
    if row is None or not row.get("head"):
        return f"#{pr}: not waiting on {voice.you}: it is no open PR of this drive's landing ledger"
    head = row["head"]
    subject = f"#{pr} at {head[:7]}"
    if wait := board.tap_at(pr, head):
        return f"{subject}: waits on {voice.your} tap on {wait.block or 'its board row'}, up since {wait.since}"
    facts = [board_fact(pr, head, board, voice)]
    if row.get("test_state") == "failure":
        facts.append(f"its build is red since {pacific_text(row.get('red_at') or '')}")
    if row.get("mergeable_state") == "dirty":
        facts.append("it conflicts with its base")
    about = [record for record in records if record["drive"] == drive_name and grades.about_pr(record, repo, pr)]
    standing = grades.standing(grades.fidelity_grades_in(about, drive_name, fidelity_lanes, repo, pr), fidelity_lanes, head)
    if not standing:
        facts.append(f"no fidelity grade names {head[:7]}")
    elif found := missing(standing, records, comments, fidelity_lanes):
        facts.append(missing_words(found))
    finder = grades.newest_finder(grades.finder_records(grades.finder_reviews_in(about, drive_name, repo, pr)), head)
    if finder is not None and finder["grade"] == grades.FINDER_FAIL:
        facts.append(f"the finder found majors at {head[:7]} (cci #{finder['seq']})")
    if parents := downstack(rows, row):
        facts.append(f"it is stacked on open {' and '.join(f'#{parent}' for parent in parents)}")
    return f"{subject}: not waiting on {voice.you}: {'; '.join(facts)}"


def board_fact(pr: str, head: str, board: Board, voice: Voice) -> str:
    entry = board.taps_by_pr.get(pr) or {}
    if decided := board.decided(Row("", pr, head)):
        return f"{voice.you} {decided} it at {head[:7]}{' at ' + pacific_text(entry['at']) if entry.get('at') else ''}"
    rows = board.rows_for(pr)
    shown = f"its board row names {rows[-1].head[:7]}, not its head" if rows else "the board holds no row for it"
    return f"{shown}; {voice.you} {entry['verdict']} it at {entry['head'][:7]}, an older head" if entry.get("verdict") and entry.get("head") else shown


def downstack(rows: dict[str, dict], row: dict) -> list[str]:
    below: list[str] = []
    while parent := next((pr for pr, other in rows.items() if other.get("branch") and other["branch"] == row.get("base") and pr not in below), None):
        below.append(parent)
        row = rows[parent]
    return below


def pr_comments(run: Callable[[list[str]], subprocess.CompletedProcess], repo: str, pr: str) -> list[dict]:
    done = run(ghread.read("pr", "view", pr, "--repo", repo, "--json", "comments"))
    if done.returncode:
        raise ReadFailed(f"gh pr view {pr} exited {done.returncode}: {(done.stderr or done.stdout).strip()[:200]}")
    return json.loads(done.stdout)["comments"]


class ReadFailed(RuntimeError):
    pass


def run(argv: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(argv, capture_output=True, text=True, env=env)


def read_board(drive_name: str, session: str, runner: Callable[..., subprocess.CompletedProcess] = run) -> Board:
    done = runner(["cc-present", "outcomes", "--all", "--session", session])
    if done.returncode or (start := done.stdout.find("{")) < 0:
        raise ReadFailed(f"cc-present outcomes exited {done.returncode}: {(done.stderr or done.stdout).strip()[:200]}")
    snapshots = cci.records(tail(runner), drive_name, "--lane", board_rows.OWNER_LANE, f"--topic={board_rows.SNAPSHOT_TOPIC}", since=SNAPSHOT_HOURS)
    return board_of(json.loads(done.stdout[start:]), [record for record in snapshots if record["drive"] == drive_name])


def ledger_rows(landing: dict, drive_name: str, runner: Callable[..., subprocess.CompletedProcess] = run) -> dict[str, dict]:
    argv = [sys.executable, str(Path(__file__).resolve().parent / "ledger.py"), "-C", str(Path(landing["checkout"]).expanduser()), "list", "--ledger", landing["ledger"], "--open", "--json"]
    done = runner(argv, env=os.environ | {drive.DRIVE_ENV: drive_name})
    if done.returncode:
        raise ReadFailed(f"ledger.py list exited {done.returncode}: {(done.stderr or done.stdout).strip()[:200]}")
    return {row["pr"]: row for row in json.loads(done.stdout)}


def tail(runner: Callable[..., subprocess.CompletedProcess]) -> Callable[[list[str]], str]:
    def read(argv: list[str]) -> str:
        done = runner(argv)
        if done.returncode:
            raise ReadFailed(f"cci tail exited {done.returncode}: {(done.stderr or done.stdout).strip()[:200]}")
        return done.stdout

    return read


def records_about(drive_name: str, repo: str, pr: str, runner: Callable[..., subprocess.CompletedProcess] = run) -> list[dict]:
    return cci.records(tail(runner), drive_name, "--pr", f"{repo}#{pr}")


def main(argv: list[str] | None = None, runner: Callable[..., subprocess.CompletedProcess] = run) -> int:
    parser = argparse.ArgumentParser(prog="cci owner-waits", description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--drive", required=True)
    parser.add_argument("--pr", action="append", default=[], type=lambda value: value.lstrip("#"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if (session := drive.root_session(args.drive)) is None:
        print(f"owner-waits: no drive registry entry for {args.drive} names a root session", file=sys.stderr)
        return 2
    try:
        board = read_board(args.drive, session, runner)
        if not args.pr:
            waits = board.waits()
            if args.json:
                print(json.dumps([{"kind": wait.kind, "block": wait.block, "subject": wait.subject, "pr": wait.pr, "head": wait.head, "since": wait.since, "title": wait.title, "line": wait.line()} for wait in waits]))
            else:
                print("\n".join(wait.line() for wait in waits) or NOTHING)
            return 0
        entry = next(entry for entry in drive.drives() if drive.cci_drive(entry) == args.drive and entry.get("sessions"))
        landing = json.loads((Path(entry["state_dir"]) / "runner.json").read_text())["landing"]
        repo, lanes = landing["repo"], lanes_of(landing.get("fidelity_lane", []))
        rows = ledger_rows(landing, args.drive, runner)
        found = []
        for pr in args.pr:
            records = records_about(args.drive, repo, pr, runner)
            line = state(pr, rows, board, records, lambda pr=pr: pr_comments(runner, repo, pr), repo=repo, drive_name=args.drive, fidelity_lanes=grades.with_successors(lanes, records))
            head = rows.get(pr, {}).get("head", "")
            found.append({"pr": pr, "head": head, "waiting": bool(head and board.tap_at(pr, head)), "line": line})
    except ReadFailed as failed:
        print(f"owner-waits: {failed}", file=sys.stderr)
        return 1
    print(json.dumps(found) if args.json else "\n".join(item["line"] for item in found))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
