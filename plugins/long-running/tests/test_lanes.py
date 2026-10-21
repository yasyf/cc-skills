"""drive.py lane-status and worktree against real git worktrees, with cci and gh faked."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import drive
import ledger
import pytest
from conftest import LEDGER

ROOT_SESSION = "900424b6-7393-480c-a26a-f1bd21da6e57"
LANE = "inc-liam-load-opus"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout.strip()


class LaneShell(ledger.Shell):
    def __init__(self) -> None:
        self.records: list[dict] = []
        self.pulls: dict[str, list[dict]] = {}
        self.heads: dict[str, str] = {}
        self.posts: list[list[str]] = []

    def run(self, argv: list[str], stdin: str | None = None) -> str:
        if argv[:2] == ["cci", "tail"]:
            since = argv[argv.index("--since") + 1]
            return "".join(json.dumps(record) + "\n" for record in self.records if not since.isdigit() or record["seq"] > int(since))
        if argv[:2] == ["cci", "post"]:
            self.posts.append(argv)
            return f"#{len(self.posts)}\n"
        if argv[:3] == ["gh", "pr", "list"]:
            return json.dumps(self.pulls.get(argv[argv.index("--head") + 1], []))
        if argv[:3] == ["gh", "pr", "view"]:
            return json.dumps({"headRefName": self.heads[argv[3]]})
        return super().run(argv, stdin)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv(drive.DRIVE_ENV, raising=False)
    monkeypatch.setenv(drive.SESSION_ENV, ROOT_SESSION)
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "dev", str(origin)], check=True)
    checkout = tmp_path / "monorepo"
    subprocess.run(["git", "init", "-q", "-b", "dev", str(checkout)], check=True)
    git(checkout, "remote", "add", "origin", str(origin))
    git(checkout, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "dev: start")
    git(checkout, "push", "-q", "-u", "origin", "dev")
    git(checkout, "remote", "set-head", "origin", "dev")
    monkeypatch.chdir(checkout)
    assert drive.main(["start", "--ledger", LEDGER, "--cci-drive", "d1"]) == 0
    return home


def worktree(home: Path, name: str, *add: str) -> Path:
    path = home / ".claude" / "worktrees" / "monorepo" / name
    git(Path.cwd(), "worktree", "add", "-q", *add, str(path), "dev")
    return path


def commit(path: Path, message: str) -> None:
    git(path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", message)


def lane_status(shell: LaneShell, lane: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = drive.main(["lane-status", lane], shell)
    return code, capsys.readouterr().out


def test_a_detached_worktree_named_for_the_lane_reports_its_dirty_files_and_that_nothing_is_pushed(home, capsys):
    path = worktree(home, LANE, "--detach")
    (path / "chain.rs").write_text("fn copy_chain() {}\n")

    code, out = lane_status(LaneShell(), LANE, capsys)

    assert code == 0
    assert out.startswith(f"- {path} (git worktree list): HEAD (no branch), 1 dirty file: chain.rs, 0 ahead of trunk, last commit 0 min ago ")
    assert out.rstrip().endswith("dev: start, not pushed, no PR")


def test_a_worktree_named_for_its_branch_is_found_from_the_lanes_worktree_record_and_its_pr(home, capsys):
    path = worktree(home, "inc-liam-fork-parent-grant", "-b", "inc-liam-fork-parent-grant")
    commit(path, "sandsql-server: 🐛 copy through the parent grant")
    git(path, "push", "-q", "-u", "origin", "inc-liam-fork-parent-grant")
    shell = LaneShell()
    shell.records = [
        {"seq": 1, "drive": "d1", "lane": LANE, "kind": "state", "text": f"WORKTREE {LANE} {path} inc-liam-fork-parent-grant", "fields": {"attrs": {"worktree": str(path)}}},
        {"seq": 2, "drive": "d1", "lane": LANE, "kind": "opened", "text": "opened #41461", "refs": {"prs": ["Forge-AI/monorepo#41461"]}},
    ]
    shell.heads["41461"] = "inc-liam-fork-parent-grant"
    shell.pulls["inc-liam-fork-parent-grant"] = [{"number": 41461, "state": "OPEN", "statusCheckRollup": [{"conclusion": "SUCCESS"}, {"conclusion": "FAILURE"}]}]

    code, out = lane_status(shell, LANE, capsys)

    assert code == 0
    assert out.startswith(f"- {path} (cci record, PR #41461 head): inc-liam-fork-parent-grant...origin/inc-liam-fork-parent-grant, 0 dirty files, 1 ahead of trunk, ")
    assert out.rstrip().endswith("sandsql-server: 🐛 copy through the parent grant, pushed, PR #41461 open CI red")


def test_a_commit_past_the_pushed_head_reads_as_not_pushed(home, capsys):
    path = worktree(home, LANE, "-b", LANE)
    git(path, "push", "-q", "-u", "origin", LANE)
    commit(path, "api: 🐛 the fix")

    _, out = lane_status(LaneShell(), LANE, capsys)

    assert f", HEAD not pushed, origin at {git(path, 'rev-parse', 'origin/dev')[:10]}, no PR" in out


def test_a_lane_with_no_worktree_says_so_and_exits_nonzero(home, capsys):
    worktree(home, "inc-other-fix", "--detach")

    assert lane_status(LaneShell(), LANE, capsys) == (1, f"no worktree found for lane {LANE}\n")


def test_worktree_posts_one_record_naming_the_lanes_worktree_and_branch(home, tmp_path):
    path = worktree(home, "inc-liam-fork-parent-grant", "-b", "inc-liam-fork-parent-grant")
    (path / "src").mkdir()
    shell = LaneShell()

    assert drive.main(["worktree", "--lane", LANE, "--path", str(path / "src")], shell) == 0
    elsewhere = tmp_path / "elsewhere"
    subprocess.run(["git", "init", "-q", str(elsewhere)], check=True)
    assert drive.main(["worktree", "--lane", LANE, "--path", str(elsewhere)], shell) == drive.OUTSIDE_REPO

    [post] = shell.posts
    flags = dict(zip(post[2::2], post[3::2], strict=False))
    assert flags["--id"] == f"worktree:{LANE}:{path}" and flags["--kind"] == "state" and flags["--drive"] == "d1"
    assert flags["--text"] == f"WORKTREE {LANE} {path} inc-liam-fork-parent-grant"
    assert [post[index + 1] for index, arg in enumerate(post) if arg == "--attr"] == [f"worktree={path}", "branch=inc-liam-fork-parent-grant"]
