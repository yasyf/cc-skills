import hashlib
import json
import queue
import threading
import time
from collections.abc import Callable

from drive import card, served, state
from drive.render import cut
from drive.state import DriveError


def pairs(first: str, second: str) -> dict:
    return {"type": "array", "items": {"type": "object", "properties": {first: {"type": "string"}, second: {"type": "string"}}, "required": [first, second]}}


STORY_SECONDS = 600
RETRY_SECONDS = 600
BODY_CHARS = 40_000
EFFORT = (
    'Each card needs its own story, by his words: "its almost like each one needs to tell a story, and you need to put a lot of effort into'
    ' each story, so you need 1) the primitives for it and 2) the effort in to tell those stories". Write for his time: short plain sentences,'
    " the outcome first, no hedging, filler, ids or jargon someone new to the work would not know, and nothing he must open the card to"
    ' understand, by his words "this context is still not sufficient but is the closest thing, and i had to navigte to context and then'
    ' uncollapse the whole description just to even see it". Use the card\'s own facts, numbers, systems and PRs, and never add one it does not hold.'
)
QUESTION_RULES = f"""You write the story of one decision card for the dashboard's decision view, where he, the owner, decides it, and which he wants to feel "almost like a mini applet focused on helping you make the clearest and easiest possible decisoin, with no distractions amd things gradually revealed as you need them / request them". {EFFORT}

<card> holds the card as its job asked it: its question, its body, its options in order, the recommended one, and each option's description where it has one.

Pick the format deciding it takes, never by how many options it has, by his words "all 3 depending on what hte question needs, again focus on telling the story and making it eashy for me, different questons will need different formats":
- cockpit: the paths differ in plain consequences he weighs at a glance, previewing each path's outcome beside the list.
- lab: the paths trade off along values he compares side by side, such as cost, time, risk or counts the card gives; every option carries the same metrics.
- interview: the choice turns on what he values most; he first answers `ask`, a question about his priority, each option's `priority` naming the priority it serves, and its outcome follows.

Then tell its story:
- headline: the decision itself, a plain question or statement of at most 90 characters.
- beats: two to four beats he reads in order, each `label` a few words, such as "What happened", "What's at stake" or "Why now", and each `text` one or two sentences: where things stand, what goes wrong or is gained, and what turns on his pick; where the card shows evidence, say what it shows.
- options: one for each option of the card, in its order: `title` at most six words naming the path; `consequence` one or two sentences on what is true once he picks it; `metrics` its lab values, each `label` the same across the options and each `value` a few words from the card, none outside a lab; `priority` the words of the priority it serves in an interview, empty outside one.
- ask: an interview's question on his priority, empty outside an interview."""
CHANGE_RULES = f"""You write the story of one pull request card for the dashboard's decision view, where he decides whether to approve it to land. His words on a card that failed him: "has a lot of facts but is compeltely useless for me figuring ut if I want to meg eit or not, I have no idea why were doing this change, what it does, or what the downstream impacts of it are", and on another that never said where it was tested: "IS THIS FUCKING TRSTED ON STSGIGN WHY DO I HAVE TI KEEO ASKING". {EFFORT}

<order> holds his words the change serves; <change> its title, the lead its builder wrote, its state and its description.

Tell its story:
- headline: what the change does for him, a plain statement of at most 90 characters.
- beats: three to five beats he reads in order, each `label` a few words and each `text` one or two sentences, together saying why it exists, what it changes, what it does downstream and to whom, and where it was tested, on staging, elsewhere or not at all, as its description and state say, saying so when they do not; order them as this change's story needs."""
QUESTION = {
    "type": "object",
    "properties": {
        "format": {"enum": ["cockpit", "lab", "interview"]},
        "headline": {"type": "string"},
        "beats": pairs("label", "text"),
        "ask": {"type": "string"},
        "options": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "consequence": {"type": "string"},
                    "priority": {"type": "string"},
                    "metrics": pairs("label", "value"),
                },
                "required": ["title", "consequence", "priority", "metrics"],
            },
        },
    },
    "required": ["format", "headline", "beats", "ask", "options"],
}
CHANGE = {"type": "object", "properties": {"headline": {"type": "string"}, "beats": pairs("label", "text")}, "required": ["headline", "beats"]}
ASKED = ("title", "body", "options", "recommended", "hints")


