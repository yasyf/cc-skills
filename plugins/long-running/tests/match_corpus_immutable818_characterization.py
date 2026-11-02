from __future__ import annotations

import io
import json
from pathlib import Path

from long_running import rulings
import pytest

from test_rulings import DURABLE, REPO, FakeShell


class CorpusShell(FakeShell):
    def __init__(self, program, branch, hits=(), registers=()):
        super().__init__(list(hits), list(registers), program=program)
        self.branch_answers = branch
        self.answer_rows = 0
        self.answer_bytes = 0

    def run(self, argv, stdin=None):
        if argv[:5] == ["ccn", "-R", REPO, "answer", "list"]:
            assert argv[-1] == "--json"
            if argv[3:-1] == [*DURABLE, "--label", "program:brook"]:
                rows = self.program
            else:
                assert argv[3:-1] == [*DURABLE, "--branch", "yasyf/brook"]
                rows = self.branch_answers
            payload = json.dumps(rows)
            self.answer_rows += len(rows)
            self.answer_bytes += len(payload.encode())
            return payload
        return super().run(argv, stdin)


def answer(number, body="Owner words λ."):
    return {"id": f"{number:07x}" + "0" * 33, "title": f"Ruling {number}", "body": body}


def invoke(shell, monkeypatch, *extra):
    output = io.StringIO()
    assert rulings.main(["match", "--program", "brook", "--repo", REPO, *extra], shell, stdin=io.StringIO("lane brief"), stdout=output) == 0
    return output.getvalue()


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return tmp_path / ".claude" / "scratch" / "brook" / "rulings"


@pytest.mark.parametrize("count", [32, 10000])
def test_immutable_match_reloads_and_rereads_unchanged_corpus(corpus, monkeypatch, count):
    rows = [answer(number) for number in range(count)]
    shell = CorpusShell(rows, [], [rows[0]["id"][:7]])
    reads = []
    writes = []
    original_read = Path.read_text
    original_write = rulings.write_atomic

    def read(path, *args, **kwargs):
        text = original_read(path, *args, **kwargs)
        if path.parent == corpus and path.suffix == ".md":
            reads.append(len(text.encode()))
        return text

    def write(path, text):
        if path.parent == corpus:
            writes.append(len(text.encode()))
        return original_write(path, text)

    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(rulings, "write_atomic", write)
    expected = rulings.rendered(rows[0])
    assert invoke(shell, monkeypatch) == expected
    assert len(writes) == count and reads == [] and shell.answer_rows == count
    writes.clear()
    shell.answer_rows = shell.answer_bytes = 0
    assert invoke(shell, monkeypatch) == expected
    assert len(reads) == count and writes == [] and shell.answer_rows == count
    assert sum(reads) == sum(len(f"# {row['title']}\n\n{row['body']}\n".encode()) for row in rows)
    assert len(shell.queries) == 2
    print("MATCH_CORPUS_IMMUTABLE " + json.dumps({"answers": count, "warm_answer_rows": shell.answer_rows, "warm_answer_bytes": shell.answer_bytes, "warm_mirror_reads": len(reads), "warm_mirror_bytes": sum(reads), "warm_mirror_writes": len(writes)}, sort_keys=True))


@pytest.mark.parametrize("change", ["body", "title", "delete", "branch", "duplicate", "repair", "extra", "unicode"])
def test_match_observes_current_answer_and_mirror_changes(corpus, monkeypatch, change):
    first, second = answer(1), answer(2)
    shell = CorpusShell([first], [second], [first["id"][:7], second["id"][:7]])
    assert invoke(shell, monkeypatch) == rulings.rendered(first) + rulings.rendered(second)
    selected = [first, second]
    if change == "body":
        first["body"] = "Fresh external words."
    elif change == "title":
        first["title"] = "Fresh external title"
    elif change == "delete":
        shell.program = []
        selected = [second]
    elif change == "branch":
        shell.branch = "HEAD"
        selected = [first]
    elif change == "duplicate":
        replacement = first | {"body": "Branch payload wins."}
        shell.branch_answers = [replacement, second]
        selected = [replacement, second]
    elif change == "repair":
        (corpus / "0000001.md").write_text("External file mutation")
        (corpus / "0000002.md").unlink()
    elif change == "extra":
        (corpus / "dead000.md").write_text("External stale file")
    else:
        first["body"] = "漢字 λ 🐳\nSecond line."
    assert invoke(shell, monkeypatch) == "".join(rulings.rendered(row) for row in selected)
    assert sorted(path.name for path in corpus.glob("*.md")) == sorted(row["id"][:7] + ".md" for row in selected)
    for row in selected:
        assert (corpus / (row["id"][:7] + ".md")).read_text() == f"# {row['title']}\n\n{row['body']}\n"


@pytest.mark.parametrize("mode", ["cited", "excluded", "duplicate_hits", "budget", "empty", "false_body", "query_cut"])
def test_match_keeps_ranking_filter_and_render_frontiers(corpus, monkeypatch, mode):
    first, second = answer(1), answer(2)
    shell = CorpusShell([first, second], [], [first["id"][:7], second["id"][:7]])
    extra = []
    expected = rulings.rendered(first) + rulings.rendered(second)
    if mode == "cited":
        shell.registers = [{"id": "f" * 40, "updated_at": "2026-10-01", "body": "Owner words (0000001)."}]
        expected = rulings.rendered(second)
    elif mode == "excluded":
        extra = ["--exclude", "0000001"]
        expected = rulings.rendered(second)
    elif mode == "duplicate_hits":
        shell.hits = ["0000002", "0000002", "0000001"]
        expected = rulings.rendered(second) + rulings.rendered(first)
    elif mode == "budget":
        extra = ["--budget", str(len(rulings.rendered(first).encode()) - 1)]
        expected = ""
    elif mode == "empty":
        shell.program = []
        expected = ""
    elif mode == "false_body":
        first["body"] = None
        expected = rulings.rendered(first) + rulings.rendered(second)
    else:
        output = io.StringIO()
        assert rulings.main(["match", "--program", "brook", "--repo", REPO], shell, stdin=io.StringIO("λ" * 3000), stdout=output) == 0
        assert output.getvalue() == expected
        assert shell.queries[0][0] == "λ" * rulings.QUERY_CHARS
        return
    assert invoke(shell, monkeypatch, *extra) == expected
    assert bool(shell.queries) == (mode != "empty")
    if shell.queries:
        assert shell.queries[0] == ("lane brief", str(corpus))


@pytest.mark.parametrize("failure", ["answer", "mirror"])
def test_match_errors_precede_semantic_search(corpus, monkeypatch, failure):
    row = answer(1)
    shell = CorpusShell([row], [], [row["id"][:7]])
    expected = RuntimeError("selected operation failed")
    if failure == "answer":
        original = shell.run

        def run(argv, stdin=None):
            if argv[:5] == ["ccn", "-R", REPO, "answer", "list"]:
                raise expected
            return original(argv, stdin)

        monkeypatch.setattr(shell, "run", run)
    else:
        def write(path, text):
            raise expected

        monkeypatch.setattr(rulings, "write_atomic", write)
    with pytest.raises(RuntimeError) as caught:
        invoke(shell, monkeypatch)
    assert caught.value is expected and shell.queries == []
