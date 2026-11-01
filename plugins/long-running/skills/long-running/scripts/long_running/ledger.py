"""The landing desk's ledger over cc-notes — one row per PR our lanes shipped.

    ledger.py [-C DIR] VERB ...
    ledger.py init    --title TEXT
    ledger.py ask     --ledger ID --text VERBATIM --lane NAME --accept CHECK
    ledger.py drop    --ledger ID --ask ID --reason ...
    ledger.py answer  --ledger ID --ask ID --text ...
    ledger.py live    --ledger ID --at ISO [--text ...]
    ledger.py report  --ledger ID --drive D [--repo owner/name] --pr N --head SHA --lane NAME --verdict clean|red|conflicting|held [--ask ID] [--text ...]
    ledger.py register --ledger ID --lane NAME [--repo owner/name] [--branch-prefix PREFIX] [--pr N]... [--head SHA]
    ledger.py unregister --ledger ID (--lane NAME [--branch-prefix PREFIX] | [--repo owner/name] --pr N...)
    ledger.py unadmit --ledger ID [--repo owner/name] --pr N...
    ledger.py enqueue --ledger ID --drive D [--repo owner/name] --kind p0|ruling|report|idle --pr N --head SHA --lane NAME --text ...
    ledger.py ruling  --ledger ID --drive D [--repo owner/name] --lane NAME --text ... --options "A|B|C" [--pr N]
    ledger.py inbox   --ledger ID --drive D [--take] [--all] [--json] [--shard LANES]
    ledger.py ack     --ledger ID --drive D KEY...
    ledger.py refresh [--repo owner/name] --ledger ID [--pr N]... [--lane PR=NAME]... [--lock PATH] [--shard LANES] [--ccx BIN]
    ledger.py hold    --ledger ID [--repo owner/name] --pr N --reason ... (--until ISO | --hours H) [--stack]
    ledger.py lift    --ledger ID [--repo owner/name] --pr N
    ledger.py priority --ledger ID [--repo owner/name] --pr N --priority N
    ledger.py held    --drive D
    ledger.py route   --repo owner/name --ledger ID [--pr N] [--job TEXT] [--lane NAME] [--train LANE --paths GLOB...] [--fallback LANE] [--dry-run] [--shard LANES]
    ledger.py gone    --ledger ID --lane NAME
    ledger.py label   --repo owner/name --ledger ID (--pr TIP [--expect-head SHA] | --all-clean) [--checkout DIR] [--dry-run] [--shard LANES]
    ledger.py unlabel --repo owner/name --ledger ID --pr N --reason ...
    ledger.py requeue --ledger ID [--repo owner/name] --pr N --expect-head SHA
    ledger.py landed  --repo owner/name --ledger ID --checkout DIR [--pr N]... [--shard LANES]
    ledger.py reconcile --repo owner/name --ledger ID [--checkout DIR] [--dry-run] [--shard LANES] [--ccx BIN]
    ledger.py watch   --repo owner/name --ledger ID --drive D --checkout DIR [--priority N]... [--interval S] [--once] [--shard LANES]
    ledger.py stale   --ledger ID [--repo owner/name] [--minutes N] [--hours H] [--shard LANES]
    ledger.py train   --repo owner/name --ledger ID --paths GLOB... [--cars N] [--shard LANES]
    ledger.py summary --repo owner/name --ledger ID --drive D [--checkout DIR] [--window-seconds N] [--stale-minutes N] [--shard LANES]
    ledger.py show    --ledger ID [--repo owner/name] [--red | --asks] [--json]
    ledger.py progress --ledger ID --ask ID --note TEXT [--artifact URL]...
    ledger.py list    --ledger ID [--repo owner/name] [--lane NAME] [--open] [--json]

STDLIB ONLY. A PR row exists because one of our lanes reported it, because it sits on a
branch under a lane's registered prefix or in the stack of the worktree the lane registered from, or because refresh was handed its number; the
repository's PR list is never read and this script calls no GraphQL itself: ``refresh`` reads ``ccx vcs pr state``
and ``watch`` subscribes through ``ccx vcs pr watch``, both over ccx's machine-wide pull request cache, one poll per
repository at most every 30 seconds however many desks and lanes ask. Holds, routing, the label history, and the landing are fields on that row;
lane messages are ``cci`` records on ``--drive D``, keyed ``#<seq>`` and pending until lane ``ledger``
posts an ``answer`` with ``--re <seq>``; owner asks are ``ask/<seq>`` rows in the same ledger. ``list`` marks an open PR ``ours`` only when the drive admitted it: a lane registered or reported it by name, ``refresh`` found it on a branch prefix or stack a lane registered, or the landing desk registered it for the drive's label. Record text and authorship never admit a PR, and ``label`` refuses a stack holding any PR that is not ours. ``unadmit`` withdraws a PR: its row stays, marked, so neither ``refresh``, a report, nor the desk admits it again until a lane runs ``register --pr`` for it, and a merge label ``label`` set on it is pulled. A landing is proven by a
trunk squash whose subject ends ``(#<pr>)``, or by the trunk's tree in ``--checkout`` holding
the PR's own files, never by the PR's merged field. A verb's ``--repo`` defaults to the origin of its
``--checkout``, else of the working directory, the same checkout ``ccn`` reads the ledger from. A PR row's key
is its repository and number: the bare number in that working directory's own repository, so a ledger written
before rows carried a repository reads unchanged, and ``owner/name#<pr>`` in any other; every ``--pr`` names a PR
in ``--repo``, and ``refresh`` regrades every repository the ledger holds with one ``ccx vcs pr state`` read each. Buildkite
logs come from the repo-pinned ``bk``; storage is ``ccn ledger``. Every subprocess goes
through :class:`Shell`, the one seam tests replace.

Every ``--ask ID`` takes the key ``ask`` printed, ``ask/000104``, or its bare number, ``000104``.

Every ``--ledger ID`` may be left out: it defaults to the ledger of the registered drive ``--drive`` names, by
its id or its cci drive, else of the drive this session runs in, and a verb that carries lane messages
defaults its ``--drive`` to that drive's cci drive the same way.

An owner ask's status is exactly one of LIVE, LANDED-NOT-LIVE, IN-PR, IN-PROGRESS, or LOST, plus the
terminal ``dropped`` and ``answered``. LIVE needs every linked PR landed and a
``ledger.py live`` timestamp at or after the latest of those landings; short of that a
delivered ask reads LANDED-NOT-LIVE. An open linked PR reads IN-PR; no PR at all reads
LOST once the later of ``asked_at`` and its latest ``progress`` entry is 30 minutes old, and
IN-PROGRESS before then when it has a progress entry. A standing ask, recorded with ``ask --standing [HOURS]``
or marked with ``ask --ask ID --standing``, is never done: it reads IN-PROGRESS while the later of ``asked_at``
and its latest progress entry is under HOURS old, 24 by default, and STALE after, never LOST. ``report --ask`` refuses a second, still-IN-PR
ask on one PR: a PR already carrying an unlanded ask takes no more, and a fresh ask goes
on a stacked follow-up PR instead.

``summary`` also prints the prompt watch's last poll for the drive, from ``prompt_watch.py show``:
one line per coordinator, desk, or worker terminal that is at a prompt, stale, unknown, or
unreachable, led by ``PROMPT-WATCH-DOWN`` when the watch stopped polling.

When the shared GraphQL quota is spent, ``reconcile`` and ``summary`` skip the PR-state read
once, land the squashed rows from git alone, and open with a ``pr states: cached`` banner
naming the age of the last refresh and the reset time from a rejected GraphQL call's headers; the report then renders from the rows
as last graded.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Collection
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager, redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from fnmatch import fnmatch
from functools import cache
from itertools import batched
from pathlib import Path
from statistics import median
from urllib.parse import urlencode

from . import cci
from . import ghread

AI_REVIEW_CHECK = "ai-review"
AI_REVIEW_ABSENT = "absent"
FAILED_CHECKS = frozenset({"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "STARTUP_FAILURE", "ACTION_REQUIRED"})
PASSED_CHECKS = frozenset({"SUCCESS", "NEUTRAL", "SKIPPED"})
OWNER_TAP_STATUS = "owner-tap"
STACK_MERGEABILITY_CHECK = "Graphite / mergeability_check"
APPROVED = "APPROVED"
REVIEW_DECISIONS = (APPROVED, "CHANGES_REQUESTED", "DISMISSED")
PAGE_SIZE = 100
ROUTE_STATES = ("dirty", "blocked")
TOUCHED_FIELDS = ("reported_head", "registered_head", "labelled_at", "label_head", "hold_until")
KINDS = ("p0", "ruling", "report", "idle")
VERDICTS = cci.REPORT_VERDICTS
MERGE_LABEL = "merge"
DRIVE_ENV = "CLAUDE_LONG_RUNNING_DRIVE"
STACK_ENQUEUE = ".agents/skills/submit-pr/scripts/stack-enqueue"
STACK_ENQUEUE_UNSETTLED = 2
STACK_ENQUEUE_BLOCKED = re.compile(r"^#(\d+) BLOCKED \S+ (.*)$", re.MULTILINE)
LABELLABLE_STATES = ("clean", "behind", "has_hooks")
FAILED_CONCLUSIONS = ("failure", "timed_out", "cancelled", "action_required")
SUMMARY_LINES = 10
WINDOW_SECONDS = 3600
STALE_MINUTES = 30
NO_PR = "-"
MESSAGE_KINDS = {"p0": "blocker", "ruling": "decide", "report": "report", "idle": "note"}
LEDGER_KINDS = {kind: name for name, kind in MESSAGE_KINDS.items()}
MESSAGE_ANSWERER = "ledger"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
CCN_READ_ATTEMPTS = 4
CCN_READ_BACKOFF_SECONDS = 0.5
REF_LOCK_ATTEMPTS = 5
REF_LOCK_BACKOFF_SECONDS = 0.2
REF_LOCK_RACE = "cannot lock ref"
COMMAND_TIMEOUT_SECONDS = 600
LOCK_WAIT_SECONDS = 120
LOCK_POLL_SECONDS = 0.2
LANE_PREFIX = "lane/"
ASK_PREFIX = "ask/"
OWNER_PRIORITY = 0
DEFAULT_PRIORITY = 100
ASK_KEY = re.compile(rf"(?:{ASK_PREFIX})?(?P<seq>\d+)")
REMOTE = re.compile(r"[:/](?P<repo>[\w.-]+/[\w.-]+?)(?:\.git)?/?$")
PR_KEY = re.compile(r"(?:(?P<repo>[\w.-]+/[\w.-]+)#)?(?P<pr>\d+)")
GONE_PREFIX = "gone/"
DEV_RED_PREFIX = "dev-red:"
AGED_HOURS = 60
TRAIN_CARS = 6
TRAIN_TIERS = ("green", "dirty")
LIVE_KEY = "live"
PROMPT_WATCH = Path(__file__).resolve().parents[1] / "prompt_watch.py"
LOST_MINUTES = 30
STANDING_HOURS = 24
ESCALATE_MINUTES = 60
ASK_LIVE = "LIVE"
ASK_LANDED_NOT_LIVE = "LANDED-NOT-LIVE"
ASK_IN_PR = "IN-PR"
ASK_LOST = "LOST"
ASK_IN_PROGRESS = "IN-PROGRESS"
ASK_STALE = "STALE"
PROGRESS_PREFIX = "progress_"
ASK_DROPPED = "dropped"
ASK_BACKLOG = "BACKLOG"
ASK_OPEN = "open"
ASK_OVERRIDES = {"backlog": ASK_BACKLOG, "live": ASK_LIVE, "lost": ASK_LOST}
ASK_STATES = (*ASK_OVERRIDES, ASK_OPEN)
ASK_ANSWERED = "answered"
WAITING_REASONS = ("ungraded", "refused", "red", "held")
UNROUTED_REFUSALS = ("moved", "fetched", "held", "labelled")
QUEUE_BOTS = frozenset({"graphite-app[bot]", "trunk-io[bot]"})
LANDED = "landed"
CLOSED_WITHOUT_SQUASH = "closed-without-squash"
SQUASH_SUBJECT = re.compile(r"\(#(\d+)\)$")
SETTLE_WORKERS = 8
REFRESH_BATCH = 25
PR_STATE_MAX_AGE = "2m"
REFRESH_WORKERS = 16
TERMINAL_STATES = frozenset({LANDED, CLOSED_WITHOUT_SQUASH})
REOPEN_WINDOW = timedelta(hours=24)
WATCH_P0_EVENTS = frozenset({"ejected", "conflicting", "red"})
HOLD_FIELDS = ("hold_reason", "hold_since", "hold_until")

RATE_LIMITED = re.compile(r"rate limit", re.IGNORECASE)
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
BK_TIMESTAMP = re.compile(r"^\x1b?_bk;t=\d+\x07?")
BK_SECTION = re.compile(r"^(~~~|---|\+\+\+|\^\^\^|\$ |# )")
BK_SOURCE_ECHO = re.compile(r"^\d+\s*\|")
BUILDKITE_URL = re.compile(
    r"^https://buildkite\.com/(?P<org>[^/]+)/(?P<pipeline>[^/]+)/builds/(?P<build>\d+)"
    r"(?:[#?]jid=|#)?(?P<job>[0-9a-f-]{36})?"
)
ERROR_LINE = re.compile(
    r"(?i)(^|\W)(assert\w*|error|errors|failed|failure|fatal|panic|exception|expected|traceback)(\W|$)"
)
WRAPPER_NOISE = (
    "the command exited with status",
    "user command error",
    "running commands",
    "running repository",
    "running agent",
    "exited with code",
)
BK_PROBE = re.compile(r"failed to get (?:step|meta-data)")

SHOW_COLUMNS = (
    ("pr", "key"),
    ("state", "state"),
    ("head", "head"),
    ("base", "base"),
    ("test", "test_state"),
    ("mergeable", "mergeable_state"),
    ("ai-review", "ai_review"),
    ("files", "changed_files"),
    ("lane", "lane"),
    ("hold", "hold_reason"),
    ("routed", "routed_job"),
    ("label", "label_head"),
    ("intent", "declared_intent"),
    ("branch", "branch"),
)

REBASE_TEXT = (
    "This branch conflicts with `{base}`. Rebase it onto the base branch and push — "
    "a conflicting head is never graded, so no CI result on `{head}` means anything "
    "until the rebase lands."
)
BLOCKED_TEXT = (
    "Mergeable state is `{state}` on `{head}`: the merge is held by a required check or "
    "review that has not reported, not by the diff. Clear the held requirement or say "
    "what it is waiting on."
)
FAILURE_TEXT = "CI is red on `{head}`.\n\nFirst error from {url}:\n\n```\n{error}\n```"
NO_ERROR_TEXT = "CI is red on `{head}` but {url} has no error line — the job was killed rather than failing an assertion."
NO_TARGET_TEXT = (
    "CI is red on `{head}` and no failing Buildkite job is attached to it. The red is a "
    "commit status or check run outside Buildkite; read it on the PR."
)
ROUTE_TEXT = (
    "Push the fix as a new head on the same branch, then send landing-desk the 3-line report "
    "(PR, head, verdict); the merge label waits on that head reading green and merge-clean."
)
REFUSAL = {
    "closed": "#{pr} is {state}; a landing is read from the {base} tree, never labelled",
    "moved": "head moved: expected {expected}, the forge has {head}; grade the new head before labelling",
    "held": "#{pr} is held: {reason} until {until}",
    "labelled": "{head} was labelled at {at}; a head carries the label once, and a strip is not a rejection: read the Merge activity comment",
    "pulled": "{head} had its label pulled at {at} ({reason}); the same head is never re-queued",
    "mergeable": "mergeable_state {state}: only {allowed} may be labelled",
    "status": "commit status {state} on {head}; only success is labelled",
    "checks": "failed check runs on {head}: {names}",
    "conflict": "{head} conflicts with {base} on {paths}; route the rebase, never label",
    "fetched": "refs/pull/{pr}/head is {fetched} on the forge, not {head}",
    "unapproved": "#{pr} has no approval in force: a reviewer's latest decision must be APPROVED, never dismissed or withdrawn, and mergeable_state is no proxy",
    "reviewing": "ai-review still reviewing {head}; retry once its latest run completes",
    "ai-review": "ai-review is {state} on {head}; only success is labelled, and `neutral` is a held blocking finding whose reason is a review comment on the diff",
    "children": "#{pr}'s branch {branch} is the base of {children}, which no lane tracks in a native GitHub stack or Graphite's stack record; link them with ccx vcs stack submit, or retarget them to {trunk} BEFORE labelling, or the branch delete closes them unrecoverably",
    "orphaned": "#{pr} is based on {base}, which is neither {trunk} nor exactly one open pull request's branch (found {found}); retarget it to {trunk}",
    "cycle": "#{pr} is its own ancestor through {stack}; retarget the stack to its trunk",
    "foreign": "#{pr} is in #{tip}'s stack, but no drive lane registered it in the ledger, by name or by a branch prefix or stack its branch is on, and it carries no drive label; the desk never enqueues a PR the drive did not admit",
    "stack": "the stack {stack} enqueues as one entry, so {refused} refuses all of it; nothing was labelled",
    "enqueue": "stack-enqueue refused #{pr} (stack tip #{tip}): {detail}",
    "shallow": "{checkout} is a shallow clone; trunk traversal truncates at a depth that moves with each fetch. Run: git fetch --unshallow origin",
    "fetch": "fetching {ref} failed, so this pass has graded nothing: {detail}",
    "checkout": "{checkout} is not a usable git checkout, so this pass has graded nothing: {detail}",
}


def is_ccn_read(argv: list[str]) -> bool:
    return argv[:3] == ["ccn", "ledger", "show"]


def is_git_fetch(argv: list[str]) -> bool:
    return argv[0] == "git" and "fetch" in argv[1:4]


class Shell:
    """The single subprocess boundary: `gh`, `bk`, `git`, `ccx`, and `ccn` all pass through here.

    A `ccn ledger show` exits non-zero while another desk is writing the same ledger, so
    reads retry with a short backoff; writes never do, because a retried write can land twice.
    A `git fetch` that loses a ref lock to a concurrent fetch retries the same way, since a
    forced refspec lands once however many times it runs.
    Input reaches the child from a file, never a pipe this process writes while the child's
    output fills. A command still running after COMMAND_TIMEOUT_SECONDS, or when anything else
    ends this call, dies with everything under it in this process's group. A command that is
    itself one of the plugin's scripts is told so through `drive.deadline_env`, and kills its own
    commands before dying at that deadline, which reads as the same timeout. Commands stay in this
    process's group, so a signal sent to the group still reaches them.
    """

    def run(self, argv: list[str], stdin: str | None = None, cwd: Path | None = None) -> str:
        for attempt in range(CCN_READ_ATTEMPTS - 1 if is_ccn_read(argv) else 0):
            try:
                return self.once(argv, stdin, cwd)
            except subprocess.CalledProcessError:
                time.sleep(CCN_READ_BACKOFF_SECONDS * 2**attempt)
        for attempt in range(REF_LOCK_ATTEMPTS - 1 if is_git_fetch(argv) else 0):
            try:
                return self.once(argv, stdin, cwd)
            except subprocess.CalledProcessError as error:
                if REF_LOCK_RACE not in (error.stderr or ""):
                    raise
                time.sleep(REF_LOCK_BACKOFF_SECONDS * 2**attempt)
        return self.once(argv, stdin, cwd)

    @staticmethod
    def once(argv: list[str], stdin: str | None, cwd: Path | None = None) -> str:
        from . import drive

        with tempfile.TemporaryFile("w+") as feed:
            feed.write(stdin or "")
            feed.seek(0)
            with cci.installed(argv, lambda: subprocess.Popen(argv, stdin=feed, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=cwd, env=os.environ | drive.deadline_env(COMMAND_TIMEOUT_SECONDS - drive.REAP_SECONDS))) as child:
                try:
                    out, err = child.communicate(timeout=COMMAND_TIMEOUT_SECONDS)
                except BaseException:
                    drive.kill([child.pid, *drive.under(child.pid)])
                    raise
        if child.returncode == -signal.SIGALRM:
            raise subprocess.TimeoutExpired(argv, COMMAND_TIMEOUT_SECONDS, out, err)
        if child.returncode:
            raise subprocess.CalledProcessError(child.returncode, argv, out, err)
        return out


class ForgeUnreachable(RuntimeError):
    """The forge did not answer, so this pass has graded nothing worth writing."""


class QuotaExhausted(ForgeUnreachable):
    """The shared GraphQL quota is spent; PR states cannot be read until it resets."""


class Refused(Exception):
    """One guard refused one pull request; the text names the reason."""

    def __init__(self, text: str, kind: str = ""):
        super().__init__(text)
        self.kind = kind


def refusal(kind: str, **values: object) -> Refused:
    return Refused(REFUSAL[kind].format(**values), kind)


@dataclass
class Github:
    shell: Shell
    repo: str

    def api(self, path: str, **params: object) -> object:
        endpoint = f"repos/{self.repo}/{path}"
        if params:
            endpoint = f"{endpoint}?{urlencode(params)}"
        return json.loads(self.shell.run(ghread.read("api", endpoint)))

    def paged(self, path: str) -> list[dict]:
        items: list[dict] = []
        page = 1
        while batch := self.api(path, per_page=PAGE_SIZE, page=page):
            items += batch
            page += 1
        return items

    def add_label(self, number: str, name: str) -> None:
        self.shell.run(
            ["gh", "api", f"repos/{self.repo}/issues/{number}/labels", "--method", "POST", "--input", "-"],
            stdin=json.dumps({"labels": [name]}),
        )

    def remove_label(self, number: str, name: str) -> None:
        self.shell.run(["gh", "api", f"repos/{self.repo}/issues/{number}/labels/{name}", "--method", "DELETE"])

    def default_branch(self) -> str:
        try:
            return json.loads(self.shell.run(ghread.read("api", f"repos/{self.repo}")))["default_branch"]
        except subprocess.CalledProcessError as failure:
            raise ForgeUnreachable(forge_failure(failure)) from failure


def origin_repo(shell: Shell, checkout: Path) -> str:
    url = shell.run(["git", "-C", str(checkout), "remote", "get-url", "origin"]).strip()
    if not (match := REMOTE.search(url)):
        raise SystemExit(f"origin {url!r} names no owner/name")
    return match["repo"]


def working_repo(shell: Shell, named: str | None) -> str:
    try:
        return origin_repo(shell, Path("."))
    except subprocess.CalledProcessError:
        if named:
            return named
        raise


def inside_checkout() -> bool:
    here = Path.cwd()
    return any((folder / ".git").exists() for folder in (here, *here.parents))


def registered_drive(ledger_id: str, bus: str) -> dict | None:
    """The registered drive whose ledger starts with ledger_id, or whose id or cci drive is bus."""
    from . import drive

    return next(
        (
            entry
            for entry in drive.drives()
            if (ledger_id and str(entry.get("ledger") or "").startswith(ledger_id)) or (bus and bus in (entry.get("drive"), drive.cci_drive(entry)))
        ),
        None,
    )


def registry_checkout(args: argparse.Namespace) -> str | None:
    """The checkout of the registered drive whose ledger or cci drive the command names."""
    entry = registered_drive(vars(args).get("ledger") or "", vars(args).get("drive") or "")
    return entry.get("checkout") if entry else None


def default_from_drive(args: argparse.Namespace) -> None:
    """Fill a missing --ledger, and a bus verb's missing --drive, from the drive --drive names, else the current one."""
    from . import drive

    if args.ledger and (args.drive or not args.bus):
        return
    bus = args.drive or ("" if args.ledger else drive.current_drive() or drive.orca_run_drive() or "")
    if not (entry := registered_drive(args.ledger or "", bus)):
        named = f"--ledger {args.ledger}" if args.ledger else f"--drive {bus}" if bus else "this session"
        raise SystemExit(f"no registered drive in {drive.drives_dir()} matches {named}; pass --ledger ID and --drive D")
    args.ledger = args.ledger or entry["ledger"]
    if args.bus:
        args.drive = drive.cci_drive(entry)


