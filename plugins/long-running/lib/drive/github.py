import json
import os
import re
import subprocess
import sys
from itertools import groupby, takewhile
from pathlib import Path

NUMBER = "<number>"
REQUIRED = f"isRequired(pullRequestNumber: {NUMBER})"
FIELDS = (
    "state isDraft headRefOid baseRefName headRefName title author { login } reviewDecision latestOpinionatedReviews(first: 20, writersOnly: true) { nodes { state commit { oid } } } "
    "mergeable mergeCommit { oid } labels(first: 50) { nodes { name } } "
    "commits(last: 1) { nodes { commit { statusCheckRollup { contexts(first: 100) { nodes { "
    f"... on CheckRun {{ name conclusion detailsUrl {REQUIRED} checkSuite {{ workflowRun {{ workflow {{ name }} }} }} }} ... on StatusContext {{ context state targetUrl {REQUIRED} }} }} }} }} }} }} }}"
)
RULES = "defaultBranchRef { rules(first: 50) { nodes { type parameters { ... on RequiredStatusChecksParameters { requiredStatusChecks { context } } } } } }"
PR_STATES = {"OPEN": "open", "MERGED": "merged", "CLOSED": "closed"}
FAILED = {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}
PASSED = {"SUCCESS", "NEUTRAL", "SKIPPED"}
REVIEWS = {"APPROVED": "approved", "CHANGES_REQUESTED": "changes"}
CONFLICTS = {"CONFLICTING": True, "MERGEABLE": False}
PRODUCTION = re.compile(r"^Production: (?P<names>.+)$", re.MULTILINE)
HOLDS = ("ahead", "identical")
HOLD_LABEL = "hold"
EJECTED_LABEL = "trunk-failed"
RUN_URL = re.compile(r"/actions/runs/(?P<run>\d+)/")
PRS_PER_QUERY = 25
STACK = "stack { entries(first: 100) { nodes { position pullRequest { number state } } } }"
DELETED_AUTHOR = "ghost"
CALLER = " ".join([Path(sys.argv[0]).name, *takewhile(re.compile(r"[a-z][a-z_-]*").fullmatch, sys.argv[1:3])])


class GithubError(Exception):
    pass


