#!/usr/bin/env python3
"""Build a retro from response records, record the owner's picks, and publish the rendered page.

  retro.py new --incident SRC [--incident SRC]… --docs <checkout> --title T [--date D] [--team NAME]…
  retro.py board <dir> [--out FILE]
  retro.py publish <dir> [--seconds N]
  retro.py publish <dir> --await <pr-url> [--seconds N]
  retro.py comms-check <draft-file|-> --url <rendered-url>

`new` reads an incident directory, `cci:<regex>`, or a cc-notes id. It rebuilds state.json as live
sync does and imports time-led Markdown bullets, coordination records, or the named note, answer,
investigation, or log. Timeline rows lift links into refs and store times with the display zone's
offset.

`board` turns prevention questions into cc-present cards, with options, facts, pros, cons, and
recommendations. Present it to the owner, record picked options, owners, PR links or named lanes,
and fill Remediation before the prose pass and the first retro PR.

`publish` runs check --strict, render-check, and a whole-page slop-cop count before pushing anything.
After the gates pass, it has the docs checkout's `sync-index.sh` regenerate both index pages' cards, commits only the retro directory and the two
index pages, pushes, and opens a ready PR or edits the existing PR and marks it ready. It writes
the PR body from the summary panels. A retro PR is never draft.

The wait merges the PR itself with the merge method the repository allows as soon as it is clean and every
check is green, and ends when the PR has merged and a successful github-pages deployment contains the merge
commit. GitHub sign-in protects the site, so the deployment record proves the merged revision is
served. Success exits 0 with `RENDERED: https://<CNAME>/incident-retros/<slug>/` as the last line.
A failed check or a PR closed without merging exits 1. After --seconds (default 540), an unfinished
wait exits 75 with an AWAIT: command; publish --await <pr-url> resumes it.

Only the URL from RENDERED: goes to comms. Every retro comms draft passes comms-check before posting;
it exits 1 for any GitHub or Graphite PR link or a missing rendered URL. A PR link is never posted.
The draft comes from a file or stdin (-). Stdlib only.
"""
import argparse, datetime, json, re, shutil, subprocess, sys, tempfile, time, zoneinfo
from pathlib import Path

RETRO_DIR = "incident-retros"
CNAME = "CNAME"
STATE = "state.json"
URL = re.compile(r"https?://[^\s)\]>\"'`,;]+")
CLOCK = re.compile(r"^\s*[-*]\s+\**(\d{1,2}):(\d{2})(?::(\d{2}))?(?:\s*[-–]\s*\d{1,2}:\d{2}(?::\d{2})?)?\s*([AP]M)\b"
                   r"(?:\s*PT)?\**[:,.]?\s*(.+)$", re.I)
CCN_ID = re.compile(r"[0-9a-f]{7,40}")
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
PR_LINK = re.compile(r"https?://(?:www\.)?github\.com/[^/\s]+/[^/\s]+/pull/\d+|https?://app\.graphite\.(?:dev|com)/\S*/pr/\S+")
AWAIT_SECONDS = 540
AWAIT_POLL = 10
STILL_WAITING = 75
PAGES_ENV = "github-pages"
MERGE_METHODS = ("squash", "rebase", "merge")
GREEN = ("SUCCESS", "SKIPPED", "NEUTRAL")
RED = ("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT")


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


