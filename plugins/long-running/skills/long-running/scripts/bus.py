#!/usr/bin/env python3
"""The lane bus over one cc-notes log — every lane's decisions, heads, contracts, blockers, and asks, append-only.

    bus.py init    --title TEXT [--json]
    bus.py post    --bus ID --from LANE --kind decision|head|contract|blocker|ask|answer|withdraw --topic T --text ... [--to LANE]... [--re SEQ]
    bus.py read    --bus ID --lane LANE [--topic T]... [--kind K]... [--since SEQ] [--peek] [--all] [--json] [--cursor PATH]
    bus.py watch   --bus ID --lane LANE [--topic T]... [--kind K]... [--interval S] [--for S] [--cursor PATH]
    bus.py state   --bus ID [--lane LANE] [--topic T]
    bus.py summary --bus ID [--window-seconds N]

STDLIB ONLY. One log per drive, one entry per post, and a post is the whole record: a
message between lanes carries at most the entry's number. An entry is JSON in the log
entry's text; its sequence number is its position in ``ccn log entry list``, which never
moves because the log is append-only. Each lane's cursor is a file keyed on the bus and
the lane name, so a rotated or compacted lane resumes where its predecessor stopped.
``ccn log append`` refuses a contended ref, so every post holds a lock keyed on the bus
and retries the append the way a second writer outside this tool would need it to.
Every subprocess goes through :class:`Shell`, the one seam tests replace.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

KINDS = ("decision", "head", "contract", "blocker", "ask", "answer", "withdraw")
STATE_KINDS = ("head", "contract")
REPLY_KINDS = ("answer", "withdraw")
SUMMARY_LINES = 10
WINDOW_SECONDS = 3600
WATCH_INTERVAL = 30
WATCH_SECONDS = 1740
APPEND_ATTEMPTS = 5
CONTENDED = "contended"
SHA = re.compile(r"^[0-9a-f]{40}$")


class Shell:
    """The single subprocess boundary: every `ccn` call, and the clock a watch sleeps on, pass through here."""

    def run(self, argv: list[str], stdin: str | None = None) -> str:
        proc = subprocess.run(argv, input=stdin, capture_output=True, text=True, check=True)
        return proc.stdout

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def monotonic(self) -> float:
        return time.monotonic()


class BusUnreachable(RuntimeError):
    """cc-notes did not answer, so nothing was read or written."""


@dataclass
class Entry:
    seq: int
    at: str
    kind: str
    topic: str
    sender: str
    to: tuple[str, ...]
    text: str
    re: int | None

    @classmethod
    def parse(cls, seq: int, record: dict) -> Entry:
        body = json.loads(record["text"])
        return cls(seq, record["ts"], body["kind"], body["topic"], body["from"], tuple(body["to"]), body["text"], body.get("re"))

    def body(self) -> str:
        return json.dumps({"kind": self.kind, "topic": self.topic, "from": self.sender, "to": list(self.to), "text": self.text, "re": self.re})

    def as_json(self) -> dict:
        return {"seq": self.seq, "at": self.at, "kind": self.kind, "topic": self.topic, "from": self.sender, "to": list(self.to), "text": self.text, "re": self.re}


@dataclass
class Notes:
    shell: Shell
    bus: str
    repo: Path | None = None

    def ccn(self, *argv: str) -> str:
        prefix = ["ccn", "-R", str(self.repo)] if self.repo else ["ccn"]
        try:
            return self.shell.run([*prefix, *argv])
        except subprocess.CalledProcessError as failure:
            if CONTENDED in (failure.stderr or ""):
                raise
            raise BusUnreachable((failure.stderr or "").strip().splitlines()[-1] if failure.stderr else str(failure)) from failure

    def entries(self) -> list[Entry]:
        records = json.loads(self.ccn("log", "entry", "list", self.bus, "--json"))
        return [Entry.parse(seq, record) for seq, record in enumerate(records, start=1)]

    def append(self, entry: Entry, lock: Path) -> int:
        with locked(lock):
            attempt = 1
            while True:
                try:
                    payload = json.loads(self.ccn("log", "append", self.bus, entry.body(), "--json"))
                except subprocess.CalledProcessError as failure:
                    if attempt == APPEND_ATTEMPTS:
                        raise BusUnreachable(failure.stderr.strip().splitlines()[-1]) from failure
                    self.shell.sleep(0.2 * attempt)
                    attempt += 1
                    continue
                return payload["entry_count"]


def now() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@contextmanager
def locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("w")
    fcntl.flock(handle, fcntl.LOCK_EX)
    try:
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def default_lock(bus: str) -> Path:
    return Path.home() / ".cache" / "ccn-bus" / f"{bus}.lock"


def default_cursor(bus: str, lane: str) -> Path:
    return Path.home() / ".cache" / "ccn-bus" / bus / f"{lane}.cursor"


def read_cursor(path: Path) -> int:
    return int(path.read_text()) if path.exists() else 0


def write_cursor(path: Path, seq: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(seq))


def delivered(entry: Entry, lane: str, topics: frozenset[str], kinds: frozenset[str]) -> bool:
    if entry.sender == lane:
        return False
    if lane in entry.to:
        return True
    if entry.to:
        return False
    if not topics and not kinds:
        return True
    return entry.topic in topics or entry.kind in kinds


def replies(entries: list[Entry]) -> dict[str, dict[int, int]]:
    """For each reply kind, the entries it retired: withdrawn seq -> withdraw seq, asked seq -> answer seq."""
    index: dict[str, dict[int, int]] = {kind: {} for kind in REPLY_KINDS}
    for entry in entries:
        if entry.kind in REPLY_KINDS and entry.re is not None:
            index[entry.kind].setdefault(entry.re, entry.seq)
    return index


def clock(entry: Entry) -> str:
    return parse_iso(entry.at).strftime("%H:%MZ")


def render(entry: Entry, retired: dict[str, dict[int, int]]) -> str:
    to = f" -> {','.join(entry.to)}" if entry.to else ""
    re_ = f" re #{entry.re}" if entry.re is not None else ""
    line = f"#{entry.seq} {clock(entry)} {entry.kind} {entry.topic} {entry.sender}{to}{re_}: {entry.text}"
    if entry.seq in retired["withdraw"]:
        line += f" [WITHDRAWN #{retired['withdraw'][entry.seq]}]"
    if entry.kind == "ask" and entry.seq in retired["answer"]:
        line += f" [ANSWERED #{retired['answer'][entry.seq]}]"
    return line


def current_state(entries: list[Entry]) -> list[Entry]:
    """The latest live head and contract per lane and topic, in posting order."""
    withdrawn = replies(entries)["withdraw"]
    latest: dict[tuple[str, str, str], Entry] = {}
    for entry in entries:
        if entry.kind in STATE_KINDS and entry.seq not in withdrawn:
            latest[(entry.kind, entry.sender, entry.topic)] = entry
    return sorted(latest.values(), key=lambda entry: entry.seq)


def state_line(entry: Entry) -> str:
    if entry.kind == "head":
        return f"head {entry.topic} {entry.sender} {entry.text} #{entry.seq}"
    return f"contract {entry.topic} {entry.sender}: {entry.text} #{entry.seq}"


def open_entries(entries: list[Entry], kind: str) -> list[Entry]:
    retired = replies(entries)
    closed = set(retired["withdraw"]) | (set(retired["answer"]) if kind == "ask" else set())
    return [entry for entry in entries if entry.kind == kind and entry.seq not in closed]


def age(entry: Entry, moment: datetime) -> str:
    return f"{int((moment - parse_iso(entry.at)).total_seconds() // 60)}m"


def summary_lines(bus: str, entries: list[Entry], moment: datetime, window: timedelta) -> list[str]:
    cutoff = moment - window
    recent = [entry for entry in entries if parse_iso(entry.at) >= cutoff]
    asks = open_entries(entries, "ask")
    blockers = open_entries(entries, "blocker")
    state = current_state(entries)
    heads = [entry for entry in state if entry.kind == "head"]
    contracts = [entry for entry in state if entry.kind == "contract"]
    lines = [
        f"bus {bus[:8]} {moment.strftime('%Y-%m-%dT%H:%M:%SZ')} | entries {len(entries)} | last {int(window.total_seconds() // 60)}m {len(recent)} | open asks {len(asks)} | open blockers {len(blockers)} | heads {len(heads)} | contracts {len(contracts)}"
    ]
    lines += [f"ASK #{entry.seq} {entry.sender} -> {','.join(entry.to) or 'all'} ({entry.topic}, {age(entry, moment)}): {entry.text}" for entry in asks]
    lines += [f"BLOCKER #{entry.seq} {entry.sender} ({entry.topic}, {age(entry, moment)}): {entry.text}" for entry in blockers]
    decisions = [entry for entry in reversed(recent) if entry.kind == "decision"]
    lines += [f"decision #{entry.seq} {entry.sender} ({entry.topic}): {entry.text}" for entry in decisions]
    if len(lines) > SUMMARY_LINES:
        lines = lines[: SUMMARY_LINES - 1] + [f"... {len(lines) - SUMMARY_LINES + 1} more lines in bus.py read --all"]
    return lines


def notes(args: argparse.Namespace, shell: Shell) -> Notes:
    return Notes(shell, args.bus, args.repo)


def cmd_init(args: argparse.Namespace, shell: Shell) -> int:
    payload = json.loads(Notes(shell, "", args.repo).ccn("log", "add", args.title, "--json"))
    print(json.dumps(payload) if args.json else payload["id"])
    return 0


def reply_target(args: argparse.Namespace, entries: list[Entry]) -> Entry:
    if args.re is None:
        raise SystemExit(f"{args.kind} needs --re <seq>, the entry it {'answers' if args.kind == 'answer' else 'retracts'}")
    if not 1 <= args.re <= len(entries):
        raise SystemExit(f"no entry #{args.re} on bus {args.bus}")
    target = entries[args.re - 1]
    withdrawn = replies(entries)["withdraw"]
    if args.kind == "withdraw" and target.sender != args.sender:
        raise SystemExit(f"#{args.re} is {target.sender}'s; only its poster withdraws it")
    if args.kind == "withdraw" and target.seq in withdrawn:
        raise SystemExit(f"#{args.re} is already withdrawn by #{withdrawn[target.seq]}")
    if args.kind == "answer" and target.kind != "ask":
        raise SystemExit(f"#{args.re} is a {target.kind}, not an ask")
    return target


def cmd_post(args: argparse.Namespace, shell: Shell) -> int:
    store = notes(args, shell)
    to, topic = tuple(args.to), args.topic
    if args.kind == "head" and not SHA.match(args.text):
        raise SystemExit(f"a head is the full 40-hex sha, not {args.text!r}")
    if args.kind in REPLY_KINDS:
        target = reply_target(args, store.entries())
        topic = topic or target.topic
        to = to or ((target.sender,) if args.kind == "answer" else target.to)
    elif args.re is not None:
        raise SystemExit(f"--re is for answer and withdraw, not {args.kind}")
    elif not topic:
        raise SystemExit(f"{args.kind} needs --topic")
    entry = Entry(0, "", args.kind, topic, args.sender, to, args.text, args.re)
    seq = store.append(entry, args.lock or default_lock(args.bus))
    print(f"#{seq} {entry.kind} {entry.topic} {entry.sender}{' -> ' + ','.join(to) if to else ''}")
    return 0


def deliverable(args: argparse.Namespace, entries: list[Entry], since: int) -> list[Entry]:
    topics, kinds = frozenset(args.topic), frozenset(args.kind)
    return [entry for entry in entries if entry.seq > since and delivered(entry, args.lane, topics, kinds)]


def cmd_read(args: argparse.Namespace, shell: Shell) -> int:
    cursor = args.cursor or default_cursor(args.bus, args.lane)
    entries = notes(args, shell).entries()
    since = 0 if args.all else (args.since if args.since is not None else read_cursor(cursor))
    delivered_entries = deliverable(args, entries, since)
    if args.json:
        print(json.dumps([entry.as_json() for entry in delivered_entries]))
    elif delivered_entries:
        retired = replies(entries)
        print("\n".join(render(entry, retired) for entry in delivered_entries))
    else:
        print(f"nothing new since #{since}")
    if entries and not args.peek:
        write_cursor(cursor, entries[-1].seq)
    return 0


def cmd_watch(args: argparse.Namespace, shell: Shell) -> int:
    cursor = args.cursor or default_cursor(args.bus, args.lane)
    store = notes(args, shell)
    deadline = shell.monotonic() + args.seconds
    while True:
        try:
            entries = store.entries()
        except BusUnreachable as unreachable:
            print(f"bus unreachable: {unreachable}", flush=True)
            return 1
        fresh = deliverable(args, entries, read_cursor(cursor))
        if fresh:
            retired = replies(entries)
            print("\n".join(render(entry, retired) for entry in fresh), flush=True)
        if entries:
            write_cursor(cursor, entries[-1].seq)
        if shell.monotonic() >= deadline:
            return 0
        shell.sleep(args.interval)


def cmd_state(args: argparse.Namespace, shell: Shell) -> int:
    state = current_state(notes(args, shell).entries())
    if args.lane:
        state = [entry for entry in state if entry.sender == args.lane]
    if args.topic:
        state = [entry for entry in state if entry.topic == args.topic]
    print("\n".join(state_line(entry) for entry in state) if state else "no live heads or contracts")
    return 0


def cmd_summary(args: argparse.Namespace, shell: Shell) -> int:
    print("\n".join(summary_lines(args.bus, notes(args, shell).entries(), now(), timedelta(seconds=args.window_seconds))))
    return 0


def add_bus(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--bus", required=True, help="the log id from bus.py init")
    parser.add_argument("--repo", type=Path, help="any path inside the repo whose cc-notes refs hold the bus; default cwd")


def add_subscription(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--lane", required=True, help="the reading lane: its own posts are skipped, posts addressed to it always pass")
    parser.add_argument("--topic", action="append", default=[], metavar="T", help="deliver broadcast entries on this topic (repeatable)")
    parser.add_argument("--kind", action="append", default=[], choices=KINDS, help="deliver broadcast entries of this kind (repeatable)")
    parser.add_argument("--cursor", type=Path, help="cursor file; default ~/.cache/ccn-bus/<bus>/<lane>.cursor")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bus.py", description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="create the drive's bus and print its id")
    init.add_argument("--title", required=True)
    init.add_argument("--repo", type=Path)
    init.add_argument("--json", action="store_true")
    init.set_defaults(handler=cmd_init)

    post = subparsers.add_parser("post", help="append one entry; the printed #seq is the only thing a message need carry")
    add_bus(post)
    post.add_argument("--from", dest="sender", required=True, metavar="LANE")
    post.add_argument("--kind", required=True, choices=KINDS)
    post.add_argument("--topic", help="a PR number, branch prefix, contract name, or area other lanes subscribe to; a reply inherits its target's")
    post.add_argument("--text", required=True)
    post.add_argument("--to", action="append", default=[], metavar="LANE", help="address the entry; unaddressed entries are broadcast; an answer goes to the asker")
    post.add_argument("--re", type=int, metavar="SEQ", help="the ask an answer closes, or the entry a withdraw retracts")
    post.add_argument("--lock", type=Path, help="append lock; default ~/.cache/ccn-bus/<bus>.lock")
    post.set_defaults(handler=cmd_post)

    read = subparsers.add_parser("read", help="entries since the lane's cursor that its subscription delivers, then advance the cursor")
    add_bus(read)
    add_subscription(read)
    read.add_argument("--since", type=int, metavar="SEQ", help="read after this entry instead of the cursor")
    read.add_argument("--all", action="store_true", help="read from the first entry")
    read.add_argument("--peek", action="store_true", help="leave the cursor where it is")
    read.add_argument("--json", action="store_true")
    read.set_defaults(handler=cmd_read)

    watch = subparsers.add_parser("watch", help="for a Monitor: print each newly delivered entry as it lands, nothing otherwise, and exit after --for seconds")
    add_bus(watch)
    add_subscription(watch)
    watch.add_argument("--interval", type=float, default=WATCH_INTERVAL, metavar="S")
    watch.add_argument("--for", dest="seconds", type=float, default=WATCH_SECONDS, metavar="S")
    watch.set_defaults(handler=cmd_watch)

    state = subparsers.add_parser("state", help="the live head and contract per lane and topic, read instead of asked")
    add_bus(state)
    state.add_argument("--lane", help="only this lane's")
    state.add_argument("--topic", help="only this topic's")
    state.set_defaults(handler=cmd_state)

    summary = subparsers.add_parser("summary", help="the root's view: counts, every open ask and blocker, the latest decisions, at most ten lines")
    add_bus(summary)
    summary.add_argument("--window-seconds", type=int, default=WINDOW_SECONDS)
    summary.set_defaults(handler=cmd_summary)

    return parser


def main(argv: list[str] | None = None, shell: Shell | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args, shell or Shell())
    except BusUnreachable as unreachable:
        print(f"bus unreachable, wrote nothing: {unreachable}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
