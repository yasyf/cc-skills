# Go CI & Release

The go layer ships two workflows and (with feature `release`) a goreleaser pipeline.
goreleaser is the **single canonical release tool** for every Go binary — documented here as a
**base config plus opt-in recipes**, so a repo grows from the base into whatever it needs and the
next repo inherits the pattern.

## CI (`.github/workflows/ci.yml`) — always

Runs on push to `main` and on PRs. Three jobs:

- **test** — matrix over `ubuntu-latest` + `macos-latest`, `setup-go` reading `go-version-file: go.mod`
  with module cache, then `go vet ./...`, `go test -race ./...`, `go build ./...`.
- **lint** — `golangci-lint-action` (golangci-lint v2; config is `.golangci.yml`).
- **vuln** — `govulncheck ./...` for known-vulnerability scanning.

## Base release (`.goreleaser.yaml` + `.github/workflows/release.yml`) — feature `release`

**The rule: goreleaser for builds, not for the brew part.** goreleaser v2 can only emit Homebrew
*casks*, but the shared `yasyf/homebrew-tap` is formulae — so the scaffold uses goreleaser to build
binaries, tar.gz archives, `checksums.txt`, and notarize, then hand-renders a **formula** and
publishes it through the shared tap action. Every new Go repo is formula-by-default.

The scaffolded base is **pure Go** (`CGO_ENABLED=0`), darwin/linux × amd64/arm64, version stamped
into `internal/version` via ldflags, archives + checksums. The workflow triggers on a `v*` tag,
gates on `verify-tag-on-main` (refuses tags not on `main`), runs `goreleaser release`, then renders
`Formula/<name>.rb` from the four archive checksums in `dist/checksums.txt` and publishes it to the
shared `yasyf/homebrew-tap` via `yasyf/homebrew-tap/.github/actions/publish@main` (§ Formula repos).
The formula installs notarized binaries, so it carries no quarantine hook. The darwin binaries are
Developer-ID-signed and notarized when the `MACOS_*` secrets are set (§ macOS signing &
notarization); without them the release still runs, unsigned.

The formula is a two-level template. The scaffold renders the bootstrap placeholders such as
`{{PROJECT_NAME}}`, the class name, and the license at `repo-bootstrap` time into
`.github/formula/<name>.rb.tmpl`; the release workflow fills the `__VERSION__` and four `__SHA_*__`
tokens at tag time. To add a runtime dependency, add a `depends_on "<tool>"` line in that template.
cc-context does exactly this for `ast-grep`.

**One-time setup per repo:**
1. The `yasyf/homebrew-tap` repo must exist (it does — multiple repos push to it).
2. Set the release secrets from 1Password in one shot (best-effort — skips any not in the vault,
   never blocks):

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/skills/repo-bootstrap/scripts/set-release-secrets.sh" <owner>/<repo>
   ```

   It pushes `HOMEBREW_TAP_TOKEN` (the fine-grained tap PAT — reused from 1Password, no per-repo
   mint; standardize on this name, older configs used `TAP_GITHUB_TOKEN`) plus the five `MACOS_*`
   sign/notarize secrets when present (§ macOS signing & notarization). Absent `MACOS_*` → the
   release still runs, unsigned.
3. First release: write the CHANGELOG entry, then `git tag vX.Y.Z origin/main && git push origin vX.Y.Z`.
   Watch the run to completion with the bundled helper — it resolves the release run for the tag,
   reports per-job results, and lists the GitHub release assets (drop `--pypi` for go):

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/skills/repo-bootstrap/scripts/watch-release.sh" --tag vX.Y.Z
   ```

**Install** then becomes `brew install yasyf/tap/<name>` (macOS) or `go install <module>/cmd/<name>@latest`.

Validate any config change locally before tagging:

```bash
goreleaser check                              # schema (needs an origin remote)
goreleaser release --snapshot --clean         # full build, no publish (skips notarize)
```

## macOS signing & notarization

The darwin binaries are **Developer ID-signed and notarized** when the five `MACOS_*` secrets are
set; without them the release still runs, unsigned. The **default** path is goreleaser's built-in
**quill** signer — pure Go, so it runs on the existing **`ubuntu-latest`** runner (no macOS runner),
via the `.goreleaser.yaml` `notarize.macos` block:

```yaml
notarize:
  macos:
    - enabled: '{{ if envOrDefault "MACOS_SIGN_P12" "" }}true{{ else }}false{{ end }}'
      ids: [<binary-id>]          # the build that emits the darwin binary — see below
      sign:
        certificate: "{{ .Env.MACOS_SIGN_P12 }}"
        password: "{{ .Env.MACOS_SIGN_PASSWORD }}"
      notarize:
        issuer_id: "{{ .Env.MACOS_NOTARY_ISSUER_ID }}"
        key_id: "{{ .Env.MACOS_NOTARY_KEY_ID }}"
        key: "{{ .Env.MACOS_NOTARY_KEY }}"
        wait: true
        timeout: 20m
```

The workflow just passes the five `MACOS_*` env vars to `goreleaser release` on ubuntu — no keychain
import, no macOS runner.

> **⚠️ The p12 MUST carry the full chain: leaf + Developer ID intermediate + Apple Root CA.**
> quill derives its designated requirement from certificate *chain position*. With only leaf +
> intermediate, the Developer ID CA sits at index 0 and quill emits the **unsatisfiable**
> `certificate root[field.1.2.840.113635.100.6.2.6]` (that marker lives on the intermediate, never on
> the anchor) → `codesign --verify --strict` fails → **macOS SIGKILLs the binary at exec** (exit 137).
> Adding the Apple Root CA pushes the intermediate to index 1 → quill emits the correct
> `certificate 1[…]`. (anchore/quill#566 — quill won't fix the index logic; the full-chain p12 *is*
> the fix. The credential recipe below bundles the root, so a p12 built that way just works.)

Notes:
- **`ids` must reference the build that emits the darwin binary** — base config: the single
  `{{PROJECT_NAME}}` build; with a universal-binary / FUSE recipe use that build's id.
- **`enabled` uses `envOrDefault … non-empty`, not `isEnvSet`** — GitHub Actions exports an unset
  secret as a *set-but-empty* var, which would make `isEnvSet` sign against an empty cert. The
  workflow always passes the five `MACOS_*` env vars; the guard skips them when empty.
- **Bare binaries only** — a bare Mach-O can't be stapled; notarization is recorded against its cdhash
  and checked online by Gatekeeper. The default formula installs these notarized binaries and needs no
  quarantine handling (a `brew install` of a formula doesn't quarantine). A cask-only fallback for an
  unsigned bare binary would still need an `xattr -dr com.apple.quarantine` post-install hook.

### Native codesign — when the release already runs on a macOS runner

If a repo's darwin build *needs* a macOS runner anyway (cgo with native clang, universal `lipo`, a
Swift `.app`), skip quill and sign with Apple's own `codesign` + `notarytool`: native codesign builds
a correct DR from the resolved system chain regardless of p12 ordering, so it sidesteps the quill
index issue entirely. Drop the `notarize` block; add a build post-hook plus a keychain-import step.

```yaml
# .goreleaser.yaml — sign each darwin binary before archiving (no-op without the signing env):
builds:
  - id: <name>
    hooks:
      post:
        - cmd: bash scripts/macos-codesign.sh "{{ .Path }}" "{{ .Target }}"
          output: true
```

```yaml
# .github/workflows/release.yml — goreleaser job on a macOS runner:
goreleaser:
  runs-on: macos-latest                              # codesign/notarytool are macOS-only
  env:
    MACOS_SIGN_P12: ${{ secrets.MACOS_SIGN_P12 }}     # for the `if` gate (step `if` can't read secrets)
  steps:
    - # … checkout, setup-go …
    - name: Import Developer ID certificate
      if: ${{ env.MACOS_SIGN_P12 != '' }}
      run: |   # security create-keychain → import → set-key-partition-list → list-keychains;
        …      # then export MACOS_SIGN_IDENTITY + MACOS_NOTARY_KEY_FILE to $GITHUB_ENV
    - uses: goreleaser/goreleaser-action@v6
      with: { args: release --clean }
      env:
        MACOS_SIGN_IDENTITY:   ${{ env.MACOS_SIGN_IDENTITY }}
        MACOS_NOTARY_KEY_FILE: ${{ env.MACOS_NOTARY_KEY_FILE }}
        MACOS_NOTARY_KEY_ID:   ${{ secrets.MACOS_NOTARY_KEY_ID }}
        MACOS_NOTARY_ISSUER:   ${{ secrets.MACOS_NOTARY_ISSUER_ID }}
```

