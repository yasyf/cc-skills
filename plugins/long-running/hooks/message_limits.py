import html
import itertools
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, cli, install, limits, mcp, picture, refs, render, slack, state, talk, tools, view, watch
from drive.state import DriveError

SLACK = "mcp__plugin_cc-slack_cc-slack__"
SAY = "mcp__plugin_long-running_drive__say"
DM_POSTS = {SLACK + "slack_dm_status", SAY}
EDIT = SLACK + "slack_edit"
THREAD_READ = SLACK + "slack_thread"
READ_WINDOW = timedelta(minutes=10)
CHANNEL_POSTS = {SLACK + "slack_reply", SLACK + "slack_send", EDIT}
PERMALINK_CHANNEL = re.compile(r"/archives/([A-Z0-9]+)")
SPACE = re.compile(r"\s+")
BETWEEN_QUOTES = re.compile(r"…|\.{3}|\n|\s/\s")
QUOTE_MARKS = "\"'“”‘’"
STRAIGHT = str.maketrans("‘’“”", "''\"\"")
PLACEHOLDER = re.compile(r"\$\([^()]*\)|\$\{[^{}]*\}|<(?![@#!]|\w+:)[^>|]*>")
SAVED = re.compile(r"\A(?:<persisted-output>\n)?[^\n]*?(?:Output has been saved to|Full output saved to:) (/\S+?)\.?(?=\s|$)")
ALERT_LINK = re.compile(r"^\d+:\d+ bare-url 'https://(?:[\w-]+\.slack\.com/archives/|(?:[\w-]+\.)?sentry\.io/|(?:[\w-]+\.)?datadoghq\.com/)")
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n+")
SWITCH = re.compile(r"orca terminal switch --terminal[= ]([^\s`'\"<>|]+)")
CARD_ANSWER = re.compile(r"✓ ([^\n]+)")
CARD_ANSWERED = "his answer on a card"
EXPLAIN_TAP = re.compile(r"\*You asked for more at [^*\n]+\.\*")
EXPLAIN_MORE = "Explain more"
EXPLAIN_TAPPED = "his Explain more tap on a card, asking for the reply that explains it"
COORDINATOR = re.compile(r"\bcoordinator\b", re.IGNORECASE)
DOWN = re.compile(
    r"\b(?:is|was|went|has been|had been|seems?|looks?)\s+(?:down|dead|gone|offline|unresponsive)\b|\b(?:crashed|exited|died|not running|stopped responding)\b",
    re.IGNORECASE,
)
RESTARTED = re.compile(r"\brestart", re.IGNORECASE)
PROMISED_START = re.compile(
    r"\b(?:job|builder|reader|agent|lane|release)\b[^.;\n]*?\bbeing (?:started|restarted|resumed|relaunched|put on)\b"
    r"|\b(?:is|are) (?:re)?(?:starting|resuming) (?:a|an|the|its) (?:\w+ )?(?:job|builder|reader)\b"
    r"|\basked (?:the )?(?:\w+ )?(?:coordinator|meta session|session)\b[^.;\n]*?\bto (?:start|restart|resume|rerun)\b"
    r"|\bwas asked for a (?:job|builder|reader)\b",
    re.IGNORECASE,
)
PROMISE_RULING = 'his words b4b64f5 "fix your toolking so tracking this stuff beciomes deterministic and it cant get lost at the whims of an agent"'
DOWN_RULING = "his Nov 8 ruling: \"The coordinator is down? Then why the fuck haven't you restarted it?\" and \"It's unacceptable to just say it's down\""
WAITING = "WAITING-ON-HIM"
AWAY_RULE = (
    "He is away when the drive marks him so, as it has for this post, from his saying so or an hour with no word or tap of his;"
    ' while he is away a line to him waits for his catch-up, by his words "Just do less realtime more summary / batching, with a'
    " catch up post once I'm back\": only a card, a line saying what waits on him, an incident line or the answer to a question"
    " of his goes out, and news that work moved, such as a fix merging, waits even when that work is his order."
)
REPORTER_RULE = (
    "The thread someone else reported a bug or asked him a question in hears from the drive once: that the bug is fixed once its fix"
    " is proven live, or the answer once it is posted to him, by his words \"we have authorization to message once in bug fix threads"
    ' after we ship the fix".'
)
TITLED = (
    " {} of the words counted are the titles its {{card:…}}, {{pr:…}}, {{order:…}}, {{record:…}} and {{answer:…}} references draw as their words;"
    " {{pr:<number>|<words>}} or {{record:<seq>|<words>}} names one in fewer words of your own."
)
UNREAD = (
    "refused: {why}, so this line could miss what he said there since. Read it with slack_thread(channel_id={channel},"
    " thread_ts={thread}, since_ts=<the newest ts you read there>), or limit=1000 in place of since_ts for a thread you never read, then post."
    " When the tool saves a long thread to a file, the call is not a read until you open that file and read its newest messages."
)
DRIVE_ID = re.compile(r"^\d+:\d+ drive-term '[oj]\d+'")
CAUSE_RULE = (
    "State a cause, a number or a state only through the read that measured it, as {record:<seq>} on the same line or, in a handler's post, in claims beside that read: on"
    ' Nov 10 a line at 8:46 PM blamed "the change that started the failures" without naming it, and a card he tapped at'
    ' 9:49 PM carried a false "disk full". It covers why a system, tool, run or session did something, or a number or state one'
    " measured, a cause the session worked out from code, logs or a trace included, never the session's own act or mistake told in"
    " its first person, nor an alert or error he shared, restated."
)
LENGTH_RULE = (
    "A line in his thread is at most 60 words and a summary of two or more features at most 120, the coordinator's reading"
    ' of his words "your messages need to be way shorter rewrite this for a human"; an answer to a question or order of his'
    " in that thread, his words passed as asked, goes out whole at any length. His words are the evidence items holding them, never"
    " the agent's brief or the records it reads, whatever they quote, and a count counts the post as Slack draws it, each"
    " reference's drawn words included."
)
ACK_RULE = "The communicator's ack is one sentence: what he asked, in his words, and what happens next; its handler answers the rest."
LINK_RULE = (
    "Link everything he could open where it is named, a PR, a teammate's fix, a card, a release, a build, an alert, a thread"
    " or an earlier decision, so he never has to look it up himself."
)
KNOWN_RULE = (
    "A post naming a PR or an error one of his open question cards already asks about links that card, so he reads a failure he"
    " was already told about as that card's, never as a new one."
)
OWED_TAP_RULE = "Ask for a tap only when one is owed: when he tapped a PR already, or it merged or closed, say what happened to it instead."
CARD_RULE = 'Anything that says it waits on him carries the card with its buttons, by his words "Anything that says waiting on me you always need to embed the card with the buttons".'
UNCARDED = (
    "refused: this line says something waits on him, and no open card it links asks it, where a PR's tap card carries no line"
    " waiting on his pick, answer, call or decision: link the card that asks it as {card:<its waits id>}; for a pick no card asks yet, send one"
    " `record` call, kind `handoff`, the pick as `text` and `attrs: {order: <the order it serves>}`, so the session holding that"
    " order has its job draw the card with `drive job ask`, then post this line linking that card:\n"
)
SWITCH_RULE = "A switch command he is given is the role's line `drive status` prints, word for word, naming a terminal a session of the drive runs in."
ORDERED_RULE = (
    "His message is the order, so no line answers it by a plan, record or answer of his that is missing, since his records are"
    ' read only so he is never asked again, by his words "Who cares if you have a record if they or not I\'m telling you to do it".'
)
TERM_RULE = (
    "A word he corrected names that thing no more: the post uses the proper term `drive term` keeps beside it, by his words"
    ' "use the proper fucking terms for things".'
)
TOOLING_RULE = "The line above a PR card quotes the order its PRs serve: his words, or a drive tooling order's \"Drive tooling: …\" words."
PROMISE_RULE = (
    "A line telling him a job is being started names that job once it started, or goes out only once the handoff asking for it is kept on the order"
    f" it goes to, which the watch owes the thread a line on five quiet minutes later until a job of that order starts, by {PROMISE_RULING}; a line"
    " saying a job already at work goes on, or a release or step that is no job's start, promises no such start."
)
DOWN_RULE = f"A coordinator that is down is restarted that minute, and he hears it was restarted, never only that it is down, by {DOWN_RULING}."
SAY_RULE = (
    "Every message to him in his DM, a new post or an edit of one, goes through say, the one call that links its references"
    ' and shows it on the dashboard too, by his words "it shoulkd be one tool call to a lr mcp tool that formats and sends to'
    ' both slack and dashbord".'
)
NO_READ = "your transcript shows no read of his thread {thread}, and a read by another agent or session does not count for yours"
OLD_READ = "your last read of his thread {thread} was {minutes} minutes ago, and a read counts for 10"
FAILED_READ = "your slack_thread call on his thread {thread} failed"
UNOPENED_READ = "your read of his thread {thread} was saved to {file}, which you have not opened since"
ANSWERED = "his message this post answers"
EARLIER = "his earlier message this post quotes"
LINKED_ASK = "his message this post answers, linked as asked"
ORDER_WORDS = "his words on the order this post is on"
HIS_PERMALINK = re.compile(r"<?(https://[\w.-]+/archives/([A-Z0-9]+)/p\d{16}[^\s|<>]*)>?")
PREVENTION_WORDS = {
    "id": "ruling:6a6edbf",
    "source": "his ruling on failures told to him",
    "quote": "thats not acceptable to dsay without telling me how you will prevent it",
    "detail": (
        "his words on a post telling him why something failed: it says how that is prevented, the tool change and the order or"
        " PR carrying it, so that line is within his words whenever the post answers his question or tells a failure"
    ),
}
WORKED_WORDS = {
    "id": limits.WORKED_WORDS["id"],
    "source": "his ruling on how far a card and the answers in its thread work out each option",
    "quote": limits.WORKED_WORDS["said"],
    "detail": (
        "his words on a question card and on a post in its thread explaining one of its options, an Explain more reply included:"
        " each option it explains is shown worked out to what it will look like, its real calls, the rows it writes, its flow or"
        " what a caller gets, in its text or a code or compare visual, never only the files it touches, the callers it reaches and"
        ' its cost, by his words on such an answer: "you need to flesh out what that will look like not just wave your hands,'
        ' likewise for 2 and 3"'
    ),
}
WHOLE = (
    " It names no message of his as asked, so it is not an answer and keeps that limit. An answer to a question or"
    " order of his goes out whole, at any length: pass as asked each message of his it answers, word for word or by its"
    " permalink, one per line."
)
RECORD_READ = re.compile(r"record:(\d+)")
READ_WORDS = re.compile(r"[^\s:]+")
LINE_NUMBERS = re.compile(r":\d[\d-]*(?=[\s:]|$)")
DOC_PATH = re.compile(r"[\w.~/-]+\.(?:md|mdx|rst)\b")
NAMED_SOURCE = "the read the posting handler names as {read}, as its newest call whose tool name and input hold its words"
GIVEN = "the newest call it names is given once, under {read}"
DOC_SOURCE = "; {docs} is a doc, which backs what that doc says and nothing about what code does"
CITED_SOURCE = "a record the post cites or a claim names, as the posting handler's newest call naming its seq"
UNCLAIMED = {"id": "claims:none", "source": "the reads the posting handler's claims name", "quote": "none: the post names no claim and no read behind one"}
CARRIED_SOURCE = "the report this post carries, another agent's words, as the posting handler's newest call naming its seq"


