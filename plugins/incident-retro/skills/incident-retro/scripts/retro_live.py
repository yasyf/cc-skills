#!/usr/bin/env python3
"""Keep a retro current while the incident is still running.

  retro.py live init     <incident-dir> --docs <design-docs checkout> [--slug S]
  retro.py live sync     <incident-dir> --docs <design-docs checkout> [--no-push]
  retro.py live finalize <incident-dir> --docs <design-docs checkout>

The inputs are the incident skill's `state.json` and `slack-log.jsonl`. Every
field is derived from them, so a sync calls no model and produces the same
retro.json for the same state. `init` scaffolds `incident-retros/<slug>/` with
`meta.status: "ongoing"` and the `live.source` branch the page polls, and adds
the card to both index pages. `sync` rebuilds the timeline, windows, causes,
actions and the `live` block, appends to `actions[].history` and
`hypotheses[].history` when a state changed since the last sync, replaces every
raw customer name with its codename, runs `check`, and force-pushes retro.json
and the Slack snapshots to `live/<slug>`. `finalize` drops `live.source` and
moves the retro to `draft`, where the existing prose and publish flow takes it.
Stdlib only.
"""
import argparse, datetime, json, os, re, subprocess, sys, tempfile
from pathlib import Path

RETRO_DIR = "incident-retros"
LIVE_BRANCH = "live/{slug}"
STATE = "state.json"
SLACK_LOG = "slack-log.jsonl"
SLACK_DIR = "evidence/slack"
INDEX = "index.html"
CARD_ANCHOR = re.compile(r'^[ \t]*<li><a class="card"', re.M)
DISPOSITION_STATE = {"open": "todo", "deferred": "todo", "mitigated": "in-progress", "fixed": "done",
                     "already_fixed": "done", "not_reproducible": "dropped"}
DISPOSITION_NOTE = {"deferred": "Deferred during the response.", "not_reproducible": "Not reproducible."}
PR_KIND_ENTRY = {"hotfix": "mitigation", "monitor": "action", "long_term": "action"}
ISSUE_NUMBER = re.compile(r"(\d+)$")
WORD = re.compile(r"\S+")


def utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)


def stamp(dt: datetime.datetime) -> str:
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slack_stamp(ts: str) -> str:
    return stamp(datetime.datetime.fromtimestamp(float(ts), datetime.timezone.utc))


def clip(text: str, budget: int, chars: int = 0) -> str:
    parts = WORD.findall(str(text or "").strip())
    while parts and (len(parts) > budget or (chars and len(" ".join(parts)) > chars)):
        parts.pop()
    return " ".join(parts)


def handle(text: str, fallback: str, retro) -> str:
    short = clip(text, retro.HANDLE_WORDS).rstrip(".,;:")
    return short if len(short.split()) > 1 else fallback


def alias_pairs(teams: list) -> list:
    """Raw Slack names are the only join to a team; teams[].aliases maps each to its codename."""
    pairs = []
    for team in teams or []:
        codename = (team or {}).get("codename")
        for alias in (team or {}).get("aliases") or []:
            if isinstance(alias, str) and alias.strip() and isinstance(codename, str):
                pairs.append((alias.strip(), codename))
    return sorted(pairs, key=lambda pair: -len(pair[0]))


def alias_pattern(pairs: list):
    """Delimiter-aware, so the alias Box leaves Sandbox alone. \\w is unicode by default."""
    if not pairs:
        return None
    return re.compile("|".join(rf"(?<!\w){re.escape(alias)}(?!\w)" for alias, _ in pairs), re.IGNORECASE)


def scrubber(teams: list):
    pairs = alias_pairs(teams)
    pattern = alias_pattern(pairs)
    if pattern is None:
        return lambda value: value
    table = {alias.lower(): codename for alias, codename in pairs}
    return lambda value: pattern.sub(lambda m: table[m.group().lower()], value) if isinstance(value, str) else value


def scrub_tree(value, scrub):
    if isinstance(value, dict):
        return {k: scrub_tree(v, scrub) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_tree(v, scrub) for v in value]
    return scrub(value)


def scrub_state(state: dict, scrub) -> dict:
    """Everything the retro derives comes from state, so scrub it once at the source and the slug,
    the title and every register are scrubbed by construction. teams[] is the table itself."""
    out = {k: scrub_tree(v, scrub) for k, v in state.items() if k != "teams"}
    out["teams"] = state.get("teams") or []
    return out


