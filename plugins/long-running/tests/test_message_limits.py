import io
import json
import os
from collections.abc import Callable
from pathlib import Path

import message_limits
import pytest
from conftest import Thread
from drive import slack

Run = Callable[..., tuple[int, str]]
READ_THREAD = slack.thread
LAUNCHER = slack.launcher
DM = "D123"
HANDLER = "a1"
HIS_WORDS = "Message (his words): why does the browser guess at the page map? explain the layout in full"
WHOLE = "pass as asked each message of his it answers, word for word, one per line."
NOT_HIS = "Copy each message of his this post answers word for word, one per line, with … where you skip words."

BAD_STATE_THREAD = "1794292846.190589"
LOOK_INTO = "Also look into how it got into this bad state "
FIGURE_OUT = "Get the release out but also figure out how it got into a bad state "
HOW_IT_GOT_THERE = (
    "*How it got into this state: the cause is not found, but the chain that kept it hidden is.*\n\n*Observed:* Acme's"
    " database was backed up to Acme's bucket at 1:23 AM Nov 9, and its folder there was empty by 3:15 AM Nov 10. Two"
    " more Acme databases have empty folders. Our own cleanup always keeps the newest backup, so it does not explain an"
    " empty folder.\n\n*Not known:* who removed the files. A rule on Acme's bucket or a direct delete is what is left;"
    " the logs that would tell them apart need Acme's bucket-owner access.\n\n*Why nobody noticed:* a sleeping"
    " database is trusted as backed up at server start, with no look at the bucket.\n\n*Why the rollout stayed aborted:*"
    " the release refuses to switch while an old abort exists, and nothing clears it; the retry at 3:31 AM did.\n\n*Now:*"
    " the new replica of <https://ci.example.com/builds/14185|build #14185> is still not ready 18 minutes in,"
    " held by Acme's registry database, which is behind. A fix is on it; the rollout gives up at about 3:51 AM unless"
    " it catches up. Next line 3:55 AM."
)
SWITCH_LINE = (
    "*The release switch failed again at 3:51 AM; production still serves from the old version.* Acme's registry"
    " database has uploaded no LTX files since Nov 9 5:09 PM, so the new SandSQL replica never became ready. Its"
    " snapshot is being rewritten, then the switch retries. Next line by 4:10 AM.\n\n*How it got into this state:* the"
    " LTX files under three Acme databases' bucket prefixes were removed after upload; our epoch cleanup keeps the"
    " newest populated epoch, so it was not that. Not known yet: who removed them, which Acme's bucket audit log"
    " would show; why only Acme, since Globex and Initech use the same kind of bucket; and why the registry's local LTX"
    " file 1 is gone. Next, a code fix so one stuck database cannot hold up the replica's readiness."
)
NOT_KNOWN_YET = (
    "*How it got into this state.* Acme's LTX files were uploaded to its bucket prefix at 1:23 AM Nov 9, and the"
    " prefix was empty by 3:15 AM Nov 10, like two more Acme prefixes. Our epoch cleanup keeps the newest populated"
    " epoch, so it did not remove them.\n\nNot known yet, each with what would show it: who removed them, which"
    " Acme's bucket audit log shows and which needs their bucket-owner access; why only Acme, which their bucket"
    " rules beside Globex's would show; and why the registry's local LTX file 1 is gone, which that pod's disk history"
    " would show.\n\nThe registry's uploader has failed since 5:09 PM Nov 9. A code fix so one stuck database cannot"
    " block the replica's readiness is being built."
)

DB_THREAD = "1794310240.807179"
WHY_TWO_PLACES = "Why would each db dats live in two place? Which db? wtf are you talking about?"
STILL_NO_SENSE = "And the db one still makes no sense "
WHICH_DB = (
    "*Your data is not stored twice, and the card's \"two places\" was bad wording.* It never should have been asked:"
    " your Nov 5 1:25 AM answer already settled it, and that card is withdrawn.\n\n*Which databases:* every database a"
    " cloud SandSQL server keeps. That is each chat's own database and each project environment's tables, on the"
    " SandSQL servers in the main production cluster and in each customer cluster.\n\n*What is stored where:* each"
    " server keeps the live database on its own disk and a backup in the cluster's storage bucket, as it always"
    " has.\n\n*What \"two places\" meant:* today the backup gets a new numbered folder each time a database moves to a"
    " new server or disk, and a cleanup job deletes the old folders later. The card meant those old numbered folders"
    " sitting next to the current one.\n\n*The change:* every backup goes to one fixed folder with no number.\n\n*How"
    " existing databases move:* you decided this on Nov 5 at 1:25 AM: \"no copy job, it just always falls back to the"
    " old one, and make sure in new tree all writeers are using the one with no generation\".\n\nSo nothing is copied."
    " A restore reads the fixed folder and falls back to the newest old folder when the fixed one is empty. Each server"
    " fills the fixed folder by writing a full fresh backup of its own database.\n\n*What went wrong:* the open change"
    " copies old backups into the new folder each time a server restarts. That contradicts your Nov 5 words and came"
    " from a misreading on our side.\n\nIt is being removed, and the changes come back for your tap once rebuilt. No"
    " new card follows."
)