class Warned(str):
    """A finding the session hears beside its post, which goes out as written: a word pattern that also matches the product's own refusals, such as a pod its check refused, warns rather than refuses, by his pick "1 and then if we kepe getting false rejects move to 3", whose third option reads "Cause and plain-words findings only warn the session ... posts go out"."""


def his_drive(session: str) -> dict | None:
    """The drive this session is the communicator of, whose handlers write everything he reads in his DM with the app."""
    for name in state.session_drives(session):
        if state.session_role(state.saved(name), session) == "communicator":
            return state.load(name)
    return None


def coordinator_post(payload: dict) -> str | None:
    """Why the coordinator or the meta session may not post to Slack itself: his reply under a post reaches only the session that posted it, and the communicator is the one he talks to."""
    session = payload.get("session_id") or ""
    if payload.get("agent_id"):
        return None
    for name in state.session_drives(session):
        if (role := state.session_role(state.saved(name), session)) in ("coordinator", "meta"):
            return (
                f"Refused on purpose: the {state.title(role)} posts nothing to Slack, since his reply under a post reaches only the"
                f" session that posted it. Send the words as a `report` record to communicator@{name}, and its handler"
                " posts them; a question for him is a job's ASK, kept with `drive job ask`, which draws his card."
            )
    return None


def posts_to(payload: dict, dm: str) -> bool:
    if payload["tool_name"] in DM_POSTS:
        return True
    tool_input = payload["tool_input"]
    channel = tool_input.get("channel_id") or next(iter(PERMALINK_CHANNEL.findall(tool_input.get("url") or "")), None)
    return payload["tool_name"] in CHANNEL_POSTS and channel == dm


def last_read(path: Path, dm: str, where: tuple[str, str]) -> tuple[datetime | None, dict[str, datetime], bool]:
    """When this handler last read the thread, by its own transcript's timestamped turns, never a row such as an API request's copy that carries none: a slack_thread call that did not fail on the channel and thread read_thread resolves it to; when the tool saved a long thread to a file, only once a later call of this handler opened that file. Also the saved files it never opened, and whether a call on it failed."""
    calls: dict[str, datetime] = {}
    others: dict[str, str] = {}
    saved: dict[str, datetime] = {}
    read = None
    failed = False
    with path.open() as transcript:
        for line in transcript:
            row = json.loads(line)
            if "timestamp" not in row:
                continue
            content = (row.get("message") or {}).get("content")
            for part in content if isinstance(content, list) else []:
                if part.get("type") == "tool_use" and part.get("name") == THREAD_READ and read_thread(part["input"], dm) == where:
                    calls[part["id"]] = datetime.fromisoformat(row["timestamp"])
                elif part.get("type") == "tool_use":
                    others[part["id"]] = json.dumps(part.get("input"))
                elif part.get("type") != "tool_result":
                    continue
                elif part.get("is_error"):
                    failed = failed or part.get("tool_use_id") in calls
                elif part.get("tool_use_id") in calls:
                    at = calls[part["tool_use_id"]]
                    if file := SAVED.search(result_text(part)):
                        saved[file.group(1)] = at
                    else:
                        read = max(read or at, at)
                elif opened := [file for file in saved if Path(file).name in others.get(part.get("tool_use_id"), "")]:
                    read = max([saved.pop(file) for file in opened] + ([read] if read else []))
    return read, saved, failed


