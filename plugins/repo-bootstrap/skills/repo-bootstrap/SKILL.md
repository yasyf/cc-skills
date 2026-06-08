---
name: repo-bootstrap
description: Bootstraps a new project or repository with proven conventions — AGENTS.md/CLAUDE.md/STYLEGUIDE.md, README structure, Claude Code settings, semble code search via .mcp.json, and capt-hook guard hooks — plus an optional Python layer (uv with the uv_build backend and flat package layout, Click CLI, loguru, pytest, ruff, ty type-checking) with two opt-in features: a Great Docs site published to GitHub Pages and tag-driven PyPI releases via trusted publishing. Use when creating a new repo or project from scratch, scaffolding a new Python package or CLI (with or without docs/PyPI publishing), or retrofitting these conventions onto a young repo.
---

# Bootstrap a New Repo

Scaffold a repo from battle-tested conventions in two layers: a **base** layer every
repo gets (agent docs, Claude Code settings, guard hooks, code search), and a
**python** layer on top for Python packages (uv toolchain, starter package, CI,
plus two opt-in **features** — a Great Docs site and tag-driven PyPI releases).
Templates render deterministically through one CLI; your judgment goes into naming,
prose, and the follow-up edits — not file copying.

The whole skill is driven by a single command:

```bash
BOOTSTRAP="python3 ${CLAUDE_PLUGIN_ROOT}/skills/repo-bootstrap/scripts/bootstrap.py"
$BOOTSTRAP identity | check-name NAME | scaffold [flags] | verify [flags]
```

Work the phases below in order. Each ends with an **Exit criteria** line — don't
advance until it holds. Every decision is made once, in Phase 1, and then flows
downstream as flags.

## Terminology

- **Layer** — `base` (all repos) or `python` (implies base).
- **Feature** — a python-only opt-in toggled by `--features`: `docs` (Great Docs site
  + Pages workflow) and `pypi` (trusted-publishing release workflow). Each gates both
  whole files and inline sections of shared files (README, AGENTS, pyproject).
- **Template** — a file under `templates/`; only ever rendered by `bootstrap.py scaffold`, never hand-copied.
- **Placeholder** — a `{{NAME}}` token in a template, rendered by `bootstrap.py scaffold` from `--var` inputs.
- **TODO marker** — a `TODO(bootstrap):` line in scaffolded output that you must replace with real prose afterward.

## Phase 0 — Identity & environment

Resolve author identity first (never hardcode or guess it):

```bash
$BOOTSTRAP identity
```

It prints `AUTHOR_NAME=`, `AUTHOR_EMAIL=`, `GITHUB_USER=` resolved from `git config`
and `gh`. Any value reported as `MISSING` on stderr must come from the user — ask,
don't invent. Then make sure you're in a git repo on the default branch: if the
directory isn't a repo yet, `git init -b main`.

**Exit criteria:** identity values known (resolved or supplied by the user); the
target is a git repo on `main`.

## Phase 1 — Decide layer & features (the only decision phase)

**First decide the layer.** Apply the python layer when the project is Python, uses
uv, or targets PyPI; otherwise scaffold base only. Base always applies. For a
non-Python language, scaffold base only, then write the language-specific STYLEGUIDE
rules and test/CI setup by hand, using `reference/python-stack.md` as the worked
example of a complete layer.

**Then gather everything else in one `AskUserQuestion` round:**

- **All layers**: project name, one-line description, license (default
  PolyForm-Noncommercial-1.0.0; MIT for permissive open source), extras (`superset`,
  `env` — see the table in Phase 2; `multiSelect`, default none).
- **Python additionally**: dist name, package name, Python floor + pin versions, and
  the two **features** as a `multiSelect` "Optional Python features" — `docs` (Great
  Docs on GitHub Pages) and `pypi` (tag-driven trusted-publishing release). **Default
  both selected**, matching the engine's omit-flag default. A private/internal package
  or a one-off script usually wants neither.

**Feature → flag mapping:** each selected feature becomes one token in `--features`
(`docs,pypi`, `docs`, or `pypi`); deselect both → `--features ""`. Omitting the flag
is the same as selecting both. Don't scaffold a docs site or release pipeline the
user didn't ask for and then strip it by hand — that's what the flags prevent.

**Naming rule (python):** the PyPI dist name must equal the CLI command — short and
memorable. The import package may differ. Worked example: dist + CLI `capt-hook`,
package `captain_hook`, repo `captain-hook`. Before committing to a dist name:

```bash
$BOOTSTRAP check-name DIST_NAME
```

(`AVAILABLE` → proceed; `TAKEN` → pick another; `UNKNOWN` → have the user verify;
`INVALID` → not a valid PyPI name.)

### Placeholder reference

