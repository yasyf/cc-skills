#!/usr/bin/env python3
"""Driver for the incident-retro skill.

  retro.py scaffold <dir> --title T [--date YYYY-MM-DD] [--incident N] [--slug S] [--example]
  retro.py check <dir> [--strict] [--forbidden-terms REGEX]
  retro.py render-check <dir> [--timeout S]
  retro.py snapshot <dir> [--note X] [--item …] [--force]
  retro.py links <dir> [--fetch] [--missing] [--json]
  retro.py text <dir> [--section ID]
  retro.py pdf <dir>
  retro.py evidence fetch|slack … (scripts/retro_evidence.py)
  retro.py import-gdoc <exported.md> [<docs.json>] --out <dir> [--tz Z] [--date YYYY-MM-DD]
  retro.py live init|sync|finalize <incident-dir> --docs <checkout> (scripts/retro_live.py)

scaffold creates a directory for one retro holding the renderer, retro.json,
NOTES.md and an empty evidence tree, or the Acme worked example. check lints
retro.json: identity and vocabularies, timestamp order and offsets, windows,
the timeline's order and phases, impact metrics, causes, actions, the
evidence register against the snapshot files beside it, citations and
footnotes, handles and plain twins, prose that states a derived number,
capitalisation, declared components, forbidden terms, the library pins, the
template stamp, ai.json, and the revision history; errors exit non-zero,
warnings are advisory, and --strict promotes the warnings a published retro
must not carry. render-check opens the retro in headless Chrome and fails on
a component that never mounted, a notebook cell that never rendered, or a
chart library that never loaded. snapshot records a revision of retro.json
and the evidence digest in history/. links lists the pull requests and
issues every action, cause and resolution carries, resolves their GitHub
state with --fetch, and reports an action whose state disagrees with the
change that closes it. text prints the retro as Markdown in reading order,
the input for the prose gates. pdf prints the served page. live scaffolds,
refreshes and closes a retro while the incident is still running, deriving
every field from the incident skill's state.json and slack-log.jsonl.
Stdlib only.
"""
import argparse, copy, datetime, hashlib, importlib.util, json, os, re, shutil, subprocess, sys, zoneinfo
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ddshared import *

SKILL = Path(__file__).resolve().parent.parent
SCRIPTS = SKILL / "scripts"
TEMPLATES = SKILL / "templates"
REFERENCE = SKILL / "reference"
COMPONENT_SCHEMAS = REFERENCE / "components"
SHARED = SKILL.parents[2] / "_shared"
PAGE = "incident-retro.html"
PDF_NAME = "incident-retro.pdf"
PROJECT_FILES = ("retro.json", "NOTES.md", "summary.html")
SUMMARY_PAGE = "summary.html"
EVIDENCE_DIRS = ("datadog", "slack", "images")
EVIDENCE_TEXT = {".json", ".md", ".txt", ".csv"}
SECTION_IDS = ("overview", "timeline", "causes", "impact", "resolution", "lessons", "recognize", "actions",
               "evidence", "unknowns", "glossary", "notes")
SECTION_TITLES = {"overview": "Overview", "timeline": "Timeline", "causes": "Causes", "impact": "Impact",
                  "resolution": "Detection and response", "lessons": "Lessons",
                  "recognize": "How to recognize this next time", "actions": "Action items",
                  "evidence": "Evidence", "unknowns": "Still unknown", "glossary": "Glossary", "notes": "Notes"}
STATUSES = ("ongoing", "draft", "in-review", "reviewed", "resolved")
STATUS_LABEL = {"ongoing": "Ongoing", "draft": "Draft", "in-review": "Under review", "reviewed": "Reviewed",
                "resolved": "Closed out"}
STATUS_RANK = {s: i for i, s in enumerate(STATUSES)}
PROVISIONAL = ("ongoing", "draft")
WINDOW_KINDS = ("outage", "degraded", "partial")
TIMELINE_KINDS = ("deploy", "alert", "report", "hypothesis", "action", "mitigation", "resolution", "allclear")
CAUSE_KINDS = ("root", "contributing", "trigger")
ACTION_STATES = ("todo", "in-progress", "done", "dropped")
ACTION_LABEL = {"todo": "To do", "in-progress": "In progress", "done": "Done", "dropped": "Dropped"}
ACTION_OPEN = ("todo", "in-progress")
ACTION_SOURCES = ("lessons", "review")
MONITOR_ROLES = ("caught", "missed", "added")
PR_ROLES = ("cause", "fix", "monitor", "followup")
LESSON_COLUMNS = (("well", "What went well"), ("wrong", "What went wrong"), ("lucky", "Where we got lucky"))
TIMESTAMP_KEYS = ("onset", "detected", "engaged", "mitigated", "resolved", "allClear")
PHASES = ("before", "after")
DERIVED = (("TTD", "Time to detect", "onset", "detected"),
           ("TTE", "Time to engage", "detected", "engaged"),
           ("TTM", "Time to mitigate", "onset", "mitigated"),
           ("TTR", "Time to resolve", "onset", "resolved"))
EVIDENCE_KINDS = ("notebooks", "monitors", "slack", "sentry", "linear", "builds", "prs", "runs", "images", "docs")
EVIDENCE_LABEL = {"notebooks": "Notebooks", "monitors": "Monitors", "slack": "Slack threads", "sentry": "Sentry",
                  "linear": "Linear", "builds": "Builds", "prs": "Pull requests", "runs": "Runs", "images": "Images",
                  "docs": "Documents"}
SNAPSHOT_SCHEMA = {"notebooks": "ir.notebook/1", "monitors": "ir.monitor/1", "slack": "ir.slack/1"}
EVIDENCE_NAMED = ("notebooks", "monitors", "builds", "prs")
COMPONENT_HOSTS = (("impact", "impact"), ("resolution", "resolution"), ("detection", "detection"))
COMPONENT_LISTS = ("causes", "notes")
RETRO_ACRONYMS = ("TTD", "TTE", "TTM", "TTR", "SEV")
HYPOTHESIS_STATES = ("ruled-out", "confirmed", "open")
HYPOTHESIS_LABEL = {"ruled-out": "Ruled out", "confirmed": "Confirmed", "open": "Still open"}
ID_SHAPES = (r"W\d+", r"T\d+", r"C\d+", r"AI\d+", r"I\d+", r"D\d+", r"H\d+", r"U\d+")
ID_TOKEN = re.compile(r"(?<![\w-])(?:" + "|".join(ID_SHAPES) + r")(?![\w-])")
CITE_GROUP = re.compile(r"\(((?:\s*(?:" + "|".join(ID_SHAPES) + r")\s*[,;]?)+)\s*\)")
FN_TOKEN = re.compile(r"\[\^(\d+)\]")
PROSE_SKIP = re.compile(r"https?://\S+|`[^`]*`")
COMPONENT_ID = re.compile(r"[a-z][a-z0-9-]*")
SEVERITY = re.compile(r"sev-\d")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
DERIVED_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\s*(?:min(?:ute)?s?|h(?:ou)?rs?|days?)\s+(?:after|to|before|from|until)\b", re.I)
DERIVED_TOPIC = re.compile(r"detect|mitigat|resolv|engag|onset|fired|all[- ]?clear", re.I)
SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
RENDER_TIMEOUT = 60
RENDER_CHECK_VIEWPORT = {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False}
VISIBLE_WORDS = 1500
VISIBLE_SLACK = 0
VISIBLE_JS = """(() => {
 const decorative = node => node.closest("[aria-hidden=true]") !== null;
 const hidden = node => decorative(node)
  || !node.checkVisibility({contentVisibilityAuto: true, opacityProperty: true, visibilityProperty: true})
  || !node.getClientRects().length;
 const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
 let words = 0;
 for (let n = walker.nextNode(); n; n = walker.nextNode()) {
  const text = n.nodeValue.trim();
  if (!text || !n.parentElement || hidden(n.parentElement)) continue;
  words += text.split(/\\s+/).length;
 }
 const shown = sel => [...document.querySelectorAll(sel)].filter(el => !hidden(el)).length;
 return {
  words,
  height: document.documentElement.scrollHeight,
  slack: shown(".ir-msg, .ir-slack .sumline"),
  bodies: shown(".ir-msg .msg, .ir-cell table, .ir-mon table, .ir-nb .nbbody"),
  open: shown("details[open]"),
 };
})()"""
RENDER_STATE_JS = """({
 ready: document.documentElement.dataset.ready || "",
 failed: [...document.querySelectorAll('[data-failed="1"]')].map(h => (h.dataset.source || h.dataset.component || h.id || h.tagName).split("\\n")[0].slice(0, 60)),
 unmounted: [...document.querySelectorAll('[data-component]')].filter(h => h.dataset.mounted !== "1").map(h => h.dataset.component),
 cells: [...document.querySelectorAll('[data-cell]')].length,
 pending: [...document.querySelectorAll('[data-cell]')].filter(h => h.dataset.rendered !== "1" && h.dataset.unrendered !== "1").map(h => (h.dataset.notebook || "?") + "/" + h.dataset.cell),
 snapshotless: [...document.querySelectorAll('[data-unrendered]')].filter(h => h.dataset.cell === undefined).map(h => h.dataset.notebook || h.dataset.source || h.id || h.tagName),
 uplot: typeof window.uPlot !== "undefined"
})"""
TEMPLATE_STAMP = re.compile(r"<!-- built by plugins/_shared/build\.py .*?sha256:([0-9a-f]+)")
TWINNED = (("summary", "the summary"), ("impact", "the impact"), ("resolution", "the resolution"),
           ("detection", "the detection story"))
HANDLED = (("windows", "W"), ("causes", "C"), ("actions", "AI"), ("decisions", "D"), ("hypotheses", "H"),
           ("unknowns", "U"))
HANDLE_WORDS = 6
TITLE_WORDS = 12
ACTION_TITLE_WORDS = 16
LESSON_WORDS = 40
METRIC_FIELDS = {"label", "value", "unit", "delta", "measured", "cites"}
NOTE_LENGTH = 90
DOC_TITLE_CHARS = 60
DOC_TITLE_WORDS = 8
SUBTITLE_CHARS = 120
SUBTITLE_WORDS = 20
TAG_COUNT = (2, 6)
TAG_SHAPE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
TITLE_ECHO = 0.6
SLUG_CHARS = 60
SLUG_WORDS = (3, 6)
SLUG_SHAPE = re.compile(r"(\d{4}-\d{2}-\d{2})-([a-z0-9]+(?:-[a-z0-9]+)*)")
SLUG_STOPWORDS = {"a", "an", "the", "and", "or", "but", "so", "of", "to", "in", "on", "at", "for", "from", "with",
                  "without", "by", "as", "is", "was", "were", "been", "be", "that", "this", "it", "its", "no", "not",
                  "every", "all", "any", "some", "our", "we", "us", "had", "has", "have", "did", "does", "do", "into",
                  "over", "under", "after", "before", "while", "when", "than", "then", "there", "their", "them"}
TITLE_INTERNALS = re.compile(r"[a-z0-9]_[a-z0-9]|\b[a-z]+[A-Z][a-z]|\.(?:py|ts|tsx|go|rs|sql|json|ya?ml)\b|--[a-z]")
TAKEAWAY_WORDS = 18
REFERENCE_SECTIONS = ("evidence", "glossary", "notes")
LEGACY_CUTOFF = "2026-09-19"
STATEMENT_WORDS = 25
KEY_MOMENTS = 8
DECISION_TITLE_WORDS = 16
RECOGNIZE_WORDS = 25
UNKNOWN_WORDS = 25
GLOSSARY_WORDS = 30
ENTRY_WORDS = 25
ENTRY_WORDS_MAX = 40
CAUSE_BODY_WORDS = 90
DECISION_BODY_WORDS = 60
UNKNOWN_BODY_WORDS = 45
LIVE_PHASES = ("detected", "investigating", "identified", "mitigated", "resolved")
LIVE_FIELDS = ("updatedAt", "phase", "headline", "currentState", "next", "source")
PHASE_STAMPS = {"detected": (), "investigating": ("engaged",), "identified": ("engaged",),
                "mitigated": ("engaged", "mitigated"),
                "resolved": ("engaged", "mitigated", "resolved", "allClear")}
LIVE_LINE_WORDS = 25
SUMMARY_KINDS = ("what-happened", "impact", "why", "what-changed", "still-open")
SUMMARY_KIND_TITLES = {"what-happened": "What happened", "impact": "What it cost", "why": "Why it happened",
                       "what-changed": "What changed", "still-open": "What is still open"}
XS_HEAD_WORDS = 14
XS_POINTS = 3
XS_POINT_WORDS = 18
SUMMARY_PAGE_TAG = re.compile(r"</?(html|head|body)\b", re.I)
SUMMARY_EMBED_TAG = re.compile(r"<(iframe|object|embed|script|style|form)\b", re.I)
SUMMARY_EVENT_ATTR = re.compile(r"\son[a-z]+\s*=", re.I)
SUMMARY_URL_ATTR = re.compile(r"\b(?:href|src|xlink:href)\s*=\s*[\"']([^\"']+)[\"']", re.I)


