#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""Reconcile installed Claude plugins — derive the *exact* update invocation.

`claude plugin update <bare-name>` fails ("not found"): the argument must be the
verbatim <plugin>@<marketplace> qualifier from installed_plugins.json. And
--scope defaults to user regardless of cwd, so a project-scoped install silently
stays stale or errors "not installed at scope user".

For each install record this emits `claude plugin update <plugin>@<marketplace>
--scope <scope>`, run with cwd = projectPath for project-scoped installs, after
refreshing each distinct marketplace once (`claude plugin marketplace update
<marketplace>`). Report-only by default — prints the derived table without
executing. --apply runs them in order (marketplaces first), capturing per-record
version-change / no-op / error; --only <plugin@marketplace> filters. Never invents
a qualifier or scope, never installs or uninstalls — update only. Refuses if
installed_plugins.json is missing or unparseable; exits 1 if any --apply
invocation errored."""

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

RECORDS = Path.home() / ".claude" / "plugins" / "installed_plugins.json"


def load_records(path: Path) -> dict:
    """Read installed_plugins.json; missing or unparseable is fatal — deriving an
    invocation from a guessed qualifier is exactly the footgun this tool fixes."""
    if not path.is_file():
        sys.exit(f"FAIL: records not found: {path}")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        sys.exit(f"FAIL: records unparseable ({path}): {exc}")
    if "plugins" not in data:
        sys.exit(f"FAIL: records missing 'plugins' key: {path}")
    return data


@dataclass(frozen=True)
class Install:
    """One install record — everything needed to update it in place."""

    qualifier: str  # <plugin>@<marketplace>, verbatim from the records
    scope: str  # user | project | local | managed, verbatim
    project_path: str | None
    version: str

    @property
    def marketplace(self) -> str:
        return self.qualifier.rsplit("@", 1)[1]

    @property
    def dead_path(self) -> bool:
        return self.project_path is not None and not Path(self.project_path).is_dir()

    def command(self) -> list[str]:
        return ["claude", "plugin", "update", self.qualifier, "--scope", self.scope]

    def rendered(self) -> str:
        """The exact shell line, cd-wrapped when a projectPath pins the cwd."""
        line = " ".join(self.command())
        return f"(cd {self.project_path} && {line})" if self.project_path else line


def build(records: dict, only: str | None) -> list[Install]:
    """Every install record as an Install, in records-file order. --only keeps
    only the exact plugin@marketplace qualifier."""
    out: list[Install] = []
    for qualifier, entries in records["plugins"].items():
        if only and qualifier != only:
            continue
        for rec in entries:
            out.append(Install(qualifier, rec["scope"], rec.get("projectPath"), rec["version"]))
    return out


def marketplaces(plan: list[Install]) -> list[str]:
    """Distinct marketplaces in first-seen order — refreshed before any update."""
    seen: dict[str, None] = {}
    for inst in plan:
        seen.setdefault(inst.marketplace, None)
    return list(seen)


def current_version(records: dict, inst: Install) -> str:
    """The version now recorded for inst's exact (scope, projectPath) — read back
    after an update to tell version-change from no-op."""
    for rec in records["plugins"].get(inst.qualifier, []):
        if rec["scope"] == inst.scope and rec.get("projectPath") == inst.project_path:
            return rec["version"]
    return "?"


def run(cmd: list[str], cwd: str | None = None) -> tuple[int, str]:
    """Run a claude subcommand, returning (returncode, combined output)."""
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [max(len(headers[i]), *(len(r[i]) for r in rows)) if rows else len(headers[i]) for i in range(len(headers))]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers))
    for row in rows:
        print(fmt.format(*row))


def report(plan: list[Install], mkts: list[str]) -> None:
    """Report-only: the marketplace refresh, the invocation table, the commands."""
    print("marketplace refresh (run first):")
    for mk in mkts:
        print(f"  claude plugin marketplace update {mk}")

    print("\nderived plugin updates:")
    rows = [
        [
            inst.qualifier,
            inst.scope,
            f"{inst.project_path} (MISSING)" if inst.dead_path else (inst.project_path or "-"),
            inst.version,
        ]
        for inst in plan
    ]
    _table(["plugin@marketplace", "scope", "projectPath", "version"], rows)

    print("\nupdate commands (in order):")
    for inst in plan:
        if inst.dead_path:
            print(f"  SKIP {inst.qualifier} --scope {inst.scope} — projectPath {inst.project_path} not on disk")
        else:
            print(f"  {inst.rendered()}")
    print("\nre-run with --apply to execute")


def apply(plan: list[Install], mkts: list[str], records_path: Path) -> int:
    """Refresh marketplaces, then update each record in order. A record that
    errors is reported and the loop continues; returns 1 if anything errored."""
    errors = 0

    print("== refreshing marketplaces")
    for mk in mkts:
        rc, out = run(["claude", "plugin", "marketplace", "update", mk])
        print(f"  {mk}: {'ok' if rc == 0 else 'ERROR'}")
        if rc != 0:
            errors += 1
            print(f"    {out}")

    print("\n== updating plugins")
    rows: list[list[str]] = []
    for inst in plan:
        if inst.dead_path:
            print(f"  skip {inst.qualifier} [{inst.scope}] — projectPath not on disk")
            rows.append([inst.qualifier, inst.scope, inst.version, "-", "skip"])
            continue
        rc, out = run(inst.command(), cwd=inst.project_path)
        if rc != 0:
            errors += 1
            print(f"  ERROR {inst.qualifier} [{inst.scope}]: {out.splitlines()[0] if out else rc}")
            rows.append([inst.qualifier, inst.scope, inst.version, "?", "error"])
            continue
        new = current_version(load_records(records_path), inst)
        outcome = "version-change" if new != inst.version else "no-op"
        print(f"  {outcome} {inst.qualifier} [{inst.scope}] {inst.version} -> {new}")
        rows.append([inst.qualifier, inst.scope, inst.version, new, outcome])

    print("\n== summary")
    _table(["plugin@marketplace", "scope", "from", "to", "outcome"], rows)
    print(f"\n{len(rows)} records, {errors} errored")
    return 1 if errors else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true", help="execute the updates (default: report-only)")
    parser.add_argument("--only", metavar="plugin@marketplace", help="update just this qualifier")
    parser.add_argument("--records", type=Path, default=RECORDS, help=f"install records ({RECORDS})")
    args = parser.parse_args()

    plan = build(load_records(args.records), args.only)
    if not plan:
        sys.exit(f"FAIL: no install records{f' matching --only {args.only!r}' if args.only else ''}")
    mkts = marketplaces(plan)

    if args.apply:
        sys.exit(apply(plan, mkts, args.records))
    report(plan, mkts)


if __name__ == "__main__":
    main()
