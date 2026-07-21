"""Declarative manifest for the scaffolder — data only, no logic.

Adding a layer / feature / extra / var / file is a matter of appending a record
here; the engine in ``scaffold.py`` contains no per-file branching. ``src`` paths
must match the template tree under ``../templates`` exactly.
"""

from __future__ import annotations

from .common import DerivedVar, Feature, FileSpec, Layer, VarSpec

# Layer precedence: earlier layers are overridden by later ones at the same dest.
# swift (SPM package/CLI) and swift-app (synced-folder Xcode app) are siblings —
# expand_layers never activates both, so their relative precedence never fires.
LAYERS = (
    Layer("base"),
    Layer("python", implies=("base",)),
    Layer("go", implies=("base",)),
    Layer("swift", implies=("base",)),
    Layer("swift-app", implies=("base",)),
    Layer("bun", implies=("base",)),
)
LAYER_ORDER = tuple(layer.name for layer in LAYERS)

# Languages selectable via ``--secondary-layer``: each adds only its styleguide (at
# SECONDARY_CODE_ROOT) and an AGENTS.md pointer — never a second toolchain — so the
# files never clobber the primary layer's at the root. Add one by shipping its
# ``secondary_layer=`` FileSpecs (see FILES) and listing it here.
SECONDARY_LAYERS = ("python",)

# Optional features, each scoped to the layer(s) that offer it. Each maps to a
# {{#FEATURE_*}} section token and may gate whole files (see FILES below). A
# feature requested outside its layer is silently dropped (scaffold.resolve).
FEATURES = (
    Feature("docs", "FEATURE_DOCS", layers=("python",)),
    Feature("pypi", "FEATURE_PYPI", layers=("python",)),
    # maturin (PyO3 native wheels) is opt-in: only a native-extension repo wants it,
    # and it merely toggles a section inside the pypi-gated release-pypi.yml caller
    # (no extra files — the Rust crate is a recipe, like the go formula). default=False
    # keeps it out of the omitted-`--features` set so pure-Python scaffolds stay pure.
    Feature("maturin", "FEATURE_MATURIN", layers=("python",), default=False),
    # release is opt-in too: an omitted `--features` must not scaffold a release pipeline
    # (SKILL Phase 1 promises "release defaults off"). default=False makes that honest.
    # It spans go (goreleaser cask), swift (release-swift.yml cask), and bun
    # (release-bun.yml cask — a native-runner bun --compile matrix); swift-app is
    # deliberately absent — requesting release there is silently dropped, which IS the
    # "apps have no brew release" behavior (App Store/TestFlight is product work).
    Feature("release", "FEATURE_RELEASE", layers=("go", "swift", "bun"), default=False),
    # daemonkit (opt-in, go): scaffolds a detached-daemon binary (cmd/<name>d) on
    # github.com/yasyf/daemonkit plus the scripts/test.sh fork-bomb harness.
    Feature("daemonkit", "FEATURE_DAEMONKIT", layers=("go",), default=False),
    # helper-app wraps <name>d in a signed .app + cask (release-app.yml); widget
    # adds its appex. resolve drops both without daemonkit, widget without helper-app.
    Feature("helper-app", "HELPER_APP", layers=("go",), default=False),
    Feature("widget", "WIDGET", layers=("go",), default=False),
)

# Optional extra layers, selectable in any layer via --extras.
EXTRAS = ("superset", "env", "plugin")

_ALL_LAYERS = ("base", "python", "go", "swift", "swift-app", "bun")

