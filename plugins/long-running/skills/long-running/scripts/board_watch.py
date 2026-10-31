#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
"""The board recorder: one reader per cc-present board that keeps every owner interaction as a capture.

    board_watch.py start --session ID (--program SLUG | --drive ID) [--state-dir DIR]
    board_watch.py backfill --session ID (--program SLUG | --drive ID)

STDLIB ONLY. `start` runs `cc-present watch --session ID` in the foreground until the board
closes; the `start_board_watch` hook launches it detached, once per board session. Each event
line is appended verbatim to `<state dir>/boards/<session>-events.jsonl`. Each owner interaction
is appended to `<state dir>/owner-prompts.jsonl` as one capture line,
`{"digest", "at", "session", "source": "board", "block", "text"}`, where `digest` is the first 16
hex digits of sha256 over `session`, `text`, `block` and the event's line number in the events
file joined by NUL and `at` is the arrival time in UTC. A triage row's note also carries the row's
`row` id and `verdict`, and its digest takes the row id in place of the block:

    write-in   choice.selected with `other`, and input.submitted: the text the owner typed
    note       feedback.created, the note on a decision.created, and each row note a triage.decided carries
    pick       choice.selected option ids, as the option labels in the board's current document
    verdict    decision.created: an approval's exact prompt, marked `"verdict": "approved"`, or `rejected` or `cleared`

A write-in a later pick replaces survives as its own capture, and every tap is its own capture,
so a burst that returns to an earlier pick keeps all its taps and the last one stays last. A pick or approval whose block is missing from the board's document gets no
capture, so no option id or bare verdict stands in for the owner's words; its event line stays. The state directory resolves as `rulings.py` resolves it; `--state-dir`
overrides it. A second watch of the same board exits at once.

Each decision.created, choice.selected, input.submitted, feedback.created, triage.decided and
submit also reaches the drive's bus the moment it arrives, so no lane waits on a root to relay a
tap: one `cci post --kind owner --to all` from lane `board-watch` names the card title and block
id, the verdict, picked option labels or the owner's text verbatim, each merge row's head, and the
time, and resolves every open `ask`, `blocker` or `hold` whose text names the block, its card, or
the merge row (`merge-row-<pr>` or `merge:<pr>`). Its idempotency key is the event's line number
in the events file plus a hash of the line, so a restart that resumes from the watch's cursor
never posts an event twice. Each record carries attributes a reader takes without parsing the
text: `event`, `session` and `block`; a decision's `verdict`; a pick's `picked` option ids and
`other` for a write-in; and a triage.decided posts one record per row, with that row's `row`,
`verdict` and `head`, keyed by the row as well.

Each pick and write-in on the drive root's newest board, a choice.selected with option ids or
`other` and an input.submitted with text, also asks root for a lane at once, unless it answers an
open ask a lane other than root posted, since that lane already waits on it: one `ccn task add`
on the backlog of the drive's checkout, labelled `program:<program>` and `needs-lane`, and one
`cci post --kind ask --to root` from `board-watch` on topic `needs-lane:<block>`, keyed by the
owner record's key, naming the card, the block, the owner's words and the task. The stall watch
raises such an ask no spawn or claim names at 15 minutes.

While the board is open, the watch of the drive root's newest session, read from the drive
registry, posts the board's document as one `note` record from `board-watch` on topic
`board-rows`, attribute `board`: each PR row's PR, head and write time, each merge card's PR and
head, and each card's choice blocks and PRs. It reads the document every minute and posts when it
changed or six hours after its last post, so a reader on another machine knows what the board
shows. Before each read it re-executes into a newer installed plugin version, as the desk runners
do, so it never posts a snapshot older than the runner that reads it; `cc-present watch` resumes
from its persisted cursor, so no event is lost or recorded twice. It also applies the landing runner's board writes, the `desk-runner` records on topic
`board-write` whose `session` attribute names this board, read in order from a cci cursor:
`op=carry` runs `cc-present verdict carry` for its block, row, head and note, re-upserting a block
that belongs to a closed round into the current round and carrying again; `op=ask` puts the card
its `card` attribute holds on the board with `cc-present ask`. Each write is answered once, by an
`applied` record, or a `defect` record to the runner and the root naming what cc-present said.

`backfill` posts one `owner` record, to `desk-runner`, for each verdict and pick `cc-present
outcomes --all` holds for the session that no record carries the same way, at the time the
owner made it, then reads the records again and exits 1, naming each, while any verdict or pick
still differs: the landing runner reads taps only from records once this prints `verified`.
"""

