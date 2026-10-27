import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
CCI = "http://127.0.0.1:7377/v1"
TIMELINE_KINDS = ["state", "head", "review", "owner", "go", "landed", "release", "incident", "mechanism", "fix-live", "opened", "report", "hold", "lift", "blocker", "defect", "correction", "decide", "ask", "answer"]
TIMELINE_WINDOW = dt.timedelta(hours=24)
LEDGER_KINDS = {"landed", "go", "opened", "head", "hold", "lift"}
LIVE_SOURCES = ["cc-present store: taps and questions", "cci bus: records and workers", "drive ledger in cc-notes: pull request states, re-read on every landing, go, head, opened, hold or lift record", "progress-desk steps.json: the step rows"]
REBUILD_AFTER = 2.0
SLOW_REFRESH = 300
PLAN_HEADING = "\n## Ordered plan of action"
PLAN_ITEM = re.compile(r"^\d+\. (.+)$")
GO = re.compile(r"^go #(\d+) at (\w+)")
PR_REF = re.compile(r"#(\d{4,6})\b")
PR_RANGE = re.compile(r"#(\d{4,6})-#(\d{4,6})")
REVIEW_ROUTE = re.compile(r"/api/review/(\d+)")
TAILNET_LINE = re.compile(r"^tailnet: (\S+)$", re.M)
AI_CONFIG = {"endpoint": "https://api.cerebras.ai/v1", "model": "gpt-oss-120b"}
ASSIST = HERE.parents[1] / "skills" / "long-running" / "templates" / "drive-assist.html"
PR_ROUTE = re.compile(r"/api/pr/(\d+)")
RECORD_ROUTE = re.compile(r"/record/(\d+)")
ROW_FACTS = ("number", "head", "title", "change", "plan", "risk", "review", "blocker", "block", "state")
PR_FACTS = "{body: .body, additions: .additions, deletions: .deletions, files: .changed_files, commits: .commits, head: .head.sha}"
STORE = Path.home() / ".cc-present" / "cc-interact-v1" / "state.db"
ASKS = {"choice": "choices", "approval": "decisions", "input": "inputs"}
ACTIVE = dt.timedelta(minutes=30)
WORKING = dt.timedelta(minutes=20)
BOX = dt.timedelta(minutes=5)
BOXES = 12
SEVERITY = {"blocker": "bad", "defect": "bad", "incident": "bad", "ask": "you", "owner": "you", "landed": "ok", "go": "ok", "fix-live": "ok"}
TONE_RANK = {"bad": 3, "you": 2, "ok": 1, "info": 0}
COORDINATORS = re.compile(r"^(root|main)$|(^|-)desk(-|$)|refresher|watchdog|register|landing|board|prompt|fidelity|progress|comms|tooling|finder|sweep|batch-post")
PR_MENTION = re.compile(r"#(4\d{4})\b")
STATIC = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/app.css": ("app.css", "text/css"), "/review.js": ("review.js", "text/javascript"), "/review.css": ("review.css", "text/css"), "/overview.js": ("overview.js", "text/javascript"), "/overview.css": ("overview.css", "text/css")}


def run(argv, cwd=None):
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=True, timeout=60).stdout


def cci(path, **query):
    url = f"{CCI}/{path}?" + "&".join(f"{k}={v}" for k, values in query.items() for v in (values if isinstance(values, list) else [values]))
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.load(response)


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


def activity(records, now):
    seen = {}
    for record in records:
        if COORDINATORS.search(record["lane"]) or now - at(record["at"]) > ACTIVE:
            continue
        topic = record.get("topic") or ""
        for number in {*([topic] if re.fullmatch(r"\d{5}", topic) else []), *PR_MENTION.findall(record["text"])}:
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


def part_of(title, parts):
    scope = (title or "").split(":", 1)[0].strip()
    return next((part["key"] for part in parts if scope in part["scopes"]), parts[-1]["key"])


