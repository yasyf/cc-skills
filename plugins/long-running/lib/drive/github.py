import json
import re
import subprocess
from itertools import groupby

BATCH = 50
FIELDS = (
    "state isDraft headRefOid title reviewDecision mergeable mergeCommit { oid } "
    "commits(last: 1) { nodes { commit { statusCheckRollup { contexts(first: 100) { nodes { "
    "... on CheckRun { name conclusion } ... on StatusContext { context state } } } } } } }"
)
PR_STATES = {"OPEN": "open", "MERGED": "merged", "CLOSED": "closed"}
FAILED = {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}
PASSED = {"SUCCESS", "NEUTRAL", "SKIPPED"}
REVIEWS = {"APPROVED": "approved", "CHANGES_REQUESTED": "changes"}
CONFLICTS = {"CONFLICTING": True, "MERGEABLE": False}


class GithubError(Exception):
    pass


def run(cmd: list[str]) -> str:
    try:
        result = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GithubError(str(error)) from None
    if result.returncode:
        raise GithubError(" ".join(result.stderr.split()) or f"{cmd[0]} exited {result.returncode}")
    return result.stdout


def split(key: str) -> tuple[str, str, int]:
    repo, number = key.split("#")
    owner, name = repo.split("/")
    return owner, name, int(number)


def repo_of(key: str) -> str:
    return key.split("#")[0]


def query(keys: list[str]) -> str:
    """One GraphQL query reading every PR in keys, aliased r<i> per repo and p<number> per PR."""
    blocks = []
    for index, (repo, members) in enumerate(groupby(sorted(keys, key=repo_of), key=repo_of)):
        owner, name = repo.split("/")
        pulls = " ".join(f"p{split(key)[2]}: pullRequest(number: {split(key)[2]}) {{ {FIELDS} }}" for key in members)
        blocks.append(f"r{index}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{ {pulls} }}")
    return "query { " + " ".join(blocks) + " }"


def flatten(node: dict) -> dict:
    commits = node["commits"]["nodes"]
    rollup = commits[0]["commit"]["statusCheckRollup"] if commits else None
    return {
        "state": node["state"],
        "isDraft": node["isDraft"],
        "headRefOid": node["headRefOid"],
        "mergeCommit": (node["mergeCommit"] or {}).get("oid"),
        "checks": rollup["contexts"]["nodes"] if rollup else [],
        "reviewDecision": node["reviewDecision"],
        "mergeable": node["mergeable"],
        "title": node["title"],
    }


def outcome(check: dict) -> str:
    return check.get("conclusion") or check.get("state") or ""


def check_name(check: dict) -> str:
    return check.get("name") or check["context"]


def failed_before(stored: dict, head: str) -> list[str]:
    return stored["failed_checks"] if stored["head"] == head else []


def checks(rollup: list[dict], failed: list[str]) -> str:
    """pass, fail, pending or none for one head's check runs and statuses, given the checks that failed on that head before: one queued or running reads pending, and so does one that failed before and is missing now, as a rerun drops it until its new run starts."""
    outcomes = {outcome(check) for check in rollup}
    if outcomes & FAILED:
        return "fail"
    if set(failed) - {check_name(check) for check in rollup}:
        return "pending"
    if not outcomes:
        return "none"
    return "pass" if outcomes <= PASSED else "pending"


def live(read: dict, stored: dict) -> dict:
    """The drive's stored fields (state, checks, the checks that failed on its head, review, head, title, conflict) of one fetched PR against its stored row; conflict only once GitHub has worked it out, since it reads UNKNOWN for a while after its base moves."""
    failed = sorted({*failed_before(stored, read["headRefOid"]), *(check_name(check) for check in read["checks"] if outcome(check) in FAILED)})
    fields = {
        "state": PR_STATES[read["state"]],
        "checks": checks(read["checks"], failed),
        "failed_checks": failed,
        "review": REVIEWS.get(read["reviewDecision"], "none"),
        "head": read["headRefOid"],
        "title": read["title"],
    }
    if read["mergeable"] in CONFLICTS:
        fields["conflict"] = CONFLICTS[read["mergeable"]]
    return fields


def parse(payload: dict, keys: list[str]) -> dict[str, dict]:
    repos = sorted({repo_of(key) for key in keys})
    alias = {repo: f"r{index}" for index, repo in enumerate(repos)}
    data = payload.get("data") or {}
    found = {}
    for key in keys:
        node = (data.get(alias[repo_of(key)]) or {}).get(f"p{split(key)[2]}")
        if node:
            found[key] = flatten(node)
    return found


def graphql(text: str) -> dict:
    reply = run(["gh", "api", "graphql", "-f", f"query={text}"])
    try:
        return json.loads(reply)
    except ValueError as error:
        raise GithubError(f"unreadable GraphQL reply: {error}") from None


def fetch(keys: list[str]) -> dict[str, dict]:
    """Read every PR key ("owner/name#123") in batches of 50, one `gh api graphql` call per batch."""
    found = {}
    for start in range(0, len(keys), BATCH):
        chunk = keys[start : start + BATCH]
        found.update(parse(graphql(query(chunk)), chunk))
    return found


def heads_query(heads: list[tuple[str, str]], repos: list[str]) -> str:
    """One GraphQL query for the open PRs of every (owner/name, branch) head, aliased r<i> per repo and h<j> per head."""
    blocks = []
    for index, repo in enumerate(repos):
        owner, name = repo.split("/")
        pulls = " ".join(
            f"h{slot}: pullRequests(headRefName: {json.dumps(branch)}, states: OPEN, first: 10) {{ nodes {{ number {FIELDS} }} }}"
            for slot, (other, branch) in enumerate(heads)
            if other == repo
        )
        blocks.append(f"r{index}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{ {pulls} }}")
    return "query { " + " ".join(blocks) + " }"


def open_on(heads: list[tuple[str, str]]) -> dict[str, dict]:
    """Every open PR whose head is one of the (owner/name, branch) heads, keyed "owner/name#123" with its branch, checks and review, one `gh api graphql` call per 50 heads."""
    found = {}
    for start in range(0, len(heads), BATCH):
        chunk = heads[start : start + BATCH]
        repos = sorted({repo for repo, _ in chunk})
        data = graphql(heads_query(chunk, repos)).get("data") or {}
        for slot, (repo, branch) in enumerate(chunk):
            for node in ((data.get(f"r{repos.index(repo)}") or {}).get(f"h{slot}") or {}).get("nodes", []):
                read = flatten(node)
                found[f"{repo}#{node['number']}"] = {
                    "branch": branch,
                    "checks": checks(read["checks"], []),
                    "review": REVIEWS.get(read["reviewDecision"], "none"),
                }
    return found


def repo_name(remote: str) -> str:
    owner, name = re.split(r"[/:]", remote.strip().removesuffix("/").removesuffix(".git"))[-2:]
    return f"{owner}/{name}"


def checkout_repo(checkout: str) -> str:
    """The owner/name its origin remote names, of the checkout a drive's remote lanes clone."""
    return repo_name(run(["git", "-C", checkout, "remote", "get-url", "origin"]))
