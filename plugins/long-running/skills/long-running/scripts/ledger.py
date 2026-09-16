#!/usr/bin/env python3
"""Open-PR ledger over cc-notes — one row per open PR, REST only.

    ledger.py refresh --repo owner/name --ledger ID [--pr N]... [--lane PR=NAME]... [--lock PATH]
    ledger.py route   --repo owner/name --ledger ID [--dry-run]
    ledger.py line    --repo owner/name --ledger ID [--window-seconds N]
    ledger.py show    --ledger ID [--red] [--json]

STDLIB ONLY. Every GitHub read is a ``gh api`` subprocess against one PR the ledger
already names; the repository's PR list is never read and GraphQL is never called.
Buildkite logs come from the repo-pinned ``bk``; storage is ``ccn ledger``. All three
go through :class:`Shell`, the one seam tests replace.
"""

from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

MARKER = "ccn-ledger-route"
AI_REVIEW_CHECK = "ai-review"
AI_REVIEW_ABSENT = "absent"
PAGE_SIZE = 100
HOLD_SECONDS = 3600
LINE_WIDTH = 200
ROUTE_STATES = ("dirty", "blocked")

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
    ("head", "head"),
    ("base", "base"),
    ("test", "test_state"),
    ("mergeable", "mergeable_state"),
    ("ai-review", "ai_review"),
    ("files", "changed_files"),
    ("lane", "lane"),
    ("hold", "hold_reason"),
    ("next", "next_action"),
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


class Shell:
    """The single subprocess boundary: `gh`, `bk`, and `ccn` all pass through here."""

    def run(self, argv: list[str], stdin: str | None = None) -> str:
        proc = subprocess.run(argv, input=stdin, capture_output=True, text=True, check=True)
        return proc.stdout


@dataclass
class Github:
    shell: Shell
    repo: str

    def api(self, path: str, **params: object) -> object:
        endpoint = f"repos/{self.repo}/{path}"
        if params:
            endpoint = f"{endpoint}?{urlencode(params)}"
        return json.loads(self.shell.run(["gh", "api", endpoint]))

    def paged(self, path: str, **params: object):
        page = 1
        while True:
            batch = self.api(path, per_page=PAGE_SIZE, page=page, **params)
            yield from batch
            if len(batch) < PAGE_SIZE:
                return
            page += 1

    def post_comment(self, number: str, body: str) -> None:
        self.shell.run(
            ["gh", "api", f"repos/{self.repo}/issues/{number}/comments", "--method", "POST", "--input", "-"],
            stdin=json.dumps({"body": body}),
        )


@dataclass
class Notes:
    shell: Shell
    ledger: str

    def rows(self) -> dict[str, dict[str, str]]:
        payload = json.loads(self.shell.run(["ccn", "ledger", "show", self.ledger, "--json"]))
        return {row["key"]: row["fields"] for row in payload["rows"]}

    def sync(self, rows: list[dict]) -> None:
        self.shell.run(["ccn", "ledger", "sync", self.ledger, "--file", "-"], stdin=json.dumps(rows))

    def set_fields(self, key: str, fields: dict[str, str]) -> None:
        argv = ["ccn", "ledger", "row", "set", self.ledger, "--key", key]
        for name, value in fields.items():
            argv += ["--field", f"{name}={value}"]
        self.shell.run(argv)


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def ai_review(checks: dict) -> str:
    for run in checks["check_runs"]:
        if run["name"] == AI_REVIEW_CHECK:
            return run["conclusion"] or run["status"]
    return AI_REVIEW_ABSENT


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


def already_routed(gh: Github, number: str, marker: str) -> bool:
    return any(marker in comment["body"] for comment in gh.paged(f"issues/{number}/comments"))


def load_infra_adapter(spec: str | None):
    """Resolve ``path/to/module.py:callable`` into the callable `line` asks for applied state."""
    if not spec:
        return None
    path, _, name = spec.rpartition(":")
    module_spec = importlib.util.spec_from_file_location("ccn_ledger_infra", path)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return getattr(module, name)


def landed_since(rows: dict[str, dict[str, str]], cutoff: datetime) -> int:
    return sum(1 for fields in rows.values() if fields.get("landed_at") and parse_iso(fields["landed_at"]) >= cutoff)


def render_table(rows: dict[str, dict[str, str]]) -> str:
    records = []
    for key in sorted(rows, key=int, reverse=True):
        fields = dict(rows[key], key=f"#{key}")
        fields["head"] = fields["head"][:9]
        records.append([str(fields.get(source, "-") or "-")[:40] for _, source in SHOW_COLUMNS])
    headers = [label for label, _ in SHOW_COLUMNS]
    widths = [max(len(headers[i]), *(len(r[i]) for r in records)) if records else len(headers[i]) for i in range(len(headers))]
    out = ["  ".join(h.ljust(w) for h, w in zip(headers, widths))]
    out += ["  ".join(cell.ljust(w) for cell, w in zip(record, widths)) for record in records]
    return "\n".join(out)


