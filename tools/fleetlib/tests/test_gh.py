import json
from types import SimpleNamespace

import pytest

from fleetlib import gh


def completed(stdout: str = "", returncode: int = 0, stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(stdout=stdout, returncode=returncode, stderr=stderr)


def test_has_file_distinguishes_404_from_failure(monkeypatch):
    monkeypatch.setattr(gh, "try_run", lambda *a, stdin=None: completed(returncode=0))
    assert gh.has_file("r", "x.yml")

    monkeypatch.setattr(
        gh, "try_run", lambda *a, stdin=None: completed(returncode=1, stderr="gh: Not Found (HTTP 404)")
    )
    assert not gh.has_file("r", "x.yml")

    monkeypatch.setattr(
        gh, "try_run", lambda *a, stdin=None: completed(returncode=1, stderr="gh: API rate limit exceeded (HTTP 403)")
    )
    with pytest.raises(SystemExit, match="rate limit"):
        gh.has_file("r", "x.yml")


def test_repos_with_file_filters_and_sorts(monkeypatch):
    monkeypatch.setattr(gh, "active_repos", lambda owner: ["zeta", "alpha", "mid"])
    monkeypatch.setattr(gh, "has_file", lambda repo, path, owner: repo != "mid")
    assert gh.repos_with_file("x.yml") == ["alpha", "zeta"]


def test_repos_with_file_empty_is_fatal(monkeypatch):
    monkeypatch.setattr(gh, "active_repos", lambda owner: ["a", "b"])
    monkeypatch.setattr(gh, "has_file", lambda repo, path, owner: False)
    with pytest.raises(SystemExit, match="probe failure"):
        gh.repos_with_file("x.yml")


def test_repos_with_file_propagates_probe_exit(monkeypatch):
    monkeypatch.setattr(gh, "active_repos", lambda owner: ["a", "b"])
    monkeypatch.setattr(
        gh, "try_run", lambda *a, stdin=None: completed(returncode=1, stderr="gh: HTTP 500")
    )
    with pytest.raises(SystemExit, match="HTTP 500"):
        gh.repos_with_file("x.yml")


def test_deploy_key_ids_filters_by_title(monkeypatch):
    keys = [
        {"id": 11, "title": "my-title"},
        {"id": 22, "title": "other"},
        {"id": 33, "title": "my-title"},
    ]
    monkeypatch.setattr(gh, "run", lambda *a, stdin=None: completed(json.dumps(keys)))
    assert gh.deploy_key_ids("repo1", "my-title") == ["11", "33"]


def test_add_deploy_key_command(monkeypatch):
    calls = []
    monkeypatch.setattr(gh, "run", lambda *a, stdin=None: calls.append(a) or completed())
    gh.add_deploy_key("repo1", "my-title", "ssh-ed25519 AAAA")
    (add,) = calls
    assert add[:4] == ("gh", "repo", "deploy-key", "add")
    assert {"--allow-write", "--title", "my-title", "-R", "yasyf/repo1"} <= set(add)


def test_delete_deploy_keys_by_id(monkeypatch):
    calls = []
    monkeypatch.setattr(gh, "run", lambda *a, stdin=None: calls.append(a) or completed())
    gh.delete_deploy_keys("repo1", ["11", "22"])
    assert [c[-1] for c in calls] == ["repos/yasyf/repo1/keys/11", "repos/yasyf/repo1/keys/22"]
    assert all("DELETE" in c for c in calls)


def test_set_secret_streams_value(monkeypatch):
    calls = []
    monkeypatch.setattr(gh, "run", lambda *args, stdin=None: calls.append((args, stdin)) or completed())
    gh.set_secret("repo1", "MY_SECRET", "shh")
    ((args, stdin),) = calls
    assert args == ("gh", "secret", "set", "MY_SECRET", "-R", "yasyf/repo1")
    assert stdin == "shh"
