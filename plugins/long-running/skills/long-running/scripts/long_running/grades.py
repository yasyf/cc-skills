"""Fidelity grades and finder reviews on a drive's bus, read the one way for desk-runner.py's landing checks and board_rows.py's merge-row gate."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from itertools import pairwise
from pathlib import Path
from zoneinfo import ZoneInfo

from . import cci


LANE_NUMBER = re.compile(r"(.+?)(?:-(\d+))?")
SUCCESSOR_NUMBER = re.compile(r"(?:-\d{1,2})+$")
WITHDRAWS = re.compile(r"\bwithdraws?\b[^.;:\n#]*#(\d+)", re.IGNORECASE)
WITHDRAW_NAMES = re.compile(r"(?<!not )(?<!never )\bwithdr(?:aw|aws|awing|awn|ew)\b[^.;:\n#]*#(\d+)", re.IGNORECASE)
VOIDING_KINDS = frozenset({"withdraw", "correction"})
WITHDRAWN_NAMED = re.compile(r"#(\d+)\b(?=(?:(?!\bnot\b)[^.;:\n])*\bwithdrawn\b)", re.IGNORECASE)
FIDELITY_OK = "fidelity: ok"
FIDELITY_MISSING = "fidelity: missing"
FIDELITY_GRADES = (FIDELITY_OK, FIDELITY_MISSING)
GRADER_LINES = ("fidelity:", "old-app:", "touches ")
PORT_PHRASE = "pure port, no interaction"
TEST_ONLY_PHRASE = "test-only or CI-only, no non-test code"
TEST_ONLY_PHRASES = (TEST_ONLY_PHRASE, "test-only, no non-test code", "CI-only, no non-test code")
FINDER_DESK = "finder-desk"
FINDER_LINE = "finder:"
FINDER_PASS = "ok"
FINDER_FAIL = "major"
FINDER_NAMED = re.compile(r"\bfinder\b", re.IGNORECASE)
FINDER_SAYS = re.compile(r"\bfinder(?:\s*\[\d+\])?:?\s+(ok|lgtm|major|issues)\b", re.IGNORECASE)
FINDER_ISSUES = "issues"
FINDER_LGTM = "lgtm"
FINDER_MAJOR = re.compile(r"\b[1-9]\d* majors?\b", re.IGNORECASE)
OLD_APP = re.compile(r"^old-app:\s*(unchanged|bug fix|changes)\b", re.IGNORECASE | re.MULTILINE)
OLD_APP_PASSES = frozenset({"unchanged", "bug fix"})
FINDER_CLEAN = re.compile(r"\bno (?:majors?|findings)\b|\b0 majors?\b|\bok\b", re.IGNORECASE)
SHORT_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")
GROUND_KEY = re.compile(r"(?:plan(?::(?:[\w./-]+\.md:)?\d+(?:-\d+)?)?|F\d+|(?!design/)[a-z]+/[\w./-]+)")
GO_PAIR = r"#\d+\s+at\s+[0-9a-f]{7,40}\b"
GO_CLAUSE = re.compile(rf"(?:^\s*(?:\d{{1,2}}:\d{{2}}\s*[AP]M\s+)?|[.:;!?]\s+)(?:go(?:\s+(?:port|test-only|ci-only))?|merge)\s+({GO_PAIR}(?:(?:\s*,\s*|\s*,?\s+and\s+){GO_PAIR})*)(?:\s+with\s+(#\d+(?:(?:\s*,\s*|\s*,?\s+and\s+|\s+)#\d+)*))?", re.IGNORECASE)
GO_EXEMPT = re.compile(r"\bgo\s+(port|test-only|ci-only)\b|\bpure (port)\b|\b((?:test|ci)-only)\b", re.IGNORECASE)
GO_HEAD = re.compile(r"#(\d+)\s+at\s+([0-9a-f]{7,40})\b", re.IGNORECASE)
GO_BRACKET = re.compile(r"\[[^\]]*\]")
GO_KINDS = frozenset({"go", "decide", "decision"})
GO_KIND_FLAGS = tuple(f"--kind={kind}" for kind in sorted(GO_KINDS))
PRECONDITION = re.compile(r"\b(?:lands?|merges?) only after\b|\bbefore it (?:lands|merges)\b|\bdo not (?:land|merge) until\b|\bnot for landing\b", re.IGNORECASE)
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n+")
QUOTED_BLOCK = re.compile(r"```.*?(?:```|\Z)|^[ \t]*>[^\n]*", re.DOTALL | re.MULTILINE)
QUOTED_SPAN = re.compile(r"(`+)[^\n]*?\1|\"[^\"\n]*\"|“[^”]*”")
WITHDRAWN = re.compile(r"^\s*withdrawn\b", re.IGNORECASE)
CARRY_SAYS = re.compile(r"(?<!\bnot )\bpatch-identical\b|\btap carries\b|\bno new behaviou?r\b(?! since (?:the |his |the owner['’]s )?tap at)|\btest-only\b|\bpure replay\b", re.IGNORECASE)
CARRY_FORM = re.compile(rf"\bcarried\b|{CARRY_SAYS.pattern}", re.IGNORECASE)
HEAD_ATTR = "head:"
HEAD_MOVE = re.compile(r"\b([0-9a-f]{7,40})\s*\(was ([0-9a-f]{7,40})\)", re.IGNORECASE)
PATCH_IDENTICAL = re.compile(r"(?<!\bnot )\bpatch-identical\b", re.IGNORECASE)
ADDS_BEHAVIOR = re.compile(r"(?<!\bno )(?<!\bwithout )\bnew behaviou?r\b", re.IGNORECASE)
REVIEW_BOT_CHECK = "ai-review"
REVIEW_BOT_HELD = re.compile(r"\bHeld — (?:(\d+) blocking open\b)?")
REVIEW_BOT_RESTACK = "Waiting for restack"
PACIFIC = ZoneInfo("America/Los_Angeles")


def succeeds(name: str, lane: str) -> bool:
    """Whether `name` is a numbered successor of `lane`: the same base with a higher `-<n>` suffix, where a bare name counts as 1."""
    base, number = LANE_NUMBER.fullmatch(lane).groups()
    other, later = LANE_NUMBER.fullmatch(name).groups()
    return other == base and later is not None and int(later) > int(number or 1)


def delegated(lane: str, delegates: list[str]) -> bool:
    """Whether `lane` is one of `delegates` or a numbered successor of one, so a rotated go lane keeps its authority without a config edit."""
    return any(lane == delegate or succeeds(lane, delegate) for delegate in delegates)


def names_head(named: str, sha: str) -> bool:
    return bool(sha) and (sha.startswith(named) or named.startswith(sha))


def missing_grounds(text: str) -> str:
    """The grounds a `fidelity: missing` line names: its first word after the head, the plan item or register row key `rulings.py grade` writes there."""
    words = [word for word in text.removeprefix(FIDELITY_MISSING).split() if word != "at" and not SHORT_SHA.fullmatch(word)]
    return words[0].rstrip(".,:;") if words else ""


def owner_grounded(text: str) -> bool:
    """Whether a `fidelity: missing` line rests on plan text or the owner's words: every comma-joined key it names is a plan line or item, or a register row other than a `design/` row, never a bare word or a cci seq."""
    return all(GROUND_KEY.fullmatch(key) for key in missing_grounds(text).split(",")) if missing_grounds(text) else False


