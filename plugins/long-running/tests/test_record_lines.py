import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import record_lines
from drive import limits, state

SESSION = "session-under-test"
LONG = (
    "POST thread 1794286392.313739: the journal layout is merged and installed; his card for the runner rows is up,"
    " waits durable-agents:j12-6581\nproof: https://github.com/yasyf/cc-skills/pull/4093"
)


def post(session: str = SESSION, **tool_input: object) -> dict:
    return {"session_id": session, "tool_name": "mcp__plugin_cc-inbox_cci__post", "tool_input": tool_input}


def refusal(made: dict | None) -> str:
    assert made and made["permissionDecision"] == "deny"
    return made["permissionDecisionReason"]


def test_a_drive_record_arrives_as_one_line_with_the_rest_at_the_top_of_its_body(demo: str) -> None:
    made = record_lines.decision(post(kind="report", text=LONG, body="facts: drive job report j12"))
    text = made["updatedInput"]["text"]
    assert len(text) <= limits.RECORD_CHARS and text.endswith("…") and "\n" not in text
    assert made["updatedInput"]["body"] == f"{LONG}\n\nfacts: drive job report j12"
    assert made["updatedInput"]["kind"] == "report"


def test_his_words_and_incidents_stay_whole(demo: str) -> None:
    for kind in ("owner", "Answer", "incident"):
        assert record_lines.decision(post(kind=kind, text=LONG)) is None


def test_a_one_line_record_and_another_sessions_record_are_left_alone(demo: str) -> None:
    assert record_lines.decision(post(kind="report", text="FYI j12 merged #4093")) is None
    assert record_lines.decision(post(session="another-session", kind="report", text=LONG)) is None


def test_each_record_of_a_batch_is_cut_and_his_stay_whole(demo: str) -> None:
    records = [{"kind": "ask", "text": LONG, "attrs": {"ask": str(state.drive_dir(demo) / "asks" / "j12-6581.md"), "job": "j12"}}, {"kind": "answer", "text": LONG}]
    shortened, whole = record_lines.decision(post(records=records))["updatedInput"]["records"]
    assert len(shortened["text"]) <= limits.RECORD_CHARS and shortened["body"] == LONG
    assert whole == records[1]


def test_a_long_record_that_attaches_a_file_is_cut_to_one_line_and_keeps_its_file(demo: str) -> None:
    asked = f"ASK j12 asks which layout the run page keeps; the whole question and its options are in {'/x' * 40}/j12-ab12cd34.md"
    made = record_lines.decision(post(records=[post(kind="report", text=LONG)["tool_input"], {"kind": "report", "text": asked, "path": "/tmp/facts.md"}]))
    cut = made["updatedInput"]["records"][1]
    assert cut == {"kind": "report", "text": limits.first_line(asked), "path": "/tmp/facts.md"}
    assert len(cut["text"]) <= limits.RECORD_CHARS


IDLE = "Close these 14 idle sessions and their terminals?"


def test_an_ask_naming_no_ask_drive_job_ask_kept_is_refused_whatever_it_says(demo: str) -> None:
    for attrs in ({}, {"unsettled": "ac61348: it lets a lane close them"}, {"ask": "/tmp/asks/j1.md", "job": "j1"}):
        made = record_lines.decision(post(kind="ask", text=IDLE, body="14 sessions idle since Nov 8", attrs=attrs))
        assert made["permissionDecision"] == "deny"
        assert "drive job ask <j> <file>" in made["permissionDecisionReason"]
    assert record_lines.decision(post(records=[{"kind": "report", "text": "j12 merged"}, {"kind": "ask", "text": IDLE}]))["permissionDecision"] == "deny"


def test_an_ask_naming_the_ask_drive_job_ask_kept_goes_out_and_other_sessions_are_not_checked(demo: str) -> None:
    kept = state.drive_dir(demo) / "asks" / "j1-0a1b2c3d.md"
    assert record_lines.decision(post(kind="ask", text=IDLE, attrs={"ask": str(kept), "job": "j1"})) is None
    assert record_lines.decision(post(session="another-session", kind="ask", text=IDLE)) is None