def read_thread(tool_input: dict, dm: str) -> tuple[str, str | None]:
    """The channel and thread a slack_thread call reads, resolved as cc-slack fills a call's blanks from its url: channel_id, else the url's channel, else his DM; thread_ts, else the url's thread."""
    link = state.link_thread(tool_input.get("url") or "") or (None, None)
    return tool_input.get("channel_id") or link[0] or dm, tool_input.get("thread_ts") or link[1]


def result_text(part: dict) -> str:
    content = part.get("content")
    return content if isinstance(content, str) else " ".join(piece.get("text", "") for piece in content or [])


def unread(payload: dict, dm: str) -> str | None:
    """Why a handler's new line in his thread waits for a read of that thread, which its own transcript must show in the last 10 minutes; a card is drawn from a record, not written from the thread."""
    tool_input = payload["tool_input"]
    if not payload.get("agent_id") or edited(payload) or tool_input.get("card") or not (thread := tool_input.get("thread_ts")):
        return None
    channel = tool_input.get("channel_id") or dm
    read, unopened, failed = last_read(agents.transcript(payload), dm, (channel, thread))
    if read and datetime.now(UTC) - read < READ_WINDOW:
        return None
    if unopened:
        why = UNOPENED_READ.format(thread=thread, file=max(unopened, key=unopened.__getitem__))
    elif read:
        why = OLD_READ.format(thread=thread, minutes=int((datetime.now(UTC) - read) / timedelta(minutes=1)))
    else:
        why = (FAILED_READ if failed else NO_READ).format(thread=thread)
    return UNREAD.format(why=why, channel=channel, thread=thread)


def unchecked(tool_input: dict, messages: list[dict], linked: dict[str, dict], drive: dict, report: dict, order_id: str | None) -> list[str]:
    """What `cc-slack check-post` finds in the exact words the post sends, judged by cc-slack's plain-words rule as a reply to each message of his it answers: those its asked names, word for word or by permalink, and his messages in its thread since the bot's last post there; an order or job id none of them used refuses unjudged, since no reading of his words makes it open anything; a post reporting on an incident keeps its alert's Slack message and Sentry or Datadog issue bare, so Slack unfurls them, as his incident rule asks; a post that draws a card, or a PR's tap card through tap_prs, carries the card its words say waits on him, and a line waiting on him with no card is judged by his card rule and told how a pick no card asks yet gets its card."""
    if not (text := tool_input.get("text") or ""):
        return []
    asked = [*answered_words(tool_input.get("asked"), linked), *(message["text"] for message in unanswered(messages))]
    try:
        findings = refs.answer_ids(slack.post_findings(text, bool(tool_input.get("card") or tool_input.get("tap_prs")), asked), drive)
    except slack.SlackError as error:
        return [f"refused: `cc-slack check-post` could not check this post's words, so it does not go out: {error}."]
    if any(ALERT_LINK.match(finding) for finding in findings) and incident(tool_input, drive, report, order_id):
        findings = [finding for finding in findings if not ALERT_LINK.match(finding)]
    if not findings:
        return []
    if ids := [finding for finding in findings if DRIVE_ID.match(finding)]:
        return [
            agents.Unjudged(
                "refused: an order or job id opens nothing for him, and no message of his this post answers used it: say what that"
                " order or job does instead, or pass his message naming it as asked:\n" + "\n".join(ids)
            )
        ]
    waits = [finding for finding in findings if slack.WAITS_WITHOUT_CARD.match(finding)]
    plain = [finding for finding in findings if finding not in waits]
    return [
        *([agents.Ruled(UNCARDED + "\n".join(waits), CARD_RULE)] if waits else []),
        *([agents.Ruled("refused: `cc-slack check-post` finds this in the post's exact words; fix each, then post the fixed words:\n" + "\n".join(plain), agents.PLAIN_WORDS_RULE)] if plain else []),
    ]


def thread_messages(payload: dict, dm: str) -> list[dict]:
    """The messages of the thread in his DM the post goes to, read from Slack, oldest first; none for a post at his DM root or outside his DM, such as in an alert's thread."""
    tool_input = payload["tool_input"]
    url = tool_input.get("url") or tool_input.get("edit") or ""
    if (tool_input.get("channel_id") or next(iter(PERMALINK_CHANNEL.findall(url)), dm)) != dm:
        return []
    if tool_input.get("thread_ts"):
        return slack.thread("--channel", dm, "--thread", tool_input["thread_ts"], "--limit", str(slack.WHOLE_THREAD))
    if url:
        return slack.thread("--url", url, "--limit", str(slack.WHOLE_THREAD))
    return []


def linked_words(asked: str | list[str] | None, dm: str) -> dict[str, dict]:
    """His messages asked names by permalink, each under the entry naming it, as `thread_words` keeps his words: a message in his DM with the app the bot did not write, read from Slack, so an answer posted outside the thread his message is in, such as a share he ordered to another person's group DM, quotes his order there; a link to any other message is no word of his."""
    found = {}
    for entry in slack.messages(asked or ""):
        if not (link := HIS_PERMALINK.fullmatch(entry.strip())) or link[2] != dm:
            continue
        message = slack.message(link[1])
        if not message.get("from_bot"):
            found[entry] = {"id": f"slack:{message['ts']}", "source": "slack", "quote": message["text"], "said_at": slack.sent_at(message).isoformat(), "detail": LINKED_ASK}
    return found


def answered_words(asked: str | list[str] | None, linked: dict[str, dict]) -> list[str]:
    """Each message of his asked names, word for word: an entry's own words, or the words of his message its permalink links."""
    return [linked[entry]["quote"] if entry in linked else entry for entry in slack.messages(asked or "")]


def heard_him(drive: dict, messages: list[dict], linked: dict[str, dict]) -> None:
    """Record him heard at the newest message of his a post answers, in its thread since the bot's last post there or linked by its asked, when the drive last heard him before it: his message in a handler's thread reaches that handler alone, never the communicator, whose handler start records the rest, so this keeps him present while he writes."""
    said = [slack.sent_at(message) for message in unanswered(messages)] + [datetime.fromisoformat(word["said_at"]) for word in linked.values()]
    last = (drive.get("owner") or {}).get("heard")
    if said and (last is None or max(said) > state.parse_time(last)):
        watch.hear(drive["name"], max(said))


def unanswered(messages: list[dict]) -> list[dict]:
    """His messages since the bot's last post in the thread, which a post there answers."""
    return list(itertools.takewhile(lambda message: not message.get("from_bot"), reversed(messages)))[::-1]


def thread_words(messages: list[dict]) -> list[dict]:
    """His words in the thread the post goes to, each as the evidence Jev cites, those since the bot's last post there marked as the ones it answers: in his DM with the app every message not the bot's is his, and each card the bot drew or shared there shows his answer on its ✓ line and his Explain more tap on the line cc-slack draws for it, which asks for the reply that explains the card; a thread outside his DM, such as an alert's, holds others' words, never his."""
    answers = {message["ts"] for message in unanswered(messages)}
    words = []
    for message in messages:
        said_at = slack.sent_at(message).isoformat()
        if not message.get("from_bot"):
            detail = ANSWERED if message["ts"] in answers else EARLIER
            words.append({"id": f"slack:{message['ts']}", "source": "slack", "quote": message["text"], "said_at": said_at, "detail": detail})
            continue
        cards = [message["text"], *(share.get("text") or "" for share in message.get("shares") or [])]
        taps = {answer: CARD_ANSWERED for card in cards for answer in CARD_ANSWER.findall(card)}
        taps |= {EXPLAIN_MORE: EXPLAIN_TAPPED for card in cards if EXPLAIN_TAP.search(card)}
        words += [
            {"id": f"card:{message['ts']}:{index}", "source": "card", "quote": quote, "said_at": said_at, "detail": detail}
            for index, (quote, detail) in enumerate(taps.items())
        ]
    return words


