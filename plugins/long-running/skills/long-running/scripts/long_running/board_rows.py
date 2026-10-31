"""The merge-row gate: every merge row the owner sees names its PR's current head, carries what he needs to decide, in words written after that head, and keeps his verdict.

    board_rows.py --config RUNNER_JSON --check [--json] [--session ID] [--finders DIR] BLOCK_JSON
    board_rows.py --config RUNNER_JSON [--json] [--session ID] [--finders DIR] BLOCK_JSON -- cc-present update-block|push [ARGS...]

STDLIB ONLY. `--config` is the landing runner's config, which names the drive, `landing.repo`,
`landing.fidelity_lane`, and the drive's PR ledger, `landing.ledger` in `landing.checkout`. A row is a triage item whose id is `merge-row-<pr>[-<sha7>]`, anywhere in
the block, its head the `commit <sha>` its hint names; or a `pr-groups` row, its id its PR number
and its head its `head`. Its `textAt` is the RFC 3339 time the writer last wrote its words. Each row
fails, with one line `<row-id> <pr> <head> <reasons>`, when:

    stale     its head is not the PR's head on GitHub; the reason names the new head
    old text  its `textAt` is missing or not newer than its head's commit time
    not open  the PR is closed or merged on GitHub, or a root go, decide or decision record has it
              as the subject of superseded, closed or landed, or a verb that closes or supersedes
              it, naming that record's seq, and no later root go names it at its head
    missing   a triage row's label has no words after `#<pr>:`, or a fact in `FIELDS` is absent or
              empty: what it changes for users, how it differs from the plan, its risk, the
              finder's verdict, CI, and what gates its landing; a `pr-groups` row's field in
              `PR_FIELDS` is absent or empty, `review` carrying the finder and CI
    moved     the board's outcomes record a verdict on its id under another block, and none
              under this one
    renamed   the board's outcomes record a verdict on another id for its PR that this push drops,
              none on this id, and no root record that re-keys names this id or its head; a
              `pr-groups` row clears it by `cc-present verdict import` at the head the owner tapped
    approved  a `go` from root, or from a lane the config's `landing.go_lanes` names, names the PR at its head, no root `withdraw` closes that go, and the board
              records no approval for the row at that head, so the row asks a tap he already gave;
              listing his approved head in the row's `carried` list (root #139021), `cc-present verdict
              import` at the head, or dropping the row clears it
    re-ask    the board records his approval at another head, and the landing runner has posted no
              `TAP-NOT-CARRIED` note for the PR at this head, its tap judge's finding that the head
              changes what he approved (root #138721): until then the row sits in Being fixed or checked
    since     the board records his approval at another head that its `carried` list does not name,
              and the `pr-groups` row's `since` lacks `changed`, what changed since his tap in plain
              words, or `because`, the behavior change it is shown again for (root #194315)
    rejected  the board records his reject for the row at its head, so the row asks again what he
              refused; it returns only at a new head
    landed    its blocker, the `blocker` field or the `Gate` fact, names a PR GitHub shows merged
    one tap   a row's or its group's words say one tap approves several PRs, and one they name or
              group with it is rejected on the board or no longer open on GitHub
    unledgered  the drive's PR ledger has not admitted the PR, so no tap on it can ever land; the
              lane that shipped it runs `ledger.py register --pr <pr>` and the row returns

Two `pr-groups` rows in different groups whose titles share more than half the words of the shorter
title, counting words of four letters or more, both fail with `alike`: he reads them as duplicates
(root #138624), so they belong in one group with titles that name each piece.

While the landing runner's newest record on the `dev-red` cci topic reads DEV-RED, the gate prints
one `BANNER` line and the push form opens each `pr-groups` block's prompt with it: `dev's test build
is red since <time>; nothing lands until #<fix> fixes it.` Every row stays on the board and is
checked as at any other time. A topic that does not read prints a `WARN` line and no banner.

A markdown block of the pushed document that lists a PR as a `- #<pr>:` bullet fails as
`<block>#<pr>`, with `listed`, when GitHub shows that PR merged or closed: a landed PR leaves every
list the owner reads in the same push. In a markdown block whose id says `approved`, an open listed
PR also fails, with `unapproved`, when neither a standing root go nor his board approval, a carry
included, names its GitHub head, or when no fidelity grade names that head.

A row asks a tap only once a fidelity lane graded its head and its own words settle it: a row
with no fidelity grade at its head fails with `ungraded`, and one whose words say `pending` or
`being reworked` in a sentence that names no commit, or names the row's head, fails with
`unsettled` (root #138512, #138550): a sentence naming an older commit describes that commit, so a
newer graded head clears it. A row with no finder review at its head
gets a `WARN` line and fails nothing. Grades and finder reviews are read from one
scan of the drive's `review` and `head` records, as desk-runner.py reads them, whatever verdict a
finder posted; a codex finder reply saved as `<finders dir>/<pr>-<sha7>.md` counts as a finder
review too. `--finders` names that directory, `finders/` beside the config by default. Two rows whose first sentences of five words or more, the label when there is none, share
a token Jaccard above 0.8 or their first 60 characters are flagged together as `FLAG` lines for the
root; a flag fails nothing. A GitHub, cci, ledger, or cc-present read that fails prints a `WARN` line and
fails no row on the checks it would have read. The verdicts are read with `cc-present outcomes`
for `--session`, else the push command's `--session`, else cc-present's default session. A last
line counts rows, failing rows, ungraded rows, and flags. Any failing row exits 1, and `--check` exits 0
otherwise. The push form opens each re-asked `pr-groups` row's `change` with `You approved this at <his
tap's Pacific time>. Since then: <changed>. Shown again because: <because>.` and drops every row's
`since`. Rows of one `pr-groups` block that GitHub chains into one stack, each PR based on the
branch of the one below it, push as one row at the tip when every one of them passes, asks again
with its `since`, and moved only by a fix with no new behavior: the landing runner's `TAP-KEPT` ask
names the PR at its head (root #194387). The row lists the others as cc-present's `stack`, so his one
tap decides each at its own head; its `change` opens with one line for the stack, then each PR's
change, and each other fact reads as one line when every PR shares it, else one clause per PR. It runs its command with a copy of the block, `textAt` removed and each failing
row left out (its triage item, its `pr-groups` entry, or its `- #<pr>:` line, and a triage or `pr-groups` block
left empty), as its last argument, so one refused row never holds the rest of the board, and exits 1 when it left a
row out, else with the command's status. When every row fails it pushes nothing. A one-tap claim that fails marks
every row it covers. `--json` prints it all as one object.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import cci
from . import ghread
from . import grades

ROW_ID = re.compile(r"^merge-row-(?P<pr>\d+)(?:-(?P<sha>[0-9a-f]{7,40}))?$")
ROW_HEAD = re.compile(r"\bcommit (?P<sha>[0-9a-f]{7,40})\b")
SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
WORD = re.compile(r"[a-z0-9]+")
LABEL_NUMBER = re.compile(r"^#\d+:\s*")
SAVED_FINDER = re.compile(r"^(?P<pr>\d+)-(?P<sha>[0-9a-f]{7,40})\.md$")
FINDERS = "finders"
RULING_KINDS = cci.ROOT_ANSWER_KINDS | {"answer"}
TAP_VERDICT = re.compile(r"^(?P<label>TAP-NOT-CARRIED|TAP-KEPT) #(?P<pr>\d+): the owner's tap at (?P<tapped>[0-9a-f]{7,40}) .*? carry to (?P<head>[0-9a-f]{7,40})\b")
REKEY = re.compile(r"\bre-?keys?\b|\bre-?keyed\b", re.IGNORECASE)
ONE_TAP = re.compile(r"\bone tap\b[^.]*?\bapprov(?:es|e)\b|\bapprov(?:es|e) (?:all|both|every)\b(?: (?:\d+|two|three|four|five|six|seven|eight|nine|ten))?", re.IGNORECASE)
PR_REF = re.compile(r"#(\d+)\b")
APPROVED_LIST = "approved"
TITLE_WORD = 4
UNSETTLED = re.compile(r"\bpending\b|\bbeing reworked\b", re.IGNORECASE)
LISTED = re.compile(r"^\s*[-*] #(\d+):", re.MULTILINE)
MERGED = "MERGED"
FIELDS = ("For users", "Differs from plan", "Risk", "Finder", "Build (CI)", "Gate")
PR_FIELDS = ("change", "plan", "risk", "review", "blocker")
PR_GROUPS = "pr-groups"
TEXT_AT = "textAt"
PRS_PER_QUERY = 40
SENTENCE_WORDS = 5
SAME_PREFIX = 60
SAME_JACCARD = 0.8
OPEN = "OPEN"
PACIFIC = ZoneInfo("America/Los_Angeles")
SINCE = "since"
OWNER_LANE = "board-watch"
RUNNER_LANE = "desk-runner"
SNAPSHOT_TOPIC = "board-rows"
WRITE_TOPIC = "board-write"
MERGE_CARD = re.compile(r"^merge:(\d+):([0-9a-f]{7,40})$")


@dataclass(frozen=True)
class Done:
    code: int
    out: str
    err: str


class Shell:
    """The single side-effect boundary: gh, cci, cc-present, the push command, and blob reads."""

    def run(self, argv: list[str], env: dict[str, str] | None = None) -> Done:
        done = cci.installed(argv, lambda: subprocess.run(argv, capture_output=True, text=True, env={**os.environ, **env} if env else None))
        return Done(done.returncode, done.stdout, done.stderr)

    def push(self, argv: list[str]) -> int:
        return subprocess.run(argv).returncode

    def read(self, path: str) -> str | None:
        try:
            return Path(path).read_text(errors="replace")
        except OSError:
            return None

    def files(self, directory: Path) -> list[str]:
        return [path.name for path in directory.iterdir()] if directory.is_dir() else []


class ReadFailed(Exception):
    pass


@dataclass
class Row:
    id: str
    block: str
    pr: str
    head: str
    text_at: str
    label: str
    sentence: str
    facts: dict[str, str]
    words: str = ""
    group: str = ""
    carried: tuple[str, ...] = ()
    required: tuple[str, ...] = FIELDS
    since: dict[str, str] = field(default_factory=dict)
    opener: str = ""
    entry: dict | None = None
    pure: bool = False
    pull: Pull | None = None
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Pull:
    state: str
    head: str
    committed: str
    base: str = ""
    branch: str = ""


def triage_items(node: object) -> Iterator[tuple[str, dict]]:
    if isinstance(node, dict):
        if node.get("type") == "triage":
            yield from ((node["id"], item) for item in node.get("items", []))
        for value in node.values():
            yield from triage_items(value)
    elif isinstance(node, list):
        for value in node:
            yield from triage_items(value)


def pr_group_rows(node: object) -> Iterator[tuple[str, dict]]:
    if isinstance(node, dict):
        if node.get("type") == PR_GROUPS:
            yield from ((node["id"], group.get("id", ""), pr) for group in node.get("groups", []) for pr in group.get("prs", []))
        for value in node.values():
            yield from pr_group_rows(value)
    elif isinstance(node, list):
        for value in node:
            yield from pr_group_rows(value)


def first_sentence(md: str, fallback: str) -> str:
    sentences = [part.strip() for part in SENTENCE.split(md.strip())]
    return next((part for part in sentences if len(WORD.findall(part.lower())) >= SENTENCE_WORDS), fallback)


def rows_of(block: dict) -> list[Row]:
    rows = []
    for block_id, item in triage_items(block):
        if not (named := ROW_ID.match(item.get("id", ""))):
            continue
        head = ROW_HEAD.search(item.get("hint", ""))
        facts = {fact["label"]: str(fact.get("value", "")).strip() for fact in item.get("facts", [])}
        label = item.get("label", "")
        words = "\n".join([label, item.get("hint", ""), item.get("md", ""), *facts.values()])
        rows.append(Row(item["id"], block_id, named["pr"], head["sha"] if head else "", item.get(TEXT_AT, ""), label, first_sentence(item.get("md", ""), LABEL_NUMBER.sub("", label)), facts, words))
    for block_id, group, pr in pr_group_rows(block):
        facts = {name: str(pr.get(name, "")).strip() for name in PR_FIELDS}
        words = "\n".join([pr.get("title", ""), pr.get("md", ""), *facts.values()])
        rows.append(Row(str(pr["number"]), block_id, str(pr["number"]), pr.get("head", ""), pr.get(TEXT_AT, ""), pr.get("title", ""), first_sentence(facts["change"], facts["change"]), facts, words, group, tuple(pr.get("carried") or ()), PR_FIELDS, pr.get(SINCE) or {}))
    return rows


def group_claims(node: object) -> Iterator[tuple[str, str, str, set[str]]]:
    """Each pr-groups group whose title or summary says one tap approves several PRs: its block, its first PR, the words, and every PR it groups or names."""
    if isinstance(node, dict):
        if node.get("type") == PR_GROUPS:
            for group in node.get("groups", []):
                words = f"{group.get('title', '')}\n{group.get('summary', '')}"
                prs = [str(pr["number"]) for pr in group.get("prs", [])]
                if prs and ONE_TAP.search(words):
                    yield node["id"], prs[0], words, set(prs) | set(PR_REF.findall(words))
        for value in node.values():
            yield from group_claims(value)
    elif isinstance(node, list):
        for value in node:
            yield from group_claims(value)


def listed_prs(node: object) -> Iterator[tuple[str, str]]:
    if isinstance(node, dict):
        if node.get("type") == "markdown":
            yield from ((node["id"], pr) for pr in LISTED.findall(node.get("md", "")))
        for value in node.values():
            yield from listed_prs(value)
    elif isinstance(node, list):
        for value in node:
            yield from listed_prs(value)


def title_words(row: Row) -> set[str]:
    return {word for word in WORD.findall(row.label.lower()) if len(word) >= TITLE_WORD}


def alike(rows: list[Row]) -> Iterator[tuple[Row, Row]]:
    for one, other in itertools.combinations([row for row in rows if row.group], 2):
        mine, theirs = title_words(one), title_words(other)
        if one.group != other.group and mine and theirs and len(mine & theirs) * 2 > min(len(mine), len(theirs)):
            yield one, other


def blocker_prs(row: Row) -> set[str]:
    return set(PR_REF.findall(row.facts.get("blocker" if row.required == PR_FIELDS else "Gate", ""))) - {row.pr}


def missing_fields(row: Row) -> list[str]:
    absent = [] if row.required == PR_FIELDS or LABEL_NUMBER.sub("", row.label).strip() else ["label"]
    return absent + [name for name in row.required if not row.facts.get(name)]


def pr_of(row_id: str) -> str | None:
    if row_id.isdigit():
        return row_id
    return named["pr"] if (named := ROW_ID.match(row_id)) else None


def pulls_query(repo: str, prs: list[str]) -> str:
    owner, name = repo.split("/")
    fields = " ".join(f"p{pr}: pullRequest(number: {pr}) {{ state headRefOid baseRefName headRefName commits(last: 1) {{ nodes {{ commit {{ committedDate }} }} }} }}" for pr in prs)
    return f'query {{ repository(owner: "{owner}", name: "{name}") {{ {fields} }} }}'


def read_pulls(shell: Shell, repo: str, prs: list[str], warnings: list[str]) -> dict[str, Pull | None]:
    """Each PR's state, head, and head commit time from GitHub, None for one GitHub does not know; a refused read leaves its PRs out and warns."""
    pulls: dict[str, Pull | None] = {}
    for start in range(0, len(prs), PRS_PER_QUERY):
        batch = prs[start : start + PRS_PER_QUERY]
        done = shell.run(ghread.read("api", "graphql", "-f", f"query={pulls_query(repo, batch)}"), env={"GH_REPO": repo})
        try:
            found = json.loads(done.out)["data"]["repository"]
        except (ValueError, KeyError, TypeError):
            warnings.append(f"WARN GitHub refused the PR read for {' '.join(f'#{pr}' for pr in batch)}: {(done.err or done.out).strip()[:200] or f'exit {done.code}'}; their stale, old-text and closed checks are skipped")
            continue
        for pr in batch:
            node = found.get(f"p{pr}")
            pulls[pr] = Pull(node["state"], node["headRefOid"], node["commits"]["nodes"][0]["commit"]["committedDate"], node["baseRefName"], node["headRefName"]) if node else None
    return pulls


