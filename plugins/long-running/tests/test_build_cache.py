from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from long_running import actions
from long_running.build_cache import BuildCache
from test_desk_runner import build_reads, config, landing_of, runner_module, shell, with_ci_build


NOW = datetime(2026, 10, 30, 20, tzinfo=timezone.utc)


@pytest.fixture
def cache(tmp_path):
    return BuildCache(tmp_path / "runner.json", SimpleNamespace(now=lambda: NOW), "landing")


def passed(finished=NOW):
    return {"state": "passed", "commit": "a" * 40, "finished_at": actions.stamp(finished), "jobs": [{"type": "script", "name": "test", "step_key": "test", "state": "passed", "soft_failed": False, "id": "discarded"}], "raw": "discarded"}


def lean(build):
    return {key: build[key] for key in ("state", "commit", "finished_at")} | {"jobs": [{key: value for key, value in job.items() if key != "id"} for job in build["jobs"]]}


@pytest.fixture
def readonly_connections(monkeypatch):
    connections = []
    connect = sqlite3.connect

    class ObservedConnection(sqlite3.Connection):
        def close(self):
            assert self.owner == threading.get_ident()
            self.closed = True
            self.closed_at = time.monotonic()
            super().close()

    def observed(database, *args, **kwargs):
        if not str(database).endswith("?mode=ro"):
            return connect(database, *args, **kwargs)
        connection = connect(database, *args, **kwargs, factory=ObservedConnection)
        connection.owner = threading.get_ident()
        connection.thread_name = threading.current_thread().name
        connection.opened_at = time.monotonic()
        connection.closed_at = None
        frames = []
        frame = sys._getframe(1)
        while frame is not None and len(frames) < 8:
            frames.append((frame.f_code.co_filename.rsplit("/", 1)[-1], frame.f_code.co_name, frame.f_lineno))
            frame = frame.f_back
        del frame
        connection.origin = tuple(frames)
        connection.closed = False
        connection.database = str(database)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", observed)
    return connections


def test_nested_reader_lease_survives_inner_error_and_observes_writes(cache, readonly_connections):
    cache.put({"one": passed()}, NOW - timedelta(days=3))
    with cache.book.store.reading():
        assert cache.get("one") == lean(passed())
        connection = readonly_connections[0]
        with pytest.raises(ValueError, match="inner"):
            with cache.book.store.reading():
                assert cache.get("one") == lean(passed())
                raise ValueError("inner")
        assert len(readonly_connections) == 1 and not connection.closed and not connection.in_transaction
        updated = passed() | {"commit": "b" * 40}
        cache.put({"one": updated}, NOW - timedelta(days=3))
        writer = sqlite3.connect(cache.book.store.path(cache.container))
        try:
            assert writer.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0] == 0
        finally:
            writer.close()
        assert cache.get("one") == lean(updated)
        assert len(readonly_connections) == 1 and not connection.in_transaction
    assert connection.closed


def test_outer_reader_error_closes_only_its_owned_connection(cache, readonly_connections):
    cache.put({"one": passed()}, NOW - timedelta(days=3))
    with pytest.raises(ValueError, match="outer"):
        with cache.book.store.reading():
            with cache.book.store.reading():
                assert cache.get("one") == lean(passed())
            raise ValueError("outer")
    assert len(readonly_connections) == 1 and readonly_connections[0].closed
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        readonly_connections[0].execute("SELECT 1")


def test_reader_leases_are_isolated_by_thread_and_store(cache, tmp_path, readonly_connections):
    cache.put({"one": passed()}, NOW - timedelta(days=3))
    other = BuildCache(tmp_path / "other.json", cache.book.shell, "landing")
    updated = passed() | {"commit": "c" * 40}
    other.put({"one": updated}, NOW - timedelta(days=3))
    together = threading.Barrier(2, timeout=10)

    def read(index):
        selected = cache if index == 0 else other
        with selected.book.store.reading():
            with selected.book.store.reading():
                assert selected.get("one") == lean(passed() if index == 0 else updated)
                together.wait()
            assert selected.get("one") == lean(passed() if index == 0 else updated)
        return threading.get_ident()

    with cache.book.store.reading():
        assert cache.get("one") == lean(passed())
        main = readonly_connections[0]
        with ThreadPoolExecutor(2) as pool:
            owners = list(pool.map(read, range(2)))
        assert len(set(owners)) == 2 and threading.get_ident() not in owners
        assert not main.closed and not main.in_transaction
        assert len(readonly_connections) == 3
        assert all(connection.closed for connection in readonly_connections[1:])
        assert {connection.database for connection in readonly_connections} == {cache.book.store.path(cache.container).absolute().as_uri() + "?mode=ro", other.book.store.path(other.container).absolute().as_uri() + "?mode=ro"}
    assert all(connection.closed for connection in readonly_connections)


