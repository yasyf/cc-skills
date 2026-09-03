#!/usr/bin/env python3
"""Driver for the design-doc skill.

  design.py scaffold [dir] [--title X] [--slug x] [--example]
  design.py check <dir> [--strict]
  design.py summary-text <dir>
  design.py plainify <dir> [--only DQ3,A2] [--provider slop-cop|claude|codex|none] [--dry-run]
  design.py render-check <dir> [--timeout S]
  design.py pdf [dir]
  design.py snapshot [dir] [--note X] [--item …] [--force]

scaffold creates a fresh directory for one design doc — named after the
slug when no dir is given — holding the doc renderer, the executive-summary
fragment, and either the empty starter registers or the tinyq worked
example. check lints the registers: ID shapes and uniqueness, dangling
cross-references, supersession integrity, footnote tokens, the qa-log round
linkage, the Mermaid diagram's structure, the plain twins, keys and themes,
and the summary deck; errors exit non-zero, warnings are advisory, and
--strict promotes the warnings a published doc must not carry into errors.
summary-text prints summary.html as plain text, one "## <kind>" section per
panel, the input for the voice gate. plainify writes a plain-language twin
for every rendered entry that lacks one, through `slop-cop plainify` by
default or the claude or codex CLI, and prints a review table carrying
whatever slop-cop grades against. render-check opens the doc in headless Chrome
over its debugging pipe, waits for the page to report every diagram
rendered, and fails on a Mermaid parse error or a diagram that never
rendered, printing Chrome's stderr and the page's console so a failure
names its cause. pdf
prints the served doc to its design-doc.pdf through the template's print
stylesheet. snapshot records a revision of the registers in the project's
history directory. Stdlib only.
"""
import argparse, copy, datetime, hashlib, importlib.util, json, os, re, shutil, subprocess, sys, tempfile, urllib.request
from html.parser import HTMLParser
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
TEMPLATES = SKILL / "templates"
REFERENCE = SKILL / "reference"
ICON_CACHE = Path.home() / ".cache" / "design-doc"
ICON_LIST = "https://data.jsdelivr.com/v1/package/npm/lucide-static@{version}/flat"
PROJECT_FILES = ("registers.json", "qa-log.json", "NOTES.md", "summary.html")

DECISION_STATUSES = {"resolved", "superseded", "open"}
ASSUMPTION_STATUSES = {"working", "validate"}
PANEL_BUDGET = {"thesis": 40, "compare": 120, "numbers": 60, "cost": 90, "poster": 180}
DECK_BUDGET = 350
DECK_SIZE = (3, 5)
TWIN_WORDS = 30
TLDR_WORDS = 20
KEY_RANGE = (3, 8)
TWINNED = (("tldr", "md"), ("constraints", "t"), ("decisions", "r"), ("assumptions", "b"), ("open", "t"))
IDENTIFIED = ("assumptions", "decisions", "arch", "open", "numbers", "paths")
TWIN_KIND = {"tldr": "summary bullet", "constraints": "ground rule", "decisions": "decision",
             "assumptions": "assumption", "open": "open question"}
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
MERMAID_EDGE_TEXT = re.compile(r"(?:--|-\.|==)\s[^\n]*?\s(?:-->|\.->|==>|---|-\.-|===)")
MERMAID_ARROW = re.compile(r"[<>]?[-=.]{2,}[>xo]?|[<>]?[-=.]+>")
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
        if tag in self.MUTED or (tag == "pre" and "mermaid" in classes):
            self.muted.append(tag)
        if self.muted:
            return
        if tag == "section":
            if self.panel is None and "xs-panel" in classes:
                self.panel = {"kind": a.get("data-kind") or "", "text": [], "figures": []}
                self.panels.append(self.panel)
                self.sections = 0
            elif self.panel is not None:
                self.sections += 1
        elif tag == "figure" and self.panel is not None:
            self.figure = {"labelled": bool((a.get("aria-label") or "").strip())}
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
        text = re.sub(r"\[[^\[\]]*\]|\([^()]*\)|\{[^{}]*\}|>[^\]\n]*\]", " ", text)
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


def ask_plain_batch(binary: str, titles: dict, batch: list) -> dict:
    with tempfile.TemporaryDirectory() as scratch:
        glossary = Path(scratch) / "glossary.json"
        glossary.write_text(json.dumps(titles, ensure_ascii=False))
        cmd = [binary, "plainify", "-", "--json", "--max-words", str(TWIN_WORDS),
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
    if written:
        (root / "registers.json").write_text(json.dumps(R, indent=2, ensure_ascii=False) + "\n")
        print(f"plainify: wrote {written} twin(s); read each against its original before snapshot")
    flagged = sum(1 for _, _, twin, issues in rows if twin and issues)
    if flagged:
        print(f"plainify: {flagged} twin(s) carry issues in the table above; rewrite each before snapshot")
    return 0


class RenderedDom(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rendered, self.failed, self.syntax_error, self.rendered_ids, self.muted = 0, [], False, set(), []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
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
        if len(f) != 4:
            rep.err(f"findings: row {f!r} is not [n, severity, title, ref]")
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

    known = a_ids | d_ids | c_ids | open_ids | n_ids | {p.get("id") for p in R.get("paths", [])}
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
        for r in Q.get("rounds", []):
            lint_question(rep, f"qa round {r.get('round')} topic", r.get("topic"))
            for q in r.get("questions", []):
                lint_question(rep, f"qa round {r.get('round')} {q.get('header')!r} header", q.get("header"))
                lint_question(rep, f"qa round {r.get('round')} {q.get('header')!r} question", q.get("question"))
                labels = [o.get("label") for o in q.get("options", [])]
                ans = q.get("answer", "")
                parts = [a.strip() for a in ans.split(",")] if q.get("multiSelect") else [ans]
                if ans and not (ans in labels or all(p in labels for p in parts)):
                    rep.warn(f"qa round {r.get('round')} {q.get('header')!r}: answer is custom text (not an offered label) — fine if intended")
    else:
        rep.warn("qa-log.json not found")

    check_libs(rep)
    return rep.finish()


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
    pd = sub.add_parser("pdf", help="print the project's doc to its design-doc.pdf")
    pd.add_argument("dir", nargs="?", default=".")
    pd.set_defaults(fn=pdf)
    sn = sub.add_parser("snapshot", help="record a revision of the project's registers")
    sn.add_argument("dir", nargs="?", default=".")
    sn.add_argument("--note", default="")
    sn.add_argument("--item", action="append", help="reader-facing bullet describing this revision; repeatable")
    sn.add_argument("--force", action="store_true")
    sn.set_defaults(fn=snapshot)
    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
