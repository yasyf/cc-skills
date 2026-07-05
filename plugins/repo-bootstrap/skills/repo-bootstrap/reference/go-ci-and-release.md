# Go CI & Release

The go layer ships two workflows and (with feature `release`) a goreleaser pipeline.
goreleaser is the **single canonical release tool** for every Go binary — documented here as a
**base config plus opt-in recipes**, so a repo grows from the base into whatever it needs and the
next repo inherits the pattern. The shared release infrastructure lives in `yasyf/homebrew-tap`,
pinned `@v1`: one reusable workflow (`release-go.yml`) and four composite actions
(`verify-tag-on-main`, `import-developer-id`, `render-formula`, `publish`). A scaffolded repo
forwards to it; it never vendors the mechanics.

## CI (`.github/workflows/ci.yml`) — always

Runs on push to `main` and on PRs. Three jobs:

- **test** — matrix over `ubuntu-latest` + `macos-latest`, `setup-go` reading `go-version-file: go.mod`
  with module cache, then `go vet ./...`, `go test -race ./...`, `go build ./...`.
- **lint** — `golangci-lint-action` (golangci-lint v2; config is `.golangci.yml`).
- **vuln** — `govulncheck ./...` for known-vulnerability scanning.

## Base release (`.goreleaser.yaml` + `.github/workflows/release.yml`) — feature `release`

The whole `release.yml` is a **one-liner** that forwards to the shared reusable workflow:

```yaml
jobs:
  release:
    uses: <user>/homebrew-tap/.github/workflows/release-go.yml@v1
    secrets: inherit
```

`secrets: inherit` forwards `HOMEBREW_TAP_TOKEN` plus the five `MACOS_*` secrets. The reusable
workflow runs on `ubuntu-latest`: it gates on `verify-tag-on-main`, then runs goreleaser, which
builds + quill-signs the binaries and **publishes the cask itself**. Pass `setup-bun: true` to the
reusable workflow when a `before` hook builds a bun/Vite asset.

**The default distribution is a native Homebrew cask**, emitted by goreleaser's `homebrew_casks:`
block straight into the shared tap — no render/publish step. (goreleaser v2 emits *both* casks and
formulae; the casks-only premise some older docs carried is false.) The scaffolded `.goreleaser.yaml`
is **pure Go** (`CGO_ENABLED=0`), darwin/linux × amd64/arm64, version stamped into
`internal/version` via ldflags, tar.gz archives + checksums, the quill `notarize:` block, and:

```yaml
homebrew_casks:
  - name: <name>
    binaries: [<name>]
    repository:
      owner: <user>
      name: homebrew-tap
      token: "{{ .Env.HOMEBREW_TAP_TOKEN }}"
    homepage: <repo-url>
    description: <description>
    hooks:
      post:
        install: |
          if OS.mac?
            system_command "/usr/bin/xattr", args: ["-dr", "com.apple.quarantine", "#{staged_path}/<name>"]
          end
```

The `xattr -dr com.apple.quarantine` post-install hook lets the (notarized, or unsigned) binary run
on first launch. A cask ships the prebuilt binary as-is — pick a **formula** instead only when you
need `brew services`, a runtime `depends_on`, or conditional install (§ Formula recipe).

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

## Formula recipe — `brew services`, runtime deps, or conditional install

A cask can't run a `service do` block, declare a runtime `depends_on`, or branch its install. When
the repo needs that, ship a **formula** instead of the cask. Two ways:

**Native `brews:` (preferred when it's expressible).** goreleaser v2 emits a formula from a `brews:`
block — and it supports `service:` and `dependencies:`. Drop `homebrew_casks:`, add:

```yaml
brews:
  - name: <name>
    repository: { owner: <user>, name: homebrew-tap, token: "{{ .Env.HOMEBREW_TAP_TOKEN }}" }
    homepage: <repo-url>
    description: <description>
    dependencies: [{ name: ast-grep }]      # runtime depends_on
    service: |                               # brew services
      run [opt_bin/"<name>", "serve"]
      keep_alive true
```

**Rendered `.rb.tmpl` via the render-formula action** — when the formula needs `livecheck`, a
`head do` build-from-source block, or conditional Ruby that `brews:` can't express. Keep a
`.github/formula/<name>.rb.tmpl` (placeholders `__VERSION__` + four `__SHA_<OS>_<ARCH>__`) and a
**composed** `release.yml` (not the one-liner): goreleaser builds archives + notarize with **no**
homebrew block, then two shared actions fill and publish the template:

```yaml
- uses: <user>/homebrew-tap/.github/actions/render-formula@v1
  with:
    template: .github/formula/<name>.rb.tmpl
    output: Formula/<name>.rb
    name: <name>
- uses: <user>/homebrew-tap/.github/actions/publish@v1
  with:
    token: ${{ secrets.HOMEBREW_TAP_TOKEN }}
    dir: tap-staging
    message: "<name> ${{ github.ref_name }}"
```

`render-formula` reads `dist/checksums.txt`, fills `__VERSION__` and the four `__SHA_*__` tokens, and
writes `Formula/<name>.rb`; `publish` does the one canonical `git add -A` → `git diff --cached
--quiet` → commit → push into the tap. In the fleet: **ccx** and **synckitd** use this render-formula
path.

## macOS signing & notarization

Two modes. Both no-op when the `MACOS_*` secrets are unset, so a repo without Apple creds still
releases (unsigned).

### (1) quill on ubuntu — the default

The scaffold's quill `notarize:` block signs the darwin binaries on the **`ubuntu-latest`** runner
(pure Go, no macOS runner). The reusable workflow passes the five `MACOS_*` env vars to goreleaser:

```yaml
notarize:
  macos:
    - enabled: '{{ if envOrDefault "MACOS_SIGN_P12" "" }}true{{ else }}false{{ end }}'
      ids: [<binary-id>]          # the build that emits the darwin binary
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

> **⚠️ The p12 MUST carry the full chain: leaf + Developer ID intermediate + Apple Root CA.**
> quill derives its designated requirement from certificate *chain position*. With only leaf +
> intermediate, the Developer ID CA sits at index 0 and quill emits the **unsatisfiable**
> `certificate root[field.1.2.840.113635.100.6.2.6]` (that marker lives on the intermediate, never on
> the anchor) → `codesign --verify --strict` fails → **macOS SIGKILLs the binary at exec** (exit 137).
> Adding the Apple Root CA pushes the intermediate to index 1 → quill emits the correct
> `certificate 1[…]`. (anchore/quill#566 — quill won't fix the index logic; the full-chain p12 *is*
> the fix. The credential recipe below bundles the root, so a p12 built that way just works.)

Notes:
- **`ids` must reference the build that emits the darwin binary** — base config: the single `<name>`
  build; with a universal-binary / FUSE recipe use that build's id.
- **`enabled` uses `envOrDefault … non-empty`, not `isEnvSet`** — GitHub Actions exports an unset
  secret as a *set-but-empty* var, which would make `isEnvSet` sign against an empty cert. The
  workflow always passes the five `MACOS_*` env vars; the guard skips them when empty.
- **Bare binaries only** — a bare Mach-O can't be stapled; notarization is recorded against its cdhash
  and checked online by Gatekeeper. The default cask carries the `xattr -dr com.apple.quarantine`
  post-install hook so an unsigned bare binary still runs on first launch.

### (2) native codesign — when the release already runs on a macOS runner

If a repo's darwin build *needs* a macOS runner anyway (cgo with native clang, universal `lipo`, a
Swift `.app`), skip quill and sign with Apple's own `codesign` + `notarytool`: native codesign builds
a correct DR from the resolved system chain regardless of p12 ordering, so it sidesteps the quill
index issue entirely. Drop the `notarize` block; instead use the shared **`import-developer-id`**
action and a goreleaser build post-hook. The action does the keychain import, exports the signing env,
and drops `$MACOS_CODESIGN_SCRIPT` (the canonical `macos-codesign.sh`, which now lives only in the
action — repos must not vendor a copy):

```yaml
# .goreleaser.yaml — sign each darwin binary before archiving (no-op without the signing env):
builds:
  - id: <name>
    hooks:
      post:
        - cmd: bash "$MACOS_CODESIGN_SCRIPT" "{{ .Path }}" "{{ .Target }}"
          output: true
```

```yaml
# .github/workflows/release.yml — composed (not the one-liner), goreleaser job on a macOS runner:
goreleaser:
  runs-on: macos-latest                              # codesign/notarytool are macOS-only
  steps:
    - # … checkout, setup-go …
    - uses: <user>/homebrew-tap/.github/actions/import-developer-id@v1
      # imports the cert into a keychain, exports the signing env + $MACOS_CODESIGN_SCRIPT
    - uses: goreleaser/goreleaser-action@v7
      with: { args: release --clean }
```

The signing script runs for `darwin_*` targets only and no-ops without the env:
`codesign --force --options runtime --timestamp -s "$MACOS_SIGN_IDENTITY"` then
`xcrun notarytool submit … --wait`. Used by: **slop-cop**, **cc-orchestrate** (cgo darwin builds),
and the formula repos **cc-notes** / **claude-pool**. App bundles (claude-pool's `CCPoolStatus.app`)
sign inside-out with hardened runtime, then notarize **and `xcrun stapler staple`** the bundle
(stapling works for `.app`).

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

CI installs zig with `xyzzylabs/setup-zig@v1` (a maintained Node-24 fork of `mlugg/setup-zig`, which is stuck on the deprecated Node-20 runtime). Used by: **slop-cop**, **cc-notes** (fuse builds).

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
`before` hook so the directory exists before `go build`, and pass `setup-bun: true` to the reusable
workflow so the runner has bun:

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
the shared `publish` action (§ Formula recipe). The Go binary ships as a cask (or formula), and the
app's cask lives beside it in `yasyf/homebrew-tap`; stage both under `tap-staging/` as
`Casks/<app>.rb` (plus `Formula/<name>.rb` if the Go side is a formula).

A hand-maintained app cask must strip Homebrew's download quarantine itself — the goreleaser
template's post_install hook only covers goreleaser-built casks. Add a postflight to the `.rb.tmpl`
(best-effort, so a headless install never fails the cask):

```ruby
postflight do
  # Strip Homebrew's download quarantine so first launch is silent (notarized+stapled).
  system_command "/usr/bin/xattr",
                 args: ["-dr", "com.apple.quarantine", "#{appdir}/<App>.app"],
                 must_succeed: false
end
```

Skip it only when quarantine is load-bearing (e.g. `cookiesync-keyhelper` deliberately keeps it).

Used by: **claude-pool** (the `cc-pool-status` widget app), **fusekit** (`fusekit-holder`).

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
