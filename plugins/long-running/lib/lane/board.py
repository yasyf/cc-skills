"""His dashboard's queue as the lane drive holds it: each stack ready for his tap by lane-land's rule, lane by lane in the priority queue's order, a lane whose incident hurts users first, for the old dashboard to draw in its own item shape; what is not ready yet stays on his lane page's counts, so the queue is never a wall of every unapproved PR."""

from lane import page, rank, store
from lane.rows import CHECKS


def taps(drive: str) -> list[dict] | None:
    """Each stack ready for his tap, in queue order: its bottom PR at the head GitHub read last, the PRs stacked above it, its lane, its position, and whether its lane's incident hurts users; None when the drive runs no live lanes, so the old drive's queue stands."""
    if not store.live(drive):
        return None
    lanes = store.load_all(drive)
    ranked = rank.last(drive)
    hurting = set(ranked.get("hurting", []))
    order = [lane_id for lane_id in ranked.get("order", []) if lane_id in lanes] + sorted(set(lanes) - set(ranked.get("order", [])))
    order.sort(key=lambda lane_id: lane_id not in hurting)
    seen = page.view(drive)["prs"]
    found = []
    for position, lane_id in enumerate(order, 1):
        stacks, _ = page.ready(page.tapless(lanes[lane_id], seen), seen)
        for row in stacks:
            key = f"{row['repo']}#{row['number']}"
            found.append({
                "key": key,
                "head": seen[key]["head"],
                "checks": CHECKS.get(row["checks"] or "", "pending"),
                "title": row.get("words") or seen[key].get("title") or key,
                "moved": row["moved"],
                "stack": [{"key": member, "url": url(member), "title": seen[member].get("title") or member, "checks": CHECKS.get(seen[member]["rollup"] or "", "pending")} for member in [key, *row["above"]]],
                "lane": lane_id,
                "lane_title": lanes[lane_id]["title"],
                "position": position,
                "hurting": lane_id in hurting,
            })
    return found


def url(key: str) -> str:
    repo, _, number = key.rpartition("#")
    return f"https://github.com/{repo}/pull/{number}"
