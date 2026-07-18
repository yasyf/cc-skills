# Bun CI & Release

The bun layer's CI workflow and its opt-in `release` feature — one single-file
`bun build --compile` binary per platform, shipped as a Homebrew cask via the
shared `release-bun.yml@bun-v1` reusable workflow in `yasyf/homebrew-tap`.
Scaffolded files: `.github/workflows/ci.yml` (always) and
`.github/workflows/release.yml` (feature `release`). There is nothing else to
configure — no goreleaser config, no cask template.

## CI (`.github/workflows/ci.yml`) — always

One ubuntu job: checkout → `oven-sh/setup-bun@v2` reading the repo's
`.bun-version` file → `bun install --frozen-lockfile` → `bun run typecheck` →
`bun test`. Two coupling rules the scaffold bakes in:

- **`.bun-version` is the toolchain pin.** CI and every release-matrix leg read
  it via `bun-version-file` — never a floating `bun-version: latest`. The
  release build guards fail loud when the file is missing.
- **`bun.lock` must be committed** (the go.sum/uv.lock analogue). The frozen
  install fails without it; run `bun install` once after scaffolding and commit
  the lock.

## Release (`.github/workflows/release.yml`) — feature `release`

The scaffolded caller is the entire repo-side configuration:

```yaml
jobs:
  release:
    uses: <user>/homebrew-tap/.github/workflows/release-bun.yml@bun-v1
    secrets: inherit
```

goreleaser has no bun builder, so the shared workflow owns the whole job. On a
`v*` tag it: verifies the tag is on `main` → builds one single-file binary per
platform on a 4-leg native-runner matrix (darwin-arm64 on `macos-15`,
darwin-x64 on `macos-15-intel`, linux-x64 on `ubuntu-24.04`, linux-arm64 on
`ubuntu-24.04-arm`) → signs + notarizes the two darwin binaries via the
canonical `macos-codesign.sh` (no staple — a bare binary can't be stapled;
Gatekeeper checks the cdhash online, exactly like the Go and Swift paths) →
zips each to `<name>-<tag>-<platform>.zip` with per-zip `.sha256` files and an
aggregate `checksums.txt` → creates ONE GitHub release holding all four zips →
renders the standard 4-platform binary cask (on_macos/on_linux ×
on_arm/on_intel, quarantine-strip postflight) → pushes it to the tap. A
hyphenated tag (`v0.1.0-rc.1`) publishes a prerelease and skips the cask —
brew has no prerelease channel, so the tap only advances on final tags.

**Why native runners:** cross-compiling breaks for any project with
platform-specific native deps (OpenTUI, for one, ships per-platform native
packages). bun refuses to extract a platform-mismatched optional dep, and
`bun build --compile --target=…` inlines `process.platform`, which makes the
target's platform package a hard build-time dep. Each target therefore builds
on its own runner — which also means every leg can smoke-test the real
artifact it built.

**The zero-config contract:** the entry point is `src/index.ts`, the binary
and cask carry the repo's name, and `.bun-version` pins the toolchain. Cask
`desc`/`homepage` come from the GitHub repo's description/homepage — set them
(Phase 6 does) and the cask inherits them. Everything else is a `with:` input
for the rare exception: `entry-point`, `name`, `auto-tag`, `pre-build`,
`run-tests`, `smoke-command`, `entitlements`, `description`, `homepage`,
`cask-template`. The linux zips serve Linuxbrew opportunistically (the cask's
on_linux stanzas) and plain-zip consumers; the cask's first-class home is
macOS.

**Signing** is the native codesign + notarytool path on the darwin legs. With
the `MACOS_*` secrets absent the release ships unsigned with a warning; the
cask postflight strips the quarantine xattr so an unsigned binary still runs
after `brew install`. One bun-specific wrinkle: hardened-runtime notarization
can break a JIT runtime. If the signed binary dies at launch where an unsigned
build ran, pass `entitlements:` pointing at a plist that grants
`com.apple.security.cs.allow-jit` — the workflow exports it to
`macos-codesign.sh` on the darwin legs.

**Versioning the binary:** bun inlines a `package.json` import at compile
time, so a `--version` fast-path reading the imported `version` field costs
nothing at runtime — bump `package.json`'s `version` in the release CHANGELOG
commit alongside the tag (bun has no `-ldflags` equivalent). Pair it with
`smoke-command: "--version"` and every matrix leg self-verifies the compiled
artifact before anything ships.

## One-time setup

Same as go and swift: the tap repo must exist, and the repo needs
`HOMEBREW_TAP_TOKEN` (+ optionally the five `MACOS_*` secrets) —
`scripts/set-release-secrets.sh <owner>/<repo>` pushes all six from 1Password
(SKILL Phase 6). Mint the Apple credentials once per
`reference/go-ci-and-release.md` § macOS signing & notarization — the same
Developer ID cert and notary key serve Go, Swift, and Bun releases.

First release: CHANGELOG entry → `git tag v0.1.0` on a commit that's on `main`
→ push the tag → `scripts/watch-release.sh v0.1.0` (no `--pypi`). Verify with
`brew install <user>/tap/<name>` and `<name> --version`.

## Runner-label drift

The matrix hardcodes GitHub's current native runner labels. Two are worth
watching: `macos-15-intel` is the end-of-line Intel image (long queues today,
retirement eventually), and `ubuntu-24.04-arm` is free for public repos only.
When a label dies, the fix lands once in `release-bun.yml` and reaches every
caller via a `bun-v1` tag move — never in a consumer repo.
