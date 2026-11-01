from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/long-running/scripts/remote-vet.py"

SPRITE = """#!/usr/bin/env python3
import json, os, subprocess, sys
args = sys.argv[4:]
if args[-1] == "/proc/loadavg":
    loads = json.loads(os.environ["LOADS"])
    if sys.argv[3] not in loads:
        sys.exit("failed to start sprite command: Storage not ready")
    print(loads[sys.argv[3]])
    sys.exit(0)
mac, sprite = os.environ["MAC_ROOT"], os.environ["SPRITE_ROOT"]
with open(os.environ["SPRITES_LOG"], "a") as log:
    log.write(sys.argv[3] + "\\n")
sys.exit(subprocess.run([sprite + arg if arg.startswith(mac) else arg for arg in args[1:]]).returncode)
"""

TOOL = """#!/bin/sh
printf '%s wrapper=%s %s\\n' "$(pwd)" "${{RUSTC_WRAPPER-unset}}" "$*" >> {log}
[ "$1 $(basename "$(pwd)")" != "{failing}" ]
"""


GOFMT = """#!/bin/sh
printf '%s gofmt %s\\n' "$(pwd)" "$*" >> {log}
for f in "$@"; do case "$f" in -*) ;; *) printf '// gofmt\\n' >> "$f" ;; esac; done
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
    git(work, "checkout", "-q", "-b", "below")
    write(work, "go/m/b/b.go", "package b\n\nfunc B() {}\n")
    git(work, "commit", "-q", "-am", "b")
    git(work, "push", "-q", "origin", "below")
    git(work, "checkout", "-q", "-b", "lane")
    write(work, "go/m/a/a.go", "package a\n\nfunc A() {}\n")
    git(work, "commit", "-q", "-am", "a")
    write(work, "rust/c/src/lib.rs", "pub fn c() -> u8 { 1 }\n")
    write(work, "go/m/a/new.go", "package a\n\nfunc New() {}\n")
    write(work, "README.md", "changed\n")
    config = tmp_path / "runner.json"
    config.write_text(json.dumps({"drive": "d1", "landing": {"repo": "o/r", "checkout": str(clone)}, "finder": {"sprite": ["vet4", "fleet2"]}, "fidelity": {"sprite": ["vet4"]}, "vet": {"sprite": ["busy", "vetbox"], "prepare": prepare}}))
    log = tmp_path / "tools.log"
    executable(mac / "bin" / "sprite", SPRITE)
    executable(mac / "bin" / "gh", "#!/bin/sh\necho token\n")
    executable(mac / "bin" / "ccx", '#!/bin/sh\necho \'{"branches": [{"branch": "below"}, {"branch": "lane"}]}\'\n')
    executable(mac / "bin" / "go", TOOL.format(log=log, failing=failing))
    executable(mac / "bin" / "cargo", TOOL.format(log=log, failing=failing))
    executable(mac / "bin" / "gofmt", GOFMT.format(log=log))
    env = os.environ | {"PATH": f"{mac / 'bin'}:{os.environ['PATH']}", "MAC_ROOT": str(mac), "SPRITE_ROOT": str(sprite), "SPRITE_VET_ROOT": str(mac / "vet"), "RUSTC_WRAPPER": "sccache", "SPRITES_LOG": str(tmp_path / "sprites.log"), "LOADS": json.dumps({"busy": "21.5", "vetbox": "3.2"})}
    return mac, sprite, work, config, log, env


@pytest.mark.parametrize("failing", ["", "vet m"], ids=["touched-packages-pass", "a-failure-still-runs-the-rest"])
def test_a_remote_vet_builds_the_lanes_uncommitted_change_on_the_sprite_for_only_the_packages_and_crates_it_touches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing: str) -> None:
    """Root #221365, under the owner's 11:52 AM Oct 30 order to move grading load off this machine: a lane ships its diff to a Sprite, which runs go build, go vet and cargo check on the touched packages and returns the result.

    Root #235301, from team-credential-4's #235284: no Sprite has sccache, so cargo runs with RUSTC_WRAPPER cleared, and with --tests so a test-only change compiles. Root #235223: the partial clone's lazy fetches authenticate, so every git command on the Sprite carries the credential helper. remote-desks-5, 10:50 PM Oct 30: dar-vet-1 and dar-fleet-14 to 16, 8 GB with no swap, restarted mid-build under go's default parallelism, so builds run at most three compile jobs."""
    mac, sprite, work, config, log, env = lane_change(tmp_path, monkeypatch, failing, "echo generated > prepared.txt && git config --get-all credential.helper > helper.txt || true")

    done = subprocess.run([sys.executable, str(SCRIPT), "--config", str(config), "--lane", "l1", str(work)], env=env, capture_output=True, text=True)

    vetted = Path(f"{sprite}{mac}/vet/vet-l1")
    assert done.returncode == (1 if failing else 0), done.stdout + done.stderr
    assert (vetted / "go/m/a/a.go").read_text() == "package a\n\nfunc A() {}\n"
    assert (vetted / "go/m/a/new.go").read_text() == "package a\n\nfunc New() {}\n"
    assert (vetted / "rust/c/src/lib.rs").read_text() == "pub fn c() -> u8 { 1 }\n"
    assert (vetted / "prepared.txt").read_text() == "generated\n"
    assert (vetted / "helper.txt").read_text().splitlines()[-1] == '!f() { echo username=x-access-token; echo "password=$GH_TOKEN"; }; f'
    assert log.read_text().splitlines() == [f"{vetted}/go/m gofmt -w a/a.go a/new.go", f"{vetted}/rust wrapper= fmt -p cee", f"{vetted}/go/m wrapper=sccache build -p 3 ./a", f"{vetted}/go/m wrapper=sccache vet -p 3 ./a", f"{vetted}/rust wrapper= check --quiet --tests -j 3 -p cee"]
    assert [line for line in done.stdout.splitlines() if line.startswith("START ")][:5] == ["START lock", "START fetch", "START checkout", "START apply", "START prepare"]
    assert ("FAIL cd go/m" in done.stdout) == bool(failing)
    assert "PASS cd rust" in done.stdout
    assert (tmp_path / "sprites.log").read_text() == "vetbox\n"
    assert git(work, "status", "--porcelain") == "M README.md\n M go/m/a/a.go\n M rust/c/src/lib.rs\n?? go/m/a/new.go".lstrip()


