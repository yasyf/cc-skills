import collections
import contextlib
import functools
import hashlib
import json
import mmap
import os
import re
import select
import shlex
import threading
import time
import traceback
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple, Self

from drive import agents, brief, codex, dashboard, github, install, land, launchd, orca, page, place, procs, reads, receiver, refs, render, served, settled, slack, stage, stall, state, superseded, view
from drive.install import PROOF_FAILED, add_prover, config_dir, done_before_proofs, done_unproven, merged_done, merged_ready, merged_said, product, proof_gap, proofless, proved_prs, prover_of, proves, reported_prs, upgraded, watch_proves, widen
from drive.render import cut, when

POLL_SECONDS = 5
RELAY_SECONDS = 2
READ_EVERY = timedelta(seconds=60)
FAILURES_BEFORE_SAYING = 3
FAILURE_EVERY = timedelta(minutes=10)
DUE_EVERY = timedelta(minutes=10)
QUIET_THREAD = timedelta(minutes=5)
CLAIM_REPLIES = timedelta(minutes=5)
HANDOFF_FACT = "handoff"
FREED_AGAIN = timedelta(minutes=30)
LIVE_EVERY = timedelta(minutes=5)
TELL_AFTER = timedelta(minutes=10)
WAKES = ("TAP ", "DUE ", "TELL ", "PROVE ", "ORPHAN ", "ANSWER ", "JOB ", "LANE ", "STAGE ", "FREE ", "MOVED ", "WATCH stopped")
PR_EVENT = re.compile(r"PR (?P<key>\S+): (?P<change>.+) \[(?P<order>o\d+) (?P<job>j\d+)\]$")
STAGE_LINE = re.compile(r"STAGE (?P<key>\S+) (?P<name>\w+)\b")
ENDED = ("merged", "closed", PROOF_FAILED)
NOT_LANDED = "not landed"
LANDER = "LANDER "
TAP_EVENT = re.compile(rf"TAP (?P<label>\S+) (?P<key>\S+)(?P<stalled> {NOT_LANDED})?")
LANE = "watch"
READS_LOG = "reads.log"
CONFLICTS = "conflicts with its base"
NEWEST_MAIL = "newest lane mail"
LANE_MAIL = "lane mail"
RELAYING = threading.RLock()
RELAUNCHES_LOG = "relaunches.log"
PASS_DONE = (
    "this pass's round is done: it returns DONE naming what the round did and where the next round starts, which the watch quotes to the next"
    " pass, since drive status reads a pass's HANDOFF as stopped"
)
RELAUNCH_REFUSED = "relaunch_refused"
READ_UNANSWERED = "read_unanswered"
PROOFS_LOG = "proofs.log"
UNTHREADED = "no thread holds the order's lines to him, so its time was the drive's own and is said this once"
CUT_OFF = (
    f"its read stopped before it finished, so what it did and printed are unknown, its output in the drive's {READS_LOG}, and no read"
    " runs twice: read what a drive command changed, then ask again if it must run again"
)
LIVE_JOB_STATES = ("running", "waiting")
SAFEGUARDED = "stopped by its model's safeguards"
RELEASES_AT_ONCE = 2
QUIET_LANE = timedelta(minutes=30)
RECLAIMED = "reclaimed by the watch's drive job reclaim once its job was quiet"
LINE_BREAKS = re.compile(r"[\r\n]+")
HANDOFF = timedelta(minutes=2)
REPLY_READ = 100
SETTLING = ""
PR_LINE = re.compile(r"\s*(?:READY|PUSHED|PR:)")
JOB_LINE = re.compile(r"JOB (?P<job>j\d+) (?P<order>o\d+) ")
MESSAGE_LINE = re.compile(r"MESSAGE (?P<id>\S+): ")
MERGE_ENDS = re.compile(r"(?:READY|PUSHED|WAITING)\b")
UNKEPT_ASK = "keep it with `drive job ask {job}`, which draws his card on it, since no card draws for it until then"
ASK_FIELD = re.compile(r"^ASK:?[ \t]+([/~]\S*)[ \t]*$", re.MULTILINE)
PAUSED = "The recommended answer to this question is built and paused on your pick: tap it and the work ships; another pick rebuilds it first."
QUIET_FOR = timedelta(minutes=30)
UNSTAFFED_FOR = timedelta(minutes=5)
UNSTARTED_FOR = timedelta(minutes=1)
RELAUNCH = timedelta(seconds=orca.LAUNCH_SECONDS)
RECLAIM = timedelta(seconds=orca.RECLAIM_SECONDS)
TAIL_BYTES = 65536
COMPACT_PAST_TOKENS = 120_000
COMPACT_WHEN_QUIET = timedelta(minutes=10)
NOT_SLUG = re.compile(r"[^A-Za-z0-9]")
NAMED_JOB = re.compile(r"^[A-Z]+ (?:[^\s#:]+ )??(?P<job>j\d+)\b")
BUILD_FIELDS = (
    "state url message commit createdAt startedAt finishedAt blocks: jobs(first: 10, type: [BLOCK], state: [BLOCKED]) { edges { node { ... on JobTypeBlock { label } } } }"
    " failed: jobs(first: 20, type: [COMMAND], passed: false) { edges { node { ... on JobTypeCommand { label retried } } } }"
    " running: jobs(first: 20, type: [COMMAND], state: [RUNNING]) { edges { node { ... on JobTypeCommand { label startedAt } } } }"
    " steps: jobs(type: [COMMAND]) { count } done: jobs(type: [COMMAND], state: [FINISHED, CANCELED, TIMED_OUT, SKIPPED, BROKEN, EXPIRED]) { count }"
)
BUILD_ENDS = ("PASSED", "FAILED", "CANCELED", "BLOCKED", "SKIPPED", "NOT_RUN")
FOLLOW_ENDS = tuple(end for end in BUILD_ENDS if end != "BLOCKED")
ABOUT = re.compile(r"\[(?P<order>o\d+) (?P<job>j\d+)\]$|^[A-Z]+ (?:\S+ )?(?:j\d+ )?(?P<named>o\d+)\b")
MOVED_LINE = re.compile(r"MOVED (?:o\d+, )*o\d+ from (?P<lane>\S+) to ")


@dataclass
class Watcher:
    name: str
    offset: int
    project: str | None = None
    read_at: datetime | None = None
    failures: dict[str, int] = field(default_factory=dict)
    failure_said: dict[str, datetime] = field(default_factory=dict)
    page_shape: str | None = None
    cards: dict | None = None
    owed: set[str] | None = None
    merging: set[str] = field(default_factory=set)
    stale: set[str] = field(default_factory=set)
    unhookable: set[str] = field(default_factory=set)
    agent_gaps: dict[str, str | None] = field(default_factory=dict)
    made: list[str] = field(default_factory=list)
    unsent: dict[str, list[str]] = field(default_factory=dict)
    down: str | None = None
    dashboard_down: bool = False
    serving: dict[str, dict[str, bool]] | None = None
    fix_waits: dict[str, list[str]] | None = None
    production: set[str] = field(default_factory=set)
    live_at: datetime | None = None
    staged: set[str] | None = None
    live: list[dict] | None = None
    builds: dict[str, dict | None] = field(default_factory=dict)
    builds_heard: set[str] = field(default_factory=set)
    started: dict[str, datetime] = field(default_factory=dict)
    mail_read: set[str] = field(default_factory=set)
    mailbox_seen: int | None = None
    resumed: dict[str, str] = field(default_factory=dict)
    ci_marks: dict[str, str | None] = field(default_factory=dict)
    carried: dict[str, tuple[set[str], datetime]] = field(default_factory=dict)
    untracked: dict[str, str] = field(default_factory=dict)
    relayed: collections.deque[str] = field(default_factory=collections.deque)
    relay_error: Exception | None = None
    questions_taken: set[str] = field(default_factory=set)

    @property
    def lane(self) -> str:
        """The cci lane its lines go to: root for the drive's own watch, the project's sub-coordinator's for a project's."""
        return state.project_lane(self.project) if self.project else "root"


def hears(watcher: Watcher, data: dict, order_id: str) -> bool:
    return state.hears(data, data["orders"][order_id], watcher.project)


def heard_job(watcher: Watcher, data: dict, job: dict) -> bool:
    """Whether the watch hears a job, so one watch alone makes every line about it, by state.heard_job_lane."""
    return state.heard_job_lane(data, job) == watcher.lane


def heard_orders(watcher: Watcher, data: dict) -> dict[str, dict]:
    return {order_id: order for order_id, order in state.open_orders(data).items() if hears(watcher, data, order_id)}


def read_failed(watcher: Watcher, source: str, error: Exception, moment: datetime) -> list[str]:
    """Retry a failed read on the next poll; say so once it fails three times running, then at most every ten minutes."""
    if watcher.failures.get(source, 0) + 1 < FAILURES_BEFORE_SAYING:
        watcher.read_at = None
    return failed(watcher, source, f"{source} read", error, moment)


def failed(watcher: Watcher, source: str, what: str, error: Exception, moment: datetime) -> list[str]:
    failures = watcher.failures[source] = watcher.failures.get(source, 0) + 1
    if failures < FAILURES_BEFORE_SAYING:
        return []
    said = watcher.failure_said.get(source)
    if said and moment - said < FAILURE_EVERY:
        return []
    watcher.failure_said[source] = moment
    return [f"WATCH {what} failed {failures} times in a row: {error}"]


def ids(pr: dict) -> str:
    return f"[{pr['order']} {pr['job']}]"


def pr_changes(pr: dict, read: dict, entry: dict) -> list[str]:
    """What moved on the PR since the watch last read it, saved onto its row, its landing by github.live; ejected once Trunk put the trunk-failed label on entry, the read of the PR whose Trunk queue entry carries it, after the PR's lander started its try, since Trunk labels only the top PR its lander queued and leaves the label on through later pushes; its lander's try ends dequeued, out of the queue, once Trunk put the trunk-cancelled label on entry after that try started and ejected nothing, as when a stack-enqueue --dequeue took it out."""
    fresh = github.live(read, pr)
    changes = []
    if fresh["head"] != pr["head"] and pr["head"]:
        changes.append(f"new head {fresh['head'][:7]}")
    if fresh["checks"] != pr["checks"] and fresh["checks"] in ("pass", "fail"):
        changes.append("checks passed" if fresh["checks"] == "pass" else "checks failed")
    if fresh["review"] != pr["review"] and fresh["review"] in ("approved", "changes"):
        changes.append("approved" if fresh["review"] == "approved" else "changes requested")
    if fresh["conflict"] and not pr["conflict"]:
        changes.append(CONFLICTS)
    if fresh["state"] != pr["state"]:
        changes.append(github.fold_line(fresh) if fresh["state"] == github.FOLDED else fresh["state"])
    lander = pr["lander"]
    ejected = bool(lander and entry["ejected_at"]) and state.parse_time(entry["ejected_at"]) > state.parse_time(lander["at"])
    if ejected and not pr["ejected"]:
        changes.append("ejected from the merge queue")
    if lander and lander["outcome"] == land.QUEUED and not ejected and entry["cancelled_at"] and state.parse_time(entry["cancelled_at"]) > state.parse_time(lander["at"]):
        lander["outcome"] = land.DEQUEUED
        changes.append(land.DEQUEUED)
    pr.update(fresh, draft=read["isDraft"], ejected=ejected)
    return changes


def github_lines(watcher: Watcher, moment: datetime, polled: bool = False) -> list[str]:
    """Read from GitHub each heard unmerged PR the watch owes a read, and say what changed on each. It reads whole every one at a watch's start that no watch before it handed its owed reads to from the same GitHub query, by state.take_held_reads, so a field a new install reads fills on every row, then each a delivery named, each open one on a repository the receiver holds no hook for on each READ_EVERY pass, polled, ahead of that pass's stage_lines, since no delivery ever says it changed, each never asked for, each any waiting builder's PUSHED names unread since its take, whichever watch hears that builder, each folded into another open PR's branch, beside the PR it folded into, every pass until that PR lands or closes, and again each whose read GitHub refused. Of each open PR a push to its base named, and each whose mergeability GitHub had not worked out yet, since no delivery says when it has, it reads only the state, head and mergeability, by github.mergeability, saying a conflict that read starts, and reads whole in the same call each that read finds moved or ended. A PR GitHub answered nothing for stays stale, as its stage does, until a read answers for it. In the same call it reads each PR the drive does not track that carries a stopped builder's parts or that a live job keeps or waits on, by state.carrier_keys, once until GitHub answers for it and again each time a delivery names it, keeping only GitHub's state of it on the watcher for job_gap and out_because, and saying nothing of it. What it still owes is kept on the watcher only once the reads are saved, so a pass that ends before then hands the next watch the reads it owed. Once they are saved it redraws in place, by redraw_lines, his card on each PR it read that the read left drawn stale, so a read that turns a PR green takes "CI passes" off his card in the same pass, never at the next pass's redraw_lines minutes later."""
    data = state.load(watcher.name)
    pushed = {key for job_id, job in data["jobs"].items() if awaits_checks(job) for key in state.unread_since_take(data, job_id)}
    heard = [key for key in state.unmerged_pr_keys(data) if hears(watcher, data, data["prs"][key]["order"])]
    hookless = set(receiver.unhooked({github.repo_of(key) for key in heard})) if polled and not state.scratch() else set()
    owed = [
        key
        for key in heard
        if watcher.owed is None
        or key in watcher.owed
        or key in pushed
        or github.repo_of(key) in hookless and data["prs"][key]["state"] == "open"
        or data["prs"][key]["state"] == github.FOLDED
        or (data["prs"][key]["required"] is None and key not in watcher.stale)
    ]
    remerge = [key for key in heard if key in watcher.merging and key not in owed and key not in watcher.stale and data["prs"][key]["state"] == "open"]
    carried = [key for key in state.carrier_keys(data) if watcher.owed is None or key not in watcher.untracked or key in watcher.owed or watcher.untracked[key] == github.FOLDED]
    if not owed and not remerge and not carried:
        watcher.owed, watcher.merging = set(), set()
        return []
    merged, unmerged = github.mergeability(remerge) if remerge else ({}, None)
    moved = {key for key, read in merged.items() if read["state"] != "OPEN" or read["headRefOid"] != data["prs"][key]["head"]}
    keys = [*owed, *(key for key in remerge if key in moved)]
    reads, refused = github.fetch([*keys, *carried], {key: into for key in keys if (into := data["prs"][key]["into"])}) if keys or carried else ({}, None)
    carrying = {key: github.landing(reads.pop(key))["state"] for key in carried if key in reads}
    unread = set(keys) - set(reads)
    unanswered = set(remerge) - set(merged)
    owes = (unread | set(carried) - set(carrying) if refused else set()) | (set() if unmerged else unanswered)
    merging = (unanswered if unmerged else set()) | {key for key, read in reads.items() if read["mergeable"] == "UNKNOWN"} | {key for key, read in merged.items() if key not in moved and read["mergeable"] == "UNKNOWN"}
    stale = watcher.stale - set(reads) | unread
    failure = "; ".join(str(error) for error in (unmerged, refused) if error)
    lines = read_failed(watcher, "github", github.GithubError(failure), moment) if failure else []
    if not failure:
        watcher.failures.pop("github", None)
    foreign, redraws = {}, []
    if reads or merged:
        with state.edit(watcher.name) as data:
            for key, read in reads.items():
                pr = data["prs"][key]
                unlanded = pr["state"] in github.UNLANDED
                changes = pr_changes(pr, read, reads.get((pr["lander"] or {}).get("entry"), read))
                pr["required"]["read"] = state.stamp(moment)
                if why := state.foreign_pr(data, key):
                    foreign[key] = (pr["url"], pr["author"], f"{why} {ids(pr)}")
                    continue
                if edited_since_undrawn(pr, read, watcher.cards):
                    redraws.append(pr["url"])
                pr["updated"] = state.stamp(moment)
                if unlanded or pr["state"] == "open":
                    lines.extend(f"PR {key}: {change} {ids(pr)}" for change in changes)
            for key, read in merged.items():
                pr = data["prs"][key]
                if key in moved or (conflict := github.conflicts(read, pr)) == pr["conflict"]:
                    continue
                if conflict:
                    lines.append(f"PR {key}: {CONFLICTS} {ids(pr)}")
                pr.update(conflict=conflict, updated=state.stamp(moment))
    watcher.owed, watcher.merging, watcher.stale = owes, merging, stale | set(foreign)
    watcher.untracked |= carrying
    for url in redraws:
        try:
            slack.redraw_pr_cards(url)
        except slack.SlackError as error:
            lines.append(f"WATCH his open card for {url} still leaves off the Show me picture its body was edited to fix, since its redraw failed: {error}")
    if reads or merged:
        lines += redraw_lines(watcher, moment, {*reads, *merged} - {state.pr_key(url) for url in redraws})
    if ejected := [key for key in reads if data["prs"][key]["ejected"] and data["prs"][key]["lander"]["outcome"] == land.QUEUED]:
        lines += refused_lines(watcher, ejected, moment)
    return lines + dropped_lines(watcher, foreign, moment)


def dangerous_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Read in one github.paths call the changed files of each heard PR whose head no read of its files saw, by state.unmarked, while the repository's .claude/long-running.toml names a [[dangerous]] class, and put each whose head changes one on its staging test with a tester, by state.mark_dangerous, ahead of the stage_lines that draws his card on it; each tester added said as drive pr add says it. A PR registered before an install that brought this stage has no head read, so the first pass after it reads every open one."""
    data = state.load(watcher.name)
    if not state.repo_config(data).get("dangerous"):
        return []
    keys = [key for key in state.unmarked(data) if key not in watcher.stale and hears(watcher, data, data["prs"][key]["order"])]
    if not keys:
        return []
    try:
        read = github.paths(keys)
    except github.GithubError as error:
        return read_failed(watcher, "changed files", error, moment)
    watcher.failures.pop("changed files", None)
    with state.edit(watcher.name) as data:
        added = {key: tester for key, (head, paths) in read.items() if key in data["prs"] and (tester := state.mark_dangerous(data, key, head, paths))}
    return [f"PR {key}: {line} {ids(data['prs'][key])}" for key, tester in added.items() for line in render.builder_lines(data, [tester])]


