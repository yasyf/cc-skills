---
name: repo-bootstrap
description: Bootstraps a new project or repository with proven conventions — AGENTS.md/CLAUDE.md/STYLEGUIDE.md, README structure, a generated mascot logo, README banner, and GitHub social-preview card, Claude Code settings, the cc-context facade (ccx) via the cc-context plugin, and capt-hook guard hooks — plus an optional Python layer (uv with the uv_build backend and flat package layout, Click CLI, loguru, pytest, ruff, ty type-checking) with two opt-in features (a Great Docs site published to GitHub Pages and tag-driven PyPI releases via trusted publishing), or an optional Go layer (cobra CLI, log/slog, golangci-lint + gofumpt, Taskfile, table-driven tests) with an opt-in goreleaser release to a shared Homebrew tap. Use when creating a new repo or project from scratch, scaffolding a new Python or Go package or CLI (with or without docs/PyPI/Homebrew publishing), or retrofitting these conventions onto a young repo.
---

# Bootstrap a New Repo

Scaffold a repo from battle-tested conventions in layers: a **base** layer every
repo gets (agent docs, Claude Code settings, guard hooks, code search), and a
language layer on top — **python** for Python packages (uv toolchain, starter
package, CI, plus opt-in **features** — a Great Docs site and tag-driven PyPI
releases) or **go** for Go CLIs (cobra, slog, golangci-lint + gofumpt, Taskfile, CI,
plus an opt-in `release` feature — goreleaser to a shared Homebrew tap). Templates
render deterministically through one CLI; your judgment goes into naming, prose, and
the follow-up edits — not file copying.

**Scope:** this skill scaffolds conventions and a minimal skeleton only — it does
**not** implement the project's features, in any language. Filling `TODO(bootstrap)`
prose markers (Phase 4) is the only content work; the starter command stays a
hello-world placeholder (the python and go layers each scaffold one; other languages
get a hand-written equivalent). **STOP at the skeleton:** no business logic, real
commands, services/daemons, or release/distribution tooling beyond basic CI — the go
`release` feature (goreleaser, Homebrew tap, release workflow) ships only when the
user selects it. Building the product is separate work that begins after Phase 6.

The whole skill is driven by a single command:

```bash
BOOTSTRAP="python3 ${CLAUDE_PLUGIN_ROOT}/skills/repo-bootstrap/scripts/bootstrap.py"
$BOOTSTRAP identity | check-name NAME | scaffold [flags] | verify [flags]
```

Work the phases below in order. Each ends with an **Exit criteria** line — don't
advance until it holds. Every decision is made once, in Phase 1, and then flows
downstream as flags.

## Terminology

- **Layer** — `base` (all repos), `python` (implies base), or `go` (implies base).
- **Feature** — a layer-scoped opt-in toggled by `--features`. Python: `docs` (Great
  Docs site + Pages workflow) and `pypi` (trusted-publishing release workflow). Go:
  `release` (goreleaser → shared Homebrew tap). A feature requested outside its layer
  is silently dropped. Each gates whole files and inline sections of shared files
  (README, AGENTS, pyproject / goreleaser).
- **Template** — a file under `templates/`; only ever rendered by `bootstrap.py scaffold`, never hand-copied.
- **Placeholder** — a `{{NAME}}` token in a template, rendered by `bootstrap.py scaffold` from `--var` inputs.
- **Partial** — a `{{> path}}` token that inlines a shared fragment from `templates/_partials/` at scaffold time (e.g. the collaboration sections and VC/CI rules shared by base, python, and go `AGENTS.md`). The fragment is render-only — it carries no `dest` in the manifest and is never written to the target repo.
- **TODO marker** — a `TODO(bootstrap):` line in scaffolded output that you must replace with real prose afterward.

## Phase 0 — Identity & environment

Resolve author identity first (never hardcode or guess it):

```bash
$BOOTSTRAP identity
```

It prints `AUTHOR_NAME=`, `AUTHOR_EMAIL=`, `GITHUB_USER=` resolved from `git config`
and `gh`. Any value reported as `MISSING` on stderr must come from the user — ask,
don't invent. Then make sure you're in a git repo on the default branch: if the
directory isn't a repo yet, `git init -b main`. Then create a colocated jj repo
unless one already exists (`.jj/` present): `jj git init --git-repo .` — it backs
jj onto the git repo so the scaffolded `.claude/jj-config.toml` is live from the
first commit (see `reference/base-conventions.md`).

