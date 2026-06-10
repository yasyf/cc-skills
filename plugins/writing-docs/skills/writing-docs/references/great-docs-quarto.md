# Great Docs and Quarto addendum

Stack specifics for a project documented with Great Docs, which renders a Quarto site from `.qmd` pages and a generated API reference.

## Site config

- `great-docs.yml` holds the site config, including `module`, `sections` where each names a directory under `docs/`, `hero`, `navbar_color`, `accent_color`, `cli`, and `pre_render` scripts.
- The curated symbol reference comes from the package root `__init__.py` re-exports, which often carry no `__all__`. The auto-generated symbol pages live under the generated `reference/` directory; hand-written cheatsheets live under `docs/reference/`. Keep the two distinct and link between them.
- Build green with `uv run great-docs build` after `uv sync --group docs`. Set `GITHUB_TOKEN` locally so the GitHub-Releases changelog page does not skip on rate limits.

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
