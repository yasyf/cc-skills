"""The self-check a desk running off this ledger needs: the red count it reports and
the red set it hands over must be the same number.

The interim shell ledger this replaces reported zero red PRs while twenty were red,
and the two numbers disagreeing was the only visible symptom. A false zero is worse
than a wrong count, because a wrong count invites a check and a zero closes the
question.
"""

from __future__ import annotations

import ledger
from conftest import DIRTY_HEAD, FakeShell, GREEN_HEAD, LEDGER, MOVED_HEAD

ROWS = [
    {"key": "21052", "fields": {"head": MOVED_HEAD, "base": "dev", "test_state": "failure", "mergeable_state": "unknown"}},
    {"key": "20961", "fields": {"head": DIRTY_HEAD, "base": "dev", "test_state": "success", "mergeable_state": "dirty"}},
    {"key": "20970", "fields": {"head": GREEN_HEAD, "base": "dev", "test_state": "success", "mergeable_state": "clean"}},
    {"key": "19999", "fields": {"head": "dead", "test_state": "success", "mergeable_state": "clean", "state": "merged"}},
]


def red_count(capsys) -> int:
    reported = capsys.readouterr().out
    field = next(part for part in reported.split(" | ") if part.startswith("red "))
    return int(field.split()[1])


def test_the_reported_red_count_equals_the_red_set_it_produced(capsys):
    shell = FakeShell(rows=ROWS)

    ledger.main(["line", "--repo", "Forge-AI/monorepo", "--ledger", LEDGER], shell=shell)
    reported = red_count(capsys)

    ledger.main(["show", "--ledger", LEDGER, "--red"], shell=shell)
    listed = len([row for row in capsys.readouterr().out.splitlines()[1:] if row.strip()])

    assert reported == listed == 2


def test_a_merged_row_counts_as_neither_open_nor_red(capsys):
    shell = FakeShell(rows=ROWS)
    ledger.main(["line", "--repo", "Forge-AI/monorepo", "--ledger", LEDGER], shell=shell)
    reported = capsys.readouterr().out

    assert "open 3" in reported
    assert "red 2" in reported


def test_a_closed_without_squash_row_is_reported_rather_than_folded_away(capsys):
    rows = [dict(ROWS[3], fields=dict(ROWS[3]["fields"], state="closed-without-squash"))]
    shell = FakeShell(rows=rows)
    ledger.main(["line", "--repo", "Forge-AI/monorepo", "--ledger", LEDGER], shell=shell)

    assert "closed-no-squash 1" in capsys.readouterr().out


def test_no_stranded_rows_leaves_the_line_quiet(capsys):
    shell = FakeShell(rows=ROWS)
    ledger.main(["line", "--repo", "Forge-AI/monorepo", "--ledger", LEDGER], shell=shell)

    assert "closed-no-squash" not in capsys.readouterr().out
