"""The verbs that change a lane: open, his message, his ruling, park, end_turn and the owner's own writes. Each is one locked write, and no verb writes a fourth state."""

import json
from datetime import timedelta

from lane import facts, store


def woken(lane: dict, why: str) -> None:
    """Leave a parked or waiting lane waiting with its wait fired, so the next pass starts its owner."""
    moment = store.stamp()
    lane["state"], lane["parked"] = store.WAITING, None
    lane["wait"] = {"on": [{"kind": "his_reply"}], "until": moment, "why": why, "set": moment, "fired": moment, "fired_why": why}


def open_lane(drive: str, lane_id: str, title: str, words: str | None = None, channel: str | None = None, thread: str | None = None, link: str | None = None, ts: str | None = None, parks: str | None = None, park_words: str | None = None, prs: list[tuple[str, int]] = (), worktree: str | None = None, next_step: str | None = None, incident: dict | None = None) -> dict:
    """Open a lane, woken at once so the same call starts its owner; at the cap it must name the lane it parks, with his words."""
    cap = store.config(drive)["active"]
    if not worktree:
        raise store.Refused("a lane needs its worktree, a checkout of a repository Claude trusts, where its owner starts: --worktree")

    def opening(lanes: dict[str, dict]) -> dict:
        if lane_id in lanes:
            raise store.Refused(f"lane {lane_id} is already open")
        if parks:
            if parks not in lanes or lanes[parks]["state"] == store.PARKED:
                raise store.Refused(f"{parks} is not an active lane to park")
            if not park_words:
                raise store.Refused("parking a lane takes his words: --park-words")
            park_in(lanes[parks], park_words, None)
        elif len(store.active(lanes)) >= cap:
            raise store.Refused(f"{len(store.active(lanes))} lanes are active, the cap is {cap}: name the lane this one parks with --parks, and his words for it")
        lane = store.new_lane(lane_id, title, words, channel, thread, link, ts)
        lane["prs"] = [pr_row(repo, number, None) for repo, number in prs]
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


def pr_row(repo: str, number: int, words: str | None) -> dict:
    return {"repo": repo, "number": number, "words": words, "approved_head": None, "held": None, "dangerous": None, "staging": None}


def park_in(lane: dict, words: str, link: str | None) -> None:
    """Park a lane on his words. A waiting lane parks now; a running one parks at its owner's end_turn, which reads the parked request."""
    if lane["state"] == store.RUNNING:
        lane["park"] = {"words": words, "link": link}
    else:
        lane["state"], lane["wait"], lane["parked"] = store.PARKED, None, {"words": words, "link": link}
    store.note(lane, f"parked: {words}")


def park(drive: str, lane_id: str, words: str, link: str | None = None) -> None:
    store.update(drive, lane_id, lambda lane: park_in(lane, words, link))
    store.kick(drive)


def unpark(drive: str, lane_id: str) -> None:
    cap = store.config(drive)["active"]

    def unparking(lanes: dict[str, dict]) -> None:
        lane = lanes[lane_id]
        if lane["state"] != store.PARKED:
            raise store.Refused(f"lane {lane_id} is not parked")
        if len(store.active(lanes)) >= cap:
            raise store.Refused(f"{cap} lanes are active: park one before unparking {lane_id}")
        woken(lane, "unparked")
        store.note(lane, "unparked")

    store.transact(drive, unparking)
    store.kick(drive)


def take_messages(drive: str, lanes: dict[str, dict], lane_id: str, asks: list[dict], cap: int) -> int:
    """Append his messages and taps to a lane by ts, once each, and keep his presence from their Slack ts by store.heard, the one inbound path for his words. A parked lane unparks on his word, since his ask would otherwise wait unanswered; past the cap the check names it. The count appended."""
    lane = lanes[lane_id]
    known = {ask.get("ts") for ask in lane.get("asks", []) if ask.get("ts")}
    fresh = [ask for ask in asks if not ask.get("ts") or ask["ts"] not in known]
    if not fresh:
        return 0
    lane["asks"] = [*lane.get("asks", []), *fresh]
    store.heard(drive, fresh)
    if lane["state"] == store.PARKED:
        woken(lane, "his word in a parked lane")
        store.note(lane, f"unparked by his word{'' if len(store.active(lanes)) <= cap else f'; {len(store.active(lanes))} lanes are active, over the cap of {cap}'}")
    return len(fresh)


def ask_row(words: str, link: str | None, ts: str | None) -> dict:
    return {"words": words, "link": link, "at": store.stamp(), "ts": ts, "replied": None, "done": None}


def message(drive: str, lane_id: str, words: str, link: str | None = None, ts: str | None = None) -> int:
    """His message in a lane's channel: appended to its file, and that write is the owner's wake."""
    cap = store.config(drive)["active"]
    taken = store.transact(drive, lambda lanes: take_messages(drive, lanes, lane_id, [ask_row(words, link, ts)], cap))
    if taken:
        from lane import supervisor

        supervisor.run_pass(drive, only={lane_id}, read_outside=False)
    return taken