TAPPED = "https://github.com/Forge-AI/monorepo/pull/4242"


def test_a_ready_record_for_a_pr_the_drive_owes_no_tap_on_is_refused(demo: str) -> None:
    with state.edit(demo) as data:
        data["prs"]["Forge-AI/monorepo#4242"] = state.new_pr(TAPPED, "j1", "o1")
    assert record_lines.decision(post(kind="ready", text="j1 green, his order o1", pr=[TAPPED])) is None
    with state.edit(demo) as data:
        data["prs"]["Forge-AI/monorepo#4242"] |= {"tap": {"label": "Approve", "at": "2026-11-10T18:40:00-08:00", "head": "abc", "source": "slack"}, "state": "merged"}
    made = record_lines.decision(post(kind="ready", text="j1 green, his order o1", pr=[TAPPED]))
    assert made["permissionDecision"] == "deny"
    assert "Forge-AI/monorepo#4242: he tapped Approve at " in made["permissionDecisionReason"]
    assert "he tapped Approve" in record_lines.decision(post(kind="report", text=f"POST the update-button fix needs your tap: {TAPPED}"))["permissionDecisionReason"]
    kept = {"ask": str(state.drive_dir(demo) / "asks" / "j1-0a1b2c3d.md"), "job": "j1"}
    assert "he tapped Approve" in record_lines.decision(post(kind="ask", text=f"tap Approve on {TAPPED}?", attrs=kept))["permissionDecisionReason"]
    assert record_lines.decision(post(kind="report", text=f"FYI the update-button fix merged: {TAPPED}")) is None


TO = ["communicator@demo"]
THREAD = "1794378983.440769"
OTHER = "1794379098.881109"


def by(agent_type: str, transcript: Path | None = None, **tool_input: object) -> dict:
    return post(**tool_input) | {"agent_id": "a1", "agent_type": agent_type, "transcript_path": str(transcript or "/nonexistent.jsonl")}


@pytest.fixture
def turn(tmp_path: Path) -> Path:
    path = tmp_path / "coordinator.jsonl"
    path.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "TICK o1"}}) + "\n")
    return path


def coordinator(turn: Path, **tool_input: object) -> dict:
    return post(**tool_input) | {"transcript_path": str(turn), "tool_use_id": "now"}


def called(path: Path, call: str, tool: str, tool_input: dict, failed: bool = False, age: timedelta = timedelta()) -> None:
    at = (datetime.now(UTC) - age).isoformat()
    use = {"type": "tool_use", "id": call, "name": tool, "input": tool_input}
    result = {"type": "tool_result", "tool_use_id": call, "content": "ok"} | ({"is_error": True} if failed else {})
    with path.open("a") as transcript:
        transcript.write(json.dumps({"type": "assistant", "timestamp": at, "message": {"role": "assistant", "content": [use]}}) + "\n")
        transcript.write(json.dumps({"type": "user", "timestamp": at, "message": {"role": "user", "content": [result]}}) + "\n")


def test_a_reader_or_designer_posting_to_him_is_refused_and_its_defect_to_the_communicator_goes_out(demo: str) -> None:
    for agent in ("long-running:reader", "long-running:designer"):
        reason = refusal(record_lines.decision(by(agent, kind="report", text="j12 found the leak", to=["owner@demo"])))
        assert "drive job report <j>" in reason and "communicator@<drive>" in reason
    assert record_lines.decision(by("long-running:reader", kind="defect", text="drive job report fails", to=TO, lane="reader")) is None
    assert record_lines.decision(by("long-running:handler", kind="note", text="j12 found the leak", to=["owner@demo"])) is None


