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
DIAGRAMS = ("arch", "mermaid", "chart", "compare", "dot")
WORDS = ("", "text", "txt", "plain")
RAW = {
    **dict.fromkeys(("json", "jsonc", "json5", "jsonl", "ndjson"), "JSON"),
    **dict.fromkeys(("yaml", "yml"), "YAML"),
    **dict.fromkeys(("csv", "tsv"), "raw rows"),
    **dict.fromkeys(("log", "logs"), "log lines"),
    **dict.fromkeys(("console", "terminal", "shell-session", "sh-session", "pwsh-session"), "a terminal session"),
    **dict.fromkeys(("output", "stdout", "stderr"), "command output"),
    **dict.fromkeys(("traceback", "pytb", "stacktrace"), "a stack trace"),
}
INSTEAD = (
    "draw what it means instead, a ```mermaid flow for routing or ownership, a ```compare or a markdown table for"
    " options, a ```chart for numbers, or show no picture at all"
)
PROMPT = re.compile(r"^[ \t]*(?:\$|%|❯|>>>)[ \t]+\S")
STACK_TRACE = re.compile(
    r"Traceback \(most recent call last\)|^goroutine \d+ \[|^panic: |thread '[^'\n]*' panicked at"
    r"|^[ \t]+at [\w$.<>/]+ ?\(\S+:\d+(?::\d+)?\)[ \t]*$|^[ \t]+File \"[^\"\n]+\", line \d+",
    re.MULTILINE,
)
LOG_LINE = re.compile(
    r"^[ \t]*\[?(?:\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}|\d{2}:\d{2}:\d{2}\b)|\[(?:TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL)\]|\blevel=\w+"
)
VISUALS = Path(__file__).parent / "site" / "visuals.js"
VISUAL_WINDOW = 1200
VISUAL_SCALE = 2
VISUAL_PAGE = """<!doctype html>
<html data-print><head><meta charset="utf-8"><style>html,body{margin:0;background:#fff}#visual{display:inline-block}#visual:not([data-kind=code]){padding:8px}#visual[data-kind=arch]{display:block;width:960px}.smap.printing>.smap-canvas{max-height:none!important}.smap-readout:not(:has(> :not(.smap-hint))){display:none}</style></head>
<body><div id="visual"></div><script type="application/json" id="source">SOURCE</script>
<script type="module">VISUALS
const host = document.getElementById("visual");
const shown = JSON.parse(document.getElementById("source").textContent);
try {
  await draw(host, shown.kind, shown.source);
  await document.fonts.ready;
  const box = host.getBoundingClientRect();
  document.documentElement.dataset.drawn = `${Math.ceil(box.width)}x${Math.ceil(box.height)}`;
} catch (error) {
  document.documentElement.dataset.drawn = String(error?.message ?? error);
}
</script></body></html>
"""
DRAWN = re.compile(r'<html[^>]*\bdata-drawn="([^"]*)"')
SIZE = re.compile(r"(\d+)x(\d+)")
CAPTIONED = re.compile(
    r"(?:^(?![ \t]*(?:```|!\[|\||[-*•][ \t]|\d+[.)][ \t]|#))[ \t]*(?P<caption>\S[^\n]*?)[ \t:]*\n)?"
    r"^[ \t]*```[ \t]*(?P<language>[A-Za-z0-9_+-]*)[^\n]*\n(?P<source>.*?)^[ \t]*```[ \t]*$",
    re.MULTILINE | re.DOTALL,
)
CHROME_APPS = ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/Applications/Chromium.app/Contents/MacOS/Chromium")
CHROME_NAMES = ("google-chrome", "chromium", "chromium-browser", "chrome")
CHROME_SECONDS = 60
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


def visual_png(kind: str, source: str, out: Path) -> None:
    """Draw one visual with site/visuals.js, the module the dashboard draws it with live: once to measure it, then a screenshot of exactly that box."""
    with tempfile.TemporaryDirectory(prefix="drive-visual-") as scratch:
        page = Path(scratch) / "visual.html"
        shown = json.dumps({"kind": kind, "source": source}).replace("</", "<\\/")
        page.write_text(VISUAL_PAGE.replace("VISUALS", VISUALS.read_text(), 1).replace("SOURCE", shown, 1))
        if not (drawn := DRAWN.search(headless(page, f"--window-size={VISUAL_WINDOW},{VISUAL_WINDOW}", "--dump-dom"))):
            raise PictureError(f"the {kind} did not finish drawing")
        if not (size := SIZE.fullmatch(said := html.unescape(drawn[1]))):
            raise PictureError(said)
        headless(page, f"--window-size={size[1]},{size[2]}", f"--force-device-scale-factor={VISUAL_SCALE}", f"--screenshot={out}")
    if not out.is_file():
        raise PictureError("Chrome wrote no screenshot")


