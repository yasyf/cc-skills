#!/usr/bin/env python3
"""Read, digest, and rotate desk inboxes without moving their cursors.

    inbox-digest.py (--state FILE | --all) [--line-cap 200] [--budget 6144] FILE...
    inbox-rotate.py [--hours 6] FILE...
    desk-wait.sh <seconds> (<file>=<cursor-file> | cci:<drive>:<lane>)... [-- <command> [<arg>...]]

STDLIB ONLY. The shims call this module's digest, rotate, and wait commands.
An inbox's sorted <file>.archive/YYYY-MM-DD.md files, oldest first, followed by
the live file form one byte stream. Byte offsets and line counts survive rotation.
Readers hold a shared flock on the inbox's directory; rotation holds it exclusively.

digest groups new lines by file, clips each to --line-cap characters, and keeps
the newest within --budget bytes, plus a line counting omissions. --state advances
each inbox's cursor and starts an unseen inbox at the live file's beginning. It
never reads archives and reports unread archived bytes. --all reads archives and
the live file with the same caps, touching no state, for a new lane's orientation.
The root uses <drive>/inbox/.inbox-digest.json.

rotate records a time and stream-end mark each run. It archives complete lines
through the newest mark at least --hours old, writes the rest to a temporary file,
and atomically renames it over the inbox. tail -F reopens by name; bytes appended
to the old file during the rename are copied into the new one. With the default
six-hour window, archival starts about six hours after the first mark.

wait advances line-count cursors over the same stream and prints new nonblank
lines clipped to 400 characters, ending clipped lines with an ellipsis. It returns
when lines arrive or prints QUIET with the Pacific time at the deadline. Display
clipping never cuts lines stored in the inbox. A .json file in an inboxes directory
is a team mailbox whose cursor counts seen entries, starting at 0 if missing or
if the mailbox shrinks below it. An entry past the cursor with read=false ends
the call with MAILBOX <n> unread (n counts all read=false entries), letting Claude
Code deliver the message at that tool-call boundary. The stat check runs every
0.2 s, so a wake lands within about 2 s; read=true entries and a missing mailbox
never wake it. A cci:<drive>:<lane> source prints the cci records addressed to
<lane> past the cursor named <lane>, through `cci tail`, which advances it.

wait ... -- <command> runs the command in its own process group, with its output
passing through, and returns with its exit status when it exits first. When a source
wakes the call or the deadline passes, wait prints what it prints without a command
(MAILBOX, lines, or QUIET), ends the command's process group with SIGTERM, then
SIGKILL after 5 s, and exits 0. Use it for read-only watches such as `bk build watch`
or `ccx vcs pr watch`, never for a command that writes.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
DISPLAY_CHARS = 400
DIGEST_CHARS = 200
DIGEST_BYTES = 6144
ROTATE_HOURS = 6.0
WAIT_USAGE = "usage: desk-wait.sh <seconds> (<file>=<cursor-file> | cci:<drive>:<lane>)... [-- <command> [<arg>...]]"
WAIT_SOURCE = re.compile(r"cci:(?P<drive>[^:=]+):(?P<lane>[^:=]+)|(?P<file>[^=]+)=(?P<cursor>.+)", re.DOTALL)
WAIT_SLICE = 2.0
MAILBOX_POLL = 0.2
CHILD_GRACE = 5.0


def clip(text: str, cap: int = DISPLAY_CHARS) -> str:
    return text if len(text) <= cap else f"{text[: cap - 1]}…"


@dataclass(frozen=True)
class Line:
    start: int
    end: int
    text: str


class Inbox:
    def __init__(self, path: Path):
        self.path = path
        self.archive = path.with_name(f"{path.name}.archive")
        self.marks = self.archive / "marks.json"

    @contextmanager
    def locked(self, operation: int) -> Iterator[None]:
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            fcntl.flock(directory, operation)
            yield
        finally:
            os.close(directory)

    def archives(self) -> list[Path]:
        return sorted(self.archive.glob("*.md"))

    def base(self) -> int:
        return sum(part.stat().st_size for part in self.archives())

    def parts(self) -> list[Path]:
        return [*self.archives(), *([self.path] if self.path.is_file() else [])]

    def end(self) -> int:
        with self.locked(fcntl.LOCK_SH):
            return sum(part.stat().st_size for part in self.parts())

    def lines(self, start: int = 0) -> list[Line]:
        with self.locked(fcntl.LOCK_SH):
            data = self.read(start)
        lines = []
        for raw in data[: data.rfind(b"\n") + 1].split(b"\n")[:-1]:
            lines.append(Line(start, start + len(raw) + 1, raw.decode(errors="replace")))
            start += len(raw) + 1
        return lines

    def read(self, start: int) -> bytes:
        chunks = []
        position = 0
        for part in self.parts():
            size = part.stat().st_size
            if position + size > start:
                with part.open("rb") as handle:
                    handle.seek(max(0, start - position))
                    chunks.append(handle.read() if part == self.path else handle.read(position + size - max(start, position)))
            position += size
        return b"".join(chunks)

    def rotate(self, now: float, hours: float = ROTATE_HOURS) -> int:
        with self.locked(fcntl.LOCK_EX):
            if not self.path.is_file():
                return 0
            cutoff = now - hours * 3600
            marks = json.loads(self.marks.read_text()) if self.marks.is_file() else []
            base = self.base()
            self.archive.mkdir(exist_ok=True)
            with self.path.open("rb") as live:
                held = live.read()
                cut = held.rfind(b"\n", 0, max((end for at, end in marks if at <= cutoff), default=base) - base) + 1
                late = b""
                if cut:
                    with (self.archive / f"{datetime.fromtimestamp(now, PACIFIC):%Y-%m-%d}.md").open("ab") as archived:
                        archived.write(held[:cut])
                    staged = self.path.with_name(f".{self.path.name}.rotating")
                    staged.write_bytes(held[cut:])
                    os.replace(staged, self.path)
                    late = live.read()
                    if late:
                        with self.path.open("ab") as fresh:
                            fresh.write(late)
            staged = self.marks.with_name(f"{self.marks.name}.new")
            staged.write_text(json.dumps([*([at, end] for at, end in marks if at > cutoff), [now, base + len(held) + len(late)]]))
            os.replace(staged, self.marks)
            return cut


class Mailbox:
    def __init__(self, path: Path):
        self.path = path

    @staticmethod
    def holds(path: Path) -> bool:
        return path.suffix == ".json" and path.parent.name == "inboxes"

    def mark(self) -> tuple[int, int] | None:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return None
        return stat.st_mtime_ns, stat.st_size

    def poll(self, seen: int | None) -> tuple[int, int] | None:
        try:
            entries = json.loads(self.path.read_text())
        except FileNotFoundError:
            return 0, 0
        except ValueError:
            return None
        if seen is None:
            return len(entries), 0
        fresh = entries[seen:] if seen <= len(entries) else entries
        unread = sum(not entry["read"] for entry in entries)
        return len(entries), unread if any(not entry["read"] for entry in fresh) else 0


def pause(seconds: float, mailboxes: list[Mailbox], child: subprocess.Popen | None) -> None:
    marks = [mailbox.mark() for mailbox in mailboxes]
    until = time.monotonic() + seconds
    while (left := until - time.monotonic()) > 0:
        time.sleep(min(MAILBOX_POLL, left))
        if [mailbox.mark() for mailbox in mailboxes] != marks or (child and child.poll() is not None):
            return


def end(child: subprocess.Popen) -> None:
    if child.poll() is None:
        os.killpg(child.pid, signal.SIGTERM)
        try:
            child.wait(CHILD_GRACE)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()


def cost(texts: list[str]) -> int:
    return sum(len(text.encode()) + 1 for text in texts)


def digest(paths: list[Path], state: Path | None, cap: int, budget: int) -> str:
    cursors = json.loads(state.read_text()) if state and state.is_file() else {}
    news: dict[Path, list[str]] = {}
    archived: dict[Path, int] = {}
    for path in paths:
        inbox = Inbox(path)
        start = 0
        if state is not None:
            base = inbox.base()
            cursor = cursors.get(str(path), base)
            archived[path] = max(0, base - cursor)
            start = max(cursor, base)
        lines = inbox.lines(start)
        if state is not None:
            cursors[str(path)] = lines[-1].end if lines else start
        news[path] = [clip(line.text, cap) for line in lines if line.text.strip()]
    shown: dict[Path, list[str]] = {}
    remaining = budget
    for index, path in enumerate(sorted(news, key=lambda path: cost(news[path]))):
        texts = news[path]
        allot = remaining // (len(news) - index)
        spent = cost([f"== {path.name}: {len(texts)} of {len(texts)} lines"]) if texts else 0
        kept = 0
        for text in reversed(texts):
            if spent + cost([text]) > allot:
                break
            spent += cost([text])
            kept += 1
        shown[path] = texts[len(texts) - kept :]
        remaining -= spent
    out = []
    for path, texts in news.items():
        if texts:
            out += [f"== {path.name}: {len(shown[path])} of {len(texts)} lines", *shown[path]]
        if archived.get(path):
            out.append(f"== {path.name}: {archived[path]} unread bytes moved to {path.name}.archive/; --all reads them")
    omitted = [f"{path.name} {len(texts) - len(shown[path])}" for path, texts in news.items() if len(shown[path]) < len(texts)]
    if omitted:
        out.append(f"omitted, oldest first, over the {budget}-byte budget: {', '.join(omitted)} lines")
    if state is not None:
        staged = state.with_name(f"{state.name}.new")
        staged.write_text(json.dumps(cursors))
        os.replace(staged, state)
    return "\n".join(out or ["no new inbox lines"]) + "\n"


def seen_count(cursor: Path) -> int:
    text = cursor.read_text().strip() if cursor.is_file() else ""
    return int(text) if text else 0


def wait(argv: list[str]) -> int:
    guarded = "--" in argv
    split = argv.index("--") if guarded else len(argv)
    argv, command = argv[:split], argv[split + 1 :]
    sources = [WAIT_SOURCE.fullmatch(source) for source in argv[1:]]
    if len(argv) < 2 or not re.fullmatch(r"[0-9]+", argv[0]) or not all(sources) or (guarded and not command):
        print(WAIT_USAGE, file=sys.stderr)
        return 2
    deadline = time.monotonic() + int(argv[0])
    feeds = [(source["drive"], source["lane"]) for source in sources if source["drive"]]
    paths = [source for source in sources if source["file"]]
    files = [source for source in paths if not Mailbox.holds(Path(source["file"]))]
    mailboxes = [(Mailbox(Path(source["file"])), Path(source["cursor"])) for source in paths if Mailbox.holds(Path(source["file"]))]
    child = subprocess.Popen(command, start_new_session=True) if command else None
    while True:
        changed = False
        for drive, lane in feeds:
            unread = subprocess.run(["cci", "tail", "--drive", drive, "--cursor", lane, "--to", lane], capture_output=True, text=True, check=True).stdout
            for line in unread.splitlines():
                print(clip(line), flush=True)
                changed = True
        for mailbox, cursor in mailboxes:
            polled = mailbox.poll(seen_count(cursor))
            if polled is None:
                continue
            count, unread = polled
            cursor.write_text(f"{count}\n")
            if unread:
                print(f"MAILBOX {unread} unread", flush=True)
                changed = True
        for source in files:
            watched, cursor = Path(source["file"]), Path(source["cursor"])
            seen = seen_count(cursor)
            lines = Inbox(watched).lines() if watched.is_file() else []
            if len(lines) > seen:
                for line in lines[seen:]:
                    if line.text.split():
                        print(clip(line.text), flush=True)
                cursor.write_text(f"{len(lines)}\n")
                changed = True
        if not changed and child and child.poll() is not None:
            return child.returncode if child.returncode >= 0 else 128 - child.returncode
        remaining = deadline - time.monotonic()
        if not changed and remaining <= 0:
            print(f"QUIET {datetime.now(PACIFIC):%-I:%M %p}")
            changed = True
        if changed:
            if child:
                end(child)
            return 0
        pause(min(WAIT_SLICE, remaining), [mailbox for mailbox, _ in mailboxes], child)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["wait"]:
        return wait(argv[1:])
    parser = argparse.ArgumentParser(prog="inboxes.py", description=__doc__)
    verbs = parser.add_subparsers(dest="verb", required=True)
    summary = verbs.add_parser("digest")
    since = summary.add_mutually_exclusive_group(required=True)
    since.add_argument("--state", type=Path)
    since.add_argument("--all", action="store_true")
    summary.add_argument("--line-cap", type=int, default=DIGEST_CHARS)
    summary.add_argument("--budget", type=int, default=DIGEST_BYTES)
    summary.add_argument("files", nargs="+", type=Path)
    rotation = verbs.add_parser("rotate")
    rotation.add_argument("--hours", type=float, default=ROTATE_HOURS)
    rotation.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args(argv)
    if args.verb == "digest":
        sys.stdout.write(digest([path.expanduser() for path in args.files], args.state.expanduser() if args.state else None, args.line_cap, args.budget))
        return 0
    for path in args.files:
        Inbox(path.expanduser()).rotate(time.time(), args.hours)
    return 0


if __name__ == "__main__":
    sys.exit(main())
