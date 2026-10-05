from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import inboxes
import pytest

PLUGIN = Path(__file__).resolve().parents[1]
HOUR = 3600.0
T0 = 1_790_000_000.0


def append(path: Path, *lines: str) -> None:
    with path.open("a") as handle:
        handle.writelines(f"{line}\n" for line in lines)


def rotated(path: Path, *lines: str) -> inboxes.Inbox:
    inbox = inboxes.Inbox(path)
    append(path, *lines)
    inbox.rotate(T0)
    append(path, "R9 kept")
    inbox.rotate(T0 + 7 * HOUR)
    return inbox


def digest(*args: str) -> str:
    result = subprocess.run([str(PLUGIN / "bin/inbox-digest.py"), *args], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_clip_keeps_a_short_line_and_cuts_a_long_one_to_the_cap():
    assert inboxes.clip("short", 10) == "short"
    assert inboxes.clip("x" * 11, 10) == "x" * 9 + "…"
    assert len(inboxes.clip("y" * 1500)) == inboxes.DISPLAY_CHARS


def test_rotation_waits_until_a_mark_is_older_than_the_window(tmp_path):
    path = tmp_path / "deploy-go.md"
    path.write_text("R1 old\n")
    inbox = inboxes.Inbox(path)
    inode = path.stat().st_ino

    assert inbox.rotate(T0) == 0
    assert inbox.rotate(T0 + 5 * HOUR) == 0

    assert path.stat().st_ino == inode
    assert path.read_text() == "R1 old\n"
    assert inbox.archives() == []


def test_rotation_renames_a_new_file_over_the_inbox_and_archives_the_old_lines(tmp_path):
    path = tmp_path / "deploy-go.md"
    path.write_text("")
    inode = path.stat().st_ino
    with path.open("rb") as before:
        inbox = rotated(path, "R1 first", "R2 second")

        assert path.stat().st_ino != inode
        assert before.read() == b"R1 first\nR2 second\nR9 kept\n"
    assert path.read_text() == "R9 kept\n"
    assert [archive.name for archive in inbox.archives()] == ["2026-09-21.md"]
    assert inbox.archives()[0].read_text() == "R1 first\nR2 second\n"
    assert [line.text for line in inbox.lines()] == ["R1 first", "R2 second", "R9 kept"]
    assert not list(tmp_path.glob(".*rotating"))


def test_offsets_name_the_same_line_before_and_after_rotation(tmp_path):
    path = tmp_path / "orca-desk.md"
    path.write_text("")
    inbox = inboxes.Inbox(path)
    append(path, "R1 first", "R2 second")
    inbox.rotate(T0)
    append(path, "R3 third")
    before = {line.text: (line.start, line.end) for line in inbox.lines()}

    inbox.rotate(T0 + 7 * HOUR)

    assert {line.text: (line.start, line.end) for line in inbox.lines()} == before
    assert [line.text for line in inbox.lines(before["R3 third"][0])] == ["R3 third"]
    assert inbox.end() == before["R3 third"][1]


def test_a_line_written_to_the_old_file_during_the_rename_moves_to_the_new_one(tmp_path, monkeypatch):
    path = tmp_path / "runner.md"
    path.write_text("")
    inbox = inboxes.Inbox(path)
    append(path, "R1 first")
    inbox.rotate(T0)
    real = os.replace

    def racing(source, target):
        if Path(target) == path:
            append(path, "R2 raced")
        real(source, target)

    monkeypatch.setattr(inboxes.os, "replace", racing)
    inbox.rotate(T0 + 7 * HOUR)

    assert path.read_text() == "R2 raced\n"
    assert [line.text for line in inbox.lines()] == ["R1 first", "R2 raced"]


def test_digest_prints_only_lines_since_the_last_digest_and_advances_its_state(tmp_path):
    path = tmp_path / "runner.md"
    state = tmp_path / ".inbox-digest.json"
    path.write_text("R1 first\n\nR2 second\n")

    assert digest("--state", str(state), str(path)) == "== runner.md: 2 of 2 lines\nR1 first\nR2 second\n"
    assert json.loads(state.read_text()) == {str(path): path.stat().st_size}
    assert digest("--state", str(state), str(path)) == "no new inbox lines\n"

    append(path, "R3 third")
    with path.open("a") as handle:
        handle.write("R4 half")

    assert digest("--state", str(state), str(path)) == "== runner.md: 1 of 1 lines\nR3 third\n"


def test_digest_cuts_each_line_to_the_line_cap(tmp_path):
    path = tmp_path / "deploy-go.md"
    path.write_text("x" * 1500 + "\n")

    assert digest("--all", str(path)) == "== deploy-go.md: 1 of 1 lines\n" + "x" * 199 + "…\n"
    assert digest("--all", "--line-cap", "20", str(path)) == "== deploy-go.md: 1 of 1 lines\n" + "x" * 19 + "…\n"


def test_digest_keeps_the_newest_lines_within_the_budget_and_counts_the_rest(tmp_path):
    busy = tmp_path / "deploy-go.md"
    quiet = tmp_path / "runner.md"
    append(busy, *(f"G{n} " + "y" * 300 for n in range(100)))
    append(quiet, "R1 quiet")

    out = digest("--all", "--budget", "2000", str(busy), str(quiet))

    lines = out.splitlines()
    assert len(out.encode()) - len(lines[-1].encode()) - 1 <= 2000
    assert lines[0].startswith("== deploy-go.md: ")
    shown = int(lines[0].split(": ")[1].split(" of ")[0])
    assert lines[1 : shown + 1] == [f"G{n} " + "y" * 195 + "…" for n in range(100 - shown, 100)]
    assert lines[shown + 1 : shown + 3] == ["== runner.md: 1 of 1 lines", "R1 quiet"]
    assert lines[-1] == f"omitted, oldest first, over the 2000-byte budget: deploy-go.md {100 - shown} lines"


def test_digest_reads_archives_only_with_all(tmp_path):
    path = tmp_path / "orca-desk.md"
    state = tmp_path / ".inbox-digest.json"
    path.write_text("")
    digest("--state", str(state), str(path))
    rotated(path, "R1 archived")

    assert digest("--state", str(state), str(path)) == (
        "== orca-desk.md: 1 of 1 lines\nR9 kept\n== orca-desk.md: 12 unread bytes moved to orca-desk.md.archive/; --all reads them\n"
    )
    assert digest("--all", str(path)) == "== orca-desk.md: 2 of 2 lines\nR1 archived\nR9 kept\n"


def test_the_rotate_command_records_a_mark_and_leaves_young_lines(tmp_path):
    path = tmp_path / "landing-desk.md"
    path.write_text("L1 young\n")

    result = subprocess.run([str(PLUGIN / "bin/inbox-rotate.py"), str(path)], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert path.read_text() == "L1 young\n"
    [[_, end]] = json.loads((tmp_path / "landing-desk.md.archive/marks.json").read_text())
    assert end == path.stat().st_size


@pytest.mark.parametrize("script", [PLUGIN / "skills/long-running/scripts/desk-wait.sh", PLUGIN / "bin/desk-wait.sh"])
def test_a_desk_wait_cursor_counts_archived_lines(tmp_path, script):
    path = tmp_path / "landing-desk.md"
    cursor = tmp_path / "landing-desk.cursor"
    path.write_text("")
    rotated(path, "L1 read", "L2 read")
    cursor.write_text("3\n")
    append(path, "L3 new")

    result = subprocess.run([str(script), "3", f"{path}={cursor}"], capture_output=True, text=True, timeout=5)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "L3 new\n"
    assert cursor.read_text() == "4\n"
