import json
import re
from pathlib import Path

from drive import dashboard, picture, reads, render, settled, state, view
from drive.limits import REF
from drive.state import DriveError

ACK = re.compile(r"\bAck: (https://\S+)")
RAW_ANSWER = re.compile(r"^\d+:\d+ commit-sha '([0-9a-f]{7,40})'")
OPEN_LINK = re.compile(r"<((?:https?://|mailto:)[^<>|\s]*)\|[^<>]*\Z")
INNER = re.compile(r"<[^<>]*>?")
CODE = re.compile(r"`[^`\n]+`")
LINK_WORDS = str.maketrans({"<": "", ">": "", "|": "/", "[": "(", "]": ")"})
THIS_CARD = "this card"
JOB_START = "(job start)"
CAUSE = re.compile(
    r"\b(?:because|caus(?:ed|es|ing)|due to|culprit)\b"
    r"|\bcause(?:\s+(?:is|was)|:)\s+(?!(?:yes|still|being|unknown|unclear|pending|tbd|not yet|under (?:way|investigation|review))\b|an?\s+(?:read|reader|lane|agent)\s+(?:is|has)\b)",
    re.IGNORECASE,
)
STATE = re.compile(
    r"\b(?:cover(?:s|ed)\s+(?:it|this|that|the|his|your|both|each|every|by)|fixed|merged|landed|(?:en)?queued|shipped|deployed|resolved|rolled (?:out|back))\b"
    r"|\b(?:is|are|was|were|went|now|already)\s+live\b",
    re.IGNORECASE,
)
UNVERIFIED = re.compile(r"\bunverified\b", re.IGNORECASE)
UNSTATED = re.compile(r"\b(?:once|when|after|until|unless|if|whether|not|never|yet|will|would|to be)\b|n['\u2019]t\b", re.IGNORECASE)
FAILURE = re.compile(
    r"\b(?:refused|failed to|missed|disconnected|dropped (?:its|the|their) connection|forgot(?:ten)?|lost track|fell through|went unchecked|slipped"
    r"|(?:nobody|no one) (?:\w+ed|ran|saw|read|took|got|drew|caught|knew)"
    r"|never (?:checked|ran|reached|heard|started|drew|posted|landed|got|went out))\b",
    re.IGNORECASE,
)
DRIVE_PART = re.compile(
    r"\b(?:cards?|checks?|hooks?|builders?|handlers?|designers?|watch|coordinator|communicator|meta|drive|lanes?|skill|cci|say)\b",
    re.IGNORECASE,
)
PREVENTION = re.compile(r"\{(?:order|pr):|github\.com/[^/\s]+/[^/\s]+/pull/\d+|\bo\d+\b")
QUOTED = re.compile(r'"[^"\n]*"|\u201c[^\u201d\n]*\u201d')
CLAUSE_END = re.compile(r"[,;:.!?](?=\s|$)")
ABSENT_RECORD = re.compile(
    r"\b(?:no|nothing in (?:the|his|your|any)|not in (?:the|his|your|any)|never in (?:the|his|your|any))\s+(?:[\w-]+\s+){0,3}?"
    r"(?:plans?|rulings?|recorded answers?|answers? of his|cc-notes|records? of (?:his|him|you|your))\b"
    r"|\b(?:plan|records?|rulings?)\s+(?:does|do|did)(?:\s*not|n['’]t)\s+(?:say|name|mention|ask|cover|settle)",
    re.IGNORECASE,
)
RECORD_WORDS = 10


def resolved(name: str, text: str) -> dict[str, dict]:
    """Each reference the text holds, read from the drive: {card:<waits id>}, {pr:<number, owner/repo#number or URL>}, {order:<id>}, {record:<seq>}, {answer:<id>}, and {started:<job>} and {at:<seq>}, the time a job started, labelled as the job's start, and the time a record went out, with the words that name it, the words after a | when it has them beside its own title, and where it opens; one that names nothing, or a reference or link inside a raw link's words, refuses the post."""
    if refusal := nested(text):
        raise DriveError(refusal)
    found = {}
    cards = None
    data = None
    for match in REF.finditer(text):
        if match[0] in found:
            continue
        label = (match[3] or "").strip()
        data = data or state.load(name)
        match match[1]:
            case "card":
                cards = cards or drawn_cards(name)
                found[match[0]] = card(cards, data, match[2])
            case "pr":
                found[match[0]] = pr(data["prs"], match[2], label)
            case "order":
                found[match[0]] = order(name, data, match[2])
            case "record":
                found[match[0]] = record(name, match[2])
            case "answer":
                found[match[0]] = answer(data, match[2])
            case "started":
                found[match[0]] = started(data, match[2], label)
            case "at":
                found[match[0]] = posted_at(name, match[2])
        if label:
            found[match[0]] |= {"own": found[match[0]]["title"], "title": label}
    return found


