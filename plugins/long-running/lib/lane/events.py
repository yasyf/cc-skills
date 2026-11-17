"""Each thing that happens, handled in the call that reports it, by his words "everything used to be fast bc it was event based": his message reaches its owner in the call that hears it, and his tap starts the lander in the call that writes it. The supervisor's pass only replays what a source lost."""

from lane import owner as owners
from lane import store


def deliver(drive: str, lane_id: str, text: str) -> str:
    """Put text before the lane's owner now, whatever its process is doing: a running owner gets it as a message, which Claude Code queues for its next tool round when it is inside one and starts a turn when it is between turns; a waiting or parked lane was woken by the write that carried his words, and a pass for that lane alone starts its owner in the same call. What it did."""
    lane = store.load(drive, lane_id)
    if lane["state"] == store.RUNNING and (owner := lane.get("owner")) and owner.get("job"):
        owners.deliver(owner, text)

        def marking(lane: dict) -> None:
            if lane.get("owner"):
                lane["owner"]["nudged"] = store.stamp()

        store.update(drive, lane_id, marking, leaving_unreplied=True)
        return f"delivered to its running owner {owner['job']}"
    from lane import supervisor

    supervisor.run_pass(drive, only={lane_id}, read_outside=False)
    return f"started its owner ({store.load(drive, lane_id)['state']})"


def words_for(lane_id: str, words: str, link: str | None, ts: str) -> str:
    return f"His words to lane {lane_id}, just now{', ' + link if link else ''} (ts {ts}), not answered yet; answer them with `lane say --re {ts}` before anything else:\n{words}"


def message(drive: str, lane_id: str, words: str, link: str | None, ts: str) -> str:
    """His message on one lane: written as an ask, then delivered in the same call."""
    from lane import verbs

    if not verbs.message(drive, lane_id, words, link, ts):
        return f"{lane_id}: seen before"
    return f"{lane_id}: 1 new, {deliver(drive, lane_id, words_for(lane_id, words, link, ts))}"


def tapped(drive: str, landed: list[str]) -> str:
    """His Approve or Hold written on PRs: the lander starts on them in the same call, so a green approved stack is submitted within seconds, not at the next pass."""
    from lane import supervisor

    supervisor.lane_land.spawn(drive)
    return f"his tap on {', '.join(landed)}; the lander started"
