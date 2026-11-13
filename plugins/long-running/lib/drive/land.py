import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from drive import github, install, served, state, view
from drive.render import cut

ENQUEUE = Path(".agents/skills/submit-pr/scripts/stack-enqueue")
DRIVE = Path(__file__).resolve().parents[2] / "bin" / "drive"
LOG = "landings.log"
LANDINGS = "landing"
CLONES = "clones"
CHECKOUT_SECONDS = 600
LFS_POINTERS = ("-c", "filter.lfs.smudge=git-lfs smudge --skip -- %f", "-c", "filter.lfs.process=git-lfs filter-process --skip")
SAID_CHARS = 400
SAID_LINES = 4
QUEUED = "queued for merge"
BASE_MOVED = "Base branch was modified"
NOT_MERGING = "not merging it"
NEWER_INSTALL = "not landed: a newer long-running saved the drive while stack-enqueue --check ran, so the landing runs again on it"
LANDS_ON = ("approved", "stale")
REVIEW_ASK = "@forge-pr-reviewer review"
BLOCKED_LINE = re.compile(r"^#(?P<number>\d+) BLOCKED (?P<head>\w+) (?P<gaps>.+)$", re.MULTILINE)
OLDER_HEAD = re.compile(r"awaiting approval on head from \S+ \(approved [^,]+, (?:on an older head|dismissed by a push)\)")
RETARGETS = re.compile(r"(?:targets \S+ without a native GitHub stack|sits in a native stack on \S+) and retargets onto \S+ once #\d+ lands")
REVERT = re.compile(r"^Reverts?\s|⏪|\bThis reverts\b", re.MULTILINE)
STORED_DATA = re.compile(r"(?:^|/)migrations?/|(?:^|/)[^/]*schema[^/]*$|\.(?:sql|proto)$", re.IGNORECASE)
CI_FILES = re.compile(r"^(?:\.github|\.buildkite)/|^(?:go|tools)/ci/")


def open_cards(data: dict, job_id: str, waits: list[str], cards: dict | None) -> list[str]:
    """His open cards holding a job's work from landing, since work does not land with a question of his on it unanswered: each card its PR or job waits on, and each card of an open question, any job's on an order it serves, whose `Holds:` line names a change it works on there, the design it builds included; none on an open incident, whose repair lands on the recommended option, or an immediate fix, which lands while he picks the proper fix."""
    job = data["jobs"][job_id]
    if state.IMMEDIATE_FIX in job["parts"] or state.open_incidents(data, [job["order"], *job["carries"]]):
        return []
    waited = [wait for wait in view.holding(data, list(dict.fromkeys([*waits, *job["waits"]])), cards) if state.card_wait(data, wait)]
    asked = [
        drawn
        for order_id in [job["order"], *job["carries"]]
        for asker in data["orders"][order_id]["jobs"]
        for question in view.open_questions(data["jobs"][asker])
        if (drawn := question["card"] or question["waits"]) and state.names_part(question["holds"], state.worked(data, job_id, order_id))
    ]
    return list(dict.fromkeys([*waited, *asked]))


def ship_held(data: dict, job_id: str, cards: dict | None) -> list[str]:
    """His open cards holding a job's PR from going up, which the `bash_guard` hook refuses the `ccx vcs ship` that would open it by, on this Mac and through `drive job held` on a Sprite: none once the job has an open PR, which a ship pushes to rather than opens."""
    if any(data["prs"].get(state.pr_key(url), {}).get("state") == "open" for url in data["jobs"][job_id]["prs"]):
        return []
    return open_cards(data, job_id, [], cards)

def approved(pr: dict) -> bool:
    return (pr["tap"] or {}).get("label") == "Approve"


def stored(paths: list[str]) -> list[str]:
    """The paths that change how data is stored, a migration, schema or wire format, which no pure port carries."""
    return [path for path in paths if STORED_DATA.search(path)]