def drive_prs(prs, rows, gos, records, trees, tree_names, steps, parts, now):
    by_number = {str(row["number"]): row for row in rows}
    active = activity(records, now)
    listed = []
    for number, pr in sorted(prs.items()):
        if pr.get("state") not in ("open", "landed", None):
            continue
        found = trees.get(number, [])
        listed.append({
            "number": int(number),
            "state": pr_state(pr, by_number.get(number), gos.get(int(number)), active.get(number), now),
            "tree": next((name for name in tree_names if name in found), tree_names[-1]),
            "part": part_of(pr.get("title"), parts),
            "step": next((step["id"] for step in steps if int(number) in step["prs"]), None),
            "worker": active.get(number),
        })
    return {"source": "every pull request in the drive's ledger that is open or landed, since the drive began on Oct 22", "prs": listed}


def drive_workers(records, steps, now):
    live = {}
    for record in records:
        age = now - at(record["at"])
        if COORDINATORS.search(record["lane"]) or age >= BOX * BOXES:
            continue
        worker = live.setdefault(record["lane"], {"lane": record["lane"], "at": record["at"], "prs": set(), "boxes": ["" for _ in range(BOXES)]})
        worker["at"] = max(worker["at"], record["at"])
        if age <= WORKING:
            topic = record.get("topic") or ""
            worker["prs"].update(int(n) for n in {*([topic] if re.fullmatch(r"\d{5}", topic) else []), *PR_MENTION.findall(record["text"])})
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


def step_tone(step, prs, workers):
    states = {entry["state"] for entry in prs}
    if prs and states == {"landed"}:
        return "done"
    for tone in ("you", "failing", "held"):
        if tone in states:
            return tone
    if "doing" in states or workers:
        return "moving"
    if prs and states <= {"landed", "queued"}:
        return "queued"
    return "waiting" if prs else step["tone"]


def step_rows(steps, drive, workers):
    by_number = {entry["number"]: entry for entry in drive["prs"]}
    rows = []
    for step in steps:
        prs = [by_number[n] for n in step["prs"] if n in by_number]
        on = [{"lane": w["lane"], "at": w["at"], "prs": w["prs"]} for w in workers if w["live"] and w["step"] == step["id"]]
        rows.append({**step, "prs": [{"number": entry["number"], "state": entry["state"]} for entry in prs], "tone": step_tone(step, prs, on), "workers": on})
    return rows


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
    return {"kind": "ask", "ask": ask, "why": f"{record['lane']} asked in #{record['seq']}, due now: {ask['why']}", "source": {"label": f"Record #{record['seq']} from {record['lane']}", "href": f"/record/{record['seq']}"}}


