import pytest
from server import parse_plan, plan_prs

DOC = """id: a5262e7
updated: 2026-10-27T06:30:00Z

# Progress

## Ordered plan of action (what lands, in what order)
1. **Now, no owner action needed.** The gateway batch (#42712, #42714) lands, then #43200.
2. **Taps he still owes:** the credential stack (#42788-#42790, #42737).
3. Step 11 cutover.

## Lanes live
- #49999 is not a plan step.
"""


@pytest.mark.parametrize(
    ("text", "numbers"),
    [
        ("#42712 and #42714", [42712, 42714]),
        ("(#42788-#42790, #42737)", [42788, 42789, 42790, 42737]),
        ("#42712 again #42712", [42712]),
        ("no pull requests", []),
    ],
)
def test_plan_prs_expands_ranges_once(text, numbers):
    assert plan_prs(text) == numbers


def test_parse_plan_reads_only_the_ordered_section():
    updated, steps = parse_plan(DOC)
    assert updated == "2026-10-27T06:30:00Z"
    assert [step["title"] for step in steps] == ["Now, no owner action needed", "Taps he still owes", "Step 11 cutover."]
    assert steps[0]["prs"] == [42712, 42714, 43200]
    assert steps[1]["prs"] == [42788, 42789, 42790, 42737]
    assert steps[2]["text"] == ""


def test_parse_plan_skips_a_doc_without_the_section():
    assert parse_plan("id: x\nupdated: now\n\n## Lanes live\n1. **A.** b\n") is None