def test_a_remote_vet_formats_the_touched_go_and_rust_on_the_sprite_and_brings_the_formatting_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Owner card improve7-format-on-sprite (root #277422): a worker formats and lints its Rust and Go changes on a build machine before it pushes, never with a local toolchain. The Sprite formats the touched files, and the formatting comes back as a patch applied to the lane's worktree, so the lane commits formatted code without gofmt or cargo fmt on the Mac."""
    mac, sprite, work, config, log, env = lane_change(tmp_path, monkeypatch, "", "")

    done = subprocess.run([sys.executable, str(SCRIPT), "--config", str(config), "--lane", "l1", str(work)], env=env, capture_output=True, text=True)

    assert done.returncode == 0, done.stdout + done.stderr
    assert (work / "go/m/a/a.go").read_text() == "package a\n\nfunc A() {}\n// gofmt\n"
    assert (work / "go/m/a/new.go").read_text() == "package a\n\nfunc New() {}\n// gofmt\n"
    assert (work / "go/m/b/b.go").read_text() == "package b\n\nfunc B() {}\n"
    assert "remote-vet: formatted 2 files in" in done.stdout
    assert "FORMAT-BEGIN" not in done.stdout


def test_a_remote_vet_that_hangs_stops_at_its_timeout_and_names_the_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """devbox-explore-3 #234975: a vet printed nothing and hit the lane's own timeout twice; it now streams each stage and stops at a bounded wait naming the stage it stuck in."""
    _, _, work, config, _, env = lane_change(tmp_path, monkeypatch, "", "sleep 60")

    done = subprocess.run([sys.executable, str(SCRIPT), "--config", str(config), "--lane", "l1", "--timeout", "3", str(work)], env=env, capture_output=True, text=True, timeout=30)

    assert done.returncode == 1
    assert "START prepare" in done.stdout
    assert "gave no result within 3s, still in stage prepare" in done.stderr


def test_a_remote_vet_refuses_when_every_vet_sprite_is_over_load_so_the_lane_pushes_for_ci(tmp_path: Path) -> None:
    """progress-build-6 #230992, root #231006: two vets ran go build for 30+ min at Sprite load 39 against a 2-minute target, so the lane waited instead of pushing."""
    mac, work = tmp_path / "mac", tmp_path / "work"
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    config = tmp_path / "runner.json"
    config.write_text(json.dumps({"drive": "d1", "landing": {"repo": "o/r", "checkout": str(mac / "clone")}, "vet": {"sprite": ["busy", "busier"]}}))
    executable(mac / "bin" / "sprite", SPRITE)
    env = os.environ | {"PATH": f"{mac / 'bin'}:{os.environ['PATH']}", "MAC_ROOT": str(mac), "SPRITE_ROOT": str(tmp_path / "sprite"), "SPRITES_LOG": str(tmp_path / "sprites.log"), "LOADS": json.dumps({"busy": "16.5", "busier": "39"})}

    done = subprocess.run([sys.executable, str(SCRIPT), "--config", str(config), "--lane", "l1", str(work)], env=env, capture_output=True, text=True)

    assert done.returncode == 3, done.stdout + done.stderr
    assert done.stdout == "remote-vet: every vet Sprite is over load 16 (busy at 16.5); push and let CI grade l1\n"
    assert not (tmp_path / "sprites.log").exists()


@pytest.mark.parametrize(("loads", "said"), [
    ({"busy": "39"}, "remote-vet: every vet Sprite is over load 16 (busy at 39); push and let CI grade l1\n"),
    ({}, "remote-vet: no vet Sprite of down, busy answered; push and let CI grade l1\n"),
], ids=["one-down", "all-down"])
def test_a_vet_sprite_that_cannot_start_a_command_is_passed_over_instead_of_failing_the_vet(tmp_path: Path, loads: dict[str, str], said: str) -> None:
    """pod-volume-3 #257954: a vet Sprite answering 'Storage not ready' failed every vet instead of leaving the choice to the Sprites that answered."""
    mac, work = tmp_path / "mac", tmp_path / "work"
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    config = tmp_path / "runner.json"
    config.write_text(json.dumps({"drive": "d1", "landing": {"repo": "o/r", "checkout": str(mac / "clone")}, "vet": {"sprite": ["down", "busy"]}}))
    executable(mac / "bin" / "sprite", SPRITE)
    env = os.environ | {"PATH": f"{mac / 'bin'}:{os.environ['PATH']}", "MAC_ROOT": str(mac), "SPRITE_ROOT": str(tmp_path / "sprite"), "SPRITES_LOG": str(tmp_path / "sprites.log"), "LOADS": json.dumps(loads)}

    done = subprocess.run([sys.executable, str(SCRIPT), "--config", str(config), "--lane", "l1", str(work)], env=env, capture_output=True, text=True)

    assert done.returncode == 3, done.stdout + done.stderr
    assert done.stdout == said
    assert not (tmp_path / "sprites.log").exists()


@pytest.mark.parametrize("vet", [{}, {"vet": {"prepare": "true"}}, {"vet": {"sprite": []}}], ids=["no-vet", "no-sprite", "empty-sprite"])
def test_a_remote_vet_with_no_build_sprite_refuses_and_never_falls_back_to_a_grading_sprite(tmp_path: Path, vet: dict) -> None:
    """Root #240536: remote-vet never runs on a grading box, and refuses when no BUILD Sprite is registered."""
    mac, work = tmp_path / "mac", tmp_path / "work"
    subprocess.run(["git", "init", "-q", str(work)], check=True)
    config = tmp_path / "runner.json"
    config.write_text(json.dumps({"drive": "d1", "landing": {"repo": "o/r", "checkout": str(mac / "clone")}, "finder": {"sprite": ["grader"]}, "fidelity": {"sprite": ["grader"]}} | vet))
    executable(mac / "bin" / "sprite", SPRITE)
    env = os.environ | {"PATH": f"{mac / 'bin'}:{os.environ['PATH']}", "MAC_ROOT": str(mac), "SPRITE_ROOT": str(tmp_path / "sprite"), "SPRITES_LOG": str(tmp_path / "sprites.log"), "LOADS": json.dumps({"grader": "1"})}

    done = subprocess.run([sys.executable, str(SCRIPT), "--config", str(config), "--lane", "l1", str(work)], env=env, capture_output=True, text=True, check=False)

    assert done.returncode == 3, done.stdout + done.stderr
    assert done.stdout == f"remote-vet: {config} names no BUILD Sprite under vet.sprite, and a vet never runs on a grading Sprite; pass --sprite or push and let CI grade l1\n"
    assert not (tmp_path / "sprites.log").exists()
