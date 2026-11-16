import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest
from drive import github, install, state

FLOCKED = state.flocked


def test_ci_before_first_webhook_is_shared_for_one_poll_interval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(state, "root", lambda: tmp_path)
    now = [1000.0]
    monkeypatch.setattr(install.time, "time", lambda: now[0])
    calls = []
    monkeypatch.setattr(install, "pushed_ci", lambda repo, sha: calls.append((repo, sha)) or [("running", False)])
    assert install.heard_ci("owner/repo", "sha", None) == [("running", False)]
    assert install.heard_ci("owner/repo", "sha", None) == [("running", False)]
    assert len(calls) == 1
    now[0] += install.READ_EVERY_SECONDS
    install.heard_ci("owner/repo", "sha", None)
    assert len(calls) == 2


def test_interleaved_merge_reads_keep_both_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(state, "root", lambda: tmp_path)
    calls = []

    def read(repo: str, sha: str) -> list[tuple[str, bool]]:
        calls.append(sha)
        if sha == "first":
            install.heard_ci(repo, "second", "event")
        return [(sha, False)]

    monkeypatch.setattr(install, "pushed_ci", read)
    assert install.heard_ci("owner/repo", "first", "event") == [("first", False)]
    assert install.heard_ci("owner/repo", "second", "event") == [("second", False)]
    assert calls == ["first", "second"]


def test_ci_event_and_expiry_refresh_the_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(state, "root", lambda: tmp_path)
    now = [1000.0]
    monkeypatch.setattr(install.time, "time", lambda: now[0])
    calls = []
    monkeypatch.setattr(install, "pushed_ci", lambda repo, sha: calls.append(sha) or [(str(len(calls)), False)])
    assert install.heard_ci("owner/repo", "sha", "event1") == [("1", False)]
    now[0] += install.READ_EVERY_SECONDS
    assert install.heard_ci("owner/repo", "sha", "event1") == [("1", False)]
    assert install.heard_ci("owner/repo", "sha", "event2") == [("2", False)]
    now[0] += install.CI_HEARD_SECONDS
    assert install.heard_ci("owner/repo", "sha", "event2") == [("3", False)]


def test_failed_ci_read_is_not_cached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(state, "root", lambda: tmp_path)
    calls = []

    def read(repo: str, sha: str) -> list[tuple[str, bool]]:
        calls.append(sha)
        if len(calls) == 1:
            raise github.GithubError("unavailable")
        return [("passed", True)]

    monkeypatch.setattr(install, "pushed_ci", read)
    with pytest.raises(github.GithubError, match="unavailable"):
        install.heard_ci("owner/repo", "sha", "event")
    assert install.heard_ci("owner/repo", "sha", "event") == [("passed", True)]
def test_concurrent_readers_share_one_inflight_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(state, "root", lambda: tmp_path)
    entered = threading.Event()
    count_lock = threading.Lock()
    arrivals = 0
    calls = []

    @contextmanager
    def locked(path: Path):
        nonlocal arrivals
        with count_lock:
            arrivals += 1
            if arrivals == 2:
                entered.set()
        with FLOCKED(path):
            yield

    def read(repo: str, sha: str) -> list[tuple[str, bool]]:
        calls.append(sha)
        assert entered.wait(5)
        return [("running", False)]

    monkeypatch.setattr(state, "flocked", locked)
    monkeypatch.setattr(install, "pushed_ci", read)
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(install.heard_ci, "owner/repo", "sha", "event") for _ in range(2)]
        assert [future.result(timeout=5) for future in futures] == [[("running", False)]] * 2
    assert calls == ["sha"]
