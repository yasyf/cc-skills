import html
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from drive import agents, limits, picture, refs, render, slack, state, view
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
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n+")
PROMISE = re.compile(r"\b(?:from now on|next you(?:['’]ll| will)? get|I(?:['’]ll| will))\b", re.IGNORECASE)
CARRIER = re.compile(r"\b[oj]\d+\b|\b\d{1,2}:\d{2}\s*[AP]M\b", re.IGNORECASE)
PROMISED_TIME = re.compile(
    r"\b(?:by|before|next (?:line|update|post|summary)(?:\s+(?:by|at))?)\s+(?:about\s+|around\s+)?(\d{1,2}):(\d{2})\s*([AP]M)\b", re.IGNORECASE
)
AHEAD = timedelta(hours=12)
SWITCH = re.compile(r"orca terminal switch --terminal[= ]([^\s`'\"<>|]+)")
WAITING = "WAITING-ON-HIM"
TITLED = (
    " {} of the words counted are the titles its {{card:…}}, {{pr:…}}, {{order:…}} and {{record:…}} references draw as their words;"
    " {{pr:<number>|<words>}} names one in fewer words of your own."
)
UNREAD = (
    "refused: {why}, so this line could miss what he said there since. Read it with slack_thread(channel_id={dm},"
    " thread_ts={thread}, limit=1000), then post. When the tool saves a long thread to a file, the call is not a read"
    " until you open that file and read its newest messages."
)
NO_READ = "your transcript shows no read of his thread {thread}, and a read by another agent or session does not count for yours"
OLD_READ = "your last read of his thread {thread} was {minutes} minutes ago, and a read counts for 10"
FAILED_READ = "your slack_thread call on his thread {thread} failed"
UNOPENED_READ = "your read of his thread {thread} was saved to {file}, which you have not opened since"
WHOLE = (
    " It names no message of his as asked, so it is not an answer and keeps that limit. An answer to a question or"
    " order of his goes out whole, at any length: pass as asked each message of his it answers, word for word, one per line."
)


def his_drive(session: str) -> dict | None:
    """The drive this session is the communicator of, whose handlers write everything he reads in his DM with the app."""
    for name in state.session_drives(session):
        data = state.load(name)
        if state.session_role(data, session) == "communicator":
            return data
    return None


def coordinator_post(payload: dict) -> str | None:
    """Why the coordinator may not post to Slack itself: his reply under a post reaches only the session that posted it, and the communicator is the one he talks to."""
    session = payload.get("session_id") or ""
    if payload.get("agent_id"):
        return None
    for name in state.session_drives(session):
        if state.session_role(state.load(name), session) == "coordinator":
            return (
                "Refused on purpose: the coordinator posts nothing to Slack, since his reply under a post reaches only the"
                f" session that posted it. Send the words as a `report` record to communicator@{name}, and its handler"
                " posts them."
            )
    return None


def posts_to(payload: dict, dm: str) -> bool:
    if payload["tool_name"] in DM_POSTS:
        return True
    tool_input = payload["tool_input"]
    channel = tool_input.get("channel_id") or next(iter(PERMALINK_CHANNEL.findall(tool_input.get("url") or "")), None)
    return payload["tool_name"] in CHANNEL_POSTS and channel == dm


def last_read(path: Path, dm: str, thread: str) -> tuple[datetime | None, dict[str, datetime], bool]:
    """When this handler last read the thread, by its own transcript's timestamped turns, never a row such as an API request's copy that carries none: a slack_thread call on it that did not fail, its channel_id his DM or left out, which cc-slack reads as his DM; when the tool saved a long thread to a file, only once a later call of this handler opened that file. Also the saved files it never opened, and whether a call on it failed."""
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
                if part.get("type") == "tool_use" and part.get("name") == THREAD_READ and (part["input"].get("channel_id") or dm, part["input"].get("thread_ts")) == (dm, thread):
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
    if not payload.get("agent_id") or payload["tool_name"] == EDIT or tool_input.get("card") or not (thread := tool_input.get("thread_ts")):
        return None
    read, unopened, failed = last_read(agents.transcript(payload), dm, thread)
    if read and datetime.now(UTC) - read < READ_WINDOW:
        return None
    if unopened:
        why = UNOPENED_READ.format(thread=thread, file=max(unopened, key=unopened.__getitem__))
    elif read:
        why = OLD_READ.format(thread=thread, minutes=int((datetime.now(UTC) - read) / timedelta(minutes=1)))
    else:
        why = (FAILED_READ if failed else NO_READ).format(thread=thread)
    return UNREAD.format(why=why, dm=dm, thread=thread)


