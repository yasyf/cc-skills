#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
"""The finder desk's job: one codex finder pass over one drive head, posted as the `finder:` record a lane would post.

    finder.py DIR

STDLIB ONLY. desk-runner.py's orca desk writes `DIR/job.json` for each head it dispatches and starts
this script detached. The job reads the PR through ccx's gateway, retrying a failed read after each pause of
`RETRY_BACKOFF`, and a fetch git killed on a zero ref another lane's `git worktree add` left in the
shared checkout the same way. It reads the base branch's and trunk's tips in one `ls-remote`, and runs
`rulings.py plan` and a whole pass's `rulings.py slice` while the head fetches. It skips a closed PR or a head that is no longer the
PR's live head. It reads the PR's own commits as the head's commits no commit on its parent's tip, nor on the trunk's, matches
by patch, so neither a parent restacked onto a newer trunk nor a stale base branch the head was
rebased past adds a commit. The parent is the base branch's tip when the head carries it, else the
newest pull request head in the head's history the trunk lacks: a graphite-base a raw push left at
the parent's old tip names none of the commits the restacked parent added. A head after one an earlier finder record
graded, or after the head its `head` record replaced when the PR has any finder review, is a fixed
head: `git range-diff` pairs its own commits with that head's, and it reads only the hunks of each
changed pair, each new commit, and each dropped commit the base lacks, and runs no row pass. A changed pair that adds no line, whitespace aside, and drops only
hunks the base now carries is a carry, and a head whose every pair is equal or a carry posts the
graded head's verdict as `finder: ok|major <head7> carried from #<seq>`, or, when no finder
record graded that head, nothing, with `ungraded` in its result so the runner queues a whole pass. A major that root settled, or one carried from a record root settled, carries as
`finder: ok <head7> carried from #<seq>, settled by root #<decide>`: the job's `settled` maps each finder
record a root `decide` names by `--re` or `--resolves`, or that a root `decide` about the PR saying
settled came after, to that decide.
Every pass hands codex the PR's earlier finder records and their answers, and a finding they closed
is not raised again. A whole pass writes the head's
required register rows to `DIR/rows.md` with `rulings.py slice --required`. A `full` job, which only a root `ask` to the finder desk saying `finder: whole` queues, reads the PR's
whole own change at its live head whatever earlier records graded. It fills
`reference/finder-desk-brief.md` and runs `codex-ask -m <model> --schema findings`, with `--incident` when the job is `fast`, in the drive's
checkout, on sol whatever the job names when the slice holds more than `BIG_SLICE` rows. The reply names a
row only in a `row <key> missing:` finding and ends `swept` with `rows checked: <n>`, so no slice size
exhausts the model's output. A reply whose count is not the slice's row count, or that gives no reasoning
in `swept`, is asked once more, and a second such reply fails the job. A head that moved while codex read
it posts nothing. The verdict is posted under lane `finder-desk` as `finder: ok <head> …` or
`finder: major <head> <n> open over <range>: …`, naming `whole <first own>^..<head>` or `delta <graded>..<head>`, kind
`review`, with `--pr <repo>#<n>`, `--id finder:<repo>#<n>:<head>` and `:whole` for a `full` job, and the whole reply on `--path`, to the lane that posted the head
and every `finder.to` address. A blocker or major stands only when one of its cites names a line
the PR's own diff adds or changes; it and every finding the reply marks `census:` are appended to
`<config dir>/finder-desk/census-<date>.md` instead, with one `finder census:` note per head to the lane that posted the head and every `finder.to` address. `DIR/result.json` is written last, `{"error": …}` when the job
raised, and the runner settles the dispatch from it. A job whose `DIR/cancelled` the runner wrote, as a newer head of the
PR queued, asks codex nothing; the runner cancels a codex run already reading it.
"""

from __future__ import annotations

import argparse
from long_running import cci
import json
import os
import re
import shlex
import subprocess
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from long_running import drive
from long_running import ghread
from long_running.grades import FINDER_DESK, FINDER_PASS, finder_grade, head_entries, names_head, repo_prs

