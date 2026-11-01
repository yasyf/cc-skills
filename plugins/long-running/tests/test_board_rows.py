"""board_rows.py over a fake GitHub, drive bus, and board: a merge row, a triage item or a pr-groups row, passes only at its PR's current head, with every field the owner decides by, in words written after that head, while the PR is open and unruled and its recorded verdict stays where the board keys it."""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

from long_running import board_rows, grades
import pytest
from conftest import gateway

DRIVE = "d1"
REPO = "Forge-AI/monorepo"
HEAD = "35aa930c1d2e3f405162738495a6b7c8d9e0f1a2"
NEW_HEAD = "7b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c"
LEDGER = "1a2b3c4d"
COMMITTED = "2026-10-26T19:00:00Z"
WRITTEN = "2026-10-26T20:00:00Z"
BEFORE = "2026-10-26T18:00:00Z"


class FakeShell(board_rows.Shell):
    def __init__(self) -> None:
        self.pulls: dict[str, dict | None] = {}
        self.records: list[dict] = []
        self.blobs: dict[str, str] = {}
        self.gh_fails = False
        self.verdicts: dict[str, dict[str, dict]] = {}
        self.outcomes_fail = False
        self.saved: list[str] = []
        self.pushed: list[tuple[list[str], dict]] = []
        self.ledger: set[str] | None = None
        self.ledger_fails = False
        self.push_code = 0

    def run(self, argv: list[str], env: dict[str, str] | None = None) -> board_rows.Done:
        argv = gateway(argv)
        if argv[:3] == ["gh", "api", "graphql"]:
            assert env == {"GH_REPO": REPO}
            if self.gh_fails:
                return board_rows.Done(1, "", "HTTP 502: Bad Gateway")
            query = argv[-1]
            found = {f"p{pr}": pull for pr, pull in self.pulls.items() if f"pullRequest(number: {pr})" in query}
            return board_rows.Done(0, json.dumps({"data": {"repository": found}}), "")
        if argv[:2] == ["cc-present", "outcomes"] and "--all" not in argv:
            return board_rows.Done(0, json.dumps({"since": "last-submit", "interactions": [{"seq": 1, "type": "triage.decided", "payload": {}}]}), "")
        if argv[:2] == ["cc-present", "outcomes"]:
            if self.outcomes_fail:
                return board_rows.Done(1, "", "no board for this session")
            return board_rows.Done(0, "cc-present: dropped pack x\n" + json.dumps({"interactions": {"triage": self.verdicts}}), "")
        if argv[1].endswith("/ledger.py"):
            assert argv[2:] == ["-C", "/checkout", "list", "--ledger", LEDGER, "--json"] and env == {"CLAUDE_LONG_RUNNING_DRIVE": DRIVE}, argv
            if self.ledger_fails:
                return board_rows.Done(1, "", "ccn: no ledger 1a2b")
            return board_rows.Done(0, json.dumps([{"pr": pr, "repo": REPO, "ours": True} for pr in sorted(self.ledger)] + [{"pr": "1", "repo": "other/repo", "ours": True}, {"pr": "2", "repo": REPO, "ours": False}]), "")
        assert argv[:2] == ["cci", "tail"], argv
        return board_rows.Done(0, "".join(json.dumps(record) + "\n" for record in self.tailed(argv)), "")

    def tailed(self, argv: list[str]) -> list[dict]:
        since = argv[argv.index("--since-seq" if "--since-seq" in argv else "--since") + 1]
        kinds = [arg.split("=", 1)[1] for arg in argv if arg.startswith("--kind=")]
        lanes = [argv[at + 1] for at, arg in enumerate(argv) if arg == "--lane"]
        pr = argv[argv.index("--pr") + 1] if "--pr" in argv else None
        topics = [arg.split("=", 1)[1] for arg in argv if arg.startswith("--topic=")]
        return [
            record
            for record in self.records
            if (record["seq"] > int(since) if since.isdigit() else record.get("at", since) >= since)
            and (not kinds or record["kind"] in kinds)
            and (not lanes or record["lane"] in lanes)
            and (pr is None or pr in record["refs"].get("prs", []))
            and (not topics or record.get("topic") in topics)
        ]

    def push(self, argv: list[str]) -> int:
        self.pushed.append((argv, json.loads(Path(argv[-1]).read_text())))
        return self.push_code

    def read(self, path: str) -> str | None:
        return self.blobs.get(path)

    def files(self, directory: Path) -> list[str]:
        assert directory.name == "finders"
        return self.saved

    def pull(self, pr: str, head: str = HEAD, state: str = "OPEN", committed: str = COMMITTED, base: str = "dev") -> None:
        self.pulls[pr] = {"state": state, "headRefOid": head, "baseRefName": base, "headRefName": f"b/{pr}", "commits": {"nodes": [{"commit": {"committedDate": committed}}]}}

    def post(self, kind: str, lane: str, text: str, pr: str | None = None, path: str | None = None, drive: str = DRIVE, topic: str = "") -> int:
        seq = len(self.records) + 1
        refs = {"prs": [f"{REPO}#{pr}"]} if pr else {}
        self.records.append({"seq": seq, "drive": drive, "kind": kind, "lane": lane, "text": text, "topic": topic, "refs": refs | ({"path": path} if path else {})})
        return seq

    def trunk(self, *texts: str) -> None:
        for text in texts:
            self.post("incident" if text.startswith("DEV-RED") else "report", "desk-runner", text, topic="dev-red")

    def graded(self, pr: str, head: str = HEAD) -> None:
        self.post("review", "fidelity-desk", f"fidelity: ok {head[:7]} checked rule/1", pr)
        self.post("review", "finder-desk", f"finder: ok {head[:7]} no majors", pr)


FACTS = [{"label": name, "value": f"{name.lower()} in plain words"} for name in board_rows.FIELDS]


def row(pr: str, head: str = HEAD, text_at: str | None = WRITTEN, md: str = "Saves the old converter's on-disk chat layouts as samples.", key: str = "", facts: list[dict] | None = None) -> dict:
    item = {"id": f"merge-row-{pr}{key}", "label": f"#{pr}: a change", "hint": f"commit {head[:7]}, built on dev", "md": md, "facts": FACTS if facts is None else facts}
    return item | ({"textAt": text_at} if text_at else {})


def card(*items: dict, block: str = "merge-triage") -> dict:
    return {"id": "card-merge-triage", "type": "card", "children": [{"id": block, "type": "triage", "items": list(items)}]}


def pr_row(pr: str, head: str = HEAD, text_at: str | None = WRITTEN, **fields: str) -> dict:
    row = {"number": int(pr), "head": head[:7], "title": "A change", "md": "Full text."} | {name: f"{name} in plain words" for name in board_rows.PR_FIELDS} | fields
    return row | ({"textAt": text_at} if text_at else {})


def groups(*rows: dict, block: str = "merge-prs") -> dict:
    return {"id": "card-merge-triage", "type": "card", "children": [{"id": block, "type": "pr-groups", "repo": REPO, "groups": [{"id": "g1", "title": "A group", "summary": "One line.", "prs": list(rows)}]}]}


def gate(tmp_path: Path, shell: FakeShell, block: dict, *argv: str) -> int:
    config = tmp_path / "runner.json"
    config.write_text(json.dumps({"drive": DRIVE, "landing": {"repo": REPO, "fidelity_lane": ["fidelity-desk", "fidelity-desk-2"], "ledger": LEDGER, "checkout": "/checkout"}}))
    if shell.ledger is None:
        shell.ledger = {row.pr for row in board_rows.rows_of(block)}
    path = tmp_path / "card-merge-triage.json"
    path.write_text(json.dumps(block))
    return board_rows.main(["--config", str(config), str(path), *argv], shell)


