"""The verbs that change a lane: open, his message, park, end_turn and the owner's own writes. Each is one locked write, and no verb writes a third state."""

from datetime import timedelta

from lane import facts, store


def open_lane(drive: str, lane_id: str, title: str, words: str | None = None, channel: str | None = None, thread: str | None = None, link: str | None = None, ts: str | None = None, parks: str | None = None, park_words: str | None = None, prs: list[tuple[str, int]] = (), worktree: str | None = None, next_step: str | None = None, incident: dict | None = None) -> dict:
    """Open a lane, woken at once so the same call starts its owner; at the cap it must name the lane it parks."""
    cap = store.config(drive)["active"]

    def opening(lanes: dict[str, dict]) -> dict:
        if lane_id in lanes:
            raise store.Refused(f"lane {lane_id} is already open")
        running = store.active(lanes)
        if parks:
            if parks not in lanes or lanes[parks].get("parked"):
                raise store.Refused(f"{parks} is not an active lane to park")
            park_in(lanes[parks], park_words or f"parked to open {lane_id}")
        elif len(running) >= cap:
            raise store.Refused(f"{len(running)} lanes are active, the cap is {cap}: name the lane this one parks with --parks, and his words for it")
        lane = store.new_lane(lane_id, title, words, channel, thread, link, ts)
        lane["prs"] = [{"repo": repo, "number": number, "head": None, "approved_head": None, "held": None, "dangerous": None} for repo, number in prs]
        lane["worktree"] = worktree
        lane["next"] = next_step
        lane["incident"] = incident
        store.note(lane, "opened")
        lanes[lane_id] = lane
        return lane

    lane = store.transact(drive, opening)
    from lane import supervisor

    supervisor.run_pass(drive, only={lane_id}, read_outside=False)
    return store.load(drive, lane["id"])


def park_in(lane: dict, words: str) -> None:
    lane["parked"] = words
    if lane["state"] == store.WAITING:
        lane["wait"] = {"on": [{"kind": "reply"}], "until": None, "why": f"parked: {words}", "set": store.stamp()}
    store.note(lane, f"parked: {words}")


def park(drive: str, lane_id: str, words: str) -> None:
    store.update(drive, lane_id, lambda lane: park_in(lane, words))


def unpark(drive: str, lane_id: str) -> None:
    cap = store.config(drive)["active"]

    def unparking(lanes: dict[str, dict]) -> None:
        lane = lanes[lane_id]
        if not lane.get("parked"):
            raise store.Refused(f"lane {lane_id} is not parked")
        if len(store.active(lanes)) >= cap:
            raise store.Refused(f"{cap} lanes are active: park one before unparking {lane_id}")
        lane["parked"] = None
        if lane["state"] == store.WAITING:
            moment = store.stamp()
            lane["wait"] = {"on": [{"kind": "reply"}], "until": moment, "why": "unparked", "set": moment, "fired": moment}
        store.note(lane, "unparked")

    store.transact(drive, unparking)
    store.kick(drive)


def take_messages(lanes: dict[str, dict], lane_id: str, asks: list[dict], cap: int) -> int:
    """Append his messages to a lane by ts, once each; a parked lane unparks on his word while the cap has room. The count appended."""
    lane = lanes[lane_id]
    known = {ask.get("ts") for ask in lane.get("asks", []) if ask.get("ts")}
    fresh = [ask for ask in asks if not ask.get("ts") or ask["ts"] not in known]
    if not fresh:
        return 0
    lane["asks"] = [*lane.get("asks", []), *fresh]
    if lane.get("parked") and len(store.active(lanes)) < cap:
        lane["parked"] = None
        if lane["state"] == store.WAITING:
            lane["wait"] = {**lane["wait"], "until": lane["wait"].get("until") or store.stamp()}
        store.note(lane, "unparked by his word")
    return len(fresh)


def message(drive: str, lane_id: str, words: str, link: str | None = None, ts: str | None = None) -> int:
    """His message in a lane's channel: appended to its file, and that write is the owner's wake."""
    cap = store.config(drive)["active"]
    taken = store.transact(drive, lambda lanes: take_messages(lanes, lane_id, [{"at": store.stamp(), "words": words, "link": link, "ts": ts, "seen": False}], cap))
    if taken:
        from lane import supervisor

        supervisor.run_pass(drive, only={lane_id}, read_outside=False)
    return taken


