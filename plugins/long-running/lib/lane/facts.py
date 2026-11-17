"""The outside facts a wait names, each read from its source: pull requests in one batched GraphQL query, a staging cut's or release's result file, and his messages in a lane's channel."""

import json
import os
import re
import subprocess
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from functools import cache
from pathlib import Path

from lane import store

DONE_ROLLUPS = ("SUCCESS", "FAILURE", "ERROR")
PRS_PER_QUERY = 50
COMMITS_READ = 50
LIGHT_PRS_PER_QUERY = 100
READERS = 8
ALERTING = ("Alert", "Warn", "No Data")
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


def read_prs(keys: set[tuple[str, int]], commits: int = COMMITS_READ) -> dict[tuple[str, int], dict]:
    """Each pull request's state, merged flag, head, base and head branches, its repository's default branch, review decision and check rollup, and its last `commits` commits with their authors, in GraphQL requests of PRS_PER_QUERY run READERS at a time, since one request for the ~490 PRs of twelve lanes outran its 60 seconds. The supervisor's pass asks for one commit, the head's, which is all a wait or the rank reads, so its read stays light; lane-land asks for COMMITS_READ."""
    ordered = sorted(keys)
    size = PRS_PER_QUERY if commits > 1 else LIGHT_PRS_PER_QUERY
    chunks = [ordered[start : start + size] for start in range(0, len(ordered), size)]
    with ThreadPoolExecutor(max_workers=READERS) as pool:
        return {key: pull for read in pool.map(lambda chunk: read_chunk(chunk, commits), chunks) for key, pull in read.items()}


def read_chunk(ordered: list[tuple[str, int]], commits: int = COMMITS_READ) -> dict[tuple[str, int], dict]:
    parts = []
    for index, (repo, number) in enumerate(ordered):
        owner, name = repo.split("/", 1)
        parts.append(
            f'p{index}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{ pullRequest(number: {number}) {{ title state merged mergedAt closedAt headRefOid baseRefName headRefName repository {{ defaultBranchRef {{ name }} }} reviewDecision latestReviews(first: 20) {{ nodes {{ state author {{ login }} commit {{ oid }} }} }} commits(last: {commits}) {{ totalCount nodes {{ commit {{ oid author {{ user {{ login }} }} statusCheckRollup {{ state }} }} }} }} }} }}'
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
        rollup = ((nodes[-1]["commit"].get("statusCheckRollup") or {}).get("state")) if nodes else None
        read[key] = {"title": pull["title"], "state": pull["state"], "merged": pull["merged"], "merged_at": pull.get("mergedAt"), "closed_at": pull.get("closedAt"), "head": pull["headRefOid"], "base": pull["baseRefName"], "branch": pull["headRefName"], "default": pull["repository"]["defaultBranchRef"]["name"], "review": pull["reviewDecision"], "rollup": rollup, "commits": [(node["commit"]["oid"], ((node["commit"]["author"] or {}).get("user") or {}).get("login")) for node in nodes], "commit_count": pull["commits"]["totalCount"], "approvals": {node["author"]["login"]: node["commit"]["oid"] for node in (pull.get("latestReviews") or {}).get("nodes") or [] if node["state"] == "APPROVED" and node.get("author") and node.get("commit")}}
    return read


@cache
def his_login(drive: str) -> str:
    """His GitHub login, whose approving reviews count as his Approve: the drive's config `his_login`, else the account gh is signed in to, his on this Mac."""
    if named := store.config(drive).get("his_login"):
        return named
    return subprocess.run(["gh", "api", "user", "-q", ".login"], capture_output=True, text=True, check=True, timeout=60).stdout.strip()


def side_path(drive: str, folder: str, name: str) -> Path:
    return store.drive_dir(drive) / folder / f"{name}.json"


def result_written(drive: str, folder: str, name: str) -> bool:
    """Whether a staging cut's or a release's result file, written by whoever ran it, holds its result."""
    path = side_path(drive, folder, name)
    return path.is_file() and "result" in json.loads(path.read_text())


def holds(drive: str, lane: dict, event: dict, prs: dict[tuple[str, int], dict]) -> bool:
    """Whether the outside fact a wait names holds now: a PR merged or closed, its checks finished at its head, a cut's or release's result written, a unit of a queue's resource free for the lane first in line for it, or an ask of his with no reply."""
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
        case "queue_grant":
            from lane import queue

            return queue.free_for(drive, event["ref"], lane["id"])
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


def datadog_keys(drive: str) -> dict[str, str]:
    """The Datadog API and application keys: DD_API_KEY and DD_APPLICATION_KEY in the environment, else the drive config's datadog_keys, {"command": argv, "names": {"DD_API_KEY": name, "DD_APPLICATION_KEY": name}}, whose command prints either those keys as a JSON object or a parameter-store answer, {"Parameters": [{"Name", "Value"}]}, read by those names. The keys are cached for the process."""
    if os.environ.get("DD_API_KEY") and os.environ.get("DD_APPLICATION_KEY"):
        return {"DD_API_KEY": os.environ["DD_API_KEY"], "DD_APPLICATION_KEY": os.environ["DD_APPLICATION_KEY"]}
    if drive in KEYS:
        return KEYS[drive]
    if not (shape := store.config(drive).get("datadog_keys")):
        raise store.Refused("no Datadog keys: set DD_API_KEY and DD_APPLICATION_KEY, or the drive config's datadog_keys")
    done = subprocess.run(shape["command"], capture_output=True, text=True, check=False, timeout=60, stdin=subprocess.DEVNULL)
    if done.returncode:
        raise store.Refused(f"the datadog_keys command failed: {done.stderr.strip()[:200]}")
    printed = json.loads(done.stdout)
    if "Parameters" in printed:
        values = {row["Name"]: row["Value"] for row in printed["Parameters"]}
        printed = {key: values[name] for key, name in shape["names"].items()}
    KEYS[drive] = {"DD_API_KEY": printed["DD_API_KEY"], "DD_APPLICATION_KEY": printed["DD_APPLICATION_KEY"]}
    return KEYS[drive]


KEYS: dict[str, dict[str, str]] = {}


def monitor_states(drive: str, ids: set[str]) -> dict[str, str]:
    """Each Datadog monitor's overall state, in one request for all of them."""
    if not ids:
        return {}
    keys = datadog_keys(drive)
    site = os.environ.get("DD_SITE", "datadoghq.com")
    request = urllib.request.Request(f"https://api.{site}/api/v1/monitor?monitor_ids={','.join(sorted(ids))}", headers={"DD-API-KEY": keys["DD_API_KEY"], "DD-APPLICATION-KEY": keys["DD_APPLICATION_KEY"]})
    try:
        with urllib.request.urlopen(request, timeout=20) as answer:
            return {str(monitor["id"]): monitor["overall_state"] for monitor in json.loads(answer.read())}
    except OSError as failed:
        KEYS.pop(drive, None)
        raise store.Refused(f"Datadog read of monitors {', '.join(sorted(ids))} failed: {failed}") from None


def hurting(lane: dict, monitors: dict[str, str]) -> bool:
    """Whether a lane's incident hurts users now, from facts and never a hand-set flag: its record is open with no fix live, and its Datadog monitor, when it names one, alerts by monitors, the states read this pass or else the last ones read; a monitor never read counts as alerting."""
    incident = lane.get("incident") or {}
    if not incident or incident.get("fix_live"):
        return False
    return not incident.get("monitor") or monitors.get(str(incident["monitor"]), "Alert") in ALERTING