def new(args) -> int:
    retro, live = args.retro, args.retro.sibling_module("retro_live")
    tz = zoneinfo.ZoneInfo(args.tz)
    docs = Path(args.docs).expanduser().resolve()
    day = datetime.date.fromisoformat(args.date) if args.date else datetime.datetime.now(tz).date()
    slug = retro.retro_slug(args.slug, args.title, day.isoformat())
    root = docs / RETRO_DIR / slug
    started = time.monotonic()
    rows, state, read = gather(args.incident, args, tz, retro, live, day, Path.cwd())
    made = retro.scaffold(argparse.Namespace(dir=str(root), title=args.title, subtitle=None, tags=args.tags,
                                             date=day.isoformat(), incident=None, slug=slug, example=False))
    if made:
        return made
    R = json.loads((root / "retro.json").read_text())
    if state:
        incident, raw = state
        R = live.rebuild(raw, live.read_slack(incident), R, retro, live.utc_now(), None)
        R.pop("live", None)
    R["meta"]["timezone"] = args.tz
    R["meta"]["teams"] = [t.strip() for t in args.team or [] if t.strip()] or R["meta"].get("teams") or []
    R["meta"]["status"] = "draft"
    R["timeline"] = sorted(R.get("timeline", []) + rows, key=lambda r: retro.parse_ts(r["ts"]))
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
                     "".join(f"- `{s}`: {n} timeline row(s)\n" for s, n in read))
    print(f"new: {root} from {len(read)} source(s), {len(R['timeline'])} timeline row(s), "
          f"{sum(1 for r in R['timeline'] if r.get('key'))} key, in {time.monotonic() - started:.1f}s")
    if retro.check(argparse.Namespace(dir=str(root), strict=False)):
        print("new: check found errors; fix them, then run publish", file=sys.stderr)
        return 1
    return 0


def rendered_url(root: Path) -> str:
    docs = root.parents[1]
    return f"https://{(docs / CNAME).read_text().strip()}/{RETRO_DIR}/{root.name}/"


def pr_body(root: Path, retro) -> str:
    lines = retro.summary_markdown((root / retro.SUMMARY_PAGE).read_text())
    return "\n".join(lines + [f"\nRendered page, live once this merges and Pages deploys: {rendered_url(root)}"])


def render_with_plugin_template(root: Path, retro) -> int:
    """A docs repo serves the renderer its own pin names, which can lag this plugin; render-check against this
    plugin's template so a section the pinned renderer cannot draw still counts against the page budget."""
    with tempfile.TemporaryDirectory() as scratch:
        copy = Path(scratch) / root.name
        shutil.copytree(root, copy, ignore=shutil.ignore_patterns("index.html"))
        shutil.copy(retro.TEMPLATES / retro.PAGE, copy / retro.PAGE)
        return retro.render_check(argparse.Namespace(dir=str(copy), timeout=retro.RENDER_TIMEOUT,
                                                     words=retro.VISIBLE_WORDS))


def gate(root: Path, retro) -> int:
    failed = 0
    for name, call in (
            ("check --strict", lambda: retro.check(argparse.Namespace(dir=str(root), strict=True))),
            ("render-check", lambda: render_with_plugin_template(root, retro))):
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
    if args.await_pr:
        return await_rendered(root, args.await_pr, args.seconds)
    if gate(root, retro):
        print("publish: a gate failed; nothing was pushed", file=sys.stderr)
        return 1
    R = json.loads((root / "retro.json").read_text())
    docs = Path(run(["git", "-C", str(root), "rev-parse", "--show-toplevel"]).strip())
    slug = root.name
    touched = retro.sibling_module("retro_live").sync_cards(docs)
    paths = [str(root), *map(str, touched)]
    run(["git", "-C", str(docs), "add", *paths])
    title = f"incident retros: 📝 {R['meta'].get('title', slug)}"
    if run(["git", "-C", str(docs), "diff", "--cached", "--name-only", "--", *paths]).strip():
        run(["git", "-C", str(docs), "commit", "-q", "-m", title, "--", *paths])
    run(["git", "-C", str(docs), "push", "-q", "-u", "origin", "HEAD"])
    body = pr_body(root, retro)
    url = run(["gh", "pr", "view", "--json", "url", "-q", ".url"], cwd=docs, check=False).strip()
    if url:
        run(["gh", "pr", "edit", url, "--title", title, "--body", body], cwd=docs)
        run(["gh", "pr", "ready", url], cwd=docs, check=False)
    else:
        url = run(["gh", "pr", "create", "--title", title, "--body", body], cwd=docs).strip().splitlines()[-1]
    print(f"publish: {url} is ready and merges once it is clean and its checks pass")
    print(f"AWAIT: {Path(sys.argv[0]).resolve()} publish {root} --await {url}")
    return await_rendered(root, url, args.seconds)


def merge_flag(url: str) -> str:
    allowed = json.loads(run(["gh", "api", f"repos/{'/'.join(url.split('/')[3:5])}"]))
    return next(f"--{m}" for m in MERGE_METHODS if allowed[f"allow_{m}_merge"])