def unchecked(tool_input: dict) -> str | None:
    """What `cc-slack check-post` finds in the exact words the post sends; a post that draws a card, or a PR's tap card through tap_prs, carries the card its words say waits on him."""
    if not (text := tool_input.get("text") or ""):
        return None
    try:
        findings = slack.post_findings(text, bool(tool_input.get("card") or tool_input.get("tap_prs")))
    except slack.SlackError as error:
        return f"refused: `cc-slack check-post` could not check this post's words, so it does not go out: {error}."
    if not findings:
        return None
    return "refused: `cc-slack check-post` finds this in the post's exact words; fix each, then post the fixed words:\n" + "\n".join(findings)


def his_messages(payload: dict, dm: str) -> str:
    """What he wrote in the thread the post goes to, read from Slack; in his DM with the app every message not the bot's is his."""
    tool_input = payload["tool_input"]
    if tool_input.get("thread_ts"):
        where = ("--channel", dm, "--thread", tool_input["thread_ts"])
    elif tool_input.get("url"):
        where = ("--url", tool_input["url"])
    else:
        return ""
    return " ".join(message["text"] for message in slack.thread(*where, "--limit", str(slack.WHOLE_THREAD)) if not message.get("from_bot"))


def edited_words(payload: dict, dm: str) -> int:
    """How long the post an edit replaces is, read from Slack; 0 for a new post."""
    if payload["tool_name"] != EDIT:
        return 0
    tool_input = payload["tool_input"]
    if url := tool_input.get("url"):
        return limits.words(slack.message(url)["text"])
    ts = tool_input["ts"]
    return limits.words(slack.message_at(ts, "--channel", dm, "--thread", tool_input.get("thread_ts") or ts)["text"])


def squashed(text: str) -> str:
    return SPACE.sub(" ", html.unescape(text).translate(STRAIGHT)).strip().casefold()


def quotes(asked: str | list[str]) -> list[str]:
    """Each stretch of his words that asked quotes: one message of his per entry or line, cut where the quote skips some of them."""
    return [part for entry in messages(asked) for piece in BETWEEN_QUOTES.split(entry) if (part := piece.strip().strip(QUOTE_MARKS).strip())]


def messages(asked: str | list[str]) -> list[str]:
    """The messages of his asked holds: a list's entries, a string that is a JSON list of strings, as a list arrives through a tool whose schema declares no asked, or the one string."""
    if isinstance(asked, list):
        return asked
    try:
        listed = json.loads(asked)
    except json.JSONDecodeError:
        return [asked]
    return listed if isinstance(listed, list) and all(isinstance(entry, str) for entry in listed) else [asked]


def unsaid(parts: list[str], said: str) -> list[str]:
    said = squashed(said)
    return [part for part in parts if squashed(part) not in said]


