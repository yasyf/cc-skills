import io
import json
import os
from collections.abc import Callable
from pathlib import Path

import message_limits
import pytest

Run = Callable[..., tuple[int, str]]
DM = "D123"
HANDLER = "a1"
HIS_WORDS = "Message (his words): why does the browser guess at the page map? explain the layout in full"


def said(count: int) -> str:
    return " ".join(["word"] * count)


@pytest.fixture
def talker(run: Run) -> str:
    run("start", "demo", "--role", "communicator", "--channel", DM)
    return os.environ["CLAUDE_CODE_SESSION_ID"]


@pytest.fixture
def transcript(tmp_path: Path) -> Path:
    parent = tmp_path / "communicator.jsonl"
    lane = parent.with_suffix("") / "subagents" / f"agent-{HANDLER}.jsonl"
    lane.parent.mkdir(parents=True)
    lane.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": HIS_WORDS}}) + "\n")
    return parent


def call(tool: str, transcript: Path, session: str | None = None, **tool_input: object) -> dict:
    return {
        "session_id": session or os.environ["CLAUDE_CODE_SESSION_ID"],
        "transcript_path": str(transcript),
        "agent_id": HANDLER,
        "agent_type": "long-running:handler",
        "tool_name": f"mcp__plugin_cc-slack_cc-slack__{tool}",
        "tool_input": tool_input,
    }


def test_a_line_in_his_thread_over_60_words_is_refused_with_its_count_and_limit(talker: str, transcript: Path) -> None:
    reason = message_limits.deny_reason(call("slack_dm_status", transcript, text=said(61), thread_ts="1.2"))
    assert reason.startswith("refused: this post is 61 words, and a line in his thread is at most 60 words")
    assert "way shorter" in reason
    assert reason.endswith("pass his words, quoted, as asked.")
    assert message_limits.deny_reason(call("slack_dm_status", transcript, text=said(60), thread_ts="1.2")) is None


def test_link_labels_count_as_the_words_he_reads(talker: str, transcript: Path) -> None:
    text = said(57) + " <https://github.com/yasyf/cc-skills/pull/1|the run page shows its history>"
    assert "63 words" in message_limits.deny_reason(call("slack_dm_status", transcript, text=text))


def test_a_summary_of_several_features_runs_to_120_words(talker: str, transcript: Path) -> None:
    summary = f"*Run page: opens in 3 requests*\n{said(50)}\n*Cards: shorter*\n{said(60)}"
    assert message_limits.deny_reason(call("slack_dm_status", transcript, text=summary)) is None
    reason = message_limits.deny_reason(call("slack_dm_status", transcript, text=summary + " " + said(10)))
    assert "is 128 words, and a summary of 2 features is at most 120 words" in reason


def test_blocks_count_as_much_as_the_text(talker: str, transcript: Path) -> None:
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": said(70)}}]
    assert "70 words" in message_limits.deny_reason(call("slack_dm_status", transcript, text="short", blocks=blocks))


def test_a_question_card_holds_150_words_and_options_of_25(talker: str, transcript: Path) -> None:
    card = {"title": said(10), "body": said(40), "options": [said(25), said(25), said(25)], "waits": "demo:j1-7"}
    assert message_limits.deny_reason(call("slack_dm_status", transcript, card=card)) is None
    long_option = message_limits.deny_reason(call("slack_dm_status", transcript, card=card | {"options": [said(26), "No"]}))
    assert "each option is one line of at most 25 words" in long_option
    assert "is 26 words" in long_option
    too_long = message_limits.deny_reason(call("slack_dm_status", transcript, card=card, text=said(26)))
    assert "this card is 151 words, and a question with its options is at most 150 words in all" in too_long


@pytest.mark.parametrize("asked", ["explain the layout in full", "Why does the browser   guess at the page map"])
def test_his_words_asking_for_more_lift_the_limit(talker: str, transcript: Path, asked: str) -> None:
    assert message_limits.deny_reason(call("slack_dm_status", transcript, text=said(400), asked=asked)) is None


@pytest.mark.parametrize("asked", ["explain everything about the snapshot", "the page map"])
def test_words_he_did_not_write_or_that_ask_for_nothing_lift_nothing(talker: str, transcript: Path, asked: str) -> None:
    reason = message_limits.deny_reason(call("slack_dm_status", transcript, text=said(400), asked=asked))
    assert reason.startswith("refused: this post is 400 words")
    assert reason.endswith("in his message as your brief quotes it; these do not.")


def test_the_communicators_own_post_has_no_brief_to_quote(talker: str, transcript: Path) -> None:
    payload = call("slack_dm_status", transcript, text=said(80), asked="explain the layout in full")
    del payload["agent_id"]
    assert "80 words" in message_limits.deny_reason(payload)


def test_a_reply_counts_only_in_his_dm(talker: str, transcript: Path) -> None:
    assert "61 words" in message_limits.deny_reason(call("slack_reply", transcript, channel_id=DM, thread_ts="1.2", text=said(61)))
    permalink = f"https://acme.slack.com/archives/{DM}/p1794288799976279"
    assert "61 words" in message_limits.deny_reason(call("slack_edit", transcript, url=permalink, text=said(61)))
    share = call("slack_reply", transcript, channel_id="C0B75AL4XEW", thread_ts="1.2", text=said(300))
    assert message_limits.deny_reason(share) is None


def test_a_session_that_is_no_drives_communicator_is_not_limited(run: Run, demo: str, transcript: Path) -> None:
    assert message_limits.deny_reason(call("slack_dm_status", transcript, text=said(300))) is None
    assert message_limits.deny_reason(call("slack_dm_status", transcript, session="elsewhere", text=said(300))) is None


def test_main_prints_a_pretooluse_denial(
    talker: str, transcript: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(call("slack_dm_status", transcript, text=said(900)))))
    message_limits.main()
    decision = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert decision["hookEventName"] == "PreToolUse"
    assert decision["permissionDecision"] == "deny"
    assert "this post is 900 words" in decision["permissionDecisionReason"]
