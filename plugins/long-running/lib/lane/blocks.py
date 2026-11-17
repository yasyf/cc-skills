"""A block only he can clear reaches his thread, by his rule that a block on his work reaches him within minutes saying what clears it: each block a lane is given is kept on it, Jev judges once whether only he can clear it, and one of his that no post of the lane's has followed wakes its owner to tell him, then files a defect when the owner still has not."""

from datetime import datetime, timedelta

import httpx
from spawnllm import (
    JEV,
    Binary,
    BinaryAnswer,
    DecideError,
    DecideKeyMissing,
    decide_sync,
)

from lane import audit, store, verbs

TELL_WITHIN = timedelta(minutes=10)
JUDGE_SECONDS = 30.0
HIS_AT = 0.5


def judged(blocks: list[dict]) -> dict[str, bool]:
    """One Jev request: for each block, whether only he can clear it; none when the request fails, so those blocks are judged on the next pass."""
    questions = {block["at"]: Binary("Can only he, the drive's owner, clear this block: his Approve or Hold, his decision between options, his word on scope, or access only he can grant?", yes="only he can clear it", no="the lane or another tool can clear it") for block in blocks}
    try:
        decision = decide_sync({"blocks": {block["at"]: block["what"] for block in blocks}}, questions, provider=JEV, timeout=JUDGE_SECONDS)
    except (TimeoutError, ValueError, DecideError, DecideKeyMissing, httpx.HTTPError):
        return {}
    return {key: answer.p_yes >= HIS_AT for key, answer in decision.answers.items() if isinstance(answer, BinaryAnswer)}


def told(drive: str, moment: datetime) -> None:
    """Judge each new block, then wake the owner of each block of his that no post followed within TELL_WITHIN, and file a defect for one still untold TELL_WITHIN after that wake."""
    lanes = store.load_all(drive)
    fresh = [block for lane in lanes.values() for block in lane.get("blocks", []) if "his" not in block]
    verdicts = judged(fresh) if fresh else {}
    late: list[tuple[str, dict]] = []

    def telling(lanes: dict[str, dict]) -> None:
        for lane_id, lane in lanes.items():
            for block in lane.get("blocks", []):
                if block["at"] in verdicts and "his" not in block:
                    block["his"] = verdicts[block["at"]]
                if not block.get("his") or block.get("told") or block.get("defect"):
                    continue
                if not block.get("woken") and moment - store.parse(block["at"]) >= TELL_WITHIN:
                    block["woken"] = store.stamp(moment)
                    verbs.woken(lane, f"a block only he can clear has not reached his thread: {block['what']}. Tell him there what clears it, with lane say")
                elif block.get("woken") and moment - store.parse(block["woken"]) >= TELL_WITHIN:
                    block["defect"] = store.stamp(moment)
                    late.append((lane_id, block))

    store.transact(drive, telling, leaving_unreplied=True)
    for lane_id, block in late:
        audit.defect(drive, f"lane {lane_id} never told him of a block only he can clear", f"blocked at {block['at']} by {block['by']}: {block['what']}; its owner was woken at {block['woken']} and posted nothing", "a block only he can clear reached no post to his thread", "lane-voice")
