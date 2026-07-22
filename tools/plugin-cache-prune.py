#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = []
# ///
"""Prune the Claude plugin cache — historical version/sha dirs accumulate forever.

For each installed <marketplace>/<plugin> (discovered from installed_plugins.json,
so transient clones and orphaned trees are never touched), keep every dir any
install record references across all scopes, plus the newest KEEP_NEWEST of the
rest by mtime, plus anything modified within MAX_AGE_DAYS; delete the remainder.
Dry-run by default; --apply mutates. Refuses to run if installed_plugins.json is
missing or unparseable, and never deletes outside the cache root."""

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

CACHE_DIR = Path.home() / ".claude" / "plugins" / "cache"
RECORDS = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
KEEP_NEWEST = 2
MAX_AGE_DAYS = 7


def load_records(path: Path) -> dict:
    """Read installed_plugins.json; a missing or unparseable file is fatal — the
    cache is load-bearing, so we refuse to prune without the keep list."""
    if not path.is_file():
        sys.exit(f"FAIL: records not found: {path}")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        sys.exit(f"FAIL: records unparseable ({path}): {exc}")
    if "plugins" not in data:
        sys.exit(f"FAIL: records missing 'plugins' key: {path}")
    return data


def referenced_dirs(records: dict, cache_dir: Path) -> dict[Path, set[str]]:
    """Map each plugin dir to the set of version/sha dir names its install records
    reference, across every scope. Records whose installPath escapes the cache
    root are skipped — we only ever prune inside the cache."""
    cache_root = cache_dir.resolve()
    out: dict[Path, set[str]] = {}
    for entries in records["plugins"].values():
        for rec in entries:
            install = Path(rec["installPath"]).resolve()
            if cache_root not in install.parents:
                print(f"skip (outside cache root): {install}")
                continue
            out.setdefault(install.parent, set()).add(install.name)
    return out


def dir_size(path: Path) -> int:
    """Total bytes of regular files under path, symlinks excluded."""
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            fp = os.path.join(root, name)
            if not os.path.islink(fp):
                total += os.path.getsize(fp)
    return total


def plan_plugin(plugin_dir: Path, referenced: set[str], now: float) -> tuple[list[Path], list[Path]]:
    """Keep referenced dirs, the newest KEEP_NEWEST of the rest by mtime, and any
    touched within MAX_AGE_DAYS; the remainder is deleted. Symlinked children are
    left alone (never candidates for deletion)."""
    children = [d for d in plugin_dir.iterdir() if d.is_dir() and not d.is_symlink()]
    keep = {d for d in children if d.name in referenced}
    rest = sorted((d for d in children if d not in keep), key=lambda d: d.stat().st_mtime, reverse=True)
    keep.update(rest[:KEEP_NEWEST])
    cutoff = now - MAX_AGE_DAYS * 86400
    keep.update(d for d in rest if d.stat().st_mtime >= cutoff)
    delete = [d for d in children if d not in keep]
    return sorted(keep), sorted(delete)


def human(n: int) -> str:
    """Bytes as a compact IEC string."""
    size = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{int(size)}B" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    raise AssertionError


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--apply", action="store_true", help="delete pruned dirs (default: dry-run)")
    parser.add_argument("--cache-dir", type=Path, default=CACHE_DIR, help=f"cache root ({CACHE_DIR})")
    parser.add_argument("--records", type=Path, default=RECORDS, help=f"install records ({RECORDS})")
    args = parser.parse_args()

    plan = referenced_dirs(load_records(args.records), args.cache_dir)
    cache_root = args.cache_dir.resolve()

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"plugin-cache-prune [{mode}] — cache {args.cache_dir}")

    now = time.time()
    rows: list[tuple[str, int, int, int]] = []
    total_pruned = total_bytes = 0
    for plugin_dir in sorted(plan):
        if not plugin_dir.is_dir():
            continue
        keep, delete = plan_plugin(plugin_dir, plan[plugin_dir], now)
        if not delete:
            continue
        reclaimed = 0
        for d in delete:
            resolved = d.resolve()
            assert cache_root in resolved.parents, f"refuse: {resolved} outside {cache_root}"
            reclaimed += dir_size(d)
            if args.apply:
                shutil.rmtree(d)
        rows.append((str(plugin_dir.relative_to(cache_root)), len(keep), len(delete), reclaimed))
        total_pruned += len(delete)
        total_bytes += reclaimed

    verb = "pruned" if args.apply else "would prune"
    width = max((len(r[0]) for r in rows), default=len("TOTAL"))
    print(f"{'plugin':<{width}}  kept  {verb:>10}  reclaimed")
    for name, kept, pruned, reclaimed in rows:
        print(f"{name:<{width}}  {kept:>4}  {pruned:>10}  {human(reclaimed):>9}")
    print(f"{'TOTAL':<{width}}  {'':>4}  {total_pruned:>10}  {human(total_bytes):>9}")
    if not args.apply and total_pruned:
        print("\nre-run with --apply to delete")


if __name__ == "__main__":
    main()
