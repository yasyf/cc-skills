"""Guard: the cc-guides settings fragments must never re-grow hook wiring."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

GUIDES_JSON = Path(__file__).resolve().parents[3] / "plugin" / "guides" / "json"


@pytest.mark.parametrize(
    "fragment",
    sorted(GUIDES_JSON.glob("*.json")),
    ids=lambda p: p.name,
)
def test_settings_fragment_has_no_hook_wiring(fragment: Path):
    # Hook registration is plugin-canonical: since capt-hook 9.0.0 the captain-hook
    # plugin registers all 12 events via its shipped hooks.json, so the cc-guides
    # settings fragments must never carry a `hooks` key or a `capt-hook` command —
    # that would double-register every event.
    raw = fragment.read_text()
    data = json.loads(raw)  # every fragment must be valid JSON
    assert "hooks" not in data, f"{fragment.name} must not carry a hooks key"
    assert "capt-hook" not in raw, f"{fragment.name} must not reference capt-hook"
