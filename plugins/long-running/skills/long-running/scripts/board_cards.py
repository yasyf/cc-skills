#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
"""Record the event that ends each question card on the owner's board, and take the card down once that event happens.

    board_cards.py until [--drive ID] CARD --until COND
    board_cards.py missing [--drive ID] [--session S] [CARD ...]
    board_cards.py sweep [--drive ID] [--session S] --lane LANE

COND is at:TIME, pr:N, release:STACK@ENV:COMMIT or record:SEQ (long_running/ends.py, the check retro rows share). `until` records a card's end before it is pushed, and refuses a missing, malformed or past condition; board_text_gate warns on a card pushed with none. `missing` prints each named card, or each card on the board, that has no recorded end, one id per line. `sweep` removes every card whose end has happened and posts one cci withdraw record per card naming the outcome, and prints PROBLEM for each card on the board with no end; the board lane runs it every cycle. A card whose owner answer has no ccn answer row stays up, with a PROBLEM line naming the answer, until `board_watch.py backfill` writes the row.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from long_running import answer_rows, drive, ends, ledger

PREFIX = "card/"
STATE_FILE = "board_cards.json"
NOT_CARDS = ("card-merge-triage",)


class Board:
    def __init__(self, entry: dict, session: str | None, shell: ledger.Shell):
        self.shell = shell
        self.cci = drive.cci_drive(entry)
        self.checkout = Path(entry["checkout"])
        self.session = session or drive.root_session(self.cci)
        self.path = Path(entry["state_dir"]) / STATE_FILE
        self.state = json.loads(self.path.read_text()) if self.path.is_file() else {}

    def ccn(self, *argv: str, stdin: str | None = None) -> str:
        return self.shell.run(["ccn", "-R", str(self.checkout), *argv], stdin=stdin)

    def present(self, *argv: str) -> str:
        return self.shell.run(["cc-present", *argv, "--session", self.session])

    def ledger(self) -> str:
        if not self.state.get("ledger"):
            self.state["ledger"] = json.loads(
                self.ccn("ledger", "add", f"board cards: {self.cci}", "--json")
            )["id"]
            drive.write_atomic(self.path, self.state)
        return self.state["ledger"]

    def ends(self) -> dict[str, dict[str, str]]:
        rows = json.loads(self.ccn("ledger", "show", self.ledger(), "--json"))["rows"]
        return {
            row["key"].removeprefix(PREFIX): row["fields"]
            for row in rows
            if row["key"].startswith(PREFIX) and not row["fields"].get("withdrawn")
        }

    def record(self, card: str, fields: dict[str, str]) -> None:
        self.ccn(
            "ledger",
            "sync",
            self.ledger(),
            "--file",
            "-",
            stdin=json.dumps(
                [
                    {
                        "key": PREFIX + card,
                        "fields": {**fields, "moved": ledger.utc_stamp()},
                    }
                ]
            ),
        )

    def cards(self) -> list[str]:
        doc = json.loads(self.present("outcomes", "--all"))["doc"]
        return [
            block["id"]
            for block in doc["blocks"]
            if block["type"] == "card" and block["id"] not in NOT_CARDS
        ]


def end_of(text: str) -> ends.End:
    moment = ledger.now()
    if why := ends.refusal(text, moment):
        raise SystemExit(f"board_cards: refused: {why}")
    return ends.parse(text, moment)


def main(argv: list[str] | None = None, shell: ledger.Shell | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--drive")
    parser.add_argument("--session")
    verbs = parser.add_subparsers(dest="verb", required=True)
    until = verbs.add_parser("until", help="record the event that ends a card")
    until.add_argument("card")
    until.add_argument("--until", required=True)
    missing = verbs.add_parser("missing", help="print each card with no recorded end")
    missing.add_argument("cards", nargs="*")
    sweep = verbs.add_parser("sweep", help="withdraw every card whose end has happened")
    sweep.add_argument("--lane", required=True)
    args = parser.parse_args(argv)
    found = drive.find(args.drive or drive.current_drive(), None)
    if not found:
        raise SystemExit(
            f"board_cards: no drive {args.drive or 'bound to this session'}; pass --drive"
        )
    board = Board(found, args.session, shell or ledger.Shell())
    if args.verb == "until":
        end = end_of(args.until)
        board.record(args.card, {"until": str(end), "set": ledger.utc_stamp()})
        print(f"{args.card} ends at {end}")
    elif args.verb == "missing":
        known = board.ends()
        print(
            "\n".join(card for card in args.cards or board.cards() if card not in known)
        )
    else:
        withdraw(board, args.lane)
    return 0


def withdraw(board: Board, lane: str) -> None:
    known = board.ends()
    moment = ledger.now()
    cards = board.cards()
    for card in cards:
        if card not in known:
            print(f"PROBLEM {card}: no end condition; record one with board_cards.py until {card} --until COND")
    waiting = {card: ends.parse(known[card]["until"], moment) for card in cards if card in known}
    facts = ends.Facts.read(list(waiting.values()), board.shell, board.checkout)
    for card, end in waiting.items():
        if not (outcome := facts.reached(end, moment)):
            continue
        if owed := answer_rows.unrowed(lambda argv: board.shell.run(["cc-present", *argv]), lambda argv: board.ccn(*argv), board.session, card):
            blocks = ", ".join(answer["payload"]["blockId"] for answer in owed)
            print(f"PROBLEM {card}: the owner's answer on {blocks} has no ccn answer row; run board_watch.py backfill --session {board.session} --program {board.cci} before it leaves the board")
            continue
        board.present("remove-block", card)
        board.shell.run(
            [
                "cci",
                "post",
                "--drive",
                board.cci,
                "--lane",
                lane,
                "--kind",
                "withdraw",
                "--topic",
                card,
                "--to",
                "root",
                "--text",
                f"Withdrew {card} from the owner's board: {outcome} (its end, {end}).",
            ]
        )
        board.record(
            card, {**known[card], "withdrawn": ledger.utc_stamp(), "outcome": outcome}
        )
        print(f"WITHDRAWN {card}: {outcome}")


if __name__ == "__main__":
    sys.exit(main())
