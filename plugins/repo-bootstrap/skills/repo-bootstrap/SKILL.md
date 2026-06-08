---
name: repo-bootstrap
description: Bootstraps a new project or repository with proven conventions — AGENTS.md/CLAUDE.md/STYLEGUIDE.md, README structure, Claude Code settings, semble code search via .mcp.json, and capt-hook guard hooks — plus an optional Python layer (uv with the uv_build backend and flat package layout, Click CLI, loguru, pytest, ruff, pyright strict) with two opt-in features: a Great Docs site published to GitHub Pages and tag-driven PyPI releases via trusted publishing. Use when creating a new repo or project from scratch, scaffolding a new Python package or CLI (with or without docs/PyPI publishing), or retrofitting these conventions onto a young repo.
---

# Bootstrap a New Repo

Scaffold a repo from battle-tested conventions in two layers: a **base** layer every
repo gets (agent docs, Claude Code settings, guard hooks, code search), and a
**python** layer on top for Python packages (uv toolchain, starter package, CI,
plus two opt-in **features** — a Great Docs site and tag-driven PyPI releases).
Templates render deterministically through a script; your judgment goes into
naming, prose, and the follow-up edits — not file copying.

## Terminology

- **Layer** — `base` (all repos) or `python` (implies base).
- **Feature** — a python-only opt-in toggled by `--features`: `docs` (Great Docs site
  + Pages workflow) and `pypi` (trusted-publishing release workflow). Each gates both
  whole files and inline sections of shared files (README, AGENTS, pyproject).
- **Template** — a file under `templates/`; only ever rendered by `scaffold.py`, never hand-copied.
- **Placeholder** — a `{{NAME}}` token in a template, rendered by `scaffold.py` from `--var` inputs.
- **TODO marker** — a `TODO(bootstrap):` line in scaffolded output that you must replace with real prose afterward.

## Layer dispatch

**First decide the layer.** Apply the python layer when the project is Python, uses uv,
or targets PyPI; otherwise scaffold base only. Base always applies. For a non-Python
language, scaffold base only, then write the language-specific STYLEGUIDE rules and
test/CI setup by hand, using `reference/python-stack.md` as the worked example of a
complete layer.

**Then, for python, decide the features.** `docs` and `pypi` are independent opt-ins —
ask the user for each (see Step 1). Omitting `--features` enables both (the default);
pass a subset (or empty) to drop the docs site and/or PyPI release entirely. Don't
scaffold a docs site or release pipeline the user didn't ask for and then strip it by
hand — that's exactly what the feature flags exist to prevent.

## Step 1 — Gather inputs

Run the identity script first (never hardcode or guess author identity):

```bash
bash "${CLAUDE_PLUGIN_ROOT}/skills/repo-bootstrap/scripts/resolve-identity.sh"
```

It prints `AUTHOR_NAME=`, `AUTHOR_EMAIL=`, `GITHUB_USER=` resolved from `git config`
and `gh`. Any value it reports as `MISSING` must come from the user — ask, don't invent.

Then ask the user (one `AskUserQuestion` round) for anything not already clear from
their request:

- **All layers**: project name, one-line description, **layer (Python or not — ask this
  first)**, license (default PolyForm-Noncommercial-1.0.0; MIT for permissive open
  source), extras (`superset`, `env` — see the table below; default none).
- **Python additionally**: dist name, package name, Python floor + pin versions, and
  the two **features** — *docs site?* (Great Docs on GitHub Pages) and *publish to
  PyPI?* (tag-driven trusted-publishing release). Both default to yes when the user has
  no preference; offer them as plain yes/no opt-ins. A private/internal package or a
  one-off script usually wants neither.

