import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

FENCE = re.compile(r"^[ \t]*```[ \t]*([A-Za-z0-9_+-]*)[^\n]*\n(.*?)^[ \t]*```[ \t]*$", re.MULTILINE | re.DOTALL)
IMAGE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)>?[^)]*\)|<img\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*>", re.IGNORECASE)
DIAGRAMS = ("arch", "dot")
UNDRAWN = ("", "mermaid", "text", "txt", "plain")
FREEZE = ["freeze", "--theme", "github", "--window=false", "--font.family", "Menlo", "--font.size", "14", "--padding", "20", "--margin", "0", "--background", "#ffffff"]
ARCH_LIB = "https://cdn.jsdelivr.net/gh/yasyf/cc-present@v0.60.2/plugin/.claude/components/display/src/arch-map.min.js"
ARCH_WIDTH = 1200
ARCH_SCALE = 2
ARCH_PAGE = """<!doctype html>
<html data-print><head><meta charset="utf-8">
<style>html,body{{margin:0;background:#fff}}.smap.printing>.smap-canvas{{max-height:none!important}}.smap-readout:not(:has(> :not(.smap-hint))){{display:none}}</style></head>
<body><div id="map"></div><script type="application/json" id="doc">{doc}</script>
<script type="module">
const {{ mount }} = await import({lib});
const map = document.getElementById("map");
const drawn = mount(map, JSON.parse(document.getElementById("doc").textContent), {{ key: false }});
await drawn.ready;
document.documentElement.dataset.drawn = drawn.problems.length ? drawn.problems.join("; ") : Math.ceil(map.getBoundingClientRect().height);
</script></body></html>
"""
DRAWN = re.compile(r'<html[^>]*\bdata-drawn="([^"]*)"')
CHROME_APPS = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Chromium.app/Contents/MacOS/Chromium")
CHROME_NAMES = ("google-chrome", "chromium", "chromium-browser", "chrome")
CHROME_SECONDS = 60
DOT_SECONDS = 30
DOWNLOAD_SECONDS = 20


class PictureError(Exception):
    pass


def chrome() -> str:
    if found := os.environ.get("CHROME"):
        return found
    if found := next((app for app in CHROME_APPS if Path(app).exists()), None) or next(filter(None, map(shutil.which, CHROME_NAMES)), None):
        return found
    raise PictureError("no Chrome or Chromium here; install Google Chrome or set CHROME to one")


def headless(page: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            [chrome(), "--headless=new", "--disable-gpu", "--no-first-run", "--hide-scrollbars", f"--user-data-dir={page.parent / 'profile'}", "--virtual-time-budget=15000", *args, page.as_uri()],
            check=False,
            capture_output=True,
            text=True,
            timeout=CHROME_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PictureError(f"Chrome did not run: {error}") from None
    if result.returncode:
        raise PictureError(" ".join(result.stderr.split()) or f"Chrome exited {result.returncode}")
    return result.stdout


def arch_png(source: str, out: Path) -> None:
    """Draw an arch/v1 diagram with cc-present's arch-map, the component the dashboard draws it with: once to measure its height at ARCH_WIDTH, then a screenshot of exactly that."""
    with tempfile.TemporaryDirectory(prefix="drive-arch-") as scratch:
        page = Path(scratch) / "arch.html"
        page.write_text(ARCH_PAGE.format(doc=json.dumps(json.loads(source)).replace("</", "<\\/"), lib=json.dumps(ARCH_LIB)))
        if not (drawn := DRAWN.search(headless(page, f"--window-size={ARCH_WIDTH},{ARCH_WIDTH}", "--dump-dom"))):
            raise PictureError("arch-map did not finish drawing it")
        if not (height := html.unescape(drawn[1])).isdecimal():
            raise PictureError(height)
        headless(page, f"--window-size={ARCH_WIDTH},{height}", f"--force-device-scale-factor={ARCH_SCALE}", f"--screenshot={out}")
    if not out.is_file():
        raise PictureError("Chrome wrote no screenshot")


def dot_png(source: str, out: Path) -> None:
    try:
        result = subprocess.run(
            ["dot", "-Tpng", "-Gdpi=144", "-o", str(out)],
            input=source,
            check=False,
            capture_output=True,
            text=True,
            timeout=DOT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PictureError(f"Graphviz dot did not run: {error}") from None
    if result.returncode:
        raise PictureError(" ".join(result.stderr.split()) or f"dot exited {result.returncode}")


def code_png(language: str, source: str, out: Path) -> None:
    try:
        result = subprocess.run(
            [*FREEZE, "--language", language, "--output", str(out)],
            input=source,
            check=False,
            capture_output=True,
            text=True,
            timeout=DOT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PictureError(f"freeze did not run: {error}") from None
    if result.returncode:
        raise PictureError(" ".join(result.stderr.split()) or f"freeze exited {result.returncode}")


def downloaded(url: str, out: Path) -> None:
    try:
        with urllib.request.urlopen(url, timeout=DOWNLOAD_SECONDS) as reply:
            out.write_bytes(reply.read())
    except OSError as error:
        raise PictureError(f"{url} did not download: {error}") from None


def sources(show_me: str) -> list[tuple[str, str]]:
    """Each picture a Show me section holds, in order, as (kind, source): an arch or dot fence, a code fence drawn highlighted, or an image link."""
    found = [(match.start(), match[1].lower(), match[2]) for match in FENCE.finditer(show_me) if match[1].lower() in DIAGRAMS]
    found += [
        (match.start(), f"code:{match[1].lower()}", match[2])
        for match in FENCE.finditer(show_me)
        if match[1].lower() not in (*DIAGRAMS, *UNDRAWN)
    ]
    found += [(match.start(), "image", match[1] or match[2]) for match in IMAGE.finditer(show_me)]
    return [(kind, source) for _, kind, source in sorted(found)]


def draw(kind: str, source: str, out: Path) -> Path:
    match kind:
        case "arch":
            arch_png(source, out)
        case "dot":
            dot_png(source, out)
        case _ if kind.startswith("code:"):
            code_png(kind.removeprefix("code:"), source, out)
        case _:
            out = out.with_suffix(Path(source.split("?")[0]).suffix or ".png")
            downloaded(source, out)
    return out


def pictures(folder: Path, label: str, show_me: str) -> list[str]:
    """Local image files for a PR's Show me, drawn once per source under folder and named after the PR for Slack's title."""
    drawn = []
    for index, (kind, source) in enumerate(sources(show_me), start=1):
        if kind == "image" and not source.startswith(("http://", "https://")):
            if (local := Path(source).expanduser()).is_absolute() and local.is_file():
                drawn.append(str(local))
            else:
                print(f"drive card: {label}: its Show me links {source}, neither a link nor a file here", file=sys.stderr)
            continue
        name = label if index == 1 else f"{label} ({index})"
        home = folder / hashlib.sha256(f"{kind}\n{source}{ARCH_LIB if kind == 'arch' else ''}".encode()).hexdigest()[:16]
        if cached := sorted(home.glob(f"{name}.*")):
            drawn.append(str(cached[0]))
            continue
        home.mkdir(parents=True, exist_ok=True)
        try:
            drawn.append(str(draw(kind, source, home / f"{name}.png")))
        except (PictureError, ValueError) as error:
            print(f"drive card: {label}: its Show me {kind} does not draw: {error}", file=sys.stderr)
    return drawn
