from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

parser = argparse.ArgumentParser()
parser.add_argument("--plugin", type=Path, required=True)
parser.add_argument("--format", choices=("json", "sqlite"), required=True)
args = parser.parse_args()
sys.path[:0] = [str(args.plugin / "tests"), str(args.plugin / "skills/long-running/scripts")]
import test_desk_runner as desk
import test_incident as incidents
from long_running import actions
from long_running.book import Book

if args.format == "sqlite":
    from long_running import sqlite_store


@contextlib.contextmanager
def measured():
    costs = {"json_decodes": 0, "decoded_bytes": 0, "action_builds": 0, "json_payload_writes": 0, "json_payload_written_bytes": 0, "changed_sql_rows": 0}
    loads, construct, write = json.loads, actions.Action.load.__func__, Path.write_text

    def decoded(value, *positional, **keywords):
        costs["json_decodes"] += 1
        costs["decoded_bytes"] += len(value.encode() if isinstance(value, str) else value)
        return loads(value, *positional, **keywords)

    def built(cls, value):
        costs["action_builds"] += 1
        return construct(cls, value)

    def written(path, text, *positional, **keywords):
        if ".json." in path.name:
            costs["json_payload_writes"] += 1
            costs["json_payload_written_bytes"] += len(text.encode())
        return write(path, text, *positional, **keywords)

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(json, "loads", decoded))
        stack.enter_context(patch.object(actions.Action, "load", classmethod(built)))
        stack.enter_context(patch.object(Path, "write_text", written))
        if args.format == "sqlite":
            flush = sqlite_store.Transaction.flush

            def flushed(transaction):
                flush(transaction)
                costs["changed_sql_rows"] += transaction._connection.total_changes

            stack.enter_context(patch.object(sqlite_store.Transaction, "flush", flushed))
        yield costs


def measure(count):
    with tempfile.TemporaryDirectory() as directory, pytest.MonkeyPatch.context() as monkeypatch:
        root = Path(directory)
        shell = desk.shell.__wrapped__(root, monkeypatch)
        config = desk.config.__wrapped__(root, shell)
        store = actions.Store(root / "store")
        book = Book(store, shell)
        state = actions.Incident("selected", "owner", 0, actions.stamp(shell.now()), {"clocks": {str(index): {"at": [index]} for index in range(count)}})
        for index in range(count):
            key = str(index)
            state.accept(key, "send", "target", None, shell.now())
            if index < count - 1:
                state.verify(key, {"done": True})
        store.create(state)
        with measured() as cold:
            assert book.known("selected", str(count - 1)).status == "accepted"
            assert len(book.actions("selected", "send", "accepted")) == 1
        with measured() as transition:
            book.edit("selected", lambda live: live.start(str(count - 1), shell.now()))
        with measured() as fact:
            book.edit("selected", lambda live: live.facts["clocks"][str(count - 1)]["at"].append(-1))
        with measured() as owner:
            assert not book.as_owner("selected", "other", lambda live: live.facts.update({"bad": True}))
        runner = desk.runner_module.Runner(shell, desk.runner_module.Config.load(config), store)
        assert runner.check()

        def seed(live):
            for index in range(count):
                key = f"history:{index}"
                live.accept(key, "history", "old", None, shell.now())
                live.verify(key, {"done": True})

        book.edit(desk.runner_module.RUNNER, seed)
        for _ in range(32):
            shell.receive({"type": "heartbeat"})
        with measured() as heartbeat:
            assert runner.check()
        def seed_polls(live):
            live.facts[desk.runner_module.RELEASE_BUILDS] = {str(index): {"done": True} for index in range(count)}
            live.facts[desk.runner_module.RELEASE_BUILDS]["active"] = {"done": False, "lane": "release", "release": ""}
            def cut_clock(lane, ended):
                return {"lane": lane, "components": ["component"], "since": actions.stamp(shell.now()), "trigger": "fixture", "commit": "head", "env": "dev", "pr": "", "prs": [], "ended": ended}

            live.facts[desk.runner_module.CUT_CLOCKS] = {str(index): cut_clock(str(index), "closed") for index in range(count)}
            live.facts[desk.runner_module.CUT_CLOCKS]["active"] = cut_clock("active", "")
            live.facts[desk.runner_module.CUT_CLOCKS]["silent"] = cut_clock("silent", "silent")

        book.edit(desk.runner_module.RUNNER, seed_polls)
        runner.config.landing["applying"] = {"build": "build", "step": "step", "components": []}
        pressed, escalated = [], []
        monkeypatch.setattr(desk.runner_module, "release_build_argv", lambda *_: None)
        monkeypatch.setattr(runner, "escalate", lambda *parts: escalated.append(parts))
        monkeypatch.setattr(runner, "open_cut_clocks", lambda: None)
        monkeypatch.setattr(runner, "cut_closed", lambda *_: "")
        monkeypatch.setattr(runner, "cut_records", lambda clock, *_: pressed.append((clock["lane"], clock["ended"])) or [])
        with measured() as polls:
            runner.watch_applies()
            runner.cut_clock()
        assert len(escalated) == 1
        assert pressed == [("active", ""), ("silent", "silent")]
        overflow = desk.landing_of(shell, config, root)

        def seed_overflow(live):
            seed(live)
            live.facts[desk.runner_module.OVERFLOW_SEEN] = {"selected": ["head", actions.stamp(shell.now())]}

        overflow.book.edit(desk.runner_module.LANDING, seed_overflow)
        monkeypatch.setattr(overflow, "quota_low", lambda: False)
        monkeypatch.setattr(overflow, "rows", lambda: {"selected": {"head": "head"}})
        monkeypatch.setattr(overflow, "checks_paused", lambda: False)
        monkeypatch.setattr(overflow, "queue_states", lambda: {})
        monkeypatch.setattr(overflow, "held", lambda _: [])
        monkeypatch.setattr(overflow, "overflow_landable", lambda *_: {"selected"})
        with measured() as unchanged_overflow:
            if args.format == "sqlite":
                overflow.overflow(overflow)
            else:
                overflow.overflow()
        assert unchanged_overflow["json_payload_writes"] == unchanged_overflow["changed_sql_rows"] == 0
        clock = incidents.clock.__wrapped__()
        replay = incidents.fake.__wrapped__(clock)
        incident_store = incidents.store.__wrapped__(root)
        with contextlib.redirect_stdout(io.StringIO()):
            identity = incidents.open_incident(incident_store, clock)
        incident_book = Book(incident_store, clock)

        def seed_recovery(live):
            seed(live)
            live.facts["unrelated"] = {str(index): "historical" for index in range(count)}
            live.accept("active", "send", "new", None, clock.now())
            live.start("active", clock.now())

        incident_book.edit(identity, seed_recovery)
        recovering = incidents.Runner(incident_store, identity, incidents.world_of(replay), clock)
        with measured() as recovery:
            recovering.exhausted()
            recovering.mark_lost()
            recovering.deadlines()
        assert incident_store.load(identity).action("active").status == "unverifiable"
        return {"history": count, "cold_selected": cold, "one_transition": transition, "one_fact_leaf": fact, "wrong_owner": owner, "actual_32_heartbeat_page": heartbeat, "actual_recovery": recovery, "actual_release_cut_polls": polls, "actual_unchanged_overflow": unchanged_overflow}


print(json.dumps({"format": args.format, "metrics": "Logical decoded bytes, constructed Actions, JSON staged payload bytes, and changed SQL rows; physical IO not measured", "measurements": [measure(count) for count in (32, 10_000)]}, indent=2))
