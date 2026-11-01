import json
import sqlite3
from contextlib import closing

import pytest

from blocker_index_old_writer_integration import old_actions
from test_cut_release_read_proof import release_runner
from test_desk_runner import actions, config, runner_module, shell


def test_immutable_old_writer_completes_old_ordinal_and_changes_payload(shell, config, tmp_path):
    runner = release_runner(shell, config, tmp_path, late=True)
    old = old_actions()
    writer = old.Store(runner.book.store.root)
    with runner.book.store.reading():
        runner.open_cut_clocks()
        with writer.transaction(runner_module.LANDING) as state:
            state.start("release:90001", shell.now())
            state.complete("release:90001", {"at": old.stamp(shell.now())})
        runner.open_cut_clocks()
        assert runner.book.fact_path(runner_module.RUNNER, [runner_module.CUT_CLOCKS, "release:90001", "pr"]) == 90001
        runner.open_cut_clocks()
        with writer.transaction(runner_module.LANDING) as state:
            state.actions["release:1"].target = "{"
        with pytest.raises(json.JSONDecodeError):
            runner.open_cut_clocks()
        assert runner.cut_clock_proof is None


def test_immutable_old_creator_needs_no_metadata_or_schema_change(shell, config, tmp_path):
    old = old_actions()
    writer = old.Store(tmp_path / "old")
    stamp = old.stamp(shell.now())
    writer.create(old.Incident(runner_module.RUNNER, "owner", 0, stamp, {runner_module.CUT_CURSOR: 10, runner_module.CUT_CLOCKS: {"release:1": {"ended": "done"}}}))
    incident = old.Incident(runner_module.LANDING, "owner", 0, stamp, {})
    incident.accept("release:1", "release", json.dumps({"pr": 1, "components": ["sand"], "sha": "94d61b91951"}), None, shell.now())
    incident.start("release:1", shell.now())
    incident.complete("release:1", {"at": stamp})
    writer.create(incident)
    store = actions.Store(writer.root)
    schemas = {}
    for container in (runner_module.RUNNER, runner_module.LANDING):
        with closing(sqlite3.connect(store.path(container))) as observer:
            schemas[container] = observer.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name").fetchall()
    runner = runner_module.Runner(shell, runner_module.Config.load(config), store)
    with store.reading():
        for _ in range(3):
            runner.open_cut_clocks()
        with writer.transaction(runner_module.RUNNER) as state:
            del state.facts[runner_module.CUT_CLOCKS]["release:1"]
        runner.open_cut_clocks()
        assert runner.book.fact_path(runner_module.RUNNER, [runner_module.CUT_CLOCKS, "release:1", "pr"]) == 1
    for container in schemas:
        with closing(sqlite3.connect(store.path(container))) as observer:
            assert observer.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name").fetchall() == schemas[container]
