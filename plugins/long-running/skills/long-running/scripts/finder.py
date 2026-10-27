#!/usr/bin/env python3
"""The finder desk's job: one codex finder pass over one drive head, posted as the `finder:` record a lane would post.

    finder.py DIR

STDLIB ONLY. desk-runner.py's orca desk writes `DIR/job.json` for each head it dispatches and starts
this script detached. The job reads the PR through ccx's gateway, retrying a failed read once after
`RETRY_SECONDS`, and skips a closed PR or a head that is no longer the PR's live head. It reads the
PR's own commits as the head's commits no commit on the base branch's current tip matches by patch,
so a parent restacked onto a newer trunk adds nothing. A head after one an earlier finder record
graded is a fixed head: it reads only its own commits the graded head lacks by patch, runs no row
pass, and posts nothing when there are none. A whole pass writes the head's
required register rows to `DIR/rows.md` with `rulings.py slice --required`. It fills
`reference/finder-desk-brief.md` and runs `codex-ask -m <model> --schema findings` in the drive's
checkout. A reply that names no verdict for a row key the slice printed, or gives no reasoning in
`swept`, is asked once more; a second such reply fails the job. A head that moved while codex read
it posts nothing. The verdict is posted under lane `finder-desk` as `finder: ok <head> …` or
`finder: major <head> <n> open: …`, kind `review`, with `--pr <repo>#<n>`,
`--id finder:<repo>#<n>:<head>`, and the whole reply on `--path`, to the lane that posted the head
and every `finder.to` address. `DIR/result.json` is written last, `{"error": …}` when the job
raised, and the runner settles the dispatch from it.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

import drive
import ghread
from grades import names_head, repo_prs

SCRIPTS = Path(__file__).resolve().parent
BRIEF = SCRIPTS.parent / "reference" / "finder-brief.md"
TEMPLATE = SCRIPTS.parent / "reference" / "finder-desk-brief.md"
ROWS_STEP = SCRIPTS.parent / "reference" / "finder-desk-rows.md"
SCOPED_STEP = (
    "2. This head fixes one an earlier finder record graded, so it gets no row pass. Name an owner row\n"
    "   only when a hunk this pass reads breaks it, as one major finding whose defect starts\n"
    '   "row <key> missing:".'
)
LANE = "finder-desk"
JOB = "job.json"
RESULT = "result.json"
LOG = "job.log"
HEAD_SHA = re.compile(r"(?<!was )\b[0-9a-f]{40}\b")
FULL_SHA = re.compile(r"\b[0-9a-f]{40}\b")
ROW_KEY = re.compile(r"^### (\S+) \|", re.MULTILINE)
REPLY_FILE = re.compile(r"^REPLY_FILE: (\S+)$", re.MULTILINE)
MAJORS = frozenset({"blocker", "major"})
LISTS = re.compile(r"\b(rows met|n/a|pending|tests):")
REASON_WORDS = 12
REASONLESS = "no reasoning in `swept`, which gives one sentence per group of related hunks saying what it changes and why it holds or fails"
CCI_TEXT = 400
READ_SECONDS = 120
RETRY_SECONDS = 30
SLICE_SECONDS = 900
FINDER_SECONDS = 5400


class JobFailed(RuntimeError):
    pass


class Shell:
    def run(self, argv: list[str], cwd: Path | None = None, timeout: int = READ_SECONDS) -> str:
        return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, check=True, timeout=timeout).stdout


def heads(record: dict, repo: str) -> list[tuple[str, str]]:
    """Each `(pr, head)` a `head` record names: its `commit` field, else the first full sha of its text that no `was` precedes, for each PR its refs name in `repo`."""
    head = (record.get("fields") or {}).get("commit") or next(iter(HEAD_SHA.findall(record["text"])), "")
    return [(pr, head) for pr, _ in repo_prs(record, repo)] if FULL_SHA.fullmatch(head) else []


def since(reviews: list[dict], records: list[dict], head: str) -> tuple[str, dict | None]:
    """The full head the PR's newest finder record at another head read, empty when no `head` record spells it out, and that record."""
    prior = max((review for review in reviews if not names_head(review["head"], head)), key=lambda review: review["seq"], default=None)
    if prior is None:
        return "", None
    spelled = [prior["head"], *(sha for record in records if record["kind"] == "head" for sha in [(record.get("fields") or {}).get("commit", ""), *FULL_SHA.findall(record["text"])])]
    full = next((sha for sha in spelled if FULL_SHA.fullmatch(sha) and sha.startswith(prior["head"])), "")
    return full, {"seq": prior["seq"], "lane": prior["lane"], "head": prior["head"], "text": prior["text"]}