AUDIT_THREAD = "1794206636.946169"
CHECK_SIMPLE = "Check if we've done any other complex changes when a simple change like this would have sufficed "
AUDIT = (
    "*Audit done: 3 over-built changes are being removed, 1 simplified.* I checked about 320 items for complex changes"
    " where a simple one would do.\n\n*Browser tier (open PRs):* one builder is removing the per-team anti-bot"
    " permission, the per-browser-type warm counts (only normal browsers stay warm), and a second saved list of"
    " browser types in every deploy file.\n\n*Drive tooling (merged):* a follow-up is starting to go back to one"
    " mailbox per drive instead of one per worker.\n\n*Left as is:* two exception paths for the 60-word limit. One"
    " 150-word limit would let routine messages run longer.\n\nYour two card picks since then leave nothing to build."
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


HARDWARE_THREAD = "1794292799.809419"
WHATS_WRONG = "What’s wrong with how it is now?"
CONNING = "Ok so it should be something conning user and hardware id right?"
HARDWARE_ANSWER = """*Yes: user plus the Mac's hardware ID, and that settles the card.* Each chat will record the hardware ID of the Mac that created it, once, next to the user it already records.

A chat opens writable only when both match; otherwise it opens read-only with fork. The builder is building this now.

*What is wrong today:* copy a home folder to a second Mac (Migration Assistant, a Time Machine restore, a copied folder) and open a chat there as the same user. The user matches, so it opens writable.

The chat's only claim is a number that names one app launch, not a Mac, and each Mac checks only its own copy. So both Macs write and upload under the same chat with the same transaction ids.

The later upload replaces the earlier, and the web or a later restore can read pages from two chats that went different ways. No other path does this: cloud pods restore only their own agent chats, and a Mac without a chat's file opens it read-only.

*Limits:*
- A chat already copied to two Macs before the update stays writable on both.
- A Mac with a replaced logic board gets a new hardware ID, so it reads its old chats read-only and continues them by fork.
- A chat made before the update has no hardware ID and is claimed by the Mac holding its file on first writable open."""

REWORDINGS = [
    (
        "1794308165.132599",
        ("*Table size was not the cause.* The new replica restored its stored copy, which ends at write 22, but its readiness"
        " check needs write 23, so it never passed and logged no error.\n\nThe largest database (13 GB) caught up in about two"
        " minutes, so splitting large tables would not have changed tonight. Production still serves from the old replica."
        " The fix is being written; next line by 3:25 AM."),
        ("*Table size was not the cause.* The new replica restored the snapshot from the bucket, which ends at write 22, but"
        " its readiness check needs write 23, so it never passed and logged no error.\n\nThe largest database (13 GB) caught"
        " up in about two minutes, so splitting large tables would not have changed tonight. Production still serves from"
        " the old replica. The fix is being written; next line by 3:25 AM."),
    ),
    (
        "1794309557.014979",
        ("*Acme's stuck database is repaired, 3:15 AM.* Its 3 missing history files are back in its bucket, which was empty"
        " before.\n\nA replica rebuilt from them matches the primary byte for byte. A second Acme database also has an empty"
        " history folder.\n\nThe release job is finishing the switch, then runs the full release. Next line by 3:40 AM."),
        ("*Acme's stuck database is repaired, 3:15 AM.* Its 3 missing LTX files are back in its bucket, which was empty"
        " before.\n\nA replica rebuilt from them matches the primary byte for byte. A second Acme database also has no LTX"
        " files in its bucket.\n\nThe release job is finishing the switch, then runs the full release. Next line by 3:40 AM."),
    ),
    (
        "1794310226.425819",
        ("*Two causes are being traced.* Acme's history files were uploaded at 1:23 AM Nov 9 and gone by 3:15 AM Nov 10; the"
        " bucket's delete log is refused to us, so who removed them is open. The aborted rollout blocked the resume because"
        " the release tool refuses to retry it. Next line by 3:50 AM."),
        ("*Two causes are being traced.* Acme's snapshot was uploaded at 1:23 AM Nov 9 and its LTX files in the bucket were"
        " gone by 3:15 AM Nov 10; the bucket's delete log is refused to us, so who removed them is open. The aborted rollout"
        " blocked the resume because the release tool refuses to retry it. Next line by 3:50 AM."),
    ),
]


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


@pytest.fixture
def briefed(transcript: Path) -> Path:
    lane = transcript.with_suffix("") / "subagents" / f"agent-{HANDLER}.jsonl"
    lane.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "Record: 658738"}}) + "\n")
    return transcript


