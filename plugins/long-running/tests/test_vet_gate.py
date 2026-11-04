from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from cc_transcript.query import Session
from captain_hook.events import PreToolUseEvent
from captain_hook.testing.helpers import build_context

from hooks import vet_before_push
from hooks.vet_before_push import compiles, go_commands, record


def test_one_refused_line_counts_for_every_touched_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vet_before_push, "run_quietly", lambda argv, _cwd: subprocess.CompletedProcess(argv, 0, "/usr/local/go\n", ""))
    module = tmp_path / "go"
    changed = [module / name / f"{name}.go" for name in ("a", "b", "c")]
    for path in changed:
        path.parent.mkdir(parents=True)
        path.write_text("package x\n")
    (module / "go.mod").write_text("module x\n")
    transcript = tmp_path / "lane.jsonl"
    transcript.write_text("")

    def event(command: str) -> PreToolUseEvent:
        raw = {"session_id": "0123456789abcdef", "transcript_path": str(transcript), "cwd": str(tmp_path), "tool_name": "Bash", "tool_input": {"command": command}}
        return PreToolUseEvent(_raw=raw, ctx=build_context(transcript=Session.from_path(transcript), session_dir=tmp_path / "session"))

    assert (commands := go_commands(event("ccx vcs ship"), tmp_path, changed)) == [f"cd {module} && go build ./a ./b ./c && go vet ./a ./b ./c"]

    refused = event(commands[0])
    record(refused, [check for call in refused.command.calls() for check in compiles(refused, call)])

    assert go_commands(event("ccx vcs ship"), tmp_path, changed) == []