def pages_live(repo: str, sha: str) -> bool:
    for dep in json.loads(run(["gh", "api", f"repos/{repo}/deployments?environment={PAGES_ENV}&per_page=10"])):
        ahead = json.loads(run(["gh", "api", f"repos/{repo}/compare/{sha}...{dep['sha']}"]))["status"]
        if ahead not in ("identical", "ahead"):
            continue
        states = json.loads(run(["gh", "api", f"repos/{repo}/deployments/{dep['id']}/statuses?per_page=1"]))
        if states and states[0]["state"] == "success":
            return True
    return False


def await_rendered(root: Path, url: str, seconds: float) -> int:
    """The merged commit counts as rendered once a successful Pages deployment contains it: the site sits
    behind GitHub sign-in, so an anonymous fetch of the page cannot tell a fresh deploy from a stale one."""
    repo = "/".join(url.split("/")[3:5])
    deadline = time.monotonic() + seconds
    while True:
        pr = json.loads(run(["gh", "pr", "view", url, "--json", "state,mergeCommit,mergeStateStatus,statusCheckRollup"]))
        verdicts = [c.get("conclusion") or c.get("state") for c in pr["statusCheckRollup"]]
        failed = [c.get("name") or c.get("context") for c, v in zip(pr["statusCheckRollup"], verdicts) if v in RED]
        if failed:
            print(f"publish: {url} failed {', '.join(failed)}; fix it and run publish again", file=sys.stderr)
            return 1
        if pr["state"] == "CLOSED":
            print(f"publish: {url} was closed without merging", file=sys.stderr)
            return 1
        if pr["state"] == "OPEN" and pr["mergeStateStatus"] == "CLEAN" and all(v in GREEN for v in verdicts):
            run(["gh", "pr", "merge", url, merge_flag(url)], cwd=root.parents[1])
        if pr["state"] == "MERGED" and pages_live(repo, pr["mergeCommit"]["oid"]):
            print(f"RENDERED: {rendered_url(root)}")
            return 0
        if time.monotonic() >= deadline:
            stage = "deploying to Pages" if pr["state"] == "MERGED" else "waiting on checks to merge"
            print(f"publish: still {stage} after {seconds:.0f}s")
            print(f"AWAIT: {Path(sys.argv[0]).resolve()} publish {root} --await {url}")
            return STILL_WAITING
        time.sleep(AWAIT_POLL)


def comms_check(args) -> int:
    text = sys.stdin.read() if args.draft == "-" else Path(args.draft).read_text()
    problems = [f"links the pull request {m}; post the rendered page instead" for m in PR_LINK.findall(text)]
    if args.url not in text:
        problems.append(f"does not link the rendered retro {args.url}")
    for p in problems:
        print(f"comms-check: the draft {p}", file=sys.stderr)
    return 1 if problems else 0


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
    nw.add_argument("--team", action="append", metavar="NAME",
                    help="a team the incident affected, by its customer name; repeatable")
    nw.add_argument("--since", help="cci window: a duration such as 8h, or an RFC 3339 time")
    nw.set_defaults(fn=new, retro=retro)
    pb = sub.add_parser("publish", help="gate, commit, open the ready pull request, merge it, and print the rendered "
                        "page once Pages serves it")
    pb.add_argument("dir")
    pb.add_argument("--await", dest="await_pr", metavar="PR_URL", help="wait on an already published retro's merge "
                    "and Pages deployment")
    pb.add_argument("--seconds", type=float, default=AWAIT_SECONDS, help=f"how long to wait before exiting "
                    f"{STILL_WAITING} with a fresh AWAIT: line")
    pb.set_defaults(fn=publish, retro=retro)
    cc = sub.add_parser("comms-check", help="refuse a comms draft that links a pull request or omits the rendered page")
    cc.add_argument("draft", help="the draft's file, or - for stdin")
    cc.add_argument("--url", required=True, help="the RENDERED: line publish printed")
    cc.set_defaults(fn=comms_check, retro=retro)
    bd = sub.add_parser("board", help="write prevention[] as a cc-present board")
    bd.add_argument("dir")
    bd.add_argument("--out")
    bd.set_defaults(fn=board, retro=retro)
