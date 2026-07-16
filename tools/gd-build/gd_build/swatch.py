"""Rewrite great-docs' runtime color-swatch loader as a depth-correct static tag.

The runtime loader great-docs emits strips exactly two path segments from the
canonical URL, which 404s on any page not exactly one directory deep (e.g. the
homepage). This runs over the built site after `great-docs build`, replacing the
loader with a static `<script src>` whose relative depth matches each page.
"""

from __future__ import annotations

import re
from pathlib import Path

SITE_DIR = Path("great-docs/_site")
LOADER = re.compile(
    r"<script>\(function\(\)\{var s=document\.createElement\('script'\);"
    r".*?color-swatch\.js.*?\}\)\(\)</script>"
)


def fix_swatches(site_dir: Path) -> None:
    for page in site_dir.rglob("*.html"):
        depth = len(page.relative_to(site_dir).parts) - 1
        text = page.read_text()
        tag = f'<script src="{"../" * depth}color-swatch.js"></script>'
        if (new := LOADER.sub(tag, text)) != text:
            page.write_text(new)


if __name__ == "__main__":
    fix_swatches(SITE_DIR)