def admitted(shell: Shell, drive: str, landing: dict) -> set[str]:
    """The PRs of `landing.repo` the drive's ledger admitted, the only ones the landing desk lands."""
    done = shell.run([sys.executable, str(Path(__file__).resolve().parents[1] / "ledger.py"), "-C", str(Path(landing["checkout"]).expanduser()), "list", "--ledger", landing["ledger"], "--json"], env={"CLAUDE_LONG_RUNNING_DRIVE": drive})
    if done.code != 0:
        raise ReadFailed(f"ledger.py list exited {done.code}: {(done.err or done.out).strip()[:200]}")
    return {row["pr"] for row in json.loads(done.out) if row["ours"] and row["repo"] == landing["repo"]}


def tail(shell: Shell) -> Callable[[list[str]], str]:
    def run(argv: list[str]) -> str:
        done = shell.run(argv)
        if done.code != 0:
            raise ReadFailed(f"cci tail exited {done.code}: {(done.err or done.out).strip()[:200]}")
        return done.out

    return run


def ruling_text(shell: Shell, record: dict) -> str:
    path = record.get("refs", {}).get("path")
    return (shell.read(path) if path else None) or record["text"]


def root_rulings(shell: Shell, drive: str, lanes: list[str]) -> list[dict]:
    """The go, decide, decision and answer records of root and of each lane `landing.go_lanes` lets rule for it, or its numbered successor; root rules a re-ask in an answer as often as in a decide (board-refresher-2, root #213285 and #213347)."""
    kinds = [f"--kind={kind}" for kind in sorted(RULING_KINDS)]
    records = [record for record in cci.records(tail(shell), drive, *kinds) if grades.delegated(record["lane"], lanes) and record["drive"] == drive and record["kind"] in RULING_KINDS]
    return [record | {"full": ruling_text(shell, record)} for record in records]