**Exit criteria:** identity values known (resolved or supplied by the user); the
target is a git repo on `main` with a colocated jj repo (`.jj/`).

## Phase 1 — Decide layer & features (the only decision phase)

**First decide the layer.** Apply the **python** layer for a Python project (uv / PyPI);
apply the **go** layer for a Go CLI; otherwise scaffold base only. Base always applies.
For a non-Python, non-Go language, scaffold base only, then hand-write a skeleton that
mirrors the **shape** of a layered starter, not its substance: a minimal package/module
layout, exactly **one** hello-world command that builds and runs, **one** smoke test, a
CI workflow that builds and tests, and the language STYLEGUIDE rules — use
`reference/python-stack.md` and `reference/go-stack.md` as the worked examples (the
hello-world command stays a placeholder). **STOP at the skeleton:** no business logic,
real commands, services/daemons, or release/distribution tooling. For the go layer,
goreleaser / Homebrew / release workflows ship **only** via the opt-in `release` feature
(off by default); for a hand-written language they're product work the user must
explicitly ask for.

**Then gather everything else in one `AskUserQuestion` round:**

- **All layers**: project name, one-line description, **visibility** (public or
  private GitHub repo — it sets the license and feature defaults below and the
  `gh repo create` flag in Phase 6), license (first option "default for
  visibility": PolyForm-Noncommercial-1.0.0 if public, `none` if private; MIT for
  permissive open source), extras (`superset`, `env` — see the table in Phase 2;
  `multiSelect`, default none). The scaffold requires `--extras` explicitly — pass
  `none` when no extras are chosen.
- **Python additionally**: dist name, package name, Python floor + pin versions, and
  the two **features** as a `multiSelect` "Optional Python features" — `docs` (Great
  Docs on GitHub Pages) and `pypi` (tag-driven trusted-publishing release). **Default
  both selected for a public repo, neither for private** — PyPI publishing is
  inherently public, and Pages on a private repo needs a paid plan.
- **Go additionally**: the Go toolchain version (`GO_VERSION`, e.g. `1.26`), and the
  one **feature** as a `multiSelect` "Optional Go features" — `release` (goreleaser
  build + a Homebrew formula pushed to `yasyf/homebrew-tap`). **Default unselected (off)**
  regardless of visibility — release/distribution tooling is product work the user opts
  into, and it needs the tap repo plus a `HOMEBREW_TAP_TOKEN` secret.

Before Phase 2, reconcile the answers into concrete flags: a "default for
visibility" license answer becomes `LICENSE_ID=PolyForm-Noncommercial-1.0.0`
(public) or `LICENSE_ID=none` (private); the feature picks become `--features`.

**Feature → flag mapping:** each selected feature becomes one token in `--features`
(python: `docs,pypi`, `docs`, or `pypi`; go: `release`); deselect everything →
`--features ""`. Omitting the flag selects all of the chosen layer's features — fine
for python (defaults to both), but for **go always pass `--features` explicitly**
(`release` when selected, else `""`), because release defaults off. Don't scaffold a
docs site or release pipeline the user didn't ask for and then strip it by hand —
that's what the flags prevent.

**Naming rule (python):** the PyPI dist name must equal the CLI command — short and
memorable. The import package may differ. Worked example: dist + CLI `capt-hook`,
package `captain_hook`, repo `captain-hook`. Before committing to a dist name:

```bash
$BOOTSTRAP check-name DIST_NAME
```

(`AVAILABLE` → proceed; `TAKEN` → pick another; `UNKNOWN` → have the user verify;
`INVALID` → not a valid PyPI name.) `check-name` checks the exact token only; PyPI's similarity
guard also rejects names that ultranormalize (lowercase, strip `-`/`_`/`.`) to an existing project,
so sweep separator variants too (`reference/python-stack.md` § The Naming Triad).

**Naming rule (go):** the binary, Go module leaf, and repo all share the project name
(`cmd/<name>`, `module github.com/<user>/<name>`) — no dist/package split, and
`check-name` (a PyPI check) is python-only.

### Placeholder reference

