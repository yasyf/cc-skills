from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, NamedTuple
from zoneinfo import ZoneInfo

import plainwords

CURATOR_KINDS = {"blocker", "ask", "answer", "decide", "go", "landed", "release", "incident", "mechanism", "fix-live", "hold", "lift", "owner", "defect", "state", "report", "opened", "decision"}
MIN_GAP = 120
HISTORY = 50
WORDS_WINDOW = dt.timedelta(hours=2)
TROUBLE_WINDOW = dt.timedelta(hours=3)
TROUBLE_KINDS = {"blocker", "incident", "mechanism", "fix-live", "ask", "decide", "decision", "hold", "lift"}
SUCCESSOR = re.compile(r"-\d+$")
ROOT = {"root", "main"}
COORDINATORS = re.compile(r"^(root|main)$|(^|-)desk(-|$)|refresher|watchdog|register|landing|(^|-)board(-|$)|prompt|fidelity|progress|comms|tooling|finder|sweep|batch-post")
PR_MENTION = re.compile(r"#(\d{4,6})\b")
CLOCK = re.compile(r"\d{1,2}:\d{2} [AP]M")
WAITED = re.compile(r"\bfor \d+ (?:min|minutes?|hours?|h)\b|\bsince \d{1,2}:\d{2} [AP]M\b")
CHECK_PLACEHOLDER = re.compile(r"\bfixes [\w-]+/[\w-]+\.?$")
BYSTANDERS = ("dashboard", "register", "cc-present", "desk-runner", "stall-watch")
CHANGE_KINDS = {"decide", "decision", "hold", "lift", "blocker", "go", "landed", "head", "review", "fix-live", "answer", "mechanism", "opened", "state", "report", "done", "note", "handoff", "stand-down", "stopped"}
CHANGE_WINDOW = dt.timedelta(hours=12)
FINDING = re.compile(r"finding|defect|blocker|must|fails?\b", re.I)
QUIET = ("--setting-sources", "", "--strict-mcp-config", "--disable-slash-commands", "--tools", "", "--no-session-persistence")
PACIFIC = ZoneInfo("America/Los_Angeles")
IDENTITY = ("key", "id", "item", "number", "title")
SLOTS = {
    ("headline",): "headline",
    ("because",): "sentence",
    ("band", "text"): "sentence",
    ("progress", "sentence"): "headline",
    ("calm",): "sentence",
}
STOP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["state", "label", "why"],
    "properties": {
        "state": {"enum": ["live", "progress", "next", "paused", "broken", "not"]},
        "label": {"type": "string", "description": "one word, copied from milestone_state, such as Live, Blocked, Building, Releasing, Testing, Later"},
        "why": {"type": "string", "description": "at most three words of reason for a paused or broken milestone, else empty"},
    },
}
NODE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "text", "state", "how", "item", "change", "since"],
    "properties": {
        "id": {"type": "string"},
        "change": {"type": "integer", "description": "the number from open_changes of the change this node is about, or 0"},
        "text": {"type": "string", "description": "what is stuck or what must happen, at most 10 words"},
        "state": {"enum": ["fixing", "you", "nobody", "waiting", "done"]},
        "how": {"type": "string", "description": "who acts next, named by their work and never as another drive or team, what they do, and by when, at most 12 words, such as 'The cloud runs worker rebuilds the fix by 5 PM' or 'Lands on your tap'; a time comes only from a record"},
        "item": {"type": "string", "description": "the work item id fixing it, or empty"},
        "since": {"type": "string", "description": "Pacific clock time of the record that started this wait, such as 11:45 AM; empty only when done"},
    },
}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["headline", "because", "band", "progress", "paths", "calm", "work"],
    "properties": {
        "headline": {"type": "string"},
        "because": {"type": "array", "maxItems": 3, "items": {"type": "string"}},
        "band": {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "text"],
            "properties": {"kind": {"enum": ["none", "incident"]}, "text": {"type": "string"}},
        },
        "progress": {
            "type": "object",
            "additionalProperties": False,
            "required": ["sentence", "stops"],
            "properties": {"sentence": {"type": "string"}, "stops": {"type": "array", "items": STOP_SCHEMA}},
        },
        "paths": {
            "type": "array",
            "maxItems": 2,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["milestone", "nodes", "edges"],
                "properties": {
                    "milestone": {"type": "integer", "description": "index into the milestones list"},
                    "nodes": {"type": "array", "maxItems": 3, "items": NODE_SCHEMA},
                    "edges": {
                        "type": "array",
                        "items": {"type": "object", "additionalProperties": False, "required": ["from", "to"], "properties": {"from": {"type": "string"}, "to": {"type": "string"}}},
                    },
                },
            },
        },
        "calm": {"type": "string"},
        "work": {
            "type": "array",
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["item", "workers", "changes", "title", "goal", "status", "latest", "unblocks", "since", "asks"],
                "properties": {
                    "item": {"type": "string", "description": "stable short id, lowercase words joined by dashes, kept across briefs"},
                    "workers": {"type": "array", "items": {"type": "string"}},
                    "changes": {"type": "array", "items": {"type": "integer"}},
                    "title": {"type": "string"},
                    "goal": {"type": "string", "description": "what the work is for, in user terms, at most 20 words"},
                    "status": {"enum": ["Under review", "Ready for you", "Ready to merge", "Testing", "Building", "Rolling out", "Stuck", "Waiting on tests", "Fixing", "Investigating"]},
                    "latest": {"type": "string"},
                    "unblocks": {"type": "string", "description": "id of the blocker node this work fixes, or empty"},
                    "since": {"type": "string", "description": "Pacific clock time its current status began, such as 5:52 PM"},
                    "asks": {"type": "array", "items": {"type": "string"}, "description": "for Ready for you, the ask of each waiting_on_owner entry it waits on; else empty"},
                },
            },
        },
    },
}