def closes(pr: str, text: str) -> bool:
    verb = rf"\b(?:clos(?:e|es|ed|ing)|supersed(?:es|ing))\s+(?:#\d+(?:\s*,\s*|\s*,?\s+and\s+))*#{pr}\b"
    ruled = rf"(?<!\bon )(?<!\bof )(?<!\bin )#{pr}\b(?:\s+\(?[0-9a-f]{{7,40}}\)?)?(?:\s+(?:is|was|now|stays|has\s+been))*\s+(?:superseded|closed|landed)\b"
    return re.search(verb, text, re.IGNORECASE) is not None or re.search(ruled, text, re.IGNORECASE) is not None


def closing_ruling(rulings: list[dict], pr: str, head: str) -> dict | None:
    """The newest root ruling that says `pr` is superseded or closed, unless a later root `go` names it at `head`."""
    ruling = max((record for record in rulings if closes(pr, record["full"])), key=lambda record: record["seq"], default=None)
    if ruling is None:
        return None
    revived = [record for record in grades.gone_at([record for record in rulings if record["kind"] == "go" and record["seq"] > ruling["seq"]], pr, head) if not grades.WITHDRAWN.match(record["text"])]
    return None if revived else ruling


def rekeyed(rulings: list[dict], row: Row) -> dict | None:
    """The newest root record that re-keys a row and names `row`'s id, or its PR with its head or id's short sha."""
    named = ROW_ID.match(row.id)
    keyed = (named["sha"] or "") if named else ""
    shas = [sha for sha in (row.head, keyed) if sha]

    def names(text: str) -> bool:
        if named and row.id in text:
            return True
        if not re.search(rf"#{row.pr}\b|\bmerge-row-{row.pr}\b", text):
            return False
        return any(grades.names_head(named, sha) for named in grades.SHORT_SHA.findall(text) for sha in shas)

    return max((record for record in rulings if REKEY.search(record["full"]) and names(record["full"])), key=lambda record: record["seq"], default=None)


