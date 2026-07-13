"""Swift layers (swift = SPM package/CLI, swift-app = synced-folder Xcode app):
selection matrix, validators, derived vars, render-throughs of the real
templates, and the coupling guards that pin runner images, action majors, and
the release-swift caller contract. All pure/offline."""

from __future__ import annotations

import datetime
import json
import re

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

# AGENTS.md, CLAUDE.md, and .claude/settings.json scaffold as cc-guides layout dirs
# (layout.toml + repo-local *.fragment.* pieces); shared across every layer.
FRAGMENT_DESTS = {
    ".claude/fragments/AGENTS.md/layout.toml",
    ".claude/fragments/AGENTS.md/demo-proj-development-guide.fragment.md",
    ".claude/fragments/AGENTS.md/demo-proj-style.fragment.md",
    ".claude/fragments/AGENTS.md/demo-proj-hook-style.fragment.md",
    ".claude/fragments/CLAUDE.md/layout.toml",
    ".claude/fragments/.claude/settings.json/layout.toml",
    ".claude/fragments/.claude/settings.json/settings-overrides.fragment.json",
}

SWIFT_DESTS = FRAGMENT_DESTS | {
    "STYLEGUIDE.md", "README.md", "CHANGELOG.md",
    ".mcp.json", ".claude/jj-config.toml", ".claude/hooks/packs.toml", ".claude/hooks/STYLEGUIDE.md",
    ".claude/skills/xcodebuildmcp-cli/SKILL.md",
    ".github/workflows/guides.yml",
    ".gitignore", "LICENSE",
    "Package.swift",
    "Sources/DemoProj/Hello.swift", "Sources/demo-proj/Main.swift",
    "Tests/DemoProjTests/HelloTests.swift",
    ".swiftformat", ".swiftlint.yml", ".pre-commit-config.yaml",
    ".github/workflows/ci.yml",
}

SWIFT_APP_DESTS = FRAGMENT_DESTS | {
    "STYLEGUIDE.md", "README.md", "CHANGELOG.md",
    ".mcp.json", ".claude/jj-config.toml", ".claude/hooks/packs.toml", ".claude/hooks/STYLEGUIDE.md",
    ".claude/skills/xcodebuildmcp-cli/SKILL.md",
    ".github/workflows/guides.yml",
    ".gitignore", "LICENSE",
    "demo-proj.xcodeproj/project.pbxproj",
    "demo-proj.xcodeproj/xcshareddata/xcschemes/demo-proj.xcscheme",
    "demo-proj/App/DemoProjApp.swift", "demo-proj/App/ContentView.swift",
    "demo-proj/Assets.xcassets/Contents.json",
    "demo-proj/Assets.xcassets/AccentColor.colorset/Contents.json",
    "demo-proj/Assets.xcassets/AppIcon.appiconset/Contents.json",
    "demo-projTests/ScaffoldSmokeTests.swift",
    ".swiftformat", ".swiftlint.yml", ".pre-commit-config.yaml",
    ".github/workflows/ci.yml",
}


def test_swift_selection_exact(swift_var_pairs):
    got = dests("swift", swift_var_pairs)
    assert got == SWIFT_DESTS
    assert not any("{{" in d for d in got)


def test_swift_app_selection_exact(swift_app_var_pairs):
    got = dests("swift-app", swift_app_var_pairs)
    assert got == SWIFT_APP_DESTS
    assert not any("{{" in d for d in got)


def test_swift_release_feature_gates(swift_var_pairs):
    got = dests("swift", swift_var_pairs, features=["release"])
    # release adds the one-liner caller + the Releases AGENTS fragment: no goreleaser
    # config (goreleaser cannot build Swift) and no cask template (the shared workflow
    # synthesizes it).
    assert got == SWIFT_DESTS | {
        ".github/workflows/release.yml",
        ".claude/fragments/AGENTS.md/releases.fragment.md",
    }


def test_swift_app_silently_drops_release(swift_app_var_pairs):
    r = scaffold.resolve("swift-app", [], ["release"], swift_app_var_pairs, DATE)
    assert r.features == ()
    assert dests("swift-app", swift_app_var_pairs, features=["release"]) == SWIFT_APP_DESTS


def test_swift_silently_drops_python_features(swift_var_pairs):
    got = dests("swift", swift_var_pairs, features=["docs", "pypi"])
    assert got == SWIFT_DESTS


