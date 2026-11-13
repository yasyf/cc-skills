import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import record_lines
from conftest import SHORT_ASK, Thread
from drive import agents, limits, reads, settled, state, view

SESSION = "session-under-test"
ALERT = "1794413465.441409"
LONG = (
    "POST thread 1794286392.313739: the journal layout is merged and installed; his card for the runner rows is up,"
    " waits durable-agents:j12-6581\nproof: https://github.com/yasyf/cc-skills/pull/4093"
)
SSQL = "are we doing the ssql release foe it"
FOOTERS = "why didnt our release footers whatever catch this and bring ssql along with API"


def post(session: str = SESSION, **tool_input: object) -> dict:
    return {"session_id": session, "tool_name": "mcp__plugin_cc-inbox_cci__post", "tool_input": tool_input}


def refusal(made: dict | None) -> str:
    assert made and made["permissionDecision"] == "deny"
    return made["permissionDecisionReason"]


def kept(name: str, job_id: str, title: str, card: str | None = None) -> dict[str, str]:
    with state.edit(name) as data:
        data["orders"].setdefault("o1", {"text": "build it", "from": "terminal", "state": "open", "jobs": [], "due": None, "updated": state.stamp()})
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
        assert record_lines.decision(post(kind=kind, text=LONG, attrs={"about": "his work"})) is None


def test_a_one_line_record_and_another_sessions_record_are_left_alone(demo: str) -> None:
    assert record_lines.decision(post(kind="report", text="FYI j12 merged #4093")) is None
    assert record_lines.decision(post(session="another-session", kind="report", text=LONG)) is None


def test_each_record_of_a_batch_is_cut_and_his_stay_whole(demo: str) -> None:
    asked = f"{LAYOUT}\nproof: https://github.com/yasyf/cc-skills/pull/4093"
    records = [{"kind": "ask", "text": asked, "attrs": kept(demo, "j12", LAYOUT)}, {"kind": "answer", "text": LONG}]
    shortened, whole = record_lines.decision(post(records=records))["updatedInput"]["records"]
    assert shortened["text"] == LAYOUT and shortened["body"] == asked
    assert whole == records[1]


def test_a_long_record_that_attaches_a_file_keeps_its_whole_text_atop_its_body_and_the_file_under_it(demo: str, tmp_path: Path) -> None:
    facts = tmp_path / "j1936.md"
    facts.write_text("DONE dev already fixes it\n")
    asked = f"POST mechanism found: the failing Macs run an older sand, and their reports name migration 10 in {'/x' * 40}/j1936.md"
    made = record_lines.decision(post(records=[post(kind="report", text=LONG)["tool_input"], {"kind": "report", "text": asked, "path": str(facts)}]))
    cut = made["updatedInput"]["records"][1]
    assert cut == {"kind": "report", "text": limits.first_line(asked), "body": f"{asked}\n\nDONE dev already fixes it\n"}
    assert len(cut["text"]) <= limits.RECORD_CHARS
    both = record_lines.decision(post(kind="report", text=asked, body="facts: drive job report j1936", path=str(facts)))["updatedInput"]
    assert both == {"kind": "report", "text": limits.first_line(asked), "body": f"{asked}\n\nfacts: drive job report j1936\n\nDONE dev already fixes it\n"}


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
    assert "`drive job ask j1` with no file for a remote lane's ASK the watch did not keep, which reads its report" in reason
    with state.edit(demo) as data:
        data["jobs"]["j1"]["questions"][-1]["closed"] = state.new_close("answered", "One snapshot file", CARD)
    answered = refusal(record_lines.decision(post(kind="ask", text=LAYOUT, attrs=earlier)))
    assert f"j1's question \"{LAYOUT}\" kept in {earlier['ask']} ended answered, so its card never draws again" in answered
    assert record_lines.decision(post(kind="ask", text=HANDLE, attrs=kept(demo, "j1", HANDLE))) is None


def test_an_ask_naming_another_open_question_or_another_jobs_file_is_refused_and_one_redoing_its_own_open_card_goes_out(
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    shown = {"url": CARD, "question": LAYOUT, "asked": state.stamp(), "card": {"title": LAYOUT, "waits": "demo:j1-7"}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [shown], "pr_cards": [], "closed": []})
    earlier = kept(demo, "j1", LAYOUT, card=CARD)
    assert "which this record does not ask" in refusal(record_lines.decision(post(kind="ask", text=HANDLE, attrs=earlier)))
    elsewhere = kept(demo, "j2", HANDLE)
    assert f"{elsewhere['ask']} keeps no question of j1" in refusal(record_lines.decision(post(kind="ask", text=HANDLE, attrs=elsewhere | {"job": "j1"})))
    redone = earlier | {"waits": "demo:j1-7"}
    assert record_lines.decision(post(kind="ask", text=f"ASK (o1, j1): {LAYOUT}", attrs=redone)) is None


def test_a_jobs_second_question_from_a_remote_lane_goes_out_on_the_new_file_drive_job_ask_keeps(
    run: Callable[..., tuple[int, str]], demo: str, tmp_path: Path
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "design the run page")
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


def test_a_ready_record_to_the_communicator_for_a_pr_the_drive_owes_no_tap_on_is_refused_and_a_line_saying_he_tapped_it_goes_out(
    run: Callable[..., tuple[int, str]], demo: str
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "fix the update button")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it")
    with state.edit(demo) as data:
        data["prs"]["Forge-AI/monorepo#4242"] = state.new_pr(TAPPED, "j1", "o1") | {"head": "4a4d1f0aa1", "checked": {"head": "4a4d1f0aa1", "orders": ["o1"], "patch": ""}}
    assert record_lines.decision(post(kind="ready", text="j1 green, his order o1", pr=[TAPPED], to=TO)) is None
    with state.edit(demo) as data:
        data["prs"]["Forge-AI/monorepo#4242"] |= {"tap": {"label": "Approve", "at": "2026-11-10T18:40:00-08:00", "head": "abc", "source": "slack"}}
    reason = refusal(record_lines.decision(post(kind="ready", text="j1 green, his order o1", pr=[TAPPED], to=TO)))
    assert "Forge-AI/monorepo#4242: he tapped Approve at " in reason
    for said in ("carries his Approve tap", "he tapped Approve at `6:40 PM`", "his approval landed at `6:40 PM`"):
        for to in (["root@demo"], META):
            assert record_lines.decision(post(kind="ready", text=f"land it, his order o1: {said}", pr=[TAPPED], to=to)) is None
        assert record_lines.decision(post(kind="handoff", text=f"land {TAPPED}: {said}", to=["root@demo"])) is None
    for said in ("no longer waits on his tap", "he did tap Approve", "he tapped Approve"):
        assert record_lines.decision(post(kind="report", text=f"FYI {TAPPED} {said}", to=["root@demo"])) is None