def read_state(incident: Path) -> dict:
    path = incident / STATE
    if not path.exists():
        raise SystemExit(f"live: {path} not found; the incident skill writes it at intake")
    return json.loads(path.read_text())


def read_slack(incident: Path) -> list:
    path = incident / SLACK_LOG
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if line.strip():
            out.append(json.loads(line))
    return sorted(out, key=lambda m: float(m["ts"]))


def slug_of(state: dict, given, retro) -> str:
    started = retro.try_ts(state.get("started_at"))
    date = started.date().isoformat() if started else datetime.date.today().isoformat()
    return retro.retro_slug(given or state.get("retro_slug"), state.get("title") or "", date)


def retro_root(docs: Path, slug: str) -> Path:
    return docs / RETRO_DIR / slug


def issue_number(issue_id) -> str:
    found = ISSUE_NUMBER.search(str(issue_id or ""))
    return found.group(1) if found else ""


def live_phase(state: dict) -> str:
    if state.get("all_clear_at"):
        return "resolved"
    dispositions = {e.get("disposition") for e in state.get("inventory") or []}
    if state.get("deploys") or dispositions & {"fixed", "mitigated", "already_fixed"}:
        return "mitigated"
    if state.get("diagnoses"):
        return "identified"
    if state.get("detected_at"):
        return "investigating"
    return "detected"


def live_block(state: dict, retro, now: datetime.datetime, source) -> dict:
    inventory = state.get("inventory") or []
    open_issues = [e for e in inventory if e.get("disposition") in (None, "open")]
    prs = state.get("prs") or []
    merged = [p for p in prs if p.get("merged_at")]
    counts = f"{len(open_issues)} of {len(inventory)} issues open, {len(merged)} of {len(prs)} pull requests merged"
    if state.get("all_clear_at"):
        nxt = "All clear called; the retro is being written."
    elif open_issues:
        nxt = clip("Open: " + "; ".join(e.get("summary", "") for e in open_issues[:2]), retro.STATEMENT_WORDS)
    else:
        nxt = "Every issue has a disposition; waiting on the all-clear."
    block = {"updatedAt": stamp(now), "phase": live_phase(state),
             "headline": clip(state.get("title") or "", retro.XS_HEAD_WORDS),
             "currentState": clip(counts, retro.STATEMENT_WORDS), "next": nxt}
    if source is not None:
        block["source"] = source
    return block


def timeline_of(state: dict, messages: list, retro, onset) -> list:
    rows = []
    for m in messages:
        if m.get("thread_ts") or not (m.get("text") or "").strip():
            continue
        author = m.get("author") or m.get("user") or "a responder"
        rows.append({"ts": slack_stamp(m["ts"]), "kind": "report", "actor": author,
                     "h": handle(m.get("text"), "Slack report", retro),
                     "text": clip(m.get("text"), retro.ENTRY_WORDS), "refs": [m["permalink"]], "key": False})
    for monitor in state.get("monitors") or []:
        if monitor.get("fired_at"):
            rows.append({"ts": monitor["fired_at"], "kind": "alert", "actor": "Datadog",
                         "h": f"monitor {monitor.get('id')} fired",
                         "text": f"Monitor {monitor.get('id')} fired.", "refs": [monitor["url"]], "key": True})
    for deploy in state.get("deploys") or []:
        what = "rollback of" if deploy.get("rollback") else "deploy of"
        rows.append({"ts": deploy["at"], "kind": "deploy", "actor": "release",
                     "h": f"{what} {deploy.get('target')}",
                     "text": f"{what.capitalize()} {deploy.get('target')} at {deploy.get('build')}.",
                     "refs": [deploy["url"]] if deploy.get("url") else [], "key": True})
    for pr in state.get("prs") or []:
        for key, kind in (("opened_at", "action"), ("merged_at", PR_KIND_ENTRY.get(pr.get("kind"), "action"))):
            if pr.get(key):
                verb = "opened" if key == "opened_at" else "merged"
                rows.append({"ts": pr[key], "kind": kind, "actor": "the fix lane",
                             "h": f"{pr.get('kind', 'fix')} pull request {verb}",
                             "text": f"The {pr.get('kind', 'fix')} pull request for {pr.get('issue')} was {verb}.",
                             "refs": [pr["url"]], "key": verb == "merged"})
    if state.get("all_clear_at"):
        rows.append({"ts": state["all_clear_at"], "kind": "allclear", "actor": "the commander",
                     "h": "all clear called", "text": "The commander called the all-clear.", "refs": [], "key": True})
    rows.sort(key=lambda row: retro.parse_ts(row["ts"]))
    flagged = 0
    for row in rows:
        if onset and retro.parse_ts(row["ts"]) < onset:
            row["phase"] = "before"
        if row["key"]:
            flagged += 1
            if flagged > retro.KEY_MOMENTS:
                row["key"] = False
        if not row["key"]:
            row.pop("key")
        if not row["refs"]:
            row.pop("refs")
    return rows


