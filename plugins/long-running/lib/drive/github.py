import functools
import json
import os
import posixpath
import re
import subprocess
import sys
import time
import tomllib
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from fnmatch import fnmatchcase
from itertools import groupby, takewhile
from pathlib import Path

NUMBER = "<number>"
REQUIRED = f"isRequired(pullRequestNumber: {NUMBER})"
FIELDS = (
    "state isDraft headRefOid baseRefName headRefName title lastEditedAt author { login } reviewDecision latestOpinionatedReviews(first: 20, writersOnly: true) { nodes { state commit { oid } } } reviews(last: 50) { nodes { state author { login } commit { oid } } } "
    "mergeable mergeCommit { oid } mergedAt labels(first: 50) { nodes { name } } timelineItems(itemTypes: [LABELED_EVENT], last: 20) { nodes { ... on LabeledEvent { createdAt label { name } } } } "
    "commits(last: 1) { nodes { commit { statusCheckRollup { contexts(first: 100) { nodes { "
    f"... on CheckRun {{ name conclusion detailsUrl {REQUIRED} checkSuite {{ workflowRun {{ workflow {{ name }} }} }} }} ... on StatusContext {{ context state targetUrl {REQUIRED} }} }} }} }} }} }} }}"
)
LANDING = 'landing: object(expression: "HEAD:.claude/long-running.toml") { ... on Blob { text } }'
RULES = "defaultBranchRef { name rules(first: 50) { nodes { type parameters { ... on RequiredStatusChecksParameters { requiredStatusChecks { context } } ... on PullRequestParameters { requiredApprovingReviewCount allowedMergeMethods } } } } } squashMergeAllowed rebaseMergeAllowed mergeCommitAllowed viewerPermission " + LANDING
METHODS = {"squash": "squashMergeAllowed", "rebase": "rebaseMergeAllowed", "merge": "mergeCommitAllowed"}
PR_STATES = {"OPEN": "open", "MERGED": "merged", "CLOSED": "closed"}
FOLDED = "folded"
UNLANDED = ("open", FOLDED)
ON_DEFAULT = {"BEHIND", "IDENTICAL"}
OWNERS_PER_BASE = 100
OWNER = "number state isCrossRepository createdAt closedAt mergedAt baseRefName mergeCommit { oid }"
FAILED = {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}
PASSED = {"SUCCESS", "NEUTRAL", "SKIPPED"}
REVIEWS = {"APPROVED": "approved", "CHANGES_REQUESTED": "changes"}
SUBMITTED = ("APPROVED", "CHANGES_REQUESTED", "COMMENTED")
BUILD = re.compile(r"https://buildkite\.com/(?P<pipeline>[\w-]+/[\w-]+)/builds/(?P<number>\d+)")
BUILDKITE_ORG = "forge"
HOLD_LABEL = "hold"
EJECTED_LABEL = "trunk-failed"
CANCELLED_LABEL = "trunk-cancelled"
TRUNK_CHECK = "Trunk Merge Queue"
RUN_URL = re.compile(r"/actions/runs/(?P<run>\d+)/")
JOB_URL = re.compile(r"/actions/runs/\d+/job/(?P<job>\d+)")
FAILED_TEST = re.compile(r"^\S+ (?:FAILED|ERROR) (?P<pytest>\S+\.py(?:::[^\r\n]+?)?)(?: - [^\r\n]*)?\r?$|--- FAIL: (?P<go>[^\s\"\\]+)", re.MULTILINE)
PRS_PER_QUERY = 25
MERGEABILITY = "state headRefOid mergeable"
MERGEABILITY_PER_QUERY = 100
PATHS = "headRefOid changedFiles files(first: 100) { nodes { path } }"
STACKS_AGE = "60s"
STACK = "stack { baseRefName entries(first: 100) { nodes { position pullRequest { number state } } } }"
UNDER = "baseRef { associatedPullRequests(states: OPEN, first: 2) { nodes { number } } }"
READ = f"{FIELDS} {UNDER} {STACK}"
DELETED_AUTHOR = "ghost"
PICK = re.compile(r" \(#(\d+)\)$")
DIFF = ["--no-renames", "--no-ext-diff", "--no-color", "--no-textconv", "-U0", "--src-prefix=a/", "--dst-prefix=b/"]
UNSHIPPED = re.compile(r"(?:^|/)(?:AGENTS\.md|CLAUDE\.md|[^/]*_test\.go)$|(?:^|/)(?:tests|testdata)/")
EMBEDS = "//go:embed[[:space:]]|include_(str|bytes)!|(from|import|require|glob)[[:space:]]*\\(?[[:space:]]*['\"`][^'\"`]*(\\.md|\\*)"
EMBEDDING = ["*.go", "*.rs", "*.js", "*.jsx", "*.mjs", "*.cjs", "*.ts", "*.tsx"]
QUOTED = re.compile(r"\"([^\"]*)\"|`([^`]*)`|'([^']*)'")
CALLER = " ".join([Path(sys.argv[0]).name, *takewhile(re.compile(r"[a-z][a-z_-]*").fullmatch, sys.argv[1:3])])
FLOOR = re.compile(r"ccx: GitHub rate limit floor: [^(]*\((?P<reset>[^)]+)\)")
UNANSWERED = re.compile(r'\b(?:Get|Head|Post|Put|Patch|Delete) "https?://[^"]+": |\bHTTP 5\d\d\b|TLS handshake timeout|context deadline exceeded|i/o timeout|connection reset', re.IGNORECASE)


class GithubError(Exception):
    pass


