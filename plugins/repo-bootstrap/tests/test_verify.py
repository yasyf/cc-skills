"""verify subcommand: offline checks (leftover tokens, LICENSE) and an opt-in
end-to-end tier that scaffolds a real python repo and runs the full verify."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from bootstrap import verify

SCRIPTS = Path(__file__).resolve().parent.parent / "skills" / "repo-bootstrap" / "scripts"
BOOTSTRAP = SCRIPTS / "bootstrap.py"

BASE_VARS = [
    "--var", "PROJECT_NAME=demo-proj",
    "--var", "DESCRIPTION=A demo project.",
    "--var", "AUTHOR_NAME=Jane Doe",
    "--var", "AUTHOR_EMAIL=jane@example.com",
    "--var", "GITHUB_USER=janedoe",
    "--var", "LICENSE_ID=MIT",
]
# PYTHON_MIN must satisfy the docs feature's great-docs floor (>=3.11).
PY_VARS = BASE_VARS + [
    "--var", "DIST_NAME=demo-proj",
    "--var", "PACKAGE=demo_proj",
    "--var", "PYTHON_PIN=3.12",
    "--var", "PYTHON_MIN=3.11",
]


# --- offline check units ---

def test_no_leftover_tokens_pass(tmp_path, monkeypatch):
    (tmp_path / "clean.md").write_text("all good here\n")
    monkeypatch.chdir(tmp_path)
    ok, _ = verify._no_leftover_tokens()
    assert ok


def test_no_leftover_tokens_fail(tmp_path, monkeypatch):
    (tmp_path / "bad.md").write_text("oops {{PROJECT_NAME}} left in\n")
    monkeypatch.chdir(tmp_path)
    ok, output = verify._no_leftover_tokens()
    assert not ok
    assert "{{PROJECT_NAME}}" in output


def test_no_leftover_tokens_skips_ignored_dirs(tmp_path, monkeypatch):
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "x.txt").write_text("{{LEFT}}\n")
    monkeypatch.chdir(tmp_path)
    ok, _ = verify._no_leftover_tokens()
    assert ok  # dist/ is excluded


def test_license_check(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert not verify._license_check(False)[0]
    (tmp_path / "LICENSE").write_text("")
    assert not verify._license_check(False)[0]  # empty fails
    (tmp_path / "LICENSE").write_text("MIT License\n")
    assert verify._license_check(False)[0]


def test_license_check_no_license(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert verify._license_check(True)[0]  # absent passes
    (tmp_path / "LICENSE").write_text("MIT License\n")
    ok, output = verify._license_check(True)
    assert not ok
    assert "delete it" in output
    (tmp_path / "LICENSE").unlink()
    (tmp_path / "LICENSE").symlink_to(tmp_path / "gone")
    assert not verify._license_check(True)[0]  # dangling symlink is still a LICENSE entry


# --- opt-in end-to-end (needs the uv toolchain + network for capt-hook) ---

@pytest.mark.uv
@pytest.mark.parametrize(
    ("features", "license_id"),
    [("docs,pypi", "MIT"), ("", "none")],
    ids=["all-features-mit", "bare-no-license"],
)
def test_end_to_end_python(tmp_path, features, license_id):
    if not shutil.which("uv"):
        pytest.skip("uv not installed")
    # Don't let the test-runner's own venv leak into the scaffolded project's uv runs.
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    py_vars = [f"LICENSE_ID={license_id}" if v == "LICENSE_ID=MIT" else v for v in PY_VARS]
    # Phase 0 precondition: a bootstrapped project is a git repo on main, which the
    # prek hook-config check (`uvx prek prepare-hooks`) needs to find a git root.
    subprocess.run(["git", "init", "-b", "main", str(tmp_path)], check=True, capture_output=True)
    scaffold = subprocess.run(
        [sys.executable, str(BOOTSTRAP), "scaffold", "--target", str(tmp_path),
         "--layer", "python", "--extras", "none", "--features", features, *py_vars],
        capture_output=True, text=True, env=env,
    )
    assert scaffold.returncode == 0, scaffold.stdout + scaffold.stderr

    verify_flags = ["--no-license"] if license_id == "none" else []
    result = subprocess.run(
        [sys.executable, str(BOOTSTRAP), "verify", "--layer", "python", "--target", str(tmp_path), *verify_flags],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "All checks passed" in result.stdout
