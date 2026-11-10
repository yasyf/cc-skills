from collections.abc import Callable
from pathlib import Path

import pytest
from drive import cli, orca, page, view, watch

SESSION = "session-under-test"
LIVE_WORKER = {
    "projection": {"liveness": {"verdict": "live"}, "outcome": "in_progress", "attention": {"categories": []}},
    "terminal": {"lastOutputAt": None},
}


@pytest.fixture
def state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "state"
    monkeypatch.setenv("LONG_RUNNING_STATE", str(root))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    monkeypatch.delenv("LONG_RUNNING_DRIVE", raising=False)
    monkeypatch.delenv("ORCA_TERMINAL_HANDLE", raising=False)
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.chdir(tmp_path)
    return root


@pytest.fixture
def run(state_root: Path, capsys: pytest.CaptureFixture[str]) -> Callable[..., tuple[int, str]]:
    def invoke(*argv: str) -> tuple[int, str]:
        code = cli.main(list(argv))
        return code, capsys.readouterr().out.strip()

    return invoke


@pytest.fixture
def demo(run: Callable[..., tuple[int, str]]) -> str:
    run("start", "demo", "--role", "coordinator", "--channel", "D123")
    return "demo"


@pytest.fixture(autouse=True)
def drawn(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    pages: list[str] = []
    monkeypatch.setattr(view, "cards", lambda name: view.NO_CARDS)
    monkeypatch.setattr(page, "draw", lambda name, markdown: pages.append(markdown) or "https://slack.example/docs/T1/F1")
    return pages


@pytest.fixture(autouse=True)
def agents_at_work(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watch, "local_gap", lambda job_id, job, data, moment: None)
    monkeypatch.setattr(orca, "worker", lambda dispatch: LIVE_WORKER)