def run(cmd: list[str], cwd: str | None = None, timeout: int = 120, max_age: str = "0s", text: bool = True, stdin: str | None = None) -> str | bytes:
    """Run a command, given stdin when named, its output as text or, with text off, as the bytes it wrote; a gh one goes through ccx's GitHub gateway, which charges it to this drive verb or hook in the machine-wide ledger, refuses it under the rate-limit floor, retries a read that failed on a transient network error, and answers a read from its cache when the same read was answered within max_age. One still running at timeout is killed, its failure naming it by its words up to its first flag, never its arguments such as a GraphQL query, with what it wrote to stderr by then."""
    named = " ".join(takewhile(lambda word: not word.startswith("-"), cmd))
    env = None
    if cmd[0] == "gh":
        cmd, env = ["ccx", "vcs", "gh", "--max-age", max_age, "--", *cmd[1:]], os.environ | {"CCX_CALLER": CALLER}
    try:
        result = subprocess.run(cmd, cwd=cwd, env=env, input=stdin, check=False, capture_output=True, text=text, timeout=timeout)
    except OSError as error:
        raise GithubError(str(error)) from None
    except subprocess.TimeoutExpired as error:
        said = " ".join((error.stderr or b"").decode(errors="replace").split())
        raise GithubError(f"{named} timed out after {timeout} s" + (f": {said}" if said else "")) from None
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


def read_past_floor(what: str, cmd: list[str]) -> str:
    """Run a gh read, waiting out ccx's rate-limit floor: a read it refuses there sleeps until the reset the refusal names and runs again, since the floor says nothing of what GitHub would answer; each wait is said on stderr with the refusal whole, which floor_lifts_in reads."""
    while True:
        try:
            return read(what, cmd)
        except GithubError as error:
            if (wait := floor_lifts_in(str(error))) is None:
                raise
            print(f"{CALLER}: waiting {round(wait)} s for ccx's GitHub rate-limit floor to lift before reading {what} again: {error}", file=sys.stderr, flush=True)
            time.sleep(wait)


def floor_lifts_in(said: str) -> float | None:
    """Seconds from now to a second past the reset the last ccx rate-limit floor refusal in what a command said names, or None when it names none."""
    if not (resets := FLOOR.findall(said)):
        return None
    return max(datetime.fromisoformat(resets[-1]).timestamp() - time.time(), 0) + 1


def close(url: str) -> None:
    """Close a pull request on GitHub."""
    run(["gh", "pr", "close", url])


def files(key: str) -> list[str]:
    """The path of each file a PR changes, as GitHub lists them."""
    owner, name, number = split(key)
    return run(["gh", "api", "--paginate", f"repos/{owner}/{name}/pulls/{number}/files", "--jq", ".[].filename"]).splitlines()


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


def by_repository(keys: list[str], size: int) -> list[list[str]]:
    """The keys cut into lists of at most size PRs of one repository each, in the order named within each, since a GraphQL query naming one repository alone runs on that repository's own token, as ccx gives a GitHub App's installation token to a query of its repositories alone, and costs GitHub's compute for that repository's PRs alone."""
    grouped = [list(members) for _, members in groupby(sorted(keys, key=repo_of), key=repo_of)]
    return [members[start : start + size] for members in grouped for start in range(0, len(members), size)]


def repository_query(repo: str, fields: str) -> str:
    owner, name = repo.split("/")
    return f"query {{ r0: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{ {fields} }} }}"


def query(keys: list[str], fields: str = FIELDS, repository: str = "") -> str:
    """One GraphQL query reading the fields of every PR in keys, all of one repository, aliased r0 and p<number> per PR, each check's isRequired asked for that PR, the only way GitHub answers it, and the repository's fields."""
    (repo,) = {repo_of(key) for key in keys}
    pulls = " ".join(f"p{split(key)[2]}: pullRequest(number: {split(key)[2]}) {{ {fields.replace(NUMBER, str(split(key)[2]))} }}" for key in keys)
    return repository_query(repo, f"{repository} {pulls}")


def flatten(node: dict) -> dict:
    """One PR's GraphQL node as the drive reads it, with ejected_at and cancelled_at, when Trunk last put the trunk-failed or trunk-cancelled label it carries on it, or None when it carries none, and reviews, each reviewer's newest submitted review among its last 50 by casefolded login, with the commit it reviewed and its state, a review that only comments counting too, which GitHub's latestReviews leaves out."""
    commits = node["commits"]["nodes"]
    rollup = commits[0]["commit"]["statusCheckRollup"] if commits else None
    labels = [label["name"] for label in node["labels"]["nodes"]]
    return {
        "state": node["state"],
        "isDraft": node["isDraft"],
        "headRefOid": node["headRefOid"],
        "baseRefName": node["baseRefName"],
        "headRefName": node["headRefName"],
        "mergeCommit": (node["mergeCommit"] or {}).get("oid"),
        "mergedAt": node["mergedAt"],
        "checks": [context(check) for check in rollup["contexts"]["nodes"]] if rollup else [],
        "reviewDecision": node["reviewDecision"],
        "approvals": [(review["commit"] or {}).get("oid") for review in node["latestOpinionatedReviews"]["nodes"] if review["state"] == "APPROVED"],
        "reviews": {review["author"]["login"].casefold(): {"login": review["author"]["login"], "head": review["commit"]["oid"], "state": review["state"]} for review in node["reviews"]["nodes"] if review["author"] and review["commit"] and review["state"] in SUBMITTED},
        "mergeable": node["mergeable"],
        "title": node["title"],
        "lastEditedAt": node["lastEditedAt"],
        "author": (node["author"] or {"login": DELETED_AUTHOR})["login"],
        "labels": labels,
        "ejected_at": labeled(node, labels, EJECTED_LABEL),
        "cancelled_at": labeled(node, labels, CANCELLED_LABEL),
    }