@pytest.fixture
def shell() -> FakeShell:
    fake = FakeShell()
    fake.pull("42590")
    fake.graded("42590")
    return fake


def pushed_rows(shell: FakeShell) -> list[str]:
    [(_, pushed)] = shell.pushed
    return [item["id"] for _, item in board_rows.triage_items(pushed)] + [str(row["number"]) for *_, row in board_rows.pr_group_rows(pushed)]


def lines(capsys: pytest.CaptureFixture[str]) -> list[str]:
    return capsys.readouterr().out.splitlines()


def test_a_fresh_graded_open_row_pushes_a_copy_without_its_text_time(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str]) -> None:
    assert gate(tmp_path, shell, card(row("42590")), "--", "cc-present", "update-block", "--session", "s1") == 0

    [(argv, pushed)] = shell.pushed
    assert argv[:4] == ["cc-present", "update-block", "--session", "s1"]
    assert pushed == card(row("42590", text_at=None))
    assert lines(capsys) == ["rows 1 failing 0 ungraded 0 flags 0"]


def test_a_push_the_command_refuses_ends_with_a_push_failed_line_and_its_exit_code(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str]) -> None:
    shell.push_code = 1

    assert gate(tmp_path, shell, card(row("42590")), "--", "cc-present", "update-block", "--session", "s1") == 1

    assert lines(capsys) == ["rows 1 failing 0 ungraded 0 flags 0", "PUSH-FAILED cc-present update-block exited 1: the board kept its last revision, so no row above reached it"]


def test_a_row_behind_its_prs_head_fails_naming_the_new_head_and_pushes_nothing(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str]) -> None:
    shell.pull("42590", head=NEW_HEAD)

    assert gate(tmp_path, shell, card(row("42590")), "--", "cc-present", "update-block") == 1

    assert lines(capsys)[0] == f"merge-row-42590 42590 {HEAD[:7]} stale: GitHub's head is {NEW_HEAD[:7]}"
    assert shell.pushed == []


def test_a_row_with_no_grade_or_no_finder_at_its_head_warns_and_still_pushes(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str]) -> None:
    shell.pull("42591")
    shell.graded("42591", head=NEW_HEAD)
    shell.pull("42592")
    shell.post("review", "fidelity-desk-2", f"fidelity: missing {HEAD[:7]} rule/2 one added line", "42592")
    rows = card(row("42590"), row("42591", md="Keeps a copy of each release's Rust-built files."), row("42592", md="Ports the journal reader tests to the new tree."))

    assert gate(tmp_path, shell, rows, "--", "cc-present", "update-block") == 1

    assert lines(capsys) == [
        f"merge-row-42591 42591 {HEAD[:7]} ungraded: no fidelity grade from fidelity-desk or fidelity-desk-2 names {HEAD[:7]}",
        f"WARN merge-row-42591 42591 {HEAD[:7]} ungraded: no finder review names {HEAD[:7]}",
        f"WARN merge-row-42592 42592 {HEAD[:7]} ungraded: no finder review names {HEAD[:7]}",
        "rows 3 failing 1 ungraded 2 flags 0",
    ]
    [(_, pushed)] = shell.pushed
    assert pushed == card(row("42590", text_at=None), row("42592", md="Ports the journal reader tests to the new tree.", text_at=None))


@pytest.mark.parametrize(
    ("facts", "label", "missing"),
    [
        ([fact for fact in FACTS if fact["label"] not in ("Differs from plan", "Risk")], "#42590: a change", "Differs from plan, Risk"),
        ([fact | {"value": ""} if fact["label"] == "For users" else fact for fact in FACTS], "#42590: a change", "For users"),
        (FACTS, "#42590: ", "label"),
    ],
)
def test_a_row_missing_a_field_the_owner_decides_by_fails(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str], facts: list[dict], label: str, missing: str) -> None:
    assert gate(tmp_path, shell, card(row("42590", facts=facts) | {"label": label}), "--check") == 1

    assert lines(capsys)[0] == f"merge-row-42590 42590 {HEAD[:7]} missing: {missing}"


@pytest.mark.parametrize(("text_at", "reason"), [(BEFORE, f"old text: written {BEFORE}, not after {HEAD[:7]} was committed at {COMMITTED}"), (None, "old text: no textAt on the row")])
def test_words_not_written_after_the_head_was_committed_fail(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str], text_at: str | None, reason: str) -> None:
    assert gate(tmp_path, shell, card(row("42590", text_at=text_at)), "--check") == 1

    assert lines(capsys)[0] == f"merge-row-42590 42590 {HEAD[:7]} {reason}"


@pytest.mark.parametrize("state", ["CLOSED", "MERGED"])
def test_a_pr_github_no_longer_holds_open_fails(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str], state: str) -> None:
    shell.pull("42590", state=state)

    assert gate(tmp_path, shell, card(row("42590")), "--check") == 1

    assert lines(capsys)[0] == f"merge-row-42590 42590 {HEAD[:7]} not open: the PR is {state.lower()} on GitHub"


def test_a_root_ruling_that_supersedes_the_pr_warns_with_that_seq_and_never_refuses_the_row(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str]) -> None:
    """#281149 reopened #47188 after #280149 closed it, and the gate kept refusing its row on the close it parsed from root's prose (board-refresher-6, #281366)."""
    seq = shell.post("go", "root", "withdrawn: go for #42590 35aa930 (#131549). My own #130667 ruled it superseded…", path="/blobs/a.txt")
    shell.blobs["/blobs/a.txt"] = "withdrawn: go for #42590 35aa930 (#131549). My own #130667 ruled it superseded by the repeat list's deletion. p8b-step10 closes #42590 now."

    assert gate(tmp_path, shell, card(row("42590")), "--check") == 0
    assert f"WARN merge-row-42590 42590 {HEAD[:7]} root ruled it superseded or closed on cci #{seq}; drop the row unless a later ruling reopened it" in lines(capsys)

    shell.post("go", "root", f"go #42590 at {HEAD[:7]} (owner tap 2:10 PM)", "42590")
    shell.verdicts = {"merge-triage": {"merge-row-42590": {"verdict": "approved"}}}
    assert gate(tmp_path, shell, card(row("42590")), "--check") == 0


@pytest.mark.parametrize(
    ("text", "closed"),
    [
        ("p2-credentials: close #42519 and #42590 unmerged now", True),
        ("#42590 is CLOSED unmerged; its tests move to #42706", True),
        ("#42590 superseded by #43001", True),
        ("#43001 supersedes #42590", True),
        ("merge #42590 at 45cfa25 (owner 'these are approved'; this go records it so the card at 1792787026.160129 closes)", False),
        ("#43001 superseded by #42590", False),
        ("go #42590 at 35aa930, closing the gap #131210 named", False),
        ("(2) is #42590's declared red, closed by step 10, no action", False),
        ("#42590 now superseded by #43001", True),
        ("#42590 (4d8b3a1) landed on dev", True),
        ("3:32 AM root: finder major #160051 on #42590 is closed: refuted by the lane at #160239 on fidelity #160071 and root #160081", False),
        ("the red check of #42590 is closed", False),
        ("the flake in #42590 was closed", False),
    ],
)
def test_a_closing_ruling_must_tie_the_word_to_the_pr(text: str, closed: bool) -> None:
    assert board_rows.closes("42590", text) is closed


