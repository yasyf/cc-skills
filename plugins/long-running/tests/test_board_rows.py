"""board_rows.py over a fake GitHub and drive bus: a merge row passes only at its PR's current head, graded there, in words written after it, while the PR is open and unruled."""

from __future__ import annotations

import json
from pathlib import Path

import board_rows
import pytest

DRIVE = "d1"
REPO = "Forge-AI/monorepo"
HEAD = "35aa930c1d2e3f405162738495a6b7c8d9e0f1a2"
NEW_HEAD = "7b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c"
COMMITTED = "2026-10-26T19:00:00Z"
WRITTEN = "2026-10-26T20:00:00Z"
BEFORE = "2026-10-26T18:00:00Z"


class FakeShell(board_rows.Shell):
    def __init__(self) -> None:
        self.pulls: dict[str, dict | None] = {}
        self.records: list[dict] = []
        self.blobs: dict[str, str] = {}
        self.gh_fails = False
        self.pushed: list[tuple[list[str], dict]] = []

    def run(self, argv: list[str]) -> board_rows.Done:
        if argv[:3] == ["gh", "api", "graphql"]:
            if self.gh_fails:
                return board_rows.Done(1, "", "HTTP 502: Bad Gateway")
            query = argv[-1]
            found = {f"p{pr}": pull for pr, pull in self.pulls.items() if f"pullRequest(number: {pr})" in query}
            return board_rows.Done(0, json.dumps({"data": {"repository": found}}), "")
        assert argv[:2] == ["cci", "tail"], argv
        return board_rows.Done(0, "".join(json.dumps(record) + "\n" for record in self.tailed(argv)), "")

    def tailed(self, argv: list[str]) -> list[dict]:
        since = int(argv[argv.index("--since") + 1])
        kinds = [arg.split("=", 1)[1] for arg in argv if arg.startswith("--kind=")]
        lanes = [argv[at + 1] for at, arg in enumerate(argv) if arg == "--lane"]
        pr = argv[argv.index("--pr") + 1] if "--pr" in argv else None
        return [
            record
            for record in self.records
            if record["seq"] > since and (not kinds or record["kind"] in kinds) and (not lanes or record["lane"] in lanes) and (pr is None or pr in record["refs"].get("prs", []))
        ]

    def push(self, argv: list[str]) -> int:
        self.pushed.append((argv, json.loads(Path(argv[-1]).read_text())))
        return 0

    def read(self, path: str) -> str | None:
        return self.blobs.get(path)

    def pull(self, pr: str, head: str = HEAD, state: str = "OPEN", committed: str = COMMITTED) -> None:
        self.pulls[pr] = {"state": state, "headRefOid": head, "commits": {"nodes": [{"commit": {"committedDate": committed}}]}}

    def post(self, kind: str, lane: str, text: str, pr: str | None = None, path: str | None = None) -> int:
        seq = len(self.records) + 1
        refs = {"prs": [f"{REPO}#{pr}"]} if pr else {}
        self.records.append({"seq": seq, "drive": DRIVE, "kind": kind, "lane": lane, "text": text, "refs": refs | ({"path": path} if path else {})})
        return seq

    def graded(self, pr: str, head: str = HEAD) -> None:
        self.post("review", "fidelity-desk", f"fidelity: ok {head[:7]} checked rule/1", pr)
        self.post("review", "p7", f"finder: ok {head[:7]} no majors", pr)


def row(pr: str, head: str = HEAD, text_at: str | None = WRITTEN, md: str = "Saves the old converter's on-disk chat layouts as samples.") -> dict:
    item = {"id": f"merge-row-{pr}", "label": f"#{pr}: a change", "hint": f"commit {head[:7]}, built on dev", "md": md, "facts": []}
    return item | ({"textAt": text_at} if text_at else {})


def card(*items: dict) -> dict:
    return {"id": "card-merge-triage", "type": "card", "children": [{"id": "merge-triage", "type": "triage", "items": list(items)}]}


def gate(tmp_path: Path, shell: FakeShell, block: dict, *argv: str) -> int:
    config = tmp_path / "runner.json"
    config.write_text(json.dumps({"drive": DRIVE, "landing": {"repo": REPO, "fidelity_lane": ["fidelity-desk", "fidelity-desk-2"]}}))
    path = tmp_path / "card-merge-triage.json"
    path.write_text(json.dumps(block))
    return board_rows.main(["--config", str(config), str(path), *argv], shell)