def outcomes_args(session: str | None, command: list[str]) -> list[str]:
    if session is None and "--session" in command[:-1]:
        session = command[command.index("--session") + 1]
    return ["cc-present", "outcomes", "--all", "--no-doc", *(["--session", session] if session else [])]


def verdicts_of(done: Done) -> dict[str, dict[str, dict]]:
    """Each triage or pr-groups block's recorded verdicts, a row id or PR number to its entry of verdict, head and time, from what `cc-present outcomes` printed."""
    start = done.out.find("{")
    if done.code != 0 or start < 0:
        raise ReadFailed(f"cc-present outcomes exited {done.code}: {(done.err or done.out).strip()[:200]}")
    try:
        triage = json.loads(done.out[start:])["interactions"].get("triage") or {}
    except (ValueError, KeyError) as error:
        raise ReadFailed(f"cc-present outcomes printed no interactions: {error}") from error
    return {block: {row: entry for row, entry in rows.items() if entry.get("verdict")} for block, rows in triage.items()}


def verdict_reasons(rows: list[Row], verdicts: dict[str, dict[str, dict]], rulings: list[dict] | None) -> dict[str, list[str]]:
    """Why each row would hide a recorded verdict: its id decided under another block, or its PR decided under an id this push drops, with no root re-key."""
    pushed = {row.id for row in rows}
    decided = [(block, row_id) for block, by_row in verdicts.items() for row_id in by_row]
    reasons: dict[str, list[str]] = {}
    for row in rows:
        if row.id in verdicts.get(row.block, {}):
            continue
        if elsewhere := sorted(block for block, row_id in decided if row_id == row.id):
            reasons.setdefault(row.id, []).append(f"moved: decided under block {', '.join(elsewhere)}, pushed under {row.block}")
            continue
        dropped = sorted({row_id for _, row_id in decided if row_id != row.id and row_id not in pushed and pr_of(row_id) == row.pr})
        if dropped and rulings is not None and not rekeyed(rulings, row):
            reasons.setdefault(row.id, []).append(f"renamed: decided as {', '.join(dropped)}, and no root re-key record names {row.id}")
    return reasons


def at_head(entry: dict | None, verdict: str, head: str) -> bool:
    return entry is not None and entry.get("verdict") == verdict and (not entry.get("head") or grades.names_head(entry["head"], head))


def standing_gos(rulings: list[dict], withdrawn: set[int], pr: str, head: str) -> list[dict]:
    return grades.gone_at([record for record in rulings if record["kind"] == "go" and record["seq"] not in withdrawn], pr, head)


def tap_reasons(row: Row, entry: dict | None, gos: list[dict], taps: list[dict]) -> list[str]:
    if at_head(entry, "rejected", row.head):
        return [f"rejected: he rejected it at {row.head[:7]}{' at ' + entry['at'] if entry and entry.get('at') else ''}; it returns only at a new head"]
    carried = entry is not None and entry.get("verdict") == "approved" and bool(entry.get("head")) and any(grades.names_head(sha, entry["head"]) for sha in row.carried)
    if gos and not at_head(entry, "approved", row.head) and not carried:
        return [f"approved: root go cci #{gos[-1]['seq']} names it at {row.head[:7]}, and neither a board approval there nor his approved head in its carried list backs it; add {entry['head'] if entry and entry.get('verdict') == 'approved' and entry.get('head') else 'his approved head'} to carried, import the verdict, or drop the row"]
    if moved_tap(row, entry) and not tap_verdict(taps, row, entry, "TAP-NOT-CARRIED"):
        return [f"re-ask: he approved it at {entry['head']}, and the landing runner has not found that {row.head[:7]} changes what he approved; it waits in Being fixed or checked"]
    return []


def moved_tap(row: Row, entry: dict | None) -> bool:
    return entry is not None and entry.get("verdict") == "approved" and bool(entry.get("head")) and not at_head(entry, "approved", row.head) and not any(grades.names_head(sha, entry["head"]) for sha in row.carried)


def stacks(rows: list[Row]) -> list[list[Row]]:
    """Each chain of two or more `pr-groups` rows of one block that GitHub stacks, each based on the branch of the one below, bottom first, when every row in it passes, asks again with its `since`, and moved only by a fix with no new behavior."""
    chains = []
    for block_id in dict.fromkeys(row.block for row in rows if row.required == PR_FIELDS):
        mine = [row for row in rows if row.block == block_id and row.required == PR_FIELDS and row.pull]
        above = {row.pull.base: row for row in mine}
        for bottom in (row for row in mine if not any(row.pull.base == other.pull.branch for other in mine)):
            chain = [bottom]
            while (up := above.get(chain[-1].pull.branch)) and up not in chain:
                chain.append(up)
            if len(chain) > 1 and all(row.pure and row.opener and not row.reasons for row in chain):
                chains.append(chain)
    return chains


