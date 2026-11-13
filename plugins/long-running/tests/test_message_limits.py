import io
import json
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import message_limits
import pytest
from conftest import WRITTEN_AT, Checked, Thread, explained
from drive import agents, limits, picture, refs, slack, state, talk, view
from drive.state import DriveError

Run = Callable[..., tuple[int, str]]
READ_THREAD = slack.thread
LAUNCHER = slack.launcher
CHECK_POST = slack.check_post
DM = "D123"
EDITED = "1794288799.000003"
REPORTED = "https://acme.slack.com/archives/C0REPORTS01/p1794400100000200"
HANDLER = "a1"
HIS_WORDS = "Message (his words): why does the browser guess at the page map? explain the layout in full"
ELEVEN_FIXES = {
    "Order close": "An order closing on a post that never recorded its part is refused with the reason.",
    "PR serves": "`drive pr set --serves` checks a PR of any size.",
    "Builder cap": "The builder cap names each builder it counts.",
    "Card amounts": "A card's amount check no longer refuses amounts outside the question's subject.",
    "Older drive": "An older `drive` hands each command to the newest install.",
    "Catch-up": "Your catch-up shows each held report with the corrections that answer it.",
    "Say judge": "The say hook's judge reads only your words as yours, never a handler's.",
    "Due time": "A time a post promises sets its order's due time, even with two times in it.",
    "Brief binding": "Only a job's own agent type binds to it by reading its brief.",
    "Batch adds": "Several orders or jobs added in one call all land or none do.",
    "Status tables": "Status posts draw tables as Slack tables, and the bot reads back its canvas.",
}
WHOLE = "pass as asked each message of his it answers, word for word or by its permalink, one per line."
RELAY_BRIEF = (
    "Record: 670054 report from meta (job j1546, closes o598)\nDrive: demo\nContext: read 670054 whole. Post it in o598's thread if it"
    " has one, else at his DM root. He is away, so say may hold it for his catch-up.\nDeliverable: the line from 670054 posted, or held for his catch-up"
)
NOT_HIS = (
    "Copy each message of his this post answers word for word, one per line, with … where you skip words, or pass its permalink"
    " when it is in another thread of his DM, as a share he ordered to another person's DM does."
)
TEAMMATE = "U2"
SHARE_ORDER = f"can you summarize all our findings, the session IDs, the patterns, the fixes we are considering, etc to <@{TEAMMATE}>"
SHARE_LINK = f"https://acme.slack.com/archives/{DM}/p1794512898040859?thread_ts=1794491173.244549&cid={DM}"
SHARE = "*What happened*\n" + " ".join(["word"] * 70) + "\n*Fixes being built*\n" + " ".join(["word"] * 70)

BAD_STATE_THREAD = "1794292846.190589"
LOOK_INTO = "Also look into how it got into this bad state "
FIGURE_OUT = "Get the release out but also figure out how it got into a bad state "
HOW_IT_GOT_THERE = (
    "*How it got into this state: the cause is not found, but the chain that kept it hidden is.*\n\n*Observed:* Acme's"
    " database was backed up to Acme's bucket at `1:23 AM` Nov 9, and its folder there was empty by `3:15 AM` Nov 10. Two"
    " more Acme databases have empty folders. Our own cleanup always keeps the newest backup, so it does not explain an"
    " empty folder.\n\n*Not known:* who removed the files. A rule on Acme's bucket or a direct delete is what is left;"
    " the logs that would tell them apart need Acme's bucket-owner access.\n\n*Why nobody noticed:* a sleeping"
    " database is trusted as backed up at server start, with no look at the bucket.\n\n*Why the rollout stayed aborted:*"
    " a release refuses to switch while an old abort exists, and nothing clears it; the retry at `3:31 AM` did.\n\n*Now:*"
    " the new replica of <https://ci.example.com/builds/14185|build #14185> is still not ready 18 minutes in,"
    " held by Acme's registry database, which is behind. A fix is on it; the rollout gives up by about 3:51 AM unless"
    " it catches up. Next line 3:55 AM."
)
SWITCH_LINE = (
    "*<https://ci.example.com/builds/14185|The release switch> failed again at `3:51 AM`; production still serves from the old version.* Acme's registry"
    " database has uploaded no LTX files since Nov 9 `5:09 PM`, so the new SandSQL replica never became ready. Its"
    " snapshot is being rewritten, then the switch retries. Next line by 4:10 AM.\n\n*How it got into this state:* the"
    " LTX files under three Acme databases' bucket prefixes were removed after upload; our epoch cleanup keeps the"
    " newest populated epoch, so it was not that. Not known yet: who removed them, which Acme's bucket audit log"
    " would show; why only Acme, since Globex and Initech use the same kind of bucket; and why the registry's local LTX"
    " file 1 is gone. Next, a code fix so one stuck database cannot hold up the replica's readiness."
)
NOT_KNOWN_YET = (
    "*How it got into this state.* Acme's LTX files were uploaded to its bucket prefix at `1:23 AM` Nov 9, and the"
    " prefix was empty by `3:15 AM` Nov 10, like two more Acme prefixes. Our epoch cleanup keeps the newest populated"
    " epoch, so it did not remove them.\n\nNot known yet, each with what would show it: who removed them, which"
    " Acme's bucket audit log shows and which needs their bucket-owner access; why only Acme, which their bucket"
    " rules beside Globex's would show; and why the registry's local LTX file 1 is gone, which that pod's disk history"
    " would show.\n\nThe registry's uploader has failed since `5:09 PM` Nov 9. A code fix so one stuck database cannot"
    " block the replica's readiness is being built."
)