def scope(job: dict, view: dict, own: list[str], last: str, new: list[str]) -> str:
    whole = f"`git diff {own[0]}^ {job['head']}`"
    if not last:
        return f"This pass reads the PR's whole change: {whole}."
    return (
        f"The finder last graded {last}. This pass reads only the commits this head carries that neither "
        f"{view['baseRefName']} nor {last[:12]} carries by patch, each with `git show <sha>`: {' '.join(new)}. "
        f"The PR's whole change, for context only, is {whole}."
    )


def prior_text(job: dict) -> str:
    if not (prior := job["prior"]):
        return ""
    return (
        f"The PR's last finder record, cci #{prior['seq']} from {prior['lane']} at {prior['head']}, said: {prior['text']}\n"
        "Read each major it names against this head: one the head fixes, or one the PR body rebuts in a way the code "
        "bears out, is no longer open; any other is a finding again.\n\n"
    )


def prompt(job: dict, view: dict, base: str, own: list[str], last: str, new: list[str], rows: Path | None, plan: str) -> str:
    return TEMPLATE.read_text().format(
        repo=job["repo"],
        pr=job["pr"],
        head=job["head"],
        base=view["baseRefName"],
        base_oid=base,
        checkout=job["checkout"],
        fetch=f"{job['head']} {base}",
        scope=scope(job, view, own, last, new),
        prior=prior_text(job),
        rows_step=ROWS_STEP.read_text().format(rows=rows).rstrip() if rows else SCOPED_STEP,
        plan=plan or "none; the register names no plan",
        body=view["body"].strip() or "(empty)",
    )


def record_text(job: dict, view: dict, last: str, findings: list[dict]) -> str:
    head = job["head"]
    read = f"{last[:7]}..{head[:7]}" if last else f"{view['baseRefName']}...{head[:7]}"
    if not (majors := [finding for finding in findings if finding["severity"] in MAJORS]):
        return f"finder: ok {head} {job['model']} finder over {read}: no majors"
    opened = "; ".join(" ".join(f"{finding['cite']} {finding['defect']}".split()) for finding in majors)
    return f"finder: major {head} {len(majors)} open: {opened}"


def rulings(job: dict, verb: str, *argv: str) -> list[str]:
    return [sys.executable, str(SCRIPTS / "rulings.py"), verb, "--repo", job["checkout"], "--drive", job["drive"], *argv]


def ask(shell: Shell, job: dict, directory: Path, question: Path, attempt: int) -> str:
    out = shell.run(["codex-ask", "-m", job["model"], "--schema", "findings", "-s", str(directory / f"codex-{attempt}"), str(question)], cwd=Path(job["checkout"]), timeout=FINDER_SECONDS)
    return Path(REPLY_FILE.search(out)[1]).read_text()


def unanswered(keys: list[str], reply: str) -> list[str]:
    return [key for key in keys if key not in reply]


def reasoned(reply: str) -> bool:
    swept = json.loads(reply)["swept"]
    return len(swept[: (lists.start() if (lists := LISTS.search(swept)) else len(swept))].split()) >= REASON_WORDS


def shortfalls(keys: list[str], reply: str) -> list[str]:
    missing = unanswered(keys, reply)
    return ([f"no verdict for {', '.join(missing)}"] if missing else []) + ([] if reasoned(reply) else [REASONLESS])


def view_pr(shell: Shell, job: dict) -> dict:
    argv = ghread.read("pr", "view", job["pr"], "--repo", job["repo"], "--json", "state,baseRefName,headRefOid,body", max_age=ghread.FRESH)
    try:
        return json.loads(shell.run(argv))
    except subprocess.CalledProcessError:
        time.sleep(RETRY_SECONDS)
        return json.loads(shell.run(argv))


def git(shell: Shell, job: dict, *argv: str) -> str:
    return shell.run(["git", *argv], cwd=Path(job["checkout"]), timeout=SLICE_SECONDS).strip()


def beyond(shell: Shell, job: dict, other: str, head: str) -> list[str]:
    """The commits `head` carries, oldest first, that no commit since its merge-base with `other` matches by patch."""
    return git(shell, job, "rev-list", "--cherry-pick", "--right-only", "--no-merges", "--reverse", f"{other}...{head}").split()


def resolve(shell: Shell, job: dict, named: str) -> str:
    try:
        return git(shell, job, "rev-parse", "--verify", "--quiet", f"{named}^{{commit}}")
    except subprocess.CalledProcessError:
        return ""


