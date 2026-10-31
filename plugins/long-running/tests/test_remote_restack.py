from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/long-running/scripts/remote-restack.py"

SPRITE = """#!/usr/bin/env python3
import os, subprocess, sys
args = sys.argv[4:]
mac, sprite = os.environ["MAC_ROOT"], os.environ["SPRITE_ROOT"]
sys.exit(subprocess.run([sprite + arg if arg.startswith(mac) else arg for arg in args[1:]]).returncode)
"""

GH = """#!/usr/bin/env python3
import json, sys
args = sys.argv[1:]
if args[:2] == ["auth", "token"]:
    print("token")
elif args[:2] == ["pr", "view"]:
    print(json.dumps({"number": 2, "headRefName": "b", "baseRefName": "a"}))
elif args[:2] == ["pr", "list"]:
    head = args[args.index("--head") + 1]
    print(json.dumps([{"number": 1, "headRefName": "a", "baseRefName": "dev"}] if head == "a" else []))
"""


def git(repo: Path, *argv: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *argv], check=True, capture_output=True, text=True).stdout.strip()


def commit(repo: Path, name: str, text: str) -> None:
    (repo / name).write_text(text)
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", f"{name}: {text.strip()}")


def executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


@pytest.mark.parametrize("conflicting", [False, True], ids=["moves-the-chain", "conflict-pushes-nothing"])
def test_a_remote_restack_replays_the_chain_onto_its_base_on_the_sprite_and_posts_each_new_head(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, conflicting: bool) -> None:
    """Owner 1:27 PM Oct 30, through root #221702: restacks move off this machine; the lane names a PR or stack, a Sprite fetches, rebases, reports conflicts and pushes, and the lane gets the result record. p8a-step7 #227831: a conflict in the lower PR's commit was reported against the top PR with no conflict output."""
    mac, sprite = tmp_path / "mac", tmp_path / "sprite"
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("[user]\n\tname = Lane\n\temail = lane@example.com\n[init]\n\tdefaultBranch = dev\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    env = dict(os.environ)
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True, env=env)
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, env=env, capture_output=True)
    commit(work, "dev.txt", "one\n")
    git(work, "push", "-q", "origin", "HEAD:dev")
    git(work, "checkout", "-q", "-b", "a")
    commit(work, "dev.txt" if conflicting else "a.txt", "a\n")
    git(work, "checkout", "-q", "-b", "b")
    commit(work, "b.txt", "b\n")
    git(work, "push", "-q", "origin", "a", "b")
    git(work, "checkout", "-q", "dev")
    commit(work, "dev.txt", "two\n")
    git(work, "push", "-q", "origin", "dev")
    heads = {branch: git(origin, "rev-parse", branch) for branch in ("a", "b")}
    clone = mac / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), f"{sprite}{clone}"], check=True, env=env, capture_output=True)
    config = tmp_path / "runner.json"
    config.write_text(json.dumps({"drive": "d1", "landing": {"repo": "o/r", "checkout": str(clone)}, "fidelity": {"sprite": "vet4"}}))
    executable(mac / "bin" / "sprite", SPRITE)
    executable(mac / "bin" / "gh", GH)
    executable(mac / "bin" / "cci", f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {tmp_path / 'posts'}\necho '#9'\n")

    done = subprocess.run(
        [sys.executable, str(SCRIPT), "--config", str(config), "--lane", "lane-a", "2"],
        env={**env, "PATH": f"{mac / 'bin'}:{os.environ['PATH']}", "MAC_ROOT": str(mac), "SPRITE_ROOT": str(sprite)},
        capture_output=True,
        text=True,
        timeout=120,
    )

    posts = (tmp_path / "posts").read_text().splitlines() if (tmp_path / "posts").exists() else []
    if conflicting:
        assert done.returncode == 1 and "CONFLICT replaying a onto dev at " in done.stdout
        assert "  dev.txt" in done.stdout.splitlines() and "CONFLICT (content): Merge conflict in dev.txt" in done.stdout
        assert "see the lines above" not in done.stderr
        assert {branch: git(origin, "rev-parse", branch) for branch in ("a", "b")} == heads and posts == []
        return
    assert done.returncode == 0, done.stderr
    dev = git(origin, "rev-parse", "dev")
    moved = {branch: git(origin, "rev-parse", branch) for branch in ("a", "b")}
    assert git(origin, "rev-parse", "b~1") == moved["a"] and git(origin, "rev-parse", "a~1") == dev
    assert [post.split(" --text ")[1] for post in posts] == [f"HEAD #{number} {moved[branch]}: restacked onto dev {dev[:10]} on vet4 by remote-restack (was {heads[branch][:10]})" for number, branch in ((1, "a"), (2, "b"))]