| Var | Meaning | Example |
|---|---|---|
| `PROJECT_NAME` | Repo name | `captain-hook` |
| `DESCRIPTION` | One-line description | `Declarative hook framework for Claude Code.` |
| `AUTHOR_NAME` | From `bootstrap.py identity` | — |
| `AUTHOR_EMAIL` | From `bootstrap.py identity` | — |
| `GITHUB_USER` | GitHub login | `yasyf` |
| `LICENSE_ID` | SPDX id, or `none` for no license | `PolyForm-Noncommercial-1.0.0` |
| `DIST_NAME` | PyPI dist == CLI command (python) | `capt-hook` |
| `PACKAGE` | Import package (python) | `captain_hook` |
| `PYTHON_MIN` / `PYTHON_PIN` | Supported floor / dev pin (python) | `3.13` / `3.14` |
| `GO_VERSION` | Go toolchain version (go) | `1.26` |

Derived automatically: `REPO_URL`, `DOCS_URL` (GitHub Pages), `PY_TARGET`,
`MODULE_PATH` (go: `github.com/<user>/<name>`), `YEAR`.
Features are independent of `--var`: they gate files and template sections, not
placeholder values.

**Exit criteria:** layer and visibility chosen; names, license, and extras chosen;
for python, the two features chosen and the dist name `check-name`d; for go,
`GO_VERSION` and the `release` feature chosen.

## Phase 2 — Scaffold

```bash
$BOOTSTRAP scaffold \
  --target . --layer python --extras env --features docs,pypi \
  --var PROJECT_NAME=... --var "DESCRIPTION=..." \
  --var "AUTHOR_NAME=..." --var AUTHOR_EMAIL=... --var GITHUB_USER=... \
  --var LICENSE_ID=PolyForm-Noncommercial-1.0.0 \
  --var DIST_NAME=... --var PACKAGE=... \
  --var PYTHON_MIN=3.13 --var PYTHON_PIN=3.14
```

For the **go** layer:

```bash
$BOOTSTRAP scaffold \
  --target . --layer go --extras none --features "" \
  --var PROJECT_NAME=... --var "DESCRIPTION=..." \
  --var "AUTHOR_NAME=..." --var AUTHOR_EMAIL=... --var GITHUB_USER=... \
  --var LICENSE_ID=MIT --var GO_VERSION=1.26
```

Set `--features` from Phase 1 — python: `docs,pypi` (both), `pypi`/`docs` (one), or
`""` (neither; omitting the flag equals both); go: `release` or `""` — **always pass
it explicitly for go** (omitting equals `release`, but go release defaults off). For
base layer, drop the language `--var`s and `--features`. `--extras` is always
required; pass `--extras none` if none were chosen.

Rules:

- **Never copy from `templates/` by hand** and never leave a `{{...}}` token in the
  repo. The CLI renders, validates inputs, and fails loudly on leftovers.
- Idempotent: identical files are `SKIP`ped; differing files are reported as
  `CONFLICT` and nothing is written (resolve per-file, or re-run with `--force`).
- `LICENSE_ID=none` writes no LICENSE and drops every license reference (README
  badge and License section, pyproject `license`/`license-files`). Licenses without
  a bundled template (bundled: `PolyForm-Noncommercial-1.0.0`, `MIT`) print a
  `MANUAL` line — fetch the text from the SPDX list:
  `curl -fsS https://raw.githubusercontent.com/spdx/license-list-data/main/text/<SPDX-ID>.txt > LICENSE`.
- `--dry-run` previews without writing.

For python, follow the scaffold with `uv sync --extra dev` (creates `uv.lock` —
commit it), `uv run pytest`, and `uvx prek install` to activate the commit hooks
(`.pre-commit-config.yaml`; ruff auto-formats and fixes import order, ty prints
whole-project type warnings — never blocking — on every commit).

For go, follow the scaffold with `go mod tidy` (resolves cobra and writes `go.sum` —
commit it), then `go vet ./...`, `task build`, and `task test` (`go test -race ./...`);
run `uvx prek install` to activate the gofumpt + golangci-lint commit hooks.

For every repo, run `uvx capt-hook review enable` to arm the **session reviewer**:
it registers the captain-hook plugin in `.claude/settings.json` (commit it in
Phase 6), wires the SessionEnd `review run` hook into `.claude/settings.json`, and
watches the repo (machine-local) so ended sessions mine durable corrections into hook
PRs. It needs an authenticated `claude` and `gh`; `uvx capt-hook review disable` turns
it off. See `reference/hooks.md`.

