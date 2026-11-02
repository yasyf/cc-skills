import argparse
import datetime as dt
import gzip
import hashlib
import html
import json
import os
import queue
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

import plainwords
from brief import COORDINATORS, ROOT, Curator
from pages import PAGES, source_key, stamp
from review_assistant import ReviewAssistant
from story import Stories

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "long-running" / "scripts"))
import holds
import milestones

HERE = Path(__file__).resolve().parent
PACIFIC = ZoneInfo("America/Los_Angeles")
CCI = "http://127.0.0.1:7377/v1"
TIMELINE_KINDS = ["state", "head", "review", "owner", "go", "landed", "release", "incident", "mechanism", "fix-live", "opened", "report", "hold", "lift", "blocker", "defect", "correction", "decide", "decision", "ask", "answer", "done", "stopped", "stand-down", "handoff", "evidence"]
TIMELINE_WINDOW = dt.timedelta(hours=24)
LEDGER_KINDS = {"landed", "go", "opened", "head", "hold", "lift"}
LIVE_SOURCES = ["cc-present store: taps and questions", "cci bus: records and workers", "drive ledger in cc-notes: pull request states, re-read on every landing, go, head, opened, hold or lift record", "progress-desk steps.json: the step rows", "findings ledger in cc-notes: open sweep findings and owner picks, re-read every 5 minutes"]
REBUILD_AFTER = 2.0
LIVE_REFRESH = 5.0
WORD_BYSTANDERS = ("dashboard", "register", "cc-present", "desk-runner")
PR_NAMED = re.compile(r"#(\d{5})(?!\d)")
SLOW_REFRESH = 300
PLAN_HEADING = "\n## Ordered plan of action"
PLAN_ITEM = re.compile(r"^\d+\. (.+)$")
GO = re.compile(r"^go #(\d+) at (\w+)")
CI_FIX = re.compile(r"\bCI[- ]fix\b", re.I)
TAP_CARRY = re.compile(r"\bTAP-(?:CARRIED|KEPT)\b")
WITHDRAWN_GO = re.compile(r"\bgo\b[^.]*\bwithdr(?:aw|awn|ew)\b", re.I)
CARD_ID = re.compile(r"\bcard-[a-z0-9]+(?:-[a-z0-9]+)*")
PR_REF = re.compile(r"#(\d{4,6})\b")
PR_RANGE = re.compile(r"#(\d{4,6})-#(\d{4,6})")
REVIEW_ROUTE = re.compile(r"/api/review/(\d+)")
AI_CONFIG = {"endpoint": "https://api.cerebras.ai/v1", "model": "gpt-oss-120b"}
ASSIST = HERE.parents[1] / "skills" / "long-running" / "templates" / "drive-assist.html"
BUILD = hashlib.sha1(b"".join(path.read_bytes() for path in sorted([*HERE.iterdir(), ASSIST]) if path.suffix in (".js", ".css", ".html", ".py"))).hexdigest()[:12]
CLIENT_RECORD = ("seq", "lane", "kind", "at", "text", "refs", "re")
PR_ROUTE = re.compile(r"/api/pr/(\d+)")
RECORD_ROUTE = re.compile(r"/record/(\d+)")
CLOCK_OPENING = re.compile(r"^\s*\d{1,2}:\d{2}\s*[AP]M\b[,:]?\s*")
PR_TAG = re.compile(r"\s*\bpr:\S+")
FILE_PATH = re.compile(r"\s*(?<![\w.])~?/(?:[\w.@-]+/)+[\w.@-]+")
ASKED_PREFIX = "Asked on the dashboard: "
OWNER_LANES = frozenset({"dashboard", "owner-board", "owner"})
LANE_ROUTE = re.compile(r"/api/lane/([\w.-]+)")
REGISTER_DAY = dt.timedelta(days=1)
REGISTER_SHOWN = 25
MILESTONE_TTL = 60
PICK_GRACE = dt.timedelta(minutes=1)
ANSWER_EVENTS = ("choice.selected", "decision.created", "input.submitted")
UNSETTLED = ("open", "routed")
RECORD_PAGE = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="dark"><title>{title}</title><link rel="stylesheet" href="/app.css"><link rel="stylesheet" href="/pages.css"></head><body><main class="wrap pg"><a class="pg-back" href="/">‹ The drive dashboard</a><header class="pg-h"><div><h1>{title}</h1></div><span class="pg-at">{byline}</span></header>{lead}<section class="pg-lead">{body}</section><p class="pg-at"><a href="/record/{seq}?raw=1">The raw record</a></p></main></body></html>'
SOURCE_PAGE = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="dark"><title>{title}</title><link rel="stylesheet" href="/app.css"><link rel="stylesheet" href="/pages.css"></head><body><main class="wrap pg"><a class="pg-back" href="/">‹ The drive dashboard</a><header class="pg-h"><div><h1>{title}</h1></div></header><pre class="src-text">{text}</pre></main></body></html>'
STORY_ROUTE = re.compile(r"/api/story/([a-z0-9-]+)")
TRANSCRIPT_ROUTE = re.compile(r"/api/transcript/([a-z0-9-]+)")
ROW_FACTS = ("number", "head", "key", "title", "change", "plan", "risk", "review", "blocker", "md", "block", "state", "ci_fix_go", "checked_at")
CONFLICT = "Conflicts with its base, so it cannot land until its worker rebases it."
UNCHECKED = "GitHub has not yet rechecked whether it merges cleanly."
RELEASED = re.compile(r"^(released|done|live)", re.I)
HELD = re.compile(r"^(hold|held|paused|stopped)", re.I)
WAITS = re.compile(r"^(waits? on|blocked)", re.I)
QUEUED_NEXT = "Lands when the merge queue reaches it."
PR_WORD = {"landed": "landed", "you": "waits on your tap", "failing": "its tests fail", "held": "held", "queued": "approved, waiting to land", "doing": "being worked on", "waiting": "waits its turn"}
TONIGHT_KINDS = {"landed": "Landed", "go": "Approved", "lift": "Hold lifted", "fix-live": "Fix live", "incident": "Broke", "blocker": "Blocked"}
PR_FACTS = "{body: .body, head: .head.sha}"
BOT_COMMENTS = '[.[] | select(.user.type == "Bot") | .body]'
BOT_REVIEW = '[.[] | select(.user.type == "Bot" and .state != "COMMENTED") | {state: .state, at: .submitted_at}] | last'
ELI5 = re.compile(r"^### ELI5\n+(.+?)\s*(?=^### |^<sub>|^<!--|\Z)", re.S | re.M)
BOT_SUMMARY = "<!-- pr-reviewer bot summary -->"
FINDINGS_LEAD = re.compile(r"(This head|(One|Two|Three|Four|Five|\d+) new (non-?blocking |blocking )?findings?\b)", re.I)
BOT_COMPANION = "<!-- pr-reviewer bot companion -->"
SEQUENCE_WORDS = {"actor", "participant", "note", "loop", "alt", "else", "opt", "par", "and", "end", "rect", "critical", "break", "box", "activate", "deactivate", "autonumber", "title", "create", "destroy", "link", "links"}
COMPANION_DIAGRAM = re.compile(r"^### (?!ELI5)(.+)\n+```mermaid\n(.*?)\n```", re.S | re.M)
STORE = Path.home() / ".cc-present" / "cc-interact-v1" / "state.db"
ASKS = {"choice": "choices", "approval": "decisions", "input": "inputs"}
NATIVE = {*ASKS, "record", "display.page"}
OTHER = re.compile(r"^other$", re.I)
MERGE_ONLY = re.compile(r"^(waits? (for|on)|lands? after|after|stacked on)[\s#\d,\-–and]+(to (merge|land))?( first)?\.?$", re.I)
MERGE_CARD = "card-merge-triage"
ACTIVE = dt.timedelta(minutes=30)
WORKING = dt.timedelta(minutes=20)
BOX = dt.timedelta(minutes=5)
BOXES = 12
SEVERITY = {"blocker": "bad", "defect": "bad", "incident": "bad", "ask": "you", "owner": "you", "landed": "ok", "go": "ok", "fix-live": "ok"}
TONE_RANK = {"bad": 3, "you": 2, "ok": 1, "info": 0}
PR_MENTION = re.compile(r"#(4\d{4})\b")
STATIC = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/app.css": ("app.css", "text/css"), "/review.js": ("review.js", "text/javascript"), "/review.css": ("review.css", "text/css"), "/overview.js": ("overview.js", "text/javascript"), "/overview.css": ("overview.css", "text/css"), "/cockpit.css": ("cockpit.css", "text/css"), "/now.js": ("now.js", "text/javascript"), "/now.css": ("now.css", "text/css"), "/pages.js": ("pages.js", "text/javascript"), "/pages.css": ("pages.css", "text/css"), "/theme.css": ("theme.css", "text/css")}


def gh(repo, *args):
    return run(["env", "CCX_CALLER=drive-dashboard", f"GH_REPO={repo}", "ccx", "vcs", "gh", "--max-age", "5m", "--", *args])


def run(argv, cwd=None):
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=True, timeout=60).stdout


def cci(path, **query):
    url = f"{CCI}/{path}?" + "&".join(f"{k}={v}" for k, values in query.items() for v in (values if isinstance(values, list) else [values]))
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)


