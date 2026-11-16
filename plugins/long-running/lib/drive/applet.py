"""Applet pages: a page for one goal or one decision that the dashboard composes itself from the typed blocks of its component library, fed the page's live slice, made for every priority order and whenever the communicator calls drive page make; the page's own words come from a Claude session that reads the slice, and every PR, decision, order and moment it draws stays live."""

import hashlib
import json
import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path

from drive import pages, served, state
from drive.state import DriveError

COMPOSE_SECONDS = 900
RETRY_SECONDS = 900
KIND = "applet"
SLICE_PRS = 40
SLICE_QUESTIONS = 20
SLICE_ORDERS = 80
SLICE_MOMENTS = 20
TOLD_MOMENTS = {"order", "answer", "answered", "tap", "merged", "live", "failed"}
DONE_STATES = {"merged", "live"}
BLOCKS = {
    "waits": "what waits on him, each decision in questions with its recommended path one tap away and each PR in prs with its own Approve, so he acts without leaving the page",
    "story": "the story in two to four steps he walks one at a time: why this goal exists, where it stands, what is at stake",
    "progress": "a stage bar of steps toward the goal, each naming its orders, showing which step it is on and how many of each step's orders are done",
    "risk": "the one risk he must weigh, in text: what could go wrong, for whom, and what guards against it; tone warn or bad",
    "decide": "the decisions in questions, each drawn live as its own decision applet with its paths and their consequences",
    "approve": "the PRs in prs as one list he approves together or one by one, each with its plain status",
    "stack": "the PRs in prs as one stack, bottom first, each with what it does and where it stands",
    "diff": "the code change of each PR in prs, at most three, file by file with its hunks, each file folding open, the reviewer bot's comments and his own on the lines they name",
    "staging": "the PRs in prs as tiles, each with where it stands and where it was tested, for work that needs nothing from him",
    "path": "steps toward the goal in order, each naming its orders, drawn as a track with each step's orders listed",
    "timeline": "the newest moments of the slice, live",
    "compare": "two or more options side by side, each with its consequence; one recommended",
    "before_after": "what is true before the goal is met and what is true after, in before and after",
    "diagram": "a Mermaid flowchart or sequence diagram in diagram, its node labels plain words, never an id",
    "metrics": "a strip of live counts of the slice with a sparkline of the changes landed each day: decisions, PRs awaiting his approval, PRs ready to land, orders done of all",
    "callout": "one short line he must not miss, in text, with tone",
}
PAIR = {"type": "object", "properties": {"label": {"type": "string"}, "text": {"type": "string"}}, "required": ["label", "text"]}
GLANCE = {
    "type": "object",
    "properties": {
        "tone": {"enum": ["good", "warn", "bad", "plain"]},
        "status": {"type": "string"},
        "changed": {"type": "string"},
        "next": {"type": "string"},
        "why": {"type": "string"},
    },
    "required": ["tone", "status", "changed", "next", "why"],
}
SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "glance": GLANCE,
        "changed_for_him": {"type": "string"},
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"enum": list(BLOCKS)},
                    "title": {"type": "string"},
                    "text": {"type": "string"},
                    "tone": {"enum": ["plain", "good", "warn", "bad"]},
                    "fold": {"type": "boolean"},
                    "group": {"type": "string"},
                    "prs": {"type": "array", "items": {"type": "string"}},
                    "questions": {"type": "array", "items": {"type": "string"}},
                    "orders": {"type": "array", "items": {"type": "string"}},
                    "beats": {"type": "array", "items": PAIR},
                    "steps": {"type": "array", "items": {"type": "object", "properties": {"label": {"type": "string"}, "orders": {"type": "array", "items": {"type": "string"}}}, "required": ["label", "orders"]}},
                    "options": {"type": "array", "items": {"type": "object", "properties": {"title": {"type": "string"}, "consequence": {"type": "string"}, "recommended": {"type": "boolean"}}, "required": ["title", "consequence", "recommended"]}},
                    "before": {"type": "string"},
                    "after": {"type": "string"},
                    "diagram": {"type": "string"},
                },
                "required": ["kind", "title", "text", "tone", "fold", "group", "prs", "questions", "orders", "beats", "steps", "options", "before", "after", "diagram"],
            },
        },
    },
    "required": ["headline", "summary", "glance", "changed_for_him", "blocks"],
}
COMPOSE_VERSION = 2
RULES = f"""You compose one page of the drive's dashboard for its owner. Think of it as the note a sharp colleague would leave him about one goal: what he must know or do first, then the story, with everything else one tap away. His words: "each one should almost feel like a mini applet focused on helping you make the clearest and easiest possible decisoin, with no distractions amd things gradually revealed as you need them", and on a page that failed him: "it's just a plain dashboard with an overwhelming amount of info, not a story, not helping make things easier at a glance, and certainly not thinking about how to make my life easier".

<page> holds the page's goal and title; <slice> its live orders, PRs, decisions and newest moments, each under its `key`; <before> the glance the page showed last, or nothing for a new page.

How to think about it:
- Start from one question: what does he do next, and why? The answer is the glance's `next` and `why`. When nothing needs him, `next` says so and what he will hear next, such as "Nothing needs you; the release goes out once staging passes".
- The glance is what he reads in five seconds: `status` where the goal stands in a few words, with `tone`; `changed` what moved since he last looked, in one line; `next` the one thing he must do or know; `why` one line on why that, now.
- Then the story, in a few steps he can follow, not a list of facts. Every block answers a question he would ask in order: what needs me, what is this, where does it stand, what could go wrong, what is moving without me.
- Reveal the rest progressively. A block of detail he needs only to dig in gets `fold`: true, drawn folded with its title and `text` as the one line he reads before opening it. Blocks that are views of the same thing share a `group` name and draw as tabs, such as a stack, its diff and its staging tiles under one group.
- Cut anything that serves neither a decision nor his understanding. A count nobody acts on, a list of every order, a moment that changed nothing: leave it out. Six good blocks beat twelve.
- Write the way his gold-standard post does: name who it is for, short headed sections, plain words, a small picture only when it shows what words cannot, every name a thing he would recognize, never an id, job or order number, branch, hash or internal state word such as WAITING.

Three pages, told well and badly:
- A release with two PRs waiting on him. Badly: metrics, a diagram of every service, then a table of 40 PRs. Well: glance "Blocked on your Approve · next: approve the two migration PRs, because staging passed and the release window opens at 2 PM"; a waits block with those two PRs; a three-step story; the risk; the rest of the stack folded.
- A design decision. Badly: a story block repeating the card, then a compare block repeating the story. Well: glance "Needs your pick · next: choose how writes move during the handover, because both builders wait on it"; a waits block holding that decision; a before/after of what each path changes for users; the evidence folded.
- Work that needs nothing from him. Badly: every block drawn as if urgent. Well: glance "On track · next: nothing; you will hear when it is live"; a progress bar; a folded timeline. Short is right here.

Blocks, in the order that tells this goal's story:
{chr(10).join(f"- {kind}: {what}" for kind, what in BLOCKS.items())}

Rules:
- headline: the goal in plain words, at most 90 characters. summary: who this page is for and what it helps him do, one sentence.
- When <before> holds a glance and the one thing he must do next is the same, repeat its `next` word for word and keep its `tone`, however the counts moved.
- changed_for_him: compare the glance with <before>. One line naming what changed for him when his next step is a different thing to do, or the goal turned blocked, unblocked or done; empty when only wording or counts moved, such as one more PR joining the many already waiting on his approval. For a new page, one line on why it exists.
- When the slice holds decisions or PRs waiting on him, the first block is a waits block holding them. A code review approving a PR is not his approval: say "code review approved" for one, and "needs your approval" for what waits on him.
- prs, questions and orders hold only keys the slice gives, exactly; a block's other fields not used by its kind stay empty ("", [] or false).
- Use the slice's facts only, never add one it does not hold.
- A callout's or risk's text never repeats its title; leave the title empty when the line says it all.
- A diagram earns its place only when the goal's parts connect in a way words do not show."""


