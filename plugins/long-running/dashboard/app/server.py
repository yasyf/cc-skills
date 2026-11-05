import argparse
import base64
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
import ssl
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

import plainwords
from brief import COORDINATORS, ROOT, Curator
from owed import CHAT_HOLDER, closed_asks, kept, order_replies, overdue_rows, owner_ask_rows, told_asks, trimmed
from pages import PAGES, asks_brief, source_key, stamp, titled_asks
from review_assistant import ReviewAssistant
from screens import SCREENS, changed_screens, screen_kind, screen_view
from story import Stories

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "long-running" / "scripts"))
import drive
import holds
import milestones
import picks
import taps

HERE = Path(__file__).resolve().parent
PACIFIC = ZoneInfo("America/Los_Angeles")
CCI = "http://127.0.0.1:7377/v1"
TIMELINE_KINDS = ["state", "head", "review", "owner", "go", "landed", "release", "incident", "mechanism", "fix-live", "opened", "report", "hold", "lift", "blocker", "defect", "correction", "decide", "decision", "ask", "answer", "done", "stopped", "stand-down", "handoff", "evidence"]
TIMELINE_WINDOW = dt.timedelta(hours=24)
CHAT_WINDOW = dt.timedelta(days=30)
THREAD_HEAD = re.compile(r"^\[thread: .*\]\n")
LEDGER_KINDS = {"landed", "go", "opened", "head", "hold", "lift"}
LIVE_SOURCES = ["cc-present store: taps and questions", "cci bus: records and workers", "drive ledger in cc-notes: pull request states, re-read on every landing, go, head, opened, hold or lift record", "progress-desk steps.json: the step rows", "findings ledger in cc-notes: open sweep findings and owner picks, re-read every 5 minutes", "retro table in cc-notes through retro.py: what failed and what stops it, re-read every 5 minutes"]
REBUILD_AFTER = 2.0
LIVE_REFRESH = 5.0
SNAPSHOT_EVERY = 60.0
DELTAS_KEPT = 30
ROW_DELTA_KINDS = ("lean", "now")
LAZY_KEYS = ("prs", "history", "drive", "records", "pr_state", "lanes", "owner_asks", "status_tables", "step_records", "replies", "chat_lines")
NOW_DRAWS = ("needs", "board", "brief", "milestone_state", "pages", "workers", "now", "status_now")
NOW_EVENTS = 5
NOW_RECORDS = 300
DRIVE_LIGHT = ("number", "state", "part", "step", "tree")
LANE_NAME = re.compile(r'"([a-z][a-z0-9]*(?:-[a-z0-9]+)+)"')
WORD_BYSTANDERS = ("dashboard", "register", "cc-present", "desk-runner")
PR_NAMED = re.compile(r"#(\d{5})(?!\d)")
SLACK_POST = re.compile(r"slack\.com/archives/(\w+)/p(\d+)")
SLOW_REFRESH = 300
OWED_KINDS = ("ask", "owner", "defect", "done", "report", "answer")
OWED_WINDOW = dt.timedelta(days=7)
BLOCKS_WINDOW = dt.timedelta(hours=24)
QUESTION_BOARDS = "questions-for-you--%"
PLAN_HEADING = "\n## Ordered plan of action"
PLAN_ITEM = re.compile(r"^\d+\. (.+)$")
GO = re.compile(r"^go #(\d+) at (\w+)")
CI_FIX = re.compile(r"\bCI[- ]fix\b", re.I)
TAP_CARRY = re.compile(r"\bTAP-(?:CARRIED|KEPT)\b")
WITHDRAWN_GO = re.compile(r"\bgo\b[^.]*\bwithdr(?:aw|awn|ew)\b", re.I)
CARD_ID = re.compile(r"\bcard-[a-z0-9]+(?:-[a-z0-9]+)*")
PR_REF = re.compile(r"#(\d{4,6})\b")
PR_RANGE = re.compile(r"#(\d{4,6})-#(\d{4,6})")
ALERTS_SENT = 20
CERT_CHECK = 24 * 60 * 60
SCREEN_ROUTE = re.compile(r"/api/view/([a-z]+)")
REVIEW_ROUTE = re.compile(r"/api/review/(\d+)")
MODEL_ROUTE = "/ai/chat/completions"
MODEL_URL = "https://api.cerebras.ai/v1/chat/completions"
MODEL_NAME = "gpt-oss-120b"
MODEL_FIELDS = ("messages", "tools", "tool_choice", "parallel_tool_calls", "reasoning_effort", "reasoning_format", "max_completion_tokens", "temperature", "response_format", "prompt_cache_key", "stream", "stream_options")
ASSIST =HERE.parents[1] / "skills" / "long-running" / "templates" / "drive-assist.html"
BUILD = hashlib.sha1(b"".join(path.read_bytes() for path in sorted([*HERE.iterdir(), ASSIST]) if path.suffix in (".js", ".css", ".html", ".py"))).hexdigest()[:12]
CLIENT_RECORD = ("seq", "lane", "kind", "at", "text", "refs", "re")
PR_ROUTE = re.compile(r"/api/pr/(\d+)")
RECORD_ROUTE = re.compile(r"/record/(\d+)")
ASSET_ROUTE = re.compile(r"/asset/([0-9a-f]{64})")
ASSET_REF = re.compile(r"^asset:([0-9a-f]{64})$")
THREAD_EVENTS = ("feedback.created", "reply.created")
CLOCK_OPENING = re.compile(r"^\s*\d{1,2}:\d{2}\s*[AP]M\b[,:]?\s*")
PR_TAG = re.compile(r"\s*\bpr:\S+")
FILE_PATH = re.compile(r"\s*(?<![\w.])~?/(?:[\w.@-]+/)+[\w.@-]+")
ASKED_PREFIX = "Asked on the dashboard: "
OWNER_LANES = frozenset({"dashboard", "owner-board", "owner"})
NOT_A_REPLY = frozenset({"ask", "decide", "go", "blocked", "hold"})
COMMUNICATOR = "owner-relay"
HOLDER_WORDS = {"root": "the coordinator", COMMUNICATOR: CHAT_HOLDER}
LANE_ROUTE = re.compile(r"/api/lane/([\w.-]+)")
REGISTER_DAY = dt.timedelta(days=1)
REGISTER_SHOWN = 25
MILESTONE_TTL = 60
PICK_GRACE = dt.timedelta(minutes=1)
ANSWER_EVENTS = ("choice.selected", "decision.created", "input.submitted")
UNSETTLED = ("open", "routed")
RECORD_PAGE = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="dark"><title>{title}</title><link rel="stylesheet" href="/app.css"><link rel="stylesheet" href="/pages.css"></head><body><main class="wrap pg"><a class="pg-back" href="/">‹ The drive dashboard</a><header class="pg-h"><div><h1>{title}</h1></div><span class="pg-at">{byline}</span></header><dl class="mr-f rec">{rows}</dl><details class="rec-raw"><summary>The record as written</summary><pre>{raw}</pre><a href="/record/{seq}?raw=1">With its bus header</a></details></main></body></html>'
KIND_TAG = re.compile(r"(?:[A-Z][A-Z-]{2,}\s+)*[A-Z][A-Z-]{2,}\b[:,]?\s*")
KIND_LABEL = {"owner": "Note", "go": "Go to land", "landed": "Landed", "mechanism": "Cause found", "fix-live": "Fix live", "opened": "Opened", "report": "Report", "hold": "Hold", "lift": "Hold lifted", "blocker": "Blocker", "defect": "Tool problem", "decide": "Decision", "ask": "Question", "answer": "Answer", "head": "New version", "contract": "Contract", "state": "Status", "evidence": "Evidence"}
URL = re.compile(r"https?://[^\s)<>\"'\]]*[^\s)<>\"'\].,;:!?]")
REPO_PR = re.compile(r"\b([\w.-]+/[\w.-]+)#(\d+)\b")
PR_AT_HEAD = re.compile(r"(?<![\w/])#(4\d{4})\b[^#.;\n]{0,24}?\b(?:at|as|head)\s+([0-9a-f]{7,40})\b")
SHA = re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b")
RECORD_SEQ = re.compile(r"(?<![\w/])#(\d{6})\b")
DIRECTIVE = re.compile(r"(?:^|(?<=[.;!?]\s))([a-z][a-z0-9]*(?:-[a-z0-9]+)*):\s")
SENTENCE = re.compile(r"(?<=[.!?])(?<!\be\.g\.)(?<!\bi\.e\.)(?<!\bvs\.)(?<!\betc\.)\s+")
SOURCE_PAGE = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="dark"><title>{title}</title><link rel="stylesheet" href="/app.css"><link rel="stylesheet" href="/pages.css"></head><body><main class="wrap pg"><a class="pg-back" href="/">‹ The drive dashboard</a><header class="pg-h"><div><h1>{title}</h1></div></header><pre class="src-text">{text}</pre></main></body></html>'
STORY_ROUTE = re.compile(r"/api/story/([a-z0-9-]+)")
TRANSCRIPT_ROUTE = re.compile(r"/api/transcript/([a-z0-9-]+)")
ROW_FACTS = ("number", "head", "key", "title", "change", "plan", "risk", "review", "blocker", "meta", "md", "block", "state", "ci_fix_go", "checked_at", "awaiting")
CONFLICT = "Conflicts with its base, so it cannot land until its worker rebases it."
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
NATIVE = {*ASKS, "record", "display.page", "markdown", "image", "pr", "table"}
OTHER = re.compile(r"^other$", re.I)
MERGE_BLOCK = "merge-prs"
LR_STORE = "lr.sqlite"
ROW_META = (
    ("Waits on", "waits_on"), ("Description", "description"), ("CI", "ci"), ("Approved by", "approvers"), ("Grade", "grade"), ("Findings", "grade_findings"),
    ("Mergeable", "mergeable"), ("Queue", "queue"), ("Hold", "hold"), ("Superseded by", "superseded_by"), ("Lane", "lane"), ("Branch", "branch"), ("Parent", "parent"),
    ("Head", "head"), ("Tapped at", "tapped_at"), ("Tapped by", "tapped_by"), ("Tap refused", "tap_refused"), ("Landed at", "landed_at"), )
