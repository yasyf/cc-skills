from __future__ import annotations

import json
import runpy
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest
import drive
from long_running import actions
from long_running.book import Book


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 10, 30, tzinfo=timezone.utc)


def history(tmp_path: Path, count: int) -> actions.Store:
    root = tmp_path / "store"
    store = actions.Store(root)
    incident = actions.Incident("runner", "root", 0, "2026-10-30T00:00:00Z", {})
    for index in range(count):
        action = incident.accept(str(index), "send", "target", "owner", Clock().now())
        action.status = "verified" if index < count - 1 else "accepted"
        action.response = {"items": [index]}
    store.create(incident)
    return store


@pytest.mark.parametrize("count", [32, 10_000])
def test_cold_book_queries_construct_only_selected_actions(tmp_path, monkeypatch, count):
    store = history(tmp_path, count)
    loads, builds = 0, 0
    load_json, load_action = json.loads, actions.Action.load.__func__

    def decode(*args, **kwargs):
        nonlocal loads
        record = load_json(*args, **kwargs)
        assert "action_id" in record
        loads += 1
        return record

    def construct(cls, record):
        nonlocal builds
        builds += 1
        return load_action(cls, record)

    monkeypatch.setattr(json, "loads", decode)
    monkeypatch.setattr(actions.Action, "load", classmethod(construct))
    for _ in range(50):
        book = Book(actions.Store(store.root), Clock())
        selected = book.actions("runner", kind="send", status="accepted")
        assert [action.action_id for action in selected] == [str(count - 1)]
        assert book.known("runner", str(count - 1)) == selected[0]
    assert loads == builds == 100


def test_selected_actions_are_detached_and_status_union_keeps_order(tmp_path):
    store = history(tmp_path, 5)
    selected = store.actions("runner", kind="send")
    selected[0].response["items"].append("changed")
    selected[0].status = "accepted"
    assert [action.action_id for action in store.actions("runner", statuses={"accepted", "verified"})] == ["0", "1", "2", "3", "4"]
    assert store.find_action("runner", "0").response == {"items": [0]}
    assert store.actions("runner", statuses=[]) == []
    assert store.find_action("runner", "missing") is None
    with pytest.raises(actions.UnknownAction):
        store.action("runner", "missing")


def test_readers_see_other_commits_without_retained_view(tmp_path):
    store = history(tmp_path, 5)
    other = actions.Store(store.root)
    with other.transaction("runner") as incident:
        incident.verify("4", {"done": True})
    assert store.actions("runner", status="accepted") == []
    assert store.find_action("runner", "4").verification_receipt == {"done": True}


def test_concurrent_cold_queries_return_detached_results(tmp_path):
    store = history(tmp_path, 64)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: store.find_action("runner", "63"), range(64)))
    assert all(action.action_id == "63" for action in results)
    results[0].response["items"].append(99)
    assert results[1].response == {"items": [63]}


def test_owned_generation_is_checked_against_actual_writer_state(tmp_path):
    store = history(tmp_path, 5)
    book = Book(store, Clock())
    other = actions.Store(store.root)
    other.transfer("runner", 0, "peer")
    other.ack("runner", "peer")
    assert not book.as_owner("runner", "root", lambda incident: incident.facts.update(wrong=True))
    assert book.as_owner("runner", "peer", lambda incident: incident.facts.update(right=True))
    assert store.facts("runner") == {"right": True}


def test_book_ensure_avoids_existing_snapshot_and_edit_returns_detached_values(tmp_path, monkeypatch):
    store = history(tmp_path, 5)
    book = Book(store, Clock())
    monkeypatch.setattr(store, "load", lambda _: pytest.fail("ensure loaded full snapshot"))
    assert book.ensure("runner", "ignored") is None
    selected = book.edit("runner", lambda incident: incident.facts.setdefault("cache", {"items": [1]}))
    assert isinstance(selected, dict)
    selected["items"].append(2)
    assert book.fact("runner", "cache") == {"items": [1]}
    action, created = book.accept("runner", "next", "send", "target", "owner", None)
    assert created and action.action_id == "next"
    action.target = "changed"
    assert book.action("runner", "next").target == "target"


def test_full_snapshot_is_detached_and_corruption_is_not_hidden(tmp_path):
    store = history(tmp_path, 5)
    incident = store.load("runner")
    incident.actions["4"].status = "failed"
    assert store.find_action("runner", "4").status == "accepted"
    connection = sqlite3.connect(store.path("runner"))
    try:
        connection.execute("UPDATE action SET record = 'invalid' WHERE action_key = '4'")
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(json.JSONDecodeError):
        store.find_action("runner", "4")


def test_original_actions_script_keeps_installed_version_routing(monkeypatch):
    script = Path(__file__).parents[1] / "skills/long-running/scripts/actions.py"
    routed = []
    monkeypatch.setattr(drive, "run_installed", routed.append)
    monkeypatch.setattr(sys, "argv", [str(script), "--help"])
    with pytest.raises(SystemExit) as exited:
        runpy.run_path(str(script), run_name="__main__")
    assert exited.value.code == 0
    assert routed == [str(script)]
