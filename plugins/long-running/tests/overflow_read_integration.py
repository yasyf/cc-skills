from datetime import timedelta
import json
import sqlite3
import threading

import pytest

from long_running import actions
from test_desk_runner import config, landing_of, runner_module, shell


@pytest.mark.parametrize("history", [32, 10_000])
def test_actual_overflow_refusal_historical_read_cost(shell, config, tmp_path, monkeypatch, history):
    landing = landing_of(shell, config, tmp_path)
    book = landing.book
    now = shell.now()
    member, head = "29020", "cccc333333"
    key = f"blocker:{member}:{head}"
    target = json.dumps({"text": "selected routed refusal"})
    with book.store.transaction(runner_module.LANDING) as state:
        for index in range(history):
            state.accept(f"archive:{index}", "bus", json.dumps({"text": "unrelated " + "x" * 256}), None, now)
        state.accept(key, "bus", target, None, now - timedelta(seconds=2))
    lanes = [runner_module.lane_container(f"history-{index}") for index in range(56)]
    for offset, lane in enumerate(lanes):
        book.ensure(lane, lane)
        with book.store.transaction(lane) as state:
            for index in range(offset, history, len(lanes)):
                state.accept(f"archive:{index}", "relay", json.dumps({"text": "unrelated " + "x" * 256}), None, now)
            if offset == 0:
                state.accept(key, "relay", target, None, now - timedelta(seconds=1))
    with book.store.transaction(runner_module.RUNNER) as state:
        for index in range(history):
            state.accept(f"archive:{index}", "escalation", "ARCHIVE unrelated " + "x" * 256, None, now)
        state.accept("selected", "escalation", f"GATE-FAILED #{member} selected newest refusal", None, now)
    monkeypatch.setattr(landing.runner, "landing_lanes", lambda _: [])
    monkeypatch.setattr(landing, "owner", lambda *_: "selected-owner")
    counts = {"opens": 0, "closes": 0, "changed_rows": 0, "decoded_actions": 0, "decoded_bytes": 0}
    owner = threading.get_ident()
    connect, load = sqlite3.connect, actions.Action.load

    class Observed(sqlite3.Connection):
        def close(self):
            counts["closes"] += 1
            counts["changed_rows"] += self.total_changes
            super().close()

    def observed(*args, **kwargs):
        if threading.get_ident() != owner:
            return connect(*args, **kwargs)
        counts["opens"] += 1
        return connect(*args, **kwargs, factory=Observed)

    def decoded(record):
        if threading.get_ident() == owner:
            counts["decoded_actions"] += 1
            counts["decoded_bytes"] += len(json.dumps(record, separators=(",", ":")).encode())
        return load(record)

    monkeypatch.setattr(sqlite3, "connect", observed)
    monkeypatch.setattr(actions.Action, "load", staticmethod(decoded))
    result = landing.last_refusal({member}, {member: {"head": head}}, now - timedelta(minutes=1))
    print(json.dumps({"history": history, "lanes": len(lanes), "caller": "Landing.last_refusal", "costs": counts, "result": result}))
    assert result is not None and result[1] == "selected-owner" and "selected newest refusal" in result[0]
    assert counts["opens"] == counts["closes"] == len(lanes) + 2
    assert counts["changed_rows"] == 0
    assert counts["decoded_actions"] == 3, f"overflow route history decoded {counts['decoded_actions']} actions; expected 3"


@pytest.mark.parametrize("kind", ["bus", "relay"])
@pytest.mark.parametrize("key", ["blocker:29020:cccc333333", "bot-held:29020:cccc333333", "conflict:29020:dev"])
def test_selected_overflow_routes_preserve_kind_head_base_and_old_refusals(shell, config, tmp_path, monkeypatch, kind, key):
    landing = landing_of(shell, config, tmp_path)
    lane = runner_module.lane_container("selected")
    landing.book.ensure(lane, lane)
    with landing.book.store.transaction(runner_module.LANDING if kind == "bus" else lane) as state:
        state.accept("blocker:29020:old", kind, "not-json", None, shell.now())
        state.accept(key, kind, json.dumps({"text": "selected old refusal"}), None, shell.now() - timedelta(days=1))
    with landing.book.store.transaction(lane if kind == "bus" else runner_module.LANDING) as state:
        state.accept(key, "note", "not-json", None, shell.now())
    monkeypatch.setattr(landing.runner, "landing_lanes", lambda _: [])
    monkeypatch.setattr(landing, "owner", lambda *_: "selected-owner")
    result = landing.last_refusal({"29020"}, {"29020": {"head": "cccc333333ffff", "base": "dev"}}, shell.now())
    assert result is not None and "selected old refusal" in result[0] and result[1] == "selected-owner"