def graded_head(text: str) -> str:
    """The head a grade or finder line names: its first short sha, so no register key it lists after the head reads as one."""
    found = SHORT_SHA.search(text)
    return found[0] if found else ""


def finder_grade(text: str) -> str:
    if said := FINDER_SAYS.search(text):
        return {FINDER_ISSUES: FINDER_FAIL, FINDER_LGTM: FINDER_PASS}.get(said[1].lower(), said[1].lower())
    if FINDER_MAJOR.search(text):
        return FINDER_FAIL
    return FINDER_PASS if FINDER_CLEAN.search(text) else ""


def newest_finder(records: list[dict], sha: str) -> dict | None:
    return max((record for record in records if names_head(record["head"], sha)), key=lambda record: record["seq"], default=None)


def newest_naming(grades: list[dict], sha: str) -> dict | None:
    return max((record for record in grades if (head := graded_head(record["text"])) and names_head(head, sha)), key=lambda record: record["seq"], default=None)


def repo_prs(record: dict, repo: str) -> list[tuple[str, bool]]:
    """Each PR `record` names that may be in `repo`, with whether its ref says so: `owner/repo#N` refs to `repo`, and bare numbers, which name no repo."""
    return [named for ref in record.get("refs", {}).get("prs", []) if (named := repo_pr(ref, repo))]