LANE_NUMBER = re.compile(r"-\d+$")
ACTIVE = dt.timedelta(minutes=30)
WORKING = dt.timedelta(minutes=20)
BOX = dt.timedelta(minutes=5)
BOXES = 12
SEVERITY = {"blocker": "bad", "defect": "bad", "incident": "bad", "ask": "you", "owner": "you", "landed": "ok", "go": "ok", "fix-live": "ok"}
TONE_RANK = {"bad": 3, "you": 2, "ok": 1, "info": 0}
PR_MENTION = re.compile(r"#(4\d{4})\b")
DIAGRAM_ROUTE = re.compile(r"/diagram/([\w-]+)")
DIAGRAM_PAGE = """<!doctype html><meta charset="utf-8"><title>{title}</title><script type="module" src="/arch-kit/arch-doc.js"></script><style>html,body{{margin:0;background:#12150f}}arch-doc{{display:block}}</style><arch-doc src="data:application/json;base64,{doc}"></arch-doc>"""
FOLLOW_RESTART = 60
STATIC = {"/arch-kit/arch-doc.js": ("arch-kit/arch-doc.js", "text/javascript"), "/arch-kit/vendor/arch-map.js": ("arch-kit/vendor/arch-map.js", "text/javascript"), "/arch-kit/vendor/system-map.js": ("arch-kit/vendor/system-map.js", "text/javascript"), "/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/app.css": ("app.css", "text/css"), "/review.js": ("review.js", "text/javascript"), "/review.css": ("review.css", "text/css"), "/overview.js": ("overview.js", "text/javascript"), "/overview.css": ("overview.css", "text/css"), "/cockpit.css": ("cockpit.css", "text/css"), "/now.js": ("now.js", "text/javascript"), "/now.css": ("now.css", "text/css"), "/pages.js": ("pages.js", "text/javascript"), "/pages.css": ("pages.css", "text/css"), "/chat.js": ("chat.js", "text/javascript"), "/chat.css": ("chat.css", "text/css"), "/theme.css": ("theme.css", "text/css")}


def gh(repo, *args):
    return run(["env", "CCX_CALLER=drive-dashboard", f"GH_REPO={repo}", "ccx", "vcs", "gh", "--max-age", "5m", "--", *args])


def run(argv, cwd=None, stdin=None):
    return subprocess.run(argv, cwd=cwd, input=stdin, capture_output=True, text=True, check=True, timeout=60).stdout


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


def until_changed(path, within):
    seen, ends = path.stat().st_mtime_ns, time.monotonic() + within
    while time.monotonic() < ends and path.stat().st_mtime_ns == seen:
        time.sleep(1)


def lean(records):
    return [{k: r[k] for k in CLIENT_RECORD if k in r} for r in records]


def browser_view(model):
    return {**{k: v for k, v in model.items() if k != "words"}, "words_at": max((w["at"] for w in model["words"]), default=None), "records": lean(model["records"]), "lanes": lean(model["lanes"])}


def now_view(view):
    """`view` cut to what the Now tab draws, so a first load parses that alone (root #306148): the open pull requests and those Now names, their state and newest few history events, each landed change as a light, the newest records, and no lanes."""
    drawn = json.dumps([view[key] for key in NOW_DRAWS])
    named = set(re.findall(r"\b\d{3,6}\b", drawn))
    numbers = {n for n, pr in view["prs"].items() if pr.get("state") == "open" or n in named}
    lanes = {re.sub(r"-\d+$", "", lane) for lane in [*(view["prs"][n].get("lane") for n in numbers), *LANE_NAME.findall(drawn)] if lane}
    return {
        **{key: value for key, value in view.items() if key not in LAZY_KEYS},
        "cut": True,
        "prs": {n: view["prs"][n] for n in numbers},
        "pr_state": {n: state for n, state in view["pr_state"].items() if n in numbers},
        "history": {key: {**entry, "events": entry["events"][:NOW_EVENTS]} for key, entry in view["history"].items() if key.partition(":")[2] in (numbers if key.startswith("pr:") else lanes)},
        "drive": {**view["drive"], "prs": [entry if entry["state"] != "landed" else {key: entry[key] for key in DRIVE_LIGHT} for entry in view["drive"]["prs"]]},
        "records": view["records"][-NOW_RECORDS:],
        "lanes": [],
    }


def encoded(value):
    piece = json.dumps(value).encode()
    return value, piece, hashlib.sha1(piece).digest()


def joined(pieces, keys):
    return b"{" + b", ".join(json.dumps(key).encode() + b": " + pieces[key][1] for key in keys) + b"}"


def changed_since(version, pieces, past):
    if not past or past.keys() != pieces.keys():
        return None
    return b'{"version": %d, "changed": %s}' % (version, joined(pieces, [key for key, (_, _, digest) in pieces.items() if past[key] != digest]))


def row_mark(value):
    return hashlib.sha1(json.dumps(value).encode()).digest()[:8]


def rows_of(value):
    if isinstance(value, list):
        return [row_mark(row) for row in value]
    if isinstance(value, dict):
        return {key: rows_of(item) if isinstance(item, list) else row_mark(item) for key, item in value.items()}
    return row_mark(value)


def list_patch(rows, marks, past):
    place = {mark: at for at, mark in enumerate(past)}
    runs = []
    for row, mark in zip(rows, marks):
        at = place.get(mark)
        if at is None:
            runs.append({"row": row})
        elif runs and isinstance(runs[-1], list) and sum(runs[-1]) == at:
            runs[-1][1] += 1
        else:
            runs.append([at, 1])
    return {"$runs": runs}


def row_patch(value, marks, past):
    if isinstance(value, list) and isinstance(past, list):
        return list_patch(value, marks, past)
    if not (isinstance(value, dict) and isinstance(past, dict)):
        return None
    moved = {
        key: list_patch(item, marks[key], past[key]) if isinstance(item, list) and isinstance(past.get(key), list) else {"$value": item}
        for key, item in value.items()
        if marks[key] != past.get(key)
    }
    return {"$keys": moved, "$gone": [key for key in past if key not in value]}


def changed_rows(version, pieces, past, shapes):
    if not past or past.keys() != pieces.keys():
        return None
    patched = {}
    whole = []
    for key, (value, _, digest) in pieces.items():
        if past[key] == digest:
            continue
        patch = row_patch(value, shapes[digest], shapes[past[key]]) if digest in shapes and past[key] in shapes else None
        if patch is None:
            whole.append(key)
        else:
            patched[key] = patch
    return b'{"version": %d, "changed": %s, "patched": %s}' % (version, joined(pieces, whole), json.dumps(patched).encode())


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
    step_of = {}
    for step in steps:
        for n in step["prs"]:
            step_of.setdefault(n, step["id"])
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
            "step": step_of.get(int(number)),
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


def record_body(record):
    """The whole body `record` carries: its text, then the file it names, or the file alone when the text is that file's opening cut short."""
    text, path = record["text"], Path(record.get("refs", {}).get("path") or "/nonexistent")
    if not path.is_file():
        return text
    file = path.read_text()
    return file if file.startswith(text.rstrip("…").rstrip()) or file.splitlines()[:1] == [text] else f"{text}\n\n{file}"


def lead_trimmed(text, lane):
    """`text` without the header a lane writes before what it says: a clock, a kind in capitals, its own name, and the label those end in."""
    text = CLOCK_OPENING.sub("", text.strip())
    tagged, named = KIND_TAG.match(text), re.match(rf"{re.escape(lane)}\b[:,]?\s*", text)
    if not (tagged or named):
        return text
    text = CLOCK_OPENING.sub("", text[(tagged or named).end():])
    if named and (tagged := KIND_TAG.match(text)):
        text = text[tagged.end():]
    label = re.match(r"[^:.\n]{0,48}:\s+", text)
    return text[label.end():] if label else text


def unparsed(body, text):
    """Why `body` is no message a reader can take line by line, or None when it is one."""
    if body.lstrip().startswith(("{", "[")):
        try:
            json.loads(body)
            return "It is JSON data, not sentences."
        except ValueError:
            pass
    if not text.strip():
        return "It carries no words beyond its header."
    opening = text.strip()[:600]
    if len(opening) == 600 and not re.search(r"[.!?:;](\s|$)", opening):
        return f"Its first 600 of {len(text):,} characters hold no sentence break, so it is a log or a dump, not a message."
    return None


