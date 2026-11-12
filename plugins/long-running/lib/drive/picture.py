import contextlib
import functools
import hashlib
import html
import http.server
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
from collections.abc import Iterator
from html.parser import HTMLParser
from pathlib import Path

from drive import github

FENCE =re.compile(r"^[ \t]*```[ \t]*([A-Za-z0-9_+-]*)[^\n]*\n(.*?)^[ \t]*```[ \t]*$", re.MULTILINE | re.DOTALL)
UNNAMEABLE = re.compile(r'[\x00-\x1f/\\:*?"<>|\[\]]+')
ALT = re.compile(r"!\[([^\]]*)\]")
IMAGE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)>?[^)]*\)|<img\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*>", re.IGNORECASE)
DIAGRAMS = ("arch", "mermaid", "chart", "compare", "dot")
NATIVE = ("chart",)
WORDS = ("", "text", "txt", "plain")
TITLE_CHARS = 150
TITLES = {"arch": "Architecture map", "mermaid": "Diagram", "compare": "Options side by side", "dot": "Graph", "code:": "Code", "image": "Picture"}
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
    "draw what it means instead, a ```mermaid flow for routing or ownership or a ```mermaid sequenceDiagram for calls"
    " between services, a ```compare or a markdown table for options or before and after, a ```chart for numbers, or"
    " show no picture at all"
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
HTTP_STATUS = re.compile(r"^[ \t<]*HTTP/\d(?:\.\d)?[ \t]+[1-5]\d\d\b", re.MULTILINE)
HTTP_REQUEST = re.compile(r"\b(?:GET|HEAD|POST|PUT|PATCH|DELETE|OPTIONS)[ \t]+(?:https?://|/|[\w.-]+/)")
HTTP_ANSWER = re.compile(
    r"(?:->|→|<-|←)[ \t]*(?:[1-5]\d\d(?=[ \t]*(?:$|[,;{\[(]|OK\b|[A-Z][a-z]+\b))|[{\[][ \t]*[\"{\[\]}])"
    r"|(?:^[ \t]*(?:#+|//)?|:)[ \t]*[1-5]\d\d[ \t]+[{\[][ \t]*[\"{\[\]}]"
)
COMMENT = re.compile(r"^[ \t]*(?://|#|--|/?\*)")
SPAN = re.compile(r"<[^<>\n]*\|([^<>\n]*)>|<https?://[^<>\s]*>|https?://\S+|`[^`\n]*`")
CODE_LINE = re.compile(
    r"^(?:[+-][ \t]+)?(?:"
    r"(?://|/\*|\*/|#[ \t!]|--[ \t])"
    r"|(?:func|def|fn|const|let|var|return|if|else|elif|for|while|switch|case|import|export|package|class|struct|enum|impl|pub|async|await|try"
    r"|catch|throw|SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|WHERE|SET|FROM|JOIN|UNION|VALUES)\b"
    r"|[\w.\[\]\"'$]+[ \t]*(?::=|[+-]?=)(?!=)"
    r"|\"?[A-Za-z_][\w-]*\"?[ \t]*:[ \t]*[\"'\d\[{][^\n]*[,{\[][ \t]*$"
    r"|[)\]}]"
    r")"
    r"|[{\[(;][ \t]*$"
    r"|\w\((?:[^()\n]|\([^()\n]*\))*\)"
    r"|[ \t](?:==|!=|>=|<=|&&|\|\||=>)[ \t]"
    r"|(?:^|[ \t])--?[a-z][\w-]*(?=[ \t]|$)"
)
LAYOUT = re.compile(r"^\s|\S {2,}\S|\t|->|<-|→|←|[─-╿]")
SENTENCE_END = re.compile(r"[.?!][)\"'”’]*(?=\s|$)")
PROSE_WORD = re.compile(r"[^\W\d_][\w'’-]*")
PROSE_WORDS = 6
VISUALS = Path(__file__).parent / "site" / "visuals.js"
VISUAL_WINDOW = 1200
VISUAL_SCALE = 2
VISUAL_TRIES = 3
VISUAL_PAGE = """<!doctype html>
<html data-print><head><meta charset="utf-8"><style>html,body{margin:0;background:#fff}#visual{display:inline-block}#visual:not([data-kind=code]){padding:8px}#visual[data-kind=arch]{display:block;width:960px}.smap.printing>.smap-canvas{max-height:none!important}.smap-readout:not(:has(> :not(.smap-hint))){display:none}</style></head>
<body><div id="visual"></div><script type="application/json" id="source">SOURCE</script>
<script type="module">import { draw } from "./visuals.js";
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
LOAD_FAILED = re.compile(r"Page load failed: (\S+)")
UNSHOWN_TAGS = frozenset({"head", "script", "style", "noscript", "template"})
DOWNLOAD_SECONDS = 20
REPO_FILE = re.compile(
    r"https://(?:github\.com/(?P<repo>[\w.-]+/[\w.-]+)/(?:blob|raw)|raw\.githubusercontent\.com/(?P<raw_repo>[\w.-]+/[\w.-]+))/(?P<ref>[^/?#]+)/(?P<path>[^?#]+)"
)
ATTACHMENT = re.compile(r"https://github\.com/user-attachments/assets/")
SIGNED = re.compile(r'<img src="([^"]+)"')
SIGNING_CONTEXT = "cli/cli"
SOURCED = ".sourced"
SOURCE = "source.json"


class PictureError(Exception):
    pass


def chrome() -> str:
    if found := os.environ.get("CHROME"):
        return found
    if found := next((app for app in CHROME_APPS if Path(app).exists()), None) or next(filter(None, map(shutil.which, CHROME_NAMES)), None):
        return found
    raise PictureError("no Chrome or Chromium here; install Google Chrome or set CHROME to one")


def headless(url: str, profile: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            [chrome(), "--headless=new", "--disable-gpu", "--no-first-run", "--hide-scrollbars", f"--user-data-dir={profile}", "--virtual-time-budget=15000", *args, url],
            check=False,
            capture_output=True,
            text=True,
            timeout=CHROME_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PictureError(f"Chrome did not run: {error}") from None
    if result.returncode:
        raise PictureError(" ".join(result.stderr.split()) or f"Chrome exited {result.returncode}")
    if failed := LOAD_FAILED.search(result.stderr):
        raise PictureError(f"Chrome could not load {url}: {failed[1]}")
    return result.stdout


class ShownWords(HTMLParser):
    """The words of a serialized DOM a reader sees on the page: none inside its head, a script, a style, a noscript or a template."""

    def __init__(self) -> None:
        super().__init__()
        self.unshown = 0
        self.words: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in UNSHOWN_TAGS:
            self.unshown += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in UNSHOWN_TAGS and self.unshown:
            self.unshown -= 1

    def handle_data(self, data: str) -> None:
        if not self.unshown:
            self.words += data.split()


def page_text(url: str) -> str:
    """The words a web page shows once Chrome has opened it and run its scripts, in reading order, as he reads the view a link opens."""
    with tempfile.TemporaryDirectory(prefix="drive-page-") as scratch:
        dom = headless(url, Path(scratch) / "profile", "--dump-dom")
    shown = ShownWords()
    shown.feed(dom)
    return " ".join(shown.words)


def visual_png(kind: str, source: str, out: Path) -> None:
    """Draw one visual with site/visuals.js, the module the dashboard draws it with live, and the renderers it keeps beside it, served on a loopback port so nothing loads from the network and Chrome's virtual time, which waits on a page's network loads but never on its file loads, waits for them: once to measure it, again while Chrome's time ran out before it finished drawing, then a screenshot of exactly that box."""
    shown = json.dumps({"kind": kind, "source": source}).replace("</", "<\\/")
    with tempfile.TemporaryDirectory(prefix="drive-visual-") as scratch, visual_site(Path(scratch), VISUAL_PAGE.replace("SOURCE", shown, 1)) as page:
        profile = Path(scratch) / "profile"
        measured = (DRAWN.search(headless(page, profile, f"--window-size={VISUAL_WINDOW},{VISUAL_WINDOW}", "--dump-dom")) for _ in range(VISUAL_TRIES))
        if not (drawn := next(filter(None, measured), None)):
            raise PictureError(f"the {kind} did not finish drawing in {VISUAL_TRIES} tries")
        if not (size := SIZE.fullmatch(said := html.unescape(drawn[1]))):
            raise PictureError(said)
        headless(page, profile, f"--window-size={size[1]},{size[2]}", f"--force-device-scale-factor={VISUAL_SCALE}", f"--screenshot={out}")
    if not out.is_file():
        raise PictureError("Chrome wrote no screenshot")