def test_a_ready_record_for_a_pr_whose_change_at_its_head_nobody_checked_against_his_words_is_refused_naming_its_orders_holder(
    run: Callable[..., tuple[int, str]], demo: str
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Releases", "fix the update button")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it")
    with state.edit(demo) as data:
        data["prs"]["Forge-AI/monorepo#4242"] = state.new_pr(TAPPED, "j1", "o1") | {"head": "ebc103801ea6", "checked": {"head": "4a4d1f0aa1", "orders": ["o1"], "patch": ""}}
        data["orders"]["o1"]["owner"] = "communicator"
    ready = post(kind="ready", text="j1 green, his order o1", pr=[TAPPED], to=TO)
    reason = refusal(record_lines.decision(ready))
    assert reason.startswith(
        "refused: this ready record asks his tap on Forge-AI/monorepo#4242 at ebc1038 (order o1, held by the communicator), whose change at that head is not checked against his words"
    )
    assert "The watch's drive pr check judges it once its builder returns" in reason
    with state.edit(demo) as data:
        data["prs"]["Forge-AI/monorepo#4242"]["checked"]["head"] = "ebc103801ea6"
    assert record_lines.decision(ready) is None


TO = ["communicator@demo"]
META = ["meta@demo"]
PROGRESS = {"progress": "true"}
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


def test_a_reader_or_designer_posting_to_him_is_refused_and_its_defect_goes_out_to_the_meta_session(demo: str) -> None:
    for agent in ("long-running:reader", "long-running:designer"):
        reason = refusal(record_lines.decision(by(agent, kind="report", text="j12 found the leak", to=["owner@demo"])))
        assert "drive job report <j>" in reason and "meta@<drive>" in reason
    assert record_lines.decision(by("long-running:reader", kind="defect", text="drive job report fails", to=META, lane="reader")) is None
    assert record_lines.decision(by("long-running:reader", kind="defect", text="drive job report fails", to=TO, lane="reader"))["updatedInput"]["to"] == META
    assert record_lines.decision(by("long-running:handler", kind="note", text="j12 found the leak", to=["owner@demo"])) is None


def test_a_handler_returns_a_defect_line_instead_of_posting_one(communicator: str) -> None:
    reason = refusal(record_lines.decision(by("long-running:handler", kind="defect", text="cc-slack say fails", to=["root@demo"])))
    assert "DEFECT <tool>" in reason
    assert record_lines.decision(post(kind="defect", text="cc-slack say fails", to=META)) is None
    assert record_lines.decision(by("long-running:designer", kind="defect", text="drive job ask fails", to=META, lane="designer")) is None
    assert record_lines.decision(by("long-running:handler", kind="report", text="POST j12 merged", to=TO, lane="handler", attrs=PROGRESS)) is None


def test_an_ask_naming_no_job_is_refused(demo: str) -> None:
    asked = kept(demo, "j750", IDLE)
    assert refusal(record_lines.decision(post(kind="ask", text=IDLE, attrs={"ask": asked["ask"]}))) == settled.UNJOBBED
    assert record_lines.decision(post(kind="ask", text=IDLE, attrs=asked)) is None


def test_a_ready_record_says_which_kind_its_prs_are(demo: str) -> None:
    assert "his order o<n>" in refusal(record_lines.decision(post(kind="ready", text="#4244 is ready", pr=[TAPPED])))
    for text in ("#4244, his order o284, is ready", "Drive tooling: the watch reads taps", "port of #4100"):
        assert record_lines.decision(post(kind="ready", text=text)) is None


def test_a_ready_record_naming_its_order_takes_its_kind_from_that_order(run: Callable[..., tuple[int, str]], demo: str) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: fix ask")
    made = record_lines.decision(post(kind="ready", text="#4244 is ready", attrs={"order": "o1"}))
    assert made["updatedInput"]["text"] == '#4244 is ready; his order o1 "ship the fix"'
    assert record_lines.decision(post(kind="ready", text="#4244 is ready", attrs={"order": "o2"}))["updatedInput"]["text"] == "#4244 is ready; Drive tooling: fix ask"
    assert "his order o<n>" in refusal(record_lines.decision(post(kind="ready", text="#4244 is ready", attrs={"order": "o9"})))


def test_an_ask_attaching_its_kept_file_takes_it_as_its_ask(demo: str) -> None:
    asked = kept(demo, "j1", IDLE)
    made = record_lines.decision(post(kind="ask", text=IDLE, path=asked["ask"], attrs={"job": "j1"}))
    assert made["updatedInput"]["attrs"] == asked


def test_an_ask_attaching_its_kept_file_with_no_text_asks_the_line_cci_stores_from_it(demo: str) -> None:
    asked = kept(demo, "j1", IDLE)
    file = Path(asked["ask"])
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(f"\n  ASK {IDLE}\nThe idle ones hold a slot each.\n")
    assert record_lines.decision(post(kind="ask", path=asked["ask"], attrs=asked)) is None
    made = record_lines.decision(post(kind="ask", path=asked["ask"], attrs={"job": "j1"}))["updatedInput"]
    assert made["attrs"] == asked and "text" not in made
    file.write_text(f"ASK {LAYOUT}\n")
    reason = refusal(record_lines.decision(post(kind="ask", path=asked["ask"], attrs=asked)))
    assert "which this record does not ask" in reason and "on the cci `post` tool" in reason and "cci post --" not in reason


