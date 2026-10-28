from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
import threading
import time
from pathlib import Path
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
PR_MENTION = re.compile(r"#(\d{4,6})\b")
FINDING = re.compile(r"finding|defect|blocker|must|fails?\b", re.I)
QUIET = ("--setting-sources", "", "--strict-mcp-config", "--disable-slash-commands", "--tools", "", "--no-session-persistence")
PACIFIC = ZoneInfo("America/Los_Angeles")
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
        "label": {"type": "string", "description": "two to four words, such as Live, Next, Being tested, Stuck in testing, Not started"},
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
        "how": {"type": "string", "description": "who acts next and what they do, at most 12 words, such as 'The cloud runs worker rebuilds the fix, then the review rechecks'"},
        "item": {"type": "string", "description": "the work item id fixing it, or empty"},
        "since": {"type": "string", "description": "Pacific clock time it got stuck, such as 11:45 AM, or empty"},
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
                "required": ["item", "workers", "title", "status", "latest", "unblocks"],
                "properties": {
                    "item": {"type": "string", "description": "stable short id, lowercase words joined by dashes, kept across briefs"},
                    "workers": {"type": "array", "items": {"type": "string"}},
                    "title": {"type": "string"},
                    "status": {"enum": ["Under review", "Ready for you", "Ready to merge", "Testing", "Building", "Rolling out", "Stuck", "Waiting on tests", "Fixing", "Investigating"]},
                    "latest": {"type": "string"},
                    "unblocks": {"type": "string", "description": "id of the blocker node this work fixes, or empty"},
                },
            },
        },
    },
}

PROMPT = """You write the main page of a live dashboard that a busy engineering lead reads at a glance. It answers four questions: overall progress, what is being worked on, the biggest blockers, and how each blocker is being unblocked. The user message is the drive's facts as JSON. Return one JSON object matching the schema.

Rules for every string:
- Written for a human who never reads the agents' records. Plain English, short sentences, the product names users know (Sand, Iris, SandSQL, S3, codex) where they are the fact.
- Never a record number, pull request number, release code (R1, P8), step number, hash, file path, branch, URL, worker name from worker_names, or these coined words: lane, desk, watchdog, finder, slice, census, fidelity, register, mirror, forest, tap row, ci-grade, rider, carry, restack, rebase, enqueue, old world, new world, cherry-pick. Say "a worker", "the merge process", "the automated review", "old code", "new code" instead. Describe what changed for users or what is left, never git mechanics.
- Times are Pacific clock times like 12:40 PM, never later than the facts' now.
- Never guess. If a fact is not known, say "not known".
- Never count findings, points, fixes or approvals ("one finding", "the third point", "the first fix"); say what each one is, who raised it, and who is answering it. Leave out anything you cannot name.
- coordinator_rulings are the coordinator's decisions; where they name a blocker's fixers or a change's state, they override every other record. Each entry of open_changes carries the latest ruling about it and its fixers; a node about a change with fixers is fixing and its how names them by their work.
- Every change's state comes from open_changes alone (landed changes are absent from it); never call a change queued, merged or failing unless open_changes says so, and never let the headline, the chart and a work row disagree about one.
- A change that fails only because another change or the shared code is broken is being fixed by that other change's worker: name them, never nobody.
- One fact, one wording: a node and the work row for the same work must agree (coverage, state, next step).
- Say new app and current app, never new tree or old tree; name a test by what it proves.
- Name every thing you mention by what it does for users. Never write an unnamed reference such as "the second part", "two changes", "other work" or "their worker"; say which part, which changes, which work.
- Keep every string from previous_brief whose facts did not change and that follows these rules, word for word; rewrite any that breaks a rule.

Fields:
- headline: one sentence, at most 25 words, that a person could read aloud at standup: where we are, what is next, what is in the way.
- because: up to three short sentences naming the facts the headline rests on.
- band: kind "incident" only for a customer-facing incident from the last three hours that has no fix live yet, with one sentence of what is broken and the fix; else kind "none" and empty text.
- progress.stops: exactly one entry per milestone, in order. state is live, progress (being built or tested now), next (the next one to ship), paused, broken (a problem is stopping it), or not (not started). label is two to four words such as "Live", "Next", "Being tested", "Stuck in testing", "Not started". why is at most three words for paused or broken, else empty.
- progress.sentence: at most 25 words: what ships before the next milestone. Never repeat the headline.
- paths: the dependency chart of blockers. One path per milestone that something blocks (at most two, the next milestone first). nodes are the things that must happen before that milestone, each one stuck, being fixed, waiting on the owner, or done; edges go from a node to the node that waits on it ("from" must finish before "to"). A node with no outgoing edge leads straight to the milestone. Use at most three nodes per path: the biggest things in the way, merged where they share a fix. state: fixing (a worker reported on it in the last 20 minutes), you (the owner's tap or answer), nobody (no worker owns it, or its worker has been silent an hour), waiting (tests, the merge process, a reviewer), done. how always names who acts next and the specific fix they make, never a goal like "make it compile" (write "the cloud runs worker moves the 22 files still using the deleted session store"): a worker named by its work ("the cloud runs worker"), you, the coordinator, the merge process, or the automated review. Never write "not known" in how; when the next step is undecided, say who decides it. For nobody, say "The coordinator assigns a worker" plus when it was raised. A decision the owner already made is done: drop it, and never ask him again for something he approved. item names the work item fixing it.
- calm: when nothing blocks the next milestone, one sentence saying so and what ships next; else empty.
- work: up to eight pieces of work being done now, most important first (fixing a blocker, then the next milestone, then the rest). Never a coordinator (merge process, board upkeep, review graders, monitors). title at most 8 words, named for what it makes for users, never who makes it. latest at most 16 words: the newest real progress. workers lists the worker names doing it (facts only, never shown). item is a stable id; reuse the previous brief's id for the same work."""


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
        return {"title": "title", "latest": "latest"}.get(path[-1])
    if path[:1] == ("because",):
        return "sentence"
    return SLOTS.get(tuple(path))


