"""board_rows.py over a fake GitHub, drive bus, and board: a merge row, a triage item or a pr-groups row, passes only at its PR's current head, with every field the owner decides by, in words written after that head, while the PR is open and unruled and its recorded verdict stays where the board keys it."""

from __future__ import annotations

import json
from pathlib import Path

import board_rows
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
        since = int(argv[argv.index("--since") + 1])
        kinds = [arg.split("=", 1)[1] for arg in argv if arg.startswith("--kind=")]
        lanes = [argv[at + 1] for at, arg in enumerate(argv) if arg == "--lane"]
        pr = argv[argv.index("--pr") + 1] if "--pr" in argv else None
        topics = [arg.split("=", 1)[1] for arg in argv if arg.startswith("--topic=")]
        return [
            record
            for record in self.records
            if record["seq"] > since
            and (not kinds or record["kind"] in kinds)
            and (not lanes or record["lane"] in lanes)
            and (pr is None or pr in record["refs"].get("prs", []))
            and (not topics or record.get("topic") in topics)
        ]

    def push(self, argv: list[str]) -> int:
        self.pushed.append((argv, json.loads(Path(argv[-1]).read_text())))
        return 0

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


def test_a_root_ruling_that_supersedes_the_pr_fails_it_with_that_seq_until_a_later_go_names_its_head(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str]) -> None:
    seq = shell.post("go", "root", "withdrawn: go for #42590 35aa930 (#131549). My own #130667 ruled it superseded…", path="/blobs/a.txt")
    shell.blobs["/blobs/a.txt"] = "withdrawn: go for #42590 35aa930 (#131549). My own #130667 ruled it superseded by the repeat list's deletion. p8b-step10 closes #42590 now."

    assert gate(tmp_path, shell, card(row("42590")), "--check") == 1
    assert lines(capsys)[0] == f"merge-row-42590 42590 {HEAD[:7]} not open: root ruled it superseded or closed on cci #{seq}"

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
        ("go", "root", f"go-carry #42590 5321d37->{HEAD[:7]}: its commits implement his pick only.", None),
        ("head", "p8f", f"dar-p8f-replica-plan {HEAD[:7]} on signal-1 509a21c (#133292). Patch-identical.", "42590"),
    ],
)
def test_a_carry_record_tying_the_head_to_the_pr_counts_as_its_grade_and_finder(tmp_path: Path, capsys: pytest.CaptureFixture[str], kind: str, lane: str, text: str, pr: str | None) -> None:
    fake = FakeShell()
    fake.pull("42590")
    fake.post(kind, lane, text, pr)
    fake.verdicts = {"merge-triage": {"merge-row-42590": {"verdict": "approved"}}}

    assert gate(tmp_path, fake, card(row("42590")), "--check") == 0
    assert lines(capsys) == ["rows 1 failing 0 ungraded 0 flags 0"]


@pytest.mark.parametrize(
    "text",
    [
        f"Desktop chain restacked, each patch-identical: #42721 4ddf3c8, #42829 b4d1bc1 (= {HEAD[:7]}, tapped 5321d37).",
        f"#42590 {HEAD[:7]} restacked onto dev; finder pending.",
        f"FIXED: 42590 5321d37/{HEAD[:7]} waits (log calls it patch-identical).",
    ],
)
def test_a_record_that_does_not_carry_the_head_for_the_pr_leaves_it_ungraded(tmp_path: Path, capsys: pytest.CaptureFixture[str], text: str) -> None:
    fake = FakeShell()
    fake.pull("42590")
    fake.post("head", "p8e", text)

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


@pytest.mark.parametrize("kind", ["decide", "answer"])
def test_a_row_at_a_head_he_did_not_approve_waits_until_root_rules_it_new_content(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str], kind: str) -> None:
    """board-refresher-2 (root #213579): root ruled re-asks in answer records, which the gate never read."""
    shell.verdicts = {"merge-prs": {"42590": approved("42590", NEW_HEAD)}}

    assert gate(tmp_path, shell, groups(pr_row("42590") | {"since": SINCE}), "--check") == 1
    assert lines(capsys)[0] == f"42590 42590 {HEAD[:7]} re-ask: he approved it at {NEW_HEAD[:7]}, and no root record rules {HEAD[:7]} replay or new content; it waits in Being fixed or checked"

    shell.post(kind, "root", f"#42590 {HEAD[:7]} adds a retry he has not seen: the row re-asks his tap", "42590")
    assert gate(tmp_path, shell, groups(pr_row("42590") | {"since": SINCE}), "--check") == 0


