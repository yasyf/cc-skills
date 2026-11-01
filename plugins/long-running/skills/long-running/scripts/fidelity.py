#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
"""The fidelity pipeline's job: one fidelity grade of one drive head, carried with no model call when the head's patch matches a graded head's, else read by codex.

    fidelity.py DIR

STDLIB ONLY. desk-runner.py's orca desk writes `DIR/job.json` for each head it dispatches and starts
this script detached. The job reads the PR, skips a closed PR or a head that is no longer its live
head, and takes the PR's own commits as finder.py does, running `rulings.py plan`, and the slice when no
earlier grade could carry, while the head fetches. When `git patch-id --stable` of those
commits, read for every earlier graded head at once, matches the same number of commits up to an earlier head every fidelity lane's newest grade
of which is ok, or a `missing` the job's `settled` map names, it posts `fidelity: ok <head> carried from
<earlier>` with the earlier head's `old-app:` line and no model call. So does a head whose `git range-diff`
against the newest such earlier head pairs every own commit equal, or amended only by lines the base now
carries, whitespace aside, as finder.py reads a carry: a restack whose conflict resolution moved only
context keeps what root settled, and only a changed own commit opens grading again. A carry whose grade,
or a grade it was carried from, is a `missing` root settled names that decide as `settled by root #<n>`. The carry repeats no exempt phrase when a root `class wrong`
decision answers the carried grade or a grade it was carried from, else each one the newest
fidelity-desk ok grade on the PR says, else each one the carried grade says. Otherwise it fills
`reference/fidelity-job-brief.md` with the head's own hunks, the change to read (the whole PR, or, past the newest
earlier head every lane stands ok at, only what `git range-diff` shows changed since it), its required register rows
as the owner's words verbatim with the board card each tap answered and none of the register's implications
(`rulings.py slice --required --words`), the repository's AGENTS.md rules as the trunk tip has them, the plan, the lane's newest contract
and the PR body, and runs
`codex-ask -m <model> --schema fidelity.schema.json`, with `--incident` when the job is `fast`, in the drive's checkout. A reply that leaves
a row ungraded, keys a `missing` line on anything but a plan line, an owner row or an AGENTS.md line, or gives no
reasoning in `swept` is asked once more; a second such reply fails the job. A reply with any `missing` entry grades
`missing` whatever verdict it names, and posts its `reader` reason to the human fidelity desks as well. A head that moved while
codex read it posts nothing. The question lists every point of a `missing` grade root settled, and a
reply's `missing` entry that repeats one word for word, or cites only code lines a settled point at an earlier head
cites and no change since touched, is dropped; a reply left with none posts ok citing
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
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from functools import cache
from pathlib import Path

from long_running import drive
from long_running import cci
import finder
import findings
from long_running import ledger
from long_running.grades import FIDELITY_MISSING, FIDELITY_OK, graded_head, names_head, newest_finder, standing

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
FINDING_CHARS = 160
OLD_APP_LINE = re.compile(r"^old-app:\s*(unchanged|bug fix|changes)\b:?\s*(?:[0-9a-f]{7,40}\b)?\s*(.*)$", re.IGNORECASE)
CODEX_SECONDS = 3600
PLAN_LINE = re.compile(r"plan:(?:[\w./-]+\.md:)?\d+(?:-\d+)?")
AGENTS_LINE = re.compile(r"(?:[\w.-]+/)*AGENTS\.md:\d+(?:-\d+)?")
SPAN_DASH = re.compile(r"[–—]")


def patch_id(shell: finder.Shell, job: dict, start: str, end: str) -> str:
    """`git patch-id --stable` of `git diff start end`, empty when the diff is empty or does not read."""
    out = shell.run(["sh", "-c", 'git diff "$1" "$2" | git patch-id --stable', "patch-id", start, end], cwd=Path(job["checkout"]), timeout=finder.SLICE_SECONDS)
    return next(iter(out.split()), "")


def candidates(job: dict) -> list[tuple[str, list[dict]]]:
    """Each earlier graded head, newest first, whose every fidelity lane stands at ok, or at a `missing` a root `decide` settled, with those lanes' grades."""
    oks = [grade for grade in sorted(job["grades"], key=lambda grade: grade["seq"], reverse=True) if passes(job, grade)]
    named = dict.fromkeys(head for grade in oks if (head := graded_head(grade["text"].split(maxsplit=2)[2])) and not names_head(head, job["head"]))
    return [(head, stand) for head in named if (stand := standing(job["grades"], job["lanes"], head)) and all(passes(job, grade) for grade in stand)]


