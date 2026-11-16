import re
from pathlib import Path

import ci_tip
import pytest
from conftest import commit_files, git_in
from drive import state
from drive.providers import LaneTool

TOOL = LaneTool(("tools/ci", "./tools/ci"), re.compile(r"(?<![\w./-])(?:\./)?tools/ci(?![\w./-])"), "tools/ci", ("go/ci", "tools/ci"))


@pytest.fixture
def lane(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    tip, lane = tmp_path / "drive-d", tmp_path / "lane"
    tip.mkdir()
    git_in(tip, "init", "-q")
    commit_files(tip, "ci", {"tools/ci": "#!/bin/sh\n", "go/ci/main.go": "package main\n"})
    git_in(tip, "worktree", "add", "-q", "--detach", str(lane))
    commit_files(tip, "dev moves on", {"go/ci/main.go": "package main\n\nfunc main() {}\n"})
    monkeypatch.setattr(state, "drive_checkout", lambda name, clone: tip)
    return tip, lane


def test_a_lane_runs_the_ci_the_drive_checkout_builds(lane: tuple[Path, Path]) -> None:
    tip, worktree = lane
    commit_files(worktree, "app work", {"app.txt": "x\n"})
    assert ci_tip.tip_ci("cd . && tools/ci mac-box prove j1 --commit abc", str(worktree), "d", TOOL) == f"cd . && {tip}/tools/ci mac-box prove j1 --commit abc"
    assert ci_tip.tip_ci("./tools/ci release plan", str(worktree), "d", TOOL) == f"{tip}/tools/ci release plan"


def test_a_lane_changing_go_ci_keeps_its_own_ci(lane: tuple[Path, Path]) -> None:
    _, worktree = lane
    (worktree / "go/ci/main.go").write_text("package main\n\nfunc lane() {}\n")
    assert ci_tip.tip_ci("tools/ci mac-box list", str(worktree), "d", TOOL) is None


def test_other_commands_and_the_drive_checkout_are_left_alone(lane: tuple[Path, Path]) -> None:
    tip, worktree = lane
    assert ci_tip.tip_ci("gh pr view 1 --json body", str(worktree), "d", TOOL) is None
    assert ci_tip.tip_ci("tools/ci mac-box list", str(tip), "d", TOOL) is None


def test_only_a_run_of_a_tree_file_needs_the_providers(lane: tuple[Path, Path]) -> None:
    _, worktree = lane
    assert ci_tip.runs_tree_tool("cd . && ./tools/ci release plan", str(worktree))
    assert not ci_tip.runs_tree_tool("ls tools && /usr/bin/env true", str(worktree))