def test_a_report_attaching_a_file_with_no_text_is_checked_by_the_files_first_line(demo: str, tmp_path: Path) -> None:
    facts = tmp_path / "j702.md"
    facts.write_text("POST j702 merged\nproof: https://github.com/yasyf/cc-skills/pull/4093\n")
    assert record_lines.decision(post(kind="report", to=TO, path=str(facts), attrs=PROGRESS)) is None
    facts.write_text("j702 merged\n")
    assert "`POST`" in refusal(record_lines.decision(post(kind="report", to=TO, path=str(facts), attrs=PROGRESS)))


def test_an_ask_for_a_job_on_an_open_incident_order_says_so_and_names_the_incidents_thread(run: Callable[..., tuple[int, str]], demo: str) -> None:
    run("order", "add", "--title", "Short title", "--project", "SandSQL", "--workstream", "Alerts", "--from", f"slack:C0822AHFY3G/{ALERT}", "unknown column case_v2.audit_status", "--incident", "--parts", "fix", "first check")
    asked = kept(demo, "j1", IDLE) | {"order": "o1"}
    marked = asked | {"incident": "true", "thread": ALERT}
    assert record_lines.decision(post(kind="ask", text=IDLE, attrs=asked))["updatedInput"]["attrs"] == marked
    assert record_lines.decision(post(kind="ask", text=IDLE, attrs=asked | {"thread": THREAD}))["updatedInput"]["attrs"] == marked
    assert record_lines.decision(post(kind="ask", text=IDLE, attrs=marked)) is None
    with state.edit(demo) as data:
        data["orders"]["o1"]["state"] = "done"
    assert record_lines.decision(post(kind="ask", text=IDLE, attrs=asked)) is None


def test_a_record_goes_out_without_a_max_words_attr_on_any_kind(demo: str) -> None:
    for record in ({"kind": "report", "text": "FYI j702 merged", "to": TO}, {"kind": "note", "text": "the watch reads taps"}):
        assert record_lines.decision(post(**record, attrs={"max_words": "200"})) == {"updatedInput": record | {"attrs": {}}}
        assert record_lines.decision(post(**record)) is None


def test_a_report_opens_post_or_fyi(demo: str) -> None:
    assert "`POST`" in refusal(record_lines.decision(post(kind="report", text="j702 merged", to=TO)))
    assert record_lines.decision(post(kind="report", text="POST j702 merged", to=TO, attrs=PROGRESS)) is None
    assert record_lines.decision(post(kind="report", text="j702 merged", to=["builder@demo"])) is None


def test_a_reports_url_names_what_it_measured_never_his_message_its_thread_names(demo: str, turn: Path) -> None:
    his = "https://acme.slack.com/archives/D123/p1794480738192769"
    cause = {"kind": "report", "text": "POST cause of the read-only alert: the agent row is refused", "to": TO, "attrs": PROGRESS | {"thread": "1794480738.192769"}}
    assert f"{his} is a message in his DM, which --attr thread=1794480738.192769 names" in refusal(record_lines.decision(coordinator(turn, **cause, url=his)))
    assert "--attr thread=1794200000.000100" in refusal(record_lines.decision(coordinator(turn, **cause, url=f"{his}?thread_ts=1794200000.000100&cid=D123")))
    for url in ("https://app.datadoghq.com/apm/trace/6af59a19000000005751719c320fc91b", "https://acme.slack.com/archives/C777/p1794413465441409"):
        assert record_lines.decision(coordinator(turn, **cause, url=url)) is None
    assert record_lines.decision(post(kind="owner", text="why is it read-only?", url=his, attrs={"about": "his work"})) is None


def test_a_catch_up_goes_at_his_dm_root_without_its_thread(demo: str, turn: Path) -> None:
    for attrs in ({"thread": THREAD, "catch_up": "true"}, {"thread": THREAD, "progress": "true"}):
        made = record_lines.decision(coordinator(turn, kind="report", text="POST *Catch-up since 9 PM*", to=TO, attrs=attrs))
        assert made["updatedInput"]["attrs"] == {key: value for key, value in attrs.items() if key != "thread"}
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


def test_a_report_carrying_a_part_in_an_attr_but_part_is_refused_naming_the_one_part_attr_that_carries_both_in_a_batch_too(
    run: Callable[..., tuple[int, str]], demo: str, turn: Path
) -> None:
    run("order", "add", "--title", "Short title", "--project", "Platform", "--workstream", "Releases", f"1) {SSQL}, and 2) {FOOTERS}?", "--thread", THREAD, "--parts", SSQL, FOOTERS, "the --only card")
    both = f"o1:{SSQL};o1:{FOOTERS}"
    answered = {"text": "POST o1, both questions: the SandSQL release is live.", "attrs": {"order": "o1", "thread": THREAD, "part": f"o1:{SSQL}", "part2": f"o1:{FOOTERS}"}}
    reason = refusal(record_lines.decision(coordinator(turn, kind="report", to=TO, records=[answered])))
    assert "the drive reads no `part2` attr on a report" in reason and "`part=<o>:<a>;<o>:<b>`" in reason
    assert f'`--attr part="{both}"` in place of `part`, `part2`' in reason and agents.ruled(reason) == {"judged": False}
    for stray in ({"parts": FOOTERS}, {"part_2": FOOTERS}, {"second": f"o1:{FOOTERS}"}):
        reason = refusal(record_lines.decision(coordinator(turn, kind="report", text=answered["text"], to=TO, attrs={"order": "o1", "thread": THREAD, "part": f"o1:{SSQL}", **stray})))
        assert f"no `{next(iter(stray))}` attr on a report" in reason and f'`--attr part="{both}"`' in reason
    assert record_lines.decision(coordinator(turn, kind="report", to=TO, records=[answered | {"attrs": {"order": "o1", "thread": THREAD, "part": both}}])) is None