def main_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Keep the tests each yasyf/* repository's main fails since a drive PR merged over a check main failed too, which the lander reads per check, never per test, each owned by the merge that broke it: once a heard merged PR's own run failed, each test its log names that main's last finished run before the merge passed, and no finished main run since has passed, is that PR's. Wake its builder to fix them forward, at once and again every 30 minutes while main fails one. A test clears once the newest finished main run created since its merge passes it, read again each time the receiver hears a run complete on main."""
    data = state.load(watcher.name)
    grades = [key for key, pr in data["prs"].items() if state.grading(key, pr) and hears(watcher, data, pr["order"])]
    marks = {repo: mark for repo, owned in data["mains"].items() if owned and (mark := receiver.heard(repo, "ci")) != watcher.ci_marks.get(repo)}
    if not (grades or marks or main_wakes(watcher, data, moment)):
        return []
    try:
        broke = {key: broken(github.repo_of(key), data["prs"][key]) for key in grades}
        passed = {repo: passing(repo, data["mains"][repo]) for repo in marks}
    except github.GithubError as error:
        return read_failed(watcher, "github main runs", error, moment)
    watcher.failures.pop("github main runs", None)
    watcher.ci_marks |= marks
    with state.edit(watcher.name) as data:
        for repo, tests in passed.items():
            for test in tests:
                data["mains"][repo].pop(test, None)
        for key, found in broke.items():
            data["prs"][key]["graded"] = True
            data["mains"].setdefault(github.repo_of(key), {}).update({test: failure | {"owner": key, "woke": None} for test, failure in found.items()})
        wakes = main_wakes(watcher, data, moment)
        for repo, owner, tests in wakes:
            for test in tests:
                data["mains"][repo][test]["woke"] = state.stamp(moment)
        texts = [(owner, main_fix(repo, owner, data["mains"][repo], tests)) for repo, owner, tests in wakes]
    for owner, text in texts:
        stage.wake(watcher.name, owner, text)
    return []


def broken(repo: str, pr: dict) -> dict[str, dict]:
    """The tests a merged PR broke on main: each its own run failed at its head that main's last finished run before the merge passed and no finished main run since has passed, with that run's link, its workflow and when the merge landed; none once its run passed."""
    if pr["checks"] != "fail":
        return {}
    failures = install.head_failures(repo, pr["head"])
    since = install.merged_at(repo, pr["merged_sha"]) if failures else ""
    found = {}
    for workflow in {failure["workflow"] for failure in failures.values()}:
        before = install.main_failures(repo, workflow, f"<{since}") or []
        after = install.main_failures(repo, workflow, f">={since}")
        found |= {test: failure | {"since": since} for test, failure in failures.items() if failure["workflow"] == workflow and test not in before and (after is None or test in after)}
    return found


def passing(repo: str, owned: dict[str, dict]) -> set[str]:
    """The owned tests main passes now: each the newest finished main run of its workflow created since its owner merged passed."""
    groups: dict[tuple[int, str], set[str]] = {}
    for test, kept in owned.items():
        groups.setdefault((kept["workflow"], kept["since"]), set()).add(test)
    return {test for (workflow, since), tests in groups.items() if (failing := install.main_failures(repo, workflow, f">={since}")) is not None for test in tests - set(failing)}


def main_wakes(watcher: Watcher, data: dict, moment: datetime) -> list[tuple[str, str, list[str]]]:
    """Each heard merged PR owing a wake for the tests it broke on its repository's main, with those tests: one owning a test whose builder was never woken for it, or last woken 30 minutes ago."""
    owners: dict[tuple[str, str], list[str]] = {}
    for repo, owned in data["mains"].items():
        for test, kept in owned.items():
            owners.setdefault((repo, kept["owner"]), []).append(test)
    return [
        (repo, owner, sorted(tests))
        for (repo, owner), tests in owners.items()
        if hears(watcher, data, data["prs"][owner]["order"]) and any(not (woke := data["mains"][repo][test]["woke"]) or moment - state.parse_time(woke) >= stage.EVERY["red"] for test in tests)
    ]


def main_fix(repo: str, owner: str, owned: dict[str, dict], tests: list[str]) -> str:
    runs = ", ".join(sorted({owned[test]["run"] for test in tests}))
    return f"{owner}: {repo}'s main fails {', '.join(tests)} since it merged, as its own run {runs} failed them; fix them forward in a PR of your own from a fresh main, since no other builder is asked to"


def refused_lines(watcher: Watcher, keys: list[str], moment: datetime) -> list[str]:
    """Record on every PR it queued GitHub's refusal of the merge Trunk's queue took for each try Trunk ejected one of keys from, by Trunk's queue records, which its landing event says; a run of failed reads of them is said."""
    try:
        outcomes = land.refusals(state.load(watcher.name), keys)
    except state.DriveError as error:
        return failed(watcher, "trunk", "Trunk queue read", error, moment)
    watcher.failures.pop("trunk", None)
    if outcomes:
        with state.edit(watcher.name) as data:
            land.refuse(watcher.name, data, outcomes)
    return []


def edited_since_undrawn(pr: dict, read: dict, cards: dict | None) -> bool:
    """Whether his open card left a Show me picture of the PR, or how its stored data moves, off and GitHub reads its body edited since the read that card drew, so a redraw in place draws what the edit fixed."""
    if not (left := pr["undrawn"]) or cards is None or not view.card_for(cards["pr_cards"], pr["url"]):
        return False
    return bool(edited := read["lastEditedAt"]) and state.parse_time(edited) > state.parse_time(left["at"])


def pushed_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Settle each builder's PUSHED the watch hears once its GitHub read after the take finds the required checks of every PR it names: all green takes the READY it holds, its JOB line waking whoever acts on a READY; a red one ends the hold with a `READY refused` line per failed check, which goes back to a remote lane waiting for its next event and wakes the coordinator otherwise. A PUSHED whose PRs all merged settles nothing and says no READY, since finished takes the merge's finish on it."""
    data = state.load(watcher.name)
    if not any(settles(watcher, data, job_id) for job_id in data["jobs"]):
        return []
    lines = []
    with state.edit(watcher.name) as data:
        for job_id, job in data["jobs"].items():
            if not (verdict := settles(watcher, data, job_id)):
                continue
            result, held = verdict
            if result == "pass":
                state.take_result(data, job_id, "READY" + job["said"].removeprefix("PUSHED"), state.report_text(watcher.name, job_id))
                lines.append(f"JOB {job_id} {job['order']} {job['said']}{stage.heads(data, job['said'], job['order'], watcher.cards)}")
                continue
            job.update(said="READY refused: " + "; ".join(f"{key}'s {why}" for key, why in held), updated=state.stamp(moment))
            lines += [f"PR {key}: READY refused, its {why} [{job['order']} {job_id}]" for key, why in held]
    return lines


def settles(watcher: Watcher, data: dict, job_id: str) -> tuple[str, list[tuple[str, str]]] | None:
    if not pushing(watcher, data, job_id) or merged_ready(data, job_id):
        return None
    verdict = state.pushed_verdict(data, job_id)
    return verdict if verdict[0] != "pending" else None


def pushing(watcher: Watcher, data: dict, job_id: str) -> bool:
    """Whether a builder's PUSHED the watch hears waits on the checks of the PRs it names."""
    job = data["jobs"][job_id]
    return awaits_checks(job) and heard_job(watcher, data, job)


def awaits_checks(job: dict) -> bool:
    """Whether a builder waits on a PUSHED, which waits on the checks of the PRs it names, whichever watch hears it."""
    return job["kind"] == "builder" and job["state"] == "waiting" and bool(state.PUSHED_LINE.match(job["said"] or ""))


def dropped_lines(watcher: Watcher, foreign: dict[str, tuple[str, str, str]], moment: datetime) -> list[str]:
    """Stop tracking each PR someone else opened that no order of his it serves names, with nothing done on GitHub: each open card of his carrying it withdrawn first, saying who opened it, then the PR dropped from the drive; one whose withdrawal fails stays, refused everywhere, and goes again next read."""
    lines, gone = [], []
    for key, (url, author, why) in foreign.items():
        try:
            withdrawn = slack.withdraw_pr_card(url, f"Dropped from the drive: {author} opened it, and the drive tracks no one else's PR unless your order names it")
        except slack.SlackError as error:
            lines += failed(watcher, "foreign drop", "withdrawal of a dropped PR's card", error, moment)
            continue
        watcher.failures.pop("foreign drop", None)
        gone.append(key)
        lines += [f"DROPPED {key}: {why}", *(f"CARD {link} withdrawn: {key} is dropped" for link in withdrawn)]
    if gone:
        with state.edit(watcher.name) as data:
            for key in gone:
                state.drop_pr(data, key)
    return lines


def bare(wait: str) -> str:
    return wait.split("?")[0]


def waits_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Drop each wait of a job or PR that ended, his card answered, the job finished, the release out by the watch's release live read or its PR closed unmerged, the Buildkite build finished or stopped at an approve step by the watch's Buildkite read, a process on this Mac ended, a teammate's reply come, a builder slot on this Mac given to the earliest due builder waiting its turn, one per slot no builder holds, or a once-only pass of state.shaped ended it, each by the reason that pass handed by state.drop_waits, forgotten as this edit saves, and say once, as its last wait ends, that nothing holds it, holding back the job's ORPHAN for 30 minutes, but for a builder's READY or PUSHED whose PRs all merged, which the watch finishes next in the same pass, or else says once as an ORPHAN naming who finishes it; a builder given its slot waits on nothing with no agent yet, its slot stamped with when its turn came, and holds that slot until its agent starts, and one moving here from its Sprite, or taking over here from its agent on this Mac, keeps its other waits, its slot stamped, while drive job launch <j> --local, run by start_verb, moves it, a refusal of that launch reaching the watch's lines as its relaunch_refused event; a Sprite job busy reads another drive verb acting on takes its turn on a pass after that verb ends. The cards a job asked him on hold it too, so it keeps its waits until he answers them. A codex job whose process ended holds the text exited_text writes, which carry_held's launch of its next codex-ask run carries, so its FREE line asks nobody to launch it. A release wait is a wake the job set, freed by out_because once every cluster of its service, or the one env it names, serves the PR's merge, and never comes back."""
    data = state.load(watcher.name)
    out, lines = replies(watcher, data, moment)
    out |= shipped(watcher, data)
    turns, held = place.turns(data, moment)
    turned = {job_id for job_id in turns if heard_job(watcher, data, data["jobs"][job_id]) and not (place.moves_here(data["jobs"][job_id]) and busy(watcher, job_id, moment))}
    owners = [
        *(job for job in data["jobs"].values() if heard_job(watcher, data, job) and not view.open_cards(job)),
        *(pr for pr in data["prs"].values() if hears(watcher, data, pr["order"])),
    ]
    dropped = any(heard_job(watcher, data, job := data["jobs"][job_id]) and not view.open_cards(job) for job_id in data["dropped_waits"])
    if not out and not turned and not dropped and all(view.holding(data, owner["waits"], watcher.cards) == owner["waits"] for owner in owners):
        return lines
    slot = {state.SLOT_WAIT: f"its turn came as a builder slot opened on this Mac, {held} of its {place.LOCAL_BUILDERS} held"}
    moves = []
    with state.edit(watcher.name) as data:
        for job_id, job in data["jobs"].items():
            if not heard_job(watcher, data, job):
                continue
            if job_id in turned and place.moves_here(job):
                job["waits"] = [wait for wait in job["waits"] if wait != state.SLOT_WAIT]
                job.update(slot=state.stamp(moment), updated=state.stamp(moment))
                moves.append(job_id)
            if view.open_cards(job):
                continue
            turn = job_id in turned and job["state"] == "waiting" and state.SLOT_WAIT in job["waits"]
            waited = job["waits"]
            ended = released(data, job, job_id, watcher.cards, out | data["dropped_waits"].pop(job_id, {}) | (slot if turn else {}))
            exited = [wait for wait in waited if state.PID_WAIT.fullmatch(wait) and wait not in job["waits"]] if job["kind"] == "codex" else []
            if exited:
                state.hold_text(job, exited_text(watcher.name, job_id, exited), state.WATCH_SENDER)
            if ended and job["state"] not in state.FINISHED_JOB_STATES and not (watch_proves(data, job_id) or at_merge(data, job_id) or judging(data, job_id)):
                job["updated"] = state.stamp(moment)
                if turn:
                    job["slot"] = state.stamp(moment)
                for order_id in [job["order"], *job["carries"]]:
                    parts = data["orders"][order_id]["parts"]
                    for part in parts.keys() & set(state.job_parts(job, order_id)):
                        parts[part].update(orphan_said=state.stamp(moment), orphan_gap=state.FREED)
                built = f", built on {job['branch']}" if job["state"] == "built" else ""
                unlaunched = (
                    f", its codex-ask run ended: drive job launch {job_id} starts its next"
                    if job["kind"] == "codex" and job.get("codex_run")
                    else f", not started yet: drive job launch {job_id} starts it"
                    if job["kind"] == "codex" or (job.get("where") == "remote" and not job.get("dispatch"))
                    else ""
                )
                unstarted = brief_first(job)
                if job["undelivered"] and carrier(job_id, job, data, watcher.cards):
                    his = ", his answers among them" if any(held["sender"] == state.HIM for held in job["undelivered"]) else ""
                    unlaunched, unstarted = ", and the watch now starts its next codex-ask run carrying its last report" if exited else f", and the watch now carries it the texts it holds{his}", ""
                lines.append(f"FREE {job_id} {job['order']}: {ended}, nothing else holds it{built}{unlaunched}{unstarted}")
        for key, pr in data["prs"].items():
            if hears(watcher, data, pr["order"]) and (ended := released(data, pr, pr["job"], watcher.cards, out)) and pr["state"] == "open":
                lines.append(f"FREE {key} {pr['job']} {pr['order']}: {ended}, nothing else holds it")
    for job_id in moves:
        start_verb(watcher, job_id, ["job", "launch", job_id, "--local"], moment)
    return lines


def exited_text(name: str, job_id: str, exited: list[str]) -> str:
    """The text a codex job holds once a process it waited on ended, which carry_held's launch of its next codex-ask run carries under From the watch since:, since that run reads only its brief: which process ended and the job's last report."""
    ended = f"The process on this Mac {job_id} waited on ended, crashed or finished: {', '.join(wait.partition('@')[0] for wait in exited)}."
    report = state.report_text(name, job_id, every=False).strip()
    return f"{ended} Your last report:\n{report}" if report else ended


def build_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Read from Buildkite, for waits_lines and status_lines, each build a wait the watch follows names or a part of an order it hears follows: once as it is first held, then again only once a delivery of Buildkite's to the receiver's /buildkite route names it. One `tools/ci bk graphql` call reads them all, spending go/ci's shared Buildkite budget, run in the drive's checkout as orca.checkout moves it to its default branch's head, and each answer is kept on the watcher as builds while the build is held, None for a build Buildkite answers none for; a read that fails is said as a failed read is and owed again next pass."""
    data = state.load(watcher.name)
    held = sorted({wait for wait in followed(watcher, data) if github.BUILD.fullmatch(wait)} | state.followed_builds(heard_orders(watcher, data)))
    watcher.builds = {link: read for link, read in watcher.builds.items() if link in held}
    watcher.builds_heard &= set(held)
    if not (due := [link for link in held if link not in watcher.builds or link in watcher.builds_heard]):
        return []
    slugs = [f"{found['pipeline']}/{found['number']}" for found in map(github.BUILD.fullmatch, due)]
    query = "{ " + " ".join(f"b{at}: build(slug: {json.dumps(slug)}) {{ {BUILD_FIELDS} }}" for at, slug in enumerate(slugs)) + " }"
    try:
        checkout = orca.checkout(watcher.name, data["roles"]["coordinator"]["cwd"])
        read = json.loads(reads.run([str(checkout / "tools" / "ci"), "bk", "graphql", query], checkout))
    except (state.DriveError, reads.ReadError) as error:
        return read_failed(watcher, "buildkite builds", error, moment)
    watcher.failures.pop("buildkite builds", None)
    watcher.builds |= {link: read[f"b{at}"] for at, link in enumerate(due)}
    watcher.builds_heard -= set(due)
    return []


def build_said(link: str, read: dict | None) -> str | None:
    """What a build did, as a Buildkite read found it: its state once it ended or blocked, the approve step it stopped at and, unless it passed, each step that did not pass; None while it runs."""
    if read is None:
        return f"Buildkite answers no build at {link}"
    if read["state"] not in BUILD_ENDS:
        return None
    blocks, failed = approve_steps(read), unpassed_steps(read)
    said = f"{link} reads {read['state']}" + (f" at its approve step {', '.join(blocks)}" if blocks else "")
    return said + (f", its steps that did not pass {', '.join(failed)}" if failed and read["state"] != "PASSED" else "")


def approve_steps(read: dict) -> list[str]:
    return [f'"{edge["node"]["label"]}"' for edge in read["blocks"]["edges"]]


def unpassed_steps(read: dict) -> list[str]:
    return list(dict.fromkeys(f'"{edge["node"]["label"]}"' for edge in read["failed"]["edges"] if not edge["node"]["retried"]))


def status_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Keep his thread current on each Buildkite build a part of an open order the watch hears follows, by his pick "A live status message: one message in your thread, rewritten each minute with the running step, then an end line; one Buildkite read a minute": from the newest read build_lines holds, which Buildkite's deliveries to the receiver renew, one status message posted in the thread of his ask and rewritten whenever its words change, its running steps' minutes moving each pass, so no session's turn keeps it current and the follow makes no Buildkite read of its own; once the build ends, a report the handler the watch starts posts as the end line, which delivers the part. A failed post, write or start is said as a failed read is and tried again next pass, a start with the same report."""
    lines = []
    for order_id, order in heard_orders(watcher, state.load(watcher.name)).items():
        for part, held in order["parts"].items():
            if not (follow := held["follow"]) or not follow["record"] and (follow["ended"] or follow["build"] not in watcher.builds):
                continue
            source = f"follow {order_id} {follow['build']}"
            try:
                if seq := follow["record"] or status(watcher, order_id, order, part, follow, moment):
                    agents.handle_report(watcher.name, seq, f"Record: {seq} Drive: {watcher.name} Scratch: {state.drive_dir(watcher.name) / 'scratch' / f'follow-{seq}'}")
            except (slack.SlackError, reads.ReadError, state.DriveError) as error:
                lines += failed(watcher, source, f"follow of {follow['build']} for {order_id}", error, moment)
                continue
            watcher.failures.pop(source, None)
            if seq:
                with state.edit(watcher.name) as data:
                    data["orders"][order_id]["parts"][part]["follow"]["record"] = None
    return lines


def status(watcher: Watcher, order_id: str, order: dict, part: str, follow: dict, moment: datetime) -> str | None:
    """Post the status message of the build a part follows, or rewrite it when its words changed, and once the build ended write the report owing its end line, kept on the follow until a handler has it; that report's seq, None while the build runs."""
    from drive import talk

    if (read := watcher.builds[follow["build"]]) is None:
        raise state.DriveError(f"Buildkite answers no build at {follow['build']}")
    words = status_words(read, moment)
    message = follow["message"] or talk.posted(watcher.name, {"text": words, "thread_ts": follow["thread"]})["permalink"]
    if follow["message"] and words != follow["words"]:
        talk.posted(watcher.name, {"edit": message, "text": words})
    kept = {"message": message, "words": words}
    if read["state"] in FOLLOW_ENDS:
        kept |= {"ended": state.stamp(moment), "record": end_report(watcher, order_id, order, part, follow | kept, read)}
    with state.edit(watcher.name) as data:
        data["orders"][order_id]["parts"][part]["follow"].update(kept)
    return kept.get("record")


def status_words(read: dict, moment: datetime) -> str:
    """A followed build's status message: its title linked to it, then each step running with its minutes, or the approve step it stopped at, or once it ended its state, minutes and the steps that did not pass, then how many of its steps are done, such as "Staging release plangent-macdiarmid: Watch the tenant smoke on plat-usw2-staging, 4 min; 120 of 136 steps done"."""
    title = cut(first_line(read["message"] or "") or "the build", 80).translate(refs.LINK_WORDS)
    link = f"<{read['url']}|{title}>"
    tally = f"{read['done']['count']} of {read['steps']['count']} steps done"
    if read["state"] in FOLLOW_ENDS:
        took = f" after {minutes(state.parse_time(read['finishedAt']) - state.parse_time(read['startedAt'] or read['createdAt']))} min" if read["finishedAt"] else ""
        unpassed = unpassed_steps(read) if read["state"] != "PASSED" else []
        return f"{link}: {read['state'].lower().replace('_', ' ')}{took}; {tally}" + (f"; did not pass: {', '.join(unpassed)}" if unpassed else "")
    if blocks := approve_steps(read):
        return f"{link}: stopped at its approve step {', '.join(blocks)}; {tally}"
    running = [f"{edge['node']['label']}, {minutes(moment - state.parse_time(edge['node']['startedAt']))} min" for edge in read["running"]["edges"]]
    return f"{link}: {'; '.join(running) or read['state'].lower()}; {tally}"


def end_report(watcher: Watcher, order_id: str, order: dict, part: str, follow: dict, read: dict) -> str:
    """Write the report owing him the end line of the build a part of the order follows, for the handler that posts it in the thread of his ask, where the line delivers the part; its seq."""
    facts = [
        f"His order {order_id}: {order['text']}",
        f"His ask, the part the end line delivers: {part}",
        f"The build: {follow['build']}, {read['state']}, at commit {read['commit']}" + (f", its steps that did not pass {', '.join(unpassed)}" if (unpassed := unpassed_steps(read)) else ""),
        f"Its status message, which the watch kept current: {follow['message']}, now reading: {follow['words']}",
        f"Read the build's failed steps and the jobs on {order_id}, then post one line in that thread: how the build ended and what his ask wants of its end, such as how confident we are to release the same commit further.",
    ]
    record = {
        "kind": "report",
        "topic": f"follow-{order_id}",
        "attr": {"order": order_id, "part": f"{order_id}:{part}", "thread": follow["thread"]},
        "text": f"POST the build {order_id} follows ended {read['state']}: post its end line in the thread of his ask",
        "body": "\n".join(facts) + "\n",
    }
    [seq] = reported(watcher, [record])
    return seq


def build_line(watcher: Watcher, link: str) -> str:
    """A build wait as the watch's last Buildkite read found it."""
    if link not in watcher.builds:
        return f"{link}: the watch holds no Buildkite read of it, as its read failed or the build is not held yet"
    read = watcher.builds[link]
    return build_said(link, read) or f"{link} reads {read['state']} at the watch's last Buildkite read, which it makes again once a delivery of Buildkite's to the receiver's {receiver.BUILDKITE_PATH} route names it"


def monitors(data: dict, order_id: str) -> dict[str, str]:
    """The link of each Datadog monitor recorded on the orders of an order's incident, by its id."""
    links = (link for other in state.feature_orders(data, order_id) for link in data["orders"][other]["alerts"])
    return {found[1]: link for link in links if (found := state.DATADOG_MONITOR.fullmatch(link))}


def followed(watcher: Watcher, data: dict) -> list[str]:
    """The waits of each live job or open PR whose order the watch hears."""
    return [wait for order_id, owner in release_owners(data) if hears(watcher, data, order_id) for wait in owner["waits"]]


def serving_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Read which clusters serve the merged Forge-AI/monorepo PR of each release wait the watch follows and the soak commit of each staging wait, for waits_lines, and of each component in the releases release_reach names for each fix PR of an open incident, for recovered_lines: one `tools/ci release live --json`, run in the drive's checkout as orca.checkout moves it, at most once every LIVE_EVERY, since it takes most of a minute, prints the commit each cluster of every service serves, production or not, a stack whose switch never passed serving its older build. Kept on the watcher as serving until the next read, each wait's clusters by whether each serves a commit carrying the PR's merge, by github.carrier's read of the checkout's own history, or, for a staging wait, the soak commit itself or a build carrying it by the same read, so a promotion of dev holding a merge frees a wait on that merge commit while a soak commit off dev's line still frees only at itself, a service no cluster runs reading none, each fix PR's waits as fix_waits, both None while the read fails, and as production the clusters it read that go/ci counts as production, by settled.prod."""
    from drive import cli

    data = state.load(watcher.name)
    merged = [
        wait
        for wait in followed(watcher, data)
        if (found := state.RELEASE_WAIT.fullmatch(wait)) and data["prs"][found["pr"]]["merged_sha"] and github.repo_of(found["pr"]).lower() == state.MONOREPO.lower()
    ]
    soaks = {wait: found for wait in followed(watcher, data) if (found := state.STAGING_WAIT.fullmatch(wait))}
    fixes = sorted({key for keys in incident_fixes(watcher, data).values() for key in keys})
    if not merged and not soaks and not fixes:
        watcher.serving, watcher.fix_waits = {}, {}
        return []
    if watcher.live_at and moment - watcher.live_at < LIVE_EVERY:
        return []
    watcher.serving = watcher.fix_waits = None
    try:
        checkout, live = live_rows(watcher, data)
        reach = cli.release_reach(watcher.name, data, fixes) if fixes else {}
        fix_waits = {key: [f"release:{component}:{key}" for component in reach[key]["releases"]] for key in fixes}
        carries = github.carrier(str(checkout))
        production = {env for env in {row["env"] for row in live} if settled.prod(env, str(checkout))}
        serving = {
            wait: {row["env"]: carries(row["commit"], data["prs"][found["pr"]]["merged_sha"], github.split(data["prs"][found["pr"]]["into"] or found["pr"])[2]) for row in live if row["target"] == found["service"]}
            for wait in dict.fromkeys([*merged, *(wait for waits in fix_waits.values() for wait in waits)])
            if (found := state.RELEASE_WAIT.fullmatch(wait))
        } | {
            wait: {row["env"]: row["commit"].startswith(found["commit"]) or carries(row["commit"], found["commit"], 0) for row in live if row["target"] == found["target"]}
            for wait, found in soaks.items()
        }
    except (state.DriveError, reads.ReadError, github.GithubError) as error:
        return read_failed(watcher, "release live", error, moment)
    watcher.serving = serving
    watcher.fix_waits, watcher.production, watcher.live_at = fix_waits, production, moment
    watcher.failures.pop("release live", None)
    return []


def live_rows(watcher: Watcher, data: dict) -> tuple[Path, list[dict]]:
    """The drive's checkout, as orca.checkout moves it, and the rows of one `tools/ci release live --json` run there, a commit each cluster of every service serves, keeping them on the watcher as live for staging_line, and as staged the targets state.STAGING_ENV runs for add_prover."""
    checkout = orca.checkout(watcher.name, data["roles"]["coordinator"]["cwd"])
    live = json.loads(reads.run([str(checkout / "tools" / "ci"), "release", "live", "--json"], checkout))
    watcher.live = live
    watcher.staged = {row["target"] for row in live if row["env"] == state.STAGING_ENV}
    return checkout, live


def incident_fixes(watcher: Watcher, data: dict) -> dict[str, list[str]]:
    """The fix PRs of each open incident the watch hears, by its head order, once no job on a fix part of its orders still works, no PR of one is open and each is a merged Forge-AI/monorepo PR."""
    fixes = {}
    for head in state.open_incidents(data, list(data["orders"])):
        if data["orders"][head]["joins"] or data["orders"][head]["recovered"] or not hears(watcher, data, head):
            continue
        jobs = {job_id: job for order_id in state.feature_orders(data, head) for job_id, job in state.part_jobs(data, order_id, state.INCIDENT_FIX)}
        prs = [key for key, pr in data["prs"].items() if pr["job"] in jobs and pr["state"] != "closed"]
        if (
            prs
            and all(data["prs"][key]["state"] == "merged" and key.split("#")[0].lower() == state.MONOREPO.lower() for key in prs)
            and all(job["state"] in state.FINISHED_JOB_STATES for job in jobs.values())
        ):
            fixes[head] = prs
    return fixes


def recovered_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Close each open incident the watch hears once its fix is live, every Datadog monitor recorded on it reads OK, one with no monitor on its fix alone, and each of its orders that builds something he uses keeps a proof from there, by brief.unseen, or else say so once by proof_owed: what is left of it moves to a plain order, and a POST report hands the communicator the Live line for its thread, whose handler closes each order of it. Its fix is live when incident_fixes names its PRs and the watch's last release live read, by serving_lines, has it in production for each component it touches, by in_production, one touching none live at its merge; a fix PR outside Forge-AI/monorepo has no such read, so the coordinator closes its incident. The monitors of the incidents whose fix is live are read in one search."""
    if not watcher.fix_waits:
        return []
    data = state.load(watcher.name)
    fixes = incident_fixes(watcher, data)
    landed = {head: monitors(data, head) for head, keys in fixes.items() if all(key in watcher.fix_waits and all(in_production(watcher, wait) for wait in watcher.fix_waits[key]) for key in keys)}
    lines, ok = [], set()
    if ids := sorted({monitor for found in landed.values() for monitor in found}):
        argv = ["pup", "--no-agent", "monitors", "search", "--query", f"id:({' OR '.join(ids)})", "--per-page", str(len(ids)), "--jq", ".monitors | map({id, status})"]
        try:
            statuses = json.loads(reads.run(argv, orca.checkout(watcher.name, data["roles"]["coordinator"]["cwd"])))
        except (state.DriveError, reads.ReadError) as error:
            lines = read_failed(watcher, "datadog monitors", error, moment)
        else:
            watcher.failures.pop("datadog monitors", None)
            ok = {str(monitor["id"]) for monitor in statuses if monitor["status"] == "OK"}
    for head, found in landed.items():
        if not found.keys() <= ok:
            continue
        fix = f"{', '.join(map(state.pr_url, fixes[head]))} live in production"
        group = state.open_incidents(data, state.feature_orders(data, head))
        if unseen := {order_id: surface for order_id in group if (surface := brief.unseen(data, data["orders"][order_id], data["orders"][order_id]["proof"]))}:
            lines += proof_owed(watcher, head, unseen, fix, moment)
            continue
        lines += closed(watcher, head, list(found.values()), fix, moment)
    return lines



def in_production(watcher: Watcher, wait: str) -> bool:
    """Whether a fix PR runs in production for one component, by his pick "Read each fix PR's production landing each pass, no latch": the watch's last release live read has the component on a cluster, and each of its clusters go/ci counts as production serves the merge, whatever staging, dev and sandbox run, as go/ci's liveInProduction reads it."""
    clusters = watcher.serving.get(wait)
    return bool(clusters) and all(serves for env, serves in clusters.items() if env in watcher.production)


def proof_owed(watcher: Watcher, head: str, unseen: dict[str, str], fix: str, moment: datetime) -> list[str]:
    """Say once that an incident whose fix is live stays open until each order of it that builds something he uses keeps a recording or live post from there, naming the builder that proves it and builds nothing, whose DONE keeps that proof on each order of the incident, as state.take_result does."""
    with state.edit(watcher.name) as data:
        if data["orders"][head]["prove_said"]:
            return []
        data["orders"][head]["prove_said"] = state.stamp(moment)
    surfaces = " and ".join(dict.fromkeys(unseen.values()))
    goal = f"prove {fix.removesuffix(' live in production')} live on {surfaces} with a recording of it"
    add = shlex.join(["drive", "job", "add", "builder", "--builds-nothing", "--order", head, "--part", state.INCIDENT_FIX, "--goal", goal])
    return [
        f"PROVE {head}: {fix}, but {', '.join(unseen)} builds what he uses on {surfaces}, so its Live line waits on a recording or live post"
        f" from there: add the builder that proves it, {add}, brief it with --native and --interactions for {surfaces} and start it; its DONE"
        f" keeps that recording as the proof of {', '.join(unseen)}, and the watch then closes {head}"
    ]


def page_owner(name: str, data: dict, title: str, why: str) -> str:
    """Page his own Datadog On-Call user at high urgency with `tools/ci oncall page me` in the drive's checkout, which Platy answers for the release operator this Mac signs in as, so the drive holds no Datadog credential and can page nobody else; go/ci's line naming the page."""
    checkout = orca.checkout(name, data["roles"]["coordinator"]["cwd"])
    argv = [str(checkout / "tools" / "ci"), "oncall", "page", "me", "--title", cut(title, 80), "--description", f"{why}\n{dashboard.link(name)}"]
    return state.unrehearsed(name, lambda: reads.run(argv, checkout).strip(), "<page>")


def owner_page_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Page him once for each incident order the watch hears that opened since he was last heard and is open while he is not present, by state.absence, the presence the landing gate reads, and by his words "if anything goes wrong while doing things autonomously, the agent should have a tool to use ddog to page its human"; one that fails is said as a failed read is and tried again next pass."""
    data = state.load(watcher.name)
    if not state.absence(data, moment):
        return []
    heard = data["owner"]["heard"]
    owed = [
        key
        for key in state.open_incidents(data, list(data["orders"]))
        if not data["orders"][key]["paged"] and (not heard or state.parse_time(data["orders"][key]["created"]) > state.parse_time(heard)) and hears(watcher, data, key)
    ]
    lines = []
    for key in owed:
        order = data["orders"][key]
        try:
            paged = page_owner(watcher.name, data, f"{key} {order['title'] or order['text']}", render.order_line(data, key, order))
        except (state.DriveError, reads.ReadError) as error:
            return lines + failed(watcher, "owner page", "page of his Datadog On-Call user", error, moment)
        watcher.failures.pop("owner page", None)
        with state.edit(watcher.name) as edited:
            edited["orders"][key]["paged"] = state.stamp(moment)
        lines.append(f"WATCH paged him for incident {key} while he is away: {paged}")
    return lines


def close_report_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Post in one cci batch each close report drive job prove, or the watch's DONE at a merge, keeps on an open order it finished, for the communicator's handler to post in the order's thread, or at his DM root for an order of his with none, and close it; each stays on its order until cci takes it, so a post that fails goes again next read, said from its third failure in a row, and a watch on a newer install posts it as well. One an older install kept on a drive tooling order a session opened, which posts him no line, goes nowhere: that order closes done on the proof it holds, as install.finish now closes one."""
    data = state.load(watcher.name)
    closing = {order_id: order for order_id, order in heard_orders(watcher, data).items() if order["close_report"]}
    if unasked := [order_id for order_id, order in closing.items() if state.unasked_tooling(order)]:
        with state.edit(watcher.name) as data:
            for order_id in unasked:
                data["orders"][order_id].update(close_report=None, state="done", updated=state.stamp(moment))
    owed = {order_id: order["close_report"] for order_id, order in closing.items() if order_id not in unasked}
    if not owed:
        return []
    try:
        reported(watcher, list(owed.values()))
    except state.DriveError as error:
        return failed(watcher, "close reports", "close report post to the communicator", error, moment)
    watcher.failures.pop("close reports", None)
    with state.edit(watcher.name) as data:
        for order_id in owed:
            data["orders"][order_id]["close_report"] = None
    return []


def reported(watcher: Watcher, records: list[dict]) -> list[str]:
    """Post the watch's reports in one cci batch, each addressed to the communicator, whose handlers post them, so no coordinator's or the meta session's channel carries one, and mark each order a report closes closed on its seq unless another report closes it already, so second_close refuses every later close of it; their seqs."""
    from drive import tools

    seqs = tools.sent(watcher.name, LANE, [record | {"to": [f"communicator@{watcher.name}"]} for record in records])
    if closes := [(order_id, seq) for record, seq in zip(records, seqs, strict=True) for order_id in state.closed_orders(record.get("attr") or {})]:
        with state.edit(watcher.name) as data:
            for order_id, seq in closes:
                data["orders"][order_id]["closed_on"] = data["orders"][order_id]["closed_on"] or seq
    return seqs


def closed(watcher: Watcher, head: str, links: list[str], fix: str, moment: datetime) -> list[str]:
    """Close an incident that is over: its live work moves to a plain order and each of its orders keeps the recovery of its monitors, when it has any, and its fix live as its proof, then a POST report, naming each PR of the incident as the watch last read it from GitHub, names each order for the communicator's handler to post in its thread and close; a post that fails goes again next read."""
    read_ok = f"{', '.join(links)} read OK"
    with state.edit(watcher.name) as data:
        group = state.open_incidents(data, state.feature_orders(data, head))
        incident = data["orders"][head]
        prs = incident_pr_lines(watcher.name, group)
        follow = follow_up(data, head, group, state.stamp(moment))
        for order_id in group:
            data["orders"][order_id]["proof"] = data["orders"][order_id]["proof"] or "; ".join([*([f"{read_ok} at {when(state.stamp(moment))}"] if links else []), fix])
    facts = [
        f"Incident: {incident['text']}",
        *([f"Recovered: {read_ok}"] if links else []),
        f"Fix live: {fix}",
        *prs,
    ]
    record = {
        "kind": "report",
        "topic": f"watch-recovered-{head}",
        "attr": {"close": ",".join(group)} | state.thread_attr(incident),
        "text": f"POST incident {head} is over: {'its monitors read OK and ' if links else ''}its fix is live; post the Live line in its thread",
        "body": "\n".join(facts) + "\n",
    }
    try:
        reported(watcher, [record])
    except state.DriveError as error:
        return failed(watcher, f"close of {head}", f"close report post of {head} to the communicator", error, moment)
    watcher.failures.pop(f"close of {head}", None)
    with state.edit(watcher.name) as data:
        for order_id in group:
            data["orders"][order_id]["recovered"] = state.stamp(moment)
    moved = f"; its live jobs, open PRs and parts left moved to the plain order {follow}" if follow else ""
    return [f"RECOVERED {head}: {f'{read_ok} and ' if links else ''}{fix}, so the communicator posts its Live line and closes {', '.join(group)}{moved}"]


def follow_up(data: dict, head: str, group: list[str], created: str) -> str | None:
    """Move what is left of an incident that is over to a plain build order in its thread, so closing it leaves no work on it: each open PR of its orders and that PR's job, each part of its orders beyond the incident's own not yet delivered, and each live job carrying one; that order's id, also on the head's note, or None when nothing is left."""
    own = {state.INCIDENT_FIX, *(part for parts in state.INCIDENT_PARTS.values() for part in parts)}
    incident = data["orders"][head]
    left = {other: [part for part in data["orders"][other]["parts"] if part not in own and not state.delivered(data, other, part)] for other in group}
    prs = [pr for pr in state.open_prs(data) if pr["order"] in group]
    live = [
        job_id
        for job_id, job in data["jobs"].items()
        if job["state"] not in state.FINISHED_JOB_STATES and any(set(state.job_parts(job, other)) & set(names) for other, names in left.items())
    ]
    jobs = list(dict.fromkeys([*(pr["job"] for pr in prs), *live]))
    if not (any(left.values()) or jobs):
        return None
    order_id = state.next_id(data["orders"], "o")
    parts = {part: data["orders"][other]["parts"].pop(part) for other, names in left.items() for part in names}
    follow = data["orders"][order_id] = state.new_order(incident["text"], incident["from"], state.promise_thread(incident), parts, created) | {
        "owner": incident["owner"],
        "project": incident["project"],
        "workstream": incident["workstream"],
        "title": incident["title"],
        "note": f"follow-up of incident {head}",
    }
    for job_id in jobs:
        job = data["jobs"][job_id]
        served = list(dict.fromkeys(part for other in group for part in state.job_parts(job, other)))
        for other in group:
            job["carries"].pop(other, None)
            if job_id in data["orders"][other]["jobs"]:
                data["orders"][other]["jobs"].remove(job_id)
        if job["order"] in group:
            job["order"], job["parts"] = order_id, served
        else:
            job["carries"][order_id] = served
        follow["jobs"].append(job_id)
        follow["parts"] |= {part: state.new_part() for part in served if part not in follow["parts"]}
    for pr in prs:
        pr["order"] = order_id
    incident["note"] = "; ".join(filter(None, [incident["note"], f"follow-up {order_id}"]))
    return order_id


def settle_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Settle in place each open PR card whose every PR merged or closed, its buttons replaced by how each ended so a late tap records nothing; one that fails goes again next read. cc-slack withdraws every open card carrying the PR it is given, so a card goes through a PR no card with a PR still open carries, the one the fewest cards carry first, by the watch hearing that PR's order."""
    if watcher.cards is None:
        return []
    data = state.load(watcher.name)
    prs = {pr["url"]: (key, pr) for key, pr in data["prs"].items()}
    cards = watcher.cards["pr_cards"]
    carriers = {url: [card for card in cards if url in card["prs"]] for card in cards for url in card["prs"]}
    ended = [card for card in cards if all(url in prs and prs[url][1]["state"] != "open" for url in card["prs"])]
    routes = sorted(
        (
            (url, card)
            for card in ended
            for url in card["prs"]
            if all(other in ended for other in carriers[url]) and hears(watcher, data, prs[url][1]["order"])
        ),
        key=lambda route: len(carriers[route[0]]),
    )
    done: set[str] = set()
    lines = []
    for url, card in routes:
        if view.message_key(card["url"]) in done:
            continue
        done.add(view.message_key(card["url"]))
        reason = ", ".join(f"{prs[pr][0]} {prs[pr][1]['state']}" for pr in card["prs"])
        try:
            withdrawn = slack.withdraw_pr_card(url, reason)
        except slack.SlackError as error:
            lines += failed(watcher, "card settle", "PR card settle", error, moment)
            continue
        watcher.failures.pop("card settle", None)
        done |= {view.message_key(link) for link in withdrawn}
        lines += [f"CARD {link} settled in place: {reason}" for link in withdrawn]
    return lines


def follow_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Close in place each of his question cards still showing buttons whose every question ended, showing how the newest ended, and each still showing a tap of his that his words replaced, by view.replaced_taps, since the drive's question decides its card: a close cc-slack missed when the question ended goes again here, and one that fails goes again next read."""
    if watcher.cards is None:
        return []
    data = state.load(watcher.name)
    owed = view.replaced_taps(data, watcher.cards)
    for card in watcher.cards["questions"]:
        drawn = view.drawn_on(data, *filter(None, [card["card"].get("waits"), card["url"]]))
        if drawn and all(question["closed"] for _, question in drawn):
            owed[card["url"]] = max((question["closed"] for _, question in drawn), key=lambda closed: closed["at"])
    lines = []
    for url, closed in owed.items():
        try:
            superseded.close_card(url, closed)
        except slack.SlackError as error:
            lines += failed(watcher, "card follow", "question card follow", error, moment)
            continue
        watcher.failures.pop("card follow", None)
        lines.append(f"CARD {url} closed in place: its question {closed['how']}" + (", his words in place of his tap" if closed["replaces"] and closed["replaces"]["tap"] else ""))
    return lines


def checks(watcher: Watcher, moment: datetime) -> None:
    """Start the check against his words of each PR the watch hears that owes one, within one GitHub read of finding it unchecked and with no coordinator turn: redraw_lines draws its verdict on his card in place, so his Approve of a changed patch lands it once it is green, and its NOT READY goes to the builders of the PRs it faults, by objections."""
    if watcher.cards is None:
        return
    data = state.load(watcher.name)
    stacks = land.landing_stacks(data)
    if owed := [key for key, pr in data["prs"].items() if hears(watcher, data, pr["order"]) and check_owed(watcher.name, key, pr, data, stacks.get(key, (key,)), moment)]:
        served.start(watcher.name, owed)
    objections(watcher)


def objections(watcher: Watcher) -> None:
    """Send through drive pr wake each NOT READY the check against his words keeps at a head of a PR the watch hears and faults, once a head and once to each job the PRs it faults belong to, while neither his Approve nor an open card of his holds any PR of its stack, by his_hold, so the finding wakes only a builder that owes a step now and waits on his Approve or question, never on his Hold or the hold label, which stop only its landing; the head told is kept as the PR's objected, outside its checker, so a check run again at that head, or a newer install dropping its checker, tells it no second time."""
    told: dict[tuple[str, str], tuple[str, str]] = {}
    with state.edit(watcher.name) as data:
        stacks = land.landing_stacks(data)
        for key in state.open_pr_keys(data):
            pr = data["prs"][key]
            if pr["objected"] == pr["head"] or not state.against(pr) or key not in pr["checker"]["faulted"] or not hears(watcher, data, pr["order"]):
                continue
            if any(his_hold(data, data["prs"][member]) for member in stacks.get(key, (key,))):
                continue
            pr["objected"] = pr["head"]
            told.setdefault((stage.owner_job(data, key) or pr["job"], pr["checker"]["outcome"]), (key, f"PR {key}: {pr['checker']['outcome']} {ids(pr)}"))
    for key, line in told.values():
        stage.wake(watcher.name, key, line)


def his_hold(data: dict, pr: dict) -> bool:
    """Whether his Approve or an open card of his holds the PR, a card on an open incident's work included, by state.part_cards, since his answer to it can change what its builder does; his Hold and the hold label stop only its landing, so a builder still hears an objection under either."""
    return land.approved(pr) or bool(state.part_cards(data, pr["job"], pr))


def check_owed(name: str, key: str, pr: dict, data: dict, unit: tuple[str, ...], moment: datetime) -> bool:
    """Whether a PR owes the check of its change against his words: open and waiting on his card, its change at its head not checked beside each PR of unit, the stack it sits in, at its head, its builder's report no port of an old-tree PR, which the coordinator checks against that PR, and no check of it and the rest of unit together running, or done at this head against the orders and answers of his that bind it now. It runs on every head whatever its job's state or its waits, its objection a line on his card and, until he approves the PR, a wake of its builder, and his Approve lands the PR whatever it says."""
    if pr["state"] != "open" or not stage.carded(pr) or served.stands(data, key, unit):
        return False
    return not served.ported(state.report_text(name, pr["job"])) and not served.tried(name, data, key, unit, moment)


def waiting_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Tell him once, in one reply in the card's own thread naming no card, the minute a card of his starts holding a job built on its recommended option, once per job and card whether the job's waits or what it was told name the card by its Slack link or its waits id. Each job's card is claimed under the lock before its post with the pid and start time of the watch claiming it, so no other watch posts it too, and is said once the post goes through; a post that fails gives its claims back and goes again next read, and a claim whose watch died before saying it is taken again, its pid running another process since or none, so a watch dying between its post and saying it is the one way he is told twice."""
    if watcher.cards is None:
        return []
    from drive import talk

    if not unsaid_waits(watcher, state.load(watcher.name)):
        return []
    cards: dict[str, list[dict]] = {}
    claimer = {"pid": os.getpid(), "started": started(os.getpid())}
    with state.edit(watcher.name) as data:
        for entry in unsaid_waits(watcher, data):
            data["jobs"][entry["job"]]["waiting_claims"][entry["wait"]] = claimer
            cards.setdefault(entry["card"]["url"], []).append(entry)
    lines = []
    for url, entries in cards.items():
        try:
            reply = talk.posted(watcher.name, {"text": PAUSED, "thread_ts": view.card_thread(url)})
        except (state.DriveError, slack.SlackError) as error:
            with state.edit(watcher.name) as data:
                for entry in entries:
                    data["jobs"][entry["job"]]["waiting_claims"].pop(entry["wait"])
            lines += failed(watcher, "waiting post", "built-and-paused reply", error, moment)
            continue
        with state.edit(watcher.name) as data:
            for entry in entries:
                job = data["jobs"][entry["job"]]
                job["waiting_claims"].pop(entry["wait"])
                job["waiting_said"].append(entry["wait"])
        watcher.failures.pop("waiting post", None)
        lines += [
            f"WAITING-ON-HIM {entry['wait']} blocks {entry['job']}, built on {entry['branch']}, since {when(entry['card']['asked'])}, posted {reply['permalink']}"
            for entry in entries
        ]
    return lines


def unsaid_waits(watcher: Watcher, data: dict) -> list[dict]:
    return [
        entry
        for entry in view.waiting_on_him(data, view.with_questions(data, watcher.cards))
        if heard_job(watcher, data, data["jobs"][entry["job"]])
        and not any(view.names_card(entry["card"], said) for said in data["jobs"][entry["job"]]["waiting_said"])
        and not claimed(data["jobs"][entry["job"]]["waiting_claims"].get(entry["wait"]))
    ]


def started(pid: int) -> str | None:
    """When the process holding pid started, as `ps -o lstart=` prints it, which a pid the system hands to another process changes; None when no process holds it."""
    return procs.lstart(seconds) if (seconds := procs.started(pid)) is not None else None


def shipped(watcher: Watcher, data: dict) -> dict[str, str]:
    """Each release wait of a live job or open PR that out_because frees, each staging wait whose env runs its soak commit, each Buildkite build that ended or stopped at an approve step and each PR the drive does not track that merged or closed, with what says so."""
    return {wait: said for order_id, owner in release_owners(data) if hears(watcher, data, order_id) for wait in owner["waits"] if (said := out_because(watcher, data, wait))}


def out_because(watcher: Watcher, data: dict, wait: str) -> str | None:
    """What frees a wait. A release wait is a wake the job set, never a gate: it frees once its PR merged and the watch's release live read finds every cluster of its service, production or not, or the one env the wait names after its @, serving a commit holding the merge, so a bare wait never frees while staging alone serves it, and at once once its PR merged where release live cannot follow it, a PR outside Forge-AI/monorepo or a service or env no cluster runs, naming why. A staging wait frees once release live reads its env's target serving its commit or a build carrying it, a build wait once the watch's Buildkite read finds it ended or stopped at an approve step, and a wait on a PR the drive does not track once github_lines reads it merged or closed. None for any other wait, or while it holds."""
    if found := state.RELEASE_WAIT.fullmatch(wait):
        key, service, env = found["pr"], found["service"], found["env"]
        if not data["prs"][key]["merged_sha"]:
            return None
        if github.repo_of(key).lower() != state.MONOREPO.lower():
            return f"{key} merged, and tools/ci release live reads only {state.MONOREPO}, so nothing follows its release"
        if watcher.serving is None or (clusters := watcher.serving.get(wait)) is None:
            return None
        if env and env not in clusters:
            return f"{key} merged, and tools/ci release live reads {service} on no env {env}, so nothing follows its release there"
        if not clusters:
            return f"{key} merged, and tools/ci release live reads {service} on no cluster, so nothing follows its release"
        serves = [env] if env else sorted(clusters)
        return f"tools/ci release live reads {service} on {', '.join(serves)} serving {key}" if all(clusters[each] for each in serves) else None
    if (found := state.STAGING_WAIT.fullmatch(wait)) and (watcher.serving or {}).get(wait, {}).get(found["env"]):
        return f"tools/ci release live reads {found['env']}'s {found['target']} serving {found['commit'][:12]} or a build carrying it"
    if github.BUILD.fullmatch(wait) and wait in watcher.builds:
        return build_said(wait, watcher.builds[wait])
    if state.untracked_wait(data, wait) and (landed := watcher.untracked.get(wait)) and landed not in github.UNLANDED:
        return f"{wait} {'merged' if landed == 'merged' else 'closed unmerged'}"
    return None


def release_owners(data: dict) -> list[tuple[str, dict]]:
    """Each live job and open PR with its own order."""
    return [
        *((job["order"], job) for job in data["jobs"].values() if job["state"] not in state.FINISHED_JOB_STATES),
        *((pr["order"], pr) for pr in state.open_prs(data)),
    ]


def released(data: dict, owner: dict, own: str, cards: dict | None, out: dict[str, str]) -> str:
    """Drop the waits of a job or PR that ended, a wait on a PR or its release whose PR, of a job other than own, closed unmerged taking that job's change in its place by state.change_wait; what ended, when nothing else holds it, a process by its number."""
    waits = list(dict.fromkeys(state.change_wait(data, pr["job"]) if (pr := data["prs"].get(state.waited_pr(wait))) and pr["state"] == "closed" and pr["job"] != own else wait for wait in owner["waits"]))
    holds = [wait for wait in view.holding(data, waits, cards) if wait not in out]
    ended = [wait for wait in waits if wait not in holds]
    owner["waits"] = holds
    if holds or not ended:
        return ""
    answered = [bare(wait) for wait in ended if wait not in out and not state.PID_WAIT.fullmatch(wait) and not state.RETURN_WAIT.fullmatch(wait)]
    reported = [returning["job"] for wait in ended if (returning := state.RETURN_WAIT.fullmatch(wait))]
    exited = [process["pid"] for wait in ended if (process := state.PID_WAIT.fullmatch(wait))]
    return ", ".join(
        [
            *([f"{', '.join(answered)} answered or done"] if answered else []),
            *([f"{', '.join(reported)} reported"] if reported else []),
            *([f"process {', '.join(exited)} on this Mac ended"] if exited else []),
            *(out[wait] for wait in ended if wait in out),
        ]
    )


def replies(watcher: Watcher, data: dict, moment: datetime) -> tuple[dict[str, str], list[str]]:
    """Each reply wait of a live job the watch hears, his cards asking it nothing, that a teammate's reply ended, with who replied and its link: the first message after the drive's question from anyone but the drive and a bot, read once a minute. Each reply first goes to the communicator in a report, whose handler posts it in the thread of the job's order, and onto the job's held texts, which the watch carries it once nothing holds it; a failed read or write is said as a failed read is and tried again next read, the report under the same idempotency key."""
    out, lines = {}, []
    for job_id, job in data["jobs"].items():
        if job["state"] in state.FINISHED_JOB_STATES or not heard_job(watcher, data, job) or view.open_cards(job):
            continue
        for wait in job["waits"]:
            if not (asked := state.REPLY_WAIT.fullmatch(wait)):
                continue
            source = f"reply {wait}"
            try:
                if reply := first_reply(asked["link"]):
                    out[wait] = told(watcher, data, job_id, asked["link"], reply)
            except (slack.SlackError, reads.ReadError, state.DriveError) as error:
                lines += failed(watcher, source, f"read of the reply {job_id} waits on to {asked['link']}", error, moment)
                continue
            watcher.failures.pop(source, None)
    return out, lines


def first_reply(question: str) -> dict | None:
    """The first message after the drive's question at this permalink from anyone but the drive and a bot, with its link: in the question's thread and, for a question at its conversation's top level, there too, one cc-slack history read finding both a top-level message and whether the question has a reply newer than it, which only then is read from its thread."""
    channel, ts, thread = state.slack_message(question)
    after, threaded = [], thread != ts
    if not threaded:
        top = slack.cc_slack("history", "--channel", channel, "--limit", str(REPLY_READ), "--json")["messages"]
        after = [message for message in top if float(message["ts"]) > float(ts)]
        threaded = any(float(message.get("latest_reply") or 0) > float(ts) for message in top if message["ts"] == ts)
    if threaded:
        team = question.split("/archives/")[0]
        after += [
            {"permalink": f"{team}/archives/{channel}/p{message['ts'].replace('.', '')}?thread_ts={thread}&cid={channel}"} | message
            for message in slack.thread("--channel", channel, "--thread", thread, "--since-ts", ts, "--limit", str(slack.WHOLE_THREAD))
            if float(message["ts"]) > float(ts)
        ]
    theirs = [message for message in after if not (message.get("from_claude") or message.get("from_bot"))]
    return min(theirs, key=lambda message: float(message["ts"]), default=None)


def told(watcher: Watcher, data: dict, job_id: str, question: str, reply: dict) -> str:
    """Send the communicator a teammate's reply to the question the drive asked them for a job, for the handler it starts to post in the thread of the job's order, and hold the reply's words on the job; what ended its wait."""
    from drive import cli

    job = data["jobs"][job_id]
    who = reply.get("user_name") or reply.get("user")
    words = f'{who} replied {reply["permalink"]} to the question the drive asked them for {job_id} at {question}: "{reply.get("text") or ""}"'
    record = {
        "kind": "report",
        "topic": f"reply-{job_id}",
        "id": f"reply-{job_id}-{reply['ts']}",
        "attr": {"order": job["order"], "job": job_id, "progress": "true", **state.thread_attr(data["orders"][job["order"]])},
        "text": f"POST {who} replied to the question the drive asked them for {job_id}: post their words in the thread of {job['order']}",
        "body": f"His order {job['order']}: {data['orders'][job['order']]['text']}\n{words}\n",
    }
    [seq] = reported(watcher, [record])
    agents.handle_report(watcher.name, seq, f"Record: {seq} Drive: {watcher.name} Scratch: {state.drive_dir(watcher.name) / 'scratch' / f'reply-{seq}'}")
    cli.hold(watcher.name, job_id, words, state.WATCH_SENDER)
    return f"{who} replied {reply['permalink']}"


def minutes(span: timedelta) -> int:
    return int(span.total_seconds() // 60)


def brief_first(job: dict) -> str:
    """The prompt an agent starts an untaken job on, its brief, when the job is no codex job, which drive job launch starts."""
    return f"; Read your brief first: {job['brief']}" if job["kind"] != "codex" and job.get("brief") and not job.get("agent") and not job.get("dispatch") else ""


def first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def mail_line(name: str, data: dict, job_id: str, message: dict, moment: datetime, kept: str | None, taken: bool) -> str | None:
    """Record one message from a remote lane on its job, a final report ending any question the lane asked before it and setting its state unless state.result_refusal refuses it or it types a clock time for work already done, which the event line names, and a question opening with a result other than ASK being that result; what it took goes as its job's returned event, by state.returned, naming when to start each builder a design's DESIGN or a builder's UNBUILT: lines added, which for an `ASK <file> [<file>…]` naming the blocks `drive job ask` kept is that line, as a job's own return is, and for any other `ASK` says that `drive job ask` keeps it, and a PUSHED makes only the line naming when to start each builder its UNBUILT: lines added, since pushed_lines settles the rest. The message ending the turn of a report `drive job report` already took, a worker_done or Orca's escalation for a worker that exited, whatever its body, only ends that question, since that command wrote its returned event, and any such message from a lane `drive job end` ended changes nothing, so its question stays open for the next worker's handoff; any other escalation is a STOPPED quoting on one line the whole of Orca's last output, or its first line when it names none, and a refused result its line with the refusal, each returned for the coordinator; a report the watch takes itself names each PR it links that the drive does not track, since only `drive pr add` registers one; a status, the lane's word to its coordinator while it works, is its job's returned event `MESSAGE <id>: <subject>`, setting no said line, report or state, whose record carries the message whole by whole_message."""
    job = data["jobs"][job_id]
    kind = message["type"]
    body = message["body"] or ""
    job["updated"] = state.stamp(moment)
    if taken:
        if not state.lane_ended(job):
            job.update(report=body, ask=None)
        return None
    if kind == "escalation":
        output = body.partition("Last output:")[2].partition("\nFull output:")[0] or first_line(body)
        return f"JOB {job_id} {job['order']} STOPPED without a report, last output: {' '.join(output.split())}"
    if kind == "status":
        state.returned(name, data, job_id, f"MESSAGE {message['id']}: {message['subject'] or first_line(body)}")
        return None
    if not kept:
        job["said"] = message["subject"] if kind == "worker_done" else first_line(body) or message["subject"]
    job["report"] = body
    added = []
    if kind == "question" and (kept or not state.reported(job["said"])):
        job.update(ask=message["id"], ask_at=job["updated"], state="waiting")
    elif kind in ("question", "worker_done"):
        if job.get("ask"):
            job.update(ask=None, state="running")
        if job["kind"] == "builder":
            job["said"] = re.sub(r"^READY\b", "PUSHED", job["said"])
        if refused := state.result_refusal(data, job_id, job["said"], body):
            return f'JOB {job_id} {job["order"]} {job["said"]}; refused, its state unset: {refused}; drive job resume {job_id} "<the refusal>" sends it back'
        state.take_result(data, job_id, job["said"], body)
        state.cite_designs(data, job_id, job["said"], body)
        added = state.add_changes(name, data, job_id, body)
        if untracked := [url for url in dict.fromkeys(found[0] for line in body.splitlines() if PR_LINE.match(line) for found in state.PR_URL.finditer(line)) if state.pr_key(url) not in data["prs"]]:
            state.returned(name, data, job_id, "; ".join([f"{job['said']}; the drive tracks no {', '.join(untracked)}: drive pr add <url> --job {job_id} registers each", *render.builder_lines(data, added)]))
            return None
        if state.PUSHED_LINE.match(job["said"]):
            if added:
                state.returned(name, data, job_id, "; ".join(render.builder_lines(data, added)))
            return None
    if kept:
        state.returned(name, data, job_id, f"ASK {kept}")
        return None
    state.returned(name, data, job_id, "; ".join([job["said"], *render.builder_lines(data, added), *([UNKEPT_ASK.format(job=job_id)] if job.get("ask") == message["id"] and reads.asks(body) else [])]))
    return None


def lane_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Remote lanes' mail while a lane the watch hears is live, read in one call of Orca's whole mailbox and given by senders to the job whose worker sent it, whatever Run Orca filed it on and whatever that job's state, from every worker of its job and not only the newest, and kept in its job's history, where the mail of a worker a relaunch replaced, and Orca's word on a permission prompt, which Claude Code's timer or the bash_guard hook answers while the lane carries on, only stays: reports and statuses become JOB lines, Orca's notice that its mail did not reach a finished worker ends that worker's turn with no STOPPED and says nothing, read and `DRIVE` questions the relay has not taken queue on their job to be answered here, an open question kept before the watch read it as a read included. A follow-up Orca started to carry a recorded worker's unread mail is that job's mail delivery and never its worker: each turn it ends, settles the texts its job keeps queued by that Dispatch's mailbox, by deliveries and state.take_mail, and a follow-up's failed start posts no STOPPED but holds on the job each text its mailbox still shows unread, runs `drive job resume` on the job, which carries them to its worker or relaunches a finished one with them, and says so on one JOB line. The read starts past the newest sequence the watch's last read reached, and a watch's first read past the newest mark of the jobs it hears, each job keeping the sequence of the newest of its messages taken, judged and moved under the lock, so a pass reads only the mail that came since, never Orca's whole history, and a job's mail no watch has taken waits for whichever watch hears it next. A message its job's history already keeps, read again under the lock, is never taken again, so a watch whose mark another install's save lost replays nothing and a question the relay took meanwhile never runs twice."""
    queue_kept_reads(watcher)
    data = state.load(watcher.name)
    if not live_lanes(watcher, data):
        return [line for line in read_lines(watcher, moment) if line]
    lines, messages, delivered, undelivered = [], [], {}, []
    after = watcher.mailbox_seen or max((job["mail_seen"] for job in data["jobs"].values() if heard_job(watcher, data, job)), default=0)
    try:
        mail = orca.mail(after or None)
        delivered = deliveries(watcher, data, mail)
    except orca.OrcaError as error:
        lines += read_failed(watcher, LANE_MAIL, error, moment)
    else:
        messages = mail
        watcher.failures.pop(LANE_MAIL, None)
    kept = functools.cache(lambda job_id: {reply["id"] for reply in state.replies(watcher.name, job_id)})
    asks = kept_asks(data, senders(watcher, data, delivered), messages, kept)
    if messages:
        with state.edit(watcher.name) as data:
            sender = senders(watcher, data, delivered)
            kept.cache_clear()
            for message in messages:
                job_id = sender(message)
                if not job_id or message["sequence"] <= data["jobs"][job_id]["mail_seen"]:
                    continue
                data["jobs"][job_id]["mail_seen"] = message["sequence"]
                if message["type"] == "heartbeat" or message["id"] in kept(job_id):
                    continue
                state.keep_reply(watcher.name, job_id, message)
                job = data["jobs"][job_id]
                if sending(message) in replaced(job) or state.prompted(message):
                    continue
                if message["type"] == "question" and (question := reads.request(message["body"] or "")):
                    job.setdefault("reads", {})[message["id"]] = question
                    continue
                if message["type"] in state.TURN_ENDS and message["from_handle"] == f"dispatch:{job['dispatch']}":
                    job["turn_open"] = False
                if orca.unread_notice(message):
                    continue
                if delivery := delivered.get(sending(message)):
                    held = state.take_mail(job, delivery.box, bool(delivery.failed))
                    if delivery.failed:
                        if held:
                            undelivered.append((job_id, job["order"], held, f"Orca's follow-up dispatch {delivery.dispatch} never started: {delivery.failed}"))
                        continue
                taken = message["type"] in state.TURN_ENDS and (state.lane_ended(job) or message["id"] in state.turn_ends(state.replies(watcher.name, job_id)))
                lines.append(mail_line(watcher.name, data, job_id, message, moment, asks.get(message["id"]), taken))
        watcher.mailbox_seen = messages[-1]["sequence"]
    for job_id, order_id, held, failed in undelivered:
        carried = "" if busy(watcher, job_id, moment) else f"; the watch ran drive job resume {job_id}, which carries it, its output in the drive's {RELAUNCHES_LOG}"
        if carried:
            send_held(watcher, job_id, moment)
        lines.append(f"JOB {job_id} {order_id} {render.unread(held, failed)}{carried}")
    watcher.made += [line for line in lines if line]
    return [line for line in read_lines(watcher, moment) if line]


class Delivery(NamedTuple):
    """The Dispatch mailbox a turn ended on that delivers a recorded worker's mail: that worker's own, read while its job keeps texts queued for it, or the one Orca's follow-up carrying the mail that worker had not read when it reported holds; the recorded worker's handle, the Dispatch read, its mail, and the error the follow-up's start record shows when that start failed, else None."""

    of: str
    dispatch: str
    box: list[dict]
    failed: str | None


def deliveries(watcher: Watcher, data: dict, mail: list[dict]) -> dict[str, Delivery]:
    """By its handle, each Dispatch whose turn ended that delivers mail of a worker a job the watch hears recorded, whatever that job's state, with its mailbox read before the lock, so an OrcaError leaves the mail to read again next pass: a Dispatch no job recorded, Orca's notice that mail did not reach a finished worker aside, sending on the Run of a job the watch hears, that Orca started as a follow-up of such a worker, whose followUpOf names it or a follow-up of it, by orca.follow_up."""
    recorded, found = workers(data), {}
    runs = {f"run:{job['run']}" for job in data["jobs"].values() if job.get("run") and heard_job(watcher, data, job)}
    for message in mail:
        handle = sending(message)
        if message["type"] not in state.TURN_ENDS or orca.unread_notice(message) or not handle.startswith("dispatch:") or handle in found:
            continue
        dispatch = handle.removeprefix("dispatch:")
        if handle in recorded or message["to_handle"] not in runs:
            continue
        shown = carried = orca.follow_up(dispatch)
        while carried and f"dispatch:{carried['dispatch']['followUpOf']}" not in recorded:
            carried = orca.follow_up(carried["dispatch"]["followUpOf"])
        if carried and heard_job(watcher, data, job := data["jobs"][recorded[of := f"dispatch:{carried['dispatch']['followUpOf']}"]]):
            found[handle] = Delivery(of, dispatch, orca.mailbox(job, dispatch), shown["worker"]["lastError"] if shown["worker"]["state"] == "failed" else None)
    return found


def kept_asks(data: dict, sender: Callable[[dict], str | None], messages: list[dict], kept: Callable[[str], set[str]]) -> dict[str, str]:
    """The files of each new `ASK <file> [<file>…]` question a remote lane asked naming only blocks `drive job ask` kept for its job, by its message id, as a job on this Mac returns `ASK <file>`; any other ASK goes on to the coordinator, whose `drive job ask <j>` keeps it."""
    asks = {}
    for message in messages:
        job_id = sender(message)
        if not job_id or message["sequence"] <= data["jobs"][job_id]["mail_seen"] or message["type"] != "question" or message["id"] in kept(job_id):
            continue
        if questions := state.asked_in(data["jobs"][job_id], reads.opening(message["body"] or "")):
            asks[message["id"]] = " ".join(question["file"] for question in questions)
    return asks


def queue_kept_reads(watcher: Watcher) -> None:
    """Queue each live lane's open question that asks a read, so a question kept as the coordinator's, such as one kept before the watch read it as a read, is answered here and its lane runs again."""
    data = state.load(watcher.name)
    if not any(kept_read(job) for job in live_lanes(watcher, data).values()):
        return
    with state.edit(watcher.name) as data:
        for job in live_lanes(watcher, data).values():
            if question := kept_read(job):
                job.setdefault("reads", {})[job["ask"]] = question
                job.update(ask=None, state="running")


def kept_read(job: dict) -> str | None:
    return reads.request(job["report"] or "") if job.get("ask") else None


def live_lanes(watcher: Watcher, data: dict) -> dict[str, dict]:
    """Each remote job whose lane this watch reads, by read_lane."""
    return {job_id: job for job_id, job in data["jobs"].items() if read_lane(job) and heard_job(watcher, data, job)}


def read_lane(job: dict) -> bool:
    """Whether the job's lane is one a watch reads: a remote one running or waiting, and one built while the turn of the worker that built it is open, so the reads it asks after its BUILT report are answered."""
    return bool(job.get("where") == "remote" and job.get("dispatch") and job.get("run") and (job["state"] in LIVE_JOB_STATES or (job["state"] == "built" and job["turn_open"])))


def replaced(job: dict) -> set[str]:
    """The handle of each worker a relaunch moved the job off, whose turn ended before its next worker started: its history keeps that worker's mail, but no read it asked is run and no result it sent is the job's."""
    return {f"dispatch:{death['dispatch']}" for death in job.get("deaths", []) if death["dispatch"] != job["dispatch"]}


def senders(watcher: Watcher, data: dict, delivered: dict[str, Delivery]) -> Callable[[dict], str | None]:
    """Whose each lane message is: the job that recorded the worker sending it, by sending or its terminal's handle, a follow-up Orca started to carry a recorded worker's mail sending as that worker, whatever Run Orca filed it on and whatever that job's state, since Orca files a lane's send to the drive's terminal on whichever Run that terminal last bound, and only while the watch hears that job, so a worker another watch's job recorded sends this watch none. A message from a worker no job recorded is the mail of the live job holding the Run it was filed on alone, which takes the mail of every worker there the drive never recorded, such as the first of two workers two racing resumes started; a Run several jobs share, as drives kept lanes before each job got its own, takes none."""
    owners = workers(data)
    holders = collections.Counter(job.get("run") for job in data["jobs"].values())
    alone = {f"run:{job['run']}": job_id for job_id, job in live_lanes(watcher, data).items() if holders[job["run"]] == 1}

    def sender(message: dict) -> str | None:
        handle = delivered[sent].of if (sent := sending(message)) in delivered else sent
        job_id = next((owners[handle] for handle in (handle, message["from_handle"]) if handle in owners), None)
        if job_id is None:
            return alone.get(message["to_handle"])
        return job_id if heard_job(watcher, data, data["jobs"][job_id]) else None

    return sender


def worker_handles(job: dict) -> list[str]:
    """Each handle the job's workers send from: the `dispatch:` handle of its worker and of each one a relaunch replaced, and the terminal its worker runs in, as orca.run_of records it, which a worker on this Mac sends a reply from."""
    dispatches = [job.get("dispatch"), *(ended["dispatch"] for ended in job.get("deaths", []))]
    return [*(f"dispatch:{dispatch}" for dispatch in dispatches if dispatch), *([job["terminal"]] if job.get("terminal") else [])]


def workers(data: dict) -> dict[str, str]:
    """The job that recorded each handle a worker of the drive sends from, by worker_handles, live or finished."""
    return {handle: job_id for job_id, job in data["jobs"].items() for handle in worker_handles(job)}


def sending(message: dict) -> str:
    """The handle of the worker a lane's message is from: its `dispatch:<id>` sender, or for a worker on this Mac sending from its terminal's handle the dispatch Orca's payload names."""
    dispatch = json.loads(message["payload"] or "{}").get("dispatchId")
    return message["from_handle"] if message["from_handle"].startswith("dispatch:") or not dispatch else f"dispatch:{dispatch}"


@contextlib.contextmanager
def relaying(watcher: Watcher) -> Iterator[None]:
    """Answer the lanes' reads and owe him each promised line on a thread of the watch process for as long as the watch runs, ended and waited for before the watch keeps its unposted lines, so each line the relay made is kept."""
    stop = threading.Event()
    thread = threading.Thread(target=relay, args=(watcher, stop), daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()


def relay(watcher: Watcher, stop: threading.Event) -> None:
    """A relay pass every RELAY_SECONDS beside the passes until the watch stops it, so no GitHub read, landing, Slack redraw or install that holds a pass for minutes holds a lane's answer or the handler on a line promised him by a time; its lines wait on the watcher for the pass after them to hand on. It keeps a Watcher of its own, so a failed Orca read of its own never reruns the pass's 60-second block, ends on a drive a newer install saved, as the watch does, and keeps any other error for the watch to stop on, so its launch agent starts both again."""
    relaying = Watcher(watcher.name, offset=watcher.offset, project=watcher.project)
    try:
        while not stop.wait(RELAY_SECONDS):
            watcher.relayed.extend(relay_pass(relaying, state.now()))
    except state.NewerDrive:
        return
    except Exception as error:
        watcher.relay_error = error


def relay_pass(watcher: Watcher, moment: datetime) -> list[str]:
    """Queue each new read or `DRIVE` question a live lane asked, then send each queued read the answer its reader kept, starting a reader on each that has none, then start the handler on each line promised him by a time this pass's own clock has passed, by due_lines. Its first pass, and one after a failed read, first has this process's Orca calls speak as the drive's own terminal; a failed read goes again next pass, said as the watch says its reads."""
    lines = []
    try:
        if watcher.read_at is None:
            orca.speak_as_drive(watcher.name)
            watcher.read_at = moment
        queue_questions(watcher)
    except orca.OrcaError as error:
        lines = read_failed(watcher, NEWEST_MAIL, error, moment)
    else:
        watcher.failures.pop(NEWEST_MAIL, None)
    return lines + read_lines(watcher, moment) + due_lines(watcher, moment)


def queue_questions(watcher: Watcher) -> None:
    """Queue on its job each read or `DRIVE` question in Orca's newest mail that a lane's worker asked, by senders, keeping it in its job's history as lane_lines keeps lane mail, so lane_lines, which reads past its own mark, finds it kept and never queues it again; each message is judged once in the process, and no mail is read while no lane is live."""
    data = state.load(watcher.name)
    if not live_lanes(watcher, data):
        return
    sender = senders(watcher, data, {})
    asked = [
        (message, question)
        for message in orca.newest_mail()
        if message["type"] == "question"
        and message["id"] not in watcher.questions_taken
        and sender(message)
        and (question := reads.request(message["body"] or ""))
    ]
    if not asked:
        return
    with state.edit(watcher.name) as data:
        sender = senders(watcher, data, {})
        for message, question in asked:
            watcher.questions_taken.add(message["id"])
            job_id = sender(message)
            if not job_id or message["sequence"] <= data["jobs"][job_id]["mail_seen"] or message["id"] in {reply["id"] for reply in state.replies(watcher.name, job_id)}:
                continue
            state.keep_reply(watcher.name, job_id, message)
            if sending(message) not in replaced(job := data["jobs"][job_id]) and not state.prompted(message):
                job.setdefault("reads", {})[message["id"]] = question


def drained(lines: collections.deque[str]) -> list[str]:
    return [lines.popleft() for _ in range(len(lines))]


def read_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Start `drive job read` here as its own process on each `READ`, `LOG` or `DRIVE` question a lane queued, since a Sprite holds no copy of the drive's records, which sends the answer it keeps, by answer_kept, for each question with neither an answer nor a reader still running whose lane may wait on it, a live lane's or one whose worker's turn is open whatever its job's state, as when the lane's own `drive job report` took its job done while its next question waited, so a drive command that runs for minutes holds up no pass; one the read cannot answer gets why as its answer and a `JOB` line saying so. A question no worker waits on leaves its queue with no line, never read and any answer kept for it never sent, so no write a lane asked runs after its lane gave up on it, even once a later move of its job makes it live again, by gone_reads, which send_answers reads too. A lane's `drive job report` wrote its returned event as it took the return, so the answer carries no line of its own, and the return may have moved its job out of the live states, so a kept answer goes whatever its job's state. A reader starts only on what drive.json held once its reader was seen ended, so no reader that finished since the watch last read drive.json starts again; then each Run holding an answer kept for a job this watch hears gets every answer a refused reply left by send_answers, which sends them again and never reruns the read, each refusal said once as read_failed says it. The relay's thread and the pass take turns here under RELAYING, each reading the drive afresh, so no question gets two readers."""
    with RELAYING:
        running, data = ended_reads(watcher.name, state.load(watcher.name))
        unowed = {}
        for job_id, job in data["jobs"].items():
            idle = [message_id for message_id in job.get("reads", {}) if (job_id, message_id) not in running]
            if not idle or not heard_job(watcher, data, job):
                continue
            gone = gone_reads(watcher.name, job_id, job, idle)
            if dropped := [message_id for message_id in idle if message_id in gone]:
                unowed[job_id] = dropped
            for message_id in idle:
                if message_id in gone or message_id in job.get("read_answers", {}):
                    continue
                reader = job.setdefault("readers", {})[message_id] = spawn_reader(watcher.name, job_id, message_id)
                with state.edit(watcher.name) as fresh:
                    if message_id in fresh["jobs"][job_id].get("reads", {}):
                        fresh["jobs"][job_id].setdefault("readers", {})[message_id] = reader
        if unowed:
            with state.edit(watcher.name) as fresh:
                for job_id, message_ids in unowed.items():
                    for message_id in message_ids:
                        for held in ("reads", "read_answers", "readers"):
                            fresh["jobs"][job_id].get(held, {}).pop(message_id, None)
        lines = []
        running, data = ended_reads(watcher.name, data)
        for run in dict.fromkeys(job["run"] for job_id, job in data["jobs"].items() if heard_job(watcher, data, job) and unsent_answers(watcher.name, job_id, job, running)):
            for job_id, error in send_answers(watcher.name, run):
                source = f"the answer to {job_id}'s"
                if error:
                    lines += read_failed(watcher, source, error, moment)
                else:
                    watcher.failures.pop(source, None)
        return lines


def unsent_answers(name: str, job_id: str, job: dict, running: set[tuple[str, str]]) -> list[str]:
    """The questions of the job whose answer is kept, whose reader no longer runs and that a worker still waits on, by gone_reads."""
    kept = [message_id for message_id in job.get("reads", {}) if (job_id, message_id) not in running and job.get("read_answers", {}).get(message_id)]
    if not kept:
        return []
    gone = gone_reads(name, job_id, job, kept)
    return [message_id for message_id in kept if message_id not in gone]


def gone_reads(name: str, job_id: str, job: dict, idle: list[str]) -> set[str]:
    """The reads among these no worker waits on: each of a job whose lane no watch reads, by read_lane, once its worker ended its turn, since that worker's wait ended with its turn, and each its job's live worker did not ask, by foreign_reads."""
    return set(idle) if not read_lane(job) and not job["turn_open"] else foreign_reads(job, state.replies(name, job_id))


def send_answers(name: str, run: str) -> list[tuple[str, orca.OrcaError | None]]:
    """Send every answer kept on the Run's questions, each in its own orca.reply as the drive's own terminal, which needs no bind of the Run, whether the watch or the reader that just kept one sends them, so a lane gets its answer the moment its read ends, never once a pass of the watch reaches it, and no answer waits on another lane's. Each answer is read from what drive.json held once its reader was seen ended, so the cut-off answer a reader keeps before it runs never goes ahead of the one it finishes with; answer_read wrote any answer past one Orca relay message onto the lane's Sprite before it kept it, so only Orca calls run here. Two senders sending one answer at once send the same text, which Orca takes as the answer it already holds, and only the one whose save takes the question off its job's queue writes its event. A question leaves that queue once its answer goes through, or once Orca says it already holds another answer, since a lane whose wait ends waits again on the same question, or that the question's dispatch closed, since no lane waits on it then; any other refusal leaves it for the watch's next pass. A sent answer whose read failed writes the event the watch hearing its job says as a `JOB` line. What came of each answer, by its job: the refusal, or None."""
    running, data = ended_reads(name, state.load(name))
    sent, refused, told = [], [], []
    for job_id, message_id in run_answers(name, data, run, running):
        said, why = data["jobs"][job_id]["read_answers"][message_id]
        try:
            orca.reply(name, run, message_id, said)
        except orca.OrcaError as error:
            if error.code not in (orca.ANSWERED_ELSEWHERE, orca.DISPATCH_CLOSED):
                refused.append((job_id, error))
                continue
            why = None
        sent.append((job_id, message_id, why))
    if sent:
        with state.edit(name) as data:
            for job_id, message_id, why in sent:
                job = data["jobs"][job_id]
                if job.get("reads", {}).pop(message_id, None) is not None and why:
                    told.append((job_id, why))
                for held in ("read_answers", "readers"):
                    job.get(held, {}).pop(message_id, None)
    for job_id, why in told:
        state.append_event(name, READ_UNANSWERED, job=job_id, order=data["jobs"][job_id]["order"], why=why, lane=state.heard_job_lane(data, data["jobs"][job_id]))
    return [(job_id, None) for job_id, _, _ in sent] + refused


def run_answers(name: str, data: dict, run: str, running: set[tuple[str, str]]) -> list[tuple[str, str]]:
    return [(job_id, message_id) for job_id, job in data["jobs"].items() if job.get("run") == run for message_id in unsent_answers(name, job_id, job, running)]


def foreign_reads(job: dict, kept: list[dict]) -> set[str]:
    """The reads a lane queued on the job that its live worker did not ask, by the job's kept messages: every one once `drive job end` ended its lane, else each whose message Orca stamped with a dispatch other than the job's; a message naming no dispatch is its job's, as sender takes it."""
    if state.lane_ended(job):
        return set(job["reads"])
    return {reply["id"] for reply in kept if reply["id"] in job["reads"] and json.loads(reply.get("payload") or "{}").get("dispatchId", job["dispatch"]) != job["dispatch"]}


def ended_reads(name: str, data: dict) -> tuple[set[tuple[str, str]], dict]:
    """The reads in data a reader still runs on, by job and message id, then the drive read again after that check, so each other read's answer there is the one its reader finished with, since a reader keeps its final answer before it exits; data names every reader started so far."""
    return running_reads(data), state.load(name)


def running_reads(data: dict) -> set[tuple[str, str]]:
    return {(job_id, message_id) for job_id, job in data["jobs"].items() for message_id in job.get("reads", {}) if reading(job, message_id)}


def reading(job: dict, message_id: str) -> bool:
    """Whether a reader still runs on this question: its answer is not final yet and its process lives, so a reader the watch has not reaped yet holds no answer it already kept."""
    kept = job.get("read_answers", {}).get(message_id)
    return (not kept or kept[1] == CUT_OFF) and bool((reader := job.get("readers", {}).get(message_id)) and state.alive(reader))


def spawn_reader(name: str, job_id: str, message_id: str) -> int:
    """Run `drive job read` on one question a lane queued as its own process, outliving the pass that started it, its output appended to the drive's reads log."""
    return state.detach(name, ["job", "read", job_id, message_id], READS_LOG)


def answer_read(name: str, job_id: str, message_id: str) -> list[str | None]:
    """Run a lane's queued read here, keep its answer, and why it is no answer when it failed, under its message id, and send it by answer_kept. It first keeps the answer a reader stopped mid-run leaves, so no read runs twice, a `drive break-glass` in production included, and none that crashes starts again, with this reader's process id in the same save, so a watch whose own save of that id a newer install's save refused still reads it running. An answer past one Orca relay message is written onto the lane's Sprite here, before any lock, and kept as the line naming that file; one the Sprite cannot take is answered with why, as a failed read is, so its question leaves the queue at its first reply and Orca, not a Sprite, says whether its lane still waits."""
    data = state.load(name)
    question = data["jobs"][job_id]["reads"][message_id]
    keep_answer(name, job_id, message_id, [reads.lane_exit(1, CUT_OFF) if reads.DRIVE.match(question) else f"{question}: {CUT_OFF}", CUT_OFF], reader=os.getpid())
    try:
        said = reads.answer(question, Path(data["roles"]["coordinator"]["cwd"]), name, job_id)
    except reads.ReadError as error:
        kept = [f"{question}: {error}"] * 2
    else:
        try:
            kept = [orca.sprite_answer(said, data["jobs"][job_id], message_id), None]
        except orca.OrcaError as error:
            why = f"{question.partition("\n")[0]}: its {len(said.encode())}-byte answer, past one Orca relay message, could not go to its Sprite: {error}"
            kept = [orca.exit_line(said) + why, why]
    install.finished(f"{job_id}: its read {message_id} ran", state.DRIVE_LOCK_SECONDS, answer_kept, name, job_id, message_id, kept)
    return kept


def answer_kept(name: str, job_id: str, message_id: str, kept: list[str | None]) -> None:
    """Keep the answer a read came to, then send it with every other answer kept on its job's Run by send_answers, which leaves one Orca refuses for the watch's next pass; answer_read runs it through install.finished once the read ran, since a newer install may save the drive while it runs, so the read never runs again."""
    keep_answer(name, job_id, message_id, kept)
    send_answers(name, state.load(name)["jobs"][job_id]["run"])


def keep_answer(name: str, job_id: str, message_id: str, kept: list[str | None], reader: int | None = None) -> None:
    with state.edit(name) as data:
        job = data["jobs"][job_id]
        job.setdefault("read_answers", {})[message_id] = kept
        if reader:
            job.setdefault("readers", {})[message_id] = reader


def page_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Redraw the drive's Slack status page when what it shows changed since the last drawing, its "Updated" line aside; say once, kept in the drive across watch restarts, when Slack starts refusing so the page he sees is stale, once when it takes a redraw again, and with the link whenever the page moved to a new tab."""
    if watcher.project:
        return []
    markdown = page.render(view.read(watcher.name, moment), dashboard.link(watcher.name))
    shape = markdown.split("\n", 1)[1]
    if shape == watcher.page_shape:
        return []
    stale = state.load(watcher.name)["slack"].get("page_stale")
    try:
        drawn = page.draw(watcher.name, markdown)
    except page.PageError as error:
        if stale:
            return []
        with state.edit(watcher.name) as data:
            data["slack"]["page_stale"] = state.stamp(moment)
        return [f"WATCH status page stale since {when(state.stamp(moment))}: {error}"]
    watcher.page_shape = shape
    if stale:
        with state.edit(watcher.name) as data:
            data["slack"].pop("page_stale")
    if drawn.new_tab:
        return [f"WATCH status page redrawn on a new tab: {drawn.url}"]
    if stale:
        return [f"WATCH status page redrawn: Slack takes its redraws again, after refusing them since {when(stale)}"]
    return []


def event_line(event: dict, data: dict) -> str:
    if event["kind"] == state.ORDERS_MOVED:
        return moved_line(event, data)
    prs = data["prs"]
    key = f"{event.get('repo')}#{event.get('number')}"
    suffix = f" {ids(prs[key])}" if key in prs else ""
    if event["kind"] == "tap":
        return f"TAP {event['label']} {key}{suffix}"
    if event["kind"] == "landing" and event["change"].startswith(land.OWN_FAILURE):
        return f"{LANDER}{event['change']}"
    if event["kind"] == state.RETURNED:
        return f"JOB {event['job']} {event['order']} {event['line']}"
    if event["kind"] == "proof" and "job" in event:
        return f"JOB {event['job']} {event['order']}: {event['change']}"
    if event["kind"] in ("landing", "check", "proof"):
        return f"PR {key}: {event['change']}{suffix}"
    if event["kind"] == READ_UNANSWERED:
        return f"JOB {event['job']} {event['order']} READ unanswered, its lane was told why: {event['why']}"
    if event["kind"] == RELAUNCH_REFUSED:
        return f"EVENT {RELAUNCH_REFUSED} drive job {event['verb']} {event['job']}, which the watch ran, refused: {event['refusal']}; its output is in the drive's {event['log']} [{event['order']} {event['job']}]"
    details = " ".join(f"{name}={value}" for name, value in event.items() if name not in ("kind", "at", "lane"))
    return f"EVENT {event['kind']} {details}".rstrip() + suffix


def moved_line(event: dict, data: dict) -> str:
    """What a lane's watch says to it once a save moved open orders it held to another session: the orders, the lanes holding them now, their unfinished jobs, the agents its own session runs on this Mac among them, which only its SendMessage reaches, and the one handoff record it sends their holder of what it holds on them outside the drive file."""
    jobs = list(dict.fromkeys(job_id for order_id in event["orders"] for job_id in data["orders"][order_id]["jobs"] if data["jobs"][job_id]["state"] not in state.FINISHED_JOB_STATES))
    own = [job_id for job_id in jobs if data["jobs"][job_id]["where"] == "local" and data["jobs"][job_id]["agent"] and state.session_lane(data, data["jobs"][job_id]["session"]) == event["lane"]]
    return (
        f"MOVED {', '.join(event['orders'])} from {event['lane']} to {', '.join(event['to'])}: unfinished jobs {', '.join(jobs) or 'none'};"
        f" agents of your session among them: {', '.join(own) or 'none'}, which run on and take SendMessage from you alone. Add, brief and land nothing more on these orders,"
        " and send their holder, as drive status names it, one handoff record of what you hold on them outside the drive file: holds, waits, jobs you meant to add or brief, promises to him"
    )


def event_lines(watcher: Watcher) -> list[str]:
    """Each new event the watch hears: one is written with the lane of the coordinator session hearing its order then, so a binding or a refiling after it moves it to no other watch, an ORDERS_MOVED one with the lane that lost the orders it names, the drive's own watch saying one the communicator or the meta session lost, which handed sends that session, and one written with no lane is the drive's own watch's. A delivery the receiver wrote is said by no line: it owes each watch a read of the PRs it names, which github_lines reads later in the same pass for the PRs that watch hears, a push to their base a read of their mergeability alone. A build delivery owes it a read of the Buildkite builds it names, which build_lines makes later in the same pass."""
    events = list(state.read_events(watcher.name, watcher.offset))
    if not events:
        return []
    if watcher.owed is not None:
        delivered = [(f"{event['repo']}#{number}", event["event"] in receiver.REMERGES) for event, _ in events if event["kind"] == receiver.DELIVERY for number in event["numbers"]]
        watcher.owed |= {key for key, remerges in delivered if not remerges}
        watcher.merging |= {key for key, remerges in delivered if remerges}
    watcher.builds_heard |= {link for event, _ in events if event["kind"] == receiver.BUILD_DELIVERY for link in event["builds"]}
    data = state.load(watcher.name)
    heard = (watcher.lane,) if watcher.project else (watcher.lane, *state.HOLDERS)
    said = [event_line(event, data) for event, _ in events if event["kind"] not in (*view.HISTORY_ONLY, receiver.DELIVERY, receiver.BUILD_DELIVERY) and event.get("lane", "root") in heard]
    watcher.offset = events[-1][1]
    return said


def overdue(data: dict, order: dict, moment: datetime) -> bool:
    """Whether the watch owes a report on the line an order promised him by a time already past: every ten minutes while he is here, none while state.owed_at_return reads it owed at his return, once while he is away for an order whose lines have no thread, and at once the first pass after he is heard, whatever report went before his absence."""
    if order["state"] in state.CLOSED_ORDER_STATES or not state.owed(order) or state.parse_time(state.owed_at(order)) > moment or state.owed_at_return(data, order, moment):
        return False
    said, absence = order["due_said"], data["owner"]["absence"]
    return said is None or not state.owner_away(data, moment) and (moment - state.parse_time(said) >= DUE_EVERY or bool(absence) and state.parse_time(said) < state.parse_time(absence["until"]))


def due_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Owe him the line promised by each time that passed with no drive post in its order's thread or one of its cards' since it was promised, read from Slack: the relay writes the report owing that line and starts the handler that posts it there, as quiet_lines does, within RELAY_SECONDS of the time by its own clock, however long a pass holds the watch, so no session's judgment stands between the time and its line, by his words "fix your toolking so tracking this stuff beciomes deterministic and it cant get lost at the whims of an agent"; again every ten minutes until a post is there, by overdue's clock, none while he is away and the first at once after he is heard, each counting him late once he read present ten minutes past the time. A report reaching a handler keeps its facts as quiet_lines' own reports do, so the thread's next news report waits five minutes from it. A thread that could not be read and a report that could not be written are said as a failed read is and tried again next pass; a report whose handler did not start gets one from post_lines, as each POST report does. An incident promises none, since quiet_lines owes its next line five quiet minutes after its last once it has news. An order whose lines to him have no thread has no line to read, so its time is the drive's own, said once as a DUE line and kept by being said."""
    data = state.load(watcher.name)
    threads = {
        order_id: slack.order_posts(data, order_id, order) if state.promise_thread(order) else None
        for order_id, order in heard_orders(watcher, data).items()
        if overdue(data, order, moment)
    }
    if not threads:
        return []
    lines = []
    owing = []
    with state.edit(watcher.name) as data:
        for order_id, read in threads.items():
            order = data["orders"][order_id]
            if read and overdue(data, order, moment):
                state.keep(order, read[0])
            if read and not read[1]:
                watcher.failures.pop(f"due read {order_id}", None)
            if not overdue(data, order, moment):
                continue
            if read is None:
                order["due_said"] = order["kept"] = state.stamp(moment)
                lines.append(f'DUE {order_id} "{cut(order["text"], 80)}" was due {when(state.owed_at(order))}: {UNTHREADED}')
            elif read[1]:
                lines += failed(watcher, f"due read {order_id}", f"read of {order_id}'s threads for the line promised him by {when(state.owed_at(order))}", slack.SlackError(read[1]), moment)
            else:
                owing.append(order_id)
    registered = render.prs_by_job(data)
    for order_id in owing:
        source = f"due {order_id}"
        order = data["orders"][order_id]
        group = [order_id, *state.joined_orders(data).get(order_id, [])]
        facts = line_facts(data, group, registered, moment)
        working = [job_id for job_id, job in state.orders_jobs(data, group).items() if job["state"] == "running"]
        try:
            seq = quiet_report(watcher, data, {order_id: group}, quiet_since(data, group), moment, untold(order, facts) if order["quiet"] else list(facts), working, state.owed_at(order))
        except (reads.ReadError, state.DriveError) as error:
            lines += failed(watcher, source, f"report owing the line {order_id} promised him by {when(state.owed_at(order))}", error, moment)
            continue
        handler = None
        try:
            handler, _ = agents.handle_report(watcher.name, seq, f"Record: {seq} Drive: {watcher.name} Scratch: {state.drive_dir(watcher.name) / 'scratch' / f'due-{seq}'}")
            watcher.failures.pop(source, None)
        except state.DriveError as error:
            lines += failed(watcher, source, f"handler on report {seq} owing the line {order_id} promised him", error, moment)
        with state.edit(watcher.name) as data:
            order = data["orders"][order_id]
            order["due_said"] = state.stamp(moment)
            if order["quiet"]:
                order["quiet"] |= {"record": seq, "at": state.stamp(moment), "handler": handler, "news": facts}
    return lines


def claim_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Owe the thread of each claim whose end passed, such as a staging claim's in #platform-internal, its closing line: read from Slack, a drive post there after the post that set the end ends the claim, and with none the order's lane gets a DUE line naming the claim, again every ten minutes until one is there, by his words "this thread message saying we're done and what the result is was is good, we should always do that"; a line in that thread naming a new end moves it, which state.claim keeps. A thread that could not be read is said as a failed read is and tried again next pass."""
    data = state.load(watcher.name)
    due = [
        (order_id, ts, held)
        for order_id, order in data["orders"].items()
        if order["claims"] and hears(watcher, data, order_id)
        for ts, held in order["claims"].items()
        if state.parse_time(held["until"]) <= moment and (held["said"] is None or moment - state.parse_time(held["said"]) >= DUE_EVERY)
    ]
    lines = []
    for order_id, ts, held in due:
        source = f"claim read {ts}"
        try:
            messages = slack.thread("--channel", held["channel"], "--thread", ts, "--since-ts", held["since"], "--limit", str(slack.WHOLE_THREAD))
        except slack.SlackError as error:
            lines += failed(watcher, source, f"read of the thread of claim {held['link']}", error, moment)
            continue
        watcher.failures.pop(source, None)
        closed = any(message.get("from_claude") for message in messages)
        with state.edit(watcher.name) as data:
            claims = data["orders"][order_id]["claims"]
            if claims.get(ts, {}).get("since") != held["since"]:
                continue
            if closed:
                claims.pop(ts)
                continue
            claims[ts]["said"] = state.stamp(moment)
        lines.append(
            f"DUE {order_id} claim {held['link']} ended {when(held['until'])} with no closing line in its thread: once the work ends, free what it"
            f" claimed and send a POST report with --attr thread={ts} saying it is done, what is free again and the result, as the repository's"
            " staging-claim rule says, or one with --attr until=<the new end> while it runs on"
        )
    return lines


def claim_reply_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Read once the thread of each claim posted 5 minutes ago, such as a staging claim's in #platform-internal, by his words "You then need to watch the thread for responses and if none within 5 mins you can proceed": the order's lane gets a DUE line saying the work it claims may start, or quoting each reply there from anyone but the drive and a bot for it to answer before that work starts. A thread that could not be read is said as a failed read is and tried again next pass."""
    data = state.load(watcher.name)
    due = [
        (order_id, ts, held)
        for order_id, order in data["orders"].items()
        if order["claims"] and hears(watcher, data, order_id)
        for ts, held in order["claims"].items()
        if held["replies_read"] is None and moment - datetime.fromtimestamp(float(ts)).astimezone() >= CLAIM_REPLIES
    ]
    lines = []
    for order_id, ts, held in due:
        source = f"claim replies read {ts}"
        try:
            messages = slack.thread("--channel", held["channel"], "--thread", ts, "--since-ts", ts, "--limit", str(slack.WHOLE_THREAD))
        except slack.SlackError as error:
            lines += failed(watcher, source, f"read of the replies to claim {held['link']}", error, moment)
            continue
        watcher.failures.pop(source, None)
        replies = [message for message in messages if float(message["ts"]) > float(ts) and not (message.get("from_claude") or message.get("from_bot"))]
        with state.edit(watcher.name) as data:
            if ts not in (claims := data["orders"][order_id]["claims"]):
                continue
            claims[ts]["replies_read"] = state.stamp(moment)
        if not replies:
            lines.append(f"DUE {order_id} claim {held['link']} is 5 minutes old with no reply in its thread: the work it claims may start, as the repository's staging-claim rule says")
            continue
        quoted = "; ".join(f'{message.get("user_name") or message.get("user")}: "{cut(message.get("text") or "", 200)}"' for message in replies)
        lines.append(
            f"DUE {order_id} claim {held['link']} is 5 minutes old with {len(replies)} {'reply' if len(replies) == 1 else 'replies'} in its thread, {quoted}:"
            f" start nothing it claims until a POST report with --attr thread={ts} answers {'it' if len(replies) == 1 else 'each'} there and nobody objects,"
            " as the repository's staging-claim rule says"
        )
    return lines


def last_line(data: dict, group: list[str]) -> datetime:
    """When he last had a line on an open order or incident, by the orders state.feature_orders names for it: the newest drive post on any of them, else when the first was opened."""
    lined = [data["orders"][order_id]["last_line"] for order_id in group if data["orders"][order_id]["last_line"]]
    return max(map(state.parse_time, lined or [data["orders"][group[0]]["created"]]))


def quiet_since(data: dict, group: list[str]) -> datetime:
    """When an open order or incident last had a line, or when the watch's last report on it reached a handler, whichever is newer."""
    owed = data["orders"][group[0]]["quiet"]
    return max([last_line(data, group), *([state.parse_time(owed["at"])] if owed and owed["at"] else [])])


def line_facts(data: dict, group: list[str], registered: dict[str, list[str]], moment: datetime) -> dict[str, list]:
    """What a line on an open order or incident tells him, by the order, job, PR or handoff it is about: each order of it, each job whose own order it is, never one only carrying it, by heard_job's rule that one watch alone makes every line about a job, with the facts state.job_news names once it is not running, so one wait of several ending is no news while the job still waits, each PR registered on those jobs or named in its orders' words, by its state, checks, Trunk ejection and tap as the watch last read them, so a new commit is news only through its checks or state, and each handoff of the communicator's side state.owed_handoffs names, once five quiet minutes passed since it went out, so the post promising its job takes it in as told no sooner than the watch owes its line, which stays owed whatever the session it went to answered until a job of its order is made or started; never a PR's head, which a push moves while the same checks fail, a stamp or the time of a read, which move with nothing changed, nor the stage stage.of names from those, which relabels a PR with nothing of its own changed, such as red to behind once a PR below it in its stack goes red."""
    jobs = {job_id: job for job_id, job in state.orders_jobs(data, group).items() if job["order"] in group}
    prs = {key: data["prs"][key] for key in incident_prs(data, group, jobs, registered) if key in data["prs"]}
    return {
        **{order_id: [data["orders"][order_id]["state"]] for order_id in group},
        **{job_id: state.job_news(job["state"], job["waits"], view.open_cards(job), job["said"]) for job_id, job in jobs.items() if job["state"] != "running"},
        **{key: [pr["state"], pr["checks"], pr["ejected"], pr["tap"] and pr["tap"]["label"]] for key, pr in prs.items()},
        **{f"{HANDOFF_FACT} {handoff['seq']}": ["owed"] for handoff in quiet_handoffs(data, group, moment)},
    }


def quiet_handoffs(data: dict, group: list[str], moment: datetime) -> list[dict]:
    """Each handoff state.owed_handoffs names on the orders that went out five quiet minutes or more before moment."""
    return [handoff for handoff in state.owed_handoffs(data, group) if moment - state.parse_time(handoff["at"]) >= QUIET_THREAD]


def quiet_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Owe him a line in the thread of each open order with news the watch hears, by his pick "A handler posts each step, started by the watch on news", and in each thread of open incidents whose users are not unblocked yet, the watch not having found it over and no order of it keeping a proof, that had none in five minutes and has news, while he is away too, by his pick "Watch-owned clock: no 10-minute silence again, a line every five quiet minutes from a handler reading the jobs; costs one handler session each" and "Proof ends the line: none once users are unblocked; the incident stays open until its root fix is live." and "Owe a line only on news: while a job works or something it names changed; silent while all waits.". News is an order, job or PR of it whose facts moved since the thread's last line or the watch's last report on it, which the watch keeps on each order at the first pass after that line and once each report reaches a handler: a job returning, its first wait beginning or its last ending, never one wait of several ending while it still waits, a PR's state or checks settling, a new commit only through those, Trunk ejecting it or his tap, never its stage relabelled with none of those changed, a card of his opening or closing; on an incident, a job of it at work too. An order owes its line once five minutes passed since its thread's last line or the watch's last report on it, as an incident does, so a thread lined minutes before waits out that window and its line carries every step in it; never while he is away, since his catch-up carries it then, nor once a report closing it is kept or posted, since that report's post is its line. Each such thread gets one report naming each order and incident in it that owes the line, an order only once its facts moved and an incident also while a job of its own is at work, never an order whose job works in a thread an incident shares, with its jobs and what moved, worded as an incident's only when every head it names is an incident, written once, and one handler the watch starts on it, which reads the jobs and posts what changed in that thread, so orders sharing a thread get one line, not one each. A handler already at work in that thread takes the report at its next step. A report reaching a handler, started, handed or held by one already at work on it, keeps the facts it carried, so news a handler judged not worth posting starts no second handler and the next report carries only what moved after it; a failed write or start is said as a failed read is and tried again next pass with the same report."""
    data = state.load(watcher.name)
    away = state.owner_away(data, moment)
    registered = render.prs_by_job(data)
    groups = news_groups(data, heard_orders(watcher, data))
    threads: dict[tuple[str, str] | str, list[str]] = {}
    for head in groups:
        threads.setdefault(state.promise_thread(data["orders"][head]) or head, []).append(head)
    facts = {head: line_facts(data, group, registered, moment) for head, group in groups.items()}
    heard = {head: state.stamp(max(last_line(data, groups[other]) for other in heads)) for heads in threads.values() for head in heads}
    if stale := [head for head in groups if not data["orders"][head]["quiet"] or data["orders"][head]["quiet"]["heard"] != heard[head]]:
        with state.edit(watcher.name) as data:
            for head in stale:
                data["orders"][head]["quiet"] = (data["orders"][head]["quiet"] or {"record": None, "at": None, "handler": None}) | {"heard": heard[head], "news": facts[head]}
    lines = []
    for heads in threads.values():
        if not (heads := [head for head in heads if data["orders"][head]["incident"] or not away]):
            continue
        since = max(quiet_since(data, groups[head]) for head in heads)
        moved = {head: untold(data["orders"][head], facts[head]) for head in heads}
        working = {head: [job_id for job_id, job in state.orders_jobs(data, groups[head]).items() if job["state"] == "running"] for head in heads}
        if moment - since < QUIET_THREAD or not (heads := [head for head in heads if moved[head] or data["orders"][head]["incident"] and working[head]]):
            continue
        source = f"quiet {heads[0]}"
        owed = data["orders"][heads[0]]["quiet"]
        try:
            seq = (
                owed["record"]
                if owed["record"] and not owed["at"]
                else quiet_report(
                    watcher,
                    data,
                    {head: groups[head] for head in heads},
                    since,
                    moment,
                    list(dict.fromkeys(key for head in heads for key in moved[head])),
                    list(dict.fromkeys(job_id for head in heads for job_id in working[head])),
                )
            )
            with state.edit(watcher.name) as data:
                for head in heads:
                    data["orders"][head]["quiet"] |= {"record": seq, "at": None, "handler": None}
            handler, _ = agents.handle_report(watcher.name, seq, f"Record: {seq} Drive: {watcher.name} Scratch: {state.drive_dir(watcher.name) / 'scratch' / f'quiet-{seq}'}")
        except (reads.ReadError, state.DriveError) as error:
            lines += failed(watcher, source, f"line owed in the thread of {', '.join(heads)}", error, moment)
            continue
        watcher.failures.pop(source, None)
        with state.edit(watcher.name) as data:
            for head in heads:
                data["orders"][head]["quiet"] |= {"record": seq, "at": state.stamp(moment), "handler": handler, "news": facts[head]}
    return lines


def news_groups(data: dict, orders: dict[str, dict]) -> dict[str, list[str]]:
    joined = state.joined_orders(data)
    groups = {}
    for head, order in orders.items():
        if not (order["incident"] or order["thread"]) or order["joins"] or order["recovered"] or order["close_report"] or order["closed_on"]:
            continue
        group = [head, *joined.get(head, [])]
        if not any(data["orders"][order_id]["proof"] for order_id in group):
            groups[head] = group
    return groups


def untold(order: dict, facts: dict[str, list]) -> list[str]:
    return [key for key, fact in facts.items() if order["quiet"]["news"].get(key) != fact]


def quiet_report(watcher: Watcher, data: dict, groups: dict[str, list[str]], since: datetime, moment: datetime, moved: list[str], working: list[str], due: str | None = None) -> str:
    """Write the report owing him a line in one thread, for the handler that posts it there: on the orders and incidents with news in it, each by its head and the orders state.feature_orders names for it, then each job and PR of them once, however many of those orders list or name it, or, given due, on the one order owing him the line it promised by that time, which the report calls late once he read present ten minutes past it, never counting the minutes of his absence, and which says when he went away of a line that waited for his return; its seq."""
    heads = list(groups)
    minutes = int((moment - since).total_seconds() // 60)
    one = len(heads) == 1
    incidents = all(data["orders"][head]["incident"] for head in heads)
    past = int((moment - state.parse_time(due)).total_seconds() // 60) if due else 0
    present = int(state.present(data, state.parse_time(due), moment).total_seconds() // 60) if due else 0
    late = timedelta(minutes=present) >= DUE_EVERY
    absence = data["owner"]["absence"] if present < past else None
    orders = [order_id for group in groups.values() for order_id in group]
    jobs = [f"- {render.job_line(data, job_id, job)}" for job_id, job in state.orders_jobs(data, orders).items()]
    handoffs = quiet_handoffs(data, orders, moment)
    if due:
        opening = (
            ", opening with that line being late"
            if late
            else f", opening with its time having waited for his return since he went away at {when(absence['since'])}, never calling it late"
            if absence
            else ""
        )
        asked = (
            f"post where it stands now in its thread{opening}: what moved since its last line, what each job at work is doing, and the clock time of his next line, as \"Next line by <time>\", which that post promises him."
        )
    elif incidents:
        asked = f"post where {'the' if one else 'each'} incident stands now in its thread: what moved since its last line, what each job at work is doing, a card of his on it with each job building its recommended option meanwhile, or that nothing is built before his pick, and what ships next."
    else:
        asked = "post what changed since its last line in its thread: the finding a job returned, each PR that went up or changed stage with his card on it linked, his tap, and who owes the next step, such as the session that re-lands a PR Trunk ejected."
    facts = [
        *(f"{'Incident' if data['orders'][head]['incident'] else 'Order'} {head}, {', '.join(group)}: {data['orders'][head]['text']}" for head, group in groups.items()),
        *incident_pr_lines(watcher.name, orders),
        f"No line to him since {when(state.stamp(since))}, {minutes} minutes.",
        *(
            [
                f"Its line was promised him by {when(due)}, {past} minutes ago"
                + (
                    f", {present} of them while he read present: he went away at {when(absence['since'])}, read away from {when(absence['from'])} and was heard back at {when(absence['until'])}."
                    if absence
                    else "."
                )
            ]
            if due
            else []
        ),
        f"Moved since the thread's last line or the watch's last report on it: {', '.join(moved) or 'nothing'}. At work: {', '.join(working) or 'no job'}.",
        f"Read each job below, its kept report with drive job report <j> and the transcript of one still at work, then {asked}",
        f"Jobs: {len(jobs)}",
        *jobs,
        *(
            [
                f"Handoffs no job of their order was made or started after: {len(handoffs)}. Say in its thread what each asked for, what each record re it answered, and that no job of its order started since, then send the work it asked for on as work no job holds, so a job is put on it.",
                *(line for handoff in handoffs for line in handoff_lines(watcher.name, handoff)),
            ]
            if handoffs
            else []
        ),
    ]
    head = heads[0]
    order = data["orders"][head]
    kind = "incident" if incidents else "order"
    named = f"{kind} {head} has" if one else f"{kind}s {', '.join(heads)} have"
    if due:
        text = (
            f"POST order {head}'s line promised by {when(due)} is {present} minutes late: post where it stands now in its thread, saying it is late"
            if late
            else f"POST order {head} owes him the line it promised by {when(due)}, which waited for his return since he went away at {when(absence['since'])}: post where it stands now in its thread, saying so"
            if absence
            else f"POST order {head} owes him the line it promised by {when(due)}: post where it stands now in its thread"
        )
    elif incidents:
        text = f"POST {named} had no line to him in {minutes} minutes: post where {'it stands' if one else 'each stands'} now in its thread"
    else:
        text = f"POST {named} news since the thread's last line: post what changed there"
    record = {
        "kind": "report",
        "topic": f"watch-{'due' if due else 'quiet'}-{head}",
        "attr": {"order": head, "progress": "true", "quiet": "true"} | ({"due": due} if due else {}) | state.thread_attr(order),
        "text": text,
        "body": "\n".join(facts) + "\n",
    }
    [seq] = reported(watcher, [record])
    return seq


def handoff_lines(name: str, handoff: dict) -> list[str]:
    """A handoff as a quiet report names it, with each record cci holds re it."""
    answers = agents.answering(name, handoff["seq"])
    return [
        f"- {HANDOFF_FACT} {handoff['seq']}, sent {when(handoff['at'])}: {handoff['text']}",
        *(f"  - {answer['seq']} {answer['kind']} from {answer['lane']} re it: {answer['text']}" for answer in answers),
        *([] if answers else ["  - no record re it"]),
    ]


def watched_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Answer each teammate thread an open order the watch hears watches, by his pick "Keep it on the order: the drive's always-running watch answers each reply within about a minute, for one Slack read a minute per thread": the messages there newer than its watch has seen, from anyone but him, the drive and a bot, go in one report, and the watch starts the handler that answers them in that thread, or hands the report to the live handler at work there. A failed read, write or start is said as a failed read is and tried again next read, a start with the same report."""
    lines = []
    for order_id, order in heard_orders(watcher, state.load(watcher.name)).items():
        if not (watch := order["watch"]):
            continue
        source = f"watch {order_id}"
        try:
            if seq := watch["record"] or watched_report(watcher, order_id, order):
                agents.handle_report(watcher.name, seq, f"Record: {seq} Drive: {watcher.name} Scratch: {state.drive_dir(watcher.name) / 'scratch' / f'watch-{seq}'}")
        except (slack.SlackError, reads.ReadError, state.DriveError) as error:
            lines += failed(watcher, source, f"answer of {order_id}'s watched thread {watch['channel']}/{watch['thread']}", error, moment)
            continue
        watcher.failures.pop(source, None)
        if seq:
            with state.edit(watcher.name) as data:
                data["orders"][order_id]["watch"]["record"] = None
    return lines


def watched_report(watcher: Watcher, order_id: str, order: dict) -> str | None:
    """Write the report naming each message newer than an order's watch has seen in its thread from anyone but him, the drive and a bot, for the handler that answers them there, and move the watch past every message read, keeping the report until a handler has it; its seq, None when no such message came."""
    watch = order["watch"]
    read = slack.thread("--channel", watch["channel"], "--thread", watch["thread"], "--since-ts", watch["seen"], "--limit", str(slack.WHOLE_THREAD))
    if not (messages := [message for message in read if float(message["ts"]) > float(watch["seen"])]):
        return None
    theirs = [message for message in messages if not (message.get("from_user") or message.get("from_claude") or message.get("from_bot"))]
    seq = None
    if theirs:
        facts = [
            f"His order {order_id}, from {order['from']}: {order['text']}",
            f"Thread: {watch['channel']}/{watch['thread']}",
            f"New there from anyone but him, the drive and a bot: {len(theirs)}",
            *(f"- {message['ts']} {message.get('user_name') or message.get('user')}: {message.get('text') or ''}" for message in theirs),
        ]
        record = {
            "kind": "report",
            "topic": f"watch-{order_id}",
            "attr": {"order": order_id, "progress": "true", "watch": "true", "thread": watch["thread"]},
            "text": f"POST {len(theirs)} new in the thread {order_id} watches: answer {'it' if len(theirs) == 1 else 'them'} there",
            "body": "\n".join(facts) + "\n",
        }
        [seq] = reported(watcher, [record])
    with state.edit(watcher.name) as data:
        data["orders"][order_id]["watch"].update(seen=max((message["ts"] for message in messages), key=float), record=seq)
    return seq


def incident_pr_lines(name: str, group: list[str]) -> list[str]:
    """Each PR of an incident's orders, on their jobs or named in their words, from its row as the watch last read it from GitHub with his card on it, never from what an order's words or note say of it, which only someone typed."""
    from drive import cli

    data = cli.with_cards(name, state.load(name))
    jobs = state.orders_jobs(data, group)
    lines = [render.pr_line(data, key, data["prs"][key]["card_state"]) if key in data["prs"] else state.pr_url(key) for key in incident_prs(data, group, jobs, render.prs_by_job(data))]
    return [f"PRs, as the watch last read them from GitHub: {len(lines)}", *(f"- {line}" for line in lines)]


def incident_prs(data: dict, group: list[str], jobs: dict[str, dict], registered: dict[str, list[str]]) -> list[str]:
    """Each PR of an incident's orders by its key, on these jobs of them, by render.prs_by_job for the ones registered on a job, or named in their words."""
    return list(
        dict.fromkeys(
            [
                *(key for job_id, job in jobs.items() for key in render.job_pr_keys(job, registered.get(job_id, []))),
                *(key for order_id in group for key in state.named_prs(data, order_id, data["orders"][order_id]["text"])),
            ]
        )
    )


def owed_reporters(data: dict, order: dict, moment: datetime) -> list[dict]:
    """Each reporter whose thread is owed its note and the watch has not said so: a bug report's once the order keeps its proof, a question's once its answer is posted, nothing moved on it for ten minutes, the drive's last line on an answer order counting as a move since posting its answer moves nothing else, and no note is recorded."""
    moved = (order["last_line"] if order["kind"] == "answer" else None) or order["updated"]
    if not (state.may_tell(data, order) and moment - state.parse_time(moved) >= TELL_AFTER):
        return []
    return [held for held in state.untold_reporters(order) if not held["tell_said"]]


def tell_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Each open order proven live, or whose answer is posted, with a reporter's thread that has not heard it ten minutes on, said once for each thread."""
    data = state.load(watcher.name)
    if not (owing := [order_id for order_id, order in heard_orders(watcher, data).items() if owed_reporters(data, order, moment)]):
        return []
    lines = []
    with state.edit(watcher.name) as data:
        for order_id in owing:
            owed = owed_reporters(data, data["orders"][order_id], moment)
            for held in owed:
                held["tell_said"] = state.stamp(moment)
            answered = data["orders"][order_id]["kind"] == "answer"
            happened, asked, heard, note = (
                ("its answer is posted", "its question was asked in", "the answer", "the answer")
                if answered
                else (f"its fix is proven live by {cut(data['orders'][order_id]['proof'], 160)}", "its bug was reported in", "it is fixed", "the fix-live note")
            )
            threads = f"the thread {asked}, has not heard {heard}" if len(owed) == 1 else f"the threads {asked}, have not heard {heard}"
            lines.append(
                f"TELL {order_id}: {happened} and nothing moved on it for 10 minutes, but {' and '.join(held['link'] for held in owed)}, {threads}:"
                f" send its report with --attr close={order_id}, whose handler posts {note} {'there' if len(owed) == 1 else 'in each'} and records it with"
                f" drive order told {order_id} <its permalink>; done refuses until then"
            )
    return lines


def ran_out(job: dict, moment: datetime) -> bool:
    return bool(job["due"]) and state.parse_time(job["due"]) <= moment


def clock_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Each job clock that ran out while its job still runs, said once; a job that returned before it owes none, and either way the clock ends."""
    data = state.load(watcher.name)
    if not any(ran_out(job, moment) and heard_job(watcher, data, job) for job in data["jobs"].values()):
        return []
    lines = []
    with state.edit(watcher.name) as data:
        for job_id, job in data["jobs"].items():
            if not ran_out(job, moment) or not heard_job(watcher, data, job):
                continue
            if job["state"] == "running":
                lines.append(
                    f'DUE {job_id} {job["order"]} "{cut(job["goal"], 80)}" was due {when(job["due"])} and still runs: read its transcript,'
                    " and on a miss start a second job on its brief and set its clock (incidents: Clocks)"
                )
            job["due"] = None
    return lines


def undelivered_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Each text a job an agent or lane started holds, his answers among them, ten minutes after it came and every ten minutes until the watch, a resume or a SendMessage carries it, as its agent's transcript shows each SendMessage by hear_sent."""
    if not (owed := owed_answers(watcher, state.load(watcher.name), moment)):
        return []
    heard = hear_sent(watcher.name, {job_id for job_id, _ in owed})
    if not (still := owed_answers(watcher, heard, moment)):
        return []
    locked = {job_id: why for job_id in {job_id for job_id, _ in still} if (why := orca.locked_out(watcher.name, job_id, heard["jobs"][job_id]))}
    lines = []
    with state.edit(watcher.name) as data:
        for job_id, answer in owed_answers(watcher, data, moment):
            answer["said"] = state.stamp(moment)
            job = data["jobs"][job_id]
            lines.append(f'ANSWER undelivered {job_id}: {unreached(job_id, job, data, answer, locked.get(job_id))} [{job["order"]} {job_id}]')
    return lines


def unreached(job_id: str, job: dict, data: dict, answer: dict, locked: str | None) -> str:
    """A text the job holds that has not reached it, what the watch does with such texts, and the delivery that reaches the job now: for a lane on Orca, drive job resume, which sends it what it holds, or, while orca.locked_out names why that resume is refused, the local launch orca.moved_here names; else the step superseded.delivery names from the session holding the job."""
    held = (
        f'"{cut(answer["words"], 80)}", held at {when(answer["at"])}, has not reached {job_id}; the watch sends it to a worker that takes messages,'
        " hands it to the next worker of one that returned and launches a builder never started with it"
    )
    if locked:
        return f"{held}; drive job resume {job_id} is refused now: {locked}, so {orca.moved_here(job_id)}"
    if job.get("where") == "remote" and job.get("dispatch"):
        return f"{held}, and drive job resume {job_id} delivers it now"
    return f"{held}; {superseded.delivery(data, job_id, job, answer['words'])[1]}"


def owed_answers(watcher: Watcher, data: dict, moment: datetime) -> list[tuple[str, dict]]:
    """Each text a live job an agent or lane started holds that was last said ten minutes ago or more, while none of its waits holds; a job none started gets its texts from the launch that starts it, which carries them, one whose waits hold gets them from the watch once they end, and a finished job's is its ORPHAN's, since no resume carries it."""
    return [
        (job_id, answer)
        for job_id, job in data["jobs"].items()
        if job["state"] not in state.FINISHED_JOB_STATES and (job.get("agent") or job.get("dispatch")) and heard_job(watcher, data, job) and not view.holding(data, job["waits"], watcher.cards)
        for answer in job["undelivered"]
        if moment - state.parse_time(answer["said"] or answer["at"]) >= DUE_EVERY
    ]


def hear_sent(name: str, job_ids: set[str]) -> dict:
    """Hear each SendMessage the coordinator session gave the agent of each of these jobs only it reaches, as superseded.hear hears any text that reaches a worker, read from that agent's transcript back to when the oldest text the job holds or answer of his it owes was given, so his words relayed verbatim leave nothing held or owed; the drive as it stands after."""
    data = state.load(name)
    sent = {}
    for job_id in job_ids:
        job = data["jobs"][job_id]
        given = [*(superseded.held_answer(data, held)[1] for held in job["undelivered"]), *(superseded.given(data, answer_id) for answer_id, _ in superseded.owed(data, job_id))]
        if not given or not state.agent_lane(data, job) or not (path := agent_transcript(session_of(job, data), job, data)).exists():
            continue
        sent[job_id] = [(said, state.parse_time(row["timestamp"])) for row in agents.recent_rows(path, min(given)) if (said := settled.prompt_words(row, agents.MESSAGE_ORIGIN)) is not None]
    if not any(sent.values()):
        return data
    with state.edit(name) as data:
        for job_id, messages in sent.items():
            for said, written in messages:
                superseded.hear(data, job_id, said, written)
    return data


def noted_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Record in cc-notes each card answer of his the drive holds and no recorded answer of his does, as settled.note_cards does once, on the drive's own watch alone, since a sub-coordinator's watch running it beside it would record each answer again."""
    if watcher.project:
        return []
    try:
        settled.note_cards(watcher.name)
    except state.DriveError as error:
        return failed(watcher, "cc-notes", "backfill of his older card answers into cc-notes", error, moment)
    watcher.failures.pop("cc-notes", None)
    return []


def answer_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Each answer of his that leaves a card still asking him, an order or part it lists of an order still open or a PR not yet live with no mark, standing, settled, dropped, superseded or reopened by it, or a live job it touches not yet sent his words, as hear_sent reads each SendMessage, again every ten minutes until each has one: one line for each session owing a step on it, the session state.holding_lane names for the order of each card, order, part and PR, root's for a card no order holds, and the one state.job_lane names for each job, its header naming one of them so line_lane hands it to that session; never one cc-notes marks superseded, by superseded.standing, which is read again only as often."""
    if watcher.cards is None or watcher.project:
        return []
    data = state.load(watcher.name)
    due = [answer_id for answer_id, kept in data["answers"].items() if moment - state.parse_time(kept["said"]) >= DUE_EVERY]
    data = hear_sent(watcher.name, {job_id for answer_id in due for job_id in superseded.unheard(data, data["answers"][answer_id])})
    asking = superseded.asking(data, watcher.cards)
    asked = {answer_id: superseded.listed(data, data["answers"][answer_id], asking) for answer_id in due}
    owed = {answer_id: (superseded.unmarked(data["answers"][answer_id], asked[answer_id]), superseded.unheard(data, data["answers"][answer_id])) for answer_id in due}
    owed = {answer_id: left for answer_id, left in owed.items() if any(left)}
    if not owed:
        return []
    with state.edit(watcher.name) as data:
        for answer_id in owed:
            data["answers"][answer_id]["said"] = state.stamp(moment)
    try:
        owed = dict(superseded.standing(data, list(owed.items())))
    except state.DriveError as error:
        return failed(watcher, "cc-notes supersedes", "read of which answers of his cc-notes marks superseded", error, moment)
    watcher.failures.pop("cc-notes supersedes", None)
    lines = []
    for answer_id, (keys, jobs) in owed.items():
        owners: dict[str, tuple[str | None, list[str], list[str]]] = {}
        for key in keys:
            order_id = asked[answer_id][key]["order"]
            owners.setdefault(state.holding_lane(data, data["orders"][order_id]) if order_id else "root", (order_id, [], []))[1].append(key)
        for job_id in jobs:
            owners.setdefault(state.job_lane(data, data["jobs"][job_id]), (job_id, [], []))[2].append(job_id)
        lines += [
            f"ANSWER {answer_id}{f' {named}' if named else ''} \"{cut(data['answers'][answer_id]['words'], 80)}\": "
            + "; ".join(
                [
                    *([f"{superseded.counted(owned, asked[answer_id])} with no mark, standing, settled, dropped, superseded or reopened by it"] if owned else []),
                    *([f"{', '.join(unheard)} not sent his words"] if unheard else []),
                ]
            )
            + f"; drive answer {answer_id} lists them"
            for named, owned, unheard in owners.values()
        ]
    return lines


def session_transcript(holder: dict) -> Path:
    """The session's transcript, which Claude Code keeps in the project folder of the directory the session first ran in wherever cci resumes it since, as a role's moving into the drive's checkout; the path its own folder would hold while it has written none."""
    named = f"{holder['session']}.jsonl"
    return next((config_dir() / "projects").glob(f"*/{named}"), config_dir() / "projects" / NOT_SLUG.sub("-", holder["cwd"]) / named)


