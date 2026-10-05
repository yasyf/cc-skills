#!/usr/bin/env python3
"""Start a retro from the records a response already wrote, publish it, and present its prevention options.

  retro.py new --incident SRC [--incident SRC]… --docs <checkout> --title T [--date D] [--team C=a,b]… [--pr]
  retro.py publish <dir> [--ready]
  retro.py board <dir> [--out FILE]

A source is an incident directory, `cci:<regex>`, or a cc-notes id. A directory holding the incident
skill's state.json is rebuilt exactly as `live sync` would; every Markdown file in a directory
contributes its time-led bullets (`- 9:18 PM: …`). `cci:` reads the drive's coordination records,
and a cc-notes id reads that note, answer, investigation or log. Each record becomes one timeline
row with its links lifted into refs, raw customer names replaced through the --team aliases, and
times stored with the display zone's offset. `--pr` commits the scaffold and opens a draft pull
request at once, so the link exists before any prose is written.

`publish` refreshes both index cards from retro.json, commits the retro, pushes, and writes the pull
request body from the summary panels. With `--ready` it first runs the strict check, the render
check and the prose lint once, refuses on an error, and marks the pull request ready.

`board` turns `prevention[]` into a cc-present board: one card per question, one option per answer,
its buys, costs, loses, must-land-first and alternatives as facts and its pros and cons as detail.
Stdlib only.
"""
import argparse, datetime, html, json, re, shutil, subprocess, sys, time, zoneinfo
from pathlib import Path

RETRO_DIR = "incident-retros"
CNAME = "CNAME"
INDEX = "index.html"
STATE = "state.json"
URL = re.compile(r"https?://[^\s)\]>\"'`,;]+")
CLOCK = re.compile(r"^\s*[-*]\s+\**(\d{1,2}):(\d{2})(?::(\d{2}))?(?:\s*[-–]\s*\d{1,2}:\d{2}(?::\d{2})?)?\s*([AP]M)\b"
                   r"(?:\s*PT)?\**[:,.]?\s*(.+)$", re.I)
CCN_ID = re.compile(r"[0-9a-f]{7,40}")
CARD = re.compile(r'^[ \t]*<li><a class="card"[^\n]*data-retro="{slug}">\n(?:.*\n)*?[ \t]*</a></li>\n', re.M)
KIND_MARKS = (
    ("mitigation", re.compile(r"\bFIX-LIVE\b|\bROLLBACK\b|\bREVERTED\b|\b[Rr]olled back\b|\b[Rr]everted\b|"
                              r"\b[Ss]witched (?:\w+ )?back\b|\b[Uu]nfro(?:ze|zen)\b")),
    ("alert", re.compile(r"\bINCIDENT\b|\bALERT\b|\b[Pp]aged\b|sentry\.io/issues|/monitors/\d+")),
    ("hypothesis", re.compile(r"\bMECHANISM\b|\bHYPOTHESIS\b")),
    ("deploy", re.compile(r"buildkite\.com/[\w-]+/release(?:-v\d+)?/builds/\d+")),
    ("action", re.compile(r"\bOPENED\b|\bLAUNCH(?:ED)?\b|\bGO\b|/pull/\d+")),
)
CCI_KIND = {"release": "deploy", "go": "action", "claim": "action", "defect": "report", "decide": "report",
            "report": "report", "note": "report", "incident": "alert", "ask": "report"}
CCI_BUDGET = "16000"
CCI_FULL = 0.9
CCI_ENVELOPE = re.compile(r"\bmsg_[0-9a-f]+\s*|\bdispatch[:=]ctx_[0-9a-f]+:?\s*|\bterm_[0-9a-f-]+|"
                          r"^R\d+\s+|\(\d{1,2}:\d{2}(?::\d{2})?\s*[AP]M(?:\s*PT)?\)\s*")
KEY_KINDS = ("alert", "mitigation", "deploy")
DRAFT_BODY = ("Draft retro, scaffolded from the incident's records. The prose pass is running; `publish` replaces "
              "this body with the summary once it lands.")


def run(argv, cwd=None, check=True) -> str:
    done = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    if check and done.returncode:
        raise SystemExit(f"{' '.join(argv)} exited {done.returncode}: {(done.stderr or done.stdout).strip()}")
    return done.stdout


