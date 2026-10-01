from __future__ import annotations

from pathlib import Path
from types import ModuleType

from captain_hook.app import _state
from captain_hook.conditions import matches_conditions
from captain_hook.dispatch import execute_hook


def fire(module: ModuleType, evt) -> list:
    return [
        result
        for entry in _state.hooks
        if Path(str(entry.source_file)) == Path(module.__file__)
        and evt.event in entry.spec.events
        and matches_conditions(entry.spec, evt)
        and (result := execute_hook(entry, evt))
    ]