**Naming rule (python):** the PyPI dist name must equal the CLI command — short and
memorable. The import package may differ. Worked example: dist + CLI `capt-hook`,
package `captain_hook`, repo `captain-hook`. Before settling on a dist name:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/skills/repo-bootstrap/scripts/check-pypi-name.sh" DIST_NAME
```

(`AVAILABLE` → proceed; `TAKEN` → pick another; `UNKNOWN` → have the user verify.)

### Placeholder reference

| Var | Meaning | Example |
|---|---|---|
| `PROJECT_NAME` | Repo name | `captain-hook` |
| `DESCRIPTION` | One-line description | `Declarative hook framework for Claude Code.` |
| `AUTHOR_NAME` | From resolve-identity.sh | — |
| `AUTHOR_EMAIL` | From resolve-identity.sh | — |
| `GITHUB_USER` | GitHub login | `yasyf` |
| `LICENSE_ID` | SPDX id | `PolyForm-Noncommercial-1.0.0` |
| `DIST_NAME` | PyPI dist == CLI command (python) | `capt-hook` |
| `PACKAGE` | Import package (python) | `captain_hook` |
| `PYTHON_MIN` / `PYTHON_PIN` | Supported floor / dev pin (python) | `3.13` / `3.14` |

Derived automatically: `REPO_URL`, `DOCS_URL` (GitHub Pages), `PY_TARGET`, `YEAR`.

Beyond `--var`, the python layer takes `--features` (comma-separated): `docs`, `pypi`,
or both (the default when the flag is omitted). Pass only what the user wants — e.g.
`--features pypi` for a published package with no docs site, or `--features ""` for
neither. Features are independent of `--var`; they gate files and template sections,
not placeholder values.

## Step 2 — Scaffold (script, never hand-copy)

If the directory is not yet a git repo: `git init -b main` first. Then:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/repo-bootstrap/scripts/scaffold.py" \
  --target . --layer python --extras env --features docs,pypi \
  --var PROJECT_NAME=... --var "DESCRIPTION=..." \
  --var "AUTHOR_NAME=..." --var AUTHOR_EMAIL=... --var GITHUB_USER=... \
  --var LICENSE_ID=PolyForm-Noncommercial-1.0.0 \
  --var DIST_NAME=... --var PACKAGE=... \
  --var PYTHON_MIN=3.13 --var PYTHON_PIN=3.14
```

Set `--features` to match the user's answers: `docs,pypi` (both), `pypi` or `docs`
(one), or `""` (neither). Omitting the flag is the same as `docs,pypi`.

Rules:

- **Never copy from `templates/` by hand** and never leave a `{{...}}` token in the
  repo. The script renders, validates inputs, and fails loudly on leftovers.
- The script is idempotent: identical files are `SKIP`ped; differing files are
  reported as `CONFLICT` and nothing is written (resolve per-file, or `--force`).
- Licenses without a bundled template (bundled: `PolyForm-Noncommercial-1.0.0`,
  `MIT`) print a `MANUAL` line — fetch the text from the SPDX list:
  `curl -fsS https://raw.githubusercontent.com/spdx/license-list-data/main/text/<SPDX-ID>.txt > LICENSE`.
- `--dry-run` previews without writing.

### What lands where

| Destination | Layer | Notes |
|---|---|---|
| `AGENTS.md`, `STYLEGUIDE.md`, `README.md` | base; python **overrides** | python versions carry feature-gated sections (docs badge/section, PyPI badges/install) rendered to match `--features` |
| `CLAUDE.md`, `CHANGELOG.md`, `LICENSE`, `.gitignore` | base | `CLAUDE.md` is just `@AGENTS.md`; `.gitignore` gains python entries when layered |
| `.mcp.json` | base | semble code search via uvx |
| `.claude/settings.json` | base; python **overrides** | hooks wired to `uvx capt-hook run <Event>`; registers the `yasyf/cc-skills` marketplace and enables `codex@skills` |
| `.claude/jj-config.toml` | base | jj VCS config; `settings.json` env points `JJ_CONFIG` at it |
| `.claude/hooks/{__init__,audit,commands,stewardship}.py` | base | guard hooks (see `reference/hooks.md`) |
| `.claude/hooks/{testing,style,toolchain}.py` | python | pytest gate, style rules, ruff/uv guards |
| `pyproject.toml`, `.python-version` | python | `pyproject` gains a `docs` dependency group only with feature `docs` |
| `great-docs.yml`, `docs/scripts/fix_color_swatch.py` | python + feature `docs` | omitted entirely without `docs` |
| `.github/workflows/ci.yml` | python | always |
| `.github/workflows/docs.yml` | python + feature `docs` | Pages docs build |
| `.github/workflows/release-pypi.yml` | python + feature `pypi` | trusted publishing |
| `<PACKAGE>/{__init__,__main__,cli}.py`, `<PACKAGE>/py.typed` | python | Click + loguru starter |
| `tests/{__init__,test_cli}.py` | python | strict CliRunner tests |
| `.superset/config.json` | extra `superset` | worktree bootstrap (env copy, direnv, uv sync on python, jj init + identity) |
| `.env` | extra `env` | `DEBUG=1`; the one local env file, always gitignored |