@contextlib.contextmanager
def visual_site(folder: Path, page: str) -> Iterator[str]:
    (folder / "visual.html").write_text(page)
    for name in ("visuals.js", "vendor"):
        (folder / name).symlink_to(VISUALS.parent / name)
    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietFiles, directory=folder)) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/visual.html"
        finally:
            server.shutdown()


class QuietFiles(http.server.SimpleHTTPRequestHandler):
    extensions_map = http.server.SimpleHTTPRequestHandler.extensions_map | {".mjs": "text/javascript"}

    def log_message(self, *args: object) -> None:
        pass


def downloaded(url: str, out: Path) -> None:
    try:
        out.write_bytes(fetched(url))
    except (OSError, github.GithubError) as error:
        raise PictureError(f"{url} did not download: {error}") from None


def fetched(url: str) -> bytes:
    """An image link's bytes, on the gh token when GitHub hosts it, since a private repository answers a bare download with HTTP 404: a repository file's blob, raw or raw.githubusercontent.com link through the contents API, and a user-attachments image from the address GitHub's markdown renderer signs for the token's account, which it does when rendering for any repository, here gh's own public one; any other link, or a local file's, as it is."""
    if file := REPO_FILE.match(url):
        repo = file["repo"] or file["raw_repo"]
        return github.read(
            f"{repo}'s {file['path']} at {file['ref']}", ["gh", "api", "-H", "Accept: application/vnd.github.raw", f"repos/{repo}/contents/{file['path']}?ref={file['ref']}"], text=False
        )
    if ATTACHMENT.match(url):
        rendered = github.read(f"a signed address for {url}", ["gh", "api", "markdown", "-f", f"text=![]({url})", "-f", "mode=gfm", "-f", f"context={SIGNING_CONTEXT}"])
        url = html.unescape(SIGNED.search(rendered)[1])
    with urllib.request.urlopen(url, timeout=DOWNLOAD_SECONDS) as reply:
        return reply.read()


