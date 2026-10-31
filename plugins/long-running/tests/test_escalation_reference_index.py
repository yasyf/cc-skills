import json
import re
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone

import pytest

from long_running import actions
from long_running.book import Book


NOW = datetime(2026, 10, 31, tzinfo=timezone.utc)


def populated(root, targets):
    store = actions.Store(root)
    incident = actions.Incident("runner", "owner", 0, actions.stamp(NOW), {})
    for index, target in enumerate(targets):
        incident.accept(str(index), "escalation", target, None, NOW)
    store.create(incident)
    return store


@pytest.mark.parametrize("target,expected", [
    ("GATE-FAILED #29 #029 #29 #30", {"29", "029", "30"}),
    ("GATE-FAILED #29a #29_ #29\u0301 #٢٩ #２９ #30", {"29", "30"}),
    ('GATE-FAILED \\"#29\\\\ #30\n#31\t#32', {"29", "30", "31", "32"}),
    ("GATE-FAILED #29\0suffix#30 #\0#31", {"29", "30", "31"}),
    ("GATE-FAILED #+29 #-29 # #٢9 #29２", {"29"}),
    ("GATE-FAILED " + "#29" * 4096, {"29"}),
])
def test_escalation_reference_tokens_preserve_ascii_text_and_deduplicate(tmp_path, target, expected):
    store = populated(tmp_path, [target])
    with sqlite3.connect(store.path("runner")) as connection:
        actual = {row[0] for row in connection.execute("SELECT pr FROM escalation_pr")}
    assert actual == expected
    for pr in expected:
        found = store.escalations_for("runner", pr)
        assert len(found) == 1 and found[0].target == target


def test_escalation_candidates_preserve_original_unicode_word_filter_and_order(tmp_path):
    targets = ["GATE-FAILED #29a wrong suffix", "GATE-FAILED #29\u0301 combining boundary", "GATE-FAILED #029 leading zero", "GATE-FAILED #29 selected last"]
    store = populated(tmp_path, targets)
    expected = [asdict(action) for action in store.actions("runner", kind="escalation") if re.search(r"#29\b", action.target)]
    candidates = store.escalations_for("runner", "29")
    assert [action.action_id for action in candidates] == ["0", "1", "3"]
    assert [asdict(action) for action in candidates if re.search(r"#29\b", action.target)] == expected
    assert [action.action_id for action in store.escalations_for("runner", "029")] == ["2"]
    assert store.escalations_for("runner", "29 OR 1=1") == []


def test_existing_action_updates_keep_membership_atomic_and_status_changes_do_not_rewrite_it(tmp_path):
    store = populated(tmp_path, ["GATE-FAILED #29 #30"])
    with sqlite3.connect(store.path("runner")) as connection:
        key, kind, status, encoded = connection.execute("SELECT action_key, kind, status, record FROM action").fetchone()
        record = json.loads(encoded)
        record["status"] = "started"
        before = connection.total_changes
        connection.execute("UPDATE action SET kind = ?, status = ?, record = ? WHERE action_key = ?", (kind, "started", json.dumps(record), key))
        assert connection.total_changes - before == 1
        assert connection.execute("SELECT pr FROM escalation_pr ORDER BY pr").fetchall() == [("29",), ("30",)]
        connection.commit()
        record["target"] = "GATE-FAILED #31"
        connection.execute("UPDATE action SET kind = ?, status = ?, record = ? WHERE action_key = ?", (kind, status, json.dumps(record), key))
        assert connection.execute("SELECT pr FROM escalation_pr").fetchall() == [("31",)]
        connection.rollback()
        assert connection.execute("SELECT pr FROM escalation_pr ORDER BY pr").fetchall() == [("29",), ("30",)]
    with store.transaction("runner") as state:
        state.actions["0"].target = "GATE-FAILED #31"
    assert store.escalations_for("runner", "29") == []
    assert store.escalations_for("runner", "31")[0].target == "GATE-FAILED #31"
    with store.transaction("runner") as state:
        state.actions["0"].kind = "note"
    assert store.escalations_for("runner", "31") == []
    with store.transaction("runner") as state:
        state.actions["0"].kind = "escalation"
        state.actions["0"].target = "GATE-FAILED #032"
    assert store.escalations_for("runner", "032")[0].action_id == "0"
    with store.transaction("runner") as state:
        del state.actions["0"]
    assert store.escalations_for("runner", "032") == []


@pytest.mark.parametrize("history", [32, 10_000])
def test_selected_escalation_lookup_is_history_bounded_and_read_only(tmp_path, monkeypatch, history):
    store = populated(tmp_path, [*(f"ARCHIVE #{90000 + index} unrelated" for index in range(history)), "GATE-FAILED #29 selected"])
    counts = {"vm_steps": 0, "decoded": 0, "changed_rows": 0}
    connect, load = sqlite3.connect, actions.Action.load

    class Observed(sqlite3.Connection):
        def close(self):
            counts["changed_rows"] += self.total_changes
            super().close()

    def progress():
        counts["vm_steps"] += 1
        return 0

    def connected(*args, **kwargs):
        connection = connect(*args, **kwargs, factory=Observed)
        connection.set_progress_handler(progress, 1)
        return connection

    def decoded(record):
        counts["decoded"] += 1
        return load(record)

    monkeypatch.setattr(sqlite3, "connect", connected)
    monkeypatch.setattr(actions.Action, "load", staticmethod(decoded))
    found = Book(store, None).escalations_for("runner", "29")
    print(json.dumps({"caller": "Book.escalations_for", "history": history, "costs": counts}))
    assert [action.target for action in found] == ["GATE-FAILED #29 selected"]
    assert counts["decoded"] == 1 and counts["changed_rows"] == 0
    assert 0 < counts["vm_steps"] < 256
