from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from hooks import turns

FIXTURES = Path(__file__).resolve().parents[1] / "capt-hook" / "hooks" / "tests" / "fixtures"


@pytest.mark.parametrize(
    ("model", "hint", "window"),
    [
        ("claude-fable-5-1", None, 1_000_000),
        ("claude-opus-5-5", "claude-fable-5-1", 1_000_000),
        ("claude-opus-4-7", None, 1_000_000),
        ("claude-sonnet-5", None, 1_000_000),
        ("claude-opus-4-6", None, 200_000),
        ("claude-opus-4-20250514", None, 200_000),
        ("claude-opus-4-1-20250805", None, 200_000),
        ("claude-sonnet-4-5-20250929", None, 200_000),
        ("claude-haiku-4-5-20251001", None, 200_000),
        ("claude-3-5-sonnet-20241022", None, 200_000),
        ("claude-sonnet-4-6", "claude-sonnet-4-6[1m]", 1_000_000),
        ("claude-sonnet-4-6", "claude-opus-5-5[1m]", 200_000),
        ("gpt-5", None, 200_000),
    ],
)
def test_model_window(model: str, hint: str | None, window: int) -> None:
    assert turns.model_window(model, hint) == window


def test_reversed_lines_crosses_block_boundaries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(turns, "TAIL_BLOCK", 7)
    path = tmp_path / "t.jsonl"
    lines = [b"first", b"", b"a much longer second line", b"3"]
    path.write_bytes(b"\n".join(lines) + b"\n")

    assert list(turns.reversed_lines(path)) == [b"", *reversed(lines)]


def test_latest_turn_skips_synthetic_and_sidechain_entries() -> None:
    assert turns.latest_turn(FIXTURES / "usage-170k-synthetic-tail.jsonl") == turns.Turn(
        "claude-sonnet-4-6", 170_000, datetime(2026, 9, 24, 16, 3, tzinfo=UTC)
    )
    assert turns.latest_turn(FIXTURES / "usage-460k.jsonl").tokens == 460_000
    assert turns.latest_turn(FIXTURES / "usage-460k.jsonl", sidechain=True).tokens == 5_000


@pytest.mark.parametrize(
    ("env", "project", "model", "hint", "limit"),
    [
        ({}, "project-600k", "claude-fable-5-1", "claude-fable-5-1", 567_000),
        ({}, "project-600k", "claude-sonnet-4-6", None, 167_000),
        ({}, "project-600k", "claude-sonnet-4-6", "claude-sonnet-4-6[1m]", 567_000),
        ({}, "project-local", "claude-opus-5-5", None, 267_000),
        ({"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "50000"}, "project-600k", "claude-opus-5-5", None, 67_000),
        ({"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "2000000"}, None, "claude-opus-5-5", None, 967_000),
        ({"CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "50"}, "project-600k", "claude-opus-5-5", None, 290_000),
        ({"CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "nan"}, "project-600k", "claude-opus-5-5", None, 567_000),
    ],
)
def test_threshold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    env: dict,
    project: str | None,
    model: str,
    hint: str | None,
    limit: int,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    for key in ("CLAUDE_CODE_AUTO_COMPACT_WINDOW", "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    assert turns.threshold(model, hint, FIXTURES / project if project else None) == limit