When `cc-notes` is installed (`command -v cc-notes`), also run `cc-notes init` to
adopt the git-native notes/tasks layer: it installs the `refs/cc-notes/*` refspecs,
records the `[packs.cc-notes]` entry (`source = github:yasyf/cc-notes@latest`) in
`.claude/hooks/packs.toml`, and installs the reconcile CI workflow under `.github/`
(commit both in Phase 6). capt-hook auto-fetches the declared pack on the next hook
event — no `uvx capt-hook pack update` to run by hand. The pack's nudges gate on the
`cc-notes` binary being on PATH, so they stay silent on machines without it; this is
why adoption is **conditional** — never declare `[packs.cc-notes]` in a template's
`packs.toml`, or capt-hook would auto-fetch it in every bootstrapped repo, including
ones whose users don't run cc-notes (see `reference/hooks.md`). If `cc-notes` isn't
installed, skip `init` and mention cc-notes as an optional add-on — install the binary,
then `cc-notes init` — so the user can adopt it later. The cc-notes plugin is already
registered by the `.claude/settings.json` template, so a bootstrapped repo gets the
`using-cc-notes` skill even when the binary is absent.

### What lands where

| Destination | Layer | Notes |
|---|---|---|
| `AGENTS.md`, `STYLEGUIDE.md`, `README.md` | base; python/go **override** | the language versions carry feature-gated sections (docs/PyPI for python; a `release` install section for go) rendered to match `--features` |
| `CLAUDE.md`, `CHANGELOG.md`, `LICENSE`, `.gitignore` | base | `CLAUDE.md` is `@AGENTS.md` plus Claude-only rules (AskUserQuestion, task tracking, plan execution & orchestration); `.gitignore` gains python/go entries when layered; `LICENSE` omitted with license `none` |
| `.mcp.json` | base | empty `{"mcpServers":{}}` — code search ships via the `cc-context@skills` plugin (the `ccx` facade), not a per-project server; this is just the seam for any repo-specific MCP server added later |
| `.claude/settings.json` | base; python/go **override** | hooks wired to `uvx capt-hook run <Event>`; registers the `yasyf/cc-skills` and `yasyf/cc-notes` marketplaces and enables `codex@skills`, `slop-cop@skills`, `llm-prompts@skills`, `writing-docs@skills`, `cc-context@skills` (the `ccx` code-search facade), `cc-notes@cc-notes`; go adds `go`/`task` allow-perms (and drops the python-only `TY_CONFIG_FILE`) |
| `.claude/jj-config.toml` | base | jj VCS config; `settings.json` env points `JJ_CONFIG` at it |
| `.claude/ty-quiet.toml` | python | `[rules] all = "ignore"`; `settings.json` env points `TY_CONFIG_FILE` at it so ty is silent inside Claude sessions (no thrashing on diagnostics). CI (`uvx prek run ty`), commits made outside Claude sessions, and editors run without that env and keep the real `[tool.ty]` config (`all = "warn"` — diagnostics print, nothing blocks) |
| `.claude/hooks/packs.toml` | base; python/go **override** | enables capt-hook's builtin packs — `general` (base), plus `python` on the python layer or `go` on the go layer — and the external `[packs.ccx]` pack (`source = github:yasyf/cc-context`, with a `REPLACE_WITH_PINNED_SHA` placeholder `commit` to pin once cc-context cuts a tag) that guards the token-heavy primitives toward `ccx`; the packs ship the guard hooks (see `reference/hooks.md`) |
| `pyproject.toml`, `.python-version` | python | `pyproject` gains a `docs` dependency group only with feature `docs` |
| `great-docs.yml`, `docs/scripts/fix_color_swatch.py`, `docs/scripts/native_reference_titles.py` | python + feature `docs` | omitted entirely without `docs`; `native_reference_titles.py` is a `pre_render` perf workaround (see `reference/docs-site.md`) |
| `.github/workflows/ci.yml` | python **or** go | always; the go workflow runs `go vet`/`go test -race`/`go build` + golangci-lint + govulncheck |
| `.pre-commit-config.yaml` | python **or** go | python: `ruff` + `ty`; go: gofumpt + golangci-lint — via prek, activate with `uvx prek install` |
| `.github/workflows/docs.yml` | python + feature `docs` | Pages docs build |
| `.github/workflows/release-pypi.yml` | python + feature `pypi` | trusted publishing |
| `<PACKAGE>/{__init__,__main__,cli}.py`, `<PACKAGE>/py.typed` | python | Click + loguru starter |
| `tests/{__init__,test_cli}.py` | python | strict CliRunner tests |
| `go.mod`, `cmd/<name>/main.go`, `internal/{cli,version,log}/*.go`, `Taskfile.yml`, `.golangci.yml`, `.editorconfig` | go | cobra + slog starter (one `hello` command + one smoke test); `go.sum` comes from `go mod tidy` |
| `.goreleaser.yaml`, `.github/workflows/release.yml`, `.github/formula/<name>.rb.tmpl` | go + feature `release` | goreleaser builds the matrix; release workflow renders the formula template from the checksums and publishes it to `yasyf/homebrew-tap` via the shared action, gating on `verify-tag-on-main` |
| `.superset/config.json` | extra `superset` | worktree bootstrap (env copy, direnv, uv sync on python, jj init + identity) |
| `.env` | extra `env` | `DEBUG=1`; the one local env file, always gitignored |
| `docs/assets/{logo.png,readme-banner.webp,social-preview.jpg}` | base | **generated, not scaffolded** — Phase 3 creates them via the gen-image skill's brand pipeline; the README banner line and Great Docs logo auto-detection point here, and Phase 6 uploads the social card as the repo's GitHub social preview |