def transcript(holder: dict, agent: str) -> Path:
    return session_transcript(holder).with_suffix("") / "subagents" / f"agent-{agent}.jsonl"


def last_turn(path: Path, kinds: tuple[str, ...] = ("user", "assistant")) -> dict | None:
    with open(path, "rb") as handle:
        end = handle.seek(0, os.SEEK_END)
        span = TAIL_BYTES
        while True:
            start = max(0, end - span)
            handle.seek(start)
            for line in reversed(handle.read().splitlines()[1 if start else 0 :]):
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if entry.get("type") in kinds:
                    return entry
            if not start:
                return None
            span *= 4


def running_build(path: Path) -> tuple[str | None, str | None]:
    """The Claude Code version the session's newest API request reports, and the model its newest turn ran on."""
    version = model = None
    with open(path, "rb") as handle:
        end = handle.seek(0, os.SEEK_END)
        span = TAIL_BYTES
        while True:
            start = max(0, end - span)
            handle.seek(start)
            for line in reversed(handle.read().splitlines()[1 if start else 0 :]):
                if version is None and b'"api-request-shape"' in line:
                    version = json.loads(line).get("version")
                elif model is None and b'"type":"assistant"' in line.replace(b" ", b""):
                    model = json.loads(line)["message"].get("model")
                if version and model:
                    return version, model
            if not start:
                return version, model
            span *= 4


