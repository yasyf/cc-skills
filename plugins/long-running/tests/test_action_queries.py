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

