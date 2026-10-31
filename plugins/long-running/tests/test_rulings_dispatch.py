from __future__ import annotations

import io
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from functools import partial
from pathlib import Path

import pytest
from long_running import drive, rulings
from test_rulings import REPO, RegisterShell
from test_rulings_io import CoordinatedShell


def runtime(root: Path, *, selected: str | None = "same", lock=None, checkpoint=lambda: None) -> rulings.Runtime:
    @contextmanager
    def unlocked(path: Path):
        yield

    drives = root / "config" / "long-running" / "drives"
    return rulings.Runtime(
        io.StringIO(""), io.StringIO(), io.StringIO(), drives, root / "scratch",
        partial(drive.current_drive, {drive.DRIVE_ENV: selected} if selected else {}, drives),
        lock or unlocked, lambda path: False, checkpoint,
    )


def test_dispatches_keep_their_default_drives_and_streams_request_local(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    barrier = threading.Barrier(2)
    requests = [runtime(tmp_path / str(index)) for index in range(2)]
    for index, request in enumerate(requests):
        request.drives.mkdir(parents=True)
        (request.drives / "same.json").write_text(json.dumps({"drive": "same", "program": "brook", "state_dir": str(tmp_path / str(index) / "state"), "checkout": REPO}))

    def call(index: int) -> int:
        return rulings.dispatch(["register", "--repo", REPO], CoordinatedShell(str(index), barrier), requests[index])

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(call, [0, 1])) == [0, 0]
    assert [json.loads(request.stdout.getvalue()) for request in requests] == [
        {"id": "0", "body": "0"}, {"id": "1", "body": "1"},
    ]
    assert [request.stderr.getvalue() for request in requests] == ["", ""]
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("argv", [["--help"], ["match", "--help"], ["missing-verb"], ["rows", "--bad"]])
def test_dispatch_parser_keeps_cli_bytes_and_exit_status_request_local(tmp_path: Path, capsys: pytest.CaptureFixture[str], argv: list[str]) -> None:
    with pytest.raises(SystemExit) as original:
        rulings.build_parser().parse_args(argv)
    expected = capsys.readouterr()
    request = runtime(tmp_path)
    with pytest.raises(SystemExit) as direct:
        rulings.dispatch(argv, RegisterShell({}), request)
    assert direct.value.code == original.value.code
    assert (request.stdout.getvalue(), request.stderr.getvalue()) == expected
    assert capsys.readouterr() == ("", "")


def test_dispatch_uses_the_owned_lock_and_state_root(tmp_path: Path) -> None:
    locked = []
    checkpoints = []

    @contextmanager
    def lock(path: Path):
        locked.append(path)
        yield

    request = runtime(tmp_path, lock=lock, checkpoint=lambda: checkpoints.append(True))
    assert rulings.dispatch(["rows", "--program", "brook", "--repo", REPO], RegisterShell({}), request) == 0
    assert locked == [request.scratch / "brook" / "register-brook.lock"]
    shown = json.loads(request.stdout.getvalue())
    assert shown["state_dir"] == str(request.scratch / "brook")
    assert checkpoints == [True, True, True]
    assert request.stderr.getvalue() == ""


def test_authorization_transcripts_use_the_explicit_request_projects_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    expected = {"type": "user", "message": {"content": "selected request"}}
    requested = tmp_path / "request" / "projects" / "repo"
    requested.mkdir(parents=True)
    (requested / "same-session.jsonl").write_text(json.dumps(expected) + "\n")
    unrelated = tmp_path / "other" / "projects" / "repo"
    unrelated.mkdir(parents=True)
    (unrelated / "same-session.jsonl").write_text(json.dumps({"message": "unrelated request"}) + "\n")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "other"))
    assert rulings.transcript_entries({"sessions": ["same-session"]}, requested.parent) == [expected]
