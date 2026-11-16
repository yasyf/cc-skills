import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from drive import agents, github, install, providers, served, state, train, view
from drive.render import cut

DRIVE = Path(__file__).resolve().parents[2] / "bin" / "drive"
LOG = "landings.log"
LANDINGS = "landing"
MISE_TRUST = "MISE_TRUSTED_CONFIG_PATHS"
CHECKOUT_SECONDS = 600
CLONE_SECONDS = 3600
LANE_HELD_SECONDS = 540
SAID_CHARS = 400
SAID_LINES = 4
QUEUED = "queued for merge"
DEQUEUED = "dequeued: Trunk cancelled its merge queue entry"
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
NEWER_INSTALL = "not landed: a newer long-running saved the drive while the merge queue's script ran its check, so the landing runs again on it"
OWN_FAILURE = "the lander could not"
NO_ENQUEUE = f"{OWN_FAILURE} finish the merge queue's script"
NO_DEQUEUE = f"{OWN_FAILURE} take"
ENQUEUE_FAILED = 5
HELD = "not landed while he holds it"
WAITED = "not landed while it waits"
AWAY = "not landed while he is away"
UNREAD = "not landed: a GitHub read failed before it queued or merged anything, so the next watch pass lands it again"
RUNNING = "not landed: its checks still run"
RETRIED = (NEWER_INSTALL, OWN_FAILURE, HELD, WAITED, AWAY, UNREAD, RUNNING)
OWN_RED = "not landed: its own change fails"
DEQUEUED_LINE = re.compile(r"^#\d+ DEQUEUED ", re.MULTILINE)
BLOCKED_LINE = re.compile(r"^#(?P<number>\d+) BLOCKED (?P<head>\w+) (?P<gaps>.+)$", re.MULTILINE)
OLDER_HEAD = re.compile(r"awaiting approval on head from \S+ \(approved [^,]+, (?:on an older head|dismissed by a push)\)")
REVERT = re.compile(r"^Reverts?\s|⏪|\bThis reverts\b", re.MULTILINE)
STORED_DATA = re.compile(r"(?:^|/)migrations?/|(?:^|/)[^/]*schema[^/]*$|\.(?:sql|proto)$", re.IGNORECASE)
BRANCH_HISTORY = 50
BRANCH_SECONDS = 20
TESTED = re.compile(r"HEAD is now at \w+ Merge [0-9a-f]{40} into (?P<base>[0-9a-f]{40})")


def open_cards(data: dict, job_id: str, pr: dict | None) -> list[str]:
    """His open cards holding a job's work, its PR when given, from landing, since work does not land with a question of his on it unanswered: each card of an open question holding its work by state.part_cards, the one rule by which a card holds work, so a card a job or PR waits on otherwise only wakes it; none for an immediate fix, which lands while he picks the proper fix, or a flake fix or main fix, the PR's exempt class, which fixes a flaking test the day it flakes by his ruling 532cd3d or the default branch's failing CI, and on an open incident, whose repair lands on the recommended option, only a card whose question asks him to lift a ruling of his, which `drive job ask` marks when Jev reads any option it offers as breaking a recorded answer or ruling of his, its recommended option first, or --unsettled names one, by his pick "Hold the PR until you answer; the drive finds each card against your recorded answers"."""
    job = data["jobs"][job_id]
    if state.IMMEDIATE_FIX in job["parts"] or (pr or {}).get("exempt") in state.UNCARDED_CLASSES:
        return []
    found = list(dict.fromkeys(state.part_cards(data, job_id, pr)))
    if state.open_incidents(data, [job["order"], *job["carries"]]):
        return [card for card in found if any(question["unsettled"] and question["closed"] is None for _, question in view.drawn_on(data, card))]
    return found


def pr_cards(data: dict, pr: dict) -> list[str]:
    """His open cards holding a PR from landing: those holding the work of the job its row names, by open_cards(), never a card of a job it moved off or that lists it beside work of its own."""
    return open_cards(data, pr["job"], pr)


def staged(data: dict, pr: dict) -> list[str]:
    """Each started staging test of the PR that holds it from landing until it passes, by state.staging_runs, as view.holding reads it."""
    return view.holding(data, state.staging_runs(data, pr), None)


def claim_main(name: str, job_id: str) -> str:
    """Claim for a job about to open a main fix, in one write that runs again on the drive as it then stands when another claim committed meanwhile, each test its repository's main fails now that no other job holds, by state.claim_mains, before its PR goes up, so of two builders shipping a fix of one test seconds apart the second is refused naming the first: the `bash_guard` hook asks it of every `ccx vcs ship` registering its PR as a main fix in the same call, on this Mac and through `drive job held` on a Sprite; the refusal, else ""."""
    if not (repo := state.load(name)["jobs"][job_id]["repo"]):
        return ""
    failing = install.default_failures(repo)
    return state.write(name, lambda data: state.claim_mains(data, repo, job_id, failing, None, state.stamp(state.now())))


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
    """The paths outside test and fixture code that change how data is stored, a migration, schema or wire format, which no pure port carries."""
    return [path for path in paths if STORED_DATA.search(path) and not served.TESTS.search(path)]


def dangerous(data: dict, paths: list[str]) -> list[str]:
    """The paths outside test and fixture code that the repository's provider calls dangerous, able to break FDE or customer runs or lose their data."""
    return [path for path in providers.of(data).repo.dangerous_paths(paths) if not served.TESTS.search(path)]


def lifted(pr: dict) -> bool:
    """Whether its builder's `STAGING: none: <why>` lifted the PR's staging test at its head, by state.unstage, or his answer let it merge first."""
    return bool(pr["unstaged"]) and (pr["unstaged"]["merges_first"] or pr["unstaged"]["head"] == pr["head"])


def danger(data: dict, key: str) -> str:
    """What makes a PR of the guarded owner dangerous, so it lands or releases only while he is present: each path dangerous() names, as GitHub lists its files now, else an order's `staging test of #N before it lands` part for it, delivered or not, which a builder's STAGING line gives only a change it read as dangerous, an infra or blue-green one included whatever its paths; "" for neither."""
    if found := dangerous(data, github.files(key)):
        return f"it changes {', '.join(found[:3])}{', …' if len(found) > 3 else ''}"
    part = state.STAGING_TEST.format(number=key.partition("#")[2])
    if not state.home_repo(data, github.repo_of(key)) or not (order_id := next((order_id for order_id, order in data["orders"].items() if part in order["parts"]), None)):
        return ""
    return f"{order_id} holds its {part} part, given only to a change its builder read as dangerous"


def unattended(data: dict, key: str, moment: datetime) -> str | None:
    """Why a PR of the guarded owner waits for his next word to land: he is not present, by state.absence, and danger() reads it dangerous, by his words state.PRESENCE_WORDS; None otherwise, reading GitHub only while he is not present."""
    if not state.guarded(providers.of(data).repo.guarded_owner, key) or not (away := state.absence(data, moment)) or not (why := danger(data, key)):
        return None
    return (
        f"{away}, and {why}, which can break FDE or customer runs or lose their data,"
        f" so it lands only while he is present, his word or tap within {state.PRESENT_WITHIN.seconds // 60} minutes, at his next word, by his words {state.PRESENCE_WORDS}"
    )


def not_ci(data: dict, paths: list[str]) -> list[str]:
    """The paths outside CI configuration and CI tooling, as the repository's provider reads them, which no ci-only PR changes."""
    repo = providers.of(data).repo
    return [path for path in paths if not repo.ci_config(path) and not repo.checkout_tools(path)]