def parse_record(record, chain):
    """What a reader takes from `record`: who sent it to whom and when, the owner's question it answers up `chain`, what it says, what it asks each lane, the pull requests, commits and evidence it names; or why its body is no message."""
    body = record_body(record)
    own = record.get("refs", {}).get("path")
    urls = list(dict.fromkeys(URL.findall(body)))
    heads = {int(n): sha for n, sha in PR_AT_HEAD.findall(body)}
    parsed = {
        "question": None,
        "lines": [("Who", record["lane"] + (f", to {', '.join(record['to'])}" if record.get("to") else "")), ("When", at(record["at"]).astimezone(PACIFIC).strftime("%-I:%M %p, %b %-d"))],
        "details": [],
        "prs": list(dict.fromkeys([*map(int, record.get("refs", {}).get("prs") or []), *map(int, PR_MENTION.findall(body)), *heads])),
        "heads": heads,
        "elsewhere": list(dict.fromkeys(f"{repo}#{n}" for repo, n in REPO_PR.findall(body))),
        "commits": [sha for sha in dict.fromkeys(SHA.findall(body)) if sha not in heads.values() and not any(sha in url for url in urls)],
        "records": [int(n) for n in dict.fromkeys(RECORD_SEQ.findall(body)) if int(n) not in (record["seq"], chain[0]["seq"] if chain else None)],
        "urls": urls,
        "files": [path for path in dict.fromkeys(found.strip().rstrip(".") for found in FILE_PATH.findall(body)) if path != own],
    }
    if owner := next((link for link in chain if link["lane"] in OWNER_LANES or link["kind"] == "owner"), None):
        parsed["question"] = " ".join(FILE_PATH.sub("", PR_TAG.sub("", CLOCK_OPENING.sub("", record_body(owner)))).removeprefix(ASKED_PREFIX).split())
        on = f" on #{owner['refs']['prs'][0]}" if owner.get("refs", {}).get("prs") else ""
        via = f", routed through {', '.join(f'#{link["seq"]}' for link in chain[: chain.index(owner)])}" if chain.index(owner) else ""
        parsed["lines"].append(("Answers", f"Your question{on} at {at(owner['at']).astimezone(PACIFIC).strftime('%-I:%M %p')}: “{parsed['question']}”{via}"))
    elif chain:
        parent = chain[0]
        opening = SENTENCE.split(lead_trimmed(PR_TAG.sub("", FILE_PATH.sub("", parent["text"])), parent["lane"]), maxsplit=1)[0]
        parsed["lines"].append(("Answers", f"{parent['lane']}'s {KIND_LABEL.get(parent['kind'], parent['kind']).lower()} #{parent['seq']}: {opening[:200]}"))
    text = lead_trimmed(FILE_PATH.sub(lambda path: f" {path[0].strip().rsplit('/', 1)[-1] if path[0].count('/') > 2 else path[0].strip()}", PR_TAG.sub("", URL.sub("", body))), record["lane"])
    if why := unparsed(body, text):
        parsed["lines"].append(("Not parsed", why))
        return parsed
    prose, bullets, table = [], [], 0
    for line in map(str.strip, text.splitlines()):
        if line.startswith("|"):
            table += 1
        elif line.startswith(("- ", "* ")):
            bullets.append(line[2:].strip())
        elif line.startswith("#"):
            prose.append(f"{line.lstrip('#').strip()}:")
        elif line:
            prose.append(line)
    joined = " ".join(prose)
    if parsed["question"]:
        joined = re.sub(rf"[\"“]?{re.escape(parsed['question'])}[\"”]?", "\0", joined)
    cuts = [m for m in DIRECTIVE.finditer(joined) if m[1] in (record.get("to") or []) or m[1] == "root" or re.search(r"-\d+$", m[1])]
    sentences = [s.strip() for s in SENTENCE.split(joined[: cuts[0].start()] if cuts else joined) if s.strip() and "\0" not in s]
    if sentences:
        parsed["lines"].append(("Says", sentences[0]))
    parsed["lines"] += [(f"Asks {m[1]}", joined[m.end() : cuts[i + 1].start() if i + 1 < len(cuts) else len(joined)].strip().replace("\0", "your question")) for i, m in enumerate(cuts)]
    parsed["details"] = [*sentences[1:], *bullets, *([f"A table of {max(table - 2, 1)} rows, in the raw record below."] if table else [])]
    return parsed


def record_page(record, chain, said, titles):
    """The record a dashboard link opens as labelled lines, never a block of text (owner 12:03 AM Nov 2): the owner's question as the title when it answers one, and the raw body behind a disclosure."""
    who = "You" if record["kind"] == "owner" or record["lane"] in OWNER_LANES else record["lane"]
    parsed = parse_record(record, chain)
    title = parsed["question"] or f"{KIND_LABEL.get(record['kind'], record['kind'].capitalize())} from {who}"
    rows = "".join(f"<dt>{html.escape(label)}</dt><dd>{html.escape(value)}</dd>" for label, value in [*parsed["lines"], *([("In short", said)] if said else [])])
    if parsed["details"]:
        rows += "<dt>Details</dt><dd><ul>" + "".join(f"<li>{html.escape(line)}</li>" for line in parsed["details"]) + "</ul></dd>"
    if parsed["prs"]:
        rows += "<dt>Pull requests</dt><dd><ul>" + "".join(
            f'<li><a href="/#/review/{n}">#{n}</a>{f" at <code>{parsed["heads"][n][:9]}</code>" if n in parsed["heads"] else ""}{f" {html.escape(titles[str(n)])}" if str(n) in titles else ""}</li>' for n in parsed["prs"]
        ) + "</ul></dd>"
    if parsed["elsewhere"]:
        rows += "<dt>Other repos</dt><dd>" + ", ".join(f'<a href="https://github.com/{ref.replace("#", "/pull/")}" target="_blank" rel="noopener">{html.escape(ref)}</a>' for ref in parsed["elsewhere"]) + "</dd>"
    if parsed["commits"]:
        rows += "<dt>Commits</dt><dd>" + ", ".join(f"<code>{sha[:9]}</code>" for sha in parsed["commits"]) + "</dd>"
    evidence = [*(f'<a href="/record/{n}">record #{n}</a>' for n in parsed["records"]), *(f'<a href="{html.escape(url)}" target="_blank" rel="noopener">{html.escape(url)}</a>' for url in parsed["urls"]), *(f"<code>{html.escape(path)}</code>" for path in parsed["files"])]
    if evidence:
        rows += "<dt>Evidence</dt><dd><ul>" + "".join(f"<li>{item}</li>" for item in evidence) + "</ul></dd>"
    byline = f"{who} {'answered' if chain else 'wrote this'} at {at(record['at']).astimezone(PACIFIC).strftime('%-I:%M %p')}"
    return RECORD_PAGE.format(title=html.escape(title), byline=html.escape(byline), rows=rows, raw=html.escape(record_body(record)), seq=record["seq"])


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
    freed_by, finished_by = {}, {}
    for entry in drive["prs"]:
        if entry["state"] != "landed":
            freed_by.setdefault(entry["base"], []).append(entry["number"])
    for step in steps:
        if step["tone"] == "done":
            finished_by.setdefault(max((str(item["number"]) for item in step["prs"]), key=lambda n: prs.get(n, {}).get("landed_at") or "", default=None), []).append(step["label"])
    happened = []
    for number, pr in prs.items():
        if pr.get("landed_at") and at(pr["landed_at"]) >= since:
            freed = freed_by.get(int(number), [])
            done = finished_by.get(number, [])
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


def pushed_at(records, number, head):
    """When the newest `head` or `opened` record naming `#number` at `head` was posted, or None."""
    return max((record["at"] for record in records if record["kind"] in ("head", "opened") and f"#{number}" in record["text"] and head[:7] in record["text"]), default=None)


def live_evidence(evidence, prs, runner, records):
    """The progress desk's evidence for each open PR, unless the PR's live head was pushed after it was written, so no line read at an older version reaches the owner (root #203813)."""
    return {number: entry for number, entry in evidence.items() if still_open(number, prs, runner) and entry["at"] >= (pushed_at(records, number, prs[number].get("head") or "") or "")}


def owner_asks(records, prs, told=None):
    """Each pull request's questions from the owner's row box, oldest first; one stays open until a lane confirms it down its chain, a reply to him names it, or a new version of the pull request is pushed (owner 12:03 AM Nov 2, root #308820). It carries only the reply written to him, never a record between lanes (m347, m380)."""
    children = {}
    for record in records:
        for parent in {record.get("re"), record.get("resolves"), *(record.get("also_resolves") or [])} - {None}:
            children.setdefault(parent, []).append(record)
    asks = {}
    for record in records:
        if record["lane"] != "owner-board" or record["kind"] != "ask":
            continue
        confirmed, frontier = False, [record["seq"]]
        while frontier:
            for child in children.get(frontier.pop(), []):
                frontier.append(child["seq"])
                confirmed = confirmed or (child["kind"] not in NOT_A_REPLY and child["lane"] not in OWNER_LANES)
        attrs = (record.get("fields") or {}).get("attrs") or {}
        asked_at = attrs.get("head") or ""
        reply = (told or {}).get(record["seq"]) or {}
        for number in map(str, (record.get("refs") or {}).get("prs") or []):
            live = (prs.get(number) or {}).get("head") or ""
            pushed = bool(asked_at and live and not live.startswith(asked_at))
            asks.setdefault(number, []).append({
                "seq": record["seq"],
                "at": record["at"],
                "text": record["text"],
                "answered": pushed or confirmed or bool(reply),
                "action": None if pushed else reply.get("action"),
                "reply": reply.get("id"),
                "due": attrs.get("deadline"),
                "holder": HOLDER_WORDS.get(attrs.get("owner")),
            })
    return asks


def awaiting_reply(rows, asks):
    """Each board row with the owner's latest unanswered question on it, which keeps the row out of his review queue until the answer lands."""
    def open_ask(number):
        last = (asks.get(str(number)) or [None])[-1]
        return last["seq"] if last and not last["answered"] else None

    return [{**row, "awaiting": open_ask(row["number"])} for row in rows]


def rows_written(stored):
    return max((row["written_at"] for row in stored), default=None)