def test_a_report_that_finishes_an_order_names_one_of_its_threads(run: Callable[..., tuple[int, str]], demo: str, turn: Path) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    with state.edit(demo) as data:
        data["orders"]["o1"]["thread"] = ["D123", THREAD]
    reason = refusal(record_lines.decision(coordinator(turn, kind="report", text="POST j1 merged", to=TO, attrs={"close": "o1", "thread": OTHER})))
    assert f"thread {OTHER} is none of its threads: {THREAD}" in reason and "pass re naming the record of his message" in reason and "drive order set" not in reason
    assert record_lines.decision(coordinator(turn, kind="report", text="POST j1 merged", to=TO, attrs={"close": "o1", "thread": THREAD})) is None
    assert record_lines.decision(coordinator(turn, kind="report", text="POST j1 is building", to=TO, attrs={"thread": OTHER, **PROGRESS})) is None


def test_a_report_answering_his_message_finishes_an_order_in_that_messages_thread_and_no_other(
    run: Callable[..., tuple[int, str]], demo: str, turn: Path, cci_records: dict[str, dict]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    with state.edit(demo) as data:
        data["orders"]["o1"]["thread"] = ["D123", THREAD]
    cci_records["671734"] = {"seq": 671734, "kind": "owner", "lane": "communicator", "refs": {"url": f"https://acme.slack.com/archives/D123/p{OTHER.replace('.', '')}"}, "fields": {"attrs": {}}}
    assert record_lines.decision(coordinator(turn, kind="report", text="POST j1 merged", to=TO, re=["671734"], attrs={"part": "ship the fix", "close": "o1", "thread": OTHER})) is None
    reason = refusal(record_lines.decision(coordinator(turn, kind="report", text="POST j1 merged", to=TO, re=["671734"], attrs={"close": "o1", "thread": ALERT})))
    assert f"thread {ALERT} is none of its threads: {THREAD}" in reason and "pass re naming the record of his message" in reason


def test_a_report_about_an_order_in_another_orders_thread_is_refused_when_written_until_its_re_names_his_message_there(
    run: Callable[..., tuple[int, str]], demo: str, turn: Path, cci_records: dict[str, dict]
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the 50 dashboard improvements")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "why the card asked for Okta MFA")
    with state.edit(demo) as data:
        data["orders"]["o1"]["thread"] = ["D123", THREAD]
        data["orders"]["o2"]["thread"] = ["D123", OTHER]
    cci_records["672468"] = {"seq": 672468, "kind": "owner", "lane": "communicator", "refs": {"url": f"https://acme.slack.com/archives/D123/p1794379200000100?thread_ts={OTHER}&cid=D123"}, "fields": {"attrs": {}}}
    unnamed = {"progress": "true", "order": "o1", "thread": OTHER}
    reason = refusal(record_lines.decision(coordinator(turn, kind="report", text="POST The table fix is building now", to=TO, attrs=unnamed)))
    assert f"this report is about o1, and thread {OTHER} is none of its threads: {THREAD}" in reason and "pass re naming the record of his message" in reason
    assert record_lines.decision(coordinator(turn, kind="report", text="POST The table fix is building now", to=TO, re="672468", attrs=unnamed)) is None
    assert record_lines.decision(coordinator(turn, kind="report", text="POST The table fix is building now", to=TO, attrs={"progress": "true", "order": "o1", "thread": THREAD})) is None


def test_a_report_about_an_order_in_another_orders_thread_his_message_started_is_taken_when_written_and_a_thread_he_did_not_start_refused(
    run: Callable[..., tuple[int, str]], demo: str, turn: Path, slack_thread: Thread
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the incident badge counts only live incidents")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "how is the dashboard looking")
    with state.edit(demo) as data:
        data["orders"]["o1"]["thread"] = ["D123", THREAD]
        data["orders"]["o2"]["thread"] = ["D123", OTHER]
    slack_thread.messages.append({"ts": OTHER, "thread_ts": OTHER, "from_user": True, "text": "how is the dashboard looking"})
    assert record_lines.decision(coordinator(turn, kind="report", text="POST The incident badge counts only live incidents.", to=TO, attrs={"close": "o1", "thread": OTHER})) is None
    reason = refusal(record_lines.decision(coordinator(turn, kind="report", text="POST The fix is building.", to=TO, attrs={"progress": "true", "order": "o1", "thread": ALERT})))
    assert f"this report is about o1, and thread {ALERT} is none of its threads: {THREAD}, nor one his message started." in reason


def test_a_report_whose_thread_is_none_goes_without_it_and_one_naming_no_slack_thread_is_refused_every_time(run: Callable[..., tuple[int, str]], demo: str, turn: Path) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix", "--parts", "cause", "fix")
    for named in ({"close": "o1"}, {"part": "o1:cause"}):
        made = record_lines.decision(coordinator(turn, kind="report", text="POST j1 merged", to=TO, attrs=named | {"thread": "none"}))
        assert made["updatedInput"]["attrs"] == named
    for thread in ("dm", "1794478810"):
        reason = refusal(record_lines.decision(coordinator(turn, kind="report", text="POST j1 merged", to=TO, attrs={"part": "o1:cause", "thread": thread})))
        assert f"thread {thread} is no Slack thread" in reason and "leave thread out for a post at his DM root" in reason
        assert agents.ruled(reason) == {"judged": False}
    assert record_lines.decision(coordinator(turn, kind="report", text="POST j1 merged", to=TO, attrs={"close": "o1"})) is None


def test_a_part_whose_name_a_cut_or_its_writer_left_spaces_around_is_saved_trimmed_so_a_part_attr_names_it(
    run: Callable[..., tuple[int, str]], demo: str, turn: Path
) -> None:
    words = "both PRs he approved this morning and the stack he tapped an hour later sat for two hours with no attempt to land them"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", words)
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "fix the search worker", "--parts", "fix ", " first check")
    run("order", "set", "o2", "--parts", "fix", " first check", "cause ")
    whole = "both PRs he approved this morning and the stack he tapped an hour later sat for"
    assert [list(order["parts"]) for order in state.load(demo)["orders"].values()] == [[whole], ["fix", "first check", "cause"]]
    assert record_lines.decision(coordinator(turn, kind="report", text="POST the stack landed", to=TO, attrs={"part": f"o1:{whole}", "close": "o1"})) is None
    assert record_lines.decision(coordinator(turn, kind="report", text="POST the fix is live", to=TO, attrs={"part": "o2:fix"})) is None


def test_a_post_report_telling_him_the_drive_failed_goes_out_warning_the_session_until_it_says_how_that_is_prevented(
    run: Callable[..., tuple[int, str]], demo: str, turn: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    with state.edit(demo) as data:
        data["orders"]["o1"]["thread"] = ["D123", THREAD]
    failed = "POST j1 merged; its card was refused since nobody checked that PR against your words"
    warned = record_lines.decision(coordinator(turn, kind="report", text=failed, to=TO, attrs={"close": "o1", "thread": THREAD}))
    assert set(warned) == {"additionalContext"}
    assert warned["additionalContext"].startswith("record_lines: this report goes out as written")
    assert warned["additionalContext"].endswith(f"```\n{failed}\n```")
    prevented = f"{failed}; {{order:o2}} fixes the card hook"
    assert record_lines.decision(coordinator(turn, kind="report", text=prevented, to=TO, attrs={"close": "o1", "thread": THREAD})) is None
    old_pod = "POST j1 merged; the check that this pod still runs the agent compares writer, so an old pod is refused"
    assert record_lines.decision(coordinator(turn, kind="report", text=old_pod, to=TO, attrs={"close": "o1", "thread": THREAD}))["additionalContext"].endswith(f"```\n{old_pod}\n```")
    monkeypatch.setattr(reads, "run", lambda argv, checkout: json.dumps({"kind": "defect", "text": "check-post refused a status table", "refs": {}}))
    cited = "POST j1 merged; tables post as Slack tables again, {record:671981}"
    warned = record_lines.decision(coordinator(turn, kind="report", text=cited, to=TO, attrs={"close": "o1", "thread": THREAD}))
    assert warned["additionalContext"].endswith(f"```\n{cited}\n```")


def test_a_post_report_names_what_its_post_finishes_and_the_last_part_of_an_order_closes_it(run: Callable[..., tuple[int, str]], demo: str, turn: Path) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "why does the search worker OOM?", "--parts", "cause", "fix")
    run("job", "add", "builder", "--order", "o1", "--part", "cause", "--goal", "find the cause")
    merged = {"kind": "report", "text": "POST the search worker's fix merged", "to": TO}
    reason = refusal(record_lines.decision(coordinator(turn, **merged, attrs={"job": "j1"})))
    assert "here o1, the open order it is about" in reason and 'one of "cause", "fix"' in reason and "`--attr progress=true`" in reason
    reason = refusal(record_lines.decision(coordinator(turn, **merged)))
    assert "`--attr close=<o,…>` naming each order it finishes, `--attr part=<o>:<part>` naming the part it delivers, or" in reason
    for named in ({"job": "j1", "close": "o1"}, {"job": "j1", "part": "cause"}, {"job": "j1", "progress": "true"}, {"catch_up": "true"}, {"close": "o1"}):
        assert record_lines.decision(coordinator(turn, **merged, attrs=named)) is None
    assert record_lines.decision(coordinator(turn, kind="report", text="FYI the search worker's fix merged", to=TO, attrs={"job": "j1"})) is None
    with state.edit(demo) as data:
        data["orders"]["o1"]["parts"]["cause"]["posted"] = CARD
    assert 'delivers "fix", the last part of o1' in refusal(record_lines.decision(coordinator(turn, **merged, attrs={"job": "j1", "part": "fix"})))
    assert record_lines.decision(coordinator(turn, **merged, attrs={"job": "j1", "part": "fix", "close": "o1"})) is None
    with state.edit(demo) as data:
        data["orders"]["o1"]["incident"] = True
        data["orders"]["o1"]["fixed"] = "release 13990 ships #54336 on sand"
    assert record_lines.decision(coordinator(turn, **merged, attrs={"job": "j1", "part": "fix"})) is None
    with state.edit(demo) as data:
        data["orders"]["o1"]["state"] = "done"
    assert "the open order it is about" not in refusal(record_lines.decision(coordinator(turn, **merged, attrs={"job": "j1"})))


def test_a_post_report_on_an_order_carries_each_part_a_finished_job_answered_and_no_post_delivered(
    run: Callable[..., tuple[int, str]], demo: str, turn: Path
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "why does the search worker OOM?", "--parts", "cause", "fix")
    run("job", "add", "reader", "--order", "o1", "--part", "cause", "--goal", "find the cause")
    progress = {"kind": "report", "text": "POST the search worker's fix is building", "to": TO}
    assert record_lines.decision(coordinator(turn, **progress, attrs={"job": "j1", **PROGRESS})) is None
    with state.edit(demo) as data:
        data["jobs"]["j1"]["state"] = "done"
    reason = refusal(record_lines.decision(coordinator(turn, **progress, attrs={"job": "j1", **PROGRESS})))
    assert 'o1\'s "cause" was answered by j1, which finished' in reason and '`--attr part="o1:cause"`' in reason
    assert 'drive order posted o1 <that post\'s link> --part "cause"' in reason
    assert '`--attr part="o1:fix;o1:cause"`' in refusal(record_lines.decision(coordinator(turn, **progress, attrs={"job": "j1", "part": "fix"})))
    for carried in ({"job": "j1", "part": "cause"}, {"job": "j1", "part": "o1:cause"}, {"job": "j1", "close": "o1"}):
        assert record_lines.decision(coordinator(turn, **progress, attrs=carried)) is None
    assert record_lines.decision(coordinator(turn, kind="report", text="FYI j1 found the cause", to=TO, attrs={"job": "j1"})) is None
    run("job", "add", "reader", "--order", "o1", "--part", "cause", "--goal", "check the cause again")
    assert record_lines.decision(coordinator(turn, **progress, attrs={"job": "j1", **PROGRESS})) is None
    with state.edit(demo) as data:
        data["jobs"]["j2"]["state"] = "stopped"
        data["orders"]["o1"]["parts"]["cause"]["posted"] = CARD
    assert record_lines.decision(coordinator(turn, **progress, attrs={"job": "j1", **PROGRESS})) is None


def test_a_report_on_an_open_incident_neither_needs_nor_names_its_fix_until_the_watch_records_it_live(
    run: Callable[..., tuple[int, str]], demo: str, turn: Path
) -> None:
    run("order", "add", "--title", "Short title", "--project", "Platform", "--workstream", "Journal", "the journal will not open on two Macs", "--parts", "fix", "first check")
    run("job", "add", "reader", "--order", "o1", "--part", "fix", "--goal", "find what fixes it")
    with state.edit(demo) as data:
        data["orders"]["o1"]["incident"] = True
        data["jobs"]["j1"]["state"] = "done"
    mechanism = {"kind": "report", "text": "POST mechanism found: the failing Macs run an older sand, and no release has reached them yet", "to": TO}
    assert record_lines.decision(coordinator(turn, **mechanism, attrs={"order": "o1", **PROGRESS})) is None
    reason = refusal(record_lines.decision(coordinator(turn, **mechanism, attrs={"order": "o1", "part": "o1:fix", **PROGRESS})))
    assert 'o1\'s "fix" is the fix of an open incident' in reason and 'drop "o1:fix" from the part attr' in reason and "`--attr close=o1`" in reason
    assert record_lines.decision(coordinator(turn, **mechanism, attrs={"order": "o1", "part": "o1:fix", "close": "o1"})) is None
    with state.edit(demo) as data:
        data["orders"]["o1"]["fixed"] = "release 13990 ships #54336 on sand"
    assert '`--attr part="o1:fix"`' in refusal(record_lines.decision(coordinator(turn, **mechanism, attrs={"order": "o1", **PROGRESS})))
    assert record_lines.decision(coordinator(turn, **mechanism, attrs={"order": "o1", "part": "o1:fix", **PROGRESS})) is None


def test_a_part_attr_names_parts_of_several_orders_and_each_must_be_one_the_drive_holds(run: Callable[..., tuple[int, str]], demo: str, turn: Path) -> None:
    run("order", "add", "--title", "Short title", "--project", "Platform", "--workstream", "Search", "fix the search worker", "--parts", "fix", "first check")
    run("order", "add", "--title", "Short title", "--project", "Platform", "--workstream", "Search", "raise its RAM", "--parts", "increase the RAM")
    shared = {"kind": "report", "text": "POST the search worker's fix and its RAM increase are live", "to": TO}
    assert record_lines.decision(coordinator(turn, **shared, attrs={"order": "o1", "part": "fix;o2:increase the RAM", "close": "o2"})) is None
    assert 'delivers "increase the RAM", the last part of o2' in refusal(record_lines.decision(coordinator(turn, **shared, attrs={"order": "o1", "part": "fix;o2:increase the RAM"})))
    reason = refusal(record_lines.decision(coordinator(turn, **shared, attrs={"order": "o1", "part": "o1:the fix", **PROGRESS})))
    assert 'o1 has no part "the fix"' in reason and reason.endswith('its parts: "fix", "first check"')
    assert "names o9, no order of drive demo" in refusal(record_lines.decision(coordinator(turn, **shared, attrs={"order": "o1", "part": "fix;o9:fix"})))
    assert state.part_items({"part": "first check; o2:increase the RAM"}, "o1") == [("o1", "first check"), ("o2", "increase the RAM")]


def test_the_coordinator_sends_one_report_per_thread_per_turn(demo: str, turn: Path) -> None:
    tool = "mcp__plugin_cc-inbox_cci__post"
    earlier = {"kind": "report", "text": "POST j1 merged", "to": TO, "attrs": {"thread": THREAD, **PROGRESS}}
    called(turn, "refused", tool, earlier, failed=True)
    assert record_lines.decision(coordinator(turn, **earlier)) is None
    called(turn, "first", tool, earlier)
    assert "one report per thread per turn" in refusal(record_lines.decision(coordinator(turn, kind="report", text="POST j2 merged", to=TO, attrs={"thread": THREAD, **PROGRESS})))
    assert record_lines.decision(coordinator(turn, kind="report", text="POST j2 merged", to=TO, attrs={"thread": OTHER, **PROGRESS})) is None
    wake = '<channel source="plugin:cc-inbox:cci" kind="handoff" lane="watch" seq="662305">\n@2:36 AM\n662305 handoff watch j242 green'
    with turn.open("a") as transcript:
        transcript.write(json.dumps({"type": "user", "isMeta": True, "message": {"role": "user", "content": "the skill's body"}}) + "\n")
    assert "one report per thread per turn" in refusal(record_lines.decision(coordinator(turn, kind="report", text="POST j2 merged", to=TO, attrs={"thread": THREAD, **PROGRESS})))
    with turn.open("a") as transcript:
        transcript.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "stop_reason": "end_turn", "content": [{"type": "text", "text": "Sent."}]}}) + "\n")
        transcript.write(json.dumps({"type": "user", "isMeta": True, "message": {"role": "user", "content": wake}}) + "\n")
    called(turn, "now", tool, earlier)
    assert record_lines.decision(coordinator(turn, kind="report", text="POST j2 merged", to=TO, attrs={"thread": THREAD, **PROGRESS})) is None
    assert "one report per thread per turn" in refusal(record_lines.decision(coordinator(turn, records=[earlier, earlier])))
    called(turn, "woken", tool, earlier)
    assert "the next wake" in refusal(record_lines.decision(coordinator(turn, kind="report", text="POST j3 merged", to=TO, attrs={"thread": THREAD, **PROGRESS})))


