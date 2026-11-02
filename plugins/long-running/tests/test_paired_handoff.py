import contextlib
import os
from datetime import timedelta

import pytest

from long_running import actions
from test_desk_runner import cli, config, newer, pin, pinned, runner_module, shell, successor


DESKS = ("landing", "orca")
PIDS = {"landing": 41001, "orca": 41002}


def prepared(shell, config, tmp_path, monkeypatch):
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    pin(config, runner_module.RUNNING_VERSION)
    monkeypatch.setattr(runner_module.drive, "release_faults", lambda _: [])
    monkeypatch.setattr(runner_module.Runner, "successor_fault", lambda *_: "")
    monkeypatch.setattr(runner_module.Runner, "replay_refusal", lambda *_: "")
    return runner


def hold(runner, desk):
    held = contextlib.ExitStack()
    assert held.enter_context(runner.desk_lock(desk)) is None
    runner.config.source.with_name(f"{runner.config.source.name}.{desk}.lock").write_text(f"{PIDS[desk]}\n")
    return held


@pytest.mark.parametrize("supervised", [set(), {"landing"}, set(DESKS)])
def test_pair_stops_each_old_holder_before_one_pin_and_releases_before_successors(shell, config, tmp_path, monkeypatch, capsys, supervised):
    runner = prepared(shell, config, tmp_path, monkeypatch)
    version = newer(runner_module.RUNNING_VERSION)
    old = {desk: hold(runner, desk) for desk in DESKS}
    events = []
    unrelated = runner.stop_marker(49000)
    unrelated.write_text("49000\n")
    monkeypatch.setattr(runner_module.Runner, "supervised", lambda _, desk: desk in supervised)

    def exits(_):
        assert pinned(config) == runner_module.RUNNING_VERSION
        desk = next(desk for desk in DESKS if runner.stop_marker(PIDS[desk]).exists())
        events.append(("exit", desk))
        runner.stop_marker(PIDS[desk]).unlink()
        old[desk].close()

    original_pin = runner_module.Runner.pin

    def pins(self, target):
        assert all(self.desk_holder(desk) == os.getpid() for desk in DESKS)
        assert events == [("exit", desk) for desk in DESKS]
        events.append(("pin", target))
        original_pin(self, target)

    def awaits(self, desk, predecessor):
        assert predecessor == PIDS[desk]
        assert all(self.desk_holder(name) is None for name in DESKS)
        events.append(("await", desk))
        return 0

    monkeypatch.setattr(shell, "sleep", exits)
    monkeypatch.setattr(runner_module.Runner, "pin", pins)
    monkeypatch.setattr(runner_module.Runner, "await_launchd", awaits)
    try:
        assert cli(shell, config, "handoff", "--paired", "--version", version) == 0
    finally:
        for held in old.values():
            held.close()
    assert events == [("exit", desk) for desk in DESKS] + [("pin", version)] + [("await", desk) for desk in DESKS if desk in supervised]
    assert shell.successors == [successor(config, tmp_path, desk) for desk in DESKS if desk not in supervised]
    assert unrelated.read_text() == "49000\n"
    assert not any(runner.stop_marker(PIDS[desk]).exists() for desk in DESKS)
    assert pinned(config) == version
    assert "target uptake still requires native process evidence and two fresh target heartbeats per desk" in capsys.readouterr().out
    assert shell.reexeced == []


@pytest.mark.parametrize("failure", ["release", "landing", "orca", "replay"])
def test_pair_validates_both_successors_before_any_stop_or_pin(shell, config, tmp_path, monkeypatch, failure):
    runner = prepared(shell, config, tmp_path, monkeypatch)
    old = {desk: hold(runner, desk) for desk in DESKS}
    checked = []
    monkeypatch.setattr(runner_module.Runner, "supervised", lambda *_: False)
    monkeypatch.setattr(runner_module.drive, "release_faults", lambda _: ["edited source"] if failure == "release" else [])

    def preflight(_, desk, *args):
        checked.append(desk)
        return "refused" if desk == failure else ""

    monkeypatch.setattr(runner_module.Runner, "successor_fault", preflight)
    monkeypatch.setattr(runner_module.Runner, "replay_refusal", lambda *_: "replay refused" if failure == "replay" else "")
    try:
        assert cli(shell, config, "handoff", "--paired", "--version", newer(runner_module.RUNNING_VERSION)) == (1 if failure == "release" else 3)
        assert all(runner.desk_holder(desk) == PIDS[desk] for desk in DESKS)
        assert not any(runner.stop_marker(PIDS[desk]).exists() for desk in DESKS)
    finally:
        for held in old.values():
            held.close()
    assert checked == ([] if failure == "release" else ["landing"] if failure == "landing" else list(DESKS))
    assert pinned(config) == runner_module.RUNNING_VERSION
    assert shell.successors == []


