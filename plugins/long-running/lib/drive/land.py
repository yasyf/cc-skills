import os
import re
import subprocess
from itertools import takewhile
from pathlib import Path

from drive import github, install, served, state, view
from drive.render import cut

ENQUEUE = Path(".agents/skills/submit-pr/scripts/stack-enqueue")
DRIVE = Path(__file__).resolve().parents[2] / "bin" / "drive"
LOG = "landings.log"
SAID_CHARS = 400
SAID_LINES = 4
QUEUED = "queued for merge"
NOT_MERGING = "not merging it"
LANDS_ON = ("approved", "stale")
REVIEW_ASK = "@forge-pr-reviewer review"
BLOCKED_LINE = re.compile(r"^#(?P<number>\d+) BLOCKED (?P<head>\w+) (?P<gaps>.+)$", re.MULTILINE)
OLDER_HEAD = re.compile(r"awaiting approval on head from \S+ \(approved [^,]+, (?:on an older head|dismissed by a push)\)")
REVERT = re.compile(r"^Reverts?\s|⏪|\bThis reverts\b", re.MULTILINE)
STORED_DATA = re.compile(r"(?:^|/)migrations?/|(?:^|/)[^/]*schema[^/]*$|\.(?:sql|proto)$", re.IGNORECASE)
CI_FILES = re.compile(r"^(?:\.github|\.buildkite)/|^(?:go|tools)/ci/")


def open_cards(data: dict, pr: dict, cards: dict | None) -> list[str]:
    """His open cards a drive PR or its job waits on, outside an open incident, whose repair lands on the recommended option, and outside an immediate fix, which lands while he picks the proper fix."""
    job = data["jobs"][pr["job"]]
    if state.IMMEDIATE_FIX in job["parts"] or state.open_incidents(data, [job["order"], *job["carries"]]):
        return []
    waits = list(dict.fromkeys([*pr["waits"], *job["waits"]]))
    return [wait for wait in view.holding(data, waits, cards) if wait not in data["jobs"] and not state.RELEASE_WAIT.fullmatch(wait)]


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


def unit(data: dict, key: str) -> tuple[str, ...]:
    """The PRs one lander queues with key, bottom first: the open PRs of the stack his card drew it in, else key alone, up to the first neither his Approve nor an exempt class clears."""
    members = [member for member in data["prs"][key].get("stack") or [key] if data["prs"][member]["state"] == "open"]
    return tuple(takewhile(lambda member: state.cleared(data["prs"][member]), members))


def ready(data: dict, keys: tuple[str, ...], cards: dict) -> bool:
    """Whether a unit is queued now: each PR of it green and approved, or approved only on an older head, whose review `drive pr land` asks for, with nothing holding it, no draft, no conflict with its base, no card of his open on it or its job and no lander running on it, and one of them on a head no lander has tried. A job still running on it does not hold it, since his Approve lands it at once."""
    prs = [data["prs"][key] for key in keys]
    fit = all(
        view.unblocked(pr, data, cards, LANDS_ON)
        and not pr["draft"]
        and not pr["conflict"]
        and not open_cards(data, pr, cards)
        and not view.open_cards(data["jobs"][pr["job"]], cards)
        and not running(pr)
        for pr in prs
    )
    return fit and any(not tried(data, pr) for pr in prs)


def tried(data: dict, pr: dict) -> bool:
    """Whether a lander tried the PR's head and nothing it waited on has moved since: one that found PRs below it neither his Approve nor an exempt class clears tries again once each of them is cleared or merges."""
    lander = pr["lander"] or {}
    if lander.get("head") != pr["head"]:
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
    found = dict.fromkeys(unit(data, key) for key in keys if state.queued_by_lander(key) and data["prs"][key]["state"] == "open")
    return [members for members in found if members and ready(data, members, cards)]


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
    """Start the lander on a yasyf/* PR its builder registers, which merges it with --admin past its checks and installs what it changes, unless a lander already runs on it or it may not merge yet; what was started, or why not."""
    why = unmergeable(key, read)
    with state.edit(name) as data:
        pr = data["prs"][key]
        if why is None and running(pr):
            why = f"lander pid {pr['lander']['pid']} already merges it"
        if why is None:
            mark(data, (key,), spawn(name, (key,)), read["headRefOid"])
    if why:
        return f"{NOT_MERGING}: {why}"
    return f"merging it with gh pr merge --squash --admin, then installing each plugin it changes; the outcome reaches its job as a PR line, its log {state.drive_dir(name) / LOG}"


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
    env = {key: value for key, value in os.environ.items() if key != "CLAUDE_CODE_SESSION_ID"}
    with (state.drive_dir(name) / LOG).open("a") as log:
        return subprocess.Popen(
            [str(DRIVE), "pr", "land", *map(state.pr_url, keys), "--drive", name],
            cwd=state.drive_dir(name),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        ).pid


