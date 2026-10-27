import json
import os
import queue
import re
import subprocess
import threading
import time
import urllib.request
import uuid
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "skills" / "long-running" / "scripts"
WATCH_BACKOFF = 30
ATTACH_FOR = 20 * 60
SERVED = {"ai.request.created", "comment.created"}
TAILNET_LINE = re.compile(r"^tailnet: (\S+)$", re.M)
STALE_LINE = re.compile(r"^stale: ", re.M)
DAEMON_PORT = re.compile(r"^http:\s+\S+:(\d+)$", re.M)

PROMPT = """You are the review assistant behind the drive dashboard's embedded cc-review of {repo}#{number}. This directory is that review's scope and your session owns it. Handle the one event below, then stop. Never edit a file.

Event:
{event}

- An `ai.request.created` asking for review work (organize, reorganize, mark or hide files, annotate, re-rank): run the cc-review:organize agent in the foreground (Agent tool, subagent_type "cc-review:organize", the event's `request` JSON verbatim as its prompt) and wait for it.
- An `ai.request.created` that asks a question in plain words: open it with update_ai_request status "working", then answer it yourself from the diff (get_review_files and its patch paths), the PR's merge-board row (`curl -s {model_url} | python3 -c "import json,sys; print([r for r in json.load(sys.stdin)['board']['rows'] if r['number'] == {number}])"`), the owner's rulings (`echo "<question>" | python3 {scripts}/rulings.py match --drive {drive}`), and the drive's records (`cci grep {number} --drive {drive} --budget 20000`). Close it with update_ai_request status "done" and the answer as its summary: plain words, at most five sentences, leading with the answer. Then run `cci post --drive {drive} --lane review-assistant --to root --kind owner --pr {number} --text "Asked on #{number}: <his question> | answered: <your first sentence>"`; a question that starts with `[user-test]` comes from the user-test lane, not him, so post it with `--lane dashboard-ask-test --kind note` instead.
- A `comment.created` from him: read the commented lines and reply with the cc-review `reply` tool (kind "question" when you need his intent, else "clarification"), answering from the same sources. A comment addressed to someone else, such as a review bot, needs no reply.
"""


class ReviewAssistant:
    def __init__(self, config, reviews, model_url):
        self.config = config
        self.reviews = reviews
        (reviews / "assisted").mkdir(parents=True, exist_ok=True)
        self.model_url = model_url
        self.lock = threading.Lock()
        self.queues = {}
        self.watching = {}
        self.seen = set()
        self.urls = {}
        self.stale = set()
        self.closed = set()
        self.starting = {}

    def session(self, number):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"https://github.com/{self.config['repo']}/pull/{number}#{self.scope(number)}"))

    def scope(self, number):
        return self.reviews / "assisted" / str(number)

    def attach(self, number):
        with self.lock:
            watching = number in self.watching
            self.watching[number] = time.monotonic() + ATTACH_FOR
            if number not in self.queues:
                self.queues[number] = queue.Queue()
                threading.Thread(target=self.serve, args=(number,), daemon=True).start()
        if not watching:
            threading.Thread(target=self.watch, args=(number,), daemon=True).start()

    def review_url(self, number, head, landed):
        with self.lock:
            starting = self.starting.setdefault(number, threading.Lock())
        with starting:
            if not ((url := self.urls.get((number, head))) and live(url)):
                cwd = self.scope(number)
                cwd.mkdir(exist_ok=True)
                try:
                    started = subprocess.run(["cc-review", "start", "--pr", f"{self.config['repo']}#{number}", "--session", self.session(number), "--cwd", str(cwd)], capture_output=True, text=True, check=True, timeout=120)
                except subprocess.CalledProcessError:
                    if not (url := self.opened(cwd)):
                        raise
                    self.stale.add(number)
                else:
                    url = TAILNET_LINE.search(started.stdout)[1]
                    if STALE_LINE.search(started.stdout):
                        self.stale.add(number)
                    else:
                        self.stale.discard(number)
                        self.urls[(number, head)] = url
            if landed and url not in self.closed:
                subprocess.run(["cc-review", "close", url.rsplit("/s/", 1)[1]], capture_output=True, check=True, timeout=60)
                self.closed.add(url)
            if not landed:
                self.attach(number)
            return url

    def opened(self, cwd):
        listed = subprocess.run(["cc-review", "list"], capture_output=True, text=True, check=True, timeout=30).stdout.splitlines()[1:]
        slug = next((f[0] for f in map(str.split, listed) if f[1] == "open" and f[-1] == str(cwd)), None)
        port = DAEMON_PORT.search(subprocess.run(["cc-review", "status"], capture_output=True, text=True, check=True, timeout=30).stdout)
        return slug and port and f"http://{self.config['host']}:{port[1]}/s/{slug}"

    def watch(self, number):
        while True:
            with self.lock:
                if time.monotonic() >= self.watching[number]:
                    del self.watching[number]
                    return
            stream = subprocess.Popen(["cc-review", "watch", "--session", self.session(number), "--cwd", str(self.scope(number))], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            threading.Thread(target=self.relay, args=(number, stream), daemon=True).start()
            while stream.poll() is None and time.monotonic() < self.watching[number]:
                time.sleep(5)
            if stream.poll() is None:
                stream.terminate()
                stream.wait()
            else:
                time.sleep(WATCH_BACKOFF)

    def relay(self, number, stream):
        for line in stream.stdout:
            self.offer(number, json.loads(line))

    def offer(self, number, event):
        if event.get("type") not in SERVED:
            return
        if event["type"] == "ai.request.created":
            request = event["request"]
            key = (number, request["id"], request.get("attempt"))
        else:
            if event["comment"].get("author") != "user":
                return
            key = (number, event.get("id") or json.dumps(event, sort_keys=True))
        with self.lock:
            if key in self.seen:
                return
            self.seen.add(key)
        self.queues[number].put(event)

    def serve(self, number):
        while True:
            event = self.queues[number].get()
            prompt = PROMPT.format(repo=self.config["repo"], number=number, event=json.dumps(event, indent=2), model_url=self.model_url, scripts=SCRIPTS, drive=self.config["drive"])
            transcripts = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")) / "projects"
            session = ["--resume" if any(transcripts.glob(f"*/{self.session(number)}.jsonl")) else "--session-id", self.session(number)]
            with (self.scope(number) / "assistant.log").open("a") as log:
                ran = subprocess.run(["claude", "-p", prompt, "--model", "sonnet", "--effort", "low", "--permission-mode", "bypassPermissions", *session], cwd=self.scope(number), stdin=subprocess.DEVNULL, stdout=log, stderr=log)
                if ran.returncode:
                    log.write(f"exit {ran.returncode} on {event['type']}\n")

def live(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=3):
            return True
    except OSError:
        return False
