#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
"""The fidelity pipeline's job: one fidelity grade of one drive head, carried with no model call when the head's patch matches a graded head's, else read by codex.

    fidelity.py DIR

STDLIB ONLY. desk-runner.py's orca desk writes `DIR/job.json` for each head it dispatches and starts
this script detached. The job reads the PR, skips a closed PR or a head that is no longer its live
head, and takes the PR's own commits as finder.py does. When `git patch-id --stable` of those
commits matches the same number of commits up to an earlier head every fidelity lane's newest grade
of which is ok, or a `missing` the job's `settled` map names, it posts `fidelity: ok <head> carried from
<earlier>`, citing the root `decide` that settled a carried `missing`, with the earlier head's
`old-app:` line and no model call. The carry repeats no exempt phrase when a root `class wrong`
decision answers the carried grade or a grade it was carried from, else each one the newest
fidelity-desk ok grade on the PR says, else each one the carried grade says. Otherwise it fills
`reference/fidelity-job-brief.md` with the head's own hunks, its required register rows
(`rulings.py slice --required`), the plan, the lane's newest contract and the PR body, and runs
`codex-ask -m <model> --schema fidelity.schema.json`, with `--incident` when the job is `fast`, in the drive's checkout. A reply that leaves
a row ungraded, keys a `missing` line on anything but a plan line or an owner row, or gives no
reasoning in `swept` is asked once more; a second such reply fails the job. A head that moved while
codex read it posts nothing. The question lists every point of a `missing` grade root settled, and a
reply's `missing` entry that repeats one word for word is dropped; a reply left with none posts ok citing
the settling decide. Each line is posted under lane `fidelity-pipeline`, kind `review`, to the
head's lane and every `to` address: `fidelity: ok`, one `fidelity: missing` per broken row, or
`fidelity: reader` to the human fidelity desks as well, then one `touches <key>` per reserved row and
the `old-app:` line against the job's baseline. `DIR/result.json` is written last, `{"error": …}`
when the job raised, and the runner settles the dispatch from it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import drive
import finder
from grades import FIDELITY_MISSING, FIDELITY_OK, graded_head, names_head, newest_finder, standing

SCRIPTS = Path(__file__).resolve().parent
TEMPLATE = SCRIPTS.parent / "reference" / "fidelity-job-brief.md"
RUBRIC = SCRIPTS.parent / "reference" / "fidelity-desk-brief.md"
SCHEMA = SCRIPTS / "fidelity.schema.json"
LANE = "fidelity-pipeline"
CARRIED_SEQ = re.compile(r"\bcarried from [0-9a-f]{7,40} \(cci #(\d+)\)")
JOB = "job.json"
RESULT = "result.json"
LOG = "job.log"
READER = "fidelity: reader"
OLD_APP_LINE = re.compile(r"^old-app:\s*(unchanged|bug fix|changes)\b:?\s*(?:[0-9a-f]{7,40}\b)?\s*(.*)$", re.IGNORECASE)
CODEX_SECONDS = 3600
PLAN_LINE = re.compile(r"plan:(?:[\w./-]+\.md:)?\d+(?:-\d+)?")


def patch_id(shell: finder.Shell, job: dict, start: str, end: str) -> str:
    """`git patch-id --stable` of `git diff start end`, empty when the diff is empty or does not read."""
    out = shell.run(["sh", "-c", 'git diff "$1" "$2" | git patch-id --stable', "patch-id", start, end], cwd=Path(job["checkout"]), timeout=finder.SLICE_SECONDS)
    return next(iter(out.split()), "")


def carry(shell: finder.Shell, job: dict, own: list[str]) -> dict | None:
    """The newest earlier head whose every fidelity lane stands at ok, or at a `missing` a root `decide` settled, and whose last `len(own)` commits have the head's patch, with that lane's newest grade and the patch id."""
    ours = patch_id(shell, job, f"{own[0]}^", job["head"])
    oks = [grade for grade in sorted(job["grades"], key=lambda grade: grade["seq"], reverse=True) if passes(job, grade)]
    for named in dict.fromkeys(head for grade in oks if (head := graded_head(grade["text"].split(maxsplit=2)[2])) and not names_head(head, job["head"])):
        stand = standing(job["grades"], job["lanes"], named)
        if not stand or not all(passes(job, grade) for grade in stand) or not (full := finder.fetched(shell, job, named)):
            continue
        if ours and patch_id(shell, job, f"{full}~{len(own)}", full) == ours:
            return {"head": full, "grade": max(stand, key=lambda grade: grade["seq"]), "patch": ours}
    return None


def passes(job: dict, grade: dict) -> bool:
    return grade["text"].startswith(FIDELITY_OK) or (grade["text"].startswith(FIDELITY_MISSING) and str(grade["seq"]) in job["settled"])


def exempt_phrases(job: dict, text: str) -> list[str]:
    return [phrase for phrase in job["exempt_grades"] if phrase.casefold() in text.casefold()]


def carried_class(job: dict, grade: dict) -> str:
    by_seq = {record["seq"]: record for record in job["grades"]}
    chain, record = set(), grade
    while record:
        chain.add(record["seq"])
        record = by_seq.get(int(carried[1])) if (carried := CARRIED_SEQ.search(record["text"])) else None
    if chain & set(job["corrected"]):
        return ""
    desk = max((record for record in job["grades"] if record["lane"] != LANE and record["text"].startswith(FIDELITY_OK)), key=lambda record: record["seq"], default=grade)
    return "".join(f"{phrase}: " for phrase in exempt_phrases(job, desk["text"]))


def carried_lines(job: dict, found: dict) -> list[tuple[str, list[str]]]:
    head, earlier, grade = job["head"], found["head"], found["grade"]
    said = carried_class(job, grade)
    settled = f" Root settled its missing points at #{root}." if (root := job["settled"].get(str(grade["seq"]))) else ""
    lines = [(f"{FIDELITY_OK} {head} {said}carried from {earlier[:7]} (cci #{grade['seq']}), patch-identical by git patch-id over its own commits.{settled}", [])]
    if (old_app := newest_finder(job["old_apps"], earlier)) and (parts := OLD_APP_LINE.match(old_app["line"])):
        lines.append((f"old-app: {parts[1]} {head[:7]} {parts[2]}".rstrip(), []))
    return lines


def prompt(job: dict, view: dict, base: str, own: list[str], rows: Path, plan: str, touched: dict[str, list[tuple[int, int]]]) -> str:
    return TEMPLATE.read_text().format(
        repo=job["repo"],
        pr=job["pr"],
        head=job["head"],
        base=view["baseRefName"],
        base_oid=base,
        checkout=job["checkout"],
        rubric=RUBRIC,
        fetch=f"{job['head']} {base}",
        whole=f"`git diff {own[0]}^ {job['head']}`",
        hunks=finder.hunk_lines(touched),
        rows=rows,
        port=job["exempt"]["port"],
        test_only=job["exempt"]["test-only"],
        baseline=baseline(job),
        plan=plan or "none; the register names no plan",
        package=Path(plan).parents[1] if plan else "none; the register names no plan",
        contract=job["contract"].strip() or "(the lane posted none)",
        body=view["body"].strip() or "(empty)",
        rulings=finder.rulings_comments(view),
        settled="".join(f"- {point} (root #{root})\n" for point, root in settled_points(job).items()) or "(none)",
    )


def settled_points(job: dict) -> dict[str, int]:
    """Each point of a `fidelity: missing` grade on the PR that a root `decide` settled, with that decide."""
    return {point: root for grade in job["grades"] if grade["text"].startswith(FIDELITY_MISSING) and (root := job["settled"].get(str(grade["seq"]))) for point in grade["text"][len(FIDELITY_MISSING) :].split(maxsplit=2)[2].split("; ")}


def unsettled(job: dict, reply: dict) -> dict:
    """`reply` less each `missing` entry that repeats a settled point word for word, read as ok citing the settling decides when none is left."""
    if reply["verdict"] != "missing" or not (points := settled_points(job)):
        return reply
    kept = [entry for entry in reply["missing"] if " ".join(entry["line"].split()) not in points]
    if kept:
        return reply | {"missing": kept}
    roots = ", ".join(f"#{root}" for root in sorted({points[" ".join(entry["line"].split())] for entry in reply["missing"]}))
    return reply | {"verdict": "ok", "missing": [], "summary": f"{reply['summary']} Every missing point is one root settled at {roots}."}


def baseline(job: dict) -> str:
    return job["baseline"] or "the old app as last released"


def grounded(keys: list[str], key: str) -> bool:
    """Whether a `missing` key is a plan line, a line of a doc in the plan's package, or a non-`design/` row of the head's required slice."""
    return bool(PLAN_LINE.fullmatch(key)) or (key in keys and not key.startswith("design/"))


def shortfalls(keys: list[str], reply: dict) -> list[str]:
    short = []
    if unchecked := [key for key in keys if key not in reply["checked"]]:
        short.append(f"no grade for {', '.join(unchecked)}")
    if ungrounded := [entry["key"] for entry in reply["missing"] if not grounded(keys, entry["key"])]:
        short.append(f"`missing` keys that are neither a plan line `plan:<n>`, a package doc line `plan:<path>:<n>`, nor a non-design row of the rows file: {', '.join(ungrounded)}")
    if (reply["verdict"] == "missing") != bool(reply["missing"]):
        short.append(f"verdict {reply['verdict']} with {len(reply['missing'])} `missing` entries")
    if reply["verdict"] == "reader" and not reply["reader"].strip():
        short.append("verdict reader with no reason in `reader`")
    if len(reply["swept"].split()) < finder.REASON_WORDS:
        short.append(finder.REASONLESS)
    return short


def graded_lines(job: dict, reply: dict) -> list[tuple[str, list[str]]]:
    head = job["head"]
    lines: list[tuple[str, list[str]]]
    if reply["verdict"] == "ok":
        said = f"{job['exempt'][reply['exempt']]}: " if reply["exempt"] != "none" else ""
        lines = [(f"{FIDELITY_OK} {head} {said}{' '.join(reply['summary'].split())}", [])]
    elif reply["verdict"] == "missing":
        keys = ",".join(dict.fromkeys(entry["key"] for entry in reply["missing"]))
        lines = [(f"{FIDELITY_MISSING} {head} {keys} {'; '.join(' '.join(entry['line'].split()) for entry in reply['missing'])}", [])]
    else:
        lines = [(f"{READER} {head} {' '.join(reply['reader'].split())}", job["readers"])]
    lines += [(f"touches {key}", []) for key in reply["touches"]]
    grade = "CHANGES" if reply["old_app"]["grade"] == "changes" else reply["old_app"]["grade"]
    return [*lines, (f"old-app: {grade} {head[:7]} baseline {baseline(job)}; {' '.join(reply['old_app']['why'].split())}", [])]


def ask(shell: finder.Shell, job: dict, directory: Path, question: Path, attempt: int) -> dict:
    out = shell.run(["codex-ask", "-m", job["model"], *(["--incident"] if job["fast"] else []), "--schema", str(SCHEMA), "-s", str(directory / f"codex-{attempt}"), str(question)], cwd=Path(job["checkout"]), timeout=CODEX_SECONDS)
    return json.loads(Path(finder.REPLY_FILE.search(out)[1]).read_text())


def post(shell: finder.Shell, job: dict, lines: list[tuple[str, list[str]]], body: Path) -> list[str]:
    pr = f"{job['repo']}#{job['pr']}"
    posted = []
    for index, (text, also) in enumerate(lines):
        to = ",".join(dict.fromkeys(filter(None, [job["lane"], *job["to"], *also])))
        clipped = text if len(text) <= finder.CCI_TEXT else text[: finder.CCI_TEXT - 1] + "…"
        argv = ["cci", "post", "--drive", job["drive"], "--lane", LANE, "--kind", "review", "--pr", pr, "--id", f"fidelity:{pr}:{job['head']}:{index}", "--path", str(body)]
        posted.append(shell.run([*argv, *(["--to", to, "--force"] if to else []), "--text", clipped]).strip())
    return posted


def run(directory: Path, shell: finder.Shell) -> dict:
    job = json.loads((directory / JOB).read_text())
    pr, head = f"{job['repo']}#{job['pr']}", job["head"]
    view = finder.view_pr(shell, job)
    if view["state"] == "CLOSED":
        return {"skipped": f"{pr} is closed"}
    if not view["headRefOid"].startswith(head):
        return {"skipped": f"{pr} moved to {view['headRefOid'][:12]}"}
    job["head"] = head = view["headRefOid"]
    base = finder.git(shell, job, "ls-remote", "origin", f"refs/heads/{view['baseRefName']}").split()[0]
    trunk = finder.git(shell, job, "ls-remote", "origin", "HEAD").split()[0]
    finder.fetch(shell, job, head, base, trunk)
    base = finder.parent(shell, job, base, trunk)
    if not (own := finder.beyond(shell, job, (base, trunk), head)):
        return {"skipped": f"{pr} at {head[:12]} carries no commit {view['baseRefName']} lacks"}
    body = directory / "record.md"
    if found := carry(shell, job, own):
        lines = carried_lines(job, found)
        body.write_text("\n".join(text for text, _ in lines) + f"\n\npatch-id {found['patch']} at both heads.\n\n#{found['grade']['seq']}: {found['grade']['text']}\n")
        return {"verdict": "carried", "from": found["head"], "posted": post(shell, job, lines, body)}
    rows = directory / "rows.md"
    rows.write_text(shell.run(finder.rulings(job, "slice", "--required", "--pr", pr, "--head", head, "--base", view["baseRefName"]), timeout=finder.SLICE_SECONDS))
    keys = finder.ROW_KEY.findall(rows.read_text())
    touched = finder.hunks(finder.git(shell, job, "diff", "--unified=0", "--no-color", "--no-ext-diff", f"{own[0]}^", head))
    question = directory / "question-1.md"
    question.write_text(prompt(job, view, base, own, rows, shell.run(finder.rulings(job, "plan")).strip(), touched))
    reply = ask(shell, job, directory, question, 1)
    if short := shortfalls(keys, reply):
        retry = directory / "question-2.md"
        retry.write_text(f"{question.read_text()}\nYour last reply gave {'; and '.join(short)}. Every key in the rows file appears in `checked`.\n")
        reply = ask(shell, job, directory, retry, 2)
        if short := shortfalls(keys, reply):
            raise finder.JobFailed(f"two replies gave {'; and '.join(short)}")
    if (now := finder.view_pr(shell, job)["headRefOid"]) != head:
        return {"skipped": f"{pr} moved to {now[:12]} while codex read {head[:12]}"}
    lines = graded_lines(job, reply := unsettled(job, reply))
    body.write_text("\n".join(text for text, _ in lines) + f"\nchecked {','.join(reply['checked'])} names {'|'.join(reply['names'])}\n\n{json.dumps(reply, indent=2)}\n")
    return {"verdict": reply["verdict"], "posted": post(shell, job, lines, body)}


def main(argv: list[str] | None = None, shell: finder.Shell | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fidelity.py", description=__doc__.split("\n", 1)[0])
    parser.add_argument("dir", type=Path)
    directory = parser.parse_args(argv).dir
    try:
        outcome = run(directory, shell or finder.Shell())
    except Exception as error:
        finder.write_result(directory, {"error": finder.failure(error)})
        raise
    finder.write_result(directory, outcome)
    return 0


if __name__ == "__main__":
    drive.run_installed(__file__)
    sys.exit(main())
