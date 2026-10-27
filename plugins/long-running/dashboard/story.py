from __future__ import annotations

import datetime as dt
import json
import os
import queue
import re
import select
import subprocess
import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import plainwords
from brief import QUIET

PACIFIC = ZoneInfo("America/Los_Angeles")
CATCH_UP = dt.timedelta(minutes=30)
CATCH_UP_CHARS = 90_000
MIN_CATCH_UP = 60
FRESH = 2 * 3600
BATCH_EVENTS = 40
BATCH_IDLE = 90
LINGER = 180
MAX_SIDECARS = 4
MAX_CONTEXT_CHARS = 500_000
MARKS = {"progress", "finding", "stuck", "done"}
BEAT = re.compile(r"git push|ccx vcs ship|cci post|gh pr create")
LINE = re.compile(r"^\s*(\d{1,2}:\d{2} [AP]M)\s*\|\s*(\w+)\s*\|\s*(.+?)\s*$")
WRITER = """You turn an AI coding worker's transcript into a short live story for a busy engineering lead who never reads transcripts.

The work: {title}. What it is for: {goal}

Each message you get is a slice of the worker's transcript. Answer with zero or more story lines, one per line, exactly in the form
TIME | MARK | LINE
where TIME is the Pacific clock time of the moment in the transcript (such as 12:13 PM), MARK is one of progress, finding, stuck, done, and LINE is one plain sentence of at most 30 words saying what the worker found, changed, pushed, learned, or is stuck on, and why it matters.

Write a line only for a real beat: a finding, a change made, a push or review opened ("Opened for review" is progress, never done), a test or CI result, a stuck point, a finish. Skip routine bookkeeping such as status posts, reading files, handing over to a successor, or waiting. When nothing in the slice is worth a line, answer with the single word SKIP.

Never write a command, file path, hash, record number, pull request number, branch, URL, worker name, or these words: lane, desk, watchdog, finder, slice, census, fidelity, register, mirror, forest, rider, carry, restack, rebase, enqueue, old world, new world. Say "the worker", "the merge process", "the automated review". Use the product names users know (Sand, Iris, SandSQL, S3, codex). Never repeat a line you already wrote."""


def stamp(iso):
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))


def clock(when):
    return when.astimezone(PACIFIC).strftime("%-I:%M %p")


def render(event):
    kind = event.get("type")
    when = event.get("timestamp")
    content = event.get("message", {}).get("content")
    if kind not in ("user", "assistant") or not when or not isinstance(content, list):
        return None
    parts = []
    for block in content:
        if block.get("type") == "text" and kind == "assistant":
            parts.append(f"said: {block['text'][:1500]}")
        elif block.get("type") == "tool_use":
            parts.append(f"ran {block['name']}: {json.dumps(block.get('input'))[:400]}")
        elif block.get("type") == "tool_result":
            body = block.get("content")
            text = body if isinstance(body, str) else " ".join(b.get("text", "") for b in body or [] if isinstance(b, dict))
            parts.append(f"{'ERROR ' if block.get('is_error') else ''}result: {text[:500]}")
    return f"[{clock(stamp(when))}] " + " | ".join(parts) if parts else None


def is_beat(event):
    content = event.get("message", {}).get("content")
    if not isinstance(content, list):
        return False
    for block in content:
        if block.get("type") == "text" and event.get("type") == "assistant" and len(block.get("text", "")) > 80:
            return True
        if block.get("type") == "tool_result" and block.get("is_error"):
            return True
        if block.get("type") == "tool_use" and BEAT.search(json.dumps(block.get("input"))):
            return True
    return False