**Exit criteria:** `scaffold` exited 0 (no `CONFLICT`s, no leftover `{{...}}`);
LICENSE present (or `MANUAL` line resolved, or license `none`); for python,
`uv sync --extra dev` succeeded and `uv.lock` is committed; for go, `go mod tidy`
succeeded and `go build ./...` passes (`go.sum` committed).

## Phase 3 — Brand images (mascot + banner + social card)

Every repo gets three generated brand assets, produced by the **`gen-image`
skill's** brand pipeline — apply that skill for this phase; it owns the image
CLI, API-key resolution, model choice, and compression. If the gen-image plugin
is not installed, install it from this marketplace (`gen-image@skills`,
marketplace `yasyf/cc-skills`) or apply the **No brand images** escape hatch.

- `docs/assets/logo.png` — square 1024x1024 mascot character, transparent
  background. With feature `docs`, Great Docs auto-detects it as the navbar logo
  and favicon — zero config (`reference/docs-site.md`). Stays PNG: Great Docs
  detection only matches svg/png.
- `docs/assets/readme-banner.webp` — wide 1536x512 dark banner: project name and
  tagline on the left, the same mascot on the right. The scaffolded README
  already references it.
- `docs/assets/social-preview.jpg` — 1536x768 (2:1) social card from the same
  banner generation; Phase 6 uploads it as the repo's GitHub social preview
  (GitHub accepts only PNG/JPG/GIF under 1 MB there — hence JPEG).

gen-image compresses every output to under 1 MiB locally — small enough for
jj's snapshot limit and GitHub's upload cap.

Default-on for every layer. Skip only when the user declines or gen-image's key
resolution comes up empty (its SKILL.md owns the chain) — then apply the **No
brand images** escape hatch instead of leaving a dangling README reference.

Pick the mascot concept first: a cute character that puns on the project's name or
purpose (a crab for a fleet tool, an octopus for an orchestrator). Then invoke the
gen-image skill's `brand` pipeline from the repo root:

```
brand --name PROJECT_NAME --tagline "DESCRIPTION" --concept "MASCOT_CONCEPT" \
  --out-dir docs/assets
```

It generates the mascot first, composes the banner from it so the character
matches, writes all three files above, and prints every output path.

**View all three files with Read** — re-run with a refined `--concept` if the
mascot misses, or regenerate if the in-image name/tagline text is wrong. On a
re-run where all three files already exist, skip unless the user asked for a
regeneration. Retrofitting a repo that already has a logo but no social card?
Re-run with `--from-logo` — it reuses the existing `logo.png` and regenerates
only banner + social card (`--concept` not needed).

