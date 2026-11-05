#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
"""The board recorder: one reader per cc-present board that keeps every owner interaction as a capture.

    board_watch.py start --session ID (--program SLUG | --drive ID) [--state-dir DIR]
    board_watch.py backfill --session ID (--program SLUG | --drive ID)
    board_watch.py blanket --session ID --word SEQ --at TIME (--program SLUG | --drive ID)

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
overrides it. A second watch of the same board exits at once. A board whose document carries
`proof: true` was made to prove a change, so its taps are an agent's: its event lines stay, and
neither a capture nor an owner record comes of them, here or in `backfill`.

Each decision.created, choice.selected, input.submitted, feedback.created, pack.interaction,
triage.decided and submit also reaches the drive's bus the moment it arrives, so no lane waits on a coordinator to relay a
tap: one `cci post --kind owner` from lane `board-watch` names the card title and block
id, the verdict, picked option labels or the owner's text verbatim, each merge row's head, each
head a pick's card asked about, and the time. `lr board classify` routes it, and its `answer` attribute says which way: `structured` (an
option tap, a pack pick, an approval or rejection, a merge-row verdict) goes to the coordinator alone,
and `noted` (a verdict with a note) to the coordinator and to `owner-relay`; both resolve every open `ask`, `blocker` or `hold` whose text
names the block, its card, or the merge row (`merge-row-<pr>` or `merge:<pr>`); `free-text` (a
write-in, a note, an input, any other reply) goes to `owner-relay` alone and resolves nothing, since
the communicator routes it; `none` (a submit, an emptied choice) goes to the coordinator. Each answer also adds a row
to the communicator's owner ledger: `lr owner add` writes it `addressed` at its record for `structured`, and `lr owner received` writes
`noted` and `free-text` due at their deadline, with the acknowledgement he reads on that row and in chat. The communicator hears of taps only in batches: every tenth
`structured` answer on a board posts one `note` to `owner-relay` on topic `tap-batch`, a single line
naming those ten, which `<state dir>/boards/<session>-taps.jsonl` holds until the tenth arrives. Its idempotency key is the event's line number
in the events file plus a hash of the line, so a restart that resumes from the watch's cursor
never posts an event twice. Each record carries attributes a reader takes without parsing the
text: `event`, `session` and `block`; a decision's `verdict`; a pick's `picked` option ids,
`other` for a write-in, and `heads`, each `owner/name#number=head` its card asked about; and a triage.decided posts one record per row, with that row's `row`,
`verdict` and `head`, keyed by the row as well.

Each of those records that carries the owner's answer, every one but a triage row and a submit,
also writes one `ccn answer add` in the drive's checkout as it posts: the card's title as the
question, and as the body the owner's words verbatim, the card and block ids, the board and the
Pacific time, labelled `from:owner`, `board-answer`, `program:<program>`, `board-session:<session>`,
`board-card:<card>`, `board-block:<block>` and a `proof:board-watch:<key>` that keeps it to one row
per record. A Yes on a Slack permission card, whose id is `slack:<channel>:<thread>:<digest>`, also
carries `slack-grant:<channel>/<thread>`, or `slack-grant:<channel>` when the card named no thread, and
a No carries the matching `slack-revoke:`, each read from the label the board showed on the tapped
option, so the Slack permission hook reads his newest tap as his grant or revocation from any session. A card whose answer has no row stays on the board: the long-running hooks refuse a
remove-block, push or update-block that takes it off, and `lr board cards sweep` leaves it up.

A pick, write-in or pack pick that `lr findings cited` finds no record naming, of any kind and from
anyone but this watcher, by its card, block id or owner record's seq, and that no PR row carries (a land pick whose
row reads tapped since, landed, or at a newer head), 30 minutes after that record is posted again as an `ask` to `meta-desk` on topic `uncited:<block>`, and at 60 minutes as a
`blocker` to `meta-desk`, each once, keyed `uncited:<seq>` and `uncited-blocker:<seq>`.

Each answer also runs `lr board wake`, which addresses a wake to the lane whose ask drew the card, the
lane on the card's row or else the newest ask naming the card from a lane that is not a desk, and relays
it to a lane placed on Orca. The supervisor relaunches a retired lane with no live heir from its brief, once,
with the answer in the new lane's brief. A structured pick on the drive coordinator's newest board that no
lane asked for draws the same ask to `owner-relay`. Every minute `lr board wake late` tells `meta-desk` and `owner-relay`, once, of each
wake its lane has not acknowledged in ten minutes; nothing blocks.

While the board is open, the watch of the drive coordinator's newest session, read from the drive
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
`applied` record, or a `defect` record to the runner and the coordinator naming what cc-present said.

`backfill` posts one `owner` record, to `desk-runner`, for each verdict and pick `cc-present
outcomes --all` holds for the session that no record carries the same way, at the time the
owner made it, then reads the records again and exits 1, naming each, while any verdict or pick
still differs: the landing runner reads taps only from records once this prints `verified`. It also
writes the ccn answer row for each answered block that has none, from the newest answer
`cc-present outcomes --block` lists, and exits 1 naming each block still without one.

`blanket` records an owner word that approves everything waiting on him, such as "every pending PR
is approved", as one approved `owner` record per row of `lr board rows` that is ready for his tap,
not tapped, and whose head was pushed by the word's `--at` time, at that row's head and the word's
time. A head pushed after the word gets no record, so it waits on his tap at its own row: every landing path
reads the verdict records, never the word (coordinator 313672, 314327).
"""

