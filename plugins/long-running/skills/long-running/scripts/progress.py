"""Cap the generated sections of a progress record.

A record whose generated sections run over :data:`CAP` bytes is refused with the largest
section named. The root's narrative is never measured, folded, or truncated.
Only the Python standard library is used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CAP = 40_000
FENCE = re.compile(r"^(`{3,}|~{3,})", re.MULTILINE)


@dataclass(frozen=True)
class Section:
    heading: str
    body: str

    @property
    def title(self) -> str:
        return self.heading.lstrip("#").strip()

    @property
    def text(self) -> str:
        return f"{self.heading}\n{self.body}".strip("\n") if self.heading else self.body.strip("\n")


def fences(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    opened = None
    for match in FENCE.finditer(text):
        if opened is None:
            opened = match
        elif match[1][0] == opened[1][0] and len(match[1]) >= len(opened[1]):
            spans.append((opened.start(), match.end()))
            opened = None
    return spans + ([(opened.start(), len(text))] if opened else [])


def split(text: str, level: int) -> tuple[str, list[Section]]:
    heading = re.compile(rf"^{'#' * level} (?!#).*$", re.MULTILINE)
    spans = fences(text)
    starts = [match for match in heading.finditer(text) if not any(start < match.start() < end for start, end in spans)]
    if not starts:
        return text, []
    sections = [
        Section(match[0], text[match.end() + 1 : following.start() if following else len(text)])
        for match, following in zip(starts, [*starts[1:], None], strict=True)
    ]
    return text[: starts[0].start()], sections


def oversized(markdown: str) -> str | None:
    size = len(markdown.encode())
    if size <= CAP:
        return None
    preamble, sections = split(markdown, 2)
    candidates = [Section("", preamble)] + sections if preamble.strip() else sections
    largest = max(candidates, key=lambda section: len(section.text.encode()))
    _, parts = split(largest.body, 3)
    inner = max(parts, key=lambda part: len(part.text.encode()), default=None)
    within = f", most of it `{inner.title}` at {len(inner.text.encode())} bytes" if inner else ""
    return (
        f"progress record is {size} bytes, over the {CAP}-byte cap: its largest section is `{largest.title or 'text before the first section'}` at "
        f"{len(largest.text.encode())} bytes{within}; trim that section, then write the record again"
    )
