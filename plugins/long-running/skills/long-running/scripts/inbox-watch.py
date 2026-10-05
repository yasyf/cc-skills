#!/usr/bin/env python3
"""The root's one Monitor over the drive's inbox files.

    inbox-watch.py --state FILE [--match REGEX]... [--heartbeat NAME=PATH:SECONDS]...
                   [--session ID] [--push-after SECONDS] [--push-command CMD]
                   [--interval SECONDS] [--timeout SECONDS] [--width N] [--burst N] FILE...

STDLIB ONLY. Prints one line per appended inbox line that matches, prefixed with the
file's name. ESCALATION, INCIDENT, URGENT, DECIDE, ALERT, and `ASK root` lines always
match; --match adds more. Displayed inbox text is clipped to --width characters
(default 400), ending clipped lines with an ellipsis; stored lines stay whole.
Each file's byte offset persists in --state after every pass and survives rotation
through the stream of archives and the live file, so a re-armed watch resumes
exactly where the last one stopped: no gap, and no replay. A file the state has never
seen starts at its end. A stream that shrinks below its offset prints one RESET line
and resumes at its new end. Beyond --burst matching
lines in one pass, the urgent lines still print and the rest fold into one count.

A --heartbeat file older than its SECONDS prints one WATCH-STALE line per stale
streak, and one WATCH-LIVE line when it is written again.

With --session, an urgent line the root has not taken a turn since for --push-after
seconds (default 300) is pushed once through --push-command (default
`cc-slack dm-status --session <ID>`, the text appended as the last argument). The
root's last turn is the newest assistant event in its transcript; an
AskUserQuestion with no answer yet is named in the push, since it holds every
Monitor event and teammate message until it is answered.

The watch exits after --timeout seconds (default 1740) so the Monitor re-arms it
before its own 30-minute expiry; the offsets carry over.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from inboxes import Inbox, clip

PACIFIC = ZoneInfo("America/Los_Angeles")
URGENT = re.compile(r"\b(?:ESCALATION|INCIDENT|URGENT|DECIDE|ALERT)\b|\bASK root\b")
TAIL_BYTES = 1 << 20


def clock(moment: float) -> str:
    return datetime.fromtimestamp(moment, PACIFIC).strftime("%-I:%M %p")


@dataclass(frozen=True)
class Heartbeat:
    name: str
    path: Path
    seconds: float

    @classmethod
    def parse(cls, spec: str) -> Heartbeat:
        name, _, rest = spec.partition("=")
        path, _, seconds = rest.rpartition(":")
        if not name or not path or not seconds:
            raise argparse.ArgumentTypeError(f"--heartbeat takes NAME=PATH:SECONDS, not {spec!r}")
        return cls(name, Path(path).expanduser(), float(seconds))


@dataclass(frozen=True)
class RootTurn:
    at: float
    asking_since: float | None


def transcript_of(session: str) -> Path | None:
    return next((Path.home() / ".claude" / "projects").glob(f"*/{session}.jsonl"), None)


def epoch(stamp: str) -> float:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()


def root_turn(transcript: Path) -> RootTurn | None:
    size = transcript.stat().st_size
    span = TAIL_BYTES
    while True:
        turn = turn_in(transcript, size, span)
        if turn or span >= size:
            return turn
        span *= 4


def turn_in(transcript: Path, size: int, span: int) -> RootTurn | None:
    with transcript.open("rb") as handle:
        handle.seek(max(0, size - span))
        tail = handle.read(min(span, size)).splitlines()[1 if size > span else 0 :]
    last = None
    asked: dict[str, float] = {}
    for raw in tail:
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        if event.get("isSidechain") or "timestamp" not in event:
            continue
        content = (event.get("message") or {}).get("content")
        blocks = content if isinstance(content, list) else []
        if event.get("type") == "assistant":
            last = epoch(event["timestamp"])
            for block in blocks:
                if block.get("type") == "tool_use" and block.get("name") == "AskUserQuestion":
                    asked[block["id"]] = last
        for block in blocks:
            if block.get("type") == "tool_result":
                asked.pop(block.get("tool_use_id"), None)
    if last is None:
        return None
    return RootTurn(last, min(asked.values()) if asked else None)


class Watch:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.files = [Path(path).expanduser() for path in args.files]
        self.extra = [re.compile(pattern) for pattern in args.match]
        self.state_path = Path(args.state).expanduser()
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        self.state.setdefault("offsets", {})
        self.state.setdefault("stale", {})
        self.state.setdefault("pending", [])
        self.transcript = transcript_of(args.session) if args.session else None
        if args.session and not self.transcript:
            raise SystemExit(f"inbox-watch: no transcript for session {args.session} under ~/.claude/projects")

    def save(self) -> None:
        temporary = self.state_path.with_name(self.state_path.name + ".new")
        temporary.write_text(json.dumps(self.state))
        os.replace(temporary, self.state_path)

    def matches(self, line: str) -> bool:
        return bool(URGENT.search(line)) or any(pattern.search(line) for pattern in self.extra)

    def read(self, path: Path) -> list[tuple[bool, str]]:
        key = str(path)
        if not path.is_file():
            self.state["offsets"].setdefault(key, 0)
            return []
        inbox = Inbox(path)
        end = inbox.end()
        offset = self.state["offsets"].get(key)
        if offset is None:
            self.state["offsets"][key] = end
            return []
        if end < offset:
            self.state["offsets"][key] = end
            return [(False, f"RESET {path.name}: shrank from {offset} to {end} bytes; resuming at its end")]
        lines = inbox.lines(offset)
        if lines:
            self.state["offsets"][key] = lines[-1].end
        return [
            (bool(URGENT.search(line.text)), f"{path.name}: {clip(line.text, self.args.width)}")
            for line in lines
            if line.text.strip() and self.matches(line.text)
        ]

    def emit(self, lines: list[tuple[bool, str]], now: float) -> None:
        urgent = [line for loud, line in lines if loud]
        rest = [line for loud, line in lines if not loud]
        room = max(0, self.args.burst - len(urgent))
        for line in urgent:
            print(line, flush=True)
            self.state["pending"].append({"at": now, "line": line, "pushed": False})
        for line in rest[:room]:
            print(line, flush=True)
        if len(rest) > room:
            print(f"+{len(rest) - room} more matching lines this pass; catch up with inbox-digest.py --state <drive>/inbox/.inbox-digest.json <files>", flush=True)

    def heartbeats(self, now: float) -> None:
        for beat in self.args.heartbeat:
            written = beat.path.stat().st_mtime if beat.path.exists() else 0.0
            stale = now - written > beat.seconds
            reported = self.state["stale"].get(beat.name)
            if stale and reported is None:
                since = f"last written {clock(written)}, {int((now - written) // 60)} min ago" if written else "never written"
                print(f"WATCH-STALE {beat.name}: {beat.path} {since}; that watch is blind until it is replaced", flush=True)
                self.state["stale"][beat.name] = written
            elif not stale and reported is not None:
                print(f"WATCH-LIVE {beat.name}: {beat.path} written {clock(written)}", flush=True)
                del self.state["stale"][beat.name]

    def push(self, now: float) -> None:
        if not self.transcript:
            return
        turn = root_turn(self.transcript)
        if turn is None:
            return
        self.state["pending"] = [item for item in self.state["pending"] if item["at"] > turn.at]
        overdue = [
            item
            for item in self.state["pending"]
            if not item["pushed"] and now - item["at"] >= self.args.push_after and now - item.get("tried", 0) >= self.args.push_after
        ]
        if not overdue:
            return
        waiting = f"; it has been waiting on your answer in the terminal since {clock(turn.asking_since)}" if turn.asking_since else ""
        text = f"The drive's root has not acted on an urgent line for {int((now - overdue[0]['at']) // 60)} min{waiting}: " + " | ".join(item["line"] for item in overdue)
        result = subprocess.run([*shlex.split(self.args.push_command), text], capture_output=True, text=True)
        for item in overdue:
            item["pushed"] = result.returncode == 0
            item["tried"] = now
        print(f"PUSHED {len(overdue)} urgent line(s) to the owner{' (root waiting on an open question)' if turn.asking_since else ''}" + ("" if result.returncode == 0 else f"; the push failed: {(result.stderr or result.stdout).strip()[:200]}"), flush=True)

    def tick(self) -> None:
        now = time.time()
        lines = [line for path in self.files for line in self.read(path)]
        self.emit(lines, now)
        self.heartbeats(now)
        self.push(now)
        self.save()


def main() -> int:
    parser = argparse.ArgumentParser(usage=__doc__)
    parser.add_argument("files", nargs="+")
    parser.add_argument("--state", required=True)
    parser.add_argument("--match", action="append", default=[])
    parser.add_argument("--heartbeat", action="append", default=[], type=Heartbeat.parse)
    parser.add_argument("--session")
    parser.add_argument("--push-after", type=float, default=300)
    parser.add_argument("--push-command")
    parser.add_argument("--interval", type=float, default=2)
    parser.add_argument("--timeout", type=float, default=1740)
    parser.add_argument("--width", type=int, default=400)
    parser.add_argument("--burst", type=int, default=12)
    args = parser.parse_args()
    if args.session and not args.push_command:
        args.push_command = f"cc-slack dm-status --session {shlex.quote(args.session)}"
    watch = Watch(args)
    deadline = time.monotonic() + args.timeout
    while True:
        watch.tick()
        if time.monotonic() + args.interval >= deadline:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
