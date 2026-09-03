#!/usr/bin/env python3
"""Driver for the design-doc skill.

  design.py scaffold [dir] [--title X] [--slug x] [--example]
  design.py check <dir> [--strict]
  design.py summary-text <dir>
  design.py glossary <dir>
  design.py plainify <dir> [--only DQ3,A2] [--provider slop-cop|claude|codex|none] [--dry-run]
  design.py render-check <dir> [--timeout S]
  design.py pdf [dir]
  design.py build [dir]
  design.py snapshot [dir] [--note X] [--item …] [--force]
  design.py links <dir> [--fetch] [--json] [--missing]

scaffold creates a fresh directory for one design doc — named after the
slug when no dir is given — holding the doc renderer, the executive-summary
fragment, and either the empty starter registers or the tinyq worked
example. check lints the registers: ID shapes and uniqueness, dangling
cross-references, supersession integrity, footnote tokens, the qa-log round
linkage, the Mermaid diagram's structure and its overview, the plain twins,
the handles citations show, noun-phrase decision titles, label
capitalisation, the word caps each section reads at, the glossary, keys and
themes, and the summary deck; errors exit non-zero, warnings are advisory,
and --strict promotes the warnings a published doc must not carry into
errors.
summary-text prints summary.html as plain text, one "## <kind>" section per
panel, the input for the voice gate. glossary prints the recurring terms the
registers never define, as JSON to paste into terms[]. plainify writes a
plain-language twin for every rendered entry that lacks one and a handle for
every entry that has none, through `slop-cop plainify` by default or the
claude or codex CLI, and prints a review table carrying whatever slop-cop
grades against. render-check opens the doc in headless Chrome
over its debugging pipe, waits for the page to report every diagram
rendered, and fails on a Mermaid parse error or a diagram that never
rendered, printing Chrome's stderr and the page's console so a failure
names its cause. pdf
prints the served doc to its design-doc.pdf through the template's print
stylesheet. build compiles the project's author-written components/*.tsx
into components.js with the pinned Vite and Preact pack, installed under
~/.cache/design-doc on first use. snapshot records a revision of the
registers in the project's history directory. links lists every open item
and decision with the pull requests, issues and commits it links, flags the
ones with none, resolves their GitHub state with --fetch and reports an
open item whose closing change landed, or a closed one whose change did
not. Stdlib only.
"""
import argparse, copy, datetime, hashlib, importlib.util, itertools, json, os, re, shutil, subprocess, sys, tempfile, urllib.parse, urllib.request
from html.parser import HTMLParser
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
TEMPLATES = SKILL / "templates"
REFERENCE = SKILL / "reference"
ICON_CACHE = Path.home() / ".cache" / "design-doc"
ICON_LIST = "https://data.jsdelivr.com/v1/package/npm/lucide-static@{version}/flat"
PROJECT_FILES = ("registers.json", "qa-log.json", "NOTES.md", "summary.html")
AI_CONFIG_KEYS = ("endpoint", "model", "key")
SITE_CONFIG_KEYS = ("github",)
AI_CONFIG_OPTIONAL = ("reasoning",)
AI_REASONING = ("low", "medium", "high", "none")
LINK_KINDS = ("pr", "issue", "commit", "doc")
LINK_FIELDS = {"url", "kind", "label", "closes"}
LINKED = (("open", "id"), ("decisions", "id"))
OPEN_STATUSES = {"open", "closed"}
GITHUB_LINK = re.compile(r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/(pull|issues|commit)/([A-Za-z0-9]+)/?(?:[?#].*)?$")
GITHUB_KIND = {"pull": "pr", "issues": "issue", "commit": "commit"}
REPO_SLUG = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
GITHUB_API = "https://api.github.com"
GITHUB_STATE_CLOSED = {"merged", "closed"}

DECISION_STATUSES = {"resolved", "superseded", "open"}
ASSUMPTION_STATUSES = {"working", "validate"}
PANEL_BUDGET = {"thesis": 40, "compare": 120, "numbers": 60, "cost": 90, "poster": 180}
DECK_BUDGET = 350
DECK_SIZE = (3, 5)
HAND_FIGURE_RECTS = 8
FIGURE_MARKS = ("rect", "path", "marker")
TWIN_WORDS = 30
TLDR_WORDS = 20
KEY_RANGE = (3, 8)
TWINNED = (("tldr", "md"), ("constraints", "t"), ("decisions", "r"), ("assumptions", "b"), ("open", "t"))
IDENTIFIED = ("assumptions", "decisions", "arch", "open", "numbers", "paths")
TWIN_KIND = {"tldr": "summary bullet", "constraints": "ground rule", "decisions": "decision",
             "assumptions": "assumption", "open": "open question", "arch": "architecture card",
             "numbers": "numbers table"}
PLAIN_PROVIDERS = ("slop-cop", "claude", "codex", "none")
PLAIN_TIMEOUT = 180
RENDER_TIMEOUT = 60
PLAIN_FORBID = (r"\b(DQ|A|Q|V)\d+\b", r"(?i)\bfinding \d+", r"(?i)\bpass-\d")
SLOP_COP_FORMULA = "yasyf/tap/slop-cop"
MERMAID_HEADER = re.compile(r"^\s*(flowchart|graph)(-elk)?\b")
MERMAID_KEYWORDS = {"flowchart", "flowchart-elk", "graph", "subgraph", "end", "direction", "default",
                    "LR", "RL", "TB", "TD", "BT"}
MERMAID_STATEMENT = re.compile(r"^\s*(?:style|classDef|linkStyle|click)\b.*$", re.M)
MERMAID_FRONTMATTER = re.compile(r"\A\s*---.*?^---[ \t]*$", re.S | re.M)
MERMAID_EDGE_TEXT = re.compile(r"[ox](?:--|-\.|==)\s[^\n]*?\s(?:--|\.-|==)[ox]"
                               r"|(?:--|-\.|==)\s[^\n]*?\s(?:--[->]|-\.-|\.->?|===|==>)")
MERMAID_ARROW = re.compile(r"o[-=.]{2,}o|x[-=.]{2,}x|[<>]?[-=.]{2,}[>xo]?|[<>]?[-=.]+>")
MERMAID_TOKEN = re.compile(r"([A-Za-z0-9_][A-Za-z0-9_-]*)@?")
NODE_DOM_ID = re.compile(r"flowchart-(.+)-\d+$")
FN_TOKEN = re.compile(r"\[\^(\d+)\]")
ID_SHAPES = (r"DQ\d+", r"A\d+", r"Q\d+", r"V\d+", r"c-[a-z0-9-]+")
ID_TOKEN = re.compile(r"\b(?:" + "|".join(ID_SHAPES) + r")\b")
LEADING_ID = re.compile(r"^\s*(" + "|".join(ID_SHAPES) + r")\b")
ID_LIKE = re.compile(r"[A-Z0-9-]")
FINDING_BY_NUMBER = re.compile(r"finding \d+", re.I)
PASS_TOKEN = re.compile(r"\bpass-\d+\b", re.I)
FILE_PATH = re.compile(r"(?<![\w:])(?:~|\.{1,2})?/[\w.@-]+(?:/[\w.@-]+)+"
                       r"|(?<![\w/])[\w.-]+/[\w.-]+/[\w./-]+"
                       r"|\b[\w-]+\.(?:py|json|md|html|go|rs|sh|yml|yaml|toml|svg|css)\b"
                       r"|\b[a-z0-9_][\w-]*\.(?:ts|tsx|js|mjs)\b")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
PAGE_TAG = re.compile(r"<\s*/?\s*(html|head|body|script)\b", re.I)
EMBED_TAG = re.compile(r"<\s*/?\s*(iframe|object|embed)\b", re.I)
EVENT_ATTR = re.compile(r"\s(on[a-z]+)\s*=", re.I)
URL_ATTRS = {"href", "src", "xlink:href"}
URL_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*):")
MEASURED = {"yes", "no"}
LIB_URL = re.compile(r"cdn\.jsdelivr\.net/npm/((?:@[\w.-]+/)?[\w.-]+)@(\d+\.\d+\.\d+)")
PINNED_LIB = re.compile(r"(?<![\w/])((?:@[\w.-]+/)?[A-Za-z][\w.-]*)@(\d+\.\d+\.\d+)")
SECTION_IDS = ("overview", "ground", "architecture", "paths", "numbers", "ceilings", "decisions",
               "assumptions", "open", "footnotes")
HANDLED = ("decisions", "assumptions", "open", "arch", "numbers")
HANDLE_RANGE = (2, 5)
TITLE_WORDS = 12
BANNER_WORDS = 40
TERM_COUNT = 12
OVERVIEW_NODES = 10
CEILING_ROWS = 8
CEILING_CELL_WORDS = 20
ARCH_BLOCKS = 4
ARCH_BLOCK_WORDS = 70
ARCH_LEAD_WORDS = 25
WORD_CAPS = (("constraints", "p", 25, "a ground rule's plain twin"),
             ("terms", "v", 20, "a term's definition"),
             ("decisions", "r", 80, "what we decided"),
             ("decisions", "x", 80, "what we turned down"),
             ("assumptions", "b", 60, "an assumption's basis"),
             ("assumptions", "n", 40, "an assumption's history note"),
             ("open", "t", 40, "an open item's title"),
             ("open", "p", 20, "an open item's plain twin"),
             ("numbers", "sub", 15, "a numbers sub-line"),
             ("numbers", "note", 30, "a numbers note"),
             ("footnotes", "b", 60, "a footnote"))
ACRONYMS = ("API", "SSO", "JWT", "DPoP", "TLS", "mTLS", "HTTP", "HTTPS", "gRPC", "k8s", "S3", "R2", "IAM", "SQL",
            "DB", "ID", "URL", "JSON", "YAML", "CLI", "UI", "UX", "CI", "CD", "PR", "RPM", "TPM", "QPS", "CPU",
            "GPU", "RAM", "AWS", "GCP", "OIDC", "OAuth", "SAML", "DNS", "CDN", "VPC", "RDS", "KMS", "ELK", "SVG",
            "PDF", "HTML", "CSS", "JS", "TSX", "LLM", "AI", "FDE", "SLA", "SLO", "P95", "P99", "RLS", "NLB",
            "EBS", "SNI", "WAF", "DDoS", "GraphQL", "SDK", "WAL", "Postgres", "SQLite", "GitHub", "Kubernetes",
            "Pulumi", "Datadog", "Cloudflare", "WorkOS", "SandSQL", "SandDB")
AMBIGUOUS_PRODUCTS = ("Envoy", "Restate", "Valkey", "Iris", "Sand")
GLOSS_MIN_ENTRIES = 2
GLOSS_MAX = 20
GLOSS_SKIP = {"terms", "diagram", "housekeeping", "acronyms"}
GLOSS_STOP = {"a", "an", "and", "at", "both", "but", "by", "each", "every", "for", "how", "if", "in", "it", "its",
              "no", "not", "of", "on", "one", "our", "that", "the", "their", "then", "these", "this", "those",
              "to", "we", "what", "when", "where", "while", "why", "with"}
GLOSS_ACRONYM = re.compile(r"\b(?=[A-Za-z0-9]*[A-Z][A-Za-z0-9]*[A-Z])[A-Za-z][A-Za-z0-9]{1,15}\b")
GLOSS_PHRASE = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+){1,3}\b")
MERMAID_NODE = re.compile(r"([A-Za-z0-9_][A-Za-z0-9_-]*)\s*(?:\[\(|\(\(|\[\[|\{\{|\[|\(|\{|>)\s*\"?([^\"\]\)\}\n]*)")
LABEL_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[_./-][A-Za-z0-9]+)*")
IDENTIFIER_HEAD = re.compile(r"[A-Za-z0-9]+[_./-]")
CITE_SKIP = {"id", "node", "source", "overview", "file", "kind", "slug", "links", "url"}
SCRIPTS = SKILL / "scripts"
COMPONENT_SCHEMAS = REFERENCE / "components"
COMPONENT_PACK = ICON_CACHE / "components-pack"
COMPONENT_ID = re.compile(r"[a-z][a-z0-9-]*")
EXPR_TOKEN = re.compile(r"[0-9]*\.?[0-9]+|[A-Za-z_][A-Za-z0-9_]*|[-+*/(),]|\s+")
EXPR_FNS = {"min": min, "max": max}
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
TSX_EXPORT = re.compile(r"^export\s+(?:async\s+)?(?:function|const|class)\s+([A-Za-z_]\w*)", re.M)
TSX_EXPORT_LIST = re.compile(r"^export\s*\{([^}]*)\}", re.M)
TSX_EXPORT_DEFAULT = re.compile(r"^export\s+default\b", re.M)
TSX_EXPORT_NAME = re.compile(r"[A-Za-z_]\w*$")
TSX_BANNED = (
    (re.compile(r"\bfetch\s*\("), "calls fetch("),
    (re.compile(r"""\bimport\s*\(\s*["'](?:[a-z]+:)?//"""), "imports from a remote origin"),
    (re.compile(r"\beval\s*\("), "calls eval("),
    (re.compile(r"(?:\.innerHTML\s*=|\bdangerouslySetInnerHTML\b)"), "writes raw HTML"),
)
SVG_TAGS = {"svg", "g", "defs", "title", "desc", "path", "rect", "circle", "ellipse", "line", "polyline",
            "polygon", "text", "tspan", "marker", "use", "symbol", "lineargradient", "radialgradient", "stop",
            "clippath", "mask", "pattern"}
SVG_ATTRS = {"id", "class", "style", "transform", "d", "x", "y", "dx", "dy", "x1", "y1", "x2", "y2", "cx", "cy",
             "r", "rx", "ry", "width", "height", "viewbox", "preserveaspectratio", "fill", "fill-opacity",
             "fill-rule", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "stroke-dasharray",
             "stroke-dashoffset", "stroke-opacity", "opacity", "font-family", "font-size", "font-weight",
             "font-style", "letter-spacing", "text-anchor", "dominant-baseline", "points", "offset",
             "stop-color", "stop-opacity", "gradientunits", "gradienttransform", "spreadmethod", "markerwidth",
             "markerheight", "refx", "refy", "orient", "markerunits", "patternunits", "clip-path", "mask",
             "vector-effect", "paint-order", "xmlns", "role", "aria-label", "aria-hidden"}
SVG_FRAGMENT_HREF = re.compile(r"^#[\w.:-]+$")
SVG_CSS_BAN = re.compile(r"url\s*\(|expression|@import|behavior", re.I)
WHATIF_GRID_MAX = 20000
SCHEMA_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool,
                "number": (int, float), "integer": int}


def foreign_scheme(url: str):
    m = URL_SCHEME.match(re.sub(r"[\s\x00-\x1f]", "", url))
    return m.group(1) if m and m.group(1).lower() not in ("http", "https") else None


def words(s: str) -> int:
    return len(s.split())


def classes_of(attrs) -> set:
    return set((dict(attrs).get("class") or "").split())