def question_key(asked: dict) -> str:
    return digest({field: asked.get(field) for field in ASKED})


def change_key(entry: dict) -> str:
    return digest({field: entry[field] for field in ("key", "head", "lead", "staging")})


def digest(told: dict) -> str:
    return hashlib.sha256(json.dumps(told, sort_keys=True).encode()).hexdigest()[:32]


def question(name: str, asked: dict) -> dict:
    """The story of his question card, its options carrying the card's own labels, in its order; refused when the story names another count of options."""
    told = served.structured(name, QUESTION_RULES, f"<card>\n{json.dumps({field: asked.get(field) for field in ASKED}, indent=1)}\n</card>", QUESTION, STORY_SECONDS, "writing the story of his card")
    if len(told["options"]) != len(asked["options"]):
        raise DriveError(f"the story written for his card drew {len(told['options'])} options for its {len(asked['options'])}")
    return told | {"options": [option | {"label": label} for option, label in zip(told["options"], asked["options"], strict=True)]}


def change(name: str, entry: dict) -> dict:
    """The story of a PR card waiting on his tap, from his order's words, its builder's lead, its state and its description as GitHub holds it."""
    data = state.load(name)
    pr = data["prs"][entry["key"]]
    read = card.read(entry["key"], pr)
    words = data["orders"][pr["order"]]["text"] if pr["order"] in data["orders"] else "none recorded"
    facts = [f"checks {entry['checks']}", f"review {entry['review']}", entry["staging"], (entry["stage"] or {}).get("why"), entry["approve_waits"] and f"after his Approve it waits on {entry['approve_waits']}"]
    body = cut(card.body_of(read), BODY_CHARS) if read else "GitHub gave no description"
    told = "\n".join([f'{entry["key"]} "{entry["title"]}"', f"Lead: {json.dumps(entry['lead'])}", f"State: {'; '.join(filter(None, facts))}", "", body])
    return served.structured(name, CHANGE_RULES, f"<order>\n{words}\n</order>\n\n<change>\n{told}\n</change>", CHANGE, STORY_SECONDS, f"writing the story of {entry['key']}")


class Writer:
    """Each card's story, written in one background thread one card at a time, so the dashboard runs at most one story's Claude session whatever the number of cards, and kept on disk by the digest of what it was written from, so a restart writes none again and a card that changes gets its own; a write that failed is kept beside it and tried again after RETRY_SECONDS."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.folder = state.drive_dir(name) / "stories"
        self.lock = threading.Lock()
        self.kept: dict[str, dict] = {}
        self.queued: set[str] = set()
        self.writing: queue.Queue[tuple[str, Callable[[], dict]]] = queue.Queue()
        threading.Thread(target=self.loop, daemon=True).start()

    def tell(self, model: dict) -> None:
        """Give each question card in the queue and each PR waiting on his tap its story as it stands, queueing each one not yet written in the queue's order."""
        for item in model["queue"]:
            if item["kind"] == "question":
                item["told"] = self.told(question_key(item["card"]), lambda asked=item["card"]: question(self.name, asked))
        for entry in model["review"]:
            if entry["group"] == "you":
                entry["told"] = self.told(change_key(entry), lambda entry=entry: change(self.name, entry))

    def told(self, key: str, write: Callable[[], dict]) -> dict:
        if key in self.kept:
            return {"story": self.kept[key]}
        if (path := self.folder / f"{key}.json").is_file():
            self.kept[key] = json.loads(path.read_text())
            return {"story": self.kept[key]}
        failed = self.folder / f"{key}.failed"
        if failed.is_file() and time.time() < failed.stat().st_mtime + RETRY_SECONDS:
            return {"error": failed.read_text()}
        with self.lock:
            if key not in self.queued:
                self.queued.add(key)
                self.writing.put((key, write))
        return {"writing": True}

    def loop(self) -> None:
        while True:
            self.write(*self.writing.get())

    def write(self, key: str, write: Callable[[], dict]) -> None:
        """Keep the story write returns as key's, or why it failed in its place."""
        try:
            written, kept = json.dumps(write()), f"{key}.json"
        except DriveError as error:
            written, kept = str(error), f"{key}.failed"
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / f"{key}.failed").unlink(missing_ok=True)
        (self.folder / kept).write_text(written)
        with self.lock:
            self.queued.discard(key)
