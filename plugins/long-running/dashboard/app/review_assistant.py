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

PROMPT = """You are the review assistant behind the drive dashboard's embedded cc-review of {repo}#{number}. This directory is that review's scope and your session owns it. Handle the one event below, then stop. Never edit a file.

Event:
{event}

- An `ai.request.created` asking for review work (organize, reorganize, mark or hide files, annotate, re-rank): run the cc-review:organize agent in the foreground (Agent tool, subagent_type "cc-review:organize", the event's `request` JSON verbatim as its prompt) and wait for it.
- An `ai.request.created` that asks a question in plain words: open it with update_ai_request status "working", then answer it yourself from the diff (get_review_files and its patch paths), the PR's merge-board row (`curl -s {model_url} | python3 -c "import json,sys; print([r for r in json.load(sys.stdin)['board']['rows'] if r['number'] == {number}])"`), the owner's rulings (`echo "<question>" | python3 {scripts}/rulings.py match --drive {drive}`), and the drive's records (`cci grep {number} --drive {drive} --budget 20000`). Close it with update_ai_request status "done" and the answer as its summary: plain words, at most five sentences, leading with the answer. Then run `cci post --drive {drive} --lane review-assistant --to root --kind owner --pr {number} --text "Asked on #{number}: <his question> | answered: <your first sentence>"`.
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
        self.closed = set()
        self.starting = {}
        self.warming = queue.Queue()
        threading.Thread(target=self.warm_loop, daemon=True).start()

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

    def review_url(self, number, head, landed, viewing=True):
        with self.lock:
            starting = self.starting.setdefault(number, threading.Lock())
        with starting:
            if not ((url := self.urls.get((number, head))) and live(url)):
                cwd = self.scope(number)
                cwd.mkdir(exist_ok=True)
                started = subprocess.run(["cc-review", "start", "--pr", f"{self.config['repo']}#{number}", "--session", self.session(number), "--cwd", str(cwd)], capture_output=True, text=True, check=True, timeout=120)
                url = TAILNET_LINE.search(started.stdout)[1]
                self.urls[(number, head)] = url
            if landed and url not in self.closed:
                subprocess.run(["cc-review", "close", url.rsplit("/s/", 1)[1]], capture_output=True, check=True, timeout=60)
                self.closed.add(url)
            if viewing and not landed:
                self.attach(number)
            return url

    def refresh(self, waiting):
        for number, head in waiting:
            if (number, head) not in self.urls:
                self.warming.put((number, head))

    def warm_loop(self):
        while True:
            number, head = self.warming.get()
            if (number, head) in self.urls:
                continue
            try:
                self.review_url(number, head, False, viewing=False)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as failure:
                with (self.reviews / "warm.log").open("a") as log:
                    log.write(f"#{number} at {head}: {failure.stderr}\n")

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
