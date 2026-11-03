"""owner_update.py over a recorded task list, ledger, board and cci: the lines the root's update to the owner must cover."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import owner_update
import pytest

DRIVE = "d1"
SESSION = "5e5510aa-0000-0000-0000-000000000000"
HEAD = "632c4a5aa1b2c3d4e5f60718293a4b5c6d7e8f90"
NOW = datetime.now(timezone.utc)


def stamp(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


def record(seq: int, lane: str, kind: str, text: str, minutes_ago: float) -> dict:
    return {"seq": seq, "drive": DRIVE, "lane": lane, "kind": kind, "at": stamp(minutes_ago), "text": text, "to": ["root"], "refs": {}}


def task(task_id: str, owner: str, status: str, subject: str) -> dict:
    return {"id": task_id, "subject": subject, "status": status, "owner": owner, "description": "", "blocks": [], "blockedBy": []}


def ask(seq: int, lane: str, text: str, minutes_ago: float, **fields: str) -> dict:
    return {"key": f"ask/{seq:06d}", "fields": {"lane": lane, "text": text, "asked_at": stamp(minutes_ago), "accept": "-"} | fields}


class Runner:
    def __init__(self, records: list[dict], asks: list[dict] = (), board: dict | None = None, incidents: list[dict] = ()) -> None:
        self.records, self.asks, self.board, self.incidents = records, list(asks), board, list(incidents)

    def __call__(self, argv: list[str], env: dict | None = None) -> subprocess.CompletedProcess:
        if argv[:2] == ["cc-present", "outcomes"]:
            return done(json.dumps(self.board)) if self.board is not None else done("", 1, "no board")
        if argv[:2] == ["cci", "tail"]:
            return done("" if "--lane" in argv else "".join(json.dumps(found) + "\n" for found in self.records))
        if argv[:2] == ["cci", "digest"]:
            return done(json.dumps({"open_incidents": self.incidents}))
        if argv[0] == "ccn" and "ledger" in argv:
            return done(json.dumps({"rows": self.asks}))
        raise AssertionError(f"unexpected command {argv}")


def done(out: str, code: int = 0, err: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], code, out, err)


@pytest.fixture
def tasks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    entry = home / "long-running/drives/d1.json"
    entry.parent.mkdir(parents=True)
    entry.write_text(json.dumps({"drive": DRIVE, "cci_drive": DRIVE, "sessions": [SESSION], "ledger": "abc", "checkout": str(tmp_path), "state_dir": str(tmp_path)}))
    folder = home / "tasks" / f"session-{SESSION[:8]}"
    folder.mkdir(parents=True)
    return folder


def write(folder: Path, *rows: dict) -> None:
    for row in rows:
        (folder / f"{row['id']}.json").write_text(json.dumps(row))


def found(runner: Runner, since_minutes: float = 120) -> dict:
    return owner_update.update(DRIVE, NOW - timedelta(minutes=since_minutes), runner)


def test_each_open_task_lane_with_a_report_since_the_last_update_is_delivered_once_at_its_newest(tasks):
    write(
        tasks,
        task("10", "devbox-explore-7", "pending", "Scenario list"),
        task("11", "devbox-explore-7", "in_progress", "Perf worker scenarios"),
        task("12", "old-lane-1", "completed", "Finished long ago"),
        task("13", "quiet-lane-1", "in_progress", "Quiet work"),
    )
    records = [
        record(1, "devbox-explore-7", "report", "scenario 2 measured", 90),
        record(2, "devbox-explore-7", "report", "scenario 3 measured, see #47525", 30),
        record(3, "devbox-explore-7", "state", "still measuring", 5),
        record(4, "old-lane-1", "report", "done again", 10),
        record(5, "quiet-lane-1", "report", "before the last update", 200),
    ]

    delivered = found(Runner(records, board={"doc": {}, "interactions": {}}))["delivered"]

    assert [(item["task"], item["lane"], item["seq"], item["prs"]) for item in delivered] == [("11", "devbox-explore-7", 2, ["47525"])]
    assert delivered[0]["line"].startswith("delivered #11 devbox-explore-7: Perf worker scenarios | report #2 ")


def test_in_flight_names_each_lane_last_record_or_none_and_asks_skip_answered_ones(tasks):
    write(tasks, task("11", "devbox-explore-7", "in_progress", "Perf worker scenarios"), task("13", "quiet-lane-1", "in_progress", "Quiet work"))
    records = [record(2, "devbox-explore-7", "head", "pushed", 30)]
    asks = [ask(1, "devbox-explore-7", "measure idle resources", 300), ask(2, "quiet-lane-1", "already answered", 300, answered_at=stamp(100))]

    result = found(Runner(records, asks, board={"doc": {}, "interactions": {}}))

    assert [item["line"].split(" | ")[1].split()[:2] for item in result["in_flight"]] == [["last", "head"], ["no", "record"]]
    assert [item["key"] for item in result["asks"]] == ["ask/000001"]
    assert result["asks"][0]["line"].startswith("ask LOST ask/000001 devbox-explore-7: measure idle resources | last head ")


def test_a_board_that_does_not_read_is_named_and_everything_else_still_prints(tasks):
    write(tasks, task("11", "devbox-explore-7", "in_progress", "Perf worker scenarios"))
    incident = record(9, "inc-api-fix", "incident", "SCOPE active yes", 20)

    result = found(Runner([], board=None, incidents=[incident]))

    assert result["board"][0]["line"].startswith("board unread: cc-present outcomes exited 1")
    assert result["incidents"][0]["line"] == f"incident #9 inc-api-fix {owner_update.when(incident)}: SCOPE active yes"
    assert [item["task"] for item in result["in_flight"]] == ["11"]


def test_a_board_row_names_its_pr_head_and_the_owner_verdict_at_that_head(tasks):
    doc = {"blocks": [{"id": "sec", "type": "section", "children": [{"id": "merge-prs", "type": "pr-groups", "groups": [{"id": "g", "title": "Fixes", "prs": [{"number": 47321, "head": HEAD[:7], "title": "PR"}]}]}]}]}
    interactions = {"triage": {"merge-prs": {"47321": {"verdict": "approved", "head": HEAD[:7], "at": stamp(5)}}}}

    board = found(Runner([], board={"doc": doc, "interactions": interactions}))["board"]

    assert [item["line"] for item in board] == ["board #47321 at 632c4a5: approved"]


def test_a_written_update_misses_each_lane_ask_and_pr_it_does_not_name_whole():
    names = ["tooling-codify-1", "release-5", "ask:abc123", "#48401", "tooling-codify-1"]
    assert owner_update.missing("tooling-codify-1 landed #48401; release-5-2 still runs", names) == ["release-5", "ask:abc123"]


def test_a_lane_sits_under_the_plan_step_its_brief_names_by_number_or_word():
    step = owner_update.plans.Step("11", "11. Tooling and the long-running rewrite")
    assert owner_update.on_step({"step": "11"}, step) and owner_update.on_step({"step": "tooling"}, step)
    assert not owner_update.on_step({"step": "memory"}, step) and not owner_update.on_step({"step": ""}, step)
