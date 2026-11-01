import ctypes
import json
import os

import pytest

from long_running import pass_io
from test_desk_runner import config, landing_of, landing_pass, orca_pass, runner_module, shell


def records(capsys):
    return [json.loads(line.removeprefix("PASS-IO ")) for line in capsys.readouterr().err.splitlines() if line.startswith("PASS-IO ")]


def test_native_snapshot_reads_only_own_reviewed_rusage_fields(monkeypatch):
    calls = []

    class Rusage:
        def __call__(self, pid, kind, pointer):
            calls.append((pid, kind))
            values = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint64 * 40)).contents
            values[2], values[3], values[18], values[19] = 7, 8, 4096, 8192
            return 0

    class Library:
        proc_pid_rusage = Rusage()

    def library(path, use_errno):
        assert path == "/usr/lib/libproc.dylib" and use_errno
        return Library()

    monkeypatch.setattr(pass_io.ctypes, "CDLL", library)
    assert pass_io.snapshot() == {"read_bytes": 4096, "write_bytes": 8192, "cpu_ticks": 15}
    assert calls == [(os.getpid(), 2)]


def test_native_snapshot_error_is_not_a_zero_measurement(monkeypatch):
    class Rusage:
        def __call__(self, *_):
            return -1

    class Library:
        proc_pid_rusage = Rusage()

    monkeypatch.setattr(pass_io.ctypes, "CDLL", lambda *_args, **_kwargs: Library())
    monkeypatch.setattr(pass_io.ctypes, "get_errno", lambda: 5)
    with pytest.raises(OSError) as failure:
        pass_io.snapshot()
    assert failure.value.errno == 5


def test_disabled_and_finished_phases_do_not_read_native_counters(monkeypatch, capsys):
    def forbidden():
        raise AssertionError("disabled diagnostic read native counters")

    monkeypatch.setattr(pass_io, "snapshot", forbidden)
    diagnostic = pass_io.OnePassIO()
    seen = []
    for phase in ("gate-setup", "rows-and-gating", "pass-cleanup", "post-state", "flush-and-beat", "overflow", *sorted(pass_io.OVERFLOW_PHASES)):
        with diagnostic.phase(phase):
            seen.append(phase)
    assert len(seen) == 9
    assert not records(capsys)


def test_one_overflow_finishes_its_children_after_main_pass_ends(monkeypatch, capsys):
    calls = []

    def counted():
        calls.append(None)
        return {"read_bytes": len(calls) * 4096, "write_bytes": len(calls) * 8192, "cpu_ticks": len(calls) * 11}

    monkeypatch.setattr(pass_io, "snapshot", counted)
    diagnostic = pass_io.OnePassIO(True)
    with diagnostic.phase("overflow"):
        diagnostic.finish()
        for phase in sorted(pass_io.OVERFLOW_PHASES):
            for _ in range(3):
                with diagnostic.phase(phase):
                    pass
    for phase in ("overflow", "gate-setup", *sorted(pass_io.OVERFLOW_PHASES)):
        with diagnostic.phase(phase):
            pass
    observed = records(capsys)
    assert len(observed) == 4 and len(calls) == 8
    assert {row["phase"] for row in observed} == {"overflow", *pass_io.OVERFLOW_PHASES}
    assert all(row["completed"] and row["delta"]["write_bytes"] > 0 for row in observed)
    assert all("overlapping" in row["scope"] for row in observed)


