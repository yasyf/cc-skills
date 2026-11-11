import io
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import message_limits
import pytest
from conftest import Checked, Thread
from drive import limits, slack, state
from drive.state import DriveError

Run = Callable[..., tuple[int, str]]
READ_THREAD = slack.thread
LAUNCHER = slack.launcher
CHECK_POST = slack.check_post
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
    " a release refuses to switch while an old abort exists, and nothing clears it; the retry at 3:31 AM did.\n\n*Now:*"
    " the new replica of <https://ci.example.com/builds/14185|build #14185> is still not ready 18 minutes in,"
    " held by Acme's registry database, which is behind. A fix is on it; the rollout gives up at about 3:51 AM unless"
    " it catches up. Next line 3:55 AM."
)
SWITCH_LINE = (
    "*<https://ci.example.com/builds/14185|The release switch> failed again at 3:51 AM; production still serves from the old version.* Acme's registry"
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
        " A fix is being written; next line by 3:25 AM."),
        ("*Table size was not the cause.* The new replica restored the snapshot from the bucket, which ends at write 22, but"
        " its readiness check needs write 23, so it never passed and logged no error.\n\nThe largest database (13 GB) caught"
        " up in about two minutes, so splitting large tables would not have changed tonight. Production still serves from"
        " the old replica. A fix is being written; next line by 3:25 AM."),
    ),
    (
        "1794309557.014979",
        ("*Acme's stuck database is repaired, 3:15 AM.* Its 3 missing history files are back in its bucket, which was empty"
        " before.\n\nA replica rebuilt from them matches the primary byte for byte. A second Acme database also has an empty"
        " history folder.\n\nThe release job is finishing the switch, then runs a full release. Next line by 3:40 AM."),
        ("*Acme's stuck database is repaired, 3:15 AM.* Its 3 missing LTX files are back in its bucket, which was empty"
        " before.\n\nA replica rebuilt from them matches the primary byte for byte. A second Acme database also has no LTX"
        " files in its bucket.\n\nThe release job is finishing the switch, then runs a full release. Next line by 3:40 AM."),
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


THREADS = ("1.2", BAD_STATE_THREAD, DB_THREAD, AUDIT_THREAD, HARDWARE_THREAD)

TEAMMATES_FIX = "A teammate's fix landed at 2:12 PM today: every locked chat shows an Update sand button."
JOSHS_FIX = (
    "<https://github.com/Forge-AI/monorepo/pull/53618|Josh Lorincz's fix> landed at 2:12 PM today: every locked chat shows"
    " an Update sand button."
)
SHOW_ME = "*Show me*\nToday, the card after a download:\n```\n" + "\n".join(f"line {index} of the card" for index in range(60)) + "\n```"
SENTRY_EVENT = "https://forge-rf.sentry.io/issues/SAND-3Z/events/bc99372dd60d40dcb36c07511693ce06/"
KENTS_ASK = f"""ASK Kent's stray chats likely come from the old build writing into the new app's chat folder on his Mac; how do we stop it?
Both builds share one chat folder on his Mac. <{SENTRY_EVENT}|The old build> runs its own server there and saves each new chat before setup, which the new app lists Untitled and read-only. A check inside the new app cannot stop that server, so I recommend a separate folder. Unproven until Kent's files are read.
Show me:
<https://github.com/Forge-AI/monorepo/blob/dev/go/sand/internal/durable/agent.go#L120-L125|The old build> makes a chat file holding only a writer stamp; the chat's setup waits for a first turn that never comes.
```
func Create(ctx context.Context, s Session, writer int64) (*DurableAgent, error) {{
```
Both builds use the same folder, and the old one never sees the new app's server, so it starts its own there.
```
old:  ~/.sand/sandsql-pair/primary/data/db/<chat>.db   finds a running server through server.json
new:  ~/.sand/sandsql-pair/primary/data/db/<chat>.db   records its server in primary.json, never server.json
```
- New app moves its chats once into a folder of its own and lists unloaded chats as loading; the old build cannot reach them. (recommended)
- New app refuses chats created without setup and lists unloaded chats as loading; smaller, but the old build still writes strays into the shared folder.
- Kent first runs a read-only command listing his broken chat files, then we fix what it shows; proves the cause, costs time.
Findings:
Kent sees Untitled, unstarted chats marked state_missing that must be forked.
"""
HIS_PICK = "https://poetic-technology.slack.com/archives/D0C4HQWPLCC/p1794239768768559?thread_ts=1794220171.175389&cid=D0C4HQWPLCC"
WATCHDOG_QUESTION = 'Which "1+2 hybrid" did you pick on Nov 9: an always-on listener that never acts, or a service that also lands and installs?'
WATCHDOG_SUMMARY = (
    f"<{HIS_PICK}|Your Nov 9 pick> reads two ways, and only the watcher that saves its place is built.\n"
    "Recommended: one always-on listener that takes GitHub pushes and runs the stuck-work watchdog, and lands nothing."
)
WATCHDOG_SHOW_ME = """What each reading builds, beside what runs today.
```
                         today          listener, never acts      service that lands and installs
hears a push             60 s read      at once, from GitHub      at once, from GitHub
lands a PR               a session,     a session,                the service, only a PR
                         after your tap after your tap            you tapped Approve
```
The listener: one process per Mac, started at login, its state in the drive's file.
```
# drive listen
on push, check, review or merge from GitHub -> append one line to events.jsonl
never: gh pr merge, an install, a push, a new job
```"""
WATCHDOG_OPTIONS = [
    "An always-on listener that feeds pushes and runs the watchdog but never lands or installs: one new process; landing still needs a session.",
    "An always-on service that also lands PRs you tapped Approve and installs after merge: nothing waits for a session; it holds merge rights.",
]
WATCHDOG_ASK = (
    f"ASK {WATCHDOG_QUESTION}\n{WATCHDOG_SUMMARY}\nShow me:\n{WATCHDOG_SHOW_ME}\n- {WATCHDOG_OPTIONS[0]} (recommended)\n"
    f"- {WATCHDOG_OPTIONS[1]}\nFindings:\nOnly the watcher that saves its place is built today.\n"
)


def said(count: int) -> str:
    return " ".join(["word"] * count)


def handler(lane: Path, opening: str, *read: str) -> None:
    lane.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": opening}}) + "\n")
    reads(lane, *read)


def reads(lane: Path, *threads: str, age: timedelta = timedelta(), failed: bool = False, channel: str = DM) -> None:
    at = (datetime.now(UTC) - age).isoformat()
    with lane.open("a") as transcript:
        for thread in threads:
            call_id = f"read-{thread}-{at}"
            use = {"type": "tool_use", "id": call_id, "name": message_limits.THREAD_READ, "input": {"channel_id": channel, "thread_ts": thread, "limit": 1000}}
            result = {"type": "tool_result", "tool_use_id": call_id, "content": [{"type": "text", "text": "{}"}]} | ({"is_error": True} if failed else {})
            transcript.write(json.dumps({"type": "assistant", "timestamp": at, "message": {"role": "assistant", "content": [use]}}) + "\n")
            transcript.write(json.dumps({"type": "user", "timestamp": at, "message": {"role": "user", "content": [result]}}) + "\n")


def lane_of(transcript: Path) -> Path:
    return transcript.with_suffix("") / "subagents" / f"agent-{HANDLER}.jsonl"


@pytest.fixture
def talker(run: Run) -> str:
    run("start", "demo", "--role", "communicator", "--channel", DM)
    return os.environ["CLAUDE_CODE_SESSION_ID"]


@pytest.fixture
def transcript(tmp_path: Path) -> Path:
    parent = tmp_path / "communicator.jsonl"
    lane_of(parent).parent.mkdir(parents=True)
    handler(lane_of(parent), HIS_WORDS, *THREADS)
    return parent


@pytest.fixture
def briefed(transcript: Path) -> Path:
    handler(lane_of(transcript), "Record: 658738", *THREADS)
    return transcript


@pytest.fixture
def unread(transcript: Path) -> Path:
    handler(lane_of(transcript), "Record: 659865")
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
    assert posted(transcript, card=card, text=" ".join(["beside"] * 26)).startswith("refused: this card is 151 words")
    assert posted(transcript, card=card, text=said(40)) is None
    too_long = posted(transcript, card=card | {"title": said(36)}, asked="explain the layout in full")
    assert too_long == (
        "refused: this card is 151 words, and a question with its summary and options is at most 150 words in all; its Show"
        " me goes under a `*Show me*` line in the body, drawn whole and not counted (the coordinator's reading of his Nov 9"
        ' 9:33 PM words "your messages need to be way shorter rewrite this for a human").'
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


@pytest.mark.parametrize("text", ["$(cat)", "<see file>", "$(cat post.txt)"])
def test_a_placeholder_in_place_of_the_post_is_refused_wherever_it_goes(talker: str, transcript: Path, text: str) -> None:
    assert f"The text {text} is a placeholder" in posted(transcript, thread_ts="1.2", text=text)
    share = call("slack_reply", transcript, channel_id="C0B75AL4XEW", thread_ts="1.2", text=text)
    assert "is a placeholder" in message_limits.deny_reason(share)


def test_a_post_that_is_one_slack_link_or_mention_goes_out(talker: str, transcript: Path) -> None:
    assert posted(transcript, thread_ts="1.2", text="<https://github.com/yasyf/cc-skills/pull/1|the fix>") is None
    assert posted(transcript, thread_ts="1.2", text="<@U123>") is None


def test_a_session_that_is_no_drives_communicator_is_not_limited(run: Run, demo: str, transcript: Path) -> None:
    assert posted(transcript, text=said(300)) is None
    assert message_limits.deny_reason(call("slack_dm_status", transcript, session="elsewhere", text=said(300))) is None


def test_a_handlers_line_in_his_thread_waits_for_its_own_fresh_read_of_that_thread(talker: str, unread: Path) -> None:
    refusal = (
        "refused: your transcript shows no read of his thread 1.2 in the last 10 minutes, so this line could miss what he"
        f" said there since. Read it with slack_thread(channel_id={DM}, thread_ts=1.2, limit=1000), then post. When the"
        " tool saves a long thread to a file, the call is not a read until you open that file and read its newest messages."
    )
    assert posted(unread, text="Fixed.", thread_ts="1.2") == refusal
    reads(lane_of(unread), "1.3")
    reads(lane_of(unread), "1.2", channel="C0B75AL4XEW")
    reads(lane_of(unread), "1.2", age=timedelta(minutes=11))
    reads(lane_of(unread), "1.2", failed=True)
    assert posted(unread, text="Fixed.", thread_ts="1.2") == refusal
    reads(lane_of(unread), "1.2", age=timedelta(minutes=9))
    assert posted(unread, text="Fixed.", thread_ts="1.2") is None
    assert message_limits.deny_reason(call("slack_reply", unread, channel_id=DM, thread_ts="1.4", text="Fixed.")).startswith(
        "refused: your transcript shows no read of his thread 1.4"
    )


def test_a_card_an_edit_a_root_line_and_the_communicators_own_line_need_no_thread_read(talker: str, unread: Path) -> None:
    card = {"title": said(10), "body": said(40), "options": ["Land it", "Hold it"], "waits": "demo:j1-7"}
    assert posted(unread, card=card, thread_ts="1.2") is None
    assert message_limits.deny_reason(call("slack_edit", unread, channel_id=DM, ts="1.3", thread_ts="1.2", text="Fixed.")) is None
    assert posted(unread, text="Fixed.") is None
    payload = call("slack_dm_status", unread, text="Fixed.", thread_ts="1.2")
    del payload["agent_id"]
    assert message_limits.deny_reason(payload) is None


def test_a_handlers_post_links_each_thing_it_names_where_it_names_it(talker: str, transcript: Path) -> None:
    refusal = posted(transcript, text=TEAMMATES_FIX)
    assert refusal.startswith(
        "refused: \"teammate's fix\" names something he could open with no link there; put those words in its link,"
        " <url|teammate's fix>, so he opens it where it is named"
    )
    assert '"never mention somehting and expect me to go look it up myself"' in refusal
    assert posted(transcript, text=JOSHS_FIX) is None
    assert posted(transcript, text='He wrote "the fix is in" at noon; a fix for the rest is being written.') is None
    assert "says only a number" in posted(transcript, text="<https://github.com/Forge-AI/monorepo/pull/53618|#53618> landed.")
    assert "is a number he cannot open" in posted(transcript, text="Josh's change, #53618, landed.")
    assert posted(transcript, text=TEAMMATES_FIX, tap_prs=["https://github.com/Forge-AI/monorepo/pull/53618"]) is None
    ack = call("slack_dm_status", transcript, text=TEAMMATES_FIX)
    del ack["agent_id"]
    assert message_limits.deny_reason(ack) is None


def test_a_cards_title_and_options_name_only_what_its_summary_links(talker: str, transcript: Path) -> None:
    card = {
        "title": "A teammate's fix landed first: keep it or replace it?",
        "body": TEAMMATES_FIX,
        "options": ["Keep theirs (recommended)", "Replace it; it undoes a teammate's landed fix"],
        "waits": "demo:j1-7",
    }
    refusal = posted(transcript, card=card)
    assert "\"teammate's fix\" names something he could open with no link there" in refusal
    assert "\"A teammate's fix\" in the question or an option names something he could open" not in refusal
    assert "\"teammate's landed fix\" in the question or an option names something he could open, and those draw no links" in refusal
    assert posted(transcript, card=card | {"body": JOSHS_FIX}) is None
    assert "in the question or an option" in posted(transcript, card=card | {"body": JOSHS_FIX.replace("fix>", "change>")})


def test_a_block_drive_job_ask_keeps_posts_as_written_and_a_word_over_is_refused_by_both(talker: str, transcript: Path) -> None:
    card = limits.question(KENTS_ASK).card("demo", "demo:j585-7")
    assert posted(transcript, card=card) is None
    assert posted(transcript, card=card, text="Kent's stray chats: pick a fix").startswith("refused: this card is 155 words")
    with pytest.raises(DriveError, match="the question, summary and options are 151 words; at most 150"):
        limits.question(KENTS_ASK.replace("costs time.", "costs time and money."))
    longer = [*card["options"][:2], card["options"][2].replace("costs time.", "costs time and money.")]
    assert posted(transcript, card=card | {"options": longer}).startswith("refused: this card is 151 words")


def test_a_card_whose_kept_block_has_a_show_me_carries_every_code_block_of_it_under_its_show_me_line(talker: str, transcript: Path) -> None:
    limits.question(WATCHDOG_ASK)
    asks = state.drive_dir("demo") / "asks"
    asks.mkdir()
    (asks / "j605-7c32364e.md").write_text(WATCHDOG_ASK)
    card = {"title": WATCHDOG_QUESTION, "body": WATCHDOG_SUMMARY, "options": WATCHDOG_OPTIONS, "waits": "demo:j605-660176"}
    assert posted(transcript, card=card, text="Watchdog: which hybrid") == (
        "refused: the Show me goes in the card body under *Show me*, never only behind Explain more"
        f" ({limits.SHOW_ME_RULING}); this body carries 0 of the 2 code blocks its kept `Show me:` holds, and no `*Show me*`"
        " line: copy every line under `Show me:` there as written, fences kept."
    )
    shown = f"{WATCHDOG_SUMMARY}\n*Show me*\n{WATCHDOG_SHOW_ME}"
    assert posted(transcript, card=card | {"body": shown}) is None
    sketched = shown.split("The listener:")[0] + "```\n# drive listen\n```"
    assert "carries 1 of the 2 code blocks" in posted(transcript, card=card | {"body": sketched})
    assert posted(transcript, card=card | {"body": WATCHDOG_SUMMARY, "waits": "demo:j606-660177"}) is None


def test_a_cards_show_me_is_drawn_whole_and_counts_against_no_limit(talker: str, transcript: Path) -> None:
    card = {"title": said(10), "body": said(40), "options": [said(25), said(25), said(25)], "waits": "demo:j1-7"}
    assert posted(transcript, card=card | {"body": said(40) + "\n" + SHOW_ME}) is None
    assert "this card is 431 words" in posted(transcript, card=card | {"body": said(40) + "\n" + SHOW_ME.removeprefix("*Show me*\n")})
    assert "names something he could open" in posted(transcript, card=card | {"body": said(40) + "\n*Show me*\nThe fix, after:\n```\nx\n```"})
    assert posted(transcript, card=card | {"body": said(40) + "\n*Show me*\n```\nthe fix #41\n```"}) is None


def test_a_saved_thread_is_a_read_only_once_the_handler_opens_its_file(talker: str, unread: Path) -> None:
    lane = lane_of(unread)
    saved = "/Users/me/.claude/projects/p/s/tool-results/mcp-plugin_cc-slack_cc-slack-slack_thread-1.txt"
    at = datetime.now(UTC).isoformat()
    use = {"type": "tool_use", "id": "long", "name": message_limits.THREAD_READ, "input": {"channel_id": DM, "thread_ts": "1.2", "limit": 1000}}
    notice = (
        "Error: result (111,642 characters across 1 line) exceeds maximum allowed tokens. Output has been saved to"
        f" {saved}. Format: Plain text - For targeted searches (find a string): use grep on the file directly."
    )
    result = {"type": "tool_result", "tool_use_id": "long", "content": [{"type": "text", "text": notice}]}
    opened = {"type": "tool_use", "id": "open", "name": "Bash", "input": {"command": f"python3 -c 'print(open(\"{saved}\").read()[-80000:])'"}}
    printed = {"type": "tool_result", "tool_use_id": "open", "content": "the newest messages"}
    with lane.open("a") as transcript:
        for row in ({"type": "assistant", "message": {"content": [use]}}, {"type": "user", "message": {"content": [result]}}):
            transcript.write(json.dumps(row | {"timestamp": at}) + "\n")
    assert posted(unread, text="Fixed.", thread_ts="1.2").startswith("refused: your transcript shows no read of his thread 1.2")
    with lane.open("a") as transcript:
        for row in ({"type": "assistant", "message": {"content": [opened]}}, {"type": "user", "message": {"content": [printed]}}):
            transcript.write(json.dumps(row | {"timestamp": at}) + "\n")
    assert posted(unread, text="Fixed.", thread_ts="1.2") is None


def test_every_post_to_his_dm_passes_check_post_on_its_exact_words(talker: str, transcript: Path, checked: Checked) -> None:
    checked.findings = ["1:5 bare-number '#41': link it as <url|#N> or <url|deploy #N>"]
    reason = posted(transcript, text="See 41 now.", thread_ts="1.2")
    assert reason == (
        "refused: `cc-slack check-post` finds this in the post's exact words; fix each, then post the fixed words:\n"
        "1:5 bare-number '#41': link it as <url|#N> or <url|deploy #N>"
    )
    edit = call("slack_edit", transcript, channel_id=DM, ts="1.3", thread_ts="1.2", text="See 41 then.")
    assert message_limits.deny_reason(edit).startswith("refused: `cc-slack check-post` finds this")
    share = call("slack_reply", transcript, channel_id="C0B75AL4XEW", thread_ts="1.2", text="See #41 there.")
    assert message_limits.deny_reason(share) is None
    assert checked.texts == ["See 41 now.", "See 41 then."]
    checked.findings = []
    assert posted(transcript, text="See <https://github.com/yasyf/cc-skills/pull/41|the fix> now.", thread_ts="1.2") is None


def test_a_post_check_post_cannot_check_does_not_go_out(talker: str, transcript: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(text: str) -> list[str]:
        raise slack.SlackError("the cc-slack@cc-slack plugin is not installed")

    monkeypatch.setattr(slack, "check_post", broken)
    assert posted(transcript, text="Fixed.", thread_ts="1.2") == (
        "refused: `cc-slack check-post` could not check this post's words, so it does not go out: the cc-slack@cc-slack"
        " plugin is not installed."
    )


def test_check_post_runs_the_installed_cc_slack_on_the_exact_words(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    launcher = tmp_path / "cc-slack"
    launcher.write_text(
        f"#!/bin/sh\n[ \"$1 $2\" = 'check-post -' ] || exit 2\n/bin/cat > '{tmp_path / 'words'}'\n"
        "echo \"1:5 bare-number '#41': link it\"\nexit 1\n"
    )
    launcher.chmod(0o755)
    monkeypatch.setattr(slack, "launcher", lambda: str(launcher))
    assert CHECK_POST("See #41 now.") == ["1:5 bare-number '#41': link it"]
    assert (tmp_path / "words").read_text() == "See #41 now."
    launcher.write_text("#!/bin/sh\necho 'usage: check-post <file|->' >&2\nexit 2\n")
    with pytest.raises(slack.SlackError, match="usage: check-post"):
        CHECK_POST("See #41 now.")


def test_main_prints_a_pretooluse_denial(
    talker: str, transcript: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(call("slack_dm_status", transcript, text=said(900)))))
    message_limits.main()
    decision = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert decision["hookEventName"] == "PreToolUse"
    assert decision["permissionDecision"] == "deny"
    assert "this post is 900 words" in decision["permissionDecisionReason"]
