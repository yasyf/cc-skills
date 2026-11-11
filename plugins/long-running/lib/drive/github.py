import json
import os
import re
import subprocess
import sys
from itertools import groupby, takewhile
from pathlib import Path

FIELDS = (
    "state isDraft headRefOid title reviewDecision mergeable mergeCommit { oid } labels(first: 50) { nodes { name } } "
    "commits(last: 1) { nodes { commit { statusCheckRollup { contexts(first: 100) { nodes { "
    "... on CheckRun { name conclusion detailsUrl checkSuite { workflowRun { workflow { name } } } } ... on StatusContext { context state } } } } } } }"
)
PR_STATES = {"OPEN": "open", "MERGED": "merged", "CLOSED": "closed"}
FAILED = {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}
PASSED = {"SUCCESS", "NEUTRAL", "SKIPPED"}
REVIEWS = {"APPROVED": "approved", "CHANGES_REQUESTED": "changes"}
CONFLICTS = {"CONFLICTING": True, "MERGEABLE": False}
COMPONENTS = re.compile(r"^Components: (?P<names>.+)$", re.MULTILINE)
CANDIDATE = "rc/"
HOLDS = ("ahead", "identical")
HOLD_LABEL = "hold"
EJECTED_LABEL = "trunk-failed"
RUN_URL = re.compile(r"/actions/runs/(?P<run>\d+)/")
PRS_PER_QUERY = 25
CALLER = " ".join([Path(sys.argv[0]).name, *takewhile(re.compile(r"[a-z][a-z_-]*").fullmatch, sys.argv[1:3])])


class GithubError(Exception):
    pass


def run(cmd: list[str], cwd: str | None = None, timeout: int = 120, max_age: str = "0s") -> str:
    """Run a command; a gh one goes through ccx's GitHub gateway, which charges it to this drive verb or hook in the machine-wide ledger, refuses it under the rate-limit floor, and answers a read from its cache when the same read was answered within max_age."""
    env = None
    if cmd[0] == "gh":
        cmd, env = ["ccx", "vcs", "gh", "--max-age", max_age, "--", *cmd[1:]], os.environ | {"CCX_CALLER": CALLER}
    try:
        result = subprocess.run(cmd, cwd=cwd, env=env, check=False, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GithubError(str(error)) from None
    if result.returncode:
        raise GithubError(" ".join(result.stderr.split()) or f"{cmd[0]} exited {result.returncode}")
    return result.stdout


def read(what: str, cmd: list[str], max_age: str = "0s") -> str:
    """Run a gh read; one that fails names what it could not read."""
    try:
        return run(cmd, max_age=max_age)
    except GithubError as error:
        raise GithubError(f"could not read {what} from GitHub: {error}") from None


def close(url: str) -> None:
    """Close a pull request on GitHub."""
    run(["gh", "pr", "close", url])


def default_branch(repo: str) -> str:
    return read(f"{repo}'s default branch", ["gh", "api", f"repos/{repo}", "--jq", ".default_branch"], max_age="1h").strip()


def visibility(repo: str) -> str | None:
    """public, private or internal, as GitHub reports the repository to us; None when it knows no repository by that name."""
    try:
        return read(f"{repo}'s visibility", ["gh", "api", f"repos/{repo}", "--jq", ".visibility"], max_age="1h").strip()
    except GithubError as error:
        if "HTTP 404" in str(error):
            return None
        raise


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
        "checks": [context(check) for check in rollup["contexts"]["nodes"]] if rollup else [],
        "reviewDecision": node["reviewDecision"],
        "mergeable": node["mergeable"],
        "title": node["title"],
        "labels": [label["name"] for label in node["labels"]["nodes"]],
    }


def outcome(check: dict) -> str:
    return check.get("conclusion") or check.get("state") or ""


def check_name(check: dict) -> str:
    return check.get("name") or check["context"]


def failed_before(stored: dict, head: str) -> list[str]:
    return stored["failed_checks"] if stored["head"] == head else []


def context(node: dict) -> dict:
    """A check run or status from the GraphQL rollup in the shape `gh pr view --json statusCheckRollup` gives it, naming a check run's workflow as workflowName, empty for a check no workflow ran."""
    if "checkSuite" not in node:
        return node
    run = node["checkSuite"]["workflowRun"]
    return {key: value for key, value in node.items() if key != "checkSuite"} | {"workflowName": run["workflow"]["name"] if run else ""}


def run_of(check: dict) -> tuple[str, str] | None:
    """The workflow and run id of a GitHub Actions check run, read from its details URL; None for a status or a check no workflow ran."""
    found = RUN_URL.search(check.get("detailsUrl") or "")
    return (check["workflowName"], found["run"]) if found else None


def counted(rollup: list[dict]) -> list[dict]:
    """The check runs and statuses that speak for one head: all but the cancelled jobs of a workflow run when another run of the same workflow on that head passed or is still running, as when a newer run cancels its duplicate."""
    going = {run_of(check) for check in rollup if outcome(check) not in FAILED} - {None}
    return [check for check in rollup if outcome(check) != "CANCELLED" or not superseded(run_of(check), going)]


def superseded(run: tuple[str, str] | None, going: set[tuple[str, str]]) -> bool:
    return run is not None and any(workflow == run[0] and number != run[1] for workflow, number in going)


def checks(rollup: list[dict], failed: list[str]) -> str:
    """pass, fail, pending or none for the checks that speak for one head, given the checks that failed on that head before: one queued or running reads pending, and so does one that failed before and is missing now, as a rerun drops it until its new run starts."""
    outcomes = {outcome(check) for check in counted(rollup)}
    if outcomes & FAILED:
        return "fail"
    if set(failed) - {check_name(check) for check in rollup}:
        return "pending"
    if not outcomes:
        return "none"
    return "pass" if outcomes <= PASSED else "pending"


