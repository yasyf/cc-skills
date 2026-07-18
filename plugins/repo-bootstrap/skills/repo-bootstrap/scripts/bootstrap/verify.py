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
_SWIFT_EXECUTABLE_RE = re.compile(r'\.executable\(\s*name:\s*"([^"]+)"')
# Xcode's "download the platform" refusal: an environment gap, not a code failure.
_XCODE_PLATFORM_MISSING = "Please download and install the platform"


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
    # .build holds SPM dependency checkouts, which may legitimately contain {{...}}.
    hits = _grep({".git", ".venv", "dist", "great-docs", ".build"}, lambda line: bool(PLACEHOLDER.search(line)))
    return not hits, "\n".join(hits)


def _license_check(no_license: bool) -> tuple[bool, str]:
    path = Path("LICENSE")
    if no_license:
        ok = not (path.exists() or path.is_symlink())
        return ok, "" if ok else "LICENSE exists but license `none` was chosen — delete it, or re-scaffold with a real SPDX id"
    ok = path.is_file() and path.stat().st_size > 0
    return ok, "" if ok else "LICENSE is missing or empty"


def _banner_file() -> Path | None:
    """The on-disk banner, whichever extension this repo has (webp, or legacy png)."""
    for name in ("readme-banner.webp", "readme-banner.png"):
        path = Path("docs/assets") / name
        if path.is_file():
            return path
    return None


def _missing_banner_note() -> str | None:
    """NOTE when the README references the banner Phase 3 generates but it's absent.

    Keyed off the README reference so it stays silent after the no-brand-images
    escape hatch removes the line."""
    readme = Path("README.md")
    if not readme.is_file() or "readme-banner." not in readme.read_text():
        return None
    if _banner_file() is not None:
        return None
    return (
        "README references docs/assets/readme-banner but the file is missing"
        " — generate it (brand-images phase, gen-image skill brand pipeline) or remove the reference"
    )


_DEMO_REF_RE = re.compile(r"docs/assets/(demo\.[a-z0-9]+)")
_DEMO_GENERATORS = ("docs/scripts/demo.sh", ".cli-demo/demo.tape")


def _missing_demo_note() -> str | None:
    """NOTE when the README's demo slot is dangling: the referenced asset is
    missing, or the asset has no committed generator (so it can't be
    regenerated). Keyed off the README reference so it stays silent after the
    no-terminal-demo escape hatch replaces the img with a fenced output block."""
    readme = Path("README.md")
    if not readme.is_file():
        return None
    match = _DEMO_REF_RE.search(readme.read_text())
    if match is None:
        return None
    asset = Path("docs/assets") / match.group(1)
    if not asset.is_file():
        return (
            f"README references {asset} but the file is missing"
            " — record the demo (Phase 4 demo step: freeze via docs/scripts/demo.sh, or the cli-demo skill)"
            " or apply the no-terminal-demo escape hatch"
        )
    if any(Path(gen).is_file() for gen in _DEMO_GENERATORS):
        return None
    return (
        f"{asset} has no committed generator — commit docs/scripts/demo.sh (freeze)"
        " or .cli-demo/demo.tape (cli-demo) so the demo can be regenerated"
    )


