"""Bun layer (single-binary TypeScript CLI/TUI): selection matrix, the bun_version
validator, render-throughs of the real templates, and the coupling guards that pin
the CI action majors, the .bun-version pin contract, and the release-bun caller
contract. All pure/offline."""

from __future__ import annotations

import datetime
import json

import pytest
from bootstrap import scaffold
from bootstrap.common import ScaffoldError
from bootstrap.manifest import FEATURES

DATE = datetime.date(2026, 6, 8)


def dests(layer, var_pairs, *, extras=None, features=None):
    r = scaffold.resolve(layer, extras or [], features if features is not None else [], var_pairs, DATE)
    return {item.dest for item in scaffold.select_files(r)}


def _real_plan(layer, var_pairs, *, features=None):
    r = scaffold.resolve(layer, [], features if features is not None else [], var_pairs, DATE)
    items = scaffold.select_files(r)
    return scaffold.render_plan(items, r, scaffold.read_template, scaffold.template_exists)


# --- selection matrix ---

# AGENTS.md, CLAUDE.md, .claude/settings.json, .mcp.json, and .claude/capt-hook.toml scaffold as
# cc-guides layout dirs (layout.toml + repo-local *.fragment.* pieces); shared across every layer.
FRAGMENT_DESTS = {
    ".claude/fragments/AGENTS.md/layout.toml",
    ".claude/fragments/AGENTS.md/demo-proj-development-guide.fragment.md",
    ".claude/fragments/AGENTS.md/demo-proj-style.fragment.md",
    ".claude/fragments/AGENTS.md/demo-proj-hook-style.fragment.md",
    ".claude/fragments/CLAUDE.md/layout.toml",
    ".claude/fragments/.claude/settings.json/layout.toml",
    ".claude/fragments/.claude/settings.json/settings-overrides.fragment.json",
    ".claude/fragments/.mcp.json/layout.toml",
    ".claude/fragments/.mcp.json/mcp-overrides.fragment.json",
    ".claude/fragments/.claude/capt-hook.toml/layout.toml",
}

BUN_DESTS = FRAGMENT_DESTS | {
    "STYLEGUIDE.md", "README.md", "CHANGELOG.md",
    ".claude/jj-config.toml", ".claude/hooks/STYLEGUIDE.md",
    ".github/workflows/guides.yml",
    ".gitignore", "LICENSE",
    "package.json", "tsconfig.json", ".bun-version",
    "src/index.ts", "tests/hello.test.ts",
    ".github/workflows/ci.yml",
}


def test_bun_selection_exact(bun_var_pairs):
    got = dests("bun", bun_var_pairs)
    assert got == BUN_DESTS
    assert not any("{{" in d for d in got)


def test_bun_release_feature_gates(bun_var_pairs):
    got = dests("bun", bun_var_pairs, features=["release"])
    # release adds the one-liner caller + the Releases AGENTS fragment: no goreleaser
    # config (goreleaser cannot build bun binaries) and no cask template (the shared
    # workflow synthesizes it, or the repo supplies one via the cask-template input).
    assert got == BUN_DESTS | {
        ".github/workflows/release.yml",
        ".claude/fragments/AGENTS.md/releases.fragment.md",
    }


def test_bun_silently_drops_python_features(bun_var_pairs):
    got = dests("bun", bun_var_pairs, features=["docs", "pypi"])
    assert got == BUN_DESTS


def test_bun_overrides_base_for_shared_dest(bun_var_pairs):
    r = scaffold.resolve("bun", [], [], bun_var_pairs, DATE)
    items = {item.dest: item for item in scaffold.select_files(r)}
    assert items[".claude/fragments/AGENTS.md/layout.toml"].src == "bun/claude/fragments/AGENTS.md/layout.toml"
    assert items["README.md"].src == "bun/README.md"
    assert items["STYLEGUIDE.md"].src == "bun/STYLEGUIDE.md"
    assert (
        items[".claude/fragments/.claude/settings.json/layout.toml"].src
        == "bun/claude/fragments/settings.json/layout.toml"
    )
    assert (
        items[".claude/fragments/.claude/capt-hook.toml/layout.toml"].src
        == "bun/claude/fragments/capt-hook.toml/layout.toml"
    )
    # bun ships no .mcp.json variant — the base layout (empty server map) serves it.
    assert items[".claude/fragments/.mcp.json/layout.toml"].src == "base/claude/fragments/mcp.json/layout.toml"


# --- vars: validation ---

@pytest.mark.parametrize("version", ["1", "1.3", "v1.3.14", "latest", "1.3.14-canary.1"])
def test_bad_bun_version(bun_var_pairs, version):
    pairs = [p for p in bun_var_pairs if not p.startswith("BUN_VERSION=")]
    with pytest.raises(ScaffoldError):
        scaffold.resolve("bun", [], [], pairs + [f"BUN_VERSION={version}"], DATE)


