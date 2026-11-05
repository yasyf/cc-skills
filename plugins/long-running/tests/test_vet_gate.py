from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from cc_transcript.query import Session
from captain_hook.events import PreToolUseEvent
from captain_hook.testing.helpers import build_context
from captain_hook.util.builds import LOAD_PER_CORE

from hooks import vet_before_push
from hooks.vet_before_push import compiles, go_commands, note_attempted_checks, record


def touched_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, list[Path]]:
    monkeypatch.setattr(vet_before_push, "run_quietly", lambda argv, _cwd: subprocess.CompletedProcess(argv, 0, "/usr/local/go\n", ""))
    module = tmp_path / "go"
    changed = [module / name / f"{name}.go" for name in ("a", "b", "c")]
    for path in changed:
        path.parent.mkdir(parents=True)
        path.write_text("package x\n")
    (module / "go.mod").write_text("module x\n")
    return module, changed


def event(tmp_path: Path, command: str) -> PreToolUseEvent:
    transcript = tmp_path / "lane.jsonl"
    transcript.touch()
    raw = {"session_id": "0123456789abcdef", "transcript_path": str(transcript), "cwd": str(tmp_path), "tool_name": "Bash", "tool_input": {"command": command}}
    return PreToolUseEvent(_raw=raw, ctx=build_context(transcript=Session.from_path(transcript), session_dir=tmp_path / "session"))


def test_one_refused_line_counts_for_every_touched_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module, changed = touched_module(tmp_path, monkeypatch)

    assert (commands := go_commands(event(tmp_path, "ccx vcs ship"), tmp_path, changed)) == [f"cd {module} && go build ./a ./b ./c && go vet ./a ./b ./c"]

    refused = event(tmp_path, commands[0])
    record(refused, [check for call in refused.command.calls() for check in compiles(refused, call)])

    assert go_commands(event(tmp_path, "ccx vcs ship"), tmp_path, changed) == []


@pytest.mark.parametrize(("load_per_core", "vet_owed"), [("0", False), ("1000000", True)])
def test_a_build_the_load_gate_refuses_counts_for_the_vet_of_the_same_push(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, load_per_core: str, vet_owed: bool) -> None:
    module, changed = touched_module(tmp_path, monkeypatch)
    monkeypatch.setenv("CLAUDE_LONG_RUNNING_LANE", "lane-a")
    monkeypatch.setenv(LOAD_PER_CORE, load_per_core)

    note_attempted_checks(event(tmp_path, f"cd {module} && go build ./a ./b ./c"))

    assert go_commands(event(tmp_path, "ccx vcs ship"), tmp_path, changed) == ([f"cd {module} && go vet ./a ./b ./c"] if vet_owed else [])