RULES = """Rules for every string:
- Written for a human who never reads the agents' records. Plain English, short sentences. Use the real system names where they are the fact (Sand, Iris, SandSQL, sandsql-server, S3, Litestream, the api, Buildkite), never a generic stand-in such as "the database server" or "cloud storage".
- Never a record number, pull request number, release code (R1, P8), step number, hash, file path, branch, URL, worker name from worker_names, or these coined words: lane, desk, watchdog, finder, slice, census, fidelity, register, mirror, forest, tap row, ci-grade, rider, carry, restack, rebase, enqueue, old world, new world, cherry-pick. Say "a worker", "the merge process", "old code", "new code" instead. Describe what changed for users or what is left, never git mechanics.
- Never a branch, command, crate or package name (dev, main, "sand sql blob", "ssql"); say what it does for users. Spell SandSQL as SandSQL.
- Times are Pacific clock times like 12:40 PM, never later than the facts' now.
- Lead with what a person can now do or what is still broken for them. Leave build and runtime mechanisms out (credentials, tokens, timers, polling, query plans, packages, crates, build checks, machines, hosts, ports, description footers, stacked changes) unless the decision is about that mechanism; say the behavior instead.
- A problem says whether it is still open and who is on it, or when and how it was settled.
- Never "shared fixes", "the fixes", "its dependencies" or "other changes" alone: name each one by what it does and who lands it.
- A newer record that takes a change out of the merge queue, holds it, withdraws its go or removes its approval row overrides every older record saying it is queued, approved or landing; say why it left the queue and the step that returns it, as the newest record names it, with who takes that step; never "not known" or "the coordinator decides" while a record names the step.
- Never guess. If a fact is not known, say "not known".
- Name each reviewer as what it is: codex, the reviewer bot, or Yasyf; never "the automated review" or "a second AI model".
- Never count findings, points, fixes or approvals ("one finding", "the third point", "the first fix"); say what each one is, who raised it, and who is answering it. Leave out anything you cannot name.
- coordinator_rulings are the coordinator's decisions; where they name a blocker's fixers or a change's state, they override every other record. Each entry of open_changes carries the coordinator's last rulings about it, its owner, and its fixers; a node about a change with fixers is fixing and its how names them by their work.
- Every change's state comes from open_changes alone (landed changes are absent from it); never call a change queued, merged or failing unless open_changes says so, and never let the headline, the chart and a work row disagree about one.
- A change that fails only because another change or the shared code is broken is being fixed by that other change's worker: name them, never nobody.
- One fact, one wording: a node and the work row for the same work must agree (coverage, state, next step).
- A blocker and a work row state the newest fact about their change, from its newest_records and coordinator_rulings, never an older state left in its next or blocker; since is when that newest state began.
- Nothing waits on the owner unless waiting_on_owner lists it: no node is you and no work row is Ready for you otherwise. A Ready for you row lists the ask of each entry it waits on in asks.
- A node's text and how are fields, not sentences: one thing each, with no semicolon and no second clause. Once waiting_on_owner no longer lists a question, no string says he answers it; say what happens with his pick and who does it.
- status_paragraphs sit above your page and the owner reads them first: call each change by the same name they use, and never contradict them unless open_changes or a coordinator ruling is newer. When two changes share a subject, such as two cloud viewer changes, name each by what sets it apart.
- Say new app and current app, never new tree or old tree; name a test by what it proves.
- Name every thing you mention by what it does for users. Never write an unnamed reference such as "the second part", "two changes", "other work" or "their worker"; say which part, which changes, which work.
- Keep every string from previous_brief whose facts did not change and that follows these rules, word for word; rewrite any that breaks a rule. A string about a change has changed facts when a coordinator ruling or one of its newest_records says something the string does not, such as a new reason it is red, a restack done, or a new step it waits on; rewrite it from the newest of them.
- pull_request_stages, when the facts carry it, is the one current account of each open pull request: anything you write about one of those changes, as a blocker, a work row, a part or a change's state, says what its stage and next say."""