def labeled(node: dict, labels: list[str], label: str) -> str | None:
    return max((event["createdAt"] for event in node["timelineItems"]["nodes"] if event["label"]["name"] == label), default=None) if label in labels else None


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


def job_of(check: dict) -> int | None:
    """The job id of a GitHub Actions check run, read from its details URL; None for a status or a check no workflow ran."""
    return int(found["job"]) if (found := JOB_URL.search(check.get("detailsUrl") or "")) else None


def job_log(repo: str, job: int) -> str:
    return read(f"{repo}'s job {job} log", ["gh", "api", f"repos/{repo}/actions/jobs/{job}/logs"])


def failed_at_checkout(repo: str, job: int) -> bool:
    """Whether a GitHub Actions job failed on its run's first attempt at a checkout, before any step could run what the checkout fetches: the first of its steps that failed names a checkout, as actions/checkout's unnamed step reads "Run actions/checkout@<sha>"; an ended job's record never changes, so a read of it within the hour answers."""
    ran = json.loads(read(f"{repo}'s job {job}", ["gh", "api", f"repos/{repo}/actions/jobs/{job}"], max_age="1h"))
    first = next((step["name"] for step in ran["steps"] if step["conclusion"] == "failure"), "")
    return ran["run_attempt"] == 1 and "checkout" in first.casefold()


def failed_tests(log: str) -> set[str]:
    """Each test a GitHub Actions job's log names failing: each node id pytest's short test summary says FAILED or ERROR, and each test go test says `--- FAIL:` of, plainly or inside `go test -json` output."""
    return {found["pytest"] or found["go"] for found in FAILED_TEST.finditer(log)}


def counted(rollup: list[dict]) -> list[dict]:
    """The check runs and statuses that speak for one head: all but Trunk's merge queue check, whose failure is the queue's end of a landing that the lander reads, and the cancelled jobs of a workflow run when another run of the same workflow on that head passed or is still running, as when a newer run cancels its duplicate."""
    going = {run_of(check) for check in rollup if outcome(check) not in FAILED} - {None}
    return [check for check in rollup if not check_name(check).startswith(TRUNK_CHECK) and (outcome(check) != "CANCELLED" or not superseded(run_of(check), going))]


def superseded(run: tuple[str, str] | None, going: set[tuple[str, str]]) -> bool:
    return run is not None and any(workflow == run[0] and number != run[1] for workflow, number in going)


def failing(rollup: list[dict]) -> list[str]:
    """The name of each check that speaks for one head and failed there."""
    return sorted({check_name(check) for check in counted(rollup) if outcome(check) in FAILED})


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


def rule_reviews(repository: dict) -> int:
    """The approving reviews the rules of a repository's default branch require before merging, as GitHub's GraphQL answers RULES: 0 when no pull request rule names any."""
    rules = repository["defaultBranchRef"]["rules"]["nodes"]
    return max((rule["parameters"]["requiredApprovingReviewCount"] for rule in rules if rule["type"] == "PULL_REQUEST"), default=0)


def merge_method(repository: dict) -> str:
    """The method the lander merges a PR of a repository by, as GitHub's GraphQL answers RULES: squash, else rebase, else merge, the first its settings allow that every pull request rule of its default branch allows too, a rule naming no allowedMergeMethods allowing each, since GitHub refuses a merge by any other method and gh pr merge picks none for a caller with no terminal."""
    rules = [rule["parameters"]["allowedMergeMethods"] for rule in repository["defaultBranchRef"]["rules"]["nodes"] if rule["type"] == "PULL_REQUEST"]
    return next(method for method, setting in METHODS.items() if repository[setting] and all(allowed is None or method.upper() in allowed for allowed in rules))


def live(read: dict, stored: dict) -> dict:
    """The drive's stored fields (state, the PR it folded into and the merge commit carrying it onto its repository's default branch, by landing(), its repository's default branch, checks, the checks that failed on its head, the required checks' verdict, review, head, its base and head branches, the open PR it sits on, by parent(), the commits its base holds that the default branch lacks, by ahead(), title, author, whether it carries the hold label, the PRs of its native GitHub stack that are not closed and that stack's trunk, the approving reviews its repository's rules require, the method its repository merges it by, what its repository lets this account do, as GitHub's viewerPermission reads it, conflict, by conflicts(), and whether its base is a branch its repository lands PRs on, by landing_base()) of one fetched PR against its stored row."""
    failed = sorted({*failed_before(stored, read["headRefOid"]), *failing(read["checks"])})
    fields = landing(read) | {
        "default": read["default"],
        "checks": checks(read["checks"], failed),
        "failed_checks": failed,
        "required": required(read),
        "review": review(read),
        "head": read["headRefOid"],
        "base": read["baseRefName"],
        "branch": read["headRefName"],
        "under": read["under"],
        "base_ahead": read["base_ahead"],
        "title": read["title"],
        "author": read["author"],
        "hold_label": HOLD_LABEL in read["labels"],
        "native": [member for member, status in read["native"] if status != "closed"],
        "trunk": read["trunk"],
        "reviews_required": read["required_reviews"],
        "merge_method": read["merge_method"],
        "permission": read["permission"],
        "conflict": conflicts(read, stored),
        "landing_base": landing_base(read["baseRefName"], read["default"], read["landing_bases"]),
    }
    return fields


def conflicts(read: dict, stored: dict) -> bool:
    """Whether a PR conflicts with its base, from GitHub's read of its head and mergeability against its stored row: while GitHub answers CONFLICTING, and through its UNKNOWN at the stored head, which is no answer while it works mergeability out again after a base move, so a conflict GitHub has not cleared never flips its stage; a new head reads no conflict until GitHub answers for it."""
    return read["mergeable"] == "CONFLICTING" or read["mergeable"] == "UNKNOWN" and read["headRefOid"] == stored["head"] and stored["conflict"]