@pytest.mark.parametrize("failed", [False, True])
def test_actual_stack_task_owns_and_closes_its_cache_reader(shell, config, tmp_path, readonly_connections, failed):
    landing = landing_of(shell, config, tmp_path)
    cache = landing.build_cache
    cache.put({"one": passed(shell.now())}, shell.now() - timedelta(days=3))
    database = cache.book.store.path(cache.container).absolute().as_uri() + "?mode=ro"

    def work():
        assert cache.get("one") == lean(passed(shell.now()))
        assert cache.get("one") == lean(passed(shell.now()))
        if failed:
            raise ValueError("stack task")

    try:
        future = landing.dispatch("stack", {"1"}, work)
        assert future is not None
        if failed:
            with pytest.raises(ValueError, match="stack task"):
                future.result(timeout=10)
        else:
            assert future.result(timeout=10) is None
        readers = [connection for connection in readonly_connections if connection.database == database]
        assert len(readers) == 1 and readers[0].closed
        assert readers[0].owner != threading.get_ident()
    finally:
        landing.close()


def test_actual_landing_pass_closes_cache_reader_after_failure(shell, config, tmp_path, readonly_connections, monkeypatch):
    landing = landing_of(shell, config, tmp_path)
    cache = landing.build_cache
    cache.put({"one": passed(shell.now())}, shell.now() - timedelta(days=3))
    database = cache.book.store.path(cache.container).absolute().as_uri() + "?mode=ro"

    def gate():
        assert cache.get("one") == lean(passed(shell.now()))
        with cache.book.store.reading():
            assert cache.get("one") == lean(passed(shell.now()))
        raise ValueError("gate pass")

    monkeypatch.setattr(landing, "gate_pass", gate)
    try:
        with pytest.raises(ValueError, match="gate pass"):
            landing.run()
        readers = [connection for connection in readonly_connections if connection.database == database]
        assert len(readers) == 1 and readers[0].closed
        assert readers[0].owner == threading.get_ident()
    finally:
        landing.close()


def test_exact_url_and_configuration_survive_new_reader(cache, tmp_path):
    url = "https://buildkite.com/forge/test/builds/1"
    build = passed()
    cache.put({url: build}, NOW - timedelta(days=3))
    resumed = BuildCache(tmp_path / "runner.json", cache.book.shell, "landing")
    assert resumed.get(url) == lean(build)
    assert resumed.get(url.upper()) is None
    assert BuildCache(tmp_path / "other.json", cache.book.shell, "landing").get(url) is None


def test_only_finished_passed_builds_persist(cache):
    cache.put({"failed": passed() | {"state": "failed"}, "canceled": passed() | {"state": "canceled"}, "running": passed() | {"finished_at": None}}, NOW - timedelta(days=3))
    assert cache.book.fact(cache.container, "builds", {}) == {}


def test_expiry_keeps_exact_boundary_and_empty_pass_does_not_prune(cache):
    floor = NOW - timedelta(days=3)
    boundary = passed(floor)
    boundary["finished_at"] = floor.astimezone(timezone(timedelta(hours=-7))).isoformat()
    cache.put({"old": passed(floor - timedelta(seconds=1)), "boundary": boundary}, floor - timedelta(days=1))
    cache.put({}, floor)
    assert cache.get("old") is not None
    cache.put({"new": passed()}, floor)
    assert cache.get("old") is None
    assert cache.get("boundary") == lean(boundary)
    assert cache.get("new") == lean(passed())


