import importlib
import importlib.util
import os
import sys
from pathlib import Path

import pytest

from long_running import actions
from test_blocker_candidates import CONTAINER, NOW, populated


def test_actual_old_store_writer_keeps_blocker_candidates_current(tmp_path):
    source = Path(os.environ["LR_BLOCKER_OLD_SCRIPTS"]) / "long_running"
    spec = importlib.util.spec_from_file_location("immutable_old_blocker", source / "__init__.py", submodule_search_locations=[str(source)])
    package = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = package
    spec.loader.exec_module(package)
    old = importlib.import_module(spec.name + ".actions")
    store = populated(tmp_path, ['{"kind":"blocker","pr":"29"}'])
    writer = old.Store(tmp_path)
    with store.reading():
        assert [a.action_id for a in store.blocker_candidates(CONTAINER)] == ["0"]
        with writer.transaction(CONTAINER) as state:
            state.actions["0"].target = '{"kind":"note"}'
            state.accept("old-new", "bus", '{"kind":"blocker","pr":"30"}', None, NOW)
            state.start("old-new", NOW)
            state.complete("old-new", {"posted": "#52"})
        assert [a.action_id for a in store.blocker_candidates(CONTAINER)] == ["old-new"]
        with pytest.raises(ValueError, match="private rollback"):
            with writer.transaction(CONTAINER) as state:
                state.actions["old-new"].target = '{"kind":"note"}'
                raise ValueError("private rollback")
        assert [a.action_id for a in store.blocker_candidates(CONTAINER)] == ["old-new"]
        with writer.transaction(CONTAINER) as state:
            state.verify("old-new", {"resolved": "#90"})
        assert store.blocker_candidates(CONTAINER) == []
        with writer.transaction(CONTAINER) as state:
            del state.actions["old-new"]
        assert store.blocker_candidates(CONTAINER) == []
    assert isinstance(store.action(CONTAINER, "0"), actions.Action)