def unbacked(payload: dict, said: str) -> str | None:
    """Why a line of a post to him names a cause, or tells him a state of work, with no {record:} of the read that measured it: a cause on any say post but a handler's listing claims, whose every cause line Jev checks against the read its claims name beside it, and a state, such as fixed, covers it, merged, queued or live, on any post but a handler's, since Jev holds a handler's post for each state line its claims leave out, while no read stands behind the line of a session posting on its own."""
    handler = payload.get("agent_id")
    causes = refs.unbacked(said) if payload["tool_name"] == SAY and not (handler and payload["tool_input"].get("claims")) else []
    states = [] if handler else [line for line in refs.unread_states(said) if line not in causes]
    if not (lines := [*causes, *states]):
        return None
    return agents.Ruled(
        "refused: a line naming a cause or telling him a state of work carries {record:<seq>} of the read that measured it, on that line, or, in a"
        f" handler's post, sits in claims beside the read behind it; a state no read of this turn measured says unverified: {' | '.join(lines)}",
        CAUSE_RULE,
    )


def unprevented(said: str, found: dict[str, dict], subject: set[str]) -> Warned | None:
    """What the session hears when a line of its post to him reads as a failure, refusal or gap of the drive's own, a line of it late included, or cites its defect record, and the post has no word on how it is prevented, the order it goes to and that order's PRs, subject, never counting as that word: the post goes out as written. A post drawing a PR card carries the PR that prevents it."""
    if not (lines := refs.unprevented(said, found, subject)):
        return None
    return Warned(
        "message_limits: this post goes out as written, and these lines read as telling him a card, check, hook, lane or session"
        " of the drive failed, refused, disconnected or left a gap, or that a line of it is late, with no word on how it is prevented. When one does, reword"
        " the post with say(edit=<its permalink>, text=…) adding the tool change, the {order:<o>} or {pr:<n>}"
        " carrying it and when it is live, never the order or PR the post is about, or return a DEFECT line when no fix is started; a disconnect is no excuse, since a"
        " session reaches another with SendMessage while cci is down. A line telling what the product does, such as a pod its"
        ' own check refused, needs nothing. His ruling 6a6edbf: "thats not acceptable to dsay without telling me how you will'
        f' prevent it". The lines: {" | ".join(lines)}'
    )


def unordered(said: str) -> str | None:
    """Why a line of a post to him answers his order by a plan, record or answer of his that is missing, when his message is the order."""
    if not (lines := refs.record_excuses(said)):
        return None
    return agents.Ruled(
        "refused: his message is the order, so no line answers it by a plan, record or answer of his that is missing; his records"
        " are read only so he is never asked again. Do what his words ask, or name what stands in the way of doing it. His ruling"
        f" 9769307: \"Who cares if you have a record if they or not I'm telling you to do it\". The lines: {' | '.join(lines)}",
        ORDERED_RULE,
    )


def corrected(tool_input: dict, drive: dict) -> str | None:
    """Why a post calls a thing by a word he corrected, which `drive term` keeps beside its proper term."""
    if (refusal := limits.stand_in_refusal(tool_input, drive["stand_ins"])) is None:
        return None
    return agents.Ruled(f"refused: {refusal}", TERM_RULE)


def untooled(payload: dict, drive: dict, said: str) -> str | None:
    """Why say's PR card goes under a line leaving out the words of the drive tooling order a PR on it serves."""
    if payload["tool_name"] != SAY:
        return None
    stack = [key for each in payload["tool_input"].get("tap_prs") or [] if (key := state.pr_key(each)) in drive["prs"]]
    if (refusal := talk.unquoted_tooling(drive, stack, said)) is None:
        return None
    return agents.Ruled(f"refused: {refusal}", TOOLING_RULE)


def his_words(payload: dict) -> list[dict]:
    """What Jev judges a post by besides the transcripts: only the words of his in his DM it answers, so his share of Jev's prompt grows with the post, never with his threads: his messages in the thread it goes to since the bot's last post there, his answers on the card that thread is under, each message of his its asked links by permalink, each older word of his in that thread its asked or a quote in its text holds, and the words of his the order it is on keeps, read from the drive; a handler's brief and the record it reads in are the communicator's and a writer's words, never his, and a thread outside his DM, such as a bug report's, holds others' words; a post naming a cause or failure with the order or PR preventing it carries his ruling asking for that line, and a post in a question card's thread his ruling that each option it explains is worked out."""
    if (drive := his_drive(payload.get("session_id") or "")) is None or not posts_to(payload, dm := slack.dm()):
        return []
    tool_input = payload["tool_input"]
    text = tool_input.get("text") or ""
    ruled = [PREVENTION_WORDS] if refs.prevented(text) else []
    ruled += [WORKED_WORDS] if in_card_thread(drive, tool_input) else []
    try:
        thread = thread_words(thread_messages(payload, dm))
        linked = linked_words(tool_input.get("asked"), dm)
    except slack.SlackError:
        return ruled
    quoted = [squashed(part) for part in quotes([*answered_words(tool_input.get("asked"), linked), *limits.SAID.findall(text)])]
    card = f"card:{talk.place(tool_input)[1]}:"
    answered = [word for word in thread if word["detail"] == ANSWERED or word["id"].startswith(card) or any(part in squashed(word["quote"]) for part in quoted)]
    words = {word["id"]: word for word in [*answered, *order_words(drive, tool_input, said_order(payload, drive)[1]), *linked.values()]}
    return [*words.values(), *ruled]


def order_words(drive: dict, tool_input: dict, order_id: str | None) -> list[dict]:
    """His words on the order the post is on, as the drive keeps them with no Slack read: the message of his it was added from, or the quotes of his its text holds."""
    if not (found := posted_order(drive, tool_input, order_id)) or not (said := limits.his_words(order := drive["orders"][found])):
        return []
    message = state.slack_message(order["from"]) if limits.from_his_message(order) else None
    return [{"id": f"slack:{message[1]}" if message else f"order:{found}", "source": "slack" if message else "order", "quote": said, "detail": ORDER_WORDS}]


def in_card_thread(drive: dict, tool_input: dict) -> bool:
    """Whether the post goes in the thread of one of his question cards: say's `about` names the card, as an Explain more reply's does, or an order keeps its thread as a question card's."""
    where = list(talk.place(tool_input))
    return bool(tool_input.get("about")) or any(thread[:2] == where and thread[3] == "question" for order in drive["orders"].values() for thread in order["threads"])


def edited(payload: dict) -> dict | None:
    """Where the post a call rewords is, as cc-slack names a message: say's edit permalink as its url, or slack_edit's url, or its ts and thread; none for a new post."""
    tool_input = payload["tool_input"]
    if payload["tool_name"] == SAY:
        return {"url": tool_input["edit"]} if tool_input.get("edit") else None
    return tool_input if payload["tool_name"] == EDIT else None


def edited_words(payload: dict, dm: str) -> int:
    """How long the post an edit replaces is, read from Slack; 0 for a new post."""
    if (where := edited(payload)) is None:
        return 0
    if url := where.get("url"):
        return limits.words(slack.message(url)["text"])
    ts = where["ts"]
    return limits.words(slack.message_at(ts, "--channel", dm, "--thread", where.get("thread_ts") or ts)["text"])


