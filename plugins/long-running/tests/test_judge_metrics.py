from __future__ import annotations

import contextvars
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from captain_hook import dispatch_metrics
from hooks.judge_metrics import judge_call
from long_running import judge


@pytest.mark.parametrize(
    ("backend", "steps"),
    [
        ("sonnet", [("claude", "no")]),
        ("sonnet", [("claude", "unsure")]),
        ("sonnet", [("claude", "yes"), ("claude", "yes")]),
        ("sonnet", [("claude", "yes"), ("claude", "no")]),
        ("jev", [("uv", "yes")]),
        ("jev", [("uv", "no")]),
        ("jev+sonnet", [("uv", "no")]),
        ("jev+sonnet", [("uv", "yes"), ("claude", "yes")]),
        ("jev+sonnet", [("uv", "yes"), ("claude", "no")]),
        ("sonnet+jev", [("claude", "no")]),
        ("sonnet+jev", [("claude", "unsure"), ("uv", "yes")]),
        ("sonnet+jev", [("claude", "unsure"), ("uv", "no")]),
        ("sonnet+jev", [("claude", "yes"), ("claude", "no")]),
    ],
)
def test_measured_judgments_preserve_policy_commands_environment_and_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str, steps: list[tuple[str, str]]):
    selected = {"HOME": str(tmp_path), "CLAUDE_CONFIG_DIR": str(tmp_path / "config"), judge.BACKEND_ENV: backend}
    clock = [0.0]
    calls = []
    remaining = []
    state = [tmp_path / "ordinary"]
    monkeypatch.setattr(dispatch_metrics, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(judge, "state_dir", lambda drive, env=None: state[0])

    def transport(argv, **kwargs):
        calls.append((list(argv), dict(kwargs)))
        command, verdict = remaining.pop(0)
        assert argv[0] == command
        assert kwargs["capture_output"] and kwargs["text"] and kwargs["check"]
        clock[0] += 1
        payload = (
            {"structured_output": {"verdict": verdict, "artifact_phrase": "artifact", "evidence_phrase": "words", "sentence": "Judgment."}, "usage": {"input_tokens": 3}, "total_cost_usd": 0.01}
            if command == "claude"
            else {"p_yes": 0.9 if verdict == "yes" else 0.1, "tokens": 2}
        )
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "ignored")

    monkeypatch.setattr(judge.subprocess, "run", transport)
    arguments = {"backend": backend, "drive": "d1", "timeout": 7, "env": selected}
    remaining[:] = steps
    expected = judge.judge("plain", "words", "artifact", **arguments)
    expected_calls = list(calls)
    expected_row = json.loads((state[0] / judge.CALLS).read_text())
    state[0] = tmp_path / "measured"
    calls.clear()
    remaining[:] = steps
    with dispatch_metrics.collect_dispatch("Stop", 17) as metrics:
        actual = judge_call(judge, "plain", "words", "artifact", **arguments)
        summary = metrics.summary(clock[0] * 1000)
    actual_row = json.loads((state[0] / judge.CALLS).read_text())

    assert actual == expected and calls == expected_calls and not remaining
    assert {k: v for k, v in actual_row.items() if k not in {"at", "seconds"}} == {k: v for k, v in expected_row.items() if k not in {"at", "seconds"}}
    assert selected == {"HOME": str(tmp_path), "CLAUDE_CONFIG_DIR": str(tmp_path / "config"), judge.BACKEND_ENV: backend}
    assert summary["request_id"] == 17 and summary["worker_pid"] > 0
    phases = summary["dispatch_phases"]
    assert phases["llm"]["count"] == 1 and phases["llm"]["sum_ms"] == 1000 * len(steps)
    assert phases["cli"]["count"] == len(steps) and phases["cli"]["sum_ms"] == 1000 * len(steps)
    assert all(name["name"].startswith(f"{judge.__name__}:plain:{backend}") for phase in phases.values() for name in phase["slowest"])


