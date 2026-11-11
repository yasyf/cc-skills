import json
import re
from pathlib import Path

import pytest
from drive import artifacts, install, state

JOB = {"goal": "fix the load gate", "where": "local", "agent": "a1", "session": "s1"}


@pytest.fixture
def drive_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(state, "drive_dir", lambda name: tmp_path / "drives" / name)
    monkeypatch.setattr(install, "config_dir", lambda: tmp_path / "claude")
    return tmp_path


def transcript(home: Path, *entries: dict) -> str:
    path = home / "claude" / "projects" / "-repo" / "s1" / "subagents" / "agent-a1.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(entry) + "\n" for entry in entries))
    return artifacts.page("demo", "j1", JOB, "transcript")


def turn(kind: str, *blocks: dict, message_id: str | None = None, **fields: object) -> dict:
    return {"type": kind, "message": {"role": kind, "content": list(blocks), **({"id": message_id} if message_id else {})}, **fields}


def test_a_job_lists_the_files_the_drive_keeps_for_it_in_reading_order() -> None:
    remote = {"where": "remote", "brief": "/b.md", "report": "DONE", "question": "/q.md", "repro": "/r.md", "agent": None, "session": None}
    assert artifacts.kept("j1", remote, set()) == ["brief", "report", "question", "repro"]
    assert artifacts.kept("j2", JOB, {"j2"}) == ["report", "transcript"]
    assert artifacts.kept("j3", JOB | {"session": None}, set()) == []


def test_a_kept_report_reads_from_its_file_before_the_lane_text(drive_home: Path) -> None:
    kept = drive_home / "drives" / "demo" / "reports" / "j1.md"
    kept.parent.mkdir(parents=True)
    kept.write_text("DONE the whole return\n")
    assert "DONE the whole return" in artifacts.page("demo", "j1", JOB | {"report": "DONE short"}, "report")
    assert "DONE short" in artifacts.page("demo", "j2", JOB | {"report": "DONE short"}, "report")


def test_a_reproduction_in_code_keeps_every_line_and_its_indentation(tmp_path: Path) -> None:
    repro = tmp_path / "j1.go"
    repro.write_text("func main() {\n\tprintln()\n\n}\n")
    page = artifacts.page("demo", "j1", JOB | {"repro": str(repro)}, "repro")
    assert re.findall(r"<span>(.*?)</span>", page) == ["func main() {", "\tprintln()", "", "}"]
    assert f"<p>4 lines · {repro}</p>" in page


def test_a_brief_reads_as_text(tmp_path: Path) -> None:
    brief = tmp_path / "j1.md"
    brief.write_text("Order: fix it\n- one\n")
    page = artifacts.page("demo", "j1", JOB | {"brief": str(brief)}, "brief")
    assert '<div class="doc">Order: fix it\n- one</div>' in page


def test_a_title_and_text_that_hold_markup_stay_text(tmp_path: Path) -> None:
    repro = tmp_path / "j1.html"
    repro.write_text("<script>alert(1)</script>")
    page = artifacts.page("demo", "j1", JOB | {"goal": "<img src=x onerror=alert(1)>", "repro": str(repro)}, "repro")
    assert "<script>" not in page
    assert "<img" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_a_file_this_mac_no_longer_holds_says_so(drive_home: Path) -> None:
    assert "This Mac no longer holds /gone/j1.md." in artifacts.page("demo", "j1", JOB | {"brief": "/gone/j1.md"}, "brief")
    assert "This Mac no longer holds the transcript of agent a1." in artifacts.page("demo", "j1", JOB, "transcript")


def test_a_transcript_keeps_its_turns_in_order_with_who_said_each(drive_home: Path) -> None:
    page = transcript(
        drive_home,
        {"type": "user", "message": {"role": "user", "content": "why did it stop?"}},
        turn("assistant", {"type": "text", "text": "The load gate."}),
    )
    assert re.findall(r'<article class="turn (\w+)"', page) == ["user", "assistant"]
    assert page.index("why did it stop?") < page.index("The load gate.")
    assert "<h2>Coordinator" in page and "<h2>Claude" in page


def test_a_turn_time_reads_in_pacific(drive_home: Path) -> None:
    page = transcript(drive_home, turn("assistant", {"type": "text", "text": "ok"}, timestamp="2026-11-04T21:20:49.123Z"))
    assert "<time>Nov 4 1:20:49 PM</time>" in page


def test_thinking_and_tool_calls_fold_and_keep_their_text(drive_home: Path) -> None:
    page = transcript(
        drive_home,
        turn(
            "assistant",
            {"type": "thinking", "thinking": "read the log first"},
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/a.go"}},
            {"type": "text", "text": "Done."},
        ),
        turn("user", {"type": "tool_result", "content": [{"type": "text", "text": "line one"}, {"type": "image", "source": {"data": "aGVsbG8="}}]}),
    )
    assert "<details><summary>Thinking</summary>" in page and "read the log first" in page
    assert "<details><summary>Read</summary>" in page and "file_path" in page
    assert "<details><summary>Result</summary>" in page and "line one\n[image]" in page
    assert "aGVsbG8=" not in page
    assert page.index("Done.") > page.index("read the log first")
    assert re.findall(r'<article class="turn (\w+)"', page) == ["assistant", "tool"]


def test_what_is_not_a_turn_is_not_drawn(drive_home: Path) -> None:
    page = transcript(
        drive_home,
        {"type": "attachment", "attachment": {"content": "hook output"}},
        {"type": "user", "isMeta": True, "message": {"role": "user", "content": "a caveat"}},
        turn("assistant", {"type": "text", "text": "ok"}),
    )
    assert "hook output" not in page
    assert "a caveat" not in page
    assert "<p>1 turn · " in page


def test_one_assistant_message_written_as_several_lines_reads_as_one_turn(drive_home: Path) -> None:
    page = transcript(
        drive_home,
        turn("assistant", {"type": "thinking", "thinking": "check first"}, message_id="m1"),
        turn("assistant", {"type": "text", "text": "Checked."}, message_id="m1"),
        turn("assistant", {"type": "text", "text": "Next."}, message_id="m2"),
    )
    assert re.findall(r'<article class="turn (\w+)"', page) == ["assistant", "assistant"]
    assert "<p>2 turns · " in page


def test_a_block_this_page_does_not_know_still_shows_its_content(drive_home: Path) -> None:
    assert "a new kind of thing" in transcript(drive_home, turn("assistant", {"type": "future", "data": "a new kind of thing"}))


def test_a_source_window_holds_the_cited_lines_and_three_either_side() -> None:
    lines = [f"line {n}" for n in range(1, 21)]
    assert [row["n"] for row in artifacts.source_window(lines, 10, 11)] == list(range(7, 15))
    assert [row["n"] for row in artifacts.source_window(lines, 1, 1)] == [1, 2, 3, 4]
    assert [row["n"] for row in artifacts.source_window(lines, 20, 20)] == [17, 18, 19, 20]