def unknown_model(version: str | None, model: str | None, builds: Path) -> str | None:
    """What his line says when the session's Claude Code build has no entry for its model, so it runs it at a default window."""
    binary = builds / version if version else None
    if not model or not binary or not binary.is_file():
        return None
    with open(binary, "rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as image:
        if image.find(model.encode()) >= 0:
            return None
    return f"runs {model} on Claude Code {version}, which has no entry for that model and runs it at a default context window: restart it on the installed build on his word"


def context_tokens(entry: dict) -> int:
    usage = entry["message"].get("usage") or {}
    return sum(usage.get(key, 0) for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))


def compact_due(data: dict, moment: datetime) -> str | None:
    """Why the communicator should compact now: past the mark, and both it and he quiet, so no message lands mid-compaction."""
    if not (held := data["roles"].get("communicator")):
        return None
    path = session_transcript(held)
    if not path.exists() or moment - datetime.fromtimestamp(path.stat().st_mtime).astimezone() < COMPACT_WHEN_QUIET:
        return None
    heard = (data.get("owner") or {}).get("heard")
    if heard and moment - state.parse_time(heard) < COMPACT_WHEN_QUIET:
        return None
    turn = last_turn(path)
    if not turn or not agents.ended(turn):
        return None
    tokens = context_tokens(turn)
    return f"at {tokens} tokens of context, quiet 10 minutes" if tokens >= COMPACT_PAST_TOKENS else None