def nested(text: str) -> str | None:
    """Why the text, outside its code, writes a reference, link or mention inside the words of a raw <url|words> link, which Slack draws as a literal <, a link whose words are its own URL, and a literal >."""
    plain = CODE.sub("", picture.FENCE.sub("", text))
    for inner in sorted([*REF.finditer(plain), *INNER.finditer(plain)], key=lambda found: found.start()):
        if outer := OPEN_LINK.search(plain, 0, inner.start()):
            return (
                f"{inner[0]} sits inside the words of the link to {outer[1]}, and a link's words cannot hold a link, which Slack"
                f" draws broken: give that link a few words of its own and close it with >, then put {inner[0]} after it"
            )
    return None


def unbacked(text: str) -> list[str]:
    """Each line of the text, outside a fence, a quote and a quoted line, that states a cause with no {record:…} on it: a because, due to, caused by, culprit or the cause is, never a line naming the cause as the question or as what a read is on."""
    return [
        line.strip()
        for line in picture.FENCE.sub("", text).splitlines()
        if not line.lstrip().startswith(">") and CAUSE.search(QUOTED.sub("", line)) and "{record:" not in line
    ]


def unread_states(text: str) -> list[str]:
    """Each line of the text, outside a fence, a quote and a quoted line, that tells him a state of work as true now with no {record:…} on it: fixed, covering his case, merged, landed, queued, shipped, deployed, resolved, rolled out or back, or live, never in a reference's own words, a line saying it is unverified, a question, or a clause hedging it before the word as future, conditional or negated."""
    return [
        line.strip()
        for line in picture.FENCE.sub("", text).splitlines()
        if not line.lstrip().startswith(">") and "{record:" not in line and not UNVERIFIED.search(line) and stated(QUOTED.sub("", REF.sub("", line)))
    ]


def stated(line: str) -> bool:
    for found in STATE.finditer(line):
        clause = max((end.end() for end in CLAUSE_END.finditer(line, 0, found.start())), default=0)
        closing = CLAUSE_END.search(line, found.end())
        if not UNSTATED.search(line, clause, found.start()) and not (closing and closing[0] == "?"):
            return True
    return False


def unprevented(text: str, found: dict[str, dict]) -> list[str]:
    """Each line of the text, outside a fence, a quote and a quoted line, that names a failure, refusal or gap of the drive's own, a card, check, hook, lane or session role of it, or cites the defect record the drive filed for one, when the text outside its fences names no order or PR carrying the tool change that prevents it; found holds each reference the text makes, as resolved reads it."""
    told = picture.FENCE.sub("", text)
    if PREVENTION.search(told):
        return []
    return [
        line.strip()
        for line in told.splitlines()
        if not line.lstrip().startswith(">")
        and (
            FAILURE.search(plain := QUOTED.sub("", line))
            and DRIVE_PART.search(plain)
            or any(found[match[0]].get("defect") for match in REF.finditer(line) if match[1] == "record")
        )
    ]


def prevented(text: str) -> bool:
    """Whether a line of the text, outside a fence, a quote and a quoted line, names a cause or a failure of the drive's own while a line outside its fences and quotes carries the order or PR preventing it, the post his ruling on failures asks for."""
    told = [line for line in picture.FENCE.sub("", text).splitlines() if not line.lstrip().startswith(">")]
    return any(PREVENTION.search(line) for line in told) and any(
        CAUSE.search(plain := QUOTED.sub("", line)) or FAILURE.search(plain) and DRIVE_PART.search(plain) for line in told
    )


