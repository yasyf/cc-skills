from __future__ import annotations

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "skills/long-running/scripts/ts-delta.sh"

TSGO = """#!/bin/sh
case " $* " in *" --listFilesOnly "*) echo "$FAKE_TSGO_ROOT/api/src/a.ts"; exit 0 ;; esac
if [ "$PWD" = "$FAKE_TSGO_ROOT" ]; then cat "$FAKE_TSGO_HEAD"; else cat "$FAKE_TSGO_BASE"; fi
exit 1
"""

NODE_TEST = "api/src/a.ts(1,18): error TS2591: Cannot find name 'node:test'. Do you need to install type definitions for node? Try `npm i --save-dev @types/node` and then add 'node' to the types field in your tsconfig."
STRING_AS_NUMBER = "error TS2322: Type 'string' is not assignable to type 'number'."
IMPLICIT_T = "error TS7006: Parameter 't' implicitly has an 'any' type."


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_a_moved_error_stays_out_and_the_new_one_is_named_at_its_own_line(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    (repo / "api/src").mkdir(parents=True)
    (repo / "yarn.lock").write_text(
        '"@typescript/native-preview@npm:7.0.0-test":\n  resolution: "@typescript/native-preview@npm:7.0.0-test"\n'
    )
    (repo / "tsconfig.base.json").write_text("{}\n")
    (repo / "api/tsconfig.json").write_text("{}\n")
    source = repo / "api/src/a.ts"
    source.write_text(
        'import test from "node:test"\nconst count: number = "three"\ntest("old", async (t) => {})\n'
    )
    git(repo, "init", "-q")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    git(repo, "update-ref", "refs/remotes/origin/dev", "HEAD")
    source.write_text(
        'import test from "node:test"\nconst total: number = "four"\ntest("new", async (t) => {})\nconst count: number = "three"\ntest("old", async (t) => {})\n'
    )
    root = git(repo, "rev-parse", "--show-toplevel")
    system = (
        subprocess.run(["uname", "-s"], check=True, capture_output=True, text=True)
        .stdout.strip()
        .lower()
    )
    arch = subprocess.run(
        ["uname", "-m"], check=True, capture_output=True, text=True
    ).stdout.strip()
    machine = {"x86_64": "x64", "aarch64": "arm64"}.get(arch, arch)
    tsgo = (
        tmp_path
        / "cache"
        / "ts-delta"
        / f"7.0.0-test-{system}-{machine}"
        / "package"
        / "lib"
        / "tsgo"
    )
    tsgo.parent.mkdir(parents=True)
    tsgo.write_text(TSGO)
    tsgo.chmod(0o755)
    (tmp_path / "base.txt").write_text(
        f"{NODE_TEST}\napi/src/a.ts(2,7): {STRING_AS_NUMBER}\napi/src/a.ts(3,20): {IMPLICIT_T}\n"
    )
    (tmp_path / "head.txt").write_text(
        f"{NODE_TEST}\napi/src/a.ts(2,7): {STRING_AS_NUMBER}\napi/src/a.ts(3,20): {IMPLICIT_T}\napi/src/a.ts(4,7): {STRING_AS_NUMBER}\napi/src/a.ts(5,20): {IMPLICIT_T}\n"
    )
    env = {
        **os.environ,
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "FAKE_TSGO_ROOT": root,
        "FAKE_TSGO_BASE": str(tmp_path / "base.txt"),
        "FAKE_TSGO_HEAD": str(tmp_path / "head.txt"),
    }

    ran = subprocess.run(
        ["bash", str(SCRIPT), "api"],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert (ran.returncode, ran.stdout) == (
        1,
        f"api/src/a.ts(2,7): {STRING_AS_NUMBER}\n",
    ), ran.stderr
    assert (
        "1 new errors in api" in ran.stderr
        and "3 from types this checkout lacks left out" in ran.stderr
    )