def ruling(drive: str, lane_id: str | None, words: str, link: str | None, binds: str) -> None:
    """Keep one answer of his verbatim: on the lane it binds, which wakes its owner in the same write, or with no lane in the drive's shared rulings every owner reads."""
    row = {"words": words, "link": link, "at": store.stamp(), "binds": binds}
    if lane_id is None:
        with store.locked(drive):
            path = store.drive_dir(drive) / store.RULINGS
            kept = store.shared_rulings(drive)
            fresh = path.with_name(".rulings.json.tmp")
            fresh.write_text(json.dumps([*kept, row], indent=1, ensure_ascii=False) + "\n")
            fresh.replace(path)
        return

    def recording(lane: dict) -> None:
        lane["rulings"] = [*lane.get("rulings", []), row]
        if lane["state"] != store.RUNNING:
            woken(lane, f"his ruling: {binds}")
        store.note(lane, f"ruling: {binds}")

    store.update(drive, lane_id, recording)
    from lane import supervisor

    supervisor.run_pass(drive, only={lane_id}, read_outside=False)


def parse_event(text: str) -> dict:
    """One wait event as typed: <kind>:<ref> for pr_merged, check, staging_result and release, or his_reply."""
    kind, _, ref = text.partition(":")
    if kind == "his_reply" and not ref:
        return {"kind": kind}
    if kind in store.REF_KINDS and ref:
        if kind in ("pr_merged", "check"):
            facts.pr_ref(ref)
        return {"kind": kind, "ref": ref}
    raise store.Refused(f"a wait event is pr_merged:<owner/repo>#<n>, check:<owner/repo>#<n>, staging_result:<cut id>, release:<release id> or his_reply, not {text!r}")


def end_turn(drive: str, lane_id: str, token: str, on: list[dict], until: str, why: str, next_step: str | None = None) -> dict:
    """The only way out of running: refuses a wait on no outside fact, with no time bound, past its bound or the drive's longest, on a fact that already holds, or while his ask is unanswered; else writes the wait, clears the owner and frees the slot in one write. A lane he parked while it ran parks here."""
    shape = store.config(drive)
    moment = store.now()
    bound = store.when(until, moment)
    if not on:
        raise store.Refused("a wait names at least one outside fact; with nothing else to wait for, wait on his_reply")
    if bound <= moment:
        raise store.Refused(f"the bound {store.stamp(bound)} has passed; a wait needs a time ahead")
    if bound > moment + timedelta(hours=shape["max_wait_hours"]):
        raise store.Refused(f"the bound {store.stamp(bound)} is past the drive's longest wait of {shape['max_wait_hours']}h")
    lane = store.load(drive, lane_id)
    store.fenced(lane, token)
    prs = facts.read_prs({facts.pr_ref(event["ref"]) for event in on if event["kind"] in ("pr_merged", "check")})
    if held := [event for event in on if event["kind"] != "his_reply" and facts.holds(drive, lane, event, prs)]:
        raise store.Refused(f"already true, act on it instead of waiting: {held}")

    def ending(lane: dict) -> dict:
        store.fenced(lane, token)
        if lane["state"] != store.RUNNING:
            raise store.Refused(f"lane {lane_id} is not running")
        promised = (lane.get("promise") or {}).get("by")
        kept = min(bound, store.parse(promised)) if promised and store.parse(promised) > moment else bound
        owner = lane["owner"]
        lane["owner"] = None
        if next_step:
            lane["next"] = next_step
        if parked := lane.pop("park", None):
            lane["state"], lane["parked"] = store.PARKED, parked
            store.note(lane, f"parked at turn end: {parked['words']}")
        else:
            lane["state"] = store.WAITING
            lane["wait"] = {"on": on, "until": store.stamp(kept), "why": why, "set": store.stamp(moment)}
            store.note(lane, f"waits: {why}")
        return owner

    owner = store.update(drive, lane_id, ending)
    from lane import owner as owners

    owners.stop(owner)
    store.kick(drive)
    return store.load(drive, lane_id)


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


def add_pr(drive: str, lane_id: str, token: str | None, repo: str, number: int, words: str | None) -> None:
    def adding(lane: dict) -> None:
        for row in lane["prs"]:
            if row["repo"] == repo and row["number"] == number:
                row["words"] = words or row.get("words")
                return
        lane["prs"].append(pr_row(repo, number, words))

    owner_write(drive, lane_id, token, adding)


def done(drive: str, lane_id: str, token: str | None, ts: str) -> None:
    """Mark one of his asks done, by its ts."""

    def marking(lane: dict) -> None:
        for ask in lane["asks"]:
            if ask.get("ts") == ts:
                ask["done"] = store.stamp()
                return
        raise store.Refused(f"no ask of his with ts {ts} on lane {lane_id}")

    owner_write(drive, lane_id, token, marking)


def set_incident(drive: str, lane_id: str, token: str | None, hurting: bool, link: str | None) -> None:
    def setting(lane: dict) -> None:
        lane["incident"] = {"hurting_users": hurting, "link": link or (lane.get("incident") or {}).get("link")}
        store.note(lane, f"incident {'hurts' if hurting else 'no longer hurts'} users")

    owner_write(drive, lane_id, token, setting)
    store.kick(drive)


def pin(drive: str, lane_id: str, pinned: bool) -> None:
    store.update(drive, lane_id, lambda lane: lane.__setitem__("pinned", pinned))
    store.kick(drive)


def set_worktree(drive: str, lane_id: str, path: str) -> None:
    def setting(lane: dict) -> None:
        lane["worktree"] = path
        store.note(lane, f"worktree: {path}")

    store.update(drive, lane_id, setting)
    store.kick(drive)