def unexempt(data: dict, key: str) -> str | None:
    """Why a PR landing by its exempt class rather than his Approve is not in that class, by its changed files, title and body on GitHub now, by his ruling that the landing tool checks the PR really is in the exempt class before it lands; None when it is, or he approved it."""
    pr = data["prs"][key]
    if approved(pr):
        return None
    read = served.change(key)
    paths = [path for path, _ in served.files(read["diff"])]
    match pr["exempt"]:
        case "pure port":
            outside = stored(paths)
            why = f"it changes {', '.join(outside)}, how its data is stored, which a port models anew for the target tree on his card" if outside else ""
        case "test-only" | "flake fix":
            outside = [path for path in paths if not served.TESTS.search(path)]
            why = f"it changes {', '.join(outside)}, outside test and fixture code" if outside else ""
        case "ci-only":
            outside = [path for path in paths if not CI_FILES.search(path)]
            why = f"it changes {', '.join(outside)}, outside CI configuration and CI tooling" if outside else ""
        case "incident fix":
            why = "its title or body says it reverts a change, which lands only on his tap" if REVERT.search(f"{read['title']}\n{read['body']}") else ""
    if not why:
        return None
    return f'{key} is no "{pr["exempt"]}": {why}; it lands on his Approve once drive pr set {state.pr_url(key)} --no-exempt --tap puts it on his card'


def running(pr: dict) -> bool:
    lander = pr["lander"]
    return bool(lander) and lander["outcome"] is None and state.alive(lander["pid"])


def reported(key: str, pr: dict) -> bool:
    """Whether the lander merges a yasyf/* PR itself and says so on its own line, so GitHub's merged line asks no step: it still runs on it, or it merged it."""
    return state.merges_untapped(key) and (running(pr) or ((pr["lander"] or {}).get("outcome") or "").startswith("merged"))


def landing(pr: dict) -> bool:
    """Whether a lander holds the PR on its head: still running on it, or done with it queued for merge."""
    lander = pr["lander"] or {}
    return lander.get("head") == pr["head"] and (running(pr) or lander.get("outcome") == QUEUED)


def landing_stacks(data: dict) -> dict[str, tuple[str, ...]]:
    """Each open PR the drive tracks in a stack, mapped to the stack's open tracked PRs bottom first, as the watch last read them: the PRs his Approve lands together, joined as stack-enqueue lands them, a PR sitting on the one before it in its native GitHub stack or, at the bottom of one or in none, on the open PR whose head branch is its base."""
    opened = {key: pr for key, pr in data["prs"].items() if pr["state"] == "open"}
    by_branch = {(github.repo_of(key), pr["branch"]): key for key, pr in opened.items() if pr["branch"]}
    under: dict[str, str | None] = {}
    for key, pr in opened.items():
        native = [member for member in pr["native"] if member in opened]
        under[key] = native[native.index(key) - 1] if key in native and native.index(key) else by_branch.get((github.repo_of(key), pr["base"]))

    def depth(key: str) -> int:
        return depth(parent) + 1 if (parent := under[key]) else 0

    def bottom(key: str) -> str:
        return bottom(parent) if (parent := under[key]) else key

    stacks: dict[str, list[str]] = {}
    for key in opened:
        stacks.setdefault(bottom(key), []).append(key)
    return {key: tuple(sorted(members, key=depth)) for members in stacks.values() if len(members) > 1 or opened[members[0]]["native"] for key in members}


def together(data: dict, stacks: dict[str, tuple[str, ...]], key: str) -> list[str]:
    """The open PRs a session's stack-enqueue names with key, bottom first, which the landing_guard hook holds it to: those of the card his Approve covered it on that share its landing stack, else key alone, since stack-enqueue refuses PRs named together that no stack links, such as the sibling PRs of one order his card draws together."""
    landed = stacks.get(key, (key,))
    return [member for member in data["prs"][key].get("stack") or [key] if data["prs"][member]["state"] == "open" and member in landed]


def unready(data: dict, key: str, cards: dict) -> str | None:
    """Why a lander does not queue the PR now, the first condition it fails, or None when it lands: his Approve or an exempt class clears it, it is green and approved, or approved only on an older head, whose review `drive pr land` asks for, nothing holds it, whatever the check of its change against his words says, which his card and its owner read as a line, it is no draft, has no conflict with its base and no card of his open on its work, one it or its job waits on or a question naming a change its job builds, and no lander runs on it. A job still running on it does not hold it, since his Approve lands it at once."""
    pr = data["prs"][key]
    if not state.cleared(pr):
        return "neither his Approve nor an exempt class clears it"
    if pr["checks"] != "pass":
        return f"its checks read {pr['checks']}"
    if pr["review"] not in LANDS_ON:
        return f"its review reads {pr['review']}"
    if view.held(pr):
        return "his Hold or its hold label holds it"
    if waiting := view.holding(data, pr["waits"], cards):
        return f"it waits on {', '.join(waiting)}"
    if pr["draft"]:
        return "it is a draft"
    if pr["conflict"]:
        return "it conflicts with its base"
    if asking := open_cards(data, pr["job"], pr["waits"], cards):
        return f"his cards {', '.join(asking)} on its work are open"
    if running(pr):
        return f"lander pid {pr['lander']['pid']} runs on it"
    return None