**Exit criteria:** `docs/assets/logo.png` (square, transparent),
`docs/assets/readme-banner.webp` (1536x512), and `docs/assets/social-preview.jpg`
(1536x768) exist and look right when viewed with Read — or the escape hatch was
applied and the README banner line removed.

## Phase 4 — Replace TODO(bootstrap) markers

Every `TODO(bootstrap):` marker is judgment work for you. Find them all with
`rg -n 'TODO\(bootstrap\)'`, and read the matching reference before editing.
For prose markers — anything a human reads rather than a tool parses — apply the
`writing-docs` skill before drafting: its technical-builder voice governs the
README pitch and why-bullets and the great-docs hero tagline. Run
`slop-cop check <file> --lang=markdown` on each prose file you fill (slop-cop is a Go binary; if it's not on PATH, use the `/slop-cop-check` skill — never `uvx slop-cop`).

- `README.md` (pitch, quickstart, why-bullets) and `AGENTS.md` (repository
  structure tree) → read `reference/base-conventions.md` first; write the
  README prose through the `writing-docs` skill.
- `great-docs.yml` (navbar color, accent color, hero tagline) → read
  `reference/docs-site.md` first. *(Only present with feature `docs`.)*

**Exit criteria:** `rg -n 'TODO\(bootstrap\)'` returns nothing.

## Phase 5 — Verify

```bash
$BOOTSTRAP verify --layer python --target .
```

Set `--layer go` for a go repo. Add `--no-license` when license `none` was chosen —
the LICENSE check inverts to require the file absent. Runs every check and reports
`PASS`/`FAIL` per check: leftover-token scan, LICENSE presence (or absence), hook
inline tests, and either (python) `uv sync` → `pytest` → `uv build` → wheel smoke,
or (go) `go vet` → golangci-lint (skipped with a NOTE if not installed) → `go build`
→ `go test -race` → binary smoke. Fix failures and re-run; **never skip a `FAIL`.**
Remaining `TODO(bootstrap)` markers are listed as a `NOTE` — clear them before calling
the repo done — and so is a README banner reference whose image is missing (Phase 3 was
dropped: generate the images, or apply the escape hatch), or a banner without
`social-preview.jpg` beside it (generate it with `--from-logo`). For base layer,
drop the `--layer` flag (it defaults to base).

**Exit criteria:** `verify` prints `All checks passed`.

## Phase 6 — Commit & publish

Atomic, conventional-prefix commits — one logical change each, conditioned on the
layer and features actually scaffolded:

1. `chore: scaffold repo conventions (AGENTS, STYLEGUIDE, settings, hooks)` — include the `.claude/settings.json` captain-hook plugin registration written by `capt-hook review enable` in Phase 2 (and the `cc-notes init` refspecs/pack/CI when cc-notes is installed)
2. `feat: initial <package> package and CLI skeleton` *(python)* / `feat: initial CLI skeleton (cmd + internal packages)` *(go — include `go.mod`/`go.sum`)*
3. `ci: add CI workflow` *(python; append "docs, and PyPI release workflows" per enabled features. go; append "and goreleaser release" with feature `release`)*
4. `docs: README and CHANGELOG` *(append "and Great Docs config" with feature `docs`)*
5. `docs: add mascot logo, README banner, and social card` *(skip if Phase 3 was skipped)*

Then, optionally, publish and wire one-time setups:

- `gh repo create --source . --push --description "$DESCRIPTION"` plus `--public`
  or `--private` per the Phase 1 visibility — always set the description; *(feature
  docs)* also pass `--homepage "$DOCS_URL"` (Pages on a private repo requires a
  paid GitHub plan). For an existing remote, `gh repo edit` with the same flags
  (visibility via `--visibility public|private --accept-visibility-change-consequences`).
- *(any layer)* if `reposync` is installed locally (`command -v reposync`), register the
  new repo so it converges across the user's machines: `reposync repo add .` from the repo
  root (repos live under `~/Code`, reposync's `default_location`). reposync reads the
  freshly pushed `origin`, records the repo, propagates it to peer hosts, and clones it
  wherever it's missing. Best-effort — skip silently when reposync isn't on `PATH`, and it
  never blocks the bootstrap. It needs the GitHub remote to exist, so run it after
  `gh repo create --push`; for a repo you didn't publish, either skip it (register later
  once published) or `reposync repo add --local-only .` to track it on this host only.
- *(feature docs)* enable GitHub Pages with the Actions build type:
  `gh api repos/{owner}/{repo}/pages -X POST -f build_type=workflow`
  (`reference/ci-and-release.md`).
- *(feature pypi)* register the PyPI **pending trusted publisher** for `DIST_NAME`,
  then run the first release: CHANGELOG entry → tag `v0.1.0` on a commit that's on
  `main` → push tag → watch it to completion with `scripts/watch-release.sh` (per-job
  results, release assets, PyPI check; see `reference/ci-and-release.md`). The release's
  `verify-tag-on-main` gate refuses tags off `main`.
- *(feature release, go)* ensure the `yasyf/homebrew-tap` repo exists, then set the release
  secrets from 1Password right after the repo is created:
  `bash "${CLAUDE_PLUGIN_ROOT}/skills/repo-bootstrap/scripts/set-release-secrets.sh" <owner>/<repo>`.
  It pushes `HOMEBREW_TAP_TOKEN` (the tap PAT — required for the formula push) plus the five
  `MACOS_*` sign/notarize secrets (`MACOS_SIGN_P12`, `MACOS_SIGN_PASSWORD`,
  `MACOS_NOTARY_ISSUER_ID`, `MACOS_NOTARY_KEY_ID`, `MACOS_NOTARY_KEY`) from
  `op://OpenClaw/<NAME>/credential`, skipping any not present (absent `MACOS_*` → the release
  runs unsigned; mint missing macOS creds once per `reference/go-ci-and-release.md` § macOS
  signing & notarization). It's best-effort — if 1Password is locked or absent it lists the
  secrets to set by hand and doesn't block. Then run the first release: CHANGELOG entry → tag
  `v0.1.0` on a commit that's on `main` → push tag → watch it with `scripts/watch-release.sh`
  (drop `--pypi` for go; see `reference/ci-and-release.md`). goreleaser builds the binaries and cuts
  the GitHub release, then the workflow renders the formula from the checksums and publishes it to the
  Homebrew tap via the shared action; the `verify-tag-on-main` gate refuses tags off
  `main`. No PyPI/Pages for go (`reference/go-ci-and-release.md`).
- Set the repo's social preview to `docs/assets/social-preview.jpg`. GitHub has
  no API for it — use the **`agent-browser-with-cookies`** skill (install
  `agent-browser-with-cookies@skills` from this marketplace if absent) to run an
  authenticated session from the user's existing GitHub login: it extracts the
  github.com cookies (one Touch ID tap) and opens
  `https://github.com/{owner}/{repo}/settings`. Upload the card straight from disk
  into the social-preview file input:
  `agent-browser --session abwc upload '#repo-image-file-input' docs/assets/social-preview.jpg`
  (snapshot the page and find the input if that selector has moved; no need to push
  the image first — the bytes go from disk to GitHub). Verify with
  `gh api graphql -f query='{ repository(owner: "{owner}", name: "{repo}") { usesCustomOpenGraphImage } }'`
  — it flips to `true` (re-check after ~30s for cache) — then
  `agent-browser --session abwc close`. Not logged into github in any local browser
  (or no Touch ID)? Ask the user to upload it by hand (repo Settings → Social preview).

**Exit criteria:** commits made; for a published repo, remote created with description
(and homepage, with feature `docs`) set, and — when `reposync` is installed — the repo
registered for cross-host sync, and any enabled feature's one-time setup done (or
explicitly deferred with the user).

