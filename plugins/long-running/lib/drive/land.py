import os
import re
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
CHECKOUT_SECONDS = 600
SAID_CHARS = 400
SAID_LINES = 4
QUEUED = "queued for merge"
BASE_MOVED = "Base branch was modified"
NOT_MERGING = "not merging it"
MERGE = ("gh", "pr", "merge", "--squash")
NEWER_INSTALL = "not landed: a newer long-running saved the drive while stack-enqueue --check ran, so the landing runs again on it"
NO_WORKTREE = "not landed: no fresh worktree"
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
    """Whether the lander merges the PR itself with gh pr merge, as it does every PR but a Forge-AI/monorepo one Trunk merges, and says so on its own line, so GitHub's merged line asks no step: it still runs on it, or it merged it."""
    return not state.queued_by_lander(key) and (running(pr) or ((pr["lander"] or {}).get("outcome") or "").startswith("merged"))


def landing(pr: dict) -> bool:
    """Whether a lander holds the PR on its head: still running on it, or done with it queued for merge and not ejected by Trunk since."""
    lander = pr["lander"] or {}
    return lander.get("head") == pr["head"] and (running(pr) or lander.get("outcome") == QUEUED and not pr["ejected"])


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
    """Why a lander does not queue the PR now, its stage when that is not ready, or None when it lands."""
    from drive import stage

    now = stage.of(data, key, cards)
    return None if now.name == "ready" else f"{now.name}: {now.why}"


def unit(data: dict, stacks: dict[str, tuple[str, ...]], key: str, cards: dict) -> tuple[str, ...]:
    """The PRs one lander queues for key: its whole landing stack bottom first once every PR of it lands now, else none, since a stack lands whole, its bottom waiting for its top, as stack-enqueue lands it."""
    stack = stacks.get(key, (key,))
    return stack if all(unready(data, member, cards) is None for member in stack) else ()


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
    """The units a lander lands now among keys' landing stacks, in every repository the drive tracks: each one whose PRs read ready from its bottom, whole for a Forge-AI/monorepo stack stack-enqueue queues, and its bottom PR alone in any other repository, which gh pr merge merges one PR at a time."""
    stacks = landing_stacks(data)
    found = dict.fromkeys(unit(data, stacks, key, cards) for key in keys if data["prs"][key]["state"] == "open")
    return [members if state.queued_by_lander(members[0]) else members[:1] for members in found if members]


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
    return f"merging it with {' '.join(MERGE)} --admin, then installing each plugin it changes; the outcome reaches its job as a PR line, its log {state.drive_dir(name) / LOG}"


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
    """Mark the PRs a lander starts on, each keeping what the try before it on the same head came to, so record() says an outcome the same head already said only once."""
    at = state.stamp()
    for key in keys:
        pr = data["prs"][key]
        before = pr["lander"] or {}
        tries = head or pr["head"]
        pr["lander"] = {"head": tries, "pid": pid, "at": at, "outcome": None, "was": before.get("outcome") if before.get("head") == tries else None}


def spawn(name: str, keys: tuple[str, ...]) -> int:
    """Run `drive pr land` on the keys as its own process, outliving the watch pass or card tap that started it, its output appended to the drive's landing log."""
    return state.detach(name, ["pr", "land", *map(state.pr_url, keys)], LOG)


def run(name: str, keys: list[str], dry_run: bool = False) -> str:
    """Land one unit his Approve or exempt classes clear, or one yasyf/* PR, in whichever repository the drive tracks it, never a PR the drive does not track or one someone else opened that no order of his it serves names, then record what came of it on each PR, which its stage reads, and as a landing event: a yasyf/* PR merges with gh pr merge --admin past its checks, a Forge-AI/monorepo unit queues through stack-enqueue, and a PR of any other repository merges with gh pr merge at the head its stage read ready, which GitHub merges only once its repository's rules pass. His Approve lands the PR whatever the check of its change against his words says, which only informs his card and its owner; a unit with a PR below it that nothing clears is not landed, since its landing would land that PR too, nor one with a PR its changes put outside its exempt class. A dry run runs stack-enqueue --check as the landing would, says what it would do, and lands and records nothing."""
    data = state.load(name)
    if untracked := [key for key in keys if key not in data["prs"]]:
        raise state.DriveError(f"{', '.join(untracked)}: the drive tracks no such PR, so it lands none; drive pr add <url> --job <job> registers one first")
    if refused := [key for key in keys if not (state.merges_untapped(key) or state.cleared(data["prs"][key]))]:
        raise state.DriveError(f"{', '.join(refused)}: the drive lands a yasyf/* PR, or a PR he approved or an exempt class lands, and no other")
    if foreign := [why for key in keys if (why := state.foreign_pr(data, key))]:
        raise state.DriveError("; ".join(foreign))
    below: list[str] = []
    if state.merges_untapped(keys[0]):
        outcome = f"would merge it with {' '.join(MERGE)} --admin, then install each plugin it changes" if dry_run else merged(name, keys[0], ["--admin"])
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
            elif state.queued_by_lander(keys[0]):
                outcome = enqueued(name, Path(data["roles"]["coordinator"]["cwd"]), keys, dry_run)
            else:
                head = data["prs"][keys[0]]["head"]
                outcome = f"would merge it with {' '.join(MERGE)} --match-head-commit {head[:12]}" if dry_run else merged(name, keys[0], ["--match-head-commit", head])
    if dry_run:
        return outcome
    return record(name, keys, outcome, below)