def outside(data: dict, exempt: str, key: str, read: dict) -> str:
    """Why a PR, by its changed files, title and body as served.change read them, or for a main fix by its repository's default branch's CI, is not in the exempt class, the one check of an exempt class: the lander's before it lands, `drive pr add`'s and `drive pr set`'s before they keep the class, and a flake fix's tap; "" when it is."""
    paths = [path for path, _ in served.files(read["diff"])]
    match exempt:
        case state.MAIN_FIX:
            repo = github.repo_of(key)
            return "" if install.red_on_default(repo) else f"no workflow's newest run on {repo}'s {github.default_branch(repo)} failed, so it fixes no red default branch"
        case "pure port":
            found = stored(paths)
            return f"it changes {', '.join(found)}, how its data is stored, which a port models anew for the target tree on his card" if found else ""
        case "test-only" | state.FLAKE_FIX:
            found = [path for path in paths if not served.TESTS.search(path)]
            return f"it changes {', '.join(found)}, outside test and fixture code" if found else ""
        case state.CI_ONLY:
            found = not_ci(data, paths)
            return f"it changes {', '.join(found)}, outside CI configuration and CI tooling" if found else ""
        case "incident fix":
            return "its title or body says it reverts a change, which lands only on his tap" if REVERT.search(f"{read['title']}\n{read['body']}") else ""


def unexempt(data: dict, key: str) -> str | None:
    """Why a PR landing by its exempt class rather than his Approve is not in that class, by outside() on its changed files, title and body on GitHub now, by his ruling that the landing tool checks the PR really is in the exempt class before it lands; None when it is, or he approved it. A ci-only PR whose head left its class loses the class the paths gave it, by run(), so his card asks his Approve with no session's word."""
    pr = data["prs"][key]
    if approved(pr):
        return None
    if not (why := outside(data, pr["exempt"], key, served.change(key))):
        return None
    if pr["exempt"] == state.FLAKE_FIX:
        return f'{key} is a "{pr["exempt"]}", which lands with no tap only in test or fixture code, and {why}; it lands on his Approve once drive pr set {state.pr_url(key)} --tap puts it on his card'
    if pr["exempt"] == state.CI_ONLY:
        return f'{key} is no "{pr["exempt"]}" at its head: {why}; its class comes off, so it lands on his Approve, which his card asks'
    return f'{key} is no "{pr["exempt"]}": {why}; it lands on his Approve once drive pr set {state.pr_url(key)} --no-exempt --tap puts it on his card'


def running(pr: dict) -> bool:
    """Whether a lander still holds the PR's try with no outcome: the watch or tap that reserved it, until a child adopts it, or the child that adopted it, by state.holder."""
    lander = pr["lander"]
    return bool(lander) and lander["outcome"] is None and runs(lander)


def pid_of(record: dict) -> int | None:
    """The pid of the process a lander's, proof's or dequeuer's record names: its reservation's holder, by state.holder, or the pid an install before reservations kept on it."""
    return record["pid"] if "state" not in record and "pid" in record else state.holder(record)


def runs(record: dict) -> bool:
    """Whether the process a lander's or proof's record names still runs: its reservation's holder, by state.holder, or the pid an install before reservations kept on it, so a lander or proof that install started still reads running after the install that ships reservations restarts the watch."""
    # TODO: delete the pid read once no drive keeps a lander or proof record an install before reservations wrote
    return state.holder(record) is not None or "state" not in record and "pid" in record and state.alive(record["pid"])


def attempt(lander: dict) -> object:
    """The one try a lander record belongs to, shared by every PR of its unit: its reservation's token, or for a try an older install recorded, its pid and start."""
    return lander.get("token") or (lander["pid"], lander["at"])


def reported(data: dict, key: str, pr: dict) -> bool:
    """Whether the lander merges the PR itself with gh pr merge, as it does every PR but one of the drive's home repository its merge queue merges, by state.queued_by_lander, and says so on its own line, so GitHub's merged line asks no step: it still runs on it, or it merged it."""
    return not state.queued_by_lander(data, key) and (running(pr) or ((pr["lander"] or {}).get("outcome") or "").startswith("merged"))


def landing(pr: dict) -> bool:
    """Whether a lander holds the PR on its head: still running on it, done with it queued for merge while Trunk has neither ejected nor dequeued it since, the latter of which the watch records as the try's outcome, and GitHub reads no conflict with its base, which Trunk cannot merge past and only its builder's rebase settles, or done rebasing it onto its base, until the watch reads the head it pushed."""
    lander = pr["lander"] or {}
    outcome = lander.get("outcome") or ""
    return lander.get("head") == pr["head"] and (running(pr) or outcome == QUEUED and not pr["ejected"] and not pr["conflict"] or outcome.startswith(REBASED))


def flaked(pr: dict) -> dict | None:
    """What drive pr set --flaky kept at the PR's head: the flaky test outside its diff that failed it there, in its own checks, which failed_checks keeps, or in Trunk's ejection of its green head, which keeps none, and that test's fix, a PR the drive tracks, or its cause in words with no fix PR; None at any other head, and once a lander requeued this one."""
    flaky = pr["flaky"]
    return flaky if flaky and flaky["head"] == pr["head"] and not flaky["requeued"] else None


def requeues(data: dict, pr: dict) -> bool:
    """Whether a lander queues the PR again at the green head the merge queue ejected it at, through the queue's flake requeue, as it does once, when its flaky test's fix merged, or at once when the record names the test's cause and no fix; never one whose own checks failed there, whose builder restacks it onto the fix."""
    return bool(flaky := flaked(pr)) and not flaky["failed_checks"] and (not flaky["fix"] or data["prs"][flaky["fix"]]["state"] == "merged")


def refused(pr: dict) -> bool:
    """Whether GitHub refused, for the first time at the PR's head, the merge its lander queued there once Trunk's merge queue took it, with no failing check, so the lander lands it again one PR at a time, bottom first; a refusal of that one-at-a-time landing is not landed."""
    lander = pr["lander"] or {}
    return lander.get("head") == pr["head"] and (lander.get("outcome") or "").startswith(REFUSED) and not (lander.get("was") or "").startswith(REFUSED)


def conflicted(pr: dict) -> bool:
    """Whether GitHub refused the lander's merge at the PR's head for a conflict with its base, which noted() writes onto its conflict flag, so the PR reads red until GitHub reads that head mergeable again and lands again then."""
    lander = pr["lander"] or {}
    return lander.get("head") == pr["head"] and any(conflict in (lander.get("outcome") or "") for conflict in CONFLICTS)


def own_red(pr: dict) -> str:
    """What fails at the PR's head on its own change, which only its builder's fix settles: the checks the watch's read of that head judged so, by own_reds(), or what the lander's try there found; "" when neither found one."""
    judged = pr["own_reds"] or {}
    if judged.get("head") == pr["head"] and judged["own"]:
        return own_words(judged["own"], pr["head"])
    lander = pr["lander"] or {}
    return lander["outcome"].removeprefix("not landed: ") if lander.get("head") == pr["head"] and (lander.get("outcome") or "").startswith(OWN_RED) else ""


def own_words(owned: list[str], head: str) -> str:
    return f"its own change fails {', '.join(owned)} at {head[:7]}, which the default branch does not fail on the same tests where the PR's run of it merged its head"


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
    """Each open PR the drive tracks mapped to the open tracked PR it sits on, as the watch last read them and as the merge queue joins them: the one before it in its native GitHub stack or, at the bottom of one or in none, the open PR GitHub read heading its base, by github.parent; None for a PR on no tracked PR."""
    if index := state.index_of(data):
        return index.under
    opened = {key: pr for key, pr in data["prs"].items() if pr["state"] == "open"}
    under: dict[str, str | None] = {}
    for key, pr in opened.items():
        native = [member for member in pr["native"] if member in opened]
        under[key] = native[native.index(key) - 1] if key in native and native.index(key) else pr["under"] if pr["under"] in opened else None
    return under


