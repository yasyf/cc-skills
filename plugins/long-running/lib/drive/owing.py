from datetime import datetime, timedelta

from drive import state
from drive.render import cut, when
from drive.state import DriveError

STEP = "step"
FIX_LIVE = "fix_live"
CLAIM = "claim"
CARD = "card"
DELIVER = "deliver"
REVERT = "revert"
KINDS = (STEP, FIX_LIVE, CLAIM, CARD, DELIVER, REVERT)
TELL_AFTER = timedelta(minutes=10)
SPAN_FLOOR = timedelta(minutes=15)
SPAN_CEILING = timedelta(minutes=60)
COORDINATOR_MISS = 2
HIS_MISS = 3


def items(order: dict) -> list[dict]:
    """The items owed on an order, each carrying the class of the ask it serves, by state.order_class, and the job it is about, if any."""
    owed = order.setdefault("owed", [])
    for item in owed:
        item.setdefault("class", state.order_class(order))
        item.setdefault("job", None)
    return owed


def still_open(order: dict) -> list[dict]:
    return [item for item in items(order) if not item["closed"]]


def find(data: dict, item_id: str) -> tuple[str, dict]:
    for order_id, order in data["orders"].items():
        for item in items(order):
            if item["id"] == item_id:
                return order_id, item
    raise DriveError(f"{item_id}: no owed item has that id: drive owed list names them")


def next_id(data: dict) -> str:
    taken = [int(item["id"][1:]) for order in data["orders"].values() for item in items(order)]
    return f"w{max(taken, default=0) + 1}"


def add(data: dict, order_id: str, kind: str, what: str, due: datetime | None, done_when: dict, source: str, moment: datetime, job: str | None = None) -> str:
    """Owe one thing on the order: what it is, when he should have it, named by the session that owes it, and the condition on the drive's rows that ends it, in the class of the order's ask; its id."""
    item_id = next_id(data)
    order = data["orders"][order_id]
    items(order).append(
        {
            "id": item_id,
            "kind": kind,
            "what": what,
            "due": due and state.stamp(due),
            "done_when": done_when,
            "source": source,
            "opened": state.stamp(moment),
            "misses": 0,
            "woke": None,
            "closed": None,
            "class": state.order_class(order),
            "job": job,
        }
    )
    return item_id


def open_on(order: dict, kind: str, done_when: dict) -> dict | None:
    return next((item for item in still_open(order) if item["kind"] == kind and item["done_when"] == done_when), None)


def untold(data: dict, order_id: str) -> list[dict]:
    """The reporters whose record the order keeps itself and whose thread has not heard its one note."""
    return [held for held in data["orders"][order_id]["reporters"] if "kept_by" not in held and not held["told"]]


def tell_due(data: dict, order_id: str) -> datetime | None:
    """When an order's reporters are owed their note: TELL_AFTER after its last move once its fix is proven live, or its answer posted, by state.may_tell, the drive's last line on an answer order counting as a move; None while none is owed."""
    order = data["orders"][order_id]
    if order["state"] in state.CLOSED_ORDER_STATES or not untold(data, order_id) or not state.may_tell(data, order_id):
        return None
    moved = (order["last_line"] if order["kind"] == "answer" else None) or order["updated"]
    return state.parse_time(moved) + TELL_AFTER


def tell_what(data: dict, order_id: str) -> str:
    order, owed = data["orders"][order_id], untold(data, order_id)
    answered = order["kind"] == "answer"
    happened, note = ("its answer is posted", "the answer") if answered else (f"its fix is proven live by {state.fix_proof(data, order)}", "the fix-live note")
    threads = " and ".join(held["link"] for held in owed)
    return f"{happened}, so post {note} in {threads}: send its report with --attr close={order_id}, which the communicator posts and records with drive order told {order_id} <its permalink>"


def asked(data: dict, order_id: str, moment: datetime) -> None:
    """Owe the delivery of an ask of his, an order of the asked class that is no drive tooling order, from the moment it is filed until it closes; due only once a session names one with drive owed due."""
    order = data["orders"][order_id]
    if state.order_class(order) == state.CLASSES[1] and not state.tooling(order):
        add(data, order_id, DELIVER, order["title"] or cut(order["text"], 120), None, {"order_closed": order_id}, "his ask", moment)


def reverted(data: dict, answer_id: str, link: str, due: datetime, moment: datetime, acted: list[str]) -> list[str]:
    """His undo of an answer, by his Undo tap or in words: mark it undone and owe a revert, due when the caller says, on each job that acted on it, those it reached directly and each carrying it through a hand-off, so everyone who acted hears it without anyone remembering to; none when no job acted. The new items' ids."""
    if answer_id in data["answers"]:
        data["answers"][answer_id]["undone"] = {"at": state.stamp(moment), "link": link}
    jobs = dict.fromkeys([*acted, *(job_id for job_id, job in data["jobs"].items() if answer_id in job["answers"])])
    return [
        add(data, data["jobs"][job_id]["order"], REVERT, f"undo what {job_id} did on his answer {answer_id}, which he undid in {link}", due, {"outcome": answer_id}, link, moment, job_id)
        for job_id in jobs
    ]