def record(name: str, keys: list[str], outcome: str, below: list[str]) -> str:
    """Record what came of a landing on each PR, and as a landing event unless the try before it on the same head came to the same, such as a retried landing that again could make no worktree; on the newest install, which `drive pr land --outcome` records it on when one landed while this lander waited on the queue or an install."""
    install.reopen(["pr", "land", *map(state.pr_url, keys), "--drive", name, "--outcome", outcome, *(flag for key in below for flag in ("--below", key))])
    with state.edit(name) as data:
        for key in keys:
            pr = data["prs"][key]
            pr["lander"] = (pr["lander"] or {"head": pr["head"], "pid": os.getpid(), "at": state.stamp()}) | {"outcome": outcome, "below": below}
            if pr["lander"].get("was") == outcome:
                continue
            repo, number = key.split("#")
            state.append_event(name, "landing", repo=repo, number=int(number), change=outcome, lane=state.heard_lane(data, pr["order"]))
    return outcome


def said(output: str) -> str:
    return cut("; ".join([line for line in output.splitlines() if line.strip()][-SAID_LINES:]), SAID_CHARS)


def enqueued(name: str, clone: Path, keys: list[str], dry_run: bool = False) -> str:
    """Queue the unit through stack-enqueue as the head of its repository's default branch holds it, run from a fresh() worktree of the coordinator checkout's clone and never from that checkout: --check first, whose BLOCKED lines end the landing, unless each gap is an approval on an older head, which stack-enqueue names only once ai-review finished on the head, or a wait to retarget onto the default branch once the PR under it lands; a dry run stops there, saying what it would queue; then forge-pr-reviewer is asked to review each head approved only on an older one, once, and --watch waits until Trunk holds every PR of it or one needs a fix rather than a wait; what came of it. The lander lets go of its install's lock before --check, which reads GitHub for every PR of the unit, and holds none through --watch, which may wait an hour and a half on Trunk; a drive a newer install saved meanwhile ends the landing unlanded, for the next landing on that install."""
    repo = github.repo_of(keys[0])
    numbers = [key.split("#")[1] for key in keys]
    try:
        branch = github.default_branch(repo)
        with fresh(name, clone, branch) as (checkout, sha):
            script = str(checkout / ENQUEUE)
            print(f"{', '.join(keys)}: stack-enqueue from {checkout}, a fresh worktree of {repo}'s {branch} at {sha}", flush=True)
            state.let_go_install()
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
        return f"{NO_WORKTREE} of {repo}'s default branch to run stack-enqueue from: {cut(str(error), SAID_CHARS)}"
    print(done.stdout + done.stderr, flush=True)
    if done.returncode == 0:
        return QUEUED
    return f"not landed: stack-enqueue --watch exited {done.returncode}: {said(done.stdout + done.stderr)}; its whole output is in {state.drive_dir(name) / LOG}"


@contextmanager
def fresh(name: str, clone: Path, branch: str) -> Iterator[tuple[Path, str]]:
    """A worktree of the clone's repository at the head of branch as origin holds it now, made for one landing in the drive's landing folder and removed once it ends: its path and that head's sha. A landing runs the repository's scripts, and the toolchain its mise.toml pins for them, as that head holds them, never as the coordinator's checkout holds them at whatever commit and edits it carries, which it leaves untouched. Every core checks it out at once, with no hook, since the monorepo's post-checkout hook warms node_modules that no landing step reads."""
    git = ["git", "-C", str(clone)]
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


def merged(name: str, key: str, flags: list[str]) -> str:
    """Merge a PR with gh pr merge and the flags given, --admin past its checks for a yasyf/* one by his rule for his own repositories and --match-head-commit for any other, which GitHub merges only once its repository's rules pass, with gh itself since ccx's GitHub gateway refuses a merge, record its merge commit on its row, then install from it each plugin of this Mac it changes, having let go of its install's lock, which an install of long-running would wait on; what came of it. GitHub refuses a merge whose base moved while it merged, which another merge into that base did, so the lander merges again on the new base, and any other refusal is not landed."""
    repo, number = key.split("#")
    command = ["gh", "pr", "merge", number, "-R", repo, "--squash", *flags]
    while (done := subprocess.run(command, capture_output=True, text=True)).returncode and BASE_MOVED in done.stderr:
        print(f"{key}: {' '.join(done.stderr.split())}; merging it again on its new base", flush=True)
    if done.returncode:
        return f"not landed: {' '.join([*MERGE, *flags])} refused it: {cut(' '.join(done.stderr.split()), SAID_CHARS)}"
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