def windows_of(state: dict, retro, now: datetime.datetime) -> list:
    start = state.get("started_at")
    if not start:
        return []
    end = state.get("all_clear_at") or stamp(now)
    if retro.parse_ts(end) <= retro.parse_ts(start):
        return []
    teams = [t["codename"] for t in state.get("teams") or [] if isinstance(t, dict) and t.get("codename")]
    return [{"id": "W1", "h": "the incident window", "kind": "outage", "start": start, "end": end, "teams": teams}]


def causes_of(state: dict, retro, before: dict, now: datetime.datetime) -> list:
    out = []
    for issue, diagnosis in sorted((state.get("diagnoses") or {}).items()):
        number = issue_number(issue)
        if not number or not (diagnosis or {}).get("cause"):
            continue
        cid = f"C{number}"
        out.append({"id": cid, "kind": "root", "t": clip(diagnosis["cause"], retro.TITLE_WORDS),
                    "h": handle(diagnosis["cause"], "the diagnosed cause", retro),
                    "text": clip(diagnosis["cause"], retro.CAUSE_BODY_WORDS),
                    "identifiedAt": (before.get(cid) or {}).get("identifiedAt") or stamp(now)})
    return out


def actions_of(state: dict, retro, before: dict, cause_ids: set, now: datetime.datetime) -> list:
    out = []
    for entry in state.get("inventory") or []:
        number = issue_number(entry.get("id"))
        if not number:
            continue
        aid = f"AI{number}"
        state_now = DISPOSITION_STATE.get(entry.get("disposition") or "open", "todo")
        action = {"id": aid, "t": clip(entry.get("summary"), retro.ACTION_TITLE_WORDS),
                  "h": handle(entry.get("summary"), "an open issue", retro),
                  "source": f"C{number}" if f"C{number}" in cause_ids else "review", "state": state_now}
        note = DISPOSITION_NOTE.get(entry.get("disposition"))
        if note:
            action["note"] = note
        links = [{"url": pr["url"], "kind": "pr", "closes": pr.get("kind") == "hotfix"}
                 for pr in state.get("prs") or [] if pr.get("issue") == entry.get("id") and pr.get("url")]
        if links:
            action["links"] = links
        history = list((before.get(aid) or {}).get("history") or [])
        if not history or history[-1].get("state") != state_now:
            history.append({"ts": stamp(now), "state": state_now})
        action["history"] = history
        out.append(action)
    return out


def slack_snapshots(messages: list, now: datetime.datetime, cap: int) -> dict:
    """One ir.slack/1 file per thread, so the page can quote what the timeline cites. The messages
    arrive scrubbed, so the channel name carried into metadata and the file name are scrubbed too;
    lower-casing keeps a codename substitution a valid Slack channel name."""
    threads = {}
    for m in messages:
        root_ts = m.get("thread_ts") or m["ts"]
        threads.setdefault((m["channel"], root_ts), []).append(m)
    out = {}
    for (channel, root_ts), group in sorted(threads.items(), key=lambda kv: float(kv[0][1])):
        group.sort(key=lambda m: float(m["ts"]))
        first = group[0]
        name = (first.get("channel_name") or channel).lstrip("#").lower()
        rendered = [{"ts": m["ts"], "user_id": m.get("user") or "", "user_name": m.get("author") or "unknown",
                     "datetime": slack_stamp(m["ts"]), "text": m.get("text") or "",
                     "reactions": [], "files": []} for m in group[:cap]]
        snapshot = {"schema": "ir.slack/1", "permalink": first["permalink"], "channel_id": channel,
                    "channel_name": name, "ts": first["ts"],
                    "thread_ts": root_ts if first.get("thread_ts") else None, "fetchedAt": stamp(now),
                    "messages": rendered, "redactions": []}
        if len(group) > cap:
            snapshot["truncated"] = True
        out[f"{name}-{first['ts']}.json"] = snapshot
    return out