def needs(rows, cards, asks, board_url):
    return [*merge_needs(rows, board_url), *(card_need(card, board_url) for card in cards), *(ask_need(ask) for ask in asks if not ask.get("optional"))]


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
    def __init__(self, config, reviews):
        self.config = config
        self.reviews = reviews
        self.lock = threading.Lock()
        self.changed = threading.Condition(self.lock)
        self.records = deque(maxlen=4000)
        self.version = 0
        self.model = {}
        self.slow = {}
        self.slow_at = 0.0
        self.dirty_at = time.monotonic()
        self.errors = {}
        self.paths = {}
        self.reported = set()
        self.pr_facts = {}
        self.board_seq = None
        self.gos = {}
        self.board_raw = None

    def review_url(self, number):
        cwd = self.reviews / str(number)
        cwd.mkdir(parents=True, exist_ok=True)
        url = TAILNET_LINE.search(run(["cc-review", "start", "--pr", f"{self.config['repo']}#{number}", "--cwd", str(cwd)]))[1]
        if self.model["prs"].get(str(number), {}).get("state") == "landed":
            run(["cc-review", "close", url.rsplit("/s/", 1)[1]])
        return url

    def decide(self, block, number, verdict, head, note):
        entry = {"verdict": verdict} if verdict == "cleared" else {"verdict": verdict, "head": head, **({"note": note} if note else {})}
        return self.interact({"type": "triage.decided", "blockId": block, "verdicts": {str(number): entry}})

    def answer(self, body):
        if body["kind"] == "choice":
            return self.interact({"type": "choice.selected", "blockId": body["block"], "optionIds": body.get("options", []), **({"other": body["other"]} if body.get("other") else {})})
        return self.interact({"type": "feedback.created", "id": str(uuid.uuid4()), "blockId": body["block"], "text": body["text"]})

    def asked(self, text):
        lane, kind = ("dashboard-ask-test", "note") if text.startswith("[user-test") else ("dashboard", "owner")
        run(["cci", "post", "--drive", self.config["drive"], "--lane", lane, "--kind", kind, "--to", "root", "--text", f"Asked on the dashboard: {text}"])
        return 200, ""

    def interact(self, interaction):
        origin, _, subject = self.config["board_url"].partition("/p/")
        request = urllib.request.Request(f"{origin}/api/interactions", method="POST", headers={"Content-Type": "application/json"}, data=json.dumps({"subject": subject, "nonce": str(uuid.uuid4()), "interaction": interaction}).encode())
        try:
            with urllib.request.urlopen(request, timeout=15):
                pass
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode().strip()
        self.touch(slow=True)
        return 200, ""

    def ccn(self, *args):
        return run(["ccn", *args], cwd=self.config["checkout"])

    def load_records(self):
        since = (dt.datetime.now(dt.timezone.utc) - TIMELINE_WINDOW).strftime("%Y-%m-%dT%H:%M:%SZ")
        after = 0
        while True:
            query = {"drive": self.config["drive"], "kind": TIMELINE_KINDS, "limit": 500}
            query.update({"since": after} if after else {"since_time": since})
            page = cci("records", **query)
            self.records.extend(page)
            if len(page) < 500:
                return page[-1]["seq"] if page else 0
            after = page[-1]["seq"]

    def follow_records(self, after):
        while True:
            try:
                url = f"{CCI}/stream?drive={self.config['drive']}&since={after}&" + "&".join(f"kind={k}" for k in TIMELINE_KINDS)
                with urllib.request.urlopen(url, timeout=None) as stream:
                    for line in stream:
                        if line.startswith(b"data: "):
                            record = json.loads(line[6:])
                            after = record["seq"]
                            self.note_go(record)
                            with self.lock:
                                self.records.append(record)
                            self.touch(slow=record["kind"] in LEDGER_KINDS)
            except OSError as error:
                self.errors["records"] = f"{type(error).__name__}: {error}"
                time.sleep(5)

    def follow_board(self):
        while True:
            watch = subprocess.Popen(["cc-present", "watch", "--session", self.config["board_session"]], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            for _ in watch.stdout:
                self.touch(slow=True)
            watch.wait()
            time.sleep(5)

    def follow_steps(self):
        steps = Path(self.config["steps"])
        seen = None
        while True:
            stamp = steps.stat().st_mtime_ns
            if stamp != seen:
                seen = stamp
                self.touch()
            time.sleep(1)

    def follow_paths(self):
        cache_file = Path(self.config["paths_cache"])
        cache = json.loads(cache_file.read_text()) if cache_file.exists() else {}
        while True:
            with self.lock:
                prs = dict(self.slow.get("prs", {}))
            stale = [(n, pr["head"]) for n, pr in prs.items() if pr.get("head") and pr.get("state") in ("open", "landed") and cache.get(n, {}).get("head") != pr["head"]]
            for number, head in stale:
                try:
                    paths = run(["gh", "api", f"repos/{self.config['repo']}/pulls/{number}/files", "--paginate", "--jq", ".[].filename"]).split()
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                    self.errors["paths"] = f"#{number}: {str(error)[:200]}"
                    continue
                cache[number] = {"head": head, "trees": trees(paths, self.config["trees"])}
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

    def rebuild_loop(self):
        while True:
            time.sleep(0.5)
            with self.lock:
                due = self.dirty_at and time.monotonic() - self.dirty_at >= REBUILD_AFTER
            stale = time.monotonic() - self.slow_at >= SLOW_REFRESH
            if not due and not stale:
                continue
            with self.lock:
                self.dirty_at = 0.0
            try:
                model = self.build(refresh_slow=stale)
            except Exception as error:
                failure = f"{type(error).__name__}: {error}"
                if self.errors.get("build") != failure:
                    self.report_defect(failure)
                self.errors["build"] = failure
                self.slow_at = time.monotonic()
                continue
            self.errors.pop("build", None)
            with self.changed:
                self.model = model
                self.version += 1
                self.changed.notify_all()

    def note_go(self, record):
        if record["kind"] == "go" and (found := GO.match(record["text"])):
            self.gos[int(found[1])] = found[2]

    def load_gos(self):
        after = 0
        while True:
            page = cci("records", drive=self.config["drive"], kind="go", limit=500, since=after)
            for record in page:
                self.note_go(record)
            if len(page) < 500:
                return
            after = page[-1]["seq"]

    def check_gos(self, rows):
        for row in rows:
            row["go_head"] = self.gos.get(row["number"])
            stored = verdict_state({**row, "go_head": None}) == "approved"
            row["state"] = verdict_state(row)
            key = (row["number"], row["head"])
            if row["state"] == "approved" and not stored and key not in self.reported:
                self.reported.add(key)
                self.report_defect(f"#{row['number']} has a root go at {row['head']} but the board store holds no approval at that head; the dashboard shows it approved, landing")

    def board(self):
        subject = self.config["board_url"].rsplit("/p/", 1)[1]
        with sqlite3.connect(f"file:{STORE}?mode=ro", uri=True) as store:
            seq = store.execute("SELECT max(seq) FROM events JOIN subjects ON subjects.id = events.subject_id WHERE subjects.slug = ?", (subject,)).fetchone()[0]
        if seq != self.board_seq:
            self.board_raw = run(["cc-present", "outcomes", "--session", self.config["board_session"]])
            self.board_seq = seq
        return self.read_board(json.loads(self.board_raw))

    def live(self, model):
        board = self.board()
        self.check_gos(board["rows"])
        now = dt.datetime.now(dt.timezone.utc)
        progress = json.loads(Path(self.config["steps"]).read_text())
        activity_records = [*model["records"], *model["lanes"]]
        drive = drive_prs(model["prs"], board["rows"], self.gos, activity_records, model["trees"], model["tree_names"], progress["steps"], self.config["parts"], now)
        workers = drive_workers(activity_records, progress["steps"], now)
        return {
            **model,
            "board": board,
            "drive": drive,
            "workers": workers,
            "steps": step_rows(progress["steps"], drive, workers),
            "parts": self.config["parts"],
            "now": progress.get("now", {}),
            "steps_at": progress["updated"],
            "live": LIVE_SOURCES,
            "needs": needs(board["rows"], board["cards"], model["story"].get("asks", []), board["url"]),
        }

    def report_defect(self, text):
        subprocess.run(["cci", "post", "--drive", self.config["drive"], "--lane", self.config["keeper_lane"], "--to", f"{self.config['keeper_lane']},root", "--kind", "defect", "--text", f"drive dashboard build failed: {text}"[:390]], capture_output=True)

    def build(self, refresh_slow):
        if refresh_slow or not self.slow:
            self.slow = self.read_slow()
            self.slow_at = time.monotonic()
        with self.lock:
            records = list(self.records)
        lanes = cci("lanes", drive=self.config["drive"])
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
        prs = {row["key"]: {k: v for k, v in row["fields"].items() if not k.startswith("progress_")} for row in ledger["rows"] if row["key"].isdigit()}
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
            "releases": self.read_doc(self.config["release_doc"]),
            "words": [{"id": a["id"][:7], "at": a["updated_at"], "title": a["title"], "text": a["body"]} for a in json.loads(self.ccn("answer", "list", "--label", f"program:{self.config['drive']}", "--json"))],
        }

    def record(self, seq):
        found = json.loads(run(["cci", "show", str(seq), "--json"]))
        return {"seq": seq, "lane": found["lane"], "at": found["at"], "text": run(["cci", "show", str(seq)]).partition("\n")[2].strip() or found["text"]}

    def facts(self, number):
        head = self.slow.get("prs", {}).get(str(number), {}).get("head")
        if head and self.pr_facts.get(number, {}).get("head", "").startswith(head):
            return self.pr_facts[number]
        facts = json.loads(run(["gh", "api", f"repos/{self.config['repo']}/pulls/{number}", "--jq", PR_FACTS]))
        self.pr_facts[number] = facts
        return facts

    def read_plan(self):
        for line in self.ccn("doc", "list", "--label", self.config["progress_label"]).splitlines()[:6]:
            if plan := parse_plan(self.ccn("doc", "show", line.split("\t")[0])):
                return plan
        return None, []

    def read_doc(self, ref):
        body = self.ccn("doc", "show", ref)
        meta, _, markdown = body.partition("\n\n")
        return {"markdown": markdown, "updated": next((row.split(": ", 1)[1] for row in meta.splitlines() if row.startswith("updated: ")), None)}

    def read_board(self, board):
        interactions = board["interactions"]
        verdicts = interactions["triage"]
        retired = set(board.get("retired") or [])
        rows, cards = [], []
        for block in board["doc"]["blocks"]:
            if block["type"] != "card" or block["id"] in retired:
                continue
            groups = [child for child in block.get("children", []) if child["type"] == "pr-groups"]
            for child in groups:
                for group in child["groups"]:
                    for row in group["prs"]:
                        rows.append({**row, "group": group["title"], "group_id": group["id"], "group_summary": group.get("summary", ""), "block": child["id"], "verdict": verdicts.get(child["id"], {}).get(str(row["number"]))})
            if groups:
                continue
            asks = [child for child in block.get("children", []) if child["type"] in ASKS]
            answered = any(interactions[ASKS[child["type"]]].get(child["id"]) for child in asks) or any(interactions["feedback"].get(child["id"]) for child in [block, *block.get("children", [])])
            if answered or not (asks or block.get("status") == "open"):
                continue
            choice = next((child for child in asks if child["type"] == "choice"), None)
            options = [{"id": option["id"], "label": option["label"], "recommended": bool(option.get("recommended"))} for option in (choice or {}).get("options", [])]
            recommended = next((option["label"] for option in options if option["recommended"]), None)
            cards.append({"id": block["id"], "title": block.get("title", ""), "summary": block.get("summary", ""), "recommended": recommended, "choice": choice and choice["id"], "options": options})
        return {"rows": rows, "cards": cards, "url": self.config["board_url"]}


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
                    for asset in ("app.js", "app.css", "review.js", "review.css", "overview.js", "overview.css"):
                        body = body.replace(f"/{asset}", f"/{asset}?v={(HERE / asset).stat().st_mtime_ns}")
                    body = body.replace("</body>", ASSIST.read_text() + "</body>")
                return self.send(200, kind, body.encode())
            if path == "/api/model":
                with drive.lock:
                    model = {"version": drive.version, **drive.model}
                return self.send(200, "application/json", json.dumps(drive.live(model) if "prs" in model else model).encode())
            if path == "/api/stream":
                return self.stream()
            if found := REVIEW_ROUTE.fullmatch(path):
                try:
                    url = drive.review_url(int(found[1]))
                except subprocess.CalledProcessError as error:
                    return self.send(502, "application/json", json.dumps({"error": (error.stderr or error.stdout).strip()}).encode())
                return self.send(200, "application/json", json.dumps({"url": url}).encode())
            if path == "/ai.json":
                return self.send(200, "application/json", json.dumps({**AI_CONFIG, "key": os.environ["CEREBRAS_API_KEY"]}).encode())
            if found := RECORD_ROUTE.fullmatch(path):
                try:
                    return self.send(200, "text/plain; charset=utf-8", run(["cci", "show", found[1]]).encode())
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
            if self.path not in ("/api/verdict", "/api/answer", "/api/asked"):
                return self.send(404, "text/plain", b"not found")
            if self.headers.get_content_type() != "application/json":
                return self.send(415, "text/plain", b"Content-Type must be application/json")
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/api/answer":
                status, error = drive.answer(body)
            elif self.path == "/api/asked":
                status, error = drive.asked(body["text"])
            else:
                status, error = drive.decide(body["block"], body["number"], body["verdict"], body.get("head"), body.get("note"))
            self.send(status, "application/json", json.dumps({"error": error} if error else {}).encode())

        def send(self, status, kind, body):
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def stream(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            seen = -1
            while True:
                with drive.changed:
                    drive.changed.wait_for(lambda: drive.version != seen, timeout=15)
                    version = drive.version
                try:
                    if version == seen:
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
    drive = Drive(config, args.config.parent / "reviews")
    after = drive.load_records()
    drive.load_gos()
    for target, rest in ((drive.follow_records, (after,)), (drive.follow_board, ()), (drive.follow_steps, ()), (drive.rebuild_loop, ()), (drive.follow_paths, ())):
        threading.Thread(target=target, args=rest, daemon=True).start()
    server = ThreadingHTTPServer((config["host"], config["port"]), handler(drive))
    server.daemon_threads = True
    print(f"http://{config['host']}:{config['port']}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