def stack_row(chain: list[Row], tip: dict) -> dict:
    times = list(dict.fromkeys(tapped_at(row.entry) for row in sorted(chain, key=lambda row: row.entry.get("at", ""))))
    when = f"at {times[0]}" if len(times) == 1 else f"between {times[0]} and {times[-1]}"
    since = "; ".join(f"#{row.pr} {row.since['changed'].strip().rstrip('.')}" for row in chain)
    because = "; ".join(dict.fromkeys(row.since["because"].strip().rstrip(".") for row in chain))
    opener = f"You approved these {len(chain)} changes {when}. Since then: {since}. Shown again because: {because}."
    facts = {name: shared([(row.pr, row.facts[name]) for row in chain]) for name in PR_FIELDS}
    return {key: value for key, value in tip.items() if key != SINCE} | facts | {
        "change": f"{opener} {facts['change']}",
        "title": f"{tip.get('title', '')} (stack of {len(chain)})",
        "md": "\n\n".join(f"#{row.pr} {row.label}: {row.facts['change']}" for row in chain) + (f"\n\n{tip['md']}" if tip.get("md") else ""),
        "stack": [{"number": int(row.pr), "head": row.head} for row in chain[:-1]],
    }


def shared(values: list[tuple[str, str]]) -> str:
    return values[0][1] if len({value for _, value in values}) == 1 else "; ".join(f"#{pr}: {value.rstrip('.')}" for pr, value in values) + "."


def folded(node: object, chains: list[list[Row]]) -> object:
    """`node` with each chain of `chains` pushed as one row at its tip, standing for the others through its `stack`, and each group left empty dropped."""
    if isinstance(node, list):
        return [folded(value, chains) for value in node]
    if not isinstance(node, dict):
        return node
    if node.get("type") != PR_GROUPS:
        return {key: folded(value, chains) for key, value in node.items()}
    mine = [chain for chain in chains if chain[-1].block == node["id"]]
    tips = {chain[-1].pr: chain for chain in mine}
    under = {row.pr for chain in mine for row in chain[:-1]}
    groups = [dict(group, prs=[stack_row(tips[str(pr["number"])], pr) if str(pr["number"]) in tips else pr for pr in group.get("prs", []) if str(pr["number"]) not in under]) for group in node.get("groups", [])]
    return dict(node, groups=[group for group in groups if group["prs"]])


def tapped_at(entry: dict) -> str:
    return datetime.fromisoformat(entry["at"]).astimezone(PACIFIC).strftime("%-I:%M %p %b %-d") if entry.get("at") else f"head {entry['head'][:7]}"


def since_reasons(row: Row, entry: dict) -> list[str]:
    changed, because = (str(row.since.get(name, "")).strip().rstrip(".") for name in ("changed", "because"))
    when = tapped_at(entry)
    if not (changed and because):
        return [f"since: he approved it at {entry['head'][:7]}, so the row opens 'You approved this at {when}. Since then: <what changed, in plain words>. Shown again because: <the behavior change>.'; give its `since` both `changed` and `because`"]
    row.opener = f"You approved this at {when}. Since then: {changed}. Shown again because: {because}."
    return []


def tap_verdict(taps: list[dict], row: Row, entry: dict, label: str) -> bool:
    """Whether the landing runner's tap judge posted `label` for `row`'s PR from the head of his tap `entry` to the row's head: `TAP-NOT-CARRIED` when the head changes what the owner approved, so the board asks him again, `TAP-KEPT` when it changes nothing he approved."""
    return any((found := TAP_VERDICT.match(record["text"])) and found["label"] == label and found["pr"] == row.pr and grades.names_head(found["tapped"], entry["head"]) and grades.names_head(found["head"], row.head) for record in taps)


def attrs(record: dict) -> dict[str, str]:
    return (record.get("fields") or {}).get("attrs") or {}


def verdicts_of_records(records: list[dict]) -> dict[str, dict[str, dict]]:
    """Each block's standing verdict per row, an entry of verdict, head, time, the board session it stands on, and the head a carry came from, from the board-watch `owner` records that carry the row and verdict and the runner's own `board-write` carries, the newest tap time winning and a later record breaking a tie."""
    entries = []
    for record in records:
        found = attrs(record)
        owner = (record["lane"], record["kind"]) == (OWNER_LANE, "owner") and found.get("row") and found.get("verdict")
        carry = record["lane"] == RUNNER_LANE and record.get("topic") == WRITE_TOPIC and found.get("op") == "carry"
        if not (owner or carry):
            continue
        entry = {"verdict": found["verdict"] if owner else "approved", "head": found.get("head", ""), "at": found.get("at") or record["at"], "session": found.get("session", "")}
        if carried := found.get("carried_from" if owner else "from"):
            entry["carriedFrom"] = carried
        entries.append((found["block"], found["row"], entry, record["seq"]))
    verdicts: dict[str, dict[str, dict]] = {}
    for block, row, entry, _ in sorted(entries, key=lambda item: (parsed(item[2]["at"]) or datetime.min.replace(tzinfo=PACIFIC), item[3])):
        verdicts.setdefault(block, {})[row] = entry
    return verdicts


def answered_of_records(records: list[dict]) -> set[str]:
    """Each choice block the owner picked an option on or wrote into, from board-watch `owner` records."""
    return {found["block"] for record in records if (record["lane"], record["kind"]) == (OWNER_LANE, "owner") and (found := attrs(record)).get("event") == "choice.selected" and (found.get("picked") or found.get("other"))}


def snapshot(doc: dict) -> dict:
    """What the runner and the stall watch read of the board's document: each block holding PR rows, each PR row's PR, head and write time, each merge card's PR and head, and each card's choice blocks and PRs."""
    rows = rows_of(doc)
    return {
        "blocks": sorted({row.block for row in rows}),
        "rows": [[row.pr, row.head, row.text_at] for row in rows if row.head],
        "merges": [[found[1], found[2]] for key in block_ids(doc) if (found := MERGE_CARD.match(key))],
        "cards": [[card["id"], sorted(choice["id"] for choice in typed(card, "choice")), sorted(str(found["number"]) for found in typed(card, "pr"))] for card in typed(doc, "card")],
    }


