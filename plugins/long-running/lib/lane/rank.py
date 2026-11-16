"""The priority queue across lanes: a model reads the rank rule against each lane's facts, re-read every supervisor pass and asked again whenever the rule or the facts change."""

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


def facts(lane: dict, moment: datetime) -> dict:
    asks = lane.get("asks", [])
    return {
        "id": lane["id"],
        "title": lane["title"],
        "parked": bool(lane.get("parked")),
        "pinned": bool(lane.get("pinned")),
        "incident": lane.get("incident"),
        "his_last_words": ago(asks[-1]["at"] if asks else None, moment),
        "unread_words": sum(1 for ask in asks if not ask.get("seen")),
        "open_prs": len([pr for pr in lane.get("prs", []) if not pr.get("merged")]),
        "approved_open": len([pr for pr in lane.get("prs", []) if pr.get("approved_head") and not pr.get("merged")]),
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


def ranked(drive: str, lanes: dict[str, dict], moment: datetime) -> dict:
    """The order lanes take slots and staging in, best first: the model's scores when the rule or the facts changed since the last ask, else the last ones; a failed ask keeps the last scores and says why."""
    text = rule(drive)
    told = [facts(lane, moment) for lane in lanes.values()]
    key = hashlib.sha256(json.dumps({"rule": text, "lanes": told}, sort_keys=True).encode()).hexdigest()[:16]
    kept = last(drive)
    scores, error = kept.get("scores", {}), None
    if key != kept.get("key") and told:
        try:
            scores = judge(text, told)
        except (TimeoutError, ValueError, DecideError, DecideKeyMissing, httpx.HTTPError) as failed:
            error = f"{type(failed).__name__}: {failed}"
            key = kept.get("key")
    order = sorted(lanes, key=lambda lane_id: (bool(lanes[lane_id].get("parked")), -scores.get(lane_id, 0.0), lanes[lane_id]["created"]))
    result = {"at": store.stamp(moment), "key": key, "order": order, "scores": scores, "error": error}
    fresh = path(drive).with_name(".rank.json.tmp")
    fresh.parent.mkdir(parents=True, exist_ok=True)
    fresh.write_text(json.dumps(result, indent=1) + "\n")
    fresh.replace(path(drive))
    return result