def _missing_social_note() -> str | None:
    """NOTE when the banner exists but the social card doesn't (pre-social-card repo).

    Keyed off the banner file so it stays silent after the no-brand-images
    escape hatch."""
    if _banner_file() is None:
        return None
    if Path("docs/assets/social-preview.jpg").is_file():
        return None
    return (
        "docs/assets/social-preview.jpg is missing — generate it"
        " (gen-image skill: brand --from-logo) so the GitHub social preview can be set"
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


def _go_test_cmd() -> list[str]:
    """The race-suite command, routed through scripts/test.sh when the repo ships
    it. That harness caps RLIMIT_NPROC so a daemonkit proc.Spawn path that execs a
    test binary hits EAGAIN instead of fork-bombing the machine; bare `go test` is
    that exact fork-bomb class, so it is used only when no harness is present."""
    script = Path("scripts/test.sh")
    if script.is_file():
        return ["bash", str(script), "-race", "./..."]
    return ["go", "test", "-race", "./..."]


def _go_binary_smoke() -> tuple[bool, str]:
    """Build the first cmd/<name>/ binary to a temp dir and run it with --help.

    The go analogue of the python wheel smoke test: it proves the starter CLI
    compiles and runs end to end, not just that the package builds."""
    cmd_dirs = sorted(p for p in glob.glob("cmd/*") if Path(p).is_dir())
    if not cmd_dirs:
        return False, "no cmd/<name>/ directory found to build"
    name = Path(cmd_dirs[0]).name
    shutil.rmtree(".go-smoke", ignore_errors=True)
    output = ""
    try:
        Path(".go-smoke").mkdir()
        binpath = f".go-smoke/{name}"
        ok, captured = _run_cmd(["go", "build", "-o", binpath, f"./cmd/{name}"])
        output += captured
        if not ok:
            return False, output
        ok2, captured2 = _run_cmd([f"./{binpath}", "--help"])
        return ok2, output + captured2
    finally:
        shutil.rmtree(".go-smoke", ignore_errors=True)


def _swift_binary_smoke() -> tuple[bool, str]:
    """Run the SPM executable with --help — the swift analogue of the go smoke.

    ArgumentParser exits 0 on --help, so this proves the starter CLI links and
    runs end to end, not just that the package compiles."""
    match = _SWIFT_EXECUTABLE_RE.search(Path("Package.swift").read_text())
    if not match:
        return False, "could not find an .executable product in Package.swift"
    return _run_cmd(["swift", "run", match.group(1), "--help"])


def _xcodebuild_usable() -> bool:
    """True when a real Xcode is selected. NOT shutil.which: CLT-only Macs ship a
    /usr/bin/xcodebuild stub that errors 'requires Xcode'."""
    return run(["xcodebuild", "-version"]).returncode == 0


def _app_project_name() -> str:
    projects = sorted(glob.glob("*.xcodeproj"))
    return Path(projects[0]).stem if projects else ""


def _swift_lint_checks(check: Callable[[str, CheckFn], None]) -> None:
    """swiftformat + swiftlint, NOTE-skipped when absent (the golangci pattern:
    CI and the commit hook still run them)."""
    if shutil.which("swiftformat"):
        check("swiftformat --lint .", lambda: _run_cmd(["swiftformat", "--lint", "."]))
    else:
        print("NOTE  swiftformat not installed — skipping format check (CI and the commit hook run it; brew install swiftformat)")
    if shutil.which("swiftlint"):
        check("swiftlint", lambda: _run_cmd(["swiftlint", "--quiet"]))
    else:
        print("NOTE  swiftlint not installed — skipping lint check (CI and the commit hook run it; brew install swiftlint)")


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

    if social_note := _missing_social_note():
        print(f"NOTE  {social_note}")

    if demo_note := _missing_demo_note():
        print(f"NOTE  {demo_note}")

    if layer == "python":
        check("pre-commit hook config (uvx prek prepare-hooks)", _prek_config)
        check("uv sync --extra dev", lambda: _run_cmd(["uv", "sync", "--extra", "dev"]))
        check("uv run pytest", lambda: _run_cmd(["uv", "run", "pytest"]))
        check("uv build", lambda: _run_cmd(["uv", "build"]))
        check("wheel smoke test", _wheel_smoke)

    if layer == "go":
        check("go vet ./...", lambda: _run_cmd(["go", "vet", "./..."]))
        if shutil.which("golangci-lint"):
            check("golangci-lint run", lambda: _run_cmd(["golangci-lint", "run"]))
        else:
            print("NOTE  golangci-lint not installed — skipping lint check (CI and the commit hook run it)")
        check("go build ./...", lambda: _run_cmd(["go", "build", "./..."]))
        go_test = _go_test_cmd()
        label = "scripts/test.sh -race ./..." if go_test[0] == "bash" else "go test -race ./..."
        check(label, lambda: _run_cmd(go_test))
        # The go library escape hatch (delete cmd/, expose packages at the module
        # root) leaves no binary to smoke — NOTE-skip rather than FAIL.
        if any(Path(p).is_dir() for p in glob.glob("cmd/*")):
            check("binary smoke test", _go_binary_smoke)
        else:
            print("NOTE  no cmd/<name>/ binary (library repo — the go library escape hatch); skipping binary smoke test")

    if layer == "swift":
        check("swift build", lambda: _run_cmd(["swift", "build"]))
        check("swift test", lambda: _run_cmd(["swift", "test"]))
        _swift_lint_checks(check)
        check("binary smoke test (swift run --help)", _swift_binary_smoke)

    if layer == "swift-app":
        _swift_lint_checks(check)
        if _xcodebuild_usable():
            name = _app_project_name()
            # generic simulator destination: compiles everything with no booted
            # simulator, no named device, and no signing. Run once, then decide:
            # a refusal because the iOS platform component isn't downloaded is an
            # environment gap (NOTE), not a scaffold failure.
            ok, output = _run_cmd([
                "xcodebuild", "build",
                "-project", f"{name}.xcodeproj", "-scheme", name,
                "-destination", "generic/platform=iOS Simulator",
                "CODE_SIGNING_ALLOWED=NO",
            ])
            if not ok and _XCODE_PLATFORM_MISSING in output:
                print("NOTE  iOS platform not installed (Xcode Settings > Components) — skipping app build check (CI runs it)")
            else:
                check("xcodebuild build (generic iOS Simulator)", lambda: (ok, output))
            print(
                f"NOTE  simulator test suite not run by verify — run once: xcodebuild test"
                f" -project {name}.xcodeproj -scheme {name}"
                f" -destination 'platform=iOS Simulator,name=iPhone 17' (CI runs it on every push)"
            )
        else:
            print("NOTE  Xcode not available (xcodebuild -version failed) — skipping app build check (CI runs it)")

    if failures:
        print(f"{failures} check(s) failed")
        return 1
    print("All checks passed")
    return 0