def squashed(text: str) -> str:
    return SPACE.sub(" ", html.unescape(text).translate(STRAIGHT)).strip().casefold()


def quotes(asked: str | list[str]) -> list[str]:
    """Each stretch of his words that asked quotes: one message of his per entry or line, cut where the quote skips some of them."""
    return [part for entry in slack.messages(asked) for piece in BETWEEN_QUOTES.split(entry) if (part := piece.strip().strip(QUOTE_MARKS).strip())]


def unsaid(parts: list[str], said: str) -> list[str]:
    said = squashed(said)
    return [part for part in parts if squashed(part) not in said]


def deny_reason(payload: dict) -> str | None:
    reason = checked(payload)[0]
    return None if isinstance(reason, Warned) else reason


def checked(payload: dict) -> tuple[str | None, dict]:
    """What the drive's checks find in a post to his DM, and the call's input as Slack gets it, each of say's references linked, with its words as written beside them, each {started:} or {at:} stamp say fills from the drive's stamps listed beside the words drawn for it and what it stamps, when that job was added or that record went out, so Jev never reads a time say drew as one the poster typed, and its claims parsed from the JSON text a client may send them as, which Jev weighs the findings against: the coordinator posts nothing, and say refuses a reference that names nothing, claims whose text is not JSON or a link inside a link's words outright, whatever Jev reads."""
    if posted := coordinator_post(payload):
        return posted, payload["tool_input"]
    if (drive := his_drive(payload.get("session_id") or "")) is None:
        return None, payload["tool_input"]
    said = payload["tool_input"].get("text") or ""
    if payload["tool_name"] != SAY:
        return found_in(payload, drive, said, {}), payload["tool_input"]
    try:
        found = refs.resolved(drive["name"], said)
        claims = tools.parsed("claims", payload["tool_input"].get("claims") or [], mcp.CLAIMS)
        channel, thread = talk.place(payload["tool_input"])
    except DriveError as error:
        return agents.Unjudged(str(error)), payload["tool_input"]
    titled = bare(said, found)
    stamps = [{"written": reference, "drawn": shown["title"], "stamps": shown["stamps"]} for reference, shown in found.items() if "stamps" in shown]
    sent = (
        payload["tool_input"]
        | ({"text": refs.slack(said, found, talk.shown_in(drive["name"], payload["tool_input"])), "written": refs.REF.sub(lambda match: titled.get(match[0], match[0]), said)} if found else {})
        | ({"stamps": stamps} if stamps else {})
        | ({"claims": [claim | {"claim": claimed(claim["claim"], titled)} for claim in claims]} if claims else {})
    )
    return found_in(payload | {"tool_input": sent}, drive, said, found), sent | ({"thread_ts": thread} if thread else {}) | ({"channel_id": channel} if channel != slack.dm() else {})


def bare(said: str, found: dict[str, dict]) -> dict[str, str]:
    """Each {pr:…} or {card:…} of the post whose words after its | quote that PR's title or that card's question, word for word with … where they skip some, beside it written bare, since those words name it as the title say draws for a bare one does: the written field Jev reads holds it bare."""
    return {match[0]: f"{{{match[1]}:{match[2]}}}" for match in refs.REF.finditer(said) if match[3] and found[match[0]]["kind"] in ("pr", "card") and not unsaid(quotes(match[3]), found[match[0]]["own"])}


def claimed(claim: str, titled: dict[str, str]) -> str:
    """A claim's words as Jev reads them, each reference of titled and each copy of its words there written bare, so a claim that a PR merged, in the words of its title, names that PR and says nothing of its code."""
    for reference, written in titled.items():
        claim = claim.replace(reference, written).replace(refs.REF.fullmatch(reference)[3], written)
    return claim


def found_in(payload: dict, drive: dict, said: str, found: dict[str, dict]) -> str | None:
    """What the drive's checks find in a post, which the say hook hands Jev to judge against his words: a bug report's thread gets one fix-live note once the fix is proven live, a question's thread one answer once it is posted to him, an edit of his question card redraws it, an edit of a post say made goes through say, a post in his DM goes through say, a thread outside his DM, such as an alert's, is named with its channel_id, a handler's line in his thread waits for its read of that thread, and then, every finding at once, the post keeps its length, links what it names, links the open question card of his asking about each PR or error it names, asks no tap that is not owed, backs each cause, and each state of work a session posting on its own tells him, with the record of the read that measured it, answers his order by no plan, record or answer of his that is missing, calls nothing by a word he corrected, tells him no coordinator is down that was not restarted, calls no job stuck or silent before its poster read that job's transcript, says no work sits with no one on it while it names no job on it now, types no clock time for work already done, says in a summary what a PR it names serves, which a PR card's line above its card already quotes, and `cc-slack check-post` passes its exact words; a line naming a failure of the drive's with no word on how it is prevented only warns the session, and the post goes out."""
    dm = slack.dm()
    tool_input = payload["tool_input"]
    if PLACEHOLDER.fullmatch(text := (tool_input.get("text") or "").strip()):
        return (
            f"The text {text} is a placeholder, and Slack would send it as written. Pass the post's own words as text,"
            " the exact words `cc-slack check-post -` passed."
        )
    if refusal := reporter_post(payload, drive) or card_edit(payload, drive) or unsaid_post(payload, drive):
        return refusal
    if not posts_to(payload, dm):
        return None
    report, order_id = said_order(payload, drive)
    if refusal := held_while_away(payload, drive, report, order_id) or talk.misplaced(drive, tool_input) or unread(payload, dm):
        return refusal
    try:
        messages = thread_messages(payload, dm)
        linked = linked_words(tool_input.get("asked"), dm)
    except slack.SlackError as error:
        return f"refused: his thread could not be read, so this post cannot be checked as a reply to his messages there: {error}."
    heard_him(drive, messages, linked)
    if (long := over_length(payload, dm, messages, linked)) and found:
        long += TITLED.format(limits.words(refs.slack(" ".join(match[0] for match in refs.REF.finditer(said)), found, talk.shown_in(drive["name"], tool_input))))
    return every(
        [
            long and agents.Ruled(long, LENGTH_RULE, long),
            long_ack(payload),
            unlinked(payload, drive, order_id),
            uncarded_known(payload, drive, found),
            untapped(payload),
            claimed_tap(tool_input, drive, order_id),
            raw_visuals(tool_input),
            unswitched(tool_input, drive),
            *unchecked(tool_input, messages, linked, drive, report, order_id),
            unbacked(payload, said),
            None if tool_input.get("tap_prs") else unprevented(said, found, refs.subject(drive, posted_order(drive, tool_input, order_id))),
            unordered(said),
            corrected(tool_input, drive),
            untooled(payload, drive, said),
            reported_down(drive, said),
            agents.unread_job(payload, said),
            agents.unowned_work(said),
            unbacked_start(payload, drive, said, order_id),
            agents.typed_time(said, drive),
            None if tool_input.get("tap_prs") else limits.kind_refusal(tool_input.get("text") or "", said, drive),
        ]
    )