def test_a_report_on_an_open_incident_goes_however_many_its_thread_already_had_this_turn(run: Callable[..., tuple[int, str]], demo: str, turn: Path) -> None:
    run("order", "add", "--title", "Short title", "--project", "Platform", "--workstream", "Journal", "the journal will not open on two Macs", "--thread", THREAD, "--incident", "--parts", "fix", "first check")
    called(turn, "first", "mcp__plugin_cc-inbox_cci__post", {"kind": "report", "text": "POST the fix job is changing the journal's open call", "to": TO, "attrs": {"thread": THREAD, "order": "o1", **PROGRESS}})
    second = {"kind": "report", "text": "POST the first check found the newest build fails too", "to": TO, "attrs": {"thread": THREAD, "order": "o1", **PROGRESS}}
    assert record_lines.decision(coordinator(turn, **second)) is None
    with state.edit(demo) as data:
        data["orders"]["o1"]["incident"] = False
    assert "one report per thread per turn" in refusal(record_lines.decision(coordinator(turn, **second)))


def said(count: int) -> str:
    return " ".join(["word"] * count)


def test_a_post_report_past_a_posts_length_goes_to_the_handler_whose_post_say_checks(demo: str) -> None:
    assert "permissionDecision" not in record_lines.decision(post(kind="report", text=f"POST {said(90)}", to=TO, attrs=PROGRESS))