def repo_pr(ref: int | str, repo: str) -> tuple[str, bool] | None:
    if isinstance(ref, int):
        return str(ref), False
    where = str(ref).rpartition("#")
    return (where[2], True) if where[0].lower() == repo.lower() else None


def head_entries(record: dict, repo: str) -> dict[str, dict] | None:
    """Each PR in `repo` a multi-head `head` record names, to its `commit`, `was` and `note`: one `cci post --head` record carries every head a restack moved, each as the attribute `head:<pr>` = `[<was>..]<sha>[ <note>]`; None for a record with no such attribute."""
    named = [(key.removeprefix(HEAD_ATTR), value) for key, value in ((record.get("fields") or {}).get("attrs") or {}).items() if key.startswith(HEAD_ATTR)]
    if not named:
        return None
    entries = {}
    for ref, value in named:
        moved, _, note = value.partition(" ")
        was, _, commit = moved.rpartition("..")
        if found := repo_pr(int(ref) if ref.isdigit() else ref, repo):
            entries[found[0]] = {"commit": commit.lower(), "was": was.lower(), "note": note}
    return entries


def head_of(record: dict, repo: str, pr: str) -> str:
    """The head a `head` record names for `pr`: its `head:<pr>` attribute's commit, else its text's first short sha."""
    entries = head_entries(record, repo)
    return graded_head(record["text"]) if entries is None else entries.get(pr, {}).get("commit", "")


def head_says(record: dict, repo: str, pr: str) -> str:
    """What `record` says of `pr`'s head: a multi-head `head` record's note for `pr`, else the record's whole text."""
    return ((head_entries(record, repo) or {}).get(pr) or {}).get("note") or full_text(record)


def names_pr(record: dict, pr: str) -> bool:
    return pr in {str(ref).rsplit("#", 1)[-1] for ref in record.get("refs", {}).get("prs", [])} or re.search(rf"#{pr}\b", record["text"]) is not None


def go_heads(record: dict) -> dict[str, str]:
    """The PRs a root `go` lets merge, each with the head it names: only the `#<n> at <sha>` pairs of its first `go`, `go port`, `go test-only`, `go ci-only` or `merge` clause that opens the text, after an optional Pacific clock time such as `1:19 AM`, or opens a sentence or follows a colon, the word then the pairs joined by commas or `and`, so a PR and sha the text mentions outside that clause, a quoted or negated go, and a record opening `withdrawn` are never a go."""
    if WITHDRAWN.match(record["text"]):
        return {}
    clause = GO_CLAUSE.search(record["text"])
    return {pr: sha.lower() for pr, sha in GO_HEAD.findall(clause[1])} if clause else {}


def is_go(record: dict) -> bool:
    """Whether `record` is a go, as every reader of the bus counts one: a `go` record, or a `decide` or `decision` whose text carries a go clause (root #243333)."""
    return record["kind"] == "go" or record["kind"] in GO_KINDS and bool(go_heads(record))


def go_batch(record: dict) -> set[str]:
    """The PRs a root `go` binds into one enqueue: every PR of its clause and each `#<n>` of the `with` list that follows the clause, or none when it has no `with` list."""
    clause = None if WITHDRAWN.match(record["text"]) else GO_CLAUSE.search(record["text"])
    return set(go_heads(record)) | set(re.findall(r"#(\d+)", clause[2])) if clause and clause[2] else set()


