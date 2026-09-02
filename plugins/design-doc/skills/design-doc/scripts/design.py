#!/usr/bin/env python3
"""Driver for the design-doc skill.

  design.py scaffold [dir] [--title X] [--slug x] [--example]
  design.py check <dir> [--strict]
  design.py summary-text <dir>
  design.py pdf [dir]
  design.py snapshot [dir] [--note X] [--item …] [--force]

scaffold creates a fresh directory for one design doc — named after the
slug when no dir is given — holding the doc renderer, the system diagram
and executive-summary files, and either the empty starter registers or the
tinyq worked example. check lints the registers: ID shapes and uniqueness,
dangling cross-references, supersession integrity, footnote tokens, the
qa-log round linkage, and the executive summary; errors exit non-zero,
warnings are advisory, and --strict promotes the warnings a published doc
must not carry into errors. summary-text prints summary.html as plain text,
the input for the voice gate. pdf renders the project's registers into its
design-doc.pdf via the generic build-pdf.py beside this script. snapshot
records a revision of the registers in the project's history directory.
Stdlib only.
"""
import argparse, copy, datetime, hashlib, json, re, shutil, subprocess, sys
from html.parser import HTMLParser
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"
PROJECT_FILES = ("registers.json", "qa-log.json", "NOTES.md", "sysd.svg", "summary.html")
SNAPSHOT_FILES = {"summary": "summary.html", "sysd": "sysd.svg"}

DECISION_STATUSES = {"resolved", "superseded", "open"}
ASSUMPTION_STATUSES = {"working", "validate"}
FN_TOKEN = re.compile(r"\[\^(\d+)\]")
ID_TOKEN = re.compile(r"\b(?:DQ\d+|A\d+|Q\d+|V\d+|c-[a-z0-9-]+)\b")
LEADING_ID = re.compile(r"^\s*(DQ\d+|A\d+|Q\d+|V\d+|c-[a-z0-9-]+)\b")
FINDING_BY_NUMBER = re.compile(r"finding \d+", re.I)
PAGE_TAG = re.compile(r"<\s*/?\s*(html|head|body|script)\b", re.I)
EMBED_TAG = re.compile(r"<\s*/?\s*(iframe|object|embed)\b", re.I)
EVENT_ATTR = re.compile(r"\s(on[a-z]+)\s*=", re.I)
JS_URL = re.compile(r"\b(href|src|xlink:href)\s*=\s*[\"']?\s*javascript:", re.I)
URL_SCHEME = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*):")


def foreign_scheme(url: str):
    m = URL_SCHEME.match(re.sub(r"[\s\x00-\x1f]", "", url))
    return m.group(1) if m and m.group(1).lower() not in ("http", "https") else None


class FragmentText(HTMLParser):
    HEADINGS = {f"h{n}": "#" * n for n in range(1, 7)}
    BLOCKS = {"p", "li", "div", "section", "figure", "figcaption", "blockquote", "pre", "tr", "td", "th"} | set(HEADINGS)
    SKIPPED = {"svg", "script", "style"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines, self.buf, self.cells, self.skip = [], [], [], 0

    def _take(self) -> str:
        text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        self.buf = []
        return text

    def _emit(self, text: str):
        if text:
            self.lines.append(text)

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIPPED:
            self.skip += 1
        elif self.skip:
            return
        elif tag == "br":
            self.buf.append(" ")
        elif tag in self.BLOCKS:
            self._emit(self._take())
            if tag == "tr":
                self.cells = []

    def handle_endtag(self, tag):
        if tag in self.SKIPPED:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag in ("td", "th"):
            self.cells.append(self._take())
        elif tag == "tr":
            self.cells.append(self._take())
            row = [c for c in self.cells if c]
            self.cells = []
            if row:
                self._emit("| " + " | ".join(row) + " |")
        elif tag in self.HEADINGS:
            text = self._take()
            self._emit(f"{self.HEADINGS[tag]} {text}" if text else "")
        elif tag == "li":
            text = self._take()
            self._emit(f"- {text}" if text else "")
        elif tag in self.BLOCKS:
            self._emit(self._take())

    def handle_data(self, data):
        if not self.skip:
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


def digest(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12] if path.exists() else None


# ---------------------------------------------------------------- scaffold

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
    builder = Path(__file__).resolve().parent / "build-pdf.py"
    return subprocess.run([sys.executable, str(builder), args.dir]).returncode


def snapshot(args) -> int:
    root = Path(args.dir)
    registers_path = root / "registers.json"
    try:
        data = json.loads(registers_path.read_text())
    except (OSError, ValueError) as e:
        print(f"snapshot: cannot load registers.json: {e}", file=sys.stderr)
        return 1
    if not isinstance(data, dict):
        print("snapshot: registers.json must be a JSON object", file=sys.stderr)
        return 1

    meta = data.setdefault("meta", {})
    revisions = meta.get("revisions") or []
    last = max(meta.get("rev") or 0, max((r.get("rev") or 0 for r in revisions), default=0))

    files = {k: digest(root / name) for k, name in SNAPSHOT_FILES.items() if (root / name).exists()}
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
                print(f"snapshot: nothing changed since rev {last} — no registers, summary or diagram edits (use --force to record anyway)")
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


# ------------------------------------------------------------------- check

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
                for key, name in SNAPSHOT_FILES.items():
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

    for c in R.get("arch", []):
        for x in c.get("dq", []):
            if x not in d_ids:
                rep.err(f"arch {c.get('id')}: unknown decision {x}")
        for x in c.get("a", []):
            if x not in a_ids:
                rep.err(f"arch {c.get('id')}: unknown assumption {x}")
    for c in R.get("constraints", []):
        for x in str(c.get("a", "")).split():
            if x not in a_ids:
                rep.err(f"constraint {c.get('t', '')[:40]!r}: unknown assumption {x}")
    for n in R.get("pipe", []):
        if n.get("card") and n["card"] not in c_ids:
            rep.err(f"pipe {n.get('t')!r}: card {n['card']!r} is not an arch id")
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
                rep.err(f"path {p.get('id')}: bad seg {g!r} (want [name, p50, p95, desc])")
            elif g[2] < g[1]:
                rep.warn(f"path {p.get('id')} seg {g[0]!r}: p95 {g[2]} < p50 {g[1]}")
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
        for attr in sorted({a.lower() for a in JS_URL.findall(fragment)}):
            rep.err(f"summary.html has a javascript: URL in {attr}; link to a document, not to a script")
        if "TODO" in fragment:
            rep.strict_warn("summary.html is still the scaffold skeleton (it carries a TODO); the doc opens without an executive summary")
        known = a_ids | d_ids | c_ids | open_ids | n_ids | {p.get("id") for p in R.get("paths", [])}
        for cited in sorted(set(ID_TOKEN.findall(fragment_text(fragment))) - known):
            rep.warn(f"summary.html cites {cited}, which no register defines")

    sysd_path = root / "sysd.svg"
    if sysd_path.exists() and "viewBox" not in sysd_path.read_text():
        rep.strict_warn("sysd.svg has no viewBox; the doc cannot scale the diagram to the reading column")

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
    pd = sub.add_parser("pdf", help="render the project's registers into its design-doc.pdf")
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