PROMPT = """You write the main page of a live dashboard that a busy engineering lead reads at a glance. It answers four questions: overall progress, what is being worked on, the biggest blockers, and how each blocker is being unblocked. The user message is the drive's facts as JSON. Return one JSON object matching the schema.

""" + RULES + """

Fields:
- headline: one sentence, at most 25 words, written from the Main work entry of status_paragraphs, that a person could read aloud at standup: where we are, what is next, and the one thing in the way with how long it has waited, such as "for 51 minutes, since 6:58 PM". Never a count of approvals, taps or questions waiting on Yasyf: the needs band beside it gives the live total, and a second number reads as a contradiction.
- because: up to three short sentences naming the facts the headline rests on.
- band: kind "incident" only for a customer-facing incident from the last three hours that has no fix live yet, with one sentence of what is broken and the fix; else kind "none" and empty text.
- progress.stops: exactly one entry per milestone, in order. milestone_state is computed from the records and wins: copy each stop's state and label from it, and give every broken milestone a path built from its blockers, their owners and next steps. state is live, progress (being built or tested now), next (the next one to ship), paused, broken (a problem is stopping it), or not (not started). label is the one word milestone_state gives, such as "Live", "Blocked", "Building", "Releasing", "Later". why is at most three words for paused or broken, else empty.
- progress.sentence: at most 25 words: what ships before the next milestone. Never repeat the headline.
- paths: the dependency chart of blockers. One path for every milestone whose state is paused or broken, and one for the next milestone when something blocks it, the next milestone first. A milestone reads paused or broken only when its path holds at least one open blocker. nodes are the things that must happen before that milestone, each one stuck, being fixed, waiting on the owner, or done; edges go from a node to the node that waits on it ("from" must finish before "to"). A node with no outgoing edge leads straight to the milestone. Use at most three nodes per path: the biggest things in the way, merged where they share a fix. state: fixing (a worker reported on it in the last 20 minutes), you (the owner's tap or answer), nobody (no worker owns it, or its worker has been silent an hour; a blocker whose reporting worker states the fix it is making is that worker's, fixing), waiting (tests, the merge process, a reviewer), done. how always names who acts next and the specific fix they make, never a goal like "make it compile" (write "the cloud runs worker moves the 22 files still using the deleted session store"): a worker named by its work ("the cloud runs worker"), you, the coordinator, the merge process, or the automated review. Never write "not known" in how; when the next step is undecided, say who decides it. When a review finding or a failing test holds a change, how says what the finding or failure is and the change that answers it, such as "the agent machines worker makes a failed restore retry", never "answers the review" or "fixes its failing tests". For nobody, say "The coordinator assigns a worker" plus when it was raised. A decision the owner already made is done: drop it, and never ask him again for something he approved. item names the work item fixing it. While band is an incident, the first path is the incident's: what is broken for users, each fix and who is on it; release paths follow it.
- calm: when nothing blocks the next milestone, one sentence saying so and what ships next; else empty.
- work: up to eight pieces of work being done now, most important first (fixing a blocker, then the next milestone, then the rest). Never a coordinator (merge process, board upkeep, review graders, monitors). title at most 8 words, named for what it makes for users, never who makes it. goal at most 20 words: what this work changes for users once it lands, such as "A release no longer breaks customer runs; affected runs retry once and carry on." latest at most 16 words: the newest real progress. workers lists the worker names doing it (facts only, never shown), the one whose own change it is first; a worker that also does other work comes after it. changes lists the pull request numbers this work makes (facts only, never shown). item is a stable id; reuse the previous brief's id for the same work. since is the Pacific clock time its current status began.
- since, on every node not done and every work row: the time of the record that started the wait, such as the push its tests run on, the hold, the review request, or the card going up. The page shows how long that has been."""