def test_sibling_writers_preserve_distinct_builds(cache, tmp_path):
    sibling = BuildCache(tmp_path / "runner.json", cache.book.shell, "landing")
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda pair: pair[0].put({pair[1]: passed()}, NOW - timedelta(days=3)), [(cache, "one"), (sibling, "two")]))
    assert cache.get("one") == cache.get("two") == lean(passed())


def test_config_suffix_aliases_share_the_exact_legacy_cache_owner(cache, tmp_path):
    alias = BuildCache(tmp_path / "runner.conf", cache.book.shell, "landing")
    cache.put({"same-url": passed()}, NOW - timedelta(days=3))
    assert alias.get("same-url") == lean(passed())
    assert alias.book.store.path(alias.container) == cache.book.store.path(cache.container)
    assert alias.book.store.root == tmp_path / "runner.landing-builds.store"


def test_preimported_cache_is_shared_across_config_suffixes_and_runner_stores(shell, config, tmp_path):
    with_ci_build(config)
    alias = config.with_suffix(".conf")
    alias.write_text(config.read_text())
    number = "99999999"
    url = f"https://buildkite.com/forge/test/builds/{number}"
    stored = passed(shell.now())
    imported = BuildCache(config, shell, runner_module.LANDING)
    imported.put({url: stored}, shell.now() - timedelta(days=3))
    landings = []
    try:
        for source, root in [(config, tmp_path / "first-store"), (alias, tmp_path / "second-store")]:
            runner = runner_module.Runner(shell, runner_module.Config.load(source), actions.Store(root))
            runner_module.seed_policy(runner)
            landing = runner_module.Landing(runner, runner.config.landing)
            landings.append(landing)
            assert landing.build(url) == lean(stored)
            assert landing.build_cache.book.store.path(imported.container) == imported.book.store.path(imported.container)
        assert landings[0].book.store.root != landings[1].book.store.root
        assert build_reads(shell, number) == []
    finally:
        for landing in landings:
            landing.close()


def test_indexed_expiry_does_not_select_another_subtree(cache):
    def seed(incident):
        incident.facts["builds"] = {"old": {"finished": "2026-01-01"}, "new": {"finished": "2027-01-01"}}
        incident.facts["other"] = {"wrong": {"finished": "2026-01-01"}}
        return incident.fact_keys_before("builds", "finished", "2026-10-30")

    assert cache.book.edit(cache.container, seed) == ["old"]


@pytest.mark.parametrize("population", [32, 10_000])
def test_actual_landing_changed_and_unchanged_persistence_is_bounded(shell, config, tmp_path, monkeypatch, population):
    with_ci_build(config)
    landing = landing_of(shell, config, tmp_path)
    finished = shell.now() - timedelta(hours=1)
    stored = passed(finished)
    landing.build_cache.put({f"https://buildkite.com/forge/test/builds/{number}": stored for number in range(population)}, shell.now() - timedelta(days=3))
    costs = {"vm_steps": 0, "changed_rows": 0, "connections": 0}
    connect = sqlite3.connect
    owner_thread = threading.get_ident()

    def progress():
        costs["vm_steps"] += 1
        return 0

    class ObservedConnection(sqlite3.Connection):
        def close(self):
            costs["changed_rows"] += self.total_changes
            super().close()

    def observed(*args, **kwargs):
        if threading.get_ident() != owner_thread:
            return connect(*args, **kwargs)
        costs["connections"] += 1
        connection = connect(*args, **kwargs, factory=ObservedConnection)
        connection.set_progress_handler(progress, 1)
        return connection

    monkeypatch.setattr(sqlite3, "connect", observed)
    number = "99999999"
    url = f"https://buildkite.com/forge/test/builds/{number}"
    newest = passed(finished) | {"commit": "b" * 40}
    shell.builds[number] = newest
    resumed = None
    try:
        assert landing.build(url) == newest
        landing.keep_builds()
        first = dict(costs)
        landing.keep_builds()
        assert costs == first
        resumed = landing_of(shell, config, tmp_path)
        assert resumed.build(url) == lean(newest)
        assert len(build_reads(shell, number)) == 1
        print(json.dumps({"population": population, "first_changed": first, "after_resumed": costs, "remote_build_reads": 1, "unchanged_connections_and_steps": 0, "scope": "actual Landing.build/keep_builds with real Book/Store and SQLite VM operations; no physical IO claim"}))
        assert first["vm_steps"] < 4000
        assert first["changed_rows"] < 64
    finally:
        landing.close()
        if resumed is not None:
            resumed.close()