def test_a_handler_returns_a_defect_line_instead_of_posting_one(communicator: str) -> None:
    reason = refusal(record_lines.decision(by("long-running:handler", kind="defect", text="cc-slack say fails", to=["root@demo"])))
    assert "DEFECT <tool>" in reason
    assert record_lines.decision(post(kind="defect", text="cc-slack say fails", to=["root@demo"])) is None
    assert record_lines.decision(by("long-running:designer", kind="defect", text="drive job ask fails", to=TO, lane="designer")) is None
    assert record_lines.decision(by("long-running:handler", kind="report", text="POST j12 merged", to=TO, lane="handler")) is None


def test_an_ask_naming_no_job_is_refused(demo: str) -> None:
    kept = str(state.drive_dir(demo) / "asks" / "j750-ab12.md")
    assert "--attr job=<j>" in refusal(record_lines.decision(post(kind="ask", text=IDLE, attrs={"ask": kept})))
    assert record_lines.decision(post(kind="ask", text=IDLE, attrs={"ask": kept, "job": "j750"})) is None


def test_a_ready_record_says_which_kind_its_prs_are(demo: str) -> None:
    assert "his order o<n>" in refusal(record_lines.decision(post(kind="ready", text="#4244 is ready", pr=[TAPPED])))
    for text in ("#4244, his order o284, is ready", "Drive tooling: the watch reads taps", "port of #4100"):
        assert record_lines.decision(post(kind="ready", text=text)) is None


def test_a_record_carrying_a_max_words_attr_is_refused_on_any_kind(demo: str) -> None:
    for record in ({"kind": "report", "text": "POST j702 merged", "to": TO}, {"kind": "note", "text": "the watch reads taps"}):
        reason = refusal(record_lines.decision(post(**record, attrs={"max_words": "200"})))
        assert "`max_words`" in reason
        assert f"at most {limits.LINE_WORDS} words" in reason
        assert f"at most {limits.SUMMARY_WORDS}" in reason
        assert record_lines.decision(post(**record)) is None


def test_a_report_opens_post_or_fyi(demo: str) -> None:
    assert "`POST`" in refusal(record_lines.decision(post(kind="report", text="j702 merged", to=TO)))
    assert record_lines.decision(post(kind="report", text="POST j702 merged", to=TO)) is None
    assert record_lines.decision(post(kind="report", text="j702 merged", to=["builder@demo"])) is None


def test_a_catch_up_names_no_thread(demo: str, turn: Path) -> None:
    for attrs in ({"thread": THREAD, "catch_up": "true"}, {"thread": THREAD}):
        reason = refusal(record_lines.decision(coordinator(turn, kind="report", text="POST *Catch-up since 9 PM*", to=TO, attrs=attrs)))
        assert "without `--attr thread`" in reason
    assert record_lines.decision(coordinator(turn, kind="report", text="POST *Catch-up since 9 PM*", to=TO, attrs={"catch_up": "true"})) is None


def test_a_report_that_finishes_an_order_names_one_of_its_threads(run: Callable[..., tuple[int, str]], demo: str, turn: Path) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    with state.edit(demo) as data:
        data["orders"]["o1"]["thread"] = THREAD
    reason = refusal(record_lines.decision(coordinator(turn, kind="report", text="POST j1 merged", to=TO, attrs={"close": "o1", "thread": OTHER})))
    assert f"drive order set <o> --thread {OTHER}" in reason and THREAD in reason
    assert record_lines.decision(coordinator(turn, kind="report", text="POST j1 merged", to=TO, attrs={"close": "o1", "thread": THREAD})) is None
    assert record_lines.decision(coordinator(turn, kind="report", text="POST j1 is building", to=TO, attrs={"thread": OTHER})) is None