def chain(under: dict[str, str | None], key: str) -> tuple[str, ...]:
    """Key and the open tracked PRs under it, bottom first: what the merge queue queues when key is named, never a PR above it."""
    return (*chain(under, parent), key) if (parent := under.get(key)) else (key,)


def landing_stacks(data: dict, under: dict[str, str | None] | None = None) -> dict[str, tuple[str, ...]]:
    """Each open PR the drive tracks in a stack, mapped to the stack's open tracked PRs bottom first, as the watch last read them: the PRs one card of his draws together, joined by unders() unless the caller already read them."""
    if (index := state.index_of(data)) and (under is None or under is index.under):
        return index.stacks
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
    """The open PRs his card drew with key that share its landing stack, bottom first, else key alone, since the merge queue refuses PRs named together that no stack links, such as the sibling PRs of one order his card draws together; on his Approve, the PRs the card his press answered listed, by the tap's card, never the card as drawn since, so a PR that joined the card after his press holds nothing below it."""
    pr = data["prs"][key]
    landed = stacks.get(key, (key,))
    return [member for member in (pr["tap"]["card"] if approved(pr) else pr.get("stack")) or [key] if member in landed and data["prs"][member]["state"] == "open"]


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
    """The open PRs the merge queue would queue with keys, below one of them in its native GitHub stack or under one through its base, that neither his Approve nor an exempt class clears, a pure port waiting on its original by state.port_hold among them."""
    under = dict.fromkeys(key for target in keys for key in github.below(target) if key not in keys)
    return [key for key in under if not (key in data["prs"] and state.cleared(data["prs"][key]) and not state.port_hold(data, data["prs"][key]))]


def held_below(keys: list[str], below: list[str]) -> str:
    return (
        f"not landed: {', '.join(below)} sits below {', '.join(keys)} in its stack with neither his Approve nor an exempt class, and the merge queue queues a stack"
        " from its bottom, so landing a PR above would land it too; it lands once he approves each of them or they merge"
    )


def units(data: dict, keys: list[str], cards: dict) -> list[tuple[str, ...]]:
    """The units a lander lands now among keys, in every repository the drive tracks: each key's unit() that reads ready, whole for a unit the repository's merge queue queues unless GitHub refused() its merge, and its bottom PR alone after that refusal and in any other repository, which gh pr merge merges one PR at a time, longest first, leaving out one that shares a PR with a unit taken before it, since one lander holds a PR at a time: a unit inside a longer one, or one on the same PR as a sibling's, which lands at a later pass."""
    under = unders(data)
    stacks = landing_stacks(data, under)
    found = dict.fromkeys(
        members if state.queued_by_lander(data, members[0]) and not any(refused(data["prs"][member]) for member in members) else members[:1]
        for key in keys
        if data["prs"][key]["state"] == "open" and (members := unit(data, under, stacks, key, cards))
    )
    taken: list[tuple[str, ...]] = []
    for members in sorted(found, key=len, reverse=True):
        if all(set(members).isdisjoint(other) for other in taken):
            taken.append(members)
    return taken


def start(name: str, keys: list[str], cards: dict | None) -> list[tuple[str, ...]]:
    """Answer each card his Approve on a PR among keys picks an option on, by answer_picks, then start a lander on each unit among keys that is ready, one his Approve or exempt classes clear or a yasyf/* PR its builder reported, reserving its PRs under one token, by mark, in a write that runs again when another committed meanwhile, then starting its lander with that token, which it adopts before anything else, so no second lander starts on their heads, and cancelling the reservation of a start that raised; the units started. With his cards unread, none, since a card of his may hold one. A pick that failed to answer holds its PR as its open card does, and raises once the rest started, naming why."""
    failed = answer_picks(name, keys)
    started: list[tuple[str, ...]] = []
    if cards is not None and units(state.load(name), keys, cards):
        def reserved(data: dict) -> dict[tuple[str, ...], state.Reservation]:
            return {members: held for members in units(data, keys, cards) if (held := mark(data, members))}

        held = state.write(name, reserved)
        unlaunched = dict(held)
        try:
            for members, reservation in held.items():
                pid = spawn(name, members, reservation.token)
                del unlaunched[members]
                state.settle(name, reservation, pid)
                started.append(members)
        finally:
            for reservation in unlaunched.values():
                state.cancel(name, reservation)
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
    def answer_picks_write(data: dict):
        for wait, seq in seqs.items():
            for key, tap in owed[wait]:
                if (now := data["prs"][key]["tap"]) and now["at"] == tap["at"]:
                    now["picks"][wait]["seq"] = seq

    state.write(name, answer_picks_write)
    superseded.follow(name, [(job_id, question) for job_id, question in view.drawn_on(state.load(name), *seqs) if (question["closed"] or {}).get("seq") in seqs.values()])
    return failed


def halt(name: str, keys: list[str], cards: dict | None) -> list[str]:
    """Stop each landing of a merge-queue unit with a PR among keys that stopping() stops, or that unattended() holds while he is not present, read before the write since it reads GitHub, so a unit queued before he went quiet leaves the queue: a lander still running on it by cancelling the reservation its unit's PRs hold, so a lander started on it that has not adopted it yet ends without working, and else by ending the process group of the child that cancel read as having adopted that very reservation, while that process, by its pid and start, still runs, never its starter nor whatever holds the PRs since, which dequeues what it queued, its stop reserved on each PR of its unit and adopted by this halt itself before the signal, so it holds that stop while it lives and signals nothing once another halt took it, and the hold recorded there once it went, so a halt that dies between them leaves the lander running for the next halt to stop, by halting(), and a unit the merge queue holds after its lander ended by unqueue(), a process of its own that adopts the dequeue reserved on each PR of the unit, takes it out of the queue and records it; each PR reads that hold until he answers, waiting until the wait ends or away until his next word, and lands then; the PRs stopped."""
    data = state.load(name)
    away = {key: f"{AWAY}: {why}" for key in keys if landing(pr := data["prs"][key]) and not dequeuing(pr) and not halting(pr) and not stopping(data, key, cards) and (why := unattended(data, key, state.now()))}
    if not held_landings(data, keys, cards, away):
        return []
    at = state.stamp()

    def stop_landings(data: dict) -> tuple[dict[object, list[str]], dict[tuple[str, ...], tuple[state.Reservation | int, state.Reservation, str]], dict[tuple[str, ...], tuple[state.Reservation, str]]]:
        stopped = held_landings(data, keys, cards, away)
        under = unders(data)
        units = {tried: sorted((key for key, pr in data["prs"].items() if landing(pr) and attempt(pr["lander"]) == tried), key=lambda key: len(chain(under, key))) for tried in stopped}
        kills, dequeues = {}, {}
        for tried, unit in units.items():
            if running(data["prs"][unit[0]]):
                lander = data["prs"][unit[0]]["lander"]
                if stop := state.reserve(data, tuple(("prs", key, "lander", "stop") for key in unit)):
                    for key in unit:
                        data["prs"][key]["lander"]["stop"] |= {"at": at}
                    target = state.Reservation(lander["token"], tuple(map(tuple, lander["members"]))) if "token" in lander else lander["pid"]
                    kills[tuple(unit)] = (target, stop, stopped[tried])
                continue
            if dequeuer := state.reserve(data, tuple(("prs", key, "lander", "dequeue") for key in unit)):
                for key in unit:
                    data["prs"][key]["lander"]["dequeue"] |= {"at": at}
                dequeues[tuple(unit)] = (dequeuer, stopped[tried])
        return units, kills, dequeues

    units, kills, dequeues = state.write(name, stop_landings)
    signalled: set[tuple[str, ...]] = set()

    def stopped_held(data: dict) -> None:
        for unit, (_, stop, why) in kills.items():
            held = [key for key in unit if (data["prs"][key]["lander"].get("stop") or {}).get("token") == stop.token]
            for key in held:
                data["prs"][key]["lander"].pop("stop")
            if unit in signalled and (settled := [key for key in held if data["prs"][key]["lander"]["outcome"] is None]):
                noted(name, data, settled, why, [])

    unlaunched = {unit: dequeuer for unit, (dequeuer, _) in dequeues.items()}
    try:
        for unit, (target, stop, _) in kills.items():
            if not state.adopt(name, stop.members[0], stop.token):
                continue
            if isinstance(target, state.Reservation):
                if (child := state.cancel(name, target)) and state.lives(child):
                    os.killpg(child[0], signal.SIGTERM)
            # TODO: delete the pid branch once no lander an install before reservations started still runs
            elif state.alive(target):
                os.killpg(target, signal.SIGTERM)
            signalled.add(unit)
        for unit, (dequeuer, why) in dequeues.items():
            pid = unqueue(name, list(unit), why, dequeuer.token)
            del unlaunched[unit]
            state.settle(name, dequeuer, pid)
    finally:
        if kills:
            state.write(name, stopped_held, patient=True)
        for dequeuer in unlaunched.values():
            state.cancel(name, dequeuer)
    return [key for unit in units.values() for key in unit]


