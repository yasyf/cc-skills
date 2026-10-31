from __future__ import annotations

import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import rulings
from test_rulings import FLAGS, REPO, FakeShell


class CoordinatedShell(FakeShell):
    def __init__(self, body: str, barrier: threading.Barrier) -> None:
        super().__init__([], [{"id": body, "body": body, "updated_at": "1"}])
        self.barrier = barrier

    def run(self, argv: list[str], stdin: str | None = None) -> str:
        if argv[3:5] == ["doc", "list"]:
            self.barrier.wait(timeout=5)
        return super().run(argv, stdin)


def test_concurrent_dispatches_use_their_explicit_streams(capsys: pytest.CaptureFixture[str]) -> None:
    barrier = threading.Barrier(2)
    outputs = [io.StringIO(), io.StringIO()]

    def call(index: int) -> int:
        return rulings.main(
            ["register", "--program", "brook", "--repo", REPO],
            CoordinatedShell(str(index), barrier),
            stdin=io.StringIO("unused"),
            stdout=outputs[index],
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(call, [0, 1])) == [0, 0]
    assert [json.loads(output.getvalue()) for output in outputs] == [
        {"id": "0", "body": "0"}, {"id": "1", "body": "1"}
    ]
    assert capsys.readouterr().out == ""


def test_match_consumes_its_input_before_query_clipping(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    brief = "explicit request input " * 200
    source, output = io.StringIO(brief), io.StringIO()
    shell = FakeShell([FLAGS["id"][:7]], [])
    assert rulings.main(["match", "--program", "brook", "--repo", REPO], shell, stdin=source, stdout=output) == 0
    assert source.tell() == len(brief)
    assert shell.queries[0][0] == brief[: rulings.QUERY_CHARS]
    assert "May a migration ship behind a flag?" in output.getvalue()
    assert capsys.readouterr().out == ""
