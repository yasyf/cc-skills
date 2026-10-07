from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from livedash import payloads

UPSTREAM = "https://api.cerebras.ai/v1/chat/completions"
MODEL = "gpt-oss-120b"
KEY_ENV = "CEREBRAS_API_KEY"
USER_AGENT = "live-dashboard"
UPSTREAM_TIMEOUT_SECONDS = 300
CHUNK_BYTES = 8192
HIT_LIMIT = 25
HIT_CHARS = 320
READ_CHARS = 12000
CONTEXT_LINES = 12
DIGEST_ROWS = 6
DIGEST_CHARS = 220
REF = re.compile(r"^(?P<kind>[a-z_]+):(?P<ident>.+)$")
FILE_LINE = re.compile(r"^(?P<path>.+?)(?::(?P<line>\d+))?$")
TERM = re.compile(r"\S+")
ROW_LISTS = ("rows", "entries", "items", "tiles", "dists", "nodes")


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
    ccn: Callable[..., str] | None

    @property
    def inbox_dir(self) -> Path:
        return self.state_dir / "inbox"


def key() -> str | None:
    return os.environ.get(KEY_ENV)


def site_config(token: str) -> dict:
    return {"model": MODEL, "key": token}


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


def records(sources: Sources) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for card in sources.state.get("cards") or []:
        payload = card.get("payload") or {}
        for name in ROW_LISTS:
            for row in payload.get(name) or []:
                if isinstance(row, dict) and (ref := row.get("cite")) and ref not in found:
                    found[ref] = row | {"card": card["id"]}
    return found


def corpus(sources: Sources) -> Iterator[Hit]:
    for ref, row in records(sources).items():
        yield Hit(ref, row.get("at"), " ".join(str(value) for value in row.values() if isinstance(value, (str, int)) and not isinstance(value, bool)))


def search(sources: Sources, query: str) -> list[dict]:
    wanted = terms(query)
    if not wanted:
        return []
    hits = [hit for hit in corpus(sources) if matches(hit.text + " " + hit.ref, wanted)]
    hits.sort(key=lambda hit: hit.at or "", reverse=True)
    found = [hit.row() for hit in hits[:HIT_LIMIT]]
    for result in json.loads(sources.ccn("search", query, "--json", "--limit", str(HIT_LIMIT // 2)) or "[]") if sources.ccn else []:
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
        raise LookupError(f"{ref} is not a ref; use card:, inbox:, ccn:, file: or a cite copied from a card")
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
    if kind == "ccn" and sources.ccn:
        return sources.ccn("show", ident)[:READ_CHARS]
    if kind == "card":
        return card(sources.state, ident)
    row = records(sources).get(f"{kind}:{ident}")
    if row is None:
        raise LookupError(f"no record {kind}:{ident}; read a ref copied from the digest or a search result")
    return json.dumps(row, indent=1, default=str)[:READ_CHARS]


def card(state: dict, ident: str | None) -> str:
    shown = state.get("cards") or []
    if not ident:
        return json.dumps([{"id": item["id"], "section": item["section"], "title": item["title"], "kind": item["kind"], "status": item["status"]} for item in shown])
    found = next((item for item in shown if item["id"] == ident), None)
    if found is None:
        raise LookupError(f"the dashboard has no card {ident}; cards: {', '.join(item['id'] for item in shown)}")
    return json.dumps(found, default=str)[:READ_CHARS]


def summary(item: dict) -> list[str]:
    if item.get("error"):
        return [f"error: {item['error']}"]
    if not item.get("payload"):
        return [f"{item['status']}: no data yet"]
    lines = payloads.markdown(item["kind"], item["payload"]).splitlines()
    return [line[:DIGEST_CHARS] for line in lines[:DIGEST_ROWS + 2]] + ([f"... read card:{item['id']} for all {len(lines)} lines"] if len(lines) > DIGEST_ROWS + 2 else [])


def digest(state: dict) -> str:
    dashboard = state.get("dashboard") or {}
    out = [
        f"dashboard {dashboard.get('title')} ({dashboard.get('id')}), repo {dashboard.get('repo')}, state generated {state.get('generated_at')}",
        f"layout error: {state['layout_error']}" if state.get("layout_error") else "layout: current",
    ]
    for item in state.get("cards") or []:
        out += ["", f"card:{item['id']} ({item['section']} / {item['title']}, {item['kind']}, {item['status']}, as of {item['as_of']}):", *summary(item)]
    return "\n".join(out)


def run_ccn(checkout: str) -> Callable[..., str]:
    def call(*args: str) -> str:
        return subprocess.run(["ccn", "-R", checkout, *args], capture_output=True, text=True, check=True, timeout=30).stdout

    return call
