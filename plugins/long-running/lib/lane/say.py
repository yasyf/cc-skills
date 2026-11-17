"""say(next, by): a line to him from a lane's owner. A line that promises something writes the lane's time bound in the same write, so the promise is the lane's own wait, and the asks it answers are marked replied there too; the send itself is a SENDER registered behind this interface."""

from collections.abc import Callable

from lane import store


def logged(drive: str, lane: dict, text: str, re: str | None = None, options: list[str] | None = None, edit: str | None = None, about: list[str] | None = None, pictures: list[str] | None = None) -> str:
    return f"log:{store.stamp()}"


SENDER: Callable[[str, dict, str, str | None, list[str] | None, str | None, list[str] | None, list[str] | None], str] = logged


def say(drive: str, lane_id: str, text: str, next_step: str | None = None, by: str | None = None, token: str | None = None, answers: list[str] | None = None, re: str | None = None, options: list[str] | None = None, edit: str | None = None, about: list[str] | None = None, pictures: list[str] | None = None) -> str:
    """Hand the line to SENDER (or keep it in the log while posting is off), then write what promise writes; what the sender answered. re names the one ask of his the line is about, and the line answers it alone unless answers names others; options make the line a question with a button each, whose tap reaches the lane as his words; edit names the ts of the lane's earlier post the line rewrites in place; about names what else it is about, as release:<ref> or card:<ts>, so the post follows it; pictures are files it attaches."""
    checked(next_step, by)
    store.fenced(store.load(drive, lane_id), token)
    sent = SENDER(drive, store.load(drive, lane_id), text, re, options, edit, about, pictures) if store.config(drive)["posting"] else logged(drive, store.load(drive, lane_id), text, re, options, edit, about, pictures)
    promise(drive, lane_id, text, sent, next_step, by, token, answers or ([re] if re else None))
    return sent


def checked(next_step: str | None, by: str | None) -> None:
    if bool(next_step) != bool(by):
        raise store.Refused("a promise names both what comes next and by when: --next and --by together")
    if by and store.when(by) <= store.now():
        raise store.Refused(f"the promise's bound {by} has passed")


def promise(drive: str, lane_id: str, text: str, sent: str, next_step: str | None = None, by: str | None = None, token: str | None = None, answers: list[str] | None = None) -> None:
    """The lane write a post makes, in one locked write, before or as it goes out: the promise and the wait's bound clamped to it, and each ask it answers marked replied with sent, the sender's queued id or permalink: every unanswered ask, or those whose ts answers names."""
    checked(next_step, by)
    moment = store.now()
    bound = store.when(by, moment) if by else None
    if bound and bound <= moment:
        raise store.Refused(f"the promise's bound {store.stamp(bound)} has passed")

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
