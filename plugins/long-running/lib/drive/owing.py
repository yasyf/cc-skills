from datetime import datetime, timedelta

from drive import state
from drive.render import when
from drive.state import DriveError

STEP = "step"
KINDS = (STEP,)
SPAN_FLOOR = timedelta(minutes=15)
SPAN_CEILING = timedelta(minutes=60)
COORDINATOR_MISS = 2
HIS_MISS = 3


def items(order: dict) -> list[dict]:
    return order.setdefault("owed", [])


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


def add(data: dict, order_id: str, kind: str, what: str, due: datetime | None, done_when: dict, source: str, moment: datetime) -> str:
    """Owe one thing on the order: what it is, when he should have it, named by the session that owes it, and the condition on the drive's rows that ends it; its id."""
    item_id = next_id(data)
    items(data["orders"][order_id]).append(
        {"id": item_id, "kind": kind, "what": what, "due": due and state.stamp(due), "done_when": done_when, "source": source, "opened": state.stamp(moment), "misses": 0, "woke": None, "closed": None}
    )
    return item_id


def close(item: dict, by: str, moment: datetime, why: str | None = None) -> None:
    item["closed"] = {"at": state.stamp(moment), "by": by, "why": why}


def posted(data: dict, order_id: str, link: str, due: datetime | None, owes: str | None, outcome: list[str], moment: datetime) -> str | None:
    """A post on the order ends each step it names as its outcome, and with a due owes its own next step, replacing every step the order still owed, the writer naming what and by when, his words "any time you tell him a next step, give it a due"; a post naming neither leaves each step open, by his ruling that a promised step closes only on its outcome or a new due. The new item's id."""
    for item in still_open(data["orders"][order_id]):
        if item["kind"] == STEP and item["id"] in outcome:
            close(item, "delivered", moment, link)
        elif item["kind"] == STEP and due:
            close(item, "replaced", moment, link)
    if not due:
        return None
    return add(data, order_id, STEP, owes or "its next step", due, {"post_on": order_id}, link, moment)


def close_warning(order: dict, order_id: str) -> str | None:
    """The warning a close of the order carries while it owes an item still open, never a refusal, by his words "no tooling gate in the drive's way": each item stays open, in drive status and drive owed list, until it is delivered, moved or ended."""
    if not (held := still_open(order)):
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
    return sorted(still_open(order), key=lambda item: (not missed(item, moment), item["due"] is None, item["due"] and state.parse_time(item["due"])))


def status_lines(data: dict, moment: datetime) -> list[str]:
    """`drive status`'s owed section: each order's open items, a closed order's included, overdue first, then by due, with no due last."""
    lines = []
    for order_id, order in data["orders"].items():
        if shown := overdue_first(order, moment):
            lines.append(f"{order_id}: " + "; ".join(("LATE " if missed(item, moment) else "") + row(item) for item in shown))
    return lines
