from __future__ import annotations

import functools
import importlib
from dataclasses import replace
from pathlib import Path

import pytest
from captain_hook import Allow, Input
from captain_hook.app import _state
from captain_hook.testing import helpers

from hooks.tests.rulings_consumers import MIGRATED

HOOKS = Path(__file__).parents[1] / "capt-hook" / "hooks"
MODULES = tuple(importlib.import_module(f"hooks.{path.stem}") for path in sorted(HOOKS.glob("*.py")))
UNGATED = {
    "compaction_handoff.activate_on_skill": "loading the long-running skill is how a session joins a drive",
    "compaction_handoff.activate_on_command": "a typed /long-running command is how a session joins a drive",
    "compaction_handoff.arm_communicator": "`lr owner start` is how the owner's window becomes the communicator",
    "compaction_handoff.track_plan": "a plan written before the drive starts is the plan its start binds to",
    "compaction_handoff.record_model": "session start carries the model id once, before any binding, and the compaction threshold reads it",
    "nudges.deliver_nudge": "it delivers only what gated hooks queued, and compaction_handoff imports it, so it cannot import the gate",
}


def label(entry) -> str:
    return f"{Path(str(entry.source_file)).stem}.{entry.handler.__name__ if entry.handler else (entry.spec.message or entry.name)[:42]}"


def pack_hooks() -> list:
    return [entry for entry in _state.hooks if Path(str(entry.source_file)).parent == HOOKS]


def unbound(entry) -> list[Input]:
    return [replace(inp, env=None, state=None) for inp in (*(entry.spec.tests or ()), *MIGRATED.get(label(entry), ())) if isinstance(inp, Input)] or [Input()]


def fire(entry, inp: Input, patch: pytest.MonkeyPatch) -> tuple[object, bool]:
    results, ran = [], []
    handler = entry.handler and functools.wraps(entry.handler)(lambda evt: ran.append(True) or entry.handler(evt))
    patch.setattr(_state, "hooks", [replace(entry, handler=handler, spec=replace(entry.spec, tests={inp: Allow()}))])
    patch.setattr(helpers, "assert_result", lambda result, *_: results.append(result))
    helpers.run_inline_tests()
    return (results[0] if results else None), bool(ran)


def silent(result) -> bool:
    return result is None or result.action == "allow" and not (result.message or result.system_message or result.note)


def test_the_ungated_list_names_real_hooks():
    assert set(UNGATED) <= {label(entry) for entry in pack_hooks()}


@pytest.mark.parametrize("entry", pack_hooks(), ids=label)
def test_an_unbound_session_hears_nothing(entry):
    noisy = []
    for inp in unbound(entry):
        with pytest.MonkeyPatch.context() as patch:
            result, ran = fire(entry, inp, patch)
        if not silent(result):
            noisy.append(f"{result!r:.200} on {inp!r:.200}")
        if ran and label(entry) not in UNGATED:
            noisy.append(f"ran ungated on {inp!r:.200}")
    assert not noisy, "\n".join(noisy)


@pytest.mark.parametrize("name", sorted(UNGATED))
def test_each_ungated_hook_still_sees_an_unbound_session(name):
    entry = next(entry for entry in pack_hooks() if label(entry) == name)
    with pytest.MonkeyPatch.context() as patch:
        assert any(fire(entry, inp, patch)[1] for inp in unbound(entry)), UNGATED[name]
