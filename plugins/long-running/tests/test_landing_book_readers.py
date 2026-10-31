import json
import sqlite3
import threading

import pytest

from test_desk_runner import config, landing_of, runner_module, shell


@pytest.mark.parametrize("route", ["dispatch", "side"])
@pytest.mark.parametrize("reads", [32, 256])
@pytest.mark.parametrize("failure", [False, True], ids=["success", "error"])
def test_task_scoped_runner_book_readers(shell, config, tmp_path, monkeypatch, route, reads, failure):
    landing = landing_of(shell, config, tmp_path)
    store = landing.book.store
    assert store is not landing.build_cache.book.store
    landing.book.edit(runner_module.LANDING, lambda state: state.facts.update({"reader-probe": 0}))
    uri = store.path(runner_module.LANDING).absolute().as_uri() + "?mode=ro"
    connect = sqlite3.connect
    connections = []
    workers = []

    class Observed(sqlite3.Connection):
        def close(self):
            assert self.owner == threading.get_ident()
            self.closed = True
            self.changes = self.total_changes
            super().close()

    def connected(database, *args, **kwargs):
        if str(database) != uri:
            return connect(database, *args, **kwargs)
        connection = connect(database, *args, **kwargs, factory=Observed)
        connection.owner = threading.get_ident()
        connection.closed = False
        connections.append(connection)
        return connection

    def work():
        workers.append(threading.get_ident())
        for _ in range(reads):
            assert landing.book.fact(runner_module.LANDING, "reader-probe") == 0
        if failure:
            raise ValueError("task failure")
        landing.book.edit(runner_module.LANDING, lambda state: state.facts.update({"reader-probe": 1}))
        assert landing.book.fact(runner_module.LANDING, "reader-probe") == 1
        with pytest.raises(ValueError, match="inner lease"):
            with store.reading():
                assert landing.book.fact(runner_module.LANDING, "reader-probe") == 1
                raise ValueError("inner lease")
        assert landing.book.fact(runner_module.LANDING, "reader-probe") == 1

    monkeypatch.setattr(sqlite3, "connect", connected)
    main = threading.get_ident()
    try:
        with store.reading():
            assert landing.book.fact(runner_module.LANDING, "reader-probe") == 0
            if route == "dispatch":
                future = landing.dispatch("reader-probe", {"1"}, work)
            else:
                for name in ("refuse_ungone_parents", "refuse_out", "read_heads", "reconcile", "requeue", "announce_landings", "releases", "ungraded_after", "verify_restacks", "resolve_stale_blockers", "remove_landed_worktrees", "resubmit_graphite_base", "watch_graphite"):
                    monkeypatch.setattr(landing, name, lambda *args: None)
                monkeypatch.setattr(landing, "pull_ordered", lambda rows: [])
                monkeypatch.setattr(landing, "verify", lambda rows: work())
                landing.gate_rows({}, None, False)
                future = landing.side_work
            if failure:
                with pytest.raises(ValueError, match="task failure"):
                    future.result()
            else:
                future.result()
            selected = [connection for connection in connections if connection.owner != main]
            print(json.dumps({"route": route, "reads": reads, "failure": failure, "worker_read_connections": len(selected), "closed": sum(connection.closed for connection in selected), "scope": "actual task thread/Runner Book/SQLite; no physical IO attribution"}))
            assert len(selected) == 1, f"{route} reopened {len(selected)} Runner Book readers"
            assert selected[0].closed and selected[0].changes == 0
            assert workers == [selected[0].owner] and workers[0] != main
            own = [connection for connection in connections if connection.owner == main]
            assert len(own) == 1 and not own[0].closed and not own[0].in_transaction
            assert landing.book.fact(runner_module.LANDING, "reader-probe") == (0 if failure else 1)
        assert own[0].closed
    finally:
        landing.close()