def shell(state: dict, R: dict, retro, now: datetime.datetime, source) -> dict:
    """What init writes: identity, the clock, and the block the status strip reads."""
    meta = R.setdefault("meta", {})
    meta["status"] = "ongoing"
    meta["teams"] = [t["codename"] for t in state.get("teams") or [] if isinstance(t, dict) and t.get("codename")]
    timestamps = R.setdefault("timestamps", {})
    timestamps["onset"] = state.get("started_at")
    timestamps["detected"] = state.get("detected_at")
    timestamps["allClear"] = state.get("all_clear_at")
    R["live"] = live_block(state, retro, now, source)
    return R


def rebuild(state: dict, messages: list, R: dict, retro, now: datetime.datetime, source) -> dict:
    shell(state, R, retro, now, source)
    onset = retro.try_ts((R.get("timestamps") or {}).get("onset"))
    before_causes = {c["id"]: c for c in retro.entries(R, "causes") if c.get("id")}
    before_actions = {a["id"]: a for a in retro.entries(R, "actions") if a.get("id")}
    R["windows"] = windows_of(state, retro, now)
    R["timeline"] = timeline_of(state, messages, retro, onset)
    R["causes"] = causes_of(state, retro, before_causes, now)
    R["actions"] = actions_of(state, retro, before_actions, {c["id"] for c in R["causes"]}, now)
    notebook = state.get("notebook") or {}
    evidence = R.setdefault("evidence", {})
    if notebook.get("id"):
        evidence["notebooks"] = [{"id": notebook["id"], "url": notebook["url"], "h": "the incident notebook"}]
    evidence["monitors"] = [{"id": m["id"], "url": m["url"], "h": f"monitor {m['id']}"}
                            for m in state.get("monitors") or [] if m.get("id")]
    R["detection"] = {"text": "", "p": "",
                      "monitors": [{"id": m["id"], "role": "caught", "fired": m["fired_at"]}
                                   for m in state.get("monitors") or [] if m.get("id") and m.get("fired_at")]}
    return R


def incident_title(state: dict, slug: str, retro) -> str:
    return clip(state.get("title") or slug, retro.DOC_TITLE_WORDS, retro.DOC_TITLE_CHARS)


def card(slug: str, state: dict, retro, href: str) -> str:
    title = incident_title(state, slug, retro)
    started = retro.try_ts(state.get("started_at"))
    date = started.date().isoformat() if started else ""
    return (f'    <li><a class="card" href="{href}" data-retro="{slug}">\n'
            f'      <div class="t">{title}</div>\n'
            f'      <div class="d">The incident is still running; this page updates itself.</div>\n'
            f'      <div class="m">{date} · Ongoing</div>\n'
            f'    </a></li>\n')


def add_card(path: Path, slug: str, state: dict, retro, href: str):
    """The newest retro heads the list, so the ongoing one is the first card a reader sees."""
    page = path.read_text()
    if f'data-retro="{slug}"' in page:
        return False
    start = 0 if path.parent.name == RETRO_DIR else page.find(f'href="{RETRO_DIR}/')
    found = CARD_ANCHOR.search(page, max(start, 0))
    if not found:
        raise SystemExit(f"live: {path} carries no card list to add to; add the card by hand")
    path.write_text(page[:found.start()] + card(slug, state, retro, href) + page[found.start():])
    return True


def run_check(retro, root: Path, forbidden) -> int:
    args = argparse.Namespace(dir=str(root), strict=False, forbidden_terms=forbidden)
    return retro.check(args)


def git(docs: Path, env, *argv, binary=False):
    result = subprocess.run(["git", "-C", str(docs), *argv], capture_output=True, text=not binary, env=env)
    if result.returncode:
        detail = result.stderr if binary else (result.stderr or result.stdout)
        raise SystemExit(f"live: git {' '.join(argv)} failed: {detail.strip()}")
    return result.stdout


def build_commit(docs: Path, paths: list, message: str):
    """A temporary index and an orphan commit, so the docs checkout's HEAD and index never move."""
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / "index"))
        git(docs, env, "read-tree", "--empty")
        git(docs, env, "add", "--force", "--", *[p for p in paths if (docs / p).exists()])
        tree = git(docs, env, "write-tree").strip()
        return tree, git(docs, env, "commit-tree", tree, "-m", message).strip()


def gate_patterns(state: dict, forbidden, root: Path, retro) -> list:
    """The two independent term sources the pushed artifact is held against."""
    out = []
    aliases = alias_pattern(alias_pairs(state.get("teams")))
    if aliases is not None:
        out.append(("teams[].aliases", aliases))
    configured = retro.sibling_module("retro_evidence").forbidden_terms(forbidden, root)
    if configured is not None:
        out.append(("the forbidden-terms source", configured))
    return out


