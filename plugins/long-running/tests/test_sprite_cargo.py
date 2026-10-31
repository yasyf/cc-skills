from __future__ import annotations

import fcntl
import json
import os
import subprocess
import time
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1] / "skills/long-running/scripts/sprite-cargo.sh"
)

SPRITE = """#!/usr/bin/env python3
import json, os, subprocess, sys
with open(os.environ["SPRITE_CALLS"], "a") as calls:
    calls.write(json.dumps(sys.argv[1:]) + "\\n")
args = sys.argv[2:]
cwd, env = None, dict(os.environ)
while args[0] != "--":
    flag = args.pop(0)
    if flag == "--dir":
        cwd = args.pop(0)
    elif flag == "--env":
        env.update(pair.split("=", 1) for pair in args.pop(0).split(","))
    elif flag == "-s":
        args.pop(0)
sys.exit(subprocess.run(args[1:], cwd=cwd, env=env).returncode)
"""


def age(path: Path, hours: float) -> None:
    stamp = time.time() - hours * 3600
    for inner in sorted(path.rglob("*"), reverse=True):
        os.utime(inner, (stamp, stamp))
    os.utime(path, (stamp, stamp))


def tree(path: Path, hours: float) -> Path:
    (path / "debug" / "deps").mkdir(parents=True)
    (path / "debug" / "deps" / "lib.rlib").write_text("x")
    age(path, hours)
    return path


def run(tmp_path: Path, command: str) -> tuple[subprocess.CompletedProcess[str], Path]:
    repo = tmp_path / "repo"
    (repo / "rust").mkdir(parents=True)
    (repo / "rust" / "Cargo.toml").write_text("[workspace]\n")
    (repo / "other.txt").write_text("skip")
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "sprite").write_text(SPRITE)
    (bin_dir / "sprite").chmod(0o755)
    root = tmp_path / "work"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "SPRITE_CARGO_ROOT": str(root),
        "SPRITE_CALLS": str(tmp_path / "calls.jsonl"),
    }
    result = subprocess.run(
        ["sh", str(SCRIPT), "box", "lane-a", str(repo), "HEAD", command, "rust"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, root


def test_command_builds_into_the_lanes_target_from_a_tree_it_then_removes(
    tmp_path: Path,
) -> None:
    result, root = run(tmp_path, 'echo "$CARGO_TARGET_DIR $(ls)" > ../seen; exit 3')

    assert result.returncode == 3
    assert (root / "seen").read_text().split() == [str(root / "target-lane-a"), "rust"]
    assert (root / "target-lane-a").is_dir()
    assert not list(root.glob("src-*"))


def test_idle_targets_and_trees_go_and_recent_or_own_ones_stay(tmp_path: Path) -> None:
    root = tmp_path / "work"
    stale = [
        tree(root / "target", 7),
        tree(root / "target-old", 7),
        tree(root / "src-old-1-2", 7),
    ]
    kept = [
        tree(root / "target-busy", 1),
        tree(root / "target-lane-a", 30),
        tree(root / "p8f-tree", 30),
    ]
    busy_deep = tree(root / "target-deep", 7)
    (busy_deep / "debug" / "deps" / "new.rlib").write_text("y")
    building = tree(root / "target-building", 7)
    held = (root / "target-building.lock").open("w")
    fcntl.flock(held, fcntl.LOCK_SH)

    result, _ = run(tmp_path, "true")
    held.close()

    assert result.returncode == 0, result.stderr
    assert [path.exists() for path in stale] == [False, False, False]
    assert all(path.exists() for path in [*kept, busy_deep, building])
    assert f"removing {root / 'target-old'}, idle over 6 h" in result.stderr


def test_idle_removal_runs_in_its_own_exec_with_no_stdin_before_the_upload(
    tmp_path: Path,
) -> None:
    """signal-valkey-2 #264389: a large idle-target removal ran inside the upload's exec, outlived its session, and the run died with the exec session lost while stdin was attached."""
    result, _ = run(tmp_path, "true")

    assert result.returncode == 0, result.stderr
    cleanup, upload, _ = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert "--no-stdin" in cleanup and "rm -rf" in cleanup[-3]
    assert "--no-stdin" not in upload and "rm -rf" not in " ".join(upload)
