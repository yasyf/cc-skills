"""The gd-build CLI: apply the patches, delegate to great-docs, fix the swatches.

`gd-build build` materializes the pre_render titles script, applies the
performance patches, delegates to `great-docs build`, then — on success only —
rewrites the color-swatch loaders in the built site. `gd-build selftest` reports
whether every selected patch applies, exiting 3 if any was skipped.

Why this exists (condensed; the full decision matrix is cc-notes doc 98b1683):

- Consumed at CI time as `uv run --with "git+https://github.com/yasyf/cc-skills@main\
#subdirectory=tools/gd-build" gd-build build` from a repo whose project venv holds
  great-docs 0.15.x — never uvx, whose isolation would hide the host great-docs.
- Patches rebind `great_docs._apiref.introspect` (great-docs core imports get_object
  function-locally, so patching core never fires). Each patch is gated on the exact
  internals it rebinds and degrades to a stock build; `GD_BUILD_PATCHES=none` is the
  per-run kill-switch. Measured: API discovery 331.75s -> 2.32s quiet.
- Titles materialize into the gitignored `docs/scripts/.gd-build/` because great-docs
  `pre_render` accepts file paths only (they are copied into the Quarto staging dir).
- The swatch pass no-ops on great-docs >=0.15 (upstream's quarto:offset loader is
  depth-correct); it exists for 0.14.x repos and retires with them.
- The whole tool self-retires: when upstream ships the shared-loader fix, the gates
  report UNPATCHED and every build runs stock — loudly, never brokenly.
"""

from __future__ import annotations

import importlib.resources
import sys
from pathlib import Path

from gd_build import swatch
from gd_build.patches import apply_patches

TITLES_DEST = Path("docs/scripts/.gd-build/native_reference_titles.py")


def materialize_titles() -> None:
    TITLES_DEST.parent.mkdir(parents=True, exist_ok=True)
    TITLES_DEST.write_text(importlib.resources.files("gd_build").joinpath("titles.py").read_text())


def exit_code(value: object) -> int:
    match value:
        case None:
            return 0
        case int():
            return value
        case _:
            return 1


def delegate(rest: list[str]) -> int:
    from great_docs.cli import main

    sys.argv = ["great-docs", "build", *rest]
    try:
        return exit_code(main())
    except SystemExit as exc:
        if not isinstance(exc.code, int | None):
            print(exc.code, file=sys.stderr)
        return exit_code(exc.code)


def build(rest: list[str]) -> int:
    materialize_titles()
    apply_patches()
    if (code := delegate(rest)) != 0:
        return code
    swatch.fix_swatches(swatch.SITE_DIR)
    return 0


def main() -> None:
    match sys.argv[1:]:
        case ["selftest"]:
            raise SystemExit(0 if all(apply_patches().values()) else 3)
        case ["build", *rest]:
            raise SystemExit(build(rest))
        case _:
            print("usage: gd-build {build [args...] | selftest}", file=sys.stderr)
            raise SystemExit(2)


if __name__ == "__main__":
    main()