@pytest.mark.parametrize("since", [None, {"changed": "the upload now retries twice"}, {"changed": " ", "because": "a failed upload retries"}])
def test_a_re_asked_row_without_what_changed_since_his_tap_fails(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str], since: dict | None) -> None:
    """Owner 6:59 PM Oct 29 (root #194315): 'if ur asking for a PR tap that Ive alrady apporiove dyou ned to tell me whats changed / why ur showing it gain'."""
    shell.verdicts = {"merge-prs": {"42590": approved("42590", NEW_HEAD)}}
    shell.post("decide", "root", f"#42590 {HEAD[:7]} adds a retry he has not seen: the row re-asks his tap", "42590")

    assert gate(tmp_path, shell, groups(pr_row("42590") | ({"since": since} if since else {})), "--", "cc-present", "update-block") == 1

    assert lines(capsys)[0] == f"42590 42590 {HEAD[:7]} since: he approved it at {NEW_HEAD[:7]}, so the row opens 'You approved this at 1:00 PM Oct 26. Since then: <what changed, in plain words>. Shown again because: <the behavior change>.'; give its `since` both `changed` and `because`"
    assert shell.pushed == []


def test_a_re_asked_row_opens_with_his_tap_time_what_changed_and_why_it_is_shown_again(tmp_path: Path, shell: FakeShell) -> None:
    shell.verdicts = {"merge-prs": {"42590": approved("42590", NEW_HEAD, at="2026-10-30T01:59:00Z")}}
    shell.post("decide", "root", f"#42590 {HEAD[:7]} adds a retry he has not seen: the row re-asks his tap", "42590")

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


REASK = "the row re-asks his tap"


def stacked(shell: FakeShell, grade: str = "fidelity: ok {head} a review fix, no new behavior", base: str = "b/42590") -> None:
    shell.pull("42591", base=base)
    for pr in ("42590", "42591"):
        shell.post("review", "fidelity-desk", grade.format(head=HEAD[:7]), pr)
        shell.post("review", "finder-desk", f"finder: ok {HEAD[:7]} no majors", pr)
        shell.post("decide", "root", f"#{pr} {HEAD[:7]} answers the finder: {REASK}", pr)
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
    ("grade", "base"),
    [
        ("fidelity: ok {head} adds new behavior: a retry", "b/42590"),
        ("fidelity: missing {head} rule/2", "b/42590"),
        ("fidelity: ok {head} a review fix, no new behavior", "dev"),
    ],
    ids=["new-behavior", "no-ok-grade", "not-stacked"],
)
def test_a_stack_with_new_behavior_an_ungraded_fix_or_no_chain_keeps_a_row_per_pr(tmp_path: Path, shell: FakeShell, grade: str, base: str) -> None:
    stacked(shell, grade, base)

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


def test_a_multi_head_record_carries_only_the_prs_whose_own_note_says_patch_identical():
    record = {
        "kind": "head",
        "refs": {"prs": ["Forge-AI/monorepo#45001", "Forge-AI/monorepo#45002"]},
        "fields": {"heads": [
            {"pr": "Forge-AI/monorepo#45001", "commit": "a1b2c3d4e5", "was": "0f0f0f0f0f", "note": "restack, range-diff patch-identical (2 commits)"},
            {"pr": "Forge-AI/monorepo#45002", "commit": "c3b44a3fee", "was": "4423264aaa", "note": "replay, range-diff 1 identical, 1 changed"},
        ]},
        "text": "HEAD 2 PRs: #45001 a1b2c3d, #45002 c3b44a3: restacked onto dev",
    }
    assert board_rows.carries(record, "Forge-AI/monorepo", "45001", "a1b2c3d4e5")
    assert not board_rows.carries(record, "Forge-AI/monorepo", "45002", "c3b44a3fee")
    assert not board_rows.carries(record, "Forge-AI/monorepo", "45001", "c3b44a3fee")
