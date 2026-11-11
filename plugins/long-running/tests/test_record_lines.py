import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import record_lines
from conftest import SHORT_ASK
from drive import limits, settled, state, view

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


def kept(name: str, job_id: str, title: str, card: str | None = None) -> dict[str, str]:
    with state.edit(name) as data:
        job = data["jobs"].setdefault(job_id, state.new_job("o1", "builder", "build it", [], state.stamp()))
        file = state.drive_dir(name) / "asks" / f"{job_id}-{len(job['questions'])}.md"
        job["questions"].append(state.new_question(str(file), title, state.stamp(), card=card) | {"waits": card and f"{name}:{job_id}-7"})
    return {"ask": str(file), "job": job_id}


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
    asked = f"{LAYOUT}\nproof: https://github.com/yasyf/cc-skills/pull/4093"
    records = [{"kind": "ask", "text": asked, "attrs": kept(demo, "j12", LAYOUT)}, {"kind": "answer", "text": LONG}]
    shortened, whole = record_lines.decision(post(records=records))["updatedInput"]["records"]
    assert shortened["text"] == LAYOUT and shortened["body"] == asked
    assert whole == records[1]


def test_a_long_record_that_attaches_a_file_is_cut_to_one_line_and_keeps_its_file(demo: str) -> None:
    asked = f"ASK j12 asks which layout the run page keeps; the whole question and its options are in {'/x' * 40}/j12-ab12cd34.md"
    made = record_lines.decision(post(records=[post(kind="report", text=LONG)["tool_input"], {"kind": "report", "text": asked, "path": "/tmp/facts.md"}]))
    cut = made["updatedInput"]["records"][1]
    assert cut == {"kind": "report", "text": limits.first_line(asked), "path": "/tmp/facts.md"}
    assert len(cut["text"]) <= limits.RECORD_CHARS


IDLE = "Close these 14 idle sessions and their terminals?"
LAYOUT = "How should a finished run's files be laid out so its page opens without guessing?"
HANDLE = "How does an agent another agent started get the handle it sends its starter messages through?"


def test_an_ask_naming_no_ask_drive_job_ask_kept_is_refused_whatever_it_says(demo: str) -> None:
    for attrs in ({}, {"unsettled": "ac61348: it lets a lane close them"}, {"ask": "/tmp/asks/j1.md", "job": "j1"}):
        made = record_lines.decision(post(kind="ask", text=IDLE, body="14 sessions idle since Nov 8", attrs=attrs))
        assert made["permissionDecision"] == "deny"
        assert "drive job ask <j> <file>" in made["permissionDecisionReason"]
    assert record_lines.decision(post(records=[{"kind": "report", "text": "j12 merged"}, {"kind": "ask", "text": IDLE}]))["permissionDecision"] == "deny"


def test_an_ask_naming_the_ask_drive_job_ask_kept_goes_out_and_other_sessions_are_not_checked(demo: str) -> None:
    assert record_lines.decision(post(kind="ask", text=IDLE, attrs=kept(demo, "j1", IDLE))) is None
    assert record_lines.decision(post(session="another-session", kind="ask", text=IDLE)) is None


def test_an_ask_naming_its_jobs_earlier_answered_question_is_refused_until_drive_job_ask_keeps_its_own(demo: str) -> None:
    earlier = kept(demo, "j1", LAYOUT, card=CARD)
    reason = refusal(record_lines.decision(post(kind="ask", text=HANDLE, attrs=earlier)))
    assert f"{earlier['ask']} keeps j1's question \"{LAYOUT}\", which this record does not ask" in reason
    assert "`drive job ask j1` with no file for a remote lane's ASK, which reads its report" in reason
    assert f"his answer closed j1's question \"{LAYOUT}\"" in refusal(record_lines.decision(post(kind="ask", text=LAYOUT, attrs=earlier)))
    assert record_lines.decision(post(kind="ask", text=HANDLE, attrs=kept(demo, "j1", HANDLE))) is None


def test_an_ask_naming_another_open_question_or_another_jobs_file_is_refused_and_one_redoing_its_own_open_card_goes_out(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    shown = {"url": CARD, "question": LAYOUT, "asked": state.stamp(), "card": {"title": LAYOUT, "waits": "demo:j1-7"}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [shown], "pr_cards": []})
    earlier = kept(demo, "j1", LAYOUT, card=CARD)
    assert "which this record does not ask" in refusal(record_lines.decision(post(kind="ask", text=HANDLE, attrs=earlier)))
    elsewhere = kept(demo, "j2", HANDLE)
    assert f"{elsewhere['ask']} keeps no question of j1" in refusal(record_lines.decision(post(kind="ask", text=HANDLE, attrs=elsewhere | {"job": "j1"})))
    redone = earlier | {"waits": "demo:j1-7", "reworking": "the layout is being redone, back by 5 PM"}
    assert record_lines.decision(post(kind="ask", text=f"ASK (o1, j1): {LAYOUT}", attrs=redone)) is None