def at(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))


def clock(when):
    return when.astimezone(PACIFIC).strftime("%-I:%M %p")


def base_lane(lane):
    return SUCCESSOR.sub("", lane)


def clip(text, limit=320):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " [cut]"


def slot_of(path):
    if path[:1] == ("progress",) and path[1:2] == ("stops",):
        return "line" if path[-1] in ("label", "why") else None
    if path[:1] == ("paths",):
        return {"text": "node", "how": "how"}.get(path[-1])
    if path[:1] == ("work",):
        return {"title": "title", "goal": "line", "latest": "latest"}.get(path[-1])
    if path[:1] == ("because",):
        return "sentence"
    return SLOTS.get(tuple(path))


def said(record, limit):
    return f"{clock(at(record['at']))} {record['kind']} from {record['lane']}: {clip(record['text'], limit)}"


def recent(records, now):
    closed = {r["resolves"] for r in records if r.get("resolves")}
    return [r for r in records if r["kind"] in TROUBLE_KINDS and r.get("seq") not in closed and now - at(r["at"]) <= TROUBLE_WINDOW and r["lane"] != "desk-runner" and not r["text"].startswith("Register classify")]


def trouble(records, now):
    return [said(r, 260) for r in recent(records, now) if r["lane"] not in ROOT][-30:]


def rulings(records, now):
    return [f"{clock(at(r['at']))} {r['kind']}: {clip(r['text'], 700)}" for r in recent(records, now) if r["lane"] in ROOT][-15:]


def named_changes(model):
    states = {str(e["number"]): e for e in model["drive"]["prs"]}
    numbers = {n for release in model["now"].get("releases", []) for n in PR_MENTION.findall(f"{release.get('state')} {release.get('blocker')}")}
    return [{"number": n, "title": model["prs"].get(n, {}).get("title"), "state": states.get(n, {}).get("state"), "next": states.get(n, {}).get("next")} for n in sorted(numbers)]


def change_records(model, now):
    unique = {(r["at"], r["lane"], r["text"]): r for r in [*model["fetch"](CHANGE_KINDS, now - CHANGE_WINDOW), *model["records"]] if not r["text"].startswith("Register classify")}
    return sorted(unique.values(), key=lambda r: at(r["at"]))


def mentioned(records):
    rulings, mentions = {}, {}
    for r in records:
        refs = {re.search(r"(\d+)$", str(ref))[1] for ref in (r.get("refs") or {}).get("prs") or []}
        for n in set(PR_MENTION.findall(r["text"])) | refs:
            if r["lane"] in ROOT or not r["lane"].startswith(BYSTANDERS):
                (rulings if r["lane"] in ROOT else mentions).setdefault(int(n), []).append(r)
    return rulings, mentions


def pr_stages(model):
    return [{k: pr[k] for k in ("number", "stage", "next")} for pr in (model["pages"].get("work") or {}).get("pull_requests", [])]