def run(cmd: list[str], cwd: str | None = None, timeout: int = 120, max_age: str = "0s", text: bool = True) -> str | bytes:
    """Run a command, holding no install's lock while it runs, its output as text or, with text off, as the bytes it wrote; a gh one goes through ccx's GitHub gateway, which charges it to this drive verb or hook in the machine-wide ledger, refuses it under the rate-limit floor, and answers a read from its cache when the same read was answered within max_age."""
    from drive import state

    state.let_go_install()
    env = None
    if cmd[0] == "gh":
        cmd, env = ["ccx", "vcs", "gh", "--max-age", max_age, "--", *cmd[1:]], os.environ | {"CCX_CALLER": CALLER}
    try:
        result = subprocess.run(cmd, cwd=cwd, env=env, check=False, capture_output=True, text=text, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GithubError(str(error)) from None
    if result.returncode:
        stderr = result.stderr if text else result.stderr.decode(errors="replace")
        raise GithubError(" ".join(stderr.split()) or f"{cmd[0]} exited {result.returncode}")
    return result.stdout


def read(what: str, cmd: list[str], max_age: str = "0s", text: bool = True) -> str | bytes:
    """Run a gh read; one that fails names what it could not read."""
    try:
        return run(cmd, max_age=max_age, text=text)
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


def query(keys: list[str], fields: str = FIELDS, repository: str = "") -> str:
    """One GraphQL query reading the fields of every PR in keys, aliased r<i> per repo and p<number> per PR, each check's isRequired asked for that PR, the only way GitHub answers it, and the repository fields of each repo."""
    blocks = []
    for index, (repo, members) in enumerate(groupby(sorted(keys, key=repo_of), key=repo_of)):
        owner, name = repo.split("/")
        pulls = " ".join(f"p{split(key)[2]}: pullRequest(number: {split(key)[2]}) {{ {fields.replace(NUMBER, str(split(key)[2]))} }}" for key in members)
        blocks.append(f"r{index}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{ {repository} {pulls} }}")
    return "query { " + " ".join(blocks) + " }"


def flatten(node: dict) -> dict:
    commits = node["commits"]["nodes"]
    rollup = commits[0]["commit"]["statusCheckRollup"] if commits else None
    return {
        "state": node["state"],
        "isDraft": node["isDraft"],
        "headRefOid": node["headRefOid"],
        "baseRefName": node["baseRefName"],
        "headRefName": node["headRefName"],
        "mergeCommit": (node["mergeCommit"] or {}).get("oid"),
        "checks": [context(check) for check in rollup["contexts"]["nodes"]] if rollup else [],
        "reviewDecision": node["reviewDecision"],
        "approvals": [(review["commit"] or {}).get("oid") for review in node["latestOpinionatedReviews"]["nodes"] if review["state"] == "APPROVED"],
        "mergeable": node["mergeable"],
        "title": node["title"],
        "author": (node["author"] or {"login": DELETED_AUTHOR})["login"],
        "labels": [label["name"] for label in node["labels"]["nodes"]],
    }


def review(read: dict) -> str:
    """approved once every reviewer's latest approval is on the PR's head, the only approval stack-enqueue counts; stale while GitHub's reviewDecision still counts one given on an older head; changes or none otherwise."""
    decision = REVIEWS.get(read["reviewDecision"], "none")
    if decision == "approved" and any(commit != read["headRefOid"] for commit in read["approvals"]):
        return "stale"
    return decision


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


def checks(rollup: list[dict], expected: list[str]) -> str:
    """pass, fail, pending or none for the checks that speak for one head, given the checks expected on it: one queued or running reads pending, and so does an expected one missing now, such as one that failed there before, as a rerun drops it until its new run starts."""
    outcomes = {outcome(check) for check in counted(rollup)}
    if outcomes & FAILED:
        return "fail"
    if set(expected) - {check_name(check) for check in rollup}:
        return "pending"
    if not outcomes:
        return "none"
    return "pass" if outcomes <= PASSED else "pending"


def required(read: dict) -> dict:
    """The verdict on the checks the PR must pass before merging, those the rules of its repository's default branch name and any GitHub marks isRequired on its head: pass, fail, pending or none over them as checks() reads, a named one not yet posted on the head reading pending and none when the rules name none and GitHub marks none, and the build or run link of each that failed."""
    names = read["required_checks"]
    held = [check for check in counted(read["checks"]) if check.get("isRequired") or check_name(check) in names]
    return {"checks": checks(held, names), "failed": {check_name(check): check.get("targetUrl") or check.get("detailsUrl") or "" for check in held if outcome(check) in FAILED}}


def rule_checks(repository: dict) -> list[str]:
    """The checks the rules of a repository's default branch require before merging, as GitHub's GraphQL answers RULES."""
    rules = repository["defaultBranchRef"]["rules"]["nodes"]
    return sorted({check["context"] for rule in rules if rule["type"] == "REQUIRED_STATUS_CHECKS" for check in rule["parameters"]["requiredStatusChecks"]})


def live(read: dict, stored: dict) -> dict:
    """The drive's stored fields (state, checks, the checks that failed on its head, the required checks' verdict, review, head, its base and head branches, title, author, whether it carries the hold label, the PRs of its native GitHub stack that are not closed, conflict) of one fetched PR against its stored row; conflict only once GitHub has worked it out, since it reads UNKNOWN for a while after its base moves."""
    failed = sorted({*failed_before(stored, read["headRefOid"]), *(check_name(check) for check in counted(read["checks"]) if outcome(check) in FAILED)})
    fields = {
        "state": PR_STATES[read["state"]],
        "checks": checks(read["checks"], failed),
        "failed_checks": failed,
        "required": required(read),
        "review": review(read),
        "head": read["headRefOid"],
        "base": read["baseRefName"],
        "branch": read["headRefName"],
        "title": read["title"],
        "author": read["author"],
        "hold_label": HOLD_LABEL in read["labels"],
        "native": [member for member, status in read["native"] if status != "closed"],
    }
    if read["mergeable"] in CONFLICTS:
        fields["conflict"] = CONFLICTS[read["mergeable"]]
    return fields


def repositories(payload: dict, keys: list[str]) -> dict[str, dict]:
    """Each repository's node in the reply to query(keys), leaving out one GitHub answered none for."""
    data = payload.get("data") or {}
    return {repo: node for index, repo in enumerate(sorted({repo_of(key) for key in keys})) if (node := data.get(f"r{index}"))}


def nodes(payload: dict, keys: list[str]) -> dict[str, dict]:
    """Each PR's node in the reply to query(keys), leaving out a PR GitHub answered none for."""
    found = repositories(payload, keys)
    return {key: node for key in keys if (node := (found.get(repo_of(key)) or {}).get(f"p{split(key)[2]}"))}


def parse(payload: dict, keys: list[str]) -> dict[str, dict]:
    found = repositories(payload, keys)
    return {
        key: flatten(node) | {"native": entries(repo_of(key), node["stack"]) if node["stack"] else [], "required_checks": rule_checks(found[repo_of(key)])}
        for key, node in nodes(payload, keys).items()
    }


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
        f"baseRef {{ associatedPullRequests(states: OPEN, first: 2) {{ nodes {{ number }} }} }} {STACK} }} }} }}"
    )
    pull = graphql(text, f"the native stack under {key}")["data"]["repository"]["pullRequest"]
    if pull["stack"]:
        under = takewhile(lambda entry: entry[0] != key, entries(f"{owner}/{name}", pull["stack"]))
        return [member for member, status in under if status == "open"]
    opened = (pull["baseRef"] or {"associatedPullRequests": {"nodes": []}})["associatedPullRequests"]["nodes"]
    if pull["baseRefName"] == default_branch(f"{owner}/{name}") or len(opened) != 1:
        return []
    parent = f"{owner}/{name}#{opened[0]['number']}"
    return [*below(parent), parent]