def compact_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Type /compact into the first Orca terminal the communicator runs in at a quiet moment, never at its context limit mid-conversation, and into none while no terminal shows it; the drive's own watch alone does."""
    data = state.load(watcher.name)
    if watcher.project or not (why := compact_due(data, moment)):
        return []
    try:
        session = data["roles"]["communicator"]["session"]
        if not (terminals := orca.session_terminals([session])[session]):
            return []
        orca.request("terminal", "send", "--terminal", terminals[0]["handle"], "--text", "/compact", "--enter")
    except orca.OrcaError as error:
        return read_failed(watcher, "communicator compaction", error, moment)
    watcher.failures.pop("communicator compaction", None)
    return [f"WATCH compacted the communicator {why}"]


def coordinator(data: dict, project: str | None) -> dict:
    """The session a watch posts to: a project's sub-coordinator, or the coordinator."""
    return data["subcoordinators"][project] if project else data["roles"]["coordinator"]


def down_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Tell the communicator, once each time it exits, that the coordinator this watch posts to runs no Claude Code process, so nothing hears the drive's records: the communicator restarts it that minute, resuming its session, and never reports it to him as down."""
    held = coordinator(state.load(watcher.name), watcher.project)
    if orca.live_pid(held["session"]) is not None:
        watcher.down = None
        return []
    if watcher.down == held["session"]:
        return []
    watcher.down = held["session"]
    role = f"sub-coordinator for {watcher.project}" if watcher.project else "coordinator"
    line = (
        f"DOWN the {role}'s Claude Code process has exited, so nothing hears the drive's records: restart it now, with no word from"
        f" him, by steps 5 to 7 of drive manual migration, Into an Orca terminal, creating the terminal that shows tmux session"
        f" {state.alias(watcher.name, 'coordinator', watcher.project)}, where cci session run resumes {held['session']} in {held['cwd']},"
        " and tell him it was restarted, never that it is down"
    )
    return sent(watcher, "communicator", [line], moment)


def dashboard_lines(watcher: Watcher) -> list[str]:
    """Hand the drive's dashboard to its launch agent when no agent keeps it, as one started before the agent existed, telling the coordinator once each time it then does not start, a defect of the drive's code; a proof's drive and a project's watch leave it alone."""
    if watcher.project or state.scratch() or launchd.kept(dashboard.label(watcher.name)):
        watcher.dashboard_down = False
        return []
    try:
        dashboard.start(watcher.name, dashboard.port_of(watcher.name))
    except state.DriveError as error:
        said, watcher.dashboard_down = watcher.dashboard_down, True
        return [] if said else [f"WATCH no launch agent kept the dashboard, and it did not start under one: {error}; its log is {state.drive_dir(watcher.name) / 'dashboard.log'}"]
    watcher.dashboard_down = False
    return []


def receiver_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Keep the receiver taking GitHub's deliveries for every PR the watch hears and every release a job or PR it hears waits on: its launch agent loaded, and a webhook pointing at it on each of their repositories this account administers, each new one owing a read of that repository's PRs, since GitHub delivered nothing of them before it. A repository it does not administer, such as an upstream project, is asked once and never hooked, and github_lines reads its open PRs every READ_EVERY. Each repository is tried whatever another's hook did, and one that cannot be kept, or a receiver not answering on loopback, is said as a failed read is, naming the gh call; a proof's drive leaves it alone."""
    if state.scratch():
        return []
    data = state.load(watcher.name)
    heard = [key for key in state.unmerged_pr_keys(data) if hears(watcher, data, data["prs"][key]["order"])]
    awaited = {found["pr"] for order_id, owner in release_owners(data) if hears(watcher, data, order_id) for wait in owner["waits"] if (found := state.RELEASE_WAIT.fullmatch(wait))}
    errors: list[str] = []
    try:
        receiver.keep()
    except state.DriveError as error:
        errors.append(str(error))
    for repo in sorted(set(receiver.unhooked({github.repo_of(key) for key in [*heard, *awaited]})) - watcher.unhookable):
        try:
            if receiver.hook(repo):
                watcher.owed |= {key for key in heard if github.repo_of(key) == repo}
            else:
                watcher.unhookable.add(repo)
        except (github.GithubError, state.DriveError) as error:
            errors.append(str(error))
    if not receiver.answering():
        errors.append(f"nothing answers on its port {receiver.PORT}; its log is {receiver.log_path()}")
    if errors:
        return failed(watcher, "receiver", "upkeep of the receiver", state.DriveError("; ".join(errors)), moment)
    watcher.failures.pop("receiver", None)
    return []


def said_last(entry: dict) -> str:
    texts = [block["text"] for block in entry["message"]["content"] if block.get("type") == "text"]
    return first_line(texts[-1]) if texts else "nothing"


class Answered(str):
    """A running codex job's gap once the codex-ask run `drive job launch` started for it ended, carrying that run, which the watch takes as the job's result."""

    run: codex.Run

    def __new__(cls, job_id: str, run: codex.Run) -> Self:
        made = super().__new__(cls, f"{job_id}'s codex-ask run {run.state} and its result is not recorded")
        made.run = run
        return made

    def take(self, name: str, data: dict, job_id: str) -> str | None:
        return codex.take(name, data, job_id, self.run)


class Idle(str):
    """A local agent's gap that waking it closes: its turn ended on a result its job never took, or on no result line. agent_gap adds the step that wakes it, never local_gap, since that step's place.woken judges every builder by local_gap."""


def agent_gap(job_id: str, job: dict, data: dict, moment: datetime) -> str | None:
    """A running local job's gap: a codex job's from its codex-ask run, and while an incident's second-lane builder holds it beside that run, the builder's once the run has not ended, so whichever delivers first sets the job; any other job's from its agent's transcript, an idle agent's naming superseded.delivery's step that wakes it from the session holding the job."""
    if job["kind"] == "codex" and (job.get("codex_run") or not job.get("agent")):
        gap = codex_gap(job_id, job)
        if isinstance(gap, Answered) or not job.get("agent"):
            return gap
    gap = local_gap(job_id, job, data, moment)
    return f"{gap}: {superseded.delivery(data, job_id, job, gap, 'this line')[1]}" if isinstance(gap, Idle) else gap


def codex_gap(job_id: str, job: dict) -> str | None:
    """A running codex job's gap from the codex-ask run its launch recorded: its run once it ended, which the watch takes, none while it goes, and why when codex-ask cannot read it, when no launch started one, or when its ended run was taken before the job ran again, which only its next launch answers."""
    if not job.get("codex_run"):
        return f"{job_id} has no codex-ask run: drive job launch {job_id} starts it on its brief"
    try:
        run = codex.run_of(job)
    except codex.CodexError as error:
        return f"{job_id}'s codex-ask run could not be read: {cut(str(error), 100)}"
    if not run.ended:
        return None
    if job.get("returned") and run.reply.stat().st_mtime <= state.parse_time(job["returned"]).timestamp():
        return f"{job_id}'s codex-ask run {run.state} before its last return: drive job launch {job_id} starts its next"
    return Answered(job_id, run)


def codex_asks(name: str, job_id: str) -> list[str]:
    """Keep the ASK block a codex job's reply names on its `ASK: <file>` line, or on an `ASK <file>` line it returned, as `drive job ask` keeps a builder's, in the pass its run is taken, so his card goes up as the lane returns while the recommended option it built ships, since a codex run is never resumed mid-run and an incident never stops on a choice; what its JOB line says of it: where his card stands, the settled line when he already answered that same question, or why it was not kept."""
    from drive import cli

    if not (newest := state.returns(name, job_id)) or not (named := ASK_FIELD.search(newest[0]["body"])):
        return []
    try:
        _, _, question, said = cli.keep_ask(name, job_id, Path(named[1]).expanduser().read_text(), None)
    except (OSError, state.DriveError, slack.SlackError) as error:
        return [f"its ASK: {named[1]} is not kept: {error}; keep it with drive job ask {job_id} {named[1]}"]
    if question["closed"]:
        return [said]
    kept, _, drawn = said.partition("\n")
    return [f"its ASK is kept in {kept}, {drawn}, and the recommended option it built ships meanwhile"]


def fix_shipped(name: str, builder_id: str) -> str:
    """What a codex fix job's JOB line says of the builder state.fix_shipper added to ship its work, briefed here so the coordinator starts its agent in the turn this line wakes, or why its brief was not written."""
    from drive import cli

    head = f'its fix is not live, so {builder_id}, an Opus 5.5 long-running:builder the drive added on "{state.INCIDENT_FIX}" to ship it'
    try:
        started = cli.write_brief(name, builder_id, {})[0].splitlines()[0]
    except cli.VERB_ERRORS as error:
        return f"{head}, has no brief: {error}; brief it with drive job brief {builder_id}, then start its agent this turn"
    return f'{head}: start its agent now, run_in_background, on the prompt "{started}"'


def session_of(job: dict, data: dict) -> dict | None:
    """The bound session whose agent runs a local job."""
    session = job.get("session")
    return next((held for held in state.bound_sessions(data).values() if session and held["session"] == session), None)


def local_gap(job_id: str, job: dict, data: dict, moment: datetime) -> str | None:
    """A running local job's gap from its agent's transcript: one no agent has taken that no worker started, by worker_started, which only state.start writes, never started, whoever's session added it, since drive job add stamps the adding session on every job; else its agent's session, transcript and last turn."""
    if not (job.get("agent") or job.get("worker_started")):
        return f"{job_id} never started: no agent has taken it, so start its agent now"
    holder = session_of(job, data)
    if holder is None:
        return f"{job_id}'s agent ended with an earlier session"
    if not job.get("agent"):
        return f"{job_id} has no agent recorded"
    path = agent_transcript(holder, job, data)
    if not path.exists():
        return f"{job_id}'s agent {job['agent']} has no transcript"
    quiet = moment - datetime.fromtimestamp(path.stat().st_mtime).astimezone()
    if quiet < HANDOFF:
        return None
    turn = last_turn(path)
    if turn and agents.ended(turn):
        line = said_last(turn)
        if state.result_state(job, line):
            return Idle(
                f'{job_id}\'s agent finished and its result is not recorded: "{cut(line, 100)}"; only drive job report {job_id} - sets it,'
                " or names why it refuses, so wake it to pipe its return there"
            )
        if quiet >= QUIET_FOR and agents.died(turn):
            return f'{job_id}\'s agent stopped {minutes(quiet)} minutes ago on {turn["message"]["stop_reason"]}: "{cut(line, 100)}"; drive job resume {job_id} <the event> relaunches it on a Sprite with a handoff'
        if quiet >= QUIET_FOR:
            return Idle(f'{job_id}\'s agent ended its turn {minutes(quiet)} minutes ago on "{cut(line, 100)}" with no result line, so wake it')
        return None
    if quiet >= QUIET_FOR:
        return f"{job_id}'s agent has written nothing for {minutes(quiet)} minutes"
    return None


def agent_death(job: dict, data: dict) -> str | None:
    """Why a local job's agent takes no message: it ended with its session, which no role holds any more, or its last response ended on an API error or a refusal; None while it works, after a turn it ended itself, or with no transcript to read."""
    if not job.get("agent"):
        return None
    if (holder := session_of(job, data)) is None:
        return "it ended with its session, which no role holds any more"
    return stopped_on(turn) if (turn := stopped_turn(holder, job, data)) else None


def agent_stop(job: dict, data: dict) -> dict | None:
    """The stop of a running local job's agent the watch acts on, its last response: at once when a model's safeguards stopped it, which safeguard_lines passes to him, and when it ended on an API error only once the session holding it has had a response answered since, so the fresh worker the watch hands the job to does not start inside the outage that stopped it; None until then, and for an agent that ended with its session."""
    if not job.get("agent") or (holder := session_of(job, data)) is None or not (turn := stopped_turn(holder, job, data)):
        return None
    return turn if agents.refused(turn) or answered_since(holder, turn) else None


def answered_since(holder: dict, turn: dict) -> bool:
    """Whether the bound session has had a response answered after the turn, so whatever starts after it does not land inside the outage that stopped that turn."""
    main = session_transcript(holder)
    answered = main.exists() and last_turn(main, ("assistant",))
    return bool(answered) and not agents.died(answered) and state.parse_time(answered["timestamp"]) > state.parse_time(turn["timestamp"])


def stopped_turn(holder: dict, job: dict, data: dict) -> dict | None:
    """The last row of a local job's agent's transcript when that response ended on an API error or a refusal."""
    path = agent_transcript(holder, job, data)
    return turn if path.exists() and (turn := last_turn(path)) and agents.died(turn) else None


def stopped_on(turn: dict) -> str:
    return f'its last response ended on {turn["message"]["stop_reason"]} at {when(turn["timestamp"])}: "{cut(said_last(turn), 100)}"'


def lane_stop(worker: dict, data: dict) -> tuple[dict, Path] | None:
    """The last row of the newest transcript in the worktree of a lane on this Mac, and that transcript, when a model's safeguards stopped that response, or when it ended on an API error and a session bound to the drive has had a response answered since; None until then."""
    sessions = config_dir() / "projects" / NOT_SLUG.sub("-", worker["terminal"]["worktreePath"])
    path = max(sessions.glob("*.jsonl"), key=lambda found: found.stat().st_mtime, default=None)
    if not path or not (turn := last_turn(path)) or not agents.died(turn):
        return None
    return (turn, path) if agents.refused(turn) or any(answered_since(held, turn) for held in state.bound_sessions(data).values()) else None


def resume_lane(watcher: Watcher, job_id: str, job: dict, terminal: str, stop: tuple[dict, Path], moment: datetime) -> list[str]:
    """Type agents.RESUMED into the terminal of a lane on this Mac that stopped, once for each stop, as the StopFailure hook resumes a lane on a Sprite, whose RESUMED prompts count toward the same four; once four resumes met the stop again, say so once and type no more."""
    turn, path = stop
    if watcher.resumed.get(job_id) == turn["timestamp"]:
        return []
    head = f'LANE {job_id} {job["order"]} stopped {orca.ON_MAC}: {stopped_on(turn)}'
    if (tries := agents.resumes(agents.tail(path))) >= len(agents.OUTAGE_WAITS):
        watcher.resumed[job_id] = turn["timestamp"]
        return [f"{head}, after {tries} resumes met the same stop. The watch types no more into it; drive job resume {job_id} <the event> sends it one"]
    source = f"resume of {job_id}"
    try:
        orca.type_prompt(terminal, agents.RESUMED)
    except orca.OrcaError as error:
        return failed(watcher, source, f"typing the resume into {job_id}'s terminal {terminal}", error, moment)
    watcher.failures.pop(source, None)
    watcher.resumed[job_id] = turn["timestamp"]
    return [f"{head}. The watch typed the resume prompt into its terminal {terminal}"]


def agent_transcript(holder: dict, job: dict, data: dict) -> Path:
    """A local job's agent's transcript: its handler's own session's, else the one beside the session holding it."""
    return Path(own) if (own := state.own_transcript(data, job)) else transcript(holder, job["agent"])


def last_write(job: dict, data: dict) -> tuple[datetime, dict | None]:
    """When a local job's agent last wrote its transcript, and the turn it wrote last."""
    path = agent_transcript(session_of(job, data), job, data)
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone(), last_turn(path)


def remote_gap(job_id: str, worker: dict, moment: datetime) -> str | None:
    projection = worker["projection"]
    if projection["outcome"] != "in_progress":
        if moment - state.parse_time(worker["dispatch"]["completedAt"]) < HANDOFF:
            return None
        return f"{job_id}'s lane {projection['outcome']} and its result is not recorded"
    output = (worker.get("terminal") or {}).get("lastOutputAt")
    quiet = output and moment - datetime.fromtimestamp(output / 1000).astimezone()
    if "input" in projection["attention"]["categories"] and quiet and quiet >= QUIET_FOR:
        return f"{job_id}'s lane has sat at its prompt for {minutes(quiet)} minutes"
    return None


def ended_gap(job_id: str, job: dict, ended: dict, moment: datetime) -> str | None:
    """A running job whose lane died or finished and no next lane replaced in the time a relaunch takes: none while the watch's relaunch of it runs, and once that relaunch ended or overran, the line names it and its log; a lane whose death is the job's second since its last line, which no resume starts a worker for, names both deaths with their causes once the resume recorded the second's."""
    gone = moment - state.parse_time(ended["at"])
    relaunch = ended.get("relaunch")
    if relaunch and state.alive(relaunch["pid"]) and moment - state.parse_time(relaunch["at"]) < RELAUNCH:
        return None
    if ended.get("cause") and (first := state.died_before(job, ended["dispatch"])):
        return (
            f"{job_id}'s lane died twice since its last line, so drive job resume starts no worker for it: on Sprite {first['workspace']} at {when(first['at'])}"
            f" ({state.why_died(first['why'], first['cause'])}) and on Sprite {ended['workspace']} at {when(ended['at'])} ({state.why_died(ended['why'], ended['cause'])});"
            f" drive job launch {job_id} --local moves it to a fresh worktree {orca.ON_MAC}"
        )
    if not relaunch:
        return None if gone < RELAUNCH else f"{job_id}'s lane ended {minutes(gone)} minutes ago ({cut(ended['why'], 60)}), was not resumed and its result is not recorded"
    return (
        f"{job_id}'s lane ended {minutes(gone)} minutes ago ({cut(ended['why'], 60)}) and the watch's drive job resume at"
        f" {when(relaunch['at'])} started no next lane, its output in the drive's {RELAUNCHES_LOG}"
    )


def launching(job: dict, moment: datetime) -> bool:
    """Whether the job's lane launch began within the time one takes, so a job with no dispatch yet is starting, not stuck."""
    return bool(job.get("launching")) and moment - state.parse_time(job["launching"]) < RELAUNCH


def agent_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Each running job's gap from its agent or lane, with each PR it has open, a remote lane, running or waiting, whose worker died, and a running job's lane Orca could not reach three reads running, which wake hands to drive job resume, its failed reads neither counted nor said while busy reads another drive verb acting on the job, as the wake an earlier watch started does, and a running lane on this Mac whose last response ended on an API error, which resume_lane resumes through its terminal, while a job whose agent or lane on this Mac a model's safeguards stopped goes to safeguard_lines; a lane recorded by its dispatch alone gets its Run, Task and terminal from the worker-show read for its liveness, and a lane filed on this Mac whose worker-show names a Sprite is filed on that Sprite; a live lane whose job keeps texts queued has them settled by its mailbox each pass, by state.take_mail, and a remote lane whose worker finished or exited has the mail it never read held on its job once for its dispatch by unread_lines, before death_lines relaunches a running job's lane. A local agent that ended its turn on a result line its job never took is a gap, since only `drive job report` sets a job's state from its return; a codex job no agent holds has its codex-ask run taken once that run ended, its returned event telling the coordinator the return, failed or not, with where his card on its ASK stands and, for an incident fix job, the builder that ships its fix."""
    data = state.load(watcher.name)
    gaps, deaths, refusals, heads, found, taken, shippers, finished, boxes, lines = {}, {}, {}, {}, {}, {}, {}, {}, {}, []
    for order_id, order in heard_orders(watcher, data).items():
        for job_id in order["jobs"]:
            job = data["jobs"][job_id]
            if job["order"] != order_id:
                continue
            running = job["state"] == "running"
            remote = job.get("where") == "remote"
            if running and job.get("branch") and (job.get("repo") or remote):
                heads.setdefault(job_id, set()).add((job.get("repo"), job["branch"]))
            if not remote:
                if running and (turn := agent_stop(job, data)):
                    if agents.refused(turn):
                        refusals[job_id] = turn
                    elif ended := state.lane_death(job):
                        gaps[job_id] = ended_gap(job_id, job, ended, moment)
                    else:
                        deaths[job_id] = (job["agent"], stopped_on(turn))
                    continue
                gap = running and agent_gap(job_id, job, data, moment)
                if isinstance(gap, Answered):
                    taken[job_id] = gap
                    heads.pop(job_id, None)
                elif running:
                    gaps[job_id] = gap
                continue
            if not job.get("dispatch"):
                if running:
                    gaps[job_id] = None if launching(job, moment) else f"{job_id} never started {orca.ON_MAC if state.on_mac(job) else 'on its Sprite'}"
                continue
            if job["state"] not in LIVE_JOB_STATES:
                continue
            if ended := state.lane_death(job):
                if running:
                    gaps[job_id] = ended_gap(job_id, job, ended, moment)
                continue
            source = f"lane of {job_id}"
            try:
                worker = orca.worker(job["dispatch"])
            except orca.OrcaError as error:
                if why := orca.gone(error):
                    deaths[job_id] = (job["dispatch"], why)
                    continue
                if running and orca.UNREACHABLE in str(error) and busy(watcher, job_id, moment):
                    continue
                said = read_failed(watcher, source, error, moment)
                if running and orca.UNREACHABLE in str(error) and watcher.failures[source] == FAILURES_BEFORE_SAYING:
                    wake(watcher, job_id, moment)
                    said = [
                        f'LANE {job_id} {job["order"]} unreachable {orca.place(job)}: {error}, while it worked on "{cut(job["said"] or job["goal"], 60)}". The watch ran'
                        f" drive job resume {job_id}, which wakes that Sprite and restarts the lane there, else on a fresh Sprite, its output in the drive's {RELAUNCHES_LOG}"
                    ]
                lines += said
                continue
            watcher.failures.pop(source, None)
            lane = orca.placement(worker) if state.on_mac(job) else {}
            if not job.get("run"):
                lane |= orca.run_of(worker)
            if any(job.get(field) != value for field, value in lane.items()):
                found[job_id] = lane
            if (orca.death(worker) or worker["worker"]["state"] in orca.FINISHED) and job["dispatch"] not in watcher.mail_read:
                finished[job_id] = job | lane
            elif job["queued"]:
                try:
                    boxes[job_id] = (job["dispatch"], orca.mailbox(job, job["dispatch"]))
                except orca.OrcaError as error:
                    lines += read_failed(watcher, f"mail of {job_id}", error, moment)
                else:
                    watcher.failures.pop(f"mail of {job_id}", None)
            if job["undelivered"] and state.SLOT_WAIT not in job["waits"] and orca.takes_messages(job, worker) and not busy(watcher, job_id, moment):
                send_held(watcher, job_id, moment)
            if why := orca.death(worker):
                deaths[job_id] = (job["dispatch"], why)
            elif running:
                gaps[job_id] = remote_gap(job_id, worker, moment)
                if (terminal := orca.typed(job, worker)) and (stop := lane_stop(worker, data)):
                    if agents.refused(stop[0]):
                        refusals[job_id] = stop[0]
                    else:
                        lines += resume_lane(watcher, job_id, job, terminal, stop, moment)
                if branch := ((worker.get("terminal") or {}).get("branch") or "").removeprefix("refs/heads/"):
                    heads.setdefault(job_id, set()).add((job.get("repo"), branch))
    if found or taken or boxes:
        with state.edit(watcher.name) as data:
            for job_id, lane in found.items():
                data["jobs"][job_id].update(lane)
            for job_id, (dispatch, box) in boxes.items():
                if data["jobs"][job_id]["dispatch"] == dispatch:
                    state.take_mail(data["jobs"][job_id], box, False)
            for job_id, gap in taken.items():
                if builder_id := gap.take(watcher.name, data, job_id):
                    shippers[job_id] = builder_id
    for job_id in taken:
        state.returned(watcher.name, data, job_id, "; ".join([data["jobs"][job_id]["said"], *codex_asks(watcher.name, job_id), *([fix_shipped(watcher.name, shippers[job_id])] if job_id in shippers else [])]))
    lines += unread_lines(watcher, finished, {job_id for job_id, job in finished.items() if job_id in deaths and job["state"] == "running"}, moment)
    watcher.made += lines
    gapped = {job_id: gap for job_id, gap in gaps.items() if gap}
    unlisted, failed = untracked_prs(watcher, data, {job_id: heads[job_id] for job_id in gapped if job_id in heads}, moment)
    watcher.agent_gaps = {job_id: stranded(job_id, data, gap, unlisted.get(job_id, {})) if gap else None for job_id, gap in gaps.items()}
    return failed + death_lines(watcher, deaths, moment) + safeguard_lines(watcher, refusals, moment)


def unread_lines(watcher: Watcher, finished: dict[str, dict], relaunched: set[str], moment: datetime) -> list[str]:
    """Hold on each job whose lane's worker ended, finished or exited, while the job runs or waits, the texts Orca queued in that worker's Dispatch and it never read, read once for that dispatch from the Orca that keeps its mailbox, by cli.hold_unread, which takes each text it shows read off the job's queued list, since a sent text lives only there until its worker reads it and Orca shows a finished worker none of it; each job that held any gets `drive job resume` as a process of its own, whose relaunch carries them in its next worker's handoff, unless the death this pass records already relaunches it, and a JOB line naming them. A read Orca refuses is read again next pass, and so is the mail of a job busy reads another drive verb acting on, so its resume starts once that verb ends."""
    from drive import cli

    lines = []
    for job_id, lane in finished.items():
        if busy(watcher, job_id, moment):
            continue
        source = f"unread mail of {job_id}"
        try:
            held = cli.hold_unread(watcher.name, job_id, lane)
        except orca.OrcaError as error:
            lines += read_failed(watcher, source, error, moment)
            continue
        watcher.failures.pop(source, None)
        watcher.mail_read.add(lane["dispatch"])
        if not held:
            continue
        carried = "its lane's relaunch carries it" if job_id in relaunched else f"the watch ran drive job resume {job_id}, whose relaunch carries it, its output in the drive's {RELAUNCHES_LOG}"
        if job_id not in relaunched:
            send_held(watcher, job_id, moment)
        lines.append(f"JOB {job_id} {lane['order']} {render.unread(held)}; {carried}")
    return lines


def safeguard_lines(watcher: Watcher, refusals: dict[str, dict], moment: datetime) -> list[str]:
    """Pass Claude Code's notice on each stop a model's safeguards made of a running job's agent or lane to him in one POST report each, as the notice asks, then set each job stopped, so nothing resumes, relaunches or restarts it or does its work and only `drive job launch` on his word runs it again; a post that fails leaves its jobs running, so the next read posts them again, said from its third failure in a row."""
    if not refusals:
        return []
    data = state.load(watcher.name)
    try:
        reported(watcher, [safeguard_report(data, job_id, turn) for job_id, turn in refusals.items()])
    except state.DriveError as error:
        return failed(watcher, "safeguards stops", "post to him of a model's safeguards stop", error, moment)
    watcher.failures.pop("safeguards stops", None)
    with state.edit(watcher.name) as data:
        for job_id, turn in refusals.items():
            data["jobs"][job_id].update(state="stopped", said=f'{SAFEGUARDED}: "{cut(agents.notice(turn), 100)}"; the notice went to him and nothing restarts it', updated=state.stamp(moment))
    return [
        f"LANE {job_id} {data['jobs'][job_id]['order']} {SAFEGUARDED}: the watch set it stopped, restarts nothing and sent Claude Code's notice to him, as it asks;"
        f" drive job launch {job_id} runs it again only on his word"
        for job_id in refusals
    ]


def safeguard_report(data: dict, job_id: str, turn: dict) -> dict:
    """The report passing him whole the notice Claude Code wrote when a model's safeguards stopped a job's agent, in the thread its order's lines to him go in."""
    job = data["jobs"][job_id]
    order = data["orders"][job["order"]]
    facts = [f"Order {job['order']}, job {job_id}: {order['text']}", f"Its work: {job['said'] or job['goal']}", f"Claude Code's notice: {agents.notice(turn)}"]
    return {
        "kind": "report",
        "topic": f"watch-safeguards-{job_id}",
        "attr": {"order": job["order"], "job": job_id} | state.thread_attr(order),
        "text": f"POST {job_id}'s agent was {SAFEGUARDED} and the drive restarts nothing: post Claude Code's notice to him whole, as it asks",
        "body": "\n".join(facts) + "\n",
    }


def stall_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Age each thing the drive's chains of waits end on, a PR stack's card, a PR's release, a staging env or a job, and act once on each that stood unmoved past its deadline, however many jobs and PRs wait on it, by his words "the watch reports a stall for each job held by the same open Approve card, so three jobs waiting on one card would each send him a line" and his pick "an automated watchdog that spawns a small agent if it detects anything truly stick which can go read transcript etc": start one watchdog agent on it, at most two at a time and six an hour, oldest stall first, never on a chain ending at a step of his, his hold on every release read once a pass, nor on a staging env, or a release on it, behind another drive's staging claim until that claim ends, as stall.behind() reads the claims of every other drive on this Mac once a pass; once that agent answers, or ten minutes pass, say one STUCK line naming it, each job and PR waiting on it and what the agent found, a NEXT brief.release_ask_refusal() refuses replaced by its refusal, or that alone; and, by his pick "One line to you, once per stall, after the coordinator had 30 minutes and nothing changed; never while you are away.", once that line stood 30 minutes with the thing unmoved and he is here, write him one report the communicator's handler posts in that work's thread. Each stall is one row of the drive's stalls, aged under the drive's lock by every watch and acted on only by the watch hearing the job answering for the first job or PR waiting on it, each step claimed under the lock with the pid and start time of the watch taking it, as waiting_lines claims its lines, then taken outside the lock and settled under it, so a watch restart adopts its agent, two watches never take one step and nothing is said twice; a thing that moves starts a fresh one."""
    if watcher.cards is None:
        return []
    known = state.load(watcher.name)
    try:
        on_hold = bool(settled.hold(known["roles"]["coordinator"]["cwd"]))
    except state.DriveError as error:
        return failed(watcher, "stall hold", "stall read of his hold on every release", error, moment)
    watcher.failures.pop("stall hold", None)
    found = stall.things(known, stall.chains(known, watcher.cards, on_hold, moment), state.others_claims(watcher.name, moment))
    printed = {thing: stall.words(known, watcher.cards, held) for thing, held in found.items()}
    aged = {thing: stall.aged(known["stalls"].get(thing), printed[thing], held["joins"], moment) for thing, held in found.items()}
    if aged == known["stalls"] and not owed(watcher, known, found, aged, moment):
        return []
    claimer = {"pid": os.getpid(), "started": started(os.getpid())}
    with state.edit(watcher.name) as data:
        stalls = data["stalls"] = {thing: stall.aged(data["stalls"].get(thing), printed[thing], held["joins"], moment) for thing, held in found.items()}
        steps = owed(watcher, data, found, stalls, moment)
        for thing in steps:
            stalls[thing]["claim"] = claimer
        claims = {thing: dict(stalls[thing]) for thing in steps}
    lines = []
    for thing, step in steps.items():
        held = claims[thing]
        if step == "agent":
            out = state.drive_dir(watcher.name) / "stalls" / f"{NOT_SLUG.sub('-', thing)}-{moment:%Y%m%dT%H%M%S}.md"
            try:
                pid = agents.watchdog(watcher.name, watchdog_brief(watcher, known, thing, found[thing], held, moment), str(orca.checkout(watcher.name, coordinator(known, watcher.project)["cwd"])), out)
            except (OSError, state.DriveError) as error:
                settle(watcher.name, thing, held, {})
                lines += failed(watcher, "watchdog", f"watchdog agent on {thing}", error, moment)
                continue
            watcher.failures.pop("watchdog", None)
            settle(watcher.name, thing, held, {"agent": {"pid": pid, "out": str(out), "at": state.stamp(moment)}})
        elif step == "said":
            answered = stall.answer(Path(held["agent"]["out"]).read_text())
            order = known["orders"][stall.owner(known, found[thing]["subjects"][0])[1]]
            try:
                refused = "NEXT" in answered and brief.release_ask_refusal("handoff", [f"{state.holder_lane(order) or 'coordinator'}@{watcher.name}"], answered["NEXT"], known)
            except state.DriveError as error:
                settle(watcher.name, thing, held, {})
                lines += failed(watcher, f"stall {thing}", f"release check of the NEXT {thing}'s watchdog agent answered", error, moment)
                continue
            watcher.failures.pop(f"stall {thing}", None)
            done = {"found": answered | ({"NEXT": refused} if refused else {}), "said": state.stamp(moment)}
            settle(watcher.name, thing, held, done)
            lines.append(stuck_line(known, thing, found[thing], held | done, moment))
        else:
            try:
                [seq] = reported(watcher, [stall_report(known, thing, found[thing], held, watcher.cards, moment)])
            except (reads.ReadError, state.DriveError) as error:
                settle(watcher.name, thing, held, {})
                lines += failed(watcher, f"stall {thing}", f"line to him on {thing}, which stood unmoved", error, moment)
                continue
            settle(watcher.name, thing, held, {"told": state.stamp(moment)})
            watcher.failures.pop(f"stall {thing}", None)
            agents.handle_report(watcher.name, seq, f"Record: {seq} Drive: {watcher.name} Scratch: {state.drive_dir(watcher.name) / 'scratch' / f'stall-{seq}'}")
    return lines


def owed(watcher: Watcher, data: dict, found: dict[str, dict], stalls: dict[str, dict], moment: datetime) -> dict[str, str]:
    """The step each stall past its deadline owes now, oldest first, of those this watch hears and no live watch claims: its agent while the watchdog caps have room, its STUCK line, or his report."""
    running, begun = stall.starts(stalls, moment)
    room = min(stall.AT_ONCE - running, stall.STARTS_AN_HOUR - begun)
    away = state.owner_away(data, moment)
    steps = {}
    for thing in stall.stalled(data, stalls, found, moment):
        if claimed(stalls[thing]["claim"]) or not heard_job(watcher, data, data["jobs"][stall.owner(data, found[thing]["subjects"][0])[0]]):
            continue
        step = stall.step(stalls[thing], moment, away)
        if step == "agent":
            if room <= 0:
                continue
            room -= 1
        if step:
            steps[thing] = step
    return steps


def settle(name: str, thing: str, held: dict, done: dict) -> None:
    """Give back a stall's claim, keeping what its step did, on the row it was claimed on; a stall that moved meanwhile is a fresh row and keeps nothing."""
    with state.edit(name) as data:
        if (kept := data["stalls"].get(thing)) and kept["since"] == held["since"]:
            kept |= {"claim": None} | done


