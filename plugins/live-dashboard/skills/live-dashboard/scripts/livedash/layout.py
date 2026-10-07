from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from livedash import registry, yamlish

LAYOUT_FILE = "layout.yaml"
TOP_KEYS = {"title", "banner", "sections"}
SECTION_KEYS = {"title", "collapsed", "components"}
CARD_KEYS = {"use", "id", "title", "question", "every", "width", "pinned", "with"}
USE_KEY = re.compile(r"(?:^|[{,\s-])use:\s")
LINE = re.compile(r"\bline (\d+)")
CARD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class LayoutError(ValueError):
    def __init__(self, line: int | None, message: str):
        super().__init__(message)
        self.line = line

    def where(self, path: Path) -> str:
        return f"{path.name}:{self.line}: {self}" if self.line else f"{path.name}: {self}"


@dataclass(frozen=True)
class Card:
    id: str
    use: str
    title: str
    question: str | None
    every: str
    width: int
    pinned: bool
    given: dict
    section: str
    line: int
    spec: registry.Spec | None = None
    error: str | None = None
    bound: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Section:
    title: str
    collapsed: bool
    cards: list[Card]


@dataclass(frozen=True)
class Layout:
    title: str
    banner: str | None
    sections: list[Section]

    @property
    def cards(self) -> list[Card]:
        return [card for section in self.sections for card in section.cards]


def card_lines(text: str) -> list[int]:
    return [number for number, raw in enumerate(text.splitlines(), 1) for _ in USE_KEY.finditer(yamlish.strip_comment(raw))]


def expect(value, kind: type, what: str, line: int | None):
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise LayoutError(line, f"{what} must be a {kind.__name__}, not {value!r}")
    return value


def parse(text: str) -> tuple[str, str | None, list[tuple[str, bool, list[tuple[dict, int]]]]]:
    try:
        document = yamlish.loads(text)
    except yamlish.YamlError as failure:
        found = LINE.search(str(failure))
        raise LayoutError(int(found[1]) if found else None, str(failure)) from failure
    expect(document, dict, "the layout", 1)
    if unknown := set(document) - TOP_KEYS:
        raise LayoutError(1, f"unknown top-level key {', '.join(sorted(unknown))}; use {', '.join(sorted(TOP_KEYS))}")
    lines = card_lines(text)
    count = 0
    sections = []
    for section in expect(document.get("sections"), list, "sections", None):
        expect(section, dict, "a section", None)
        if unknown := set(section) - SECTION_KEYS:
            raise LayoutError(lines[count] if count < len(lines) else None, f"section {section.get('title')!r} has unknown key {', '.join(sorted(unknown))}")
        cards = []
        for card in expect(section.get("components") or [], list, f"section {section.get('title')!r} components", None):
            line = lines[count] if count < len(lines) else None
            expect(card, dict, "a card", line)
            cards.append((card, line))
            count += 1
        sections.append((expect(section.get("title"), str, "a section title", None), bool(section.get("collapsed")), cards))
    return expect(document.get("title"), str, "title", 1), document.get("banner"), sections


def card_of(raw: dict, line: int, section: str, facts: dict, base: Path) -> Card:
    if unknown := set(raw) - CARD_KEYS:
        raise LayoutError(line, f"card has unknown key {', '.join(sorted(unknown))}; use {', '.join(sorted(CARD_KEYS))}")
    use = expect(raw.get("use"), str, "use", line)
    every = raw.get("every")
    if every is not None and (not isinstance(every, str) or every not in registry.CADENCES):
        raise LayoutError(line, f"every must be one of {', '.join(registry.CADENCES)}, not {every!r}")
    width = expect(raw.get("width", 1), int, "width", line)
    if not 1 <= width <= 3:
        raise LayoutError(line, f"width must be 1, 2 or 3, not {width}")
    given = expect(raw.get("with") or {}, dict, "with", line)
    ident = expect(raw.get("id", use), str, "id", line)
    if not CARD_ID.match(ident):
        raise LayoutError(line, f"card id {ident!r} must be letters, digits, dots, dashes or underscores, starting with a letter or digit")
    spec = registry.REGISTRY.get(use)
    if spec is None:
        if use.startswith("local.") and any(name.startswith(registry.LOCAL_PACKAGE) for name in registry.LOAD_ERRORS):
            return Card(ident, use, raw.get("title") or use, raw.get("question"), every or "manual", width, bool(raw.get("pinned")), given, section, line, error=registry.missing(use))
        raise LayoutError(line, registry.missing(use))
    try:
        bound = registry.bind(spec, given, facts, base)
    except registry.BindError as failure:
        raise LayoutError(line, str(failure)) from failure
    return Card(ident, use, raw.get("title") or spec.title, raw.get("question") or spec.question, every or spec.every, width, bool(raw.get("pinned")), given, section, line, spec, bound=bound)


def build(text: str, facts: dict, base: Path) -> Layout:
    title, banner, raw_sections = parse(text)
    seen: dict[str, int] = {}
    sections = []
    for name, collapsed, raw_cards in raw_sections:
        cards = []
        for raw, line in raw_cards:
            card = card_of(raw, line, name, facts, base)
            if card.id in seen:
                raise LayoutError(line, f"card id {card.id!r} repeats line {seen[card.id]}; give one an `id:`")
            seen[card.id] = line
            cards.append(card)
        sections.append(Section(name, collapsed, cards))
    return Layout(title, banner, sections)


def load(directory: Path, facts: dict) -> Layout:
    return build((directory / LAYOUT_FILE).read_text(), facts, directory)