def landing(read: dict) -> dict:
    """Whether a PR landed, the one rule every reader of the drive's PR state goes through: open or closed as GitHub reads it; merged with its own merge commit while GitHub reads it merged with no base_pr, the read fetch() gives of the PR whose head its base was; else as that PR lands, since none of its change is on its repository's default branch until then: folded into it while it is open, into naming it, merged with its merge commit once it lands, closed once it closes unmerged, and folded into the PR it folded into once it did, into naming the PR that carries it."""
    if read["state"] != "MERGED":
        return {"state": PR_STATES[read["state"]], "into": None, "merged_sha": None}
    if not (parent := read["base_pr"]):
        return {"state": "merged", "into": None, "merged_sha": read["mergeCommit"]}
    onward = landing(parent)
    return {"state": FOLDED if onward["state"] == "open" else onward["state"], "into": onward["into"] or parent["key"], "merged_sha": onward["merged_sha"]}


def fold_line(pr: dict) -> str:
    """What the watch says of a PR that folded into another open PR's branch."""
    return f"merged into {pr['base']} (#{split(pr['into'])[2]}), not {pr['default']}"


def answered(payload: dict) -> dict:
    """The repository's node in the reply to a query of one repository, empty when GitHub answered none for it."""
    return (payload.get("data") or {}).get("r0") or {}


def nodes(payload: dict, keys: list[str]) -> dict[str, dict]:
    """Each PR's node in the reply to query(keys), leaving out a PR GitHub answered none for."""
    found = answered(payload)
    return {key: node for key in keys if (node := found.get(f"p{split(key)[2]}"))}


def parse(payload: dict, keys: list[str]) -> dict[str, dict]:
    repository = answered(payload)
    return {
        key: flatten(node)
        | {
            "key": key,
            "native": entries(repo_of(key), node["stack"]) if node["stack"] else [],
            "trunk": node["stack"]["baseRefName"] if node["stack"] else None,
            "required_checks": rule_checks(repository),
            "required_reviews": rule_reviews(repository),
            "merge_method": merge_method(repository),
            "permission": repository["viewerPermission"],
            "default": repository["defaultBranchRef"]["name"],
            "landing_bases": landing_bases(repository),
            "under": parent(repo_of(key), landing_base(node["baseRefName"], repository["defaultBranchRef"]["name"], landing_bases(repository)), node["baseRef"]),
            "base_ahead": None,
            "base_pr": None,
        }
        for key, node in nodes(payload, keys).items()
    }


def graphql(text: str, what: str, max_age: str = "0s") -> dict:
    reply = read(what, ["gh", "api", "graphql", "-f", f"query={text}"], max_age=max_age)
    try:
        return json.loads(reply)
    except ValueError as error:
        raise GithubError(f"unreadable GraphQL reply for {what}: {error}") from None


def below(key: str) -> list[str]:
    """The open PRs stack-enqueue queues under key, bottom first: those below it in its native GitHub stack and those under the lowest of them, or, at the bottom of a native stack or in none, on a base that is no branch its repository lands PRs on, by landing_base(), the one open PR whose head is that base and those under it."""
    owner, name, number = split(key)
    text = (
        f"query {{ repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{ defaultBranchRef {{ name }} {LANDING} pullRequest(number: {number}) {{ baseRefName "
        f"{UNDER} {STACK} }} }} }}"
    )
    repository = graphql(text, f"the native stack under {key}")["data"]["repository"]
    pull = repository["pullRequest"]
    under = [member for member, status in takewhile(lambda entry: entry[0] != key, entries(f"{owner}/{name}", pull["stack"])) if status == "open"] if pull["stack"] else []
    if under:
        return [*below(under[0]), *under]
    if not (pr := parent(f"{owner}/{name}", landing_base(pull["baseRefName"], repository["defaultBranchRef"]["name"], landing_bases(repository)), pull["baseRef"])):
        return []
    return [*below(pr), pr]


def parent(repo: str, landed: bool, ref: dict | None) -> str | None:
    """The PR a PR of repo sits on, from the open PRs GitHub's UNDER reads heading its base branch: the one same-repository open PR whose head is its base, or None when its base is a branch its repository lands PRs on, by landing_base(), whatever PR heads it, no open PR heads it, or more than one does."""
    opened = (ref or {"associatedPullRequests": {"nodes": []}})["associatedPullRequests"]["nodes"]
    return f"{repo}#{opened[0]['number']}" if not landed and len(opened) == 1 else None


def landing_bases(repository: dict) -> list[str]:
    """The branches besides its default that a repository lands PRs on, such as a release line kept beside it: those the [landing] table of the .claude/long-running.toml on its default branch names as its bases, as GitHub's GraphQL answers LANDING; none when it holds no such file."""
    return tomllib.loads(repository["landing"]["text"]).get("landing", {}).get("bases", []) if repository["landing"] else []


def landing_base(base: str, default: str, bases: list[str]) -> bool:
    """Whether a PR's base is a branch its repository lands PRs on, its default branch or one of the bases landing_bases() reads, so the PR lands there and never reads as sitting on another PR's branch or on a stale one; the one rule the stage, the lander and every read of a merge go through."""
    return base == default or base in bases


def entries(repo: str, stack: dict) -> list[tuple[str, str]]:
    """Each PR of a native GitHub stack, bottom first, with its state as the drive stores it."""
    ordered = sorted(stack["entries"]["nodes"], key=lambda entry: entry["position"])
    return [(f"{repo}#{entry['pullRequest']['number']}", PR_STATES[entry["pullRequest"]["state"]]) for entry in ordered]


