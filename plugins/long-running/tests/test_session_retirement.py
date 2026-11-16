from contextlib import nullcontext
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from drive import agents, state, watch

GONE = agents.gone


@pytest.mark.parametrize("job_state", ["waiting", "built", "done", "failed", "stopped"])
@pytest.mark.parametrize("bound", [False, True])
def test_idle_background_session_retires_after_its_job_turn(
    monkeypatch: pytest.MonkeyPatch, job_state: str, bound: bool
) -> None:
    job = {
        "state": job_state,
        "agent": "worker" if bound else None,
        "bg": {"agent": "worker", "session": "background", "cwd": "/work"},
    }
    ended = retirement(monkeypatch, job)
    watch.rest_sessions(SimpleNamespace(name="demo"), datetime.now(UTC))
    assert ended == [("background", Path("/work"), job_state in state.FINISHED_JOB_STATES)]


@pytest.mark.parametrize("change", ["running", "busy", "replacement"])
def test_retirement_keeps_active_or_replaced_background_session(monkeypatch: pytest.MonkeyPatch, change: str) -> None:
    job = {"state": "waiting", "agent": None, "bg": {"agent": "worker", "session": "background", "cwd": "/work"}}
    ended = retirement(monkeypatch, job)
    if change == "running":
        job["state"] = "running"
    elif change == "busy":
        monkeypatch.setattr(agents, "busy", lambda session, records: True)
    else:
        replacement = deepcopy(job)
        replacement["bg"]["session"] = "replacement"
        snapshots = iter([{"jobs": {"j1": job}}, {"jobs": {"j1": replacement}}])
        monkeypatch.setattr(state, "load", lambda name: next(snapshots))
    watch.rest_sessions(SimpleNamespace(name="demo"), datetime.now(UTC))
    assert ended == []


def retirement(monkeypatch: pytest.MonkeyPatch, job: dict) -> list:
    ended = []
    monkeypatch.setattr(state, "scratch", lambda: False)
    monkeypatch.setattr(state, "scratch", lambda: False)
    monkeypatch.setattr(state, "load", lambda name: {"jobs": {"j1": job}})
    monkeypatch.setattr(state, "job_locked", lambda *args: nullcontext())
    monkeypatch.setattr(watch, "heard_job", lambda *args: True)
    monkeypatch.setattr(agents, "session_records", lambda: [])
    monkeypatch.setattr(agents, "alive", lambda session, records: True)
    monkeypatch.setattr(agents, "busy", lambda session, records: False)
    monkeypatch.setattr(agents, "gone", GONE)
    monkeypatch.setattr(agents, "background_job", lambda session: ("job", {"state": "done"}))
    monkeypatch.setattr(agents, "end_bg", lambda session, cwd, removed: ended.append((session, cwd, removed)))
    return ended