| Var | Meaning | Example |
|---|---|---|
| `PROJECT_NAME` | Repo name | `captain-hook` |
| `DESCRIPTION` | One-line description | `Declarative hook framework for Claude Code.` |
| `AUTHOR_NAME` | From `bootstrap.py identity` | — |
| `AUTHOR_EMAIL` | From `bootstrap.py identity` | — |
| `GITHUB_USER` | GitHub login | `yasyf` |
| `LICENSE_ID` | SPDX id | `PolyForm-Noncommercial-1.0.0` |
| `DIST_NAME` | PyPI dist == CLI command (python) | `capt-hook` |
| `PACKAGE` | Import package (python) | `captain_hook` |
| `PYTHON_MIN` / `PYTHON_PIN` | Supported floor / dev pin (python) | `3.13` / `3.14` |

Derived automatically: `REPO_URL`, `DOCS_URL` (GitHub Pages), `PY_TARGET`, `YEAR`.
Features are independent of `--var`: they gate files and template sections, not
placeholder values.

**Exit criteria:** layer chosen; names, license, and extras chosen; for python, the
two features chosen and the dist name `check-name`d.

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

Set `--features` from Phase 1: `docs,pypi` (both), `pypi` or `docs` (one), or `""`
(neither). Omitting the flag equals `docs,pypi`. For base layer, drop the python-only
`--var`s and `--features`.

Rules:

- **Never copy from `templates/` by hand** and never leave a `{{...}}` token in the
  repo. The CLI renders, validates inputs, and fails loudly on leftovers.
- Idempotent: identical files are `SKIP`ped; differing files are reported as
  `CONFLICT` and nothing is written (resolve per-file, or re-run with `--force`).
- Licenses without a bundled template (bundled: `PolyForm-Noncommercial-1.0.0`,
  `MIT`) print a `MANUAL` line — fetch the text from the SPDX list:
  `curl -fsS https://raw.githubusercontent.com/spdx/license-list-data/main/text/<SPDX-ID>.txt > LICENSE`.
- `--dry-run` previews without writing.

For python, follow the scaffold with `uv sync --extra dev` (creates `uv.lock` —
commit it), `uv run pytest`, and `uvx prek install` to activate the ruff commit
hook (`.pre-commit-config.yaml`; auto-formats and fixes import order on every commit).

### What lands where

| Destination | Layer | Notes |
|---|---|---|
| `AGENTS.md`, `STYLEGUIDE.md`, `README.md` | base; python **overrides** | python versions carry feature-gated sections (docs badge/section, PyPI badges/install) rendered to match `--features` |
| `CLAUDE.md`, `CHANGELOG.md`, `LICENSE`, `.gitignore` | base | `CLAUDE.md` is just `@AGENTS.md`; `.gitignore` gains python entries when layered |
| `.mcp.json` | base | semble code search via uvx |
| `.claude/settings.json` | base; python **overrides** | hooks wired to `uvx capt-hook run <Event>`; registers the `yasyf/cc-skills` marketplace and enables `codex@skills`, `slop-cop@skills`, `llm-prompts@skills` |
| `.claude/jj-config.toml` | base | jj VCS config; `settings.json` env points `JJ_CONFIG` at it |
| `.claude/ty-quiet.toml` | python | `[rules] all = "ignore"`; `settings.json` env points `TY_CONFIG_FILE` at it so ty is silent inside Claude sessions (no thrashing on diagnostics). CI (`uv run ty check`) and editors run without that env and keep the real `[tool.ty]` config |
| `.claude/hooks/{__init__,audit,commands,stewardship,prompts,docs,tasks}.py` | base | guard hooks (see `reference/hooks.md`) |
| `.claude/hooks/{testing,style,toolchain}.py` | python | pytest gate, style rules, ruff/uv guards |
| `pyproject.toml`, `.python-version` | python | `pyproject` gains a `docs` dependency group only with feature `docs` |
| `great-docs.yml`, `docs/scripts/fix_color_swatch.py` | python + feature `docs` | omitted entirely without `docs` |
| `.github/workflows/ci.yml` | python | always |
| `.pre-commit-config.yaml` | python | `ruff format` + `check --fix` on every commit via prek; activate with `uvx prek install` |
| `.github/workflows/docs.yml` | python + feature `docs` | Pages docs build |
| `.github/workflows/release-pypi.yml` | python + feature `pypi` | trusted publishing |
| `<PACKAGE>/{__init__,__main__,cli}.py`, `<PACKAGE>/py.typed` | python | Click + loguru starter |
| `tests/{__init__,test_cli}.py` | python | strict CliRunner tests |
| `.superset/config.json` | extra `superset` | worktree bootstrap (env copy, direnv, uv sync on python, jj init + identity) |
| `.env` | extra `env` | `DEBUG=1`; the one local env file, always gitignored |

**Exit criteria:** `scaffold` exited 0 (no `CONFLICT`s, no leftover `{{...}}`);
LICENSE present (or `MANUAL` line resolved); for python, `uv sync --extra dev`
succeeded and `uv.lock` is committed.