def record_excuses(text: str) -> list[str]:
    """Each line of the text, outside a fence, a quote and a quoted line, that answers him by a plan, record or answer of his that is missing."""
    return [
        line.strip()
        for line in picture.FENCE.sub("", text).splitlines()
        if not line.lstrip().startswith(">") and ABSENT_RECORD.search(QUOTED.sub("", line))
    ]


def drawn_cards(name: str) -> dict:
    try:
        return view.cards(name)
    except view.CardsError as error:
        raise DriveError(f"a {{card:…}} reference needs the drive's cards, which cc-slack could not list: {error}") from None


def card(cards: dict, data: dict, waits: str) -> dict:
    """A card by its waits id: Slack draws it under a link naming it, and the dashboard opens that card in the queue it waits in. A question card he answered or the drive withdrew keeps its permalink, so it is found among the questions the drive drew, and the dashboard opens the task it was asked for; any other card he answered or cc-slack closed is found among the closed cards cc-slack keeps, and the dashboard opens it in Slack."""
    for entry in reversed([*cards["questions"], *cards["pr_cards"]]):
        if entry["card"].get("waits") == waits:
            queue = "tap" if waits.startswith("merge:") else "question"
            ts = view.message_key(entry["url"])
            return {"kind": "card", "title": entry["card"]["title"], "url": entry["url"], "board": f"#/queue/{queue}/{ts[:-6]}.{ts[-6:]}"}
    for job in data["jobs"].values():
        for question in reversed(job["questions"]):
            if question["waits"] == waits and question["card"]:
                return {"kind": "card", "title": question["title"] or "his question card", "url": question["card"], "board": f"#/feature/{job['order']}"}
    if closed := view.closed_card(cards, waits):
        return {"kind": "card", "title": closed["title"], "url": closed["url"], "board": closed["url"]}
    raise DriveError(
        f"{{card:{waits}}} names no card the drive drew, open or settled; `cc-slack drive-cards --drive <drive> | jq -r '(.questions[], .pr_cards[] | .card),"
        " .closed[] | [.waits, .status // \"closed\", .title] | @tsv' | grep -i '<words of its title>'` prints each card's waits id, state and title on one line"
    )


def pr(prs: dict, ref: str, label: str) -> dict:
    """A PR the drive holds, named by what it does, or any other PR named by its owner/repo#number or URL, which has no title of its own here, under the words the post gives it."""
    number = ref.removeprefix("#")
    keys = [key for key in prs if key.endswith(f"#{number}")] if number.isdigit() else [state.pr_key(ref)]
    if len(keys) > 1:
        raise DriveError(f"{{pr:{ref}}} could be {' or '.join(keys)}: name it as owner/repo#{number}")
    if keys and keys[0] in prs:
        found = prs[keys[0]]
        return {"kind": "pr", "title": view.pr_title(found) if found["title"] else keys[0], "url": found["url"], "board": found["url"]}
    if keys and label:
        url = state.pr_url(keys[0])
        return {"kind": "pr", "title": "", "url": url, "board": url}
    raise DriveError(
        f"{{pr:{ref}}} names no PR of the drive's, which drive status lists; name any other PR as"
        " {pr:<owner/repo#number>|<words saying what it does>}"
    )


def order(name: str, data: dict, order_id: str) -> dict:
    """An order of his under the first line of his words: Slack links the thread its lines go in, or its dashboard page when it has none, and the dashboard opens its page."""
    if order_id not in data["orders"]:
        raise DriveError(f"{{order:{order_id}}} names no order of the drive's; drive status lists them")
    board = f"#/feature/{order_id}"
    url = view.thread_url(data, view.stored_cards(name)[0]["team_url"], data["orders"][order_id]) or dashboard.link(name) + board
    return {"kind": "order", "title": view.plain(data["orders"][order_id]["text"], view.TITLE_CHARS), "url": url, "board": board}


def record(name: str, seq: str) -> dict:
    """A cci record under the opening of its own words, so a cause links the measurement behind it in a few words and the record's whole text never joins the post: a link to what the record measured, its url, which report_refusals keeps off a report naming his message, or those words alone when it links nothing."""
    found = shown(name, "record", seq)
    url = found["refs"].get("url")
    return {"kind": "record", "title": opening(found["text"]), "url": url, "board": url, "defect": found["kind"] == "defect"}