## Escape hatches

- **Existing repo**: scaffold skips identical files and reports conflicts without
  writing; resolve each conflict deliberately (merge by hand, then re-run — it will
  `SKIP` everything that matches).
- **No PyPI / no docs site**: don't hand-strip — re-scaffold with the feature off.
  `--features docs` drops PyPI (release workflow, badges, `uvx` install, the docs-site
  install widget — README falls back to clone + `uv run`); `--features pypi` drops the docs site (great-docs
  config, Pages workflow, docs badge/section, `docs` dependency group); `--features ""`
  drops both.
- **No release pipeline (go)**: release is off by default — `--features ""` scaffolds no
  `.goreleaser.yaml` / `release.yml` and the README falls back to `go install` + `task
  build`. Re-scaffold with `--features release` to add it; don't hand-add or hand-strip.
- **Library, not a CLI (go)**: the go layer scaffolds a `cmd/<name>` binary. For a library,
  scaffold the go layer, then delete `cmd/` and expose packages at the module root (or under
  `<name>/`); drop the cobra dependency and the `release` feature. The starter `internal/cli`
  becomes the example package to replace.
- **Other licenses**: PolyForm-Noncommercial-1.0.0 (the public-repo default) and MIT
  render from bundled templates; any other SPDX id prints a `MANUAL` line to fetch from the SPDX
  list (see Phase 2) and is set in `pyproject.toml`. MIT is the choice for permissive
  open source (see `reference/base-conventions.md`). `none` (the private-repo
  default) scaffolds no license at all; when retrofitting with `none`, delete any
  existing LICENSE by hand — the scaffold never deletes, and `verify --no-license`
  fails while it remains.