def unbacked_start(payload: dict, drive: dict, said: str, order_id: str | None) -> agents.Ruled | None:
    """Why a handler's line says a job is being started while it names no job as {started:<j>} and no handoff state.owed_handoffs names is kept on the orders of the incident or order it goes to: the promise then rests on a session's answer alone, which nothing tracks."""
    if not payload.get("agent_id") or not (found := PROMISED_START.search(said)) or any(match[1] == "started" for match in limits.REF.finditer(said)):
        return None
    posted = posted_order(drive, payload["tool_input"], order_id)
    if posted and state.owed_handoffs(drive, state.incident_orders(drive, posted)):
        return None
    return agents.Ruled(
        f'refused: "{found[0]}" says a job is being started, and the line names no job as {{started:<j>}} and no handoff asking for one is kept on'
        f" {f'order {posted}' if posted else 'the order it goes to'}: name the job that started as {{started:<j>}}, or first send the coordinator a"
        " `handoff` with the `record` tool naming the step as its text and `attrs.order` the order this line goes to, so the watch owes this"
        " thread a line five quiet minutes on until a job of that order starts",
        PROMISE_RULE,
    )


def reported_down(drive: dict, said: str) -> str | None:
    """Why a post tells him a coordinator is down: a coordinator whose process exited is restarted that minute, resuming its session, and he hears that it was restarted, never only that it is down."""
    if RESTARTED.search(said) or not any(COORDINATOR.search(part) and DOWN.search(part) for part in SENTENCE_BREAK.split(said)):
        return None
    held = drive["roles"]["coordinator"]
    return agents.Ruled(
        f"refused: a coordinator that is down is restarted that minute, never reported to him as down ({DOWN_RULING}). Restart it now"
        " by steps 5 to 7 of `drive manual migration`, Into an Orca terminal, creating the terminal resuming session"
        f" {held['session']} in {held['cwd']}, or a sub-coordinator's session in the folder its `drive status` line names, then tell"
        " him it was restarted",
        DOWN_RULE,
    )


def reporter_post(payload: dict, drive: dict) -> str | None:
    """Why a post may not go in the thread a bug an order fixes was reported in, or a question an answer order answers was asked in: that thread gets one note, a bug report's only once the fix is proven live and a question's only once the answer is posted, which Jev judges by REPORTER_RULE and which refuses when Jev cannot finish; an open order's own thread, such as a teammate's he ordered the drive to keep replying in, is his order to post there."""
    tool_input = payload["tool_input"]
    if payload["tool_name"] not in CHANNEL_POSTS - {EDIT} and not (payload["tool_name"] == SAY and tool_input.get("channel_id")):
        return None
    link = state.CARD_LINK.search(tool_input.get("url") or "")
    where = (tool_input.get("channel_id") or (link and link[1]), tool_input.get("thread_ts") or (link and (link[4] or f"{link[2]}.{link[3]}")))
    reported = [(order, held) for order in drive["orders"].values() for held in order["reporters"] if (held["channel"], held["thread"]) == where]
    if not reported or any(order["state"] not in state.CLOSED_ORDER_STATES and state.may_tell(drive, order) and not held["told"] for order, held in reported):
        return None
    if any(state.own_thread(order) == where for order in state.open_orders(drive).values()):
        return None
    told_order, told = next(((order, held["told"]) for order, held in reported if held["told"]), (None, None))
    if told and told_order["kind"] == "answer":
        refused = f"Refused on purpose: this thread already heard the answer to its question, in {told}, and hears it once. Post nothing more there."
    elif told:
        refused = (
            f"Refused on purpose: this thread already heard its bug is fixed, in {told}, and a bug report's thread gets its fix-live note once."
            " Post nothing more there."
        )
    elif any(order["kind"] == "answer" for order, _ in reported):
        refused = "Refused on purpose: the answer to the question asked in this thread is not posted to him yet, so a note now would come before it. It goes once the answer is posted."
    else:
        refused = (
            "Refused on purpose: the fix for the bug reported in this thread keeps no proof that it is live, from where he uses it when it"
            " builds something he uses, so a note now would come before the fix. It goes once the coordinator records the proof and sends the"
            " report closing the order."
        )
    return agents.Ruled(refused, REPORTER_RULE, refused)


def unsaid_post(payload: dict, drive: dict) -> str | None:
    """Why a write goes through say: slack_edit rewords a post in his DM, or one say made outside it, with each {card:…}, {pr:…}, {order:…}, {record:…} and {answer:…} reference left as raw text and the dashboard still showing its old words, another tool posting a file in his DM, such as a screen recording, puts it in Slack alone and never on the dashboard, and one posting a reference there sends it raw too."""
    dm = slack.dm()
    if payload["tool_name"] == EDIT and (posts_to(payload, dm) or said_post(drive["name"], edited(payload))):
        return agents.Ruled(
            "Refused on purpose: slack_edit sends each {card:…}, {pr:…}, {order:…}, {record:…} or {answer:…} reference to him as raw text and"
            " leaves the dashboard showing the post's old words. Reword it with say(edit=<its permalink>, text=<the new words>),"
            " which links each reference as a new post's are and rewords the post on the dashboard too.",
            SAY_RULE,
        )
    if payload["tool_name"] in (SAY, EDIT) or not posts_to(payload, dm):
        return None
    if payload["tool_input"].get("files"):
        return agents.Unjudged(
            f"Refused on purpose: a file {payload['tool_name'].removeprefix(SLACK)} posts to him reaches Slack alone and never the dashboard."
            ' Post it with say(text=…, visuals=[{"kind": "image", "source": "<its absolute path>", "caption": "<what it shows>"}]),'
            " one visual per file, which uploads each picture or screen recording to his DM and shows it in the post on the dashboard too."
        )
    if not (raw := refs.REF.search(payload["tool_input"].get("text") or "")):
        return None
    return agents.Ruled(
        f"Refused on purpose: {raw[0]} would reach him as raw text, since only say links a drive reference. Post it with"
        " say(text=…), which links it as he reads it and shows the post on the dashboard too.",
        SAY_RULE,
    )


def said_post(name: str, where: dict) -> bool:
    """Whether say made the post an edit rewords, as a said event of the drive's names it."""
    key = message_key(where)
    return any(event["kind"] == "said" and view.message_key(event["url"]) == key for event, _ in state.read_events(name))


def message_key(where: dict) -> str:
    return view.message_key(where["url"]) if where.get("url") else (where.get("ts") or "").replace(".", "")


def card_edit(payload: dict, drive: dict) -> str | None:
    """Why an edit may not touch his question card: an edit with text alone replaces its layout and buttons with plain text, and drawing it again under its waits id edits it where it sits."""
    if (where := edited(payload)) is None:
        return None
    key = message_key(where)
    for job_id, job in drive["jobs"].items():
        for question in job["questions"]:
            if question["card"] and view.message_key(question["card"]) == key:
                return (
                    "Refused on purpose: that message is his question card, and an edit with text alone replaces its layout and buttons"
                    f" with plain text. Draw it again with `drive job card {question['file'] or job_id} --waits {question['waits']}` and"
                    " post what it prints through say(card=…), which edits the card where it sits, buttons included."
                )
    return None


def said_order(payload: dict, drive: dict) -> tuple[dict, str | None]:
    """The report a post carries and the one order say records it on, as cli.post_order decides it for the posting session, a handler's own `claude --bg` session included; a post through another tool goes on none."""
    tool_input = payload["tool_input"]
    report = cli.report_attrs(seq) if (seq := tool_input.get("record")) else {}
    session = payload.get("handler_session") or payload.get("session_id")
    return report, cli.post_order(drive, session, tool_input, report) if payload["tool_name"] == SAY else None


def posted_order(drive: dict, tool_input: dict, order_id: str | None) -> str | None:
    """The order a post goes to: the one say records it on, else the open order whose own thread, in his DM or the channel its channel_id names, or the thread its lines to him go in, it goes in."""
    where = talk.place(tool_input)
    return order_id or next((each for each, order in state.open_orders(drive).items() if where[1] and where in (state.own_thread(order), state.promise_thread(order))), None)