@pytest.mark.parametrize(
    ("separator", "accepted"),
    [(",", True), (", ", True), (",\n", True), (" and ", True), ("  and ", True), ("\tand\n", True), (", and ", True), (" ,  and ", True), ("\u00a0and\u00a0", True), (",and ", False), (" and", False), (" & ", False), ("  ", False)],
)
@pytest.mark.parametrize("verb", ["close", "closes", "closed", "closing", "supersedes", "superseding"])
def test_closing_lists_keep_their_reference_separator_language(separator: str, accepted: bool, verb: str) -> None:
    assert board_rows.closes("42590", f"root {verb} #42519{separator}#42590{separator}#42706") is accepted


def test_multispace_closing_list_has_linear_cpu_growth() -> None:
    code = """
import json
import sys
import time
sys.path.insert(0, sys.argv[1])
from long_running import board_rows
def measure(size):
    text = 'root closes ' + '  and '.join(f'#{1000 + at}' for at in range(size)) + '. Separately #1 remains open.'
    assert not board_rows.closes('1', text)
    start = time.process_time_ns()
    for _ in range(16):
        assert not board_rows.closes('1', text)
    return time.process_time_ns() - start
print(json.dumps({'small_cpu_ns': measure(8), 'large_cpu_ns': measure(16)}))
"""
    done = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(Path(board_rows.__file__).resolve().parents[1])], stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True)
    cost = json.loads(done.stdout)
    print(json.dumps(cost))
    assert cost["large_cpu_ns"] <= 8 * cost["small_cpu_ns"] + 1_000_000, f"closing-list CPU grew superlinearly: {cost}"


@pytest.mark.parametrize("pr", ["12", "13", "123", "0012", "999"])
@pytest.mark.parametrize("head", [HEAD, NEW_HEAD])
@pytest.mark.parametrize("withdrawn", [set(), {7}, {2, 7}])
def test_indexed_rulings_preserve_closures_revivals_and_standing_go_provenance(pr: str, head: str, withdrawn: set[int]) -> None:
    record = {"seq": 1, "kind": "decision", "text": "summary", "full": "root closes #12"}
    rulings = [
        record,
        record | {"seq": 2, "kind": "go", "text": f"go #12 at {HEAD[:7]}", "full": "the body does not repeat the reference"},
        record | {"seq": 3, "kind": "go", "text": f"go #13 at {HEAD[:7]}", "full": "no repeated reference", "voided": ["13"]},
        record | {"seq": 4, "text": "summary #13", "full": "root supersedes #12"},
        record | {"seq": 5, "kind": "go", "text": f"withdrawn: go #12 at {HEAD[:7]}", "full": f"go #12 at {HEAD[:7]}"},
        record | {"seq": 6, "text": "summary #123", "full": "root closes #123"},
        record | {"seq": 7, "kind": "go", "text": f"go #12 at {HEAD[:7]}", "full": "the body does not repeat the reference"},
        record | {"seq": 8, "kind": "go", "text": f"go #12 at {NEW_HEAD[:7]}", "full": "TAP-REQUIRED without repeating the reference"},
        record | {"seq": 9, "text": "no go clause", "full": f"go #13 at {HEAD[:7]}"},
        record | {"seq": 10, "text": "summary", "full": "root closes #0012"},
    ]
    rulings.append(record)
    encoded = json.dumps(rulings)
    selected = board_rows.rulings_by_pr(rulings).get(pr, [])

    assert board_rows.closing_ruling(selected, pr, head) == board_rows.closing_ruling(rulings, pr, head)
    assert board_rows.standing_gos(selected, withdrawn, pr, head) == board_rows.standing_gos(rulings, withdrawn, pr, head)
    assert json.dumps(rulings) == encoded


def test_ruling_index_keeps_duplicate_source_identity_and_order() -> None:
    first = {"seq": 2, "text": "#12 #12", "full": "closes #12 #12"}
    second = {"seq": 1, "text": "#12", "full": "no reference in the body"}
    selected = board_rows.rulings_by_pr([first, second, first])["12"]
    assert len(selected) == 3
    assert selected[0] is first
    assert selected[1] is second
    assert selected[2] is first


@pytest.mark.parametrize("count", [1, 91])
@pytest.mark.parametrize("history", [32, 10000])
def test_board_ruling_predicates_visit_only_the_requested_pr_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, count: int, history: int) -> None:
    shell = FakeShell()
    prs = [str(42590 + at) for at in range(count)]
    for pr in prs:
        shell.pull(pr)
        shell.graded(pr)
    shell.post("decision", "root", "closes #42590", "42590")
    shell.post("go", "root", f"go #42590 at {HEAD[:7]}", "42590")
    shell.verdicts = {"merge-prs": {"42590": {"verdict": "approved", "head": HEAD}}}
    for _ in range(history):
        shell.post("go", "root", f"go #90000 at {HEAD[:7]}", "90000")
    calls = {"closes": 0, "go_heads": 0}
    original_closes, original_heads = board_rows.closes, grades.go_heads

    def closes(pr, text):
        calls["closes"] += 1
        return original_closes(pr, text)

    def heads(record):
        calls["go_heads"] += 1
        return original_heads(record)

    monkeypatch.setattr(board_rows, "closes", closes)
    monkeypatch.setattr(grades, "go_heads", heads)

    assert gate(tmp_path, shell, groups(*(pr_row(pr) for pr in prs)), "--check") == 0
    assert calls == {"closes": 2, "go_heads": 2}, f"unrelated ruling predicates were evaluated: {calls}"
    print(json.dumps({"rows": count, "unrelated_rulings": history, "predicate_calls": calls}))


def test_a_failed_ruling_read_keeps_its_warning_and_builds_no_index(tmp_path: Path, shell: FakeShell, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def read(shell, drive, lanes):
        raise board_rows.ReadFailed("rulings unavailable")

    def index(rulings):
        pytest.fail("a failed ruling read must not build an index")

    monkeypatch.setattr(board_rows, "root_rulings", read)
    monkeypatch.setattr(board_rows, "rulings_by_pr", index)

    assert gate(tmp_path, shell, card(row("42590")), "--check") == 0
    assert lines(capsys) == ["WARN the root rulings did not read (rulings unavailable); no row is checked for a closing ruling or a root go at its head, and no renamed row is excused by a re-key", "rows 1 failing 0 ungraded 0 flags 0"]


def test_a_decided_row_pushed_under_another_block_fails_before_anything_is_sent(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str]) -> None:
    shell.verdicts = {"merge-triage": {"merge-row-42590": {"verdict": "approved"}}}

    assert gate(tmp_path, shell, card(row("42590"), block="merge-triage-new-copy"), "--", "cc-present", "update-block") == 1

    assert lines(capsys)[0] == f"merge-row-42590 42590 {HEAD[:7]} moved: decided under block merge-triage, pushed under merge-triage-new-copy"
    assert shell.pushed == []


def test_a_decided_row_kept_in_its_block_or_dropped_from_the_push_passes(tmp_path: Path, shell: FakeShell) -> None:
    shell.verdicts = {"merge-triage": {"merge-row-42590": {"verdict": "approved"}, "merge-row-42593": {"verdict": "approved"}}}

    assert gate(tmp_path, shell, card(row("42590")), "--check") == 0


@pytest.mark.parametrize(
    "rekey",
    [
        "Re-key merge-row-42590 to merge-row-42590-7b1c2d3 (#131585)",
        "re-key merge-row-42590 to 7b1c2d3 (#131714, finder fixed)",
        "go moves with the owner-ordered deletions: #42590 35aa930 -> 7b1c2d3; his taps carry, board rows re-key without a new tap",
    ],
)
def test_a_decided_row_renamed_passes_only_with_a_root_re_key_naming_it(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str], rekey: str) -> None:
    shell.pull("42590", head=NEW_HEAD)
    shell.graded("42590", head=NEW_HEAD)
    shell.verdicts = {"merge-triage": {"merge-row-42590": {"verdict": "approved"}}}
    renamed = card(row("42590", head=NEW_HEAD, key=f"-{NEW_HEAD[:7]}"))

    assert gate(tmp_path, shell, renamed, "--check") == 1
    assert lines(capsys)[0] == f"merge-row-42590-{NEW_HEAD[:7]} 42590 {NEW_HEAD[:7]} renamed: decided as merge-row-42590, and no root re-key record names merge-row-42590-{NEW_HEAD[:7]}"

    shell.post("go", "root", rekey)
    assert gate(tmp_path, shell, renamed, "--check") == 0


