"""Owner rules as checked rows: a predicate reads the diff's hunks, never the tree after the change.

A rule anchored on a symbol is met, broken or untouched by what the hunks add, change or
delete; a symbol the diff removes is a deletion, not a change that left it alone. The three
classes of change that land without a tap are predicates here too: a diff that touches only
tests and CI, and a diff that is empty against its base.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fnmatch import fnmatch

FILE = re.compile(r"^diff --git a/(?P<a>\S+) b/(?P<b>\S+)$", re.MULTILINE)
TEST_PATHS = ("*/tests/*", "*/test/*", "*_test.go", "*_test.py", "*.test.ts", "*.test.tsx", "*.spec.ts", "test_*.py", "*/__tests__/*", "*/testdata/*", ".github/*", "*/ci/*", "*.ci.yml")


@dataclass(frozen=True)
class Change:
    path: str
    removed: tuple[str, ...]
    added: tuple[str, ...]


def parse(diff: str) -> list[Change]:
    out = []
    for text in re.split(r"(?m)^(?=diff --git )", diff):
        if not (found := FILE.match(text)):
            continue
        body = text.split("\n", 1)[1] if "\n" in text else ""
        lines = [line for line in body.splitlines() if not line.startswith(("---", "+++"))]
        removed = tuple(line[1:] for line in lines if line.startswith("-"))
        added = tuple(line[1:] for line in lines if line.startswith("+"))
        out.append(Change(found["b"] if found["b"] != "/dev/null" else found["a"], removed, added))
    return out


def touch(changes: list[Change], symbol: str) -> str:
    """How the diff treats `symbol`: `deletes` when lines naming it go and none come back, `adds` when the reverse, `changes` when both, `untouched` when neither."""
    word = re.compile(rf"\b{re.escape(symbol)}\b")
    removed = sum(1 for change in changes for line in change.removed if word.search(line))
    added = sum(1 for change in changes for line in change.added if word.search(line))
    if removed and not added:
        return "deletes"
    if added and not removed:
        return "adds"
    return "changes" if added else "untouched"


def test_only(changes: list[Change]) -> bool:
    return bool(changes) and all(any(fnmatch(change.path, pattern) or fnmatch(f"/{change.path}", pattern) for pattern in TEST_PATHS) for change in changes)


def empty(changes: list[Change]) -> bool:
    return not any(change.removed or change.added for change in changes)


def symbol_kept(changes: list[Change], symbol: str) -> bool:
    return touch(changes, symbol) != "deletes"


PREDICATES = {"test-only": test_only, "empty-diff": empty, "symbol-kept": symbol_kept}


@dataclass(frozen=True)
class Check:
    rule: str
    met: bool
    how: str


def check(rule_id: str, check_ref: str, changes: list[Change]) -> Check:
    """Run the rule row's predicate, `<name>` or `<name>:<argument>`, over the diff and say in words what the diff did."""
    name, _, argument = check_ref.partition(":")
    predicate = PREDICATES[name]
    if name == "symbol-kept":
        what = touch(changes, argument)
        return Check(rule_id, predicate(changes, argument), f"{what} {argument}")
    met = predicate(changes)
    return Check(rule_id, met, {"test-only": "touches only tests and CI" if met else "touches " + ", ".join(sorted(c.path for c in changes if not test_only([c]))[:3]), "empty-diff": "changes nothing against its base" if met else "changes files against its base"}[name])
