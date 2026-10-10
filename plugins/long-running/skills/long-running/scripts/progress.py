"""Fold a progress record's root narrative so each dump replaces the last.

The narrative is a run of ``##`` sections. The last one that is not binding is the
current dump and stays whole, ``###`` addenda included. A ``#`` title before the first
section is dropped; other text there folds like an earlier dump. A ``##`` section without
``###`` parts, or a ``###`` part, whose heading says ``binding`` or ``byte-for-byte``,
names rulings ``verbatim``, or names owner rules or open items, is binding: it moves under
:data:`CARRIED` once, byte for byte. Every earlier dump keeps its heading, as ``###``
under :data:`FOLDED`, over one dated line that names the rest of its parts and their
opening sentences.

A record over :data:`CAP` bytes is refused with the largest section named.
Only the Python standard library is used.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

CARRIED = "## Carried binding sections"
FOLDED = "## Folded narrative"
BINDING = re.compile(r"binding|byte-for-byte|ruling.*verbatim|verbatim.*ruling|owner rules|\bopen\b.*\bitems\b", re.IGNORECASE)
CAP = 40_000
DIGEST_CHARS = 240
OPENING_CHARS = 120
SENTENCE = re.compile(r"(?<=[.!?])\s")
MARKUP = re.compile(r"^[\s>*_#-]*(?:\d+\.\s+)?")
TITLE = re.compile(r"^# .*\n?", re.MULTILINE)
QUALIFIER = re.compile(r" \(| — |; |\. ")
FENCE = re.compile(r"^(`{3,}|~{3,})", re.MULTILINE)
DIGEST = re.compile(r"^- \d{4}-\d{2}-\d{2} \d{2}:\d{2}Z, ")


@dataclass(frozen=True)
class Section:
    heading: str
    body: str

    @property
    def title(self) -> str:
        return self.heading.lstrip("#").strip()

    @property
    def label(self) -> str:
        return QUALIFIER.split(self.title, maxsplit=1)[0]

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


def binding(section: Section) -> bool:
    return BINDING.search(section.title) is not None


def opening(text: str) -> str:
    lines = [MARKUP.sub("", line).strip() for line in text.splitlines()]
    prose = " ".join(line for line in lines if line)
    first = SENTENCE.split(prose, maxsplit=1)[0]
    return first if len(first) <= OPENING_CHARS else first[: OPENING_CHARS - 1] + "…"


def digest(parts: list[Section], lead: str, when: datetime, label: str = "") -> str:
    named = ([opening(lead)] if lead.strip() else []) + [f"{part.label}: {opening(part.body)}" for part in parts]
    line = f"- {when:%Y-%m-%d %H:%MZ}, {label}{'; '.join(named)}"
    return line if len(line) <= DIGEST_CHARS else line[: DIGEST_CHARS - 1] + "…"


def fold(narrative: str, when: datetime, history: str) -> str:
    preamble, sections = split(narrative.strip("\n") + "\n", 2)
    if not sections:
        return narrative.strip("\n")
    carried: list[Section] = []
    digests: list[str] = []
    folded: list[Section] = []
    preamble = TITLE.sub("", preamble)
    dumps = [Section("", preamble)] if preamble.strip() else []
    for section in sections:
        if section.heading == CARRIED:
            carried += split(section.body, 3)[1]
        elif section.heading == FOLDED:
            lead, parts = split(section.body, 3)
            digests += [line for line in lead.splitlines() if line.startswith("- ")]
            folded += parts
        elif binding(section) and not split(section.body, 3)[1]:
            carried.append(Section(f"#{section.heading}", section.body))
        else:
            dumps.append(section)
    current = dumps.pop() if dumps else None
    for dump in dumps:
        lead, parts = split(dump.body, 3)
        carried += [part for part in parts if binding(part)]
        rest = [part for part in parts if not binding(part)]
        if dump.heading:
            folded.append(Section(f"#{dump.heading}", f"{digest(rest, lead, when)}\n" if lead.strip() or rest else ""))
        else:
            digests.append(digest(rest, lead, when, "untitled narrative: "))
    kept = {part.text for part in split(current.body, 3)[1]} if current else set()
    unique = []
    for part in carried:
        if part.text not in kept:
            kept.add(part.text)
            unique.append(part)
    out = [current.text] if current and not current.heading else []
    if unique:
        out.append("\n\n".join([CARRIED, *(part.text for part in unique)]))
    if digests or folded:
        listed = ["\n".join(digests)] if digests else []
        out.append("\n\n".join([FOLDED, f"Full text of each folded dump: {history}.", *listed, *(part.text for part in folded)]))
    if current and current.heading:
        out.append(current.text)
    return "\n\n".join(out)


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