def stacks(keys: list[str]) -> dict[str, list[tuple[str, str]]]:
    """Each PR's native GitHub stack, the one stack-enqueue lands, as entries() gives it, PRS_PER_QUERY PRs of one repository to a `gh api graphql` call read through ccx's cache up to STACKS_AGE old, since a card asks again for the same PRs within a post; a PR in no native stack is left out."""
    found = {}
    for chunk in by_repository(keys, PRS_PER_QUERY):
        for key, node in nodes(graphql(query(chunk, STACK), f"the native stacks of {', '.join(chunk)}", STACKS_AGE), chunk).items():
            if node["stack"]:
                found[key] = entries(repo_of(key), node["stack"])
    return found


def holds(repo: str, ref: str, commit: str) -> bool:
    """Whether the history of ref, a commit or tag on repo, holds commit, as GitHub's compare of the two reads it, a commit holding itself with no read."""
    return ref == commit or read(f"whether {repo}'s {ref} holds {commit[:7]}", ["gh", "api", f"repos/{repo}/compare/{commit}...{ref}", "--jq", ".status"], max_age="1h").strip() in ("ahead", "identical")


def build(pipeline: str, number: str) -> dict:
    """A Buildkite build as `bk build view` reads it now: its state, its jobs and the environment it started with, such as a go/ci release's RELEASE_START or a cluster job's CLUSTER_JOB."""
    try:
        return json.loads(run(["bk", "build", "view", number, "-p", pipeline, "-o", "json"]))
    except GithubError as error:
        raise GithubError(f"could not read {pipeline} build {number} from Buildkite: {error}") from None


def finished(pipeline: str, branch: str) -> dict:
    """The newest build of a Buildkite pipeline on a branch that passed or failed, as `bk build list --summary` reads it: the branch's latest verdict, past the builds still running there."""
    try:
        found = json.loads(run(["bk", "build", "list", "-p", pipeline, "--branch", branch, "--state", "passed,failed", "--limit", "1", "--summary", "-o", "json"]))
    except GithubError as error:
        raise GithubError(f"could not read {pipeline}'s newest finished {branch} build from Buildkite: {error}") from None
    if not found:
        raise GithubError(f"{pipeline} has no finished {branch} build")
    return found[0]


def job_build(job: str) -> tuple[str, str]:
    """The pipeline, as <org>/<slug>, and number of the Buildkite build a command job belongs to, by one GraphQL read of the job's UUID, the only key `bk job retry` gives."""
    query = f"query JobBuild {{ job(uuid: {json.dumps(job)}) {{ ... on JobTypeCommand {{ build {{ number pipeline {{ slug organization {{ slug }} }} }} }} }} }}"
    try:
        found = (json.loads(run(["bk", "api", "-f", "/dev/stdin"], stdin=query))["data"]["job"] or {}).get("build")
    except GithubError as error:
        raise GithubError(f"could not read the build of Buildkite job {job}: {error}") from None
    if not found:
        raise GithubError(f"Buildkite holds no command job {job}")
    return f"{found['pipeline']['organization']['slug']}/{found['pipeline']['slug']}", str(found["number"])


def run_conclusion(run_id: str | None, job: str | None, repo: str | None, cwd: str) -> str:
    """A GitHub Actions run's conclusion as `gh run view --json` reads it, or with job that job's, its status while it has none, such as in_progress; with no repo, gh reads the repository of the checkout at cwd."""
    named = f"job {job}" if job else f"run {run_id}"
    cmd = ["gh", "run", "view", *([run_id] if run_id else []), *(["--job", job] if job else []), *(["-R", repo] if repo else []), "--json", "conclusion,status,jobs"]
    try:
        read = json.loads(run(cmd, cwd=cwd))
    except GithubError as error:
        raise GithubError(f"could not read GitHub Actions {named} from GitHub: {error}") from None
    ran = next((each for each in read["jobs"] if str(each["databaseId"]) == job), None) if job else read
    if ran is None:
        raise GithubError(f"GitHub Actions holds no job {job}")
    return ran["conclusion"] or ran["status"]


def fetch(keys: list[str], into: dict[str, str] | None = None) -> tuple[dict[str, dict], GithubError | None]:
    """Read every PR key ("owner/name#123"), its native GitHub stack, its repository's default branch, the checks the rules of that branch require, the method it merges by and what the repository lets this account do included, PRS_PER_QUERY of one repository to a `gh api graphql` call, since GitHub refuses a query past its resource limits, an open one on a base no open PR heads with base_ahead, by ahead(), each with base_pr, the read of the PR whose head its base was: for a key into names, the PR the drive holds it folded into, read beside it; for any other GitHub reads merged into a base that is no branch its repository lands PRs on, by landing_base(), the one bases() finds. It gives the reads of every key whose reads all answered, and an error joining what the others could not read, so one refused call loses only its own PRs and those folded into them."""
    into = into or {}
    asked = list(dict.fromkeys([*keys, *(into[key] for key in keys if key in into)]))
    found, refused = {}, []
    for chunk in by_repository(asked, PRS_PER_QUERY):
        try:
            found |= parse(graphql(query(chunk, READ, RULES), f"{len(chunk)} PRs of {repo_of(chunk[0])}"), chunk)
        except GithubError as error:
            refused.append(str(error))
    for chunk in by_repository([key for key, read in found.items() if unheaded(read)], PRS_PER_QUERY):
        try:
            ahead([(key, found[key]) for key in chunk])
        except GithubError as error:
            refused.append(str(error))
            for key in chunk:
                del found[key]
    for chunk in by_repository([key for key, read in found.items() if key not in into and merged_off_landing_base(read)], PRS_PER_QUERY):
        try:
            bases([(key, found[key]) for key in chunk])
        except GithubError as error:
            refused.append(str(error))
            for key in chunk:
                del found[key]
    while unread := [key for key in found if key in into and into[key] not in found]:
        for key in unread:
            del found[key]
    for key, read in found.items():
        if key in into:
            read["base_pr"] = found[into[key]]
    return {key: found[key] for key in keys if key in found}, GithubError("; ".join(refused)) if refused else None