def unit(data: dict, stacks: dict[str, tuple[str, ...]], key: str, cards: dict) -> tuple[str, ...]:
    """The PRs one lander queues for key: its whole landing stack bottom first once every PR of it lands now, else none, since a stack lands whole, its bottom waiting for its top, as stack-enqueue lands it."""
    stack = stacks.get(key, (key,))
    return stack if all(unready(data, member, cards) is None for member in stack) else ()


def stall(data: dict, key: str, cards: dict) -> str | None:
    """Why his Approve has not landed the PR, or None while a lander holds it or one below it in its landing stack: the first PR of that stack, bottom first, that does not land now and why, since the stack lands whole, else what the last lander's try on its head came to."""
    stack = landing_stacks(data).get(key, (key,))
    if landing(data["prs"][key]) or any(running(data["prs"][member]) for member in stack[: stack.index(key)]):
        return None
    for member in stack:
        if why := unready(data, member, cards):
            return why if member == key else f"{member} {'below' if stack.index(member) < stack.index(key) else 'above'} it in its stack: {why}"
    lander = data["prs"][key]["lander"] or {}
    if lander.get("head") == data["prs"][key]["head"] and lander.get("outcome"):
        return f"its lander's try at {lander['head'][:12]} ended {lander['outcome']}"
    return "no lander has started on it"


def tried(data: dict, pr: dict) -> bool:
    """Whether a lander tried the PR's head and nothing it waited on has moved since: one that found PRs below it neither his Approve nor an exempt class clears tries again once each of them is cleared or merges, and one a newer install's save ended before it queued anything tries again on that install."""
    lander = pr["lander"] or {}
    if lander.get("head") != pr["head"] or (lander.get("outcome") or "").startswith(NEWER_INSTALL):
        return False
    return not lander.get("below") or not all(key in data["prs"] and (state.cleared(data["prs"][key]) or data["prs"][key]["state"] == "merged") for key in lander["below"])


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
    """The units a lander queues now among keys' landing stacks: each one with a PR on a head no lander has tried."""
    stacks = landing_stacks(data)
    found = dict.fromkeys(unit(data, stacks, key, cards) for key in keys if state.queued_by_lander(key) and data["prs"][key]["state"] == "open")
    return [members for members in found if any(not tried(data, data["prs"][member]) for member in members)]


def start(name: str, keys: list[str], cards: dict | None) -> list[tuple[str, ...]]:
    """Start a lander on each unit his Approve or exempt classes clear among keys that is ready, marking its PRs under the drive's lock so no second lander starts on their heads; the units started. With his cards unread, none, since a card of his may hold one."""
    if cards is None or not units(state.load(name), keys, cards):
        return []
    with state.edit(name) as data:
        started = units(data, keys, cards)
        for members in started:
            mark(data, members, spawn(name, members))
    return started


def merge_ready(name: str, key: str, read: dict) -> str:
    """Start the lander on a yasyf/* PR a builder's taken READY or PUSHED names, which merges it with --admin past its checks and installs what it changes, unless a lander already runs on it or it may not merge yet; what was started, or why not."""
    why = unmergeable(key, read)
    cards = view.live_cards(name)
    with state.edit(name) as data:
        pr = data["prs"][key]
        if why is None and (asking := open_cards(data, pr["job"], pr["waits"], cards)):
            why = f"his questions on its work are unanswered, {', '.join(asking)}; its builder reports again once he answers each"
        if why is None and running(pr):
            why = f"lander pid {pr['lander']['pid']} already merges it"
        if why is None:
            mark(data, (key,), spawn(name, (key,)), read["headRefOid"])
    if why:
        return f"{NOT_MERGING}: {why}"
    return f"merging it with gh pr merge --squash --admin, then installing each plugin it changes; the outcome reaches its job as a PR line, its log {state.drive_dir(name) / LOG}"


def follow(name: str, keys: list[str]) -> list[str]:
    """Start the lander on each yasyf/* PR among keys whose head moved past the one its last lander tried, by its builder's push, marking it under the drive's lock so no second lander starts on that head; the PRs started."""
    prs = state.load(name)["prs"]
    if not any(moved(key, prs[key]) for key in keys):
        return []
    with state.edit(name) as data:
        started = [key for key in keys if moved(key, data["prs"][key])]
        for key in started:
            mark(data, (key,), spawn(name, (key,)))
    return started


