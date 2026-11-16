import hashlib
import json
import os
import queue
import threading
import time
from collections.abc import Callable

from drive import card, diff, pages, picture, served, state
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
EXEMPLAR = """Yasyf asked me to walk through his point about agent inboxes, using your PR as the example.

*What your task-tracker PR does:* when someone answers a task, the api sends a message to the agent that filed it:

```
// api, at the end of answering a task
mailAnswer(task):
  if task.author is a cloud agent:
    sendToCloudAgent(task.author, kind: "task", "PER-12 answered by Dana")
```

*What we want instead:* the agent waits for its own answer, with the call it uses to wait for any change. The api knows nothing about agents.

*Why it matters:* an agent waits in only three ways: suspend, sleep and watch. Mailing the filer adds a fourth, private path, and it shows in the PR:
- a new message kind, `task` rows
- a send-route rule refusing `task` rows from anyone else
- answering a task now waits on that send

*A test for next time:* before adding a new way to reach an agent, ask which of send, suspend, sleep or watch already does it."""
TASTE = f"""He holds every card to this post of his, which reads well because it opens on one line saying who it is for and why, says what the code does in one plain sentence with a sketch of a few lines, then what we want instead or what is decided, then why it matters as the mechanism with three concrete consequences, and ends on one test or takeaway; every section leads with a few bold words, it names no id, sha, job or internal state, and a reader new to the work follows it:

<his_post>
{EXEMPLAR}
</his_post>

Write the card's first fields the way that post is written. They are what he sees; everything else on the card folds behind a disclosure he opens only to check a detail."""
QUESTION_RULES = f"""You write the story of one decision card for the dashboard's decision view, where he, the owner, decides it, and which he wants to feel "almost like a mini applet focused on helping you make the clearest and easiest possible decisoin, with no distractions amd things gradually revealed as you need them / request them". {EFFORT}

{TASTE}

<card> holds the card as its job asked it: its question, its body, its options in order, the recommended one, and each option's description where it has one.

Pick the format deciding it takes, never by how many options it has, by his words "all 3 depending on what hte question needs, again focus on telling the story and making it eashy for me, different questons will need different formats":
- cockpit: the paths differ in plain consequences he weighs at a glance, previewing each path's outcome beside the list.
- lab: the paths trade off along values he compares side by side, such as cost, time, risk or counts the card gives; every option carries the same metrics.
- interview: the choice turns on what he values most; he first answers `ask`, a question about his priority, each option's `priority` naming the priority it serves, and its outcome follows.

Then tell its story:
- headline: the decision itself, a plain question or statement of at most 90 characters.
- lead: two or three plain sentences he reads first: what happens now, who it hurts or helps, and what his pick changes. Open on the story itself, as his post opens on what the PR does, such as "Today a refused Iris call fails SoFi's action and skips its own error handler."; never a preamble naming him or a reader, such as "For you, …:" or "This is for …".
- now: what it does today, as the post's first section: `text` one plain sentence on what the code or system does now that this decision turns on; `sketch` at most eight lines of code or pseudo-code showing it, as the post sketches, or empty when no code is involved.
- matters: why it matters, as the post's section: `mechanism` one or two sentences on the mechanism; `consequences` exactly three short concrete consequences of it, each a few words to one sentence.
- beats: what happened, as two to four short steps in the order they happened, never paragraphs: each `label` a few words naming the step, such as "Staging test failed" or "Your pick waits"; `text` one short sentence on that step, where the card shows evidence saying what it shows; `when` the time or day the card gives for that step, as it writes it, such as "Nov 15, 9 PM" or "this morning", empty when it gives none; `picture` the path of the image in the card's body that shows that step, exactly as the body writes it, empty when none does. The last step says what turns on his pick.
- options: one for each option of the card, in its order: `title` at most six words naming the path; `consequence` one or two sentences on what is true once he picks it; `after` one sharp sentence on what happens right after he picks it: what the drive does next and when he will hear, from the card's own facts, such as "The builder merges it tonight; you hear when it is live on staging, about an hour later"; `metrics` its lab values, each `label` the same across the options and each `value` a few words from the card, none outside a lab; `priority` the words of the priority it serves in an interview, empty outside one.
- ask: an interview's question on his priority, empty outside an interview.
- needs_him: why this is his to decide and not the drive's, the one that fits best: "dangerous act" when the answer lets a step run that can break customers' work or lose their data, "customer data" when it touches what customers stored, "money" when it spends money, "his hands" when he must do something himself, such as sign in, tap or hand over a secret, "his own words" when the drive needs a ruling only his words give, "only he knows" when it turns on a fact only he holds.
- today: one or two sentences on what happens while he has not picked: what a user, a service or the drive sees meanwhile, as the card says.
- diagram: a Mermaid flowchart of how the paths part when a picture makes the choice clearer, its node labels plain words, never an id, else empty.
- waits: what waits on his answer, in actions a user of the product or the drive would see and for how long, such as "New chats stop being searchable until you answer; about 40 a day queue up", never code paths, by his words on a card about a lock: "is this all row writes? if so do 2. if its only creation of dbs etc then do 1. this was not clear at all"; one sentence, empty when nothing waits."""
CHANGE_RULES = f"""You write the interactive story of one pull request card for the dashboard, which he walks chapter by chapter to decide whether to approve it to land: the problem, the change file by file, what it does at runtime, the evidence, then his choice. His words on the cards: "these also need to tell an interactive story, as do the decision cards, to help make decisions and review the changes", and on a card that failed him: "has a lot of facts but is compeltely useless for me figuring ut if I want to meg eit or not, I have no idea why were doing this change, what it does, or what the downstream impacts of it are", and on another that never said where it was tested: "IS THIS FUCKING TRSTED ON STSGIGN WHY DO I HAVE TI KEEO ASKING". {EFFORT}

{TASTE}

<order> holds his words the change serves; <change> its title, the lead its builder wrote, its state and its description; <diff> its changed files, each hunk numbered as [hunk N] within its file, from 0.

Tell its story:
- headline: what the change does for him, a plain statement of at most 90 characters.
- lead: two or three plain sentences he reads first: what is wrong or missing today, what the change does about it, and what his Approve decides. Open on the story itself, as his post opens on what the PR does, such as "Nothing today proves a team's moved memories dropped the old fields."; never a preamble naming him or a reader, such as "For you, …:" or "This is for …".
- does: what it does, as the post's first section: `text` one plain sentence; `sketch` at most eight lines of code or pseudo-code showing the change, as the post sketches, or empty when a sketch adds nothing.
- decided: one or two sentences on what we want instead of today's behavior, or what the change decides, as the post's second section.
- matters: why it matters: `mechanism` one or two sentences on the mechanism; `consequences` exactly three short concrete consequences of it.
- check: one sentence, the post's closing test: where it was tested and what that proved, or the one thing he should check before approving.
- problem: `who` names in a few words who feels the problem, such as "customers whose chats fail to load" or "you, approving PRs"; `text` says in one or two sentences what goes wrong today without this change.
- hunks: the why of each hunk that matters, in the diff's order: `file` its path exactly as <diff> names it, `hunk` its number, `why` one sentence on why this hunk changes what it does and what that buys, in plain words, never a restatement of the code; leave out lockfiles, generated files, renames and formatting; at most 30.
- runtime: what it does when it runs: `before` and `after` one or two concrete sentences each on what a user, a service or the drive sees before and after; `diagram` a Mermaid flowchart of the path after the change when a picture makes it clearer, its node labels plain words, never an id, else empty.
- tested: one or two sentences on where it was tested, on staging, elsewhere or not at all, and what that proved, as its description and state say, saying so when they do not.
- approve: one or two sentences on what happens once he taps Approve: when it lands and ships and who sees the change.
- hold: one or two sentences on what stays true while he holds it."""
SKETCHED = {"type": "object", "properties": {"text": {"type": "string"}, "sketch": {"type": "string"}}, "required": ["text", "sketch"]}
MATTERS = {"type": "object", "properties": {"mechanism": {"type": "string"}, "consequences": {"type": "array", "items": {"type": "string"}}}, "required": ["mechanism", "consequences"]}
NEEDS_HIM = ("dangerous act", "customer data", "money", "his hands", "his own words", "only he knows")
QUESTION_VERSION = 6
QUESTION = {
    "type": "object",
    "properties": {
        "format": {"enum": ["cockpit", "lab", "interview"]},
        "lead": {"type": "string"},
        "now": SKETCHED,
        "matters": MATTERS,
        "headline": {"type": "string"},
        "beats": {"type": "array", "items": {"type": "object", "properties": {"label": {"type": "string"}, "text": {"type": "string"}, "when": {"type": "string"}, "picture": {"type": "string"}}, "required": ["label", "text", "when", "picture"]}},
        "ask": {"type": "string"},
        "needs_him": {"enum": list(NEEDS_HIM)},
        "waits": {"type": "string"},
        "today": {"type": "string"},
        "diagram": {"type": "string"},
        "options": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "consequence": {"type": "string"},
                    "after": {"type": "string"},
                    "priority": {"type": "string"},
                    "metrics": pairs("label", "value"),
                },
                "required": ["title", "consequence", "after", "priority", "metrics"],
            },
        },
    },
    "required": ["format", "headline", "lead", "now", "matters", "beats", "ask", "needs_him", "waits", "today", "diagram", "options"],
}
CHANGE_VERSION = 3
CHANGE = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "lead": {"type": "string"},
        "does": SKETCHED,
        "decided": {"type": "string"},
        "matters": MATTERS,
        "check": {"type": "string"},
        "problem": {"type": "object", "properties": {"who": {"type": "string"}, "text": {"type": "string"}}, "required": ["who", "text"]},
        "hunks": {"type": "array", "items": {"type": "object", "properties": {"file": {"type": "string"}, "hunk": {"type": "integer"}, "why": {"type": "string"}}, "required": ["file", "hunk", "why"]}},
        "runtime": {"type": "object", "properties": {"before": {"type": "string"}, "after": {"type": "string"}, "diagram": {"type": "string"}}, "required": ["before", "after", "diagram"]},
        "tested": {"type": "string"},
        "approve": {"type": "string"},
        "hold": {"type": "string"},
    },
    "required": ["headline", "lead", "does", "decided", "matters", "check", "problem", "hunks", "runtime", "tested", "approve", "hold"],
}
DIFF_CHARS = 60_000
HUNK_LINES = 60
ASKED = ("title", "body", "options", "recommended", "hints")


