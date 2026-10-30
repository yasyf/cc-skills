from __future__ import annotations

import json
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from long_running import actions, sqlite_store

NOW = datetime(2026, 10, 30, tzinfo=timezone.utc)


@contextmanager
def counted():
    costs = {"json_decodes": 0, "decoded_bytes": 0, "action_builds": 0, "encoded_action_rows": 0, "encoded_action_bytes": 0, "changed_rows": 0}
    decode, construct = json.loads, actions.Action.load.__func__
    encode, flush = sqlite_store._action_record, sqlite_store.Transaction.flush

    def loads(value, *args, **kwargs):
        costs["json_decodes"] += 1
        costs["decoded_bytes"] += len(value.encode() if isinstance(value, str) else value)
        return decode(value, *args, **kwargs)

    def build(cls, record):
        costs["action_builds"] += 1
        return construct(cls, record)

    def record(action):
        value = encode(action)
        costs["encoded_action_rows"] += 1
        costs["encoded_action_bytes"] += len(value.encode())
        return value

    def written(transaction):
        flush(transaction)
        costs["changed_rows"] += transaction._connection.total_changes

    with patch.object(json, "loads", loads), patch.object(actions.Action, "load", classmethod(build)), patch.object(sqlite_store, "_action_record", record), patch.object(sqlite_store.Transaction, "flush", written):
        yield costs


def measure(count: int) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        store = sqlite_store.Store(Path(directory))
        state = actions.Incident("runner", "root", 0, actions.stamp(NOW), {"history": {str(index): {"value": index} for index in range(count)}})
        for index in range(count):
            action = state.accept(str(index), "send", "target", "owner", NOW)
            action.status = "verified" if index < count - 1 else "accepted"
        store.create(state)
        with counted() as cold:
            sqlite_store.Store(Path(directory)).actions("runner", "send", "accepted")
        with counted() as repeated:
            for _ in range(100):
                sqlite_store.Store(Path(directory)).find_action("runner", str(count - 1))
                sqlite_store.Store(Path(directory)).actions("runner", "send", "accepted")
        with counted() as transition:
            with store.transaction("runner", generation=0) as transaction:
                transaction.start(str(count - 1), NOW)
        with counted() as metadata:
            with store.transaction("runner", generation=0) as transaction:
                transaction.facts["one"] = True
        return {"history": count, "cold_selected": cold, "cold_repeated_200_queries": repeated, "one_action_transition": transition, "one_fact_key_remaining_whole_field_cost": metadata}


if __name__ == "__main__":
    print(json.dumps({"metrics": "Logical JSON bytes and changed SQL rows; physical disk I/O is not measured", "measurements": [measure(count) for count in (32, 10_000)]}, indent=2))
