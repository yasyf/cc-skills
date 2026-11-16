import functools
import hashlib
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
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing
from fnmatch import fnmatchcase
from itertools import groupby, takewhile
from pathlib import Path

NUMBER = "<number>"
REQUIRED = f"isRequired(pullRequestNumber: {NUMBER})"
FIELDS = (
    "state isDraft headRefOid baseRefName headRefName title lastEditedAt author { __typename login } reviewDecision latestOpinionatedReviews(first: 20, writersOnly: true) { nodes { state commit { oid } } } reviews(last: 50) { nodes { state author { login } commit { oid } } } "
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
HOLD_LABEL = "hold"
EJECTED_LABEL = "trunk-failed"
CANCELLED_LABEL = "trunk-cancelled"
TRUNK_CHECK = "Trunk Merge Queue"
JOB_READS = "job-reads"
JOB_READ_DAYS = 2
RUN_URL = re.compile(r"/actions/runs/(?P<run>\d+)/")
JOB_URL = re.compile(r"/actions/runs/\d+/job/(?P<job>\d+)")
FAILED_TEST = re.compile(r"^\S+ (?:FAILED|ERROR) (?P<pytest>\S+\.py(?:::[^\r\n]+?)?)(?: - [^\r\n]*)?\r?$|--- FAIL: (?P<go>[^\s\"\\]+)", re.MULTILINE)
PRS_PER_QUERY = 25
MERGEABILITY = "state headRefOid mergeable"
MERGEABILITY_PER_QUERY = 100
QUERIES_AT_ONCE = 4
PATHS = "headRefOid baseRefOid changedFiles files(first: 100) { nodes { path } pageInfo { hasNextPage endCursor } }"
STACKS_AGE = "60s"
STACK = "stack { baseRefName entries(first: 100) { nodes { position pullRequest { number state } } } }"
UNDER = "baseRef { associatedPullRequests(states: OPEN, first: 2) { nodes { number } } }"
SIGNATURE = "updatedAt headRefOid mergeable signatureChecks: commits(last: 1) { nodes { commit { statusCheckRollup { state contexts(first: 0) { checkRunCountsByState { state count } statusContextCountsByState { state count } } } } } }"
READ = f"{FIELDS} {UNDER} {STACK} {SIGNATURE}"
DELETED_AUTHOR = "ghost"
BOT_PREFIX = "app/"
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


class MissingBranch(GithubError):
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


def read(what: str, cmd: list[str], max_age: str = "0s", text: bool = True, timeout: int = 120) -> str | bytes:
    """Run a gh read; one that fails names what it could not read."""
    try:
        return run(cmd, timeout=timeout, max_age=max_age, text=text)
    except GithubError as error:
        raise GithubError(f"could not read {what} from GitHub: {error}") from None


def close(url: str, comment: str | None = None) -> None:
    """Close a pull request on GitHub, leaving the comment on it when one is given."""
    run(["gh", "pr", "close", url, *(["--comment", comment] if comment else [])])


def author_login(author: dict | None) -> str:
    """A PR author's login as `gh pr view` names it: `app/<login>` for a GitHub App's bot, such as ai-oncall's, and the deleted account's for none."""
    if author is None:
        return DELETED_AUTHOR
    return f"{BOT_PREFIX}{author['login']}" if author.get("__typename") == "Bot" else author["login"]


def files(key: str) -> list[str]:
    """Every changed path from a fresh PR snapshot, reusing complete large lists at matching base and head commits."""
    if key not in (read := paths([key])):
        raise GithubError(f"could not read the changed files of {key} from GitHub")
    return read[key][1]


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


def at_once(chunks: list[list[str]], read: Callable[[list[str]], object]) -> list[tuple[list[str], object]]:
    """Each chunk with what read gave for it, or the GithubError it raised, QUERIES_AT_ONCE chunks read side by side, so a pass waits on its slowest query rather than on every query in turn."""

    def one(chunk: list[str]) -> object:
        try:
            return read(chunk)
        except GithubError as error:
            return error

    with ThreadPoolExecutor(QUERIES_AT_ONCE) as pool:
        return list(zip(chunks, pool.map(one, chunks)))


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
        "author": author_login(node["author"]),
        "labels": labels,
        "ejected_at": labeled(node, labels, EJECTED_LABEL),
        "cancelled_at": labeled(node, labels, CANCELLED_LABEL),
    }


