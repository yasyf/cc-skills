#!/usr/bin/env python3
"""The root's one Monitor over cci records, team mailboxes, and watch heartbeats.

    inbox-watch.py --state FILE --drive DRIVE [--kind KIND]...
                   [--cursor CURSOR] [--reader READER] [--heartbeat NAME=PATH:SECONDS]...
                   [--session ID] [--push-after SECONDS] [--push-command CMD]
                   [--interval SECONDS] [--timeout SECONDS] [--width N] [--burst N]
                   [MAILBOX.json ...]

Each pass runs `cci tail --drive <drive> --cursor <cursor> --reader <reader> --json`
for incident, decide, ask, defect, and blocker, plus each --kind. The required --drive
selects the cci drive; --cursor defaults to root-watch and --reader to root.
Records addressed to root arrive regardless of kind with the default reader.
The watch polls every --interval seconds (default 5). It reads no markdown inboxes.

Each record prints as `#<seq> <KIND> <lane>: <text> [path]`, with the path when
attached. The whole line is clipped to --width characters (default 400), ending
clipped lines with an ellipsis; stored records stay whole. The five default kinds
are urgent, as is any record whose text contains ESCALATION, INCIDENT, URGENT,
DECIDE, ALERT, or `ASK root`. Urgent records always print. Beyond --burst lines per
pass (default 12), the remaining records fold into a count with
`read them with cci grep --drive <drive> --since 1h`. A failed cci read prints one
CCI-FAIL line per failure streak.

Positional arguments are team mailbox .json files only. Each mailbox's saved offset
counts entries, and an unseen mailbox starts at its current entry count. A new
read=false entry prints one MAILBOX <n> unread line, with n counting all read=false
entries. That line is never urgent or pushed.

A --heartbeat file older than its SECONDS prints one WATCH-STALE line per stale
streak, and one WATCH-LIVE line when it is written again.

With --session, an urgent line the root has not taken a turn since for --push-after
seconds (default 300) is pushed once through --push-command (default
`cc-slack dm-status --session <ID>`, the text appended as the last argument). The
root's last turn is the newest assistant event in its transcript; an
AskUserQuestion with no answer yet is named in the push, since it holds every
Monitor event and teammate message until it is answered.

cci owns the record cursor. Mailbox offsets, heartbeat status, pending pushes, and
failure status persist in --state after every pass. The watch exits after --timeout
seconds (default 1740). Arm the Monitor at timeout 1800000 and re-arm it on every
exit and after compaction with the same state file and cci cursor.
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

from inboxes import Mailbox, clip

PACIFIC = ZoneInfo("America/Los_Angeles")
URGENT = re.compile(r"\b(?:ESCALATION|INCIDENT|URGENT|DECIDE|ALERT)\b|\bASK root\b")
URGENT_KINDS = ("incident", "decide", "ask", "defect", "blocker")
CCI_BUDGET = 16000
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
        self.mailboxes = [Path(path).expanduser() for path in args.mailboxes]
        self.kinds = [*URGENT_KINDS, *args.kind]
        self.state_path = Path(args.state).expanduser()
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        self.state.setdefault("offsets", {})
        self.state.setdefault("stale", {})
        self.state.setdefault("pending", [])
        self.state.setdefault("cci_failing", False)
        self.transcript = transcript_of(args.session) if args.session else None
        if args.session and not self.transcript:
            raise SystemExit(f"inbox-watch: no transcript for session {args.session} under ~/.claude/projects")

    def save(self) -> None:
        temporary = self.state_path.with_name(self.state_path.name + ".new")
        temporary.write_text(json.dumps(self.state))
        os.replace(temporary, self.state_path)

    def mail(self, path: Path) -> list[tuple[bool, str]]:
        polled = Mailbox(path).poll(self.state["offsets"].get(str(path)))
        if polled is None:
            return []
        count, unread = polled
        self.state["offsets"][str(path)] = count
        return [(False, f"MAILBOX {unread} unread")] if unread else []

    def records(self) -> list[tuple[bool, str]]:
        argv = ["cci", "tail", "--drive", self.args.drive, "--cursor", self.args.cursor, "--reader", self.args.reader, "--json", "--budget", str(CCI_BUDGET)]
        result = subprocess.run([*argv, *(f"--kind={kind}" for kind in self.kinds)], capture_output=True, text=True)
        if result.returncode != 0:
            failing, self.state["cci_failing"] = self.state["cci_failing"], True
            return [] if failing else [(False, f"CCI-FAIL {(result.stderr or result.stdout).strip()[:200]}")]
        self.state["cci_failing"] = False
        lines = []
        for raw in result.stdout.splitlines():
            record = json.loads(raw)
            text = f"#{record['seq']} {record['kind'].upper()} {record['lane']}: {record['text']}"
            if path := record.get("refs", {}).get("path"):
                text += f" {path}"
            lines.append((record["kind"] in URGENT_KINDS or bool(URGENT.search(record["text"])), clip(text, self.args.width)))
        return lines

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
            print(f"+{len(rest) - room} more records this pass; read them with cci grep --drive {self.args.drive} --since 1h", flush=True)

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
        lines = [*self.records(), *(line for path in self.mailboxes for line in self.mail(path))]
        self.emit(lines, now)
        self.heartbeats(now)
        self.push(now)
        self.save()


def main() -> int:
    parser = argparse.ArgumentParser(usage=__doc__)
    parser.add_argument("mailboxes", nargs="*")
    parser.add_argument("--state", required=True)
    parser.add_argument("--drive", required=True)
    parser.add_argument("--cursor", default="root-watch")
    parser.add_argument("--reader", default="root")
    parser.add_argument("--kind", action="append", default=[])
    parser.add_argument("--heartbeat", action="append", default=[], type=Heartbeat.parse)
    parser.add_argument("--session")
    parser.add_argument("--push-after", type=float, default=300)
    parser.add_argument("--push-command")
    parser.add_argument("--interval", type=float, default=5)
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
