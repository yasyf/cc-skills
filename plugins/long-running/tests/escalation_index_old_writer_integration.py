import importlib
import importlib.util
import os
import sqlite3
import sys
from pathlib import Path

import pytest

from long_running import actions
from test_escalation_reference_index import NOW, populated


def test_immutable_old_store_writer_keeps_native_membership_and_fresh_reads(tmp_path):
    scripts = Path(os.environ["LR_ESCALATION_OLD_SCRIPTS"])
    source = scripts / "long_running"
    spec = importlib.util.spec_from_file_location("immutable_old_escalation", source / "__init__.py", submodule_search_locations=[str(source)])
    package = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = package
    spec.loader.exec_module(package)
    old = importlib.import_module(spec.name + ".actions")
    store = populated(tmp_path, ["GATE-FAILED #29 original"])
    writer = old.Store(tmp_path)
    with store.reading():
        assert store.escalations_for("runner", "29")[0].target.endswith("original")
        with writer.transaction("runner") as state:
            state.actions["0"].target = "GATE-FAILED #30 old-writer changed"
            state.accept("old-new", "escalation", "GATE-FAILED #030 #30 old-writer added", None, NOW)
        assert store.escalations_for("runner", "29") == []
        assert [action.action_id for action in store.escalations_for("runner", "30")] == ["0", "old-new"]
        assert store.escalations_for("runner", "030")[0].action_id == "old-new"
        with pytest.raises(ValueError, match="private rollback"):
            with writer.transaction("runner") as state:
                state.actions["0"].target = "GATE-FAILED #31 rollback"
                raise ValueError("private rollback")
        assert store.escalations_for("runner", "31") == []
        with writer.transaction("runner") as state:
            state.actions["0"].kind = "note"
        assert [action.action_id for action in store.escalations_for("runner", "30")] == ["old-new"]
        with writer.transaction("runner") as state:
            del state.actions["old-new"]
        assert store.escalations_for("runner", "30") == []
    with sqlite3.connect(store.path("runner")) as connection:
        assert connection.execute("SELECT COUNT(*) FROM escalation_pr").fetchone()[0] == 0
    assert isinstance(store.action("runner", "0"), actions.Action)
