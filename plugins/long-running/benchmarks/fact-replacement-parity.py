from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

parser = argparse.ArgumentParser()
parser.add_argument("--plugin", type=Path, required=True)
parser.add_argument("--format", choices=("json", "sqlite"), required=True)
args = parser.parse_args()
sys.path.insert(0, str(args.plugin / "skills/long-running/scripts"))
from long_running import actions

if args.format == "sqlite":
    from long_running import sqlite_store
    from long_running.migration import initialize


def measure(root, nested):
    if args.format == "sqlite":
        initialize(root)
    store = actions.Store(root)
    initial = {"a": 1, "b": 2, "drop": 3}
    store.create(actions.Incident("ordered", "owner", 0, "2026-10-30T00:00:00Z", {"m": initial} if nested else initial))
    edit = store.transaction if args.format == "sqlite" else store.edit
    receipts = []

    def selected(live):
        return live.facts["m"] if nested else live.facts

    def observed(expected):
        live = store.load("ordered")
        assert list(selected(live)) == expected
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            assert actions.cmd_show(argparse.Namespace(incident="ordered", json=True), store) == 0
        facts = json.loads(output.getvalue())["facts"]
        assert list(facts["m"] if nested else facts) == expected
        receipts.append({"keys": list(selected(live)), "cli_facts": json.dumps(facts)})

    for replacement in ({"b": 2, "a": 1, "drop": 3}, {"new": 4, "a": 1, "b": 2}):
        with edit("ordered") as live:
            if nested:
                live.facts["m"] = replacement
            else:
                live.facts = replacement
        observed(list(replacement))
    with edit("ordered") as live:
        selected(live).update({"a": 5})
    observed(["new", "a", "b"])
    with edit("ordered") as live:
        del selected(live)["a"]
        selected(live)["a"] = 5
    observed(["new", "b", "a"])
    replacement = {"new": 4, "b": 2, "a": 5}
    if args.format == "sqlite":
        changes = []
        statements = []
        flush = sqlite_store.Transaction.flush

        def flushed(transaction):
            flush(transaction)
            changes.append(transaction._connection.total_changes)

        with patch.object(sqlite_store.Transaction, "flush", flushed):
            with edit("ordered") as live:
                live._connection.set_trace_callback(statements.append)
                if nested:
                    live.facts["m"] = replacement
                else:
                    live.facts = replacement
        assert changes == [0]
        assert not any(statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE")) for statement in statements)
    else:
        with edit("ordered") as live:
            if nested:
                live.facts["m"] = replacement
            else:
                live.facts = replacement
    observed(["new", "b", "a"])
    return receipts


with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    print(json.dumps({"root": measure(root / "root", False), "nested": measure(root / "nested", True)}, indent=2))