SCRIPTS = Path(__file__).resolve().parent
TEMPLATE = SCRIPTS.parent / "reference" / "finder-desk-brief.md"
ROWS_STEP = SCRIPTS.parent / "reference" / "finder-desk-rows.md"
SCOPED_STEP = (
    "2. This head follows an earlier reviewed head, so it gets no row pass. Name an owner row\n"
    "   only when a hunk this pass reads breaks it, as one major finding whose defect starts\n"
    '   "row <key> missing:".'
)
LANE = FINDER_DESK
JOB = "job.json"
RESULT = "result.json"
LOG = "job.log"
PID = "pid"
POSTS = "posts.jsonl"
CANCELLED = "cancelled"
WHOLE_ASKED = "finder: whole"
CARRIED_FROM = re.compile(r"\bcarried from #(\d+)")
WHOLE_READ = "over whole "
HEAD_SHA = re.compile(r"(?<!was )\b[0-9a-f]{40}\b")
SHORT_SHA = re.compile(r"(?<!was )\b(?=[0-9]*[a-f])[0-9a-f]{7,39}\b")
FULL_SHA = re.compile(r"\b[0-9a-f]{40}\b")
TEXT_PR = re.compile(r"\b([\w.-]+/[\w.-]+)#(\d+)\b|(?<![\w/])#(\d{4,})\b")
ZERO_REF = re.compile(r"bad object (?:0{40}|worktrees/\S+/HEAD)")
WAS = re.compile(r"\(was ([0-9a-f]{7,40})\b")
RANGE_PAIR = re.compile(r"^\s*(?:\d+|-+):\s+([0-9a-f]+|-+)\s+([=!<>])\s+(?:\d+|-+):\s+([0-9a-f]+|-+)[ \t]*(.*)$", re.MULTILINE)
BASE_SUBJECTS = 500
DIFF_FILE = re.compile(r"^diff --git a/\S+ b/(\S+)$")
HUNK = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@")
CITE = re.compile(r"([\w./@+-]+):(\d+)(?:-(\d+))?")
CENSUS = "census:"
RULINGS_HEADING = re.compile(r"^#{1,6}\s*Rulings\b", re.IGNORECASE | re.MULTILINE)
BODY_COMMENT = re.compile(r"^(?:#{1,6}\s*|\*\*)?(?:Rulings|Regression tests|Body addendum)\b", re.IGNORECASE | re.MULTILINE)
ROW_KEY = re.compile(r"^### (\S+) \|", re.MULTILINE)
COUNTED = re.compile(r"\brows checked:\s*(\d+)")
BIG_SLICE = 150
BIG_SLICE_MODEL = "sol"
OUTPUT_CAP = "max_output_tokens"
REPLY_FILE = re.compile(r"^REPLY_FILE: (\S+)$", re.MULTILINE)
MAJORS = frozenset({"blocker", "major"})
LISTS = re.compile(r"\b(rows met|rows checked|n/a|pending|tests):")
REASON_WORDS = 12
REASONLESS = "no reasoning in `swept`, which gives one sentence per group of related hunks saying what it changes and why it holds or fails"
CCI_TEXT = 400
READ_SECONDS = 120
PLAN_SECONDS = 300
RETRY_BACKOFF = (2, 5, 10)
SLICE_SECONDS = 900
FINDER_SECONDS = 5400
STAGE_WORDS = 4


class JobFailed(RuntimeError):
    pass


class Shell:
    def run(self, argv: list[str], cwd: Path | None = None, timeout: int = READ_SECONDS) -> str:
        started = time.monotonic()
        try:
            return cci.installed(argv, lambda: subprocess.run(argv, cwd=cwd, capture_output=True, text=True, errors="replace", check=True, timeout=timeout)).stdout
        finally:
            stage = " ".join(Path(word).name if "/" in word else word for word in argv[:STAGE_WORDS])
            print(f"finder: {stage} took {time.monotonic() - started:.1f}s", file=sys.stderr, flush=True)


def heads(record: dict, repo: str) -> list[tuple[str, str]]:
    """Each `(pr, head)` a `head` record names: each `head:<pr>` attribute of a multi-head record in `repo`, else its `commit` field, else the first full sha of its text that no `was` precedes, else the first abbreviated one, which the caller spells whole from the PR's live head (#220098), for each PR its refs name in `repo`, or with no PR refs the PR its text names, as a lane that posts a head without `--pr` still gets its finder pass (root #158365)."""
    if (entries := head_entries(record, repo)) is not None:
        return [(pr, entry["commit"]) for pr, entry in entries.items()]
    head = (record.get("fields") or {}).get("commit") or next(iter(HEAD_SHA.findall(record["text"])), "") or next(iter(SHORT_SHA.findall(record["text"])), "")
    prs = [pr for pr, _ in repo_prs(record, repo)] if (record.get("refs") or {}).get("prs") else text_pr(record["text"], repo)
    return [(pr, head) for pr in prs] if head else []