def was_refused(row):
    return row["tap_refused"] not in ("", "none")


def refused_words(row):
    refused = row["tap_refused"]
    if refused == row["waits_on"]:
        return f"Your tap was refused: {refused}"
    return f"Your tap was refused: {refused}. It now waits on: {row['waits_on']}"


def review_blocker(row):
    if row["ready_for_tap"] and not row["tapped"]:
        return "your approval"
    return refused_words(row) if was_refused(row) else row["waits_on"]


def review_rows(stored, verdicts):
    rows = []
    for row in stored:
        alias = LANE_NUMBER.sub("", row["lane"])
        ready = bool(row["ready_for_tap"])
        tapped = bool(row["tapped"])
        rows.append({
            "number": row["number"],
            "head": row["head"],
            "title": row["title"],
            "change": row["for_users"],
            "plan": row["plan_row"],
            "risk": row["risk"],
            "review": row["review"],
            "blocker": review_blocker(row),
            "meta": [[label, str(row[column])] for label, column in ROW_META],
            "group": alias,
            "group_id": alias,
            "group_summary": "",
            "block": MERGE_BLOCK,
            "verdict": None if was_refused(row) else verdicts.get(MERGE_BLOCK, {}).get(str(row["number"])),
            "tapped": tapped,
            "ready": ready,
            "branch": row["branch"],
            "base": row["base"],
        })
    return rows


def with_open_rows(prs, rows):
    return {**prs, **{str(row["number"]): {**prs.get(str(row["number"]), {}), "state": "open", "head": row["head"], "branch": row["branch"], "base": row["base"], "title": row["title"], "lane": row["group"]} for row in rows}}


