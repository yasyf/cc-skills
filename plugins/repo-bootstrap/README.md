# repo-bootstrap

**Stop copying dotfiles, CI workflows, and linter configs from your last repo.** Scaffold a new repo or retrofit a young one in one pass with agent docs, guard hooks, brand images, and opinionated Python, Go, Swift, or Bun layers.

## Install

```text
/plugin marketplace add yasyf/cc-skills
/plugin install repo-bootstrap@skills
```

- `cc-guides`, installed with `brew install yasyf/tap/cc-guides`, plus `git`, `gh`, and `jj`.
- `gen-image@skills` and `OPENAI_API_KEY` for brand images, unless you skip the brand-image step.
- `brew install swiftformat swiftlint` for Swift layers, plus Xcode for `swift-app`.
- macOS, 1Password, `agent-browser-with-cookies`, and an active Account Holder session on `developer.apple.com` for `apple-certs`.

## Use it

### Scaffold a new CLI repo with docs and releases

Starting a project from scratch means wiring linters, test runners, agent instructions, and release workflows before writing a line of code. With `repo-bootstrap` installed, ask:

```text
Bootstrap a new Python CLI repo called trimmy, with docs and PyPI releases
```

One pass resolves your identity from `git` and `gh`, asks for name, description, visibility, license, and layer features in one question round, and initializes `git` with a colocated `jj` repository. The scaffolder renders `AGENTS.md`, `STYLEGUIDE.md`, Claude Code settings, the `ccx` code-search facade from cc-context, `capt-hook` guard hooks, and three brand images at `docs/assets/logo.png`, `docs/assets/readme-banner.webp`, and `docs/assets/social-preview.jpg`. The `uv` Python layer sets up Click, `pytest`, `ruff`, and `ty`, with optional features for a Great Docs site on GitHub Pages, tag-driven PyPI trusted publishing, and opt-in native `PyO3` wheels through `maturin` on top of `pypi`. Next, the workflow fills `TODO(bootstrap)` prose markers, runs `verify` across leftover tokens, `LICENSE`, hook tests, and the build/test/smoke chain, makes atomic conventional-prefix commits, and optionally runs `gh repo create`. It stops at a hello-world placeholder with no business logic.

### Bootstrap a Go, Swift, or Bun project

Wiring linters, build tools, and Homebrew release workflows across compiled CLIs and iOS apps repeats the same setup in every repository. With `repo-bootstrap` installed, ask:

```text
Bootstrap a public Swift CLI repo with Homebrew cask releases
```

The Go layer wires `cobra`, `slog`, `golangci-lint`, `gofumpt`, and `Taskfile`, while the Swift layer wires `swift-argument-parser`, Swift Testing, `SwiftFormat`, `SwiftLint`, and `XcodeBuildMCP`. Bun scaffolds a single-binary TypeScript CLI or terminal UI, and `swift-app` scaffolds a SwiftUI iOS app with a synced-folder Xcode project. When you enable the optional `release` feature, which is off by default, the Go, Swift, and Bun layers ship Homebrew releases to a shared tap. Public repositories default to `PolyForm-Noncommercial-1.0.0` with `MIT` available on request, and private repositories default to no license. On an existing repository, the scaffolder skips identical files and reports conflicts, while monorepos remain out of scope.

### Mint and stash Apple signing and notarization credentials

Renewing an expiring Developer ID certificate or wiring macOS notarization secrets by hand means clicking through portal forms and exporting keychains. With `repo-bootstrap` installed, ask:

```text
Use repo-bootstrap:apple-certs to set up macOS signing and notarization secrets for this repo
```

The skill generates a certificate signing request locally so the private key never leaves your machine. Using `agent-browser-with-cookies` and your existing desktop session on `developer.apple.com`, it mints a full-chain `.p12` Developer ID Application certificate and an App Store Connect notary API key. After verifying that the identity signs, it pushes both into 1Password and the repository's GitHub secrets as `MACOS_SIGN_P12`, `MACOS_SIGN_PASSWORD`, `MACOS_NOTARY_ISSUER_ID`, `MACOS_NOTARY_KEY_ID`, and `MACOS_NOTARY_KEY`.

## What's inside

| Kind | Name | What it does |
| --- | --- | --- |
| Skill | `repo-bootstrap:repo-bootstrap` | Bootstraps a new single-package repo or retrofits a young one with agent docs, brand assets, Claude Code settings, `ccx`, `capt-hook` guard hooks, and Python, Go, Swift, `swift-app`, or Bun layers. |
| Skill | `repo-bootstrap:apple-certs` | Mints a Developer ID Application `.p12` certificate and App Store Connect notary API key, verifies signing, and saves them to 1Password and GitHub secrets. |

Part of [cc-skills](../../README.md).