@pytest.mark.parametrize("stage", ["before", "after", "output"])
@pytest.mark.parametrize("body_raises", [False, True])
def test_expected_capture_failures_preserve_body_and_exception(monkeypatch, capsys, stage, body_raises):
    calls, body = [], []

    def counted():
        calls.append(None)
        if (stage == "before" and len(calls) == 1) or (stage == "after" and len(calls) == 2):
            raise OSError(5, "counter failure")
        return {"read_bytes": 10, "write_bytes": 20, "cpu_ticks": 30}

    def broken_output(*_args, **_kwargs):
        raise BrokenPipeError(32, "diagnostic output unavailable")

    monkeypatch.setattr(pass_io, "snapshot", counted)
    if stage == "output":
        monkeypatch.setattr(pass_io, "print", broken_output, raising=False)
    diagnostic = pass_io.OnePassIO(True)
    failure = RuntimeError("original phase failure")

    def execute():
        with diagnostic.phase("overflow"):
            body.append("ran")
            if body_raises:
                raise failure

    if body_raises:
        with pytest.raises(RuntimeError) as raised:
            execute()
        assert raised.value is failure
    else:
        execute()
    assert body == ["ran"]
    count = len(calls)
    with diagnostic.phase("overflow-routes"):
        body.append("after")
    assert len(calls) == count
    observed = records(capsys)
    if stage == "output":
        with diagnostic.phase("post-state"):
            body.append("disabled")
        assert len(calls) == count and not observed
    else:
        assert len(observed) == 1
        assert observed[0]["status"] == "COUNTER_ERROR" and observed[0]["delta"] is None
        assert observed[0]["counter_error"]["stage"] == stage
        assert observed[0]["completed"] is not body_raises


def test_actual_once_landing_phase_boundaries_and_default_disabled(shell, config, monkeypatch, capsys):
    calls = []

    def counted():
        calls.append(None)
        return {"read_bytes": len(calls) * 100, "write_bytes": len(calls) * 200, "cpu_ticks": len(calls) * 10}

    monkeypatch.setattr(pass_io, "snapshot", counted)
    landing_pass(shell, config)
    assert not calls and not records(capsys)
    configured = json.loads(config.read_text())
    configured["landing"]["trace_pass_io"] = True
    config.write_text(json.dumps(configured))
    landing_pass(shell, config)
    observed = records(capsys)
    assert {row["phase"] for row in observed} == {"gate-setup", "rows-and-gating", "pass-cleanup", "post-state", "flush-and-beat"}
    assert len(observed) == 5 and len(calls) == 10
    assert all(row["pid"] == os.getpid() and row["completed"] and row["seconds"] >= 0 for row in observed)


@pytest.mark.parametrize("gate_raises", [False, True])
def test_actual_landing_cleanup_survives_failed_initial_capture(shell, config, tmp_path, monkeypatch, capsys, gate_raises):
    landing = landing_of(shell, config, tmp_path)
    landing.runner.pass_io = pass_io.OnePassIO(True)
    cleanup = []
    failure = ValueError("original gate failure")

    def counter_failed():
        raise OSError(5, "cleanup counter failure")

    def gate():
        if gate_raises:
            raise failure

    monkeypatch.setattr(pass_io, "snapshot", counter_failed)
    monkeypatch.setattr(landing, "gate_pass", gate)
    monkeypatch.setattr(landing.cci_tail, "stop", lambda: cleanup.append("stop"))
    monkeypatch.setattr(landing, "report_unread", lambda: cleanup.append("report"))
    monkeypatch.setattr(landing, "keep_builds", lambda: cleanup.append("keep"))
    if gate_raises:
        with pytest.raises(ValueError) as raised:
            landing.run()
        assert raised.value is failure
    else:
        landing.run()
    assert cleanup == ["stop", "report", "keep"]
    observed = records(capsys)
    assert len(observed) == 1 and observed[0]["phase"] == "pass-cleanup"
    assert observed[0]["status"] == "COUNTER_ERROR" and observed[0]["delta"] is None


def test_orca_without_optional_landing_keeps_native_diagnostic_disabled(shell, config, monkeypatch, capsys):
    configured = json.loads(config.read_text())
    del configured["landing"]
    config.write_text(json.dumps(configured))
    stops = []
    keep_alive = runner_module.Runner.keep_alive

    def keeping(self, desk):
        stop = keep_alive(self, desk)
        stops.append(stop)
        return stop

    def forbidden():
        raise AssertionError("no-landing Orca read native diagnostic counters")

    monkeypatch.setattr(pass_io, "snapshot", forbidden)
    monkeypatch.setattr(runner_module.Runner, "keep_alive", keeping)
    try:
        orca_pass(shell, config)
    finally:
        for stop in stops:
            stop.set()
    assert stops and not records(capsys)
