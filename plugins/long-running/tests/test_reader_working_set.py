import json
import sqlite3
import threading
from datetime import timedelta
from random import Random

import pytest

from long_running import sqlite_store
from test_desk_runner import config, landing_of, runner_module, shell


@pytest.mark.parametrize("lanes,seed", [pytest.param(lanes, seed, id=f"lanes-{lanes}-seed-{seed}") for lanes in (62, 63, 64) for seed in (0, 1, 7)])
def test_actual_refusal_reader_working_set_boundary(shell, config, tmp_path, monkeypatch, lanes, seed):
    monkeypatch.setattr(sqlite_store, "Random", lambda _: Random(seed), raising=False)
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
    counts = {"opens": 0, "closes": 0, "changes": 0, "live": 0, "peak": 0}

    class Observed(sqlite3.Connection):
        def close(self):
            counts["closes"] += 1
            counts["live"] -= 1
            counts["changes"] += self.total_changes
            super().close()

    def observed(database, *args, **kwargs):
        if threading.get_ident() != owner or not str(database).endswith("?mode=ro"):
            return connect(database, *args, **kwargs)
        connection = connect(database, *args, **kwargs, factory=Observed)
        counts["opens"] += 1
        counts["live"] += 1
        counts["peak"] = max(counts["peak"], counts["live"])
        return connection

    monkeypatch.setattr(sqlite3, "connect", observed)
    rows = {member: {"head": head}}
    since = shell.now() - timedelta(minutes=1)
    with book.store.reading():
        first = landing.last_refusal({member}, rows, since)
        cold = counts["opens"]
        with book.store.transaction(runner_module.LANDING) as state:
            state.actions[key].target = json.dumps({"text": "fresh refusal"})
        warm = []
        for _ in range(16):
            before = counts["opens"]
            result = landing.last_refusal({member}, rows, since)
            warm.append(counts["opens"] - before)
            assert "fresh refusal" in result[0] and result[1] == "selected-owner"
    print(json.dumps({"caller": "Landing.last_refusal", "lanes": lanes, "seed": seed, "database_working_set": lanes + 2, "cold_opens": cold, "warm_opens": warm, "counts": counts}))
    assert "first refusal" in first[0] and first[1] == "selected-owner"
    assert cold == lanes + 2
    bound = 0 if lanes == 62 else (lanes - 62) * 8 * len(warm)
    assert sum(warm) <= bound, f"cyclic reader working set reopened {sum(warm)} connections; budget {bound}"
    assert counts["peak"] <= 64 and counts["live"] == 0
    assert counts["opens"] == counts["closes"] and counts["changes"] == 0


@pytest.mark.parametrize("seed", [0, 1, 7])
def test_reader_cache_adapts_after_sustained_disjoint_working_sets(shell, config, tmp_path, monkeypatch, seed):
    monkeypatch.setattr(sqlite_store, "Random", lambda _: Random(seed), raising=False)
    landing = landing_of(shell, config, tmp_path)
    book = landing.book
    groups = [[runner_module.lane_container(f"phase-{phase}-{index:03}") for index in range(width)] for phase, width in enumerate((65, 64, 64))]
    for group in groups:
        for container in group:
            book.ensure(container, container)
    connect = sqlite3.connect
    counts = {"opens": 0, "closes": 0, "live": 0, "peak": 0, "changes": 0}

    class Observed(sqlite3.Connection):
        def close(self):
            counts["closes"] += 1
            counts["live"] -= 1
            counts["changes"] += self.total_changes
            super().close()

    def observed(database, *args, **kwargs):
        if not str(database).endswith("?mode=ro"):
            return connect(database, *args, **kwargs)
        connection = connect(database, *args, **kwargs, factory=Observed)
        counts["opens"] += 1
        counts["live"] += 1
        counts["peak"] = max(counts["peak"], counts["live"])
        return connection

    monkeypatch.setattr(sqlite3, "connect", observed)
    phases = []
    with book.store.reading():
        for group, rounds in zip(groups, (8, 16, 16)):
            costs = []
            for _ in range(rounds):
                before = counts["opens"]
                for container in group:
                    assert book.header(container)["incident_id"] == container
                costs.append(counts["opens"] - before)
            phases.append(costs)
    print(json.dumps({"seed": seed, "phase_costs": phases, "counts": counts}))
    for costs in phases[1:]:
        assert sum(costs) < 64 * 8 and sum(costs[-4:]) <= 64, f"reader cache kept an obsolete working set: {costs}"
    assert counts["peak"] <= 64 and counts["live"] == 0
    assert counts["opens"] == counts["closes"] and counts["changes"] == 0