def test_a_report_saying_something_waits_on_him_names_its_card(demo: str) -> None:
    assert "--attr waits=" in refusal(record_lines.decision(post(kind="report", text="POST the copy fix waits on his pick", to=TO, attrs=PROGRESS)))
    assert record_lines.decision(post(kind="report", text="POST the copy fix waits on his pick", to=TO, attrs={"waits": "durable-agents:j750-661229", **PROGRESS})) is None
    assert record_lines.decision(post(kind="report", text="POST the copy fix waits on his pick {card:durable-agents:j750-661229}", to=TO, attrs=PROGRESS)) is None
    assert record_lines.decision(post(kind="report", text="POST https://github.com/yasyf/cc-skills/pull/4093 waits on his pick", to=TO, attrs=PROGRESS)) is None


def test_a_wait_on_anything_else_says_how_long_and_whether_it_runs(demo: str) -> None:
    assert "how long it has waited" in refusal(record_lines.decision(post(kind="report", text="POST j702 waits on CI", to=TO, attrs=PROGRESS)))
    assert record_lines.decision(post(kind="report", text="POST j702 waits on CI, 40 min, run 123 running", to=TO, attrs=PROGRESS)) is None


def test_a_job_is_called_stuck_only_after_a_read_of_its_transcript(demo: str, turn: Path) -> None:
    stuck = {"kind": "report", "text": "POST j702 is stuck with no line for 40 minutes", "to": TO, "attrs": PROGRESS}
    assert "`drive job replies <j>`" in refusal(record_lines.decision(coordinator(turn, **stuck)))
    called(turn, "old", "Bash", {"command": "drive job replies j702"}, age=timedelta(minutes=30))
    assert "j702" in refusal(record_lines.decision(coordinator(turn, **stuck)))
    called(turn, "read", "Bash", {"command": "drive job replies j702"})
    assert record_lines.decision(coordinator(turn, **stuck)) is None