def met(data: dict, order_id: str, item: dict) -> bool:
    """Whether an item's condition holds on the drive's rows: a fix-live note once no reporter is owed it, a claim once its thread closed it, a card once the job it names draws one of his cards or ends, an ask once its order closes; a step or revert closes only on a post naming it or by hand."""
    from drive import view

    order, when = data["orders"][order_id], item["done_when"]
    if "told" in when:
        return not untold(data, order_id)
    if "claim" in when:
        return when["claim"] not in order["claims"]
    if "card" in when:
        job = data["jobs"].get(when["card"])
        return not job or job["state"] in state.FINISHED_JOB_STATES or bool(view.open_cards(job))
    if "order_closed" in when:
        return order["state"] in state.CLOSED_ORDER_STATES
    return False


def settle(data: dict, moment: datetime) -> bool:
    """Open what an event owes and close what is met, quietly: an order whose reporters are owed their note owes it TELL_AFTER on, by his ruling that a reporter hears within 10 minutes of the fix going live; then each open item whose condition holds closes as met. Whether anything changed."""
    changed = False
    for order_id, order in data["orders"].items():
        if (due := tell_due(data, order_id)) and not open_on(order, FIX_LIVE, {"told": order_id}):
            add(data, order_id, FIX_LIVE, tell_what(data, order_id), due, {"told": order_id}, "its reporters", moment)
            changed = True
        for item in still_open(order):
            if met(data, order_id, item):
                close(item, "met", moment)
                changed = True
    return changed



def close(item: dict, by: str, moment: datetime, why: str | None = None) -> None:
    item["closed"] = {"at": state.stamp(moment), "by": by, "why": why}


def posted(data: dict, order_id: str, link: str, due: datetime | None, owes: str | None, outcome: list[str], moment: datetime) -> str | None:
    """A post on the order ends each item it names as its outcome, a revert's or a card's as well as a step's, and with a due owes its own next step beside every step the order still owes, the writer naming what and by when, his words "any time you tell him a next step, give it a due"; a step closes only on a post naming it as its outcome, by his ruling, so a reworded status with a new time never buries a promise, and moving a step's time is drive owed due, which keeps its misses. The new item's id."""
    for item in still_open(data["orders"][order_id]):
        if item["id"] in outcome:
            close(item, "delivered", moment, link)
    if not due:
        return None
    return add(data, order_id, STEP, owes or "its next step", due, {"post_on": order_id}, link, moment)


def close_warning(order: dict, order_id: str) -> str | None:
    """The warning a close of the order carries while it owes an item still open, never a refusal, by his words "no tooling gate in the drive's way": each item stays open, in drive status and drive owed list, until it is delivered, moved or ended."""
    if not (held := [item for item in still_open(order) if item["kind"] != DELIVER]):
        return None
    return f"WARNING {order_id} closed still owing {'; '.join(row(item) for item in held)}: each stays open in drive owed list until delivered, or ended with drive owed done <w> --because \"<why>\""


def row(item: dict) -> str:
    due = f"due {when(item['due'])}" if item["due"] else "no due"
    missed = f", missed {item['misses']}" if item["misses"] else ""
    return f"{item['id']} {item['kind']} \"{item['what']}\" {due}{missed}"


def span(item: dict) -> timedelta:
    return min(max(state.parse_time(item["due"]) - state.parse_time(item["opened"]), SPAN_FLOOR), SPAN_CEILING)


def missed(item: dict, moment: datetime) -> bool:
    return not item["closed"] and bool(item["due"]) and state.parse_time(item["due"]) <= moment


def miss(item: dict, moment: datetime) -> int:
    """Count one miss of the item and give it a new due its own span away, clamped to SPAN_FLOOR..SPAN_CEILING, so a second and third miss come on its own clock; the miss's number."""
    item["misses"] += 1
    item["woke"] = state.stamp(moment)
    item["due"] = state.stamp(moment + span(item))
    return item["misses"]


def overdue_first(order: dict, moment: datetime) -> list[dict]:
    return sorted(still_open(order), key=lambda item: (not missed(item, moment), state.CLASSES.index(item["class"]), item["due"] is None, item["due"] and state.parse_time(item["due"])))


def status_lines(data: dict, moment: datetime) -> list[str]:
    """`drive status`'s owed section: each order's open items, a closed order's included, overdue first, then by due, with no due last, an ask's delivery only once a session named its due, since the order's own row shows it until then."""
    lines = []
    for order_id, order in data["orders"].items():
        if shown := [item for item in overdue_first(order, moment) if item["kind"] != DELIVER or item["due"]]:
            lines.append(f"{order_id}: " + "; ".join(("LATE " if missed(item, moment) else "") + row(item) for item in shown))
    return lines