@pytest.fixture
def shell() -> FakeShell:
    fake = FakeShell()
    fake.pull("42590")
    fake.graded("42590")
    return fake


def test_a_fresh_graded_open_row_pushes_a_copy_without_its_text_time(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str]) -> None:
    assert gate(tmp_path, shell, card(row("42590")), "--", "cc-present", "update-block", "--session", "s1") == 0

    [(argv, pushed)] = shell.pushed
    assert argv[:4] == ["cc-present", "update-block", "--session", "s1"]
    assert pushed == card(row("42590", text_at=None))
    assert capsys.readouterr().out == ""


def test_a_row_behind_its_prs_head_fails_naming_the_new_head_and_pushes_nothing(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str]) -> None:
    shell.pull("42590", head=NEW_HEAD)

    assert gate(tmp_path, shell, card(row("42590")), "--", "cc-present", "update-block") == 1

    assert capsys.readouterr().out == f"merge-row-42590 42590 {HEAD[:7]} stale: GitHub's head is {NEW_HEAD[:7]}\n"
    assert shell.pushed == []


def test_a_row_with_no_grade_or_no_finder_at_its_head_fails(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str]) -> None:
    shell.pull("42591")
    shell.graded("42591", head=NEW_HEAD)
    shell.pull("42592")
    shell.post("review", "fidelity-desk-2", f"fidelity: missing {HEAD[:7]} rule/2 one added line", "42592")

    assert gate(tmp_path, shell, card(row("42590"), row("42591", md="Keeps a copy of each release's Rust-built files."), row("42592", md="Ports the journal reader tests to the new tree.")), "--check") == 1

    out = capsys.readouterr().out.splitlines()
    assert out == [
        f"merge-row-42591 42591 {HEAD[:7]} ungraded: no fidelity grade from fidelity-desk or fidelity-desk-2 names {HEAD[:7]}; ungraded: no finder review names {HEAD[:7]}",
        f"merge-row-42592 42592 {HEAD[:7]} ungraded: no finder review names {HEAD[:7]}",
    ]


@pytest.mark.parametrize(("text_at", "reason"), [(BEFORE, f"old text: written {BEFORE}, not after {HEAD[:7]} was committed at {COMMITTED}"), (None, "old text: no textAt on the row")])
def test_words_not_written_after_the_head_was_committed_fail(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str], text_at: str | None, reason: str) -> None:
    assert gate(tmp_path, shell, card(row("42590", text_at=text_at)), "--check") == 1

    assert capsys.readouterr().out == f"merge-row-42590 42590 {HEAD[:7]} {reason}\n"


@pytest.mark.parametrize("state", ["CLOSED", "MERGED"])
def test_a_pr_github_no_longer_holds_open_fails(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str], state: str) -> None:
    shell.pull("42590", state=state)

    assert gate(tmp_path, shell, card(row("42590")), "--check") == 1

    assert capsys.readouterr().out == f"merge-row-42590 42590 {HEAD[:7]} not open: the PR is {state.lower()} on GitHub\n"


def test_a_root_ruling_that_supersedes_the_pr_fails_it_with_that_seq_until_a_later_go_names_its_head(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str]) -> None:
    seq = shell.post("go", "root", "withdrawn: go for #42590 35aa930 (#131549). My own #130667 ruled it superseded…", path="/blobs/a.txt")
    shell.blobs["/blobs/a.txt"] = "withdrawn: go for #42590 35aa930 (#131549). My own #130667 ruled it superseded by the repeat list's deletion. p8b-step10 closes #42590 now."

    assert gate(tmp_path, shell, card(row("42590")), "--check") == 1
    assert capsys.readouterr().out == f"merge-row-42590 42590 {HEAD[:7]} not open: root ruled it superseded or closed on cci #{seq}\n"

    shell.post("go", "root", f"go #42590 at {HEAD[:7]} (owner tap 2:10 PM)", "42590")
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
    ],
)
def test_a_closing_ruling_must_tie_the_word_to_the_pr(text: str, closed: bool) -> None:
    assert board_rows.closes("42590", text) is closed


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


def test_a_github_outage_warns_and_fails_no_row_on_what_it_could_not_read(tmp_path: Path, shell: FakeShell, capsys: pytest.CaptureFixture[str]) -> None:
    shell.gh_fails = True

    assert gate(tmp_path, shell, card(row("42590", text_at=None)), "--check") == 0

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("WARN GitHub refused the PR read for #42590: HTTP 502: Bad Gateway")
