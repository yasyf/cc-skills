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
    assert counts["decoded_actions"] == history * 3 + 3