def claimed(claim: dict | None) -> bool:
    """Whether the watch that claimed a step still runs: its pid holds the process that started when the claim says, never one the system handed that pid since."""
    return bool(claim) and started(claim["pid"]) == claim["started"]


def watchdog_brief(watcher: Watcher, data: dict, thing: str, found: dict, held: dict, moment: datetime) -> str:
    """The brief a watchdog agent starts on: the stall, each job and PR waiting on it, their chains to it, the session holding the order of the first of them, which its STUCK line reaches and which alone takes the next step, with how a job's agent on this Mac is woken, or that the job's lane or agent ended, by state.lane_death, so no wake reaches it, what to read, the drive's rows of everything in the chains, the job's returns and its agent's transcript, the holding session's transcript since the thing last moved, and the checks of each PR in the chains, then what the chains' releases and builds rest on, which the agent reads nowhere else: a Release line per release wait in them, what the watch's own read of that wait last found, a Staging line per service a staging wait in them awaits on its env, by staging_line, a Build line per Buildkite build they wait on, what the watch's last Buildkite read of it found, a Card line per open card of his on the chains or naming a service they wait on, and an Answered line per recorded answer of his ccn ranks nearest the job's goal and those services, so it names the answer that settles what it would ask him and orders no card asking it again."""
    job_id, order_id = stall.owner(data, found["subjects"][0])
    job = data["jobs"][job_id]
    holder, holding = state.holding_role(data, data["orders"][order_id])
    named = dict.fromkeys([order_id, job_id, *(link for link in found["chain"] if link in data["jobs"] or link in data["prs"])])
    reading = [f"drive status {' '.join(named)} --drive {watcher.name}", f"drive job replies {job_id} --drive {watcher.name}"]
    wake = ""
    if job.get("agent") and (runs_in := session_of(job, data)):
        reading.append(f"cc-transcript {agent_transcript(runs_in, job, data)}")
        wake = f"; {job_id} runs on this Mac as agent {job['agent']} of session {runs_in['session']}; to wake it: {superseded.delivery(data, job_id, job, '', 'the message')[1]}"
    if ended := state.lane_death(job):
        wake = f"; {job_id}'s lane ended at {when(ended['at'])}: {state.why_died(ended['why'], ended.get('cause'))}, so neither drive job wake nor SendMessage reaches it, and only the holder's drive job resume {job_id} starts its next worker"
    if holding:
        reading.append(f"cc-transcript {session_transcript(holding)}, from {when(held['since'])} on")
    reading += [f"gh pr {verb} {state.pr_url(key)}" for key in named if key in data["prs"] for verb in ("view", "checks")]
    awaited: dict[tuple[str, str], list[str]] = {}
    for link in found["chain"]:
        if staging := state.STAGING_WAIT.fullmatch(link):
            awaited.setdefault((staging["target"], staging["env"]), []).append(staging["commit"][:12])
    return "\n".join(
        [
            f"Stall: {thing}{which_holds(found)} unmoved {minutes(moment - state.parse_time(held['since']))} minutes",
            f"Job: {job_id} of {order_id}: {cut(data['orders'][order_id]['text'], 300)}",
            f"Holder: {holder}, session {holding.get('session') or 'not started'}{wake}",
            f"Chain: {'; '.join(found['chain'].values())}",
            f"Drive: {watcher.name}",
            "Read:",
            *(f"- {line}" for line in reading),
            *(f"Release: {release_line(watcher, data, link)}" for link in found["chain"] if state.RELEASE_WAIT.fullmatch(link)),
            *(f"Staging: {staging_line(watcher, target, env, commits)}" for (target, env), commits in awaited.items()),
            *(f"Build: {build_line(watcher, link)}" for link in found["chain"] if github.BUILD.fullmatch(link)),
            *(f"Card: {card}" for card in stall_cards(data, found["chain"], watcher.cards)),
            *(f"Answered: {answer}" for answer in stall_answers(data, job, found["chain"])),
        ]
    )


def release_line(watcher: Watcher, data: dict, wait: str) -> str:
    """A release wait as the watch's release live read found it: what frees it, by out_because, or that its PR has not merged, that a read holds no line for it, or the clusters of its service, or the env it names, behind its PR."""
    found = state.RELEASE_WAIT.fullmatch(wait)
    key, service = found["pr"], found["service"]
    pr = data["prs"][key]
    if said := out_because(watcher, data, wait):
        return f"{wait}: {said}"
    if not pr["merged_sha"]:
        return f"{wait}: {key} is {github.fold_line(pr) if pr['state'] == github.FOLDED else pr['state'] + ' and unmerged'}, so no release holds it yet"
    if not watcher.serving or not (clusters := watcher.serving.get(wait)):
        return f"{wait}: the watch's tools/ci release live read holds no line for {key}, as it failed or has not run since the wait was added"
    return f"{wait}: {service} on {found['env'] or ', '.join(sorted(env for env, serves in clusters.items() if not serves))} is behind {key}'s merge commit {pr['merged_sha'][:12]}, running an older build"


def staging_line(watcher: Watcher, target: str, env: str, commits: list[str]) -> str:
    """A service staging waits await on one env, with each commit they await there and the commit and release the watch's last tools/ci release live read finds it serving, or that the read finds it on no cluster there or has not run."""
    awaited = f"{target} on {env}, awaited at {', '.join(commits)}"
    if watcher.live is None:
        return f"{awaited}: the watch has made no tools/ci release live read since it started"
    if not (row := next((row for row in watcher.live if row["target"] == target and row["env"] == env), None)):
        return f"{awaited}: the watch's last tools/ci release live read finds no {target} on {env}"
    shipped = row.get("name") or row.get("release")
    return f"{awaited}: the watch's last tools/ci release live read finds it serving {row['commit'][:12]}" + (f" of {shipped}" if shipped else "")


def stall_cards(data: dict, chain: dict[str, str], cards: dict) -> list[str]:
    """His open cards a stall's chains rest on: his card on each PR of them that waits on his tap, and each question card a job of them asked, a link of them names, or whose question or body names a service a release wait of them waits on."""
    subjects = list(chain)
    services = {found["service"] for link in subjects if (found := state.RELEASE_WAIT.fullmatch(link))}
    naming = re.compile(rf"(?<![\w-])(?:{'|'.join(map(re.escape, sorted(services)))})(?![\w-])") if services else None
    taps = [f"his Approve on {key}, {url}" for key in subjects if (pr := data["prs"].get(key)) and (url := view.tap_card(data, pr, cards))]
    asks = [
        f'"{cut(card["question"], 300)}" {card["url"]}, asked by {card["job"] or "no job of the drive"}'
        for card in view.with_questions(data, cards)["questions"]
        if card["job"] in subjects or any(view.names_card(card, link) for link in subjects) or (naming and naming.search(f"{card['question']}\n{card['card'].get('body') or ''}"))
    ]
    return [*taps, *asks]


def stall_answers(data: dict, job: dict, chain: dict[str, str]) -> list[str]:
    """His recorded answers ccn ranks nearest a stall's job's goal and each service a release wait of its chains waits on, with where he gave each, so its watchdog agent names the one that settles what it would ask him."""
    services = dict.fromkeys(found["service"] for link in chain if (found := state.RELEASE_WAIT.fullmatch(link)))
    return [
        f'{answer["id"]} "{cut(answer["asked"], 200)}": "{cut(answer["said"], 300)}"' + (f" {answer['link']}" if answer["link"] else "")
        for answer in settled.matched([text for text in [job["goal"], *services] if text], data)
    ]


def which_holds(found: dict) -> str:
    return f", which holds {', '.join(waiting)}," if (waiting := stall.waiting(found)) else ""


def stuck_line(data: dict, thing: str, found: dict, held: dict, moment: datetime) -> str:
    """The STUCK line a stall's watchdog agent ends on: the thing, each job and PR waiting on it and the thing in words, then its WHY and NEXT, or that it answered nothing."""
    job_id, order_id = stall.owner(data, found["subjects"][0])
    answered = held["found"]
    said = "; ".join(f"{label}: {cut(answered[label], 200)}" for label in ("WHY", "NEXT") if label in answered) or "its watchdog agent answered nothing"
    return (
        f"STUCK {job_id} {order_id}: {thing}{which_holds(found)} unmoved {minutes(moment - state.parse_time(held['since']))} minutes: {cut(held['print'], 400)}. {said};"
        f" its whole answer, WHO and UNREAD with it, in {held['agent']['out']}"
    )


def stall_report(data: dict, thing: str, found: dict, held: dict, cards: dict, moment: datetime) -> dict:
    """The report owing him one line on a stall the session holding its order heard 30 minutes ago with nothing moving since, in the thread of his DM its order's lines to him go in, an incident's too, naming each job and PR waiting on it with its order, and his card on each PR in its chains that waits on his tap as his cards stand now, never as its watchdog agent saw them."""
    job_id, order_id = stall.owner(data, found["subjects"][0])
    order = data["orders"][order_id]
    holder, _ = state.holding_session(data, order)
    answered = held["found"]
    stood, said = (minutes(moment - state.parse_time(held[field])) for field in ("since", "said"))
    waiting = stall.waiting(found)
    facts = [
        f"Order {order_id}, job {job_id}: {order['text']}",
        *([f"Holds: {', '.join(f'{subject} of {stall.owner(data, subject)[1]}' for subject in waiting)}"] if waiting else []),
        f"Unmoved {stood} minutes: {held['print']}",
        f"The STUCK line went to {holder} at {when(held['said'])}, {said} minutes ago.",
        *(f"{label}: {answered[label]}" for label in ("WHY", "WHO", "NEXT", "UNREAD") if label in answered),
        *(
            f"His card on {key} now: {view.tap_card(data, pr, cards) or 'none open at its head, so the watch draws or redraws it'}"
            for key in found["chain"]
            if (pr := data["prs"].get(key)) and state.asks_tap(data, pr)
        ),
    ]
    return {
        "kind": "report",
        "topic": f"watch-stall-{NOT_SLUG.sub('-', thing)}",
        "attr": {"order": order_id, "job": job_id, "stall": "true"} | ({"thread": where[1]} if (where := state.dm_thread(order)) else {}),
        "text": f"POST {thing}{which_holds(found)} has not moved in {stood} minutes and {holder} was told {said} minutes ago: post one line on it in its thread",
        "body": "\n".join(facts) + "\n",
    }


def untracked_prs(watcher: Watcher, data: dict, heads: dict[str, set], moment: datetime) -> tuple[dict[str, dict], list[str]]:
    """For each running job with a gap, the open PRs on its recorded branch or its lane's that the drive does not track once GitHub answered, read from GitHub in one batch; a head with no repo is in the repo of the checkout remote lanes clone."""
    if not heads:
        return {}, []
    try:
        home = github.checkout_repo(data["roles"]["coordinator"]["cwd"]) if any(repo is None for both in heads.values() for repo, _ in both) else None
        resolved = {job_id: {(repo or home, branch) for repo, branch in both} for job_id, both in heads.items()}
        found = github.open_on(sorted(set().union(*resolved.values())))
    except github.GithubError as error:
        return {}, read_failed(watcher, "github branches", error, moment)
    watcher.failures.pop("github branches", None)
    tracked = state.load(watcher.name)["prs"]
    return {
        job_id: {key: pr for key, pr in found.items() if key not in tracked and (github.repo_of(key), pr["branch"]) in both}
        for job_id, both in resolved.items()
    }, []


def stranded(job_id: str, data: dict, gap: str, unlisted: dict[str, dict]) -> str:
    """A running job's gap from its agent or lane, with the checks and review of every PR it has open, each open PR on its branch the drive does not track among them."""
    tracked = [
        f"its PR {key}: checks {pr['checks']}, review {pr['review']}"
        for key in map(state.pr_key, data["jobs"][job_id]["prs"])
        if (pr := data["prs"].get(key)) and pr["state"] == "open"
    ]
    untracked = [f"{key} on its branch {pr['branch']} is not in the drive: checks {pr['checks']}, review {pr['review']}" for key, pr in unlisted.items()]
    return "; ".join([gap, *tracked, *untracked])


def release_lanes(watcher: Watcher, moment: datetime) -> None:
    """Free what each job it hears is done with still holds, never what a running job holds, starting each drive verb that frees one as a process of its own, by state.detach, and keeping that process's pid and time on the drive, so a restarted watch reads what every earlier watch asked. First, by one rule, a remote job that is not running holds no Sprite once its worker's turn is over: the own lane of each job whose line set it done, failed or stopped, or set it waiting or built and whose worker then ended its turn or died, by unended, so a live lane waiting on its own question or on a read it asked keeps it, is ended by `drive job end`, which keeps its transcripts, saves what its Sprite holds on no remote and destroys it, once HANDOFF has passed since that line so the lane finishes its last turn, at most two a pass, the newest line first so an end that keeps failing never holds up the rest, kept as the job's end, so no watch ends that lane again while that end is in_flight, and its Sprite left to that end alone while the end it records on the lane is, ahead of every reclaim, so no reclaim and no install delays an end. Then each Sprite cc-remote still records that an earlier or ended lane of any job left, such as the Sprite a relaunch moved off or one an end kept, destroyed with its Orca environment by --destroy once what it holds on no remote is saved to GitHub as saved/<the job's lane>/<branch>, or else kept by that reclaim, said once and never asked for again; and the dispatch of a done, failed or stopped lane on this Mac once its job has been quiet for QUIET_LANE, as lr's gc --done waited after a lane's last word, whose terminal --run --done closes and whose worktree it removes once clean and pushed, the reclaimer skipping whatever its pauses hold, its death recorded first. Each of those goes to `drive job reclaim`, whose orca.reclaimer kills the reclaimer with every process it started past RECLAIM_SECONDS, kept as the reclaim on the death of the lane that left it, at most two in_flight at once, counted from those records, and none asked again while its own is in_flight; what each run printed goes to the watch log."""
    if state.scratch():
        return
    data = state.load(watcher.name)
    heard = {job_id: job for job_id, job in data["jobs"].items() if heard_job(watcher, data, job)}
    due = [job_id for job_id, job in heard.items() if unended(job, moment)]
    ending = sorted(due, key=lambda job_id: state.parse_time(heard[job_id]["returned"] or heard[job_id]["updated"]), reverse=True)[:RELEASES_AT_ONCE]
    live = {job.get(key) for job in data["jobs"].values() if job["state"] not in state.FINISHED_JOB_STATES for key in ("workspace", "run", "dispatch")} - {None}
    held = [(job_id, lane) for job_id, job in heard.items() for lane in sorted(lane_holds(job, moment, live) - live)]
    asking = [(job_id, lane) for job_id, lane in held if not in_flight((state.death_on(heard[job_id], lane) or {}).get("reclaim"), moment)]
    reclaiming = asking[: max(0, RELEASES_AT_ONCE - len(held) + len(asking))]
    if not ending and not reclaiming:
        return
    log = state.watch_log_path(watcher.name, watcher.project).name
    with state.edit(watcher.name) as data:
        for job_id in ending:
            data["jobs"][job_id]["end"] = {"pid": end_lane(watcher.name, job_id), "at": state.stamp(moment)}
        for job_id, lane in reclaiming:
            job = data["jobs"][job_id]
            if state.on_mac(job):
                state.lane_died(job, lane, RECLAIMED, moment)
            state.death_on(job, lane)["reclaim"] = {"pid": state.detach(watcher.name, ["job", "reclaim", job_id, lane], log), "at": state.stamp(moment)}


def in_flight(started: dict | None, moment: datetime) -> bool:
    """Whether a drive verb the watch started to free a lane, by the pid and time kept on its job, still runs or started within RECLAIM, so the watch asks for that lane again only once that process is gone and RECLAIM_SECONDS, past which orca.reclaimer kills a reclaimer, have passed since it started; a process alive past the time a relaunch takes is another that took its pid."""
    if not started:
        return False
    age = moment - state.parse_time(started["at"])
    return age < RECLAIM or state.alive(started["pid"]) and age < RELAUNCH


def unended(job: dict, moment: datetime) -> bool:
    """Whether the job's own lane on a Sprite cc-remote still records is due its `drive job end`: the job is not running, it is done, failed or stopped or its worker's turn is over, by a worker_done or escalation from that worker or its death, no end ended that lane nor is in_flight on it, and HANDOFF has passed since the line that stopped it, or since it was stopped with none. So a waiting or built job whose worker still waits inside its own question, his ASK's included, which Orca keeps that worker's turn open for, or on a read it asked after its line, keeps its Sprite, and the answer `drive job resume` or its reader sends reaches that worker in its own session."""
    settled = not job["returned"] or moment - state.parse_time(job["returned"]) >= HANDOFF
    turn_over = job["state"] in state.FINISHED_JOB_STATES or not job["turn_open"] or bool(state.lane_death(job))
    return job["state"] != "running" and turn_over and settled and not state.lane_ended(job) and not in_flight(job.get("end"), moment) and orca.holds_sprite(job)


def lane_holds(job: dict, moment: datetime, live: set[str]) -> set[str]:
    """What the job's lanes left for the reclaimer: on this Mac, the dispatch of a done, failed or stopped job quiet for QUIET_LANE whose Run no unfinished job shares and no drive job reclaim freed; on a Sprite, each Sprite cc-remote still records that a lane of its died on or an end ended, never its own lane's before an end ended it, since unended gives that one to `drive job end`, nor while the end that ended it is in_flight, which destroys it itself, nor one a drive job reclaim kept on the newest death on it, since what it may hold on no remote could not be saved, which that reclaim's refusal says once, so the watch asks for it once and a session asks again by hand."""
    if state.on_mac(job):
        quiet = job["state"] in state.FINISHED_JOB_STATES and job.get("dispatch") and job.get("run") not in live | {None} and moment - state.parse_time(job["updated"]) >= QUIET_LANE
        return {job["dispatch"]} if quiet and not (state.death_on(job, job["dispatch"]) or {}).get("freed") else set()
    left = {death.get("workspace") for death in job.get("deaths", [])}
    if not state.lane_ended(job) or in_flight(state.lane_death(job, "dispatch")["ended"], moment):
        left.discard(job.get("workspace"))
    return {workspace for workspace in left - {None} if not state.death_on(job, workspace).get("kept") and orca.holds_sprite({**job, "workspace": workspace})}


def death_lines(watcher: Watcher, deaths: dict[str, tuple[str, str]], moment: datetime) -> list[str]:
    """Record each worker's death on its job: a remote lane's, by its dispatch, or a running local job's agent's, by its agent, and say it once for a running job, whose worker is replaced that pass by `drive job resume` as a process of its own, its pid kept on the death so no later pass starts a second: a Sprite lane's is resumed in its own Claude session on its Sprite, or else relaunched, and an agent's is handed to a lane on a Sprite, or on this Mac when its brief declares a step only this Mac runs. A running job busy reads another drive verb acting on has its death neither recorded nor said, so the first pass after that verb ends relaunches it, or reads the next worker that verb started. A waiting job's death asks no step, since his answer reaches it through drive job resume whether or not its lane lives, so it is recorded and not said: its Sprite is freed by the `drive job end` release_lanes runs on every remote job that is not running once its worker's turn is over, as its death makes it, which copies its Sprite's Orca runtime and worker logs to the drive's folder before the Sprite goes, and its lane on this Mac just waits. A job stopped, done or failed since the pass read it has the death recorded and nothing started or said. A running Sprite lane's line names the file in the drive's folder that resume copies those logs to, and, when another lane of the job died with a cause since its last line, that death and its cause, since the resume then starts no worker unless those logs show its Sprite failed on its own."""
    if not deaths:
        return []
    lines = []
    with state.edit(watcher.name) as data:
        for job_id, (worker, why) in deaths.items():
            job = data["jobs"][job_id]
            if job["state"] in state.FINISHED_JOB_STATES or job.get("where") == "remote" and job["state"] == "waiting":
                state.lane_died(job, worker, why, moment)
                continue
            if job[state.worker_key(job)] != worker or state.lane_death(job) or busy(watcher, job_id, moment):
                continue
            state.lane_died(job, worker, why, moment)
            job["deaths"][-1]["relaunch"] = {"pid": relaunch(watcher, job_id, moment), "at": state.stamp(moment)}
            if job.get("where") != "remote":
                lines.append(
                    f'LANE {job_id} {job["order"]} died as agent {worker} on this Mac: {why}, while it worked on "{cut(job["said"] or job["goal"], 60)}". The watch ran'
                    f" drive job resume {job_id}, which hands the job to a lane on a Sprite, or on this Mac when its brief declares a step only this Mac runs,"
                    f" with a handoff, its output in the drive's {RELAUNCHES_LOG}"
                )
                continue
            fresh = f'{"in a fresh worktree " + orca.ON_MAC if state.on_mac(job) else "on a fresh Sprite"} with a handoff'
            logs = "" if state.on_mac(job) else f"copies its Sprite's Orca runtime and worker logs to {orca.death_logs(watcher.name, job_id, worker)}, "
            again = f"relaunches it {fresh}" if state.on_mac(job) else f"resumes its Claude session on its Sprite, or else relaunches it {fresh}"
            if not state.on_mac(job) and (first := state.died_before(job, worker)):
                again = (
                    f"reads from them why it died and starts no worker unless they show its Sprite failed on its own, a kernel error line after init with no OOM kill"
                    f" and its home volume not full, as its lane on Sprite {first['workspace']} died too since its last line, at {when(first['at'])}"
                    f" ({state.why_died(first['why'], first['cause'])})"
                )
            lines.append(
                f'LANE {job_id} {job["order"]} died {orca.place(job)}: {why}, while it worked on "{cut(job["said"] or job["goal"], 60)}".'
                f" The watch ran drive job resume {job_id}, which {logs}{again}, its output in the drive's {RELAUNCHES_LOG}"
            )
    return lines


def busy(watcher: Watcher, job_id: str, moment: datetime) -> bool:
    """Whether the watch starts no drive verb on the job now, the one gate each of its starts passes: it started one on the job this pass, whose moment every stage of a pass shares, or another drive verb holds the job's own lock, by state.job_lock_held, as a resume a watch started before an install restarted it still does while it wakes the job's Sprite. A start it holds back is left for a pass after that verb ends, so the watch never starts a second verb beside the one acting on the job."""
    return watcher.started.get(job_id) == moment or state.job_lock_held(watcher.name, job_id)


def start_verb(watcher: Watcher, job_id: str, args: list[str], moment: datetime) -> int:
    """Run a drive verb on the job busy let through as its own process, outliving the pass that started it, its output appended to the drive's relaunches log, kept as the pass's start on the job; its pid."""
    watcher.started[job_id] = moment
    return state.detach(watcher.name, args, RELAUNCHES_LOG)


def relaunch(watcher: Watcher, job_id: str, moment: datetime) -> int:
    """Run `drive job resume` by start_verb on a running job whose lane died."""
    return start_verb(watcher, job_id, ["job", "resume", job_id, state.CARRY_ON], moment)


def wake(watcher: Watcher, job_id: str, moment: datetime) -> int:
    """Run `drive job resume` with UNREACHED by start_verb on a running job whose lane Orca could not reach FAILURES_BEFORE_SAYING reads running: its lane_end wakes that lane's Sprite, then sends the text to a worker that takes messages again, or restarts the lane on that Sprite with the text in its handoff, else on a fresh one."""
    return start_verb(watcher, job_id, ["job", "resume", job_id, state.UNREACHED], moment)


def send_held(watcher: Watcher, job_id: str, moment: datetime) -> int:
    """Run `drive job resume` with no text by start_verb on a job holding texts a resume could not deliver, once Orca reads its worker alive and no wait on builder-slot holds it to move here, so the pass never waits on Orca or the job's lock; each goes under the request id it was held with, so Orca never applies one twice."""
    return start_verb(watcher, job_id, ["job", "resume", job_id], moment)


def carry_held(watcher: Watcher, moment: datetime) -> None:
    """Carry the texts each heard job holds, his answers among them, with no session's turn, once his cards ask nothing of the job: `drive job resume` for a lane on a Sprite or this Mac whose job returned, its worker ended or not, which sends them to a worker that takes messages and hands them to the next worker of one that finished, and `drive job launch` for an agent on this Mac that returned and that no session reaches by state.agent_lane, whose job it hands to a lane on a Sprite, and for a job no agent or lane started once its waits end, which it briefs and launches with them under His answers since:, on this Mac when its brief declares a step only this Mac runs. Each runs by start_verb once busy lets it, for each new text and again every ten minutes while one stays held."""
    data = state.load(watcher.name)
    for job_id, job in data["jobs"].items():
        held = {kept["request"] for kept in job["undelivered"]}
        if not held or job["state"] in state.FINISHED_JOB_STATES or not heard_job(watcher, data, job) or view.open_cards(job) or launching(job, moment):
            continue
        if busy(watcher, job_id, moment) or (tried := watcher.carried.get(job_id)) and held <= tried[0] and moment - tried[1] < DUE_EVERY:
            continue
        if command := carrier(job_id, job, data, watcher.cards):
            start_verb(watcher, job_id, command, moment)
            watcher.carried[job_id] = (held, moment)


def carrier(job_id: str, job: dict, data: dict, cards: dict | None) -> list[str] | None:
    """The drive command that carries a job's held texts, by carry_held; None while its worker runs, which agent_lines sends them to, while the session that started its agent on this Mac reaches it, by state.agent_lane, whose SendMessage the ANSWER undelivered line names, by his pick "Keep with the starter: the agent finishes its job, your answers reach it at once", while its codex-ask run goes, whose next launch carries them in its brief, while a wait holds it, builder-slot included for a lane started, whose move here the watch's turn starts with them in its handoff, or for an adopted PR's job no builder started, which starts only on a PR event."""
    started = job.get("agent") or job.get("dispatch") or job.get("codex_run")
    if job["state"] == "running" and started or job["prs"] and not started or state.agent_lane(data, job):
        return None
    remote = job.get("where") == "remote" and bool(job.get("dispatch"))
    local = not remote and bool(job.get("brief")) and bool(place.mac_step(Path(job["brief"]).read_text()))
    if any(remote or local or wait != state.SLOT_WAIT for wait in view.holding(data, job["waits"], cards)):
        return None
    return ["job", "resume", job_id] if remote else ["job", "launch", job_id, *(["--local"] if local else [])]


def end_lane(name: str, job_id: str) -> int:
    """Run `drive job end` as its own process on a job that is not running whose worker's turn is over and whose lane on a Sprite no end ended, by unended, so that idle Sprite, its forward and its Orca environment go once its transcripts are kept and what it holds is saved, and a waiting job's next event starts its next lane on a fresh one; its output appended to the drive's relaunches log; its pid."""
    return state.detach(name, ["job", "end", job_id], RELAUNCHES_LOG)


def can_wake(watcher: Watcher, job_id: str, job: dict, data: dict, moment: datetime) -> bool:
    """Whether something can wake a waiting or built job: what state.wakes reads, the rule drive job report's refusal of a WAITING reads too, with GitHub's state of each PR the drive does not track as github_lines last read it, or what only the watch follows: its lane's question, a wait it stores that ended, which waits_lines frees by a FREE line, its proof to come, or an open PR of its or one folded into another open PR's branch, whose stage wakes its builder."""
    if job.get("ask") or job["waits"] or proving(data, job_id) or any(pr["job"] == job_id and pr["state"] in github.UNLANDED for pr in data["prs"].values()):
        return True
    return state.wakes(data, job_id, moment, read=watcher.untracked)


def unanswered(name: str, job_id: str, job: dict, data: dict, moment: datetime) -> str | None:
    """A lane's question no resume has answered for 30 minutes, whatever PR it has, that his cards do not carry; his answer since it came that no resume carried leads the line, and an `ASK` no `drive job ask` kept says that keeps it, as mail_line does, since no resume answers his question. A running lane asking no such question may wait on a read it queued, which unread ages; a waiting or built job's waits carry its part whatever read its lane left."""
    if not job.get("ask"):
        return unread(name, job_id, job, moment) if job["state"] == "running" else None
    asked = state.parse_time(job["ask_at"])
    if moment - asked < QUIET_FOR or carded(job, asked):
        return None
    question = f'"{cut(job["said"] or "", 80)}" {minutes(moment - asked)} minutes ago'
    if answers := [answer for answer in job["undelivered"] if state.parse_time(answer["at"]) >= asked]:
        return f"{unreached(job_id, job, data, answers[-1], orca.locked_out(name, job_id, job))}, answering its lane's question {question}"
    if reads.asks(job["report"] or "") and not state.asked_in(job, reads.opening(job["report"])):
        return f"{job_id}'s lane asked {question}, an ASK only his card answers: {UNKEPT_ASK.format(job=job_id)}"
    return f'{job_id}\'s lane asked {question} and nothing answered it: drive job resume {job_id} "<the answer>" answers it'


def unread(name: str, job_id: str, job: dict, moment: datetime) -> str | None:
    """The oldest `READ`, `LOG` or `DRIVE` question the job's live worker queued that no answer reached for 30 minutes, timed from when the lane asked it, as Orca stamped its message in the job's history, with what holds it: its reader still running, its answer kept for a reply Orca has not taken, or no reader on it, which `drive job read` runs and answers here. A read foreign_reads names waits on nobody, since read_lines drops it."""
    if not job.get("reads"):
        return None
    kept = state.replies(name, job_id)
    foreign = foreign_reads(job, kept)
    asked = {reply["id"]: state.parse_time(reply["created_at"]) for reply in kept if reply["id"] in job["reads"] and reply["id"] not in foreign}
    if not asked:
        return None
    message_id = min(asked, key=asked.__getitem__)
    if (waited := moment - asked[message_id]) < QUIET_FOR:
        return None
    if reading(job, message_id):
        held = "its reader still runs"
    elif message_id in job.get("read_answers", {}):
        held = "its answer is kept and Orca has not taken the reply"
    else:
        held = f"no reader runs on it, and drive job read {job_id} {message_id} runs and answers it here"
    return f'{job_id}\'s lane asked "{cut(first_line(job["reads"][message_id]), 80)}" {minutes(waited)} minutes ago and no answer reached it: {held}, each reader\'s output in the drive\'s {READS_LOG}'


def carded(job: dict, asked: datetime) -> bool:
    """Whether his cards carry a lane's question: each question its job kept that it names, by the files its `ASK <file> [<file>…]` report names or as its said line, open on a card of his whenever it was kept; a question naming none of them, a card drawn since it came, since an older card asks him something else."""
    drawn = [question for question in view.open_questions(job) if question["card"] or question["waits"]]
    said = [question for question in job["questions"] if state.same_question(question["title"], job["said"])][-1:]
    if named := state.asked_in(job, reads.opening(job["report"] or "")) or said:
        return all(question in drawn for question in named)
    return any(state.parse_time(question["asked"]) >= asked for question in drawn)


class NotStarted(str):
    """A part's gap naming a job drive job add recorded that no worker started, which the watch says before its reads on every pass after its first, so its ORPHAN comes on time whatever the length of a pass."""


def job_gap(watcher: Watcher, job_id: str, job: dict, data: dict, order_id: str, part: str, moment: datetime) -> str | None:
    """Why the job leaves the order's part with no one, None when it carries it or is a design its builder carries, or SETTLING while a change in the last two minutes is still being handed over; a builder that took DONE at its merge carries it only through the builder proving it live; a done tester leaves a staging test part whose PR it ran no full pass of waiting on a rerun; a DONE of a job's own carries work only while drive job prove's run of its proof file is to come; a job drive job add recorded not started that waits on nothing settles one minute, or while its launch takes, a drive job launch only while it holds the job's lock, read against state.now() since the pass may have started long before, then names the minutes since drive job add and the brief its agent reads first; a waiting job nothing can wake also names what its line names that ended or moved; a builder stopped before any agent took it carries the part by the PRs the drive does not track that it holds, by untracked_gap."""
    if state.unstarted(job) and not job["waits"]:
        if (now := state.now()) - state.parse_time(job["updated"]) < UNSTARTED_FOR or launching(job, now) and (job["where"] != "remote" or state.launch_in_flight(watcher.name, job_id, job)):
            return SETTLING
        return NotStarted(f"{job_id} not started {minutes(now - state.parse_time(job['created']))} minutes after drive job add{brief_first(job)}")
    if moment - state.parse_time(job["updated"]) < HANDOFF:
        return SETTLING
    if job["state"] == "running":
        return watcher.agent_gaps.get(job_id) or unanswered(watcher.name, job_id, job, data, moment)
    if job["state"] in ("waiting", "built"):
        if found := unposted_card(job_id, job, data):
            owner, unposted = found
            whose = "its" if owner == job_id else f"{owner}'s"
            if not unposted["refused"]:
                return f'{job_id} waits on a card never posted: {whose} question "{cut(unposted["title"], 80)}" kept in {unposted["file"]} did not draw, and the watch draws it again on its next read'
            return (
                f'{job_id} waits on a card never posted: {state.refused_by(unposted)} refused {whose} question "{cut(unposted["title"], 80)}" kept in {unposted["file"]}:'
                f" {cut(unposted['refused'].removeprefix('refused: '), 160)}"
            )
        if asked := unanswered(watcher.name, job_id, job, data, moment):
            return asked
        if can_wake(watcher, job_id, job, data, moment):
            return None
        if watch_proves(data, job_id):
            return merged_gap(job_id, data)
        waiting = f"is built on {job['branch']}" if job["state"] == "built" else f'waits on "{cut(job["said"] or "nothing named", 80)}"'
        return f"{job_id} {waiting} and no open PR, card or due time can wake it{ended_waits(data, job_id, job)}"
    if job["state"] == "done":
        if judging(data, job_id):
            return None
        if state.done_at_merge(job):
            return (
                f"{job_id} took DONE at its merge, which proves nothing live, and the watch adds the builder proving it live once its"
                f" `tools/ci release select --pr` read of {', '.join(reported_prs(data, job_id))} answers"
            )
        if job["kind"] in state.CHANGE_KINDS and (staging := state.STAGING_PART.fullmatch(part)):
            held, key, add = data["orders"][order_id]["parts"][part], f"{state.MONOREPO}#{staging['number']}", f"drive job add builder --order {order_id} --part {shlex.quote(part)}"
            if held["posted_by"] != job_id:
                return f"{job_id}'s DONE ran no case of {key}'s staging test, so the part waits on a run of it: {add} --goal '<run it>'"
            if held["posted"]:
                return f"{job_id}'s DONE passed {key}'s staging test, and a newer tester of it holds the pass"
            return f"{job_id}'s DONE is no pass of {key}'s staging test: {cut(state.unpassed(held), 240)}, so the part waits on a rerun: {add} --goal '<rerun it>'"
        if done_unproven(data, job_id):
            return None if proving(data, job_id) else merged_gap(job_id, data)
        if done_before_proofs(data, job_id):
            return None
        if state.unposted_answer(data, job_id):
            return f"{job_id} answered and the answer is not posted to him"
        if state.design_builder(data, job_id):
            return None
        drawn = [question for question in job["questions"] if question["card"]]
        answered = "card is" if len(drawn) == 1 else "cards are"
        return None if view.open_cards(job) else f"{job_id}'s {answered} answered and no job carries his answer"
    if keys := state.untracked_carriers(data, job):
        return untracked_gap(watcher, job_id, job, order_id, part, keys)
    return f"{job_id} {job['state']}"


