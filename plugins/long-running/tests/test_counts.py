"""The self-check a desk running off this ledger needs: the red set the ledger hands
over must be the same size as the red count anything derives from it.

The interim shell ledger this replaces reported zero red PRs while twenty were red, and
the two numbers disagreeing was the only visible symptom. A false zero is worse than a
wrong count, because a wrong count invites a check and a zero closes the question.
"""

from __future__ import annotations

import ledger
from conftest import DIRTY_HEAD, FakeShell, GREEN_HEAD, LEDGER, MOVED_HEAD

ROWS = [
    {"key": "21052", "fields": {"head": MOVED_HEAD, "base": "dev", "test_state": "failure", "mergeable_state": "unknown"}},
    {"key": "20961", "fields": {"head": DIRTY_HEAD, "base": "dev", "test_state": "success", "mergeable_state": "dirty"}},
    {"key": "20970", "fields": {"head": GREEN_HEAD, "base": "dev", "test_state": "success", "mergeable_state": "clean"}},
    {"key": "19999", "fields": {"head": "dead", "test_state": "success", "mergeable_state": "clean", "state": "landed"}},
]


def test_the_red_predicate_and_the_red_listing_agree(capsys):
    shell = FakeShell(rows=ROWS)
    rows = ledger.Notes(shell, LEDGER).rows()
    predicate = sum(1 for fields in rows.values() if ledger.needs_route(fields))

    ledger.main(["show", "--ledger", LEDGER, "--red"], shell=shell)
    listed = len([row for row in capsys.readouterr().out.splitlines()[1:] if row.strip()])

    assert predicate == listed == 2


def test_a_landed_row_is_neither_open_nor_red():
    shell = FakeShell(rows=ROWS)
    rows = ledger.Notes(shell, LEDGER).rows()

    assert sum(1 for fields in rows.values() if ledger.is_open(fields)) == 3
    assert not ledger.needs_route(rows["19999"])
