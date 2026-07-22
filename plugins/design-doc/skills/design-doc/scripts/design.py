#!/usr/bin/env python3
"""Driver for the design-doc skill.

  design.py scaffold <dir> [--title X] [--slug x] [--example]
  design.py check <dir>

scaffold copies the doc renderer, the PDF builder, and either the empty
starter registers or the tinyq worked example into <dir>. check lints the
registers: ID shapes and uniqueness, dangling cross-references, supersession
integrity, footnote tokens, and the qa-log round linkage. Errors exit
non-zero; warnings are advisory. Stdlib only.
"""
import argparse, datetime, json, re, shutil, sys
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"

DECISION_STATUSES = {"resolved", "superseded", "open"}
ASSUMPTION_STATUSES = {"working", "validate"}
FN_TOKEN = re.compile(r"\[\^(\d+)\]")


# ---------------------------------------------------------------- scaffold

def slugify(s: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-") or "design"


def scaffold(args) -> int:
    dest = Path(args.dir)
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
    shutil.copy(TEMPLATES / "build-pdf.py", dest / "build-pdf.py")
    for name in ("registers.json", "qa-log.json", "NOTES.md"):
        shutil.copy(src / name, dest / name)

    if not args.example:
        title = args.title or "Untitled design"
        slug = args.slug or slugify(title)
        today = datetime.date.today().isoformat()
        for name in ("registers.json", "NOTES.md"):
            p = dest / name
            p.write_text(p.read_text()
                         .replace("PROJECT_TITLE", title)
                         .replace("PROJECT_SLUG", slug)
                         .replace("PROJECT_DATE", today))

    print(f"scaffolded {dest} ({'tinyq example' if args.example else 'starter'})")
    print(f"serve:  cd {dest} && python3 -m http.server 8641")
    print(f"check:  {Path(__file__).name} check {dest}")
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

    meta = R.get("meta", {})
    for k in ("title", "slug", "date"):
        if not meta.get(k):
            rep.err(f"meta.{k} is missing or empty")

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
    sc = sub.add_parser("scaffold", help="create a new design project directory")
    sc.add_argument("dir")
    sc.add_argument("--title")
    sc.add_argument("--slug")
    sc.add_argument("--example", action="store_true", help="use the tinyq worked example instead of the empty starter")
    sc.set_defaults(fn=scaffold)
    ck = sub.add_parser("check", help="lint the registers in a design project directory")
    ck.add_argument("dir")
    ck.set_defaults(fn=check)
    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