def held_landings(data: dict, keys: list[str], cards: dict | None, away: dict[str, str]) -> dict[object, str]:
    """Each landing of a merge-queue unit with a PR among keys that stopping() stops, or that away holds by the outcome it names for that PR, a lander running on it or the merge queue holding what its lander queued, by that lander's try, by attempt, with the outcome saying why; none a dequeuer already takes out of the queue, nor one a halt still stops, by halting()."""
    return {
        attempt(pr["lander"]): outcome
        for key in keys
        if landing(pr := data["prs"][key]) and not dequeuing(pr) and not halting(pr) and (outcome := stopping(data, key, cards) or away.get(key, ""))
    }


def halting(pr: dict) -> bool:
    """Whether a halt() that took the stop of the PR's lander, still running, still runs to signal it and record the hold, so no second halt signals it again, which would cut short the dequeue its first signal started; once that halt died, the next one takes the stop again."""
    stop = (pr["lander"] or {}).get("stop")
    return bool(stop) and running(pr) and runs(stop)


def stopping(data: dict, key: str, cards: dict | None) -> str:
    """What stops a merge-queue landing on the PR, whether its lander still runs or the merge queue holds what it queued, as the outcome its unit records: a hold of his, by held(), a wait of the PR that still stands, by state.job_waits, its own staging tests included, his words ordering its rework, or nothing clearing it now, as once his words under its card took back his Approve; "" while what let it land stands, and for a PR of any other repository, whose lander merges it at once."""
    pr = data["prs"][key]
    if not state.queued_by_lander(data, key):
        return ""
    if holding := held(data, [key]):
        return f"{HELD}: {holding}"
    if standing := view.holding(data, state.job_waits(data, pr), cards):
        return f"{WAITED}: {key} waits on {', '.join(standing)}"
    if pr["rework"]:
        return f'{HELD}: {key}: it is being reworked to his words "{pr["rework"]["words"]}"'
    return "" if state.cleared(pr) else f"{HELD}: {key}: nothing clears it now, since his Approve came off it"


def dequeuing(pr: dict) -> bool:
    """Whether a dequeuer unqueue() started still takes the PR's unit out of Trunk's merge queue."""
    dequeuer = (pr["lander"] or {}).get("dequeue")
    return bool(dequeuer) and runs(dequeuer)


def unqueue(name: str, keys: list[str], why: str, token: str) -> int:
    """Run `drive pr land --dequeue` on a unit Trunk's merge queue holds as its own process, as spawn() runs a lander, so neither the watch pass nor the card command that stopped it waits on its worktree, with --reserved naming the token of the dequeue halt() reserved on its PRs, which it adopts before anything else."""
    return state.detach(name, ["pr", "land", *map(state.pr_url, keys), "--dequeue", why, "--reserved", token], LOG)


def dequeue(name: str, keys: list[str], why: str) -> str:
    """Take a unit the merge queue holds out of it with the queue's dequeue on each PR, its top first, the entry the queue queued the unit by, from a fresh() worktree of its repository's default branch as a landing runs it, then record why and what the queue said as its outcome, so each PR reads that hold and lands again once he approves it; on a failure of its own, no worktree, the queue's script failing itself, or no PR of the unit answering DEQUEUED, as #56900's NOT_QUEUED once the queue had passed its tests, the dequeue took nothing out, so undequeued() says so and the watch's next pass dequeues it again while the queue still holds it."""
    repo = github.repo_of(keys[0])
    data = state.load(name)
    try:
        enqueue = providers.of(data).repo.queue().script
        with fresh(name, Path(data["roles"]["coordinator"]["cwd"]), repo, github.default_branch(repo)) as (checkout, _):
            done = [subprocess.run([str(checkout / enqueue), "--dequeue", key.split("#")[1]], cwd=checkout, env=os.environ | {MISE_TRUST: str(checkout)}, capture_output=True, text=True) for key in reversed(keys)]
    except (github.GithubError, providers.ProviderError) as error:
        return undequeued(name, keys, cut(str(error), SAID_CHARS))
    output = "".join(each.stdout + each.stderr for each in done)
    print(output, flush=True)
    if any(each.returncode == ENQUEUE_FAILED for each in done) or not DEQUEUED_LINE.search(output):
        return undequeued(name, keys, f"{said(output)}; its whole output is in {state.drive_dir(name) / LOG}")
    return record(name, keys, f"{why}; {Path(enqueue).name} --dequeue said {said(output)}", [], {})


def undequeued(name: str, keys: list[str], why: str) -> str:
    """Say the dequeue of a unit failed on its own step, as the lander's own failure, and drop its dequeuer, so the watch's next pass starts another while Trunk's merge queue still holds it."""
    outcome = f"{NO_DEQUEUE} {', '.join(keys)} out of Trunk's merge queue: {why}"

    repo, number = keys[-1].split("#")

    def dropped(data: dict) -> None:
        for key in keys:
            data["prs"][key]["lander"].pop("dequeue")
        lane = state.heard_lane(data, data["prs"][keys[-1]]["order"])
        state.after(lambda: state.append_event(name, "landing", repo=repo, number=int(number), change=outcome, lane=lane) or [])

    state.write(name, dropped)
    return outcome


def unmergeable(read: dict) -> str | None:
    """Why a yasyf/* PR may not merge at the head GitHub holds now, by github.fetch's read of it, its repository's default branch and landing bases included: it is not open, is a draft, carries the hold label, or sits on a branch its repository lands no PRs on, by github.landing_base."""
    if read["state"] != "OPEN":
        return f"it is {read['state'].lower()}"
    if read["isDraft"]:
        return "it is a draft"
    if github.HOLD_LABEL in read["labels"]:
        return "it carries the hold label"
    if not github.landing_base(read["baseRefName"], read["default"], read["landing_bases"]):
        return f"it sits on {read['baseRefName']}, not {read['default']} or a landing base its .claude/long-running.toml names"
    return None