def local(dt: datetime.datetime, tz) -> str:
    return dt.astimezone(tz).replace(microsecond=0).isoformat()


def kind_of(text: str, default: str = "report") -> str:
    for kind, mark in KIND_MARKS:
        if mark.search(text):
            return kind
    return default


def row(when: datetime.datetime, text: str, tz, retro, live, kind=None, actor=None, refs=()) -> dict:
    links = list(dict.fromkeys([*refs, *URL.findall(text)]))
    bare = re.sub(r"\s+", " ", re.sub(r"\(\s*\)", "", URL.sub("", text))).strip(" -:;,")
    out = {"ts": local(when, tz), "kind": kind or kind_of(text), "h": live.handle(bare, "a recorded event", retro),
           "text": live.clip(bare, retro.ENTRY_WORDS)}
    if actor:
        out["actor"] = actor
    if links:
        out["refs"] = links
    return out


def facts_rows(path: Path, day: datetime.date, tz, retro, live) -> list:
    out, previous = [], None
    for line in path.read_text().splitlines():
        m = CLOCK.match(line)
        if not m:
            continue
        hour, minute, second, half, text = int(m[1]) % 12, int(m[2]), int(m[3] or 0), m[4].upper(), m[5]
        when = datetime.datetime.combine(day, datetime.time(hour + (12 if half == "PM" else 0), minute, second), tz)
        if previous and when < previous - datetime.timedelta(hours=12):
            day += datetime.timedelta(days=1)
            when += datetime.timedelta(days=1)
        previous = when
        out.append(row(when, text, tz, retro, live))
    return out


def cci_rows(pattern: str, since: str, tz, retro, live) -> list:
    """One query per record kind: cci caps a reply at its byte budget, newest first, so one query over every
    kind silently drops the incident's opening records."""
    records = {}
    for kind in CCI_KIND:
        argv = ["cci", "grep", pattern, "--json", "--budget", CCI_BUDGET, "--kind", kind]
        page = run(argv + (["--since", since] if since else []))
        if len(page) >= int(CCI_BUDGET) * CCI_FULL:
            print(f"new: warn: cci {kind} records for {pattern!r} filled the {CCI_BUDGET}-byte budget; the oldest "
                  f"are missing, so narrow the pattern or --since", file=sys.stderr)
        for line in page.splitlines():
            rec = json.loads(line)
            records[rec["seq"]] = rec
    out = []
    for rec in records.values():
        out.append(row(retro.parse_ts(rec["at"]), CCI_ENVELOPE.sub("", rec.get("text") or ""), tz, retro, live,
                       kind=kind_of(rec.get("text") or "", CCI_KIND[rec["kind"]]), actor=rec.get("lane")))
    return out


def ccn_rows(ident: str, cwd: Path, tz, retro, live) -> list:
    rec = json.loads(run(["ccn", "show", ident, "--json"], cwd=cwd))
    if rec.get("entries_omitted"):
        raise SystemExit(f"new: ccn show {ident} omitted {rec['entries_omitted']} entries; pass the log's full history")
    tags = " ".join(rec.get("tags") or [])
    actor = "owner" if "from:owner" in tags else (rec.get("author") or "").split(" ")[0] or None
    if rec.get("entries"):
        return [row(retro.parse_ts(e["ts"]), f"{rec.get('title', '')}: {e.get('text', '')}", tz, retro, live,
                    actor=actor) for e in rec["entries"]]
    text = rec.get("title") or ""
    if rec.get("body") and "from:owner" in tags:
        text = f"{text} Owner: {rec['body']}"
    return [row(retro.parse_ts(rec["created_at"]), text, tz, retro, live, actor=actor)]