class FragmentText(HTMLParser):
    HEADINGS = {f"h{n}": "#" * n for n in range(1, 7)}
    PANEL_HEADINGS = {f"h{n}": "#" * min(6, max(3, n + 1)) for n in range(1, 7)}
    BLOCKS = {"p", "li", "div", "section", "figure", "figcaption", "blockquote", "pre", "tr", "td", "th"} | set(HEADINGS)
    SPACED = {"br", "b", "span", "small"}
    SKIPPED = {"svg", "script", "style"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines, self.buf, self.cells, self.muted, self.panel = [], [], [], [], 0

    def _take(self) -> str:
        text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        self.buf = []
        return text

    def _emit(self, text: str):
        if text:
            self.lines.append(text)

    def handle_starttag(self, tag, attrs):
        classes = classes_of(attrs)
        if tag in self.SKIPPED or (tag == "pre" and "mermaid" in classes):
            self.muted.append(tag)
        if self.muted:
            return
        if tag in self.SPACED:
            self.buf.append(" ")
        elif tag in self.BLOCKS:
            self._emit(self._take())
            if tag == "section" and self.panel:
                self.panel += 1
            elif tag == "section" and "xs-panel" in classes:
                self.panel = 1
                self._emit(f"## {dict(attrs).get('data-kind') or 'panel'}")
            if tag == "tr":
                self.cells = []

    def handle_endtag(self, tag):
        if self.muted:
            if tag == self.muted[-1]:
                self.muted.pop()
            return
        if tag in self.SPACED:
            self.buf.append(" ")
        elif tag in ("td", "th"):
            self.cells.append(self._take())
        elif tag == "tr":
            self.cells.append(self._take())
            row = [c for c in self.cells if c]
            self.cells = []
            if row:
                self._emit("| " + " | ".join(row) + " |")
        elif tag in self.HEADINGS:
            text = self._take()
            marks = (self.PANEL_HEADINGS if self.panel else self.HEADINGS)[tag]
            self._emit(f"{marks} {text}" if text else "")
        elif tag == "li":
            text = self._take()
            self._emit(f"- {text}" if text else "")
        elif tag in self.BLOCKS:
            if tag == "section" and self.panel:
                self.panel -= 1
            self._emit(self._take())

    def handle_data(self, data):
        if not self.muted:
            self.buf.append(data)

    def close(self):
        super().close()
        self._emit(self._take())


def fragment_text(fragment: str) -> str:
    parser = FragmentText()
    parser.feed(fragment)
    parser.close()
    out = []
    for line in parser.lines:
        run = line[:2] in ("- ", "| ")
        if out and not (run and out[-1][:2] == line[:2]):
            out.append("")
        out.append(line)
    return "\n".join(out)


class DeckParser(HTMLParser):
    MUTED = {"svg", "code"}
    BLOCKS = FragmentText.BLOCKS

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.panels, self.muted, self.panel, self.figure, self.sections = [], [], None, None, 0
        self.icons, self.urls, self.stats = [], [], []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = classes_of(attrs)
        self.urls.extend((k, v) for k, v in attrs if k.lower() in URL_ATTRS and v)
        if "xs-stat" in classes:
            self.stats.append(a.get("data-measured"))
        if self.figure is not None and tag == "svg" and (a.get("aria-label") or "").strip():
            self.figure["labelled"] = True
        if a.get("data-icon"):
            self.icons.append(a["data-icon"])
        if self.figure is not None and a.get("data-component"):
            self.figure["host"] = True
        if tag in self.MUTED or (tag == "pre" and "mermaid" in classes):
            self.muted.append(tag)
        if self.muted:
            if self.figure is not None and self.muted[0] == "svg":
                if tag in FIGURE_MARKS:
                    self.figure[tag] += 1
                if "stroke-dasharray" in a:
                    self.figure["dashed"] += 1
            return
        if tag == "section":
            if self.panel is None and "xs-panel" in classes:
                self.panel = {"kind": a.get("data-kind") or "", "text": [], "figures": []}
                self.panels.append(self.panel)
                self.sections = 0
            elif self.panel is not None:
                self.sections += 1
        elif tag == "figure" and self.panel is not None:
            self.figure = {"labelled": bool((a.get("aria-label") or "").strip()), "host": False, "dashed": 0,
                           **{mark: 0 for mark in FIGURE_MARKS}}
            self.panel["figures"].append(self.figure)
        elif tag == "figcaption" and self.figure is not None:
            self.figure["labelled"] = True
        if self.panel is not None and (tag in self.BLOCKS or tag == "br"):
            self.panel["text"].append("\n")

    def handle_endtag(self, tag):
        if self.muted:
            if tag == self.muted[-1]:
                self.muted.pop()
            return
        if tag == "section" and self.panel is not None:
            if self.sections:
                self.sections -= 1
            else:
                self.panel = None
        elif tag == "figure":
            self.figure = None
        if self.panel is not None and tag in self.BLOCKS:
            self.panel["text"].append("\n")

    def handle_data(self, data):
        if not self.muted and self.panel is not None:
            self.panel["text"].append(data)


def mermaid_ids(source: str) -> set:
    text = MERMAID_FRONTMATTER.sub("", source)
    text = re.sub(r"%%.*", "", text)
    text = re.sub(r'"[^"]*"', " ", text)
    text = re.sub(r"\|[^|\n]*\|", " ", text)
    for _ in range(2):
        text = re.sub(r"\[[^\[\]]*\]|\([^()]*\)|\{[^{}]*\}|(?<![-.=])>[^\]\n]*\]", " ", text)
    text = MERMAID_STATEMENT.sub("", text)
    text = MERMAID_EDGE_TEXT.sub(" ", text)
    text = MERMAID_ARROW.sub(" ", text)
    return {t for t in MERMAID_TOKEN.findall(text) if t not in MERMAID_KEYWORDS}


def digest(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12] if path.exists() else None


def snapshot_files(R: dict, root: Path) -> dict:
    files = {"summary": "summary.html"}
    diagram = R.get("diagram")
    if isinstance(diagram, dict) and diagram.get("kind") == "svg":
        files["sysd"] = diagram.get("file") or "sysd.svg"
    elif diagram is None and (root / "sysd.svg").exists():
        files["sysd"] = "sysd.svg"
    return files


def id_matcher(known) -> re.Pattern:
    named = sorted({str(k) for k in known if k and ID_LIKE.search(str(k))}, key=len, reverse=True)
    return re.compile(r"(?<![\w-])(?:" + "|".join(ID_SHAPES + tuple(map(re.escape, named))) + r")(?![\w-])")


def register_ids(R: dict) -> set:
    ids = set()
    for reg in IDENTIFIED:
        ids.update(e.get("id") for e in R.get(reg) or [] if isinstance(e, dict))
    return ids


def register_titles(R: dict) -> dict:
    titles = {}
    for reg in IDENTIFIED:
        for e in R.get(reg) or []:
            if isinstance(e, dict) and e.get("id"):
                title = e.get("t") or e.get("name")
                if isinstance(title, str) and title.strip():
                    titles[str(e["id"])] = title.strip()
    return titles


def load_registers(root: Path, cmd: str):
    try:
        data = json.loads((root / "registers.json").read_text())
    except (OSError, ValueError) as e:
        print(f"{cmd}: cannot load registers.json: {e}", file=sys.stderr)
        return None
    if not isinstance(data, dict):
        print(f"{cmd}: registers.json must be a JSON object", file=sys.stderr)
        return None
    return data


def load_builder():
    spec = importlib.util.spec_from_file_location("build_pdf", Path(__file__).resolve().parent / "build-pdf.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def slugify(s: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-") or "design"


def scaffold(args) -> int:
    title = args.title
    slug = args.slug or (slugify(title) if title else ("tinyq" if args.example else None))
    if args.dir:
        dest = Path(args.dir)
    elif slug:
        dest = Path.cwd() / slug
    else:
        print("scaffold: pass --title (the directory is named after its slug) or an explicit directory.", file=sys.stderr)
        return 1
    if dest.exists() and any(dest.iterdir()):
        print(f"scaffold: {dest} exists and is not empty; refusing to overwrite.", file=sys.stderr)
        return 1
    dest.mkdir(parents=True, exist_ok=True)

    src = TEMPLATES / ("example" if args.example else "starter")
    (dest / "design-doc.html").write_text((TEMPLATES / "design-doc.html").read_text())
    for name in PROJECT_FILES:
        shutil.copy(src / name, dest / name)

    if not args.example:
        today = datetime.date.today().isoformat()
        for name in ("registers.json", "NOTES.md"):
            p = dest / name
            p.write_text(p.read_text()
                         .replace("PROJECT_TITLE", title or "Untitled design")
                         .replace("PROJECT_SLUG", slug or "design")
                         .replace("PROJECT_DATE", today))

    print(f"scaffolded {dest} ({'tinyq example' if args.example else 'starter'})")
    print(f"serve:  cd {dest} && python3 -m http.server 8641")
    print(f"check:  {Path(__file__).name} check {dest}")
    print(f"pdf:    {Path(__file__).name} pdf {dest}")
    return 0


def pdf(args) -> int:
    return load_builder().build(Path(args.dir))


def snapshot(args) -> int:
    root = Path(args.dir)
    registers_path = root / "registers.json"
    data = load_registers(root, "snapshot")
    if data is None:
        return 1

    meta = data.setdefault("meta", {})
    revisions = meta.get("revisions") or []
    last = max(meta.get("rev") or 0, max((r.get("rev") or 0 for r in revisions), default=0))

    files = {k: digest(root / name) for k, name in snapshot_files(data, root).items() if (root / name).exists()}
    recorded = (revisions[-1].get("files") or {}) if revisions and isinstance(revisions[-1], dict) else {}
    changed = sorted(k for k in set(files) | set(recorded) if files.get(k) != recorded.get(k))

    previous_path = root / "history" / f"rev-{last}.json"
    if not args.force and not changed and previous_path.exists():
        try:
            previous = json.loads(previous_path.read_text())
        except (OSError, ValueError):
            previous = None
        if isinstance(previous, dict):
            current = copy.deepcopy(data)
            previous = copy.deepcopy(previous)
            for candidate in (current, previous):
                candidate_meta = candidate.get("meta", {})
                for key in ("rev", "revisions", "date"):
                    candidate_meta.pop(key, None)
            if json.dumps(current, sort_keys=True) == json.dumps(previous, sort_keys=True):
                print(f"snapshot: nothing changed since rev {last} — no registers or summary edits (use --force to record anyway)")
                return 0

    rev = last + 1
    meta["rev"] = rev
    meta["revisions"] = revisions
    entry = {"rev": rev, "date": datetime.date.today().isoformat(), "note": args.note}
    if args.item:
        entry["items"] = args.item
    if changed and revisions:
        entry["changed"] = changed
    if files:
        entry["files"] = files
    revisions.append(entry)
    payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    previous_path = root / "history" / f"rev-{rev}.json"
    previous_path.parent.mkdir(parents=True, exist_ok=True)
    previous_path.write_text(payload)
    registers_path.write_text(payload)
    note = f" (changed: {', '.join(entry['changed'])})" if entry.get("changed") else ""
    print(f"snapshot: rev {rev} recorded → history/rev-{rev}.json{note}")
    return 0


def summary_text(args) -> int:
    path = Path(args.dir) / "summary.html"
    if not path.exists():
        print(f"summary-text: {path} not found.", file=sys.stderr)
        return 1
    print(fragment_text(path.read_text()))
    return 0


def twin_entries(R):
    for reg, field in TWINNED:
        for i, e in enumerate(R.get(reg) or []):
            if isinstance(e, dict):
                yield reg, i, e, field


def entry_id(reg: str, i: int, e: dict) -> str:
    return str(e.get("id") or f"{reg}[{i}]")


def original_text(reg: str, e: dict) -> str:
    if reg == "tldr":
        parts = [e.get("md")]
    elif reg == "decisions":
        parts = [e.get("t"), e.get("r"), e.get("x")]
    elif reg == "assumptions":
        parts = [e.get("t"), e.get("b"), e.get("n")]
    else:
        parts = [e.get("t")]
    return " ".join(str(p).strip() for p in parts if isinstance(p, str) and p.strip())


def twin_issues(twin: str, original: str, ids: re.Pattern):
    issues = []
    n = words(twin)
    if n > TWIN_WORDS and n * 3 > words(original):
        issues.append(f"{n} words; keep it under {TWIN_WORDS} or a third of the original")
    ids = sorted(set(ids.findall(twin)))
    if ids:
        issues.append("names register ids " + ", ".join(ids))
    paths = sorted(set(FILE_PATH.findall(twin)))
    if paths:
        issues.append("names file paths " + ", ".join(paths))
    if FINDING_BY_NUMBER.search(twin) or PASS_TOKEN.search(twin):
        issues.append("names a review finding or pass by number")
    return issues


def with_twin(e: dict, field: str, twin: str) -> dict:
    out = {}
    for k, v in e.items():
        if k == "p":
            continue
        out[k] = v
        if k == field:
            out["p"] = twin
    out.setdefault("p", twin)
    return out


def ask_plain(provider: str, prompt: str, reg: str, e: dict, original: str) -> str:
    request = f"{prompt}\n\nKind: {TWIN_KIND[reg]}\n"
    if isinstance(e.get("t"), str) and reg != "open":
        request += f"Title: {e['t']}\n"
    request += f"Text: {original}\n"
    if provider == "claude":
        out = subprocess.run(["env", "-u", "CLAUDECODE", "claude", "-p", "--model", "claude-haiku-4-5", request],
                             capture_output=True, text=True, check=True, timeout=PLAIN_TIMEOUT).stdout
    else:
        with tempfile.TemporaryDirectory() as scratch:
            reply = Path(scratch) / "reply.md"
            subprocess.run(["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only",
                            "--output-last-message", str(reply), request],
                           capture_output=True, text=True, check=True, timeout=PLAIN_TIMEOUT)
            out = reply.read_text()
    return " ".join(out.split()).strip("\"“” ")


def slop_cop_binary():
    binary = os.environ.get("SLOP_COP") or shutil.which("slop-cop")
    if binary is None:
        print(f"plainify: no slop-cop found; install it with `brew install {SLOP_COP_FORMULA}`, point SLOP_COP at "
              "the binary, or pass --provider claude", file=sys.stderr)
    return binary


def ask_plain_batch(binary: str, titles: dict, batch: list, max_words: int = TWIN_WORDS) -> dict:
    with tempfile.TemporaryDirectory() as scratch:
        glossary = Path(scratch) / "glossary.json"
        glossary.write_text(json.dumps(titles, ensure_ascii=False))
        cmd = [binary, "plainify", "-", "--json", "--max-words", str(max_words),
               "--timeout", f"{PLAIN_TIMEOUT}s", "--name-by-title", "--glossary", str(glossary)]
        for pattern in PLAIN_FORBID:
            cmd += ["--forbid", pattern]
        out = subprocess.run(cmd, input=json.dumps(batch), capture_output=True, text=True,
                             check=True, timeout=PLAIN_TIMEOUT * 2 * len(batch) + 60).stdout
    return {r["id"]: r for r in json.loads(out)}


def graded_issues(result: dict) -> list:
    issues = []
    if result.get("truncated"):
        issues.append(f"slop-cop truncated it at {result['words']} words")
    for violation in result.get("violations") or []:
        issues.append(f"slop-cop: {violation}")
    return issues


def first_line(s: str, width: int = 72) -> str:
    line = s.strip().splitlines()[0] if s.strip() else ""
    return line if len(line) <= width else line[:width - 1].rstrip() + "…"


def print_review_table(rows):
    print("| id | original | plain twin | issues |")
    print("|---|---|---|---|")
    for ident, original, twin, issues in rows:
        cells = (ident, original, twin, "; ".join(issues))
        print("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")


def plainify(args) -> int:
    root = Path(args.dir)
    R = load_registers(root, "plainify")
    if R is None:
        return 1
    prompt = (REFERENCE / "plain.md").read_text().strip()
    only = {s.strip() for s in args.only.split(",") if s.strip()} if args.only else None
    todo, seen = [], set()
    for reg, i, e, field in twin_entries(R):
        ident = entry_id(reg, i, e)
        seen.add(ident)
        if (ident in only) if only is not None else not e.get("p"):
            todo.append((reg, i, e, field, ident))
    if only is not None and only - seen:
        print(f"plainify: no rendered entry is called {', '.join(sorted(only - seen))}", file=sys.stderr)
        return 1

    graded = {}
    if args.provider == "slop-cop" and todo:
        binary = slop_cop_binary()
        if binary is None:
            return 1
        batch = [{"id": ident, "text": original_text(reg, e)} for reg, _, e, _, ident in todo]
        try:
            graded = ask_plain_batch(binary, register_titles(R), batch)
        except (OSError, ValueError, subprocess.SubprocessError) as err:
            detail = err.stderr.strip() if isinstance(err, subprocess.CalledProcessError) and err.stderr else err
            print(f"plainify: slop-cop failed: {detail}", file=sys.stderr)
            return 1

    rows, written = [], 0
    ids = id_matcher(register_ids(R))
    for reg, i, e, field, ident in todo:
        original = original_text(reg, e)
        twin, issues = "", []
        if args.provider == "slop-cop":
            twin, issues = graded[ident]["plain"].strip(), graded_issues(graded[ident])
        elif args.provider != "none":
            try:
                twin = ask_plain(args.provider, prompt, reg, e, original)
            except (OSError, subprocess.SubprocessError) as err:
                print(f"plainify: {args.provider} failed on {ident}: {err}", file=sys.stderr)
                return 1
        if twin:
            issues += twin_issues(twin, original, ids)
        rows.append((ident, first_line(original), twin, issues))
        if twin and not args.dry_run:
            R[reg][i] = with_twin(e, field, twin)
            written += 1
    print_review_table(rows)
    if not todo:
        print("plainify: every rendered entry has a plain twin")
    elif args.provider == "none":
        print(f"plainify: {len(todo)} entr{'y' if len(todo) == 1 else 'ies'} need a twin")
    handles, handle_rows = draft_handles(args, R)
    if written or handles:
        (root / "registers.json").write_text(json.dumps(R, indent=2, ensure_ascii=False) + "\n")
        if written:
            print(f"plainify: wrote {written} twin(s); read each against its original before snapshot")
        if handles:
            print(f"plainify: wrote {handles} handle(s) h; read each against its title before snapshot")
    flagged = sum(1 for _, _, draft, issues in rows + handle_rows if draft and issues)
    if flagged:
        print(f"plainify: {flagged} draft(s) carry issues in the tables above; rewrite each before snapshot")
    return 0


class RenderedDom(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rendered, self.failed, self.syntax_error, self.rendered_ids, self.muted = 0, [], False, set(), []
        self.unmounted = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("data-component") and a.get("data-mounted") != "1":
            self.unmounted.append(a["data-component"])
        if a.get("data-id"):
            self.rendered_ids.add(a["data-id"])
        m = NODE_DOM_ID.search(a.get("id") or "")
        if m:
            self.rendered_ids.add(m.group(1))
        if tag in ("script", "style"):
            self.muted.append(tag)
        if tag == "svg" and "mmd" in classes_of(attrs):
            self.rendered += 1
        if a.get("data-failed") == "1":
            self.failed.append(first_line(a.get("data-source") or "", 60) or a.get("id") or tag)

    def handle_endtag(self, tag):
        if self.muted and tag == self.muted[-1]:
            self.muted.pop()

    def handle_data(self, data):
        if not self.muted and "Syntax error" in data:
            self.syntax_error = True


def render_check(args) -> int:
    root = Path(args.dir)
    R = load_registers(root, "render-check")
    if R is None:
        return 1
    builder = load_builder()
    page = builder.doc_page(root)
    if not page:
        print(f"render-check: {root} holds no design-doc.html or index.html.", file=sys.stderr)
        return 1
    chrome = builder.Chrome(builder.require_chrome())
    server, base = builder.serve(root)
    problems, html = [], ""
    try:
        session = builder.open_page(chrome, base + page)
        state = builder.wait_ready(chrome, session, args.timeout)
        if state["ready"] != "1":
            problems.append(builder.ready_problem(state, args.timeout))
        html = builder.evaluate(chrome, session, builder.DOM_JS)
    except builder.ChromeError as e:
        problems.append(str(e))
    finally:
        report = builder.diagnostics(chrome)
        server.shutdown()
        chrome.close()

    dom = RenderedDom()
    dom.feed(html)
    dom.close()
    cause = "its source has a syntax error" if dom.rendered or dom.syntax_error else f"no diagram rendered at all, so {builder.NETWORK_HINT}"
    for label in dom.failed:
        problems.append(f"Mermaid did not render {label!r}: {cause}")
    for name in sorted(set(dom.unmounted)):
        problems.append(f"the component {name!r} never mounted, so the page shows its fallback")
    if dom.syntax_error and not dom.failed:
        problems.append("a rendered diagram reports a syntax error")
    wanted = set()
    if (R.get("diagram") or {}).get("kind") == "mermaid":
        if not dom.rendered:
            problems.append(f"the system diagram never rendered; {builder.NETWORK_HINT}")
        wanted = {c["node"] for c in R.get("arch", []) if c.get("node")}
        wanted |= {g[4] for p in R.get("paths", []) for g in p.get("segs", []) if len(g) > 4}
        missing = sorted(wanted - dom.rendered_ids)
        if missing:
            problems.append("diagram ids never rendered: " + ", ".join(missing))
    for msg in chrome.console:
        if re.search(r"parse error|syntax error|mermaid|diagram", msg, re.I):
            problems.append("console: " + first_line(msg, 160))
    for p in problems:
        print(f"ERROR: {p}")
    if problems:
        print("\n".join(report))
        return 1
    print(f"render-check: {dom.rendered} Mermaid diagram(s) rendered, {len(wanted)} diagram id(s) resolved")
    return 0


class Report:
    def __init__(self, strict=False):
        self.errors, self.warnings, self.strict = [], [], strict

    def err(self, msg):
        self.errors.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)

    def strict_warn(self, msg):
        (self.errors if self.strict else self.warnings).append(msg)

    def finish(self) -> int:
        for m in self.errors:
            print(f"ERROR: {m}")
        for m in self.warnings:
            print(f"warn:  {m}")
        print(f"{len(self.errors)} error(s), {len(self.warnings)} warning(s)")
        return 1 if self.errors else 0


def walk_strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, list):
        for v in node:
            yield from walk_strings(v)
    elif isinstance(node, dict):
        for v in node.values():
            yield from walk_strings(v)