from __future__ import annotations

import argparse
import fcntl
import os
import hashlib
import json
import re
import signal
import subprocess
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from long_running import board_rows
from long_running import cci
from long_running import drive
from long_running import rulings

SOURCE = "board"
CAPTURES = "owner-prompts.jsonl"
CREDENTIAL = re.compile(
    r"\b(?:sk|pk|rk)-[\w-]{16,}"
    r"|\b(?:gh[pousr]|github_pat)_[\w.-]{20,}"
    r"|\bxox[abprs]-[\w-]{10,}"
    r"|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"
    r"|\b(?:api|sdk|mob)-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b"
    r"|\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}"
    r"|(?<![\w/+=.-])(?=[\w+/=]*[a-z])(?=[\w+/=]*[A-Z])(?=[\w+/=]*\d)[\w+/=]{32,}(?=[.,;:!?)\]\"'`]*(?:\s|$))"
)
REDACTED = "[credential redacted]"
BOARDS = "boards"
OWNER_EVENTS = frozenset({"decision.created", "choice.selected", "input.submitted", "feedback.created", "triage.decided", "submit"})
OPEN_KINDS = ("ask", "blocker", "hold")
BUS_LANE = board_rows.OWNER_LANE
RUNNER = board_rows.RUNNER_LANE
GREP_BYTES = 200000
SNAPSHOT_SECONDS = 60
SNAPSHOT_FRESH = timedelta(hours=6)
CLOSED_ROUND = re.compile(r"belongs to closed round")
UNKNOWN_BLOCK = re.compile(r'\bunknown block "')
WATCH_BYTES = 10_000_000
LANE_TOPIC = "needs-lane"
ROOT_LANES = frozenset({"root", "main", BUS_LANE, RUNNER})
WORDS_CHARS = 300
CCN_SECONDS = 60


@dataclass
class Bus:
    drive: str
    lines: int


