from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest
from long_running import actions
from long_running.book import Book


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 10, 30, tzinfo=timezone.utc)


def history(tmp_path: Path, count: int) -> actions.Store:
    store = actions.Store(tmp_path)
    incident = actions.Incident("runner", "root", 0, "2026-10-30T00:00:00Z", {})
    for index in range(count):
        action = incident.accept(str(index), "send", "target", "owner", Clock().now())
        action.status = "verified" if index < count - 1 else "accepted"
        action.response = {"items": [index]}
    store.create(incident)
    return store


@pytest.mark.parametrize("count", [32, 10_000])
def test_targeted_book_queries_decode_unchanged_history_once(tmp_path, monkeypatch, count):
    store = history(tmp_path, count)
    book = Book(store, Clock())
    loads, builds = 0, 0
    load_json, load_action = json.loads, actions.Action.load.__func__

    def decode(*args, **kwargs):
        nonlocal loads
        loads += 1
        return load_json(*args, **kwargs)

    def construct(cls, record):
        nonlocal builds
        builds += 1
        return load_action(cls, record)

    monkeypatch.setattr(json, "loads", decode)
    monkeypatch.setattr(actions.Action, "load", classmethod(construct))
    for _ in range(50):
        selected = book.actions("runner", kind="send", status="accepted")
        assert [action.action_id for action in selected] == [str(count - 1)]
        assert book.known("runner", str(count - 1)) == selected[0]
    print(f"history={count} queries=100 json_decodes={loads} action_builds={builds}")
    assert loads == 1
    assert builds == count


def test_selected_actions_are_independent_and_keep_stored_order(tmp_path):
    store = history(tmp_path, 5)
    selected = store.actions("runner", kind="send")
    selected[0].response["items"].append("changed")
    selected[0].status = "accepted"
    assert [action.action_id for action in store.actions("runner", kind="send")] == ["0", "1", "2", "3", "4"]
    assert store.find_action("runner", "0").response == {"items": [0]}
    assert [action.action_id for action in store.actions("runner", status="accepted")] == ["4"]
    assert store.find_action("runner", "missing") is None
    assert store.actions("runner", kind="missing") == []


def test_query_refreshes_after_another_writer_replaces_the_container(tmp_path):
    store = history(tmp_path, 5)
    assert [action.action_id for action in store.actions("runner", status="accepted")] == ["4"]
    other = actions.Store(tmp_path)
    with other.edit("runner") as incident:
        incident.verify("4", {"done": True})
    assert store.actions("runner", status="accepted") == []
    assert store.find_action("runner", "4").verification_receipt == {"done": True}


def test_query_refreshes_after_in_place_change_with_restored_mtime(tmp_path):
    store = history(tmp_path, 5)
    assert store.ownership("runner") == ("root", 0)
    path = store.path("runner")
    before = path.stat()
    path.write_text(path.read_text().replace('"root"', '"peer"', 1))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_size == before.st_size
    assert store.ownership("runner") == ("peer", 0)


def test_in_place_change_during_decode_is_not_reused_as_current(tmp_path, monkeypatch):
    store = history(tmp_path, 5)
    load_json = json.load
    changed = False

    def decode(stream):
        nonlocal changed
        record = load_json(stream)
        if not changed:
            changed = True
            path = store.path("runner")
            path.write_text(path.read_text().replace('"root"', '"peer"', 1))
        return record

    monkeypatch.setattr(json, "load", decode)
    assert store.ownership("runner") == ("root", 0)
    assert store.ownership("runner") == ("peer", 0)


def test_concurrent_queries_share_one_decode(tmp_path, monkeypatch):
    store = history(tmp_path, 64)
    load_json = json.loads
    loads = 0

    def decode(*args, **kwargs):
        nonlocal loads
        loads += 1
        return load_json(*args, **kwargs)

    monkeypatch.setattr(json, "loads", decode)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: store.find_action("runner", "63"), range(64)))
    assert all(action.action_id == "63" for action in results)
    assert loads == 1


def test_owned_generation_is_checked_against_the_actual_writer_state(tmp_path):
    store = history(tmp_path, 5)
    book = Book(store, Clock())
    assert store.ownership("runner") == ("root", 0)
    other = actions.Store(tmp_path)
    other.transfer("runner", 0, "peer")
    other.ack("runner", "peer")
    assert not book.as_owner("runner", "root", lambda incident: incident.facts.update(wrong=True))
    assert book.as_owner("runner", "peer", lambda incident: incident.facts.update(right=True))
    assert store.load("runner").facts == {"right": True}


def test_legacy_whole_container_reads_do_not_share_mutable_state(tmp_path):
    store = history(tmp_path, 5)
    store.actions("runner")
    incident = store.load("runner")
    incident.actions["4"].status = "failed"
    assert store.find_action("runner", "4").status == "accepted"


def test_current_malformed_container_is_not_hidden_by_a_read_view(tmp_path):
    store = history(tmp_path, 5)
    store.actions("runner")
    store.path("runner").write_text("invalid")
    with pytest.raises(json.JSONDecodeError):
        store.actions("runner")