def same_repo(left: str, right: str) -> bool:
    return left.lower() == right.lower()


def is_pr_key(key: str) -> bool:
    return bool(PR_KEY.fullmatch(key))


def pull_rows(rows: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    return {key: fields for key, fields in rows.items() if is_pr_key(key)}


def pr_order(key: str) -> tuple[str, int]:
    match = PR_KEY.fullmatch(key)
    return (match["repo"] or "").lower(), int(match["pr"])


def ref(key: str) -> str:
    return key if "#" in key else f"#{key}"


@dataclass(frozen=True)
class Scope:
    """One repository's view of a ledger: its PR rows under bare numbers, every other repository's PR rows out of sight.

    A PR row is stored under its bare number in the ledger checkout's own repository, `default`, and as `owner/name#<pr>` in any other.
    """

    repo: str
    default: str

    def key(self, pr: str) -> str:
        return pr if same_repo(self.repo, self.default) else f"{self.repo.lower()}#{pr}"

    def store(self, key: str) -> str:
        return self.key(key) if key.isdigit() else key

    def local(self, key: str) -> str | None:
        if not (match := PR_KEY.fullmatch(key)):
            return key
        if not same_repo(match["repo"] or self.default, self.repo):
            return None
        return match["pr"]

    def view(self, stored: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        return {local: fields for key, fields in stored.items() if (local := self.local(key)) is not None}

    def lanes(self, stored: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        return {key: fields for key, fields in stored.items() if key.startswith(LANE_PREFIX) and not is_pr_key(key) and same_repo(fields.get("repo") or self.default, self.repo)}


def row_repo(key: str, fields: dict[str, str], default: str) -> str | None:
    if match := PR_KEY.fullmatch(key):
        return match["repo"] or default
    if key.startswith(LANE_PREFIX):
        return fields.get("repo") or default
    return None


def ledger_repos(stored: dict[str, dict[str, str]], scope: Scope) -> list[str]:
    """Every repository the ledger holds a PR row or a lane registration for, and the scope's own."""
    repos = {scope.repo.lower(): scope.repo}
    for key, fields in stored.items():
        if repo := row_repo(key, fields, scope.default):
            repos.setdefault(repo.lower(), repo)
    return sorted(repos.values(), key=str.lower)


def is_malformed(key: str) -> bool:
    """A PR key that is neither a bare number nor `owner/name#<pr>`, or an ask key whose sequence is not one."""
    if is_pr_key(key):
        return False
    if key[:1].isdigit():
        return not key.isdigit()
    return key.startswith(ASK_PREFIX) and not key[len(ASK_PREFIX) :].isdigit()


@dataclass
class Notes:
    """A ledger's rows, under `scope` when one is given, else under the keys they are stored at."""

    shell: Shell
    ledger: str
    scope: Scope | None = None
    warned: bool = False

    def show(self) -> dict:
        return json.loads(self.shell.run(["ccn", "ledger", "show", self.ledger, "--json"]))

    def key(self, pr: str) -> str:
        return self.scope.key(pr) if self.scope else pr

    def store(self, key: str) -> str:
        return self.scope.store(key) if self.scope else key

    def view(self, stored: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        return self.scope.view(stored) if self.scope else stored

    def rows(self) -> dict[str, dict[str, str]]:
        return self.view(self.stored())

    def stored(self) -> dict[str, dict[str, str]]:
        payload = self.show()
        malformed = [row["key"] for row in payload["rows"] if is_malformed(row["key"])]
        if malformed and not self.warned:
            self.warned = True
            print(f"ledger {self.ledger[:8]}: skipping malformed keys {', '.join(map(repr, malformed))}; remove each with: ccn ledger row rm {self.ledger} --key KEY", file=sys.stderr)
        return {row["key"]: row["fields"] for row in payload["rows"] if row["key"] not in malformed}

    def pr_rows(self) -> dict[str, dict[str, str]]:
        return {key: fields for key, fields in self.rows().items() if key.isdigit()}

    def asks(self) -> dict[str, dict[str, str]]:
        return {key: fields for key, fields in self.rows().items() if key.startswith(ASK_PREFIX)}

    def sync(self, rows: list[dict]) -> None:
        stored = [dict(row, key=self.store(row["key"])) for row in rows]
        self.shell.run(["ccn", "ledger", "sync", self.ledger, "--file", "-"], stdin=json.dumps(stored))

    def set_fields(self, key: str, fields: dict[str, str]) -> None:
        argv = ["ccn", "ledger", "row", "set", self.ledger, "--key", self.store(key)]
        for name, value in fields.items():
            argv += ["--field", f"{name}={value}"]
        self.shell.run(argv)

    def remove(self, key: str) -> None:
        self.shell.run(["ccn", "ledger", "row", "rm", self.ledger, "--key", self.store(key)])


@dataclass
class Messages:
    """Lane messages on the drive's cci records: a p0 is a blocker, a ruling a decide, an idle a note; an answer from `ledger` takes one."""

    shell: Shell
    drive: str

    def post(self, fields: dict[str, str]) -> cci.Posted:
        argv = ["cci", "post", "--drive", self.drive, "--lane", fields["lane"], "--kind", MESSAGE_KINDS[fields["kind"]], "--topic", fields["pr"], "--text", fields["text"]]
        argv += ["--pr", fields["pr"]] if PR_KEY.fullmatch(fields["pr"]) else []
        argv += ["--commit", fields["head"]] if FULL_SHA.match(fields["head"]) else []
        return cci.posted(self.shell.run([*argv, "--json"]))[0]

    def answer(self, key: str, fields: dict[str, str], text: str) -> None:
        self.shell.run(["cci", "post", "--drive", self.drive, "--lane", MESSAGE_ANSWERER, "--kind", "answer", "--re-seq", key.lstrip("#"), "--topic", fields["pr"], "--text", f"{text} {key}"])

    def records(self, kinds: Collection[str], lanes: Collection[str]) -> list[dict]:
        """The drive's records of `kinds` from `lanes`, every lane when empty; `cci tail` also carries this session's peer mail from any drive, kind, or lane."""
        found = cci.records(self.shell.run, self.drive, *(f"--kind={kind}" for kind in kinds), *(f"--lane={lane}" for lane in sorted(lanes)))
        return [record for record in found if record["drive"] == self.drive and record["kind"] in kinds and (not lanes or record["lane"] in lanes)]

    def read(self, shard: frozenset[str] | None) -> dict[str, dict[str, str]]:
        """Every lane message, oldest first, keyed `#<seq>`; one is pending until `ledger` answers it."""
        records = self.records(LEDGER_KINDS, shard or ())
        answered = {record["re"] for record in self.records(("answer",), (MESSAGE_ANSWERER,)) if record.get("re")}
        return {
            f"#{record['seq']}": {
                "kind": LEDGER_KINDS[record["kind"]],
                "pr": record.get("topic") or NO_PR,
                "head": record.get("fields", {}).get("commit") or NO_PR,
                "lane": record["lane"],
                "text": record["text"],
                "at": record["at"],
                "state": "acked" if record["seq"] in answered else "pending",
            }
            for record in records
        }


def now() -> datetime:
    return datetime.now(timezone.utc)


def stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_stamp() -> str:
    return stamp(now())


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@contextmanager
def locked(path: Path):
    """Hold the ledger lock, naming this process in the lock file; a waiter gives up after LOCK_WAIT_SECONDS and names the holder."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    handle.seek(0)
                    holder = handle.read().strip() or "a process that did not name itself"
                    raise SystemExit(f"{path} is still held after {LOCK_WAIT_SECONDS}s by {holder}; nothing written, rerun once it finishes")
                time.sleep(LOCK_POLL_SECONDS)
        handle.truncate(0)
        handle.write(f"pid {os.getpid()} since {utc_stamp()}\n")
        handle.flush()
        try:
            yield
        finally:
            handle.truncate(0)
            fcntl.flock(handle, fcntl.LOCK_UN)


def held(path: Path) -> bool:
    if not path.exists():
        return False
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle, fcntl.LOCK_UN)
        return False


def default_lock(ledger: str) -> Path:
    return Path.home() / ".cache" / "ccn-ledger" / f"{ledger}.lock"


def is_shallow(shell: Shell, checkout: Path) -> bool:
    try:
        return shell.run(["git", "-C", str(checkout), "rev-parse", "--is-shallow-repository"]).strip() != "false"
    except subprocess.CalledProcessError as error:
        raise ForgeUnreachable(REFUSAL["checkout"].format(checkout=checkout, detail=(error.stderr or "").strip())) from error


def fetch(shell: Shell, checkout: Path, *refs: str) -> None:
    """Fetch, converting a failure into ForgeUnreachable.

    A concurrent fetch in another worktree loses the ref lock, and a pass that grades
    after a failed fetch grades the previous state while reporting it as current.
    """
    try:
        shell.run(["git", "-C", str(checkout), "fetch", "-q", "origin", *refs])
    except subprocess.CalledProcessError as error:
        raise ForgeUnreachable(REFUSAL["fetch"].format(ref=" ".join(refs), detail=(error.stderr or "").strip())) from error


def open_children(gh: Github, branch: str) -> list[dict]:
    """Open pull requests whose base is this branch.

    Scoped to one branch we own, never a repository listing. A child left on a parent's
    branch is closed by the forge when that branch is deleted, and reopening is refused,
    so the retarget has to happen before the parent carries a label rather than in a
    race with its landing.
    """
    return gh.api("pulls", base=branch, state="open")


def stacked(gh: Github, rows: dict[str, dict[str, str]], child: dict) -> bool:
    """A tracked child GitHub lists in a native stack, or carrying Graphite's mergeability check on a legacy Graphite stack, is in its stack's record, so its parent's landing retargets it."""
    if not is_tracked(rows.get(str(child["number"]), {})):
        return False
    if child.get("stack"):
        return True
    return bool(gh.api(f"commits/{child['head']['sha']}/check-runs", check_name=STACK_MERGEABILITY_CHECK, per_page=PAGE_SIZE)["check_runs"])


def queue_ejected(gh: Github, pr: str) -> str:
    """When did the queue eject this pull request, if it did?

    An ejection and a landing look identical: both end with the queue's bot removing
    the merge label. Only the events separate them, and nothing announces an ejection.
    """
    events = gh.api(f"issues/{pr}/events?per_page=100")
    labelled = [e["created_at"] for e in events if e["event"] == "labeled" and e["label"]["name"] == MERGE_LABEL]
    ejected = [
        e["created_at"]
        for e in events
        if e["event"] == "unlabeled" and e["label"]["name"] == MERGE_LABEL and e["actor"]["login"] in QUEUE_BOTS
    ]
    if ejected and (not labelled or ejected[-1] > labelled[-1]):
        return ejected[-1]
    return ""


def ai_review(contexts: list[dict]) -> str:
    """The newest ai-review run's verdict; a rollup lists a check's re-runs oldest first."""
    runs = [context for context in contexts if context.get("__typename") == "CheckRun" and context.get("name") == AI_REVIEW_CHECK]
    if not runs:
        return AI_REVIEW_ABSENT
    return (runs[-1].get("conclusion") or runs[-1].get("status") or "").lower()


def latest_ai_review(gh: Github, head: str) -> dict | None:
    runs = gh.api(f"commits/{head}/check-runs", check_name=AI_REVIEW_CHECK)["check_runs"]
    return max(runs, key=lambda run: run["started_at"], default=None)


def approvers(gh: Github, pr: str) -> list[str]:
    decisions = {review["user"]["login"]: review["state"] for review in gh.paged(f"pulls/{pr}/reviews") if review["state"] in REVIEW_DECISIONS}
    return sorted(login for login, state in decisions.items() if state == APPROVED)


def pr_state(shell: Shell, ccx: str, repo: str, prs: list[str], prefixes: list[str]) -> dict:
    """One read of ccx's machine-wide pull request cache, which every desk and watcher on the machine shares."""
    if not prs and not prefixes:
        return {"lanes": {}, "prs": {}}
    argv = [ccx, "vcs", "pr", "state", "--repo", repo, "--max-age", PR_STATE_MAX_AGE, *prs]
    for prefix in prefixes:
        argv += ["--lane-prefix", prefix]
    try:
        return json.loads(shell.run(argv))
    except subprocess.CalledProcessError as failure:
        if RATE_LIMITED.search(failure.stderr or ""):
            raise QuotaExhausted(f"ccx vcs pr state: {failure.stderr.strip()}") from failure
        raise


def forge_failure(failure: subprocess.CalledProcessError) -> str:
    """The read that failed and why, in the failure's own words: a quota refusal from ccx names the pool and its reset, so no further GitHub call asks."""
    return f"{' '.join(ghread.gh_args(failure.cmd)[:3])}: {' '.join((failure.stderr or str(failure)).split())}"


def is_gh_api(failure: subprocess.CalledProcessError) -> bool:
    return ghread.gh_args(failure.cmd)[:2] == ["gh", "api"]


def cache_banner(exhausted: QuotaExhausted, rows: dict[str, dict[str, str]], moment: datetime) -> str:
    refreshed = max((fields["last_refresh"] for fields in rows.values() if fields.get("last_refresh")), default="")
    age = f"{age_minutes(refreshed, moment)}m" if refreshed else "age unknown"
    return f"pr states: cached {age} ({' '.join(str(exhausted).split())})"


def failing_checks(contexts: list[dict], repo: str) -> str:
    """Each check whose newest run on the head failed, as a JSON list of its name and URL, off the rollup the row's test_state comes from, so a reader names the red check without a GitHub call (root #145266)."""
    newest = {node.get("name") or node.get("context"): node for node in contexts}
    return json.dumps([
        {"name": name, "url": f"https://github.com/{repo}/runs/{node['databaseId']}" if node["__typename"] == "CheckRun" else node.get("targetUrl") or ""}
        for name, node in newest.items()
        if (node.get("conclusion") or node.get("state") or "").upper() in FAILED_CHECKS
    ])


def with_errors(shell: Shell, checks: str, stored: str | None) -> str:
    """Each failing check with `job`, the name of its failed Buildkite job, `error`, that job's first error line, and `probe`, whether that line is only Buildkite's benign step-export or meta-data 404 probe, read once per check URL and kept from the stored row after that; a check outside Buildkite, or whose log read fails or names no failed job, carries neither and is read again on the next refresh (root #148571)."""
    known = {check["url"]: check for check in json.loads(stored or "[]") if "probe" in check}
    enriched = []
    for check in json.loads(checks):
        if check["url"] in known:
            check |= {name: known[check["url"]][name] for name in ("job", "error", "probe")}
        elif target := BUILDKITE_URL.match(check["url"]):
            try:
                check["job"], check["error"] = resolve_error(shell, target)
                check["probe"] = bool(BK_PROBE.search(check["error"]))
            except (subprocess.CalledProcessError, StopIteration):
                pass
        enriched.append(check)
    return json.dumps(enriched)


def tests_state(record: dict) -> str:
    status = (record.get("status") or "pending").lower()
    newest = {node.get("name") or node.get("context"): node for node in ((record.get("rollup") or {}).get("contexts") or {}).get("nodes", [])}
    if status == "pending" and (newest.get(OWNER_TAP_STATUS) or {}).get("state") == "PENDING" and all((node.get("conclusion") or node.get("state") or "").upper() in PASSED_CHECKS for name, node in newest.items() if name != OWNER_TAP_STATUS):
        return "success"
    return status


def grade(record: dict, repo: str) -> dict[str, str]:
    """A row's graded fields, off one record whose head and verdicts a single poll read together."""
    rollup = record.get("rollup") or {}
    return {
        "state": "open" if record["state"] == "OPEN" else "closed",
        "head": record["headRefOid"],
        "base": record["baseRefName"],
        "branch": record["headRefName"],
        "author": record.get("author", ""),
        "title": record["title"],
        "created_at": record["createdAt"],
        "test_state": tests_state(record),
        "mergeable_state": record["mergeStateStatus"].lower(),
        "labels": ",".join(record.get("labels") or []),
        "ai_review": ai_review(rollup.get("contexts", {}).get("nodes", [])),
        "failing_checks": failing_checks(rollup.get("contexts", {}).get("nodes", []), repo),
        "changed_files": str(record["changedFiles"]),
    }


def is_open(fields: dict[str, str]) -> bool:
    return fields.get("state", "open") == "open"


def needs_route(fields: dict[str, str]) -> bool:
    return is_open(fields) and (fields.get("test_state") == "failure" or fields.get("mergeable_state") in ROUTE_STATES)


def is_held(fields: dict[str, str]) -> bool:
    return bool(fields.get("hold_until"))


def carries_label(fields: dict[str, str]) -> bool:
    labelled_head = fields.get("label_head", "")
    on_head = bool(labelled_head) and labelled_head == fields.get("head") and bool(fields.get("labelled_at")) and not fields.get("label_pulled_at")
    return MERGE_LABEL in fields.get("labels", "").split(",") or on_head


def current_head(fields: dict[str, str]) -> str:
    return fields.get("head") or fields.get("reported_head", "")


def is_tracked(fields: dict[str, str]) -> bool:
    return bool(fields.get("reported_head") or fields.get("registered"))


def is_ours(fields: dict[str, str]) -> bool:
    """The drive admitted the PR and has not withdrawn it: a lane registered or reported it by name, `refresh` found it on a branch prefix or stack a lane registered, or the landing desk registered it for the drive's label; record text and authorship never count."""
    return is_tracked(fields) and not fields.get("unadmitted")


def lane_held(fields: dict[str, str]) -> bool:
    return fields.get("reported_verdict") == "held" and fields.get("reported_head") == current_head(fields)


def dev_red_held(fields: dict[str, str], moment: datetime) -> bool:
    return fields.get("hold_reason", "").startswith(DEV_RED_PREFIX) and parse_iso(fields["hold_until"]) > moment


def stacked_above(rows: dict[str, dict[str, str]], pr: str) -> list[str]:
    """Every open row stacked on this one, nearest first, walked through the ledger's base and branch fields."""
    above: list[str] = []
    frontier = [pr]
    while frontier:
        branch = rows.get(frontier.pop(0), {}).get("branch")
        children = sorted((key for key, fields in rows.items() if branch and fields.get("base") == branch and is_open(fields) and key not in above), key=int)
        above += children
        frontier += children
    return above


def touches(files: list[str], globs: list[str]) -> bool:
    return any(fnmatch(name, glob) for name in files for glob in globs)


def pr_files(gh: Github, pr: str) -> list[str]:
    return [row["filename"] for row in gh.paged(f"pulls/{pr}/files")]


def waiting_reason(fields: dict[str, str]) -> str:
    """Why a tracked open row has no labelable head, or "" when it has one or is already queued."""
    if not is_open(fields) or not is_tracked(fields) or carries_label(fields):
        return ""
    if is_held(fields) or lane_held(fields):
        return "held"
    if needs_route(fields):
        return "red"
    return "refused" if fields.get("label_refused_head") == current_head(fields) else "ungraded"


def waiting_line(rows: dict[str, dict[str, str]]) -> list[str]:
    groups = {reason: [pr for pr in sorted(rows, key=int) if waiting_reason(rows[pr]) == reason] for reason in WAITING_REASONS}
    parts = [f"{reason} " + " ".join(f"#{pr}" for pr in prs) for reason, prs in groups.items() if prs]
    return ["waiting: " + " | ".join(parts)] if parts else []


def shard_lanes(value: str) -> frozenset[str]:
    return frozenset(lane.strip() for lane in value.split(",") if lane.strip())


def sharded(rows: dict[str, dict[str, str]], shard: frozenset[str] | None) -> dict[str, dict[str, str]]:
    """The rows a shard owns: those whose lane is in it, PR rows and messages alike."""
    if shard is None:
        return rows
    return {key: fields for key, fields in rows.items() if fields.get("lane") in shard}


def is_clean_reported(fields: dict[str, str]) -> bool:
    return is_open(fields) and fields.get("reported_verdict") == "clean"


def blocker(fields: dict[str, str]) -> str:
    head = current_head(fields)
    if is_held(fields):
        return f"held: {fields['hold_reason']} until {fields['hold_until']}"
    if carries_label(fields):
        if fields.get("labelled_at") and fields.get("label_head") == head:
            return f"in the queue since {fields['labelled_at']}"
        return "in the queue, labelled outside the desk"
    if fields.get("routed_head") == head:
        return f"routed: {fields['routed_job']}"
    if fields.get("label_refused_head") == head:
        return f"label refused: {fields['label_refused']}"
    if head != fields["reported_head"]:
        return "head moved since the report"
    return "never graded: run label --all-clean"


def stale_lines(rows: dict[str, dict[str, str]], moment: datetime, after: timedelta) -> list[str]:
    """Every open PR row reported clean at least `after` ago, oldest first, with what holds it."""
    aged = sorted(
        ((moment - parse_iso(fields["reported_at"]), pr, fields) for pr, fields in rows.items() if pr.isdigit() and is_clean_reported(fields)),
        reverse=True,
    )
    return [f"stale #{pr} {int(age.total_seconds() // 60)}m {fields['lane']}: {blocker(fields)}" for age, pr, fields in aged if age >= after]


def aged_lines(rows: dict[str, dict[str, str]], moment: datetime, after: timedelta) -> list[str]:
    """Every open PR row opened at least `after` ago, oldest first, with what holds it: each is landed or closed by 72 hours."""
    aged = sorted(((moment - parse_iso(fields["created_at"]), pr, fields) for pr, fields in rows.items() if is_open(fields) and fields.get("created_at")), reverse=True)
    return [
        f"aged #{pr} {int(age.total_seconds() // 3600)}h {fields.get('lane', '-')}: {'in the queue' if carries_label(fields) else waiting_reason(fields) or 'untracked'}"
        for age, pr, fields in aged
        if age >= after
    ]


def report_to_landed(landed: list[dict[str, str]]) -> str:
    minutes = [(parse_iso(fields["landed_at"]) - parse_iso(fields["reported_at"])).total_seconds() / 60 for fields in landed if fields.get("reported_at")]
    return f"{round(median(minutes))}m" if minutes else "-"


def buildkite_targets(status: dict, checks: dict) -> list[re.Match]:
    urls = [entry["target_url"] or "" for entry in status["statuses"] if entry["state"] == "failure"]
    urls += [run["details_url"] or "" for run in checks["check_runs"] if run["conclusion"] == "failure"]
    matches = [BUILDKITE_URL.match(url) for url in urls]
    return [match for match in matches if match]


def failed_job(shell: Shell, pipeline: str, build: str, job_id: str | None) -> dict:
    jobs = json.loads(shell.run(["bk", "build", "view", "-p", pipeline, build]))["jobs"]
    return next(job for job in jobs if job["id"] == job_id) if job_id else next(job for job in jobs if job["state"] == "failed" and not job["soft_failed"])


def first_error_line(log: str) -> str:
    """The log's first error line that is not a benign Buildkite step-export or meta-data 404 probe, else its first probe line."""
    probe = ""
    for raw in log.splitlines():
        line = ANSI.sub("", BK_TIMESTAMP.sub("", raw)).strip()
        if not line or BK_SECTION.match(line) or BK_SOURCE_ECHO.match(line):
            continue
        lowered = line.lower()
        if any(noise in lowered for noise in WRAPPER_NOISE):
            continue
        if ERROR_LINE.search(line) and not BK_PROBE.search(line):
            return line
        if ERROR_LINE.search(line):
            probe = probe or line
    return probe


def resolve_error(shell: Shell, target: re.Match) -> tuple[str, str]:
    """The failed job's name and its log's first error line."""
    pipeline, build = target["pipeline"], target["build"]
    job = failed_job(shell, pipeline, build, target["job"])
    return job["name"], first_error_line(shell.run(["bk", "job", "log", "-p", pipeline, "-b", build, job["id"]]))


def route_verdict(shell: Shell, gh: Github, fields: dict[str, str]) -> tuple[str, str]:
    head = fields["head"]
    if fields["mergeable_state"] == "dirty":
        return REBASE_TEXT.format(base=fields["base"], head=head[:9]), "rebase onto " + fields["base"]
    if fields["test_state"] != "failure":
        state = fields["mergeable_state"]
        return BLOCKED_TEXT.format(state=state, head=head[:9]), "unblock: " + state
    targets = buildkite_targets(gh.api(f"commits/{head}/status"), gh.api(f"commits/{head}/check-runs"))
    if not targets:
        return NO_TARGET_TEXT.format(head=head[:9]), "red outside buildkite"
    target = targets[0]
    url = target.group(0)
    _, error = resolve_error(shell, target)
    if not error:
        return NO_ERROR_TEXT.format(head=head[:9], url=url), "red with no error line"
    return FAILURE_TEXT.format(head=head[:9], url=url, error=error), "fix: " + error[:120]


def priority(item: tuple[str, dict[str, str]]) -> tuple[int, int]:
    key, fields = item
    return KINDS.index(fields["kind"]), int(key.lstrip("#"))


def next_key(prefix: str, rows: dict[str, dict[str, str]]) -> str:
    seq = max((int(suffix) for suffix in (key[len(prefix):] for key in rows) if suffix.isdigit()), default=0) + 1
    return f"{prefix}{seq:06d}"


def same_report(existing: dict[str, str], fields: dict[str, str]) -> bool:
    return (existing.get("kind"), existing.get("pr"), existing.get("head")) == ("report", fields["pr"], fields["head"])


def duplicate(messages: dict[str, dict[str, str]], fields: dict[str, str]) -> str | None:
    if fields["kind"] == "report":
        latest = next((key for key, existing in reversed(messages.items()) if same_report(existing, fields)), None)
        return latest if latest and messages[latest]["text"] == fields["text"] else None
    if fields["kind"] == "ruling":
        identity = ("kind", "pr", "text")
    elif "event" in fields:
        identity = ("kind", "pr", "head", "text")
    else:
        identity = ("kind", "pr", "head")
    wanted = tuple(fields[name] for name in identity)
    for key, existing in messages.items():
        if tuple(existing.get(name, "") for name in identity) == wanted:
            return key
    return None


def enqueue(messages: Messages, fields: dict[str, str]) -> str:
    seen = duplicate(messages.read(None), fields)
    if seen:
        print(f"duplicate of {seen}; nothing recorded, nothing to answer")
        return seen
    posted = messages.post(fields)
    print(f"{posted.num} {fields['kind']} {ref(fields['pr'])} {fields['head'][:9]} from {fields['lane']}")
    return cci.receipt(posted.seq)


def moot_reports(messages: dict[str, dict[str, str]], rows: dict[str, dict[str, str]]) -> frozenset[str]:
    latest: dict[str, str] = {}
    moot: set[str] = set()
    for key, fields in messages.items():
        if fields["kind"] != "report" or fields["state"] != "pending":
            continue
        if rows.get(fields["pr"], {}).get("state") in TERMINAL_STATES:
            moot.add(key)
            continue
        if fields["pr"] in latest:
            moot.add(latest[fields["pr"]])
        latest[fields["pr"]] = key
    return frozenset(moot)


def message_line(key: str, fields: dict[str, str]) -> str:
    return f"{key} {fields['kind']} {ref(fields['pr'])} {fields['head'][:9]} {fields['lane']}: {fields['text']}"


def ruling_text(text: str, options: str) -> str:
    return f"{text}; options: {' / '.join(part.strip() for part in options.split('|'))}"


def ruling_line(fields: dict[str, str]) -> str:
    return f"RULING NEEDED: {fields['text']}"


def hold_line(pr: str, fields: dict[str, str], moment: datetime) -> str:
    expired = " EXPIRED" if parse_iso(fields["hold_until"]) < moment else ""
    return f"held #{pr}: {fields['hold_reason']} until {fields['hold_until']}{expired}"


def route_message(pr: str, head: str, lane: str, job: str, verdict: str) -> str:
    body = f"DESK #{pr} {head[:9]}: {job}"
    if verdict:
        body += f"\n{verdict}"
    return f"to {lane}:\n{body}\n{ROUTE_TEXT}"


def landed_on_base(shell: Shell, gh: Github, checkout: Path, tip: str, pr: str, head: str) -> tuple[str, str] | None:
    """Does the base tree hold this PR's payload right now?

    Tree equality proves a landing. A difference proves nothing, so this returns None
    for "cannot tell from content" and the caller consults the forge. Two findings put
    it that way: the base can take the payload and then move on one of the files, and a
    squash onto a moved base merges branch with base, so the result equals neither side
    for a file both touched and the head's blob never appears in history at all.

    What content answers and the forge cannot: a stacked child carrying its parent's
    payload, after which the parent merges as a no-op under no number of its own. No
    commit on the base ever carries that number, so searching the log for ``(#<pr>)``
    finds nothing. A shallow checkout truncates that search further, at a depth that
    moves with each fetch.

    Two dots, never three: three would diff against the merge base and report the
    branch side regardless of what the base received. Both sides are named refs rather
    than ``FETCH_HEAD``, which the second fetch would otherwise move onto the head,
    making every row diff against itself and read as landed.
    """
    git = ["git", "-C", str(checkout)]
    files = [row["filename"] for row in gh.api(f"pulls/{pr}/files?per_page=100")]
    if not files:
        return None
    if shell.run(git + ["diff", "--numstat", tip, head, "--"] + files).strip():
        return None
    delivered = shell.run(git + ["log", tip, "-1", "--format=%H %cI", "--"] + files).split()
    if not delivered:
        return None
    sha, landed_at = delivered[0], delivered[1]
    return sha, stamp(parse_iso(landed_at).astimezone(timezone.utc))


def merge_conflicts(shell: Shell, checkout: Path, pr: str, base: str, head: str) -> Refused | None:
    git = ["git", "-C", str(checkout)]
    pull = f"refs/desk/pr{pr}"
    shell.run(git + ["fetch", "-q", "origin", f"+refs/pull/{pr}/head:{pull}"])
    fetched = shell.run(git + ["rev-parse", pull]).strip()
    if fetched != head:
        return refusal("fetched", pr=pr, fetched=fetched[:9], head=head[:9])
    tip = f"refs/desk/base/{base}"
    shell.run(git + ["fetch", "-q", "origin", f"+refs/heads/{base}:{tip}"])
    try:
        shell.run(git + ["merge-tree", "--write-tree", tip, head])
    except subprocess.CalledProcessError as failure:
        paths = sorted({line.split("\t")[-1] for line in failure.stdout.splitlines()[1:] if "\t" in line})
        return refusal("conflict", head=head[:9], base=base, paths=", ".join(paths) or "unlisted paths")
    return None


def linked_prs(fields: dict[str, str]) -> list[str]:
    return [pr for pr in fields.get("prs", "").split(",") if pr]


def ask_line(key: str, fields: dict[str, str]) -> str:
    standing = f" standing {fields['standing_hours']}h" if fields.get("standing_hours") else ""
    return f"{key} {fields['lane']}{standing}: {fields['text']}"


def progress_names(fields: dict[str, str]) -> list[str]:
    return sorted(name for name in fields if name.startswith(PROGRESS_PREFIX))


def latest_progress(fields: dict[str, str]) -> dict | None:
    names = progress_names(fields)
    return json.loads(fields[names[-1]]) if names else None


def progress_line(fields: dict[str, str], moment: datetime) -> str:
    if not (entry := latest_progress(fields)):
        return ""
    return f" | progress {age_minutes(entry['at'], moment)}m: {' '.join([entry['note'], *entry['artifacts']])}"


def age_minutes(since: str, moment: datetime) -> int:
    return int((moment - parse_iso(since)).total_seconds() // 60)


def live_since(rows: dict[str, dict[str, str]]) -> str:
    return rows.get(LIVE_KEY, {}).get("at", "")


def ask_delivered(fields: dict[str, str], prs: dict[str, dict[str, str]]) -> bool:
    linked = linked_prs(fields)
    return bool(linked) and all(prs.get(pr, {}).get("state") == LANDED for pr in linked)


def ask_landed_at(fields: dict[str, str], prs: dict[str, dict[str, str]]) -> str:
    return max((prs[pr]["landed_at"] for pr in linked_prs(fields) if prs.get(pr, {}).get("state") == LANDED), default="")


def ask_open_pr(fields: dict[str, str], prs: dict[str, dict[str, str]]) -> str:
    return next((pr for pr in linked_prs(fields) if is_open(prs.get(pr, {}))), "")


def ask_state(fields: dict[str, str], prs: dict[str, dict[str, str]], moment: datetime, live: str) -> str:
    """LIVE, LANDED-NOT-LIVE, IN-PR, IN-PROGRESS, LOST, STALE, BACKLOG, dropped, or answered; "" while too fresh to grade."""
    if fields.get("dropped_at"):
        return ASK_DROPPED
    if fields.get("answered_at"):
        return ASK_ANSWERED
    if override := ASK_OVERRIDES.get(fields.get("state", "")):
        return override
    progress = latest_progress(fields)
    if hours := fields.get("standing_hours"):
        return ASK_IN_PROGRESS if age_minutes(progress["at"] if progress else fields["asked_at"], moment) < int(hours) * 60 else ASK_STALE
    if ask_delivered(fields, prs):
        landed = ask_landed_at(fields, prs)
        return ASK_LIVE if live and live >= landed else ASK_LANDED_NOT_LIVE
    if ask_open_pr(fields, prs):
        return ASK_IN_PR
    if age_minutes(progress["at"] if progress else fields["asked_at"], moment) >= LOST_MINUTES:
        return ASK_LOST
    return ASK_IN_PROGRESS if progress else ""


def frozen_by(asks: dict[str, dict[str, str]], prs: dict[str, dict[str, str]], pr: str, ask: str, moment: datetime, live: str) -> str | None:
    """The other ask already IN-PR on this PR, if one holds it; a fresh ask goes on a follow-up PR instead."""
    return next(
        (key for key, fields in asks.items() if key != ask and pr in linked_prs(fields) and ask_state(fields, prs, moment, live) == ASK_IN_PR),
        None,
    )


def render_table(rows: dict[str, dict[str, str]]) -> str:
    records = []
    for key in sorted(rows, key=int, reverse=True):
        fields = dict(rows[key], key=f"#{key}")
        fields["head"] = current_head(fields)[:9]
        fields["label_head"] = fields.get("label_head", "")[:9]
        records.append([str(fields.get(source, "-") or "-")[:40] for _, source in SHOW_COLUMNS])
    headers = [label for label, _ in SHOW_COLUMNS]
    widths = [max(len(headers[i]), *(len(r[i]) for r in records)) if records else len(headers[i]) for i in range(len(headers))]
    out = ["  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True))]
    out += ["  ".join(cell.ljust(w) for cell, w in zip(record, widths, strict=True)) for record in records]
    return "\n".join(out)


def prompt_lines(shell: Shell, drive: str, shard: frozenset[str] | None) -> list[str]:
    """The prompt watch's last poll for the cci drive: its own line when it stopped polling, then one per terminal that is not clear."""
    watch = json.loads(shell.run([sys.executable, str(PROMPT_WATCH), "show", "--cci-drive", drive, "--json"]) or "{}")
    return [row["line"] for row in watch.get("rows") or [] if shard is None or row["role"] == "watch" or row["name"] in shard]


def summary_lines(
    rows: dict[str, dict[str, str]],
    pulls: dict[str, dict[str, str]],
    messages: dict[str, dict[str, str]],
    moment: datetime,
    window: timedelta,
    stale_after: timedelta,
    prompts: list[str],
) -> list[str]:
    """The desk lines off one repository's `rows`; owner asks grade against `pulls`, every repository's PR rows under their stored keys."""
    cutoff = moment - window
    prs = {key: fields for key, fields in rows.items() if key.isdigit()}
    asks = {key: fields for key, fields in rows.items() if key.startswith(ASK_PREFIX)}
    live = live_since(rows)
    states = {key: ask_state(fields, pulls, moment, live) for key, fields in asks.items()}
    lost = sorted(key for key, state in states.items() if state == ASK_LOST)
    standing_stale = sorted(key for key, state in states.items() if state == ASK_STALE)
    landed_not_live = sorted(key for key, state in states.items() if state == ASK_LANDED_NOT_LIVE)
    backlog = sorted(key for key, state in states.items() if state == ASK_BACKLOG)
    in_progress = sorted(key for key, state in states.items() if state == ASK_IN_PROGRESS)
    stuck = sorted(key for key, state in states.items() if state == ASK_IN_PR and age_minutes(asks[key]["asked_at"], moment) >= ESCALATE_MINUTES)
    landed = sorted((pr for pr, fields in prs.items() if fields.get("landed_at") and parse_iso(fields["landed_at"]) >= cutoff), key=int)
    stale = stale_lines(prs, moment, stale_after)
    open_rows = {pr: fields for pr, fields in prs.items() if is_open(fields)}
    labelled = sorted((pr for pr, fields in open_rows.items() if carries_label(fields)), key=int)
    holds = {pr: fields for pr, fields in open_rows.items() if is_held(fields)}
    routed = sorted((pr for pr, fields in open_rows.items() if fields.get("routed_head") and fields["routed_head"] == current_head(fields)), key=int)
    pending = [fields for fields in messages.values() if fields["state"] == "pending"]
    rulings = [fields for fields in pending if fields["kind"] == "ruling"]
    p0s = [fields for fields in pending if fields["kind"] == "p0"]
    lines = [
        f"desk {stamp(moment)} | open {len(open_rows)} | merged/h {len(landed)} | labelled {len(labelled)} | held {len(holds)} | rulings {len(rulings)} | p0 {len(p0s)} | routed {len(routed)} | stale {len(stale)} | p50 report→landed {report_to_landed([prs[pr] for pr in landed])} | lost {len(lost)} | landed-not-live {len(landed_not_live)}",
        *stale,
        *waiting_line(prs),
    ]
    if landed:
        lines.append("merged: " + " ".join(f"#{pr}" for pr in landed))
    if labelled:
        lines.append("labelled: " + " ".join(f"#{pr}" for pr in labelled))
    lines += [f"P0 {ref(fields['pr'])} {fields['lane']}: {fields['text']}" for fields in p0s]
    lines += [ruling_line(fields) for fields in rulings]
    lines += [hold_line(pr, holds[pr], moment) for pr in sorted(holds, key=int)]
    if routed:
        lines.append("routed, awaiting a new head: " + " ".join(f"#{pr}" for pr in routed))
    if len(lines) > SUMMARY_LINES:
        lines = lines[: SUMMARY_LINES - 1] + [f"... {len(lines) - SUMMARY_LINES + 1} more lines in ledger show"]
    asked = [f"LOST {ask_line(key, asks[key])}{progress_line(asks[key], moment)}" for key in lost]
    asked += [f"STALE {ask_line(key, asks[key])}{progress_line(asks[key], moment)}" for key in standing_stale]
    asked += [f"LANDED-NOT-LIVE {ask_line(key, asks[key])}{progress_line(asks[key], moment)}" for key in landed_not_live]
    asked += [" ".join(["BACKLOG", key, asks[key].get("state_note", "")]).rstrip() + progress_line(asks[key], moment) for key in backlog]
    asked += [f"IN-PROGRESS {ask_line(key, asks[key])}{progress_line(asks[key], moment)}" for key in in_progress]
    asked += [
        f"IN-PR {age_minutes(asks[key]['asked_at'], moment)}m {ask_line(key, asks[key])}: {ref(pr := ask_open_pr(asks[key], pulls))} {blocker(pulls[pr])}{progress_line(asks[key], moment)}"
        for key in stuck
    ]
    return [lines[0], *prompts, *asked, *lines[1:]]


def cmd_init(args: argparse.Namespace, shell: Shell) -> int:
    payload = json.loads(shell.run(["ccn", "ledger", "add", args.title, "--json"]))
    print(payload["id"])
    return 0


def cmd_report(args: argparse.Namespace, shell: Shell) -> int:
    notes = Notes(shell, args.ledger, args.scope)
    key = notes.key(args.pr)
    if args.ask:
        stored = notes.stored()
        asks = notes.view(stored)
        if args.ask not in asks:
            raise SystemExit(f"no ask {args.ask} in {args.ledger}; record it with ledger.py ask first")
        held_by = frozen_by(asks, pull_rows(stored), key, args.ask, now(), live_since(stored))
        if held_by:
            raise SystemExit(f"{ref(key)} already carries {held_by}, still IN-PR; open a stacked follow-up PR for {args.ask}")
        prs = linked_prs(asks[args.ask])
        notes.set_fields(args.ask, {"prs": ",".join(prs if key in prs else [*prs, key])})
    enqueue(Messages(shell, args.drive), {"kind": "report", "pr": key, "head": args.head, "lane": args.lane, "text": f"{args.verdict} {args.text}".strip()})
    notes.set_fields(args.pr, {"lane": args.lane, "reported_head": args.head, "reported_verdict": args.verdict, "reported_at": utc_stamp()})
    return 0


def cmd_ask_state(args: argparse.Namespace, shell: Shell) -> int:
    notes = Notes(shell, args.ledger)
    if args.ask not in notes.asks():
        raise SystemExit(f"no ask {args.ask} in {args.ledger}")
    notes.set_fields(args.ask, {"state": "" if args.state == ASK_OPEN else args.state, "state_note": args.note})
    print(f"{args.ask} {args.state}" + (f": {args.note}" if args.note else ""))
    return 0


def cmd_ask_standing(args: argparse.Namespace, shell: Shell) -> int:
    notes = Notes(shell, args.ledger)
    asks = notes.asks()
    if args.ask not in asks:
        raise SystemExit(f"no ask {args.ask} in {args.ledger}")
    standing = {"standing_hours": str(args.standing)}
    notes.set_fields(args.ask, standing)
    print(ask_line(args.ask, asks[args.ask] | standing))
    return 0


def cmd_ask(args: argparse.Namespace, shell: Shell) -> int:
    if args.state:
        if not args.ask:
            raise SystemExit("ask --state needs --ask ID")
        return cmd_ask_state(args, shell)
    if args.ask and args.standing:
        return cmd_ask_standing(args, shell)
    if not (args.text and args.lane and args.accept):
        raise SystemExit("ask needs --text, --lane and --accept to record an ask, --state with --ask to mark one, or --standing with --ask to make one standing")
    notes = Notes(shell, args.ledger)
    key = next_key(ASK_PREFIX, notes.asks())
    fields = {"text": args.text, "lane": args.lane, "accept": args.accept, "asked_at": utc_stamp()} | ({"standing_hours": str(args.standing)} if args.standing else {})
    notes.set_fields(key, fields)
    print(ask_line(key, fields))
    return 0


def cmd_progress(args: argparse.Namespace, shell: Shell) -> int:
    notes = Notes(shell, args.ledger)
    asks = notes.asks()
    if args.ask not in asks:
        raise SystemExit(f"no ask {args.ask} in {args.ledger}")
    names = progress_names(asks[args.ask])
    seq = int(names[-1][len(PROGRESS_PREFIX) :]) + 1 if names else 1
    entry = {"at": utc_stamp(), "note": args.note, "artifacts": args.artifact}
    notes.set_fields(args.ask, {f"{PROGRESS_PREFIX}{seq:06d}": json.dumps(entry)})
    print(f"{args.ask} progress: {' '.join([args.note, *args.artifact])}")
    return 0


def cmd_drop(args: argparse.Namespace, shell: Shell) -> int:
    notes = Notes(shell, args.ledger)
    if args.ask not in notes.asks():
        raise SystemExit(f"no ask {args.ask} in {args.ledger}")
    notes.set_fields(args.ask, {"dropped_at": utc_stamp(), "dropped_reason": args.reason})
    print(f"dropped {args.ask}: {args.reason}")
    return 0


def cmd_answer(args: argparse.Namespace, shell: Shell) -> int:
    notes = Notes(shell, args.ledger)
    if args.ask not in notes.asks():
        raise SystemExit(f"no ask {args.ask} in {args.ledger}")
    notes.set_fields(args.ask, {"answered_at": utc_stamp(), "answer": args.text})
    print(f"answered {args.ask}: {args.text}")
    return 0


def cmd_live(args: argparse.Namespace, shell: Shell) -> int:
    Notes(shell, args.ledger).set_fields(LIVE_KEY, {"at": args.at, "text": args.text})
    print(f"live at {args.at}" + (f": {args.text}" if args.text else ""))
    return 0


def cmd_enqueue(args: argparse.Namespace, shell: Shell) -> int:
    enqueue(Messages(shell, args.drive), {"kind": args.kind, "pr": args.scope.key(args.pr), "head": args.head, "lane": args.lane, "text": args.text})
    return 0


def cmd_ruling(args: argparse.Namespace, shell: Shell) -> int:
    fields = {"kind": "ruling", "pr": args.scope.key(args.pr) if args.pr.isdigit() else args.pr, "head": NO_PR, "lane": args.lane, "text": ruling_text(args.text, args.options)}
    enqueue(Messages(shell, args.drive), fields)
    print(ruling_line(fields))
    return 0


def cmd_inbox(args: argparse.Namespace, shell: Shell) -> int:
    rows = Notes(shell, args.ledger).rows()
    messages = Messages(shell, args.drive)
    inbox = messages.read(args.shard)
    moot = frozenset() if args.all else moot_reports(inbox, rows)
    shown = sorted(((key, fields) for key, fields in inbox.items() if key not in moot and (args.all or fields["state"] == "pending")), key=priority)
    if args.json:
        print(json.dumps([dict(fields, key=key) for key, fields in shown]))
    else:
        for key, fields in shown:
            print(ruling_line(fields) if fields["kind"] == "ruling" else message_line(key, fields))
    if args.take:
        for key in sorted(moot, key=lambda key: int(key.lstrip("#"))):
            messages.answer(key, inbox[key], "moot")
        for key, fields in shown:
            if fields["state"] == "pending":
                messages.answer(key, fields, "taken")
    return 0


def cmd_ack(args: argparse.Namespace, shell: Shell) -> int:
    messages = Messages(shell, args.drive)
    inbox = messages.read(None)
    for key in args.keys:
        if key not in inbox:
            raise SystemExit(f"no lane message {key} on drive {args.drive}")
        messages.answer(key, inbox[key], "acked")
    return 0


def branch_prefix(value: str) -> str:
    if value.strip("/-") == "":
        raise argparse.ArgumentTypeError(f"{value!r} names no branch; pass a branch name, or a namespace ending in '/' or '-', as in 'lightning/'")
    return value


def on_prefix(branch: str, prefix: str) -> bool:
    """A prefix ending in '/' or '-' is a namespace; any other names exactly one branch, so `lightning` never claims `lightningbolt`."""
    return branch.startswith(prefix) if prefix.endswith(("/", "-")) else branch == prefix


def lane_slug(lane: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", lane.lower()).strip("-")


def prefix_prs(state: dict, prefix: str) -> list[str]:
    return [pr for pr in map(str, state["lanes"][prefix]) if on_prefix(state["prs"][pr]["headRefName"], prefix)]


def stack_listing(shell: Shell, ccx: str, worktree: Path) -> dict:
    return json.loads(shell.run([ccx, "vcs", "stack", "list", "--json"], cwd=worktree))


def lane_worktree(shell: Shell, args: argparse.Namespace) -> str:
    """The working copy register runs in when its stack holds the lane's branch in the lane's repository, else empty."""
    try:
        listing = stack_listing(shell, args.ccx, Path.cwd())
    except subprocess.CalledProcessError:
        return ""
    held = any(on_prefix(branch["branch"], args.branch_prefix) for branch in listing["branches"])
    return listing["root"] if held and same_repo(origin_repo(shell, Path(listing["root"])), args.repo) else ""


def lane_stacks(shell: Shell, ccx: str, registrations: list[dict[str, str]]) -> dict[str, list[str]]:
    """Resolve the registered worktrees' distinct stacks together, retaining each lane's membership and refusal."""
    worktrees = {lane["lane"]: str(Path(worktree)) for lane in registrations if (worktree := lane.get("worktree")) and Path(worktree).is_dir()}
    paths = list(dict.fromkeys(worktrees.values()))
    results = {}
    if paths:
        try:
            for result in json.loads(shell.run([ccx, "vcs", "stack", "list-batch", *paths])):
                if error := result.get("error"):
                    result["error"] = f"ccx: {error}"
                results[result["path"]] = result
        except subprocess.CalledProcessError as failure:
            results = {path: {"error": (failure.stderr or "").strip() or str(failure)} for path in paths}
    stacks = {}
    for lane in registrations:
        stacks[lane["lane"]] = []
        if not (path := worktrees.get(lane["lane"])):
            continue
        result = results[path]
        if error := result.get("error"):
            print(f"ledger.py: lane {lane['lane']}'s worktree {lane['worktree']} lists no stack, so only its prefix is tracked: {error}", file=sys.stderr)
            continue
        stacks[lane["lane"]] = [branch["branch"] for branch in result["listing"]["branches"]]
    return stacks


def foreign_prs(shell: Shell, args: argparse.Namespace, notes: Notes) -> list[str]:
    """Open PRs on the prefix that another lane already owns or that predate the drive's ledger; none when the forge does not answer, so the lane registers and the next pass reads its PRs."""
    try:
        state = pr_state(shell, args.ccx, args.repo, [], [args.branch_prefix])
    except subprocess.CalledProcessError as failure:
        print(f"ledger.py register: the open PRs on {args.branch_prefix} did not read, so no other lane's PR was checked: {(failure.stderr or '').strip() or failure}", file=sys.stderr)
        return []
    started = parse_iso(notes.show()["created_at"])
    rows = notes.pr_rows()
    reasons = {}
    for pr in prefix_prs(state, args.branch_prefix):
        if (owner := rows.get(pr, {}).get("lane", args.lane)) != args.lane:
            reasons[pr] = f"lane {owner}"
        elif parse_iso(state["prs"][pr]["createdAt"]) < started:
            reasons[pr] = f"opened {state['prs'][pr]['createdAt']}, before the drive"
    return [f"#{pr} ({reasons[pr]})" for pr in sorted(reasons, key=int)]


def guard_prefix(shell: Shell, args: argparse.Namespace, notes: Notes) -> None:
    login = json.loads(shell.run(ghread.read("api", "user")))["login"]
    suggestion = f"register the lane's exact branch name, a lane-specific namespace such as {login}/{lane_slug(args.lane)}-, or each of its PRs with --pr N"
    if args.branch_prefix.endswith(("/", "-")) and f"{login}/".startswith(args.branch_prefix):
        raise SystemExit(f"{args.branch_prefix} covers {login}'s whole branch namespace, not lane {args.lane}'s; {suggestion}")
    if foreign := foreign_prs(shell, args, notes):
        raise SystemExit(f"{args.branch_prefix} already matches PRs that are not lane {args.lane}'s: {' '.join(foreign)}; {suggestion}")


def discovered_only(fields: dict[str, str], lane: str, prefix: str) -> bool:
    """A row the lane's prefix alone pulled in, which no report, explicit registration, label, or hold has touched since."""
    return (
        fields.get("registered") == lane
        and fields.get("lane") == lane
        and on_prefix(fields.get("branch", ""), prefix)
        and not any(fields.get(name) for name in TOUCHED_FIELDS)
    )


def step_field(step: str | None) -> dict[str, str]:
    return {"step": step} if step else {}


def cmd_register(args: argparse.Namespace, shell: Shell) -> int:
    """A PR row keeps the lane that first claimed it; a re-push only moves its registered head."""
    if not args.branch_prefix and not args.pr:
        raise SystemExit("register needs --branch-prefix, --pr, or both")
    if args.head and len(args.pr) != 1:
        raise SystemExit("--head names one PR's head; pass exactly one --pr with it")
    notes = Notes(shell, args.ledger, args.scope)
    if args.branch_prefix:
        guard_prefix(shell, args, notes)
        notes.set_fields(
            f"{LANE_PREFIX}{args.lane}",
            {"lane": args.lane, "branch_prefix": args.branch_prefix, "repo": args.repo, "worktree": lane_worktree(shell, args), "registered_at": utc_stamp()} | step_field(args.step),
        )
    with locked(default_lock(args.ledger)):
        rows = notes.pr_rows() if args.pr else {}
        owners = {pr: rows.get(pr, {}).get("lane") or args.lane for pr in args.pr}
        for pr, owner in owners.items():
            fields = {"lane": owner, "registered": owner, **({"unadmitted": ""} if rows.get(pr, {}).get("unadmitted") else {})} | ({"registered_head": args.head} if args.head else {}) | step_field(args.step)
            if any(rows.get(pr, {}).get(name) != value for name, value in fields.items()):
                notes.set_fields(pr, fields)
    namespace = f" on {args.branch_prefix}*" if args.branch_prefix else ""
    claimed = "".join(f" #{pr}" if owner == args.lane else f" #{pr} (lane {owner})" for pr, owner in owners.items())
    print(f"registered {args.lane}{namespace}{claimed}")
    return 0


def cmd_reassign(args: argparse.Namespace, shell: Shell) -> int:
    notes = Notes(shell, args.ledger, args.scope)
    with locked(default_lock(args.ledger)):
        rows = notes.pr_rows()
        if missing := [pr for pr in args.pr if pr not in rows]:
            raise SystemExit(f"{args.ledger} has no row for {' '.join(f'#{pr}' for pr in missing)}; register it with --pr first")
        for pr in args.pr:
            fields = rows[pr]
            if fields["lane"] == args.lane:
                print(f"#{pr} already belongs to {args.lane}")
                continue
            move = f"{fields['lane']}>{args.lane} {utc_stamp()}"
            history = "; ".join(filter(None, [fields.get("reassigned"), move]))
            notes.set_fields(pr, {"lane": args.lane, "registered": args.lane, "reassigned": history})
            print(f"reassigned #{pr} from {fields['lane']} to {args.lane}")
    return 0


def unregister_prs(args: argparse.Namespace, shell: Shell) -> int:
    """Remove each named PR row whole, so the desk neither mirrors a grade onto it nor gates it; a lane prefix covering it or a drive lane's `opened` or `head` record rows it again."""
    if args.branch_prefix:
        raise SystemExit("--branch-prefix names a lane's prefix; pass it with --lane, not --pr")
    notes = Notes(shell, args.ledger, args.scope)
    with locked(default_lock(args.ledger)):
        rows = notes.pr_rows()
        if missing := [pr for pr in args.pr if pr not in rows]:
            raise SystemExit(f"{args.ledger} has no row for {' '.join(f'#{pr}' for pr in missing)}")
        for pr in args.pr:
            notes.remove(pr)
    print("unregistered " + " ".join(f"#{pr}" for pr in args.pr))
    return 0


def cmd_unregister(args: argparse.Namespace, shell: Shell) -> int:
    if args.pr:
        return unregister_prs(args, shell)
    notes = Notes(shell, args.ledger)
    key = f"{LANE_PREFIX}{args.lane}"
    with locked(default_lock(args.ledger)):
        rows = notes.rows()
        if key not in rows:
            raise SystemExit(f"lane {args.lane} has no branch prefix registered in {args.ledger}")
        prefix = rows[key]["branch_prefix"]
        if args.branch_prefix and args.branch_prefix != prefix:
            raise SystemExit(f"lane {args.lane} is registered on {prefix}, not {args.branch_prefix}")
        default = origin_repo(shell, Path("."))
        scope = Scope(rows[key].get("repo") or default, default)
        dropped = sorted((pr for pr, fields in pull_rows(rows).items() if scope.local(pr) and discovered_only(fields, args.lane, prefix)), key=pr_order)
        for pr in dropped:
            notes.remove(pr)
        notes.remove(key)
    print(f"unregistered {args.lane} from {prefix}*; dropped " + (" ".join(map(ref, dropped)) or "no rows"))
    return 0


def merged_pr_state(reads: list[Future]) -> dict:
    """The answers of `pr_state` calls over `REFRESH_BATCH` PRs or prefixes each, merged into one.

    One call naming a whole drive's ledger outlives its timeout once the cache falls back to a REST read per record.
    """
    try:
        answers = [read.result() for read in reads]
    except subprocess.CalledProcessError as failure:
        raise ForgeUnreachable(f"ccx vcs pr state: {(failure.stderr or '').strip() or failure}") from failure
    return {"lanes": {prefix: prs for answer in answers for prefix, prs in answer["lanes"].items()}, "prs": {pr: record for answer in answers for pr, record in answer["prs"].items()}}


def refresh_repo(shell: Shell, args: argparse.Namespace, scope: Scope, stored: dict[str, dict[str, str]], named: list[str], lanes: dict[str, str], moment: str) -> list[dict]:
    """One `ccx vcs pr state` read for one repository's rows, its lanes' prefixes, and the PRs `named` in it; returns the graded rows under their stored keys."""
    every = {key: fields for key, fields in scope.view(stored).items() if key.isdigit()}
    reopenable = {key for key, fields in every.items() if fields.get("state") == CLOSED_WITHOUT_SQUASH and (key in named or ((last := fields.get("last_refresh")) and parse_iso(moment) - parse_iso(last) < REOPEN_WINDOW))}
    settled = {key for key, fields in every.items() if fields.get("state") in TERMINAL_STATES} - reopenable
    known = sharded(every, args.shard)
    registrations = list(sharded(scope.lanes(stored), args.shard).values())
    wanted = sorted((set(known) | set(named)) - settled, key=int)
    with ThreadPoolExecutor(REFRESH_WORKERS) as reads:
        numbered = [reads.submit(pr_state, shell, args.ccx, scope.repo, list(chunk), []) for chunk in batched(wanted, REFRESH_BATCH)]
        stacks = lane_stacks(shell, args.ccx, registrations)
        prefixes = sorted({lane["branch_prefix"] for lane in registrations} | {branch for branches in stacks.values() for branch in branches})
        state = merged_pr_state([*numbered, *(reads.submit(pr_state, shell, args.ccx, scope.repo, [], list(chunk)) for chunk in batched(prefixes, REFRESH_BATCH))])
    registered = {pr: lane["lane"] for lane in registrations for pr in prefix_prs(state, lane["branch_prefix"])}
    steps = {lane["lane"]: lane.get("step", "") for lane in registrations}
    for lane, branches in stacks.items():
        for pr in (pr for branch in branches for pr in prefix_prs(state, branch)):
            if pr not in registered and every.get(pr, {}).get("lane", lane) == lane:
                registered[pr] = lane
    rows = []
    for key in sorted((set(wanted) | set(registered)) - settled, key=int):
        if key in reopenable and state["prs"][key]["state"] != "OPEN":
            continue
        was = every.get(key, {})
        fields = grade(state["prs"][key], scope.repo)
        fields["failing_checks"] = with_errors(shell, fields["failing_checks"], was.get("failing_checks"))
        for since, build in (("green_at", "success"), ("red_at", "failure")):
            if fields["test_state"] != build:
                fields[since] = ""
            elif (was.get("head"), was.get("test_state")) == (fields["head"], build):
                fields[since] = was.get(since, moment)
            else:
                fields[since] = moment
        fields["last_refresh"] = moment
        if key not in known:
            fields["first_seen"] = moment
        if key in registered and not was.get("reassigned") and not was.get("unadmitted"):
            fields |= {"lane": registered[key], "registered": registered[key]} | ({} if was.get("step") else step_field(steps.get(registered[key])))
        if key in lanes:
            fields["lane"] = lanes[key]
        rows.append({"key": scope.store(key), "fields": fields})
    return rows


def kept_steps(rows: list[dict], stored: dict[str, dict[str, str]]) -> list[dict]:
    return [
        row | {"fields": {name: value for name, value in row["fields"].items() if name != "step"}} if stored.get(row["key"], {}).get("step") else row
        for row in rows
    ]


def cmd_refresh(args: argparse.Namespace, shell: Shell) -> int:
    """Regrade every repository the ledger holds, one `ccx vcs pr state` read each; `--pr` and `--lane` name PRs in `--repo`.

    The ledger lock covers only the merge-write, so a `register` never waits out the forge read.
    """
    notes = Notes(shell, args.ledger)
    lanes = dict(pair.split("=", 1) for pair in args.lane)
    stored = notes.stored()
    moment = utc_stamp()
    rows = []
    for repo in ledger_repos(stored, args.scope):
        scope = Scope(repo, args.scope.default)
        named = same_repo(repo, args.repo)
        rows += refresh_repo(shell, args, scope, stored, args.pr if named else [], lanes if named else {}, moment)
    with locked(args.lock or default_lock(args.ledger)):
        notes.sync(kept_steps(rows, notes.stored()) if any("step" in row["fields"] for row in rows) else rows)
    print(f"refreshed {len(rows)} PRs into {args.ledger}")
    return 0


def cmd_hold(args: argparse.Namespace, shell: Shell) -> int:
    notes = Notes(shell, args.ledger, args.scope)
    rows = notes.pr_rows()
    above = stacked_above(rows, args.pr)
    green = [pr for pr in above if rows[pr].get("test_state") == "success"]
    if green and not args.stack:
        raise SystemExit(
            f"#{args.pr} has green PRs stacked on it ({', '.join(f'#{pr}' for pr in green)}), and a hold under green work blocks them for its whole life; "
            "reparent them onto the trunk with `ccx vcs stack rebase --parent <child>=<trunk>`, or hold the whole stack with --stack"
        )
    since = now()
    until = parse_iso(args.until) if args.until else since + timedelta(hours=args.hours)
    for pr in [args.pr, *above] if args.stack else [args.pr]:
        notes.set_fields(pr, {"hold_reason": args.reason, "hold_since": stamp(since), "hold_until": stamp(until)})
        print(f"held #{pr} until {stamp(until)}: {args.reason}")
    return 0


def cmd_lift(args: argparse.Namespace, shell: Shell) -> int:
    Notes(shell, args.ledger, args.scope).set_fields(args.pr, {name: "" for name in HOLD_FIELDS})
    print(f"lifted #{args.pr} at {utc_stamp()}")
    return 0


def cmd_priority(args: argparse.Namespace, shell: Shell) -> int:
    Notes(shell, args.ledger, args.scope).set_fields(args.pr, {"priority": str(args.priority)})
    print(f"#{args.pr} priority {args.priority}: the merge queue gates and enqueues lower values first")
    return 0


def queue_priority(fields: dict[str, str], asked: set[str], pr: str) -> int:
    """A row's merge-queue priority: its own `priority` field, else top priority while an open owner ask links it, else the default."""
    if fields.get("priority"):
        return int(fields["priority"])
    return OWNER_PRIORITY if pr in asked else DEFAULT_PRIORITY


def route_one(notes: Notes, gh: Github, pr: str, fields: dict[str, str], job: str | None, lane: str | None, dry_run: bool) -> bool:
    head = current_head(fields)
    lane = lane or fields["lane"]
    verdict = ""
    if not job:
        verdict, job = route_verdict(notes.shell, gh, fields)
    if fields.get("routed_head") == head and fields.get("routed_job") == job and fields.get("routed_lane") == lane:
        print(f"#{pr} {head[:9]} already routed to {fields['routed_lane']} at {fields['routed_at']}; nothing to send")
        return False
    print(route_message(pr, head, lane, job, verdict))
    if dry_run:
        return False
    notes.set_fields(pr, {"routed_head": head, "routed_job": job, "routed_lane": lane, "routed_at": utc_stamp(), "next_action": job})
    return True


def gone_lanes(rows: dict[str, dict[str, str]]) -> frozenset[str]:
    return frozenset(fields["lane"] for key, fields in rows.items() if key.startswith(GONE_PREFIX))


def sweep_lane(gh: Github, pr: str, fields: dict[str, str], gone: frozenset[str], train: str | None, paths: list[str], fallback: str | None) -> str | None:
    """The train for a hot-set conflict or a hot-set row whose lane is gone, else the row's own lane while live, else the fallback."""
    owner_gone = fields["lane"] in gone
    if train and (owner_gone or fields.get("mergeable_state") == "dirty") and touches(pr_files(gh, pr), paths):
        return train
    return fallback if owner_gone else fields["lane"]


def cmd_route(args: argparse.Namespace, shell: Shell) -> int:
    notes = Notes(shell, args.ledger, args.scope)
    gh = Github(shell, args.repo)
    if bool(args.train) != bool(args.paths):
        raise SystemExit("--train and --paths go together: the train takes the rows whose files match its paths")
    rows = notes.pr_rows()
    gone = gone_lanes(notes.rows())
    if args.pr:
        if not args.lane and rows[args.pr]["lane"] in gone:
            raise SystemExit(f"#{args.pr}'s lane {rows[args.pr]['lane']} is gone; name a live one with --lane")
        try:
            route_one(notes, gh, args.pr, rows[args.pr], args.job, args.lane, args.dry_run)
        except subprocess.CalledProcessError as failure:
            if not is_gh_api(failure):
                raise
            print(f"route #{args.pr}: not routed this pass, {forge_failure(failure)}")
        return 0
    moment = now()
    routed = 0
    for pr, fields in sorted(sharded(rows, args.shard).items(), key=lambda item: int(item[0])):
        if not needs_route(fields):
            continue
        if dev_red_held(fields, moment):
            print(f"#{pr} {fields['hold_reason']} until {fields['hold_until']}: red on the trunk too, not routed")
            continue
        try:
            lane = sweep_lane(gh, pr, fields, gone, args.train, args.paths, args.fallback)
            if lane is None:
                print(f"#{pr} {fields['lane']} is gone and no --fallback covers it; not routed")
                continue
            routed += route_one(notes, gh, pr, fields, None, lane, args.dry_run)
        except subprocess.CalledProcessError as failure:
            if not is_gh_api(failure):
                raise
            print(f"route #{pr}: not routed this pass, {forge_failure(failure)}")
    print(f"routed {routed} rows" if not args.dry_run else "dry run, nothing written")
    return 0


def cmd_gone(args: argparse.Namespace, shell: Shell) -> int:
    Notes(shell, args.ledger).set_fields(f"{GONE_PREFIX}{args.lane}", {"lane": args.lane, "gone_at": utc_stamp()})
    print(f"{args.lane} is gone; route --fallback sends its red and conflicting rows to a live lane")
    return 0


def train_tier(gh: Github, pr: str, fields: dict[str, str]) -> str:
    """`green`, `dirty` for a conflicting head someone approved, or "" for a row that is no car."""
    if fields.get("mergeable_state") == "dirty":
        return "dirty" if approvers(gh, pr) else ""
    if fields.get("test_state") == "success" and fields.get("mergeable_state") in LABELLABLE_STATES:
        return "green"
    return ""


def is_train_candidate(fields: dict[str, str]) -> bool:
    """Tracked, open, unheld, and not in the queue: a push to a queued PR is silently left out of what lands."""
    return is_tracked(fields) and is_open(fields) and not is_held(fields) and not lane_held(fields) and not carries_label(fields)


def cmd_train(args: argparse.Namespace, shell: Shell) -> int:
    gh = Github(shell, args.repo)
    rows = {pr: fields for pr, fields in sharded(Notes(shell, args.ledger, args.scope).pr_rows(), args.shard).items() if is_train_candidate(fields)}
    files = {pr: pr_files(gh, pr) for pr in sorted(rows, key=int)}
    hot = [pr for pr in files if touches(files[pr], args.paths)]
    tiers = {pr: train_tier(gh, pr, rows[pr]) for pr in hot}
    ready = [pr for pr in hot if tiers[pr]]
    overlaps = {pr: sum(1 for other in ready if other != pr and set(files[pr]) & set(files[other])) for pr in ready}
    ordered = sorted(ready, key=lambda pr: (TRAIN_TIERS.index(tiers[pr]), overlaps[pr], int(pr)))
    cars, later = ordered[: args.cars], ordered[args.cars :]
    if not cars:
        print(f"no ready row touches {' '.join(args.paths)}")
    for index, pr in enumerate(cars, 1):
        fields = rows[pr]
        print(f"car {index} #{pr} {current_head(fields)[:9]} {tiers[pr]} overlaps {overlaps[pr]} {fields['lane']} {fields['branch']}")
    if later:
        print("next train: " + " ".join(f"#{pr}" for pr in later))
    unready = [pr for pr in hot if not tiers[pr]]
    if unready:
        print("not ready: " + " | ".join(f"#{pr} {rows[pr].get('test_state', '-')}/{rows[pr].get('mergeable_state', '-')}" for pr in unready))
    if cars:
        print("ccx vcs stack rebase --linearize " + ",".join(rows[pr]["branch"] for pr in cars))
    return 0


def stack_to_trunk(gh: Github, tip: dict, trunk: str) -> list[dict]:
    """The open pull requests from the trunk up to the tip, bottom first.

    The queue enqueues a labelled pull request together with everything below it, so the
    label on the tip is a label on each of these. A base that is neither the trunk nor an
    open pull request's branch is a parent that closed without landing, and the walk
    refuses there rather than enqueue a child onto it.
    """
    owner = gh.repo.split("/")[0]
    stack = [tip]
    while (base := stack[0]["base"]["ref"]) != trunk:
        parents = [pull for pull in gh.api("pulls", head=f"{owner}:{base}", state="open") if pull["head"]["repo"]["full_name"] == gh.repo]
        if len(parents) != 1:
            raise refusal("orphaned", pr=stack[0]["number"], base=base, trunk=trunk, found=", ".join(f"#{pull['number']}" for pull in parents) or "none")
        if any(pull["number"] == parents[0]["number"] for pull in stack):
            raise refusal("cycle", pr=parents[0]["number"], stack=" <- ".join(f"#{pull['number']}" for pull in stack))
        stack.insert(0, gh.api(f"pulls/{parents[0]['number']}"))
    return stack


def stack_gate_pending(pull: dict, trunk: str, checks: dict) -> bool:
    """A non-bottom PR reads `unstable` while Graphite's mergeability check waits on the PR below it, and nothing else is unfinished."""
    unfinished = [run["name"] for run in checks["check_runs"] if run["status"] != "completed" and run["name"] != AI_REVIEW_CHECK]
    return pull["mergeable_state"] == "unstable" and pull["base"]["ref"] != trunk and unfinished == [STACK_MERGEABILITY_CHECK]


def guard(
    shell: Shell, gh: Github, rows: dict[str, dict[str, str]], pull: dict, expected: str | None, above: str | None, trunk: str, checkout: Path | None
) -> list[str]:
    """Every per-PR guard, in order; returns the approvers or raises the first refusal."""
    pr = str(pull["number"])
    fields = rows.get(pr, {})
    head, base = pull["head"]["sha"], pull["base"]["ref"]
    if pull["state"] != "open":
        raise refusal("closed", pr=pr, state=pull["state"], base=base)
    if expected and not head.startswith(expected):
        raise refusal("moved", expected=expected[:9], head=head[:9])
    if is_held(fields):
        raise refusal("held", pr=pr, reason=fields["hold_reason"], until=fields["hold_until"])
    if lane_held(fields) and fields["reported_head"] == head:
        raise refusal("held", pr=pr, reason="its lane reported this head held", until="the lane reports it again")
    if fields.get("label_head") == head and fields.get("label_pulled_at"):
        raise refusal("pulled", head=head[:9], at=fields["label_pulled_at"], reason=fields["label_pull_reason"])
    if fields.get("label_head") == head and fields.get("labelled_at"):
        raise refusal("labelled", head=head[:9], at=fields["labelled_at"])
    approved = approvers(gh, pr)
    if not approved:
        raise refusal("unapproved", pr=pr)
    status = gh.api(f"commits/{head}/status")
    checks = gh.api(f"commits/{head}/check-runs", per_page=PAGE_SIZE)
    if pull["mergeable_state"] not in LABELLABLE_STATES and not stack_gate_pending(pull, trunk, checks):
        raise refusal("mergeable", state=pull["mergeable_state"], allowed="/".join(LABELLABLE_STATES))
    if status["state"] != "success":
        raise refusal("status", state=status["state"], head=head[:9])
    failed = [run["name"] for run in checks["check_runs"] if run["conclusion"] in FAILED_CONCLUSIONS]
    if failed:
        raise refusal("checks", head=head[:9], names=", ".join(failed))
    review = latest_ai_review(gh, head)
    if review and review["status"] != "completed":
        raise refusal("reviewing", head=head[:9])
    verdict = review["conclusion"] if review else AI_REVIEW_ABSENT
    if verdict != "success":
        raise refusal("ai-review", state=verdict, head=head[:9])
    children = [str(child["number"]) for child in open_children(gh, pull["head"]["ref"]) if str(child["number"]) != above and not stacked(gh, rows, child)]
    if children:
        raise refusal("children", pr=pr, branch=pull["head"]["ref"], children=", ".join(f"#{c}" for c in children), trunk=trunk)
    if checkout:
        conflict = merge_conflicts(shell, checkout, pr, base, head)
        if conflict:
            raise conflict
    return approved


def stack_enqueue_script(checkout: Path | None) -> Path | None:
    """The repository's own enqueue script, when the checkout carries one; otherwise the label is the enqueue."""
    script = checkout / STACK_ENQUEUE if checkout else None
    return script if script and script.is_file() else None


def run_stack_enqueue(shell: Shell, script: Path, tip: str, heads: dict[str, str], dry_run: bool) -> dict[str, tuple[str, Refused]]:
    """Enqueue `tip` with its whole downstack, or with `dry_run` only gate it; returns each PR the script blocked.

    Exit 2 means the enqueue was made and part of it dropped or has not settled, so the
    heads count as enqueued: the same head is never queued twice.
    """
    try:
        report = shell.run([str(script), tip] + (["--check"] if dry_run else []))
    except subprocess.CalledProcessError as failure:
        output = failure.stdout or ""
        if failure.returncode != STACK_ENQUEUE_UNSETTLED:
            blocked = STACK_ENQUEUE_BLOCKED.findall(output) or [(tip, output.strip() or (failure.stderr or "").strip() or f"exit {failure.returncode}")]
            return {pr: (heads[pr], refusal("enqueue", pr=pr, tip=tip, detail=detail)) for pr, detail in blocked}
        report = output
    print(report, end="")
    return {}


def record_refusals(notes: Notes, rows: dict[str, dict[str, str]], refusals: dict[str, tuple[str, str]]) -> None:
    """Write each tracked row's refusal and the head it was graded at, so `stale` can name the blocker."""
    at = utc_stamp()
    for pr, (head, reason) in refusals.items():
        if pr in rows:
            notes.set_fields(pr, {"head": head, "label_refused": reason, "label_refused_head": head, "label_refused_at": at})


def label_stack(
    shell: Shell, gh: Github, notes: Notes, rows: dict[str, dict[str, str]], trunk: str, tip: dict, expected: str | None, checkout: Path | None, dry_run: bool
) -> dict[str, tuple[str, Refused]]:
    """Guard every PR from the trunk up to `tip`, then enqueue the tip once, through the repository's stack-enqueue script or else the label; returns each refusing PR's head and reason."""
    number = str(tip["number"])
    try:
        stack = stack_to_trunk(gh, tip, trunk)
    except Refused as refused:
        print(f"REFUSED {refused}")
        if not dry_run:
            record_refusals(notes, rows, {number: (tip["head"]["sha"], str(refused))})
        return {number: (tip["head"]["sha"], refused)}
    numbers = [str(pull["number"]) for pull in stack]
    approved: dict[str, list[str]] = {}
    refused: dict[str, tuple[str, Refused]] = {}
    for index, pull in enumerate(stack):
        pr, head = numbers[index], pull["head"]["sha"]
        fields = rows.get(pr, {})
        above = numbers[index + 1] if index + 1 < len(stack) else None
        try:
            if not is_ours(fields):
                raise refusal("foreign", pr=pr, tip=number)
            approved[pr] = guard(shell, gh, rows, pull, expected if pr == number else current_head(fields), above, trunk, checkout)
        except Refused as reason:
            print(f"REFUSED {reason} (#{pr})")
            refused[pr] = (head, reason)
    chain = " <- ".join(f"#{pr}" for pr in numbers)
    script = stack_enqueue_script(checkout)
    if script and not refused:
        heads = {pr: pull["head"]["sha"] for pull, pr in zip(stack, numbers, strict=True)}
        refused = run_stack_enqueue(shell, script, number, heads, dry_run)
        for pr, (_, reason) in refused.items():
            print(f"REFUSED {reason} (#{pr})")
    if refused:
        whole = REFUSAL["stack"].format(stack=chain, refused=", ".join(f"#{pr}" for pr in refused))
        print(f"REFUSED {whole}")
        if not dry_run:
            record_refusals(notes, rows, {pr: (pull["head"]["sha"], str(refused[pr][1]) if pr in refused else whole) for pull, pr in zip(stack, numbers, strict=True)})
        return refused
    head = stack[-1]["head"]["sha"]
    if dry_run:
        if not script:
            print(f"would label #{number} {head}" + (f", enqueuing {chain}" if len(stack) > 1 else ""))
        return {}
    if not script:
        gh.add_label(number, MERGE_LABEL)
    labelled = utc_stamp()
    print(f"{'enqueued' if script else 'labelled'} #{number} {head} at {labelled}; the queue takes {chain} as one entry")
    for pull, pr in zip(stack, numbers, strict=True):
        head, approved_by = pull["head"]["sha"], ",".join(approved[pr])
        notes.set_fields(
            pr,
            {
                "head": head,
                "base": pull["base"]["ref"],
                "label_head": head,
                "labelled_at": labelled,
                "label_stack": ",".join(numbers),
                "approved_by": approved_by,
                "label_pulled_at": "",
                "label_pull_reason": "",
                "label_refused": "",
                "label_refused_head": "",
                "label_refused_at": "",
            },
        )
        print(f"#{pr} {head[:9]} approved by {approved_by}")
    return {}


def is_label_candidate(fields: dict[str, str]) -> bool:
    """Tracked, open, unheld, and never labelled at its current head; a lane's report gates nothing."""
    head = current_head(fields)
    return bool(head) and is_tracked(fields) and is_open(fields) and not is_held(fields) and not lane_held(fields) and fields.get("label_head") != head


def label_candidates(rows: dict[str, dict[str, str]]) -> list[str]:
    return sorted((pr for pr, fields in rows.items() if is_label_candidate(fields)), key=int)


def route_refused(notes: Notes, gh: Github, rows: dict[str, dict[str, str]], refused: dict[str, tuple[str, Refused]], dry_run: bool, gone: frozenset[str]) -> None:
    """Send each lane one line per refused head and blocker.

    A push since the refresh is not a blocker, since the next pass grades the new head, and
    red CI or a conflict is `route`'s to send.
    """
    for pr, (head, reason) in refused.items():
        fields = rows.get(pr, {})
        if not is_tracked(fields) or reason.kind in UNROUTED_REFUSALS or needs_route(fields):
            continue
        if fields["lane"] in gone:
            print(f"#{pr} {fields['lane']} is gone; its refusal goes to the root, not the lane")
            continue
        job = f"new head {head[:9]}: {reason}"
        if route_one(notes, gh, pr, dict(fields, head=head), job, None, dry_run):
            fields |= {"routed_head": head, "routed_job": job, "routed_lane": fields["lane"], "routed_at": utc_stamp()}


def cmd_held(args: argparse.Namespace, shell: Shell) -> int:
    print("".join(f"{pr}\n" for pr in sorted(cci.held_prs(shell.run, args.drive), key=int)), end="")
    return 0


def cmd_label(args: argparse.Namespace, shell: Shell) -> int:
    gh = Github(shell, args.repo)
    notes = Notes(shell, args.ledger, args.scope)
    trunk = gh.default_branch()
    every = notes.rows()
    rows = {key: fields for key, fields in every.items() if key.isdigit()}
    if not args.all_clean:
        refused = label_stack(shell, gh, notes, every, trunk, gh.api(f"pulls/{args.pr}"), args.expect_head, args.checkout, args.dry_run)
        return 1 if refused else 0
    gone = gone_lanes(every)
    pulls = {pr: pull for pr in label_candidates(sharded(rows, args.shard)) if (pull := gh.api(f"pulls/{pr}"))["state"] == "open"}
    bases = {pull["base"]["ref"] for pull in pulls.values()}
    tips = [pr for pr, pull in pulls.items() if pull["head"]["ref"] not in bases]
    passed, failed = [], []
    for pr in tips:
        refused = label_stack(shell, gh, notes, every, trunk, gh.api(f"pulls/{pr}"), current_head(rows[pr]), args.checkout, args.dry_run)
        (failed if refused else passed).append(pr)
        route_refused(notes, gh, rows, refused, args.dry_run, gone)
    verb = "would label" if args.dry_run else "labelled"
    print(f"batch: {verb} {len(passed)} of {len(tips)} stacks" + "".join(f" #{pr}" for pr in passed) + (" | refused" + "".join(f" #{pr}" for pr in failed) if failed else ""))
    return 0


def pull_label(gh: Github, notes: Notes, pr: str, reason: str) -> str:
    head = gh.api(f"pulls/{pr}")["head"]["sha"]
    gh.remove_label(pr, MERGE_LABEL)
    pulled = utc_stamp()
    notes.set_fields(pr, {"label_head": head, "label_pulled_at": pulled, "label_pull_reason": reason})
    return f"pulled {MERGE_LABEL} from #{pr} {head[:9]} at {pulled}; a pull is not a stop, the queue may already own this head"


def cmd_unlabel(args: argparse.Namespace, shell: Shell) -> int:
    print(pull_label(Github(shell, args.repo), Notes(shell, args.ledger, args.scope), args.pr, args.reason))
    return 0


def cmd_unadmit(args: argparse.Namespace, shell: Shell) -> int:
    """Withdraw each named PR from the drive: its row stays, unregistered and marked, so neither a refresh nor the landing desk admits it again, and the merge label that `label` set on every tip whose stack holds it comes off unless something pulled it."""
    notes = Notes(shell, args.ledger, args.scope)
    with locked(default_lock(args.ledger)):
        rows = notes.pr_rows()
        if missing := [pr for pr in args.pr if pr not in rows]:
            raise SystemExit(f"{args.ledger} has no row for {' '.join(f'#{pr}' for pr in missing)}")
        withdrawn = utc_stamp()
        for pr in args.pr:
            notes.set_fields(pr, {"registered": "", "unadmitted": withdrawn})
    tips: dict[str, list[str]] = {}
    for key, fields in rows.items():
        stack = fields.get("label_stack", "").split(",") if fields.get("label_stack") else [key]
        if fields.get("labelled_at") and not fields.get("label_pulled_at") and stack[-1] == key:
            for pr in args.pr:
                if pr in stack:
                    tips.setdefault(key, []).append(pr)
    for pr in args.pr:
        print(f"unadmitted #{pr} at {withdrawn}: the drive no longer gates or enqueues it, and only `ledger.py register --pr {pr}` admits it again")
    gh = Github(shell, args.repo)
    for tip, withdrawn_prs in tips.items():
        print(pull_label(gh, notes, tip, f"unadmitted {' '.join(f'#{pr}' for pr in withdrawn_prs)} from the drive"))
    return 0


def cmd_requeue(args: argparse.Namespace, shell: Shell) -> int:
    notes = Notes(shell, args.ledger, args.scope)
    head = notes.rows().get(args.pr, {}).get("label_head", "")
    if not head or not head.startswith(args.expect_head):
        print(f"#{args.pr} {args.expect_head[:9]} carries no label latch; label may queue it")
        return 0
    requeued = utc_stamp()
    notes.set_fields(args.pr, {"labelled_at": "", "label_pulled_at": "", "label_pull_reason": "", "label_requeued_at": requeued})
    print(f"cleared the label latch on #{args.pr} {head[:9]} at {requeued}; label may queue the same head once more")
    return 0


def sync_trunk(shell: Shell, checkout: Path, trunk: str) -> None:
    if is_shallow(shell, checkout):
        raise ForgeUnreachable(REFUSAL["shallow"].format(checkout=checkout))
    fetch(shell, checkout, f"+refs/heads/{trunk}:refs/desk/base/{trunk}")


def absent_heads(shell: Shell, checkout: Path, heads: list[str]) -> frozenset[str]:
    out = shell.run(["git", "-C", str(checkout), "cat-file", "--batch-check"], stdin="".join(f"{head}\n" for head in heads))
    return frozenset(line.split()[0] for line in out.splitlines() if line.endswith(" missing"))


def pull_records(gh: Github, prs: list[str]) -> dict[str, dict]:
    """Each PR's REST read in the ccx record's shape, so settling a landing never waits on the GraphQL quota."""
    try:
        with ThreadPoolExecutor(SETTLE_WORKERS) as pool:
            pulls = list(pool.map(lambda pr: gh.api(f"pulls/{pr}"), prs))
    except subprocess.CalledProcessError as failure:
        raise ForgeUnreachable(f"gh api pulls: {(failure.stderr or '').strip() or failure}") from failure
    return {
        pr: {"state": pull["state"].upper(), "headRefOid": pull["head"]["sha"], "baseRefName": pull["base"]["ref"], "createdAt": pull["created_at"]} for pr, pull in zip(prs, pulls, strict=True)
    }


def settle_row(trunk: str, pr: str, record: dict, read: Future, squashes: Callable[[], dict[str, tuple[str, str]]]) -> tuple[dict[str, str], str]:
    """One row's settled fields and line, against the trunk, never against the PR's own base: the queue deletes a stacked PR's base branch when it lands, so that ref is often gone."""
    if record["state"] == "OPEN":
        ejected = read.result()
        if not ejected:
            return {}, ""
        return {"ejected_at": ejected}, f"#{pr} was EJECTED by the queue at {ejected} and still reads open; the label is gone exactly as a landing would leave it"
    cleared = {"settle_error": "", "settle_failed_at": ""}
    if (payload := read.result()):
        sha, landed_at = payload
        return {"state": LANDED, "landed_sha": sha, "landed_at": landed_at, "base": trunk, **cleared}, f"landed #{pr}, payload delivered by {sha[:9]} on {trunk} at {landed_at}"
    if (squash := squashes().get(pr)):
        sha, landed_at = squash
        return {"state": LANDED, "landed_sha": sha, "landed_at": landed_at, "base": trunk, **cleared}, f"landed #{pr} as {sha[:9]} on {trunk} at {landed_at}, which has moved on its files since"
    return (
        {"state": CLOSED_WITHOUT_SQUASH, "base": record["baseRefName"], **cleared},
        f"#{pr} is {CLOSED_WITHOUT_SQUASH} on {trunk}: a human closed it and its payload is absent, so the row stays until its lane answers",
    )


def settle(shell: Shell, gh: Github, notes: Notes, checkout: Path, records: dict[str, dict], searched: dict[str, tuple[str, str]] | None = None) -> int:
    """Settle every row in one trunk fetch, one object check, one ledger write, and the per-PR forge reads in parallel.

    A head this clone lacks is fetched before any diff, one at a time, so no two fetches race for the clone's locks.
    A row content cannot settle takes its squash from `searched`, else from one trunk search since its PRs opened.
    """
    if not records:
        return 0
    trunk = gh.default_branch()
    git = ["git", "-C", str(checkout)]
    closed = {pr: record for pr, record in records.items() if record["state"] != "OPEN"}
    failures: dict[str, subprocess.CalledProcessError] = {}
    if closed:
        sync_trunk(shell, checkout, trunk)
        absent = absent_heads(shell, checkout, [record["headRefOid"] for record in closed.values()])
        for pr, record in closed.items():
            if record["headRefOid"] in absent:
                try:
                    shell.run(git + ["fetch", "-q", "origin", f"+refs/pull/{pr}/head:refs/desk/pr{pr}"])
                except subprocess.CalledProcessError as failure:
                    failures[pr] = failure
    updates: dict[str, dict[str, str]] = {}

    @cache
    def squashes() -> dict[str, tuple[str, str]]:
        return trunk_squashes(shell, checkout, trunk, closed, min(record["createdAt"] for record in closed.values())) if searched is None else searched

    moved = 0
    with ThreadPoolExecutor(SETTLE_WORKERS) as pool:
        reads = {
            pr: pool.submit(landed_on_base, shell, gh, checkout, f"refs/desk/base/{trunk}", pr, record["headRefOid"]) if pr in closed else pool.submit(queue_ejected, gh, pr)
            for pr, record in records.items()
            if pr not in failures
        }
        for pr in sorted(records, key=int):
            try:
                if pr in failures:
                    raise failures[pr]
                fields, line = settle_row(trunk, pr, records[pr], reads[pr], squashes)
            except subprocess.CalledProcessError as failure:
                reason = (failure.stderr or str(failure)).strip()
                updates[pr] = {"settle_error": reason, "settle_failed_at": utc_stamp()}
                print(f"#{pr} NOT SETTLED, recorded on its row and the pass continues: {reason}")
                continue
            if fields:
                updates[pr] = fields
            if line:
                print(line)
            moved += pr in closed
    if updates:
        notes.sync([{"key": pr, "fields": fields} for pr, fields in updates.items()])
    return moved


def cmd_landed(args: argparse.Namespace, shell: Shell) -> int:
    gh = Github(shell, args.repo)
    notes = Notes(shell, args.ledger, args.scope)
    rows = sharded(notes.pr_rows(), args.shard)
    prs = [pr for pr in args.pr if pr in rows] if args.pr else [pr for pr in sorted(rows, key=int) if rows[pr].get("state") != LANDED]
    settle(shell, gh, notes, args.checkout, pull_records(gh, prs))
    return 0


def watch_state(ledger: str, shard: frozenset[str] | None, scope: Scope) -> Path:
    name = ledger if same_repo(scope.repo, scope.default) else f"{ledger}.{scope.repo.replace('/', '.')}"
    name = name if shard is None else f"{name}.{hashlib.sha256('+'.join(sorted(shard)).encode()).hexdigest()[:16]}"
    return Path.home() / ".cache" / "ccn-ledger" / f"{name}.watch.json"


def snapshot_prs(state: Path) -> frozenset[str]:
    return frozenset(json.loads(state.read_text()).get("prs") or ()) if state.exists() else frozenset()


def watch_line(event: dict) -> str:
    detail = event.get("detail", "")
    if event["event"] == "ejected" and detail:
        return f"ejected ({detail})"
    return f"{event['event']} {detail}".strip()


def is_watch_p0(event: dict, fields: dict[str, str], priority: frozenset[str]) -> bool:
    """An ejection always is: the PR was in the queue, so someone meant it to land."""
    if event["event"] == "ejected":
        return True
    return event["event"] in WATCH_P0_EVENTS and (str(event["pr"]) in priority or carries_label(fields) or fields.get("watch_queued") == "true")


def announce_reports(messages: Messages, cursor: str, rows: dict[str, dict[str, str]], shard: frozenset[str] | None) -> None:
    """Each report lands once on the watch's own cci cursor; one whose PR is already settled stays quiet."""
    lanes = [f"--lane={lane}" for lane in sorted(shard or ())]
    out = messages.shell.run(["cci", "tail", "--drive", messages.drive, "--cursor", cursor, "--json", "--budget", str(cci.PAGE_BYTES), "--kind=report", *lanes])
    for record in (json.loads(line) for line in out.splitlines() if line.strip()):
        fields = {"pr": record.get("topic") or NO_PR, "head": record.get("fields", {}).get("commit") or NO_PR, "lane": record["lane"], "text": record["text"], "kind": "report"}
        if rows.get(fields["pr"], {}).get("state") not in TERMINAL_STATES:
            key = f"#{record['seq']}"
            print(f"REPORT {message_line(key, fields)}", flush=True)


def watch_pass(
    shell: Shell, notes: Notes, messages: Messages, gh: Github, checkout: Path, ccx: str, state: Path, priority: frozenset[str], shard: frozenset[str] | None
) -> None:
    stored = notes.stored()
    announce_reports(messages, f"ledger-watch:{state.name}", stored, shard)
    rows = sharded({key: fields for key, fields in notes.view(stored).items() if key.isdigit()}, shard)
    watched = sorted((pr for pr, fields in rows.items() if fields.get("state") not in TERMINAL_STATES), key=int)
    if not watched:
        return
    armed = snapshot_prs(state)
    pending = state.with_name(state.name + ".pending")
    if state.exists():
        shutil.copyfile(state, pending)
    else:
        pending.unlink(missing_ok=True)
    argv = [ccx, "vcs", "pr", "watch", *watched, "--repo", gh.repo, "--once", "--json", "--until", "never", "--state", str(pending)]
    events = []
    for line in shell.run(argv).splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            print(f"ccx: {line}", file=sys.stderr)
    settled: list[str] = []
    recorded: dict[str, dict[str, str]] = {}
    for event in events:
        if "pr" not in event:
            print(watch_line(event), flush=True)
            continue
        pr = str(event["pr"])
        if event["event"] in (LANDED, CLOSED_WITHOUT_SQUASH) and pr not in armed:
            continue
        fields = rows[pr]
        line = watch_line(event)
        changes = recorded.setdefault(pr, {})
        changes.update(watch_event=line, watch_at=event["at"])
        if event["event"] == "queued":
            changes["watch_queued"] = "true"
        elif event["event"] in ("ejected", LANDED, CLOSED_WITHOUT_SQUASH):
            changes["watch_queued"] = ""
        if event["event"] == "ejected":
            changes["ejected_at"] = event["at"]
        if is_watch_p0(event, fields, priority):
            lane = fields.get("lane", "?")
            with redirect_stdout(sys.stderr):
                enqueue(messages, {"kind": "p0", "pr": notes.key(pr), "head": event.get("head") or current_head(fields), "lane": lane, "event": line, "text": f"{ref(notes.key(pr))} {line}: rebase or fix now"})
            print(f"P0 #{pr} {line} {lane}", flush=True)
        else:
            print(f"#{pr} {line}", file=sys.stderr)
        fields.update(changes)
        if event["event"] in (LANDED, CLOSED_WITHOUT_SQUASH):
            settled.append(pr)
    for pr, changes in recorded.items():
        notes.set_fields(pr, changes)
    if settled:
        with redirect_stdout(sys.stderr):
            settle(shell, gh, notes, checkout, pull_records(gh, settled))
    if pending.exists():
        pending.replace(state)


def cmd_watch(args: argparse.Namespace, shell: Shell) -> int:
    notes = Notes(shell, args.ledger, args.scope)
    gh = Github(shell, args.repo)
    priority = frozenset(args.priority or ())
    state = args.state or watch_state(args.ledger, args.shard, args.scope)
    while True:
        try:
            watch_pass(shell, notes, Messages(shell, args.drive), gh, args.checkout, args.ccx, state, priority, args.shard)
        except (subprocess.CalledProcessError, ForgeUnreachable) as failure:
            detail = failure.stderr.strip() if isinstance(failure, subprocess.CalledProcessError) and failure.stderr else str(failure)
            print(f"watch pass failed, retrying next pass: {detail}", file=sys.stderr)
            if args.once:
                return 1
        if args.once:
            return 0
        time.sleep(args.interval)


def trunk_squashes(shell: Shell, checkout: Path, trunk: str, prs: Collection[str], since: str = "") -> dict[str, tuple[str, str]]:
    """The squash on the trunk whose subject ends with each PR's ``(#n)``, newest first.

    Searched by number, never to a fixed depth: a busy trunk takes hundreds of squashes
    a day, so a PR closed days ago falls past any depth. ``since`` bounds the walk at a
    moment no squash of these PRs predates, such as the earliest one's creation.

    Nothing the forge asserts about itself is admissible here. The closing actor proves
    nothing, because the queue's bot also closes a stacked child when its base branch is
    deleted, landing nothing. The queue's own `externally-merged` label proves nothing
    either: it is applied to OPEN pull requests whose content never reached the trunk —
    two carried it while their trees still differed and no commit named them. Only the
    trunk answers, through its tree or through a commit it names.
    """
    if not prs:
        return {}
    bound = [f"--since={since}"] if since else []
    grep = [f"--grep=(#{pr})" for pr in sorted(prs, key=int)]
    log = shell.run(["git", "-C", str(checkout), "log", f"refs/desk/base/{trunk}", *bound, "--fixed-strings", *grep, "--format=%H %cI %s"])
    squashes: dict[str, tuple[str, str]] = {}
    for line in log.splitlines():
        sha, committed, subject = line.split(" ", 2)
        if (match := SQUASH_SUBJECT.search(subject)) and match[1] in prs:
            squashes.setdefault(match[1], (sha, stamp(parse_iso(committed).astimezone(timezone.utc))))
    return squashes


def cmd_reconcile(args: argparse.Namespace, shell: Shell) -> int:
    """Settle the whole board in one log read, one ccx cache read, and one ledger write.

    A squash subject ending ``(#n)`` lands its row outright. Only a row with no squash
    whose PR the cache reads closed costs a forge call, its file list, through ``settle``,
    because a payload a stacked child carried shows up in the tree under no number of its own.
    """
    notes = Notes(shell, args.ledger, args.scope)
    gh = Github(shell, args.repo)
    rows = sharded(notes.pr_rows(), args.shard)
    pending = sorted((pr for pr, fields in rows.items() if fields.get("state") not in TERMINAL_STATES), key=int)
    if not pending:
        print("reconciled 0 non-terminal rows")
        return 0
    trunk = gh.default_branch()
    sync_trunk(shell, args.checkout, trunk)
    squashes = trunk_squashes(shell, args.checkout, trunk, pending)
    landed = [pr for pr in pending if pr in squashes]
    unsquashed = [pr for pr in pending if pr not in squashes]
    records: dict[str, dict] | None
    try:
        records = pr_state(shell, args.ccx, args.repo, unsquashed, [])["prs"]
    except QuotaExhausted as exhausted:
        print(cache_banner(exhausted, {pr: rows[pr] for pr in unsquashed}, now()))
        records = None
    except subprocess.CalledProcessError as failure:
        raise ForgeUnreachable(f"ccx vcs pr state: {(failure.stderr or '').strip() or failure}") from failure
    closed = [] if records is None else [pr for pr in unsquashed if records[pr]["state"] != "OPEN"]
    verb = "would land" if args.dry_run else "landed"
    for pr in landed:
        sha, landed_at = squashes[pr]
        print(f"{verb} #{pr} as {sha[:9]} on {trunk} at {landed_at}")
    if args.dry_run:
        print("".join(f"would settle closed #{pr} with no squash on {trunk}\n" for pr in closed), end="")
        settled = 0
    else:
        if landed:
            notes.sync([{"key": pr, "fields": {"state": LANDED, "landed_sha": squashes[pr][0], "landed_at": squashes[pr][1], "base": trunk, "settle_error": "", "settle_failed_at": ""}} for pr in landed])
        settled = settle(shell, gh, notes, args.checkout, {pr: records[pr] for pr in closed}, squashes)
    unsettled = f"{len(unsquashed)} unread" if records is None else f"{len(unsquashed) - len(closed)} open"
    print(f"reconciled {len(pending)} non-terminal rows: {len(landed)} landed by squash, {settled} of {len(closed)} closed settled, {unsettled}")
    return 0


def cmd_summary(args: argparse.Namespace, shell: Shell) -> int:
    try:
        cmd_reconcile(args, shell)
    except ForgeUnreachable as unreachable:
        print(f"summary: reconcile skipped this pass, {unreachable}")
    notes = Notes(shell, args.ledger, args.scope)
    stored = notes.stored()
    rows = sharded(notes.view(stored), args.shard)
    prompts = prompt_lines(shell, args.drive, args.shard)
    messages = Messages(shell, args.drive).read(args.shard)
    print("\n".join(summary_lines(rows, pull_rows(stored), messages, now(), timedelta(seconds=args.window_seconds), timedelta(minutes=args.stale_minutes), prompts)))
    return 0


def cmd_stale(args: argparse.Namespace, shell: Shell) -> int:
    rows = sharded(Notes(shell, args.ledger, args.scope).pr_rows(), args.shard)
    moment = now()
    lines = stale_lines(rows, moment, timedelta(minutes=args.minutes)) + aged_lines(rows, moment, timedelta(hours=args.hours))
    print("\n".join(lines) if lines else f"no clean row older than {args.minutes}m and no PR older than {args.hours}h")
    return 0


def cmd_show(args: argparse.Namespace, shell: Shell) -> int:
    if args.json:
        sys.stdout.write(shell.run(["ccn", "ledger", "show", args.ledger, "--json"]))
        return 0
    if args.asks:
        rows = Notes(shell, args.ledger).rows()
        prs = pull_rows(rows)
        live = live_since(rows)
        moment = now()
        asks = {key: fields for key, fields in rows.items() if key.startswith(ASK_PREFIX)}
        for key, fields in sorted(asks.items()):
            print(f"{ask_line(key, fields)} [{ask_state(fields, prs, moment, live) or 'pending'}]{progress_line(fields, moment)}")
        return 0
    rows = Notes(shell, args.ledger, args.scope).pr_rows()
    if args.red:
        rows = {key: fields for key, fields in rows.items() if needs_route(fields)}
    print(render_table(rows))
    return 0


def cmd_list(args: argparse.Namespace, shell: Shell) -> int:
    every = Notes(shell, args.ledger, args.scope).rows()
    rows = {
        key: fields
        for key, fields in every.items()
        if key.isdigit() and (not args.lane or fields.get("lane") == args.lane) and (not args.open or is_open(fields))
    }
    ordered = sorted(rows.items(), key=lambda item: int(item[0]))
    if args.json:
        asked = {pr for key, fields in every.items() if key.startswith(ASK_PREFIX) and not fields.get("dropped_at") and not fields.get("answered_at") for pr in linked_prs(fields)}
        print(
            json.dumps(
                [
                    {"pr": key, "repo": args.repo, **fields, "priority": queue_priority(fields, asked, key), "ours": is_open(fields) and is_ours(fields)}
                    for key, fields in ordered
                ]
            )
        )
        return 0
    for key, fields in ordered:
        print(f"#{key} {fields.get('lane', NO_PR)} {fields.get('state', 'open')} {fields.get('branch', NO_PR)} {current_head(fields)[:12] or NO_PR}")
    return 0


def add_ledger(parser: argparse.ArgumentParser, repo: bool = False, drive: bool = False) -> None:
    if repo:
        parser.add_argument("--repo", metavar="OWNER/NAME", help="the repository the PRs live in; default: the origin of --checkout, else of the working directory")
    parser.add_argument("--ledger", help="the ledger id; default: the ledger of the registered drive --drive names, else of the drive this session runs in")
    if drive:
        parser.add_argument("--drive", help="the cci drive that carries the lane messages; default: the cci drive of the registered drive --ledger names, else of this session's")
    else:
        parser.add_argument("--drive", help="the registered drive, by id or cci drive, whose ledger --ledger defaults to")
    parser.set_defaults(bus=drive)


def ask_key(value: str) -> str:
    if not (match := ASK_KEY.fullmatch(value)):
        raise argparse.ArgumentTypeError("an ask id as ledger.py ask printed it, like ask/000104, or its number, like 000104")
    return f"{ASK_PREFIX}{int(match['seq']):06d}"


def pr_number(value: str) -> str:
    if not value.isdigit():
        raise argparse.ArgumentTypeError("a bare PR number, like 28510")
    return value


def head_prefix(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{7,40}", value):
        raise argparse.ArgumentTypeError("a 7 to 40 character lowercase hex prefix of the head sha")
    return value


def add_shard(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--shard", type=shard_lanes, metavar="LANE,LANE", help="only the rows and messages of these lanes")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ledger.py", description=__doc__.splitlines()[0])
    parser.add_argument("-C", dest="directory", type=Path, help="run as if started in this directory, which ccn reads the ledger's repository from; default: the working directory, or outside any checkout the checkout of the registered drive whose --ledger or --drive the command names")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="create the ledger and print its id")
    init.add_argument("--title", required=True)
    init.set_defaults(handler=cmd_init)

    report = subparsers.add_parser("report", help="record a lane's 3-line ship report; the only way a PR row is opened by hand")
    add_ledger(report, repo=True, drive=True)
    report.add_argument("--pr", required=True, type=pr_number)
    report.add_argument("--head", required=True, type=head_prefix)
    report.add_argument("--lane", required=True)
    report.add_argument("--verdict", required=True, choices=VERDICTS)
    report.add_argument("--text", default="")
    report.add_argument("--ask", type=ask_key, metavar="ID", help="link this PR to the owner ask it delivers")
    report.set_defaults(handler=cmd_report)

    ask = subparsers.add_parser("ask", help="record an owner ask verbatim with its lane and acceptance check, or mark an ask's state with --state")
    add_ledger(ask)
    ask.add_argument("--text")
    ask.add_argument("--lane")
    ask.add_argument("--accept", metavar="CHECK")
    ask.add_argument("--state", choices=ASK_STATES, help="override the computed state of --ask; open restores it")
    ask.add_argument("--ask", type=ask_key, metavar="ID")
    ask.add_argument("--note", default="")
    ask.add_argument(
        "--standing",
        nargs="?",
        const=STANDING_HOURS,
        type=int,
        metavar="HOURS",
        help=f"an ask that is never done: IN-PROGRESS while its latest progress entry is under HOURS old (default {STANDING_HOURS}), STALE after, never LOST; with --ask, makes that ask standing",
    )
    ask.set_defaults(handler=cmd_ask)

    progress = subparsers.add_parser("progress", help="append a timestamped progress entry to an ask with no PR yet; restarts its 30-minute LOST clock")
    add_ledger(progress)
    progress.add_argument("--ask", required=True, type=ask_key, metavar="ID")
    progress.add_argument("--note", required=True)
    progress.add_argument("--artifact", action="append", default=[], metavar="URL", help="a Slack permalink, register entry, or other id the progress produced; repeatable")
    progress.set_defaults(handler=cmd_progress)

    drop = subparsers.add_parser("drop", help="terminal: the owner withdrew this ask")
    add_ledger(drop)
    drop.add_argument("--ask", required=True, type=ask_key, metavar="ID")
    drop.add_argument("--reason", required=True)
    drop.set_defaults(handler=cmd_drop)

    answer = subparsers.add_parser("answer", help="terminal: this ask was a question, now answered")
    add_ledger(answer)
    answer.add_argument("--ask", required=True, type=ask_key, metavar="ID")
    answer.add_argument("--text", required=True)
    answer.set_defaults(handler=cmd_answer)

    live = subparsers.add_parser("live", help="record when the pipeline that ships our lanes' PRs last ran, so a delivered ask can grade LIVE")
    add_ledger(live)
    live.add_argument("--at", required=True, metavar="ISO")
    live.add_argument("--text", default="")
    live.set_defaults(handler=cmd_live)

    register = subparsers.add_parser("register", help="track a lane's PRs, or every open PR on its branch prefix, with no per-head report")
    add_ledger(register)
    register.add_argument("--lane", required=True)
    register.add_argument("--branch-prefix", type=branch_prefix, help="the lane's branch name, or a namespace ending in '/' or '-' covering its stack")
    register.add_argument("--repo", metavar="OWNER/NAME", help="the repository the lane's PRs live in; default: the working directory's origin")
    register.add_argument("--ccx", default="ccx", help="the ccx binary")
    register.add_argument("--pr", action="append", default=[], metavar="N", type=pr_number)
    register.add_argument("--head", type=head_prefix, help="the head the one --pr was opened or pushed at")
    register.add_argument("--step", help="the plan step the lane's PRs carry, from its plan task's step:<n> label")
    register.set_defaults(handler=cmd_register)

    reassign = subparsers.add_parser("reassign", help="move PR rows to a new owning lane, so route and refresh address it from now on")
    add_ledger(reassign, repo=True)
    reassign.add_argument("--pr", action="append", required=True, metavar="N", type=pr_number)
    reassign.add_argument("--lane", required=True)
    reassign.set_defaults(handler=cmd_reassign)

    unregister = subparsers.add_parser("unregister", help="remove a lane's branch prefix and drop the untouched rows only that prefix pulled in, or remove the named PR rows whole")
    add_ledger(unregister, repo=True)
    named = unregister.add_mutually_exclusive_group(required=True)
    named.add_argument("--lane")
    named.add_argument("--pr", action="append", default=[], metavar="N", type=pr_number)
    unregister.add_argument("--branch-prefix", type=branch_prefix, help="refuse unless the lane is registered on exactly this prefix")
    unregister.set_defaults(handler=cmd_unregister)

    unadmit = subparsers.add_parser("unadmit", help="withdraw PRs from the drive: unregister their rows so no refresh or desk admits them again, and pull a merge label the desk set")
    add_ledger(unadmit, repo=True)
    unadmit.add_argument("--pr", action="append", required=True, metavar="N", type=pr_number)
    unadmit.set_defaults(handler=cmd_unadmit)

    enqueue_cmd = subparsers.add_parser("enqueue", help="record any lane message; duplicates by kind+PR+head are dropped")
    add_ledger(enqueue_cmd, repo=True, drive=True)
    enqueue_cmd.add_argument("--kind", required=True, choices=KINDS)
    enqueue_cmd.add_argument("--pr", required=True, type=pr_number)
    enqueue_cmd.add_argument("--head", required=True)
    enqueue_cmd.add_argument("--lane", required=True)
    enqueue_cmd.add_argument("--text", required=True)
    enqueue_cmd.set_defaults(handler=cmd_enqueue)

    ruling = subparsers.add_parser("ruling", help="record a question only the root can answer and print the RULING NEEDED line")
    add_ledger(ruling, repo=True, drive=True)
    ruling.add_argument("--lane", required=True)
    ruling.add_argument("--text", required=True)
    ruling.add_argument("--options", required=True, metavar="A|B|C")
    ruling.add_argument("--pr", default=NO_PR)
    ruling.set_defaults(handler=cmd_ruling)

    inbox = subparsers.add_parser("inbox", help="pending messages, P0 first, then rulings, reports, idles")
    add_ledger(inbox, drive=True)
    inbox.add_argument("--take", action="store_true")
    inbox.add_argument("--all", action="store_true")
    inbox.add_argument("--json", action="store_true")
    add_shard(inbox)
    inbox.set_defaults(handler=cmd_inbox)

    ack = subparsers.add_parser("ack", help="mark messages handled")
    add_ledger(ack, drive=True)
    ack.add_argument("keys", nargs="+")
    ack.set_defaults(handler=cmd_ack)

    refresh = subparsers.add_parser("refresh", help="regrade every PR the ledger holds, plus any --pr, and sync it")
    add_ledger(refresh, repo=True)
    refresh.add_argument("--pr", action="append", default=[], metavar="N", type=pr_number, help="admit this PR, reported by one of our lanes")
    refresh.add_argument("--lane", action="append", default=[], metavar="PR=NAME")
    refresh.add_argument("--lock", type=Path)
    refresh.add_argument("--ccx", default="ccx", help="the ccx binary")
    add_shard(refresh)
    refresh.set_defaults(handler=cmd_refresh)

    hold = subparsers.add_parser("hold", help="hold a PR with a reason and an expiry")
    add_ledger(hold, repo=True)
    hold.add_argument("--pr", required=True, type=pr_number)
    hold.add_argument("--reason", required=True)
    expiry = hold.add_mutually_exclusive_group(required=True)
    expiry.add_argument("--until")
    expiry.add_argument("--hours", type=float)
    hold.add_argument("--stack", action="store_true", help="hold every open row stacked on this PR too; required when any of them is green")
    hold.set_defaults(handler=cmd_hold)

    lift = subparsers.add_parser("lift", help="lift a hold")
    add_ledger(lift, repo=True)
    lift.add_argument("--pr", required=True, type=pr_number)
    lift.set_defaults(handler=cmd_lift)

    priority = subparsers.add_parser("priority", help="set the merge-queue priority of a PR; lower values gate and enqueue first")
    add_ledger(priority, repo=True)
    priority.add_argument("--pr", required=True, type=pr_number)
    priority.add_argument("--priority", required=True, type=int)
    priority.set_defaults(handler=cmd_priority)

    route = subparsers.add_parser("route", help="print the message that sends a red or conflicting head to its lane, once per PR, head, and job")
    add_ledger(route, repo=True)
    route.add_argument("--pr", type=pr_number)
    route.add_argument("--job", help="the failing job or blocker; read from the forge and Buildkite when omitted")
    route.add_argument("--lane")
    route.add_argument("--train", metavar="LANE", help="route a swept conflict, or a swept row whose lane is gone, to this train when its files match --paths")
    route.add_argument("--paths", nargs="+", default=[], metavar="GLOB", help="the train's hot set as fnmatch globs; * also crosses /")
    route.add_argument("--fallback", metavar="LANE", help="route any other swept row whose lane is gone to this live lane")
    route.add_argument("--dry-run", action="store_true")
    add_shard(route)
    route.set_defaults(handler=cmd_route)

    gone = subparsers.add_parser("gone", help="record that a lane has finished, so route never addresses it again")
    add_ledger(gone)
    gone.add_argument("--lane", required=True)
    gone.set_defaults(handler=cmd_gone)

    train = subparsers.add_parser("train", help="print the ordered car list of ready rows whose files touch the hot set")
    add_ledger(train, repo=True)
    train.add_argument("--paths", nargs="+", required=True, metavar="GLOB", help="fnmatch globs naming the hot set; * also crosses /")
    train.add_argument("--cars", type=int, default=TRAIN_CARS)
    add_shard(train)
    train.set_defaults(handler=cmd_train)

    held = subparsers.add_parser("held", help="print each PR an open cci hold record names, one number per line, for stack-enqueue --hold")
    held.add_argument("--drive", required=True, help="the cci drive whose hold records to read")
    held.set_defaults(handler=cmd_held)

    label = subparsers.add_parser("label", help="re-read every PR from the tip down to the trunk, run every guard on each, then enqueue the tip once: through the checkout's stack-enqueue, else by label")
    add_ledger(label, repo=True)
    target = label.add_mutually_exclusive_group(required=True)
    target.add_argument("--pr", type=pr_number)
    target.add_argument("--all-clean", action="store_true", help="label every clean, unheld, never-labelled stack's tip in one batch")
    label.add_argument("--expect-head", type=head_prefix)
    label.add_argument("--checkout", type=Path, help="clone for the conflict check; a copy of .agents/skills/submit-pr/scripts/stack-enqueue in it replaces the label")
    label.add_argument("--dry-run", action="store_true")
    add_shard(label)
    label.set_defaults(handler=cmd_label)

    unlabel = subparsers.add_parser("unlabel", help="pull the merge label and record why")
    add_ledger(unlabel, repo=True)
    unlabel.add_argument("--pr", required=True, type=pr_number)
    unlabel.add_argument("--reason", required=True)
    unlabel.set_defaults(handler=cmd_unlabel)

    requeue = subparsers.add_parser("requeue", help="clear the label latch on a PR's labelled or pulled head, so label may queue that same head once more")
    add_ledger(requeue, repo=True)
    requeue.add_argument("--pr", required=True, type=pr_number)
    requeue.add_argument("--expect-head", required=True, metavar="SHA")
    requeue.set_defaults(handler=cmd_requeue)

    landed = subparsers.add_parser("landed", help="settle closed PRs by the squash on the trunk")
    add_ledger(landed, repo=True)
    landed.add_argument("--checkout", type=Path, required=True)
    landed.add_argument("--pr", type=pr_number, action="append")
    add_shard(landed)
    landed.set_defaults(handler=cmd_landed)

    watch = subparsers.add_parser(
        "watch",
        help="subscribe to every non-terminal row through ccx vcs pr watch: record each transition on its row, "
        "and print a P0 line (with a p0 inbox message) for an ejection, or a conflict or red on a priority, labelled, or queued row",
    )
    add_ledger(watch, repo=True, drive=True)
    watch.add_argument("--checkout", type=Path, required=True)
    watch.add_argument("--priority", action="append", metavar="N", help="a PR whose conflict or red is always a P0; repeatable")
    watch.add_argument("--interval", type=float, default=60, help="seconds between passes")
    watch.add_argument("--once", action="store_true", help="one pass, then exit")
    watch.add_argument("--state", type=Path, help="ccx snapshot file (default ~/.cache/ccn-ledger/<ledger>[.<lanes>].watch.json)")
    add_shard(watch)
    watch.add_argument("--ccx", default="ccx", help="the ccx binary")
    watch.set_defaults(handler=cmd_watch)

    reconcile = subparsers.add_parser("reconcile", help="land every non-terminal row whose squash is on the trunk, and settle the closed rest, in one batch")
    add_ledger(reconcile, repo=True)
    reconcile.add_argument("--checkout", type=Path, default=Path("."), help="a full clone of the repo (default: the working directory)")
    reconcile.add_argument("--dry-run", action="store_true", help="print what would change and write nothing")
    reconcile.add_argument("--ccx", default="ccx", help="the ccx binary")
    add_shard(reconcile)
    reconcile.set_defaults(handler=cmd_reconcile)

    summary = subparsers.add_parser("summary", help="the desk-to-root report every 30 minutes, at most ten lines")
    add_ledger(summary, repo=True, drive=True)
    summary.add_argument("--checkout", type=Path, default=Path("."), help="a full clone of the repo (default: the working directory)")
    summary.add_argument("--window-seconds", type=int, default=WINDOW_SECONDS)
    summary.add_argument("--stale-minutes", type=int, default=STALE_MINUTES)
    summary.add_argument("--ccx", default="ccx", help="the ccx binary")
    add_shard(summary)
    summary.set_defaults(handler=cmd_summary, dry_run=False)

    stale = subparsers.add_parser("stale", help="every open row reported clean at least --minutes ago, with its blocker")
    add_ledger(stale, repo=True)
    stale.add_argument("--minutes", type=int, default=STALE_MINUTES)
    stale.add_argument("--hours", type=int, default=AGED_HOURS, help="also name every open PR opened at least this long ago")
    add_shard(stale)
    stale.set_defaults(handler=cmd_stale)

    show = subparsers.add_parser("show", help="render the PR rows")
    add_ledger(show, repo=True)
    view = show.add_mutually_exclusive_group()
    view.add_argument("--red", action="store_true")
    view.add_argument("--asks", action="store_true")
    show.add_argument("--json", action="store_true")
    show.set_defaults(handler=cmd_show)

    list_cmd = subparsers.add_parser("list", help="every PR row, oldest first, with its lane, state, branch, and head")
    add_ledger(list_cmd, repo=True)
    list_cmd.add_argument("--lane", help="only this lane's rows")
    list_cmd.add_argument("--open", action="store_true", help="only rows still open")
    list_cmd.add_argument("--json", action="store_true")
    list_cmd.set_defaults(handler=cmd_list)

    return parser


def main(argv: list[str] | None = None, shell: Shell | None = None) -> int:
    args = build_parser().parse_args(argv)
    shell = shell or Shell()
    if "ledger" in vars(args):
        default_from_drive(args)
    if args.directory:
        os.chdir(args.directory)
    elif not inside_checkout() and (checkout := registry_checkout(args)):
        os.chdir(checkout)
    if "repo" in vars(args):
        default = working_repo(shell, args.repo)
        checkout = vars(args).get("checkout")
        args.repo = args.repo or (origin_repo(shell, checkout) if checkout else default)
        args.scope = Scope(args.repo, default)
    try:
        return args.handler(args, shell)
    except ForgeUnreachable as unreachable:
        print(f"forge unreachable, wrote nothing: {unreachable}", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired as expired:
        print(f"{' '.join(expired.cmd)} timed out after {expired.timeout:.0f}s and was killed", file=sys.stderr)
        return 1
