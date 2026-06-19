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

The scaffolded base is **pure Go** (`CGO_ENABLED=0`), darwin/linux × amd64/arm64, version stamped
into `internal/version` via ldflags, archives + checksums, and a Homebrew **cask** pushed to the
shared `yasyf/homebrew-tap` with a quarantine-strip post-install hook. The workflow triggers on a
`v*` tag, gates on `verify-tag-on-main` (refuses tags not on `main`), and runs `goreleaser release`.
The darwin binaries are Developer-ID-signed and notarized when the `MACOS_*` secrets are set
(§ macOS signing & notarization); without them the release still runs, unsigned.

**One-time setup per repo:**
1. The `yasyf/homebrew-tap` repo must exist (it does — multiple repos push to it).
2. A `HOMEBREW_TAP_TOKEN` repo secret — the fine-grained tap PAT lives in 1Password, so set it
   straight from there (no need to mint a new token per repo). Standardize on this name everywhere
   (older configs used `TAP_GITHUB_TOKEN`):

   ```bash
   gh secret set HOMEBREW_TAP_TOKEN -R <owner>/<repo> \
     --body "$(op read 'op://OpenClaw/HOMEBREW_TAP_TOKEN/credential')"
   ```

3. *(optional)* The five `MACOS_*` secrets to sign + notarize the macOS binaries
   (§ macOS signing & notarization).
4. First release: write the CHANGELOG entry, then `git tag vX.Y.Z origin/main && git push origin vX.Y.Z`.
   Watch the run to completion with the bundled helper — it resolves the release run for the tag,
   reports per-job results, and lists the GitHub release assets (drop `--pypi` for go):

   ```bash
   bash "${CLAUDE_PLUGIN_ROOT}/skills/repo-bootstrap/scripts/watch-release.sh" --tag vX.Y.Z
   ```

**Install** then becomes `brew install yasyf/tap/<name>` (macOS) or `go install <module>/cmd/<name>@latest`.

Validate any config change locally before tagging:

```bash
goreleaser check                              # schema (needs an origin remote)
goreleaser release --snapshot --clean         # full build, no publish (sign hook no-ops locally)
```

## macOS signing & notarization

The darwin binaries are **Developer ID-signed and notarized with Apple's own `codesign` + `notarytool`**,
run from a goreleaser **build post-hook** (`scripts/macos-codesign.sh`) on a **macOS runner**.

> **Why not goreleaser's built-in `notarize`?** It signs with **quill**, whose arm64 signatures get
> **SIGKILLed at exec by macOS 15/26** — the binary notarizes fine but the kernel kills it on launch
> (`anchore/quill#566`, closed *not planned*; downstream: `opencode#18503`). Apple's `codesign`
> produces a correct signature (runs, and passes `codesign --verify`), so we use it — which is why the
> release job runs on macOS rather than Linux. (quill *is* cross-platform; that's its only advantage,
> and it's not worth shipping binaries the kernel kills.)

`.goreleaser.yaml` signs each darwin binary in a build post-hook (before archiving); no-op without the
signing env:

```yaml
builds:
  - id: <name>
    # …
    hooks:
      post:
        - cmd: bash scripts/macos-codesign.sh "{{ .Path }}" "{{ .Target }}"
          output: true
```

`scripts/macos-codesign.sh` runs, for `darwin_*` targets only and only when the env is set:
`codesign --force --options runtime --timestamp -s "$MACOS_SIGN_IDENTITY"` then
`xcrun notarytool submit … --wait`. The release workflow imports the cert into a throwaway keychain,
derives the identity, and writes the `.p8`, then hands them to goreleaser:

```yaml
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

Notes:
- **No-op without the secrets** — the import step is gated on `MACOS_SIGN_P12` and the script exits
  early when `MACOS_SIGN_IDENTITY` is empty, so a repo without Apple creds releases unsigned, unchanged.
- **Sign every build that emits a darwin binary** — the hook keys off the `darwin_*` `{{ .Target }}`.
  A repo with a universal-binary / FUSE build signs that build's output the same way.
- **Bare binaries only** — a bare Mach-O can't be stapled; notarization is recorded against its cdhash
  and checked online by Gatekeeper. The cask's `xattr -dr com.apple.quarantine` hook stays.
- **App bundles** (e.g. claude-pool's `CCPoolStatus.app`) sign inside-out with `codesign --options
  runtime`, then notarize **and `xcrun stapler staple`** the bundle (stapling works for `.app`).

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
openssl pkcs12 -export -inkey DeveloperID.key -in DeveloperID.pem \
  -certfile DeveloperIDCA.pem -out DeveloperID.p12 -passout pass:<password>   # <password> = MACOS_SIGN_PASSWORD
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

```bash
for repo in <owner>/<repo> ...; do
  for k in MACOS_SIGN_P12 MACOS_SIGN_PASSWORD MACOS_NOTARY_ISSUER_ID MACOS_NOTARY_KEY_ID MACOS_NOTARY_KEY; do
    gh secret set "$k" -R "$repo" --body "$(op read "op://OpenClaw/$k/credential")"
  done
done
```

(`yasyf` is a user, not an org — there are no org-level secrets, so each repo gets its own five.)
After the first signed release, verify on a Mac: `codesign -dv --verbose=4 "$(command -v <name>)"`
shows `Authority=Developer ID Application: …`; the binary runs (no SIGKILL); and
`spctl -a -t install -vv "$(command -v <name>)"` reports `accepted … source=Notarized Developer ID`.
(Use `-t install`, not `-t exec` — a bare CLI binary isn't an app bundle, so `-t exec` says "not an app"
even when it's correctly notarized.)

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
the shared tap action (below) — goreleaser handles the Go binary's cask; the app's cask lives beside
it in `yasyf/homebrew-tap`.

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

## Formula repos (goreleaser can't emit formulas) → the shared publish action

goreleaser v2 only emits Homebrew **casks**. A repo whose package needs **formula-only** features —
a `service do` block for `brew services` (claude-pool/cc-pool), or a native-Linux build a cask can't
carry (cc-notes' FUSE `mount`) — keeps its own hand-rolled build + renders its own `.rb`, then
publishes through **one shared composite action** so the cross-repo git mechanics live in a single
place (and can't drift or grow a per-repo bug like a `git diff` that runs before `git add`):

```yaml
# in the release job, after rendering Formula/<name>.rb (and any Casks/<name>.rb)
# into a local staging dir mirroring the tap layout:
- name: Publish to the tap
  uses: yasyf/homebrew-tap/.github/actions/publish@main
  with:
    token: ${{ secrets.HOMEBREW_TAP_TOKEN }}   # PAT with contents:write on the tap
    dir: tap-staging                            # contains Formula/ and/or Casks/
    message: "<name> ${{ github.ref_name }}"
```

The action (`yasyf/homebrew-tap/.github/actions/publish`) checks out the tap, merges the staging
dir's `Formula/`/`Casks/` files in, and does the one canonical `git add -A` → `git diff --cached
--quiet` → commit → push. The calling repo only renders its `.rb` (formula *content* is repo-specific
— URLs into its own releases, the service block, fuse resources). New formula repos should use this
action rather than copy-pasting tap git bash. Used by: **cc-notes**, **claude-pool**. (Cask-only
repos don't need it — goreleaser's `homebrew_casks` publishes for them.)

**Sign the darwin binaries the same way** (§ macOS signing & notarization) — a hand-rolled formula
build still ships Mach-O binaries macOS 15/26 will SIGKILL if they're ad-hoc/Team-less, so replace any
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
