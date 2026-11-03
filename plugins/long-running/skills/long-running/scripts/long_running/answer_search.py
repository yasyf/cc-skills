from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable

Run = Callable[[list[str]], str]

GLOB = re.compile(r"[*?\[]")


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