def test_release_feature_spans_go_swift_bun():
    # The bun layer extended the release span; this pins all three (the old two-tuple
    # assertion lived in test_swift.py and moved here in the same change).
    release = next(f for f in FEATURES if f.name == "release")
    assert release.layers == ("go", "swift", "bun")
    assert release.default is False


# --- render-throughs of the real templates ---
# A passing _real_plan already proves zero leftover {{...}} tokens: render_plan
# raises on any unrendered section or placeholder.

def test_bun_package_json_renders(bun_var_pairs):
    plan, notices = _real_plan("bun", bun_var_pairs)
    pkg = json.loads(plan["package.json"])
    assert pkg["name"] == "demo-proj"
    assert pkg["description"] == "A demo project."
    assert pkg["license"] == "MIT"
    assert pkg["private"] is True
    assert pkg["type"] == "module"
    assert pkg["scripts"] == {
        "start": "bun run src/index.ts",
        "typecheck": "tsc --noEmit",
        "test": "bun test",
    }
    assert "@types/bun" in pkg["devDependencies"]
    assert "typescript" in pkg["devDependencies"]
    assert notices == []


def test_bun_version_file_renders(bun_var_pairs):
    plan, _ = _real_plan("bun", bun_var_pairs)
    assert plan[".bun-version"].strip() == "1.3.14"


def test_bun_ci_coupling(templates_dir):
    ci = (templates_dir / "bun/github/workflows/ci.yml").read_text()
    assert "oven-sh/setup-bun@v2" in ci
    # the .bun-version file is the pin contract — never a floating `bun-version: latest`.
    assert "bun-version-file: .bun-version" in ci
    assert "bun-version: latest" not in ci
    assert "actions/checkout@v7" in ci
    assert "bun install --frozen-lockfile" in ci


def test_bun_release_workflow_uses_reusable_workflow(bun_var_pairs):
    plan, _ = _real_plan("bun", bun_var_pairs, features=["release"])
    release = plan[".github/workflows/release.yml"]
    assert "uses: janedoe/homebrew-tap/.github/workflows/release-bun.yml@bun-v1" in release
    assert "secrets: inherit" in release
    # zero-config contract: the caller passes no inputs
    assert "with:" not in release


def test_bun_agents_renders_directives_and_release(bun_var_pairs):
    plan, _ = _real_plan("bun", bun_var_pairs, features=["release"])
    layout = plan[".claude/fragments/AGENTS.md/layout.toml"]
    assert '"cc-skills:ask-before-assuming"' in layout  # cc-skills import, not inlined
    assert '"cc-skills:version-control"' in layout
    # release on -> the Releases fragment carries the bun release caller and is listed
    assert '"releases"' in layout
    assert "release-bun.yml@bun-v1" in plan[".claude/fragments/AGENTS.md/releases.fragment.md"]
    plan_off, _ = _real_plan("bun", bun_var_pairs, features=[])
    assert ".claude/fragments/AGENTS.md/releases.fragment.md" not in plan_off
    assert '"releases"' not in plan_off[".claude/fragments/AGENTS.md/layout.toml"]


def test_bun_readme_install_follows_release(bun_var_pairs):
    plan_on, _ = _real_plan("bun", bun_var_pairs, features=["release"])
    assert "brew install janedoe/tap/demo-proj" in plan_on["README.md"]
    plan_off, _ = _real_plan("bun", bun_var_pairs, features=[])
    assert "brew install" not in plan_off["README.md"]
    assert "bun start" in plan_off["README.md"]


def test_bun_gitignore_concat(bun_var_pairs):
    plan, _ = _real_plan("bun", bun_var_pairs)
    gitignore = plan[".gitignore"]
    assert ".DS_Store" in gitignore  # base fragment first
    assert "node_modules/" in gitignore
    assert "dist/" in gitignore
    # no swift/go fragment bleeding in
    assert ".build/" not in gitignore
    assert "/bin/" not in gitignore


def test_bun_settings_layout_imports_bun_variant(bun_var_pairs):
    # settings.json composes from pack fragments: the bun layout imports
    # settings-base + settings-bun (bun/tsc perms), never the go/python/swift variants.
    plan, _ = _real_plan("bun", bun_var_pairs)
    layout = plan[".claude/fragments/.claude/settings.json/layout.toml"]
    assert '"cc-skills:settings-base"' in layout
    assert '"cc-skills:settings-bun"' in layout
    assert '"cc-skills:settings-go"' not in layout
    assert '"cc-skills:settings-python"' not in layout
    assert '"cc-skills:settings-swift"' not in layout