def records(name: str, text: str) -> dict[str, dict]:
    """Each {record:<seq>} reference the text holds, read from cci as resolved reads it."""
    return {match[0]: record(name, match[2]) for match in REF.finditer(text) if match[1] == "record"}


def answer(data: dict, answer_id: str) -> dict:
    """A ruling of his by the id cc-notes keeps it under, which means nothing to him: his words under the time he said them, linked to the ack of them in his DM."""
    found = settled.shown(answer_id, data["roles"]["coordinator"]["cwd"])
    said = render.when(state.parse_time(found["created_at"]).astimezone().isoformat())
    url = ack[1] if (ack := ACK.search(found["body"])) else None
    return {"kind": "answer", "title": f'his {said} ruling "{opening(found["body"].splitlines()[0])}"', "url": url, "board": url}


def answer_ids(findings: list[str], data: dict) -> list[str]:
    """The findings but each commit-sha one on the id of a recorded answer of his, which `ccn answer show` reads and is no commit."""
    return [finding for finding in findings if not ((raw := RAW_ANSWER.match(finding)) and answered(raw[1], data))]


def answered(answer_id: str, data: dict) -> bool:
    try:
        settled.shown(answer_id, data["roles"]["coordinator"]["cwd"])
    except DriveError:
        return False
    return True


def started(data: dict, job_id: str, label: str) -> dict:
    """When a job started, the stamp the drive wrote as it added the job, so no session types the time, labelled as the job's start so it never reads as when anything the job did started, such as a release; words after a | would replace that label, and refuse the post."""
    if job_id not in data["jobs"]:
        raise DriveError(f"{{started:{job_id}}} names no job of the drive's; drive status lists them")
    if label:
        raise DriveError(
            f"{{started:{job_id}|{label}}} would draw its words in place of the job's start: {{started:{job_id}}} draws only when that job"
            " started, labelled so, and takes no words after a |"
        )
    return {"kind": "started", "title": f"{render.when(data['jobs'][job_id]['created'])} {JOB_START}", "url": None, "board": None}


def posted_at(name: str, seq: str) -> dict:
    """When a cci record went out, its own stamp read in local time, so no session types the time."""
    moment = state.parse_time(shown(name, "at", seq)["at"]).astimezone()
    return {"kind": "at", "title": render.when(state.stamp(moment)), "url": None, "board": None}


def shown(name: str, kind: str, seq: str) -> dict:
    try:
        return json.loads(reads.run(["cci", "show", seq, "--json", "--drive", name], Path.cwd()))
    except reads.ReadError as error:
        raise DriveError(f"{{{kind}:{seq}}} could not be read: {error}") from None


def opening(text: str) -> str:
    """The first clause of a record's first line, at most ten words, which a link's words in his posts rarely pass."""
    words = CLAUSE_END.split(view.plain(text, len(text)), maxsplit=1)[0].split()
    return " ".join(words[:RECORD_WORDS]) + ("…" if len(words) > RECORD_WORDS else "")


def slack(text: str, found: dict[str, dict], thread: str | None) -> str:
    """The text as Slack shows it in this thread of his DM, None for its root: each reference a link under the words that name it, which Slack draws a card under, or a record that links nothing as its words; a card named in its own thread reads as the words after its | or as "this card", with no link, since a link there draws the card again under the post. His words: "why are you showing me a card in the thread of the same post that this is a thread of"."""

    def shown(match: re.Match[str]) -> str:
        ref = found[match[0]]
        if ref["kind"] == "card" and thread and view.message_key(ref["url"]) == thread.replace(".", ""):
            return (match[3] or THIS_CARD).strip().translate(LINK_WORDS)
        return linked(ref, ref["url"], "<{url}|{words}>")

    return REF.sub(shown, text)


def board(text: str, found: dict[str, dict]) -> str:
    """The text as the dashboard shows it: each reference a link to the page or PR it names, or a record that links nothing as its words."""
    return REF.sub(lambda match: linked(found[match[0]], found[match[0]]["board"], "[{words}]({url})"), text)


def linked(ref: dict, url: str | None, form: str) -> str:
    words = ref["title"].translate(LINK_WORDS)
    return form.format(url=url, words=words) if url else words
