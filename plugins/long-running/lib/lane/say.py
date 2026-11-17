"""say(next, by): a line to him from a lane's owner. A line that promises something writes the lane's time bound in the same write, so the promise is the lane's own wait, and the asks it answers are marked replied there too; the send itself is a SENDER registered behind this interface."""

import re
from collections.abc import Callable
from datetime import datetime, timedelta

from lane import store


def logged(drive: str, lane: dict, text: str, re: str | None = None, options: list[str] | None = None, edit: str | None = None, about: list[str] | None = None, pictures: list[str] | None = None) -> str:
    return f"log:{store.stamp()}"


SENDER: Callable[[str, dict, str, str | None, list[str] | None, str | None, list[str] | None, list[str] | None], str] = logged


DIRECT = ("D", "U")


def say(drive: str, lane_id: str, text: str, next_step: str | None = None, by: str | None = None, token: str | None = None, answers: list[str] | None = None, re: str | None = None, options: list[str] | None = None, edit: str | None = None, about: list[str] | None = None, pictures: list[str] | None = None) -> str:
    """Hand the line to SENDER (or keep it in the log while posting is off), then write what promise writes; what the sender answered. re names the one ask of his the line is about, and the line answers it alone unless answers names others; options make the line a question with a button each, whose tap reaches the lane as his words; edit names the ts of the lane's earlier post the line rewrites in place; about names what else it is about, as release:<ref> or card:<ts>, so the post follows it; pictures are files it attaches."""
    checked(next_step, by)
    store.fenced(lane := store.load(drive, lane_id), token)
    if (lane.get("channel") or "").startswith(DIRECT):
        raise store.Refused(f"lane {lane_id}'s home is his DM, and drive work never posts there, by his words \"drive stuff leaves DMs entirely\": give it its project channel with `lane home {lane_id} --channel <id>`, then post")
    sent = SENDER(drive, store.load(drive, lane_id), text, re, options, edit, about, pictures) if store.config(drive)["posting"] else logged(drive, store.load(drive, lane_id), text, re, options, edit, about, pictures)
    promise(drive, lane_id, text, sent, next_step, by, token, named(lane, answers, re), re or "")
    if options:
        store.update(drive, lane_id, lambda lane: lane.__setitem__("asked_at", store.stamp()), leaving_unreplied=True)
    return sent


def named(lane: dict, answers: list[str] | None, re: str | None) -> list[str]:
    """The asks a post answers: those --answers or --re name, or with neither the one ask of his still waiting on a reply; with several waiting, a post naming none answers none, so his unanswered ask stays open."""
    if answers or re:
        return answers or [re]
    waiting = [ask["ts"] for ask in lane["asks"] if ask.get("ts") and not ask.get("replied") and not ask.get("done")]
    return waiting if len(waiting) == 1 else []


def checked(next_step: str | None, by: str | None) -> None:
    if bool(next_step) != bool(by):
        raise store.Refused("a promise names both what comes next and by when: --next and --by together")
    if by and store.when(by) <= store.now():
        raise store.Refused(f"the promise's bound {by} has passed")


WITHIN = re.compile(r"\b(?:within|in)\s+(\d+)\s*(?:minutes|mins?)\b", re.I)


def stated(text: str, moment: datetime) -> datetime | None:
    """The bound a post states in its words, "by 3:45 PM", "next line by 5:40 AM tomorrow" or "within 45 minutes": the earliest, a clock time after "by" read in Pacific as its next one after the post; a time with no "by", such as when something happened, is no promise."""
    from lane import verbs

    found = [moment + timedelta(minutes=int(match[1])) for match in WITHIN.finditer(text)]
    for match in verbs.CLOCK.finditer(text):
        if not text[: match.start()].rstrip().lower().endswith("by"):
            continue
        at = verbs.clocked(match)
        found.append(at + timedelta(days=1) if at <= moment or "tomorrow" in text[match.end() : match.end() + 14] else at)
    return min(found) if found else None


def promise(drive: str, lane_id: str, text: str, sent: str, next_step: str | None = None, by: str | None = None, token: str | None = None, answers: list[str] | None = None, thread: str = "") -> None:
    """The lane write a post makes, in one locked write, before or as it goes out: the promise and the wait's bound clamped to it, and each ask whose ts answers names marked replied with sent, the sender's queued id or permalink."""
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
            if not ask.get("replied") and ask.get("ts") in (answers or []):
                ask["replied"], ask["replied_at"] = sent, store.stamp()
        for block in lane.get("blocks", []):
            block.setdefault("told", store.stamp(moment))
        for kept in lane.get("promises", []):
            if kept["thread"] == thread and not kept.get("followed"):
                kept["followed"] = store.stamp(moment)
        if due := bound or stated(re.sub(r"[^.!?\n]*\?", "", text), moment):
            lane["promises"] = [*lane.get("promises", []), {"text": text, "due": store.stamp(due), "thread": thread, "at": store.stamp(moment), "sent": sent}][-50:]
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