def named_changes(model):
    states = {str(e["number"]): e for e in model["drive"]["prs"]}
    numbers = {n for release in model["now"].get("releases", []) for n in PR_MENTION.findall(f"{release.get('state')} {release.get('blocker')}")}
    return [{"number": n, "title": model["prs"].get(n, {}).get("title"), "state": states.get(n, {}).get("state"), "next": states.get(n, {}).get("next")} for n in sorted(numbers)]


def open_changes(model):
    lanes = {r["lane"] for r in model["records"]} | {w["lane"] for w in model["workers"]}
    active = {w["lane"] for w in model["workers"]}
    rulings = {}
    for r in model["records"]:
        if r["lane"] in ROOT:
            for n in PR_MENTION.findall(r["text"]):
                rulings[int(n)] = r
    changes = []
    for e in model["drive"]["prs"]:
        if e["state"] == "landed":
            continue
        worker = e.get("worker") or e.get("owner")
        ruling = rulings.get(e["number"])
        named = sorted(lane for lane in lanes - ROOT if ruling and re.search(rf"(?<![\w-]){re.escape(lane)}(?![\w-])", ruling["text"]))
        changes.append({"number": e["number"], "title": clip(model["prs"].get(str(e["number"]), {}).get("title", ""), 120), "state": e["state"], "worker": worker, "next": clip(e.get("next") or "", 160), "blocker": clip(e.get("blocker") or "", 160), "base": e.get("base"), "base_failing": e.get("base_failing"),
                        "coordinator_ruling": ruling and f"{clock(at(ruling['at']))}: {clip(ruling['text'], 500)}", "fixers": named or ([worker] if worker in active else [])})
    return changes


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
    recent = [r for r in records if r["kind"] in TROUBLE_KINDS and now - at(r["at"]) <= TROUBLE_WINDOW and r["lane"] != "desk-runner" and not r["text"].startswith("Register classify")]
    trouble = [f"{clock(at(r['at']))} {r['kind']} from {r['lane']}: {clip(r['text'], 260)}" for r in recent if r["lane"] not in ROOT][-30:]
    rulings = [f"{clock(at(r['at']))} {r['kind']}: {clip(r['text'], 700)}" for r in recent if r["lane"] in ROOT][-15:]
    states = {}
    for entry in model["drive"]["prs"]:
        states[entry["state"]] = states.get(entry["state"], 0) + 1
    return {
        "now": clock(now),
        "milestones": [{"name": m["name"], **next((r for r in model["now"].get("releases", []) if r.get("id") == m.get("release")), {})} for m in config["milestones"]],
        "plan_steps": [{k: s.get(k) for k in ("label", "title", "state", "tone", "blocker", "next", "delivers")} | {"pull_requests": [p["number"] if isinstance(p, dict) else p for p in s.get("prs", [])][:8]} for s in model["steps"]],
        "code": {"tracked_changes": len(model["drive"]["prs"]), "by_state": states},
        "changes_named_by_milestones": named_changes(model),
        "open_changes": open_changes(model),
        "waiting_on_owner": [{k: n.get(k) for k in ("kind", "title", "question", "number", "change", "risk")} for n in model.get("needs", [])][:8],
        "workers": workers,
        "recent_trouble": trouble,
        "coordinator_rulings": rulings,
        "review_findings": [f"{clock(at(r['at']))} from {r['lane']}: {clip(r['text'], 300)}" for r in records if r["kind"] == "review" and now - at(r["at"]) <= WORDS_WINDOW and FINDING.search(r["text"])][-10:],
        "owner_words": [f"{clock(at(w['at']))}: {clip(w['title'] + ' ' + w['text'], 300)}" for w in model["words"] if now - at(w["at"]) <= WORDS_WINDOW][-12:],
        "drive_summary": {k: model["story"].get(k) for k in ("now", "users_now")},
        "previous_brief": previous and {k: previous[k] for k in SCHEMA["required"]},
        "worker_names": sorted(lanes),
    }



