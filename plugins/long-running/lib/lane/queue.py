"""Grants from the priority queue for the one contended thing beside owner slots: release starts. Asking him is never one: a lane posts its card the moment it is ready, and his page orders the open cards by rank, by drive-redesign's ruling; nor is staging, whose cuts run side by side with no hold, by his 5:00 PM ruling. A grant goes to the best-ranked lane asking, by rank.json; a re-rank never takes a grant back."""

import json
from datetime import timedelta
from pathlib import Path

from lane import rank, store

CAPACITY = {"release": 1}


def path(drive: str) -> Path:
    return store.drive_dir(drive) / "queue.json"


def capacity(drive: str, resource: str) -> int:
    return (store.config(drive).get("capacity") or {}).get(resource, CAPACITY[resource])


def kept(drive: str) -> dict:
    found = path(drive)
    return json.loads(found.read_text()) if found.is_file() else {"leases": {}, "requests": {}}


def written(drive: str, queue: dict) -> None:
    fresh = path(drive).with_name(".queue.json.tmp")
    fresh.write_text(json.dumps(queue, indent=1) + "\n")
    fresh.replace(path(drive))


def free_for(drive: str, resource: str, lane_id: str) -> bool:
    """Whether a unit of resource is free now and lane_id asks for it ahead of every other lane in line, or holds it already: what a lane waiting on queue_grant waits for."""
    if resource not in CAPACITY:
        raise store.Refused(f"the queue grants {', '.join(CAPACITY)}, not {resource!r}")
    moment = store.now()
    order = {lane: index for index, lane in enumerate(rank.last(drive).get("order", []))}
    queue = kept(drive)
    leases = [lease for lease in queue["leases"].get(resource, []) if store.parse(lease["until"]) > moment]
    if any(lease["lane"] == lane_id for lease in leases):
        return True
    line = sorted(queue["requests"].get(resource, {}).values(), key=lambda asked: (order.get(asked["lane"], len(order)), asked["at"]))
    ahead = [asked["lane"] for asked in line]
    first = ahead.index(lane_id) if lane_id in ahead else len(ahead)
    return capacity(drive, resource) - len(leases) > first


def held_by(name: str, holder: str) -> bool:
    """Whether a lease or request under name is holder's: the same name, or one an earlier owner of the lane holder names took as `<lane>/<token>`."""
    return name == holder or name.startswith(f"{holder}/")


def asking(drive: str, lane_id: str) -> list[str]:
    """Each resource the lane stands in line for."""
    return [resource for resource, requests in kept(drive)["requests"].items() if resource in CAPACITY and any(asked["lane"] == lane_id for asked in requests.values())]


def holding(drive: str, lane_id: str) -> list[str]:
    """Each resource the lane holds a live grant of."""
    moment = store.now()
    return [resource for resource, leases in kept(drive)["leases"].items() if resource in CAPACITY and any(lease["lane"] == lane_id and store.parse(lease["until"]) > moment for lease in leases)]


def prune(drive: str) -> int:
    """Drop every lease past its bound and every lease or request of a resource the queue no longer grants, such as the card and staging-env units, so no stale row stands in queue.json; how many went."""
    moment = store.now()
    with store.locked(drive):
        queue = kept(drive)
        before = sum(len(leases) for leases in queue["leases"].values()) + sum(len(asked) for asked in queue["requests"].values())
        queue["leases"] = {resource: [lease for lease in leases if store.parse(lease["until"]) > moment] for resource, leases in queue["leases"].items() if resource in CAPACITY}
        queue["requests"] = {resource: asked for resource, asked in queue["requests"].items() if resource in CAPACITY}
        after = sum(len(leases) for leases in queue["leases"].values()) + sum(len(asked) for asked in queue["requests"].values())
        if after != before:
            written(drive, queue)
    return before - after


def take(drive: str, resource: str, holder: str, lane_id: str, ttl_minutes: int) -> dict:
    """Ask for one of resource for holder, on lane_id's behalf: granted with its bound when a unit is free and no better-ranked lane is asking; else the holder's place in line."""
    if resource not in CAPACITY:
        raise store.Refused(f"the queue grants {', '.join(CAPACITY)}, not {resource!r}")
    moment = store.now()
    order = {lane: index for index, lane in enumerate(rank.last(drive).get("order", []))}
    with store.locked(drive):
        queue = kept(drive)
        leases = [lease for lease in queue["leases"].get(resource, []) if store.parse(lease["until"]) > moment and lease["holder"] != holder]
        requests = queue["requests"].setdefault(resource, {})
        earlier = [requests.pop(name) for name in [name for name, asked in requests.items() if asked["lane"] == lane_id and name != holder]]
        requests.setdefault(holder, min(earlier, key=lambda asked: asked["at"], default={"lane": lane_id, "at": store.stamp(moment)}))
        line = sorted(requests, key=lambda name: (order.get(requests[name]["lane"], len(order)), requests[name]["at"]))
        free = capacity(drive, resource) - len(leases)
        granted = holder in line[: max(0, free)]
        if granted:
            requests.pop(holder)
            leases.append({"holder": holder, "lane": lane_id, "at": store.stamp(moment), "until": store.stamp(moment + timedelta(minutes=ttl_minutes))})
        queue["leases"][resource] = leases
        written(drive, queue)
    if granted:
        return {"granted": True, "until": leases[-1]["until"], "why": "granted in rank order"}
    return {"granted": False, "position": line.index(holder) + 1, "ahead": line[: line.index(holder)], "held_by": [lease["holder"] for lease in leases]}


def renew(drive: str, resource: str, holder: str, ttl_minutes: int) -> str:
    """Extend holder's grant; refused once it lapsed."""
    moment = store.now()
    with store.locked(drive):
        queue = kept(drive)
        lease = next((lease for lease in queue["leases"].get(resource, []) if held_by(lease["holder"], holder) and store.parse(lease["until"]) > moment), None)
        if lease is None:
            raise store.Refused(f"{holder} holds no live grant of {resource}")
        lease["until"] = store.stamp(moment + timedelta(minutes=ttl_minutes))
        written(drive, queue)
        return lease["until"]


def release(drive: str, resource: str, holder: str) -> None:
    """Give holder's grant back and wake the best-ranked lane waiting in line for it, so the freed unit goes to it now and no waiter learns of it only at its bound."""
    order = {lane: index for index, lane in enumerate(rank.last(drive).get("order", []))}
    with store.locked(drive):
        queue = kept(drive)
        queue["leases"][resource] = [lease for lease in queue["leases"].get(resource, []) if not held_by(lease["holder"], holder)]
        requests = queue["requests"].get(resource, {})
        for name in [name for name in requests if held_by(name, holder)]:
            requests.pop(name)
        written(drive, queue)
        line = sorted(requests.values(), key=lambda asked: (order.get(asked["lane"], len(order)), asked["at"]))
    if line:
        from lane import verbs

        def waking(lane: dict) -> None:
            if lane["state"] == store.WAITING:
                verbs.woken(lane, f"the queue freed {resource}, and this lane is first in line for it: take it now")

        store.update(drive, line[0]["lane"], waking)
    store.kick(drive)
