import json
import os
import re
import signal
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from drive import agents, github, install, served, state, view
from drive.render import cut

ENQUEUE = Path(".agents/skills/submit-pr/scripts/stack-enqueue")
DRIVE = Path(__file__).resolve().parents[2] / "bin" / "drive"
LOG = "landings.log"
LANDINGS = "landing"
CHECKOUT_SECONDS = 600
LANE_HELD_SECONDS = 540
SAID_CHARS = 400
SAID_LINES = 4
QUEUED = "queued for merge"
REFUSED = "merge refused by GitHub"
GITHUB_API = "GITHUB_API_"
BASE_MOVED = "Base branch was modified"
CONFLICTS = ("Pull Request has merge conflicts", "the merge commit cannot be cleanly created")
REBASED = "rebased onto"
CLONES = "clones"
CLONE_URL = "https://github.com/{}.git"
CLAUSE = re.compile(r"(?<=[.;:!?|]) ")
LINE_END = "\x1f"
MERGE_ASYNC = "merge-async"
STACKED = "must be merged using the asynchronous merge REST API"
ASYNC_SECONDS = 2
MERGE = ("gh", "pr", "merge")
# TODO: delete with its case in stage.unlanded once no lander of an install that refused mid-check still runs: its record() reopens on this install with this outcome.
NEWER_INSTALL = "not landed: a newer long-running saved the drive while stack-enqueue --check ran, so the landing runs again on it"
OWN_FAILURE = "the lander could not"
NO_WORKTREE = f"{OWN_FAILURE} check out"
NO_ENQUEUE = f"{OWN_FAILURE} finish stack-enqueue"
ENQUEUE_FAILED = 5
HELD = "not landed while he holds it"
UNREAD = "not landed: a GitHub read failed before it queued or merged anything, so the next watch pass lands it again"
RUNNING = "not landed: its checks still run"
RETRIED = (NEWER_INSTALL, OWN_FAILURE, HELD, UNREAD, RUNNING)
OWN_RED = "not landed: its own change fails"
REVIEW_ASK = "@forge-pr-reviewer review"
BLOCKED_LINE = re.compile(r"^#(?P<number>\d+) BLOCKED (?P<head>\w+) (?P<gaps>.+)$", re.MULTILINE)
OLDER_HEAD = re.compile(r"awaiting approval on head from \S+ \(approved [^,]+, (?:on an older head|dismissed by a push)\)")
REVERT = re.compile(r"^Reverts?\s|⏪|\bThis reverts\b", re.MULTILINE)
STORED_DATA = re.compile(r"(?:^|/)migrations?/|(?:^|/)[^/]*schema[^/]*$|\.(?:sql|proto)$", re.IGNORECASE)
CI_FILES = re.compile(r"^(?:\.github|\.buildkite|(?:go|infra)/ci)/|^tools/ci(?:-ts)?(?:/|$)")
BRANCH_HISTORY = 50
BRANCH_SECONDS = 20


def open_cards(data: dict, job_id: str, pr: dict | None, exempt: str | None = None) -> list[str]:
    """His open cards holding a job's work, its PR when given, from landing, since work does not land with a question of his on it unanswered: each card of an open question holding its work by state.part_cards, the one rule by which a card holds work, so a card a job or PR waits on otherwise only wakes it; none for an immediate fix, which lands while he picks the proper fix, or a flake fix, the PR's exempt class or else `exempt`, which fixes a flaking test the day it flakes by his ruling 532cd3d, and on an open incident, whose repair lands on the recommended option, only a card whose question asks him to lift a ruling of his, which `drive job ask` marks when Jev reads any option it offers as breaking a recorded answer or ruling of his, its recommended option first, or --unsettled names one, by his pick "Hold the PR until you answer; the drive finds each card against your recorded answers"."""
    job = data["jobs"][job_id]
    if state.IMMEDIATE_FIX in job["parts"] or (pr.get("exempt") if pr is not None else exempt) == state.FLAKE_FIX:
        return []
    found = list(dict.fromkeys(state.part_cards(data, job_id, pr)))
    if state.open_incidents(data, [job["order"], *job["carries"]]):
        return [card for card in found if any(question["unsettled"] and question["closed"] is None for _, question in view.drawn_on(data, card))]
    return found


def pr_cards(data: dict, pr: dict) -> list[str]:
    """His open cards holding a PR from landing: those holding the work of any job carrying it, its own and each that holds its landing, by open_cards()."""
    return list(dict.fromkeys(card for job_id in state.pr_jobs(data, pr) for card in open_cards(data, job_id, pr)))


def ship_held(data: dict, job_id: str, exempt: str | None = None) -> list[str]:
    """His open cards holding a job's PR from going up, which the `bash_guard` hook refuses the `ccx vcs ship` that would open it by, on this Mac and through `drive job held` on a Sprite: none once the job has an open PR, which a ship pushes to rather than opens, holding it as open_cards() holds it once registered under the exempt class `exempt` the same call gives it."""
    if any(data["prs"].get(state.pr_key(url), {}).get("state") == "open" for url in data["jobs"][job_id]["prs"]):
        return []
    return open_cards(data, job_id, None, exempt)


