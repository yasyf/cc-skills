import json
import os
import re
import signal
import subprocess
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
MERGE_ASYNC = "merge-async"
STACKED = "must be merged using the asynchronous merge REST API"
ASYNC_SECONDS = 2
MERGE = ("gh", "pr", "merge", "--squash")
# TODO: delete with its case in stage.unlanded once no lander of an install that refused mid-check still runs: its record() reopens on this install with this outcome.
NEWER_INSTALL = "not landed: a newer long-running saved the drive while stack-enqueue --check ran, so the landing runs again on it"
OWN_FAILURE = "the lander could not"
NO_WORKTREE = f"{OWN_FAILURE} check out"
NO_ENQUEUE = f"{OWN_FAILURE} finish stack-enqueue"
ENQUEUE_FAILED = 5
HELD = "not landed while he holds it"
AWAY = "not landed while he is away"
UNREAD = "not landed: a GitHub read failed before it queued or merged anything, so the next watch pass lands it again"
RETRIED = (NEWER_INSTALL, OWN_FAILURE, HELD, AWAY, UNREAD)
REVIEW_ASK = "@forge-pr-reviewer review"
BLOCKED_LINE = re.compile(r"^#(?P<number>\d+) BLOCKED (?P<head>\w+) (?P<gaps>.+)$", re.MULTILINE)
OLDER_HEAD = re.compile(r"awaiting approval on head from \S+ \(approved [^,]+, (?:on an older head|dismissed by a push)\)")
REVERT = re.compile(r"^Reverts?\s|⏪|\bThis reverts\b", re.MULTILINE)
STORED_DATA = re.compile(r"(?:^|/)migrations?/|(?:^|/)[^/]*schema[^/]*$|\.(?:sql|proto)$", re.IGNORECASE)
CI_FILES = re.compile(r"^(?:\.github|\.buildkite|(?:go|infra)/ci)/|^tools/ci(?:-ts)?(?:/|$)")


def open_cards(data: dict, job_id: str, pr: dict | None, cards: dict | None, exempt: str | None = None) -> list[str]:
    """His open cards holding a job's work, its PR when given, from landing, since work does not land with a question of his on it unanswered: each card the PR or job waits on, and each card of an open question holding its work by state.part_cards, the one rule a BUILT line's waits read too; none for an immediate fix, which lands while he picks the proper fix, or a flake fix, the PR's exempt class or else `exempt`, which fixes a flaking test the day it flakes by his ruling 532cd3d, and on an open incident, whose repair lands on the recommended option, only a card whose question asks him to lift a ruling of his, which `drive job ask --unsettled` marks, by his pick "Hold the PR until you answer; the drive finds each card against your recorded answers"."""
    job = data["jobs"][job_id]
    if state.IMMEDIATE_FIX in job["parts"] or (pr.get("exempt") if pr is not None else exempt) == state.FLAKE_FIX:
        return []
    waited = [wait for wait in view.holding(data, list(dict.fromkeys([*(pr["waits"] if pr is not None else []), *job["waits"]])), cards) if state.card_wait(data, wait)]
    found = list(dict.fromkeys([*waited, *state.part_cards(data, job_id, pr)]))
    if state.open_incidents(data, [job["order"], *job["carries"]]):
        return [card for card in found if any(question["unsettled"] and question["closed"] is None for _, question in view.drawn_on(data, card))]
    return found


def pr_cards(data: dict, pr: dict, cards: dict | None) -> list[str]:
    """His open cards holding a PR from landing: those holding the work of any job carrying it, its own and each that holds its landing, by open_cards()."""
    return list(dict.fromkeys(card for job_id in state.pr_jobs(data, pr) for card in open_cards(data, job_id, pr, cards)))


def ship_held(data: dict, job_id: str, cards: dict | None, exempt: str | None = None) -> list[str]:
    """His open cards holding a job's PR from going up, which the `bash_guard` hook refuses the `ccx vcs ship` that would open it by, on this Mac and through `drive job held` on a Sprite: none once the job has an open PR, which a ship pushes to rather than opens, holding it as open_cards() holds it once registered under the exempt class `exempt` the same call gives it."""
    if any(data["prs"].get(state.pr_key(url), {}).get("state") == "open" for url in data["jobs"][job_id]["prs"]):
        return []
    return open_cards(data, job_id, None, cards, exempt)


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
    return not state.queued_by_lander(key) and (running(pr) or ((pr["lander"] or {}).get("outcome") or "").startswith("merged"))


def landing(pr: dict) -> bool:
    """Whether a lander holds the PR on its head: still running on it, or done with it queued for merge and not ejected by Trunk since."""
    lander = pr["lander"] or {}
    return lander.get("head") == pr["head"] and (running(pr) or lander.get("outcome") == QUEUED and not pr["ejected"])


def flaked(pr: dict) -> dict | None:
    """What drive pr set --flaky kept at the PR's head: the flaky test outside its diff Trunk ejected it on there, and that test's fix, a PR the drive tracks; None at any other head, and once a lander requeued this one."""
    flaky = pr["flaky"]
    return flaky if flaky and flaky["head"] == pr["head"] and not flaky["requeued"] else None


