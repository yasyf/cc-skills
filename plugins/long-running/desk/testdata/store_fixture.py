from __future__ import annotations

import argparse
import difflib
import json
import os
import sqlite3
import stat
import struct
import sys
import tempfile
from contextlib import ExitStack
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from long_running import actions
from long_running.book import Book

RUNNER = "desk-runner"
LANDING = "desk-landing"
LANE = "lane-ñandú"
UNREACHABLE = "orca-unreachable"
DEFERRED = "deferred-escalations"
START = datetime(2026, 11, 2, 8, 0, 0, tzinfo=timezone.utc)
PACIFIC = timezone(timedelta(hours=-8))
MOUNTAIN = timezone(timedelta(hours=-7))
NUMBERS = {
    "int": 3,
    "big": 2**70,
    "negative": -12,
    "float": 1.5,
    "tiny": 1e-07,
    "huge": 1e22,
    "neg0": -0.0,
    "inf": float("inf"),
    "list": [1, "two", None, True, False, {"k": "v", "a": []}],
    "nested": {"deep": {"deeper": "ünïcode 🚀 <>& \u2028 \"q\" \\ /"}},
    "control": "tab\there\nnewline\r\x01\x7f\x1f",
    "lone": "\ud83d",
    "empty": {},
    "emptylist": [],
}


class Clock:
    def __init__(self) -> None:
        self.at = START

    def now(self) -> datetime:
        return self.at


def record(book: Book, action_id: str, line: str) -> list:
    action, created = book.accept(RUNNER, action_id, "escalation", line, "runner", None)
    return [asdict(action), created]


def hold(incident, key: str) -> None:
    held = incident.facts.setdefault(DEFERRED, {"incident": "lane-a, lane-b", "ids": []})
    if key not in held["ids"]:
        held["ids"].append(key)


def release_deferred(book: Book, clock: Clock) -> list:
    held = book.fact(RUNNER, DEFERRED, None)
    lines = [book.action(RUNNER, key).target for key in held["ids"]]

    def settle(incident) -> None:
        for key in held["ids"]:
            if incident.action(key).status == "accepted":
                incident.start(key, clock.now())
                incident.complete(key, {"at": book.stamp(), "deferred": True})
        incident.facts.pop(DEFERRED)

    book.edit(RUNNER, settle)
    return record(book, f"escalation:deferred:{held['ids'][0]}", f"DEFERRED {len(lines)} escalations held during {held['incident']}: {' | '.join(lines)}")


def accept_policy(book: Book, key: str, rule: str, revision: str, source: str, supersedes: str) -> list:
    book.ensure(LANDING, LANDING)
    verified = book.actions(LANDING, kind="policy", status="verified")
    superseded = {json.loads(action.target)["supersedes"] for action in verified}
    current = next((action for action in verified if json.loads(action.target)["revision"] not in superseded), None)
    target = json.dumps({"rule": rule, "revision": revision, "supersedes": supersedes})
    action, created = book.accept(LANDING, key, "policy", target, source, None)
    if not created:
        return [asdict(action), created]
    if current and supersedes != json.loads(current.target)["revision"]:
        held = json.loads(current.target)
        reason = f"names {supersedes or 'no'} predecessor; the accepted landing policy is {held['rule']} at {held['revision']} ({current.authority_ref})"
        book.attempt(LANDING, lambda incident: incident.fail(key, reason))
        record(book, f"escalation:{key}", f"STALE-POLICY {key} landing: {rule} at {revision} from {source} rejected: {reason}")
    else:
        book.attempt(LANDING, lambda incident: incident.verify(key, {"at": book.stamp(), "supersedes": supersedes}))
    return [asdict(book.action(LANDING, key)), created]


