from collections.abc import Callable
from pathlib import Path

import pytest
from drive import cli

SESSION = "session-under-test"


@pytest.fixture
def state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "state"
    monkeypatch.setenv("LONG_RUNNING_STATE", str(root))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    monkeypatch.delenv("LONG_RUNNING_DRIVE", raising=False)
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
    run("start", "demo", "--channel", "D123")
    return "demo"