def test_swift_overrides_base_for_shared_dest(swift_var_pairs):
    r = scaffold.resolve("swift", [], [], swift_var_pairs, DATE)
    items = {item.dest: item for item in scaffold.select_files(r)}
    assert items[".claude/fragments/AGENTS.md/layout.toml"].src == "swift/claude/fragments/AGENTS.md/layout.toml"
    assert items["README.md"].src == "swift/README.md"
    assert items[".mcp.json"].src == "swift/mcp.json"
    assert items[".claude/hooks/packs.toml"].src == "swift/claude/hooks/packs.toml"


def test_swift_app_shares_swift_srcs(swift_app_var_pairs):
    # The language-level files are written once under templates/swift/ and
    # consumed by both layers — swift-app must not fork its own copies.
    r = scaffold.resolve("swift-app", [], [], swift_app_var_pairs, DATE)
    items = {item.dest: item for item in scaffold.select_files(r)}
    for dest in ("STYLEGUIDE.md", ".mcp.json", ".claude/fragments/.claude/settings.json/layout.toml",
                 ".claude/hooks/packs.toml", ".swiftformat", ".swiftlint.yml",
                 ".pre-commit-config.yaml", ".claude/skills/xcodebuildmcp-cli/SKILL.md"):
        assert items[dest].src.startswith("swift/"), f"{dest} forked from {items[dest].src}"
    # AGENTS prose is app-specific, so swift-app ships its own AGENTS layout dir
    assert items[".claude/fragments/AGENTS.md/layout.toml"].src == "swift-app/claude/fragments/AGENTS.md/layout.toml"
    assert items[".github/workflows/ci.yml"].src == "swift-app/github/workflows/ci.yml"


# --- vars: validation + derivation ---

def test_bundle_id_derived(swift_app_var_pairs):
    r = scaffold.resolve("swift-app", [], [], swift_app_var_pairs, DATE)
    assert r.variables["BUNDLE_ID"] == "com.janedoe.demo-proj"


def test_bundle_id_absent_without_prefix(swift_var_pairs):
    assert "BUNDLE_ID" not in scaffold.resolve("swift", [], [], swift_var_pairs, DATE).variables


@pytest.mark.parametrize("version", ["6", "6.x", "v6.2", "6.2.1"])
def test_bad_swift_tools_version(swift_var_pairs, version):
    pairs = [p for p in swift_var_pairs if not p.startswith("SWIFT_TOOLS_VERSION=")]
    with pytest.raises(ScaffoldError):
        scaffold.resolve("swift", [], [], pairs + [f"SWIFT_TOOLS_VERSION={version}"], DATE)


@pytest.mark.parametrize("module", ["demo-proj", "Demo Proj", "1Demo"])
def test_bad_module_name(swift_var_pairs, module):
    pairs = [p for p in swift_var_pairs if not p.startswith("MODULE_NAME=")]
    with pytest.raises(ScaffoldError):
        scaffold.resolve("swift", [], [], pairs + [f"MODULE_NAME={module}"], DATE)


def test_module_name_must_differ_from_project_name(swift_var_pairs):
    pairs = [p for p in swift_var_pairs if not p.startswith("MODULE_NAME=")]
    with pytest.raises(ScaffoldError):
        scaffold.resolve("swift", [], [], pairs + ["MODULE_NAME=demo-proj", "PROJECT_NAME=demo-proj"], DATE)


def test_module_name_case_only_difference_rejected(swift_var_pairs):
    # Sources/Demoproj/ and Sources/demoproj/ are ONE directory on macOS's
    # case-insensitive APFS — a case-only difference breaks `swift build` with
    # the exact confusing error the guard exists to prevent.
    pairs = [p for p in swift_var_pairs if not p.startswith(("MODULE_NAME=", "PROJECT_NAME="))]
    with pytest.raises(ScaffoldError):
        scaffold.resolve("swift", [], [], pairs + ["MODULE_NAME=Demoproj", "PROJECT_NAME=demoproj"], DATE)


