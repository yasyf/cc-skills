import io
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import communicator_calls
import pytest
from drive import agents, state

Run = Callable[..., tuple[int, str]]
SLACK = "mcp__plugin_cc-slack_cc-slack__"


def hook_output(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], payload: dict) -> str:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    communicator_calls.main()
    return capsys.readouterr().out


def call(tool: str, transcript: Path | None = None, **tool_input: object) -> dict:
    return {
        "session_id": os.environ["CLAUDE_CODE_SESSION_ID"],
        "transcript_path": str(transcript or "/nowhere.jsonl"),
        "tool_name": tool,
        "tool_input": tool_input,
    }


@pytest.fixture
def builder(run: Run, demo: str) -> Callable[..., None]:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it")

    def holds(**fields: object) -> None:
        with state.edit(demo) as data:
            data["jobs"]["j1"].update(fields)

    return holds


def transcript(tmp_path: Path, *commands: str) -> Path:
    path = tmp_path / "session.jsonl"
    at = datetime.now(UTC).isoformat()
    rows = [{"type": "user", "timestamp": at, "message": {"content": "go"}}]
    for index, command in enumerate(commands):
        rows.append({"timestamp": at, "message": {"content": [{"type": "tool_use", "id": f"t{index}", "name": "Bash", "input": {"command": command}}]}})
        rows.append({"timestamp": at, "message": {"content": [{"type": "tool_result", "tool_use_id": f"t{index}", "content": "read"}]}})
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def test_the_communicator_runs_only_its_own_calls_and_starts_a_handler_for_the_rest(
    communicator: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    output = json.loads(hook_output(monkeypatch, capsys, call(SLACK + "slack_thread", channel_id="D123", thread_ts="1.2")))
    reason = output["hookSpecificOutput"]["permissionDecisionReason"]
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert reason.startswith(f"Refused on purpose: the communicator does not run {SLACK}slack_thread itself")
    assert "Start a handler with the drive's handler tool" in reason
    assert communicator_calls.deny_reason(call("Bash", command="cc-transcript show /x/agent-a1.jsonl")).startswith(
        "Refused on purpose: the communicator does not run Bash itself"
    )
    assert communicator_calls.deny_reason(call("Skill", skill="cc-slack:slack")) is not None
    assert communicator_calls.deny_reason(call("mcp__plugin_long-running_drive__handler", brief="Record: 9 Drive: demo")) is None
    for own in (
        call("mcp__plugin_long-running_drive__say", thread_ts="1.2", text="On it: the fix ships next."),
        call(SLACK + "slack_react", channel_id="D123", ts="1.2", name="+1"),
        call("mcp__plugin_long-running_drive__heard", ts="1.2"),
        call("ToolSearch", query="select:SendMessage"),
        call("SendMessage", to="a-7", message="Approve"),
        call("Skill", skill="long-running:long-running"),
        call("Bash", command="drive heard --ts 1.2"),
        call("Bash", command="cci subscribe --reader communicator --cursor communicator"),
    ):
        assert communicator_calls.deny_reason(own) is None
    assert communicator_calls.deny_reason(call(SLACK + "slack_pickup", channel_id="D123", ts="1.2")) == (
        "Refused on purpose: cc-slack puts the eyes and Slack's working line on his message itself the moment it lands, so the"
        " communicator never calls slack_pickup. Go on with the ack."
    )
    for post in ("slack_reply", "slack_dm_status", "slack_send", "slack_edit"):
        output = json.loads(hook_output(monkeypatch, capsys, call(SLACK + post, channel_id="D123", thread_ts="1.2", text="On it: the fix ships next.")))
        assert output["hookSpecificOutput"]["permissionDecisionReason"].startswith(f"Refused on purpose: {SLACK}{post} holds the post until")
        assert "say(thread_ts=<his thread>, text=<the ack>)" in output["hookSpecificOutput"]["permissionDecisionReason"]
        assert output["judged"] is False
    assert communicator_calls.deny_reason({**call(SLACK + "slack_thread"), "agent_id": "a-1"}) is None
    assert hook_output(monkeypatch, capsys, {**call(SLACK + "slack_thread"), "session_id": "someone-else"}) == ""


def test_the_coordinator_sends_no_pr_event_to_a_builder_that_returned_and_starts_a_fresh_one(
    demo: str, builder: Callable[..., None], tmp_path: Path
) -> None:
    builder(agent="a-7", state="built")
    reason = communicator_calls.deny_reason(call("SendMessage", transcript(tmp_path), to="a-7", message="CI went red, fix it"))
    assert reason.startswith("Refused on purpose: j1's builder already returned (built)")
    assert "`drive job add builder`" in reason
    assert "`Handoff:` line" in reason
    assert "which takes each PR j1 opened and stops it" in reason
    fresh = "Handoff: red check on https://github.com/Forge-AI/monorepo/pull/4244 Drive: demo, order o1, job j2"
    assert communicator_calls.deny_reason(call("Agent", subagent_type="long-running:builder", prompt=fresh)) is None
    builder(state="waiting")
    assert communicator_calls.deny_reason(call("SendMessage", transcript(tmp_path), to="a-7", message="He picked Approve")) is None
    builder(state="done")
    with state.edit(demo) as data:
        data["orders"]["o1"]["incident"] = True
    assert communicator_calls.deny_reason(call("SendMessage", transcript(tmp_path), to="a-7", message="CI went red, fix it")) is None


def test_the_coordinator_messages_a_running_job_only_after_reading_its_transcript(
    demo: str, builder: Callable[..., None], tmp_path: Path
) -> None:
    builder(where="remote", dispatch="ctx_9", agent=None, state="running")
    resume = 'drive job resume j1 "status?"'
    reason = communicator_calls.deny_reason(call("Bash", transcript(tmp_path), command=resume))
    assert reason.startswith("A message that tells j1 it stopped, stalled, went quiet or is stuck, or asks it whether it is, needs a read of its transcript first")
    assert "`drive job replies j1`" in reason
    assert communicator_calls.deny_reason(call("Bash", transcript(tmp_path), command=f"drive job replies j1 --limit 20 && {resume}")) is None
    assert communicator_calls.deny_reason(call("Bash", transcript(tmp_path), command=f"{resume} && drive job replies j1")) is not None
    assert communicator_calls.deny_reason(call("Bash", transcript(tmp_path, "drive job replies j1"), command=resume)) is None
    assert communicator_calls.deny_reason(call("Bash", transcript(tmp_path, "drive job replies j12"), command=resume)) is not None
    worker_read = transcript(tmp_path, "orca orchestration worker-read --dispatch ctx_9 --limit 50 --full --json")
    assert communicator_calls.deny_reason(call("Bash", worker_read, command=resume)) is None
    builder(where="local", dispatch=None, agent="a-7")
    reason = communicator_calls.deny_reason(call("SendMessage", transcript(tmp_path), to="a-7", message="status?"))
    assert "`cc-transcript` on agent a-7's transcript" in reason
    read = transcript(tmp_path, "cc-transcript show ~/.claude/projects/x/s/subagents/agent-a-7.jsonl --tail 40")
    assert communicator_calls.deny_reason(call("SendMessage", read, to="a-7", message="status?")) is None
    assert communicator_calls.deny_reason(call("Bash", transcript(tmp_path), command="drive status o1")) is None


def test_an_order_to_a_running_job_goes_unread_and_only_a_claim_or_ask_about_its_state_waits_for_a_read(
    demo: str, builder: Callable[..., None], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    builder(where="local", dispatch=None, agent="a-7", state="running")
    for order in (
        "STOP landing #4244 now: do not enqueue it, his card on it is still open",
        "Do not land #4244: it is not in the merge queue now, so wait for his tap",
        "He picked Approve",
        "If you are stuck on the rebase, return ASK with the conflict",
    ):
        assert communicator_calls.deny_reason(call("SendMessage", transcript(tmp_path), to="a-7", message=order)) is None
    builder(where="remote", dispatch="ctx_9", agent=None)
    relayed = 'drive job resume j1 "Your ASK was not sent, it is out of shape: it asks him for the proper fix of a bug hurting someone now"'
    assert communicator_calls.deny_reason(call("Bash", transcript(tmp_path), command=relayed)) is None
    for claim in ("You went quiet 40 minutes ago: are you stuck?", "No word from you since the push", "Any update?"):
        assert communicator_calls.deny_reason(call("Bash", transcript(tmp_path), command=f'drive job resume j1 "{claim}"')) is not None
    output = json.loads(hook_output(monkeypatch, capsys, call("Bash", transcript(tmp_path), command='drive job resume j1 "you have been silent"')))
    assert "(`you have been silent`)" in output["hookSpecificOutput"]["permissionDecisionReason"]
    assert output["rule"] == communicator_calls.STATE_RULE


def test_a_question_about_another_job_or_another_resumes_words_is_not_about_the_job_it_goes_to(
    demo: str, run: Run, builder: Callable[..., None], tmp_path: Path
) -> None:
    builder(where="local", dispatch=None, agent="a-7", state="running")
    for other in (
        "Find who pushed c14e327 (the PR's own builder j3812 still running?) and whether the handoff should go to the PR's live builder instead of a new one.",
        "Is j3812 still running?",
        "Is CI still running?",
    ):
        assert communicator_calls.deny_reason(call("SendMessage", transcript(tmp_path), to="a-7", message=other)) is None, other
    for own in ("j3812 merged #12. Still running?", "What's your status?"):
        assert communicator_calls.deny_reason(call("SendMessage", transcript(tmp_path), to="a-7", message=own)) is not None, own
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "build the rest")
    run("job", "add", "builder", "--order", "o2", "--goal", "build the rest")
    with state.edit(demo) as data:
        for job_id in ("j1", "j2"):
            data["jobs"][job_id].update(where="remote", dispatch=f"ctx_{job_id}", agent=None, state="running")
    both = 'drive job resume j1 "Rebase it on dev"; drive job resume j2 "You stopped without a result line"'
    assert communicator_calls.deny_reason(call("Bash", transcript(tmp_path), command=both)).startswith("A message that tells j2 ")
    assert communicator_calls.deny_reason(call("Bash", transcript(tmp_path), command=f"drive job replies j2 && {both}")) is None
    assert communicator_calls.deny_reason(call("Bash", transcript(tmp_path), command='drive job resume j1 "Rebase it on dev"; drive job resume j1 "Are you stuck?"')).startswith("A message that tells j1 ")


def test_the_coordinators_message_quoting_when_past_work_happened_goes_out_since_it_never_reaches_him(
    demo: str, builder: Callable[..., None], tmp_path: Path
) -> None:
    builder(where="local", dispatch=None, agent="a-7", state="running")
    for message in (
        "Your fix job started at 10:55 PM; rebase it on dev",
        "GitHub approved it at 3:45:58 PM and its checks went green at 3:47:52 PM; record 680813 went out at 4:07 PM",
        "Rebase it on dev; return by 11:30 PM",
    ):
        assert communicator_calls.deny_reason(call("SendMessage", transcript(tmp_path), to="a-7", message=message)) is None, message


def test_the_coordinator_wakes_no_builder_here_whose_turn_ended_while_eight_hold_this_macs_builder_slots(
    demo: str, run: Run, builder: Callable[..., None], tmp_path: Path
) -> None:
    builder(agent="a-7", state="waiting")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "build the parts", "--parts", *(f"part {number}" for number in range(1, 9)))
    for number in range(1, 9):
        run("job", "add", "builder", "--order", "o2", "--part", f"part {number}", "--goal", f"build part {number}", "--agent", f"b{number}")
    reason = communicator_calls.deny_reason(call("SendMessage", transcript(tmp_path), to="a-7", message="He picked Approve"))
    assert reason.startswith(
        "Refused on purpose: j1's agent on this Mac takes no message now: its turn ended and 8 builders already run on this Mac, its cap of 8"
        " (j2 on o2, its agent b1 last wrote at "
    )
    assert reason.endswith(
        'so this message would start it again in a slot it has no turn for. `drive job resume j1 "<the message>"` hands it to a lane on a Sprite'
        " with the message in its handoff, or, when its brief declares a step only this Mac runs on its Mac: line, which drive job brief --mac"
        " writes, or its Release: line, refuses until a slot opens; when its work holds no checkout or compiler, such as a dry run or a read,"
        " `drive job set j1 --builds-nothing` marks it to take the message here at once"
    )
    assert agents.ruled(reason) == {"judged": False}
    run("job", "set", "j1", "--builds-nothing")
    assert communicator_calls.deny_reason(call("SendMessage", transcript(tmp_path), to="a-7", message="He picked Approve")) is None
    run("job", "set", "j1", "--no-builds-nothing")
    assert communicator_calls.deny_reason(call("SendMessage", transcript(tmp_path), to="a-7", message="He picked Approve")) == reason
    builder(state="running")
    assert communicator_calls.deny_reason(call("SendMessage", transcript(tmp_path), to="a-7", message="He picked Approve")) is None
    builder(state="waiting")
    run("job", "set", "j2", "--state", "stopped")
    assert communicator_calls.deny_reason(call("SendMessage", transcript(tmp_path), to="a-7", message="He picked Approve")) is None
