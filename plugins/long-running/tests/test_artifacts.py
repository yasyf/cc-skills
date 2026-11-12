import json
from pathlib import Path

import pytest
from drive import artifacts, install, state

JOB = {"goal": "fix the load gate", "where": "local", "agent": "a1", "session": "s1", "questions": []}


@pytest.fixture
def drive_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(state, "drive_dir", lambda name: tmp_path / "drives" / name)
    monkeypatch.setattr(install, "config_dir", lambda: tmp_path / "claude")
    return tmp_path


def transcript(home: Path, *entries: dict) -> dict:
    path = home / "claude" / "projects" / "-repo" / "s1" / "subagents" / "agent-a1.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(entry) + "\n" for entry in entries))
    return artifacts.read("demo", "j1", JOB, "transcript")


def turn(kind: str, *blocks: dict, message_id: str | None = None, **fields: object) -> dict:
    return {"type": kind, "message": {"role": kind, "content": list(blocks), **({"id": message_id} if message_id else {})}, **fields}


def test_a_job_lists_the_files_the_drive_keeps_for_it_in_reading_order() -> None:
    remote = {"where": "remote", "brief": "/b.md", "report": "DONE", "questions": [state.new_question("/q.md", "Q?", "now")], "repro": "/r.md", "agent": None, "session": None}
    assert artifacts.kept("j1", remote, {"j1"}) == ["brief", "report", "question", "repro"]
    assert artifacts.kept("j2", JOB, {"j2"}) == ["brief", "report", "transcript"]
    assert artifacts.kept("j3", JOB | {"session": None}, set()) == []


def test_a_lane_on_this_mac_without_a_written_brief_shows_its_transcripts_first_turn_as_its_brief(drive_home: Path) -> None:
    transcript(drive_home, turn("user", {"type": "text", "text": "Build the load gate"}), turn("assistant", {"type": "text", "text": "On it"}))
    shown = artifacts.read("demo", "j1", JOB, "brief")
    assert [block["text"] for turn in shown["turns"] for block in turn["blocks"]] == ["Build the load gate"]


def test_a_lanes_newest_line_is_its_newest_words_while_it_runs_else_the_line_it_sent_else_its_report(drive_home: Path) -> None:
    transcript(
        drive_home,
        turn("user", {"type": "text", "text": "Build the load gate"}),
        turn("assistant", {"type": "text", "text": "Drew the gate.\nNext the test."}, timestamp="2026-11-12T01:00:00Z"),
        turn("assistant", {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}, timestamp="2026-11-12T01:01:00Z"),
    )
    lane = JOB | {"state": "running", "said": None, "report": None, "updated": "2026-11-12T02:00:00Z"}
    assert artifacts.newest_line("demo", "j1", lane) == {"line": "Drew the gate.", "from": "transcript", "at": "2026-11-12T01:00:00Z"}
    assert artifacts.newest_line("demo", "j1", lane | {"state": "built", "said": "BUILT the gate"}) == {"line": "BUILT the gate", "from": "said", "at": lane["updated"]}
    assert artifacts.newest_line("demo", "j1", lane | {"state": "done"}) is None
    (drive_home / "drives" / "demo").mkdir(parents=True)
    state.keep_report("demo", "j1", "DONE the gate", "DONE the gate\nSERVES: it\n")
    assert artifacts.newest_line("demo", "j1", lane | {"state": "done"}) == {"line": "DONE the gate", "from": "report", "at": lane["updated"]}


def test_a_lanes_newest_line_passes_over_lines_that_name_only_records_and_keeps_one_when_every_line_does(drive_home: Path) -> None:
    transcript(
        drive_home,
        turn("assistant", {"type": "text", "text": "Drew the gate."}, timestamp="2026-11-12T01:00:00Z"),
        turn("assistant", {"type": "text", "text": "Pushed 4f3a2b1c9d."}, timestamp="2026-11-12T01:05:00Z"),
    )
    lane = JOB | {"state": "running", "said": None, "report": None, "updated": "2026-11-12T02:00:00Z"}
    assert artifacts.newest_line("demo", "j1", lane) == {"line": "Drew the gate.", "from": "transcript", "at": "2026-11-12T01:00:00Z"}
    waiting = lane | {"state": "waiting", "said": "waits on j1987"}
    assert artifacts.newest_line("demo", "j1", waiting) == {"line": "waits on j1987", "from": "said", "at": lane["updated"]}
    (drive_home / "drives" / "demo").mkdir(parents=True)
    state.keep_report("demo", "j1", "DONE the gate", "DONE the gate\n")
    assert artifacts.newest_line("demo", "j1", waiting) == {"line": "DONE the gate", "from": "report", "at": lane["updated"]}


def test_the_question_file_holds_every_question_the_job_asked_oldest_first(tmp_path: Path) -> None:
    first, second = tmp_path / "j1-a.md", tmp_path / "j1-b.md"
    first.write_text("ASK Which store?\n")
    second.write_text("ASK Which layout?\n")
    asked = JOB | {"questions": [state.new_question(str(path), None, "now") for path in (first, second, tmp_path / "gone.md")]}
    assert artifacts.read("demo", "j1", asked, "question") == {
        "title": "j1 question: fix the load gate",
        "facts": "3 questions, oldest first",
        "docs": ["ASK Which store?\n", "ASK Which layout?\n", f"This Mac no longer holds {tmp_path / 'gone.md'}."],
    }