def test_swift_app_project_name_must_be_bundle_id_safe(swift_app_var_pairs):
    # CFBundleIdentifier forbids underscores; the failure would otherwise defer
    # to App ID registration, long after scaffold + verify both pass.
    pairs = [p for p in swift_app_var_pairs if not p.startswith("PROJECT_NAME=")]
    with pytest.raises(ScaffoldError):
        scaffold.resolve("swift-app", [], [], pairs + ["PROJECT_NAME=my_app"], DATE)
    # ...but the plain swift layer has no bundle id, so underscores stay legal there.
    swift_pairs = [p for p in swift_app_var_pairs if not p.startswith(("PROJECT_NAME=", "BUNDLE_ID_PREFIX=", "IOS_DEPLOYMENT_TARGET="))]
    r = scaffold.resolve("swift", [], [], swift_pairs + ["PROJECT_NAME=my_app", "SWIFT_TOOLS_VERSION=6.2"], DATE)
    assert r.variables["PROJECT_NAME"] == "my_app"


@pytest.mark.parametrize("prefix", ["com", "1com.x", "com.", ".com", "com._x"])
def test_bad_bundle_id_prefix(swift_app_var_pairs, prefix):
    pairs = [p for p in swift_app_var_pairs if not p.startswith("BUNDLE_ID_PREFIX=")]
    with pytest.raises(ScaffoldError):
        scaffold.resolve("swift-app", [], [], pairs + [f"BUNDLE_ID_PREFIX={prefix}"], DATE)


@pytest.mark.parametrize("prefix", ["com.1password", "com.37signals", "io.agile-bits"])
def test_digit_leading_org_labels_are_valid_prefixes(swift_app_var_pairs, prefix):
    # Real reverse-DNS prefixes have digit-leading org labels (1password.com);
    # only the TLD label stays letter-first.
    pairs = [p for p in swift_app_var_pairs if not p.startswith("BUNDLE_ID_PREFIX=")]
    r = scaffold.resolve("swift-app", [], [], pairs + [f"BUNDLE_ID_PREFIX={prefix}"], DATE)
    assert r.variables["BUNDLE_ID"] == f"{prefix}.demo-proj"


@pytest.mark.parametrize("version", ["ios26", "26.0.1", ""])
def test_bad_ios_deployment_target(swift_app_var_pairs, version):
    pairs = [p for p in swift_app_var_pairs if not p.startswith("IOS_DEPLOYMENT_TARGET=")]
    with pytest.raises(ScaffoldError):
        scaffold.resolve("swift-app", [], [], pairs + [f"IOS_DEPLOYMENT_TARGET={version}"], DATE)


def test_release_feature_spans_go_and_swift():
    release = next(f for f in FEATURES if f.name == "release")
    assert release.layers == ("go", "swift")
    assert release.default is False


# --- render-throughs of the real templates ---
# A passing _real_plan already proves zero leftover {{...}} tokens: render_plan
# raises on any unrendered section or placeholder.

def test_swift_package_manifest_renders(swift_var_pairs):
    plan, notices = _real_plan("swift", swift_var_pairs)
    manifest = plan["Package.swift"]
    assert "// swift-tools-version: 6.2" in manifest
    assert '.library(name: "DemoProj", targets: ["DemoProj"])' in manifest
    assert '.executable(name: "demo-proj", targets: ["demo-proj"])' in manifest
    assert '.testTarget(name: "DemoProjTests", dependencies: ["DemoProj"])' in manifest
    assert notices == []


def test_swift_sources_render(swift_var_pairs):
    plan, _ = _real_plan("swift", swift_var_pairs)
    assert "import DemoProj" in plan["Sources/demo-proj/Main.swift"]
    assert 'commandName: "demo-proj"' in plan["Sources/demo-proj/Main.swift"]
    assert "@testable import DemoProj" in plan["Tests/DemoProjTests/HelloTests.swift"]
    assert "import Testing" in plan["Tests/DemoProjTests/HelloTests.swift"]


def test_swift_pbxproj_invariants(swift_app_var_pairs):
    plan, _ = _real_plan("swift-app", swift_app_var_pairs)
    pbxproj = plan["demo-proj.xcodeproj/project.pbxproj"]
    assert "objectVersion = 77;" in pbxproj
    assert "fileSystemSynchronizedGroups" in pbxproj
    assert "0000000000000000000000B1" in pbxproj  # stable synthetic UUIDs
    assert 'PRODUCT_BUNDLE_IDENTIFIER = "com.janedoe.demo-proj";' in pbxproj
    assert 'PRODUCT_BUNDLE_IDENTIFIER = "com.janedoe.demo-projTests";' in pbxproj
    assert 'PRODUCT_MODULE_NAME = "DemoProj";' in pbxproj
    assert "IPHONEOS_DEPLOYMENT_TARGET = 26.0;" in pbxproj
    assert "SWIFT_STRICT_CONCURRENCY = complete;" in pbxproj
    assert "GENERATE_INFOPLIST_FILE = YES;" in pbxproj
    # never ship a team id or the room-scan SPM plumbing
    assert "DEVELOPMENT_TEAM" not in pbxproj
    assert "XCLocalSwiftPackageReference" not in pbxproj
    assert "INFOPLIST_FILE" not in pbxproj.replace("GENERATE_INFOPLIST_FILE", "")