def labeled(node: dict, labels: list[str], label: str) -> str | None:
    return max((event["createdAt"] for event in node["timelineItems"]["nodes"] if event["label"]["name"] == label), default=None) if label in labels else None


def review(read: dict) -> str:
    """approved once every reviewer's latest approval is on the PR's head, the only approval the repository's merge queue counts; stale while GitHub's reviewDecision still counts one given on an older head; changes or none otherwise."""
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
    return finished_job_read(repo, job, "log", "/logs")


def finished_job_read(repo: str, job: int, what: str, path: str) -> str:
    """A read of a finished GitHub Actions job, its record or its log, kept by kept, since a finished job never changes."""
    return kept(repo, f"{job}.{what}", lambda: str(read(f"{repo}'s job {job} {what}", ["gh", "api", f"repos/{repo}/actions/jobs/{job}{path}"])))


def kept(repo: str, name: str, answer: Callable[[], str]) -> str:
    """What answer gives for a GitHub read that never changes once made, such as a finished job's log or the tests a finished run failed, made once and kept under the drive's state root for JOB_READ_DAYS, since every watch pass judging a red check re-read it on the owner's core quota; kept reads older than that go as a new one is kept."""
    from drive import state

    path = state.root() / JOB_READS / repo.replace("/", "__") / name
    if path.exists():
        return path.read_text()
    text = answer()
    path.parent.mkdir(parents=True, exist_ok=True)
    for old in path.parent.parent.glob("*/*"):
        if time.time() - old.stat().st_mtime > JOB_READ_DAYS * 86400:
            old.unlink(missing_ok=True)
    path.write_text(text)
    return text


def failed_at_checkout(repo: str, job: int) -> bool:
    """Whether a GitHub Actions job failed on its run's first attempt at a checkout, before any step could run what the checkout fetches: the first of its steps that failed names a checkout, as actions/checkout's unnamed step reads "Run actions/checkout@<sha>"; an ended job's record never changes, so a read of it within the hour answers."""
    ran = json.loads(finished_job_read(repo, job, "record", ""))
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


def cached_nodes(keys: list[str], fields: str, max_age: int, read: Callable[[list[str]], dict[str, dict]], accept: Callable[[str, dict], bool] | None = None) -> dict[str, dict]:
    """Reuse each PR's fields within max_age seconds across overlapping batches, coalescing concurrent misses without caching absent or refused reads."""
    from drive import state

    if not keys:
        return {}
    folder = state.root()
    folder.mkdir(parents=True, exist_ok=True)
    with closing(state.connect(folder / "pr-nodes.sqlite3")) as db, ExitStack() as locks:
        db.execute("CREATE TABLE IF NOT EXISTS nodes (fields TEXT NOT NULL, key TEXT NOT NULL, read_at REAL NOT NULL, body TEXT NOT NULL, PRIMARY KEY (fields, key)) WITHOUT ROWID")
        def reusable(keys: list[str]) -> dict[str, dict]:
            found = {}
            for key in keys:
                row = db.execute("SELECT read_at, body FROM nodes WHERE fields = ? AND key = ?", (fields, key.casefold())).fetchone()
                if row and max_age and time.time() - row[0] < max_age:
                    node = json.loads(row[1])
                    if accept is None or accept(key, node):
                        found[key] = node
            return found

        found = reusable(keys)
        missing = [key for key in keys if key not in found]
        for key in sorted({key.casefold() for key in missing}):
            locks.enter_context(state.flocked(folder / "pr-node-locks" / hashlib.sha256((fields + key).encode()).hexdigest()))
        found |= reusable(missing)
        if unread := [key for key in missing if key not in found]:
            started = time.time()
            answered = read(unread)
            with db:
                db.execute("BEGIN")
                db.executemany("INSERT OR REPLACE INTO nodes VALUES (?, ?, ?, ?)", [(fields, key.casefold(), started, json.dumps(node)) for key, node in answered.items()])
            found |= answered
    return found


