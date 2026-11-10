import io
import json
import os
from collections.abc import Callable
from pathlib import Path

import message_limits
import pytest
from drive import slack

Run = Callable[..., tuple[int, str]]
DM = "D123"
HANDLER = "a1"
HIS_WORDS = "Message (his words): why does the browser guess at the page map? explain the layout in full"
HIS_QUESTION = (
    "What the runner should save: it says I answered it but I don't. I think we want one row per program line, explain"
    " how that's different than the step logs form before (it should be different and better), and what the pros/cons"
    " are of that vs deriving it live from the journal (and speed / bandwidth implications of both)"
)
QUOTED = (
    "explain how that's different than the step logs form before… and what the pros/cons are… (and speed / bandwidth"
    " implications of both)"
)
HIS_RULING = (
    "2, it can proactively build the recommended but can’t put up a PR until I answer design Qs, and make that state a"
    " first class thing in our ledger"
)
CONFIRMATION = """*Installed: long-running 1.0.76.* The skill update for your 1:26 AM answer is merged and live. The rules now in the skill:
1. A builder may build the recommended option before you answer a design card, on a pushed branch, and opens no pull request until you answer.
2. The drive's list has a state for that work, "Built, waiting on your design answers", on your status tab and the dashboard.
3. A pull request that waits on a card of yours gets no approve card and stays out of your tap queue.
4. A pull request or a built branch can wait on several cards and is released only when the last is answered.
5. Each question card shows how many finished pull requests it holds up, and your queue lists them most first."""


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
    assert reason.endswith('installed naming the plugin and the version now installed, such as "long-running 1.0.76", which the post states.')
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
def test_his_words_asking_for_more_raise_the_limit_to_a_cards_150(talker: str, transcript: Path, asked: str) -> None:
    assert message_limits.deny_reason(call("slack_dm_status", transcript, text=said(150), asked=asked)) is None
    reason = message_limits.deny_reason(call("slack_dm_status", transcript, text=said(151), asked=asked))
    assert reason.startswith(
        "refused: this post is 151 words, and an answer to his own words asking for an explanation or a comparison is at most 150 words"
    )
    assert reason.endswith("the rest goes behind the card's Explain more or into a write-up attached as a file, never a second line.")


@pytest.mark.parametrize("asked", ["explain everything about the snapshot", "the page map"])
def test_words_he_did_not_write_or_that_ask_for_nothing_lift_nothing(talker: str, transcript: Path, asked: str) -> None:
    reason = message_limits.deny_reason(call("slack_dm_status", transcript, text=said(400), asked=asked))
    assert reason.startswith("refused: this post is 400 words, and a line in his thread is at most 60 words")
    assert reason.endswith("in the thread this post goes to or in your brief; these do not.")


def briefed_with_a_record(transcript: Path) -> Path:
    lane = transcript.with_suffix("") / "subagents" / f"agent-{HANDLER}.jsonl"
    lane.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "Record: 658274"}}) + "\n")
    return transcript


def test_his_question_in_the_thread_raises_the_limit_when_the_brief_only_names_a_record(
    talker: str, transcript: Path, slack_thread: "Thread"
) -> None:
    slack_thread.messages += [{"ts": "1.3", "user": "U1", "text": HIS_QUESTION}, {"ts": "1.4", "from_bot": True, "text": "the card"}]
    briefed = briefed_with_a_record(transcript)
    assert message_limits.deny_reason(call("slack_dm_status", briefed, text=said(150), thread_ts="1.2", asked=QUOTED)) is None
    assert slack_thread.reads == [("--channel", DM, "--thread", "1.2", "--limit", "1000")]
    assert "is 151 words" in message_limits.deny_reason(call("slack_dm_status", briefed, text=said(151), thread_ts="1.2", asked=QUOTED))


def test_words_only_the_bot_wrote_in_the_thread_lift_nothing(talker: str, transcript: Path, slack_thread: "Thread") -> None:
    slack_thread.messages.append({"ts": "1.3", "from_claude": True, "from_bot": True, "text": HIS_QUESTION})
    reason = message_limits.deny_reason(call("slack_dm_status", briefed_with_a_record(transcript), text=said(100), thread_ts="1.2", asked=QUOTED))
    assert reason.startswith("refused: this post is 100 words, and a line in his thread is at most 60 words")
    assert reason.endswith("these do not.")


