"""The gd-build CLI: apply the patches, then delegate to great-docs.

`gd-build build` materializes the pre_render titles script and the fleet
design-system CSS, applies the performance patches, delegates to `great-docs
build`, then ranks the rendered search index. `gd-build selftest` reports
whether every selected patch applies, exiting 3 if any was skipped. `build`
supervises that work in a child process under a hard wall-clock cap (default
300s, GD_BUILD_TIMEOUT overrides in seconds, 0 disables), killing the process
tree and exiting 124 on timeout.

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
- The color-swatch fixup pass retired 2026-07-17: great-docs >=0.15 ships its own
  depth-correct `quarto:offset` loader, and no fleet repo pins <0.15 any more.
- The whole tool self-retires: when upstream ships the shared-loader fix, the gates
  report UNPATCHED and every build runs stock — loudly, never brokenly.
"""

from __future__ import annotations

import importlib.resources
import os
import signal
import subprocess
import sys
from pathlib import Path

from gd_build.fleet_assets import materialize_fleet_css
from gd_build.patches import apply_patches
from gd_build.search_rank import apply_search_ranking

TITLES_DEST = Path("docs/scripts/.gd-build/native_reference_titles.py")
SITE_DIR = Path("great-docs") / "_site"


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


def build_inprocess(rest: list[str]) -> int:
    materialize_titles()
    materialize_fleet_css()
    apply_patches()
    code = delegate(rest)
    if code == 0:
        apply_search_ranking(SITE_DIR)
    return code


class Interrupted(Exception):
    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


def terminate(child: subprocess.Popen) -> None:
    if child.poll() is not None:
        return
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait()


def wait_for_child(child: subprocess.Popen, cap: int) -> int:
    try:
        return child.wait() if cap == 0 else child.wait(timeout=cap)
    except subprocess.TimeoutExpired:
        line = (
            f"TIMEOUT: docs build exceeded the {cap}s hard cap — killing the quarto process tree. "
            "Profile the build or trim the API reference (repo-bootstrap reference/docs-site.md); "
            "GD_BUILD_TIMEOUT=<secs> overrides, 0 disables."
        )
        print(line, file=sys.stderr)
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::error::{line}")
        return 124


def supervise(argv: list[str], cap: int) -> int:
    child = subprocess.Popen(argv, start_new_session=True)

    def relay(signum: int, _frame: object) -> None:
        raise Interrupted(signum)

    previous = {sig: signal.signal(sig, relay) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        return wait_for_child(child, cap)
    except Interrupted as exc:
        raise SystemExit(128 + exc.signum) from None
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        terminate(child)


def build(rest: list[str]) -> int:
    cap = int(os.environ.get("GD_BUILD_TIMEOUT", "300"))
    return supervise([sys.executable, "-m", "gd_build", "_build", *rest], cap)


def main() -> None:
    match sys.argv[1:]:
        case ["selftest"]:
            raise SystemExit(0 if all(apply_patches().values()) else 3)
        case ["_build", *rest]:
            raise SystemExit(build_inprocess(rest))
        case ["build", *rest]:
            raise SystemExit(build(rest))
        case _:
            print("usage: gd-build {build [args...] | selftest}", file=sys.stderr)
            raise SystemExit(2)


if __name__ == "__main__":
    main()