def call(tool: str, transcript: Path, session: str | None = None, **tool_input: object) -> dict:
    return {
        "session_id": session or os.environ["CLAUDE_CODE_SESSION_ID"],
        "transcript_path": str(transcript),
        "agent_id": HANDLER,
        "agent_type": "long-running:handler",
        "tool_name": f"mcp__plugin_cc-slack_cc-slack__{tool}",
        "tool_input": tool_input,
    }


def posted(transcript: Path, **tool_input: object) -> str | None:
    return message_limits.deny_reason(call("slack_dm_status", transcript, **tool_input))


def his(slack_thread: Thread, *messages: str) -> None:
    slack_thread.messages += [{"ts": f"1794310000.{index:06d}", "user": "U1", "text": text} for index, text in enumerate(messages)]
    slack_thread.messages.append({"ts": "1794319999.000000", "from_bot": True, "text": "Got it."})


def test_a_line_in_his_thread_over_60_words_is_refused_with_its_count_limit_and_what_would_pass(talker: str, transcript: Path) -> None:
    reason = posted(transcript, text=said(61), thread_ts="1.2")
    assert reason.startswith("refused: this post is 61 words, and a line in his thread is at most 60 words")
    assert "way shorter" in reason
    assert "It names no message of his as asked, so it is not an answer and keeps that limit." in reason
    assert reason.endswith(WHOLE)
    assert posted(transcript, text=said(60), thread_ts="1.2") is None


def test_link_labels_count_as_the_words_he_reads(talker: str, transcript: Path) -> None:
    text = said(57) + " <https://github.com/yasyf/cc-skills/pull/1|the run page shows its history>"
    assert "63 words" in posted(transcript, text=text)


def test_a_summary_of_several_features_runs_to_120_words(talker: str, transcript: Path) -> None:
    summary = f"*Run page: opens in 3 requests*\n{said(50)}\n*Cards: shorter*\n{said(60)}"
    assert posted(transcript, text=summary) is None
    assert "is 128 words, and a summary of 2 features is at most 120 words" in posted(transcript, text=summary + " " + said(10))


def test_blocks_count_as_much_as_the_text(talker: str, transcript: Path) -> None:
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": said(70)}}]
    assert "70 words" in posted(transcript, text="short", blocks=blocks)


def test_a_question_card_holds_150_words_and_options_of_25_whatever_it_quotes(talker: str, transcript: Path) -> None:
    card = {"title": said(10), "body": said(40), "options": [said(25), said(25), said(25)], "waits": "demo:j1-7"}
    assert posted(transcript, card=card) is None
    long_option = posted(transcript, card=card | {"options": [said(26), "No"]})
    assert "each option is one line of at most 25 words" in long_option
    assert "is 26 words" in long_option
    too_long = posted(transcript, card=card, text=said(26), asked="explain the layout in full")
    assert too_long == (
        "refused: this card is 151 words, and a question with its options is at most 150 words in all (the coordinator's"
        ' reading of his Nov 9 9:33 PM words "your messages need to be way shorter rewrite this for a human").'
    )


def test_the_answer_to_how_it_got_into_a_bad_state_goes_out_whole(talker: str, briefed: Path, slack_thread: Thread) -> None:
    his(slack_thread, LOOK_INTO, FIGURE_OUT)
    assert posted(briefed, text=HOW_IT_GOT_THERE, thread_ts=BAD_STATE_THREAD).startswith(
        "refused: this post is 185 words, and a line in his thread is at most 60 words"
    )
    assert posted(briefed, text=HOW_IT_GOT_THERE, thread_ts=BAD_STATE_THREAD, asked=FIGURE_OUT.strip()) is None
    assert slack_thread.reads == [("--channel", DM, "--thread", BAD_STATE_THREAD, "--limit", "1000")]


