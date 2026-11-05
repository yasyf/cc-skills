from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass

Run = Callable[[list[str]], str]

GLOB = re.compile(r"[*?\[]")
TOPICS = "answer-topics"
MATCHED = 10


def matched(ccn: Run, text: str) -> list[dict]:
    return json.loads(ccn(["answer", "match", "--label", "scope:durable", "--limit", str(MATCHED), "--json", "--", text]) or "[]") if text.strip() else []


def own_words(text: str, siblings: Iterable[str]) -> str:
    carried = {line for sibling in siblings for line in sibling.splitlines()}
    return "\n".join(line for line in text.splitlines() if line not in carried)


def roots(paths: Iterable[str]) -> list[str]:
    fixed = ("/".join(parts[: next((index for index, part in enumerate(parts) if GLOB.search(part)), len(parts))]) for parts in (path.split("/") for path in paths))
    return [root for root in dict.fromkeys(fixed) if root]


def commands(topics: Iterable[str], paths: Iterable[str]) -> list[str]:
    return [*(f"ccn answer search {topic}" for topic in topics), *(f"ccn relevant {root}" for root in roots(paths))]


def hits(ccn: Run, topics: Iterable[str], paths: Iterable[str], base: str) -> list[dict]:
    found: dict[str, dict] = {}
    for topic in topics:
        for answer in json.loads(ccn(["answer", "search", topic, "--limit", "0", "--json"]) or "[]"):
            found.setdefault(answer["id"], answer)
    for root in roots(paths):
        for item in json.loads(ccn(["relevant", root, "--attached", "--limit", "0", *(("--base", base) if base else ()), "--json"]) or "[]"):
            if item["kind"] == "answer":
                found.setdefault(item["answer"]["id"], item["answer"])
    return sorted(found.values(), key=lambda answer: answer["updated_at"], reverse=True)


@dataclass(frozen=True)
class Topic:
    slug: str
    words: str
    dirs: tuple[str, ...]

    def names(self, text: str) -> bool:
        return re.search(self.words, text, re.IGNORECASE) is not None

    def covers(self, path: str) -> bool:
        return any(path == root or path.startswith(f"{root}/") or root.startswith(f"{path}/") for root in self.dirs)

    def flags(self, verb: str = "") -> list[str]:
        return [f"--{verb}label", f"topic:{self.slug}", *(flag for root in self.dirs for flag in (f"--{verb}dir", root))]


def vocabulary_ledger(ccn: Run, program: str) -> str | None:
    found = json.loads(ccn(["ledger", "list", "--label", TOPICS, "--label", f"program:{program}", "--json"]) or "[]")
    return max(found, key=lambda ledger: ledger["updated_at"])["id"] if found else None


def vocabulary(ccn: Run, program: str) -> list[Topic]:
    if not (ledger := vocabulary_ledger(ccn, program)):
        return []
    rows = json.loads(ccn(["ledger", "show", ledger, "--json"]))["rows"]
    return [Topic(row["key"], row["fields"]["words"], tuple(json.loads(row["fields"].get("dirs") or "[]"))) for row in rows]


def named(topics: Iterable[Topic], text: str = "", paths: Iterable[str] = ()) -> list[Topic]:
    paths = list(paths)
    return [topic for topic in topics if (text and topic.names(text)) or any(topic.covers(path) for path in paths)]


def anchors(topics: Iterable[Topic], text: str) -> list[str]:
    return [flag for topic in named(topics, text) for flag in topic.flags()]
