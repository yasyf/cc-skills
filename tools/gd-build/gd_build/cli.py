"""The gd-build CLI: apply the patches, delegate to great-docs, fix the swatches.

`gd-build build` materializes the pre_render titles script, applies the
performance patches, delegates to `great-docs build`, then — on success only —
rewrites the color-swatch loaders in the built site. `gd-build selftest` reports
whether every selected patch applies, exiting 3 if any was skipped.
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
