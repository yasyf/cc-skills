"""Build the argv of a raw diff read on any gh: a current gh refuses a diff carrying terminal escape sequences without `--allow-escape-sequences`, and the Sprites' gh 2.79 has no such flag, exits on it, and prints those diffs as they are."""

from __future__ import annotations

import functools
import subprocess

from . import ghread

MEDIA = "Accept: application/vnd.github.diff"
ALLOW_ESCAPES = "--allow-escape-sequences"


@functools.cache
def gh_usage() -> str:
    return subprocess.run(["gh", "api", "--help"], capture_output=True, text=True, check=True).stdout


def read(path: str) -> list[str]:
    return ghread.read("api", path, "-H", MEDIA, *((ALLOW_ESCAPES,) if ALLOW_ESCAPES in gh_usage() else ()))
