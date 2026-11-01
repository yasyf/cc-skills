import sqlite3

import pytest

from blocker_index_old_writer_integration import old_actions
from long_running import actions
from test_desk_runner import config, landing_of, runner_module, shell
from test_admission_direct_merge_cost import admission_rows, prepare_admission


@pytest.mark.parametrize("indexed", [True, False], ids=["indexed", "indexless"])
def test_actual_old_writer_updates_direct_merge_candidates(shell, config, tmp_path, monkeypatch, indexed):
    old = old_actions()
    landing = landing_of(shell, config, tmp_path)
    prepare_admission(landing, monkeypatch)
    store = landing.book.store
    if not indexed:
        with sqlite3.connect(store.path(runner_module.LANDING)) as connection:
            connection.execute("DROP INDEX action_direct_merge_candidates")
    writer = old.Store(store.root)
    with store.reading():
        assert set(landing.admission(admission_rows(), [], None).heads) == set(admission_rows())
        with writer.transaction(runner_module.LANDING) as state:
            state.accept("old-key", "bus", "opaque", None, shell.now()).action_id = "merged-direct:1@abcdef1234"
        assert set(landing.admission(admission_rows(), [], None).heads) == set(admission_rows()) - {"1"}
        with writer.transaction(runner_module.LANDING) as state:
            state.actions["old-key"].action_id = "merged-direct:2@abcdef1234"
            state.actions["old-key"].status = "verified"
        assert set(landing.admission(admission_rows(), [], None).heads) == set(admission_rows()) - {"2"}
        with pytest.raises(ValueError, match="private rollback"):
            with writer.transaction(runner_module.LANDING) as state:
                state.actions["old-key"].action_id = "merged-direct:3@abcdef1234"
                raise ValueError("private rollback")
        assert set(landing.admission(admission_rows(), [], None).heads) == set(admission_rows()) - {"2"}
        with writer.transaction(runner_module.LANDING) as state:
            del state.actions["old-key"]
        assert set(landing.admission(admission_rows(), [], None).heads) == set(admission_rows())


def test_actual_old_creator_stays_indexless_and_readable(tmp_path, shell):
    old = old_actions()
    writer = old.Store(tmp_path)
    incident = old.Incident("old-direct", "old-owner", 0, old.stamp(shell.now()), {})
    incident.accept("merged-direct:1@abcdef1234", "bus", "opaque", None, shell.now())
    writer.create(incident)
    store = actions.Store(tmp_path)
    with sqlite3.connect(store.path("old-direct")) as connection:
        before = connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name").fetchall()
        assert not connection.execute("SELECT 1 FROM sqlite_master WHERE name='action_direct_merge_candidates'").fetchall()
    assert [a.action_id for a in store.direct_merge_candidates("old-direct")] == ["merged-direct:1@abcdef1234"]
    with sqlite3.connect(store.path("old-direct")) as connection:
        assert connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY name").fetchall() == before