def drawable(source):
    reserved = {found[1] for found in re.finditer(r"^\s*(?:participant|actor)\s+(\w+)", source, re.M) if found[1].lower() in SEQUENCE_WORDS}
    if not source.lstrip().startswith("sequenceDiagram") or not reserved:
        return source
    name = re.compile(rf"(?<![\w-])({'|'.join(reserved)})(?![\w-])")

    def line(text):
        if declared := re.match(r"(\s*(?:participant|actor)\s+)(\w+)(.*)", text):
            return declared[1] + (f"{declared[2]}_{'' if ' as ' in declared[3] else f' as {declared[2]}'}" if declared[2] in reserved else declared[2]) + declared[3]
        head, colon, tail = text.partition(":")
        return name.sub(r"\1_", head) + colon + tail

    return "\n".join(line(text) for text in source.splitlines())


def bot_words(comments):
    eli5 = next((found[1] for body in comments if (found := ELI5.search(body))), None)
    diagram = next(({"title": found[1].strip(), "source": drawable(found[2])} for body in comments if body.startswith(BOT_COMPANION) and (found := COMPANION_DIAGRAM.search(body))), None)
    summary = next((body for body in comments if body.startswith(BOT_SUMMARY)), "").partition("<details>")[0]
    lead = [part.strip() for part in summary.split("\n\n") if part.strip() and not part.lstrip().startswith((">", "**Since", "<!--", "No new findings")) and not FINDINGS_LEAD.match(part.strip())]
    return {"eli5": eli5, "diagram": diagram, "summary": "\n\n".join(lead) or None}


def plan_prs(text):
    numbers = []
    for low, high in PR_RANGE.findall(text):
        numbers += range(int(low), int(high) + 1)
    numbers += [int(n) for n in PR_REF.findall(PR_RANGE.sub("", text))]
    return list(dict.fromkeys(numbers))


def trees(paths, config):
    found = []
    for path in paths:
        tree = next(tree["name"] for tree in config if path.startswith(tuple(tree["prefixes"])) or not tree["prefixes"])
        if tree not in found:
            found.append(tree)
    return found


def at(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))


def verdict_state(row):
    heads = [row["head"][:7], *(sha[:7] for sha in row.get("carried") or [])]
    verdict = row["verdict"] or {}
    if verdict.get("verdict", "cleared") != "cleared" and (verdict.get("head") or "")[:7] in heads:
        return "approved" if verdict["verdict"] == "approved" else "held"
    return "approved" if (row.get("go_head") or "")[:7] in heads else "you"


def worked_on(record, prs):
    topic = record.get("topic") or ""
    pushed = record["kind"] in ("head", "opened")
    return {*([topic] if re.fullmatch(r"\d{5}", topic) else []), *(n for n in PR_MENTION.findall(record["text"]) if pushed or prs.get(n, {}).get("lane") == record["lane"])}


def activity(records, now, prs):
    seen = {}
    for record in records:
        if COORDINATORS.search(record["lane"]) or now - at(record["at"]) > ACTIVE:
            continue
        for number in worked_on(record, prs):
            if number not in seen or seen[number]["at"] < record["at"]:
                seen[number] = {"lane": record["lane"], "at": record["at"]}
    return seen


def pr_state(pr, row, go_head, active, now):
    if pr.get("state") == "landed":
        return "landed"
    if row and pr.get("head") and not pr["head"].startswith(row["head"][:7]):
        row = None
    if row and row["state"] == "you":
        return "you"
    if pr.get("test_state") == "failure":
        return "failing"
    if (row and row["state"] == "held") or (pr.get("hold_reason") and pr.get("hold_until") and at(pr["hold_until"]) > now):
        return "held"
    if (row and row["state"] == "approved") or (go_head and (pr.get("head") or "").startswith(go_head[:7])):
        return "queued"
    return "doing" if active or pr.get("test_state") == "pending" else "waiting"


def with_releases(history, releases):
    merged = {key: {**entry, "events": list(entry["events"])} for key, entry in history.items()}
    for record in releases:
        events = merged.setdefault("lane:" + re.sub(r"-\d+$", "", record["lane"]), {"events": []})["events"]
        if all(event["ts"] != record["at"] for event in events):
            events.append({"ts": record["at"], "kind": "release", "text": record["text"], "holder": None, "next": None})
            events.sort(key=lambda event: event["ts"], reverse=True)
    return merged


def lean(records):
    return [{k: r[k] for k in CLIENT_RECORD if k in r} for r in records]


def browser_view(model):
    return {**{k: v for k, v in model.items() if k != "words"}, "words_at": max((w["at"] for w in model["words"]), default=None), "records": lean(model["records"]), "lanes": lean(model["lanes"])}


def part_of(title, parts):
    scope = (title or "").split(":", 1)[0].strip()
    return next((part["key"] for part in parts if scope in part["scopes"]), parts[-1]["key"])


def next_action(state, pr, row, owner, base, base_landed, hold):
    who = owner or "Its worker"
    if state == "landed":
        return "Nothing: it has landed."
    if state == "you":
        return f"Your tap on the merge board at {row['head'][:7]}{f', and {who} rebases it onto its base' if pr.get('mergeable_state') == 'dirty' else ''}."
    if hold:
        return f"Held by root: {plain_hold(hold)}."
    if pr.get("mergeable_state") == "dirty":
        return f"{who} rebases it onto its base."
    if state == "failing":
        checks = ", ".join(check["name"] for check in pr.get("failing_checks") or [])
        return f"{who} fixes {checks or 'its failing tests'}."
    if state == "held":
        return f"Held by root: {plain_hold(pr['hold_reason'])}." if pr.get("hold_reason") else "Held by your verdict on the merge board."
    if base and not base_landed:
        return f"Lands after #{base} lands."
    if state == "queued":
        return QUEUED_NEXT
    if state == "doing":
        return f"{who} is working on it."
    return "Waits its turn in its stack."


def plain_hold(text):
    return re.sub(r"\s*\([^()]*#\d{6}[^()]*\)", "", re.sub(r"^(root cci hold:|lane:\S+)\s*", "", text)).rstrip(". ")


def hold_reasons(entries, prs):
    return {pr: re.sub(r"^#\d+\s+", "", next(entry.line for entry in entries if pr in holds.held([entry], prs))) for pr in holds.held(entries, prs)}


def drive_prs(prs, rows, gos, records, trees, tree_names, steps, parts, holds, now):
    by_number = {str(row["number"]): row for row in rows}
    active = activity(records, now, prs)
    spoke = {}
    said = {}
    for record in sorted(records, key=lambda record: (at(record["at"]), record["seq"])):
        spoke[record["lane"]] = max(spoke.get(record["lane"], ""), record["at"])
        for number in {*PR_MENTION.findall(record["text"]), *([record["topic"]] if re.fullmatch(r"\d{5}", record.get("topic") or "") else [])}:
            said[(record["lane"], number)] = {"seq": record["seq"], "at": record["at"], "text": record["text"][:600]}
    by_branch = {pr["branch"]: number for number, pr in prs.items() if pr.get("state") == "open" and pr.get("branch")}
    listed = []
    for number, pr in sorted(prs.items()):
        if pr.get("state") not in ("open", "landed", None):
            continue
        found = trees.get(number, [])
        base = by_branch.get(pr.get("base"))
        inherited = base and pr.get("test_state") == "failure" and prs[base].get("test_state") == "failure"
        row = by_number.get(number)
        state = pr_state({**pr, "test_state": None} if inherited else pr, row, gos.get(int(number)), active.get(number), now)
        owner = (active.get(number) or {}).get("lane") or pr.get("lane")
        blocker = row["blocker"] if row and row.get("blocker") and not re.match(r"nothing", row["blocker"], re.I) else CONFLICT if pr.get("mergeable_state") == "dirty" else None
        listed.append({
            "number": int(number),
            "state": state,
            "base": int(base) if base else None,
            "base_failing": bool(base) and prs[base].get("test_state") == "failure",
            "tree": next((name for name in tree_names if name in found), tree_names[-1]),
            "part": part_of(pr.get("title"), parts),
            "step": next((step["id"] for step in steps if int(number) in step["prs"]), None),
            "worker": active.get(number),
            "head": (pr.get("head") or "")[:7],
            "owner": owner,
            "owner_at": spoke.get(owner),
            "word": said.get((owner, number)),
            "blocker": blocker,
            "checks": pr.get("failing_checks") or [],
            "next": next_action(state, pr, row, owner, base, bool(base) and prs[base].get("state") == "landed", holds.get(number)),
            "read_at": pr.get("last_refresh"),
        })
    return {"source": "every pull request in the drive's ledger that is open or landed, since the drive began on Oct 22", "prs": listed, "tracked": sorted(int(number) for number in prs)}


def still_open(number, prs, runner):
    return prs.get(str(number), {}).get("state") == "open" and runner.get(str(number), {}).get("state") != "Landed"


def without_finished_prs(pages, prs, runner):
    work = pages.get("work")
    if not work:
        return pages
    return {**pages, "work": {**work, "pull_requests": [row for row in work["pull_requests"] if still_open(row["number"], prs, runner)]}}


def prose(text):
    """`text` as the paragraphs a reader follows: each opening clock label, `pr:` tag and file path dropped, and a paragraph of `- ` lines kept as a list."""
    blocks = []
    for part in re.split(r"\n\s*\n", FILE_PATH.sub("", PR_TAG.sub("", text))):
        lines = [CLOCK_OPENING.sub("", line).strip() for line in part.strip().splitlines()]
        if not any(lines):
            continue
        if all(line.startswith(("- ", "* ")) for line in lines if line):
            blocks.append("<ul>" + "".join(f"<li>{html.escape(line[2:])}</li>" for line in lines if line) + "</ul>")
        else:
            blocks.append(f"<p>{html.escape(' '.join(line for line in lines if line))}</p>")
    return blocks