def untracked_gap(watcher: Watcher, job_id: str, job: dict, order_id: str, part: str, keys: list[str]) -> str | None:
    """Why the PRs the drive does not track that a builder stopped before any agent took it holds leave its part with no one, by GitHub's state of each as github_lines last read it: None while one reads open or folded into another open PR's branch, by github.landing, SETTLING until each was read, then the merged one, naming the prover to start, since a merge delivers nothing, or else each closed unmerged, naming the builder to start on the stopped one's goal."""
    read = [watcher.untracked.get(key) for key in keys]
    if any(now in github.UNLANDED for now in read):
        return None
    if None in read:
        return SETTLING
    if merged := [key for key, now in zip(keys, read, strict=True) if now == "merged"]:
        return unproven_merge(order_id, part, merged[0])
    return f"{' and '.join(keys)} closed unmerged, carrying nothing of {job_id}'s work: start its builder with drive job add builder --order {order_id} --part {shlex.quote(part)} --goal {shlex.quote(job['goal'])}"


def unproven_merge(order_id: str, part: str, key: str) -> str:
    return (
        f"{key} merged, which delivers nothing until drive job prove runs a proof of it that exits 0: start its prover with drive job add builder --order {order_id}"
        f" --part {shlex.quote(part)} --builds-nothing --goal {shlex.quote(f'prove {key} live')}, briefed with drive job brief <its id> --handoff '<{key}, its merge commit, INSTALL: and PROVE:>', and it keeps that proof file"
    )


def unposted_card(job_id: str, job: dict, data: dict) -> tuple[str, dict] | None:
    """The open question whose card never drew for a waiting job, by the job keeping it: one of its own, or one asked on a card it waits on, such as its reader's, that `drive job ask` or `drive job card` refused, or whose draw failed, kept with no card and no refusal and folded into no other, which `draw_questions` draws again."""
    asked = [*((job_id, question) for question in view.open_questions(job)), *view.drawn_on(data, *job["waits"])]
    unposted = [(owner, question) for owner, question in asked if question["closed"] is None and (question["refused"] or question["file"] and not (question["card"] or question["replaced"]))]
    return next((found for found in unposted if found[1]["refused"]), None) or next(iter(unposted), None)


def merged_gap(job_id: str, data: dict) -> str:
    """Who proves live a builder's READY or PUSHED whose PRs all merged, a DONE of a job's own on work no proof delivered, or a repair's or prover's marked proof, that the watch neither took DONE on at its merge nor proves: who proof_gap names, else a run again of the proof that ended with no result, or of one that took a result on the line its job was put back on, which no install runs again."""
    if (why := proof_gap(data, job_id)) and done_unproven(data, job_id):
        return f"{job_id} returned DONE, which delivers its part only once drive job prove runs its proof file and sees it exit 0: {why}"
    if why:
        return f"{job_id}'s PRs all merged and nothing proves them live: {why}" if proved_prs(data, job_id) else f"nothing proves {job_id}'s repair: {why}"
    proof = data["jobs"][job_id]["proof"]
    if "result" in proof:
        return f"{job_id} was put back on the line its proof, drive job prove pid {proof['pid']}, took a result on, so no install runs it again: drive job prove {job_id} runs it again"
    return f"{job_id}'s proof, drive job prove pid {proof['pid']}, ended with no result, its output in the drive's {PROOFS_LOG}: drive job prove {job_id} runs it again"


def ended_waits(data: dict, job_id: str, job: dict) -> str:
    """What a stranded job's said line names that ended or moved, so its holder reads which of its waits went stale: each drive PR it names now another job's, merged or closed, and each other job it names that finished."""
    said = job["said"] or ""
    prs = [(key, data["prs"][key]) for key in state.named_prs(data, job["order"], said)]
    ended = [f"{key} moved to {pr['job']}" if pr["job"] != job_id else f"{key} {pr['state']}" for key, pr in prs if pr["job"] != job_id or pr["state"] != "open"]
    ended += [f"{other} {data['jobs'][other]['state']}" for other in dict.fromkeys(re.findall(r"\bj\d+\b", said)) if other != job_id and data["jobs"].get(other, {}).get("state") in state.FINISHED_JOB_STATES]
    return f"; of what its line names, {', '.join(ended)}" if ended else ""


def unproven(data: dict, job_id: str) -> bool:
    """Whether a builder's READY or PUSHED whose PRs all merged names a yasyf/* PR and keeps no executable proof file, so nothing proves it live and the watch takes BLOCKED on it."""
    return merged_ready(data, job_id) and not product(data, job_id) and bool(proof_gap(data, job_id))


def judging(data: dict, job_id: str) -> bool:
    """Whether a job whose PRs, by judged_prs, all merged waits on the check of a merged diff, the one that names the parts of his orders it carries, while that check is still to start or runs at a merged head, by served.merge_judging, one that failed or ended there with no outcome ending the wait as one naming none of them does: a builder waiting on a READY, PUSHED or WAITING line, before the watch takes its DONE or proves it; and, while a part it holds is undelivered, a product builder that took DONE at its merge before checks named parts, before unproved hands that part to its prover, and a builder or codex job holding a DONE of its own, whatever its line says, such as one it returned after its merge or one drive job prove took, whose passing proof delivers the parts that check names, by state.deliver_proven, at the proof or by served.kept once the check lands after it."""
    job = data["jobs"][job_id]
    waiting = job["state"] == "waiting" and MERGE_ENDS.match(job["said"] or "")
    own = job["state"] == "done" and job["kind"] in state.CHANGE_KINDS and not state.done_at_merge(job)
    if not (own or job["kind"] == "builder" and (waiting or state.done_at_merge(job) and product(data, job_id))):
        return False
    keys = judged_prs(data, job_id)
    if not (keys and all(data["prs"][key]["merged_sha"] for key in keys) and any(served.merge_judging(data["prs"][key]) for key in keys)):
        return False
    return bool(waiting) or not all(state.delivered(data, order_id, part) for order_id, parts in open_served(data, job).items() for part in parts)


def judged_prs(data: dict, job_id: str) -> list[str]:
    """The PRs whose merged diffs' check names the parts a job delivers: those its said line names, by reported_prs, but for a DONE of a job's own each merged one of those its proof proves, by proved_prs."""
    job = data["jobs"][job_id]
    if job["state"] != "done" or state.done_at_merge(job):
        return reported_prs(data, job_id)
    return [key for key in proved_prs(data, job_id) if data["prs"][key]["merged_sha"]]


def at_merge(data: dict, job_id: str) -> bool:
    """Whether the watch takes DONE at its merge on a builder waiting on a READY, PUSHED or WAITING line whose PRs, by reported_prs, all merged outside yasyf/*, a product change, each merged diff checked for the parts it carries, held by no open card of his and no wait but on a release, which ends with it, on whatever order it serves, an open incident's fix like any other, since the builder proving it live starts at the merge and sets its own release wait."""
    job = data["jobs"][job_id]
    if job["kind"] != "builder" or job["state"] != "waiting" or not MERGE_ENDS.match(job["said"] or "") or not product(data, job_id) or judging(data, job_id):
        return False
    return all(data["prs"][key]["merged_sha"] for key in reported_prs(data, job_id)) and not view.open_cards(job) and all(map(state.RELEASE_WAIT.fullmatch, job["waits"]))


def open_served(data: dict, job: dict) -> dict[str, list[str]]:
    """The parts a job holds of each open order it serves, its own first, then each it carries, but a staging test part, which only its tester's full pass before the merge delivers, so no builder proving the merge live holds one."""
    return {
        order_id: [part for part in state.job_parts(job, order_id) if part in data["orders"][order_id]["parts"] and not state.STAGING_PART.fullmatch(part)]
        for order_id in [job["order"], *job["carries"]]
        if data["orders"][order_id]["state"] not in state.CLOSED_ORDER_STATES
    }


def unproved(data: dict, job_id: str) -> dict[str, list[str]]:
    """The parts the builder proving a merge outside yasyf/* live takes, each list under the open order holding it, its own first, as open_served reads them, of those only the parts the check of its PRs' merged diffs found they carry, by state.carried_by, so a part they leave out has no one and its ORPHAN line asks for its builder at once: owed by a builder that at_merge takes DONE on now and by one that took that DONE before, alike, while one of those parts is undelivered, each of them while one of its PRs has no prover, by prover_of, else those the prover of one of its PRs does not hold yet; none otherwise, so a prover once added or found holds every part, and one that failed or stopped is said by its part's ORPHAN line, never given a second."""
    job = data["jobs"][job_id]
    if not (at_merge(data, job_id) or job["kind"] == "builder" and state.done_at_merge(job) and product(data, job_id)):
        return {}
    carried = state.carried_by(data, reported_prs(data, job_id))
    served = {order_id: kept for order_id, parts in open_served(data, job).items() if (kept := [part for part in parts if part in carried.get(order_id, [])])}
    if all(state.delivered(data, order_id, part) for order_id, parts in served.items() for part in parts):
        return {}
    found = [prover_of(data, key) for key in reported_prs(data, job_id)]
    if None in found:
        return served
    return {order_id: unheld for order_id, parts in served.items() if (unheld := [part for part in parts if any(part not in state.job_parts(data["jobs"][prover], order_id) for prover in found)])}


def proof_due(data: dict, job_id: str) -> bool:
    """Whether the watch starts the job's proof: a builder still waiting on a READY or PUSHED whose PRs all merged, a yasyf/* one among them, each merged diff checked for the parts it carries, none a PR a lander still merges or installs, a DONE of a job's own on work no proof delivered, or a repair waiting on the proof its drive job prove marked, keeping an executable proof file, and no proof started on that line, one that waits on his next word once he is heard after it started, or one that ended with no result before a newer long-running's watch read it, never one that took a pass or a fail, which proof_kept marks on it as its result, so an install reruns no proof that ran to its end, one whose job was put back on the line it ran on included."""
    job = data["jobs"][job_id]
    if not watch_proves(data, job_id) or judging(data, job_id) or proof_gap(data, job_id) or any(land.running(data["prs"][key]) for key in reported_prs(data, job_id)):
        return False
    proof = job.get("proof")
    if not proof or proof["said"] != job["said"]:
        return True
    if proof.get("waits"):
        heard = data["owner"]["heard"]
        return not state.alive(proof["pid"]) and bool(heard) and state.parse_time(heard) > state.parse_time(proof["at"])
    return "result" not in proof and proof["version"] != state.VERSION and not state.alive(proof["pid"])


def proving(data: dict, job_id: str) -> bool:
    """Whether a merged job's, a DONE of a job's own's or a repair's proof is to come: the check of a merged diff of it still to name the parts it carries, a lander still merging or installing one of its PRs, a proof running on its line or waiting on his next word, or one the watch starts on its next read."""
    job = data["jobs"][job_id]
    if judging(data, job_id):
        return True
    if not watch_proves(data, job_id) or proof_gap(data, job_id):
        return False
    proof = job.get("proof")
    return (
        proof_due(data, job_id)
        or any(land.running(data["prs"][key]) for key in reported_prs(data, job_id))
        or proof["said"] == job["said"]
        and bool(proof.get("waits") or state.alive(proof["pid"]))
    )


def finished(watcher: Watcher, moment: datetime) -> list[str]:
    """Finish each builder the watch hears whose PRs all merged, once the check of each merged diff named the parts of his orders it carries, which the watch starts on each merged head no check named them at, by judging, the merged PRs a DONE of a builder's or codex job's own proves among them, whose parts its passing proof delivers once that check lands: one waiting on a READY, PUSHED or WAITING line whose PRs all merged outside yasyf/* takes DONE at its merge, its order keeping no proof until a builder proves it live on the release holding its merge, its release waits ending with it; one whose READY or PUSHED names a yasyf/* PR and keeps an executable proof file, a DONE of a job's own on work no proof delivered, or a repair waiting on the proof it marked, gets drive job prove, which takes its DONE or BLOCKED once the installed plugins hold its yasyf/* merges, marked under the drive's lock so no second proof starts on its line, said on its PRs' lines; and one naming a yasyf/* PR and keeping no executable proof file takes BLOCKED, said by its part's ORPHAN line when no other job carries the part, so it leaves the jobs whose lanes each pass reads. Each builder unproved owes its parts, at that DONE or one it took before, goes to its PRs' prover by add_prover, which adds one only for PRs prover_of finds none for, waiting on no release, its handoff naming whether their paths reach only tests or CI config, read by cli.release_reach before the drive's lock, a read that fails leaving both the DONE and the prover to the next pass; one line says each prover."""
    from drive import cli

    data = state.load(watcher.name)
    heard = [job_id for job_id, job in data["jobs"].items() if heard_job(watcher, data, job)]
    owed = [job_id for job_id in heard if unproved(data, job_id)]
    if not (owed or any(finishes(data, job_id) for job_id in heard)):
        return []
    if due := [key for job_id in heard if judging(data, job_id) for key in judged_prs(data, job_id) if served.merge_due(data["prs"][key])]:
        served.start(watcher.name, list(dict.fromkeys(due)))
    merging = [job_id for job_id in heard if at_merge(data, job_id)]
    lines: list[str] = []
    try:
        reach = cli.release_reach(watcher.name, data, [key for job_id in owed for key in reported_prs(data, job_id) if not prover_of(data, key)])
        if watcher.staged is None and any(answer["releases"] for answer in reach.values()):
            live_rows(watcher, data)
    except (github.GithubError, state.DriveError, reads.ReadError) as error:
        merging, owed, reach, lines = [], [], {}, failed(watcher, "release select", "`tools/ci release select --pr` and `tools/ci release live` reads of the releases a merged PR reaches and the targets staging runs", error, moment)
    else:
        watcher.failures.pop("release select", None)
    with state.edit(watcher.name) as data:
        for job_id, job in list(data["jobs"].items()):
            if not heard_job(watcher, data, job):
                continue
            if job_id in merging and at_merge(data, job_id):
                merged_done(watcher.name, data, job_id)
            elif proof_due(data, job_id):
                job["proof"] = {"pid": prove(watcher.name, job_id), "at": state.stamp(moment), "said": job["said"], "version": state.VERSION}
            elif unproven(data, job_id):
                proofless(watcher.name, data, job_id)
        proving = {job_id: parts for job_id in owed if (parts := unproved(data, job_id))}
        known = set(data["jobs"])
        provers: dict[str, list[str]] = {}
        for job_id, parts in proving.items():
            provers.setdefault(add_prover(watcher.name, data, job_id, reach, parts, watcher.staged or set()), []).append(job_id)
    return lines + [merged_said(watcher.name, builder_id, f"widened to the parts of {', '.join(jobs)}" if builder_id in known else "added") for builder_id, jobs in provers.items()]


def folded_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Fold each prover no agent or lane took whose PRs another prover proves, by folded_into, into that prover, which widen gives its open parts and, while no agent or lane took it either, the PRs it lacks, its handoff and its Mac: step, and hand_held the texts it holds, and stop it, one line saying each fold, so the provers two passes, two orders or a coordinator added for one PR become one."""
    if not doubled(state.load(watcher.name)):
        return []
    folds = []
    with state.edit(watcher.name) as data:
        for job_id in doubled(data):
            if not (first := folded_into(data, job_id)):
                continue
            job = data["jobs"][job_id]
            widen(data, first, open_served(data, job), [key for key in proves(data, job_id) if key not in proves(data, first)], {flag: (job.get("brief_flags") or {}).get(flag) for flag in ("handoff", "mac")})
            state.hand_held(data, job_id, first)
            job.update(state="stopped", said=f"folded into {first}", updated=state.stamp(moment))
            folds.append((first, job_id))
    return [merged_said(watcher.name, first, f"took the parts of {job_id}, a duplicate now stopped") for first, job_id in folds]


def doubled(data: dict) -> list[str]:
    return [job_id for job_id, job in data["jobs"].items() if state.untaken(job) and folded_into(data, job_id)]


def folded_into(data: dict, job_id: str) -> str | None:
    """The prover a prover no agent or lane took folds into: the one prover_of names for one of its PRs, when that one can take its other PRs too, no agent or lane having taken it either, or another prover proves each of them."""
    keys = proves(data, job_id)
    found = [prover for key in keys if (prover := prover_of(data, key)) != job_id]
    return found[0] if found and (state.untaken(data["jobs"][found[0]]) or len(found) == len(keys)) else None


def finishes(data: dict, job_id: str) -> bool:
    return judging(data, job_id) or at_merge(data, job_id) or proof_due(data, job_id) or unproven(data, job_id)


def prove(name: str, job_id: str) -> int:
    """Run `drive job prove` on the job as its own process, outliving the pass that started it, its output appended to the drive's proofs log."""
    return state.detach(name, ["job", "prove", job_id], PROOFS_LOG)


def part_gap(watcher: Watcher, data: dict, order_id: str, part: str, moment: datetime) -> str | None:
    """Why a part of the order has no one carrying it, None when it is carried, delivered or waits on something named, such as his word on a job a model's safeguards stopped: work he ordered, every part of a build order or an incident, but an incident's first check and cause and a part drive order set --kind answer --part sorted as an answer that no builder, codex or design job holds, is carried only by a builder, codex or design job, never by research, his card, a post or a stopped job, by state.carriers, nor by a builder or codex job done with no merged PR whose checked diff carries it, but while the check of its merged diffs runs, while drive job prove's run of the proof file of its own DONE is to come, or on a staging test part, which its tester's full pass delivers by state.staged and only its testers carry, by state.part_testers, never a job following a release it started, and of its testers done or holding their DONE for their own PRs' merge only the one whose DONE last ran its cases, by state.keep_pass, while one did, so it has no one the moment none is on it past its handover, and the gap names the builder to start; a change a design lists has no job only while the order asks for options only, waiting on his pick, which adds its builder, since drive job report adds one for each change as the design returns; a builder, codex or design job on the order's immediate fix part carries the order's other parts too until it is done; an open incident's fix is carried only by a live job, or an open or merged PR of a job on it, which the release train ships, never by a post, so once its jobs end otherwise the gap says it has no fix job and names the second one to start at once, by no_fix_job; a standing part, never delivered, is held only by an unfinished job on it, and once none is its gap is the Restart standing_lines starts its next pass on, by standing_gap; a part whose followed build still runs is carried by the watch's follow."""
    order, staging = data["orders"][order_id], state.STAGING_PART.fullmatch(part)
    jobs = state.part_testers(data, order_id, part) if staging else state.part_jobs(data, order_id, part)
    gaps = {job_id: job_gap(watcher, job_id, job, data, order_id, part, moment) for job_id, job in jobs}
    if stuck := [gap for job_id, job in jobs if (gap := gaps[job_id]) and (job["state"] == "running" or job["state"] == "waiting" and job.get("ask"))]:
        return stuck[-1]
    if order["parts"][part]["standing"]:
        return standing_gap(jobs, gaps, moment)
    if state.following(order["parts"][part]):
        return None
    if fixer := no_fix_job(data, order_id, part, jobs):
        last, job = fixer
        idle = "" if part == state.INCIDENT_FIX else f", no job of {order_id} is at work or has his card open, and it keeps no proof users are unblocked"
        return (
            f"no fix job: {last} ended {job['state']} with no open or merged PR{idle}; start the second fix job now, an Opus 5.5 long-running:builder on {last}'s brief:"
            f" drive job add builder --order {order_id} --part {shlex.quote(part)} --goal {shlex.quote(job['goal'])} (incidents: Clocks)"
        )
    if state.delivered(data, order_id, part):
        return None
    if part != state.IMMEDIATE_FIX and any(job["kind"] in state.WORK_KINDS and job["state"] in ("running", "waiting", "built") for _, job in state.part_jobs(data, order_id, state.IMMEDIATE_FIX)):
        return None
    if any(job["state"] == "stopped" and (job["said"] or "").startswith(SAFEGUARDED) for _, job in jobs):
        return None
    ran = order["parts"][part]["posted_by"] if staging else None
    if carried := [
        gaps[job_id]
        for job_id, job in state.carriers(data, order_id, part)
        if job_id in gaps
        and not (ran and job_id != ran and state.done_or_held(job))
        and (not (job["kind"] in state.CHANGE_KINDS and job["state"] == "done" and state.work(data, order_id, part) and not staging) or judging(data, job_id) or done_unproven(data, job_id) or done_before_proofs(data, job_id))
    ]:
        return None if None in carried else SETTLING if SETTLING in carried else carried[-1]
    if jobs:
        return SETTLING if SETTLING in gaps.values() else unbuilt(data, order_id, part, jobs)
    if moment - state.parse_time(order["updated"]) < UNSTAFFED_FOR or order["options_only"] and order["parts"][part]["design"]:
        return None
    return "no job"


class Restart(str):
    """A standing part's gap once no unfinished job holds it and STANDING_EVERY passed since its last pass was added, carrying that pass, none before its first, which standing_lines starts the next pass after."""

    last: str | None

    def __new__(cls, last: str | None) -> Self:
        made = super().__new__(cls, f"no pass of it is at work since {last} ended" if last else "no pass of it ran yet")
        made.last = last
        return made


def standing_gap(jobs: list[tuple[str, dict]], gaps: dict[str, str | None], moment: datetime) -> str | None:
    """Who holds a standing part: each unfinished job on it, read as any part's carriers are, else no one, its Restart after its last pass once state.pass_due lets its next pass start, and SETTLING until then."""
    if held := [gaps[job_id] for job_id, job in jobs if job["state"] not in state.FINISHED_JOB_STATES]:
        return None if None in held else SETTLING if SETTLING in held else held[-1]
    passes = state.passes(jobs)
    if (due := state.pass_due(passes)) and moment < due:
        return SETTLING
    return Restart(passes[-1][0] if passes else None)


def standing_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Start the next pass of each standing part its Restart gap names, as finished adds provers: a codex job on this Mac on the part's standing goal, its brief the last pass's flags with a handoff quoting that pass's return and PASS_DONE as its Done when, holding the texts and owing the answers that pass held, by added_pass, launched by drive job launch as its own process, the one start of a pass, since launch and resume refuse a pass that ended, its output in the drive's relaunches log; one STANDING line each, naming why the last pass ended. A launch that refuses leaves the job not started, which its part's ORPHAN line says."""
    data = state.load(watcher.name)
    if not any(isinstance(gap, Restart) for gap in part_gaps(watcher, data, moment).values()):
        return []
    with state.edit(watcher.name) as data:
        restarts = [(key, gap.last, added_pass(data, key, gap.last, moment)) for key, gap in part_gaps(watcher, data, moment).items() if isinstance(gap, Restart)]
    return [launched_pass(watcher.name, *restart) for restart in restarts]


def added_pass(data: dict, key: tuple[str, str], last: str | None, moment: datetime) -> str:
    """Add a standing part's next pass, on the last pass's brief flags but its handoff, holding each text the last pass held and owing each answer of his it was owed, which move off it, so its launch carries them and nothing waits on a pass that ended."""
    order_id, part = key
    flags = (data["jobs"][last].get("brief_flags") or {}) if last else {}
    job_id = state.next_id(data["jobs"], "j")
    data["jobs"][job_id] = state.new_job(order_id, "codex", data["orders"][order_id]["parts"][part]["standing"], [part], state.stamp(moment)) | {
        "brief_flags": {flag: value for flag, value in flags.items() if flag != "handoff"}
    }
    data["orders"][order_id]["jobs"].append(job_id)
    if last:
        state.hand_held(data, last, job_id)
        for _, kept in superseded.owed(data, last):
            kept["jobs"][job_id] = kept["jobs"].pop(last)
    return job_id


def launched_pass(name: str, key: tuple[str, str], last: str | None, job_id: str) -> str:
    """Brief and launch the pass standing_lines added, the line saying it, or why its brief was not written: its Done when is PASS_DONE, whatever its goal says, so a pass that did its round reads done."""
    from drive import cli

    order_id, part = key
    data = state.load(name)
    ended = f'{last} ended {data["jobs"][last]["state"]}: "{cut(data["jobs"][last]["said"] or "no line", 160)}"' if last else "no pass of it ran before"
    head = f'STANDING {job_id} {order_id} "{cut(part, 80)}": {ended}; the watch added {job_id}, a codex job on this Mac on its standing goal'
    try:
        cli.write_brief(name, job_id, {"handoff": pass_handoff(name, data, key, last), "done_when": PASS_DONE})
    except cli.VERB_ERRORS as error:
        return f"{head}, and wrote it no brief: {error}; brief it with drive job brief {job_id}, then drive job launch {job_id}"
    state.detach(name, ["job", "launch", job_id], RELAUNCHES_LOG)
    return f"{head}, and ran drive job launch {job_id}, its output in the drive's {RELAUNCHES_LOG}"


def pass_handoff(name: str, data: dict, key: tuple[str, str], last: str | None) -> str:
    """The handoff a standing part's next pass starts on: its last pass's newest return whole, else that pass's line, else that it is the first pass, each line of the return quoted, so none reads as a line of the brief."""
    standing = f'{key[0]}\'s standing part "{key[1]}"'
    if not last:
        return f"The watch started the first pass of {standing}: no pass of it ran before."
    returned = next(iter(state.returns(name, last)), None)
    quoted = returned["body"].strip() if returned else data["jobs"][last]["said"] or "nothing"
    lines = "\n".join(f"> {line}".rstrip() for line in quoted.splitlines())
    return f"The watch started this pass of {standing} once its last pass, {last}, ended {data['jobs'][last]['state']}. Its return, verbatim:\n{lines}"


def no_fix_job(data: dict, order_id: str, part: str, jobs: list[tuple[str, dict]]) -> tuple[str, dict] | None:
    """The newest builder or codex job on a part of an open incident that holds its fix with no one at work on it, whose brief the second fix job starts on, never a reader's or design's, nor a diagnosis job's that a later fix job followed: its fix part once no job on it is live or has an open or merged PR, or, on an incident with no fix part, such as one joined to another, each of its work parts once none of the incident's jobs is live, has an open or merged PR or his card open, and it keeps no proof users are unblocked; None while one is at work or the part never had a builder or codex job."""
    order = data["orders"][order_id]
    if not state.open_incidents(data, [order_id]):
        return None
    if part == state.INCIDENT_FIX:
        held = jobs
    elif state.INCIDENT_FIX not in order["parts"] and state.work(data, order_id, part) and not order["proof"]:
        held = [(job_id, data["jobs"][job_id]) for job_id in order["jobs"]]
    else:
        return None
    opened = {pr["job"] for pr in data["prs"].values() if pr["state"] != "closed"}
    if any(job["state"] not in state.FINISHED_JOB_STATES or job_id in opened or part != state.INCIDENT_FIX and view.open_cards(job) for job_id, job in held):
        return None
    return max(((job_id, job) for job_id, job in jobs if job["kind"] in state.CHANGE_KINDS), key=lambda found: state.parse_time(found[1]["created"]), default=None)


def unbuilt(data: dict, order_id: str, part: str, jobs: list[tuple[str, dict]]) -> str:
    """Why work he ordered that only research, a design done with no builder, a builder done that merged no PR whose checked diff carries it, his card or a post holds has no one carrying it, naming the drive pr check that judges again each of its merged PRs whose one check at its merged head, by served.merge_due, failed, ended with no outcome or came before checks named parts: the builder to start, waiting on the reader still at work alone, since a wait says only when a job starts, which builds the recommended option of each open card of his whose `Holds:` line names the part or that was kept with none and opens no PR until he answers, each card holding its PR by state.part_cards; then the prover to start on the merged PR the part's words name, since a merge delivers nothing, or else the sort of the part as an answer."""
    held = ", ".join(f"{job['kind']} {job_id} {job['state']}" for job_id, job in jobs)
    finished = [job_id for job_id, job in jobs if job["kind"] in state.CHANGE_KINDS and job["state"] == "done"]
    unjudged = dict.fromkeys(key for job_id in finished for key in judged_prs(data, job_id) if data["prs"][key]["merged_sha"] and state.carried(data["prs"][key]) is None and not served.merge_judging(data["prs"][key]))
    rerun = f", the check at its merged head naming no parts, so {' and '.join(f'drive pr check {state.pr_url(key)}' for key in unjudged)} judges it again" if unjudged else ""
    left = f"; {' and '.join(finished)} merged no PR whose checked diff carries it{rerun}" if finished else ""
    reading = [job_id for job_id, job in jobs if job["kind"] == "reader" and job["state"] not in state.FINISHED_JOB_STATES]
    cards = list(
        dict.fromkeys(
            drawn
            for _, job in jobs
            for question in view.open_questions(job)
            if (drawn := question["card"] or question["waits"]) and (question["holds"] is None or state.names_part(question["holds"], [part]))
        )
    )
    waits = "".join(f" --waits {wait}" for wait in reading[-1:])
    open_cards = f", his card {' and '.join(cards)} open" if cards else ""
    named = shlex.quote(part)
    start = f"drive job add builder --order {order_id} --part {named} --goal '<what it builds>'{waits}"
    prs = state.named_prs(data, order_id, part)
    merged = prs and all(data["prs"][key]["state"] == "merged" for key in prs)
    done = unproven_merge(order_id, part, prs[0]) if merged else f"when it asks only for an answer, drive order set {order_id} --kind answer --part {named}"
    return f"no builder carries this work, only {held}{left}{open_cards}: start one with {start}, or add it to a builder with drive job set <builder> --part {named}, adding --carries {order_id} for another order's builder; {done}"


def part_gaps(watcher: Watcher, data: dict, moment: datetime) -> dict[tuple[str, str], str | None]:
    return {
        (order_id, part): part_gap(watcher, data, order_id, part, moment)
        for order_id, order in heard_orders(watcher, data).items()
        if not order["watch"]
        for part in order["parts"]
    }


def orphan_change(data: dict, key: tuple[str, str], gap: str | None, moment: datetime) -> str | None:
    """Whether the part's gap is said or healed, never a standing part's Restart, which standing_lines answers with its next pass: said once as it appears, by his pick "Said once, then listed in drive status: no repeats and a standing gap stays readable", and again only once a job on the part changes, by state.orphan_stands, or 30 minutes after a FREE line said its job's last wait ended, since drive status lists the gap it said until it heals or a job on the part moves."""
    part = data["orders"][key[0]]["parts"][key[1]]
    if gap is None:
        return "heal" if part["orphan_said"] else None
    if gap == SETTLING or isinstance(gap, Restart):
        return None
    if not part["orphan_said"]:
        return "say"
    if part["orphan_gap"] == state.FREED and moment - state.parse_time(part["orphan_said"]) >= FREED_AGAIN:
        return "say"
    return None if state.orphan_stands(data, *key) else "say"


def orphan_lines(watcher: Watcher, moment: datetime, said: type = str) -> list[str]:
    data = state.load(watcher.name)
    if not any(orphan_change(data, key, gap, moment) for key, gap in part_gaps(watcher, data, moment).items() if gap is None or isinstance(gap, said)):
        return []
    lines = []
    with state.edit(watcher.name) as data:
        for key, gap in part_gaps(watcher, data, moment).items():
            change = orphan_change(data, key, gap, moment) if gap is None or isinstance(gap, said) else None
            part = data["orders"][key[0]]["parts"][key[1]]
            if change == "heal":
                part.update(orphan_said=None, orphan_gap=None)
            if change == "say":
                part.update(orphan_said=state.stamp(moment), orphan_gap=gap)
                lines.append(f'ORPHAN {key[0]} "{cut(key[1], 80)}": {gap}')
    return lines


def hear(name: str, moment: datetime, away: bool = False) -> dict:
    """Record him heard at moment, by his words or his tap on a card, the owner row keeping the moment he reads away from, moment itself with away, else an hour later; when he was away, keep that absence on the owner row, from when he went away, the moment he read away from and moment, which state.present reads, and add to the catch-up the drive owes him, which keeps the time of the first such absence it covers and which the watch's next pass posts, each open order and incident whose line facts moved since its thread's last line or the watch's last report on it, by the one test quiet_lines owes a line on, with the orders joined to it, keeping those facts as told so no thread gets a line on news his catch-up carries, and each order a report held for his catch-up is on."""
    with state.edit(name) as data:
        back = state.owner_away(data, moment)
        owner = data["owner"]
        since = owner["heard"]
        absence = {"since": since, "from": owner["away"], "until": state.stamp(moment)} if back and since else owner["absence"]
        data["owner"] = {"heard": state.stamp(moment), "away": state.stamp(moment if away else moment + state.AWAY_AFTER), "absence": absence}
        news = []
        held = list(data["held"]) if back and since else []
        if back and since:
            registered = render.prs_by_job(data)
            for head, group in news_groups(data, state.open_orders(data)).items():
                order, facts = data["orders"][head], line_facts(data, group, registered, moment)
                if order["quiet"] and untold(order, facts):
                    order["quiet"]["news"] = facts
                    news += group
            news += [mark["order"] for mark in data["held"].values() if mark["order"]]
        if news or held:
            owed = data["catch_up"] or state.new_catch_up(since, [])
            data["catch_up"] = owed | {"orders": list(dict.fromkeys([*owed["orders"], *news])), "record": None}
        owed = data["catch_up"]
    return {"away": away, "back": back, "since": since, "catch_up": owed and owed["orders"], "held": held}


def unhear(name: str, last: datetime, why: str) -> dict:
    """Take back what the drive heard of him after last, his last word or tap, such as a job's tap kept as his: the owner row reads him heard at last and away an hour after it, its absence kept, and the moment taken back is kept with why in the drive's events; refused when the drive heard nothing of him after last."""
    with state.edit(name) as data:
        taken = data["owner"]["heard"]
        if not taken or last >= state.parse_time(taken):
            raise state.DriveError(f"refused, nothing changed: the drive last heard him at {taken}, so it heard nothing after {state.stamp(last)} to take back")
        data["owner"] |= {"heard": state.stamp(last), "away": state.stamp(last + state.AWAY_AFTER)}
    state.append_event(name, "unheard", heard=state.stamp(last), taken=taken, why=why)
    return {"heard": state.stamp(last), "taken": taken}


