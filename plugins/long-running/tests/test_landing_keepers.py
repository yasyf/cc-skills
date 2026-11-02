import threading
from datetime import timedelta

import pytest

from test_build_cache import readonly_connections
from test_desk_runner import Reexeced, config, landing_of, runner_module, shell


class WaitingStop(threading.Event):
    def __init__(self):
        super().__init__()
        self.waiting = threading.Event()
        self.timeouts = []

    def wait(self, timeout=None):
        self.timeouts.append(timeout)
        self.waiting.set()
        return super().wait(timeout)


@pytest.mark.parametrize("keeper", ["refresh", "overflow"])
def test_close_wakes_and_joins_its_waiting_keeper(shell, config, tmp_path, monkeypatch, readonly_connections, keeper):
    landing = landing_of(shell, config, tmp_path)
    stop = landing.keepers_stopped = WaitingStop()
    monkeypatch.setattr(runner_module, "REFRESH_EVERY", timedelta(hours=1))
    monkeypatch.setattr(runner_module, "OVERFLOW_EVERY", timedelta(hours=1))
    monkeypatch.setattr(landing, "down_since", lambda: False)
    before = len(readonly_connections)
    calls = []

    def read(self):
        calls.append(keeper)
        self.book.store.header(runner_module.LANDING)

    monkeypatch.setattr(runner_module.Landing, "refresh", read)
    monkeypatch.setattr(runner_module.Landing, "overflow", lambda self, gate: read(self))
    try:
        if keeper == "refresh":
            landing.keep_fresh()
            thread = landing.refresher
        else:
            landing.keep_overflowing()
            thread = landing.overflowing
        assert stop.waiting.wait(2)
        landing.close()
        assert stop.is_set() and not thread.is_alive()
        assert calls == [keeper] and len(stop.timeouts) == 1 and 0 < stop.timeouts[0] <= 3600
        opened = [connection for connection in readonly_connections[before:] if connection.owner == thread.ident]
        assert len(opened) == 1 and opened[0].closed
        if landing.overflower is not None:
            assert all(pool._shutdown for pool in (landing.overflower.workers, landing.overflower.side, landing.overflower.tidying))
    finally:
        landing.close()


@pytest.mark.parametrize("keeper", ["refresh", "overflow"])
def test_close_finishes_inflight_keeper_work_before_returning(shell, config, tmp_path, monkeypatch, keeper):
    landing = landing_of(shell, config, tmp_path)
    entered, release, returned = threading.Event(), threading.Event(), threading.Event()
    monkeypatch.setattr(landing, "down_since", lambda: False)

    def work(self):
        entered.set()
        release.wait()

    def close():
        landing.close()
        returned.set()

    monkeypatch.setattr(runner_module.Landing, "refresh", work)
    monkeypatch.setattr(runner_module.Landing, "overflow", lambda self, gate: work(self))
    closer = threading.Thread(target=close, daemon=True)
    try:
        if keeper == "refresh":
            landing.keep_fresh()
        else:
            landing.keep_overflowing()
        assert entered.wait(2)
        closer.start()
        assert landing.keepers_stopped.wait(2)
        assert not returned.is_set()
        release.set()
        closer.join(2)
        assert returned.is_set() and not closer.is_alive()
        assert all(thread is None or not thread.is_alive() for thread in (landing.refresher, landing.overflowing))
    finally:
        release.set()
        if closer.ident is not None:
            closer.join(2)
        landing.close()


@pytest.mark.parametrize("exit_mode", ["once", "stop", "exception", "refresh-start-error", "start-error", "heartbeat-error", "reexec"])
def test_controller_exit_closes_its_owned_keeper_lifetime(shell, config, tmp_path, monkeypatch, exit_mode):
    instances = []
    initialize = runner_module.Landing.__init__

    def initialized(self, *args, **kwargs):
        initialize(self, *args, **kwargs)
        instances.append(self)

    def run(self):
        if exit_mode == "stop":
            raise runner_module.StopRequested()
        if exit_mode == "exception":
            raise KeyboardInterrupt("private keeper lifetime")
        if exit_mode == "reexec":
            raise RuntimeError("private keeper reexec")

    start = threading.Thread.start
    start_failure = RuntimeError("private keeper start")

    def failed_start(thread):
        target = "landing-refresh" if exit_mode == "refresh-start-error" else "landing-overflow"
        if thread.name == target:
            raise start_failure
        return start(thread)

    alive = threading.Event()

    def keep_alive(desk):
        if exit_mode == "heartbeat-error":
            raise RuntimeError("private heartbeat start")
        return alive

    def reexec(desk):
        assert all(thread is None or not thread.is_alive() for item in instances for thread in (item.refresher, item.overflowing))
        raise Reexeced()

    monkeypatch.setattr(runner_module.Landing, "__init__", initialized)
    monkeypatch.setattr(runner_module.Landing, "refresh", lambda self: None)
    monkeypatch.setattr(runner_module.Landing, "overflow", lambda self, gate: None)
    monkeypatch.setattr(runner_module.Landing, "down_since", lambda self: False)
    monkeypatch.setattr(runner_module.Landing, "run", run)
    monkeypatch.setattr(runner_module.Landing, "post_state", lambda self, took: None)
    monkeypatch.setattr(runner_module.Landing, "drain", lambda self: None)
    if exit_mode in {"refresh-start-error", "start-error"}:
        monkeypatch.setattr(threading.Thread, "start", failed_start)
    runner = runner_module.Runner(shell, runner_module.Config.load(config), runner_module.actions.Store(tmp_path / "store"))
    monkeypatch.setattr(runner, "keep_alive", keep_alive)
    monkeypatch.setattr(runner, "flush", lambda: None)
    monkeypatch.setattr(runner, "beat", lambda desk: None)
    monkeypatch.setattr(runner, "moved", lambda: False)
    monkeypatch.setattr(runner, "pass_raised", lambda desk, error: True)
    monkeypatch.setattr(runner, "reexec", reexec)
    if exit_mode == "stop":
        runner.stop_marker().write_text("private keeper lifetime")
    try:
        if exit_mode in {"once", "stop"}:
            assert runner_module.run_landing(runner, exit_mode == "once") == 0
        else:
            expected = {"exception": KeyboardInterrupt, "refresh-start-error": RuntimeError, "start-error": RuntimeError, "heartbeat-error": RuntimeError, "reexec": Reexeced}[exit_mode]
            with pytest.raises(expected) as raised:
                runner_module.run_landing(runner, False)
            if exit_mode in {"refresh-start-error", "start-error"}:
                assert raised.value is start_failure
        assert instances
        for landing in instances:
            assert landing.keepers_stopped.is_set()
            assert all(thread is None or not thread.is_alive() for thread in (landing.refresher, landing.overflowing))
            assert all(pool._shutdown for pool in (landing.workers, landing.side, landing.tidying))
        if exit_mode not in {"refresh-start-error", "start-error", "heartbeat-error"}:
            assert alive.is_set()
    finally:
        for landing in reversed(instances):
            landing.close()
