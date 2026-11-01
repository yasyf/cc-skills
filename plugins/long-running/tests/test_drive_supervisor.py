"""The supervisor process reads liveness from Orca and context size from transcripts the way the lanes mover expects."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import drive_supervisor as supervisor
from lr import config as lr_config

NOW = datetime(2026, 10, 31, 17, 0, tzinfo=UTC)


def assistant(tokens: int) -> str:
    return json.dumps({"type": "assistant", "message": {"usage": {"input_tokens": 10, "cache_creation_input_tokens": 90, "cache_read_input_tokens": tokens - 100}}})


def test_context_size_is_the_newest_assistant_turn(tmp_path: Path):
    path = tmp_path / "agent.jsonl"
    path.write_text("\n".join([assistant(200_000), json.dumps({"type": "user", "message": {"content": "go"}}), assistant(640_000), json.dumps({"type": "user", "message": {"content": "ok"}})]) + "\n")

    assert supervisor.last_tokens(path) == 640_000


def test_a_compaction_after_the_last_turn_means_no_reading(tmp_path: Path):
    path = tmp_path / "agent.jsonl"
    path.write_text("\n".join([assistant(900_000), json.dumps({"type": "system", "subtype": "compact_boundary"})]) + "\n")

    assert supervisor.last_tokens(path) is None


def test_every_teammate_of_a_live_lead_session_reads_live_and_carries_its_team(tmp_path: Path):
    (tmp_path / "session-d50e80e8").mkdir()
    (tmp_path / "session-d50e80e8" / "config.json").write_text(json.dumps({"members": [{"name": "team-lead"}, {"name": "flock-race-1"}, {"name": "ci-github-403-1"}]}))
    rows = [
        {"lane": "root", "session": "d50e80e8-7ba3", "live": True},
        {"lane": "flock-race-1", "session": "d50e80e8-7ba3", "agent": "aflock-race-1-01", "live": False},
        {"lane": "ci-github-403-1", "session": "d50e80e8-7ba3", "agent": "aci-github-403-1-01", "live": True},
        {"lane": "gone-lane", "session": "d50e80e8-7ba3", "agent": "agone-lane-01", "live": False},
        {"lane": "orphan", "session": "0badc0de-1111", "agent": "aorphan-01", "live": False},
    ]

    out = supervisor.rostered(rows, tmp_path)

    assert [row["live"] for row in out] == [True, True, True, False, False]
    assert [row.get("roster") for row in out] == [None, *[str(tmp_path / "session-d50e80e8")] * 2, None, None]


def test_a_wake_mails_an_idle_lane_its_page_or_once_per_interval_while_its_root_task_is_in_progress(tmp_path: Path):
    tasks = tmp_path / "tasks" / "session-d50e80e8"
    tasks.mkdir(parents=True)
    (tasks / "1688.json").write_text(json.dumps({"id": "1688", "subject": "flock-race-1: fix the lane-flock race", "status": "in_progress", "owner": "flock-race-1"}))
    (tasks / "1690.json").write_text(json.dumps({"id": "1690", "subject": "done-lane: ship it", "status": "completed", "owner": "done-lane"}))
    roster = tmp_path / "teams" / "session-d50e80e8"
    clock = [NOW]
    waker = supervisor.Waker("d1", lambda: clock[0], tmp_path / "tasks")
    row = {"session": "d50e80e8-7ba3", "roster": str(roster)}

    assert waker("flock-race-1", row, None) and not waker("flock-race-1", row, None) and not waker("done-lane", row, None)
    clock[0] = NOW + timedelta(minutes=16)
    assert waker("flock-race-1", row, None)
    assert waker("done-lane", row, {"topic": "red:46475:89f46c3", "why": "this blocker on your PR #46475 arrived after your last record: STALL red #46475 at 89f46c3", "text": "STALL red #46475 at 89f46c3", "prs": [46475]})

    inbox = json.loads((roster / "inboxes" / "flock-race-1.json").read_text())
    assert [message["from"] for message in inbox] == ["long-running", "long-running"]
    assert inbox[0]["text"].startswith("WAKE flock-race-1: ") and "do not restart your brief" in inbox[0]["text"]
    [paged] = json.loads((roster / "inboxes" / "done-lane.json").read_text())
    assert "this blocker on your PR #46475 arrived after your last record: STALL red #46475 at 89f46c3" in paged["text"] and not (roster / "inboxes" / "flock-race-1.json.lock").exists()

    (roster / "inboxes" / "held-lane.json.lock").mkdir()
    assert not waker("held-lane", row, {"topic": "red:1:a", "why": "this blocker on your PR #1 arrived", "text": "STALL red #1", "prs": [1]}) and not (roster / "inboxes" / "held-lane.json").exists()


def test_only_active_orca_dispatches_count_as_live(tmp_path: Path, monkeypatch):
    for lane, dispatch in (("sol-fix", "ctx_1"), ("sol-done", "ctx_2"), ("sol-gone", "ctx_3")):
        (tmp_path / f"{lane}.json").write_text(json.dumps({"result": {"dispatchId": dispatch}}))
        (tmp_path / f"{lane}.terminal").write_text("term_1\n")
    workers = [{"dispatchId": "ctx_1", "dispatchStatus": "dispatched"}, {"dispatchId": "ctx_2", "dispatchStatus": "completed"}]
    calls = []
    monkeypatch.setattr(supervisor, "run", lambda argv: calls.append(argv) or json.dumps({"ok": True, "result": {"workers": workers, "page": {}}}))

    rows = supervisor.orca_rows("run_1", tmp_path, None, NOW)

    assert rows == [{"lane": "sol-fix", "live": True, "seen_at": "2026-10-31T17:00:00Z", "dispatch": "ctx_1"}]
    assert len(calls) == 1
    assert supervisor.orca_rows("run_1", tmp_path, {"sol-done"}, NOW) == []


def test_a_reinstall_bootstraps_only_once_launchd_has_finished_booting_out_the_old_copy(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(supervisor.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(supervisor.time, "sleep", lambda seconds: None)
    calls: list[str] = []
    loaded = iter([0, 0, 0, 113])

    def launchctl(*argv: str):
        calls.append(argv[0])
        return supervisor.subprocess.CompletedProcess(argv, next(loaded) if argv[0] == "print" else 0, "", "")

    monkeypatch.setattr(supervisor, "launchctl", launchctl)
    config = lr_config.Config("org/repo", "d1", "/srv/checkout", "bot", "drive", ("lanes",))

    assert supervisor.cmd_install(config, tmp_path / "lr.json") == 0
    assert calls == ["print", "bootout", "print", "print", "print", "bootstrap"]