def carry(shell: finder.Shell, job: dict, own: list[str], heads: list[tuple[str, list[dict]]]) -> dict | None:
    """The first of `heads` whose last `len(own)` commits have the head's patch, with its lanes' newest grade and the patch id; every head is fetched and read at once."""

    def patch_at(named: str) -> tuple[str, str]:
        full = finder.fetched(shell, job, named)
        return full, patch_id(shell, job, f"{full}~{len(own)}", full) if full else ""

    with ThreadPoolExecutor() as pool:
        reading = pool.submit(patch_id, shell, job, f"{own[0]}^", job["head"])
        patches = list(pool.map(patch_at, [named for named, _ in heads]))
    if not (ours := reading.result()):
        return None
    return next(({"head": full, "grade": max(stand, key=lambda grade: grade["seq"]), "patch": ours} for (full, patch), (_, stand) in zip(patches, heads, strict=True) if patch == ours), None)


def passes(job: dict, grade: dict) -> bool:
    return grade["text"].startswith(FIDELITY_OK) or (grade["text"].startswith(FIDELITY_MISSING) and str(grade["seq"]) in job["settled"])


def exempt_phrases(job: dict, text: str) -> list[str]:
    return [phrase for phrase in job["exempt_grades"] if phrase.casefold() in text.casefold()]


def chain(job: dict, grade: dict) -> list[int]:
    """`grade`'s seq, then the seq of each grade it was carried from, back to the first."""
    by_seq = {record["seq"]: record for record in job["grades"]}
    seqs, record = [], grade
    while record:
        seqs.append(record["seq"])
        record = by_seq.get(int(carried[1])) if (carried := CARRIED_SEQ.search(record["text"])) else None
    return seqs


def carried_class(job: dict, grade: dict) -> str:
    if set(chain(job, grade)) & set(job["corrected"]):
        return ""
    desk = max((record for record in job["grades"] if record["lane"] != LANE and record["text"].startswith(FIDELITY_OK)), key=lambda record: record["seq"], default=grade)
    return "".join(f"{phrase}: " for phrase in exempt_phrases(job, desk["text"]))


def carried(shell: finder.Shell, job: dict, found: dict, by: str, proof: str, body: Path) -> dict:
    """Post `found`'s grade carried to the head, patch-identical `by` git patch-id or git range-diff over its own commits, naming the root `decide` that settled a `missing` it carries."""
    head, earlier, grade = job["head"], found["head"], found["grade"]
    said = carried_class(job, grade)
    settled = f", settled by root #{root}" if (root := next((job["settled"][str(seq)] for seq in chain(job, grade) if str(seq) in job["settled"]), None)) else ""
    lines = [(f"{FIDELITY_OK} {head} {said}carried from {earlier[:7]} (cci #{grade['seq']}), patch-identical by {by} over its own commits{settled}.", [])]
    if (old_app := newest_finder(job["old_apps"], earlier)) and (parts := OLD_APP_LINE.match(old_app["line"])):
        lines.append((f"old-app: {parts[1]} {head[:7]} {parts[2]}".rstrip(), []))
    body.write_text("\n".join(text for text, _ in lines) + f"\n\n{proof}\n\n#{grade['seq']}: {grade['text']}\n")
    return {"verdict": "carried", "from": earlier, "posted": post(shell, job, lines, body)}


def prompt(job: dict, view: dict, base: str, trunk: str, rows: Path, plan: str, touched: dict[str, list[tuple[int, int]]], scope: str) -> str:
    return TEMPLATE.read_text().format(
        repo=job["repo"],
        pr=job["pr"],
        head=job["head"],
        base=view["baseRefName"],
        base_oid=base,
        trunk_oid=trunk,
        checkout=job["checkout"],
        rubric=RUBRIC,
        fetch=f"{job['head']} {base}",
        scope=scope,
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
        root_rulings="".join(f"- root #{ruling['seq']}: {' '.join(ruling['text'].split())}\n" for ruling in job["rulings"]) or "(none)",
    )


def settled_grades(job: dict) -> Iterator[tuple[str, list[str], int]]:
    """Each `fidelity: missing` grade on the PR that a root record settled: its head, its points, and that record."""
    for grade in job["grades"]:
        if grade["text"].startswith(FIDELITY_MISSING) and (root := job["settled"].get(str(grade["seq"]))):
            head, _, points = grade["text"][len(FIDELITY_MISSING) :].split(maxsplit=2)
            yield head, points.split("; "), root


def settled_points(job: dict) -> dict[str, int]:
    """Each point of a `fidelity: missing` grade on the PR that a root record settled, with that record."""
    return {point: root for _, points, root in settled_grades(job) for point in points}


def places(line: str) -> list[tuple[str, int, int]]:
    """The code lines a point cites as `<path>:<line>` or `<path>:<first>-<last>`, a rule's AGENTS.md line aside."""
    return [(path, int(first), int(last or first)) for path, first, last in finder.CITE.findall(SPAN_DASH.sub("-", line)) if "." in path.rpartition("/")[2] and not AGENTS_LINE.fullmatch(f"{path}:{first}")]