def test_selected_overflow_route_errors_and_tie_order_remain_visible(shell, config, tmp_path, monkeypatch):
    landing = landing_of(shell, config, tmp_path)
    lane = runner_module.lane_container("selected")
    landing.book.ensure(lane, lane)
    key = "blocker:29020:cccc333333"
    for container, kind, text in ((runner_module.LANDING, "bus", "z-first"), (lane, "relay", "a-last")):
        with landing.book.store.transaction(container) as state:
            state.accept(key, kind, json.dumps({"text": text}), None, shell.now())
    monkeypatch.setattr(landing.runner, "landing_lanes", lambda _: [])
    monkeypatch.setattr(landing, "owner", lambda *_: "selected-owner")
    result = landing.last_refusal({"29020"}, {"29020": {"head": "cccc333333"}}, shell.now())
    assert result is not None and "z-first" in result[0]
    with landing.book.store.transaction(lane) as state:
        state.accept("conflict:29020:dev", "relay", "not-json", None, shell.now())
    with pytest.raises(json.JSONDecodeError):
        landing.last_refusal({"29020"}, {"29020": {"head": "cccc333333", "base": "dev"}}, shell.now())


def test_multiple_overflow_members_keep_exact_keys_and_escalation_time_floor(shell, config, tmp_path, monkeypatch):
    landing = landing_of(shell, config, tmp_path)
    lane = runner_module.lane_container("selected")
    landing.book.ensure(lane, lane)
    with landing.book.store.transaction(runner_module.LANDING) as state:
        state.accept("blocker:29020:aaaa111111", "bus", json.dumps({"text": "first old route"}), None, shell.now() - timedelta(days=1))
    with landing.book.store.transaction(lane) as state:
        state.accept("conflict:29021:dev", "relay", json.dumps({"text": "second newest route"}), None, shell.now())
        state.accept("blocker:29021:old", "relay", "not-json", None, shell.now())
    with landing.book.store.transaction(runner_module.RUNNER) as state:
        state.accept("old", "escalation", "GATE-FAILED #29020 too old", None, shell.now() - timedelta(seconds=1))
        state.accept("other", "escalation", "GATE-FAILED #290210 wrong PR", None, shell.now() + timedelta(seconds=1))
    monkeypatch.setattr(landing.runner, "landing_lanes", lambda _: [])
    monkeypatch.setattr(landing, "owner", lambda pr, _: f"owner-{pr}")
    result = landing.last_refusal({"29020", "29021"}, {"29020": {"head": "aaaa111111ffff"}, "29021": {"head": "bbbb222222ffff", "base": "dev"}}, shell.now())
    assert result is not None and "second newest route" in result[0] and result[1] == "owner-29021"


def test_overflow_thread_reuses_readers_sees_writes_and_closes_on_exit(shell, config, tmp_path, monkeypatch):
    landing = landing_of(shell, config, tmp_path)
    landing.book.edit(runner_module.LANDING, lambda state: state.facts.update({"reader-probe": 1}))
    thread_class, real_sleep, connect = threading.Thread, runner_module.time.sleep, sqlite3.connect
    main_thread = threading.get_ident()
    selected, values, opens = [], [], []
    first, resume, stop = threading.Event(), threading.Event(), threading.Event()
    counts = {"opens": 0, "closes": 0}

    class EndLoop(BaseException):
        pass

    class Observed(sqlite3.Connection):
        def close(self):
            counts["closes"] += 1
            super().close()

    def observed(*args, **kwargs):
        if selected and threading.current_thread() is selected[0]:
            counts["opens"] += 1
            return connect(*args, **kwargs, factory=Observed)
        return connect(*args, **kwargs)

    def thread(*args, **kwargs):
        if kwargs.get("name") != "landing-overflow" or threading.get_ident() != main_thread:
            return thread_class(*args, **kwargs)
        target = kwargs["target"]

        def run():
            try:
                target()
            except EndLoop:
                pass

        kwargs["target"] = run
        made = thread_class(*args, **kwargs)
        selected.append(made)
        return made

    def probe(self, gate):
        values.append(self.book.fact(runner_module.LANDING, "reader-probe"))
        opens.append(counts["opens"])

    def sleep(seconds):
        if not selected or threading.current_thread() is not selected[0]:
            return real_sleep(seconds)
        if stop.is_set() or len(values) >= 2:
            raise EndLoop
        first.set()
        if not resume.wait(5):
            raise EndLoop

    monkeypatch.setattr(sqlite3, "connect", observed)
    monkeypatch.setattr(runner_module.threading, "Thread", thread)
    monkeypatch.setattr(runner_module.Landing, "overflow", probe)
    monkeypatch.setattr(runner_module.time, "sleep", sleep)
    try:
        landing.keep_overflowing()
        assert first.wait(5)
        landing.book.edit(runner_module.LANDING, lambda state: state.facts.update({"reader-probe": 2}))
        resume.set()
        selected[0].join(5)
    finally:
        stop.set()
        resume.set()
        if selected:
            selected[0].join(5)
    assert selected and not selected[0].is_alive()
    assert values == [1, 2]
    assert selected[0].ident != main_thread
    print(json.dumps({"caller": "Landing.keep_overflowing", "iterations": values, "reader_opens": opens, "costs": counts}))
    assert counts == {"opens": 1, "closes": 1}, f"overflow thread opened {counts['opens']} readers; expected 1"
    assert opens == [1, 1]