def make(name: str, slug: str, order_id: str, goal: str, scope: dict | str | None, made_by: str) -> dict:
    """Keep an applet page at pages/<slug>, titled by its order, for the goal named: its page.json alone, which the dashboard lists under Pages and composes; refused for a slug or order the drive does not hold."""
    if not pages.SLUG.fullmatch(slug):
        raise DriveError(f"{slug!r} is no page slug: lowercase letters, digits and dashes, starting with a letter or digit, at most 63")
    drive = state.load(name)
    if order_id not in drive["orders"]:
        raise DriveError(f"the {name} drive has no order {order_id}")
    from drive import view

    folder = pages.pages_dir(name) / slug
    kept = json.loads((folder / "page.json").read_text()) if (folder / "page.json").is_file() else {}
    orders = list(dict.fromkeys([*kept.get("orders", []), order_id]))
    page = {
        "kind": KIND,
        "title": view.order_title(drive["orders"][orders[0]]),
        "goal": goal,
        "orders": orders,
        "prs": kept.get("prs", []),
        "scope": scope if scope is not None else kept.get("scope"),
        "source": None,
        "job": None,
        "made_by": made_by,
        "created": kept.get("created") or state.stamp(),
    }
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "page.json").write_text(json.dumps(page, indent=2) + "\n")
    pages.pages_dir(name).touch()
    return {"slug": slug, **page}


