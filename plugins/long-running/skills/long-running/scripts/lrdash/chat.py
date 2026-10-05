from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import ledger

UPSTREAM = "https://api.cerebras.ai/v1/chat/completions"
MODEL = "gpt-oss-120b"
KEY_ENV = "CEREBRAS_API_KEY"
USER_AGENT = "lr-dashboard"
UPSTREAM_TIMEOUT_SECONDS = 300
CHUNK_BYTES = 8192
HIT_LIMIT = 25
HIT_CHARS = 320
READ_CHARS = 12000
CONTEXT_LINES = 12
DIGEST_ROWS = 12
REF = re.compile(r"^(?P<kind>inbox|ccn|pr|ledger|task|board|file):(?P<ident>.+)$")
FILE_LINE = re.compile(r"^(?P<path>.+?)(?::(?P<line>\d+))?$")
TERM = re.compile(r"\S+")
SETTLED_ASKS = frozenset({ledger.ASK_LIVE, ledger.ASK_DROPPED, ledger.ASK_ANSWERED})


@dataclass(frozen=True)
class Hit:
    ref: str
    at: str | None
    text: str

    def row(self) -> dict:
        return {"ref": self.ref, "at": self.at, "text": self.text[:HIT_CHARS]}


@dataclass
class Sources:
    state: dict
    state_dir: Path
    inbox: Callable[[], list]
    tasks: Callable[[], list[dict]]
    ledger_rows: Callable[[], dict[str, dict]]
    ccn: Callable[..., str]

    @property
    def inbox_dir(self) -> Path:
        return self.state_dir / "inbox"


def key() -> str | None:
    return os.environ.get(KEY_ENV)


def site_config(origin: str, token: str) -> dict:
    return {"endpoint": f"{origin}/ai", "model": MODEL, "key": token}


def relay(body: bytes) -> urllib.request.Request:
    return urllib.request.Request(UPSTREAM, data=body, method="POST", headers={"Content-Type": "application/json", "Authorization": f"Bearer {key()}", "User-Agent": USER_AGENT})


def stream(response) -> Iterator[bytes]:
    while chunk := response.read1(CHUNK_BYTES):
        yield chunk


def terms(query: str) -> list[str]:
    return [term.lower() for term in TERM.findall(query)]


def matches(text: str, wanted: list[str]) -> bool:
    lowered = text.lower()
    return all(term in lowered for term in wanted)


def corpus(sources: Sources) -> Iterator[Hit]:
    for line in sources.inbox():
        yield Hit(f"inbox:{line.file}:{line.number}", line.row()["at"], line.text)
    for task in sources.tasks():
        yield Hit(f"task:{task['id']}", task.get("updated_at"), f"{task.get('status')} {task.get('owner') or ''} {task.get('subject')} {task.get('description') or ''}")
    for name, fields in sources.ledger_rows().items():
        ref = f"pr:{name}" if name.isdigit() else f"ledger:{name}"
        yield Hit(ref, fields.get("landed_at") or fields.get("asked_at"), " ".join(f"{field}={value}" for field, value in fields.items()))
    for board in sources.state.get("boards") or []:
        yield Hit(f"board:{board['slug']}", None, f"{board['status']} {board['subject']} {board['slug']} {board['url']}")


