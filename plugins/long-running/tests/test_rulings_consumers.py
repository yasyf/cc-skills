from __future__ import annotations

import importlib
import json
import shutil
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest
from captain_hook.app import _state
from captain_hook.testing.helpers import run_inline_tests

from hooks import compaction_handoff
from hooks.tests.rulings_consumers import MIGRATED, MIGRATED_AT

MODULES = tuple(importlib.import_module(f"hooks.{name}") for name in (
    "board_text_gate", "refusal_kinds_need_owner_answer", "owner_facing", "register_capture",
    "register_gates", "register_injection", "ruling_judge", "settled_questions", "ruling_misread",
))


def cases():
    result = []
    for name, migrated in MIGRATED.items():
        module, handler = name.split(".")
        entries = [entry for entry in _state.hooks if entry.handler and entry.handler.__module__ == f"hooks.{module}" and entry.handler.__name__ == handler]
        assert len(entries) == 1, name
        entry = entries[0]
        assert not set(migrated) & set(entry.spec.tests)
        for inp, expected in migrated.items():
            result.append(pytest.param(entry, inp, expected, id=f"{name}:{inp!r}"))
    assert result
    return result


@pytest.mark.parametrize("entry,inp,expected", cases())
def test_rulings_consumer_outcomes(entry, inp, expected, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    fixtures = Path(compaction_handoff.__file__).parent / "tests" / "fixtures"
    env = inp.env or {}
    configured = env.get("CLAUDE_CONFIG_DIR")
    if configured in {str(fixtures / "register-config"), str(fixtures / "claude-config")}:
        copied = tmp_path / "config"
        shutil.copytree(configured, copied)
        for registry in (copied / "long-running" / "drives").glob("*.json"):
            entry_data = json.loads(registry.read_text())
            entry_data["state_dir"] = str(tmp_path / "state" / Path(entry_data["state_dir"]).name)
            registry.write_text(json.dumps(entry_data))
        inp = replace(inp, env=env | {"CLAUDE_CONFIG_DIR": str(copied)})

    def capability(cwd, *args, evt, stdin="", timeout=compaction_handoff.CCN_TIMEOUT_SECONDS):
        argv = [sys.executable, str(compaction_handoff.script("rulings.py")), *args, "--repo", cwd]
        assert any(tuple(argv[:len(prefix)]) == prefix for key in inp.commands for prefix in [tuple(key.split())])
        return evt.ctx.call_cli(argv, input=stdin, timeout=timeout)

    for module in (*MODULES, compaction_handoff):
        if hasattr(module, "rulings"):
            monkeypatch.setattr(module, "rulings", capability)
    if entry.handler.__module__ == "hooks.board_text_gate":
        captured = MIGRATED_AT[f"board_text_gate.{entry.handler.__name__}"]

        class FixtureDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return captured.astimezone(tz) if tz is not None else captured.replace(tzinfo=None)

        monkeypatch.setattr(next(module for module in MODULES if module.__name__ == "hooks.board_text_gate"), "datetime", FixtureDateTime)
    isolated = replace(entry, spec=replace(entry.spec, tests={inp: expected}))
    monkeypatch.setattr(_state, "hooks", [isolated])
    results = run_inline_tests()
    assert len(results) == 1
    assert results[0][2], results[0]
