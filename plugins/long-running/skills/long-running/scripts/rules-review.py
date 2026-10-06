#!/usr/bin/env python3
"""The landing desk's pre-merge rules review: one codex verdict per PR head against the owner's durable rulings.

    rules-review.py sweep --repo owner/name --ledger ID --checkout DIR [--inbox FILE]... [--state DIR] [--parallel N]

Each sweep collects finished reviews, dispatches one detached gpt-6.1-sol xhigh review on the standard tier for every open PR head
without one, applies the root inbox's ``rules-override`` lines, posts each head's findings once as a PR review, and
mirrors each head's verdict into its ``rules-review`` commit status. Verdicts live on ``review/<pr>@<head>`` ledger
rows; ``ledger.py list`` marks a PR ``rules_blocked`` while its current head's review is pending or holds a finding no
override waives, and the landing runner holds blocked PRs. The commit status reads ``pending`` and ``failure`` in the
same two cases, so ``stack-enqueue`` refuses the head however it is called. A head with no review, or an errored one,
a diff over the review bound included, lands. Paths the checkout's ``.gitattributes`` marks ``linguist-generated``
leave the reviewed diff and its bound, and a ``GENERATED`` line names them.

An override is one root inbox line, ``R<n> rules-override #<pr> <ruling>[ <ruling>...] :: <reason>``, naming each
ruling id it waives for that PR on every head.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

import ledger

SCHEMA = Path(__file__).resolve().parent / "rules-review.schema.json"
DIFF_CHARS = 150_000
PARALLEL = 6
ATTEMPTS = 2
QUOTE_CHARS = 300
SHORT_ID = 7
ETHOS = "AGENTS.md"
TERMINAL = frozenset({"completed", "failed", "died", "no-run"})
OVERRIDE = re.compile(r"^(?:-\s+)?(?P<key>R\d+)\s+(?:\([^)]*\)\s+)?rules-override #(?P<pr>\d+) (?P<rulings>\S.*?) :: (?P<reason>\S.*)$")
HEX = re.compile(r"^[0-9a-f]+$")
DIFF_MEDIA = "Accept: application/vnd.github.v3.diff"
FILE_SECTION = re.compile(r"^(?=diff --git )", re.M)
FILE_HEADER = re.compile(r"^diff --git a/.+ b/(?P<path>.+)$")
GENERATED_ATTR = "linguist-generated"
GENERATED_VALUES = frozenset({"set", "true"})
STATUS_CONTEXT = "rules-review"
STATUS_DESCRIPTION_CHARS = 140

PROMPT = """You are the pre-merge rules reviewer for pull request #{pr} at head {head} in {repo}. The landing desk enqueues this PR only after your verdict.

Decide whether the diff at the end of this message breaks any of the owner's durable rulings or any rule in the repository's AGENTS.md. Report a finding only when an added or changed line does what a ruling forbids, or undoes what a ruling requires. A diff that merely touches a ruling's subject is not a finding. Style, test coverage, and defects no ruling covers are not findings.

Each finding names:
- ruling: the ruling's id exactly as its heading shows it (seven hex characters), or AGENTS.md:<line> for a line of AGENTS.md
- cite: path:line of the offending line in the PR's new tree
- sentence: one sentence naming what the diff does and which rule it breaks
- quote: the ruling's own words the finding rests on, verbatim, at most 300 characters

Return verdict "clean" with an empty findings list when nothing breaks a ruling. Everything you need is in this message; run no commands and open no files.

# Durable rulings

{rulings}

# AGENTS.md, line-numbered

{ethos}

# Diff of #{pr} at {head}