def gather(sources: list, args, tz, retro, live, day: datetime.date, cwd: Path):
    rows, state, read = [], None, []
    for src in sources:
        path = Path(src).expanduser()
        if src.startswith("cci:"):
            found = cci_rows(src[4:], args.since, tz, retro, live)
        elif path.is_dir():
            found = []
            if (path / STATE).exists():
                state = (path, live.read_state(path))
            for md in sorted(path.glob("*.md")):
                found += facts_rows(md, day, tz, retro, live)
        elif CCN_ID.fullmatch(src):
            found = ccn_rows(src, cwd, tz, retro, live)
        else:
            raise SystemExit(f"new: {src} is not a directory, a cci:<regex> or a cc-notes id")
        read.append((src, len(found)))
        rows += found
    seen, unique = set(), []
    for r in sorted(rows, key=lambda r: retro.parse_ts(r["ts"])):
        key = (r["ts"][:16], r["text"][:60])
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique, state, read


def stamp_rows(rows: list, retro) -> dict:
    first = lambda kind: next((r["ts"] for r in rows if r["kind"] == kind), None)
    detected = first("alert")
    engaged = next((r["ts"] for r in rows if r["kind"] == "action" and detected
                    and retro.parse_ts(r["ts"]) >= retro.parse_ts(detected)), None)
    flagged = 0
    for r in rows:
        if r["kind"] in KEY_KINDS and flagged < retro.KEY_MOMENTS:
            r["key"] = True
            flagged += 1
    return {"detected": detected, "engaged": engaged, "mitigated": first("mitigation")}


def teams_of(specs: list) -> list:
    out = []
    for spec in specs or []:
        codename, _, aliases = spec.partition("=")
        out.append({"codename": codename.strip(), "aliases": [a.strip() for a in aliases.split(",") if a.strip()]})
    return out


def new(args) -> int:
    retro, live = args.retro, args.retro.sibling_module("retro_live")
    tz = zoneinfo.ZoneInfo(args.tz)
    docs = Path(args.docs).expanduser().resolve()
    day = datetime.date.fromisoformat(args.date) if args.date else datetime.datetime.now(tz).date()
    teams = teams_of(args.team)
    scrub = live.scrubber(teams)
    slug = retro.retro_slug(args.slug, scrub(args.title), day.isoformat())
    root = docs / RETRO_DIR / slug
    started = time.monotonic()
    rows, state, read = gather(args.incident, args, tz, retro, live, day, Path.cwd())
    made = retro.scaffold(argparse.Namespace(dir=str(root), title=scrub(args.title), subtitle=None, tags=args.tags,
                                             date=day.isoformat(), incident=None, slug=slug, example=False))
    if made:
        return made
    R = json.loads((root / "retro.json").read_text())
    if state:
        incident, raw = state
        messages = live.scrub_tree(live.read_slack(incident), scrub)
        R = live.rebuild(live.scrub_state(raw, scrub), messages, R, retro, live.utc_now(), None)
        R.pop("live", None)
    R["meta"]["timezone"] = args.tz
    R["meta"]["teams"] = [t["codename"] for t in teams] or R["meta"].get("teams") or []
    R["meta"]["status"] = "draft"
    R["timeline"] = live.scrub_tree(sorted(R.get("timeline", []) + rows, key=lambda r: retro.parse_ts(r["ts"])),
                                    scrub)
    for r in R["timeline"]:
        r.pop("id", None)
        r.pop("key", None)
    stamps = stamp_rows(R["timeline"], retro)
    for key, value in stamps.items():
        R["timestamps"][key] = R["timestamps"].get(key) or value
    R.setdefault("prevention", [])
    retro.write_retro(root, R)
    notes = root / "NOTES.md"
    notes.write_text(notes.read_text() + "\n## Sources\n\n" +
                     "".join(f"- `{scrub(s)}`: {n} timeline row(s)\n" for s, n in read))
    print(f"new: {root} from {len(read)} source(s), {len(R['timeline'])} timeline row(s), "
          f"{sum(1 for r in R['timeline'] if r.get('key'))} key, in {time.monotonic() - started:.1f}s")
    if retro.check(argparse.Namespace(dir=str(root), strict=False, forbidden_terms=None)):
        print("new: check found errors; fix them, then run publish", file=sys.stderr)
        return 1
    if args.pr:
        return publish(argparse.Namespace(dir=str(root), ready=False, retro=retro))
    return 0