def test_pair_second_holder_timeout_releases_first_lock_keeps_pin_and_starts_nothing(shell, config, tmp_path, monkeypatch):
    runner = prepared(shell, config, tmp_path, monkeypatch)
    old = hold(runner, "orca")
    monkeypatch.setattr(runner_module, "HANDOFF_WAIT", timedelta(seconds=1))
    monkeypatch.setattr(runner_module.Runner, "supervised", lambda *_: False)
    try:
        assert cli(shell, config, "handoff", "--paired", "--version", newer(runner_module.RUNNING_VERSION)) == 1
        assert runner.control.holder("landing") is None
        assert runner.desk_holder("orca") == PIDS["orca"]
        assert runner.stop_marker(PIDS["orca"]).read_text() == f"{PIDS['orca']}\n"
    finally:
        old.close()
    assert pinned(config) == runner_module.RUNNING_VERSION
    assert shell.successors == []


def test_pair_pin_failure_releases_both_lifetime_locks_without_starting_a_successor(shell, config, tmp_path, monkeypatch):
    runner = prepared(shell, config, tmp_path, monkeypatch)
    monkeypatch.setattr(runner_module.Runner, "supervised", lambda *_: False)

    def failed_pin(*_):
        raise OSError("pin write refused")

    monkeypatch.setattr(runner_module.Runner, "pin", failed_pin)
    with pytest.raises(OSError, match="pin write refused"):
        cli(shell, config, "handoff", "--paired", "--version", newer(runner_module.RUNNING_VERSION))
    assert all(runner.desk_holder(desk) is None for desk in DESKS)
    assert pinned(config) == runner_module.RUNNING_VERSION
    assert shell.successors == []


def test_pair_checks_the_other_supervised_successor_after_one_wait_refuses(shell, config, tmp_path, monkeypatch):
    prepared(shell, config, tmp_path, monkeypatch)
    monkeypatch.setattr(runner_module.Runner, "supervised", lambda *_: True)
    checked = []
    monkeypatch.setattr(runner_module.Runner, "await_launchd", lambda _, desk, pid: checked.append(desk) or (1 if desk == "landing" else 0))
    assert cli(shell, config, "handoff", "--paired", "--version", newer(runner_module.RUNNING_VERSION)) == 1
    assert checked == list(DESKS)
    assert shell.successors == []


def test_startup_can_read_the_old_pin_before_a_paired_unlock_and_must_not_be_called_target_uptake(shell, config, tmp_path, monkeypatch):
    runner = prepared(shell, config, tmp_path, monkeypatch)
    target = newer(runner_module.RUNNING_VERSION)
    original_lock = runner_module.Runner.desk_lock
    entered = []

    @contextlib.contextmanager
    def after_pin(self, desk):
        assert pinned(config) == runner_module.RUNNING_VERSION
        pin(config, target)
        with original_lock(self, desk) as holder:
            yield holder

    def run_stale(self, once):
        entered.append((runner_module.RUNNING_VERSION, self.pinned(), self.moved()))
        return 0

    monkeypatch.setattr(runner_module.Runner, "desk_lock", after_pin)
    monkeypatch.setattr(runner_module, "run_orca", run_stale)
    assert cli(shell, config, "run", "--desk", "orca", "--once") == 0
    assert entered == [(runner_module.RUNNING_VERSION, target, True)]
    assert shell.reexeced == []
    assert runner.control.holder("landing") is None


def test_pair_replacement_holder_refuses_without_pin_and_releases_the_first_lock(shell, config, tmp_path, monkeypatch):
    runner = prepared(shell, config, tmp_path, monkeypatch)
    old = hold(runner, "orca")
    replacement = []
    monkeypatch.setattr(runner_module.Runner, "supervised", lambda *_: False)

    def replaced(_):
        old.close()
        replacement.append(hold(runner, "orca"))
        runner.config.source.with_name(f"{runner.config.source.name}.orca.lock").write_text("42002\n")

    monkeypatch.setattr(shell, "sleep", replaced)
    try:
        assert cli(shell, config, "handoff", "--paired", "--version", newer(runner_module.RUNNING_VERSION)) == 1
        assert runner.control.holder("landing") is None
        assert runner.control.holder("orca") == 42002
    finally:
        old.close()
        for held in replacement:
            held.close()
    assert runner.stop_marker(PIDS["orca"]).exists()
    assert not runner.stop_marker(42002).exists()
    assert pinned(config) == runner_module.RUNNING_VERSION
    assert shell.successors == []


def test_interrupted_pair_releases_the_first_lifetime_lock_without_clearing_the_unfinished_stop(shell, config, tmp_path, monkeypatch):
    runner = prepared(shell, config, tmp_path, monkeypatch)
    old = hold(runner, "orca")
    monkeypatch.setattr(runner_module.Runner, "supervised", lambda *_: False)

    def interrupted(_):
        raise RuntimeError("interrupted wait")

    monkeypatch.setattr(shell, "sleep", interrupted)
    try:
        with pytest.raises(RuntimeError, match="interrupted wait"):
            cli(shell, config, "handoff", "--paired", "--version", newer(runner_module.RUNNING_VERSION))
        assert runner.control.holder("landing") is None
        assert runner.control.holder("orca") == PIDS["orca"]
        assert runner.stop_marker(PIDS["orca"]).exists()
    finally:
        old.close()
    assert pinned(config) == runner_module.RUNNING_VERSION
    assert shell.successors == []