def slug_of(title: str) -> str:
    words = "".join(c if c.isalnum() else " " for c in title.lower()).split()
    return "-".join(words)[:63].strip("-") or "page"


def fed(page: dict, sliced: dict) -> dict:
    """What the composing session reads of a page's slice: each order, PR, decision and moment in plain fields under its key."""
    return {
        "orders": [{"key": entry["id"], "title": entry["title"], "state": entry["state"], "project": entry.get("project"), "workstream": entry.get("workstream")} for entry in sliced["features"][:SLICE_ORDERS]],
        "prs": [
            {
                "key": entry["key"],
                "does": ((entry.get("told") or {}).get("story") or {}).get("headline") or (entry.get("lead") or {}).get("headline") or entry["title"],
                "order": entry["feature"],
                "waits_on_him": entry["group"] == "you",
                "checks": entry["checks"],
                "review": entry["review"],
                "staging": entry["staging_plain"]["word"],
                "after_approve": entry["approve_plain"],
            }
            for entry in sliced["prs"][:SLICE_PRS]
        ],
        "questions": [
            {
                "key": entry["answer"],
                "asks": ((entry.get("told") or {}).get("story") or {}).get("headline") or entry["title"],
                "options": entry["card"]["options"],
                "recommended": entry["card"].get("recommended"),
                "orders": entry["tasks"],
            }
            for entry in sliced["questions"][:SLICE_QUESTIONS]
        ],
        "moments": [{"at": entry["at"], "what": entry["kind"], "text": entry.get("text")} for entry in sliced["story"] if entry["kind"] in TOLD_MOMENTS][:SLICE_MOMENTS],
    }


def compose(name: str, page: dict, told: dict, before: dict | None) -> dict:
    """The page's layout as a Claude session composes it from its slice and the glance it showed last, each key it names kept only where the slice holds it."""
    prompt = f"<page>\n{json.dumps({'title': page['title'], 'goal': page['goal']}, indent=1)}\n</page>\n\n<slice>\n{json.dumps(told, indent=1)}\n</slice>\n\n<before>\n{json.dumps(before, indent=1) if before else 'nothing: a new page'}\n</before>"
    layout = served.structured(state.drive_dir(name), RULES, prompt, SCHEMA, COMPOSE_SECONDS, f"composing the page {page['slug']}")
    known = {"prs": {each["key"] for each in told["prs"]}, "questions": {each["key"] for each in told["questions"]}, "orders": {each["key"] for each in told["orders"]}}
    blocks = [block | {field: [key for key in block[field] if key in keys] for field, keys in known.items()} | {"steps": [step | {"orders": [key for key in step["orders"] if key in known["orders"]]} for step in block["steps"]]} for block in layout["blocks"]]
    return layout | {"blocks": blocks, "basis": basis(told)}


def basis(told: dict) -> dict:
    """What a page's next step for him turns on, read from its slice rather than its words: the decisions waiting on him, whether any PR waits on his approval, and whether every order is done, so one more PR joining those already waiting changes nothing."""
    return {
        "questions": sorted(each["key"] for each in told["questions"]),
        "approvals": any(each["waits_on_him"] for each in told["prs"]),
        "done": all(each["state"] in DONE_STATES for each in told["orders"]),
    }


def digest(page: dict, told: dict) -> str:
    """What a layout's glance turns on: the page's goal, its decisions, the PRs waiting on him and its orders, so a PR that needs nothing from him joining the slice, or any state change, redraws live without composing again, and a new decision, a PR newly waiting on him or a new order composes anew."""
    keys = {"questions": sorted(each["key"] for each in told["questions"]), "prs": sorted(each["key"] for each in told["prs"] if each["waits_on_him"]), "orders": sorted(each["key"] for each in told["orders"])}
    return hashlib.sha256(json.dumps({"goal": page["goal"], "version": COMPOSE_VERSION, **keys}, sort_keys=True).encode()).hexdigest()[:32]