def card(slug: str, R: dict, href: str) -> str:
    meta = R.get("meta") or {}
    esc = lambda key, default="": html.escape(str(meta.get(key, default)))
    return (f'    <li><a class="card" href="{html.escape(href)}" data-retro="{html.escape(slug)}">\n'
            f'      <div class="t">{esc("title")}</div>\n'
            f'      <div class="d">{esc("subtitle")}</div>\n'
            f'      <div class="m">{esc("date")} &middot; {esc("status", "draft")}</div>\n'
            f'    </a></li>\n')


def refresh_cards(docs: Path, slug: str, R: dict) -> list:
    touched = []
    for path, href in ((docs / INDEX, f"{RETRO_DIR}/{slug}/"), (docs / RETRO_DIR / INDEX, f"{slug}/")):
        if not path.exists():
            continue
        page = path.read_text()
        found = re.compile(CARD.pattern.replace("{slug}", re.escape(slug)), re.M).search(page)
        if found:
            path.write_text(page[:found.start()] + card(slug, R, href) + page[found.end():])
        else:
            insert_card(path, card(slug, R, href))
        touched.append(path)
    return touched


def insert_card(path: Path, text: str):
    page = path.read_text()
    start = 0 if path.parent.name == RETRO_DIR else page.find(f'href="{RETRO_DIR}/')
    found = re.compile(r'^[ \t]*<li><a class="card"', re.M).search(page, max(start, 0))
    if not found:
        raise SystemExit(f"publish: {path} carries no card list to add to")
    path.write_text(page[:found.start()] + text + page[found.start():])


def pr_body(root: Path, R: dict, retro) -> str:
    summary = root / retro.SUMMARY_PAGE
    lines = retro.summary_markdown(summary.read_text()) if summary.exists() else []
    if not any(line.strip() and "TODO" not in line for line in lines):
        return DRAFT_BODY
    docs = root.parents[1]
    host = (docs / CNAME).read_text().strip() if (docs / CNAME).exists() else ""
    page = f"https://{host}/{RETRO_DIR}/{root.name}/" if host else ""
    return "\n".join(lines + ([f"\nPage, once merged: {page}"] if page else []))


def gate(root: Path, retro) -> int:
    failed = 0
    for name, call in (
            ("check --strict", lambda: retro.check(argparse.Namespace(dir=str(root), strict=True,
                                                                      forbidden_terms=None))),
            ("render-check", lambda: retro.render_check(argparse.Namespace(dir=str(root),
                                                                           timeout=retro.RENDER_TIMEOUT,
                                                                           words=retro.VISIBLE_WORDS)))):
        started = time.monotonic()
        code = call()
        print(f"publish: {name} exited {code} in {time.monotonic() - started:.1f}s")
        failed |= code
    if shutil.which("slop-cop"):
        started = time.monotonic()
        text = run([sys.executable, str(Path(retro.__file__)), "text", str(root)])
        found = len(json.loads(subprocess.run(["slop-cop", "check", "-", "--lang=markdown", "--llm-effort=off"],
                                              input=text, capture_output=True, text=True).stdout)["violations"])
        print(f"publish: slop-cop over the whole page found {found} passage(s) in {time.monotonic() - started:.1f}s; "
              f"triage them against reference/writing.md and rerun prose --field on the ones that hold")
    return failed


def publish(args) -> int:
    retro = args.retro
    root = Path(args.dir).resolve()
    R = json.loads((root / "retro.json").read_text())
    if args.ready and gate(root, retro):
        print("publish: a gate failed; the pull request stays draft", file=sys.stderr)
        return 1
    docs = Path(run(["git", "-C", str(root), "rev-parse", "--show-toplevel"]).strip())
    slug = root.name
    touched = refresh_cards(docs, slug, R)
    paths = [str(root), *map(str, touched)]
    run(["git", "-C", str(docs), "add", *paths])
    title = f"incident retros: 📝 {R['meta'].get('title', slug)}"
    if run(["git", "-C", str(docs), "diff", "--cached", "--name-only", "--", *paths]).strip():
        run(["git", "-C", str(docs), "commit", "-q", "-m", title, "--", *paths])
    run(["git", "-C", str(docs), "push", "-q", "-u", "origin", "HEAD"])
    body = pr_body(root, R, retro)
    url = run(["gh", "pr", "view", "--json", "url", "-q", ".url"], cwd=docs, check=False).strip()
    if url:
        run(["gh", "pr", "edit", url, "--title", title, "--body", body], cwd=docs)
    else:
        url = run(["gh", "pr", "create", "--draft", "--title", title, "--body", body], cwd=docs).strip().splitlines()[-1]
    if args.ready:
        run(["gh", "pr", "ready", url], cwd=docs)
    print(f"PR: {url} ({'ready' if args.ready else 'draft'})")
    return 0


