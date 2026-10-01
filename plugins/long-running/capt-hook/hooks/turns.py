from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from cc_transcript import AssistantEvent, SystemEvent
from cc_transcript.models import TranscriptEvent
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
ROTATE_PERCENT = 70


@dataclass(frozen=True)
class Turn:
    model: str
    tokens: int
    at: datetime


def turn_of(events: Iterable[TranscriptEvent], *, sidechain: bool = False) -> Turn | None:
    compacted: Turn | None = None
    for event in reversed(tuple(events)):
        match event:
            case SystemEvent(subtype="compact_boundary") if compacted is None and event.meta.is_sidechain == sidechain:
                compacted = Turn("", event.detail.post_tokens, event.meta.timestamp)
            case AssistantEvent(usage=usage) if (
                usage is not None and event.model != SYNTHETIC_MODEL and event.meta.is_sidechain == sidechain
            ):
                if compacted:
                    return replace(compacted, model=event.model)
                tokens = usage.input_tokens + usage.cache_creation_input_tokens + usage.cache_read_input_tokens
                return Turn(event.model, tokens, event.meta.timestamp)
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


def rotation_line(model: str, hint: str | None, project: Path | None) -> int:
    if tokens := reqenv.getenv("LONG_RUNNING_LANE_ROTATE_TOKENS"):
        return int(tokens)
    one_m = model + ONE_M_SUFFIX if hint and hint.endswith(ONE_M_SUFFIX) else None
    return threshold(model, one_m, project) * ROTATE_PERCENT // 100