def live(read: dict, stored: dict) -> dict:
    """The drive's stored fields (state, checks, the checks that failed on its head, review, head, title, whether it carries the hold label, conflict) of one fetched PR against its stored row; conflict only once GitHub has worked it out, since it reads UNKNOWN for a while after its base moves."""
    failed = sorted({*failed_before(stored, read["headRefOid"]), *(check_name(check) for check in counted(read["checks"]) if outcome(check) in FAILED)})
    fields = {
        "state": PR_STATES[read["state"]],
        "checks": checks(read["checks"], failed),
        "failed_checks": failed,
        "review": REVIEWS.get(read["reviewDecision"], "none"),
        "head": read["headRefOid"],
        "title": read["title"],
        "hold_label": HOLD_LABEL in read["labels"],
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


def graphql(text: str, what: str) -> dict:
    reply = read(what, ["gh", "api", "graphql", "-f", f"query={text}"])
    try:
        return json.loads(reply)
    except ValueError as error:
        raise GithubError(f"unreadable GraphQL reply for {what}: {error}") from None


def below(key: str) -> list[str]:
    """The open PRs stack-enqueue queues under key, bottom first: those below it in its native GitHub stack, or, with no native stack and a base other than its repository's default, the one open PR whose head is that base and those under it."""
    owner, name, number = split(key)
    text = (
        f"query {{ repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{ pullRequest(number: {number}) {{ baseRefName "
        "baseRef { associatedPullRequests(states: OPEN, first: 2) { nodes { number } } } "
        "stack { entries(first: 100) { nodes { position pullRequest { number state } } } } } } }"
    )
    pull = graphql(text, f"the native stack under {key}")["data"]["repository"]["pullRequest"]
    if pull["stack"]:
        entries = sorted(pull["stack"]["entries"]["nodes"], key=lambda entry: entry["position"])
        under = takewhile(lambda entry: entry["pullRequest"]["number"] != number, entries)
        return [f"{owner}/{name}#{entry['pullRequest']['number']}" for entry in under if entry["pullRequest"]["state"] == "OPEN"]
    opened = (pull["baseRef"] or {"associatedPullRequests": {"nodes": []}})["associatedPullRequests"]["nodes"]
    if pull["baseRefName"] == default_branch(f"{owner}/{name}") or len(opened) != 1:
        return []
    parent = f"{owner}/{name}#{opened[0]['number']}"
    return [*below(parent), parent]


def release_of(key: str, service: str, commit: str) -> str | None:
    """The name of the newest published release that ships the service, when it holds the commit; a release candidate, named rc/, ships to staging and never counts."""
    repo = repo_of(key)
    shipping = [release for release in json.loads(read(f"{repo}'s releases", ["gh", "api", f"repos/{repo}/releases?per_page=100"], max_age="60s")) if ships(release, service)]
    if not shipping:
        return None
    newest = max(shipping, key=lambda release: release["published_at"])
    compared = read(f"whether {repo}'s release {newest['tag_name']} holds {commit[:7]}", ["gh", "api", f"repos/{repo}/compare/{commit}...{newest['tag_name']}", "--jq", ".status"], max_age="1h")
    return newest["name"] if compared.strip() in HOLDS else None


def ships(release: dict, service: str) -> bool:
    """Whether a published release's notes list the service among the components it shipped."""
    listed = COMPONENTS.search(release["body"] or "")
    published = not release["draft"] and not release["prerelease"] and not release["name"].startswith(CANDIDATE)
    return published and bool(listed) and service in listed["names"].split(", ")


def fetch(keys: list[str]) -> tuple[dict[str, dict], GithubError | None]:
    """Read every PR key ("owner/name#123") PRS_PER_QUERY to a `gh api graphql` call, since GitHub refuses a query past its resource limits: the reads of every call that answered, and an error joining what the others could not read, so one refused call loses only its own PRs."""
    found, refused = {}, []
    for start in range(0, len(keys), PRS_PER_QUERY):
        chunk = keys[start : start + PRS_PER_QUERY]
        try:
            found |= parse(graphql(query(chunk), f"{len(chunk)} PRs of {', '.join(sorted({repo_of(key) for key in chunk}))}"), chunk)
        except GithubError as error:
            refused.append(str(error))
    return found, GithubError("; ".join(refused)) if refused else None


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
    """Every open PR whose head is one of the (owner/name, branch) heads, keyed "owner/name#123" with its branch, checks and review, in one `gh api graphql` call, however many heads there are."""
    repos = sorted({repo for repo, _ in heads})
    data = graphql(heads_query(heads, repos), f"the open PRs on running jobs' branches in {', '.join(repos)}").get("data") or {}
    found = {}
    for slot, (repo, branch) in enumerate(heads):
        for node in ((data.get(f"r{repos.index(repo)}") or {}).get(f"h{slot}") or {}).get("nodes", []):
            pulled = flatten(node)
            found[f"{repo}#{node['number']}"] = {
                "branch": branch,
                "checks": checks(pulled["checks"], []),
                "review": REVIEWS.get(pulled["reviewDecision"], "none"),
            }
    return found


def repo_name(remote: str) -> str:
    owner, name = re.split(r"[/:]", remote.strip().removesuffix("/").removesuffix(".git"))[-2:]
    return f"{owner}/{name}"


def checkout_repo(checkout: str) -> str:
    """The owner/name its origin remote names, of the checkout a drive's remote lanes clone."""
    return repo_name(run(["git", "-C", checkout, "remote", "get-url", "origin"]))