`scripts/macos-codesign.sh` (shipped in the go layer) runs for `darwin_*` targets only and no-ops
without the env: `codesign --force --options runtime --timestamp -s "$MACOS_SIGN_IDENTITY"` then
`xcrun notarytool submit … --wait`. Used by: **slop-cop**, **cc-orchestrate** (cgo darwin builds),
and the hand-rolled formula repos **cc-notes** / **claude-pool** (§ Formula repos). App bundles
(claude-pool's `CCPoolStatus.app`) sign inside-out with hardened runtime, then notarize **and
`xcrun stapler staple`** the bundle (stapling works for `.app`).

### Creating the credentials (one-time, reusable across all repos)

Requires a paid **Apple Developer Program** membership (Developer ID certs aren't on a free Apple ID);
the account must be Account Holder/Admin. The crypto is all `openssl` (no Keychain GUI); the two Apple
**web** actions have no CLI bootstrap, so drive them with the **`agent-browser-with-cookies`** skill
against the user's logged-in Apple session (fall back to a manual web step if Apple re-challenges 2FA).

**Developer ID Application certificate → `MACOS_SIGN_P12` / `MACOS_SIGN_PASSWORD`:**

```bash
openssl genrsa -out DeveloperID.key 2048
openssl req -new -key DeveloperID.key -out DeveloperID.csr \
  -subj "/CN=Developer ID Application/emailAddress=<apple-id-email>/C=US"
# → developer.apple.com (agent-browser-with-cookies): Certificates → + → Developer ID Application,
#   upload DeveloperID.csr, download developer_id_application.cer. Also grab the Developer ID
#   intermediate from https://www.apple.com/certificateauthority/ as DeveloperIDCA.pem.
openssl x509 -inform DER -in developer_id_application.cer -out DeveloperID.pem
# Full chain — REQUIRED for quill (see the ⚠️ above): intermediate + Apple Root CA. Without the root,
# quill emits a `certificate root[…6.2.6…]` DR that macOS SIGKILLs.
curl -fsSL -o AppleRoot.cer https://www.apple.com/appleca/AppleIncRootCertificate.cer
openssl x509 -inform DER -in AppleRoot.cer -out AppleRoot.pem
cat DeveloperIDCA.pem AppleRoot.pem > chain.pem
openssl pkcs12 -export -inkey DeveloperID.key -in DeveloperID.pem \
  -certfile chain.pem -out DeveloperID.p12 -passout pass:<password>           # <password> = MACOS_SIGN_PASSWORD
base64 -i DeveloperID.p12 | tr -d '\n'                                        # = MACOS_SIGN_P12
```

**App Store Connect API key → `MACOS_NOTARY_ISSUER_ID` / `MACOS_NOTARY_KEY_ID` / `MACOS_NOTARY_KEY`:**
On `appstoreconnect.apple.com` (agent-browser-with-cookies) → Users and Access → Integrations → App
Store Connect API → Team Keys → generate a key (the **Developer** role suffices for notarization).
Capture the **Issuer ID** (`MACOS_NOTARY_ISSUER_ID`) and the key's **Key ID** (`MACOS_NOTARY_KEY_ID`),
download the one-time `.p8`, then `base64 -i AuthKey_XXXXXX.p8 | tr -d '\n'` → `MACOS_NOTARY_KEY`.

Store all five raw values in 1Password (e.g. `op://OpenClaw/MACOS_SIGN_P12/credential`, …) so every
repo reuses the same credentials.

### Setting the secrets per repo

Push them all (the five `MACOS_*` plus `HOMEBREW_TAP_TOKEN`) from 1Password in one shot — accepts
any number of repos, reads each secret from the vault once, and skips whatever isn't there:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/skills/repo-bootstrap/scripts/set-release-secrets.sh" <owner>/<repo> [<owner>/<repo> ...]
```

(`yasyf` is a user, not an org — there are no org-level secrets, so each repo gets its own copies.)
After the first signed release, verify on a Mac:
- `codesign -d -r- "$(command -v <name>)"` → the designated requirement reads
  `certificate 1[field.1.2.840.113635.100.6.2.6]`, **never** `certificate root[…]`. `root` means the
  p12 lacked the Apple Root CA → the binary is SIGKILLed; re-mint the p12 with the full chain.
- `codesign -dv --verbose=4` shows `Authority=Developer ID Application: …`, and
  `codesign --verify --strict "$(command -v <name>)"` exits 0; the binary runs (no SIGKILL).
- `spctl -a -t install -vv "$(command -v <name>)"` reports `accepted … source=Notarized Developer ID`.
  (Use `-t install`, not `-t exec` — a bare CLI binary isn't an app bundle, so `-t exec` says "not an
  app" even when it's correctly notarized.)

## Recipes

Layer these onto the base as a repo needs them. Each is a minimal diff against the base config.

### CGO cross-compile with zig

When the binary needs cgo, set the C compiler per target with zig (bundles libc for every target).
darwin builds use the native clang on a macOS runner; linux/windows cross-compile with zig on linux.

```yaml
builds:
  - id: app
    main: ./cmd/app
    env: [CGO_ENABLED=1]
    goos: [linux, darwin]
    goarch: [amd64, arm64]
    overrides:
      - goos: linux
        goarch: amd64
        env: [CGO_ENABLED=1, "CC=zig cc -target x86_64-linux-musl", "CXX=zig c++ -target x86_64-linux-musl"]
      - goos: linux
        goarch: arm64
        env: [CGO_ENABLED=1, "CC=zig cc -target aarch64-linux-musl", "CXX=zig c++ -target aarch64-linux-musl"]
```

CI installs zig with `mlugg/setup-zig@v2`. Used by: **slop-cop**, **cc-notes** (fuse builds).

### Build tags (pure + tagged variant)

One binary with a feature compiled in via a build tag (e.g. FUSE). Define a second build id:

```yaml
builds:
  - id: pure
    main: ./cmd/app
    env: [CGO_ENABLED=0]
  - id: fuse
    main: ./cmd/app
    env: [CGO_ENABLED=1]
    flags: [-tags=fuse]
archives:
  - id: pure
    ids: [pure]
  - id: fuse
    ids: [fuse]
    name_template: "{{ .ProjectName }}_{{ .Version }}_fuse_{{ .Os }}_{{ .Arch }}"
```

Used by: **cc-notes**, **claude-pool**. Note: if the tagged binary lazily `dlopen`s its dependency
(runs fine without it installed — verify with a no-dep smoke test), you can ship *only* the tagged
build as the default cask and drop the pure/fuse split entirely. That collapse is the cc-notes /
claude-pool simplification.

### Universal macOS binaries

Combine amd64 + arm64 into one fat binary with `lipo`:

```yaml
universal_binaries:
  - id: app-universal
    ids: [app]          # or [fuse] / [pure]
    replace: true       # drop the per-arch archives, keep only the universal one
    mod_timestamp: "{{ .CommitTimestamp }}"
homebrew_casks:
  - name: app
    ids: [app-universal]
```

Used by: **claude-pool**.

### Embed prebuild (`go:embed` assets)

When the binary embeds a built asset (a Vite/bun SPA in `internal/web/dist`), build it in a global
`before` hook so the directory exists before `go build`:

```yaml
before:
  hooks:
    - go mod tidy
    - bash -c 'cd web && bun install --frozen-lockfile && bunx vite build'
```

Used by: **cc-review**.

### `format: binary` archive (keep direct-download installers working)

If something downloads a *bare binary* from the release (a plugin's `install-binary.sh` that fetches
`app_<os>_<arch>`, not a tarball), add a binary-format archive alongside the tar.gz:

```yaml
archives:
  - id: raw
    ids: [app]
    formats: [binary]
    name_template: "{{ .ProjectName }}_{{ .Os }}_{{ .Arch }}"
```

Used by: **cc-review** (its Claude Code plugin downloads the raw binary).

### Extra hand-maintained cask (externally-built artifact)

goreleaser builds Go binaries, not Xcode/Swift `.app` bundles. For a macOS app built by a separate
job (xcodegen + xcodebuild + `ditto` zip), keep that job, render its cask `.rb`, and publish it via
the shared tap action (§ Formula repos). The Go binary ships as the rendered formula, and the app's
cask lives beside it in `yasyf/homebrew-tap`; stage both under `tap-staging/` as `Formula/<name>.rb`
plus `Casks/<app>.rb`.

Used by: **claude-pool** (the `cc-pool-status` widget app).

### Auto-tag-on-push (preserve "push to main auto-releases")

A repo that releases on every push to `main` (monotonic `v0.1.<run_number>`) keeps that UX with
goreleaser: a workflow step creates and pushes the tag, then goreleaser runs with the tag pinned.

```yaml
# in the release job, before goreleaser:
- name: Tag
  run: |
    TAG="v0.1.${{ github.run_number }}"
    git tag "$TAG" && git push origin "$TAG"
    echo "GORELEASER_CURRENT_TAG=$TAG" >> "$GITHUB_ENV"
```

`goreleaser release` then reads `GORELEASER_CURRENT_TAG` instead of requiring a pre-existing tag.
Used by: **slop-cop**.

## Formula repos (the default) → the shared publish action

This is the default path the scaffold wires for every new Go repo, not an exception. goreleaser v2
only emits Homebrew **casks**, but the shared `yasyf/homebrew-tap` is formulae — so a repo lets
goreleaser build + notarize, renders its own `Formula/<name>.rb`, then publishes through **one shared
composite action** so the cross-repo git mechanics live in a single place (and can't drift or grow a
per-repo bug like a `git diff` that runs before `git add`).

The scaffold's `.github/workflows/release.yml` ships the two steps already: a render step that fills
the formula template from `dist/checksums.txt`, and the publish step below.

```yaml
# render Formula/<name>.rb from the release checksums:
- name: Render the formula from release checksums
  run: |
    set -euo pipefail
    version="${GITHUB_REF_NAME#v}"
    sha() { grep -E "  <name>_${version}_$1\.tar\.gz\$" dist/checksums.txt | cut -d' ' -f1; }
    mkdir -p tap-staging/Formula
    sed \
      -e "s|__VERSION__|${version}|g" \
      -e "s|__SHA_DARWIN_ARM64__|$(sha darwin_arm64)|" \
      -e "s|__SHA_DARWIN_AMD64__|$(sha darwin_amd64)|" \
      -e "s|__SHA_LINUX_ARM64__|$(sha linux_arm64)|" \
      -e "s|__SHA_LINUX_AMD64__|$(sha linux_amd64)|" \
      .github/formula/<name>.rb.tmpl > tap-staging/Formula/<name>.rb

# publish the staged Formula/ into the tap:
- name: Publish to the tap
  uses: yasyf/homebrew-tap/.github/actions/publish@main
  with:
    token: ${{ secrets.HOMEBREW_TAP_TOKEN }}   # PAT with contents:write on the tap
    dir: tap-staging                            # contains Formula/ and/or Casks/
    message: "<name> ${{ github.ref_name }}"
```

The action (`yasyf/homebrew-tap/.github/actions/publish`) checks out the tap, merges the staging
dir's `Formula/`/`Casks/` files in, and does the one canonical `git add -A` → `git diff --cached
--quiet` → commit → push. The calling repo only renders its `.rb`, since formula content stays
repo-specific — URLs into its own releases, the runtime `depends_on`, a `service do` block, and fuse
resources. Every scaffolded Go repo uses this, along with **cc-context**, **cc-notes**, and
**claude-pool**. cc-context adds `depends_on "ast-grep"`; claude-pool's formula carries a `service do`
block and a native-Linux FUSE build a cask cannot express.

**The cask fallback.** A cask-only artifact such as a Swift/Xcode `.app` bundle that goreleaser cannot
build still renders a `Casks/<name>.rb` and publishes it through the same action, staged under
`tap-staging/Casks/`. The action merges `Formula/` and `Casks/` alike.

**Sign the darwin binaries the same way** (§ Native codesign — when the release already runs on a
macOS runner) — a hand-rolled formula build still ships Mach-O binaries macOS 15/26 will SIGKILL if
they're ad-hoc/Team-less, so replace any
`codesign --force -s -` with real Developer ID signing on the macOS build job: import the cert (same
keychain step), then `codesign --force --options runtime --timestamp -s "$MACOS_SIGN_IDENTITY"` each
darwin binary and `xcrun notarytool submit … --wait`. A **cgo build that `dlopen`s a third-party dylib**
(cc-notes / claude-pool fuse → libfuse-t) must sign with an entitlements file that sets
`com.apple.security.cs.disable-library-validation` — hardened runtime blocks loading another team's
library otherwise. `lipo` universal binaries sign fine (codesign signs every slice); sign **after**
the `lipo -create`. Used by: **cc-notes**, **claude-pool** (its `cc-pool` binary).

For a real `.app` bundle (claude-pool's `CCPoolStatus.app`, built by xcodebuild) sign at build time with
`CODE_SIGN_STYLE=Manual CODE_SIGN_IDENTITY="Developer ID Application: …" OTHER_CODE_SIGN_FLAGS=--timestamp`
+ hardened runtime (xcodebuild signs inside-out, preserving the appex entitlements), then
`notarytool submit` and — unlike a bare binary — **`xcrun stapler staple`** the bundle, so the cask can
drop its `--no-quarantine` workaround.