STALL = (
    "POST The builder for your pick on the 3:27 AM replica stall started at 4:55 AM, about 25 minutes late: the workflow_runs"
    " export moves to its own read replica and the api replica replays at once, cancelling a conflicting read."
)
FIX_HALF = (
    "POST Yes to both, and the fix half was missing until 4:50 AM. Cause: a reader is on it and has not confirmed one; its"
    " leads are the replication sync deadline (30 s) against a large first snapshot upload on adopted or retried journals,"
    " and the SandSQL sidecar's 2 CPU / 2 Gi limits with the replicator at 1.47 GB. Fix: the first fix job ended at a dead"
    " end with no PR (journal /view and replication /sync failures precede the executor exits, evidence"
    " https://app.datadoghq.com/notebook/1), and nothing replaced it; a second fix job started at 4:50 AM and builds from"
    " the cause as it lands. Prevention: the drive's watch gets a rule that an incident fix job ending with no PR starts the"
    " next fix job in the same minute, sent to the tooling session at 4:54 AM. Next line by 5:05 AM."
)
FIX_JOBS = (
    "Jobs: j1784 (codex fix, ended BLOCKED, record 46b398c), j1845 (cause reader, running since about 4:25 AM), j1860"
    " (Opus builder on the fix part, started 4:50 AM)."
)