VARS = (
    VarSpec("PROJECT_NAME", _ALL_LAYERS),
    VarSpec("DESCRIPTION", _ALL_LAYERS),
    VarSpec("AUTHOR_NAME", _ALL_LAYERS),
    VarSpec("AUTHOR_EMAIL", _ALL_LAYERS),
    VarSpec("GITHUB_USER", _ALL_LAYERS),
    VarSpec("LICENSE_ID", _ALL_LAYERS, validate="license_id"),
    # PACKAGE is validated before DIST_NAME to match the legacy check order.
    VarSpec("PACKAGE", ("python",), validate="identifier"),
    VarSpec("DIST_NAME", ("python",), validate="dist_name"),
    VarSpec("PYTHON_PIN", ("python",), validate="py_version"),
    VarSpec("PYTHON_MIN", ("python",), validate="py_version"),
    VarSpec("GO_VERSION", ("go",), validate="go_version"),
    # The importable module (UpperCamelCase), distinct from the executable/repo name —
    # mirrors python's DIST_NAME/PACKAGE split. Python's isidentifier() accepts Swift
    # module names exactly. Must differ from PROJECT_NAME (checked in scaffold.resolve):
    # duplicate SPM target names break `swift build` with a confusing error.
    VarSpec("MODULE_NAME", ("swift", "swift-app"), validate="identifier"),
    VarSpec("SWIFT_TOOLS_VERSION", ("swift",), validate="swift_tools_version"),
    VarSpec("BUNDLE_ID_PREFIX", ("swift-app",), validate="bundle_id_prefix"),
    VarSpec("IOS_DEPLOYMENT_TARGET", ("swift-app",), validate="ios_version"),
    # The bun toolchain pin, written verbatim into `.bun-version` — setup-bun's
    # `bun-version-file` and the release matrix both read it. Exact X.Y.Z: setup-bun
    # downloads an exact version, never a range or `latest`.
    VarSpec("BUN_VERSION", ("bun",), validate="bun_version"),
    # Tokens for the `plugin` extra's install-binary.sh (the canonical plugin
    # binary provisioner — see reference/go-ci-and-release.md § format: binary).
    # required_in is empty because extras are layer-independent; a missing token
    # still fails loudly via render_plan's unrendered-placeholder scan.
    VarSpec("BINARY_NAME", ()),
    VarSpec("RELEASE_REPO", ()),
    VarSpec("BREW_PACKAGE", ()),
    VarSpec("PLUGIN_NAME", ()),
    # pinned (target release = plugin.json version; the default) or latest
    # (releases/latest redirect — for plugins whose version isn't coupled to
    # binary releases). Drives the PINNED/LATEST sections, never a placeholder.
    VarSpec("BINARY_VERSION_MODE", (), validate="binary_version_mode"),
    # Repo-root-relative dir holding a --secondary-layer's code, e.g. plugin/hooks.
    # required_in is empty (a secondary layer is layer-independent); resolve() makes
    # it mandatory whenever --secondary-layer is set.
    VarSpec("SECONDARY_CODE_ROOT", (), validate="code_root"),
    # daemonkit ownership mode: client-spawn adds exact-build lazy start and idle
    # retirement; launchagent delegates restart ownership to launchd.
    VarSpec("LAUNCHD_MODE", (), validate="launchd_mode"),
)

DERIVED = (
    DerivedVar("REPO_URL", lambda v, now: f"https://github.com/{v['GITHUB_USER']}/{v['PROJECT_NAME']}"),
    DerivedVar("DOCS_URL", lambda v, now: f"https://{v['GITHUB_USER']}.github.io/{v['PROJECT_NAME']}/"),
    DerivedVar("YEAR", lambda v, now: str(now.year)),
    DerivedVar("PY_TARGET", lambda v, now: ("py" + v["PYTHON_MIN"].replace(".", "")) if v.get("PYTHON_MIN") else None),
    # Go module path, e.g. github.com/yasyf/demo. Only the go layer supplies a
    # version; derive it only when GO_VERSION is present so python/base stay clean.
    DerivedVar(
        "MODULE_PATH",
        lambda v, now: f"github.com/{v['GITHUB_USER']}/{v['PROJECT_NAME']}" if v.get("GO_VERSION") else None,
    ),
    # shields.io reads single dashes as the label/message/color separators, so a
    # license id with dashes (PolyForm-Noncommercial-1.0.0) must double them for
    # the static badge URL. The alt text keeps the readable single-dash form.
    DerivedVar(
        "LICENSE_BADGE",
        lambda v, now: v["LICENSE_ID"].replace("-", "--") if v.get("LICENSE_ID", "none") != "none" else None,
    ),
    # App bundle id, e.g. com.yasyf.room-scan (hyphens are legal in bundle ids).
    # Only swift-app supplies a prefix, so python/go/swift stay clean.
    DerivedVar(
        "BUNDLE_ID",
        lambda v, now: f"{v['BUNDLE_ID_PREFIX']}.{v['PROJECT_NAME']}" if v.get("BUNDLE_ID_PREFIX") else None,
    ),
)

