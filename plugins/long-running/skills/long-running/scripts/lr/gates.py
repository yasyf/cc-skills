"""What a head must show before it lands, and how a tap follows a rebase.

A tap belongs to a head. When a lane rebases and the new head's patch id equals the tapped
head's, the tap carries as a fact row on the new head, noted with the old head, so the owner
is never re-asked for a change he already read. A carried tap admits nothing by itself: the
new head still needs green CI and clean grades, which :func:`lr.machine.classify` reads.
"""

from __future__ import annotations

from datetime import datetime

from lr.store import Store

CARRIED = "carried-from"


def carry_tap(store: Store, repo: str, number: int, old_head: str, new_head: str, patch_ids: dict[str, str], at: datetime) -> bool:
    """Copy a `tapped` fact from `old_head` to `new_head` when both heads have the same patch id; false when the tap stays behind."""
    if old_head == new_head or patch_ids.get(old_head) is None or patch_ids.get(old_head) != patch_ids.get(new_head):
        return False
    old = store.facts(repo, number, old_head)
    if old.get("tap") != "tapped":
        return False
    changed = store.put_fact(repo, number, new_head, "tap", "tapped", at)
    store.put_fact(repo, number, new_head, CARRIED, old_head, at)
    if "exempt" in old:
        store.put_fact(repo, number, new_head, "exempt", old["exempt"], at)
    return changed


def missing(facts) -> list[str]:
    """Every gate a head still fails, in words, empty when it may land."""
    out = []
    if facts.ci != "success":
        out.append("CI is not green")
    if facts.mergeable != "clean":
        out.append(f"mergeability is {facts.mergeable}")
    if facts.carries:
        out.append("branch carries " + ", ".join(f"#{n}" for n in facts.carries))
    if not facts.graded:
        out.append("not graded")
    elif not facts.cleared:
        out.append("a grade needs a verdict")
    if not facts.tapped:
        out.append("no tap")
    if facts.hold is not None:
        out.append(f"held by {facts.hold}")
    return out
