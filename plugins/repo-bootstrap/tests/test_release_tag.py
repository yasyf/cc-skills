"""release_tag: the next version from commit subjects since the latest v* tag,
the skip verdicts, and the tag push plus release dispatch."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / ".github" / "scripts" / "release_tag.py"

_spec = importlib.util.spec_from_file_location("release_tag", SCRIPT)
release_tag = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = release_tag
_spec.loader.exec_module(release_tag)


def _run(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _out(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def _commit(repo: Path, msg: str) -> None:
    _run(repo, "commit", "--allow-empty", "-m", msg)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    work = tmp_path / "work"
    work.mkdir()
    _run(work, "init", "-b", "main")
    _run(work, "config", "user.name", "Test")
    _run(work, "config", "user.email", "test@example.com")
    _run(work, "config", "commit.gpgsign", "false")
    _run(work, "config", "tag.gpgsign", "false")
    _commit(work, "init")
    _run(work, "tag", "v1.4.2")
    return work


def test_patch_bump_for_a_plain_change(repo: Path) -> None:
    _commit(repo, "cli: 🐛 fix the lease")
    assert release_tag.plan(repo) == release_tag.Plan("v1.4.3", "1 change(s) since v1.4.2")


def test_sparkles_bump_minor(repo: Path) -> None:
    _commit(repo, "cli: 🐛 fix the lease")
    _commit(repo, "cli: ✨ add stack rebase")
    assert release_tag.plan(repo).tag == "v1.5.0"


def test_collision_bumps_major(repo: Path) -> None:
    _commit(repo, "cli: ✨ add stack rebase")
    _commit(repo, "cli: 💥 drop the old verb")
    assert release_tag.plan(repo).tag == "v2.0.0"


def test_latest_tag_is_the_highest_version_not_the_newest(repo: Path) -> None:
    _run(repo, "tag", "v1.10.0")
    _commit(repo, "cli: 🐛 fix")
    _run(repo, "tag", "v1.9.9")
    _commit(repo, "cli: 🐛 fix again")
    assert release_tag.plan(repo).tag == "v1.10.1"


def test_release_bookkeeping_alone_is_not_a_release(repo: Path) -> None:
    _commit(repo, "chore(release): sync ccx.binrun + manifest")
    _commit(repo, "chore(plugins): bump cc-context to 0.72.24")
    assert release_tag.plan(repo).tag is None


def test_already_tagged_head_is_skipped(repo: Path) -> None:
    assert release_tag.plan(repo) == release_tag.Plan(None, "HEAD is already released as v1.4.2")


def test_no_version_tag_is_skipped(repo: Path) -> None:
    _run(repo, "tag", "-d", "v1.4.2")
    _commit(repo, "cli: 🐛 fix")
    assert release_tag.plan(repo).tag is None


def test_ci_run_pushes_the_tag_and_dispatches_the_release(repo: Path, tmp_path: Path, monkeypatch) -> None:
    remote = tmp_path / "remote.git"
    _run(tmp_path, "init", "--bare", str(remote))
    _run(repo, "remote", "add", "origin", str(remote))
    _commit(repo, "cli: 🐛 fix the lease")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "gh-calls"
    gh = bin_dir / "gh"
    gh.write_text(f'#!/bin/sh\necho "$@" >> {calls}\n')
    gh.chmod(0o755)

    monkeypatch.chdir(repo)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("RELEASE_WORKFLOW", "release.yml")
    monkeypatch.setenv("RELEASE_DRY_RUN", "false")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    assert release_tag.main() == 0
    assert _out(remote, "rev-parse", "v1.4.3^{commit}") == _out(repo, "rev-parse", "HEAD")
    assert calls.read_text() == "workflow run release.yml --ref v1.4.3\n"


def test_dry_run_tags_nothing(repo: Path, monkeypatch, capsys) -> None:
    _commit(repo, "cli: 🐛 fix the lease")
    monkeypatch.chdir(repo)
    monkeypatch.setenv("RELEASE_WORKFLOW", "release.yml")
    monkeypatch.setenv("RELEASE_DRY_RUN", "true")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    assert release_tag.main() == 0
    assert "would tag v1.4.3" in capsys.readouterr().out
    assert _out(repo, "tag", "--points-at", "HEAD") == ""