def scenario(root: Path) -> list:
    clock = Clock()
    book = Book(actions.Store(root), clock)
    seen = []
    book.ensure(RUNNER, RUNNER)
    clock.at = datetime(2026, 11, 2, 0, 0, 1, tzinfo=PACIFIC)
    book.ensure(RUNNER, "someone-else")
    book.ensure(LANDING, LANDING)
    clock.at = START + timedelta(seconds=2, microseconds=999999)
    book.ensure(LANE, "dispatch-ü")
    seen.append([book.exists(RUNNER), book.exists("desk-missing"), book.known(RUNNER, "escalation:none") is None, book.known("desk-missing", "x") is None])

    clock.at = START + timedelta(minutes=1)
    streak = book.edit(RUNNER, lambda incident: dict(incident.facts.setdefault(UNREACHABLE, {"since": book.stamp(), "error": "Orca gave no answer — curl: (7) ñ 🚀"})))
    clock.at = START + timedelta(minutes=1, seconds=30)
    again = book.edit(RUNNER, lambda incident: dict(incident.facts.setdefault(UNREACHABLE, {"since": book.stamp(), "error": "later"})))
    seen.append([streak, again, book.facts(RUNNER, [UNREACHABLE, "absent"])])

    escalation = f"escalation:orca:{streak['since']}"
    line = f"ORCA-UNREACHABLE orca:{streak['since']} runner: {streak['error']}; since 12:01 AM PST the runner reads no dispatch as gone"
    seen.append(record(book, escalation, line))
    seen.append(record(book, escalation, "ignored"))

    clock.at = START + timedelta(minutes=2)
    posted = book.attempt(RUNNER, lambda incident: (incident.start(escalation, clock.now()), incident.complete(escalation, {"at": book.stamp(), "posted": {"seq": 4217, "drive": "drive-ü", "ratio": 0.25}})))
    restarted = book.attempt(RUNNER, lambda incident: incident.start(escalation, clock.now()))
    unknown = book.attempt(RUNNER, lambda incident: incident.action("escalation:missing"))
    rolled_back = book.attempt(RUNNER, lambda incident: (incident.facts.__setitem__("rolled-back", True), incident.start(escalation, clock.now())))
    seen.append([posted, restarted, unknown, rolled_back, book.facts(RUNNER, ["rolled-back"])])

    refused = "escalation:refused:lane-a"
    seen.append(record(book, refused, "POSTS-REFUSED refused:lane-a runner: 4 cci posts refused in the last minute, the newest to lane-a: exit 2"))
    seen.append(book.attempt(RUNNER, lambda incident: (incident.start(refused, clock.now()), incident.complete(refused, {"at": book.stamp(), "posted": {"seq": 4218}, "to": "lane-a"}))))

    clock.at = START + timedelta(minutes=3)
    deadline_key = "escalation:deadline:lane-b"
    stale_key = "escalation:stale-mail:lane-c"
    seen.append(record(book, deadline_key, "DEADLINE launch:lane-b lane-b: no ack in 10 min on #1234 and #99"))
    seen.append(record(book, stale_key, "STALE-MAIL mail:lane-c lane-c: 3 unread"))
    for key in (deadline_key, stale_key, deadline_key):
        book.edit(RUNNER, lambda incident, key=key: hold(incident, key))
    seen.append(book.fact(RUNNER, DEFERRED, None))
    seen.append(release_deferred(book, clock))
    seen.append([book.fact(RUNNER, DEFERRED, None), book.fact(RUNNER, DEFERRED, "absent")])

    seen.append(accept_policy(book, "policy:r1", "ready-prefix", "r1", "owner", ""))
    seen.append(accept_policy(book, "policy:r3", "ready-prefix", "r3", "owner-ü", "r2"))
    seen.append(accept_policy(book, "policy:r1", "ready-prefix", "r1", "owner", ""))

    clock.at = START + timedelta(minutes=4)
    launch_key = "launch:lane-ñandú:1"
    launch_target = json.dumps({"model": "opus", "effort": "xhigh", "brief": "/briefs/ñandú.md", "prior": None, "urgent": False, "fast": False, "role": "", "incident": ""})
    action, created = book.accept(LANE, launch_key, "launch", launch_target, launch_key, datetime(2026, 11, 2, 1, 30, 0, tzinfo=MOUNTAIN))
    seen.append([asdict(action), created])
    book.accept(LANE, "reply:ü", "reply", json.dumps({"reply_to": "thread-ü"}), "auth-ü", None)
    seen.append(book.attempt(LANE, lambda incident: (incident.start("reply:ü", clock.now(), START + timedelta(minutes=9), "dispatch-1"), incident.complete("reply:ü", {"z": 1, "a": [1.25, None, 1e-07, 1e22]}))))
    seen.append(book.attempt(LANE, lambda incident: incident.verify("reply:ü", {"receipt": "ok"})))
    seen.append(book.attempt(LANE, lambda incident: incident.fail("reply:ü", "late")))
    book.edit(LANE, lambda incident: incident.facts.__setitem__("numbers", NUMBERS))
    book.edit(LANE, lambda incident: incident.facts.__setitem__("path/with~tilde", "escaped"))
    seen.append(book.facts(LANE))
    book.edit(LANE, lambda incident: incident.facts.__setitem__("numbers", {"list": [3, 2, 1], "int": 4, "nested": "flattened", "empty": {"now": "filled"}}))
    book.edit(LANE, lambda incident: incident.facts["numbers"]["list"].__setitem__(0, 99))
    seen.append([book.edit(LANE, lambda incident: incident.facts.pop("path/with~tilde")), book.edit(LANE, lambda incident: incident.facts.pop("missing", "fallback"))])
    book.edit(RUNNER, lambda incident: incident.action(escalation).response["posted"].__setitem__("acked", True))
    seen.append(book.facts(LANE))
    seen.append([asdict(action) for action in book.actions(RUNNER, kind="escalation")])
    seen.append([action.action_id for action in book.actions(RUNNER, status="completed")])
    seen.append([action.action_id for action in book.actions(RUNNER, kind="escalation", statuses=("accepted", "completed"))])
    seen.append([action.action_id for action in book.actions(RUNNER, kind="escalation", statuses=())])
    seen.append(asdict(book.action(LANE, "reply:ü")))
    return seen