def head_gap(record: dict, repo: str) -> str:
    """Why a `head` record names no head a grader can queue: it names a PR of `repo` and no commit sha, or no pull request at all; empty when it names a head, or only PRs of other repos."""
    if heads(record, repo) or head_entries(record, repo) is not None:
        return ""
    refs = (record.get("refs") or {}).get("prs")
    if prs := [pr for pr, _ in repo_prs(record, repo)] if refs else text_pr(record["text"], repo):
        return f"names #{prs[0]} but no commit sha"
    return "" if refs or TEXT_PR.search(record["text"]) else "names no pull request"


def text_pr(text: str, repo: str) -> list[str]:
    """The first `repo#N` the text names, else its first bare `#N` of four or more digits, as a bare `#5` is an audit or step number (#170072)."""
    return text_prs(text, repo)[:1]


def text_prs(text: str, repo: str) -> list[str]:
    """Every `repo#N` the text names, else every bare `#N` of four or more digits, in order and once each."""
    named = TEXT_PR.findall(text)
    return list(dict.fromkeys([number for owner, number, _ in named if owner.lower() == repo.lower()] or [bare for _, _, bare in named if bare]))


def requested(record: dict, repo: str) -> list[str]:
    """The PRs an `ask` to the finder desk asks a whole pass over by saying `finder: whole`, every one it names; a `decide` asks none, since root settles findings in one (root #182759)."""
    if record["kind"] != "ask" or LANE not in (record.get("to") or []) or WHOLE_ASKED not in record["text"].casefold():
        return []
    return [pr for pr, _ in repo_prs(record, repo)] if (record.get("refs") or {}).get("prs") else text_prs(record["text"], repo)


def since(reviews: list[dict], records: list[dict], head: str, repo: str) -> tuple[str, dict | None]:
    """The full head the PR's newest finder record at another head read, empty when no `head` record spells it out, and that record."""
    prior = max((review for review in reviews if not names_head(review["head"], head)), key=lambda review: review["seq"], default=None)
    if prior is None:
        return "", None
    spelled = [prior["head"], *(sha for record in records if record["kind"] == "head" for sha in [(record.get("fields") or {}).get("commit", ""), *(entry[key] for entry in (head_entries(record, repo) or {}).values() for key in ("commit", "was")), *FULL_SHA.findall(record["text"])])]
    full = next((sha for sha in spelled if FULL_SHA.fullmatch(sha) and sha.startswith(prior["head"])), "")
    return full, {"seq": prior["seq"], "lane": prior["lane"], "head": prior["head"], "text": prior["text"]}


def scope(job: dict, own: list[str], last: str, pairs: str, changed: list[tuple[str, str]]) -> str:
    whole = f"`git diff {own[0]}^ {job['head']}`"
    if not last:
        return f"This pass reads the PR's whole change: {whole}."
    amended = " ".join(sha for mark, sha in changed if mark == "!") or "none"
    added = " ".join(sha for mark, sha in changed if mark == ">") or "none"
    dropped = " ".join(sha for mark, sha in changed if mark == "<") or "none"
    return (
        f"An earlier head, {last}, was reviewed. `git range-diff {pairs}` pairs its own commits with this head's; "
        f"a `<` line is a commit the base now carries, or one this head dropped. This pass reads only what changed: for each amended commit, "
        f"only the hunks the range-diff shows for its pair ({amended}); each new commit whole with `git show <sha>` "
        f"({added}); and the removal of each dropped commit the base lacks, read with `git show <sha>` ({dropped}). "
        f"The PR's whole change, for context only, is {whole}."
    )


def listed(records: list[dict]) -> str:
    return "".join(f"- cci #{record['seq']} from {record['lane']}: {record['text']}" + (f" (whole record: {record['path']})" if record["path"] else "") + "\n" for record in records)


