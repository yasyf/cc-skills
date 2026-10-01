from __future__ import annotations

import json
from pathlib import Path

import pytest
import standing

RELEASE = {"id": "4ffc9a5" + "0" * 33, "title": "When does a merged change get released?", "tags": ["scope:durable", "v3"]}
TICKETS = {"id": "104926a" + "0" * 33, "title": "Does the owner ever owe an approval click?", "tags": ["scope:durable", "v3"]}
LIVE = {RELEASE["id"], TICKETS["id"]}
CARRIED = f"## Standing owner rules\n- 4ffc9a5 {RELEASE['title']}\n- 104926a {TICKETS['title']}\n- R312 deploy every landing\n"


def test_a_standing_line_stays_live_until_superseded_by_id() -> None:
    inbox = standing.read_inbox(
        [
            "R311 deploy everything at dev head now",
            "R312 (standing): every landed PR is deployed in the same pass it lands",
            "R313 (standing) the landing desk is the single enqueuer",
            "R400 R313 superseded by R399",
        ]
    )

    assert inbox.live() == {"R312": "R312 (standing): every landed PR is deployed in the same pass it lands"}
    assert inbox.violations == []


@pytest.mark.parametrize(
    "line",
    [
        "R311/R312 (standing) deploy now, and deploy every landing",
        "R311 deploy everything now; R312 (standing) deploy every landing",
        "- rulings L65–L107 (standing)",
    ],
)
def test_a_standing_tag_sharing_a_line_is_a_violation(line: str) -> None:
    inbox = standing.read_inbox([line])

    assert inbox.live() == {}
    assert inbox.violations == [f"1: `(standing)` must follow the line's own single id: {line}"]


def test_marking_a_standing_rule_done_is_a_violation() -> None:
    inbox = standing.read_inbox(["R312 (standing) deploy every landing", "R348 receiver at 340f5f37b8 live — R312 done"])

    assert inbox.live() == {"R312": "R312 (standing) deploy every landing"}
    assert inbox.violations == ["2: standing rule R312 is marked done; it ends only with `R312 superseded by <id>`"]


def test_done_on_a_one_off_is_fine() -> None:
    assert standing.read_inbox(["R311 deploy everything now", "R348 R311 done"]).violations == []


def test_a_handoff_carrying_every_rule_passes() -> None:
    body = CARRIED + "\n## Owner asks\nSoFi releases as it merges (4ffc9a5), never on the owner's word\n"

    assert standing.lint(body, CARRIED, [RELEASE, TICKETS], LIVE) == []


def test_a_handoff_without_the_section_fails() -> None:
    [missing, title] = standing.lint("## Owner asks\nnone\n", None, [RELEASE], LIVE)

    assert missing.startswith("no `## Standing owner rules` section")
    assert title.startswith(f"missing durable rule `- 4ffc9a5 {RELEASE['title']}`")


def test_a_re_summarized_title_fails() -> None:
    body = "## Standing owner rules\n- 4ffc9a5 release everything as it merges\n"

    assert standing.lint(body, None, [RELEASE], LIVE) == [
        f"missing durable rule `- 4ffc9a5 {RELEASE['title']}`; copy the title verbatim, never re-summarized"
    ]


def test_dropping_a_carried_rule_needs_a_superseded_by_line() -> None:
    dropped = f"## Standing owner rules\n- 4ffc9a5 {RELEASE['title']}\n- 104926a {TICKETS['title']}\n"
    superseded = dropped + "- R312 superseded by R575\n"

    assert standing.lint(dropped, CARRIED, [RELEASE, TICKETS], LIVE) == [
        "dropped `R312` since the previous handoff (- R312 deploy every landing); carry it, or write "
        "`- R312 superseded by <id>`"
    ]
    assert standing.lint(superseded, CARRIED, [RELEASE, TICKETS], LIVE) == []
    assert standing.lint(dropped, superseded, [RELEASE, TICKETS], LIVE) == []


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
    problems = standing.lint(CARRIED + "\n## Asks\n" + line + "\n", None, [RELEASE, TICKETS], LIVE)

    assert problems == ([f"owner-gate line cites no live answer id: {line}"] if flagged else [])