def snapshot(root: Path) -> list[str]:
    names = sorted(name for name in os.listdir(root) if not name.endswith(("-wal", "-shm")))
    lines = [f"files {names}"]
    lines.extend(f"mode {name} {oct(stat.S_IMODE((root / name).stat().st_mode))}" for name in names)
    store = actions.Store(root)
    for incident_id in store.ids():
        path = store.path(incident_id)
        lines.append(f"load {incident_id} {json.dumps(store.load(incident_id).dump())}")
        connection = sqlite3.connect(path.absolute().as_uri() + "?mode=ro", uri=True)
        try:
            for pragma in ("user_version", "journal_mode", "page_size", "encoding", "auto_vacuum"):
                lines.append(f"pragma {incident_id} {pragma} {connection.execute(f'PRAGMA {pragma}').fetchone()[0]}")
            for row in connection.execute("SELECT type, name, tbl_name, rootpage, sql FROM sqlite_schema ORDER BY name"):
                lines.append(f"schema {incident_id} {row!r}")
            tables = [name for (name,) in connection.execute("SELECT name FROM sqlite_schema WHERE type = 'table' ORDER BY name")]
            for table in tables:
                columns = [column[1] for column in connection.execute(f'PRAGMA table_info("{table}")')]
                order = ", ".join(str(index) for index in range(1, len(columns) + 1))
                for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY {order}'):
                    cells = [("blob", len(value)) if (table, column) == ("release_action_clock", "epoch") else (type(value).__name__, value) for column, value in zip(columns, row)]
                    lines.append(f"row {incident_id} {table} {cells!r}")
        finally:
            connection.close()
    return lines


def check(target: Path) -> int:
    with tempfile.TemporaryDirectory() as scratch:
        reference = Path(scratch) / "store"
        scenario(reference)
        expected, actual = snapshot(reference), snapshot(target)
    if expected == actual:
        return 0
    sys.stdout.writelines(difflib.unified_diff([line + "\n" for line in expected], [line + "\n" for line in actual], "python", "go"))
    return 1


def serve(root: Path) -> int:
    store = actions.Store(root)
    book = Book(store, Clock())
    held = ExitStack()
    raw = None
    with store.reading():
        for line in sys.stdin:
            command, *arguments = json.loads(line)
            if command == "hold":
                container, key, value = arguments
                held.enter_context(store.transaction(container)).facts[key] = value
                reply = "held"
            elif command == "commit":
                held.close()
                reply = "committed"
            elif command == "holdraw":
                raw = sqlite3.connect(store.path(arguments[0]), isolation_level=None)
                raw.execute("BEGIN IMMEDIATE")
                reply = "held"
            elif command == "commitraw":
                raw.execute("COMMIT")
                raw.close()
                reply = "committed"
            elif command == "read":
                reply = {"facts": book.facts(arguments[0]), "actions": [[action.action_id, action.status] for action in book.actions(arguments[0])]}
            else:
                return 0
            print(json.dumps(reply), flush=True)
    return 0


def double(bits: str) -> float:
    return struct.unpack("<d", int(bits).to_bytes(8, "little"))[0]


def oracle() -> int:
    request = json.load(sys.stdin)
    texts = []
    for text in request["texts"]:
        try:
            value = json.loads(text)
        except ValueError as error:
            texts.append({"error": str(error)})
        else:
            texts.append({"default": json.dumps(value), "compact": json.dumps(value, separators=(",", ":"))})
    parses = []
    for text in request["parses"]:
        try:
            parsed = actions.parse_stamp(text)
        except ValueError as error:
            parses.append({"error": str(error)})
        else:
            parses.append({"utc": parsed.astimezone(timezone.utc).isoformat(), "offset": int(parsed.utcoffset().total_seconds())})
    json.dump(
        {
            "texts": texts,
            "floats": [json.dumps(double(bits)) for bits in request["floats"]],
            "reprs": [repr(double(bits)) for bits in request["floats"]],
            "stamps": [actions.stamp(datetime(*fields[:7], tzinfo=timezone(timedelta(seconds=fields[7])))) for fields in request["stamps"]],
            "parses": parses,
        },
        sys.stdout,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Python's side of the Go store's round-trip tests")
    parser.add_argument("command", choices=("write", "check", "serve", "oracle"))
    parser.add_argument("root", nargs="?", type=Path)
    args = parser.parse_args()
    if args.command == "write":
        print(json.dumps(scenario(args.root)))
        return 0
    if args.command == "check":
        return check(args.root)
    if args.command == "serve":
        return serve(args.root)
    return oracle()


if __name__ == "__main__":
    raise SystemExit(main())
