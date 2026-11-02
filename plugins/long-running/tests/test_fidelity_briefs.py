"""The fidelity desk brief's grade lines against what the landing runner reads, and the fixture set against its README."""

from __future__ import annotations

import re
from pathlib import Path

from long_running import grades
import pytest

SKILL = Path(__file__).resolve().parents[1] / "skills/long-running"
FIXTURES = SKILL / "reference/fidelity-fixtures"

HEAD = "3f3acff97aa11bb22cc33dd44ee55ff667788990"
OTHER_HEAD = "9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b"


def head_grades() -> list[str]:
    brief = (SKILL / "reference/fidelity-desk-brief.md").read_text()
    return sorted({line.replace("\\|", "|") for line in re.findall(r"`(fidelity: (?:ok|missing) <head7>[^`]*)`", brief)})


def filled(line: str, head: str) -> str:
    return line.replace("<head7>", head[:7]).replace("<key>", "rule/1068167").replace("<name>", "hourly re-read").replace("<one line>", "the sync line is kept")


def test_the_desk_brief_head_grades_are_the_lines_the_landing_runner_reads_for_that_head():
    missing, ok = head_grades()
    assert missing == "fidelity: missing <head7> <key> <one line>"
    assert ok == "fidelity: ok <head7> checked <key>,<key>... names <name>|<name>..."
    for line in (missing, ok):
        record = {"seq": 7, "text": filled(line, HEAD)}
        assert record["text"].startswith(grades.FIDELITY_GRADES)
        assert grades.newest_naming([record], HEAD) == record
        assert grades.newest_naming([record], OTHER_HEAD) is None
    assert filled(ok, HEAD).startswith(grades.FIDELITY_OK)
    assert not filled(missing, HEAD).startswith(grades.FIDELITY_OK)


def readme_rows() -> list[list[str]]:
    readme = (FIXTURES / "README.md").read_text()
    return [[cell.strip() for cell in re.split(r"(?<!\\)\|", line)[1:-1]] for line in readme.splitlines() if line.startswith(("| [", "| `"))]


def test_every_fixture_is_a_committed_synthetic_diff_or_a_pointer_pinned_to_a_commit():
    rows = readme_rows()
    linked = [match.group(1) for row in rows if (match := re.fullmatch(r"\[`([^`]+\.diff)`\]\(\1\)", row[0]))]
    assert sorted(linked) == sorted(path.name for path in FIXTURES.glob("*.diff"))
    for row in rows:
        assert row[2] in ("`ok`", "`missing`") and re.search(r"`(rule|prompt|corpus)/[^`]+`", row[3])
        assert row[1].startswith("Synthetic: ") if row[0] in [f"[`{name}`]({name})" for name in linked] else re.search(r"`git -C <checkout> [^`]*\b[0-9a-f]{11,40}\b[^`]*`", row[1])


def test_the_reader_first_breaks_replay_as_fixtures_pinned_to_their_merges_that_grade_missing():
    rows = {row[0]: row for row in readme_rows()}
    for fixture, merged in (("`46612-new-input-to-old-api`", "a88b7a6a1f3caf1023e70dcc91ae5534517f4242"), ("`46615-api-refuses-list-all`", "5b2f97e067b20721112e0630f080aa8c15ed3df6")):
        source, grade, row = rows[fixture][1:]
        assert f"diff {merged}^ {merged}`" in source and grade == "`missing`" and row == "`rule/7ab71e2`", fixture


@pytest.mark.parametrize(
    ("text", "grade"),
    [
        ("finder: ok 28cf4ad identical replay", "ok"),
        ("finder: major fb1d3f0 11 open", "major"),
        ("finder: issues 96d3048f7ec1 #42785: nine majors in two groups", "major"),
        ("finder [42751] issues 6a2abdc: gpt-6.1-sol, 2 majors", "major"),
        ("p8a-step6 finder verdict, #42745 at eca764c: 1 major, fixed in 408772a", "major"),
    ],
)
def test_every_finder_line_lanes_post_reads_as_a_verdict(text: str, grade: str) -> None:
    assert grades.finder_grade(text) == grade