from __future__ import annotations

import argparse
import fcntl
import os
import hashlib
import json
import re
import subprocess
import sys
import threading
import time
import traceback
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from long_running import answer_rows
from long_running import answer_search
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
OWNER_EVENTS = frozenset({"decision.created", "choice.selected", "input.submitted", "feedback.created", "pack.interaction", "triage.decided", "submit"})
PICKS = frozenset({"choice.selected", "input.submitted", "pack.interaction"})
REPOST_AFTER = timedelta(minutes=30)
ESCALATE_AFTER = timedelta(minutes=60)
PICK_WINDOW = "3h"
UNCITED = "uncited"
OPEN_KINDS = ("ask", "blocker", "hold")
BUS_LANE = board_rows.OWNER_LANE
BLANKET_BLOCK = "merge-prs"
RUNNER = board_rows.RUNNER_LANE
GREP_BYTES = 200000
SNAPSHOT_SECONDS = 60
RESTART_SECONDS = 60
SNAPSHOT_FRESH = timedelta(hours=6)
CLOSED_ROUND = re.compile(r"belongs to closed round")
UNKNOWN_BLOCK = re.compile(r'\bunknown block "')
WATCH_BYTES = 10_000_000
RELAY_LANE = "owner-relay"
META_DESK = "meta-desk"
LR = Path(__file__).resolve().parents[3] / "bin" / "lr"
STRUCTURED = "structured"
NOTED = "noted"
FREE_TEXT = "free-text"
NO_ANSWER = "none"
LANE_PICKS = frozenset({"choice.selected", "pack.interaction"})
ANSWER_TO = {STRUCTURED: "root", NOTED: f"root,{RELAY_LANE}", FREE_TEXT: RELAY_LANE, NO_ANSWER: "root"}
TAPS_PER_LINE = 10
TAP_TOPIC = "tap-batch"
CCN_SECONDS = 60


@dataclass
class Bus:
    drive: str
    lines: int


