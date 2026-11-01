import json
import sqlite3
import threading
from datetime import timedelta

import pytest

from test_desk_runner import config, landing_of, runner_module, shell


@pytest.mark.parametrize("lanes", [62, 63, 64])
def test_actual_refusal_reader_working_set_boundary(shell, config, tmp_path, monkeypatch, lanes):
    landing = landing_of(shell, config, tmp_path)
    book = landing.book
    member, head = "29020", "cccc333333"
    key = f"blocker:{member}:{head}"
    for index in range(lanes):
        container = runner_module.lane_container(f"working-set-{index:03}")
        book.ensure(container, container)
    with book.store.transaction(runner_module.LANDING) as state:
        state.accept(key, "bus", json.dumps({"text": "first refusal"}), None, shell.now())
    monkeypatch.setattr(landing.runner, "landing_lanes", lambda _: [])
    monkeypatch.setattr(landing, "owner", lambda *_: "selected-owner")
    connect = sqlite3.connect
    owner = threading.get_ident()
    counts = {"opens": 0, "closes": 0, "changes": 0}

    class Observed(sqlite3.Connection):
        def close(self):
            counts["closes"] += 1
            counts["changes"] += self.total_changes
            super().close()

    def observed(database, *args, **kwargs):
        if threading.get_ident() != owner or not str(database).endswith("?mode=ro"):
            return connect(database, *args, **kwargs)
        counts["opens"] += 1
        return connect(database, *args, **kwargs, factory=Observed)

    monkeypatch.setattr(sqlite3, "connect", observed)
    rows = {member: {"head": head}}
    since = shell.now() - timedelta(minutes=1)
    with book.store.reading():
        first = landing.last_refusal({member}, rows, since)
        cold = counts["opens"]
        with book.store.transaction(runner_module.LANDING) as state:
            state.actions[key].target = json.dumps({"text": "fresh refusal"})
        second = landing.last_refusal({member}, rows, since)
        warm = counts["opens"] - cold
        before_third = counts["opens"]
        third = landing.last_refusal({member}, rows, since)
        repeated = counts["opens"] - before_third
    print(json.dumps({"caller": "Landing.last_refusal", "lanes": lanes, "database_working_set": lanes + 2, "cold_opens": cold, "warm_opens": warm, "repeated_opens": repeated, "counts": counts}))
    assert "first refusal" in first[0] and first[1] == "selected-owner"
    assert "fresh refusal" in second[0] and second == third
    assert cold == lanes + 2
    assert warm == repeated == (0 if lanes == 62 else lanes + 2)
    assert counts["opens"] == counts["closes"] and counts["changes"] == 0