def record_body(record):
    """The whole body `record` carries: its text, then the file it names, or the file alone when the text is that file's opening cut short."""
    text, path = record["text"], Path(record.get("refs", {}).get("path") or "/nonexistent")
    if not path.is_file():
        return text
    file = path.read_text()
    return file if file.startswith(text.rstrip("…").rstrip()) or file.splitlines()[:1] == [text] else f"{text}\n\n{file}"


def record_page(record, asked, said):
    """The record a dashboard link opens, as prose: the owner's question in his words when `record` answers one, the body as paragraphs, who said it and when on one small line, and the raw record behind a link (root #203757)."""
    who = "You" if record["kind"] == "owner" or record["lane"] in OWNER_LANES else record["lane"]
    when = at(record["at"]).astimezone(PACIFIC).strftime("%-I:%M %p")
    question = asked and " ".join(FILE_PATH.sub("", PR_TAG.sub("", CLOCK_OPENING.sub("", record_body(asked)))).removeprefix(ASKED_PREFIX).split())
    title = question or f"What {who} said"
    text = record_body(record)
    body = prose(text)
    lead = f'<p class="pg-rel">{html.escape(said)}</p>' if said and (len(body) > 3 or len(text) > 600) else ""
    byline = f"{who} {'answered' if asked else 'wrote this'} at {when}"
    return RECORD_PAGE.format(title=html.escape(title), byline=html.escape(byline), lead=lead, body="".join(body), seq=record["seq"])


def answer_line(text, who=None, since=None, next=None, href=None, label=None):
    return {"text": text, "who": who, "since": since, "next": next, "source": {"label": label, "href": href} if href else None}


def pr_line(entry, prs):
    number = str(entry["number"])
    pr = prs.get(number, {})
    clears = pr.get("queue_clears") if entry["state"] == "queued" else None
    blocker = f". {entry['blocker']}" if entry["blocker"] else ""
    return answer_line(
        f"#{number} {pr.get('title', '')}: {PR_WORD[entry['state']]}{blocker}",
        who=entry["owner"], since=(entry["word"] or {}).get("at"), next=clears or entry["next"], href=f"#/review/{number}", label=f"#{number}",
    )


def journeys(drive, steps, releases, needs, records, prs, progress_now, tree_names, now):
    by_number = {entry["number"]: entry for entry in drive["prs"]}
    open_entry = lambda number: by_number.get(number) if by_number.get(number, {}).get("state") not in (None, "landed") else None

    stop = next((release for release in releases if release.get("word") in ("now", "blocked", "held")), None)
    release_lines, release_missing = [], []
    for release in releases:
        if release.get("word") not in ("now", "blocked", "held"):
            continue
        if release["word"] == "held":
            release_lines.append(answer_line(f"{release['id']} {release['name']} is held: {release['state']}", who="you", href="#/releases", label="Release plan"))
            break
        named = plan_prs(release["state"])
        blockers = [entry for entry in map(open_entry, named) if entry] if named else [entry for entry in drive["prs"] if entry["state"] != "landed" and entry["tree"] == tree_names[0] and entry["part"] in release.get("parts", [])]
        release_lines.append(answer_line(f"{release['id']} {release['name']}: {release['state']}", href="#/releases", label="Release plan"))
        release_lines += [pr_line(entry, prs) for entry in blockers]
        release_missing += [f"#{entry['number']}: what clears its place in the merge queue (landing-desk queue_clears)" for entry in blockers if entry["next"] == QUEUED_NEXT and not prs.get(str(entry["number"]), {}).get("queue_clears")]

    tap = needs[0] if needs else None
    tap_lines = [answer_line(
        {"prs": lambda: f"{len(tap['prs'])} pull request{'s' if len(tap['prs']) > 1 else ''} in {tap.get('group')}: approve at the commit shown", "card": lambda: tap["card"]["title"], "ask": lambda: tap["ask"]["what"]}[tap["kind"]](),
        who="you", next=tap["why"], href=(tap["source"] or {}).get("href") or "#/now", label=(tap["source"] or {}).get("label"),
    )] if tap else [answer_line("Nothing needs you right now.", href="#/now", label="Needs you")]

    red = next((step for step in steps if step["tone"] == "failing"), None)
    red_entries = [by_number[item["number"]] for item in (red or {}).get("prs", []) if item["state"] == "failing"]
    failed = lambda checks: "; ".join(": ".join(filter(None, (check["name"], check.get("error")))) for check in checks) or "its tests fail"
    red_lines = [answer_line(
        f"#{entry['number']} {prs.get(str(entry['number']), {}).get('title', '')}: {failed(entry['checks'])}",
        who=entry["owner"], since=(entry["word"] or {}).get("at"), next=entry["next"], href=(entry["checks"][0]["url"] if entry["checks"] else f"#/review/{entry['number']}"), label="failing check" if entry["checks"] else f"#{entry['number']}",
    ) for entry in red_entries]
    red_missing = [f"#{entry['number']} {check['name']}: its first error line (ledger writer failing_checks error)" for entry in red_entries for check in entry["checks"] if not check.get("error")]

    since = now - dt.timedelta(hours=6)
    happened = []
    for number, pr in prs.items():
        if pr.get("landed_at") and at(pr["landed_at"]) >= since:
            freed = [entry["number"] for entry in drive["prs"] if entry["base"] == int(number) and entry["state"] != "landed"]
            done = [step["label"] for step in steps if step["tone"] == "done" and number == max((str(item["number"]) for item in step["prs"]), key=lambda n: prs.get(n, {}).get("landed_at") or "", default=None)]
            happened.append((pr["landed_at"], answer_line(
                f"#{number} {pr.get('title', '')} landed",
                since=pr["landed_at"], next="; ".join([*(f"#{n} can now land" for n in freed), *(f"finishes step {label}" for label in done)]) or None, href=f"#/review/{number}", label=f"#{number}",
            )))
    latest = {}
    for record in records:
        if record["kind"] not in TONIGHT_KINDS or record["kind"] == "landed" or at(record["at"]) < since:
            continue
        approved = GO.match(record["text"]) if record["kind"] == "go" else None
        if record["kind"] == "go" and not (approved and approved[1] in prs):
            continue
        if record["kind"] == "blocker" and not ("root" in record.get("to", []) and any(n in prs for n in PR_MENTION.findall(record["text"]))):
            continue
        latest[(record["kind"], approved[1] if approved else record["seq"])] = (record, approved)
    for record, approved in latest.values():
        named = [n for n in dict.fromkeys(PR_MENTION.findall(record["text"])) if n in prs]
        happened.append((record["at"], answer_line(
            f"Approved #{approved[1]} {prs[approved[1]].get('title', '')} at {approved[2]}" if approved else f"{TONIGHT_KINDS[record['kind']]}: {record['text'][:240]}",
            who=record["lane"], since=record["at"],
            next=f"#{approved[1]} can land" if approved else f"lets {', '.join(f'#{n}' for n in named[:4])} move" if named and record["kind"] in ("lift", "fix-live") else None,
            href=f"/record/{record['seq']}", label=f"record #{record['seq']}",
        )))

    new_tree = [entry for entry in drive["prs"] if entry["tree"] == tree_names[0]]
    tonight = [release for release in releases if release.get("tonight")]
    proof = progress_now.get("preservation")
    held = next((release for release in releases if release.get("word") == "held"), None)
    gates = [release for release in (stop, held) if release][: 1 if stop is held else 2]
    track_lines = [
        answer_line(f"{sum(entry['state'] == 'landed' for entry in new_tree)} of {len(new_tree)} new-app changes merged", href="#/", label="the plan"),
        answer_line(f"{sum(release.get('word') == 'out' for release in tonight)} of {len(tonight)} releases out tonight", href="#/releases", label="Release plan"),
        *([answer_line(f"Old app and flagged users: {proof['result']}", since=proof.get("checked_at"), next=proof.get("how"), href=f"/record/{proof['link'][1:]}" if re.fullmatch(r"#\d+", proof.get("link") or "") else proof.get("link"), label="proof")] if proof else []),
        *[answer_line(f"Tonight's {'stop' if release['word'] == 'held' else 'gate'}: {release['id']} {release['name']}, {release['state']}", who="you" if release["word"] == "held" else None, href="#/releases", label="Release plan") for release in gates],
    ]

    labels = {step["id"]: step["label"] for step in steps}
    released = {(line["source"] or {}).get("href") for line in release_lines}
    waits = [
        *([answer_line(f"{len(needs)} decisions in Needs you", who="you", next="those pull requests and questions move", href="#/now", label="Needs you")] if needs else []),
        *[line for line in release_lines if line["who"]],
        *[answer_line(f"#{entry['number']} {prs.get(str(entry['number']), {}).get('title', '')}: {entry['next']}", who=entry["owner"], since=(entry["word"] or {}).get("at"), next=f"unblocks step {labels[entry['step']]}" if entry["step"] in labels else None, href=f"#/review/{entry['number']}", label=f"#{entry['number']}") for entry in drive["prs"] if entry["state"] == "held" and f"#/review/{entry['number']}" not in released],
        *[answer_line(item.get("what", ""), who=item.get("owed_by"), since=item.get("since"), next=item.get("unblocks"), href=item.get("link"), label=item.get("link") and "source") for item in progress_now.get("waiting", [])],
    ]

    stamp = now.isoformat()
    return {
        "release": {"question": f"What is blocking {stop['id'] if stop else 'the next release'}, and why?", "lines": release_lines, "missing": release_missing, "as_of": stamp},
        "tap": {"question": "What do I tap now?", "lines": tap_lines, "missing": [], "as_of": stamp},
        "red": {"question": f"Why is {red['label'] if red else 'a step'} red, and who is on it?", "lines": red_lines, "missing": red_missing, "as_of": stamp},
        "since": {"question": "What happened in the last six hours?", "lines": [line for _, line in sorted(happened, key=lambda pair: at(pair[0]), reverse=True)], "missing": [], "as_of": stamp},
        "track": {"question": "Is tonight's goal on track?", "lines": track_lines, "missing": [] if proof else ["the latest proof that the old app and flagged users still work (progress-desk now.preservation)"], "as_of": stamp},
        "waiting": {"question": "What is everyone waiting on right now?", "lines": waits, "missing": [], "as_of": stamp},
    }