def open_changes(model, records):
    active = {w["lane"] for w in model["workers"]}
    rulings, mentions = mentioned(records)
    changes = []
    for e in model["drive"]["prs"]:
        if e["state"] == "landed":
            continue
        owner = e.get("owner") or (e.get("worker") or {}).get("lane")
        said = rulings.get(e["number"], [])[-3:]
        named = next((found for r in reversed(said) if (found := sorted(lane for lane in active if re.search(rf"(?<![\w-]){re.escape(lane)}(?![\w-])", r["text"])))), [])
        changes.append({"number": e["number"], "title": clip(model["prs"].get(str(e["number"]), {}).get("title", ""), 120), "state": e["state"], "owner": owner, "next": "" if CHECK_PLACEHOLDER.search(e.get("next") or "") else clip(e.get("next") or "", 160), "blocker": clip(e.get("blocker") or "", 160), "base": e.get("base"), "base_failing": e.get("base_failing"),
                        "coordinator_rulings": [f"{clock(at(r['at']))}: {clip(r['text'], 300)}" for r in said],
                        "newest_records": [f"{clock(at(r['at']))} {r['kind']} from {r['lane']}: {clip(r['text'], 220)}" for r in mentions.get(e["number"], [])[-4:]], "fixers": named or sorted(lane for lane in active if owner and base_lane(lane) == base_lane(owner))})
    return changes


def undated(brief):
    nodes = [f"paths.{i}.nodes.{j}.since" for i, path in enumerate(brief["paths"]) for j, node in enumerate(path["nodes"]) if node["state"] != "done" and not CLOCK.fullmatch(node.get("since", ""))]
    rows = [f"work.{i}.since" for i, row in enumerate(brief["work"]) if not CLOCK.fullmatch(row.get("since", ""))]
    return [f"{path}: empty; give the Pacific clock time of the record that started this wait" for path in nodes + rows]


def unwaited(brief):
    stuck = any(node["state"] != "done" for path in brief["paths"] for node in path["nodes"])
    return ["headline: say the one thing in the way and how long it has waited, such as 'for 51 minutes, since 6:58 PM'"] if stuck and not WAITED.search(brief["headline"]) else []


def stuck_alone(brief):
    held = {path["milestone"] for path in brief["paths"] if any(node["state"] != "done" for node in path["nodes"])}
    return [i for i, stop in enumerate(brief["progress"]["stops"]) if stop["state"] in ("paused", "broken") and i not in held]


def owner_asks(needs):
    cards = [{"ask": n["card"]["id"], "what": n["card"]["title"]} for n in needs if n["kind"] == "card"]
    prs = [{"ask": str(pr["number"]), "what": pr["title"]} for n in needs if n["kind"] == "prs" for pr in n["prs"]]
    return (cards + prs)[:12]


def unasked(row, needs):
    return row["status"] == "Ready for you" and not {ask.lstrip("#") for ask in row["asks"]} & {n["ask"] for n in needs}


def phantoms(brief, needs):
    nodes = [] if needs else [f"paths.{i}.nodes.{j}.state" for i, path in enumerate(brief["paths"]) for j, node in enumerate(path["nodes"]) if node["state"] == "you"]
    rows = [f"work.{i}.status" for i, row in enumerate(brief["work"]) if unasked(row, needs)]
    return [f"{path}: no entry of waiting_on_owner it names in asks is open, so it may not wait on him" for path in nodes + rows]


def without_phantoms(brief, needs):
    brief["work"] = [row for row in brief["work"] if not unasked(row, needs)]
    if needs:
        return brief
    for path in brief["paths"]:
        gone = {node["id"] for node in path["nodes"] if node["state"] == "you"}
        path["nodes"] = [node for node in path["nodes"] if node["id"] not in gone]
        path["edges"] = [edge for edge in path["edges"] if edge["from"] not in gone and edge["to"] not in gone]
    return brief


def unassigned(brief, changes):
    fixers = {c["number"]: c["fixers"] for c in changes if c["fixers"]}
    return [(i, j, node, fixers[node["change"]]) for i, path in enumerate(brief["paths"]) for j, node in enumerate(path["nodes"]) if node["state"] == "nobody" and node.get("change") in fixers]


