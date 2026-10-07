from __future__ import annotations

import io
import json
from pathlib import Path

import ledger
import pytest
import rulings

REPO = "/repo"
REGISTER_ID = "0cf17c9" + "0" * 33
REGISTER_BODY = "# Register\n\n1. Pulumi state is the only truth (ec2881e).\n"
FLAGS = {"id": "1984bf6" + "0" * 33, "title": "May a migration ship behind a flag?", "body": "No.\nDelete or replace."}
TRUTH = {"id": "ec2881e" + "0" * 33, "title": "Is any ledger a source of truth?", "body": "No: Pulumi state is."}
SEED = {"id": "8075b46" + "0" * 33, "title": "How does a seed converge?", "body": "On every apply.\n" + "x" * 400}


DURABLE = ["answer", "list", "--label", "scope:durable", "--limit", "0"]


class FakeShell(ledger.Shell):
    def __init__(self, hits: list[str], registers: list[dict], branch: str = "yasyf/brook") -> None:
        self.hits = hits
        self.registers = registers
        self.branch = branch
        self.queries: list[tuple[str, str]] = []

    def run(self, argv: list[str], stdin: str | None = None) -> str:
        if argv[0] == "git":
            assert argv[1:] == ["-C", REPO, "rev-parse", "--abbrev-ref", "HEAD"]
            return f"{self.branch}\n"
        if argv[0] == "ccx":
            assert argv[1:7] == ["code", "search", "--semantic", "--content", "docs", "-k"]
            self.queries.append((argv[-2], argv[-1]))
            return "# semantic (native)\n" + "".join(f"{hit}.md:1-3#abcd (score=0.016)\n# title\n" for hit in self.hits)
        assert argv[:3] == ["ccn", "-R", REPO], argv
        match argv[3:5]:
            case ["answer", "list"]:
                match argv[3:-1]:
                    case [*durable, "--label", "program:brook"] if durable == DURABLE:
                        return json.dumps([FLAGS, TRUTH])
                    case [*durable, "--branch", "yasyf/brook"] if durable == DURABLE:
                        return json.dumps([TRUTH, SEED])
            case ["doc", "list"]:
                assert argv[5:7] == ["--label", "standing-rules:brook"]
                return json.dumps(self.registers)
            case ["doc", "show"]:
                return json.dumps({"id": argv[5], "body": REGISTER_BODY})
        raise AssertionError(argv)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def match(shell: FakeShell, brief: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *extra: str) -> str:
    monkeypatch.setattr("sys.stdin", io.StringIO(brief))
    assert rulings.main(["match", "--program", "brook", "--repo", REPO, *extra], shell) == 0
    return capsys.readouterr().out


def test_register_prints_the_newest_register_doc_or_null(home: Path, capsys: pytest.CaptureFixture[str]) -> None:
    older = {"id": "1" * 40, "updated_at": "2026-10-04T00:00:00Z"}
    newest = {"id": REGISTER_ID, "updated_at": "2026-10-05T00:00:00Z"}

    assert rulings.main(["register", "--program", "brook", "--repo", REPO], FakeShell([], [newest, older])) == 0
    assert json.loads(capsys.readouterr().out) == {"id": REGISTER_ID, "body": REGISTER_BODY}

    assert rulings.main(["register", "--program", "brook", "--repo", REPO], FakeShell([], [])) == 0
    assert json.loads(capsys.readouterr().out) is None


def test_match_mirrors_the_corpus_and_quotes_uncited_hits_in_rank_order(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    corpus = home / ".claude" / "scratch" / "brook" / "rulings"
    corpus.mkdir(parents=True)
    (corpus / "dead000.md").write_text("# superseded\n")
    shell = FakeShell(["1984bf6", "ec2881e", "1984bf6", "8075b46"], [{"id": REGISTER_ID, "updated_at": "2026-10-05T00:00:00Z"}])

    out = match(shell, "Lane brief: add a --skip-tenant flag.", monkeypatch, capsys)

    assert out.startswith("- 1984bf6 May a migration ship behind a flag?\n  > No.\n  > Delete or replace.\n- 8075b46 How does a seed converge?\n")
    assert "ec2881e" not in out
    assert sorted(path.name for path in corpus.iterdir()) == ["1984bf6.md", "8075b46.md", "ec2881e.md"]
    assert (corpus / "1984bf6.md").read_text() == "# May a migration ship behind a flag?\n\nNo.\nDelete or replace.\n"
    assert shell.queries == [("Lane brief: add a --skip-tenant flag.", str(corpus))]


def test_match_stops_at_the_budget(home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    out = match(FakeShell(["1984bf6", "8075b46"], []), "brief", monkeypatch, capsys, "--budget", "200")

    assert out == "- 1984bf6 May a migration ship behind a flag?\n  > No.\n  > Delete or replace.\n"


def test_a_drive_worker_resolves_its_program_from_the_registry(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    drives = home / ".claude" / "long-running" / "drives"
    drives.mkdir(parents=True)
    state = home / "state" / "brook"
    (drives / "d1.json").write_text(json.dumps({"drive": "d1", "state_dir": str(state)}))
    shell = FakeShell(["1984bf6"], [])

    monkeypatch.setattr("sys.stdin", io.StringIO("brief"))
    assert rulings.main(["match", "--drive", "d1", "--repo", REPO], shell) == 0

    assert capsys.readouterr().out.startswith("- 1984bf6 ")
    assert shell.queries == [("brief", str(state / "rulings"))]


def test_match_reads_only_the_drives_program_and_branch_answers(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    corpus = home / ".claude" / "scratch" / "brook" / "rulings"

    match(FakeShell(["1984bf6"], []), "brief", monkeypatch, capsys)
    assert sorted(path.name for path in corpus.iterdir()) == ["1984bf6.md", "8075b46.md", "ec2881e.md"]

    out = match(FakeShell(["8075b46", "1984bf6"], [], branch="HEAD"), "brief", monkeypatch, capsys)
    assert out == "- 1984bf6 May a migration ship behind a flag?\n  > No.\n  > Delete or replace.\n"
    assert sorted(path.name for path in corpus.iterdir()) == ["1984bf6.md", "ec2881e.md"]
