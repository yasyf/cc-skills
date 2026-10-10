from __future__ import annotations

import re
from pathlib import Path

__capt_hook_skip__ = True

VERSION = re.compile(r"\d+(?:\.\d+)+")
ORPHANED = ".orphaned_at"
SCRIPTS = Path("skills") / "long-running" / "scripts"


def release(path: Path) -> tuple[int, ...]:
    return tuple(int(part) for part in path.name.split("."))


def newest(root: Path) -> Path:
    if not VERSION.fullmatch(root.name):
        return root
    live = [path for path in root.parent.iterdir() if VERSION.fullmatch(path.name) and not (path / ORPHANED).exists() and (path / SCRIPTS).is_dir()]
    return max(live, key=release)


def script(name: str) -> Path:
    return newest(Path(__file__).parents[2]) / SCRIPTS / name