def run_model(model_name, prompt, schema):
    out = subprocess.run(["claude", "-p", *QUIET, "--model", model_name, "--effort", "medium", "--output-format", "json", "--json-schema", json.dumps(schema), "--system-prompt", PROMPT], input=prompt, capture_output=True, text=True, timeout=600, check=True)
    result = json.loads(out.stdout)
    return result.get("structured_output") or json.loads(result["result"])


def gate(brief, lanes):
    return plainwords.check(brief, slot_of, tuple(lanes))


class Curator:
    def __init__(self, config, live_model, on_brief):
        self.config = config
        self.live_model = live_model
        self.on_brief = on_brief
        self.brief = None
        self.dirty = threading.Event()
        self.error = None
        self.running = False

    def poke(self, kind=None):
        if kind is None or kind in CURATOR_KINDS:
            self.dirty.set()

    def loop(self):
        self.dir = Path(self.config["brief_dir"])
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "brief.json"
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
            time.sleep(max(0.0, MIN_GAP - (time.monotonic() - started)))

    def curate(self):
        model = self.live_model()
        now = dt.datetime.now(dt.timezone.utc)
        facts = digest(model, self.config, self.brief, now)
        lanes = facts["worker_names"]
        prompt = json.dumps(facts, indent=1)
        model_name = self.config.get("curator_model", "claude-opus-5-5")
        brief = run_model(model_name, prompt, SCHEMA)
        problems = gate(brief, lanes) + [f"paths.{i}.nodes.{j}.state: change {node['change']} is being fixed by {', '.join(names)}; its state is fixing and how names them by their work" for i, j, node, names in unassigned(brief, facts["open_changes"])]
        if problems:
            brief = run_model(model_name, prompt + "\n\nYOUR LAST ANSWER BROKE THESE RULES; return the whole object again with each fixed:\n" + "\n".join(problems) + "\n\nLAST ANSWER:\n" + json.dumps(brief), SCHEMA)
            problems = gate(brief, lanes)
        brief = self.keep_passing(brief, problems, lanes)
        for _, _, node, _ in unassigned(brief, facts["open_changes"]):
            node["state"] = "fixing"
        brief["at"] = now.isoformat()
        brief["violations"] = problems
        self.write(brief)

    def keep_passing(self, brief, problems, lanes):
        if not problems:
            return brief
        failing = {p.split(":", 1)[0] for p in problems}
        stale = self.brief or {}

        def fix(value, path):
            if isinstance(value, dict):
                return {k: fix(v, (*path, k)) for k, v in value.items()}
            if isinstance(value, list):
                return [fix(v, (*path, i)) for i, v in enumerate(value)]
            if ".".join(map(str, path)) not in failing:
                return value
            old = stale
            for key in path:
                try:
                    old = old[key]
                except (KeyError, IndexError, TypeError):
                    return ""
            return old if isinstance(old, str) and not plainwords.violations(old, slot_of(path) or "line", tuple(lanes)) else ""

        return fix(brief, ())

    def write(self, brief):
        history = self.dir / "history"
        history.mkdir(exist_ok=True)
        stamp = brief["at"].replace(":", "")
        (history / f"{stamp}.json").write_text(json.dumps(brief, indent=1))
        for old in sorted(history.glob("*.json"))[:-HISTORY]:
            old.unlink()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(brief, indent=1))
        tmp.replace(self.path)
        self.brief = brief
        self.on_brief()