def sources(show_me: str) -> list[tuple[str, str, str]]:
    """Each picture a Show me section holds, in order, as (kind, source, caption): a diagram or compare fence, or a code fence naming its language and holding no sentences, captioned by the line just above it, or an image link, captioned by its alt text. A ```chart is no picture, since cc-slack draws it as Slack's own chart."""
    found = [
        (match.start(), fence_kind(match["language"]), match["source"], match["caption"] or "")
        for match in CAPTIONED.finditer(show_me)
        if pictured(match["language"], match["source"])
    ]
    found += [(match.start(), "image", match[1] or match[2], alt(match)) for match in IMAGE.finditer(show_me)]
    return [(kind, source, caption) for _, kind, source, caption in sorted(found)]


def alt(image: re.Match[str]) -> str:
    """An IMAGE match's alt text, the words he reads for it, or "" for an `<img>` tag."""
    return found[1] if (found := ALT.match(image[0])) else ""


def fence_kind(language: str) -> str:
    return language.lower() if language.lower() in DIAGRAMS else f"code:{language.lower()}"


def raw(language: str, source: str) -> str | None:
    """What a fence holds when it is raw data or tool output, whatever language it names: JSON, YAML, raw rows, log lines, a terminal session, an HTTP exchange, command output or a stack trace, of which a picture shows him nothing."""
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
    if exchange(source):
        return "an HTTP exchange"
    try:
        return "JSON" if isinstance(json.loads(source), dict | list) else None
    except json.JSONDecodeError:
        return None


def exchange(source: str) -> bool:
    """Whether a fence holds an HTTP exchange: a response's status line as curl or httpie prints it, or requests with their answers, a status or a JSON body after an arrow or a status opening a JSON body, on as many lines as the rest, comments narrating them aside. A request alone is the real call, never an exchange."""
    lines = [line for line in source.splitlines() if line.strip()]
    calls = [line for line in lines if HTTP_REQUEST.search(line) or HTTP_ANSWER.search(line)]
    told = [line for line in lines if line not in calls and not COMMENT.match(line)]
    return bool(HTTP_STATUS.search(source)) or any(map(HTTP_REQUEST.search, calls)) and any(map(HTTP_ANSWER.search, calls)) and len(calls) >= len(told)