def admin_merged(name: str, key: str) -> str:
    """Merge a yasyf/* PR with --admin at the head GitHub holds now, once a fresh read finds nothing there that unmergeable() names, every check there finished and none failing on its own change, by own_reds(), and pinned to that head so a push since the read merges nothing, its outcome naming each failing check it merged past as the default branch's; what came of it, raising the GithubError of a read that failed before the merge."""
    reads, refused = github.fetch([key])
    if refused:
        raise refused
    read = reads[key]
    head = read["headRefOid"]
    if why := unmergeable(read):
        return f"not landed: {why}"
    if github.checks(read["checks"], []) == "pending":
        return f"{RUNNING} at {head[:7]}"
    while (judged := own_reds(key, read["checks"])) is None:
        time.sleep(BRANCH_SECONDS)
    owned, mains = judged
    if owned:
        return f"not landed: {own_words(owned, head)}"
    past = [
        f"{check} red at {head[:7]}, which {read['default']} failed on the same {f'{main["tests"]} tests' if main['tests'] > 1 else 'test'} at {main['at'][:7]}"
        for check, main in sorted(mains.items())
    ]
    return merged(name, key, read["merge_method"], ["--admin", "--match-head-commit", head], ", and ".join(past))


def own_reds(key: str, rollup: list[dict]) -> tuple[list[str], dict[str, dict]] | None:
    """The name of each check the PR's head fails on its own change: each it fails on a test the default branch does not fail too, by inherited(), by his words that tooling fixes merge early only when no check is failing on their own change; beside it each other failing check by name, with the default branch's run of it that fails the same tests; None while the default branch's run of one still runs."""
    judged = [(github.check_name(check), inherited(key, check)) for check in github.counted(rollup) if github.outcome(check) in github.FAILED]
    if any(found is None for _, found in judged):
        return None
    return sorted({name for name, found in judged if not found}), {name: found for name, found in judged if found}


def inherited(key: str, check: dict) -> dict | None:
    """The default branch's run of a check the PR's head fails, when it fails every test the PR's run failed, as each job's log names them, where the PR's run of it merged that head: at the base its log's checkout merged the head into, or the newest commit below it the check ran on, as on a `[skip ci]` commit no workflow runs on, once that run finished, as the commit it ran at and the count of those tests; {} when it does not, None while it runs. Never for a check no workflow ran, or whose log names no failing test or no merge, with no failure of the default branch's to compare it to, since a check is one verdict over every test of its job and the default branch failing one of them says nothing of another."""
    if not (job := github.job_of(check)):
        return {}
    repo = github.repo_of(key)
    log = github.job_log(repo, job)
    if not (failed := github.failed_tests(log)) or not (tested := TESTED.search(log)):
        return {}
    if not (main := branch_checks(repo, tested["base"]).get((check["workflowName"], github.check_name(check)))):
        return {}
    if main["conclusion"] is None:
        return None
    if main["conclusion"] in github.FAILED and failed <= github.failed_tests(github.job_log(repo, main["databaseId"])):
        return {"at": main["oid"], "tests": len(failed)}
    return {}


def branch_checks(repo: str, base: str) -> dict[tuple[str, str], dict]:
    """Each check run of a workflow run on the default branch, its name, conclusion, None while it runs, job id as databaseId and the commit it ran at as oid, by its workflow and name, at the newest of base and the BRANCH_HISTORY commits below it where it ran and was not cancelled."""
    owner, name = repo.split("/")
    text = (
        f"query {{ repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) {{ object(oid: {json.dumps(base)}) {{ ... on Commit {{ history(first: {BRANCH_HISTORY}) {{ nodes {{ oid "
        "checkSuites(first: 20) { nodes { workflowRun { workflow { name } } checkRuns(first: 20) { nodes { name conclusion databaseId } } } } } } } } } }"
    )
    found: dict[tuple[str, str], dict] = {}
    for commit in github.graphql(text, f"the default branch's checks at and below {repo}@{base[:12]}")["data"]["repository"]["object"]["history"]["nodes"]:
        for suite in commit["checkSuites"]["nodes"]:
            for check in suite["checkRuns"]["nodes"] if suite["workflowRun"] else []:
                if check["conclusion"] != "CANCELLED":
                    found.setdefault((suite["workflowRun"]["workflow"]["name"], check["name"]), check | {"oid": commit["oid"]})
    return found


def base_failures(repo: str, run: str) -> list[str]:
    """Each test the default branch failed where a PR's workflow run, by its link, merged the PR's head: at the base its failed jobs' logs checked out, or the newest commit below it the workflow ran on, as below a `[skip ci]` autobump no workflow runs on, each of the workflow's checks read there by branch_checks, so a test main already failed where the PR was tested is never the PR's; none when no log names a merge or main's checks there passed or still run."""
    jq = '.jobs[] | select(.conclusion == "failure") | {id, workflow_name}'
    jobs = [json.loads(line) for line in github.read(f"{repo}'s run {run} failed jobs", ["gh", "api", f"repos/{repo}/actions/runs/{run.rpartition('/')[2]}/jobs?filter=latest&per_page=100", "--jq", jq]).splitlines()]
    if not (tested := next((found for job in jobs if (found := TESTED.search(github.job_log(repo, job["id"])))), None)):
        return []
    main = [check for (workflow, _), check in branch_checks(repo, tested["base"]).items() if workflow == jobs[0]["workflow_name"] and check["conclusion"] in github.FAILED]
    return sorted({test for check in main for test in github.failed_tests(github.job_log(repo, check["databaseId"]))})


def mark(data: dict, keys: tuple[str, ...]) -> state.Reservation | None:
    """Reserve the PRs a lander starts on under one token, by state.reserve, for the lander that adopts them all, marked at the head and base it tries, each keeping what the try before it on the same head came to, so record() says an outcome the same head already said only once, and none ejected, since an ejection is Trunk's label set after the try began; the reservation, or None, marking nothing, while a lander still holds any of them."""
    was = {key: before.get("outcome") if (before := data["prs"][key]["lander"] or {}).get("head") == data["prs"][key]["head"] else None for key in keys}
    if not (held := state.reserve(data, tuple(("prs", key, "lander") for key in keys))):
        return None
    at = state.stamp()
    for key in keys:
        pr = data["prs"][key]
        pr["lander"] |= {"head": pr["head"], "base": pr["base"], "at": at, "outcome": None, "was": was[key]}
        pr["ejected"] = False
    return held


def spawn(name: str, keys: tuple[str, ...], token: str) -> int:
    """Run `drive pr land` on the keys as its own process, outliving the watch pass or card tap that started it, its output appended to the drive's landing log, with --reserved naming the token of the reservation mark made on them, which it adopts before anything else."""
    return state.detach(name, ["pr", "land", *map(state.pr_url, keys), "--reserved", token], LOG)


