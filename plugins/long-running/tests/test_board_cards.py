from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import board_cards
import pytest
from long_running import ends

DRIVE = "brook-cci"
NOW = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
LIVE = "api (platform) on plat-usw2-prod: outside a release at 0dfbd44e165e, applied 3h0m ago\n"


class FakeShell:
    def __init__(
        self,
        cards: list[str],
        prs: dict[str, str] | None = None,
        closed: set[str] = frozenset(),
        ancestors: set[tuple[str, str]] = frozenset(),
    ):
        self.cards = cards
        self.prs = prs or {}
        self.closed = closed
        self.ancestors = ancestors
        self.rows: dict[str, dict[str, str]] = {}
        self.removed: list[str] = []
        self.posted: list[str] = []
        self.status_reads = 0

    def run(
        self, argv: list[str], stdin: str | None = None, cwd: Path | None = None
    ) -> str:
        match argv:
            case ["ccn", "-R", _, "ledger", "add", *_]:
                return json.dumps({"id": "c4rd5"})
            case ["ccn", "-R", _, "ledger", "show", *_]:
                return json.dumps(
                    {
                        "rows": [
                            {"key": key, "fields": fields}
                            for key, fields in self.rows.items()
                        ]
                    }
                )
            case ["ccn", "-R", _, "ledger", "sync", *_]:
                for row in json.loads(stdin or "[]"):
                    self.rows[row["key"]] = row["fields"]
                return ""
            case ["cc-present", "outcomes", *_]:
                blocks = [
                    {"id": "sec-switch", "type": "markdown"},
                    {"id": "card-merge-triage", "type": "card"},
                    *({"id": card, "type": "card"} for card in self.cards),
                ]
                return json.dumps({"doc": {"blocks": blocks}})
            case ["cc-present", "remove-block", card, *_]:
                self.removed.append(card)
                return ""
            case ["ccx", "vcs", "pr", "status", "--json", "-R", _, *numbers]:
                self.status_reads += 1
                return json.dumps([{"number": int(number), "state": self.prs.get(number, "OPEN")} for number in numbers])
            case ["cci", "show", seq]:
                return (
                    f"#{seq} ASK root -> lane text"
                    + (" [RESOLVED #9]" if seq in self.closed else "")
                    + "\n"
                )
            case ["cci", "post", *_]:
                self.posted.append(argv[-1])
                return "#10\n"
            case ["tools/ci", "release", "live"]:
                return LIVE
            case ["git", "-C", _, "merge-base", "--is-ancestor", commit, live]:
                if (commit, live) not in self.ancestors:
                    raise subprocess.CalledProcessError(1, argv)
                return ""
        raise AssertionError(f"unexpected call {argv}")


@pytest.fixture(autouse=True)
def entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    found = {
        "drive": "brook",
        "cci_drive": DRIVE,
        "state_dir": str(tmp_path),
        "checkout": str(tmp_path),
    }
    monkeypatch.setattr(
        board_cards.drive, "find", lambda name, session, root=None: found
    )
    monkeypatch.setattr(board_cards.drive, "root_session", lambda name: "s1")
    monkeypatch.setattr(board_cards.ledger, "now", lambda: NOW)
    return found


@pytest.mark.parametrize(
    ("given", "stored"),
    [
        ("at:4h", "at:2026-11-02T12:00:00Z"),
        ("pr:47440", "pr:Forge-AI/monorepo#47440"),
        ("pr:yasyf/cc-skills#1691", "pr:yasyf/cc-skills#1691"),
        ("release:api@plat-usw2-prod:0dfbd44e", "release:api@plat-usw2-prod:0dfbd44e"),
        ("record:#308624", "record:308624"),
    ],
)
def test_until_records_the_event_that_ends_a_card(given, stored):
    shell = FakeShell(["card-a"])

    assert board_cards.main(["until", "card-a", "--until", given], shell) == 0

    assert shell.rows["card/card-a"]["until"] == stored


@pytest.mark.parametrize(
    ("given", "said"),
    [
        ("soon", "names no end condition"),
        ("when Anubhav's release is done", "names no end condition"),
        ("pr:abc", "is not an end condition"),
        ("at:2026-11-02T07:00:00Z", "has already passed"),
    ],
)
def test_until_refuses_a_card_with_no_checkable_end(given, said):
    """Owner 12:03 AM Nov 2: a card about a release that happened 12 hours ago was still on the board."""
    shell = FakeShell(["card-a"])

    with pytest.raises(SystemExit) as refused:
        board_cards.main(["until", "card-a", "--until", given], shell)

    assert said in str(refused.value.code)
    assert shell.rows == {}


def test_missing_lists_board_cards_with_no_end_and_skips_the_merge_list(capsys):
    shell = FakeShell(["card-a", "card-b"])
    board_cards.main(["until", "card-a", "--until", "pr:1"], shell)
    capsys.readouterr()

    board_cards.main(["missing"], shell)

    assert capsys.readouterr().out.split() == ["card-b"]


def test_sweep_withdraws_each_card_whose_event_happened_and_names_the_outcome(
    capsys, monkeypatch
):
    shell = FakeShell(
        [
            "merged-pr",
            "open-pr",
            "past-time",
            "future-time",
            "closed-record",
            "shipped",
            "unshipped",
            "no-end",
        ],
        prs={"1": "MERGED"},
        closed={"7"},
        ancestors={("0dfbd44e", "0dfbd44e165e")},
    )
    for card, until in [
        ("merged-pr", "pr:1"),
        ("open-pr", "pr:2"),
        ("past-time", "at:1h"),
        ("future-time", "at:3h"),
        ("closed-record", "record:7"),
        ("shipped", "release:api@plat-usw2-prod:0dfbd44e"),
        ("unshipped", "release:api@plat-usw2-prod:1234567"),
    ]:
        board_cards.main(["until", card, "--until", until], shell)
    capsys.readouterr()
    monkeypatch.setattr(board_cards.ledger, "now", lambda: NOW.replace(hour=10))

    board_cards.main(["sweep", "--lane", "board-lane"], shell)

    assert shell.removed == ["merged-pr", "past-time", "closed-record", "shipped"]
    assert shell.status_reads == 1
    assert (
        "Withdrew merged-pr from the owner's board: Forge-AI/monorepo#1 is merged"
        in shell.posted[0]
    )
    assert "PROBLEM no-end: no end condition" in capsys.readouterr().out
    assert shell.rows["card/merged-pr"]["withdrawn"]


def test_a_withdrawn_card_pushed_again_needs_a_new_end(capsys):
    shell = FakeShell(["merged-pr"], prs={"1": "MERGED"})
    board_cards.main(["until", "merged-pr", "--until", "pr:1"], shell)
    board_cards.main(["sweep", "--lane", "board-lane"], shell)
    capsys.readouterr()

    board_cards.main(["missing", "merged-pr"], shell)

    assert capsys.readouterr().out.split() == ["merged-pr"]


def test_retro_due_times_read_through_the_same_check():
    assert ends.refusal("at:90m", NOW, kinds=("at",)) is None
    assert "is a pr condition" in ends.refusal("pr:1", NOW, kinds=("at",))
    assert "no end condition" in ends.refusal("", NOW)
