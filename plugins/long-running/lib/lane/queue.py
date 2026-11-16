"""Grants from the priority queue for each contended thing beside owner slots: the staging env, release starts and the cards that reach him. A grant goes to the best-ranked lane asking, by rank.json; a re-rank never takes a grant back, except that a lane whose incident hurts users takes the staging env from a lane that is not one."""

import json
from datetime import timedelta
from pathlib import Path

from lane import rank, store

CAPACITY = {"staging-env": 1, "release": 1, "card": 2}
PREEMPTS = ("staging-env",)


def path(drive: str) -> Path:
    return store.drive_dir(drive) / "queue.json"


def capacity(drive: str, resource: str) -> int:
    return (store.config(drive).get("capacity") or {}).get(resource, CAPACITY[resource])


def kept(drive: str) -> dict:
    found = path(drive)
    return json.loads(found.read_text()) if found.is_file() else {"leases": {}, "requests": {}, "revoked": {}}


def written(drive: str, queue: dict) -> None:
    fresh = path(drive).with_name(".queue.json.tmp")
    fresh.write_text(json.dumps(queue, indent=1) + "\n")
    fresh.replace(path(drive))


def hurting(drive: str, lane_id: str) -> bool:
    """Whether the lane's incident hurt users at the last rank, which reads it from the incident's facts."""
    return lane_id in rank.last(drive).get("hurting", [])


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
    return [resource for resource, requests in kept(drive)["requests"].items() if any(asked["lane"] == lane_id for asked in requests.values())]


def holding(drive: str, lane_id: str) -> list[str]:
    """Each resource the lane holds a live grant of."""
    moment = store.now()
    return [resource for resource, leases in kept(drive)["leases"].items() if any(lease["lane"] == lane_id and store.parse(lease["until"]) > moment for lease in leases)]


def take(drive: str, resource: str, holder: str, lane_id: str, ttl_minutes: int) -> dict:
    """Ask for one of resource for holder, on lane_id's behalf: granted with its bound when a unit is free and no better-ranked lane is asking, or taken from a lane that is no incident when lane_id's incident hurts users and the resource is the staging env; else the holder's place in line."""
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
        why = "granted in rank order" if granted else None
        if not granted and resource in PREEMPTS and hurting(drive, lane_id) and (victim := next((lease for lease in leases if not hurting(drive, lease["lane"])), None)):
            leases.remove(victim)
            queue["revoked"][victim["holder"]] = {"resource": resource, "at": store.stamp(moment), "why": f"taken by lane {lane_id}, whose incident hurts users"}
            granted, why = True, f"taken from {victim['holder']} for an incident hurting users"
        if granted:
            requests.pop(holder)
            leases.append({"holder": holder, "lane": lane_id, "at": store.stamp(moment), "until": store.stamp(moment + timedelta(minutes=ttl_minutes))})
            queue["revoked"].pop(holder, None)
        queue["leases"][resource] = leases
        written(drive, queue)
    if granted:
        return {"granted": True, "until": leases[-1]["until"], "why": why}
    return {"granted": False, "position": line.index(holder) + 1, "ahead": line[: line.index(holder)], "held_by": [lease["holder"] for lease in leases]}


def renew(drive: str, resource: str, holder: str, ttl_minutes: int) -> str:
    """Extend holder's grant; refused once it lapsed or an incident took it, with why."""
    moment = store.now()
    with store.locked(drive):
        queue = kept(drive)
        if revoked := queue["revoked"].get(holder):
            raise store.Refused(f"{resource} was taken from {holder}: {revoked['why']}")
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
        queue["revoked"].pop(holder, None)
        written(drive, queue)
        line = sorted(requests.values(), key=lambda asked: (order.get(asked["lane"], len(order)), asked["at"]))
    if line:
        from lane import verbs

        def waking(lane: dict) -> None:
            if lane["state"] == store.WAITING:
                verbs.woken(lane, f"the queue freed {resource}, and this lane is first in line for it: take it now")

        store.update(drive, line[0]["lane"], waking)
    store.kick(drive)