def digest(model, config, previous, now):
    records = model["records"]
    lanes = {r["lane"] for r in records}
    workers = []
    for worker in model["workers"][:14]:
        mine = [r for r in records if r["lane"] == worker["lane"]][-3:]
        workers.append({"worker": worker["lane"], "last_report": clock(at(worker["at"])), "pull_requests": worker["prs"][:6], "records": [f"{clock(at(r['at']))} {r['kind']}: {clip(r['text'], 280)}" for r in mine]})
    states = {}
    for entry in model["drive"]["prs"]:
        states[entry["state"]] = states.get(entry["state"], 0) + 1
    return {
        "now": clock(now),
        "milestones": [{"name": m["name"], **next((r for r in model["now"].get("releases", []) if r.get("id") == m.get("release")), {})} for m in config["milestones"]],
        "milestone_state": model["milestone_state"],
        "plan_steps": [{k: s.get(k) for k in ("label", "title", "state", "tone", "blocker", "next", "delivers")} | {"pull_requests": [p["number"] if isinstance(p, dict) else p for p in s.get("prs", [])][:8]} for s in model["steps"]],
        "code": {"tracked_changes": len(model["drive"]["prs"]), "by_state": states},
        "changes_named_by_milestones": named_changes(model),
        "open_changes": open_changes(model, change_records(model, now)),
        "pull_request_stages": pr_stages(model),
        "waiting_on_owner": owner_asks(model["needs"]),
        "workers": workers,
        "recent_trouble": trouble(records, now),
        "coordinator_rulings": rulings(records, now),
        "review_findings": [f"{clock(at(r['at']))} from {r['lane']}: {clip(r['text'], 300)}" for r in records if r["kind"] == "review" and now - at(r["at"]) <= WORDS_WINDOW and FINDING.search(r["text"])][-10:],
        "owner_words": [f"{clock(at(w['at']))}: {clip(w['title'] + ' ' + w['text'], 300)}" for w in model["words"] if now - at(w["at"]) <= WORDS_WINDOW][-12:],
        "drive_summary": {k: model["story"].get(k) for k in ("now", "users_now")},
        "status_paragraphs": [f"{p['label']}: {p['text']}" for p in (model.get("status_now") or {}).get("paragraphs", [])],
        "previous_brief": previous and {k: previous[k] for k in SCHEMA["required"]},
        "worker_names": sorted(lanes),
    }



def string(description=None):
    return {"type": "string", **({"description": description} if description else {})}


def strict(properties, **extra):
    return {"type": "object", "additionalProperties": False, "required": list(properties), "properties": properties, **extra}


def listing(item, **extra):
    return {"type": "array", "items": item, **extra}


def now_checks(brief, facts):
    return undated(brief) + unwaited(brief) + phantoms(brief, facts["waiting_on_owner"]) + [f"progress.stops.{i}.state: it reads {brief['progress']['stops'][i]['label']!r} but no path holds an open blocker for milestone {i}; add the path with the blocker that stops it and how naming who unblocks it, their next step and when, from the records, or give the milestone its real state from the records" for i in stuck_alone(brief)] + [f"paths.{i}.nodes.{j}.state: change {node['change']} is being fixed by {', '.join(names)}; its state is fixing and how names them by their work" for i, j, node, names in unassigned(brief, facts["open_changes"])]


def now_settle(brief, facts):
    without_phantoms(brief, facts["waiting_on_owner"])
    for i in stuck_alone(brief):
        brief["progress"]["stops"][i].update(state="progress", label="Being worked on", why="")
    for _, _, node, _ in unassigned(brief, facts["open_changes"]):
        node["state"] = "fixing"
    for stop, computed in zip(brief["progress"]["stops"], facts["milestone_state"]):
        stop.update(state=computed["state"], label=computed["label"], why="Blocked" if computed["state"] == "broken" else "")


def unchecked(brief, facts):
    return []


def unsettled(brief, facts):
    pass


class Page(NamedTuple):
    name: str
    prompt: str
    schema: dict
    digest: Callable
    slot_of: Callable
    kinds: frozenset
    gap: int
    checks: Callable = unchecked
    settle: Callable = unsettled