def test_a_re_key_for_another_head_does_not_excuse_the_rename(tmp_path: Path, shell: FakeShell) -> None:
    shell.pull("42590", head=NEW_HEAD)
    shell.graded("42590", head=NEW_HEAD)
    shell.verdicts = {"merge-triage": {"merge-row-42590": {"verdict": "approved"}}}
    shell.post("go", "root", "Re-key merge-row-42590 to merge-row-42590-ad4a275")

    assert gate(tmp_path, shell, card(row("42590", head=NEW_HEAD, key=f"-{NEW_HEAD[:7]}")), "--check") == 1


def test_two_rows_opening_with_the_same_sentence_are_flagged_without_failing(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str]) -> None:
    shell.pull("42785")
    shell.graded("42785")
    serve = "Tests only. The serve half of one background conversion of old Mac chats; #42767 is the app half."
    app = "Tests only. The app half of one background conversion of old Mac chats; #42785 is the serve half."

    assert gate(tmp_path, shell, card(row("42590", md=serve), row("42785", md=app)), "--check", "--json") == 0

    [flag] = json.loads(capsys.readouterr().out)["flags"]
    assert flag["rows"] == ["merge-row-42590", "merge-row-42785"]
    assert flag["jaccard"] > board_rows.SAME_JACCARD


def test_rows_that_open_differently_are_not_flagged(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str]) -> None:
    shell.pull("42785")
    shell.graded("42785")

    assert gate(tmp_path, shell, card(row("42590", md="Tests only. Saves the old converter's chat layouts as samples."), row("42785", md="Tests only. Copies sand's Go code into a new folder for the rebuild.")), "--check", "--json") == 0

    assert json.loads(capsys.readouterr().out)["flags"] == []


def test_a_github_or_board_outage_warns_and_fails_no_row_on_what_it_could_not_read(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str]) -> None:
    shell.gh_fails = True
    shell.outcomes_fail = True

    assert gate(tmp_path, shell, card(row("42590", text_at=None)), "--check") == 0

    out = lines(capsys)
    assert out[0].startswith("WARN GitHub refused the PR read for #42590: HTTP 502: Bad Gateway")
    assert out[1].startswith("WARN the board's verdicts did not read (cc-present outcomes exited 1: no board for this session)")


def test_a_grade_from_a_successor_fidelity_desk_counts_with_no_config_edit(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str]) -> None:
    """Root #200233: fidelity-desk-13 succeeded a listed desk, and the gate read its grades as missing until root edited runner.json."""
    shell.pull("42590")
    shell.post("review", "fidelity-desk-13", f"fidelity: ok {HEAD[:7]} checked rule/1", "42590")
    shell.post("review", "finder-desk", f"finder: ok {HEAD[:7]} no majors", "42590")

    assert gate(tmp_path, shell, card(row("42590")), "--", "cc-present", "update-block") == 0
    assert not [line for line in lines(capsys) if "ungraded" in line and "fidelity grade" in line]


def fidelity_only(shell: FakeShell, pr: str) -> None:
    shell.pull(pr)
    shell.post("review", "fidelity-desk", f"fidelity: ok {HEAD[:7]} checked rule/1", pr)


def warned(capsys: pytest.CaptureFixture[str]) -> int:
    return json.loads(capsys.readouterr().out)["counts"]["ungraded"]


@pytest.mark.parametrize(
    ("lane", "text", "ungraded"),
    [
        ("finder-desk", f"finder: ok {HEAD[:7]} no majors", 0),
        ("finder-desk", f"finder: major {HEAD[:7]} 2 open: vet.sh:40 row missing", 0),
        ("finder-desk-2", f"finder: ok {HEAD[:7]} no majors", 0),
        ("p8b-step8", f"finder: ok {HEAD[:7]} no majors", 1),
        ("p8b-step8", f"p8b-step8 REVIEW reply to finder 202370 on #42591 {HEAD[:7]}; new HEAD {NEW_HEAD[:7]}.", 1),
    ],
    ids=["desk-ok", "desk-major", "desk-successor", "lane-finder-line", "lane-reply-naming-the-finder"],
)
def test_only_a_finder_desk_finder_line_counts_at_its_head(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str], lane: str, text: str, ungraded: int) -> None:
    fidelity_only(shell, "42591")
    shell.post("review", lane, text, "42591")

    assert gate(tmp_path, shell, card(row("42591")), "--check", "--json") == 0

    assert warned(capsys) == ungraded


def test_a_ref_less_finder_review_about_another_pr_does_not_count(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str]) -> None:
    fidelity_only(shell, "42591")
    shell.post("review", "finder-desk", f"finder: major {HEAD[:7]} 2 open on [42751]")
    shell.post("review", "finder-desk", f"finder: ok {HEAD[:7]} on #42591", "42751")
    shell.post("review", "finder-desk", f"finder: ok {HEAD[:7]} #42591", drive="other")

    assert gate(tmp_path, shell, card(row("42591")), "--check", "--json") == 0

    assert warned(capsys) == 1


@pytest.mark.parametrize(("saved", "ungraded"), [([f"42591-{HEAD[:7]}.md"], 0), ([f"42591-{NEW_HEAD[:7]}.md", f"42592-{HEAD[:7]}.md"], 1)])
def test_a_saved_codex_finder_reply_counts_only_for_its_pr_and_head(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str], saved: list[str], ungraded: int) -> None:
    fidelity_only(shell, "42591")
    shell.saved = saved

    assert gate(tmp_path, shell, card(row("42591")), "--check", "--json") == 0

    assert warned(capsys) == ungraded


@pytest.mark.parametrize("pr", ["12", "13", "012", "123", "999"])
@pytest.mark.parametrize("head", [HEAD, NEW_HEAD])
def test_indexed_board_reviews_keep_the_original_grade_finder_and_warning_order(pr: str, head: str) -> None:
    record = {"seq": 1, "drive": DRIVE, "kind": "review", "lane": "finder-desk", "text": "finder: ok", "refs": {"prs": [f"{REPO}#12"]}}
    reviews = [
        record | {"kind": "head", "fields": {"attrs": {f"head:{REPO}#12": HEAD, f"head:{REPO}#13": NEW_HEAD}}, "refs": {"prs": [f"{REPO}#12", f"{REPO}#13"]}},
        record | {"kind": "head", "text": NEW_HEAD},
        record | {"seq": 2, "origin_seq": 100},
        record | {"seq": 3, "refs": {"prs": [f"{REPO}#13"]}},
        record | {"seq": 4, "lane": "fidelity-desk", "text": f"fidelity: missing {HEAD[:7]} rule/1", "refs": {"prs": [12, 12, f"{REPO.lower()}#13"]}},
        record | {"seq": 5, "lane": "fidelity-desk-13", "text": f"fidelity: ok {NEW_HEAD[:7]} rule/1"},
        record | {"seq": 6, "text": f"finder: major {NEW_HEAD[:7]} #12 [13]", "refs": {}},
        record | {"seq": 7, "text": f"finder: major {HEAD[:7]} #12 [13]", "refs": {"prs": ["other/repo#12"]}},
        record | {"seq": 8, "text": f"finder: major {HEAD[:7]} #12", "refs": {"prs": ["12"]}},
        record | {"seq": 9, "text": f"finder: ok {HEAD[:7]} #123a [012] #123", "refs": {}},
        record | {"seq": 10, "drive": "another-drive", "text": f"finder: major {HEAD[:7]}"},
        record | {"seq": 11, "lane": "p8", "text": f"finder: major {HEAD[:7]}"},
        record | {"seq": 12, "text": f"fidelity: ok {HEAD[:7]} finder"},
        record | {"seq": 13, "lane": "fidelity-desk", "text": f"fidelity: ok {HEAD[:7]} #12 rule/1", "refs": {}},
    ]
    reviews.append(reviews[4])
    encoded = json.dumps(reviews)
    selected = board_rows.reviews_by_pr(reviews, REPO).get(pr, [])
    lanes = grades.with_successors(["fidelity-desk"], reviews)
    saved = [f"12-{HEAD[:7]}.md", f"13-{NEW_HEAD[:7]}.md"]
    [named] = board_rows.rows_of(card(row(pr, head)))

    assert grades.fidelity_grades_in(selected, DRIVE, lanes, REPO, pr) == grades.fidelity_grades_in(reviews, DRIVE, lanes, REPO, pr)
    assert grades.finder_reviews_in(selected, DRIVE, REPO, pr) == grades.finder_reviews_in(reviews, DRIVE, REPO, pr)
    assert board_rows.graded_warnings(selected, saved, DRIVE, lanes, REPO, named) == board_rows.graded_warnings(reviews, saved, DRIVE, lanes, REPO, named)
    assert json.dumps(reviews) == encoded