def run(name: str, keys: list[str], dry_run: bool = False) -> str:
    """Land one unit his Approve or exempt classes clear, or one yasyf/* PR, in whichever repository the drive tracks it, never a PR the drive does not track or one someone else opened that no order of his it serves names, nor several PRs outside a merge-queue unit, then record what came of it on each PR, which its stage reads, and as a landing event: a yasyf/* PR merges with gh pr merge --admin at the head a fresh read finds every check finished on with none failing on its own change, a merge-queue unit queues through the repository's merge queue, and a PR of any other repository merges with gh pr merge at the head its stage read ready, which GitHub merges only once its repository's rules pass, each by the method its repository allows, by github.merge_method. His Approve lands the PR whatever the check of its change against his words says, which only informs his card and its owner; a unit with a PR below it that nothing clears is not landed, since its landing would land that PR too, nor one with a PR its changes put outside its exempt class, a ci-only one losing that class so his card asks his Approve, nor one a hold of his holds, his Hold, the hold label or an open card of his on the work of any job carrying a PR of it, nor one of the guarded owner unattended() holds while he is not present, nor one with a PR whose started staging test has not passed, by state.staging_runs, nor one with a PR waiting on his tap whose paths at its head reach a component a release deploys and that no staging test passed yet, by cli.staging_reach, which it puts on its staging test first, its tester asking his exception when staging has a copy of none of them, but one drive order set --drop-part or his answer let merge before that test, by state.merge_first, which lands and gets its after-merge staging pass. A GitHub read or drive read that fails before it queues or merges anything, in any repository, ends the try not landed by UNREAD's rule, so the next watch pass lands it again. A dry run runs the merge queue's check as the landing would, says what it would do, and lands and records nothing."""
    data = state.load(name)
    if untracked := [key for key in keys if key not in data["prs"]]:
        raise state.DriveError(f"{', '.join(untracked)}: the drive tracks no such PR, so it lands none; drive pr add <url> --job <job> registers one first")
    if refused := [key for key in keys if not (state.merges_untapped(key) or state.cleared(data["prs"][key]) and not state.port_hold(data, data["prs"][key]))]:
        raise state.DriveError(f"{', '.join(refused)}: the drive lands a yasyf/* PR, or a PR he approved or an exempt class lands, a pure port only once its original merged or he approved it, and no other")
    if foreign := [why for key in keys if (why := state.foreign_pr(data, key))]:
        raise state.DriveError("; ".join(foreign))
    if len(keys) > 1 and not state.queued_by_lander(data, keys[0]):
        raise state.DriveError(f"{', '.join(keys)}: {' '.join(MERGE)} merges one PR at a time, so the lander lands one outside a merge queue, never crediting the rest with its merge")
    below: list[str] = []
    asked: dict[str, str] = {}
    try:
        if holding := held(data, keys):
            outcome = f"{HELD}: {holding}"
        elif testing := [run for key in keys for run in staged(data, data["prs"][key])]:
            outcome = f"{WAITED}: its staging test {', '.join(testing)} has not passed"
        elif state.merges_untapped(keys[0]):
            outcome = f"would merge it with {' '.join(MERGE)} --{data['prs'][keys[0]]['merge_method']} --admin once every check at its head finished with none failing on its own change, then board each plugin it changes on the install train, or install it at once for an incident or priority order" if dry_run else admin_merged(name, keys[0])
        else:
            from drive import cli

            below = uncleared_below(data, keys)
            misfiled = {key: why for key in keys if (why := unexempt(data, key))}
            untested = {key: unstaged for key, unstaged in cli.staging_reach(name, data, [key for key in keys if data["prs"][key]["wants_tap"]]).items() if not state.staging_tested(data, key) and not (unstaged and (data["prs"][key]["exception"] or {}).get("granted"))}
            changed = {key: github.files(key) for key in keys if key not in untested and not state.staging_tested(data, key) and not lifted(data["prs"][key]) and not any(state.part_wait(data, wait) for wait in data["prs"][key]["waits"])}
            risky = {key: made for key, files in changed.items() if (made := dangerous(data, files))}
            if (untested or misfiled or risky) and not dry_run:

                def reached(edited: dict) -> None:
                    for key, unstaged in untested.items():
                        state.reach_staging(edited, key, unstaged)
                    for key, made in risky.items():
                        edited["prs"][key]["paths_at"] = None
                        state.mark_dangerous(edited, key, edited["prs"][key]["head"], changed[key])
                    for key in misfiled:
                        if edited["prs"][key]["exempt"] == state.CI_ONLY:
                            edited["prs"][key].update(exempt=None, wants_tap=True)

                state.write(name, reached)
            if misfiled:
                outcome = f"not landed: {'; '.join(misfiled.values())}"
            elif untested:
                outcome = f"{WAITED}: " + "; ".join(
                    f"{key} waits on his exception before it lands, since its paths at its head reach {', '.join(unstaged)}, which staging has no copy of:"
                    f" its builder's DONE carries the production dry-run plan drive pr plan {key} reads, he reads it on his card, and drive pr set {key} --excepted <his answer> keeps his word"
                    if unstaged
                    else f"{key} waits on its staging test before it lands, since its paths at its head reach a component staging runs"
                    for key, unstaged in untested.items()
                )
            elif risky:
                outcome = f"{WAITED}: " + "; ".join(f"{key} waits on its staging test before it lands, since its head changes {', '.join(made[:3])}{', …' if len(made) > 3 else ''}, a dangerous change by AGENTS.md" for key, made in risky.items())
            elif away := next((why for key in keys if (why := unattended(data, key, state.now()))), None):
                outcome = f"{AWAY}: {away}"
            elif below:
                outcome = held_below(keys, below)
            elif state.queued_by_lander(data, keys[0]):
                outcome = enqueued(name, Path(data["roles"]["coordinator"]["cwd"]), providers.of(data).repo.queue().script, keys, data["prs"], asked, dry_run, any(requeues(data, data["prs"][key]) for key in keys))
            else:
                pr = data["prs"][keys[0]]
                outcome = f"would merge it with {' '.join(MERGE)} --{pr['merge_method']} --match-head-commit {pr['head'][:12]}" if dry_run else merged(name, keys[0], pr["merge_method"], ["--match-head-commit", pr["head"]])
    except (github.GithubError, state.DriveError) as error:
        print(error, flush=True)
        outcome = f"{UNREAD}: {cut(str(error), SAID_CHARS)}"
    if dry_run:
        return outcome
    return record(name, keys, outcome, below, asked)


def record(name: str, keys: list[str], outcome: str, below: list[str], asked: dict[str, str]) -> str:
    """Record what came of a landing on each PR, with the top of keys as its entry, the PR the merge queue submits and so the one whose labels say the queue ejected them all, and as a landing event unless the try before it on the same head came to the same, such as a retried landing whose queue script again failed on its own or whose GitHub read failed again, whatever its refusal said, as ccx's rate-limit floor names the requests it leaves each time, and on each PR asked names the head its lander asked the repository's review bot to review, so none is asked twice; on the newest install, which `drive pr land --outcome` records it on when one landed while this lander worked."""
    install.reopen(["pr", "land", *map(state.pr_url, keys), "--drive", name, "--outcome", outcome, *(flag for key in below for flag in ("--below", key)), *(flag for key, head in asked.items() for flag in ("--asked", key, head))])

    def recorded(data: dict) -> None:
        for key, head in asked.items():
            data["prs"][key]["review_asked"] = head
        noted(name, data, keys, outcome, below)

    state.write(name, recorded)
    return outcome


def noted(name: str, data: dict, keys: list[str], outcome: str, below: list[str]) -> None:
    events = []
    at = state.stamp()
    for key in keys:
        pr = data["prs"][key]
        if outcome == QUEUED and requeues(data, pr):
            pr["flaky"]["requeued"] = at
        pr["lander"] = (pr["lander"] or {"head": pr["head"], "base": pr["base"], "pid": os.getpid(), "at": at}) | {"outcome": outcome, "below": below, "entry": keys[-1]}
        pr["conflict"] |= conflicted(pr)
        was = pr["lander"].get("was")
        if was == outcome or outcome.startswith(OWN_FAILURE) and (was or "").partition(": ")[0] == outcome.partition(": ")[0] or outcome.startswith(UNREAD) and (was or "").startswith(UNREAD):
            continue
        repo, number = key.split("#")
        events.append({"repo": repo, "number": int(number), "change": outcome, "lane": state.heard_lane(data, pr["order"])})
    if events:
        state.after(lambda: [state.append_event(name, "landing", **event) for event in events] and [])


class Halted(SystemExit):
    pass


def halted(signum: int, frame: object) -> None:
    raise Halted(128 + signum)


