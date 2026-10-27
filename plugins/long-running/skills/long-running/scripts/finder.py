#!/usr/bin/env python3
"""The finder desk's job: one codex finder pass over one drive head, posted as the `finder:` record a lane would post.

    finder.py DIR

STDLIB ONLY. desk-runner.py's orca desk writes `DIR/job.json` for each head it dispatches and starts
this script detached. The job reads the PR through ccx's gateway and its base branch's live tip with
`git ls-remote`, so a stacked head reads against its parent's current head, never an older one GitHub
still names. A first pass writes the head's required register rows to `DIR/rows.md` with
`rulings.py slice --required`; a pass over a fixed head, one an earlier finder record read at another
head, reads only what changed since and no rows (2cc5a1f3). It fills `reference/finder-desk-brief.md`
and runs `codex-ask -m <model> --schema findings` in the drive's checkout. A reply that names no verdict
for a row key the slice printed is asked once more; a second such reply fails the job. The verdict is
posted under lane `finder-desk` as `finder: ok <head> …` or `finder: major <head> <n> open: …`, kind
`review`, with `--pr <repo>#<n>`, `--id finder:<repo>#<n>:<head>`, and the whole reply on `--path`,
to the lane that posted the head and every `finder.to` address. A closed PR, or one whose head has
moved past the job's head when the pass starts or ends, posts nothing (root #151269).
`DIR/result.json` is written last, `{"error": …}` when the job raised, and the runner settles the
dispatch from it.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import drive
import ghread
from grades import names_head, repo_prs

SCRIPTS = Path(__file__).resolve().parent
TEMPLATE = SCRIPTS.parent / "reference" / "finder-desk-brief.md"
LANE = "finder-desk"
JOB = "job.json"
RESULT = "result.json"
LOG = "job.log"
HEAD_SHA = re.compile(r"(?<!was )\b[0-9a-f]{40}\b")
FULL_SHA = re.compile(r"\b[0-9a-f]{40}\b")
ROW_KEY = re.compile(r"^### (\S+) \|", re.MULTILINE)
REPLY_FILE = re.compile(r"^REPLY_FILE: (\S+)$", re.MULTILINE)
MAJORS = frozenset({"blocker", "major"})
CCI_TEXT = 400
READ_SECONDS = 120
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


def scope(job: dict, base: str) -> str:
    head, last = job["head"], job["since"]
    whole = f"`git diff {base}...{head}`"
    if not last:
        return f"This pass reads the PR's whole change: {whole}."
    return (
        f"The finder last read {last}. This pass reads what changed since: `git diff {last} {head}` when "
        f"`git merge-base --is-ancestor {last} {head}` succeeds; otherwise the head was rebased, and "
        f"`git range-diff {base} {last} {head}` names each commit that changed, which you read whole. "
        f"The PR's whole change, for context, is {whole}."
    )


def prior_text(job: dict) -> str:
    if not (prior := job["prior"]):
        return ""
    return (
        f"The PR's last finder record, cci #{prior['seq']} from {prior['lane']} at {prior['head']}, said: {prior['text']}\n"
        "Read each major it names against this head: one the head fixes, or one the PR body rebuts in a way the code "
        "bears out, is no longer open; any other is a finding again.\n\n"
    )


def prompt(job: dict, view: dict, tip: str, rows: Path, plan: str) -> str:
    return TEMPLATE.read_text().format(
        repo=job["repo"],
        pr=job["pr"],
        head=job["head"],
        base=view["baseRefName"],
        base_oid=tip,
        checkout=job["checkout"],
        fetch=" ".join(filter(None, [job["head"], tip, job["since"]])),
        scope=scope(job, tip),
        prior=prior_text(job),
        rows=rows,
        plan=plan or "none; the register names no plan",
        body=view["body"].strip() or "(empty)",
    )


def record_text(job: dict, view: dict, findings: list[dict]) -> str:
    head = job["head"]
    read = f"{job['since'][:7]}..{head[:7]}" if job["since"] else f"{view['baseRefName']}...{head[:7]}"
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


def pr_view(shell: Shell, job: dict) -> dict:
    return json.loads(shell.run(ghread.read("pr", "view", job["pr"], "--repo", job["repo"], "--json", "state,baseRefName,headRefOid,body")))


def moved(job: dict, view: dict) -> dict | None:
    if names_head(job["head"], view["headRefOid"]):
        return None
    return {"skipped": f"{job['repo']}#{job['pr']} moved past {job['head'][:10]} to {view['headRefOid'][:10]}"}


def post(shell: Shell, job: dict, text: str, body: Path) -> str:
    to = ",".join(dict.fromkeys(filter(None, [job["lane"], *job["to"]])))
    argv = ["cci", "post", "--drive", job["drive"], "--lane", LANE, "--kind", "review", "--pr", f"{job['repo']}#{job['pr']}", "--id", f"finder:{job['repo']}#{job['pr']}:{job['head']}", "--path", str(body)]
    clipped = text if len(text) <= CCI_TEXT else text[: CCI_TEXT - 1] + "…"
    return shell.run([*argv, *(["--to", to] if to else []), "--text", clipped]).strip()


def run(directory: Path, shell: Shell) -> dict:
    job = json.loads((directory / JOB).read_text())
    pr = f"{job['repo']}#{job['pr']}"
    view = pr_view(shell, job)
    if view["state"] == "CLOSED":
        return {"skipped": f"{pr} is closed"}
    if skipped := moved(job, view):
        return skipped
    tip = shell.run(["git", "ls-remote", "origin", f"refs/heads/{view['baseRefName']}"], cwd=Path(job["checkout"])).split()[0]
    rows = directory / "rows.md"
    if job["since"]:
        rows.write_text(f"No rows: the finder read every required row at {job['since'][:10]}, and this pass reads only what changed since.\n")
    else:
        rows.write_text(shell.run(rulings(job, "slice", "--required", "--pr", pr, "--head", job["head"], "--base", view["baseRefName"]), timeout=SLICE_SECONDS))
    keys = ROW_KEY.findall(rows.read_text())
    question = directory / "question-1.md"
    question.write_text(prompt(job, view, tip, rows, shell.run(rulings(job, "plan")).strip()))
    reply = ask(shell, job, directory, question, 1)
    if missing := unanswered(keys, reply):
        retry = directory / "question-2.md"
        retry.write_text(f"{question.read_text()}\nYour last reply named no verdict for {', '.join(missing)}. Every key in the rows file appears exactly once, as met, n/a, pending, or a missing finding.\n")
        reply = ask(shell, job, directory, retry, 2)
        if missing := unanswered(keys, reply):
            raise JobFailed(f"two replies named no verdict for {', '.join(missing)}")
    if skipped := moved(job, pr_view(shell, job)):
        return skipped
    text = record_text(job, view, json.loads(reply)["findings"])
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
