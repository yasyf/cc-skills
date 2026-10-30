from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from pathlib import Path

Run = Callable[[list[str]], str]
Lock = Callable[[Path], AbstractContextManager[None]]
UNANCHORED = frozenset({"pending", "none", "all"})
BINDING_DIRECTIONS = frozenset({"removes", "requires"})
NOT_RULINGS = ("corpus/", "no/")
SLICE_LINE = "Register slice:"
LEDGER_REFS = "refs/cc-notes/ledgers/"


def program_of(entry: dict) -> str:
    return entry.get("program") or Path(entry["state_dir"]).name


def ccn(run: Run, repo: str, *args: str) -> object:
    return json.loads(run(["ccn", "-R", repo, *args, "--json"]) or "null")


def register(run: Run, repo: str, program: str) -> dict | None:
    docs = ccn(run, repo, "doc", "list", "--label", f"standing-rules:{program}", "--full") or []
    if not docs:
        return None
    newest = max(docs, key=lambda doc: doc["updated_at"])
    return {"id": newest["id"], "body": newest["body"]}


def ledger_of(run: Run, repo: str, program: str) -> str:
    found = ccn(run, repo, "ledger", "list", "--label", "register", "--label", f"program:{program}") or []
    if not found:
        raise SystemExit(f"no register ledger labelled register and program:{program} in {repo}")
    return max(found, key=lambda entry: entry["updated_at"])["id"]


def active_register(run: Run, repo: str, kept: Path) -> dict | None:
    if not kept.is_file() or (cached := json.loads(kept.read_text()))["repo"] != repo:
        return None
    shown = ccn(run, repo, "ledger", "show", cached["ledger"])
    return shown if shown["status"] == "active" else None


def load(run: Run, lock: Lock, held: Callable[[Path], bool], repo: str, program: str, state: Path, last: bool = False) -> tuple[str, dict]:
    tips = run(["git", "-C", repo, "for-each-ref", "--format=%(refname) %(objectname)", LEDGER_REFS])
    kept = state / f"register-{program}.json"
    if cached := kept_copy(kept, repo, tips):
        return cached["ledger"], cached["rows"]
    if last and held(kept.with_suffix(".lock")) and (cached := kept_copy(kept, repo, None)):
        return cached["ledger"], cached["rows"]
    with lock(kept.with_suffix(".lock")):
        if cached := kept_copy(kept, repo, tips):
            return cached["ledger"], cached["rows"]
        shown = active_register(run, repo, kept) or ccn(run, repo, "ledger", "show", ledger_of(run, repo, program))
        identity = shown["id"]
        rows = {row["key"]: row["fields"] | {"updated_at": row["updated_at"]} for row in shown["rows"]}
        write_atomic(kept, json.dumps({"repo": repo, "tips": tips, "ledger": identity, "rows": rows}, ensure_ascii=False))
    return identity, rows


def live_rows(rows: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    return {key: fields for key, fields in sorted(rows.items()) if not key.startswith(NOT_RULINGS) and fields.get("status") != "superseded"}


def root_slice(program: str, ledger: str, source_rows: dict[str, dict[str, str]]) -> str:
    rows = {key: fields for key, fields in live_rows(source_rows).items() if fields.get("kind") == "scope" or "root" in listed(fields.get("lanes"))}
    lines = [f"{SLICE_LINE} lane=root step=all", f"Program {program}, register ledger {ledger[:12]}. Every scope row and every row naming the root, verbatim."]
    return hashed(lines + section("Scope rows and rows naming the root", rows.items()))


def kept_copy(kept: Path, repo: str, tips: str | None) -> dict | None:
    return cached if kept.is_file() and (cached := json.loads(kept.read_text()))["repo"] == repo and tips in (None, cached["tips"]) else None

def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{os.getpid()}")
    staged.write_text(text)
    staged.replace(path)

def listed(value: str | None) -> list:
    if not value:
        return []
    return json.loads(value) if value.startswith("[") else [value]

def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]

def binding(implication: dict) -> str:
    if implication.get("state") == "struck":
        return "struck"
    if implication.get("direction") in BINDING_DIRECTIONS or implication.get("state") == "ticked":
        return "binds now"
    return "waits for the owner's tick"

def rendered_row(key: str, fields: dict[str, str]) -> list[str]:
    lines = [f"### {key} | {fields.get('kind', '')} | {fields.get('status', '')} | said {fields.get('said') or 1} | {fields.get('at', '')}"]
    lines += [f"> {line}" for line in (fields.get("words") or "").splitlines()]
    lines += [f"> also, {repeat.get('at', '')}: {repeat.get('words', '')}" for repeat in listed(fields.get("repeats"))]
    lines += [f"{label}: {fields[name]}" for label, name in (("Protects", "protects"), ("Does not apply when", "not_when")) if fields.get(name)]
    if implications := listed(fields.get("implications")):
        lines.append("This also means:")
        lines += [f"{item['n']}. {item['text']} ({item['direction']}, {item['state']}, {binding(item)})" for item in implications]
    for label, name in (("Names", "names"), ("Surfaces", "surfaces"), ("Tests", "tests")):
        if values := [str(value) for value in listed(fields.get(name)) if value not in UNANCHORED]:
            lines.append(f"{label}: {', '.join(values)}")
    return lines

def hashed(lines: list[str]) -> str:
    body = "\n".join(lines).rstrip() + "\n"
    return body + f"slice-hash: {digest(body)}\n"

def section(title: str, rows: Iterable[tuple[str, dict[str, str]]]) -> list[str]:
    rows = list(rows)
    lines = ["", f"## {title} ({len(rows)})"]
    for key, fields in rows:
        lines += ["", *rendered_row(key, fields)]
    return lines