def same_file(one: str, other: str) -> bool:
    return one == other or one.endswith(f"/{other}") or other.endswith(f"/{one}")


def settled_place(job: dict, line: str, moved: Callable[[str], dict[str, list[tuple[int, int]]] | None]) -> int | None:
    """The root record that settled a point at an earlier head citing each code line `line` cites, when no change since that head touches those lines (root #271977, #273671)."""
    if not (cited := places(line)):
        return None
    roots = []
    for path, first, last in cited:
        root = next((root for head, points, root in settled_grades(job) for named, start, end in places("; ".join(points)) if same_file(path, named) and start <= last and first <= end and (touched := moved(head)) is not None and not finder.in_diff(f"{path}:{first}-{last}", touched)), None)
        if root is None:
            return None
        roots.append(root)
    return min(roots)


def changed_since(shell: finder.Shell, job: dict, since: str) -> dict[str, list[tuple[int, int]]] | None:
    """The new-side lines each file changed from `since` to the job's head, None when `since` no longer resolves."""
    if not (full := finder.fetched(shell, job, since)):
        return None
    return finder.hunks(finder.git(shell, job, "diff", "--unified=0", "--no-color", "--no-ext-diff", full, job["head"]))


def unsettled(job: dict, reply: dict, moved: Callable[[str], dict[str, list[tuple[int, int]]] | None]) -> dict:
    """`reply` less each `missing` entry that repeats a settled point word for word, cites only code lines a settled point at an earlier head cites and no change since touched, or that the reply marks `settled` by a root record the job carries (root #265931), read as ok citing the settling records when none is left."""
    points = settled_points(job)
    roots = set(points.values()) | {ruling["seq"] for ruling in job["rulings"]}
    if reply["verdict"] != "missing":
        return reply
    settling = {id(entry): points.get(" ".join(entry["line"].split())) or settled_place(job, entry["line"], moved) or (entry["settled"] if entry["settled"] in roots else None) for entry in reply["missing"]}
    if kept := [entry for entry in reply["missing"] if settling[id(entry)] is None]:
        return reply | {"missing": kept}
    cited = ", ".join(f"#{root}" for root in sorted(set(settling.values())))
    return reply | {"verdict": "ok", "missing": [], "summary": f"{reply['summary']} Every missing point is one root settled at {cited}."}


def baseline(job: dict) -> str:
    return job["baseline"] or "the old app as last released"


def grounded(keys: list[str], key: str) -> bool:
    """Whether a `missing` key is a plan line, a line of a doc in the plan's package, a line of an AGENTS.md, or a non-`design/` row of the head's required slice."""
    return bool(PLAN_LINE.fullmatch(key) or AGENTS_LINE.fullmatch(key)) or (key in keys and not key.startswith("design/"))


def shortfalls(keys: list[str], reply: dict) -> list[str]:
    short = []
    if unchecked := [key for key in keys if key not in reply["checked"]]:
        short.append(f"no grade for {', '.join(unchecked)}")
    if ungrounded := [entry["key"] for entry in reply["missing"] if not grounded(keys, entry["key"])]:
        short.append(f"`missing` keys that are neither a plan line `plan:<n>`, a package doc line `plan:<path>:<n>`, an AGENTS.md line `<path>AGENTS.md:<n>`, nor a non-design row of the rows file: {', '.join(ungrounded)}")
    if reply["verdict"] == "missing" and not reply["missing"]:
        short.append("verdict missing with no `missing` entries")
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
        lines += [(f"{READER} {head} {' '.join(reply['reader'].split())}", job["readers"])] if reply["reader"].strip() else []
    else:
        lines = [(f"{READER} {head} {' '.join(reply['reader'].split())}", job["readers"])]
    lines += [(f"touches {key}", []) for key in reply["touches"]]
    grade = "CHANGES" if reply["old_app"]["grade"] == "changes" else reply["old_app"]["grade"]
    return [*lines, (f"old-app: {grade} {head[:7]} baseline {baseline(job)}; {' '.join(reply['old_app']['why'].split())}", [])]


def open_findings(job: dict) -> list[dict[str, str]]:
    entry = next((entry for entry in drive.drives() if drive.cci_drive(entry) == job["drive"]), None)
    if not entry or not (Path(entry["state_dir"]) / findings.STATE_FILE).is_file():
        return []
    return findings.live(findings.Findings(entry, ledger.Shell()).sync(), None, str(job["pr"]))