def prior_text(job: dict) -> str:
    text = ""
    if history := job["history"]:
        text += (
            f"The PR's earlier finder records and the lanes' answers to them, oldest first:\n{listed(history)}"
            "A finding one of them raised is closed when the head fixes it or an answer rebuts it in a way the code bears "
            "out; never raise a closed finding again. Raise one only when a hunk this pass reads reintroduces it.\n\n"
        )
    if ruled := job["ruled"]:
        text += (
            f"The root's rulings on this PR, oldest first:\n{listed(ruled)}"
            "A point a ruling settles is closed: never raise it again, and never grade against the ruling what it decided.\n\n"
        )
    if history or ruled:
        text += "A change the head makes because one of these records asked for it is planned; never grade it as unplanned or raise it as a defect of its own.\n\n"
    return text


def hunks(diff: str) -> dict[str, list[tuple[int, int]]]:
    """Each file's new-side line ranges a `git diff --unified=0` adds or changes, a pure deletion as the line it follows."""
    ranges: dict[str, list[tuple[int, int]]] = {}
    path = ""
    for line in diff.splitlines():
        if found := DIFF_FILE.match(line):
            path = found[1]
        elif found := HUNK.match(line):
            start = int(found[1])
            ranges.setdefault(path, []).append((start, start + max(int(found[2] or 1), 1) - 1))
    return ranges


def in_diff(cite: str, touched: dict[str, list[tuple[int, int]]]) -> bool:
    return any(
        (path == named or path.endswith(f"/{named}") or named.endswith(f"/{path}")) and start <= int(last or first) and int(first) <= end
        for named, first, last in CITE.findall(cite)
        for path, spans in touched.items()
        for start, end in spans
    )


def triage(findings: list[dict], touched: dict[str, list[tuple[int, int]]]) -> tuple[list[dict], list[dict]]:
    """The findings that stand, and the census: each finding marked `census:` and each blocker or major none of whose cites names a line the diff touched."""
    census = [finding for finding in findings if finding["defect"].startswith(CENSUS) or (finding["severity"] in MAJORS and not in_diff(finding["cite"], touched))]
    return [finding for finding in findings if finding not in census], census


def hunk_lines(touched: dict[str, list[tuple[int, int]]]) -> str:
    return "\n".join(f"- {path}: {', '.join(str(start) if start == end else f'{start}-{end}' for start, end in spans)}" for path, spans in touched.items())


def prompt(job: dict, view: dict, base: str, own: list[str], last: str, pairs: str, changed: list[tuple[str, str]], rows: Path | None, plan: str, touched: dict[str, list[tuple[int, int]]]) -> str:
    return TEMPLATE.read_text().format(
        repo=job["repo"],
        pr=job["pr"],
        head=job["head"],
        base=view["baseRefName"],
        base_oid=base,
        checkout=job["checkout"],
        fetch=f"{job['head']} {base}",
        scope=scope(job, own, last, pairs, changed),
        prior=prior_text(job),
        rows_step=ROWS_STEP.read_text().format(rows=rows).rstrip() if rows else SCOPED_STEP,
        hunks=hunk_lines(touched),
        plan=plan or "none; the register names no plan",
        body=view["body"].strip() or "(empty)",
        rulings=author_comments(view),
    )


def author_comments(view: dict) -> str:
    """The comments the PR's author posted, oldest first: the lanes' Rulings, Body addenda and evidence such as a caller trace, which correct and extend a body nobody edits after the PR opens. Reviewer bots and service accounts post under other logins."""
    return "\n\n---\n\n".join(comment["body"].strip() for comment in view["comments"] if comment["author"]["login"] == view["author"]["login"]) or "(none)"


def rulings_comments(view: dict) -> str:
    """The PR's comments that open a `Rulings`, `Regression tests` or `Body addendum` section, where a lane corrects or extends a body it may not edit, oldest first."""
    return "\n\n---\n\n".join(comment["body"].strip() for comment in view["comments"] if BODY_COMMENT.search(comment["body"])) or "(none)"


def record_text(job: dict, own: list[str], last: str, findings: list[dict]) -> str:
    head = job["head"]
    read = f"over delta {last[:7]}..{head[:7]}" if last else f"{WHOLE_READ}{own[0][:7]}^..{head[:7]}"
    if not (majors := [finding for finding in findings if finding["severity"] in MAJORS]):
        return f"finder: ok {head} {job['model']} finder {read}: no majors"
    opened = "; ".join(" ".join(f"{finding['cite']} {finding['defect']}".split()) for finding in majors)
    return f"finder: major {head} {len(majors)} open {read}: {opened}"


