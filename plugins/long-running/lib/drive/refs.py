import re

from drive import state, view
from drive.state import DriveError

REF = re.compile(r"\{(card|pr):([^{}\s]+)\}")
LINK_WORDS = str.maketrans({"<": "", ">": "", "|": "/", "[": "(", "]": ")"})


def resolved(name: str, text: str) -> dict[str, dict]:
    """Each reference the text holds, read from the drive: {card:<waits id>} and {pr:<number, owner/repo#number or URL>}, with the words that name it and where it opens; one that names nothing refuses the post."""
    found = {}
    cards = None
    for match in REF.finditer(text):
        if match[0] in found:
            continue
        if match[1] == "card":
            cards = cards or open_cards(name)
            found[match[0]] = card(cards, match[2])
        else:
            found[match[0]] = pr(state.load(name)["prs"], match[2])
    return found


def open_cards(name: str) -> list[dict]:
    try:
        listed = view.cards(name)
    except view.CardsError as error:
        raise DriveError(f"a {{card:…}} reference needs the drive's cards, which cc-slack could not list: {error}") from None
    return [*listed["questions"], *listed["pr_cards"]]


def card(cards: list[dict], waits: str) -> dict:
    """A card by its waits id: Slack draws it under a link naming it, and the dashboard opens the queue it waits in."""
    for entry in reversed(cards):
        if entry["card"].get("waits") == waits:
            queue = "tap" if waits.startswith("merge:") else "question"
            return {"kind": "card", "title": entry["card"]["title"], "url": entry["url"], "board": f"#/queue/{queue}"}
    raise DriveError(f"{{card:{waits}}} names no card of the drive's; `cc-slack drive-cards --drive <drive>` lists each card's waits id")


def pr(prs: dict, ref: str) -> dict:
    """A PR the drive holds, named by what it does."""
    number = ref.removeprefix("#")
    keys = [key for key in prs if key.endswith(f"#{number}")] if number.isdigit() else [state.pr_key(ref)]
    if len(keys) > 1:
        raise DriveError(f"{{pr:{ref}}} could be {' or '.join(keys)}: name it as owner/repo#{number}")
    if not keys or keys[0] not in prs:
        raise DriveError(f"{{pr:{ref}}} names no PR of the drive's; drive status lists them")
    found = prs[keys[0]]
    return {"kind": "pr", "title": view.pr_title(found) if found["title"] else keys[0], "url": found["url"], "board": found["url"]}


def slack(text: str, found: dict[str, dict]) -> str:
    """The text as Slack shows it: each reference a link under the words that name it, which Slack draws a card under."""
    return REF.sub(lambda match: f"<{found[match[0]]['url']}|{found[match[0]]['title'].translate(LINK_WORDS)}>", text)


def board(text: str, found: dict[str, dict]) -> str:
    """The text as the dashboard shows it: each reference a link to the page or PR it names."""
    return REF.sub(lambda match: f"[{found[match[0]]['title'].translate(LINK_WORDS)}]({found[match[0]]['board']})", text)
