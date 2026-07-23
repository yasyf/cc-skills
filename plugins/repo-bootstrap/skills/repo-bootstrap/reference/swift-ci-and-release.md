# Swift CI & Release

The swift/swift-app CI workflows and the swift layer's opt-in `release` feature —
a universal-binary Homebrew cask via the shared `release-swift.yml@83ee384b1d4fe25a8e4aa7258bb76d55e1593735`
reusable workflow in `yasyf/homebrew-tap`. Scaffolded files: `.github/workflows/
ci.yml` (always) and `.github/workflows/release.yml` (feature `release`, swift
only). There is nothing else to configure — no goreleaser config, no cask
template.

## CI (`.github/workflows/ci.yml`) — always

One job on **`macos-26`**, not a matrix and not `macos-latest`:

- Everything (format, lint, build, test) needs the same macOS runner, and macOS
  minutes bill 10× — one job is the whole budget.
- `macos-26` is pinned because its image ships Xcode 26.x — the Swift 6.2
  toolchain the scaffolded `swift-tools-version: 6.2` needs — while
  `macos-latest` still resolves to macOS 15 (Xcode 16, Swift 6.0) and will jump
  underneath a floating pin. When GitHub retires the label, the guard test in
  the plugin (`test_swift_ci_runner_and_actions`) fails and the bump is a
  deliberate edit.
- The image ships swiftformat but not swiftlint (verified live on macos-26), so
  the workflow's first step brew-installs whichever of the two is missing.

Steps: `swiftformat --lint .` → `swiftlint --quiet` → build → test. The package
workflow runs `swift build` + `swift test` with an SPM cache (`actions/cache@v5`
on `.build`, keyed on `Package.resolved`); the app workflow runs `xcodebuild test
… -destination 'platform=iOS Simulator,name=iPhone 17' CODE_SIGNING_ALLOWED=NO`
(no cache — Xcode's DerivedData caches poorly across runs).

**Simulator-name drift:** `iPhone 17` exists on today's macos-26 image; when a
future image drops it, the run fails with "unable to find destination" — bump the
name (or switch to `xcrun simctl list devices` + first available) in one place.

## Base release (`.github/workflows/release.yml`) — feature `release`, swift only

The scaffolded caller is the entire repo-side configuration:

```yaml
jobs:
  release:
    uses: <user>/homebrew-tap/.github/workflows/release-swift.yml@83ee384b1d4fe25a8e4aa7258bb76d55e1593735
    secrets: inherit
```

goreleaser has no Swift builder, so the shared workflow hand-rolls what
`release-go.yml` gets from goreleaser. On a `v*` tag it: verifies the tag is on
`main` → selects Xcode 26.x on the `macos-15` runner → imports the Developer ID
cert (`import-developer-id`) → builds ONE universal binary (`swift build -c
release --arch arm64 --arch x86_64`; both slices asserted with `lipo`) → signs +
notarizes it via the canonical `macos-codesign.sh` (no staple — a bare binary
can't be stapled; Gatekeeper checks the cdhash online, exactly like the Go quill
path) → ditto-zips it to `<name>-<tag>-darwin-universal.zip` + checksums →
creates the GitHub release with generated notes → renders a standard binary cask
(Gatekeeper quarantine preserved, `depends_on macos: ">= :sequoia"`) → pushes it to
the tap.

**The zero-config contract:** the SPM executable product must be named exactly
the repo name (the scaffold guarantees this). Cask `desc`/`homepage` come from
the GitHub repo's description/homepage — set them (Phase 6 does) and the cask
inherits them. Everything else is a `with:` input for the rare exception: runner,
xcode-version, product, package-path, auto-tag, pre-build, run-tests, description,
homepage, macos-floor, cask-template. `macos-floor` defaults to `sequoia` and must
match `Package.swift`'s platforms floor (`.macOS(.v15)` → sequoia) — raise both
together or the cask installs where the binary won't run.

**Signing** is always the native codesign + notarytool path (the job is already
on a macOS runner, so quill buys nothing). The release rejects missing `MACOS_*`
secrets before building and never publishes an unsigned Darwin artifact. The
cask preserves quarantine so Gatekeeper verifies the notarized binary after install.

**Versioning the binary:** the starter's `CommandConfiguration(version:
"0.0.0-dev")` is a compile-time string. To stamp the release tag into
`--version`, generate it in a build-tool step or just keep a one-line
`Sources/<name>/Version.swift` (`let version = "0.1.0"`) that the release
CHANGELOG commit bumps alongside the tag — SPM has no `-ldflags` equivalent, so
the committed-constant approach is the simple one.

## One-time setup

Same as go: the tap repo must exist, and the repo needs `HOMEBREW_TAP_TOKEN`
plus all five `MACOS_*` secrets —
`scripts/set-release-secrets.sh <owner>/<repo>` pushes all six from 1Password
(SKILL Phase 6). Mint the Apple credentials once per
`reference/go-ci-and-release.md` § macOS signing & notarization — the same
Developer ID cert and notary key serve Go and Swift releases.

First release: CHANGELOG entry → `git tag v0.1.0` on a commit that's on `main` →
push the tag → `scripts/watch-release.sh v0.1.0` (no `--pypi`). Verify with
`brew install <user>/tap/<name>` and `<name> --version`.

## Why apps get no generic release feature

`swift-app` scaffolds an iOS app: its distribution is TestFlight/App Store —
provisioning, review, and release trains are product work, not scaffolding. A fixed
signed macOS product app is built, signed, notarized, and stapled by its consumer,
published as an asset of the same release as its CLI, and reconciled by that CLI into
`~/Applications/<MeaningfulProduct>.app` using an exact version, SHA-256, Team ID, and
bundle identifier. It is not a separate holder, release, or cask, and its installer
never strips quarantine. See the Go reference's same-release product application
delivery contract.