def question_key(asked: dict) -> str:
    return digest({field: asked.get(field) for field in ASKED} | {"version": QUESTION_VERSION})


def change_key(entry: dict) -> str:
    return digest({field: entry[field] for field in ("key", "head", "lead", "staging")} | {"version": CHANGE_VERSION})


def digest(told: dict) -> str:
    return hashlib.sha256(json.dumps(told, sort_keys=True).encode()).hexdigest()[:32]


def question(name: str, asked: dict) -> dict:
    """The story of his question card, its options carrying the card's own labels, in its order, each step's picture kept only when the card's body shows that image; refused when the story names another count of options."""
    told = served.structured(state.drive_dir(name), QUESTION_RULES, f"<card>\n{json.dumps({field: asked.get(field) for field in ASKED}, indent=1)}\n</card>", QUESTION, STORY_SECONDS, "writing the story of his card")
    if len(told["options"]) != len(asked["options"]):
        raise DriveError(f"the story written for his card drew {len(told['options'])} options for its {len(asked['options'])}")
    shown = {found[1] for found in picture.IMAGE.finditer(asked.get("body") or "") if found[1]}
    beats = [beat | {"picture": beat["picture"] if beat["picture"] in shown else ""} for beat in told["beats"]]
    return told | {"beats": beats, "options": [option | {"label": label} for option, label in zip(told["options"], asked["options"], strict=True)]}