def carried_gos(gos: list[dict], grades: list[dict], repo: str, lands: Callable[[str, str], bool]) -> list[dict]:
    """The gos a fidelity desk carries: a root go naming a PR at head G and a fidelity grade of H on that PR that names G and `carries`, such as `fidelity: ok H carried: patch-identical to G` or `fidelity: ok H, tap carries (399b5fa): pure replay of G`, make a go at H when `lands(pr, H)`, and so on along each chain of carries, so a replayed head never waits for root to post its go again (root #136770, #143450). A carried go keeps its source go's seq, time, `with` batch and port, test-only or CI-only claim, so it never reads as a newer root decision or frees a PR from its batch, and records its grade's time as `graded_at`, when the carried head became ready to queue."""
    gone = defaultdict(dict)
    for go in gos:
        for pr, sha in go_heads(go).items():
            if pr not in go.get("voided", ()):
                gone[pr][(pr, sha)] = go

    @cache
    def parsed(text: str) -> tuple[str, list[str]] | None:
        return carry_of(text) if carries(text) else None

    carried: list[dict] = []
    while fresh := [
        (pr, found[0], source, grade["at"])
        for grade in grades
        if (found := parsed(grade["text"]))
        for pr, _ in repo_prs(grade, repo)
        if (source := next(filter(None, (reached(gone[pr], pr, earlier) for earlier in found[1])), None)) and not reached(gone[pr], pr, found[0]) and lands(pr, found[0])
    ]:
        for pr, head, source, graded_at in fresh:
            go = carried_go(source, pr, head, graded_at)
            gone[pr][(pr, head)] = go
            carried.append(go)
    return carried


def carried_go(source: dict, pr: str, head: str, graded_at: str) -> dict:
    """The go `source` makes for `pr` at `head` once a grade posted at `graded_at` carried it there, keeping its exempt word, its bracketed conditions and the PRs it binds `pr` with."""
    bound = " ".join(f"#{mate}" for mate in sorted(go_batch(source) - {pr}, key=int))
    exempt = f"{next(filter(None, said.groups())).lower()} " if (said := GO_EXEMPT.search(source["text"])) else ""
    brackets = "".join(f" {bracket}" for bracket in GO_BRACKET.findall(full_text(source)))
    return {"seq": source["seq"], "at": source["at"], "drive": source["drive"], "lane": source["lane"], "kind": "go", "text": f"go {exempt}#{pr} at {head}" + (f" with {bound}" if bound else "") + brackets, "refs": {"prs": [int(pr)]}, "voided": set(), "graded_at": graded_at}


def fidelity_carries(grades: list[dict]) -> list[tuple[str, str]]:
    """Each `(H, G)` a carry grade of H naming G states, unless it says the change adds new behavior: the desk read H as G's change, so G's finder verdict stands for H (root #139077)."""
    return [(found[0], earlier) for grade in grades if not ADDS_BEHAVIOR.search(grade["text"]) and (found := carry_of(grade["text"])) for earlier in found[1]]


def head_moves(found: list[dict], drive: str, lanes: list[str], repo: str, pr: str) -> list[tuple[str, str, bool, int]]:
    """Each move of `pr` from an old head to a new one that a record of `found` states, as `(new, old, patch-identical, seq)`: a `head` record's `<new> (was <old>)`, patch-identical when the words after it, up to the end of its sentence or the next move, say so and name no new behavior, or when a fidelity lane's grade of `<new>` that names no old head calls it patch-identical; and each `(new, old)` a fidelity lane's carry grade states, patch-identical when `carries` says so."""
    moves = []
    mine = [record for record in found if record["drive"] == drive and about_pr(record, repo, pr)]
    graded = [graded_head(record["text"][len(FIDELITY_OK) :]) for record in mine if record["kind"] == "review" and record["lane"] in lanes and record["text"].startswith(FIDELITY_OK) and identical(record["text"])]
    for record in mine:
        text = record["text"]
        if record["kind"] == "head" and (entries := head_entries(record, repo)) is not None:
            if (entry := entries.get(pr)) and entry.get("was"):
                said = head_says(record, repo, pr)
                moves.append((entry["commit"], entry["was"], bool(PATCH_IDENTICAL.search(said)) and not ADDS_BEHAVIOR.search(said) or any(names_head(sha, entry["commit"]) for sha in graded), record["seq"]))
        elif record["kind"] == "head":
            named = list(HEAD_MOVE.finditer(text))
            for move, after in pairwise([*named, None]):
                said = SENTENCE_BREAK.split(text[move.start() : after.start() if after else len(text)], maxsplit=1)[0]
                moves.append((move[1].lower(), move[2].lower(), bool(PATCH_IDENTICAL.search(said)) and not ADDS_BEHAVIOR.search(said) or any(names_head(sha, move[1]) for sha in graded), record["seq"]))
        elif record["kind"] == "review" and record["lane"] in lanes and (found := carry_of(text)):
            moves.extend((found[0], earlier, carries(text), record["seq"]) for earlier in found[1])
    return moves