def requeues(data: dict, pr: dict) -> bool:
    """Whether a lander queues the PR again at the head Trunk ejected it at, with stack-enqueue --flake, as it does once, when its flaky test's fix merged."""
    return bool(flaky := flaked(pr)) and data["prs"][flaky["fix"]]["state"] == "merged"


def refused(pr: dict) -> bool:
    """Whether GitHub refused, for the first time at the PR's head, the merge its lander queued there once Trunk's merge queue took it, with no failing check, so the lander lands it again one PR at a time, bottom first; a refusal of that one-at-a-time landing is not landed."""
    lander = pr["lander"] or {}
    return lander.get("head") == pr["head"] and (lander.get("outcome") or "").startswith(REFUSED) and not (lander.get("was") or "").startswith(REFUSED)


def conflicted(pr: dict) -> bool:
    """Whether GitHub refused the lander's merge at the PR's head for a conflict with its base, which only its builder's rebase settles, whatever the watch's last read of its mergeability said."""
    lander = pr["lander"] or {}
    return lander.get("head") == pr["head"] and any(conflict in (lander.get("outcome") or "") for conflict in CONFLICTS)


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


def held(data: dict, keys: list[str], cards: dict | None) -> str:
    """What of his holds any PR among keys, each by its key, as its stage reads it; empty when nothing does."""
    from drive import stage

    return "; ".join(f"{key}: {why}" for key in keys if (why := stage.held(data, data["prs"][key], cards)))


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
    """Start a lander on each unit among keys that is ready, one his Approve or exempt classes clear or a yasyf/* PR its builder reported, marking its PRs under the drive's lock so no second lander starts on their heads; the units started. With his cards unread, none, since a card of his may hold one."""
    if cards is None or not units(state.load(name), keys, cards):
        return []
    with state.edit(name) as data:
        started = units(data, keys, cards)
        for members in started:
            mark(data, members, spawn(name, members))
    return started


def halt(name: str, keys: list[str], cards: dict | None) -> list[str]:
    """Stop each lander queueing a Forge-AI/monorepo unit through stack-enqueue that a hold of his on a PR among keys holds, his Hold, the hold label or a card of his opened on its work since it started, by ending its process group, which dequeues what it queued, and record that on each PR of its unit, which reads held until he answers and lands then; the PRs stopped. With his cards unread, none."""
    if cards is None or not held_landers(state.load(name), keys, cards):
        return []
    with state.edit(name) as data:
        stopped = held_landers(data, keys, cards)
        under = unders(data)
        units = {pid: sorted((key for key, pr in data["prs"].items() if running(pr) and pr["lander"]["pid"] == pid), key=lambda key: len(chain(under, key))) for pid in stopped}
        for pid, unit in units.items():
            os.killpg(pid, signal.SIGTERM)
            noted(name, data, unit, stopped[pid], [])
    return [key for unit in units.values() for key in unit]


def held_landers(data: dict, keys: list[str], cards: dict) -> dict[int, str]:
    """Each running lander queueing a Forge-AI/monorepo unit with a PR among keys that a hold of his holds, by its pid, with the outcome saying so."""
    return {
        data["prs"][key]["lander"]["pid"]: f"{HELD}: {holding}"
        for key in keys
        if state.queued_by_lander(key) and running(data["prs"][key]) and (holding := held(data, [key], cards))
    }


def unmergeable(key: str, read: dict) -> str | None:
    """Why a yasyf/* PR may not merge at the head GitHub holds now: it is not open, is a draft, carries the hold label, sits on another branch than its repository's default, or a check fails there, by his words that tooling fixes merge early only when no check is failing on their own change."""
    if read["state"] != "OPEN":
        return f"it is {read['state'].lower()}"
    if read["isDraft"]:
        return "it is a draft"
    if github.HOLD_LABEL in read["labels"]:
        return "it carries the hold label"
    if read["baseRefName"] != (default := github.default_branch(github.repo_of(key))):
        return f"it sits on {read['baseRefName']}, not {default}"
    if failed := github.failing(read["checks"]):
        return f"its checks fail at {read['headRefOid'][:7]}: {', '.join(failed)}"
    return None