def cmd_refresh(args: argparse.Namespace, shell: Shell) -> int:
    gh = Github(shell, args.repo)
    notes = Notes(shell, args.ledger)
    lanes = dict(pair.split("=", 1) for pair in args.lane)
    with locked(args.lock or default_lock(args.ledger)):
        known = notes.rows()
        stamp = utc_stamp()
        rows = []
        for key in sorted(set(known) | set(args.pr), key=int):
            fields = grade(gh, key)
            fields["last_refresh"] = stamp
            if key not in known:
                fields["first_seen"] = stamp
            if key in lanes:
                fields["lane"] = lanes[key]
            rows.append({"key": key, "fields": fields})
        notes.sync(rows)
    print(f"refreshed {len(rows)} PRs into {args.ledger}")
    return 0


def cmd_route(args: argparse.Namespace, shell: Shell) -> int:
    gh = Github(shell, args.repo)
    notes = Notes(shell, args.ledger)
    routed = 0
    rows = notes.rows()
    for key in sorted(rows, key=int):
        fields = rows[key]
        if not needs_route(fields):
            continue
        head = fields["head"]
        if fields.get("last_graded_head") == head:
            continue
        marker = f"<!-- {MARKER} {head} -->"
        if already_routed(gh, key, marker):
            continue
        text, action = route_verdict(shell, gh, fields)
        body = f"{marker}\n{text}"
        if args.dry_run:
            print(f"#{key} would post:\n{body}\n")
            continue
        gh.post_comment(key, body)
        notes.set_fields(key, {"next_action": action, "last_graded_head": head})
        routed += 1
    print(f"routed {routed} rows" if not args.dry_run else "dry run, nothing written")
    return 0


def cmd_line(args: argparse.Namespace, shell: Shell) -> int:
    rows = Notes(shell, args.ledger).rows()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=args.window_seconds)
    hold_cutoff = now - timedelta(seconds=HOLD_SECONDS)
    red = sum(1 for fields in rows.values() if needs_route(fields))
    held = [
        f"#{key} {fields.get('hold_reason') or 'UNREASONED'}"
        for key, fields in sorted(rows.items(), key=lambda item: int(item[0]))
        if fields.get("hold_since") and parse_iso(fields["hold_since"]) < hold_cutoff
    ]
    open_rows = sum(1 for fields in rows.values() if is_open(fields))
    parts = [f"merged/h {landed_since(rows, cutoff)}", f"open {open_rows}", f"red {red}"]
    adapter = load_infra_adapter(args.infra_adapter or os.environ.get("CCN_LEDGER_INFRA_ADAPTER"))
    if adapter:
        parts.append(adapter(repo=args.repo, rows=rows))
    parts.append("held " + ("; ".join(held) if held else "0"))
    print(" | ".join(parts)[:LINE_WIDTH])
    return 0


def cmd_show(args: argparse.Namespace, shell: Shell) -> int:
    if args.json:
        sys.stdout.write(shell.run(["ccn", "ledger", "show", args.ledger, "--json"]))
        return 0
    rows = Notes(shell, args.ledger).rows()
    if args.red:
        rows = {key: fields for key, fields in rows.items() if needs_route(fields)}
    print(render_table(rows))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ledger.py", description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    refresh = subparsers.add_parser("refresh", help="regrade every PR the ledger holds, plus any --pr, and sync it")
    refresh.add_argument("--repo", required=True)
    refresh.add_argument("--ledger", required=True)
    refresh.add_argument("--pr", action="append", default=[], metavar="N", help="admit this PR, reported by one of our lanes")
    refresh.add_argument("--lane", action="append", default=[], metavar="PR=NAME")
    refresh.add_argument("--lock", type=Path)
    refresh.set_defaults(handler=cmd_refresh)

    route = subparsers.add_parser("route", help="comment once on every red or conflicting row")
    route.add_argument("--repo", required=True)
    route.add_argument("--ledger", required=True)
    route.add_argument("--dry-run", action="store_true")
    route.set_defaults(handler=cmd_route)

    line = subparsers.add_parser("line", help="one-line hourly cadence summary")
    line.add_argument("--repo", required=True)
    line.add_argument("--ledger", required=True)
    line.add_argument("--window-seconds", type=int, default=HOLD_SECONDS)
    line.add_argument("--infra-adapter", metavar="PATH.py:CALLABLE")
    line.set_defaults(handler=cmd_line)

    show = subparsers.add_parser("show", help="render the ledger")
    show.add_argument("--ledger", required=True)
    show.add_argument("--red", action="store_true")
    show.add_argument("--json", action="store_true")
    show.set_defaults(handler=cmd_show)

    return parser


def main(argv: list[str] | None = None, shell: Shell | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args, shell or Shell())


if __name__ == "__main__":
    raise SystemExit(main())
