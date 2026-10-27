import argparse
import datetime as dt
import json
import os
import re
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
TIMELINE_KINDS = ["owner", "go", "landed", "release", "incident", "mechanism", "fix-live", "opened", "report", "hold", "lift", "blocker", "defect", "correction", "decide", "ask", "answer"]
TIMELINE_WINDOW = dt.timedelta(hours=24)
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
PR_FACTS = "{body: .body, additions: .additions, deletions: .deletions, files: .changed_files, commits: .commits, head: .head.sha}"
STATIC = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"), "/app.css": ("app.css", "text/css"), "/review.js": ("review.js", "text/javascript"), "/review.css": ("review.css", "text/css")}


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

    def review_url(self, number):
        cwd = self.reviews / str(number)
        cwd.mkdir(parents=True, exist_ok=True)
        return TAILNET_LINE.search(run(["cc-review", "start", "--pr", f"{self.config['repo']}#{number}", "--cwd", str(cwd)]))[1]

    def decide(self, block, number, verdict, head, note):
        origin, _, subject = self.config["board_url"].partition("/p/")
        entry = {"verdict": verdict} if verdict == "cleared" else {"verdict": verdict, "head": head, **({"note": note} if note else {})}
        interaction = {"type": "triage.decided", "blockId": block, "verdicts": {str(number): entry}}
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
                            with self.lock:
                                self.records.append(record)
                            self.touch()
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

    def check_gos(self, records):
        gos = {}
        for record in records:
            if record["kind"] == "go" and (found := GO.match(record["text"])):
                gos[int(found[1])] = found[2]
        for row in self.slow["board"]["rows"]:
            row["go_head"] = gos.get(row["number"])
            verdict = row["verdict"] or {}
            stored = verdict.get("verdict") == "approved" and row["head"].startswith(verdict.get("head", "")[:7] or "-")
            key = (row["number"], row["head"])
            if row["go_head"] and row["head"].startswith(row["go_head"][:7]) and not stored and key not in self.reported:
                self.reported.add(key)
                self.report_defect(f"#{row['number']} has a root go at {row['head']} but the board store holds no approval at that head; the dashboard shows it approved, landing")

    def report_defect(self, text):
        subprocess.run(["cci", "post", "--drive", self.config["drive"], "--lane", self.config["keeper_lane"], "--to", "root", "--kind", "defect", "--text", f"drive dashboard build failed: {text}"[:390]], capture_output=True)

    def build(self, refresh_slow):
        if refresh_slow or not self.slow:
            self.slow = self.read_slow()
            self.slow_at = time.monotonic()
        with self.lock:
            records = list(self.records)
        lanes = cci("lanes", drive=self.config["drive"])
        self.check_gos(records)
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
        board = json.loads(run(["cc-present", "outcomes", "--session", self.config["board_session"]]))
        plan_at, plan = self.read_plan()
        return {
            "prs": prs,
            "plan": plan,
            "plan_at": plan_at,
            "board": self.read_board(board),
            "story": json.loads(Path(self.config["story"]).read_text()),
            "releases": self.read_doc(self.config["release_doc"]),
            "words": [{"id": a["id"][:7], "at": a["updated_at"], "title": a["title"], "text": a["body"]} for a in json.loads(self.ccn("answer", "list", "--label", f"program:{self.config['drive']}", "--json"))],
        }

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
        verdicts = board["interactions"]["triage"]
        rows, cards = [], []
        for block in board["doc"]["blocks"]:
            if block["type"] != "card":
                continue
            groups = [child for child in block.get("children", []) if child["type"] == "pr-groups"]
            for child in groups:
                for group in child["groups"]:
                    for row in group["prs"]:
                        rows.append({**row, "group": group["title"], "group_summary": group.get("summary", ""), "block": child["id"], "verdict": verdicts.get(child["id"], {}).get(str(row["number"]))})
            if not groups and block.get("status") == "open":
                choice = next((child for child in block.get("children", []) if child["type"] == "choice"), None)
                recommended = next((option["label"] for option in (choice or {}).get("options", []) if option.get("recommended")), None)
                answered = bool(choice and board["interactions"]["choices"].get(choice["id"]))
                cards.append({"id": block["id"], "title": block.get("title", ""), "summary": block.get("summary", ""), "recommended": recommended, "answered": answered})
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
                    for asset in ("app.js", "app.css", "review.js", "review.css"):
                        body = body.replace(f"/{asset}", f"/{asset}?v={(HERE / asset).stat().st_mtime_ns}")
                    body = body.replace("</body>", ASSIST.read_text() + "</body>")
                return self.send(200, kind, body.encode())
            if path == "/api/model":
                with drive.lock:
                    body = json.dumps({"version": drive.version, **drive.model}).encode()
                return self.send(200, "application/json", body)
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
            if found := PR_ROUTE.fullmatch(path):
                try:
                    facts = drive.facts(int(found[1]))
                except subprocess.CalledProcessError as error:
                    return self.send(502, "application/json", json.dumps({"error": error.stderr.strip()}).encode())
                return self.send(200, "application/json", json.dumps(facts).encode())
            self.send(404, "text/plain", b"not found")

        def do_POST(self):
            if self.path != "/api/verdict":
                return self.send(404, "text/plain", b"not found")
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
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
    for target, rest in ((drive.follow_records, (after,)), (drive.follow_board, ()), (drive.rebuild_loop, ()), (drive.follow_paths, ())):
        threading.Thread(target=target, args=rest, daemon=True).start()
    server = ThreadingHTTPServer((config["host"], config["port"]), handler(drive))
    server.daemon_threads = True
    print(f"http://{config['host']}:{config['port']}/", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
