"""Verify a scaffolded repo. Runs every check, reports PASS/FAIL per check, and
exits 1 if any failed. The python layer adds sync/test/build/wheel-smoke.

Ports the former ``verify.sh`` and fixes its two bugs: each check captures its own
output (no shared ``/tmp/verify-check.log`` clobbered across checks), and the wheel
smoke test captures an explicit return code instead of ``$?`` after ``&&``.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
from collections.abc import Callable, Iterator
from pathlib import Path

from .common import PLACEHOLDER, run

# Output of a single check: (passed, captured combined stdout+stderr).
CheckFn = Callable[[], "tuple[bool, str]"]

_TODO_MARKER = "TODO(bootstrap)"
_NAME_RE = re.compile(r'^name = "(.*)"$')


def _walk_files(skip_dirs: set[str]) -> Iterator[Path]:
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for name in files:
            yield Path(root) / name


def _grep(skip_dirs: set[str], predicate: Callable[[str], bool]) -> list[str]:
    hits: list[str] = []
    for path in _walk_files(skip_dirs):
        try:
            text = path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if predicate(line):
                hits.append(f"{path}:{lineno}:{line}")
    return hits


def _run_cmd(cmd: list[str]) -> tuple[bool, str]:
    proc = run(cmd)
    return proc.returncode == 0, proc.stdout + proc.stderr


def _no_leftover_tokens() -> tuple[bool, str]:
    hits = _grep({".git", ".venv", "dist", "great-docs"}, lambda line: bool(PLACEHOLDER.search(line)))
    return not hits, "\n".join(hits)


def _license_check(no_license: bool) -> tuple[bool, str]:
    path = Path("LICENSE")
    if no_license:
        ok = not (path.exists() or path.is_symlink())
        return ok, "" if ok else "LICENSE exists but license `none` was chosen — delete it, or re-scaffold with a real SPDX id"
    ok = path.is_file() and path.stat().st_size > 0
    return ok, "" if ok else "LICENSE is missing or empty"


def _missing_banner_note() -> str | None:
    """NOTE when the README references the banner Phase 3 generates but it's absent.

    Keyed off the README reference so it stays silent after the no-brand-images
    escape hatch removes the line."""
    readme = Path("README.md")
    if not readme.is_file() or "readme-banner.png" not in readme.read_text():
        return None
    if Path("docs/assets/readme-banner.png").is_file():
        return None
    return (
        "README references docs/assets/readme-banner.png but the file is missing"
        " — generate it (brand-images phase, scripts/genimages.py) or remove the reference"
    )


def _hook_tests() -> tuple[bool, str]:
    if not shutil.which("uvx"):
        return False, "uvx not found — install uv: https://docs.astral.sh/uv/"
    ok, output = _run_cmd(
        ["uvx", "--from", "capt-hook", "python", "-c",
         "from captain_hook.util.model_cache import ensure_spacy_model; ensure_spacy_model()"]
    )
    if not ok:
        return False, output
    ok2, output2 = _run_cmd(["uvx", "capt-hook", "test"])
    return ok2, output + output2


def _prek_config() -> tuple[bool, str]:
    if not shutil.which("uvx"):
        return False, "uvx not found — install uv: https://docs.astral.sh/uv/"
    # prepare-hooks parses .pre-commit-config.yaml and resolves/builds the pinned
    # ruff-pre-commit and ty-pre-commit revs — catching a malformed config or a
    # non-existent rev (the failure that would break every contributor's commits).
    # Needs a git repo (Phase 0 guarantees one) but no tracked files or commits.
    return _run_cmd(["uvx", "prek", "prepare-hooks"])


def _wheel_smoke() -> tuple[bool, str]:
    dist_name = ""
    for line in Path("pyproject.toml").read_text().splitlines():
        if match := _NAME_RE.match(line):
            dist_name = match.group(1)
            break
    if not dist_name:
        return False, "could not read project name from pyproject.toml"

    shutil.rmtree(".wheel-smoke", ignore_errors=True)
    output = ""
    try:
        steps = [["uv", "venv", "--seed", ".wheel-smoke"]]
        steps.append(["uv", "pip", "install", "--python", ".wheel-smoke/bin/python", *sorted(glob.glob("dist/*.whl"))])
        steps.append([f".wheel-smoke/bin/{dist_name}", "--help"])
        for cmd in steps:
            ok, captured = _run_cmd(cmd)
            output += captured
            if not ok:
                return False, output
        return True, output
    finally:
        shutil.rmtree(".wheel-smoke", ignore_errors=True)


def main(layer: str, target: str, no_license: bool) -> int:
    os.chdir(target)
    failures = 0

    def check(name: str, fn: CheckFn) -> None:
        nonlocal failures
        ok, output = fn()
        if ok:
            print(f"PASS  {name}")
            return
        print(f"FAIL  {name}")
        for line in output.splitlines()[-30:]:
            print(f"      {line}")
        failures += 1

    check("no unrendered {{...}} tokens", _no_leftover_tokens)
    license_label = "LICENSE absent (license none)" if no_license else "LICENSE present"
    check(license_label, lambda: _license_check(no_license))
    check("hook inline tests (uvx capt-hook test)", _hook_tests)

    todos = _grep({".git"}, lambda line: _TODO_MARKER in line)
    if todos:
        print("NOTE  TODO(bootstrap) markers remain (replace them with real prose):")
        for hit in todos:
            print(f"      {hit}")

    if banner_note := _missing_banner_note():
        print(f"NOTE  {banner_note}")

    if layer == "python":
        check("pre-commit hook config (uvx prek prepare-hooks)", _prek_config)
        check("uv sync --extra dev", lambda: _run_cmd(["uv", "sync", "--extra", "dev"]))
        check("uv run pytest", lambda: _run_cmd(["uv", "run", "pytest"]))
        check("uv build", lambda: _run_cmd(["uv", "build"]))
        check("wheel smoke test", _wheel_smoke)

    if failures:
        print(f"{failures} check(s) failed")
        return 1
    print("All checks passed")
    return 0
