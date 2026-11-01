import json
import sqlite3

import pytest

from test_desk_runner import WALKER, actions, config, landing_of, runner_module, shell


def clock_runner(shell, config, tmp_path, initial=None):
    runner = landing_of(shell, config, tmp_path).runner
    runner.book.ensure(runner_module.RUNNER, runner_module.RUNNER)
    runner.book.ensure(runner_module.LANDING, runner_module.LANDING)
    if initial is not None:
        runner.book.edit(runner_module.RUNNER, lambda state: state.facts.update(initial))
    return runner


def initialized(cursor=10):
    return {runner_module.CUT_CURSOR: cursor, runner_module.CUT_CLOCKS: {}}


def cut_record(seq, **changes):
    return {"seq": seq, "drive": "d1", "lane": runner_module.ROOT, "kind": "go", "text": "cut sand now at 94d61b91951", "refs": {"targets": ["sand"]}, "at": "2026-10-01T18:00:00Z", "to": [WALKER]} | changes


def release_action(runner, shell, pr=41):
    stamp = actions.stamp(shell.now())
    target = json.dumps({"pr": pr, "components": ["sand"], "sha": "94d61b91951"})
    action = actions.Action(runner_module.LANDING, f"release-{pr}", "release", target, None, 0, 0, stamp, status="completed", response={"at": stamp})
    runner.book.edit(runner_module.LANDING, lambda state: state.actions.update({action.action_id: action}))
    return action


@pytest.mark.parametrize("history", [32, 10_000])
@pytest.mark.parametrize("route", ["open", "parent"])
@pytest.mark.parametrize("source", ["empty", "existing-release"])
def test_unchanged_cut_poll_avoids_writer_transaction(shell, config, tmp_path, monkeypatch, history, route, source):
    initial = initialized()
    initial[runner_module.CUT_CLOCKS] = {f"history:{index}": {"ended": "done", "payload": "x" * 1024} for index in range(history)}
    if source == "existing-release":
        initial[runner_module.CUT_CLOCKS].update({f"release:{pr}": {"ended": "done", "payload": "owned"} for pr in (41, 42)})
    runner = clock_runner(shell, config, tmp_path, initial)
    if source == "existing-release":
        for pr in (41, 42):
            release_action(runner, shell, pr)
    store = runner.book.store
    connect, loads = store._connect, json.loads
    flush = runner_module.actions.Transaction.flush
    connections, changes = [], []
    counts = {"ro": 0, "rw": 0, "decoded_bytes": 0, "vm": 0}

    def decoded(value, *args, **kwargs):
        counts["decoded_bytes"] += len(value.encode())
        return loads(value, *args, **kwargs)

    def opened(path, writable=False):
        connection = connect(path, writable)
        connections.append(connection)
        counts["rw" if writable else "ro"] += 1
        def progress():
            counts["vm"] += 1
            return 0
        connection.set_progress_handler(progress, 1)
        return connection

    def written(transaction):
        flush(transaction)
        if transaction._connection in connections:
            changes.append(transaction._connection.total_changes)

    monkeypatch.setattr(store, "_connect", opened)
    monkeypatch.setattr(json, "loads", decoded)
    monkeypatch.setattr(runner_module.actions.Transaction, "flush", written)
    with store.reading():
        for _ in range(32):
            runner.cci_tail.forget()
            (runner.open_cut_clocks if route == "open" else runner.cut_clock)()
        print(json.dumps({"history": history, "route": route, "source": source, "counts": counts, "writer_changes": changes, "scope": "actual caller/private Store; no physical or SHM attribution"}))
        assert counts["rw"] == 0, f"unchanged cut polling opened {counts['rw']} writers for 32 calls"
        assert counts["ro"] == 2 and not changes
        assert counts["decoded_bytes"] < 32 * 4096
        assert counts["vm"] < 32 * 1500
        assert all(connection.total_changes == 0 and not connection.in_transaction for connection in connections)
        plan = connections[0].execute("EXPLAIN QUERY PLAN SELECT kind, record FROM fact WHERE path = ?", ("/cut-clocks",)).fetchall()
        assert any("INDEX" in row[3] and "path" in row[3] for row in plan)
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")


