# Great Docs: the scaffolded docs site

## What it is

[Great Docs](https://posit-dev.github.io/great-docs/) is a Quarto-based docs generator,
installed from PyPI as `great-docs` via the `[dependency-groups].docs` group in
`pyproject.toml` (`great-docs>=0.13`). It was chosen over mkdocs/Read the Docs because it
generates the API reference dynamically from docstrings with zero nav maintenance, takes a
single YAML config, and deploys to GitHub Pages.

Commands (Quarto CLI must be on PATH; CI installs it via `quarto-dev/quarto-actions/setup@v2`):

```bash
uv sync --group docs        # install great-docs
uv run great-docs build     # output lands in great-docs/_site
uv run great-docs preview   # live-reloading local preview
```

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
| `pypi` | `true` | Renders the install widget |
| `github_style` | `widget` | GitHub repo widget in the navbar |
| `jupyter` | `python3` | Kernel for executable code blocks |
| `navbar_style` / `content_style` | `lilac` | Theme presets |
| `accent_color` | `light: "#ffb300"` / `dark: "#ffca28"` | Marked `TODO(bootstrap)` — replace with colors that fit the project's brand |
| `dark_mode_toggle` / `back_to_top` / `keyboard_nav` | `true` | UX toggles, leave on |

**`hero`** — landing-page banner: `name` (project name), `tagline` (the one-line description
supplied at scaffold time, e.g. captain-hook's "Declarative hooks for Claude Code — rules as
data, tested inline."), and `starfield: true`. Tighten the tagline when the README pitch firms up.

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
`ContentBlock`, `InlineTests`, and `TCondition` because they are cyclic aliases that break
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
(`uv sync --group docs` → `uv run great-docs build` → upload `great-docs/_site` with
`include-hidden-files: true`), then a `publish-docs` job gated on
`if: github.ref == 'refs/heads/main'` deploys via `actions/deploy-pages` to the
`github-pages` environment. Enable Pages in the repo settings with source "GitHub Actions"
or the deploy job fails.

The site lives at `https://<user>.github.io/<repo>/` (captain-hook:
`https://yasyf.github.io/captain-hook/`). Three places point there and must agree:
`site_url` in `great-docs.yml`, `[project.urls].Documentation` in `pyproject.toml`, and the
README docs badge (`.../actions/workflow/status/<user>/<repo>/docs.yml?branch=main&label=docs`,
linked to the docs URL).