def moved(key: str, pr: dict) -> bool:
    """Whether a yasyf/* PR a lander tried has a head GitHub read since that the lander has not, open, ready, unheld and free of conflicts with no lander running on it."""
    lander = pr["lander"]
    return (
        state.merges_untapped(key)
        and bool(lander and pr["head"])
        and lander["head"] != pr["head"]
        and pr["state"] == "open"
        and not (pr["draft"] or pr["hold_label"] or pr["conflict"] or running(pr))
    )


def unmergeable(key: str, read: dict) -> str | None:
    """Why a yasyf/* PR may not merge yet: it is not open, is a draft, carries the hold label, or sits on another branch than its repository's default."""
    if read["state"] != "OPEN":
        return f"it is {read['state'].lower()}"
    if read["isDraft"]:
        return "it is a draft"
    if github.HOLD_LABEL in [label["name"] for label in read["labels"]]:
        return "it carries the hold label"
    if read["baseRefName"] != (default := github.default_branch(github.repo_of(key))):
        return f"it sits on {read['baseRefName']}, not {default}; register it again once the PR below it merges"
    return None


def mark(data: dict, keys: tuple[str, ...], pid: int, head: str | None = None) -> None:
    at = state.stamp()
    for key in keys:
        data["prs"][key]["lander"] = {"head": head or data["prs"][key]["head"], "pid": pid, "at": at, "outcome": None}


def spawn(name: str, keys: tuple[str, ...]) -> int:
    """Run `drive pr land` on the keys as its own process, outliving the watch pass or card tap that started it, its output appended to the drive's landing log."""
    return state.detach(name, ["pr", "land", *map(state.pr_url, keys)], LOG)


def run(name: str, keys: list[str], dry_run: bool = False) -> str:
    """Land one unit his Approve or exempt classes clear, or one yasyf/* PR, never one someone else opened that no order of his it serves names, then record what came of it on each PR and as a landing event, which the watch says on the PR's line: a failure wakes its job, a queued one does not. His Approve lands the PR whatever the check of its change against his words says, which only informs his card and its owner; a unit with a PR below it that nothing clears is not queued, since stack-enqueue would queue that PR too, nor one with a PR its changes put outside its exempt class. A dry run runs stack-enqueue --check as the landing would, says what it would do, and queues and records nothing."""
    data = state.load(name)
    if refused := [key for key in keys if not (state.merges_untapped(key) or state.queued_by_lander(key) and state.cleared(data["prs"][key]))]:
        raise state.DriveError(f"{', '.join(refused)}: the drive lands a yasyf/* PR, or a Forge-AI/monorepo PR he approved or an exempt class lands, and no other")
    if foreign := [why for key in keys if (why := state.foreign_pr(data, key))]:
        raise state.DriveError("; ".join(foreign))
    below: list[str] = []
    if state.merges_untapped(keys[0]):
        outcome = "would merge it with gh pr merge --squash --admin, then install each plugin it changes" if dry_run else merged(name, keys[0])
    else:
        try:
            below = uncleared_below(data, keys)
            misfiled = "; ".join(why for key in keys if (why := unexempt(data, key)))
        except (github.GithubError, state.DriveError) as error:
            outcome = f"not landed: {cut(str(error), SAID_CHARS)}"
        else:
            if misfiled:
                outcome = f"not landed: {misfiled}"
            elif below:
                outcome = held_below(keys, below)
            else:
                outcome = enqueued(name, Path(data["roles"]["coordinator"]["cwd"]), keys, dry_run)
    if dry_run:
        return outcome
    return record(name, keys, outcome, below)


def record(name: str, keys: list[str], outcome: str, below: list[str]) -> str:
    """Record what came of a landing on each PR and as a landing event, on the newest install, which `drive pr land --outcome` records it on when one landed while this lander waited on the queue or an install."""
    install.reopen(["pr", "land", *map(state.pr_url, keys), "--drive", name, "--outcome", outcome, *(flag for key in below for flag in ("--below", key))])
    with state.edit(name) as data:
        for key in keys:
            pr = data["prs"][key]
            pr["lander"] = (pr["lander"] or {"head": pr["head"], "pid": os.getpid(), "at": state.stamp()}) | {"outcome": outcome, "below": below}
            repo, number = key.split("#")
            state.append_event(name, "landing", repo=repo, number=int(number), change=outcome, lane=state.heard_lane(data, pr["order"]))
    return outcome


