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

**One-time setup per repo:**
1. The `yasyf/homebrew-tap` repo must exist (it does — multiple repos push to it).
2. A `HOMEBREW_TAP_TOKEN` repo secret: a PAT with `contents:write` on the tap repo. (Standardize on
   this name everywhere — older configs used `TAP_GITHUB_TOKEN`.)
3. First release: write the CHANGELOG entry, then `git tag vX.Y.Z origin/main && git push origin vX.Y.Z`.

**Install** then becomes `brew install yasyf/tap/<name>` (macOS) or `go install <module>/cmd/<name>@latest`.

Validate any config change locally before tagging:

```bash
goreleaser check                              # schema (needs an origin remote)
goreleaser release --snapshot --clean         # full build, no publish
```

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
job (xcodegen + xcodebuild + `ditto` zip), keep that job, and maintain its cask **by hand** in the
shared tap (a small CI step that rewrites the version + sha256 and pushes). goreleaser handles the
Go binary's cask; the app's cask lives beside it in `yasyf/homebrew-tap`.

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