def admin_merged(name: str, key: str) -> str:
    """Merge a yasyf/* PR with --admin at the head GitHub holds now, once a fresh read finds nothing there that unmergeable() names, while any check still runs, and pinned to that head so a push since the read merges nothing; what came of it."""
    reads, refused = github.fetch([key])
    if refused:
        return f"not landed: {cut(str(refused), SAID_CHARS)}"
    if why := unmergeable(key, reads[key]):
        return f"not landed: {why}"
    return merged(name, key, ["--admin", "--match-head-commit", reads[key]["headRefOid"]])


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
    """Land one unit his Approve or exempt classes clear, or one yasyf/* PR, in whichever repository the drive tracks it, never a PR the drive does not track or one someone else opened that no order of his it serves names, then record what came of it on each PR, which its stage reads, and as a landing event: a yasyf/* PR merges with gh pr merge --admin at the head a fresh read finds no check failing on, a Forge-AI/monorepo unit queues through stack-enqueue, and a PR of any other repository merges with gh pr merge at the head its stage read ready, which GitHub merges only once its repository's rules pass. His Approve lands the PR whatever the check of its change against his words says, which only informs his card and its owner; a unit with a PR below it that nothing clears is not landed, since its landing would land that PR too, nor one with a PR its changes put outside its exempt class, nor one a hold of his holds, his Hold, the hold label or an open card of his on the work of any job carrying a PR of it, nor, while he is not present, one state.unattended() holds. A dry run runs stack-enqueue --check as the landing would, says what it would do, and lands and records nothing."""
    data = state.load(name)
    if untracked := [key for key in keys if key not in data["prs"]]:
        raise state.DriveError(f"{', '.join(untracked)}: the drive tracks no such PR, so it lands none; drive pr add <url> --job <job> registers one first")
    if refused := [key for key in keys if not (state.merges_untapped(key) or state.cleared(data["prs"][key]))]:
        raise state.DriveError(f"{', '.join(refused)}: the drive lands a yasyf/* PR, or a PR he approved or an exempt class lands, and no other")
    if foreign := [why for key in keys if (why := state.foreign_pr(data, key))]:
        raise state.DriveError("; ".join(foreign))
    below: list[str] = []
    asked: dict[str, str] = {}
    if holding := held(data, keys, view.live_cards(name)):
        outcome = f"{HELD}: {holding}"
    elif away := next((why for key in keys if (why := state.unattended(data, data["prs"][key], state.now()))), None):
        outcome = f"{AWAY}: {away}"
    elif state.merges_untapped(keys[0]):
        outcome = f"would merge it with {' '.join(MERGE)} --admin once no check fails at its head, then install each plugin it changes" if dry_run else admin_merged(name, keys[0])
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
                head = data["prs"][keys[0]]["head"]
                outcome = f"would merge it with {' '.join(MERGE)} --match-head-commit {head[:12]}" if dry_run else merged(name, keys[0], ["--match-head-commit", head])
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
    """The lander's own failure when stack-enqueue exits ENQUEUE_FAILED, its FAILED, which says nothing of the PRs, such as a binary it runs that a Homebrew upgrade unlinked for a moment or a GitHub read that timed out, so the watch's next pass tries the same head again."""
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


def merged(name: str, key: str, flags: list[str]) -> str:
    """Merge a PR with gh pr merge and the flags given, --admin for a yasyf/* one by his rule for his own repositories, and --match-head-commit, which GitHub merges only at that head and, without --admin, only once its repository's rules pass, or through GitHub's merge-async endpoint once gh pr merge is refused for a PR of a native GitHub stack, with gh itself since ccx's GitHub gateway refuses a merge; record its merge commit on its row, then install from it each plugin of this Mac it changes; what came of it, a refusal not landed."""
    repo, number = key.split("#")
    if (refusal := merged_sync(key, flags)) and STACKED in refusal:
        print(f"{key}: {refusal}; merging it through GitHub's {MERGE_ASYNC} instead", flush=True)
        refusal = merged_async(key, flags)
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


def merged_sync(key: str, flags: list[str]) -> str | None:
    """Merge a PR with gh pr merge and the flags given; what gh said refusing it, or None once merged. GitHub refuses a merge whose base moved while it merged, which another merge into that base did, so the lander merges again on the new base."""
    repo, number = key.split("#")
    command = ["gh", "pr", "merge", number, "-R", repo, "--squash", *flags]
    while (done := subprocess.run(command, capture_output=True, text=True)).returncode and BASE_MOVED in done.stderr:
        print(f"{key}: {' '.join(done.stderr.split())}; merging it again on its new base", flush=True)
    return f"{' '.join([*MERGE, *flags])} refused it: {cut(' '.join(done.stderr.split()), SAID_CHARS)}" if done.returncode else None


def merged_async(key: str, flags: list[str]) -> str | None:
    """Squash-merge a PR of a native GitHub stack through GitHub's merge-async endpoint, the one GitHub merges a stacked PR through, past the rules its actor may bypass for --admin, which GitHub grants only the stack's bottom PR, the one a lander outside Forge-AI/monorepo lands, or at the head --match-head-commit names, then read its result every ASYNC_SECONDS until GitHub ends it; why GitHub refused it, or None once merged."""
    repo, number = key.split("#")
    endpoint = f"repos/{repo}/pulls/{number}/{MERGE_ASYNC}"
    pinned = ["-f", f"sha={flags[flags.index('--match-head-commit') + 1]}"] if "--match-head-commit" in flags else []
    fields = ["-f", "merge_method=squash", "-f", "merge_action=direct_merge", "-F", f"bypass_rules={str('--admin' in flags).lower()}", *pinned]
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