def said(output: str) -> str:
    return cut("; ".join([line for line in output.splitlines() if line.strip()][-SAID_LINES:]), SAID_CHARS)


def enqueued(name: str, coordinator: Path, keys: list[str], dry_run: bool = False) -> str:
    """Queue the unit through stack-enqueue as the head of its repository's default branch holds it, run from a fresh() worktree of this Mac's landing clone and never from the coordinator's checkout: --check first, whose BLOCKED lines end the landing, unless each gap is an approval on an older head, which stack-enqueue names only once ai-review finished on the head, or a wait to retarget onto the default branch once the PR under it lands; a dry run stops there, saying what it would queue; then forge-pr-reviewer is asked to review each head approved only on an older one, once, and --watch waits until Trunk holds every PR of it or one needs a fix rather than a wait; what came of it. The lander lets go of its install's lock before it makes its worktree, which may wait on the landing clone's lock and the first landing's clone of the repository, and holds none through --check, which reads GitHub for every PR of the unit, or --watch, which may wait an hour and a half on Trunk; a drive a newer install saved meanwhile ends the landing unlanded, for the next landing on that install."""
    repo = github.repo_of(keys[0])
    numbers = [key.split("#")[1] for key in keys]
    try:
        branch = github.default_branch(repo)
        state.let_go_install()
        with fresh(name, coordinator, repo, branch) as (checkout, sha):
            script = str(checkout / ENQUEUE)
            print(f"{', '.join(keys)}: stack-enqueue from {checkout}, a fresh worktree of {repo}'s {branch} at {sha}", flush=True)
            try:
                checked = subprocess.run([script, "--check", *numbers], cwd=checkout, capture_output=True, text=True)
            except OSError as error:
                return f"not landed: {script} did not start: {error}"
            print(checked.stdout + checked.stderr, flush=True)
            blocked = list(BLOCKED_LINE.finditer(checked.stdout))
            if any(not (OLDER_HEAD.fullmatch(gap) or RETARGETS.fullmatch(gap)) for line in blocked for gap in line["gaps"].split("; ")):
                return f"not landed: {cut('; '.join(line[0] for line in blocked), SAID_CHARS)}; its whole output is in {state.drive_dir(name) / LOG}"
            if dry_run:
                return f"would queue {', '.join(keys)} through stack-enqueue --watch from {branch} at {sha[:12]}: every PR below it is merged, approved by him or exempt"
            try:
                refused = ask_review(name, repo, {line["number"]: line["head"] for line in blocked if OLDER_HEAD.search(line["gaps"])})
            except state.NewerDrive as error:
                return f"{NEWER_INSTALL}: {cut(str(error), SAID_CHARS)}"
            if refused:
                return refused
            done = subprocess.run([script, "--watch", *numbers], cwd=checkout, capture_output=True, text=True)
    except github.GithubError as error:
        return f"not landed: no fresh worktree of {repo}'s default branch to run stack-enqueue from: {cut(str(error), SAID_CHARS)}"
    print(done.stdout + done.stderr, flush=True)
    if done.returncode == 0:
        return QUEUED
    return f"not landed: stack-enqueue --watch exited {done.returncode}: {said(done.stdout + done.stderr)}; its whole output is in {state.drive_dir(name) / LOG}"


