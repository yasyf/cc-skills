from __future__ import annotations

import json
from pathlib import Path

import pytest
import standing

RELEASE = "4ffc9a5" + "0" * 33
TICKETS = "104926a" + "0" * 33
LIVE = {RELEASE, TICKETS}
REGISTER = {"id": "0cf17c9" + "0" * 33, "body": "# Register\n\n1. Release as it merges, never on the owner's word (4ffc9a5).\n"}
CARRIED = standing.section_of(REGISTER, ["- R312 deploy every landing"])
UNQUOTED = "the standing rules section does not name register `0cf17c9`; regenerate the handoff with `handoff.py generate`"


def test_a_handoff_quoting_the_register_passes() -> None:
    body = CARRIED + "\n## Owner asks\nSoFi releases as it merges (4ffc9a5), never on the owner's word\n"

    assert standing.lint(body, CARRIED, REGISTER, LIVE) == []


def test_a_handoff_without_the_section_fails() -> None:
    [missing, unquoted] = standing.lint("## Owner asks\nnone\n", None, REGISTER, LIVE)

    assert missing.startswith("no `## Standing owner rules` section")
    assert unquoted == UNQUOTED


def test_a_section_without_the_register_fails() -> None:
    assert standing.lint("## Standing owner rules\n- R312 deploy every landing\n", None, REGISTER, LIVE) == [UNQUOTED]
    assert standing.lint("## Standing owner rules\n- R312 deploy every landing\n", None, None, LIVE) == []
    assert standing.lint("## Standing owner rules\n\n  > # Register\n", None, REGISTER, LIVE) == [UNQUOTED]


def test_dropping_a_carried_rule_needs_a_superseded_by_line() -> None:
    dropped = standing.section_of(REGISTER, [])
    superseded = standing.section_of(REGISTER, ["- R312 superseded by R575"])

    assert standing.lint(dropped, CARRIED, REGISTER, LIVE) == [
        "dropped `R312` since the previous handoff (- R312 deploy every landing); carry it, or write "
        "`- R312 superseded by <id>`"
    ]
    assert standing.lint(superseded, CARRIED, REGISTER, LIVE) == []
    assert standing.lint(dropped, superseded, REGISTER, LIVE) == []


@pytest.mark.parametrize(
    ("line", "flagged"),
    [
        ("real sanddb on SoFi (owner's word)", True),
        ("needs owner approval before enqueue", True),
        ("the SoFi release still requires owner sign-off", True),
        ("Card 1: reserved for the owner", True),
        ("released as it merges, no owner's word gate (4ffc9a5)", False),
        ("CODEOWNER approvals on #28756", False),
        ("owner approval missing (deadbee)", True),
    ],
)
def test_an_owner_gate_line_must_cite_a_live_answer(line: str, flagged: bool) -> None:
    problems = standing.lint(CARRIED + "\n## Asks\n" + line + "\n", None, REGISTER, LIVE)

    assert problems == ([f"owner-gate line cites no live answer id: {line}"] if flagged else [])


def test_cli_lint_reads_the_register(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    def ccn(repo: str, *args: str) -> str:
        if args[:2] == ("doc", "show"):
            return json.dumps({"id": args[2], "body": "## Standing owner rules\n- none\n"})
        return json.dumps([{"id": RELEASE}, {"id": TICKETS}])

    monkeypatch.setattr(standing, "ccn", ccn)
    monkeypatch.setattr(standing.rulings, "register", lambda shell, repo, program: REGISTER)

    assert standing.main(["lint", "--doc", "abc", "--program", "v3"]) == standing.VIOLATIONS
    assert capsys.readouterr().out == UNQUOTED + "\n"

    (tmp_path / "handoff.md").write_text(CARRIED)
    assert standing.main(["lint", "--file", str(tmp_path / "handoff.md"), "--program", "v3"]) == 0


def standing_records(*records: dict):
    def run(argv: list[str]) -> str:
        assert argv[:3] == ["cci", "tail", "--drive"] and argv[3] == "brook"
        assert argv[-3:] == ["--topic=standing", "--kind=go", "--kind=correction"]
        since = int(argv[argv.index("--since") + 1])
        return "".join(json.dumps(record) + "\n" for record in records if record["seq"] > since)

    return run


def test_a_standing_rule_stays_live_until_a_correction_replaces_it() -> None:
    found = standing.read(
        standing_records(
            {"seq": 4, "kind": "go", "text": "deploy every landing", "refs": {"ccn": "4ffc9a5"}},
            {"seq": 7, "kind": "go", "text": "dev is always releasable", "refs": {}},
            {"seq": 9, "kind": "correction", "re": 4, "text": "deploy every landing within five minutes", "refs": {"ccn": "87833ea"}},
        ),
        "brook",
    )
    assert found.rules == {"#7": "#7 dev is always releasable", "#9": "#9 deploy every landing within five minutes"}
    assert found.sources == {"#7": "cci #7", "#9": "ccn 87833ea"}
    assert found.superseded == {"#4": "#9"}


def test_a_carried_cci_rule_id_is_a_bullet_id() -> None:
    assert standing.carried(["- #9 deploy every landing within five minutes [ccn 87833ea]"]) == {"#9": "- #9 deploy every landing within five minutes [ccn 87833ea]"}
