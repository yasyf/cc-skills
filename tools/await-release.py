#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""Wait for a cut release to finish propagating — the release, PyPI, and the tap.

After a tag is pushed, propagation lags it: the GitHub release and its assets
appear, PyPI's index starts serving the new version, and the Homebrew tap formula
bumps — each on its own delay. Agents hand-roll a poll loop per surface every
time. This waits on all requested surfaces in one pass.

Phases (each gated only when its flag is given, run in order):
  gh-release  poll `gh release view` until the tag exists, is non-draft, and has
              >=1 asset — or, for a source-only release, is non-draft on two
              consecutive polls (the matched case is noted).
  pypi        (--pypi) poll https://pypi.org/pypi/<pkg>/<version>/json until it
              returns 200, then one confirmation poll.
  brew        (--brew) poll yasyf/homebrew-tap's Casks/<name>.rb or
              Formula/<name>.rb until it mentions the version string.

<version> is <tag> with a leading 'v' stripped. This polls PyPI's JSON API, never
uvx/uv: uvx's resolver lags the JSON endpoint and UV_EXCLUDE_NEWER can hide a
fresh publish outright, so the JSON 200 — not uvx availability — is the real
propagation signal (no uv/uvx is ever invoked).

Timestamped lines mark each phase transition; a final table summarizes
phase | waited | outcome. Exit 0 when every requested phase confirms, 1 on a
per-phase timeout (naming the phase), 2 on a usage/config error. Ctrl-C prints
the partial summary and exits cleanly."""

import argparse
import json
import shutil
import subprocess
import sys
import time

TAP = "yasyf/homebrew-tap"


def run(cmd: list[str]) -> tuple[int, str, str]:
    """Run a subprocess, returning (returncode, stdout, stderr)."""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def stamp() -> str:
    return time.strftime("%H:%M:%S")


class GhReleasePhase:
    """Wait for the GitHub release: exists, non-draft, and either has an asset or
    proves source-only by staying non-draft/zero-asset across two polls (which
    rules out catching it mid asset-upload)."""

    name = "gh-release"

    def __init__(self, repo: str, tag: str):
        self.repo = repo
        self.tag = tag
        self.target = f"{repo} {tag}"
        self._zero_streak = 0

    def poll(self) -> tuple[bool, str]:
        rc, out, err = run(["gh", "release", "view", self.tag, "-R", self.repo, "--json", "assets,isDraft"])
        if rc != 0:
            self._zero_streak = 0
            first = next(iter(err.strip().splitlines()), "not found")
            return False, f"absent ({first})"
        data = json.loads(out)
        if data["isDraft"]:
            self._zero_streak = 0
            return False, "present, draft"
        n = len(data["assets"])
        if n >= 1:
            return True, f"with-assets, {n} asset{'s' if n != 1 else ''}"
        self._zero_streak += 1
        if self._zero_streak >= 2:
            return True, "source-only, 0 assets"
        return False, "present, non-draft, 0 assets (confirming source-only)"


class PypiPhase:
    """Wait for PyPI's per-version JSON endpoint to return 200, then confirm once
    more — a single 200 can precede stable CDN propagation."""

    name = "pypi"

    def __init__(self, package: str, version: str):
        self.package = package
        self.version = version
        self.url = f"https://pypi.org/pypi/{package}/{version}/json"
        self.target = f"{package} {version}"

    def poll(self) -> tuple[bool, str]:
        code = self._http()
        if code != "200":
            return False, f"HTTP {code}"
        confirm = self._http()  # one extra confirmation poll
        if confirm == "200":
            return True, "HTTP 200 (confirmed)"
        return False, f"HTTP 200 then {confirm}"

    def _http(self) -> str:
        _, out, _ = run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-m", "10", self.url])
        return out.strip() or "000"


class BrewPhase:
    """Wait for the tap's formula/cask file to mention the version. Resolves the
    file lazily (Casks/ then Formula/); an absent file is pending, not fatal —
    render-formula may not have committed it yet."""

    name = "brew"

    def __init__(self, formula: str, version: str):
        self.formula = formula
        self.version = version
        self.target = f"{TAP} {formula} {version}"
        self._path: str | None = None

    def poll(self) -> tuple[bool, str]:
        content = self._fetch()
        if content is None:
            return False, "cask/formula file absent"
        if self.version in content:
            return True, f"mentions {self.version} ({self._path})"
        return False, f"{self._path} present, version not yet {self.version}"

    def _fetch(self) -> str | None:
        candidates = [self._path] if self._path else [f"Casks/{self.formula}.rb", f"Formula/{self.formula}.rb"]
        for path in candidates:
            rc, out, _ = run(["gh", "api", f"repos/{TAP}/contents/{path}", "-H", "Accept: application/vnd.github.raw"])
            if rc == 0:
                self._path = path
                return out
        return None


def run_phase(phase, timeout: int, interval: int) -> tuple[str | None, float]:
    """Poll a phase until it confirms or the per-phase timeout elapses. Prints the
    start line, a line each time the observed state changes, and the terminal
    line. Returns (detail, waited) on success, (None, waited) on timeout."""
    start = time.monotonic()
    print(f"{stamp()}  {phase.name:<10}  waiting — {phase.target}")
    last = None
    while True:
        done, detail = phase.poll()
        waited = time.monotonic() - start
        if done:
            print(f"{stamp()}  {phase.name:<10}  confirmed — {detail}  ({waited:.0f}s)")
            return detail, waited
        if detail != last:
            print(f"{stamp()}  {phase.name:<10}  … {detail}")
            last = detail
        remaining = timeout - waited
        if remaining <= 0:
            print(f"{stamp()}  {phase.name:<10}  TIMEOUT after {waited:.0f}s — last: {detail}")
            return None, waited
        time.sleep(min(interval, remaining))


def _table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [max(len(headers[i]), *(len(r[i]) for r in rows)) if rows else len(headers[i]) for i in range(len(headers))]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    for row in rows:
        print(fmt.format(*row))


def print_summary(results: list[list[str]]) -> None:
    print()
    _table(["phase", "waited", "outcome"], results)


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)  # live progress + ordered vs stderr when piped
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("repo", metavar="owner/repo", help="GitHub repository, e.g. yasyf/captain-hook")
    parser.add_argument("tag", help="release tag, e.g. v12.10.0")
    parser.add_argument("--pypi", metavar="package", help="also wait for this PyPI package's JSON endpoint to return 200")
    parser.add_argument("--brew", metavar="formula-or-cask", help=f"also wait for {TAP}'s <name>.rb to mention the version")
    parser.add_argument("--timeout", type=int, default=600, metavar="sec", help="per-phase timeout in seconds (default 600)")
    parser.add_argument("--interval", type=int, default=15, metavar="sec", help="seconds between polls (default 15)")
    args = parser.parse_args()

    if args.repo.count("/") != 1 or args.repo.startswith("/") or args.repo.endswith("/"):
        parser.error(f"owner/repo must be in 'owner/repo' form: {args.repo!r}")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.interval <= 0:
        parser.error("--interval must be positive")
    for tool in ["gh"] + (["curl"] if args.pypi else []):
        if shutil.which(tool) is None:
            parser.error(f"required tool not found on PATH: {tool}")

    version = args.tag[1:] if args.tag.startswith("v") else args.tag

    phases = [GhReleasePhase(args.repo, args.tag)]
    if args.pypi:
        phases.append(PypiPhase(args.pypi, version))
    if args.brew:
        phases.append(BrewPhase(args.brew, version))

    results: list[list[str]] = []
    timed_out: str | None = None
    try:
        for i, phase in enumerate(phases):
            detail, waited = run_phase(phase, args.timeout, args.interval)
            if detail is None:
                results.append([phase.name, f"{waited:.0f}s", "TIMEOUT"])
                timed_out = phase.name
                results.extend([later.name, "—", "not reached"] for later in phases[i + 1 :])
                break
            results.append([phase.name, f"{waited:.0f}s", detail])
    except KeyboardInterrupt:
        done = {r[0] for r in results}
        results.extend([p.name, "—", "interrupted"] for p in phases if p.name not in done)
        print_summary(results)
        print("\ninterrupted", file=sys.stderr)
        sys.exit(130)

    print_summary(results)
    if timed_out:
        print(f"\ntimed out waiting on phase: {timed_out}", file=sys.stderr)
        sys.exit(1)
    print("\nall phases confirmed")
    sys.exit(0)


if __name__ == "__main__":
    main()
