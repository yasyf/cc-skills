"""The outside facts a wait names, each read from its source: pull requests in one batched GraphQL query, a staging cut's or release's result file, and his messages in a lane's channel."""

import json
import re
import subprocess
from functools import cache
from pathlib import Path

from lane import store

DONE_ROLLUPS = ("SUCCESS", "FAILURE", "ERROR")
CHANNEL_EVENT = re.compile(r"^(<@\w+> has (joined|left) the channel|set the channel (description|topic|purpose|name)|renamed the channel)")


def pr_ref(ref: str) -> tuple[str, int]:
    repo, _, number = ref.partition("#")
    if "/" not in repo or not number.isdigit():
        raise store.Refused(f"a PR ref is <owner/repo>#<number>, not {ref!r}")
    return repo, int(number)


def pr_keys(lanes: dict[str, dict]) -> set[tuple[str, int]]:
    """Every PR a lane holds or a wait names: the set each pass reads in its one GitHub request."""
    keys = {(row["repo"], row["number"]) for lane in lanes.values() for row in lane.get("prs", [])}
    return keys | {pr_ref(event["ref"]) for lane in lanes.values() for event in (lane.get("wait") or {}).get("on", []) if event["kind"] in ("pr_merged", "check")}


def read_prs(keys: set[tuple[str, int]]) -> dict[tuple[str, int], dict]:
    """Each pull request's state, merged flag, head, base and head branches, its repository's default branch, review decision and check rollup, in one GraphQL request."""
    if not keys:
        return {}
    ordered = sorted(keys)
    parts = []
    for index, (repo, number) in enumerate(ordered):
        owner, name = repo.split("/", 1)
        parts.append(
            f'p{index}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{ pullRequest(number: {number}) {{ state merged headRefOid baseRefName headRefName repository {{ defaultBranchRef {{ name }} }} reviewDecision commits(last: 1) {{ nodes {{ commit {{ statusCheckRollup {{ state }} }} }} }} }} }}'
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
        read[key] = {"state": pull["state"], "merged": pull["merged"], "head": pull["headRefOid"], "base": pull["baseRefName"], "branch": pull["headRefName"], "default": pull["repository"]["defaultBranchRef"]["name"], "review": pull["reviewDecision"], "rollup": rollup}
    return read


def side_path(drive: str, folder: str, name: str) -> Path:
    return store.drive_dir(drive) / folder / f"{name}.json"


def result_written(drive: str, folder: str, name: str) -> bool:
    """Whether a staging cut's or a release's result file, written by whoever ran it, holds its result."""
    path = side_path(drive, folder, name)
    return path.is_file() and "result" in json.loads(path.read_text())


def holds(drive: str, lane: dict, event: dict, prs: dict[tuple[str, int], dict]) -> bool:
    """Whether the outside fact a wait names holds now: a PR merged or closed, its checks finished at its head, a cut's or release's result written, or an ask of his with no reply."""
    match event["kind"]:
        case "pr_merged":
            pull = prs.get(pr_ref(event["ref"]))
            return bool(pull) and pull["state"] in ("MERGED", "CLOSED")
        case "check":
            pull = prs.get(pr_ref(event["ref"]))
            return bool(pull) and pull["rollup"] in DONE_ROLLUPS
        case "staging_result":
            return result_written(drive, "cuts", event["ref"])
        case "release":
            return result_written(drive, "releases", event["ref"])
        case "his_reply":
            return bool(store.unreplied(lane))
    raise store.Refused(f"waiting on {event['kind']!r} is not an outside fact")


CHECKS = {"SUCCESS": "pass", "FAILURE": "fail", "ERROR": "fail"}


def viewed(lanes: dict[str, dict], prs: dict[tuple[str, int], dict]) -> dict[str, dict]:
    """Each lane with GitHub's state on its PR rows, held in memory only, never written: head, checks, merged, and under, the open PR whose branch is its base, None on the default branch, absent when its base is a branch no open PR heads, which landing reads as unread."""
    heads = {(repo, pull["branch"]): f"{repo}#{number}" for (repo, number), pull in prs.items() if pull["state"] == "OPEN"}
    shown = json.loads(json.dumps(lanes))
    for lane in shown.values():
        for row in lane.get("prs", []):
            if not (pull := prs.get((row["repo"], row["number"]))):
                continue
            row["head"] = pull["head"]
            row["checks"] = CHECKS.get(pull["rollup"] or "", "pending")
            row["merged"] = pull["merged"]
            if pull["base"] == pull["default"]:
                row["under"] = None
            elif below := heads.get((row["repo"], pull["base"])):
                row["under"] = below
    return shown


def view(drive: str) -> dict[str, dict]:
    """Every lane with GitHub's state on its rows, from one GitHub request."""
    lanes = store.load_all(drive)
    return viewed(lanes, read_prs(pr_keys(lanes)))


@cache
def owner_user() -> dict:
    done = subprocess.run(["cc-slack", "whoami"], capture_output=True, text=True, check=True, timeout=30)
    return json.loads(done.stdout)


def his_messages(lane: dict) -> list[dict]:
    """His messages in the lane's channel newer than its newest ask, each as an ask row."""
    if not lane.get("channel"):
        return []
    who = owner_user()
    after = max((ask["ts"] for ask in lane.get("asks", []) if ask.get("ts")), key=float, default=None)
    if lane.get("thread"):
        argv = ["cc-slack", "thread", "--channel", lane["channel"], "--thread", lane["thread"], "--json", "--limit", "50", *(["--since-ts", after] if after else [])]
    else:
        argv = ["cc-slack", "history", "--channel", lane["channel"], "--json", "--limit", "20"]
    done = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=60)
    if done.returncode:
        raise store.Refused(f"Slack read of lane {lane['id']} failed: {done.stderr.strip()[:300]}")
    messages = json.loads(done.stdout).get("messages") or []
    found = []
    for message in messages:
        if message.get("user") != who["user_id"] or message.get("from_claude") or CHANNEL_EVENT.match(message.get("text") or "") or (after and float(message["ts"]) <= float(after)):
            continue
        link = f"{who['team_url']}archives/{lane['channel']}/p{message['ts'].replace('.', '')}" + (f"?thread_ts={lane['thread']}&cid={lane['channel']}" if lane.get("thread") else "")
        found.append({"words": message.get("text") or "", "link": link, "at": store.stamp(), "ts": message["ts"], "replied": None, "done": None})
    return sorted(found, key=lambda ask: float(ask["ts"]))