def catch_up_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Post the catch-up the drive owes him while he is here, once for each time he is heard: a report of it the watch writes once, its body naming each report held for it that its writer did not withdraw, with each correction cci holds re one, and each order with news, for a handler the watch starts to post at his DM root, unless the handler started last is still at work on it; a failed write or start is said as a failed read is and tried again next pass."""
    data = state.load(watcher.name)
    owed, heard = data["catch_up"], data["owner"]["heard"]
    if watcher.project or owed is None or owed["tried"] == heard or state.owner_away(data, moment):
        return []
    if (entry := data["handlers"].get(owed["handler"] or "")) and not agents.gone(entry):
        with state.edit(watcher.name) as data:
            data["catch_up"]["tried"] = heard
        return []
    try:
        seq = owed["record"] or catch_up_report(watcher, data)
        with state.edit(watcher.name) as data:
            data["catch_up"]["record"] = seq
        handler, _ = agents.handle_report(watcher.name, seq, f"Record: {seq} Drive: {watcher.name} Scratch: {state.drive_dir(watcher.name) / 'scratch' / f'catch-up-{seq}'}")
    except (reads.ReadError, state.DriveError) as error:
        return failed(watcher, "catch-up", "post of his catch-up", error, moment)
    watcher.failures.pop("catch-up", None)
    with state.edit(watcher.name) as data:
        data["catch_up"] |= {"handler": handler, "tried": heard}
    return []


def catch_up_report(watcher: Watcher, data: dict) -> str:
    """Write the report of his catch-up for the handler that posts it; its seq."""
    owed = data["catch_up"]
    amended = agents.amendments(watcher.name, data["held"])
    held = [f"- report {seq} on {mark['order'] or 'no order'}{render.corrected_by(amended['correction'].get(seq, []))}" for seq, mark in data["held"].items() if seq not in amended["withdraw"]]
    orders = [f"- {render.order_line(data, order_id, data['orders'][order_id])}" for order_id in owed["orders"]]
    facts = [
        f"His catch-up, owed since he was last heard at {when(owed['since'])}. Read each held report and each correction with cci read: no held report reached him, and a correction's facts replace those it corrects.",
        f"Held for it: {len(held)}",
        *held,
        f"Orders with news since then: {len(orders)}",
        *orders,
    ]
    text = f"POST his catch-up since {when(owed['since'])}: {len(orders)} orders with news, {len(held)} reports held for it"
    [seq] = reported(watcher, [{"kind": "report", "attr": {"catch_up": "true"}, "text": text, "body": "\n".join(facts) + "\n"}])
    return seq


def run_pass(watcher: Watcher, moment: datetime) -> list[str]:
    """One pass's lines for the coordinator, every stage's together, as a proof runs its passes in-process."""
    return [line for lines in passed(watcher, moment) for line in lines]


def passed(watcher: Watcher, moment: datetime) -> Iterator[list[str]]:
    """The coordinator's lines of one pass as each stage returns, first none, as the communicator, the meta session and the coordinator are each sent again what failed to post to them before, then each stage's, its lines for the communicator or the meta session posted to them as it returns, so a pass that reads each lane from Orca for minutes says what GitHub and the lanes said in its first minute then, not at its end. A stage's lines join the watcher's made lines first, so a pass that ends early, in a crash or on a drive a newer install saved, leaves those not yet handed to the watch process after it."""
    yield handed(watcher, [], moment)
    for lines in stages(watcher, moment):
        watcher.made += lines
        if watcher.made and (said := handed(watcher, watcher.made, moment)):
            yield said


def stages(watcher: Watcher, moment: datetime) -> Iterator[list[str]]:
    """The lines of each stage of one loop of the watch, as it returns: first the lines its relay made since the last pass, then the answer kept for each lane's read, so a pass a newer install ends while it reads every live lane's mail holds back none it already had, and new events, so a return `drive job report` took in another process is said in the pass its event wakes, then each part a job drive job add recorded not started leaves with no one, on every pass after the watch's first, so its ORPHAN comes on time whatever the length of a pass, then the drive's own Orca terminal every Orca call speaks as, his cards, the GitHub reads it owes, the staging test of each PR whose new head changes a dangerous class of file, his card on each kept question none drew yet, a lander on each PR his Approve covers that qualifies, the finish of each builder whose READY's PRs all merged, DONE at the merge of a product change or the proof of a yasyf/* one, the check against his words of each PR whose builder's READY owes one, his PR cards whose PRs all ended, where each merged PR a release wait or an incident fix names runs, each Buildkite build a wait names or a part follows that is newly held or a delivery of Buildkite's named, the status message of each build a part follows, the waits his answers, a job's end, that read or the Buildkite read release or hold again, a port's READY the coordinator has not checked in five minutes, his answers that leave cards unmarked, lane mail, each builder's PUSHED GitHub's read settles, agents, the chains of waits that stood unmoved, the texts jobs hold that no worker taking messages gets, the communicator's compaction, the status page, a coordinator whose process exited, a dashboard whose server exited, the receiver and each teammate thread an order watches every 60 s, his PR cards whose rows went stale, and GitHub again next poll after a failure, events written since, its own stages' among them, then the GitHub reads they and the rest owe while its last read answered, so a delivery is read in the pass it wakes, the catch-up owed him, claims whose end passed, the line owed in each thread whose order or incident has news, job clocks run out, his answers no resume has carried to their jobs, fixes proven live whose bug report has not heard, the next pass of each standing part no job holds, parts nothing carries, and a handler on each POST report to the communicator. A stage that keeps working after its save joins the lines it made by then to the watcher's made lines itself."""
    yield drained(watcher.relayed)
    yield read_lines(watcher, moment)
    yield event_lines(watcher)
    if watcher.read_at is not None:
        yield orphan_lines(watcher, moment, NotStarted)
    if watcher.read_at is None or moment - watcher.read_at >= READ_EVERY:
        watcher.read_at = moment
        try:
            orca.speak_as_drive(watcher.name)
        except orca.OrcaError as error:
            yield read_failed(watcher, "drive terminal", error, moment)
        else:
            watcher.failures.pop("drive terminal", None)
        view.refresh_cards(watcher.name)
        found, source = view.stored_cards(watcher.name)
        watcher.cards = found if source == "live" else None
        yield github_lines(watcher, moment, polled=True)
        yield main_lines(watcher, moment)
        yield dangerous_lines(watcher, moment)
        yield carried_lines(watcher, moment)
        checks(watcher, moment)
        yield stage_lines(watcher, moment)
        draw_questions(watcher, moment)
        landed(watcher, moment)
        yield settle_lines(watcher, moment)
        yield follow_lines(watcher, moment)
        yield serving_lines(watcher, moment)
        yield build_lines(watcher, moment)
        yield status_lines(watcher, moment)
        yield waits_lines(watcher, moment)
        yield folded_lines(watcher, moment)
        yield finished(watcher, moment)
        yield recovered_lines(watcher, moment)
        yield owner_page_lines(watcher, moment)
        yield close_report_lines(watcher, moment)
        yield waiting_lines(watcher, moment)
        yield answer_lines(watcher, moment)
        yield noted_lines(watcher, moment)
        yield lane_lines(watcher, moment)
        release_lanes(watcher, moment)
        yield pushed_lines(watcher, moment)
        yield agent_lines(watcher, moment)
        yield stall_lines(watcher, moment)
        carry_held(watcher, moment)
        yield compact_lines(watcher, moment)
        yield page_lines(watcher, moment)
        yield down_lines(watcher, moment)
        yield dashboard_lines(watcher)
        yield receiver_lines(watcher, moment)
        yield watched_lines(watcher, moment)
        yield redraw_lines(watcher, moment)
    yield event_lines(watcher)
    if "github" not in watcher.failures:
        yield github_lines(watcher, moment)
    yield catch_up_lines(watcher, moment)
    yield claim_reply_lines(watcher, moment)
    yield claim_lines(watcher, moment)
    yield quiet_lines(watcher, moment)
    yield clock_lines(watcher, moment)
    yield undelivered_lines(watcher, moment)
    yield tell_lines(watcher, moment)
    yield standing_lines(watcher, moment)
    yield orphan_lines(watcher, moment)
    yield post_lines(watcher, moment)


def post_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Start the handler on each `POST` report to communicator@<drive>, whichever session wrote it, so the communicator starts none: the handler posts it in his DM and its line goes to the communicator, or on a drive whose roles hold none, as one started from a terminal holds none, to the coordinator. Each report is read once, through cci's cursor in a hold of the drive's POSTS_LOCK, which a stage of the watch starting its own report's handler and the handler tool starting one on a POST report read first too, so whichever reads a report first starts its one handler, however soon that handler ends; a handler that did not start sets the cursor back to its report, so the next poll starts it again."""
    if watcher.project:
        return []
    try:
        with state.flocked(state.drive_dir(watcher.name) / agents.POSTS_LOCK):
            agents.start_posts(watcher.name)
    except state.DriveError as error:
        return failed(watcher, "POST reports", "start of the POST reports' handlers", error, moment)
    watcher.failures.pop("POST reports", None)
    return []


def carried_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Carry each heard PR's check against his words to a head a restack moved, its change the one judged, so no second judge runs on it; a diff read that fails is retried and said as any GitHub read is."""
    data = state.load(watcher.name)
    try:
        served.carry(watcher.name, [key for key in state.open_pr_keys(data) if hears(watcher, data, data["prs"][key]["order"])])
    except state.DriveError as error:
        return read_failed(watcher, "moved heads' diffs", error, moment)
    watcher.failures.pop("moved heads' diffs", None)
    return []


def redraw_lines(watcher: Watcher, moment: datetime, keys: set[str] | None = None) -> list[str]:
    """Redraw in place each card of his on a heard open PR, or on those of keys alone, as github_lines asks for the PRs it just read, open or answered and neither replaced nor withdrawn, whose row cc-slack last drew differs from the card.REDRAWN lines drive card draws on it now, so its head, Where it stands, Waits on and staging line never stay stale: one slack.draw_pr_lines call writes those lines onto every row of each such PR, which cc-slack redraws in place in the background, buttons kept, and the watch's copy of his cards takes them too. A PR his open card drew asking his tap that no longer asks it is first redrawn whole from drive card, so the card gives up its Approve, and a whole redraw that fails leaves its lines unwritten, so the next pass asks both again."""
    from drive import card

    if watcher.cards is None:
        return []
    data = state.load(watcher.name)
    rows: dict[str, list[dict]] = {}
    for shown in [*watcher.cards["pr_cards"], *(closed for closed in watcher.cards["closed"] if "card" in closed)]:
        for row in shown["card"]["prs"]:
            rows.setdefault(state.pr_key(row["url"]), []).append(row)
    asking = {state.pr_key(row["url"]) for shown in watcher.cards["pr_cards"] for row in shown["card"]["prs"] if row.get("lands")}
    lines, fresh = [], {}
    for key, drawn in rows.items():
        pr = data["prs"].get(key)
        if (keys is not None and key not in keys) or not pr or pr["state"] != "open" or not pr["head"] or pr["rework"] or key in watcher.stale or not hears(watcher, data, pr["order"]):
            continue
        now = card.drawn_lines(watcher.name, data, key)
        if all({line: row.get(line, "") for line in card.REDRAWN} == now for row in drawn):
            continue
        if key in asking and not now["lands"]:
            try:
                slack.redraw_pr_cards(pr["url"])
            except slack.SlackError as error:
                lines += failed(watcher, "card redraw", f"redraw of his card on {key}", error, moment)
                continue
            watcher.failures.pop("card redraw", None)
        fresh[key] = now
    if not fresh:
        return lines
    try:
        slack.draw_pr_lines({data["prs"][key]["url"]: now for key, now in fresh.items()})
    except slack.SlackError as error:
        return lines + failed(watcher, "card lines", f"redraw of his card on {', '.join(fresh)}", error, moment)
    watcher.failures.pop("card lines", None)
    for key, now in fresh.items():
        for row in rows[key]:
            row.update(now)
    return lines


def stage_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Keep each heard unmerged PR's stage, as stage.of names it from the approval, checks and head GitHub last answered for it, and act once on each open one that entered a stage or passed its deadline, leaving a PR never read, or whose last read failed, as it stands until a read answers for it: draw his card on each PR that entered uncarded, one card for the PRs of one card unit as drive card draws them, and never again while it stays there, or redraw in place the card cc-slack keeps closed on one, so it offers Approve again and no second card shares its waits id, or, once a card of the unit leaves off a PR of its stack, native or chained by base branch, by view.apart, draw one fresh card for the unit and those PRs, which cc-slack marks each older card of theirs replaced by, wake the builder answering for a PR rework or red owe, ask forge-pr-reviewer once for a review of a head grading an hour and then wake its builder, wake past its deadline the job a waiting or blocked PR names as owing it and the builder of one its author lands, wake the session holding its order on one unlanded and on any other past its deadline, uncarded included, and draw his card again, once per card unit, on the PRs that wait on him while they fix a live bug and whose card asks his tap, by state.asks_tap, with a line in its thread saying why it asks. A draw or redraw in place that fails, or leaves a PR that asks his tap with no card of his offering Approve at its head, as one cc-slack answers by linking a card he already answered does or a redraw cc-slack leaves the card closed under, clears the PR's stage, so the next read enters uncarded again and draws, and is said to the meta session from its third failure in a row."""
    if watcher.cards is None:
        return []
    with state.edit(watcher.name) as data:
        stacks = land.landing_stacks(data)
        units = {key: state.card_unit(key, stacks.get(key, ()), pr) for key, pr in data["prs"].items()}
        acts = []
        for key in state.unmerged_pr_keys(data):
            pr = data["prs"][key]
            if key in watcher.stale or pr["required"] is None or not hears(watcher, data, pr["order"]):
                continue
            now = stage.of(data, key, watcher.cards)
            if stage.entered(pr["stage"], now):
                stage.stamp(pr, now, moment)
                if pr["state"] == "open":
                    acts.append((key, now, True, stage.line(key, pr, now) + f" {ids(pr)}"))
            elif stage.overdue(data, pr, now, moment):
                stage.kept(pr, now)
                pr["stage"]["woke"] = state.stamp(moment)
                acts.append((key, now, False, stage.line(key, pr, now) + f" {ids(pr)}"))
            else:
                stage.kept(pr, now)
    lines: list[str] = []
    uncarded = {}
    asked = {}
    for key, now, entered, line in acts:
        if now.name == "uncarded" and entered:
            uncarded.setdefault(units[key], []).append(key)
            continue
        if now.owner == stage.HIM and not entered:
            if state.asks_tap(data, data["prs"][key]):
                asked.setdefault(units[key], []).append(key)
            continue
        lines += acted(watcher, key, now, entered, line, moment)
    from drive import talk

    for keys in uncarded.values():
        standing = [key for key in keys if view.standing_card(data["prs"][key], watcher.cards)]
        left = [mate for key in standing for mate in view.apart(data, key, watcher.cards["pr_cards"])]
        try:
            if left:
                fresh = list(dict.fromkeys([*keys, *left]))
            else:
                for key in standing:
                    slack.redraw_pr_cards(state.pr_url(key))
                fresh = [key for key in keys if key not in standing]
            went = f"it went out as {talk.draw(watcher.name, fresh)}" if fresh else "it was redrawn in place"
            drew, found = state.load(watcher.name), view.live_cards(watcher.name)
            if bare := [key for key in dict.fromkeys([*keys, *fresh]) if state.asks_tap(drew, drew["prs"][key]) and not view.tap_card(drew, drew["prs"][key], found)]:
                raise state.DriveError(f"{went}, which offers him no Approve at the head of {', '.join(bare)}")
        except (slack.SlackError, state.DriveError) as error:
            with state.edit(watcher.name) as data:
                for key in keys:
                    data["prs"][key]["stage"] = None
            if said := failed(watcher, "card draw", f"draw of his card on {', '.join(keys)}", error, moment):
                lines += sent(watcher, "meta", said, moment)
            continue
        watcher.failures.pop("card draw", None)
    for keys in asked.values():
        try:
            talk.draw(watcher.name, keys, again=True)
        except (slack.SlackError, state.DriveError) as error:
            lines += failed(watcher, "card ask", f"ask again of his card on {', '.join(keys)}", error, moment)
            continue
        watcher.failures.pop("card ask", None)
    return lines


def draw_questions(watcher: Watcher, moment: datetime) -> None:
    """Draw his card on each open question of a heard job, kept in its file with no card drawn and no refusal, as drive job ask draws it, so a draw that failed in that call goes up on this read; a job that failed or stopped asks him nothing new, and one his answer to the same question settled since it was kept is kept settled by `keep_settled` and draws none. A draw that fails again is said to the meta session from its third failure in a row, and a card drive job card refuses leaves its question refused, which the job's line then says."""
    if watcher.cards is None:
        return
    from drive import cli

    data = state.load(watcher.name)
    undrawn = [
        (job_id, question, Path(question["file"]))
        for job_id, job in data["jobs"].items()
        if job["state"] not in state.ENDED_JOB_STATES and heard_job(watcher, data, job)
        for question in view.open_questions(job)
        if question["file"] and not (question["card"] or question["refused"])
    ]
    for job_id, question, file in undrawn:
        try:
            if answer := settled.answered(data, question["title"]):
                cli.keep_settled(watcher.name, job_id, file.read_text(), file, question["title"], question["unsettled"], answer)
                continue
            cli.post_ask(watcher.name, job_id, file, cli.ask_card(watcher.name, job_id, file))
        except (slack.SlackError, state.DriveError) as error:
            watcher.unsent.setdefault("meta", []).extend(failed(watcher, "question draw", f"draw of {job_id}'s card on {file}", error, moment))
            continue
        watcher.failures.pop("question draw", None)


def acted(watcher: Watcher, key: str, now: stage.Stage, entered: bool, line: str, moment: datetime) -> list[str]:
    """What the watch does for a PR that entered its stage, entered, or passed that stage's deadline, and the lines it says: his card withdrawn from a PR reworked to his words that still has one, then the act its stage owes, none for a PR behind another, whose owner that PR's stage wakes."""
    from drive import talk

    said = []
    data = state.load(watcher.name)
    pr = data["prs"][key]
    if pr["rework"] and view.standing_card(pr, view.live_cards(watcher.name) or view.NO_CARDS):
        try:
            talk.withdraw(watcher.name, key)
        except slack.SlackError as error:
            said += failed(watcher, "rework withdraw", f"withdrawal of his card on {key}, reworked to his words", error, moment)
    if now.name == "behind":
        return said
    job_id = now.owner if now.owner in data["jobs"] else None
    if now.name in ("rework", "red") or not entered and (job_id or now.name == "author"):
        stage.wake(watcher.name, key, line, job_id)
    elif now.name == "grading" and not entered:
        repo, number = key.split("#")
        if pr["review_asked"] == pr["head"]:
            stage.wake(watcher.name, key, line)
        elif refused := land.ask_review(repo, number, pr["head"]):
            said.append(f"STAGE {refused} {ids(pr)}")
        else:
            with state.edit(watcher.name) as data:
                data["prs"][key]["review_asked"] = pr["head"]
    elif now.name == "unlanded" or not entered:
        said.append(f"STAGE {line}")
    return said


def landed(watcher: Watcher, moment: datetime) -> None:
    """Stop each lander on a PR the watch hears that a hold of his or a wait came on since it started, and through land.start answer each card his Approve on one picks that is still unanswered and start the drive's lander on each once its stage reads ready, within one GitHub read of it getting there: green and cleared by his Approve or an exempt class, or a yasyf/* one its builder reported with every check at its head finished, which a head its builder pushes since its lander's try reaches again. A pick that fails to answer again is said to the meta session from its third failure in a row."""
    data = state.load(watcher.name)
    keys = [key for key in state.open_pr_keys(data) if hears(watcher, data, data["prs"][key]["order"])]
    try:
        land.halt(watcher.name, keys, watcher.cards)
    except github.GithubError as error:
        watcher.unsent.setdefault("meta", []).extend(failed(watcher, "halt", "read of the files of each queued PR his absence may hold", error, moment))
        return
    watcher.failures.pop("halt", None)
    try:
        land.start(watcher.name, keys, watcher.cards)
    except state.DriveError as error:
        watcher.unsent.setdefault("meta", []).extend(failed(watcher, "pick answer", "answer of the cards his Approve picks", error, moment))
        return
    watcher.failures.pop("pick answer", None)


def line_lane(line: str, data: dict) -> str | None:
    """The lane of the one session that acts on a line, whichever watch made it: the lane a MOVED line's orders left; for a line whose header names a job, as JOB, LANE, STUCK, DUE, FREE and ANSWER lines do, the session state.job_lane names, the one the Agent hook and the job verbs let start and message its worker; for any other line naming an order, by its header or a PR's trailing ids, the session state.holding_lane names; None for a line naming neither."""
    if moved := MOVED_LINE.match(line):
        return moved["lane"]
    if (named := NAMED_JOB.match(line)) and (job := data["jobs"].get(named["job"])):
        return state.job_lane(data, job)
    found = ABOUT.search(line)
    order = found and data["orders"].get(found["order"] or found["named"])
    return state.holding_lane(data, order) if order else None


def needs_step(line: str, data: dict, cards: dict | None) -> bool:
    """Whether a line asks for a step now: any line but a PR's, his tap but an Approve, which the drive's lander lands in every repository once its stage reads ready, a STAGE line on a PR the drive tracks only while stage.of still names that stage, so one made before its PR merged or its lander tried a newer head asks nothing, a PR's merged line only when nobody else says it, never GitHub's merged line for a PR the lander merges and says so itself or whose job took DONE at its merge, whose prover's JOB line asks the step, or is one the watch finishes in that pass, nor the lander's merged line on a job already proved or whose proof the watch runs, whose proof line says so, and a line on a PR no longer open or folded only when it says how the PR ended, its merge, close or failed proof, while its job does not run to take it, never a move made while it was open, such as a lander's try refused at an older head, new heads, checks or a check against his words. An open PR's moves wake nobody, since its stage wakes its owner. A standing part's pass that ended DONE or HANDOFF asks nothing by its own line alone, since the watch alone starts the next pass, quoting it; a clause the drive adds to that line, such as an ASK it did not keep, still asks its step."""
    if (tapped := TAP_EVENT.match(line)) and tapped["label"] == "Approve":
        return False
    if (job := JOB_LINE.match(line)) and standing_pass_ended(data, job["job"], line):
        return False
    if (staged := STAGE_LINE.match(line)) and staged["key"] in data["prs"]:
        return stage.of(data, staged["key"], cards).name == staged["name"]
    found = PR_EVENT.match(line)
    if not found:
        return not line.startswith("PR ")
    pr = data["prs"][found["key"]]
    if found["change"] == "merged":
        return not (land.reported(found["key"], pr) or data["jobs"][found["job"]]["state"] == "done" or finishes(data, found["job"]))
    if found["change"].startswith("merged at"):
        return not (data["jobs"][found["job"]]["state"] in state.FINISHED_JOB_STATES or proving(data, found["job"]))
    if pr["state"] in github.UNLANDED or not found["change"].startswith(ENDED):
        return False
    return data["jobs"][found["job"]]["state"] != "running"


def standing_pass_ended(data: dict, job_id: str, line: str) -> bool:
    """Whether a JOB line is a standing part's pass ending its round, DONE or HANDOFF, with no clause beside the line it said."""
    job = data["jobs"][job_id]
    return job["state"] in ("done", "stopped") and bool(state.standing_part(data, job)) and line == LINE_BREAKS.sub(" ", f"JOB {job_id} {job['order']} {job['said']}")


def fixing(line: str, data: dict, cards: dict | None) -> bool:
    """Whether a line is a builder's READY whose every open PR the lander lands on that READY with no step of the holder's, by state.ships_untapped, while stage.of owes its builder the fix, red, reworked to his words or behind a PR its builder fixes, which stage_lines sends that builder through drive pr wake: the communicator or the meta session holding its order would start a second builder on the same fix, so neither gets it, and the lander lands the head the builder pushes on the READY already taken."""
    if not (job := JOB_LINE.match(line)) or not line.startswith("READY", job.end()):
        return False
    keys = [key for key in state.named_prs(data, job["order"], line) if data["prs"][key]["state"] == "open"]
    return bool(keys) and all(state.ships_untapped(data, key) and stage.of(data, key, cards).owner == stage.BUILDER for key in keys)


def wakes(line: str, data: dict, cards: dict | None) -> bool:
    """Whether the coordinator acts on the line now: his tap but an Approve the drive's lander acts on, a promised time, an orphan, his answer, a lane's report or death but a standing part's pass ending its round, his return, a stopped watch, or a PR's line that asks for a step, such as its exempt landing or a failure of its landing."""
    return (line.startswith(WAKES) or line.startswith("PR ")) and needs_step(line, data, cards)


def handed(watcher: Watcher, lines: list[str], moment: datetime) -> list[str]:
    """Hand each line to the one who acts on it: the lander's own failure, once however many PRs it held back, to the meta session, each line line_lane names another session for to that session, and the rest, returned, to the watch's own coordinator. Until it is handed, each line stays on the watcher, the coordinator's among its made lines and another session's among its unsent, so a newer install that takes the watch over meanwhile gets it."""
    lines = [LINE_BREAKS.sub(" ", line) for line in lines]
    if not lines and not any(owed for lane, owed in watcher.unsent.items() if lane != watcher.lane):
        return []
    data = state.load(watcher.name)
    kept, routed = [], {}
    for line in lines:
        if line.startswith(LANDER):
            if line not in [*watcher.unsent.get("meta", []), *(meta := routed.setdefault("meta", []))]:
                meta.append(line)
        elif (lane := line_lane(line, data)) not in (None, watcher.lane):
            routed.setdefault(lane, []).append(line)
        else:
            kept.append(line)
    watcher.made = kept
    lanes = dict.fromkeys([*state.HOLDERS, *routed, *watcher.unsent])
    failures = [failure for lane in lanes if lane != watcher.lane and (lane in routed or not lines) for failure in sent(watcher, lane, routed.get(lane, []), moment)]
    watcher.made = []
    return kept + failures


def sent(watcher: Watcher, lane: str, lines: list[str], moment: datetime) -> list[str]:
    """Post the lines owed the lane over cci as one record, by posted_record, a handoff when any of them asks a step now, by wakes, or the lane is the communicator's or the meta session's, else evidence, which reaches the lane with others at most every five minutes, the lander's own failures apart as one defect, which the meta session starts a fix from, so a pass that says many gaps reaches the lane as one record however many it says; but drop each one owed the communicator or the meta session that needs_step, judging it as it posts, finds asks no step now, or that is a READY whose fix its builder owes, by fixing(), so a line held across a newer install's takeover or a failed post never hands work that ended meanwhile: each credential cci refuses is withheld from it, and a record that fails goes again when the next pass starts, sent with no lines, each line made meanwhile waiting behind it, so a pass tries a failing lane once and its third failure in a row is said. The lines stay among the watcher's unsent until their record posts, so a newer install that takes the watch over meanwhile gets them."""
    if lines and watcher.unsent.get(lane):
        watcher.unsent[lane] += lines
        return []
    owed = watcher.unsent[lane] = watcher.unsent.get(lane, []) + lines
    if not owed:
        return []
    data = state.load(watcher.name)
    posting = [line for line in owed if lane not in state.HOLDERS or needs_step(line, data, watcher.cards) and not fixing(line, data, watcher.cards)]
    defects = [line for line in posting if line.startswith(LANDER)]
    rest = [line for line in posting if line not in defects]
    kind = "handoff" if lane in state.HOLDERS or any(wakes(line, data, watcher.cards) for line in rest) else "evidence"
    refusal, unsent = None, []
    for each, said in (("defect", defects), (kind, rest)):
        try:
            posted_record(watcher.name, lane, each, said, Path(data["roles"]["coordinator"]["cwd"]))
        except reads.ReadError as error:
            unsent += said
            refusal = error
    watcher.unsent[lane] = unsent
    project = next((project for project in data["subcoordinators"] if state.project_lane(project) == lane), lane)
    what = {"communicator": "handoff post to the communicator", "meta": "handoff post to the meta session", "root": "post to the coordinator"}.get(lane, f"post to the sub-coordinator for {project}")
    if refusal is None:
        watcher.failures.pop(what, None)
        return []
    return failed(watcher, what, what, refusal, moment)


def posted_record(name: str, lane: str, kind: str, lines: list[str], checkout: Path) -> None:
    """Post these lines to the lane as one cci record of this kind: one line as its text, the remote lane's question or status it names whole as its body, by whole_message; several counted by first word as its text, as `35 lines: 35 ORPHAN`, each line in its body with any such message under it, indented."""
    if not lines:
        return
    wholes = [whole_message(name, line) for line in lines]
    if len(lines) == 1:
        text, body = lines[0], wholes[0]
    else:
        counts = collections.Counter(line.partition(" ")[0] for line in lines)
        text = f"{len(lines)} lines: " + ", ".join(f"{count} {word}" for word, count in counts.items())
        body = "".join(f"{line}\n" + ("".join(f"  {row}\n" for row in whole.splitlines()) if whole else "") for line, whole in zip(lines, wholes, strict=True))
    topic = f"watch-{hashlib.sha1('\n'.join(lines).encode()).hexdigest()[:10]}"
    argv = ["cci", "post", "--drive", name, "--lane", LANE, "--kind", kind, "--to", f"{lane}@{name}", "--topic", topic, *(["--path", "-"] if body else []), "--text", reads.withheld(text)]
    reads.run(argv, checkout, reads.posting(), stdin=body and reads.withheld(body))


def whole_message(name: str, line: str) -> str | None:
    """The whole of the lane message a JOB line names: the status its `MESSAGE <id>:` names when its body says more than the line, or the newest question its job's history keeps that the line names by its first line, a result sent as a question aside; None for any other line or a question of one line."""
    if not (found := JOB_LINE.match(line)):
        return None
    said = line.removeprefix(found[0])
    kept = state.replies(name, found["job"])
    if message := MESSAGE_LINE.match(said):
        body = next((reply["body"] or "" for reply in kept if reply["id"] == message["id"]), "")
        return body if body.strip() not in ("", said.removeprefix(message[0])) else None
    for reply in reversed(kept):
        body = reply["body"] or ""
        opening = state.opening(body)
        if reply["type"] == "question" and body.strip() != opening and not state.reported(opening) and (said == opening or said.startswith(f"{opening}; ")):
            return body
    return None


def holder(path: Path) -> int | None:
    held = path.read_text().strip() if path.exists() else ""
    return int(held) if held.isdigit() and state.alive(int(held)) else None


def claim(name: str, project: str | None = None) -> bool:
    path = state.pid_path(name, project)
    with state.locked(name):
        if holder(path) not in (None, os.getpid()):
            return False
        path.write_text(f"{os.getpid()}\n")
    return True


def release(name: str, project: str | None = None) -> None:
    path = state.pid_path(name, project)
    with state.locked(name):
        if path.exists() and path.read_text().strip() == str(os.getpid()):
            path.unlink()


def label(name: str, project: str | None = None) -> str:
    """The label of the launch agent keeping the drive's watch, or a project's."""
    return f"{launchd.LABEL}.watch.{name}" + (f".{state.project_lane(project)}" if project else "")


def ensure(name: str, project: str | None = None) -> str:
    """Load the launch agent keeping the drive's watch, or a project's, unless it is loaded or LONG_RUNNING_STATE or XDG_STATE_HOME moves the drive's state root, by state.scratch: launchd starts the watch now and again within RESTART_SECONDS of any exit, crash included, on the newest install, its output in its own log and its environment naming the drive; the line saying which. A project's watch first reads the events from where the log ended as its sub-coordinator was bound."""
    watcher = f"the watch for {project}" if project else "the watch"
    reader = f"the sub-coordinator for {project}" if project else "the coordinator"
    if state.scratch():
        return f"{watcher} does not start for a drive whose state LONG_RUNNING_STATE or XDG_STATE_HOME moves to {state.root()}; a proof runs its passes in-process with watch.run_pass(watch.Watcher(...), moment)"
    agent = f"its launch agent {label(name, project)}, which starts it again within {launchd.RESTART_SECONDS} seconds of any exit, crash included"
    if launchd.kept(label(name, project)):
        return f"{watcher} runs under {agent}, and posts its lines to {reader} over cci; arm no Monitor for it"
    launchd.keep(
        label(name, project),
        ["watch", "--loop", "--drive", name, *(["--project", project] if project else [])],
        state.watch_log_path(name, project),
        {"CLAUDE_LONG_RUNNING_LANE": LANE, state.DRIVE_ENV: name},
        state.drive_dir(name),
    )
    return f"started {watcher} under {agent}; it posts its lines to {reader} over cci, so arm no Monitor for it"


def stopped(watcher: Watcher, error: Exception) -> None:
    """Say the watch stopped, naming its version and where the error was raised, the innermost frame of this package's code, so the line alone says which line to read."""
    frame = next(frame for frame in reversed(traceback.extract_tb(error.__traceback__)) if Path(frame.filename).resolve().parent == Path(__file__).resolve().parent)
    where = f"{type(error).__name__} at {Path(frame.filename).name}:{frame.lineno} in {frame.name}"
    said = f"{where}: {cut(str(error), 200)}" if str(error) else where
    line = f"WATCH stopped on long-running {state.VERSION}: {said}; its traceback is in {state.watch_log_path(watcher.name, watcher.project)}; its launch agent starts it again within {launchd.RESTART_SECONDS} seconds"
    with contextlib.suppress(reads.ReadError):
        sent(watcher, watcher.lane, [line], state.now())


def main(drive_name: str | None = None, project: str | None = None) -> int:
    """Run the drive's watch, or a project's, as the process its launch agent starts, until it stops, once a watch an older install ran under a Monitor has ended, with the env of this Mac's Claude settings in its environment, so each read it runs carries the API keys a session here carries. Any other process, such as one an older install's watch re-executed itself into, lets the watch go, loads its agent and ends, so launchd runs every watch. On a drive whose state root LONG_RUNNING_STATE or XDG_STATE_HOME moves, end at once, so a watch an older install started there ends at its next re-exec."""
    if state.scratch():
        print(f"no watch runs for a drive whose state LONG_RUNNING_STATE or XDG_STATE_HOME moves to {state.root()}", flush=True)
        return 0
    name = state.resolve(drive_name)
    if os.environ.get(launchd.LAUNCHED_ENV) != label(name, project):
        release(name, project)
        print(ensure(name, project), flush=True)
        return 0
    if not claim(name, project):
        return 0
    try:
        os.environ.update(launchd.session_env())
        while holder(state.monitor_pid_path(name)):
            time.sleep(POLL_SECONDS)
        held = state.take_held_reads(name, project)
        owes = {"owed": set(held["owed"]), "merging": set(held["merging"]), "stale": set(held["stale"]), "untracked": held["untracked"]} if held else {}
        watching(Watcher(name, offset=state.watch_cursor(name, project), project=project, cards=view.live_cards(name), made=state.take_held_lines(name, project), **owes))
    finally:
        release(name, project)
    return 0


def watching(watcher: Watcher) -> None:
    """Hand each pass's lines to whoever acts on them as each of its stages returns, first any a watch before this one held for it, and end before a pass once a newer long-running is installed, or the moment a pass finds the drive saved by one, so its launch agent starts the watch on that install and no pass runs older code on a drive a newer install saved; a crash wakes the coordinator session it posts to first, and an error that stopped its relay answering the lanes' reads stops it before its next pass. Every line made and not yet posted is held for the watch after this one, which hands it on before its first pass."""
    state.save_watch_cursor(watcher.name, watcher.offset, watcher.project)
    try:
        with relaying(watcher):
            for _ in rests(watcher.name):
                if upgraded():
                    break
                if watcher.relay_error:
                    raise watcher.relay_error
                read_from, moment = watcher.offset, state.now()
                for said in [handed(watcher, watcher.made, moment)] if watcher.made else passed(watcher, moment):
                    for line in sent(watcher, watcher.lane, said, moment):
                        print(line, flush=True)
                if watcher.offset != read_from:
                    state.save_watch_cursor(watcher.name, watcher.offset, watcher.project)
    except Exception as error:
        keep_unposted(watcher)
        if isinstance(error, state.NewerDrive) and upgraded():
            return
        stopped(watcher, error)
        raise
    keep_unposted(watcher)


def rests(name: str) -> Iterator[None]:
    """Yield at once, then POLL_SECONDS after each yield, or the moment anything appends to the drive's events file, as the receiver does for each delivery, so the pass that reads its PRs starts as it lands."""
    with open(state.events_path(name), "a") as events:
        queue = select.kqueue()
        queue.control([select.kevent(events, select.KQ_FILTER_VNODE, select.KQ_EV_ADD | select.KQ_EV_CLEAR, select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND)], 0)
        while True:
            yield
            queue.control(None, 1, POLL_SECONDS)


def keep_unposted(watcher: Watcher) -> None:
    """Move each line the watch made and has not posted, routed or not, to the watch process after it, which hands them on before its first pass and reads events from past the last one they were made from, and, once it read GitHub, the reads it still owes, so the watch after it reads those and the PRs the deliveries past that event name, never every PR again unless it reads GitHub by another query."""
    state.save_watch_cursor(watcher.name, watcher.offset, watcher.project)
    state.hold_lines(watcher.name, watcher.project, [LINE_BREAKS.sub(" ", line) for line in [*watcher.made, *drained(watcher.relayed), *(line for lines in watcher.unsent.values() for line in lines)]])
    if watcher.owed is not None:
        state.hold_reads(watcher.name, watcher.project, {"owed": sorted(watcher.owed), "merging": sorted(watcher.merging), "stale": sorted(watcher.stale), "untracked": watcher.untracked})
    watcher.made, watcher.unsent = [], {}