def graded(shell: Shell, job: dict) -> str:
    """The full head the PR's last finder record graded, from the checkout or fetched when spelled whole, empty when neither has it."""
    if not (named := job["since"] or (job["prior"] or {}).get("head", "")):
        return ""
    if (full := resolve(shell, job, named)) or not FULL_SHA.fullmatch(named):
        return full
    try:
        git(shell, job, "fetch", "--no-write-fetch-head", "-q", "origin", named)
    except subprocess.CalledProcessError:
        return ""
    return resolve(shell, job, named)


def post(shell: Shell, job: dict, text: str, body: Path) -> str:
    to = ",".join(dict.fromkeys(filter(None, [job["lane"], *job["to"]])))
    argv = ["cci", "post", "--drive", job["drive"], "--lane", LANE, "--kind", "review", "--pr", f"{job['repo']}#{job['pr']}", "--id", f"finder:{job['repo']}#{job['pr']}:{job['head']}", "--path", str(body)]
    clipped = text if len(text) <= CCI_TEXT else text[: CCI_TEXT - 1] + "…"
    return shell.run([*argv, *(["--to", to] if to else []), "--text", clipped]).strip()


def run(directory: Path, shell: Shell) -> dict:
    job = json.loads((directory / JOB).read_text())
    pr, head = f"{job['repo']}#{job['pr']}", job["head"]
    view = view_pr(shell, job)
    if view["state"] == "CLOSED":
        return {"skipped": f"{pr} is closed"}
    if view["headRefOid"] != head:
        return {"skipped": f"{pr} moved to {view['headRefOid'][:12]}"}
    base = git(shell, job, "ls-remote", "origin", f"refs/heads/{view['baseRefName']}").split()[0]
    git(shell, job, "fetch", "--no-write-fetch-head", "-q", "origin", head, base)
    last = graded(shell, job)
    if not (own := beyond(shell, job, base, head)):
        return {"skipped": f"{pr} at {head[:12]} carries no commit {view['baseRefName']} lacks"}
    new = [sha for sha in own if sha in set(beyond(shell, job, last, head))] if last else []
    if last and not new:
        return {"skipped": f"{pr} at {head[:12]} carries no commit {last[:12]} lacks, which cci #{job['prior']['seq']} graded"}
    rows = None if last else directory / "rows.md"
    keys: list[str] = []
    if rows:
        rows.write_text(shell.run(rulings(job, "slice", "--required", "--pr", pr, "--head", head, "--base", view["baseRefName"]), timeout=SLICE_SECONDS))
        keys = ROW_KEY.findall(rows.read_text())
    question = directory / "question-1.md"
    question.write_text(prompt(job, view, base, own, last, new, rows, shell.run(rulings(job, "plan")).strip()))
    reply = ask(shell, job, directory, question, 1)
    if short := shortfalls(keys, reply):
        retry = directory / "question-2.md"
        rule = " Every key in the rows file appears exactly once, as met, n/a, pending, or a missing finding." if keys else ""
        retry.write_text(f"{question.read_text()}\nYour last reply gave {'; and '.join(short)}.{rule}\n")
        reply = ask(shell, job, directory, retry, 2)
        if short := shortfalls(keys, reply):
            raise JobFailed(f"two replies gave {'; and '.join(short)}")
    if (now := view_pr(shell, job)["headRefOid"]) != head:
        return {"skipped": f"{pr} moved to {now[:12]} while codex read {head[:12]}"}
    text = record_text(job, view, last, json.loads(reply)["findings"])
    body = directory / "record.md"
    body.write_text(f"{text}\n\n{reply.strip()}\n")
    return {"verdict": text.split()[1], "record": text, "posted": post(shell, job, text, body)}


def failure(error: Exception) -> str:
    if isinstance(error, subprocess.CalledProcessError):
        return f"{shlex.join(error.cmd[:4])} exited {error.returncode}: {' '.join((error.stderr or error.stdout or '').split())[:300]}"
    return f"{type(error).__name__}: {error}"


def write_result(directory: Path, outcome: dict) -> None:
    staged = directory / f".{RESULT}"
    staged.write_text(json.dumps(outcome) + "\n")
    staged.replace(directory / RESULT)


def main(argv: list[str] | None = None, shell: Shell | None = None) -> int:
    parser = argparse.ArgumentParser(prog="finder.py", description=__doc__.split("\n", 1)[0])
    parser.add_argument("dir", type=Path)
    directory = parser.parse_args(argv).dir
    try:
        outcome = run(directory, shell or Shell())
    except Exception as error:
        write_result(directory, {"error": failure(error)})
        raise
    write_result(directory, outcome)
    return 0


if __name__ == "__main__":
    drive.run_installed(__file__)
    sys.exit(main())
