"""Guards that `ab` resolves the Browserbase key only for calls that can use it."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

AB = Path(__file__).resolve().parents[1] / "bin" / "ab"

COOKIESYNC = """#!/bin/bash
[ "$1" = requestor ] && echo test-requestor
"""

OP = """#!/bin/bash
touch "$OP_MARKER"
"""

AGENT_BROWSER = """#!/bin/bash
exit 0
"""


def stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(body)
    path.chmod(0o755)


def run_ab(tmp_path: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub(bin_dir, "cookiesync", COOKIESYNC)
    stub(bin_dir, "op", OP)
    stub(bin_dir, "agent-browser", AGENT_BROWSER)
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "OP_MARKER": str(tmp_path / "op-called"),
    }
    return subprocess.run([str(AB), *argv], capture_output=True, text=True, env=env)


@pytest.mark.parametrize("argv", [("--local", "mode"), ("--local", "get", "url"), ("--bridge", "mode")])
def test_local_and_bridge_calls_never_read_the_key(tmp_path: Path, argv: tuple[str, ...]):
    proc = run_ab(tmp_path, *argv)
    assert proc.returncode == 0, proc.stderr
    assert not (tmp_path / "op-called").exists()


def test_default_mode_reads_the_key(tmp_path: Path):
    proc = run_ab(tmp_path, "mode")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "local"
    assert (tmp_path / "op-called").exists()
