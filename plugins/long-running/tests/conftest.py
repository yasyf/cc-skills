from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pytest
from drive import cli, dashboard, github, orca, page, settled, slack, view, watch

SESSION = "session-under-test"
LIVE_WORKER = {
    "worker": {"state": "ready"},
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
    monkeypatch.setattr(dashboard, "tailnet", lambda: None)
    monkeypatch.setattr(page, "draw", lambda name, markdown: pages.append(markdown) or page.Drawn("https://slack.example/docs/T1/F1", False))
    return pages


@dataclass
class Thread:
    messages: list[dict] = field(default_factory=list)
    reads: list[tuple[str, ...]] = field(default_factory=list)

    def post(self, moment: datetime, **fields: object) -> str:
        ts = f"{moment.timestamp():.6f}"
        self.messages.append({"ts": ts, "thread_ts": "1794200000.000100", **fields})
        return f"https://acme.slack.com/archives/D123/p{ts.replace('.', '')}?thread_ts=1794200000.000100"


@pytest.fixture(autouse=True)
def slack_thread(monkeypatch: pytest.MonkeyPatch) -> Thread:
    thread = Thread()
    monkeypatch.setattr(slack, "launcher", lambda: "cc-slack")
    monkeypatch.setattr(slack, "thread", lambda *where: thread.reads.append(where) or list(thread.messages))
    return thread


@dataclass
class Checked:
    findings: list[str] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)


@pytest.fixture(autouse=True)
def checked(monkeypatch: pytest.MonkeyPatch) -> Checked:
    check = Checked()
    monkeypatch.setattr(slack, "check_post", lambda text: check.texts.append(text) or list(check.findings))
    return check


@dataclass
class Drawing:
    refusal: str | None = None
    cards: list[dict] = field(default_factory=list)


@pytest.fixture(autouse=True)
def drawing(monkeypatch: pytest.MonkeyPatch) -> Drawing:
    draw = Drawing()
    monkeypatch.setattr(slack, "draw_refusal", lambda card: draw.cards.append(card) or draw.refusal)
    return draw


@dataclass
class Withdrawals:
    cards: list[str] = field(default_factory=list)
    asked: list[tuple[str, str]] = field(default_factory=list)


@pytest.fixture(autouse=True)
def withdrawals(monkeypatch: pytest.MonkeyPatch) -> Withdrawals:
    withdrawn = Withdrawals()
    monkeypatch.setattr(slack, "withdraw_pr_card", lambda url, reason: withdrawn.asked.append((url, reason)) or list(withdrawn.cards))
    return withdrawn


@pytest.fixture(autouse=True)
def agents_at_work(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watch, "local_gap", lambda job_id, job, data, moment: None)
    monkeypatch.setattr(orca, "worker", lambda dispatch: LIVE_WORKER)
    monkeypatch.setattr(github, "open_on", lambda heads: {})


@dataclass
class Answers:
    found: list[dict] = field(default_factory=list)
    asked: list[str] = field(default_factory=list)


@pytest.fixture(autouse=True)
def recorded(monkeypatch: pytest.MonkeyPatch) -> Answers:
    answers = Answers()
    monkeypatch.setattr(settled, "answers", lambda text, cwd: answers.asked.append(text) or list(answers.found))
    return answers