def test_swift_xcscheme_matches_pbxproj_targets(swift_app_var_pairs):
    # The scheme's BlueprintIdentifiers must equal the pbxproj target UUIDs —
    # a mismatch breaks Xcode silently (no scheme can build).
    plan, _ = _real_plan("swift-app", swift_app_var_pairs)
    pbxproj = plan["demo-proj.xcodeproj/project.pbxproj"]
    scheme = plan["demo-proj.xcodeproj/xcshareddata/xcschemes/demo-proj.xcscheme"]
    for blueprint in re.findall(r'BlueprintIdentifier = "([0-9A-F]+)"', scheme):
        assert re.search(rf"{blueprint} /\* demo-proj(Tests)? \*/ = {{\s*isa = PBXNativeTarget", pbxproj), blueprint
    assert 'ReferencedContainer = "container:demo-proj.xcodeproj"' in scheme


def test_swift_app_smoke_test_imports_module(swift_app_var_pairs):
    plan, _ = _real_plan("swift-app", swift_app_var_pairs)
    smoke = plan["demo-projTests/ScaffoldSmokeTests.swift"]
    assert "@testable import DemoProj" in smoke
    assert "DemoProjApp.self" in smoke
    assert "struct DemoProjApp: App" in plan["demo-proj/App/DemoProjApp.swift"]


def test_swift_agents_renders_directives_and_release(swift_var_pairs):
    plan, _ = _real_plan("swift", swift_var_pairs, features=["release"])
    layout = plan[".claude/fragments/AGENTS.md/layout.toml"]
    style = plan[".claude/fragments/AGENTS.md/demo-proj-style.fragment.md"]
    assert '"cc-skills:ask-before-assuming"' in layout  # cc-skills import, not inlined
    assert '"cc-skills:version-control"' in layout
    assert "xcodebuildmcp-cli" in style  # the XcodeBuildMCP rule lives in the style fragment
    # release on -> the Releases fragment carries the swift release caller and is listed
    assert '"releases"' in layout
    assert "release-swift.yml@swift-v1" in plan[".claude/fragments/AGENTS.md/releases.fragment.md"]
    plan_off, _ = _real_plan("swift", swift_var_pairs, features=[])
    assert ".claude/fragments/AGENTS.md/releases.fragment.md" not in plan_off
    assert '"releases"' not in plan_off[".claude/fragments/AGENTS.md/layout.toml"]


def test_swift_readme_install_follows_release(swift_var_pairs):
    plan_on, _ = _real_plan("swift", swift_var_pairs, features=["release"])
    assert "brew install janedoe/tap/demo-proj" in plan_on["README.md"]
    plan_off, _ = _real_plan("swift", swift_var_pairs, features=[])
    assert "brew install" not in plan_off["README.md"]
    assert "swift run demo-proj hello" in plan_off["README.md"]


def test_swift_app_agents_renders(swift_app_var_pairs):
    plan, notices = _real_plan("swift-app", swift_app_var_pairs)
    dev = plan[".claude/fragments/AGENTS.md/demo-proj-development-guide.fragment.md"]
    style = plan[".claude/fragments/AGENTS.md/demo-proj-style.fragment.md"]
    assert "file-system-synchronized" in dev
    assert "no\n`.pbxproj` edit" in dev or "no `.pbxproj` edit" in dev
    assert "platform=iOS Simulator,name=iPhone 17" in style
    assert 'subsystem == "com.janedoe.demo-proj"' in style
    assert notices == []


# --- coupling guards (template text pinned against convention drift) ---

