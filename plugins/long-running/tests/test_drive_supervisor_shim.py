from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SHIM = Path(__file__).resolve().parents[1] / "skills" / "long-running" / "scripts" / "drive_supervisor.py"


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["run", "--config", "lr.json"], "supervisor run --config lr.json"),
        (["install", "--config", "lr.json"], "supervisor install --config lr.json"),
        (["uninstall", "--config", "lr.json"], "supervisor uninstall --config lr.json"),
        (["lanes", "a", "--config", "lr.json", "--json"], "lanes a --config lr.json --json"),
        (["route", "a", "--config", "lr.json"], "route a --config lr.json"),
        (["request", "stand-down", "a", "--config", "lr.json", "--last-read", "5"], "request stand-down a --config lr.json --last-read 5"),
    ],
)
def test_each_retired_verb_reaches_the_lr_verb_that_replaced_it(tmp_path: Path, argv: list[str], expected: str):
    scripts = tmp_path / "skills" / "long-running" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(SHIM, scripts / SHIM.name)
    lr = tmp_path / "bin" / "lr"
    lr.parent.mkdir()
    lr.write_text('#!/bin/sh\nprintf "%s" "$*"\n')
    lr.chmod(0o755)

    done = subprocess.run([sys.executable, str(scripts / SHIM.name), *argv], capture_output=True, text=True, check=True)

    assert done.stdout == expected