def artifact_hits(docs: Path, tree: str, named: dict, patterns: list) -> list:
    """Every path and every blob of the tree about to be pushed, whatever the file type."""
    hits = []

    def scan(where, text):
        for source, pattern in patterns:
            found = pattern.search(text)
            if found:
                hits.append((where, found.group(), source))

    for where, text in named.items():
        scan(where, text)
    for row in git(docs, None, "ls-tree", "-r", "-z", tree).split("\0"):
        if not row:
            continue
        meta, path = row.split("\t", 1)
        scan(f"the path {path}", path)
        blob = git(docs, None, "cat-file", "blob", meta.split()[2], binary=True)
        scan(f"the contents of {path}", blob.decode("utf-8", "replace"))
    return hits


def push_commit(docs: Path, branch: str, commit: str):
    git(docs, None, "push", "--force", "origin", f"{commit}:refs/heads/{branch}")


def init(args) -> int:
    retro, incident, docs = args.retro, Path(args.incident_dir), Path(args.docs)
    raw = read_state(incident)
    state = scrub_state(raw, scrubber(raw.get("teams")))
    slug = slug_of(state, args.slug, retro)
    root = retro_root(docs, slug)
    if root.exists() and any(root.iterdir()):
        print(f"live init: {root} exists; run retro.py live sync to bring it current", file=sys.stderr)
        return 1
    scaffold = argparse.Namespace(dir=str(root), title=incident_title(state, slug, retro), subtitle=None,
                                  tags=args.tags, date=slug[:10], incident=None, slug=slug, example=False)
    if retro.scaffold(scaffold):
        return 1
    R = retro.load_retro(root, "live init")
    R["meta"]["timezone"] = args.timezone
    branch = LIVE_BRANCH.format(slug=slug)
    shell(state, R, retro, utc_now(), {"repo": args.repo, "branch": branch})
    retro.write_retro(root, R)
    raw["retro_slug"] = slug
    (incident / STATE).write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n")
    add_card(docs / RETRO_DIR / INDEX, slug, state, retro, f"{slug}/")
    add_card(docs / INDEX, slug, state, retro, f"{RETRO_DIR}/{slug}/")
    print(f"live init: {root} is ongoing, polling {args.repo}@{branch}")
    print(f"sync:  retro.py live sync {incident} --docs {docs}")
    return run_check(retro, root, args.forbidden_terms)


def sync(args) -> int:
    retro, incident, docs = args.retro, Path(args.incident_dir), Path(args.docs)
    prose = retro.sibling_module("retro_prose")
    raw = read_state(incident)
    slug = slug_of(scrub_state(raw, scrubber(raw.get("teams"))), args.slug, retro)
    root = retro_root(docs, slug)
    if not (root / "retro.json").exists():
        print(f"live sync: {root} has no retro.json; run retro.py live init first", file=sys.stderr)
        return 1
    try:
        with prose.Owner(root):
            return write_sync(args, retro, prose, incident, docs, slug, root)
    except prose.Busy as held:
        print(f"live sync: {held} is already writing {root}", file=sys.stderr)
        return 1