def check_ids(rep, entries, key, pattern, label):
    seen = set()
    for e in entries:
        i = e.get(key)
        if i is None:
            rep.err(f"{label}: entry missing '{key}': {e}")
            continue
        if i in seen:
            rep.err(f"{label}: duplicate id {i}")
        seen.add(i)
        if pattern and not re.fullmatch(pattern, str(i)):
            rep.err(f"{label}: id {i!r} does not match {pattern}")
    return seen


def lint_question(rep, where, s):
    if not isinstance(s, str) or not s.strip():
        return
    lead = LEADING_ID.match(s)
    if lead:
        rep.warn(f"{where} opens with the register id {lead.group(1)}; ask a question a person can answer cold and leave the id to the fork block")
    if FINDING_BY_NUMBER.search(s):
        rep.warn(f"{where} names a review finding by number; say what the reviewer found in words")


def github_ref(url: str):
    m = GITHUB_LINK.match(url)
    if not m:
        return None
    owner, repo, path, ref = m.groups()
    kind = GITHUB_KIND[path]
    if kind == "commit":
        if not re.fullmatch(r"[0-9a-f]{7,40}", ref):
            return None
        return {"owner": owner, "repo": repo, "kind": kind, "sha": ref, "key": f"{owner}/{repo}@{ref[:7]}"}
    if not ref.isdigit():
        return None
    return {"owner": owner, "repo": repo, "kind": kind, "n": int(ref), "key": f"{owner}/{repo}#{ref}"}


def normalise_link(link):
    if isinstance(link, str):
        link = {"url": link}
    if not isinstance(link, dict):
        return None, f"{link!r} is neither a URL string nor an object with 'url'"
    url = link.get("url")
    if not (isinstance(url, str) and url.strip()):
        return None, f"{link!r} has no 'url'"
    url = url.strip()
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        return None, f"{url} is not an https:// URL"
    extra = sorted(set(link) - LINK_FIELDS)
    if extra:
        return None, f"{url} carries {', '.join(map(repr, extra))}; a link is {{url, kind?, label?, closes?}}"
    gh = github_ref(url)
    kind = link.get("kind")
    if kind is not None and kind not in LINK_KINDS:
        return None, f"{url}: kind {kind!r} not in {', '.join(LINK_KINDS)}"
    if gh and kind and kind != gh["kind"]:
        return None, f"{url}: kind {kind!r} disagrees with the URL, which is a GitHub {gh['kind']}"
    if not gh and kind in ("pr", "issue", "commit"):
        return None, f"{url}: kind {kind!r} needs a github.com pull, issues or commit URL"
    kind = kind or (gh["kind"] if gh else "doc")
    label = link.get("label")
    if label is not None and not (isinstance(label, str) and label.strip()):
        return None, f"{url}: label must be a non-empty string"
    closes = link.get("closes")
    if closes is not None and not isinstance(closes, bool):
        return None, f"{url}: closes must be true or false"
    if "closes" in link and kind not in ("pr", "issue"):
        return None, f"{url}: closes belongs on a pull request or an issue, not a {kind}"
    out = {"url": url, "kind": kind, "closes": bool(closes)}
    if label:
        out["label"] = label.strip()
    if gh:
        out["gh"] = gh
    return out, None


def entry_links(rep, where, entry, closes_ok: bool) -> list:
    raw = entry.get("links") if isinstance(entry, dict) else None
    if raw is None:
        return []
    if not isinstance(raw, list):
        rep.err(f"{where}: links must be a list of URLs or {{url, kind?, label?, closes?}} objects")
        return []
    out = []
    for link in raw:
        norm, problem = normalise_link(link)
        if problem:
            rep.err(f"{where}: link {problem}")
            continue
        if not closes_ok and isinstance(link, dict) and "closes" in link:
            rep.err(f"{where}: link {norm['url']} carries closes; it belongs on the open item the change retires")
            continue
        out.append(norm)
    return out


def check_links(rep, R):
    repo = R.get("meta", {}).get("repo")
    if repo is not None and not (isinstance(repo, str) and REPO_SLUG.match(repo)):
        rep.err(f"meta.repo {repo!r} is not an owner/repo slug")
    seen = {}
    for o in R.get("open", []):
        where = f"open {o.get('id')}"
        if "s" in o and o["s"] not in OPEN_STATUSES:
            rep.err(f"{where}: status {o['s']!r} not in {sorted(OPEN_STATUSES)}")
        for link in entry_links(rep, where, o, True):
            seen.setdefault(link["url"], []).append(where)
    for d in R.get("decisions", []):
        where = f"decision {d.get('id')}"
        for link in entry_links(rep, where, d, False):
            seen.setdefault(link["url"], []).append(where)
    for f in R.get("findings", []):
        if len(f) == 5:
            entry_links(rep, f"finding {f[0]}", {"links": f[4]}, False)
    for url, places in seen.items():
        if len(places) > 1:
            rep.warn(f"link {url} appears on {', '.join(places)}; one entry usually owns a change")


def check_diagram(rep, R, root):
    diagram = R.get("diagram")
    sysd = root / "sysd.svg"
    if diagram is None:
        if sysd.exists():
            rep.warn("sysd.svg is a hand-drawn diagram; move it into registers.json as diagram {kind: \"mermaid\"} for pan, zoom, and card links")
            if "viewBox" not in sysd.read_text():
                rep.strict_warn("sysd.svg has no viewBox; the doc cannot scale the diagram to the reading column")
        else:
            rep.warn("registers.json has no diagram; the doc opens without a system diagram")
        return None
    if not isinstance(diagram, dict):
        rep.err("diagram must be an object with 'kind'")
        return None
    caption = diagram.get("caption")
    if caption is not None and not (isinstance(caption, str) and caption.strip()):
        rep.err("diagram.caption must be a non-empty string")
    kind = diagram.get("kind")
    if kind == "mermaid":
        source = diagram.get("source")
        if not (isinstance(source, str) and source.strip()):
            rep.err("diagram.source must be a non-empty Mermaid string")
            return None
        body = re.sub(r"%%.*", "", MERMAID_FRONTMATTER.sub("", source)).strip()
        if not MERMAID_HEADER.match(body):
            rep.err("diagram.source must open with a flowchart or graph header")
        if "TODO" in source or "TODO" in (caption or ""):
            rep.strict_warn("diagram is still the scaffold placeholder (it carries a TODO)")
        return mermaid_ids(source)
    if kind == "svg":
        name = diagram.get("file") or "sysd.svg"
        path = root / name
        if not path.exists():
            rep.err(f"diagram.kind is svg but {name} is missing")
        else:
            rep.warn(f"{name} is a hand-drawn diagram; a Mermaid diagram gets pan, zoom, and card links")
            if "viewBox" not in path.read_text():
                rep.strict_warn(f"{name} has no viewBox; the doc cannot scale the diagram to the reading column")
        return None
    rep.err(f"diagram.kind must be \"mermaid\" or \"svg\", not {kind!r}")
    return None


