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
)
LAYER_ORDER = tuple(layer.name for layer in LAYERS)

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
    # It spans go (goreleaser cask) and swift (release-swift.yml cask); swift-app is
    # deliberately absent — requesting release there is silently dropped, which IS the
    # "apps have no brew release" behavior (App Store/TestFlight is product work).
    Feature("release", "FEATURE_RELEASE", layers=("go", "swift"), default=False),
)

# Optional extra layers, selectable in any layer via --extras.
EXTRAS = ("superset", "env")

_ALL_LAYERS = ("base", "python", "go", "swift", "swift-app")

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
    FileSpec("AGENTS.md", "base/AGENTS.md", "base"),
    FileSpec("CLAUDE.md", "base/CLAUDE.md", "base"),
    FileSpec("STYLEGUIDE.md", "base/STYLEGUIDE.md", "base"),
    FileSpec("README.md", "base/README.md", "base"),
    FileSpec("CHANGELOG.md", "base/CHANGELOG.md", "base"),
    FileSpec(".mcp.json", "base/mcp.json", "base"),
    FileSpec(".claude/settings.json", "base/claude/settings.json", "base"),
    FileSpec(".claude/jj-config.toml", "base/claude/jj-config.toml", "base"),
    # capt-hook hooks ship as the `general` builtin pack; the project enables it
    # via packs.toml instead of vendoring the hook files. See reference/hooks.md.
    FileSpec(".claude/hooks/packs.toml", "base/claude/hooks/packs.toml", "base"),
    # synthesized base files (no single template src)
    FileSpec(".gitignore", None, "base", transform="gitignore"),
    FileSpec("LICENSE", None, "base", transform="license"),
    # --- python layer (overrides base where dest collides) ---
    FileSpec("AGENTS.md", "python/AGENTS.md", "python"),
    FileSpec("STYLEGUIDE.md", "python/STYLEGUIDE.md", "python"),
    FileSpec("README.md", "python/README.md", "python"),
    FileSpec(".claude/settings.json", "python/claude/settings.json", "python"),
    FileSpec(".claude/ty-quiet.toml", "python/claude/ty-quiet.toml", "python"),
    FileSpec("pyproject.toml", "python/pyproject.toml", "python"),
    FileSpec(".python-version", "python/python-version", "python"),
    # python layer adds the `python` builtin pack on top of `general` (overrides
    # the base packs.toml at the same dest with both packs enabled).
    FileSpec(".claude/hooks/packs.toml", "python/claude/hooks/packs.toml", "python"),
    FileSpec(".github/workflows/ci.yml", "python/github/workflows/ci.yml", "python"),
    FileSpec(".pre-commit-config.yaml", "python/pre-commit-config.yaml", "python"),
    FileSpec("{{PACKAGE}}/__init__.py", "python/package/__init__.py", "python"),
    FileSpec("{{PACKAGE}}/__main__.py", "python/package/__main__.py", "python"),
    FileSpec("{{PACKAGE}}/cli.py", "python/package/cli.py", "python"),
    FileSpec("{{PACKAGE}}/py.typed", "python/package/py.typed", "python"),
    FileSpec("tests/__init__.py", "python/tests/__init__.py", "python"),
    FileSpec("tests/conftest.py", "python/tests/conftest.py", "python"),
    FileSpec("tests/test_cli.py", "python/tests/test_cli.py", "python"),
    # feature-gated python files (content-level feature diffs live in templates)
    FileSpec("great-docs.yml", "python/great-docs.yml", "python", feature="docs"),
    FileSpec("docs/scripts/fix_color_swatch.py", "python/docs/scripts/fix_color_swatch.py", "python", feature="docs"),
    FileSpec("docs/scripts/native_reference_titles.py", "python/docs/scripts/native_reference_titles.py", "python", feature="docs"),
    FileSpec(".github/workflows/docs.yml", "python/github/workflows/docs.yml", "python", feature="docs"),
    FileSpec(".github/workflows/release-pypi.yml", "python/github/workflows/release-pypi.yml", "python", feature="pypi"),
    # --- go layer (overrides base where dest collides) ---
    FileSpec("AGENTS.md", "go/AGENTS.md", "go"),
    FileSpec("STYLEGUIDE.md", "go/STYLEGUIDE.md", "go"),
    FileSpec("README.md", "go/README.md", "go"),
    FileSpec(".claude/settings.json", "go/claude/settings.json", "go"),
    # go layer enables the `general` + `go` builtin packs (overrides base packs.toml).
    FileSpec(".claude/hooks/packs.toml", "go/claude/hooks/packs.toml", "go"),
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
    # --- swift layer (SPM package/CLI; overrides base where dest collides) ---
    FileSpec("AGENTS.md", "swift/AGENTS.md", "swift"),
    FileSpec("STYLEGUIDE.md", "swift/STYLEGUIDE.md", "swift"),
    FileSpec("README.md", "swift/README.md", "swift"),
    # swift layers override the empty base .mcp.json with the xcodebuildmcp server.
    FileSpec(".mcp.json", "swift/mcp.json", "swift"),
    FileSpec(".claude/settings.json", "swift/claude/settings.json", "swift"),
    # no swift capt-hook pack exists — general + steering + ccx only.
    FileSpec(".claude/hooks/packs.toml", "swift/claude/hooks/packs.toml", "swift"),
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
    FileSpec("AGENTS.md", "swift-app/AGENTS.md", "swift-app"),
    FileSpec("STYLEGUIDE.md", "swift/STYLEGUIDE.md", "swift-app"),
    FileSpec("README.md", "swift-app/README.md", "swift-app"),
    FileSpec(".mcp.json", "swift/mcp.json", "swift-app"),
    FileSpec(".claude/settings.json", "swift/claude/settings.json", "swift-app"),
    FileSpec(".claude/hooks/packs.toml", "swift/claude/hooks/packs.toml", "swift-app"),
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
    # --- extras (apply in any layer) ---
    FileSpec(".env", "extras/env", "base", extra="env"),
    FileSpec(".superset/config.json", "extras/superset-config.json", "base", extra="superset", transform="superset_strip"),
)
