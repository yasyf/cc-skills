"""Applet pages: a page for one goal or one decision that the dashboard composes itself from the typed blocks of its component library, fed the page's live slice, made for every priority order and whenever the communicator calls drive page make; the page's own words come from a Claude session that reads the slice, and every PR, decision, order and moment it draws stays live."""

import hashlib
import json
import queue
import threading
import time
from collections.abc import Callable

from drive import pages, served, state
from drive.state import DriveError

COMPOSE_SECONDS = 900
RETRY_SECONDS = 900
KIND = "applet"
SLICE_PRS = 40
SLICE_QUESTIONS = 20
SLICE_ORDERS = 80
SLICE_MOMENTS = 20
BLOCKS = {
    "story": "beats he reads in order: why this goal exists, where it stands, what is at stake; two to four beats",
    "decide": "the decisions in questions, each drawn live as its own decision applet with its paths and their consequences",
    "approve": "the PRs in prs as one list he approves together or one by one, each with its plain status",
    "stack": "the PRs in prs as one stack, bottom first, each with what it does and where it stands",
    "staging": "the PRs in prs as tiles, each with where it stands and where it was tested, for work that needs nothing from him",
    "path": "steps toward the goal in order, each naming its orders; drawn as a track showing how many of each step's orders are done",
    "timeline": "the newest moments of the slice, live",
    "compare": "two or more options side by side, each with its consequence; one recommended",
    "before_after": "what is true before the goal is met and what is true after, in before and after",
    "diagram": "a Mermaid flowchart or sequence diagram in diagram, its node labels plain words, never an id",
    "metrics": "live counts of the slice: PRs ready to land, PRs waiting, decisions, orders done of all",
    "callout": "one short line he must not miss, in text, with tone",
}
PAIR = {"type": "object", "properties": {"label": {"type": "string"}, "text": {"type": "string"}}, "required": ["label", "text"]}
SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string"},
        "summary": {"type": "string"},
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"enum": list(BLOCKS)},
                    "title": {"type": "string"},
                    "text": {"type": "string"},
                    "tone": {"enum": ["plain", "good", "warn", "bad"]},
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
                "required": ["kind", "title", "text", "tone", "prs", "questions", "orders", "beats", "steps", "options", "before", "after", "diagram"],
            },
        },
    },
    "required": ["headline", "summary", "blocks"],
}
RULES = f"""You compose one page of the drive's dashboard for its owner: a small app built around one goal or one decision, by his words "it should crate a cusotm page that tracks all the remaining tasks for that gol, which might be PRs and deciosns across various prokects or workstreams, ahould build custom animatios and diagrams and interactivity based on the context" and "each one should almost feel like a mini applet focused on helping you make the clearest and easiest possible decisoin, with no distractions amd things gradually revealed as you need them".

<page> holds the page's goal and title; <slice> its live orders, PRs, decisions and newest moments, each PR, decision and order under its `key`.

Compose it from these blocks, in the order that tells this goal's story, five to nine blocks, each one earning its place:
{chr(10).join(f"- {kind}: {what}" for kind, what in BLOCKS.items())}

Rules:
- headline: what the goal is and where it stands, a plain statement of at most 90 characters. summary: one or two plain sentences on what he does here.
- Open with metrics or a story, then lead with what needs him: decide and approve blocks come early when the slice holds decisions or PRs waiting on him; group PRs by what they do together, a stack or an approve block each.
- prs, questions and orders hold only keys the slice gives, exactly; a block's other fields not used by its kind stay empty ("" or []).
- Every word is for him: short plain sentences, outcome first, no ids, job or order numbers, branch names, commit hashes or internal state words such as WAITING; name a change by what it does.
- Use the slice's facts only, never add one it does not hold.
- A callout's text never repeats its title; leave the title empty when the line says it all.
- A diagram earns its place only when the goal's parts connect in a way words do not show; its labels are plain words."""


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
    page = {
        "kind": KIND,
        "title": view.order_title(drive["orders"][order_id]),
        "goal": goal,
        "orders": list(dict.fromkeys([*kept.get("orders", []), order_id])),
        "prs": kept.get("prs", []),
        "scope": scope,
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
        "moments": [{"at": entry["at"], "what": entry["kind"], "text": entry.get("text")} for entry in sliced["story"] if entry["kind"] != "heard"][:SLICE_MOMENTS],
    }


def compose(name: str, page: dict, told: dict) -> dict:
    """The page's layout as a Claude session composes it from its slice, each key it names kept only where the slice holds it."""
    prompt = f"<page>\n{json.dumps({'title': page['title'], 'goal': page['goal']}, indent=1)}\n</page>\n\n<slice>\n{json.dumps(told, indent=1)}\n</slice>"
    layout = served.structured(state.drive_dir(name), RULES, prompt, SCHEMA, COMPOSE_SECONDS, f"composing the page {page['slug']}")
    known = {"prs": {each["key"] for each in told["prs"]}, "questions": {each["key"] for each in told["questions"]}, "orders": {each["key"] for each in told["orders"]}}
    blocks = [block | {field: [key for key in block[field] if key in keys] for field, keys in known.items()} | {"steps": [step | {"orders": [key for key in step["orders"] if key in known["orders"]]} for step in block["steps"]]} for block in layout["blocks"]]
    return layout | {"blocks": blocks}


def digest(page: dict, told: dict) -> str:
    """What a layout is composed from: the page's goal and the set of PRs, decisions and orders its slice holds, so a state change redraws live without composing again, and new work in scope composes anew."""
    keys = {field: sorted(each["key"] for each in told[field]) for field in ("prs", "questions", "orders")}
    return hashlib.sha256(json.dumps({"goal": page["goal"], **keys}, sort_keys=True).encode()).hexdigest()[:32]


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
            page["layout"] = self.laid(page["slug"], key, lambda page=page, told=told: compose(self.name, page, told))

    def laid(self, slug: str, key: str, write: Callable[[], dict]) -> dict:
        path = self.folder / slug / f"{key}.json"
        if path.is_file():
            return {"composed": json.loads(path.read_text())}
        failed = path.with_suffix(".failed")
        older = sorted((self.folder / slug).glob("*.json"), key=lambda each: each.stat().st_mtime) if (self.folder / slug).is_dir() else []
        last = {"composed": json.loads(older[-1].read_text())} if older else {}
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
