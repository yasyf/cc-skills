from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from datetime import timedelta
from pathlib import Path

from livedash import Col, Context, Entry, Feed, Kv, Table, component, view
from livedash.components import boards, cci, tasks
from livedash.components import ledger as ledgers
from livedash.components.release import overview, platy

from . import inboxlines

DRIVES = Path("long-running") / "drives"
ACTIONS_FILE = "owner-actions.json"
OWNER_ACTIONS = {"question": "Question", "complete": "Mark complete", "reply": "Reply"}
CCI_TEXT = 400
HEADER_CHARS = 80
DECISION_WINDOW = timedelta(hours=48)
CLOSING_KINDS = ["done", "lift", "go", "decision", "owner", "answer"]
OWNER_KINDS = frozenset({"owner-item", "owner-ask"})
OWNER_VERBS = frozenset({"DECIDE", "ASK", "RULING"})
OWNER_WORD = re.compile(r"(?:->|→)\s*owner\b|\b(?:for|to|asks?|needs|awaits?|waiting on) the owner\b|\bowner (?:pick|decision|call|ruling|answer) (?:needed|pending|required)\b", re.IGNORECASE)
DECIDER = re.compile(r"\bDECIDE (?:msg_\w+ )?(?P<lane>[\w.:-]+?)(?::| \()")
OWNER_BULLET = re.compile(r"^- \*\*(?P<title>[^*]+)\*\*:?[ \t]*(?P<detail>.*)$", re.MULTILINE)
COMPACT_BOUNDARY = b'"subtype":"compact_boundary"'
VERB_TONE = {"LANDED": "ok", "RELEASED": "ok", "FIX-LIVE": "ok", "HOLD": "warn", "DECIDE": "warn", "ASK": "warn", "INCIDENT": "bad", "ALERT": "bad"}
INBOXES: dict[str, inboxlines.Inbox] = {}
COMPACTIONS: dict[str, tuple[int, list[dict]]] = {}
ACTING = threading.Lock()


def inbox(ctx: Context, state_dir: Path) -> list[dict]:
    reader = INBOXES.setdefault(str(state_dir), inboxlines.Inbox(state_dir / "inbox", ctx.dir / inboxlines.SEEN_FILE))
    return [line.row() | {"cite": f"inbox:{line.file}:{line.number}"} for line in reader.lines()]


def is_owner_task(task: dict) -> bool:
    metadata = task.get("metadata") or {}
    return (task.get("subject") or "").startswith("Owner item") or metadata.get("kind") in OWNER_KINDS or bool(OWNER_KINDS & set(metadata.get("labels") or []))


def open_decisions(lines: list[dict], floor: str) -> list[dict]:
    later: list[dict] = []
    waiting = []
    for line in lines:
        if (line["at"] or "") < floor:
            break
        if line["verb"] in OWNER_VERBS and OWNER_WORD.search(line["text"]):
            asker = line["lane"] or ((found := DECIDER.search(line["text"])) and found["lane"])
            named = asker and platy.mentions(asker)
            if not asker or not any(after["lane"] == asker or named.search(after["text"]) for after in later):
                waiting.append(line)
        later.append(line)
    return waiting


def owner_bullets(path: Path) -> list[dict]:
    text = path.read_text()
    return [{"title": match["title"], "detail": match["detail"], "line": text.count("\n", 0, match.start()) + 1} for match in OWNER_BULLET.finditer(text)]


def other_drive_sessions(ident: str) -> set[str]:
    return {session for path in (Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / DRIVES).glob("*.json") if (entry := json.loads(path.read_text()))["drive"] != ident for session in entry["sessions"]}


def actions(directory: Path) -> list[dict]:
    path = directory / ACTIONS_FILE
    return json.loads(path.read_text()) if path.exists() else []


def acting(name: str):
    label = OWNER_ACTIONS[name]

    def act(ctx: Context, row: dict, text: str) -> str:
        if name != "complete" and not text:
            raise ValueError(f"{label} needs a note")
        tail = f" — {text}" if text else ""
        room = CCI_TEXT - len(f"{label}:  [{row['cite']}]{tail}")
        if room < HEADER_CHARS // 4:
            raise ValueError(f"the note is {len(text)} characters; keep it under {CCI_TEXT - HEADER_CHARS} so the item still fits")
        item = row["title"] if len(row["title"]) <= room else row["title"][: room - 1] + "…"
        stored = ctx.json(["cci", "post", "--drive", ctx.facts["cci_drive"], "--lane", "owner", "--kind", "owner", "--to", "main", "--text", f"{label}: {item} [{row['cite']}]{tail}", "--json"])
        record = {"cite": row["cite"], "action": name, "text": text, "at": stored["at"], "seq": stored["seq"]}
        with ACTING:
            path = ctx.dir / ACTIONS_FILE
            staged = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}")
            staged.write_text(json.dumps([*actions(ctx.dir), record]))
            staged.replace(path)
        return f"{label} sent to main as cci #{stored['seq']}"

    return act