def rulings(job: dict, verb: str, *argv: str) -> list[str]:
    return [sys.executable, str(SCRIPTS / "rulings.py"), verb, "--repo", job["checkout"], "--drive", job["drive"], *argv]


def ask(shell: Shell, job: dict, directory: Path, question: Path, attempt: int) -> str:
    def asked(model: str, lane: str) -> str:
        return shell.run(["codex-ask", "-m", model, *(["--incident"] if job["fast"] else []), "--schema", "findings", "-s", str(directory / lane), str(question)], cwd=Path(job["checkout"]), timeout=FINDER_SECONDS)

    try:
        out = asked(job["model"], f"codex-{attempt}")
    except subprocess.CalledProcessError as error:
        if job["model"] == BIG_SLICE_MODEL or OUTPUT_CAP not in f"{error.stdout or ''}{error.stderr or ''}":
            raise
        out = asked(BIG_SLICE_MODEL, f"codex-{attempt}-{BIG_SLICE_MODEL}")
    return Path(REPLY_FILE.search(out)[1]).read_text()


def reasoned(reply: str) -> bool:
    swept = json.loads(reply)["swept"]
    return len(swept[: (lists.start() if (lists := LISTS.search(swept)) else len(swept))].split()) >= REASON_WORDS


def shortfalls(keys: list[str], reply: str) -> list[str]:
    counted = COUNTED.findall(json.loads(reply)["swept"])
    miscounted = [f"`rows checked: {counted[-1] if counted else '<none>'}` where the rows file holds {len(keys)} rows"] if keys and (not counted or int(counted[-1]) != len(keys)) else []
    return miscounted + ([] if reasoned(reply) else [REASONLESS])


def retried[T](attempt: Callable[[], T], transient: Callable[[subprocess.CalledProcessError], bool] = lambda error: True) -> T:
    """`attempt()`, run again after each pause of `RETRY_BACKOFF` while it raises a failure `transient` accepts."""
    for pause in RETRY_BACKOFF:
        try:
            return attempt()
        except subprocess.CalledProcessError as error:
            if not transient(error):
                raise
        time.sleep(pause)
    return attempt()


def view_pr(shell: Shell, job: dict) -> dict:
    argv = ghread.read("pr", "view", job["pr"], "--repo", job["repo"], "--json", "state,baseRefName,headRefOid,body,comments,author", max_age=ghread.FRESH)
    return json.loads(retried(lambda: shell.run(argv)))


def git(shell: Shell, job: dict, *argv: str) -> str:
    return shell.run(["git", *argv], cwd=Path(job["checkout"]), timeout=SLICE_SECONDS).strip()


def fetch(shell: Shell, job: dict, *shas: str) -> None:
    """Fetch `shas`, again after each pause of `RETRY_BACKOFF` while the connectivity walk dies on a zero ref a lane creating a worktree in the shared checkout had not yet written."""
    retried(lambda: git(shell, job, "fetch", "--no-write-fetch-head", "-q", "origin", *shas), lambda error: bool(ZERO_REF.search(error.stderr)))


def tips(shell: Shell, job: dict, branch: str) -> tuple[str, str]:
    """The remote tips of `branch` and of the trunk, `HEAD`, from one `ls-remote`; a base branch the remote deleted once its PR merged into it reads as the trunk's tip, which now carries it (#219204)."""
    refs = {ref: sha for sha, ref in (line.split("\t") for line in git(shell, job, "ls-remote", "origin", "HEAD", f"refs/heads/{branch}").splitlines())}
    return refs.get(f"refs/heads/{branch}", refs["HEAD"]), refs["HEAD"]


def checked_out(shell: Shell, job: dict, view: dict) -> tuple[str, str]:
    """The tip the head's own commits sit on, and the trunk's, once the head and both tips are fetched."""
    base, trunk = tips(shell, job, view["baseRefName"])
    fetch(shell, job, job["head"], base, trunk)
    return parent(shell, job, base, trunk), trunk