def parse(payload: dict, keys: list[str]) -> dict[str, dict]:
    repository = answered(payload)
    return {
        key: flatten(node)
        | {
            "key": key,
            "signature": signature(node),
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
    """The open PRs the repository's merge queue queues under key, bottom first: those below it in its native GitHub stack and those under the lowest of them, or, at the bottom of a native stack or in none, on a base that is no branch its repository lands PRs on, by landing_base(), the one open PR whose head is that base and those under it."""
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
    """Each PR's native GitHub stack, reusing its own snapshot up to STACKS_AGE old across card batches; a PR in no native stack is left out."""
    found = {}
    for chunk in by_repository(keys, PRS_PER_QUERY):
        for key, node in cached_nodes(chunk, STACK, int(STACKS_AGE.removesuffix("s")), lambda unread: nodes(graphql(query(unread, STACK), f"the native stacks of {', '.join(unread)}"), unread)).items():
            if node["stack"]:
                found[key] = entries(repo_of(key), node["stack"])
    return found


def merge_commit(key: str) -> str:
    """The merge commit of a PR named owner/repo#N, empty while it is not merged."""
    repo, number = key.split("#")
    return read(f"{key}'s merge commit", ["gh", "pr", "view", number, "-R", repo, "--json", "mergeCommit", "-q", '.mergeCommit.oid // ""']).strip()


def holds(repo: str, ref: str, commit: str) -> bool:
    """Whether the history of ref, a commit or tag on repo, holds commit, as GitHub's compare of the two reads it, a commit holding itself with no read."""
    return ref == commit or read(f"whether {repo}'s {ref} holds {commit[:7]}", ["gh", "api", f"repos/{repo}/compare/{commit}...{ref}", "--jq", ".status"], max_age="1h").strip() in ("ahead", "identical")


def build(pipeline: str, number: str) -> dict:
    """A Buildkite build as `bk build view` reads it now: its state, its jobs and the environment it started with, which a repository's providers read to tell what the build runs."""
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
    for chunk, read in at_once(by_repository(asked, PRS_PER_QUERY), lambda chunk: parse(graphql(query(chunk, READ, RULES), f"{len(chunk)} PRs of {repo_of(chunk[0])}"), chunk)):
        if isinstance(read, GithubError):
            refused.append(str(read))
        else:
            found |= read
    for fill, keys_of in ((ahead, lambda: [key for key, read in found.items() if unheaded(read)]), (bases, lambda: [key for key, read in found.items() if key not in into and merged_off_landing_base(read)])):
        for chunk, failed in at_once(by_repository(keys_of(), PRS_PER_QUERY), lambda chunk, fill=fill: fill([(key, found[key]) for key in chunk])):
            if isinstance(failed, GithubError):
                refused.append(str(failed))
                for key in chunk:
                    del found[key]
    while unread := [key for key in found if key in into and into[key] not in found]:
        for key in unread:
            del found[key]
    for key, read in found.items():
        if key in into:
            read["base_pr"] = found[into[key]]
    return {key: found[key] for key in keys if key in found}, GithubError("; ".join(refused)) if refused else None


def signature(node: dict) -> str:
    """What a PR's read must differ in for a fresh read to say anything new: GitHub's updatedAt, its head, its mergeability and how many of its head's checks sit in each state, which a check finishing moves though it leaves updatedAt alone."""
    return json.dumps([node["updatedAt"], node["headRefOid"], node["mergeable"], node["signatureChecks"]], sort_keys=True)


def signatures(keys: list[str]) -> tuple[dict[str, str], GithubError | None]:
    """Each PR's signature, MERGEABILITY_PER_QUERY PRs of one repository to a `gh api graphql` call, QUERIES_AT_ONCE at a time: the light read a watch that starts again makes in place of reading every PR it holds whole. It gives what answered and an error joining what did not, as fetch does."""
    found, refused = {}, []
    for chunk, read in at_once(by_repository(keys, MERGEABILITY_PER_QUERY), lambda chunk: nodes(graphql(query(chunk, SIGNATURE), f"the signatures of {len(chunk)} PRs of {repo_of(chunk[0])}"), chunk)):
        if isinstance(read, GithubError):
            refused.append(str(read))
        else:
            found |= {key: signature(node) for key, node in read.items()}
    return found, GithubError("; ".join(refused)) if refused else None


def mergeability(keys: list[str]) -> tuple[dict[str, dict], GithubError | None]:
    """Each PR's state, head and mergeability, MERGEABLE, CONFLICTING or UNKNOWN while GitHub works it out again, MERGEABILITY_PER_QUERY PRs of one repository to a `gh api graphql` call: the read a push to a base owes the PRs on it, since it moves none of their heads, checks or reviews, only what GitHub merges each into. It gives the reads of every call that answered and an error joining what the others could not read, as fetch does."""
    found, refused = {}, []
    for chunk, read in at_once(by_repository(keys, MERGEABILITY_PER_QUERY), lambda chunk: nodes(graphql(query(chunk, MERGEABILITY), f"the mergeability of {len(chunk)} PRs of {repo_of(chunk[0])}"), chunk)):
        if isinstance(read, GithubError):
            refused.append(str(read))
        else:
            found |= read
    return found, GithubError("; ".join(refused)) if refused else None


def paths(keys: list[str]) -> dict[str, tuple[str, list[str]]]:
    """Each PR's fresh head and complete changed paths, batched by repository, with large lists retained by base and head commits."""
    found = {}
    for chunk in by_repository(keys, PRS_PER_QUERY):
        for key, node in nodes(graphql(query(chunk, PATHS), f"the changed files of {len(chunk)} PRs of {repo_of(chunk[0])}"), chunk).items():
            found[key] = (node["headRefOid"], complete_paths(key, node))
    return found


def complete_paths(key: str, node: dict) -> list[str]:
    """Complete one changed-file snapshot, sharing large-list pagination across callers without caching partial or moving snapshots."""
    from drive import state

    listed = [changed["path"] for changed in node["files"]["nodes"]]
    if node["changedFiles"] == len(listed):
        return listed
    folder = state.root()
    lock = folder / "pr-path-locks" / hashlib.sha256(key.casefold().encode()).hexdigest()
    base, head = node["baseRefOid"], node["headRefOid"]
    with state.flocked(lock), closing(state.connect(folder / "pr-paths.sqlite3")) as db:
        db.execute("CREATE TABLE IF NOT EXISTS paths (key TEXT PRIMARY KEY, base TEXT NOT NULL, head TEXT NOT NULL, paths TEXT NOT NULL) WITHOUT ROWID")
        was = db.execute("SELECT paths FROM paths WHERE key = ? AND base = ? AND head = ?", (key.casefold(), base, head)).fetchone()
        if was:
            return json.loads(was[0])
        page = node
        while page["files"]["pageInfo"]["hasNextPage"]:
            cursor = json.dumps(page["files"]["pageInfo"]["endCursor"])
            fields = PATHS.replace("files(first: 100)", f"files(first: 100, after: {cursor})")
            page = nodes(graphql(query([key], fields), f"the changed files of {key}"), [key]).get(key)
            if page is None or (page["baseRefOid"], page["headRefOid"]) != (base, head):
                raise GithubError(f"{key}'s changed-file snapshot changed while reading its pages")
            listed.extend(changed["path"] for changed in page["files"]["nodes"])
        if len(listed) != node["changedFiles"]:
            raise GithubError(f"{key}'s changed-file snapshot is incomplete")
        db.execute("INSERT OR REPLACE INTO paths VALUES (?, ?, ?, ?)", (key.casefold(), base, head, json.dumps(listed)))
    return listed


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
    """The sha of branch's head as origin holds it now, read with ls-remote and fetched by that sha into the clone's objects, writing no ref, so it never loses refs/remotes/origin/<branch>'s ref lock to another fetch of the shared clone, the lander's, a launch's or a session's, and needs no lock of its own; a branch origin lacks raises MissingBranch naming it. The fetch starts no git maintenance: its detached worktree-prune task deletes a .git/worktrees entry holding neither gitdir nor locked, as the worktree add each caller runs next holds its own for a moment, and that add then fails writing its locked file."""
    git = ["git", "-C", clone]
    if not (listed := run([*git, "ls-remote", "origin", f"refs/heads/{branch}"]).split()):
        raise MissingBranch(f"origin of {clone} has no branch {branch!r}")
    sha = listed[0]
    run([*git, "fetch", "-q", "--no-tags", "--no-write-fetch-head", "--no-auto-maintenance", "origin", sha])
    return sha


def carrier(clone: str) -> Callable[[str, str, int], bool]:
    """Whether a build carries a squash merge of PR number, read from the clone's own history at its HEAD, its default branch's head, by the release tool's rule: the build sits at or after the merge on the first-parent line, or it is a hotfix whose fork from that line sits at or after it or whose own commits copy the PR's change, a commit whose `git patch-id --stable` matches the merge's; never by a commit's words, since a revert or a follow-up naming the PR carries none of it, and the release tool's hotfix pick writes no cherry-pick trailer to read. A merge commit may be abbreviated, matched to the line's commit it starts. One rev-list orders the line; each build off it costs one merge-base and one log, a build the clone lacks fetched by its sha, by fetch_commit."""
    git = ["git", "-C", clone]
    order = {sha: at for at, sha in enumerate(reversed(run([*git, "rev-list", "--first-parent", "HEAD"]).split()))}

    @functools.cache
    def held(build: str) -> tuple[int, set[str]]:
        if build in order:
            return order[build], set()
        fetch_commit(clone, build)
        fork = run([*git, "merge-base", build, "HEAD"]).strip()
        return order[fork], patch_ids(run([*git, "log", "-p", "--no-merges", build, "--not", "HEAD"]))

    @functools.cache
    def whole(merge: str) -> str:
        return merge if merge in order else next((sha for sha in order if sha.startswith(merge)), merge)

    @functools.cache
    def patch_of(merge: str) -> set[str]:
        return patch_ids(run([*git, "show", whole(merge)]))

    def patch_ids(diff: str) -> set[str]:
        return {line.split()[0] for line in run(["git", "-C", clone, "patch-id", "--stable"], stdin=diff).splitlines() if line.strip()}

    def carries(build: str, merge: str, number: int) -> bool:
        at, patches = held(build)
        return order.get(whole(merge), at + 1) <= at or bool(patch_of(merge) & patches)

    return carries


def contains(clone: str) -> Callable[[str, str, str, str | None], bool]:
    """Whether a build's tree holds a PR's change by its net content: the merge sits in the build's history, else, for a build off the line such as a hotfix, every hunk of the PR's change since its fork from base reads in what the build serves, by net_held, so a copy on other context counts and a copy later reverted does not; with no base to read the fork from, or a base branch origin no longer holds, only the merge counts."""
    git = ["git", "-C", clone]

    @functools.cache
    def holds(build: str, merge: str, head: str, base: str | None) -> bool:
        for sha in (build, merge):
            fetch_commit(clone, sha)
        if not subprocess.run([*git, "merge-base", "--is-ancestor", merge, build], capture_output=True, check=False).returncode:
            return True
        try:
            return bool(base) and net_held(clone, build, head, base)
        except MissingBranch:
            return False

    return holds


def net_held(clone: str, build: str, head: str, base: str) -> bool:
    """Whether each hunk a PR at head changes since its fork from base reads in a build's files: its added lines stand there together, and its removed lines, unless head keeps them too, do not, whatever lines sit around them."""
    fetch_commit(clone, head)
    git = ["git", "-C", clone, "--literal-pathspecs", "-c", "core.quotePath=false"]
    fork = fork_point(clone, git, head, fetched(clone, base))
    hunks: dict[str, list[tuple[list[str], list[str]]]] = {}
    path = was = None
    for line in run([*git, "diff", *DIFF, fork, head]).splitlines():
        if line.startswith("diff --git "):
            path = was = None
        elif line.startswith("--- ") and path is None:
            was = None if line == "--- /dev/null" else line[6:]
        elif line.startswith("+++ ") and path is None:
            path = was if line == "+++ /dev/null" else line[6:]
        elif line.startswith("@@") and path:
            hunks.setdefault(path, []).append(([], []))
        elif path and hunks.get(path) and line[:1] in ("+", "-"):
            hunks[path][-1][0 if line[0] == "+" else 1].append(line[1:])

    def blob(commit: str, name: str) -> str:
        shown = subprocess.run([*git, "show", f"{commit}:{name}"], capture_output=True, text=True, check=False)
        return f"\n{shown.stdout}\n" if not shown.returncode else "\n"

    for name, changes in hunks.items():
        built, kept = blob(build, name), blob(head, name)
        for added, removed in changes:
            if added and "\n" + "\n".join(added) + "\n" not in built:
                return False
            if removed and (gone := "\n" + "\n".join(removed) + "\n") in built and gone not in kept:
                return False
    return True


def fetch_commit(clone: str, sha: str) -> None:
    """Fetch a commit the clone lacks by its sha, writing no ref, as fetched does."""
    if subprocess.run(["git", "-C", clone, "cat-file", "-e", f"{sha}^{{commit}}"], capture_output=True, check=False).returncode:
        run(["git", "-C", clone, "fetch", "-q", "--no-tags", "--no-write-fetch-head", "origin", sha])


def uncarried(clone: str, build: str, head: str, base: str) -> list[str]:
    """Each file a PR at head changes since its fork from base, its merge-base with base's head as origin holds it now, by fetched, that a build's commit does not carry: its blob there differs from head's and the lines the PR removes and adds in it are not all among those the build's own commits since that fork remove and add in it, by changed_lines, so a shared cut on an older line of the default branch that cherry-picks, squashes or replays the PR on other context carries it as one built on its branch does, whatever its patch ids, and a file no build ships, by shipped, may differ, as when a cut keeps another member's AGENTS.md or the PR's head moved on only in its tests; each commit the clone lacks fetched first, by fetch_commit; a PR with no head or base read raises GithubError naming it."""
    if not head or not base:
        raise GithubError(f"no head or base read for the PR at {head or 'no head'} on {base or 'no base'}")
    for sha in (build, head):
        fetch_commit(clone, sha)
    git = ["git", "-C", clone, "--literal-pathspecs", "-c", "core.quotePath=false"]
    fork = fork_point(clone, git, head, fetched(clone, base))
    if not (wanted := changed_lines(run([*git, "diff", *DIFF, fork, head]))):
        return []
    built = changed_lines(run([*git, "log", "-p", "--no-merges", "--format=", *DIFF, build, f"^{fork}", "--", *wanted]))
    build_tree, head_tree = ({path: entry for entry, path in (row.split("\t", 1) for row in run([*git, "ls-tree", "-r", "-z", commit, "--", *wanted]).split("\0") if row)} for commit in (build, head))
    missing = [path for path, lines in wanted.items() if build_tree.get(path) != head_tree.get(path) and not (lines and Counter(lines) <= Counter(built.get(path, [])))]
    return shipped(clone, build, missing)


CUT_DEPTH = 500


def carries(clone: str, commit: str, number: str, head: str) -> bool:
    """Whether commit, a staging cut's, carries the PR numbered number at head: head is in its history, or one of its newest CUT_DEPTH commits is the commit a packed cut makes of that PR, whose subject names `#<number> at <head's first 12>`, both commits fetched by their exact sha."""
    for sha in (commit, head):
        fetch_commit(clone, sha)
    read = subprocess.run(["git", "-C", clone, "merge-base", "--is-ancestor", head, commit], capture_output=True, text=True, check=False)
    if read.returncode > 1:
        raise GithubError(f"git merge-base --is-ancestor {head[:12]} {commit[:12]} in {clone}: {read.stderr.strip()}")
    if read.returncode == 0:
        return True
    return f"#{number} at {head[:12]}" in run(["git", "-C", clone, "log", "-n", str(CUT_DEPTH), "--format=%s", commit])


def fork_point(clone: str, git: list[str], head: str, tip: str) -> str:
    """The commit head forked from tip, by merge-base; in a shallow clone whose history stops short of it, the fork GitHub's compare names is fetched by its exact sha, never by depth, and merge-base read once more, a second failure naming the shallow clone and that commit."""
    try:
        return run([*git, "merge-base", head, tip]).strip()
    except GithubError:
        if run(["git", "-C", clone, "rev-parse", "--is-shallow-repository"]).strip() != "true":
            raise
    repo = checkout_repo(clone)
    fork = read(f"the fork of {head[:12]} from {tip[:12]}", ["gh", "api", f"repos/{repo}/compare/{tip}...{head}", "--jq", ".merge_base_commit.sha"]).strip()
    fetch_commit(clone, fork)
    try:
        return run([*git, "merge-base", head, tip]).strip()
    except GithubError as error:
        raise GithubError(f"{clone} is a shallow clone, and git merge-base of {head[:12]} and {tip[:12]} failed again after fetching their fork {fork} by its sha: it cannot reach {fork[:12]} ({error})") from None


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
