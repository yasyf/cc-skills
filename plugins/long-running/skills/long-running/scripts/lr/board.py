"""The owner's board is a view over the work items whose mover is the owner.

One row per open `(pr, head)` the owner must answer, with every gate the head still fails
and, after a rebase, the head the tap was carried from. An answered key never renders
again. Every card carries Merge, Hold and Explain more; Merge becomes a `tap` fact and
record, Hold goes to the hold hook the runner wires to cci, Explain more is a reply.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from lr import gates
from lr.machine import Facts, Mover
from lr.store import Store

OPTIONS = ("Merge", "Hold", "Explain more")


@dataclass(frozen=True)
class Row:
    pr: int
    head: str
    kind: str
    gates: tuple[str, ...]
    carried_from: str | None

    @property
    def key(self) -> tuple[int, str]:
        return (self.pr, self.head)

    @property
    def block_id(self) -> str:
        return f"{self.kind}-{self.pr}-{self.head[:12]}"


def rows(store: Store, repo: str, snapshot: dict[int, Facts]) -> list[Row]:
    out = []
    for item in store.open_items(repo):
        if item.mover != Mover.OWNER or item.number not in snapshot:
            continue
        facts = snapshot[item.number]
        if facts.head != item.head or facts.tapped:
            continue
        out.append(Row(item.number, item.head, item.kind, tuple(gates.missing(facts)), store.facts(repo, item.number, item.head).get(gates.CARRIED)))
    return out


def render(board: list[Row], answered: set[tuple[int, str]]) -> list[dict]:
    blocks = []
    for row in board:
        if row.key in answered:
            continue
        waits = ", ".join(gate for gate in row.gates if gate != "no tap") or "every other gate is met"
        carried = f"; rebased from {row.carried_from[:12]}, same patch" if row.carried_from else ""
        blocks.append({"id": row.block_id, "type": "choice", "title": f"#{row.pr} at {row.head[:12]}: {row.kind}", "body": f"{waits}{carried}", "options": list(OPTIONS)})
    return blocks


def answer(store: Store, repo: str, post: Callable[..., object], hold: Callable[[int, str], None], row: Row, choice: str, at: datetime) -> None:
    if choice == "Merge":
        store.put_fact(repo, row.pr, row.head, "tap", "tapped", at)
        post("tap", to=["supervisor"], pr=row.pr, head=row.head, by="owner", value="tapped")
    elif choice == "Hold":
        hold(row.pr, row.head)
    elif choice != "Explain more":
        raise ValueError(f"not a board answer: {choice!r}")
