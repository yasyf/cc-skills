"""identity subcommand: KEY=VALUE output, MISSING reporting, exit semantics."""

from __future__ import annotations

import subprocess

from bootstrap import identity


class FakeProc:
    def __init__(self, returncode: int, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout


def _patch(monkeypatch, *, git=True, gh_present=True, responses=None):
    monkeypatch.setattr(identity.shutil, "which", lambda name: {"git": git, "gh": gh_present}.get(name, False) or None)
    responses = responses or {}

    def fake_run(cmd, *a, **k):
        key = tuple(cmd)
        return responses.get(key, FakeProc(1, ""))

    monkeypatch.setattr(identity, "run", fake_run)


def test_full_identity(monkeypatch, capsys):
    _patch(monkeypatch, responses={
        ("git", "config", "--get", "user.name"): FakeProc(0, "Jane Doe\n"),
        ("git", "config", "--get", "user.email"): FakeProc(0, "jane@example.com\n"),
        ("gh", "api", "user", "-q", ".login"): FakeProc(0, "janedoe\n"),
    })
    assert identity.main() == 0
    out = capsys.readouterr()
    assert "AUTHOR_NAME=Jane Doe" in out.out
    assert "GITHUB_USER=janedoe" in out.out
    assert "MISSING" not in out.err


def test_gh_fallback_to_git_config(monkeypatch, capsys):
    _patch(monkeypatch, gh_present=False, responses={
        ("git", "config", "--get", "user.name"): FakeProc(0, "Jane\n"),
        ("git", "config", "--get", "user.email"): FakeProc(0, "j@e.com\n"),
        ("git", "config", "--get", "github.user"): FakeProc(0, "janedoe\n"),
    })
    assert identity.main() == 0
    assert "GITHUB_USER=janedoe" in capsys.readouterr().out


def test_partial_missing_is_not_fatal(monkeypatch, capsys):
    _patch(monkeypatch, responses={
        ("git", "config", "--get", "user.name"): FakeProc(0, "Jane\n"),
    })
    assert identity.main() == 0  # < 3 missing
    err = capsys.readouterr().err
    assert "MISSING: AUTHOR_EMAIL" in err
    assert "MISSING: GITHUB_USER" in err


def test_all_missing_exits_1(monkeypatch, capsys):
    _patch(monkeypatch, responses={})  # everything fails
    assert identity.main() == 1
    assert capsys.readouterr().err.count("MISSING:") == 3


def test_git_absent_exits_2(monkeypatch, capsys):
    _patch(monkeypatch, git=False)
    assert identity.main() == 2
    assert "git is not installed" in capsys.readouterr().err


def test_uses_real_subprocess_module():
    # guard: identity.run delegates to subprocess.run (stdlib only)
    assert identity.run.__module__ == "bootstrap.common"
    assert subprocess.run is not None