def test_a_jobs_second_question_from_a_remote_lane_goes_out_on_the_new_file_drive_job_ask_keeps(
    run: Callable[..., tuple[int, str]], demo: str, tmp_path: Path
) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "design the run page")
    run("job", "add", "builder", "--order", "o1", "--goal", "design it")
    block = tmp_path / "ask.md"
    block.write_text(SHORT_ASK)
    first = run("job", "ask", "j1", str(block))[1]
    with state.edit(demo) as data:
        data["jobs"]["j1"]["questions"][0].update(waits="demo:j1-7", card=CARD)
        data["jobs"]["j1"]["report"] = SHORT_ASK.replace(f"ASK {LAYOUT}", f"ASK {HANDLE}")
    assert "drive job ask j1" in refusal(record_lines.decision(post(kind="ask", text=HANDLE, attrs={"ask": first, "job": "j1"})))
    code, second = run("job", "ask", "j1")
    assert code == 0 and second != first
    assert record_lines.decision(post(kind="ask", text=HANDLE, attrs={"ask": second, "job": "j1"})) is None


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
    tap = f"tap Approve on {TAPPED}?"
    assert "he tapped Approve" in record_lines.decision(post(kind="ask", text=tap, attrs=kept(demo, "j1", tap)))["permissionDecisionReason"]
    assert record_lines.decision(post(kind="report", text=f"FYI the update-button fix merged: {TAPPED}")) is None


def test_a_ready_record_for_a_pr_under_a_readers_not_ready_is_refused_until_a_later_readers_ready(demo: str) -> None:
    with state.edit(demo) as data:
        data["prs"]["Forge-AI/monorepo#4242"] = state.new_pr(TAPPED, "j1", "o1") | {"wants_tap": True}
        for reader in ("j2", "j3"):
            data["jobs"][reader] = state.new_job("o1", "reader", "READY-row check of #4242", [], state.stamp())
        state.take_result(data, "j2", "NOT READY #4242: Releasable alone needs #4243 live first")
    reason = refusal(record_lines.decision(post(kind="ready", text="j1 fixed the finding, his order o1", pr=[TAPPED])))
    assert "Forge-AI/monorepo#4242: reader j2 called it NOT READY at " in reason
    assert ": Releasable alone needs #4243 live first; only a later reader's READY naming it answers that, never its builder's word" in reason
    with state.edit(demo) as data:
        state.take_result(data, "j3", f"READY {TAPPED}")
    assert record_lines.decision(post(kind="ready", text="j1 fixed the finding, his order o1", pr=[TAPPED])) is None


TO = ["communicator@demo"]
THREAD = "1794378983.440769"
OTHER = "1794379098.881109"
CARD = "https://forge.slack.com/archives/D123/p1794379200123456"
CARD_THREAD = "1794379200.123456"


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
    asked = kept(demo, "j750", IDLE)
    assert refusal(record_lines.decision(post(kind="ask", text=IDLE, attrs={"ask": asked["ask"]}))) == settled.UNJOBBED
    assert record_lines.decision(post(kind="ask", text=IDLE, attrs=asked)) is None


def test_a_ready_record_says_which_kind_its_prs_are(demo: str) -> None:
    assert "his order o<n>" in refusal(record_lines.decision(post(kind="ready", text="#4244 is ready", pr=[TAPPED])))
    for text in ("#4244, his order o284, is ready", "Drive tooling: the watch reads taps", "port of #4100"):
        assert record_lines.decision(post(kind="ready", text=text)) is None


