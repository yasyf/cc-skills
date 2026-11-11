import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from collections.abc import Iterable
from pathlib import Path

from drive import install, state
from drive.state import DriveError

PLUGIN = "cc-review@cc-review"
MODEL = "claude-sonnet-5-5"
START_SECONDS = 120
ATTACH_SECONDS = 20 * 60
WATCH_BACKOFF_SECONDS = 30
LIVE_SECONDS = 3
SERVED = {"ai.request.created", "comment.created"}
TAILNET_LINE = re.compile(r"^tailnet: (\S+)$", re.M)
LOCAL_LINE = re.compile(r"^(https?://\S+)$", re.M)

PROMPT = """You are the review assistant behind the drive dashboard's embedded cc-review of {key}. This directory is that review's scope and your session owns it. Handle the one event below, then stop. Never edit a file.

Event:
{event}

- An `ai.request.created` asking for review work (organize, reorganize, mark or hide files, annotate, re-rank): run the cc-review:organize agent in the foreground (Agent tool, subagent_type "cc-review:organize", the event's `request` JSON verbatim as its prompt) and wait for it.
- An `ai.request.created` that asks a question in plain words: open it with update_ai_request status "working", then answer it yourself from the diff (get_review_files and its patch paths), what the drive knows of the PR (`drive status {key} --drive {drive}`), the owner's recorded rulings (`ccn -R {rulings} answer search "<a few words of the question>"`), and the drive's records (`cci grep {number} --drive {drive} --budget 20000`). Close it with update_ai_request status "done" and the answer as its summary: plain words, at most five sentences, leading with the answer. Then run `cci post --drive {drive} --lane review-assistant --to root --kind owner --pr {key} --text "Asked in the review of {key}, and answered there: <his question> | answer: <your first sentence>"`.
- A `comment.created` from him: read the commented lines and reply with the cc-review `reply` tool (kind "question" when you need his intent, else "clarification"), answering from the same sources. A comment addressed to someone else, such as a review bot, needs no reply.
"""


def cc_review() -> str:
    return str(Path(install.installed(PLUGIN)["installPath"]) / "bin" / "cc-review")


def live(url: str) -> bool:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=LIVE_SECONDS):
            return True
    except OSError:
        return False