def test_swift_ci_runner_and_actions(templates_dir):
    # macos-26 is load-bearing: the image ships Xcode 26.x / Swift 6.2, while
    # macos-latest still resolves to macOS 15 / Swift 6.0. When GitHub retires
    # the label this test fails loudly and the bump is deliberate.
    package_ci = (templates_dir / "swift/github/workflows/ci.yml").read_text()
    app_ci = (templates_dir / "swift-app/github/workflows/ci.yml").read_text()
    for ci in (package_ci, app_ci):
        assert "runs-on: macos-26" in ci
        assert "actions/checkout@v7" in ci
        assert "runs-on: macos-latest" not in ci
        # macos-26 ships swiftformat but NOT swiftlint (verified live) — the
        # install-if-missing guard is load-bearing, not belt-and-braces.
        assert "command -v swiftlint >/dev/null || brew install swiftlint" in ci
        assert "command -v swiftformat >/dev/null || brew install swiftformat" in ci
    assert "actions/cache@v5" in package_ci
    assert "hashFiles('Package.resolved')" in package_ci


def test_swift_app_ci_tests_on_simulator(templates_dir):
    app_ci = (templates_dir / "swift-app/github/workflows/ci.yml").read_text()
    assert "xcodebuild test" in app_ci
    assert "platform=iOS Simulator,name=iPhone 17" in app_ci
    assert "CODE_SIGNING_ALLOWED=NO" in app_ci


def test_swift_release_workflow_uses_reusable_workflow(swift_var_pairs):
    plan, _ = _real_plan("swift", swift_var_pairs, features=["release"])
    release = plan[".github/workflows/release.yml"]
    assert "uses: janedoe/homebrew-tap/.github/workflows/release-swift.yml@swift-v1" in release
    assert "secrets: inherit" in release
    # zero-config contract: the caller passes no inputs
    assert "with:" not in release


def test_swift_packs_toml_no_swift_pack(templates_dir):
    swift_packs = (templates_dir / "swift/claude/hooks/packs.toml").read_text()
    assert "[packs.fixes]" in swift_packs
    assert "[packs.general]" in swift_packs
    assert "[packs.steering]" in swift_packs
    assert "[packs.swift]" not in swift_packs
    assert "[packs.ccx]" in swift_packs  # ccx + cc-present pin repo-scoped now, alongside the plugin attach
    assert "[packs.cc-present]" in swift_packs


def test_swift_mcp_json_overrides_with_xcodebuildmcp(swift_var_pairs):
    plan, _ = _real_plan("swift", swift_var_pairs)
    mcp = json.loads(plan[".mcp.json"])
    assert mcp["mcpServers"]["xcodebuildmcp"] == {"command": "xcodebuildmcp", "args": ["mcp"]}
    assert "semble" not in mcp["mcpServers"]


def test_swift_settings_layout_imports_swift_variant(swift_var_pairs):
    # settings.json composes from pack fragments: the swift layout imports
    # settings-base + settings-swift (swift perms; no ty/go), never the go/python
    # variants. The composed content is verified end-to-end against `cc-guides render`.
    plan, _ = _real_plan("swift", swift_var_pairs)
    layout = plan[".claude/fragments/.claude/settings.json/layout.toml"]
    assert '"cc-skills:settings-base"' in layout
    assert '"cc-skills:settings-swift"' in layout
    assert '"cc-skills:settings-go"' not in layout
    assert '"cc-skills:settings-python"' not in layout


def test_swift_precommit_local_system_hooks(templates_dir):
    # Deliberately local system hooks against the brew binaries: the upstream
    # SwiftFormat/SwiftLint pre-commit hooks build from source via SPM
    # (minutes-long). Guard against someone "upgrading" back to them.
    config = (templates_dir / "swift/pre-commit-config.yaml").read_text()
    assert "repo: local" in config
    assert config.count("\n        language: system\n") == 2
    assert "id: swiftformat" in config
    assert "id: swiftlint" in config
    assert "github.com/nicklockwood/SwiftFormat" not in config
    assert "github.com/realm/SwiftLint" not in config


def test_swift_gitignore_concat(swift_var_pairs, swift_app_var_pairs):
    for layer, pairs in (("swift", swift_var_pairs), ("swift-app", swift_app_var_pairs)):
        plan, _ = _real_plan(layer, pairs)
        gitignore = plan[".gitignore"]
        assert ".DS_Store" in gitignore  # base fragment first
        for entry in (".build/", ".swiftpm/", "DerivedData/", ".xcodebuildmcp/"):
            assert entry in gitignore, f"{entry} missing in {layer}"
        assert "/bin/" not in gitignore  # no go fragment bleeding in