def lane_held(*flags: str) -> dict:
    """What `drive job held --json` answers a Sprite lane, which holds no drive: the lane's `drive` asks it of the coordinator's Mac, where the drive's records live, waiting up to LANE_HELD_SECONDS; DriveError when it could not say."""
    try:
        done = subprocess.run(["drive", "job", "held", "--json", *flags], capture_output=True, text=True, timeout=LANE_HELD_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise state.DriveError(str(error)) from error
    if done.returncode:
        raise state.DriveError((done.stderr or done.stdout).strip())
    try:
        return json.loads(done.stdout)
    except ValueError as error:
        raise state.DriveError(f"it printed no JSON: {done.stdout.strip()}") from error


def approved(pr: dict) -> bool:
    return (pr["tap"] or {}).get("label") == "Approve"


def stored(paths: list[str]) -> list[str]:
    """The paths that change how data is stored, a migration, schema or wire format, which no pure port carries."""
    return [path for path in paths if STORED_DATA.search(path)]


def outside(exempt: str, read: dict) -> str:
    """Why a PR, by its changed files, title and body as served.change read them, is not in the exempt class, the one check of an exempt class: the lander's before it lands, `drive pr add`'s and `drive pr set`'s before they keep the class, and a flake fix's tap; "" when it is."""
    paths = [path for path, _ in served.files(read["diff"])]
    match exempt:
        case "pure port":
            found = stored(paths)
            return f"it changes {', '.join(found)}, how its data is stored, which a port models anew for the target tree on his card" if found else ""
        case "test-only" | state.FLAKE_FIX:
            found = [path for path in paths if not served.TESTS.search(path)]
            return f"it changes {', '.join(found)}, outside test and fixture code" if found else ""
        case "ci-only":
            found = [path for path in paths if not CI_FILES.search(path)]
            return f"it changes {', '.join(found)}, outside CI configuration and CI tooling" if found else ""
        case "incident fix":
            return "its title or body says it reverts a change, which lands only on his tap" if REVERT.search(f"{read['title']}\n{read['body']}") else ""


def unexempt(data: dict, key: str) -> str | None:
    """Why a PR landing by its exempt class rather than his Approve is not in that class, by outside() on its changed files, title and body on GitHub now, by his ruling that the landing tool checks the PR really is in the exempt class before it lands; None when it is, or he approved it."""
    pr = data["prs"][key]
    if approved(pr):
        return None
    if not (why := outside(pr["exempt"], served.change(key))):
        return None
    if pr["exempt"] == state.FLAKE_FIX:
        return f'{key} is a "{pr["exempt"]}", which lands with no tap only in test or fixture code, and {why}; it lands on his Approve once drive pr set {state.pr_url(key)} --tap puts it on his card'
    return f'{key} is no "{pr["exempt"]}": {why}; it lands on his Approve once drive pr set {state.pr_url(key)} --no-exempt --tap puts it on his card'


def running(pr: dict) -> bool:
    lander = pr["lander"]
    return bool(lander) and lander["outcome"] is None and state.alive(lander["pid"])


def reported(key: str, pr: dict) -> bool:
    """Whether the lander merges the PR itself with gh pr merge, as it does every PR but a Forge-AI/monorepo one Trunk merges, and says so on its own line, so GitHub's merged line asks no step: it still runs on it, or it merged it."""
    return not state.queued_by_lander(key) and (running(pr) or merged_try(pr))


def merged_try(pr: dict) -> bool:
    """Whether the lander's last try on the PR merged it, by the outcome merged() records, which says so before the watch's next GitHub read of the PR does; a merged PR takes no later head, so the try that merged it stands at whatever head the row reads."""
    return ((pr["lander"] or {}).get("outcome") or "").startswith("merged")


def landing(pr: dict) -> bool:
    """Whether a lander holds the PR on its head: still running on it, done with it queued for merge and not ejected by Trunk since, or done rebasing it onto its base, until the watch reads the head it pushed."""
    lander = pr["lander"] or {}
    outcome = lander.get("outcome") or ""
    return lander.get("head") == pr["head"] and (running(pr) or outcome == QUEUED and not pr["ejected"] or outcome.startswith(REBASED))


def flaked(pr: dict) -> dict | None:
    """What drive pr set --flaky kept at the PR's head: the flaky test outside its diff that failed it there, in its own checks, which failed_checks keeps, or in Trunk's ejection of its green head, which keeps none, and that test's fix, a PR the drive tracks; None at any other head, and once a lander requeued this one."""
    flaky = pr["flaky"]
    return flaky if flaky and flaky["head"] == pr["head"] and not flaky["requeued"] else None


def requeues(data: dict, pr: dict) -> bool:
    """Whether a lander queues the PR again at the green head Trunk ejected it at, with stack-enqueue --flake, as it does once, when its flaky test's fix merged; never one whose own checks failed there, whose builder restacks it onto the fix."""
    return bool(flaky := flaked(pr)) and not flaky["failed_checks"] and data["prs"][flaky["fix"]]["state"] == "merged"


def refused(pr: dict) -> bool:
    """Whether GitHub refused, for the first time at the PR's head, the merge its lander queued there once Trunk's merge queue took it, with no failing check, so the lander lands it again one PR at a time, bottom first; a refusal of that one-at-a-time landing is not landed."""
    lander = pr["lander"] or {}
    return lander.get("head") == pr["head"] and (lander.get("outcome") or "").startswith(REFUSED) and not (lander.get("was") or "").startswith(REFUSED)


def conflicted(pr: dict) -> bool:
    """Whether GitHub refused the lander's merge at the PR's head for a conflict with its base, which only its builder's rebase settles, whatever the watch's last read of its mergeability said."""
    lander = pr["lander"] or {}
    return lander.get("head") == pr["head"] and any(conflict in (lander.get("outcome") or "") for conflict in CONFLICTS)


def own_red(pr: dict) -> bool:
    """Whether the lander's try at the PR's head found a check failing there on its own change, by own_reds(), which only its builder's fix settles."""
    lander = pr["lander"] or {}
    return lander.get("head") == pr["head"] and (lander.get("outcome") or "").startswith(OWN_RED)


def refusals(data: dict, keys: list[str]) -> dict[str, str]:
    """GitHub's refusal of the merge Trunk's merge queue took for the try that queued each of keys, by that try's entry, the PR Trunk names: its newest failure since the try among Trunk's queue records, when GitHub's API gave it, such as GITHUB_API_MERGE_PR_FAILED, with no failing check, which landing the PRs one at a time gets past; a try whose newest failure is any other, or that no record names yet, is left out."""
    tries = {(lander := data["prs"][key]["lander"]).get("entry", key): lander["at"] for key in keys}
    newest: dict[str, dict] = {}
    for record in agents.trunk_failures(list(tries), min(tries.values(), key=state.parse_time)):
        for entry in set(tries) & set(record["refs"].get("prs", [])) - set(newest):
            if state.parse_time(record["at"]) >= state.parse_time(tries[entry]):
                newest[entry] = record["fields"].get("attrs") or {}
    return {
        entry: f"{REFUSED} once Trunk's merge queue took {entry}: {attrs['reason']}, with no failing check"
        for entry, attrs in newest.items()
        if attrs.get("reason", "").startswith(GITHUB_API) and not attrs.get("failing")
    }


def refuse(name: str, data: dict, outcomes: dict[str, str]) -> None:
    """Record GitHub's refusal of each try outcomes names by its entry as that try's outcome on every PR it queued, since Trunk drops the whole stack whose merge GitHub refused, as noted() records a landing."""
    for entry, outcome in outcomes.items():
        tried = [key for key, pr in data["prs"].items() if (pr["lander"] or {}).get("outcome") == QUEUED and pr["lander"].get("entry", key) == entry]
        if entry in tried:
            noted(name, data, [*(key for key in tried if key != entry), entry], outcome, data["prs"][entry]["lander"]["below"])


def unders(data: dict) -> dict[str, str | None]:
    """Each open PR the drive tracks mapped to the open tracked PR it sits on, as the watch last read them and as stack-enqueue joins them: the one before it in its native GitHub stack or, at the bottom of one or in none, the open PR whose head branch is its base; None for a PR on no tracked PR."""
    opened = {key: pr for key, pr in data["prs"].items() if pr["state"] == "open"}
    by_branch = {(github.repo_of(key), pr["branch"]): key for key, pr in opened.items() if pr["branch"]}
    under: dict[str, str | None] = {}
    for key, pr in opened.items():
        native = [member for member in pr["native"] if member in opened]
        under[key] = native[native.index(key) - 1] if key in native and native.index(key) else by_branch.get((github.repo_of(key), pr["base"]))
    return under


def chain(under: dict[str, str | None], key: str) -> tuple[str, ...]:
    """Key and the open tracked PRs under it, bottom first: what stack-enqueue queues when key is named, never a PR above it."""
    return (*chain(under, parent), key) if (parent := under.get(key)) else (key,)


def landing_stacks(data: dict, under: dict[str, str | None] | None = None) -> dict[str, tuple[str, ...]]:
    """Each open PR the drive tracks in a stack, mapped to the stack's open tracked PRs bottom first, as the watch last read them: the PRs one card of his draws together, joined by unders() unless the caller already read them."""
    under = unders(data) if under is None else under
    stacks: dict[str, list[str]] = {}
    for key in under:
        stacks.setdefault(chain(under, key)[0], []).append(key)
    return {key: tuple(sorted(members, key=lambda member: len(chain(under, member)))) for members in stacks.values() if len(members) > 1 or data["prs"][members[0]]["native"] for key in members}


def unready(data: dict, key: str, cards: dict) -> str | None:
    """Why a lander does not queue the PR now, its stage when that is not ready, or None when it lands."""
    from drive import stage

    now = stage.of(data, key, cards)
    return None if now.name == "ready" else f"{now.name}: {now.why}"


def held(data: dict, keys: list[str]) -> str:
    """What of his holds any PR among keys, each by its key, as its stage reads it; empty when nothing does."""
    from drive import stage

    return "; ".join(f"{key}: {why}" for key in keys if (why := stage.held(data, data["prs"][key])))


def together(data: dict, stacks: dict[str, tuple[str, ...]], key: str) -> list[str]:
    """The open PRs his card drew with key that share its landing stack, bottom first, else key alone, since stack-enqueue refuses PRs named together that no stack links, such as the sibling PRs of one order his card draws together."""
    landed = stacks.get(key, (key,))
    return [member for member in data["prs"][key].get("stack") or [key] if data["prs"][member]["state"] == "open" and member in landed]


def landed_with(data: dict, under: dict[str, str | None], stacks: dict[str, tuple[str, ...]], key: str) -> tuple[str, ...]:
    """What his Approve lands key with, bottom first: the PRs of his card on it that share its stack, by together(), each open PR under one of them, and the PRs of that PR's card in turn, since his Approve lands exactly the PRs his card lists, bottom first, and a PR stacked above on a card of its own never holds them."""
    found: dict[str, None] = {}
    todo = [key]
    while todo:
        if (member := todo.pop()) not in found:
            found[member] = None
            todo += [below for mate in together(data, stacks, member) for below in chain(under, mate)]
    return tuple(sorted(found, key=lambda member: (len(chain(under, member)), int(member.split("#")[1]))))


def unit(data: dict, under: dict[str, str | None], stacks: dict[str, tuple[str, ...]], key: str, cards: dict) -> tuple[str, ...]:
    """The PRs one lander queues for key, its landed_with(), once every PR of it lands now, else none."""
    members = landed_with(data, under, stacks, key)
    return members if all(unready(data, member, cards) is None for member in members) else ()


def uncleared_below(data: dict, keys: list[str]) -> list[str]:
    """The open PRs stack-enqueue would queue with keys, below one of them in its native GitHub stack or under one through its base, that neither his Approve nor an exempt class clears."""
    under = dict.fromkeys(key for target in keys for key in github.below(target) if key not in keys)
    return [key for key in under if not (key in data["prs"] and state.cleared(data["prs"][key]))]


def held_below(keys: list[str], below: list[str]) -> str:
    return (
        f"not landed: {', '.join(below)} sits below {', '.join(keys)} in its stack with neither his Approve nor an exempt class, and stack-enqueue queues a stack"
        " from its bottom, so landing a PR above would land it too; it lands once he approves each of them or they merge"
    )


def units(data: dict, keys: list[str], cards: dict) -> list[tuple[str, ...]]:
    """The units a lander lands now among keys, in every repository the drive tracks: each key's unit() that reads ready, whole for a Forge-AI/monorepo unit stack-enqueue queues unless GitHub refused() its merge, and its bottom PR alone after that refusal and in any other repository, which gh pr merge merges one PR at a time, longest first, leaving out one that shares a PR with a unit taken before it, since one lander holds a PR at a time: a unit inside a longer one, or one on the same PR as a sibling's, which lands at a later pass."""
    under = unders(data)
    stacks = landing_stacks(data, under)
    found = dict.fromkeys(
        members if state.queued_by_lander(members[0]) and not any(refused(data["prs"][member]) for member in members) else members[:1]
        for key in keys
        if data["prs"][key]["state"] == "open" and (members := unit(data, under, stacks, key, cards))
    )
    taken: list[tuple[str, ...]] = []
    for members in sorted(found, key=len, reverse=True):
        if all(set(members).isdisjoint(other) for other in taken):
            taken.append(members)
    return taken


def start(name: str, keys: list[str], cards: dict | None) -> list[tuple[str, ...]]:
    """Answer each card his Approve on a PR among keys picks an option on, by answer_picks, then start a lander on each unit among keys that is ready, one his Approve or exempt classes clear or a yasyf/* PR its builder reported, marking its PRs under the drive's lock so no second lander starts on their heads; the units started. With his cards unread, none, since a card of his may hold one. A pick that failed to answer holds its PR as its open card does, and raises once the rest started, naming why."""
    failed = answer_picks(name, keys)
    started: list[tuple[str, ...]] = []
    if cards is not None and units(state.load(name), keys, cards):
        with state.edit(name) as data:
            started = units(data, keys, cards)
            for members in started:
                mark(data, members, spawn(name, members))
    if failed:
        raise state.DriveError("; ".join(failed))
    return started


def answer_picks(name: str, keys: list[str]) -> list[str]:
    """Answer with its option, as his tap on that card would, each card his Approve on an open PR among keys picked, by card.picks as he tapped, that is still open and that tap has answered no time before, once however many PRs it picks it on, then stamp the answer's seq on each such pick, so an Undo on that card leaves it his to answer, and close each card its answer ended; why each pick that failed to answer did."""
    from drive import card, superseded

    data = state.load(name)
    owed: dict[str, list[tuple[str, dict]]] = {}
    for key in keys:
        if (pr := data["prs"][key])["state"] == "open" and approved(pr):
            for wait, pick in pr["tap"]["picks"].items():
                if pick["seq"] is None and any(question["closed"] is None for _, question in view.drawn_on(data, wait)):
                    owed.setdefault(wait, []).append((key, pr["tap"]))
    failed, seqs = [], {}
    for wait, taps in owed.items():
        tap = taps[0][1]
        pick = tap["picks"][wait]
        try:
            seqs[wait] = card.answer_question(name, {"waits": pick["waits"], "url": pick["url"], "label": pick["option"], "action_ts": tap["action_ts"], "source": tap["source"]})["seq"]
        except state.DriveError as error:
            failed.append(f'his Approve on {taps[0][0]} picking "{pick["option"]}" on {pick["url"]}: {error}')
    if not seqs:
        return failed
    with state.edit(name) as data:
        for wait, seq in seqs.items():
            for key, tap in owed[wait]:
                if (now := data["prs"][key]["tap"]) and now["at"] == tap["at"]:
                    now["picks"][wait]["seq"] = seq
    superseded.follow(name, [(job_id, question) for job_id, question in view.drawn_on(state.load(name), *seqs) if (question["closed"] or {}).get("seq") in seqs.values()])
    return failed


def halt(name: str, keys: list[str]) -> list[str]:
    """Stop each lander queueing a Forge-AI/monorepo unit through stack-enqueue that a hold of his on a PR among keys holds, his Hold, the hold label or a card of his opened on its work since it started, by ending its process group, which dequeues what it queued, and record that on each PR of its unit, which reads held until he answers and lands then; the PRs stopped."""
    if not held_landers(state.load(name), keys):
        return []
    with state.edit(name) as data:
        stopped = held_landers(data, keys)
        under = unders(data)
        units = {pid: sorted((key for key, pr in data["prs"].items() if running(pr) and pr["lander"]["pid"] == pid), key=lambda key: len(chain(under, key))) for pid in stopped}
        for pid, unit in units.items():
            os.killpg(pid, signal.SIGTERM)
            noted(name, data, unit, stopped[pid], [])
    return [key for unit in units.values() for key in unit]


def held_landers(data: dict, keys: list[str]) -> dict[int, str]:
    """Each running lander queueing a Forge-AI/monorepo unit with a PR among keys that a hold of his holds, by its pid, with the outcome saying so."""
    return {
        data["prs"][key]["lander"]["pid"]: f"{HELD}: {holding}"
        for key in keys
        if state.queued_by_lander(key) and running(data["prs"][key]) and (holding := held(data, [key]))
    }


def unmergeable(key: str, read: dict) -> str | None:
    """Why a yasyf/* PR may not merge at the head GitHub holds now: it is not open, is a draft, carries the hold label, or sits on another branch than its repository's default."""
    if read["state"] != "OPEN":
        return f"it is {read['state'].lower()}"
    if read["isDraft"]:
        return "it is a draft"
    if github.HOLD_LABEL in read["labels"]:
        return "it carries the hold label"
    if read["baseRefName"] != (default := github.default_branch(github.repo_of(key))):
        return f"it sits on {read['baseRefName']}, not {default}"
    return None


def admin_merged(name: str, key: str) -> str:
    """Merge a yasyf/* PR with --admin at the head GitHub holds now, once a fresh read finds nothing there that unmergeable() names, every check there finished and none failing on its own change, by own_reds(), and pinned to that head so a push since the read merges nothing; what came of it."""
    reads, refused = github.fetch([key])
    if refused:
        return f"not landed: {cut(str(refused), SAID_CHARS)}"
    read = reads[key]
    head = read["headRefOid"]
    if why := unmergeable(key, read):
        return f"not landed: {why}"
    if github.checks(read["checks"], []) == "pending":
        return f"{RUNNING} at {head[:7]}"
    try:
        owned = own_reds(key, read["checks"])
    except github.GithubError as error:
        return f"{UNREAD}: {cut(str(error), SAID_CHARS)}"
    if owned:
        return f"{OWN_RED} {', '.join(owned)} at {head[:7]}, which the default branch does not fail where the PR's run of it merged its head"
    return merged(name, key, read["merge_method"], ["--admin", "--match-head-commit", head])


def own_reds(key: str, rollup: list[dict]) -> list[str]:
    """The name of each check the PR's head fails on its own change: each it fails that the default branch does not fail too, by inherited(), by his words that tooling fixes merge early only when no check is failing on their own change."""
    return sorted({github.check_name(check) for check in github.counted(rollup) if github.outcome(check) in github.FAILED and not inherited(key, check)})


def inherited(key: str, check: dict) -> bool:
    """Whether the default branch fails a check the PR's head fails too, where the PR's run of it merged that head: at the newest commit at or below that one it ran on, as on a `[skip ci]` commit no workflow runs on, once that run finished, which the lander waits for; never for a check no workflow ran, with no run of it there to compare."""
    if not (run := github.run_of(check)):
        return False
    repo, number = key.split("#")
    base = github.read(f"the commit run {run[1]} of {key} tested", ["gh", "api", f"repos/{repo}/actions/runs/{run[1]}", "--jq", f".pull_requests[] | select(.number == {number}) | .base.sha"]).strip()
    named = (run[0], github.check_name(check))
    while (main := branch_checks(repo, base)).get(named, "") is None:
        time.sleep(BRANCH_SECONDS)
    return named in main and main[named] not in github.PASSED


def branch_checks(repo: str, base: str) -> dict[tuple[str, str], str | None]:
    """The conclusion of each check of a workflow run on the default branch, by its workflow and name, at the newest of base and the BRANCH_HISTORY commits below it where it ran and was not cancelled, None while it runs."""
    owner, name = repo.split("/")
    text = (
        f"query {{ repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{ object(oid: {json.dumps(base)}) {{ ... on Commit {{ history(first: {BRANCH_HISTORY}) {{ nodes {{ "
        "checkSuites(first: 20) { nodes { workflowRun { workflow { name } } checkRuns(first: 20) { nodes { name conclusion } } } } } } } } } }"
    )
    found: dict[tuple[str, str], str | None] = {}
    for commit in github.graphql(text, f"the default branch's checks at and below {repo}@{base[:12]}")["data"]["repository"]["object"]["history"]["nodes"]:
        for suite in commit["checkSuites"]["nodes"]:
            for check in suite["checkRuns"]["nodes"] if suite["workflowRun"] else []:
                if check["conclusion"] != "CANCELLED":
                    found.setdefault((suite["workflowRun"]["workflow"]["name"], check["name"]), check["conclusion"])
    return found


def mark(data: dict, keys: tuple[str, ...], pid: int) -> None:
    """Mark the PRs a lander starts on, each keeping what the try before it on the same head came to, so record() says an outcome the same head already said only once, and none ejected, since an ejection is Trunk's label set after the try began."""
    at = state.stamp()
    for key in keys:
        pr = data["prs"][key]
        before = pr["lander"] or {}
        pr["lander"] = {"head": pr["head"], "pid": pid, "at": at, "outcome": None, "was": before.get("outcome") if before.get("head") == pr["head"] else None}
        pr["ejected"] = False


def spawn(name: str, keys: tuple[str, ...]) -> int:
    """Run `drive pr land` on the keys as its own process, outliving the watch pass or card tap that started it, its output appended to the drive's landing log."""
    return state.detach(name, ["pr", "land", *map(state.pr_url, keys)], LOG)


def run(name: str, keys: list[str], dry_run: bool = False) -> str:
    """Land one unit his Approve or exempt classes clear, or one yasyf/* PR, in whichever repository the drive tracks it, never a PR the drive does not track or one someone else opened that no order of his it serves names, then record what came of it on each PR, which its stage reads, and as a landing event: a yasyf/* PR merges with gh pr merge --admin at the head a fresh read finds every check finished on with none failing on its own change, a Forge-AI/monorepo unit queues through stack-enqueue, and a PR of any other repository merges with gh pr merge at the head its stage read ready, which GitHub merges only once its repository's rules pass, each by the method its repository allows, by github.merge_method. His Approve lands the PR whatever the check of its change against his words says, which only informs his card and its owner; a unit with a PR below it that nothing clears is not landed, since its landing would land that PR too, nor one with a PR its changes put outside its exempt class, nor one a hold of his holds, his Hold, the hold label or an open card of his on the work of any job carrying a PR of it. A dry run runs stack-enqueue --check as the landing would, says what it would do, and lands and records nothing."""
    data = state.load(name)
    if untracked := [key for key in keys if key not in data["prs"]]:
        raise state.DriveError(f"{', '.join(untracked)}: the drive tracks no such PR, so it lands none; drive pr add <url> --job <job> registers one first")
    if refused := [key for key in keys if not (state.merges_untapped(key) or state.cleared(data["prs"][key]))]:
        raise state.DriveError(f"{', '.join(refused)}: the drive lands a yasyf/* PR, or a PR he approved or an exempt class lands, and no other")
    if foreign := [why for key in keys if (why := state.foreign_pr(data, key))]:
        raise state.DriveError("; ".join(foreign))
    below: list[str] = []
    asked: dict[str, str] = {}
    if holding := held(data, keys):
        outcome = f"{HELD}: {holding}"
    elif state.merges_untapped(keys[0]):
        outcome = f"would merge it with {' '.join(MERGE)} --{data['prs'][keys[0]]['merge_method']} --admin once every check at its head finished with none failing on its own change, then install each plugin it changes" if dry_run else admin_merged(name, keys[0])
    else:
        try:
            below = uncleared_below(data, keys)
            misfiled = "; ".join(why for key in keys if (why := unexempt(data, key)))
        except (github.GithubError, state.DriveError) as error:
            print(error, flush=True)
            outcome = f"{UNREAD}: {cut(str(error), SAID_CHARS)}"
        else:
            if misfiled:
                outcome = f"not landed: {misfiled}"
            elif below:
                outcome = held_below(keys, below)
            elif state.queued_by_lander(keys[0]):
                outcome = enqueued(name, Path(data["roles"]["coordinator"]["cwd"]), keys, data["prs"], asked, dry_run, any(requeues(data, data["prs"][key]) for key in keys))
            else:
                pr = data["prs"][keys[0]]
                outcome = f"would merge it with {' '.join(MERGE)} --{pr['merge_method']} --match-head-commit {pr['head'][:12]}" if dry_run else merged(name, keys[0], pr["merge_method"], ["--match-head-commit", pr["head"]])
    if dry_run:
        return outcome
    return record(name, keys, outcome, below, asked)


def record(name: str, keys: list[str], outcome: str, below: list[str], asked: dict[str, str]) -> str:
    """Record what came of a landing on each PR, with the top of keys as its entry, the PR stack-enqueue submits and so the one whose labels say Trunk ejected them all, and as a landing event unless the try before it on the same head came to the same, such as a retried landing that again could make no worktree, and on each PR asked names the head its lander asked forge-pr-reviewer to review, so none is asked twice; on the newest install, which `drive pr land --outcome` records it on when one landed while this lander worked."""
    install.reopen(["pr", "land", *map(state.pr_url, keys), "--drive", name, "--outcome", outcome, *(flag for key in below for flag in ("--below", key)), *(flag for key, head in asked.items() for flag in ("--asked", key, head))])
    with state.edit(name) as data:
        for key, head in asked.items():
            data["prs"][key]["review_asked"] = head
        noted(name, data, keys, outcome, below)
    return outcome


def noted(name: str, data: dict, keys: list[str], outcome: str, below: list[str]) -> None:
    for key in keys:
        pr = data["prs"][key]
        if outcome == QUEUED and requeues(data, pr):
            pr["flaky"]["requeued"] = state.stamp()
        pr["lander"] = (pr["lander"] or {"head": pr["head"], "pid": os.getpid(), "at": state.stamp()}) | {"outcome": outcome, "below": below, "entry": keys[-1]}
        if pr["lander"].get("was") == outcome:
            continue
        repo, number = key.split("#")
        state.append_event(name, "landing", repo=repo, number=int(number), change=outcome, lane=state.heard_lane(data, pr["order"]))


class Halted(SystemExit):
    pass


def halted(signum: int, frame: object) -> None:
    raise Halted(128 + signum)


def said(output: str) -> str:
    return cut("; ".join([line for line in output.splitlines() if line.strip()][-SAID_LINES:]), SAID_CHARS)


def enqueued(name: str, clone: Path, keys: list[str], prs: dict, asked: dict[str, str], dry_run: bool = False, flake: bool = False) -> str:
    """Queue the unit through stack-enqueue as the head of its repository's default branch holds it, run from a fresh() worktree of the coordinator checkout's clone and never from that checkout, with --flake when a PR of it requeues() at the head Trunk ejected it at: --check first, whose exit of ENQUEUE_FAILED ends the landing as the lander's own failure, by unfinished(), as the same exit of --watch does, and whose BLOCKED lines name each head approved only on an older one, which stack-enqueue names only once ai-review finished on the head; a dry run stops there, saying what it would queue and the BLOCKED lines it read; then forge-pr-reviewer is asked to review each such head that prs, the drive's PRs as the lander read them at its start, records no ask of, each head it asks added to asked for record() to keep, and --watch, which alone tells a wait from a fix, such as GitHub still computing mergeability from a conflict, waits until Trunk holds every PR of it or one needs a fix rather than a wait, unless halt() ends it first, which dequeues each PR of it; what came of it. It reads and writes no drive state, so an install that saves the drive while it makes the worktree, runs --check or waits on Trunk never ends the landing."""
    repo = github.repo_of(keys[0])
    numbers = [key.split("#")[1] for key in keys]
    flags = ["--flake"] if flake else []
    signal.signal(signal.SIGTERM, halted)
    try:
        branch = github.default_branch(repo)
        with fresh(name, clone, branch) as (checkout, sha):
            script = str(checkout / ENQUEUE)
            print(f"{', '.join(keys)}: stack-enqueue from {checkout}, a fresh worktree of {repo}'s {branch} at {sha}", flush=True)
            try:
                checked = subprocess.run([script, "--check", *flags, *numbers], cwd=checkout, capture_output=True, text=True)
            except OSError as error:
                return f"not landed: {script} did not start: {error}"
            print(checked.stdout + checked.stderr, flush=True)
            if checked.returncode == ENQUEUE_FAILED:
                return unfinished(name, "--check", keys, checked)
            blocked = list(BLOCKED_LINE.finditer(checked.stdout))
            if dry_run:
                read = f"; --watch waits out or stops on what --check read: {cut('; '.join(line[0] for line in blocked), SAID_CHARS)}" if blocked else ""
                return f"would queue {', '.join(keys)} through stack-enqueue {' '.join(['--watch', *flags])} from {branch} at {sha[:12]}: every PR below it is merged, approved by him or exempt{read}"
            for line in blocked:
                key = f"{repo}#{line['number']}"
                if not OLDER_HEAD.search(line["gaps"]) or prs[key]["review_asked"] == line["head"]:
                    continue
                if refused := ask_review(repo, line["number"], line["head"]):
                    return refused
                asked[key] = line["head"]
            try:
                done = subprocess.run([script, "--watch", *flags, *numbers], cwd=checkout, capture_output=True, text=True)
            except Halted:
                for number in numbers:
                    subprocess.run([script, "--dequeue", number], cwd=checkout)
                raise
    except github.GithubError as error:
        return f"{NO_WORKTREE} {repo}'s default branch to queue {', '.join(keys)} from: {cut(str(error), SAID_CHARS)}"
    print(done.stdout + done.stderr, flush=True)
    if done.returncode == 0:
        return QUEUED
    if done.returncode == ENQUEUE_FAILED:
        return unfinished(name, "--watch", keys, done)
    return f"not landed: stack-enqueue --watch exited {done.returncode}: {said(done.stdout + done.stderr)}; its whole output is in {state.drive_dir(name) / LOG}"


def unfinished(name: str, mode: str, keys: list[str], done: subprocess.CompletedProcess) -> str:
    """The lander's own failure when stack-enqueue exits ENQUEUE_FAILED, its FAILED, which says nothing of the PRs, such as a GitHub read that timed out, so the watch's next pass tries the same head again."""
    return f"{NO_ENQUEUE} {mode} on {', '.join(keys)}, which exited {ENQUEUE_FAILED} on a failure of its own: {said(done.stdout + done.stderr)}; its whole output is in {state.drive_dir(name) / LOG}"


@contextmanager
def fresh(name: str, clone: Path, branch: str) -> Iterator[tuple[Path, str]]:
    """A worktree of the clone's repository at the head of branch as origin holds it now, made for one landing in the drive's landing folder and removed once it ends: its path and that head's sha. A landing runs the repository's scripts, and the toolchain its mise.toml pins for them, as that head holds them, never as the coordinator's checkout holds them at whatever commit and edits it carries, which it leaves untouched. Every core checks it out at once, with no hook, since the monorepo's post-checkout hook warms node_modules that no landing step reads."""
    git = ["git", "-C", str(clone)]
    sha = github.fetched(str(clone), branch)
    checkout = state.drive_dir(name) / LANDINGS / str(os.getpid())
    try:
        github.run([*git, "-c", "core.hooksPath=/dev/null", "-c", "checkout.workers=0", "worktree", "add", "-q", "--detach", str(checkout), sha], timeout=CHECKOUT_SECONDS)
    except github.GithubError as error:
        raise github.GithubError(f"git worktree add at {sha[:12]} failed: {error}") from None
    try:
        yield checkout, sha
    finally:
        subprocess.run([*git, "worktree", "remove", "--force", str(checkout)])


def ask_review(repo: str, number: str, head: str) -> str | None:
    """Post the comment ai-review names for a head it will not review on its own on the PR, whose caller records that head as its review_asked so none is asked twice; why not, when GitHub refused it."""
    done = subprocess.run(["gh", "pr", "comment", number, "-R", repo, "--body", REVIEW_ASK], capture_output=True, text=True)
    if done.returncode:
        return f"not landed: #{number} waits on a review of head {head}, and gh pr comment refused {REVIEW_ASK!r}: {cut(' '.join(done.stderr.split()), SAID_CHARS)}"
    print(f"#{number} asked forge-pr-reviewer to review head {head}: {done.stdout.strip()}", flush=True)
    return None


def merged(name: str, key: str, method: str, flags: list[str]) -> str:
    """Merge a PR by method, squash, rebase or merge, the one github.merge_method reads its repository allowing, with gh pr merge and the flags given, --admin for a yasyf/* one by his rule for his own repositories, and --match-head-commit, which GitHub merges only at that head and, without --admin, only once its repository's rules pass, or through GitHub's merge-async endpoint once gh pr merge is refused for a PR of a native GitHub stack, with gh itself since ccx's GitHub gateway refuses a merge; record its merge commit on its row, then install from it each plugin of this Mac it changes; what came of it, a refusal not landed but a yasyf/* squash merge GitHub refuses on a conflict, which rebased() takes."""
    repo, number = key.split("#")
    if (refusal := merged_sync(key, method, flags)) and STACKED in refusal:
        print(f"{key}: {refusal}; merging it through GitHub's {MERGE_ASYNC} instead", flush=True)
        refusal = merged_async(key, method, flags)
    if refusal and method == "squash" and state.merges_untapped(key) and any(conflict in refusal for conflict in CONFLICTS):
        return rebased(name, key, refusal)
    if refusal:
        return f"not landed: {refusal}"
    try:
        sha = github.run(["gh", "pr", "view", number, "-R", repo, "--json", "mergeCommit", "-q", ".mergeCommit.oid"]).strip()
        files = github.files(key)
    except github.GithubError as error:
        return f"merged, but its merge commit did not read, so nothing was installed: {cut(str(error), SAID_CHARS)}"
    with state.edit(name) as data:
        data["prs"][key]["merged_sha"] = sha
    plugins = install.touched(repo, files)
    if not plugins:
        return f"merged at {sha[:12]}; it changes no plugin installed on this Mac"
    lines = [f"merged at {sha[:12]}"]
    for plugin in plugins:
        done = subprocess.run([str(DRIVE), "install", plugin, "--sha", sha, "--drive", name], capture_output=True, text=True)
        print(done.stdout + done.stderr, flush=True)
        lines.append(done.stdout.splitlines()[0] if done.returncode == 0 else f"but drive install {plugin} --sha {sha[:12]} failed: {said(done.stderr)}")
    return "; ".join(lines)


def merged_sync(key: str, method: str, flags: list[str]) -> str | None:
    """Merge a PR by method with gh pr merge and the flags given; what gh said refusing it, or None once merged. GitHub refuses a merge whose base moved while it merged, which another merge into that base did, so the lander merges again on the new base."""
    repo, number = key.split("#")
    command = ["gh", "pr", "merge", number, "-R", repo, f"--{method}", *flags]
    while (done := subprocess.run(command, capture_output=True, text=True)).returncode and BASE_MOVED in done.stderr:
        print(f"{key}: {' '.join(done.stderr.split())}; merging it again on its new base", flush=True)
    return f"{' '.join([*MERGE, f'--{method}', *flags])} refused it: {cut(' '.join(done.stderr.split()), SAID_CHARS)}" if done.returncode else None


def merged_async(key: str, method: str, flags: list[str]) -> str | None:
    """Merge a PR of a native GitHub stack by method through GitHub's merge-async endpoint, the one GitHub merges a stacked PR through, past the rules its actor may bypass for --admin, which GitHub grants only the stack's bottom PR, the one a lander outside Forge-AI/monorepo lands, or at the head --match-head-commit names, then read its result every ASYNC_SECONDS until GitHub ends it; why GitHub refused it, or None once merged."""
    repo, number = key.split("#")
    endpoint = f"repos/{repo}/pulls/{number}/{MERGE_ASYNC}"
    pinned = ["-f", f"sha={flags[flags.index('--match-head-commit') + 1]}"] if "--match-head-commit" in flags else []
    fields = ["-f", f"merge_method={method}", "-f", "merge_action=direct_merge", "-F", f"bypass_rules={str('--admin' in flags).lower()}", *pinned]
    done = subprocess.run(["gh", "api", "-X", "PUT", endpoint, *fields], capture_output=True, text=True)
    if done.returncode:
        return f"GitHub's {MERGE_ASYNC} refused it: {cut(' '.join((done.stdout + done.stderr).split()), SAID_CHARS)}"
    result = json.loads(done.stdout)
    while result["status"] == "pending":
        time.sleep(ASYNC_SECONDS)
        try:
            result = json.loads(github.read(f"the result of {key}'s {MERGE_ASYNC}", ["gh", "api", f"{endpoint}/{result['details']['uuid']}"]))
        except github.GithubError as error:
            return cut(str(error), SAID_CHARS)
    return None if result["status"] == "merged" else f"GitHub's {MERGE_ASYNC} ended it {result['status']}: {cut(result['details'].get('message', ''), SAID_CHARS)}"


def rebased(name: str, key: str, refusal: str) -> str:
    """Rebase a yasyf/* PR whose merge GitHub refused on a conflict onto its base as one commit, by his rule for direct merges, "always rebase first", in the drive's own clone of its repository, each markdown page git's merge conflicts in merged again by clause_merged(), and push it to its branch with a lease on the head fetched, so the watch, reading that head, starts the lander on it as on any head its builder pushes; a conflict in any other file, or in a clause both sides changed, is not landed, and reads red for its builder, who settles it. The commit carries the message of the branch's one commit, or its PR's title over each commit's message, as GitHub's squash of the branch would."""
    pr = state.load(name)["prs"][key]
    refs = f"refs/lander/{os.getpid()}"
    try:
        clone = cloned(name, github.repo_of(key))
        git = ["git", "-C", str(clone)]
        try:
            github.run([*git, "fetch", "-q", "--no-tags", "--no-write-fetch-head", "origin", f"+refs/heads/{pr['base']}:{refs}/base", f"+refs/heads/{pr['branch']}:{refs}/head"])
            base, head = github.run([*git, "rev-parse", f"{refs}/base", f"{refs}/head"]).split()
            if subprocess.run([*git, "merge-base", "--is-ancestor", base, head]).returncode == 0:
                return f"not landed: {refusal}; {pr['branch']} already sits on {pr['base']} at {base[:12]}, so a rebase changes nothing"
            tree, pages, unsettled = settled(clone, base, head)
            if unsettled:
                return f"not landed: {refusal}; {', '.join(unsettled)} conflict past a clause-by-clause merge, so its builder rebases it"
            messages = [message.strip() for message in github.run([*git, "log", "--reverse", "--format=%B%x00", f"{base}..{head}"]).split("\0") if message.strip()]
            message = messages[0] if len(messages) == 1 else "\n\n".join([pr["title"], *(f"* {message}" for message in messages)])
            commit = github.run([*git, "commit-tree", tree, "-p", base, "-F", "-"], stdin=message).strip()
            github.run([*git, "push", "-q", f"--force-with-lease=refs/heads/{pr['branch']}:{head}", "origin", f"{commit}:refs/heads/{pr['branch']}"])
        finally:
            subprocess.run([*git, "update-ref", "--stdin"], input=f"delete {refs}/base\ndelete {refs}/head\n", capture_output=True, text=True)
    except github.GithubError as error:
        return f"not landed: {refusal}; the lander could not rebase it onto {pr['base']}: {cut(str(error), SAID_CHARS)}"
    how = f"{', '.join(pages)} merged clause by clause with git merge-file" if pages else "which git merges clean"
    return f"{REBASED} {pr['base']} at {base[:12]} as {commit[:12]} after GitHub refused its merge on a conflict, {how}; the lander merges that head once the watch reads it"


def cloned(name: str, repo: str) -> Path:
    """The drive's own blobless bare clone of repo, in its clones folder, made on first use."""
    clone = state.drive_dir(name) / CLONES / repo
    if not clone.exists():
        github.run(["git", "clone", "-q", "--bare", "--filter=blob:none", CLONE_URL.format(repo), str(clone)], timeout=CHECKOUT_SECONDS)
    return clone


def settled(clone: Path, base: str, head: str) -> tuple[str, list[str], list[str]]:
    """The tree git's merge of head onto base makes, with each markdown page it conflicts in that clause_merged() merges written into it; the pages it merged, and each path still conflicting."""
    git = ["git", "-C", str(clone)]
    done = subprocess.run([*git, "merge-tree", "--write-tree", "-z", "--no-messages", base, head], capture_output=True, text=True)
    if done.returncode > 1:
        raise github.GithubError(f"git merge-tree exited {done.returncode}: {' '.join(done.stderr.split())}")
    tree, *entries = [entry for entry in done.stdout.split("\0") if entry]
    stages: dict[str, dict[str, str]] = {}
    for entry in entries:
        info, path = entry.split("\t", 1)
        _, oid, stage = info.split()
        stages.setdefault(path, {})[stage] = oid
    texts = {
        path: clause_merged(*(github.run([*git, "cat-file", "blob", sides[stage]]) for stage in "123")) if path.endswith(".md") and sides.keys() == {"1", "2", "3"} else None
        for path, sides in stages.items()
    }
    unsettled = [path for path, text in texts.items() if text is None]
    if unsettled or not texts:
        return tree, [], unsettled
    index = clone / f"lander-{os.getpid()}.index"
    indexed = ["env", f"GIT_INDEX_FILE={index}", *git]
    try:
        github.run([*indexed, "read-tree", tree])
        for path, text in texts.items():
            blob = github.run([*git, "hash-object", "-w", "--stdin"], stdin=text).strip()
            github.run([*indexed, "update-index", "--cacheinfo", f"100644,{blob},{path}"])
        return github.run([*indexed, "write-tree", "--missing-ok"]).strip(), list(texts), []
    finally:
        index.unlink(missing_ok=True)


def clause_merged(base: str, ours: str, theirs: str) -> str | None:
    """The three-way merge of a markdown page by git merge-file with each of its lines split after each clause mark, so edits to different clauses of one long table row merge as git merges edits to different lines; None while a clause conflicts."""
    with tempfile.TemporaryDirectory() as folder:
        sides = []
        for side, text in (("ours", ours), ("base", base), ("theirs", theirs)):
            (path := Path(folder, side)).write_text(CLAUSE.sub(" \n", text.replace("\n", LINE_END + "\n")), encoding="utf-8")
            sides.append(str(path))
        done = subprocess.run(["git", "merge-file", "-p", *sides], capture_output=True, text=True, encoding="utf-8")
    return None if done.returncode else done.stdout.replace(" \n", " ").replace(LINE_END + "\n", "\n")
