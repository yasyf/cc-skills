from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from long_running import actions
from long_running.book import Book
from long_running.build_cache import BuildCache
from test_desk_runner import build_reads, config, landing_of, shell, with_ci_build


NOW = datetime(2026, 10, 30, 20, tzinfo=timezone.utc)


@pytest.fixture
def cache(tmp_path):
    book = Book(actions.Store(tmp_path / "store"), SimpleNamespace(now=lambda: NOW))
    return BuildCache(book, tmp_path / "runner.json", "landing")


def passed(finished=NOW):
    return {"state": "passed", "commit": "a" * 40, "finished_at": actions.stamp(finished), "jobs": [{"type": "script", "name": "test", "step_key": "test", "state": "passed", "soft_failed": False, "id": "discarded"}], "raw": "discarded"}


def lean(build):
    return {key: build[key] for key in ("state", "commit", "finished_at")} | {"jobs": [{key: value for key, value in job.items() if key != "id"} for job in build["jobs"]]}


def test_exact_url_and_configuration_survive_new_reader(cache, tmp_path):
    url = "https://buildkite.com/forge/test/builds/1"
    build = passed()
    cache.put({url: build}, NOW - timedelta(days=3))
    resumed = BuildCache(cache.book, tmp_path / "runner.json", "landing")
    assert resumed.get(url) == lean(build)
    assert resumed.get(url.upper()) is None
    assert BuildCache(cache.book, tmp_path / "other.json", "landing").get(url) is None


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
    sibling = BuildCache(cache.book, tmp_path / "runner.json", "landing")
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda pair: pair[0].put({pair[1]: passed()}, NOW - timedelta(days=3)), [(cache, "one"), (sibling, "two")]))
    assert cache.get("one") == cache.get("two") == lean(passed())


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