def spoken(source: str) -> list[str]:
    """A fence's lines with something on them, each Slack link read as its label and each URL and bit of inline code as one mark, so neither reads as code."""
    return [SPAN.sub(lambda span: span[1] or "x", line).strip() for line in source.splitlines() if line.strip()]


def code(source: str) -> bool:
    """Whether a fence naming no language holds code: at least half its lines are statements of code, such as a declaration, an assignment, a call, a command's flag, a comment or a line opening or closing a block."""
    lines = spoken(source)
    return bool(lines) and 2 * sum(bool(CODE_LINE.search(line)) for line in lines) >= len(lines)


def prose(source: str) -> bool:
    """Whether a fence holds sentences, such as a message or a quote, rather than code or a layout of words: no line is indented, aligned in columns, drawn with arrows or box lines, or a statement of code, its lines run about six words or more, and a sentence ends among them."""
    lines = spoken(source)
    if not lines or any(LAYOUT.search(line) for line in source.splitlines() if line.strip()) or any(CODE_LINE.search(line) for line in lines):
        return False
    return sum(len(PROSE_WORD.findall(line)) for line in lines) >= PROSE_WORDS * len(lines) and bool(SENTENCE_END.search("\n".join(lines)))


def quoted(language: str, source: str) -> bool:
    """Whether a fence holds a message or a quote, which his card shows as Slack's own quote of its text, never a picture of it: sentences in a fence of words or code, never a diagram's, raw data or tool output."""
    return language.lower() not in DIAGRAMS and not raw(language, source) and prose(source)


def pictured(language: str, source: str) -> bool:
    """Whether a fence draws as a picture on his card: one naming a language, holding neither raw data, tool output nor sentences, and no chart, which Slack draws as its own."""
    return language.lower() not in (*WORDS, *NATIVE) and not raw(language, source) and not quoted(language, source)


def file_name(caption: str) -> str:
    """A caption as a file name, which Slack titles its picture with: each run of characters a file name cannot hold one space, cut to the most a Slack picture's title shows."""
    return " ".join(UNNAMEABLE.sub(" ", caption).split())[:TITLE_CHARS].rstrip()


def title(kind: str) -> str:
    """A picture's title when no caption line names it: the kind of picture it is, never a bare "Show me"."""
    return TITLES.get(kind) or f"{kind.removeprefix('code:')} code"


def draw(kind: str, source: str, out: Path) -> Path:
    if kind != "image":
        visual_png(kind, source, out)
        return out
    out = out.with_suffix(Path(source.split("?")[0]).suffix or ".png")
    downloaded(source, out)
    return out


def cached(folder: Path, name: str, kind: str, source: str) -> Path:
    """The picture of one source, drawn once under folder and named name, never the source it keeps beside it."""
    renderer = VISUALS.read_text() if kind != "image" else ""
    home = folder / hashlib.sha256(f"{kind}\n{source}{renderer}".encode()).hexdigest()[:16]
    home.mkdir(parents=True, exist_ok=True)
    (home / SOURCE).write_text(json.dumps({"kind": kind, "source": source}))
    if found := sorted(path for path in home.glob(f"{name}.*") if path.name != SOURCE):
        return found[0]
    return draw(kind, source, home / f"{name}.png")


def fill(folder: Path, kept: list[str]) -> None:
    """Write source.json beside each picture drawn before pictures kept their source, from the fence among kept, the drive's kept questions and PR bodies, whose code picture was keyed then by its kind and source alone; a picture none of them holds stays as drawn, all counted in one line, and folder is marked so this runs once."""
    found = {}
    for text in kept:
        for fence in FENCE.finditer(text):
            kind = f"code:{fence[1].lower()}"
            found[hashlib.sha256(f"{kind}\n{fence[2]}".encode()).hexdigest()[:16]] = {"kind": kind, "source": fence[2]}
    bare = [home for home in sorted({png.parent for png in folder.rglob("*.png")}) if not (home / SOURCE).exists()]
    filled = [home for home in bare if home.name in found]
    for home in filled:
        (home / SOURCE).write_text(json.dumps(found[home.name]))
    if bare:
        left = sum(len(list(home.glob("*.png"))) for home in bare if home not in filled)
        print(f"drive pictures: {len(filled)} drawn before pictures kept their source now keep it; {left} keep none the drive can find and stay as drawn", file=sys.stderr)
    (folder / SOURCED).touch()