def test_cli_titles_and_lint_read_cc_notes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    def ccn(repo: str, *args: str) -> str:
        if args[:2] == ("doc", "show"):
            return json.dumps({"id": args[2], "body": "## Standing owner rules\n- none\n"})
        labels = [args[i + 1] for i, arg in enumerate(args) if arg == "--label"]
        return json.dumps([answer for answer in (RELEASE, TICKETS) if all(label in answer["tags"] for label in labels)])

    monkeypatch.setattr(standing, "ccn", ccn)

    assert standing.main(["titles", "--program", "v3"]) == 0
    assert capsys.readouterr().out == f"- 4ffc9a5 {RELEASE['title']}\n- 104926a {TICKETS['title']}\n"

    assert standing.main(["lint", "--doc", "abc", "--program", "v3"]) == standing.VIOLATIONS
    assert capsys.readouterr().out.count("missing durable rule") == 2

    (tmp_path / "handoff.md").write_text(CARRIED)
    assert standing.main(["lint", "--file", str(tmp_path / "handoff.md"), "--program", "v3"]) == 0


def test_cli_inbox_prints_live_ids_and_exits_on_violations(tmp_path: Path, capsys) -> None:
    inbox = tmp_path / "orca-desk.md"
    inbox.write_text("R40 (standing) hand-apply every landing\nR41 R40 done\nR42 (standing) one enqueuer\n")

    assert standing.main(["inbox", str(inbox)]) == standing.VIOLATIONS
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "live standing: R40, R42"
    assert out[-1] == f"violation {inbox}:2: standing rule R40 is marked done; it ends only with `R40 superseded by <id>`"


def test_a_provenance_parenthetical_ending_in_standing_is_a_standing_line() -> None:
    line = "- R575 (root, 05:0xZ Oct 2 label / 14:50Z, standing) → all desks: owner-word gates are superseded by 4ffc9a5"

    inbox = standing.read_inbox([line])

    assert inbox.live() == {"R575": line}
    assert inbox.violations == []


def test_a_later_supersede_clears_an_earlier_done_mark() -> None:
    lines = ["R312 (standing) deploy every landing", "R348 receiver live — R312 done", "- R591 R312 superseded by G122"]

    inbox = standing.read_inbox(lines)

    assert inbox.live() == {}
    assert inbox.violations == []


def test_a_done_mark_after_the_supersede_is_still_flagged() -> None:
    lines = ["R312 (standing) deploy every landing", "R591 R312 superseded by G122", "R600 R312 done"]

    assert standing.read_inbox(lines).violations == [
        "3: standing rule R312 is marked done; it ends only with `R312 superseded by <id>`"
    ]


def test_a_tag_quoted_in_prose_is_not_a_misplaced_tag() -> None:
    line = "- R575 (root, 14:50Z, standing) → all desks: standing rules get their own `(standing)` ID line"

    inbox = standing.read_inbox([line, "R576 note: a line tagged (standing) never shares its id"])

    assert inbox.live() == {"R575": line}
    assert inbox.violations == []


def test_a_later_standing_line_that_supersedes_a_rule_ends_it() -> None:
    lines = [
        "- G115 (root, 14:30Z, binding, standing) release everything as we merge it, on the owner's word",
        "- G138 (standing) supersedes G115 as the cited form: no owner-word gate on any release, answer 4ffc9a5",
    ]

    inbox = standing.read_inbox(lines)

    assert inbox.live() == {"G138": lines[1].strip()}
    assert inbox.at == {"G115": 1, "G138": 2}
    assert inbox.violations == []


def test_restating_a_standing_rule_under_its_own_id_replaces_it() -> None:
    lines = ["- R576 (standing) no PR waits for an owner click", "- R576 (standing) no PR waits for an owner click (answer 104926a)"]

    inbox = standing.read_inbox(lines)

    assert inbox.live() == {"R576": lines[1]}
    assert inbox.at == {"R576": 2}