def snapshot_of_records(records: list[dict]) -> dict | None:
    """The newest board snapshot board-watch posted, with the session of the board it shows and empty lists for any part an older board-watch does not post, or None while it has posted none."""
    found = [attrs(record) for record in records if record["lane"] == OWNER_LANE and record.get("topic") == SNAPSHOT_TOPIC and "board" in attrs(record)]
    return {"blocks": [], "rows": [], "merges": [], "cards": []} | json.loads(found[-1]["board"]) | {"session": found[-1]["session"]} if found else None


def block_ids(node: object) -> Iterator[str]:
    if isinstance(node, dict):
        if isinstance(node.get("id"), str):
            yield node["id"]
        for value in node.values():
            yield from block_ids(value)
    elif isinstance(node, list):
        for value in node:
            yield from block_ids(value)


def typed(node: object, kind: str) -> Iterator[dict]:
    if isinstance(node, dict):
        if node.get("type") == kind:
            yield node
        for value in node.values():
            yield from typed(value, kind)
    elif isinstance(node, list):
        for value in node:
            yield from typed(value, kind)


def newest_by_pr(verdicts: dict[str, dict[str, dict]]) -> dict[str, dict]:
    newest: dict[str, dict] = {}
    for by_row in verdicts.values():
        for row_id, entry in by_row.items():
            if (pr := pr_of(row_id)) and entry.get("at", "") >= newest.get(pr, {}).get("at", ""):
                newest[pr] = entry
    return newest


def claim_reason(members: set[str], newest: dict[str, dict], pulls: dict[str, Pull | None]) -> str | None:
    if len(members) < 2:
        return None
    rejected = sorted((pr for pr in members if newest.get(pr, {}).get("verdict") == "rejected"), key=int)
    closed = sorted((pr for pr in members if pr in pulls and (pulls[pr] is None or pulls[pr].state != OPEN)), key=int)
    if not rejected and not closed:
        return None
    parts = ([f"{' '.join(f'#{pr}' for pr in rejected)} rejected on the board"] if rejected else []) + ([f"{' '.join(f'#{pr}' for pr in closed)} no longer open"] if closed else [])
    return f"one tap: its words say one tap approves {len(members)} PRs, but {' and '.join(parts)}"


def graded_warnings(reviews: list[dict], saved: list[str], drive: str, lanes: list[str], repo: str, row: Row) -> list[str]:
    warnings = []
    if not grades.standing(grades.fidelity_grades_in(reviews, drive, lanes, repo, row.pr), lanes, row.head):
        warnings.append(f"ungraded: no fidelity grade from {' or '.join(lanes)} names {row.head}")
    found = grades.finder_records(grades.finder_reviews_in(reviews, drive, repo, row.pr))
    replies = [name for name in saved if (named := SAVED_FINDER.match(name)) and named["pr"] == row.pr and grades.names_head(named["sha"], row.head)]
    if not (grades.newest_finder(found, row.head) or replies):
        warnings.append(f"ungraded: no finder review names {row.head}")
    return warnings


def parsed(stamp: str) -> datetime | None:
    try:
        return datetime.fromisoformat(stamp)
    except ValueError:
        return None


def github_reasons(row: Row, pull: Pull | None) -> list[str]:
    if pull is None:
        return ["not open: GitHub knows no such PR"]
    reasons = []
    if pull.state != OPEN:
        reasons.append(f"not open: the PR is {pull.state.lower()} on GitHub")
    if not grades.names_head(row.head, pull.head):
        reasons.append(f"stale: GitHub's head is {pull.head[:7]}")
    elif not (written := parsed(row.text_at)):
        reasons.append(f"old text: no {TEXT_AT} on the row")
    elif written <= datetime.fromisoformat(pull.committed):
        reasons.append(f"old text: written {row.text_at}, not after {pull.head[:7]} was committed at {pull.committed}")
    return reasons


def flags(rows: list[Row]) -> list[dict]:
    found = []
    for one, other in itertools.combinations(rows, 2):
        mine, theirs = set(WORD.findall(one.sentence.lower())), set(WORD.findall(other.sentence.lower()))
        jaccard = len(mine & theirs) / len(mine | theirs) if mine | theirs else 0.0
        if jaccard > SAME_JACCARD or one.sentence.lower()[:SAME_PREFIX] == other.sentence.lower()[:SAME_PREFIX]:
            found.append({"rows": [one.id, other.id], "prs": [one.pr, other.pr], "jaccard": round(jaccard, 2), "sentences": [one.sentence, other.sentence]})
    return found