def incident(tool_input: dict, drive: dict, report: dict, order_id: str | None) -> bool:
    """Whether a post reports on an incident, as the record it posts or the order it goes to marks it, never as its words read."""
    return report.get("incident") == "true" or bool((posted := posted_order(drive, tool_input, order_id)) and drive["orders"][posted]["incident"])


def held_while_away(payload: dict, drive: dict, report: dict, order_id: str | None) -> str | None:
    """Why a handler's line waits while he is away: only cards, the WAITING-ON-HIM line, incident lines and answers to his messages go out, and the rest joins his catch-up, the report it posts marked held on the drive; say posts nothing of a held report whatever Jev reads, so that refusal is unjudged and comes back before any size check of Jev's prompt."""
    tool_input = payload["tool_input"]
    if not payload.get("agent_id") or edited(payload) or tool_input.get("card") or tool_input.get("tap_prs") or tool_input.get("asked"):
        return None
    if WAITING in (tool_input.get("text") or "") or not state.owner_away(drive, state.now()) or incident(tool_input, drive, report, order_id):
        return None
    away = (
        "Refused on purpose: he is away, since he said so or has written nothing for an hour, and while he is away only cards,"
        " incident lines and answers to his messages go out. Post nothing: this news joins the catch-up the drive posts"
        " when he is back."
    )
    if not (seq := report.get("seq")):
        return agents.Ruled(away, AWAY_RULE, away)
    talk.hold(drive["name"], seq, posted_order(drive, tool_input, order_id))
    return agents.Unjudged(f"{away} Report {seq} is marked held: `drive status` lists it until the catch-up posts. Return NOTHING held for his catch-up.")


def long_ack(payload: dict) -> str | None:
    """Why the communicator's own ack runs past one sentence: it goes out inside seconds, and the handler answers the rest."""
    tool_input = payload["tool_input"]
    if payload.get("agent_id") or edited(payload) or tool_input.get("card") or tool_input.get("tap_prs"):
        return None
    sentences = [part for part in SENTENCE_BREAK.split(limits.masked(tool_input.get("text") or "").strip()) if part.strip()]
    if len(sentences) < 2:
        return None
    return agents.Ruled(
        f"refused: this ack is {len(sentences)} sentences, and an ack is one: what he asked, in his words, and what happens next."
        " Post that one sentence and put the rest in the handler's brief; the handler answers him in this thread.",
        ACK_RULE,
    )


def raw_visuals(tool_input: dict) -> str | None:
    """Why say's code visual is raw data or tool output, of which a picture shows him nothing."""
    raw = [
        (visual["source"], what)
        for visual in tool_input.get("visuals") or []
        if visual.get("kind") == "code" and (what := picture.raw(visual.get("language") or "", visual["source"]))
    ]
    if not raw:
        return None
    source, what = raw[0]
    return agents.Ruled(
        f'refused: the code visual holding "{render.cut(" ".join(source.split()), 60)}" is {what}, raw data or tool output, and a'
        f" picture of it shows him nothing: say what it means in words, show a short snippet of real code, or {picture.INSTEAD}"
        f" ({limits.RAW_RULING})",
        f"A picture shows him what words cannot, never raw data or tool output, by {limits.RAW_RULING}.",
    )


def unswitched(tool_input: dict, drive: dict) -> str | None:
    """Why a post gives him a switch command for a terminal no session of the drive runs in."""
    held = {role.get("terminal") for role in state.bound_sessions(drive).values()}
    if not (wrong := [handle for handle in SWITCH.findall(tool_input.get("text") or "") if handle not in held]):
        return None
    return agents.Ruled(
        f"refused: `orca terminal switch --terminal {wrong[0]}` names a terminal no session of this drive runs in. Give him a switch"
        " command only as the role's line `drive status` prints it, word for word, and none when that line carries none.",
        SWITCH_RULE,
    )


def every(refusals: list[str | None]) -> str | None:
    """One refusal holding each check's, so a single rewrite fixes them all, its rules and its counted findings numbered as its findings are so Jev judges each finding by its own; a refusal no Jev judges comes back alone, and a warning only when nothing refuses."""
    found = [refusal for refusal in refusals if refusal]
    refused = [refusal for refusal in found if not isinstance(refusal, Warned)]
    if unjudged := next((refusal for refusal in refused if isinstance(refusal, agents.Unjudged)), None):
        return unjudged
    if len(refused) < 2:
        return next(iter(refused or found), None)
    listed = "\n".join(f"{index}. {refusal.removeprefix('refused: ')}" for index, refusal in enumerate(refused, 1))
    rules = "\n".join(f"{index}. {refusal.rule}" for index, refusal in enumerate(refused, 1) if isinstance(refusal, agents.Ruled))
    counted = "\n".join(f"{index}. {refusal.counted.removeprefix('refused: ')}" for index, refusal in enumerate(refused, 1) if isinstance(refusal, agents.Ruled) and refusal.counted)
    return agents.Ruled(f"refused, {len(refused)} ways; fix each, then post the fixed words once:\n{listed}", rules, counted)


def untapped(payload: dict) -> str | None:
    """Why a PR card asks him to tap a PR its `tap_prs` names that he tapped, or that merged or closed."""
    if not (tap_prs := payload["tool_input"].get("tap_prs")):
        return None
    prs = {key: pr for name in state.session_drives(payload["session_id"]) for key, pr in state.load(name)["prs"].items()}
    refusal = limits.tap_refusal("\n".join(tap_prs), prs)
    return refusal and agents.Ruled(refusal, OWED_TAP_RULE)


def claimed_tap(tool_input: dict, drive: dict, order_id: str | None) -> str | None:
    """Why a post's text or card says a PR waits on his tap or Approve with nothing for him to tap; a PR card's own PRs are `untapped`'s to check, since the post draws their tap card."""
    if tool_input.get("tap_prs"):
        return None
    card = tool_input.get("card") or {}
    words = "\n".join([tool_input.get("text") or "", card.get("title") or "", card.get("body") or "", *(card.get("options") or [])])
    refusal = view.tap_claim_refusal(drive, order_id or "", words, (view.live_cards(drive["name"]) or view.NO_CARDS)["pr_cards"])
    return refusal and agents.Ruled(f"{refusal}. A PR the drive owes his tap on and no open card asks gets its tap card from tap_prs on the post.", CARD_RULE)


def unlinked(payload: dict, drive: dict, order_id: str | None) -> str | None:
    """Why a handler's post names a PR or card the drive holds with no link where it is named; a PR card's line quotes his order and cc-slack draws its card, and the communicator's ack names nothing."""
    if not payload.get("agent_id") or payload["tool_input"].get("tap_prs"):
        return None
    refusal = limits.link_refusal(payload["tool_input"], limits.held(drive, order_id))
    return refusal and agents.Ruled(refusal, LINK_RULE)


def uncarded_known(payload: dict, drive: dict, found: dict[str, dict]) -> str | None:
    """Why a handler's post names a PR or an error his open question card already asks about, by state.asking_cards, with no {card:…} of that card, so a failure he was already told about would read to him as new; a card or PR card the post draws, and the card a reply is about, link themselves."""
    tool_input = payload["tool_input"]
    if not payload.get("agent_id") or tool_input.get("card") or tool_input.get("tap_prs"):
        return None
    linked = {view.message_key(ref["url"]) for ref in found.values() if ref["kind"] == "card"}
    about = tool_input.get("about") and state.waits_of(drive["name"], tool_input["about"])
    if not (known := [(question, what) for question, what in state.asking_cards(drive, tool_input.get("text") or "") if view.message_key(question["card"]) not in linked and question["waits"] != about]):
        return None
    named = "; ".join(f"{state.asked_line(question, what)}, so link it as {{card:{question['waits']}}}" for question, what in known)
    return agents.Ruled(f"refused: {named}. Name it beside what it asks about, and tell him the news as that card's.", KNOWN_RULE)


