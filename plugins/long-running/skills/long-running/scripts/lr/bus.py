"""The typed bus over cci: twelve record kinds, each a JSON body checked before it is posted.

A record is refused at write time when its kind is unknown, a field is missing, extra or
of the wrong shape, it names more than one PR, or it is addressed to an audience the kind
does not reach; the refusal prints the corrected command. Posting the same record twice
is one record: the identity fields become cci's idempotency key. Readers get typed
:class:`Record` values from a cursor and never parse free text.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

NAMESPACE = "lr"
KINDS: dict[str, dict] = json.loads((Path(__file__).parent / "kinds.json").read_text())
SHA = re.compile(r"^[0-9a-f]{40}$")
TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
PAGE_ROWS = 500
PAGE_BYTES = 4_000_000


class Refused(ValueError):
    """A post the bus would not make; the message carries the corrected command."""


@dataclass(frozen=True)
class Record:
    seq: int
    at: datetime
    kind: str
    lane: str
    to: tuple[str, ...]
    body: dict

    def __getattr__(self, name: str):
        try:
            return self.body[name]
        except KeyError:
            raise AttributeError(name) from None


def command(kind: str, body: dict, to: tuple[str, ...] = ()) -> str:
    fields = " ".join(f"--{name} {json.dumps(value) if not isinstance(value, str) else value}" for name, value in body.items())
    return f"lr post {kind} {fields}" + "".join(f" --to {lane}" for lane in to)


def check(kind: str, body: dict, to: tuple[str, ...]) -> None:
    if kind not in KINDS:
        raise Refused(f"unknown kind {kind!r}; the kinds are {', '.join(KINDS)}")
    spec = KINDS[kind]
    required, optional = spec["fields"], spec.get("optional", {})
    given = set(body)
    if given != set(required) and not set(required) <= given <= set(required) | set(optional):
        for other, other_spec in KINDS.items():
            if other != kind and set(other_spec["fields"]) <= given <= set(other_spec["fields"]) | set(other_spec.get("optional", {})):
                raise Refused(f"these fields are a {other} record, not {kind}; run: {command(other, body, to)}")
        missing = sorted(set(required) - given)
        extra = sorted(given - set(required) - set(optional))
        raise Refused(f"{kind} takes {', '.join(required)}" + (f"; missing {', '.join(missing)}" if missing else "") + (f"; unexpected {', '.join(extra)}" if extra else ""))
    for name, value in body.items():
        shape = required.get(name) or optional[name]
        if name == "pr" and isinstance(value, (list, tuple)):
            raise Refused(f"a {kind} names one PR; post one record per PR: " + " && ".join(command(kind, {**body, "pr": pr}, to) for pr in value))
        if not fits(shape, value):
            raise Refused(f"{kind}.{name} must be {describe(shape)}, got {value!r}")
    audience = spec["audience"]
    if audience != "any":
        for lane in to:
            if lane not in audience:
                raise Refused(f"a {kind} record never reaches {lane}; it goes to {', '.join(audience)}" + (f"; to tell {lane}, post an escalation, an ask or a decision" if lane == "root" else ""))


def fits(shape, value) -> bool:
    if isinstance(shape, list):
        return value in shape
    return {"int": lambda v: isinstance(v, int) and not isinstance(v, bool), "str": lambda v: isinstance(v, str) and bool(v.strip()), "sha": lambda v: isinstance(v, str) and bool(SHA.match(v)), "time": lambda v: isinstance(v, str) and bool(TIME.match(v))}[shape](value)


def describe(shape) -> str:
    return "one of " + ", ".join(shape) if isinstance(shape, list) else {"int": "an integer", "str": "non-empty text", "sha": "a full 40-character sha", "time": "a UTC time like 2026-10-31T06:00:00Z"}[shape]


def identity(kind: str, body: dict) -> str:
    return "/".join([f"{NAMESPACE}.{kind}", *(str(body[name]) for name in KINDS[kind]["identity"])])


def summary(kind: str, body: dict) -> str:
    parts = [kind, *(f"#{body['pr']}" if name == "pr" else str(value)[:12] if name == "head" else str(value) for name, value in body.items() if name != "text")]
    return " ".join(parts) + (f": {body['text']}" if "text" in body else "")


def argv(drive: str, lane: str, kind: str, body: dict, to: tuple[str, ...], body_file: str) -> list[str]:
    out = ["cci", "post", "--drive", drive, "--lane", lane, "--kind", f"{NAMESPACE}.{kind}", "--body-file", body_file, "--text", summary(kind, body), "--json"]
    if identity(kind, body) != f"{NAMESPACE}.{kind}":
        out += ["--id", identity(kind, body)]
    if "pr" in body:
        out += ["--pr", str(body["pr"])]
    if "re" in body:
        out += ["--re", str(body["re"])]
    for recipient in to:
        out += ["--to", recipient]
    return out


class Bus:
    def __init__(self, drive: str, lane: str, run: Callable[[list[str]], str] = lambda argv: subprocess.run(argv, capture_output=True, text=True, check=True).stdout):
        self.drive = drive
        self.lane = lane
        self.run = run

    def post(self, kind: str, to: tuple[str, ...] | list[str] = (), **body) -> Record:
        to = tuple(to)
        check(kind, body, to)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete_on_close=False) as handle:
            json.dump(body, handle)
            handle.close()
            stored = json.loads(self.run(argv(self.drive, self.lane, kind, body, to, handle.name)))
        return parse(stored)

    def read(self, since: int, kinds: tuple[str, ...] = tuple(KINDS)) -> tuple[list[Record], list[dict]]:
        """Every typed record after `since`, oldest first, and the raw records whose body the bus would have refused."""
        filters = [f"--kind={NAMESPACE}.{kind}" for kind in kinds]
        rows: list[dict] = []
        while True:
            out = self.run(["cci", "tail", "--drive", self.drive, "--since", str(since), "--json", "--budget", str(PAGE_BYTES), *filters])
            page = [json.loads(line) for line in out.splitlines() if line.strip()]
            rows += page
            if len(page) < PAGE_ROWS and len(out.encode()) < PAGE_BYTES // 2:
                break
            since = page[-1]["seq"]
        records, rejected = [], []
        for row in rows:
            try:
                records.append(parse(row))
            except (Refused, KeyError, TypeError, json.JSONDecodeError):
                rejected.append(row)
        return records, rejected


def parse(row: dict) -> Record:
    kind = row["kind"].removeprefix(f"{NAMESPACE}.")
    body = row["body"] if isinstance(row["body"], dict) else json.loads(row["body"])
    to = tuple(row.get("to") or ())
    check(kind, body, to)
    return Record(row["seq"], datetime.fromisoformat(row["at"]).astimezone(UTC), kind, row["lane"], to, body)