@pytest.mark.parametrize(
    "asked",
    [
        f"{WHY_TWO_PLACES} / {STILL_NO_SENSE.strip()}",
        f'"{WHY_TWO_PLACES}"\n"{STILL_NO_SENSE.strip()}"',
        [WHY_TWO_PLACES, STILL_NO_SENSE],
    ],
)
def test_an_answer_to_two_of_his_messages_names_both_as_asked(
    talker: str, briefed: Path, slack_thread: Thread, asked: str | list[str]
) -> None:
    his(slack_thread, WHY_TWO_PLACES, STILL_NO_SENSE)
    assert posted(briefed, text=WHICH_DB, thread_ts=DB_THREAD, asked=asked) is None


def test_the_answer_to_whats_wrong_goes_out_whole_though_one_of_its_two_messages_is_in_another_thread(
    talker: str, briefed: Path, slack_thread: Thread
) -> None:
    his(slack_thread, CONNING)
    assert posted(briefed, text=HARDWARE_ANSWER, thread_ts=HARDWARE_THREAD).startswith(
        "refused: this post is 236 words, and a line in his thread is at most 60 words"
    )
    assert posted(briefed, text=HARDWARE_ANSWER, thread_ts=HARDWARE_THREAD, asked=f"{WHATS_WRONG} / {CONNING}") is None


def test_his_curly_apostrophe_matches_a_straight_one_in_asked(talker: str, briefed: Path, slack_thread: Thread) -> None:
    his(slack_thread, WHATS_WRONG)
    assert posted(briefed, text=HARDWARE_ANSWER, thread_ts=HARDWARE_THREAD, asked="What's wrong with how it is now?") is None


@pytest.mark.parametrize(("ts", "before", "after"), REWORDINGS)
def test_rewording_a_posted_line_is_held_to_that_lines_own_length(
    talker: str, transcript: Path, slack_thread: Thread, ts: str, before: str, after: str
) -> None:
    slack_thread.messages.append({"ts": ts, "from_claude": True, "from_bot": True, "text": before})
    edit = call("slack_edit", transcript, channel_id=DM, ts=ts, thread_ts=BAD_STATE_THREAD, text=after)
    assert message_limits.deny_reason(edit) is None


def test_an_edit_that_grows_its_post_past_a_rewording_is_refused_naming_both_limits(talker: str, transcript: Path, slack_thread: Thread) -> None:
    slack_thread.messages.append({"ts": "1.3", "from_claude": True, "from_bot": True, "text": said(55)})
    assert message_limits.deny_reason(call("slack_edit", transcript, channel_id=DM, ts="1.3", thread_ts="1.2", text=said(65))) is None
    reason = message_limits.deny_reason(call("slack_edit", transcript, channel_id=DM, ts="1.3", thread_ts="1.2", text=said(66)))
    assert reason.startswith(
        "refused: this post is 66 words, and a line in his thread is at most 60 words, or 10 past the 55 words of the post it edits"
    )
    assert reason.endswith(WHOLE)


def test_an_edit_whose_post_cannot_be_read_keeps_the_line_limit_and_says_why(talker: str, transcript: Path) -> None:
    reason = message_limits.deny_reason(call("slack_edit", transcript, channel_id=DM, ts="1.3", thread_ts="1.2", text=said(61)))
    assert reason.startswith("refused: this post is 61 words, and a line in his thread is at most 60 words (")
    assert "The post it edits could not be read to check its length: no message 1.3 in the thread at --channel D123 --thread 1.2." in reason


def test_the_answer_to_his_audit_order_goes_out_whole_and_its_refusal_says_how(talker: str, briefed: Path, slack_thread: Thread) -> None:
    his(slack_thread, CHECK_SIMPLE)
    reason = posted(briefed, text=AUDIT, thread_ts=AUDIT_THREAD)
    assert reason.startswith("refused: this post is 106 words, and a line in his thread is at most 60 words")
    assert reason.endswith(WHOLE)
    assert posted(briefed, text=AUDIT, thread_ts=AUDIT_THREAD, asked=CHECK_SIMPLE) is None


@pytest.mark.parametrize(("text", "asked"), [(SWITCH_LINE, FIGURE_OUT), (NOT_KNOWN_YET, LOOK_INTO)])
def test_a_line_that_also_answers_his_question_carries_what_is_not_known_yet(
    talker: str, briefed: Path, slack_thread: Thread, text: str, asked: str
) -> None:
    his(slack_thread, LOOK_INTO, FIGURE_OUT)
    assert posted(briefed, text=text, thread_ts=BAD_STATE_THREAD).startswith("refused: this post is")
    assert posted(briefed, text=text, thread_ts=BAD_STATE_THREAD, asked=asked) is None


