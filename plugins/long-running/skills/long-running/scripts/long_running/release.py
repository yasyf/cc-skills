"""The one link that names the whole install of this plugin every reader runs.

Claude Code writes each version into a directory of its own and names it in its registry only once the copy is whole.
The first process of a version `promote`s it: ``$CLAUDE_CONFIG_DIR/long-running/current`` is swapped to that version's
directory with one rename, only to a higher version, so a reader that resolves the link once runs one whole version,
the old one or the new one. A reader never scans the cache for the newest copy. STDLIB ONLY.
"""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

VERSION = re.compile(r"\d+(?:\.\d+)*")
VERSION_PART = re.compile(r"\d+")
SHIMS = {"cci": "cc-inbox", "ccx": "cc-context"}


def config_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def link() -> Path:
    return config_dir() / "long-running" / "current"


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in VERSION_PART.findall(version.partition("-")[0]))


def held() -> Path | None:
    """The install directory the link names, or None when no link exists or its directory is gone."""
    try:
        return link().resolve(strict=True)
    except OSError:
        return None


def current(own: Path) -> Path:
    """The install a reader running from `own` runs: the one the link names, or `own` itself while the link names none or `own` is no version directory."""
    return (held() or own) if VERSION.fullmatch(own.name) else own


def promote(own: Path) -> None:
    """Name `own` in the link when it is a version directory above the one the link names, or the link names none."""
    if not VERSION.fullmatch(own.name):
        return
    named = held()
    if named is not None and version_key(named.name) >= version_key(own.name):
        return
    staged = link().with_name(f"current.{uuid.uuid4().hex}")
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.symlink_to(own)
    os.replace(staged, link())


def command(argv: list[str]) -> list[str]:
    """`argv` with cci and ccx replaced by their plugin's shim, which execs a content-addressed binary, so a Homebrew relink of the command on PATH never reaches a call; any other command, and one whose plugin is not installed, keeps its name."""
    shim = config_dir() / "plugins" / "marketplaces" / SHIMS.get(argv[0], "") / "plugin" / "bin" / argv[0]
    return [str(shim), *argv[1:]] if argv[0] in SHIMS and shim.exists() else argv