def search(sources: Sources, query: str) -> list[dict]:
    wanted = terms(query)
    if not wanted:
        return []
    hits = [hit for hit in corpus(sources) if matches(hit.text + " " + hit.ref, wanted)]
    hits.sort(key=lambda hit: hit.at or "", reverse=True)
    found = [hit.row() for hit in hits[:HIT_LIMIT]]
    for result in json.loads(sources.ccn("search", query, "--json", "--limit", str(HIT_LIMIT // 2)) or "[]"):
        record = result[result["kind"]]
        found.append({"ref": f"ccn:{record['id'][:8]}", "at": record.get("updated_at"), "text": f"{result['kind']}: {record.get('title')}"})
    return found


def excerpt(directory: Path, name: str, line: int | None, context: int) -> str:
    lines = (directory / name).read_text(errors="replace").splitlines()
    target = line or len(lines)
    start = max(target - context, 1)
    return "\n".join(f"{'>' if number == target else ' '} {number:>6}  {lines[number - 1]}" for number in range(start, min(target + context, len(lines)) + 1)) + "\n"


def inside(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise LookupError(f"no file {relative} under {root}")
    return path


def read(sources: Sources, ref: str) -> str:
    if not (match := REF.match(ref.strip())):
        raise LookupError(f"{ref} is not a ref; use inbox:, ccn:, pr:, ledger:, task:, board: or file:")
    kind, ident = match["kind"], match["ident"]
    if kind == "inbox":
        name, _, number = ident.rpartition(":")
        inside(sources.inbox_dir, name)
        return excerpt(sources.inbox_dir, name, int(number), CONTEXT_LINES)
    if kind == "file":
        parts = FILE_LINE.match(ident)
        path = inside(sources.state_dir, parts["path"])
        if parts["line"]:
            return excerpt(path.parent, path.name, int(parts["line"]), CONTEXT_LINES * 4)
        return path.read_text(errors="replace")[:READ_CHARS]
    if kind == "ccn":
        return sources.ccn("show", ident)[:READ_CHARS]
    if kind in ("pr", "ledger"):
        rows = sources.ledger_rows()
        if ident not in rows:
            raise LookupError(f"the ledger has no row {ident}")
        return json.dumps({"key": ident, **rows[ident]}, indent=1)[:READ_CHARS]
    if kind == "task":
        task = next((task for task in sources.tasks() if str(task["id"]) == ident), None)
        if task is None:
            raise LookupError(f"no task {ident}")
        return json.dumps(task, indent=1)[:READ_CHARS]
    board = next((board for board in sources.state.get("boards") or [] if board["slug"] == ident), None)
    if board is None:
        raise LookupError(f"no board {ident}")
    return json.dumps(board, indent=1)


def section(state: dict, name: str | None) -> str:
    if not name:
        return json.dumps({key: type(value).__name__ for key, value in state.items()})
    if name not in state:
        raise LookupError(f"the dashboard state has no section {name}; sections: {', '.join(state)}")
    return json.dumps(state[name], default=str)[:READ_CHARS]


def digest(state: dict) -> str:
    drive = state.get("drive") or {}
    book = state.get("ledger") or {}
    tasks = state.get("tasks") or {}
    compactions = state.get("compactions") or {}
    out = [
        f"drive {drive.get('program')} ({drive.get('drive')}), repo {drive.get('repo')}, state generated {state.get('generated_at')}",
        f"ledger counts {json.dumps(book.get('counts') or {})}, last live {book.get('live_at')}",
        f"compactions {compactions.get('count')}, last {compactions.get('last_at')}",
        f"read errors {json.dumps(state.get('errors') or {})}",
        "",
        "open owner asks (ledger):",
        *(f"- ledger:{ask['key']} {ask['state']} {ask.get('lane')}: {(ask.get('text') or '')[:200]}" for ask in (book.get("asks") or []) if ask["state"] not in SETTLED_ASKS),
        "",
        "open tasks:",
        *(f"- task:{task['id']} {task.get('status')} {task.get('owner') or ''}: {task.get('subject')}" for task in (tasks.get("open") or [])[: DIGEST_ROWS * 2]),
        "",
        "open boards:",
        *(f"- board:{board['slug']} {board['url']}" for board in state.get("boards") or [] if board["status"] == "open"),
        "",
        "lanes, most recent first:",
        *(f"- {lane['lane']} idle {lane['idle_minutes']}m: {((lane.get('last') or {}).get('text') or '')[:160]}" for lane in (state.get("lanes") or [])[: DIGEST_ROWS * 2]),
        "",
        "latest census lines:",
        *(f"- inbox:{row['file']}:{row['line']} {row['text'][:300]}" for row in (state.get("census") or [])[:3]),
        "",
        f"state sections for the state tool: {', '.join(state)}",
    ]
    return "\n".join(out)


def run_ccn(checkout: str) -> Callable[..., str]:
    def call(*args: str) -> str:
        return subprocess.run(["ccn", "-R", checkout, *args], capture_output=True, text=True, check=True, timeout=30).stdout

    return call
