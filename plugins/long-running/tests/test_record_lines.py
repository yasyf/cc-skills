import record_lines
from conftest import Answers
from drive import state

SESSION = "session-under-test"
LONG = (
    "POST thread 1794286392.313739: the journal layout is merged and installed; his card for the runner rows is up,"
    " waits durable-agents:j12-6581\nproof: https://github.com/yasyf/cc-skills/pull/4093"
)


def post(session: str = SESSION, **tool_input: object) -> dict:
    return {"session_id": session, "tool_name": "mcp__plugin_cc-inbox_cci__post", "tool_input": tool_input}


def test_a_drive_record_arrives_as_one_line_with_the_rest_at_the_top_of_its_body(demo: str) -> None:
    made = record_lines.decision(post(kind="report", text=LONG, body="facts: drive job report j12"))
    text = made["updatedInput"]["text"]
    assert len(text) <= record_lines.RECORD_CHARS and text.endswith("…") and "\n" not in text
    assert made["updatedInput"]["body"] == f"{LONG}\n\nfacts: drive job report j12"
    assert made["updatedInput"]["kind"] == "report"


def test_his_words_and_incidents_stay_whole(demo: str) -> None:
    for kind in ("owner", "Answer", "incident"):
        assert record_lines.decision(post(kind=kind, text=LONG)) is None


def test_a_one_line_record_and_another_sessions_record_are_left_alone(demo: str) -> None:
    assert record_lines.decision(post(kind="report", text="FYI j12 merged #4093")) is None
    assert record_lines.decision(post(session="another-session", kind="report", text=LONG)) is None


def test_each_record_of_a_batch_is_cut_and_his_stay_whole(demo: str) -> None:
    records = [{"kind": "ask", "text": LONG}, {"kind": "answer", "text": LONG}]
    shortened, whole = record_lines.decision(post(records=records))["updatedInput"]["records"]
    assert len(shortened["text"]) <= record_lines.RECORD_CHARS and shortened["body"] == LONG
    assert whole == records[1]


def test_a_long_record_that_attaches_a_file_is_refused(demo: str) -> None:
    made = record_lines.decision(post(kind="report", text=LONG, path="/tmp/facts.md"))
    assert made["permissionDecision"] == "deny"
    assert "put the rest of its text in that file" in made["permissionDecisionReason"]


IDLE = "Close these 14 idle sessions and their terminals?"
RULING = {"id": "ac6134849ff8a392c9f26294d4303635d1c27a58", "title": "close every completed or idle Claude or Codex session and its terminal"}


def test_an_ask_his_recorded_answer_may_settle_is_refused_until_a_reason_names_that_answer(demo: str, recorded: Answers) -> None:
    recorded.found = [RULING]
    made = record_lines.decision(post(kind="ask", text=IDLE, body="14 sessions idle since Nov 8"))
    assert made["permissionDecision"] == "deny"
    assert "- ac61348 close every completed or idle Claude or Codex session and its terminal" in made["permissionDecisionReason"]
    assert recorded.asked == [IDLE]
    assert recorded.near == [(f"{IDLE}\n\n14 sessions idle since Nov 8", ("--nearest", "--label", f"drive:{demo}"))]
    assert record_lines.decision(post(records=[{"kind": "report", "text": "j12 merged"}, {"kind": "ask", "text": IDLE}]))["permissionDecision"] == "deny"
    named = {"unsettled": "ac61348: it lets a lane close them; the session guard still needs his tap on each terminal"}
    assert record_lines.decision(post(kind="ask", text=IDLE, attrs={"unsettled": "the guard needs his tap"}))["permissionDecision"] == "deny"
    assert record_lines.decision(post(kind="ask", text=IDLE, attrs=named)) is None


def test_an_ask_carrying_an_ask_drive_job_ask_kept_is_not_checked_again(demo: str, recorded: Answers) -> None:
    recorded.found = [RULING]
    kept = state.drive_dir(demo) / "asks" / "j1.md"
    assert record_lines.decision(post(kind="ask", text=IDLE, attrs={"ask": str(kept), "job": "j1"})) is None
    assert recorded.asked == []
    assert record_lines.decision(post(kind="ask", text=IDLE, attrs={"ask": "/tmp/asks/j1.md"}))["permissionDecision"] == "deny"


def test_an_ask_no_answer_of_his_touches_goes_out_and_other_sessions_are_not_checked(demo: str, recorded: Answers) -> None:
    assert record_lines.decision(post(kind="ask", text=IDLE)) is None
    recorded.found = [RULING]
    assert record_lines.decision(post(session="another-session", kind="ask", text=IDLE)) is None


TAPPED = "https://github.com/Forge-AI/monorepo/pull/4242"


def test_a_ready_record_for_a_pr_the_drive_owes_no_tap_on_is_refused(demo: str) -> None:
    with state.edit(demo) as data:
        data["prs"]["Forge-AI/monorepo#4242"] = state.new_pr(TAPPED, "j1", "o1")
    assert record_lines.decision(post(kind="ready", text="j1 green", pr=[TAPPED])) is None
    with state.edit(demo) as data:
        data["prs"]["Forge-AI/monorepo#4242"] |= {"tap": {"label": "Approve", "at": "2026-11-10T18:40:00-08:00", "head": "abc", "source": "slack"}, "state": "merged"}
    made = record_lines.decision(post(kind="ready", text="j1 green", pr=[TAPPED]))
    assert made["permissionDecision"] == "deny"
    assert "Forge-AI/monorepo#4242: he tapped Approve at " in made["permissionDecisionReason"]
    assert record_lines.decision(post(kind="report", text=f"The update-button fix needs your tap: {TAPPED}")) is None