def beyond(shell: Shell, job: dict, others: tuple[str, ...], head: str) -> list[str]:
    """The commits `head` carries, oldest first, that no commit since its merge-base with any of `others` matches by patch.

    The patches compare over only the paths the head's own commits touch, so a partial clone fetches the blobs of those
    paths alone rather than of every trunk commit since the merge-base.
    """
    with ThreadPoolExecutor() as pool:
        first, *rest = (commits.split() for commits in pool.map(lambda other: own_since(shell, job, other, head), dict.fromkeys(others)))
    return [sha for sha in first if all(sha in commits for commits in rest)]


def own_since(shell: Shell, job: dict, other: str, head: str) -> str:
    paths = dict.fromkeys(git(shell, job, "log", "--no-merges", "--no-renames", "--format=", "--name-only", f"{other}..{head}").split("\n"))
    return git(shell, job, "rev-list", "--cherry-pick", "--right-only", "--no-merges", "--reverse", f"{other}...{head}", "--", *filter(None, paths))


def parent(shell: Shell, job: dict, base: str, trunk: str) -> str:
    """The tip the PR's own commits sit on: `base` when the head carries it, else the newest pull request head in the head's history `trunk` lacks, else `base`."""
    head = job["head"]
    try:
        git(shell, job, "merge-base", "--is-ancestor", base, head)
        return base
    except subprocess.CalledProcessError:
        pulls = {line.split("\t")[0] for line in git(shell, job, "ls-remote", "origin", "refs/pull/*/head").splitlines()}
        return next((sha for sha in git(shell, job, "rev-list", "--topo-order", head, f"^{trunk}").split() if sha != head and sha in pulls), base)


def resolve(shell: Shell, job: dict, named: str) -> str:
    try:
        return git(shell, job, "rev-parse", "--verify", "--quiet", f"{named}^{{commit}}")
    except subprocess.CalledProcessError:
        return ""


def fetched(shell: Shell, job: dict, named: str) -> str:
    """`named` as a full sha, from the checkout or fetched when spelled whole, empty when neither has it."""
    if (full := resolve(shell, job, named)) or not FULL_SHA.fullmatch(named):
        return full
    try:
        fetch(shell, job, named)
    except subprocess.CalledProcessError:
        return ""
    return resolve(shell, job, named)


def earlier_heads(job: dict) -> list[str]:
    """The heads this pass may read since: the one the PR's last finder record graded, then the head the `head` record replaced."""
    return [name for name in (job["since"], (job["prior"] or {}).get("head", ""), job["previous"]) if name]


def reviewed(shell: Shell, job: dict) -> str:
    """The first of `earlier_heads` that resolves, empty when none does."""
    return next((full for name in earlier_heads(job) if (full := fetched(shell, job, name))), "")


def slice_rows(shell: Shell, job: dict, pr: str, head: str, branch: str, *flags: str) -> str:
    return shell.run(rulings(job, "slice", "--required", "--pr", pr, "--head", head, "--base", branch, *flags), timeout=SLICE_SECONDS)


def previous(record: dict | None, repo: str, pr: str) -> str:
    """The head a `head` record says `pr`'s head replaced: its `head:<pr>` attribute's `was`, else its text's `(was <sha>)`, empty when it names none."""
    if record is None:
        return ""
    if (entries := head_entries(record, repo)) is not None:
        return entries.get(pr, {}).get("was", "")
    return found[1] if (found := WAS.search(record["text"])) else ""


def range_pairs(shell: Shell, job: dict, base: str, last: str, earlier: list[str], own: list[str]) -> tuple[str, list[tuple[str, str]]]:
    """The `git range-diff` ranges pairing `last`'s own commits, `earlier`, with the head's, each head commit it marks amended (`!`) or new (`>`), leaving out an amended commit that only drops hunks `base` carries, and each earlier commit it marks dropped (`<`) that `base` does not carry: one whose subject a commit of `base` has, since it moved down the stack and its own PR's review covers it, or whose removed hunks `base` has line for line."""
    pairs = f"{earlier[0]}^..{last} {own[0]}^..{job['head']}"
    marks = RANGE_PAIR.findall(git(shell, job, "range-diff", "--no-patch", "--no-color", *pairs.split()))
    moved = set(git(shell, job, "log", "--format=%s", "-n", str(BASE_SUBJECTS), base).splitlines()) if any(mark == "<" for _, mark, _, _ in marks) else set()
    return pairs, [(mark, left if mark == "<" else sha) for left, mark, sha, subject in marks if mark == ">" or (mark in "!<" and not (mark == "<" and subject.strip() in moved) and not drops_carried(shell, job, base, left, sha))]