class Assistant:
    """Each open PR's cc-review session, started when he opens the PR or ahead of time while it waits on his tap, and a short Claude session for each question or comment he leaves in it while he has it open."""

    def __init__(self, drive: str) -> None:
        self.drive = drive
        self.lock = threading.Lock()
        self.urls: dict[tuple[str, str], str] = {}
        self.starting: dict[str, threading.Lock] = {}
        self.watching: dict[str, float] = {}
        self.streams: set[subprocess.Popen] = set()
        self.queues: dict[str, queue.Queue[dict]] = {}
        self.seen: set[tuple] = set()
        self.warmed: set[tuple[str, str]] = set()
        self.warming: queue.Queue[tuple[str, str]] = queue.Queue()
        threading.Thread(target=self.warm_loop, daemon=True).start()

    def scope(self, key: str) -> Path:
        repo, number = key.split("#")
        return state.drive_dir(self.drive) / "reviews" / repo / number

    def session(self, key: str) -> str:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{state.pr_url(key)}#{self.scope(key)}"))

    def url(self, key: str, head: str, viewing: bool = True) -> str:
        """The review's link, starting or resuming its session at head unless the link known for head still answers, and following what he says in it for ATTACH_SECONDS when he is viewing it."""
        with self.lock:
            starting = self.starting.setdefault(key, threading.Lock())
        with starting:
            if not ((url := self.urls.get((key, head))) and live(url)):
                scope = self.scope(key)
                scope.mkdir(parents=True, exist_ok=True)
                started = subprocess.run(
                    [cc_review(), "start", "--pr", key, "--session", self.session(key), "--cwd", str(scope)],
                    capture_output=True,
                    text=True,
                    timeout=START_SECONDS,
                    check=False,
                )
                if started.returncode:
                    raise DriveError(" ".join(started.stderr.split()) or f"cc-review start exited {started.returncode}")
                url = (TAILNET_LINE.search(started.stdout) or LOCAL_LINE.search(started.stdout))[1]
                self.urls[(key, head)] = url
        if viewing:
            self.attach(key)
        return url

    def warm(self, waiting: Iterable[tuple[str, str]]) -> None:
        """Start the review of each PR waiting on his tap at its head in the background, once per head, so it opens at once."""
        with self.lock:
            fresh = [pair for pair in waiting if pair not in self.warmed]
            self.warmed.update(fresh)
        for pair in fresh:
            self.warming.put(pair)

    def warm_loop(self) -> None:
        while True:
            key, head = self.warming.get()
            try:
                self.url(key, head, viewing=False)
            except (DriveError, install.InstallError, subprocess.TimeoutExpired) as error:
                print(f"starting the review of {key} at {head[:12]} failed: {error}", file=sys.stderr, flush=True)

    def attach(self, key: str) -> None:
        with self.lock:
            watching = key in self.watching
            self.watching[key] = time.monotonic() + ATTACH_SECONDS
            if key not in self.queues:
                self.queues[key] = queue.Queue()
                threading.Thread(target=self.serve, args=(key,), daemon=True).start()
        if not watching:
            threading.Thread(target=self.watch, args=(key,), daemon=True).start()

    def attached(self, key: str) -> bool:
        with self.lock:
            if time.monotonic() < self.watching.get(key, 0):
                return True
            self.watching.pop(key, None)
            return False

    def left(self, key: str) -> float:
        with self.lock:
            return self.watching.get(key, 0) - time.monotonic()

    def watch(self, key: str) -> None:
        """Relay his review events while he has the PR open, starting the watch again when it ends on its own."""
        while self.attached(key):
            stream = subprocess.Popen(
                [cc_review(), "watch", "--session", self.session(key), "--cwd", str(self.scope(key))],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            with self.lock:
                self.streams.add(stream)
            threading.Thread(target=self.relay, args=(key, stream), daemon=True).start()
            while stream.poll() is None and (left := self.left(key)) > 0:
                try:
                    stream.wait(timeout=left)
                except subprocess.TimeoutExpired:
                    pass
            if stream.poll() is None:
                stream.terminate()
                stream.wait()
            with self.lock:
                self.streams.discard(stream)
            if stream.returncode >= 0:
                time.sleep(WATCH_BACKOFF_SECONDS)

    def stop(self) -> None:
        """End every watch, so none outlives the dashboard; a Claude session answering him finishes on its own."""
        with self.lock:
            self.watching.clear()
            for stream in self.streams:
                stream.terminate()

    def relay(self, key: str, stream: subprocess.Popen) -> None:
        for line in stream.stdout:
            self.offer(key, json.loads(line))

    def offer(self, key: str, event: dict) -> None:
        if event.get("type") not in SERVED:
            return
        if event["type"] == "ai.request.created":
            seen = (key, event["request"]["id"], event["request"].get("attempt"))
        elif event["comment"].get("author") == "user":
            seen = (key, event.get("id") or json.dumps(event, sort_keys=True))
        else:
            return
        with self.lock:
            if seen in self.seen:
                return
            self.seen.add(seen)
        self.queues[key].put(event)

    def serve(self, key: str) -> None:
        """Hand each event to the PR's own Claude session, one at a time, resuming it after the first."""
        scope, session = self.scope(key), self.session(key)
        while True:
            event = self.queues[key].get()
            rulings = state.load(self.drive)["roles"]["coordinator"]["cwd"]
            prompt = PROMPT.format(key=key, number=key.split("#")[1], event=json.dumps(event, indent=2), drive=self.drive, rulings=rulings)
            resume = "--resume" if any((install.config_dir() / "projects").glob(f"*/{session}.jsonl")) else "--session-id"
            allowed = [
                "mcp__plugin_cc-review_cc-review__*",
                "Agent",
                f"Read(/{Path.home()}/.cc-review/**)",
                f"Read(/{scope}/**)",
                "Bash(drive status:*)",
                f"Bash(ccn -R {rulings} answer search:*)",
                "Bash(cci grep:*)",
                f"Bash(cci post --drive {self.drive} --lane review-assistant:*)",
            ]
            with (scope / "assistant.log").open("a") as log:
                ran = subprocess.run(
                    [
                        "claude", "-p", prompt, "--model", MODEL, "--effort", "low", "--restricted",
                        "--tools", "Agent,Bash,Read,Grep,Glob,ToolSearch",
                        "--plugin-dir", install.installed(PLUGIN)["installPath"],
                        "--add-dir", str(Path.home() / ".cc-review"),
                        "--permission-mode", "dontAsk", "--allowedTools", *allowed, resume, session,
                    ],
                    cwd=scope,
                    env=os.environ | {"DISABLE_AUTOUPDATER": "1"},
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    check=False,
                )
                if ran.returncode:
                    log.write(f"exit {ran.returncode} on {event['type']}\n")