def test_a_board_review_index_preserves_duplicate_source_records_without_repeating_duplicate_refs() -> None:
    first = {"seq": 2, "refs": {"prs": [12, f"{REPO}#12", 12]}, "text": "first"}
    second = {"seq": 1, "refs": {}, "text": "#12 [12] #12"}

    selected = board_rows.reviews_by_pr([first, second, first], REPO)["12"]

    assert len(selected) == 3
    assert selected[0] is first
    assert selected[1] is second
    assert selected[2] is first


@pytest.mark.parametrize("count", [1, 91])
@pytest.mark.parametrize("history", [32, 10000])
@pytest.mark.parametrize("same_pr", [False, True])
def test_board_checks_traverse_fetched_review_history_only_twice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, count: int, history: int, same_pr: bool) -> None:
    shell = FakeShell()
    prs = [str(42590 if same_pr else 42590 + at) for at in range(count)]
    for pr in prs:
        shell.pull(pr)
        shell.graded(pr)
    shell.pull("80000")
    shell.graded("80000")
    shell.post("go", "root", f"go #80000 at {HEAD[:7]}", "80000")
    for _ in range(history):
        shell.post("review", "unrelated", "a review for another PR", "90000")
    block = groups(*(pr_row(pr) for pr in prs))
    block["children"].append({"id": "merge-approved-landing", "type": "markdown", "md": "- #80000: One writer per run."})

    class CountedReviews(list[dict]):
        visits = 0

        def __iter__(self) -> Iterator[dict]:
            for record in super().__iter__():
                self.visits += 1
                yield record

    fetched = CountedReviews()
    original = board_rows.cci.records

    def records(run, drive, *filters, **options):
        found = original(run, drive, *filters, **options)
        if filters == ("--kind=review", "--kind=head"):
            fetched.extend(found)
            return fetched
        return found

    monkeypatch.setattr(board_rows.cci, "records", records)

    assert gate(tmp_path, shell, block, "--check") == 0
    assert len(fetched) == history + 2 * count + 2
    assert fetched.visits == 2 * len(fetched)
    indexed_visits = fetched.visits
    fetched.visits = 0
    for named in board_rows.rows_of(block):
        assert board_rows.graded_warnings(fetched, [], DRIVE, ["fidelity-desk"], REPO, named) == []
    assert fetched.visits == 2 * count * len(fetched)
    print(json.dumps({"rows": count, "same_pr": same_pr, "unrelated_history": history, "fetched_records": len(fetched), "indexed_history_visits": indexed_visits, "baseline_history_visits": fetched.visits}))


def test_a_failed_board_review_read_keeps_its_warning_and_does_not_build_an_index(tmp_path: Path, shell: FakeShell, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    original = board_rows.cci.records

    def records(run, drive, *filters, **options):
        if filters == ("--kind=review", "--kind=head"):
            raise board_rows.ReadFailed("reviews unavailable")
        return original(run, drive, *filters, **options)

    def index(reviews, repo):
        pytest.fail("a failed review read must not build an index")

    monkeypatch.setattr(board_rows.cci, "records", records)
    monkeypatch.setattr(board_rows, "reviews_by_pr", index)

    assert gate(tmp_path, shell, card(row("42590")), "--check") == 0
    assert lines(capsys) == ["WARN the grades did not read (reviews unavailable); no row is checked for a grade or finder at its head", "rows 1 failing 0 ungraded 0 flags 0"]


def test_a_fresh_graded_pr_groups_row_pushes_a_copy_without_its_text_time(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str]) -> None:
    assert gate(tmp_path, shell, groups(pr_row("42590")), "--", "cc-present", "update-block") == 0

    [(_, pushed)] = shell.pushed
    assert pushed == groups(pr_row("42590", text_at=None))
    assert lines(capsys) == ["rows 1 failing 0 ungraded 0 flags 0"]


def test_a_pr_groups_row_behind_its_prs_head_or_missing_a_field_fails(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str]) -> None:
    shell.pull("42590", head=NEW_HEAD)

    assert gate(tmp_path, shell, groups(pr_row("42590", review="")), "--", "cc-present", "update-block") == 1

    assert lines(capsys)[0] == f"42590 42590 {HEAD[:7]} missing: review; stale: GitHub's head is {NEW_HEAD[:7]}"
    assert shell.pushed == []


def test_a_triage_verdict_moved_onto_its_pr_groups_row_passes(tmp_path: Path, shell: FakeShell) -> None:
    shell.verdicts = {"merge-triage": {}, "merge-prs": {"42590": {"verdict": "approved", "head": HEAD[:7]}}}

    assert gate(tmp_path, shell, groups(pr_row("42590")), "--check") == 0


def test_a_triage_verdict_left_behind_fails_the_pr_groups_row_until_a_re_key_names_its_head(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str]) -> None:
    shell.verdicts = {"merge-triage": {"merge-row-42590": {"verdict": "approved"}}}

    assert gate(tmp_path, shell, groups(pr_row("42590")), "--check") == 1
    assert lines(capsys)[0] == f"42590 42590 {HEAD[:7]} renamed: decided as merge-row-42590, and no root re-key record names 42590"

    shell.post("go", "root", "re-key 42590 to ad4a275")
    assert gate(tmp_path, shell, groups(pr_row("42590")), "--check") == 1

    shell.post("go", "root", f"re-key #42590 to {HEAD[:7]}")
    assert gate(tmp_path, shell, groups(pr_row("42590")), "--check") == 0


@pytest.mark.parametrize(
    ("kind", "lane", "text", "pr"),
    [
        ("head", "p8e", f"Restacked onto #42722 0c2cb1d: #42815 43072de and #42590 {HEAD[:11]} (patch-identical to go'd 8bb87a3).", None),
        ("go", "root", f"merge #42711 at e6d93c0 and #42590 at {HEAD[:7]}: his taps and go carry, the patch is otherwise identical.", None),
        ("head", "p8f", f"dar-p8f-replica-plan {HEAD[:7]} on signal-1 509a21c (#133292). Patch-identical.", "42590"),
        ("head", "p8e", f"#42590 {HEAD[:7]} restacked onto dev; finder pending.", None),
    ],
)
def test_a_record_saying_a_head_carries_counts_as_no_grade_or_finder_there(tmp_path: Path, capsys: pytest.CaptureFixture[str], kind: str, lane: str, text: str, pr: str | None) -> None:
    """p8a-step7 #199440: words alone never carry; a head's grade and finder are the records naming it, and a tap moves only by the landing runner's carry."""
    fake = FakeShell()
    fake.pull("42590")
    fake.post(kind, lane, text, pr)

    assert gate(tmp_path, fake, card(row("42590")), "--check") == 1
    assert lines(capsys)[-1] == "rows 1 failing 1 ungraded 1 flags 0"