## Phase 3 — Replace TODO(bootstrap) markers

Every `TODO(bootstrap):` marker is judgment work for you. Find them all with
`rg -n 'TODO\(bootstrap\)'`, and read the matching reference before editing:

- `README.md` (pitch, quickstart, why-bullets) and `AGENTS.md` (repository
  structure tree) → read `reference/base-conventions.md` first.
- `great-docs.yml` (navbar color, accent color, hero tagline) → read
  `reference/docs-site.md` first. *(Only present with feature `docs`.)*
- `<PACKAGE>/cli.py` `hello` command → replace with the first real command (and
  update `tests/test_cli.py` to match).

**Exit criteria:** `rg -n 'TODO\(bootstrap\)'` returns nothing.

## Phase 4 — Verify

```bash
$BOOTSTRAP verify --layer python --target .
```

Runs every check and reports `PASS`/`FAIL` per check: leftover-token scan, LICENSE
presence, hook inline tests, and (python) `uv sync` → `pytest` → `uv build` → wheel
smoke test. Fix failures and re-run; **never skip a `FAIL`.** Remaining
`TODO(bootstrap)` markers are listed as a `NOTE` — clear them before calling the repo
done. For base layer, drop `--layer python`.

**Exit criteria:** `verify` prints `All checks passed`.

## Phase 5 — Commit & publish

Atomic, conventional-prefix commits — one logical change each, conditioned on the
layer and features actually scaffolded:

1. `chore: scaffold repo conventions (AGENTS, STYLEGUIDE, settings, hooks)`
2. `feat: initial <package> package and CLI skeleton` *(python)*
3. `ci: add CI workflow` *(python; append "docs, and PyPI release workflows" per enabled features)*
4. `docs: README and CHANGELOG` *(append "and Great Docs config" with feature `docs`)*

Then, optionally, publish and wire one-time setups:

- `gh repo create --public --source . --push` to create the GitHub remote.
- *(feature docs)* set the GitHub Pages source to **GitHub Actions**
  (`reference/ci-and-release.md`).
- *(feature pypi)* register the PyPI **pending trusted publisher** for `DIST_NAME`,
  then run the first release: CHANGELOG entry → tag `v0.1.0` → push tag
  (`reference/ci-and-release.md`).

**Exit criteria:** commits made; for a published repo, remote created and any enabled
feature's one-time setup done (or explicitly deferred with the user).

## Escape hatches

- **Existing repo**: scaffold skips identical files and reports conflicts without
  writing; resolve each conflict deliberately (merge by hand, then re-run — it will
  `SKIP` everything that matches).
- **No PyPI / no docs site**: don't hand-strip — re-scaffold with the feature off.
  `--features docs` drops PyPI (release workflow, badges, `uvx` install — README
  falls back to clone + `uv run`); `--features pypi` drops the docs site (great-docs
  config, Pages workflow, docs badge/section, `docs` dependency group); `--features ""`
  drops both.
- **Other licenses**: PolyForm-Noncommercial-1.0.0 (default) and MIT render from
  bundled templates; any other SPDX id prints a `MANUAL` line to fetch from the SPDX
  list (see Phase 2) and is set in `pyproject.toml`. MIT is the choice for permissive
  open source (see `reference/base-conventions.md`).
- **No capt-hook hooks wanted**: delete `.claude/hooks/` and the `"hooks"` block
  from `.claude/settings.json`.
- **No ruff commit hook wanted**: delete `.pre-commit-config.yaml` (and skip
  `uvx prek install`).
- **No Codex**: delete the second-opinion nudge at the bottom of
  `.claude/hooks/commands.py`, plus the `"enabledPlugins"` entry (and
  `"extraKnownMarketplaces"` if nothing else uses it) in `.claude/settings.json`.
- **No prompt-writing nudge**: delete `.claude/hooks/prompts.py` and the
  `slop-cop@skills` / `llm-prompts@skills` entries from `.claude/settings.json`
  `enabledPlugins`.
- **Monorepos**: out of scope — this skill scaffolds single-package repos.

## Reference map

Read these on demand — each is self-contained:

- `reference/base-conventions.md` — AGENTS/CLAUDE/STYLEGUIDE/README/CHANGELOG
  anatomy, commit conventions, license guidance, `.claude` settings explained.
- `reference/python-stack.md` — every python-layer choice with rationale
  (uv/uv_build, flat layout, Click, loguru, pytest, ruff, ty + pyright, naming triad),
  pyproject walkthrough.
- `reference/hooks.md` — what each scaffolded hook does, testing with
  `uvx capt-hook test`, tailoring and removal, version requirements.
- `reference/ci-and-release.md` — the three workflows, one-time PyPI trusted
  publisher + GitHub Pages setup, release procedure.
- `reference/docs-site.md` — Great Docs config, build/preview commands, enabling
  narrative sections and curated reference.
