import json
import re
from pathlib import Path

from drive import dashboard, picture, reads, state, view
from drive.state import DriveError

REF = re.compile(r"\{(card|pr|order|record):([^{}\s|]+)(?:\|([^{}]+))?\}")
OPEN_LINK = re.compile(r"<((?:https?://|mailto:)[^<>|\s]*)\|[^<>]*\Z")
INNER = re.compile(r"<[^<>]*>?")
CODE = re.compile(r"`[^`\n]+`")
LINK_WORDS = str.maketrans({"<": "", ">": "", "|": "/", "[": "(", "]": ")"})
CAUSE = re.compile(r"\b(?:because|caus(?:e|ed|es|ing)|due to|culprit)\b", re.IGNORECASE)
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
    """Each reference the text holds, read from the drive: {card:<waits id>}, {pr:<number, owner/repo#number or URL>}, {order:<id>} and {record:<seq>}, with the words that name it, the words after a | when it has them, and where it opens; one that names nothing, or a reference or link inside a raw link's words, refuses the post."""
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
                cards = cards or open_cards(name)
                found[match[0]] = card(cards, data, match[2])
            case "pr":
                found[match[0]] = pr(data["prs"], match[2], label)
            case "order":
                found[match[0]] = order(name, data, match[2])
            case "record":
                found[match[0]] = record(name, match[2])
        if label:
            found[match[0]]["title"] = label
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
    """Each line of the text, outside a fence, a quote and a quoted line, that names a cause with no {record:…} on it."""
    return [
        line.strip()
        for line in picture.FENCE.sub("", text).splitlines()
        if not line.lstrip().startswith(">") and CAUSE.search(QUOTED.sub("", line)) and "{record:" not in line
    ]


def record_excuses(text: str) -> list[str]:
    """Each line of the text, outside a fence, a quote and a quoted line, that answers him by a plan, record or answer of his that is missing."""
    return [
        line.strip()
        for line in picture.FENCE.sub("", text).splitlines()
        if not line.lstrip().startswith(">") and ABSENT_RECORD.search(QUOTED.sub("", line))
    ]


def open_cards(name: str) -> list[dict]:
    try:
        listed = view.cards(name)
    except view.CardsError as error:
        raise DriveError(f"a {{card:…}} reference needs the drive's cards, which cc-slack could not list: {error}") from None
    return [*listed["questions"], *listed["pr_cards"]]


def card(cards: list[dict], data: dict, waits: str) -> dict:
    """A card by its waits id: Slack draws it under a link naming it, and the dashboard opens that card in the queue it waits in. A question card he answered or the drive withdrew keeps its permalink, so it is found among the questions the drive drew, and the dashboard opens the task it was asked for."""
    for entry in reversed(cards):
        if entry["card"].get("waits") == waits:
            queue = "tap" if waits.startswith("merge:") else "question"
            ts = view.message_key(entry["url"])
            return {"kind": "card", "title": entry["card"]["title"], "url": entry["url"], "board": f"#/queue/{queue}/{ts[:-6]}.{ts[-6:]}"}
    for job in data["jobs"].values():
        for question in reversed(job["questions"]):
            if question["waits"] == waits and question["card"]:
                return {"kind": "card", "title": question["title"] or "his question card", "url": question["card"], "board": f"#/feature/{job['order']}"}
    raise DriveError(f"{{card:{waits}}} names no card the drive drew, open or settled; `cc-slack drive-cards --drive <drive>` lists each open card's waits id")


def pr(prs: dict, ref: str, label: str) -> dict:
    """A PR the drive holds, named by what it does, or any other PR named by its owner/repo#number or URL under the words the post gives it."""
    number = ref.removeprefix("#")
    keys = [key for key in prs if key.endswith(f"#{number}")] if number.isdigit() else [state.pr_key(ref)]
    if len(keys) > 1:
        raise DriveError(f"{{pr:{ref}}} could be {' or '.join(keys)}: name it as owner/repo#{number}")
    if keys and keys[0] in prs:
        found = prs[keys[0]]
        return {"kind": "pr", "title": view.pr_title(found) if found["title"] else keys[0], "url": found["url"], "board": found["url"]}
    if keys and label:
        url = state.pr_url(keys[0])
        return {"kind": "pr", "title": label, "url": url, "board": url}
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
    """A cci record under the opening of its own words, so a cause links the measurement behind it in a few words and the record's whole text never joins the post: a link to what the record links, or those words alone when it links nothing."""
    try:
        found = json.loads(reads.run(["cci", "show", seq, "--json", "--drive", name], Path.cwd()))
    except reads.ReadError as error:
        raise DriveError(f"{{record:{seq}}} could not be read: {error}") from None
    url = found["refs"].get("url")
    return {"kind": "record", "title": opening(found["text"]), "url": url, "board": url}


def opening(text: str) -> str:
    """The first clause of a record's first line, at most ten words, which a link's words in his posts rarely pass."""
    words = CLAUSE_END.split(view.plain(text, len(text)), maxsplit=1)[0].split()
    return " ".join(words[:RECORD_WORDS]) + ("…" if len(words) > RECORD_WORDS else "")


def slack(text: str, found: dict[str, dict]) -> str:
    """The text as Slack shows it: each reference a link under the words that name it, which Slack draws a card under, or a record that links nothing as its words."""
    return REF.sub(lambda match: linked(found[match[0]], found[match[0]]["url"], "<{url}|{words}>"), text)


def board(text: str, found: dict[str, dict]) -> str:
    """The text as the dashboard shows it: each reference a link to the page or PR it names, or a record that links nothing as its words."""
    return REF.sub(lambda match: linked(found[match[0]], found[match[0]]["board"], "[{words}]({url})"), text)


def linked(ref: dict, url: str | None, form: str) -> str:
    words = ref["title"].translate(LINK_WORDS)
    return form.format(url=url, words=words) if url else words
