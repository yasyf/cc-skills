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


@pytest.mark.parametrize("live", [False, True])
def test_a_background_session_claude_still_lists_as_running_is_gone_once_no_process_by_pid_and_start_names_it_as_j6063s_was(monkeypatch: pytest.MonkeyPatch, live: bool) -> None:
    records = [(4123, {"sessionId": "429b9597", "status": "idle", "procStart": "Mon Nov 16 04:02:11 2026"})]
    monkeypatch.setattr(agents, "session_records", lambda: records)
    monkeypatch.setattr(agents, "background_job", lambda session: ("job", {"state": "running", "tempo": "active"}))
    monkeypatch.setattr(agents, "running", lambda started: set(started) if live else set())
    monkeypatch.setattr(agents, "gone", GONE)
    assert agents.gone({"session": "429b9597", "started": "2026-11-16T04:02:11-08:00"}) is not live


def test_a_dead_session_resumes_first_and_starts_fresh_on_its_brief_once_it_died_again_soon_after_a_resume_or_near_its_context_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from datetime import timedelta

    from drive import cli

    transcript = tmp_path / "429b9597.jsonl"
    monkeypatch.setattr(watch, "session_transcript", lambda holder: transcript)
    bg = {"session": "429b9597", "cwd": str(tmp_path), "started": "2026-11-16T04:02:11-08:00"}
    assert cli.fresh_start(bg, []) is None
    transcript.write_text('{"type": "assistant", "message": {"usage": {"input_tokens": 12, "cache_read_input_tokens": 175000}}}\n')
    assert cli.fresh_start(bg, []) == "died at 175012 tokens of context, near its limit"
    transcript.write_text('{"type": "assistant", "message": {"usage": {"input_tokens": 12}}}\n')
    assert cli.fresh_start(bg | {"resumed": state.stamp(state.now() - timedelta(minutes=30))}, []) is None
    resumed = state.stamp(state.now() - timedelta(minutes=4))
    assert cli.fresh_start(bg | {"resumed": resumed}, []) == f"died again within 10 minutes of its resume at {resumed}"


REPORTED = {"created_at": "2026-11-16T07:06:12-08:00", "body": "DONE https://buildkite.com/forge/release/builds/17947: #56306 and #56314 passed"}


def tool_call(at: str) -> str:
    return f'{{"type": "assistant", "timestamp": "{at}", "message": {{"content": [{{"type": "tool_use", "name": "Bash"}}]}}}}\n'


def closing_text(at: str) -> str:
    return f'{{"type": "assistant", "timestamp": "{at}", "message": {{"content": [{{"type": "text", "text": "Reported."}}]}}}}\n'


@pytest.fixture
def ended_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, dict]:
    transcript = tmp_path / "299d123b.jsonl"
    monkeypatch.setattr(watch, "session_transcript", lambda holder: transcript)
    return transcript, {"session": "299d123b", "cwd": str(tmp_path), "started": "2026-11-16T05:12:40-08:00"}


def test_a_gone_session_whose_kept_report_came_after_its_last_tool_call_finished_as_j4481s_did_at_7_06(ended_session: tuple[Path, dict]) -> None:
    from drive import cli

    transcript, bg = ended_session
    transcript.write_text(tool_call("2026-11-16T15:06:10.412Z") + closing_text("2026-11-16T15:06:20.003Z"))
    job = {"said": "running", "undelivered": [{"words": "an answer", "request": "734148"}]}
    called = state.stamp(state.parse_time("2026-11-16T15:06:10Z").astimezone())
    assert cli.finished(job, [REPORTED], bg) == f"kept its report at 2026-11-16T07:06:12-08:00 after its last tool call at {called}, so it ended its turn on that report"


@pytest.mark.parametrize("said", ["DONE https://buildkite.com/forge/release/builds/17947", "BUILT j4481-strip, waits on #56306", "BLOCKED no staging slot", "WAITING #56306 to merge"])
def test_a_gone_session_whose_job_reads_finished_with_nothing_held_is_left_alone(ended_session: tuple[Path, dict], said: str) -> None:
    from drive import cli

    transcript, bg = ended_session
    transcript.write_text(tool_call("2026-11-16T15:09:00Z"))
    assert cli.finished({"said": said, "undelivered": []}, [REPORTED], bg) == f"reads {said.split()[0]} with nothing held for it"


@pytest.mark.parametrize("said", ["running", "WAITING #56306 to merge"])
def test_a_gone_session_still_owing_work_with_held_texts_wakes(ended_session: tuple[Path, dict], said: str) -> None:
    from drive import cli

    transcript, bg = ended_session
    transcript.write_text(tool_call("2026-11-16T15:09:00Z") + closing_text("2026-11-16T15:09:30Z"))
    held = [{"words": "rerun the strip on cut W2n", "request": "734190"}]
    assert cli.finished({"said": said, "undelivered": held}, [REPORTED], bg) is None
    assert cli.finished({"said": "running", "undelivered": []}, [], bg) is None


def test_own_post_to_a_finished_gone_session_neither_resumes_nor_starts_it(monkeypatch: pytest.MonkeyPatch, ended_session: tuple[Path, dict]) -> None:
    from drive import cli

    transcript, bg = ended_session
    transcript.write_text(tool_call("2026-11-16T15:06:10Z"))
    monkeypatch.setattr(agents, "gone", lambda holder: True)
    monkeypatch.setattr(state, "load", lambda name: {"jobs": {"j4481": {"bg": bg, "said": "DONE https://buildkite.com/forge/release/builds/17947", "undelivered": []}}})
    monkeypatch.setattr(state, "returns", lambda name, job_id: [REPORTED])
    monkeypatch.setattr(agents, "resume_job", lambda *args: pytest.fail("resumed a finished session"))
    monkeypatch.setattr(state, "detach", lambda *args: pytest.fail("started a fresh agent for a finished session"))
    line = cli.own_post("durable-agents", "j4481", "299d123b", "734148")
    assert line.startswith("j4481: posted as handoff record 734148; its session 299d123b is gone but kept its report at 2026-11-16T07:06:12-08:00")
    assert line.endswith("so nothing resumes or starts it: the record waits at its cci address")


@pytest.mark.parametrize("reported", [True, False])
def test_a_session_that_kept_a_report_after_its_resume_ended_normally_however_soon_as_j7705s_did_26_seconds_after_and_one_with_none_died(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reported: bool
) -> None:
    from datetime import timedelta

    from drive import cli

    monkeypatch.setattr(watch, "session_transcript", lambda holder: tmp_path / "86e88fb6.jsonl")
    resumed = state.now() - timedelta(minutes=2)
    bg = {"session": "86e88fb6", "cwd": str(tmp_path), "started": "2026-11-16T04:02:11-08:00", "resumed": state.stamp(resumed)}
    waiting = {"created_at": state.stamp(resumed + timedelta(seconds=26) if reported else resumed - timedelta(minutes=40)), "body": "WAITING #57170 to merge"}
    expected = None if reported else f"died again within 10 minutes of its resume at {bg['resumed']}"
    assert cli.fresh_start(bg, [waiting]) == expected
