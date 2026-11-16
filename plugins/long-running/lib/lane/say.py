"""say(next, by): a line to him from a lane's owner. A line that promises something writes the lane's time bound in the same write, so the promise is the lane's own wait; the send itself is a SENDER registered behind this interface."""

from collections.abc import Callable

from lane import store


def logged(drive: str, lane: dict, text: str) -> str:
    return f"posting is off on drive {drive}; kept in lane {lane['id']}'s log"


SENDER: Callable[[str, dict, str], str] = logged


def say(drive: str, lane_id: str, text: str, next_step: str | None = None, by: str | None = None, token: str | None = None) -> str:
    """Write the promise and clamp a waiting lane's bound to it, then hand the line to SENDER, or keep it in the log while the drive's posting is off; what the sender answered."""
    if bool(next_step) != bool(by):
        raise store.Refused("a promise names both what comes next and by when: --next and --by together")
    moment = store.now()
    bound = store.when(by, moment) if by else None
    if bound and bound <= moment:
        raise store.Refused(f"the promise's bound {store.stamp(bound)} has passed")

    def saying(lane: dict) -> dict:
        store.fenced(lane, token)
        if bound:
            lane["promise"] = {"text": text, "next": next_step, "by": store.stamp(bound), "at": store.stamp(moment)}
            wait = lane.get("wait")
            if wait and wait.get("until") and store.parse(wait["until"]) > bound:
                lane["wait"] = {**wait, "until": store.stamp(bound)}
        store.note(lane, f"said: {text}")
        return lane

    lane = store.update(drive, lane_id, saying)
    if not store.config(drive)["posting"]:
        return logged(drive, lane, text)
    return SENDER(drive, lane, text)
