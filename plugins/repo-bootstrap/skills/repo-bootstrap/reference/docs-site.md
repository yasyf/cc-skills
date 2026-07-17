# Great Docs: the scaffolded docs site

This whole layer is the **`docs` feature** — it exists only when the repo was scaffolded with
`--features docs` (the default includes it). Without that feature there is no `great-docs.yml`,
no `docs.yml` workflow, and no `docs` dependency group; skip this reference.

## What it is

[Great Docs](https://posit-dev.github.io/great-docs/) is a Quarto-based docs generator,
installed via the `[dependency-groups].docs` group in `pyproject.toml`. It was chosen over
mkdocs/Read the Docs because it generates the API reference dynamically from docstrings with
zero nav maintenance, takes a single YAML config, and deploys to GitHub Pages.

The docs group pins **`great-docs>=0.15,<0.16`** plus `griffelib>=2.0`. The `<0.16` cap keeps
new repos inside gd-build's patch-gate window (the fleet's build wrapper gates its perf patches
on 0.15.x); 0.15 carries the build-time GitHub widget stats (embedded at build time via the CI
`GITHUB_TOKEN`, so the navbar widget makes no client-side API calls that would 403 on the live
site); and `griffelib` is the modern griffe 2.x distribution that great-docs' module layout
needs, overriding the stale `griffe<2` pin great-docs still declares.

Commands (Quarto CLI must be on PATH; CI installs it via `quarto-dev/quarto-actions/setup@v2`):

```bash
uv sync --group docs        # install great-docs
uv run --with "git+https://github.com/yasyf/cc-skills@main#subdirectory=tools/gd-build" gd-build build
                            # the exact CI build — output lands in great-docs/_site
uv run great-docs preview   # live-reloading local preview (needs a prior gd-build run,
                            # which materializes the pre_render titles script)
```