class Composer:
    """Each applet page's layout, composed in one background thread one page at a time and kept on disk by the digest of what it was composed from, so a restart composes none again; until one is written the page shows the layout it last had, else the dashboard's own; a compose that failed is kept beside it and tried again after RETRY_SECONDS."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.folder = state.drive_dir(name) / "applets"
        self.lock = threading.Lock()
        self.queued: set[str] = set()
        self.composing: queue.Queue[tuple[str, str, Callable[[], dict]]] = queue.Queue()
        threading.Thread(target=self.loop, daemon=True).start()

    def tell(self, model: dict) -> None:
        """Give each applet page of the model its layout as it stands, queueing a compose for each page whose slice changed."""
        for page in model["pages"]:
            if page.get("kind") != KIND:
                continue
            told = fed(page, pages.slice_of(model, page))
            key = digest(page, told)
            page["layout"] = self.laid(page["slug"], key, lambda page=page, told=told: compose(self.name, page, told, self.glance(page["slug"])))

    def glance(self, slug: str) -> dict | None:
        """The glance of the page's newest layout, which its next compose weighs what changed for him against."""
        return newest(self.folder / slug).get("glance")

    def laid(self, slug: str, key: str, write: Callable[[], dict]) -> dict:
        path = self.folder / slug / f"{key}.json"
        if path.is_file():
            return {"composed": json.loads(path.read_text())}
        failed = path.with_suffix(".failed")
        last = {"composed": composed} if (composed := newest(self.folder / slug)) else {}
        if failed.is_file() and time.time() < failed.stat().st_mtime + RETRY_SECONDS:
            return last | {"error": failed.read_text()}
        with self.lock:
            if path.name not in self.queued:
                self.queued.add(path.name)
                self.composing.put((slug, key, write))
        return last | {"writing": True}

    def loop(self) -> None:
        while True:
            self.write(*self.composing.get())

    def write(self, slug: str, key: str, write: Callable[[], dict]) -> None:
        try:
            written, kept = json.dumps(write()), f"{key}.json"
        except DriveError as error:
            written, kept = str(error), f"{key}.failed"
        folder = self.folder / slug
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{key}.failed").unlink(missing_ok=True)
        (folder / kept).write_text(written)
        self.folder.touch()
        with self.lock:
            self.queued.discard(f"{key}.json")


def newest(folder: Path) -> dict:
    """The layout composed last in a page's folder, or {} for none."""
    laid = sorted(folder.glob("*.json"), key=lambda each: each.stat().st_mtime) if folder.is_dir() else []
    return json.loads(laid[-1].read_text()) if laid else {}


def seen_path(name: str, slug: str) -> Path:
    return state.drive_dir(name) / "applets" / f"{slug}.seen"


def unannounced(name: str) -> list[tuple[dict, str, dict]]:
    """Each applet page whose newest layout he has not been sent: a page never sent, or one whose basis, the decisions waiting on him, whether any PR waits on his approval and whether its orders are all done, or whose glance's tone differs from what he was last sent; never one recomposed on the same basis, whatever its words say, so a page reaches him once per material change. Each with the name of its newest layout and the layout."""
    due = []
    for page in pages.listed(name):
        if page.get("kind") != KIND:
            continue
        folder = state.drive_dir(name) / "applets" / page["slug"]
        laid = sorted(folder.glob("*.json"), key=lambda each: each.stat().st_mtime) if folder.is_dir() else []
        if not laid or "basis" not in (layout := json.loads(laid[-1].read_text())):
            continue
        seen = seen_path(name, page["slug"])
        sent = json.loads(seen.read_text()) if seen.is_file() else None
        if sent is None or laid[-1].name != sent["laid"] and (layout["basis"], layout["glance"]["tone"]) != (sent["basis"], sent["tone"]):
            due.append((page, laid[-1].name, layout))
    return due


def announce(name: str, slug: str, laid: str, layout: dict) -> None:
    """Keep the layout, basis and tone he was last sent of a page, which unannounced weighs the next compose against."""
    seen_path(name, slug).write_text(json.dumps({"laid": laid, "basis": layout["basis"], "tone": layout["glance"]["tone"]}) + "\n")
