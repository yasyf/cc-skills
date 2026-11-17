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
PRS_PER_QUERY = 40
CHUNK_SECONDS = 45
CHUNK_TRIES = 2
GH_ENV = "LONG_RUNNING_GH"
COMMITS_READ = 50
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


def read_prs(keys: set[tuple[str, int]], commits: int = COMMITS_READ, failed: list[str] | None = None) -> dict[tuple[str, int], dict]:
    """Each pull request's state, merged flag, head, base and head branches, its repository's default branch, review decision and check rollup, and its last `commits` commits with their authors, in GraphQL requests of PRS_PER_QUERY run READERS at a time, each with its own timeout and CHUNK_TRIES tries, since the ~516 PRs of twelve lanes timed out or came back cut in bigger requests. A chunk that still fails drops only its own PRs, its why added to failed; refused only when every chunk failed. The supervisor's pass asks for one commit, the head's; lane-land asks for COMMITS_READ."""
    ordered = sorted(keys)
    chunks = [ordered[start : start + PRS_PER_QUERY] for start in range(0, len(ordered), PRS_PER_QUERY)]

    def tried(chunk: list[tuple[str, int]]) -> dict | str:
        why = ""
        for _ in range(CHUNK_TRIES):
            try:
                return read_chunk(chunk, commits)
            except (store.Refused, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
                why = f"{len(chunk)} PRs from {chunk[0][0]}#{chunk[0][1]}: {type(error).__name__}: {str(error)[:200]}"
        return why

    with ThreadPoolExecutor(max_workers=READERS) as pool:
        reads = list(pool.map(tried, chunks))
    lost = [read for read in reads if isinstance(read, str)]
    if failed is not None:
        failed += lost
    if chunks and len(lost) == len(chunks):
        raise store.Refused(f"GitHub read failed for every chunk: {lost[0]}")
    found = {key: pull for read in reads if isinstance(read, dict) for key, pull in read.items()}
    red = sorted(key for key, pull in found.items() if pull["state"] == "OPEN" and pull["rollup"] in ("FAILURE", "ERROR"))
    for start in range(0, len(red), PRS_PER_QUERY):
        try:
            for key, failing in failing_checks(red[start : start + PRS_PER_QUERY]).items():
                found[key]["failing"] = failing
        except (store.Refused, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
            if failed is not None:
                failed.append(f"failing checks of {len(red[start : start + PRS_PER_QUERY])} red PRs: {type(error).__name__}: {str(error)[:200]}")
    return found


def failing_checks(ordered: list[tuple[str, int]]) -> dict[tuple[str, int], list[dict]]:
    """Each red PR's failing checks at its head, by name, with what each says, read for the red PRs alone so the pass's big read carries no check contexts; a merge queue's own check is told apart from the PR's CI by its name."""
    parts = []
    for index, (repo, number) in enumerate(ordered):
        owner, name = repo.split("/", 1)
        parts.append(f'p{index}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{ pullRequest(number: {number}) {{ commits(last: 1) {{ nodes {{ commit {{ statusCheckRollup {{ contexts(first: 100) {{ nodes {{ __typename ... on CheckRun {{ name conclusion title }} ... on StatusContext {{ context state description }} }} }} }} }} }} }} }} }}')
    done = subprocess.run([os.environ.get(GH_ENV) or "gh", "api", "graphql", "-f", f"query={{ {' '.join(parts)} }}"], capture_output=True, text=True, check=False, timeout=CHUNK_SECONDS)
    if done.returncode:
        raise store.Refused(f"GitHub read failed: {done.stderr.strip()[:300]}")
    data = json.loads(done.stdout)["data"]
    found = {}
    for index, key in enumerate(ordered):
        nodes = (((data.get(f"p{index}") or {}).get("pullRequest") or {}).get("commits") or {}).get("nodes") or []
        contexts = ((nodes[-1]["commit"].get("statusCheckRollup") or {}).get("contexts") or {}).get("nodes") or [] if nodes else []
        found[key] = [{"name": node.get("name") or node.get("context"), "says": node.get("title") or node.get("description") or ""} for node in contexts if node.get("conclusion") in ("FAILURE", "TIMED_OUT", "CANCELLED", "STARTUP_FAILURE", "ACTION_REQUIRED") or node.get("state") in ("FAILURE", "ERROR")]
    return found


def read_chunk(ordered: list[tuple[str, int]], commits: int = COMMITS_READ) -> dict[tuple[str, int], dict]:
    parts = []
    for index, (repo, number) in enumerate(ordered):
        owner, name = repo.split("/", 1)
        parts.append(
            f'p{index}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{ pullRequest(number: {number}) {{ title state merged mergedAt mergeCommit {{ oid }} closedAt mergeable headRefOid baseRefName headRefName repository {{ defaultBranchRef {{ name }} }} reviewDecision latestReviews(first: 20) {{ nodes {{ state author {{ login }} commit {{ oid }} }} }} commits(last: {commits}) {{ totalCount nodes {{ commit {{ oid author {{ user {{ login }} }} statusCheckRollup {{ state }} }} }} }} }} }}'
        )
    done = subprocess.run([os.environ.get(GH_ENV) or "gh", "api", "graphql", "-f", f"query={{ {' '.join(parts)} }}"], capture_output=True, text=True, check=False, timeout=CHUNK_SECONDS)
    if done.returncode:
        raise store.Refused(f"GitHub read failed: {done.stderr.strip()[:300]}")
    data = json.loads(done.stdout)["data"]
    read = {}
    for index, key in enumerate(ordered):
        pull = (data.get(f"p{index}") or {}).get("pullRequest")
        if pull is None:
            continue
        nodes = pull["commits"]["nodes"]
        checks = (nodes[-1]["commit"].get("statusCheckRollup") or {}) if nodes else {}
        rollup = checks.get("state")
        read[key] = {"title": pull["title"], "state": pull["state"], "merged": pull["merged"], "merged_at": pull.get("mergedAt"), "merge_commit": (pull.get("mergeCommit") or {}).get("oid"), "closed_at": pull.get("closedAt"), "head": pull["headRefOid"], "base": pull["baseRefName"], "branch": pull["headRefName"], "default": pull["repository"]["defaultBranchRef"]["name"], "review": pull["reviewDecision"], "rollup": rollup, "mergeable": pull.get("mergeable"), "reviewed_heads": sorted({node["commit"]["oid"] for node in (pull.get("latestReviews") or {}).get("nodes") or [] if node.get("commit")}), "commits": [(node["commit"]["oid"], ((node["commit"]["author"] or {}).get("user") or {}).get("login")) for node in nodes], "commit_count": pull["commits"]["totalCount"], "approvals": {node["author"]["login"]: node["commit"]["oid"] for node in (pull.get("latestReviews") or {}).get("nodes") or [] if node["state"] == "APPROVED" and node.get("author") and node.get("commit")}}
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
        case "work":
            from lane import work

            item = work.load(drive, event["ref"])
            return item is None or item["state"] in (work.DONE, work.FAILED)
        case "his_reply":
            return bool(store.unreplied(lane))
    raise store.Refused(f"waiting on {event['kind']!r} is not an outside fact")


def unproduced(drive: str, lane: dict, event: dict, prs: dict[tuple[str, int], dict]) -> str | None:
    """Why nothing is producing the fact a wait names, or None while something is: a staging result with no stager's ask open for its cut, checks with no CI at the PR's head, a merge of a PR no Approve of his, no card asking for one and no tooling mark will land, a work item that does not exist, a queue grant the lane is not in line for, and a release the release tool has no record of. His reply, for a lane with an unmet done check, needs a card or question of the lane's still open to him: one he has not tapped since it was asked."""
    match event["kind"]:
        case "staging_result":
            from lane import stager

            asked = stager.wants(drive).get(lane["id"]) or {}
            return None if asked.get("cut") == event["ref"] or result_written(drive, "cuts", event["ref"]) else f"no stager has an ask open for cut {event['ref']}"
        case "check":
            pull = prs.get(pr_ref(event["ref"]))
            return f"no CI runs at the head of {event['ref']}" if pull and pull["state"] == "OPEN" and pull.get("rollup") is None else None
        case "pr_merged":
            pull = prs.get(pr_ref(event["ref"]))
            row = next((row for row in lane["prs"] if (row["repo"], row["number"]) == pr_ref(event["ref"])), None)
            if not pull or pull["state"] != "OPEN" or row is None:
                return None
            if row.get("approved_head") or (row.get("guarded") or {}).get("tooling") or (row.get("carded") or {}).get("head") == pull["head"]:
                return None
            return f"nothing will merge {event['ref']}: no Approve of his, no tooling mark, and no card asks him for one"
        case "his_reply":
            return None if store.unreplied(lane) or asking(lane) or not any(not check.get("met_at") for check in lane.get("done", [])) else "no card or question of this lane is open to him, so nothing he could answer would end it"
        case "work":
            from lane import work

            item = work.load(drive, event["ref"])
            if item is None:
                return f"no work item {event['ref']} exists"
            return None if item["state"] in (work.QUEUED, work.TAKEN, work.DONE, work.FAILED) and lane["state"] != store.PARKED else f"work item {event['ref']} waits on a parked lane"
        case "queue_grant":
            from lane import queue

            return None if event["ref"] in queue.asking(drive, lane["id"]) or queue.free_for(drive, event["ref"], lane["id"]) else f"this lane is not in line for {event['ref']}: ask with `lane queue take {event['ref']}`"
        case "release":
            return None if side_path(drive, "releases", event["ref"]).is_file() else f"the release tool knows no release {event['ref']}"
    return None


def asking(lane: dict) -> bool:
    """Whether a card or question of the lane is open to him: an Approve card he has not tapped, or a question with buttons asked after his newest tap."""
    if any(not card.get("tapped") and not card.get("superseded") for card in lane.get("cards", [])):
        return True
    taps = [store.parse(ask["at"]) for ask in lane.get("asks", []) if ask["words"].startswith("tapped ")]
    return bool(lane.get("asked_at")) and (not taps or store.parse(lane["asked_at"]) > max(taps))


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
    """His Slack identity, read from `cc-slack whoami` once and kept in the lanes' owner-user file, so a failing whoami never costs a pass once it has answered."""
    kept = store.root() / "owner-user.json"
    if kept.is_file():
        return json.loads(kept.read_text())
    done = subprocess.run(["cc-slack", "whoami"], capture_output=True, text=True, check=True, timeout=30)
    who = json.loads(done.stdout)
    fresh = kept.with_name(f".owner-user.{os.getpid()}.tmp")
    fresh.write_text(json.dumps({"user_id": who["user_id"]}))
    fresh.replace(kept)
    return who


def said_at(link: str) -> str:
    """The text of his own Slack message at link, read from Slack; refused when it is no message of his."""
    done = subprocess.run(["cc-slack", "thread", "--url", link, "--json", "--limit", "1"], capture_output=True, text=True, check=False, timeout=60)
    if done.returncode:
        raise store.Refused(f"Slack read of {link} failed: {done.stderr.strip()[:300]}")
    ts = re.search(r"/p(\d{10})(\d{6})", link)
    message = next((message for message in json.loads(done.stdout).get("messages") or [] if ts and message["ts"] == f"{ts[1]}.{ts[2]}"), None)
    if message is None or message.get("user") != owner_user()["user_id"] or message.get("from_claude"):
        raise store.Refused(f"{link} is no message of his")
    return message.get("text") or ""


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