@pytest.mark.parametrize("successful_first", [False, True])
@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired("claude", 7), subprocess.CalledProcessError(2, "claude"), OSError("unavailable"), ValueError("invalid")])
def test_failed_attempt_elapsed_time_is_retained_without_changing_fail_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, successful_first: bool, failure: Exception):
    clock = [0.0]
    calls = []
    monkeypatch.setattr(dispatch_metrics, "perf_counter", lambda: clock[0])

    def transport(argv, **kwargs):
        calls.append((argv, kwargs))
        clock[0] += 1
        if successful_first and len(calls) == 1:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"structured_output": {"verdict": "yes", "artifact_phrase": "artifact", "evidence_phrase": "words", "sentence": "Judgment."}}), "")
        raise failure

    monkeypatch.setattr(judge.subprocess, "run", transport)
    with dispatch_metrics.collect_dispatch("PreToolUse", 19) as metrics:
        actual = judge_call(judge, "answer", "words", "artifact", backend="sonnet", drive=None, timeout=7, env={"HOME": str(tmp_path)})
        summary = metrics.summary(clock[0] * 1000)

    assert actual is None
    assert len(calls) == 1 + successful_first
    assert summary["dispatch_phases"]["cli"]["count"] == len(calls)
    assert summary["dispatch_phases"]["cli"]["sum_ms"] == 1000 * len(calls)
    assert summary["dispatch_phases"]["llm"]["sum_ms"] == 1000 * len(calls)


def test_uncaught_error_identity_is_preserved_and_timed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    failure = RuntimeError("unexpected")
    clock = [0.0]
    monkeypatch.setattr(dispatch_metrics, "perf_counter", lambda: clock[0])

    def transport(*args, **kwargs):
        clock[0] += 1
        raise failure

    monkeypatch.setattr(judge.subprocess, "run", transport)
    with dispatch_metrics.collect_dispatch("Stop", 23) as metrics:
        with pytest.raises(RuntimeError) as caught:
            judge_call(judge, "plain", "words", "artifact", backend="sonnet", drive=None, timeout=7, env={"HOME": str(tmp_path)})
        summary = metrics.summary(1000)

    assert caught.value is failure
    assert summary["dispatch_phases"]["cli"]["sum_ms"] == summary["dispatch_phases"]["llm"]["sum_ms"] == 1000


@pytest.mark.parametrize("closed", [False, True])
def test_inactive_or_closed_measurement_never_reads_its_timer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, closed: bool):
    def timer():
        raise AssertionError("inactive timer")

    def transport(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, json.dumps({"structured_output": {"verdict": "no", "artifact_phrase": "", "evidence_phrase": "", "sentence": "Clear."}}), "")

    monkeypatch.setattr(dispatch_metrics, "perf_counter", timer)
    monkeypatch.setattr(judge.subprocess, "run", transport)
    arguments = {"backend": "sonnet", "drive": None, "timeout": 7, "env": {"HOME": str(tmp_path)}}
    if closed:
        with dispatch_metrics.collect_dispatch("Stop", 29) as metrics:
            before = metrics.summary(1000)
            actual = judge_call(judge, "plain", "words", "artifact", **arguments)
            assert metrics.summary(1000) == before
    else:
        assert dispatch_metrics.ACTIVE.get() is None
        actual = judge_call(judge, "plain", "words", "artifact", **arguments)
    assert actual.verdict == "no"


def test_context_copied_question_threads_keep_the_request_measurement(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def transport(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, json.dumps({"structured_output": {"verdict": "no", "artifact_phrase": "", "evidence_phrase": "", "sentence": "Clear."}}), "")

    monkeypatch.setattr(judge.subprocess, "run", transport)
    with ThreadPoolExecutor(1) as pool:
        for request in [31, 37]:
            with dispatch_metrics.collect_dispatch("PreToolUse", request) as metrics:
                context = contextvars.copy_context()
                result = pool.submit(context.run, judge_call, judge, "ruled", "words", "artifact", backend="sonnet", drive=None, timeout=7, env={"HOME": str(tmp_path)}).result()
                summary = metrics.summary(1000)
            assert result.verdict == "no" and summary["request_id"] == request
            assert summary["dispatch_phases"]["llm"]["count"] == summary["dispatch_phases"]["cli"]["count"] == 1