@pytest.mark.parametrize("cursor", [0, 10])
def test_missing_clock_parent_initializes_without_replacing_cursor(shell, config, tmp_path, cursor):
    runner = clock_runner(shell, config, tmp_path, {runner_module.CUT_CURSOR: cursor})
    runner.open_cut_clocks()
    assert runner.book.facts(runner_module.RUNNER, initialized()) == initialized(cursor)


@pytest.mark.parametrize("value", [None, False, 0, "", []])
@pytest.mark.parametrize("new_clock", [False, True])
def test_wrong_clock_parent_retains_original_empty_or_error_path(shell, config, tmp_path, value, new_clock):
    initial = initialized()
    initial[runner_module.CUT_CLOCKS] = value
    runner = clock_runner(shell, config, tmp_path, initial)
    if new_clock:
        release_action(runner, shell)
        with pytest.raises(AttributeError):
            runner.open_cut_clocks()
    else:
        runner.open_cut_clocks()
    assert runner.book.facts(runner_module.RUNNER, initial) == initial


@pytest.mark.parametrize("value", [None, False, 0, "", []])
def test_existing_falsey_clock_is_not_replaced_or_treated_missing(shell, config, tmp_path, monkeypatch, value):
    initial = initialized()
    initial[runner_module.CUT_CLOCKS] = {"release:41": value}
    runner = clock_runner(shell, config, tmp_path, initial)
    release_action(runner, shell)
    edit = runner.book.edit
    calls = []
    def edited(container, change):
        calls.append(container)
        return edit(container, change)
    monkeypatch.setattr(runner.book, "edit", edited)
    runner.open_cut_clocks()
    assert calls == []
    assert runner.book.facts(runner_module.RUNNER, initial) == initial


def test_first_cursor_then_clock_initialization_keeps_original_pass_order(shell, config, tmp_path):
    runner = clock_runner(shell, config, tmp_path)
    shell.records = [cut_record(42, kind="note", text="not an order", refs={})]
    runner.open_cut_clocks()
    assert runner.book.fact(runner_module.RUNNER, runner_module.CUT_CURSOR) == 42
    assert runner.book.fact(runner_module.RUNNER, runner_module.CUT_CLOCKS, None) is None
    runner.cci_tail.forget()
    runner.open_cut_clocks()
    assert runner.book.fact(runner_module.RUNNER, runner_module.CUT_CLOCKS) == {}


def test_new_orders_and_release_actions_use_unchanged_live_merge_order(shell, config, tmp_path, monkeypatch):
    initial = initialized()
    kept = {"ended": "kept", "since": "original", "payload": [False, None]}
    initial[runner_module.CUT_CLOCKS] = {"release:41": kept}
    runner = clock_runner(shell, config, tmp_path, initial)
    release_action(runner, shell, 41)
    release_action(runner, shell, 42)
    shell.records = [cut_record(11), cut_record(12, kind="decision", refs={}, text="not a cut")]
    edit = runner.book.edit
    writer = actions.Store(runner.book.store.root)
    def edited(container, change):
        if container == runner_module.RUNNER:
            with writer.transaction(container) as state:
                state.facts[runner_module.CUT_CLOCKS]["outside"] = {"ended": "outside"}
        return edit(container, change)
    monkeypatch.setattr(runner.book, "edit", edited)
    runner.open_cut_clocks()
    clocks = runner.book.fact(runner_module.RUNNER, runner_module.CUT_CLOCKS)
    assert list(clocks) == ["release:41", "outside", "go:11", "release:42"]
    assert clocks["release:41"] == kept and clocks["outside"] == {"ended": "outside"}
    assert clocks["go:11"]["components"] == ["sand"] and clocks["go:11"]["ended"] == ""
    assert clocks["release:42"]["pr"] == 42 and clocks["release:42"]["ended"] == ""
    assert runner.book.fact(runner_module.RUNNER, runner_module.CUT_CURSOR) == 12