def owner_rows(ctx: Context, checkout: str, sessions: list[str], state_dir: Path, ledger: str | None, owner_files: list[str], started_at: str | None) -> list[dict]:
    rows = []
    found, _ = tasks.session_tasks(checkout, sessions)
    for task in found:
        if task.get("status") != "completed" and not task.get("archived") and is_owner_task(task):
            rows.append({"kind": "task", "title": task.get("subject"), "state": task.get("status"), "at": task.get("updated_at"), "cite": f"task:{task['id']}"})
    if ledger:
        all_rows = ledgers.rows(ctx, ledger)
        prs = {key: fields for key, fields in all_rows.items() if key.isdigit()}
        for key, fields in all_rows.items():
            if key.startswith(ledgers.ASK_PREFIX) and (state := ledgers.ask_state(fields, prs)) not in ledgers.DONE_ASKS:
                rows.append({"kind": "ask", "title": fields.get("text"), "state": state, "at": fields.get("asked_at"), "cite": f"ask:{key}"})
    for line in open_decisions(inbox(ctx, state_dir), view.iso(ctx.now - DECISION_WINDOW)):
        rows.append({"kind": "decide", "title": line["text"], "state": line["lane"], "at": line["at"], "cite": line["cite"]})
    elsewhere = other_drive_sessions(ctx.facts["id"])
    for board in boards.board_rows(ctx, started_at):
        if boards.waiting(board) and board["session"] not in elsewhere:
            rows.append({"kind": "board", "title": board["title"], "title_url": board["title_url"], "state": f"{board['submitted']}, {board['answered']} answered", "at": board["updated"], "cite": board["cite"]})
    for name in owner_files:
        path = state_dir / name
        if path.exists():
            stamp = inboxlines.iso(inboxlines.epoch(path.stat().st_mtime))
            rows += [{"kind": "pending", "title": f"{item['title']}: {item['detail']}".rstrip(": "), "state": name, "at": stamp, "cite": f"file:{name}:{hashlib.sha1(item['title'].encode()).hexdigest()[:8]}"} for item in owner_bullets(path)]
    return rows


@component("needs-owner", "Needs you", every="1m", timeout="90s", actions={name: acting(name) for name in OWNER_ACTIONS})
def needs_owner(ctx: Context, *, checkout: str, sessions: list[str], state_dir: Path, ledger: str | None = None, owner_files: list[str] = [], started_at: str | None = None) -> Table:
    """What waits on the owner: open tasks titled "Owner item" or carrying an owner-item or owner-ask kind, ledger asks not
    yet answered, dropped or delivered, owner-addressed DECIDE, ASK and RULING inbox lines no later line answered, open
    unsubmitted cc-present boards from this drive that ask something, and `- **title**: detail` bullets in `owner_files`. An item
    leaves when a curator `resolved:` record names its cite or the owner marks it complete. Actions question, complete and
    reply post an owner record to main; main's replies show beside the item."""
    rows = owner_rows(ctx, checkout, sessions, state_dir, ledger, owner_files, started_at)
    closed = overview.curated(cci.records(ctx, started_at, kind=CLOSING_KINDS))
    sent = actions(ctx.dir)
    replies = cci.records(ctx, min(action["at"] for action in sent), to="owner") if sent else []
    answering = {reply.get("re") or reply.get("resolves"): reply for reply in replies}
    out = []
    for row in rows:
        mine = [action for action in sent if action["cite"] == row["cite"]]
        if row["cite"] in closed or any(action["action"] == "complete" for action in mine):
            continue
        last = mine[-1] if mine else None
        reply = answering.get(last["seq"]) if last else None
        said = f"you: {OWNER_ACTIONS[last['action']].lower()}{' — ' + last['text'] if last['text'] else ''}" if last else None
        out.append(row | {"key": row["cite"], "title": row["title"] or "", "said": f"{said}; main: {reply['text']}" if reply else said, "tone": "warn" if row["kind"] in ("board", "decide", "ask") else None})
    out.sort(key=lambda row: row["at"] or "", reverse=True)
    return Table([Col("kind", "Kind", "badge"), Col("title", "Item", "link"), Col("state", "State", "badge"), Col("at", "Raised", "age"), Col("said", "Conversation")], out, note=None if out else "Nothing is waiting on you.")


