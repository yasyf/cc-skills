"""The fidelity desk brief's grade lines against what the landing runner reads, the fixture set against its README, and the PR-body lines every lane brief carries."""

from __future__ import annotations

import re
from pathlib import Path

import grades
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


def test_the_desk_brief_reads_callers_at_the_head_and_carries_a_restack_grade_past_register_growth():
    brief = " ".join((SKILL / "reference/fidelity-desk-brief.md").read_text().split())
    assert "callees on the base branch" not in brief
    assert "its callers and callees as the head has them" in brief
    restack = brief.split("A restack whose patch is identical")[1].split("An incident fix")[0]
    assert "slice --required" not in restack and "before any ok" not in restack and "never hold it" in restack
    assert "counts an ok only when" not in brief


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


@pytest.mark.parametrize("path", ["SKILL.md", "reference/orca-lane-brief.md", "scripts/pr-prose.py"])
def test_lane_briefs_ask_for_regression_tests_that_failed_and_a_risk_profile_in_prose(path):
    text = (SKILL / path).read_text()
    assert "`## Regression tests` section" in text
    assert "each test id" in text and "failed against the old tree" in text
    assert "states Load and Data in prose" in text
    assert "Census" not in text and "Backout" not in text


def test_both_graders_hold_each_added_test_to_the_plan_line_it_rests_on():
    for name in ("finder-brief.md", "fidelity-desk-brief.md"):
        brief = " ".join((SKILL / "reference" / name).read_text().split())
        assert "pins unplanned behavior" in brief and "(e20e623)" in brief, name
        assert "Every assertion the diff adds or changes in a test" in brief and "untouched assertions are dev's" in brief, name
        assert "behavior a plan line, sub-plan line, or owner ruling names" in brief, name


def test_finders_grade_a_ports_tests_against_the_old_tree_pr_not_the_plan():
    for name in ("finder-brief.md", "finder-desk-brief.md"):
        brief = " ".join((SKILL / "reference" / name).read_text().split())
        assert "ports an old-tree PR by number" in brief, name
        assert "graded against the old-tree PR" in brief and "<test> <- old-tree #<n>" in brief, name


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