def patch_lines(shell: Shell, job: dict, sha: str) -> set[tuple[str, str, str]]:
    """Each added or removed line of `sha`'s patch as `(file, sign, text)`, its whitespace collapsed as `git patch-id` ignores it, blank lines left out."""
    lines, path = set(), ""
    for line in git(shell, job, "show", "--format=", "--no-color", "--no-ext-diff", sha).splitlines():
        if found := DIFF_FILE.match(line):
            path = found[1]
        elif line.startswith(("+", "-")) and not line.startswith(("+++", "---")) and line[1:].strip():
            lines.add((path, line[0], " ".join(line[1:].split())))
    return lines


def drops_carried(shell: Shell, job: dict, base: str, left: str, right: str) -> bool:
    """Whether `right`, or no commit when it is all dashes, adds no line `left` lacks and every line it drops is where `base` already has it: an added line present, a removed line gone."""
    earlier, later = patch_lines(shell, job, left), set() if not right.strip("-") else patch_lines(shell, job, right)
    if later - earlier:
        return False
    texts: dict[str, set[str]] = {}
    for path, sign, text in earlier - later:
        if path not in texts:
            try:
                texts[path] = {" ".join(line.split()) for line in git(shell, job, "show", f"{base}:{path}").splitlines()}
            except subprocess.CalledProcessError:
                texts[path] = set()
        if (text in texts[path]) != (sign == "+"):
            return False
    return True


def record_census(shell: Shell, job: dict, directory: Path, census: list[dict]) -> cci.Posted:
    path = directory.parent / f"census-{time.strftime('%Y-%m-%d')}.md"
    with path.open("a") as file:
        file.writelines(f"- {job['repo']}#{job['pr']} {job['head'][:12]} {' '.join(finding['cite'].split())}: {' '.join(finding['defect'].removeprefix(CENSUS).split())}\n" for finding in census)
    pr = f"{job['repo']}#{job['pr']}"
    text = f"finder census: {len(census)} dev-only findings on #{job['pr']} appended to {path}"
    to = ",".join(dict.fromkeys(filter(None, [job["lane"], *job["to"]])))
    return cci.posted(shell.run(["cci", "post", "--drive", job["drive"], "--lane", LANE, "--kind", "note", "--to", to, "--pr", pr, "--id", f"finder-census:{pr}:{job['head']}", "--text", text, "--json"]))[0]


def post(shell: Shell, job: dict, text: str, body: Path) -> cci.Posted:
    to = ",".join(dict.fromkeys(filter(None, [job["lane"], *job["to"]])))
    argv = ["cci", "post", "--drive", job["drive"], "--lane", LANE, "--kind", "review", "--pr", f"{job['repo']}#{job['pr']}", "--id", f"finder:{job['repo']}#{job['pr']}:{job['head']}{':whole' if job['full'] else ''}", "--path", str(body)]
    clipped = text if len(text) <= CCI_TEXT else text[: CCI_TEXT - 1] + "…"
    return cci.posted(shell.run(logged(body.parent, [*argv, *(["--to", to] if to else []), "--text", clipped, "--json"])))[0]


def logged(directory: Path, argv: list[str]) -> list[str]:
    """`argv`, once it is appended to the job's `POSTS`, which the runner replays on its own bus: a job on a Sprite posts to the Sprite's cci, whose relay can drop the record while the post still prints a seq (root #217856)."""
    with (directory / POSTS).open("a") as posts:
        posts.write(json.dumps(argv) + "\n")
    return argv


