import os
import subprocess
from itertools import takewhile
from pathlib import Path

from drive import github, install, state, view
from drive.render import cut

ENQUEUE = Path(".agents/skills/submit-pr/scripts/stack-enqueue")
DRIVE = Path(__file__).resolve().parents[2] / "bin" / "drive"
LOG = "landings.log"
SAID_CHARS = 400
SAID_LINES = 4
QUEUED = "queued for merge"


def open_cards(data: dict, pr: dict, cards: dict | None) -> list[str]:
    """His open cards a drive PR or its job waits on, outside an open incident, whose repair lands on the recommended option, and outside an immediate fix, which lands while he picks the proper fix."""
    job = data["jobs"][pr["job"]]
    if state.IMMEDIATE_FIX in job["parts"] or state.open_incidents(data, [job["order"], *job["carries"]]):
        return []
    waits = list(dict.fromkeys([*pr["waits"], *job["waits"]]))
    return [wait for wait in view.holding(data, waits, cards) if wait not in data["jobs"] and not state.RELEASE_WAIT.fullmatch(wait)]


def approved(pr: dict) -> bool:
    return (pr["tap"] or {}).get("label") == "Approve"


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
    """The PRs one lander queues with key, bottom first: the open PRs of the stack his card drew it in, else key alone, up to the first he has not approved."""
    members = [member for member in data["prs"][key].get("stack") or [key] if data["prs"][member]["state"] == "open"]
    return tuple(takewhile(lambda member: approved(data["prs"][member]), members))


def ready(data: dict, keys: tuple[str, ...], cards: dict) -> bool:
    """Whether a unit is queued now: each PR of it green and approved with nothing holding it, no draft, no conflict with its base, no card of his open on it or its job and no lander running on it, and one of them on a head no lander has tried. A job still running on it does not hold it, since his Approve lands it at once."""
    prs = [data["prs"][key] for key in keys]
    fit = all(
        view.unblocked(pr, data, cards)
        and not pr["draft"]
        and not pr["conflict"]
        and not open_cards(data, pr, cards)
        and not view.open_cards(data["jobs"][pr["job"]], cards)
        and not running(pr)
        for pr in prs
    )
    return fit and any((pr["lander"] or {}).get("head") != pr["head"] for pr in prs)


def units(data: dict, keys: list[str], cards: dict) -> list[tuple[str, ...]]:
    found = dict.fromkeys(unit(data, key) for key in keys if state.queued_on_tap(key) and data["prs"][key]["state"] == "open")
    return [members for members in found if members and ready(data, members, cards)]


def start(name: str, keys: list[str], cards: dict | None) -> list[tuple[str, ...]]:
    """Start a lander on each unit his Approve covers among keys that is ready, marking its PRs under the drive's lock so no second lander starts on their heads; the units started. With his cards unread, none, since a card of his may hold one."""
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
        return f"not merging it: {why}"
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


def run(name: str, keys: list[str]) -> str:
    """Land one unit his Approve covers, or one yasyf/* PR, then record what came of it on each PR and as a landing event, which the watch says on the PR's line: a failure wakes its job, a queued one does not."""
    data = state.load(name)
    if refused := [key for key in keys if not (state.merges_untapped(key) or state.queued_on_tap(key) and approved(data["prs"][key]))]:
        raise state.DriveError(f"{', '.join(refused)}: the drive lands a yasyf/* PR, or a Forge-AI/monorepo PR he approved, and no other")
    outcome = merged(keys[0]) if state.merges_untapped(keys[0]) else enqueued(name, Path(data["roles"]["coordinator"]["cwd"]), keys)
    with state.edit(name) as data:
        for key in keys:
            pr = data["prs"][key]
            pr["lander"] = (pr["lander"] or {"head": pr["head"], "pid": os.getpid(), "at": state.stamp()}) | {"outcome": outcome}
            repo, number = key.split("#")
            state.append_event(name, "landing", repo=repo, number=int(number), change=outcome, lane=state.heard_lane(data, pr["order"]))
    return outcome


def said(output: str) -> str:
    return cut("; ".join([line for line in output.splitlines() if line.strip()][-SAID_LINES:]), SAID_CHARS)


def enqueued(name: str, checkout: Path, keys: list[str]) -> str:
    """Queue the unit through the coordinator checkout's stack-enqueue, which watches until Trunk holds every PR of it or one needs a fix rather than a wait; what came of it."""
    argv = [str(checkout / ENQUEUE), "--watch", *(key.split("#")[1] for key in keys)]
    try:
        done = subprocess.run(argv, cwd=checkout, capture_output=True, text=True)
    except OSError as error:
        return f"not landed: {argv[0]} did not start: {error}"
    print(done.stdout + done.stderr, flush=True)
    if done.returncode == 0:
        return QUEUED
    return f"not landed: stack-enqueue --watch exited {done.returncode}: {said(done.stdout + done.stderr)}; its whole output is in {state.drive_dir(name) / LOG}"


def merged(key: str) -> str:
    """Merge a yasyf/* PR with --admin past its checks, by his rule for his own repositories, then install from its merge commit each plugin of this Mac it changes; what came of it."""
    repo, number = key.split("#")
    try:
        github.run(["gh", "pr", "merge", number, "-R", repo, "--squash", "--admin"])
    except github.GithubError as error:
        return f"not landed: gh pr merge --squash --admin refused it: {cut(str(error), SAID_CHARS)}"
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