def test_a_ready_record_naming_its_order_takes_its_kind_from_that_order(run: Callable[..., tuple[int, str]], demo: str) -> None:
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("order", "add", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: fix ask")
    made = record_lines.decision(post(kind="ready", text="#4244 is ready", attrs={"order": "o1"}))
    assert made["updatedInput"]["text"] == '#4244 is ready; his order o1 "ship the fix"'
    assert record_lines.decision(post(kind="ready", text="#4244 is ready", attrs={"order": "o2"}))["updatedInput"]["text"] == "#4244 is ready; Drive tooling: fix ask"
    assert "his order o<n>" in refusal(record_lines.decision(post(kind="ready", text="#4244 is ready", attrs={"order": "o9"})))


def test_an_ask_attaching_its_kept_file_takes_it_as_its_ask(demo: str) -> None:
    asked = kept(demo, "j1", IDLE)
    made = record_lines.decision(post(kind="ask", text=IDLE, path=asked["ask"], attrs={"job": "j1"}))
    assert made["updatedInput"]["attrs"] == asked


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


def test_a_report_attr_a_separator_or_one_letter_from_one_the_drive_reads_is_refused_naming_it(demo: str, turn: Path) -> None:
    catch_up = "POST his catch-up: the import fix merged and the upload page is live"
    for key in ("catchup", "catch-up", "catchUp"):
        reason = refusal(record_lines.decision(coordinator(turn, kind="report", text=catch_up, to=TO, attrs={key: "1:46 AM-3:10 AM"})))
        assert f"no `{key}` attr" in reason and "`--attr catch_up=true`" in reason
    for key, attr in (("closes", "close"), ("Part", "part"), ("jobs", "job"), ("thred", "thread"), ("wait", "waits")):
        assert f"no `{key}` attr on a report" in (reason := refusal(record_lines.decision(post(kind="report", text="POST j702 merged", to=TO, attrs={key: "x"}))))
        assert f"`--attr {attr}=" in reason
    assert record_lines.decision(coordinator(turn, kind="report", text=catch_up, to=TO, attrs={"catch_up": "true"})) is None
    assert record_lines.decision(post(kind="report", text="POST j702 merged", to=TO, attrs={"order": "o1", "incident": "i3", "close": "o1"})) is None
    assert record_lines.decision(post(kind="ask", text=IDLE, attrs={"catchup": "true", **kept(demo, "j1", IDLE)})) is None


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
    assert record_lines.decision(post(kind="report", text="POST https://github.com/yasyf/cc-skills/pull/4093 waits on his pick", to=TO)) is None


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


def test_the_communicators_record_for_a_coordinator_goes_to_the_session_hearing_the_order_it_serves_whatever_lane_it_names(
    run: Callable[..., tuple[int, str]], demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--project", "Iris", "--workstream", "Run page", "ship the run page")
    run("order", "add", "--project", "Platform", "--workstream", "Alerts", "fix the alert")
    run("job", "add", "builder", "--order", "o1", "--goal", "build it")
    with state.edit(demo) as data:
        data["orders"]["o1"]["thread"], data["orders"]["o2"]["thread"] = THREAD, OTHER
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "iris-1")
    run("start", demo, "--role", "coordinator", "--project", "Iris")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", demo, "--role", "communicator")
    root = ["root@demo"]
    records = [
        {"kind": "owner", "text": "also the logs page", "to": root, "attrs": {"thread": THREAD}},
        {"kind": "answer", "text": "Ring", "to": root, "re": ["7"], "attrs": {"job": "j1", "maps": "none"}},
        {"kind": "posted", "text": "card up demo:j1-8", "to": root, "resolves": ["8"], "attrs": {"waits": "demo:j1-8"}},
        {"kind": "refused", "text": "cc-slack refused the card", "to": root, "resolves": ["9"], "attrs": {"job": "j1"}},
        {"kind": "owner", "text": "and the alert", "to": ["iris@demo"], "attrs": {"thread": OTHER}},
        {"kind": "owner", "text": "a new order", "to": root},
        {"kind": "report", "text": "FYI j1 merged", "attrs": {"close": "o1"}},
    ]
    made = record_lines.decision(post("talker", records=records))["updatedInput"]["records"]
    assert [record.get("to") for record in made] == [["iris@demo"]] * 4 + [root, root, None]
    assert record_lines.decision(post("talker", **records[0]))["updatedInput"] == records[0] | {"to": ["iris@demo"]}
    assert record_lines.decision(post("talker", **records[5])) is None
    assert record_lines.decision(post("iris-1", **records[0])) is None


def bound_to_iris(run: Callable[..., tuple[int, str]], demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    run("order", "add", "--project", "Iris", "--workstream", "Run page", "ship the run page")
    run("job", "add", "builder", "--order", "o1", "--goal", "build it")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "iris-1")
    run("start", demo, "--role", "coordinator", "--project", "Iris")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", demo, "--role", "communicator")
    with state.edit(demo) as data:
        data["orders"]["o1"]["thread"] = THREAD
        data["jobs"]["j1"]["questions"] = [state.new_question(str(state.drive_dir(demo) / "asks" / "j1-7.md"), "Which ring?", state.stamp(), card=CARD)]


def test_the_communicators_record_is_routed_on_the_drive_it_names_while_its_session_holds_a_newer_one(
    run: Callable[..., tuple[int, str]], demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    bound_to_iris(run, demo, monkeypatch)
    run("start", "newer", "--role", "communicator")
    with state.edit("newer") as data:
        data["roles"]["communicator"]["started"] = "2099-01-01T00:00:00-08:00"
    assert state.session_drives("talker") == ["newer", demo]
    answer = {"kind": "answer", "text": "Ring", "to": ["root@demo", "root@newer"], "re": ["7"], "attrs": {"job": "j1", "maps": "none"}}
    assert record_lines.decision(post("talker", **answer))["updatedInput"]["to"] == ["iris@demo", "root@newer"]


def test_his_words_in_the_thread_of_a_card_an_orders_job_drew_go_to_the_session_hearing_that_order(
    run: Callable[..., tuple[int, str]], demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    bound_to_iris(run, demo, monkeypatch)
    feedback = {"kind": "owner", "text": "that card is unclear", "to": ["root@demo"], "attrs": {"thread": CARD_THREAD}}
    assert record_lines.decision(post("talker", **feedback))["updatedInput"]["to"] == ["iris@demo"]
    assert state.thread_order(state.load(demo), CARD_THREAD) == "o1"
