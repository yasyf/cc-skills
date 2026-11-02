from __future__ import annotations

import subprocess

import merge_task


def commit(checkout, text):
    (checkout / "task.sh").write_text(f'#!/bin/sh\necho "$0 {text} $*"\n')
    (checkout / "task.sh").chmod(0o755)
    subprocess.run(["git", "-C", str(checkout), "add", "task.sh"], check=True)
    subprocess.run(["git", "-C", str(checkout), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", text], check=True)
    return subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()


def test_a_task_runs_in_the_merge_worktree_at_its_head_and_follows_a_newer_head(tmp_path):
    checkout = tmp_path / "runner"
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    first = commit(checkout, "first")
    second = commit(checkout, "second")
    tree = tmp_path / "runner.merge"
    ran = merge_task.run({"checkout": str(checkout), "head": first, "argv": [str(checkout / "task.sh"), "10", "--check"]})
    assert ran == {"code": 0, "out": f"{tree}/task.sh first 10 --check\n", "err": ""}
    ran = merge_task.run({"checkout": str(checkout), "head": second, "argv": [str(checkout / "task.sh"), "10"]})
    assert ran["out"] == f"{tree}/task.sh second 10\n"


def test_a_head_the_clone_lacks_is_an_error_result_not_a_crash(tmp_path):
    checkout = tmp_path / "runner"
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    commit(checkout, "first")
    ran = merge_task.run({"checkout": str(checkout), "head": "0" * 40, "argv": [str(checkout / "task.sh")]})
    assert "did not reach 000000000000" in ran["error"]


def test_a_worktree_a_failed_first_task_left_without_its_git_link_is_made_again(tmp_path):
    checkout = tmp_path / "runner"
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    head = commit(checkout, "first")
    (tmp_path / "runner.merge" / "infra").mkdir(parents=True)
    ran = merge_task.run({"checkout": str(checkout), "head": head, "argv": [str(checkout / "task.sh")]})
    assert ran["out"] == f"{tmp_path / 'runner.merge'}/task.sh first \n"


def test_a_worktree_whose_index_a_sprite_restart_truncated_is_made_again(tmp_path):
    checkout = tmp_path / "runner"
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    first = commit(checkout, "first")
    second = commit(checkout, "second")
    merge_task.run({"checkout": str(checkout), "head": first, "argv": [str(checkout / "task.sh")]})
    index = checkout / ".git" / "worktrees" / "runner.merge" / "index"
    index.write_bytes(index.read_bytes()[:10])
    ran = merge_task.run({"checkout": str(checkout), "head": second, "argv": [str(checkout / "task.sh")]})
    assert ran["out"] == f"{tmp_path / 'runner.merge'}/task.sh second \n"
