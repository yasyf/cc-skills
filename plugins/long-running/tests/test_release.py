from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from long_running import release
from long_running.release import promote


@pytest.fixture
def cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    versions = tmp_path / "cache"
    for version in ("0.7.9", "0.7.44", "0.7.57"):
        (versions / version).mkdir(parents=True)
    return versions


def named() -> str:
    return os.readlink(release.link())


def test_the_first_process_of_a_version_names_it(cache: Path):
    promote(cache / "0.7.44")
    assert named() == str(cache / "0.7.44")


def test_a_session_whose_path_predates_lr_reaches_the_installed_lr_through_local_bin(cache: Path, tmp_path: Path):
    tool = cache / "0.7.57" / "bin" / "lr"
    tool.parent.mkdir()
    tool.write_text('#!/bin/sh\necho "installed $*"\n')
    tool.chmod(0o755)
    promote(cache / "0.7.57")

    ran = subprocess.run([tmp_path / "home" / ".local" / "bin" / "lr", "wait", "2"], capture_output=True, text=True, check=True)

    assert ran.stdout == "installed wait 2\n"


def test_a_published_lr_is_left_alone(cache: Path, tmp_path: Path):
    other = tmp_path / "home" / ".local" / "bin" / "lr"
    other.parent.mkdir(parents=True)
    other.write_text("someone else's lr")
    promote(cache / "0.7.44")
    assert other.read_text() == "someone else's lr"


def test_a_higher_version_replaces_the_link_and_a_lower_one_does_not(cache: Path):
    promote(cache / "0.7.44")
    promote(cache / "0.7.57")
    promote(cache / "0.7.9")
    assert named() == str(cache / "0.7.57")


def test_a_machine_that_installs_through_lr_only_never_moves_the_link_from_a_process(cache: Path):
    promote(cache / "0.7.44")
    (release.link().parent / "lr-install-only").touch()
    promote(cache / "0.7.57")
    assert named() == str(cache / "0.7.44")


def test_lr_install_only_still_lets_the_first_process_create_the_link(cache: Path):
    release.link().parent.mkdir(parents=True)
    (release.link().parent / "lr-install-only").touch()
    promote(cache / "0.7.44")
    assert named() == str(cache / "0.7.44")


def test_versions_compare_by_number_not_by_text(cache: Path):
    promote(cache / "0.7.9")
    promote(cache / "0.7.44")
    assert named() == str(cache / "0.7.44")


def test_a_directory_that_is_not_a_version_is_never_named(tmp_path: Path, cache: Path):
    worktree = tmp_path / "long-running"
    worktree.mkdir()
    promote(worktree)
    assert not release.link().is_symlink()


def test_a_link_whose_directory_is_gone_is_replaced(cache: Path):
    promote(cache / "0.7.57")
    (cache / "0.7.57").rmdir()
    promote(cache / "0.7.44")
    assert named() == str(cache / "0.7.44")


def test_current_is_the_named_install_or_the_callers_own_while_none_is_named(cache: Path):
    assert release.current(cache / "0.7.44") == cache / "0.7.44"
    promote(cache / "0.7.57")
    assert release.current(cache / "0.7.44") == (cache / "0.7.57").resolve()


def test_current_leaves_a_directory_that_is_no_version_where_it_is(tmp_path: Path, cache: Path):
    promote(cache / "0.7.57")
    worktree = tmp_path / "long-running"
    assert release.current(worktree) == worktree