@component("inbox-feed", "Inbox", every="30s")
def inbox_feed(ctx: Context, *, state_dir: Path, limit: int = 200) -> Feed:
    """The drive's markdown inbox lines and archives, newest first; a line without a clock inherits the next one's, marked ≈."""
    lines = inbox(ctx, state_dir)[:limit]
    entries = [Entry(line["at"] or "", line["lane"] or line["file"], ("≈ " if line["approx"] else "") + line["text"], None, VERB_TONE.get(line["verb"] or ""), key=line["cite"], cite=line["cite"]) for line in lines]
    return Feed(entries, note=None if entries else "The inbox is empty.")


def compactions_in(path: Path, offset: int) -> tuple[list[dict], int]:
    found = []
    with path.open("rb") as transcript:
        transcript.seek(offset)
        for raw in transcript:
            if not raw.endswith(b"\n"):
                break
            offset += len(raw)
            if COMPACT_BOUNDARY in raw:
                event = json.loads(raw)
                meta = event.get("compactMetadata") or {}
                found.append({"at": event.get("timestamp"), "session": event.get("sessionId"), "trigger": meta.get("trigger"), "pre_tokens": meta.get("preTokens"), "post_tokens": meta.get("postTokens")})
    return found, offset


def compactions_of(checkout: str, sessions: list[str]) -> list[dict]:
    found = []
    for session in sessions:
        path = tasks.transcript_of(checkout, session)
        if not path.exists():
            continue
        offset, seen = COMPACTIONS.get(session, (0, []))
        if path.stat().st_size < offset:
            offset, seen = 0, []
        fresh, offset = compactions_in(path, offset)
        COMPACTIONS[session] = (offset, seen := seen + fresh)
        found += seen
    return found


@component("compactions", "Compactions", every="2m")
def compactions(ctx: Context, *, checkout: str, sessions: list[str]) -> Table:
    """Every compaction in the root sessions' transcripts, newest first, with tokens before and after."""
    found = sorted(compactions_of(checkout, sessions), key=lambda item: item["at"] or "", reverse=True)
    rows = [item | {"key": item["at"], "cite": f"compaction:{item['at']}"} for item in found]
    return Table([Col("at", "When", "age"), Col("trigger", "Trigger", "badge"), Col("pre_tokens", "Before", "num"), Col("post_tokens", "After", "num")], rows, note=None if rows else "No compactions yet.")


@component("watches", "Watch files", every="1m")
def watches(ctx: Context, *, state_dir: Path) -> Table:
    """Beat and watch-state files under the drive's state dir and inbox: their age and pending count."""
    inbox_dir = state_dir / "inbox"
    rows = []
    for path in sorted([*inbox_dir.glob(".*.beat"), *state_dir.glob("*.beat"), *inbox_dir.glob(".*.json")]):
        stat = path.stat()
        payload = json.loads(path.read_text()) if path.suffix == ".json" else None
        rows.append({"key": path.name, "cite": f"watch:{path.name}", "name": path.name, "at": inboxlines.iso(inboxlines.epoch(stat.st_mtime)), "pending": len(payload.get("pending", [])) if isinstance(payload, dict) else None})
    return Table([Col("name", "File"), Col("at", "Touched", "age"), Col("pending", "Pending", "num")], rows, note=None if rows else "No watch files.")


@component("drive", "Drive", every="2m")
def drive(ctx: Context, *, checkout: str, sessions: list[str], state_dir: Path, ledger: str | None = None) -> Kv:
    """The drive's registry facts with counts: open ledger PRs, open root tasks, compactions and inbox lines."""
    found, directory = tasks.session_tasks(checkout, sessions)
    opened = sum(1 for key, fields in ledgers.rows(ctx, ledger).items() if key.isdigit() and fields.get("state", "open") == "open") if ledger else None
    history = compactions_of(checkout, sessions)
    return Kv(
        {
            "program": ctx.facts.get("program"),
            "drive": ctx.facts["id"],
            "repo": ctx.facts.get("repo"),
            "ledger": ledger,
            "cci drive": ctx.facts.get("cci_drive"),
            "Orca run": ctx.facts.get("orca_run"),
            "open PRs in the ledger": opened,
            "open tasks": sum(1 for task in found if task.get("status") in ("in_progress", "pending")),
            "task list": str(directory) if directory else None,
            "compactions": len(history),
            "last compaction": max((item["at"] for item in history if item["at"]), default=None),
            "inbox lines": len(inbox(ctx, state_dir)),
            "sessions": ", ".join(sessions),
            "started": ctx.facts.get("started_at"),
        }
    )