def deny_reason(payload: dict) -> str | None:
    """Why a post to his DM does not go out as written: the coordinator posts nothing, a handler's line in his thread waits for its read of that thread, and then, refused at once with every finding, the post keeps its length, links what it names, asks no tap that is not owed, and `cc-slack check-post` passes its exact words."""
    if posted := coordinator_post(payload):
        return posted
    if (drive := his_drive(payload.get("session_id") or "")) is None:
        return None
    dm = drive["slack"]["channel"]
    titled = 0
    if payload["tool_name"] == SAY and refs.REF.search(said := payload["tool_input"].get("text") or ""):
        try:
            found = refs.resolved(drive["name"], said)
        except DriveError as error:
            return str(error)
        titled = limits.words(refs.slack(" ".join(match[0] for match in refs.REF.finditer(said)), found))
        payload = {**payload, "tool_input": {**payload["tool_input"], "text": refs.slack(said, found)}}
    tool_input = payload["tool_input"]
    if PLACEHOLDER.fullmatch(text := (tool_input.get("text") or "").strip()):
        return (
            f"The text {text} is a placeholder, and Slack would send it as written. Pass the post's own words as text,"
            " the exact words `cc-slack check-post -` passed."
        )
    if not posts_to(payload, dm):
        return None
    if refusal := card_edit(payload, drive) or held_while_away(payload, drive) or unread(payload, dm):
        return refusal
    if (long := over_length(payload, dm)) and titled:
        long += TITLED.format(titled)
    return every(
        [
            long,
            long_ack(payload),
            unlinked(payload, drive),
            untapped(payload),
            unpromised(payload),
            undue(payload, drive),
            raw_visuals(tool_input),
            unswitched(tool_input, drive),
            unchecked(tool_input),
        ]
    )


def card_edit(payload: dict, drive: dict) -> str | None:
    """Why an edit may not touch his question card: an edit with text alone replaces its layout and buttons with plain text, and drawing it again under its waits id edits it where it sits."""
    if payload["tool_name"] != EDIT:
        return None
    tool_input = payload["tool_input"]
    key = view.message_key(tool_input["url"]) if tool_input.get("url") else (tool_input.get("ts") or "").replace(".", "")
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


def held_while_away(payload: dict, drive: dict) -> str | None:
    """Why a handler's line waits while he is away: only cards, the WAITING-ON-HIM line, incident lines and answers to his messages go out, and the rest joins his catch-up."""
    tool_input = payload["tool_input"]
    if not payload.get("agent_id") or payload["tool_name"] == EDIT or tool_input.get("card") or tool_input.get("tap_prs") or tool_input.get("asked"):
        return None
    if WAITING in (tool_input.get("text") or "") or not state.owner_away(drive, state.now()):
        return None
    if (found := posted_order(drive, tool_input)) and found[1]["incident"]:
        return None
    return (
        "Refused on purpose: he is away, since he said so or has written nothing for an hour, and while he is away only cards,"
        " incident lines and answers to his messages go out. Post nothing: this news joins the catch-up the coordinator sends"
        " when he is back."
    )


def long_ack(payload: dict) -> str | None:
    """Why the communicator's own ack runs past one sentence: it goes out inside seconds, and the handler answers the rest."""
    tool_input = payload["tool_input"]
    if payload.get("agent_id") or payload["tool_name"] == EDIT or tool_input.get("card") or tool_input.get("tap_prs"):
        return None
    sentences = [part for part in SENTENCE_BREAK.split(limits.masked(tool_input.get("text") or "").strip()) if part.strip()]
    if len(sentences) < 2:
        return None
    return (
        f"refused: this ack is {len(sentences)} sentences, and an ack is one: what he asked, in his words, and what happens next."
        " Post that one sentence and put the rest in the handler's brief; the handler answers him in this thread."
    )


def unpromised(payload: dict) -> str | None:
    """Why a handler's post promises him something no order, job or due time carries."""
    tool_input = payload["tool_input"]
    if not payload.get("agent_id") or tool_input.get("card") or tool_input.get("tap_prs") or tool_input.get("order"):
        return None
    text = limits.masked(tool_input.get("text") or "")
    if not (promise := PROMISE.search(text)) or CARRIER.search(text):
        return None
    return (
        f'refused: "{promise[0]}" promises him something, and this post names no order, job or due time that does it, so nothing'
        " holds the drive to it. Pass say's order=<the order that does it>, or name the time it lands, or drop the promise."
    )


