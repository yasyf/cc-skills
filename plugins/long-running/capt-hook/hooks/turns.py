from __future__ import annotations

import json
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from captain_hook.util import reqenv

__capt_hook_skip__ = True

LEADING_FLOAT = re.compile(r"\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?")
LEGACY_WINDOW = re.compile(r"claude-(?:3-|haiku-4-5|sonnet-4-|opus-4-(?:[0-6]\b|\d{8}))")
ONE_M_SUFFIX = "[1m]"
SYNTHETIC_MODEL = "<synthetic>"
WINDOW_FLOOR = 100_000
WINDOW_CEILING = 1_000_000
DEFAULT_WINDOW = 200_000
OUTPUT_RESERVE = 20_000
AUTOCOMPACT_BUFFER = 13_000
TAIL_BLOCK = 1 << 16


@dataclass(frozen=True)
class Turn:
    model: str
    tokens: int
    at: datetime


def reversed_lines(path: Path) -> Iterator[bytes]:
    with path.open("rb") as file:
        end = file.seek(0, os.SEEK_END)
        head = b""
        while end > 0:
            start = max(0, end - TAIL_BLOCK)
            file.seek(start)
            head, *lines = (file.read(end - start) + head).split(b"\n")
            yield from reversed(lines)
            end = start
        yield head


def latest_turn(transcript: Path, *, sidechain: bool = False) -> Turn | None:
    for line in reversed_lines(transcript):
        if not line.strip():
            continue
        entry = json.loads(line)
        if (
            entry.get("type") == "assistant"
            and entry.get("isSidechain", False) == sidechain
            and (model := entry["message"].get("model")) != SYNTHETIC_MODEL
            and (usage := entry["message"].get("usage"))
        ):
            tokens = usage["input_tokens"] + usage["cache_creation_input_tokens"] + usage["cache_read_input_tokens"]
            return Turn(model, tokens, datetime.fromisoformat(entry["timestamp"]))
    return None


def model_window(model: str, hint: str | None) -> int:
    if hint and hint.endswith(ONE_M_SUFFIX) and model.startswith(hint.removesuffix(ONE_M_SUFFIX)):
        return WINDOW_CEILING
    if model.startswith("claude-") and not LEGACY_WINDOW.match(model):
        return WINDOW_CEILING
    return DEFAULT_WINDOW


def settings_window(project: Path | None) -> int | None:
    files = [Path.home() / ".claude" / "settings.json"]
    if project:
        files += [project / ".claude" / "settings.json", project / ".claude" / "settings.local.json"]
    window = None
    for file in files:
        if file.is_file() and (value := json.loads(file.read_text()).get("autoCompactWindow")) is not None:
            window = value
    return window


def configured_window(project: Path | None) -> int | None:
    if env := reqenv.getenv("CLAUDE_CODE_AUTO_COMPACT_WINDOW"):
        return min(max(int(env), WINDOW_FLOOR), WINDOW_CEILING)
    return settings_window(project)


def pct_override() -> float | None:
    raw = reqenv.getenv("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE") or ""
    if (match := LEADING_FLOAT.match(raw)) and 0 < (pct := float(match[0])) <= 100:
        return pct
    return None


def threshold(model: str, hint: str | None, project: Path | None) -> int:
    cap = model_window(model, hint)
    window = min(configured_window(project) or cap, cap)
    limit = window - OUTPUT_RESERVE - AUTOCOMPACT_BUFFER
    if pct := pct_override():
        limit = min(int((window - OUTPUT_RESERVE) * pct / 100), limit)
    return limit