def entries(repo: str, stack: dict) -> list[tuple[str, str]]:
    """Each PR of a native GitHub stack, bottom first, with its state as the drive stores it."""
    ordered = sorted(stack["entries"]["nodes"], key=lambda entry: entry["position"])
    return [(f"{repo}#{entry['pullRequest']['number']}", PR_STATES[entry["pullRequest"]["state"]]) for entry in ordered]


def stacks(keys: list[str]) -> dict[str, list[tuple[str, str]]]:
    """Each PR's native GitHub stack, the one stack-enqueue lands, as entries() gives it, PRS_PER_QUERY PRs to a `gh api graphql` call; a PR in no native stack is left out."""
    found = {}
    for start in range(0, len(keys), PRS_PER_QUERY):
        chunk = keys[start : start + PRS_PER_QUERY]
        for key, node in nodes(graphql(query(chunk, STACK), f"the native stacks of {', '.join(chunk)}"), chunk).items():
            if node["stack"]:
                found[key] = entries(repo_of(key), node["stack"])
    return found


def release_of(key: str, service: str, commit: str) -> str | None:
    """The name of the newest published release that ships the service to production, when it holds the commit."""
    repo = repo_of(key)
    shipping = [release for release in json.loads(read(f"{repo}'s releases", ["gh", "api", f"repos/{repo}/releases?per_page=100"], max_age="60s")) if ships(release, service)]
    if not shipping:
        return None
    newest = max(shipping, key=lambda release: release["published_at"])
    compared = read(f"whether {repo}'s release {newest['tag_name']} holds {commit[:7]}", ["gh", "api", f"repos/{repo}/compare/{commit}...{newest['tag_name']}", "--jq", ".status"], max_age="1h")
    return newest["name"] if compared.strip() in HOLDS else None


def ships(release: dict, service: str) -> bool:
    """Whether a published release's notes name the service on their Production line, which go/ci's release finish step writes from the components it applied to an environment flagged prod: true in infra/envs; a release with no such line, staging-only or written before it, never counts."""
    listed = PRODUCTION.search(release["body"] or "")
    return not release["draft"] and bool(listed) and service in listed["names"].split(", ")


def fetch(keys: list[str]) -> tuple[dict[str, dict], GithubError | None]:
    """Read every PR key ("owner/name#123"), its native GitHub stack and the checks the rules of its repository's default branch require included, PRS_PER_QUERY to a `gh api graphql` call, since GitHub refuses a query past its resource limits: the reads of every call that answered, and an error joining what the others could not read, so one refused call loses only its own PRs."""
    found, refused = {}, []
    for start in range(0, len(keys), PRS_PER_QUERY):
        chunk = keys[start : start + PRS_PER_QUERY]
        try:
            found |= parse(graphql(query(chunk, f"{FIELDS} {STACK}", RULES), f"{len(chunk)} PRs of {', '.join(sorted({repo_of(key) for key in chunk}))}"), chunk)
        except GithubError as error:
            refused.append(str(error))
    return found, GithubError("; ".join(refused)) if refused else None


def heads(keys: list[str]) -> dict[str, str]:
    """Each PR's head on GitHub now, by one GraphQL read of that field alone."""
    found = nodes(graphql(query(keys, "headRefOid"), f"the heads of {', '.join(keys)}"), keys)
    if missing := [key for key in keys if key not in found]:
        raise GithubError(f"GitHub answered no head for {', '.join(missing)}")
    return {key: node["headRefOid"] for key, node in found.items()}

def heads_query(heads: list[tuple[str, str]], repos: list[str]) -> str:
    """One GraphQL query for the open PRs of every (owner/name, branch) head, aliased r<i> per repo and h<j> per head."""
    blocks = []
    for index, repo in enumerate(repos):
        owner, name = repo.split("/")
        pulls = " ".join(
            f"h{slot}: pullRequests(headRefName: {json.dumps(branch)}, states: OPEN, first: 10) {{ nodes {{ number {FIELDS.replace(f' {REQUIRED}', '')} }} }}"
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
                "review": review(pulled),
            }
    return found


def repo_name(remote: str) -> str:
    owner, name = re.split(r"[/:]", remote.strip().removesuffix("/").removesuffix(".git"))[-2:]
    return f"{owner}/{name}"


def checkout_repo(checkout: str) -> str:
    """The owner/name its origin remote names, of the checkout a drive's remote lanes clone."""
    return repo_name(run(["git", "-C", checkout, "remote", "get-url", "origin"]))


def fetched(clone: str, branch: str) -> str:
    """The sha of branch's head as origin holds it now, fetched with one call into the clone's refs/remotes/origin/<branch>."""
    git = ["git", "-C", clone]
    run([*git, "fetch", "-q", "--no-tags", "--no-write-fetch-head", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}"])
    return run([*git, "rev-parse", f"refs/remotes/origin/{branch}"]).strip()
