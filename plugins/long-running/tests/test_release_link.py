from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from long_running import release
from long_running.release import promote

SHIM = Path(__file__).resolve().parents[1] / "scripts" / "bin-shim.sh"
TOOL = 'import pathlib\nroot = pathlib.Path(__file__).resolve().parents[3]\nprint("{version}", (root / "data.txt").read_text())\n'


def install(cache: Path, version: str) -> Iterator[None]:
    root = cache / version
    (root / "bin").mkdir(parents=True)
    (root / "scripts").mkdir()
    shim = root / "scripts" / "bin-shim.sh"
    shim.write_text(SHIM.read_text())
    shim.chmod(0o755)
    (root / "bin" / "tool.py").symlink_to("../scripts/bin-shim.sh")
    yield
    scripts = root / "skills" / "long-running" / "scripts"
    scripts.mkdir(parents=True)
    yield
    (scripts / "tool.py").write_text(TOOL.format(version=version))
    yield
    (root / "data.txt").write_text(version)
    promote(root)
    yield


def installed(cache: Path, version: str) -> None:
    for _ in install(cache, version):
        pass


def shim_reader(cache: Path, started_on: str) -> str:
    return subprocess.run(
        [str(cache / started_on / "bin" / "tool.py")],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": os.environ["PATH"], "CLAUDE_CONFIG_DIR": str(cache.parent / "config")},
    ).stdout.strip()


def hook_reader(started_on: Path) -> str:
    root = release.current(started_on)
    return f"{root.name} {(root / 'data.txt').read_text()}"


@pytest.mark.parametrize("installs", [1, 144])
def test_a_reader_that_starts_during_an_install_runs_one_whole_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, installs: int):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    cache = tmp_path / "cache"
    installed(cache, "0.7.1")
    started_on = cache / "0.7.1"
    for number in range(2, installs + 2):
        version, previous = f"0.7.{number}", f"0.7.{number - 1}"
        for step, _ in enumerate(install(cache, version)):
            expected = version if step == 3 else previous
            assert hook_reader(started_on) == f"{expected} {expected}"
            if installs == 1 or step == 2:
                assert shim_reader(cache, "0.7.1") == f"{expected} {expected}"