- **No capt-hook hooks wanted**: delete `.claude/hooks/packs.toml` (or `.claude/hooks/`
  entirely) and the `"hooks"` block from `.claude/settings.json`.
- **No commit hooks wanted**: delete `.pre-commit-config.yaml` (and skip
  `uvx prek install`). If you already ran `uvx prek install`, also run
  `uvx prek uninstall` — deleting the config alone orphans the hook and aborts every
  commit. (python) To drop only the ty hook, delete its `repo:` block there and the CI
  ty step. (go) The config runs gofumpt + golangci-lint; the `go` capt-hook pack blocks
  manual invocation, so also relax that pack if you drop the hook.
- **No Codex**: the second-opinion nudge ships in the `general` pack — override it with a
  local `.claude/hooks/commands.py` (a local hook shadows the pack's; see `reference/hooks.md`),
  then remove the `"enabledPlugins"` entry (and `"extraKnownMarketplaces"` if nothing else
  uses it) in `.claude/settings.json`.
- **No brand images**: skip Phase 3 (user declined, or no `OPENAI_API_KEY`) and
  delete the `![<project> banner](...)` line under the README H1. Nothing else to
  clean up — Great Docs simply auto-detects no logo, and `verify` only NOTEs a
  banner the README still references.
- **No prompt-writing nudge**: the nudge ships in the `general` pack — override it with a
  local `.claude/hooks/prompts.py` (see `reference/hooks.md`), then remove the
  `slop-cop@skills` / `llm-prompts@skills` entries from `.claude/settings.json`
  `enabledPlugins` (keep `slop-cop@skills` if you keep the docs nudge).
- **No docs nudge**: the nudge ships in the `general` pack — override it with a local
  `.claude/hooks/docs.py` (see `reference/hooks.md`), then remove the `writing-docs@skills`
  entry from `.claude/settings.json` `enabledPlugins` (keep `slop-cop@skills` if
  the prompt-writing nudge remains).
- **Monorepos**: out of scope — this skill scaffolds single-package repos.

## Reference map

Read these on demand — each is self-contained:

- `reference/base-conventions.md` — AGENTS/CLAUDE/STYLEGUIDE/README/CHANGELOG
  anatomy, commit conventions, license guidance, `.claude` settings explained.
- `reference/python-stack.md` — every python-layer choice with rationale
  (uv/uv_build, flat layout, Click, loguru, pytest, ruff, ty + pyright, naming triad),
  pyproject walkthrough.
- `reference/go-stack.md` — every go-layer choice with rationale (cmd/+internal layout,
  cobra, slog, golangci-lint + gofumpt, table-driven tests, version stamping, the
  error→exit-code idiom).
- `reference/hooks.md` — what each scaffolded hook does, testing with
  `uvx capt-hook test`, tailoring and removal, version requirements.
- `reference/ci-and-release.md` — the three python workflows, one-time PyPI trusted
  publisher + GitHub Pages setup, release procedure.
- `reference/go-ci-and-release.md` — the go CI workflow, the goreleaser base config,
  the formula-by-default Homebrew publish flow, and opt-in recipes (zig CGO, build
  tags, universal binaries, embed-prebuild, `format: binary`, extra cask,
  auto-tag-on-push); shared-tap one-time setup.
- `reference/docs-site.md` — Great Docs config, build/preview commands, enabling
  narrative sections and curated reference.