def change(name: str, entry: dict) -> dict:
    """The story of a PR card waiting on his tap, from his order's words, its builder's lead, its state and its description as GitHub holds it."""
    data = state.load(name)
    pr = data["prs"][entry["key"]]
    read = card.read(entry["key"], pr)
    words = data["orders"][pr["order"]]["text"] if pr["order"] in data["orders"] else "none recorded"
    facts = [f"checks {entry['checks']}", f"review {entry['review']}", entry["staging"], (entry["stage"] or {}).get("why"), entry["approve_waits"] and f"after his Approve it waits on {entry['approve_waits']}"]
    body = cut(card.body_of(read), BODY_CHARS) if read else "GitHub gave no description"
    told = "\n".join([f'{entry["key"]} "{entry["title"]}"', f"Lead: {json.dumps(entry['lead'])}", f"State: {'; '.join(filter(None, facts))}", "", body])
    return served.structured(state.drive_dir(name), CHANGE_RULES, f"<order>\n{words}\n</order>\n\n<change>\n{told}\n</change>\n\n<diff>\n{diff_text(entry['key'])}\n</diff>", CHANGE, STORY_SECONDS, f"writing the story of {entry['key']}")


def diff_text(key: str) -> str:
    """The PR's diff as its story reads it: each file's path, state and counts, then each hunk numbered within its file, its lines cut at HUNK_LINES, the whole cut at DIFF_CHARS."""
    read = diff.read(key)
    told = []
    for file in read["files"]:
        told.append(f"### {file['path']} ({file['status']}, +{file['added']} -{file['removed']})")
        for n, hunk in enumerate(file["hunks"]):
            lines = [{"add": "+", "del": "-", "ctx": " "}[line["kind"]] + line["text"] for line in hunk["lines"]]
            told.append(f"[hunk {n}] at line {hunk['new']} {hunk['section']}".rstrip())
            told.extend(lines[:HUNK_LINES] + ([f"... {len(lines) - HUNK_LINES} more lines"] if len(lines) > HUNK_LINES else []))
    return cut("\n".join(told), DIFF_CHARS)


