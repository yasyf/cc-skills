from __future__ import annotations

import json
import subprocess
from pathlib import Path
from threading import Timer

import pytest
from conftest import PLUGIN

SCRIPT = PLUGIN / "scripts" / "pr-watch-armed.sh"


def test_normalized_state_is_armed(tmp_path: Path):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"watermarks": {}}))
    result = subprocess.run(
        ["bash", str(SCRIPT), str(state_file)],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0
    assert result.stdout == f"armed {state_file}\n"
    assert result.stderr == ""


@pytest.mark.parametrize("initial", [None, "{}"])
def test_waits_for_normalized_state(tmp_path: Path, initial: str | None):
    state_file = tmp_path / "state.json"
    if initial is not None:
        state_file.write_text(initial)
    writer = Timer(0.1, state_file.write_text, args=(json.dumps({"watermarks": {}}),))
    writer.start()
    try:
        result = subprocess.run(
            ["bash", str(SCRIPT), str(state_file), "10"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    finally:
        writer.join()
    assert result.returncode == 0
    assert result.stdout == f"armed {state_file}\n"
    assert result.stderr == ""


@pytest.mark.parametrize("initial", [None, "{}", '{"watermarks": null}', '{"watermarks": false}', '{"watermarks":'])
def test_times_out_without_watermarks(tmp_path: Path, initial: str | None):
    state_file = tmp_path / "state.json"
    if initial is not None:
        state_file.write_text(initial)
    result = subprocess.run(
        ["bash", str(SCRIPT), str(state_file), "1"],
        capture_output=True,
        text=True,
        timeout=3,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == f"not-armed {state_file}: no pr-poll.sh pass wrote it\n"
