"""Guard: the cc-guides settings fragments must never re-grow hook wiring."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
GUIDES_JSON = REPO_ROOT / "plugin" / "guides" / "json"
LOCK = REPO_ROOT / ".claude" / "fragments" / "cc-guides.lock"
SETTINGS = REPO_ROOT / ".claude" / "settings.json"


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


def test_settings_base_fragment_enables_captain_hook():
    """The rendered-settings guard can't see fragment regressions until the next render —
    this one reds the PR that removes captain-hook from the base fragment before the fleet
    cron propagates it."""
    base = json.loads((GUIDES_JSON / "settings-base.json").read_text())
    assert base["enabledPlugins"]["captain-hook@captain-hook"] is True, (
        "settings-base fragment must enable captain-hook@captain-hook"
    )
    assert (
        base["extraKnownMarketplaces"]["captain-hook"]["source"]["repo"]
        == "yasyf/captain-hook"
    ), "settings-base fragment must register the captain-hook marketplace at yasyf/captain-hook"


def test_settings_base_fragment_disables_artifact():
    """Fragment-level guard: settings-base must hide the built-in Artifact tool
    (disableArtifact: true) so presentation flows through chat or a cc-present live board.
    Reds the PR that drops the key before the next render can propagate it."""
    base = json.loads((GUIDES_JSON / "settings-base.json").read_text())
    assert base["disableArtifact"] is True, (
        "settings-base fragment must set disableArtifact: true"
    )


def test_settings_json_is_a_rendered_artifact():
    """cc-skills must render its own settings.json from the fragments it publishes —
    commit b7d2e86 showed what half-migration looks like."""
    lock = tomllib.loads(LOCK.read_text())
    assert ".claude/settings.json" in lock["artifacts"], (
        ".claude/settings.json must be a cc-guides-rendered artifact in the lock"
    )
    layout_dir = REPO_ROOT / ".claude" / "fragments" / ".claude" / "settings.json"
    assert (layout_dir / "layout.toml").is_file(), (
        "the settings.json layout.toml must exist on disk — lock membership alone "
        "doesn't prove the layout survived"
    )
    assert (layout_dir / "settings-overrides.fragment.json").is_file(), (
        "the settings-overrides overlay fragment must exist on disk next to the layout"
    )


def test_captain_hook_plugin_stays_enabled():
    """the captain-hook plugin carries ALL hook registration (12 events incl.
    PermissionRequest — the fixes-pack subagent auto-approve rides on it);
    disabling it silently kills every capt-hook hook in this repo."""
    settings = json.loads(SETTINGS.read_text())
    assert settings["enabledPlugins"]["captain-hook@captain-hook"] is True, (
        "captain-hook@captain-hook must stay enabled"
    )
    assert "captain-hook" in settings["extraKnownMarketplaces"], (
        "captain-hook marketplace must stay registered"
    )
    assert (
        settings["extraKnownMarketplaces"]["captain-hook"]["source"]["repo"]
        == "yasyf/captain-hook"
    ), "captain-hook marketplace must point at yasyf/captain-hook"