def test_the_installed_confirmation_quoting_his_ruling_goes_out_whole(talker: str, briefed: Path, slack_thread: Thread) -> None:
    his(slack_thread, HIS_RULING)
    assert posted(briefed, text=CONFIRMATION + "\n" + said(40), thread_ts="1.2", asked=HIS_RULING) is None


def test_his_words_in_the_brief_lift_the_limit_with_no_thread_read(talker: str, transcript: Path, slack_thread: Thread) -> None:
    assert posted(transcript, text=said(400), thread_ts="1.2", asked="Explain the layout in   full") is None
    assert slack_thread.reads == []


@pytest.mark.parametrize(
    ("asked", "named"),
    [
        ("explain everything about the snapshot", '"explain everything about the snapshot"'),
        (["the bucket history", "and the snapshot…"], '"the bucket history", "and the snapshot"'),
    ],
)
def test_words_he_did_not_write_lift_nothing_and_the_refusal_names_them(
    talker: str, transcript: Path, asked: str | list[str], named: str
) -> None:
    reason = posted(transcript, text=said(400), thread_ts="1.2", asked=asked)
    assert reason.startswith("refused: this post is 400 words, and a line in his thread is at most 60 words")
    assert "It is not an answer, so it keeps that limit" in reason
    assert f"nor in your brief: {named}. {NOT_HIS}" in reason


def test_words_only_the_bot_wrote_in_the_thread_lift_nothing(talker: str, briefed: Path, slack_thread: Thread) -> None:
    slack_thread.messages.append({"ts": "1.3", "from_claude": True, "from_bot": True, "text": CHECK_SIMPLE})
    reason = posted(briefed, text=AUDIT, thread_ts=AUDIT_THREAD, asked=CHECK_SIMPLE)
    assert reason.startswith("refused: this post is 106 words, and a line in his thread is at most 60 words")
    assert reason.endswith(NOT_HIS)


def test_an_unreadable_thread_refuses_with_why(talker: str, briefed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def unreadable(*where: str) -> list[dict]:
        raise slack.SlackError("daemon not running")

    monkeypatch.setattr(slack, "thread", unreadable)
    assert posted(briefed, text=said(100), thread_ts="1.2", asked=CHECK_SIMPLE).endswith(
        "His thread could not be read to check asked, so it is not yet an answer: daemon not running."
    )


def installed_cc_slack(config: Path, messages: list[dict]) -> None:
    install = config / "plugins" / "cache" / "cc-slack" / "cc-slack" / "0.19.62"
    (install / "bin").mkdir(parents=True)
    (install / "thread.json").write_text(json.dumps({"messages": messages}))
    launcher = install / "bin" / "cc-slack"
    launcher.write_text(f"#!/bin/sh\nexec /bin/cat '{install / 'thread.json'}'\n")
    launcher.chmod(0o755)
    entry = {"installPath": str(install), "version": "0.19.62", "lastUpdated": "2026-11-10T12:00:00Z"}
    (config / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {"cc-slack@cc-slack": [entry]}}))


def test_the_hook_reads_his_thread_through_the_cc_slack_plugins_launcher_with_an_empty_path(
    talker: str, briefed: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config"
    installed_cc_slack(config, [{"ts": "1794310000.000000", "user": "U1", "text": CHECK_SIMPLE}])
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(slack, "thread", READ_THREAD)
    monkeypatch.setattr(slack, "launcher", LAUNCHER)
    assert posted(briefed, text=AUDIT, thread_ts=AUDIT_THREAD, asked=CHECK_SIMPLE) is None


def test_no_cc_slack_plugin_refuses_saying_his_thread_could_not_be_read(
    talker: str, briefed: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config"
    (config / "plugins").mkdir(parents=True)
    (config / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {}}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.setattr(slack, "thread", READ_THREAD)
    monkeypatch.setattr(slack, "launcher", LAUNCHER)
    assert posted(briefed, text=AUDIT, thread_ts=AUDIT_THREAD, asked=CHECK_SIMPLE).endswith(
        "His thread could not be read to check asked, so it is not yet an answer: the cc-slack@cc-slack plugin is not installed."
    )


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
    assert posted(transcript, text=said(300)) is None
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