def run(name: str, keys: list[str], dry_run: bool = False) -> str:
    """Land one unit his Approve or exempt classes clear, or one yasyf/* PR, never one someone else opened that no order of his it serves names, then record what came of it on each PR and as a landing event, which the watch says on the PR's line: a failure wakes its job, a queued one does not. A unit with a PR below it that nothing clears is not queued, since stack-enqueue would queue that PR too, nor one with a PR its changes put outside its exempt class. A dry run says what it would do and records nothing."""
    data = state.load(name)
    if refused := [key for key in keys if not (state.merges_untapped(key) or state.queued_by_lander(key) and state.cleared(data["prs"][key]))]:
        raise state.DriveError(f"{', '.join(refused)}: the drive lands a yasyf/* PR, or a Forge-AI/monorepo PR he approved or an exempt class lands, and no other")
    if foreign := [why for key in keys if (why := state.foreign_pr(data, key))]:
        raise state.DriveError("; ".join(foreign))
    below: list[str] = []
    if state.merges_untapped(keys[0]):
        outcome = "would merge it with gh pr merge --squash --admin, then install each plugin it changes" if dry_run else merged(keys[0])
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
            elif dry_run:
                outcome = f"would queue {', '.join(keys)} through stack-enqueue --watch: every PR below it is merged, approved by him or exempt"
            else:
                outcome = enqueued(name, Path(data["roles"]["coordinator"]["cwd"]), keys)
    if dry_run:
        return outcome
    return record(name, keys, outcome, below)


def record(name: str, keys: list[str], outcome: str, below: list[str]) -> str:
    """Record what came of a landing on each PR and as a landing event; when a newer install saved the drive while this lander waited on the queue or an install, hand the record to that install's `drive pr land --outcome`, since this code may misread the drive."""
    try:
        with state.edit(name) as data:
            for key in keys:
                pr = data["prs"][key]
                pr["lander"] = (pr["lander"] or {"head": pr["head"], "pid": os.getpid(), "at": state.stamp()}) | {"outcome": outcome, "below": below}
                repo, number = key.split("#")
                state.append_event(name, "landing", repo=repo, number=int(number), change=outcome, lane=state.heard_lane(data, pr["order"]))
    except state.NewerDrive:
        if not (newer := install.upgraded()):
            raise
        os.execv(newer, [str(newer), "pr", "land", *map(state.pr_url, keys), "--drive", name, "--outcome", outcome, *(flag for key in below for flag in ("--below", key))])
    return outcome


def said(output: str) -> str:
    return cut("; ".join([line for line in output.splitlines() if line.strip()][-SAID_LINES:]), SAID_CHARS)


def enqueued(name: str, checkout: Path, keys: list[str]) -> str:
    """Queue the unit through the coordinator checkout's stack-enqueue: --check first, whose BLOCKED lines end the landing, unless each gap is an approval on an older head, which stack-enqueue names only once ai-review finished on the head; then forge-pr-reviewer is asked to review each such head once, and --watch waits until Trunk holds every PR of it or one needs a fix rather than a wait; what came of it."""
    script = str(checkout / ENQUEUE)
    numbers = [key.split("#")[1] for key in keys]
    try:
        checked = subprocess.run([script, "--check", *numbers], cwd=checkout, capture_output=True, text=True)
    except OSError as error:
        return f"not landed: {script} did not start: {error}"
    print(checked.stdout + checked.stderr, flush=True)
    blocked = list(BLOCKED_LINE.finditer(checked.stdout))
    if any(not OLDER_HEAD.fullmatch(gap) for line in blocked for gap in line["gaps"].split("; ")):
        return f"not landed: {cut('; '.join(line[0] for line in blocked), SAID_CHARS)}; its whole output is in {state.drive_dir(name) / LOG}"
    if refused := ask_review(name, github.repo_of(keys[0]), {line["number"]: line["head"] for line in blocked}):
        return refused
    done = subprocess.run([script, "--watch", *numbers], cwd=checkout, capture_output=True, text=True)
    print(done.stdout + done.stderr, flush=True)
    if done.returncode == 0:
        return QUEUED
    return f"not landed: stack-enqueue --watch exited {done.returncode}: {said(done.stdout + done.stderr)}; its whole output is in {state.drive_dir(name) / LOG}"


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


def merged(key: str) -> str:
    """Merge a yasyf/* PR with --admin past its checks, by his rule for his own repositories, with gh itself since ccx's GitHub gateway refuses a merge, then install from its merge commit each plugin of this Mac it changes; what came of it."""
    repo, number = key.split("#")
    done = subprocess.run(["gh", "pr", "merge", number, "-R", repo, "--squash", "--admin"], capture_output=True, text=True)
    if done.returncode:
        return f"not landed: gh pr merge --squash --admin refused it: {cut(' '.join(done.stderr.split()), SAID_CHARS)}"
    try:
        sha = github.run(["gh", "pr", "view", number, "-R", repo, "--json", "mergeCommit", "-q", ".mergeCommit.oid"]).strip()
        files = github.run(["gh", "api", "--paginate", f"repos/{repo}/pulls/{number}/files", "--jq", ".[].filename"]).splitlines()
    except github.GithubError as error:
        return f"merged, but its merge commit did not read, so nothing was installed: {cut(str(error), SAID_CHARS)}"
    plugins = install.touched(repo, files)
    if not plugins:
        return f"merged at {sha[:12]}; it changes no plugin installed on this Mac"
    lines = [f"merged at {sha[:12]}"]
    for plugin in plugins:
        done = subprocess.run([str(DRIVE), "install", plugin, "--sha", sha], capture_output=True, text=True)
        print(done.stdout + done.stderr, flush=True)
        lines.append(done.stdout.splitlines()[0] if done.returncode == 0 else f"but drive install {plugin} --sha {sha[:12]} failed: {said(done.stderr)}")
    return "; ".join(lines)
