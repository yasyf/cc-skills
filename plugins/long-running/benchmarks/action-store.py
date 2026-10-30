from __future__ import annotations

import json
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from long_running import actions
from long_running.book import Book


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 10, 30, tzinfo=timezone.utc)


@contextmanager
def counted():
    costs = {"json_decodes": 0, "decoded_bytes": 0, "action_builds": 0, "writes": 0, "written_bytes": 0}
    decode, construct, write = json.loads, actions.Action.load.__func__, Path.write_text

    def loads(value, *args, **kwargs):
        costs["json_decodes"] += 1
        costs["decoded_bytes"] += len(value.encode() if isinstance(value, str) else value)
        return decode(value, *args, **kwargs)

    def build(cls, record):
        costs["action_builds"] += 1
        return construct(cls, record)

    def write_text(path, value, *args, **kwargs):
        costs["writes"] += 1
        costs["written_bytes"] += len(value.encode())
        return write(path, value, *args, **kwargs)

    with patch.object(json, "loads", loads), patch.object(actions.Action, "load", classmethod(build)), patch.object(Path, "write_text", write_text):
        yield costs


def measure(count: int) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        store = actions.Store(Path(directory))
        state = actions.Incident("runner", "root", 0, "2026-10-30T00:00:00Z", {})
        for index in range(count):
            action = state.accept(str(index), "send", "target", "owner", Clock().now())
            action.status = "verified" if index < count - 1 else "accepted"
        store.create(state)
        book = Book(store, Clock())
        with counted() as cold:
            book.actions("runner", kind="send", status="accepted")
        with counted() as warm:
            for _ in range(100):
                book.known("runner", str(count - 1))
                book.actions("runner", kind="send", status="accepted")
        with counted() as transition:
            book.edit("runner", lambda incident: incident.start(str(count - 1), Clock().now()))
        return {"history": count, "cold_selected": cold, "warm_selected_200_queries": warm, "one_transition": transition}


if __name__ == "__main__":
    print(json.dumps({"metrics": "Decoded JSON bytes and written JSON bytes, not physical disk I/O", "measurements": [measure(count) for count in (32, 10_000)]}, indent=2))