def mergeability(keys: list[str]) -> tuple[dict[str, dict], GithubError | None]:
    """Each PR's state, head and mergeability, MERGEABLE, CONFLICTING or UNKNOWN while GitHub works it out again, MERGEABILITY_PER_QUERY PRs of one repository to a `gh api graphql` call: the read a push to a base owes the PRs on it, since it moves none of their heads, checks or reviews, only what GitHub merges each into. It gives the reads of every call that answered and an error joining what the others could not read, as fetch does."""
    found, refused = {}, []
    for chunk in by_repository(keys, MERGEABILITY_PER_QUERY):
        try:
            found |= nodes(graphql(query(chunk, MERGEABILITY), f"the mergeability of {len(chunk)} PRs of {repo_of(chunk[0])}"), chunk)
        except GithubError as error:
            refused.append(str(error))
    return found, GithubError("; ".join(refused)) if refused else None


def paths(keys: list[str]) -> dict[str, tuple[str, list[str]]]:
    """Each PR's head and the path of each file it changes, PRS_PER_QUERY PRs of one repository to a `gh api graphql` call, the files of one changing more than the 100 a query lists read over REST, by files(); a PR GitHub answered none for left out."""
    found = {}
    for chunk in by_repository(keys, PRS_PER_QUERY):
        for key, node in nodes(graphql(query(chunk, PATHS), f"the changed files of {len(chunk)} PRs of {repo_of(chunk[0])}"), chunk).items():
            listed = [changed["path"] for changed in node["files"]["nodes"]]
            found[key] = (node["headRefOid"], listed if node["changedFiles"] <= len(listed) else files(key))
    return found


def unheaded(read: dict) -> bool:
    return read["state"] == "OPEN" and not landing_base(read["baseRefName"], read["default"], read["landing_bases"]) and not read["under"]


def ahead(reads: list[tuple[str, dict]]) -> None:
    """Set base_ahead on each (key, read) of an open PR of one repository whose base is no branch its repository lands PRs on, by landing_base(), and that no open PR heads: how many commits that base holds that the default branch lacks, as GitHub's compare of the two reads it, which a restack onto the default branch would drop, so its builder opens that base's PR instead; None once the base branch is gone. One `gh api graphql` call."""
    refs = " ".join(f"b{slot}: ref(qualifiedName: {json.dumps('refs/heads/' + read['baseRefName'])}) {{ compare(headRef: {json.dumps(read['default'])}) {{ behindBy }} }}" for slot, (_, read) in enumerate(reads))
    (repo,) = {repo_of(key) for key, _ in reads}
    repository = answered(graphql(repository_query(repo, refs), f"the commits the bases of {', '.join(key for key, _ in reads)} hold that their default branch lacks"))
    for slot, (_, read) in enumerate(reads):
        read["base_ahead"] = ref["compare"]["behindBy"] if (ref := repository.get(f"b{slot}")) else None


def merged_off_landing_base(read: dict) -> bool:
    return read["state"] == "MERGED" and not landing_base(read["baseRefName"], read["default"], read["landing_bases"])


def bases(folds: list[tuple[str, dict]]) -> None:
    """Set base_pr on each (key, read) of a PR of one repository GitHub reads merged into a base that is no branch its repository lands PRs on, by landing_base(): None while that default branch holds its merge commit, as it does for each PR of a native GitHub stack its parent landed with; else the same-repository PR whose head is that base and was open as it merged, its own base_pr found the same way in turn when it merged into another, one `gh api graphql` call a round."""
    while folds:
        repository = answered(graphql(bases_query(folds), f"the PRs whose heads are the bases of {', '.join(key for key, _ in folds)}"))
        onward = []
        for slot, (key, read) in enumerate(folds):
            if ((repository.get("defaultBranchRef") or {}).get(f"c{slot}") or {}).get("status") in ON_DEFAULT:
                continue
            pulls = (repository.get(f"o{slot}") or {"nodes": []})["nodes"]
            if not (pull := next((pull for pull in pulls if open_at(pull, read["mergedAt"])), None)):
                if len(pulls) == OWNERS_PER_BASE:
                    raise GithubError(f"{key}: {OWNERS_PER_BASE} PRs have {read['baseRefName']} as their head and none of them was open as it merged, so GitHub's first page cannot say which PR it merged into")
                continue
            read["base_pr"] = parent = {
                "key": f"{repo_of(key)}#{pull['number']}",
                "state": pull["state"],
                "baseRefName": pull["baseRefName"],
                "mergeCommit": (pull["mergeCommit"] or {}).get("oid"),
                "mergedAt": pull["mergedAt"],
                "default": read["default"],
                "landing_bases": read["landing_bases"],
                "base_pr": None,
            }
            if merged_off_landing_base(parent):
                onward.append((parent["key"], parent))
        folds = onward


def open_at(pull: dict, moment: str) -> bool:
    """Whether a PR of the same repository was open at moment, a GitHub timestamp, as the PR whose head a PR merging then had as its base."""
    return not pull["isCrossRepository"] and pull["createdAt"] <= moment and (pull["closedAt"] is None or pull["closedAt"] > moment)