def approved(pr: str, head: str = HEAD, at: str = WRITTEN) -> dict:
    return {"verdict": "approved", "head": head[:7], "at": at}


def rejected(pr: str, head: str = HEAD, at: str = WRITTEN) -> dict:
    return {"verdict": "rejected", "head": head[:7], "at": at}


def claimed(summary: str, *rows: dict) -> dict:
    block = groups(*rows)
    block["children"][0]["groups"][0]["summary"] = summary
    return block


def test_a_row_root_approved_at_its_head_fails_until_the_board_holds_the_approval_or_the_go_is_withdrawn(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str]) -> None:
    seq = shell.post("go", "root", f"go #42590 at {HEAD[:7]} (owner tap 9:12 PM)", "42590")

    assert gate(tmp_path, shell, groups(pr_row("42590")), "--check") == 1
    assert lines(capsys)[0] == f"42590 42590 {HEAD[:7]} approved: root go cci #{seq} names it at {HEAD[:7]}, and neither a board approval there nor his approved head in its carried list backs it; add his approved head to carried, import the verdict, or drop the row"

    shell.verdicts = {"merge-prs": {"42590": approved("42590")}}
    assert gate(tmp_path, shell, groups(pr_row("42590")), "--check") == 0

    shell.verdicts = {}
    shell.post("withdraw", "root", "withdrawn: that go named the wrong head")
    shell.records[-1]["re"] = seq
    assert gate(tmp_path, shell, groups(pr_row("42590")), "--check") == 0


def test_a_root_withdraw_naming_the_go_in_its_text_clears_the_approval_check(tmp_path: Path, shell: FakeShell) -> None:
    """board-refresher-2 #199137: root's WITHDRAW #198682 answered #198673 by --re and named the go only as 'withdraws go #198635', so the gate kept refusing the row it ordered."""
    seq = shell.post("go", "root", f"go #42590 at {HEAD[:7]} (owner tap 9:12 PM)", "42590")
    asked = shell.post("decide", "root", "row #42590 at its head with his 1:00 PM words")
    assert gate(tmp_path, shell, groups(pr_row("42590")), "--check") == 1
    shell.post("withdraw", "root", f"10:37 PM root withdraws go #{seq} on #42590: it rested on design words, not a tap of the PR")
    shell.records[-1]["re"] = asked
    assert gate(tmp_path, shell, groups(pr_row("42590")), "--check") == 0


def test_a_newer_tap_required_go_at_the_same_head_overrides_an_older_exempt_go(tmp_path: Path, shell: FakeShell) -> None:
    """#46598 at bcf89da failed on an Oct 31 exempt go (#251254) though root's newer go #281042 at that head was TAP-REQUIRED (board-refresher-6, #282335)."""
    shell.post("go", "root", f"go #42590 at {HEAD[:7]} on green [exempt: ci-tooling]", "42590")
    assert gate(tmp_path, shell, groups(pr_row("42590")), "--check") == 1
    shell.post("go", "root", f"go #42590 at {HEAD[:7]} on green [TAP-REQUIRED first-ask row]", "42590")
    assert gate(tmp_path, shell, groups(pr_row("42590")), "--check") == 0


@pytest.mark.parametrize("condition", ["[TAP-REQUIRED; signal stack restacked onto dev]", "TAP-CARRIED: the owner's tap at 1a2b3c4 carries to it"])
def test_a_go_conditioned_on_his_tap_is_no_approval(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str], condition: str) -> None:
    shell.post("go", "root", f"go #42590 at {HEAD[:7]} {condition}", "42590")
    shell.pull("42740")
    shell.graded("42740")
    shell.post("go", "root", f"go #42740 at {HEAD[:7]} {condition}", "42740")
    block = groups(pr_row("42590"))
    block["children"].append({"id": "merge-approved-landing", "type": "markdown", "md": "- #42740: One writer per run."})

    assert gate(tmp_path, shell, block, "--check") == 1
    assert lines(capsys)[0] == f"merge-approved-landing#42740 42740 {HEAD[:7]} unapproved: listed under merge-approved-landing with no go, approval or carry at GitHub's head {HEAD[:7]}"


@pytest.mark.parametrize(("verdict_head", "failing"), [(HEAD, 1), (NEW_HEAD, 0)])
def test_a_row_he_rejected_at_its_head_fails_and_returns_at_a_new_head(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str], verdict_head: str, failing: int) -> None:
    shell.verdicts = {"merge-prs": {"42590": rejected("42590", verdict_head)}}

    assert gate(tmp_path, shell, groups(pr_row("42590")), "--check") == failing
    assert lines(capsys)[-1] == f"rows 1 failing {failing} ungraded 0 flags 0"


@pytest.mark.parametrize(("state", "failing"), [("MERGED", 1), ("OPEN", 0)])
def test_a_blocker_naming_a_merged_pr_fails(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str], state: str, failing: int) -> None:
    shell.pull("42740", state=state)

    assert gate(tmp_path, shell, groups(pr_row("42590", blocker="#42740 merges first")), "--check") == failing
    assert lines(capsys)[-1] == f"rows 1 failing {failing} ungraded 0 flags 0"


@pytest.mark.parametrize(
    ("summary", "verdicts", "failing"),
    [
        ("Gateway fix: your one tap approves all five.", {"42590": approved("42590"), "42593": rejected("42593", NEW_HEAD)}, 1),
        ("Gateway fix: your one tap approves both.", {"42590": approved("42590"), "42593": approved("42593")}, 0),
        ("Gateway fix in two steps.", {"42593": rejected("42593", NEW_HEAD)}, 0),
    ],
)
def test_a_one_tap_claim_over_a_rejected_pr_fails(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str], summary: str, verdicts: dict, failing: int) -> None:
    shell.pull("42593")
    shell.graded("42593")
    shell.verdicts = {"merge-prs": verdicts}

    assert gate(tmp_path, shell, claimed(summary, pr_row("42590"), pr_row("42593")), "--check") == failing
    if failing:
        assert lines(capsys)[0].endswith("one tap: its words say one tap approves 2 PRs, but #42593 rejected on the board")


@pytest.mark.parametrize(("state", "failing"), [("MERGED", 1), ("CLOSED", 1), ("OPEN", 0)])
def test_a_pr_listed_in_a_markdown_block_after_it_merged_or_closed_fails(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str], state: str, failing: int) -> None:
    shell.pull("42740", state=state)
    block = groups(pr_row("42590"))
    block["children"].append({"id": "merge-fixing", "type": "markdown", "md": "**Being fixed or checked.**\n\n- #42740: One writer per run."})

    assert gate(tmp_path, shell, block, "--check") == failing
    if failing:
        assert lines(capsys)[0] == f"merge-fixing#42740 42740 {HEAD[:7]} listed: the PR is {state.lower()} on GitHub and still listed under merge-fixing"


