import os

import message_limits
import pytest

REPLY = message_limits.SLACK + "slack_reply"


def call(tool: str, **tool_input: object) -> dict:
    return {"session_id": os.environ["CLAUDE_CODE_SESSION_ID"], "tool_name": tool, "tool_input": tool_input}


def denied(payload: dict) -> str | None:
    output = message_limits.hook_output(payload)
    if reason := (output.get("hookSpecificOutput") or {}).get("permissionDecisionReason"):
        assert output["judged"] is False
    return reason


def test_the_coordinator_posts_nothing_to_slack_and_sends_its_words_to_the_communicator(demo: str) -> None:
    said = call(message_limits.SAY, text="Merged.")
    assert denied(said).startswith("Refused on purpose: the coordinator posts nothing to Slack")
    assert denied(call(REPLY, channel_id="C9", thread_ts="1.2", text="x")).startswith("Refused on purpose")
    assert denied(said | {"agent_id": "a-1"}) is None
    assert message_limits.hook_output(said | {"session_id": "someone-else"}) == {}


@pytest.mark.parametrize("text", ["$(cat)", "<see file>", "$(cat post.txt)"])
def test_a_placeholder_in_place_of_the_post_is_refused_wherever_it_goes(communicator: str, text: str) -> None:
    assert f"The text {text} is a placeholder" in denied(call(REPLY, channel_id="C9", thread_ts="1.2", text=text))


@pytest.mark.parametrize("link", ["<http://localhost:8080/run|the run>", "file:///Users/me/report.md", "<|the report>"])
def test_a_link_that_opens_nowhere_from_slack_is_refused(communicator: str, link: str) -> None:
    assert "opens nowhere from Slack" in denied(call(REPLY, channel_id="C9", thread_ts="1.2", text=f"The run is up: {link}."))


def test_a_say_reference_naming_nothing_the_drive_holds_is_refused_and_any_other_post_goes_out_unchecked(communicator: str) -> None:
    assert denied(call(message_limits.SAY, text="The fix is {order:o99}.")) is not None
    assert message_limits.hook_output(call(message_limits.SAY, text="The fix merged, and the release goes out next.")) == {}


def test_a_status_card_goes_as_a_plain_line(communicator: str) -> None:
    output = message_limits.hook_output(call(message_limits.STATUS, thread_ts="1.2", card={"title": "Reading your thread"}))
    assert output["hookSpecificOutput"]["updatedInput"] == {"thread_ts": "1.2", "text": "Reading your thread"}
