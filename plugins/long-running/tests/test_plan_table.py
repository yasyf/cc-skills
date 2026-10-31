from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from long_running import ledger
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills" / "long-running" / "scripts" / "plan-table.py"
spec = importlib.util.spec_from_file_location("plan_table", SCRIPT)
plan_table = importlib.util.module_from_spec(spec)
sys.modules["plan_table"] = plan_table
spec.loader.exec_module(plan_table)

DOC_ID = "acc560e" + "0" * 33
LOG_ID = "843dd8b" + "0" * 33
HEADER = "| Step | What it delivers | State | What blocks it | What we are doing to unblock it | What went wrong or slowed it so far |"
BODY = f"""# Owner table

Intro with #99999 outside the table.

{HEADER}
|---|---|---|---|---|---|
| 1. Empty the skip files | Every session verified | in progress | 140 sessions | A deadline PR (#38759) | Three passes failed alike. |
| 2b. Drop the mirror | Readers use SandSQL | READY-HELD | Data gate (#36935) | deletions-3 restacks | Restack was late. |
| 8. Binding refs | Writer ships | in progress | Release (#38417) | Walker ships it | None recorded. |
| 9. Live already | Done work | live | Nothing (#38001) | Nothing | None recorded. |

| Step | Other |
|---|---|
| ignored | #12345 |
"""
VERDICTS = {38759: "blocked:ci-pending", 36935: "blocked:conflict,ci-red", 38417: "landed", 38001: "landed"}
ENTRIES = [
    {"ts": "2026-10-16T11:22:59Z", "text": "Baseline with no step tag."},
    {"ts": "2026-10-16T11:30:00Z", "text": "[step 2b] restack missed its 4:05 clock."},
    {"ts": "2026-10-16T11:40:00Z", "text": "[Step 2B] restack landed at 4:38 after a second post."},
    {"ts": "2026-10-16T11:35:00Z", "text": "[step 1] SoFi write pass stalled on s2."},
]


class FakeShell(ledger.Shell):
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, argv: list[str], stdin: str | None = None) -> str:
        self.calls.append(argv)
        if argv[:3] == ["ccn", "doc", "show"]:
            return json.dumps({"id": DOC_ID, "body": BODY})
        if argv[:3] == ["ccn", "log", "show"]:
            return json.dumps({"id": LOG_ID, "entries": ENTRIES})
        if argv[:4] == ["ccx", "vcs", "pr", "status"]:
            return json.dumps([{"number": int(n), "verdict": VERDICTS[int(n)]} for n in argv[4:-1]])
        raise AssertionError(f"unexpected call {argv}")


def test_owner_rows_reads_only_the_first_step_table():
    rows = plan_table.owner_rows(BODY)
    assert [row[0] for row in rows] == ["1. Empty the skip files", "2b. Drop the mirror", "8. Binding refs", "9. Live already"]


def test_owner_rows_refuses_a_doc_without_the_table():
    with pytest.raises(ValueError, match="Step"):
        plan_table.owner_rows("# Nothing here\n\n| Lane | State |\n|---|---|\n| a | b |\n")


def test_owner_rows_refuses_a_row_with_the_wrong_shape():
    with pytest.raises(ValueError, match="cells"):
        plan_table.owner_rows(HEADER + "\n|---|---|---|---|---|---|\n| 1. Short | row |\n")


@pytest.mark.parametrize(
    ("state", "verdicts", "expected"),
    [
        ("in progress", [], "in progress"),
        ("in progress", ["landed", "landed"], "landed"),
        ("live", ["landed"], "live"),
        ("READY-HELD", ["landed", "blocked:ci-red", "landable"], "READY-HELD, PRs: 1 blocked, 1 landable, 1 landed"),
    ],
)
def test_refreshed_state(state, verdicts, expected):
    assert plan_table.refreshed_state(state, verdicts) == expected


def test_latest_retro_keeps_the_newest_tagged_entry_per_step():
    assert plan_table.latest_retro(ENTRIES) == {
        "2b": ("2026-10-16T11:40:00Z", "restack landed at 4:38 after a second post."),
        "1": ("2026-10-16T11:35:00Z", "SoFi write pass stalled on s2."),
    }


def test_main_writes_the_refreshed_table(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    shell = FakeShell()
    out = tmp_path / "dashboard" / "plan-table.md"
    now = datetime(2026, 10, 16, 11, 45, tzinfo=UTC)
    assert plan_table.main(["--doc", DOC_ID, "--out", str(out), "--retro-log", LOG_ID, "--repo", str(tmp_path)], shell, now) == 0
    pr_call = next(call for call in shell.calls if call[:2] == ["ccx", "vcs"])
    assert pr_call[4:-1] == ["38759", "36935", "38417", "38001"]
    text = out.read_text()
    assert text.startswith("# Plan table\n\nRefreshed 4:45 AM Oct 16 Pacific from doc acc560e")
    lines = text.splitlines()
    assert lines[4] == HEADER
    rows = {line.split(" | ")[0].lstrip("| "): line.split(" | ") for line in lines[6:]}
    assert rows["1. Empty the skip files"][2] == "in progress, PRs: 1 blocked"
    assert rows["1. Empty the skip files"][5] == "Three passes failed alike. Watchdog 4:35 AM: SoFi write pass stalled on s2. |"
    assert rows["2b. Drop the mirror"][5] == "Restack was late. Watchdog 4:40 AM: restack landed at 4:38 after a second post. |"
    assert rows["8. Binding refs"][2] == "landed"
    assert rows["9. Live already"][2] == "live"
    assert not out.with_suffix(".tmp").exists()
