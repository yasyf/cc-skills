#!/usr/bin/env python3
"""Driver for the design-doc skill.

  design.py scaffold [dir] [--title X] [--slug x] [--example]
  design.py check <dir>
  design.py pdf [dir]
  design.py snapshot [dir] [--note X] [--force]

scaffold creates a fresh directory for one design doc — named after the
slug when no dir is given — holding the doc renderer and either the empty
starter registers or the tinyq worked example. check lints the registers:
ID shapes and uniqueness, dangling cross-references, supersession
integrity, footnote tokens, and the qa-log round linkage; errors exit
non-zero, warnings are advisory. pdf renders the project's registers into
its design-doc.pdf via the generic build-pdf.py beside this script. snapshot
records a revision of the registers in the project's history directory. Stdlib
only.
"""
import argparse, copy, datetime, json, re, shutil, subprocess, sys
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"

DECISION_STATUSES = {"resolved", "superseded", "open"}
ASSUMPTION_STATUSES = {"working", "validate"}
FN_TOKEN = re.compile(r"\[\^(\d+)\]")


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
    html = (TEMPLATES / "design-doc.html").read_text()
    if args.example:
        svg = (src / "sysd.svg").read_text().strip()
        html = re.sub(r"<!--SYSD-->.*?<!--/SYSD-->", lambda m: f"<!--SYSD-->\n    {svg}\n    <!--/SYSD-->", html, count=1, flags=re.S)
    (dest / "design-doc.html").write_text(html)
    for name in ("registers.json", "qa-log.json", "NOTES.md"):
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
    previous_path = root / "history" / f"rev-{last}.json"
    if not args.force and previous_path.exists():
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
                print(f"snapshot: no register changes since rev {last} — nothing recorded (use --force to record anyway)")
                return 0

    rev = last + 1
    meta["rev"] = rev
    meta["revisions"] = revisions
    revisions.append({"rev": rev, "date": datetime.date.today().isoformat(), "note": args.note})
    payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    previous_path = root / "history" / f"rev-{rev}.json"
    previous_path.parent.mkdir(parents=True, exist_ok=True)
    previous_path.write_text(payload)
    registers_path.write_text(payload)
    print(f"snapshot: rev {rev} recorded → history/rev-{rev}.json")
    return 0


# ------------------------------------------------------------------- check

class Report:
    def __init__(self):
        self.errors, self.warnings = [], []

    def err(self, msg):
        self.errors.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)

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


def check(args) -> int:
    rep = Report()
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
                        rep.warn(f"history/rev-{revision_rev}.json is missing; the changes-since picker cannot diff against it")
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
            last_revision = revisions[-1]
            last_rev = last_revision.get("rev") if isinstance(last_revision, dict) else None
            if rev_valid and isinstance(last_rev, int) and not isinstance(last_rev, bool) and rev != last_rev:
                rep.err(f"meta.rev {rev} does not match last meta.revisions rev {last_rev}")
            if len(revs) == len(revisions):
                if any(a >= b for a, b in zip(revs, revs[1:])):
                    rep.err("meta.revisions revs must be strictly increasing and unique")
                if revs != list(range(1, len(revs) + 1)):
                    rep.warn("meta.revisions revs are not contiguous from 1 (fine if intentional)")

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
    for k in rounds:
        if k not in referenced_rounds:
            rep.warn(f"rounds[{k}] is referenced by no decision")

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
    open_ids = {o.get("id") for o in R.get("open", [])}
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
    check_ids(rep, R.get("numbers", []), "id", r"n-[a-z0-9-]+", "numbers")
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
            for q in r.get("questions", []):
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
    ck.set_defaults(fn=check)
    pd = sub.add_parser("pdf", help="render the project's registers into its design-doc.pdf")
    pd.add_argument("dir", nargs="?", default=".")
    pd.set_defaults(fn=pdf)
    sn = sub.add_parser("snapshot", help="record a revision of the project's registers")
    sn.add_argument("dir", nargs="?", default=".")
    sn.add_argument("--note", default="")
    sn.add_argument("--force", action="store_true")
    sn.set_defaults(fn=snapshot)
    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
