from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import retro

DRIVE = "brook-cci"
NOW = datetime(2026, 11, 2, 7, 0, tzinfo=timezone.utc)


def stamp(minutes: float) -> str:
    return (NOW + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeShell:
    def __init__(self, heard: dict[str, str] | None = None):
        self.heard = heard if heard is not None else {"fix-lane": "state"}
        self.rows: dict[str, dict[str, str]] = {}

    def run(self, argv: list[str], stdin: str | None = None, cwd: Path | None = None) -> str:
        if argv[:1] == ["ccn"]:
            verb = argv[3:5]
            if verb == ["ledger", "add"]:
                return json.dumps({"id": "r3tr0"})
            if verb == ["ledger", "show"]:
                return json.dumps({"rows": [{"key": key, "fields": fields} for key, fields in self.rows.items()]})
            if verb == ["ledger", "sync"]:
                for row in json.loads(stdin or "[]"):
                    self.rows[row["key"]] = {**self.rows.get(row["key"], {}), **row["fields"]}
                return ""
        if argv[:2] == ["cci", "grep"]:
            if "--lane" not in argv:
                return ""
            lane = argv[argv.index("--lane") + 1]
            return json.dumps({"lane": lane, "kind": self.heard[lane]}) + "\n" if lane in self.heard else ""
        raise AssertionError(f"unexpected call {argv}")


@pytest.fixture(autouse=True)
def entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    found = {"drive": "brook", "cci_drive": DRIVE, "state_dir": str(tmp_path), "checkout": str(tmp_path)}
    monkeypatch.setattr(retro.drive, "find", lambda name, session, root=None: found)
    monkeypatch.setattr(retro.ledger, "now", lambda: NOW)
    return found


def add(shell: FakeShell, *extra: str, failure: str = "UNDEFINED rows on the owner's table", owner: str = "fix-lane", due: str = "4h") -> int:
    return retro.main(["add", "--failure", failure, "--mechanism", "the writer refuses a row with no state", "--owner", owner, "--due", due, *extra], shell)


def test_a_row_carries_its_failure_mechanism_owner_due_and_state(capsys):
    shell = FakeShell()

    assert add(shell) == 0

    row = shell.rows["retro/000001"]
    assert (row["state"], row["owner"], row["due"]) == ("building", "fix-lane", stamp(240))
    assert capsys.readouterr().out.startswith("retro/000001 building owner fix-lane due ")


@pytest.mark.parametrize(
    ("given", "heard", "said"),
    [
        ({"owner": "root"}, {}, "is the coordinator"),
        ({"owner": "gone-lane"}, {}, "has posted nothing"),
        ({"owner": "done-lane"}, {"done-lane": "stand-down"}, "since its stand-down"),
        ({"due": "soon"}, None, "is neither an ISO 8601 time"),
        ({"due": "2026-11-02T06:00:00Z"}, None, "past due"),
    ],
    ids=["coordinator", "silent-owner", "stood-down-owner", "bad-due", "already-past"],
)
def test_add_refuses_a_row_with_no_live_worker_or_no_future_due(given, heard, said, capsys):
    """Owner 10:26 PM Nov 1: no row without a live owner lane and a due time."""
    shell = FakeShell(heard)

    with pytest.raises(SystemExit) as refused:
        add(shell, **given)

    assert said in f"{refused.value.code} {capsys.readouterr().err}"
    assert shell.rows == {}


def test_add_refuses_an_open_row_with_no_pr():
    shell = FakeShell()

    with pytest.raises(SystemExit, match="state open names no PR"):
        add(shell, "--state", "open")


def test_a_failure_already_on_the_table_is_refused_with_the_row_holding_it():
    shell = FakeShell()
    add(shell)

    with pytest.raises(SystemExit, match="retro/000001 already holds this failure"):
        add(shell, failure="  undefined ROWS on the owner's table")


def test_a_row_closes_only_live_with_its_record_or_as_a_duplicate():
    """Owner 10:26 PM Nov 1: a row closes only at FIX-LIVE with the record id."""
    shell = FakeShell()
    add(shell)
    add(shell, failure="a second failure")

    assert retro.main(["set", "retro/000001", "--state", "open", "--pr", "yasyf/cc-skills#1700"], shell) == 0
    assert retro.main(["live", "retro/000001", "--record", "307999"], shell) == 0
    assert retro.main(["duplicate", "retro/000002", "--of", "retro/000001"], shell) == 0

    assert (shell.rows["retro/000001"]["state"], shell.rows["retro/000001"]["record"]) == ("live", "307999")
    with pytest.raises(SystemExit, match="is closed"):
        retro.main(["set", "retro/000001", "--state", "building"], shell)


def test_check_lists_each_malformed_repeated_or_past_due_row_and_exits_one(capsys):
    """The coordinator's Stop hook and the stall watch read this one rule (owner 10:26 PM Nov 1)."""
    shell = FakeShell()
    shell.rows = {
        "retro/000001": {"failure": "no state written", "mechanism": "m", "owner": "fix-lane", "due": stamp(60)},
        "retro/000002": {"failure": "late fix", "mechanism": "m", "owner": "fix-lane", "due": stamp(-60), "state": "open", "pr": "#1"},
        "retro/000003": {"failure": "Late  fix", "mechanism": "m", "owner": "fix-lane", "due": stamp(60), "state": "building"},
        "retro/000004": {"failure": "fine", "mechanism": "m", "owner": "fix-lane", "due": stamp(60), "state": "building"},
        "retro/000005": {"failure": "shipped", "mechanism": "m", "owner": "fix-lane", "due": stamp(-600), "state": "live", "record": "9"},
        "retro/000006": {"failure": "free text state", "mechanism": "m", "owner": "root", "due": stamp(60), "state": "fixed 6:04 PM"},
    }

    assert retro.main(["check"], shell) == 1

    out = capsys.readouterr().out.splitlines()
    assert [line.split()[1] for line in out] == ["retro/000001", "retro/000002", "retro/000003", "retro/000006"]
    assert "no state" in out[0]
    assert "past due since" in out[1]
    assert "repeats retro/000002" in out[2]
    assert "is not one of building, open, merged, live" in out[3] and "is the coordinator" in out[3]
    shell.rows = {"retro/000004": shell.rows["retro/000004"]}
    assert retro.main(["check"], shell) == 0