def identical(grade: str) -> bool:
    """Whether a fidelity grade calls its head patch-identical in its first sentence and names no new behavior anywhere."""
    return bool(PATCH_IDENTICAL.search(SENTENCE_BREAK.split(grade, maxsplit=1)[0])) and not ADDS_BEHAVIOR.search(grade)


def carry_of(text: str) -> tuple[str, list[str]] | None:
    """The head a `fidelity: ok <new>` grade carries to and every other head it names, when it says carried, patch-identical, tap carries, no new behavior, test-only, or pure replay, in whatever words surround them."""
    if not text.startswith(FIDELITY_OK) or not CARRY_FORM.search(text) or not (new := graded_head(text[len(FIDELITY_OK) :]).lower()):
        return None
    earlier = list(dict.fromkeys(sha.lower() for sha in SHORT_SHA.findall(text) if not names_head(sha.lower(), new)))
    return (new, earlier) if earlier else None


def carries(grade: str) -> bool:
    """Whether a carry grade's first sentence says patch-identical, tap carries, no new behavior, test-only, or pure replay, and it adds no new behavior anywhere, so a go or tap at the head it names stands at its new head; a bare `carried from` carries only a finder verdict."""
    return bool(CARRY_SAYS.search(SENTENCE_BREAK.split(grade, maxsplit=1)[0])) and not ADDS_BEHAVIOR.search(grade)


def patch_chain(moves: list[tuple[str, str, bool, int]], head: str, tapped: str) -> list[int]:
    """The seqs of the records whose patch-identical moves of `moves` lead from `tapped` to `head`, newest first, or none when no such chain stands."""
    frontier, seen = [(head, [])], set()
    while frontier:
        at, seqs = frontier.pop()
        for new, old, identical, seq in moves:
            if not identical or not names_head(new, at) or old in seen:
                continue
            if names_head(old, tapped):
                return [*seqs, seq]
            seen.add(old)
            frontier.append((old, [*seqs, seq]))
    return []


def heads_since(moves: list[tuple[str, str, bool, int]], head: str, tapped: str) -> list[str]:
    """The heads the moves of `moves`, patch-identical or not, pass through from `tapped` to `head`, oldest first and ending at `head`; only `head` when no chain of moves joins them."""
    frontier, seen = [(head, [head])], set()
    while frontier:
        at, path = frontier.pop()
        for new, old, _, _ in moves:
            if not names_head(new, at) or old in seen:
                continue
            if names_head(old, tapped):
                return path[::-1]
            seen.add(old)
            frontier.append((old, [*path, old]))
    return [head]


def reached(gone: dict[tuple[str, str], dict], pr: str, sha: str) -> dict | None:
    return next((go for (named, head), go in gone.items() if named == pr and names_head(head, sha)), None)


def goes(record: dict, pr: str) -> bool:
    return pr in go_heads(record) and pr not in record.get("voided", ())


def precondition_sentences(text: str) -> list[str]:
    """Each sentence of `text` that holds a landing back on something outside the PR in so many words: `lands only after`, `merge only after`, `before it lands`, `do not land until`, or `not for landing`, said outside quotes, code and block quotes, so a text that cites the phrases never reads as saying them."""
    text = QUOTED_BLOCK.sub("", text)
    masked = QUOTED_SPAN.sub(lambda span: "\0" * len(span[0]), text)
    cuts = [0, *(cut for found in SENTENCE_BREAK.finditer(masked) for cut in found.span()), len(text)]
    return [" ".join(text[start:end].split()) for start, end in zip(cuts[::2], cuts[1::2]) if PRECONDITION.search(masked[start:end])]


def owes(record: dict, lane: str) -> bool:
    """Whether a record addressed to `lane` waits on its answer: an `ask`, or a `decide` or `correction` whose text gives `lane` or a numbered successor an order as `<lane>: ...`; a lane only listed as a recipient owes nothing."""
    if record["kind"] == "ask":
        return True
    return record["kind"] in ("decide", "correction") and re.search(rf"(?<![\w-]){re.escape(lane)}(?:-\d+)?:", full_text(record)) is not None


