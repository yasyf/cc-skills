"""say(next, by): a line to him from a lane's owner. A line that promises something writes the lane's time bound in the same write, so the promise is the lane's own wait, and the asks it answers are marked replied there too; the send itself is a SENDER registered behind this interface."""

from collections.abc import Callable

from lane import store


def logged(drive: str, lane: dict, text: str) -> str:
    return f"log:{store.stamp()}"


SENDER: Callable[[str, dict, str], str] = logged


def say(drive: str, lane_id: str, text: str, next_step: str | None = None, by: str | None = None, token: str | None = None, answers: list[str] | None = None) -> str:
    """Write the promise, clamp the wait's bound to it, hand the line to SENDER (or keep it in the log while posting is off), and mark the asks it answers replied with what the sender answered: every unanswered ask, or those whose ts answers names. What the sender answered."""
    if bool(next_step) != bool(by):
        raise store.Refused("a promise names both what comes next and by when: --next and --by together")
    moment = store.now()
    bound = store.when(by, moment) if by else None
    if bound and bound <= moment:
        raise store.Refused(f"the promise's bound {store.stamp(bound)} has passed")
    lane = store.load(drive, lane_id)
    store.fenced(lane, token)
    sent = SENDER(drive, lane, text) if store.config(drive)["posting"] else logged(drive, lane, text)

    def saying(lane: dict) -> None:
        store.fenced(lane, token)
        if bound:
            lane["promise"] = {"text": text, "next": next_step, "by": store.stamp(bound), "at": store.stamp(moment)}
            wait = lane.get("wait")
            if wait and store.parse(wait["until"]) > bound:
                lane["wait"] = {**wait, "until": store.stamp(bound)}
        for ask in lane["asks"]:
            if not ask.get("replied") and (answers is None or ask.get("ts") in answers):
                ask["replied"] = sent
        store.note(lane, f"said ({sent}): {text}")

    store.update(drive, lane_id, saying)
    return sent


def replied(drive: str, lane_id: str, sent: str, permalink: str) -> None:
    """Swap the sender's queued id for the post's permalink on each ask that post answered."""

    def swapping(lane: dict) -> None:
        for ask in lane["asks"]:
            if ask.get("replied") == sent:
                ask["replied"] = permalink

    store.update(drive, lane_id, swapping)


def dropped(drive: str, lane_id: str, sent: str, why: str) -> None:
    """A post the mechanical check dropped: its asks are unanswered again, which wakes the owner."""

    def clearing(lane: dict) -> None:
        for ask in lane["asks"]:
            if ask.get("replied") == sent:
                ask["replied"] = None
        store.note(lane, f"post {sent} dropped: {why}")

    store.update(drive, lane_id, clearing)
    store.kick(drive)