class Sidecar:
    def __init__(self, item, title, goal, saved_path, publish, lanes, model, raw_log):
        self.item = item
        self.raw_log = raw_log
        self.saved_path = saved_path
        self.publish = publish
        self.lanes = lanes
        self.chars = 0
        self.proc = subprocess.Popen(
            ["claude", "-p", *QUIET, "--model", model, "--effort", "low", "--input-format", "stream-json", "--output-format", "stream-json", "--include-partial-messages", "--verbose", "--system-prompt", WRITER.format(title=title, goal=goal)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self.turn_done = threading.Event()
        self.turn_done.set()
        threading.Thread(target=self.read, daemon=True).start()

    def send(self, text):
        self.turn_done.wait()
        self.turn_done.clear()
        self.chars += len(text)
        self.proc.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": text}}) + "\n")
        self.proc.stdin.flush()

    def read(self):
        pending = ""
        for raw in self.proc.stdout:
            event = json.loads(raw)
            if event.get("type") == "result":
                with self.raw_log.open("a") as log:
                    log.write(json.dumps({"at": time.time(), "result": event.get("result")}) + "\n")
            if event.get("type") == "stream_event":
                delta = event["event"].get("delta", {})
                if delta.get("type") == "text_delta":
                    pending += delta["text"]
                    *done, pending = pending.split("\n")
                    for line in done:
                        self.finish(line)
                    if pending.strip() and pending.strip() != "SKIP":
                        self.publish({"type": "draft", "text": LINE.sub(r"\3", pending) if LINE.match(pending + " ") else pending.split("|")[-1].strip()})
            elif event.get("type") == "result":
                self.finish(pending)
                pending = ""
                self.publish({"type": "idle"})
                self.turn_done.set()
        self.turn_done.set()

    def finish(self, line):
        found = LINE.match(line)
        if not found or found[2] not in MARKS or plainwords.violations(found[3], "story", self.lanes):
            return
        entry = {"type": "line", "at": found[1], "mark": found[2], "line": found[3]}
        with self.saved_path.open("a") as saved:
            saved.write(json.dumps(entry) + "\n")
        self.publish(entry)

    def stop(self):
        self.proc.terminate()


class Story:
    def __init__(self, stories, item, work):
        self.stories = stories
        self.item = item
        self.work = work
        self.viewers = []
        self.lock = threading.Lock()
        self.left_at = None
        self.sidecar = None
        self.saved = stories.dir / f"{item}.jsonl"
        self.meta_path = stories.dir / f"{item}.meta.json"
        self.status = None
        self.draft = None

    def lines(self):
        return [json.loads(line) for line in self.saved.read_text().splitlines()] if self.saved.exists() else []

    def publish(self, entry):
        if entry["type"] == "idle" and not self.saved.exists():
            entry = {"type": "status", "text": "Nothing worth telling in its recent work yet; the next step it takes shows here."}
        if entry["type"] == "draft":
            self.draft = entry["text"]
        elif entry["type"] == "status":
            self.status = entry["text"]
            self.draft = None
        elif entry["type"] in ("line", "idle"):
            self.draft = None
            self.status = None
        with self.lock:
            viewers = list(self.viewers)
        for viewer in viewers:
            viewer.put(entry)

    def join(self):
        viewer = queue.Queue()
        with self.lock:
            self.viewers.append(viewer)
            self.left_at = None
        if self.sidecar is None or self.sidecar.proc.poll() is not None:
            threading.Thread(target=self.run, daemon=True).start()
        return viewer

    def leave(self, viewer):
        with self.lock:
            self.viewers.remove(viewer)
            if not self.viewers:
                self.left_at = time.monotonic()

    def run(self):
        transcript = self.stories.transcript(self.work["workers"])
        fresh = transcript is not None and time.time() - transcript.stat().st_mtime < FRESH
        meta = json.loads(self.meta_path.read_text()) if self.meta_path.exists() else {}
        resume = fresh and meta.get("file") == str(transcript) and self.saved.exists()
        offset = meta["offset"] if resume else 0
        self.sidecar = Sidecar(self.item, self.work["title"], self.work.get("goal", self.work["latest"]), self.saved, self.publish, self.stories.lanes(), self.stories.model, self.stories.dir / f"{self.item}.raw.log")
        self.stories.started(self)
        self.status = "Catching up on the last 30 minutes"
        self.publish({"type": "status", "text": self.status})
        if not fresh:
            reports = self.stories.reports(self.work["workers"])
            if not reports:
                self.publish({"type": "status", "text": "This work has not reported anything yet."})
                return self.linger()
            self.sidecar.send("This worker's transcript is not on this Mac. Here are its latest reports. Write three to five lines that tell the story so far.\n\n" + "\n".join(reports))
            return self.linger()
        cutoff = dt.datetime.now(dt.timezone.utc) - CATCH_UP
        rendered = []
        with transcript.open() as handle:
            handle.seek(offset)
            for raw in handle:
                event = json.loads(raw)
                if text := render(event):
                    rendered.append((event, text))
            offset = handle.tell()
        recent = [text for event, text in rendered if stamp(event["timestamp"]) >= cutoff]
        backlog = recent if resume or len(recent) >= MIN_CATCH_UP else [text for _, text in rendered[-MIN_CATCH_UP:]]
        catch_up = "\n".join(backlog)[-CATCH_UP_CHARS:]
        if catch_up:
            self.sidecar.send(f"Here is the worker's recent transcript. Write three to five lines that tell the story so far.\n\n{catch_up}")
        self.follow(transcript, offset)

    def linger(self):
        try:
            while self.sidecar.proc.poll() is None:
                time.sleep(5)
                with self.lock:
                    if self.left_at is not None and time.monotonic() - self.left_at > LINGER:
                        return
        finally:
            self.sidecar.stop()
            self.stories.ended(self)

    def follow(self, transcript, offset):
        events = []
        kq = select.kqueue()
        fd = os.open(transcript, os.O_RDONLY)
        watch = select.kevent(fd, filter=select.KQ_FILTER_VNODE, flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR, fflags=select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND)
        try:
            while self.sidecar.proc.poll() is None:
                with self.lock:
                    idle = self.left_at is not None and time.monotonic() - self.left_at > LINGER
                if idle:
                    break
                kq.control([watch], 1, 30)
                with transcript.open() as handle:
                    handle.seek(offset)
                    for raw in handle:
                        if not raw.endswith("\n"):
                            break
                        offset += len(raw.encode())
                        event = json.loads(raw)
                        if text := render(event):
                            events.append((text, is_beat(event)))
                if events and (any(beat for _, beat in events) or len(events) >= BATCH_EVENTS):
                    self.sidecar.send("New transcript since your last lines:\n\n" + "\n".join(text for text, _ in events))
                    events = []
                    self.meta_path.write_text(json.dumps({"file": str(transcript), "offset": offset}))
                if self.sidecar.chars > MAX_CONTEXT_CHARS:
                    self.sidecar.stop()
                    self.sidecar = Sidecar(self.item, self.work["title"], self.work.get("goal", self.work["latest"]), self.saved, self.publish, self.stories.lanes(), self.stories.model, self.stories.dir / f"{self.item}.raw.log")
                    self.sidecar.send("The story so far:\n" + "\n".join(f"{e['at']} | {e['mark']} | {e['line']}" for e in self.lines()[-20:]) + "\n\nAnswer SKIP to this message.")
        finally:
            os.close(fd)
            kq.close()
            self.meta_path.write_text(json.dumps({"file": str(transcript), "offset": offset}))
            self.sidecar.stop()
            self.stories.ended(self)


class Stories:
    def __init__(self, config, lanes, reports):
        self.config = config
        self.lanes = lanes
        self.reports = reports
        self.open = {}
        self.running = []
        self.lock = threading.Lock()

    @property
    def dir(self):
        found = Path(self.config["stories_dir"])
        found.mkdir(parents=True, exist_ok=True)
        return found

    @property
    def transcripts(self):
        return Path(self.config["transcripts"])

    @property
    def model(self):
        return self.config.get("story_model", "claude-sonnet-5-5")

    def story(self, item, work):
        with self.lock:
            found = self.open.get(item)
            if found is None:
                found = self.open[item] = Story(self, item, work)
            found.work = work
            return found

    def transcript(self, workers):
        candidates = [path for lane in workers for path in self.transcripts.glob(f"agent-a{re.sub(r'-\d+$', '', lane)}*.jsonl")]
        return max(candidates, key=lambda p: p.stat().st_mtime, default=None)

    def started(self, story):
        with self.lock:
            self.running.append(story)
            excess = len(self.running) - MAX_SIDECARS
            evict = sorted((s for s in self.running if s is not story), key=lambda s: s.left_at or float("inf"))[:max(0, excess)]
        for old in evict:
            with old.lock:
                old.left_at = 0.0

    def stop_all(self):
        with self.lock:
            running = list(self.running)
        for story in running:
            story.sidecar.stop()

    def ended(self, story):
        with self.lock:
            if story in self.running:
                self.running.remove(story)