def full_text(record: dict) -> str:
    """The whole text of `record`: the body file cci spilled it to when it ran past cci's text limit, else its text, as when its path names an attachment such as a tarball rather than a text body (#207472 stopped every stall pass for 3.5 hours on Oct 30)."""
    spilled = Path(record.get("refs", {}).get("path") or "")
    if not spilled.is_file():
        return record["text"]
    try:
        with spilled.open() as body:
            return "".join(iter(lambda: body.read(64 * 1024), ""))
    except UnicodeDecodeError:
        return record["text"]


def gone_at(gos: list[dict], pr: str, head: str) -> list[dict]:
    """The root `go` records of `gos` whose opening clause names `pr` at `head` by a short sha."""
    return [record for record in gos if (named := go_heads(record).get(pr)) and names_head(named, head) and pr not in record.get("voided", ())]


@dataclass(frozen=True)
class GoState:
    """Where a PR's go stands at its head: the newest go naming that head, a root go or one a carry brought there, else the newest go naming another head of the PR."""

    pr: str
    go: dict | None
    older: dict | None

    def said(self) -> str:
        """The go state in words that cite the go's seq, the one text every desk says a PR has, lacks, or lost a go with."""
        if self.go:
            return f"go #{self.go['seq']} by {self.go['lane']} at {clock(self.go['at'])}" + (", carried to this head" if "graded_at" in self.go else "")
        if self.older:
            return f"no go names this head; the newest, go #{self.older['seq']} by {self.older['lane']} at {clock(self.older['at'])}, names {go_heads(self.older)[self.pr][:10]}"
        return "no go names this head"


def go_state(gos: list[dict], pr: str, head: str) -> GoState:
    """The `GoState` of `pr` at `head` over `gos`, root gos with their `voided` PRs and the gos carries made, as `desk-runner.py`'s `root_gos` reads them."""
    newest = max(gone_at(gos, pr, head), key=lambda go: (go["seq"], "graded_at" not in go), default=None)
    older = None if newest else max((go for go in gos if pr in go_heads(go) and pr not in go.get("voided", ())), key=lambda go: go["seq"], default=None)
    return GoState(pr, newest, older)


def clock(stamp: str) -> str:
    return datetime.fromisoformat(stamp).astimezone(PACIFIC).strftime("%-I:%M %p")


def with_successors(lanes: list[str], records: list[dict]) -> list[str]:
    """`lanes` plus every lane posting in `records` that succeeds one of them, such as `fidelity-desk-13` for `fidelity-desk-12`, so a successor counts with no config edit."""
    family = re.compile("|".join(rf"{re.escape(SUCCESSOR_NUMBER.sub('', lane))}(?:-\d{{1,2}})*" for lane in lanes))
    return list(dict.fromkeys([*lanes, *sorted({record["lane"] for record in records if family.fullmatch(record["lane"])})]))


def closes(record: dict) -> set[int]:
    """The seqs `record` closes: each it resolves, and for a `withdraw`, the one it answers by `--re` and each it names after "withdraw", such as `root withdraws go #198635`."""
    closed = cci.resolved(record)
    if record["kind"] != "withdraw":
        return closed
    return closed | ({record["re"]} if record.get("re") else set()) | {int(seq) for seq in WITHDRAWS.findall(record["text"])}


def voids(record: dict) -> set[int]:
    """The records `record` voids: the one a `withdraw` or `correction` answers by `--re`, and each `#<seq>` any record names after a form of "withdraw", such as `withdrawing go #229223` (root #232136)."""
    named = {int(seq) for seq in WITHDRAW_NAMES.findall(record["text"])}
    return named | {record["re"]} if record.get("re") and record["kind"] in VOIDING_KINDS else named


def fidelity_grades_in(reviews: list[dict], drive: str, lanes: list[str], repo: str, pr: str) -> list[dict]:
    """Every grade a fidelity lane of `lanes` posted about `pr` among `reviews`, by an `owner/repo#N` reference to `repo` or a bare number."""
    return [record for record in reviews if record["kind"] == "review" and record["drive"] == drive and record["lane"] in lanes and record["text"].startswith(FIDELITY_GRADES) and pr in dict(repo_prs(record, repo))]