def check_twins(rep, R, root, d_ids, known):
    ids = id_matcher(known)
    themes = R.get("themes")
    if themes is None:
        themes = {}
    elif not (isinstance(themes, dict) and all(isinstance(k, str) and isinstance(v, str) and v.strip() for k, v in themes.items())):
        rep.err("themes must map theme keys to non-empty labels")
        themes = {}
    used_themes = set()
    for reg in ("decisions", "assumptions"):
        entries = R.get(reg) or []
        keys = 0
        for e in entries:
            ident = e.get("id")
            if "key" in e and not isinstance(e["key"], bool):
                rep.err(f"{ident}: key must be true or false")
            keys += e.get("key") is True
            theme = e.get("theme")
            if theme is None:
                rep.strict_warn(f"{ident} has no theme; the {reg} section groups by it")
            elif theme not in themes:
                rep.err(f"{ident}: theme {theme!r} is not defined in themes")
            else:
                used_themes.add(theme)
        if entries:
            lo = min(KEY_RANGE[0], len(entries))
            if not lo <= keys <= KEY_RANGE[1]:
                rep.strict_warn(f"{reg}: {keys} entries carry key: true; mark {lo}–{KEY_RANGE[1]} so the section opens on spotlight cards")
    for t in themes:
        if t not in used_themes:
            rep.warn(f"themes[{t}] is used by no decision or assumption")

    for o in R.get("open", []):
        blocks = o.get("blocks")
        if blocks is None:
            continue
        if not (isinstance(blocks, list) and all(isinstance(b, str) for b in blocks)):
            rep.err(f"open {o.get('id')}: blocks must be a list of decision ids")
            continue
        for b in blocks:
            if b not in d_ids:
                rep.err(f"open {o.get('id')}: blocks {b}, which is not a decision")

    previous = {}
    rev = (R.get("meta") or {}).get("rev")
    history_path = root / "history" / f"rev-{rev}.json"
    if rep.strict and rev and history_path.exists():
        try:
            hist = json.loads(history_path.read_text())
        except (OSError, ValueError):
            hist = {}
        if isinstance(hist, dict):
            previous = {(reg, entry_id(reg, i, e)): (original_text(reg, e), e.get("p")) for reg, i, e, _ in twin_entries(hist)}

    for reg, i, e in ((reg, i, e) for reg, _ in TWINNED for i, e in enumerate(R.get(reg) or [])):
        where = f"{reg}[{i}]" if not isinstance(e, dict) else entry_id(reg, i, e)
        if not isinstance(e, dict):
            rep.err(f"{where} is not an object; every {reg} entry carries its wording and a plain twin p")
            continue
        if reg == "tldr" and not (isinstance(e.get("md"), str) and e["md"].strip()):
            rep.err(f"{where}.md is missing or empty")
        p = e.get("p")
        if p is None:
            rep.strict_warn(f"{where} has no plain twin p; write one or run design.py plainify")
            continue
        if not (isinstance(p, str) and p.strip()):
            rep.err(f"{where}.p must be a non-empty string")
            continue
        original = original_text(reg, e)
        for issue in twin_issues(p, original, ids):
            rep.strict_warn(f"{where}.p {issue}")
        if reg == "tldr" and words(p) > TLDR_WORDS:
            rep.warn(f"{where}.p is {words(p)} words; the tl;dr reads best at {TLDR_WORDS} or fewer")
        before = previous.get((reg, where))
        if before and before[0] != original and before[1] == p:
            rep.err(f"{where}: the wording changed since rev {rev} but the plain twin did not; rewrite p or run design.py plainify --only {where}")


def check_summary_deck(rep, fragment: str):
    parser = DeckParser()
    parser.feed(fragment)
    parser.close()
    for attr, url in parser.urls:
        scheme = foreign_scheme(url)
        if scheme:
            rep.err(f"summary.html has a {scheme}: URL in {attr}; link to a document over http(s) or a relative path")
    for measured in parser.stats:
        if measured not in MEASURED:
            rep.strict_warn("summary.html has an .xs-stat without data-measured=\"yes\" or \"no\"; every number is measured or tagged estimated")
            break
    panels = parser.panels
    if not panels:
        rep.strict_warn("summary.html has no <section class=\"xs-panel\">; the summary is a deck of 3–5 panels or one poster")
        return
    kinds = [p["kind"] for p in panels]
    poster = kinds == ["poster"]
    if not poster:
        if not DECK_SIZE[0] <= len(panels) <= DECK_SIZE[1]:
            rep.strict_warn(f"summary.html has {len(panels)} panels; a deck is {DECK_SIZE[0]}–{DECK_SIZE[1]} panels, or exactly one poster")
        if kinds[0] != "thesis":
            rep.strict_warn(f"summary.html opens on a {kinds[0] or 'kind-less'} panel; the first panel is the thesis")
        if kinds.count("thesis") > 1:
            rep.strict_warn("summary.html has more than one thesis panel; the thesis opens the deck once")
        if "poster" in kinds:
            rep.strict_warn("summary.html puts a poster beside other panels; a poster is the whole summary")
    total = 0
    for i, panel in enumerate(panels, 1):
        kind = panel["kind"]
        label = f"summary.html panel {i} ({kind or 'no data-kind'})"
        if kind not in PANEL_BUDGET:
            rep.strict_warn(f"{label}: data-kind must be one of {', '.join(PANEL_BUDGET)}")
        text = "".join(panel["text"])
        n = words(text)
        total += n
        budget = PANEL_BUDGET.get(kind)
        if budget and n > budget:
            rep.strict_warn(f"{label} is {n} words; the budget is {budget}")
        if not panel["figures"]:
            rep.strict_warn(f"{label} has no <figure>; every panel carries one figure")
        for figure in panel["figures"]:
            if not figure["labelled"]:
                rep.strict_warn(f"{label} has a <figure> with no aria-label or figcaption")
            if kind == "compare" and not figure["host"] and figure["rect"] >= HAND_FIGURE_RECTS:
                rep.warn(f"{label} draws its figure by hand, {figure['rect']} rects and {figure['path']} paths; "
                         "a columns-with-callouts figure is a dd.flow, a two-lane figure is a dd.lanes; both lay themselves out")
        for sentence in (s.strip() for s in SENTENCE_END.split(text)):
            lead = LEADING_ID.match(sentence)
            if lead:
                rep.strict_warn(f"{label} opens a sentence with the register id {lead.group(1)}: {first_line(sentence, 60)!r}")
        if FINDING_BY_NUMBER.search(text) or PASS_TOKEN.search(text):
            rep.strict_warn(f"{label} names a review finding or pass by number; say what was found in words")
    if not poster and total > DECK_BUDGET:
        rep.strict_warn(f"summary.html is {total} words across its panels; the deck budget is {DECK_BUDGET}")
    if parser.icons:
        check_icons(rep, parser.icons)


def lucide_names(version: str) -> set:
    cache = ICON_CACHE / f"lucide-static@{version}.json"
    if not cache.exists():
        with urllib.request.urlopen(ICON_LIST.format(version=version), timeout=30) as r:
            files = json.load(r)["files"]
        names = sorted(f["name"][len("/icons/"):-len(".svg")] for f in files
                       if f["name"].startswith("/icons/") and f["name"].endswith(".svg"))
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(names))
    return set(json.loads(cache.read_text()))


def check_icons(rep, icons):
    version = dict(LIB_URL.findall((TEMPLATES / "design-doc.html").read_text()))["lucide-static"]
    try:
        known = lucide_names(version)
    except (OSError, ValueError, KeyError) as e:
        rep.warn(f"could not fetch the lucide-static@{version} icon list ({e}); icon names were not checked")
        return
    for name in sorted(set(icons) - known):
        rep.strict_warn(f"summary.html names the icon {name!r}, which lucide-static@{version} does not ship; the slot renders empty")


def check_libs(rep):
    template = TEMPLATES / "design-doc.html"
    schema = REFERENCE / "schema.md"
    pinned = dict(LIB_URL.findall(template.read_text()))
    stated = dict(PINNED_LIB.findall(schema.read_text())) if schema.exists() else {}
    for name, version in sorted(pinned.items()):
        if stated.get(name) != version:
            rep.warn(f"templates/design-doc.html pins {name}@{version} but reference/schema.md states {stated.get(name) or 'no version'}; bump both together")


def check(args) -> int:
    rep = Report(args.strict)
    root = Path(args.dir)
    try:
        R = json.loads((root / "registers.json").read_text())
    except (OSError, ValueError) as e:
        print(f"ERROR: cannot load registers.json: {e}")
        return 1
    if not isinstance(R, dict):
        print("ERROR: registers.json must be a JSON object")
        return 1

    meta = R.get("meta", {})
    for k in ("title", "slug", "date"):
        if not meta.get(k):
            rep.err(f"meta.{k} is missing or empty")

    if "draft" in meta and not isinstance(meta["draft"], bool):
        rep.err("meta.draft must be true or false")
    if "draftNote" in meta and not (isinstance(meta["draftNote"], str) and meta["draftNote"].strip()):
        rep.err("meta.draftNote must be a non-empty string")

    home = meta.get("homeLink")
    if home is not None:
        if not isinstance(home, dict):
            rep.err("meta.homeLink must be an object with 'href' and 'label'")
        else:
            for k in ("href", "label"):
                if not (isinstance(home.get(k), str) and home[k].strip()):
                    rep.err(f"meta.homeLink.{k} must be a non-empty string")
            if isinstance(home.get("href"), str):
                scheme = foreign_scheme(home["href"])
                if scheme:
                    rep.err(f"meta.homeLink.href uses the {scheme}: scheme; it must be relative or an http(s) URL")

    if "rev" in meta or "revisions" in meta:
        rev = meta.get("rev")
        rev_valid = isinstance(rev, int) and not isinstance(rev, bool) and rev > 0
        if not rev_valid:
            rep.err("meta.rev must be a positive integer")
        revisions = meta.get("revisions")
        if not isinstance(revisions, list) or not revisions:
            rep.err("meta.revisions must be a non-empty list")
        else:
            revs = []
            for i, revision in enumerate(revisions):
                revision_rev = revision.get("rev") if isinstance(revision, dict) else None
                if not isinstance(revision_rev, int) or isinstance(revision_rev, bool):
                    rep.err(f"meta.revisions[{i}].rev is missing or is not an integer")
                elif revision_rev < 1:
                    rep.err(f"meta.revisions[{i}].rev must be >= 1")
                else:
                    revs.append(revision_rev)
                    history_path = root / "history" / f"rev-{revision_rev}.json"
                    if not history_path.exists():
                        rep.strict_warn(f"history/rev-{revision_rev}.json is missing; the changes-since picker cannot diff against it")
                    else:
                        try:
                            hist = json.loads(history_path.read_text())
                        except (OSError, ValueError) as e:
                            rep.warn(f"history/rev-{revision_rev}.json does not parse: {e}")
                        else:
                            if not isinstance(hist, dict):
                                rep.warn(f"history/rev-{revision_rev}.json is not a JSON object")
                revision_date = revision.get("date") if isinstance(revision, dict) else None
                if not isinstance(revision_date, str) or not revision_date:
                    rep.err(f"meta.revisions[{i}].date is missing or empty")
                for key in ("items", "changed"):
                    if isinstance(revision, dict) and key in revision:
                        values = revision[key]
                        if not isinstance(values, list) or not all(isinstance(x, str) and x.strip() for x in values):
                            rep.err(f"meta.revisions[{i}].{key} must be a list of non-empty strings")
                revision_note = revision.get("note") if isinstance(revision, dict) else None
                if isinstance(revision_note, str) and len(revision_note) > 90:
                    rep.warn(f"meta.revisions[{i}].note is {len(revision_note)} chars; keep it a short reader-facing headline and move detail into --item bullets")
            last_revision = revisions[-1]
            last_rev = last_revision.get("rev") if isinstance(last_revision, dict) else None
            if rev_valid and isinstance(last_rev, int) and not isinstance(last_rev, bool) and rev != last_rev:
                rep.err(f"meta.rev {rev} does not match last meta.revisions rev {last_rev}")
            if len(revs) == len(revisions):
                if any(a >= b for a, b in zip(revs, revs[1:])):
                    rep.err("meta.revisions revs must be strictly increasing and unique")
                if revs != list(range(1, len(revs) + 1)):
                    rep.warn("meta.revisions revs are not contiguous from 1 (fine if intentional)")
            current = next((r for r in revisions if isinstance(r, dict) and r.get("rev") == rev), None)
            recorded_files = (current or {}).get("files")
            if isinstance(recorded_files, dict):
                for key, name in snapshot_files(R, root).items():
                    if key in recorded_files and digest(root / name) != recorded_files[key]:
                        rep.strict_warn(f"{name} changed since rev {rev}; run design.py snapshot")

    a_ids = check_ids(rep, R.get("assumptions", []), "id", r"A\d+", "assumptions")
    d_ids = check_ids(rep, R.get("decisions", []), "id", r"DQ\d+", "decisions")
    c_ids = check_ids(rep, R.get("arch", []), "id", r"c-[a-z0-9-]+", "arch")
    fn_ids = check_ids(rep, R.get("footnotes", []), "n", r"\d+", "footnotes")
    fn_nums = {int(n) for n in fn_ids if str(n).isdigit()}

    for a in R.get("assumptions", []):
        if a.get("s") not in ASSUMPTION_STATUSES:
            rep.err(f"{a.get('id')}: status {a.get('s')!r} not in {sorted(ASSUMPTION_STATUSES)}")
    rounds = R.get("rounds", {})
    for d in R.get("decisions", []):
        did = d.get("id")
        if d.get("s") not in DECISION_STATUSES:
            rep.err(f"{did}: status {d.get('s')!r} not in {sorted(DECISION_STATUSES)}")
        if d.get("by"):
            if d["by"] not in d_ids:
                rep.err(f"{did}: superseded by unknown decision {d['by']}")
            if d.get("s") != "superseded":
                rep.err(f"{did}: has 'by' but status is {d.get('s')!r}, not 'superseded'")
        elif d.get("s") == "superseded":
            rep.err(f"{did}: status 'superseded' but no 'by' pointer")
        if d.get("round") is not None and str(d["round"]) not in rounds:
            rep.warn(f"{did}: round {d['round']} has no entry in registers rounds (fine if it lives only in qa-log)")

    referenced_rounds = {str(d["round"]) for d in R.get("decisions", []) if d.get("round") is not None}
    for k, r in rounds.items():
        if k not in referenced_rounds:
            rep.warn(f"rounds[{k}] is referenced by no decision")
        lint_question(rep, f"rounds[{k}].q", r.get("q") if isinstance(r, dict) else None)

    node_ids = check_diagram(rep, R, root)
    for c in R.get("arch", []):
        for x in c.get("dq", []):
            if x not in d_ids:
                rep.err(f"arch {c.get('id')}: unknown decision {x}")
        for x in c.get("a", []):
            if x not in a_ids:
                rep.err(f"arch {c.get('id')}: unknown assumption {x}")
        node = c.get("node")
        if node_ids is not None:
            if node is None:
                rep.warn(f"arch {c.get('id')} names no diagram node; clicking the diagram cannot open its card")
            elif node not in node_ids:
                rep.err(f"arch {c.get('id')}: node {node!r} is not in the diagram source")
    for c in R.get("constraints", []):
        for x in str(c.get("a", "")).split():
            if x not in a_ids:
                rep.err(f"constraint {c.get('t', '')[:40]!r}: unknown assumption {x}")
    open_ids = check_ids(rep, R.get("open", []), "id", r"[A-Za-z0-9][A-Za-z0-9-]*", "open")
    for f in R.get("findings", []):
        if not 4 <= len(f) <= 5:
            rep.err(f"findings: row {f!r} is not [n, severity, title, ref, links?]")
            continue
        ref = f[3]
        if re.fullmatch(r"DQ\d+", str(ref)) and ref not in d_ids:
            rep.err(f"finding {f[0]}: unknown decision {ref}")
        elif re.fullmatch(r"V\d+", str(ref)) and ref not in open_ids:
            rep.err(f"finding {f[0]}: spike {ref} is not in the open list")
    groups = R.get("openGroups", {})
    for o in R.get("open", []):
        if o.get("g") not in groups:
            rep.err(f"open {o.get('id')}: group {o.get('g')!r} not in openGroups")
    check_links(rep, R)
    banner = meta.get("banner") or {}
    if banner.get("assumption") and banner["assumption"] not in a_ids:
        rep.err(f"meta.banner.assumption {banner['assumption']!r} is not an assumption id")

    used_fns = set()
    for s in walk_strings(R):
        used_fns.update(int(n) for n in FN_TOKEN.findall(s))
    for n in sorted(used_fns - fn_nums):
        rep.err(f"footnote token [^{n}] has no footnotes entry")
    for n in sorted(fn_nums - used_fns):
        rep.warn(f"footnote {n} is never referenced")

    p_ids = check_ids(rep, R.get("paths", []), "id", r"p-[a-z0-9-]+", "paths")
    for p in R.get("paths", []):
        for g in p.get("segs", []):
            if len(g) < 3 or not all(isinstance(v, (int, float)) for v in g[1:3]):
                rep.err(f"path {p.get('id')}: bad seg {g!r} (want [step, p50, p95, description, node?])")
                continue
            if g[2] < g[1]:
                rep.warn(f"path {p.get('id')} seg {g[0]!r}: p95 {g[2]} < p50 {g[1]}")
            if len(g) > 4 and node_ids is not None and g[4] not in node_ids:
                rep.err(f"path {p.get('id')} seg {g[0]!r}: {g[4]!r} is not a node or edge id in the diagram source")
    for row in R.get("ceilings", []):
        if len(row) != 4:
            rep.err(f"ceilings: row {row!r} is not [resource, ceiling, symptom, guard]")
    for m in R.get("scaleMarks", []):
        if not isinstance(m.get("ms"), (int, float)) or m["ms"] <= 0:
            rep.err(f"scaleMarks: bad ms in {m!r}")
    n_ids = check_ids(rep, R.get("numbers", []), "id", r"n-[a-z0-9-]+", "numbers")
    for nt in R.get("numbers", []):
        if not nt.get("t"):
            rep.err(f"numbers {nt.get('id')}: missing title 't'")
        cols = nt.get("cols")
        if not (isinstance(cols, list) and cols and all(isinstance(c, str) for c in cols)):
            rep.err(f"numbers {nt.get('id')}: 'cols' must be a non-empty list of strings")
            continue
        for row in nt.get("rows", []):
            if not (isinstance(row, list) and len(row) == len(cols)):
                rep.err(f"numbers {nt.get('id')}: row {row!r} does not have {len(cols)} cells")

    known = a_ids | d_ids | c_ids | open_ids | n_ids | p_ids
    check_twins(rep, R, root, d_ids, known)

    summary_path = root / "summary.html"
    if not summary_path.exists():
        rep.strict_warn("summary.html is missing; the doc opens without an executive summary")
    else:
        fragment = summary_path.read_text()
        for tag in sorted({t.lower() for t in PAGE_TAG.findall(fragment)}):
            rep.err(f"summary.html contains a <{tag}> tag; it is a body-level fragment, not a page")
        for tag in sorted({t.lower() for t in EMBED_TAG.findall(fragment)}):
            rep.err(f"summary.html contains a <{tag}> tag; the summary carries prose and diagrams, not embedded content")
        for attr in sorted({a.lower() for a in EVENT_ATTR.findall(fragment)}):
            rep.err(f"summary.html carries an inline {attr}= handler; the renderer strips it, so the behaviour belongs outside the fragment")
        if "TODO" in fragment:
            rep.strict_warn("summary.html is still the scaffold skeleton (it carries a TODO); the doc opens without an executive summary")
        for cited in sorted(set(ID_TOKEN.findall(fragment_text(fragment))) - known):
            rep.warn(f"summary.html cites {cited}, which no register defines")
        check_summary_deck(rep, fragment)

    index_path = root / "index.html"
    if index_path.exists() and "GENERATED" not in index_path.read_text():
        rep.strict_warn("index.html carries no GENERATED stamp; it looks copied rather than generated from the current renderer")

    qa_path = root / "qa-log.json"
    if qa_path.exists():
        try:
            Q = json.loads(qa_path.read_text())
        except ValueError as e:
            rep.err(f"qa-log.json does not parse: {e}")
            Q = {}
        qa_rounds = {str(r.get("round")) for r in Q.get("rounds", [])}
        for k in rounds:
            if k not in qa_rounds:
                rep.warn(f"registers rounds[{k}] has no matching round in qa-log.json")
        settled = d_ids | open_ids
        decided_per_round = {}
        for d in R.get("decisions", []):
            if d.get("round") is not None:
                decided_per_round[str(d["round"])] = decided_per_round.get(str(d["round"]), 0) + 1
        for r in Q.get("rounds", []):
            lint_question(rep, f"qa round {r.get('round')} topic", r.get("topic"))
            for q in r.get("questions", []):
                where = f"qa round {r.get('round')} {q.get('header')!r}"
                lint_question(rep, f"{where} header", q.get("header"))
                lint_question(rep, f"{where} question", q.get("question"))
                labels = [o.get("label") for o in q.get("options", [])]
                ans = q.get("answer", "")
                parts = [a.strip() for a in ans.split(",")] if q.get("multiSelect") else [ans]
                if ans and not (ans in labels or all(p in labels for p in parts)):
                    rep.warn(f"{where}: answer is custom text (not an offered label) — fine if intended")
                decides = q.get("decides")
                if decides is not None:
                    if not isinstance(decides, list) or not decides or not all(isinstance(x, str) and x in settled for x in decides):
                        rep.err(f"{where}: decides must be a non-empty list of decision or open-item ids, got {decides!r}")
                elif decided_per_round.get(str(r.get("round")), 0) > 1:
                    named = set(ID_TOKEN.findall(f"{q.get('header') or ''} {q.get('question') or ''}")) & settled
                    if not named:
                        rep.warn(f"{where}: names no decision or open item, and round {r.get('round')} settles {decided_per_round[str(r.get('round'))]} decisions; add a decides field so the replay can map it")
    else:
        rep.warn("qa-log.json not found")

    check_model(rep, R, root, known, node_ids)
    check_ai_config(rep, root)
    check_libs(rep)
    check_components(rep, R, root, known, node_ids)
    return rep.finish()


