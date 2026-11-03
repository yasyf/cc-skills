"""Which files a brief names that a remote box must receive."""

from __future__ import annotations

from pathlib import Path

import lane_brief
import pytest


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    for name in ("scratch/program/meta/contract.md", "scratch/program/lane/notes.txt", ".claude/plans/plan.md"):
        path = home / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    return home


def test_a_drive_file_travels_relative_to_the_drive_and_a_home_file_elsewhere_is_stranded(home):
    drive = home / "scratch/program"
    text = f"Read {drive}/meta/contract.md first, then `~/scratch/program/lane/notes.txt`.\nThe plan is at {home}/.claude/plans/plan.md."
    assert lane_brief.named_files(text, drive) == (["lane/notes.txt", "meta/contract.md"], [f"{home}/.claude/plans/plan.md"])


@pytest.mark.parametrize(
    "text",
    [
        "Write your report to {home}/scratch/program/lane/report.md.",
        "Run from {home}/scratch/program.",
        "See plugins/long-running/scratch/program/meta/contract.md and https://example.test/meta/contract.md.",
        "/bin/sh runs it.",
    ],
    ids=["not-yet-written", "a-directory", "repo-relative-and-url", "outside-home"],
)
def test_what_is_no_file_under_this_home_names_nothing(home, text):
    assert lane_brief.named_files(text.format(home=home), home / "scratch/program") == ([], [])
