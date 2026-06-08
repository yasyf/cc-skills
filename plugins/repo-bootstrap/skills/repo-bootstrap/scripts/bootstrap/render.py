"""Pure template rendering: ``str -> str``, no filesystem access.

Splitting rendering out of the path-coupled original makes it unit-testable with
literal template strings. The leftover scanners return findings; the validate
step (in ``scaffold.py``) decides policy.
"""

from __future__ import annotations

import re

from .common import PLACEHOLDER, SECTION_BLOCK, SECTION_INLINE, SECTION_LEFTOVER


def render_sections(text: str, enabled: frozenset[str]) -> str:
    """Resolve {{#NAME}}/{{^NAME}} conditional sections against ``enabled``.

    Applies the block pattern (whole-line) then the inline pattern, each to a
    fixpoint, so nested and inline two-branch sections resolve correctly.
    """

    def repl(match: re.Match[str]) -> str:
        kind, name, body = match.group(1), match.group(2), match.group(3)
        keep = (name in enabled) if kind == "#" else (name not in enabled)
        return body if keep else ""

    for pattern in (SECTION_BLOCK, SECTION_INLINE):
        prev = None
        while prev != text:
            prev, text = text, pattern.sub(repl, text)
    return text


def substitute_vars(text: str, variables: dict[str, str]) -> str:
    for key, value in variables.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def render(text: str, variables: dict[str, str], enabled: frozenset[str]) -> str:
    """Render conditional sections then substitute {{NAME}} placeholders."""
    return substitute_vars(render_sections(text, enabled), variables)


def find_unrendered_sections(text: str) -> list[str]:
    return sorted({m.group(0) for m in SECTION_LEFTOVER.finditer(text)})


def find_unrendered_placeholders(text: str) -> list[str]:
    return sorted({m.group(0) for m in PLACEHOLDER.finditer(text)})
