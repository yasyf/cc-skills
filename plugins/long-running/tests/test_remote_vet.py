from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/long-running/scripts/remote-vet.py"

SPRITE = """#!/usr/bin/env python3
import os, subprocess, sys
args = sys.argv[4:]
mac, sprite = os.environ["MAC_ROOT"], os.environ["SPRITE_ROOT"]
sys.exit(subprocess.run([sprite + arg if arg.startswith(mac) else arg for arg in args[1:]]).returncode)
"""

TOOL = """#!/bin/sh
printf '%s wrapper=%s %s\\n' "$(pwd)" "${{RUSTC_WRAPPER-unset}}" "$*" >> {log}
[ "$1 $(basename "$(pwd)")" != "{failing}" ]
"""


def git(repo: Path, *argv: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *argv], check=True, capture_output=True, text=True).stdout.strip()


def write(root: Path, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


def lane_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing: str, prepare: str) -> tuple[Path, Path, Path, Path, Path, dict[str, str]]:
    mac, sprite = tmp_path / "mac", tmp_path / "sprite"
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("[user]\n\tname = Lane\n\temail = lane@example.com\n[init]\n\tdefaultBranch = dev\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gitconfig))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True)
    write(work, "go/m/go.mod", "module m\n")
    write(work, "go/m/a/a.go", "package a\n")
    write(work, "go/m/b/b.go", "package b\n")
    write(work, "rust/Cargo.toml", '[workspace]\nmembers = ["c", "d"]\n')
    write(work, "rust/c/Cargo.toml", '[package]\nname = "cee"\n')
    write(work, "rust/c/src/lib.rs", "pub fn c() {}\n")
    write(work, "rust/d/Cargo.toml", '[package]\nname = "dee"\n')
    write(work, "rust/d/src/lib.rs", "pub fn d() {}\n")
    write(work, "README.md", "readme\n")
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "base")
    git(work, "push", "-q", "origin", "HEAD:dev")
    clone = mac / "clone"
    subprocess.run(["git", "clone", "-q", str(origin), f"{sprite}{clone}"], check=True, capture_output=True)
    git(work, "remote", "set-head", "origin", "dev")
    git(work, "checkout", "-q", "-b", "lane")
    write(work, "go/m/a/a.go", "package a\n\nfunc A() {}\n")
    git(work, "commit", "-q", "-am", "a")
    write(work, "rust/c/src/lib.rs", "pub fn c() -> u8 { 1 }\n")
    write(work, "go/m/a/new.go", "package a\n\nfunc New() {}\n")
    write(work, "README.md", "changed\n")
    config = tmp_path / "runner.json"
    config.write_text(json.dumps({"drive": "d1", "landing": {"repo": "o/r", "checkout": str(clone)}, "finder": {"sprite": ["vet4", "fleet2"]}, "vet": {"prepare": prepare}}))
    log = tmp_path / "tools.log"
    executable(mac / "bin" / "sprite", SPRITE)
    executable(mac / "bin" / "gh", "#!/bin/sh\necho token\n")
    executable(mac / "bin" / "go", TOOL.format(log=log, failing=failing))
    executable(mac / "bin" / "cargo", TOOL.format(log=log, failing=failing))
    env = os.environ | {"PATH": f"{mac / 'bin'}:{os.environ['PATH']}", "MAC_ROOT": str(mac), "SPRITE_ROOT": str(sprite), "SPRITE_VET_ROOT": str(mac / "vet"), "RUSTC_WRAPPER": "sccache"}
    return mac, sprite, work, config, log, env


@pytest.mark.parametrize("failing", ["", "vet m"], ids=["touched-packages-pass", "a-failure-still-runs-the-rest"])
def test_a_remote_vet_builds_the_lanes_uncommitted_change_on_the_sprite_for_only_the_packages_and_crates_it_touches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing: str) -> None:
    """Root #221365, under the owner's 11:52 AM Oct 30 order to move grading load off this machine: a lane ships its diff to a Sprite, which runs go build, go vet and cargo check on the touched packages and returns the result.

    Root #235301, from team-credential-4's #235284: no Sprite has sccache, so cargo runs with RUSTC_WRAPPER cleared, and with --tests so a test-only change compiles. Root #235223: the partial clone's lazy fetches authenticate, so every git command on the Sprite carries the credential helper."""
    mac, sprite, work, config, log, env = lane_change(tmp_path, monkeypatch, failing, "echo generated > prepared.txt && git config --get-all credential.helper > helper.txt || true")

    done = subprocess.run([sys.executable, str(SCRIPT), "--config", str(config), "--lane", "l1", str(work)], env=env, capture_output=True, text=True)

    vetted = Path(f"{sprite}{mac}/vet/vet-l1")
    assert done.returncode == (1 if failing else 0), done.stdout + done.stderr
    assert (vetted / "go/m/a/a.go").read_text() == "package a\n\nfunc A() {}\n"
    assert (vetted / "go/m/a/new.go").read_text() == "package a\n\nfunc New() {}\n"
    assert (vetted / "rust/c/src/lib.rs").read_text() == "pub fn c() -> u8 { 1 }\n"
    assert (vetted / "prepared.txt").read_text() == "generated\n"
    assert (vetted / "helper.txt").read_text().splitlines()[-1] == '!f() { echo username=x-access-token; echo "password=$GH_TOKEN"; }; f'
    assert log.read_text().splitlines() == [f"{vetted}/go/m wrapper=sccache build ./a", f"{vetted}/go/m wrapper=sccache vet ./a", f"{vetted}/rust wrapper= check --quiet --tests -p cee"]
    assert [line for line in done.stdout.splitlines() if line.startswith("START ")][:5] == ["START lock", "START fetch", "START checkout", "START apply", "START prepare"]
    assert ("FAIL cd go/m" in done.stdout) == bool(failing)
    assert "PASS cd rust" in done.stdout
    assert git(work, "status", "--porcelain") == "M README.md\n M rust/c/src/lib.rs\n?? go/m/a/new.go".lstrip()


def test_a_remote_vet_that_hangs_stops_at_its_timeout_and_names_the_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """devbox-explore-3 #234975: a vet printed nothing and hit the lane's own timeout twice; it now streams each stage and stops at a bounded wait naming the stage it stuck in."""
    _, _, work, config, _, env = lane_change(tmp_path, monkeypatch, "", "sleep 60")

    done = subprocess.run([sys.executable, str(SCRIPT), "--config", str(config), "--lane", "l1", "--timeout", "3", str(work)], env=env, capture_output=True, text=True, timeout=30)

    assert done.returncode == 1
    assert "START prepare" in done.stdout
    assert "gave no result within 3s, still in stage prepare" in done.stderr