class Shell:
    """The single side-effect boundary: the board's event stream, its document and outcomes, cc-present writes, cci, the drive registry, threads, and the clock."""

    def __init__(self) -> None:
        self._watch_lock = threading.Lock()
        self._watches: list[subprocess.Popen] = []
        self._closing = False

    def stale(self) -> bool:
        held = drive.release.held()
        return held is not None and drive.behind(drive.plugin_manifest()["version"], held.name)

    def reexec(self) -> None:
        os.environ.pop(drive.CALLER_VERSION_ENV, None)
        with self._watch_lock:
            drive.run_installed(__file__, before_exec=self._end_watches_locked)

    def _end_watch_locked(self, child: subprocess.Popen) -> None:
        child.kill()
        child.wait(timeout=drive.REAP_SECONDS)
        self._watches.remove(child)

    def _end_watches_locked(self) -> None:
        self._closing = True
        for child in list(self._watches):
            self._end_watch_locked(child)

    def _watch(self, argv: list[str]) -> Iterator[str]:
        with self._watch_lock:
            if self._closing:
                return
            child = subprocess.Popen(argv, stdout=subprocess.PIPE, stdin=subprocess.DEVNULL, text=True)
            self._watches.append(child)
        try:
            yield from child.stdout
        finally:
            with self._watch_lock:
                if child in self._watches:
                    self._end_watch_locked(child)
            child.stdout.close()

    def watch(self, session: str) -> Iterator[str]:
        yield from self._watch(["cc-present", "watch", "--session", session])

    def document(self, session: str, block: str) -> dict | None:
        return (self.shown(session, block) or {}).get("doc")

    def shown(self, session: str, block: str) -> dict | None:
        done = subprocess.run(["cc-present", "outcomes", "--session", session, "--block", block], capture_output=True, text=True)
        return json.loads(done.stdout) if done.returncode == 0 else None

    def outcomes(self, session: str) -> dict | None:
        done = subprocess.run(["cc-present", "outcomes", "--all", "--session", session], capture_output=True, text=True)
        start = done.stdout.find("{")
        return json.loads(done.stdout[start:]) if done.returncode == 0 and start >= 0 else None

    def present(self, argv: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(argv, input=stdin, capture_output=True, text=True)

    def writes(self, drive_name: str, cursor: str) -> Iterator[str]:
        argv = ["cci", "watch", "--drive", drive_name, "--cursor", cursor, "--lane", RUNNER, "--lane", board_rows.SUPERVISOR_LANE, "--topic", board_rows.WRITE_TOPIC, "--json", "--budget", str(WATCH_BYTES)]
        yield from self._watch(argv)

    def spawn(self, target, *args) -> None:
        def run() -> None:
            try:
                target(*args)
            except Exception:
                traceback.print_exc()
                time.sleep(RESTART_SECONDS)
                self.reexec()
                with self._watch_lock:
                    self._end_watches_locked()
                    drive.reexec([sys.executable, *sys.argv])

        threading.Thread(target=run, name=target.__name__, daemon=True).start()

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

    def lr(self, argv: list[str], stdin: str) -> subprocess.CompletedProcess:
        return subprocess.run([str(LR), *argv], input=stdin, capture_output=True, text=True)

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
            words = [*(["picked", quoted(labels) if (labels := picked(found, ids)) else "option " + ", ".join(ids)] if ids else []), *said("wrote", event.get("other")), *[f"at {pr} {head}" for pr, head in sorted((event.get("heads") or {}).items())]]
        case {"type": "input.submitted"}:
            words = said("wrote", event.get("text"))
        case {"type": "pack.interaction"}:
            words = said("picked", packed(event.get("payload")))
        case _:
            words = said("noted", event.get("text"))
    return " ".join(words)


def packed(payload: object) -> str | None:
    if not payload:
        return None
    return payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def place(doc: dict | None, block: str) -> str:
    title = (card_of(doc, block) or {}).get("title")
    found = find(doc, block) or {}
    item = found.get("prompt") if found.get("type") == "choice" and found.get("prompt") != title else None
    named = ", ".join(filter(None, [title, item]))
    return f"{named} ({block})" if named else block or "the board"


def announced(event: dict, session: str, doc: dict | None) -> str:
    return CREDENTIAL.sub(REDACTED, f"Owner on board {session[:8]}, {place(doc, event.get('blockId', ''))}: {told(event, doc)}")


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


def announce(event: dict, line: str, session: str, doc: dict | None, shell: Shell, bus: Bus) -> list[str]:
    key = f"board:{session}:{bus.lines}:{hashlib.sha256(line.encode()).hexdigest()[:8]}"
    block = event.get("blockId", "")
    fields = {"event": event["type"], "session": session, "block": block}
    if event["type"] == "triage.decided":
        heads = {row.id: row.head for row in board_rows.rows_of(doc or {})}
        taps = []
        for row, entry in event["verdicts"].items():
            single = dict(event, verdicts={row: entry})
            pr = board_rows.pr_of(row)
            answer = answer_of(single, session, shell)
            post_owner(shell, bus, f"{key}:{row}", announced(single, session, doc), block, named(single, doc), [pr] if pr else [], fields | {"row": row, "verdict": entry["verdict"], "head": entry.get("head") or heads.get(row) or ""}, answer)
            taps += [f"{place(doc, block)}: {told(single, doc)}"] if answer == STRUCTURED else []
        return taps
    if event["type"] == "decision.created":
        fields["verdict"] = event.get("verdict", "")
    if event["type"] == "choice.selected":
        fields |= {"picked": ",".join(event.get("optionIds") or []), "other": "1" if event.get("other") else "", "heads": ",".join(f"{pr}={head}" for pr, head in sorted((event.get("heads") or {}).items()))}
    answer = answer_of(event, session, shell)
    seq = post_owner(shell, bus, key, announced(event, session, doc), block, named(event, doc), [], fields, answer)
    register(event, key, session, doc, shell, bus.drive)
    if answer_rows.answered(event):
        card = (card_of(doc, block) or {}).get("id")
        words = CREDENTIAL.sub(REDACTED, told(event, doc))
        if card and (done := shell.lr(["board", "cards", "recheck", "--drive", bus.drive, "--session", session, "--lane", BUS_LANE, "--pick", card, "--said", words], "")).returncode:
            print(f"board-watch {session}: the cards resting on {card} were not re-checked: {done.stderr.strip()}", flush=True)
        unclaimed = answer == STRUCTURED and event["type"] in LANE_PICKS and shell.root_session(bus.drive) in (None, session)
        wake_lane(card or block, block, words, key, seq, unclaimed, session, shell, bus)
    return [f"{place(doc, block)}: {told(event, doc)}"] if answer == STRUCTURED else []


def batch_taps(taps: list[str], pending: Path, session: str, shell: Shell, bus: Bus) -> None:
    if not taps:
        return
    with pending.open("a") as sink:
        sink.writelines(f"{json.dumps(tap, ensure_ascii=False)}\n" for tap in taps)
    held = [json.loads(line) for line in pending.read_text().splitlines() if line.strip()]
    batches = len(held) // TAPS_PER_LINE
    for index in range(batches):
        batch = held[index * TAPS_PER_LINE : (index + 1) * TAPS_PER_LINE]
        text = f"The owner made {TAPS_PER_LINE} taps on board {session[:8]}, each already with the coordinator: " + "; ".join(" ".join(tap.split()) for tap in batch)
        argv = ["post", "--drive", bus.drive, "--lane", BUS_LANE, "--kind", "note", "--to", RELAY_LANE, "--topic", TAP_TOPIC, "--id", f"{TAP_TOPIC}:{session}:{bus.lines}:{index}", *attr_args({"session": session}), "--text", CREDENTIAL.sub(REDACTED, text)]
        if (done := shell.cci(argv)).returncode:
            print(f"board-watch {session}: the tap batch did not post: {done.stderr.strip()}", flush=True)
    if batches:
        pending.write_text("".join(f"{json.dumps(tap, ensure_ascii=False)}\n" for tap in held[batches * TAPS_PER_LINE :]))


def answer_of(event: dict, session: str, shell: Shell) -> str:
    done = shell.lr(["board", "classify"], json.dumps(event))
    if done.returncode:
        print(f"board-watch {session}: lr board classify did not run, so this answer goes to {RELAY_LANE} first: {done.stderr.strip()}", flush=True)
        return FREE_TEXT
    return done.stdout.strip()


def register(event: dict, key: str, session: str, doc: dict | None, shell: Shell, drive_name: str, moment: datetime | None = None) -> None:
    if not answer_rows.answered(event):
        return
    if (entry := shell.entry(drive_name)) is None:
        print(f"board-watch {session}: no drive registry entry names {drive_name}, so the answer on {event.get('blockId')} gets no ccn row", flush=True)
        return
    block = event["blockId"]
    top = card_of(doc, block) or {}
    title = top.get("title") or (find(doc, block) or {}).get("prompt") or block
    proof = f"proof:board-watch:{key}"
    listed = shell.ccn(entry["checkout"], ["answer", "list", "--label", proof, "--json"])
    if listed.returncode == 0 and json.loads(listed.stdout or "[]"):
        return
    words = CREDENTIAL.sub(REDACTED, told(event, doc))
    topics = answer_search.vocabulary(lambda argv: shell.ccn(entry["checkout"], argv).stdout, drive.program_of(entry))
    card = top.get("id") or block
    argv = answer_rows.add_argv(drive.program_of(entry), session, card, block, title, CREDENTIAL.sub(REDACTED, answer_rows.card_words(top)), words, moment or shell.now(), proof, answer_search.anchors(topics, f"{title}\n{words}"), answer_rows.slack_labels(top, event))
    if (done := shell.ccn(entry["checkout"], argv)).returncode:
        print(f"board-watch {session}: the ccn answer row for {block} did not write: {done.stderr.strip()}", flush=True)


def wake_lane(card: str, block: str, words: str, key: str, seq: str, unclaimed: bool, session: str, shell: Shell, bus: Bus) -> None:
    argv = ["board", "wake", card, "--drive", bus.drive, "--session", session, "--lane", BUS_LANE, "--block", block, "--said", words, "--key", key, *(["--owner-seq", seq] if seq.isdigit() else []), *(["--unclaimed"] if unclaimed else [])]
    if (done := shell.lr(argv, "")).returncode:
        print(f"board-watch {session}: the lane that asked for {card} was not woken: {done.stderr.strip()}", flush=True)


def wake_late(shell: Shell, bus: Bus, session: str) -> None:
    if (done := shell.lr(["board", "wake", "late", "--drive", bus.drive, "--session", session, "--lane", BUS_LANE], "")).returncode:
        print(f"board-watch {session}: the unacknowledged wakes were not read: {done.stderr.strip()}", flush=True)


def attr_args(fields: dict[str, str]) -> list[str]:
    return [arg for name, value in fields.items() if value for arg in ("--attr", f"{name}={value}")]


def post_owner(shell: Shell, bus: Bus, key: str, text: str, block: str, names: list[str], prs: list[str], fields: dict[str, str], answer: str) -> str:
    resolves = [] if answer == FREE_TEXT else open_asks(shell, bus, names)
    argv = [
        "post", "--drive", bus.drive, "--lane", BUS_LANE, "--kind", "owner", "--to", ANSWER_TO[answer], "--id", key, "--text", text,
        *(["--topic", block] if block else []),
        *(f"--pr={pr}" for pr in prs),
        *attr_args(fields | {"answer": answer}),
        *(["--resolves-seq", ",".join(str(record["seq"]) for record in resolves)] if resolves else []),
    ]
    done = shell.cci(argv)
    if done.returncode:
        print(f"board-watch {fields['session']}: cci post {key} failed: {done.stderr.strip()}", flush=True)
    if answer != NO_ANSWER:
        add_ledger_row(shell, bus, fields["session"], text, answer, done.stdout)
    return done.stdout.strip().removeprefix("#")


def add_ledger_row(shell: Shell, bus: Bus, session: str, text: str, answer: str, posted: str) -> None:
    seq = posted.strip().removeprefix("#")
    routed = ["--routed", seq] if seq.isdigit() else []
    verb = ["add", "--drive", bus.drive, "--state", "addressed", *routed] if answer == STRUCTURED else ["received", "--drive", bus.drive, *routed]
    done = shell.lr(["owner", *verb], text)
    if done.returncode:
        print(f"board-watch {session}: the ledger row did not write: {done.stderr.strip()}", flush=True)


def bus_records(shell: Shell, drive_name: str, *filters: str, since: int | str = 0) -> list[dict]:
    def run(argv: list[str]) -> str:
        done = shell.cci(argv[1:])
        if done.returncode:
            raise RuntimeError(f"cci {' '.join(argv[1:3])} exited {done.returncode}: {done.stderr.strip()[:200]}")
        return done.stdout

    return cci.records(run, drive_name, *filters, since=since)


def board_records(shell: Shell, drive_name: str) -> list[dict]:
    return sorted([*bus_records(shell, drive_name, "--lane", BUS_LANE, "--kind=owner"), *bus_records(shell, drive_name, "--lane", RUNNER, "--lane", board_rows.SUPERVISOR_LANE, f"--topic={board_rows.WRITE_TOPIC}")], key=lambda record: record["seq"])


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
    reposted: set[str] = set()
    while True:
        if shell.stale():
            shell.reexec()
        post_snapshot(shell, bus, session, posted)
        repost_uncited(shell, bus, session, reposted)
        wake_late(shell, bus, session)
        if stop.wait(SNAPSHOT_SECONDS):
            return


def repost_uncited(shell: Shell, bus: Bus, session: str, reposted: set[str]) -> None:
    now = shell.now()
    due = []
    for record in bus_records(shell, bus.drive, "--lane", BUS_LANE, "--kind=owner", since=PICK_WINDOW):
        fields = board_rows.attrs(record)
        age = now - datetime.fromisoformat(record["at"])
        if fields.get("session") != session or fields.get("event") not in PICKS or age < REPOST_AFTER:
            continue
        if fields["event"] == "choice.selected" and not (fields.get("picked") or fields.get("other")):
            continue
        kind, tag = ("blocker", f"{UNCITED}-blocker") if age >= ESCALATE_AFTER else ("ask", UNCITED)
        key = f"{tag}:{record['seq']}"
        if key not in reposted:
            due.append((record, fields, age, kind, key))
    named = cited(shell, bus, {record["seq"]: fields["block"] for record, fields, *_ in due})
    for record, fields, age, kind, key in due:
        if record["seq"] in named:
            continue
        minutes = int(age.total_seconds() // 60)
        text = f"UNCITED owner pick #{record['seq']} on board {session[:8]}: no record names {fields['block']} {minutes} minutes after it. {record['text']}"
        argv = ["post", "--drive", bus.drive, "--lane", BUS_LANE, "--kind", kind, "--to", META_DESK, "--id", key, "--topic", f"{UNCITED}:{fields['block']}", "--re-seq", str(record["seq"]), *attr_args({"session": session, "block": fields["block"]}), "--text", CREDENTIAL.sub(REDACTED, text)]
        if (done := shell.cci(argv)).returncode:
            print(f"board-watch {session}: the uncited re-post of #{record['seq']} did not post: {done.stderr.strip()}", flush=True)
            continue
        reposted.add(key)


def cited(shell: Shell, bus: Bus, picks: dict[int, str]) -> set[int]:
    if not picks:
        return set()
    done = shell.lr(["findings", "--drive", bus.drive, "cited", *(f"{seq}={block}" for seq, block in picks.items())], "")
    if done.returncode:
        print(f"board-watch: lr findings cited failed, so no pick is re-posted this pass: {done.stderr.strip()}", flush=True)
        return set(picks)
    return {int(seq) for seq in done.stdout.split()}


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
        if record["lane"] not in (RUNNER, board_rows.SUPERVISOR_LANE) or write.get("session") != session or write.get("op") not in ("carry", "ask"):
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
    if owner and ((doc if event.get("blockId") else (shell.outcomes(session) or {}).get("doc")) or {}).get("proof"):
        return
    if owner:
        batch_taps(announce(event, line, session, doc, shell, bus), state / BOARDS / f"{session}-taps.jsonl", session, shell, bus)
    with (state / CAPTURES).open("a") as sink:
        for capture in captures(event, session, doc, shell.now(), bus.lines):
            if capture["digest"] not in seen:
                sink.write(json.dumps(capture, ensure_ascii=False) + "\n")
                seen.add(capture["digest"])


def cmd_start(args: argparse.Namespace, shell: Shell) -> int:
    os.environ.update(drive.own_lane(BUS_LANE))
    state = Path(args.state_dir) if args.state_dir else rulings.state_dir(args, drive.drives_dir(), drive.scratch_dir())
    (state / BOARDS).mkdir(parents=True, exist_ok=True)
    with (state / BOARDS / f"{args.session}.lock").open("a+") as lock:
        if not free(lock):
            print(f"board-watch {args.session}: another watch holds {lock.name}")
            return 0
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
    if (outcomes.get("doc") or {}).get("proof"):
        return 0
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
    gaps = backfill_rows(shell, drive_name, args.session, outcomes)
    for gap in gaps:
        print(f"UNROWED {gap}")
    if verdicts or picks or gaps:
        return 1
    triage = (outcomes.get("interactions") or {}).get("triage") or {}
    print(f"verified: every one of {sum(1 for rows in triage.values() for entry in rows.values() if entry.get('verdict'))} verdicts on board {args.session[:8]} stands in cci records")
    return 0


def cmd_blanket(args: argparse.Namespace, shell: Shell) -> int:
    drive_name = bus_drive(args)
    word = datetime.fromisoformat(args.at)
    done = shell.lr(["board", "rows", "--drive", drive_name, "--json"], "")
    if done.returncode:
        print(f"board-watch blanket: lr board rows failed: {done.stderr.strip()}", file=sys.stderr)
        return 1
    rows = [(str(row["number"]), row["head"]) for row in json.loads(done.stdout) if row["ready_for_tap"] and not row["tapped"] and datetime.fromisoformat(row["head_at"]) <= word]
    standing = board_rows.verdicts_of_records(board_records(shell, drive_name))
    for pr, head in rows:
        if same(standing.get(BLANKET_BLOCK, {}).get(pr), {"verdict": "approved", "head": head}):
            continue
        text = f"The owner's word {args.word} at {args.at} approved #{pr} at {head}, ready for his tap in lr board rows"
        fields = {"event": "blanket", "word": args.word, "session": args.session, "block": BLANKET_BLOCK, "row": pr, "verdict": "approved", "head": head, "at": args.at}
        backfilled(shell, drive_name, f"blanket:{args.word}:{BLANKET_BLOCK}:{pr}:{head}", text, [pr], fields)
    print(f"blanket {args.word}: approved {', '.join(f'#{pr} at {head}' for pr, head in rows) or 'no rows'}, the rows ready for his tap with a head from by {args.at}")
    return 0


def backfill_rows(shell: Shell, drive_name: str, session: str, outcomes: dict) -> list[str]:
    if (entry := shell.entry(drive_name)) is None:
        return [f"no drive registry entry names {drive_name}, so no answer gets a ccn row"]

    def ccn(argv: list[str]) -> str:
        done = shell.ccn(entry["checkout"], argv)
        if done.returncode:
            raise RuntimeError(f"ccn {' '.join(argv[:2])} exited {done.returncode}: {done.stderr.strip()[:200]}")
        return done.stdout

    interactions = outcomes.get("interactions") or {}
    blocks = {block for group in ("decisions", "choices", "inputs", "packs", "feedback") for block in interactions.get(group) or {}} - answer_rows.rowed(ccn, session)
    owed = []
    for block in sorted(blocks):
        shown = shell.shown(session, block) or {}
        if (found := answer_rows.newest(shown, {block}).get(block)) is None:
            continue
        owed.append(block)
        register(found["payload"], f"backfill:{session}:{block}:{found['at']}", session, shown.get("doc"), shell, drive_name, datetime.fromisoformat(found["at"]))
    have = answer_rows.rowed(ccn, session)
    return [f"{block}: the owner's answer has no ccn answer row" for block in owed if block not in have]


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
    blanket = verbs.add_parser("blanket", help="record an owner word approving the whole board as one approved verdict per row written by the word's time")
    blanket.add_argument("--session", required=True, metavar="ID")
    blanket.add_argument("--word", required=True, metavar="SEQ")
    blanket.add_argument("--at", required=True, metavar="TIME")
    which = blanket.add_mutually_exclusive_group(required=True)
    which.add_argument("--program", metavar="SLUG")
    which.add_argument("--drive", metavar="ID")
    blanket.set_defaults(handler=cmd_blanket)
    return parser


def main(argv: list[str] | None = None, shell: Shell | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args, shell or Shell())


if __name__ == "__main__":
    drive.run_installed(__file__)
    sys.exit(main())