def test_the_coordinator_sends_one_report_per_thread_per_turn(demo: str, turn: Path) -> None:
    tool = "mcp__plugin_cc-inbox_cci__post"
    earlier = {"kind": "report", "text": "POST j1 merged", "to": TO, "attrs": {"thread": THREAD}}
    called(turn, "refused", tool, earlier, failed=True)
    assert record_lines.decision(coordinator(turn, **earlier)) is None
    called(turn, "first", tool, earlier)
    assert "one report per thread per turn" in refusal(record_lines.decision(coordinator(turn, kind="report", text="POST j2 merged", to=TO, attrs={"thread": THREAD})))
    assert record_lines.decision(coordinator(turn, kind="report", text="POST j2 merged", to=TO, attrs={"thread": OTHER})) is None
    wake = '<channel source="plugin:cc-inbox:cci" kind="handoff" lane="watch" seq="662305">\n@2:36 AM\n662305 handoff watch j242 green'
    with turn.open("a") as transcript:
        transcript.write(json.dumps({"type": "user", "isMeta": True, "message": {"role": "user", "content": "the skill's body"}}) + "\n")
    assert "one report per thread per turn" in refusal(record_lines.decision(coordinator(turn, kind="report", text="POST j2 merged", to=TO, attrs={"thread": THREAD})))
    with turn.open("a") as transcript:
        transcript.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "stop_reason": "end_turn", "content": [{"type": "text", "text": "Sent."}]}}) + "\n")
        transcript.write(json.dumps({"type": "user", "isMeta": True, "message": {"role": "user", "content": wake}}) + "\n")
    called(turn, "now", tool, earlier)
    assert record_lines.decision(coordinator(turn, kind="report", text="POST j2 merged", to=TO, attrs={"thread": THREAD})) is None
    assert "one report per thread per turn" in refusal(record_lines.decision(coordinator(turn, records=[earlier, earlier])))
    called(turn, "woken", tool, earlier)
    assert "the next wake" in refusal(record_lines.decision(coordinator(turn, kind="report", text="POST j3 merged", to=TO, attrs={"thread": THREAD})))


def said(count: int) -> str:
    return " ".join(["word"] * count)


def test_a_post_report_keeps_the_length_of_its_post(demo: str) -> None:
    assert "at most 60 words" in refusal(record_lines.decision(post(kind="report", text=f"POST {said(90)}", to=TO)))
    assert "permissionDecision" not in record_lines.decision(post(kind="report", text=f"POST {said(55)}", to=TO))
    assert "permissionDecision" not in record_lines.decision(post(kind="report", text=f"FYI {said(90)}", to=TO))


def test_a_post_reports_feature_headings_are_counted_past_its_post_word(demo: str) -> None:
    upload = "POST *Upload*\n" + said(35)
    assert "permissionDecision" not in record_lines.decision(post(kind="report", text=f"{upload}\n*Download*\n{said(35)}", to=TO))
    assert "at most 60 words" in refusal(record_lines.decision(post(kind="report", text=f"{upload}\n{said(35)}", to=TO)))


def test_a_report_saying_something_waits_on_him_names_its_card(demo: str) -> None:
    assert "--attr waits=" in refusal(record_lines.decision(post(kind="report", text="POST the copy fix waits on his pick", to=TO)))
    assert record_lines.decision(post(kind="report", text="POST the copy fix waits on his pick", to=TO, attrs={"waits": "durable-agents:j750-661229"})) is None
    assert record_lines.decision(post(kind="report", text="POST the copy fix waits on his pick {card:durable-agents:j750-661229}", to=TO)) is None


def test_a_wait_on_anything_else_says_how_long_and_whether_it_runs(demo: str) -> None:
    assert "how long it has waited" in refusal(record_lines.decision(post(kind="report", text="POST j702 waits on CI", to=TO)))
    assert record_lines.decision(post(kind="report", text="POST j702 waits on CI, 40 min, run 123 running", to=TO)) is None


def test_a_job_is_called_stuck_only_after_a_read_of_its_transcript(demo: str, turn: Path) -> None:
    stuck = {"kind": "report", "text": "POST j702 is stuck since 5 PM", "to": TO}
    assert "`drive job replies <j>`" in refusal(record_lines.decision(coordinator(turn, **stuck)))
    called(turn, "old", "Bash", {"command": "drive job replies j702"}, age=timedelta(minutes=30))
    assert "j702" in refusal(record_lines.decision(coordinator(turn, **stuck)))
    called(turn, "read", "Bash", {"command": "drive job replies j702"})
    assert record_lines.decision(coordinator(turn, **stuck)) is None