def test_filtered_new_record_still_advances_cursor_through_original_writer(shell, config, tmp_path, monkeypatch):
    runner = clock_runner(shell, config, tmp_path, initialized())
    shell.records = [cut_record(11, refs={}, text="not a cut")]
    def unexpected(*args):
        raise AssertionError("advanced cursor must use original writer without guard snapshot")
    monkeypatch.setattr(runner.book, "read_facts", unexpected)
    runner.open_cut_clocks()
    assert runner.book.fact(runner_module.RUNNER, runner_module.CUT_CURSOR) == 11
    assert runner.book.fact(runner_module.RUNNER, runner_module.CUT_CLOCKS) == {}


def test_parent_clock_deadline_keeps_advancing_after_open_noop(shell, config, tmp_path):
    initial = initialized()
    initial[runner_module.CUT_CLOCKS] = {"go:11": {"lane": WALKER, "components": ["sand"], "since": actions.stamp(shell.now()), "trigger": "GO #11", "commit": "94d61b91951", "env": "<env>", "pr": "", "ended": ""}}
    runner = clock_runner(shell, config, tmp_path, initial)
    runner.cut_clock()
    assert runner.book.fact_path(runner_module.RUNNER, [runner_module.CUT_CLOCKS, "go:11", "ended"]) == ""
    shell.sleep((runner.config.cut_minutes + 1) * 60)
    runner.cci_tail.forget()
    runner.cut_clock()
    assert runner.book.fact_path(runner_module.RUNNER, [runner_module.CUT_CLOCKS, "go:11", "ended"]) == "silent"


def test_selected_existing_scalar_clock_decode_error_is_not_skipped(shell, config, tmp_path):
    runner = clock_runner(shell, config, tmp_path, initialized())
    release_action(runner, shell)
    with runner.book.store.transaction(runner_module.RUNNER) as state:
        state.facts[runner_module.CUT_CLOCKS]["release:41"] = "valid before corruption"
        state._connection.execute("UPDATE fact SET record = ? WHERE path = ?", ("{", "/cut-clocks/release:41"))
    with pytest.raises(json.JSONDecodeError):
        runner.open_cut_clocks()
    assert runner.book.fact(runner_module.RUNNER, runner_module.CUT_CURSOR) == 10


def test_guard_snapshot_and_original_writer_preserve_concurrent_clock_and_cursor(shell, config, tmp_path, monkeypatch):
    runner = clock_runner(shell, config, tmp_path, initialized())
    release_action(runner, shell)
    read = runner.book.read_facts
    writer = actions.Store(runner.book.store.root)
    kept = {"ended": "external", "since": "outside", "payload": 0}
    def guarded(container, choose):
        def snapshot(facts):
            assert facts[runner_module.CUT_CURSOR] == 10
            with writer.transaction(container) as state:
                state.facts[runner_module.CUT_CLOCKS]["release:41"] = kept
                state.facts[runner_module.CUT_CLOCKS]["outside"] = {"ended": "outside"}
                state.facts[runner_module.CUT_CURSOR] = 20
            assert facts[runner_module.CUT_CURSOR] == 10
            return choose(facts)
        return read(container, snapshot)
    monkeypatch.setattr(runner.book, "read_facts", guarded)
    runner.open_cut_clocks()
    assert runner.book.fact(runner_module.RUNNER, runner_module.CUT_CURSOR) == 10
    clocks = runner.book.fact(runner_module.RUNNER, runner_module.CUT_CLOCKS)
    assert clocks["release:41"] == kept and clocks["outside"] == {"ended": "outside"}


