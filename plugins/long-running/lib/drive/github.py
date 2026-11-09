import json
import subprocess
from itertools import groupby

BATCH = 50
FIELDS = (
    "state isDraft headRefOid title reviewDecision mergeCommit { oid } "
    "commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }"
)
PR_STATES = {"OPEN": "open", "MERGED": "merged", "CLOSED": "closed"}
CHECKS = {"SUCCESS": "pass", "FAILURE": "fail", "ERROR": "fail", "PENDING": "pending", "EXPECTED": "pending"}
REVIEWS = {"APPROVED": "approved", "CHANGES_REQUESTED": "changes"}


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
        "checks": rollup["state"] if rollup else None,
        "reviewDecision": node["reviewDecision"],
        "title": node["title"],
    }


def live(read: dict) -> dict:
    """The drive's stored fields (state, checks, review, head, title) of one fetched PR."""
    return {
        "state": PR_STATES[read["state"]],
        "checks": CHECKS.get(read["checks"], "none"),
        "review": REVIEWS.get(read["reviewDecision"], "none"),
        "head": read["headRefOid"],
        "title": read["title"],
    }


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


def fetch(keys: list[str]) -> dict[str, dict]:
    """Read every PR key ("owner/name#123") in batches of 50, one `gh api graphql` call per batch."""
    found = {}
    for start in range(0, len(keys), BATCH):
        chunk = keys[start : start + BATCH]
        reply = run(["gh", "api", "graphql", "-f", f"query={query(chunk)}"])
        try:
            payload = json.loads(reply)
        except ValueError as error:
            raise GithubError(f"unreadable GraphQL reply: {error}") from None
        found.update(parse(payload, chunk))
    return found