class Writer:
    """Each card's story, written in one background thread one card at a time, so the dashboard runs at most one story's Claude session whatever the number of cards, and kept on disk by the digest of what it was written from, so a restart writes none again and a card that changes gets its own; a write that failed is kept beside it and tried again after RETRY_SECONDS."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.folder = pages.kept_dir(name, "stories")
        self.lock = threading.Lock()
        self.kept: dict[str, dict] = {}
        self.queued: set[str] = set()
        self.writing: queue.Queue[tuple[str, Callable[[], dict]]] = queue.Queue()
        threading.Thread(target=self.loop, daemon=True).start()

    def tell(self, model: dict) -> None:
        """Give each question card in the queue, each open PR a queue card shows whether or not it waits on his tap yet, and each other PR waiting on his tap its story as it stands, queueing each one not yet written in the queue's order, a PR card's PRs at its place in it, then each other PR waiting on his tap; a card showing a PR decided or not yet ready still opens on its story."""
        review = {entry["key"]: entry for entry in model["review"]}
        yours = [key for key, entry in review.items() if entry["group"] == "you"]
        for item in model["queue"]:
            if item["kind"] == "question":
                item["told"] = self.told(question_key(item["card"]), lambda asked=item["card"]: question(self.name, asked))
            for key in [state.pr_key(pr["url"]) for pr in item.get("prs") or [] if pr.get("state", "open") == "open"]:
                if (entry := review.pop(key, None)) is not None:
                    entry["told"] = self.told(change_key(entry), lambda entry=entry: change(self.name, entry))
        for entry in (review[key] for key in yours if key in review):
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
        """Keep the story write returns as key's, or why it failed in its place, written whole in one rename, so a dashboard reading it while another writes, as one install hands over to the next, never reads it empty."""
        try:
            written, kept = json.dumps(write()), f"{key}.json"
        except DriveError as error:
            written, kept = str(error), f"{key}.failed"
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / f"{key}.failed").unlink(missing_ok=True)
        fresh = self.folder / f".{kept}.{os.getpid()}.tmp"
        fresh.write_text(written)
        fresh.replace(self.folder / kept)
        with self.lock:
            self.queued.discard(key)