def option_block(o: dict) -> dict:
    facts = [{"label": label, "value": o[key]} for key, label in
             (("buys", "buys"), ("costs", "costs"), ("loses", "loses"), ("first", "must land first"),
              ("alternatives", "alternatives")) if o.get(key)]
    detail = {k: [p["text"] for p in o.get(k) or []] for k in ("pros", "cons") if o.get(k)}
    if o.get("text"):
        detail["md"] = o["text"]
    out = {"id": o["id"], "label": o["t"]}
    if o.get("hint"):
        out["hint"] = o["hint"]
    if facts:
        out["facts"] = facts
    if detail:
        out["detail"] = detail
    if o.get("recommended"):
        out["recommended"] = True
    return out


def board(args) -> int:
    retro = args.retro
    root = Path(args.dir).resolve()
    R = json.loads((root / "retro.json").read_text())
    questions = [q for q in R.get("prevention") or [] if isinstance(q, dict)]
    if not questions:
        print(f"board: {root / 'retro.json'} carries no prevention[] to present", file=sys.stderr)
        return 1
    meta = R.get("meta") or {}
    blocks = []
    for q in questions:
        lede = q.get("text") or ""
        children = [{"id": f"{q['id']}-lede", "type": "markdown", "md": lede}] if lede else []
        children.append({"id": f"{q['id']}-choice", "type": "choice", "prompt": q["t"], "multi": False,
                         "options": [option_block(o) for o in q.get("options") or []]})
        blocks.append({"id": q["id"], "type": "card", "title": q["t"],
                       "summary": retro.first_sentence(lede) if lede else q.get("h", ""), "children": children})
    doc = {"version": 1, "title": f"Prevention options: {meta.get('title', '')}",
           "intro": meta.get("subtitle", ""), "presentation": "focus",
           "submit": {"label": "Record picks", "note": "Submitting records one pick per prevention question."},
           "blocks": blocks}
    text = json.dumps(doc, indent=1, ensure_ascii=False) + "\n"
    if args.out:
        Path(args.out).write_text(text)
        print(f"board: {len(blocks)} question(s) written to {args.out}")
    else:
        sys.stdout.write(text)
    return 0


def add_new_parsers(sub, retro):
    nw = sub.add_parser("new", help="scaffold a retro whose timeline is read from the response's own records")
    nw.add_argument("--incident", action="append", required=True, metavar="SRC",
                    help="an incident directory, cci:<regex>, or a cc-notes id; repeatable")
    nw.add_argument("--docs", required=True, help="the design-docs checkout, on the retro's branch")
    nw.add_argument("--title", required=True, help="a working headline; the prose pass rewrites it")
    nw.add_argument("--date", help="the day the incident started, YYYY-MM-DD; anchors clock-only bullet times")
    nw.add_argument("--slug")
    nw.add_argument("--tags")
    nw.add_argument("--tz", default="America/Los_Angeles", help="the display zone and the zone bullet times are in")
    nw.add_argument("--team", action="append", metavar="CODENAME=alias,alias",
                    help="replace each alias with the codename everywhere the records are copied; repeatable")
    nw.add_argument("--since", help="cci window: a duration such as 8h, or an RFC 3339 time")
    nw.add_argument("--pr", action="store_true", help="commit, push and open the draft pull request at once")
    nw.set_defaults(fn=new, retro=retro)
    pb = sub.add_parser("publish", help="refresh the index cards, commit, push, and update the pull request")
    pb.add_argument("dir")
    pb.add_argument("--ready", action="store_true", help="run the gates once and mark the pull request ready")
    pb.set_defaults(fn=publish, retro=retro)
    bd = sub.add_parser("board", help="write prevention[] as a cc-present board")
    bd.add_argument("dir")
    bd.add_argument("--out")
    bd.set_defaults(fn=board, retro=retro)