@contextmanager
def fresh(name: str, coordinator: Path, repo: str, branch: str) -> Iterator[tuple[Path, str]]:
    """A worktree of repo at the head of branch as origin holds it now, made for one landing in the drive's landing folder and removed once it ends: its path and that head's sha. It comes from the one clone of repo this Mac keeps for landings, which holds every object with no blob filter, so neither its checkout nor stack-enqueue's merge checks ask origin for an object by its id, as a checkout of a partial clone such as the coordinator's does and as GitHub may answer without the object: the first landing clones it from the origin the coordinator's checkout names, made whole before it takes the clone's place, and each fetches branch into it by name, both under the clone's lock, since landings run at once and each moves its one ref. Its LFS files check out as their pointers, since no landing step reads one and fetching it would ask GitHub's LFS store by id. A landing runs the repository's scripts, and the toolchain its mise.toml pins for them, as that head holds them, never as the coordinator's checkout holds them at whatever commit and edits it carries, which it leaves untouched. Every core checks it out at once, with no hook, since the monorepo's post-checkout hook warms node_modules that no landing step reads."""
    clone = state.root() / CLONES / f"{repo}.git"
    git = ["git", "-C", str(clone)]
    with state.flocked(clone.with_name(f"{clone.name}.lock")):
        if not clone.exists():
            making = clone.with_name(f"{clone.name}.tmp")
            shutil.rmtree(making, ignore_errors=True)
            origin = github.run(["git", "-C", str(coordinator), "remote", "get-url", "origin"]).strip()
            github.run(["git", "clone", "-q", "--bare", "--no-tags", "--single-branch", "--branch", branch, *LFS_POINTERS, origin, str(making)], timeout=CHECKOUT_SECONDS)
            making.rename(clone)
        sha = github.fetched(str(clone), branch)
    checkout = state.drive_dir(name) / LANDINGS / str(os.getpid())
    github.run([*git, "-c", "core.hooksPath=/dev/null", "-c", "checkout.workers=0", "worktree", "add", "-q", "--detach", str(checkout), sha], timeout=CHECKOUT_SECONDS)
    try:
        yield checkout, sha
    finally:
        subprocess.run([*git, "worktree", "remove", "--force", str(checkout)])


def ask_review(name: str, repo: str, heads: dict[str, str]) -> str | None:
    """Post the comment ai-review names for a head it will not review on its own on each PR whose head, as stack-enqueue names it, was not asked before, and record that head on the PR so none is asked twice; why not, when GitHub refused one."""
    asked = state.load(name)["prs"]
    for number, head in heads.items():
        if asked[f"{repo}#{number}"]["review_asked"] == head:
            continue
        done = subprocess.run(["gh", "pr", "comment", number, "-R", repo, "--body", REVIEW_ASK], capture_output=True, text=True)
        if done.returncode:
            return f"not landed: #{number} waits on a review of head {head}, and gh pr comment refused {REVIEW_ASK!r}: {cut(' '.join(done.stderr.split()), SAID_CHARS)}"
        print(f"#{number} asked forge-pr-reviewer to review head {head}: {done.stdout.strip()}", flush=True)
        with state.edit(name) as data:
            data["prs"][f"{repo}#{number}"]["review_asked"] = head
    return None


def merged(name: str, key: str) -> str:
    """Merge a yasyf/* PR with --admin past its checks, by his rule for his own repositories, with gh itself since ccx's GitHub gateway refuses a merge, record its merge commit on its row, then install from it each plugin of this Mac it changes, having let go of its install's lock, which an install of long-running would wait on; what came of it. GitHub refuses a merge whose base moved while it merged, which another merge into that base did, so the lander merges again on the new base, and any other refusal is not landed."""
    repo, number = key.split("#")
    while (done := subprocess.run(["gh", "pr", "merge", number, "-R", repo, "--squash", "--admin"], capture_output=True, text=True)).returncode and BASE_MOVED in done.stderr:
        print(f"{key}: {' '.join(done.stderr.split())}; merging it again on its new base", flush=True)
    if done.returncode:
        return f"not landed: gh pr merge --squash --admin refused it: {cut(' '.join(done.stderr.split()), SAID_CHARS)}"
    try:
        sha = github.run(["gh", "pr", "view", number, "-R", repo, "--json", "mergeCommit", "-q", ".mergeCommit.oid"]).strip()
        files = github.run(["gh", "api", "--paginate", f"repos/{repo}/pulls/{number}/files", "--jq", ".[].filename"]).splitlines()
    except github.GithubError as error:
        return f"merged, but its merge commit did not read, so nothing was installed: {cut(str(error), SAID_CHARS)}"
    with state.edit(name) as data:
        data["prs"][key]["merged_sha"] = sha
    state.let_go_install()
    plugins = install.touched(repo, files)
    if not plugins:
        return f"merged at {sha[:12]}; it changes no plugin installed on this Mac"
    lines = [f"merged at {sha[:12]}"]
    for plugin in plugins:
        done = subprocess.run([str(DRIVE), "install", plugin, "--sha", sha, "--drive", name], capture_output=True, text=True)
        print(done.stdout + done.stderr, flush=True)
        lines.append(done.stdout.splitlines()[0] if done.returncode == 0 else f"but drive install {plugin} --sha {sha[:12]} failed: {said(done.stderr)}")
    return "; ".join(lines)
