# Great Docs and Quarto addendum

Stack specifics for a project documented with Great Docs, which renders a Quarto site from `.qmd` pages and a generated API reference.

## Site config

- `great-docs.yml` holds the site config, including `module`, `sections` where each names a directory under `docs/`, `hero`, `navbar_color`, `accent_color`, `cli`, and `pre_render` scripts.
- The curated symbol reference comes from the package root `__init__.py` re-exports, which often carry no `__all__`. The auto-generated symbol pages live under the generated `reference/` directory; hand-written cheatsheets live under `docs/reference/`. Keep the two distinct and link between them.
- Build green with `uv run --with "git+https://github.com/yasyf/cc-skills@main#subdirectory=tools/gd-build" gd-build build` after `uv sync --group docs` — the exact command docs CI runs; it applies the fleet's perf patches and materializes the `pre_render` titles fix into the gitignored `docs/scripts/.gd-build/`. Never bare `great-docs build`: without the materialized script a large API reference makes the render crawl for an hour (pandoc #11687 — repo-bootstrap's `reference/docs-site.md` has the details). Set `GITHUB_TOKEN` locally so the GitHub-Releases changelog page does not skip on rate limits.

## Landing page

- The `hero` tagline in `great-docs.yml` is the opener fragment, verbatim — one of the five surfaces in `readme.md`'s one-fragment contract.
- `index.qmd` shows the one get-started command and the demo image above the fold, before any feature grid.
- At most three use-case cards, each linking to a how-to.
- The homepage funnels, never documents: every block links deeper, and none carries the full detail it points to.

## Walkthrough pages

The getting-started tutorial and any multi-step how-to follow one contract:

- The title is a promise plus a time budget: "Write your first hook in under five minutes".
- Show the end state first, before step 1, so the reader sees where they land.
- Number the steps. Every two or three steps, a checkpoint shows the exact expected output at that point:

  ```markdown
  ::: {.callout-tip title="Checkpoint"}
  `uvx tool status` now prints `3 hooks active`.
  :::
  ```

- A checkpoint may carry at most one grounded recovery line ("An empty table means the daemon isn't running — `uvx tool up`"). This is the one scoped carve-out from the no-pre-emptive-admonishment rule: name the single likely failure and its fix, flat, and stop.
- End with the verified outcome and next-steps links.
- Screenshot and demo generators live in `docs/scripts/`, committed, so the shown output regenerates instead of drifting.

## Docstrings

- Write Google-style docstrings on the public API only. That covers user-facing classes, primitives, and the types that render into the reference. Internal helpers get none.
- A docstring that restates the signature is clutter. Delete it.
- An `Example:` block in a public docstring renders on the site, so make it real and runnable.

## Pages

- Every `.qmd` starts with front matter holding at least a `title`.
- Each section's `index.qmd` states which Diataxis mode its pages are.
- End a how-to or tutorial with a "Next steps" or "See also" list of relative links.
- `.qmd` is not auto-detected by slop-cop, so always pass `--lang=markdown`.

## Single-source example code with gd-embed

Keep example code in real, tested `.py` files and inject it into pages at pre-render, so the rendered page and the tested code cannot drift.

- A page holds a marker like `<!-- gd-embed: name.py -->`, and the pre-render script at `docs/scripts/embed_examples.py` replaces it with the fenced source of `docs/examples/name.py`.
- When a snippet must appear verbatim on more than one page, such as a hero example or a canonical command, author it once in a fragments directory and inject it with a sibling marker, so the pages cannot diverge.
- Run the example tests in CI, which for capt-hook projects means `uvx capt-hook --hooks docs/examples test`, so a broken snippet fails the build.

## Command convention

State the run convention once at the top of the install page and use it verbatim everywhere. For a tool distributed through uvx, every command is the full `uvx <tool> ...` form. Only the generated settings or config file holds the bare command, so call that exception out where it lands.
