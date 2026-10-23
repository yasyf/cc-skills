#!/usr/bin/env python3
"""The board recorder: one reader per cc-present board that keeps every owner interaction as a capture.

    board_watch.py start --session ID (--program SLUG | --drive ID) [--state-dir DIR]

STDLIB ONLY. `start` runs `cc-present watch --session ID` in the foreground until the board
closes; the `start_board_watch` hook launches it detached, once per board session. Each event
line is appended verbatim to `<state dir>/boards/<session>-events.jsonl`. Each owner interaction
is appended to `<state dir>/owner-prompts.jsonl` as one capture line,
`{"digest", "at", "session", "source": "board", "block", "text"}`, where `digest` is the first 16
hex digits of sha256 over `session`, `text` and `block` joined by NUL and `at` is the arrival
time in UTC:

    write-in   choice.selected with `other`, and input.submitted: the text the owner typed
    note       feedback.created, and the note on a decision.created
    pick       choice.selected option ids, as the option labels in the board's current document
    verdict    decision.created: an approval's exact prompt, or `rejected` or `cleared`

A write-in a later pick replaces survives as its own capture. A capture already in the file is
not written again. A pick or approval whose block is missing from the board's document gets no
capture, so no option id or bare verdict stands in for the owner's words; its event line stays. The state directory resolves as `rulings.py` resolves it; `--state-dir`
overrides it. A second watch of the same board exits at once.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import rulings

SOURCE = "board"
CAPTURES = "owner-prompts.jsonl"
BOARDS = "boards"


class Shell:
    """The single side-effect boundary: the board's event stream, its document, and the clock."""

    def watch(self, session: str) -> Iterator[str]:
        with subprocess.Popen(["cc-present", "watch", "--session", session], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True) as child:
            yield from child.stdout

    def block(self, session: str, block: str) -> dict | None:
        argv = ["cc-present", "outcomes", "--session", session, "--block", block]
        done = subprocess.run(argv, capture_output=True, text=True)
        return find(json.loads(done.stdout).get("doc"), block) if done.returncode == 0 else None

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


def rendered(event: dict, session: str, shell: Shell) -> str | None:
    block = event.get("blockId", "")
    match event:
        case {"type": "choice.selected", "optionIds": [_, *_] as ids}:
            text = picked(shell.block(session, block), ids)
        case {"type": "decision.created", "verdict": "approved"}:
            text = (shell.block(session, block) or {}).get("prompt")
        case {"type": "decision.created", "verdict": str() as verdict}:
            return verdict
        case _:
            return None
    if text is None:
        unread(session, block)
    return text


def texts(event: dict, session: str, shell: Shell) -> list[str]:
    match event.get("type"):
        case "choice.selected":
            written = [event["other"]] if event.get("other") else []
        case "input.submitted" | "feedback.created":
            written = [event["text"]] if event.get("text") else []
        case "decision.created":
            written = [event["note"]] if event.get("note") else []
        case _:
            written = []
    verdict = rendered(event, session, shell)
    return [*([verdict] if verdict else []), *written] if event.get("type") == "decision.created" else [*written, *([verdict] if verdict else [])]


def captures(event: dict, session: str, shell: Shell, moment: datetime) -> list[dict]:
    block = event.get("blockId", "")
    return [
        {"digest": digest(session, text, block), "at": stamp(moment), "session": session, "source": SOURCE, "block": block, "text": text}
        for text in texts(event, session, shell)
    ]


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


def record(line: str, state: Path, session: str, shell: Shell, seen: set[str]) -> None:
    with (state / BOARDS / f"{session}-events.jsonl").open("a") as events:
        events.write(line if line.endswith("\n") else f"{line}\n")
    if not line.strip():
        return
    with (state / CAPTURES).open("a") as sink:
        for capture in captures(json.loads(line), session, shell, shell.now()):
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
        for line in shell.watch(args.session):
            record(line, state, args.session, shell, seen)
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
    sys.exit(main())
