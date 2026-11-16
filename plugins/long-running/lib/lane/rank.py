"""The priority queue over every lane and every open ask inside it: a model reads the rank rule against each lane's facts, re-read every supervisor pass and asked again only when the rule or the facts change, which is when an event lands (his message or tap, a merge, a check, a staging result, an incident flag); asks rank under their lane, oldest first."""

import hashlib
import json
from datetime import datetime
from pathlib import Path

import httpx
from spawnllm import JEV, DecideError, DecideKeyMissing, Score, ScoreAnswer, decide_sync

from lane import store

RULE = Path(__file__).with_name("rank.md")
LEVELS = {
    "last": "parked, or nothing about it can move",
    "low": "can wait behind most lanes",
    "normal": "ordinary work of his",
    "high": "he is waiting on it now, or approved work is ready to finish",
    "first": "an incident hurting users now",
}
SECONDS = 20.0


def rule(drive: str) -> str:
    own = store.drive_dir(drive) / "rank.md"
    return (own if own.is_file() else RULE).read_text()


def ago(text: str | None, moment: datetime) -> str:
    if not text:
        return "never"
    hours = (moment - store.parse(text)).total_seconds() / 3600
    return "under 2h" if hours < 2 else "under a day" if hours < 24 else "over a day"


def facts(lane: dict, moment: datetime, hurting: bool = False) -> dict:
    asks = lane.get("asks", [])
    return {
        "id": lane["id"],
        "title": lane["title"],
        "parked": lane["state"] == store.PARKED,
        "pinned": bool(lane.get("pinned")),
        "incident": {"hurting_users": hurting, "fix_live": bool((lane.get("incident") or {}).get("fix_live"))} if lane.get("incident") else None,
        "his_last_words": ago(asks[-1]["at"] if asks else None, moment),
        "unanswered_asks": len(store.unreplied(lane)),
        "open_asks": sum(1 for ask in asks if not ask.get("done")),
        "open_prs": len([pr for pr in lane.get("prs", []) if not pr.get("merged")]),
        "approved_open": len([pr for pr in lane.get("prs", []) if pr.get("approved_head") and pr.get("approved_head") == pr.get("head") and not pr.get("merged")]),
        "red_prs": len([pr for pr in lane.get("prs", []) if pr.get("checks") == "fail" and not pr.get("merged")]),
        "age": ago(lane["created"], moment),
        "state": lane["state"],
    }


def path(drive: str) -> Path:
    return store.drive_dir(drive) / "rank.json"


def last(drive: str) -> dict:
    kept = path(drive)
    return json.loads(kept.read_text()) if kept.is_file() else {"order": [], "scores": {}, "key": None}


def judge(text: str, told: list[dict]) -> dict[str, float]:
    decision = decide_sync({"rule": text, "lanes": told}, {item["id"]: Score(f"By the rule, how much does it matter that lane {item['id']} ({item['title']}) moves next?", LEVELS) for item in told}, provider=JEV, timeout=SECONDS)
    return {key: answer.score for key, answer in decision.answers.items() if isinstance(answer, ScoreAnswer)}


def ranked(drive: str, lanes: dict[str, dict], moment: datetime, hurt: set[str] = frozenset()) -> dict:
    """The order lanes take slots and staging in, best first: the model's scores when the rule or the facts changed since the last ask, else the last ones; a failed ask keeps the last scores and says why."""
    text = rule(drive)
    told = [facts(lane, moment, lane["id"] in hurt) for lane in lanes.values()]
    key = hashlib.sha256(json.dumps({"rule": text, "lanes": told}, sort_keys=True).encode()).hexdigest()[:16]
    kept = last(drive)
    scores, error = kept.get("scores", {}), None
    if key != kept.get("key") and told:
        try:
            scores = judge(text, told)
        except (TimeoutError, ValueError, DecideError, DecideKeyMissing, httpx.HTTPError) as failed:
            error = f"{type(failed).__name__}: {failed}"
            key = kept.get("key")
    order = sorted(lanes, key=lambda lane_id: (lanes[lane_id]["state"] == store.PARKED, -scores.get(lane_id, 0.0), lanes[lane_id]["created"]))
    asks = [{"lane": lane_id, "ts": ask.get("ts"), "at": ask["at"], "words": ask["words"][:200], "replied": ask.get("replied")} for lane_id in order for ask in sorted(lanes[lane_id].get("asks", []), key=lambda ask: ask["at"]) if not ask.get("done")]
    result = {"at": store.stamp(moment), "key": key, "order": order, "scores": scores, "asks": asks, "hurting": sorted(hurt), "error": error}
    fresh = path(drive).with_name(".rank.json.tmp")
    fresh.parent.mkdir(parents=True, exist_ok=True)
    fresh.write_text(json.dumps(result, indent=1) + "\n")
    fresh.replace(path(drive))
    return result