def merge_needs(rows, board_url):
    groups = {}
    for row in rows:
        group = groups.setdefault((row["block"], row["group_id"]), {"group": row["group"], "relation": row["group_summary"], "rows": []})
        group["rows"].append({key: row.get(key) for key in ROW_FACTS})
    items = []
    for group in groups.values():
        waiting = taps.waits_on_him(group["rows"])
        if not waiting:
            continue
        count = f"{len(waiting)} pull request{'' if len(waiting) == 1 else 's'}"
        items.append({
            "kind": "prs",
            "key": source_key(group["group"], group["relation"], [row["key"] for row in waiting]),
            "group": group["group"],
            "relation": group["relation"],
            "prs": waiting,
            "others": [row for row in group["rows"] if row not in waiting],
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
    if kind in ("display.page", "markdown"):
        return {"kind": "page", "id": child["id"], "md": child.get("md", "")}
    if kind == "image":
        return {"kind": "image", "id": child["id"], "src": ASSET_REF.sub(r"/asset/\1", child.get("src", "")), "alt": child.get("alt", "")}
    if kind == "pr":
        return {"kind": "pr", "id": child["id"], "repo": child.get("repo", ""), "number": child.get("number")}
    if kind == "table":
        return {"kind": "table", "id": child["id"], "columns": child.get("columns", []), "rows": child.get("rows", [])}
    if kind == "choice":
        options = [{"id": option["id"], "label": option["label"], "hint": option.get("hint", ""), "facts": option.get("facts", []), "detail": option.get("detail") or {}, "recommended": bool(option.get("recommended"))} for option in child.get("options", []) if not OTHER.match(option["label"])]
        return {"kind": "choice", "id": child["id"], "prompt": child.get("prompt", ""), "options": options, "answer": answer}
    if kind == "approval":
        return {"kind": "approval", "id": child["id"], "prompt": child.get("prompt", ""), "detail": child.get("detail") or {}, "answer": answer}
    return {"kind": "input", "id": child["id"], "label": child.get("label", ""), "placeholder": child.get("placeholder", ""), "multiline": bool(child.get("multiline")), "answer": answer}


def unanswered(cards, words):
    answered = {card for word in words for card in CARD_ID.findall(word["title"])}
    return [card for card in cards if card["id"] not in answered]


def needs(rows, cards, asks, board_url):
    return [*merge_needs(rows, board_url), *(card_need(card, board_url) for card in cards if card["decides"]), *(ask_need(ask) for ask in asks if not ask.get("optional") and ask.get("card") in {None, *(card["id"] for card in cards)})]


def typed_in_chat(record):
    return record["kind"] == "owner" and record["lane"] == "dashboard" and COMMUNICATOR in (record.get("to") or ())


def chat_line(record):
    return {"seq": record["seq"], "at": record["at"], "on": record.get("topic") or "", "text": THREAD_HEAD.sub("", record["text"], count=1)}


def stored_replies(read, store):
    return sorted((read(str(path)) for path in store.glob("*.json")), key=lambda entry: at(entry["at"]), reverse=True)


def drive_alerts(replies, cards):
    newest = [entry["reply"] for entry in replies[:ALERTS_SENT]]
    waiting = [entry["reply"] for entry in replies if entry["reply"].get("status") in ("needs_you", "incident") or entry["reply"].get("picks")][:ALERTS_SENT]
    kept = newest + [reply for reply in waiting if reply not in newest]
    alerts = [{"id": reply["id"], "title": reply["title"], "body": " · ".join(reply.get("picks", [])) or reply["md"].strip().partition("\n")[0].strip("*_# "), "incident": reply.get("status") == "incident"} for reply in kept]
    sent = {reply["id"] for reply in kept}
    return alerts + [{"id": own, "title": card["title"], "body": card["summary"], "incident": False, "card": card["id"]} for card in cards if (own := card["id"].removeprefix("card-")) not in sent]


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
        self.tapped = {}
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
        self.alerting = threading.Condition()
        self.alerts = None
        self.cards = []
        self.board_moved = threading.Event()
        self.browsers = {}
        self.listening = Counter()
        self.secure_url = None
        self.own_hosts = []
        self.own_ports = []
        self.served = None
        self.live_model = None
        self.watchers = 0
        self.asked_at = 0.0
        self.live_at = 0.0
        self.snapshot_at = 0.0
        self.pieces = {}
        self.files = {}
        self.merged_history = None
        self.plans = {}
        self.fetching = threading.Lock()
        self.fetched = {}
        self.lanes_due = True
        self.digests = deque(maxlen=DELTAS_KEPT)
        self.shapes = {}
        self.slow = {}
        self.slow_at = 0.0
        self.dirty_at = time.monotonic()
        self.errors = {}
        self.paths = {}
        self.board_seq = None
        self.reported_build = None
        self.reported_live = None
        self.reported_owed = None
        self.flagged = set()
        self.board_times = {}
        self.gos = {}
        self.carried = {}
        self.ci_fixes = set()
        self.board_doc = None
        self.question_boards = {}
        self.board_threads = {}
        self.card_lanes = {}
        self.curator = Curator(config, self.curator_model, self.bump)
        self.status_now = None
        self.status_rejected = None
        self.owed = {"owner_asks": {"messages": [], "taps": [], "store": []}, "coordinator_overdue": []}
        self.replies = []
        self.told_asks = {}
        self.chat_lines = []
        self.pages = {page.name: Curator(config, self.curator_model, self.bump, page) for page in PAGES}
        self.curators = (self.curator, *self.pages.values())
        self.stories = Stories(config, self.lane_names, self.worker_reports)

    def curator_model(self):
        with self.serving:
            self.watchers += 1
            asked = self.asked_at = time.monotonic()
            self.serving.wait_for(lambda: self.live_model is not None and self.live_at >= asked)
            self.watchers -= 1
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
                curator.brief = model["pages"].get(name)
            self.pages["asks"].brief = asks_brief(model["owner_asks"])
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
        row = next((row for row in self.stored_rows() if row["number"] == int(number)), None)
        shown = row and row["head"]
        if shown is None:
            return 409, {"error": f"#{number} left your board after this page last updated, so this tap changed nothing; reload the page to see where it went"}
        if verdict != "cleared" and shown[:7] != head[:7]:
            return 409, {"error": f"#{number} is now at {shown[:7]}, not the {head[:7]} this page shows, so this tap changed nothing; reload the page to see what changed"}
        if verdict == "approved" and not row["ready_for_tap"]:
            return 409, {"error": f"#{number} is not ready for your tap, so this tap changed nothing: {row['waits_on']}"}
        said = f"Owner on the dashboard: {number} {verdict} at {head[:7]}" + (f": {note}" if note else "")
        run(["cci", "post", "--drive", self.config["drive"], "--lane", "board-watch", "--kind", "owner", "--to", "root", "--id", f"dashboard-tap:{number}:{uuid.uuid4().hex}", "--pr", str(number), "--topic", block, "--attr", "event=triage.decided", "--attr", f"block={block}", "--attr", f"row={number}", "--attr", f"verdict={verdict}", "--attr", f"head={head}", "--text", said])
        self.tapped[str(number)] = {"verdict": verdict, "head": head, "at": dt.datetime.now(dt.timezone.utc).isoformat(), **({"note": note} if note else {})}
        self.touch()
        return 200, {"seq": self.store_seq()}

    def answer(self, body):
        if body["kind"] == "choice":
            return self.interact({"type": "choice.selected", "blockId": body["block"], "optionIds": body.get("options", []), **({"other": body["other"]} if body.get("other") else {})}, body.get("subject"))
        if body["kind"] == "approval":
            return self.interact({"type": "decision.created", "blockId": body["block"], "verdict": body["verdict"], **({"note": body["text"]} if body.get("text") else {})})
        if body["kind"] == "input":
            return self.interact({"type": "input.submitted", "blockId": body["block"], "text": body["text"]})
        status, out = self.interact({"type": "feedback.created", "id": str(uuid.uuid4()), "blockId": body["block"], "text": body["text"]})
        if status == 200:
            try:
                self.tell_lanes(body["block"], body["text"])
            except (OSError, subprocess.SubprocessError) as failure:
                self.flag(f"his question on {body['block']} reached the board but not the lanes parked on it: {failure}", ())
        return status, out

    def tell_lanes(self, card, text):
        lanes = self.card_lanes.get(card)
        if not lanes or "mirror" in self.config:
            return
        run(["cci", "post", "--drive", self.config["drive"], "--lane", "dashboard", "--kind", "owner", "--to", ",".join(sorted(lanes)), "--topic", f"card:{card}", "--text", f"Owner, on card {card} from the dashboard: {text}\nAnswer him on that card: cc-present reply --block {card} --body '<answer>' --session {self.config['board_session']}"])

    def asked(self, text):
        lane, kind = ("dashboard-ask-test", "note") if text.startswith("[user-test") else ("dashboard", "owner")
        run(["cci", "post", "--drive", self.config["drive"], "--lane", lane, "--kind", kind, "--to", "root", "--text", f"Asked on the dashboard: {text}"])
        return 200, {}

    def reply_back(self, text, on, about):
        if "mirror" in self.config:
            return 409, {"error": "a test copy sends nothing to the communicator"}
        thread = ["--topic", on] if on else []
        said = f"[thread: {about}]\n{text}" if on else text
        seq = run(["cci", "post", "--drive", self.config["drive"], "--lane", "dashboard", "--kind", "owner", "--to", COMMUNICATOR, *thread, "--text", said]).strip().splitlines()[-1].lstrip("#")
        return 200, {"seq": int(seq)}

    def change_request(self, number, head, text):
        if "mirror" in self.config:
            return 409, {"error": "a test copy sends no change requests"}
        try:
            seq = run(["lr", "owner", "ask", "--drive", self.config["drive"], "--pr", str(number), "--head", head], stdin=text)
        except subprocess.CalledProcessError as failure:
            if not failure.stdout.strip():
                raise
            self.report_defect(f"the question on #{number} reached the records with no received line: {failure.stderr.strip()}")
            seq = failure.stdout
        return 200, {"seq": int(seq.strip())}

    def interact(self, interaction, subject=None):
        origin, _, main = self.config["board_url"].partition("/p/")
        request = urllib.request.Request(f"{origin}/api/interactions", method="POST", headers={"Content-Type": "application/json"}, data=json.dumps({"subject": subject or main, "nonce": str(uuid.uuid4()), "interaction": interaction}).encode())
        try:
            with urllib.request.urlopen(request, timeout=15):
                pass
        except urllib.error.HTTPError as error:
            return error.code, {"error": error.read().decode().strip()}
        except (urllib.error.URLError, TimeoutError) as error:
            return 502, {"error": f"the board did not answer ({error})"}
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
        since = since.replace(microsecond=0)
        with self.fetching:
            start, after, kept = self.fetched.get(frozenset(kinds), (since, 0, []))
            if since < start:
                after, kept = 0, []
            fresh = self.read_records(kinds, since, after)
            kept = [record for record in [*kept, *fresh] if at(record["at"]) >= since]
            self.fetched[frozenset(kinds)] = (since, fresh[-1]["seq"] if fresh else after, kept)
        return kept

    def read_records(self, kinds, since, after=0):
        found = []
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
        self.records.extend(self.read_records(TIMELINE_KINDS, dt.datetime.now(dt.timezone.utc) - TIMELINE_WINDOW))
        self.chat_lines = [chat_line(record) for record in self.read_records(["owner"], dt.datetime.now(dt.timezone.utc) - CHAT_WINDOW) if typed_in_chat(record)]
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
                            if typed_in_chat(record):
                                self.chat_lines = [*self.chat_lines, chat_line(record)]
                            self.note_evidence(record)
                            with self.lock:
                                self.records.append(record)
                            self.lanes_due = True
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
                self.board_moved.set()
                for curator in self.curators:
                    curator.poke()
            self.watch.wait()
            time.sleep(5)

    def read_cards(self):
        try:
            self.cards = self.read_board(json.loads(run(["cc-present", "outcomes", "--all", "--session", self.config["board_session"]])))["cards"]
        except (OSError, subprocess.SubprocessError, ValueError) as error:
            self.errors["cards"] = f"{type(error).__name__}: {error}"
            return
        self.errors.pop("cards", None)
        self.publish_alerts()

    def follow_cards(self):
        while True:
            self.board_moved.clear()
            self.read_cards()
            self.board_moved.wait()

    def publish_alerts(self):
        with self.alerting:
            alerts = drive_alerts(self.replies, self.cards)
            if alerts != self.alerts:
                self.alerts = alerts
                self.alerting.notify_all()

    def browser_report(self, client, agent, report):
        if "page_error" in report:
            print(f"{dt.datetime.now(PACIFIC):%b %d %H:%M:%S} page error from {client}: {report['page_error']}", flush=True)
            return 200, {}
        self.browsers[client, agent] = {**report, "client": client, "agent": agent, "at": dt.datetime.now(PACIFIC).isoformat()}
        return 200, {}

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

    def owner_rows(self, known, store, lanes, threads, now, owed=None):
        ledger_file = Path(self.config["owner_ledger"])
        ledger = ledger_file.read_text()
        told = order_replies(self.replies)
        self.told_asks = closed_asks(ledger) | told_asks(ledger, told)
        answers = {path.stem: path.read_text() for path in (ledger_file.parent / "answers").glob("*.md")} | {row: reply["md"] for row, reply in told.items()}
        return owner_ask_rows(ledger, known, self.model.get("prs", {}), store, lanes, now.astimezone(PACIFIC).year, answers, threads, owed)

    def followed(self, target, *rest):
        name = target.__name__
        while True:
            try:
                target(*rest)
            except Exception as failure:
                traceback.print_exc()
                words = f"{name} crashed and restarts in {FOLLOW_RESTART} s: {failure!r}"[:300]
                self.errors[name] = words
                self.bump()
                if words not in self.flagged:
                    self.flagged.add(words)
                    self.flag(words, ())
                time.sleep(FOLLOW_RESTART)
                self.errors.pop(name, None)

    def follow_owed(self):
        drive = self.config["drive"]
        known, after = [], 0
        self.owed = {**self.owed, "owner_asks": self.owner_rows([], [], set(), {}, dt.datetime.now(dt.timezone.utc))}
        while True:
            try:
                now = dt.datetime.now(dt.timezone.utc)
                fresh = self.read_records(OWED_KINDS, now - OWED_WINDOW, after)
                known += [trimmed(record) for record in fresh if kept(record)]
                after = fresh[-1]["seq"] if fresh else after
                store = json.loads(run(["lr", "asks", "--drive", drive, "--json"]))
                threads = {ask["seq"]: ask for asks in self.model.get("asks", {}).values() for ask in asks}
                owed = {row["id"]: row for row in json.loads(run(["lr", "owner", "audit", "--drive", drive, "--json"])) or []}
                asks = self.owner_rows(known, store, set(self.lane_names()), threads, now, owed)
                if len(asks["messages"]) != len(self.owed["owner_asks"]["messages"]):
                    self.pages["asks"].poke()
                self.owed = {
                    "owner_asks": asks,
                    "coordinator_overdue": overdue_rows(store, self.model.get("preventions", []), now),
                }
                self.reported_owed = None
                self.touch()
                self.bump()
            except (OSError, subprocess.SubprocessError, ValueError) as failure:
                if self.reported_owed != str(failure):
                    self.reported_owed = str(failure)
                    self.flag(f"his messages on the dashboard could not refresh: {failure}", ())
            until_changed(Path(self.config["owner_ledger"]), SLOW_REFRESH)

    def follow_replies(self):
        store = Path(self.config["replies"])
        seen = None
        while True:
            stamp = store.stat().st_mtime_ns if store.exists() else None
            if stamp != seen:
                seen = stamp
                self.replies = stored_replies(self.read, store) if stamp else []
                self.publish_alerts()
                self.touch()
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
            zipped = snapshot.read_bytes()
            lean = gzip.decompress(zipped)
            view = json.loads(lean)
            cut = json.dumps(now_view(view)).encode()
            self.served = {"version": view["version"], "sig": None, "live": None, "views": {}, "bodies": {("lean",): (lean, zipped), ("now",): (cut, gzip.compress(cut, 5))}}

    def refresh_loop(self, snapshot):
        seen = None
        while True:
            time.sleep(0.5)
            with self.serving:
                asked = self.asked_at > self.live_at
                if not (asked or self.watchers):
                    continue
            self.rebuild()
            with self.changed:
                moved, seen = self.version != seen, self.version
            if moved or asked or time.monotonic() - self.live_at >= LIVE_REFRESH:
                self.serve(snapshot)

    def serve(self, snapshot):
        started = time.monotonic()
        with self.changed:
            model = dict(self.model)
        if "prs" in model and (live := self.live_or_report(model)) is not None:
            self.publish(live, snapshot)
        with self.serving:
            self.live_at = started
            self.serving.notify_all()

    def publish(self, live, snapshot):
        view = browser_view(live)
        lean = self.encode("lean", view)
        sig = hashlib.sha256(b"".join(key.encode() + digest for key, (_, _, digest) in lean.items())).hexdigest()
        if self.served and self.served["sig"] == sig:
            return
        version = (self.served["version"] if self.served else 0) + 1
        live = {**live, "version": version}
        lean = {**lean, "version": encoded(version)}
        views = {"lean": lean, "now": self.encode("now", now_view({**view, "version": version}), lean)}
        for name in SCREENS:
            views[screen_kind(name)] = self.encode(screen_kind(name), screen_view(name, view, version))
        served = {"version": version, "sig": sig, "live": live, "views": views, "bodies": {}}
        shapes = {digest: self.shapes.get(digest) or rows_of(value) for kind in ROW_DELTA_KINDS for value, _, digest in views[kind].values() if isinstance(value, (list, dict))}
        with self.serving:
            self.live_model = live
            self.served = served
            self.digests.append((version, {kind: {key: digest for key, (_, _, digest) in pieces.items()} for kind, pieces in views.items()}))
            kept = {digest for _, kinds in self.digests for kind in ROW_DELTA_KINDS for digest in kinds[kind].values()}
            self.shapes = {digest: shape for digest, shape in (self.shapes | shapes).items() if digest in kept}
            self.serving.notify_all()
        if time.monotonic() - self.snapshot_at >= SNAPSHOT_EVERY:
            self.snapshot_at = time.monotonic()
            with self.serving:
                _, zipped = self.built(served, ("lean",), lambda: joined(lean, lean))
            snapshot.with_suffix(".tmp").write_bytes(zipped)
            snapshot.with_suffix(".tmp").replace(snapshot)

    def encode(self, kind, view, reuse=None):
        kept = self.pieces.get(kind, {})
        pieces = {}
        for key, value in view.items():
            pieces[key] = next((found for found in ((reuse or {}).get(key), kept.get(key)) if found and found[0] is value), None) or encoded(value)
        self.pieces[kind] = pieces
        return pieces

    def body(self, kind, since=None, by_row=False):
        with self.serving:
            if not self.watchers:
                self.asked_at = time.monotonic()
            self.serving.wait_for(lambda: self.served and (kind in ("lean", "now") or self.served["live"]), timeout=60)
            served = self.served
            if not served or (kind not in ("lean", "now") and not served["live"]):
                return None
            past = dict(self.digests).get(since)
            if past and by_row and (delta := self.built(served, (kind, since, "rows"), lambda: changed_rows(served["version"], served["views"][kind], past[kind], self.shapes))):
                return delta
            if past and (delta := self.built(served, (kind, since), lambda: changed_since(served["version"], served["views"][kind], past[kind]))):
                return delta
            return self.built(served, (kind,), lambda: json.dumps(served["live"]).encode() if kind == "full" else joined(served["views"][kind], served["views"][kind]))

    def built(self, served, key, raw):
        if key not in served["bodies"]:
            body = raw()
            served["bodies"][key] = body and (body, gzip.compress(body, 5))
        return served["bodies"][key]

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
            row["go_head"] = row["head"] if row.get("tapped") else row["checked_at"] if exempt or self.carried.get(row["number"]) else None
            stored = verdict_state({**row, "go_head": None}) == "approved"
            row["state"] = verdict_state(row)
            if row["state"] == "you" and row.get("ready") is False:
                row["state"] = "not-ready"
            row["ci_fix_go"] = row["state"] == "approved" and not stored and exempt

    def stored_rows(self):
        store_path = Path(self.config["steps"]).parents[1] / LR_STORE
        with sqlite3.connect(f"file:{store_path}?mode=ro", uri=True) as store:
            store.row_factory = sqlite3.Row
            return [dict(row) for row in store.execute("SELECT * FROM review_rows WHERE repo = ? AND state NOT IN ('landed', 'closed', 'superseded') ORDER BY ready_for_tap DESC, number", (self.config["repo"],))]

    def follow_rows(self):
        seen = None
        while True:
            written = rows_written(self.stored_rows())
            if written != seen:
                seen = written
                self.touch()
            time.sleep(1)

    def store_seq(self):
        subject = self.config["board_url"].rsplit("/p/", 1)[1]
        with sqlite3.connect(f"file:{STORE}?mode=ro", uri=True) as store:
            return store.execute("SELECT max(seq) FROM events JOIN subjects ON subjects.id = events.subject_id WHERE subjects.slug = ?", (subject,)).fetchone()[0]

    def read_threads(self):
        subject = self.config["board_url"].rsplit("/p/", 1)[1]
        with sqlite3.connect(f"file:{STORE}?mode=ro", uri=True) as store:
            events = store.execute(f"SELECT json_extract(payload, '$.blockId'), events.type, events.created_at, json_extract(payload, '$.text'), json_extract(payload, '$.md') FROM events JOIN subjects ON subjects.id = events.subject_id WHERE subjects.slug = ? AND events.type IN ({', '.join('?' * len(THREAD_EVENTS))}) ORDER BY events.seq", (subject, *THREAD_EVENTS)).fetchall()
        return taps.card_threads(events)

    def answer_times(self):
        subject = self.config["board_url"].rsplit("/p/", 1)[1]
        with sqlite3.connect(f"file:{STORE}?mode=ro", uri=True) as store:
            found = store.execute(f"SELECT json_extract(payload, '$.blockId'), max(events.created_at) FROM events JOIN subjects ON subjects.id = events.subject_id WHERE subjects.slug = ? AND events.type IN ({', '.join('?' * len(ANSWER_EVENTS))}) GROUP BY 1", (subject, *ANSWER_EVENTS)).fetchall()
        return {block: dt.datetime.fromtimestamp(stamp, dt.timezone.utc).isoformat() for block, stamp in found}

    def slack_cards(self):
        with sqlite3.connect(f"file:{STORE}?mode=ro", uri=True) as store:
            boards = store.execute("SELECT subjects.slug, subjects.session_id, max(events.seq) FROM subjects JOIN events ON events.subject_id = subjects.id WHERE subjects.slug LIKE ? AND subjects.status = 'open' GROUP BY subjects.id", (QUESTION_BOARDS,)).fetchall()
        for slug, session, seq in boards:
            if self.question_boards.get(slug, (None,))[0] != seq:
                self.question_boards[slug] = (seq, taps.slack_cards(json.loads(run(["cc-present", "outcomes", "--all", "--session", session])), slug))
        return [card for slug, _, _ in boards for card in self.question_boards[slug][1]]

    def tap_queue(self, board, model, drive, now):
        titles = {row["number"]: row["title"] for row in board["rows"]}
        urgent = {f"card-{entry['reply']['id']}" for entry in self.replies if entry["reply"].get("status") == "incident"}
        blocks = taps.open_blocks(self.fetch(("blocked",), now - BLOCKS_WINDOW), model["lanes"])
        self.card_lanes = taps.parked_on(blocks, {card["id"] for card in board["cards"]})[0]
        return taps.tap_queue(board["rows"], board["cards"], self.slack_cards(), drive["prs"], blocks, lambda number: titles.get(number) or taps.plain_title(model["prs"][str(number)].get("title", "")), urgent)

    def board(self):
        seq = self.store_seq()
        if seq != self.board_seq:
            self.board_doc = json.loads(run(["cc-present", "outcomes", "--all", "--session", self.config["board_session"]]))
            self.board_times = self.answer_times()
            self.board_threads = self.read_threads()
            self.board_seq = seq
        read = self.read_board(self.board_doc)
        stored = self.stored_rows()
        read["rows"] = review_rows(stored, {MERGE_BLOCK: {**self.board_doc["interactions"]["triage"].get(MERGE_BLOCK, {}), **self.tapped}})
        read["rows_at"] = rows_written(stored)
        answered = {card: {"picked": answer["picked"], "at": max(self.board_times[ask] for ask in answer["asks"])} for card, answer in read["answered"].items()}
        return {**read, "answered": answered, "seq": seq}

    def screens_changed(self, seen):
        history = dict(self.digests)
        return changed_screens(history.get(seen), history[self.served["version"]])

    def live(self, model):
        board = self.board()
        model = {**model, "prs": with_open_rows(model["prs"], board["rows"])}
        self.check_gos(board["rows"])
        pushes = [record for record in model["records"] if record["kind"] in ("head", "opened")]
        board["rows"] = awaiting_reply(board["rows"], model["asks"])
        board["cards"] = unanswered(board["cards"], model["words"])
        stamp(board)
        now = dt.datetime.now(dt.timezone.utc)
        progress = self.read(self.config["steps"])
        activity_records = [*model["records"], *model["lanes"]]
        gated = {row["number"] for row in board["rows"] if not row["go_head"]}
        drive = drive_prs(model["prs"], board["rows"], {number: head for number, head in self.gos.items() if number not in gated}, activity_records, model["trees"], model["tree_names"], progress["steps"], self.config["parts"], hold_reasons(holds.read(Path(self.config["holds"])), model["prs"]), now)
        workers = drive_workers(activity_records, model["prs"], progress["steps"], now)
        steps = step_rows(progress["steps"], drive, workers, board["answered"])
        releases = release_path(progress.get("now", {}).get("releases", []))
        milestone_rows = self.milestone_state(drive["prs"], now, board["answered"])
        self.flag_stale(milestone_rows, progress["steps"], board["answered"], now)
        queue = self.tap_queue(board, model, drive, now)
        board["rows"] = taps.in_queue_order(queue, board["rows"], lambda row: f"land-{row['number']}")
        board["cards"] = taps.in_queue_order(queue, board["cards"], lambda card: card["id"])
        need_items = needs(board["rows"], board["cards"], model["story"].get("asks", []), board["url"])
        return {
            **model,
            "board": board,
            "drive": drive,
            "workers": workers,
            "desks": drive_workers(activity_records, model["prs"], progress["steps"], now, desks=True),
            "steps": steps,
            "parts": self.config["parts"],
            "now": {**progress.get("now", {}), "releases": releases},
            "fyi": progress.get("fyi", []),
            "steps_at": progress["updated"],
            "history": self.history(progress, self.milestone_cache[2]),
            "pr_state": progress.get("pr_state", {}),
            "live": LIVE_SOURCES,
            "needs": need_items,
            "taps": queue,
            "journeys": journeys(drive, steps, releases, need_items, model["records"], model["prs"], progress.get("now", {}), model["tree_names"], now),
            "brief": self.curator.brief,
            "brief_error": self.curator.error,
            "status_now": self.status_now,
            "status_rejected": self.status_rejected,
            "stack": self.read(self.config["stack"])["prs"],
            "status_table": self.read(self.config["status_table"], str),
            "status_tables": [table for table in self.read(self.config["status_tables"]) if table["id"] not in ("plan", "what-waits-on-you")],
            "step_records": {step["id"]: {"wrong": step.get("went_wrong", ""), "lanes": step.get("lanes", []), "prs": step.get("prs", [])} for step in progress["steps"]},
            **self.owed,
            "owner_asks": titled_asks(self.owed["owner_asks"], self.pages["asks"].brief),
            "replies": self.replies,
            "chat_lines": self.chat_lines,
            "register_log": "register" in self.config,
            "latest_words": self.words(),
            "evidence": live_evidence(self.evidences(), model["prs"], progress.get("pr_state", {}), pushes),
            "pages": without_finished_prs({name: curator.brief for name, curator in self.pages.items() if name != "asks"}, model["prs"], progress.get("pr_state", {})),
            "milestone_state": milestone_rows,
            "milestones": [m["name"] for m in self.config["milestones"]],
            "milestones_label": self.config["milestones_label"],
            "own_hosts": self.own_hosts,
            "own_ports": self.own_ports,
            "views": self.config.get("views", {}),
        }

    def read(self, path, parse=json.loads):
        stat = os.stat(path)
        stamp = (stat.st_mtime_ns, stat.st_size)
        if (found := self.files.get(path)) is None or found[0] != stamp:
            found = self.files[path] = (stamp, parse(Path(path).read_text()))
        return found[1]

    def history(self, progress, releases):
        if not (self.merged_history and self.merged_history[0] is progress and self.merged_history[1] is releases):
            self.merged_history = (progress, releases, with_releases(progress.get("history", {}), releases))
        return self.merged_history[2]

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
        lanes = self.model.get("lanes", [])
        if self.lanes_due or refresh_slow or "lanes" not in self.model:
            self.lanes_due = False
            try:
                lanes = cci("lanes", drive=self.config["drive"])
                self.errors.pop("lanes", None)
            except OSError as error:
                self.lanes_due = True
                self.errors["lanes"] = f"the cci daemon at {CCI} did not answer ({error}); workers are as of the last answer"
        return {
            **self.slow,
            "title": self.config["title"],
            "repo": self.config["repo"],
            "records": records[-1500:],
            "asks": owner_asks(records, self.slow["prs"], self.told_asks),
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
            "preventions": self.read_preventions(),
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

    def read_preventions(self):
        try:
            retro = Path(drive.newest_install()["installPath"]) / "bin" / "retro.py"
            rows = [json.loads(line) for line in run([str(retro), "--drive", self.config["drive"], "rows", "--json"]).splitlines() if line.strip()]
        except (OSError, TypeError, ValueError, subprocess.SubprocessError) as error:
            self.errors["preventions"] = f"the retro table did not read, so this section is incomplete ({type(error).__name__}: {getattr(error, 'stderr', '') or error})"[:300]
            return []
        self.errors.pop("preventions", None)
        return [row for row in rows if not row.get("dup_of")]

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
        for doc in json.loads(self.ccn("doc", "list", "--label", self.config["progress_label"], "--json"))[:6]:
            key = (doc["id"], doc["updated_at"])
            if key not in self.plans:
                self.plans[key] = parse_plan(self.ccn("doc", "show", doc["id"]))
            if plan := self.plans[key]:
                return plan
        return None, []

    def read_board(self, board):
        interactions = board["interactions"]
        retired = set(board.get("retired") or [])
        cards, answered = [], {}
        posts = {"/".join(found.groups()): block["id"] for block in board["doc"]["blocks"] if block["type"] == "card" and (found := SLACK_POST.search(block.get("slack", {}).get("url", "")))}
        for block in board["doc"]["blocks"]:
            if block["type"] != "card" or block["id"] in retired:
                continue
            groups = [child for child in block.get("children", []) if child["type"] == "pr-groups"]
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
            embed = any(child["type"] not in NATIVE or child.get("multi") for child in children)
            parts = [] if embed else picks.fold_picks([card_part(child, answers.get(child["id"])) for child in children])
            options = [{"id": option["id"], "label": option["label"], "recommended": bool(option.get("recommended"))} for option in (choice or {}).get("options", []) if not OTHER.match(option["label"])]
            recommended = next((option["label"] for option in options if option["recommended"]), None)
            cards.append({
                "id": block["id"], "title": picks.context_title(block.get("title", "")), "summary": picks.context_summary(block.get("summary", ""), options), "chips": block.get("chips") or [], "prs": sorted({int(number) for number in taps.PR_NUMBER.findall(json.dumps(block))} | {child["number"] for child in children if child["type"] == "pr"}),
                "recommended": recommended, "choice": choice and choice["id"], "options": options, "decides": decides,
                "parts": parts,
                "thread": self.board_threads.get(block["id"], []),
                "embed": f"{self.config['board_url']}?{urllib.parse.urlencode({'block': block['id'], 'theme': 'dark'})}" if embed else None,
            })
        return {"cards": cards, "answered": answered, "card_posts": posts, "url": self.config["board_url"]}


def own_names(host):
    me = json.loads(run(["tailscale", "status", "--json"]))["Self"]
    name = me["DNSName"].rstrip(".").lower()
    return {host, name, name.split(".")[0], *me["TailscaleIPs"]}


def handler(drive, tailnet_owner, names):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def owner_calling(self):
            if urllib.parse.urlsplit(f"//{self.headers.get('Host', '')}").hostname not in names:
                return False
            try:
                who = json.loads(run(["tailscale", "whois", "--json", self.client_address[0]]))
                return not who["Node"].get("Tags") and who["UserProfile"]["LoginName"] == tailnet_owner
            except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, AttributeError):
                return False

        def refuse(self):
            return self.send(403, "application/json", json.dumps({"error": "only the drive's owner can do this, and this device is not signed in as them"}).encode())

        def source_lines(self):
            if not self.owner_calling():
                return self.refuse()
            query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(self.path).query))
            path, line, to = query.get("path", ""), query.get("line", ""), query.get("to", "")
            if not picks.SOURCE_PATH.fullmatch(path) or ".." in path.split("/") or not line.isdecimal() or not to.isdecimal():
                return self.send(400, "application/json", json.dumps({"error": "That is not a file and line in the repo."}).encode())
            repo = drive.config["repo"]
            try:
                lines = gh(repo, "api", f"repos/{repo}/contents/{urllib.parse.quote(path)}", "-H", "Accept: application/vnd.github.raw+json").split("\n")
            except (OSError, subprocess.SubprocessError):
                return self.send(200, "application/json", json.dumps({"error": f"GitHub has no {path} on {repo}'s default branch, or could not be reached."}).encode())
            return self.send(200, "application/json", json.dumps({"lines": picks.source_window(lines, int(line), int(to))}).encode())

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if STORY_ROUTE.fullmatch(path) and not self.owner_calling():
                return self.refuse()
            if path == "/" and drive.secure_url and not isinstance(self.connection, ssl.SSLSocket):
                self.send_response(302)
                self.send_header("Location", drive.secure_url)
                self.send_header("Content-Length", "0")
                return self.end_headers()
            if path in STATIC:
                name, kind = STATIC[path]
                body = (HERE / name).read_text()
                if name == "index.html":
                    for asset, _ in STATIC.values():
                        body = body.replace(f"/{asset}", f"/{asset}?v={(HERE / asset).stat().st_mtime_ns}")
                    body = body.replace("</body>", ASSIST.read_text() + "</body>").replace("<head>", f'<head>\n<meta name="build" content="{BUILD}">\n<meta name="secure-url" content="{drive.secure_url or ""}">', 1)
                    if "CEREBRAS_API_KEY" in os.environ and self.owner_calling():
                        body = body.replace("<html", '<html class="ai-on"', 1)
                return self.send(200, kind, body.encode())
            if found := DIAGRAM_ROUTE.fullmatch(path):
                reply = next((entry["reply"] for entry in drive.replies if entry["reply"]["id"] == found[1] and entry["reply"].get("diagram")), None)
                if not reply:
                    return self.send(404, "text/plain", b"no such diagram")
                doc = base64.b64encode(json.dumps(reply["diagram"]).encode()).decode()
                return self.send(200, "text/html; charset=utf-8", DIAGRAM_PAGE.format(title=html.escape(reply["title"]), doc=doc).encode())
            if path == "/api/model":
                kind = "full" if "full=1" in self.path else "now" if "tab=now" in self.path else "lean"
                since = kind != "full" and re.search(r"since=(\d+)", self.path)
                if not (body := drive.body(kind, int(since[1]) if since else None, "rows=1" in self.path)):
                    return self.send(503, "text/plain", b"The dashboard is building its first view.")
                return self.send_built(*body)
            if found := SCREEN_ROUTE.fullmatch(path):
                if found[1] not in SCREENS:
                    return self.send(404, "text/plain", b"no such screen")
                since = re.search(r"since=(\d+)", self.path)
                if not (body := drive.body(screen_kind(found[1]), int(since[1]) if since else None)):
                    return self.send(503, "text/plain", b"The dashboard is building its first view.")
                return self.send_built(*body)
            if path == "/api/words":
                with drive.lock:
                    words = drive.model.get("words", [])
                return self.send(200, "application/json", json.dumps(words).encode())
            if path == "/api/source":
                return self.source_lines()
            if path == "/api/stream":
                return self.stream()
            if path == "/api/alerts":
                return self.alerts()
            if path == "/api/browser":
                return self.send(200, "application/json", json.dumps({"browsers": list(drive.browsers.values()), "listening": drive.listening}).encode())
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
            if found := ASSET_ROUTE.fullmatch(path):
                origin = drive.config["board_url"].partition("/p/")[0]
                try:
                    with urllib.request.urlopen(f"{origin}/assets/{found[1]}", timeout=15) as asset:
                        return self.send(200, asset.headers.get_content_type(), asset.read())
                except OSError as error:
                    return self.send(502, "text/plain", str(error).encode())
            if found := RECORD_ROUTE.fullmatch(path):
                try:
                    if "raw=1" in self.path.partition("?")[2].split("&"):
                        return self.source("The record this links to", run(["cci", "show", found[1]]))
                    record = json.loads(run(["cci", "show", found[1], "--json"]))
                    chain = []
                    while len(chain) < 4 and (parent := (chain[-1] if chain else record).get("re")) and not (chain and chain[-1]["lane"] in OWNER_LANES):
                        chain.append(json.loads(run(["cci", "show", str(parent), "--json"])))
                    said = json.loads(Path(drive.config["steps"]).read_text()).get("said", {}).get(found[1])
                    titles = {n: pr["title"] for n, pr in (drive.live_model or {}).get("prs", {}).items() if pr.get("title")}
                    return self.send(200, "text/html; charset=utf-8", record_page(record, chain, said, titles).encode())
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
            review = REVIEW_ROUTE.fullmatch(self.path)
            if not review and self.path not in (MODEL_ROUTE, "/api/verdict", "/api/answer", "/api/asked", "/api/reply", "/api/change", "/api/browser"):
                return self.send(404, "text/plain", b"not found")
            if self.headers.get_content_type() != "application/json":
                return self.send(415, "text/plain", b"Content-Type must be application/json")
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            if not self.owner_calling():
                return self.refuse()
            if review:
                try:
                    url = drive.review_url(int(review[1]))
                except subprocess.CalledProcessError as error:
                    return self.send(502, "application/json", json.dumps({"error": (error.stderr or error.stdout).strip()}).encode())
                return self.send(200, "application/json", json.dumps({"url": url, "assistant": int(review[1]) in drive.assistant.watching, "stale": int(review[1]) in drive.assistant.stale}).encode())
            body = json.loads(raw)
            if self.path == MODEL_ROUTE:
                call = {name: body[name] for name in MODEL_FIELDS if name in body} | {"model": MODEL_NAME}
                return self.relay(urllib.request.Request(MODEL_URL, data=json.dumps(call).encode(), method="POST", headers={"Content-Type": "application/json", "Authorization": f"Bearer {os.environ['CEREBRAS_API_KEY']}", "User-Agent": "drive-dashboard"}))
            if self.path == "/api/answer":
                status, out = drive.answer(body)
            elif self.path == "/api/asked":
                status, out = drive.asked(body["text"])
            elif self.path == "/api/browser":
                status, out = drive.browser_report(self.client_address[0], self.headers.get("User-Agent", ""), body)
            elif self.path == "/api/reply":
                try:
                    status, out = drive.reply_back(body["text"].strip(), body["on"], body["about"])
                except (OSError, subprocess.SubprocessError) as failure:
                    status, out = 502, {"error": f"the drive's records could not take it ({getattr(failure, 'stderr', None) or failure})"}
            elif self.path == "/api/change":
                try:
                    status, out = drive.change_request(int(body["number"]), body["head"], body["text"].strip())
                except (OSError, subprocess.SubprocessError) as failure:
                    status, out = 502, {"error": f"the drive's records could not take it ({getattr(failure, 'stderr', None) or failure})"}
            else:
                status, out = drive.decide(body["block"], body["number"], body["verdict"], body.get("head"), body.get("note"))
            if self.path in ("/api/answer", "/api/verdict"):
                print(f"{dt.datetime.now(PACIFIC):%b %d %H:%M:%S} {self.path} {body.get('block')} {body.get('number', '')} -> {status} {out.get('error', '')}", flush=True)
            self.send(status, "application/json", json.dumps(out).encode())

        def source(self, title, text):
            return self.send(200, "text/html; charset=utf-8", SOURCE_PAGE.format(title=title, text=html.escape(text)).encode())

        def send(self, status, kind, body, retry_after=None):
            zipped = len(body) > 4096 and "gzip" in self.headers.get("Accept-Encoding", "")
            if zipped:
                body = gzip.compress(body, 5)
            self.send_response(status)
            self.send_header("Content-Type", kind)
            if retry_after:
                self.send_header("Retry-After", retry_after)
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

        def relay(self, request):
            try:
                upstream = urllib.request.urlopen(request, timeout=60)
            except urllib.error.HTTPError as error:
                return self.send(error.code, "text/plain", error.read(), error.headers["Retry-After"])
            except OSError as error:
                return self.send(502, "text/plain", f"the upstream did not answer: {error}".encode())
            self.send_response(200)
            self.send_header("Content-Type", upstream.headers["Content-Type"])
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with upstream:
                try:
                    for line in upstream:
                        self.wfile.write(line)
                        self.wfile.flush()
                except OSError:
                    return

        def alerts(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            client, sent = self.client_address[0], None
            with drive.alerting:
                drive.listening[client] += 1
            try:
                self.wfile.write(f"event: build\ndata: {BUILD}\n\n".encode())
                while True:
                    with drive.alerting:
                        drive.alerting.wait_for(lambda: drive.alerts is not sent, timeout=15)
                        alerts = drive.alerts
                    if alerts is sent:
                        self.wfile.write(b": keep-alive\n\n")
                    else:
                        self.wfile.write(f"event: alerts\ndata: {json.dumps(alerts)}\n\n".encode())
                        sent = alerts
                    self.wfile.flush()
            except OSError:
                return
            finally:
                with drive.alerting:
                    drive.listening[client] -= 1

        def stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(f"event: build\ndata: {BUILD}\n\n".encode())
            seen = -1
            with drive.serving:
                drive.watchers += 1
            try:
                while True:
                    with drive.serving:
                        drive.serving.wait_for(lambda: drive.served and drive.served["version"] != seen, timeout=15)
                        version = drive.served and drive.served["version"]
                        screens = drive.screens_changed(seen) if version not in (None, seen) and drive.served["live"] else []
                    if version in (None, seen):
                        self.wfile.write(b": keep-alive\n\n")
                    else:
                        self.wfile.write(f"event: version\ndata: {version}\n\n".encode())
                        if screens:
                            self.wfile.write(f"event: screens\ndata: {json.dumps({'version': version, 'screens': screens})}\n\n".encode())
                        seen = version
                    self.wfile.flush()
            except OSError:
                return
            finally:
                with drive.serving:
                    drive.watchers -= 1

    return Handler


def tailnet_name():
    return json.loads(run(["tailscale", "status", "--json"]))["Self"]["DNSName"].rstrip(".").lower()


def serve_tls(drive, config, app, store):
    name, cert, key = tailnet_name(), store / "tls.crt", store / "tls.key"
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)

    def certify():
        run(["tailscale", "cert", "--cert-file", str(cert), "--key-file", str(key), name])
        context.load_cert_chain(cert, key)

    def keep_certified():
        while True:
            time.sleep(CERT_CHECK)
            try:
                certify()
            except (subprocess.SubprocessError, ssl.SSLError):
                traceback.print_exc()

    certify()
    server = ThreadingHTTPServer((config["host"], config["tls_port"]), app)
    server.daemon_threads = True
    server.socket = context.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
    threading.Thread(target=keep_certified, daemon=True).start()
    drive.secure_url = f"https://{name}:{config['tls_port']}/"
    print(drive.secure_url, flush=True)
    server.serve_forever()