@pytest.fixture
def ruled(slack_thread: "Thread", tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    slack_thread.messages += [{"ts": "1.3", "user": "U1", "text": HIS_RULING}, {"ts": "1.4", "from_bot": True, "text": "Updating the skill."}]
    config = tmp_path / "config"
    (config / "plugins").mkdir(parents=True)
    entries = {
        "long-running@skills": [
            {"version": "1.0.75", "lastUpdated": "2026-11-10T09:58:00Z"},
            {"version": "1.0.76", "lastUpdated": "2026-11-10T10:16:00Z"},
        ],
        "cc-slack@forge": [{"version": "0.19.50", "lastUpdated": "2026-11-10T09:00:00Z"}],
    }
    (config / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": entries}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))


def test_the_installed_confirmation_of_his_ruling_gets_150_words(talker: str, transcript: Path, ruled: None) -> None:
    briefed = briefed_with_a_record(transcript)
    ruling = {"text": CONFIRMATION, "thread_ts": "1.2", "asked": HIS_RULING}
    confirmed = ruling | {"installed": "long-running 1.0.76"}
    assert message_limits.deny_reason(call("slack_dm_status", briefed, **ruling)).startswith(
        "refused: this post is 134 words, and a line in his thread is at most 60 words"
    )
    assert message_limits.deny_reason(call("slack_dm_status", briefed, **confirmed)) is None
    reason = message_limits.deny_reason(call("slack_dm_status", briefed, **confirmed | {"text": CONFIRMATION + " " + said(17)}))
    assert reason.startswith("refused: this post is 151 words, and the installed confirmation of a ruling of his is at most 150 words")
    assert reason.endswith("List each rule now in the installed skill in plain words, one short line each; everything else stays on disk.")


@pytest.mark.parametrize(
    ("installed", "text", "why"),
    [
        ("long-running 1.0.75", CONFIRMATION.replace("1.0.76", "1.0.75"), "but this machine has long-running at 1.0.76."),
        ("long-running 1.0.77", CONFIRMATION.replace("1.0.76", "1.0.77"), "but this machine has long-running at 1.0.76."),
        ("long-running 1.0.76", CONFIRMATION.replace("long-running 1.0.76", "the update"), "never says the version it confirms, 1.0.76."),
    ],
)
def test_a_confirmation_lifts_nothing_unless_the_version_it_states_is_installed_now(
    talker: str, transcript: Path, ruled: None, installed: str, text: str, why: str
) -> None:
    payload = call("slack_dm_status", briefed_with_a_record(transcript), text=text, thread_ts="1.2", asked=HIS_RULING, installed=installed)
    reason = message_limits.deny_reason(payload)
    assert reason.startswith("refused: this post is 134 words, and a line in his thread is at most 60 words")
    assert reason.endswith(why)


def test_an_ordinary_61_word_line_is_still_refused_with_his_ruling_as_asked(talker: str, transcript: Path, ruled: None) -> None:
    briefed = briefed_with_a_record(transcript)
    status = "Two builders are on the run page and three pull requests wait on CI. " + said(47)
    reason = message_limits.deny_reason(call("slack_dm_status", briefed, text=status, thread_ts="1.2", asked=HIS_RULING))
    assert reason.startswith("refused: this post is 61 words, and a line in his thread is at most 60 words")
    assert reason.endswith("in the thread this post goes to or in your brief; these do not.")
    plain = message_limits.deny_reason(call("slack_dm_status", briefed, text=status, thread_ts="1.2"))
    assert plain.startswith("refused: this post is 61 words, and a line in his thread is at most 60 words")


def test_installed_lifts_nothing_without_his_ruling_in_the_thread(talker: str, transcript: Path, ruled: None) -> None:
    confirmed = {"text": CONFIRMATION, "thread_ts": "1.2", "asked": "make the skill better", "installed": "long-running 1.0.76"}
    payload = call("slack_dm_status", briefed_with_a_record(transcript), **confirmed)
    assert message_limits.deny_reason(payload).endswith("in the thread this post goes to or in your brief; these do not.")


def test_an_unreadable_thread_refuses_with_why(talker: str, transcript: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def unreadable(*where: str) -> list[dict]:
        raise slack.SlackError("daemon not running")

    monkeypatch.setattr(slack, "thread", unreadable)
    reason = message_limits.deny_reason(call("slack_dm_status", briefed_with_a_record(transcript), text=said(100), thread_ts="1.2", asked=QUOTED))
    assert reason.endswith("His thread could not be read to check the asked words: daemon not running.")


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