class DeckLabels(HTMLParser):
    VOID = {"br", "img", "hr", "input", "meta", "link", "source", "area", "col", "embed", "param", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.labels, self.stack, self.buf, self.where, self.depth = [], [], None, None, 0

    def handle_starttag(self, tag, attrs):
        if self.buf is not None and tag == "br":
            self.buf.append("\n")
        if tag in self.VOID:
            return
        classes = classes_of(attrs)
        where = None
        if tag == "b" and any("xs-node" in c for _, c in self.stack):
            where = "summary.html .xs-node b"
        elif tag == "h3" and any("xs-card" in c for _, c in self.stack):
            where = "summary.html .xs-card h3"
        elif "xs-badge" in classes:
            where = "summary.html .xs-badge"
        elif tag == "pre" and "mermaid" in classes:
            where = "summary.html mermaid"
        self.stack.append((tag, classes))
        if where and self.buf is None:
            self.buf, self.where, self.depth = [], where, len(self.stack)

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if self.buf is not None and len(self.stack) == self.depth:
            text = "".join(self.buf)
            if self.where.endswith("mermaid"):
                for nid, label in mermaid_labels(text):
                    self.labels.append((f"summary.html mermaid node {nid}", label))
            else:
                text = " ".join(text.split())
                if text:
                    self.labels.append((self.where, text))
            self.buf, self.where = None, None
        if self.stack:
            self.stack.pop()

    def handle_data(self, data):
        if self.buf is not None:
            self.buf.append(data)


def mermaid_labels(source: str):
    text = re.sub(r"%%.*", "", MERMAID_FRONTMATTER.sub("", source))
    for nid, label in MERMAID_NODE.findall(text):
        if nid in MERMAID_KEYWORDS:
            continue
        first = re.split(r"<br\s*/?>", label)[0].strip()
        if first:
            yield nid, first


def acronym_map(meta: dict) -> dict:
    raw = meta.get("acronyms")
    extra = [a.strip() for a in raw if isinstance(a, str) and a.strip()] if isinstance(raw, list) else []
    named = {a.lower() for a in extra}
    names = list(ACRONYMS) + [p for p in AMBIGUOUS_PRODUCTS if p.lower() in named] + extra
    return {a.lower(): a for a in names}


def check_case(rep, where, label, acronyms, sentence_case=True):
    head = label.strip()
    if sentence_case and head[:1].isalpha() and head[:1].islower() and not IDENTIFIER_HEAD.match(head):
        rep.warn(f"{where}: {first_line(label, 48)!r} opens lower-case; a label is sentence case unless it opens on "
                 "an identifier in backticks")
    for word in LABEL_WORD.findall(re.sub(r"`[^`]*`", " ", label)):
        if any(c in word for c in "_-./"):
            continue
        canon = acronyms.get(word.lower())
        if canon and canon != word:
            rep.warn(f"{where}: {word!r} should read {canon!r}; acronyms and product names keep their own "
                     "capitalisation (name a product that is also an ordinary word in meta.acronyms to lint it)")


def label_sources(R, root):
    diagram = R.get("diagram")
    if isinstance(diagram, dict):
        for key in ("source", "overview"):
            source = diagram.get(key)
            if isinstance(source, str):
                for nid, label in mermaid_labels(source):
                    yield f"diagram.{key} node {nid}", label, True
    for reg in HANDLED:
        for i, e in enumerate(R.get(reg) or []):
            if isinstance(e, dict) and isinstance(e.get("h"), str) and e["h"].strip():
                yield f"{entry_id(reg, i, e)}.h", e["h"], False
    for nt in R.get("numbers") or []:
        if isinstance(nt, dict):
            for j, col in enumerate(nt.get("cols") or []):
                if isinstance(col, str) and col.strip():
                    yield f"numbers {nt.get('id')}: cols[{j}]", col, True
    for reg in ("themes", "openGroups"):
        for key, label in (R.get(reg) or {}).items():
            if isinstance(label, str) and label.strip():
                yield f"{reg}[{key}]", label, True
    fragment = root / "summary.html"
    if fragment.exists():
        parser = DeckLabels()
        parser.feed(fragment.read_text())
        parser.close()
        for where, label in parser.labels:
            yield where, label, True
    yield from component_labels(R)


def cited_ids(R, ids: re.Pattern, prose: str) -> set:
    found = set(ids.findall(prose))

    def walk(node, key):
        if isinstance(node, str):
            if key not in CITE_SKIP:
                found.update(ids.findall(node))
        elif isinstance(node, list):
            for v in node:
                walk(v, key)
        elif isinstance(node, dict):
            for k, v in node.items():
                walk(v, k)

    walk(R, None)
    return found


def handle_range_issue(handle: str):
    n = words(handle)
    if not HANDLE_RANGE[0] <= n <= HANDLE_RANGE[1]:
        return f"is {n} word(s); a handle is {HANDLE_RANGE[0]}–{HANDLE_RANGE[1]} words a reader would say out loud"
    return None


def handle_issues(handle: str, ids: re.Pattern) -> list:
    issues = []
    range_issue = handle_range_issue(handle)
    if range_issue:
        issues.append(range_issue)
    named = sorted(set(ids.findall(handle)))
    if named:
        issues.append("names register ids " + ", ".join(named))
    return issues


def check_handles(rep, R, cited, ids):
    for reg in HANDLED:
        for i, e in enumerate(R.get(reg) or []):
            if not isinstance(e, dict):
                continue
            ident = entry_id(reg, i, e)
            h = e.get("h")
            if h is None:
                if ident in cited:
                    rep.strict_warn(f"{ident} is cited but has no handle h; write the 2–5 word noun phrase a "
                                    "citation shows, or run design.py plainify")
                continue
            if not (isinstance(h, str) and h.strip()):
                rep.err(f"{ident}.h must be a non-empty string")
                continue
            range_issue = handle_range_issue(h)
            if range_issue:
                rep.warn(f"{ident}.h {range_issue}")


def check_titles(rep, R, ids):
    for d in R.get("decisions") or []:
        t = d.get("t") if isinstance(d, dict) else None
        if not (isinstance(t, str) and t.strip()):
            continue
        if t.rstrip().endswith("?"):
            rep.warn(f"{d.get('id')}.t is a question; a decision title is the noun phrase it settled, and the "
                     "question belongs in rounds[].q")
        if words(t) > TITLE_WORDS:
            rep.warn(f"{d.get('id')}.t is {words(t)} words; a decision title is a noun phrase of {TITLE_WORDS} "
                     "words or fewer")
    for reg in ("constraints",) + HANDLED:
        for i, e in enumerate(R.get(reg) or []):
            if not isinstance(e, dict):
                continue
            for field in ("t", "h"):
                value = e.get(field)
                if not isinstance(value, str):
                    continue
                named = sorted(set(ids.findall(value)))
                if named:
                    rep.strict_warn(f"{entry_id(reg, i, e)}.{field} names register ids {', '.join(named)}; a "
                                    "rendered string names an entry by its wording and leaves the id to the citation")


def cap_where(reg: str, i: int, e: dict) -> str:
    if reg == "terms":
        return f"terms[{e.get('k')}]"
    if reg == "footnotes":
        return f"footnotes[{e.get('n')}]"
    return entry_id(reg, i, e)


def cap_words(rep, where, text, cap, what):
    if isinstance(text, str) and text.strip() and words(text) > cap:
        rep.strict_warn(f"{where} is {words(text)} words; {what} caps at {cap}")


def check_caps(rep, R, meta):
    cap_words(rep, "meta.banner.text", (meta.get("banner") or {}).get("text"), BANNER_WORDS, "the banner")
    for reg, field, cap, what in WORD_CAPS:
        for i, e in enumerate(R.get(reg) or []):
            if isinstance(e, dict):
                cap_words(rep, f"{cap_where(reg, i, e)}.{field}", e.get(field), cap, what)
    for i, c in enumerate(R.get("arch") or []):
        if not isinstance(c, dict):
            continue
        where = entry_id("arch", i, c)
        blocks = c.get("b")
        if not isinstance(blocks, list):
            continue
        if len(blocks) > ARCH_BLOCKS:
            rep.strict_warn(f"{where}.b has {len(blocks)} paragraphs; a card carries {ARCH_BLOCKS} or fewer")
        for j, para in enumerate(blocks):
            cap_words(rep, f"{where}.b[{j}]", para, ARCH_BLOCK_WORDS, "a card paragraph")
        if blocks and isinstance(blocks[0], str) and blocks[0].strip():
            cap_words(rep, f"{where}.b[0] first sentence", SENTENCE_END.split(blocks[0].strip())[0],
                      ARCH_LEAD_WORDS, "the sentence the summary row shows")
    terms = R.get("terms") or []
    if len(terms) > TERM_COUNT:
        rep.strict_warn(f"terms lists {len(terms)} entries; the glossary reads at {TERM_COUNT} or fewer")
    rows = R.get("ceilings") or []
    if len(rows) > CEILING_ROWS:
        rep.strict_warn(f"ceilings has {len(rows)} rows; the table reads at {CEILING_ROWS} or fewer")
    for i, row in enumerate(rows):
        if isinstance(row, list):
            for j, cell in enumerate(row):
                cap_words(rep, f"ceilings[{i}][{j}]", cell, CEILING_CELL_WORDS, "a ceilings cell")


def check_terms(rep, R, prose):
    corpus = [prose]
    for reg, entries in R.items():
        if reg != "terms":
            corpus.extend(walk_strings(entries))
    haystack = " ".join(corpus)
    for i, t in enumerate(R.get("terms") or []):
        if not isinstance(t, dict):
            rep.err(f"terms[{i}] is not an object; a term is {{k, v}} with optional aliases")
            continue
        k = t.get("k")
        names = [k] if isinstance(k, str) and k.strip() else []
        aliases = t.get("aliases")
        if aliases is not None:
            if not (isinstance(aliases, list) and aliases and all(isinstance(a, str) and a.strip() for a in aliases)):
                rep.err(f"terms[{k}]: aliases must be a non-empty list of non-empty strings")
            else:
                names += aliases
        if names and not any(re.search(r"(?<![\w-])" + re.escape(n) + r"s?(?![\w-])", haystack, re.I) for n in names):
            rep.warn(f"terms[{k}] is defined but never used outside its definition; drop it or name it in the "
                     "prose (aliases count)")


def check_overview(rep, R, node_ids):
    diagram = R.get("diagram")
    if not isinstance(diagram, dict) or "overview" not in diagram:
        return
    overview = diagram.get("overview")
    if not (isinstance(overview, str) and overview.strip()):
        rep.err("diagram.overview must be a non-empty Mermaid string, or absent")
        return
    body = re.sub(r"%%.*", "", MERMAID_FRONTMATTER.sub("", overview)).strip()
    if not MERMAID_HEADER.match(body):
        rep.err("diagram.overview must open with a flowchart or graph header")
        return
    ids = mermaid_ids(overview)
    if len(ids) > OVERVIEW_NODES:
        rep.strict_warn(f"diagram.overview draws {len(ids)} nodes; the overview card reads at {OVERVIEW_NODES} "
                        "or fewer")
    if node_ids is None:
        rep.err("diagram.overview needs a Mermaid diagram.source to summarise")
        return
    for missing in sorted(ids - node_ids):
        rep.err(f"diagram.overview: {missing!r} is not in diagram.source; the overview draws a subset of the "
                "full graph, so a card and a highlight resolve in both")


def check_ai(rep, meta):
    acronyms = meta.get("acronyms")
    if acronyms is not None and not (isinstance(acronyms, list)
                                     and all(isinstance(a, str) and a.strip() for a in acronyms)):
        rep.err("meta.acronyms must be a list of non-empty strings")
    ai = meta.get("ai")
    if ai is None:
        return
    if not isinstance(ai, dict):
        rep.err("meta.ai must be an object, e.g. {\"suggest\": {\"decisions\": [\"Why 24-hour tokens?\"]}}")
        return
    suggest = ai.get("suggest")
    if suggest is None:
        return
    if not isinstance(suggest, dict):
        rep.err("meta.ai.suggest must map a section id to its list of suggested prompts")
        return
    for sid, prompts in suggest.items():
        if sid not in SECTION_IDS:
            rep.warn(f"meta.ai.suggest[{sid}] is not a section id ({', '.join(SECTION_IDS)}); its prompts render "
                     "under no heading")
        if not (isinstance(prompts, list) and prompts
                and all(isinstance(p, str) and p.strip() for p in prompts)):
            rep.err(f"meta.ai.suggest[{sid}] must be a non-empty list of prompt strings")


def check_model(rep, R, root, known, node_ids):
    meta = R.get("meta") or {}
    fragment = root / "summary.html"
    prose = fragment_text(fragment.read_text()) if fragment.exists() else ""
    ids = id_matcher(known)
    check_handles(rep, R, cited_ids(R, ids, prose), ids)
    check_titles(rep, R, ids)
    check_caps(rep, R, meta)
    check_terms(rep, R, prose)
    check_overview(rep, R, node_ids)
    check_ai(rep, meta)
    acronyms = acronym_map(meta)
    for where, label, sentence_case in label_sources(R, root):
        check_case(rep, where, label, acronyms, sentence_case)


def handle_todo(R, only):
    todo = []
    for reg in HANDLED:
        for i, e in enumerate(R.get(reg) or []):
            if not isinstance(e, dict) or e.get("h") or not isinstance(e.get("t"), str):
                continue
            ident = entry_id(reg, i, e)
            if only is None or ident in only:
                todo.append((reg, i, e, ident))
    return todo


def with_handle(e: dict, handle: str) -> dict:
    out = {}
    for k, v in e.items():
        out[k] = v
        if k == "t":
            out["h"] = handle
    out.setdefault("h", handle)
    return out


def draft_handles(args, R):
    only = {s.strip() for s in args.only.split(",") if s.strip()} if args.only else None
    todo = handle_todo(R, only)
    if not todo:
        print("plainify: every entry that can carry a handle h has one")
        return 0, []
    if args.provider == "none":
        print(f"plainify: {len(todo)} entr{'y' if len(todo) == 1 else 'ies'} need a handle h: "
              + ", ".join(ident for _, _, _, ident in todo))
        return 0, []
    graded = {}
    if args.provider == "slop-cop":
        binary = slop_cop_binary()
        if binary is None:
            return 0, []
        batch = [{"id": ident, "text": e["t"]} for _, _, e, ident in todo]
        try:
            graded = ask_plain_batch(binary, register_titles(R), batch, HANDLE_RANGE[1])
        except (OSError, ValueError, subprocess.SubprocessError) as err:
            detail = err.stderr.strip() if isinstance(err, subprocess.CalledProcessError) and err.stderr else err
            print(f"plainify: slop-cop failed on the handles: {detail}", file=sys.stderr)
            return 0, []
    prompt = (REFERENCE / "handle.md").read_text().strip()
    rows, written = [], 0
    ids = id_matcher(register_ids(R))
    for reg, i, e, ident in todo:
        if args.provider == "slop-cop":
            handle, issues = graded[ident]["plain"].strip(), graded_issues(graded[ident])
        else:
            try:
                handle, issues = ask_plain(args.provider, prompt, reg, e, e["t"]), []
            except (OSError, subprocess.SubprocessError) as err:
                print(f"plainify: {args.provider} failed on {ident}: {err}", file=sys.stderr)
                return written, rows
        handle = handle.strip().rstrip(".")
        issues += handle_issues(handle, ids)
        rows.append((ident, first_line(e["t"]), handle, issues))
        if handle and not args.dry_run:
            R[reg][i] = with_handle(e, handle)
            written += 1
    print_review_table(rows)
    return written, rows


def candidate_terms(text: str) -> set:
    body = FILE_PATH.sub(" ", re.sub(r"`[^`]*`", " ", text))
    found = {w for w in GLOSS_ACRONYM.findall(body) if not ID_TOKEN.fullmatch(w)}
    for phrase in GLOSS_PHRASE.findall(body):
        parts = phrase.split()
        if parts[0].lower() in GLOSS_STOP:
            parts = parts[1:]
        if len(parts) > 1:
            found.add(" ".join(parts))
    return found


def glossary_units(R, root):
    for reg, entries in R.items():
        if reg in GLOSS_SKIP:
            continue
        if isinstance(entries, list):
            for i, e in enumerate(entries):
                where = entry_id(reg, i, e) if isinstance(e, dict) else f"{reg}[{i}]"
                yield where, " ".join(walk_strings(e))
        elif isinstance(entries, dict):
            for k, v in entries.items():
                if k not in GLOSS_SKIP:
                    yield f"{reg}[{k}]", " ".join(walk_strings(v))
    fragment = root / "summary.html"
    if fragment.exists():
        yield "summary.html", fragment_text(fragment.read_text())


def glossary(args) -> int:
    root = Path(args.dir)
    R = load_registers(root, "glossary")
    if R is None:
        return 1
    defined = {n.lower() for t in R.get("terms") or [] if isinstance(t, dict)
               for n in [t.get("k")] + list(t.get("aliases") or []) if isinstance(n, str)}
    seen = {}
    for where, text in glossary_units(R, root):
        for candidate in candidate_terms(text):
            if candidate.lower() not in defined:
                seen.setdefault(candidate, set()).add(where)
    rows = sorted(((c, w) for c, w in seen.items() if len(w) >= GLOSS_MIN_ENTRIES),
                  key=lambda row: (-len(row[1]), row[0].lower()))[:GLOSS_MAX]
    print(json.dumps([{"k": c, "v": ""} for c, _ in rows], indent=2, ensure_ascii=False))
    for c, where in rows:
        print(f"glossary: {c} — {len(where)} entries ({', '.join(sorted(where)[:4])})", file=sys.stderr)
    if not rows:
        print(f"glossary: no term recurs across {GLOSS_MIN_ENTRIES} entries outside terms[]", file=sys.stderr)
    return 0


class ExprError(ValueError):
    pass


def expr_tokens(src: str) -> list:
    out, at = [], 0
    for m in EXPR_TOKEN.finditer(src):
        if m.start() != at:
            raise ExprError(f"unexpected {src[at]!r}")
        at = m.end()
        if not m.group().isspace():
            out.append(m.group())
    if at != len(src):
        raise ExprError(f"unexpected {src[at]!r}")
    return out


def parse_expr(src):
    t = expr_tokens(str(src))
    pos = [0]

    def peek():
        return t[pos[0]] if pos[0] < len(t) else None

    def eat(x):
        if peek() != x:
            raise ExprError(f"expected {x!r}")
        pos[0] += 1

    def expression():
        node = term()
        while peek() in ("+", "-"):
            op = t[pos[0]]
            pos[0] += 1
            node = (op, node, term())
        return node

    def term():
        node = unary()
        while peek() in ("*", "/"):
            op = t[pos[0]]
            pos[0] += 1
            node = (op, node, unary())
        return node

    def unary():
        if peek() in ("+", "-"):
            op = t[pos[0]]
            pos[0] += 1
            return (op, ("num", 0.0), unary())
        return primary()

    def primary():
        x = peek()
        if x is None:
            raise ExprError("the expression ends early")
        if x == "(":
            pos[0] += 1
            node = expression()
            eat(")")
            return node
        if x[0].isdigit() or x[0] == ".":
            pos[0] += 1
            return ("num", float(x))
        if x[0].isalpha() or x[0] == "_":
            pos[0] += 1
            if peek() != "(":
                return ("ref", x)
            pos[0] += 1
            args = [expression()]
            while peek() == ",":
                pos[0] += 1
                args.append(expression())
            eat(")")
            if x not in EXPR_FNS:
                raise ExprError(f"unknown function {x}(); the grammar has {', '.join(sorted(EXPR_FNS))}")
            if len(args) < 2:
                raise ExprError(f"{x}() takes two or more arguments")
            return ("fn", x, args)
        raise ExprError(f"unexpected {x!r}")

    node = expression()
    if pos[0] != len(t):
        raise ExprError(f"unexpected {t[pos[0]]!r}")
    return node


def expr_refs(node, out: set) -> set:
    kind = node[0]
    if kind == "ref":
        out.add(node[1])
    elif kind == "fn":
        for arg in node[2]:
            expr_refs(arg, out)
    elif kind != "num":
        expr_refs(node[1], out)
        expr_refs(node[2], out)
    return out


def eval_expr(node, values):
    kind = node[0]
    if kind == "num":
        return node[1]
    if kind == "ref":
        return values[node[1]]
    if kind == "fn":
        return EXPR_FNS[node[1]](*(eval_expr(a, values) for a in node[2]))
    a, b = eval_expr(node[1], values), eval_expr(node[2], values)
    if kind == "+":
        return a + b
    if kind == "-":
        return a - b
    if kind == "*":
        return a * b
    if b == 0:
        raise ExprError("divides by zero")
    return a / b


def finite(v) -> bool:
    return v == v and abs(v) != float("inf")


def schema_errors(value, schema, where: str) -> list:
    if "const" in schema:
        return [] if value == schema["const"] else [f"{where} must be {schema['const']!r}"]
    if "enum" in schema and value not in schema["enum"]:
        return [f"{where} must be one of {', '.join(map(str, schema['enum']))}"]
    kind = schema.get("type")
    want = SCHEMA_TYPES.get(kind)
    if want and (not isinstance(value, want) or (kind != "boolean" and isinstance(value, bool))):
        return [f"{where} must be {'an' if kind[0] in 'aoi' else 'a'} {kind}"]
    out = []
    if kind == "object":
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                out.append(f"{where} is missing {key!r}")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in props:
                    out.append(f"{where} has an unknown property {key!r}")
        for key, sub in props.items():
            if key in value:
                out.extend(schema_errors(value[key], sub, f"{where}.{key}"))
    elif kind == "array":
        if "minItems" in schema and len(value) < schema["minItems"]:
            out.append(f"{where} needs at least {schema['minItems']} entries")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            out.append(f"{where} takes at most {schema['maxItems']} entries")
        if "items" in schema:
            for i, item in enumerate(value):
                out.extend(schema_errors(item, schema["items"], f"{where}[{i}]"))
    elif kind == "string":
        if len(value.strip()) < schema.get("minLength", 0):
            out.append(f"{where} must not be empty")
        if "pattern" in schema and not re.fullmatch(schema["pattern"].strip("^$"), value):
            out.append(f"{where} must match {schema['pattern']}")
    elif kind in ("number", "integer"):
        if "minimum" in schema and value < schema["minimum"]:
            out.append(f"{where} must be at least {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            out.append(f"{where} must be at most {schema['maximum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            out.append(f"{where} must be greater than {schema['exclusiveMinimum']}")
    return out


class ComponentHosts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hosts, self.open, self.panel, self.topic = [], [], None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = classes_of(attrs)
        if tag == "section" and "xs-panel" in classes:
            self.panel = {"kind": a.get("data-kind") or "", "topics": []}
        if "xs-topic" in classes:
            self.topic = {"depth": 0, "text": []}
        elif self.topic is not None and tag not in VOID_TAGS:
            self.topic["depth"] += 1
        name = a.get("data-component")
        if name:
            self.open.append({"id": name, "tag": tag, "depth": 0, "figure": False, "panel": self.panel})
        elif self.open and tag not in VOID_TAGS:
            self.open[-1]["depth"] += 1
        if self.open and tag == "figure":
            self.open[-1]["figure"] = True

    def handle_endtag(self, tag):
        if self.topic is not None:
            if self.topic["depth"]:
                self.topic["depth"] -= 1
            else:
                if self.panel is not None:
                    self.panel["topics"].append(topic_key("".join(self.topic["text"])))
                self.topic = None
        if not self.open:
            return
        host = self.open[-1]
        if host["depth"]:
            host["depth"] -= 1
        elif tag == host["tag"]:
            self.hosts.append(self.open.pop())

    def handle_data(self, data):
        if self.topic is not None:
            self.topic["text"].append(data)

    def close(self):
        super().close()
        while self.open:
            self.hosts.append(self.open.pop())


def component_schemas(rep) -> dict:
    out = {}
    for path in sorted(COMPONENT_SCHEMAS.glob("dd.*.json")):
        try:
            out[path.stem] = json.loads(path.read_text())
        except ValueError as e:
            rep.err(f"reference/components/{path.name} does not parse: {e}")
    return out


def check_whatif(rep, label, spec, known):
    ids = [i["id"] for i in spec["inputs"]]
    for i in spec["inputs"]:
        if ids.count(i["id"]) > 1:
            rep.err(f"{label}: two inputs share the id {i['id']!r}")
        if i["min"] >= i["max"]:
            rep.err(f"{label}: input {i['id']!r} has min {i['min']} at or above max {i['max']}")
        elif not i["min"] <= i["value"] <= i["max"]:
            rep.err(f"{label}: input {i['id']!r} starts at {i['value']}, outside {i['min']}–{i['max']}")
    grid = whatif_grid(spec["inputs"])
    box = {i["id"]: (min(i["min"], i["value"]), max(i["max"], i["value"])) for i in spec["inputs"]}
    for out in spec["outputs"]:
        where = f"{label}: output {out['label']!r}"
        try:
            node = parse_expr(out["expr"])
        except ExprError as e:
            rep.err(f"{where} does not parse ({e}); the grammar is + - * / ( ) min max over numbers and input ids")
            continue
        unknown = sorted(expr_refs(node, set()) - set(ids))
        if unknown:
            rep.err(f"{where} names {', '.join(unknown)}, which is not an input of this component")
            continue
        if grid is None:
            try:
                lo, hi = expr_interval(node, box)
            except ExprError as e:
                rep.err(f"{where} {e}")
                continue
            if not (finite(lo) and finite(hi)):
                rep.err(f"{where} is not a number across the slider range")
            continue
        for sample in grid:
            try:
                value = eval_expr(node, sample)
            except ExprError as e:
                rep.err(f"{where} {e} at " + ", ".join(f"{k}={v}" for k, v in sorted(sample.items())))
                break
            if not finite(value):
                rep.err(f"{where} is not a number at " + ", ".join(f"{k}={v}" for k, v in sorted(sample.items())))
                break
    for cited in spec.get("cites", []):
        if cited not in known:
            rep.err(f"{label}: cites {cited}, which no register defines")


def topic_key(text: str) -> str:
    return " ".join(text.split()).lower()


def check_flow(rep, label, spec, topics):
    ids = [c["id"] for c in spec["columns"]]
    for cid in sorted({cid for cid in ids if ids.count(cid) > 1}):
        rep.err(f"{label}: two columns share the id {cid!r}")
    per_side = {}
    for i, call in enumerate(spec["callouts"]):
        if call["col"] not in ids:
            rep.err(f"{label}.callouts[{i}]: col {call['col']!r} is not a column id ({', '.join(ids)})")
            continue
        side = call.get("side") or ("above" if per_side.get((call["col"], "above"), 0) < 3 else "below")
        per_side[(call["col"], side)] = per_side.get((call["col"], side), 0) + 1
        topic = call.get("topic")
        if topic and topics is not None and topic_key(topic) not in topics:
            rep.err(f"{label}.callouts[{i}]: topic {topic!r} matches no .xs-topic in the compare panel that hosts it")
    for (col, side), n in sorted(per_side.items()):
        if n > 4:
            rep.warn(f"{label}: column {col!r} stacks {n} callouts {side}; more than four on a side crowds the figure")


def check_component_props(rep, label, spec, known, node_ids, topics=None):
    kind = spec["kind"]
    if kind == "dd.flow":
        check_flow(rep, label, spec, topics)
    elif kind == "dd.lanes":
        ids = [lane["id"] for lane in spec["lanes"]]
        if ids[0] == ids[1]:
            rep.err(f"{label}: both lanes carry the id {ids[0]!r}")
    elif kind == "dd.whatif":
        check_whatif(rep, label, spec, known)
    elif kind == "dd.tabs":
        for i, tab in enumerate(spec["tabs"]):
            if not (tab.get("md") or tab.get("figure")):
                rep.err(f"{label}.tabs[{i}] carries neither 'md' nor 'figure'; a pane shows one or both")
    elif kind == "dd.steps":
        for i, step in enumerate(spec["steps"]):
            target = step.get("target")
            if target is None or node_ids is None:
                continue
            ends = target.split(">") if ">" in target else [target]
            missing = [e for e in ends if e not in node_ids]
            if missing:
                rep.err(f"{label}.steps[{i}]: target {target!r} names {', '.join(missing)}, which is not in the diagram source")
    elif kind == "dd.timeline":
        for i, phase in enumerate(spec["phases"]):
            gate = phase.get("gate")
            if gate and gate not in known:
                rep.err(f"{label}.phases[{i}]: gate {gate}, which no register defines")
    elif kind == "dd.matrix":
        cols, cells = spec["cols"], spec["cells"]
        if len(cells) != len(spec["rows"]):
            rep.err(f"{label}: {len(cells)} cell rows for {len(spec['rows'])} rows")
        for i, row in enumerate(cells):
            if len(row) != len(cols):
                rep.err(f"{label}.cells[{i}] has {len(row)} cells for {len(cols)} columns")
        labels = [c["label"] for c in cols]
        if spec.get("pick") and spec["pick"] not in labels:
            rep.err(f"{label}: pick {spec['pick']!r} is not one of {', '.join(labels)}")


def check_components(rep, R, root, known, node_ids):
    declared = R.get("components")
    if declared is not None and not isinstance(declared, dict):
        rep.err("components must map a component id to its declaration")
        declared = None
    declared = declared or {}
    sources = {p.stem: p.read_text() for p in sorted((root / "components").glob("*.tsx"))}
    summary_path = root / "summary.html"
    hosts = component_hosts(summary_path.read_text()) if summary_path.exists() else []
    fields = [(f"arch {c.get('id')}", c.get("component")) for c in R.get("arch", [])]
    fields += [(f"numbers {n.get('id')}", n.get("component")) for n in R.get("numbers", [])]
    fields.append(("meta.ceilingsComponent", R.get("meta", {}).get("ceilingsComponent")))
    fields = [(where, name) for where, name in fields if name]
    if not (declared or sources or hosts or fields):
        return

    exports = set()
    for stem, text in sources.items():
        code = code_only(text)
        exports |= tsx_exports(stem, code)
        for pattern, what in TSX_BANNED:
            if pattern.search(code):
                rep.err(f"components/{stem}.tsx {what}; an author component draws the registers it is handed and reaches nothing else")
    if sources and not (root / "components.js").exists():
        rep.strict_warn(f"components/ holds {len(sources)} .tsx component(s) but components.js is missing; run design.py build")

    schemas = component_schemas(rep)
    compare_topics = {host["id"]: host["panel"]["topics"] for host in hosts
                      if host["panel"] and host["panel"]["kind"] == "compare"}
    for cid, spec in sorted(declared.items()):
        label = f"components.{cid}"
        if not COMPONENT_ID.fullmatch(cid):
            rep.err(f"{label}: a component id is lower-case words joined by hyphens")
        if not isinstance(spec, dict):
            rep.err(f"{label} must be an object with a 'kind'")
            continue
        schema = schemas.get(spec.get("kind"))
        if schema is None:
            rep.err(f"{label}: kind {spec.get('kind')!r} is not in the kit ({', '.join(sorted(schemas))})")
            continue
        problems = schema_errors(spec, schema, label)
        for msg in problems:
            rep.err(msg)
        if not problems:
            check_component_props(rep, label, spec, known, node_ids, compare_topics.get(cid))
            check_figures(rep, label, spec)

    referenced = set()
    for where, name in fields:
        if name in declared:
            referenced.add(name)
        else:
            rep.err(f"{where}: component {name!r} is not an entry in components")
    for host in hosts:
        if host["id"] in declared:
            referenced.add(host["id"])
        elif host["id"] in exports:
            if not host["figure"]:
                rep.err(f"summary.html hosts the author component {host['id']!r} with no <figure> fallback; print and Markdown render the fallback")
        elif host["id"] in sources:
            rep.err(f"summary.html hosts {host['id']!r} and components/{host['id']}.tsx exists, but that file has no default export, so the bundle registers nothing under its name")
        else:
            rep.err(f"summary.html hosts {host['id']!r}, which is neither an entry in components nor an export of components/*.tsx")
    for cid in sorted(set(declared) - referenced):
        rep.warn(f"components.{cid} is declared but no register field or summary.html host renders it")


def component_hosts(fragment: str) -> list:
    parser = ComponentHosts()
    parser.feed(fragment)
    parser.close()
    return parser.hosts


def component_pack() -> Path:
    COMPONENT_PACK.mkdir(parents=True, exist_ok=True)
    for name in ("package.json", "package-lock.json"):
        source, installed = SCRIPTS / name, COMPONENT_PACK / name
        if not installed.exists() or installed.read_bytes() != source.read_bytes():
            shutil.copy(source, installed)
            shutil.rmtree(COMPONENT_PACK / "node_modules", ignore_errors=True)
    if not (COMPONENT_PACK / "node_modules" / "vite").exists():
        print(f"build: installing the component pack in {COMPONENT_PACK}", flush=True)
        subprocess.run(["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
                       cwd=COMPONENT_PACK, check=True)
    return COMPONENT_PACK


def entry_source(sources) -> str:
    lines = ['import { h, render } from "preact";']
    for i, path in enumerate(sources):
        lines.append(f"import * as M{i} from {json.dumps('./src/' + path.name)};")
    pairs = ", ".join(f"[{json.dumps(p.stem)}, M{i}]" for i, p in enumerate(sources))
    lines += [
        f"const MODS = [{pairs}];",
        "const REG = {};",
        "for (const [stem, mod] of MODS) {",
        '  for (const [name, value] of Object.entries(mod)) if (name !== "default" && typeof value === "function") REG[name] = value;',
        '  if (typeof mod.default === "function") REG[stem] = mod.default;',
        "}",
        "export const names = Object.keys(REG);",
        "export function mount(host, name, props) {",
        "  const Component = REG[name];",
        "  if (!Component) return false;",
        "  render(h(Component, props), host);",
        "  return true;",
        "}",
        "",
    ]
    return "\n".join(lines)


def vite_config(work: Path, out: Path) -> str:
    return (
        'import { defineConfig } from "vite";\n'
        "export default defineConfig({\n"
        f"  root: {json.dumps(str(work))},\n"
        '  logLevel: "warn",\n'
        '  esbuild: { jsx: "automatic", jsxImportSource: "preact" },\n'
        "  build: {\n"
        f"    outDir: {json.dumps(str(out))},\n"
        "    emptyOutDir: false,\n"
        '    target: "es2022",\n'
        f'    lib: {{ entry: {json.dumps(str(work / "entry.js"))}, formats: ["es"], fileName: () => "components.js" }}\n'
        "  }\n"
        "});\n"
    )


def build(args) -> int:
    root = Path(args.dir)
    sources = sorted((root / "components").glob("*.tsx"))
    if not sources:
        print(f"build: {root}/components holds no .tsx component; the declared kit needs no build")
        return 0
    if shutil.which("npm") is None:
        print("build: npm is not on PATH; compiling components/*.tsx needs node", file=sys.stderr)
        return 1
    pack = component_pack()
    work = pack / "build"
    shutil.rmtree(work, ignore_errors=True)
    (work / "src").mkdir(parents=True)
    for path in sources:
        shutil.copy(path, work / "src" / path.name)
    (work / "entry.js").write_text(entry_source(sources))
    (work / "vite.config.mjs").write_text(vite_config(work, root.resolve()))
    result = subprocess.run(["node", str(pack / "node_modules" / "vite" / "bin" / "vite.js"),
                             "build", "--config", str(work / "vite.config.mjs")], cwd=pack)
    if result.returncode:
        print("build: vite could not compile the components", file=sys.stderr)
        return result.returncode
    bundle = root / "components.js"
    print(f"build: {', '.join(p.name for p in sources)} → {bundle} ({bundle.stat().st_size // 1024} KB)")
    return 0


def check_ai_config(rep, root):
    path = root / "ai.json"
    if not path.exists():
        return
    try:
        cfg = json.loads(path.read_text())
    except ValueError as e:
        rep.err(f"ai.json does not parse: {e}")
        return
    if not isinstance(cfg, dict):
        rep.err('ai.json must be a JSON object: {"endpoint", "model", "key"} for the assistant, {"github": {"token"}} for '
                'link states, or {"disabled": true} to turn the assistant off')
        return
    if "github" in cfg:
        github = cfg["github"]
        if not (isinstance(github, dict) and isinstance(github.get("token"), str) and github["token"].strip()):
            rep.err('ai.json: "github" must be {"token": "<read-only fine-grained PAT>"}')
        else:
            for k in sorted(set(github) - {"token"}):
                rep.warn(f"ai.json carries github.{k}, which the page ignores")
    if cfg.get("disabled") is True:
        for k in sorted(set(cfg) - {"disabled"} - set(SITE_CONFIG_KEYS)):
            rep.warn(f"ai.json disables the assistant, so {k!r} beside it does nothing")
        return
    if not (set(cfg) & set(AI_CONFIG_KEYS)) and not (set(cfg) & set(SITE_CONFIG_KEYS)):
        rep.err("ai.json carries neither the assistant keys (endpoint, model, key) nor a github block")
        return
    if set(cfg) & set(AI_CONFIG_KEYS):
        for k in AI_CONFIG_KEYS:
            v = cfg.get(k)
            if not (isinstance(v, str) and v.strip()):
                rep.err(f"ai.json: {k!r} must be a non-empty string")
        endpoint = cfg.get("endpoint")
        if isinstance(endpoint, str) and endpoint.strip():
            problem = ai_endpoint_problem(endpoint.strip())
            if problem:
                rep.err(f"ai.json: endpoint {problem}")
        reasoning = cfg.get("reasoning")
        if reasoning is not None and reasoning not in AI_REASONING:
            rep.err(f"ai.json: 'reasoning' must be one of {', '.join(AI_REASONING)}")
        elif reasoning in AI_REASONING[:3] and str(cfg.get("model", "")).startswith("gemma"):
            rep.warn(f"ai.json: reasoning {reasoning!r} on a gemma model only switches reasoning on; low, medium and high behave the same")
    for k in sorted(set(cfg) - set(AI_CONFIG_KEYS) - set(AI_CONFIG_OPTIONAL) - set(SITE_CONFIG_KEYS)):
        rep.warn(f"ai.json carries {k!r}, which the page ignores")


def link_rows(R) -> list:
    rows = []
    for o in R.get("open", []):
        links = [normalise_link(l)[0] for l in (o.get("links") or [])]
        rows.append({"id": o.get("id"), "register": "open", "kind": "spike" if o.get("g") == "spikes" else "open item",
                     "title": o.get("t", ""), "s": o.get("s", "open"), "links": [l for l in links if l]})
    for d in R.get("decisions", []):
        links = [normalise_link(l)[0] for l in (d.get("links") or [])]
        rows.append({"id": d.get("id"), "register": "decisions", "kind": "decision", "title": d.get("t", ""),
                     "s": d.get("s", ""), "links": [l for l in links if l]})
    return rows


def github_get(path: str):
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req = urllib.request.Request(GITHUB_API + path, headers={
            "Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
    if not shutil.which("gh"):
        raise RuntimeError("set GITHUB_TOKEN or install the gh CLI to resolve GitHub links")
    p = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    if p.returncode:
        if "404" in p.stderr or "Not Found" in p.stderr:
            return None
        raise RuntimeError(p.stderr.strip() or f"gh api {path} failed")
    return json.loads(p.stdout)


def github_state(gh: dict):
    base = f"/repos/{gh['owner']}/{gh['repo']}"
    if gh["kind"] == "commit":
        c = github_get(f"{base}/commits/{gh['sha']}")
        if not c:
            return {"state": "unknown"}
        return {"state": "commit", "title": c["commit"]["message"].split("\n", 1)[0],
                "author": (c.get("author") or {}).get("login") or c["commit"]["author"]["name"],
                "date": c["commit"]["author"]["date"][:10]}
    if gh["kind"] == "pr":
        pr = github_get(f"{base}/pulls/{gh['n']}")
        if not pr:
            return {"state": "unknown"}
        state = "merged" if pr.get("merged_at") else "draft" if pr.get("draft") else pr["state"]
        return {"state": state, "title": pr["title"], "author": pr["user"]["login"],
                "date": (pr.get("merged_at") or pr.get("closed_at") or pr["updated_at"])[:10]}
    issue = github_get(f"{base}/issues/{gh['n']}")
    if not issue:
        return {"state": "unknown"}
    return {"state": issue["state"], "title": issue["title"], "author": issue["user"]["login"],
            "date": (issue.get("closed_at") or issue["updated_at"])[:10]}


def link_line(link: dict, repo, state) -> str:
    gh = link.get("gh")
    if gh:
        prefix = "" if repo == f"{gh['owner']}/{gh['repo']}" else f"{gh['owner']}/{gh['repo']}"
        label = link.get("label") or (prefix + ("@" + gh["sha"][:7] if gh["kind"] == "commit" else f"#{gh['n']}"))
    else:
        label = link.get("label") or link["url"]
    bits = [f"{link['kind']} {label}"]
    if state:
        bits.append(state["state"] + (f" {state['date']}" if state.get("date") else "")
                    + (f" by {state['author']}" if state.get("author") else ""))
        if state.get("title"):
            bits.append(f"\"{state['title']}\"")
    if link["closes"]:
        bits.append("closes")
    return " · ".join(bits)


def link_drift(row: dict, states: dict) -> str:
    closers = [(l, states.get(l["url"])) for l in row["links"] if l["closes"]]
    if not closers:
        return ""
    ref = lambda l: f"{l['kind']} {l['gh']['key']}"
    landed = [(l, st) for l, st in closers if st and st["state"] in GITHUB_STATE_CLOSED]
    if row["s"] != "closed" and landed:
        l, st = landed[0]
        return f"{row['id']} is still open but {ref(l)} was {st['state']} on {st['date']}; set s: \"closed\""
    if row["s"] == "closed" and all(st and st["state"] not in GITHUB_STATE_CLOSED and st["state"] != "unknown" for _, st in closers):
        l, st = closers[0]
        return f"{row['id']} is marked closed but {ref(l)} is still {st['state']}"
    return ""


def links(args) -> int:
    root = Path(args.dir)
    R = load_registers(root, "links")
    if R is None:
        return 1
    rep = Report()
    check_links(rep, R)
    for m in rep.errors:
        print(f"ERROR: {m}", file=sys.stderr)
    if rep.errors:
        return 1
    repo = R.get("meta", {}).get("repo")
    rows = link_rows(R)
    states = {}
    if args.fetch:
        for row in rows:
            for link in row["links"]:
                if link.get("gh") and link["url"] not in states:
                    try:
                        states[link["url"]] = github_state(link["gh"])
                    except (RuntimeError, OSError, ValueError, KeyError) as e:
                        print(f"links: cannot resolve {link['url']}: {e}", file=sys.stderr)
                        return 1
    if args.missing:
        rows = [r for r in rows if not r["links"]]
    if args.json:
        out = []
        for row in rows:
            out.append({**row, "links": [{**{k: v for k, v in l.items() if k != "gh"}, **({"github": l["gh"]["key"]} if l.get("gh") else {}),
                                          **(states.get(l["url"]) or {})} for l in row["links"]]})
        print(json.dumps(out, indent=1))
        return 0
    width = max([len(str(r["id"])) for r in rows] + [2])
    for row in rows:
        status = f" · {row['s']}" if row["register"] == "open" else ""
        print(f"{str(row['id']).ljust(width)}  {row['kind']}{status}  {row['title'][:60]}")
        if not row["links"]:
            print(f"{' ' * width}    no link")
        for link in row["links"]:
            print(f"{' ' * width}    {link_line(link, repo, states.get(link['url']))}")
    if args.fetch:
        drift = [d for d in (link_drift(r, states) for r in link_rows(R)) if d]
        for d in drift:
            print(f"drift: {d}")
    unlinked = [r["id"] for r in link_rows(R) if not r["links"]]
    if unlinked and not args.missing:
        print(f"{len(unlinked)} entries carry no link: {', '.join(map(str, unlinked))}")
    return 0


def component_figures(spec):
    kind = spec.get("kind")
    if kind == "dd.tabs":
        return [t.get("figure") for t in spec.get("tabs") or [] if isinstance(t, dict)]
    if kind == "dd.before-after":
        return [(spec.get(side) or {}).get("figure") for side in ("before", "after")
                if isinstance(spec.get(side), dict)]
    return []


def component_labels(R):
    declared = R.get("components")
    if not isinstance(declared, dict):
        return
    for cid, spec in sorted(declared.items()):
        if not isinstance(spec, dict):
            continue
        where = f"components.{cid}"
        if isinstance(spec.get("title"), str) and spec["title"].strip():
            yield f"{where}.title", spec["title"], True
        for field in ("tabs", "steps", "phases", "inputs", "outputs", "rows", "cols", "columns", "lanes"):
            for i, item in enumerate(spec.get(field) or []):
                if isinstance(item, dict) and isinstance(item.get("label"), str) and item["label"].strip():
                    yield f"{where}.{field}[{i}].label", item["label"], True
        for i, call in enumerate(spec.get("callouts") or []):
            if isinstance(call, dict) and isinstance(call.get("title"), str) and call["title"].strip():
                yield f"{where}.callouts[{i}].title", call["title"], True
        for i, lane in enumerate(spec.get("lanes") or []):
            for j, box in enumerate((lane.get("boxes") if isinstance(lane, dict) else None) or []):
                if isinstance(box, dict) and isinstance(box.get("title"), str) and box["title"].strip():
                    yield f"{where}.lanes[{i}].boxes[{j}].title", box["title"], True
        for side in ("before", "after"):
            pane = spec.get(side)
            if isinstance(pane, dict) and isinstance(pane.get("label"), str) and pane["label"].strip():
                yield f"{where}.{side}.label", pane["label"], True
        for figure in component_figures(spec):
            if isinstance(figure, dict) and figure.get("kind") == "mermaid" and isinstance(figure.get("source"), str):
                for nid, label in mermaid_labels(figure["source"]):
                    yield f"{where} figure node {nid}", label, True


def code_only(src: str) -> str:
    out, i, n = list(src), 0, len(src)
    stack = [["code", 0]]

    def blank(start, end):
        for k in range(start, min(end, n)):
            if out[k] != "\n":
                out[k] = " "

    while i < n:
        top = stack[-1]
        c = src[i]
        if top[0] == "template":
            if c == "\\":
                blank(i, i + 2)
                i += 2
            elif c == "`":
                stack.pop()
                i += 1
            elif src[i:i + 2] == "${":
                stack.append(["code", 0])
                i += 2
            else:
                blank(i, i + 1)
                i += 1
            continue
        if src[i:i + 2] == "//":
            end = src.find("\n", i)
            end = n if end < 0 else end
            blank(i, end)
            i = end
        elif src[i:i + 2] == "/*":
            end = src.find("*/", i + 2)
            end = n if end < 0 else end + 2
            blank(i, end)
            i = end
        elif c in "\"'":
            j = i + 1
            while j < n and src[j] != c and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            if j < n and src[j] == c:
                blank(i + 1, j)
                i = j + 1
            else:
                i += 1
        elif c == "`":
            stack.append(["template", 0])
            i += 1
        elif c == "{":
            top[1] += 1
            i += 1
        elif c == "}":
            if top[1] == 0 and len(stack) > 1:
                stack.pop()
            else:
                top[1] -= 1
            i += 1
        else:
            i += 1
    return "".join(out)


def tsx_exports(stem: str, code: str) -> set:
    names = set(TSX_EXPORT.findall(code))
    for group in TSX_EXPORT_LIST.findall(code):
        for part in group.split(","):
            m = TSX_EXPORT_NAME.search(part.strip())
            if m:
                names.add(m.group(0))
    if TSX_EXPORT_DEFAULT.search(code):
        names.add(stem)
    return names


def whatif_axis(inp) -> list:
    lo, hi = float(inp["min"]), float(inp["max"])
    step = float(inp.get("step") or 1)
    values = [lo + k * step for k in range(int((hi - lo) / step + 1e-9) + 1)]
    if values[-1] < hi:
        values.append(hi)
    if float(inp["value"]) not in values:
        values.append(float(inp["value"]))
    return values


def whatif_grid(inputs):
    axes, total = [], 1
    for inp in inputs:
        axis = whatif_axis(inp)
        total *= len(axis)
        if total > WHATIF_GRID_MAX:
            return None
        axes.append(axis)
    ids = [i["id"] for i in inputs]
    return [dict(zip(ids, point)) for point in itertools.product(*axes)]


def expr_interval(node, box):
    kind = node[0]
    if kind == "num":
        return (node[1], node[1])
    if kind == "ref":
        return box[node[1]]
    if kind == "fn":
        parts = [expr_interval(a, box) for a in node[2]]
        fn = EXPR_FNS[node[1]]
        return (fn(*(lo for lo, _ in parts)), fn(*(hi for _, hi in parts)))
    a, b = expr_interval(node[1], box), expr_interval(node[2], box)
    if kind == "+":
        return (a[0] + b[0], a[1] + b[1])
    if kind == "-":
        return (a[0] - b[1], a[1] - b[0])
    if kind == "/" and b[0] <= 0 <= b[1]:
        raise ExprError("divides by a denominator the sliders can drive to zero")
    corners = [x * y if kind == "*" else x / y for x in a for y in b]
    return (min(corners), max(corners))


class SvgScan(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.roots, self.depth, self.tags, self.attrs = [], 0, [], []

    def handle_starttag(self, tag, attrs):
        if self.depth == 0:
            self.roots.append(tag)
        if tag not in SVG_TAGS:
            self.tags.append(tag)
        for name, value in attrs:
            if name.startswith("xml"):
                continue
            key = name.split(":")[-1]
            if key == "href":
                if not SVG_FRAGMENT_HREF.match((value or "").strip()):
                    self.attrs.append(f"{name}={value!r}")
            elif key == "style":
                if SVG_CSS_BAN.search(value or ""):
                    self.attrs.append(f"{name}={value!r}")
            elif key not in SVG_ATTRS:
                self.attrs.append(name)
        if tag not in VOID_TAGS:
            self.depth += 1

    def handle_startendtag(self, tag, attrs):
        depth = self.depth
        self.handle_starttag(tag, attrs)
        self.depth = depth

    def handle_endtag(self, tag):
        if self.depth:
            self.depth -= 1


def svg_problems(source: str) -> list:
    scan = SvgScan()
    scan.feed(source)
    scan.close()
    out = []
    if len(scan.roots) != 1 or scan.roots[0] != "svg":
        out.append("is not a single <svg> element; the renderer parses it as XML and drops anything else")
    if scan.tags:
        out.append("carries " + ", ".join(f"<{t}>" for t in sorted(set(scan.tags))) +
                   "; the renderer keeps only drawing elements")
    if scan.attrs:
        out.append("carries " + ", ".join(sorted(set(scan.attrs))) +
                   "; the renderer keeps only drawing attributes and same-document href fragments")
    return out


def check_figures(rep, label, spec):
    for figure in component_figures(spec):
        if not isinstance(figure, dict) or figure.get("kind") != "svg":
            continue
        if not (isinstance(figure.get("label"), str) and figure["label"].strip()):
            rep.err(f"{label}: an svg figure needs a 'label'; it is the figure's aria-label and the only "
                    "wording print and the Markdown export carry")
        source = figure.get("source")
        if isinstance(source, str):
            for problem in svg_problems(source):
                rep.err(f"{label}: svg figure {problem}")


AI_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def ai_endpoint_problem(endpoint: str):
    parts = urllib.parse.urlsplit(endpoint)
    if not parts.scheme or not parts.netloc:
        return "must be an absolute URL; the page calls it with no page-relative base to resolve against"
    if parts.scheme == "http" and (parts.hostname or "") in AI_LOOPBACK:
        return None
    if parts.scheme != "https":
        return (f"uses the {parts.scheme}: scheme; a page served over https can only call an https endpoint "
                "(http is allowed on localhost)")
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("scaffold", help="create a fresh directory for one design doc")
    sc.add_argument("dir", nargs="?", help="destination (default: ./<slug> from --title)")
    sc.add_argument("--title")
    sc.add_argument("--slug")
    sc.add_argument("--example", action="store_true", help="use the tinyq worked example instead of the empty starter")
    sc.set_defaults(fn=scaffold)
    ck = sub.add_parser("check", help="lint the registers in a design project directory")
    ck.add_argument("dir")
    ck.add_argument("--strict", action="store_true", help="treat the warnings a published doc must not carry as errors")
    ck.set_defaults(fn=check)
    st = sub.add_parser("summary-text", help="print summary.html as plain text, for the voice gate")
    st.add_argument("dir")
    st.set_defaults(fn=summary_text)
    pl = sub.add_parser("plainify", help="write a plain-language twin for every rendered entry that lacks one")
    pl.add_argument("dir")
    pl.add_argument("--only", help="comma-separated entry ids to rewrite even when a twin exists, e.g. DQ3,A2,tldr[0]")
    pl.add_argument("--provider", choices=PLAIN_PROVIDERS, default="slop-cop", help="slop-cop runs `slop-cop plainify` over the whole batch and grades what it writes, claude runs the Claude Code CLI, codex runs codex exec, none only lists the entries that need a twin")
    pl.add_argument("--dry-run", action="store_true", help="print the review table without writing registers.json")
    pl.set_defaults(fn=plainify)
    rc = sub.add_parser("render-check", help="render the doc in headless Chrome and fail on a Mermaid error")
    rc.add_argument("dir")
    rc.add_argument("--timeout", type=float, default=RENDER_TIMEOUT, help="seconds to wait for the page to report every diagram rendered")
    rc.set_defaults(fn=render_check)
    gl = sub.add_parser("glossary", help="print the terms a doc leans on but never defines, as JSON for terms[]")
    gl.add_argument("dir")
    gl.set_defaults(fn=glossary)
    pd = sub.add_parser("pdf", help="print the project's doc to its design-doc.pdf")
    pd.add_argument("dir", nargs="?", default=".")
    pd.set_defaults(fn=pdf)
    bd = sub.add_parser("build", help="compile the project's components/*.tsx into components.js")
    bd.add_argument("dir", nargs="?", default=".")
    bd.set_defaults(fn=build)
    sn = sub.add_parser("snapshot", help="record a revision of the project's registers")
    sn.add_argument("dir", nargs="?", default=".")
    sn.add_argument("--note", default="")
    sn.add_argument("--item", action="append", help="reader-facing bullet describing this revision; repeatable")
    sn.add_argument("--force", action="store_true")
    sn.set_defaults(fn=snapshot)
    lk = sub.add_parser("links", help="list the pull requests and issues each open item and decision links")
    lk.add_argument("dir")
    lk.add_argument("--fetch", action="store_true", help="resolve GitHub links through GITHUB_TOKEN or the gh CLI and report drift against s")
    lk.add_argument("--json", action="store_true", help="print the rows as JSON")
    lk.add_argument("--missing", action="store_true", help="print only the entries that carry no link")
    lk.set_defaults(fn=links)
    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