def drive_workers(records, prs, steps, now, desks=False):
    live = {}
    for record in records:
        age = now - at(record["at"])
        if bool(COORDINATORS.search(record["lane"])) != desks or age >= BOX * BOXES:
            continue
        worker = live.setdefault(record["lane"], {"lane": record["lane"], "at": record["at"], "prs": set(), "boxes": ["" for _ in range(BOXES)]})
        worker["at"] = max(worker["at"], record["at"])
        if age <= WORKING:
            worker["prs"].update(int(n) for n in worked_on(record, prs))
        slot = BOXES - 1 - int(age / BOX)
        tone = SEVERITY.get(record["kind"], "info")
        if TONE_RANK[tone] >= TONE_RANK.get(worker["boxes"][slot], -1):
            worker["boxes"][slot] = tone
    workers = []
    for worker in sorted(live.values(), key=lambda w: w["at"], reverse=True):
        prs = sorted(worker["prs"])
        step = next((s["id"] for s in steps if worker["lane"] in s["lanes"] or set(prs) & set(s["prs"])), None)
        workers.append({**worker, "prs": prs, "step": step, "live": now - at(worker["at"]) <= WORKING})
    return workers


def step_tone(step, prs, workers, untracked):
    states = {entry["state"] for entry in prs}
    if prs and states == {"landed"} and (not untracked or step["tone"] == "done"):
        return "done"
    for tone in ("you", "failing", "held"):
        if tone in states:
            return tone
    if "doing" in states or workers:
        return "moving"
    if prs and states <= {"landed", "queued"} and not untracked:
        return "queued"
    return "waiting" if prs else step["tone"]


def step_rows(steps, drive, workers, answered):
    by_number = {entry["number"]: entry for entry in drive["prs"]}
    tracked = set(drive["tracked"])
    rows = []
    for step in steps:
        if step.get("card") in answered:
            step = {**step, "blocker": None}
        named = [int(n) for n in PR_MENTION.findall(step.get("blocker") or "")]
        prs = [by_number[n] for n in dict.fromkeys([*step["prs"], *named]) if n in by_number]
        on = [{"lane": w["lane"], "at": w["at"], "prs": w["prs"]} for w in workers if w["live"] and w["step"] == step["id"]]
        untracked = [n for n in dict.fromkeys([*step["prs"], *named]) if n not in tracked]
        rows.append({**step, "prs": [{"number": entry["number"], "state": entry["state"]} for entry in prs], "untracked": untracked, "tone": step_tone(step, prs, on, untracked), "workers": on})
    return rows


def release_path(releases):
    stop = next((i for i, release in enumerate(releases) if HELD.match(release["state"])), len(releases))
    upcoming = next((i for i, release in enumerate(releases) if i < stop and not RELEASED.match(release["state"])), None)
    def word(i, release):
        if RELEASED.match(release["state"]):
            return "out"
        if i == stop:
            return "held"
        if i > stop:
            return "unreachable"
        if i == upcoming:
            return "blocked" if WAITS.match(release["state"]) else "now"
        return "after"
    return [{**release, "word": word(i, release)} for i, release in enumerate(releases)]


def paths_current(cached, pr):
    return bool(cached) and (cached["head"] == pr["head"] or cached.get("files") == pr.get("changed_files"))


def with_conflicts(rows, prs):
    def blocker(row):
        board = row.get("blocker") or ""
        named = plan_prs(board)
        if named and MERGE_ONLY.match(board.strip()) and all(prs.get(str(n), {}).get("state") == "landed" for n in named):
            board = f"Nothing now: {', '.join(f'#{n}' for n in named)} landed."
        merge = prs.get(str(row["number"]), {}).get("mergeable_state")
        clear = not board or re.match(r"nothing", board, re.I)
        if merge == "dirty":
            return CONFLICT if clear else f"{CONFLICT} It also waits on: {board}"
        if merge == "unknown" and clear:
            return f"{UNCHECKED} The merge board said: {board}"
        return board
    return [{**row, "blocker": blocker(row)} for row in rows]


def pushed_at(records, number, head):
    """When the newest `head` or `opened` record naming `#number` at `head` was posted, or None."""
    return max((record["at"] for record in records if record["kind"] in ("head", "opened") and f"#{number}" in record["text"] and head[:7] in record["text"]), default=None)


def live_evidence(evidence, prs, runner, records):
    """The progress desk's evidence for each open PR, unless the PR's live head was pushed after it was written, so no line read at an older version reaches the owner (root #203813)."""
    return {number: entry for number, entry in evidence.items() if still_open(number, prs, runner) and entry["at"] >= (pushed_at(records, number, prs[number].get("head") or "") or "")}


def at_live_head(rows, prs, records):
    """Each board row waiting on the owner's tap, re-read at its PR's live head: one whose tests fail or have not passed there, or whose parent's tests fail, leaves the tap deck as `failing` and says why until that head is green (root #203813)."""
    by_branch = {pr["branch"]: number for number, pr in prs.items() if pr.get("state") == "open" and pr.get("branch")}

    def pushed(number, head):
        found = pushed_at(records, number, head)
        return f" pushed at {at(found).astimezone(PACIFIC).strftime('%-I:%M %p')}" if found else ""

    def held_back(row):
        if not (pr := prs.get(str(row["number"]))):
            return None
        head = pr.get("head") or row["head"]
        if pr.get("test_state") == "failure":
            return f"Tests failing on the version{pushed(row['number'], head)}; back when green."
        if pr.get("test_state") != "success":
            return f"Tests still running on the version{pushed(row['number'], head)}; back when green."
        if (parent := by_branch.get(pr.get("base"))) and prs[parent].get("test_state") == "failure":
            return f"#{parent} below it fails its tests; back when green."
        return None

    return [{**row, "state": "failing", "blocker": why} if row["state"] == "you" and (why := held_back(row)) else row for row in rows]


def merge_needs(rows, board_url):
    groups = {}
    for row in rows:
        group = groups.setdefault((row["block"], row["group_id"]), {"group": row["group"], "relation": row["group_summary"], "rows": []})
        group["rows"].append({key: row.get(key) for key in ROW_FACTS})
    items = []
    for group in groups.values():
        waiting = [row for row in group["rows"] if row["state"] == "you"]
        if not waiting:
            continue
        count = f"{len(waiting)} pull request{'' if len(waiting) == 1 else 's'}"
        items.append({
            "kind": "prs",
            "key": source_key(group["group"], group["relation"], [row["key"] for row in waiting]),
            "group": group["group"],
            "relation": group["relation"],
            "prs": waiting,
            "others": [row for row in group["rows"] if row["state"] != "you"],
            "why": f"The merge board waits on your tap for {count} in “{group['group']}”; each merges only at the commit shown, and it is due now.",
            "source": {"label": f"Merge board: {group['group']}", "href": board_url},
        })
    return items


def card_need(card, board_url):
    recommended = f" Recommended: {card['recommended']}." if card["recommended"] else ""
    return {"kind": "card", "card": card, "why": f"The drive's board asks you this and it is open now. {card['summary']}{recommended}".strip(), "source": {"label": f"Board card: {card['title']}", "href": board_url}}


def ask_need(ask):
    record = ask.get("record")
    if not record:
        return {"kind": "ask", "ask": ask, "why": f"No drive record asked for this; only the progress desk's list carries it. {ask['why']}", "source": {"label": "No record behind this ask", "href": None}}
    return {"kind": "ask", "ask": ask, "why": f"{record['lane']} asked in #{record['seq']}, due now: {ask['why']}", "source": {"label": "Open the record", "href": f"/record/{record['seq']}"}}


def picked_words(child, answer):
    if child["type"] == "choice":
        labels = {option["id"]: option["label"] for option in child.get("options", [])}
        return ", ".join([*(labels.get(option, option) for option in answer["optionIds"]), *([answer["other"]] if answer.get("other") else [])])
    return answer["verdict"] if child["type"] == "approval" else answer["text"]


def stale_waits(milestone_rows, steps, answered, now):
    late = {card for card, answer in answered.items() if now - at(answer["at"]) >= PICK_GRACE}
    found = []
    for row in milestone_rows:
        for pick in row["picked"]:
            if pick["card"] in late:
                found.append((f"blocker:{pick['seq']}:{pick['card']}", (pick["lane"],), f"milestone {row['release']} blocker #{pick['seq']} still waits on board card {pick['card']}, answered at {milestones.clock(pick['at'])} ({pick['picked']}); post its clearing blocker record with --attr cleared=yes now"))
    for step in steps:
        if (card := step.get("card")) in late:
            found.append((f"step:{step['id']}:{card}", (), f"step {step['id']} in steps.json still waits on board card {card}, answered at {milestones.clock(answered[card]['at'])} ({answered[card]['picked']}); rewrite it without that wait now"))
    return found