def said(output: str) -> str:
    return cut("; ".join([line for line in output.splitlines() if line.strip()][-SAID_LINES:]), SAID_CHARS)


def enqueued(name: str, coordinator: Path, enqueue: str, keys: list[str], prs: dict, asked: dict[str, str], dry_run: bool = False, flake: bool = False) -> str:
    """Queue the unit through the repository's merge queue as the head of its repository's default branch holds it, run from a fresh() worktree of this Mac's landing clone and never from the coordinator's checkout, with --flake when a PR of it requeues() at the head the queue ejected it at: --check first, whose exit of ENQUEUE_FAILED ends the landing as the lander's own failure, by unfinished(), as the same exit of --watch does, and whose BLOCKED lines name each head approved only on an older one, which the queue names only once its review finished on the head; a dry run stops there, saying what it would queue and the BLOCKED lines it read; then the repository's review bot is asked to review each such head that prs, the drive's PRs as the lander read them at its start, records no ask of, each head it asks added to asked for record() to keep, and --watch, which alone tells a wait from a fix, such as GitHub still computing mergeability from a conflict, waits until the queue holds every PR of it or one needs a fix rather than a wait, unless halt() ends it first, which dequeues each PR of it; what came of it, a GitHub read that failed before anything queued, the landing clone's fetch included, not landed by UNREAD's rule. It reads and writes no drive state, so an install that saves the drive while it makes the worktree, runs --check or waits on the queue never ends the landing."""
    repo = github.repo_of(keys[0])
    numbers = [key.split("#")[1] for key in keys]
    flags = ["--flake"] if flake else []
    signal.signal(signal.SIGTERM, halted)
    try:
        branch = github.default_branch(repo)
        with fresh(name, coordinator, repo, branch) as (checkout, sha):
            script = str(checkout / enqueue)
            trusted = os.environ | {MISE_TRUST: str(checkout)}
            print(f"{', '.join(keys)}: {Path(enqueue).name} from {checkout}, a fresh worktree of {repo}'s {branch} at {sha}", flush=True)
            try:
                checked = subprocess.run([script, "--check", *flags, *numbers], cwd=checkout, env=trusted, capture_output=True, text=True)
            except OSError as error:
                return f"{OWN_FAILURE} start {script} on {', '.join(keys)}: {error}"
            print(checked.stdout + checked.stderr, flush=True)
            if failed := unfinished(name, "--check", keys, checked):
                return failed
            blocked = list(BLOCKED_LINE.finditer(checked.stdout))
            if dry_run:
                read = f"; --watch waits out or stops on what --check read: {cut('; '.join(line[0] for line in blocked), SAID_CHARS)}" if blocked else ""
                return f"would queue {', '.join(keys)} through {Path(enqueue).name} {' '.join(['--watch', *flags])} from {branch} at {sha[:12]}: every PR below it is merged, approved by him or exempt{read}"
            for line in blocked:
                key = f"{repo}#{line['number']}"
                if not OLDER_HEAD.search(line["gaps"]) or prs[key]["review_asked"] == line["head"]:
                    continue
                if refused := ask_review(providers.at(coordinator).repo.review_ask, repo, line["number"], line["head"]):
                    return refused
                asked[key] = line["head"]
            try:
                done = subprocess.run([script, "--watch", *flags, *numbers], cwd=checkout, env=trusted, capture_output=True, text=True)
            except Halted:
                for number in numbers:
                    subprocess.run([script, "--dequeue", number], cwd=checkout, env=trusted)
                raise
    except github.GithubError as error:
        return f"{UNREAD}: {cut(str(error), SAID_CHARS)}"
    print(done.stdout + done.stderr, flush=True)
    if done.returncode == 0:
        return QUEUED
    return unfinished(name, "--watch", keys, done) or f"not landed: {Path(enqueue).name} --watch exited {done.returncode}: {said(done.stdout + done.stderr)}; its whole output is in {state.drive_dir(name) / LOG}"


def unfinished(name: str, mode: str, keys: list[str], done: subprocess.CompletedProcess) -> str | None:
    """The lander's own failure when the queue's script ended with no answer on the PRs, so the watch's next pass tries the same head again: its FAILED exit, ENQUEUE_FAILED, on a failure of its own such as a GitHub read that timed out, or a failing exit before it printed the line every answer of its own prints, as when mise, which runs the uv of its shebang, refuses the landing worktree's config; None when it answered."""
    if done.returncode != ENQUEUE_FAILED and (not done.returncode or done.stdout.strip()):
        return None
    how = "on a failure of its own" if done.returncode == ENQUEUE_FAILED else "before it printed an answer"
    return f"{NO_ENQUEUE} {mode} on {', '.join(keys)}, which exited {done.returncode} {how}: {said(done.stdout + done.stderr)}; its whole output is in {state.drive_dir(name) / LOG}"