def parse_event(text: str) -> dict:
    """One wait event as typed: pr:<owner/repo>#<n>:<fact>, cut:<id> or reply."""
    if text == "reply":
        return {"kind": "reply"}
    kind, _, rest = text.partition(":")
    if kind == "cut" and rest:
        return {"kind": "cut", "id": rest}
    if kind == "pr":
        target, _, fact = rest.rpartition(":")
        repo, _, number = target.partition("#")
        if "/" in repo and number.isdigit() and fact in store.PR_FACTS:
            return {"kind": "pr", "repo": repo, "number": int(number), "fact": fact}
    raise store.Refused(f"a wait event is pr:<owner/repo>#<n>:<{'|'.join(store.PR_FACTS)}>, cut:<id> or reply, not {text!r}")


def end_turn(drive: str, lane_id: str, token: str, on: list[dict], until: str, why: str, next_step: str | None = None) -> dict:
    """The only way out of running: refuses a wait with no time bound, one past its bound or the drive's longest, and one whose event already holds; else writes the wait and frees the slot in one write."""
    shape = store.config(drive)
    moment = store.now()
    bound = store.when(until, moment)
    if bound <= moment:
        raise store.Refused(f"the bound {store.stamp(bound)} has passed; a wait needs a time ahead")
    if bound > moment + timedelta(hours=shape["max_wait_hours"]):
        raise store.Refused(f"the bound {store.stamp(bound)} is past the drive's longest wait of {shape['max_wait_hours']}h")
    lane = store.load(drive, lane_id)
    store.fenced(lane, token)
    prs = facts.read_prs({facts.pr_key(event) for event in on if event["kind"] == "pr"})
    if held := [event for event in on if facts.holds(drive, lane, event, prs)]:
        raise store.Refused(f"already true, act on it instead of waiting: {held}" + (" (his unread words: run `lane asks`)" if any(event["kind"] == "reply" for event in held) else ""))
    if facts.unseen(lane):
        raise store.Refused("he wrote since you last read his words: run `lane asks` first")
    events = [facts.baseline(event, prs) for event in on]

    def ending(lane: dict) -> dict:
        store.fenced(lane, token)
        if lane["state"] != store.RUNNING:
            raise store.Refused(f"lane {lane_id} is not running")
        promised = (lane.get("promise") or {}).get("by")
        kept = min(bound, store.parse(promised)) if promised and store.parse(promised) > moment else bound
        owner = lane["owner"]
        lane["state"] = store.WAITING
        lane["owner"] = None
        lane["wait"] = {"on": events, "until": store.stamp(kept), "why": why, "set": store.stamp(moment)}
        if next_step:
            lane["next"] = next_step
        if lane.get("parked"):
            lane["wait"] = {**lane["wait"], "on": [*events, *([] if any(event["kind"] == "reply" for event in events) else [{"kind": "reply"}])]}
        store.note(lane, f"waits: {why}")
        return owner

    owner = store.update(drive, lane_id, ending)
    from lane import owner as owners

    owners.stop(owner)
    store.kick(drive)
    return store.load(drive, lane_id)


def asks(drive: str, lane_id: str, token: str | None) -> list[dict]:
    """His words on the lane, the unread ones marked read."""

    def reading(lane: dict) -> list[dict]:
        store.fenced(lane, token)
        rows = [dict(ask) for ask in lane.get("asks", [])]
        for ask in lane.get("asks", []):
            ask["seen"] = True
        return rows

    return store.update(drive, lane_id, reading)


def owner_write(drive: str, lane_id: str, token: str | None, change) -> None:
    def writing(lane: dict) -> None:
        store.fenced(lane, token)
        change(lane)

    store.update(drive, lane_id, writing)


def set_next(drive: str, lane_id: str, token: str | None, text: str) -> None:
    def setting(lane: dict) -> None:
        lane["next"] = text
        store.note(lane, f"next: {text}")

    owner_write(drive, lane_id, token, setting)


def log(drive: str, lane_id: str, token: str | None, text: str) -> None:
    owner_write(drive, lane_id, token, lambda lane: store.note(lane, text))


def add_pr(drive: str, lane_id: str, token: str | None, repo: str, number: int) -> None:
    def adding(lane: dict) -> None:
        if not any(pr["repo"] == repo and pr["number"] == number for pr in lane["prs"]):
            lane["prs"].append({"repo": repo, "number": number, "head": None, "approved_head": None, "held": None, "dangerous": None})

    owner_write(drive, lane_id, token, adding)


def set_incident(drive: str, lane_id: str, token: str | None, hurting: bool, link: str | None) -> None:
    def setting(lane: dict) -> None:
        lane["incident"] = {"hurting_users": hurting, "link": link or (lane.get("incident") or {}).get("link")}
        store.note(lane, f"incident {'hurts' if hurting else 'no longer hurts'} users")

    owner_write(drive, lane_id, token, setting)


def pin(drive: str, lane_id: str, pinned: bool) -> None:
    store.update(drive, lane_id, lambda lane: lane.__setitem__("pinned", pinned))