def card_part(child, answer):
    kind = child["type"]
    if kind == "record":
        return {"kind": "record", "id": child["id"], "title": child.get("title", ""), "facts": child.get("facts", [])}
    if kind == "display.page":
        return {"kind": "page", "id": child["id"], "md": child.get("md", "")}
    if kind == "choice":
        options = [{"id": option["id"], "label": option["label"], "hint": option.get("hint", ""), "facts": option.get("facts", []), "detail": option.get("detail") or {}, "recommended": bool(option.get("recommended"))} for option in child.get("options", []) if not OTHER.match(option["label"])]
        return {"kind": "choice", "id": child["id"], "prompt": child.get("prompt", ""), "options": options, "answer": answer}
    if kind == "approval":
        return {"kind": "approval", "id": child["id"], "prompt": child.get("prompt", ""), "detail": child.get("detail") or {}, "answer": answer}
    return {"kind": "input", "id": child["id"], "label": child.get("label", ""), "placeholder": child.get("placeholder", ""), "multiline": bool(child.get("multiline")), "answer": answer}


def unanswered(cards, words):
    answered = {card for word in words for card in CARD_ID.findall(word["title"])}
    return [card for card in cards if card["id"] not in answered]


def needs(rows, cards, asks, board_url, landed):
    return [*merge_needs([row for row in rows if row["number"] not in landed], board_url), *(card_need(card, board_url) for card in cards if card["decides"]), *(ask_need(ask) for ask in asks if not ask.get("optional") and ask.get("card") in {None, *(card["id"] for card in cards)})]


def open_findings(rows):
    groups = {}
    for fields in sorted((row["fields"] for row in rows if row["fields"]["state"] in UNSETTLED), key=lambda fields: fields["at"]):
        groups.setdefault(fields["sweep"], []).append(fields)
    return [{"sweep": sweep, "rows": found} for sweep, found in groups.items()]


def parse_plan(body):
    if PLAN_HEADING not in body:
        return None
    updated = next(row.split(": ", 1)[1] for row in body.splitlines() if row.startswith("updated: "))
    section = body.split(PLAN_HEADING, 1)[1].split("\n", 1)[1].split("\n## ", 1)[0]
    steps = []
    for row in section.splitlines():
        if found := PLAN_ITEM.match(row.strip()):
            lead, _, rest = found[1].partition("** ")
            steps.append({"title": lead.strip("*").rstrip(":. ") if rest else found[1], "text": rest, "prs": plan_prs(found[1])})
    return updated, steps