@pytest.mark.parametrize("removed", [False, True])
def test_clock_parent_change_before_guard_keeps_original_initialization_or_error(shell, config, tmp_path, monkeypatch, removed):
    runner = clock_runner(shell, config, tmp_path, initialized())
    release_action(runner, shell)
    read = runner.book.read_facts
    writer = actions.Store(runner.book.store.root)
    def guarded(container, choose):
        with writer.transaction(container) as state:
            if removed:
                state.facts.pop(runner_module.CUT_CLOCKS)
            else:
                state.facts[runner_module.CUT_CLOCKS] = None
        return read(container, choose)
    monkeypatch.setattr(runner.book, "read_facts", guarded)
    if removed:
        runner.open_cut_clocks()
        assert runner.book.fact_path(runner_module.RUNNER, [runner_module.CUT_CLOCKS, "release:41", "pr"]) == 41
    else:
        with pytest.raises(AttributeError):
            runner.open_cut_clocks()
        assert runner.book.fact(runner_module.RUNNER, runner_module.CUT_CLOCKS) is None
    assert runner.book.fact(runner_module.RUNNER, runner_module.CUT_CURSOR) == 10


@pytest.mark.parametrize(("target", "error"), [("{", json.JSONDecodeError), ("null", TypeError), ("[]", TypeError)])
def test_malformed_release_action_error_is_not_elided_by_existing_clock(shell, config, tmp_path, target, error):
    initial = initialized()
    initial[runner_module.CUT_CLOCKS] = {"release:41": {"ended": "done"}}
    runner = clock_runner(shell, config, tmp_path, initial)
    action = release_action(runner, shell)
    with runner.book.store.transaction(runner_module.LANDING) as state:
        state.actions[action.action_id].target = target
    with pytest.raises(error):
        runner.open_cut_clocks()
    assert runner.book.fact(runner_module.RUNNER, runner_module.CUT_CURSOR) == 10


def test_missing_release_timestamp_error_precedes_noop_guard(shell, config, tmp_path):
    initial = initialized()
    initial[runner_module.CUT_CLOCKS] = {"release:41": {"ended": "done"}}
    runner = clock_runner(shell, config, tmp_path, initial)
    action = release_action(runner, shell)
    with runner.book.store.transaction(runner_module.LANDING) as state:
        state.actions[action.action_id].response = None
    with pytest.raises(TypeError):
        runner.open_cut_clocks()


def test_source_error_preserves_identity_before_any_clock_edit(shell, config, tmp_path, monkeypatch):
    runner = clock_runner(shell, config, tmp_path, initialized())
    error = runner_module.CciTailFailed("private source failure")
    def failed(*args, **kwargs):
        raise error
    monkeypatch.setattr(runner_module.cci, "records", failed)
    with pytest.raises(runner_module.CciTailFailed) as raised:
        runner.open_cut_clocks()
    assert raised.value is error


def test_changed_schema_is_rejected_by_current_reader_before_noop_guard(shell, config, tmp_path):
    runner = clock_runner(shell, config, tmp_path, initialized())
    writer = actions.Store(runner.book.store.root)
    with writer.transaction(runner_module.RUNNER) as state:
        state._connection.execute("PRAGMA user_version = 3")
    with pytest.raises(ValueError, match="unsupported action store version"):
        runner.open_cut_clocks()


def test_no_landing_configuration_keeps_root_order_clock_behavior(shell, config, tmp_path):
    data = json.loads(config.read_text())
    data["landing"] = None
    config.write_text(json.dumps(data))
    runner = runner_module.Runner(shell, runner_module.Config.load(config), actions.Store(tmp_path / "store"))
    runner_module.seed_policy(runner)
    runner.book.ensure(runner_module.RUNNER, runner_module.RUNNER)
    runner.book.edit(runner_module.RUNNER, lambda state: state.facts.update(initialized()))
    shell.records = [cut_record(11)]
    runner.open_cut_clocks()
    assert runner.book.fact_path(runner_module.RUNNER, [runner_module.CUT_CLOCKS, "go:11", "components"]) == ["sand"]
    assert runner.book.fact(runner_module.RUNNER, runner_module.CUT_CURSOR) == 11