@pytest.mark.parametrize(("md", "failing"), [("Old-path check: pending.", 1), ("Being reworked on your 11:17 PM note.", 1), (f"Being reworked at {HEAD[:7]}.", 1), (f"Was being reworked at {NEW_HEAD[:7]}.", 0), ("Old-path check: clean.", 0)])
def test_a_row_whose_own_words_say_pending_or_being_reworked_asks_no_tap(tmp_path: Path, shell: FakeShell, md: str, failing: int) -> None:
    assert gate(tmp_path, shell, groups(pr_row("42590", md=md)), "--check") == failing


@pytest.mark.parametrize(("graded", "failing"), [(True, 0), (False, 1)])
def test_an_open_pr_in_the_approved_list_needs_a_go_and_a_fidelity_ok_at_its_head(tmp_path: Path, shell: FakeShell, graded: bool, failing: int) -> None:
    shell.pull("42740")
    shell.post("go", "root", f"go #42740 at {HEAD[:7]} (owner tap 9:12 PM)", "42740")
    if graded:
        shell.graded("42740")
    block = groups(pr_row("42590"))
    block["children"].append({"id": "merge-approved-landing", "type": "markdown", "md": "- #42740: One writer per run."})

    assert gate(tmp_path, shell, block, "--check") == failing


@pytest.mark.parametrize(("other_title", "same_group", "failing"), [("Runs one background server per environment", False, 1), ("Runs one background server per environment", True, 0), ("Keeps each staging folder apart", False, 0)])
def test_rows_in_different_groups_whose_titles_read_alike_fail(tmp_path: Path, shell: FakeShell, other_title: str, same_group: bool, failing: int) -> None:
    shell.pull("42593")
    shell.graded("42593")
    block = groups(pr_row("42590", title="Runs one background server for each environment"))
    second = pr_row("42593", title=other_title)
    if same_group:
        block["children"][0]["groups"][0]["prs"].append(second)
    else:
        block["children"][0]["groups"].append({"id": "g2", "title": "Another group", "summary": "One line.", "prs": [second]})

    assert gate(tmp_path, shell, block, "--check") == failing


SINCE = {"changed": "the upload now retries twice", "because": "a failed upload retries instead of failing the turn"}


def changed(pr: str, head: str = HEAD) -> str:
    return f"TAP-NOT-CARRIED #{pr}: the owner's tap at {NEW_HEAD[:10]} (1:00 PM Oct 26 Pacific) on board block merge-prs does not carry to {head[:10]}: the judge reads new behavior since his tap: It retries."


def kept(pr: str) -> str:
    return f"TAP-KEPT #{pr}: the owner's tap at {NEW_HEAD[:10]} (1:00 PM Oct 26 Pacific) on board block merge-prs can carry to {HEAD[:10]}, a fix the judge reads as changing nothing he approved: A nil check."


@pytest.mark.parametrize(("lane", "head"), [("desk-runner", HEAD), ("root", HEAD), ("desk-runner", NEW_HEAD)], ids=["runner-at-head", "root-words", "runner-at-another-head"])
def test_a_row_at_a_head_he_did_not_approve_waits_until_the_landing_runner_finds_it_changes_what_he_approved(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str], lane: str, head: str) -> None:
    """board-refresher-2 (root #213579): a re-ask rests on the runner's tap judge, never on a phrase in a root record."""
    shell.verdicts = {"merge-prs": {"42590": approved("42590", NEW_HEAD)}}
    shell.post("decide", "root", f"#42590 {HEAD[:7]} adds a retry he has not seen: the row re-asks his tap", "42590")

    assert gate(tmp_path, shell, groups(pr_row("42590") | {"since": SINCE}), "--check") == 1
    assert lines(capsys)[0] == f"42590 42590 {HEAD[:7]} re-ask: he approved it at {NEW_HEAD[:7]}, and the landing runner has not found that {HEAD[:7]} changes what he approved; it waits in Being fixed or checked"

    shell.post("note", lane, changed("42590", head), "42590")
    assert gate(tmp_path, shell, groups(pr_row("42590") | {"since": SINCE}), "--check") == (0 if (lane, head) == ("desk-runner", HEAD) else 1)


@pytest.mark.parametrize("since", [None, {"changed": "the upload now retries twice"}, {"changed": " ", "because": "a failed upload retries"}])
def test_a_re_asked_row_without_what_changed_since_his_tap_fails(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str], since: dict | None) -> None:
    """Owner 6:59 PM Oct 29 (root #194315): 'if ur asking for a PR tap that Ive alrady apporiove dyou ned to tell me whats changed / why ur showing it gain'."""
    shell.verdicts = {"merge-prs": {"42590": approved("42590", NEW_HEAD)}}
    shell.post("note", "desk-runner", changed("42590"), "42590")

    assert gate(tmp_path, shell, groups(pr_row("42590") | ({"since": since} if since else {})), "--", "cc-present", "update-block") == 1

    assert lines(capsys)[0] == f"42590 42590 {HEAD[:7]} since: he approved it at {NEW_HEAD[:7]}, so the row opens 'You approved this at 1:00 PM Oct 26. Since then: <what changed, in plain words>. Shown again because: <the behavior change>.'; give its `since` both `changed` and `because`"
    assert shell.pushed == []


def test_a_re_asked_row_opens_with_his_tap_time_what_changed_and_why_it_is_shown_again(tmp_path: Path, shell: FakeShell) -> None:
    shell.verdicts = {"merge-prs": {"42590": approved("42590", NEW_HEAD, at="2026-10-30T01:59:00Z")}}
    shell.post("note", "desk-runner", changed("42590"), "42590")

    assert gate(tmp_path, shell, groups(pr_row("42590") | {"since": SINCE | {"because": "a failed upload retries instead of failing the turn."}}), "--", "cc-present", "update-block") == 0

    [(_, pushed)] = shell.pushed
    [*_, row] = next(board_rows.pr_group_rows(pushed))
    assert row == pr_row("42590", text_at=None, change="You approved this at 6:59 PM Oct 29. Since then: the upload now retries twice. Shown again because: a failed upload retries instead of failing the turn. change in plain words")


@pytest.mark.parametrize("verdicts", [{}, {"merge-prs": {"42590": approved("42590")}}, {"merge-prs": {"42590": approved("42590", NEW_HEAD)}}], ids=["untapped", "tapped-here", "carried"])
def test_a_row_he_is_not_asked_again_needs_no_since_and_pushes_without_it(tmp_path: Path, shell: FakeShell, verdicts: dict) -> None:
    shell.verdicts = verdicts

    assert gate(tmp_path, shell, groups(pr_row("42590") | {"carried": [NEW_HEAD[:7]], "since": SINCE}), "--", "cc-present", "update-block") == 0

    [(_, pushed)] = shell.pushed
    assert pushed == groups(pr_row("42590", text_at=None) | {"carried": [NEW_HEAD[:7]]})


@pytest.mark.parametrize(("carried", "failing"), [([], 1), ([NEW_HEAD[:7]], 0)])
def test_a_row_root_go_d_at_its_head_passes_when_its_carried_list_names_his_approved_head(tmp_path: Path, shell: FakeShell, carried: list[str], failing: int) -> None:
    shell.post("go", "root", f"go #42590 at {HEAD[:7]}: patch-identical replay of his tap", "42590")
    shell.verdicts = {"merge-prs": {"42590": approved("42590", NEW_HEAD)}}

    assert gate(tmp_path, shell, groups(pr_row("42590") | {"carried": carried}), "--check") == failing


RED = "DEV-RED since 12:03 AM: dev's buildkite/test fails lint on build 1. No PR can land until this is fixed."


