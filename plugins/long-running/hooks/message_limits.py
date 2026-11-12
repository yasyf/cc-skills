import html
import itertools
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, cli, limits, picture, refs, render, slack, state, talk, view
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
SAVED = re.compile(r"Output has been saved to (/\S+?)\.?(?=\s|$)")
ALERT_LINK = re.compile(r"^\d+:\d+ bare-url 'https://(?:[\w-]+\.slack\.com/archives/|(?:[\w-]+\.)?sentry\.io/|(?:[\w-]+\.)?datadoghq\.com/)")
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n+")
PROMISE = re.compile(r"\b(?:from now on|next you(?:['’]ll| will)? get|I(?:['’]ll| will))\b", re.IGNORECASE)
CARRIER = re.compile(r"\b[oj]\d+\b|\b\d{1,2}:\d{2}\s*[AP]M\b", re.IGNORECASE)
SWITCH = re.compile(r"orca terminal switch --terminal[= ]([^\s`'\"<>|]+)")
CARD_ANSWER = re.compile(r"✓ ([^\n]+)")
COORDINATOR = re.compile(r"\bcoordinator\b", re.IGNORECASE)
DOWN = re.compile(
    r"\b(?:is|was|went|has been|had been|seems?|looks?)\s+(?:down|dead|gone|offline|unresponsive)\b|\b(?:crashed|exited|died|not running|stopped responding)\b",
    re.IGNORECASE,
)
RESTARTED = re.compile(r"\brestart", re.IGNORECASE)
DOWN_RULING = "his Nov 8 ruling: \"The coordinator is down? Then why the fuck haven't you restarted it?\" and \"It's unacceptable to just say it's down\""
WAITING = "WAITING-ON-HIM"
AWAY_RULE = "While he is away, a line of news to him that is not a card, a line saying what waits on him, an incident line or an answer to a message of his."
TITLED = (
    " {} of the words counted are the titles its {{card:…}}, {{pr:…}}, {{order:…}}, {{record:…}} and {{answer:…}} references draw as their words;"
    " {{pr:<number>|<words>}} or {{record:<seq>|<words>}} names one in fewer words of your own."
)
UNREAD = (
    "refused: {why}, so this line could miss what he said there since. Read it with slack_thread(channel_id={channel},"
    " thread_ts={thread}, limit=1000), then post. When the tool saves a long thread to a file, the call is not a read"
    " until you open that file and read its newest messages."
)
DRIVE_ID = re.compile(r"^\d+:\d+ drive-term '[oj]\d+'")
CAUSE_RULE = (
    "State a cause, a number or a state only through the record that measured it, as {record:<seq>} on the same line: on"
    ' Nov 10 a line at 8:46 PM blamed "the change that started the failures" without naming it, and a card he tapped at'
    ' 9:49 PM carried a false "disk full".'
)
PLAIN_WORDS_RULE = (
    "Write every post for the person reading the thread, in plain words: no internal shorthand such as tracking ids, agent"
    " lane names, raw commit shas or object ULIDs, and code, files, products and alerts by their proper names. A multi-agent"
    " term such as lane, desk, walker, parity or fence is shorthand, but never inside a function, file, path, product or"
    " alert name or in a code span, and one the reader used in the message the post answers passes in the answer."
)
LENGTH_RULE = (
    "A line in his thread is at most 60 words and a summary of two or more features at most 120, the coordinator's reading"
    ' of his words "your messages need to be way shorter rewrite this for a human"; an answer to a question or order of his'
    " in that thread, his words passed as asked, goes out whole at any length."
)
ACK_RULE = "The communicator's ack is one sentence: what he asked, in his words, and what happens next; its handler answers the rest."
LINK_RULE = (
    "Link everything he could open where it is named, a PR, a teammate's fix, a card, a release, a build, an alert, a thread"
    " or an earlier decision, so he never has to look it up himself."
)
OWED_TAP_RULE = "Ask for a tap only when one is owed: when he tapped a PR already, or it merged or closed, say what happened to it instead."
CARD_RULE = 'Anything that says it waits on him carries the card with its buttons, by his words "Anything that says waiting on me you always need to embed the card with the buttons".'
PROMISE_RULE = (
    'A promise such as "from now on…" or "next you get…" goes out only with the order, due time or job that carries it out'
    " named in the same message, because a promise with nothing behind it is one he cannot believe."
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
ANSWERED = "his message in the thread this post goes to since the bot's last post there, which this post answers"
PREVENTION_WORDS = {
    "id": "ruling:6a6edbf",
    "source": "his ruling on failures told to him",
    "quote": "thats not acceptable to dsay without telling me how you will prevent it",
    "detail": (
        "his words on a post telling him why something failed: it says how that is prevented, the tool change and the order or"
        " PR carrying it, so that line is within his words whenever the post answers his question or tells a failure"
    ),
}
WHOLE = (
    " It names no message of his as asked, so it is not an answer and keeps that limit. An answer to a question or"
    " order of his goes out whole, at any length: pass as asked each message of his it answers, word for word, one per line."
)
CODE_SPAN = re.compile(r"`([^`\n]+)`")
LINKED = re.compile(r"<https?://[^>]*>|https?://\S+")
INNER = re.compile(r"\w(?:_|\.|/|::)\w")
PLACE = re.compile(r"<[^<>]*>")
WORD = re.compile(r"[a-z_]\w*")
EDGES = ".,;:!?()[]\"'“”‘’*"
CALL_SHOWN = 240
LINE_SHOWN = 200
READS_SOURCE = (
    "the reads the posting handler made itself, from its own transcript: each tool call whose result came back, its posts"
    " aside, numbered, then each name the post uses beside the first line of each read, its call or its result, holding it;"
    " another agent's or"
    " session's read, a doc, the project's loaded instructions and the handler's brief are none of these"
)
CARRIED_SOURCE = "the report this post carries as say's record, another agent's words, which back a line only as the record that measured it"


class Warned(str):
    """A finding the session hears beside its post, which goes out as written: a word pattern that also matches the product's own refusals, such as a pod its check refused, warns rather than refuses, by his pick "1 and then if we kepe getting false rejects move to 3", whose third option reads "Cause and plain-words findings only warn the session ... posts go out"."""


def his_drive(session: str) -> dict | None:
    """The drive this session is the communicator of, whose handlers write everything he reads in his DM with the app."""
    for name in state.session_drives(session):
        data = state.load(name)
        if state.session_role(data, session) == "communicator":
            return data
    return None


def coordinator_post(payload: dict) -> str | None:
    """Why the coordinator or the meta session may not post to Slack itself: his reply under a post reaches only the session that posted it, and the communicator is the one he talks to."""
    session = payload.get("session_id") or ""
    if payload.get("agent_id"):
        return None
    for name in state.session_drives(session):
        if (role := state.session_role(state.load(name), session)) in ("coordinator", "meta"):
            return (
                f"Refused on purpose: the {state.title(role)} posts nothing to Slack, since his reply under a post reaches only the"
                f" session that posted it. Send the words as a `report` record to communicator@{name}, and its handler"
                " posts them; a question for him goes as an `ask` record there."
            )
    return None


def posts_to(payload: dict, dm: str) -> bool:
    if payload["tool_name"] in DM_POSTS:
        return True
    tool_input = payload["tool_input"]
    channel = tool_input.get("channel_id") or next(iter(PERMALINK_CHANNEL.findall(tool_input.get("url") or "")), None)
    return payload["tool_name"] in CHANNEL_POSTS and channel == dm


def last_read(path: Path, dm: str, where: tuple[str, str]) -> tuple[datetime | None, dict[str, datetime], bool]:
    """When this handler last read the thread, by its own transcript's timestamped turns, never a row such as an API request's copy that carries none: a slack_thread call on its channel and thread that did not fail, a call leaving channel_id out reading his DM; when the tool saved a long thread to a file, only once a later call of this handler opened that file. Also the saved files it never opened, and whether a call on it failed."""
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
                if part.get("type") == "tool_use" and part.get("name") == THREAD_READ and (part["input"].get("channel_id") or dm, part["input"].get("thread_ts")) == where:
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
                elif opened := [file for file in saved if file in others.get(part.get("tool_use_id"), "")]:
                    read = max([saved.pop(file) for file in opened] + ([read] if read else []))
    return read, saved, failed


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


def unchecked(tool_input: dict, messages: list[dict], drive: dict) -> str | None:
    """What `cc-slack check-post` finds in the exact words the post sends, judged by cc-slack's plain-words rule as a reply to each message of his it answers: those its asked names and his messages in its thread since the bot's last post there; an order or job id none of them used refuses unjudged, since no reading of his words makes it open anything; a post reporting on an incident keeps its alert's Slack message and Sentry or Datadog issue bare, so Slack unfurls them, as his incident rule asks; a post that draws a card, or a PR's tap card through tap_prs, carries the card its words say waits on him."""
    if not (text := tool_input.get("text") or ""):
        return None
    asked = [*slack.messages(tool_input.get("asked") or ""), *(message["text"] for message in unanswered(messages))]
    try:
        findings = refs.answer_ids(slack.post_findings(text, bool(tool_input.get("card") or tool_input.get("tap_prs")), asked), drive)
    except slack.SlackError as error:
        return f"refused: `cc-slack check-post` could not check this post's words, so it does not go out: {error}."
    if any(ALERT_LINK.match(finding) for finding in findings) and incident(tool_input, drive):
        findings = [finding for finding in findings if not ALERT_LINK.match(finding)]
    if not findings:
        return None
    if ids := [finding for finding in findings if DRIVE_ID.match(finding)]:
        return agents.Unjudged(
            "refused: an order or job id opens nothing for him, and no message of his this post answers used it: say what that"
            " order or job does instead, or pass his message naming it as asked:\n" + "\n".join(ids)
        )
    return agents.Ruled("refused: `cc-slack check-post` finds this in the post's exact words; fix each, then post the fixed words:\n" + "\n".join(findings), PLAIN_WORDS_RULE)


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


def unanswered(messages: list[dict]) -> list[dict]:
    """His messages since the bot's last post in the thread, which a post there answers."""
    return list(itertools.takewhile(lambda message: not message.get("from_bot"), reversed(messages)))[::-1]


def thread_words(messages: list[dict]) -> list[dict]:
    """His words in the thread the post goes to, each as the evidence Jev cites, those since the bot's last post marked as the ones it answers: in his DM with the app every message not the bot's is his, and each card the bot drew or shared there shows his answer on its ✓ line; a thread outside his DM, such as an alert's, holds others' words, never his."""
    answers = {message["ts"] for message in unanswered(messages)}
    words = []
    for message in messages:
        said_at = slack.sent_at(message).isoformat()
        if not message.get("from_bot"):
            detail = ANSWERED if message["ts"] in answers else "his message in the thread this post goes to"
            words.append({"id": f"slack:{message['ts']}", "source": "slack", "quote": message["text"], "said_at": said_at, "detail": detail})
            continue
        cards = [message["text"], *(share.get("text") or "" for share in message.get("shares") or [])]
        words += [
            {"id": f"card:{message['ts']}:{index}", "source": "card", "quote": answer, "said_at": said_at, "detail": "his answer, as the card the bot drew in this thread shows it"}
            for index, answer in enumerate(dict.fromkeys(answer for card in cards for answer in CARD_ANSWER.findall(card)))
        ]
    return words


def unbacked(payload: dict, said: str) -> str | None:
    """Why a line of say's post names a cause with no {record:} of the record that measured it."""
    if payload["tool_name"] != SAY or not (lines := refs.unbacked(said)):
        return None
    return agents.Ruled(f"refused: a line naming a cause carries {{record:<seq>}} of the record that measured it, on that line: {' | '.join(lines)}", CAUSE_RULE)


def unprevented(said: str, found: dict[str, dict]) -> Warned | None:
    """What the session hears when a line of its post to him reads as a failure, refusal or gap of the drive's own, or cites its defect record, with no word on how it is prevented: the post goes out as written."""
    if not (lines := refs.unprevented(said, found)):
        return None
    return Warned(
        "message_limits: this post goes out as written, and these lines read as telling him a card, check, hook, lane or session"
        " of the drive failed, refused, disconnected or left a gap, with no word on how it is prevented. When one does, reword"
        " the post with say(edit=<its permalink>, text=…) adding on that line the tool change, the {order:<o>} or {pr:<n>}"
        " carrying it and when it is live, or return a DEFECT line when no fix is started; a disconnect is no excuse, since a"
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
    """What Jev judges a refused post by besides the transcripts: his words in the thread it goes to in his DM, read from Slack, his messages and the answers its cards show; a handler's brief and the record it reads in are the communicator's and a writer's words, never his, and a thread outside his DM, such as a bug report's, holds others' words; a post naming a cause or failure with the order or PR preventing it carries his ruling asking for that line."""
    if (drive := his_drive(payload.get("session_id") or "")) is None or not posts_to(payload, drive["slack"]["channel"]):
        return []
    ruled = [PREVENTION_WORDS] if refs.prevented(payload["tool_input"].get("text") or "") else []
    try:
        return [*thread_words(thread_messages(payload, drive["slack"]["channel"])), *ruled]
    except slack.SlackError:
        return ruled


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
    """What the drive's checks find in a post to his DM, and the call's input as Slack gets it, each of say's references linked, which Jev weighs the findings against: the coordinator posts nothing, and say refuses a reference that names nothing or a link inside a link's words outright, whatever Jev reads."""
    if posted := coordinator_post(payload):
        return posted, payload["tool_input"]
    if (drive := his_drive(payload.get("session_id") or "")) is None:
        return None, payload["tool_input"]
    said = payload["tool_input"].get("text") or ""
    if payload["tool_name"] != SAY:
        return found_in(payload, drive, said, {}), payload["tool_input"]
    try:
        found = refs.resolved(drive["name"], said)
    except DriveError as error:
        return agents.Unjudged(str(error)), payload["tool_input"]
    sent = payload["tool_input"] | {"text": refs.slack(said, found, talk.shown_in(drive["name"], payload["tool_input"]))} if found else payload["tool_input"]
    return found_in(payload | {"tool_input": sent}, drive, said, found), sent


def found_in(payload: dict, drive: dict, said: str, found: dict[str, dict]) -> str | None:
    """What the drive's checks find in a post, which the say hook hands Jev to judge against his words: a bug report's thread gets one fix-live note once the fix is proven live, an edit of his question card redraws it, an edit of a post say made goes through say, a post in his DM goes through say, a handler's line in his thread waits for its read of that thread, and then, every finding at once, the post keeps its length, links what it names, asks no tap that is not owed, backs each cause with its record, answers his order by no plan, record or answer of his that is missing, calls nothing by a word he corrected, tells him no coordinator is down that was not restarted, calls no job stuck or silent before its poster read that job's transcript, types no clock time for work already done, says in a summary what a PR it names serves, which a PR card's line above its card already quotes, and `cc-slack check-post` passes its exact words; a line naming a failure of the drive's with no word on how it is prevented only warns the session, and the post goes out."""
    dm = drive["slack"]["channel"]
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
    if refusal := held_while_away(payload, drive) or unread(payload, dm):
        return refusal
    try:
        messages = thread_messages(payload, dm)
    except slack.SlackError as error:
        return f"refused: his thread could not be read, so this post cannot be checked as a reply to his messages there: {error}."
    if (long := over_length(payload, dm, messages)) and found:
        long += TITLED.format(limits.words(refs.slack(" ".join(match[0] for match in refs.REF.finditer(said)), found, talk.shown_in(drive["name"], tool_input))))
    return every(
        [
            long and agents.Ruled(long, LENGTH_RULE),
            long_ack(payload),
            unlinked(payload, drive),
            untapped(payload),
            claimed_tap(tool_input, drive),
            unpromised(payload),
            raw_visuals(tool_input),
            unswitched(tool_input, drive),
            unchecked(tool_input, messages, drive),
            unbacked(payload, said),
            unprevented(said, found),
            unordered(said),
            corrected(tool_input, drive),
            untooled(payload, drive, said),
            reported_down(drive, said),
            agents.unread_job(payload, said),
            agents.typed_time(said, drive),
            None if tool_input.get("tap_prs") else limits.kind_refusal(tool_input.get("text") or "", said, drive),
        ]
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
    """Why a post may not go in the thread a bug an order fixes was reported in: that thread gets one fix-live note, only once the fix is proven live."""
    tool_input = payload["tool_input"]
    if payload["tool_name"] not in CHANNEL_POSTS - {EDIT} and not (payload["tool_name"] == SAY and tool_input.get("channel_id")):
        return None
    link = state.CARD_LINK.search(tool_input.get("url") or "")
    where = (tool_input.get("channel_id") or (link and link[1]), tool_input.get("thread_ts") or (link and (link[4] or f"{link[2]}.{link[3]}")))
    reported = [order for order in drive["orders"].values() if order["reporter"] and (order["reporter"]["channel"], order["reporter"]["thread"]) == where]
    if not reported or any(order["state"] not in state.CLOSED_ORDER_STATES and order["proof"] and not order["reporter"]["told"] for order in reported):
        return None
    if told := next((order["reporter"]["told"] for order in reported if order["reporter"]["told"]), None):
        return (
            f"Refused on purpose: this thread already heard its bug is fixed, in {told}, and a bug report's thread gets its fix-live note once."
            " Post nothing more there."
        )
    return (
        "Refused on purpose: the fix for the bug reported in this thread keeps no proof that it is live, so a note now would come before"
        " the fix. It goes once the coordinator records the proof and sends the report closing the order."
    )


def unsaid_post(payload: dict, drive: dict) -> str | None:
    """Why a write goes through say: slack_edit rewords a post in his DM, or one say made outside it, with each {card:…}, {pr:…}, {order:…}, {record:…} and {answer:…} reference left as raw text and the dashboard still showing its old words, and another tool posting a reference in his DM sends it raw too."""
    dm = drive["slack"]["channel"]
    if payload["tool_name"] == EDIT and (posts_to(payload, dm) or said_post(drive["name"], edited(payload))):
        return agents.Ruled(
            "Refused on purpose: slack_edit sends each {card:…}, {pr:…}, {order:…}, {record:…} or {answer:…} reference to him as raw text and"
            " leaves the dashboard showing the post's old words. Reword it with say(edit=<its permalink>, text=<the new words>),"
            " which links each reference as a new post's are and rewords the post on the dashboard too.",
            SAY_RULE,
        )
    if payload["tool_name"] in (SAY, EDIT) or not posts_to(payload, dm) or not (raw := refs.REF.search(payload["tool_input"].get("text") or "")):
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


def posted_order(drive: dict, tool_input: dict) -> tuple[str, dict] | None:
    """The order a post goes to: the one say's order names, else the open order whose thread it goes in."""
    if (order_id := tool_input.get("order")) in drive["orders"]:
        return order_id, drive["orders"][order_id]
    thread = tool_input.get("thread_ts")
    return next(((order_id, order) for order_id, order in state.open_orders(drive).items() if thread and state.dm_thread(drive, order) == thread), None)


def reported_order(drive: dict, tool_input: dict, report: dict) -> str | None:
    """The order a post reports on: the one say's order or thread names, else the one the record it posts is about."""
    return found[0] if (found := posted_order(drive, tool_input)) else state.report_order(drive, report) if tool_input.get("record") else None


def incident(tool_input: dict, drive: dict) -> bool:
    """Whether a post reports on an incident, as the record it posts or the order it reports on marks it, never as its words read."""
    report = cli.report_attrs(seq) if (seq := tool_input.get("record")) else {}
    return report.get("incident") == "true" or bool((order_id := reported_order(drive, tool_input, report)) and drive["orders"][order_id]["incident"])


def held_while_away(payload: dict, drive: dict) -> str | None:
    """Why a handler's line waits while he is away: only cards, the WAITING-ON-HIM line, incident lines and answers to his messages go out, and the rest joins his catch-up, the report it posts marked held on the drive and its writer told so."""
    tool_input = payload["tool_input"]
    if not payload.get("agent_id") or edited(payload) or tool_input.get("card") or tool_input.get("tap_prs") or tool_input.get("asked"):
        return None
    if WAITING in (tool_input.get("text") or "") or not state.owner_away(drive, state.now()):
        return None
    seq = tool_input.get("record")
    report = cli.report_attrs(seq) if seq else {}
    order_id = reported_order(drive, tool_input, report)
    if order_id and drive["orders"][order_id]["incident"]:
        return None
    held = (
        f" Report {seq} is marked held: `drive status` lists it until the catch-up posts, and record {talk.hold(drive['name'], seq, report, order_id)}"
        f" told its writer so. Return NOTHING held for his catch-up."
        if seq
        else ""
    )
    return agents.Ruled(
        "Refused on purpose: he is away, since he said so or has written nothing for an hour, and while he is away only cards,"
        " incident lines and answers to his messages go out. Post nothing: this news joins the catch-up the drive posts"
        f" when he is back.{held}",
        AWAY_RULE,
    )


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


def unpromised(payload: dict) -> str | None:
    """Why a handler's post promises him something no order, job or due time carries."""
    tool_input = payload["tool_input"]
    if not payload.get("agent_id") or tool_input.get("card") or tool_input.get("tap_prs") or tool_input.get("order"):
        return None
    text = limits.masked(tool_input.get("text") or "")
    if not (promise := PROMISE.search(text)) or CARRIER.search(text):
        return None
    return agents.Ruled(
        f'refused: "{promise[0]}" promises him something, and this post names no order, job or due time that does it, so nothing'
        " holds the drive to it. Pass say's order=<the order that does it>, or name the time it lands, or drop the promise.",
        PROMISE_RULE,
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
    """One refusal holding each check's, so a single rewrite fixes them all, its rules numbered as its findings are so Jev judges each finding by its own; a refusal no Jev judges comes back alone, and a warning only when nothing refuses."""
    found = [refusal for refusal in refusals if refusal]
    refused = [refusal for refusal in found if not isinstance(refusal, Warned)]
    if unjudged := next((refusal for refusal in refused if isinstance(refusal, agents.Unjudged)), None):
        return unjudged
    if len(refused) < 2:
        return next(iter(refused or found), None)
    listed = "\n".join(f"{index}. {refusal.removeprefix('refused: ')}" for index, refusal in enumerate(refused, 1))
    rules = "\n".join(f"{index}. {refusal.rule}" for index, refusal in enumerate(refused, 1) if isinstance(refusal, agents.Ruled))
    return agents.Ruled(f"refused, {len(refused)} ways; fix each, then post the fixed words once:\n{listed}", rules)


def untapped(payload: dict) -> str | None:
    """Why a PR card asks him to tap a PR its `tap_prs` names that he tapped, or that merged or closed."""
    if not (tap_prs := payload["tool_input"].get("tap_prs")):
        return None
    prs = {key: pr for name in state.session_drives(payload["session_id"]) for key, pr in state.load(name)["prs"].items()}
    refusal = limits.tap_refusal("\n".join(tap_prs), prs)
    return refusal and agents.Ruled(refusal, OWED_TAP_RULE)


def claimed_tap(tool_input: dict, drive: dict) -> str | None:
    """Why a post's text or card says a PR waits on his tap or Approve with nothing for him to tap; a PR card's own PRs are `untapped`'s to check, since the post draws their tap card."""
    if tool_input.get("tap_prs"):
        return None
    card = tool_input.get("card") or {}
    words = "\n".join([tool_input.get("text") or "", card.get("title") or "", card.get("body") or "", *(card.get("options") or [])])
    refusal = view.tap_claim_refusal(drive, tool_input.get("order") or "", words, (view.live_cards(drive["name"]) or view.NO_CARDS)["pr_cards"])
    return refusal and agents.Ruled(f"{refusal}. A PR the drive owes his tap on and no open card asks gets its tap card from tap_prs on the post.", CARD_RULE)


def unlinked(payload: dict, drive: dict) -> str | None:
    """Why a handler's post names a PR or card the drive holds with no link where it is named; a PR card's line quotes his order and cc-slack draws its card, and the communicator's ack names nothing."""
    if not payload.get("agent_id") or payload["tool_input"].get("tap_prs"):
        return None
    refusal = limits.link_refusal(payload["tool_input"], limits.held(drive, payload["tool_input"].get("order")))
    return refusal and agents.Ruled(refusal, LINK_RULE)


def over_length(payload: dict, dm: str, messages: list[dict]) -> str | None:
    """Why the post is longer than he reads: a routine line, summary or card keeps its limit, an edit may reword its post at that post's length, and an answer quoting any message of his word for word as asked goes out whole."""
    tool_input = payload["tool_input"]
    if (refusal := limits.post_refusal(tool_input)) is None or tool_input.get("card"):
        return refusal
    try:
        refusal = limits.post_refusal(tool_input, edited_words(payload, dm))
    except slack.SlackError as error:
        refusal += f" The post it edits could not be read to check its length: {error}."
    if refusal is None:
        return None
    if not (asked := quotes(tool_input.get("asked") or "")):
        return refusal + WHOLE
    missing = unsaid(asked, " ".join(word["quote"] for word in thread_words(messages)))
    if len(missing) < len(asked):
        return None
    named = ", ".join(f'"{part}"' for part in missing)
    return (
        refusal + " It is not an answer, so it keeps that limit: asked lifts the limit only with his own words, and these stand"
        f" in no message of his in the thread this post goes to and no answer of his a card there shows: {named}."
        " Copy each message of his this post answers word for word, one per line, with … where you skip words."
    )


def denial(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def read_evidence(payload: dict) -> list[dict]:
    """What Jev weighs every handler's say post by beside his words, whatever the checks found: the reads the handler made itself and the report the post carries, since a line on where code lives, what it does, or a number or state stands only on a read or the record that measured it; none for any other post, such as the communicator's ack, or a card or PR card, which a record draws."""
    tool_input = payload["tool_input"]
    if payload["tool_name"] != SAY or not payload.get("agent_id") or tool_input.get("card") or tool_input.get("tap_prs") or his_drive(payload.get("session_id") or "") is None:
        return []
    report = [{"id": f"record:{seq}", "source": CARRIED_SOURCE, "quote": agents.record(seq)}] if (seq := tool_input.get("record")) else []
    return [own_reads(payload, tool_input.get("text") or ""), *report]


def own_reads(payload: dict, said: str) -> dict:
    """The handler's own reads as one evidence item: each tool call of its transcript whose result came back, its posts aside, and for each name the post uses the line of each read's result holding it, so Jev sees a name no read returned, and one only a comment, a panic message or a doc holds."""
    calls: dict[str, str] = {}
    results: list[tuple[list[str], str]] = []
    for row in agents.tail(agents.transcript(payload)):
        for part in agents.parts(row):
            if part.get("type") == "tool_use" and part.get("name") not in DM_POSTS | CHANNEL_POSTS:
                calls[part["id"]] = f"{part['name'].rpartition('__')[2]} {render.cut(json.dumps(part.get('input'), ensure_ascii=False), CALL_SHOWN)}"
            elif part.get("type") == "tool_result" and not part.get("is_error") and part.get("tool_use_id") in calls:
                lines = [calls[part["tool_use_id"]], *result_text(part).splitlines()]
                results.append((lines, "\n".join(lines).casefold()))
    listed = [f"[{index}] {lines[0]}" for index, (lines, _) in enumerate(results, 1)] or ["none: no call of the handler's returned a result"]
    held = []
    for name in names(said):
        found = [f"[{index}] {render.cut(line, LINE_SHOWN)}" for index, (lines, folded) in enumerate(results, 1) if (line := holding(pieces(name), lines, folded))]
        held.append(f"{name}: {' | '.join(found) or 'no read returned it'}")
    return {"id": "reads:own", "source": READS_SOURCE, "quote": "\n".join(["Reads:", *listed, "Names the post uses:", *(held or ["none"])])}


def names(said: str) -> list[str]:
    """Each name a post uses: a code span, a word with `_`, `.`, `/` or `::` inside it, such as a path, a function or a number, and a capitalized word not opening a sentence, such as a product."""
    found = CODE_SPAN.findall(said)
    for sentence in SENTENCE_BREAK.split(LINKED.sub(" ", refs.REF.sub(" ", CODE_SPAN.sub(" ", said)))):
        for index, word in enumerate(word.strip(EDGES) for word in sentence.split()):
            if INNER.search(word) or (index and len(word) > 1 and word[0].isupper()):
                found.append(word)
    return list(dict.fromkeys(found))


def pieces(name: str) -> list[str]:
    """The words a read must hold, on one line, to return a name, case aside: each identifier in it, so `journalValues.PutAt` is returned by `func (v *journalValues) PutAt` and a path by the call reading it, a `<placeholder>` standing for any words; the whole name when it holds none, such as a number."""
    return [word for word in WORD.findall(PLACE.sub(" ", name.casefold())) if len(word) > 2] or [name.casefold()]


def holding(words: list[str], lines: list[str], folded: str) -> str | None:
    """The first line of a read, its call then its result, holding every word."""
    if not all(word in folded for word in words):
        return None
    return next((line for line in lines if all(word in line.casefold() for word in words)), None)


def main() -> None:
    payload = agents.seen(json.load(sys.stdin))
    try:
        reason, sent = checked(payload)
        reads = [] if isinstance(reason, agents.Unjudged) else read_evidence(payload)
        heard = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": reason}} if isinstance(reason, Warned) else {}
        reason = "" if heard else reason
        judged = {"his_words": his_words(payload), "reads": reads, "post": sent} if (reason or reads) and not isinstance(reason, agents.Unjudged) else {}
    except Exception as error:
        reason, heard, judged = (
            f"refused: the drive's message check failed on this post with {type(error).__name__}: {error}, so it does not go"
            " out unchecked. Name it on a DEFECT line, and the post goes out once the check's fix is installed."
        ), {}, {}
    refused = denial(reason) | agents.ruled(reason) if reason else {}
    if output := heard | refused | judged:
        print(json.dumps(output))


if __name__ == "__main__":
    main()