def bases_query(folds: list[tuple[str, dict]]) -> str:
    """One GraphQL query for every (key, read) of folds, all of one repository: how its default branch compares to each merge commit, and the PRs whose head is each base, newest first, aliased r0 and c<j> and o<j> per PR."""
    compares = " ".join(f"c{slot}: compare(headRef: {json.dumps(read['mergeCommit'])}) {{ status }}" for slot, (_, read) in enumerate(folds))
    pulls = " ".join(
        f"o{slot}: pullRequests(headRefName: {json.dumps(read['baseRefName'])}, first: {OWNERS_PER_BASE}, orderBy: {{field: CREATED_AT, direction: DESC}}) {{ nodes {{ {OWNER} }} }}" for slot, (_, read) in enumerate(folds)
    )
    (repo,) = {repo_of(key) for key, _ in folds}
    return repository_query(repo, f"defaultBranchRef {{ {compares} }} {pulls}")


def heads_query(repo: str, branches: list[str]) -> str:
    """One GraphQL query for the open PRs of every branch of one repository, aliased r0 and h<j> per branch."""
    return repository_query(repo, " ".join(f"h{slot}: pullRequests(headRefName: {json.dumps(branch)}, states: OPEN, first: 10) {{ nodes {{ number {FIELDS.replace(f' {REQUIRED}', '')} }} }}" for slot, branch in enumerate(branches)))


def open_on(heads: list[tuple[str, str]]) -> dict[str, dict]:
    """Every open PR whose head is one of the (owner/name, branch) heads, keyed "owner/name#123" with its branch, checks and review, one `gh api graphql` call a repository, however many of its branches there are."""
    found = {}
    for repo, named in groupby(sorted(heads), key=lambda head: head[0]):
        branches = [branch for _, branch in named]
        repository = answered(graphql(heads_query(repo, branches), f"the open PRs on running jobs' branches in {repo}"))
        for slot, branch in enumerate(branches):
            for node in (repository.get(f"h{slot}") or {}).get("nodes", []):
                pulled = flatten(node)
                found[f"{repo}#{node['number']}"] = {
                    "branch": branch,
                    "checks": checks(pulled["checks"], []),
                    "review": review(pulled),
                }
    return found


def branch_heads(heads: list[tuple[str, str]]) -> dict[tuple[str, str], str | None]:
    """The commit each (owner/name, branch) head points at on GitHub, None for a branch its repository does not hold, one `gh api graphql` call a repository, however many of its branches there are."""
    found = {}
    for repo, named in groupby(sorted(heads), key=lambda head: head[0]):
        branches = [branch for _, branch in named]
        refs = " ".join(f"h{slot}: ref(qualifiedName: {json.dumps(f'refs/heads/{branch}')}) {{ target {{ oid }} }}" for slot, branch in enumerate(branches))
        repository = answered(graphql(repository_query(repo, refs), f"the heads of built branches in {repo}"))
        found |= {(repo, branch): ((repository.get(f"h{slot}") or {}).get("target") or {}).get("oid") for slot, branch in enumerate(branches)}
    return found


def repo_name(remote: str) -> str:
    owner, name = re.split(r"[/:]", remote.strip().removesuffix("/").removesuffix(".git"))[-2:]
    return f"{owner}/{name}"


def checkout_repo(checkout: str) -> str:
    """The owner/name its origin remote names, of the checkout a drive's remote lanes clone."""
    return repo_name(run(["git", "-C", checkout, "remote", "get-url", "origin"]))


def saved_branches(repo: str, lane: str, every: bool = False) -> list[str]:
    """Each branch repo holds on GitHub under saved/<lane>/, or with every under saved/<lane>-<n>/ of each later attempt of that lane too, as `saved/<lane>/<name> at <its short sha>`, by one matching-refs read."""
    prefix = f"saved/{lane}" if every else f"saved/{lane}/"
    refs = json.loads(read(f"{repo}'s {prefix} branches", ["gh", "api", f"repos/{repo}/git/matching-refs/heads/{prefix}"]))
    named = [(ref["ref"].removeprefix("refs/heads/"), ref["object"]["sha"][:10]) for ref in refs]
    return [f"{branch} at {sha}" for branch, sha in named if re.match(rf"saved/{re.escape(lane)}(-\d+)?/", branch)]


def fetched(clone: str, branch: str) -> str:
    """The sha of branch's head as origin holds it now, by head, fetched by that sha into the clone's objects by fetch_commit, so it needs no lock of its own."""
    fetch_commit(clone, sha := head(clone, branch))
    return sha


def head(clone: str, branch: str) -> str:
    """The sha of branch's head as origin holds it now, read with ls-remote, which fetches nothing."""
    return run(["git", "-C", clone, "ls-remote", "origin", f"refs/heads/{branch}"]).split()[0]


def carrier(clone: str) -> Callable[[str, str, int], bool]:
    """Whether a build carries a squash merge of PR number, read from the clone's own history at its HEAD, its default branch's head, by go/ci's rule: the build sits at or after the merge on the first-parent line, or it is a hotfix whose fork from that line sits at or after it or whose own commits hold one whose subject ends `(#<number>)`, the subject a cherry-pick of it carries. A merge commit may be abbreviated, matched to the line's commit it starts. One rev-list orders the line; each build off it costs one merge-base and one log, a build the clone lacks fetched by its sha, by fetch_commit."""
    git = ["git", "-C", clone]
    order = {sha: at for at, sha in enumerate(reversed(run([*git, "rev-list", "--first-parent", "HEAD"]).split()))}

    @functools.cache
    def held(build: str) -> tuple[int, set[str]]:
        if build in order:
            return order[build], set()
        fetch_commit(clone, build)
        fork = run([*git, "merge-base", build, "HEAD"]).strip()
        return order[fork], {found[1] for subject in run([*git, "log", "--format=%s", build, "--not", "HEAD"]).splitlines() if (found := PICK.search(subject))}

    @functools.cache
    def whole(merge: str) -> str:
        return merge if merge in order else next((sha for sha in order if sha.startswith(merge)), merge)

    def carries(build: str, merge: str, number: int) -> bool:
        at, picks = held(build)
        return order.get(whole(merge), at + 1) <= at or str(number) in picks

    return carries