FILES = (
    # --- base layer ---
    # cc-guides v3 artifacts (AGENTS.md, CLAUDE.md, settings.json, .mcp.json): scaffold
    # writes a `.claude/fragments/<target>/` layout dir; `cc-guides render` composes it.
    FileSpec(".claude/fragments/AGENTS.md/layout.toml", "base/claude/fragments/AGENTS.md/layout.toml", "base"),
    FileSpec(
        ".claude/fragments/AGENTS.md/{{PROJECT_NAME}}-development-guide.fragment.md",
        "base/claude/fragments/AGENTS.md/development-guide.fragment.md",
        "base",
    ),
    FileSpec(
        ".claude/fragments/AGENTS.md/{{PROJECT_NAME}}-style.fragment.md",
        "base/claude/fragments/AGENTS.md/style.fragment.md",
        "base",
    ),
    # capt-hook hooks are Python in every repo; the `## Hook Style` pointer ships in
    # every layer's AGENTS.md (each layout.toml lists {{PROJECT_NAME}}-hook-style).
    FileSpec(
        ".claude/fragments/AGENTS.md/{{PROJECT_NAME}}-hook-style.fragment.md",
        "base/claude/fragments/AGENTS.md/hook-style.fragment.md",
        "base",
    ),
    FileSpec(".claude/fragments/CLAUDE.md/layout.toml", "base/claude/fragments/CLAUDE.md/layout.toml", "base"),
    # settings.json layout dir lives at the doubly-nested .claude/fragments/.claude/
    # settings.json/ (the target IS .claude/settings.json). The
    # `settings-overrides.fragment.json` is a placeholder-free `{}` no-op merge the
    # repo fills in later; it ships once (base) and every layer's layout.toml
    # composes it after the pack fragments.
    FileSpec(
        ".claude/fragments/.claude/settings.json/layout.toml",
        "base/claude/fragments/settings.json/layout.toml",
        "base",
    ),
    FileSpec(
        ".claude/fragments/.claude/settings.json/settings-overrides.fragment.json",
        "base/claude/fragments/settings.json/settings-overrides.fragment.json",
        "base",
    ),
    FileSpec(
        ".claude/fragments/.mcp.json/layout.toml",
        "base/claude/fragments/mcp.json/layout.toml",
        "base",
    ),
    FileSpec(
        ".claude/fragments/.mcp.json/mcp-overrides.fragment.json",
        "base/claude/fragments/mcp.json/mcp-overrides.fragment.json",
        "base",
    ),
    FileSpec("STYLEGUIDE.md", "base/STYLEGUIDE.md", "base"),
    FileSpec("README.md", "base/README.md", "base"),
    FileSpec("CHANGELOG.md", "base/CHANGELOG.md", "base"),
    FileSpec(".claude/jj-config.toml", "base/claude/jj-config.toml", "base"),
    # The cc-guides caller stub: `check` on push/PR + `re-render` on release dispatch.
    FileSpec(".github/workflows/guides.yml", "base/github/workflows/guides.yml", "base"),
    # The style guide for the repo's `.claude/hooks/` Python — shipped in every layer.
    FileSpec(".claude/hooks/STYLEGUIDE.md", "base/claude/hooks/STYLEGUIDE.md", "base"),
    # .gitignore is a cc-guides artifact: the layout composes `cc-skills:gitignore-*`
    # (base + language variant + docs) then `gitignore-local` last, like settings.json.
    FileSpec(".claude/fragments/.gitignore/layout.toml", "base/claude/fragments/gitignore/layout.toml", "base"),
    FileSpec(
        ".claude/fragments/.gitignore/gitignore-local.fragment.gitignore",
        "base/claude/fragments/gitignore/gitignore-local.fragment.gitignore",
        "base",
    ),
    # synthesized base files (no single template src)
    FileSpec("LICENSE", None, "base", transform="license"),
    # --- python layer (overrides base where dest collides) ---
    FileSpec(".claude/fragments/AGENTS.md/layout.toml", "python/claude/fragments/AGENTS.md/layout.toml", "python"),
    FileSpec(
        ".claude/fragments/AGENTS.md/{{PROJECT_NAME}}-development-guide.fragment.md",
        "python/claude/fragments/AGENTS.md/development-guide.fragment.md",
        "python",
    ),
    FileSpec(
        ".claude/fragments/AGENTS.md/{{PROJECT_NAME}}-style.fragment.md",
        "python/claude/fragments/AGENTS.md/style.fragment.md",
        "python",
    ),
    # the Releases rule ships as its own fragment after the version-control import,
    # gated on pypi (the layout.toml lists it only when FEATURE_PYPI is enabled).
    FileSpec(
        ".claude/fragments/AGENTS.md/releases.fragment.md",
        "python/claude/fragments/AGENTS.md/releases.fragment.md",
        "python",
        feature="pypi",
    ),
    FileSpec(
        ".claude/fragments/.claude/settings.json/layout.toml",
        "python/claude/fragments/settings.json/layout.toml",
        "python",
    ),
    # python gitignore layout imports gitignore-python on top of gitignore-base, plus
    # gitignore-docs when FEATURE_DOCS is enabled (the layout.toml gates it inline).
    FileSpec(
        ".claude/fragments/.gitignore/layout.toml",
        "python/claude/fragments/gitignore/layout.toml",
        "python",
    ),
    FileSpec("STYLEGUIDE.md", "python/STYLEGUIDE.md", "python"),
    FileSpec("README.md", "python/README.md", "python"),
    FileSpec(".claude/ty-quiet.toml", "python/claude/ty-quiet.toml", "python"),
    FileSpec("pyproject.toml", "python/pyproject.toml", "python"),
    FileSpec(".python-version", "python/python-version", "python"),
    FileSpec(".github/workflows/ci.yml", "python/github/workflows/ci.yml", "python"),
    FileSpec(".pre-commit-config.yaml", "python/pre-commit-config.yaml", "python"),
    FileSpec("{{PACKAGE}}/__init__.py", "python/package/__init__.py", "python"),
    FileSpec("{{PACKAGE}}/__main__.py", "python/package/__main__.py", "python"),
    FileSpec("{{PACKAGE}}/cli.py", "python/package/cli.py", "python"),
    FileSpec("{{PACKAGE}}/py.typed", "python/package/py.typed", "python"),
    FileSpec("tests/__init__.py", "python/tests/__init__.py", "python"),
    FileSpec("tests/conftest.py", "python/tests/conftest.py", "python"),
    FileSpec("tests/test_cli.py", "python/tests/test_cli.py", "python"),
    # great-docs.yml + docs.yml scaffold as cc-guides layout dirs (seed + one repo-local
    # *.fragment.yml), composed with the fleet cc-skills: imports; gd-build ships the scripts.
    FileSpec(
        ".claude/fragments/great-docs.yml/layout.toml",
        "python/claude/fragments/great-docs.yml/layout.toml",
        "python",
        feature="docs",
    ),
    FileSpec(
        ".claude/fragments/great-docs.yml/great-docs-repo.fragment.yml",
        "python/claude/fragments/great-docs.yml/great-docs-repo.fragment.yml",
        "python",
        feature="docs",
    ),
    FileSpec(
        ".claude/fragments/.github/workflows/docs.yml/layout.toml",
        "python/claude/fragments/.github/workflows/docs.yml/layout.toml",
        "python",
        feature="docs",
    ),
    FileSpec(
        ".claude/fragments/.github/workflows/docs.yml/docs-build-preamble.fragment.yml",
        "python/claude/fragments/.github/workflows/docs.yml/docs-build-preamble.fragment.yml",
        "python",
        feature="docs",
    ),
    FileSpec(".github/workflows/release-pypi.yml", "python/github/workflows/release-pypi.yml", "python", feature="pypi"),
    # --- python as a SECONDARY layer (--secondary-layer python) ---
    # Only a path-keyed styleguide beside the code and an AGENTS.md `## Python Style`
    # pointer; no toolchain. secondary_layer gates selection, so these never fire for
    # a primary `--layer python`.
    FileSpec(
        "{{SECONDARY_CODE_ROOT}}/STYLEGUIDE.md",
        "python/secondary-STYLEGUIDE.md",
        "python",
        secondary_layer="python",
    ),
    FileSpec(
        ".claude/fragments/AGENTS.md/{{PROJECT_NAME}}-secondary-style.fragment.md",
        "python/claude/fragments/AGENTS.md/secondary-style.fragment.md",
        "python",
        secondary_layer="python",
    ),
    # --- go layer (overrides base where dest collides) ---
    FileSpec(".claude/fragments/AGENTS.md/layout.toml", "go/claude/fragments/AGENTS.md/layout.toml", "go"),
    FileSpec(
        ".claude/fragments/AGENTS.md/{{PROJECT_NAME}}-development-guide.fragment.md",
        "go/claude/fragments/AGENTS.md/development-guide.fragment.md",
        "go",
    ),
    FileSpec(
        ".claude/fragments/AGENTS.md/{{PROJECT_NAME}}-style.fragment.md",
        "go/claude/fragments/AGENTS.md/style.fragment.md",
        "go",
    ),
    FileSpec(
        ".claude/fragments/AGENTS.md/releases.fragment.md",
        "go/claude/fragments/AGENTS.md/releases.fragment.md",
        "go",
        feature="release",
    ),
    FileSpec(
        ".claude/fragments/.claude/settings.json/layout.toml",
        "go/claude/fragments/settings.json/layout.toml",
        "go",
    ),
    FileSpec(
        ".claude/fragments/.gitignore/layout.toml",
        "go/claude/fragments/gitignore/layout.toml",
        "go",
    ),
    FileSpec("STYLEGUIDE.md", "go/STYLEGUIDE.md", "go"),
    FileSpec("README.md", "go/README.md", "go"),
    FileSpec("go.mod", "go/go-mod", "go"),
    FileSpec("cmd/{{PROJECT_NAME}}/main.go", "go/cmd/main.go", "go"),
    FileSpec("internal/cli/root.go", "go/internal/cli/root.go", "go"),
    FileSpec("internal/cli/hello.go", "go/internal/cli/hello.go", "go"),
    FileSpec("internal/cli/hello_test.go", "go/internal/cli/hello_test.go", "go"),
    FileSpec("internal/version/version.go", "go/internal/version/version.go", "go"),
    FileSpec("internal/log/log.go", "go/internal/log/log.go", "go"),
    FileSpec("Taskfile.yml", "go/Taskfile.yml", "go"),
    FileSpec(".golangci.yml", "go/golangci.yml", "go"),
    FileSpec(".editorconfig", "go/editorconfig", "go"),
    FileSpec(".github/workflows/ci.yml", "go/github/workflows/ci.yml", "go"),
    FileSpec(".pre-commit-config.yaml", "go/pre-commit-config.yaml", "go"),
    # feature-gated go files (the release pipeline; off by default — see SKILL Phase 1).
    # Default distribution is a native Homebrew cask, built and published by goreleaser
    # itself (homebrew_casks: in .goreleaser.yaml); release.yml is a one-liner calling the
    # shared release-go.yml@v1 reusable workflow. The formula path (render-formula + publish,
    # or native brews:) is a documented recipe, not a scaffolded file — see
    # reference/go-ci-and-release.md.
    FileSpec(".goreleaser.yaml", "go/goreleaser.yaml", "go", feature="release"),
    FileSpec(".github/workflows/release.yml", "go/github/workflows/release.yml", "go", feature="release"),
    # daemonkit files (off by default). cmd/<name>d is a second binary beside the
    # base CLI. Runtime owns takeover and exact wire v1; no consumer peer/server adapter
    # is generated. scripts/test.sh is mandatory wherever proc.Spawn lives.
    FileSpec("cmd/{{PROJECT_NAME}}d/main.go", "go/cmd/daemon-main.go", "go", feature="daemonkit"),
    FileSpec("internal/daemon/root.go", "go/internal/daemon/root.go", "go", feature="daemonkit"),
    FileSpec("internal/daemon/serve.go", "go/internal/daemon/serve.go", "go", feature="daemonkit"),
    FileSpec("internal/daemon/runtime.go", "go/internal/daemon/runtime.go", "go", feature="daemonkit"),
    FileSpec("internal/daemon/version.go", "go/internal/daemon/version.go", "go", feature="daemonkit"),
    FileSpec("internal/daemon/service.go", "go/internal/daemon/service.go", "go", feature="daemonkit"),
    FileSpec("internal/daemon/protocol_test.go", "go/internal/daemon/protocol_test.go", "go", feature="daemonkit"),
    FileSpec("scripts/test.sh", "go/scripts/test.sh", "go", feature="daemonkit"),
    # the signed-.app helper release caller (helper-app feature); widget toggles
    # its appex input via the {{#WIDGET}} section inside the file.
    FileSpec(".github/workflows/release-app.yml", "go/github/workflows/release-app.yml", "go", feature="helper-app"),
    # --- swift layer (SPM package/CLI; overrides base where dest collides) ---
    FileSpec(".claude/fragments/AGENTS.md/layout.toml", "swift/claude/fragments/AGENTS.md/layout.toml", "swift"),
    FileSpec(
        ".claude/fragments/AGENTS.md/{{PROJECT_NAME}}-development-guide.fragment.md",
        "swift/claude/fragments/AGENTS.md/development-guide.fragment.md",
        "swift",
    ),
    FileSpec(
        ".claude/fragments/AGENTS.md/{{PROJECT_NAME}}-style.fragment.md",
        "swift/claude/fragments/AGENTS.md/style.fragment.md",
        "swift",
    ),
    FileSpec(
        ".claude/fragments/AGENTS.md/releases.fragment.md",
        "swift/claude/fragments/AGENTS.md/releases.fragment.md",
        "swift",
        feature="release",
    ),
    FileSpec(
        ".claude/fragments/.claude/settings.json/layout.toml",
        "swift/claude/fragments/settings.json/layout.toml",
        "swift",
    ),
    # both swift layers share one gitignore layout (Xcode + SwiftPM + XcodeBuildMCP
    # state), as they shared the one swift/gitignore template.
    FileSpec(
        ".claude/fragments/.gitignore/layout.toml",
        "swift/claude/fragments/gitignore/layout.toml",
        "swift",
    ),
    FileSpec("STYLEGUIDE.md", "swift/STYLEGUIDE.md", "swift"),
    FileSpec("README.md", "swift/README.md", "swift"),
    FileSpec(
        ".claude/fragments/.mcp.json/layout.toml",
        "swift/claude/fragments/mcp.json/layout.toml",
        "swift",
    ),
    # vendored project skill: help-first discovery of the xcodebuildmcp CLI.
    FileSpec(".claude/skills/xcodebuildmcp-cli/SKILL.md", "swift/claude/skills/xcodebuildmcp-cli/SKILL.md", "swift"),
    FileSpec("Package.swift", "swift/Package.swift", "swift"),
    FileSpec("Sources/{{MODULE_NAME}}/Hello.swift", "swift/Sources/lib/Hello.swift", "swift"),
    FileSpec("Sources/{{PROJECT_NAME}}/Main.swift", "swift/Sources/cli/Main.swift", "swift"),
    FileSpec("Tests/{{MODULE_NAME}}Tests/HelloTests.swift", "swift/Tests/HelloTests.swift", "swift"),
    FileSpec(".swiftformat", "swift/swiftformat", "swift"),
    FileSpec(".swiftlint.yml", "swift/swiftlint.yml", "swift"),
    FileSpec(".pre-commit-config.yaml", "swift/pre-commit-config.yaml", "swift"),
    FileSpec(".github/workflows/ci.yml", "swift/github/workflows/ci.yml", "swift"),
    # feature-gated swift file (the release pipeline; off by default). One caller
    # workflow forwarding to the shared release-swift.yml@swift-v1 reusable workflow
    # (universal swift build + codesign/notarytool + binary cask to the shared tap) —
    # no goreleaser config; goreleaser has no Swift builder.
    FileSpec(".github/workflows/release.yml", "swift/github/workflows/release.yml", "swift", feature="release"),
    # --- swift-app layer (synced-folder Xcode app; shares swift/ srcs for the
    # language-level files so they are written once and consumed by both) ---
    # AGENTS.md prose is app-specific (xcodebuild, synced folders, os.Logger), so
    # swift-app ships its own AGENTS fragments; the settings.json layout imports the
    # same settings-swift variant as the swift layer.
    FileSpec(".claude/fragments/AGENTS.md/layout.toml", "swift-app/claude/fragments/AGENTS.md/layout.toml", "swift-app"),
    FileSpec(
        ".claude/fragments/AGENTS.md/{{PROJECT_NAME}}-development-guide.fragment.md",
        "swift-app/claude/fragments/AGENTS.md/development-guide.fragment.md",
        "swift-app",
    ),
    FileSpec(
        ".claude/fragments/AGENTS.md/{{PROJECT_NAME}}-style.fragment.md",
        "swift-app/claude/fragments/AGENTS.md/style.fragment.md",
        "swift-app",
    ),
    # settings.json layout is identical to the swift layer's (settings-base +
    # settings-swift), so share the one src rather than forking a copy.
    FileSpec(
        ".claude/fragments/.claude/settings.json/layout.toml",
        "swift/claude/fragments/settings.json/layout.toml",
        "swift-app",
    ),
    # gitignore layout is identical to the swift layer's (shared Xcode/SwiftPM state),
    # so share the one src rather than forking a copy.
    FileSpec(
        ".claude/fragments/.gitignore/layout.toml",
        "swift/claude/fragments/gitignore/layout.toml",
        "swift-app",
    ),
    FileSpec("STYLEGUIDE.md", "swift/STYLEGUIDE.md", "swift-app"),
    FileSpec("README.md", "swift-app/README.md", "swift-app"),
    FileSpec(
        ".claude/fragments/.mcp.json/layout.toml",
        "swift/claude/fragments/mcp.json/layout.toml",
        "swift-app",
    ),
    FileSpec(".claude/skills/xcodebuildmcp-cli/SKILL.md", "swift/claude/skills/xcodebuildmcp-cli/SKILL.md", "swift-app"),
    # The committed synced-folder project (objectVersion 77, fixed synthetic UUIDs):
    # sources sync from the folders, so scaffolded .swift files need no pbxproj entry.
    FileSpec("{{PROJECT_NAME}}.xcodeproj/project.pbxproj", "swift-app/xcodeproj/project.pbxproj", "swift-app"),
    FileSpec(
        "{{PROJECT_NAME}}.xcodeproj/xcshareddata/xcschemes/{{PROJECT_NAME}}.xcscheme",
        "swift-app/xcodeproj/app.xcscheme",
        "swift-app",
    ),
    FileSpec("{{PROJECT_NAME}}/App/{{MODULE_NAME}}App.swift", "swift-app/app/App.swift", "swift-app"),
    FileSpec("{{PROJECT_NAME}}/App/ContentView.swift", "swift-app/app/ContentView.swift", "swift-app"),
    FileSpec("{{PROJECT_NAME}}/Assets.xcassets/Contents.json", "swift-app/app/Assets.xcassets/Contents.json", "swift-app"),
    FileSpec(
        "{{PROJECT_NAME}}/Assets.xcassets/AccentColor.colorset/Contents.json",
        "swift-app/app/Assets.xcassets/AccentColor.colorset/Contents.json",
        "swift-app",
    ),
    FileSpec(
        "{{PROJECT_NAME}}/Assets.xcassets/AppIcon.appiconset/Contents.json",
        "swift-app/app/Assets.xcassets/AppIcon.appiconset/Contents.json",
        "swift-app",
    ),
    FileSpec("{{PROJECT_NAME}}Tests/ScaffoldSmokeTests.swift", "swift-app/tests/ScaffoldSmokeTests.swift", "swift-app"),
    FileSpec(".swiftformat", "swift/swiftformat", "swift-app"),
    FileSpec(".swiftlint.yml", "swift/swiftlint.yml", "swift-app"),
    FileSpec(".pre-commit-config.yaml", "swift/pre-commit-config.yaml", "swift-app"),
    FileSpec(".github/workflows/ci.yml", "swift-app/github/workflows/ci.yml", "swift-app"),
    # --- bun layer (single-binary TypeScript CLI/TUI; overrides base where dest
    # collides). No .mcp.json override (base's empty server map suffices — no bun MCP
    # variant) and no .pre-commit-config.yaml (bun ships no swiftformat/ruff analogue). ---
    FileSpec(".claude/fragments/AGENTS.md/layout.toml", "bun/claude/fragments/AGENTS.md/layout.toml", "bun"),
    FileSpec(
        ".claude/fragments/AGENTS.md/{{PROJECT_NAME}}-development-guide.fragment.md",
        "bun/claude/fragments/AGENTS.md/development-guide.fragment.md",
        "bun",
    ),
    FileSpec(
        ".claude/fragments/AGENTS.md/{{PROJECT_NAME}}-style.fragment.md",
        "bun/claude/fragments/AGENTS.md/style.fragment.md",
        "bun",
    ),
    FileSpec(
        ".claude/fragments/AGENTS.md/releases.fragment.md",
        "bun/claude/fragments/AGENTS.md/releases.fragment.md",
        "bun",
        feature="release",
    ),
    FileSpec(
        ".claude/fragments/.claude/settings.json/layout.toml",
        "bun/claude/fragments/settings.json/layout.toml",
        "bun",
    ),
    FileSpec(
        ".claude/fragments/.gitignore/layout.toml",
        "bun/claude/fragments/gitignore/layout.toml",
        "bun",
    ),
    FileSpec("STYLEGUIDE.md", "bun/STYLEGUIDE.md", "bun"),
    FileSpec("README.md", "bun/README.md", "bun"),
    FileSpec("package.json", "bun/package.json", "bun"),
    FileSpec("tsconfig.json", "bun/tsconfig.json", "bun"),
    FileSpec(".bun-version", "bun/bun-version", "bun"),
    FileSpec("src/index.ts", "bun/src/index.ts", "bun"),
    FileSpec("tests/hello.test.ts", "bun/tests/hello.test.ts", "bun"),
    FileSpec(".github/workflows/ci.yml", "bun/github/workflows/ci.yml", "bun"),
    # feature-gated bun file (the release pipeline; off by default). One caller
    # workflow forwarding to the shared release-bun.yml@bun-v1 reusable workflow
    # (native-runner bun --compile matrix + codesign/notarytool + binary cask to the
    # shared tap) — no goreleaser config; goreleaser has no bun builder.
    FileSpec(".github/workflows/release.yml", "bun/github/workflows/release.yml", "bun", feature="release"),
    # --- extras (apply in any layer) ---
    FileSpec(".env", "extras/env", "base", extra="env"),
    FileSpec(".superset/config.json", "extras/superset-config.json", "base", extra="superset", transform="superset_strip"),
    # The canonical plugin binary installer, as a cc-guides v3 layout dir: a
    # layout.toml that imports `cc-skills:install-binary-pinned` (or -latest) with the
    # binary/repo/brew/plugin args, which `cc-guides render` (the post-write step)
    # composes into the real installer. bin/<name> is only ever a symlink (brew
    # binary, durable CLAUDE_PLUGIN_DATA payload, or dev build).
    FileSpec(
        ".claude/fragments/plugin/scripts/install-binary.sh/layout.toml",
        "plugin/install-binary-layout.toml",
        "base",
        extra="plugin",
    ),
)