def check(shell: Shell, config: dict, block: dict, outcomes: list[str], finders: Path) -> tuple[list[Row], list[dict], list[str]]:
    drive, landing = config["drive"], config["landing"]
    repo, configured = landing["repo"], landing["fidelity_lane"]
    lanes = [configured] if isinstance(configured, str) else list(configured)
    rows = rows_of(block)
    warnings: list[str] = []
    for row in rows:
        if missing := missing_fields(row):
            row.reasons.append(f"missing: {', '.join(missing)}")
        if not row.head:
            row.reasons.append("stale: the row names no head" if row.required == PR_FIELDS else "stale: the hint names no `commit <sha>`")
    named = [row for row in rows if row.head]
    claims = [(row.block, row.pr, row.words, {row.pr} | set(PR_REF.findall(row.words))) for row in rows if ONE_TAP.search(row.words)] + list(group_claims(block))
    blockers = {row.id: blocker_prs(row) for row in rows}
    listed = sorted(set(listed_prs(block)))
    asked = {row.pr for row in named} | {pr for prs in blockers.values() for pr in prs} | {pr for *_, members in claims for pr in members} | {pr for _, pr in listed}
    pulls = read_pulls(shell, repo, sorted(asked, key=int), warnings)
    try:
        rulings = sorted(root_rulings(shell, drive, ["root", *landing.get("go_lanes", [])]), key=lambda record: record["seq"])
        withdrawn = {seq for record in cci.records(tail(shell), drive, "--lane", "root", "--kind=withdraw") if record["lane"] == "root" for seq in grades.closes(record)}
    except ReadFailed as failed:
        rulings, withdrawn = None, set()
        warnings.append(f"WARN the root rulings did not read ({failed}); no row is checked for a closing ruling or a root go at its head, and no renamed row is excused by a re-key")
    try:
        verdicts = verdicts_of(shell.run(outcomes))
    except ReadFailed as failed:
        verdicts = None
        warnings.append(f"WARN the board's verdicts did not read ({failed}); no row is checked for a moved, renamed, rejected or already approved verdict, or a one-tap claim over a rejected PR")
    moved = verdict_reasons(rows, verdicts, rulings) if verdicts is not None else {}
    try:
        reviews = cci.records(tail(shell), drive, "--kind=review", "--kind=head")
        lanes = grades.with_successors(lanes, reviews)
    except ReadFailed as failed:
        reviews = None
        warnings.append(f"WARN the grades did not read ({failed}); no row is checked for a grade or finder at its head")
    try:
        ours = admitted(shell, drive, landing)
    except ReadFailed as failed:
        ours = None
        warnings.append(f"WARN the drive's ledger did not read ({failed}); no row is checked for a PR the ledger never admitted")
    saved = shell.files(finders)
    taps = tap_verdicts(shell, drive, [entry["at"] for row in named if verdicts is not None and (entry := verdicts.get(row.block, {}).get(row.id)) and moved_tap(row, entry) and entry.get("at")], warnings)
    for row in rows:
        row.reasons += moved.get(row.id, [])
    for row in named:
        if ours is not None and row.pr not in ours:
            row.reasons.append(f"unledgered: the drive's ledger never admitted #{row.pr}, so no tap on it can land; its lane runs `ledger.py register --pr {row.pr}`")
        if row.pr in pulls:
            row.pull = pulls[row.pr]
            row.reasons += github_reasons(row, pulls[row.pr])
        if rulings is not None and (ruling := closing_ruling(rulings, row.pr, row.head)):
            row.reasons.append(f"not open: root ruled it superseded or closed on cci #{ruling['seq']}")
        if verdicts is not None:
            row.entry = verdicts.get(row.block, {}).get(row.id)
            row.reasons += tap_reasons(row, row.entry, standing_gos(rulings, withdrawn, row.pr, row.head) if rulings is not None else [], taps or [])
            if moved_tap(row, row.entry):
                row.reasons += since_reasons(row, row.entry)
                row.pure = tap_verdict(taps or [], row, row.entry, "TAP-KEPT")
        if reviews is not None:
            gaps = graded_warnings(reviews, saved, drive, lanes, repo, row)
            row.reasons += [gap for gap in gaps if "fidelity" in gap]
            row.warnings += [gap for gap in gaps if "fidelity" not in gap]
        if unsettled := sorted({found.lower() for sentence in SENTENCE.split(row.words) for found in UNSETTLED.findall(sentence) if not any(not grades.names_head(sha, row.head) for sha in grades.SHORT_SHA.findall(sentence))}):
            row.reasons.append(f"unsettled: its words say {' and '.join(unsettled)}, so it asks no tap yet")
    for row in rows:
        if landed := sorted((pr for pr in blockers[row.id] if (pull := pulls.get(pr)) and pull.state == MERGED), key=int):
            row.reasons.append(f"landed: its blocker names {' '.join(f'#{pr}' for pr in landed)}, merged on GitHub")
    if verdicts is not None:
        newest = newest_by_pr(verdicts)
        for block_id, anchor, _, members in claims:
            if reason := claim_reason(members, newest, pulls):
                for claimed in [row for row in rows if row.block == block_id and (row.pr == anchor or row.pr in members)]:
                    claimed.reasons.append(reason)
    for one, other in alike(rows):
        one.reasons.append(f"alike: its title shares most of its words with #{other.pr} in group {other.group}")
        other.reasons.append(f"alike: its title shares most of its words with #{one.pr} in group {one.group}")
    found = flags(rows)
    newest = newest_by_pr(verdicts) if verdicts is not None else {}
    for block_id, pr in listed:
        if pr not in pulls or (pull := pulls[pr]) is None:
            continue
        if pull.state != OPEN:
            rows.append(Row(f"{block_id}#{pr}", block_id, pr, pull.head[:7], "", "", "", {}, reasons=[f"listed: the PR is {pull.state.lower()} on GitHub and still listed under {block_id}"]))
        elif APPROVED_LIST in block_id and rulings is not None and reviews is not None and (missing := approval_gaps(pull.head, pr, newest.get(pr), rulings, withdrawn, reviews, drive, lanes, repo)):
            rows.append(Row(f"{block_id}#{pr}", block_id, pr, pull.head[:7], "", "", "", {}, reasons=[f"unapproved: listed under {block_id} with {' and '.join(missing)} at GitHub's head {pull.head[:7]}"]))
    return rows, found, warnings


def trunk_banner(shell: Shell, drive: str, warnings: list[str]) -> str:
    try:
        trunk = cci.trunk(cci.records(tail(shell), drive, f"--topic={cci.TRUNK_TOPIC}"))
    except ReadFailed as failed:
        warnings.append(f"WARN dev's state did not read ({failed}); the card carries no red-dev banner")
        return ""
    return f"dev's test build is red since {trunk.since}; nothing lands until {f'#{trunk.fix} fixes it' if trunk.fix else 'its fix lands'}." if trunk.blocked else ""


def bannered(node: object, banner: str) -> object:
    if isinstance(node, dict):
        if node.get("type") == PR_GROUPS:
            return dict(node, prompt=f"{banner} {node.get('prompt', '')}".strip())
        return {key: bannered(value, banner) for key, value in node.items()}
    if isinstance(node, list):
        return [bannered(value, banner) for value in node]
    return node


def opened(node: object, rows: list[Row]) -> object:
    """`node` with each re-asked `pr-groups` row's `change` opening with what changed since his tap, and no row's `since`."""
    if isinstance(node, list):
        return [opened(value, rows) for value in node]
    if not isinstance(node, dict):
        return node
    if node.get("type") != PR_GROUPS:
        return {key: opened(value, rows) for key, value in node.items()}
    openers = {row.pr: row.opener for row in rows if row.block == node["id"] and row.opener}
    return dict(node, groups=[dict(group, prs=[opened_row(pr, openers.get(str(pr["number"]))) for pr in group.get("prs", [])]) for group in node.get("groups", [])])