def shot(folder: Path, name: str, kind: str, source: str, which: str) -> Path:
    """The picture of one source as `cached` draws it under folder, a local image by its file URL; one that does not draw, such as a Mermaid diagram Mermaid cannot parse, raises a DriveError naming which picture and why, since a card or post that leaves it out shows him a Show me with nothing in it."""
    from drive.state import DriveError

    if kind == "image" and not source.startswith(("http://", "https://")):
        if not ((local := Path(source).expanduser()).is_absolute() and local.is_file()):
            raise DriveError(f"{which}: {source} is neither a link nor a file here")
        source = local.as_uri()
    try:
        return cached(folder, name, kind, source)
    except (PictureError, ValueError) as error:
        raise DriveError(f"{which} does not draw: {error}") from None


def pictures(folder: Path, label: str, show_me: str, taken: set[str]) -> list[str]:
    """Local image files for a PR's Show me, drawn once per source under folder, a local image copied in, each also named its caption with no extension, since cc-slack titles a PR card's picture with its file's whole name, and numbered apart from each name in taken, which gains each name it gives, since cc-slack gives each PR of a card every picture whose file name one of the PR's own has; a picture that does not draw raises the DriveError `shot` names it in."""
    drawn = []
    for kind, source, caption in sources(show_me):
        name = apart(file_name(caption) or title(kind), taken)
        found = shot(folder, "show-me", kind, source, f"{label}: its Show me {kind}")
        if not (titled := found.with_name(name)).exists():
            titled.hardlink_to(found)
        taken.add(name)
        drawn.append(str(titled))
    return drawn


def apart(name: str, taken: set[str]) -> str:
    """name, or name numbered from 2 when taken holds it or it is the source a picture keeps beside it."""
    return next(each for each in itertools.chain([name], (f"{name} ({count})" for count in itertools.count(2))) if each not in {*taken, SOURCE})


def drawn(folder: Path, show_me: list[str]) -> list[str]:
    """A question card's Show me with each fenced block that draws as a picture replaced by one line holding it, `![caption](/abs.png)`, which cc-slack draws as an image block where the line sits, or with the pictures beside it as one gallery, titled with the caption line just above the fence, or what kind of picture it is when none is. A fence of sentences, such as a draft message or a quote, becomes `> ` lines under its caption, which Slack shows as a quote of the text, links and all. A ```chart stays as written, since cc-slack draws it as Slack's own chart. A fence of other words, with no language or `text`, which only a block kept before `drive job ask` refused one holds, stays as written with its caption, so a redraw never loses it and Slack draws it as a code block, since a picture of words shows nothing; one with no language that holds code draws as a code picture, its grammar guessed by the renderer. A fence of raw data or tool output leaves the card with its caption, since a picture of JSON or a terminal shows nothing, and a fence that does not draw raises the DriveError `shot` names it in, such as Mermaid's parse error, so no card goes up without its picture."""

    def picture_line(fence: re.Match[str]) -> str:
        language = fence["language"].lower()
        if quoted(language, fence["source"]):
            return "\n".join([*filter(None, [fence["caption"]]), *(f"> {line}".rstrip() for line in fence["source"].rstrip("\n").split("\n"))])
        if language in NATIVE or language in WORDS and not (language == "" and code(fence["source"]) and not raw(language, fence["source"])):
            return fence[0]
        if what := raw(fence["language"], fence["source"]):
            print(f"drive card: its Show me holds {what} in a ```{fence['language']} fence, which leaves the card; {INSTEAD}", file=sys.stderr)
            return ""
        shown = fence_kind(fence["language"])
        caption = fence["caption"] or title(shown)
        which = f'the Show me picture "{caption}", a ```{fence["language"]} fence,'
        return f"![{caption}]({shot(folder, 'show-me', shown, fence['source'], which)})"

    return CAPTIONED.sub(picture_line, "\n".join(show_me)).split("\n")