def test_a_red_dev_banners_the_card_and_keeps_every_row_on_it(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str]) -> None:
    shell.pull("42591")
    shell.graded("42591")
    shell.trunk(RED)
    shell.post("opened", "fix-dev", "opened #42591", pr="42591", topic="dev-red")

    assert gate(tmp_path, shell, groups(pr_row("42590"), pr_row("42591")), "--", "cc-present", "update-block") == 0

    banner = "dev's test build is red since 12:03 AM; nothing lands until #42591 fixes it."
    assert f"BANNER {banner}" in lines(capsys)
    pushed = shell.pushed[0][1]["children"][0]
    assert pushed["prompt"] == banner
    assert [row["number"] for group in pushed["groups"] for row in group["prs"]] == [42590, 42591]


def test_a_red_dev_still_fails_a_row_missing_a_field(tmp_path: Path, shell: FakeShell) -> None:
    shell.trunk(RED)

    assert gate(tmp_path, shell, groups(pr_row("42590", review="")), "--", "cc-present", "update-block") == 1

    assert shell.pushed == []


@pytest.mark.parametrize("posted", [[RED, "DEV-GREEN dev's buildkite/test passes again; it was red since 12:03 AM."], ["POST-MERGE-RED since 12:03 AM: dev's buildkite/test fails publish on build 1. Landing continues."]])
def test_a_green_or_post_merge_red_dev_carries_no_banner(tmp_path: Path, shell: FakeShell, posted: list[str]) -> None:
    shell.trunk(*posted)

    assert gate(tmp_path, shell, groups(pr_row("42590")), "--", "cc-present", "update-block") == 0

    assert "prompt" not in shell.pushed[0][1]["children"][0]


def test_one_refused_row_is_left_out_and_every_other_row_and_list_line_still_pushes(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str]) -> None:
    shell.pull("42591", head=NEW_HEAD)
    shell.graded("42591")
    shell.pull("42593", state="MERGED")
    landing = {"id": "landing-list", "type": "markdown", "md": "- #42590: lands next\n- #42593: landed already\n"}
    board = card(row("42590"), row("42591", md="Keeps a copy of each release's Rust-built files."))
    board["children"].append(landing)

    assert gate(tmp_path, shell, board, "--", "cc-present", "update-block") == 1

    assert lines(capsys)[:2] == [f"merge-row-42591 42591 {HEAD[:7]} stale: GitHub's head is {NEW_HEAD[:7]}", f"landing-list#42593 42593 {HEAD[:7]} listed: the PR is merged on GitHub and still listed under landing-list"]
    [(_, pushed)] = shell.pushed
    assert pushed_rows(shell) == ["merge-row-42590"] and pushed["children"][1]["md"] == "- #42590: lands next\n"


def test_a_block_left_with_no_row_leaves_the_push_and_its_passing_siblings_still_push(tmp_path: Path, shell: FakeShell) -> None:
    shell.pull("42591", head=NEW_HEAD)
    board = groups(pr_row("42590"))
    board["children"].append({"id": "merge-triage", "type": "triage", "items": [row("42591", md="Keeps a copy of each release's Rust-built files.")]})

    assert gate(tmp_path, shell, board, "--", "cc-present", "update-block") == 1

    [(_, pushed)] = shell.pushed
    assert [child["id"] for child in pushed["children"]] == ["merge-prs"] and pushed_rows(shell) == ["42590"]


def test_a_failing_one_tap_group_holds_every_row_it_covers(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str]) -> None:
    shell.pull("42593")
    shell.graded("42593")
    shell.verdicts = {"merge-prs": {"42590": approved("42590"), "42593": rejected("42593", NEW_HEAD)}}

    assert gate(tmp_path, shell, claimed("Gateway fix: your one tap approves both.", pr_row("42590"), pr_row("42593")), "--", "cc-present", "update-block") == 1

    assert lines(capsys)[-1].startswith("rows 2 failing 2 ") and shell.pushed == []


def stacked(shell: FakeShell, grade: str = "fidelity: ok {head} a review fix", base: str = "b/42590", fix: bool = True) -> None:
    shell.pull("42591", base=base)
    for pr in ("42590", "42591"):
        shell.post("review", "fidelity-desk", grade.format(head=HEAD[:7]), pr)
        shell.post("review", "finder-desk", f"finder: ok {HEAD[:7]} no majors", pr)
        if fix:
            shell.post("ask", "desk-runner", kept(pr), pr)
        shell.post("note", "desk-runner", changed(pr), pr)
    shell.verdicts = {"merge-prs": {"42590": approved("42590", NEW_HEAD), "42591": approved("42591", NEW_HEAD, at="2026-10-30T01:59:00Z")}}


def stack_rows() -> dict:
    return groups(
        pr_row("42590", title="Saves turns", change="Each turn is saved.", since={"changed": "a nil check answers the finder", "because": "the finder's major is fixed"}),
        pr_row("42591", title="Reads turns", change="Each turn reads back.", since={"changed": "a rename the review asked for", "because": "the finder's major is fixed"}),
    )


def test_a_stack_of_re_asked_review_fixes_pushes_as_one_row_his_one_tap_decides(tmp_path: Path, shell: FakeShell) -> None:
    """Owner 7:00 PM Oct 29 (root #194387), on land-faster: one board row per stack when every change in it is a pure review fix or patch-identical replay."""
    stacked(shell)

    assert gate(tmp_path, shell, stack_rows(), "--", "cc-present", "update-block") == 0

    [(_, pushed)] = shell.pushed
    [(_, _, row)] = list(board_rows.pr_group_rows(pushed))
    assert row["number"] == 42591 and row["head"] == HEAD[:7]
    assert row["stack"] == [{"number": 42590, "head": HEAD[:7]}]
    assert row["title"] == "Reads turns (stack of 2)"
    assert row["change"] == "You approved these 2 changes between 1:00 PM Oct 26 and 6:59 PM Oct 29. Since then: #42590 a nil check answers the finder; #42591 a rename the review asked for. Shown again because: the finder's major is fixed. #42590: Each turn is saved; #42591: Each turn reads back."
    assert row["risk"] == "risk in plain words"
    assert "since" not in row and "textAt" not in row


@pytest.mark.parametrize(
    ("grade", "base", "fix"),
    [
        ("fidelity: ok {head} adds a retry", "b/42590", False),
        ("fidelity: ok {head} a review fix", "dev", True),
    ],
    ids=["new-behavior", "not-stacked"],
)
def test_a_stack_with_new_behavior_or_no_chain_keeps_a_row_per_pr(tmp_path: Path, shell: FakeShell, grade: str, base: str, fix: bool) -> None:
    stacked(shell, grade, base, fix)

    gate(tmp_path, shell, stack_rows(), "--", "cc-present", "update-block")

    pushed = [row for _, pushed in shell.pushed for *_, row in board_rows.pr_group_rows(pushed)]
    assert all("stack" not in row for row in pushed)


def test_a_row_whose_pr_the_drives_ledger_never_admitted_is_left_off_the_board(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str]) -> None:
    shell.pull("45401")
    shell.graded("45401")
    shell.ledger = {"42590"}

    assert gate(tmp_path, shell, groups(pr_row("42590"), pr_row("45401")), "--", "cc-present", "update-block") == 1

    assert lines(capsys)[0] == f"45401 45401 {HEAD[:7]} unledgered: the drive's ledger never admitted #45401, so no tap on it can land; its lane runs `ledger.py register --pr 45401`"
    assert pushed_rows(shell) == ["42590"]


def test_a_ledger_that_does_not_read_warns_and_fails_no_row(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str]) -> None:
    shell.ledger_fails = True

    assert gate(tmp_path, shell, groups(pr_row("42590")), "--", "cc-present", "update-block") == 0

    assert "WARN the drive's ledger did not read (ledger.py list exited 1: ccn: no ledger 1a2b); no row is checked for a PR the ledger never admitted" in lines(capsys)
    assert pushed_rows(shell) == ["42590"]