def standing(grades: list[dict], lanes: list[str], sha: str) -> list[dict]:
    """Each fidelity lane's newest grade naming `sha`."""
    return [grade for lane in lanes if (grade := newest_naming([record for record in grades if record["lane"] == lane], sha))]


def about_pr(record: dict, repo: str, pr: str) -> bool:
    """Whether `record` is about `pr`: its refs name it, or it carries no PR ref and its text names `#<pr>` or `[<pr>]`."""
    if record.get("refs", {}).get("prs"):
        return pr in dict(repo_prs(record, repo))
    return re.search(rf"#{pr}\b|\[{pr}\]", record["text"]) is not None


def finder_desk_line(record: dict) -> bool:
    """Whether `record` is a finder grade: a line the finder desk, or a numbered successor of it, posted opening `finder:`, never a lane's reply that names a finder record (root #202594)."""
    return delegated(record["lane"], [FINDER_DESK]) and record["text"].startswith(FINDER_LINE)


def finder_reviews_in(found: list[dict], drive: str, repo: str, pr: str) -> list[dict]:
    """Every review in `found` about `pr` that names the finder, the history a finder pass reads, with the verdict a finder grade reads as, empty for a lane's reply, and the head it names, or the head the newest `head` record about `pr` before it posted."""
    records = [record for record in found if record["drive"] == drive and record["kind"] in ("review", "head") and about_pr(record, repo, pr)]
    heads = [record for record in records if record["kind"] == "head"]
    reviews = []
    for record in records:
        if record["kind"] != "review" or not FINDER_NAMED.search(record["text"]) or record["text"].startswith(GRADER_LINES):
            continue
        reviews.append(record | {"grade": finder_grade(record["text"]) if finder_desk_line(record) else "", "head": head_named(record, heads, repo, pr)})
    return reviews


def finder_records(reviews: list[dict]) -> list[dict]:
    return [record for record in reviews if record["grade"] and record["head"]]


def head_named(record: dict, heads: list[dict], repo: str, pr: str) -> str:
    """The head `record` names: its first short sha, or the head the newest `head` record of `heads` posted before it names for `pr`."""
    posted = max((head for head in heads if head["seq"] < record["seq"]), key=lambda head: head["seq"], default=None)
    return graded_head(record["text"]) or (head_of(posted, repo, pr) if posted else "")


def old_app_grades_in(found: list[dict], drive: str, lanes: list[str], repo: str, pr: str) -> list[dict]:
    """Every `old-app:` grade a fidelity lane of `lanes` posted about `pr`, on a line of its own in a review or in the body a fidelity grade spilled to, with the grade it reads as and the head it names (root #140437)."""
    records = [record for record in found if record["drive"] == drive and record["kind"] in ("review", "head") and about_pr(record, repo, pr)]
    heads = [record for record in records if record["kind"] == "head"]
    return [
        record | {"grade": said[1].lower(), "head": head}
        for record in records
        if record["kind"] == "review" and record["lane"] in lanes and (said := OLD_APP.search(full_text(record))) and (head := head_named(record, heads, repo, pr))
    ]


def withdrawn(record: dict) -> set[int]:
    """The records `record` withdraws: the one it answers by `--re` when it opens with "withdrawn", and each `#<seq>` it names with "withdrawn" later in the same clause and no "not" between."""
    named = {int(seq) for seq in WITHDRAWN_NAMED.findall(record["text"])}
    return named | {record["re"]} if record.get("re") and WITHDRAWN.match(record["text"]) else named


def review_bot_wait(title: str, lane: str) -> str:
    """What the title of the review bot's newest `ai-review` run on a green head says the PR waits on, naming `lane` to fix or accept a Held verdict's findings; empty for any other verdict."""
    if REVIEW_BOT_RESTACK in title:
        return "the review bot waits for a restack push"
    if not (held := REVIEW_BOT_HELD.search(title)):
        return ""
    findings = f"{held[1]} blocking findings" if held[1] else title[held.end() :].strip() or "blocking findings"
    return f"the review bot holds it on {findings}; {lane or 'its lane'} fixes or accepts them"