def parse_ts(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("is not a timestamp string")
    dt = datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("carries no UTC offset; write it as 2026-08-14T17:42:00-07:00")
    return dt


def try_ts(value):
    try:
        return parse_ts(value)
    except ValueError:
        return None


def is_date(value) -> bool:
    if not (isinstance(value, str) and DATE.fullmatch(value)):
        return False
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return False
    return True


def fmt_duration(seconds: float) -> str:
    total = int(round(seconds / 60))
    if total < 1:
        return "<1m"
    days, rem = divmod(total, 1440)
    hours, minutes = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h {minutes:02d}m" if minutes else f"{hours}h"
    return f"{minutes}m"


def zone(meta: dict):
    try:
        return zoneinfo.ZoneInfo(meta.get("timezone") or "UTC")
    except (zoneinfo.ZoneInfoNotFoundError, ValueError, TypeError):
        return datetime.timezone.utc


def fmt_local(value, tz) -> str:
    dt = try_ts(value)
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M %Z") if dt else str(value)


def derived_numbers(timestamps: dict) -> list:
    out = []
    for code, label, start, end in DERIVED:
        a, b = try_ts((timestamps or {}).get(start)), try_ts((timestamps or {}).get(end))
        seconds = (b - a).total_seconds() if a and b else None
        out.append({"id": code, "label": label, "from": start, "to": end, "seconds": seconds,
                    "value": fmt_duration(seconds) if seconds is not None and seconds >= 0 else None})
    return out


def load_retro(root: Path, cmd: str):
    try:
        data = json.loads((root / "retro.json").read_text())
    except (OSError, ValueError) as e:
        print(f"{cmd}: cannot load retro.json: {e}", file=sys.stderr)
        return None
    if not isinstance(data, dict):
        print(f"{cmd}: retro.json must be a JSON object", file=sys.stderr)
        return None
    return data


def plugin_version() -> str:
    manifest = SKILL.parents[1] / ".claude-plugin" / "plugin.json"
    return json.loads(manifest.read_text())["version"] if manifest.exists() else "0"


def write_retro(root: Path, R: dict):
    path = root / "retro.json"
    scratch = path.with_name(f"retro.json.{os.getpid()}.part")
    scratch.write_text(json.dumps(R, indent=2, ensure_ascii=False) + "\n")
    scratch.replace(path)


def load_builder():
    spec = importlib.util.spec_from_file_location("build_pdf", SCRIPTS / "build-pdf.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sibling_module(name: str):
    path = SCRIPTS / f"{name}.py"
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SLACK_PERMALINK = sibling_module("retro_evidence").SLACK_PERMALINK


def evidence_files(root: Path) -> list:
    folder = root / "evidence"
    return sorted(p for p in folder.rglob("*") if p.is_file() and p.name != ".gitkeep") if folder.is_dir() else []


def evidence_digest(root: Path):
    files = evidence_files(root)
    if not files:
        return None
    h = hashlib.sha256()
    for p in files:
        h.update(p.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()[:12]


def snapshot_digests(root: Path) -> dict:
    files = {}
    evidence = evidence_digest(root)
    if evidence:
        files["evidence"] = evidence
    return files


def entries(R, reg):
    return [e for e in (R.get(reg) or []) if isinstance(e, dict)]


def monitors_of(R: dict) -> list:
    detection = R.get("detection")
    return entries(detection, "monitors") if isinstance(detection, dict) else []


def handles_of(R: dict) -> dict:
    tz = zone(R.get("meta") or {})
    out = {}
    for reg, _ in HANDLED:
        for e in entries(R, reg):
            if e.get("id"):
                out[str(e["id"])] = e.get("h") or e.get("t") or e.get("q") or str(e["id"])
    for e in (R.get("meta") or {}).get("subIncidents") or []:
        if isinstance(e, dict) and e.get("id"):
            out[str(e["id"])] = e.get("h") or e.get("t") or str(e["id"])
    for e in entries(R, "timeline"):
        if e.get("id"):
            when = try_ts(e.get("ts"))
            out[str(e["id"])] = when.astimezone(tz).strftime("%H:%M") if when else str(e["id"])
    return out


def retro_ids(R: dict) -> set:
    ids = set()
    for reg in ("windows", "timeline", "causes", "actions", "decisions", "hypotheses", "unknowns"):
        ids.update(str(e["id"]) for e in entries(R, reg) if e.get("id") is not None)
    for e in (R.get("meta") or {}).get("subIncidents") or []:
        if isinstance(e, dict) and e.get("id") is not None:
            ids.add(str(e["id"]))
    return ids


def id_matcher(known) -> re.Pattern:
    named = sorted({str(k) for k in known if k}, key=len, reverse=True)
    return re.compile(r"(?<![\w-])(?:" + "|".join(ID_SHAPES + tuple(map(re.escape, named))) + r")(?![\w-])")


def prose_only(text: str) -> str:
    return PROSE_SKIP.sub(" ", text)


def prose_slots(R: dict):
    """(address, holder, key) for every field a writer authors in retro.json."""
    def slot(where, holder, key):
        if isinstance(holder, dict):
            yield where, holder, key

    yield from slot("summary.text", R.get("summary"), "text")
    for e in entries(R, "windows"):
        yield from slot(f"{e.get('id')}.text", e, "text")
    for i, e in enumerate(entries(R, "timeline")):
        yield from slot(f"{e.get('id') or f'timeline[{i}]'}.text", e, "text")
    impact = R.get("impact") or {}
    yield from slot("impact.text", R.get("impact"), "text")
    for i, t in enumerate(impact.get("teams") or []):
        yield from slot(f"impact.teams[{i}].text", t, "text")
    for e in entries(R, "causes"):
        yield from slot(f"{e.get('id')}.text", e, "text")
        yield from slot(f"{e.get('id')}.code.caption", e.get("code"), "caption")
    for e in entries(R, "actions"):
        yield from slot(f"{e.get('id')}.t", e, "t")
        yield from slot(f"{e.get('id')}.note", e, "note")
    for s in (R.get("meta") or {}).get("subIncidents") or []:
        yield from slot(f"{s.get('id')}.t", s, "t")
    yield from slot("resolution.text", R.get("resolution"), "text")
    yield from slot("detection.text", R.get("detection"), "text")
    for e in entries(R, "decisions"):
        yield from slot(f"{e.get('id')}.t", e, "t")
        yield from slot(f"{e.get('id')}.why", e, "why")
        yield from slot(f"{e.get('id')}.alternatives", e, "alternatives")
    for e in entries(R, "hypotheses"):
        yield from slot(f"{e.get('id')}.t", e, "t")
        yield from slot(f"{e.get('id')}.exonerated", e, "exonerated")
    for i, row in enumerate(R.get("recognize") or []):
        for key in ("signal", "means", "do"):
            yield from slot(f"recognize[{i}].{key}", row, key)
    for e in entries(R, "unknowns"):
        yield from slot(f"{e.get('id')}.q", e, "q")
        yield from slot(f"{e.get('id')}.why", e, "why")
    for i, row in enumerate(R.get("glossary") or []):
        yield from slot(f"glossary[{i}].def", row, "def")
    for key, _ in LESSON_COLUMNS:
        for i, e in enumerate((R.get("lessons") or {}).get(key) or []):
            yield from slot(f"lessons.{key}[{i}].text", e, "text")
    for i, e in enumerate((R.get("evidence") or {}).get("images") or []):
        yield from slot(f"evidence.images[{i}].caption", e, "caption")
    for i, e in enumerate(entries(R, "notes")):
        yield from slot(f"notes[{i}].md", e, "md")
    for i, e in enumerate(entries(R, "footnotes")):
        yield from slot(f"footnotes[{e.get('n', i)}].b", e, "b")


def prose_fields(R: dict):
    for where, holder, key in prose_slots(R):
        value = holder.get(key)
        if isinstance(value, str) and value.strip():
            yield where, value


def retro_slug(given, title: str, date: str) -> str:
    if given:
        return given if SLUG_SHAPE.fullmatch(given) else f"{date}-{slugify(given)}"
    words_out = [w for w in slugify(title).split("-") if w and w not in SLUG_STOPWORDS]
    return "-".join([date] + words_out[:SLUG_WORDS[1]])


def scaffold(args) -> int:
    dest = Path(args.dir)
    if not args.example and not args.title:
        print("scaffold: pass --title, or --example for the Acme worked example.", file=sys.stderr)
        return 1
    if args.date and not is_date(args.date):
        print(f"scaffold: --date {args.date!r} is not a calendar date in YYYY-MM-DD.", file=sys.stderr)
        return 1
    if dest.exists() and any(dest.iterdir()):
        print(f"scaffold: {dest} exists and is not empty; refusing to overwrite.", file=sys.stderr)
        return 1
    src = TEMPLATES / ("example" if args.example else "starter")
    shutil.copytree(src, dest, dirs_exist_ok=True)
    template = TEMPLATES / PAGE
    if template.exists():
        shutil.copy(template, dest / PAGE)
    else:
        print(f"scaffold: {template} is missing; the retro renders once the template ships", file=sys.stderr)
    if not args.example:
        for name in EVIDENCE_DIRS:
            (dest / "evidence" / name).mkdir(parents=True, exist_ok=True)
        date = args.date or datetime.date.today().isoformat()
        slug = retro_slug(args.slug, args.title, date)
        for name in PROJECT_FILES:
            p = dest / name
            p.write_text(p.read_text().replace("PROJECT_TITLE", args.title).replace("PROJECT_SLUG", slug)
                         .replace("PROJECT_SUBTITLE", args.subtitle or args.title).replace("PROJECT_DATE", date))
        R = json.loads((dest / "retro.json").read_text())
        if args.incident is None:
            R["meta"].pop("incident")
        else:
            R["meta"]["incident"] = {"number": args.incident}
        R["meta"]["tags"] = [t.strip() for t in (args.tags or "").split(",") if t.strip()]
        write_retro(dest, R)
    print(f"scaffolded {dest} ({'Acme example' if args.example else 'starter'})")
    print(f"serve:  cd {dest} && python3 -m http.server 8641")
    print(f"check:  {Path(__file__).name} check {dest}")
    print(f"pdf:    {Path(__file__).name} pdf {dest}")
    return 0


def pdf(args) -> int:
    root = Path(args.dir)
    if not (root / PAGE).exists() and not (root / "index.html").exists():
        print(f"pdf: {root} holds no {PAGE} or index.html; copy templates/{PAGE} beside retro.json first.", file=sys.stderr)
        return 1
    return load_builder().build(root, PDF_NAME)


def snapshot(args) -> int:
    root = Path(args.dir)
    data = load_retro(root, "snapshot")
    if data is None:
        return 1
    meta = data.setdefault("meta", {})
    revisions = meta.get("revisions") or []
    last = max(meta.get("rev") or 0, max((r.get("rev") or 0 for r in revisions), default=0))

    files = snapshot_digests(root)
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
                print(f"snapshot: nothing changed since rev {last} — no retro.json or evidence edits (use --force to record anyway)")
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
    history_path = root / "history" / f"rev-{rev}.json"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(payload)
    (root / "retro.json").write_text(payload)
    note = f" (changed: {', '.join(entry['changed'])})" if entry.get("changed") else ""
    print(f"snapshot: rev {rev} recorded → history/rev-{rev}.json{note}")
    return 0


def render_check(args) -> int:
    root = Path(args.dir)
    R = load_retro(root, "render-check")
    if R is None:
        return 1
    if not (root / PAGE).exists() and not (root / "index.html").exists():
        print(f"render-check: {root} holds no {PAGE} or index.html; copy templates/{PAGE} beside retro.json first.", file=sys.stderr)
        return 1
    builder = load_builder()
    page = builder.doc_page(root)
    chrome = builder.Chrome(builder.require_chrome())
    server, base = builder.serve(root)
    problems, state, visible = [], {}, {}
    try:
        session = builder.open_page(chrome, base + page)
        chrome.call("Emulation.setDeviceMetricsOverride", RENDER_CHECK_VIEWPORT, session=session)
        ready = builder.settle(chrome, session, args.timeout)
        if ready["ready"] != "1":
            problems.append(builder.ready_problem(ready, args.timeout))
        state = builder.evaluate(chrome, session, RENDER_STATE_JS) or {}
        visible = builder.evaluate(chrome, session, VISIBLE_JS) or {}
    except builder.ChromeError as e:
        problems.append(str(e))
    finally:
        report = builder.diagnostics(chrome)
        server.shutdown()
        chrome.close()

    for e in builder.page_errors(chrome):
        problems.append("page: " + first_line(e, 200))
    for label in state.get("failed") or []:
        problems.append(f"the page marked {label!r} as failed")
    for name in sorted(set(state.get("unmounted") or [])):
        problems.append(f"the component {name!r} never mounted, so the page shows its fallback")
    for cell in state.get("pending") or []:
        problems.append(f"notebook cell {cell} neither rendered nor declared itself unrendered")
    for notebook in state.get("snapshotless") or []:
        problems.append(f"notebook {notebook} has no snapshot")
    has_timeseries = any(cell.get("type") == "timeseries" for cell in notebook_cells(root, R))
    if has_timeseries and state and not state.get("uplot"):
        problems.append("a notebook carries a timeseries cell but window.uPlot never loaded; the charts are blank")
    if visible:
        if visible["words"] > args.words:
            problems.append(f"the page opens on {visible['words']} words; {args.words} is what a reader finishes "
                            f"before deciding to open anything, so move the rest behind a disclosure")
        if visible["slack"] > VISIBLE_SLACK:
            problems.append(f"{visible['slack']} Slack message line(s) render with every disclosure closed; a "
                            f"transcript opens only when a reader asks for it")
        if visible["bodies"]:
            problems.append(f"{visible['bodies']} notebook, monitor or transcript body render(s) with every "
                            f"disclosure closed")
    for p in problems:
        print(f"ERROR: {p}")
    if problems:
        print("\n".join(report))
        return 1
    print(f"render-check: page ready, {state.get('cells', 0)} notebook cell(s) rendered, every component mounted")
    print(f"render-check: {visible.get('words', 0)} words and {visible.get('height', 0)}px tall with every "
          f"disclosure closed, {visible.get('open', 0)} open")
    return 0


def notebook_cells(root: Path, R: dict):
    for nb in (R.get("evidence") or {}).get("notebooks") or []:
        if not isinstance(nb, dict) or not isinstance(nb.get("file"), str):
            continue
        try:
            data = json.loads((root / nb["file"]).read_text())
        except (OSError, ValueError):
            continue
        for cell in (data.get("cells") or []) if isinstance(data, dict) else []:
            if isinstance(cell, dict):
                yield cell


def check_title(rep, title: str):
    if len(title) > DOC_TITLE_CHARS:
        rep.err(f"meta.title is {len(title)} characters; the headline names the failure in {DOC_TITLE_CHARS} or "
                f"fewer, and the causal sentence belongs in meta.subtitle")
    elif words(title) > DOC_TITLE_WORDS:
        rep.err(f"meta.title is {words(title)} words; {DOC_TITLE_WORDS} is the bound for a headline, and the causal "
                f"sentence belongs in meta.subtitle")
    if ":" in title:
        rep.strict_warn("meta.title carries a colon, the shape of a symptom followed by its internals; write the "
                        "noun phrase a reader outside the response would say")
    found = TITLE_INTERNALS.search(title)
    if found:
        rep.strict_warn(f"meta.title names {found.group(0)!r}, an identifier rather than a mechanism; a column, image "
                        "or service name belongs in a cause, not the title")


def check_subtitle(rep, subtitle: str):
    if len(subtitle) > SUBTITLE_CHARS:
        rep.err(f"meta.subtitle is {len(subtitle)} characters; one sentence states the mechanism in "
                f"{SUBTITLE_CHARS} or fewer")
    elif words(subtitle) > SUBTITLE_WORDS:
        rep.strict_warn(f"meta.subtitle is {words(subtitle)} words; {SUBTITLE_WORDS} is the bound for one plain sentence")
    if ":" in subtitle:
        rep.strict_warn("meta.subtitle carries a colon, the shape of a symptom followed by its internals; write the "
                        "one sentence a reader outside the response would say")
    found = TITLE_INTERNALS.search(subtitle)
    if found:
        rep.strict_warn(f"meta.subtitle names {found.group(0)!r}, an identifier rather than a mechanism; a column, "
                        "image or service name belongs in a cause, not the subtitle")


def check_tags(rep, meta, tags):
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        rep.err("meta.tags must be a list of lower-case hyphenated topical tags, as in [\"migration\", \"release-pipeline\"]")
        return
    low, high = TAG_COUNT
    if not low <= len(tags) <= high:
        report = rep.strict_warn if meta.get("status") == "ongoing" else rep.err
        report(f"meta.tags carries {len(tags)} tags; {low} to {high} name the system, the failure class and the "
               f"surface without turning into a second summary")
    for t in tags:
        if not TAG_SHAPE.fullmatch(t):
            rep.err(f"meta.tags carries {t!r}; a tag is lower-case words joined by hyphens")
    duplicated = sorted({t for t in tags if tags.count(t) > 1})
    if duplicated:
        rep.err(f"meta.tags repeats {', '.join(duplicated)}")
    teams = {slugify(t) for t in (meta.get("teams") or ()) if isinstance(t, str)}
    shared = sorted(set(tags) & teams)
    if shared:
        rep.warn(f"meta.tags repeats the team codename{'s' if len(shared) > 1 else ''} {', '.join(shared)}; the page "
                 "already renders team chips, and a tag names a system or a failure class")


def overlap(a: str, b: str) -> float:
    left = {w for w in re.findall(r"[a-z0-9]+", a.lower()) if w not in SLUG_STOPWORDS}
    right = {w for w in re.findall(r"[a-z0-9]+", b.lower()) if w not in SLUG_STOPWORDS}
    return len(left & right) / len(left) if left else 0.0


def check_slug(rep, R, meta, slug: str):
    if len(slug) > SLUG_CHARS:
        rep.err(f"meta.slug is {len(slug)} characters; the slug is a URL, bounded at {SLUG_CHARS}")
    shape = SLUG_SHAPE.fullmatch(slug)
    if not shape:
        rep.err(f"meta.slug {slug!r} is not a date followed by plain words, as in 2026-09-18-schema-ahead-of-deploy")
        return
    low, high = SLUG_WORDS
    count = len(shape.group(2).split("-"))
    if not low <= count <= high:
        rep.strict_warn(f"meta.slug carries {count} words after the date; {low} to {high} keeps the URL short")
    onset = try_ts((R.get("timestamps") or {}).get("onset"))
    date = onset.astimezone(zone(meta)).date().isoformat() if onset else meta.get("date")
    if isinstance(date, str) and is_date(date) and shape.group(1) != date:
        rep.warn(f"meta.slug opens on {shape.group(1)}, not {date}, the day the incident started")


def check_meta(rep, R, meta):
    for k in ("title", "subtitle", "slug", "date"):
        if not (isinstance(meta.get(k), str) and meta[k].strip()):
            rep.err(f"meta.{k} is missing or empty")
    if isinstance(meta.get("title"), str) and meta["title"].strip():
        check_title(rep, meta["title"].strip())
    if isinstance(meta.get("subtitle"), str) and meta["subtitle"].strip():
        check_subtitle(rep, meta["subtitle"].strip())
    check_tags(rep, meta, meta.get("tags"))
    if isinstance(meta.get("slug"), str) and meta["slug"].strip():
        check_slug(rep, R, meta, meta["slug"].strip())
    if isinstance(meta.get("date"), str) and not is_date(meta["date"]):
        rep.err(f"meta.date {meta['date']!r} is not a calendar date in YYYY-MM-DD")
    if meta.get("status") not in STATUSES:
        rep.err(f"meta.status {meta.get('status')!r} not in {', '.join(STATUSES)}")
    if "draft" in meta and not isinstance(meta["draft"], bool):
        rep.err("meta.draft must be true or false")
    incident = meta.get("incident")
    if incident is not None:
        if not isinstance(incident, dict):
            rep.err("meta.incident must be an object {number?, severity?, severityLink?}")
        else:
            n = incident.get("number")
            if n is not None and (not isinstance(n, int) or isinstance(n, bool) or n < 1):
                rep.err("meta.incident.number must be a positive integer")
            sev = incident.get("severity")
            if sev is not None and not (isinstance(sev, str) and SEVERITY.fullmatch(sev)):
                rep.err(f"meta.incident.severity {sev!r} does not read sev-N")
            link = incident.get("severityLink")
            if link is not None and (not isinstance(link, str) or foreign_scheme(link) or not link.startswith("https://")):
                rep.err("meta.incident.severityLink must be an https:// URL")
            for extra in sorted(set(incident) - {"number", "severity", "severityLink"}):
                rep.warn(f"meta.incident carries {extra!r}, which the page ignores")
    for key in ("authors", "attendees", "teams"):
        values = meta.get(key)
        if values is None:
            continue
        if not (isinstance(values, list) and all(isinstance(v, str) and v.strip() for v in values)):
            rep.err(f"meta.{key} must be a list of non-empty strings")
            continue
        for v in values:
            if key != "teams" and ("@" in v or v.lower().startswith("mailto:")):
                rep.err(f"meta.{key} names {v!r}; people are named, never addressed")
    if meta.get("status") not in PROVISIONAL and not meta.get("authors"):
        rep.strict_warn("meta.authors is empty; a retro past draft names who wrote it")
    commander = meta.get("commander")
    if commander is not None and not (isinstance(commander, str) and commander.strip() and "@" not in commander):
        rep.err("meta.commander must be a person's name")
    repo = meta.get("repo")
    if repo is not None and not (isinstance(repo, str) and REPO_SLUG.match(repo)):
        rep.err(f"meta.repo {repo!r} is not an owner/repo slug")
    ref = meta.get("ref")
    if ref is not None and not (isinstance(ref, str) and GIT_REF.fullmatch(ref)):
        rep.err(f"meta.ref {ref!r} is not a branch name or commit sha; letters, digits, '/', '.', '_' and '-', no '..'")
    tz = meta.get("timezone")
    if tz is not None:
        try:
            zoneinfo.ZoneInfo(tz)
        except (zoneinfo.ZoneInfoNotFoundError, ValueError, TypeError):
            rep.err(f"meta.timezone {tz!r} is not an IANA zone name")
    home = meta.get("homeLink")
    if home is not None:
        if not isinstance(home, dict):
            rep.err("meta.homeLink must be an object with 'href' and 'label'")
        else:
            for k in ("href", "label"):
                if not (isinstance(home.get(k), str) and home[k].strip()):
                    rep.err(f"meta.homeLink.{k} must be a non-empty string")
            if isinstance(home.get("href"), str) and foreign_scheme(home["href"]):
                rep.err(f"meta.homeLink.href uses the {foreign_scheme(home['href'])}: scheme; it must be relative or an http(s) URL")
    subs = meta.get("subIncidents")
    sub_ids = set()
    if subs is not None:
        if not isinstance(subs, list):
            rep.err("meta.subIncidents must be a list of {id, t, h}")
        else:
            sub_ids = check_ids(rep, [s for s in subs if isinstance(s, dict)], r"I\d+", "meta.subIncidents")
            for s in subs:
                if not isinstance(s, dict):
                    rep.err(f"meta.subIncidents entry {s!r} is not an object")
                elif not (isinstance(s.get("t"), str) and s["t"].strip()):
                    rep.err(f"{s.get('id')}.t is missing or empty")
    sections = meta.get("sections")
    if sections is not None:
        if not isinstance(sections, dict):
            rep.err("meta.sections must map a section id to {sub}")
        else:
            for sid, cfg in sections.items():
                if sid not in SECTION_IDS:
                    rep.warn(f"meta.sections[{sid}] is not a section id ({', '.join(SECTION_IDS)})")
                if not isinstance(cfg, dict) or not any(isinstance(cfg.get(k), str) and cfg[k].strip()
                                                        for k in ("sub", "takeaway")):
                    rep.err(f"meta.sections[{sid}] must carry 'sub', 'takeaway', or both")
                    continue
                for extra in sorted(set(cfg) - {"sub", "takeaway"}):
                    rep.warn(f"meta.sections[{sid}] carries {extra!r}, which the page ignores")
                take = cfg.get("takeaway")
                if isinstance(take, str) and sid in REFERENCE_SECTIONS:
                    rep.strict_warn(f"meta.sections[{sid}].takeaway states a conclusion for a reference section; "
                                    f"{', '.join(REFERENCE_SECTIONS)} carry a heading and their collapsed structure, "
                                    f"and the conclusions live in the sections that argue them")
                elif isinstance(take, str) and words(take) > TAKEAWAY_WORDS:
                    rep.strict_warn(f"meta.sections[{sid}].takeaway is {words(take)} words; a section opens on one "
                                    f"sentence of {TAKEAWAY_WORDS} or fewer, with the detail behind the disclosure")
    acronyms = meta.get("acronyms")
    if acronyms is not None and not (isinstance(acronyms, list) and all(isinstance(a, str) and a.strip() for a in acronyms)):
        rep.err("meta.acronyms must be a list of non-empty strings")
    ai = meta.get("ai")
    if ai is not None:
        if not isinstance(ai, dict):
            rep.err("meta.ai must be an object, e.g. {\"suggest\": {\"causes\": [\"What was the root cause?\"]}}")
        elif ai.get("suggest") is not None:
            suggest = ai["suggest"]
            if not isinstance(suggest, dict):
                rep.err("meta.ai.suggest must map a section id to its list of suggested prompts")
            else:
                for sid, prompts in suggest.items():
                    if sid not in SECTION_IDS:
                        rep.warn(f"meta.ai.suggest[{sid}] is not a section id ({', '.join(SECTION_IDS)}); its prompts render under no heading")
                    if not (isinstance(prompts, list) and prompts and all(isinstance(p, str) and p.strip() for p in prompts)):
                        rep.err(f"meta.ai.suggest[{sid}] must be a non-empty list of prompt strings")
    return sub_ids


def check_ids(rep, items, pattern, label) -> set:
    seen = set()
    for e in items:
        i = e.get("id")
        if i is None:
            rep.err(f"{label}: entry missing 'id': {first_line(json.dumps(e, ensure_ascii=False), 60)}")
            continue
        if i in seen:
            rep.err(f"{label}: duplicate id {i}")
        seen.add(i)
        if not re.fullmatch(pattern, str(i)):
            rep.err(f"{label}: id {i!r} does not match {pattern}")
    return seen


def check_timestamps(rep, R, provisional: bool) -> dict:
    raw = R.get("timestamps")
    if not isinstance(raw, dict):
        rep.err("timestamps must be an object with onset, detected, engaged, mitigated, resolved, allClear")
        return {}
    for extra in sorted(set(raw) - set(TIMESTAMP_KEYS)):
        rep.err(f"timestamps.{extra} is not one of {', '.join(TIMESTAMP_KEYS)}")
    parsed = {}
    for key in TIMESTAMP_KEYS:
        value = raw.get(key)
        if value is None:
            if key in ("onset", "resolved"):
                if not provisional:
                    rep.err(f"timestamps.{key} is null; only an ongoing or draft retro may leave it unset")
                elif (R.get("meta") or {}).get("status") != "ongoing" or key == "onset":
                    rep.warn(f"timestamps.{key} is null; the tiles cannot compute anything without it")
            continue
        try:
            parsed[key] = parse_ts(value)
        except ValueError as e:
            rep.err(f"timestamps.{key} {value!r} {e}")
    present = [(k, parsed[k]) for k in TIMESTAMP_KEYS if k in parsed]
    for (ka, a), (kb, b) in zip(present, present[1:]):
        if b < a:
            rep.err(f"timestamps.{kb} ({b.isoformat()}) is before timestamps.{ka} ({a.isoformat()}); the order is {' ≤ '.join(TIMESTAMP_KEYS)}")
    return parsed


def check_history(rep, where, entry, key: str, states, current):
    """The scrubber reads history[] to render an entry as it stood at T."""
    rows = entry.get("history")
    if rows is None:
        return
    if not isinstance(rows, list) or not rows:
        rep.err(f"{where}.history must be a non-empty list of {{ts, {key}}}")
        return
    stamps = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            rep.err(f"{where}.history[{i}] is not an object")
            continue
        try:
            stamps.append(parse_ts(row.get("ts")))
        except ValueError as e:
            rep.err(f"{where}.history[{i}].ts {row.get('ts')!r} {e}")
        if row.get(key) not in states:
            rep.err(f"{where}.history[{i}].{key} {row.get(key)!r} not in {', '.join(states)}")
        for extra in sorted(set(row) - {"ts", key}):
            rep.warn(f"{where}.history[{i}] carries {extra!r}, which the scrubber ignores")
    if any(b < a for a, b in zip(stamps, stamps[1:])):
        rep.err(f"{where}.history runs backwards; the scrubber steps through it in order")
    last = rows[-1] if isinstance(rows[-1], dict) else {}
    if last.get(key) in states and last[key] != current:
        rep.err(f"{where}.history ends on {last[key]!r} but {where}.{key} is {current!r}; the last entry is the "
                f"state the page renders at now")


def check_phase_clock(rep, R, phase: str):
    """The strip reads live.phase and the tiles derive from timestamps, so the two state one story."""
    timestamps = R.get("timestamps") if isinstance(R.get("timestamps"), dict) else {}
    for key in PHASE_STAMPS[phase]:
        if not timestamps.get(key):
            rep.err(f"live.phase is {phase!r} but timestamps.{key} is null; the phase says the incident "
                    f"reached that point, so the moment it did belongs on the clock the tiles read")
    for key in TIMESTAMP_KEYS:
        if not timestamps.get(key):
            continue
        reached = sorted(p for p in LIVE_PHASES if key in PHASE_STAMPS[p])
        if reached and phase not in reached:
            rep.err(f"timestamps.{key} is set but live.phase is {phase!r}; that clock belongs to "
                    f"{' or '.join(reached)}")


def check_live(rep, R, status: str):
    live = R.get("live")
    if live is None:
        if status == "ongoing":
            rep.err("meta.status is ongoing but the retro carries no live block; run retro.py live init")
        return
    if not isinstance(live, dict):
        rep.err("live must be an object {updatedAt, phase, headline, currentState, next, source?}")
        return
    for extra in sorted(set(live) - set(LIVE_FIELDS)):
        rep.err(f"live.{extra} is not one of {', '.join(LIVE_FIELDS)}")
    try:
        parse_ts(live.get("updatedAt"))
    except ValueError as e:
        rep.err(f"live.updatedAt {live.get('updatedAt')!r} {e}")
    if live.get("phase") not in LIVE_PHASES:
        rep.err(f"live.phase {live.get('phase')!r} not in {', '.join(LIVE_PHASES)}")
    else:
        check_phase_clock(rep, R, live["phase"])
    for key, budget in (("headline", XS_HEAD_WORDS), ("currentState", LIVE_LINE_WORDS), ("next", LIVE_LINE_WORDS)):
        value = live.get(key)
        if not (isinstance(value, str) and value.strip()):
            rep.err(f"live.{key} is missing or empty; the status strip reads it while the incident runs")
        elif words(value) > budget:
            rep.strict_warn(f"live.{key} is {words(value)} words; the strip shows {budget} or fewer")
    source = live.get("source")
    if source is None:
        if status == "ongoing":
            rep.err("live.source is unset on an ongoing retro; the page polls {repo, branch} for its updates")
        return
    if status != "ongoing":
        rep.err(f"live.source still names a branch on a {status} retro; retro.py live finalize drops it when the "
                f"page stops polling")
    if not isinstance(source, dict):
        rep.err("live.source must be {repo, branch}")
        return
    if not (isinstance(source.get("repo"), str) and REPO_SLUG.match(source["repo"])):
        rep.err(f"live.source.repo {source.get('repo')!r} is not an owner/repo slug")
    if not (isinstance(source.get("branch"), str) and GIT_REF.fullmatch(source.get("branch") or "")):
        rep.err(f"live.source.branch {source.get('branch')!r} is not a branch name")
    for extra in sorted(set(source) - {"repo", "branch"}):
        rep.err(f"live.source.{extra} is not one of repo, branch")


def check_link_list(rep, where, entry, closes_ok: bool, field: str = "links") -> list:
    raw = entry.get(field) if isinstance(entry, dict) else None
    if raw is None:
        return []
    if not isinstance(raw, list):
        rep.err(f"{where}: {field} must be a list of URLs or {{url, kind?, label?, closes?}} objects")
        return []
    out = []
    for link in raw:
        norm, problem = normalise_link(link)
        if problem:
            rep.err(f"{where}: {field[:-1]} {problem}")
            continue
        if not closes_ok and isinstance(link, dict) and "closes" in link:
            rep.err(f"{where}: {field[:-1]} {norm['url']} carries closes; it belongs on the action the change lands")
            continue
        out.append(norm)
    return out


def check_windows(rep, R, ts: dict, sub_ids: set):
    windows = R.get("windows")
    if windows is None:
        return
    if not isinstance(windows, list):
        rep.err("windows must be a list")
        return
    items = [w for w in windows if isinstance(w, dict)]
    for w in windows:
        if not isinstance(w, dict):
            rep.err(f"windows entry {w!r} is not an object")
    check_ids(rep, items, r"W\d+", "windows")
    spans = {}
    for w in items:
        wid = w.get("id")
        if w.get("kind") not in WINDOW_KINDS:
            rep.err(f"{wid}: kind {w.get('kind')!r} not in {', '.join(WINDOW_KINDS)}")
        bounds = []
        for key in ("start", "end"):
            try:
                bounds.append(parse_ts(w.get(key)))
            except ValueError as e:
                rep.err(f"{wid}.{key} {w.get(key)!r} {e}")
                bounds.append(None)
        start, end = bounds
        if start and end:
            if end <= start:
                rep.err(f"{wid}: end {end.isoformat()} is not after start {start.isoformat()}")
            else:
                spans[wid] = (start, end, w)
            if ts.get("onset") and start < ts["onset"]:
                rep.warn(f"{wid} starts before timestamps.onset; onset is the first moment of impact")
        teams = w.get("teams")
        if teams is not None and not (isinstance(teams, list) and all(isinstance(t, str) and t.strip() for t in teams)):
            rep.err(f"{wid}.teams must be a list of team codenames")
        if w.get("incident") is not None and w["incident"] not in sub_ids:
            rep.err(f"{wid}: incident {w['incident']!r} is not in meta.subIncidents")
    for (ida, (a0, a1, wa)), (idb, (b0, b1, wb)) in ((x, y) for x in spans.items() for y in spans.items() if x[0] < y[0]):
        shared = set(wa.get("teams") or []) & set(wb.get("teams") or [])
        if shared and a0 < b1 and b0 < a1:
            rep.warn(f"{ida} and {idb} overlap on {', '.join(sorted(shared))}; one window per team per period reads cleaner")


def check_timeline(rep, R, ts: dict, window_ids: set, slack_snapshots: set) -> set:
    timeline = R.get("timeline")
    if timeline is None:
        return set()
    if not isinstance(timeline, list):
        rep.err("timeline must be a list")
        return set()
    items = [t for t in timeline if isinstance(t, dict)]
    for t in timeline:
        if not isinstance(t, dict):
            rep.err(f"timeline entry {t!r} is not an object")
    ids = check_ids(rep, [t for t in items if t.get("id") is not None], r"T\d+", "timeline")
    lo, hi = ts.get("onset"), ts.get("allClear") or ts.get("resolved")
    previous = None
    for i, t in enumerate(items):
        where = str(t.get("id") or f"timeline[{i}]")
        if t.get("kind") not in TIMELINE_KINDS:
            rep.err(f"{where}: kind {t.get('kind')!r} not in {', '.join(TIMELINE_KINDS)}")
        if not (isinstance(t.get("text"), str) and t["text"].strip()):
            rep.err(f"{where}.text is missing or empty")
        elif words(prose_only(t["text"])) > ENTRY_WORDS_MAX:
            rep.err(f"{where}.text is {words(prose_only(t['text']))} words; an entry says what happened in "
                    f"{ENTRY_WORDS} and the detail it carries belongs in a cause or the evidence it cites")
        elif words(prose_only(t["text"])) > ENTRY_WORDS:
            rep.strict_warn(f"{where}.text is {words(prose_only(t['text']))} words; {ENTRY_WORDS} is the bound for one "
                            f"moment, and the short name h is what a reader sees first")
        actor = t.get("actor")
        if actor is not None and not (isinstance(actor, str) and actor.strip()):
            rep.err(f"{where}.actor must be a non-empty string")
        try:
            when = parse_ts(t.get("ts"))
        except ValueError as e:
            rep.err(f"{where}.ts {t.get('ts')!r} {e}")
            when = None
        if when and previous and when < previous:
            rep.err(f"{where} at {when.isoformat()} is out of order; the timeline is sorted by ts")
        previous = when or previous
        phase = t.get("phase")
        if phase is not None and phase not in PHASES:
            rep.err(f"{where}.phase {phase!r} is not before or after")
        if when and lo and hi:
            inside = lo <= when <= hi
            if not inside and phase is None:
                rep.err(f"{where} at {when.isoformat()} falls outside [onset, {'allClear' if ts.get('allClear') else 'resolved'}] and carries no phase: before|after")
            elif inside and phase is not None:
                rep.warn(f"{where} carries phase {phase!r} but sits inside the incident; drop the phase")
            elif phase == "before" and when > hi or phase == "after" and when < lo:
                rep.err(f"{where}.phase {phase!r} disagrees with its ts")
        if t.get("window") is not None and t["window"] not in window_ids:
            rep.err(f"{where}: window {t['window']!r} is not a window id")
        refs = check_link_list(rep, where, t, False, "refs")
        for ref in refs:
            if ".slack.com/" in ref["url"]:
                if not SLACK_PERMALINK.match(ref["url"]):
                    rep.err(f"{where}: ref {ref['url']} is not a Slack message permalink (…/archives/C…/p…)")
                elif ref["url"] not in slack_snapshots:
                    rep.warn(f"{where}: Slack ref {ref['url']} has no snapshot under evidence/slack; the page cannot quote it")
        if t.get("kind") == "deploy" and not any(r["kind"] == "pr" or "buildkite.com" in r["url"] for r in refs):
            rep.warn(f"{where} is a deploy with no pull request or build ref; the reader cannot see what shipped")
        if t.get("key") is not None and not isinstance(t["key"], bool):
            rep.err(f"{where}.key must be true or false")
    flagged = [t for t in items if t.get("key") is True]
    if items and not flagged:
        rep.strict_warn(f"no timeline entry carries key: true; the page opens on the key moments and falls back to all "
                        f"{len(items)} entries until some are flagged")
    elif len(flagged) > KEY_MOMENTS:
        rep.strict_warn(f"{len(flagged)} timeline entries carry key: true; the opening view stays readable at "
                        f"{KEY_MOMENTS} or fewer, and the rest live behind the full list")
    return ids


def check_impact(rep, R, known: set):
    impact = R.get("impact")
    if impact is None:
        return
    if not isinstance(impact, dict):
        rep.err("impact must be an object {text, p, teams, metrics}")
        return
    teams = impact.get("teams")
    if teams is not None:
        if not isinstance(teams, list):
            rep.err("impact.teams must be a list of {codename, text}")
        else:
            for i, t in enumerate(teams):
                if not (isinstance(t, dict) and isinstance(t.get("codename"), str) and t["codename"].strip()):
                    rep.err(f"impact.teams[{i}] must carry a codename")
    metrics = impact.get("metrics")
    if metrics is None:
        return
    if not isinstance(metrics, list):
        rep.err("impact.metrics must be a list")
        return
    for i, m in enumerate(metrics):
        where = f"impact.metrics[{i}]"
        if not isinstance(m, dict):
            rep.err(f"{where} is not an object")
            continue
        for extra in sorted(set(m) - METRIC_FIELDS):
            rep.warn(f"{where} carries {extra!r}, which the page ignores")
        for key in ("label", "value"):
            if not (isinstance(m.get(key), str) and m[key].strip()):
                rep.err(f"{where}.{key} must be a non-empty string")
        if not isinstance(m.get("measured"), bool):
            rep.err(f"{where}.measured must be true or false; every number is measured or tagged estimated")
        cites = m.get("cites")
        if cites is not None:
            if not (isinstance(cites, list) and all(isinstance(c, str) for c in cites)):
                rep.err(f"{where}.cites must be a list of ids")
            else:
                for c in cites:
                    if c not in known:
                        rep.err(f"{where} cites {c}, which no register defines")


def check_causes(rep, R, known: set, sub_ids: set, status: str, slack_snapshots: set) -> set:
    causes = R.get("causes")
    if causes is None:
        return set()
    if not isinstance(causes, list):
        rep.err("causes must be a list")
        return set()
    items = [c for c in causes if isinstance(c, dict)]
    ids = check_ids(rep, items, r"C\d+", "causes")
    roots = {}
    for c in items:
        cid = c.get("id")
        if c.get("kind") not in CAUSE_KINDS:
            rep.err(f"{cid}: kind {c.get('kind')!r} not in {', '.join(CAUSE_KINDS)}")
        if not (isinstance(c.get("t"), str) and c["t"].strip()):
            rep.err(f"{cid}.t is missing or empty")
        elif words(c["t"]) > TITLE_WORDS:
            rep.warn(f"{cid}.t is {words(c['t'])} words; a cause title is a noun phrase of {TITLE_WORDS} words or fewer")
        statement = c.get("p") or first_sentence(c.get("text") or "")
        if statement and words(statement) > STATEMENT_WORDS:
            rep.strict_warn(f"{cid} opens on a {words(statement)}-word statement; the chain reads as one line of "
                            f"{STATEMENT_WORDS} words or fewer, with the rest behind the disclosure")
        if isinstance(c.get("text"), str) and words(prose_only(c["text"])) > CAUSE_BODY_WORDS:
            rep.strict_warn(f"{cid}.text is {words(prose_only(c['text']))} words; a cause argues itself in "
                            f"{CAUSE_BODY_WORDS}, and the rest is evidence to cite rather than prose to write")
        if c.get("incident") is not None and c["incident"] not in sub_ids:
            rep.err(f"{cid}: incident {c['incident']!r} is not in meta.subIncidents")
        if c.get("identifiedAt") is not None:
            try:
                parse_ts(c["identifiedAt"])
            except ValueError as e:
                rep.err(f"{cid}.identifiedAt {c['identifiedAt']!r} {e}")
        if c.get("kind") == "root":
            roots[c.get("incident")] = roots.get(c.get("incident"), 0) + 1
        evidence = c.get("evidence")
        if evidence is not None:
            if not isinstance(evidence, list):
                rep.err(f"{cid}.evidence must be a list of URLs and ids")
            else:
                for e in evidence:
                    if not isinstance(e, str):
                        rep.err(f"{cid}.evidence entry {e!r} is neither a URL nor an id")
                    elif e.startswith("https://"):
                        if ".slack.com/" in e and e not in slack_snapshots:
                            rep.warn(f"{cid}.evidence cites the Slack message {e} with no snapshot under evidence/slack")
                    elif e not in known:
                        rep.err(f"{cid}.evidence cites {e}, which no register defines")
        code = c.get("code")
        if code is not None:
            if not isinstance(code, dict):
                rep.err(f"{cid}.code must be {{lang, source, caption?}}")
            else:
                for key in ("lang", "source"):
                    if not (isinstance(code.get(key), str) and code[key].strip()):
                        rep.err(f"{cid}.code.{key} must be a non-empty string")
        check_link_list(rep, str(cid), c, False)
    if STATUS_RANK.get(status, 0) >= STATUS_RANK["reviewed"]:
        scopes = sorted(sub_ids) or [None]
        for scope in scopes:
            if not roots.get(scope) and not (scope is not None and roots.get(None)):
                label = f"incident {scope}" if scope else "the retro"
                rep.strict_warn(f"{label} names no root cause; a reviewed retro carries at least one cause with kind root")
    return ids


def check_actions(rep, R, cause_ids: set, status: str) -> set:
    actions = R.get("actions")
    if actions is None:
        return set()
    if not isinstance(actions, list):
        rep.err("actions must be a list")
        return set()
    items = [a for a in actions if isinstance(a, dict)]
    ids = check_ids(rep, items, r"AI\d+", "actions")
    for a in items:
        aid = a.get("id")
        if not (isinstance(a.get("t"), str) and a["t"].strip()):
            rep.err(f"{aid}.t is missing or empty")
        elif words(a["t"]) > ACTION_TITLE_WORDS:
            rep.warn(f"{aid}.t is {words(a['t'])} words; an action reads as one line of {ACTION_TITLE_WORDS} words or fewer")
        state = a.get("state")
        if state not in ACTION_STATES:
            rep.err(f"{aid}: state {state!r} not in {', '.join(ACTION_STATES)}")
        owner = a.get("owner")
        if owner is None:
            rep.strict_warn(f"{aid} has no owner; an action nobody owns does not land")
        elif not (isinstance(owner, str) and owner.strip() and "@" not in owner):
            rep.err(f"{aid}.owner must be a person's or team's name")
        source = a.get("source")
        if source is None:
            rep.strict_warn(f"{aid} has no source; name the cause it answers, or lessons or review")
        elif source not in ACTION_SOURCES and source not in cause_ids:
            rep.err(f"{aid}: source {source!r} is neither a cause id nor one of {', '.join(ACTION_SOURCES)}")
        due = a.get("due")
        if due is not None and not is_date(due):
            rep.err(f"{aid}.due {due!r} is not a calendar date in YYYY-MM-DD")
        note = a.get("note")
        if note is not None and not (isinstance(note, str) and note.strip()):
            rep.err(f"{aid}.note must be a non-empty string")
        check_history(rep, str(aid), a, "state", ACTION_STATES, state)
        links = check_link_list(rep, str(aid), a, True)
        if state == "done" and not links:
            rep.warn(f"{aid} is done but links nothing; the change that landed it belongs here")
        if status == "resolved" and state in ACTION_OPEN:
            rep.strict_warn(f"{aid} is still {state} but meta.status is resolved; a closed-out retro has every action done or dropped")
    return ids


def check_resolution(rep, R):
    for key, label in (("resolution", "resolution"), ("detection", "detection")):
        block = R.get(key)
        if block is None:
            continue
        if not isinstance(block, dict):
            rep.err(f"{key} must be an object {{text, p, …}}")
            continue
        if key == "resolution":
            check_link_list(rep, "resolution", block, False)
    for i, m in enumerate(monitors_of(R)):
        where = f"detection.monitors[{i}]"
        if not isinstance(m.get("id"), int) or isinstance(m.get("id"), bool):
            rep.err(f"{where}.id must be the integer monitor id")
        if m.get("role") not in MONITOR_ROLES:
            rep.err(f"{where}.role {m.get('role')!r} not in {', '.join(MONITOR_ROLES)}")
        for key in ("fired", "recovered"):
            if m.get(key) is not None:
                try:
                    parse_ts(m[key])
                except ValueError as e:
                    rep.err(f"{where}.{key} {m[key]!r} {e}")


class SummaryPanels(HTMLParser):
    """The deck contract: one xs-head, one xs-points list, an optional xs-stats block."""

    HEADINGS = ("h1", "h2", "h3", "h4")
    BLOCKS = ("p", "li", "div", "figcaption")
    SPACED = ("span", "b", "strong", "em", "br", "small", "code")

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.panels, self.depth, self.buf, self.into, self.zones = [], 0, [], None, []

    def _zone(self):
        return self.zones[-1][1] if self.zones else None

    def _flush(self):
        text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        self.buf, into = [], self.into
        self.into = None
        if not text:
            return
        panel = self.panels[-1]
        if into == "head":
            panel["heading"] = text
            return
        zone = self._zone()
        if zone == "stats":
            panel["stats"].append(text)
        elif zone == "points" and into == "point":
            panel["points"].append(text)
        else:
            panel["stray"].append(text)

    def handle_starttag(self, tag, attrs):
        if not self.depth:
            if tag == "section" and "xs-panel" in classes_of(attrs):
                self.depth, self.zones = 1, []
                self.panels.append({"kind": dict(attrs).get("data-kind"), "heading": "", "headTag": "",
                                    "headClasses": set(), "points": [], "stats": [], "stray": [], "figure": False})
            return
        self.depth += 1
        classes = classes_of(attrs)
        if "xs-stats" in classes:
            self.zones.append((self.depth, "stats"))
        elif tag == "ul" and "xs-points" in classes:
            self.zones.append((self.depth, "points"))
        if tag in self.HEADINGS:
            self._flush()
            self.into = "head"
            self.panels[-1]["headTag"] = tag
            self.panels[-1]["headClasses"] = classes
        elif tag in self.BLOCKS:
            self._flush()
            self.into = "point" if tag == "li" else "block"
        elif tag in self.SPACED:
            self.buf.append(" ")
        elif tag == "figure":
            self.panels[-1]["figure"] = True

    def handle_endtag(self, tag):
        if not self.depth:
            return
        if tag in self.HEADINGS or tag in self.BLOCKS:
            self._flush()
        if self.zones and self.zones[-1][0] == self.depth:
            self.zones.pop()
        self.depth -= 1
        if not self.depth:
            self._flush()

    def handle_data(self, data):
        if self.depth:
            self.buf.append(data)


def summary_panels(html: str) -> list:
    parser = SummaryPanels()
    parser.feed(html)
    parser.close()
    for panel in parser.panels:
        panel["text"] = " ".join([panel["heading"]] + panel["points"] + panel["stray"]).strip()
    return parser.panels


def summary_markdown(html: str) -> list:
    out = []
    for panel in summary_panels(html):
        out += ["", f"### {panel['heading'] or SUMMARY_KIND_TITLES.get(panel['kind'], 'Summary')}", ""]
        out += [f"- {point}" for point in panel["points"]]
        out += panel["stray"]
        if panel["stats"]:
            out.append(" · ".join(panel["stats"]))
    return out[1:] if out else []


def check_summary(rep, root: Path, known: set, status: str, title: str = ""):
    path = root / SUMMARY_PAGE
    if not path.exists():
        rep.strict_warn(f"{SUMMARY_PAGE} is missing; the retro opens without an executive summary")
        return
    fragment = path.read_text()
    found = SUMMARY_PAGE_TAG.search(fragment)
    if found:
        rep.err(f"{SUMMARY_PAGE} carries a <{found.group(1)}> tag; it is a body-level fragment, not a page")
    found = SUMMARY_EMBED_TAG.search(fragment)
    if found:
        rep.err(f"{SUMMARY_PAGE} carries a <{found.group(1)}> tag; the summary is prose and figures, nothing executable")
    if SUMMARY_EVENT_ATTR.search(fragment):
        rep.err(f"{SUMMARY_PAGE} carries an inline event handler; the renderer strips it, so the behavior is dead")
    for url in SUMMARY_URL_ATTR.findall(fragment):
        scheme = foreign_scheme(url)
        if scheme:
            rep.err(f"{SUMMARY_PAGE} links {url} over {scheme}:; link over http(s) or a relative path")
    if "TODO" in fragment:
        rep.strict_warn(f"{SUMMARY_PAGE} still carries a TODO; the summary is the one part written by hand")
    panels = summary_panels(fragment)
    if not panels:
        rep.strict_warn(f"{SUMMARY_PAGE} declares no <section class=\"xs-panel\">; the summary is one panel per "
                        f"question ({', '.join(SUMMARY_KINDS)})")
        return
    kinds = [p["kind"] for p in panels]
    for kind in kinds:
        if kind not in SUMMARY_KINDS:
            rep.err(f"{SUMMARY_PAGE} has a panel with data-kind {kind!r}; the questions are {', '.join(SUMMARY_KINDS)}")
    for kind in set(kinds):
        if kind and kinds.count(kind) > 1:
            rep.err(f"{SUMMARY_PAGE} answers {kind!r} {kinds.count(kind)} times; each question is answered once")
    if kinds and kinds[0] != SUMMARY_KINDS[0]:
        rep.strict_warn(f"{SUMMARY_PAGE} opens on {kinds[0]!r}; the summary opens on {SUMMARY_KINDS[0]!r}")
    ordered = [k for k in SUMMARY_KINDS if k in kinds]
    if [k for k in kinds if k in SUMMARY_KINDS] != ordered:
        rep.strict_warn(f"{SUMMARY_PAGE} orders its panels {', '.join(str(k) for k in kinds)}; the reading order is "
                        f"{', '.join(SUMMARY_KINDS)}")
    if STATUS_RANK.get(status, 0) >= STATUS_RANK["reviewed"]:
        for missing in [k for k in SUMMARY_KINDS if k not in kinds]:
            rep.strict_warn(f"{SUMMARY_PAGE} never answers {missing!r}; a reviewed retro answers every question")
    for panel in panels:
        where = f"{SUMMARY_PAGE} panel {panel['kind'] or 'with no data-kind'}"
        if not panel["heading"]:
            rep.strict_warn(f"{where} carries no heading; the panel is one h3.xs-head answer over its points")
        else:
            if panel["headTag"] != "h3" or "xs-head" not in panel["headClasses"]:
                rep.err(f"{where} heads on <{panel['headTag']}> with no xs-head class; the deck reads one "
                        f"<h3 class=\"xs-head\"> per panel")
            if words(panel["heading"]) > XS_HEAD_WORDS:
                rep.strict_warn(f"{where} heads on {words(panel['heading'])} words; a headline lands in "
                                f"{XS_HEAD_WORDS} or fewer, and the detail moves into the points")
            elif words(panel["heading"]) < 4:
                rep.strict_warn(f"{where} heads on {panel['heading']!r}, which reads as a label; the heading is the answer")
            elif panel is panels[0] and title and overlap(panel["heading"], title) >= TITLE_ECHO:
                rep.strict_warn(f"{where} heads on {panel['heading']!r}, which restates meta.title a few lines above it; "
                                f"the first panel carries the reader forward from the headline, never back over it")
        if not panel["points"]:
            rep.strict_warn(f"{where} carries no <ul class=\"xs-points\">; the heading is the answer and the points "
                            f"are the evidence for it")
        elif len(panel["points"]) > XS_POINTS:
            rep.strict_warn(f"{where} carries {len(panel['points'])} points; a panel a reader scans carries "
                            f"{XS_POINTS} or fewer")
        for point in panel["points"]:
            if words(point) > XS_POINT_WORDS:
                rep.strict_warn(f"{where} carries a {words(point)}-word point; each reads as one line of "
                                f"{XS_POINT_WORDS} words or fewer")
        for stray in panel["stray"]:
            rep.strict_warn(f"{where} carries prose outside its points: {first_line(stray, 60)!r}; the panel is a "
                            f"headline, its points, and optionally a .xs-stats block")
        for cited in ID_TOKEN.findall(panel["text"]):
            if cited not in known:
                rep.warn(f"{where} cites {cited}, which no register defines")


def first_sentence(text: str) -> str:
    stripped = prose_only(text).strip()
    return SENTENCE_END.split(stripped, 1)[0].strip() if stripped else ""


def check_decisions(rep, R, known: set) -> set:
    items = entries(R, "decisions")
    if R.get("decisions") is not None and not isinstance(R["decisions"], list):
        rep.err("decisions must be a list of {id, t, h, who, when, why}")
        return set()
    ids = check_ids(rep, items, r"D\d+", "decisions")
    for d in items:
        did = d.get("id")
        if not (isinstance(d.get("t"), str) and d["t"].strip()):
            rep.err(f"{did}.t is missing or empty")
        elif words(d["t"]) > DECISION_TITLE_WORDS:
            rep.warn(f"{did}.t is {words(d['t'])} words; a decision reads as one line of {DECISION_TITLE_WORDS} or fewer")
        for key in ("who", "why"):
            if not (isinstance(d.get(key), str) and d[key].strip()):
                rep.err(f"{did}.{key} is missing; a decision records who made it and why")
        if isinstance(d.get("why"), str) and words(prose_only(d["why"])) > DECISION_BODY_WORDS:
            rep.strict_warn(f"{did}.why is {words(prose_only(d['why']))} words; the reasoning recorded at the time "
                            f"fits {DECISION_BODY_WORDS}")
        if "@" in str(d.get("who", "")):
            rep.err(f"{did}.who names {d['who']!r}; people are named, never addressed")
        when = d.get("when")
        if when is None:
            rep.strict_warn(f"{did} has no 'when'; a decision during the response carries the moment it was made")
        else:
            try:
                parse_ts(when)
            except ValueError as e:
                rep.err(f"{did}.when {when!r} {e}")
        for cited in d.get("refs") or []:
            if isinstance(cited, str) and not cited.startswith("https://") and cited not in known:
                rep.err(f"{did}.refs cites {cited}, which no register defines")
        check_link_list(rep, str(did), d, False)
    return ids


def check_hypotheses(rep, R, known: set) -> set:
    items = entries(R, "hypotheses")
    if R.get("hypotheses") is not None and not isinstance(R["hypotheses"], list):
        rep.err("hypotheses must be a list of {id, t, h, status, exonerated?}")
        return set()
    ids = check_ids(rep, items, r"H\d+", "hypotheses")
    for hyp in items:
        hid = hyp.get("id")
        if not (isinstance(hyp.get("t"), str) and hyp["t"].strip()):
            rep.err(f"{hid}.t is missing or empty")
        if hyp.get("status") not in HYPOTHESIS_STATES:
            rep.err(f"{hid}.status {hyp.get('status')!r} not in {', '.join(HYPOTHESIS_STATES)}")
        if hyp.get("status") == "ruled-out" and not (isinstance(hyp.get("exonerated"), str) and hyp["exonerated"].strip()):
            rep.err(f"{hid} is ruled out with no 'exonerated'; record what cleared it")
        check_history(rep, str(hid), hyp, "status", HYPOTHESIS_STATES, hyp.get("status"))
        for cited in hyp.get("evidence") or []:
            if isinstance(cited, str) and not cited.startswith("https://") and cited not in known:
                rep.err(f"{hid}.evidence cites {cited}, which no register defines")
    return ids


def check_recognize(rep, R):
    items = R.get("recognize")
    if items is None:
        return
    if not isinstance(items, list):
        rep.err("recognize must be a list of {signal, means, do}")
        return
    for i, row in enumerate(items):
        where = f"recognize[{i}]"
        if not isinstance(row, dict):
            rep.err(f"{where} must be {{signal, means, do}}")
            continue
        for key in ("signal", "means", "do"):
            value = row.get(key)
            if not (isinstance(value, str) and value.strip()):
                rep.err(f"{where}.{key} is missing or empty")
            elif words(value) > RECOGNIZE_WORDS:
                rep.strict_warn(f"{where}.{key} is {words(value)} words; each column is {RECOGNIZE_WORDS} or fewer")


def check_unknowns(rep, R, known: set) -> set:
    items = entries(R, "unknowns")
    if R.get("unknowns") is not None and not isinstance(R["unknowns"], list):
        rep.err("unknowns must be a list of {id, q, h, why?, owner?}")
        return set()
    ids = check_ids(rep, items, r"U\d+", "unknowns")
    for u in items:
        uid = u.get("id")
        if not (isinstance(u.get("q"), str) and u["q"].strip()):
            rep.err(f"{uid}.q is missing; an unknown is written as the question nobody answered")
        elif words(u["q"]) > UNKNOWN_WORDS:
            rep.strict_warn(f"{uid}.q is {words(u['q'])} words; an open question is {UNKNOWN_WORDS} words or fewer")
        if isinstance(u.get("why"), str) and words(prose_only(u["why"])) > UNKNOWN_BODY_WORDS:
            rep.strict_warn(f"{uid}.why is {words(prose_only(u['why']))} words; why a question stayed open fits "
                            f"{UNKNOWN_BODY_WORDS}")
        if "@" in str(u.get("owner", "")):
            rep.err(f"{uid}.owner names {u['owner']!r}; people are named, never addressed")
        for cited in u.get("refs") or []:
            if isinstance(cited, str) and not cited.startswith("https://") and cited not in known:
                rep.err(f"{uid}.refs cites {cited}, which no register defines")
    return ids


def check_glossary(rep, R):
    items = R.get("glossary")
    if items is None:
        return
    if not isinstance(items, list):
        rep.err("glossary must be a list of {term, def}")
        return
    seen = set()
    for i, row in enumerate(items):
        where = f"glossary[{i}]"
        if not isinstance(row, dict):
            rep.err(f"{where} must be {{term, def}}")
            continue
        term = row.get("term")
        if not (isinstance(term, str) and term.strip()):
            rep.err(f"{where}.term is missing or empty")
        elif term.lower() in seen:
            rep.err(f"{where} defines {term!r} twice")
        else:
            seen.add(term.lower())
        meaning = row.get("def")
        if not (isinstance(meaning, str) and meaning.strip()):
            rep.err(f"{where}.def is missing or empty")
        elif words(meaning) > GLOSSARY_WORDS:
            rep.strict_warn(f"{where}.def is {words(meaning)} words; a definition is {GLOSSARY_WORDS} or fewer")


def check_lessons(rep, R):
    lessons = R.get("lessons")
    if lessons is None:
        return
    if not isinstance(lessons, dict):
        rep.err("lessons must be an object {well, wrong, lucky}")
        return
    for extra in sorted(set(lessons) - {k for k, _ in LESSON_COLUMNS}):
        rep.err(f"lessons.{extra} is not one of well, wrong, lucky")
    for key, _ in LESSON_COLUMNS:
        column = lessons.get(key)
        if column is None:
            continue
        if not isinstance(column, list):
            rep.err(f"lessons.{key} must be a list of {{text}}")
            continue
        for i, e in enumerate(column):
            if not (isinstance(e, dict) and isinstance(e.get("text"), str) and e["text"].strip()):
                rep.err(f"lessons.{key}[{i}] must be {{text: \"…\"}}")
            elif words(e["text"]) > LESSON_WORDS:
                rep.strict_warn(f"lessons.{key}[{i}] is {words(e['text'])} words; a lesson is one line of {LESSON_WORDS} or fewer")


def snapshot_json(rep, root: Path, where: str, rel: str):
    if not isinstance(rel, str) or not rel.strip():
        rep.err(f"{where}.file must be a path under evidence/")
        return None
    path = root / rel
    if not rel.startswith("evidence/") or ".." in Path(rel).parts:
        rep.err(f"{where}.file {rel!r} is not under evidence/")
        return None
    if not path.is_file():
        rep.err(f"{where}.file {rel} does not exist")
        return None
    if path.suffix != ".json":
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        rep.err(f"{rel} does not parse: {e}")
        return None
    if not isinstance(data, dict):
        rep.err(f"{rel} must be a JSON object")
        return None
    return data


def objects_only(rep, holder: dict, key: str, where: str) -> list:
    values = holder.get(key)
    if values is None:
        return []
    if not isinstance(values, list):
        rep.err(f"{where} must be a list")
        holder[key] = []
        return []
    for i, v in enumerate(values):
        if not isinstance(v, dict):
            rep.err(f"{where}[{i}] is not an object")
    holder[key] = [v for v in values if isinstance(v, dict)]
    return holder[key]


def check_shapes(rep, R) -> dict:
    evidence = R.get("evidence")
    if evidence is None:
        evidence = {}
    elif not isinstance(evidence, dict):
        rep.err("evidence must be an object keyed by kind")
        evidence = R["evidence"] = {}
    for extra in sorted(set(evidence) - set(EVIDENCE_KINDS)):
        rep.err(f"evidence.{extra} is not one of {', '.join(EVIDENCE_KINDS)}")
    lists = {kind: objects_only(rep, evidence, kind, f"evidence.{kind}") for kind in EVIDENCE_KINDS}
    detection = R.get("detection")
    if isinstance(detection, dict):
        objects_only(rep, detection, "monitors", "detection.monitors")
    return lists


def check_evidence_register(rep, R, root: Path, ts: dict, known: set, lists: dict) -> set:
    onset, resolved = ts.get("onset"), ts.get("resolved")
    for kind in ("notebooks", "monitors"):
        for i, e in enumerate(lists[kind]):
            where = f"evidence.{kind}[{i}]"
            if not isinstance(e.get("id"), int) or isinstance(e.get("id"), bool):
                rep.err(f"{where}.id must be the integer Datadog id")
            if not (isinstance(e.get("url"), str) and e["url"].startswith("https://")):
                rep.err(f"{where}.url must be an https:// URL")
            if e.get("file") is None:
                rep.warn(f"{where} has no snapshot file; run retro.py evidence fetch --{kind[:-1]} {e.get('id')}")
                continue
            data = snapshot_json(rep, root, where, e["file"])
            if not data:
                continue
            if data.get("schema") != SNAPSHOT_SCHEMA[kind]:
                rep.err(f"{e['file']}: schema is {data.get('schema')!r}, not {SNAPSHOT_SCHEMA[kind]!r}")
            if data.get("id") != e.get("id"):
                rep.err(f"{e['file']}: id {data.get('id')!r} does not match {where}.id {e.get('id')!r}")
            if kind == "notebooks":
                window = data.get("time") if isinstance(data.get("time"), dict) else {}
                if window.get("live") is True:
                    rep.warn(f"{e['file']} was fetched from a live notebook; set an absolute window in Datadog and refetch")
                start, end = try_ts(window.get("start")), try_ts(window.get("end"))
                if onset and resolved and start and end and (start > onset or end < resolved):
                    rep.warn(f"{e['file']} covers {start.isoformat()} → {end.isoformat()}, which does not span onset → resolved")
    slack_urls = set()
    for i, e in enumerate(lists["slack"]):
        where = f"evidence.slack[{i}]"
        url = e.get("url")
        if not (isinstance(url, str) and SLACK_PERMALINK.match(url)):
            rep.err(f"{where}.url must be a Slack message permalink (https://<workspace>.slack.com/archives/C…/p…)")
            continue
        slack_urls.add(url)
        if e.get("file") is None:
            rep.warn(f"{where} has no snapshot file; the page cannot quote {url}")
            continue
        data = snapshot_json(rep, root, where, e["file"])
        if not data:
            continue
        if data.get("schema") != SNAPSHOT_SCHEMA["slack"]:
            rep.err(f"{e['file']}: schema is {data.get('schema')!r}, not {SNAPSHOT_SCHEMA['slack']!r}")
        if data.get("permalink") != url:
            rep.err(f"{e['file']}: permalink does not match {where}.url")
        if not (isinstance(data.get("messages"), list) and data["messages"]):
            rep.err(f"{e['file']}: messages must be a non-empty list")
    for i, e in enumerate(lists["images"]):
        where = f"evidence.images[{i}]"
        snapshot_json(rep, root, where, e.get("file"))
        if not (isinstance(e.get("alt"), str) and e["alt"].strip()):
            rep.err(f"{where}.alt is missing; the alt text is what print and the export carry")
        for c in e.get("cites") or []:
            if c not in known:
                rep.err(f"{where} cites {c}, which no register defines")
    for i, e in enumerate(lists["prs"]):
        where = f"evidence.prs[{i}]"
        norm, problem = normalise_link({k: v for k, v in e.items() if k in ("url", "label")})
        if problem:
            rep.err(f"{where}: {problem}")
        elif norm["kind"] != "pr":
            rep.err(f"{where}.url is not a GitHub pull request URL")
        if e.get("role") not in PR_ROLES:
            rep.err(f"{where}.role {e.get('role')!r} not in {', '.join(PR_ROLES)}")
    for kind in ("sentry", "linear", "builds", "runs", "docs"):
        for i, e in enumerate(lists[kind]):
            where = f"evidence.{kind}[{i}]"
            url = e.get("url")
            if url is not None and not (isinstance(url, str) and url.startswith("https://")):
                rep.err(f"{where}.url must be an https:// URL")
            if kind != "runs" and url is None:
                rep.err(f"{where}.url is missing")
            if kind == "linear" and not (isinstance(e.get("key"), str) and re.fullmatch(r"[A-Z][A-Z0-9]+-\d+", e["key"])):
                rep.err(f"{where}.key must read like ENG-123")
            if kind == "runs" and not (isinstance(e.get("id"), str) and e["id"].strip()):
                rep.err(f"{where}.id must be the run id string")
    monitor_files = {m.get("id"): m.get("file") for m in lists["monitors"]}
    for i, m in enumerate(monitors_of(R)):
        if m.get("file") is None and monitor_files.get(m.get("id")) is None:
            rep.warn(f"detection.monitors[{i}] ({m.get('id')}) has no snapshot file; run retro.py evidence fetch --monitor {m.get('id')}")
        elif isinstance(m.get("file"), str):
            snapshot_json(rep, root, f"detection.monitors[{i}]", m["file"])
    return slack_urls


def slack_permalinks_in(root: Path, R: dict) -> set:
    urls = set()
    for e in entries(R.get("evidence") or {}, "slack"):
        if isinstance(e.get("url"), str):
            urls.add(e["url"])
    folder = root / "evidence" / "slack"
    if folder.is_dir():
        for p in folder.glob("*.json"):
            try:
                data = json.loads(p.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and isinstance(data.get("permalink"), str):
                urls.add(data["permalink"])
    return urls


def check_citations(rep, R, known: set):
    fn_ids = check_ids_by(rep, entries(R, "footnotes"), "n", r"\d+", "footnotes")
    fn_nums = {int(n) for n in fn_ids if str(n).isdigit()}
    used_fns = set()
    for where, text in prose_fields(R):
        for cited in sorted(set(ID_TOKEN.findall(prose_only(text))) - known):
            rep.err(f"{where} cites {cited}, which no register defines")
        used_fns.update(int(n) for n in FN_TOKEN.findall(text))
    for n in sorted(used_fns - fn_nums):
        rep.err(f"footnote token [^{n}] has no footnotes entry")
    for n in sorted(fn_nums - used_fns):
        rep.warn(f"footnote {n} is never referenced")
    for i, e in enumerate(entries(R, "notes")):
        if not (isinstance(e.get("t"), str) and e["t"].strip()):
            rep.err(f"notes[{i}].t is missing or empty")
        if not (isinstance(e.get("md"), str) and e["md"].strip()):
            rep.err(f"notes[{i}].md is missing or empty")


def check_ids_by(rep, items, key, pattern, label) -> set:
    seen = set()
    for e in items:
        i = e.get(key)
        if i is None:
            rep.err(f"{label}: entry missing '{key}'")
            continue
        if i in seen:
            rep.err(f"{label}: duplicate {key} {i}")
        seen.add(i)
        if not re.fullmatch(pattern, str(i)):
            rep.err(f"{label}: {key} {i!r} does not match {pattern}")
    return seen


def reconsidered(root: Path, where: str) -> bool:
    """True when the plain twin was written through the prose lane no earlier than its wording."""
    writer = sibling_module("retro_prose")
    if writer is None:
        return False
    fields = writer.load_lock(root)["fields"]
    twin, wording = fields.get(f"{where}.p"), fields.get(f"{where}.text")
    return bool(twin and wording and twin.get("at", "") >= wording.get("at", ""))


def short_named(R: dict):
    for reg, _ in HANDLED:
        for e in entries(R, reg):
            yield str(e.get("id")), e
    for s in (R.get("meta") or {}).get("subIncidents") or []:
        if isinstance(s, dict):
            yield str(s.get("id")), s
    for i, t in enumerate(entries(R, "timeline")):
        yield str(t.get("id") or f"timeline[{i}]"), t
    for kind in EVIDENCE_NAMED:
        for i, e in enumerate((R.get("evidence") or {}).get(kind) or []):
            if isinstance(e, dict):
                yield f"evidence.{kind}[{i}]", e


def check_handles_and_twins(rep, R, root: Path, known: set):
    ids = id_matcher(known)
    cited = set()
    for _, text in prose_fields(R):
        cited.update(ids.findall(prose_only(text)))
    for c in entries(R, "causes"):
        cited.update(e for e in c.get("evidence") or [] if isinstance(e, str))
    for ident, e in short_named(R):
        h = e.get("h")
        if h is None or (isinstance(h, str) and not h.strip()):
            rep.strict_warn(f"{ident} has no short name h; write the phrase of {HANDLE_WORDS} words or fewer that "
                            f"stands for it in every collapsed view and every citation")
            continue
        if not isinstance(h, str):
            rep.err(f"{ident}.h must be a string")
            continue
        if h.rstrip().endswith("."):
            rep.warn(f"{ident}.h ends with a period; a short name is a phrase, not a sentence")
        if not 1 < words(prose_only(h)) <= HANDLE_WORDS:
            rep.err(f"{ident}.h is {words(prose_only(h))} words; a short name reads at a glance in two to "
                    f"{HANDLE_WORDS}")
        named = sorted(set(ids.findall(prose_only(h))))
        if named:
            rep.strict_warn(f"{ident}.h names register ids {', '.join(named)}")
        if isinstance(e.get("t"), str):
            named = sorted(set(ids.findall(e["t"])))
            if named:
                rep.strict_warn(f"{ident}.t names register ids {', '.join(named)}; a rendered string names an entry by its wording and leaves the id to the citation")
    for t in entries(R, "timeline"):
        if t.get("id") is not None and str(t["id"]) not in cited:
            rep.warn(f"{t['id']} carries an id nothing cites; a timeline entry gets one only when a cause or lesson points at it")

    twinned = [(f"{key}", R.get(key), what) for key, what in TWINNED]
    twinned += [(str(c.get("id")), c, "the cause") for c in entries(R, "causes")]
    previous = previous_twins(rep, R, root)
    for where, block, what in twinned:
        if block is None:
            continue
        if not isinstance(block, dict):
            rep.err(f"{where} must be an object with text and a plain twin p")
            continue
        text = block.get("text")
        if not (isinstance(text, str) and text.strip()):
            rep.strict_warn(f"{where}.text is empty; {what} is unwritten")
            continue
        if text.strip() == "TODO":
            rep.strict_warn(f"{where}.text is still the scaffold placeholder")
            continue
        p = block.get("p")
        if p is None or (isinstance(p, str) and not p.strip()):
            rep.strict_warn(f"{where} has no plain twin p; write the version a reader outside the team sees first")
            continue
        if not isinstance(p, str):
            rep.err(f"{where}.p must be a string")
            continue
        for issue in twin_issues(prose_only(p), prose_only(text), ids):
            rep.strict_warn(f"{where}.p {issue}")
        before = previous.get(where)
        if before and before[0] != text and before[1] == p and not reconsidered(root, where):
            rep.err(f"{where}: the wording changed since the last snapshot but the plain twin did not; rewrite p")


def previous_twins(rep, R, root: Path) -> dict:
    rev = (R.get("meta") or {}).get("rev")
    path = root / "history" / f"rev-{rev}.json"
    if not (rep.strict and rev and path.exists()):
        return {}
    try:
        hist = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(hist, dict):
        return {}
    out = {}
    for key, _ in TWINNED:
        block = hist.get(key)
        if isinstance(block, dict):
            out[key] = (block.get("text"), block.get("p"))
    for c in entries(hist, "causes"):
        out[str(c.get("id"))] = (c.get("text"), c.get("p"))
    return out


def check_derived_prose(rep, R):
    for where, text in prose_fields(R):
        for sentence in (s.strip() for s in SENTENCE_END.split(text)):
            if DERIVED_NUMBER.search(sentence) and DERIVED_TOPIC.search(sentence):
                rep.warn(f"{where}: {first_line(sentence, 72)!r} states a duration the tiles derive from timestamps; say what happened and let the numbers come from the data")
                break


def label_sources(R):
    meta = R.get("meta") or {}
    if isinstance(meta.get("title"), str) and meta["title"].strip():
        yield "meta.title", meta["title"], True
    for s in meta.get("subIncidents") or []:
        if isinstance(s, dict):
            for field, sentence in (("t", True), ("h", False)):
                if isinstance(s.get(field), str) and s[field].strip():
                    yield f"{s.get('id')}.{field}", s[field], sentence
    for reg, _ in HANDLED:
        for e in entries(R, reg):
            for field, sentence in (("t", True), ("h", False)):
                if isinstance(e.get(field), str) and e[field].strip():
                    yield f"{e.get('id')}.{field}", e[field], sentence
    for i, m in enumerate((R.get("impact") or {}).get("metrics") or []):
        if isinstance(m, dict) and isinstance(m.get("label"), str) and m["label"].strip():
            yield f"impact.metrics[{i}].label", m["label"], True
    for i, e in enumerate(entries(R, "notes")):
        if isinstance(e.get("t"), str) and e["t"].strip():
            yield f"notes[{i}].t", e["t"], True
    declared = R.get("components")
    if isinstance(declared, dict):
        for cid, spec in sorted(declared.items()):
            if not isinstance(spec, dict):
                continue
            if isinstance(spec.get("title"), str) and spec["title"].strip():
                yield f"components.{cid}.title", spec["title"], True
            for field in ("tabs", "steps", "phases", "inputs", "outputs", "rows", "cols", "columns", "lanes", "tiles", "extra"):
                for i, item in enumerate(spec.get(field) or []):
                    if isinstance(item, dict) and isinstance(item.get("label"), str) and item["label"].strip():
                        yield f"components.{cid}.{field}[{i}].label", item["label"], True


def check_capitalisation(rep, R):
    meta = R.get("meta") or {}
    extra = [a for a in (meta.get("acronyms") or []) if isinstance(a, str)] if isinstance(meta.get("acronyms"), list) else []
    acronyms = acronym_map({**meta, "acronyms": list(RETRO_ACRONYMS) + extra})
    for where, label, sentence_case in label_sources(R):
        check_case(rep, where, label, acronyms, sentence_case)


def component_schemas(rep) -> dict:
    out = {}
    for path in sorted(list(COMPONENT_SCHEMAS.glob("dd.*.json")) + list(COMPONENT_SCHEMAS.glob("ir.*.json"))):
        try:
            out[path.stem] = json.loads(path.read_text())
        except ValueError as e:
            rep.err(f"reference/components/{path.name} does not parse: {e}")
    return out


def check_component_props(rep, label, spec, R, root: Path, known: set, slack_snapshots: set):
    kind = spec["kind"]
    evidence = R.get("evidence") or {}
    if kind == "ir.notebook":
        notebooks = {nb.get("id"): nb for nb in evidence.get("notebooks") or [] if isinstance(nb, dict)}
        nb = notebooks.get(spec["notebook"])
        if nb is None:
            rep.err(f"{label}: notebook {spec['notebook']} is not in evidence.notebooks")
        elif spec.get("cells") is not None and isinstance(nb.get("file"), str) and (root / nb["file"]).is_file():
            try:
                data = json.loads((root / nb["file"]).read_text())
            except (OSError, ValueError):
                data = {}
            indexes = {c.get("index") for c in (data.get("cells") or []) if isinstance(c, dict)}
            for i in spec["cells"]:
                if i not in indexes:
                    rep.err(f"{label}: cells names index {i}, which {nb['file']} does not carry")
    elif kind == "ir.monitor":
        monitors = {m.get("id") for m in evidence.get("monitors") or [] if isinstance(m, dict)}
        monitors |= {m.get("id") for m in (R.get("detection") or {}).get("monitors") or [] if isinstance(m, dict)}
        if spec["monitor"] not in monitors:
            rep.err(f"{label}: monitor {spec['monitor']} is neither in evidence.monitors nor detection.monitors")
    elif kind == "ir.slack-thread":
        if spec["permalink"] not in slack_snapshots:
            rep.err(f"{label}: permalink {spec['permalink']} has no snapshot under evidence/slack")
    elif kind == "ir.windows":
        window_ids = {str(w.get("id")) for w in entries(R, "windows")}
        for w in spec.get("windows") or []:
            if w not in window_ids:
                rep.err(f"{label}: windows names {w}, which is not a window id")
    elif kind == "ir.tiles":
        for i, tile in enumerate(spec.get("tiles") or []):
            for key in ("from", "to"):
                if tile.get(key) not in TIMESTAMP_KEYS:
                    rep.err(f"{label}.tiles[{i}].{key} {tile.get(key)!r} is not a timestamps key")
    elif kind == "ir.timeline":
        for k in spec.get("kinds") or []:
            if k not in TIMELINE_KINDS:
                rep.err(f"{label}: kinds names {k!r}, which is not a timeline kind")
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
    elif kind == "dd.tabs":
        for i, tab in enumerate(spec["tabs"]):
            if not (tab.get("md") or tab.get("figure")):
                rep.err(f"{label}.tabs[{i}] carries neither 'md' nor 'figure'; a pane shows one or both")
    elif kind == "dd.lanes":
        ids = [lane["id"] for lane in spec["lanes"]]
        if ids[0] == ids[1]:
            rep.err(f"{label}: both lanes carry the id {ids[0]!r}")
    elif kind == "dd.flow":
        ids = [c["id"] for c in spec["columns"]]
        for cid in sorted({cid for cid in ids if ids.count(cid) > 1}):
            rep.err(f"{label}: two columns share the id {cid!r}")
        for i, call in enumerate(spec["callouts"]):
            if call["col"] not in ids:
                rep.err(f"{label}.callouts[{i}]: col {call['col']!r} is not a column id ({', '.join(ids)})")
    for cited in spec.get("cites") or []:
        if cited not in known:
            rep.err(f"{label}: cites {cited}, which no register defines")


def component_fields(R):
    for key, where in COMPONENT_HOSTS:
        block = R.get(key)
        if isinstance(block, dict) and block.get("component") is not None:
            yield where, block["component"]
    for reg in COMPONENT_LISTS:
        for i, e in enumerate(entries(R, reg)):
            if e.get("component") is not None:
                yield str(e.get("id") or f"{reg}[{i}]"), e["component"]


def check_components(rep, R, root: Path, known: set, slack_snapshots: set):
    declared = R.get("components")
    if declared is not None and not isinstance(declared, dict):
        rep.err("components must map a component id to its declaration")
        declared = None
    declared = declared or {}
    fields = list(component_fields(R))
    if not (declared or fields):
        return
    schemas = component_schemas(rep)
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
            check_component_props(rep, label, spec, R, root, known, slack_snapshots)
    referenced = set()
    for where, name in fields:
        if name in declared:
            referenced.add(name)
        else:
            rep.err(f"{where}: component {name!r} is not an entry in components")
    for cid in sorted(set(declared) - referenced):
        rep.warn(f"components.{cid} is declared but no register field places it; it renders nowhere")


def masked(term: str) -> str:
    return term[:1] + "…" * (len(term) > 1) + f" ({len(term)} chars)"


def check_forbidden_terms(rep, root: Path, override):
    evidence = sibling_module("retro_evidence")
    if evidence is None:
        rep.warn("scripts/retro_evidence.py is missing, so the forbidden-terms grep it provides did not run; customer names were not checked")
        return
    pattern = evidence.forbidden_terms(override, root)
    if pattern is None:
        rep.warn("no forbidden-terms source (--forbidden-terms, FORBIDDEN_TERMS, or a .customer-names file up the tree); customer names were not checked")
        return
    files = [root / name for name in PROJECT_FILES if (root / name).exists()]
    files += [p for p in evidence_files(root) if p.suffix in EVIDENCE_TEXT]
    for path in files:
        try:
            text = path.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        hits = {}
        for n, line in enumerate(text.splitlines(), 1):
            for m in pattern.finditer(line):
                hits.setdefault(m.group().lower(), []).append(n)
        for term, lines in sorted(hits.items()):
            shown = ", ".join(map(str, lines[:5])) + (", …" if len(lines) > 5 else "")
            rep.err(f"{path.relative_to(root)} names the forbidden term {masked(term)} on line(s) {shown}")


def check_libs(rep):
    template = TEMPLATES / PAGE
    schema = REFERENCE / "schema.md"
    if not template.exists():
        rep.warn(f"templates/{PAGE} is missing; the library pins were not checked")
        return
    pinned = dict(LIB_URL.findall(template.read_text()))
    stated = dict(PINNED_LIB.findall(schema.read_text())) if schema.exists() else {}
    for name, version in sorted(pinned.items()):
        if stated.get(name) != version:
            rep.warn(f"templates/{PAGE} pins {name}@{version} but reference/schema.md states {stated.get(name) or 'no version'}; bump both together")


def check_template_fresh(rep):
    build = SHARED / "build.py"
    template = TEMPLATES / PAGE
    if not (build.exists() and template.exists()):
        return
    result = subprocess.run([sys.executable, str(build), "check"], capture_output=True, text=True, cwd=SHARED.parent.parent)
    if result.returncode == 0:
        return
    stale = [line for line in (result.stdout + result.stderr).splitlines() if "incident-retro" in line]
    for line in stale:
        rep.err(f"templates/{PAGE} is stale against plugins/_shared: {line.strip()}; run python3 plugins/_shared/build.py build")
    if not stale:
        rep.warn(f"plugins/_shared/build.py check failed outside this plugin: {first_line(result.stdout + result.stderr, 120)}")


def check_revisions(rep, R, root: Path):
    meta = R.get("meta") or {}
    if "rev" not in meta and "revisions" not in meta:
        return
    rev = meta.get("rev")
    rev_valid = isinstance(rev, int) and not isinstance(rev, bool) and rev > 0
    if not rev_valid:
        rep.err("meta.rev must be a positive integer")
    revisions = meta.get("revisions")
    if not isinstance(revisions, list) or not revisions:
        rep.err("meta.revisions must be a non-empty list")
        return
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
        if isinstance(revision_note, str) and len(revision_note) > NOTE_LENGTH:
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
    recorded = (current or {}).get("files")
    if isinstance(recorded, dict):
        for key, value in snapshot_digests(root).items():
            if key in recorded and recorded[key] != value:
                rep.strict_warn(f"{key} changed since rev {rev}; run retro.py snapshot")


def check(args) -> int:
    rep = Report(args.strict)
    root = Path(args.dir)
    R = load_retro(root, "check")
    if R is None:
        return 1
    meta = R.get("meta")
    if not isinstance(meta, dict):
        print("ERROR: meta must be an object")
        return 1
    status = meta.get("status")
    provisional = status in PROVISIONAL
    sub_ids = check_meta(rep, R, meta)
    check_live(rep, R, status)
    evidence_lists = check_shapes(rep, R)
    ts = check_timestamps(rep, R, provisional)
    slack_snapshots = slack_permalinks_in(root, R)
    check_windows(rep, R, ts, sub_ids)
    window_ids = {str(w.get("id")) for w in entries(R, "windows")}
    t_ids = check_timeline(rep, R, ts, window_ids, slack_snapshots)
    c_ids = check_causes(rep, R, retro_ids(R), sub_ids, status, slack_snapshots)
    a_ids = check_actions(rep, R, c_ids, status)
    all_ids = retro_ids(R)
    d_ids = check_decisions(rep, R, all_ids)
    h_ids = check_hypotheses(rep, R, all_ids)
    u_ids = check_unknowns(rep, R, all_ids)
    known = window_ids | {str(i) for group in (t_ids, c_ids, a_ids, sub_ids, d_ids, h_ids, u_ids) for i in group}
    check_impact(rep, R, known)
    check_resolution(rep, R)
    check_recognize(rep, R)
    check_glossary(rep, R)
    check_summary(rep, root, known, status, str(meta.get("title") or ""))
    check_lessons(rep, R)
    check_evidence_register(rep, R, root, ts, known, evidence_lists)
    evidence = sibling_module("retro_evidence")
    if evidence is not None:
        evidence.evidence_check(root, rep, R)
    check_citations(rep, R, known)
    check_handles_and_twins(rep, R, root, known)
    writer = sibling_module("retro_prose")
    if writer is not None and status != "ongoing":
        writer.check_lock(sys.modules[__name__], rep, R, root)
    check_derived_prose(rep, R)
    check_capitalisation(rep, R)
    check_components(rep, R, root, known, slack_snapshots)
    check_forbidden_terms(rep, root, args.forbidden_terms)
    check_libs(rep)
    check_template_fresh(rep)
    for folder in (root, root.parent):
        check_ai_config(rep, folder)
    check_revisions(rep, R, root)
    notes = root / "NOTES.md"
    if notes.exists() and "TODO" in notes.read_text():
        rep.strict_warn("NOTES.md still carries a TODO")
    index_path = root / "index.html"
    if index_path.exists() and "GENERATED" not in index_path.read_text():
        rep.strict_warn("index.html carries no GENERATED stamp; it looks copied rather than generated from the current renderer")
    return rep.finish()


def link_rows(R) -> list:
    rows = []
    handles = handles_of(R)
    for a in entries(R, "actions"):
        links = [normalise_link(l)[0] for l in (a.get("links") or [])]
        rows.append({"id": str(a.get("id")), "register": "actions", "kind": "action", "title": a.get("t", ""),
                     "handle": handles.get(str(a.get("id")), ""), "state": a.get("state", ""), "links": [l for l in links if l]})
    for c in entries(R, "causes"):
        links = [normalise_link(l)[0] for l in (c.get("links") or [])]
        if links:
            rows.append({"id": str(c.get("id")), "register": "causes", "kind": c.get("kind", "cause"), "title": c.get("t", ""),
                         "handle": handles.get(str(c.get("id")), ""), "state": "", "links": [l for l in links if l]})
    resolution = R.get("resolution") or {}
    links = [normalise_link(l)[0] for l in (resolution.get("links") or [])] if isinstance(resolution, dict) else []
    if links:
        rows.append({"id": "resolution", "register": "resolution", "kind": "resolution", "title": "Resolution",
                     "handle": "", "state": "", "links": [l for l in links if l]})
    for p in (R.get("evidence") or {}).get("prs") or []:
        if not isinstance(p, dict):
            continue
        norm, _ = normalise_link({k: v for k, v in p.items() if k in ("url", "label")})
        if norm:
            rows.append({"id": "evidence.prs", "register": "evidence", "kind": f"{p.get('role', '')} pr", "title": norm.get("label") or norm["url"],
                         "handle": "", "state": "", "links": [norm]})
    for i, t in enumerate(entries(R, "timeline")):
        links = [normalise_link(l)[0] for l in (t.get("refs") or [])]
        typed = [l for l in links if l and l["kind"] == "pr"]
        if typed:
            rows.append({"id": str(t.get("id") or f"timeline[{i}]"), "register": "timeline", "kind": t.get("kind", "entry"),
                         "title": t.get("text", ""), "handle": "", "state": "", "links": typed})
    return rows


def action_drift(row: dict, states: dict) -> str:
    if row["register"] != "actions":
        return ""
    closers = [(l, states.get(l["url"])) for l in row["links"] if l["closes"]]
    if not closers:
        return ""
    ref = lambda l: f"{l['kind']} {l['gh']['key']}"
    landed = [(l, st) for l, st in closers if st and st["state"] in GITHUB_STATE_CLOSED]
    if row["state"] in ACTION_OPEN and landed:
        l, st = landed[0]
        return f"{row['id']} is still {row['state']} but {ref(l)} was {st['state']} on {st['date']}; set state: \"done\""
    if row["state"] == "done" and all(st and st["state"] not in GITHUB_STATE_CLOSED and st["state"] != "unknown" for _, st in closers):
        l, st = closers[0]
        return f"{row['id']} is marked done but {ref(l)} is still {st['state']}"
    return ""


def links(args) -> int:
    root = Path(args.dir)
    R = load_retro(root, "links")
    if R is None:
        return 1
    rep = Report()
    for a in entries(R, "actions"):
        check_link_list(rep, str(a.get("id")), a, True)
    for c in entries(R, "causes"):
        check_link_list(rep, str(c.get("id")), c, False)
    check_link_list(rep, "resolution", R.get("resolution") or {}, False)
    for m in rep.errors:
        print(f"ERROR: {m}", file=sys.stderr)
    if rep.errors:
        return 1
    repo = (R.get("meta") or {}).get("repo")
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
        rows = [r for r in rows if r["register"] == "actions" and not r["links"]]
    if args.json:
        out = []
        for row in rows:
            out.append({**row, "links": [{**{k: v for k, v in l.items() if k != "gh"}, **({"github": l["gh"]["key"]} if l.get("gh") else {}),
                                          **(states.get(l["url"]) or {})} for l in row["links"]]})
        print(json.dumps(out, indent=1))
        return 0
    width = max([len(str(r["id"])) for r in rows] + [2])
    for row in rows:
        status = f" · {row['state']}" if row["state"] else ""
        print(f"{str(row['id']).ljust(width)}  {row['kind']}{status}  {(row['handle'] or row['title'])[:60]}")
        if not row["links"]:
            print(f"{' ' * width}    no link")
        for link in row["links"]:
            print(f"{' ' * width}    {link_line(link, repo, states.get(link['url']))}")
    if args.fetch:
        for d in (d for d in (action_drift(r, states) for r in link_rows(R)) if d):
            print(f"drift: {d}")
    unlinked = [r["id"] for r in link_rows(R) if r["register"] == "actions" and not r["links"]]
    if unlinked and not args.missing:
        print(f"{len(unlinked)} action(s) carry no link: {', '.join(unlinked)}")
    return 0


def cite_handles(text: str, handles: dict) -> str:
    def swap(m):
        ids = [i.strip() for i in re.split(r"[,;]", m.group(1)) if i.strip()]
        return "(" + ", ".join(handles.get(i, i) for i in ids) + ")"
    return CITE_GROUP.sub(swap, text)


def md_table(header: list, rows: list) -> list:
    if not rows:
        return []
    cells = lambda row: "| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in row) + " |"
    return [cells(header), "|" + "---|" * len(header)] + [cells(r) for r in rows]


def link_text(link) -> str:
    norm, _ = normalise_link(link)
    if not norm:
        return str(link)
    label = norm.get("label") or (norm["gh"]["key"] if norm.get("gh") else norm["url"])
    return f"[{label}]({norm['url']})"


def text_sections(R: dict, root: Path) -> dict:
    meta = R.get("meta") or {}
    tz = zone(meta)
    handles = handles_of(R)
    cite = lambda s: cite_handles(s or "", handles)
    local = lambda v: fmt_local(v, tz)
    out = {}

    lines = []
    fragment = root / SUMMARY_PAGE
    if fragment.exists():
        lines += summary_markdown(fragment.read_text()) + [""]
    tiles = [(d["label"], d["value"], local((R.get("timestamps") or {}).get(d["from"])), local((R.get("timestamps") or {}).get(d["to"])))
             for d in derived_numbers(R.get("timestamps") or {}) if d["value"]]
    lines += md_table(["Tile", "Value", "From", "To"], tiles)
    summary = R.get("summary") or {}
    if summary.get("text"):
        lines += ["", cite(summary["text"])]
    windows = entries(R, "windows")
    if windows:
        lines.append("")
        for w in windows:
            a, b = try_ts(w.get("start")), try_ts(w.get("end"))
            span = fmt_duration((b - a).total_seconds()) if a and b else "?"
            teams = f" · {', '.join(w.get('teams') or [])}" if w.get("teams") else ""
            lines.append(f"- {w.get('h') or w.get('id')} ({w.get('kind')}, {span}): {local(w.get('start'))} → {local(w.get('end'))}{teams}"
                         + (f" — {cite(w['text'])}" if w.get("text") else ""))
    out["overview"] = lines

    rows = []
    for t in entries(R, "timeline"):
        refs = " ".join(link_text(r) for r in t.get("refs") or [])
        event = cite(t.get("text", "")) + (f" {refs}" if refs else "")
        if t.get("window"):
            event += f" [{handles.get(t['window'], t['window'])}]"
        rows.append((local(t.get("ts")), t.get("kind", ""), t.get("actor", ""), event))
    out["timeline"] = md_table(["time", "kind", "actor", "event"], rows)

    impact = R.get("impact") or {}
    lines = [cite(impact["text"])] if impact.get("text") else []
    teams = [(t.get("codename", ""), cite(t.get("text", ""))) for t in impact.get("teams") or [] if isinstance(t, dict)]
    if teams:
        lines += [""] + md_table(["team", "impact"], teams)
    metrics = [(m.get("label", ""), f"{m.get('value', '')} {m.get('unit', '')}".strip(), m.get("delta", ""),
                "measured" if m.get("measured") else "estimated", ", ".join(handles.get(c, c) for c in m.get("cites") or []))
               for m in impact.get("metrics") or [] if isinstance(m, dict)]
    if metrics:
        lines += [""] + md_table(["metric", "value", "over", "kind", "cites"], metrics)
    out["impact"] = lines

    lines = []
    for c in entries(R, "causes"):
        lines.append(f"- **{c.get('t', '')}** ({c.get('kind')}; {c.get('h') or c.get('id')})")
        if c.get("text"):
            lines.append(f"  {cite(c['text'])}")
        evidence = [handles.get(e, e) if not str(e).startswith("https://") else e for e in c.get("evidence") or []]
        if evidence:
            lines.append(f"  Evidence: {', '.join(evidence)}")
        code = c.get("code")
        if isinstance(code, dict) and code.get("source"):
            lines += ["", f"  ```{code.get('lang', '')}"] + ["  " + l for l in code["source"].splitlines()] + ["  ```"]
            if code.get("caption"):
                lines.append(f"  {cite(code['caption'])}")
        if c.get("links"):
            lines.append("  Links: " + ", ".join(link_text(l) for l in c["links"]))
    out["causes"] = lines

    resolution, detection = R.get("resolution") or {}, R.get("detection") or {}
    lines = [cite(resolution["text"])] if resolution.get("text") else []
    if resolution.get("links"):
        lines.append("Links: " + ", ".join(link_text(l) for l in resolution["links"]))
    if detection.get("text"):
        lines += ["", "### Detection", "", cite(detection["text"])]
    onset = try_ts((R.get("timestamps") or {}).get("onset"))
    for m in detection.get("monitors") or []:
        if not isinstance(m, dict):
            continue
        fired = try_ts(m.get("fired"))
        latency = f", fired {fmt_duration((fired - onset).total_seconds())} after onset" if fired and onset else ""
        lines.append(f"- Monitor {m.get('id')} ({m.get('role')}{latency})")
    decisions = entries(R, "decisions")
    if decisions:
        lines += ["", "### Decisions made during the response", ""]
        lines += md_table(["when", "decision", "who", "why"],
                          [(local(d.get("when")), cite(d.get("t", "")), d.get("who", ""), cite(d.get("why", "")))
                           for d in decisions])
    hypotheses = entries(R, "hypotheses")
    if hypotheses:
        lines += ["", "### Hypotheses ruled out", ""]
        lines += md_table(["hypothesis", "verdict", "what settled it"],
                          [(cite(h.get("t", "")), HYPOTHESIS_LABEL.get(h.get("status"), h.get("status", "")),
                            cite(h.get("exonerated", ""))) for h in hypotheses])
    out["resolution"] = lines

    recognize = [r for r in R.get("recognize") or [] if isinstance(r, dict)]
    out["recognize"] = md_table(["signal", "what it means", "what to do"],
                                [(cite(r.get("signal", "")), cite(r.get("means", "")), cite(r.get("do", "")))
                                 for r in recognize]) if recognize else []

    lines = []
    actions = entries(R, "actions")
    for a in actions:
        bits = [f"- {a.get('id')} [{ACTION_LABEL.get(a.get('state'), a.get('state'))}] {a.get('t', '')}"]
        detail = [x for x in (a.get("owner"), handles.get(a.get("source"), a.get("source")) if a.get("source") else None,
                              f"due {a['due']}" if a.get("due") else None) if x]
        if detail:
            bits.append(" · ".join(detail))
        if a.get("links"):
            bits.append(", ".join(link_text(l) for l in a["links"]))
        line = " — ".join(bits)
        if a.get("note"):
            line += f" ({cite(a['note'])})"
        lines.append(line)
    if actions:
        done = sum(1 for a in actions if a.get("state") == "done")
        lines += ["", f"{done} of {len(actions)} done"]
    out["actions"] = lines

    lines = []
    lessons = R.get("lessons") or {}
    for key, label in LESSON_COLUMNS:
        column = [e for e in lessons.get(key) or [] if isinstance(e, dict) and e.get("text")]
        if column:
            lines += ["", f"### {label}", ""] + [f"- {cite(e['text'])}" for e in column]
    out["lessons"] = lines[1:] if lines else []

    lines = []
    evidence = R.get("evidence") or {}
    for kind in EVIDENCE_KINDS:
        items = [e for e in evidence.get(kind) or [] if isinstance(e, dict)]
        if not items:
            continue
        lines += ["", f"### {EVIDENCE_LABEL[kind]}", ""]
        for e in items:
            if kind == "notebooks":
                data = snapshot_data(root, e.get("file"))
                cells = [c.get("title") or c.get("type") for c in data.get("cells") or [] if isinstance(c, dict) and c.get("type") != "markdown"]
                name = data.get("name") or f"Notebook {e.get('id')}"
                lines.append(f"- [{name}]({e.get('url')})" + (f": {'; '.join(cells)}" if cells else ""))
            elif kind == "monitors":
                data = snapshot_data(root, e.get("file"))
                name = data.get("name") or f"Monitor {e.get('id')}"
                lines.append(f"- [{name}]({e.get('url')})" + (f" — `{data['query']}`" if data.get("query") else ""))
            elif kind == "slack":
                data = snapshot_data(root, e.get("file"))
                n = len(data.get("messages") or [])
                lines.append(f"- [#{data.get('channel_name') or 'thread'}]({e.get('url')})" + (f" — {n} message(s)" if n else ""))
            elif kind == "images":
                lines.append(f"- {e.get('file')}: {e.get('alt', '')}" + (f" — {cite(e['caption'])}" if e.get("caption") else ""))
            elif kind == "prs":
                lines.append(f"- {link_text({k: v for k, v in e.items() if k in ('url', 'label')})} ({e.get('role', '')})")
            elif kind == "runs":
                lines.append(f"- {e.get('id')}: {e.get('label', '')}" + (f" ({e['url']})" if e.get("url") else ""))
            else:
                label = e.get("label") or e.get("key") or e.get("url")
                lines.append(f"- [{label}]({e.get('url')})")
    out["evidence"] = lines[1:] if lines else []

    unknowns = entries(R, "unknowns")
    out["unknowns"] = [f"- {cite(u.get('q', ''))}"
                       + (f" — {cite(u['why'])}" if u.get("why") else "")
                       + (f" ({u['owner']})" if u.get("owner") else "") for u in unknowns]

    glossary = [g for g in R.get("glossary") or [] if isinstance(g, dict)]
    out["glossary"] = md_table(["term", "what it means"],
                               [(g.get("term", ""), cite(g.get("def", ""))) for g in glossary]) if glossary else []

    lines = []
    for n in entries(R, "notes"):
        lines += ["", f"### {n.get('t', '')}", "", cite(n.get("md", ""))]
    for f in entries(R, "footnotes"):
        lines.append(f"[^{f.get('n')}]: {cite(f.get('b', ''))}")
    out["notes"] = lines[1:] if lines else []
    return out


def snapshot_data(root: Path, rel) -> dict:
    if not isinstance(rel, str):
        return {}
    try:
        data = json.loads((root / rel).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def text(args) -> int:
    root = Path(args.dir)
    R = load_retro(root, "text")
    if R is None:
        return 1
    if args.section and args.section not in SECTION_IDS:
        print(f"text: --section must be one of {', '.join(SECTION_IDS)}", file=sys.stderr)
        return 1
    meta = R.get("meta") or {}
    sections = text_sections(R, root)
    out = []
    if not args.section:
        out.append(f"# {meta.get('title', '')}")
        if meta.get("subtitle"):
            out.append(meta["subtitle"])
        incident = meta.get("incident") or {}
        dateline = [meta.get("date"), STATUS_LABEL.get(meta.get("status"), meta.get("status")),
                    f"incident #{incident['number']}" if incident.get("number") else None, incident.get("severity"),
                    ", ".join(meta.get("teams") or []) or None]
        out.append(" · ".join(str(x) for x in dateline if x))
        if meta.get("authors"):
            out.append("Authors: " + ", ".join(meta["authors"]) + (f" · Commander: {meta['commander']}" if meta.get("commander") else ""))
    for sid in SECTION_IDS:
        if args.section and sid != args.section:
            continue
        body = sections.get(sid) or []
        if not body:
            continue
        if not args.section:
            out += ["", f"## {SECTION_TITLES[sid]}", ""]
        elif out:
            out.append("")
        out += body
    print("\n".join(out).strip() + "\n")
    return 0


def evidence_missing(args) -> int:
    print("evidence: scripts/retro_evidence.py is missing; the evidence commands (fetch, slack check, slack new) ship with it", file=sys.stderr)
    return 1


def import_missing(args) -> int:
    print("import-gdoc: scripts/retro_import.py is missing; the Google Docs importer ships with it", file=sys.stderr)
    return 1


def prose_missing(args) -> int:
    print("prose: scripts/retro_prose.py is missing; the astra writing lane ships with it", file=sys.stderr)
    return 1


def live_missing(args) -> int:
    print("live: scripts/retro_live.py is missing; the live-incident commands ship with it", file=sys.stderr)
    return 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("scaffold", help="create a fresh directory for one incident retro")
    sc.add_argument("dir")
    sc.add_argument("--title", help=f"the headline, {DOC_TITLE_WORDS} words or fewer")
    sc.add_argument("--subtitle", help="the one sentence stating the causal mechanism (default: the title)")
    sc.add_argument("--tags", help=f"{TAG_COUNT[0]} to {TAG_COUNT[1]} lower-case topical tags, comma separated")
    sc.add_argument("--date", help="date of the writeup, YYYY-MM-DD (default: today)")
    sc.add_argument("--incident", type=int, help="the incident number")
    sc.add_argument("--slug")
    sc.add_argument("--example", action="store_true", help="use the Acme worked example instead of the empty starter")
    sc.set_defaults(fn=scaffold)
    ck = sub.add_parser("check", help="lint retro.json and the evidence beside it")
    ck.add_argument("dir")
    ck.add_argument("--strict", action="store_true", help="treat the warnings a published retro must not carry as errors")
    ck.add_argument("--forbidden-terms", metavar="REGEX", help="customer names the retro and its evidence must not carry (default: FORBIDDEN_TERMS, then the nearest .customer-names)")
    ck.set_defaults(fn=check)
    rc = sub.add_parser("render-check", help="render the retro in headless Chrome and fail on an unmounted component or unrendered cell")
    rc.add_argument("dir")
    rc.add_argument("--timeout", type=float, default=RENDER_TIMEOUT, help="seconds to wait for the page to report ready")
    rc.add_argument("--words", type=int, default=VISIBLE_WORDS, help="words the page may show with every disclosure closed")
    rc.set_defaults(fn=render_check)
    sn = sub.add_parser("snapshot", help="record a revision of retro.json and the evidence digest")
    sn.add_argument("dir", nargs="?", default=".")
    sn.add_argument("--note", default="")
    sn.add_argument("--item", action="append", help="reader-facing bullet describing this revision; repeatable")
    sn.add_argument("--force", action="store_true")
    sn.set_defaults(fn=snapshot)
    lk = sub.add_parser("links", help="list the pull requests and issues each action, cause and the resolution links")
    lk.add_argument("dir")
    lk.add_argument("--fetch", action="store_true", help="resolve GitHub links through GITHUB_TOKEN or the gh CLI and report drift against action states")
    lk.add_argument("--json", action="store_true", help="print the rows as JSON")
    lk.add_argument("--missing", action="store_true", help="print only the actions that carry no link")
    lk.set_defaults(fn=links)
    tx = sub.add_parser("text", help="print the retro as Markdown in reading order")
    tx.add_argument("dir")
    tx.add_argument("--section", help=f"one of {', '.join(SECTION_IDS)}")
    tx.set_defaults(fn=text)
    pd = sub.add_parser("pdf", help=f"print the retro to its {PDF_NAME}")
    pd.add_argument("dir", nargs="?", default=".")
    pd.set_defaults(fn=pdf)
    importer = sibling_module("retro_import")
    if importer is not None:
        importer.add_import_parser(sub)
    else:
        ig = sub.add_parser("import-gdoc", help="turn a Google Docs post-mortem export into a draft retro (scripts/retro_import.py)")
        ig.add_argument("rest", nargs=argparse.REMAINDER)
        ig.set_defaults(fn=import_missing)
    writer = sibling_module("retro_prose")
    if writer is not None:
        writer.add_prose_parser(sub, sys.modules[__name__])
    else:
        pr = sub.add_parser("prose", help="write every authored sentence through gpt-6-astra (scripts/retro_prose.py)")
        pr.add_argument("rest", nargs=argparse.REMAINDER)
        pr.set_defaults(fn=prose_missing)
    updater = sibling_module("retro_live")
    if updater is not None:
        updater.add_live_parser(sub, sys.modules[__name__])
    else:
        lv = sub.add_parser("live", help="keep a retro current while the incident runs (scripts/retro_live.py)")
        lv.add_argument("rest", nargs=argparse.REMAINDER)
        lv.set_defaults(fn=live_missing)
    evidence = sibling_module("retro_evidence")
    if evidence is not None:
        evidence.add_evidence_parsers(sub)
    else:
        ev = sub.add_parser("evidence", help="fetch Datadog snapshots and check or start Slack ones (scripts/retro_evidence.py)")
        ev.add_argument("rest", nargs=argparse.REMAINDER)
        ev.set_defaults(fn=evidence_missing)
    args = ap.parse_args()
    sys.exit((getattr(args, "fn", None) or args.func)(args))


if __name__ == "__main__":
    main()
