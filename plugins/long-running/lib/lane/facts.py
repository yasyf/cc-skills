"""The outside facts a wait names, each read from its source: pull requests in one batched GraphQL query, a staging cut's result file, and his messages in a lane's channel."""

import json
import subprocess
from functools import cache
from pathlib import Path

from lane import store

DONE_ROLLUPS = ("SUCCESS", "FAILURE", "ERROR")


def pr_key(event: dict) -> tuple[str, int]:
    return event["repo"], int(event["number"])


def read_prs(keys: set[tuple[str, int]]) -> dict[tuple[str, int], dict]:
    """Each pull request's state, merged flag, head, review decision and check rollup, in one GraphQL request."""
    if not keys:
        return {}
    ordered = sorted(keys)
    parts = []
    for index, (repo, number) in enumerate(ordered):
        owner, name = repo.split("/", 1)
        parts.append(
            f'p{index}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{ pullRequest(number: {number}) {{ state merged headRefOid reviewDecision commits(last: 1) {{ nodes {{ commit {{ statusCheckRollup {{ state }} }} }} }} }} }}'
        )
    done = subprocess.run(["gh", "api", "graphql", "-f", f"query={{ {' '.join(parts)} }}"], capture_output=True, text=True, check=False, timeout=60)
    if done.returncode:
        raise store.Refused(f"GitHub read failed: {done.stderr.strip()[:300]}")
    data = json.loads(done.stdout)["data"]
    read = {}
    for index, key in enumerate(ordered):
        pull = (data.get(f"p{index}") or {}).get("pullRequest")
        if pull is None:
            continue
        nodes = pull["commits"]["nodes"]
        rollup = ((nodes[0]["commit"].get("statusCheckRollup") or {}).get("state")) if nodes else None
        read[key] = {"state": pull["state"], "merged": pull["merged"], "head": pull["headRefOid"], "review": pull["reviewDecision"], "rollup": rollup}
    return read


def pr_holds(event: dict, pull: dict | None) -> bool:
    if pull is None:
        return False
    match event["fact"]:
        case "merged":
            return pull["merged"]
        case "closed":
            return pull["state"] in ("CLOSED", "MERGED")
        case "checks_done":
            return pull["rollup"] in DONE_ROLLUPS or pull["head"] != event.get("head", pull["head"])
        case "approved":
            return pull["review"] == "APPROVED"
        case "head_moved":
            return pull["head"] != event.get("head")
    raise store.Refused(f"unknown pull request fact {event['fact']!r}; one of {', '.join(store.PR_FACTS)}")


def cut_path(drive: str, cut: str) -> Path:
    return store.drive_dir(drive) / "cuts" / f"{cut}.json"


def cut_holds(drive: str, event: dict) -> bool:
    path = cut_path(drive, event["id"])
    return path.is_file() and "result" in json.loads(path.read_text())


def unseen(lane: dict) -> list[dict]:
    return [ask for ask in lane.get("asks", []) if not ask.get("seen")]


def holds(drive: str, lane: dict, event: dict, prs: dict[tuple[str, int], dict]) -> bool:
    match event["kind"]:
        case "pr":
            return pr_holds(event, prs.get(pr_key(event)))
        case "cut":
            return cut_holds(drive, event)
        case "reply":
            return bool(unseen(lane))
    raise store.Refused(f"unknown event kind {event['kind']!r}")


def baseline(event: dict, prs: dict[tuple[str, int], dict]) -> dict:
    """The event with the head its pull request has now, which head_moved and checks_done are measured against."""
    if event["kind"] == "pr" and event["fact"] in ("head_moved", "checks_done") and (pull := prs.get(pr_key(event))):
        return {**event, "head": pull["head"]}
    return event


@cache
def owner_user() -> dict:
    done = subprocess.run(["cc-slack", "whoami"], capture_output=True, text=True, check=True, timeout=30)
    return json.loads(done.stdout)


def his_messages(lane: dict) -> list[dict]:
    """His messages in the lane's channel newer than its newest ask, each as an ask row."""
    channel = lane.get("channel") or {}
    if not channel.get("id"):
        return []
    who = owner_user()
    after = max((ask["ts"] for ask in lane.get("asks", []) if ask.get("ts")), key=float, default=None)
    if channel.get("thread"):
        argv = ["cc-slack", "thread", "--channel", channel["id"], "--thread", channel["thread"], "--json", "--limit", "50", *(["--since-ts", after] if after else [])]
    else:
        argv = ["cc-slack", "history", "--channel", channel["id"], "--json", "--limit", "20"]
    done = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=60)
    if done.returncode:
        raise store.Refused(f"Slack read of lane {lane['id']} failed: {done.stderr.strip()[:300]}")
    messages = json.loads(done.stdout).get("messages") or []
    found = []
    for message in messages:
        if message.get("user") != who["user_id"] or message.get("from_claude") or (after and float(message["ts"]) <= float(after)):
            continue
        link = f"{who['team_url']}archives/{channel['id']}/p{message['ts'].replace('.', '')}" + (f"?thread_ts={channel['thread']}&cid={channel['id']}" if channel.get("thread") else "")
        found.append({"at": store.stamp(), "words": message.get("text") or "", "link": link, "ts": message["ts"], "seen": False})
    return sorted(found, key=lambda ask: float(ask["ts"]))