Never build with bare `uv run great-docs build`: the `pre_render` entry points at the
gitignored `docs/scripts/.gd-build/native_reference_titles.py`, which only exists after a
gd-build run. great-docs warns "Pre-render script not found" and continues anyway — into an
unpatched build that can hang for an hour on a large API reference (pandoc #11687).

`great-docs/` is build output — it is listed in `.gitignore`; never commit it.

## great-docs.yml walkthrough

All values below come from the scaffolded `great-docs.yml`, filled with what Claude supplied
at scaffold time (worked example: project `captain-hook`, package `captain_hook`).

| Key | Scaffolded value | Meaning |
|---|---|---|
| `module` | the import package (`captain_hook`) | Root module to introspect |
| `display_name` | the project name (`captain-hook`) | Site/page title |
| `parser` | `google` | Docstring style — matches the STYLEGUIDE's Google-style mandate |
| `dynamic` | `true` | Auto-generate the API reference from the module's docstrings |
| `repo` / `site_url` | the repo URL / docs URL | Source links and canonical URL |
| `pypi` | follows feature `pypi` | Renders the install widget; `false` without a PyPI release |
| `github_style` | `widget` | GitHub repo widget in the navbar (stars/forks; the build-time stats need the CI `GITHUB_TOKEN` to avoid 403s) |
| `jupyter` | `python3` | Kernel for executable code blocks |
| `navbar_color` | `"#1e293b"` | Solid navbar color, marked `TODO(bootstrap)` — text contrast is auto-chosen |
| `accent_color` | `"#3b82f6"` | Single accent (both modes), marked `TODO(bootstrap)` — replace with a brand color; the mascot's dominant color is a good source |
| `dark_mode_toggle` / `back_to_top` / `keyboard_nav` | `true` | UX toggles, leave on |

These are **conservative defaults on purpose**: a solid navbar and one accent, no gradient
presets (`navbar_style`/`content_style`) and no hero `starfield`. The gradient/starfield extras
read as busy, and the starfield's full-viewport canvas intercepts homepage clicks — leave them
off unless a project explicitly wants the flair.

**`hero`** — landing-page banner: `name` (project name) and `tagline`. The tagline IS the
README opener fragment — the writing-docs opener register (`references/readme.md`), one
fragment on every surface — and must match what `gh repo edit --description` sets.

The landing page funnels like the README: when you add an `index.qmd`, it leads with the
get-started command and the demo above the fold. Tutorial pages follow the walkthrough
contract in the writing-docs skill's `references/great-docs-quarto.md` — pointers, not a
restated spec.

**`cli`** — renders `--help` docs for the click CLI:

```yaml
cli:
  enabled: true
  module: captain_hook.cli   # the package's cli module
  name: cli
```

`name` is the attribute holding the click group inside that module. The scaffolded
`<package>/cli.py` names its group `main`; great-docs falls back through common attribute
names (`cli`, `main`, `app`, `command`) so it resolves either way, but set `name: main` (or
rename the group) for an exact match.

**`authors`** — `name`/`role: Maintainer`/`github`/`email` from the values supplied at
scaffold time.

## Logo & favicon (auto-detected)

Bootstrap drops the generated mascot at `docs/assets/logo.png` (SKILL.md Brand
images phase), which Great Docs auto-detects — no `great-docs.yml` key needed.
Detection priority: `logo.svg|png` in the repo root → `assets/logo.*` →
`docs/assets/logo.*` → `{package}_logo.*`. A `logo-dark.*` sibling supplies a
dark-mode variant, favicons are generated from the logo automatically, and the
hero auto-enables once a logo is detected (`logo-hero.*` in the root or `assets/`
overrides the hero image specifically). Keep the logo transparent — the navbar is
a solid dark color. Don't leave a stray `logo.*` in the repo root or `assets/`;
it would shadow the mascot.

## The two commented-out optional blocks

**`sections:`** — narrative doc directories. Dynamic mode needs none of these; uncomment as
real narrative pages appear under `docs/`. Each entry takes `title`, `dir`, optional
`navbar_after` (ordering), `index: true` (auto-index page), and `index_columns`.
captain-hook grew into five: `docs/getting-started`, `docs/guide`, `docs/examples`,
`docs/reference`, `docs/development`, chained with `navbar_after`. Start with
`Getting Started` only, when the first real page exists.

**`reference:`** — curated symbol groups for the API reference. Without it, dynamic mode
documents everything exported in the package's `__all__`, flat. With it, you control grouping,
ordering, and descriptions — and it doubles as an exclusion mechanism: captain-hook omits
`InlineTests` and `TCondition` because they are cyclic aliases that break
dynamic introspection. The shape, from captain-hook's real config:

```yaml
reference:
  - title: Registration
    desc: Declaring and registering hooks.
    contents:
      - hook
      - on
      - register

  - title: Conditions
    desc: Typed filters that decide when hooks fire.
    contents:
      - Tool
      - FilePath
      - RanCommand
```

Names in `contents` are bare symbol names resolved against `module`. Add this block once the
public API has more than a handful of exports; group by how a user thinks (entry points first,
plumbing last), with a one-line `desc` per group.

## Docstring policy interplay

The site renders only what the STYLEGUIDE permits: Google-style docstrings on the public API
surface; internal helpers get none. Consequences for editing:

- Keep `__all__` accurate in the package's `__init__.py` — dynamic mode reads it. The scaffold
  starts with `__all__: list[str] = []`, so the reference is empty until you export symbols.
- A new public symbol needs both an `__all__` entry and a Google-style docstring, or it renders
  as an undocumented stub.
- `Example:` sections with `>>>` blocks render on the site; they are the cheapest usage docs.

## Publishing

`.github/workflows/docs.yml` builds on every push to `main` and every pull request
(`uv sync --group docs` → `uv run --with "git+https://github.com/yasyf/cc-skills@main#subdirectory=tools/gd-build" gd-build build`
→ upload `great-docs/_site` with `include-hidden-files: true`), then a `publish-docs` job gated
on `if: github.ref == 'refs/heads/main'` deploys via `actions/deploy-pages` to the
`github-pages` environment. Enable Pages with the Actions build first
(`gh api repos/{owner}/{repo}/pages -X POST -f build_type=workflow`) or the deploy job fails.

Two non-obvious build details:

- The build step sets `env: GITHUB_TOKEN: ${{ github.token }}` — great-docs embeds the navbar
  widget's star/fork counts at build time using it, so visitors' browsers never hit the
  GitHub API. Drop the token and the widget falls back to client-side calls that 403.
- `docs/scripts/.gd-build/native_reference_titles.py` runs *before* the render (a `pre_render:`
  entry in `great-docs.yml`, supplied by the `cc-skills:great-docs-prerender` fragment). gd-build
  materializes the script into that gitignored directory on every build — it is not committed.
  Quarto re-renders the API-reference sidebar into every page, and Pandoc's emphasis resolver
  backtracks exponentially on the `__dunder__` candidates inside the bracketed-span titles
  (jgm/pandoc#11687) — a large reference can drag the build out to an hour or more. The script
  rewrites each generated `[Name]{.doc-*}` title to a pre-parsed `` `Span (…)`{=pandoc-native} ``
  inline: linear to parse, styled identically (the kind pills survive). It is a no-op until you
  add a `reference:` section; the fragment drops once upstream fixes the backtracking.

The site lives at `https://<user>.github.io/<repo>/` (captain-hook:
`https://yasyf.github.io/captain-hook/`). Three places point there and must agree:
`site_url` in `great-docs.yml`, `[project.urls].Documentation` in `pyproject.toml`, and the
README docs badge (`.../actions/workflow/status/<user>/<repo>/docs.yml?branch=main&label=docs`,
linked to the docs URL).
