"""Post-render search ranking for great-docs sites."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

DEFAULT_NARRATIVE_PREFIXES = (
    "getting-started/",
    "guide/",
    "cheatsheet/",
    "examples/",
    "index.html",
)
REFERENCE_PREFIX = "reference/"
RANK_FIELD = "gd_rank"

FUSE_KEYS_PROBE = """  keys: [
    { name: "title", weight: 20 },
    { name: "section", weight: 20 },
    { name: "text", weight: 10 },
  ],"""
FUSE_KEYS_PATCHED = """  keys: [
    { name: "title", weight: 20 },
    { name: "section", weight: 20 },
    { name: "text", weight: 10 },
    { name: "gd_rank", weight: 30 },
  ],"""

EntryKind = Literal["narrative", "reference", "other"]


def _classify_entry(href: str, narrative_prefixes: tuple[str, ...]) -> EntryKind:
    if href.startswith(REFERENCE_PREFIX):
        return "reference"
    if href.startswith(narrative_prefixes):
        return "narrative"
    return "other"


def classify_entry(href: str) -> EntryKind:
    """Classify a search result using the default site path conventions."""
    return _classify_entry(href, DEFAULT_NARRATIVE_PREFIXES)


def _warning(reason: str) -> None:
    print(f"::warning::gd-build search ranking skipped: {reason}")


def apply_search_ranking(site_dir: Path, narrative_prefixes: list[str] | None = None) -> None:
    """Boost narrative search entries without making a docs build fragile."""
    search_path = site_dir / "search.json"
    if not search_path.is_file():
        _warning(f"{search_path} is missing")
        return

    scripts = list(site_dir.rglob("quarto-search.js"))
    if len(scripts) != 1:
        _warning(f"expected one quarto-search.js under {site_dir}, found {len(scripts)}")
        return

    script_path = scripts[0]
    try:
        entries = json.loads(search_path.read_text())
        script = script_path.read_text()
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _warning(f"could not read search assets: {type(exc).__name__}: {exc}")
        return

    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        _warning(f"{search_path} does not contain a list of search entries")
        return

    unpatched_count = script.count(FUSE_KEYS_PROBE)
    patched_count = script.count(FUSE_KEYS_PATCHED)
    if (unpatched_count, patched_count) not in {(1, 0), (0, 1)}:
        _warning(
            f"Fuse keys block not found exactly once in {script_path} "
            f"(unpatched={unpatched_count}, patched={patched_count})"
        )
        return

    prefixes = DEFAULT_NARRATIVE_PREFIXES if narrative_prefixes is None else tuple(narrative_prefixes)
    for entry in entries:
        href = entry.get("href")
        kind = _classify_entry(href if isinstance(href, str) else "", prefixes)
        if kind == "narrative":
            entry[RANK_FIELD] = " ".join(
                value
                for field in ("title", "section")
                if isinstance(value := entry.get(field), str) and value
            )
        else:
            entry.pop(RANK_FIELD, None)

    ranked_search = json.dumps(entries, ensure_ascii=False, indent=2) + "\n"
    patched_script = (
        script.replace(FUSE_KEYS_PROBE, FUSE_KEYS_PATCHED, 1) if unpatched_count else script
    )

    try:
        search_path.write_text(ranked_search)
        script_path.write_text(patched_script)
    except OSError as exc:
        _warning(f"could not write search assets: {type(exc).__name__}: {exc}")
