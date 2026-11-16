import collections
import contextlib
import faulthandler
import fcntl
import functools
import hashlib
import itertools
import json
import marshal
import mmap
import os
import re
import select
import shlex
import signal
import subprocess
import sys
import threading
import traceback
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import NamedTuple, Self

from drive import agents, answer, brief, children, codex, dashboard, github, install, land, launchd, limits, orca, page, place, procs, providers, reads, receiver, refs, render, restack, served, settled, slack, stage, stall, state, superseded, superseded_procs, train, view, work
from drive.install import PROOF_FAILED, add_prover, config_dir, handed_on, open_cut, done_before_proofs, done_unproven, merged_done, merged_ready, merges_of, merged_said, product, proof_gap, proofless, proved_prs, prover_of, proves, released_prs, reported_prs, watch_upgraded as upgraded, watch_proves, widen
from drive.render import cut, when

POLL_SECONDS = 5
ACTION_TEXTS = (state.STAGING_CASE.partition("{key}")[0],)
RELAY_SECONDS = 2
PROJECT_RELAY_IDLE = 10
POSTS_IDLE = 30
POSTS_FOR = "10m"
RESPOND_SECONDS = 5
RELAY_JOIN_SECONDS = 10
RELAY = "--relay"
READ_EVERY = timedelta(seconds=60)
CHECK_SWEEP = timedelta(minutes=30)
SETTLED_READ_EVERY = timedelta(minutes=10)
PASS_FOR = timedelta(minutes=5)
HAND_OFF_EVERY = timedelta(minutes=10)
HAND_OFF_LATEST = timedelta(minutes=20)
STALL_FOR = timedelta(minutes=2)
SLOW_STAGE = timedelta(minutes=1)
LOGGED_STAGE = timedelta(seconds=10)
BEAT_SECONDS = 15
BEAT_FILE = "watchd.beat"
EVERY_PROJECT_ENV = "LONG_RUNNING_WATCH_EVERY_PROJECT"
EVERY_PROJECT = "1"
FAILURES_BEFORE_SAYING = 3
FAILURE_EVERY = timedelta(minutes=10)
DUE_EVERY = timedelta(minutes=10)
DUE_AHEAD = timedelta(minutes=5)
CLAIM_REPLIES = timedelta(minutes=5)
HANDOFF_FACT = "handoff"
WAITED_FACT = "card waiting"
DIGEST_AGE = timedelta(hours=24)
FREED_AGAIN = timedelta(minutes=30)
ELAPSED = re.compile(r"\b\d+(?: minutes?| hours?| seconds?|s\b)|\b\d{1,2}:\d{2}(?::\d{2})?(?: [AP]M)?")
BUILT_BASE = "{built}, the change yours builds on, its PR held on his answer: cut your branch from that head, so your PR stacks on it"
LIVE_EVERY = timedelta(minutes=5)
TELL_AFTER = timedelta(minutes=10)
WAKES = ("TAP ", "DUE ", "TELL ", "PROVE ", "ORPHAN ", "ANSWER ", "JOB ", "LANE ", "STAGE ", "FREE ", "MOVED ", "WATCH stopped")
PR_EVENT = re.compile(r"PR (?P<key>\S+): (?P<change>.+) \[(?P<order>o\d+) (?P<job>j\d+)\]$")
STAGE_LINE = re.compile(r"STAGE (?P<key>\S+) (?P<name>\w+)\b")
ENDED = ("merged", "closed", PROOF_FAILED)
NOT_LANDED = "not landed"
LANDER = "LANDER "
FAILED_LINE = re.compile(r"WATCH .+ failed \d+ times in a row: ")
QUIET_LINES = ("CARD ", "WAITING-ON-HIM ", "STANDING ", "WATCH compacted", "WATCH status page")
SAID_FILE = "said.json"
SAID_FOR = timedelta(hours=24)
EVERY_TIME = ("DOWN ",)
STALE_FOR = timedelta(hours=1)
STALE_TEXT = settled.Noul(
    "Does `answer.said`, his answer, remove a premise `texts.{slot}` rests on, so a worker acting on that text would go against his answer?",
    no="The text still holds beside his answer, or speaks to something else.",
)
COALESCE = timedelta(minutes=10)
POSTED_KEY = "posted:"
WATCH_STARTS = "start no agent for it"
URGENT = ("TAP ", "DUE ", "MOVED ", "WATCH stopped", "DOWN ")
TAP_EVENT = re.compile(rf"TAP (?P<label>\S+) (?P<key>\S+)(?P<stalled> {NOT_LANDED})?")
LANE = "watch"
WATCH_OUTBOX = ""
LANDING_OUTBOX = ".land"
READS_LOG = "reads.log"
CONFLICTS = "conflicts with its base"
NEWEST_MAIL = "newest lane mail"
BAR_PROMOTED = "his Slack back-and-forth with the drive crossed a bar"
LANE_MAIL = "lane mail"
MARK_READ = "marking lane mail read"
DRAIN_EVERY = timedelta(hours=1)
UNANSWERED = "receiver check"
ANSWERS = "answers"
LANE_WAIT = timedelta(seconds=reads.LANE_GIVES_UP)
RELAUNCHES_LOG = "relaunches.log"
STANDING_CHECKS_LOG = "standing-checks.log"
TRANSCRIPTS_LOG = "transcripts.log"
DISKS_LOG = "disks.log"
DISKS_STARTED = "disks-check.started"
DISKS_EVERY = timedelta(minutes=2)
PROOF_TAIL = 20
PASS_DONE = (
    "this pass's round is done: it returns DONE naming what the round did and where the next round starts, which the watch quotes to the next"
    " pass, since drive status reads a pass's HANDOFF as stopped"
)
RELAUNCH_REFUSED = "relaunch_refused"
START_AT_ONCE = 4
LANE_READS_AT_ONCE = 6
SPENT_SHOWN = 3
SPENT_WORDS = 3
SPENT_CHARS = 60
READ_UNANSWERED = "read_unanswered"
PROOFS_LOG = "proofs.log"
POSTS_LOG = "posts.log"
UNTHREADED = "no thread holds the order's lines to him, so its time was the drive's own and is said this once"
CUT_OFF = (
    f"its read stopped before it finished, so what it did and printed are unknown, its output in the drive's {READS_LOG}, and no read"
    " runs twice: read what a drive command changed, then ask again if it must run again"
)
LIVE_JOB_STATES = ("running", "waiting")
SAFEGUARDED = "stopped by its model's safeguards"
SAFEGUARD_RESTARTS = 2
SAFEGUARD_MODEL = "claude-sonnet-5-5"
SAFEGUARD_OPTIONS = ("Restart", "Leave it stopped")
QUIET_LANE = timedelta(minutes=5)
UNPROMPTED = timedelta(minutes=15)
KEPT_RETRY = timedelta(hours=1)
RECLAIMED = "reclaimed by the watch's drive job reclaim once its job was quiet"
ADOPTED = "its Sprite stayed up for this job's lane though no row or death of the job named it, so the watch adopted it for drive job reclaim"
ADOPT_AFTER = timedelta(hours=1)
SPRITE_LIST = "sprite list"
BOX_LIST = "the Mac box list"
BOX_SWEEP = timedelta(minutes=10)
LINE_BREAKS = re.compile(r"[\r\n]+")
HANDOFF = timedelta(minutes=2)
COMMUNICATOR_IDLE = timedelta(hours=2)
ENV_ALERT_EVERY = timedelta(minutes=1)
ENV_ALERT_READ = 20
REPLY_READ = 100
SETTLING = ""
PR_LINE = re.compile(r"\s*(?:READY|PUSHED|PR:)")
JOB_LINE = re.compile(r"JOB (?P<job>j\d+) (?P<order>o\d+) ")
SETTLED_RETURN = re.compile(r"(?:WAITING|PUSHED)\b")
MESSAGE_LINE = re.compile(r"MESSAGE (?P<id>\S+): ")
MERGE_ENDS = re.compile(r"(?:READY|PUSHED|WAITING)\b")
UNKEPT_ASK = "keep it with `drive job ask {job}`, which draws his card on it, since no card draws for it until then"
ASK_FIELD = re.compile(r"^ASK:?[ \t]+([/~]\S*)[ \t]*$", re.MULTILINE)
PAUSED = "The recommended answer to this question is built and paused on your pick: tap it and the work ships; another pick rebuilds it first."
QUIET_FOR = timedelta(minutes=30)
REAP_AFTER = timedelta(hours=6)
RELAUNCH_RETRY = timedelta(minutes=5)
RELAUNCH_TRIES = 3
UNSTAFFED_FOR = timedelta(minutes=5)
UNSTARTED_FOR = timedelta(minutes=1)
READ_WAIT = timedelta(minutes=30)
RELAUNCH = timedelta(seconds=orca.LAUNCH_SECONDS)
RECLAIM = timedelta(seconds=orca.RECLAIM_SECONDS)
TAIL_BYTES = 65536
COMPACT_PAST_TOKENS = 120_000
COMPACT_WHEN_QUIET = timedelta(minutes=1)
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
RELEASE_BLOCKED_AFTER = timedelta(minutes=30)
NO_BLOCKS = {"blocks": {"edges": []}}
RELEASE_COVERS = {"migrations": "Migrations", "replaces": "Replaces", "deletes": "Deletes"}
RELEASE_MEANWHILE = "The release waits at this approve step until you tap; nothing it holds back applies meanwhile."
ABOUT = re.compile(r"\[(?P<order>o\d+) (?P<job>j\d+)\]$|^[A-Z]+ (?:\S+ )?(?:j\d+ )?(?P<named>o\d+)\b")
MOVED_LINE = re.compile(r"MOVED (?:o\d+, )*o\d+ from (?P<lane>\S+) to ")


@dataclass
class Watcher:
    name: str
    offset: int
    project: str | None = None
    delivered: int = 0
    read_at: datetime | None = None
    boxes_at: datetime | None = None
    drained_at: datetime | None = None
    failures: dict[str, int] = field(default_factory=dict)
    failure_said: dict[str, datetime] = field(default_factory=dict)
    cards: dict | None = None
    owed: set[str] | None = None
    merging: set[str] = field(default_factory=set)
    stale: set[str] = field(default_factory=set)
    unhookable: set[str] = field(default_factory=set)
    polled: dict[str, datetime] = field(default_factory=dict)
    agent_gaps: dict[str, str | None] = field(default_factory=dict)
    made: list[str] = field(default_factory=list)
    resume: str | None = None
    unsent: dict[str, list[str]] = field(default_factory=dict)
    owed_at: dict[str, datetime] = field(default_factory=dict)
    outbox: str = ""
    outboxed: list[str] = field(default_factory=list)
    handed_at: dict[str, datetime] = field(default_factory=dict)
    down: str | None = None
    dashboard_down: bool = False
    serving: dict[str, dict[str, bool]] | None = None
    fix_waits: dict[str, list[str]] | None = None
    production: set[str] = field(default_factory=set)
    live_at: datetime | None = None
    freezes_at: datetime | None = None
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
    redrawn_whole: set[str] = field(default_factory=set)
    restacked: dict[str, tuple] = field(default_factory=dict)
    checking: set[str] = field(default_factory=set)
    swept_at: datetime | None = None
    rulings: frozenset[str] | None = None
    others: dict[str, Self] = field(default_factory=dict)
    joined_at: datetime | None = None
    passed_version: int | None = None
    passed_at: float = 0.0
    receiver_took: set[str] | None = None
    floor: datetime | None = None

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
    if failures != FAILURES_BEFORE_SAYING:
        return []
    said = watcher.failure_said.get(source)
    if said and moment - said < FAILURE_EVERY:
        return []
    watcher.failure_said[source] = moment
    return [f"WATCH {what} failed {failures} times in a row: {error}"]


def ids(pr: dict) -> str:
    return f"[{pr['order']} {pr['job']}]"


def polled_settled(watcher: Watcher, pr: dict, key: str, moment: datetime) -> bool:
    """Whether the watch read this green, approved PR of a repository GitHub tells the receiver nothing of within SETTLED_READ_EVERY, so its poll waits for that, since nothing on such a PR moves but by a push, review or merge."""
    return pr["checks"] == "pass" and pr["review"] == "approved" and key in watcher.polled and moment - watcher.polled[key] < SETTLED_READ_EVERY


def pr_changes(pr: dict, read: dict, entry: dict) -> list[str]:
    """What moved on the PR since the watch last read it, saved onto its row, its landing by github.live; ejected once Trunk put the trunk-failed label on entry, the read of the PR whose Trunk queue entry carries it, after the PR's lander started its try, since Trunk labels only the top PR its lander queued and leaves the label on through later pushes; its lander's try ends dequeued, out of the queue, once Trunk put the trunk-cancelled label on entry after that try started and ejected nothing, as when the merge queue's dequeue took it out; each review:<login> wait it carries ends once that teammate's newest review is of its head, kept on its reviewed for his card's first line."""
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
    landed = {wait: review for wait in pr["waits"] if (asked := state.REVIEW_WAIT.fullmatch(wait)) and (review := read["reviews"].get(asked["login"].casefold())) and review["head"] == fresh["head"]}
    changes += [f"{review['login']} reviewed {review['head'][:7]}" for review in landed.values()]
    pr.update(fresh, draft=read["isDraft"], ejected=ejected, waits=[wait for wait in pr["waits"] if wait not in landed], reviewed=[*pr["reviewed"], *landed.values()])
    return changes


def judged(data: dict, reads: dict[str, dict]) -> tuple[dict[str, dict], dict[str, str]]:
    """The watch's verdict, by land.own_reds, on each open yasyf/* PR read failing a check at a head and failing set no verdict was kept for, so its stage reads a check failing on its own change red, owed by its builder, at the read that found it failing, never ready for its lander to find it; none while the default branch's run of one still runs, which the lander waits out; with why each PR's verdict could not be read, whose read is then refused."""
    owns, misjudged = {}, {}
    for key, read in reads.items():
        failing = github.failing(read["checks"])
        kept = data["prs"][key]["own_reds"] or {}
        if read["state"] != "OPEN" or not failing or not state.merges_untapped(key) or (kept.get("head"), kept.get("failed")) == (read["headRefOid"], failing):
            continue
        try:
            judgment = land.own_reds(key, read["checks"])
        except github.GithubError as error:
            misjudged[key] = str(error)
            continue
        if judgment is not None:
            owns[key] = {"head": read["headRefOid"], "failed": failing, "own": judgment[0]}
    return owns, misjudged


def reran(data: dict, reads: dict[str, dict]) -> list[str]:
    """Rerun once, with gh run rerun --failed, each GitHub Actions run on an open PR's head whose every failed job failed on the run's first attempt at its checkout, by github.failed_at_checkout, since no step ran the PR's code there and its builder has nothing to fix, and read those jobs queued, so the PR's checks read pending and its stage grading, waking no builder; a checkout that fails again on the rerun reads red, owed by its builder, as any failure does. A job read or rerun GitHub refused leaves the run red, said."""
    lines = []
    for key, read in reads.items():
        if read["state"] != "OPEN":
            continue
        failed = sorted((check for check in github.counted(read["checks"]) if github.outcome(check) in github.FAILED and github.job_of(check)), key=github.run_of)
        for (workflow, run_id), jobs in itertools.groupby(failed, key=github.run_of):
            jobs = list(jobs)
            try:
                if not all(github.failed_at_checkout(github.repo_of(key), github.job_of(check)) for check in jobs):
                    continue
                github.run(["gh", "run", "rerun", run_id, "--failed", "-R", github.repo_of(key)])
            except github.GithubError as error:
                lines.append(f"WATCH {key}'s failed {workflow} run {run_id} stays red, since GitHub refused its read or rerun: {error}")
                continue
            for check in jobs:
                check["conclusion"] = None
            lines.append(f"PR {key}: reran {workflow}, whose checkout failed before any of its code ran {ids(data['prs'][key])}")
    return lines


def github_reads(watcher: Watcher) -> Path:
    return state.drive_dir(watcher.name) / f"github-read-{state.project_lane(watcher.project or '') or 'drive'}.json"


def changed_since_read(watcher: Watcher, heard: list[str]) -> set[str] | None:
    """The heard PRs a watch that starts again owes a whole read: each whose signature GitHub gives now differs from the one its last whole read kept, by github.signatures, so a restart reads only what moved while it was down; None, owing every PR, while no read was kept or the signatures could not all be read."""
    if not (signed := github_reads(watcher)).is_file():
        return None
    kept = json.loads(signed.read_text())
    found, refused = github.signatures(heard)
    if refused:
        return None
    return {key for key in heard if found.get(key) is None or found[key] != kept.get(key)}


def github_lines(watcher: Watcher, moment: datetime, polled: bool = False) -> list[str]:
    """Read from GitHub each heard unmerged PR the watch owes a read, and say what changed on each, reading none while the reset ccx's rate-limit floor refused its last read until has not come, by floored, which says that refusal once. It reads whole every one at a watch's start that no watch before it handed its owed reads to from the same GitHub query, by state.take_held_reads, so a field a new install reads fills on every row, then each a delivery named, each open one on a repository GitHub tells the receiver nothing of, by receiver.silent, and every open one while the receiver's last check found it neither taking deliveries nor answering, on each READ_EVERY pass, polled, a green, approved one only each SETTLED_READ_EVERY, ahead of that pass's stage_lines, since no delivery ever says it changed, each never asked for, each any waiting builder's PUSHED names unread since its take, whichever watch hears that builder, each folded into another open PR's branch, beside the PR it folded into, every pass until that PR lands or closes, and again each whose read GitHub refused, a read of an open PR whose GitHub Actions run failed only at its checkout rerunning that run and reading it queued, by reran(), a read of an open yasyf/* PR failing a check carrying the watch's verdict on whether its own change fails it, by judged(), and refused when GitHub would not give that verdict. Of each open PR a push to its base named, and each whose mergeability GitHub had not worked out yet, since no delivery says when it has, it reads only the state, head and mergeability, by github.mergeability, saying a conflict that read starts, and reads whole in the same call each that read finds moved or ended. A PR GitHub answered nothing for stays stale, as its stage does, until a read answers for it. In the same call it reads each PR the drive does not track that carries a stopped builder's parts or that a live job keeps or waits on, by state.carrier_keys, once until GitHub answers for it and again each time a delivery names it, keeping only GitHub's state of it on the watcher for job_gap and out_because, and saying nothing of it. A read saves its PR's row only when it moved, stamping updated, or when a waiting builder's PUSHED names it unread, stamping its required read, so a read that finds nothing new writes nothing. What it still owes is kept on the watcher only once the reads are saved, so a pass that ends before then hands the next watch the reads it owed. Once they are saved it redraws in place, by redraw_lines, his card on each PR it read that the read left drawn stale, so a read that turns a PR green takes "CI passes" off his card in the same pass, never at the next pass's redraw_lines minutes later."""
    if watcher.floor and moment < watcher.floor:
        return []
    data = state.load(watcher.name)
    pushed = {key for job_id, job in data["jobs"].items() if awaits_checks(job) for key in state.unread_since_take(data, job_id)}
    heard = [key for key in state.unmerged_pr_keys(data) if hears(watcher, data, data["prs"][key]["order"])]
    if watcher.owed is None:
        watcher.owed = changed_since_read(watcher, heard)
    repos = {github.repo_of(key) for key in heard}
    hookless = (repos if UNANSWERED in watcher.failures else set(receiver.silent(repos))) if polled and not state.scratch() else set()
    owed = [
        key
        for key in heard
        if watcher.owed is None
        or key in watcher.owed
        or key in pushed
        or github.repo_of(key) in hookless and data["prs"][key]["state"] == "open" and not polled_settled(watcher, data["prs"][key], key, moment)
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
    reruns = reran(data, reads)
    owns, misjudged = judged(data, reads)
    if misjudged:
        reads = {key: read for key, read in reads.items() if key not in misjudged}
        refused = github.GithubError("; ".join([*([str(refused)] if refused else []), *misjudged.values()]))
    unread = set(keys) - set(reads)
    unanswered = set(remerge) - set(merged)
    owes = (unread | set(carried) - set(carrying) if refused else set()) | (set() if unmerged else unanswered)
    merging = (unanswered if unmerged else set()) | {key for key, read in reads.items() if read["mergeable"] == "UNKNOWN"} | {key for key, read in merged.items() if key not in moved and read["mergeable"] == "UNKNOWN"}
    stale = watcher.stale - set(reads) | unread
    failure = "; ".join(str(error) for error in (unmerged, refused) if error)
    lines = [*reruns, *(read_failed(watcher, "github", github.GithubError(failure), moment) if failure else [])]
    if resets := github.FLOOR.findall("\n".join([failure, *reruns])):
        lines = floored(watcher, lines, datetime.fromisoformat(resets[-1]), next(text for text in [failure, *reruns] if github.FLOOR.search(text)))
    if not failure:
        watcher.failures.pop("github", None)
    foreign, redraws = {}, []
    if reads or merged:
        def github_lines_write(data: dict) -> tuple[list[str], dict[str, tuple[str, str, str]], list[str], dict]:
            said, foreign, redraws = [], {}, []
            for key, read in reads.items():
                pr = data["prs"][key]
                was = marshal.loads(marshal.dumps(pr))
                unlanded = pr["state"] in github.UNLANDED
                changes = pr_changes(pr, read, reads.get((pr["lander"] or {}).get("entry"), read))
                watcher.polled[key] = moment
                if key in owns:
                    pr["own_reds"] = owns[key]
                if was["required"]:
                    pr["required"]["read"] = was["required"]["read"]
                if row_moved := pr != was:
                    pr["updated"] = state.stamp(moment)
                if row_moved or key in pushed:
                    pr["required"]["read"] = state.stamp(moment)
                if why := state.foreign_pr(data, key):
                    foreign[key] = (pr["url"], pr["author"], f"{why} {ids(pr)}")
                    continue
                if edited_since_undrawn(pr, read, watcher.cards):
                    redraws.append(pr["url"])
                if unlanded or pr["state"] == "open":
                    said.extend(f"PR {key}: {change} {ids(pr)}" for change in changes)
            for key, read in merged.items():
                pr = data["prs"][key]
                if key in moved or (conflict := github.conflicts(read, pr)) == pr["conflict"]:
                    continue
                if conflict:
                    said.append(f"PR {key}: {CONFLICTS} {ids(pr)}")
                pr.update(conflict=conflict, updated=state.stamp(moment))
            return said, foreign, redraws, data

        said, foreign, redraws, data = state.write(watcher.name, github_lines_write, budget=state.WATCH_WRITE_SECONDS)
        lines += said
    if reads:
        signed = github_reads(watcher)
        kept = json.loads(signed.read_text()) if signed.is_file() else {}
        signed.write_text(json.dumps({key: kept[key] for key in heard if key in kept} | {key: read["signature"] for key, read in reads.items()}))
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


def floored(watcher: Watcher, lines: list[str], reset: datetime, refusal: str) -> list[str]:
    """The lines of a GitHub read ccx's rate-limit floor refused until reset: each refusal's WATCH line folded into one saying when the watch reads again, said once a reset, since every read before it is refused alike and github_lines reads none until then, the reads it owes kept for that read."""
    said = [line for line in lines if not line.startswith("WATCH ")]
    if watcher.floor != reset:
        said.append(f"WATCH ccx's GitHub rate-limit floor refused the watch's GitHub reads, so it reads none until {reset.isoformat()}, then each it owes: {refusal.removeprefix('WATCH ')}")
    watcher.floor = reset
    return said


def dangerous_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Read in one github.paths call the changed files of each heard PR whose head no read of its files saw, by state.unmarked, whether the repository's .claude/long-running.toml or its provider's dangerous_paths classes it, and put each whose head changes one on its staging test with a tester, by state.mark_dangerous, ahead of the stage_lines that draws his card on it; each tester added said as drive pr add says it. A PR registered before an install that brought this stage has no head read, so the first pass after it reads every open one."""
    data = state.load(watcher.name)
    keys = [key for key in state.unmarked(data) if key not in watcher.stale and hears(watcher, data, data["prs"][key]["order"])]
    if not keys:
        return []
    try:
        read = github.paths(keys)
    except github.GithubError as error:
        return read_failed(watcher, "changed files", error, moment)
    watcher.failures.pop("changed files", None)
    def dangerous_lines_write(data: dict):
        added = {key: tester for key, (head, paths) in read.items() if key in data["prs"] and (tester := state.mark_dangerous(data, key, head, paths))}
        return added, data

    added, data = state.write(watcher.name, dangerous_lines_write, budget=state.WATCH_WRITE_SECONDS)
    return [f"PR {key}: {line} {ids(data['prs'][key])}" for key, tester in added.items() for line in render.builder_lines(watcher.name, data, [tester])]


def reach_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Read once a drive the repository's reach map of each open PR the release reach put on its staging test, by cli.release_reach, and take each whose paths now reach no component a release deploys off it, by state.unreach, since cli.staging_reach counts no path on the repository's dev list, ahead of the stage_lines that redraws his card on it: a drive kept before that rule holds PRs reaching only code a caller runs from a checkout on a test no release could deploy; each lift and each job it stopped said, the drive marked once the read passed."""
    from drive import cli

    data = state.load(watcher.name)
    keys = [key for key, pr in data["prs"].items() if pr["state"] == "open" and pr["reached"]]
    if data.get("reaches_narrowed") or not keys:
        return []
    try:
        reach = cli.release_reach(watcher.name, data, keys)
    except (state.DriveError, github.GithubError) as error:
        return read_failed(watcher, "release reach", error, moment)
    watcher.failures.pop("release reach", None)
    def reach_lines_write(data: dict):
        lifted = {key: state.unreach(data, key) for key in keys if key in reach and not reach[key]["releases"] and data["prs"][key]["reached"]}
        data["reaches_narrowed"] = True
        return data, lifted

    data, lifted = state.write(watcher.name, reach_lines_write, budget=state.WATCH_WRITE_SECONDS)
    lines = []
    for key, stopped in lifted.items():
        ended = f", and {', '.join(stopped)} stopped" if stopped else ""
        lines.append(f"PR {key}: reaches no component a release deploys, only code its caller runs from a checkout, so it waits on no staging test{ended} {ids(data['prs'][key])}")
    return lines


def release_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Land at once each open PR the release reach alone holds from a production release, green, approved and changing nothing dangerous, by state.release_held, land.dangerous and the repository's [[dangerous]] classes, read in one github.paths call: its staging test part taken off and merged first, by state.land_for_release, so the lander queues it through the repository's merge queue on this same pass's stage_lines and the cut waits minutes on its landing rather than a skip-or-hold card, by his words state.RELEASE_HOLD_WORDS; a dangerous one stays on its test and his card asks to merge it; each landing and each job it stopped said."""
    data = state.load(watcher.name)
    keys = [key for key, pr in data["prs"].items() if pr["reached"] and key not in watcher.stale and state.release_held(data, key) and hears(watcher, data, pr["order"])]
    if not keys:
        return []
    try:
        read = github.paths(keys)
    except github.GithubError as error:
        return read_failed(watcher, "release holds", error, moment)
    watcher.failures.pop("release holds", None)

    def release_lines_write(data: dict) -> list[str]:
        lines = []
        for key, (_, paths) in read.items():
            if not data["prs"][key]["reached"] or not (orders := state.release_held(data, key)) or land.dangerous(data, paths) or state.dangerous(data, paths):
                continue
            stopped = state.land_for_release(data, key, orders)
            ended = f", and {', '.join(stopped)} stopped" if stopped else ""
            lines.append(f"PR {key}: {state.RELEASE_LANDS.format(key=key, order=', '.join(orders))}{ended} {ids(data['prs'][key])}")
        return lines

    return state.write(watcher.name, release_lines_write, budget=state.WATCH_WRITE_SECONDS)


def main_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Keep the tests each yasyf/* repository's main fails since a drive PR merged over a check main failed too, which the lander reads per check, never per test, each owned by the merge that broke it: once a heard merged PR's own run failed, each test its log names that main's last finished run before the merge passed, and no finished main run since has passed, is that PR's. A test another job already holds, by state.main_holder, as the first main fix claiming it or an earlier merge owns it, stays that job's, so no second builder is woken on it. Wake its builder to fix them forward, at once and again every 30 minutes while main fails one, and a main fix's builder only once its fix merged, 30 minutes after that merge, while main still fails the test. A test clears once the newest finished main run created since its merge passes it, read again each time the receiver hears a run complete on main."""
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

    def main_lines_write(data: dict) -> list[tuple[str, str, list[str]]]:
        for repo, tests in passed.items():
            for test in tests:
                data["mains"][repo].pop(test, None)
        for key, found in broke.items():
            data["prs"][key]["graded"] = True
            owned = data["mains"].setdefault(github.repo_of(key), {})
            for kept in owned.values():
                if kept["owner"] == key:
                    kept["woke"] = state.stamp(moment)
            owned |= {test: failure | {"owner": key, "job": data["prs"][key]["job"], "woke": None, "waking": None} for test, failure in found.items() if state.main_holder(data, owned.get(test)) is None}
        wakes = main_wakes(watcher, data, moment)
        for repo, owner, tests in wakes:
            for test in tests:
                data["mains"][repo][test]["waking"] = state.stamp(moment)
        return [(owner, main_fix(repo, owner, data["prs"][owner], data["mains"][repo], tests), [f"main:{repo}@{test}" for test in tests]) for repo, owner, tests in wakes]

    for owner, text, marks in state.write(watcher.name, main_lines_write, budget=state.WATCH_WRITE_SECONDS):
        stage.wake(watcher.name, owner, text, marks=marks, at=state.stamp(moment))
    return []


def broken(repo: str, pr: dict) -> dict[str, dict]:
    """The tests a merged PR broke on main: each its own run failed at its head that main did not already fail where that run merged its head, by land.base_failures, so a merge over a red main is never named for main's own reds, and that main's last finished run before the merge passed and no finished main run since has passed, with that run's link, its workflow and when the merge landed; none once its run passed, and none of a workflow whose last run before the merge says nothing of any test, by install.main_failures, since main may have failed them already."""
    if pr["checks"] != "fail":
        return {}
    failures = install.head_failures(repo, pr["head"])
    since = install.merged_at(repo, pr["merged_sha"]) if failures else ""
    found = {}
    for workflow, run in {failure["workflow"]: failure["run"] for failure in failures.values()}.items():
        if (before := install.main_failures(repo, workflow, f"<{since}")) is None:
            continue
        after = install.main_failures(repo, workflow, f">={since}")
        based = land.base_failures(repo, run)
        found |= {test: failure | {"since": since} for test, failure in failures.items() if failure["workflow"] == workflow and test not in before and test not in based and (after is None or test in after)}
    return found


def passing(repo: str, owned: dict[str, dict]) -> set[str]:
    """The owned tests main passes now: each the newest finished main run of its workflow created since its owner merged passed."""
    groups: dict[tuple[int, str], set[str]] = {}
    for test, kept in owned.items():
        groups.setdefault((kept["workflow"], kept["since"]), set()).add(test)
    return {test for (workflow, since), tests in groups.items() if (failing := install.main_failures(repo, workflow, f">={since}")) is not None for test in tests - set(failing)}


def main_wakes(watcher: Watcher, data: dict, moment: datetime) -> list[tuple[str, str, list[str]]]:
    """Each heard merged PR owing a wake for the tests it owns on its repository's main, with those tests: one owning a test whose builder was never woken for it, or last woken 30 minutes ago; a main fix still open, or a claim whose fix is not up yet, wakes nobody, since its own stage wakes its builder."""
    owners: dict[tuple[str, str], list[str]] = {}
    for repo, owned in data["mains"].items():
        for test, kept in owned.items():
            if kept["owner"] and data["prs"][kept["owner"]]["state"] == "merged":
                owners.setdefault((repo, kept["owner"]), []).append(test)
    return [
        (repo, owner, sorted(tests))
        for (repo, owner), tests in owners.items()
        if hears(watcher, data, data["prs"][owner]["order"])
        and any((not (woke := data["mains"][repo][test]["woke"]) or moment - state.parse_time(woke) >= stage.EVERY["red"]) and not stage.waking(data["mains"][repo][test]["waking"], moment) for test in tests)
    ]


def main_fix(repo: str, owner: str, pr: dict, owned: dict[str, dict], tests: list[str]) -> str:
    """The wake to the builder owning tests its repository's main fails: the merge that broke them, from its own run, or a main fix that merged with main still failing them."""
    runs = ", ".join(sorted({owned[test]["run"] for test in tests}))
    if pr["exempt"] == state.MAIN_FIX:
        return f"{owner}: {repo}'s main still fails {', '.join(tests)} though it merged to fix them, as {runs} failed them; fix them forward in a PR of your own from a fresh main, since no other builder is asked to"
    return f"{owner}: {repo}'s main fails {', '.join(tests)} since it merged, as its own run {runs} failed them; fix them forward in a PR of your own from a fresh main, since no other builder is asked to"


def refused_lines(watcher: Watcher, keys: list[str], moment: datetime) -> list[str]:
    """Record on every PR it queued GitHub's refusal of the merge Trunk's queue took for each try Trunk ejected one of keys from, by Trunk's queue records, which its landing event says; a run of failed reads of them is said."""
    try:
        outcomes = land.refusals(state.load(watcher.name), keys)
    except state.DriveError as error:
        return failed(watcher, "trunk", "Trunk queue read", error, moment)
    watcher.failures.pop("trunk", None)
    if outcomes:
        def refused(data: dict) -> None:
            land.refuse(watcher.name, data, outcomes)

        state.write(watcher.name, refused, budget=state.WATCH_WRITE_SECONDS)
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
    def pushed_lines_write(data: dict) -> list[str]:
        lines = []
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

    return state.write(watcher.name, pushed_lines_write, budget=state.WATCH_WRITE_SECONDS)


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
        def dropped_lines_write(data: dict):
            for key in gone:
                state.drop_pr(data, key)

        state.write(watcher.name, dropped_lines_write, budget=state.WATCH_WRITE_SECONDS)
    return lines


def bare(wait: str) -> str:
    return wait.split("?")[0]


def waits_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Drop each wait of a job or PR that ended, his card answered, the job finished, a job's wait on another job's change once that job registers a PR, by state.change_pr, its FREE line and its brief's Where: then naming the branch to cut its own from, by state.stacked_on, the release out by the watch's release live read or its PR closed unmerged, a staging:<target>/<env>@<commit> wait of a test whose freeze gave way to a staging release for production, by gave_way, the Buildkite build finished or stopped at an approve step by the watch's Buildkite read, a process on this Mac ended, a teammate's reply come, a job a job waits on returned BUILT, by built_bases, a builder slot on this Mac given to the earliest due builder waiting its turn, one per slot no builder holds, or a once-only pass of state.shaped ended it, each by the reason that pass handed by state.drop_waits, forgotten as this edit saves, and say once, as its last wait ends, that nothing holds it, saying nothing of a job the watch starts itself: one start_jobs starts, and a codex job or a Sprite job no worker took, which the watch launches by drive job launch once busy lets it, or holds waiting its turn on sprite-slot while place.admission refuses it a Sprite lane, so no session is woken to run the launch; holding back the job's ORPHAN for 30 minutes, but for a builder's READY or PUSHED whose PRs all merged, which the watch finishes next in the same pass, or else says once as an ORPHAN naming who finishes it; a builder given its slot waits on nothing with no agent yet, its slot stamped with when its turn came, and holds that slot until its agent starts, and one moving here from its Sprite, or taking over here from its agent on this Mac, keeps its other waits, its slot stamped, while drive job launch <j> --local, run by start_verb, moves it, a refusal of that launch reaching the watch's lines as its relaunch_refused event; a Sprite job busy reads another drive verb acting on takes its turn on a pass after that verb ends. The cards a job asked him on hold it too, so it keeps its waits until he answers them. A codex job whose process ended holds the text exited_text writes, which carry_held's launch of its next codex-ask run carries, so its FREE line asks nobody to launch it; a job holding texts that carry_held launches, by carrier's drive job launch, names no brief and says the watch launches it and to start no agent for it, so no session starts a second worker beside that launch, while one whose worker carry_held resumes says it carries them. A release wait is a wake the job set, freed by out_because once every cluster of its service, or the one env it names, serves the PR's merge, and never comes back. A job waiting its turn for a Sprite lane on sprite-slot is freed, earliest due first after a priority order's, by place.sprite_turns, one for each lane under this Mac's Sprite cap no drive here runs, its turn stamped so that lane counts as held while its launch starts it."""
    data = state.load(watcher.name)
    out, lines = replies(watcher, data, moment)
    out |= shipped(watcher, data)
    bases, failed = built_bases(watcher, data, moment)
    lines += failed
    gave, unread = gave_way(watcher, data, moment)
    lines += unread
    if gave:
        data = state.load(watcher.name)
    turns, held = place.turns(data, moment)
    turned = {job_id for job_id in turns if heard_job(watcher, data, data["jobs"][job_id]) and not (place.moves_here(data["jobs"][job_id]) and busy(watcher, job_id, moment))}
    sprite_turns, running = place.sprite_turns(data, moment)
    sprite_turned = {job_id for job_id in sprite_turns if heard_job(watcher, data, data["jobs"][job_id])}
    jobs = [job for job in data["jobs"].values() if heard_job(watcher, data, job) and not view.open_cards(job)]
    opened = {wait: key for job in jobs for wait in job["waits"] if wait in data["jobs"] and (key := state.change_pr(data, wait))}
    owners = [*jobs, *(pr for pr in data["prs"].values() if hears(watcher, data, pr["order"]))]
    dropped = any(heard_job(watcher, data, job := data["jobs"][job_id]) and not view.open_cards(job) for job_id in data["dropped_waits"])
    if not out and not bases and not opened and not turned and not sprite_turned and not dropped and all(view.holding(data, owner["waits"], watcher.cards) == owner["waits"] for owner in owners):
        return lines
    changed = {wait: f"{wait} registered {key}" for wait, key in opened.items()} | out
    slot = {state.SLOT_WAIT: f"its turn came as a builder slot opened on this Mac, {held} of its {place.LOCAL_BUILDERS} held"}
    sprite = {state.SPRITE_WAIT: f"its turn came as a Sprite lane opened under this Mac's cap, {running} of its {place.caps()['sprite']} running"}
    def waits_lines_write(data: dict) -> tuple[list[str], list[str], list[str], list[str]]:
        freed, moves, rebriefs, launches = [], [], [], []
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
            sprite_turn = job_id in sprite_turned and job["state"] == "waiting" and state.SPRITE_WAIT in job["waits"]
            waited = job["waits"]
            ended = released(data, job, job_id, watcher.cards, changed | bases | data["dropped_waits"].pop(job_id, {}) | (slot if turn else {}) | (sprite if sprite_turn else {}))
            if sprite_turn and state.SPRITE_WAIT not in job["waits"]:
                job["sprite_turn"] = state.stamp(moment)
            for wait in waited:
                if wait in bases:
                    state.hold_text(job, BUILT_BASE.format(built=bases[wait]), state.WATCH_SENDER)
            if stacks := [opened[wait] for wait in waited if wait in opened and wait not in job["waits"]]:
                job["stacks_on"] = list(dict.fromkeys([*job["stacks_on"], *stacks]))
            exited = [wait for wait in waited if state.PID_WAIT.fullmatch(wait) and wait not in job["waits"]] if job["kind"] == "codex" else []
            if exited:
                state.hold_text(job, exited_text(watcher.name, job_id, exited), state.WATCH_SENDER)
            if ended and job["state"] not in state.FINISHED_JOB_STATES and not (watch_proves(data, job_id) or at_merge(data, job_id)):
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
                    if job["kind"] == "codex" or (job.get("where") == "remote" and not job.get("dispatch") and not starts_itself(job))
                    else ""
                )
                unstarted = brief_first(job)
                stacked = f", stacked {on}" if (on := state.stacked_on(data, job)) else ""
                if stacked and job.get("brief") and job.get("brief_flags") and not job.get("agent") and not job.get("dispatch"):
                    rebriefs.append(job_id)
                if job["undelivered"] and (command := carrier(job_id, job, data, watcher.cards)):
                    his = ", his answers among them" if any(held["sender"] == state.HIM for held in job["undelivered"]) else ""
                    carried = f", and the watch now launches it with the texts it holds{his}: start no agent for it" if command[1] == "launch" else f", and the watch now carries it the texts it holds{his}"
                    unlaunched, unstarted = ", and the watch now starts its next codex-ask run carrying its last report" if exited else carried, ""
                elif unlaunched and not busy(watcher, job_id, moment):
                    if job["kind"] != "codex" and place.admission(data, job_id, "sprite", moment):
                        place.queue_sprite(job, moment)
                    else:
                        launches.append(job_id)
                    continue
                elif starts_itself(job):
                    continue
                freed.append(f"FREE {job_id} {job['order']}: {ended}, nothing else holds it{built}{stacked}{unlaunched}{unstarted}")
        for key, pr in data["prs"].items():
            if hears(watcher, data, pr["order"]) and (ended := released(data, pr, pr["job"], watcher.cards, out)) and pr["state"] == "open":
                freed.append(f"FREE {key} {pr['job']} {pr['order']}: {ended}, nothing else holds it")
        return freed, moves, rebriefs, launches

    freed, moves, rebriefs, launches = state.write(watcher.name, waits_lines_write, budget=state.WATCH_WRITE_SECONDS)
    lines += freed
    for job_id in moves:
        start_verb(watcher, job_id, ["job", "launch", job_id, "--local"], moment)
    lines += [refused for job_id in rebriefs if (refused := stack_brief(watcher.name, job_id))]
    for job_id in launches:
        start_verb(watcher, job_id, ["job", "launch", job_id], moment)
    return lines


def stack_brief(name: str, job_id: str) -> str | None:
    """Write again the brief drive job brief wrote for a job freed with the branch to stack on, which its Where: then names, so the agent it starts and any launch reading the brief file cut their branch there; None once written, else why not and what to run."""
    from drive import cli

    try:
        cli.write_brief(name, job_id, {})
    except cli.VERB_ERRORS as error:
        return f"{job_id}'s brief does not name the branch to stack on: {error}; brief it with drive job brief {job_id} before its agent starts"
    return None


def built_bases(watcher: Watcher, data: dict, moment: datetime) -> tuple[dict[str, str], list[str]]:
    """Each job a live job the watch hears waits on that returned BUILT on a branch, with each branch it built on and that branch's head, which frees the waiting job, never a PR, so the next change of a chain starts on the built branch while his card holds the built job's PR, by his words "get everything built and tested on sandbox/staging and ready to merge": read from GitHub in one call a repository, a branch in the job's recorded repo or else the one remote lanes clone, a branch that repo does not hold named alone, and a failed read said as one is and owed again next pass."""
    built = {
        wait: data["jobs"][wait]
        for job in data["jobs"].values()
        if job["state"] not in state.FINISHED_JOB_STATES and heard_job(watcher, data, job) and not view.open_cards(job)
        for wait in job["waits"]
        if wait in data["jobs"] and data["jobs"][wait]["state"] == "built" and data["jobs"][wait]["branch"]
    }
    if not built:
        return {}, []
    try:
        home = github.checkout_repo(data["roles"]["coordinator"]["cwd"]) if any(not job.get("repo") for job in built.values()) else None
        branches = {wait: [(job.get("repo") or home, branch) for branch in job["branch"].split(", ")] for wait, job in built.items()}
        heads = github.branch_heads(sorted({head for both in branches.values() for head in both}))
    except github.GithubError as error:
        return {}, read_failed(watcher, "built branches", error, moment)
    watcher.failures.pop("built branches", None)
    return {
        wait: f"{wait} built on " + ", ".join(f"{branch} at {heads[repo, branch][:12]}" if heads[repo, branch] else f"{branch}, which {repo} does not hold" for repo, branch in both)
        for wait, both in branches.items()
    }, []


def gave_way(watcher: Watcher, data: dict, moment: datetime) -> tuple[bool, list[str]]:
    """Tell each unfinished job the watch hears that a freeze it holds on staging gave way to a staging release for production, by his pick 18c2fda, "Every staging freeze … gives way to a staging release started for production; its thread says so", whose option says the drive tells each test holding it to rerun: one read of the release tool's freezes, by brief.freezes, each READ_EVERY while such a job holds or rides a staging slot's cut, as every test on staging does, read once per freeze and release, kept on the job's gave_way, never the job that started that release or waits on its build already. The text gave_way_text writes is held on the job, which a running worker gets at once and a waiting one once its waits end; each staging:<target>/<env>@<commit> wait it holds goes to state.drop_waits, since staging serves that release now, so waits_lines ends it once his cards ask the job nothing; and the lease of the cut it holds or rides starts again and holds, by state.slot_turns, while a pass of that cut waits on that release's build, so no other test takes staging while the release runs and the test reruns in its own lease once it ends. Whether it kept any, and the lines of a read that failed, said as a failed read is and tried again a minute on."""
    holders = {f"{watcher.name}/{job_id}": job_id for job_id, job in data["jobs"].items() if job["state"] not in state.FINISHED_JOB_STATES and heard_job(watcher, data, job)}
    leased = {job_id for slot in data["slots"].values() for job_id in state.slot_members(slot)}
    if not leased & set(holders.values()) or watcher.freezes_at and moment - watcher.freezes_at < READ_EVERY:
        return False, []
    watcher.freezes_at = moment
    try:
        freezes = brief.freezes(watcher.name, data)
    except (state.DriveError, reads.ReadError) as error:
        return False, failed(watcher, "release freezes", "release freezes read", error, moment)
    watcher.failures.pop("release freezes", None)
    told = [
        (job_id, key, freeze, notice)
        for each in freezes
        for freeze in [each["freeze"]]
        for notice in freeze.get("notices", [])
        if notice["kind"] == "bypassed" and notice.get("forProduction")
        for holder in freeze.get("holders", [])
        if (job_id := holders.get(holder))
        and (key := f"{freeze['id']}/{notice['id']}") not in (job := data["jobs"][job_id])["gave_way"]
        and notice["url"] not in [job["release_build"], *job["waits"]]
    ]
    if not told:
        return False, []

    def gave(saved: dict) -> None:
        for job_id, key, freeze, notice in told:
            job = saved["jobs"][job_id]
            job["gave_way"].append(key)
            held = [slot for slot in saved["slots"].values() if job_id in state.slot_members(slot)]
            for slot in held:
                slot["holder"] = state.leased(slot["holder"], moment, slot["holder"]["riders"]) | {"released": slot["holder"]["released"], "gave_way_to": notice["url"]}
            state.hold_text(job, gave_way_text(freeze, notice, bool(held)), state.WATCH_SENDER)
            reason = f"freeze {freeze['id']} it holds gave way to the staging release for production {notice['url']}"
            state.drop_waits(saved, job_id, {wait: reason for wait in job["waits"] if state.STAGING_WAIT.fullmatch(wait)})

    state.write(watcher.name, gave, budget=state.WATCH_WRITE_SECONDS)
    return True, []


def gave_way_text(freeze: dict, notice: dict, leased: bool) -> str:
    """What a staging test holding a freeze that gave way does, decided under his ruling 43aa665, "don't ask me every little thing ask my high level preferences": its cases count for nothing, it waits for that release, then reruns every case, in its own lease, which gave_way started again, or, with no lease left, by claiming the slot again once it lifts its freeze and puts back each stack the release left on its cut, so no queued test waits behind a frozen staging."""
    after = (
        "Your staging slot's lease started again at the give-way and holds while you wait on that build, so then do the steps of your GO, or of your RIDE on another pass's cut, again from the cut on and run every case."
        if leased
        else "Then put back, by the restore step of the landing page's staging tests, only each stack you deployed that `<ci> release live` still shows serving your cut,"
        f" lift the freeze with `<ci> release unfreeze {freeze['id']}`, and claim the staging slot again with `drive slot claim`, whose GO runs every case again."
    )
    return (
        f"Release freeze {freeze['id']}, which names you its holder on staging, gave way at `{notice['at']}` to {notice['starter']}'s staging release for production"
        f" of {', '.join(notice.get('picks', []))}, {notice['url']}, which deploys over staging while the freeze stands, so your staging test there is cut short"
        f" and each case it ran counts as not exercised. Reply in your staging claim's thread that the test was cut short and reruns, and return `WAITING {notice['url']}`,"
        f" and again on a wake that finds that build stopped at an approve step, until it passed, failed or was canceled. {after}"
    )


def exited_text(name: str, job_id: str, exited: list[str]) -> str:
    """The text a codex job holds once a process it waited on ended, which carry_held's launch of its next codex-ask run carries under From the watch since:, since that run reads only its brief: which process ended and the job's last report."""
    ended = f"The process on this Mac {job_id} waited on ended, crashed or finished: {', '.join(wait.partition('@')[0] for wait in exited)}."
    report = state.report_text(name, job_id, every=False).strip()
    return f"{ended} Your last report:\n{report}" if report else ended


def build_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Read from the CI, by the repository's build provider, for waits_lines, status_lines, release_end_lines and release_card_lines, each build a wait the watch follows names, a part of an order it hears follows, a job it hears keeps as the release it started and still waits on, by awaits_release, or one of his release approve cards still asks about: once as it is first held, then again only once a delivery of the CI's to the receiver's build route names it, or on every pass while the CI never delivered to the receiver, by receiver.buildkite_silent. One read_builds call reads them all, run in the drive's checkout as orca.checkout moves it to its default branch's head, and each answer is kept on the watcher as builds while the build is held, None for a build the CI answers none for; a read that fails is said as a failed read is and owed again next pass."""
    data = state.load(watcher.name)
    releases = {job["release_build"] for job in data["jobs"].values() if awaits_release(job) and heard_job(watcher, data, job)}
    releases |= {entry["build"] for job in data["jobs"].values() if heard_job(watcher, data, job) for entry in job["release_cards"].values() if entry["result"] is None}
    held = sorted({wait for wait in followed(watcher, data) if providers.of(data).build.fullmatch(wait)} | state.followed_builds(heard_orders(watcher, data)) | releases)
    watcher.builds = {link: read for link, read in watcher.builds.items() if link in held}
    watcher.builds_heard &= set(held)
    silent = receiver.builds_silent()
    if not (due := [link for link in held if silent or link not in watcher.builds or link in watcher.builds_heard]):
        return []
    try:
        read = read_builds(Path(data["roles"]["coordinator"]["cwd"]), orca.checkout(watcher.name, data["roles"]["coordinator"]["cwd"], wait=0), due)
    except (state.DriveError, reads.ReadError, json.JSONDecodeError) as error:
        return read_failed(watcher, "buildkite builds", error, moment)
    watcher.failures.pop("buildkite builds", None)
    watcher.builds |= read
    watcher.builds_heard -= set(due)
    return []


def read_builds(clone: Path, checkout: Path, links: list[str]) -> dict[str, dict | None]:
    """Each build by its link, as the repository's BuildProvider reads it in the checkout with one read: its BUILD_FIELDS, None for a build its CI answers none for, and none for a link its build_url does not match."""
    build = providers.at(clone, checkout).build
    return build.builds([link for link in links if build.build_url and build.build_url.fullmatch(link)], BUILD_FIELDS)


def release_end_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Tell each job the watch hears that follows the release it started and still waits on it, by awaits_release, and the coordinator sessions acting on it and holding its order, by state.job_lane and state.holding_lane, in one cci record the moment the watch's newest Buildkite read of its release build, which build_lines makes, finds it ended, or stopped at an approve step RELEASE_BLOCKED_AFTER, by release_told, so no session polls its release and none goes unheard. The state each read finds is kept on the job as its release_heard with the time the watch first read it and the seq of the record that told it, so each build's outcome is told once whichever watch or install reads it again; a tell that fails is said as a failed read is and tried again next pass."""
    data = state.load(watcher.name)
    lines = []
    for job_id, job in data["jobs"].items():
        if not (awaits_release(job) and heard_job(watcher, data, job) and (read := watcher.builds.get(job["release_build"]))):
            continue
        heard = release_heard(job, read, moment)
        if heard != job["release_heard"]:
            def release_end_lines_write(kept: dict):
                kept["jobs"][job_id]["release_heard"] = heard

            state.write(watcher.name, release_end_lines_write, budget=state.WATCH_WRITE_SECONDS)
        if heard["told"] or not (read["state"] in FOLLOW_ENDS or read["state"] == "BLOCKED" and moment - state.parse_time(heard["since"]) >= RELEASE_BLOCKED_AFTER):
            continue
        source = f"release {job['release_build']}"
        try:
            release_told(watcher, job_id, read, moment)
        except (reads.ReadError, providers.ProviderError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
            lines += failed(watcher, source, f"tell {job_id} its release {job['release_build']} reads {read['state']}", error, moment)
            continue
        watcher.failures.pop(source, None)
    return lines


def awaits_release(job: dict) -> bool:
    """Whether a job still waits on the release build it keeps: it follows its release, by state.follows_release, and its waits name that build or nothing at all, so a job that moved on to other waits, such as a staging slot after its release passed, is never told of a build it no longer waits on."""
    return state.follows_release(job) and (not job["waits"] or job["release_build"] in job["waits"])


def release_heard(job: dict, read: dict, moment: datetime) -> dict:
    """What the watch keeps of its read of a job's release build: the job's kept release_heard while it names the same build and state, else that build and state, first read now and told by no record."""
    kept = job["release_heard"]
    if kept and kept["build"] == job["release_build"] and kept["state"] == read["state"]:
        return kept
    return {"build": job["release_build"], "state": read["state"], "since": state.stamp(moment), "told": None}


def release_told(watcher: Watcher, job_id: str, read: dict, moment: datetime) -> None:
    """Post one handoff record telling how a job's release build reads to the job, held on it by state.hold_addressed for its worker as any held text, and to the coordinator sessions acting on it and holding its order, keyed by the build and its state so a repost returns the stored record, and keep its seq as the job's release_heard told. The repository's release tool, by brief.release_record, names the release and the build it runs as: a build other than the one the job keeps, such as the release build a launch build triggered and passed on, becomes the job's release build, and its wait on the old one a wait on it, in place of the post, which the next pass reads; a status the release provider reads nothing of is a failed read, never a post, so a launch build is never told as the release."""
    data = state.load(watcher.name)
    link = data["jobs"][job_id]["release_build"]
    if (record := brief.release_record(link, data)) is None:
        raise reads.ReadError(f"the repository's release provider read nothing of {link}")
    if (moved := record.get("build")) and moved != link:
        def release_told_write2(kept: dict):
            moving = kept["jobs"][job_id]
            moving.update(release_build=moved, release_heard=None, waits=[moved if wait == link else wait for wait in moving["waits"]])

        state.write(watcher.name, release_told_write2, patient=True)
        print(f"{state.stamp(moment)} {job_id}'s release build {link} reads {read['state']} and started {moved}, which {job_id} follows now", flush=True)
        return
    text, body = release_words(data, job_id, link, read, record, moment)
    def release_told_write(kept: dict, seq: str):
        kept["jobs"][job_id]["release_heard"]["told"] = seq

    release_post(watcher.name, data, job_id, text, body, f"release-{link}-{read['state']}", release_told_write)


def release_post(name: str, data: dict, job_id: str, text: str, body: str, record_id: str, kept: Callable[[dict, str], None] = lambda data, seq: None, topic: str = "release") -> str:
    """Post one handoff record about a job's release, or another fact the drive holds for it such as an alert in its staging env, to the job, held on it by state.hold_addressed for its worker as any held text, and to the coordinator sessions acting on it and holding its order, under the record id so a repost returns the stored record, keeping in the same write whatever kept writes with its seq; the seq."""
    job = data["jobs"][job_id]
    to = list(dict.fromkeys([job_id, state.job_lane(data, job), state.holding_lane(data, data["orders"][job["order"]])]))
    argv = ["cci", "post", "--drive", name, "--lane", LANE, "--kind", "handoff", *(arg for lane in to for arg in ("--to", f"{lane}@{name}")), "--topic", f"{topic}-{job_id}"]
    argv += ["--id", record_id, "--json", "--path", "-", "--text", reads.withheld(text)]
    seq = str(json.loads(reads.run(argv, Path(data["roles"]["coordinator"]["cwd"]), reads.posting(), stdin=reads.withheld(body)))["seq"])
    def release_post_write(saved: dict):
        kept(saved, seq)
        state.hold_addressed(saved, {"kind": "handoff", "text": text, "body": body, "to": [f"{lane}@{name}" for lane in to]}, LANE, seq, None)

    state.write(name, release_post_write, patient=True)
    return seq


def env_alert_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Hand each new alert in a staging env's alert channel, as drive channels --env-alerts names it, to each job testing on that env, the holder of its drive slot and a job its slot's hold is for, as one handoff record with the alert's link and words, keyed by env, alert and job so none is handed twice, by his words "if a lane is doing staging tests it should automatically get the things posted in alerts-staging channel sent to it": the slot decides who gets it, never a judgment. Each env's channel is read at most once every ENV_ALERT_EVERY, from the newest alert it read; a read or post that fails goes again next read, said from its third failure in a row."""
    if watcher.project:
        return []
    data = state.load(watcher.name)
    lines = []
    for env, watched in data["slack"]["env_alerts"].items():
        if moment - watcher.polled.get(f"alerts {env}", moment - ENV_ALERT_EVERY) < ENV_ALERT_EVERY:
            continue
        watcher.polled[f"alerts {env}"] = moment
        try:
            read = slack.cc_slack("history", "--channel", watched["channel"], "--limit", str(ENV_ALERT_READ), "--json")["messages"]
        except slack.SlackError as error:
            lines += failed(watcher, f"alerts {env}", f"read of {env}'s alert channel {watched['channel']}", error, moment)
            continue
        watcher.failures.pop(f"alerts {env}", None)
        fresh = sorted((message for message in read if float(message["ts"]) > float(watched["seen"]) and not message.get("from_claude") and not message.get("from_user")), key=lambda message: float(message["ts"]))
        if not fresh:
            continue
        slot = data["slots"].get(env) or {}
        testing = [job for job in dict.fromkeys([(slot.get("holder") or {}).get("job"), (slot.get("hold") or {}).get("for")]) if job in data["jobs"] and data["jobs"][job]["state"] not in state.FINISHED_JOB_STATES]
        try:
            for message in fresh:
                for job_id in testing:
                    text = f"ALERT in {env}, which you are testing on: {message['user_name']}: {message['text'].splitlines()[0][:200]} {message['permalink']}"
                    release_post(watcher.name, state.load(watcher.name), job_id, text, message["text"], f"alert-{env}-{message['ts']}-{job_id}", topic="alert")
                lines.append(f"WATCH alert in {env} handed to {', '.join(testing) or 'no job, none tests on it'}: {message['permalink']}")
        except reads.ReadError as error:
            lines += failed(watcher, f"alerts {env}", f"hand-off of an alert in {env}", error, moment)
            continue

        def env_alert_write(saved: dict, env=env, newest=fresh[-1]["ts"]):
            saved["slack"]["env_alerts"][env]["seen"] = newest

        state.write(watcher.name, env_alert_write, patient=True)
    return lines


def release_card_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Put each approve step of a release build a job the watch hears holds to him as one card he taps, by his words "in the future if it's blocked on needing an approve for me it should have posted a card for me to tap": the first CI read that finds a build a job holds, by release_approve_builds, blocked at an approve step keeps one card for that build and step, by release_card_kept, once across every job, while the release provider names an approve_command for his Approve to run. Each card kept is then drawn, closed or withdrawn by release_card_settled; a read, post or close that fails is said as a failed read is and tried again next pass."""
    data = state.load(watcher.name)
    lines = []
    if providers.of(data).release.approve_command:
        carded = {key for job in data["jobs"].values() for key in job["release_cards"]}
        for job_id, job in data["jobs"].items():
            if job["state"] in state.FINISHED_JOB_STATES or not heard_job(watcher, data, job):
                continue
            for link in release_approve_builds(data, job):
                for step in blocked_steps(watcher.builds.get(link) or NO_BLOCKS):
                    if f"{link} {step}" in carded:
                        continue
                    try:
                        release_card_kept(watcher.name, job_id, link, step, moment)
                    except (reads.ReadError, providers.ProviderError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
                        lines += failed(watcher, f"release card {link}", f"read of release {link} for his approve card", error, moment)
                        continue
                    carded.add(f"{link} {step}")
    data = state.load(watcher.name)
    for job_id, job in data["jobs"].items():
        if not heard_job(watcher, data, job):
            continue
        for key in [key for key, entry in job["release_cards"].items() if entry["shown"] is None]:
            source = f"release card {key}"
            try:
                lines += release_card_settled(watcher, job_id, key)
            except (slack.SlackError, state.DriveError) as error:
                lines += failed(watcher, source, f"his approve card on {key}", error, moment)
                continue
            watcher.failures.pop(source, None)
    return lines


def release_approve_builds(data: dict, job: dict) -> list[str]:
    """Each Buildkite build a job holds that can stop at an approve step for him: the release build it keeps while it still waits on it, by awaits_release, and each build its waits name."""
    return list(dict.fromkeys([*([job["release_build"]] if awaits_release(job) else []), *(wait for wait in job["waits"] if providers.of(data).build.fullmatch(wait))]))


def release_card_kept(name: str, job_id: str, link: str, step: str, moment: datetime) -> None:
    """Keep on the job one approve card for the build's approve step under a waits id minted for it, before anything posts, so a retry draws the same card: the release the release provider's status names, by brief.release_record, and the migrations, replaces and deletes that record lists, which his card shows as what Approve covers. A build its status reads as no release, or as a launch build passing on to another, which the job follows once release_told moves it, keeps none."""
    data = state.load(name)
    record = brief.release_record(link, data)
    if record is None or record.get("build") not in (None, link):
        return
    covers = {kind: record[kind] for kind in RELEASE_COVERS if kind in record} or None
    entry = {"build": link, "step": step, "release": record.get("name") or link, "covers": covers, "waits": state.new_waits(name, job_id), "url": None, "asked": state.stamp(moment), "tap": None, "result": None, "shown": None}
    def release_card_kept_write(data: dict):
        data["jobs"][job_id]["release_cards"][f"{link} {step}"] = entry

    state.write(name, release_card_kept_write, budget=state.WATCH_WRITE_SECONDS)


def release_card_settled(watcher: Watcher, job_id: str, key: str) -> list[str]:
    """Move his approve card on one build's approve step one step on: post it while the build stands there and none is posted, at the root of the channel of its job's order's seat, a priority's or a project's, else the drive's own channel, by release_card_channel; withdraw it, its words saying how the build reads, once a read finds the build past that step with no tap, and keep that as its result; show on it the result his tap gave, approved, held or failed with the release tool's line, in place of his tap, once card.release_answered keeps one. A build Buildkite answers none for counts as past its step."""
    from drive import superseded, talk

    data = state.load(watcher.name)
    job, entry = data["jobs"][job_id], data["jobs"][job_id]["release_cards"][key]
    if entry["tap"]:
        if entry["result"] is None:
            return []
        superseded.answer_card(entry["url"], entry["result"], entry["build"], settled=True, replaces=entry["tap"]["action_ts"])
        def release_card_shown_write(data: dict):
            data["jobs"][job_id]["release_cards"][key]["shown"] = entry["result"]

        state.write(watcher.name, release_card_shown_write, patient=True)
        return [f"CARD {entry['url']} closed in place: {entry['result']}"]
    if entry["build"] not in watcher.builds:
        return []
    read = watcher.builds[entry["build"]]
    if read and entry["step"] in blocked_steps(read):
        if entry["url"]:
            return []
        channel = release_card_channel(watcher.name, data, job)
        post = {"card": release_card(data, entry), "request_key": f"{entry['waits'].rpartition('-')[2]}-{watcher.name}.release.{job_id}"}
        url = talk.posted(watcher.name, post | ({"channel_id": channel} if channel != slack.dm() else {}))["permalink"]
        def release_card_posted_write(data: dict):
            data["jobs"][job_id]["release_cards"][key]["url"] = url

        state.write(watcher.name, release_card_posted_write, patient=True)
        return [f"CARD {url} asks his Approve on release {entry['release']} {entry['build']} at its approve step \"{entry['step']}\""]
    result = f"Withdrawn: the build moved past \"{entry['step']}\" with no tap here, and reads {read['state'].lower() if read else 'as no build'}"
    if entry["url"]:
        superseded.answer_card(entry["url"], result, entry["build"], settled=False)
    def release_card_withdrawn_write(data: dict):
        data["jobs"][job_id]["release_cards"][key].update(result=result, shown=result)

    state.write(watcher.name, release_card_withdrawn_write, patient=True)
    return [f"CARD {entry['url']} withdrawn: {result}"] if entry["url"] else []


def release_card_channel(name: str, data: dict, job: dict) -> str:
    """The channel his approve card goes in at its root: the channel of the seat of the job's order, a priority's or a project's, by state.channel_seat_of, made on demand, else the drive's own, else his DM."""
    from drive import talk

    seat = state.channel_seat_of(data, data["orders"][job["order"]])
    return talk.channel_for(name, data, seat) if seat else slack.project_channel(data, None)


def release_card(data: dict, entry: dict) -> dict:
    """His approve card on a build's approve step: what Approve applies, each migration, replace and delete the release provider's status lists, or that it lists none of them, its show_command, and the commands Approve and Hold run, its approve_command and hold_command with the release filled; Hold with no hold_command records his hold and tells the job."""
    from drive.card import TAP_LABELS

    release = providers.of(data).release
    commands = {"approve": release.approve_command, "hold": release.hold_command, "show": release.show_command}
    filled = {verb: release.filled(command, release=entry["release"]) for verb, command in commands.items() if command}
    if entry["covers"] is None:
        covers = ["The release tool lists no migrations, replaces or deletes for it."]
    else:
        covers = ["*What Approve applies:*", *(f"- {label}: {', '.join(entry['covers'].get(kind) or []) or 'none'}" for kind, label in RELEASE_COVERS.items())]
    body = "\n".join([f"[Release {entry['release']}]({entry['build']}) stopped at its approve step \"{entry['step']}\".", "", *covers, *([f"`{filled['show']}` shows the whole release."] if "show" in filled else [])])
    hold = f"Runs `{filled['hold']}`." if "hold" in filled else "Leaves the build at its approve step and tells its job and coordinator you held it."
    return {
        "title": f"Approve release {entry['release']}?",
        "body": body,
        "options": list(TAP_LABELS),
        "recommended": TAP_LABELS[0],
        "hints": {TAP_LABELS[0]: f"Runs `{filled['approve']}` as you, and the build goes on past its approve step.", TAP_LABELS[1]: hold},
        "refs": [{"label": f"release {entry['release']}", "url": entry["build"]}],
        "waits": entry["waits"],
        "drive": data["name"],
        "meanwhile": RELEASE_MEANWHILE,
    }


def release_words(data: dict, job_id: str, link: str, read: dict, record: dict, moment: datetime) -> tuple[str, str]:
    """A release record's text and body: the job, its order, the release's name, its build and how it reads, the first failure the repository's release tool names, its step, its own cause line and its job's link, else the first step Buildkite read as not passing, or the approve step it stopped at and for how long; its body each failure on a line of its own and Buildkite's read."""
    job = data["jobs"][job_id]
    failures = record.get("failures") or [{"step": label.strip('"')} for label in unpassed_steps(read)]
    if read["state"] == "BLOCKED":
        how = f"stopped {minutes(moment - state.parse_time(job['release_heard']['since']))} min at its approve step {', '.join(approve_steps(read))}"
    elif read["state"] == "PASSED" or not failures:
        how = read["state"].lower()
    else:
        how = f"{read['state'].lower()} at {failure_said(failures[0])}"
    release = record.get("name")
    text = f"RELEASE {job_id} {job['order']}: release {f'{release} ' if release else ''}{link} {how}"
    body = "\n".join([*(failure_said(failure) for failure in failures if read["state"] != "PASSED"), build_said(link, read) or f"{link} reads {read['state']}"])
    return text, body


def failure_said(failure: dict) -> str:
    return " ".join(filter(None, [f'"{failure["step"]}"' + (":" if failure.get("said") else ""), failure.get("said"), failure.get("job")]))


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
    return [f'"{label}"' for label in blocked_steps(read)]


def blocked_steps(read: dict) -> list[str]:
    return [edge["node"]["label"] for edge in read["blocks"]["edges"]]


def unpassed_steps(read: dict) -> list[str]:
    return list(dict.fromkeys(f'"{edge["node"]["label"]}"' for edge in read["failed"]["edges"] if not edge["node"]["retried"]))


def status_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Keep his thread current on each Buildkite build a part of an open order the watch hears follows, by his pick "A live status message: one message in your thread, rewritten each minute with the running step, then an end line; one Buildkite read a minute": from the newest read build_lines holds, which Buildkite's deliveries to the receiver renew, one status message posted in the thread of his ask and rewritten whenever its words change, its running steps' minutes moving each pass, so no session's turn keeps it current and the follow makes no Buildkite read of its own; once the build ends, a report the handler the watch starts posts as the end line, which delivers the part. A failed post, write or start is said as a failed read is and tried again next pass, a start with the same report."""
    lines = []
    for order_id, order in heard_orders(watcher, state.load(watcher.name)).items():
        for part, held in order["parts"].items():
            if not (follow := held["follow"]) or follow["ended"] or follow["build"] not in watcher.builds:
                continue
            source = f"follow {order_id} {follow['build']}"
            try:
                status(watcher, order_id, order, part, follow, moment)
            except (slack.SlackError, reads.ReadError, state.DriveError) as error:
                lines += failed(watcher, source, f"follow of {follow['build']} for {order_id}", error, moment)
                continue
            watcher.failures.pop(source, None)
    return lines


def status(watcher: Watcher, order_id: str, order: dict, part: str, follow: dict, moment: datetime) -> None:
    """Post the status message of the build a part follows, or rewrite it when its words changed, and once the build ended mark the follow ended and post its end line in the thread of his ask once, by end_line, so a post the drive refuses is said as a failed read is and never tried again."""
    from drive import talk

    if (read := watcher.builds[follow["build"]]) is None:
        raise state.DriveError(f"Buildkite answers no build at {follow['build']}")
    words = status_words(read, moment)
    message = follow["message"] or talk.posted(watcher.name, {"text": words, "thread_ts": follow["thread"]})["permalink"]
    if follow["message"] and words != follow["words"]:
        talk.posted(watcher.name, {"edit": message, "text": words})
    kept = {"message": message, "words": words}
    def status_write(data: dict):
        data["orders"][order_id]["parts"][part]["follow"].update(kept)

    state.write(watcher.name, status_write, budget=state.WATCH_WRITE_SECONDS)
    if read["state"] in FOLLOW_ENDS:
        def status_write2(data: dict):
            data["orders"][order_id]["parts"][part]["follow"]["ended"] = state.stamp(moment)

        state.write(watcher.name, status_write2, budget=state.WATCH_WRITE_SECONDS)
        end_line(watcher, order_id, order, part, follow | kept, read)


def status_words(read: dict, moment: datetime) -> str:
    """A followed build's status message: its title linked to it, then each step running with its minutes, or the approve step it stopped at, or once it ended its state, minutes and the steps that did not pass, then how many of its steps are done, such as "Staging release plangent-macdiarmid: Watch the tenant smoke on staging, 4 min; 120 of 136 steps done"."""
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


def end_line(watcher: Watcher, order_id: str, order: dict, part: str, follow: dict, read: dict) -> str:
    """Post the end line of the build a part of the order follows in the thread of his ask, where the line delivers the part, through milestone: its status message's final words, over the facts of the build's end; milestone's result."""
    facts = [
        f"His order {order_id}: {order['text']}",
        f"His ask, the part the end line delivers: {part}",
        f"The build: {follow['build']}, {read['state']}, at commit {read['commit']}" + (f", its steps that did not pass {', '.join(unpassed)}" if (unpassed := unpassed_steps(read)) else ""),
        f"Its status message: {follow['message']}, now reading: {follow['words']}",
    ]
    record = {
        "kind": "report",
        "topic": f"follow-{order_id}",
        "id": f"follow-end-{order_id}-{follow['build']}",
        "attr": {"order": order_id, "part": f"{order_id}:{part}", "thread": follow["thread"]},
        "body": "\n".join(facts) + "\n",
    }
    return milestone(watcher, record, follow["words"])


def milestone(watcher: Watcher, record: dict, words: str) -> str:
    """Send a milestone's report from the watch's lane, addressed to no session, so it wakes none, its text `POST` and the words the watch wrote him and its body the facts behind them, then post it from the drive itself by talk.report_line, whose Sonnet call writes the post from those words under REPORT_RULES and places it by the record's thread and channel attrs, at his DM root for a record naming neither; its result, or, for a report the model skips, the rule it gave, which settles the milestone as a post does."""
    from drive import talk, tools

    made = record | {"text": f"POST {words}"}
    [seq] = tools.sent(watcher.name, LANE, [limits.shortened(made) or made])
    try:
        return talk.report_line(watcher.name, seq)
    except talk.Skipped as skipped:
        return f"SKIPPED report {seq}: {skipped}"


def build_line(watcher: Watcher, link: str) -> str:
    """A build wait as the watch's last Buildkite read found it."""
    if link not in watcher.builds:
        return f"{link}: the watch holds no Buildkite read of it, as its read failed or the build is not held yet"
    read = watcher.builds[link]
    return build_said(link, read) or f"{link} reads {read['state']} at the watch's last Buildkite read, which it makes again once a delivery of its CI's to the receiver names it"


def monitors(data: dict, order_id: str) -> dict[str, str]:
    """The link of each Datadog monitor recorded on the orders of an order's incident, by its id."""
    links = (link for other in state.feature_orders(data, order_id) for link in data["orders"][other]["alerts"])
    return {found[1]: link for link in links if (found := state.DATADOG_MONITOR.fullmatch(link))}


def followed(watcher: Watcher, data: dict) -> list[str]:
    """The waits of each live job or open PR whose order the watch hears."""
    return [wait for order_id, owner in release_owners(data) if hears(watcher, data, order_id) for wait in owner["waits"]]


def serving_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Read which clusters serve the merged home repository PR of each release wait the watch follows and the soak commit of each staging wait, for waits_lines, and of each component in the releases release_reach names for each fix PR of an open incident, for recovered_lines: one the release provider's live read, run in the drive's checkout as orca.checkout moves it, at most once every LIVE_EVERY, since it takes most of a minute, prints the commit each cluster of every service serves, production or not, a stack whose switch never passed serving its older build. Kept on the watcher as serving until the next read, each wait's clusters by whether each serves a commit carrying the PR's merge, by github.carrier's read of the checkout's own history, or, for a staging wait, the soak commit itself or a build carrying it by the same read, so a promotion of dev holding a merge frees a wait on that merge commit while a soak commit off dev's line still frees only at itself, a service no cluster runs reading none, each fix PR's waits as fix_waits, both None while the read fails, and as production the clusters it read that the release provider counts as production, by settled.prod."""
    from drive import cli

    data = state.load(watcher.name)
    merged = [
        wait
        for wait in followed(watcher, data)
        if (found := state.RELEASE_WAIT.fullmatch(wait)) and data["prs"][found["pr"]]["merged_sha"] and state.home_repo(data, github.repo_of(found["pr"]))
    ]
    soaks = {wait: found for wait in followed(watcher, data) if (found := state.STAGING_WAIT.fullmatch(wait))}
    fixes = sorted({key for keys in incident_fixes(watcher, data).values() for key in keys})
    if not merged and not soaks and not fixes and not cut_waiting(data):
        watcher.serving, watcher.fix_waits = {}, {}
        return []
    if watcher.live_at and moment - watcher.live_at < LIVE_EVERY:
        return []
    watcher.serving = watcher.fix_waits = None
    try:
        checkout, live = live_rows(watcher, data)
        reach = cli.release_reach(watcher.name, data, fixes) if fixes else {}
        fix_waits = {key: [f"release:{component}:{key}" for component in reach[key]["releases"]] for key in fixes if key in reach}
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
    """The drive's checkout, as orca.checkout moves it, and the rows of one the release provider's live read run there, a commit each cluster of every service serves, keeping them on the watcher as live for staging_line, and as staged the targets the staging provider's env runs for add_prover. The rows are this process's one copy of that read, by live_copy, which every project's watcher shares."""
    checkout = orca.checkout(watcher.name, data["roles"]["coordinator"]["cwd"], wait=0)
    live = live_copy(watcher.name, providers.of(data).release)
    watcher.live = live
    watcher.staged = {row["target"] for row in live if row["env"] == providers.of(data).staging.env}
    return checkout, live


LIVE_SHARED = True
LIVE_COPIES: dict[str, tuple[float, list[dict] | reads.ReadError]] = {}
LIVE_READING: dict[str, threading.Thread] = {}


def live_copy(name: str, release: providers.ReleaseProvider) -> list[dict]:
    """The rows of the drive's newest the release provider's live read read, which takes over a minute: a thread renews them once they are LIVE_EVERY old, so no pass of any project waits on the read, a failed or first read raising as a failed read does; with LIVE_SHARED off, as tests run, each call reads it at once."""
    if not LIVE_SHARED:
        return live_read(release)
    at, rows = LIVE_COPIES.get(name, (None, None))
    if (at is None or monotonic() - at >= LIVE_EVERY.total_seconds()) and not ((reading := LIVE_READING.get(name)) and reading.is_alive()):
        LIVE_READING[name] = threading.Thread(target=live_renewed, args=(name, release), name=f"release-live-{name}", daemon=True)
        LIVE_READING[name].start()
    if rows is None:
        raise reads.ReadError("the release live read: its first read runs in the background, so the watch reads its rows next pass")
    if isinstance(rows, reads.ReadError):
        raise rows
    return rows


def live_read(release: providers.ReleaseProvider) -> list[dict]:
    try:
        return release.live()
    except providers.ProviderError as error:
        raise reads.ReadError(str(error)) from None


def live_renewed(name: str, release: providers.ReleaseProvider) -> None:
    try:
        rows: list[dict] | reads.ReadError = live_read(release)
    except reads.ReadError as error:
        rows = error
    LIVE_COPIES[name] = (monotonic(), rows)


def incident_fixes(watcher: Watcher, data: dict) -> dict[str, list[str]]:
    """The fix PRs of each open incident the watch hears, by its head order, once no job on a fix part of its orders still works, no PR of one is open and each is a merged PR of the home repository."""
    fixes = {}
    for head in state.open_incidents(data, list(data["orders"])):
        if data["orders"][head]["joins"] or data["orders"][head]["recovered"] or not hears(watcher, data, head):
            continue
        jobs = {job_id: job for order_id in state.feature_orders(data, head) for job_id, job in state.part_jobs(data, order_id, state.INCIDENT_FIX)}
        prs = [key for key, pr in data["prs"].items() if pr["job"] in jobs and pr["state"] != "closed"]
        if (
            prs
            and all(data["prs"][key]["state"] == "merged" and state.home_repo(data, key.split("#")[0]) for key in prs)
            and all(job["state"] in state.FINISHED_JOB_STATES for job in jobs.values())
        ):
            fixes[head] = prs
    return fixes


def recovered_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Close each open incident the watch hears once its fix is live, every Datadog monitor recorded on it reads OK, one with no monitor on its fix alone, and each of its orders that builds something he uses keeps a proof from there, by brief.unseen, or else say so once by proof_owed: what is left of it moves to a plain order, and a POST report hands the communicator the Live line for its thread, whose handler closes each order of it. Its fix is live when incident_fixes names its PRs and the watch's last release live read, by serving_lines, has it in production for each component it touches, by in_production, one touching none live at its merge; a fix PR outside the home repository has no such read, so the coordinator closes its incident. The monitors of the incidents whose fix is live are read in one search."""
    if not watcher.fix_waits:
        return []
    data = state.load(watcher.name)
    fixes = incident_fixes(watcher, data)
    landed = {head: monitors(data, head) for head, keys in fixes.items() if all(key in watcher.fix_waits and all(in_production(watcher, wait) for wait in watcher.fix_waits[key]) for key in keys)}
    lines, ok = [], set()
    if ids := sorted({monitor for found in landed.values() for monitor in found}):
        argv = ["pup", "--no-agent", "monitors", "search", "--query", f"id:({' OR '.join(ids)})", "--per-page", str(len(ids)), "--jq", ".monitors | map({id, status})"]
        try:
            statuses = json.loads(reads.run(argv, orca.checkout(watcher.name, data["roles"]["coordinator"]["cwd"], wait=0)))
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
    """Whether a fix PR runs in production for one component, by his pick "Read each fix PR's production landing each pass, no latch": the watch's last release live read has the component on a cluster, and each of its clusters the release provider counts as production serves the merge, whatever staging, dev and sandbox run, as the release provider reads it."""
    clusters = watcher.serving.get(wait)
    return bool(clusters) and all(serves for env, serves in clusters.items() if env in watcher.production)


def proof_owed(watcher: Watcher, head: str, unseen: dict[str, str], fix: str, moment: datetime) -> list[str]:
    """Say once that an incident whose fix is live stays open until each order of it that builds something he uses keeps a recording or live post from there, naming the builder that proves it and builds nothing, whose DONE keeps that proof on each order of the incident, as state.take_result does."""
    def proof_owed_write(data: dict) -> bool:
        if data["orders"][head]["prove_said"]:
            return False
        data["orders"][head]["prove_said"] = state.stamp(moment)
        return True

    if not state.write(watcher.name, proof_owed_write, budget=state.WATCH_WRITE_SECONDS):
        return []
    surfaces = " and ".join(dict.fromkeys(unseen.values()))
    goal = f"prove {fix.removesuffix(' live in production')} live on {surfaces} with a recording of it"
    add = shlex.join(["drive", "job", "add", "builder", "--builds-nothing", "--order", head, "--part", state.INCIDENT_FIX, "--goal", goal])
    return [
        f"PROVE {head}: {fix}, but {', '.join(unseen)} builds what he uses on {surfaces}, so its Live line waits on a recording or live post"
        f" from there: add the builder that proves it, {add}, brief it with --native and --interactions for {surfaces} and start it; its DONE"
        f" keeps that recording as the proof of {', '.join(unseen)}, and the watch then closes {head}"
    ]


def page_owner(name: str, data: dict, title: str, why: str) -> str:
    """Page his own on-call user at high urgency with the page command the repository's provider names, run in the drive's checkout moved to its default branch's head, so the drive holds no paging credential and can page nobody else; the command's line naming the page."""
    orca.checkout(name, data["roles"]["coordinator"]["cwd"], wait=0)
    repo = providers.of(data).repo
    if not repo.page_owner:
        raise state.DriveError("this repository's providers name no command that pages him")
    return state.unrehearsed(name, lambda: repo.run(f"{repo.page_owner} --title {{title}} --description {{description}}", title=cut(title, 80), description=f"{why}\n{dashboard.link(name)}").strip(), "<page>")


def owner_page_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Page him once for each incident order the watch hears that opened since he was last heard, is open while he is not present, by state.absence, the presence the landing gate reads, and carries the cci record showing real users failing, by state.PAGE_RULE, so a failure only the drive's own callers or proofs hit pages nobody and one whose record lands later pages then; one that fails is said as a failed read is and tried again next pass."""
    data = state.load(watcher.name)
    if not state.absence(data, moment):
        return []
    heard = data["owner"]["heard"]
    owed = [
        key
        for key in state.open_incidents(data, list(data["orders"]))
        if data["orders"][key]["users"] and not data["orders"][key]["paged"] and (not heard or state.parse_time(data["orders"][key]["created"]) > state.parse_time(heard)) and hears(watcher, data, key)
    ]
    lines = []
    for key in owed:
        order = data["orders"][key]
        try:
            paged = page_owner(watcher.name, data, f"{key} {order['title'] or order['text']}", f"{render.order_line(data, key, order)}\nreal users failing: record {order['users']}")
        except (state.DriveError, reads.ReadError) as error:
            return lines + failed(watcher, "owner page", "page of his Datadog On-Call user", error, moment)
        watcher.failures.pop("owner page", None)
        def owner_page_lines_write(edited: dict):
            edited["orders"][key]["paged"] = state.stamp(moment)

        state.write(watcher.name, owner_page_lines_write, patient=True)
        lines.append(f"WATCH paged him for incident {key} while he is away: {paged}")
    return lines


def close_report_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Post in one cci batch each close report drive job prove, or the watch's DONE at a merge, keeps on an open order it finished, for the watch to post in the order's thread, or at his DM root for an order of his with none, and close it, by proved_lines, or, for an order whose reporters get its line in their own threads, for the report-post this read starts on it; each stays on its order until cci takes it, so a post that fails goes again next read, said from its third failure in a row, and a watch on a newer install posts it as well. One an older install kept on a drive tooling order a session opened, which posts him no line, goes nowhere: that order closes done on the proof it holds, as install.finish now closes one. A report the watch posts itself, by state.watch_posts_itself, stays on its order with its seq once cci takes it, by reported, for proved_lines to post."""
    data = state.load(watcher.name)
    closing = {order_id: order for order_id, order in heard_orders(watcher, data).items() if order["close_report"]}
    if unasked := [order_id for order_id, order in closing.items() if state.unasked_tooling(order)]:
        def close_report_lines_write(data: dict) -> None:
            for order_id in unasked:
                data["orders"][order_id].update(close_report=None, state="done", updated=state.stamp(moment))
                state.close_order_jobs(data, order_id, f"stopped: the watch closed its order {order_id} on its close report")

        state.write(watcher.name, close_report_lines_write, budget=state.WATCH_WRITE_SECONDS)
    owed = {order_id: order["close_report"] for order_id, order in closing.items() if order_id not in unasked and "seq" not in order["close_report"]}
    if not owed:
        return proved_lines(watcher, moment)
    try:
        seqs = reported(watcher, list(owed.values()))
    except state.DriveError as error:
        return failed(watcher, "close reports", "close report post", error, moment)
    watcher.failures.pop("close reports", None)
    lines = []
    for order_id, seq in zip(owed, seqs, strict=True):
        if state.watch_posts_itself(closing[order_id]):
            continue
        try:
            state.detach(watcher.name, ["report-post", seq], POSTS_LOG)
        except OSError as error:
            lines += failed(watcher, f"reporter {order_id}", f"post of the line {order_id}'s reporters get once its fix is live", error, moment)
    return lines + proved_lines(watcher, moment)


def proved_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Post the live line of each close report the watch sent that it posts itself, by talk.report_line, whose Sonnet call writes the post from the report's words, whose Proof: fact is the read drive job prove made, under REPORT_RULES and places it by the report's thread and channel attrs, then closes its order on it, so no handler session starts to post the words the report holds; a report already carried in a post only closes its order, so a watch that ended between the post and the close posts no second line, and one the model skips closes its order with no line, as the model's call. Then the order keeps the report no longer. A post that fails, or a report placed outside the drive's channels, leaves the report to the relay's report-post, the path every other report takes, which hands one outside the drive's channels to the communicator; a post whose order did not close, a step after the post failing included, keeps the report, so the next pass closes it posting nothing, said from its third failure in a row. Each outcome goes to the drive's posts log."""
    from drive import talk

    data = state.load(watcher.name)
    lines = []
    for order_id, order in heard_orders(watcher, data).items():
        if not (report := order["close_report"]) or "seq" not in report:
            continue
        seq, failed_post = report["seq"], False
        try:
            said = f"CARRIED {link}{talk.closed(watcher.name, order_id, seq)}" if (link := state.load(watcher.name)["carried"].get(seq)) else talk.report_line(watcher.name, seq)
            outcome = f"{order_id} live line posted by the drive: {said}"
        except talk.Skipped as skipped:
            outcome = f"{order_id} live line skipped: {skipped}{talk.closed(watcher.name, order_id, seq)}"
        except (state.DriveError, slack.SlackError) as error:
            outcome, failed_post = f"{order_id} live line left to the relay's report-post: {error}", True
        now = state.load(watcher.name)
        closes = now["orders"][order_id]["state"] in state.CLOSED_ORDER_STATES or failed_post and seq not in now["carried"]
        with open(state.drive_dir(watcher.name) / POSTS_LOG, "a") as log:
            log.write(f"{when(state.stamp(moment))} {outcome}\n")
        if not closes:
            lines += failed(watcher, f"proved close {order_id}", f"close of {order_id} on its live line", state.DriveError(outcome), moment)
            continue
        watcher.failures.pop(f"proved close {order_id}", None)
        def proved_lines_write(edited: dict):
            edited["orders"][order_id]["close_report"] = None

        state.write(watcher.name, proved_lines_write, budget=state.WATCH_WRITE_SECONDS)
    return lines


def reported(watcher: Watcher, records: list[dict]) -> list[str]:
    """Post the watch's reports in one cci batch, each addressed to the communicator, whose handlers post them, so no coordinator's or the meta session's channel carries one, and mark each order a report closes closed on its seq unless another report closes it already, so second_close refuses every later close of it, in the same save taking the close report an order kept off it once cci took it, but keeping one the watch posts itself, by state.watch_posts_itself, with its seq, so the relay starts no handler on it; their seqs."""
    from drive import tools

    seqs = tools.sent(watcher.name, LANE, [record | {"to": [f"{state.POSTS_LANE}@{watcher.name}"]} for record in records])
    if closes := [(record, order_id, seq) for record, seq in zip(records, seqs, strict=True) for order_id in state.closed_orders(record.get("attr") or {})]:
        def reported_write(data: dict):
            for record, order_id, seq in closes:
                order = data["orders"][order_id]
                order["closed_on"] = order["closed_on"] or seq
                if order["close_report"] and order["close_report"]["id"] == record.get("id"):
                    order["close_report"] = order["close_report"] | {"seq": seq} if state.watch_posts_itself(order) else None

        state.write(watcher.name, reported_write, budget=state.WATCH_WRITE_SECONDS)
    return seqs


def closed(watcher: Watcher, head: str, links: list[str], fix: str, moment: datetime) -> list[str]:
    """Close an incident that is over: its live work moves to a plain order and each of its orders keeps the recovery of its monitors, when it has any, and its fix live as its proof, then the record naming each PR of the incident as the watch last read it from GitHub and each order it closes, its text the Live line the watch wrote him, which the drive posts in its thread from that record by talk.report_line, whose Sonnet call writes the post from those words under REPORT_RULES and closes each order the record names; one the model skips closes them with no line, as the model's call, and one that fails goes to a report-post of its own, which hands one outside the drive's channels to the communicator. The record carries no `POST`, so the relay never posts it a second time; a record that fails goes again next read."""
    from drive import talk

    read_ok = f"{', '.join(links)} read OK"
    loaded = state.load(watcher.name)
    prs = incident_pr_lines(watcher.name, state.open_incidents(loaded, state.feature_orders(loaded, head)))

    def proven(data: dict) -> tuple[dict, list[str], dict, str | None]:
        group = state.open_incidents(data, state.feature_orders(data, head))
        incident = data["orders"][head]
        follow = follow_up(data, head, group, state.stamp(moment))
        for order_id in group:
            data["orders"][order_id]["proof"] = data["orders"][order_id]["proof"] or "; ".join([*([f"{read_ok} at {when(state.stamp(moment))}"] if links else []), fix])
        return data, group, incident, follow

    data, group, incident, follow = state.write(watcher.name, proven, budget=state.WATCH_WRITE_SECONDS)
    facts = [
        f"Incident: {incident['text']}",
        *([f"Recovered: {read_ok}"] if links else []),
        f"Fix live: {fix}",
        *prs,
    ]
    record = {
        "kind": "report",
        "topic": f"watch-recovered-{head}",
        "attr": {"close": ",".join(group)} | state.thread_attr(data, incident),
        "text": f"Live: {incident['title'] or cut(incident['text'], 80)} is over, {'its monitors read OK and ' if links else ''}its fix is live",
        "body": "\n".join(facts) + "\n",
    }
    try:
        [seq] = reported(watcher, [record])
    except state.DriveError as error:
        return failed(watcher, f"close of {head}", f"close record of {head}", error, moment)
    watcher.failures.pop(f"close of {head}", None)
    def recovered_write(data: dict) -> None:
        for order_id in group:
            data["orders"][order_id]["recovered"] = state.stamp(moment)

    state.write(watcher.name, recovered_write, budget=state.WATCH_WRITE_SECONDS)
    lines = []
    try:
        said = f"the drive posted its Live line: {talk.report_line(watcher.name, seq)}"
    except talk.Skipped as skipped:
        said = f"the drive posted no Live line, as {skipped}{''.join(talk.closed(watcher.name, order_id, seq) for order_id in group)}"
    except (slack.SlackError, state.DriveError) as error:
        said = "its Live line goes to report-post"
        lines = failed(watcher, f"live line {head}", f"Live line of {head}, posted from its record by report-post instead", error, moment)
        try:
            state.detach(watcher.name, ["report-post", seq], POSTS_LOG)
        except OSError as failure:
            lines += failed(watcher, f"live post {head}", f"post of the Live line of {head} from its record", failure, moment)
    moved = f"; its live jobs, open PRs and parts left moved to the plain order {follow}" if follow else ""
    return [*lines, f"RECOVERED {head}: {f'{read_ok} and ' if links else ''}{fix}, so {said}{moved}"]


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
    follow = data["orders"][order_id] = state.new_order(incident["text"], incident["from"], state.promise_thread(data, incident), parts, created) | {
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
    """Start the check against his words of each PR an event names that owes one, with no coordinator turn: a GitHub delivery naming it, such as a push to its head or a review, by take_deliveries, every PR once his rulings change, and every PR on the watch's first pass and each CHECK_SWEEP after, the one sweep that catches a delivery the receiver missed or a check that failed; redraw_lines draws its verdict on his card in place, so his Approve of a changed patch lands it once it is green, and its NOT READY goes to the builders of the PRs it faults, by objections."""
    if watcher.cards is None:
        return
    data = state.load(watcher.name)
    rulings = frozenset(data["answers"])
    if watcher.swept_at is None or moment - watcher.swept_at >= CHECK_SWEEP or rulings != watcher.rulings:
        watcher.swept_at, watcher.rulings, named = moment, rulings, set(data["prs"])
    else:
        named = watcher.checking & data["prs"].keys()
    watcher.checking = set()
    stacks = land.landing_stacks(data)
    if owed := [key for key in sorted(named) if hears(watcher, data, (pr := data["prs"][key])["order"]) and check_owed(watcher.name, key, pr, data, stacks.get(key, (key,)), moment)]:
        served.start(watcher.name, owed)
    objections(watcher)


def objections(watcher: Watcher) -> None:
    """Send through drive pr wake each NOT READY the check against his words keeps at a head of a PR the watch hears and faults, once a head and once to each job the PRs it faults belong to, while neither his Approve nor an open card of his holds any PR of its stack, by his_hold, so the finding wakes only a builder that owes a step now and waits on his Approve or question, never on his Hold or the hold label, which stop only its landing; the head told is kept as the PR's objected, outside its checker, so a check run again at that head, or a newer install dropping its checker, tells it no second time."""
    moment = state.now()

    def objections_write(data: dict) -> dict[tuple[str, str], tuple[str, str, list[str]]]:
        told: dict[tuple[str, str], tuple[str, str, list[str]]] = {}
        stacks = land.landing_stacks(data)
        for key in state.open_pr_keys(data):
            pr = data["prs"][key]
            if pr["objected"] == pr["head"] or not state.against(pr) or key not in pr["checker"]["faulted"] or not hears(watcher, data, pr["order"]):
                continue
            if (objecting := pr["objecting"]) and objecting["head"] == pr["head"] and stage.waking(objecting["at"], moment):
                continue
            if any(his_hold(data, data["prs"][member]) for member in stacks.get(key, (key,))):
                continue
            pr["objecting"] = {"head": pr["head"], "at": state.stamp(moment)}
            told.setdefault((stage.owner_job(data, key) or pr["job"], pr["checker"]["outcome"]), (key, f"PR {key}: {pr['checker']['outcome']} {ids(pr)}", []))[2].append(f"objected:{key}@{pr['head']}")
        return told

    for key, line, marks in state.write(watcher.name, objections_write, budget=state.WATCH_WRITE_SECONDS).values():
        stage.wake(watcher.name, key, line, marks=marks, at=state.stamp(moment))


def his_hold(data: dict, pr: dict) -> bool:
    """Whether his Approve or an open card of his holds the PR, a card on an open incident's work included, by state.part_cards, since his answer to it can change what its builder does; his Hold and the hold label stop only its landing, so a builder still hears an objection under either."""
    return land.approved(pr) or bool(state.part_cards(data, pr["job"], pr))


def check_owed(name: str, key: str, pr: dict, data: dict, unit: tuple[str, ...], moment: datetime) -> bool:
    """Whether a PR owes the check of its change against his words: open and waiting on his card, its change at its head not checked beside each PR of unit, the stack it sits in, at its head, its builder's report no port of an old-tree PR, which the coordinator checks against that PR, and no check of it and the rest of unit together running, or done at this head against the orders and answers of his that bind it now. It runs on every head whatever its job's state or its waits, its objection a line on his card and, until he approves the PR, a wake of its builder, and his Approve lands the PR whatever it says."""
    if pr["state"] != "open" or not stage.carded(pr) or served.stands(data, key, unit):
        return False
    return not served.ported(state.report_text(name, pr["job"])) and not served.tried(name, data, key, unit, moment)


def waiting_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Tell him once, in one reply in the card's own thread naming no card, the minute a card of his starts holding a job built on its recommended option, once per job and card whether the job's waits or what it was told name the card by its Slack link or its waits id. Each job's card is claimed before its post, by a write refused when another watch's claim commits first, with the pid and start time of the watch claiming it, so no other watch posts it too, and is said once the post goes through; a post that fails gives its claims back and goes again next read, and a claim whose watch died before saying it is taken again, its pid running another process since or none, so a watch dying between its post and saying it is the one way he is told twice."""
    if watcher.cards is None:
        return []
    from drive import talk

    if not unsaid_waits(watcher, state.load(watcher.name)):
        return []
    claimer = {"pid": os.getpid(), "started": started(os.getpid())}

    def waiting_claimed(data: dict) -> dict[str, list[dict]]:
        cards: dict[str, list[dict]] = {}
        for entry in unsaid_waits(watcher, data):
            data["jobs"][entry["job"]]["waiting_claims"][entry["wait"]] = claimer
            cards.setdefault(entry["card"]["url"], []).append(entry)
        return cards

    lines = []
    for url, entries in state.write(watcher.name, waiting_claimed, budget=state.WATCH_WRITE_SECONDS).items():
        try:
            reply = talk.posted(watcher.name, {"text": PAUSED, "thread_ts": view.card_thread(url)})
        except (state.DriveError, slack.SlackError) as error:
            def waiting_given_back(data: dict) -> None:
                for entry in entries:
                    data["jobs"][entry["job"]]["waiting_claims"].pop(entry["wait"])

            state.write(watcher.name, waiting_given_back, patient=True)
            lines += failed(watcher, "waiting post", "built-and-paused reply", error, moment)
            continue

        def waiting_said(data: dict) -> None:
            for entry in entries:
                job = data["jobs"][entry["job"]]
                job["waiting_claims"].pop(entry["wait"])
                job["waiting_said"].append(entry["wait"])

        state.write(watcher.name, waiting_said, patient=True)
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
    """What frees a wait. A release wait is a wake the job set, never a gate: it frees once its PR merged and the watch's release live read finds every cluster of its service, production or not, or the one env the wait names after its @, serving a commit holding the merge, so a bare wait never frees while staging alone serves it, and at once once its PR merged where release live cannot follow it, a PR outside the home repository or a service or env no cluster runs, naming why. A staging wait frees once release live reads its env's target serving its commit or a build carrying it, a build wait once the watch's Buildkite read finds it ended or stopped at an approve step, and a wait on a PR the drive does not track once github_lines reads it merged or closed. None for any other wait, or while it holds."""
    if found := state.RELEASE_WAIT.fullmatch(wait):
        key, service, env = found["pr"], found["service"], found["env"]
        if not data["prs"][key]["merged_sha"]:
            return None
        if not state.home_repo(data, github.repo_of(key)):
            return f"{key} merged, and the release live read finds only {state.home(data)}, so nothing follows its release"
        if watcher.serving is None or (clusters := watcher.serving.get(wait)) is None:
            return None
        if env and env not in clusters:
            return f"{key} merged, and the release live read finds {service} on no env {env}, so nothing follows its release there"
        if not clusters:
            return f"{key} merged, and the release live read finds {service} on no cluster, so nothing follows its release"
        serves = [env] if env else sorted(clusters)
        return f"the release live read finds {service} on {', '.join(serves)} serving {key}" if all(clusters[each] for each in serves) else None
    if (found := state.STAGING_WAIT.fullmatch(wait)) and (watcher.serving or {}).get(wait, {}).get(found["env"]):
        return f"the release live read finds {found['env']}'s {found['target']} serving {found['commit'][:12]} or a build carrying it"
    if providers.of(data).build.fullmatch(wait) and wait in watcher.builds:
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
    """Drop the waits of a job or PR that ended, a wait on a PR or its release whose PR, of a job other than own, closed unmerged taking that job's change in its place by state.change_wait, unless that job builds nothing, as a staging tester or prover, whose closed PR, such as a flake fix it opened, leaves no change of it to wait on, so the wait ends rather than hold its owner behind that job's test, which a PR's wait on the job reads as testing it, and each out names, as a job's wait on another job's change ends once that job registers a PR; what ended, when nothing else holds it, a process by its number."""
    waits = list(
        dict.fromkeys(
            state.change_wait(data, pr["job"]) if (pr := data["prs"].get(state.waited_pr(wait))) and pr["state"] == "closed" and pr["job"] != own and not state.builds_nothing(data["jobs"][pr["job"]]) else wait
            for wait in owner["waits"]
        )
    )
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
    """Each reply wait of a live job the watch hears, his cards asking it nothing, that a teammate's reply or another drive's record ended, with who replied or posted and its link or seq, read once a minute: for a teammate's, the first message after the drive's question from anyone but the drive and a bot, which first goes to the communicator in a report, which the communicator posts it in the thread of the job's order, and onto the job's held texts, which the watch carries it once nothing holds it; for another drive's, by posted_after. A failed read or write is said as a failed read is and tried again next read, the report under the same idempotency key."""
    out, lines, followed = {}, [], {}
    for job_id, job in data["jobs"].items():
        if job["state"] in state.FINISHED_JOB_STATES or not heard_job(watcher, data, job) or view.open_cards(job):
            continue
        for wait in job["waits"]:
            if not (asked := state.REPLY_WAIT.fullmatch(wait)):
                continue
            if asked["seq"]:
                followed.setdefault(wait, []).append(job_id)
                continue
            source = f"reply {wait}"
            try:
                if reply := first_reply(asked["link"]):
                    out[wait] = told(watcher, data, job_id, asked["link"], reply)
            except (slack.SlackError, reads.ReadError, state.DriveError) as error:
                lines += failed(watcher, source, f"read of the reply {job_id} waits on to {asked['link']}", error, moment)
                continue
            watcher.failures.pop(source, None)
    posted, unread = posted_after(watcher, data, followed, moment)
    return out | posted, lines + unread


def posted_after(watcher: Watcher, data: dict, followed: dict[str, list[str]], moment: datetime) -> tuple[dict[str, str], list[str]]:
    """Each reply:cci:<seq> wait among followed, which names the jobs waiting on it, that another drive's record ended, with who posted it and its seq: the first record after the one at its seq on that record's topic in the drive holding it, but for one whose lane names this drive, which is this drive's own line and no word of the other's, read in one cci show of every such seq, by state.anchors, and one cci grep per drive they name, whatever the number of waits. Its words, whole from the body file cci attaches to a record longer than its line, go onto each waiting job's held texts in one save, under the record's seq so no second read holds them again, which the watch carries it once nothing holds it, with no report to the communicator, since another drive's coordination line asks nothing of him; a failed read is said as a failed read is and tried again next read."""
    if not followed:
        return {}, []
    source = "reply:cci"
    try:
        anchors = state.anchors(data, followed)
        drives: dict[str, list[dict]] = {}
        for anchor in anchors.values():
            drives.setdefault(anchor["drive"], []).append(anchor)
        posted = {
            drive: [
                json.loads(line)
                for line in reads.run(
                    ["cci", "grep", ".", "--drive", drive, *(flag for topic in dict.fromkeys(anchor["topic"] for anchor in held) for flag in ("--topic", topic)), "--since", str(min(anchor["seq"] for anchor in held)), "--budget", str(agents.AMENDMENT_BYTES), "--json"],
                    Path.cwd(),
                ).splitlines()
            ]
            for drive, held in drives.items()
        }
    except (reads.ReadError, state.DriveError) as error:
        return {}, failed(watcher, source, f"read of the records {', '.join(followed)} wait on", error, moment)
    watcher.failures.pop(source, None)
    firsts = {}
    for wait in followed:
        anchor = anchors[state.REPLY_WAIT.fullmatch(wait)["seq"]]
        if after := [record for record in posted[anchor["drive"]] if record.get("topic") == anchor["topic"] and record["seq"] > anchor["seq"] and not record["lane"].endswith(f"@{data['name']}")]:
            firsts[wait] = (anchor, min(after, key=lambda record: record["seq"]))
    if not firsts:
        return {}, []
    def posted_after_write(saved: dict) -> None:
        for wait, (anchor, first) in firsts.items():
            for job_id in followed[wait]:
                words = f"{first['kind']} record {first['seq']} from {first['lane']}@{first['drive']}, the first on topic {anchor['topic']} after {anchor['seq']} that {job_id} waited on: {Path(body).read_text().strip() if (body := first['refs'].get('path')) else first['text']}"
                state.hold_text(saved["jobs"][job_id], words, state.WATCH_SENDER, seq=str(first["seq"]))

    state.write(watcher.name, posted_after_write, budget=state.WATCH_WRITE_SECONDS)
    return {wait: f"{first['lane']}@{first['drive']} posted {first['seq']} on {anchor['topic']}" for wait, (anchor, first) in firsts.items()}, []


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
    """Hold a teammate's reply to the question the drive asked them for a job on that job, for its agent to read; what ended its wait."""
    from drive import cli

    who = reply.get("user_name") or reply.get("user")
    words = f'{who} replied {reply["permalink"]} to the question the drive asked them for {job_id} at {question}: "{reply.get("text") or ""}"'
    cli.hold(watcher.name, job_id, words, state.WATCH_SENDER)
    return f"{who} replied {reply['permalink']}"


def minutes(span: timedelta) -> int:
    return int(span.total_seconds() // 60)


def brief_first(job: dict) -> str:
    """What starts an untaken job: for a briefed builder or reader, the watch, by start_jobs, so the line names no prompt for a session to start an agent on; for a design job, the prompt an agent starts it on, its brief; a codex job's drive job launch is named apart."""
    if job["kind"] == "codex" or not job.get("brief") or job.get("agent") or job.get("dispatch"):
        return ""
    return f"; {render.start_now(job['brief'])}" if starts_itself(job) else f"; Read your brief first: {job['brief']}"


def first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def mail_line(name: str, data: dict, job_id: str, message: dict, moment: datetime, kept: str | None, taken: bool, owed: list[tuple[str, list[str], list[str], list[str]]]) -> str | None:
    """Record one message from a remote lane on its job, a final report ending any question the lane asked before it and setting its state unless state.result_refusal refuses it or it types a clock time for work already done, which the event line names, and a question opening with a result other than ASK being that result; what it took goes as its job's returned event, by state.returned, which lane_lines writes once its save landed from what this adds to owed, the job, the words before and after its builders' lines and those builders, since render.builder_lines briefs each under the drive's lock, naming when to start each builder a design's DESIGN or a builder's UNBUILT: lines added and each tester its STAGING: lines added, which for an `ASK <file> [<file>…]` naming the blocks `drive job ask` kept is that line, as a job's own return is, and for any other `ASK` says that `drive job ask` keeps it, and a PUSHED makes only the line naming when to start each builder or tester its UNBUILT: or STAGING: lines added, since pushed_lines settles the rest. The message ending the turn of a report `drive job report` already took, a worker_done or Orca's escalation for a worker that exited, whatever its body, only ends that question, since that command wrote its returned event, and any such message from a lane `drive job end` ended changes nothing, so its question stays open for the next worker's handoff; any other escalation is a STOPPED quoting on one line the whole of Orca's last output, or its first line when it names none, and a refused result its line with the refusal, each returned for the coordinator; a builder's or codex job's report the watch takes itself names each PR it links that the drive does not track, since only `drive pr add` registers one, while a reader's or design's moves its part onto such a PR by state.hand_returned_prs; a status, the lane's word to its coordinator while it works, is its job's returned event `MESSAGE <id>: <subject>`, setting no said line, report or state, whose record carries the message whole by whole_message."""
    job = data["jobs"][job_id]
    kind = message["type"]
    body = message["body"] or ""
    job["updated"] = state.stamp(moment)
    if taken:
        if not state.lane_ended(job):
            job.update(report=body, ask=None)
        return None
    if kind == "escalation":
        output = " ".join((body.partition("Last output:")[2].partition("\nFull output:")[0] or first_line(body)).split())
        if not (limit := state.USAGE_LIMIT.search(output)):
            return f"JOB {job_id} {job['order']} STOPPED without a report, last output: {output}"
        restart = (
            f"the watch sends Escape to its dialog and types the resume prompt into its terminal {job['terminal']}, so resume it no second time"
            if state.on_mac(job) and job.get("terminal")
            else f"drive job resume {job_id} <the event> relaunches it"
        )
        return f'JOB {job_id} {job["order"]} stopped on Claude\'s usage limit {orca.place(job)}, "{limit[0]}", not on its work: {restart}'
    if kind == "status":
        owed.append((job_id, [f"MESSAGE {message['id']}: {message['subject'] or first_line(body)}"], [], []))
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
        if job["kind"] in state.CHANGE_KINDS and (untracked := [url for url in dict.fromkeys(found[0] for line in body.splitlines() if PR_LINE.match(line) for found in state.PR_URL.finditer(line)) if state.pr_key(url) not in data["prs"]]):
            owed.append((job_id, [f"{job['said']}; the drive tracks no {', '.join(untracked)}: drive pr add <url> --job {job_id} registers each"], added, []))
            return None
        if state.PUSHED_LINE.match(job["said"]):
            if added:
                owed.append((job_id, [], added, []))
            return None
    if kept:
        owed.append((job_id, [f"ASK {kept}"], [], []))
        return None
    owed.append((job_id, [job["said"]], added, [UNKEPT_ASK.format(job=job_id)] if job.get("ask") == message["id"] and reads.asks(body) else []))
    return None


def lane_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Remote lanes' mail while a lane the watch hears is live, read in one call of Orca's whole mailbox and given by senders to the job whose worker sent it, whatever Run Orca filed it on and whatever that job's state, from every worker of its job and not only the newest, and kept in its job's history, where the mail of a worker a relaunch replaced, and Orca's word on a permission prompt, which Claude Code's timer or the bash_guard hook answers while the lane carries on, only stays: reports and statuses become JOB lines, Orca's notice that its mail did not reach a finished worker ends that worker's turn with no STOPPED and says nothing, each read and `DRIVE` question the relay has not taken gets a reader started here, by start_readers, an open question kept before the watch read it as a read included. A follow-up Orca started to carry a recorded worker's unread mail is that job's mail delivery and never its worker: each turn it ends, settles the texts its job keeps queued by that Dispatch's mailbox, by deliveries and state.take_mail, and a follow-up's failed start posts no STOPPED but holds on the job each text its mailbox still shows unread, runs `drive job resume` on the job, which carries them to its worker or relaunches a finished one with them, and says so on one JOB line. The read starts past the newest sequence the watch's last read reached, and a watch's first read past the newest mark of the jobs it hears, each job keeping the sequence of the newest of its messages taken, judged and moved in one lock-free write, and each Run that mail of a job's came on and Orca still shows unread marked read once that write commits, by orca.mark_read, with every Run Orca's whole mailbox shows unread, by orca.unread_runs, on a watch's first pass and each DRAIN_EVERY after, so mail no pass took cannot pile up either, so Orca's retention drops it and a Run whose jobs ended retires, so a pass reads only the mail that came since, never Orca's whole history, and a job's mail no watch has taken waits for whichever watch hears it next. A message its job's history already keeps, read again in that write, is never taken again, and each message the write takes is kept there once it commits, every read of the history in the write finding it meanwhile, by state.owe_reply, so a watch whose mark another install's save lost replays nothing and a question the relay took meanwhile never runs twice."""
    queue_kept_reads(watcher)
    data = state.load(watcher.name)
    if not live_lanes(watcher, data):
        return []
    lines, messages, delivered, undelivered, owed, asked, unread, turned = [], [], {}, [], [], [], set(), set()
    try:
        mail = lane_mail(watcher, data)
        delivered = deliveries(watcher, data, mail)
    except orca.OrcaError as error:
        lines += read_failed(watcher, LANE_MAIL, error, moment)
    else:
        messages = mail
        watcher.failures.pop(LANE_MAIL, None)
    kept = functools.cache(lambda job_id: {reply["id"] for reply in state.replies(watcher.name, job_id)})
    asks = kept_asks(data, senders(watcher, data, delivered), messages, kept)
    if messages:

        def mail_taken(data: dict):
            sender = senders(watcher, data, delivered)
            kept.cache_clear()
            lines, undelivered, owed, asked, unread, turned = [], [], [], [], set(), set()
            for message in messages:
                job_id = sender(message)
                if job_id and message.get("delivered_at") is None:
                    unread.add(message["to_handle"].removeprefix("run:"))
                if not job_id or message["sequence"] <= data["jobs"][job_id]["mail_seen"]:
                    continue
                data["jobs"][job_id]["mail_seen"] = message["sequence"]
                if message["type"] == "heartbeat" or message["id"] in kept(job_id):
                    continue
                state.owe_reply(watcher.name, data, job_id, message)
                job = data["jobs"][job_id]
                if sending(message) in replaced(job) or state.prompted(message):
                    continue
                if message["type"] == "question" and reads.request(message["body"] or ""):
                    asked.append((job_id, message))
                    continue
                if message["type"] in state.TURN_ENDS and message["from_handle"] == f"dispatch:{job['dispatch']}":
                    job["turn_open"] = False
                    if job.get("workspace") and not state.on_mac(job):
                        turned.add(job_id)
                if orca.unread_notice(message):
                    continue
                if delivery := delivered.get(sending(message)):
                    held = state.take_mail(job, delivery.box, bool(delivery.failed))
                    if delivery.failed:
                        if held:
                            undelivered.append((job_id, job["order"], held, f"Orca's follow-up dispatch {delivery.dispatch} never started: {delivery.failed}"))
                        continue
                taken = message["type"] in state.TURN_ENDS and (state.lane_ended(job) or message["id"] in state.turn_ends(state.replies(watcher.name, job_id)))
                lines.append(mail_line(watcher.name, data, job_id, message, moment, asks.get(message["id"]), taken, owed))
            return data, lines, undelivered, owed, asked, unread, turned

        data, lines, undelivered, owed, asked, unread, turned = state.write(watcher.name, mail_taken, budget=state.WATCH_WRITE_SECONDS)
        watcher.mailbox_seen = messages[-1]["sequence"]
    for job_id in turned:
        state.detach(watcher.name, ["transcripts-keep", job_id], TRANSCRIPTS_LOG)
    for job_id, said, added, unkept in owed:
        state.returned(watcher.name, data, job_id, "; ".join([*said, *render.builder_lines(watcher.name, data, added), *unkept]))
    for job_id, order_id, held, failed in undelivered:
        carried = "" if busy(watcher, job_id, moment) else f"; the watch ran drive job resume {job_id}, which carries it, its output in the drive's {RELAUNCHES_LOG}"
        if carried:
            send_held(watcher, job_id, moment)
        lines.append(f"JOB {job_id} {order_id} {render.unread(held, failed)}{carried}")
    if watcher.drained_at is None or moment - watcher.drained_at >= DRAIN_EVERY:
        try:
            unread |= set(orca.unread_runs())
            watcher.drained_at = moment
        except orca.OrcaError as error:
            lines += read_failed(watcher, MARK_READ, error, moment)
    for run in sorted(unread):
        try:
            orca.mark_read(watcher.name, run)
        except orca.OrcaError as error:
            lines += read_failed(watcher, MARK_READ, error, moment)
    watcher.made += [line for line in lines if line]
    start_readers(watcher.name, data, asked, moment)
    return []


def lane_mail(watcher: Watcher, data: dict) -> list[dict]:
    """Orca's lane mail past the newest message the watcher's last read reached, or on its first read past the newest mark of the jobs it hears, by orca.mail."""
    return orca.mail(watcher.mailbox_seen or max((job["mail_seen"] for job in data["jobs"].values() if heard_job(watcher, data, job)), default=0) or None)


def start_readers(name: str, data: dict, asked: list[tuple[str, dict]], moment: datetime) -> None:
    """Start a reader, by spawn_reader, on each question asked, oldest first, that its worker still waits on, by still_asked, and did not ask again later among them with the same first line, as a lane does when it types the same read or drive verb again, so a read taken late runs once, on the question its worker waits on."""
    latest = {(job_id, sending(message), reads.opening(message["body"])): (job_id, message) for job_id, message in asked}
    for job_id, message in latest.values():
        if still_asked(data["jobs"][job_id], message, moment):
            spawn_reader(name, job_id, message["id"])


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
    """Start a reader on each live lane's open question that asks a read, so a question kept as the coordinator's, such as one kept before the watch read it as a read, is answered here and its lane runs again."""
    data = state.load(watcher.name)
    if not any(kept_read(job) for job in live_lanes(watcher, data).values()):
        return
    def queue_kept_reads_write(data: dict) -> list[tuple[str, str]]:
        asked = []
        for job_id, job in live_lanes(watcher, data).items():
            if kept_read(job):
                asked.append((job_id, job["ask"]))
                job.update(ask=None, state="running")
        return asked

    for job_id, message_id in state.write(watcher.name, queue_kept_reads_write, budget=state.WATCH_WRITE_SECONDS):
        spawn_reader(watcher.name, job_id, message_id)


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
    """Answer the lanes' reads and owe him each promised line from the watch's relay, and land, stage and wake from its landing loop, each a process of its own the watch starts, by beside, for as long as the watch runs: one relay for the drive and every project the watch takes, which passes a project only once the drive changed or PROJECT_RELAY_IDLE went by, by beside_passes; each line the responder or the relay writes handed to the watcher it is for as it comes, and each map of the PRs the drive does not track the landing loop writes kept as that watcher's, by relayed_lines, which starts again each that ends before the watch ends it; each ended with SIGTERM and its reader waited for RELAY_JOIN_SECONDS before the watch keeps its unposted lines, so each line the relay made is kept, then SIGKILL and as long again, so a pipe another process still holds open never holds the watch's end, which says the reader it left."""
    ending, guard, live = threading.Event(), threading.Lock(), {}
    readers: dict[str, tuple[str, threading.Thread]] = {}

    def started(slot: str, what: str, took: Callable[[str], None]) -> None:
        with guard:
            if ending.is_set():
                return
            reader = threading.Thread(target=relayed_lines, args=(watcher, ending, guard, live, slot, what, took, beside(watcher, slot, live)), daemon=True)
            readers[slot] = (what, reader)
        reader.start()

    started("--relay", "the relay", functools.partial(routed, watcher, took=took_relayed))
    started("--land", "the landing loop", functools.partial(routed, watcher, took=took_untracked))
    try:
        yield
    finally:
        with guard:
            ending.set()
        for slot, (what, reader) in sorted(readers.items(), key=lambda item: item[0] == RELAY):
            live[slot].terminate()
            reader.join(RELAY_JOIN_SECONDS)
            if reader.is_alive():
                live[slot].kill()
                reader.join(RELAY_JOIN_SECONDS)
            if reader.is_alive():
                watcher.relayed.append(f"WATCH {what}, process {live[slot].pid}, still held its pipe open {2 * RELAY_JOIN_SECONDS} seconds after the watch ended it; the watch ended without its last lines")


def beside(watcher: Watcher, flag: str, live: dict[str, subprocess.Popen]) -> int:
    """Start the process flag names beside the watch, by spawn, kept in live under flag, and return the end of its pipe the watch reads."""
    lines, given = os.pipe()
    live[flag] = spawn(watcher, flag, given)
    os.close(given)
    return lines


def relayed_lines(watcher: Watcher, ending: threading.Event, guard: threading.Lock, live: dict[str, subprocess.Popen], flag: str, what: str, took: Callable[[str], None], lines: int) -> None:
    """Hand each line the process beside the watch that flag names writes to took, until it ends; one that ends before the watch ends it is said to the watch's coordinator and started again RESTART_SECONDS later, as its launch agent starts a watch that exits, so a relay that dies costs a line owed him those seconds, never the rest of a pass of the watch, however long that pass runs."""
    while True:
        with os.fdopen(lines) as written:
            for line in written:
                took(line.rstrip("\n"))
        code = live[flag].wait()
        if ending.is_set():
            return
        watcher.relayed.append(f"WATCH {what}, process {live[flag].pid}, exited {code}; the watch starts it again within {launchd.RESTART_SECONDS} seconds; its output is in {state.watch_log_path(watcher.name, watcher.project)}")
        if ending.wait(launchd.RESTART_SECONDS):
            return
        with guard:
            if ending.is_set():
                return
            lines = beside(watcher, flag, live)


def spawn(watcher: Watcher, flag: str, lines: int) -> subprocess.Popen:
    """Start `drive watch <flag> <fd>`, the responder's `--respond`, a relay's `--relay` or the landing loop's `--land`, as a child of the watch, writing to that end of the watch's pipe, in the watch's process group, so the watch's launch agent ends it with the watch whatever ends the watch. EVERY_PROJECT_ENV in its environment says a watch of this install started it: the responder, the relay and the landing loop each run the passes of every project the watch holds beside the drive's own, by joined, each project's lines led by its lane, by relayed."""
    return subprocess.Popen(
        [str(install.DRIVE), "watch", flag, str(lines), "--drive", watcher.name],
        stdin=subprocess.DEVNULL,
        pass_fds=(lines,),
        env=os.environ | {EVERY_PROJECT_ENV: EVERY_PROJECT},
    )


def relayed(watcher: Watcher, line: str) -> str:
    """A line a process beside the watch writes down its pipe: a project's led by its lane and a tab while it runs every project's passes, by every_project, which routed hands to that project's watcher, the drive's own as it is."""
    return f"{watcher.lane}\t{line}" if watcher.project and every_project() else line


def routed(watcher: Watcher, line: str, took: Callable[["Watcher", str], None]) -> None:
    """Hand a line a process beside the watch wrote to took with the watcher it is for: the project watcher whose lane leads it, by relayed, else the drive's own."""
    lane, tab, rest = line.partition("\t")
    one = next((other for other in watcher.others.values() if other.lane == lane), None) if tab else None
    took(one or watcher, rest if one else line)


def joined(watcher: Watcher, moment: datetime, held: Callable[[str], bool], made: Callable[[str], "Watcher"]) -> None:
    """Add to the watcher's others a watcher made for each project the drive binds a sub-coordinator to that held answers for, once each READ_EVERY, so a project bound while the watch runs gets its passes within a minute."""
    if watcher.joined_at is not None and moment - watcher.joined_at < READ_EVERY:
        return
    watcher.joined_at = moment
    for project in state.saved(watcher.name)["subcoordinators"]:
        if project not in watcher.others and held(project):
            watcher.others[project] = made(project)


def every_project() -> bool:
    """Whether this process beside the watch runs every project's passes, as a watch of this install starts it, by spawn; one an older install's watch started runs only the passes it was named for."""
    return os.environ.get(EVERY_PROJECT_ENV) == EVERY_PROJECT


def took_relayed(watcher: Watcher, line: str) -> None:
    """Keep a line the responder or relay wrote for the watcher's next pass to hand on."""
    watcher.relayed.append(line)


def took_untracked(watcher: Watcher, line: str) -> None:
    """Keep as the watcher's the landing loop's newest map of each PR the drive does not track that it read, to the state GitHub answered, which the pass's job stages read."""
    watcher.untracked = json.loads(line)


def relay(name: str, project: str | None, lines: int) -> int:
    """Run the watch's relay, the process the watch starts: a respond pass of the drive and of each project the watch holds every RESPOND_SECONDS, and a relay pass of the drive every RELAY_SECONDS and of each project once due_again says so, by relayed_passes, beside the watch's passes, by beside_passes, so no GitHub read, landing, Slack redraw or install a pass runs holds the handler on a line owed him by the clock or the handler on a POST report."""
    return beside_passes(name, project, lines, RELAY, relayed_passes, RESPOND_SECONDS)


def responder(name: str, project: str | None, lines: int) -> int:
    """Run the watch's responder, the process the watch starts: a respond pass every RESPOND_SECONDS beside the watch's passes and its relay's, by beside_passes, so a lane's read or drive verb gets its reader the moment it is asked, however long the relay's handler starts or a pass of the watch take."""
    return beside_passes(name, project, lines, "--respond", lambda watcher, moment: respond_pass(watcher, moment), RESPOND_SECONDS)


def beside_passes(name: str, project: str | None, lines: int, flag: str, passing: Callable[[Watcher, datetime], list[str]], seconds: float) -> int:
    """Run one pass after another, seconds apart, each first moving this process onto the newest install, by install.reopen, once watch_upgraded reads that install running other code than this one, so the code installed now runs it however long a pass holds the watch on the install it started on, and an install that changes none of its code moves nothing. Each line a pass makes goes to the watch down the pipe end it inherited, for the watch's next pass to hand on; the SIGTERM the watch sends as it ends ends it once the pass or rest it is in ends, kept as a flag rather than an Event, which a handler setting it can deadlock on. It keeps a Watcher of its own, so a failed Orca read of its own never reruns the pass's 60-second block. The relay passes a project only when due_again says the drive moved or its idle time ran out, so seven projects cost one process and no load while nothing changed. Its guard ends it once a pass runs past PASS_FOR and a minute or a rest past STALL_FOR, its stacks in the watch's log, and the watch starts it again, by relayed_lines."""
    ended = term_flag()
    if unwatched(project):
        return 0
    children.reap_inherited()
    passer = Watcher(name, offset=state.watch_cursor(name, project), project=project)
    finished = guarded(name, flag)
    try:
        with os.fdopen(lines, "w", buffering=1) as written:
            while not over(ended):
                beat("pass", True)
                if upgraded():
                    with term_held():
                        install.reopen(["watch", flag, str(lines), "--drive", name, *(["--project", project] if project else [])])
                if every_project():
                    joined(passer, state.now(), held_beside(name), lambda project: Watcher(name, offset=state.watch_cursor(name, project), project=project))
                for one in [passer, *passer.others.values()]:
                    if over(ended):
                        break
                    for line in passing(one, state.now()):
                        written.write(f"{relayed(one, LINE_BREAKS.sub(' ', line))}\n")
                beat("rest")
                time.sleep(seconds)
    finally:
        finished.set()
    return 0


def relayed_passes(watcher: Watcher, moment: datetime) -> list[str]:
    """One tick of the relay for a watcher: its respond pass every RESPOND_SECONDS, so a lane's read gets its reader at once, then its relay pass once due_again owes it one, each handler the relay starts started detached, so no tick waits on a session's start."""
    lines = respond_pass(watcher, moment)
    if due_again(watcher):
        lines += relay_pass(watcher, moment)
        watcher.passed_version, watcher.passed_at = state.LOADED_VERSION.get(), monotonic()
    return lines


def due_again(watcher: Watcher) -> bool:
    """Whether the relay owes a watcher its next relay pass: the drive's own RELAY_SECONDS after its last, so a POST report or a line due by the clock waits on no commit, and a project's once another connection committed to the drive since its last pass read it, or PROJECT_RELAY_IDLE after that pass, so its clocks still come due on a quiet drive."""
    if not watcher.project:
        return monotonic() - watcher.passed_at >= RELAY_SECONDS
    return monotonic() - watcher.passed_at >= PROJECT_RELAY_IDLE or not state.unchanged_since(watcher.name, watcher.passed_version)


def term_flag() -> list[int]:
    """Keep each SIGTERM the watch sends a process beside it as a flag, then let one through that came while the process before this one held it back over its re-exec, by term_held, so a watch that ends its relay as it re-executes onto a newer install still ends it."""
    ended: list[int] = []
    signal.signal(signal.SIGTERM, lambda signum, frame: ended.append(signum))
    signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM})
    return ended


@contextlib.contextmanager
def term_held() -> Iterator[None]:
    """Hold back SIGTERM while a process beside the watch keeps what it owes and re-executes onto a newer install, so one the watch sends then stays pending across the exec for term_flag to take, never lost with the flag of the image it replaced."""
    signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM})


def over(ended: list[int]) -> bool:
    """Whether a process beside the watch is done: the watch sent it SIGTERM, or the watch is gone and launchd took this process as its own, as when the watch was killed, so no orphan runs the drive's passes beside the next watch's."""
    return bool(ended) or os.getppid() == 1


def unwatched(project: str | None) -> bool:
    """Whether this process beside a watch was started for one project's own watch, which an older install ran: it ends at once, so that watch ends too and the drive's watch takes its project."""
    return project is not None and EVERY_PROJECT_ENV not in os.environ


def held_beside(name: str) -> Callable[[str], bool]:
    """Whether the watch this process runs beside holds a project, its pid in the project's pid file, so a process beside the drive's watch runs no project an older install's project watch still holds."""
    return lambda project: holder(state.pid_path(name, project)) == os.getppid()


def landing_loop(name: str, project: str | None, lines: int) -> int:
    """Run the watch's landing loop, the process the watch starts beside its passes: a landing pass the moment anything appends to the drive's events file or the receiver's deliveries log, as the receiver does for each delivery, or POLL_SECONDS after the last, by rests, each first moving this process onto the newest install, by install.reopen, once it held what it owes for the landing loop it starts there, so a GitHub read, a stage's wake and a landing wait on no lane read, Sprite launch or install a pass of the watch runs, and a fix to any of them runs within a pass of its install. It hands its own lines on as the watch does, those the process before it held with the first stage of its first pass, so lines kept back for a later post never cost a landing pass, and writes the watch down the pipe end it inherited each new map of the PRs the drive does not track it read, first the one the landing loop before it held. It takes the reads the landing loop before it held, with the events and deliveries offsets it had read to, else owes a read of every PR from each file's end; the SIGTERM the watch sends as it ends ends it once the pass or rest it is in ends, and a watch gone, as a kickstart leaves it, ends it at the next stage boundary, so no orphan runs a landing pass beside the next watch's landing loop, kept as a flag, and however it ends it holds its unposted lines and owed reads for the landing loop after it."""
    ended = term_flag()
    if unwatched(project):
        return 0
    children.reap_inherited()
    landing = landing_watcher(name, project)
    argv = ["watch", "--land", str(lines), "--drive", name, *(["--project", project] if project else [])]
    told: dict[str | None, dict] = {}
    finished = guarded(name, "--land")
    try:
        with os.fdopen(lines, "w", buffering=1) as untracked:
            for _ in rests(state.events_path(name), receiver.deliveries_path()):
                if over(ended):
                    break
                if upgraded():
                    with term_held():
                        for one in [landing, *landing.others.values()]:
                            hold(one)
                        install.reopen(argv)
                if every_project():
                    joined(landing, state.now(), held_beside(name), functools.partial(landing_watcher, name))
                for one in [landing, *landing.others.values()]:
                    if over(ended):
                        break
                    if one.untracked != told.get(one.project):
                        told[one.project] = dict(one.untracked)
                        untracked.write(f"{relayed(one, json.dumps(told[one.project]))}\n")
                    beat("landing pass", True)
                    moment = state.now()
                    for said in passed(one, moment, staged=landing_pass):
                        for line in sent(one, one.lane, said, moment):
                            print(line, flush=True)
                        if over(ended):
                            break
                    outboxed(one)
                beat("rest")
    finally:
        for one in [landing, *landing.others.values()]:
            hold(one)
        finished.set()
    return 0


def landing_watcher(name: str, project: str | None) -> Watcher:
    """The landing loop's watcher of the drive, or of a project, taking the reads the landing loop before it held for it, with the events and deliveries offsets it had read to, else owing a read of every PR from each file's end."""
    held = state.take_held_reads(name, project)
    owes = {"owed": set(held["owed"]), "merging": set(held["merging"]), "stale": set(held["stale"]), "untracked": held["untracked"], "floor": held.get("floor") and datetime.fromisoformat(held["floor"])} if held else {}
    offset = held.get("offset", state.watch_cursor(name, project)) if held else state.events_end(name)
    delivered = held.get("delivered", 0) if held else receiver.deliveries_end()
    return outboxed(Watcher(name, offset=offset, project=project, delivered=delivered, cards=view.live_cards(name), made=taken_lines(name, project, LANDING_OUTBOX), outbox=LANDING_OUTBOX, **owes))


def landing_pass(watcher: Watcher, moment: datetime) -> Iterator[list[str]]:
    """The lines of each stage of one pass of the landing loop, as it returns: the reads each delivery since its last pass owes, then every READ_EVERY the landing stages, else those reads and the PUSHED lines they settle, by owed_reads, and none on a pass the deliveries alone woke that name none of the drive's PRs."""
    events = list(state.read_events(watcher.name, watcher.offset))
    if events:
        watcher.offset = events[-1][1]
    named = take_deliveries(watcher)
    if watcher.read_at is None or moment - watcher.read_at >= READ_EVERY:
        watcher.read_at = moment
        later = landing_stages(watcher, moment)
    elif named == [] and not events:
        later = []
    else:
        later = owed_reads(watcher, moment)
    for stage in later:
        beat(f"landing {stage.func.__name__}", True)
        yield contained(watcher, stage, moment)


def owed_reads(watcher: Watcher, moment: datetime) -> list[functools.partial]:
    """The stages that take the GitHub reads the deliveries since the last read owe, by github_owed_lines, then each builder's PUSHED, or READY a lane sent since, that read settles, so a READY whose checks are green waits on no 60-second landing pass, then the check of each PR those deliveries named that owes one, by checks."""
    return [functools.partial(stage, watcher, moment) for stage in (github_owed_lines, pushed_lines, checks)]


def respond_pass(watcher: Watcher, moment: datetime) -> list[str]:
    """Start a reader on each new read or `DRIVE` question a live lane asked, then send again each answer a reader kept that no reply took, by resend_lines, and nothing else, so no handler start or Slack call stands between a lane's question and its reader. Its first pass, and one after a failed read, first has this process's Orca calls speak as the drive's own terminal; a failed read goes again next pass, said as the watch says its reads."""
    lines = []
    try:
        if watcher.read_at is None:
            orca.speak_as_drive(watcher.name)
            watcher.read_at = moment
        start_reads(watcher, moment)
    except orca.OrcaError as error:
        lines = read_failed(watcher, NEWEST_MAIL, error, moment)
    else:
        watcher.failures.pop(NEWEST_MAIL, None)
    return lines + resend_lines(watcher, moment)


def relay_pass(watcher: Watcher, moment: datetime) -> list[str]:
    """Run each relay stage by contained, so one that raises, as work_lines did on every pass from 05:27 to 06:02, is logged and counted and never skips post_lines after it: take each ended codex-ask run, by codex_takes, start the disk check once DISKS_EVERY is due, by check_disks, hand on each staging slot whose lease ended or whose turn came, by slot_lines, tell him once of each Claude usage limit this Mac hit, by limit_lines, then start the handler on each POST report that needs one, by post_lines, so none of these clocks waits on a main pass that runs minutes; the lanes' reads are respond_pass's."""
    stages = [
        functools.partial(clocks_drawn, watcher),
        functools.partial(codex_takes, watcher),
        *(functools.partial(stage, watcher, moment) for stage in (limit_lines, applet_lines, check_disks, slot_lines, work_lines, post_lines)),
    ]
    return [line for stage in stages for line in contained(watcher, stage, moment)]


CLOCKS_DRAWN: dict[str, int] = {}


def clocks_drawn(watcher: Watcher) -> None:
    """Draw the drive's clocks row again from the drive's own relay once any write changed the drive since its last draw, by state.redrawn, its one writer, so the overdue block every session's call reads stays current by the rules installed now."""
    if watcher.project or state.scratch():
        return
    if CLOCKS_DRAWN.get(watcher.name) == state.drive_rev(watcher.name):
        return
    state.redrawn(watcher.name)
    CLOCKS_DRAWN[watcher.name] = state.drive_rev(watcher.name)


@dataclass
class Heard:
    process: subprocess.Popen
    arrived: threading.Event
    at: float


HEARD_POSTS: dict[str, Heard] = {}


def posts_due(name: str) -> bool:
    """Whether the relay reads the drive's POST reports now: at its first pass, once the `cci watch` it keeps on reports and corrections to the communicator printed a record, POSTS_IDLE after its last read, so a report held back for a later read is read again, or once that watch ended, which starts it again."""
    heard = HEARD_POSTS.get(name)
    if heard is None or heard.process.poll() is not None:
        HEARD_POSTS[name] = posts_heard(name)
        return True
    if not heard.arrived.is_set() and monotonic() - heard.at < POSTS_IDLE:
        return False
    heard.arrived.clear()
    heard.at = monotonic()
    return True


def posts_heard(name: str) -> Heard:
    """Start `cci watch` on the drive's reports and corrections to the communicator for POSTS_FOR, a thread setting arrived at each record it prints, and nothing else."""
    process = subprocess.Popen(["cci", "watch", "--drive", name, "--to", "communicator", "--kind", "report", "--kind", "correction", "--json", "--for", POSTS_FOR], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    heard = Heard(process, threading.Event(), monotonic())
    threading.Thread(target=lambda: [heard.arrived.set() for _ in process.stdout], daemon=True).start()
    return heard


def post_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Post each `POST` report its writer sent, once, by agents.start_posts, the watch's own progress lines carried as progress; a start that fails is said as a failed read is. It reads cci only once posts_due says a report or correction to the communicator arrived or POSTS_IDLE went by, so the relay runs no cci tail every RELAY_SECONDS."""
    if watcher.project or not posts_due(watcher.name):
        return []
    try:
        agents.start_posts(watcher.name, moment)
    except (slack.SlackError, state.DriveError) as error:
        return failed(watcher, "POST reports", "hand-off of the POST reports", error, moment)
    watcher.failures.pop("POST reports", None)
    return []


def codex_takes(watcher: Watcher) -> None:
    """Take as its result each running codex job's codex-ask run that ended, and as its BLOCKED each one no agent holds that has no run going and none left to take, by Gone, of the jobs this watch hears, by heard_job, so its returned event tells the coordinator within RELAY_SECONDS of the run's end however long a pass holds the watch, with where his card on its ASK stands and, for an incident fix job, the builder that ships its fix; codex_gap reads each run's status and pid files first, so a run that still goes starts no `codex-ask --collect`. A job another process took meanwhile is not taken again."""
    data = state.load(watcher.name)
    ended = {
        job_id: gap
        for job_id, job in data["jobs"].items()
        if job["kind"] == "codex" and job["state"] == "running" and heard_job(watcher, data, job) and (isinstance(gap := codex_gap(job_id, job), Answered) or (isinstance(gap, Gone) and not job.get("agent")))
    }
    if not ended:
        return
    def took(data: dict) -> tuple[dict, list[str], dict[str, str]]:
        taken = [job_id for job_id in ended if data["jobs"][job_id]["state"] == "running"]
        shippers = {}
        for job_id in taken:
            if builder_id := ended[job_id].take(watcher.name, data, job_id):
                shippers[job_id] = builder_id
        return data, taken, shippers

    data, taken, shippers = state.write(watcher.name, took, budget=state.WATCH_WRITE_SECONDS)
    for job_id in taken:
        state.returned(watcher.name, data, job_id, "; ".join(filter(None, [data["jobs"][job_id]["said"], *(codex_asks(watcher.name, job_id) if isinstance(ended[job_id], Answered) else []), *([fix_shipped(watcher.name, shippers[job_id])] if job_id in shippers else [])])))


def start_reads(watcher: Watcher, moment: datetime) -> None:
    """Start `drive job read`, by start_readers, on each read or `DRIVE` question a lane's worker asked, by senders, in Orca's mail past the newest message the relay's last read reached, by lane_mail, so no question drops out of a window of Orca's newest mail however fast lanes ask, keeping it in its job's history as lane_lines keeps lane mail, so lane_lines, which reads past its own mark, finds it kept and starts no reader on it; no mail is read while no lane is live, and no drive lock is taken, since the reader's own claim keeps a question that two starts race on to one reader."""
    data = state.load(watcher.name)
    if not live_lanes(watcher, data):
        return
    sender, mail, asked = senders(watcher, data, {}), lane_mail(watcher, data), []
    kept = functools.cache(lambda job_id: {reply["id"] for reply in state.replies(watcher.name, job_id)})
    for message in mail:
        if message["type"] != "question" or not (job_id := sender(message)) or not reads.request(message["body"] or ""):
            continue
        if message["sequence"] <= data["jobs"][job_id]["mail_seen"] or message["id"] in kept(job_id):
            continue
        state.keep_reply(watcher.name, job_id, message)
        if sending(message) not in replaced(data["jobs"][job_id]) and not state.prompted(message):
            asked.append((job_id, message))
    if mail:
        watcher.mailbox_seen = mail[-1]["sequence"]
    start_readers(watcher.name, data, asked, moment)


def drained(lines: collections.deque[str]) -> list[str]:
    return [lines.popleft() for _ in range(len(lines))]


def resend_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Send again each answer a reader kept for a job this watch hears that no reply took yet, as its file under the drive's answers folder marks it unsent, since a lane whose wait ends waits again on the same question: none while its reader still runs, which sends it the moment it ends, and none to a question no worker waits on any more, by still_asked, whose mark goes with no line; any other goes by send_answer and never reruns the read, each refusal said once as read_failed says it."""
    marks = sorted((state.drive_dir(watcher.name) / ANSWERS).glob("*.unsent"))
    if not marks:
        return []
    data, lines = state.load(watcher.name), []
    for mark in marks:
        kept = json.loads(mark.with_suffix(".json").read_text())
        job = data["jobs"][kept["job"]]
        if not heard_job(watcher, data, job):
            continue
        if not still_asked(job, kept_question(watcher.name, kept["job"], mark.stem), moment):
            mark.unlink(missing_ok=True)
            continue
        if kept["reader"] and state.alive(kept["reader"]):
            continue
        source = f"the answer to {kept['job']}'s"
        if error := send_answer(watcher.name, kept["job"], mark.stem, kept):
            lines += read_failed(watcher, source, error, moment)
        else:
            watcher.failures.pop(source, None)
    return lines


def still_asked(job: dict, message: dict, moment: datetime) -> bool:
    """Whether the job's worker still waits on its question: asked within LANE_WAIT, past which the lane's Sprite drive gave up on it, by a lane a watch reads, by read_lane, or whose worker's turn is open, as when the lane's own `drive job report` took its job done while its next question waited, never by one `drive job end` ended, and by the job's live worker, its message naming that worker's dispatch or none, as sender takes it."""
    return (
        moment - state.parse_time(message["created_at"]) <= LANE_WAIT
        and (read_lane(job) or job["turn_open"])
        and not state.lane_ended(job)
        and json.loads(message.get("payload") or "{}").get("dispatchId", job["dispatch"]) == job["dispatch"]
    )


def kept_question(name: str, job_id: str, message_id: str) -> dict:
    """The question with this message id in the job's kept history, which every reader's starter kept before it started the reader."""
    return {reply["id"]: reply for reply in state.replies(name, job_id)}[message_id]


def answer_file(name: str, message_id: str) -> Path:
    """The file holding the answer to one lane question, one per question in the drive's answers folder, the unsent mark beside it."""
    return state.drive_dir(name) / ANSWERS / f"{message_id}.json"


def spawn_reader(name: str, job_id: str, message_id: str) -> None:
    """Run `drive job read` on one question a lane asked as its own process, outliving the pass that started it, its output appended to the drive's reads log."""
    state.detach(name, ["job", "read", job_id, message_id], READS_LOG)


def answer_read(name: str, job_id: str, message_id: str, moment: datetime) -> tuple[str | None, str | None]:
    """Run a lane's read here and send its answer in one orca.reply, with why it is no answer when it failed, and why none ran: no worker waits on it, by still_asked, so no write a lane asked runs after its lane gave up on it, or another reader took it. A reader takes its question by creating its answer file exclusively, holding the answer a reader stopped mid-run leaves and this process's id, then marking it unsent, so no read runs twice, a `drive break-glass` in production included, and none that crashes starts again; it writes its own answer over that file before it sends it, and the relay sends again one Orca refused. An answer past one Orca relay message is written onto the lane's Sprite first and sent as the line naming that file; one the Sprite cannot take is answered with why, as a failed read is."""
    data = state.load(name)
    job, message = data["jobs"][job_id], kept_question(name, job_id, message_id)
    if not still_asked(job, message, moment):
        return None, f"{job_id}'s worker no longer waits on {message_id}, asked {message['created_at']}, so it is not run"
    question = reads.request(message["body"] or "")
    path = answer_file(name, message_id)
    path.parent.mkdir(exist_ok=True)
    claim = path.with_name(f"{message_id}.{os.getpid()}.claim")
    claim.write_text(json.dumps({"job": job_id, "reader": os.getpid(), "answer": reads.lane_exit(1, CUT_OFF) if reads.DRIVE.match(question) else f"{question}: {CUT_OFF}", "why": CUT_OFF}))
    try:
        os.link(claim, path)
    except FileExistsError:
        return None, f"another reader took {job_id}'s {message_id}"
    finally:
        claim.unlink()
    path.with_suffix(".unsent").touch()
    checkout = Path(data["roles"]["coordinator"]["cwd"])
    try:
        said = reads.answer(question, checkout, name, job_id)
    except reads.ReadError as error:
        return keep_answer(name, job_id, message_id, f"{question}: {error}", f"{question}: {error}")
    return keep_answer(name, job_id, message_id, *sprite_answered(name, job, message_id, question, said))


def sprite_answered(name: str, job: dict, message_id: str, question: str, said: str) -> tuple[str, str | None]:
    """The answer a lane's question gets for what its read printed, with why it is no answer when the Sprite could not take one past one Orca relay message, by orca.sprite_answer."""
    try:
        return orca.sprite_answer(name, said, job, message_id), None
    except state.DriveError as error:
        why = f"{question.partition("\n")[0]}: its {len(said.encode())}-byte answer, past one Orca relay message, could not go to its Sprite: {error}"
        return orca.exit_line(said) + why, why


def keep_answer(name: str, job_id: str, message_id: str, answer: str, why: str | None) -> tuple[str, str | None]:
    """Write the answer to a lane's question over its answer file and send it, by send_answer; the answer and why it is none."""
    kept = {"job": job_id, "reader": None, "answer": answer, "why": why}
    written = answer_file(name, message_id).with_name(f"{message_id}.{os.getpid()}.answer")
    written.write_text(json.dumps(kept))
    os.replace(written, answer_file(name, message_id))
    send_answer(name, job_id, message_id, kept)
    return answer, why


def send_answer(name: str, job_id: str, message_id: str, kept: dict) -> orca.OrcaError | None:
    """Send the answer kept for a lane's question in one orca.reply on its job's Run as the drive's own terminal, which needs no bind of the Run, so a lane gets its answer the moment its read ends and no answer waits on another lane's. It goes through, or Orca says it already holds another answer, since a lane whose wait ends waits again on the same question, or that the question's dispatch closed, since no lane waits on it then: its unsent mark goes, and the sender that takes the mark off one whose read failed writes the event the watch hearing its job says as a `JOB` line, so two senders sending one answer at once send the same text, which Orca takes as the answer it already holds, and write one event. Any other refusal is returned, the mark left for the relay to send again."""
    data = state.load(name)
    job, why = data["jobs"][job_id], kept["why"]
    try:
        orca.reply(name, job["run"], message_id, kept["answer"])
    except orca.OrcaError as error:
        if error.code not in (orca.ANSWERED_ELSEWHERE, orca.DISPATCH_CLOSED):
            return error
        why = None
    try:
        answer_file(name, message_id).with_suffix(".unsent").unlink()
    except FileNotFoundError:
        return None
    if why:
        state.append_event(name, READ_UNANSWERED, job=job_id, order=job["order"], why=why, lane=state.heard_job_lane(data, job))
    return None


def digest_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Once a Pacific day, post in his DM one digest of each question card of his asked more than DIGEST_AGE before, as one table linking each card, which keeps its buttons where it sits and takes his answer there: the only place an old card is asked again, by his words "you keep bringing me old questions", so the news line of its order no longer names it, by waited_cards, and tomorrow's digest lists it again while it stands; kept on the drive by its day, link and waits ids, and a post that fails goes again on the next pass, said from its third failure in a row."""
    if watcher.project or watcher.cards is None:
        return []
    from drive import talk

    midnight = moment.astimezone(view.ZONE).replace(hour=0, minute=0, second=0, microsecond=0)
    day = midnight.date().isoformat()
    if (state.load(watcher.name, ("slack",))["slack"]["digest"] or {}).get("day") == day:
        return []
    old = sorted((shown for shown in watcher.cards["questions"] if shown["card"].get("waits") and moment - state.parse_time(shown["asked"]) > DIGEST_AGE), key=lambda shown: shown["asked"])
    folded = {"day": day, "message": None, "waits": [shown["card"]["waits"] for shown in old]}
    if old:
        rows = "\n".join(f"| {when(shown['asked'])} | {{card:{shown['card']['waits']}}} |" for shown in old)
        words = f"*Asked over a day ago and still waiting on you: {len(old)} questions.* Each card takes your answer where it sits.\n\n| Asked | Question |\n| --- | --- |\n{rows}"
        try:
            folded["message"] = talk.posted(watcher.name, {"text": words, "request_key": f"{int(midnight.timestamp() * 1000)}-{watcher.name}.digest.{day}"})["permalink"]
        except (slack.SlackError, state.DriveError) as error:
            return failed(watcher, "digest", "post of the daily digest of his old questions", error, moment)
        watcher.failures.pop("digest", None)
    def digest_lines_write(saved: dict):
        saved["slack"]["digest"] = folded

    state.write(watcher.name, digest_lines_write, patient=True)
    return [f"DIGEST {day}: {len(old)} of his question cards asked more than a day ago, linked in {folded['message']}"] if old else []


def waiting_post_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Keep the one post in his DM and in each operator channel that says what waits on him there, in the words talk.waiting_posts gives it, which stands in for asking any card again: posted once a card first waits on him there and edited in place whenever its words change, and pinned to its conversation once, so it stays in that conversation's pinned list; each kept with its words and whether it is pinned in the drive across watch restarts, so a post kept before pinning began is pinned on the next pass. A post, edit or pin that fails goes again on the next pass and is said from its third failure in a row."""
    if watcher.project or watcher.cards is None:
        return []
    from drive import talk

    data = state.load(watcher.name)
    lines = []
    for channel, words in talk.waiting_posts(watcher.name, data, watcher.cards, moment).items():
        kept = data["slack"]["waiting"].get(channel)
        if words != (kept["words"] if kept else talk.NO_CARDS):
            try:
                reply = talk.posted(watcher.name, {"edit": kept["message"], "text": words} if kept else {"text": words} | ({} if channel == slack.dm() else {"channel_id": channel}))
            except (slack.SlackError, state.DriveError) as error:
                lines += failed(watcher, f"waiting post {channel}", f"post of what waits on him in {channel}", error, moment)
                continue
            watcher.failures.pop(f"waiting post {channel}", None)
            kept = {"message": reply["permalink"], "words": words, "pinned": bool(kept and kept["pinned"])}
            def waiting_post_lines_write2(saved: dict):
                saved["slack"]["waiting"][channel] = kept

            state.write(watcher.name, waiting_post_lines_write2, patient=True)
        if not kept or kept["pinned"]:
            continue
        try:
            slack.pin(kept["message"])
        except slack.SlackError as error:
            lines += failed(watcher, f"waiting pin {channel}", f"pin of the post of what waits on him in {channel}", error, moment)
            continue
        watcher.failures.pop(f"waiting pin {channel}", None)
        def waiting_post_lines_write(saved: dict):
            saved["slack"]["waiting"][channel]["pinned"] = True

        state.write(watcher.name, waiting_post_lines_write, patient=True)
    return lines


def page_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Write each channel's brief, by page.write_briefs, and redraw the own canvas of each channel the drive keeps, in the words page.canvases gives it, whenever they changed since its last drawing, by the digest kept on that channel's entry across watch restarts, and say the link of each canvas drawn the first time; a drive keeping no channel draws none. A draw that fails goes again on the next pass and is said from its third failure in a row."""
    if watcher.project:
        return []
    data = state.load(watcher.name)
    if not data["slack"]["channels"]:
        return []
    link = dashboard.link(watcher.name)
    lines = named_lines(watcher, data, link, moment)
    data = state.load(watcher.name)
    canvases = page.canvases(watcher.name, data, view.read(watcher.name, moment), link)
    page.write_briefs(watcher.name, data, canvases)
    for seat, (title, markdown) in canvases.items():
        if data["slack"]["channels"][seat].get("canvas_digest") == page.digest(markdown):
            continue
        try:
            drawn = page.draw(watcher.name, seat, title, markdown)
        except page.PageError as error:
            lines += failed(watcher, f"canvas {seat}", f"draw of the canvas of {data['slack']['channels'][seat]['name']}", error, moment)
            continue
        watcher.failures.pop(f"canvas {seat}", None)
        if drawn.new:
            lines.append(f"WATCH canvas of #{data['slack']['channels'][seat]['name']} drawn: {drawn.url}")
    return lines


def named_lines(watcher: Watcher, data: dict, link: str, moment: datetime) -> list[str]:
    """Rename each channel the drive keeps whose name slack.seat_channel_name no longer gives, so no channel goes by an order's id, and set its description to the one line slack.seat_purpose gives, by his order to name each channel plainly and describe it in one line plus its dashboard link, through `cc-slack operator-channel --rename --purpose` as him; a rename that fails goes again on the next pass and is said from its third failure in a row."""
    lines = []
    for seat, made in data["slack"]["channels"].items():
        wanted = slack.seat_channel_name(watcher.name, data, seat or None)
        purpose = slack.seat_purpose(watcher.name, data, seat, page.seat_query(link, data, seat) if seat else link)
        if (made["name"], made.get("purpose")) == (wanted, purpose):
            continue
        try:
            renamed = slack.cc_slack("operator-channel", wanted, "--rename", made["id"], "--purpose", purpose)
        except slack.SlackError as error:
            lines += failed(watcher, f"name {seat}", f"rename of #{made['name']} to #{wanted}", error, moment)
            continue
        watcher.failures.pop(f"name {seat}", None)

        def named_write(saved: dict, seat=seat, renamed=renamed, purpose=purpose):
            saved["slack"]["channels"][seat] |= {"name": renamed["name"], "purpose": purpose}

        state.write(watcher.name, named_write, budget=state.WATCH_WRITE_SECONDS)
        if renamed["name"] != made["name"]:
            lines.append(f"WATCH #{made['name']} is now #{renamed['name']}")
    return lines


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
    if event["kind"] == state.TEXT_WENT:
        return f'ANSWER went {event["job"]}: "{cut(event["words"], 80)}", held at {when(event["held"])}, went {event["how"]} at {when(event["at"])} [{event["order"]} {event["job"]}]'
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
    """Each new event the watch hears: one is written with the lane of the coordinator session hearing its order then, so a binding or a refiling after it moves it to no other watch, an ORDERS_MOVED one with the lane that lost the orders it names, the drive's own watch saying one the communicator or the meta session lost, which handed sends that session, and one written with no lane is the drive's own watch's. Each delivery the receiver logged since owes the reads take_deliveries names, which a pass run in-process makes later in the same pass, as build_lines reads the Buildkite builds a build delivery names; a delivery line an older receiver wrote to the events file is said by no line."""
    take_deliveries(watcher)
    events = list(state.read_events(watcher.name, watcher.offset))
    if not events:
        return []
    data = state.load(watcher.name)
    heard = (watcher.lane,) if watcher.project else (watcher.lane, *state.HOLDERS)
    said = [event_line(event, data) for event, _ in events if event["kind"] not in (*view.HISTORY_ONLY, receiver.DELIVERY, receiver.BUILD_DELIVERY) and event.get("lane", "root") in heard]
    watcher.offset = events[-1][1]
    return said


def take_deliveries(watcher: Watcher) -> list[str] | None:
    """Read the receiver's deliveries log past where the watch last read it, None when it holds nothing new, else the keys of the drive's PRs its GitHub deliveries name by receiver.named, against each repository's unmerged PRs and, for a delivery of receiver.CARRYING, the PRs the drive does not track by state.carrier_keys, each owed a read github_lines makes for the PRs the watch hears, a push to their base a read of their mergeability alone; none while the watch owes a read of every PR, which reads them anyway. A build delivery has build_lines read again each build the watch holds that it names, every one for a missed delivery."""
    deliveries = list(state.read_lines(receiver.deliveries_path(), watcher.delivered))
    if not deliveries:
        return None
    watcher.delivered = deliveries[-1][1]
    for delivery, _ in deliveries:
        if delivery["kind"] == receiver.BUILD_DELIVERY:
            watcher.builds_heard |= watcher.builds.keys() if delivery["builds"] is None else watcher.builds.keys() & set(delivery["builds"])
    github_deliveries = [delivery for delivery, _ in deliveries if delivery["kind"] == receiver.DELIVERY]
    if watcher.owed is None or not github_deliveries:
        return []
    data = state.load(watcher.name)
    carried = state.carrier_keys(data)
    named = []
    for delivery in github_deliveries:
        repo = delivery["repo"]
        prs = {int(key.partition("#")[2]): pr for key, pr in data["prs"].items() if github.repo_of(key) == repo and pr["state"] != "merged"}
        if delivery["event"] in receiver.CARRYING:
            prs |= {int(key.partition("#")[2]): {} for key in carried if github.repo_of(key) == repo}
        keys = [f"{repo}#{number}" for number in receiver.named(delivery, prs)]
        (watcher.merging if delivery["event"] in receiver.REMERGES else watcher.owed).update(keys)
        watcher.checking.update(keys)
        named += keys
    return named


def applet_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Send him each applet page made or changed for him since he was last sent it, by applet.unannounced, by his words "a link to one has never been sent to me": one line through milestone in the channel of the page's first order, its seat's by state.channel_seat_of, else his DM, the page's title linked on its words, where it stands and what changed for him, leaving its next step to the page, whose glance leads with it, so a step naming a release never meets say's release check without his hold, once per material change; its facts record is keyed by the page's layout, so a post retried after a refusal reuses the one record rather than reporting the page again each pass and never per recompose; a post that fails is tried again next pass."""
    from drive import applet

    if watcher.project is not None:
        return []
    data = state.load(watcher.name)
    board = dashboard.link(watcher.name)
    for page_shown, laid, layout in applet.unannounced(watcher.name):
        order_id = page_shown["orders"][0]
        order = data["orders"][order_id]
        made = data["slack"]["channels"].get(state.channel_seat_of(data, order) or "")
        glance = layout["glance"]
        title = layout["headline"].translate(refs.LINK_WORDS)
        words = "\n".join([f"<{board}#/pages/{page_shown['slug']}|{title}>", f"*Where it stands:* {glance['status'].rstrip('.')}.", f"*What changed:* {(layout['changed_for_him'] or glance['changed']).rstrip('.')}.", "The page opens on what you do next"])
        record = {
            "kind": "report",
            "attr": {"order": order_id} | ({"channel": made["id"]} if made else {}),
            "topic": f"page-{page_shown['slug']}",
            "id": f"page-{page_shown['slug']}-{laid}",
            "body": json.dumps({"page": page_shown["slug"], "glance": glance, "changed_for_him": layout["changed_for_him"]}, indent=1) + "\n",
        }
        try:
            milestone(watcher, record, words)
        except (slack.SlackError, state.DriveError) as error:
            return failed(watcher, f"page {page_shown['slug']}", f"post sending him the page {page_shown['slug']}", error, moment)
        applet.announce(watcher.name, page_shown["slug"], laid, layout)
        watcher.failures.pop(f"page {page_shown['slug']}", None)
    return []


def limit_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Tell him once for this Mac each Claude usage limit a session here hit that has not reset, as the usage-limit log every drive here shares records it, by his words on the stop "nothing restarts them or tells him": a milestone he wants to know, which the drive posts through milestone; it posts under that log's lock and marks the hit told there once the post went out, so no other drive's watch tells him again, and a post that fails is tried again next pass."""
    log = state.root() / state.USAGE_LIMIT_LOG
    if not log.exists():
        return []
    with log.open("r+") as kept:
        fcntl.flock(kept, fcntl.LOCK_EX)
        rows = [json.loads(line) for line in kept if line.strip()]
        told = {row["told"] for row in rows if "told" in row}
        if not (hit := next((row for row in reversed(rows) if "until" in row and row["until"] not in told and state.parse_time(row["until"]) > moment), None)):
            return []
        words = (
            f'Claude Code hit its {hit["limit"]} limit on this Mac: "{hit["notice"]}". Sessions here dismiss its dialog and keep working on paid usage,'
            " and the watch restarts each agent and lane it stopped"
        )
        try:
            milestone(watcher, {"kind": "report", "attr": {}, "topic": f"usage-limit-{hit['until']}", "body": hit["notice"] + "\n"}, words)
        except (slack.SlackError, state.DriveError) as error:
            return failed(watcher, "limit notice", "post telling him of Claude's usage limit on this Mac", error, moment)
        kept.write(json.dumps({"told": hit["until"], "drive": watcher.name}) + "\n")
    watcher.failures.pop("limit notice", None)
    return []


def claim_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Owe the thread of each claim whose end passed, such as a staging claim's in #releases-staging, its closing line: read from Slack, a drive post there after the post that set the end ends the claim, and with none the order's lane gets a DUE line naming the claim, again every ten minutes until one is there, by his words "this thread message saying we're done and what the result is was is good, we should always do that"; a line in that thread naming a new end moves it, which state.claim keeps. A thread that could not be read is said as a failed read is and tried again next pass."""
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

        def claim_said(data: dict) -> bool:
            claims = data["orders"][order_id]["claims"]
            if claims.get(ts, {}).get("since") != held["since"]:
                return False
            if closed:
                claims.pop(ts)
                return False
            claims[ts]["said"] = state.stamp(moment)
            return True

        if not state.write(watcher.name, claim_said, budget=state.WATCH_WRITE_SECONDS):
            continue
        lines.append(
            f"DUE {order_id} claim {held['link']} ended {when(held['until'])} with no closing line in its thread: once the work ends, free what it"
            f" claimed and send a POST report with --attr thread={ts} saying it is done, what is free again and the result, as the repository's"
            " staging-claim rule says, or one with --attr until=<the new end> while it runs on"
        )
    return lines


def claim_reply_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Read once the thread of each claim posted 5 minutes ago, such as a staging claim's in #releases-staging, by his words "You then need to watch the thread for responses and if none within 5 mins you can proceed": the order's lane gets a DUE line saying the work it claims may start, or quoting each reply there from anyone but the drive and a bot for it to answer before that work starts. A thread that could not be read is said as a failed read is and tried again next pass."""
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

        def replies_read(data: dict) -> bool:
            if ts not in (claims := data["orders"][order_id]["claims"]):
                return False
            claims[ts]["replies_read"] = state.stamp(moment)
            return True

        if not state.write(watcher.name, replies_read, budget=state.WATCH_WRITE_SECONDS):
            continue
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


def subjects(data: dict, group: list[str], registered: dict[str, list[str]]) -> tuple[dict[str, dict], dict[str, dict]]:
    """The jobs whose own order is one of these and the PRs registered on them or named in the orders' words, the ones a line on the orders is about."""
    jobs = {job_id: job for job_id, job in state.orders_jobs(data, group).items() if job["order"] in group}
    return jobs, {key: data["prs"][key] for key in incident_prs(data, group, jobs, registered) if key in data["prs"]}


def waited_cards(data: dict, cards: dict | None, jobs: dict[str, dict], prs: dict[str, dict], moment: datetime) -> dict[str, str]:
    """Each card of his on these jobs and PRs still open past CARD_WAIT, by its news fact, with what a line says of it: a job's open question drawn on a card, holding what its `Holds:` line names, unless today's digest_lines post lists it, and his open card carrying a PR that asks his Approve, by view.card_for in cc-slack's store, timed from when that card was asked, as the dashboard's queue times it, holding that PR's landing, a PR no open card carries, or with his cards unread, having none, so its order's next line tells him once that it still waits and what it holds, by his words 6cd6aa2 "stuff I assume is done is jsut sitting around for a week"."""
    digested = (data["slack"]["digest"] or {}).get("waits", [])
    asked = {
        drawn: f'{job_id}\'s card {drawn}, "{cut(question["title"] or "", 120)}", asked {when(question["asked"])}, holding '
        + ("every change of its job, its question kept with no Holds: line" if question["holds"] is None else question["holds"] or "nothing, its Holds: line reading none")
        for job_id, job in jobs.items()
        for question in view.open_questions(job)
        if (drawn := question["card"]) and moment - state.parse_time(question["asked"]) > view.CARD_WAIT and question["waits"] not in digested
    }
    tapped = {
        pr["url"]: f'his card on {key}, "{cut(pr["title"], 120)}", asking his Approve since {when(shown["asked"])}, holding {key} from landing'
        for key, pr in prs.items()
        if cards and (shown := view.card_for(cards["pr_cards"], pr["url"])) and moment - state.parse_time(shown["asked"]) > view.CARD_WAIT and not pr["rework"] and state.asks_tap(data, pr)
    }
    return {f"{WAITED_FACT} {link}": said for link, said in (asked | tapped).items()}


def withdrawn_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Take each handoff state.owed_handoffs names on the orders the watch hears off its order by state.answer_handoffs once the communicator withdrew it, read in one cci grep from the oldest of them at each of the watch's reads, once a minute, since the cci close tool posts that withdraw past every drive tool and hook and quiet_lines reads the records re a handoff only before its first report, so no later quiet report, news fact or say's job-start check counts a handoff its sender withdrew; a failed read is said as a failed read is and tried again next pass."""
    data = state.load(watcher.name)
    if not (owed := [handoff["seq"] for handoff in state.owed_handoffs(data, list(heard_orders(watcher, data)))]):
        return []
    try:
        withdrawn = agents.amendments(watcher.name, owed, "communicator")["withdraw"]
    except state.DriveError as error:
        return failed(watcher, "withdrawn handoffs", "read of the handoffs the communicator withdrew", error, moment)
    watcher.failures.pop("withdrawn handoffs", None)
    if withdrawn:
        def withdrawn_lines_write(data: dict):
            state.answer_handoffs(data, [(seq, "withdraw", {}) for seq in withdrawn])

        state.write(watcher.name, withdrawn_lines_write, patient=True)
    return []


def watched_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Answer each teammate thread an open order the watch hears watches, by his pick "Keep it on the order: the drive's always-running watch answers each reply within about a minute, for one Slack read a minute per thread": the messages there newer than its watch has seen, from anyone but him, the drive and a bot, go in one report, and the watch starts the handler that answers them in that thread, or hands the report to the live handler at work there. A failed read, write or start is said as a failed read is and tried again next read, a start with the same report."""
    lines = []
    for order_id, order in heard_orders(watcher, state.load(watcher.name)).items():
        if not (watch := order["watch"]):
            continue
        source = f"watch {order_id}"
        try:
            if seq := watch["record"] or watched_report(watcher, order_id, order):
                agents.handle(watcher.name, f"Record: {seq} Drive: {watcher.name} Scratch: {state.drive_dir(watcher.name) / 'scratch' / f'watch-{seq}'}")
        except (slack.SlackError, reads.ReadError, state.DriveError) as error:
            lines += failed(watcher, source, f"answer of {order_id}'s watched thread {watch['channel']}/{watch['thread']}", error, moment)
            continue
        watcher.failures.pop(source, None)
        if seq:
            def watched_lines_write(data: dict):
                data["orders"][order_id]["watch"]["record"] = None

            state.write(watcher.name, watched_lines_write, budget=state.WATCH_WRITE_SECONDS)
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
    def watched_report_write(data: dict):
        data["orders"][order_id]["watch"].update(seen=max((message["ts"] for message in messages), key=float), record=seq)

    state.write(watcher.name, watched_report_write, budget=state.WATCH_WRITE_SECONDS)
    return seq


def owed_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Keep each message of his `cc-slack dm-owed` lists, in his DM and each operator channel, while the session holding his DM owes it, as the drive's owed replies a communicator's compaction and reload wait on, and start the seat communicator his message in its channel wants; the communicator already holds each such message from cc-slack, so none is handed to it again. A failed read is said as a failed read is and tried again next read."""
    if watcher.project:
        return []
    data = state.load(watcher.name)
    try:
        replies = slack.owed(agents.beside(data)[1]["session"])
    except slack.SlackError as error:
        return failed(watcher, "owed replies", "read of the replies cc-slack owes him", error, moment)
    watcher.failures.pop("owed replies", None)
    if (marks := {reply["ts"]: data["owed_replies"].get(reply["ts"], {"handed": False, "lined": None}) for reply in replies}) != data["owed_replies"]:
        def owed_lines_write(data: dict):
            data["owed_replies"] = marks
            return data

        data = state.write(watcher.name, owed_lines_write, budget=state.WATCH_WRITE_SECONDS)
    return communicators_wanted(watcher, data, replies, moment)


def communicators_wanted(watcher: Watcher, data: dict, replies: list[dict], moment: datetime) -> list[str]:
    """Start the communicator of each seat whose channel holds a message of his cc-slack owes while none is bound for it, and keep when each bound one last had one, by his order that each project and priority channel gets its own communicator, started when the channel first gets a message of his and retired once idle, by communicators_idle; the line of each start."""
    from drive import cli

    owing = {seat for reply in replies if (seat := state.channel_seat(data, state.slack_message(reply["permalink"])[0]))}
    lines = [said for seat in sorted(owing - set(data["communicators"])) if (said := cli.communicator_started(watcher.name, seat)).startswith("started")]
    for said in lines:
        print(f"{state.stamp(moment)} {said}", flush=True)
    if heard := owing & set(data["communicators"]):

        def communicators_wanted_write(saved: dict):
            for seat in heard & set(saved["communicators"]):
                saved["communicators"][seat]["heard"] = state.stamp(moment)

        state.write(watcher.name, communicators_wanted_write, budget=state.WATCH_WRITE_SECONDS)
    return lines


def communicators_idle(watcher: Watcher, moment: datetime) -> list[str]:
    """Retire each seat's communicator that has held its channel COMMUNICATOR_IDLE with no message of his owed there, since its start or the last one, its seat's priority order closed included, by retire_communicator, so the drive's communicator holds that channel again until his next message there starts one anew; the line of each."""
    if watcher.project:
        return []
    data = state.load(watcher.name)
    held = {order["priority"] for order in state.open_orders(data).values() if order["priority"]}
    return [
        retire_communicator(watcher.name, seat)
        for seat, bound in data["communicators"].items()
        if seat.startswith(state.priority_seat("")) and seat not in held or moment - state.parse_time(bound.get("heard") or bound["started"]) > COMMUNICATOR_IDLE
    ]


def retire_communicator(name: str, seat: str) -> str:
    """Take a seat's communicator down: its cci alias and session, by seat_ended, its binding and launched terminal on the drive, and the drive's communicator's rules, so its next call holds the channel again; the line saying so."""
    data = state.load(name)
    session = data["communicators"][seat]["session"]
    alias = state.alias(name, "communicator", seat)
    seat_ended(alias, session)

    def retire_communicator_write(saved: dict):
        saved["communicators"].pop(seat)
        saved["launched"].pop(f"communicator {seat}", None)

    state.write(name, retire_communicator_write, budget=state.WATCH_WRITE_SECONDS)
    print(f"{state.stamp()} retired the communicator for {seat}, session {session}, idle {minutes(COMMUNICATOR_IDLE)} minutes", flush=True)
    return f"retired the communicator for {seat}, session {session}: its channel #{data['slack']['channels'][seat]['name']} had no message of his owed for {minutes(COMMUNICATOR_IDLE)} minutes, so the drive's communicator holds it again until his next message there starts one"


def incident_pr_lines(name: str, group: list[str]) -> list[str]:
    """Each PR of an incident's orders, on their jobs or named in their words, from its row as the watch last read it from GitHub with his card on it, never from what an order's words or note say of it, which only someone typed."""
    from drive import cli

    data = state.load(name)
    with state.indexed(data):
        jobs = state.orders_jobs(data, group)
        keys = incident_prs(data, group, jobs, render.prs_by_job(data))
        cli.with_cards(name, data, {"jobs": jobs, "prs": {key: data["prs"][key] for key in keys if key in data["prs"]}})
        lines = [render.pr_line(data, key, data["prs"][key]["card_state"]) if key in data["prs"] else state.pr_url(key) for key in keys]
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
    """Each reporter whose record the order keeps and whose thread is owed its note and the watch has not said so: a bug report's once its fix is proven live, a question's once its answer is posted, nothing moved on it for ten minutes, the drive's last line on an answer order counting as a move since posting its answer moves nothing else, and no note is recorded; a record another order keeps is that order's to owe."""
    moved = (order["last_line"] if order["kind"] == "answer" else None) or order["updated"]
    if not (state.may_tell(data, order) and moment - state.parse_time(moved) >= TELL_AFTER):
        return []
    return [held for held in order["reporters"] if "kept_by" not in held and not held["told"] and not held["tell_said"]]


def close_answered(watcher: Watcher, moment: datetime) -> None:
    """Close done each heard answer order left nothing owed, by state.answered, whichever post delivered its last part, a report's, drive order posted's or a POSTED line's, and whether its last job ended or its last reporter was told after it."""
    data = state.load(watcher.name)
    if not any(state.answered(data, order_id) for order_id in heard_orders(watcher, data)):
        return
    def close_answered_write(data: dict):
        for order_id in heard_orders(watcher, data):
            if state.answered(data, order_id):
                data["orders"][order_id].update(state="done", updated=state.stamp(moment))

    state.write(watcher.name, close_answered_write, budget=state.WATCH_WRITE_SECONDS)


def close_joined_repeats(watcher: Watcher, moment: datetime) -> None:
    """From the drive's own watch, close each open drive tooling order a session opened that Jev joined to an order now closed, with no unfinished job of its own, as that order closed, done with its proof or dropped, so a repeat folded into a fixed defect never stays open after the fix, as 76 did on Nov 15."""
    if watcher.project or not state.repeats_of_closed(state.load(watcher.name)):
        return
    def close_joined_repeats_write(data: dict):
        for order_id in state.repeats_of_closed(data):
            order = data["orders"][order_id]
            head = data["orders"][order["joins"]]
            order.update(state=head["state"], proof=head["proof"] if head["state"] == "done" else None, note=f"closed with {order['joins']}, the order it repeats", updated=state.stamp(moment))

    state.write(watcher.name, close_joined_repeats_write, budget=state.WATCH_WRITE_SECONDS)


def tell_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Each open order proven live, or whose answer is posted, with a reporter's thread that has not heard it ten minutes on, said once for each thread."""
    data = state.load(watcher.name)
    if not (owing := [order_id for order_id, order in heard_orders(watcher, data).items() if owed_reporters(data, order, moment)]):
        return []

    def tell_said(data: dict) -> list[str]:
        lines = []
        for order_id in owing:
            owed = owed_reporters(data, data["orders"][order_id], moment)
            for held in owed:
                held["tell_said"] = state.stamp(moment)
            answered = data["orders"][order_id]["kind"] == "answer"
            happened, asked, heard, note = (
                ("its answer is posted", "its question was asked in", "the answer", "the answer")
                if answered
                else (f"its fix is proven live by {cut(state.fix_proof(data, data['orders'][order_id]), 160)}", "its bug was reported in", "it is fixed", "the fix-live note")
            )
            threads = f"the thread {asked}, has not heard {heard}" if len(owed) == 1 else f"the threads {asked}, have not heard {heard}"
            lines.append(
                f"TELL {order_id}: {happened} and nothing moved on it for 10 minutes, but {' and '.join(held['link'] for held in owed)}, {threads}:"
                f" send its report with --attr close={order_id}, which the communicator posts {note} {'there' if len(owed) == 1 else 'in each'} and records it with"
                f" drive order told {order_id} <its permalink>; done refuses until then"
            )
        return lines

    return state.write(watcher.name, tell_said, budget=state.WATCH_WRITE_SECONDS)


def ran_out(job: dict, moment: datetime) -> bool:
    return bool(job["due"]) and state.parse_time(job["due"]) <= moment


def clock_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Each job clock that ran out while its job still runs, said once with his words on each order it works, so a second job started on it keeps them; a job that returned before it owes none, and either way the clock ends."""
    data = state.load(watcher.name)
    if not any(ran_out(job, moment) and heard_job(watcher, data, job) for job in data["jobs"].values()):
        return []

    def ran_out_lines(data: dict) -> list[str]:
        lines = []
        for job_id, job in data["jobs"].items():
            if not ran_out(job, moment) or not heard_job(watcher, data, job):
                continue
            if job["state"] == "running":
                words = [f"; his words on {order_id}: {cut(said, settled.BODY_CHARS)}" for order_id in [job["order"], *job["carries"]] if (said := limits.his_words(data["orders"][order_id]))]
                lines.append(
                    f'DUE {job_id} {job["order"]} "{cut(job["goal"], 80)}" was due {when(job["due"])} and still runs: read its transcript,'
                    " and on a miss start a second job on its brief and set its clock (incidents: Clocks)" + "".join(words)
                )
            job["due"] = None
        return lines

    return state.write(watcher.name, ran_out_lines, budget=state.WATCH_WRITE_SECONDS)


def undelivered_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Each text a job an agent or lane started holds, his answers among them, ten minutes after it came and every ten minutes until the watch, a resume or a SendMessage carries it, as its agent's transcript shows each SendMessage by hear_sent."""
    if not (owed := owed_answers(watcher, state.load(watcher.name), moment)):
        return []
    heard = hear_sent(watcher.name, {job_id for job_id, _ in owed})
    if not (still := owed_answers(watcher, heard, moment)):
        return []
    locked = {job_id: why for job_id in {job_id for job_id, _ in still} if (why := orca.locked_out(watcher.name, job_id, heard["jobs"][job_id]))}

    def said_undelivered(data: dict) -> list[str]:
        lines = []
        for job_id, answer in owed_answers(watcher, data, moment):
            answer["said"] = state.stamp(moment)
            job = data["jobs"][job_id]
            lines.append(f'ANSWER undelivered {job_id}: {unreached(job_id, job, data, answer, locked.get(job_id), moment)} [{job["order"]} {job_id}]')
        return lines

    return state.write(watcher.name, said_undelivered, budget=state.WATCH_WRITE_SECONDS)


def held_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Send on the texts jobs hold, by who should act on them now: his words on a job that returned BUILT or DONE or waits only on his own open cards go once to the session holding its order to read, by cli.deliver_to_order, never waking its agent; on any other job whose agent is a subagent on this Mac, his words and texts needing action now, a staging case it adds, reach it at once, never a STAGE line, which restates a wait a queued tester already holds by cli.deliver_held with the owed SendMessage line to the session that started it, while other texts wait for the job's own next wake. Says each owed line and each post that failed."""
    from drive import cli

    data = state.load(watcher.name)
    cards = {question["waits"] for job in data["jobs"].values() for question in job["questions"] if question["waits"] and not question["closed"]}
    lines = []
    for job_id, job in data["jobs"].items():
        fresh = [kept for kept in job["undelivered"] if not kept.get("told")]
        if not fresh or job["state"] in state.ENDED_JOB_STATES or not heard_job(watcher, data, job):
            continue
        quiet = job["state"] in superseded.FRESH_STATES or bool(job["waits"]) and set(job["waits"]) <= cards
        try:
            if quiet:
                if line := cli.deliver_to_order(watcher.name, job_id, job, [kept for kept in fresh if kept["sender"] == state.HIM]):
                    lines.append(f"HELD {line}")
            elif (now := [kept for kept in fresh if kept["sender"] == state.HIM or kept["words"].startswith(ACTION_TEXTS)]) and (lane := state.agent_lane(data, job)) and not agent_death(job, data) and not orca.locked_out(watcher.name, job_id, job):
                given = cli.deliver_held(watcher.name, job_id, state.load(watcher.name)["jobs"][job_id], lane, now)
                if given and cli.OWED in given[1]:
                    lines.append(f"HELD {given[1]} [{job['order']} {job_id}]")
        except state.DriveError as error:
            lines += failed(watcher, f"held of {job_id}", f"held text post for {job_id}", error, moment)
            continue
        watcher.failures.pop(f"held of {job_id}", None)
    return lines


def case_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Post each staging case a tester holds, by state.stage_pr, to its agent on this Mac that the session holding it started, at once and whole, as drive job resume posts a held text to such an agent, by cli.starter_post: a handoff record to the agent's cci address and the SendMessage pointing it there to that session's lane, so the tester adds the case to the test it runs rather than leaving it to its next worker; a Sprite tester's worker gets it from agent_lines, and a tester no agent of a session runs, or whose agent died, from its next worker. Says only a post that failed."""
    from drive import cli

    opens = state.STAGING_CASE.partition("{key}")[0]
    data = state.load(watcher.name)
    owed = [
        (job_id, lane, held)
        for job_id, job in data["jobs"].items()
        if job["state"] not in (*state.FINISHED_JOB_STATES, "waiting")
        and any(held["words"].startswith(opens) for held in job["undelivered"])
        and heard_job(watcher, data, job)
        and (lane := state.agent_lane(data, job))
        and not agent_death(job, data)
        and not superseded.fresh_builder(data, job_id, job, "", "a staging case")
        for held in job["undelivered"]
        if held["words"].startswith(opens)
    ]
    lines = []
    for job_id, lane, held in owed:
        job = state.load(watcher.name)["jobs"][job_id]
        try:
            _, line = cli.starter_post(watcher.name, job_id, job, lane, held["words"], held["at"], held["request"])
        except state.DriveError as error:
            lines += failed(watcher, f"case of {job_id}", f"staging case post to {job_id}", error, moment)
            continue
        watcher.failures.pop(f"case of {job_id}", None)
        if line.startswith(f"{job_id}: owed, send it now: SendMessage"):
            lines.append(f"CASE {line} [{job['order']} {job_id}]")
    return lines


def unreached(job_id: str, job: dict, data: dict, answer: dict, locked: str | None, moment: datetime) -> str:
    """A text the job holds that has not reached it, by the time its words name once that passed, what the watch does with such texts, and the delivery that reaches the job now: for a lane on Orca, drive job resume with no text, which sends it what it holds, or, while orca.locked_out names why that resume is refused, the local launch orca.moved_here names; for an agent on this Mac, the same resume, which posts each text the job holds whole to the agent and prints the SendMessage pointing it at each while its starter reaches it, else hands each to the job's next worker in its handoff, so the line's cut quote is never the text anyone sends; else, a builder that returned, which superseded.fresh_builder sends a fresh builder, among them, the step superseded.delivery names from the session holding the job."""
    named = named_time(answer)
    by = f" by {when(state.stamp(named))}, the time it names" if named and named <= moment else ""
    held = (
        f'"{cut(answer["words"], 80)}", held at {when(answer["at"])}, has not reached {job_id}{by}; the watch sends it to a worker that takes messages,'
        " hands it to the next worker of one that returned and launches a builder never started with it"
    )
    if locked:
        return f"{held}; drive job resume {job_id} is refused now: {locked}, so {orca.moved_here(job_id)}"
    if job.get("where") == "remote" and job.get("dispatch") or job.get("where") == "local" and job.get("agent") and not superseded.fresh_builder(data, job_id, job, answer["words"], "his words"):
        return f"{held}, and drive job resume {job_id} delivers it now"
    return f"{held}; {superseded.delivery(data, job_id, job, answer['words'])[1]}"


def owed_answers(watcher: Watcher, data: dict, moment: datetime) -> list[tuple[str, dict]]:
    """Each text a live job an agent or lane started holds that was last said ten minutes ago or more, or whose words name a time, by named_time, that passed since it was last said, while none of its waits holds; a job none started gets its texts from the launch that starts it, which carries them, one whose waits hold gets them from the watch once they end, and a finished job's is its ORPHAN's, since no resume carries it."""
    return [
        (job_id, answer)
        for job_id, job in data["jobs"].items()
        if job["state"] not in state.FINISHED_JOB_STATES and (job.get("agent") or job.get("dispatch")) and heard_job(watcher, data, job) and not view.holding(data, job["waits"], watcher.cards)
        for answer in job["undelivered"]
        if moment - (said := state.parse_time(answer["said"] or answer["at"])) >= DUE_EVERY or (named := named_time(answer)) and said < named <= moment
    ]


def named_time(answer: dict) -> datetime | None:
    """The first time after a held text was held that its words name, such as 9:00 AM in "start by 9:00 AM", read as limits.typed_moment reads a clock time for the moment it was held; None for a text naming none."""
    held = state.parse_time(answer["at"])
    return min((moment for found in limits.CLOCK_TIME.finditer(answer["words"]) if (moment := limits.typed_moment(answer["words"], found, held)) > held), default=None)


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
    def hear_sent_write(data: dict):
        for job_id, messages in sent.items():
            for said, written in messages:
                superseded.hear(data, job_id, said, written)
        return data

    data = state.write(name, hear_sent_write, patient=True)
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


def stale_texts(watcher: Watcher, moment: datetime) -> list[str]:
    """Once for each answer of his given within STALE_FOR, take off each job it touches each text held for that job's next worker whose premise Jev reads it removing, in one request holding only those texts, so his answer, which that job is held whole, never reaches it beside a text it overruled; his own words and his held answers stay, and a request with no reading leaves the texts held for the next pass to read again."""
    if watcher.project:
        return []
    data = state.load(watcher.name)
    fresh = [(answer_id, kept) for answer_id, kept in data["answers"].items() if not kept.get("texts_read") and moment - state.parse_time(kept["at"]) < STALE_FOR]
    for answer_id, kept in fresh:
        held = [entry for job_id in kept["jobs"] if job_id in data["jobs"] for entry in data["jobs"][job_id]["undelivered"] if entry["sender"] != state.HIM and not entry["words"].startswith("His answer ")]
        slots = {f"t{number}": entry for number, entry in enumerate(held, 1)}
        try:
            read = settled.jev({"answer": {"said": kept["words"]}, "texts": {slot: entry["words"] for slot, entry in slots.items()}}, {slot: settled.asking(STALE_TEXT, slot=slot) for slot in slots}) if slots else {}
        except settled.Unheard:
            continue
        stale = {slots[slot]["request"] for slot, sure in read.items() if isinstance(sure, float) and sure >= settled.YES}

        def texts_read(data: dict) -> dict:
            data["answers"][answer_id]["texts_read"] = state.stamp(moment)
            for job_id in kept["jobs"]:
                if job_id in data["jobs"]:
                    data["jobs"][job_id]["undelivered"] = [entry for entry in data["jobs"][job_id]["undelivered"] if entry["request"] not in stale]
            return data

        data = state.write(watcher.name, texts_read, budget=state.WATCH_WRITE_SECONDS)
    return []


def answer_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Each answer of his that leaves a card still asking him, an order or part it lists of an order still open or a PR not yet live with no mark, standing, settled, dropped, superseded or reopened by it, again every ten minutes until each has one: one line for each session owing a step on it, the session state.holding_lane names for the order of each card, order, part and PR, root's for a card no order holds, its header naming one of them so line_lane hands it to that session; a live job it touches not yet sent his words, as hear_sent reads each SendMessage, takes them held as a text its next resume, launch or GO carries, unless a text its workers were given already names the answer by its id, as a brief's Decisions that bind does, marked sent either way, with no line, so no session forwards them; never one cc-notes marks superseded, by superseded.standing, which is read again only as often."""
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

    def said_answers(data: dict) -> dict:
        for answer_id in owed:
            data["answers"][answer_id]["said"] = state.stamp(moment)
        return data

    data = state.write(watcher.name, said_answers, budget=state.WATCH_WRITE_SECONDS)
    try:
        owed = dict(superseded.standing(data, list(owed.items())))
    except state.DriveError as error:
        return failed(watcher, "cc-notes supersedes", "read of which answers of his cc-notes marks superseded", error, moment)
    watcher.failures.pop("cc-notes supersedes", None)
    if any(jobs for _, jobs in owed.values()):

        def sent_answers(data: dict) -> dict:
            for answer_id, (_, jobs) in owed.items():
                kept = data["answers"][answer_id]
                for job_id in jobs:
                    if not any(answer_id in path.read_text() for path, _ in superseded.given_texts(data, job_id)):
                        state.hold_text(data["jobs"][job_id], f'His answer {answer_id}, verbatim: "{kept["words"]}"', state.WATCH_SENDER)
                    kept["jobs"][job_id] = {"mark": "sent"}
            return data

        data = state.write(watcher.name, sent_answers, budget=state.WATCH_WRITE_SECONDS)
    lines = []
    for answer_id, (keys, _) in owed.items():
        owners: dict[str, tuple[str | None, list[str]]] = {}
        for key in keys:
            order_id = asked[answer_id][key]["order"]
            owners.setdefault(state.holding_lane(data, data["orders"][order_id]) if order_id else "root", (order_id, []))[1].append(key)
        lines += [
            f"ANSWER {answer_id}{f' {named}' if named else ''} \"{cut(data['answers'][answer_id]['words'], 80)}\": "
            f"{superseded.counted(owned, asked[answer_id])} with no mark, standing, settled, dropped, superseded or reopened by it; drive answer {answer_id} lists them"
            for named, owned in owners.values()
        ]
    return lines


def session_transcript(holder: dict) -> Path:
    """The session's transcript, which Claude Code keeps in the project folder of the directory the session first ran in wherever cci resumes it since, as a role's moving into the drive's checkout; the path its own folder would hold while it has written none."""
    named = f"{holder['session']}.jsonl"
    return next((config_dir() / "projects").glob(f"*/{named}"), config_dir() / "projects" / NOT_SLUG.sub("-", holder["cwd"]) / named)


def transcript(holder: dict, agent: str) -> Path:
    return session_transcript(holder).with_suffix("") / "subagents" / f"agent-{agent}.jsonl"


def newest_entries(path: Path) -> Iterator[dict]:
    """The transcript's entries newest first, from its tail in spans that grow fourfold, each wider span yielding the narrower one's entries again: for a search that takes the first match."""
    with open(path, "rb") as handle:
        end = handle.seek(0, os.SEEK_END)
        span = TAIL_BYTES
        while True:
            start = max(0, end - span)
            handle.seek(start)
            for line in reversed(handle.read().splitlines()[1 if start else 0 :]):
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
            if not start:
                return
            span *= 4


def last_turn(path: Path, kinds: tuple[str, ...] = ("user", "assistant")) -> dict | None:
    return next((entry for entry in newest_entries(path) if entry.get("type") in kinds), None)


def last_tool_call(path: Path) -> datetime | None:
    """When the session last called a tool, by its newest assistant entry carrying a tool_use block."""
    return next(
        (
            state.parse_time(entry["timestamp"])
            for entry in newest_entries(path)
            if entry.get("type") == "assistant" and any(isinstance(block, dict) and block.get("type") == "tool_use" for block in entry["message"].get("content") or [])
        ),
        None,
    )


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


def compact_due(data: dict, held: dict, moment: datetime) -> str | None:
    """Why a communicator should compact now: past the mark, its last turn ended a minute ago with nothing after it, and no message of his owed an answer, so no answer of his waits on the compaction; a record a minute reaching it never holds the compaction off, as ten quiet minutes did."""
    path = session_transcript(held)
    if not path.exists() or moment - datetime.fromtimestamp(path.stat().st_mtime).astimezone() < COMPACT_WHEN_QUIET:
        return None
    if state.answering(data["owed_replies"], moment):
        return None
    turn = last_turn(path)
    if not turn or not agents.ended(turn):
        return None
    tokens = context_tokens(turn)
    return f"at {tokens} tokens of context, idle with no message of his owed" if tokens >= COMPACT_PAST_TOKENS else None


def compact_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Type /compact into the first Orca terminal each communicator runs in, the drive's and each seat's, at an idle moment, never at its context limit mid-conversation, and into none while no terminal shows it; the drive's own watch alone does."""
    if watcher.project:
        return []
    data = state.load(watcher.name)
    held = {"the communicator": data["roles"]["communicator"]} if "communicator" in data["roles"] else {}
    held |= {f"the communicator for {seat}": seated for seat, seated in data["communicators"].items()}
    lines = []
    for who, seated in held.items():
        if not (why := compact_due(data, seated, moment)):
            continue
        try:
            if not (terminals := orca.session_terminals([seated["session"]])[seated["session"]]):
                continue
            orca.request("terminal", "send", "--terminal", terminals[0]["handle"], "--text", "/compact", "--enter")
        except orca.OrcaError as error:
            lines += read_failed(watcher, f"{who} compaction", error, moment)
            continue
        watcher.failures.pop(f"{who} compaction", None)
        lines.append(f"WATCH compacted {who} {why}")
    return lines


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
    """Keep the receiver taking GitHub's deliveries for every PR the watch hears and every release a job or PR it hears waits on: its launch agent loaded, and a webhook pointing at it on each of their repositories this account administers, each new one owing a read of that repository's PRs, since GitHub delivered nothing of them before it. A repository it does not administer, such as an upstream project, is asked once and never hooked, and github_lines reads its open PRs every READ_EVERY. Each repository is tried whatever another's hook did, and one that cannot be kept is said as a failed read is, naming the gh call; a receiver not answering on loopback is replaced by receiver_check_lines; a proof's drive leaves it alone."""
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
    lines = receiver_check_lines(watcher, moment)
    if errors:
        return lines + failed(watcher, "receiver", "upkeep of the receiver", state.DriveError("; ".join(errors)), moment)
    watcher.failures.pop("receiver", None)
    return lines


def receiver_check_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Replace the receiver once FAILURES_BEFORE_SAYING passes in a row found it neither taking a delivery since the pass before, by a new last delivery of a hook in receiver.hooks, nor answering on loopback, by `launchctl kickstart -k` from the watch's own process, and say so once: a receiver slow under a burst of deliveries still takes them, so it counts as answering, since a kickstart drops each delivery it accepted. The receiver started in its place asks GitHub what it missed, and github_lines reads every open PR the watch hears each READ_EVERY until a pass finds it taking deliveries or answering. One that still does neither is said as a failed read is from FAILURE_EVERY after its replacement."""
    took = {hook["last"] for hook in receiver.hooks().values()} - {None}
    taking = watcher.receiver_took is not None and bool(took - watcher.receiver_took)
    watcher.receiver_took = took
    if taking or receiver.answering():
        watcher.failures.pop(UNANSWERED, None)
        return []
    lines = failed(watcher, UNANSWERED, "the receiver's check", state.DriveError(f"it takes no delivery and nothing answers on its port {receiver.PORT}; its log is {receiver.log_path()}"), moment)
    if watcher.failures[UNANSWERED] != FAILURES_BEFORE_SAYING:
        return lines
    watcher.failure_said[UNANSWERED] = moment
    unanswered = f"the receiver took no delivery and answered nothing on its port {receiver.PORT} {FAILURES_BEFORE_SAYING} passes in a row"
    service = launchd.service(receiver.LABEL)
    try:
        launchd.launchctl("kickstart", "-k", service)
    except state.DriveError as error:
        return [f"WATCH {unanswered}, and the watch could not replace it: {error}; its log is {receiver.log_path()}"]
    return [f"WATCH {unanswered}, so the watch replaced it with `launchctl kickstart -k {service}` and reads every open PR from GitHub each minute until it takes a delivery or answers; its log is {receiver.log_path()}"]


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


class Gone(str):
    """A running codex job's gap once no run of it goes and none is left to take: its launch recorded none, or its run ended and was taken before the job ran again; the relay takes it as the job's BLOCKED, as it takes an ended run, so a codex job with no live process never reads running."""

    def take(self, name: str, data: dict, job_id: str) -> str | None:
        state.take_result(data, job_id, f"BLOCKED {self}")
        return state.fix_shipper(data, job_id)


class Returned(str):
    """A running local job's gap once its agent ended its turn on a message opening with a result word its job's history lacks, by state.kept_return, carrying that message whole and the transcript it ends, which the watch takes as `drive job report <j> -` takes it, whatever the agent's type or however many stops it took, since that agent may be gone and no resume reaches it."""

    whole: str
    path: Path

    def __new__(cls, job_id: str, whole: str, path: Path) -> Self:
        made = super().__new__(cls, f'{job_id}\'s agent finished and its result is not recorded: "{cut(first_line(whole), 100)}"')
        made.whole, made.path = whole, path
        return made

    def take(self, name: str, job_id: str) -> None:
        """Take the return by cli.take_report, or once that refuses it keep it in the job's history all the same, its state unset and the refusal on the JOB line state.returned sends the session holding the job, so the next pass reads it kept and takes it no second time."""
        from drive import cli

        try:
            cli.take_report(name, job_id, self.whole)
        except cli.VERB_ERRORS as error:
            line = first_line(self.whole)
            state.keep_report(name, job_id, line, self.whole)
            state.returned(
                name,
                state.load(name),
                job_id,
                f"{line}; its agent ended without keeping it, so the watch kept it from the end of its transcript {self.path},"
                f' its state unset since drive job report {job_id} - refuses it: {error}; drive job resume {job_id} "<the refusal>" sends it back',
            )


class Idle(str):
    """A local agent's gap that waking it closes: its turn ended on its kept return while its job reads running, on a reader's line opening with no result word, or on no result line. agent_gap adds the step that wakes it, never local_gap, since that step's place.woken judges every builder by local_gap."""


def agent_gap(job_id: str, job: dict, data: dict, moment: datetime) -> str | None:
    """A running local job's gap: a codex job's from its codex-ask run, and while an incident's second-lane builder holds it beside that run, the builder's once the run has not ended, so whichever delivers first sets the job; any other job's from its agent's transcript, an idle agent's naming superseded.delivery's step that wakes it from the session holding the job."""
    if job["kind"] == "codex" and (job.get("codex_run") or not job.get("agent")):
        gap = codex_gap(job_id, job)
        if isinstance(gap, Answered) or not job.get("agent"):
            return gap
    gap = local_gap(job_id, job, data, moment)
    return f"{gap}: {superseded.delivery(data, job_id, job, gap, 'this line')[1]}" if isinstance(gap, Idle) else gap


def codex_gap(job_id: str, job: dict) -> str | None:
    """A running codex job's gap from the codex-ask run its launch recorded: none while it goes, by codex.ended_at, so only an ended run is read by `codex-ask --collect`, then its run, which the relay takes, why when codex-ask cannot read it, which agent_lines counts lost, and Gone, which the relay takes as the job's BLOCKED, when no launch started one or its ended run was taken before the job ran again, which only its next launch answers."""
    if not job.get("codex_run"):
        return Gone(f"{job_id} has no codex-ask run: drive job launch {job_id} starts it on its brief")
    if (ended := codex.ended_at(Path(job["codex_run"]))) is None:
        return None
    if job.get("returned") and ended <= state.parse_time(job["returned"]).timestamp():
        return Gone(f"{job_id}'s codex-ask run ended before its last return: drive job launch {job_id} starts its next")
    try:
        run = codex.run_of(job)
    except codex.CodexError as error:
        return f"{job_id}'s codex-ask run could not be read: {cut(str(error), 100)}"
    return Answered(job_id, run) if run.ended else None


def codex_asks(name: str, job_id: str) -> list[str]:
    """Keep the ASK block a codex job's reply names on its `ASK: <file>` line, or on an `ASK <file>` line it returned, as `drive job ask` keeps a builder's, as the relay takes its run, so his card goes up as the lane returns while the recommended option it built ships, since a codex run is never resumed mid-run and an incident never stops on a choice; what its JOB line says of it: where his card stands, the settled line when he already answered that same question, or why it was not kept."""
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
    return f"{head}: {render.start_now(started)}"


def session_of(job: dict, data: dict) -> dict | None:
    """The bound session whose agent runs a local job; for an agent drive job start started as its own `claude --bg` session, which outlives the session that held its order, the coordinator's once that one is bound no more, so a restart of the drive's sessions reads none of those agents as ended."""
    session = job.get("session")
    bound = state.bound_sessions(data)
    held = next((held for held in bound.values() if session and held["session"] == session), None)
    return held or (bound["coordinator"] if state.own_session(job) else None)


def local_gap(job_id: str, job: dict, data: dict, moment: datetime) -> str | None:
    """A running local job's gap from its agent's transcript: one no agent has taken that no worker started, by worker_started, which only state.start writes, never started, whoever's session added it, since drive job add stamps the adding session on every job, naming the prompt its brief starts it on, by render.start_now, or the drive job brief that writes one; else its agent's session, transcript and last turn."""
    if not (job.get("agent") or job.get("worker_started")):
        start = render.start_now(f"Read your brief first: {job['brief']}") if job.get("brief") else f"brief it with drive job brief {job_id}, then start its agent on the line that prints"
        return f"{job_id} never started: no agent has taken it, so {start}"
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
        if re.match(r"[A-Z]*", line)[0] in (*brief.OPENINGS, "POSTED"):
            if state.kept_return(data["name"], job_id, job, line):
                return Idle(f'{job_id} reads running while its agent ended its turn on its kept return "{cut(line, 100)}", so wake it')
            return Returned(job_id, [block["text"] for block in turn["message"]["content"] if block.get("type") == "text"][-1], path)
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
    """The stop of a running local job's agent the watch acts on, its last response: at once when a model's safeguards stopped it, which safeguard_lines restarts in a fresh Claude session, or a Claude usage limit did, by agents.limited, since the holding session may never answer again to show it lifted, and when it ended on any other API error only once the session holding it has had a response answered since, so the fresh worker the watch hands the job to does not start inside the outage that stopped it; None until then, and for an agent that ended with its session."""
    if not job.get("agent") or (holder := session_of(job, data)) is None or not (turn := stopped_turn(holder, job, data)):
        return None
    return turn if agents.refused(turn) or agents.limited(turn) or answered_since(holder, turn) else None


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
    if agents.limited(turn):
        return f'it stopped on Claude\'s usage limit at {when(turn["timestamp"])}: "{cut(said_last(turn), 100)}"'
    return f'its last response ended on {turn["message"]["stop_reason"]} at {when(turn["timestamp"])}: "{cut(said_last(turn), 100)}"'


def lane_stop(worker: dict, data: dict) -> tuple[dict, Path] | None:
    """The last row of the lane_transcript of a lane on this Mac, and that transcript, when a model's safeguards or a Claude usage limit stopped that response, or when it ended on any other API error and a session bound to the drive has had a response answered since; None until then."""
    path = lane_transcript(worker["terminal"]["worktreePath"])
    if not path or not (turn := last_turn(path)) or not agents.died(turn):
        return None
    return (turn, path) if agents.refused(turn) or agents.limited(turn) or any(answered_since(held, turn) for held in state.bound_sessions(data).values()) else None


def lane_transcript(worktree: str) -> Path | None:
    """The newest transcript of a Claude Code session started in the worktree, the one the Orca lane on this Mac that runs there writes; None before any session there wrote one."""
    sessions = config_dir() / "projects" / NOT_SLUG.sub("-", worktree)
    return max(sessions.glob("*.jsonl"), key=lambda found: found.stat().st_mtime, default=None)


def resume_lane(watcher: Watcher, job_id: str, job: dict, terminal: str, stop: tuple[dict, Path], moment: datetime) -> list[str]:
    """Type agents.RESUMED into the terminal of a lane on this Mac that stopped, once for each stop, as the StopFailure hook resumes a lane on a Sprite, whose RESUMED prompts count toward the same four, after Escape on the usage-limit dialog a limit stop leaves up, which holds every key typed into the pane until it closes; once four resumes met the stop again, say so once and type no more."""
    turn, path = stop
    if watcher.resumed.get(job_id) == turn["timestamp"]:
        return []
    head = f'LANE {job_id} {job["order"]} stopped {orca.ON_MAC}: {stopped_on(turn)}'
    if (tries := agents.resumes(agents.tail(path))) >= len(agents.OUTAGE_WAITS):
        watcher.resumed[job_id] = turn["timestamp"]
        return [f"{head}, after {tries} resumes met the same stop. The watch types no more into it; drive job resume {job_id} <the event> sends it one"]
    source = f"resume of {job_id}"
    try:
        if agents.limited(turn):
            orca.dismiss_limit_dialog(terminal)
        orca.type_prompt(terminal, agents.RESUMED)
    except orca.OrcaError as error:
        return failed(watcher, source, f"typing the resume into {job_id}'s terminal {terminal}", error, moment)
    watcher.failures.pop(source, None)
    watcher.resumed[job_id] = turn["timestamp"]
    dismissed = "sent Escape to its usage-limit dialog and " if agents.limited(turn) else ""
    return [f"{head}. The watch {dismissed}typed the resume prompt into its terminal {terminal}, so resume it no second time"]


def agent_transcript(holder: dict, job: dict, data: dict) -> Path:
    """A local job's agent's transcript: its handler's own session's, else the one beside the session holding it."""
    return Path(own) if (own := state.own_transcript(job)) else transcript(holder, job["agent"])


def last_write(job: dict, data: dict) -> tuple[datetime, dict | None]:
    """When a local job's agent last wrote its transcript, and the turn it wrote last."""
    return written(agent_transcript(session_of(job, data), job, data))


def written(path: Path) -> tuple[datetime, dict | None]:
    """When a transcript was last written, and the turn written last."""
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


def unprompted(job: dict, worker: dict, moment: datetime) -> bool:
    """Whether the live lane on this Mac of a running or waiting job never got its prompt: its worker started UNPROMPTED ago or more and no Claude Code transcript in its worktree was written since, as a launch whose prompt Orca never delivered leaves its session idle at an empty prompt."""
    if not state.on_mac(job) or not job.get("worker_started") or not job.get("terminal") or moment - state.parse_time(job["worker_started"]) < UNPROMPTED:
        return False
    if not (worktree := (worker.get("terminal") or {}).get("worktreePath")):
        return False
    transcript = lane_transcript(worktree)
    return transcript is None or datetime.fromtimestamp(transcript.stat().st_mtime).astimezone() < state.parse_time(job["worker_started"])


def unprompted_lines(job_id: str, job: dict) -> list[str]:
    """Close the terminal of a lane unprompted finds, ending the Claude session that never got its prompt, so the next read finds its worker exited and death_lines hands a running job to drive job resume; one LANE line."""
    refused = orca.close_pane(job["terminal"])
    done = f"Orca refused to close its terminal {job['terminal']}: {refused}" if refused else f"the watch closed its terminal {job['terminal']}, so its next read finds the worker exited and a running job is relaunched"
    return [f"LANE {job_id} {job['order']} never got its prompt {orca.ON_MAC}: no transcript in {UNPROMPTED.seconds // 60} minutes since its worker started; {done}"]


def relaunchable(ended: dict | None, moment: datetime) -> bool:
    """Whether the watch starts a relaunch of a dead worker now: none started yet, or the last one ended at least RELAUNCH_RETRY ago with no next worker and fewer than RELAUNCH_TRIES ran, so a relaunch whose Sprite prepare failed is tried again within minutes, with no session, rather than leaving the job to the six-hour reap."""
    if ended is None or "relaunch" not in ended:
        return True
    last = ended["relaunch"]
    return not state.alive(last["pid"]) and last.get("tries", 1) < RELAUNCH_TRIES and moment - state.parse_time(last["at"]) >= RELAUNCH_RETRY


def bg_death(job: dict) -> str | None:
    """Why a running job's agent drive job start started as its own `claude --bg` session is dead: no running process names its session and Claude Code's background job for it failed, stopped or is gone, so the watch relaunches it as it does a dead Sprite lane, its session the bg entry names while no hook of it has bound the job's agent yet, so one that dies before its first step is relaunched too rather than left to the reap; None for any other agent and while it lives or its turn ended on its own."""
    if not (session := state.own_session(job) or (None if job.get("agent") else (job.get("bg") or {}).get("session"))) or agents.alive(session, agents.session_records()):
        return None
    found = agents.background_job(session)
    if found and found[1]["state"] not in ("failed", "stopped"):
        return None
    return f"its own claude --bg session {session} ended{' ' + found[1]['state'] if found else ', its background job gone'}"


def ended_gap(job_id: str, job: dict, ended: dict, moment: datetime) -> str | None:
    """A running job whose lane died or finished, which the watch relaunched, and no next lane replaced in the time a relaunch takes: none while it waits its turn for a builder slot on this Mac, or within the time a relaunch takes since that turn came and started its next worker, or while that relaunch runs, and once that relaunch ended or overran, the line names it and its log."""
    if state.SLOT_WAIT in job["waits"] or (slot := job.get("slot")) and moment - state.parse_time(slot) < RELAUNCH:
        return None
    gone = moment - state.parse_time(ended["at"])
    relaunch = ended["relaunch"]
    if state.alive(relaunch["pid"]) and moment - state.parse_time(relaunch["at"]) < RELAUNCH:
        return None
    return (
        f"{job_id}'s lane ended {minutes(gone)} minutes ago ({cut(ended['why'], 60)}) and the watch's drive job resume at"
        f" {when(relaunch['at'])} started no next lane, its output in the drive's {RELAUNCHES_LOG}"
    )


def launching(job: dict, moment: datetime) -> bool:
    """Whether the job's lane launch began within the time one takes, so a job with no dispatch yet is starting, not stuck."""
    return bool(job.get("launching")) and moment - state.parse_time(job["launching"]) < RELAUNCH


def mailboxes_read(jobs: list[dict]) -> dict[str, list[dict] | orca.OrcaError]:
    """Each queued remote lane's Dispatch mailbox, by its dispatch, or the OrcaError its read raised, read LANE_READS_AT_ONCE at a time beside lanes_shown, for the lanes whose held texts agent_lines checks were taken."""

    def box(job: dict) -> list[dict] | orca.OrcaError:
        try:
            return orca.mailbox(job, job["dispatch"])
        except orca.OrcaError as error:
            return error

    by_dispatch = {job["dispatch"]: job for job in jobs}
    with ThreadPoolExecutor(LANE_READS_AT_ONCE) as pool:
        return dict(zip(by_dispatch, pool.map(box, by_dispatch.values())))


def lanes_shown(jobs: list[dict]) -> dict[str, dict | orca.OrcaError]:
    """Each live remote lane's worker-show, by its dispatch, or the OrcaError its read raised, read LANE_READS_AT_ONCE at a time, so a pass waits on the slowest read of each batch rather than on every lane's read in turn."""

    def shown(dispatch: str) -> dict | orca.OrcaError:
        try:
            return orca.worker(dispatch)
        except orca.OrcaError as error:
            return error

    dispatches = sorted({job["dispatch"] for job in jobs})
    with ThreadPoolExecutor(LANE_READS_AT_ONCE) as pool:
        return dict(zip(dispatches, pool.map(shown, dispatches)))


def agent_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Each running job's gap from its agent or lane, with each PR it has open, a remote lane, running or waiting, whose worker died, and a running job's lane Orca could not reach, which wake hands to drive job resume at the first failed read, saying no LANE line, since a retry and reconnect needs no model, and again each REWAKE, its LANE line going to the session holding it from that second failure on, its failed reads neither counted nor said while busy reads another drive verb acting on the job, as the wake an earlier watch started does, and a running lane on this Mac whose last response ended on an API error, which resume_lane resumes through its terminal, while a job whose agent or lane on this Mac a model's safeguards stopped goes to safeguard_lines; a lane recorded by its dispatch alone gets its Run, Task and terminal from the worker-show read for its liveness, and a lane filed on this Mac whose worker-show names a Sprite is filed on that Sprite; a live lane whose job keeps texts queued has them settled by its mailbox each pass, by state.take_mail, its worker's next turn started by orca.wake_turn when that read leaves a text unread and its turn is over, and a remote lane whose worker finished or exited has the mail it never read held on its job once for its dispatch by unread_lines, before death_lines relaunches a running job's lane. A local agent that ended its turn on a message opening with a result word its job's history lacks has that message taken by cli.take_report, as `drive job report` takes a return, or kept in that history once that refuses it, the refusal on its JOB line, and has no gap here; a codex job whose codex-ask run ended, or that has none going and none left to take, by Gone, has no gap here, since the relay takes it, by codex_takes, while one whose run codex-ask cannot read counts lost, as a lane Orca cannot read does."""
    data = state.load(watcher.name)
    gaps, deaths, refusals, heads, found, finished, boxes, lost, idle, lines = {}, {}, {}, {}, {}, {}, {}, {}, {}, []
    read: set[str] = set()
    live = [job for order_id, order in heard_orders(watcher, data).items() for job_id in order["jobs"] if (job := data["jobs"][job_id])["order"] == order_id and job.get("where") == "remote" and job.get("dispatch") and job["state"] in LIVE_JOB_STATES and not state.lane_death(job)]
    shown, held = lanes_shown(live), mailboxes_read([job for job in live if job["queued"]])
    for order_id, order in heard_orders(watcher, data).items():
        for job_id in order["jobs"]:
            job = data["jobs"][job_id]
            if job["order"] != order_id:
                continue
            running = job["state"] == "running"
            if running:
                read.add(job_id)
            remote = job.get("where") == "remote"
            if running and job.get("branch") and (job.get("repo") or remote):
                heads.setdefault(job_id, set()).add((job.get("repo"), job["branch"]))
            if not remote:
                if running and (turn := agent_stop(job, data)):
                    if agents.refused(turn):
                        refusals[job_id] = (job["agent"], turn, None)
                    elif (ended := state.lane_death(job)) and not relaunchable(ended, moment):
                        gaps[job_id] = ended_gap(job_id, job, ended, moment)
                    else:
                        deaths[job_id] = (job["agent"], ended["why"] if ended else stopped_on(turn))
                    continue
                if running and job["kind"] != "codex" and job.get("agent") and session_of(job, data) is None:
                    lost[job_id] = f"no bound session holds its agent {job['agent']}"
                if running:
                    gap = agent_gap(job_id, job, data, moment)
                    if isinstance(gap, Returned):
                        gap.take(watcher.name, job_id)
                    elif (why := bg_death(job)) and relaunchable(state.lane_death(job), moment):
                        deaths[job_id] = (job["agent"] or job["bg"]["agent"], why)
                        continue
                    gaps[job_id] = None if isinstance(gap, Returned | Answered | Gone) else gap
                    if gaps[job_id] and job["kind"] == "codex" and not job.get("agent"):
                        lost[job_id] = gap
                continue
            if not job.get("dispatch"):
                if running:
                    gaps[job_id] = None if launching(job, moment) else f"{job_id} never started {orca.ON_MAC if state.on_mac(job) else 'on its Sprite'}"
                    if gaps[job_id]:
                        lost[job_id] = gaps[job_id]
                continue
            if job["state"] not in LIVE_JOB_STATES:
                continue
            if ended := state.lane_death(job):
                if running and relaunchable(ended, moment):
                    deaths[job_id] = (job["dispatch"], ended["why"])
                elif running:
                    gaps[job_id] = ended_gap(job_id, job, ended, moment)
                    if gaps[job_id]:
                        lost[job_id] = f"its lane on {ended['dispatch']} ended: {ended['why']}"
                continue
            source = f"lane of {job_id}"
            try:
                if isinstance(worker := shown[job["dispatch"]], orca.OrcaError):
                    raise worker
            except orca.OrcaError as error:
                if running:
                    lost[job_id] = f"Orca cannot read its lane {job['dispatch']}: {orca.gone(error) or error}"
                if why := orca.gone(error):
                    deaths[job_id] = (job["dispatch"], why)
                    continue
                if running and orca.UNREACHABLE in str(error) and busy(watcher, job_id, moment):
                    continue
                if running and orca.UNREACHABLE in str(error):
                    start_verb(watcher, job_id, ["lane-probe", job_id], moment)
                    continue
                lines += read_failed(watcher, source, error, moment)
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
                    if isinstance(box := held[job["dispatch"]], orca.OrcaError):
                        raise box
                    boxes[job_id] = (job["dispatch"], box, (job | lane, worker))
                except orca.OrcaError as error:
                    lines += read_failed(watcher, f"mail of {job_id}", error, moment)
                else:
                    watcher.failures.pop(f"mail of {job_id}", None)
            if job["undelivered"] and state.SLOT_WAIT not in job["waits"] and orca.takes_messages(job, worker) and not busy(watcher, job_id, moment):
                send_held(watcher, job_id, moment)
            if why := orca.death(worker):
                deaths[job_id] = (job["dispatch"], why)
            elif unprompted(job, worker, moment):
                lines += unprompted_lines(job_id, job)
            elif running:
                gaps[job_id] = remote_gap(job_id, worker, moment)
                if (terminal := orca.typed(job, worker)) and (stop := lane_stop(worker, data)):
                    if agents.refused(stop[0]):
                        refusals[job_id] = (job["dispatch"], stop[0], terminal)
                    else:
                        lines += resume_lane(watcher, job_id, job, terminal, stop, moment)
                if branch := ((worker.get("terminal") or {}).get("branch") or "").removeprefix("refs/heads/"):
                    heads.setdefault(job_id, set()).add((job.get("repo"), branch))
    if found or boxes:

        def taken_mail(data: dict) -> tuple[dict, dict]:
            idle = {}
            for job_id, lane in found.items():
                data["jobs"][job_id].update(lane)
            for job_id, (dispatch, box, shown) in boxes.items():
                if (job := data["jobs"][job_id])["dispatch"] == dispatch:
                    unread = len(job["queued"])
                    state.take_mail(job, box, False)
                    job["turn_open"] = job["turn_open"] or len(job["queued"]) < unread
                    if job["queued"] and not job["turn_open"]:
                        idle[job_id] = shown
            return idle, data

        idle, data = state.write(watcher.name, taken_mail, budget=state.WATCH_WRITE_SECONDS)
    lines += unread_lines(watcher, finished, {job_id for job_id, job in finished.items() if job_id in deaths and job["state"] == "running"}, moment)
    lines += wake_lines(watcher, idle, moment)
    watcher.made += lines
    gapped = {job_id: gap for job_id, gap in gaps.items() if gap}
    unlisted, failed = untracked_prs(watcher, data, {job_id: heads[job_id] for job_id in gapped if job_id in heads}, moment)
    watcher.agent_gaps = {job_id: stranded(job_id, data, gap, unlisted.get(job_id, {})) if gap else None for job_id, gap in gaps.items()}
    return failed + death_lines(watcher, deaths, moment) + safeguard_lines(watcher, refusals, moment) + reaped_lines(watcher, data, read, lost, moment)


def reaped_lines(watcher: Watcher, data: dict, read: set[str], lost: dict[str, str], moment: datetime) -> list[str]:
    """Fail each running job whose worker this pass and every pass for REAP_AFTER found gone, its recorded agent's session ended, its lane never started, ended with no next lane or unreadable by Orca, or its codex-ask run unreadable by codex-ask, once no relaunch the watch started on it still runs, whatever another drive verb's hold on its lock, so a job whose worker died never reads running for days while one read Orca could not answer fails nobody: lost_since on its row stamps the first pass that found it so, kept across watches, and the first pass that reads it live clears it. Failed, its part's ORPHAN line names who starts its next worker and release_lanes ends its Sprite. One LANE line each."""
    marking = {job_id for job_id in read if (job_id in lost) != bool(data["jobs"][job_id].get("lost_since"))}
    due = {job_id: why for job_id, why in lost.items() if (since := data["jobs"][job_id].get("lost_since")) and moment - state.parse_time(since) >= REAP_AFTER}
    if not marking and not due:
        return []

    def reaped(data: dict) -> list[str]:
        lines = []
        for job_id in marking:
            if (job := data["jobs"][job_id])["state"] == "running":
                job["lost_since"] = state.stamp(moment) if job_id in lost else None
        for job_id, why in due.items():
            job = data["jobs"][job_id]
            relaunch = (state.lane_death(job) or {}).get("relaunch")
            if job["state"] != "running" or not (since := job.get("lost_since")) or moment - state.parse_time(since) < REAP_AFTER or relaunch and state.alive(relaunch["pid"]) and moment - state.parse_time(relaunch["at"]) < RELAUNCH:
                continue
            said = f"failed: the watch found no live worker on every read since {when(since)} ({why})"
            job.update(state="failed", said=said, lost_since=None, updated=state.stamp(moment))
            lines.append(f"LANE {job_id} {job['order']} {said}; its part's ORPHAN line names who starts its next worker")
        return lines

    return state.write(watcher.name, reaped, budget=state.WATCH_WRITE_SECONDS)


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


def wake_lines(watcher: Watcher, idle: dict[str, tuple[dict, dict]], moment: datetime) -> list[str]:
    """Start the next turn of each worker whose turn is over while its mailbox read left a text queued for it unread, by orca.wake_turn typing into its terminal that it has mail, since a worker reads its mail only inside a turn and starts none on its own, so no text waits past one pass and no end takes a lane mid-turn; one that takes no messages, or that busy reads another drive verb acting on, waits for a later pass, and a type Orca refuses is tried again next pass. The lane is the job as the watch read it with that worker."""
    lines = []
    for job_id, (lane, shown) in idle.items():
        if not orca.takes_messages(lane, shown) or busy(watcher, job_id, moment):
            continue
        source = f"wake of {job_id}"
        try:
            woken = orca.wake_turn(lane, shown)
        except orca.OrcaError as error:
            lines += read_failed(watcher, source, error, moment)
            continue
        watcher.failures.pop(source, None)
        if woken:
            def wake_lines_write(data: dict):
                data["jobs"][job_id]["turn_open"] = True

            state.write(watcher.name, wake_lines_write, budget=state.WATCH_WRITE_SECONDS)
    return lines


def safeguard_lines(watcher: Watcher, refusals: dict[str, tuple[str, dict, str | None]], moment: datetime) -> list[str]:
    """Restart each running job whose agent or lane, the worker given, a model's safeguards stopped, in a fresh Claude session with a compact handoff, never resuming the session that was flagged, since the flag is a false positive on normal work that one context usually draws, by his order "restart the job at once in a FRESH Claude session", and ask him on a question card only once two restarts were stopped too. Each stop is kept on the job's safeguarded list by the worker it stopped, the stamp of its refused response and, for a lane on this Mac that runs as an Orca worker, the terminal it was read in, so the passes that read the same stop again while its restart runs count it once, and a stop read of a worker the job no longer runs counts nothing; safeguard_restart starts each restart, the second on SAFEGUARD_MODEL. A restart that leaves the same worker stopped is started again RELAUNCH_RETRY after its last try, at most RELAUNCH_TRIES times, and a job busy reads another drive verb acting on waits for a later pass. A third stop, or a stop whose restarts never started a worker, sets the job stopped and keeps a question for him on it by safeguard_ask, whose card safeguard_cards draws and whose Restart tap restarts the job. Each restart and stop is one LANE line to the session holding the job, naming no notice."""
    if not refusals:
        return []

    def claim_restarts(data: dict) -> tuple[list[tuple[str, str | None]], dict[str, dict]]:
        steps, plans = [], {}
        for job_id, (worker, turn, terminal) in refusals.items():
            job = data["jobs"][job_id]
            if job["state"] != "running" or job[state.worker_key(job)] != worker or busy(watcher, job_id, moment):
                continue
            stops = job.setdefault("safeguarded", [])
            if not stops or (stops[-1]["worker"], stops[-1]["turn"]) != (worker, turn["timestamp"]):
                stops.append({"worker": worker, "turn": turn["timestamp"], "terminal": terminal, "at": state.stamp(moment), "tries": 0, "pid": None})
            stop = stops[-1]
            if stop["pid"] and state.alive(stop["pid"]):
                continue
            if len(stops) > SAFEGUARD_RESTARTS or stop["tries"] >= RELAUNCH_TRIES:
                said = f"{SAFEGUARDED} {times(len(stops))}, {restarts(job)}; his card asks whether to restart it once more"
                job.update(state="stopped", said=said, safeguard_card=str(safeguard_ask(watcher.name, data, job_id)), updated=state.stamp(moment))
                steps.append((
                    job_id,
                    (
                        f"LANE {job_id} {job['order']} {said}: the watch set it stopped and asks him on his card. His Restart tap restarts it in a fresh session with no step of yours,"
                        " and Leave it stopped keeps it stopped"
                    ),
                ))
                continue
            if stop["tries"] and moment - state.parse_time(stop["at"]) < RELAUNCH_RETRY:
                continue
            plans[job_id] = safeguard_claim(job_id, job, stop, moment)
            steps.append((job_id, None))
        return steps, plans

    steps, plans = state.write(watcher.name, claim_restarts, budget=state.WATCH_WRITE_SECONDS)
    restarted = safeguard_restart(watcher, plans, moment)
    return [line if line is not None else restarted[job_id] for job_id, line in steps]


def safeguard_claim(job_id: str, job: dict, stop: dict, moment: datetime) -> dict:
    """Count the next try of a restart of a job a model's safeguards stopped, in the write the caller commits, and claim a launch's stop with this process's pid, so no other pass starts a second launch before safeguard_restart gives the stop that launch's pid; what that restart needs. The first stop on the job's safeguarded list clears the job's model, and the second sets SAFEGUARD_MODEL, which every later worker of the job starts on."""
    count = len(job["safeguarded"])
    job["model"] = SAFEGUARD_MODEL if count == SAFEGUARD_RESTARTS else None
    stop.update(tries=stop["tries"] + 1, at=state.stamp(moment))
    remote = job.get("where") == "remote"
    session = None if remote else state.own_session(job)
    plan = {
        "opening": f'LANE {job_id} {job["order"]} {SAFEGUARDED} {times(count)}, while it worked on "{cut(job["said"] or job["goal"], 60)}".',
        "on": f" on {SAFEGUARD_MODEL}" if job["model"] else "",
        "remote": remote,
        "terminal": stop["terminal"],
        "session": session,
        "cwd": job["bg"]["cwd"] if session else None,
        "at": stop["at"],
        "before": stop["pid"],
    }
    if not remote:
        stop["pid"] = os.getpid()
    return plan


def safeguard_restart(watcher: Watcher, plans: dict[str, dict], moment: datetime) -> dict[str, str]:
    """Start the next worker of each job safeguard_claim counted a try on, in a fresh Claude session, once that write committed, then keep the pid of each launch on its stop in place of its claim, or put back what the claim replaced when none started, only where the claim still stands; each job's LANE line. A job's agent on this Mac gets `drive job launch <j> --local` by start_verb, whose takeover from an agent that stopped hands the job to a fresh `claude --bg` session on this Mac, where the watch reads its next stop, on a handoff carrying the brief and what the stopped agent left, the stopped agent's own idle session stopped first so it holds no memory, its transcript kept for that handoff; a lane on this Mac that runs as an Orca worker has the terminal its stop was read in closed instead, by orca.close_pane, so the next read finds its worker exited and death_lines' drive job resume starts its next worker in a fresh Claude session there."""
    told: dict[str, str] = {}
    spawned: dict[str, int] = {}
    claimed = [job_id for job_id, plan in plans.items() if not plan["remote"]]

    def own(data: dict) -> None:
        for job_id in claimed:
            for stop in data["jobs"][job_id]["safeguarded"]:
                if (stop["pid"], stop["at"]) == (os.getpid(), plans[job_id]["at"]):
                    stop["pid"] = spawned.get(job_id, plans[job_id]["before"])

    try:
        for job_id, plan in plans.items():
            opening, on = plan["opening"], plan["on"]
            if plan["remote"]:
                refused = orca.close_pane(plan["terminal"])
                told[job_id] = (
                    f"{opening} Orca refused to close its terminal {plan['terminal']}: {refused}; the watch tries again in {RELAUNCH_RETRY.seconds // 60} minutes"
                    if refused
                    else f"{opening} The watch closed its terminal {plan['terminal']}, so its next read finds the worker exited and drive job resume starts the next in a fresh Claude session{on} with a handoff"
                )
                continue
            if plan["session"]:
                agents.end_bg(plan["session"], Path(plan["cwd"]), removed=False)
            spawned[job_id] = start_verb(watcher, job_id, ["job", "launch", job_id, "--local"], moment)
            told[job_id] = f"{opening} The watch ran drive job launch {job_id} --local, which starts it again in a fresh Claude session{on} on this Mac with a handoff, its output in the drive's {RELAUNCHES_LOG}"
    finally:
        if claimed:
            state.write(watcher.name, own, patient=True)
    return told


def times(count: int) -> str:
    return {1: "once", 2: "twice"}.get(count, f"{count} times")


def restarts(job: dict) -> str:
    """How the drive restarted a job its model's safeguards stopped, as safeguard_lines kept it, in words for him."""
    stops = job["safeguarded"]
    if len(stops) > SAFEGUARD_RESTARTS:
        return f"{times(SAFEGUARD_RESTARTS)} right after a fresh restart, the last on Sonnet 5.5"
    return f"{times(len(stops) - 1)} right after a fresh restart, and the drive could not restart it again" if len(stops) > 1 else "and the drive could not restart it"


def safeguard_ask(name: str, data: dict, job_id: str) -> Path:
    """Keep on the job, in the write the caller commits, the question his card asks once a model's safeguards stopped it past its restarts, in plain words: what it was doing, how often the safety check stopped it and the two choices SAFEGUARD_OPTIONS names, Restart recommended, never Claude Code's notice, an API error or a request id; its ASK block kept in its own file named for its words, as drive job ask names one, with the hash kept_ask draws it under, but past drive job ask's checks and the rulings that decide a question with no card, since the drive asks it itself and only his tap answers it. The file."""
    job = data["jobs"][job_id]
    restart, leave = SAFEGUARD_OPTIONS
    text = (
        f"ASK Restart the stopped job on {cut(job['goal'], 80)}?\n"
        f"The job working on {cut(job['goal'], 120)} for {{order:{job['order']}}} stopped on the model's safety check {times(len(job['safeguarded']))}, {restarts(job)}.\n"
        f"{restart} starts it again in a fresh session; {leave} keeps it stopped.\n"
        f"- {restart} (recommended)\n"
        f"- {leave}\n\n"
        "Findings:\n"
        f"Its model's safety check stopped it at {', '.join(when(stop['turn']) for stop in job['safeguarded'])}.\n"
        "Holds: none, since a stopped job holds no change from landing\n"
    )
    kept = state.drive_dir(name) / "asks" / f"{job_id}-{state.ask_hash(text)}.md"
    kept.parent.mkdir(exist_ok=True)
    kept.write_text(text)
    job["questions"].append(state.new_question(str(kept), f"Restart the stopped job on {cut(job['goal'], 80)}?", state.stamp()) | {"hash": state.ask_hash(text)})
    return kept


def safeguard_cards(watcher: Watcher, moment: datetime) -> list[str]:
    """Draw and act on his card on each heard stopped job a model's safeguards stopped past its restarts: a question safeguard_ask kept with no card drawn goes up by cli.ask_card and cli.post_ask, as draw_questions draws one, which skips a stopped job, so a draw that fails goes up on a later pass, said from its third failure in a row; once his tap closes it on Restart, the job runs again by safeguard_restarted, and on Leave it stopped, or any other words of his, which reach the coordinator as his answer record does, it stays stopped. An Undo of his tap reopens the question, so a later Restart tap still restarts it."""
    from drive import cli

    data = state.load(watcher.name)
    lines = []
    for job_id, job in data["jobs"].items():
        if job["state"] != "stopped" or not job.get("safeguard_card") or not heard_job(watcher, data, job):
            continue
        file = Path(job["safeguard_card"])
        question = state.kept_in(job, file)
        if question["closed"] is None and not question["card"]:
            try:
                cli.post_ask(watcher.name, job_id, file, cli.ask_card(watcher.name, job_id, file))
            except (slack.SlackError, state.DriveError) as error:
                lines += failed(watcher, f"safeguards card {job_id}", f"draw of his card on {job_id}, which a model's safeguards stopped", error, moment)
                continue
            watcher.failures.pop(f"safeguards card {job_id}", None)
        elif (closed := question["closed"]) and (closed["how"], closed["words"]) == ("answered", SAFEGUARD_OPTIONS[0]) and not busy(watcher, job_id, moment):
            lines.append(safeguard_restarted(watcher, job_id, file, moment))
    return lines


def safeguard_restarted(watcher: Watcher, job_id: str, file: Path, moment: datetime) -> str:
    """Run a stopped job again on his Restart tap on its safeguards card, by his words "Restart relaunches the job by itself": in one write the job runs again, its card let go, and its safeguarded list reset to the one stop his card asked about, so its next stop restarts it on SAFEGUARD_MODEL and a third asks him again, safeguard_claim counting the try, then safeguard_restart starts it in a fresh Claude session on the job's default model, as the first restart does. A job another live job of its order took a part of since starts nothing, said, so no two jobs do one part's work. Its LANE line."""

    def restart_claimed(data: dict) -> tuple[str, dict | None]:
        job = data["jobs"][job_id]
        if job["state"] != "stopped" or job.get("safeguard_card") != str(file):
            return f"LANE {job_id} {job['order']}: his Restart tap on its safeguards card finds it {job['state']}, so the watch starts nothing", None
        job["safeguard_card"] = None
        if taken := [other_id for part in job["parts"] for other_id, other in state.part_jobs(data, job["order"], part) if other_id != job_id and other["state"] not in state.FINISHED_JOB_STATES]:
            return f"LANE {job_id} {job['order']}: his Restart tap on its safeguards card starts nothing, since {', '.join(dict.fromkeys(taken))} took its parts since it stopped", None
        stop = job["safeguarded"][-1] | {"tries": 0, "pid": None}
        job.update(state="running", said=f"restarted on his tap, {SAFEGUARDED} {times(len(job['safeguarded']))}", safeguarded=[stop], updated=state.stamp(moment))
        return "", safeguard_claim(job_id, job, stop, moment)

    said, plan = state.write(watcher.name, restart_claimed, budget=state.WATCH_WRITE_SECONDS)
    if plan is None:
        return said
    return safeguard_restart(watcher, {job_id: plan}, moment)[job_id] + ", on his Restart tap on its safeguards card"


def budget_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Read every drive's projects' spend today again, by spend.refresh, and tell him once a day, in one report each watch on this Mac posts at most once for it, of each project that spent its daily budget, whose work waits for the next day unless a priority or incident order serves it."""
    from drive import spend

    usd = spend.refresh(moment)["usd"]
    over = []
    with spend.ledger(moment) as held:
        for mine in sorted({bucket.rpartition("/")[2] for bucket in usd} - set(held["told"])):
            if spend.over_project(mine, moment):
                over.append(mine)
                held["told"].append(mine)
    if not over:
        return []
    lines = []
    for mine in over:
        try:
            milestone(watcher, {"kind": "report", "attr": {}, "topic": f"budget-{mine}-{moment.date().isoformat()}", "body": spend.his_line(mine) + "\n"}, spend.his_line(mine))
        except (slack.SlackError, state.DriveError) as error:
            lines += failed(watcher, f"budget {mine}", f"line to him that {mine} spent its daily budget", error, moment)
            continue
        lines.append(f"BUDGET {spend.his_line(mine)}")
    return lines


def stall_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Age each thing the drive's chains of waits end on, a PR stack's card, a PR's release, a staging env or a job, and act once on each that stood unmoved past its deadline, however many jobs and PRs wait on it, by his words "the watch reports a stall for each job held by the same open Approve card, so three jobs waiting on one card would each send him a line" and his pick "an automated watchdog that spawns a small agent if it detects anything truly stick which can go read transcript etc": start one watchdog agent on it, unless the drive's own rows name its cause, by stall.cause, a staging slot, a release, a builder or Sprite turn, a stopped job leaving a part no other job carries, a dead Sprite lane or his card, whose WHY, WHO and NEXT lines stand as that agent's answer with no agent started, at most two agents at a time and six an hour, oldest stall first, never on a chain ending at a step of his, nor on a stopped job whose parts other jobs took, whose waiters count as waiting on those jobs, by stall.moved, his hold on every release read once a pass, nor on a staging env, or a release on it, behind another drive's staging claim until that claim ends, as stall.behind() reads the claims of every other drive on this Mac once a pass; once that agent answers, or ten minutes pass, say one STUCK line naming it, each job and PR waiting on it and what the agent found, or that alone; and, by his pick "One line to you, once per stall, after the coordinator had 30 minutes and nothing changed; never while you are away.", once that line stood 30 minutes with the thing unmoved and he is here, start a fresh watchdog agent, since records and PRs move while the thing's own words do not, as a merged replacement fix or a re-cut ready line, and once it answers, or ten minutes pass, write him one report of that answer the communicator's handler posts in that work's thread, his presence read by the clock right before that agent starts and again right before that report is written, never at the pass's start, which the stage can run twenty minutes after, so while he reads away the step goes back unclaimed and the first pass after he is heard takes it. Each stall is one row of the drive's stalls, aged by every watch and acted on only by the watch hearing the job answering for the first job or PR waiting on it, each step claimed with the pid and start time of the watch taking it by a write that runs again when another write changed that row meanwhile, as waiting_lines claims its lines, then taken outside any write and settled by one, so a watch restart adopts its agent, two watches never take one step and nothing is said twice; a thing that moves starts a fresh one."""
    if watcher.cards is None:
        return []
    known = state.load(watcher.name)
    try:
        on_hold = bool(settled.hold(known["roles"]["coordinator"]["cwd"]))
    except state.DriveError as error:
        return failed(watcher, "stall hold", "stall read of his hold on every release", error, moment)
    watcher.failures.pop("stall hold", None)
    with state.indexed(known):
        found = stall.things(known, stall.chains(known, watcher.cards, on_hold, moment), state.others_claims(watcher.name, moment))
        printed = {thing: stall.words(known, watcher.cards, held) for thing, held in found.items()}
    aged = {thing: stall.aged(known["stalls"].get(thing), printed[thing], held["joins"], moment) for thing, held in found.items()}
    if aged == known["stalls"] and not owed(watcher, known, found, aged, moment):
        return []
    claimer = {"pid": os.getpid(), "started": started(os.getpid())}

    def claimed_steps(data: dict) -> tuple[dict[str, str], dict[str, dict]]:
        stalls = data["stalls"] = {thing: stall.aged(data["stalls"].get(thing), printed[thing], held["joins"], moment) for thing, held in found.items()}
        steps = owed(watcher, data, found, stalls, moment)
        for thing in steps:
            stalls[thing]["claim"] = claimer
        return steps, {thing: dict(stalls[thing]) for thing in steps}

    steps, claims = state.write(watcher.name, claimed_steps, budget=state.WATCH_WRITE_SECONDS)
    lines = []
    for thing, step in steps.items():
        held = claims[thing]
        if step == "agent":
            out = state.drive_dir(watcher.name) / "stalls" / f"{NOT_SLUG.sub('-', thing)}-{moment:%Y%m%dT%H%M%S}.md"
            if known_cause := stall.cause(known, thing, found[thing]):
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text("".join(f"{label}: {said}\n" for label, said in known_cause.items()))
                settle(watcher.name, thing, held, {"agent": {"pid": None, "out": str(out), "at": state.stamp(moment)}})
                continue
            try:
                pid = agents.watchdog(watcher.name, watchdog_brief(watcher, known, thing, found[thing], held, moment), str(orca.checkout(watcher.name, coordinator(known, watcher.project)["cwd"], wait=0)), out)
            except (OSError, state.DriveError) as error:
                settle(watcher.name, thing, held, {})
                lines += failed(watcher, "watchdog", f"watchdog agent on {thing}", error, moment)
                continue
            watcher.failures.pop("watchdog", None)
            settle(watcher.name, thing, held, {"agent": {"pid": pid, "out": str(out), "at": state.stamp(moment)}})
        elif step == "said":
            done = {"found": watchdog_answer(held), "said": state.stamp(moment)}
            settle(watcher.name, thing, held, done)
            lines.append(stuck_line(known, thing, found[thing], held | done, moment))
    return lines


def watchdog_answer(held: dict) -> dict[str, str]:
    """What the stall's newest watchdog agent answered."""
    return stall.answer(Path(held["agent"]["out"]).read_text())


def owed(watcher: Watcher, data: dict, found: dict[str, dict], stalls: dict[str, dict], moment: datetime) -> dict[str, str]:
    """The step each stall past its deadline owes now, oldest first, of those this watch hears and no live watch claims: its agent while the watchdog caps have room and this Mac admits a local Claude lane, by place.admission, so a stall it holds back waits for a later pass, but at once and past both for a stall whose cause the drive's rows name, by stall.cause, which starts no agent, its STUCK line, or his report, whether he is away read by the clock, never moment, the pass's start."""
    running, begun = stall.starts(stalls, moment)
    room = 0 if place.admission(data, None, "local", moment) else min(stall.AT_ONCE - running, stall.STARTS_AN_HOUR - begun)
    steps = {}
    for thing in stall.stalled(data, stalls, found, moment):
        if claimed(stalls[thing]["claim"]) or not heard_job(watcher, data, data["jobs"][stall.owner(data, found[thing]["subjects"][0])[0]]):
            continue
        step = stall.step(stalls[thing], moment)
        if step == "agent" and not stall.cause(data, thing, found[thing]):
            if room <= 0:
                continue
            room -= 1
        if step:
            steps[thing] = step
    return steps


def settle(name: str, thing: str, held: dict, done: dict) -> None:
    """Give back a stall's claim, keeping what its step did, on the row it was claimed on; a stall that moved meanwhile is a fresh row and keeps nothing."""
    def settled_stall(data: dict) -> None:
        if (kept := data["stalls"].get(thing)) and kept["since"] == held["since"]:
            kept |= {"claim": None} | done

    state.write(name, settled_stall, patient=True)


def claimed(claim: dict | None) -> bool:
    """Whether the watch that claimed a step still runs: its pid holds the process that started when the claim says, never one the system handed that pid since."""
    return bool(claim) and started(claim["pid"]) == claim["started"]


def next_worker(data: dict, job_id: str) -> str:
    """Who starts the next worker on an ended lane's job, as a watchdog brief names it: for a stopped job, which drive job resume refuses, the jobs carrying its parts, by stall.successors, else the drive job add taking the parts no job carries, by stall.taker_add, or that none starts once it leaves no part owed; for any other, the holder's drive job resume."""
    if data["jobs"][job_id]["state"] != "stopped":
        return f"only the holder's drive job resume {job_id} starts its next worker"
    if parts := stall.uncarried(data, job_id):
        return f"{job_id} is stopped, so drive job resume refuses it: {stall.taker_add(data, job_id, parts)} takes its work on"
    if carriers := [other for other in stall.successors(data, job_id) if other != job_id]:
        return f"{job_id} is stopped and {', '.join(carriers)} carries its parts, so its next step is theirs"
    return f"{job_id} is stopped and leaves no part owed, so no worker of it starts again"


def watchdog_brief(watcher: Watcher, data: dict, thing: str, found: dict, held: dict, moment: datetime) -> str:
    """The brief a watchdog agent starts on: the stall, each job and PR waiting on it, their chains to it, the session holding the order of the first of them, which its STUCK line reaches and which alone takes the next step, with how a job's agent on this Mac is woken, or that the job's lane or agent ended, by state.lane_death, so no wake reaches it, what to read, first the newest records naming the stall's jobs, orders and PRs, newest first, then the drive's rows of everything in the chains, the job's returns and its agent's transcript, the holding session's transcript since the thing last moved, and each PR in the chains as GitHub holds it now, merged or not and the checks at its head, then what the chains' releases and builds rest on, which the agent reads nowhere else: a Release line per release wait in them, what the watch's own read of that wait last found, a Staging line per service a staging wait in them awaits on its env, by staging_line, a Build line per Buildkite build they wait on, what the watch's last Buildkite read of it found, a Card line per open card of his on the chains or naming a service they wait on, and an Answered line per recorded answer of his ccn ranks nearest the job's goal and those services, so it names the answer that settles what it would ask him and orders no card asking it again."""
    job_id, order_id = stall.owner(data, found["subjects"][0])
    job = data["jobs"][job_id]
    holder, holding = state.holding_role(data, data["orders"][order_id])
    named = dict.fromkeys([order_id, job_id, *(link for link in found["chain"] if link in data["jobs"] or link in data["prs"])])
    reading = [
        f"cci grep '({'|'.join(record_names(data, [*named, *found['chain'], *found['members']]))})\\b' --drive {watcher.name} --limit 30",
        f"drive status {' '.join(named)} --drive {watcher.name}",
        f"drive job replies {job_id} --drive {watcher.name}",
    ]
    wake = ""
    if job.get("agent") and (runs_in := session_of(job, data)):
        reading.append(f"cc-transcript {agent_transcript(runs_in, job, data)}")
        wake = f"; {job_id} runs on this Mac as agent {job['agent']} of session {runs_in['session']}; to wake it: {superseded.delivery(data, job_id, job, '', 'the message')[1]}"
    if ended := state.lane_death(job):
        wake = f"; {job_id}'s lane ended at {when(ended['at'])}: {state.why_died(ended['why'], ended.get('cause'))}, so neither drive job wake nor SendMessage reaches it, and {next_worker(data, job_id)}"
    if holding:
        reading.append(f"cc-transcript {session_transcript(holding)}, from {when(held['since'])} on")
    reading += [f"gh pr view {state.pr_url(key)} --json state,mergedAt,headRefOid,statusCheckRollup" for key in named if key in data["prs"]]
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
            *(f"Build: {build_line(watcher, link)}" for link in found["chain"] if providers.of(data).build.fullmatch(link)),
            *(f"Card: {card}" for card in stall_cards(data, found["chain"], watcher.cards)),
            *(f"Answered: {answer}" for answer in stall_answers(data, job, found["chain"])),
        ]
    )


def record_names(data: dict, links: list[str]) -> list[str]:
    """The names records give a stall's links, each once: a job or order by its id, a part wait by its order, and a PR, or the PR a release wait awaits, by #<number>, as a record's refs and text name it."""
    names = {}
    for link in links:
        if link in data["jobs"] or link in data["orders"]:
            names[link] = None
        elif part := state.part_wait(data, link):
            names[part[0]] = None
        elif key := link if link in data["prs"] else (release := state.RELEASE_WAIT.fullmatch(link)) and release["pr"]:
            names[f"#{key.split('#')[1]}"] = None
    return list(names)


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
        return f"{wait}: the watch's release live read holds no line for {key}, as it failed or has not run since the wait was added"
    return f"{wait}: {service} on {found['env'] or ', '.join(sorted(env for env, serves in clusters.items() if not serves))} is behind {key}'s merge commit {pr['merged_sha'][:12]}, running an older build"


def staging_line(watcher: Watcher, target: str, env: str, commits: list[str]) -> str:
    """A service staging waits await on one env, with each commit they await there and the commit and release the watch's last release live read finds it serving, or that the read finds it on no cluster there or has not run."""
    awaited = f"{target} on {env}, awaited at {', '.join(commits)}"
    if watcher.live is None:
        return f"{awaited}: the watch has made no release live read since it started"
    if not (row := next((row for row in watcher.live if row["target"] == target and row["env"] == env), None)):
        return f"{awaited}: the watch's last release live read finds no {target} on {env}"
    shipped = row.get("name") or row.get("release")
    return f"{awaited}: the watch's last release live read finds it serving {row['commit'][:12]}" + (f" of {shipped}" if shipped else "")


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
    tracked = state.load(watcher.name, ("prs",))["prs"]
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


def check_disks(watcher: Watcher, moment: datetime) -> list[str]:
    """Start drive disks-check as a process of its own, by state.detach, once DISKS_EVERY has passed since the last one started, its start kept as the mtime of the drive's DISKS_STARTED, so a Sprite lane's wedged nbd0 disk is checkpoint-restored within minutes; nothing under a scratch state root, whose proofs run no Sprites."""
    stamp = state.drive_dir(watcher.name) / DISKS_STARTED
    if watcher.project or state.scratch() or stamp.exists() and moment.timestamp() - stamp.stat().st_mtime < DISKS_EVERY.total_seconds():
        return []
    stamp.touch()
    os.utime(stamp, (moment.timestamp(), moment.timestamp()))
    state.detach(watcher.name, ["disks-check"], DISKS_LOG)
    return []


def release_lanes(watcher: Watcher, moment: datetime) -> list[str]:
    """Free what each job it hears is done with still holds, never what a running job holds, starting each drive verb that frees one as a process of its own, by state.detach, and keeping that process's pid and time on the drive, so a restarted watch reads what every earlier watch asked. First, by one rule, a remote job that is not running holds no Sprite once its worker's turn is over: the own lane of each job whose line set it done, failed or stopped, or set it waiting or built and whose worker then ended its turn or died, by unended, so a live lane waiting on its own question or on a read it asked keeps it, is ended by `drive job end`, which keeps its transcripts, saves what its Sprite holds on no remote and destroys it, every due job's in the pass that reads the event ending its turn, so lanes end as fast as they start, kept as the job's end, so no watch ends that lane again while that end is in_flight, and its Sprite left to that end alone while the end it records on the lane is, ahead of every reclaim, so no reclaim and no install delays an end. Then each Sprite cc-remote still records that an earlier or ended lane of any job left, such as the Sprite a relaunch moved off or one an end kept, destroyed with its Orca environment by --destroy once what it holds on no remote is saved to GitHub as saved/<the job's lane>/<branch>, or else kept by that reclaim, said once and never asked for again; and the dispatch of a done, failed or stopped lane on this Mac once its job has been quiet for QUIET_LANE, as lr's gc --done waited after a lane's last word, whose terminal --run --done closes and whose worktree it removes once clean and pushed, the reclaimer skipping whatever its pauses hold, its death recorded first. Each of those goes to `drive job reclaim`, whose orca.reclaimer kills the reclaimer with every process it started past RECLAIM_SECONDS, kept as the reclaim on the death of the lane that left it, and none asked again while its own is in_flight; what each run printed goes to the watch log."""
    if state.scratch():
        return []
    lines = adopt_sprites(watcher, moment) + finished_boxes(watcher, moment)
    data = state.load(watcher.name)
    heard = {job_id: job for job_id, job in data["jobs"].items() if heard_job(watcher, data, job)}
    ending = [job_id for job_id, job in heard.items() if unended(job, moment)]
    live = {job.get(key) for job in data["jobs"].values() if job["state"] not in state.FINISHED_JOB_STATES for key in ("workspace", "run", "dispatch")} - {None}
    reclaiming = [(job_id, lane) for job_id, job in heard.items() for lane in sorted(lane_holds(job, moment, live) - live) if not in_flight((state.death_on(job, lane) or {}).get("reclaim"), moment)]
    if not ending and not reclaiming:
        return lines
    log = state.watch_log_path(watcher.name, watcher.project).name
    claim = {"pid": os.getpid(), "at": state.stamp(moment)}

    def claim_lanes(data: dict) -> tuple[dict[str, state.Reservation], dict[tuple[str, str], object]]:
        ends, reclaims = {}, {}
        for job_id in ending:
            job = data["jobs"][job_id]
            if not in_flight(job.get("end"), moment) and (held := state.reserve(data, (("jobs", job_id, "end"),))):
                job["end"] |= {"at": claim["at"]}
                ends[job_id] = held
        for job_id, lane in reclaiming:
            job = data["jobs"][job_id]
            if in_flight((state.death_on(job, lane) or {}).get("reclaim"), moment):
                continue
            if state.on_mac(job) and lane == job.get("dispatch"):
                state.lane_died(job, lane, RECLAIMED, moment)
            reclaims[job_id, lane] = state.death_on(job, lane).get("reclaim")
            state.death_on(job, lane)["reclaim"] = dict(claim)
        return ends, reclaims

    ends, reclaims = state.write(watcher.name, claim_lanes, budget=state.WATCH_WRITE_SECONDS)
    unlaunched = dict(ends)
    spawned: dict[tuple[str, str], int] = {}

    def own(data: dict) -> None:
        for (job_id, lane), before in reclaims.items():
            death = state.death_on(data["jobs"][job_id], lane)
            if death.get("reclaim") != claim:
                continue
            if (job_id, lane) in spawned:
                death["reclaim"] = claim | {"pid": spawned[job_id, lane]}
            elif before is None:
                death.pop("reclaim")
            else:
                death["reclaim"] = before

    try:
        for job_id, held in ends.items():
            pid = end_lane(watcher.name, job_id, held.token)
            del unlaunched[job_id]
            state.settle(watcher.name, held, pid)
        for job_id, lane in reclaims:
            spawned[job_id, lane] = state.detach(watcher.name, ["job", "reclaim", job_id, lane], log)
    finally:
        for held in unlaunched.values():
            state.cancel(watcher.name, held)
        if reclaims:
            state.write(watcher.name, own, patient=True)
    return lines


def rest_sessions(watcher: Watcher, moment: datetime) -> list[str]:
    """End the own `claude --bg` session of each job the watch hears that is not running once that session's turn is over and its process still runs, by agents.end_bg under the job's own lock, passing over a job whose lock a resume or launch holds: removed for a done, failed or stopped job, so it never runs again, and stopped for a waiting or built one, its background job kept, so the text that clears its wait resumes that same session in place by agents.resume_job; so no job's idle session holds memory while it waits on a PR, CI or his card."""
    if state.scratch():
        return []
    data = state.load(watcher.name)
    records = agents.session_records()
    resting = [(job_id, session) for job_id, job in data["jobs"].items() if heard_job(watcher, data, job) and job["state"] != "running" and (session := state.background_session(job)) and agents.alive(session, records) and agents.gone(job["bg"])]
    for job_id, session in resting:
        try:
            with state.job_locked(watcher.name, job_id, 0):
                job = state.load(watcher.name)["jobs"][job_id]
                if job["state"] != "running" and state.background_session(job) == session and agents.alive(session, agents.session_records()) and agents.gone(job["bg"]):
                    agents.end_bg(session, Path(job["bg"]["cwd"]), removed=job["state"] in state.FINISHED_JOB_STATES)
        except state.DriveError:
            continue
    return []


def finished_boxes(watcher: Watcher, moment: datetime) -> list[str]:
    """Run the drive's Mac box pool once every BOX_SWEEP, each step a process of its own: give back, by `drive mac-boxes-down`, each live box held by a job of this drive the watch hears that is not running, unless state.box_wanted keeps it; then hand boxes, by `drive mac-boxes-hand`, to the jobs state.mac_queue holds, a priority order's first; it never scales the pool down, which `drive mac-boxes-reap` does only when run by hand; the lines a failed list makes."""
    if watcher.boxes_at is not None and moment - watcher.boxes_at < BOX_SWEEP:
        return []
    data = state.load(watcher.name)
    watcher.boxes_at = moment
    try:
        boxes = providers.of(data).mac_box.live_boxes()
    except providers.ProviderError as error:
        return read_failed(watcher, BOX_LIST, error, moment)
    if boxes is None:
        return []
    watcher.failures.pop(BOX_LIST, None)
    holders = {job_id for drive, _, job_id in (holder.partition("/") for holder in boxes) if drive == watcher.name}
    held = [job_id for job_id in sorted(holders) if (job := data["jobs"].get(job_id)) and job["state"] != "running" and heard_job(watcher, data, job) and not state.box_wanted(data, job_id)]
    if held:
        state.detach(watcher.name, ["mac-boxes-down", *held], state.MAC_BOXES_LOG)
    if queued := state.mac_queue(data):
        state.detach(watcher.name, ["mac-boxes-hand", *queued], state.MAC_BOXES_LOG)
    return []


def adopt_sprites(watcher: Watcher, moment: datetime) -> list[str]:
    """Record as a death of its job each live Sprite of a lane of a job the watch hears on a Sprite, by pool_claims, that neither the job's row nor any death of it names, at once when the job is done, failed or stopped, else once its records last moved ADOPT_AFTER ago or more, while the job's row names the Sprite its current lane runs on and this Sprite's lane is not that lane, since a job whose launch recorded no Sprite or whose current lane's Sprite this is may have its live worker on it, and so keeps it; and no launch of the job is in flight, as a launch that failed after its claim or a stop that came before the job recorded its Sprite leaves one, so lane_holds hands it to drive job reclaim like any Sprite a dead lane left."""
    try:
        claimed = pool_claims(watcher.name)
    except orca.OrcaError as error:
        return read_failed(watcher, SPRITE_LIST, error, moment)
    watcher.failures.pop(SPRITE_LIST, None)
    if not claimed:
        return []
    data = state.load(watcher.name)
    found = {
        (job_id, workspace)
        for workspace, (job_id, lane, moved) in claimed.items()
        if (job := data["jobs"].get(job_id)) and heard_job(watcher, data, job) and not state.on_mac(job)
        and (job["state"] in state.FINISHED_JOB_STATES or job.get("workspace") and moment - moved >= ADOPT_AFTER and lane != orca.lane_of(watcher.name, job_id, job))
        and job.get("workspace") != workspace and not state.death_on(job, workspace) and not state.launch_in_flight(watcher.name, job_id, job)
    }
    if not found:
        return []
    def adopt_sprites_write(data: dict):
        for job_id, workspace in found:
            job = data["jobs"][job_id]
            if job.get("workspace") != workspace and not state.death_on(job, workspace):
                job.setdefault("deaths", []).append({"dispatch": None, "workspace": workspace, "why": ADOPTED, "at": state.stamp(moment)})

    state.write(watcher.name, adopt_sprites_write, budget=state.WATCH_WRITE_SECONDS)
    return []


def pool_claims(drive: str) -> dict[str, tuple[str, str, datetime]]:
    """Each Sprite `sprite list` names that cc-remote still records a workspace for, whose lane, by its cc-remote pool record in whatever state that record is, else by its own name, is a lane of the drive's, a record cc-remote removes as it is read counting as none, with the job whose lane it is, that lane and when its pool record, else its workspace record, last moved; so a Sprite whose pool record went stopped or failed without a destroy, or that a lane started by name with no pool record, is found as well as a claimed one."""
    lane = re.compile(rf"{re.escape(orca.lane_name(drive, 'j0')[:-2])}(j\d+)(?:-\d+)?")
    claims = {}
    for name in orca.sprites():
        workspace, pooled = orca.CC_REMOTE_WORKSPACES / f"{name}.json", orca.CC_REMOTE / "pool" / f"{name}.json"
        if not workspace.is_file():
            continue
        for path in (pooled, workspace):
            with contextlib.suppress(FileNotFoundError):
                record = json.loads(path.read_text())
                break
        else:
            continue
        if named := lane.fullmatch(record.get("lane") or name):
            claims[name] = (named[1], named[0], datetime.fromisoformat(record["updatedAt"]).astimezone())
    return claims


def in_flight(started: dict | None, moment: datetime) -> bool:
    """Whether a drive verb the watch started to free a lane, by the reservation or the pid and time kept on its job, still runs or started within RECLAIM, so the watch asks for that lane again only once that process is gone and RECLAIM_SECONDS, past which orca.reclaimer kills a reclaimer, have passed since it started; a process alive past the time a relaunch takes is another that took its pid; a reservation its starter cancelled, or whose starter died before a child adopted it, ran nothing, so the watch asks again at once."""
    if not started:
        return False
    age = moment - state.parse_time(started["at"])
    if "state" in started:
        return state.holder(started) is not None and age < RELAUNCH or started["state"] == "adopted" and age < RECLAIM
    return age < RECLAIM or state.alive(started["pid"]) and age < RELAUNCH


def unended(job: dict, moment: datetime) -> bool:
    """Whether the job's own lane on a Sprite cc-remote still records is due its `drive job end`: the job is not running, its worker's turn is over, by a worker_done or escalation from that worker or its death, with no text queued for it unread, whose read reopens its turn and whose wait starts the next by wake_lines, and no end ended that lane nor is in_flight on it; a done, failed or stopped job whose worker's turn is still open is due once HANDOFF has passed since the line that stopped it, or at once when it was stopped with none, so the lane finishes its last turn. So a waiting or built job whose worker still waits inside its own question, his ASK's included, which Orca keeps that worker's turn open for, or on a read it asked after its line, keeps its Sprite, and the answer `drive job resume` or its reader sends reaches that worker in its own session."""
    settled = not job["returned"] or moment - state.parse_time(job["returned"]) >= HANDOFF
    turn_over = not (job["turn_open"] or job["queued"]) or bool(state.lane_death(job)) or job["state"] in state.FINISHED_JOB_STATES and settled
    return job["state"] != "running" and turn_over and not state.lane_ended(job) and not in_flight(job.get("end"), moment) and orca.holds_sprite(job)


def lane_holds(job: dict, moment: datetime, live: set[str]) -> set[str]:
    """What the job's lanes left for the reclaimer: on this Mac, each dispatch no drive job reclaim freed of an earlier lane its deaths record, QUIET_LANE after that death whatever the job's state, since a relaunch, a takeover or a resume in another pane leaves the lane before it running in Orca, and, once a done, failed or stopped job is quiet for QUIET_LANE and no unfinished job shares its Run, its last lane's too; on a Sprite, each Sprite cc-remote still records that a lane of its died on or an end ended, never its own lane's before an end ended it, since unended gives that one to `drive job end`, nor while the end that ended it is in_flight, which destroys it itself, nor one a drive job reclaim kept on the newest death on it, since what it may hold on no remote could not be saved, which that reclaim's refusal says once, so the watch asks for it once and a session asks again by hand."""
    if state.on_mac(job):
        quiet = job["state"] in state.FINISHED_JOB_STATES and job.get("run") not in live | {None} and moment - state.parse_time(job["updated"]) >= QUIET_LANE
        left = {death["dispatch"] for death in job.get("deaths", []) if not death.get("workspace") and (quiet or moment - state.parse_time(death["at"]) >= QUIET_LANE)}
        lanes = (left | {job.get("dispatch")} if quiet else left - {job.get("dispatch")}) - {None}
        return {lane for lane in lanes if not (state.death_on(job, lane) or {}).get("freed")}
    left = {death.get("workspace") for death in job.get("deaths", [])}
    if not state.lane_ended(job) or in_flight(state.lane_death(job, "dispatch")["ended"], moment):
        left.discard(job.get("workspace"))
    return {workspace for workspace in left - {None} if not kept_lately(state.death_on(job, workspace).get("kept"), moment) and orca.holds_sprite({**job, "workspace": workspace})}


def kept_lately(kept: str | None, moment: datetime) -> bool:
    """Whether a reclaim kept a Sprite within KEPT_RETRY, since what it holds on no remote could not be read or saved, so the watch asks for it again only after that, as a Sprite ssh could not reach then may answer now; it is never destroyed unsaved."""
    return bool(kept) and moment - state.parse_time(kept) < KEPT_RETRY


def death_lines(watcher: Watcher, deaths: dict[str, tuple[str, str]], moment: datetime) -> list[str]:
    """Record each worker's death on its job: a remote lane's, by its dispatch, or a running local job's agent's, by its agent, and say it once for a running job, whose worker is replaced that pass by `drive job resume` as a process of its own, its pid kept on the death so no later pass starts a second: a Sprite lane's is resumed in its own Claude session on its Sprite, or else relaunched, and an agent's is handed to a lane on a Sprite, or on this Mac when its brief declares a step only this Mac runs. A running job busy reads another drive verb acting on has its death neither recorded nor said, so the first pass after that verb ends relaunches it, or reads the next worker that verb started. A waiting job's death asks no step, since his answer reaches it through drive job resume whether or not its lane lives, so it is recorded and not said: its Sprite is freed by the `drive job end` release_lanes runs on every remote job that is not running once its worker's turn is over, as its death makes it, which copies its Sprite's Orca runtime and worker logs to the drive's folder before the Sprite goes, and its lane on this Mac just waits; a job that runs again on the lane that died while it waited has that death relaunched once, as if it died running. A job stopped, done or failed since the pass read it has the death recorded and nothing started or said. A running Sprite lane's line names the file in the drive's folder that resume copies those logs to, and, when another lane of the job died with a cause since its last line, where and when that one died, never its cause, since the resume judges this death from those logs alone and moves the job to this Mac unless those logs show its Sprite rebooted after its worker started, failed on its own or did not answer while GitHub holds the job's branch."""
    if not deaths:
        return []
    at = state.stamp(moment)

    def claim_relaunches(data: dict) -> tuple[list[str], dict[str, object]]:
        lines, claims = [], {}
        for job_id, (worker, why) in deaths.items():
            job = data["jobs"][job_id]
            if job["state"] in state.FINISHED_JOB_STATES or job.get("where") == "remote" and job["state"] == "waiting":
                state.lane_died(job, worker, why, moment)
                continue
            if job.get("where") != "remote" and not job.get("agent") and (job.get("bg") or {}).get("agent") == worker:
                job["agent"] = worker
            if job[state.worker_key(job)] != worker or not relaunchable(state.lane_death(job), moment) or busy(watcher, job_id, moment):
                continue
            state.lane_died(job, worker, why, moment)
            claims[job_id] = state.lane_death(job).get("relaunch")
            tries = (claims[job_id] or {}).get("tries", 0) + 1
            state.lane_death(job)["relaunch"] = {"pid": os.getpid(), "at": at, "tries": tries}
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
            lines.append(
                f'LANE {job_id} {job["order"]} died {orca.place(job)}: {why}, while it worked on "{cut(job["said"] or job["goal"], 60)}".'
                f" The watch ran drive job resume {job_id}, which {logs}{again}, its output in the drive's {RELAUNCHES_LOG}"
            )
        return lines, claims

    lines, claims = state.write(watcher.name, claim_relaunches, budget=state.WATCH_WRITE_SECONDS)
    spawned: dict[str, int] = {}

    def own(data: dict) -> None:
        for job_id, before in claims.items():
            for death in data["jobs"][job_id]["deaths"]:
                if (claim := death.get("relaunch")) and claim["pid"] == os.getpid() and claim["at"] == at:
                    if job_id in spawned:
                        death["relaunch"] = claim | {"pid": spawned[job_id]}
                    elif before is None:
                        death.pop("relaunch")
                    else:
                        death["relaunch"] = before

    try:
        for job_id in claims:
            spawned[job_id] = relaunch(watcher, job_id, moment)
    finally:
        if claims:
            state.write(watcher.name, own, patient=True)
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


def send_held(watcher: Watcher, job_id: str, moment: datetime) -> int:
    """Run `drive job resume` with no text by start_verb on a job holding texts a resume could not deliver, once Orca reads its worker alive and no wait on builder-slot holds it to move here, so the pass never waits on Orca or the job's lock; each goes under the request id it was held with, so Orca never applies one twice."""
    return start_verb(watcher, job_id, ["job", "resume", job_id], moment)


def starts_itself(job: dict) -> bool:
    """Whether the watch starts a job's first worker itself, by start_jobs: a briefed builder, reader or design job no agent, lane or start took yet, on this Mac by drive job start, or on a Sprite by drive job launch when it was placed there."""
    return job["kind"] in state.STARTS_ITSELF and bool(job.get("brief")) and not (job.get("agent") or job.get("dispatch") or job.get("bg"))


def starts_here(watcher: Watcher, data: dict, job: dict, first: bool) -> bool:
    """Whether this watch's start_jobs starts the job: in a first_starts call, a job its own watch hears, so each priority seat's watch starts its first-in-line work every pass; otherwise the drive's own watch alone, for a job of any lane but a first-in-line one a seat's watch hears, since a project's pass comes round only every ten to twenty minutes while the drive's comes every few, and a starved project's briefed builders waited hours for its turn."""
    lane = state.heard_job_lane(data, job)
    if first:
        return lane == watcher.lane
    return not watcher.project and (lane == watcher.lane or not (job.get("priority") or state.first_in_line(data, job)))


def start_jobs(watcher: Watcher, moment: datetime, first: bool = False) -> None:
    """Start each heard job's first worker that nothing holds, so no session's Agent call or launch starts it and no root relays it: `drive job start` for a briefed builder or reader on this Mac, which starts its agent as its own claude --bg session past every check an Agent start meets, counting its builder slot or holding it waiting its turn, and `drive job launch` for one placed on a Sprite, each by start_verb once busy lets it and at most once per RELAUNCH a watch, at most START_AT_ONCE a pass, a job whose brief declares no step only this Mac runs, by place.mac_step, launched on a Sprite at once beside the rest, taking no place of that count, or queued on sprite-slot under the Sprite cap, whose line launches it as a lane frees, a local job this Mac's load or caps hold back taking no place of that count, a builder of them queued waiting its turn on builder-slot with its place, whose FREE line starts it when a slot frees, and a builder already queued so left to that line, since refused starts heading the jobs every pass once kept about 25 briefed jobs from ever starting but for a priority order's, which first takes past that count, so a burst of freed jobs starts over several passes, and a refusal, which reaches the session holding the job as its relaunch_refused event, leaves the job to that session. A job no order of a coordinator or the meta session holds, such as a handler's, a stopped or finished one, one whose cards or waits hold it, by view.start_refusal, or one launching is left alone. With first, only a job drive job set --priority marks or one serving an open priority order, by state.first_in_line, which first_starts runs on every pass."""
    data, room, queued, sprites = state.load(watcher.name), START_AT_ONCE, [], []
    for job_id, job in data["jobs"].items():
        if first and not (job.get("priority") or state.first_in_line(data, job)):
            continue
        if room <= 0 and not first:
            break
        if not starts_itself(job) or work.queued(data, job) or job["state"] in (*state.FINISHED_JOB_STATES, "running") or state.SLOT_WAIT in job["waits"] or state.SPRITE_WAIT in job["waits"] or moment - watcher.started.get(job_id, moment - RELAUNCH) < RELAUNCH or not starts_here(watcher, data, job, first) or view.open_cards(job) or launching(job, moment):
            continue
        if state.holding_lane(data, data["orders"][job["order"]]) == "communicator" or view.start_refusal(data, job_id, False) or busy(watcher, job_id, moment):
            continue
        if job.get("where") == "remote" or not place.mac_step(Path(job["brief"]).read_text()):
            if place.admission(data, job_id, "sprite", moment):
                sprites.append(job_id)
            else:
                start_verb(watcher, job_id, ["job", "launch", job_id], moment)
            continue
        if place.crowded(data, job_id, moment) if job["kind"] == "builder" else place.admission(data, job_id, "local", moment):
            queued += [job_id] if job["kind"] == "builder" and place.queueable(data, job_id) else []
            continue
        start_verb(watcher, job_id, ["job", "launch", job_id] if job.get("where") == "remote" else ["job", "start", job_id], moment)
        room -= 1
    if queued or sprites:
        def start_jobs_write(data: dict) -> None:
            for job_id in queued:
                if place.queueable(data, job_id) and state.unstarted(data["jobs"][job_id]):
                    place.queue(data["jobs"][job_id], moment)
            for job_id in sprites:
                if state.unstarted(data["jobs"][job_id]):
                    place.queue_sprite(data["jobs"][job_id], moment)

        state.write(watcher.name, start_jobs_write, budget=state.WATCH_WRITE_SECONDS)


def first_starts(watcher: Watcher, moment: datetime) -> list[str]:
    """Start each job serving an open priority order that nothing holds, by start_jobs, on every pass right after its orphans, ahead of the READ_EVERY reads, the slow stages and a resumed pass's skip past start_jobs, by his words on a priority order, "they aren't slowed down at any cost by anything"."""
    start_jobs(watcher, moment, first=True)
    return []


def seated_first(watcher: Watcher) -> list[Watcher]:
    """The drive's watcher and each project's, each open priority order's seat first, so its pass runs before every other project's PASS_FOR."""
    seats = {order["priority"] for order in state.open_orders(state.load(watcher.name, ("orders",))).values() if order["priority"]}
    return sorted([watcher, *watcher.others.values()], key=lambda one: one.project not in seats)


def pass_reads(name: str, data: dict, prs: list[str]) -> tuple[list[str] | None, list[str] | None]:
    """Every file a staging pass's PRs change, by github.paths, and every component they reach, by cli.release_reach, the repository's own reach read, which slot_turns packs passes by; None for each that a pass naming no PR, or whose PRs GitHub or the release provider could not read, leaves unread, so it rides no other pass's cut."""
    from drive import cli

    if not prs:
        return None, None
    try:
        read = github.paths(prs)
    except github.GithubError:
        return None, None
    paths = sorted({path for key in prs if key in read for path in read[key][1]}) if all(key in read for key in prs) else None
    try:
        reach = cli.release_reach(name, data, prs)
    except (github.GithubError, reads.ReadError, state.DriveError, json.JSONDecodeError):
        return paths, None
    return paths, sorted({component for key in prs if key in reach for component in reach[key]["touched"]}) if all(key in reach for key in prs) else None


READS_PER_PASS = 12


def slot_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Hand on each staging slot, by state.slot_turns: end a lease whose job finished, extend once a lease that ran past its end and end it at its second, and give a free slot to the first job in its queue with a GO, packed with each later pass whose PRs change none of its files, up to six, each of those resumed with its RIDE line, every resume by `drive job resume`, so no session sequences or packs staging by hand; it reads the files of each queued pass whose files it has not read while the slot is held, so a pass is packed the moment the slot frees. Its lines go as evidence and wake nobody; a GO's resume that is refused reaches the session holding the job as its relaunch_refused event."""
    if watcher.project or not (slots := state.load(watcher.name, ("slots",))["slots"]):
        return []
    known = state.load(watcher.name)
    release = providers.of(known).release
    unread = {each["job"]: pass_reads(watcher.name, known, each["prs"]) for each in [each for slot in slots.values() for each in slot["queue"] if (each["paths"] is None or each["stacks"] is None) and each["prs"]][:READS_PER_PASS]}
    def slot_lines_write(data: dict):
        for slot in data["slots"].values():
            for each in slot["queue"]:
                if each["job"] in unread:
                    paths, stacks = unread[each["job"]]
                    each["paths"] = each["paths"] if paths is None else paths
                    each["stacks"] = each["stacks"] if stacks is None else stacks
        waiters = state.queue_waiters(data, approved_only=any(slot.get("hold") for slot in data["slots"].values()))
        return waiters, state.slot_turns(data, moment, release)

    waiters, moved = state.write(watcher.name, slot_lines_write, budget=state.WATCH_WRITE_SECONDS)
    lines = list(waiters)
    for env, job_id, kind, line in moved:
        if kind == "go":
            start_verb(watcher, job_id, ["job", "resume", job_id, line], moment)
            line = f"SLOT {env} to {job_id}: {line}"
        elif kind == "start":
            start_verb(watcher, job_id, ["job", "release", job_id], moment)
        lines.append(line)
    return lines


def work_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Run the work queue's spawner on a drive that turned any kind of work over to it: read the files and stacks of ready stage-test items it has not read, so packing pairs only passes it can see, take the staging slot's old line into the queue, and record and start a worker of each kind whose items have none and that this Mac admits, by work.spawned, an urgent head past the cap; its lines go as evidence and wake nobody."""
    data = state.load(watcher.name)
    if watcher.project or not data.get("work", {}).get("kinds"):
        return []
    read = {job_id: pass_reads(watcher.name, data, state.tested_prs(data, data["jobs"][job_id])) for job_id in work.unread(data)}

    def work_lines_write(data: dict) -> tuple[list[str], list[str]]:
        for job_id, (paths, stacks) in read.items():
            if paths is not None and stacks is not None:
                data["jobs"][job_id]["work_reads"] = {"paths": paths, "stacks": stacks}
        return work.spawned(data, moment)

    lines, workers = state.write(watcher.name, work_lines_write, budget=state.WATCH_WRITE_SECONDS)
    for worker_id in workers:
        start_verb(watcher, worker_id, ["work", "start", worker_id], moment)
    return lines


def carry_held(watcher: Watcher, moment: datetime) -> None:
    """Carry the texts each heard job holds, his answers among them, with no session's turn, once his cards ask nothing of the job: `drive job resume` for a lane on a Sprite or this Mac whose job returned, its worker ended or not, which sends them to a worker that takes messages and hands them to the next worker of one that finished, and `drive job launch` for an agent on this Mac that returned and that no session reaches by state.agent_lane, whose job it hands to a lane on a Sprite, and for a job no agent or lane started once its waits end, which it briefs and launches with them under His answers since:, on this Mac when its brief declares a step only this Mac runs. Each runs by start_verb once busy lets it, for each new text and again every ten minutes while one stays held."""
    data = state.load(watcher.name)
    for job_id, job in data["jobs"].items():
        held = {kept["request"] for kept in job["undelivered"]}
        if not held or work.queued(data, job) or job["state"] in state.FINISHED_JOB_STATES or not heard_job(watcher, data, job) or view.open_cards(job) or launching(job, moment):
            continue
        if busy(watcher, job_id, moment) or (tried := watcher.carried.get(job_id)) and held <= tried[0] and moment - tried[1] < DUE_EVERY:
            continue
        if command := carrier(job_id, job, data, watcher.cards):
            start_verb(watcher, job_id, command, moment)
            watcher.carried[job_id] = (held, moment)


def carrier(job_id: str, job: dict, data: dict, cards: dict | None) -> list[str] | None:
    """The drive command that carries a job's held texts, by carry_held; None while its worker runs, which agent_lines sends them to, while the session that started its agent on this Mac reaches it, by state.agent_lane, whose ANSWER undelivered line names the drive job resume that posts each held text to that agent and prints the SendMessage pointing it there, by his pick "Keep with the starter: the agent finishes its job, your answers reach it at once", while its codex-ask run goes, whose next launch carries them in its brief, while a wait holds it, builder-slot included for a lane started, whose move here the watch's turn starts with them in its handoff, but a wait on a PR of its own, by view.start_waits, or for an adopted PR's job no builder started, which starts only on a PR event."""
    if state.own_session(job):
        return ["job", "resume", job_id]
    started = job.get("agent") or job.get("dispatch") or job.get("codex_run")
    if job["state"] == "running" and started or job["prs"] and not started or state.agent_lane(data, job):
        return None
    remote = job.get("where") == "remote" and bool(job.get("dispatch"))
    local = not remote and bool(job.get("brief")) and bool(place.mac_step(Path(job["brief"]).read_text()))
    if any(remote or local or wait != state.SLOT_WAIT for wait in view.start_waits(data, job, cards)):
        return None
    return ["job", "resume", job_id] if remote else ["job", "launch", job_id, *(["--local"] if local else [])]


def end_lane(name: str, job_id: str, token: str) -> int:
    """Run `drive job end` as its own process on a job that is not running whose worker's turn is over and whose lane on a Sprite no end ended, by unended, so that idle Sprite, its forward and its Orca environment go once its transcripts are kept and what it holds is saved, and a waiting job's next event starts its next lane on a fresh one; its output appended to the drive's relaunches log, with --reserved naming the token of the end the watch reserved on the job, which it adopts before anything else; its pid."""
    return state.detach(name, ["job", "end", job_id, "--reserved", token], RELAUNCHES_LOG)


def can_wake(watcher: Watcher, job_id: str, job: dict, data: dict, moment: datetime) -> bool:
    """Whether something can wake a waiting or built job: what state.wakes reads, the rule drive job report's refusal of a WAITING reads too, with GitHub's state of each PR the drive does not track as github_lines last read it, or what only the watch follows: its lane's question, a wait it stores that ended, which waits_lines frees by a FREE line, its proof to come, or an open PR of its or one folded into another open PR's branch, whose stage wakes its builder."""
    if job.get("ask") or job["waits"] or proving(data, job_id) or any(pr["job"] == job_id and pr["state"] in github.UNLANDED for pr in data["prs"].values()):
        return True
    return state.wakes(data, job_id, moment, read=watcher.untracked)


def unanswered(name: str, job_id: str, job: dict, data: dict, moment: datetime) -> str | None:
    """A lane's question no resume has answered for 30 minutes, whatever PR it has, that his cards do not carry; his answer since it came that no resume carried leads the line, and an `ASK` no `drive job ask` kept says that keeps it, as mail_line does, since no resume answers his question. A waiting or built job's waits carry its part whatever read its lane left."""
    if not job.get("ask"):
        return None
    asked = state.parse_time(job["ask_at"])
    if moment - asked < QUIET_FOR or carded(job, asked):
        return None
    question = f'"{cut(job["said"] or "", 80)}" {minutes(moment - asked)} minutes ago'
    if answers := [answer for answer in job["undelivered"] if state.parse_time(answer["at"]) >= asked]:
        return f"{unreached(job_id, job, data, answers[-1], orca.locked_out(name, job_id, job), moment)}, answering its lane's question {question}"
    if reads.asks(job["report"] or "") and not state.asked_in(job, reads.opening(job["report"])):
        return f"{job_id}'s lane asked {question}, an ASK only his card answers: {UNKEPT_ASK.format(job=job_id)}"
    return f'{job_id}\'s lane asked {question} and nothing answered it: drive job resume {job_id} "<the answer>" answers it'


def carded(job: dict, asked: datetime) -> bool:
    """Whether his cards carry a lane's question: each question its job kept that it names, by the files its `ASK <file> [<file>…]` report names or as its said line, open on a card of his whenever it was kept; a question naming none of them, a card drawn since it came, since an older card asks him something else."""
    drawn = [question for question in view.open_questions(job) if question["card"] or question["waits"]]
    said = [question for question in job["questions"] if state.same_question(question["title"], job["said"])][-1:]
    if named := state.asked_in(job, reads.opening(job["report"] or "")) or said:
        return all(question in drawn for question in named)
    return any(state.parse_time(question["asked"]) >= asked for question in drawn)


def read_for(data: dict, job_id: str, job: dict, order_id: str, part: str, now: datetime) -> str | None:
    """The reader on the same part of the order that finished READ_WAIT or more before now while this design or builder waits for a slot, None for none, so the watch flags work his order waits on past its research."""
    return next((reader_id for reader_id, reader in data["jobs"].items() if reader_id != job_id and reader["kind"] == "reader" and reader["state"] == "done" and part in state.job_parts(reader, order_id) and now - state.parse_time(reader["updated"]) >= READ_WAIT), None)


class NotStarted(str):
    """A part's gap naming a job drive job add recorded that no worker started, which the watch says before its reads on every pass, a process's first included, so its ORPHAN comes on time whatever the length of a pass or the life of a watch process."""


def job_gap(watcher: Watcher, job_id: str, job: dict, data: dict, order_id: str, part: str, moment: datetime) -> str | None:
    """Why the job leaves the order's part with no one, None when it carries it or is a design its builder carries, or SETTLING while a change in the last two minutes is still being handed over; a builder that took DONE at its merge carries it only through the builder proving it live; a done tester leaves a staging test part whose PR it ran no full pass of waiting on a rerun; a DONE of a job's own carries work only while drive job prove's run of its proof file is to come; a job drive job add recorded not started that waits on nothing settles one minute, or while its launch takes, a drive job launch only while it holds the job's lock, read against state.now() since the pass may have started long before, or while this Mac's admission holds its Claude lane by place.admission, its cap, load gate or daily budget, since work that waits its turn is no orphan, then names the minutes since drive job add and the brief its agent reads first; a waiting job nothing can wake also names what its line names that ended or moved; a builder stopped before any agent took it carries the part by the PRs the drive does not track that it holds, by untracked_gap."""
    if work.queued(data, job):
        return work.stalled(data, job_id, state.now())
    if state.unstarted(job) and not job["waits"]:
        if (now := state.now()) - state.parse_time(job["updated"]) < UNSTARTED_FOR or launching(job, now) and (job["where"] != "remote" or state.launch_in_flight(watcher.name, job_id, job)):
            return SETTLING
        if job["kind"] in state.STARTS_ITSELF and place.admission(data, job_id, "sprite" if job["where"] == "remote" else "local", now):
            if (read := read_for(data, job_id, job, order_id, part, now)) is None:
                return SETTLING
            return NotStarted(
                f"{job_id} {job['kind']} not started, waiting its turn for a slot {minutes(now - state.parse_time(data['jobs'][read]['updated']))} minutes after its reader {read} finished:"
                f" if his words want it soon, drive order set {order_id} --part \"{part}\" --urgent starts it past the caps; if they already settle the design, start its builder on them and put only the open fork on his card"
            )
        if not job.get("brief") and not job.get("launching"):
            refused = f", which refused: {job['brief_error']}" if job.get("brief_error") else ""
            return NotStarted(f"{job_id} has no brief {minutes(now - state.parse_time(job['created']))} minutes after drive job add, so nothing starts it: `drive job brief {job_id}`{refused}, then `drive job launch {job_id}`")
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
        if state.done_at_merge(job):
            return (
                f"{job_id} took DONE at its merge, which proves nothing live, and the watch adds the builder proving it live once its"
                f" the release provider's reach read read of {', '.join(reported_prs(data, job_id))} answers"
            )
        if done_unproven(data, job_id):
            return None if proving(data, job_id) else merged_gap(job_id, data)
        if job["kind"] in state.CHANGE_KINDS and (staging := state.STAGING_PART.fullmatch(part)):
            held, key, add = data["orders"][order_id]["parts"][part], f"{state.home(data)}#{staging['number']}", f"drive job add builder --order {order_id} --part {shlex.quote(part)}"
            if held["posted_by"] != job_id:
                return f"{job_id}'s DONE ran no case of {key}'s staging test, so the part waits on a run of it: {add} --goal '<run it>'"
            if held["posted"]:
                return f"{job_id}'s DONE passed {key}'s staging test, and a newer tester of it holds the pass"
            return f"{job_id}'s DONE is no pass of {key}'s staging test: {cut(state.unpassed(held), 240)}, so the part waits on a rerun: {add} --goal '<rerun it>'"
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
    """The open question whose card never drew for a waiting job, by the job keeping it: one of its own, or one asked on a card it waits on, such as its reader's, that `drive job ask` or `drive job card` refused, or whose draw failed, kept with no card and no refusal and folded into no other, which `draw_questions` draws again; none kept undrawn while its job waits on an order he has not scheduled, by state.unscheduled_job, which draws when he schedules it."""
    asked = [*((job_id, question) for question in view.open_questions(job)), *view.drawn_on(data, *job["waits"])]
    unposted = [
        (owner, question)
        for owner, question in asked
        if question["closed"] is None and (question["refused"] or question["file"] and not (question["card"] or question["replaced"]) and not state.unscheduled_job(data, owner))
    ]
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


def at_merge(data: dict, job_id: str) -> bool:
    """Whether the watch takes DONE at its merge on a builder waiting on a READY, PUSHED or WAITING line whose PRs, by reported_prs, all merged outside yasyf/*, a product change, held by no open card of his and no wait but on a release, which ends with it, on whatever order it serves, an open incident's fix like any other, since the builder proving it live starts at the merge and sets its own release wait."""
    job = data["jobs"][job_id]
    if job["kind"] != "builder" or job["state"] != "waiting" or not MERGE_ENDS.match(job["said"] or "") or not product(data, job_id):
        return False
    return all(data["prs"][key]["merged_sha"] for key in reported_prs(data, job_id)) and not view.open_cards(job) and all(map(state.RELEASE_WAIT.fullmatch, job["waits"]))


def open_served(data: dict, job: dict) -> dict[str, list[str]]:
    """The parts a job holds of each open order it serves, its own first, then each it carries, but a staging test part, which only its tester's full pass before the merge delivers, so no builder proving the merge live holds one."""
    return {
        order_id: [part for part in state.job_parts(job, order_id) if part in data["orders"][order_id]["parts"] and not state.STAGING_PART.fullmatch(part)]
        for order_id in [job["order"], *job["carries"]]
        if data["orders"][order_id]["state"] not in state.CLOSED_ORDER_STATES
    }


def merge_delivers(data: dict, keys: list[str], order_id: str, part: str) -> bool:
    """Whether merging these PRs delivers a part, so the prover a merge adds carries it: a staging test part only when it tests one of them, since another PR's test waits on that PR; otherwise a part one of them names in its parts, or, when none names any, each work part of the job, as before PRs kept parts."""
    if found := state.STAGING_PART.fullmatch(part):
        return f"{providers.of(data).repo.name}#{found['number']}" in keys
    named = [pr for key in keys if (pr := data["prs"][key])["parts"]]
    return not named or any(pr["order"] == order_id and part in pr["parts"] for pr in named)


def unproved(data: dict, job_id: str) -> dict[str, list[str]]:
    """The parts the builder proving a merge outside yasyf/* live takes, each list under the open order holding it, its own first, as open_served reads them, every open part the job holds, own or carried, since its prover's passing proof alone delivers them: owed by a builder that at_merge takes DONE on now and by one that took that DONE before, alike, and by one whose PRs all merged, a yasyf/* one beside them, whose install's proof proves only that one, by its PRs outside yasyf/*, as released_prs reads them, while one of those parts is undelivered, each of them while one of those PRs has no prover, by prover_of, else those the prover of one of them does not hold yet; none otherwise, so a prover once added or found holds every part, and one that failed or stopped is said by its part's ORPHAN line, never given a second."""
    job = data["jobs"][job_id]
    if not (at_merge(data, job_id) or job["kind"] == "builder" and state.done_at_merge(job) and product(data, job_id) or merged_ready(data, job_id) and not product(data, job_id)):
        return {}
    keys = released_prs(data, job_id)
    served = {order_id: kept for order_id, parts in open_served(data, job).items() if (kept := [part for part in parts if merge_delivers(data, keys, order_id, part)])}
    if all(state.delivered(data, order_id, part) for order_id, parts in served.items() for part in parts):
        return {}
    found = [prover_of(data, key) for key in keys]
    if None in found:
        return served
    return {order_id: unheld for order_id, parts in served.items() if (unheld := [part for part in parts if any(part not in state.job_parts(data["jobs"][prover], order_id) for prover in found)])}


def proof_due(data: dict, job_id: str) -> bool:
    """Whether the watch starts the job's proof: a builder still waiting on a READY or PUSHED whose PRs all merged, a yasyf/* one among them, none a PR a lander still merges or installs, none a merge the install train holds, boarded or carried by an install it started that still runs, by train.holds, so the proof starts on the pass after the train's install ends rather than installing early, each merged outside yasyf/* handed with the parts its job holds to its prover, by unproved, so the install's proof delivers none of them, a DONE of a job's own on work no proof delivered, or a repair waiting on the proof its drive job prove marked, keeping an executable proof file, and no proof started on that line, one that waits on his next word once he is heard after it started, or one that ended with no result before a newer long-running's watch read it, never one that took a pass or a fail, which proof_kept marks on it as its result, so an install reruns no proof that ran to its end, one whose job was put back on the line it ran on included."""
    job = data["jobs"][job_id]
    if not watch_proves(data, job_id) or proof_gap(data, job_id) or any(land.running(data["prs"][key]) for key in reported_prs(data, job_id)) or unproved(data, job_id):
        return False
    proof = job.get("proof")
    if not proof or proof["said"] != job["said"] or unlaunched(proof):
        return not train.holds(merges_of(data, job_id))
    if proof.get("waits"):
        heard = data["owner"]["heard"]
        return not land.runs(proof) and bool(heard) and state.parse_time(heard) > state.parse_time(proof["at"]) and wait_moved(data, job_id, proof)
    return "result" not in proof and proof["version"] != state.VERSION and not land.runs(proof)


def unlaunched(proof: dict) -> bool:
    """Whether a proof the watch reserved never ran: cancelled, or reserved by a watch that died before its child adopted it, so the next pass reserves it again on the same line and install, a child started late finding its token gone."""
    return proof.get("state") in ("reserved", "cancelled") and state.holder(proof) is None


def wait_moved(data: dict, job_id: str, proof: dict) -> bool:
    """Whether what a proof's WAIT line waits on moved since the proof ran, so his word heard after it can be about it: a PR, order or job the line names, by state.named_prs, LINE_ORDER and LINE_JOB, changed after the run; a line naming none waits on any word of his, so his being heard is enough."""
    job, at = data["jobs"][job_id], state.parse_time(proof["at"])
    wait = proof["waits"]
    named = [
        *(data["prs"][key] for key in state.named_prs(data, job["order"], wait)),
        *(data["orders"][order_id] for order_id in state.LINE_ORDER.findall(wait) if order_id in data["orders"]),
        *(data["jobs"][other] for other in state.LINE_JOB.findall(wait) if other in data["jobs"] and other != job_id),
    ]
    return not named or any(owner.get("updated") and state.parse_time(owner["updated"]) > at for owner in named)


def proving(data: dict, job_id: str) -> bool:
    """Whether a merged job's, a DONE of a job's own's or a repair's proof is to come: a lander still merging or installing one of its PRs, a merge of it the install train holds, a PR of it merged outside yasyf/* still to be handed to its prover, by unproved, a proof running on its line or waiting on his next word, or one the watch starts on its next read."""
    job = data["jobs"][job_id]
    if not watch_proves(data, job_id) or proof_gap(data, job_id):
        return False
    proof = job.get("proof")
    return (
        proof_due(data, job_id)
        or any(land.running(data["prs"][key]) for key in reported_prs(data, job_id))
        or bool(unproved(data, job_id))
        or bool(train.holds(merges_of(data, job_id)))
        or proof["said"] == job["said"]
        and bool(proof.get("waits") or land.runs(proof))
    )


def finished(watcher: Watcher, moment: datetime) -> list[str]:
    """Finish each builder the watch hears whose PRs all merged, and each builder or codex job holding a DONE of its own, whose passing proof delivers every part it owes, own or carried: one waiting on a READY, PUSHED or WAITING line whose PRs all merged outside yasyf/* takes DONE at its merge, its order keeping no proof until a builder proves it live on the release holding its merge, its release waits ending with it; one whose READY or PUSHED names a yasyf/* PR and keeps an executable proof file, a DONE of a job's own on work no proof delivered, or a repair waiting on the proof it marked, gets drive job prove, which takes its DONE or BLOCKED once the installed plugins hold its yasyf/* merges, marked first by a reservation naming the watch, which the drive job prove it starts adopts before anything else, so no second proof starts on its line, said on its PRs' lines; and one naming a yasyf/* PR and keeping no executable proof file takes BLOCKED, said by its part's ORPHAN line when no other job carries the part, so it leaves the jobs whose lanes each pass reads. Each builder unproved owes its parts, at that DONE or one it took before, or beside its install's proof, which starts only once its PRs outside yasyf/* hold their prover, goes to its PRs' prover by add_prover, which adds one only for PRs prover_of finds none for, waiting on no release, its handoff naming whether their paths reach only tests or CI config, read by cli.release_reach before the write, a read that fails leaving both the DONE and the prover to the next pass; one line says each prover."""
    from drive import cli

    data = state.load(watcher.name)
    heard = [job_id for job_id, job in data["jobs"].items() if heard_job(watcher, data, job)]
    owed = [job_id for job_id in heard if unproved(data, job_id)]
    if not (owed or any(finishes(data, job_id) for job_id in heard)):
        return []
    merging = [job_id for job_id in heard if at_merge(data, job_id)]
    lines: list[str] = []
    try:
        reach = cli.release_reach(watcher.name, data, [key for job_id in owed for key in handed_on(data, job_id) if not prover_of(data, key)])
        if watcher.staged is None and any(answer["releases"] for answer in reach.values()):
            live_rows(watcher, data)
    except (github.GithubError, state.DriveError, reads.ReadError) as error:
        merging, owed, reach, lines = [], [], {}, failed(watcher, "release select", "the release provider's reach read and `the release provider's live read` reads of the releases a merged PR reaches and the targets staging runs", error, moment)
    else:
        watcher.failures.pop("release select", None)
    at = state.stamp(moment)

    def claim_proofs(data: dict) -> tuple[dict[str, state.Reservation], set[str], dict[str, list[str]]]:
        claims = {}
        for job_id, job in list(data["jobs"].items()):
            if not heard_job(watcher, data, job):
                continue
            if job_id in merging and at_merge(data, job_id):
                merged_done(watcher.name, data, job_id)
            elif proof_due(data, job_id):
                if held := state.reserve(data, (("jobs", job_id, "proof"),)):
                    job["proof"] |= {"at": at, "said": job["said"], "version": state.VERSION}
                    claims[job_id] = held
            elif unproven(data, job_id):
                proofless(watcher.name, data, job_id)
        proving = {job_id: parts for job_id in owed if (parts := unproved(data, job_id))}
        known = set(data["jobs"])
        provers: dict[str, list[str]] = {}
        for job_id, parts in proving.items():
            provers.setdefault(add_prover(watcher.name, data, job_id, reach, parts, watcher.staged or set()), []).append(job_id)
        return claims, known, provers

    claims, known, provers = state.write(watcher.name, claim_proofs, budget=state.WATCH_WRITE_SECONDS)
    unlaunched = dict(claims)
    try:
        for job_id, held in claims.items():
            pid = prove(watcher.name, job_id, held.token)
            del unlaunched[job_id]
            state.settle(watcher.name, held, pid)
    finally:
        for held in unlaunched.values():
            state.cancel(watcher.name, held)
    return lines + [merged_said(watcher.name, builder_id, f"widened to the parts of {', '.join(jobs)}" if builder_id in known else "added") for builder_id, jobs in provers.items()]


def folded_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Fold each prover no agent or lane took whose PRs another prover proves, by folded_into, into that prover, which widen gives its open parts and, while no agent or lane took it either, the PRs it lacks, its handoff and its Mac: step, and by state.hand_off the texts it holds and the waits on it, and stop it, one line saying each fold, so the provers two passes, two orders or a coordinator added for one PR become one."""
    if not doubled(state.load(watcher.name)):
        return []

    def folded(data: dict) -> list[tuple[str, str]]:
        folds = []
        for job_id in doubled(data):
            if not (first := folded_into(data, job_id)):
                continue
            job = data["jobs"][job_id]
            widen(data, first, open_served(data, job), [key for key in proves(data, job_id) if key not in proves(data, first)], {flag: (job.get("brief_flags") or {}).get(flag) for flag in ("handoff", "mac")})
            state.hand_off(data, {job_id: first})
            job.update(state="stopped", said=f"folded into {first}", updated=state.stamp(moment))
            folds.append((first, job_id))
        return folds

    folds = state.write(watcher.name, folded, budget=state.WATCH_WRITE_SECONDS)
    return [merged_said(watcher.name, first, f"took the parts of {job_id}, a duplicate now stopped") for first, job_id in folds]


def cut_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Fold each prover state.mark_cut_backlog marked, an older install's for merges outside yasyf/* that reach a release, no agent or lane having taken it, its release waits dropped, into the prover waiting on the next production release cut, by open_cut, the first such one becoming it, so one prover proves each cut; then bind each prover waiting on the cut, by state.CUT_WAIT, to the drive's own production cut still following its release whose start, by its --from commit or its start's time, carries one of its merges, as github.carrier reads them, so it waits on that cut's job, or free it once a production cluster serves a commit carrying one, as the watch's last release live read shows, a cut no job of the drive started included; one line says each fold and each bind."""
    data = state.load(watcher.name)
    if not (backlog(data) or cut_waiting(data)):
        return []

    def folded_cuts(data: dict) -> list[tuple[str, str]]:
        said = []
        for job_id in backlog(data):
            job = data["jobs"][job_id]
            if not (first := open_cut(data)):
                job.update(waits=[state.CUT_WAIT], cut=True, updated=state.stamp(moment))
                said.append((job_id, "now waits on the next production release cut"))
                continue
            widen(data, first, open_served(data, job), [key for key in proves(data, job_id) if key not in proves(data, first)], {flag: (job.get("brief_flags") or {}).get(flag) for flag in ("handoff", "mac")})
            state.hand_off(data, {job_id: first})
            job.update(state="stopped", said=f"folded into {first}, the prover of the next production release cut", updated=state.stamp(moment))
            said.append((first, f"took the parts of {job_id}, a prover of merges the next cut carries, now stopped"))
        return said

    said = state.write(watcher.name, folded_cuts, budget=state.WATCH_WRITE_SECONDS)
    data = state.load(watcher.name)
    if not (waiting := cut_waiting(data)):
        return [merged_said(watcher.name, job_id, change) for job_id, change in said]
    if not (watcher.live or any(map(state.follows_release, data["jobs"].values()))):
        return [merged_said(watcher.name, job_id, change) for job_id, change in said]
    try:
        binds = cut_binds(watcher, data, waiting)
    except (state.DriveError, github.GithubError, reads.ReadError) as error:
        return [*(merged_said(watcher.name, job_id, change) for job_id, change in said), *read_failed(watcher, "release cut", error, moment)]
    watcher.failures.pop("release cut", None)

    def bound_cuts(data: dict) -> list[tuple[str, str]]:
        bound = []
        for job_id, cut in binds.items():
            job = data["jobs"][job_id]
            if state.CUT_WAIT not in job["waits"]:
                continue
            job.update(waits=[*(wait for wait in job["waits"] if wait != state.CUT_WAIT), *([cut] if cut else [])], cut=cut or "served", updated=state.stamp(moment))
            bound.append((job_id, f"bound to the release cut {cut} starts, {data['jobs'][cut]['release_build']}, which it waits on" if cut else "freed by a production release serving its merges"))
        return bound

    said += state.write(watcher.name, bound_cuts, budget=state.WATCH_WRITE_SECONDS)
    return [merged_said(watcher.name, job_id, change) for job_id, change in said]


def cut_binds(watcher: Watcher, data: dict, waiting: list[str]) -> dict[str, str | None]:
    """What each prover waiting on the next cut binds to: the drive's production cut whose start carries one of its merges, by drive_cuts, else None once, for each of its merges, a production cluster of a target that ships that PR's paths, by cli.release_reach as release select reads them, serves a commit carrying it in the watch's last release live read, so a target that carries the commit but runs none of the PR's code never counts it live; a prover neither holds is left out."""
    from drive import cli

    checkout = str(orca.checkout(watcher.name, data["roles"]["coordinator"]["cwd"], wait=0))
    carries = github.carrier(checkout)
    cuts = drive_cuts(data, checkout)
    serving = [row for row in watcher.live or [] if settled.prod(row["env"], checkout)]
    proven = {job_id: {key: data["prs"][key]["merged_sha"] for key in proves(data, job_id) if data["prs"][key]["merged_sha"]} for job_id in waiting}
    reach = cli.release_reach(watcher.name, data, list(dict.fromkeys(key for merges in proven.values() for key in merges)))
    binds: dict[str, str | None] = {}
    for job_id, merges in proven.items():

        def held(commit: str, merges: dict[str, str] = merges) -> bool:
            return any(carries(commit, sha, github.split(data["prs"][key]["into"] or key)[2]) for key, sha in merges.items())

        def served(key: str, sha: str) -> bool:
            ships = reach[key]["releases"] if key in reach else None
            return any(carries(row["commit"], sha, github.split(data["prs"][key]["into"] or key)[2]) for row in serving if ships is None or row["target"] in ships)

        if cut := next((cut_id for cut_id, start in cuts.items() if start(merges, held)), None):
            binds[job_id] = cut
        elif merges and all(served(key, sha) for key, sha in merges.items()):
            binds[job_id] = None
    return binds


def cut_waiting(data: dict) -> list[str]:
    return [job_id for job_id, job in data["jobs"].items() if state.CUT_WAIT in job["waits"] and job["state"] not in state.FINISHED_JOB_STATES]


def backlog(data: dict) -> list[str]:
    """The provers state.mark_cut_backlog marked for the next cut's prover that no agent or lane took yet."""
    return [job_id for job_id, job in data["jobs"].items() if job.get("cut") == "backlog" and state.untaken(job)]


def drive_cuts(data: dict, checkout: str) -> dict[str, Callable[[dict[str, str], Callable[[str], bool]], bool]]:
    """Each production cut the drive started that still follows its release, its job holding the build it started, by state.starts_cut, mapped to whether it carries a prover's merges: its --from commit holding one, or, for a start naming none, its start coming after one merged."""
    cuts = {}
    for job_id, job in data["jobs"].items():
        if not state.follows_release(job):
            continue
        starts = brief.started(job["brief_flags"]["release"], data)
        if not starts or any(not all(settled.prod(env, checkout) for env in flags.get("env", ())) for flags in starts):
            continue
        cuts[job_id] = functools.partial(cut_carries, [commit for flags in starts for commit in flags.get("from", ())], state.parse_time(job["created"]))
    return cuts


def cut_carries(commits: list[str], created: datetime, merges: dict[str, str], held: Callable[[str], bool]) -> bool:
    return any(map(held, commits)) if commits else any(state.parse_time(install.merged_at(github.repo_of(key), sha)) <= created for key, sha in merges.items())


def doubled(data: dict) -> list[str]:
    return [job_id for job_id, job in data["jobs"].items() if state.untaken(job) and folded_into(data, job_id)]


def folded_into(data: dict, job_id: str) -> str | None:
    """The prover a prover no agent or lane took folds into: the one prover_of names for one of its PRs, when that one can take its other PRs too, no agent or lane having taken it either, or another prover proves each of them."""
    keys = proves(data, job_id)
    found = [prover for key in keys if (prover := prover_of(data, key)) != job_id]
    return found[0] if found and (state.untaken(data["jobs"][found[0]]) or len(found) == len(keys)) else None


def finishes(data: dict, job_id: str) -> bool:
    return at_merge(data, job_id) or proof_due(data, job_id) or unproven(data, job_id)


def prove(name: str, job_id: str, token: str) -> int:
    """Run `drive job prove` on the job as its own process, outliving the pass that started it, its output appended to the drive's proofs log, with --again, since the watch starts a proof only on a newer line, a newer install or his word on what a WAIT waits on, never on a failed run's same install, merges and file, and --reserved naming the token of the proof the watch reserved for it, which it adopts before anything else."""
    return state.detach(name, ["job", "prove", job_id, "--again", "--reserved", token], PROOFS_LOG)


def part_gap(watcher: Watcher, data: dict, order_id: str, part: str, moment: datetime) -> str | None:
    """Why a part of the order has no one carrying it, None when it is carried, delivered or waits on something named, such as his word on a job a model's safeguards stopped, or its order is one he has not scheduled, by state.unscheduled, such as the cutover, whose work starts only on his word, so no ORPHAN asks a job of it until drive order set --state open records that word: work he ordered, every part of a build order or an incident, but an incident's first check and cause and a part drive order set --kind answer --part sorted as an answer that no builder, codex or design job holds, is carried only by a builder, codex or design job, never by research, his card, a post or a stopped job, by state.carriers, nor by a builder or codex job done with no passing proof delivering it, but while drive job prove's run of the proof file of its own DONE is to come, or on a staging test part, which its tester's full pass delivers by state.proven and only its testers carry, by state.part_testers, never a job following a release it started, and of its testers done or holding their DONE for their own PRs' merge only the one whose DONE last ran its cases, by state.keep_pass, while one did, so it has no one the moment none is on it past its handover, and the gap names the builder to start; a change a design lists has no job only while the order asks for options only, waiting on his pick, which adds its builder, since drive job report adds one for each change as the design returns; a builder, codex or design job on the order's immediate fix part carries the order's other parts too until it is done; an open incident's fix is carried only by a live job, or an open or merged PR of a job on it, which the release train ships, never by a post, so once its jobs end otherwise the gap says it has no fix job and names the second one to start at once, by no_fix_job; a standing part, never delivered, is held only by an unfinished job on it, and once none is its gap is the Restart standing_lines starts its next pass on, by standing_gap; a part whose followed build still runs is carried by the watch's follow."""
    if state.unscheduled(data, order_id):
        return None
    order, staging = data["orders"][order_id], state.STAGING_PART.fullmatch(part)
    jobs = state.part_testers(data, order_id, part) if staging else state.part_jobs(data, order_id, part)
    gaps = {job_id: job_gap(watcher, job_id, job, data, order_id, part, moment) for job_id, job in jobs}
    if stuck := [gap for job_id, job in jobs if (gap := gaps[job_id]) and (job["state"] == "running" or job["state"] == "waiting" and job.get("ask"))]:
        return stuck[-1]
    if order["parts"][part]["standing"]:
        return standing_gap(jobs, gaps, order["parts"][part]["check"], moment)
    if order["parts"][part].get("lines") and order["every"]:
        return reported_gap(order, order["every"], moment)
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
        and (not (job["kind"] in state.CHANGE_KINDS and job["state"] == "done" and state.work(data, order_id, part) and not staging) or done_unproven(data, job_id) or done_before_proofs(data, job_id))
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


class Recheck(Restart):
    """A standing part's gap once its check is due by state.check_due, which standing_lines answers by running the check, never a codex pass."""

    def __new__(cls) -> Self:
        made = str.__new__(cls, "its check is due")
        made.last = None
        return made


REPORT_GRACE = timedelta(minutes=10)


def reported_gap(order: dict, every: int, moment: datetime) -> str | None:
    """Who holds a part the order's own session delivers by a line to him every so many minutes, by drive order set --every: that session, while the order's last line to him is younger than its cadence and REPORT_GRACE, so no job is made just to quiet its line; else the line naming it overdue."""
    if order["last_line"] and moment - state.parse_time(order["last_line"]) < timedelta(minutes=every) + REPORT_GRACE:
        return None
    return f"its line to him every {every} minutes is overdue: the order's last went out {f'at {order['last_line']}' if order['last_line'] else 'never'}; its session posts it now"


def standing_gap(jobs: list[tuple[str, dict]], gaps: dict[str, str | None], check: dict | None, moment: datetime) -> str | None:
    """Who holds a standing part: each unfinished job on it, read as any part's carriers are, else no one; a part kept on a check is held by its last run while that run exits 0 and its Recheck is due by state.check_due, and a run that exited nonzero is its gap, so its ORPHAN line says why once; any other its Restart after its last pass once state.pass_due lets its next pass start, and SETTLING until then."""
    if held := [gaps[job_id] for job_id, job in jobs if job["state"] not in state.FINISHED_JOB_STATES]:
        return None if None in held else SETTLING if SETTLING in held else held[-1]
    if check:
        if state.check_due(check, moment):
            return Recheck()
        return f"its check `{cut(check['command'], 80)}` exited {check['exit']}: {check['said']}" if check["exit"] else None
    passes = state.passes(jobs)
    if (due := state.pass_due(passes)) and moment < due:
        return SETTLING
    return Restart(passes[-1][0] if passes else None)


def standing_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Start the next pass of each standing part its Restart gap names, as finished adds provers: a codex job on this Mac on the part's standing goal, its brief the last pass's flags with a handoff quoting that pass's return and PASS_DONE as its Done when, holding the texts and owing the answers that pass held, by added_pass, launched by drive job launch as its own process, the one start of a pass, since launch and resume refuse a pass that ended, its output in the drive's relaunches log; one STANDING line each, naming why the last pass ended. A launch that refuses leaves the job not started, which its part's ORPHAN line says. A part kept on a check its Recheck gap names runs that check instead, detached by drive order check, its output in the drive's standing-checks log, with no job, no model session and no line, since the check files what it finds and a run that fails is said by its ORPHAN line."""
    data = state.load(watcher.name)
    if not any(isinstance(gap, Restart) for gap in part_gaps(watcher, data, moment).values()) and not unbriefed_passes(data, set()):
        return []

    def added_passes(data: dict) -> tuple[list[tuple[str, str]], list[tuple[tuple[str, str], str | None, str]]]:
        gaps = part_gaps(watcher, data, moment)
        checks = [key for key, gap in gaps.items() if isinstance(gap, Recheck)]
        for order_id, part in checks:
            data["orders"][order_id]["parts"][part]["check"].update(ran=state.stamp(moment), exit=None, said=None)
        return checks, [(key, gap.last, added_pass(data, key, gap.last, moment)) for key, gap in gaps.items() if isinstance(gap, Restart) and not isinstance(gap, Recheck)]

    checks, restarts = state.write(watcher.name, added_passes, budget=state.WATCH_WRITE_SECONDS)
    for order_id, part in checks:
        state.detach(watcher.name, ["order", "check", order_id, "--part", part], STANDING_CHECKS_LOG)
    return [launched_pass(watcher.name, *restart) for restart in [*restarts, *unbriefed_passes(state.load(watcher.name), {job_id for *_, job_id in restarts})]]


BRIEF_TRIES = 3


def unbriefed_passes(data: dict, added: set[str]) -> list[tuple[tuple[str, str], str | None, str]]:
    """Each standing part's pass the watch added whose brief was never written, as j7852's was not for two and a half hours, with the pass before it, so standing_lines briefs and launches it again on its next pass rather than leaving it unstarted."""
    found = []
    for job_id, job in data["jobs"].items():
        if job_id in added or job["kind"] != "codex" or job.get("brief") or job.get("brief_tries", 0) >= BRIEF_TRIES or not state.unstarted(job) or job["state"] in state.FINISHED_JOB_STATES:
            continue
        for part in job["parts"]:
            if (held := data["orders"][job["order"]]["parts"].get(part)) and held["standing"] == job["goal"]:
                passes = [passed for passed, _ in state.passes(state.part_jobs(data, job["order"], part)) if passed != job_id]
                found.append(((job["order"], part), passes[-1] if passes else None, job_id))
                break
    return found


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
        state.update(name, None, lambda data: data["jobs"][job_id].update(brief_error=cut(str(error), 300), brief_tries=data["jobs"][job_id].get("brief_tries", 0) + 1))
        return f"{head}, and wrote it no brief: {error}; brief it with drive job brief {job_id}, then drive job launch {job_id}"
    state.detach(name, ["job", "launch", job_id], RELAUNCHES_LOG)
    return f"{head}, and ran drive job launch {job_id}, its output in the drive's {RELAUNCHES_LOG}"


def pass_handoff(name: str, data: dict, key: tuple[str, str], last: str | None) -> str:
    """The handoff a standing part's next pass starts on: its last pass's newest return whole, else that pass's line, else that it is the first pass, and for a last pass that failed on its proof the last PROOF_TAIL lines that proof printed, so the next pass reads why, each line quoted, so none reads as a line of the brief."""
    standing = f'{key[0]}\'s standing part "{key[1]}"'
    if not last:
        return f"The watch started the first pass of {standing}: no pass of it ran before."
    returned = next(iter(state.returns(name, last)), None)
    quoted = returned["body"].strip() if returned else data["jobs"][last]["said"] or "nothing"
    proof = state.proof_path(name, last)
    if data["jobs"][last]["state"] == "failed" and proof.is_file():
        quoted += "\n\nIts proof failed, the last lines it printed:\n" + "\n".join(proof.read_text().splitlines()[-PROOF_TAIL:])
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
    """Why work he ordered that only research, a design done with no builder, a builder done with no passing proof delivering it, his card or a post holds has no one carrying it: the builder to start, waiting on the reader still at work alone, since a wait says only when a job starts, which builds the recommended option of each open card of his whose `Holds:` line names the part or that was kept with none and opens no PR until he answers, each card holding its PR by state.part_cards; then the prover to start on the merged PR the part's words name, since a merge delivers nothing, or else the sort of the part as an answer."""
    held = ", ".join(f"{job['kind']} {job_id} {job['state']}" for job_id, job in jobs)
    finished = [job_id for job_id, job in jobs if job["kind"] in state.CHANGE_KINDS and job["state"] == "done"]
    left = f"; {' and '.join(finished)} done with no passing proof delivering it" if finished else ""
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


def gap_shape(gap: str) -> str:
    return ELAPSED.sub("N", gap)


def orphan_change(data: dict, key: tuple[str, str], gap: str | None, moment: datetime) -> str | None:
    """Whether the part's gap is said, healed or kept, never a standing part's Restart, which standing_lines answers with its next pass: said once as it appears, by his pick "Said once, then listed in drive status: no repeats and a standing gap stays readable", or 30 minutes after a FREE line said its job's last wait ended; once a job on the part moves, by state.orphan_stands, said again only when the gap itself changed beyond the minutes and clock times it counts, else kept as it stands, restamped with no line, so drive status keeps listing it and a job the watch touches each pass, such as one whose agent stopped, is said once per state."""
    part = data["orders"][key[0]]["parts"][key[1]]
    if gap is None:
        return "heal" if part["orphan_said"] else None
    if gap == SETTLING or isinstance(gap, Restart):
        return None
    if not part["orphan_said"]:
        return "say"
    if part["orphan_gap"] == state.FREED and moment - state.parse_time(part["orphan_said"]) >= FREED_AGAIN:
        return "say"
    if state.orphan_stands(data, *key):
        return None
    return "keep" if part["orphan_gap"] and gap_shape(gap) == gap_shape(part["orphan_gap"]) else "say"


def orphan_lines(watcher: Watcher, moment: datetime, said: type | tuple[type, ...] = object) -> list[str]:
    data = state.load(watcher.name)
    if not any(orphan_change(data, key, gap, moment) for key, gap in part_gaps(watcher, data, moment).items() if isinstance(gap, said)):
        return []
    def orphaned(data: dict) -> list[str]:
        lines = []
        for key, gap in part_gaps(watcher, data, moment).items():
            change = orphan_change(data, key, gap, moment) if isinstance(gap, said) else None
            part = data["orders"][key[0]]["parts"][key[1]]
            if change == "heal":
                part.update(orphan_said=None, orphan_gap=None)
            if change in ("say", "keep"):
                part.update(orphan_said=state.stamp(moment), orphan_gap=gap)
            if change == "say":
                lines.append(f'ORPHAN {key[0]} "{cut(key[1], 80)}": {gap}')
        return lines

    return state.write(watcher.name, orphaned, budget=state.WATCH_WRITE_SECONDS)


def hear(name: str, moment: datetime, away: bool = False) -> dict:
    """Record him heard at moment, by his words or his tap on a card, the owner row keeping the moment he reads away from, moment itself with away, else an hour later; when he was away, keep that absence on the owner row, from when he went away, the moment he read away from and moment, which state.present reads; no order's news is marked told, since only its own thread's line tells it, by his words c91b1a9 "stop telling me about the whole drive I never asked for that"."""
    def hear_write(data: dict):
        back = state.owner_away(data, moment)
        owner = data["owner"]
        since = owner["heard"]
        absence = {"since": since, "from": owner["away"], "until": state.stamp(moment)} if back and since else owner["absence"]
        data["owner"] = {"heard": state.stamp(moment), "away": state.stamp(moment if away else moment + state.AWAY_AFTER), "absence": absence}
        return back, since

    back, since = state.write(name, hear_write)
    return {"away": away, "back": back, "since": since}


def unhear(name: str, last: datetime, why: str) -> dict:
    """Take back what the drive heard of him after last, his last word or tap, such as a job's tap kept as his: the owner row reads him heard at last and away an hour after it, its absence kept, and the moment taken back is kept with why in the drive's events; refused when the drive heard nothing of him after last."""
    def unhear_write(data: dict):
        taken = data["owner"]["heard"]
        if not taken or last >= state.parse_time(taken):
            raise state.DriveError(f"refused, nothing changed: the drive last heard him at {taken}, so it heard nothing after {state.stamp(last)} to take back")
        data["owner"] |= {"heard": state.stamp(last), "away": state.stamp(last + state.AWAY_AFTER)}
        return taken

    taken = state.write(name, unhear_write)
    state.append_event(name, "unheard", heard=state.stamp(last), taken=taken, why=why)
    return {"heard": state.stamp(last), "taken": taken}


def sweep_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Once a day, on the drive's own watch, end what still asks him only by an oversight, then file a drive tooling order for each bar the Slack back-and-forth with him crossed, by `crossed`, its measure the order's note, so work he assumes done never sits unseen (his 6cd6aa2): each of his open cards on the drive's own questions of a Drive tooling order that Jev reads as asking him only a detail ends decided on its recommended option, as `drive job ask --answer 43aa665` ends it, by his ruling 43aa665 "don't ask me every little thing ask my high level preferences", and each open flake fix of the drive's that changes only test and fixture code at its head stops waiting on his tap, by `untapped_lines`; a card still open past CARD_WAIT reaches him once in its order's news line instead, by waited_cards. The same pass drains the drive tooling backlog by state.backlog_drain, his option A of Nov 15: a BACKLOG line for each item it promotes, whose one builder the meta session starts, and each item no repeat reached in three days dropped, said in the watch's log alone. An order still open on a bar holds its words, so that bar crossed again takes the new measure in place of a second order. A sweep waits for a pass that read his cards, and one whose read, close or filing fails is said as a failed read is and tried again next pass, keeping the lines of what it ended before."""
    from drive import cli

    data = state.load(watcher.name)
    swept = data["slack"]["swept"]
    if watcher.project or watcher.cards is None or swept and moment - state.parse_time(swept["at"]) < view.SWEEP_EVERY:
        return []
    lines = []
    try:
        for job_id, file in decided_details(watcher.name, data, watcher.cards):
            lines += [f"{line} [{data['jobs'][job_id]['order']} {job_id}]" for line in cli.answered_drop(watcher.name, job_id, file, state.TOOLING_DETAILS_RULING)]
        lines += untapped_lines(watcher, moment)
        bars, offset = crossed(watcher, data, swept["offset"] if swept else state.events_end(watcher.name), moment)
        filed = {cli.added_order(cli.parser().parse_args(["order", "add", words, "--title", title, "--promote", BAR_PROMOTED, "--drive", watcher.name]))[0]: note for words, title, note in bars}
    except (slack.SlackError, github.GithubError, state.DriveError) as error:
        return lines + failed(watcher, "sweep", "daily Slack sweep", error, moment)
    watcher.failures.pop("sweep", None)

    def drained_backlog(data: dict) -> tuple[list[str], dict[str, str]]:
        for order_id, note in filed.items():
            data["orders"][order_id]["note"] = note
        data["slack"]["swept"] = {"at": state.stamp(moment), "offset": offset}
        promoted, dropped = state.backlog_drain(data, moment)
        return dropped, {order_id: data["orders"][order_id]["promoted"]["why"] for order_id in promoted}

    dropped, drained = state.write(watcher.name, drained_backlog, budget=state.WATCH_WRITE_SECONDS)
    print(f"{state.stamp(moment)} drive tooling backlog: dropped {', '.join(dropped) or 'none'}, no repeat in {state.BACKLOG_STALE.days} days", flush=True)
    return lines + [f"BACKLOG {order_id} promoted ({why}): add its one builder now, which researches inline with no reader" for order_id, why in drained.items()]


def decided_details(name: str, data: dict, cards: dict) -> list[tuple[str, Path]]:
    """Each open question of the drive's on a Drive tooling order whose card of his is open, kept in its file, that no mark keeps unsettled and no other question folded in, which Jev reads as asking him only a detail, by cli.reads_as_detail, every card read at once: its job and the file it is kept in."""
    from drive import cli

    asked = [
        (job_id, job, Path(question["file"]))
        for job_id, job in data["jobs"].items()
        if state.tooling(data["orders"][job["order"]])
        for question in view.open_questions(job)
        if question["card"] and question["file"] and not (question["unsettled"] or question["replaced"]) and view.card_open(question["card"], cards) and Path(question["file"]).exists()
    ]
    with ThreadPoolExecutor() as pool:
        details = list(pool.map(lambda each: cli.reads_as_detail(name, data, each[1], each[2]), asked))
    return [(job_id, file) for (job_id, _, file), detail in zip(asked, details, strict=True) if detail]


def untapped_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Take his tap off each open flake fix of the drive's still waiting on it whose changed files at its head are only test and fixture code, by cli.flake_tap, the one reading `drive pr add` and `drive pr set --exempt` keep a flake fix's tap by, so his card on it gives up Approve in place now, by redraw_lines, by his ruling 2c70b68 "you cna also merge CI/tst-only tweaks/fixes without me, but onyl if it doesnt touch non-test/CI code at all"."""
    from drive import cli

    data = state.load(watcher.name)
    untapped = [key for key, pr in data["prs"].items() if pr["state"] == "open" and pr["exempt"] == state.FLAKE_FIX and pr["wants_tap"] and not cli.flake_tap(key, state.waits_his_tap(data, key, pr["order"]))]
    if not untapped:
        return []

    def untapped_prs(data: dict) -> dict:
        for key in untapped:
            data["prs"][key]["wants_tap"] = False
        return data

    data = state.write(watcher.name, untapped_prs, budget=state.WATCH_WRITE_SECONDS)
    told = [f"PR {key}: waits on his tap no more, its flake fix changing only test and fixture code at {(pr := data['prs'][key])['head'][:7]}, which lands with no tap [{pr['order']} {pr['job']}]" for key in untapped]
    return told + redraw_lines(watcher, moment, set(untapped))


def crossed(watcher: Watcher, data: dict, offset: int, moment: datetime) -> tuple[list[tuple[str, str, str]], int]:
    """The words, title and note of an order for each bar the sweep finds crossed by view.slack_waits, on his answers since the last sweep and the messages of his cc-slack owes while the session this drive's handlers run beside holds his DM, so the drive owes them, and the events offset it read to."""
    said = []
    for event, offset in state.read_events(watcher.name, offset):
        said.append(event)
    waits = view.slack_waits(data, said, slack.owed(agents.beside(data)[1]["session"]), moment)
    answers, owed, approved = waits["answers"], waits["owed"], waits["approved"]
    found = f"{state.TOOLING} the daily Slack sweep found"
    bars = []
    if view.crossing("answers", answers):
        slowest = view.slowest(answers)
        bars.append((f"{found} more than 1 in 10 answers to your messages taking over 2 minutes", "Answers slower than 2 minutes", f"{len(answers)} answers, 90th percentile {view.p90(answers).total_seconds() / 60:.1f} min, slowest {slowest['wait'].total_seconds() / 60:.1f} min: {slowest['link']}"))
    if owed:
        bars.append((f"{found} a message of yours still unanswered after 10 minutes", "Message unanswered past 10 minutes", f"{len(owed)} owed, the oldest since {when(owed[0]['at'])}: {owed[0]['link']}"))
    if approved:
        bars.append((f"{found} a pull request you approved still open after 24 hours", "Approved pull request open past 24 hours", f"{len(approved)} PRs: {cut(', '.join(state.pr_key(pr['link']) for pr in approved), 300)}"))
    return bars, offset


def run_pass(watcher: Watcher, moment: datetime) -> list[str]:
    """One pass's lines for the coordinator, every stage's together, as a proof runs its passes in-process, the landing loop's stages among them."""
    return [line for lines in passed(watcher, moment, staged=functools.partial(stages, lands=True)) for line in lines]


def passed(watcher: Watcher, moment: datetime, until: Callable[[], object] = lambda: None, staged: Callable[[Watcher, datetime], Iterator[list[str]]] | None = None) -> Iterator[list[str]]:
    """The coordinator's lines of one pass of the watch's stages, or of those staged names, such as the landing loop's, as each stage returns, first none, as the communicator, the meta session and the coordinator are each sent again what failed to post to them before, then each stage's, its lines for the communicator or the meta session posted to them as it returns, so a pass that reads each lane from Orca for minutes says what GitHub and the lanes said in its first minute then, not at its end; the watch's pass ends before any stage after its first ones once until answers, as the watch's loop asks whether a newer long-running is installed, the stage it ends before kept as the watcher's resume. A stage's lines join the watcher's made lines first, so a pass that ends early in a crash leaves those not yet handed to the watch process after it."""
    yield handed(watcher, [], moment)
    for lines in staged(watcher, moment) if staged else stages(watcher, moment, until):
        watcher.made += lines
        if watcher.made and (said := handed(watcher, watcher.made, moment)):
            yield said


def stages(watcher: Watcher, moment: datetime, until: Callable[[], object] = lambda: None, lands: bool = False) -> Iterator[list[str]]:
    """The lines of each stage of one loop of the watch, as it returns: first the lines its relay made since the last pass, so a pass that crashes while it reads every live lane's mail holds back none it already had, then each message a job's outbox still owes delivered to its history, by state.replay, which a process that died between a write's commit and its delivery left, and new events, so a return `drive job report` took in another process is said in the pass its event wakes, then each part a job drive job add recorded not started leaves with no one, on every pass, a process's first included, so its ORPHAN comes on time however short a watch process lives, healing a gap that reads None only after the process's first read, since a gap a process reads before its agent_lines reads its lanes can read None and a part whose order a later stage closes is gone by the end's orphan_lines, then the drive's own Orca terminal every Orca call speaks as, his cards as the landing loop last stored them and the messages of his cc-slack owes an answer, or, in a pass run in-process, which lands, the landing stages, the finish of each builder whose READY's PRs all merged, DONE at the merge of a product change or the proof of a yasyf/* one, the check against his words of each PR whose builder's READY owes one, his PR cards whose PRs all ended, where each merged PR a release wait or an incident fix names runs, each Buildkite build a wait names or a part follows that is newly held or a delivery of Buildkite's named, the status message of each build a part follows, the waits his answers, a job's end, that read or the Buildkite read release or hold again, a port's READY the coordinator has not checked in five minutes, his answers that leave cards unmarked, lane mail, agents, the chains of waits that stood unmoved, the texts jobs hold that no worker taking messages gets, the communicator's compaction, the post in each place saying what waits on him, the status page, a coordinator whose process exited, a dashboard whose server exited, each teammate thread an order watches, each handoff the communicator withdrew and each message of his cc-slack owes an answer every 60 s, then events written since, its own stages' among them, and, in a pass that lands, the GitHub reads they and the rest owe while its last read answered, so a delivery is read in the pass it wakes, and each builder's PUSHED or lane's READY those settle, so one a lane sent this pass is taken in it, then the daily Slack sweep's drive tooling orders, claims whose end passed, job clocks run out, his answers no resume has carried to their jobs, each answer order left nothing owed, fixes proven live whose bug report has not heard, the next pass of each standing part no job holds, and parts nothing carries. Each stage after the orphans is named by its function: the pass starts those at the watcher's resume when one of them bears its name, so the watch after one that ended mid-pass for a newer install runs only the stages that one had not, and ends before the first one it reaches once until answers, keeping that one's name as the resume, though a resumed pass always runs the stage it resumes at, so a pass whose first stages run past PASS_FOR never starves the late ones; a pass that resumed or ran past a minute logs reaching its last stage. A stage that keeps working after its save joins the lines it made by then to the watcher's made lines itself."""
    beat("drained", True)
    yield drained(watcher.relayed)
    beat("replay", True)
    yield contained(watcher, functools.partial(state.replay, watcher.name), moment)
    beat("event_lines", True)
    yield contained(watcher, functools.partial(event_lines, watcher), moment)
    beat("orphan_lines", True)
    yield contained(watcher, functools.partial(orphan_lines, watcher, moment, NotStarted if watcher.read_at is None else (NotStarted, type(None))), moment)
    beat("first_starts", True)
    yield contained(watcher, functools.partial(first_starts, watcher, moment), moment)
    later: list[functools.partial] = []
    if watcher.read_at is None or moment - watcher.read_at >= READ_EVERY:
        watcher.read_at = moment
        beat("reads", True)
        try:
            orca.speak_as_drive(watcher.name)
        except orca.OrcaError as error:
            yield read_failed(watcher, "drive terminal", error, moment)
        else:
            watcher.failures.pop("drive terminal", None)
        view.refresh_owed(watcher.name, agents.beside(state.load(watcher.name))[1]["session"])
        if not lands:
            watcher.cards, watcher.redrawn_whole = view.live_cards(watcher.name), set()
        later = [
            *(landing_stages(watcher, moment) if lands else []),
            *(
                functools.partial(stage, watcher, moment)
                for stage in (
                    settle_lines,
                    follow_lines,
                    serving_lines,
                    build_lines,
                    release_end_lines,
                    release_card_lines,
                    status_lines,
                    waits_lines,
                    start_jobs,
                    folded_lines,
                    cut_lines,
                    finished,
                    recovered_lines,
                    owner_page_lines,
                    close_report_lines,
                    waiting_lines,
                    stale_texts,
                    answer_lines,
                    noted_lines,
                    lane_lines,
                    release_lanes,
                    rest_sessions,
                    agent_lines,
                    safeguard_cards,
                    stall_lines,
                    budget_lines,
                    carry_held,
                    compact_lines,
                    waiting_post_lines,
                    digest_lines,
                    page_lines,
                    down_lines,
                )
            ),
            functools.partial(dashboard_lines, watcher),
            *(functools.partial(stage, watcher, moment) for stage in (watched_lines, withdrawn_lines, owed_lines)),
        ]
    later += [
        functools.partial(superseded_lines, watcher, moment),
        functools.partial(train_lines, watcher, moment),
        functools.partial(event_lines, watcher),
        *(owed_reads(watcher, moment) if lands else []),
        *(functools.partial(stage, watcher, moment) for stage in (sweep_lines, claim_reply_lines, claim_lines, clock_lines, case_lines, held_lines, undelivered_lines, close_answered, close_joined_repeats, tell_lines, standing_lines, retired_seats, communicators_idle, env_alert_lines, orphan_lines)),
    ]
    names = [stage.func.__name__ for stage in later]
    resume, watcher.resume = watcher.resume, None
    began = monotonic()
    for ran, stage in enumerate(later[names.index(resume) if resume in names else 0 :]):
        if (ran or not resume) and until():
            watcher.resume = stage.func.__name__
            return
        beat(stage.func.__name__, True)
        yield contained(watcher, stage, moment)
    if resume or monotonic() - began >= SLOW_STAGE.total_seconds():
        print(f"{state.stamp()} pass of {watcher.project or 'the drive'} reached its last stage, {names[-1]}, in {monotonic() - began:.0f}s{f', resumed at {resume}' if resume else ''}", flush=True)


def contained(watcher: Watcher, stage: functools.partial, moment: datetime) -> list[str]:
    """A stage's lines, or, when a write of it is refused within its budget, WATCH_WRITE_SECONDS, the stage skipped to the next pass, said once by failed after FAILURES_BEFORE_SAYING refused passes in a row; or, when it raises otherwise, none: one traceback line in the watch's log each pass it raises, so every later stage of the pass still runs, and one incident record to the meta session once it raises FAILURES_BEFORE_SAYING passes in a row, at most every FAILURE_EVERY."""
    source = f"stage {stage.func.__name__}"
    try:
        lines = stage() or []
    except state.Refused as refused:
        print(f"{state.stamp()} {source} of {watcher.project or 'the drive'} skipped this pass, its write refused: {refused}", flush=True)
        return failed(watcher, source, f"{source}'s write", refused, moment)
    except Exception as error:
        where = traceback.extract_tb(error.__traceback__)[-1]
        print(f"{state.stamp()} {source} of {watcher.project or 'the drive'} raised {type(error).__name__}: {error} at {Path(where.filename).name}:{where.lineno}", flush=True)
        if said := failed(watcher, source, source, error, moment):
            try:
                posted_record(watcher.name, "meta", "incident", said, Path(state.load(watcher.name, ("roles",))["roles"]["coordinator"]["cwd"]))
            except reads.ReadError as refusal:
                print(f"{state.stamp()} {source}'s incident record to the meta session failed: {refusal}", flush=True)
        return []
    watcher.failures.pop(source, None)
    return lines


def superseded_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """End, first in each pass of every watch of the drive, each `drive pr check`, `drive pr wake` and `drive job prove` an older install left with no parent, and from the drive's own watch alone each `drive reload --seat`, whose end starts the drive's reload once, and each `drive handler-start`, whose end starts its handler again from this install while it has not started, by superseded_procs.end, and there too each session reload an install held back for the hour, by install.due_reloads, said in the watch's log, never to a session, since nothing waits on them."""
    if state.scratch():
        return []
    for line in [*superseded_procs.end(watcher.name, not watcher.project), *([] if watcher.project else install.due_reloads(watcher.name))]:
        print(f"{state.stamp(moment)} {line}", flush=True)
    return []

def train_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Send the install train from the drive's own watch once it is due, by train.depart, whichever drive's watch on this Mac reads it due first, said in the watch's log, never to a session, since each proof waiting on it starts once its install ends."""
    if state.scratch() or watcher.project:
        return []
    for line in train.depart(watcher.name, moment):
        print(f"{state.stamp(moment)} {line}", flush=True)
    return []


def github_owed_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """The GitHub reads the pass's stages and deliveries owe, unless its last GitHub read failed, which the next 60-second read retries."""
    return [] if "github" in watcher.failures else github_lines(watcher, moment)


def landing_stages(watcher: Watcher, moment: datetime) -> list[functools.partial]:
    """The stages that land, stage and wake, each READ_EVERY in the landing loop, or in a pass run in-process, once it stored his cards as cc-slack lists them now: the GitHub reads it owes, each builder's PUSHED that read settles, so a READY it takes is landed in the same pass, the tests each yasyf/* main fails, the staging test of each PR whose new head changes a dangerous class of file, the release reach, the check against his words carried to each head a restack moved, the check of each PR that owes one, each PR's stage with its wakes, his card withdrawn from each question of a job waiting on an order he has not scheduled, his card on each kept question none drew yet, a lander on each PR his Approve covers that qualifies, the receiver, and his PR cards whose rows went stale."""
    view.refresh_cards(watcher.name)
    watcher.cards, watcher.redrawn_whole = view.live_cards(watcher.name), set()
    return [
        functools.partial(github_lines, watcher, moment, polled=True),
        *(functools.partial(stage, watcher, moment) for stage in (restack_lines, pushed_lines, main_lines, dangerous_lines, reach_lines, release_lines, carried_lines, checks, stage_lines, unscheduled_questions, draw_questions, landed, receiver_lines, redraw_lines)),
    ]


def restack_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Restack each heard stack a merge or rewrite below left carrying stale commits, by restack.restack, with no model session, so only a real conflict reaches a builder, through its stage; a git or GitHub step that fails is retried and said as any read is. The stacks it already read stay kept in the drive's folder, so a watch an install or crash restarted reads again only a stack that moved since."""
    data = state.load(watcher.name)
    kept = state.drive_dir(watcher.name) / f"restacked-{state.project_lane(watcher.project or '') or 'drive'}.json"
    if not watcher.restacked and kept.is_file():
        watcher.restacked.update({root: tuple(map(tuple, signature)) for root, signature in json.loads(kept.read_text()).items()})
    tries = restack.restack(watcher.name, [key for key in state.open_pr_keys(data) if hears(watcher, data, data["prs"][key]["order"])], watcher.restacked)
    if failures := [found["failed"] for found in tries if "failed" in found]:
        for found in tries:
            if "failed" in found:
                watcher.restacked.pop(found["root"], None)
    kept.write_text(json.dumps(watcher.restacked))
    if failures:
        return read_failed(watcher, "stacks to restack", restack.RestackError("; ".join(failures)), moment)
    watcher.failures.pop("stacks to restack", None)
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
    """Redraw in place each card of his on a heard open PR, or on those of keys alone, as github_lines asks for the PRs it just read, open or answered and neither replaced nor withdrawn, whose row cc-slack last drew differs from the card.REDRAWN lines drive card draws on it now, so its head and staging line never stay stale: one slack.draw_pr_lines call writes those lines onto every row of each such PR, which cc-slack redraws in place in the background, buttons kept, and the watch's copy of his cards takes them too. A PR his open card asks his tap on, by view.asks_on, that no longer asks it, by state.asks_tap, is first redrawn whole from drive card, so the card gives up its Approve, once until the watch next reads his cards; a whole redraw that fails leaves its lines unwritten, so the next pass asks both again."""
    from drive import card

    if watcher.cards is None:
        return []
    data, found = state.load(watcher.name), view.live_cards(watcher.name)
    rows: dict[str, list[dict]] = {}
    for shown in [*watcher.cards["pr_cards"], *(closed for closed in watcher.cards["closed"] if "card" in closed)]:
        for row in shown["card"]["prs"]:
            rows.setdefault(state.pr_key(row["url"]), []).append(row)
    asking = {state.pr_key(row["url"]) for shown in watcher.cards["pr_cards"] if (waits := shown["card"].get("waits") or "").startswith("merge:") for row in shown["card"]["prs"] if view.asks_on(waits, row["url"])} - watcher.redrawn_whole
    lines, fresh = [], {}
    for key, drawn in rows.items():
        pr = data["prs"].get(key)
        if (keys is not None and key not in keys) or not pr or pr["state"] != "open" or not pr["head"] or pr["rework"] or key in watcher.stale or not hears(watcher, data, pr["order"]):
            continue
        if key in asking and not state.asks_tap(data, pr):
            try:
                slack.redraw_pr_cards(pr["url"])
            except slack.SlackError as error:
                lines += failed(watcher, "card redraw", f"redraw of his card on {key}", error, moment)
                continue
            watcher.failures.pop("card redraw", None)
            watcher.redrawn_whole.add(key)
        now = card.drawn_lines(data, key, found)
        if all({line: row.get(line, "") for line in card.REDRAWN} == now for row in drawn):
            continue
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
    """Keep each heard unmerged PR's stage, as stage.of names it from the approval, checks and head GitHub last answered for it, redraw whole from drive card his card standing on each open one whose stage owner changed, so the stage his card says, such as blocked on him, never outlives the line that set it, and act once on each open one that entered a stage or passed its deadline, leaving a PR never read, or whose last read failed, as it stands until a read answers for it: draw his card on each PR that entered uncarded or passed its deadline there, one card for the PRs of one card unit as drive card draws them, once no PR of the unit entered uncarded for stage.STACK_QUIET or one of them passed its deadline, by quiet_draws, unless a card of his already stands on one of them, or redraw in place the card cc-slack keeps closed on one, so it offers Approve again and no second card shares its waits id, or, once a card of the unit leaves off a PR of its stack, native or chained by base branch, by view.apart, draw one card for the unit and those PRs, which cc-slack draws in place over the newest card carrying some of them and marks each other one replaced by, wake the builder answering for a PR rework or red owe, ask the repository's review bot once for a review of a head grading an hour and then wake its builder, wake past its deadline the job a waiting or blocked PR names as owing it and the builder of one its author lands, wake the session holding its order on one unlanded and on any other past its deadline but uncarded. A draw or redraw in place that fails, or leaves a PR that asks his tap with no card of his offering Approve at its head, as one cc-slack answers by linking a card he already answered does or a redraw cc-slack leaves the card closed under, clears the PR's stage, so the next read enters uncarded again and draws, and is said to the meta session from its third failure in a row."""
    if watcher.cards is None:
        return []

    def staged(data: dict) -> tuple[dict, list, list, dict, list[str]]:
        with state.indexed(data):
            stacks = land.landing_stacks(data)
            units = {key: state.card_unit(key, stacks.get(key, ()), pr) for key, pr in data["prs"].items()}
            acts = []
            moved = []
            for key in staged_keys(watcher, data):
                pr = data["prs"][key]
                now = stage.of(data, key, watcher.cards)
                if stage.entered(pr["stage"], now):
                    if pr["stage"] and pr["stage"]["owner"] != now.owner and pr["state"] == "open" and now.name != "uncarded":
                        moved.append(key)
                    stage.stamp(pr, now, moment)
                    if pr["state"] == "open" and not (now.name == "waiting" and state.staging_owed(data, key)):
                        acts.append((key, now, True, stage.line(key, pr, now) + f" {ids(pr)}"))
                elif stage.overdue(pr, now, moment) and not (now.name == "waiting" and state.staging_owed(data, key)) and not stage.parked(data, key, watcher.cards):
                    stage.kept(pr, now)
                    pr["stage"]["waking"] = state.stamp(moment)
                    acts.append((key, now, False, stage.line(key, pr, now) + f" {ids(pr)}"))
                elif now.name == "uncarded" and pr["state"] == "open" and pr["stage"].get("quiet"):
                    stage.kept(pr, now)
                    acts.append((key, now, None, stage.line(key, pr, now) + f" {ids(pr)}"))
                else:
                    stage.kept(pr, now)
            uncarded, lines = quiet_draws(data, watcher.cards, units, acts, moment)
        return data, acts, moved, uncarded, lines

    data, acts, moved, uncarded, lines = state.write(watcher.name, staged, budget=state.WATCH_WRITE_SECONDS)
    for key, now, entered, line in acts:
        if now.name != "uncarded":
            lines += acted(watcher, key, now, entered, line, moment)
    for key in moved:
        if key in watcher.redrawn_whole or not view.standing_card(data["prs"][key], watcher.cards):
            continue
        try:
            slack.redraw_pr_cards(state.pr_url(key))
        except slack.SlackError as error:
            lines += failed(watcher, "card redraw", f"redraw of his card on {key}", error, moment)
            continue
        watcher.failures.pop("card redraw", None)
        watcher.redrawn_whole.add(key)
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
            def stage_lines_write(data: dict):
                for key in keys:
                    data["prs"][key]["stage"] = None

            state.write(watcher.name, stage_lines_write, budget=state.WATCH_WRITE_SECONDS)
            if said := failed(watcher, "card draw", f"draw of his card on {', '.join(keys)}", error, moment):
                lines += sent(watcher, "meta", said, moment)
            continue
        watcher.failures.pop("card draw", None)
    return lines


def staged_keys(watcher: Watcher, data: dict) -> list[str]:
    """The unmerged PRs whose stage stage_lines keeps on this pass: each the watch hears that GitHub answered for, whose last read did not fail."""
    return [key for key in state.unmerged_pr_keys(data) if key not in watcher.stale and data["prs"][key]["required"] is not None and hears(watcher, data, data["prs"][key]["order"])]


def quiet_draws(data: dict, cards: dict, units: dict, acts: list[tuple[str, stage.Stage, bool | None, str]], moment: datetime) -> tuple[dict, list[str]]:
    """The uncarded PRs of each card unit the watch draws his card on now: a unit with a card of his standing on one of its PRs, which cc-slack redraws in place, one with a PR past its uncarded deadline, and one none of whose PRs entered uncarded within stage.STACK_QUIET, so a stack its builder is still opening PR by PR goes up as one card once it settles; each other unit's PRs are marked quiet, which acts on them again every pass, and said once when first held."""
    drawn, said = {}, []
    for key, now, entered, _ in acts:
        if now.name == "uncarded":
            drawn.setdefault(units[key], []).append((key, entered))
    for unit, members in list(drawn.items()):
        rows = [data["prs"][key] for key, _ in members]
        changed = max(state.parse_time(row["stage"]["since"]) for row in rows)
        if any(entered is False for _, entered in members) or moment - changed >= stage.STACK_QUIET or any(view.standing_card(row, cards) for row in rows):
            for row in rows:
                row["stage"]["quiet"] = None
            continue
        if not any(row["stage"].get("quiet") for row in rows):
            said.append(f"HELD his card on {', '.join(key for key, _ in members)}: its stack changed {minutes(moment - changed)} minutes ago, so it draws once quiet {minutes(stage.STACK_QUIET)} minutes")
        for row in rows:
            row["stage"]["quiet"] = state.stamp(moment)
        del drawn[unit]
    return {unit: [key for key, _ in members] for unit, members in drawn.items()}, said

def unscheduled_questions(watcher: Watcher, moment: datetime) -> list[str]:
    """Withdraw his card on each open question of a heard job that waits on an order he has not scheduled, by state.unscheduled_job, drawn before it began waiting, the orders' titles its reason, by his answer state.UNSCHEDULED_RULING, leaving the question open under its waits id with no card, as `drive job ask` keeps one asked while it waits, so `draw_questions` draws it again once he schedules them, which cc-slack does in place; a job that failed or stopped keeps its card, since nothing draws it again, and a withdrawal that fails keeps the card on its question for the next read to try again."""
    from drive import talk

    data = state.load(watcher.name)
    drawn = [
        (job_id, question, steps)
        for job_id, job in data["jobs"].items()
        if job["state"] not in state.ENDED_JOB_STATES and heard_job(watcher, data, job) and (steps := state.unscheduled_job(data, job_id))
        for question in view.open_questions(job)
        if question["card"] and not question["replaced"]
    ]
    said, withdrawn = [], []
    for job_id, question, steps in drawn:
        card = question["card"]
        try:
            superseded.answer_card(card, talk.unscheduled_reason(data, steps, "This card asks you again"), card, settled=False)
        except slack.SlackError as error:
            said += failed(watcher, "question withdraw", f"withdrawal of {job_id}'s card {card}, waiting on work he has not scheduled", error, moment)
            continue
        watcher.failures.pop("question withdraw", None)
        def unscheduled_questions_write(edited: dict):
            state.question_in(edited["jobs"][job_id], question)["card"] = None

        state.write(watcher.name, unscheduled_questions_write, budget=state.WATCH_WRITE_SECONDS)
        withdrawn.append(f"CARD {card} withdrawn: {job_id} waits on {', '.join(steps)}, work he has not scheduled, and the card asks him again under {question['waits']} once he schedules it")
    if withdrawn:
        view.refresh_cards(watcher.name)
        watcher.cards = view.live_cards(watcher.name)
    return said + withdrawn


def draw_questions(watcher: Watcher, moment: datetime) -> None:
    """Draw his card on each open question of a heard job, kept in its file with no card drawn and no refusal, as drive job ask draws it, so a draw that failed in that call goes up on this read; a job that failed or stopped asks him nothing new, nor one waiting on an order he has not scheduled, by state.unscheduled_job, until he schedules it, and one his answer to the same question settled since it was kept is kept settled by `keep_settled` and draws none. A draw that fails again is said to the meta session from its third failure in a row, and a card drive job card refuses leaves its question refused, which the job's line then says."""
    if watcher.cards is None:
        return
    from drive import cli

    data = state.load(watcher.name)
    undrawn = [
        (job_id, question, Path(question["file"]))
        for job_id, job in data["jobs"].items()
        if job["state"] not in state.ENDED_JOB_STATES and heard_job(watcher, data, job)
        for question in view.open_questions(job)
        if question["file"] and not (question["card"] or question["refused"]) and not state.unscheduled_job(data, job_id)
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
    """What the watch does for a PR that entered its stage, entered, or passed that stage's deadline, and the lines it says: his card withdrawn from a PR reworked to his words or waiting on work he has not scheduled that still has one, a failed withdrawal of the second clearing its stage so the next read enters it again, then the act its stage owes, none for a PR behind another, whose owner that PR's stage wakes."""
    from drive import talk

    said = []
    data = state.load(watcher.name)
    pr = data["prs"][key]
    if (pr["rework"] or now.name == "unscheduled") and view.standing_card(pr, view.live_cards(watcher.name) or view.NO_CARDS):
        try:
            talk.withdraw(watcher.name, key)
        except slack.SlackError as error:
            said += failed(watcher, "rework withdraw", f"withdrawal of his card on {key}, {'reworked to his words' if pr['rework'] else 'waiting on work he has not scheduled'}", error, moment)
            if not pr["rework"]:
                def acted_write2(data: dict):
                    data["prs"][key]["stage"] = None
                    return data

                data = state.write(watcher.name, acted_write2, budget=state.WATCH_WRITE_SECONDS)
    if now.name == "behind":
        return said
    job_id = now.owner if now.owner in data["jobs"] else None
    if now.name in ("rework", "red") or not entered and (job_id or now.name == "author"):
        stage.wake(watcher.name, key, line, job_id, marks=[f"stage:{key}@{pr['head']}"], at=state.stamp(moment))
    elif now.name == "grading" and not entered:
        repo, number = key.split("#")
        if pr["review_asked"] == pr["head"]:
            stage.wake(watcher.name, key, line, marks=[f"stage:{key}@{pr['head']}"], at=state.stamp(moment))
        elif refused := land.ask_review(providers.of(state.load(watcher.name)).repo.review_ask, repo, number, pr["head"]):
            said.append(f"STAGE {refused} {ids(pr)}")
        else:
            def acted_write3(data: dict):
                data["prs"][key]["review_asked"] = pr["head"]
                return data

            data = state.write(watcher.name, acted_write3, patient=True)
    elif now.name == "unlanded" or not entered:
        said.append(f"STAGE {line}")
        def acted_write(data: dict):
            if kept := data["prs"][key]["stage"]:
                kept["said"] = state.stamp(moment)

        state.write(watcher.name, acted_write, budget=state.WATCH_WRITE_SECONDS)
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
    """Whether a line asks for a step now: any line but a PR's, his tap but an Approve, which the drive's lander lands in every repository once its stage reads ready, a STAGE line on a PR the drive tracks only while stage.of still names that stage, so one made before its PR merged or its lander tried a newer head asks nothing, a PR's merged line only when nobody else says it, never GitHub's merged line for a PR the lander merges and says so itself or whose job took DONE at its merge, whose prover's JOB line asks the step, or is one the watch finishes in that pass, nor the lander's merged line on a job already proved or whose proof the watch runs, whose proof line says so, and a line on a PR no longer open or folded only when it says how the PR ended, its merge, close or failed proof, while its job does not run to take it, never a move made while it was open, such as a lander's try refused at an older head, new heads, checks or a check against his words. An open PR's moves wake nobody, since its stage wakes its owner. A standing part's pass that ended DONE or HANDOFF asks nothing by its own line alone, since the watch alone starts the next pass, quoting it, nor does a WAITING or PUSHED return, by settled_return, whose row of the coordinator's manual says Nothing; a clause the drive adds to either line, such as an ASK it did not keep, still asks its step."""
    if (tapped := TAP_EVENT.match(line)) and tapped["label"] == "Approve":
        return False
    if (job := JOB_LINE.match(line)) and (standing_pass_ended(data, job["job"], line) or settled_return(data, job["job"], line)):
        return False
    if (staged := STAGE_LINE.match(line)) and staged["key"] in data["prs"]:
        return stage.of(data, staged["key"], cards).name == staged["name"]
    found = PR_EVENT.match(line)
    if not found:
        return not line.startswith("PR ")
    pr = data["prs"][found["key"]]
    if found["change"] == "merged":
        return not (land.reported(data, found["key"], pr) or data["jobs"][found["job"]]["state"] == "done" or finishes(data, found["job"]))
    if found["change"].startswith("merged at"):
        return not (data["jobs"][found["job"]]["state"] in state.FINISHED_JOB_STATES or proving(data, found["job"]))
    if pr["state"] in github.UNLANDED or not found["change"].startswith(ENDED):
        return False
    return data["jobs"][found["job"]]["state"] != "running"


def settled_return(data: dict, job_id: str, line: str) -> bool:
    """Whether a JOB line is a return whose row of the coordinator's manual says Nothing, a WAITING the result check let through only naming what the watch frees it on, or a PUSHED whose green checks the watch settles as READY, with no clause beside the line it said, so it reaches the coordinator as evidence and wakes nobody."""
    job = data["jobs"][job_id]
    return bool(SETTLED_RETURN.match(job["said"] or "")) and line == LINE_BREAKS.sub(" ", f"JOB {job_id} {job['order']} {job['said']}")


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
    failures = [failure for lane in lanes if lane != watcher.lane and (lane in routed or not lines or watcher.unsent.get(lane)) for failure in sent(watcher, lane, routed.get(lane, []), moment)]
    watcher.made = []
    return kept + failures


def sent(watcher: Watcher, lane: str, lines: list[str], moment: datetime) -> list[str]:
    """Post the lines owed the lane over cci as one record, by posted_record, a handoff when any of them asks a step now, by wakes, or the lane is the communicator's or the meta session's, else evidence, which reaches the lane with others at most every five minutes, the lander's own failures apart as one defect, which the meta session starts a fix from, so a pass that says many gaps reaches the lane as one record however many it says; but drop each line that needs_step, judging it as it posts, finds asks no step now, each one quiet() reads its row saying Nothing for, each one said to the lane already within SAID_FOR, by unsaid, and each one owed the communicator or the meta session that is a READY whose fix its builder owes, by fixing(), by his words "make everything less chatty", so a line held across a newer install's takeover or a failed post never hands work that ended meanwhile: each credential cci refuses is withheld from it, and a record that fails goes again when the next pass starts, sent with no lines, each line made meanwhile waiting behind it, so a pass tries a failing lane once and its third failure in a row is said. The lines stay among the watcher's unsent until their record posts, so a newer install that takes the watch over meanwhile gets them."""
    if lines and watcher.unsent.get(lane) and lane in state.HOLDERS:
        watcher.unsent[lane] = list(dict.fromkeys(watcher.unsent[lane] + lines))
        return []
    owed = watcher.unsent[lane] = list(dict.fromkeys(watcher.unsent.get(lane, []) + lines))
    if not owed:
        watcher.owed_at.pop(lane, None)
        return []
    watcher.owed_at.setdefault(lane, moment)
    if coalescing(watcher, lane, owed, moment):
        return []
    data = state.load(watcher.name)
    if untaken(watcher, data, lane):
        return []
    posting = unsaid(watcher.name, lane, [line for line in owed if needs_step(line, data, watcher.cards) and not quiet(line) and (lane in state.HOLDERS or not FAILED_LINE.match(line)) and (lane not in state.HOLDERS or not fixing(line, data, watcher.cards))], moment)
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
    keep_said(watcher.name, lane, [line for line in posting if line not in unsent], moment)
    if posting and not unsent:
        keep_posted(watcher.name, lane, moment)
    watcher.unsent[lane] = unsent
    if not unsent:
        watcher.owed_at.pop(lane, None)
    if kind == "handoff" and rest and not unsent:
        watcher.handed_at[lane] = moment
    project = next((project for project in data["subcoordinators"] if state.project_lane(project) == lane), lane)
    what = {"communicator": "handoff post to the communicator", "meta": "handoff post to the meta session", "root": "post to the coordinator"}.get(lane, f"post to the sub-coordinator for {project}")
    if refusal is None:
        watcher.failures.pop(what, None)
        return []
    return failed(watcher, what, what, refusal, moment)


def coalescing(watcher: Watcher, lane: str, owed: list[str], moment: datetime) -> bool:
    """Whether a coordinator's lines wait for its next record: one went to it within COALESCE, none of them is URGENT, his tap, a promised time, a move of his orders, a stopped watch or a process gone, and none has waited on this watcher COALESCE already, so a lane hears the watch at most once every COALESCE in one record of all it made, whichever watch or install posted it last, by his words "make everything less chatty", yet the landing loop's and the watch's records, which share that clock, never hold one another's lines back past it, as they held a Sprite builder's three build requests to priority-o1339 for half an hour; the communicator and the meta session hear each line as it comes."""
    at = said_lines(watcher.name).get(f"{POSTED_KEY}{lane}")
    return (
        lane not in state.HOLDERS
        and at is not None
        and moment - state.parse_time(at) < COALESCE
        and moment - watcher.owed_at[lane] < COALESCE
        and not any(line.startswith(URGENT) for line in owed)
    )


def keep_posted(name: str, lane: str, moment: datetime) -> None:
    """Record when the watch last posted the lane a record, in the said file beside the lines it said, so every watch of the drive, and the one a new install starts, holds the lane to one record every COALESCE."""
    path = state.drive_dir(name) / SAID_FILE
    taking = path.with_name(f"{SAID_FILE}.{os.getpid()}.{threading.get_ident()}")
    taking.write_text(json.dumps(said_lines(name) | {f"{POSTED_KEY}{lane}": state.stamp(moment)}))
    os.replace(taking, path)


def quiet(line: str) -> bool:
    """Whether a line is one whose row of the coordinator's manual says Nothing, so no lane hears it: a card settled or withdrawn in place, a built answer paused on his pick, a standing part's next pass, a compaction, a status page redraw, a receiver the watch replaced, a tester the watch queued in its env's slot itself, or a job the watch starts itself, saying start no agent for it, by his words "a watch record to a lane should exist only when it asks for a step"."""
    return (
        line.startswith(QUIET_LINES)
        or line.startswith("WATCH the receiver") and "could not replace it" not in line
        or line.startswith("SLOT ") and " queued " in line.split(",", 1)[0]
        or WATCH_STARTS in line
    )


def said_key(lane: str, line: str) -> str:
    """The key a line is said under: a failed read's the same for every lane, so the one outage every project's watch reads reaches one lane alone."""
    lane = "" if FAILED_LINE.match(line) else lane
    return hashlib.sha1(f"{lane}\n{ELAPSED.sub('', line)}".encode()).hexdigest()[:16]


def said_lines(name: str) -> dict[str, str]:
    path = state.drive_dir(name) / SAID_FILE
    return json.loads(path.read_text()) if path.exists() else {}


def unsaid(name: str, lane: str, lines: list[str], moment: datetime) -> list[str]:
    """The lines not said to the lane within SAID_FOR, each read with its elapsed and clock times taken out, so a line a pass makes again unchanged, by any watch, reaches its lane once, but a DOWN line, each a new exit of a session's process."""
    said = said_lines(name)
    return [line for line in dict.fromkeys(lines) if line.startswith(EVERY_TIME) or not (at := said.get(said_key(lane, line))) or moment - state.parse_time(at) >= SAID_FOR]


def keep_said(name: str, lane: str, lines: list[str], moment: datetime) -> None:
    """Record the lines posted to the lane, dropping those older than SAID_FOR, written aside and renamed over the file so a reader never reads half of it."""
    if not lines:
        return
    path = state.drive_dir(name) / SAID_FILE
    said = {key: at for key, at in said_lines(name).items() if moment - state.parse_time(at) < SAID_FOR}
    taking = path.with_name(f"{SAID_FILE}.{os.getpid()}.{threading.get_ident()}")
    taking.write_text(json.dumps(said | {said_key(lane, line): state.stamp(moment) for line in lines}))
    os.replace(taking, path)


def untaken(watcher: Watcher, data: dict, lane: str) -> bool:
    """Whether a coordinator lane's session wrote nothing to its transcript since the last handoff the watch posted it, so it took no turn on that handoff, as a session stopped on Claude's usage limit takes none, and each line owed it waits, deduplicated, for the one record that follows its next write; the communicator and the meta session, whose handlers write no role transcript, never wait."""
    if lane not in state.coordinator_lanes(data) or (handed := watcher.handed_at.get(lane)) is None:
        return False
    holder = data["roles"]["coordinator"] if lane == "root" else next(held for project, held in data["subcoordinators"].items() if state.project_lane(project) == lane)
    path = session_transcript(holder)
    return not path.exists() or datetime.fromtimestamp(path.stat().st_mtime).astimezone() <= handed


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


def ensure(name: str) -> str:
    """Load the launch agent keeping the drive's watch, which runs each project's passes beside the drive's own, unless it is loaded or LONG_RUNNING_STATE or XDG_STATE_HOME moves the drive's state root, by state.scratch: launchd starts the watch now and again within RESTART_SECONDS of any exit, crash included, on the newest install, its output in its own log and its environment naming the drive; the line saying which."""
    if state.scratch():
        return f"the watch does not start for a drive whose state LONG_RUNNING_STATE or XDG_STATE_HOME moves to {state.root()}; a proof runs its passes in-process with watch.run_pass(watch.Watcher(...), moment)"
    agent = f"its launch agent {label(name)}, which starts it again within {launchd.RESTART_SECONDS} seconds of any exit, crash included"
    if launchd.kept(label(name)):
        return f"the watch runs under {agent}, and posts its lines to the coordinator and each project's to its sub-coordinator over cci; arm no Monitor for it"
    launchd.keep(label(name), ["watch", "--loop", "--drive", name], state.watch_log_path(name), {"CLAUDE_LONG_RUNNING_LANE": LANE, state.DRIVE_ENV: name}, state.drive_dir(name))
    return f"started the watch under {agent}; it posts its lines to the coordinator and each project's to its sub-coordinator over cci, so arm no Monitor for it"


def retired_seats(watcher: Watcher, moment: datetime) -> list[str]:
    """Retire, from the drive's own watch, each priority order's seat that no open order holds any longer, its order closed or unmarked, by retire_seat, so a finished seat's session stops resuming into a start that refuses it."""
    if watcher.project:
        return []
    retired = seats_retired(watcher.name)
    for seat in retired:
        watcher.others.pop(seat, None)
    return list(retired.values())


def seats_retired(name: str) -> dict[str, str]:
    """Retire each priority seat no open order holds, by retire_seat, as the watch does each pass and drive order set does in the step that drops, closes or unmarks a priority order; each seat retired and the line saying so."""
    if state.scratch():
        return {}
    data = state.load(name)
    held = {order["priority"] for order in state.open_orders(data).values() if order["priority"]}
    return {seat: retire_seat(name, seat) for seat in data["subcoordinators"] if seat.startswith(state.priority_seat("")) and seat not in held}


def retire_seat(name: str, seat: str) -> str:
    """Take a priority seat down: its watch's launch agent and pid file, by retire, its cci alias, by `cci session rm`, which keeps its session alive no more and drops its channel subscription, its session, by seat_ended, and its binding and launched terminal on the drive, so no restart starts it again; the line saying so."""
    session = state.load(name)["subcoordinators"][seat]["session"]
    alias = state.alias(name, "coordinator", seat)
    retire(name, seat)
    seat_ended(alias, session)
    def retire_seat_write(data: dict):
        data["subcoordinators"].pop(seat)
        data["launched"].pop(f"sub-coordinator {seat}", None)

    state.write(name, retire_seat_write, budget=state.WATCH_WRITE_SECONDS)
    install.forget_reloads(name, f"sub-coordinator {seat}")
    return f"retired the seat {seat}: no open order holds it, so its alias {alias} is removed from cci and nothing starts session {session} again, `claude stop` ends it now when it runs in the background, else it ends at its own exit, and its binding is dropped"


def seat_ended(alias: str, session: str) -> None:
    """End a retired seat's session: its cci alias, so nothing starts it again once Claude Code exits, then the session itself by `claude stop` when it runs in the background; a seat cci runs in tmux ends at its own exit, since no tool of the drive kills a tmux session."""
    subprocess.run(["cci", "session", "rm", alias], capture_output=True, text=True, check=False)
    subprocess.run(["claude", "stop", session], capture_output=True, text=True, check=False)


def retire(name: str, project: str) -> None:
    """Take down the launch agent an older install loaded for a project's own watch, its plist first so no login loads it again, letting go of the project's pid file for the drive's watch to take; a bootout of the agent this process runs under ends this process."""
    release(name, project)
    (launchd.LAUNCH_AGENTS / f"{label(name, project)}.plist").unlink(missing_ok=True)
    if launchd.kept(label(name, project)):
        launchd.launchctl("bootout", launchd.service(label(name, project)))


def stopped(watcher: Watcher, error: Exception) -> None:
    """Say the watch stopped, naming its version and where the error was raised, the innermost frame of this package's code, so the line alone says which line to read."""
    line = f"WATCH stopped on long-running {state.VERSION}: {render.raised(error)}; its traceback is in {state.watch_log_path(watcher.name)}; its launch agent starts it again within {launchd.RESTART_SECONDS} seconds"
    with contextlib.suppress(reads.ReadError):
        sent(watcher, watcher.lane, [line], state.now())


@dataclass
class Pulse:
    """What the watch process is doing now: the step it began, when by time.monotonic, and whether that step is a pass's stage, which may run up to PASS_FOR, or anything else, a rest, a hand-off, its start or its end, which takes seconds."""

    step: str = "start"
    since: float = field(default_factory=monotonic)
    staged: bool = False
    spent: dict[str, list[float]] = field(default_factory=dict)


PULSE = Pulse()


def beat(step: str, staged: bool = False) -> None:
    """Mark the watch as beginning step, logging the step before it when that one was a stage that ran past LOGGED_STAGE, so the log names each slow stage by its time."""
    if PULSE.staged and (took := monotonic() - PULSE.since) >= LOGGED_STAGE.total_seconds():
        print(f"{state.stamp()} stage {PULSE.step} took {took:.0f}s{spent_in(PULSE.spent)}", flush=True)
    PULSE.step, PULSE.since, PULSE.staged, PULSE.spent = step, monotonic(), staged, {}


def spent_in(spent: dict[str, list[float]]) -> str:
    """The calls a slow stage spent longest in, by timed_calls: its three costliest kinds of state.load or subprocess.run, each with its count and seconds, which can sum past the stage's own time when calls ran side by side."""
    top = sorted(spent.items(), key=lambda item: -item[1][1])[:SPENT_SHOWN]
    return "".join(f"; {what} x{count:.0f} {seconds:.0f}s" for what, (count, seconds) in top)


def timed_calls() -> None:
    """Count each state.load and subprocess.run this watch process makes, by the step it runs in, so a slow stage's log line names what it waited on."""
    run, load = subprocess.run, state.load

    def spending(what: str, began: float) -> None:
        held = PULSE.spent.setdefault(what, [0, 0.0])
        held[0] += 1
        held[1] += monotonic() - began

    def timed_run(argv, *rest, **given):
        began = monotonic()
        try:
            return run(argv, *rest, **given)
        finally:
            spending(called(argv), began)

    def timed_load(name, fields=None):
        began = monotonic()
        try:
            return load(name, fields)
        finally:
            spending("state.load" if fields is None else "state.load fields", began)

    subprocess.run, state.load = timed_run, timed_load


def called(argv) -> str:
    """A command's first SPENT_WORDS words, its program by name and any -C directory left out, so each git subcommand counts apart whatever checkout it ran in."""
    if not isinstance(argv, list | tuple):
        return str(argv)[:SPENT_CHARS]
    words = [Path(str(argv[0])).name, *map(str, argv[1:])]
    while "-C" in words:
        at = words.index("-C")
        del words[at : at + 2]
    return " ".join(words[:SPENT_WORDS])[:SPENT_CHARS]


def stalled(pulse: Pulse, now: float) -> bool:
    """Whether the watch is stuck on its step: a stage running past PASS_FOR and a minute more, or anything else past STALL_FOR."""
    return now - pulse.since > (PASS_FOR + SLOW_STAGE if pulse.staged else STALL_FOR).total_seconds()


def guard(path: Path, ended: threading.Event) -> None:
    """Write the watch's heartbeat, its pid, step and how long that step has run, to path every BEAT_SECONDS until ended is set, and end the process once it is stalled on a step, its threads' stacks dumped to its log first, so its launch agent starts a fresh watch within RESTART_SECONDS, or the watch starts a fresh responder, relay or landing loop as RESTART_SECONDS pass, and the log names the line it hung on; no pass, rest, start or end of the watch then holds the drive's watch for longer than PASS_FOR and a minute, or STALL_FOR outside a stage."""
    while True:
        now = monotonic()
        path.write_text(json.dumps({"pid": os.getpid(), "step": PULSE.step, "for": round(now - PULSE.since), "at": state.stamp()}) + "\n")
        if stalled(PULSE, now):
            print(f"{state.stamp()} WATCH stalled {now - PULSE.since:.0f}s on {PULSE.step}, so it ends to be started again; every thread's stack:", flush=True)
            faulthandler.dump_traceback(file=sys.stdout, all_threads=True)
            sys.stdout.flush()
            os._exit(3)
        if ended.wait(BEAT_SECONDS):
            return


def guarded(name: str, flag: str | None) -> threading.Event:
    """Start the guard of this process, the watch's when flag is None, else that of the responder, relay or landing loop flag names, writing its heartbeat beside the watch's, and return the event that ends it."""
    timed_calls()
    beat("start")
    finished = threading.Event()
    threading.Thread(target=guard, args=(beat_path(name, flag), finished), daemon=True).start()
    return finished


def beat_path(name: str, flag: str | None) -> Path:
    """The heartbeat file of the watch, or of the process beside it flag names, as watchd-relay.beat for --relay."""
    return state.drive_dir(name) / (BEAT_FILE if flag is None else BEAT_FILE.replace(".", f"-{flag.lstrip('-')}.", 1))


def main(drive_name: str | None = None, project: str | None = None) -> int:
    """Run the drive's watch as the process its launch agent starts, until it stops, once a watch an older install ran under a Monitor has ended, with the env of this Mac's Claude settings in its environment, so each read it runs carries the API keys a session here carries; it runs each project's passes beside the drive's own over the one copy of the drive this process keeps, by watching. Any other process, such as one an older install's watch re-executed itself into, lets the watch go, loads its agent and ends, so launchd runs every watch; a project's own watch, as an older install's launch agent starts it, takes that agent down and ends, by retire, for the drive's watch to take its project. On a drive whose state root LONG_RUNNING_STATE or XDG_STATE_HOME moves, end at once, so a watch an older install started there ends at its next re-exec."""
    if state.scratch():
        print(f"no watch runs for a drive whose state LONG_RUNNING_STATE or XDG_STATE_HOME moves to {state.root()}", flush=True)
        return 0
    name = state.resolve(drive_name)
    if project is not None:
        print(ensure(name), flush=True)
        retire(name, project)
        return 0
    if os.environ.get(launchd.LAUNCHED_ENV) != label(name):
        release(name)
        print(ensure(name), flush=True)
        return 0
    if not claim(name):
        return 0
    ended = guarded(name, None)
    watcher = None
    try:
        os.environ.update(launchd.session_env())
        children.mark_watch()
        children.reap_inherited()
        while holder(state.monitor_pid_path(name)):
            beat("waiting for the watch a Monitor runs")
            time.sleep(POLL_SECONDS)
        beat("start")
        watcher = watched(name, None)
        watching(watcher)
    finally:
        for project in watcher.others if watcher else []:
            release(name, project)
        release(name)
        ended.set()
    return 0


def watched(name: str, project: str | None) -> Watcher:
    """The watch's watcher of the drive, or of a project, taking what the watch process before it held for it or left in its outbox."""
    return outboxed(Watcher(name, offset=state.watch_cursor(name, project), project=project, delivered=receiver.deliveries_end(), cards=view.live_cards(name), made=taken_lines(name, project, WATCH_OUTBOX), outbox=WATCH_OUTBOX, resume=state.take_held_resume(name, project)))


def taken_lines(name: str, project: str | None, outbox: str) -> list[str]:
    """The lines the process before this one held for it at a hand-off, and those it left in its outbox, which a process ended with no hand-off leaves, each once."""
    return list(dict.fromkeys([*state.take_held_lines(name, project), *state.outboxed_lines(name, project, outbox)]))


def unposted(watcher: Watcher) -> list[str]:
    return list(dict.fromkeys(LINE_BREAKS.sub(" ", line) for line in [*watcher.made, *(line for lines in watcher.unsent.values() for line in lines)]))


def outboxed(watcher: Watcher) -> Watcher:
    """Write the lines the watcher made and has not posted to its outbox on disk whenever they changed, before the watch saves its events cursor past the events they came from, so a watch killed between passes, by a reload's SIGTERM or a kickstart, loses none: the process after it posts them first."""
    lines = unposted(watcher)
    if lines != watcher.outboxed:
        state.keep_outbox(watcher.name, watcher.project, watcher.outbox, lines)
        watcher.outboxed = lines
    return watcher


def handing_off(watcher: Watcher, ran: float) -> bool:
    """Whether the watch may end for a newer install at this pass boundary: once it ran HAND_OFF_EVERY with every project's pass at its last stage, so no install leaves a stage it cut short waiting on the next process and installs landing minutes apart hand it off at most that often; once it ran HAND_OFF_LATEST however many passes stand cut short, since keep_unposted holds each one's resume for the next process and five projects' long passes kept one cut short every boundary, which held a 1.0.1510 watch over fourteen installs for half an hour; or at once while an incident order is open, whose fix must not wait out the clock."""
    data = state.load(watcher.name, ("orders",))
    if ran >= HAND_OFF_LATEST.total_seconds() or state.open_incidents(data, list(data["orders"])):
        return True
    return ran >= HAND_OFF_EVERY.total_seconds() and not any(one.resume for one in [watcher, *watcher.others.values()])


def watching(watcher: Watcher) -> None:
    """Hand each pass's lines to whoever acts on them as each of its stages returns, first any a watch before this one held for it, each open priority order's seat first, by seated_first, then the drive's pass, then each other project's, by joined, each project the watch claims the pid file of, so a project an older install's watch still runs waits for it to end; and end once a newer long-running is installed, only at a pass boundary, never between two of a pass's stages, saying so in its log, so its launch agent starts the watch on that install within RESTART_SECONDS, and a watch reads no install until it has run one whole pass, so installs landing minutes apart never cut a pass short. A pass that runs past PASS_FOR ends before its next stage the same way, that stage kept as its resume, so one slow project holds the others' passes up no longer than that. A crash wakes the session the crashing pass posts to first. Every line made and not yet posted is held for the watch after this one, which hands it on before its first pass."""
    state.save_watch_cursor(watcher.name, watcher.offset, watcher.project)
    signal.signal(signal.SIGTERM, functools.partial(terminated, watcher))
    one = watcher
    passes = 0
    began = monotonic()
    try:
        with relaying(watcher):
            for _ in rests(state.events_path(watcher.name)):
                beat("pass")
                if passes and handing_off(watcher, monotonic() - began) and (newer := upgraded()):
                    print(f"ended on long-running {state.VERSION} for {newer}, before {watcher.resume or 'its next pass'}", flush=True)
                    break
                handing = bool(watcher.made)
                joined(watcher, state.now(), functools.partial(claim, watcher.name), functools.partial(watched, watcher.name))
                for one in seated_first(watcher):
                    read_from, moment = one.offset, state.now()
                    for said in [handed(one, one.made, moment)] if one.made else passed(one, moment, lambda moment=moment: state.now() - moment > PASS_FOR):
                        for line in sent(one, one.lane, said, moment):
                            print(line, flush=True)
                    outboxed(one)
                    if one.offset != read_from:
                        state.save_watch_cursor(one.name, one.offset, one.project)
                passes += not handing
                beat("rest")
            beat("end")
    except Exception as error:
        for each in [watcher, *watcher.others.values()]:
            keep_unposted(each)
        stopped(one, error)
        raise
    for each in [watcher, *watcher.others.values()]:
        keep_unposted(each)


def rests(*paths: Path) -> Iterator[None]:
    """Yield at once, then POLL_SECONDS after each yield, or the moment anything appends to one of the files at paths, such as the receiver's deliveries log as it takes each delivery, so the pass that reads its PRs starts as it lands."""
    with contextlib.ExitStack() as stack:
        queue = select.kqueue()
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            watched = stack.enter_context(open(path, "a"))
            queue.control([select.kevent(watched, select.KQ_FILTER_VNODE, select.KQ_EV_ADD | select.KQ_EV_CLEAR, select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND)], 0)
        while True:
            yield
            queue.control(None, 1, POLL_SECONDS)


def terminated(watcher: Watcher, signum: int, _frame: object) -> None:
    """End the watch on SIGTERM, a reload's or launchd's, holding every watcher's unposted lines for the process after it first, and leaving each events cursor where its last outbox write left it, so the events since replay rather than drop."""
    for each in [watcher, *watcher.others.values()]:
        hold(each)
    os._exit(128 + signum)


def keep_unposted(watcher: Watcher) -> None:
    """Move each line the watch made and has not posted to the watch process after it, which reads events from past the last one they were made from, by hold, and the stage it ended its pass before, so the watch after it starts its first pass's later stages at that one; the lines are held before the cursor moves past their events."""
    if watcher.resume:
        state.hold_resume(watcher.name, watcher.project, watcher.resume)
    hold(watcher)
    state.save_watch_cursor(watcher.name, watcher.offset, watcher.project)


def hold(watcher: Watcher) -> None:
    """Move each line the watch or its landing loop made and has not posted, routed or not, to the process after it, which hands them on before its first pass, and, once it read GitHub, the reads it still owes with the events and deliveries offsets it read to, so the landing loop after it reads those and the PRs the deliveries past that offset name, never every PR again unless it reads GitHub by another query."""
    state.hold_lines(watcher.name, watcher.project, [LINE_BREAKS.sub(" ", line) for line in [*watcher.made, *drained(watcher.relayed), *(line for lines in watcher.unsent.values() for line in lines)]])
    if watcher.owed is not None:
        state.hold_reads(watcher.name, watcher.project, {"owed": sorted(watcher.owed), "merging": sorted(watcher.merging), "stale": sorted(watcher.stale), "untracked": watcher.untracked, "offset": watcher.offset, "delivered": watcher.delivered, "floor": watcher.floor and watcher.floor.isoformat()})
    state.keep_outbox(watcher.name, watcher.project, watcher.outbox, [])
    watcher.made, watcher.unsent, watcher.owed_at, watcher.outboxed = [], {}, {}, []