def ask(shell: finder.Shell, job: dict, directory: Path, question: Path, attempt: int) -> dict:
    out = shell.run(["codex-ask", "-m", job["model"], *(["--incident"] if job["fast"] else []), "--schema", str(SCHEMA), "-s", str(directory / f"codex-{attempt}"), str(question)], cwd=Path(job["checkout"]), timeout=CODEX_SECONDS)
    reply = json.loads(Path(finder.REPLY_FILE.search(out)[1]).read_text())
    return reply | {"verdict": "missing"} if reply["missing"] else reply


def post(shell: finder.Shell, job: dict, lines: list[tuple[str, list[str]]], body: Path) -> list[cci.Posted]:
    pr = f"{job['repo']}#{job['pr']}"
    posted = []
    for index, (text, also) in enumerate(lines):
        to = ",".join(dict.fromkeys(filter(None, [job["lane"], *job["to"], *also])))
        clipped = text if len(text) <= finder.CCI_TEXT else text[: finder.CCI_TEXT - 1] + "…"
        argv = ["cci", "post", "--drive", job["drive"], "--lane", LANE, "--kind", "review", "--pr", pr, "--id", f"fidelity:{pr}:{job['head']}:{index}", "--path", str(body)]
        posted.append(cci.posted(shell.run(finder.logged(body.parent, [*argv, *(["--to", to] if to else []), "--text", clipped, "--json"])))[0])
    return posted


def run(directory: Path, shell: finder.Shell) -> dict:
    job = json.loads((directory / JOB).read_text())
    pr, head = f"{job['repo']}#{job['pr']}", job["head"]
    pool = ThreadPoolExecutor()
    plan = pool.submit(shell.run, finder.rulings(job, "plan"), timeout=finder.PLAN_SECONDS)
    view = finder.view_pr(shell, job)
    if view["state"] == "CLOSED":
        return {"skipped": f"{pr} is closed"}
    if not view["headRefOid"].startswith(head):
        return {"skipped": f"{pr} moved to {view['headRefOid'][:12]}"}
    job["head"] = head = view["headRefOid"]
    body = directory / "record.md"
    if found := open_findings(job):
        keys = ",".join(f"finding/{row['seq']}" for row in found)
        said = "; ".join(f"open finding {row['seq']} from {row['sweep']}, owner {row['owner']}: {' '.join(row['text'].split())[:FINDING_CHARS]}" for row in found)
        lines = [(f"{FIDELITY_MISSING} {head} {keys} {said}", [])]
        body.write_text(f"{lines[0][0]}\n")
        return {"verdict": "missing", "posted": post(shell, job, lines, body)}
    heads = candidates(job)
    slicing = None if heads else pool.submit(finder.slice_rows, shell, job, pr, head, view["baseRefName"], "--words")
    base, trunk = finder.checked_out(shell, job, view)
    if not (own := finder.beyond(shell, job, (base, trunk), head)):
        return {"skipped": f"{pr} at {head[:12]} carries no commit {view['baseRefName']} lacks"}
    if heads and (found := carry(shell, job, own, heads)):
        return carried(shell, job, found, "git patch-id", f"patch-id {found['patch']} at both heads.", body)
    last = finder.fetched(shell, job, heads[0][0]) if heads else ""
    earlier = finder.beyond(shell, job, (base, trunk), last) if last else []
    last = last if earlier else ""
    pairs, changed = finder.range_pairs(shell, job, base, last, earlier, own) if last else ("", [])
    if last and not changed:
        found = {"head": last, "grade": max(heads[0][1], key=lambda grade: grade["seq"])}
        return carried(shell, job, found, "git range-diff", f"`git range-diff {pairs}` pairs every own commit equal, or amended only by lines the base now carries.", body)
    rows = directory / "rows.md"
    rows.write_text((slicing or pool.submit(finder.slice_rows, shell, job, pr, head, view["baseRefName"], "--words")).result())
    keys = finder.ROW_KEY.findall(rows.read_text())
    touched = finder.hunks(finder.git(shell, job, "diff", "--unified=0", "--no-color", "--no-ext-diff", f"{own[0]}^", head))
    question = directory / "question-1.md"
    question.write_text(prompt(job, view, base, trunk, rows, plan.result().strip(), touched, finder.scope(job, own, last, pairs, changed)))
    reply = ask(shell, job, directory, question, 1)
    if short := shortfalls(keys, reply):
        retry = directory / "question-2.md"
        retry.write_text(f"{question.read_text()}\nYour last reply gave {'; and '.join(short)}. Every key in the rows file appears in `checked`.\n")
        reply = ask(shell, job, directory, retry, 2)
        if short := shortfalls(keys, reply):
            raise finder.JobFailed(f"two replies gave {'; and '.join(short)}")
    if (now := finder.view_pr(shell, job)["headRefOid"]) != head:
        return {"skipped": f"{pr} moved to {now[:12]} while codex read {head[:12]}"}
    lines = graded_lines(job, reply := unsettled(job, reply, cache(lambda since: changed_since(shell, job, since))))
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