def opened_row(pr: dict, opener: str | None) -> dict:
    kept = {key: value for key, value in pr.items() if key != SINCE}
    return kept | {"change": f"{opener} {pr.get('change', '')}"} if opener else kept


def approval_gaps(head: str, pr: str, entry: dict | None, rulings: list[dict], withdrawn: set[int], reviews: list[dict], drive: str, lanes: list[str], repo: str) -> list[str]:
    approved = standing_gos(rulings, withdrawn, pr, head) or (entry is not None and entry.get("verdict") == "approved" and bool(entry.get("head")) and grades.names_head(entry["head"], head))
    graded = grades.standing(grades.fidelity_grades_in(reviews, drive, lanes, repo, pr), lanes, head)
    return (["no go, approval or carry"] if not approved else []) + (["no fidelity ok"] if not graded else [])


def tap_verdicts(shell: Shell, drive: str, tapped: list[str], warnings: list[str]) -> list[dict] | None:
    """The landing runner's `TAP-NOT-CARRIED` notes and `TAP-KEPT` asks since the oldest of the `tapped` times, the owner's taps at heads the rows have moved past; None when they do not read."""
    if not tapped:
        return []
    try:
        records = cci.records(tail(shell), drive, "--lane", RUNNER_LANE, "--kind=note", "--kind=ask", since=min(tapped, key=lambda at: parsed(at) or datetime.max.replace(tzinfo=PACIFIC)))
    except ReadFailed as failed:
        warnings.append(f"WARN the landing runner's tap verdicts did not read ({failed}); no moved tap clears its re-ask or folds into a stack row")
        return None
    return [record for record in records if record["lane"] == RUNNER_LANE and record["drive"] == drive and TAP_VERDICT.match(record["text"])]


def withheld(node: object, failing: list[Row]) -> object:
    """`node` without each failing row, and without each triage or `pr-groups` block left holding none, which cc-present refuses: None for such a block."""
    if isinstance(node, list):
        return [kept for value in node if (kept := withheld(value, failing)) is not None]
    if not isinstance(node, dict):
        return node
    kept = {key: withheld(value, failing) for key, value in node.items()}
    named = {row.id for row in failing if row.block == node.get("id")}
    if node.get("type") == "triage":
        kept["items"] = [item for item in kept.get("items", []) if item.get("id") not in named]
        return kept if kept["items"] else None
    if node.get("type") == PR_GROUPS:
        groups = [dict(group, prs=[pr for pr in group.get("prs", []) if str(pr.get("number")) not in named]) for group in kept.get("groups", [])]
        kept["groups"] = [group for group, before in zip(groups, kept.get("groups", []), strict=True) if group["prs"] or not before.get("prs")]
        return kept if kept["groups"] else None
    if node.get("type") == "markdown":
        listed = {row.pr for row in failing if row.id == f"{node.get('id')}#{row.pr}"}
        kept["md"] = "".join(line for line in kept.get("md", "").splitlines(keepends=True) if not ((found := LISTED.match(line)) and found[1] in listed))
    return kept


def stripped(block: object) -> object:
    if isinstance(block, dict):
        return {key: stripped(value) for key, value in block.items() if key != TEXT_AT}
    if isinstance(block, list):
        return [stripped(value) for value in block]
    return block


def counts(rows: list[Row], found: list[dict]) -> dict[str, int]:
    return {"rows": len(rows), "failing": sum(bool(row.reasons) for row in rows), "ungraded": sum(bool(row.warnings) for row in rows), "flags": len(found)}


def report(rows: list[Row], found: list[dict], warnings: list[str], as_json: bool) -> None:
    tally = counts(rows, found)
    if as_json:
        print(json.dumps({"rows": [asdict(row) | {"ok": not row.reasons} for row in rows], "flags": found, "warnings": warnings, "counts": tally}))
        return
    for row in rows:
        if row.reasons:
            print(f"{row.id} {row.pr} {row.head or '-'} {'; '.join(row.reasons)}")
    for row in rows:
        if row.warnings:
            print(f"WARN {row.id} {row.pr} {row.head} {'; '.join(row.warnings)}")
    for line in warnings:
        print(line)
    for flag in found:
        print(f"FLAG {' '.join(flag['rows'])} {' '.join(f'#{pr}' for pr in flag['prs'])} first sentences match (jaccard {flag['jaccard']}): {flag['sentences'][0]}")
    print(" ".join(f"{name} {count}" for name, count in tally.items()))


def split(argv: list[str]) -> tuple[list[str], list[str]]:
    if "--" not in argv:
        return argv, []
    at = argv.index("--")
    return argv[:at], argv[at + 1 :]


def main(argv: list[str], shell: Shell | None = None) -> int:
    shell = shell or Shell()
    ours, command = split(argv)
    parser = argparse.ArgumentParser(prog="board_rows.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--session")
    parser.add_argument("--finders", type=Path)
    parser.add_argument("block", type=Path)
    args = parser.parse_args(ours)
    if args.check == bool(command):
        parser.error("pass --check, or a push command after --, not both")
    block = json.loads(args.block.read_text())
    config = args.config.expanduser()
    loaded = json.loads(config.read_text())
    rows, found, warnings = check(shell, loaded, block, outcomes_args(args.session, command), (args.finders or config.parent / FINDERS).expanduser())
    if (banner := trunk_banner(shell, loaded["drive"], warnings)) and not args.json:
        print(f"BANNER {banner}")
    report(rows, found, warnings, args.json)
    failing = [row for row in rows if row.reasons]
    if args.check:
        return 1 if failing else 0
    if failing and len(failing) == len(rows):
        return 1
    with tempfile.NamedTemporaryFile("w", prefix=f"{args.block.stem}.", suffix=".json") as copy:
        chains = stacks(rows)
        folds = {row.pr for chain in chains for row in chain}
        json.dump(stripped(withheld(opened(folded(bannered(block, banner) if banner else block, chains), [row for row in rows if row.pr not in folds]), failing)), copy, indent=1)
        copy.flush()
        pushed = shell.push([*command, copy.name])
    return 1 if failing else pushed
