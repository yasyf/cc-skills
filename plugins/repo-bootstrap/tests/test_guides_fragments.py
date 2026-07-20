"""Guards for shared cc-guides JSON fragments."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
GUIDES_JSON = REPO_ROOT / "plugin" / "guides" / "json"
TEMPLATES = (
    REPO_ROOT / "plugins" / "repo-bootstrap" / "skills" / "repo-bootstrap" / "templates"
)
LOCK = REPO_ROOT / ".claude" / "fragments" / "cc-guides.lock"
SETTINGS = REPO_ROOT / ".claude" / "settings.json"


@pytest.mark.parametrize(
    "fragment",
    sorted(GUIDES_JSON.glob("*.json")),
    ids=lambda p: p.name,
)
def test_json_fragment_has_no_hook_wiring(fragment: Path):
    # Hook registration is plugin-canonical: since capt-hook 9.0.0 the captain-hook
    # plugin registers all 12 events via its shipped hooks.json, so the cc-guides
    # settings fragments must never carry a `hooks` key or a `capt-hook` command —
    # that would double-register every event.
    raw = fragment.read_text()
    data = json.loads(raw)  # every fragment must be valid JSON
    assert "hooks" not in data, f"{fragment.name} must not carry a hooks key"
    assert "capt-hook" not in raw, f"{fragment.name} must not reference capt-hook"


def test_mcp_fragments_are_exact():
    assert json.loads((GUIDES_JSON / "mcp-base.json").read_text()) == {"mcpServers": {}}
    assert json.loads((GUIDES_JSON / "mcp-swift.json").read_text()) == {
        "mcpServers": {
            "xcodebuildmcp": {
                "command": "xcodebuildmcp",
                "args": ["mcp"],
            }
        }
    }


def test_copy_once_mcp_templates_are_gone():
    assert not (TEMPLATES / "base" / "mcp.json").exists()
    assert not (TEMPLATES / "swift" / "mcp.json").exists()


def test_scaffold_and_json_guides_do_not_reference_semble():
    hits = []
    for root in (TEMPLATES, GUIDES_JSON):
        for path in root.rglob("*"):
            if path.is_file() and b"semble" in path.read_bytes():
                hits.append(str(path.relative_to(REPO_ROOT)))
    assert hits == []


def test_gitignore_base_fragment_keeps_claude_scratch_ignores():
    # Re-homed from the deleted templates/base/gitignore backstop: scratch and
    # worktree state must stay ignored fleet-wide or it reaches the index.
    body = (REPO_ROOT / "plugin" / "guides" / "gitignore" / "gitignore-base.gitignore").read_text()
    for pattern in (
        ".claude-scratch/",
        ".scratch/",
        ".claude/worktrees/",
        ".claude/settings.local.json",
    ):
        assert pattern in body, f"gitignore-base must keep {pattern}"


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


def test_settings_base_fragment_enables_cc_guides():
    """The cc-guides plugin ships the rendered-artifact guard pack; the fleet gets it
    through this fragment, so dropping either key un-guards every repo."""
    base = json.loads((GUIDES_JSON / "settings-base.json").read_text())
    assert base["enabledPlugins"]["cc-guides@cc-guides"] is True, (
        "settings-base fragment must enable cc-guides@cc-guides"
    )
    assert (
        base["extraKnownMarketplaces"]["cc-guides"]["source"]["repo"] == "yasyf/cc-guides"
    ), "settings-base fragment must register the cc-guides marketplace at yasyf/cc-guides"


def test_settings_base_fragment_disables_artifact():
    """Fragment-level guard: settings-base must hide the built-in Artifact tool
    (disableArtifact: true) so presentation flows through chat or a cc-present live board.
    Reds the PR that drops the key before the next render can propagate it."""
    base = json.loads((GUIDES_JSON / "settings-base.json").read_text())
    assert base["disableArtifact"] is True, (
        "settings-base fragment must set disableArtifact: true"
    )


def test_settings_base_fragment_marketplaces_auto_update():
    """Fragment-level guard: every marketplace entry in settings-base must set
    autoUpdate: true so clones stay fresh (ab68f3e). Reds the PR that drops the
    flag before the fleet cron propagates a stale render."""
    base = json.loads((GUIDES_JSON / "settings-base.json").read_text())
    for name, entry in base["extraKnownMarketplaces"].items():
        assert entry.get("autoUpdate") is True, (
            f"settings-base marketplace entry {name!r} must set autoUpdate: true"
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