def over_length(payload: dict, dm: str, messages: list[dict], linked: dict[str, dict]) -> str | None:
    """Why the post is longer than he reads: a routine line, summary or card keeps its limit, an edit may reword its post at that post's length, and an answer naming as asked any message of his, word for word as the thread it goes to shows it or by its permalink in his DM, goes out whole."""
    tool_input = payload["tool_input"]
    if (refusal := limits.post_refusal(tool_input)) is None or tool_input.get("card"):
        return refusal
    try:
        refusal = limits.post_refusal(tool_input, edited_words(payload, dm))
    except slack.SlackError as error:
        refusal += f" The post it edits could not be read to check its length: {error}."
    if refusal is None:
        return None
    if not (asked := quotes(answered_words(tool_input.get("asked"), linked))):
        return refusal + WHOLE
    missing = unsaid(asked, " ".join(word["quote"] for word in [*thread_words(messages), *linked.values()]))
    if len(missing) < len(asked):
        return None
    named = ", ".join(f'"{part}"' for part in missing)
    return (
        refusal + " It is not an answer, so it keeps that limit: asked lifts the limit only with his own words, and these stand"
        " in no message of his in the thread this post goes to, no answer or Explain more tap of his a card there shows and no message of his"
        f" in his DM an asked permalink links: {named}. Copy each message of his this post answers word for word, one per"
        " line, with … where you skip words, or pass its permalink when it is in another thread of his DM, as a share he"
        " ordered to another person's DM does."
    )


def denial(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def read_evidence(payload: dict, claims: list[dict]) -> list[dict]:
    """What Jev weighs every handler's say post by beside his words, whatever the checks found: for each read its claims name, its newest call that read names, and for each record a claim names or its text cites, the report it carries among them, its newest call naming that record's seq, each with its whole result, so a report it carries reaches Jev only when a line of it names that report, each call once under the first item naming it, since a line on where code lives, what it does, or a number or state stands only on the read behind it, and a record stands on the handler's own read of it, never its whole body, so a handler that reads again narrower hands Jev only that read, by his pick "The handler names the read behind each claim; Jev checks each claim against that read, whole"; none for any other post, such as the communicator's ack, or a card or PR card, which a record draws."""
    tool_input = payload["tool_input"]
    if payload["tool_name"] != SAY or not payload.get("agent_id") or tool_input.get("card") or tool_input.get("tap_prs") or his_drive(payload.get("session_id") or "") is None:
        return []
    reads = list(dict.fromkeys(claim["read"].strip() for claim in claims))
    named = [read for read in reads if not RECORD_READ.fullmatch(read)]
    cited = [found[2] for found in refs.REF.finditer(tool_input.get("text") or "") if found[1] == "record"]
    claimed = [read.removeprefix("record:") for read in reads if RECORD_READ.fullmatch(read)]
    seqs = list(dict.fromkeys([*cited, *claimed]))
    calls = own_calls(payload) if named or seqs else {}
    given: dict[str, str] = {}
    return [
        *(
            called(f"read:{index}", read, NAMED_SOURCE.format(read=json.dumps(read, ensure_ascii=False)), calls, given)
            for index, read in enumerate(named, 1)
        ),
        *(called(f"record:{seq}", seq, CARRIED_SOURCE if seq == tool_input.get("record") else CITED_SOURCE, calls, given) for seq in seqs),
        *([] if claims else [UNCLAIMED]),
    ]


def own_calls(payload: dict) -> dict[str, str]:
    """Each call of the handler's own transcript, its posts aside, as its tool's name and its input, beside its whole result, an error's marked so; a result the tool saved to a file, keeping only its preview in the transcript, is that file whole, its call named by the file's path too; the newest result of two calls alike, in the order of each call's newest run."""
    calls: dict[str, str] = {}
    results: dict[str, str] = {}
    for row in agents.tail(agents.transcript(payload)):
        for part in agents.parts(row):
            if part.get("type") == "tool_use" and part.get("name") not in DM_POSTS | CHANNEL_POSTS:
                calls[part["id"]] = f"{part['name']} {json.dumps(part.get('input'), ensure_ascii=False)}"
            elif part.get("type") == "tool_result" and (call := calls.get(part.get("tool_use_id"))):
                if saved := SAVED.search(text := result_text(part)):
                    call, text = f"{call} {saved[1]}", Path(saved[1]).read_text()
                results.pop(call, None)
                results[call] = ("error: " if part.get("is_error") else "") + text
    return results


def called(item: str, read: str, source: str, calls: dict[str, str], given: dict[str, str]) -> dict:
    """Jev's item for one read the claims name, or one record by its seq: the newest call whose tool name and input hold every word of the read, case aside, a path's line numbers after its colon left out, since the handler copies a read's words from the call it means and each call's result goes whole, so a narrower read again stands in for a wide one and older calls its words also hold never crowd Jev's prompt; that call whole under the first item holding it, and a later item naming that item's id in its place, since one call under two reads doubled Jev's prompt; or none when no call does. Its source names each doc, such as a report or a brief, that call opens."""
    words = [json.dumps(word, ensure_ascii=False)[1:-1] for word in READ_WORDS.findall(LINE_NUMBERS.sub("", read.casefold()))]
    held = next((call for call in reversed(calls) if words and all(word in call.casefold() for word in words)), None)
    if held is None:
        return {"id": item, "read": read, "source": source, "quote": f"none: no call of the handler's own holds {json.dumps(read, ensure_ascii=False)}"}
    docs = list(dict.fromkeys(DOC_PATH.findall(held)))
    first = given.setdefault(held, item)
    return {
        "id": item,
        "read": read,
        "source": source + (DOC_SOURCE.format(docs=json.dumps(docs, ensure_ascii=False)) if docs else ""),
        "quote": f"{held}\n{calls[held]}" if first == item else GIVEN.format(read=first),
    }


def main() -> None:
    payload = agents.seen(json.load(sys.stdin))
    try:
        reason, sent = checked(payload)
        reads = [] if isinstance(reason, agents.Unjudged) else read_evidence(payload, sent.get("claims") or [])
        ids = {item["read"]: item["id"] for item in reads if item["id"].startswith("read:")}
        sent = sent | ({"claims": [claim | {"read": ids.get(claim["read"].strip(), claim["read"])} for claim in sent["claims"]]} if sent.get("claims") else {})
        heard = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": reason}} if isinstance(reason, Warned) else {}
        reason = "" if heard else reason
        judged = {"his_words": his_words(payload), "reads": reads, "post": sent} if (reason or reads) and not isinstance(reason, agents.Unjudged) else {}
    except Exception as error:
        if isinstance(error, state.NewerDrive) and install.rechecked(Path(__file__).name, payload):
            return
        reason, heard, judged = (
            f"refused: the drive's message check failed on this post with {type(error).__name__}: {error}, so it does not go"
            " out unchecked. Name it on a DEFECT line, and the post goes out once the check's fix is installed."
        ), {}, {}
    refused = denial(reason) | agents.ruled(reason) if reason else {}
    if output := heard | refused | judged:
        print(json.dumps(output))


if __name__ == "__main__":
    main()