def reap_inherited():
    lister = subprocess.Popen(["ps", "-A", "-o", "pid=,ppid="], stdout=subprocess.PIPE, text=True)
    rows = [line.split() for line in lister.communicate()[0].splitlines()]
    for pid in {int(pid) for pid, ppid in rows if int(ppid) == os.getpid()} - {lister.pid}:
        threading.Thread(target=os.waitpid, args=(pid, 0), daemon=True).start()


def main():
    reap_inherited()
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
    for target, rest in ((drive.follow_code, ()), *writers, (drive.follow_records, (after,)), (drive.follow_board, ()), (drive.follow_rows, ()), (drive.follow_cards, ()), (drive.follow_steps, ()), (drive.follow_owed, ()), (drive.follow_replies, ()), (drive.follow_status, ()), (drive.refresh_loop, (snapshot,)), (drive.follow_paths, ())):
        threading.Thread(target=drive.followed, args=(target, *rest), daemon=True).start()
    names = own_names(config["host"])
    drive.own_hosts = sorted(names)
    drive.own_ports = [str(config[key]) for key in ("port", "tls_port") if key in config]
    app = handler(drive, config["tailnet_owner"], names)
    if "tls_port" in config:
        threading.Thread(target=serve_tls, args=(drive, config, app, args.config.parent), daemon=True).start()
    server = ThreadingHTTPServer((config["host"], config["port"]), app)
    server.daemon_threads = True
    print(f"http://{config['host']}:{config['port']}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
