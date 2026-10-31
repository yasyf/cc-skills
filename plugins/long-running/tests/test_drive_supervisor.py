"""The supervisor process reads liveness from Orca and context size from transcripts the way the lanes mover expects."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import drive_supervisor as supervisor

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


def test_an_idle_teammate_of_a_live_lead_session_reads_live(tmp_path: Path):
    (tmp_path / "session-d50e80e8").mkdir()
    (tmp_path / "session-d50e80e8" / "config.json").write_text(json.dumps({"members": [{"name": "team-lead"}, {"name": "flock-race-1"}]}))
    rows = [
        {"lane": "root", "session": "d50e80e8-7ba3", "live": True},
        {"lane": "flock-race-1", "session": "d50e80e8-7ba3", "agent": "aflock-race-1-01", "live": False},
        {"lane": "gone-lane", "session": "d50e80e8-7ba3", "agent": "agone-lane-01", "live": False},
        {"lane": "orphan", "session": "0badc0de-1111", "agent": "aorphan-01", "live": False},
    ]

    assert [row["live"] for row in supervisor.rostered(rows, tmp_path)] == [True, True, False, False]


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