```diff
{diff}
```
"""

COMMENT_HEAD = (
    "Rules review of `{head}`. Rulings violated: {count}. The landing desk holds this PR. Fix each finding on a new head or ask the root to override its ruling."
)
MARKER = "<!-- rules-review {head} -->"
COMMENT_FINDING = "**{ruling}** at `{cite}`: {sentence}\n\n> {quote}"


class Shell(ledger.Shell):
    def dispatch(self, run_dir: Path, question: str) -> str:
        argv = ["codex-ask", "-m", "sol", "--schema", str(SCHEMA), "-s", str(run_dir), "--dispatch", "-"]
        return subprocess.run(argv, input=question, cwd=run_dir, capture_output=True, text=True, check=True).stdout


def short(sha: str) -> str:
    return sha[:9]


def durable_rulings(shell: Shell, checkout: Path) -> dict[str, dict]:
    answers = json.loads(shell.run(["ccn", "-R", str(checkout), "answer", "list", "--label", "scope:durable", "--json"]))
    return {answer["id"]: answer for answer in answers}


def rulings_text(rulings: dict[str, dict]) -> str:
    return "\n\n".join(f"## {key[:SHORT_ID]} {answer['title']}\n{answer['body']}" for key, answer in sorted(rulings.items()))


def ethos_text(checkout: Path) -> str:
    return "\n".join(f"{ETHOS}:{number}: {line}" for number, line in enumerate((checkout / ETHOS).read_text().splitlines(), 1))


def resolve(ruling: str, rulings: dict[str, dict]) -> str:
    token = ruling.strip()
    if token.startswith(ETHOS) or len(token) < SHORT_ID:
        return token
    matches = [key for key in rulings if key.startswith(token)]
    return matches[0] if len(matches) == 1 else token


def overrides(paths: list[Path]) -> dict[str, list[tuple[str, frozenset[str]]]]:
    found: dict[str, list[tuple[str, frozenset[str]]]] = {}
    for path in paths:
        for line in path.read_text().splitlines():
            if match := OVERRIDE.match(line.strip()):
                found.setdefault(match["pr"], []).append((match["key"], frozenset(match["rulings"].split())))
    return found


def waives(token: str, ruling: str) -> bool:
    return token == ruling or (len(token) >= SHORT_ID and bool(HEX.match(token)) and ruling.startswith(token))


def overridden_by(review: dict[str, str], lines: list[tuple[str, frozenset[str]]]) -> list[str]:
    if review["verdict"] != "findings":
        return []
    needed = {finding["ruling"] for finding in json.loads(review["findings"])}
    keys = [key for key, tokens in lines if any(waives(token, ruling) for token in tokens for ruling in needed)]
    covered = {ruling for ruling in needed if any(waives(token, ruling) for key, tokens in lines for token in tokens)}
    return keys if covered == needed else []


def pr_diff(shell: Shell, repo: str, base: str, head: str) -> str:
    return shell.run(["gh", "api", f"repos/{repo}/compare/{base}...{head}", "-H", DIFF_MEDIA])


def section_path(section: str) -> str | None:
    match = FILE_HEADER.match(section.split("\n", 1)[0])
    return match["path"] if match else None


def generated_paths(shell: Shell, paths: list[str]) -> frozenset[str]:
    if not paths:
        return frozenset()
    fields = shell.run(["git", "check-attr", "-z", GENERATED_ATTR, "--", *paths]).split("\0")
    return frozenset(fields[index] for index in range(0, len(fields) - 2, 3) if fields[index + 2] in GENERATED_VALUES)


def without_generated(shell: Shell, diff: str) -> tuple[str, list[str]]:
    sections = [(section_path(section), section) for section in FILE_SECTION.split(diff)]
    generated = generated_paths(shell, [path for path, _ in sections if path])
    return "".join(section for path, section in sections if path not in generated), sorted(generated)


def question(repo: str, pr: str, head: str, rulings: str, ethos: str, diff: str) -> str:
    return PROMPT.format(pr=pr, head=head, repo=repo, rulings=rulings, ethos=ethos, diff=diff)


def collect(shell: Shell, run_dir: str) -> dict:
    return json.loads(shell.run(["codex-ask", "--collect", run_dir]).splitlines()[0])


def verdict_fields(record: dict, rulings: dict[str, dict]) -> dict[str, str]:
    if record["state"] != "completed":
        return {"verdict": "error", "error": f"codex run {record['state']}"}
    reply = json.loads(Path(record["reply_file"]).read_text())
    findings = [{**finding, "ruling": resolve(finding["ruling"], rulings)} for finding in reply["findings"]]
    if (reply["verdict"] == "clean") == bool(findings):
        return {"verdict": "error", "error": f"codex replied {reply['verdict']} with {len(findings)} findings"}
    return {"verdict": reply["verdict"], "findings": json.dumps(findings)}


def comment_body(head: str, findings: list[dict], rulings: dict[str, dict]) -> str:
    blocks = [
        COMMENT_FINDING.format(
            ruling=finding["ruling"][:SHORT_ID] if finding["ruling"] in rulings else finding["ruling"],
            cite=finding["cite"],
            sentence=finding["sentence"],
            quote=finding["quote"][:QUOTE_CHARS].replace("\n", "\n> "),
        )
        for finding in findings
    ]
    return "\n\n".join([COMMENT_HEAD.format(head=short(head), count=len({finding["ruling"] for finding in findings})), *blocks, MARKER.format(head=head)])


def commit_status(review: dict[str, str]) -> tuple[str, str]:
    if review["verdict"] == "pending":
        return "pending", "rules review in progress; the head waits for its verdict"
    if review["verdict"] == "clean":
        return "success", "no ruling violated"
    if review["verdict"] == "error":
        return "success", f"not reviewed, so not held: {review['error']}"[:STATUS_DESCRIPTION_CHARS]
    rulings = ",".join(sorted({finding["ruling"][:SHORT_ID] for finding in json.loads(review["findings"])}))
    if review.get("override"):
        return "success", f"findings on {rulings} waived by {review['override']}"[:STATUS_DESCRIPTION_CHARS]
    return "failure", f"rulings violated: {rulings}; a new head fixes them or a root rules-override waives them"[:STATUS_DESCRIPTION_CHARS]


def sites(findings: list[dict]) -> frozenset[tuple[str, str]]:
    return frozenset((finding["ruling"], finding["cite"].split(":", 1)[0]) for finding in findings)


def posted_sites(rows: dict[str, dict[str, str]], pr: str) -> frozenset[tuple[str, str]]:
    posted = [fields for key, fields in rows.items() if key.startswith(f"{ledger.REVIEW_PREFIX}{pr}@") and fields.get("comment") == "posted"]
    return frozenset().union(*(sites(json.loads(fields["findings"])) for fields in posted))


class Sweep:
    def __init__(self, shell: Shell, args: argparse.Namespace):
        self.shell = shell
        self.repo = args.repo
        self.checkout = Path(args.checkout).expanduser().resolve()
        self.notes = ledger.Notes(shell, args.ledger)
        self.lock = ledger.default_lock(args.ledger).with_name(f"{args.ledger}.rules-review.lock")
        self.state = Path(args.state).expanduser().resolve() if args.state else Path.home() / ".cache" / "long-running" / "rules-review" / args.ledger
        self.parallel = args.parallel
        self.overrides = overrides([Path(path).expanduser().resolve() for path in args.inbox])
        self.rulings: dict[str, dict] = {}
        self.context = ""
        self.rows: dict[str, dict[str, str]] = {}

    def set(self, key: str, fields: dict[str, str]) -> None:
        self.notes.set_fields(key, fields)
        self.rows[key] = {**self.rows.get(key, {}), **fields}

    def ensure_rulings(self) -> dict[str, dict]:
        if not self.rulings:
            self.rulings = durable_rulings(self.shell, self.checkout)
        return self.rulings

    def ensure_context(self) -> tuple[str, str]:
        if not self.context:
            self.context = rulings_text(self.ensure_rulings())
        return self.context, ethos_text(self.checkout)

    def run(self) -> None:
        with ledger.locked(self.lock):
            self.rows = self.notes.rows()
            self.collect_finished()
            self.dispatch_missing()
            self.apply_overrides()
            self.post_findings()
            self.post_statuses()

    def open_heads(self) -> list[tuple[str, str, dict[str, str]]]:
        heads = [(pr, ledger.current_head(fields), fields) for pr, fields in self.rows.items() if pr.isdigit() and ledger.is_open(fields)]
        return sorted(((pr, head, fields) for pr, head, fields in heads if head), key=lambda item: int(item[0]))

    def reviews(self, verdict: str) -> list[tuple[str, dict[str, str]]]:
        return [(key, fields) for key, fields in self.rows.items() if key.startswith(ledger.REVIEW_PREFIX) and fields.get("verdict") == verdict]

    def collect_finished(self) -> None:
        for key, review in self.reviews("pending"):
            record = collect(self.shell, review["run"])
            if record["state"] not in TERMINAL:
                continue
            fields = verdict_fields(record, self.ensure_rulings())
            self.set(key, {**fields, "override": "", "reviewed_at": ledger.utc_stamp()})
            print(self.verdict_line(review["pr"], review["head"], self.rows[key]))

    def verdict_line(self, pr: str, head: str, review: dict[str, str]) -> str:
        if review["verdict"] == "clean":
            return f"CLEAN #{pr} {short(head)}"
        if review["verdict"] == "error":
            return f"REVIEW-ERROR #{pr} {short(head)} attempt {review['attempt']}: {review['error']}"
        findings = json.loads(review["findings"])
        rulings = ",".join(sorted({finding["ruling"][:SHORT_ID] for finding in findings}))
        return f"RULES #{pr} {short(head)} {rulings}: {findings[0]['sentence']}"

    def due(self, pr: str, head: str) -> int:
        review = self.rows.get(ledger.review_key(pr, head))
        if review is None:
            return 1
        attempt = int(review.get("attempt", "1"))
        return attempt + 1 if review["verdict"] == "error" and attempt < ATTEMPTS else 0

    def dispatch_missing(self) -> None:
        running = len(self.reviews("pending"))
        for pr, head, fields in self.open_heads():
            if running >= self.parallel:
                return
            if not fields.get("base") or not (attempt := self.due(pr, head)):
                continue
            rulings, ethos = self.ensure_context()
            run_dir = self.state / f"{pr}-{head[:12]}-{attempt}"
            run_dir.mkdir(parents=True, exist_ok=True)
            try:
                diff, generated = without_generated(self.shell, pr_diff(self.shell, self.repo, fields["base"], head))
                if generated:
                    print(f"GENERATED #{pr} {short(head)} {len(generated)} paths left out of the review: {' '.join(generated)}")
                if len(diff) > DIFF_CHARS:
                    self.fail(pr, head, attempt, f"the diff is {len(diff)} characters, over the {DIFF_CHARS}-character review bound")
                    continue
                self.shell.dispatch(run_dir, question(self.repo, pr, head, rulings, ethos, diff))
            except subprocess.CalledProcessError as failed:
                error = (failed.stderr or failed.stdout or str(failed)).strip().splitlines()[-1:] or [str(failed)]
                self.fail(pr, head, attempt, error[0][:300])
                continue
            self.set(ledger.review_key(pr, head), {"pr": pr, "head": head, "verdict": "pending", "attempt": str(attempt), "run": str(run_dir), "started_at": ledger.utc_stamp()})
            running += 1
            print(f"REVIEWING #{pr} {short(head)}")

    def fail(self, pr: str, head: str, attempt: int, error: str) -> None:
        key = ledger.review_key(pr, head)
        self.set(key, {"pr": pr, "head": head, "verdict": "error", "attempt": str(attempt), "error": error, "override": "", "reviewed_at": ledger.utc_stamp()})
        print(self.verdict_line(pr, head, self.rows[key]))

    def apply_overrides(self) -> None:
        for pr, head, _ in self.open_heads():
            key = ledger.review_key(pr, head)
            review = self.rows.get(key)
            if review is None or review.get("override") or review["verdict"] == "clean":
                continue
            if keys := overridden_by(review, self.overrides.get(pr, [])):
                self.set(key, {"override": ",".join(keys)})
                print(f"OVERRIDDEN #{pr} {short(head)} by {','.join(keys)}")

    def post_findings(self) -> None:
        for pr, head, _ in self.open_heads():
            key = ledger.review_key(pr, head)
            review = self.rows.get(key)
            if review is None or review["verdict"] != "findings" or review.get("comment"):
                continue
            findings = json.loads(review["findings"])
            if review.get("override"):
                self.set(key, {"comment": "skipped-overridden"})
                continue
            if sites(findings) <= posted_sites(self.rows, pr):
                self.set(key, {"comment": "skipped-repeat"})
                continue
            try:
                if not self.already_posted(pr, head):
                    body = comment_body(head, findings, self.ensure_rulings())
                    self.shell.run(["gh", "api", f"repos/{self.repo}/pulls/{pr}/reviews", "--method", "POST", "--input", "-"], stdin=json.dumps({"commit_id": head, "event": "COMMENT", "body": body}))
            except subprocess.CalledProcessError as failed:
                self.set(key, {"comment_error": (failed.stderr or str(failed)).strip()[:200]})
                continue
            self.set(key, {"comment": "posted", "comment_error": ""})

    def post_statuses(self) -> None:
        for pr, head, _ in self.open_heads():
            key = ledger.review_key(pr, head)
            review = self.rows.get(key)
            if review is None:
                continue
            state, description = commit_status(review)
            if review.get("status") == state and review.get("status_description") == description:
                continue
            payload = {"state": state, "context": STATUS_CONTEXT, "description": description}
            try:
                self.shell.run(["gh", "api", f"repos/{self.repo}/statuses/{head}", "--method", "POST", "--input", "-"], stdin=json.dumps(payload))
            except subprocess.CalledProcessError as failed:
                self.set(key, {"status_error": (failed.stderr or str(failed)).strip()[:200]})
                continue
            self.set(key, {"status": state, "status_description": description, "status_error": ""})

    def already_posted(self, pr: str, head: str) -> bool:
        pages = json.loads(self.shell.run(["gh", "api", "--paginate", "--slurp", f"repos/{self.repo}/pulls/{pr}/reviews?per_page=100"]))
        marker = MARKER.format(head=head)
        return any(marker in (review.get("body") or "") for page in pages for review in page)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rules-review.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(required=True)
    sweep = subparsers.add_parser("sweep", help="collect, dispatch, override, and post one pass of rules reviews")
    sweep.add_argument("--repo", required=True)
    sweep.add_argument("--ledger", required=True)
    sweep.add_argument("--checkout", required=True)
    sweep.add_argument("--inbox", action="append", default=[], help="root inbox file carrying rules-override lines (repeatable)")
    sweep.add_argument("--state", help="run directory root (default ~/.cache/long-running/rules-review/<ledger>)")
    sweep.add_argument("--parallel", type=int, default=PARALLEL, help="most reviews running at once")
    return parser


def main(argv: list[str] | None = None, shell: Shell | None = None) -> int:
    sweep = Sweep(shell or Shell(), build_parser().parse_args(argv))
    os.chdir(sweep.checkout)
    sweep.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