@contextmanager
def fresh(name: str, coordinator: Path, repo: str, branch: str) -> Iterator[tuple[Path, str]]:
    """A worktree of repo at the head of branch as origin holds it now, made for one landing in the drive's landing folder and removed once it ends: its path and that head's sha. It comes from the one clone of repo this Mac keeps for landings, which holds every object with no blob filter and checks out no files of its own, so neither its checkout nor the queue script's merge checks ask origin for an object by its id, as a checkout of a partial clone such as the coordinator's does and as origin may answer without the object: the first landing clones it from the origin the coordinator's checkout names, made whole before it takes the clone's place, and each fetches branch into its refs/remotes/origin/<branch>, which the queue's script reads as the default branch it knows, both under the clone's lock, since landings run at once and each moves that one ref. Its LFS files check out as their pointers, since no landing step reads one and smudging it would ask GitHub's LFS store by id. A landing runs the repository's scripts, and the toolchain its mise.toml pins for them, as that head holds them, never as the coordinator's checkout holds them at whatever commit and edits it carries, which it leaves untouched; enqueued() and dequeue() run them with MISE_TRUST naming the worktree, since a mise shim on the lander's PATH, such as the uv of the queue script's shebang, refuses the worktree's untrusted mise configs and this Mac's mise trusts no path under the drive's folder. Every core checks it out at once, with no hook, since a repository's post-checkout hook may warm dependencies that no landing step reads."""
    clone = state.root() / CLONES / repo
    git = ["git", "-C", str(clone)]
    with state.flocked(clone.with_name(f"{clone.name}.lock")):
        if not clone.exists():
            making = clone.with_name(f"{clone.name}.making")
            shutil.rmtree(making, ignore_errors=True)
            origin = github.run(["git", "-C", str(coordinator), "remote", "get-url", "origin"]).strip()
            github.run(["git", "clone", "-q", "--no-checkout", "--no-tags", "--single-branch", "--branch", branch, origin, str(making)], timeout=CLONE_SECONDS)
            making.rename(clone)
        github.run([*git, "fetch", "-q", "--no-tags", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}"], timeout=CHECKOUT_SECONDS)
        sha = github.run([*git, "rev-parse", f"refs/remotes/origin/{branch}"]).strip()
    checkout = state.drive_dir(name) / LANDINGS / str(os.getpid())
    subprocess.run([*git, "-c", "core.hooksPath=/dev/null", "-c", "checkout.workers=0", "worktree", "add", "-q", "--detach", str(checkout), sha], env=os.environ | {"GIT_LFS_SKIP_SMUDGE": "1"}, check=True, timeout=CHECKOUT_SECONDS)
    try:
        yield checkout, sha
    finally:
        subprocess.run([*git, "worktree", "remove", "--force", str(checkout)])


def ask_review(ask: str | None, repo: str, number: str, head: str) -> str | None:
    """Post ask, the comment the repository provider names to ask its reviewer for a head it will not review on its own, on the PR, whose caller records that head as its review_asked so none is asked twice; why not, when GitHub refused it. Nothing to post when the provider names no ask."""
    if ask is None:
        return None
    done = subprocess.run(["gh", "pr", "comment", number, "-R", repo, "--body", ask], capture_output=True, text=True)
    if done.returncode:
        return unanswered(f"{repo}#{number}", f"gh pr comment {ask!r}", done.stderr) or (
            f"not landed: #{number} waits on a review of head {head}, and gh pr comment refused {ask!r}: {cut(' '.join(done.stderr.split()), SAID_CHARS)}"
        )
    print(f"#{number} asked {ask!r} for a review of head {head}: {done.stdout.strip()}", flush=True)
    return None


def merged(name: str, key: str, method: str, flags: list[str], past: str = "") -> str:
    """Merge a PR by method, squash, rebase or merge, the one github.merge_method reads its repository allowing, with gh pr merge and the flags given, --admin for a yasyf/* one by his rule for his own repositories, and --match-head-commit, which GitHub merges only at that head and, without --admin, only once its repository's rules pass, or through GitHub's merge-async endpoint once gh pr merge is refused for a PR of a native GitHub stack, with gh itself since ccx's GitHub gateway refuses a merge; then read the PR back with github.fetch and record its landing on its row by github.landing, the one rule of whether a PR merged, so the row reads merged before the watch's next read and a merge GitHub does not read merged ends the try not landed, installing nothing, rather than crediting the PR with whatever the default branch's head carries; once merged, board each plugin of this Mac it changes on the install train at its own merge commit, by train.board, which installs the newest sha boarded of each at most every train.EVERY, or install each from that commit at once when hotfix() reads its PR serving an open incident or priority order; what came of it, its merge naming past the failing checks admin_merged() read as the default branch's, a refusal not landed but a yasyf/* squash merge GitHub refuses on a conflict, which rebased() takes, and a merge GitHub gave no answer to or a read back that failed, the lander's own failure by unanswered()."""
    if (refusal := merged_sync(key, method, flags)) and STACKED in refusal:
        print(f"{key}: {refusal}; merging it through GitHub's {MERGE_ASYNC} instead", flush=True)
        refusal = merged_async(key, method, flags)
    if refusal and method == "squash" and state.merges_untapped(key) and any(conflict in refusal for conflict in CONFLICTS):
        return rebased(name, key, refusal)
    if refusal:
        return refusal if refusal.startswith(OWN_FAILURE) else f"not landed: {refusal}"
    try:
        reads, failed = github.fetch([key])
        if failed:
            raise failed
        files = github.files(key)
    except github.GithubError as error:
        return f"{OWN_FAILURE} read {key} back from GitHub after {' '.join(MERGE)} said it merged, so nothing was installed: {cut(str(error), SAID_CHARS)}; the next watch pass reads it"
    landed = github.landing(reads[key])
    def merged_write(data: dict):
        data["prs"][key].update(landed, updated=state.stamp())
        urgent = hotfix(data, data["prs"][key])
        return urgent

    urgent = state.write(name, merged_write)
    if landed["state"] != "merged":
        return f"not landed: GitHub reads it {landed['state']} after {' '.join(MERGE)} said it merged, so nothing was installed"
    sha = landed["merged_sha"]
    repo = github.repo_of(key)
    plugins = install.touched(repo, files)
    outcome = f"merged at {sha[:12]}{f' past {past}' if past else ''}"
    if not plugins:
        return f"{outcome}; it changes no plugin installed on this Mac"
    if not urgent:
        return "; ".join([outcome, *(train.board(plugin, repo, key, sha) for plugin in plugins)])
    lines = [outcome]
    for plugin in plugins:
        done = subprocess.run([str(DRIVE), "install", plugin, "--sha", sha, "--drive", name], capture_output=True, text=True)
        print(done.stdout + done.stderr, flush=True)
        lines.append(done.stdout.splitlines()[0] if done.returncode == 0 else f"but drive install {plugin} --sha {sha[:12]} failed: {said(done.stderr)}")
    return "; ".join(lines)


def hotfix(data: dict, pr: dict) -> bool:
    """Whether a merge installs at once rather than boarding the install train: its PR serves an open incident order or one drive order set --priority marked, by state.served_orders, so a fix someone waits on never waits out the train."""
    return any((order := data["orders"][order_id])["incident"] or order["priority"] for order_id in state.served_orders(data, pr) if data["orders"][order_id]["state"] not in state.CLOSED_ORDER_STATES)


def merged_sync(key: str, method: str, flags: list[str]) -> str | None:
    """Merge a PR by method with gh pr merge and the flags given; what gh said refusing it, the lander's own failure by unanswered() when GitHub gave no answer, or None once merged. GitHub refuses a merge whose base moved while it merged, which another merge into that base did, so the lander merges again on the new base."""
    repo, number = key.split("#")
    command = ["gh", "pr", "merge", number, "-R", repo, f"--{method}", *flags]
    while (done := subprocess.run(command, capture_output=True, text=True)).returncode and BASE_MOVED in done.stderr:
        print(f"{key}: {' '.join(done.stderr.split())}; merging it again on its new base", flush=True)
    if not done.returncode:
        return None
    named = " ".join([*MERGE, f"--{method}", *flags])
    return unanswered(key, named, done.stderr) or f"{named} refused it: {cut(' '.join(done.stderr.split()), SAID_CHARS)}"


def unanswered(key: str, command: str, said: str) -> str | None:
    """The lander's own failure when a gh write it ran on the PR failed with no word of GitHub's on it, so the watch's next pass tries the PR again and the meta session hears it once, as the lander's defect, rather than the session holding its order: GitHub never answered, by github.UNANSWERED, such as a TLS handshake that timed out or an HTTP 5xx, or ccx refused it before GitHub heard it, which ccx says on its last line while it stays silent when gh itself fails; None when GitHub answered, whose refusal is the PR's."""
    lines = said.strip().splitlines()
    if not (github.UNANSWERED.search(said) or lines and lines[-1].startswith("ccx: ")):
        return None
    return f"{OWN_FAILURE} get GitHub's answer on {key} to {command}: {cut(' '.join(said.split()), SAID_CHARS)}; the next watch pass tries it again"


def merged_async(key: str, method: str, flags: list[str]) -> str | None:
    """Merge a PR of a native GitHub stack by method through GitHub's merge-async endpoint, the one GitHub merges a stacked PR through, past the rules its actor may bypass for --admin, which GitHub grants only the stack's bottom PR, the one a lander outside a merge queue lands, or at the head --match-head-commit names, then read its result every ASYNC_SECONDS until GitHub ends it; why GitHub refused it, the lander's own failure by unanswered() when GitHub gave no answer to the merge, or None once merged."""
    repo, number = key.split("#")
    endpoint = f"repos/{repo}/pulls/{number}/{MERGE_ASYNC}"
    pinned = ["-f", f"sha={flags[flags.index('--match-head-commit') + 1]}"] if "--match-head-commit" in flags else []
    fields = ["-f", f"merge_method={method}", "-f", "merge_action=direct_merge", "-F", f"bypass_rules={str('--admin' in flags).lower()}", *pinned]
    done = subprocess.run(["gh", "api", "-X", "PUT", endpoint, *fields], capture_output=True, text=True)
    if done.returncode:
        return unanswered(key, f"GitHub's {MERGE_ASYNC}", done.stdout + done.stderr) or f"GitHub's {MERGE_ASYNC} refused it: {cut(' '.join((done.stdout + done.stderr).split()), SAID_CHARS)}"
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