def test_a_report_typing_when_past_work_happened_is_refused_naming_its_stamp_and_a_promise_passes(
    demo: str, turn: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    posted = state.now().replace(hour=4, minute=50, second=56, microsecond=0)
    monkeypatch.setattr(limits, "now", lambda: posted)
    assert refusal(record_lines.decision(coordinator(turn, kind="report", text=STALL, to=TO, attrs=PROGRESS))).startswith(
        'refused: "3:27 AM", "4:55 AM" are times typed for work already done, and the drive\'s stamps say when it happened:'
        " this write is stamped 4:50:56 AM. Name the job or record and let its stamp give the time, {started:<job>}"
    )
    monkeypatch.setattr(limits, "now", lambda: posted.replace(minute=47, second=0))
    assert refusal(record_lines.decision(coordinator(turn, kind="report", text=FIX_HALF, body=FIX_JOBS, to=TO, attrs=PROGRESS))).startswith(
        'refused: "4:50 AM", "4:54 AM", "4:25 AM" are times typed for work already done, and the drive\'s stamps say when it'
        " happened: this write is stamped 4:47:00 AM."
    )
    for promised in ("POST The fix PR is up; next line by 5:40 AM.", "POST The fix lands by 5:40 AM.", "POST A second fix job started at {started:j1}; due 6 AM."):
        assert record_lines.decision(coordinator(turn, kind="report", text=promised, to=TO, attrs=PROGRESS)) is None, promised
    assert record_lines.decision(post(kind="owner", text="his 4:18 AM pick: times come from stamps", attrs={"about": "his work"})) is None


def test_a_report_naming_a_jobs_start_is_refused_with_that_jobs_created_stamp(
    run: Callable[..., tuple[int, str]], demo: str, turn: Path
) -> None:
    run("order", "add", "--title", "Short title", "--project", "SandSQL", "--workstream", "Replication", "fix the replication sync")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix the sync")
    with state.edit(demo) as data:
        data["jobs"]["j1"]["created"] = state.stamp(state.now().replace(hour=4, minute=50, second=21, microsecond=0))
    reason = refusal(record_lines.decision(coordinator(turn, kind="report", text="POST j1 started at 4:55 AM on the fix part", to=TO, attrs=PROGRESS)))
    assert "j1 was created at 4:50:21 AM, which {started:j1} says." in reason


def test_a_report_giving_a_jobs_start_as_when_a_release_started_is_refused_and_one_naming_its_job_passes(demo: str, turn: Path) -> None:
    stood_in = "POST The SandSQL release at 9b6130cd is starting on your pick {started:j1}; api gets no release."
    reason = refusal(record_lines.decision(coordinator(turn, kind="report", text=stood_in, to=TO, attrs=PROGRESS)))
    assert reason.startswith("refused: {started:j1} is when job j1 started, and this line says when a release, build or deploy started")
    went_out = "POST The SandSQL release went out; it started {at:676829}."
    reason = refusal(record_lines.decision(coordinator(turn, kind="report", text=went_out, to=TO, attrs=PROGRESS)))
    assert reason.startswith("refused: {at:676829} is when record 676829 went out")
    named = "POST The SandSQL release's builder started at {started:j1}; api gets no release."
    assert record_lines.decision(coordinator(turn, kind="report", text=named, to=TO, attrs=PROGRESS)) is None


def test_the_communicators_record_for_a_coordinator_goes_to_the_session_hearing_the_order_it_serves_whatever_lane_it_names(
    run: Callable[..., tuple[int, str]], demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Run page", "ship the run page")
    run("order", "add", "--title", "Short title", "--project", "Platform", "--workstream", "Alerts", "fix the alert")
    run("job", "add", "builder", "--order", "o1", "--goal", "build it")
    with state.edit(demo) as data:
        data["orders"]["o1"]["thread"], data["orders"]["o2"]["thread"] = ["D123", THREAD], ["D123", OTHER]
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "iris-1")
    run("start", demo, "--role", "coordinator", "--project", "Iris")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", demo, "--role", "communicator")
    root = ["root@demo"]
    records = [
        {"kind": "owner", "text": "also the logs page", "to": root, "attrs": {"thread": THREAD, "about": "his work"}},
        {"kind": "answer", "text": "Ring", "to": root, "re": ["7"], "attrs": {"job": "j1", "maps": "none"}},
        {"kind": "posted", "text": "card up demo:j1-8", "to": root, "resolves": ["8"], "attrs": {"waits": "demo:j1-8"}},
        {"kind": "refused", "text": "cc-slack refused the card", "to": root, "resolves": ["9"], "attrs": {"job": "j1"}},
        {"kind": "owner", "text": "and the alert", "to": ["iris@demo"], "attrs": {"thread": OTHER, "about": "his work"}},
        {"kind": "owner", "text": "a new order", "to": root, "attrs": {"about": "his work"}},
        {"kind": "report", "text": "FYI j1 merged", "attrs": {"close": "o1"}},
    ]
    made = record_lines.decision(post("talker", records=records))["updatedInput"]["records"]
    assert [record.get("to") for record in made] == [["iris@demo"]] * 4 + [root, root, None]
    about_drive = {"kind": "owner", "text": "the dashboard drops my cards", "to": ["meta@demo"], "attrs": {"thread": THREAD, "about": "drive"}}
    unordered = refusal(record_lines.decision(post("talker", **about_drive)))
    assert isinstance(unordered, agents.Unjudged) and "his words about the drive go out on the drive's `record` tool" in unordered
    assert record_lines.decision(post("iris-1", **about_drive)) is None
    assert record_lines.decision(post("talker", **records[0]))["updatedInput"] == records[0] | {"to": ["iris@demo"]}
    assert record_lines.decision(post("talker", **records[5])) is None
    assert record_lines.decision(post("iris-1", **records[0])) is None
    unsaid = {"kind": "owner", "text": "a new order", "to": root}
    assert 'an owner record names what his words are about, about "drive" for drive tooling' in refusal(record_lines.decision(post("talker", **unsaid)))


def bound_to_iris(run: Callable[..., tuple[int, str]], demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Run page", "ship the run page")
    run("job", "add", "builder", "--order", "o1", "--goal", "build it")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "iris-1")
    run("start", demo, "--role", "coordinator", "--project", "Iris")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "talker")
    run("start", demo, "--role", "communicator")
    with state.edit(demo) as data:
        data["orders"]["o1"]["thread"] = ["D123", THREAD]
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
    feedback = {"kind": "owner", "text": "and cover the logs page", "to": ["root@demo"], "attrs": {"thread": CARD_THREAD, "about": "his work"}}
    assert record_lines.decision(post("talker", **feedback))["updatedInput"]["to"] == ["iris@demo"]
    assert state.thread_order(state.load(demo), CARD_THREAD) == "o1"


def test_his_words_on_his_open_question_go_only_through_the_record_tool_that_ends_it_and_closes_its_card(run: Callable[..., tuple[int, str]], demo: str) -> None:
    run("order", "add", "--title", "Short title", "--project", "Iris", "--workstream", "Run page", "ship the run page")
    run("job", "add", "builder", "--order", "o1", "--goal", "build it")
    with state.edit(demo) as data:
        data["jobs"]["j1"]["questions"] = [state.new_question(str(state.drive_dir(demo) / "asks" / "j1-7.md"), LAYOUT, state.stamp(), card=CARD) | {"waits": "demo:j1-7"}]
    answer = refusal(record_lines.decision(post(kind="answer", text="the lazy copy", re=["7"], attrs={"job": "j1"})))
    assert "his open question" in answer and "send it with the drive's `record` tool" in answer
    under = f"{CARD}?thread_ts={CARD_THREAD}"
    assert "the drive's `record` tool" in refusal(record_lines.decision(post(kind="owner", text="no, keep the runs flat", url=under, attrs={"about": "his work"})))
    assert record_lines.decision(post(kind="owner", text="this card should say why", url=under, attrs={"about": "drive"})) is None
    assert "the drive's `record` tool" in refusal(record_lines.decision(post(kind="owner", text="why is this card still up", topic=f"card:{CARD_THREAD}", attrs={"about": "his work"})))
    assert "the drive's `record` tool" in refusal(record_lines.decision(post(kind="owner", text="drop the sweeper", attrs={"thread_card": "demo:j1-7"})))
    assert record_lines.decision(post(kind="owner", text="a new order", attrs={"about": "his work"})) is None
    assert record_lines.decision(post(kind="answer", text="undone: Ring", re=["7"], attrs={"job": "j1", "undone": "Ring"})) is None