class Shell:
    """The single side-effect boundary: the board's event stream, its document and outcomes, cc-present writes, cci, the drive registry, threads, and the clock."""

    def stale(self) -> bool:
        return drive.behind(drive.plugin_manifest()["version"], drive.installed_version())

    def reexec(self) -> None:
        os.environ.pop(drive.CALLER_VERSION_ENV, None)
        drive.run_installed(__file__)

    def end_inherited_children(self) -> None:
        for pid in subprocess.run(["pgrep", "-P", str(os.getpid())], capture_output=True, text=True).stdout.split():
            os.kill(int(pid), signal.SIGTERM)

    def watch(self, session: str) -> Iterator[str]:
        with subprocess.Popen(["cc-present", "watch", "--session", session], stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True) as child:
            yield from child.stdout

    def document(self, session: str, block: str) -> dict | None:
        argv = ["cc-present", "outcomes", "--session", session, "--block", block]
        done = subprocess.run(argv, capture_output=True, text=True)
        return json.loads(done.stdout).get("doc") if done.returncode == 0 else None

    def outcomes(self, session: str) -> dict | None:
        done = subprocess.run(["cc-present", "outcomes", "--all", "--session", session], capture_output=True, text=True)
        start = done.stdout.find("{")
        return json.loads(done.stdout[start:]) if done.returncode == 0 and start >= 0 else None

    def present(self, argv: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(argv, input=stdin, capture_output=True, text=True)

    def writes(self, drive_name: str, cursor: str) -> Iterator[str]:
        argv = ["cci", "watch", "--drive", drive_name, "--cursor", cursor, "--lane", RUNNER, "--topic", board_rows.WRITE_TOPIC, "--json", "--budget", str(WATCH_BYTES)]
        with subprocess.Popen(argv, stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True) as child:
            yield from child.stdout

    def spawn(self, target, *args) -> None:
        threading.Thread(target=target, args=args, name=target.__name__, daemon=True).start()

    def root_session(self, drive_name: str) -> str | None:
        entry = next((entry for entry in drive.drives() if drive.cci_drive(entry) == drive_name and entry["sessions"]), None)
        return entry["sessions"][-1] if entry else None

    def entry(self, drive_name: str) -> dict | None:
        return next((entry for entry in drive.drives() if drive.cci_drive(entry) == drive_name), None)

    def ccn(self, checkout: str, argv: list[str]) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(["ccn", "-R", checkout, *argv], capture_output=True, text=True, timeout=CCN_SECONDS)
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(argv, 124, "", f"ccn ran past {CCN_SECONDS}s")

    def cci(self, argv: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(["cci", *argv], capture_output=True, text=True)

    def now(self) -> datetime:
        return datetime.now(UTC)


def stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def find(value: object, block: str) -> dict | None:
    match value:
        case {"id": found} if found == block and "type" in value:
            return value
        case dict():
            return next((hit for item in value.values() if (hit := find(item, block))), None)
        case list():
            return next((hit for item in value if (hit := find(item, block))), None)
    return None


def digest(session: str, text: str, block: str, line: int) -> str:
    return hashlib.sha256("\0".join([session, text, block, str(line)]).encode()).hexdigest()[:16]


def picked(found: dict | None, ids: list[str]) -> str | None:
    labels = {option.get("id"): option.get("label") for option in (found or {}).get("options") or []}
    return "; ".join(labels[option] for option in ids) if all(labels.get(option) for option in ids) else None


def unread(session: str, block: str) -> None:
    print(f"board-watch {session}: {block} is not in the board's document, so its event stays only in the events file", flush=True)


def rendered(event: dict, session: str, doc: dict | None) -> str | None:
    block = event.get("blockId", "")
    match event:
        case {"type": "choice.selected", "optionIds": [_, *_] as ids}:
            text = picked(find(doc, block), ids)
        case {"type": "decision.created", "verdict": "approved"}:
            text = (find(doc, block) or {}).get("prompt")
        case {"type": "decision.created", "verdict": str() as verdict}:
            return verdict
        case _:
            return None
    if text is None:
        unread(session, block)
    return text


def texts(event: dict, session: str, doc: dict | None) -> list[str]:
    match event.get("type"):
        case "choice.selected":
            written = [event["other"]] if event.get("other") else []
        case "input.submitted" | "feedback.created":
            written = [event["text"]] if event.get("text") else []
        case "decision.created":
            written = [event["note"]] if event.get("note") else []
        case _:
            written = []
    verdict = rendered(event, session, doc)
    return [*([verdict] if verdict else []), *written] if event.get("type") == "decision.created" else [*written, *([verdict] if verdict else [])]


def captures(event: dict, session: str, doc: dict | None, moment: datetime, line: int) -> list[dict]:
    block = event.get("blockId", "")
    if event.get("type") == "triage.decided":
        return [
            {"digest": digest(session, row["note"], row_id, line), "at": stamp(moment), "session": session, "source": SOURCE, "block": block, "row": row_id, "verdict": row["verdict"], "text": CREDENTIAL.sub(REDACTED, row["note"])}
            for row_id, row in event["verdicts"].items()
            if row.get("note")
        ]
    said = texts(event, session, doc)
    approval = event.get("type") == "decision.created" and event.get("verdict") == "approved" and bool(said) and said[0] == (find(doc, block) or {}).get("prompt")
    return [
        {"digest": digest(session, text, block, line), "at": stamp(moment), "session": session, "source": SOURCE, "block": block, "text": CREDENTIAL.sub(REDACTED, text)} | ({"verdict": "approved"} if approval and index == 0 else {})
        for index, text in enumerate(said)
    ]


def card_of(doc: dict | None, block: str) -> dict | None:
    return next((top for top in (doc or {}).get("blocks", []) if find(top, block)), None)


def quoted(text: str) -> str:
    return f"\u201c{text}\u201d"


def said(verb: str, text: str | None) -> list[str]:
    return [verb, quoted(text)] if text else []


def told(event: dict, doc: dict | None) -> str:
    found = find(doc, event.get("blockId", ""))
    match event:
        case {"type": "triage.decided", "verdicts": verdicts}:
            heads = {row.id: row.head for row in board_rows.rows_of(doc or {})}
            return "; ".join(
                " ".join([row, entry["verdict"], *(["at", head] if (head := entry.get("head") or heads.get(row)) else []), *said("noted", entry.get("note"))])
                for row, entry in verdicts.items()
            )
        case {"type": "submit"}:
            return f"closed round {event.get('revision', 0)}"
        case {"type": "decision.created", "verdict": verdict}:
            prompt = (found or {}).get("prompt") if verdict == "approved" else None
            words = [verdict, *([quoted(prompt)] if prompt else []), *said("noted", event.get("note"))]
        case {"type": "choice.selected"}:
            ids = event.get("optionIds") or []
            words = [*(["picked", quoted(labels) if (labels := picked(found, ids)) else "option " + ", ".join(ids)] if ids else []), *said("wrote", event.get("other"))]
        case {"type": "input.submitted"}:
            words = said("wrote", event.get("text"))
        case _:
            words = said("noted", event.get("text"))
    return " ".join(words)


def place(doc: dict | None, block: str) -> str:
    title = (card_of(doc, block) or {}).get("title")
    return f"{title} ({block})" if title else block or "the board"


def announced(event: dict, session: str, doc: dict | None) -> str:
    return CREDENTIAL.sub(REDACTED, f"Owner on board {session[:8]}, {place(doc, event.get('blockId', ''))}: {told(event, doc)}, at {{at}}")


def row_names(row: str) -> list[str]:
    pr = board_rows.pr_of(row)
    return [*([] if row.isdigit() else [row]), *([f"merge-row-{pr}", f"merge:{pr}"] if pr else [])]


def named(event: dict, doc: dict | None) -> list[str]:
    if event["type"] == "triage.decided":
        return list(dict.fromkeys(name for row in event["verdicts"] for name in row_names(row)))
    block = event.get("blockId", "")
    return list(dict.fromkeys(name for name in (block, (card_of(doc, block) or {}).get("id")) if name))


def open_asks(shell: Shell, bus: Bus, names: list[str]) -> list[dict]:
    asks: dict[int, dict] = {}
    for name in names:
        pattern = re.compile(rf"(?<![\w-]){re.escape(name)}(?![\w-])", re.IGNORECASE)
        done = shell.cci(["grep", name, "--fixed-strings", "--drive", bus.drive, "--json", "--budget", str(GREP_BYTES), *(f"--kind={kind}" for kind in OPEN_KINDS)])
        found = [json.loads(line) for line in done.stdout.splitlines() if line.strip()]
        asks |= {record["seq"]: record for record in found if record["kind"] in OPEN_KINDS and record["drive"] == bus.drive and record.get("status") == "open" and pattern.search(record["text"])}
    return [asks[seq] for seq in sorted(asks)]


def announce(event: dict, line: str, session: str, doc: dict | None, shell: Shell, bus: Bus) -> None:
    key = f"board:{session}:{bus.lines}:{hashlib.sha256(line.encode()).hexdigest()[:8]}"
    block = event.get("blockId", "")
    fields = {"event": event["type"], "session": session, "block": block}
    if event["type"] == "triage.decided":
        heads = {row.id: row.head for row in board_rows.rows_of(doc or {})}
        for row, entry in event["verdicts"].items():
            single = dict(event, verdicts={row: entry})
            pr = board_rows.pr_of(row)
            post_owner(shell, bus, f"{key}:{row}", announced(single, session, doc), block, named(single, doc), [pr] if pr else [], fields | {"row": row, "verdict": entry["verdict"], "head": entry.get("head") or heads.get(row) or ""})
        return
    if event["type"] == "decision.created":
        fields["verdict"] = event.get("verdict", "")
    if event["type"] == "choice.selected":
        fields |= {"picked": ",".join(event.get("optionIds") or []), "other": "1" if event.get("other") else ""}
    answered = post_owner(shell, bus, key, announced(event, session, doc), block, named(event, doc), [], fields)
    if wants_lane(event) and shell.root_session(bus.drive) in (None, session) and all(record["lane"] in ROOT_LANES for record in answered):
        ask_lane(event, key, session, doc, shell, bus)


def wants_lane(event: dict) -> bool:
    match event:
        case {"type": "choice.selected"}:
            return bool(event.get("optionIds") or event.get("other"))
        case {"type": "input.submitted"}:
            return bool(event.get("text"))
    return False


def ask_lane(event: dict, key: str, session: str, doc: dict | None, shell: Shell, bus: Bus) -> None:
    block = event.get("blockId", "")
    where = place(doc, block)
    words = told(event, doc)
    clipped = words if len(words) <= WORDS_CHARS else f"{words[: WORDS_CHARS - 1]}\u2026"
    task = add_task(shell, bus.drive, f"Give the owner's pick on {where} a lane", f"The owner {words} on {where}, board {session}. Spawn a lane whose brief names {block}, or have a lane post a `claim` naming it.")
    text = f"NEEDS-LANE {where} on board {session[:8]}: the owner {clipped}. Spawn a lane whose brief names {block}, or have a lane post a claim naming it" + (f"; task {task[:7]}" if task else "")
    argv = ["post", "--drive", bus.drive, "--lane", BUS_LANE, "--kind", "ask", "--to", "root", "--id", f"{LANE_TOPIC}:{key}", "--topic", f"{LANE_TOPIC}:{block}", *attr_args({"session": session, "block": block, "task": task or ""}), "--text", CREDENTIAL.sub(REDACTED, text)]
    if (done := shell.cci(argv)).returncode:
        print(f"board-watch {session}: the needs-lane ask for {block} did not post: {done.stderr.strip()}", flush=True)


def add_task(shell: Shell, drive_name: str, title: str, body: str) -> str | None:
    if (entry := shell.entry(drive_name)) is None:
        print(f"board-watch: no drive registry entry names {drive_name}, so the pick gets no task", flush=True)
        return None
    done = shell.ccn(entry["checkout"], ["task", "add", title, "--body", CREDENTIAL.sub(REDACTED, body), "--backlog", "--label", f"program:{drive.program_of(entry)}", "--label", LANE_TOPIC, "--json"])
    if done.returncode:
        print(f"board-watch: ccn task add for {title} did not run: {done.stderr.strip()}", flush=True)
        return None
    return json.loads(done.stdout)["id"]


def attr_args(fields: dict[str, str]) -> list[str]:
    return [arg for name, value in fields.items() if value for arg in ("--attr", f"{name}={value}")]


def post_owner(shell: Shell, bus: Bus, key: str, text: str, block: str, names: list[str], prs: list[str], fields: dict[str, str]) -> list[dict]:
    resolves = open_asks(shell, bus, names)
    argv = [
        "post", "--drive", bus.drive, "--lane", BUS_LANE, "--kind", "owner", "--to", "all", "--id", key, "--text", text,
        *(["--topic", block] if block else []),
        *(f"--pr={pr}" for pr in prs),
        *attr_args(fields),
        *(["--resolves-seq", ",".join(str(record["seq"]) for record in resolves)] if resolves else []),
    ]
    done = shell.cci(argv)
    if done.returncode:
        print(f"board-watch {fields['session']}: cci post {key} failed: {done.stderr.strip()}", flush=True)
    return resolves


def board_records(shell: Shell, drive_name: str) -> list[dict]:
    def run(argv: list[str]) -> str:
        done = shell.cci(argv[1:])
        if done.returncode:
            raise RuntimeError(f"cci {' '.join(argv[1:3])} exited {done.returncode}: {done.stderr.strip()[:200]}")
        return done.stdout

    return sorted([*cci.records(run, drive_name, "--lane", BUS_LANE, "--kind=owner"), *cci.records(run, drive_name, "--lane", RUNNER, f"--topic={board_rows.WRITE_TOPIC}")], key=lambda record: record["seq"])


def post_snapshot(shell: Shell, bus: Bus, session: str, posted: dict) -> None:
    if shell.root_session(bus.drive) not in (None, session) or (outcomes := shell.outcomes(session)) is None:
        return
    board = json.dumps(board_rows.snapshot(outcomes.get("doc") or {}), sort_keys=True)
    now = shell.now()
    if board == posted.get("board") and now - posted["at"] < SNAPSHOT_FRESH:
        return
    shown = json.loads(board)
    text = f"Board {session[:8]} shows {len(shown['rows'])} PR rows, {len(shown['merges'])} merge cards and {len(shown['cards'])} cards"
    done = shell.cci(["post", "--drive", bus.drive, "--lane", BUS_LANE, "--kind", "note", "--to", RUNNER, "--topic", board_rows.SNAPSHOT_TOPIC, "--text", text, *attr_args({"session": session, "board": board})])
    if done.returncode:
        print(f"board-watch {session}: the board snapshot did not post: {done.stderr.strip()}", flush=True)
        return
    posted |= {"board": board, "at": now}


def keep_snapshot(shell: Shell, bus: Bus, session: str, stop: threading.Event) -> None:
    posted: dict = {}
    while True:
        if shell.stale():
            shell.reexec()
        post_snapshot(shell, bus, session, posted)
        if stop.wait(SNAPSHOT_SECONDS):
            return


def reopened(shell: Shell, session: str, block: str) -> bool:
    found = find(shell.document(session, block), block)
    return found is not None and shell.present(["cc-present", "update-block", "-", "--round", "current", "--session", session], json.dumps(found)).returncode == 0


def apply(shell: Shell, session: str, write: dict[str, str]) -> subprocess.CompletedProcess:
    if write["op"] == "ask":
        return shell.present(["cc-present", "ask", "-", "--session", session], write["card"])
    argv = ["cc-present", "verdict", "carry", "--session", session, "--block", write["block"], "--row", write["row"], "--head", write["head"], "--note", write.get("note", "")]
    done = shell.present(argv)
    if done.returncode and CLOSED_ROUND.search(done.stderr + done.stdout) and reopened(shell, session, write["block"]):
        return shell.present(argv)
    return done


def apply_writes(shell: Shell, bus: Bus, session: str) -> None:
    for line in shell.writes(bus.drive, f"board-applier-{session}"):
        if not line.strip():
            continue
        record = json.loads(line)
        write = board_rows.attrs(record)
        if record["lane"] != RUNNER or write.get("session") != session or write.get("op") not in ("carry", "ask"):
            continue
        done = apply(shell, session, write)
        said = ((done.stderr or done.stdout).strip().splitlines() or [f"exit {done.returncode}"])[-1]
        gone = done.returncode != 0 and bool(UNKNOWN_BLOCK.search(done.stderr + done.stdout))
        failed = done.returncode != 0 and not gone
        what = f"{write['op']} on block {write.get('block') or write.get('waits', '')}" + (f" row {write['row']} to {write['head'][:10]}" if write["op"] == "carry" else "")
        text = f"board write #{record['seq']} ({what}) " + (f"failed: {said}" if failed else "had no block to carry on; nothing to do" if gone else f"applied: {said}")
        argv = ["post", "--drive", bus.drive, "--lane", BUS_LANE, "--kind", "defect" if failed else "applied", "--to", f"{RUNNER},root" if failed else RUNNER, "--re-seq", str(record["seq"]), "--id", f"applied:{record['seq']}", "--text", text[:400]]
        if (posted := shell.cci(argv)).returncode:
            print(f"board-watch {session}: the answer to board write #{record['seq']} did not post: {posted.stderr.strip()}", flush=True)


def recorded(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {json.loads(line)["digest"] for line in path.read_text().splitlines() if line.strip()}


def free(lock) -> bool:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def record(line: str, state: Path, session: str, shell: Shell, seen: set[str], bus: Bus) -> None:
    with (state / BOARDS / f"{session}-events.jsonl").open("a") as events:
        events.write(line if line.endswith("\n") else f"{line}\n")
    bus.lines += 1
    if not line.strip():
        return
    event = json.loads(line)
    owner = event.get("type") in OWNER_EVENTS
    doc = shell.document(session, event["blockId"]) if owner and event.get("blockId") else None
    if owner:
        announce(event, line, session, doc, shell, bus)
    with (state / CAPTURES).open("a") as sink:
        for capture in captures(event, session, doc, shell.now(), bus.lines):
            if capture["digest"] not in seen:
                sink.write(json.dumps(capture, ensure_ascii=False) + "\n")
                seen.add(capture["digest"])


def cmd_start(args: argparse.Namespace, shell: Shell) -> int:
    state = Path(args.state_dir) if args.state_dir else rulings.state_dir(args, drive.drives_dir(), Path.home() / ".claude" / "scratch")
    (state / BOARDS).mkdir(parents=True, exist_ok=True)
    with (state / BOARDS / f"{args.session}.lock").open("a+") as lock:
        if not free(lock):
            print(f"board-watch {args.session}: another watch holds {lock.name}")
            return 0
        shell.end_inherited_children()
        seen = recorded(state / CAPTURES)
        events = state / BOARDS / f"{args.session}-events.jsonl"
        bus = Bus(bus_drive(args), events.read_text().count("\n") if events.exists() else 0)
        stop = threading.Event()
        shell.spawn(keep_snapshot, shell, bus, args.session, stop)
        shell.spawn(apply_writes, shell, bus, args.session)
        for line in shell.watch(args.session):
            record(line, state, args.session, shell, seen, bus)
        stop.set()
    print(f"board-watch {args.session}: the board closed")
    return 0


def bus_drive(args: argparse.Namespace) -> str:
    return args.program or drive.cci_drive(drive.load(drive.drives_dir() / f"{args.drive}.json"))


def same(standing: dict | None, entry: dict) -> bool:
    return standing is not None and all((standing.get(name) or "")[:7] == (entry.get(name) or "")[:7] for name in ("verdict", "head", "carriedFrom"))


def unrecorded(outcomes: dict, records: list[dict]) -> tuple[list[tuple[str, str, dict]], list[tuple[str, dict]]]:
    interactions = outcomes.get("interactions") or {}
    standing = board_rows.verdicts_of_records(records)
    answered = board_rows.answered_of_records(records)
    verdicts = [(block, row, entry) for block, rows in (interactions.get("triage") or {}).items() for row, entry in rows.items() if entry.get("verdict") and not same(standing.get(block, {}).get(row), entry)]
    picks = [(block, choice) for block, choice in (interactions.get("choices") or {}).items() if choice and (choice.get("optionIds") or choice.get("other")) and block not in answered]
    return verdicts, picks


def cmd_backfill(args: argparse.Namespace, shell: Shell) -> int:
    drive_name = bus_drive(args)
    if (outcomes := shell.outcomes(args.session)) is None:
        print(f"board-watch backfill: cc-present outcomes did not read for {args.session}", file=sys.stderr)
        return 1
    verdicts, picks = unrecorded(outcomes, board_records(shell, drive_name))
    for block, row, entry in verdicts:
        carried = entry.get("carriedFrom") or ""
        text = f"Backfilled from board {args.session[:8]} ({block}): {row} {entry['verdict']} at {entry.get('head') or 'no head'}" + (f", carried from {carried[:10]}" if carried else "") + f", recorded {entry.get('at') or 'at an unknown time'}"
        fields = {"event": "backfill", "session": args.session, "block": block, "row": row, "verdict": entry["verdict"], "head": entry.get("head") or "", "at": entry.get("at") or "", "carried_from": carried}
        backfilled(shell, drive_name, f"backfill:{args.session}:{block}:{row}:{entry['verdict']}:{entry.get('head', '')}:{carried}", text, [pr] if (pr := board_rows.pr_of(row)) else [], fields)
    for block, choice in picks:
        fields = {"event": "choice.selected", "session": args.session, "block": block, "picked": ",".join(choice.get("optionIds") or []), "other": "1" if choice.get("other") else ""}
        backfilled(shell, drive_name, f"backfill:{args.session}:{block}:pick", f"Backfilled from board {args.session[:8]} ({block}): the owner answered this choice", [], fields)
    verdicts, picks = unrecorded(outcomes, board_records(shell, drive_name))
    for block, row, entry in verdicts:
        print(f"UNRECORDED {block} {row} {entry['verdict']} at {entry.get('head') or 'no head'}")
    for block, _ in picks:
        print(f"UNRECORDED {block} pick")
    if verdicts or picks:
        return 1
    triage = (outcomes.get("interactions") or {}).get("triage") or {}
    print(f"verified: every one of {sum(1 for rows in triage.values() for entry in rows.values() if entry.get('verdict'))} verdicts on board {args.session[:8]} stands in cci records")
    return 0


def backfilled(shell: Shell, drive_name: str, key: str, text: str, prs: list[str], fields: dict[str, str]) -> None:
    done = shell.cci(["post", "--drive", drive_name, "--lane", BUS_LANE, "--kind", "owner", "--to", RUNNER, "--id", key, "--text", text, *(["--topic", fields["block"]] if fields["block"] else []), *(f"--pr={pr}" for pr in prs), *attr_args(fields)])
    if done.returncode:
        print(f"board-watch backfill: cci post {key} failed: {done.stderr.strip()}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="board_watch.py", description=__doc__.splitlines()[0])
    verbs = parser.add_subparsers(dest="verb", required=True)
    start = verbs.add_parser("start", help="record one board's owner interactions until it closes; a second watch exits at once")
    start.add_argument("--session", required=True, metavar="ID")
    which = start.add_mutually_exclusive_group(required=True)
    which.add_argument("--program", metavar="SLUG")
    which.add_argument("--drive", metavar="ID")
    start.add_argument("--state-dir", metavar="DIR")
    start.set_defaults(handler=cmd_start)
    backfill = verbs.add_parser("backfill", help="post a record for each verdict and pick the board holds that no record carries, then verify every one stands in records")
    backfill.add_argument("--session", required=True, metavar="ID")
    which = backfill.add_mutually_exclusive_group(required=True)
    which.add_argument("--program", metavar="SLUG")
    which.add_argument("--drive", metavar="ID")
    backfill.set_defaults(handler=cmd_backfill)
    return parser


def main(argv: list[str] | None = None, shell: Shell | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args, shell or Shell())


if __name__ == "__main__":
    drive.run_installed(__file__)
    sys.exit(main())