NOW = Page("brief", PROMPT, SCHEMA, digest, slot_of, frozenset(CURATOR_KINDS), MIN_GAP, now_checks, now_settle)


def twin(old, value, index):
    if not isinstance(old, list):
        return None
    key = next((k for k in IDENTITY if isinstance(value, dict) and k in value), None)
    if key is None:
        return old[index] if index < len(old) else None
    return next((o for o in old if isinstance(o, dict) and o.get(key) == value[key]), None)


def run_model(model_name, prompt, schema, system):
    out = subprocess.run(["claude", "-p", *QUIET, "--model", model_name, "--effort", "medium", "--output-format", "json", "--json-schema", json.dumps(schema), "--system-prompt", system], input=prompt, capture_output=True, text=True, timeout=600, check=True)
    result = json.loads(out.stdout)
    return result.get("structured_output") or json.loads(result["result"])


class Curator:
    def __init__(self, config, live_model, on_brief, page=NOW):
        self.page = page
        self.config = config
        self.live_model = live_model
        self.on_brief = on_brief
        self.brief = None
        self.dirty = threading.Event()
        self.error = None
        self.running = False

    def poke(self, kind=None):
        if kind is None or kind in self.page.kinds:
            self.dirty.set()

    def loop(self):
        self.dir = Path(self.config["brief_dir"])
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{self.page.name}.json"
        self.brief = json.loads(self.path.read_text()) if self.path.exists() else None
        self.dirty.set()
        while True:
            self.dirty.wait()
            self.dirty.clear()
            started = time.monotonic()
            self.running = True
            try:
                self.curate()
                self.error = None
            except Exception as error:
                self.error = f"{type(error).__name__}: {str(error)[:300]}"
            finally:
                self.running = False
            time.sleep(max(0.0, self.page.gap - (time.monotonic() - started)))

    def curate(self):
        model = self.live_model()
        now = dt.datetime.now(dt.timezone.utc)
        facts = self.page.digest(model, self.config, self.brief, now)
        if facts is None:
            return
        lanes = tuple(facts["worker_names"])
        prompt = json.dumps(facts, indent=1)

        def write(text):
            return run_model(self.config.get("curator_model", "claude-opus-5-5"), text, self.page.schema, self.page.prompt)

        brief = write(prompt)
        problems = plainwords.check(brief, self.page.slot_of, lanes) + self.page.checks(brief, facts)
        if problems:
            brief = write(prompt + "\n\nYOUR LAST ANSWER BROKE THESE RULES; return the whole object again with each fixed:\n" + "\n".join(problems) + "\n\nLAST ANSWER:\n" + json.dumps(brief))
            problems = plainwords.check(brief, self.page.slot_of, lanes)
        brief = self.keep_passing(brief, problems, lanes)
        self.page.settle(brief, facts)
        brief["at"] = now.isoformat()
        brief["violations"] = problems
        self.write(brief)

    def keep_passing(self, brief, problems, lanes):
        if not problems:
            return brief
        failing = {p.split(":", 1)[0] for p in problems}

        def fix(value, old, path):
            if isinstance(value, dict):
                return {k: fix(v, old.get(k) if isinstance(old, dict) else None, (*path, k)) for k, v in value.items()}
            if isinstance(value, list):
                return [fix(v, twin(old, v, i), (*path, i)) for i, v in enumerate(value)]
            if ".".join(map(str, path)) not in failing:
                return value
            return old if isinstance(old, str) and not plainwords.violations(old, self.page.slot_of(path) or "line", tuple(lanes)) else ""

        return fix(brief, self.brief, ())

    def write(self, brief):
        history = self.dir / "history" / self.page.name
        history.mkdir(parents=True, exist_ok=True)
        stamp = brief["at"].replace(":", "")
        (history / f"{stamp}.json").write_text(json.dumps(brief, indent=1))
        for old in sorted(history.glob("*.json"))[:-HISTORY]:
            old.unlink()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(brief, indent=1))
        tmp.replace(self.path)
        self.brief = brief
        self.on_brief()
