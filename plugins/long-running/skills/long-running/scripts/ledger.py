#!/usr/bin/env python3
"""The landing desk's ledger over cc-notes — one row per PR our lanes shipped, REST only.

    ledger.py init    --title TEXT
    ledger.py report  --ledger ID --pr N --head SHA --lane NAME --verdict clean|red|conflicting|held [--text ...]
    ledger.py enqueue --ledger ID --kind p0|ruling|report|idle --pr N --head SHA --lane NAME --text ...
    ledger.py ruling  --ledger ID --lane NAME --text ... --options "A|B|C" [--pr N]
    ledger.py inbox   --ledger ID [--take] [--all] [--json]
    ledger.py ack     --ledger ID KEY...
    ledger.py refresh --repo owner/name --ledger ID [--pr N]... [--lane PR=NAME]... [--lock PATH]
    ledger.py hold    --ledger ID --pr N --reason ... (--until ISO | --hours H)
    ledger.py lift    --ledger ID --pr N
    ledger.py route   --repo owner/name --ledger ID [--pr N] [--job TEXT] [--lane NAME] [--dry-run]
    ledger.py label   --repo owner/name --ledger ID --pr TIP [--expect-head SHA] [--checkout DIR] [--dry-run]
    ledger.py unlabel --repo owner/name --ledger ID --pr N --reason ...
    ledger.py landed  --repo owner/name --ledger ID --checkout DIR [--pr N]
    ledger.py summary --ledger ID [--window-seconds N]
    ledger.py show    --ledger ID [--red] [--json]

STDLIB ONLY. A PR row exists because one of our lanes reported it, or because refresh
was handed its number; the repository's PR list is never read and GraphQL is never
called. Holds, routing, the label history, and the landing are fields on that row;
lane messages are ``msg/<seq>`` rows in the same ledger. A landing is proven by the
base branch's tree in ``--checkout`` holding the PR's own files, never by the PR's
merged field and never by searching the base log for its number. Buildkite
logs come from the repo-pinned ``bk``; storage is ``ccn ledger``. Every subprocess goes
through :class:`Shell`, the one seam tests replace.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import re
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

AI_REVIEW_CHECK = "ai-review"
AI_REVIEW_ABSENT = "absent"
APPROVED = "APPROVED"
REVIEW_DECISIONS = (APPROVED, "CHANGES_REQUESTED", "DISMISSED")
PAGE_SIZE = 100
ROUTE_STATES = ("dirty", "blocked")
KINDS = ("p0", "ruling", "report", "idle")
VERDICTS = ("clean", "red", "conflicting", "held")
MERGE_LABEL = "merge"
MIN_HEAD_AGE = timedelta(seconds=60)
LABELLABLE_STATES = ("clean", "behind", "has_hooks")
FAILED_CONCLUSIONS = ("failure", "timed_out", "cancelled", "action_required")
SUMMARY_LINES = 10
WINDOW_SECONDS = 3600
NO_PR = "-"
MESSAGE_PREFIX = "msg/"
QUEUE_BOT = "graphite-app[bot]"
LANDED = "landed"
CLOSED_WITHOUT_SQUASH = "closed-without-squash"
TERMINAL_STATES = frozenset({LANDED, CLOSED_WITHOUT_SQUASH})
HOLD_FIELDS = ("hold_reason", "hold_since", "hold_until")

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
BK_TIMESTAMP = re.compile(r"^_bk;t=\d+")
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
    "young": "head is {age}s old; the forge has not graded it, retry after {min}s",
    "status": "commit status {state} on {head}; only success is labelled",
    "checks": "failed check runs on {head}: {names}",
    "conflict": "{head} conflicts with {base} on {paths}; route the rebase, never label",
    "fetched": "refs/pull/{pr}/head is {fetched} on the forge, not {head}",
    "unapproved": "#{pr} has no approval in force: a reviewer's latest decision must be APPROVED, never dismissed or withdrawn, and mergeable_state is no proxy",
    "reviewing": "ai-review still reviewing {head}; retry once its latest run completes",
    "ai-review": "ai-review is {state} on {head}; only success is labelled, and `neutral` is a held blocking finding whose reason is a review comment on the diff",
    "children": "#{pr}'s branch {branch} is the base of {children}, which this stack does not enqueue; label the stack's tip, or retarget them to {trunk} BEFORE labelling, or the branch delete closes them unrecoverably",
    "orphaned": "#{pr} is based on {base}, which is neither {trunk} nor exactly one open pull request's branch (found {found}); retarget it to {trunk}",
    "cycle": "#{pr} is its own ancestor through {stack}; retarget the stack to its trunk",
    "untracked": "#{pr} is below #{tip} in the stack and no lane reported it; the label on #{tip} would enqueue it too",
    "stack": "the stack {stack} enqueues as one entry, so {refused} refuses all of it; nothing was labelled",
    "shallow": "{checkout} is a shallow clone; trunk traversal truncates at a depth that moves with each fetch. Run: git fetch --unshallow origin",
    "fetch": "fetching {ref} failed, so this pass has graded nothing: {detail}",
}


class Shell:
    """The single subprocess boundary: `gh`, `bk`, `git`, and `ccn` all pass through here."""

    def run(self, argv: list[str], stdin: str | None = None) -> str:
        proc = subprocess.run(argv, input=stdin, capture_output=True, text=True, check=True)
        return proc.stdout


class ForgeUnreachable(RuntimeError):
    """The forge did not answer, so this pass has graded nothing worth writing."""


class Refused(Exception):
    """One guard refused one pull request; the text names the reason."""


def refusal(kind: str, **values: object) -> Refused:
    return Refused(REFUSAL[kind].format(**values))


@dataclass
class Github:
    shell: Shell
    repo: str

    def api(self, path: str, **params: object) -> object:
        endpoint = f"repos/{self.repo}/{path}"
        if params:
            endpoint = f"{endpoint}?{urlencode(params)}"
        return json.loads(self.shell.run(["gh", "api", endpoint]))

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
        return json.loads(self.shell.run(["gh", "api", f"repos/{self.repo}"]))["default_branch"]


@dataclass
class Notes:
    shell: Shell
    ledger: str

    def rows(self) -> dict[str, dict[str, str]]:
        payload = json.loads(self.shell.run(["ccn", "ledger", "show", self.ledger, "--json"]))
        return {row["key"]: row["fields"] for row in payload["rows"]}

    def pr_rows(self) -> dict[str, dict[str, str]]:
        return {key: fields for key, fields in self.rows().items() if key.isdigit()}

    def messages(self) -> dict[str, dict[str, str]]:
        return {key: fields for key, fields in self.rows().items() if key.startswith(MESSAGE_PREFIX)}

    def sync(self, rows: list[dict]) -> None:
        self.shell.run(["ccn", "ledger", "sync", self.ledger, "--file", "-"], stdin=json.dumps(rows))

    def set_fields(self, key: str, fields: dict[str, str]) -> None:
        argv = ["ccn", "ledger", "row", "set", self.ledger, "--key", key]
        for name, value in fields.items():
            argv += ["--field", f"{name}={value}"]
        self.shell.run(argv)


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
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("w")
    fcntl.flock(handle, fcntl.LOCK_EX)
    try:
        yield
    finally:
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()


def default_lock(ledger: str) -> Path:
    return Path.home() / ".cache" / "ccn-ledger" / f"{ledger}.lock"


def is_shallow(shell: Shell, checkout: Path) -> bool:
    return shell.run(["git", "-C", str(checkout), "rev-parse", "--is-shallow-repository"]).strip() != "false"


def fetch(shell: Shell, checkout: Path, *refs: str) -> None:
    """Fetch, converting a failure into ForgeUnreachable.

    A concurrent fetch in another worktree loses the ref lock, and a pass that grades
    after a failed fetch grades the previous state while reporting it as current.
    """
    try:
        shell.run(["git", "-C", str(checkout), "fetch", "-q", "origin", *refs])
    except subprocess.CalledProcessError as error:
        raise ForgeUnreachable(REFUSAL["fetch"].format(ref=" ".join(refs), detail=(error.stderr or "").strip())) from error


def open_children(gh: Github, branch: str) -> list[str]:
    """Open pull requests whose base is this branch.

    Scoped to one branch we own, never a repository listing. A child left on a parent's
    branch is closed by the forge when that branch is deleted, and reopening is refused,
    so the retarget has to happen before the parent carries a label rather than in a
    race with its landing.
    """
    return [str(pull["number"]) for pull in gh.api("pulls", base=branch, state="open")]


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
        if e["event"] == "unlabeled" and e["label"]["name"] == MERGE_LABEL and e["actor"]["login"] == QUEUE_BOT
    ]
    if ejected and (not labelled or ejected[-1] > labelled[-1]):
        return ejected[-1]
    return ""


def ai_review(checks: dict) -> str:
    for run in checks["check_runs"]:
        if run["name"] == AI_REVIEW_CHECK:
            return run["conclusion"] or run["status"]
    return AI_REVIEW_ABSENT


def latest_ai_review(gh: Github, head: str) -> dict | None:
    runs = gh.api(f"commits/{head}/check-runs", check_name=AI_REVIEW_CHECK)["check_runs"]
    return max(runs, key=lambda run: run["started_at"], default=None)


def approvers(gh: Github, pr: str) -> list[str]:
    decisions = {review["user"]["login"]: review["state"] for review in gh.paged(f"pulls/{pr}/reviews") if review["state"] in REVIEW_DECISIONS}
    return sorted(login for login, state in decisions.items() if state == APPROVED)


def grade(gh: Github, number: str) -> dict[str, str]:
    """Re-read the head from the same `pulls/{n}` call the verdicts are graded against."""
    pull = gh.api(f"pulls/{number}")
    head = pull["head"]["sha"]
    status = gh.api(f"commits/{head}/status")
    labels = gh.api(f"issues/{number}/labels")
    checks = gh.api(f"commits/{head}/check-runs")
    return {
        "state": pull["state"],
        "head": head,
        "base": pull["base"]["ref"],
        "branch": pull["head"]["ref"],
        "author": pull["user"]["login"],
        "title": pull["title"],
        "test_state": status["state"],
        "mergeable_state": pull["mergeable_state"],
        "labels": ",".join(label["name"] for label in labels),
        "ai_review": ai_review(checks),
        "changed_files": str(pull["changed_files"]),
    }


def is_open(fields: dict[str, str]) -> bool:
    return fields.get("state", "open") == "open"


def needs_route(fields: dict[str, str]) -> bool:
    return is_open(fields) and (fields["test_state"] == "failure" or fields["mergeable_state"] in ROUTE_STATES)


def is_held(fields: dict[str, str]) -> bool:
    return bool(fields.get("hold_until"))


def carries_label(fields: dict[str, str]) -> bool:
    labelled_head = fields.get("label_head", "")
    on_head = bool(labelled_head) and labelled_head == fields.get("head") and bool(fields.get("labelled_at")) and not fields.get("label_pulled_at")
    return MERGE_LABEL in fields.get("labels", "").split(",") or on_head


def current_head(fields: dict[str, str]) -> str:
    return fields.get("head") or fields["reported_head"]


def buildkite_targets(status: dict, checks: dict) -> list[re.Match]:
    urls = [entry["target_url"] or "" for entry in status["statuses"] if entry["state"] == "failure"]
    urls += [run["details_url"] or "" for run in checks["check_runs"] if run["conclusion"] == "failure"]
    matches = [BUILDKITE_URL.match(url) for url in urls]
    return [match for match in matches if match]


def failed_job(shell: Shell, pipeline: str, build: str) -> str:
    payload = json.loads(shell.run(["bk", "build", "view", "-p", pipeline, build]))
    failed = [job for job in payload["jobs"] if job["state"] == "failed" and not job["soft_failed"]]
    return failed[0]["id"]


def first_error_line(log: str) -> str:
    for raw in log.splitlines():
        line = ANSI.sub("", BK_TIMESTAMP.sub("", raw)).strip()
        if not line or BK_SECTION.match(line) or BK_SOURCE_ECHO.match(line):
            continue
        lowered = line.lower()
        if any(noise in lowered for noise in WRAPPER_NOISE):
            continue
        if ERROR_LINE.search(line):
            return line
    return ""


def resolve_error(shell: Shell, target: re.Match) -> str:
    pipeline, build = target["pipeline"], target["build"]
    job = target["job"] or failed_job(shell, pipeline, build)
    return first_error_line(shell.run(["bk", "job", "log", "-p", pipeline, "-b", build, job]))


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
    error = resolve_error(shell, target)
    if not error:
        return NO_ERROR_TEXT.format(head=head[:9], url=url), "red with no error line"
    return FAILURE_TEXT.format(head=head[:9], url=url, error=error), "fix: " + error[:120]


def priority(item: tuple[str, dict[str, str]]) -> tuple[int, str]:
    key, fields = item
    return KINDS.index(fields["kind"]), key


def next_message_key(messages: dict[str, dict[str, str]]) -> str:
    seq = max((int(key[len(MESSAGE_PREFIX):]) for key in messages), default=0) + 1
    return f"{MESSAGE_PREFIX}{seq:06d}"


def duplicate(messages: dict[str, dict[str, str]], fields: dict[str, str]) -> str | None:
    identity = ("kind", "pr", "text") if fields["kind"] == "ruling" else ("kind", "pr", "head")
    wanted = tuple(fields[name] for name in identity)
    for key, existing in messages.items():
        if tuple(existing.get(name, "") for name in identity) == wanted:
            return key
    return None


def enqueue(notes: Notes, fields: dict[str, str]) -> str:
    messages = notes.messages()
    seen = duplicate(messages, fields)
    if seen:
        print(f"duplicate of {seen}; nothing recorded, nothing to answer")
        return seen
    key = next_message_key(messages)
    notes.set_fields(key, dict(fields, at=utc_stamp(), state="pending"))
    print(f"{key} {fields['kind']} #{fields['pr']} {fields['head'][:9]} from {fields['lane']}")
    return key


def message_line(key: str, fields: dict[str, str]) -> str:
    return f"{key} {fields['kind']} #{fields['pr']} {fields['head'][:9]} {fields['lane']}: {fields['text']}"


def ruling_line(fields: dict[str, str]) -> str:
    options = " / ".join(part.strip() for part in fields["options"].split("|"))
    return f"RULING NEEDED: {fields['text']}; options: {options}"


def hold_line(pr: str, fields: dict[str, str], moment: datetime) -> str:
    expired = " EXPIRED" if parse_iso(fields["hold_until"]) < moment else ""
    return f"held #{pr}: {fields['hold_reason']} until {fields['hold_until']}{expired}"


def route_message(pr: str, head: str, lane: str, job: str, verdict: str) -> str:
    body = f"DESK #{pr} {head[:9]}: {job}"
    if verdict:
        body += f"\n{verdict}"
    return f"to {lane}:\n{body}\n{ROUTE_TEXT}"


def landed_on_base(shell: Shell, gh: Github, checkout: Path, base: str, pr: str, head: str) -> tuple[str, str] | None:
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
    if is_shallow(shell, checkout):
        raise ForgeUnreachable(REFUSAL["shallow"].format(checkout=checkout))
    files = [row["filename"] for row in gh.api(f"pulls/{pr}/files?per_page=100")]
    if not files:
        return None
    tip = f"refs/desk/base/{base}"
    fetch(shell, checkout, f"+refs/heads/{base}:{tip}")
    fetch(shell, checkout, f"+refs/pull/{pr}/head:refs/desk/pr{pr}")
    if shell.run(git + ["diff", "--numstat", tip, head, "--"] + files).strip():
        return None
    delivered = shell.run(git + ["log", tip, "-1", "--format=%H %cI", "--"] + files).split()
    if not delivered:
        return None
    sha, landed_at = delivered[0], delivered[1]
    return sha, stamp(parse_iso(landed_at).astimezone(timezone.utc))


def merge_conflicts(shell: Shell, checkout: Path, pr: str, base: str, head: str) -> str | None:
    git = ["git", "-C", str(checkout)]
    shell.run(git + ["fetch", "-q", "origin", f"refs/pull/{pr}/head"])
    fetched = shell.run(git + ["rev-parse", "FETCH_HEAD"]).strip()
    if fetched != head:
        return REFUSAL["fetched"].format(pr=pr, fetched=fetched[:9], head=head[:9])
    shell.run(git + ["fetch", "-q", "origin", base])
    try:
        shell.run(git + ["merge-tree", "--write-tree", "FETCH_HEAD", head])
    except subprocess.CalledProcessError as failure:
        paths = sorted({line.split("\t")[-1] for line in failure.stdout.splitlines()[1:] if "\t" in line})
        return REFUSAL["conflict"].format(head=head[:9], base=base, paths=", ".join(paths) or "unlisted paths")
    return None


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


def summary_lines(rows: dict[str, dict[str, str]], moment: datetime, window: timedelta) -> list[str]:
    cutoff = moment - window
    prs = {key: fields for key, fields in rows.items() if key.isdigit()}
    landed = sorted((pr for pr, fields in prs.items() if fields.get("landed_at") and parse_iso(fields["landed_at"]) >= cutoff), key=int)
    open_rows = {pr: fields for pr, fields in prs.items() if is_open(fields)}
    labelled = sorted((pr for pr, fields in open_rows.items() if carries_label(fields)), key=int)
    holds = {pr: fields for pr, fields in open_rows.items() if is_held(fields)}
    routed = sorted((pr for pr, fields in open_rows.items() if fields.get("routed_head") and fields["routed_head"] == current_head(fields)), key=int)
    pending = [fields for key, fields in sorted(rows.items()) if key.startswith(MESSAGE_PREFIX) and fields["state"] == "pending"]
    rulings = [fields for fields in pending if fields["kind"] == "ruling"]
    p0s = [fields for fields in pending if fields["kind"] == "p0"]
    lines = [
        f"desk {stamp(moment)} | open {len(open_rows)} | merged/h {len(landed)} | labelled {len(labelled)} | held {len(holds)} | rulings {len(rulings)} | p0 {len(p0s)} | routed {len(routed)}"
    ]
    if landed:
        lines.append("merged: " + " ".join(f"#{pr}" for pr in landed))
    if labelled:
        lines.append("labelled: " + " ".join(f"#{pr}" for pr in labelled))
    lines += [f"P0 #{fields['pr']} {fields['lane']}: {fields['text']}" for fields in p0s]
    lines += [ruling_line(fields) for fields in rulings]
    lines += [hold_line(pr, holds[pr], moment) for pr in sorted(holds, key=int)]
    if routed:
        lines.append("routed, awaiting a new head: " + " ".join(f"#{pr}" for pr in routed))
    if len(lines) > SUMMARY_LINES:
        lines = lines[: SUMMARY_LINES - 1] + [f"... {len(lines) - SUMMARY_LINES + 1} more lines in ledger show"]
    return lines


def cmd_init(args: argparse.Namespace, shell: Shell) -> int:
    payload = json.loads(shell.run(["ccn", "ledger", "add", args.title, "--json"]))
    print(payload["id"])
    return 0


def cmd_report(args: argparse.Namespace, shell: Shell) -> int:
    notes = Notes(shell, args.ledger)
    enqueue(notes, {"kind": "report", "pr": args.pr, "head": args.head, "lane": args.lane, "text": f"{args.verdict} {args.text}".strip()})
    notes.set_fields(args.pr, {"lane": args.lane, "reported_head": args.head, "reported_verdict": args.verdict, "reported_at": utc_stamp()})
    return 0


def cmd_enqueue(args: argparse.Namespace, shell: Shell) -> int:
    enqueue(Notes(shell, args.ledger), {"kind": args.kind, "pr": args.pr, "head": args.head, "lane": args.lane, "text": args.text})
    return 0


def cmd_ruling(args: argparse.Namespace, shell: Shell) -> int:
    fields = {"kind": "ruling", "pr": args.pr, "head": NO_PR, "lane": args.lane, "text": args.text, "options": args.options}
    enqueue(Notes(shell, args.ledger), fields)
    print(ruling_line(fields))
    return 0


def cmd_inbox(args: argparse.Namespace, shell: Shell) -> int:
    notes = Notes(shell, args.ledger)
    messages = sorted(((key, fields) for key, fields in notes.messages().items() if args.all or fields["state"] == "pending"), key=priority)
    if args.json:
        print(json.dumps([dict(fields, key=key) for key, fields in messages]))
    else:
        for key, fields in messages:
            print(ruling_line(fields) if fields["kind"] == "ruling" else message_line(key, fields))
    if args.take:
        taken = utc_stamp()
        for key, fields in messages:
            if fields["state"] == "pending":
                notes.set_fields(key, {"state": "acked", "acked_at": taken})
    return 0


def cmd_ack(args: argparse.Namespace, shell: Shell) -> int:
    notes = Notes(shell, args.ledger)
    acked = utc_stamp()
    for key in args.keys:
        notes.set_fields(key, {"state": "acked", "acked_at": acked})
    return 0


def cmd_refresh(args: argparse.Namespace, shell: Shell) -> int:
    gh = Github(shell, args.repo)
    notes = Notes(shell, args.ledger)
    lanes = dict(pair.split("=", 1) for pair in args.lane)
    with locked(args.lock or default_lock(args.ledger)):
        known = notes.pr_rows()
        moment = utc_stamp()
        rows = []
        for key in sorted(set(known) | set(args.pr), key=int):
            try:
                fields = grade(gh, key)
            except subprocess.CalledProcessError as failure:
                raise ForgeUnreachable(f"#{key}: {failure.stderr.strip() or failure}") from failure
            fields["last_refresh"] = moment
            if key not in known:
                fields["first_seen"] = moment
            if key in lanes:
                fields["lane"] = lanes[key]
            rows.append({"key": key, "fields": fields})
        notes.sync(rows)
    print(f"refreshed {len(rows)} PRs into {args.ledger}")
    return 0


def cmd_hold(args: argparse.Namespace, shell: Shell) -> int:
    since = now()
    until = parse_iso(args.until) if args.until else since + timedelta(hours=args.hours)
    Notes(shell, args.ledger).set_fields(args.pr, {"hold_reason": args.reason, "hold_since": stamp(since), "hold_until": stamp(until)})
    print(f"held #{args.pr} until {stamp(until)}: {args.reason}")
    return 0


def cmd_lift(args: argparse.Namespace, shell: Shell) -> int:
    Notes(shell, args.ledger).set_fields(args.pr, {name: "" for name in HOLD_FIELDS})
    print(f"lifted #{args.pr} at {utc_stamp()}")
    return 0


def route_one(notes: Notes, gh: Github, pr: str, fields: dict[str, str], job: str | None, lane: str | None, dry_run: bool) -> bool:
    head = current_head(fields)
    lane = lane or fields["lane"]
    verdict = ""
    if not job:
        verdict, job = route_verdict(notes.shell, gh, fields)
    if fields.get("routed_head") == head and fields.get("routed_job") == job:
        print(f"#{pr} {head[:9]} already routed to {fields['routed_lane']} at {fields['routed_at']}; nothing to send")
        return False
    print(route_message(pr, head, lane, job, verdict))
    if dry_run:
        return False
    notes.set_fields(pr, {"routed_head": head, "routed_job": job, "routed_lane": lane, "routed_at": utc_stamp(), "next_action": job})
    return True


def cmd_route(args: argparse.Namespace, shell: Shell) -> int:
    notes = Notes(shell, args.ledger)
    gh = Github(shell, args.repo)
    rows = notes.pr_rows()
    if args.pr:
        route_one(notes, gh, args.pr, rows[args.pr], args.job, args.lane, args.dry_run)
        return 0
    routed = sum(route_one(notes, gh, pr, rows[pr], None, None, args.dry_run) for pr in sorted(rows, key=int) if needs_route(rows[pr]))
    print(f"routed {routed} rows" if not args.dry_run else "dry run, nothing written")
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


def guard(shell: Shell, gh: Github, pull: dict, fields: dict[str, str], expected: str | None, above: str | None, trunk: str, checkout: Path | None) -> list[str]:
    """Every per-PR guard, in order; returns the approvers or raises the first refusal."""
    pr = str(pull["number"])
    head, base = pull["head"]["sha"], pull["base"]["ref"]
    if pull["state"] != "open":
        raise refusal("closed", pr=pr, state=pull["state"], base=base)
    if expected and expected != head:
        raise refusal("moved", expected=expected[:9], head=head[:9])
    if is_held(fields):
        raise refusal("held", pr=pr, reason=fields["hold_reason"], until=fields["hold_until"])
    if fields.get("label_head") == head and fields.get("label_pulled_at"):
        raise refusal("pulled", head=head[:9], at=fields["label_pulled_at"], reason=fields["label_pull_reason"])
    if fields.get("label_head") == head and fields.get("labelled_at"):
        raise refusal("labelled", head=head[:9], at=fields["labelled_at"])
    approved = approvers(gh, pr)
    if not approved:
        raise refusal("unapproved", pr=pr)
    if pull["mergeable_state"] not in LABELLABLE_STATES:
        raise refusal("mergeable", state=pull["mergeable_state"], allowed="/".join(LABELLABLE_STATES))
    committed = parse_iso(gh.api(f"commits/{head}")["commit"]["committer"]["date"])
    age = now() - committed
    if age < MIN_HEAD_AGE:
        raise refusal("young", age=int(age.total_seconds()), min=int(MIN_HEAD_AGE.total_seconds()))
    status = gh.api(f"commits/{head}/status")
    if status["state"] != "success":
        raise refusal("status", state=status["state"], head=head[:9])
    checks = gh.api(f"commits/{head}/check-runs")
    failed = [run["name"] for run in checks["check_runs"] if run["conclusion"] in FAILED_CONCLUSIONS]
    if failed:
        raise refusal("checks", head=head[:9], names=", ".join(failed))
    review = latest_ai_review(gh, head)
    if review and review["status"] != "completed":
        raise refusal("reviewing", head=head[:9])
    verdict = review["conclusion"] if review else AI_REVIEW_ABSENT
    if verdict != "success":
        raise refusal("ai-review", state=verdict, head=head[:9])
    children = [child for child in open_children(gh, pull["head"]["ref"]) if child != above]
    if children:
        raise refusal("children", pr=pr, branch=pull["head"]["ref"], children=", ".join(f"#{c}" for c in children), trunk=trunk)
    if checkout:
        conflict = merge_conflicts(shell, checkout, pr, base, head)
        if conflict:
            raise Refused(conflict)
    return approved


def cmd_label(args: argparse.Namespace, shell: Shell) -> int:
    gh = Github(shell, args.repo)
    notes = Notes(shell, args.ledger)
    trunk = gh.default_branch()
    try:
        stack = stack_to_trunk(gh, gh.api(f"pulls/{args.pr}"), trunk)
    except Refused as refused:
        print(f"REFUSED {refused}")
        return 1
    numbers = [str(pull["number"]) for pull in stack]
    rows = notes.pr_rows()
    approved: dict[str, list[str]] = {}
    refused: list[str] = []
    for index, pull in enumerate(stack):
        pr = numbers[index]
        above = numbers[index + 1] if index + 1 < len(stack) else None
        try:
            if pr != args.pr and not rows.get(pr, {}).get("reported_head"):
                raise refusal("untracked", pr=pr, tip=args.pr)
            expected = args.expect_head if pr == args.pr else rows[pr].get("reported_head")
            approved[pr] = guard(shell, gh, pull, rows.get(pr, {}), expected, above, trunk, args.checkout)
        except Refused as reason:
            print(f"REFUSED {reason} (#{pr})")
            refused.append(pr)
    chain = " <- ".join(f"#{pr}" for pr in numbers)
    if refused:
        print(f"REFUSED {REFUSAL['stack'].format(stack=chain, refused=', '.join(f'#{pr}' for pr in refused))}")
        return 1
    tip = stack[-1]["head"]["sha"]
    if args.dry_run:
        print(f"would label #{args.pr} {tip}" + (f", enqueuing {chain}" if len(stack) > 1 else ""))
        return 0
    gh.add_label(args.pr, MERGE_LABEL)
    labelled = utc_stamp()
    print(f"labelled #{args.pr} {tip} at {labelled}; the queue takes {chain} as one entry")
    for pull, pr in zip(stack, numbers, strict=True):
        head, approved_by = pull["head"]["sha"], ",".join(approved[pr])
        notes.set_fields(pr, {"head": head, "base": pull["base"]["ref"], "label_head": head, "labelled_at": labelled, "label_stack": ",".join(numbers), "approved_by": approved_by, "label_pulled_at": "", "label_pull_reason": ""})
        print(f"#{pr} {head[:9]} approved by {approved_by}")
    return 0


def cmd_unlabel(args: argparse.Namespace, shell: Shell) -> int:
    gh = Github(shell, args.repo)
    head = gh.api(f"pulls/{args.pr}")["head"]["sha"]
    gh.remove_label(args.pr, MERGE_LABEL)
    pulled = utc_stamp()
    Notes(shell, args.ledger).set_fields(args.pr, {"label_head": head, "label_pulled_at": pulled, "label_pull_reason": args.reason})
    print(f"pulled {MERGE_LABEL} from #{args.pr} {head[:9]} at {pulled}; a pull is not a stop, the queue may already own this head")
    return 0


def squash_on_base(shell: Shell, checkout: Path, base: str, pr: str) -> str:
    """The squash the base log names for this PR, if there is one.

    Asked only when content cannot tell, which is whenever the base moved on one of the
    PR's files after the squash.

    Nothing the forge asserts about itself is admissible here. The closing actor proves
    nothing, because the queue's bot also closes a stacked child when its base branch is
    deleted, landing nothing. The queue's own `externally-merged` label proves nothing
    either: it is applied to OPEN pull requests whose content never reached the trunk —
    two carried it while their trees still differed and no commit named them. Only the
    trunk answers, through its tree or through a commit it names.
    """
    git = ["git", "-C", str(checkout)]
    log = shell.run(git + ["log", f"refs/desk/base/{base}", "--oneline", "-400", "--fixed-strings", f"--grep=(#{pr})"])
    return log.split(" ", 1)[0] if log.strip() else ""


def settle(shell: Shell, gh: Github, notes: Notes, checkout: Path, prs: list[str]) -> int:
    moved = 0
    for pr in prs:
        pull = gh.api(f"pulls/{pr}")
        if pull["state"] == "open":
            ejected = queue_ejected(gh, pr)
            if ejected:
                notes.set_fields(pr, {"ejected_at": ejected})
                print(f"#{pr} was EJECTED by the queue at {ejected} and still reads open; the label is gone exactly as a landing would leave it")
            continue
        base = pull["base"]["ref"]
        delivered = landed_on_base(shell, gh, checkout, base, pr, pull["head"]["sha"])
        if delivered:
            sha, landed_at = delivered
            notes.set_fields(pr, {"state": LANDED, "landed_sha": sha, "landed_at": landed_at, "base": base})
            print(f"landed #{pr}, payload delivered by {sha[:9]} on {base} at {landed_at}")
        elif (squash := squash_on_base(shell, checkout, base, pr)):
            notes.set_fields(pr, {"state": LANDED, "landed_sha": squash, "base": base})
            print(f"landed #{pr} as {squash[:9]} on {base}, which has moved on its files since")
        else:
            notes.set_fields(pr, {"state": CLOSED_WITHOUT_SQUASH, "base": base})
            print(f"#{pr} is {CLOSED_WITHOUT_SQUASH} on {base}: a human closed it and its payload is absent, so the row stays until its lane answers")
        moved += 1
    return moved


def cmd_landed(args: argparse.Namespace, shell: Shell) -> int:
    gh = Github(shell, args.repo)
    notes = Notes(shell, args.ledger)
    rows = notes.pr_rows()
    prs = [args.pr] if args.pr else [pr for pr in sorted(rows, key=int) if rows[pr].get("state") != LANDED]
    settle(shell, gh, notes, args.checkout, prs)
    return 0


def cmd_reconcile(args: argparse.Namespace, shell: Shell) -> int:
    notes = Notes(shell, args.ledger)
    rows = notes.pr_rows()
    open_rows = [pr for pr, fields in rows.items() if fields.get("state") not in TERMINAL_STATES]
    moved = settle(shell, Github(shell, args.repo), notes, args.checkout, sorted(open_rows, key=int))
    print(f"reconciled {len(open_rows)} non-terminal rows, {moved} moved")
    return 0


def cmd_summary(args: argparse.Namespace, shell: Shell) -> int:
    if args.repo and args.checkout:
        cmd_reconcile(args, shell)
    print("\n".join(summary_lines(Notes(shell, args.ledger).rows(), now(), timedelta(seconds=args.window_seconds))))
    return 0


def cmd_show(args: argparse.Namespace, shell: Shell) -> int:
    if args.json:
        sys.stdout.write(shell.run(["ccn", "ledger", "show", args.ledger, "--json"]))
        return 0
    rows = Notes(shell, args.ledger).pr_rows()
    if args.red:
        rows = {key: fields for key, fields in rows.items() if needs_route(fields)}
    print(render_table(rows))
    return 0


def add_ledger(parser: argparse.ArgumentParser, repo: bool = False) -> None:
    if repo:
        parser.add_argument("--repo", required=True)
    parser.add_argument("--ledger", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ledger.py", description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="create the ledger and print its id")
    init.add_argument("--title", required=True)
    init.set_defaults(handler=cmd_init)

    report = subparsers.add_parser("report", help="record a lane's 3-line ship report; the only way a PR row is opened by hand")
    add_ledger(report)
    report.add_argument("--pr", required=True)
    report.add_argument("--head", required=True)
    report.add_argument("--lane", required=True)
    report.add_argument("--verdict", required=True, choices=VERDICTS)
    report.add_argument("--text", default="")
    report.set_defaults(handler=cmd_report)

    enqueue_cmd = subparsers.add_parser("enqueue", help="record any lane message; duplicates by kind+PR+head are dropped")
    add_ledger(enqueue_cmd)
    enqueue_cmd.add_argument("--kind", required=True, choices=KINDS)
    enqueue_cmd.add_argument("--pr", required=True)
    enqueue_cmd.add_argument("--head", required=True)
    enqueue_cmd.add_argument("--lane", required=True)
    enqueue_cmd.add_argument("--text", required=True)
    enqueue_cmd.set_defaults(handler=cmd_enqueue)

    ruling = subparsers.add_parser("ruling", help="record a question only the root can answer and print the RULING NEEDED line")
    add_ledger(ruling)
    ruling.add_argument("--lane", required=True)
    ruling.add_argument("--text", required=True)
    ruling.add_argument("--options", required=True, metavar="A|B|C")
    ruling.add_argument("--pr", default=NO_PR)
    ruling.set_defaults(handler=cmd_ruling)

    inbox = subparsers.add_parser("inbox", help="pending messages, P0 first, then rulings, reports, idles")
    add_ledger(inbox)
    inbox.add_argument("--take", action="store_true")
    inbox.add_argument("--all", action="store_true")
    inbox.add_argument("--json", action="store_true")
    inbox.set_defaults(handler=cmd_inbox)

    ack = subparsers.add_parser("ack", help="mark messages handled")
    add_ledger(ack)
    ack.add_argument("keys", nargs="+")
    ack.set_defaults(handler=cmd_ack)

    refresh = subparsers.add_parser("refresh", help="regrade every PR the ledger holds, plus any --pr, and sync it")
    add_ledger(refresh, repo=True)
    refresh.add_argument("--pr", action="append", default=[], metavar="N", help="admit this PR, reported by one of our lanes")
    refresh.add_argument("--lane", action="append", default=[], metavar="PR=NAME")
    refresh.add_argument("--lock", type=Path)
    refresh.set_defaults(handler=cmd_refresh)

    hold = subparsers.add_parser("hold", help="hold a PR with a reason and an expiry")
    add_ledger(hold)
    hold.add_argument("--pr", required=True)
    hold.add_argument("--reason", required=True)
    expiry = hold.add_mutually_exclusive_group(required=True)
    expiry.add_argument("--until")
    expiry.add_argument("--hours", type=float)
    hold.set_defaults(handler=cmd_hold)

    lift = subparsers.add_parser("lift", help="lift a hold")
    add_ledger(lift)
    lift.add_argument("--pr", required=True)
    lift.set_defaults(handler=cmd_lift)

    route = subparsers.add_parser("route", help="print the message that sends a red or conflicting head to its lane, once per PR, head, and job")
    add_ledger(route, repo=True)
    route.add_argument("--pr")
    route.add_argument("--job", help="the failing job or blocker; read from the forge and Buildkite when omitted")
    route.add_argument("--lane")
    route.add_argument("--dry-run", action="store_true")
    route.set_defaults(handler=cmd_route)

    label = subparsers.add_parser("label", help="re-read every PR from the tip down to the trunk, run every guard on each, then label the tip once")
    add_ledger(label, repo=True)
    label.add_argument("--pr", required=True)
    label.add_argument("--expect-head")
    label.add_argument("--checkout", type=Path)
    label.add_argument("--dry-run", action="store_true")
    label.set_defaults(handler=cmd_label)

    unlabel = subparsers.add_parser("unlabel", help="pull the merge label and record why")
    add_ledger(unlabel, repo=True)
    unlabel.add_argument("--pr", required=True)
    unlabel.add_argument("--reason", required=True)
    unlabel.set_defaults(handler=cmd_unlabel)

    landed = subparsers.add_parser("landed", help="settle closed PRs by the squash on the base branch")
    add_ledger(landed, repo=True)
    landed.add_argument("--checkout", type=Path, required=True)
    landed.add_argument("--pr")
    landed.set_defaults(handler=cmd_landed)

    reconcile = subparsers.add_parser("reconcile", help="settle every non-terminal row against the trunk and the forge")
    add_ledger(reconcile, repo=True)
    reconcile.add_argument("--checkout", type=Path, required=True)
    reconcile.set_defaults(handler=cmd_reconcile)

    summary = subparsers.add_parser("summary", help="the hourly desk-to-root report, at most ten lines")
    add_ledger(summary)
    summary.add_argument("--repo")
    summary.add_argument("--checkout", type=Path)
    summary.add_argument("--window-seconds", type=int, default=WINDOW_SECONDS)
    summary.set_defaults(handler=cmd_summary)

    show = subparsers.add_parser("show", help="render the PR rows")
    add_ledger(show)
    show.add_argument("--red", action="store_true")
    show.add_argument("--json", action="store_true")
    show.set_defaults(handler=cmd_show)

    return parser


def main(argv: list[str] | None = None, shell: Shell | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args, shell or Shell())
    except ForgeUnreachable as unreachable:
        print(f"forge unreachable, wrote nothing: {unreachable}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