def undue(payload: dict, drive: dict) -> str | None:
    """Why a handler's post promises a clock time on an order the drive holds no due time for."""
    tool_input = payload["tool_input"]
    if not payload.get("agent_id") or tool_input.get("card") or tool_input.get("tap_prs") or not (found := posted_order(drive, tool_input)):
        return None
    order_id, order = found
    if state.owed(order) or order["next_due"]:
        return None
    moment = state.now()
    for said in PROMISED_TIME.finditer(limits.masked(tool_input.get("text") or "")):
        at = moment.replace(hour=int(said[1]) % 12 + (12 if said[3].upper() == "PM" else 0), minute=int(said[2]), second=0, microsecond=0)
        if (at - moment) % timedelta(days=1) <= AHEAD:
            return (
                f'refused: "{said[0]}" promises him a time on {order_id}, which holds no due time, so the watch cannot hold the drive'
                f" to it. Run `drive order set {order_id} --due {at:%H:%M}` first, then post."
            )
    return None


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
    return (
        f'refused: the code visual holding "{render.cut(" ".join(source.split()), 60)}" is {what}, raw data or tool output, and a'
        f" picture of it shows him nothing: say what it means in words, show a short snippet of real code, or {picture.INSTEAD}"
        f" ({limits.RAW_RULING})"
    )


def unswitched(tool_input: dict, drive: dict) -> str | None:
    """Why a post gives him a switch command for a terminal no session of the drive runs in."""
    held = {role.get("terminal") for role in drive["roles"].values()}
    if not (wrong := [handle for handle in SWITCH.findall(tool_input.get("text") or "") if handle not in held]):
        return None
    return (
        f"refused: `orca terminal switch --terminal {wrong[0]}` names a terminal no session of this drive runs in. Give him a switch"
        " command only as the role's line `drive status` prints it, word for word, and none when that line carries none."
    )


def every(refusals: list[str | None]) -> str | None:
    """One refusal holding each check's, so a single rewrite fixes them all."""
    found = [refusal for refusal in refusals if refusal]
    if len(found) < 2:
        return next(iter(found), None)
    listed = "\n".join(f"{index}. {refusal.removeprefix('refused: ')}" for index, refusal in enumerate(found, 1))
    return f"refused, {len(found)} ways; fix each, then post the fixed words once:\n{listed}"


def untapped(payload: dict) -> str | None:
    """Why a PR card asks him to tap a PR its `tap_prs` names that he tapped, or that merged or closed."""
    if not (tap_prs := payload["tool_input"].get("tap_prs")):
        return None
    prs = {key: pr for name in state.session_drives(payload["session_id"]) for key, pr in state.load(name)["prs"].items()}
    return limits.tap_refusal("\n".join(tap_prs), prs)


def unlinked(payload: dict, drive: dict) -> str | None:
    """Why a handler's post names a PR or card the drive holds with no link where it is named; a PR card's line quotes his order and cc-slack draws its card, and the communicator's ack names nothing."""
    if not payload.get("agent_id") or payload["tool_input"].get("tap_prs"):
        return None
    return limits.link_refusal(payload["tool_input"], limits.held(drive, payload["tool_input"].get("order")))


def over_length(payload: dict, dm: str) -> str | None:
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
    if len(unbriefed := unsaid(asked, agents.brief(payload))) < len(asked):
        return None
    try:
        missing = unsaid(unbriefed, his_messages(payload, dm))
    except slack.SlackError as error:
        return refusal + f" His thread could not be read to check asked, so it is not yet an answer: {error}."
    if len(missing) < len(asked):
        return None
    named = ", ".join(f'"{part}"' for part in missing)
    return (
        refusal + " It is not an answer, so it keeps that limit: asked lifts the limit only with his own words, and these stand"
        f" in no message of his in the thread this post goes to, nor in your brief: {named}. Copy each message of his this post"
        " answers word for word, one per line, with … where you skip words."
    )


def denial(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def main() -> None:
    payload = json.load(sys.stdin)
    try:
        reason = deny_reason(payload)
    except Exception as error:
        reason = (
            f"refused: the drive's message check failed on this post with {type(error).__name__}: {error}, so it does not go"
            " out unchecked. Name it on a DEFECT line, and the post goes out once the check's fix is installed."
        )
    if reason:
        print(json.dumps(denial(reason)))


if __name__ == "__main__":
    main()