def test_a_jobs_report_reads_every_return_its_history_keeps_newest_first(drive_home: Path) -> None:
    (drive_home / "drives" / "demo").mkdir(parents=True)
    state.keep_report("demo", "j1", "BLOCKED the diffs", "BLOCKED the diffs\n")
    state.keep_report("demo", "j1", "DONE the whole return", "DONE the whole return\nCHECK: it\n")
    assert artifacts.read("demo", "j1", JOB | {"report": "DONE short"}, "report") == {
        "title": "j1 report: fix the load gate",
        "facts": f"2 returns, newest first · {state.replies_path('demo', 'j1')}",
        "docs": ["DONE the whole return\nCHECK: it\n", "BLOCKED the diffs\n"],
    }
    assert artifacts.read("demo", "j2", JOB | {"report": "DONE short"}, "report")["docs"] == ["j2 has returned nothing yet; `drive job replies j2` prints each message its lane sent."]


def test_a_reproduction_in_code_keeps_every_line_and_its_language(tmp_path: Path) -> None:
    repro = tmp_path / "j1.go"
    repro.write_text("func main() {\n\tprintln()\n\n}\n")
    assert artifacts.read("demo", "j1", JOB | {"repro": str(repro)}, "repro") == {
        "title": "j1 reproduction: fix the load gate",
        "facts": f"4 lines · {repro}",
        "code": "func main() {\n\tprintln()\n\n}\n",
        "language": "go",
    }


def test_a_brief_reads_as_markdown(tmp_path: Path) -> None:
    brief = tmp_path / "j1.md"
    brief.write_text("Order: fix it\n- one\n")
    assert artifacts.read("demo", "j1", JOB | {"brief": str(brief)}, "brief")["docs"] == ["Order: fix it\n- one\n"]


def test_a_file_this_mac_no_longer_holds_says_so(drive_home: Path) -> None:
    assert artifacts.read("demo", "j1", JOB | {"brief": "/gone/j1.md"}, "brief")["docs"] == ["This Mac no longer holds /gone/j1.md."]
    assert artifacts.read("demo", "j1", JOB, "transcript")["docs"] == ["This Mac no longer holds the transcript of agent a1."]


def test_a_transcript_keeps_its_turns_in_order_with_who_said_each(drive_home: Path) -> None:
    read = transcript(
        drive_home,
        {"type": "user", "message": {"role": "user", "content": "why did it stop?"}},
        turn("assistant", {"type": "text", "text": "The load gate."}),
    )
    assert [(said["who"], said["tone"], said["blocks"]) for said in read["turns"]] == [
        ("Coordinator", "user", [{"text": "why did it stop?"}]),
        ("Claude", "assistant", [{"text": "The load gate."}]),
    ]


def test_a_turn_time_reads_in_pacific(drive_home: Path) -> None:
    read = transcript(drive_home, turn("assistant", {"type": "text", "text": "ok"}, timestamp="2026-11-04T21:20:49.123Z"))
    assert read["turns"][0]["at"] == "Nov 4 1:20:49 PM"


def test_thinking_and_tool_calls_fold_and_keep_their_text(drive_home: Path) -> None:
    read = transcript(
        drive_home,
        turn(
            "assistant",
            {"type": "thinking", "thinking": "read the log first"},
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/a.go"}},
            {"type": "text", "text": "Done."},
        ),
        turn("user", {"type": "tool_result", "content": [{"type": "text", "text": "line one"}, {"type": "image", "source": {"data": "aGVsbG8="}}]}),
    )
    assert [said["tone"] for said in read["turns"]] == ["assistant", "tool"]
    assert read["turns"][0]["blocks"] == [
        {"fold": "Thinking", "text": "read the log first"},
        {"fold": "Read", "code": json.dumps({"file_path": "/a.go"}, indent=2)},
        {"text": "Done."},
    ]
    assert read["turns"][1]["blocks"] == [{"fold": "Result", "code": "line one\n[image]"}]


def test_what_is_not_a_turn_is_not_drawn(drive_home: Path) -> None:
    read = transcript(
        drive_home,
        {"type": "attachment", "attachment": {"content": "hook output"}},
        {"type": "user", "isMeta": True, "message": {"role": "user", "content": "a caveat"}},
        turn("user", {"type": "text", "text": "  "}),
        turn("assistant", {"type": "text", "text": "ok"}),
    )
    assert [said["blocks"] for said in read["turns"]] == [[{"text": "ok"}]]
    assert read["facts"].startswith("1 turn · ")


def test_one_assistant_message_written_as_several_lines_reads_as_one_turn(drive_home: Path) -> None:
    read = transcript(
        drive_home,
        turn("assistant", {"type": "thinking", "thinking": "check first"}, message_id="m1"),
        turn("assistant", {"type": "text", "text": "Checked."}, message_id="m1"),
        turn("assistant", {"type": "text", "text": "Next."}, message_id="m2"),
    )
    assert [said["blocks"] for said in read["turns"]] == [[{"fold": "Thinking", "text": "check first"}, {"text": "Checked."}], [{"text": "Next."}]]
    assert read["facts"].startswith("2 turns · ")


def test_a_block_the_reader_does_not_know_still_shows_its_content(drive_home: Path) -> None:
    read = transcript(drive_home, turn("assistant", {"type": "future", "data": "a new kind of thing"}))
    assert read["turns"][0]["blocks"] == [{"fold": "future", "code": json.dumps({"type": "future", "data": "a new kind of thing"}, indent=2)}]


def test_a_source_window_holds_the_cited_lines_and_three_either_side() -> None:
    lines = [f"line {n}" for n in range(1, 21)]
    assert [row["n"] for row in artifacts.source_window(lines, 10, 11)] == list(range(7, 15))
    assert [row["n"] for row in artifacts.source_window(lines, 1, 1)] == [1, 2, 3, 4]
    assert [row["n"] for row in artifacts.source_window(lines, 20, 20)] == [17, 18, 19, 20]