def write_sync(args, retro, prose, incident: Path, docs: Path, slug: str, root: Path) -> int:
    """The record is read under the claim, so a sync that waited does not publish the state it
    read before waiting and append a transition that never happened."""
    now = utc_now()
    raw = read_state(incident)
    scrub = scrubber(raw.get("teams"))
    state = scrub_state(raw, scrub)
    R = retro.load_retro(root, "live sync")
    if R is None:
        return 1
    source = (R.get("live") or {}).get("source") or {"repo": args.repo, "branch": LIVE_BRANCH.format(slug=slug)}
    messages = [scrub_tree(m, scrub) for m in read_slack(incident)]
    rebuild(state, messages, R, retro, now, source)
    snapshots = slack_snapshots(messages, now, retro.sibling_module("retro_evidence").SLACK_MAX_MESSAGES)
    folder = root / SLACK_DIR
    folder.mkdir(parents=True, exist_ok=True)
    for stale in folder.glob("*.json"):
        stale.unlink()
    for name, snapshot in snapshots.items():
        prose.write_atomic(folder / name, json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n")
    R["evidence"]["slack"] = [{"url": snapshot["permalink"], "file": f"{SLACK_DIR}/{name}",
                               "h": f"the {snapshot['channel_name']} thread"}
                              for name, snapshot in sorted(snapshots.items())]
    prose.write_atomic(root / "retro.json", json.dumps(R, indent=2, ensure_ascii=False) + "\n")
    if run_check(retro, root, args.forbidden_terms):
        print("live sync: check failed; the branch was not pushed", file=sys.stderr)
        return 1
    if args.no_push:
        print(f"live sync: {root} is current as of {R['live']['updatedAt']} (not pushed)")
        return 0
    return publish(args, retro, docs, raw, slug, root, source["branch"], R["live"]["updatedAt"])


def publish(args, retro, docs: Path, raw: dict, slug: str, root: Path, branch: str, at: str) -> int:
    """The gate reads the artifact itself: the tree built for this push, its every path and blob,
    and the branch, slug and message pushed alongside it."""
    patterns = gate_patterns(raw, args.forbidden_terms, root, retro)
    if not patterns:
        print("live sync: nothing can check this push for raw customer names, because no "
              "forbidden-terms source resolved (--forbidden-terms, FORBIDDEN_TERMS, or a .customer-names "
              "file up the tree) and state.teams carries no aliases; set one of them and sync again",
              file=sys.stderr)
        return 1
    message = f"live: {slug} as of {at}"
    tree, commit = build_commit(docs, [f"{RETRO_DIR}/{slug}/retro.json", f"{RETRO_DIR}/{slug}/{SLACK_DIR}"], message)
    named = {"the branch name": branch, "the slug": slug, "the commit message": message}
    hits = artifact_hits(docs, tree, named, patterns)
    if hits:
        for where, term, source in hits:
            print(f"live sync: {where} carries {retro.masked(term)}, which {source} forbids", file=sys.stderr)
        print("live sync: the branch was not pushed", file=sys.stderr)
        return 1
    push_commit(docs, branch, commit)
    print(f"live sync: pushed {branch} as of {at}")
    return 0


def finalize(args) -> int:
    retro, incident, docs = args.retro, Path(args.incident_dir), Path(args.docs)
    prose = retro.sibling_module("retro_prose")
    raw = read_state(incident)
    state = scrub_state(raw, scrubber(raw.get("teams")))
    slug = slug_of(state, args.slug, retro)
    root = retro_root(docs, slug)
    R = retro.load_retro(root, "live finalize")
    if R is None:
        return 1
    R["meta"]["status"] = "draft"
    if args.tags:
        R["meta"]["tags"] = [t.strip() for t in args.tags.split(",") if t.strip()]
    (R.get("live") or {}).pop("source", None)
    timestamps = R.setdefault("timestamps", {})
    timestamps["resolved"] = timestamps.get("resolved") or state.get("all_clear_at")
    prose.write_atomic(root / "retro.json", json.dumps(R, indent=2, ensure_ascii=False) + "\n")
    print(f"live finalize: {root} is a draft; the page stopped polling")
    print(f"write:  retro.py prose {root}")
    return run_check(retro, root, args.forbidden_terms)


def add_live_parser(sub, retro):
    live = sub.add_parser("live", help="scaffold, refresh and close a retro while the incident is still running")
    commands = live.add_subparsers(dest="live_cmd", required=True)
    for name, fn, help_text in (("init", init, "scaffold the ongoing retro and add its card to both index pages"),
                                ("sync", sync, "rebuild the retro from state.json and slack-log.jsonl and push it"),
                                ("finalize", finalize, "drop live.source and move the retro to draft")):
        p = commands.add_parser(name, help=help_text)
        p.add_argument("incident_dir", help="the incident skill's ~/.claude/incidents/<incident-id> directory")
        p.add_argument("--docs", required=True, help="the design-docs checkout the retro lives in")
        p.add_argument("--slug", help="override the slug state.json carries")
        p.add_argument("--repo", default="Forge-AI/design-docs", help="the owner/repo the page polls")
        p.add_argument("--timezone", default="America/Los_Angeles", help="the zone the page displays times in")
        p.add_argument("--forbidden-terms", metavar="REGEX",
                       help="customer names the retro must not carry (default: FORBIDDEN_TERMS, then .customer-names)")
        if name == "sync":
            p.add_argument("--no-push", action="store_true", help="write and check the retro without pushing it")
        else:
            p.add_argument("--tags", help=f"{retro.TAG_COUNT[0]} to {retro.TAG_COUNT[1]} lower-case topical tags, "
                                          f"comma separated; a retro past ongoing carries them")
        p.set_defaults(fn=fn, retro=retro)