DB_THREAD = "1794310240.807179"
WHY_TWO_PLACES = "Why would each db dats live in two place? Which db? wtf are you talking about?"
STILL_NO_SENSE = "And the db one still makes no sense "
WHICH_DB = (
    "*Your data is not stored twice, and the card's \"two places\" was bad wording.* It never should have been asked:"
    " your Nov 5 `1:25 AM` answer already settled it, and that card is withdrawn.\n\n*Which databases:* every database a"
    " cloud SandSQL server keeps. That is each chat's own database and each project environment's tables, on the"
    " SandSQL servers in the main production cluster and in each customer cluster.\n\n*What is stored where:* each"
    " server keeps the live database on its own disk and a backup in the cluster's storage bucket, as it always"
    " has.\n\n*What \"two places\" meant:* today the backup gets a new numbered folder each time a database moves to a"
    " new server or disk, and a cleanup job deletes the old folders later. The card meant those old numbered folders"
    " sitting next to the current one.\n\n*The change:* every backup goes to one fixed folder with no number.\n\n*How"
    " existing databases move:* you decided this on Nov 5 at `1:25 AM`: \"no copy job, it just always falls back to the"
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
CONFIRMATION = """*Installed: long-running 1.0.76.* The skill update for your `1:26 AM` answer is merged and live. The rules now in the skill:
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
        " its readiness check needs write 23, so it never passed and logged no error (see {record:661300}).\n\nThe largest"
        " database (13 GB) caught up in about two minutes, so splitting large tables would not have changed tonight. Production still serves from"
        " the old replica. A fix is being written; next line by 3:25 AM."),
    ),
    (
        "1794309557.014979",
        ("*Acme's stuck database is repaired, 3:15 AM.* Its 3 missing history files are back in its bucket, which was empty"
        " before.\n\nA replica rebuilt from them matches the primary byte for byte. A second Acme database also has an empty"
        " history folder.\n\nThe release job is finishing the switch, then runs a full release. Next line by 3:40 AM."),
        ("*Acme's stuck database is repaired, `3:15 AM`.* Its 3 missing LTX files are back in its bucket, which was empty"
        " before.\n\nA replica rebuilt from them matches the primary byte for byte. A second Acme database also has no LTX"
        " files in its bucket.\n\nThe release job is finishing the switch, then runs a full release. Next line by 3:40 AM."),
    ),
    (
        "1794310226.425819",
        ("*Two causes are being traced.* Acme's history files were uploaded at 1:23 AM Nov 9 and gone by 3:15 AM Nov 10; the"
        " bucket's delete log is refused to us, so who removed them is open. The aborted rollout blocked the resume because"
        " the release tool refuses to retry it. Next line by 3:50 AM."),
        ("*Two causes are being traced.* Acme's snapshot was uploaded at `1:23 AM` Nov 9 and its LTX files in the bucket were"
        " gone by 3:15 AM Nov 10; the bucket's delete log is refused to us, so who removed them is open. The aborted rollout"
        " blocked the resume because the release tool refuses to retry it (see {record:661300}). Next line by 3:50 AM."),
    ),
]


THREADS = ("1.2", BAD_STATE_THREAD, DB_THREAD, AUDIT_THREAD, HARDWARE_THREAD)

TEAMMATES_PR = "A teammate's PR landed earlier today: every locked chat shows an Update sand button."
JOSHS_PR = (
    "<https://github.com/Forge-AI/monorepo/pull/53618|Juniper Cole's PR> landed earlier today: every locked chat shows"
    " an Update sand button."
)
SHOW_ME = "*Show me*\nToday, the card after a download:\n```\n" + "\n".join(f"line {index} of the card" for index in range(60)) + "\n```"
BREAK_GLASS = "Use break glass with machine profile then you don't need sso, I tell you this once a day"
SENTRY_EVENT = "https://forge-rf.sentry.io/issues/SAND-3Z/events/bc99372dd60d40dcb36c07511693ce06/"
STRAY_CHATS_ASK = explained(f"""ASK Rowan's stray chats likely come from the old build writing into the new app's chat folder on his Mac; how do we stop it?
Drive tooling: your pick decides how stray chats stop. Both builds share one chat folder; <{SENTRY_EVENT}|The old build> runs its own server there and saves each new chat before setup, which the new app lists Untitled and read-only. A check inside the new app cannot stop that server, so I recommend a separate folder.
Show me:
<https://github.com/Forge-AI/monorepo/blob/dev/go/sand/internal/durable/agent.go#L120-L125|The old build> makes a chat file holding only a writer stamp; the chat's setup waits for a first turn that never comes.
```go
func Create(ctx context.Context, s Session, writer int64) (*DurableAgent, error) {{
```
Both builds use the same folder, and the old one never sees the new app's server, so it starts its own there.
| Build | Chat file | Finds its server through |
| --- | --- | --- |
| old | ~/.sand/sandsql-pair/primary/data/db/<chat>.db | server.json |
| new | ~/.sand/sandsql-pair/primary/data/db/<chat>.db | primary.json, never server.json |
- New app moves its chats once into a folder of its own and lists unloaded chats as loading; the old build cannot reach them. (recommended)
- New app refuses chats created without setup and lists unloaded chats as loading; smaller, but the old build still writes strays into the shared folder.
- Rowan first runs a read-only command listing his broken chat files, then we fix what it shows; proves the cause, costs time.
Findings:
Root cause: both builds share one chat folder on his Mac, so the old build's own server writes chats there
Holds: none: no option changes code any job builds
First principles: New app moves its chats once into a folder of its own and lists unloaded chats as loading; the old build cannot reach them.
Simplest: New app moves its chats once into a folder of its own and lists unloaded chats as loading; the old build cannot reach them.
Rowan sees Untitled, unstarted chats marked state_missing that must be forked.
""")
HIS_PICK = "https://poetic-technology.slack.com/archives/D0C4HQWPLCC/p1794239768768559?thread_ts=1794220171.175389&cid=D0C4HQWPLCC"
WATCHDOG_QUESTION = 'Which "1+2 hybrid" did you pick on Nov 9: an always-on listener that never acts, or a service that also lands and installs?'
WATCHDOG_SUMMARY = (
    f"Drive tooling: your pick decides what the always-on watcher does. <{HIS_PICK}|Your Nov 9 pick> reads two ways, and only the watcher that saves its place is built.\n"
    "Recommended: one always-on listener that takes GitHub pushes and runs the stuck-work watchdog, and lands nothing."
)
WATCHDOG_SHOW_ME = """What each reading builds, beside what runs today.
| | Today | Listener, never acts | Service that lands and installs |
| --- | --- | --- | --- |
| Hears a push | 60 s read | at once, from GitHub | at once, from GitHub |
| Lands a PR | a session, after your tap | a session, after your tap | the service, only a PR you tapped Approve |

The listener: one process per Mac, started at login, its state in the drive's file.
```mermaid
flowchart LR
  github[GitHub push, check, review or merge] --> listen[drive listen] --> events[(one line per event)]
```"""
WATCHDOG_OPTIONS = [
    "An always-on listener that feeds pushes and runs the watchdog but never lands or installs: one new process; landing still needs a session.",
    "An always-on service that also lands PRs you tapped Approve and installs after merge: nothing waits for a session; it holds merge rights.",
]
WATCHDOG_ASK = explained(
    f"ASK {WATCHDOG_QUESTION}\n{WATCHDOG_SUMMARY}\nShow me:\n{WATCHDOG_SHOW_ME}\n- {WATCHDOG_OPTIONS[0]} (recommended)\n"
    f"- {WATCHDOG_OPTIONS[1]}\nFindings:\nRoot cause: none: a question about his own pick\nSimplest: An always-on listener that feeds pushes and runs the watchdog but never lands or installs\nHolds: none: no option changes code any job builds\nOnly the watcher that saves its place is built today.\n"
)
FROZEN_COPIES_ASK = explained("""ASK Before the 30 frozen copies from the Oct 13 fleet move are deleted, what must prove each moved copy is whole?
Drive tooling: your pick decides what proves a moved copy whole. Removing a frozen copy deletes its database files and its folder in the team's bucket, at once and for good. These moved on Oct 13 under older code, and one team's moved databases later lost bucket history, so "ready on Oct 13" proves nothing today. Nothing is deleted meanwhile.
Show me:
The 30 frozen copies, counted by the move's metric. Names and sizes need an in-cluster credential, so most are unread.

| Cluster | Frozen copies | Last write | Names read | Size |
| --- | --- | --- | --- | --- |
| demo-prod | 26 | Oct 13, 15:45 to 15:49Z (frozen then) | demo-a1 (bucket folder already empty), demo-b2, demo-c3 | unread |
| demo-east | 2 | Oct 13 | none | unread |
| demo-west | 1 | Oct 13 | none | unread |
| demo-north | 1 | Oct 13 | none | unread |

What one removal deletes, in order:

| Where | What | When |
| --- | --- | --- |
| source fleet disk | database file, redo and side files, replica metadata | gone at once |
| source fleet catalog | record set to Retired, registry row marked removed | same call |
| team bucket | every object under wal/<team>/<db>/<provision>/ | same call, no waiting period |
| kept | the team's blobs, the system fleet's copy and its own bucket folder | never deleted |

The recommended check, per copy, before that removal:
```python
position_frozen = source.freeze(db).durable_seq          already frozen, so this only reads
position_live   = system.ship_full_snapshot(db)          a fresh full copy in the system fleet's bucket folder
if position_live < position_frozen: fail the step, naming team and db; delete nothing
source.remove(db, id)
```
- Delete each only after its live copy uploads a fresh full snapshot at or past the frozen position; unproven ones stay and are named. (recommended)
- Same proof, and first copy each frozen bucket folder to a backup you delete later by word; adds a copy step and a cleanup.
- Keep all 30; a read-only pipeline run prints each one's name, size and positions, and you pick from that table.
Findings:
Root cause: none: a proof before a delete, with no defect behind it
Simplest: Delete each only after its live copy uploads a fresh full snapshot at or past the frozen position; unproven ones stay and are named.
Holds: none: no option changes code any job builds
- Removal unlinks the local files, then deletes the bucket folder in the same request, with no waiting period.
- A bucket that denies deletes keeps its folder; the team's blobs are not removed with the database.
- Whether the Oct 13 build had the snapshot upload step is unread, which is why the 30 are re-proved.
""")


WATCH_OPTIONS = [
    "watch:: with every, list and cancel. Polls only; a pushed event later is another sender of the same inbox mail",
    "event:: now, taking a Poll source first and pushed sources later. One API for good, named before the event system is designed",
    "self::alarm gains a check and rings only on a change. Smallest surface; no period under a minute, and alarms move to the central inbox",
]
WATCH_QUESTION = "Should a watch be its own watch:: package, or the first source of an event:: subscription API?"
WATCH_ASK = explained(f"""ASK {WATCH_QUESTION}
Drive tooling: your pick decides the watch API. A watch is Sand source with a check function and a period in seconds; its session's host reruns it, and a changed result arrives as one message in that session's inbox.
I recommend watch::; a builder may start on it meanwhile.
- {WATCH_OPTIONS[0]} (recommended)
- {WATCH_OPTIONS[1]}
- {WATCH_OPTIONS[2]}
Findings:
Root cause: none: this names a new feature's API; no defect is being diagnosed.
Simplest: self::alarm gains a check and rings only on a change. Smallest surface; no period under a minute, and alarms move to the central inbox
Holds: none: no option changes code any job builds
His order settles that a watch reruns code on a period and a change reaches the central inbox.
""")
MACHINE_PROFILE_READ = "Break-glass read: this Mac's machine profile requests the grant and runs one read-only query per SandSQL server (recommended)"
EXPIRED_SIGN_IN = (
    "ASK The hand repair needs break-glass, and this Mac's AWS sign-in expired. Renew it, or skip the hand repair?\n"
    'Drive tooling: your pick decides the hand repair. AWS answers "Token has expired and refresh failed", and the renewal, `aws sso login --profile core-root` in a terminal'
    " on this Mac, needs your MFA.\n"
    "- I signed in: the repair job takes the grant, proves the change on a copy and repairs the one Env (recommended)\n"
    "- Skip the hand repair: the Env waits for the code fix and its release\n"
    "Findings:\nRoot cause: none: a design pick\nSimplest: Skip the hand repair\nHolds: none: no option changes code any job builds\nKept on disk.\n"
)
PASS_BOX_TAP = (
    "ASK The hybrid sparse and dense search test needs one Touch ID tap at your Mac: start it when you are back?\n"
    "Drive tooling: your pick decides when searchbench runs. The approved searchbench run on a plat-staging pass box stopped before it began. Reading the Anthropic API key needs `cc-sudo`,"
    " which asks for your Touch ID, and you were away. Recommended: start it when you say you are at the Mac. Meanwhile nothing runs"
    " and no box is up.\n"
    "- On your tap here, the job starts the pass box and reads the key; you tap Touch ID once and get a results PR (recommended)\n"
    "- Drop the run: the job ends, and the hybrid search question keeps only the earlier reads, with no measured comparison\n"
    "Findings:\n"
    "Root cause: none: the key read is gated on your Touch ID by design, and the guard stops the prompt while you are away.\n"
    "Simplest: Drop the run\n"
    "Holds: none: no option changes code any job builds\n"
    "First principles: none: nothing to remove; the run needs the one tap.\n"
    "Next question: none: after the tap the job runs the command you approved and returns the results PR.\n"
    "- Searchbench never ran; about $0.02 of EC2 was spent, and the box and /pass-box/creds/searchbench/ were torn down.\n"
    "- Step 2 reads /app/forge/api/anthropic_api_key through cc-sudo.\n"
)
FIRST_PASS_BOX_TAP = (
    "ASK The hybrid sparse + dense search test needs one Touch ID tap at your Mac: run it now?\n"
    "Drive tooling: your pick decides when searchbench runs. The approved searchbench run on a plat-staging pass box stopped at step 2: reading /app/forge/api/anthropic_api_key needs cc-sudo,"
    " and the guard saw you were away. Searchbench never ran; about $0.02 of EC2 was spent and the box was torn down.\n"
    "- I'm at the Mac: start it now and I'll tap Touch ID once\n"
    "- Hold it until I say I'm back\n"
)


def said(count: int) -> str:
    return " ".join(["word"] * count)


def handler(lane: Path, opening: str, *read: str) -> None:
    lane.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": opening}}) + "\n")
    reads(lane, *read)


def reads(lane: Path, *threads: str, age: timedelta = timedelta(), failed: bool = False, channel: str | None = DM) -> None:
    at = (datetime.now(UTC) - age).isoformat()
    with lane.open("a") as transcript:
        for thread in threads:
            call_id = f"read-{thread}-{at}"
            where = {"url": thread} if thread.startswith("https://") else ({"channel_id": channel} if channel else {}) | {"thread_ts": thread}
            asked = where | {"limit": 1000}
            use = {"type": "tool_use", "id": call_id, "name": message_limits.THREAD_READ, "input": asked}
            result = {"type": "tool_result", "tool_use_id": call_id, "content": [{"type": "text", "text": "{}"}]} | ({"is_error": True} if failed else {})
            transcript.write(json.dumps({"type": "assistant", "timestamp": at, "message": {"role": "assistant", "content": [use]}}) + "\n")
            transcript.write(json.dumps({"type": "user", "timestamp": at, "message": {"role": "user", "content": [result]}}) + "\n")


def lane_of(transcript: Path) -> Path:
    return transcript.with_suffix("") / "subagents" / f"agent-{HANDLER}.jsonl"


@pytest.fixture
def talker(run: Run) -> str:
    run("start", "demo", "--role", "communicator")
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


def say(transcript: Path, **tool_input: object) -> str | None:
    return message_limits.deny_reason(call("slack_dm_status", transcript, **tool_input) | {"tool_name": message_limits.SAY})


def warning(transcript: Path, **tool_input: object) -> str | None:
    found = message_limits.checked(call("slack_dm_status", transcript, **tool_input) | {"tool_name": message_limits.SAY})[0]
    return found if isinstance(found, message_limits.Warned) else None


def link(ts: str) -> str:
    return f"https://acme.slack.com/archives/{DM}/p{ts.replace('.', '')}"


def reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        message_limits.cli.reads,
        "run",
        lambda argv, checkout: json.dumps({"seq": argv[2], "kind": "report", "lane": "root", "at": state.stamp(), "fields": {"attrs": {"order": f"o{argv[2]}"}}}),
    )


def holds_pr(order: str) -> None:
    with state.edit("demo") as data:
        data["prs"]["Forge-AI/monorepo#53618"] = state.new_pr("https://github.com/Forge-AI/monorepo/pull/53618", "j1", order)


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


def test_a_bug_reports_thread_gets_its_fix_live_note_only_once_the_fix_is_proven_live_and_a_second_live_report_posts_no_second_line(
    run: Run, talker: str, transcript: Path, slack_thread: Thread
) -> None:
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "fix the stuck staging deploy", "--reporter", REPORTED)
    note = {"channel_id": "C0REPORTS01", "thread_ts": "1794400100.000200", "text": "Fixed: staging deploys go through again since 10:02 AM."}
    assert message_limits.deny_reason(call("slack_reply", transcript, **note)).startswith(
        "Refused on purpose: the fix for the bug reported in this thread keeps no proof that it is live"
    )
    run("order", "set", "o1", "--proof", "https://buildkite.com/forge/release/builds/14185")
    assert message_limits.deny_reason(call("slack_reply", transcript, **note)) is None
    slack_thread.messages.append({"ts": "1794418000.000100", "thread_ts": "1794400100.000200", "from_claude": True})
    told = "https://acme.slack.com/archives/C0REPORTS01/p1794418000000100?thread_ts=1794400100.000200"
    assert run("order", "told", "o1", told)[0] == 0
    assert message_limits.deny_reason(call("slack_reply", transcript, **note)) == (
        f"Refused on purpose: this thread already heard its bug is fixed, in {told}, and a bug report's thread gets its fix-live note once."
        " Post nothing more there."
    )
    assert message_limits.deny_reason(call("slack_reply", transcript, url=told, text=note["text"])).startswith("Refused on purpose: this thread already heard")
    assert message_limits.deny_reason(call("slack_reply", transcript, **note | {"thread_ts": "1794400000.000100"})) is None
    run("order", "set", "o1", "--reporter", "https://acme.slack.com/archives/C0REPORTS02/p1794593225751189")
    assert message_limits.deny_reason(call("slack_reply", transcript, **note)).startswith("Refused on purpose: this thread already heard")
    assert message_limits.deny_reason(call("slack_reply", transcript, **note | {"channel_id": "C0REPORTS02", "thread_ts": "1794593225.751189"})) is None


def test_a_bug_reports_thread_its_order_came_in_hears_none_of_its_lines_but_a_reply_he_ordered_there_goes_out_on_its_first_try(
    run: Run, talker: str, transcript: Path
) -> None:
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "fix the stuck staging deploy", "--reporter", REPORTED)
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "keep replying to him there")
    with state.edit("demo") as data:
        data["orders"]["o1"]["thread"] = ["C0REPORTS01", "1794400100.000200"]
    reply = {"channel_id": "C0REPORTS01", "thread_ts": "1794400100.000200", "text": "SandSQL commits each batch in one round trip, so your import lands in about two seconds."}
    assert message_limits.deny_reason(call("slack_reply", transcript, **reply)).startswith(
        "Refused on purpose: the fix for the bug reported in this thread keeps no proof that it is live"
    )
    with state.edit("demo") as data:
        data["orders"]["o2"]["thread"] = ["C0REPORTS01", "1794400100.000200"]
    assert message_limits.deny_reason(call("slack_reply", transcript, **reply)) is None
    assert message_limits.reporter_post(call("slack_reply", transcript, **reply) | {"tool_name": message_limits.SAY}, state.load("demo")) is None


def test_a_fix_live_note_before_the_proof_is_judged_by_the_reporter_rule_and_refuses_when_jev_cannot_finish(run: Run, talker: str, transcript: Path) -> None:
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "fix the stuck staging deploy", "--reporter", REPORTED)
    note = {"channel_id": "C0REPORTS01", "thread_ts": "1794400100.000200", "text": "Fixed: staging deploys go through again since 10:02 AM."}
    reason = message_limits.deny_reason(call("slack_reply", transcript, **note))
    assert agents.ruled(reason) == {"rule": message_limits.REPORTER_RULE, "counted": reason}


def test_a_shared_questions_thread_gets_its_answer_only_once_it_is_posted_to_him_and_only_once(run: Run, talker: str, transcript: Path, slack_thread: Thread) -> None:
    run("order", "add", "--title", "Short title", "--project", "p", "--workstream", "w", "what is left for cloud Iris", "--kind", "answer", "--reporter", REPORTED)
    note = {"channel_id": "C0REPORTS01", "thread_ts": "1794400100.000200", "text": "What is left for cloud Iris: the cut-over, then the cleanup."}
    assert message_limits.deny_reason(call("slack_reply", transcript, **note)) == (
        "Refused on purpose: the answer to the question asked in this thread is not posted to him yet, so a note now would come before it."
        " It goes once the answer is posted."
    )
    with state.edit("demo") as data:
        for part in data["orders"]["o1"]["parts"].values():
            part["posted"] = "https://acme.slack.com/archives/D123/p1794200300000100"
    assert message_limits.deny_reason(call("slack_reply", transcript, **note)) is None
    slack_thread.messages.append({"ts": "1794418000.000100", "thread_ts": "1794400100.000200", "from_claude": True})
    told = "https://acme.slack.com/archives/C0REPORTS01/p1794418000000100?thread_ts=1794400100.000200"
    assert run("order", "told", "o1", told)[0] == 0
    assert message_limits.deny_reason(call("slack_reply", transcript, **note)) == (
        f"Refused on purpose: this thread already heard the answer to its question, in {told}, and hears it once. Post nothing more there."
    )


def test_link_labels_count_as_the_words_he_reads(talker: str, transcript: Path) -> None:
    text = said(57) + " <https://github.com/yasyf/cc-skills/pull/1|the run page shows its history>"
    assert "63 words" in posted(transcript, text=text)


def test_a_summary_of_several_features_runs_to_120_words(talker: str, transcript: Path) -> None:
    summary = f"*Run page: opens in 3 requests*\n{said(50)}\n*Cards: shorter*\n{said(60)}"
    assert posted(transcript, text=summary) is None
    assert "is 128 words, and a summary of 2 features is at most 120 words" in posted(transcript, text=summary + " " + said(10))


def test_each_table_row_is_a_line_of_its_own_and_the_words_around_the_table_keep_the_posts_limit(talker: str, transcript: Path) -> None:
    rows = "\n".join(f"| {area} | {fix} |" for area, fix in ELEVEN_FIXES.items())
    table = f"Eleven drive tooling fixes merged tonight are live.\n| Area | Fix |\n| --- | --- |\n{rows}"
    assert posted(transcript, text=table) is None
    assert posted(transcript, text="\n".join(ELEVEN_FIXES.values())).startswith("refused: this post is 137 words, and a line in his thread is at most 60 words")
    assert posted(transcript, text=f"{table}\n{said(53)}").startswith("refused: this post is 61 words, and a line in his thread is at most 60 words")
    assert posted(transcript, text=f"{table}\n| Walls | {said(61)} |").startswith(
        f'refused: the table row "Walls | {said(61)}" is 62 words, and each row of a table is a line in his thread, at most 60 words'
    )


def test_blocks_count_as_much_as_the_text(talker: str, transcript: Path) -> None:
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": said(70)}}]
    assert "70 words" in posted(transcript, text="short", blocks=blocks)


def test_a_say_post_is_checked_as_slack_shows_it_each_reference_linked_and_one_naming_nothing_is_refused(talker: str, transcript: Path) -> None:
    with state.edit("demo") as data:
        data["prs"]["yasyf/cc-skills#7"] = state.new_pr("https://github.com/yasyf/cc-skills/pull/7", "j1", "o1") | {"title": "New: \U0001f680 " + said(10)}
    say = call("slack_dm_status", transcript, text=said(55) + " {pr:7}") | {"tool_name": message_limits.SAY}
    refusal = message_limits.deny_reason(say)
    assert "65 words" in refusal
    assert refusal.endswith(
        " 10 of the words counted are the titles its {card:…}, {pr:…}, {order:…}, {record:…} and {answer:…} references draw as their words;"
        " {pr:<number>|<words>} or {record:<seq>|<words>} names one in fewer words of your own."
    )
    labeled = call("slack_dm_status", transcript, text=said(55) + " {pr:7|the run page}") | {"tool_name": message_limits.SAY}
    assert message_limits.deny_reason(labeled) is None
    unknown = call("slack_dm_status", transcript, text="see {card:demo:j9-1}") | {"tool_name": message_limits.SAY}
    assert "{card:demo:j9-1} names no card" in message_limits.deny_reason(unknown)


def test_jev_reads_a_refused_say_post_as_slack_gets_it_and_a_reference_nested_in_a_links_words_is_refused_unjudged(
    talker: str, transcript: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with state.edit("demo") as data:
        data["prs"]["yasyf/cc-skills#7"] = state.new_pr("https://github.com/yasyf/cc-skills/pull/7", "j1", "o1") | {"title": "New: \U0001f680 the run page shows its history"}
    release = "https://buildkite.com/acme/release/builds/14765"
    long = call("slack_dm_status", transcript, text=said(60) + " {pr:7}", thread_ts="1.2") | {"tool_name": message_limits.SAY}
    nested = call("slack_dm_status", transcript, text=f"<{release}|The release carrying {{pr:7|the fix}}> is failing.", thread_ts="1.2") | {"tool_name": message_limits.SAY}
    titled = call(
        "slack_dm_status",
        transcript,
        text=said(60) + " {pr:7|the run page … history}, {pr:7|the run page fix} and {pr:acme/tool#9|the run page}",
        thread_ts="1.2",
        claims=[{"claim": "the run page … history merged", "read": "gh pr view 7"}, {"claim": "the run page fix loads faster", "read": "gh pr view 7"}],
    ) | {"tool_name": message_limits.SAY}
    printed = []
    for payload in (long, nested, titled):
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        message_limits.main()
        printed.append(json.loads(capsys.readouterr().out))
    assert printed[0]["post"] == {"text": said(60) + " <https://github.com/yasyf/cc-skills/pull/7|the run page shows its history>", "written": said(60) + " {pr:7}", "thread_ts": "1.2"}
    assert printed[2]["post"]["written"] == said(60) + " {pr:7}, {pr:7|the run page fix} and {pr:acme/tool#9|the run page}"
    assert printed[2]["post"]["claims"] == [{"claim": "{pr:7} merged", "read": "gh pr view 7"}, {"claim": "the run page fix loads faster", "read": "gh pr view 7"}]
    assert printed[1]["judged"] is False
    assert "his_words" not in printed[1]
    assert printed[1]["hookSpecificOutput"]["permissionDecisionReason"].startswith(f"{{pr:7|the fix}} sits inside the words of the link to {release}")


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
        ' 9:33 PM words "your messages need to be way shorter rewrite this for a human"). A question card is posted exactly as'
        " `drive job card <its ask file> --waits demo:j1-7` prints it, never put together by hand: fix its words in the ASK"
        " block and keep it again with `drive job ask`."
    )


def test_a_cc_slack_permission_card_carries_the_whole_post_it_asks_to_send(talker: str, transcript: Path) -> None:
    card = {
        "status": "needs_you",
        "title": "Post this in the #proj-rollout thread?",
        "body": said(155),
        "options": ["Yes", "No"],
        "recommended": "Yes",
        "waits": "slack:C0B75AL4XEW:1794292257.690249:0123456789ab",
    }
    assert posted(transcript, card=card) is None
    assert posted(transcript, card=card | {"waits": "demo:j1-7"}).startswith("refused: this card is 163 words")


def test_the_answer_to_how_it_got_into_a_bad_state_goes_out_whole(talker: str, briefed: Path, slack_thread: Thread) -> None:
    his(slack_thread, LOOK_INTO, FIGURE_OUT)
    assert posted(briefed, text=HOW_IT_GOT_THERE, thread_ts=BAD_STATE_THREAD).startswith(
        "refused: this post is 185 words, and a line in his thread is at most 60 words"
    )
    assert posted(briefed, text=HOW_IT_GOT_THERE, thread_ts=BAD_STATE_THREAD, asked=FIGURE_OUT.strip()) is None
    assert slack_thread.reads == [("--channel", DM, "--thread", BAD_STATE_THREAD, "--limit", "1000")] * 2


@pytest.mark.parametrize(
    "asked",
    [
        f"{WHY_TWO_PLACES} / {STILL_NO_SENSE.strip()}",
        f'"{WHY_TWO_PLACES}"\n"{STILL_NO_SENSE.strip()}"',
        [WHY_TWO_PLACES, STILL_NO_SENSE],
        json.dumps([WHY_TWO_PLACES, STILL_NO_SENSE]),
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
    talker: str, transcript: Path, slack_thread: Thread, monkeypatch: pytest.MonkeyPatch, ts: str, before: str, after: str
) -> None:
    slack_thread.messages.append({"ts": ts, "from_claude": True, "from_bot": True, "text": before})
    monkeypatch.setattr(refs.reads, "run", lambda argv, checkout: json.dumps({"kind": "report", "text": "Restore log, 3:20 AM", "refs": {}}))
    monkeypatch.setattr(limits, "now", lambda: WRITTEN_AT.replace(hour=3))
    assert say(transcript, edit=link(ts), text=after) is None


def test_an_edit_that_grows_its_post_past_a_rewording_is_refused_naming_both_limits(talker: str, transcript: Path, slack_thread: Thread) -> None:
    slack_thread.messages.append({"ts": EDITED, "from_claude": True, "from_bot": True, "text": said(55)})
    assert say(transcript, edit=link(EDITED), text=said(65)) is None
    reason = say(transcript, edit=link(EDITED), text=said(66))
    assert reason.startswith(
        "refused: this post is 66 words, and a line in his thread is at most 60 words, or 10 past the 55 words of the post it edits"
    )
    assert reason.endswith(WHOLE)


def test_an_edit_whose_post_cannot_be_read_keeps_the_line_limit_and_says_why(talker: str, transcript: Path) -> None:
    reason = say(transcript, edit=link(EDITED), text=said(61))
    assert reason.startswith("refused: this post is 61 words, and a line in his thread is at most 60 words (")
    assert f"The post it edits could not be read to check its length: no message {EDITED} in the thread at --url {link(EDITED)}." in reason


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


def test_his_words_copied_into_the_brief_lift_nothing_until_his_message_in_the_thread_holds_them(
    talker: str, transcript: Path, slack_thread: Thread
) -> None:
    assert posted(transcript, text=said(400), thread_ts="1.2", asked="Explain the layout in   full").startswith("refused: this post is 400 words")
    his(slack_thread, "why does the browser guess at the page map? explain the layout in full")
    assert posted(transcript, text=said(400), thread_ts="1.2", asked="Explain the layout in   full") is None


def test_words_the_record_a_handler_was_started_with_quotes_as_his_lift_nothing(
    talker: str, briefed: Path, slack_thread: Thread, records: dict[str, str]
) -> None:
    records["658738"] = 'POST j1 is installed\nThis answers his "is that live and working yhet?"'
    assert posted(briefed, text=said(400), thread_ts="1.2", asked="is that live and working yhet?").startswith("refused: this post is 400 words")


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
    assert f"in no message of his in the thread this post goes to, no answer or Explain more tap of his a card there shows and no message of his in his DM an asked permalink links: {named}. {NOT_HIS}" in reason


def test_words_only_the_bot_wrote_in_the_thread_lift_nothing(talker: str, briefed: Path, slack_thread: Thread) -> None:
    slack_thread.messages.append({"ts": "1.3", "from_claude": True, "from_bot": True, "text": CHECK_SIMPLE})
    reason = posted(briefed, text=AUDIT, thread_ts=AUDIT_THREAD, asked=CHECK_SIMPLE)
    assert reason.startswith("refused: this post is 106 words, and a line in his thread is at most 60 words")
    assert reason.endswith(NOT_HIS)


def test_his_answer_on_a_card_in_the_thread_is_his_words_for_asked_and_for_jev(talker: str, transcript: Path, slack_thread: Thread) -> None:
    his(slack_thread, "is the release out?")
    slack_thread.messages.append({"ts": "1794418890.901979", "from_bot": True, "text": "", "shares": [{"text": f"Renew the sign-in or skip the hand repair?\n✓ <{HIS_PICK}|{BREAK_GLASS}> · 9:41am"}]})
    reason = posted(transcript, text=said(100), thread_ts="1.2", asked=BREAK_GLASS)
    assert reason is None
    words = message_limits.his_words(call("slack_dm_status", transcript, text=said(100), thread_ts="1.2"))
    assert [(word["id"], word["source"]) for word in words] == [("slack:1794310000.000000", "slack"), ("card:1794418890.901979:0", "card")]
    assert words[1]["quote"] == f"<{HIS_PICK}|{BREAK_GLASS}> · 9:41am"
    assert posted(transcript, text=said(100), thread_ts="1.2", asked="Use break glass every time").startswith("refused: this post is 100 words")


def test_his_explain_more_tap_on_a_card_in_the_thread_asks_for_the_whole_reply(talker: str, transcript: Path, slack_thread: Thread) -> None:
    card = "*A write re-citing an old file pointer passes on the database's own rows.*\n*1. Ask the names route first*"
    slack_thread.messages.append({"ts": "1794505940.419019", "from_bot": True, "text": card})
    refusal = posted(transcript, text=said(100), thread_ts="1.2", asked="Explain more")
    assert refusal.startswith("refused: this post is 100 words")
    assert '"Explain more"' in refusal
    for drawn in (":mag_right:", "🔎"):
        slack_thread.messages[-1]["text"] = f"{card}\n{drawn} *You asked for more at 12:06pm.* An explanation follows in this thread.\ndurable-agents · 9:52am"
        assert posted(transcript, text=said(100), thread_ts="1.2", asked="Explain more") is None
    assert posted(transcript, text=said(100), thread_ts="1.2", asked="Explain the names route").startswith("refused: this post is 100 words")
    words = message_limits.his_words(call("slack_dm_status", transcript, text=said(100), thread_ts="1.2"))
    assert [(word["id"], word["quote"], word["detail"]) for word in words] == [("card:1794505940.419019:0", "Explain more", message_limits.EXPLAIN_TAPPED)]


def test_a_share_he_ordered_to_another_persons_dm_answers_his_order_by_its_permalink(
    talker: str, transcript: Path, slack_thread: Thread, checked: Checked
) -> None:
    slack_thread.messages += [
        {"ts": "1794491173.244549", "user": "U1", "text": "why did the nightly runs crash?"},
        {"ts": "1794512898.040859", "thread_ts": "1794491173.244549", "user": "U1", "text": SHARE_ORDER},
        {"ts": "1794512900.000100", "thread_ts": "1794491173.244549", "from_bot": True, "text": "Sending the summary."},
    ]
    share = {"channel_id": TEAMMATE, "text": SHARE}
    assert "a summary of 2 features is at most 120 words" in say(transcript, **share)
    assert "in his DM an asked permalink links" in say(transcript, **share, asked=SHARE_ORDER)
    assert say(transcript, **share, asked=SHARE_LINK) is None
    assert say(transcript, **share, asked=[f"<{SHARE_LINK}>"]) is None
    assert checked.asked[-1] == [SHARE_ORDER]
    bot = f"https://acme.slack.com/archives/{DM}/p1794512900000100?thread_ts=1794491173.244549&cid={DM}"
    assert "a summary of 2 features is at most 120 words" in say(transcript, **share, asked=bot)
    reads = len(slack_thread.reads)
    elsewhere = "https://acme.slack.com/archives/C0REPORTS01/p1794512898040859"
    assert "a summary of 2 features is at most 120 words" in say(transcript, **share, asked=elsewhere)
    assert len(slack_thread.reads) == reads
    words = message_limits.his_words(call("slack_dm_status", transcript, **share, asked=SHARE_LINK) | {"tool_name": message_limits.SAY})
    assert [(word["id"], word["detail"]) for word in words] == [("slack:1794512898.040859", message_limits.LINKED_ASK)]


def test_a_line_saying_how_a_failure_is_prevented_carries_his_ruling_asking_for_it_to_jev(talker: str, transcript: Path, slack_thread: Thread) -> None:
    his(slack_thread, "why was the card never posted?")
    cause = "The card never posted because the approval check read an old head; {order:o2} makes it read the current one."
    words = message_limits.his_words(call("slack_dm_status", transcript, text=f"Fixed.\n{cause}", thread_ts="1.2"))
    assert [word["id"] for word in words] == ["slack:1794310000.000000", "ruling:6a6edbf"]
    assert words[1] == message_limits.PREVENTION_WORDS
    assert refs.prevented("Fixed.\nThe card never posted because the approval check read an old head.\n{order:o2} makes it read the current one.")
    assert not refs.prevented(f"Fixed.\nThe card never posted because the head was old.\n> {cause}")
    unrelated = message_limits.his_words(call("slack_dm_status", transcript, text="{order:o2} is building.", thread_ts="1.2"))
    assert [word["id"] for word in unrelated] == ["slack:1794310000.000000"]


def test_a_post_under_an_orders_pr_card_hands_jev_his_answer_on_its_question_card_in_the_orders_other_thread(
    run: Run, talker: str, transcript: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    question, card = "1794520890.022349", "1794526059.841239"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "give the sprite what it needs")
    with state.edit("demo") as data:
        state.join_thread(data["orders"]["o1"], (DM, question), state.stamp(), "question")
        state.join_thread(data["orders"]["o1"], (DM, card), state.stamp(), "pr")
    threads = {
        question: [{"ts": question, "from_bot": True, "text": "What should the sprite get?\n✓ *give the sprite what it needs* · answered 2:03pm"}],
        card: [{"ts": card, "from_bot": True, "text": "Approve lands PR 7\n✓ *Approve* · answered 3:28pm"}],
    }
    monkeypatch.setattr(slack, "thread", lambda *where: list(threads[where[where.index("--thread") + 1]]))
    reported(monkeypatch)
    words = message_limits.his_words(call("slack_dm_status", transcript, text="PR 7 merged.", thread_ts=card, record="1") | {"tool_name": message_limits.SAY})
    assert [(word["quote"], word["detail"]) for word in words] == [
        ("*Approve* · answered 3:28pm", message_limits.CARD_ANSWERED),
        ("*give the sprite what it needs* · answered 2:03pm", message_limits.ORDER_CARD),
    ]


def test_a_post_in_his_question_cards_thread_carries_his_ruling_that_each_option_it_explains_is_worked_out_to_jev(
    run: Run, talker: str, transcript: Path, slack_thread: Thread
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "design the run page")
    with state.edit("demo") as data:
        state.join_thread(data["orders"]["o1"], (DM, "1794288799.976279"), state.stamp(), "question")
        state.join_thread(data["orders"]["o1"], (DM, "1794288800.000100"), state.stamp(), "pr")
    his(slack_thread, "explain why 1 touches every client")
    answer = "Option 1 adds a field to ExecReply, which every client reads."
    for where in ({"thread_ts": "1794288799.976279"}, {"about": "demo:j1-661229"}):
        assert message_limits.his_words(call("slack_dm_status", transcript, text=answer, **where))[-1] == message_limits.WORKED_WORDS
    for elsewhere in ("1.2", "1794288800.000100"):
        assert message_limits.WORKED_WORDS not in message_limits.his_words(call("slack_dm_status", transcript, text=answer, thread_ts=elsewhere))
    assert message_limits.WORKED_WORDS["quote"] == limits.WORKED_WORDS["said"]


def test_a_refused_post_carries_his_words_for_jev_and_one_the_checks_pass_carries_none(
    talker: str, transcript: Path, slack_thread: Thread, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    his(slack_thread, "is the release out?")
    for text, judged in ((said(61), True), (said(5), False)):
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(call("slack_dm_status", transcript, text=text, thread_ts="1.2"))))
        message_limits.main()
        printed = capsys.readouterr().out
        assert (json.loads(printed)["his_words"][0]["quote"] if printed else None) == ("is the release out?" if judged else None)


CODE_READ = "mcp__plugin_cc-context_cc-context__ccx_code_read"
CODE_GREP = "mcp__plugin_cc-context_cc-context__ccx_code_grep"
HISTORY_POST = "Each table has a `<table>__history` shadow table, its columns plus the run (`rust/crates/sandsql-wire/src/lower.rs:300`)."
SHADOWED = (
    CODE_READ,
    {"path": "rust/crates/sandsql-wire/src/lower.rs", "section": "295-305"},
    "300  fn shadowed_specs(tables: &[TableSpec]) -> impl Iterator<Item = &TableSpec> {\n" + "\n".join(f"{line}      tables.iter()" for line in range(301, 700)),
)
SHADOW = (CODE_READ, {"path": "rust/crates/sandsql-wire/src/lower.rs", "section": "355-365"}, '360      let shadow = format!("{}__history", spec.name);')
PANIC = (
    CODE_GREP,
    {"text": "never staged", "paths": ["go/sand/next/internal/vm_run"]},
    "go/sand/next/internal/vm_run/values.go:262  panic(\"a run's values are never staged: a pack deletes the ids in its range\")",
)
REPORT = ("Read", {"file_path": "/drive/reports/j1880.md"}, "The cloud reads the inbox straight from Postgres.")


def tool(lane: Path, name: str, asked: dict, result: str) -> None:
    call_id = f"{name}-{len(lane.read_text())}"
    use = {"type": "tool_use", "id": call_id, "name": name, "input": asked}
    answer = {"type": "tool_result", "tool_use_id": call_id, "content": [{"type": "text", "text": result}]}
    with lane.open("a") as transcript:
        transcript.write(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [use]}}) + "\n")
        transcript.write(json.dumps({"type": "user", "message": {"role": "user", "content": [answer]}}) + "\n")


def shown(name: str, asked: dict, result: str) -> str:
    return f"{name} {json.dumps(asked, ensure_ascii=False)}\n{result}"


def evidence(transcript: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], **tool_input: object) -> list[dict]:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(call("slack_dm_status", transcript, **tool_input) | {"tool_name": message_limits.SAY})))
    message_limits.main()
    return json.loads(capsys.readouterr().out)["reads"]


@pytest.mark.parametrize("as_text", [False, True])
def test_jev_gets_each_call_a_claims_read_names_whole_beside_its_claims_a_read_naming_no_call_says_none_and_a_doc_backs_no_code_line(
    talker: str, transcript: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], as_text: bool
) -> None:
    lane = lane_of(transcript)
    for each in (SHADOW, SHADOWED, PANIC, REPORT):
        tool(lane, *each)
    claims = [
        {"claim": "Each table has a `<table>__history` shadow table", "read": "lower.rs:295-305"},
        {"claim": "its columns plus the run", "read": "lower.rs:295-305"},
        {"claim": "the journal never stages a run's values", "read": "values.go 250-280"},
        {"claim": "the cloud reads the inbox straight from Postgres", "read": "reports/j1880.md"},
    ]
    backed, unbacked, doc = evidence(transcript, monkeypatch, capsys, text=HISTORY_POST, thread_ts="1.2", claims=json.dumps(claims) if as_text else claims)
    assert (backed["id"], backed["quote"]) == ("read:1", f"{shown(*SHADOW)}\n\n{shown(*SHADOWED)}")
    assert '"lower.rs:295-305" behind its claims ["Each table has a `<table>__history` shadow table", "its columns plus the run"]' in backed["source"]
    assert "is a doc" not in backed["source"]
    assert (unbacked["id"], unbacked["quote"]) == ("read:2", 'none: no call of the handler\'s own holds "values.go 250-280"')
    assert (doc["id"], doc["quote"]) == ("read:3", shown(*REPORT))
    assert doc["source"].endswith('; ["/drive/reports/j1880.md"] this read opens is a doc, which backs a line telling what that doc says and no line on what code does, even word for word')


@pytest.mark.parametrize(
    "pointer",
    [
        "<persisted-output>\nOutput too large (59.5KB). Full output saved to: {saved}\n\nPreview (first 2KB):\n{preview}\n...\n</persisted-output>",
        "Error: result (60,894 characters across 1 line) exceeds maximum allowed tokens. Output has been saved to {saved}. Format: Plain text",
    ],
    ids=["Bash", "MCP"],
)
def test_an_output_the_tool_saved_to_a_file_reaches_jev_whole_by_its_command_or_by_the_files_path(
    talker: str, transcript: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], pointer: str
) -> None:
    saved = tmp_path / "b3keoof79.txt"
    saved.write_text("o937: long-running 1.0.697 installed\n" + "row\n" * 15000 + "yasyf/cc-skills#4817 merged\n")
    asked = {"command": "drive status o937 2>&1 | head -80"}
    tool(lane_of(transcript), "Bash", asked, pointer.format(saved=saved, preview="o937: long-running 1.0.697 installed"))
    claims = [{"claim": "#4817 merged", "read": "drive status o937"}, {"claim": "#4817 merged", "read": str(saved)}]
    by_command, by_path = evidence(transcript, monkeypatch, capsys, text=HISTORY_POST, thread_ts="1.2", claims=claims)
    whole = f"Bash {json.dumps(asked)} {saved}\n{saved.read_text()}"
    assert (by_command["quote"], by_path["quote"]) == (whole, whole)


def test_a_path_and_line_read_hands_jev_a_read_of_the_whole_file(
    talker: str, transcript: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    read = ("Read", {"file_path": "/repo/plugins/long-running/lib/drive/talk.py"}, "    46\tdef misplaced(drive: dict, tool_input: dict) -> str | None:")
    tool(lane_of(transcript), *read)
    claims = [{"claim": "misplaced checks where a post goes", "read": "lib/drive/talk.py:46"}]
    (backed,) = evidence(transcript, monkeypatch, capsys, text=HISTORY_POST, thread_ts="1.2", claims=claims)
    assert backed["quote"] == shown(*read)


def test_a_record_a_claim_names_or_the_post_cites_and_the_report_it_carries_reach_jev_whole_and_a_post_naming_no_claim_says_so(
    talker: str, transcript: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], records: dict[str, str]
) -> None:
    records |= {"673870": "DONE a pack deletes the run's values in its range", "675192": " ".join(["measured"] * 400), "675300": "DONE lower.rs:300 reads"}
    monkeypatch.setattr(
        refs.reads,
        "run",
        lambda argv, checkout: json.dumps({"kind": "report", "lane": "root", "at": state.stamp(), "text": records[argv[2]], "refs": {}, "fields": {"attrs": {}}}),
    )
    text = "A pack deletes the run's values in its range ({record:675192})."
    claims = [{"claim": "A pack deletes the run's values in its range", "read": "record:675300"}]
    reads = evidence(transcript, monkeypatch, capsys, text=text, thread_ts="1.2", record="673870", claims=claims)
    assert [(read["id"], read["source"], read["quote"]) for read in reads] == [
        ("record:675192", message_limits.CITED_SOURCE, records["675192"]),
        ("record:675300", message_limits.CITED_SOURCE, records["675300"]),
        ("record:673870", message_limits.CARRIED_SOURCE, records["673870"]),
    ]
    assert evidence(transcript, monkeypatch, capsys, text="On it; next line by 5:40 AM.", thread_ts="1.2") == [message_limits.UNCLAIMED]


def test_the_communicators_own_post_and_a_card_hand_jev_no_reads_and_claims_whose_text_is_not_json_are_refused_outright(
    talker: str, transcript: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    own = {key: value for key, value in call("slack_dm_status", transcript, text=HISTORY_POST, thread_ts="1.2").items() if key != "agent_id"}
    card = call("slack_dm_status", transcript, text="Which way?", card={"title": "Which way?", "body": said(5), "options": ["One", "Two"], "waits": "demo:j1-7"})
    for payload in (own, card):
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload | {"tool_name": message_limits.SAY})))
        message_limits.main()
        printed = capsys.readouterr().out
        assert not printed or json.loads(printed)["reads"] == []
    assert say(transcript, text=HISTORY_POST, thread_ts="1.2", claims="[{claim").startswith("claims is a list or object, and its text is not JSON")


def test_an_unreadable_thread_refuses_with_why(talker: str, briefed: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def unreadable(*where: str) -> list[dict]:
        raise slack.SlackError("daemon not running")

    monkeypatch.setattr(slack, "thread", unreadable)
    assert posted(briefed, text=said(100), thread_ts="1.2", asked=CHECK_SIMPLE).endswith(
        "his thread could not be read, so this post cannot be checked as a reply to his messages there: daemon not running."
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
        "his thread could not be read, so this post cannot be checked as a reply to his messages there: the cc-slack@cc-slack plugin is not installed."
    )


def test_the_communicators_own_post_has_no_brief_to_quote(talker: str, transcript: Path) -> None:
    payload = call("slack_dm_status", transcript, text=said(80), asked="explain the layout in full")
    del payload["agent_id"]
    assert "80 words" in message_limits.deny_reason(payload)


def test_a_reply_counts_only_in_his_dm(talker: str, transcript: Path) -> None:
    assert "61 words" in message_limits.deny_reason(call("slack_reply", transcript, channel_id=DM, thread_ts="1.2", text=said(61)))
    permalink = f"https://acme.slack.com/archives/{DM}/p1794288799976279"
    assert "61 words" in say(transcript, edit=permalink, text=said(61))
    share = call("slack_reply", transcript, channel_id="C0B75AL4XEW", thread_ts="1.2", text=said(300))
    assert message_limits.deny_reason(share) is None


@pytest.mark.parametrize("text", ["$(cat)", "<see file>", "$(cat post.txt)"])
def test_a_placeholder_in_place_of_the_post_is_refused_wherever_it_goes(talker: str, transcript: Path, text: str) -> None:
    assert f"The text {text} is a placeholder" in posted(transcript, thread_ts="1.2", text=text)
    share = call("slack_reply", transcript, channel_id="C0B75AL4XEW", thread_ts="1.2", text=text)
    assert "is a placeholder" in message_limits.deny_reason(share)


def test_a_pr_card_for_a_pr_he_tapped_is_refused_and_words_naming_it_go_out(talker: str, transcript: Path) -> None:
    url = "https://github.com/Forge-AI/monorepo/pull/4242"
    with state.edit("demo") as data:
        data["prs"]["Forge-AI/monorepo#4242"] = state.new_pr(url, "j1", "o1")
    assert posted(transcript, text="Fix: approve the update button?", tap_prs=[url]) is None
    with state.edit("demo") as data:
        data["prs"]["Forge-AI/monorepo#4242"]["tap"] = {"label": "Approve", "at": "2026-11-10T18:40:00-08:00", "head": "abc", "source": "slack"}
    assert "owes no tap on: Forge-AI/monorepo#4242" in posted(transcript, text="Fix: approve the update button?", tap_prs=[url])
    card = {"title": said(10), "body": f"{said(20)}\n*Show me*\n```\nthe programs hold them until <{url}|the update-button fix> lands\n```", "options": [said(5), said(5)], "waits": "demo:j1-7"}
    assert posted(transcript, card=card) is None


def test_a_post_saying_a_pr_waits_on_his_tap_is_refused_unless_he_has_its_tap_card_to_tap(
    talker: str, transcript: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, link = "https://github.com/Forge-AI/monorepo/pull/53971", f"https://acme.slack.com/archives/{DM}/p1794300000000400"
    with state.edit("demo") as data:
        data["prs"]["Forge-AI/monorepo#53971"] = state.new_pr(url, "j1", "o1") | {"head": "abc123", "tap": {"label": "Land it", "at": state.stamp(), "head": "abc123", "source": "slack"}}
    card = {"title": said(5), "body": "#53971 waits on your tap.", "options": [said(3), said(3)], "waits": "demo:j1-7"}
    assert "Forge-AI/monorepo#53971: he tapped Land it at" in posted(transcript, card=card)
    text = f"<{url}|The unfurl fix> waits on <{url}|your Approve>."
    assert "Forge-AI/monorepo#53971: he tapped Land it at" in say(transcript, text=text)
    assert posted(transcript, text=f"<{url}|The unfurl fix> needs no tap.") is None
    with state.edit("demo") as data:
        data["prs"]["Forge-AI/monorepo#53971"]["tap"] = None
    assert "Forge-AI/monorepo#53971: no open tap card of his stands at its head" in posted(transcript, text=text)
    tap = {"asked": state.stamp(), "url": link, "prs": [url], "card": {"title": "Fix: land it?", "prs": [{"url": url, "head": "abc123"}]}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [], "pr_cards": [tap], "closed": []})
    view.refresh_cards("demo")
    assert f"Forge-AI/monorepo#53971: the words do not link its open tap card {link}" in posted(transcript, text=text)
    assert posted(transcript, text=f"{text} <{link}|Its tap card> is up.") is None
    with state.edit("demo") as data:
        data["prs"]["Forge-AI/monorepo#53971"]["head"] = "def456"
    assert "Forge-AI/monorepo#53971: no open tap card of his stands at its head" in posted(transcript, text=f"{text} <{link}|Its tap card> is up.")


def test_a_sentence_saying_one_pr_shipped_and_another_waits_on_his_approve_is_judged_by_the_tap_card_it_links(
    talker: str, transcript: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shipped, bump = "https://github.com/yasyf/cc-remote/pull/162", "https://github.com/Forge-AI/monorepo/pull/55639"
    link = f"https://acme.slack.com/archives/{DM}/p1794558425491189"
    with state.edit("demo") as data:
        data["prs"]["yasyf/cc-remote#162"] = state.new_pr(shipped, "j1", "o1") | {"head": "9f1c2ab", "state": "merged"}
        data["prs"]["Forge-AI/monorepo#55639"] = state.new_pr(bump, "j2", "o1") | {"head": "4d7e0c3"}
    tap = {"asked": state.stamp(), "url": link, "prs": [bump], "card": {"title": "New: bump the pin?", "prs": [{"url": bump, "head": "4d7e0c3"}]}}
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [], "pr_cards": [tap], "closed": []})
    view.refresh_cards("demo")
    text = (
        "*Fixed: a builder restarted on your card answer now builds your pick, not its old result.* "
        "<https://github.com/yasyf/cc-skills/pull/4820|The fix> is live in long-running 1.0.684. "
        f"The Sprite-disk builder it stuck shipped your pick in <{shipped}|cc-remote>; "
        f"<{bump}|the monorepo bump to it> is green and waits on your Approve on <{link}|its tap card>."
    )
    assert say(transcript, text=text) is None
    uncarded = say(transcript, text=text.replace(f" on <{link}|its tap card>", ""))
    assert f"Forge-AI/monorepo#55639: the words do not link its open tap card {link}" in uncarded
    assert "yasyf/cc-remote#162" not in uncarded
    assert "yasyf/cc-remote#162: merged" in say(transcript, text=f"<{shipped}|The cc-remote fix> waits on your Approve.")
    with state.edit("demo") as data:
        data["prs"]["Forge-AI/monorepo#55639"]["head"] = "b85e1f0"
    assert f"Forge-AI/monorepo#55639: its tap card {link}, which the words link, no longer stands: it moved past the head the card shows" in say(transcript, text=text)


def test_a_tap_card_say_just_drew_lets_the_next_say_link_it_as_what_the_pr_waits_on(
    run: Run, talker: str, transcript: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it")
    url, link = "https://github.com/yasyf/cc-skills/pull/7", f"https://acme.slack.com/archives/{DM}/p1794300000000500"
    with state.edit("demo") as data:
        data["prs"]["yasyf/cc-skills#7"] = state.new_pr(url, "j1", "o1") | {"head": "f3ed5a7"}
    drawn: list[dict] = []
    monkeypatch.setattr(view, "cards", lambda name: {"team_url": "", "questions": [], "pr_cards": drawn, "closed": []})
    view.refresh_cards("demo")
    text = f"<{url}|The unfurl fix> waits on your Approve: <{link}|its tap card>."
    assert "yasyf/cc-skills#7: no open tap card of his stands at its head" in say(transcript, text=text)

    def draw(*argv: str) -> dict:
        drawn.append({"asked": state.stamp(), "url": link, "prs": [url], "card": {"title": "Fix: land it?", "prs": [{"url": url, "head": "f3ed5a7"}]}})
        return {"permalink": f"https://acme.slack.com/archives/{DM}/p1794300000000400", "card_permalink": link}

    monkeypatch.setattr(slack, "cc_slack", draw)
    talk.say("demo", {"text": "ship the fix", "tap_prs": [url]})
    assert say(transcript, text=text) is None


def test_a_post_that_is_one_slack_link_or_mention_goes_out(talker: str, transcript: Path) -> None:
    assert posted(transcript, thread_ts="1.2", text="<https://github.com/yasyf/cc-skills/pull/1|the fix>") is None
    assert posted(transcript, thread_ts="1.2", text="<@U123>") is None


def test_a_session_that_is_no_drives_communicator_is_not_limited(run: Run, demo: str, transcript: Path) -> None:
    assert posted(transcript, text=said(300)) is None
    assert message_limits.deny_reason(call("slack_dm_status", transcript, session="elsewhere", text=said(300))) is None


def test_a_handlers_line_in_his_thread_waits_for_its_own_fresh_read_of_that_thread(talker: str, unread: Path) -> None:
    read_it = (
        f", so this line could miss what he said there since. Read it with slack_thread(channel_id={DM}, thread_ts=1.2,"
        " limit=1000), then post. When the tool saves a long thread to a file, the call is not a read until you open that"
        " file and read its newest messages."
    )
    no_read = "refused: your transcript shows no read of his thread 1.2, and a read by another agent or session does not count for yours"
    assert posted(unread, text="Fixed.", thread_ts="1.2") == no_read + read_it
    reads(lane_of(unread), "1.3")
    reads(lane_of(unread), "1.2", channel="C0B75AL4XEW")
    assert posted(unread, text="Fixed.", thread_ts="1.2") == no_read + read_it
    reads(lane_of(unread), "1.2", failed=True)
    assert posted(unread, text="Fixed.", thread_ts="1.2") == "refused: your slack_thread call on his thread 1.2 failed" + read_it
    reads(lane_of(unread), "1.2", age=timedelta(minutes=11))
    assert posted(unread, text="Fixed.", thread_ts="1.2") == "refused: your last read of his thread 1.2 was 11 minutes ago, and a read counts for 10" + read_it
    reads(lane_of(unread), "1.2", age=timedelta(minutes=9))
    assert posted(unread, text="Fixed.", thread_ts="1.2") is None
    assert message_limits.deny_reason(call("slack_reply", unread, channel_id=DM, thread_ts="1.4", text="Fixed.")).startswith(
        "refused: your transcript shows no read of his thread 1.4"
    )


def test_a_handlers_say_in_an_alerts_thread_waits_for_its_own_read_of_that_thread_in_the_alerts_channel(talker: str, unread: Path) -> None:
    line = {"text": "Fixed.", "channel_id": "C0B75AL4XEW", "thread_ts": "1.2"}
    read_it = "Read it with slack_thread(channel_id=C0B75AL4XEW, thread_ts=1.2, limit=1000), then post."
    assert read_it in say(unread, **line)
    reads(lane_of(unread), "1.2")
    assert read_it in say(unread, **line)
    reads(lane_of(unread), "1.2", channel="C0B75AL4XEW")
    assert say(unread, **line) is None

def test_a_transcript_row_with_no_timestamp_is_no_read_and_breaks_none(talker: str, unread: Path) -> None:
    use = {"type": "tool_use", "id": "read-blob", "name": message_limits.THREAD_READ, "input": {"channel_id": DM, "thread_ts": "1.2"}}
    with lane_of(unread).open("a") as transcript:
        transcript.write(json.dumps({"type": "api-request-blob", "message": {"role": "assistant", "content": [use]}}) + "\n")
    assert posted(unread, text="Fixed.", thread_ts="1.2").startswith("refused: your transcript shows no read of his thread 1.2")
    reads(lane_of(unread), "1.2")
    assert posted(unread, text="Fixed.", thread_ts="1.2") is None


def test_a_read_that_leaves_out_channel_id_reads_his_dm_so_the_line_after_it_goes_out(talker: str, unread: Path) -> None:
    reads(lane_of(unread), "1.2", channel=None)
    assert say(unread, text="Fixed.", thread_ts="1.2") is None
    assert posted(unread, text="Fixed.", thread_ts="1.2") is None


@pytest.mark.parametrize("read", [link("1794543650.223299"), f"{link('1794552185.429099')}?thread_ts=1794543650.223299&cid={DM}"])
def test_a_read_by_his_threads_permalink_or_a_replys_counts_so_the_say_after_it_goes_out(talker: str, unread: Path, read: str) -> None:
    reads(lane_of(unread), read)
    assert say(unread, text="Fixed.", thread_ts="1794543650.223299") is None


def test_a_read_by_the_permalink_of_another_thread_or_channel_still_refuses(talker: str, unread: Path) -> None:
    reads(lane_of(unread), link("1794543650.223300"), "https://acme.slack.com/archives/C0B75AL4XEW/p1794543650223299")
    assert say(unread, text="Fixed.", thread_ts="1794543650.223299").startswith("refused: your transcript shows no read of his thread 1794543650.223299")


def test_a_card_an_edit_a_root_line_and_the_communicators_own_line_need_no_thread_read(talker: str, unread: Path) -> None:
    card = {"title": said(10), "body": said(40), "options": ["Land it", "Hold it"], "waits": "demo:j1-7"}
    assert posted(unread, card=card, thread_ts="1.2") is None
    assert say(unread, edit=link(EDITED), text="Fixed.") is None
    assert posted(unread, text="Fixed.") is None
    payload = call("slack_dm_status", unread, text="Fixed.", thread_ts="1.2")
    del payload["agent_id"]
    assert message_limits.deny_reason(payload) is None


def test_a_handlers_post_links_each_thing_it_names_where_it_names_it(talker: str, transcript: Path) -> None:
    holds_pr("o1")
    refusal = posted(transcript, text=TEAMMATES_PR)
    assert refusal.startswith(
        "refused: \"teammate's PR\" names something he could open with no link there, in \"A teammate's PR landed earlier"
        " today\"; put those words in its link, <url|teammate's PR>, so he opens it where it is named"
    )
    assert '"never mention somehting and expect me to go look it up myself"' in refusal
    assert posted(transcript, text=JOSHS_PR) is None
    assert posted(transcript, text='He wrote "the fix is in" at noon; a fix for the rest is being written.') is None
    assert "says only a number" in posted(transcript, text="<https://github.com/Forge-AI/monorepo/pull/53618|#53618> landed.")
    assert "is a number he cannot open" in posted(transcript, text="Juniper's change, #53618, landed.")
    assert posted(transcript, text=TEAMMATES_PR, tap_prs=["https://github.com/Forge-AI/monorepo/pull/53618"]) is None
    ack = call("slack_dm_status", transcript, text=TEAMMATES_PR)
    del ack["agent_id"]
    assert message_limits.deny_reason(ack) is None


def test_a_cards_title_and_options_name_only_what_its_summary_links(talker: str, transcript: Path) -> None:
    holds_pr("o1")
    card = {
        "title": "A teammate's PR landed first: keep it or replace it?",
        "body": TEAMMATES_PR,
        "options": ["Keep theirs (recommended)", "Replace it; it undoes a teammate's landed PR"],
        "waits": "demo:j1-7",
    }
    refusal = posted(transcript, card=card)
    assert "\"teammate's PR\" names something he could open with no link there" in refusal
    assert "\"A teammate's PR\" in the question or an option names something he could open" not in refusal
    assert "\"teammate's landed PR\" in the question or an option names something he could open, and those draw no links" in refusal
    assert posted(transcript, card=card | {"body": JOSHS_PR}) is None
    assert "in the question or an option" in posted(transcript, card=card | {"body": JOSHS_PR.replace("PR>", "change>")})


def test_a_plain_noun_needs_no_link_and_a_pr_the_drive_holds_on_the_order_say_records_the_post_on_does(
    run: Run, talker: str, transcript: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for words in ("ship the fix", "unblock the users"):
        run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", words)
    reported(monkeypatch)
    for plain in ("the thread", "that write thread", "that fix"):
        assert say(transcript, text=f"The server stalled while {plain} held its lock.", record="1") is None
    first = "Once the first PR lands, the alerts stop."
    assert say(transcript, text=first, record="1") is None
    holds_pr("o2")
    assert say(transcript, text=first, record="1") is None
    assert say(transcript, text=first, record="2").startswith(
        'refused: "the first PR" names something he could open with no link there, in "Once the first PR lands"; put those words'
        " in its link, <url|the first PR>"
    )
    assert say(transcript, text=first).startswith('refused: "the first PR" names something he could open')


@pytest.mark.parametrize(
    "text",
    [
        "*The PR is up and green: <https://github.com/Forge-AI/monorepo/pull/53618|the update-button PR>*",
        "<https://github.com/Forge-AI/monorepo/pull/53618|The update-button PR> is green; that PR lands on your tap.",
        "Its API is next: its PR goes up first.",
        "The first PR is not open yet.",
        "Juniper's <https://github.com/Forge-AI/monorepo/pull/53618|update-button> PR landed.",
        "The PR-review bot posted twice, and the PR freeze holds.",
        "That waits a PR.",
        "Every engineer's pull request gets a check.",
        "The run prints:\n```\nthe first PR lands\n```",
    ],
)
def test_a_mention_the_post_links_or_that_names_nothing_to_open_needs_no_link(talker: str, transcript: Path, text: str) -> None:
    holds_pr("o1")
    assert posted(transcript, text=text) is None


def test_a_phrase_said_twice_is_refused_at_each_clause_and_once_across_a_cards_options(talker: str, transcript: Path) -> None:
    holds_pr("o1")
    refusal = posted(transcript, text="The first PR lands today. The alerts stop once the first PR is live.")
    assert '"The first PR" names something he could open with no link there, in "The first PR lands today";' in refusal
    assert '"the first PR" names something he could open with no link there, in "The alerts stop once the first PR is live";' in refusal
    card = {"title": said(5), "body": said(20), "options": ["Keep their PR (recommended)", "Close their PR"], "waits": "demo:j1-7"}
    assert posted(transcript, card=card).count('"their PR" in the question or an option') == 1


def test_a_pr_that_exists_keeps_its_link_whatever_its_state_and_one_not_opened_yet_needs_none(talker: str, transcript: Path) -> None:
    holds_pr("o1")
    for standing in ("The first PR waits on your tap.", "The first PR has not merged."):
        assert say(transcript, text=standing).startswith('refused: "The first PR" names something he could open with no link there')
    for unopened in ("No PR yet.", "A PR goes up next.", "The first PR is next."):
        assert say(transcript, text=unopened) is None


def test_a_post_refused_several_ways_hears_every_way_at_once_after_its_thread_read(talker: str, transcript: Path, checked: Checked) -> None:
    holds_pr("o1")
    checked.findings = ["1:5 bare-number '#41': link it as <url|#N> or <url|deploy #N>"]
    text = said(61) + " Once the first PR lands, the alerts stop."
    refusal = posted(transcript, text=text, thread_ts="1.2")
    assert refusal.startswith("refused, 3 ways; fix each, then post the fixed words once:\n1. this post is 69 words")
    assert '\n2. "the first PR" names something he could open with no link there' in refusal
    assert "\n3. `cc-slack check-post` finds this in the post's exact words" in refusal
    assert posted(transcript, text=text, thread_ts="1.9").startswith("refused: your transcript shows no read of his thread 1.9")


def test_a_post_drawing_a_tap_card_or_a_card_may_say_it_waits_on_him(talker: str, transcript: Path, checked: Checked) -> None:
    url = "https://github.com/Forge-AI/monorepo/pull/4242"
    with state.edit("demo") as data:
        data["prs"]["Forge-AI/monorepo#4242"] = state.new_pr(url, "j1", "o1")
    checked.findings = ['1:1 waits-without-card "Ready for your tap: the backfill runs as a service account": link the card']
    text = "Ready for your tap: the backfill runs as a service account."
    assert posted(transcript, text=text, tap_prs=[url]) is None
    assert posted(transcript, text=text, card={"title": said(5), "body": text, "options": ["Yes", "No"], "waits": "demo:j1-7"}) is None
    assert "waits-without-card" in posted(transcript, text=text)


def test_a_line_naming_a_cause_with_no_record_is_found_its_handlers_claim_and_his_own_words_are_not(talker: str, transcript: Path) -> None:
    caused = "Merged.\nThe runs failed because the pool was full"
    assert say(transcript, text=caused) == (
        "refused: a line naming a cause carries {record:<seq>} of the record that measured it, on that line, or, in a handler's post, sits in claims"
        " beside the read behind it: The runs failed because the pool was full"
    )
    assert say(transcript, text=caused, claims=[{"claim": "The runs failed because the pool was full", "read": "pool.go:40-52"}]) is None
    assert say(transcript, text='> did it fail because of the pool?\nYou asked "is it because of the pool"; the runs are back.') is None
    assert refs.unbacked("```text\nThe cause is the pool\n```\nDue to a full pool, runs wait.") == ["Due to a full pool, runs wait."]
    for reading in ("*Why plat SandSQL lagged:* an agent is now reading the cause, minute by minute through the lag.", "Root cause: yes, a read is on it."):
        assert say(transcript, text=reading) is None
    assert refs.unbacked("*Why plat SandSQL lagged:* the cause was a full replication queue.\nRoot cause: the pool.\nIt is caused by the pool.") == [
        "*Why plat SandSQL lagged:* the cause was a full replication queue.",
        "Root cause: the pool.",
        "It is caused by the pool.",
    ]


def test_jev_judges_each_finding_by_the_rule_it_names_and_an_order_or_job_id_refuses_unjudged(talker: str, transcript: Path, checked: Checked) -> None:
    cause = "The runs failed because the pool was full"
    assert say(transcript, text=cause).rule == message_limits.CAUSE_RULE
    checked.findings = ["1:14 drive-term 'the drive': say what happens to the reader's work instead"]
    both = say(transcript, text=f"Looking into the drive dashboard.\n{cause}")
    assert both.rule == f"1. {agents.PLAIN_WORDS_RULE}\n2. {message_limits.CAUSE_RULE}"
    checked.findings.append("2:10 drive-term 'j1608': say what happens to the reader's work instead")
    unjudged = say(transcript, text=f"Looking into the drive dashboard.\nChecking j1608: {cause}")
    assert isinstance(unjudged, agents.Unjudged)
    assert unjudged.endswith("as asked:\n2:10 drive-term 'j1608': say what happens to the reader's work instead")
    assert "the pool" not in unjudged and "'the drive'" not in unjudged


def test_a_post_telling_him_the_drive_failed_goes_out_warning_the_session_until_it_names_the_order_or_pr_preventing_it_on_any_line_or_draws_its_card(talker: str, transcript: Path) -> None:
    failed = "Its card was refused: nobody checked that PR against your words."
    disconnected = "The meta session's cci disconnected, so your order never reached it."
    fix = "<https://github.com/yasyf/cc-skills/pull/4460|the card hook fix>"
    said = "\n".join(
        (
            failed,
            disconnected,
            f"The card hook refused it tonight; {fix} makes that hook read each PR's title.",
            "An older SandSQL reader in the api refused that session's newer format.",
            "> nobody checked the card",
            'You asked "why was the card refused"; the card is up now.',
        )
    )
    assert refs.unprevented(said, {}) == []
    told = f"{failed}\n{disconnected}"
    assert refs.unprevented(told, {}) == [failed, disconnected]
    assert say(transcript, text=told) is None
    warned = warning(transcript, text=told)
    assert warned.startswith("message_limits: this post goes out as written") and "6a6edbf" in warned
    assert warned.endswith(f"The lines: {failed} | {disconnected}")
    with state.edit("demo") as data:
        data["prs"]["yasyf/cc-skills#4460"] = state.new_pr("https://github.com/yasyf/cc-skills/pull/4460", "j1", "o1")
    assert message_limits.checked(call("slack_dm_status", transcript, text=told, tap_prs=["https://github.com/yasyf/cc-skills/pull/4460"]) | {"tool_name": message_limits.SAY})[0] is None
    prevented = f"Its card was refused: nobody checked that PR against your words. {fix} makes the card hook check it."
    assert message_limits.checked(call("slack_dm_status", transcript, text=prevented) | {"tool_name": message_limits.SAY})[0] is None


def test_a_line_telling_what_the_product_refuses_goes_out_with_a_warning_jev_never_judges(
    talker: str, transcript: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    old_pod = (
        "2. The check that this pod still runs the agent compares `writer` in Postgres `agent_placements`"
        " (<https://github.com/Forge-AI/monorepo/blob/e39f84cac477b65f7a0957b63b99e8c09a0532d8/api/src/entities/CloudAgent.ts#L213|CloudAgent.ts:213>)."
        " `writer` is a number that goes up each time the agent moves to a new pod, so an old pod is refused. SandSQL cannot read Postgres."
    )
    staging = "The drive's staging request was refused by SandSQL's own check, which reads only rows of its env."
    for text in (old_pod, staging):
        assert say(transcript, text=text) is None, text
        assert warning(transcript, text=text).endswith(f"The lines: {text}")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(call("slack_dm_status", transcript, text=old_pod) | {"tool_name": message_limits.SAY})))
    message_limits.main()
    printed = json.loads(capsys.readouterr().out)
    assert printed["hookSpecificOutput"] == {"hookEventName": "PreToolUse", "additionalContext": warning(transcript, text=old_pod)}
    refused = say(transcript, text=f"{old_pod}\nThe runs failed because the pool was full")
    assert refused.startswith("refused: a line naming a cause") and old_pod not in refused and refused.rule == message_limits.CAUSE_RULE


def test_a_line_reading_as_a_drive_failure_or_citing_a_defect_record_warns_the_session_it_owes_the_fix_preventing_it(
    talker: str, transcript: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shown = {
        "671981": {"kind": "defect", "text": "check-post refused a status table as a wall of text", "refs": {}},
        "672190": {"kind": "report", "text": "the one refused request was this drive's own proof job", "refs": {}},
    }
    monkeypatch.setattr(refs.reads, "run", lambda argv, checkout: json.dumps(shown[argv[2]]))
    proof = "The one refused request was this drive's own proof job starting a staging cloud agent."
    uncited = "Its card was refused: nobody checked that PR against your words."
    cited = "The say hook refused the table post, {record:671981}."
    passive = "The drive's staging request was refused by SandSQL."
    defect = "Tables post as Slack tables again, {record:671981}."
    fix = "<https://github.com/yasyf/cc-skills/pull/4486|the say fix>"
    said = "\n".join((proof, uncited, cited, passive, defect, "SandSQL's answer is in {record:672190}."))
    assert refs.unprevented(said, refs.resolved("demo", said)) == [proof, uncited, cited, passive, defect]
    assert refs.unprevented(f"{said}\n{cited} {fix} counts each table row on its own.", refs.resolved("demo", said)) == []
    told = f"{cited}\n{defect}"
    assert say(transcript, text=told) is None
    assert warning(transcript, text=told).endswith(f"The lines: {cited} | {defect}")


def test_a_line_answering_his_order_by_a_missing_plan_or_record_is_found_and_a_line_doing_it_is_not(talker: str, transcript: Path) -> None:
    excuse = "Left the compact window at 600k: no plan or recorded answer of his names 1024 tokens."
    refused = say(transcript, text=excuse)
    assert refused is not None and refused.startswith("refused: his message is the order") and refused.endswith(f"The lines: {excuse}")
    assert "Nothing in the plan asks for a lazy cache" in say(transcript, text="Nothing in the plan asks for a lazy cache, so the disk stays as is.")
    for text in ("Doing it now: the compact window drops to 1024 tokens.", "> no plan names it\nThe compact window drops to 1024 tokens now.", "No new records for an hour."):
        assert say(transcript, text=text) is None


def test_a_post_saying_a_coordinator_is_down_is_found_and_one_saying_it_was_restarted_is_not(talker: str, transcript: Path) -> None:
    with state.edit("demo") as data:
        data["roles"]["coordinator"] = {"session": "coordinator-session", "terminal": None, "cwd": "/work", "started": state.stamp()}
    for text in ("The coordinator is down, so no record has moved for an hour.", "The coordinator's process exited, so the records wait."):
        refused = say(transcript, text=text)
        assert refused is not None and refused.startswith("refused: a coordinator that is down is restarted that minute, never reported to him as down"), text
        assert "creating the terminal resuming session coordinator-session in /work, or a sub-coordinator's session" in refused
    for text in ("The coordinator's process exited; I restarted it a minute later and the records move again.", "The coordinator answered every record this hour."):
        assert say(transcript, text=text) is None, text


def test_a_post_calling_a_job_stuck_is_found_until_its_poster_read_that_jobs_transcript(talker: str, transcript: Path) -> None:
    text = "The copy fix's builder, j702, is stuck with no line for 40 minutes."
    assert say(transcript, text=text) == (
        "refused: this line calls j702 stuck or silent, and your transcript shows no read of its transcript in the last 10"
        " minutes; read it with `drive job replies <j>` or `cc-transcript`, then quote what it shows"
    )
    for age, command in ((timedelta(minutes=30), "drive job replies j702"), (timedelta(), "drive job replies j702")):
        use = {"type": "tool_use", "id": f"replies-{age}", "name": "Bash", "input": {"command": command}}
        with lane_of(transcript).open("a") as lane:
            lane.write(json.dumps({"type": "assistant", "timestamp": (datetime.now(UTC) - age).isoformat(), "message": {"role": "assistant", "content": [use]}}) + "\n")
        assert (say(transcript, text=text) is None) == (age == timedelta()), age
    assert say(transcript, text="The copy fix's builder is at work for 40 minutes.") is None


def test_say_refuses_a_line_saying_work_has_no_one_on_it_until_it_names_the_job_on_it_now(talker: str, transcript: Path) -> None:
    refused = say(transcript, text="The stall watcher flagged the partition split stuck and no job has picked it up.")
    assert refused is not None and refused.startswith('refused: "no job has picked" says work has no one on it and the line names no job')
    assert refused.rule == agents.UNOWNED_RULE
    for text in ("No one was working on the partition split, so j1 builds it now.", "No one needs to work on the old export again."):
        assert say(transcript, text=text) is None, text


@pytest.mark.parametrize(
    ("text", "written", "past"),
    [
        ("If you do not tap, the hold expires at 6:54 PM Pacific today and the release stops.", datetime(2026, 11, 13, 7, 10, 7), False),
        ("The release hold was set at 6:54 AM.", datetime(2026, 11, 13, 7, 10, 7), True),
        ("I relaunch the builder after 6:52 PM.", datetime(2026, 11, 12, 18, 43, 29), False),
        ("Turns have failed since 10:23 PM.", datetime(2026, 11, 13, 0, 11, 1), True),
        ("Next line by 12:20 AM.", datetime(2026, 11, 12, 23, 50), False),
        ("The card went up (Nov 9 9:28 PM) and waits on him.", datetime(2026, 11, 12, 19, 0), True),
        ("The hold lifts at 6:54 AM tomorrow.", datetime(2026, 11, 12, 17, 0), False),
        ("The hold lifted at 6:54 AM yesterday.", datetime(2026, 11, 12, 5, 0), True),
    ],
)
def test_a_typed_clock_time_is_past_only_at_or_before_its_write_on_the_day_written_beside_it_else_the_day_nearest_the_write(
    text: str, written: datetime, past: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(limits, "now", lambda: written.astimezone())
    assert (limits.typed_time(text, {"jobs": {}}) is not None) == past


def test_say_refuses_a_time_typed_for_past_work_and_passes_a_jobs_start_from_its_stamp_and_a_promise(talker: str, transcript: Path) -> None:
    with state.edit("demo") as data:
        data["jobs"]["j1"] = state.new_job("o1", "builder", "fix the replication sync", [], state.stamp())
    refused = say(transcript, text="The fix job started at 4:55 AM, about 25 minutes late.")
    assert refused is not None and refused.startswith('refused: "4:55 AM" is a time typed for work already done')
    assert refused.rule == agents.TYPED_TIME_RULE
    assert say(transcript, text="The fix job started at {started:j1}; next line by 5:40 AM.") is None
    assert say(transcript, text="The fix job's log printed `4:55:02 AM` for its first write.") is None


def test_say_passes_a_past_window_typed_in_pacific_on_the_line_linking_the_read_that_measured_it(
    talker: str, transcript: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    measured = {"kind": "report", "text": "the model API returned 503 to one call in three from 09:35 to 09:55 UTC", "refs": {}}
    monkeypatch.setattr(refs.reads, "run", lambda argv, checkout: json.dumps(measured))
    window = "From 1:35 to 1:55 AM, one in three calls to the model API returned 503"
    assert say(transcript, text=f"{window}, {{record:690223|measured here}}.") is None
    assert say(transcript, text=f"{window}, as <https://app.datadoghq.com/notebook/1|the Datadog notebook> shows.") is None
    refused = say(transcript, text=f"{window}.\n<https://app.datadoghq.com/notebook/1|The Datadog notebook> shows each call.")
    assert refused is not None and refused.startswith('refused: "1:55 AM" is a time typed for work already done')
    assert "in Pacific on the line linking the tool or read that measured it" in refused


def test_say_refuses_a_jobs_start_given_as_when_a_release_started_and_passes_the_builds_own_start_on_the_line_linking_it(
    talker: str, transcript: Path
) -> None:
    with state.edit("demo") as data:
        data["jobs"]["j1"] = state.new_job("o1", "builder", "release the sandsql component", [], state.stamp())
    for posted in (
        "*SandSQL is releasing at 9b6130c, on your pick; it started {started:j1}.* api needs no release.",
        "*SandSQL at api's commit 9b6130c started releasing at {started:j1}* to plat-usw2-prod.",
    ):
        refused = say(transcript, text=posted)
        assert refused is not None and refused.rule == agents.TYPED_TIME_RULE, posted
        assert "and this line says when a release, build or deploy started: a job's or record's stamp never stands in" in refused, posted
    assert say(transcript, text="*SandSQL is releasing; it started {started:j1}.*").startswith("refused: {started:j1} is when job j1 started")
    build = "<https://buildkite.com/forge/release/builds/15020|The SandSQL release build>"
    assert say(transcript, text=f"{build} started `11:30 AM`; its builder started at {{started:j1}}.") is None
    assert say(transcript, text="<https://buildkite.com/forge/release/builds/15020|The SandSQL release build started 11:30 AM>.") is None
    unlinked = say(transcript, text="The SandSQL release build started `11:30 AM`.")
    assert unlinked is not None and unlinked.startswith("refused: `11:30 AM` says when a release, build or deploy started, and its line links no build")


def test_a_summary_naming_a_drive_pr_is_found_until_it_says_what_the_pr_serves(talker: str, transcript: Path, run: Run) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "Drive tooling: the watch reads taps")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Cards", "cards show under the line")
    holds_pr("o1")
    with state.edit("demo") as data:
        data["prs"]["Forge-AI/monorepo#53619"] = state.new_pr("https://github.com/Forge-AI/monorepo/pull/53619", "j1", "o2")
        data["prs"]["Forge-AI/monorepo#53620"] = state.new_pr("https://github.com/Forge-AI/monorepo/pull/53620", "j1", "o2") | {"port_of": 49001}
    taps, cards, port = (
        f"<https://github.com/Forge-AI/monorepo/pull/{number}|{words}>"
        for number, words in ((53618, "The tap reader"), (53619, "The card line"), (53620, "The old card fix"))
    )
    summary = f"*Taps*\n{taps} landed this morning.\n*Cards*\n{cards} is green, and {port} landed tonight."
    assert say(transcript, text=summary) == (
        "refused: this summary names a PR and never says what it serves, the first thing he asks of a PR; say it where each is"
        ' named: Forge-AI/monorepo#53618 is "Drive tooling:" and what it unblocks; Forge-AI/monorepo#53619 is his order'
        " {order:o2}; Forge-AI/monorepo#53620 is port of #49001"
    )
    assert say(transcript, text=summary.replace("landed this morning", "landed this morning, Drive tooling: the watch reads taps")) is None
    assert say(transcript, text=f"{taps} landed this morning.") is None


def test_a_word_he_corrected_is_found_in_the_text_a_links_words_a_caption_or_a_card_and_not_in_a_quote_of_him(talker: str, transcript: Path) -> None:
    with state.edit("demo") as data:
        data["stand_ins"]["backup"] = "S3 replica"
    assert say(transcript, text="The backup copy is current.") == (
        "refused: this post calls a thing by a word he corrected, so write its proper term in that word's place:"
        ' "backup" becomes "S3 replica" (his Nov 11 12:36 AM words "use the proper fucking terms for things")'
    )
    assert '"backups" becomes "S3 replica"' in posted(transcript, card={"title": "Restore from which backups?", "body": "One line.", "options": ["Yes", "No"]})
    assert '"backup" becomes "S3 replica"' in say(transcript, text="The <https://example.com/replica|backup copy> is current.")
    assert '"backup" becomes "S3 replica"' in say(transcript, text="Replica status.", visuals=[{"kind": "chart", "source": "{}", "caption": "The backup copy."}])
    for text in ("The S3 replica is current.", 'You asked "is the backup current"; the S3 replica is.', "> Is the backup copy current?\nThe S3 replica is current."):
        assert say(transcript, text=text) is None


def test_a_pr_card_serving_drive_tooling_under_a_line_leaving_out_that_orders_words_is_found(talker: str, transcript: Path, run: Run) -> None:
    tooling = "Drive tooling: a card named in a post shows under the line"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Cards", "cards show under the line")
    holds_pr("o1")
    with state.edit("demo") as data:
        data["orders"]["o1"]["text"] = tooling
        data["prs"]["Forge-AI/monorepo#53618"]["serves"] = "o1"
    url = "https://github.com/Forge-AI/monorepo/pull/53618"
    assert say(transcript, text="land these", tap_prs=[url]) == (
        f'refused: Forge-AI/monorepo#53618 serves a drive tooling order, so the line above its card quotes that order: put "{tooling}" in text'
    )
    assert say(transcript, text=f"_{tooling}_", tap_prs=[url]) is None


def test_a_block_drive_job_ask_keeps_posts_as_written_and_a_word_over_counts_alike_on_the_card_and_in_the_post(talker: str, transcript: Path) -> None:
    card = limits.question(STRAY_CHATS_ASK).card("demo", "demo:j585-7")
    assert posted(transcript, card=card) is None
    longer = [*card["options"][:2], card["options"][2].replace("costs time.", "costs time and money.")]
    assert limits.card_words(card | {"options": longer}) == limits.QUESTION_WORDS + 1
    assert posted(transcript, card=card | {"options": longer}).startswith("refused: this card is 151 words")


def test_a_block_starts_from_the_root_cause_and_names_the_option_that_removes_it() -> None:
    cause = "Root cause: both builds share one chat folder on his Mac, so the old build's own server writes chats there\n"
    shown = "New app moves its chats once into a folder of its own and lists unloaded chats as loading; the old build cannot reach them."
    with pytest.raises(DriveError, match="no `Root cause:` line under `Findings:`"):
        limits.question(STRAY_CHATS_ASK.replace(cause, ""))
    _, problems = limits.question_problems(STRAY_CHATS_ASK.replace(cause, "").replace(f"Simplest: {shown}\n", ""))
    assert [problem.split(":")[0] for problem in problems] == ["no `Simplest", "no `Root cause"]
    with pytest.raises(DriveError, match="has no `First principles:` line"):
        limits.question(STRAY_CHATS_ASK.replace(f"First principles: {shown}\n", ""))
    with pytest.raises(DriveError, match="`First principles: Wait for the copy` names no option"):
        limits.question(STRAY_CHATS_ASK.replace(f"First principles: {shown}", "First principles: Wait for the copy"))
    refuses = "New app refuses chats created without setup and lists unloaded chats as loading; smaller, but the old build still writes strays into the shared folder."
    assert limits.question(STRAY_CHATS_ASK.replace(f"First principles: {shown}", f"First principles: {refuses}")).recommended == shown
    with pytest.raises(DriveError, match="names no job finding it"):
        limits.question(STRAY_CHATS_ASK.replace(cause, "Root cause: not found: the old build's writes\n"))
    assert limits.question(STRAY_CHATS_ASK.replace(cause, "Root cause: not found: j12 reads his chat files\n")).recommended
    assert limits.question(STRAY_CHATS_ASK.replace(cause, "Root cause: none: a pick between two designs of his\n")).recommended
    assert limits.question(STRAY_CHATS_ASK.replace(f"First principles: {shown}", "First principles: Wait for the copy"), kept=True).recommended


def test_a_card_asking_only_when_a_merged_fix_ships_names_the_structure_it_removed_and_links_that_pr() -> None:
    cause = "Root cause: both builds share one chat folder on his Mac, so the old build's own server writes chats there\n"
    unprincipled = "\n".join(line for line in STRAY_CHATS_ASK.splitlines() if not line.startswith("First principles:")) + "\n"
    removed = "Root cause: fixed: both builds shared one chat folder on his Mac; removed by https://github.com/acme/app/pull/12\n"
    assert limits.question(unprincipled.replace(cause, removed)).recommended
    with pytest.raises(DriveError, match=re.escape("links no merged PR: write `Root cause: fixed: <the structure>, removed by <the merged PR's link>`")):
        limits.question(unprincipled.replace(cause, "Root cause: fixed: both builds shared one chat folder on his Mac\n"))


@pytest.mark.parametrize(
    ("cause", "form"),
    [
        ("Root cause: none: his approval of the exact benchmark command his rules hold for him\n", "none"),
        ("Root cause: fixed: both builds shared one chat folder on his Mac; removed by https://github.com/acme/app/pull/12\n", "fixed"),
        ("Root cause: not found: j12 reads his chat files\n", "found"),
        ("Root cause: both builds share one chat folder on his Mac, so the old build's own server writes chats there\n", "found"),
    ],
)
def test_jev_weighs_a_card_by_the_rule_for_the_form_of_its_root_cause_line(cause: str, form: str) -> None:
    stray = "Root cause: both builds share one chat folder on his Mac, so the old build's own server writes chats there\n"
    assert limits.rule_words(STRAY_CHATS_ASK.replace(stray, cause)) == [limits.ROOT_CAUSE_WORDS | {"rule": limits.ROOT_CAUSE_RULES[form]}, limits.TEAM_DATA_WORDS]


def test_the_case_jev_reads_carries_the_show_me_each_option_with_its_description_and_the_root_cause_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(picture, "page_text", lambda url: "The old build's chats")
    asked = limits.question(STRAY_CHATS_ASK)
    case = limits.ask_case(STRAY_CHATS_ASK, asked)
    assert case.startswith(f"Question: {asked.title}\n")
    assert "func Create(ctx context.Context, s Session, writer int64) (*DurableAgent, error) {" in case
    assert f"Option 1 (recommended): {asked.recommended}" in case
    assert [line.split(":")[0] for line in case.splitlines() if line.startswith(("Option ", "Root cause:", "First principles:", "Page "))] == [
        *(f"Option {number}{' (recommended)' if number == 1 else ''}" for number in range(1, len(asked.options) + 1)),
        "Root cause",
        "First principles",
        "Page https",
    ]


def test_the_pages_a_card_links_are_the_web_pages_its_summary_and_captions_link_once_each_never_a_signed_in_or_fenced_one() -> None:
    preview = "https://prototype.example.com/index.html?slack=pr"
    queue = "https://prototype.example.com/index.html#/queue"
    asked = limits.Question(
        title="Which view should the prototype open on?",
        summary=[f"<{preview}|The Slack preview> draws both cards, as {preview}. does; <https://github.com/acme/app/pull/12|the fix that adds it> is open."],
        show_me=[f"1 · The queue at {queue}, beside <{REPORTED}|his thread>:", "```sh", "curl https://fenced.example.com/never-read", "```"],
        options=["The Slack preview first", "The queue first"],
        recommended="The Slack preview first",
        hints={},
        holds="",
    )
    assert limits.linked_pages(asked) == [preview, queue]


def test_jev_reads_the_words_each_page_a_card_links_shows_or_why_chrome_could_not_open_it(monkeypatch: pytest.MonkeyPatch) -> None:
    shown = "Sign in to Sentry " * 200

    def page_text(url: str) -> str:
        if url == SENTRY_EVENT:
            return shown
        raise picture.ChromeError(f"Chrome could not load {url}: net::ERR_NAME_NOT_RESOLVED")

    monkeypatch.setattr(picture, "page_text", page_text)
    [line] = [line for line in limits.ask_case(STRAY_CHATS_ASK, limits.question(STRAY_CHATS_ASK)).splitlines() if line.startswith("Page ")]
    assert line.startswith(f"Page {SENTRY_EVENT} shows: Sign in to Sentry")
    assert len(line) <= len(f"Page {SENTRY_EVENT} shows: ") + limits.PAGE_CHARS
    missing = "https://prototype.example.com/index.html?slack=pr"
    linked = STRAY_CHATS_ASK.replace(f"<{SENTRY_EVENT}|The old build>", f"<{missing}|The old build>")
    assert limits.ask_case(linked, limits.question(linked)).endswith(f"\nPage {missing} did not open: Chrome could not load {missing}: net::ERR_NAME_NOT_RESOLVED")


def test_jev_weighs_a_page_line_by_his_words_on_a_link_that_opened_another_view() -> None:
    rulings = limits.rule_words(STRAY_CHATS_ASK)
    paged = f"{STRAY_CHATS_ASK}\nPage https://prototype.example.com/index.html shows: Priority queue Decision 1 of 72"
    assert limits.rule_words(paged) == [*rulings, limits.LINK_VIEW_WORDS]


def options_ask(*options: str) -> str:
    listed = "".join(f"- {option}\n" for option in options)
    return explained(f"ASK Which way should this go?\nDrive tooling: your pick decides it, so the work waits on it.\n{listed}Findings:\nRoot cause: none: a design pick\nSimplest: {limits.RECOMMENDED.sub('', options[0])}\nHolds: none: no option changes code any job builds\nKept on disk.\n")


def test_the_recommended_option_comes_first_in_a_new_block_and_a_kept_one_still_redraws() -> None:
    late = options_ask("copy in batches", "server-side copy (recommended)")
    with pytest.raises(DriveError, match='the recommended option "server-side copy" is not first: put it first, as the card draws it'):
        limits.question(late)
    assert limits.question(options_ask("server-side copy (recommended)", "copy in batches")).recommended == "server-side copy"
    assert limits.question(late, kept=True).recommended == "server-side copy"


@pytest.mark.parametrize(
    "named",
    [
        "branch durable-agents-j3700-scale-up-completed-minutes",
        "`durable-agents-j3700-scale-up-completed-minutes`",
        "scratch/j3700/raise.sh",
        "j3700.md",
        "the card durable-agents:j3665-1794586294029",
    ],
)
def test_an_id_inside_a_branch_path_file_or_waits_id_keeps_its_proper_name(named: str) -> None:
    described = options_ask("server-side copy (recommended)", "copy in batches").replace("`lib/drive/cli.py`", named, 1)
    assert limits.question_problems(described)[1] == []


@pytest.mark.parametrize(
    ("ask", "found"),
    [
        (options_ask("server-side copy (recommended)", "copy in batches").replace("`lib/drive/cli.py`", "branch j3700", 1), "j3700"),
        (options_ask("server-side copy (recommended)", "copy in batches").replace("`lib/drive/cli.py`", "`j3700`", 1), "j3700"),
        (options_ask("server-side copy (recommended)", "copy in batches").replace("`lib/drive/cli.py`", "j3700's branch", 1), "j3700"),
        (options_ask("server-side copy (recommended)", "copy in batches").replace("`lib/drive/cli.py`", "what j7 built", 1), "j7"),
        (options_ask("server-side copy (recommended)", "copy in batches").replace("Which way should this go?", "Which way should {order:o1} go?"), "o1"),
    ],
)
def test_a_bare_drive_id_is_refused_wherever_the_card_draws_it(ask: str, found: str) -> None:
    with pytest.raises(DriveError, match=f'"{found}" is a drive job or order id, which he cannot open'):
        limits.question(ask)


def test_an_option_asking_someone_to_retry_or_confirm_is_refused_naming_the_fix_to_offer() -> None:
    with pytest.raises(DriveError, match='option "ask Rowan to retry the restore" asks someone to retry or confirm "ask Rowan to retry", which fixes nothing: offer the fix itself'):
        limits.question(options_ask("restore from the replica (recommended)", "ask Rowan to retry the restore"))
    assert limits.question(options_ask("restore from the replica (recommended)", "restore from last night's backup")).options


@pytest.mark.parametrize(
    ("block", "found"),
    [
        (
            options_ask("Sign in: you run the AWS single sign-on login once; a break-glass read of the chat follows (recommended)", "Close on the fix"),
            'option "Sign in: you run the AWS single sign-on login once; a break-glass read of the chat follows" asks him to sign in with "Sign in"',
        ),
        (
            options_ask("You renew the identity-break-glass sign-in with Okta MFA; a job runs the registry GET for env dev (recommended)", "Close on the fix"),
            'asks him to sign in with "You renew the identity-break-glass sign-in"',
        ),
        (EXPIRED_SIGN_IN, 'the question asks him to sign in with "Renew"'),
        (EXPIRED_SIGN_IN, 'the summary asks him to sign in with "your MFA"'),
        (EXPIRED_SIGN_IN, 'asks him to sign in with "I signed in"'),
        (
            options_ask("Break-glass read: you open BreakGlassAccess with Okta MFA and run one read-only query per SandSQL server (recommended)", "Close on the fix"),
            'asks him to sign in with "you open BreakGlassAccess"',
        ),
        (options_ask("Break-glass read: you approve the Okta push for the grant, then a lane runs one query (recommended)", "Close on the fix"), 'with "you approve the Okta"'),
        (options_ask("Break-glass read: you run aws sso login, then one read-only query per server (recommended)", "Close on the fix"), 'with "you run aws sso login"'),
        (options_ask("Break-glass read: you renew this Mac's AWS sign-in in the browser, then a lane reads (recommended)", "Close on the fix"), 'with "you renew this Mac\'s AWS sign-in"'),
        (
            PASS_BOX_TAP,
            'option "On your tap here, the job starts the pass box and reads the key; you tap Touch ID once and get a results PR" asks him to'
            ' sign in with "you tap Touch ID"',
        ),
        (PASS_BOX_TAP, 'the summary asks him to sign in with "your Touch ID"'),
        (FIRST_PASS_BOX_TAP, 'with "I\'ll tap Touch ID"'),
        (options_ask("Key read: you approve the cc-sudo prompt, then the job reads the SSM parameter (recommended)", "Close on the fix"), 'with "you approve the cc-sudo"'),
        (options_ask("Tap Touch ID once: the job claims a devbox and proves the change on it (recommended)", "Close on the fix"), 'with "Tap Touch ID"'),
    ],
)
def test_a_card_asking_him_to_sign_in_or_tap_touch_id_for_an_aws_action_is_refused_naming_the_machine_profile_route(block: str, found: str) -> None:
    with pytest.raises(DriveError) as refused:
        limits.question(block)
    assert found in str(refused.value)
    assert (
        "this Mac's machine profile runs it with no sign-in, Touch ID or cc-sudo, every AWS call under a profile ~/.aws/config chains to the"
        " machine certificate, such as `AWS_PROFILE=plat-prod tools/ci pass-box launch`, and break-glass as `drive break-glass <job> --stack"
        ' <domain>/<env> --reason "<why>" -- <command>` from a lane on this Mac, so that run takes its place'
        f" ({limits.MACHINE_PROFILE_RULING})"
    ) in str(refused.value)


@pytest.mark.parametrize(
    "block",
    [
        options_ask(MACHINE_PROFILE_READ, "Close on the fix"),
        options_ask("Break-glass read: no Okta MFA or SSO sign-in, drive break-glass runs it under the machine profile (recommended)", "Close on the fix"),
        options_ask(MACHINE_PROFILE_READ, "Close on the fix").replace(
            "so the work waits on it.", 'his words "you dont fucking need okta MFA how many times have I told u to use machine profile" settle the grant.'
        ),
        options_ask("Users sign in with Okta on the dashboard as today (recommended)", "Close on the fix"),
        options_ask("The job starts the pass box under the machine profile, reads the key and opens a results PR (recommended)", "Close on the fix"),
        options_ask("You tap Touch ID once so cookiesync seeds the dashboard session for the recording (recommended)", "Close on the fix"),
    ],
)
def test_a_card_running_an_aws_action_on_the_machine_profile_quoting_his_words_or_naming_a_sign_in_off_aws_passes(block: str) -> None:
    assert limits.question(block).options


def test_an_option_ruling_out_a_retry_or_a_sweeper_is_no_option_proposing_one() -> None:
    assert limits.question(options_ask("restore from the replica; never ask Rowan to retry the restore (recommended)", "restore from last night's backup")).options
    assert limits.question(options_ask("restore from the replica; ask Rowan not to retry (recommended)", "restore from last night's backup")).options
    assert limits.question(options_ask("one owner holds the lease until it ends; remove the sweeper (recommended)", "the run gives the lease back as it exits")).options
    with pytest.raises(DriveError, match='cleans up a bad state with "sweep orphaned"'):
        limits.question(options_ask("remove the sweeper, then sweep orphaned leases hourly (recommended)", "one owner holds the lease until it ends"))


def test_an_option_that_cleans_up_a_bad_state_is_refused_unless_unsettled_names_his_ruling() -> None:
    swept = options_ask("sweep orphaned leases every 10 min (recommended)", "one owner holds the lease until it ends")
    with pytest.raises(DriveError) as refused:
        limits.question(swept)
    assert (
        'option "sweep orphaned leases every 10 min" cleans up a bad state with "sweep orphaned", a sweeper, time rule or reclaim'
        ' loop: design the state out so it cannot exist, such as one owner holding it until it ends, or keep the option with'
        ' `drive job ask <j> <the file holding this block> --unsettled "b89209a: <why the state cannot be designed out>"`, the one'
        " verb taking --unsettled, and for a reader's DESIGN run `drive job report <j> -` again after it, which keeps that"
        ' --unsettled (his ruling b89209a, "No sweepers")'
    ) in str(refused.value)
    with pytest.raises(DriveError, match='cleans up a bad state with "older than a minute"'):
        limits.question(options_ask("a placed row older than a minute counts as waiting (recommended)", "one owner holds the lease until it ends"))
    assert limits.question(swept, unsettled="b89209a: the lease lives in a store no owner can hold").recommended == "sweep orphaned leases every 10 min"
    assert limits.question(options_ask("one owner holds the lease until it ends (recommended)", "the run gives the lease back as it exits")).options


def test_a_post_naming_the_change_links_it_and_a_link_refusal_says_how_to_say_it_does_not_exist_yet(talker: str, transcript: Path) -> None:
    holds_pr("o1")
    assert posted(transcript, text="The change is live.").startswith(
        'refused: "The change" names something he could open with no link there, in "The change is live"; put those words in its'
        ' link, <url|The change>, so he opens it where it is named, or say in that clause it does not exist yet, as "its PR goes up next" does'
    )
    assert posted(transcript, text="<https://github.com/Forge-AI/monorepo/pull/53618|The unfurl fix> is live.") is None
    assert posted(transcript, text="<https://github.com/Forge-AI/monorepo/pull/53618|The change that fixes unfurls> is live; the change needs no tap.") is None
    assert posted(transcript, text="The first PR goes up next.") is None
    assert posted(transcript, text="The first PR does not exist yet.") is None
    assert posted(transcript, text="The change doesn't exist yet.") is None


def test_a_choice_on_a_bug_hurting_someone_now_names_the_immediate_fix_shipping_while_he_picks() -> None:
    assert "`Interim fix: none: <why nobody is hurt now>`" in limits.interim_fix_problems("Root cause: none: a design choice\n")[0]
    assert limits.interim_fix_problems("Interim fix: we will repair the stuck rows\n") != []
    for named in (
        "Interim fix: https://github.com/Forge-AI/monorepo/pull/43",
        "Interim fix: j12 repairs the stuck rows",
        "Interim fix: none: the bug blocks only a test env",
    ):
        assert limits.interim_fix_problems(f"Root cause: none: a design choice\n{named}\n") == []


def test_first_principles_names_an_option_whose_first_part_holds_a_double_colon() -> None:
    options = [
        "Also write the legacy content field as a blob::Blob: old readers keep working, one more write each save",
        "Drop the legacy field: no extra write, and old readers break",
    ]
    findings = "Root cause: old readers still open the legacy field\nFirst principles: Also write the legacy content field as a blob::Blob\nSimplest: Drop the legacy field\n"
    assert limits.root_cause_problems(findings, options) == []
    assert limits.option_head("Read https://example.com/a: the page says so") == "Read https://example.com/a"


def test_a_question_or_option_naming_a_mechanism_by_a_bare_word_is_refused_until_it_says_what_runs_and_what_reaches_whom() -> None:
    with pytest.raises(DriveError) as refused:
        limits.question(WATCH_ASK)
    for where, found in [
        ("the question", "or the first source of an event:: subscription API"),
        (f'option "{WATCH_OPTIONS[0]}"', "Polls only"),
        (f'option "{WATCH_OPTIONS[1]}"', "taking a Poll source first and pushed sources later"),
        (f'option "{WATCH_OPTIONS[1]}"', "named before the event system is designed"),
    ]:
        assert f'{where} names a mechanism by a bare word in "{found}"' in str(refused.value)
    assert f'option "{WATCH_OPTIONS[2]}" names a mechanism' not in str(refused.value)
    assert limits.question(WATCH_ASK, kept=True).recommended == WATCH_OPTIONS[0]
    concrete = {
        WATCH_QUESTION: "Should the builtin that reruns a check function and messages the agent on a change be named watch:: or event::?",
        WATCH_OPTIONS[0]: "watch:: the Mac polls the check function every 30 seconds; a changed output sends one inbox message that wakes the agent",
        WATCH_OPTIONS[1]: "event:: the same polling of the check function now, and later a run's end pushes one inbox message with no polling",
    }
    rewritten = WATCH_ASK
    for bare, said_plainly in concrete.items():
        rewritten = rewritten.replace(bare, said_plainly)
    assert limits.question(rewritten).recommended == concrete[WATCH_OPTIONS[0]]


DESCRIBED_ASK = """ASK Where does the one manager for SandSQL's helper processes run?
Drive tooling: your pick decides where helpers restart; every option gives each helper one start spec and one restart rule, and nothing ships meanwhile.
Sockets only touches clone.rs, clone/child.rs and `local/process.go`, as <https://github.com/acme/app/blob/main/clone.rs|clone.rs> reads today.
- Inside each parent: each parent starts and restarts its children from one shared spec (recommended)
  A new crate in rust/crates/sandsql-proc gives each parent process one spec to start its helpers from and restarts one that exits, called from clone.rs and local/process.go; each spec lives in code.
- Sockets only: helpers move to Unix sockets, and every parent keeps its own restart code
  Each helper listens on a Unix socket its parent hands it, changing clone.rs, clone/child.rs and local/process.go, while each parent keeps its own start and restart code.
Findings:
Root cause: none: a design pick, with no defect behind it
Simplest: Sockets only
Holds: none: no option changes code any job builds
Five parents hand-write their own start and restart code today.
"""
SOCKETS = "  Each helper listens on a Unix socket its parent hands it, changing clone.rs, clone/child.rs and local/process.go, while each parent keeps its own start and restart code."
FIELDS = "  Does: each helper listens on a Unix socket its parent hands it.\n  Data: none: only the transport changes.\n  Touches: clone.rs, clone/child.rs and local/process.go."


def test_each_option_of_a_new_block_carries_one_plain_text_description_which_its_card_draws_under_it(tmp_path: Path) -> None:
    card = limits.question(DESCRIBED_ASK).card("demo", "demo:j1-7")
    assert card["hints"] == {
        "Inside each parent: each parent starts and restarts its children from one shared spec": (
            "A new crate in `rust/crates/sandsql-proc` gives each parent process one spec to start its helpers from and restarts one that"
            " exits, called from `clone.rs` and `local/process.go`; each spec lives in code."
        ),
        "Sockets only: helpers move to Unix sockets, and every parent keeps its own restart code": (
            "Each helper listens on a Unix socket its parent hands it, changing `clone.rs`, `clone/child.rs` and `local/process.go`, while"
            " each parent keeps its own start and restart code."
        ),
    }
    assert limits.redrawn(limits.rebuilt(card, tmp_path)).card("demo", "demo:j1-7") == card


@pytest.mark.parametrize(
    ("description", "refusal"),
    [
        ("", 'option "Sockets only" has no description under it'),
        (FIELDS, 'option "Sockets only" has 3 lines under it; write them as one description'),
        ("  Does: each helper listens on a Unix socket. Touches: clone.rs.", 'option "Sockets only" opens a field with "Does:"'),
        ("  Each helper listens on a Unix socket its parent hands it. Cost: every parent keeps its own restart code.", 'opens a field with "Cost:"'),
        (f"  {said(61)}", 'option "Sockets only" has a description of 61 words; it is at most 60'),
    ],
)
def test_an_option_whose_description_is_missing_split_into_fields_or_too_long_is_refused(description: str, refusal: str) -> None:
    block = DESCRIBED_ASK.replace(f"{SOCKETS}\n", f"{description}\n" if description else "")
    with pytest.raises(DriveError) as refused:
        limits.question(block)
    assert refusal in str(refused.value)
    assert limits.DESCRIBED_RULING in str(refused.value)


def test_a_block_kept_with_fields_under_its_options_redraws_them_as_its_description() -> None:
    kept = limits.question(DESCRIBED_ASK.replace(SOCKETS, FIELDS), kept=True)
    assert kept.hints["Sockets only: helpers move to Unix sockets, and every parent keeps its own restart code"] == (
        "Does: each helper listens on a Unix socket its parent hands it.\nData: none: only the transport changes.\nTouches: clone.rs, clone/child.rs and local/process.go."
    )
    undescribed = "".join(line for line in DESCRIBED_ASK.splitlines(keepends=True) if not line.startswith("  "))
    assert limits.question(undescribed, kept=True).hints == {}


@pytest.mark.parametrize(
    ("line", "drawn"),
    [
        ("It touches clone.rs.", "It touches `clone.rs`."),
        ("primary.go's cleanup in ~/repos/x and rust/crates/sandsql-proc/", "`primary.go`'s cleanup in `~/repos/x` and `rust/crates/sandsql-proc/`"),
        ("`clone.rs` and <https://github.com/acme/app/blob/main/clone.rs|clone.rs>", "`clone.rs` and <https://github.com/acme/app/blob/main/clone.rs|clone.rs>"),
        ("![1 · Before, dev](/Users/me/pictures/demo-before.png)", "![1 · Before, dev](/Users/me/pictures/demo-before.png)"),
        ("```rust\n// called from clone.rs\n```", "```rust\n// called from clone.rs\n```"),
        ("stdin/stdout lines, 3/5 of them, and/or", "stdin/stdout lines, 3/5 of them, and/or"),
        ("| clone | sandsql-storage/src/clone.rs |", "| clone | `sandsql-storage/src/clone.rs` |"),
        ("the refs refs/cc-notes/* and src/*.ts", "the refs `refs/cc-notes/*` and `src/*.ts`"),
        ("authority-v3.db.reindex-building, authority-v1.db.lock and go.sum", "`authority-v3.db.reindex-building`, `authority-v1.db.lock` and `go.sum`"),
        ("internal/store/source_reindex.go:185 and clone.rs:105-111, at 6:32", "`internal/store/source_reindex.go:185` and `clone.rs:105-111`, at 6:32"),
        ("*see lib/x.py* in v0.76.10, e.g. at 1.5 min", "*see `lib/x.py`* in v0.76.10, e.g. at 1.5 min"),
        ("serve every caller of POST /cloud-agents/:to/inbox alike", "serve every caller of POST `/cloud-agents/:to/inbox` alike"),
        ("GET /v1/teams/:team/sessions/:id.", "GET `/v1/teams/:team/sessions/:id`."),
        ("users/:id/", "`users/:id/`"),
        ("/v1/{id}/runs and app/[slug]/page.tsx", "`/v1/{id}/runs` and `app/[slug]/page.tsx`"),
        ("s3://demo-bucket/teams/t1/", "`s3://demo-bucket/teams/t1/`"),
        ("localhost:8080/api/v1", "`localhost:8080/api/v1`"),
        ("see a/b: then and/or: x at 10:30/11:00 AM", "see a/b: then and/or: x at 10:30/11:00 AM"),
        ("(lib/x.py) and [lib/y.py]", "(`lib/x.py`) and [`lib/y.py`]"),
        ("POST `/cloud-agents/`:to/inbox", "POST `/cloud-agents/`:to/inbox"),
    ],
)
def test_a_card_draws_each_file_name_and_path_as_code_so_slack_links_none_of_them(line: str, drawn: str) -> None:
    assert limits.coded(line) == drawn
    assert limits.coded(drawn) == drawn


def test_a_cards_body_draws_clone_rs_as_code_beside_the_link_that_names_it() -> None:
    card = limits.question(DESCRIBED_ASK).card("demo", "demo:j1-7")
    assert card["body"].splitlines()[1] == (
        "Sockets only touches `clone.rs`, `clone/child.rs` and `local/process.go`, as <https://github.com/acme/app/blob/main/clone.rs|clone.rs> reads today."
    )


@pytest.mark.parametrize(
    ("line", "bare"),
    [
        ("no polling, it's event based", ["no polling", "it's event based"]),
        ("push-only, so the agent never waits", ["push-only"]),
        ("the Mac polls the check function every 30 seconds", []),
        ("the agent subscribes to run-settled events", []),
        ("GitHub pushes each merge to the listener", []),
        (WATCHDOG_OPTIONS[0], []),
        ("event::on takes a run id; self::alarm keeps its cron", []),
    ],
)
def test_a_mechanism_is_bare_only_when_its_clause_says_nothing_else_or_names_a_kind_of_thing(line: str, bare: list[str]) -> None:
    assert limits.bare_mechanisms(line) == bare


def test_the_keeper_and_the_hook_count_a_kept_block_alike_and_a_drive_card_carries_no_text_beside_it(talker: str, transcript: Path) -> None:
    asked = limits.question(FROZEN_COPIES_ASK)
    card = asked.card("demo", "demo:j620-660354")
    assert limits.card_words(card) == limits.card_words({"title": asked.title, "body": "\n".join(asked.summary), "options": asked.options}) == 149
    assert posted(transcript, card=card) is None
    assert posted(transcript, card=card, text="Question: what must prove each moved copy is whole before the 30 frozen copies are deleted?") == (
        "refused: this post's text is 16 words cc-slack draws atop the card, and a drive's question card carries only the words"
        " of the ASK block `drive job ask` kept and counted: post the same card with no text. A question card is posted exactly"
        " as `drive job card <its ask file> --waits demo:j620-660354` prints it, never put together by hand: fix its words in"
        " the ASK block and keep it again with `drive job ask`."
    )
    assert posted(transcript, card=card, text=asked.summary[0]) is None


@pytest.mark.parametrize(
    ("shown", "refusal"),
    [
        ("```\nGET journal/0001.ltx\n```", 'the Show me fence holding "GET journal/0001.ltx" names no language'),
        ("```text\nSand 0.142 is ready\n```", 'the Show me fence holding "Sand 0.142 is ready" names no language'),
        ('```compare\n{"options": [{"name": "Listener"}]}\n```', "the Show me ```compare fence compares fewer than two named options"),
        ('```chart\n{"mark": "bar",}\n```', "the Show me ```chart fence is not the JSON it draws from"),
        ("```chart\nmark: bar\n```", f"the Show me ```chart fence is not the JSON it draws from: Expecting value at its line 1, column 1; write a Vega-Lite spec of {limits.CHART_SHAPE}"),
        (
            "```compare\n| Reader | Attach | Route |\n|---|---|---|\n| views | unchanged | split |\n```",
            f"the Show me ```compare fence is not the JSON it draws from: Expecting value at its line 1, column 1; write {limits.COMPARE_SHAPE}, or put a markdown table outside any fence",
        ),
        ('```compare\n["Listener", "Poller"]\n```', f"the Show me ```compare fence holds JSON that is not an object; write {limits.COMPARE_SHAPE}"),
        ("```arch\nnodes: [drive, picture]\n```", f"the Show me ```arch fence is not the JSON it draws from: Expecting value at its line 1, column 1; write {limits.ARCH_SHAPE}"),
        ("", "the Show me holds no visual, only words"),
        ('```json\n{"type": "slack.message", "watch_id": 2816}\n```', 'the Show me ```json fence holding "{"type": "slack.message", "watch_id": 2816}" is JSON'),
        ("```yaml\nwatch: [C0822AHFY3G]\n```", "is YAML, raw data or tool output"),
        ("```console\n$ cc-slack subscribe --kind alerts\nALERT open monitor 315028134\n```", "is a terminal session"),
        ("```bash\n$ drive status\ndurable-agents: 180 open of 287 orders\n```", "is a terminal session"),
        ('```python\nTraceback (most recent call last):\n  File "cli.py", line 3, in main\n```', "is a stack trace"),
        ("```go\n2026-11-10T22:41:03Z replica lane unproven\n2026-11-10T22:41:04Z replica lane unproven\n```", "is log lines"),
        ('```js\n{"count": 384}\n```', "is JSON, raw data or tool output, and a picture of it shows him nothing: draw what it means instead"),
    ],
)
def test_a_show_me_of_words_or_of_a_visual_that_cannot_draw_is_refused_but_a_block_kept_earlier_still_reads(shown: str, refusal: str) -> None:
    block = WATCHDOG_ASK.replace(WATCHDOG_SHOW_ME, f"What each reading builds, beside what runs today.\n{shown}")
    with pytest.raises(DriveError, match=re.escape(refusal)):
        limits.question(block)
    assert limits.question(block, kept=True).show_me[0] == "What each reading builds, beside what runs today."


def test_a_real_call_draws_and_a_block_with_nothing_to_show_has_no_show_me() -> None:
    call = "The call each option makes.\n```bash\n$ drive status --json\n```"
    assert limits.question(WATCHDOG_ASK.replace(WATCHDOG_SHOW_ME, call)).show_me[0] == "The call each option makes."
    asked = limits.question(WATCHDOG_ASK.replace(f"Show me:\n{WATCHDOG_SHOW_ME}\n", ""))
    assert asked.show_me == []
    assert "*Show me*" not in asked.card("demo", "demo:j1-1")["body"]


@pytest.mark.parametrize(
    ("question", "option", "found"),
    [
        ("Staging's smoke fails a page check since [release 14345](https://buildkite.com/forge/release/builds/14345); fix it?", None, "[release 14345](https://buildkite.com/forge/release/builds/14345)"),
        ("Does <https://github.com/yasyf/cc-skills/pull/4189|the raw data refusal> cover logs?", None, "<https://github.com/yasyf/cc-skills/pull/4189|the raw data refusal>"),
        ("Should the *listener* land PRs?", None, "*listener*"),
        (None, "Run `drive listen` at login: one new process", "`drive listen`"),
    ],
)
def test_markdown_or_a_link_in_a_cards_title_or_an_option_is_refused_since_slack_draws_it_raw(question: str | None, option: str | None, found: str) -> None:
    block = WATCHDOG_ASK.replace(WATCHDOG_QUESTION, question or WATCHDOG_QUESTION).replace(WATCHDOG_OPTIONS[1], option or WATCHDOG_OPTIONS[1])
    for kept in (False, True):
        with pytest.raises(DriveError, match=re.escape(f'holds "{found}", which Slack draws as raw text')):
            limits.question(block, kept=kept)


def test_a_markdown_link_in_a_summary_is_refused_with_the_slack_link_to_write() -> None:
    linked = "[the first PR](https://github.com/Forge-AI/monorepo/pull/53519)"
    with pytest.raises(DriveError) as refused:
        limits.question(WATCHDOG_ASK.replace(f"<{HIS_PICK}|Your Nov 9 pick>", f"{linked}, beside your Nov 9 pick,"))
    assert (
        f'"{linked}" is a markdown link, and Slack draws no [label](url) link: write <https://github.com/Forge-AI/monorepo/pull/53519|the first PR>'
        in str(refused.value)
    )
    assert "names something he could open" not in str(refused.value)


def test_a_show_me_table_row_is_no_caption_and_runs_past_25_words() -> None:
    row = "| Hash by key | writes arrive but the file stays flat, or over half are upserts on one key | rows spread by key; a full part is cloned, each copy drops half |"
    limits.question(WATCHDOG_ASK.replace("| Hears a push |", f"{row}\n| Hears a push |"))


def test_a_picture_inside_a_show_me_sentence_is_refused_and_one_on_its_own_line_is_kept() -> None:
    picture = "![1 · The listener's one line per push](/Users/me/shots/listen.png)"
    listener = WATCHDOG_ASK.replace("The listener:", "1 · The listener:")
    limits.question(listener.replace("1 · The listener:", f"{picture}\n1 · The listener:"))
    with pytest.raises(DriveError, match="holds a picture inside a sentence or table, which cc-slack draws as text"):
        limits.question(listener.replace("1 · The listener:", f"It writes {picture} per push.\n1 · The listener:"))


def test_a_cards_show_me_is_drawn_whole_and_counts_against_no_limit(talker: str, transcript: Path) -> None:
    holds_pr("o1")
    card = {"title": said(10), "body": said(40), "options": [said(25), said(25), said(25)], "waits": "demo:j1-7"}
    assert posted(transcript, card=card | {"body": said(40) + "\n" + SHOW_ME}) is None
    assert "this card is 431 words" in posted(transcript, card=card | {"body": said(40) + "\n" + SHOW_ME.removeprefix("*Show me*\n")})
    assert "names something he could open" in posted(transcript, card=card | {"body": said(40) + "\n*Show me*\nThe PR, after:\n```\nx\n```"})
    assert posted(transcript, card=card | {"body": said(40) + "\n*Show me*\n```\nthe fix #41\n```"}) is None


@pytest.mark.parametrize(
    "command",
    [
        "python3 -c 'print(open(\"{saved}\").read()[-80000:])'",
        "cd {folder}/; python3 -I -c \"s=open('{name}').read(); print(s[-9000:])\"",
        "cd {folder}/ && python3 -I -c \"print(open('{name}').read()[-9000:])\"",
    ],
    ids=["full path", "handler 48a784aa", "handler 7997c216"],
)
def test_a_saved_thread_is_a_read_only_once_the_handler_opens_its_file(talker: str, unread: Path, command: str) -> None:
    lane = lane_of(unread)
    saved = "/Users/me/.claude/projects/p/s/tool-results/mcp-plugin_cc-slack_cc-slack-slack_thread-1.txt"
    command = command.format(saved=saved, folder=Path(saved).parent, name=Path(saved).name)
    at = datetime.now(UTC).isoformat()
    use = {"type": "tool_use", "id": "long", "name": message_limits.THREAD_READ, "input": {"channel_id": DM, "thread_ts": "1.2", "limit": 1000}}
    notice = (
        "Error: result (111,642 characters across 1 line) exceeds maximum allowed tokens. Output has been saved to"
        f" {saved}. Format: Plain text - For targeted searches (find a string): use grep on the file directly."
    )
    result = {"type": "tool_result", "tool_use_id": "long", "content": [{"type": "text", "text": notice}]}
    opened = {"type": "tool_use", "id": "open", "name": "Bash", "input": {"command": command}}
    printed = {"type": "tool_result", "tool_use_id": "open", "content": "the newest messages"}
    with lane.open("a") as transcript:
        for row in ({"type": "assistant", "message": {"content": [use]}}, {"type": "user", "message": {"content": [result]}}):
            transcript.write(json.dumps(row | {"timestamp": at}) + "\n")
    assert posted(unread, text="Fixed.", thread_ts="1.2").startswith(f"refused: your read of his thread 1.2 was saved to {saved}, which you have not opened since, so")
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
    assert say(transcript, edit=link(EDITED), text="See 41 then.").startswith("refused: `cc-slack check-post` finds this")
    share = call("slack_reply", transcript, channel_id="C0B75AL4XEW", thread_ts="1.2", text="See #41 there.")
    assert message_limits.deny_reason(share) is None
    assert checked.texts == ["See 41 now.", "See 41 then."]
    checked.findings = []
    assert posted(transcript, text="See <https://github.com/yasyf/cc-skills/pull/41|the fix> now.", thread_ts="1.2") is None


def test_a_post_check_post_cannot_check_does_not_go_out(talker: str, transcript: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(text: str, asked: list[str]) -> list[str]:
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
    assert CHECK_POST("See #41 now.", []) == ["1:5 bare-number '#41': link it"]
    assert (tmp_path / "words").read_text() == "See #41 now."
    launcher.write_text("#!/bin/sh\necho 'usage: check-post <file|->' >&2\nexit 2\n")
    with pytest.raises(slack.SlackError, match="usage: check-post"):
        CHECK_POST("See #41 now.", [])


def test_check_post_passes_each_message_he_asked_and_refuses_an_exit_that_names_no_finding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    launcher = tmp_path / "cc-slack"
    launcher.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" > '{tmp_path / 'argv'}'\n/bin/cat > /dev/null\n")
    launcher.chmod(0o755)
    monkeypatch.setattr(slack, "launcher", lambda: str(launcher))
    assert CHECK_POST("The drive has two PRs left.", ["how far along is the drive?", "- and the deploy?"]) == []
    assert (tmp_path / "argv").read_text().splitlines() == ["check-post", "-", "--asked=how far along is the drive?", "--asked=- and the deploy?"]
    launcher.write_text("#!/bin/sh\necho 'Error: unknown flag: --asked' >&2\nexit 1\n")
    with pytest.raises(slack.SlackError, match="unknown flag: --asked"):
        CHECK_POST("The drive has two PRs left.", ["how far along is the drive?"])


def test_a_post_answering_him_is_checked_with_each_message_of_his_it_quotes(talker: str, transcript: Path, checked: Checked) -> None:
    assert posted(transcript, text="It has two merges left.", thread_ts="1.2", asked=["how far along is the drive?"]) is None
    assert posted(transcript, text="Merged.", thread_ts="1.2") is None
    assert checked.asked == [["how far along is the drive?"], []]


def test_a_reply_in_his_thread_is_checked_as_the_answer_to_his_messages_since_the_bots_last_post(
    talker: str, transcript: Path, checked: Checked, slack_thread: Thread
) -> None:
    slack_thread.messages += [
        {"ts": "1794479000.000100", "user": "U1", "text": "what? just put it on new tree only"},
        {"ts": "1794479100.000100", "from_bot": True, "text": "Option 1 builds it on the new tree only."},
        {"ts": "1794479200.000100", "user": "U1", "text": "sot hat was asnwet 1 right"},
    ]
    reads(lane_of(transcript), "1794479000.000100")
    ack = "Checking whether option 1 on that card is the new-tree-only one, and the answer comes in this thread."
    assert posted(transcript, text=ack, thread_ts="1794479000.000100") is None
    assert posted(transcript, text=ack, thread_ts="1794479000.000100", asked="the release?") is None
    assert checked.asked == [["sot hat was asnwet 1 right"], ["the release?", "sot hat was asnwet 1 right"]]
    assert [word["detail"] for word in message_limits.thread_words(slack_thread.messages)] == [
        "his message in the thread this post goes to",
        message_limits.ANSWERED,
    ]


def test_main_prints_a_pretooluse_denial(
    talker: str, transcript: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(call("slack_dm_status", transcript, text=said(900)))))
    message_limits.main()
    decision = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert decision["hookEventName"] == "PreToolUse"
    assert decision["permissionDecision"] == "deny"
    assert "this post is 900 words" in decision["permissionDecisionReason"]


def test_a_check_that_fails_refuses_the_post_rather_than_let_it_out_unchecked(
    talker: str, transcript: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(payload: dict) -> tuple[str | None, dict]:
        raise KeyError("timestamp")

    monkeypatch.setattr(message_limits, "checked", broken)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(call("slack_dm_status", transcript, text="Fixed."))))
    message_limits.main()
    decision = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert decision["permissionDecisionReason"].startswith("refused: the drive's message check failed on this post with KeyError: 'timestamp'")


def test_the_meta_session_posts_nothing_to_slack_and_sends_its_words_and_questions_to_the_communicator(
    demo: str, run: Callable[..., tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "tooler")
    run("start", demo, "--role", "meta")
    said = {"session_id": "tooler", "tool_name": message_limits.SAY, "tool_input": {"text": "The watch hook is fixed."}}
    assert message_limits.deny_reason(said) == (
        "Refused on purpose: the meta session posts nothing to Slack, since his reply under a post reaches only the session that"
        " posted it. Send the words as a `report` record to communicator@demo, and its handler posts them; a question for him is"
        " a job's ASK, kept with `drive job ask`, which draws his card."
    )


def test_the_coordinator_posts_nothing_to_slack_and_sends_its_words_to_the_communicator(demo: str) -> None:
    said = {"session_id": os.environ["CLAUDE_CODE_SESSION_ID"], "tool_name": message_limits.SAY, "tool_input": {"text": "Merged."}}
    assert message_limits.deny_reason(said).startswith("Refused on purpose: the coordinator posts nothing to Slack")
    assert "as a `report` record to communicator@demo" in message_limits.deny_reason(said)
    reply = said | {"tool_name": message_limits.SLACK + "slack_reply", "tool_input": {"channel_id": "C9", "thread_ts": "1.2", "text": "x"}}
    assert message_limits.deny_reason(reply).startswith("Refused on purpose")
    assert message_limits.deny_reason(said | {"agent_id": "a-1"}) is None
    assert message_limits.deny_reason(said | {"session_id": "someone-else"}) is None


def test_the_communicators_ack_is_one_sentence_and_the_rest_goes_in_the_handlers_brief(talker: str, transcript: Path) -> None:
    ack = call("slack_dm_status", transcript, thread_ts="1.2", text="Got it. Starting a handler. It will read the thread first.")
    del ack["agent_id"]
    reason = message_limits.deny_reason(ack)
    assert reason.startswith("refused: this ack is 3 sentences, and an ack is one")
    assert "put the rest in the handler's brief" in reason
    for one in ("Starting a handler to answer why the card redrew.", 'Starting a handler on "the card. It redraws."'):
        assert message_limits.deny_reason(ack | {"tool_input": {"thread_ts": "1.2", "text": one}}) is None
    assert posted(transcript, text="Got it. The fix merged.") is None


def test_a_say_naming_an_alert_orders_thread_with_no_channel_id_is_told_its_channel_since_his_dm_holds_no_such_thread(run: Run, talker: str, transcript: Path) -> None:
    alert = "1794493064.110609"
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "the release failed", "--from", f"slack:C0822AHFY3G/{alert}")
    assert state.promise_thread(state.load("demo")["orders"]["o1"]) == ("C0822AHFY3G", alert)
    assert say(transcript, text="The fix merged.", thread_ts=alert) == f"nothing posted: thread {alert} is in channel C0822AHFY3G, not his DM, so pass channel_id C0822AHFY3G with it"
    assert message_limits.posted_order(state.load("demo"), {"channel_id": "C0822AHFY3G", "thread_ts": alert}, None) == "o1"
    assert message_limits.posted_order(state.load("demo"), {"thread_ts": alert}, None) is None


def test_while_he_is_away_only_cards_incident_lines_and_answers_go_out(run: Run, talker: str, transcript: Path) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    with state.edit("demo") as data:
        data["owner"] = {"heard": state.stamp(), "away": True}
        data["orders"]["o1"].update(thread=[DM, "1.2"], incident=True)
    reason = posted(transcript, text="The fix merged.")
    assert reason.startswith("Refused on purpose: he is away")
    assert "joins the catch-up the drive posts when he is back" in reason
    card = {"title": said(10), "body": said(40), "options": ["Land it", "Hold it"], "waits": "demo:j1-7"}
    assert posted(transcript, card=card) is None
    assert posted(transcript, text="Users are unblocked.", thread_ts="1.2") is None
    assert posted(transcript, text="The fix merged.", asked="did it merge?") is None
    with state.edit("demo") as data:
        data["owner"]["away"] = False
    assert posted(transcript, text="The fix merged.") is None


def test_the_rule_jev_weighs_a_line_held_while_he_is_away_by_names_the_drives_away_mark_and_his_words_that_the_line_waits(run: Run, talker: str, transcript: Path) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    with state.edit("demo") as data:
        data["owner"] = {"heard": state.stamp(), "away": True}
    rule = agents.ruled(posted(transcript, text="The fix merged."))["rule"]
    assert rule.startswith("He is away when the drive marks him so, as it has for this post,")
    assert "while he is away a line to him waits for his catch-up" in rule
    assert '"Just do less realtime more summary / batching, with a catch up post once I\'m back"' in rule
    assert "news that work moved, such as a fix merging, waits even when that work is his order" in rule


def test_an_incident_report_keeps_its_alert_links_bare_by_the_incident_its_order_or_record_marks(
    run: Run, talker: str, transcript: Path, checked: Checked, slack_thread: Thread, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "unblock the users")
    with state.edit("demo") as data:
        data["orders"]["o1"].update(thread=[DM, "1.2"], incident=True)
    monkeypatch.setattr(
        message_limits.cli.reads,
        "run",
        lambda argv, checkout: json.dumps({"seq": argv[2], "kind": "report", "lane": "root", "at": state.stamp(), "fields": {"attrs": {"order": "o1"}}}),
    )
    alert, issue, pr = "https://acme.slack.com/archives/C0ALERTS/p1794485000000100", "https://app.datadoghq.com/error-tracking/issue/abc", "https://github.com/acme/app/pull/7"
    line = f"The sandsql alert {alert} fired on {issue}."
    checked.findings = [f"1:{line.index(url) + 1} bare-url {url!r}: wrap it as <url|short label>" for url in (alert, issue)]
    assert posted(transcript, text=line, thread_ts="1.2") is None
    assert say(transcript, text=line, record="672890") is None
    assert posted(transcript, text=line).splitlines()[1:] == checked.findings
    checked.findings.append(f"1:1 bare-url {pr!r}: wrap it as <url|short label>")
    assert posted(transcript, text=line, thread_ts="1.2").splitlines()[1:] == checked.findings[2:]



def test_a_107_word_line_judged_a_summary_by_its_handler_still_refuses_when_jev_cannot_finish(talker: str, transcript: Path) -> None:
    reason = say(transcript, text=f"Fixed the journal reader and the release. {said(100)}", thread_ts="1.2")
    assert "this post is 107 words, and a line in his thread is at most 60 words" in reason.counted
    assert agents.ruled(reason) == {"rule": message_limits.LENGTH_RULE, "counted": reason.counted}


def test_his_messages_a_handler_answers_in_his_thread_record_him_heard_never_back_so_he_reads_away_an_hour_after_the_last(talker: str, transcript: Path, slack_thread: Thread) -> None:
    with state.edit("demo") as data:
        data["owner"] = {"heard": "2026-11-12T18:26:35-08:00", "away": False}
    merge, status = datetime.fromisoformat("2026-11-12T18:40:27-08:00"), datetime.fromisoformat("2026-11-12T18:47:37-08:00")
    asked = "Anything approved already get merger asap and list the rest for me here"
    slack_thread.messages.append({"ts": f"{merge.timestamp():.6f}", "user": "U1", "text": asked})
    say(transcript, text="Two approved PRs are queued.", thread_ts="1.2", asked=asked)
    assert state.parse_time(state.load("demo")["owner"]["heard"]) == merge
    slack_thread.messages += [{"ts": f"{merge.timestamp() + 144:.6f}", "from_bot": True, "text": "Two approved PRs are queued."}, {"ts": f"{status.timestamp():.6f}", "user": "U1", "text": "Status?"}]
    say(transcript, text="Both are in the merge queue.", thread_ts="1.2", asked="Status?")
    data = state.load("demo")
    assert state.parse_time(data["owner"]["heard"]) == status
    assert not state.owner_away(data, datetime.fromisoformat("2026-11-12T19:31:01-08:00"))
    assert state.owner_away(data, datetime.fromisoformat("2026-11-12T19:47:37-08:00"))
    later = datetime.fromisoformat("2026-11-12T19:10:00-08:00")
    with state.edit("demo") as data:
        data["owner"]["heard"] = state.stamp(later)
    say(transcript, text="Both are in the merge queue.", thread_ts="1.2", asked="Status?")
    assert state.parse_time(state.load("demo")["owner"]["heard"]) == later

def test_a_report_held_while_he_is_away_is_marked_held_and_its_coordinator_told_once(
    run: Run, talker: str, transcript: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "unblock the users")
    with state.edit("demo") as data:
        data["owner"] = {"heard": state.stamp(), "away": True}
        data["orders"]["o2"]["incident"] = True
    told = []

    def cci(argv: list[str], checkout: Path, env: dict | None = None) -> str:
        if argv[1] == "show":
            attrs = {"700": {"order": "o1"}, "710": {"order": "o2"}, "720": {"order": "o1", "incident": "true"}}[argv[2]]
            return json.dumps({"seq": argv[2], "kind": "report", "lane": "root", "at": state.stamp(), "fields": {"attrs": attrs}})
        told.append(argv)
        return json.dumps({"seq": 701})

    monkeypatch.setattr(message_limits.cli.reads, "run", cci)
    reason = say(transcript, text="The fix merged.", record="700")
    assert reason.startswith("Refused on purpose: he is away")
    assert agents.ruled(reason) == {"rule": message_limits.AWAY_RULE, "counted": reason}
    assert reason.endswith(
        "Report 700 is marked held: `drive status` lists it until the catch-up posts, and record 701 told its writer so."
        " Return NOTHING held for his catch-up."
    )
    assert say(transcript, text="The fix merged.", record="700") == reason
    assert told == [
        ["cci", "post", "--drive", "demo", "--lane", "communicator", "--kind", "refused", "--re", "700", "--to", "root@demo", "--id", "held-700"]
        + ["--attr", "held=700", "--attr", "order=o1", "--json", "--text", talk.HELD.format(seq="700")]
    ]
    assert {seq: (mark["order"], mark["told"]) for seq, mark in state.load("demo")["held"].items()} == {"700": ("o1", "701")}
    assert say(transcript, text="Users are unblocked.", record="710") is None
    assert say(transcript, text="The sandsql outage is mitigated.", record="720") is None
    assert list(state.load("demo")["held"]) == ["700"]


def test_a_handlers_own_session_hands_jev_none_of_its_brief_as_his_words_and_his_own_message_still_posts(
    talker: str, tmp_path: Path, slack_thread: Thread, records: dict[str, str]
) -> None:
    records["670054"] = "POST resume works from any session\no598 has no thread, so the line goes at his DM root."
    own = tmp_path / "handler.jsonl"
    handler(own, RELAY_BRIEF, "1.2")
    with state.edit("demo") as data:
        data["owner"] = {"heard": state.stamp(), "away": True}
        data["handlers"]["f796ccf3"] = {"session": "handler-session", "started": state.stamp(), "holds": None, "handed": []}
    relayed = {"session_id": "handler-session", "transcript_path": str(own), "agent_type": "long-running:handler", "tool_name": message_limits.SAY}
    root = agents.seen(relayed | {"tool_input": {"text": "Resume works from any session now."}})
    assert message_limits.deny_reason(root).startswith("Refused on purpose: he is away")
    assert message_limits.his_words(root) == []
    his(slack_thread, "tell me when resume works from the meta session")
    answer = agents.seen(relayed | {"tool_input": {"text": "Resume works from the meta session now.", "thread_ts": "1.2"}})
    assert [(word["id"], word["quote"]) for word in message_limits.his_words(answer)] == [("slack:1794310000.000000", "tell me when resume works from the meta session")]
    assert message_limits.deny_reason(answer | {"tool_input": answer["tool_input"] | {"asked": "tell me when resume works from the meta session"}}) is None


def test_a_say_code_visual_of_raw_data_is_refused_and_a_short_snippet_goes_out(talker: str, transcript: Path) -> None:
    raw = {"kind": "code", "language": "json", "source": '{"time": "2026-11-10T03:15:00Z", "bytes": 0}'}
    reason = say(transcript, text="The upload stalled.", visuals=[raw])
    assert reason.startswith('refused: the code visual holding "{"time"')
    assert "raw data or tool output" in reason
    assert "show a short snippet of real code" in reason
    snippet = {"kind": "code", "language": "go", "source": "func ready(db *DB) bool {\n\treturn db.Uploaded() > 0\n}"}
    assert say(transcript, text="The upload stalled.", visuals=[snippet]) is None


def test_an_edit_of_his_question_card_is_refused_naming_the_redraw(run: Run, talker: str, transcript: Path) -> None:
    run("order", "add", "--title", "Short title", "--project", "The drive", "--workstream", "Drive tooling", "ship the fix")
    run("job", "add", "builder", "--order", "o1", "--goal", "fix it")
    card = f"https://acme.slack.com/archives/{DM}/p1794288799976279"
    with state.edit("demo") as data:
        question = state.new_question("/drive/asks/j1-ab.md", "Which store?", state.stamp(), card=card)
        data["jobs"]["j1"]["questions"].append(question | {"waits": "demo:j1-661229"})
    for edit in (call("slack_edit", transcript, text="Reworded.", url=card), call("slack_edit", transcript, text="Reworded.", channel_id=DM, ts="1794288799.976279")):
        reason = message_limits.deny_reason(edit)
        assert reason.startswith("Refused on purpose: that message is his question card")
        assert "`drive job card /drive/asks/j1-ab.md --waits demo:j1-661229`" in reason
    assert say(transcript, edit=card, text="Reworded.").startswith("Refused on purpose: that message is his question card")
    assert say(transcript, edit=link("1794288799.000001"), text="Reworded.") is None


def test_a_write_to_his_dm_goes_through_say_so_each_reference_is_linked_and_the_dashboard_shows_it(talker: str, transcript: Path) -> None:
    reason = message_limits.deny_reason(call("slack_edit", transcript, url=link(EDITED), text="Reworded."))
    assert reason.startswith("Refused on purpose: slack_edit sends each {card:…}, {pr:…}, {order:…}, {record:…} or {answer:…} reference to him as raw text")
    assert "say(edit=<its permalink>, text=<the new words>)" in reason
    assert reason.rule == message_limits.SAY_RULE
    reason = message_limits.deny_reason(call("slack_reply", transcript, channel_id=DM, thread_ts=BAD_STATE_THREAD, text="See {pr:7}."))
    assert reason.startswith("Refused on purpose: {pr:7} would reach him as raw text, since only say links a drive reference.")
    assert message_limits.deny_reason(call("slack_reply", transcript, channel_id="C0B75AL4XEW", thread_ts="1.2", text="See {pr:7}.")) is None
    alert = call("slack_edit", transcript, url="https://acme.slack.com/archives/C0B75AL4XEW/p1794288799000003?thread_ts=1794288700.000100", text="Reworded.")
    assert message_limits.deny_reason(alert) is None
    state.append_event("demo", "said", text="The release failed.", channel="C0B75AL4XEW", thread="1794288700.000100", url=alert["tool_input"]["url"], card=None, order=None, visuals=[], prs=[], edited=False)
    assert message_limits.deny_reason(alert).startswith("Refused on purpose: slack_edit sends each {card:…}")
    assert message_limits.deny_reason(call("slack_edit", transcript, channel_id="C0B75AL4XEW", ts="1794288799.000003", text="Reworded.")).startswith("Refused on purpose: slack_edit")


def test_a_file_posted_to_his_dm_outside_say_is_refused_naming_say_visuals(talker: str, transcript: Path) -> None:
    recording = "/drive/scratch/j1/after.mov"
    for tool, where in (("slack_reply", {"channel_id": DM, "thread_ts": BAD_STATE_THREAD}), ("slack_dm_status", {"thread_ts": BAD_STATE_THREAD})):
        reason = message_limits.deny_reason(call(tool, transcript, text="The recording of the fix.", files=[recording], **where))
        assert reason.startswith(f"Refused on purpose: a file {tool} posts to him reaches Slack alone and never the dashboard.")
        assert 'say(text=…, visuals=[{"kind": "image", "source": "<its absolute path>"' in reason
        assert isinstance(reason, agents.Unjudged)
    assert message_limits.deny_reason(call("slack_reply", transcript, channel_id="C0B75AL4XEW", thread_ts="1.2", text="The recording.", files=[recording])) is None
    assert say(transcript, text="The recording of the fix.", visuals=[{"kind": "image", "source": recording, "caption": "The fix (90 s)"}]) is None


def test_a_switch_command_names_the_terminal_drive_status_prints(talker: str, transcript: Path) -> None:
    with state.edit("demo") as data:
        data["roles"]["communicator"]["terminal"] = "term_live"
    reason = posted(transcript, text="Open it with `orca terminal switch --terminal term_old`.")
    assert reason.startswith("refused: `orca terminal switch --terminal term_old` names a terminal no session of this drive runs in")
    assert "as the role's line `drive status` prints it" in reason
    assert posted(transcript, text="Open it with `orca terminal switch --terminal term_live`.") is None
    with state.edit("demo") as data:
        data["subcoordinators"]["Iris"] = {"session": "iris-1", "terminal": "term_iris"}
    assert posted(transcript, text="Open Iris's coordinator with `orca terminal switch --terminal term_iris`.") is None