For python, follow the scaffold with `uv sync --extra dev` (creates `uv.lock` —
commit it) and `uv run pytest`.

## Step 3 — Replace TODO markers

Every `TODO(bootstrap):` marker is judgment work for you. Read the matching
reference before editing:

- `README.md` (pitch, quickstart, why-bullets) and `AGENTS.md` (repository
  structure tree) → read `reference/base-conventions.md` first.
- `great-docs.yml` (navbar color, accent color, hero tagline) → read `reference/docs-site.md` first. *(Only present with feature `docs`.)*
- `<PACKAGE>/cli.py` `hello` command → replace with the first real command (and
  update `tests/test_cli.py` to match).

Find them all: `rg -n 'TODO\(bootstrap\)'`.

## Workflow checklist

Copy this into your task list and check items off as you go.

```
Base (all repos):
- [ ] git repo exists, default branch main
- [ ] resolve-identity.sh run; identity confirmed with user
- [ ] Names + license + layer + extras chosen (python: features + check-pypi-name.sh passed)
- [ ] scaffold.py exited 0 (no CONFLICTs, no leftover {{...}})
- [ ] All TODO(bootstrap) markers replaced with real prose
- [ ] LICENSE present and correct
- [ ] uvx capt-hook test green (hook inline tests; needs capt-hook >= 0.3)
- [ ] Atomic conventional commits made (see Commit plan)
- [ ] Optional: gh repo create --public --source . --push

Python additionally:
- [ ] uv sync --extra dev succeeds; uv.lock committed
- [ ] uv run pytest green
- [ ] verify.sh green (build + wheel smoke + leftover scan)
- [ ] (feature docs) GitHub Pages source set to "GitHub Actions" (reference/ci-and-release.md)
- [ ] (feature pypi) PyPI pending trusted publisher registered for DIST_NAME (reference/ci-and-release.md)
- [ ] (feature pypi) First release flow understood: CHANGELOG entry → tag v0.1.0 → push tag
```

## Commit plan

Atomic, conventional-prefix commits — one logical change each:

1. `chore: scaffold repo conventions (AGENTS, STYLEGUIDE, settings, hooks)`
2. `feat: initial <package> package and CLI skeleton` *(python)*
3. `ci: add CI workflow` *(python; append "docs, and PyPI release workflows" per enabled features)*
4. `docs: README and CHANGELOG` *(append "and Great Docs config" with feature `docs`)*

## Verification

```bash
bash "${CLAUDE_PLUGIN_ROOT}/skills/repo-bootstrap/scripts/verify.sh" --layer python --target .
```

Runs every check and reports `PASS`/`FAIL` per check: leftover-token scan, LICENSE
presence, hook inline tests, and (python) `uv sync` → `pytest` → `uv build` → wheel
smoke test. Fix failures and re-run; never skip a failing check. Remaining
`TODO(bootstrap)` markers are listed as a `NOTE` — clear them before calling the
repo done.

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
  bundled templates; any other SPDX id is fetched from the SPDX list (see Step 2)
  and set in `pyproject.toml`. MIT is the choice for permissive open source
  (see `reference/base-conventions.md`).
- **No capt-hook hooks wanted**: delete `.claude/hooks/` and the `"hooks"` block
  from `.claude/settings.json`.
- **No Codex**: delete the second-opinion nudge at the bottom of
  `.claude/hooks/commands.py`, plus the `"enabledPlugins"` entry (and
  `"extraKnownMarketplaces"` if nothing else uses it) in `.claude/settings.json`.
- **Monorepos**: out of scope — this skill scaffolds single-package repos.

## Reference map

Read these on demand — each is self-contained:

- `reference/base-conventions.md` — AGENTS/CLAUDE/STYLEGUIDE/README/CHANGELOG
  anatomy, commit conventions, license guidance, `.claude` settings explained.
- `reference/python-stack.md` — every python-layer choice with rationale
  (uv/uv_build, flat layout, Click, loguru, pytest, ruff, pyright, naming triad),
  pyproject walkthrough.
- `reference/hooks.md` — what each scaffolded hook does, testing with
  `uvx capt-hook test`, tailoring and removal, version requirements.
- `reference/ci-and-release.md` — the three workflows, one-time PyPI trusted
  publisher + GitHub Pages setup, release procedure.
- `reference/docs-site.md` — Great Docs config, build/preview commands, enabling
  narrative sections and curated reference.
