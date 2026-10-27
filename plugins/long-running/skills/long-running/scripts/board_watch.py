#!/usr/bin/env python3
"""The board recorder: one reader per cc-present board that keeps every owner interaction as a capture.

    board_watch.py start --session ID (--program SLUG | --drive ID) [--state-dir DIR]

STDLIB ONLY. `start` runs `cc-present watch --session ID` in the foreground until the board
closes; the `start_board_watch` hook launches it detached, once per board session. Each event
line is appended verbatim to `<state dir>/boards/<session>-events.jsonl`. Each owner interaction
is appended to `<state dir>/owner-prompts.jsonl` as one capture line,
`{"digest", "at", "session", "source": "board", "block", "text"}`, where `digest` is the first 16
hex digits of sha256 over `session`, `text` and `block` joined by NUL and `at` is the arrival
time in UTC. A triage row's note also carries the row's `row` id and `verdict`, and its digest
takes the row id in place of the block:

    write-in   choice.selected with `other`, and input.submitted: the text the owner typed
    note       feedback.created, the note on a decision.created, and each row note a triage.decided carries
    pick       choice.selected option ids, as the option labels in the board's current document
    verdict    decision.created: an approval's exact prompt, or `rejected` or `cleared`

A write-in a later pick replaces survives as its own capture. A capture already in the file is
not written again. A pick or approval whose block is missing from the board's document gets no
capture, so no option id or bare verdict stands in for the owner's words; its event line stays. The state directory resolves as `rulings.py` resolves it; `--state-dir`
overrides it. A second watch of the same board exits at once.

Each decision.created, choice.selected, input.submitted, feedback.created, triage.decided and
submit also reaches the drive's bus the moment it arrives, so no lane waits on a root to relay a
tap: one `cci post --kind owner --to all` from lane `board-watch` names the card title and block
id, the verdict, picked option labels or the owner's text verbatim, each merge row's head, and the
time, and resolves every open `ask`, `blocker` or `hold` whose text names the block, its card, or
the merge row (`merge-row-<pr>` or `merge:<pr>`). Its idempotency key is the event's line number
in the events file plus a hash of the line, so a restart that resumes from the watch's cursor
never posts an event twice.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import board_rows
import drive
import rulings

SOURCE = "board"
CAPTURES = "owner-prompts.jsonl"
CREDENTIAL = re.compile(
    r"\b(?:sk|pk|rk)-[\w-]{16,}"
    r"|\b(?:gh[pousr]|github_pat)_\w{20,}"
    r"|\bxox[abprs]-[\w-]{10,}"
    r"|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"
    r"|\b(?:api|sdk|mob)-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b"
    r"|\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}"
    r"|(?<![\w/+=.-])(?=[\w+/=]*[a-z])(?=[\w+/=]*[A-Z])(?=[\w+/=]*\d)[\w+/=]{32,}(?=[.,;:!?)\]\"'`]*(?:\s|$))"
)
REDACTED = "[credential redacted]"
BOARDS = "boards"
OWNER_EVENTS = frozenset({"decision.created", "choice.selected", "input.submitted", "feedback.created", "triage.decided", "submit"})
OPEN_KINDS = ("ask", "blocker", "hold")
BUS_LANE = "board-watch"
GREP_BYTES = 200000


@dataclass
class Bus:
    drive: str
    lines: int


class Shell:
    """The single side-effect boundary: the board's event stream, its document, and the clock."""

    def watch(self, session: str) -> Iterator[str]:
        with subprocess.Popen(["cc-present", "watch", "--session", session], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True) as child:
            yield from child.stdout

    def document(self, session: str, block: str) -> dict | None:
        argv = ["cc-present", "outcomes", "--session", session, "--block", block]
        done = subprocess.run(argv, capture_output=True, text=True)
        return json.loads(done.stdout).get("doc") if done.returncode == 0 else None

    def cci(self, argv: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(["cci", *argv], capture_output=True, text=True)

    def now(self) -> datetime:
        return datetime.now(UTC)


def stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def find(value: object, block: str) -> dict | None:
    match value:
        case {"id": found} if found == block and "type" in value:
            return value
        case dict():
            return next((hit for item in value.values() if (hit := find(item, block))), None)
        case list():
            return next((hit for item in value if (hit := find(item, block))), None)
    return None


def digest(session: str, text: str, block: str) -> str:
    return hashlib.sha256("\0".join([session, text, block]).encode()).hexdigest()[:16]


def picked(found: dict | None, ids: list[str]) -> str | None:
    labels = {option.get("id"): option.get("label") for option in (found or {}).get("options") or []}
    return "; ".join(labels[option] for option in ids) if all(labels.get(option) for option in ids) else None


def unread(session: str, block: str) -> None:
    print(f"board-watch {session}: {block} is not in the board's document, so its event stays only in the events file", flush=True)


def rendered(event: dict, session: str, doc: dict | None) -> str | None:
    block = event.get("blockId", "")
    match event:
        case {"type": "choice.selected", "optionIds": [_, *_] as ids}:
            text = picked(find(doc, block), ids)
        case {"type": "decision.created", "verdict": "approved"}:
            text = (find(doc, block) or {}).get("prompt")
        case {"type": "decision.created", "verdict": str() as verdict}:
            return verdict
        case _:
            return None
    if text is None:
        unread(session, block)
    return text


def texts(event: dict, session: str, doc: dict | None) -> list[str]:
    match event.get("type"):
        case "choice.selected":
            written = [event["other"]] if event.get("other") else []
        case "input.submitted" | "feedback.created":
            written = [event["text"]] if event.get("text") else []
        case "decision.created":
            written = [event["note"]] if event.get("note") else []
        case _:
            written = []
    verdict = rendered(event, session, doc)
    return [*([verdict] if verdict else []), *written] if event.get("type") == "decision.created" else [*written, *([verdict] if verdict else [])]


def captures(event: dict, session: str, doc: dict | None, moment: datetime) -> list[dict]:
    block = event.get("blockId", "")
    if event.get("type") == "triage.decided":
        return [
            {"digest": digest(session, row["note"], row_id), "at": stamp(moment), "session": session, "source": SOURCE, "block": block, "row": row_id, "verdict": row["verdict"], "text": CREDENTIAL.sub(REDACTED, row["note"])}
            for row_id, row in event["verdicts"].items()
            if row.get("note")
        ]
    return [
        {"digest": digest(session, text, block), "at": stamp(moment), "session": session, "source": SOURCE, "block": block, "text": CREDENTIAL.sub(REDACTED, text)}
        for text in texts(event, session, doc)
    ]


def card_of(doc: dict | None, block: str) -> dict | None:
    return next((top for top in (doc or {}).get("blocks", []) if find(top, block)), None)


def quoted(text: str) -> str:
    return f"\u201c{text}\u201d"


def said(verb: str, text: str | None) -> list[str]:
    return [verb, quoted(text)] if text else []


def told(event: dict, doc: dict | None) -> str:
    found = find(doc, event.get("blockId", ""))
    match event:
        case {"type": "triage.decided", "verdicts": verdicts}:
            heads = {row.id: row.head for row in board_rows.rows_of(doc or {})}
            return "; ".join(
                " ".join([row, entry["verdict"], *(["at", head] if (head := entry.get("head") or heads.get(row)) else []), *said("noted", entry.get("note"))])
                for row, entry in verdicts.items()
            )
        case {"type": "submit"}:
            return f"closed round {event.get('revision', 0)}"
        case {"type": "decision.created", "verdict": verdict}:
            prompt = (found or {}).get("prompt") if verdict == "approved" else None
            words = [verdict, *([quoted(prompt)] if prompt else []), *said("noted", event.get("note"))]
        case {"type": "choice.selected"}:
            ids = event.get("optionIds") or []
            words = [*(["picked", quoted(labels) if (labels := picked(found, ids)) else "option " + ", ".join(ids)] if ids else []), *said("wrote", event.get("other"))]
        case {"type": "input.submitted"}:
            words = said("wrote", event.get("text"))
        case _:
            words = said("noted", event.get("text"))
    return " ".join(words)


def announced(event: dict, session: str, doc: dict | None) -> str:
    block = event.get("blockId", "")
    title = (card_of(doc, block) or {}).get("title")
    where = f"{title} ({block})" if title else block or "the board"
    return CREDENTIAL.sub(REDACTED, f"Owner on board {session[:8]}, {where}: {told(event, doc)}, at {{at}}")


def row_names(row: str) -> list[str]:
    pr = board_rows.pr_of(row)
    return [*([] if row.isdigit() else [row]), *([f"merge-row-{pr}", f"merge:{pr}"] if pr else [])]


def named(event: dict, doc: dict | None) -> list[str]:
    if event["type"] == "triage.decided":
        return list(dict.fromkeys(name for row in event["verdicts"] for name in row_names(row)))
    block = event.get("blockId", "")
    return list(dict.fromkeys(name for name in (block, (card_of(doc, block) or {}).get("id")) if name))


def open_asks(shell: Shell, bus: Bus, names: list[str]) -> list[int]:
    seqs: set[int] = set()
    for name in names:
        pattern = re.compile(rf"(?<![\w-]){re.escape(name)}(?![\w-])", re.IGNORECASE)
        done = shell.cci(["grep", name, "--fixed-strings", "--drive", bus.drive, "--json", "--budget", str(GREP_BYTES), *(f"--kind={kind}" for kind in OPEN_KINDS)])
        found = [json.loads(line) for line in done.stdout.splitlines() if line.strip()]
        seqs |= {record["seq"] for record in found if record["kind"] in OPEN_KINDS and record["drive"] == bus.drive and record.get("status") == "open" and pattern.search(record["text"])}
    return sorted(seqs)


def announce(event: dict, line: str, session: str, doc: dict | None, shell: Shell, bus: Bus) -> None:
    key = f"board:{session}:{bus.lines}:{hashlib.sha256(line.encode()).hexdigest()[:8]}"
    resolves = open_asks(shell, bus, named(event, doc))
    prs = [pr for row in event.get("verdicts", {}) if (pr := board_rows.pr_of(row))]
    argv = [
        "post", "--drive", bus.drive, "--lane", BUS_LANE, "--kind", "owner", "--to", "all", "--id", key, "--text", announced(event, session, doc),
        *(["--topic", event["blockId"]] if event.get("blockId") else []),
        *(f"--pr={pr}" for pr in prs),
        *(["--resolves", ",".join(map(str, resolves))] if resolves else []),
    ]
    done = shell.cci(argv)
    if done.returncode:
        print(f"board-watch {session}: cci post {key} failed: {done.stderr.strip()}", flush=True)


def recorded(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {json.loads(line)["digest"] for line in path.read_text().splitlines() if line.strip()}


def free(lock) -> bool:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def record(line: str, state: Path, session: str, shell: Shell, seen: set[str], bus: Bus) -> None:
    with (state / BOARDS / f"{session}-events.jsonl").open("a") as events:
        events.write(line if line.endswith("\n") else f"{line}\n")
    bus.lines += 1
    if not line.strip():
        return
    event = json.loads(line)
    owner = event.get("type") in OWNER_EVENTS
    doc = shell.document(session, event["blockId"]) if owner and event.get("blockId") else None
    if owner:
        announce(event, line, session, doc, shell, bus)
    with (state / CAPTURES).open("a") as sink:
        for capture in captures(event, session, doc, shell.now()):
            if capture["digest"] not in seen:
                sink.write(json.dumps(capture, ensure_ascii=False) + "\n")
                seen.add(capture["digest"])


def cmd_start(args: argparse.Namespace, shell: Shell) -> int:
    state = Path(args.state_dir) if args.state_dir else rulings.state_dir(args)
    (state / BOARDS).mkdir(parents=True, exist_ok=True)
    with (state / BOARDS / f"{args.session}.lock").open("a+") as lock:
        if not free(lock):
            print(f"board-watch {args.session}: another watch holds {lock.name}")
            return 0
        seen = recorded(state / CAPTURES)
        events = state / BOARDS / f"{args.session}-events.jsonl"
        bus = Bus(args.program or drive.cci_drive(drive.load(drive.drives_dir() / f"{args.drive}.json")), events.read_text().count("\n") if events.exists() else 0)
        for line in shell.watch(args.session):
            record(line, state, args.session, shell, seen, bus)
    print(f"board-watch {args.session}: the board closed")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="board_watch.py", description=__doc__.splitlines()[0])
    verbs = parser.add_subparsers(dest="verb", required=True)
    start = verbs.add_parser("start", help="record one board's owner interactions until it closes; a second watch exits at once")
    start.add_argument("--session", required=True, metavar="ID")
    which = start.add_mutually_exclusive_group(required=True)
    which.add_argument("--program", metavar="SLUG")
    which.add_argument("--drive", metavar="ID")
    start.add_argument("--state-dir", metavar="DIR")
    start.set_defaults(handler=cmd_start)
    return parser


def main(argv: list[str] | None = None, shell: Shell | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args, shell or Shell())


if __name__ == "__main__":
    drive.run_installed(__file__)
    sys.exit(main())