def test_actual_refresh_bypasses_persistent_passed_summary(shell, config, tmp_path):
    with_ci_build(config)
    landing = landing_of(shell, config, tmp_path)
    number = "99999999"
    url = f"https://buildkite.com/forge/test/builds/{number}"
    kept = passed(shell.now())
    landing.build_cache.put({url: kept}, shell.now() - timedelta(days=3))
    shell.builds[number] = kept | {"jobs": [{"id": "retried", "retry_source": {"job_id": "original"}}]}
    try:
        assert landing.build(url) == lean(kept)
        assert build_reads(shell, number) == []
        assert landing.build(url, refresh=True) == shell.builds[number]
        assert len(build_reads(shell, number)) == 1
    finally:
        landing.close()


@pytest.mark.parametrize("population", [32, 10_000])
def test_actual_landing_selected_cached_build_read_connections(shell, config, tmp_path, monkeypatch, population):
    with_ci_build(config)
    landing = landing_of(shell, config, tmp_path)
    stored = passed(shell.now() - timedelta(hours=1))
    urls = [f"https://buildkite.com/forge/test/builds/{number}" for number in range(population)]
    landing.build_cache.put(dict.fromkeys(urls, stored), shell.now() - timedelta(days=3))
    selected = urls[:32]
    monkeypatch.setattr(landing, "ci_builds", lambda head: [(url, "success", False) for url in selected])
    costs = {"connections": 0, "closes": 0, "vm_steps": 0}
    threads = set()
    measuring = threading.Lock()
    connect = sqlite3.connect
    database = landing.build_cache.book.store.path("passed-builds").absolute().as_uri()

    def progress():
        with measuring:
            costs["vm_steps"] += 1
        return 0

    class ObservedConnection(sqlite3.Connection):
        def close(self):
            with measuring:
                costs["closes"] += 1
            super().close()

    def observed(path, *args, **kwargs):
        if str(path).partition("?")[0] != database:
            return connect(path, *args, **kwargs)
        with measuring:
            costs["connections"] += 1
            threads.add(threading.get_ident())
        connection = connect(path, *args, **kwargs, factory=ObservedConnection)
        connection.set_progress_handler(progress, 1)
        return connection

    monkeypatch.setattr(sqlite3, "connect", observed)
    rows = {"1": {"state": "open", "head": "a" * 40}}
    try:
        assert landing.landing_steps("test", rows) == {"test"}
        first = dict(costs)
        assert first["connections"] == first["closes"] == 1, f"selected cached builds reopened {first['connections']} readers"
        assert first["vm_steps"] < 40_000
        assert landing.landing_steps("test", rows) == {"test"}
        assert costs == first
        assert all(build_reads(shell, str(number)) == [] for number in range(32))
        print(json.dumps({"population": population, "selected": 32, "first": first, "reading_threads": len(threads), "warm_additional_operations": 0, "remote_build_reads": 0, "scope": "actual Landing.landing_steps/build with real BuildCache/Book/Store; SQLite connection and VM counts, not physical IO"}))
    finally:
        landing.close()


def test_failed_persistence_keeps_pending_build_for_retry(shell, config, tmp_path, monkeypatch):
    with_ci_build(config)
    landing = landing_of(shell, config, tmp_path)
    number = "99999999"
    url = f"https://buildkite.com/forge/test/builds/{number}"
    shell.builds[number] = passed(shell.now())
    landing.build(url)
    original = landing.build_cache.put

    def fail(*args):
        raise OSError("write failed")

    try:
        monkeypatch.setattr(landing.build_cache, "put", fail)
        with pytest.raises(OSError, match="write failed"):
            landing.keep_builds()
        assert landing.builds_pending == {url}
        monkeypatch.setattr(landing.build_cache, "put", original)
        landing.keep_builds()
        assert landing.builds_pending == set()
        assert landing.build_cache.get(url) == lean(shell.builds[number])
    finally:
        landing.close()