class Drive:
    def __init__(self, config, assistant):
        self.config = config
        self.assistant = assistant
        self.lock = threading.Lock()
        self.changed = threading.Condition(self.lock)
        self.records = deque(maxlen=4000)
        self.latest_words = {}
        self.milestone_cache = None
        self.evidence = {}
        self.version = 0
        self.model = {}
        self.serving = threading.Condition()
        self.served = None
        self.live_model = None
        self.slow = {}
        self.slow_at = 0.0
        self.dirty_at = time.monotonic()
        self.errors = {}
        self.paths = {}
        self.board_seq = None
        self.reported_build = None
        self.reported_live = None
        self.flagged = set()
        self.board_times = {}
        self.gos = {}
        self.carried = {}
        self.ci_fixes = set()
        self.board_raw = None
        self.curator = Curator(config, self.curator_model, self.bump)
        self.status_now = None
        self.status_rejected = None
        self.pages = {page.name: Curator(config, self.curator_model, self.bump, page) for page in PAGES}
        self.curators = (self.curator, *self.pages.values())
        self.stories = Stories(config, self.lane_names, self.worker_reports)

    def curator_model(self):
        while "prs" not in self.model:
            time.sleep(1)
        with self.serving:
            self.serving.wait_for(lambda: self.live_model is not None)
            live = self.live_model
        return {**live, "fetch": self.fetch}

    def lane_names(self):
        with self.lock:
            return tuple({record["lane"] for record in self.records})

    def worker_reports(self, workers):
        with self.lock:
            mine = next((found for lane in workers if (found := [r for r in self.records if re.sub(r"-\d+$", "", r["lane"]) == re.sub(r"-\d+$", "", lane) and r["kind"] not in ("state", "review")])), [])
        return [f"[{dt.datetime.fromisoformat(r['at'].replace('Z', '+00:00')).astimezone(PACIFIC).strftime('%-I:%M %p')}] {r['kind']}: {r['text'][:600]}" for r in mine[-12:]]

    def follow_mirror(self):
        while True:
            try:
                with urllib.request.urlopen(f"{self.config['mirror']}/api/model?full=1", timeout=60) as response:
                    model = json.load(response)
            except urllib.error.URLError:
                time.sleep(30)
                continue
            self.curator.brief = model["brief"]
            for name, curator in self.pages.items():
                curator.brief = model["pages"][name]
            self.touch()
            self.bump()
            time.sleep(30)

    def bump(self):
        with self.changed:
            self.version += 1
            self.changed.notify_all()

    def work(self, item):
        work = self.pages["work"].brief or {}
        finished = [{**w, "status": "Finished", "latest": w["result"]} for w in work.get("finished", [])]
        found = next((w for w in [*(self.curator.brief or {}).get("work", []), *work.get("running", []), *finished] if w["item"] == item), None)
        return found and {**found, "workers": list(dict.fromkeys([*(lane for n in found.get("changes", []) if (lane := self.model["prs"].get(str(n), {}).get("lane"))), *found["workers"]]))}

    def review_url(self, number):
        pr = self.model["prs"].get(str(number), {})
        row = next((r for r in self.board()["rows"] if r["number"] == number), {})
        return self.assistant.review_url(number, pr.get("head") or row.get("head"), pr.get("state") == "landed")

    def decide(self, block, number, verdict, head, note):
        state = json.loads(run(["cc-present", "outcomes", "--block", block, "--session", self.config["board_session"]]))
        shown = next((row["head"] for card in state["doc"]["blocks"] for child in card.get("children", []) if child["id"] == block for group in child["groups"] for row in group["prs"] if row["number"] == number), None)
        given = state["interactions"]["triage"].get(block, {}).get(str(number))
        if shown is None and given and given["verdict"] == verdict != "cleared" and given["head"][:7] == head[:7]:
            return 200, {"seq": self.store_seq(), "recorded": given["at"]}
        if shown is None:
            return 409, {"error": f"#{number} left your board after this page last updated, so this tap changed nothing; reload the page to see where it went"}
        if verdict != "cleared" and shown[:7] != head[:7]:
            return 409, {"error": f"#{number} is now at {shown[:7]}, not the {head[:7]} this page shows, so this tap changed nothing; reload the page to see what changed"}
        entry = {"verdict": verdict} if verdict == "cleared" else {"verdict": verdict, "head": head, **({"note": note} if note else {})}
        return self.interact({"type": "triage.decided", "blockId": block, "verdicts": {str(number): entry}})

    def answer(self, body):
        if body["kind"] == "choice":
            return self.interact({"type": "choice.selected", "blockId": body["block"], "optionIds": body.get("options", []), **({"other": body["other"]} if body.get("other") else {})})
        if body["kind"] == "approval":
            return self.interact({"type": "decision.created", "blockId": body["block"], "verdict": body["verdict"], **({"note": body["text"]} if body.get("text") else {})})
        if body["kind"] == "input":
            return self.interact({"type": "input.submitted", "blockId": body["block"], "text": body["text"]})
        return self.interact({"type": "feedback.created", "id": str(uuid.uuid4()), "blockId": body["block"], "text": body["text"]})

    def asked(self, text):
        lane, kind = ("dashboard-ask-test", "note") if text.startswith("[user-test") else ("dashboard", "owner")
        run(["cci", "post", "--drive", self.config["drive"], "--lane", lane, "--kind", kind, "--to", "root", "--text", f"Asked on the dashboard: {text}"])
        return 200, {}

    def change_request(self, number, head, text):
        if "mirror" in self.config:
            return 409, {"error": "a test copy sends no change requests"}
        seq = run(["cci", "post", "--drive", self.config["drive"], "--lane", "owner-board", "--kind", "ask", "--to", "root", "--pr", str(number), "--attr", f"head={head}", "--text", text]).strip().splitlines()[-1].lstrip("#")
        return 200, {"seq": int(seq)}

    def interact(self, interaction):
        origin, _, subject = self.config["board_url"].partition("/p/")
        request = urllib.request.Request(f"{origin}/api/interactions", method="POST", headers={"Content-Type": "application/json"}, data=json.dumps({"subject": subject, "nonce": str(uuid.uuid4()), "interaction": interaction}).encode())
        try:
            with urllib.request.urlopen(request, timeout=15):
                pass
        except urllib.error.HTTPError as error:
            return error.code, {"error": error.read().decode().strip()}
        return 200, {"seq": self.store_seq()}

    def ccn(self, *args):
        return run(["ccn", *args], cwd=self.config["checkout"])

    def register_log(self):
        since = time.time() - REGISTER_DAY.total_seconds()
        entries = sorted((f for f in Path(self.config["register"]).glob("announce-*.md") if f.stat().st_mtime > since), key=lambda f: f.stat().st_mtime, reverse=True)[:REGISTER_SHOWN]
        return "\n\n\n".join(f.read_text().strip() for f in entries) or "Nothing registered in the last day."

    def posted(self, lane):
        found, query = [], {"drive": self.config["drive"], "lane": lane, "limit": 500, "since_time": (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")}
        while len(page := cci("records", **query)) == 500:
            found, query = page, {"drive": self.config["drive"], "lane": lane, "limit": 500, "since": page[-1]["seq"]}
        records = [*found, *page]
        return [{k: r[k] for k in ("seq", "kind", "at")} for r in ([r for r in records if r["kind"] != "state"] or records)[-8:][::-1]]

    def milestone_state(self, drive_prs, now, answered):
        if not self.milestone_cache or time.monotonic() - self.milestone_cache[0] > MILESTONE_TTL:
            self.milestone_cache = (time.monotonic(), self.fetch({"blocker"}, now - dt.timedelta(days=7)), self.fetch({"release"}, now - dt.timedelta(days=14)))
        return milestones.compute(self.config["milestones"], drive_prs, self.milestone_cache[1], self.milestone_cache[2], answered)

    def fetch(self, kinds, since):
        found, after = [], 0
        while True:
            query = {"drive": self.config["drive"], "kind": sorted(kinds), "limit": 500}
            query.update({"since": after} if after else {"since_time": since.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
            page = cci("records", **query)
            found += page
            if len(page) < 500:
                return found
            after = page[-1]["seq"]

    def note_word(self, record):
        if record["lane"].startswith(WORD_BYSTANDERS):
            return
        refs = {re.search(r"(\d+)$", str(ref))[1] for ref in (record.get("refs") or {}).get("prs") or []}
        with self.lock:
            for number in refs | set(PR_NAMED.findall(record["text"])):
                self.latest_words[number] = {k: record[k] for k in ("seq", "at", "lane", "kind")}

    def words(self):
        with self.lock:
            return dict(self.latest_words)

    def note_evidence(self, record):
        attrs = (record.get("fields") or {}).get("attrs") or {}
        if record["kind"] != "evidence" or "state" not in attrs:
            return
        with self.lock:
            for number in (record.get("refs") or {}).get("prs") or []:
                self.evidence[str(number)] = {"state": attrs["state"], "owner": attrs["owner"], "since": attrs["since"], "next": attrs.get("next", ""), "at": record["at"], "seq": record["seq"]}

    def evidences(self):
        with self.lock:
            return dict(self.evidence)

    def load_records(self):
        self.records.extend(self.fetch(TIMELINE_KINDS, dt.datetime.now(dt.timezone.utc) - TIMELINE_WINDOW))
        for record in self.records:
            self.note_word(record)
            self.note_evidence(record)
        return self.records[-1]["seq"] if self.records else 0

    def follow_records(self, after):
        while True:
            try:
                url = f"{CCI}/stream?drive={self.config['drive']}&since={after}&" + "&".join(f"kind={k}" for k in TIMELINE_KINDS)
                with urllib.request.urlopen(url, timeout=None) as stream:
                    self.errors.pop("records", None)
                    for line in stream:
                        if line.startswith(b"data: "):
                            record = json.loads(line[6:])
                            after = record["seq"]
                            self.note_go(record)
                            self.note_word(record)
                            self.note_evidence(record)
                            with self.lock:
                                self.records.append(record)
                            self.touch(slow=record["kind"] in LEDGER_KINDS)
                            for curator in self.curators:
                                curator.poke(record["kind"])
            except OSError as error:
                self.errors["records"] = f"{type(error).__name__}: {error}"
                time.sleep(5)

    def follow_board(self):
        while True:
            self.watch = subprocess.Popen(["cc-present", "watch", "--session", self.config["board_session"]], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            for _ in self.watch.stdout:
                self.touch()
                for curator in self.curators:
                    curator.poke()
            self.watch.wait()
            time.sleep(5)

    def follow_steps(self):
        steps = Path(self.config["steps"])
        seen = None
        while True:
            stamp = steps.stat().st_mtime_ns
            if stamp != seen:
                seen = stamp
                self.touch()
                for curator in self.curators:
                    curator.poke()
            time.sleep(1)

    def follow_status(self):
        status = Path(self.config["status_now"])
        seen = None
        while True:
            stamp = status.stat().st_mtime_ns if status.exists() else None
            if stamp != seen:
                seen = stamp
                fresh = json.loads(status.read_text()) if stamp else None
                lanes = self.lane_names()
                problems = [f"{p['label']} has {', '.join(found)}" for p in (fresh or {}).get("paragraphs", []) if (found := plainwords.violations(p["text"], "sentence", lanes))]
                if problems:
                    self.status_rejected = {"at": dt.datetime.now(dt.timezone.utc).isoformat(), "reason": "; ".join(problems)}
                    print(f"status-now rejected: {self.status_rejected['reason']}", file=sys.stderr, flush=True)
                else:
                    self.status_now, self.status_rejected = fresh, None
                    self.curator.poke()
                self.bump()
            time.sleep(1)

    def follow_code(self):
        sources = [*HERE.glob("*.py")]
        seen = {path: path.stat().st_mtime_ns for path in sources}
        while all(path.stat().st_mtime_ns == stamp for path, stamp in seen.items()):
            time.sleep(2)
        self.stories.stop_all()
        for pid in subprocess.run(["pgrep", "-P", str(os.getpid()), "-f", "^cc-(present|review) watch "], capture_output=True, text=True).stdout.split():
            os.kill(int(pid), signal.SIGTERM)
        os.execv(sys.executable, [sys.executable, *sys.argv])

    def follow_paths(self):
        cache_file = Path(self.config["paths_cache"])
        cache = json.loads(cache_file.read_text()) if cache_file.exists() else {}
        while True:
            with self.lock:
                prs = dict(self.slow.get("prs", {}))
            stale = [(n, pr["head"], pr.get("changed_files")) for n, pr in prs.items() if pr.get("head") and pr.get("state") in ("open", "landed") and not paths_current(cache.get(n), pr)]
            for number, head, files in stale:
                try:
                    paths = gh(self.config["repo"], "api", f"repos/{self.config['repo']}/pulls/{number}/files", "--paginate", "--jq", ".[].filename").split()
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                    self.errors["paths"] = f"#{number}: {str(error)[:200]}"
                    continue
                cache[number] = {"head": head, "files": files, "trees": trees(paths, self.config["trees"])}
                cache_file.write_text(json.dumps(cache))
                with self.lock:
                    self.paths = dict(cache)
                time.sleep(0.5)
            with self.lock:
                self.paths = dict(cache)
            if stale:
                self.touch()
            time.sleep(60)

    def touch(self, slow=False):
        with self.lock:
            self.dirty_at = self.dirty_at or time.monotonic()
            if slow:
                self.slow_at = 0.0

    def restore(self, snapshot):
        if snapshot.exists():
            lean = gzip.decompress(snapshot.read_bytes())
            self.served = {"version": json.loads(lean)["version"], "full": None, "lean": (lean, snapshot.read_bytes())}

    def serve_loop(self, snapshot):
        seen = None
        while True:
            with self.changed:
                self.changed.wait_for(lambda: self.version != seen, timeout=LIVE_REFRESH)
                seen = self.version
                model = dict(self.model)
            if "prs" not in model or (live := self.live_or_report(model)) is None:
                continue
            sig = hashlib.sha256(json.dumps({**browser_view(live), "version": None}).encode()).hexdigest()
            if self.served and self.served.get("sig") == sig:
                continue
            version = (self.served["version"] if self.served else 0) + 1
            live = {**live, "version": version}
            full, lean = json.dumps(live).encode(), json.dumps(browser_view(live)).encode()
            zipped = gzip.compress(lean, 5)
            with self.serving:
                self.live_model = live
                self.served = {"version": version, "sig": sig, "full": (full, gzip.compress(full, 5)), "lean": (lean, zipped)}
                self.serving.notify_all()
            snapshot.with_suffix(".tmp").write_bytes(zipped)
            snapshot.with_suffix(".tmp").replace(snapshot)

    def live_or_report(self, model):
        try:
            live = self.live(model)
        except Exception as error:
            failure = f"{type(error).__name__}: {error}"
            if self.reported_live != failure:
                self.reported_live = failure
                traceback.print_exc()
                served = (self.live_model or {}).get("built_at")
                self.report_defect(f"the page stopped updating{f' at {served}' if served else ''}, every refresh fails with {failure}")
            return None
        self.reported_live = None
        return live

    def rebuild_loop(self):
        while True:
            time.sleep(0.5)
            self.rebuild()

    def rebuild(self):
        with self.lock:
            due = self.dirty_at and time.monotonic() - self.dirty_at >= REBUILD_AFTER
        stale = time.monotonic() - self.slow_at >= SLOW_REFRESH
        if not due and not stale:
            return
        with self.lock:
            self.dirty_at = 0.0
        try:
            model = self.build(refresh_slow=stale)
        except Exception as error:
            failure = f"{type(error).__name__}: {error}"
            if self.errors.get("build") == failure and self.reported_build != failure:
                self.reported_build = failure
                self.report_defect(failure)
            self.errors["build"] = failure
            self.slow_at = time.monotonic()
            return
        self.errors.pop("build", None)
        self.reported_build = None
        with self.changed:
            self.model = model
            self.version += 1
            self.changed.notify_all()

    def note_go(self, record):
        if record["kind"] == "go" and (found := GO.match(record["text"])):
            self.gos[int(found[1])] = found[2]
            self.carried[int(found[1])] = bool(TAP_CARRY.search(record["text"]))
            if CI_FIX.search(record["text"]):
                self.ci_fixes.add(int(found[1]))
        elif record["kind"] == "decide" and record["lane"] in ROOT and WITHDRAWN_GO.search(record["text"]):
            for ref in (record.get("refs") or {}).get("prs") or []:
                number = int(re.search(r"(\d+)$", str(ref))[1])
                self.gos.pop(number, None)
                self.ci_fixes.discard(number)

    def load_gos(self):
        after = 0
        while True:
            page = cci("records", drive=self.config["drive"], kind=["go", "decide"], limit=500, since=after)
            for record in page:
                self.note_go(record)
            if len(page) < 500:
                return
            after = page[-1]["seq"]

    def check_gos(self, rows):
        for row in rows:
            exempt = row["number"] in self.ci_fixes and self.config["ci_fix_excludes"] not in self.paths.get(str(row["number"]), {"trees": [self.config["ci_fix_excludes"]]})["trees"]
            row["checked_at"] = self.gos.get(row["number"])
            row["go_head"] = row["checked_at"] if exempt or self.carried.get(row["number"]) else None
            stored = verdict_state({**row, "go_head": None}) == "approved"
            row["state"] = verdict_state(row)
            row["ci_fix_go"] = row["state"] == "approved" and not stored and exempt

    def store_seq(self):
        subject = self.config["board_url"].rsplit("/p/", 1)[1]
        with sqlite3.connect(f"file:{STORE}?mode=ro", uri=True) as store:
            return store.execute("SELECT max(seq) FROM events JOIN subjects ON subjects.id = events.subject_id WHERE subjects.slug = ?", (subject,)).fetchone()[0]

    def answer_times(self):
        subject = self.config["board_url"].rsplit("/p/", 1)[1]
        with sqlite3.connect(f"file:{STORE}?mode=ro", uri=True) as store:
            found = store.execute(f"SELECT json_extract(payload, '$.blockId'), max(events.created_at) FROM events JOIN subjects ON subjects.id = events.subject_id WHERE subjects.slug = ? AND events.type IN ({', '.join('?' * len(ANSWER_EVENTS))}) GROUP BY 1", (subject, *ANSWER_EVENTS)).fetchall()
        return {block: dt.datetime.fromtimestamp(stamp, dt.timezone.utc).isoformat() for block, stamp in found}

    def board(self):
        seq = self.store_seq()
        if seq != self.board_seq:
            self.board_raw = run(["cc-present", "outcomes", "--all", "--session", self.config["board_session"]])
            self.board_times = self.answer_times()
            self.board_seq = seq
        read = self.read_board(json.loads(self.board_raw))
        answered = {card: {"picked": answer["picked"], "at": max(self.board_times[ask] for ask in answer["asks"])} for card, answer in read["answered"].items()}
        return {**read, "answered": answered, "seq": seq}

    def live(self, model):
        board = self.board()
        self.check_gos(board["rows"])
        board["rows"] = at_live_head(with_conflicts(board["rows"], model["prs"]), model["prs"], model["records"])
        board["cards"] = unanswered(board["cards"], model["words"])
        stamp(board)
        now = dt.datetime.now(dt.timezone.utc)
        progress = json.loads(Path(self.config["steps"]).read_text())
        activity_records = [*model["records"], *model["lanes"]]
        gated = {row["number"] for row in board["rows"] if not row["go_head"]}
        drive = drive_prs(model["prs"], board["rows"], {number: head for number, head in self.gos.items() if number not in gated}, activity_records, model["trees"], model["tree_names"], progress["steps"], self.config["parts"], hold_reasons(holds.read(Path(self.config["holds"])), model["prs"]), now)
        workers = drive_workers(activity_records, model["prs"], progress["steps"], now)
        steps = step_rows(progress["steps"], drive, workers, board["answered"])
        releases = release_path(progress.get("now", {}).get("releases", []))
        milestone_rows = self.milestone_state(drive["prs"], now, board["answered"])
        self.flag_stale(milestone_rows, progress["steps"], board["answered"], now)
        need_items = needs(board["rows"], board["cards"], model["story"].get("asks", []), board["url"], {entry["number"] for entry in drive["prs"] if entry["state"] == "landed"})
        return {
            **model,
            "board": board,
            "drive": drive,
            "workers": workers,
            "desks": drive_workers(activity_records, model["prs"], progress["steps"], now, desks=True),
            "steps": steps,
            "parts": self.config["parts"],
            "now": {**progress.get("now", {}), "releases": releases},
            "steps_at": progress["updated"],
            "history": with_releases(progress.get("history", {}), self.milestone_cache[2]),
            "pr_state": progress.get("pr_state", {}),
            "preventions": progress.get("preventions", []),
            "live": LIVE_SOURCES,
            "needs": need_items,
            "journeys": journeys(drive, steps, releases, need_items, model["records"], model["prs"], progress.get("now", {}), model["tree_names"], now),
            "brief": self.curator.brief,
            "brief_error": self.curator.error,
            "status_now": self.status_now,
            "status_rejected": self.status_rejected,
            "stack": json.loads(Path(self.config["stack"]).read_text())["prs"],
            "status_table": Path(self.config["status_table"]).read_text(),
            "register_log": "register" in self.config,
            "latest_words": self.words(),
            "evidence": live_evidence(self.evidences(), model["prs"], progress.get("pr_state", {}), model["records"]),
            "pages": without_finished_prs({name: curator.brief for name, curator in self.pages.items()}, model["prs"], progress.get("pr_state", {})),
            "milestone_state": milestone_rows,
            "milestones": [m["name"] for m in self.config["milestones"]],
            "milestones_label": self.config["milestones_label"],
            "views": self.config.get("views", {}),
        }

    def flag_stale(self, milestone_rows, steps, answered, now):
        for key, lanes, text in stale_waits(milestone_rows, steps, answered, now):
            if key not in self.flagged:
                self.flagged.add(key)
                self.flag(text, lanes)

    def report_defect(self, text):
        self.flag(f"drive dashboard build failed: {text}", ())

    def flag(self, text, lanes):
        if not self.config.get("defects_to"):
            return
        subprocess.run(["cci", "post", "--drive", self.config["drive"], "--lane", self.config["keeper_lane"], "--to", ",".join(dict.fromkeys([*lanes, *self.config["defects_to"]])), "--kind", "defect", "--text", text[:390]], capture_output=True)

    def build(self, refresh_slow):
        if refresh_slow or not self.slow:
            self.slow = self.read_slow()
            self.slow_at = time.monotonic()
        with self.lock:
            records = list(self.records)
        try:
            lanes = cci("lanes", drive=self.config["drive"])
            self.errors.pop("lanes", None)
        except OSError as error:
            lanes = self.model.get("lanes", [])
            self.errors["lanes"] = f"the cci daemon at {CCI} did not answer ({error}); workers are as of the last answer"
        return {
            **self.slow,
            "title": self.config["title"],
            "repo": self.config["repo"],
            "records": records[-1500:],
            "trees": {n: entry["trees"] for n, entry in self.paths.items()},
            "tree_names": [tree["name"] for tree in self.config["trees"]],
            "lanes": lanes,
            "built_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "errors": dict(self.errors),
        }

    def read_slow(self):
        ledger = json.loads(self.ccn("ledger", "show", self.config["ledger"], "--json"))
        prs = {row["key"]: {k: json.loads(v) if k == "failing_checks" else v for k, v in row["fields"].items() if not k.startswith("progress_")} for row in ledger["rows"] if row["key"].isdigit()}
        for pr in prs.values():
            if pr.get("state") == "closed" and "externally-merged" in (pr.get("labels") or "").split(","):
                pr["state"] = "landed"
        plan_at, plan = self.read_plan()
        story = json.loads(Path(self.config["story"]).read_text())
        for ask in story.get("asks", []):
            if "seq" in ask:
                ask["record"] = self.record(ask["seq"])
        return {
            "prs": prs,
            "plan": plan,
            "plan_at": plan_at,
            "story": story,
            "findings": self.read_findings(),
            "words": list({a["id"]: {"id": a["id"][:7], "at": a["updated_at"], "title": a["title"], "text": a["body"]} for label in ("program", "drive") for a in json.loads(self.ccn("answer", "list", "--label", f"{label}:{self.config['drive']}", "--json"))}.values()),
        }

    def read_findings(self):
        try:
            ledger = json.loads(Path(self.config["findings"]).read_text())["ledger"]
            rows = json.loads(self.ccn("ledger", "show", ledger, "--json"))["rows"]
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            self.errors["findings"] = f"the findings ledger did not read ({type(error).__name__}: {error})"[:300]
            return []
        self.errors.pop("findings", None)
        return open_findings(rows)

    def record(self, seq):
        found = json.loads(run(["cci", "show", str(seq), "--json"]))
        return {"seq": seq, "lane": found["lane"], "at": found["at"], "text": run(["cci", "show", str(seq)]).partition("\n")[2].strip() or found["text"]}

    def facts(self, number):
        repo = self.config["repo"]
        reads = [(f"repos/{repo}/pulls/{number}", PR_FACTS), (f"repos/{repo}/pulls/{number}/reviews?per_page=100", BOT_REVIEW), (f"repos/{repo}/issues/{number}/comments?per_page=100", BOT_COMMENTS)]
        with ThreadPoolExecutor(len(reads)) as pool:
            pr, review, comments = pool.map(lambda read: json.loads(gh(repo, "api", read[0], "--jq", read[1])), reads)
        return {**pr, "review": review, **bot_words(comments)}

    def read_plan(self):
        for line in self.ccn("doc", "list", "--label", self.config["progress_label"]).splitlines()[:6]:
            if plan := parse_plan(self.ccn("doc", "show", line.split("\t")[0])):
                return plan
        return None, []

    def read_board(self, board):
        interactions = board["interactions"]
        verdicts = interactions["triage"]
        retired = set(board.get("retired") or [])
        rows, cards, answered, merge = [], [], {}, None
        for block in board["doc"]["blocks"]:
            if block["type"] != "card" or block["id"] in retired:
                continue
            groups = [child for child in block.get("children", []) if child["type"] == "pr-groups"]
            if block["id"] == MERGE_CARD:
                merge = {"title": block.get("title", ""), "summary": block.get("summary", ""), "chips": block.get("chips") or [], "tables": [child for child in block.get("children", []) if child["type"] == "table"], "pages": [child["md"] for child in block.get("children", []) if child["type"] == "display.page"]}
            for child in groups:
                for group in child["groups"]:
                    for row in group["prs"]:
                        rows.append({**row, "group": group["title"], "group_id": group["id"], "group_summary": group.get("summary", ""), "block": child["id"], "verdict": verdicts.get(child["id"], {}).get(str(row["number"]))})
            if groups:
                continue
            children = block.get("children", [])
            asks = [child for child in children if child["type"] in ASKS]
            answers = {child["id"]: interactions[ASKS[child["type"]]].get(child["id"]) for child in asks}
            noted = any(interactions["feedback"].get(child["id"]) for child in [block, *children])
            if asks and all(answers.values()):
                answered[block["id"]] = {"picked": "; ".join(picked_words(child, answers[child["id"]]) for child in asks), "asks": [child["id"] for child in asks]}
                continue
            if not asks and (noted or block.get("status") != "open"):
                continue
            choice = next((child for child in asks if child["type"] == "choice" and not answers[child["id"]]), None)
            decides = any(not answers[child["id"]] and (child["type"] != "choice" or sum(not OTHER.match(option["label"]) for option in child.get("options", [])) > 1) for child in asks)
            options = [{"id": option["id"], "label": option["label"], "recommended": bool(option.get("recommended"))} for option in (choice or {}).get("options", []) if not OTHER.match(option["label"])]
            recommended = next((option["label"] for option in options if option["recommended"]), None)
            embed = any(child["type"] not in NATIVE or child.get("multi") for child in children)
            cards.append({
                "id": block["id"], "title": block.get("title", ""), "summary": block.get("summary", ""), "chips": block.get("chips") or [],
                "recommended": recommended, "choice": choice and choice["id"], "options": options, "decides": decides,
                "parts": [] if embed else [card_part(child, answers.get(child["id"])) for child in children],
                "embed": f"{self.config['board_url']}?{urllib.parse.urlencode({'block': block['id'], 'theme': 'dark'})}" if embed else None,
            })
        return {"rows": rows, "cards": cards, "answered": answered, "merge": merge, "url": self.config["board_url"]}


def handler(drive):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in STATIC:
                name, kind = STATIC[path]
                body = (HERE / name).read_text()
                if name == "index.html":
                    for asset, _ in STATIC.values():
                        body = body.replace(f"/{asset}", f"/{asset}?v={(HERE / asset).stat().st_mtime_ns}")
                    body = body.replace("</body>", ASSIST.read_text() + "</body>").replace("<head>", f'<head>\n<meta name="build" content="{BUILD}">', 1)
                return self.send(200, kind, body.encode())
            if path == "/api/model":
                kind = "full" if "full=1" in self.path else "lean"
                with drive.serving:
                    drive.serving.wait_for(lambda: drive.served and drive.served[kind], timeout=60)
                    served = drive.served and drive.served[kind]
                if not served:
                    return self.send(503, "text/plain", b"The dashboard is building its first view.")
                return self.send_built(*served)
            if path == "/api/words":
                with drive.lock:
                    words = drive.model.get("words", [])
                return self.send(200, "application/json", json.dumps(words).encode())
            if path == "/api/stream":
                return self.stream()
            if found := REVIEW_ROUTE.fullmatch(path):
                try:
                    url = drive.review_url(int(found[1]))
                except subprocess.CalledProcessError as error:
                    return self.send(502, "application/json", json.dumps({"error": (error.stderr or error.stdout).strip()}).encode())
                return self.send(200, "application/json", json.dumps({"url": url, "assistant": int(found[1]) in drive.assistant.watching, "stale": int(found[1]) in drive.assistant.stale}).encode())
            if path == "/ai.json":
                return self.send(200, "application/json", json.dumps({**AI_CONFIG, "key": os.environ["CEREBRAS_API_KEY"]}).encode())
            if found := STORY_ROUTE.fullmatch(path):
                return self.story(found[1])
            if found := TRANSCRIPT_ROUTE.fullmatch(path):
                work = drive.work(found[1])
                transcript = work and drive.stories.transcript(work["workers"])
                if transcript is None:
                    return self.send(404, "text/plain; charset=utf-8", b"This worker's transcript is not on this Mac.")
                return self.source("The transcript this story reads", run(["cc-transcript", "show", str(transcript), "--no-junk", "--width", "300"]))
            if path == "/register":
                return self.source("The owner request log", drive.register_log())
            if found := LANE_ROUTE.fullmatch(path):
                return self.send(200, "application/json", json.dumps(drive.posted(found[1])).encode())
            if found := RECORD_ROUTE.fullmatch(path):
                try:
                    if "raw=1" in self.path.partition("?")[2].split("&"):
                        return self.source("The record this links to", run(["cci", "show", found[1]]))
                    record = json.loads(run(["cci", "show", found[1], "--json"]))
                    asked = json.loads(run(["cci", "show", str(record["re"]), "--json"])) if record.get("re") else None
                    said = json.loads(Path(drive.config["steps"]).read_text()).get("said", {}).get(found[1])
                    return self.send(200, "text/html; charset=utf-8", record_page(record, asked, said).encode())
                except subprocess.CalledProcessError as error:
                    return self.send(502, "text/plain; charset=utf-8", error.stderr.encode())
            if found := PR_ROUTE.fullmatch(path):
                try:
                    facts = drive.facts(int(found[1]))
                except subprocess.CalledProcessError as error:
                    return self.send(502, "application/json", json.dumps({"error": error.stderr.strip()}).encode())
                return self.send(200, "application/json", json.dumps(facts).encode())
            self.send(404, "text/plain", b"not found")

        def do_POST(self):
            if self.path not in ("/api/verdict", "/api/answer", "/api/asked", "/api/change"):
                return self.send(404, "text/plain", b"not found")
            if self.headers.get_content_type() != "application/json":
                return self.send(415, "text/plain", b"Content-Type must be application/json")
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/api/answer":
                status, out = drive.answer(body)
            elif self.path == "/api/asked":
                status, out = drive.asked(body["text"])
            elif self.path == "/api/change":
                try:
                    status, out = drive.change_request(int(body["number"]), body["head"], body["text"].strip())
                except (OSError, subprocess.SubprocessError) as failure:
                    status, out = 502, {"error": f"the drive's records could not take it ({getattr(failure, 'stderr', None) or failure})"}
            else:
                status, out = drive.decide(body["block"], body["number"], body["verdict"], body.get("head"), body.get("note"))
            self.send(status, "application/json", json.dumps(out).encode())

        def source(self, title, text):
            return self.send(200, "text/html; charset=utf-8", SOURCE_PAGE.format(title=title, text=html.escape(text)).encode())

        def send(self, status, kind, body):
            zipped = len(body) > 4096 and "gzip" in self.headers.get("Accept-Encoding", "")
            if zipped:
                body = gzip.compress(body, 5)
            self.send_response(status)
            self.send_header("Content-Type", kind)
            if zipped:
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def send_built(self, raw, zipped):
            body = zipped if "gzip" in self.headers.get("Accept-Encoding", "") else raw
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            if body is zipped:
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def story(self, item):
            if "mirror" in drive.config:
                return self.relay(f"{drive.config['mirror']}/api/story/{item}")
            work = drive.work(item)
            if work is None:
                return self.send(404, "text/plain", b"no such work")
            story = drive.stories.story(item, work)
            viewer = story.join()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                opening = {"type": "saved", "lines": story.lines(), "status": story.status, "draft": story.draft}
                self.wfile.write(f"data: {json.dumps(opening)}\n\n".encode())
                self.wfile.flush()
                while True:
                    try:
                        entry = viewer.get(timeout=15)
                        self.wfile.write(f"data: {json.dumps(entry)}\n\n".encode())
                    except queue.Empty:
                        self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
            except OSError:
                return
            finally:
                story.leave(viewer)

        def relay(self, url):
            try:
                upstream = urllib.request.urlopen(url, timeout=60)
            except urllib.error.HTTPError as error:
                return self.send(error.code, "text/plain", error.read())
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with upstream:
                try:
                    for line in upstream:
                        self.wfile.write(line)
                        self.wfile.flush()
                except OSError:
                    return

        def stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(f"event: build\ndata: {BUILD}\n\n".encode())
            seen = -1
            while True:
                with drive.serving:
                    drive.serving.wait_for(lambda: drive.served and drive.served["version"] != seen, timeout=15)
                    version = drive.served and drive.served["version"]
                try:
                    if version in (None, seen):
                        self.wfile.write(b": keep-alive\n\n")
                    else:
                        self.wfile.write(f"event: version\ndata: {version}\n\n".encode())
                        seen = version
                    self.wfile.flush()
                except OSError:
                    return

    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    drive = Drive(config, ReviewAssistant(config, args.config.parent / "reviews", f"http://{config['host']}:{config['port']}/api/model"))
    snapshot = args.config.parent / "served-model.json.gz"
    drive.restore(snapshot)
    after = drive.load_records()
    drive.load_gos()
    writers = [(drive.follow_mirror, ())] if "mirror" in config else [(curator.loop, ()) for curator in drive.curators]
    for target, rest in ((drive.follow_code, ()), *writers, (drive.follow_records, (after,)), (drive.follow_board, ()), (drive.follow_steps, ()), (drive.follow_status, ()), (drive.rebuild_loop, ()), (drive.serve_loop, (snapshot,)), (drive.follow_paths, ())):
        threading.Thread(target=target, args=rest, daemon=True).start()
    server = ThreadingHTTPServer((config["host"], config["port"]), handler(drive))
    server.daemon_threads = True
    print(f"http://{config['host']}:{config['port']}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