def downloaded(url: str, out: Path) -> None:
    try:
        with urllib.request.urlopen(url, timeout=DOWNLOAD_SECONDS) as reply:
            out.write_bytes(reply.read())
    except OSError as error:
        raise PictureError(f"{url} did not download: {error}") from None


def sources(show_me: str) -> list[tuple[str, str]]:
    """Each picture a Show me section holds, in order, as (kind, source): a diagram, chart or compare fence, a code fence naming its language, or an image link."""
    found = [
        (match.start(), fence_kind(match[1]), match[2])
        for match in FENCE.finditer(show_me)
        if match[1].lower() not in WORDS and not raw(match[1], match[2])
    ]
    found += [(match.start(), "image", match[1] or match[2]) for match in IMAGE.finditer(show_me)]
    return [(kind, source) for _, kind, source in sorted(found)]


def fence_kind(language: str) -> str:
    return language.lower() if language.lower() in DIAGRAMS else f"code:{language.lower()}"


def raw(language: str, source: str) -> str | None:
    """What a fence holds when it is raw data or tool output, whatever language it names: JSON, YAML, raw rows, log lines, a terminal session, command output or a stack trace, of which a picture shows him nothing."""
    if language.lower() in DIAGRAMS:
        return None
    if named := RAW.get(language.lower()):
        return named
    lines = [line for line in source.splitlines() if line.strip()]
    output = [line for above, line in zip(["", *lines], lines, strict=False) if not PROMPT.match(line) and not above.rstrip().endswith("\\")]
    if STACK_TRACE.search(source):
        return "a stack trace"
    if output and len(output) < len(lines):
        return "a terminal session"
    if len(lines) >= 2 and 2 * sum(bool(LOG_LINE.search(line)) for line in lines) >= len(lines):
        return "log lines"
    try:
        return "JSON" if isinstance(json.loads(source), dict | list) else None
    except json.JSONDecodeError:
        return None


def draw(kind: str, source: str, out: Path) -> Path:
    if kind != "image":
        visual_png(kind, source, out)
        return out
    out = out.with_suffix(Path(source.split("?")[0]).suffix or ".png")
    downloaded(source, out)
    return out


def cached(folder: Path, name: str, kind: str, source: str) -> Path:
    """The picture of one source, drawn once under folder and named name."""
    renderer = VISUALS.read_text() if kind != "image" else ""
    home = folder / hashlib.sha256(f"{kind}\n{source}{renderer}".encode()).hexdigest()[:16]
    home.mkdir(parents=True, exist_ok=True)
    (home / "source.json").write_text(json.dumps({"kind": kind, "source": source}))
    if found := sorted(home.glob(f"{name}.*")):
        return found[0]
    return draw(kind, source, home / f"{name}.png")


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
        try:
            drawn.append(str(cached(folder, label if index == 1 else f"{label} ({index})", kind, source)))
        except (PictureError, ValueError) as error:
            print(f"drive card: {label}: its Show me {kind} does not draw: {error}", file=sys.stderr)
    return drawn


def drawn(folder: Path, show_me: list[str]) -> list[str]:
    """A question card's Show me with each fenced block replaced by one line holding its picture, `![caption](/abs.png)`, which cc-slack draws as an image block where the line sits, titled with the caption line just above the fence. A fence of words, with no language or `text`, a fence of raw data or tool output, and a fence that does not draw leave the card with their caption, since a picture of words, JSON or a terminal shows nothing."""

    def picture_line(fence: re.Match[str]) -> str:
        if fence["language"].lower() in WORDS:
            print(f"drive card: its Show me holds words in a ``` fence, which leave the card: {fence['source'][:60]!r}", file=sys.stderr)
            return ""
        if what := raw(fence["language"], fence["source"]):
            print(f"drive card: its Show me holds {what} in a ```{fence['language']} fence, which leaves the card; {INSTEAD}", file=sys.stderr)
            return ""
        shown = fence_kind(fence["language"])
        try:
            path = cached(folder, "show-me", shown, fence["source"])
        except (PictureError, ValueError) as error:
            print(f"drive card: its Show me {shown} does not draw, so it leaves the card: {error}", file=sys.stderr)
            return ""
        return f"![{fence['caption'] or 'Show me'}]({path})"

    return CAPTIONED.sub(picture_line, "\n".join(show_me)).split("\n")