def fetch_commit(clone: str, sha: str) -> None:
    """Fetch a commit the clone lacks by its sha, writing no ref, so it never loses refs/remotes/origin/<branch>'s ref lock to another fetch of the shared clone, the lander's, a launch's or a session's. The read of whether the clone holds it fetches nothing, as a lazy fetch of a partial clone would, and the fetch starts no git maintenance: its detached worktree-prune task deletes a .git/worktrees entry holding neither gitdir nor locked, as the worktree add a caller runs next holds its own for a moment, and that add then fails writing its locked file."""
    if subprocess.run(["git", "--no-lazy-fetch", "-C", clone, "cat-file", "-e", f"{sha}^{{commit}}"], capture_output=True, check=False).returncode:
        run(["git", "-C", clone, "fetch", "-q", "--no-tags", "--no-write-fetch-head", "--no-auto-maintenance", "origin", sha])


def uncarried(clone: str, build: str, head: str, base: str) -> list[str]:
    """Each file a PR at head changes since its fork from base, its merge-base with base's head as origin holds it now, by fetched, that a build's commit does not carry: its blob there differs from head's and the lines the PR removes and adds in it are not all among those the build's own commits since that fork remove and add in it, by changed_lines, so a shared cut on an older line of the default branch that cherry-picks, squashes or replays the PR on other context carries it as one built on its branch does, whatever its patch ids, and a file no build ships, by shipped, may differ, as when a cut keeps another member's AGENTS.md or the PR's head moved on only in its tests; each commit the clone lacks fetched first, by fetch_commit."""
    for sha in (build, head):
        fetch_commit(clone, sha)
    git = ["git", "-C", clone, "--literal-pathspecs", "-c", "core.quotePath=false"]
    fork = run([*git, "merge-base", head, fetched(clone, base)]).strip()
    if not (wanted := changed_lines(run([*git, "diff", *DIFF, fork, head]))):
        return []
    built = changed_lines(run([*git, "log", "-p", "--no-merges", "--format=", *DIFF, build, f"^{fork}", "--", *wanted]))
    build_tree, head_tree = ({path: entry for entry, path in (row.split("\t", 1) for row in run([*git, "ls-tree", "-r", "-z", commit, "--", *wanted]).split("\0") if row)} for commit in (build, head))
    missing = [path for path, lines in wanted.items() if build_tree.get(path) != head_tree.get(path) and not (lines and Counter(lines) <= Counter(built.get(path, [])))]
    return shipped(clone, build, missing)


def changed_lines(diff: str) -> dict[str, list[str]]:
    """The lines a git diff, or each commit a git log shows, removes and adds in each file, joined in order, read only past each file's first hunk header, where a removed line starting `--` no longer reads as its header; a file it changes no line of, as a binary or mode change, maps to none."""
    lines: dict[str, list[str]] = {}
    for section in re.split(r"^diff --git ", diff, flags=re.MULTILINE)[1:]:
        named, _, rest = section.partition("\n")
        lines.setdefault(named[2 : (len(named) - 1) // 2], []).extend(line for line in rest.partition("\n@@")[2].splitlines()[1:] if line[:1] in "+-")
    return lines


def shipped(clone: str, build: str, paths: list[str]) -> list[str]:
    """The files of paths a build ships: each but an AGENTS.md, a CLAUDE.md, a Go _test.go or a file under a tests or testdata folder, which ships only when the build embeds it, by embeds, read with one git grep at the build."""
    if not (unshipped := {path for path in paths if UNSHIPPED.search(path)}):
        return paths
    grep = subprocess.run(["git", "-C", clone, "grep", "-I", "-z", "-E", EMBEDS, build, "--", *EMBEDDING], capture_output=True, text=True, check=False)
    if grep.returncode > 1:
        raise GithubError(f"git grep for what {build[:12]} embeds failed: {grep.stderr.strip()}")
    found = [(named.split(":", 1)[1], text) for named, text in (row.split("\0", 1) for row in grep.stdout.split("\n") if row)]
    return [path for path in paths if path not in unshipped or any(embeds(source, text, path) for source, text in found)]


def embeds(source: str, text: str, path: str) -> bool:
    """Whether a line of a source file builds path into what it ships: a //go:embed pattern of a Go file in a folder above path matching it or a folder above it, as Go embeds a folder whole, or a quoted path or glob of a Rust include_str! or include_bytes! or a JavaScript or TypeScript import, require or import.meta.glob, read from the file's folder, or, for one that is not relative, as the end of path, as a bundler alias or a Rust concat! of the crate's folder names it."""
    folder = posixpath.dirname(source)
    if source.endswith(".go"):
        _, embed, listed = text.partition("//go:embed")
        names = posixpath.relpath(path, folder or ".").split("/")
        patterns = [next(filter(None, found), "").removeprefix("all:").split("/") for found in re.findall(r'"([^"]*)"|`([^`]*)`|(\S+)', listed)]
        return bool(embed) and (not folder or path.startswith(f"{folder}/")) and any(len(parts) <= len(names) and all(map(fnmatchcase, names, parts)) for parts in patterns)
    literals = [literal for found in QUOTED.findall(text) if (literal := next(filter(None, found), "").partition("?")[0])]
    return any(
        fnmatchcase(path, posixpath.normpath(posixpath.join(folder, literal)) if literal.startswith(".") or source.endswith(".rs") and not literal.startswith("/") else f"*{literal.lstrip('/')}")
        for literal in literals
    )