def run(directory: Path, shell: Shell) -> dict:
    job = json.loads((directory / JOB).read_text())
    pr, head = f"{job['repo']}#{job['pr']}", job["head"]
    pool = ThreadPoolExecutor()
    plan = pool.submit(shell.run, rulings(job, "plan"), timeout=PLAN_SECONDS)
    view = view_pr(shell, job)
    if view["state"] == "CLOSED":
        return {"skipped": f"{pr} is closed"}
    if not view["headRefOid"].startswith(head):
        return {"skipped": f"{pr} moved to {view['headRefOid'][:12]}"}
    job["head"] = head = view["headRefOid"]
    slicing = None if earlier_heads(job) else pool.submit(slice_rows, shell, job, pr, head, view["baseRefName"])
    base, trunk = checked_out(shell, job, view)
    last = reviewed(shell, job)
    owning = pool.submit(beyond, shell, job, (base, trunk), head)
    earlier = beyond(shell, job, (base, trunk), last) if last else []
    if not (own := owning.result()):
        return {"skipped": f"{pr} at {head[:12]} carries no commit {view['baseRefName']} lacks"}
    last = last if earlier else ""
    pairs, changed = range_pairs(shell, job, base, last, earlier, own) if last else ("", [])
    if last and not changed:
        replayed = f"{pr} at {head[:12]} replays every own commit of {last[:12]}, patch for patch or dropping only hunks {view['baseRefName']} carries"
        if not ((earlier := job["prior"]) and names_head(earlier["head"], last)):
            return {"skipped": replayed, "ungraded": True}
        text = f"finder: {finder_grade(earlier['text'])} {head[:7]} carried from #{earlier['seq']}"
        if finder_grade(earlier["text"]) != FINDER_PASS and (root := settled_by(job, earlier)):
            text = f"finder: {FINDER_PASS} {head[:7]} carried from #{earlier['seq']}, settled by root #{root}"
        body = directory / "record.md"
        body.write_text(f"{text}\n\n{replayed}.\n\n#{earlier['seq']}: {earlier['text']}\n")
        return {"verdict": text.split()[1], "record": text, "posted": post(shell, job, text, body)}
    rows = None if last else directory / "rows.md"
    touched = hunks(git(shell, job, "diff", "--unified=0", "--no-color", "--no-ext-diff", f"{own[0]}^", head))
    keys: list[str] = []
    if rows:
        rows.write_text((slicing or pool.submit(slice_rows, shell, job, pr, head, view["baseRefName"])).result())
        keys = ROW_KEY.findall(rows.read_text())
    if len(keys) > BIG_SLICE:
        job = job | {"model": BIG_SLICE_MODEL}
    question = directory / "question-1.md"
    question.write_text(prompt(job, view, base, own, last, pairs, changed, rows, plan.result().strip(), touched))
    if (cancelled := directory / CANCELLED).is_file():
        return {"skipped": f"{pr} at {head[:12]} was superseded by {cancelled.read_text().strip()[:12]} before codex read it"}
    reply = ask(shell, job, directory, question, 1)
    if short := shortfalls(keys, reply):
        retry = directory / "question-2.md"
        rule = f" Give every row in the rows file a verdict and end `swept` with `rows checked: {len(keys)}`." if keys else ""
        retry.write_text(f"{question.read_text()}\nYour last reply gave {'; and '.join(short)}.{rule}\n")
        reply = ask(shell, job, directory, retry, 2)
        if short := shortfalls(keys, reply):
            raise JobFailed(f"two replies gave {'; and '.join(short)}")
    if (now := view_pr(shell, job)["headRefOid"]) != head:
        return {"skipped": f"{pr} moved to {now[:12]} while codex read {head[:12]}"}
    findings, census = triage(json.loads(reply)["findings"], touched)
    text = record_text(job, own, last, findings)
    body = directory / "record.md"
    body.write_text(f"{text}\n\n{reply.strip()}\n")
    outcome = {"verdict": text.split()[1], "record": text, "posted": post(shell, job, text, body)}
    return outcome | ({"census": record_census(shell, job, directory, census)} if census else {})


def settled_by(job: dict, record: dict | None) -> int | None:
    """The root `decide` that settled `record` or a finder record it was carried from."""
    by_seq = {earlier["seq"]: earlier for earlier in job["history"]}
    while record:
        if root := job["settled"].get(str(record["seq"])):
            return root
        record = by_seq.get(int(carried[1])) if (carried := CARRIED_FROM.search(record["text"])) else None
    return None


def failure(error: Exception) -> str:
    if isinstance(error, subprocess.CalledProcessError):
        return f"{shlex.join(error.cmd[:4])} exited {error.returncode}: {' '.join((error.stderr or error.stdout or '').split())[-300:]}"
    return f"{type(error).__name__}: {error}"


def write_result(directory: Path, outcome: dict) -> None:
    staged = directory / f".{RESULT}"
    with staged.open("w") as out:
        out.write(json.dumps(outcome, default=lambda posted: cci.receipt(posted.seq)) + "\n")
        out.flush()
        os.fsync(out.fileno())
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
