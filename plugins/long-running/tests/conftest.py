import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pytest
from drive import (
    agents,
    card,
    cli,
    dashboard,
    github,
    land,
    orca,
    page,
    public_check,
    served,
    settled,
    slack,
    state,
    view,
    watch,
)

SESSION = "session-under-test"
TEAM = [
    {"name": "rowan", "real_name": "Rowan Vale", "email": "rowan@example.com"},
    {"name": "juniper.cole", "real_name": "Juniper Cole", "email": "juniper.cole@example.com"},
    {"name": "mac.ivers", "real_name": "Mac Ivers", "email": "mac.ivers@example.com"},
]
LIVE_WORKER = {
    "worker": {"state": "ready"},
    "projection": {"liveness": {"verdict": "live"}, "outcome": "in_progress", "attention": {"categories": []}},
    "terminal": {"lastOutputAt": None},
}
CARD_BODY = """## Summary
`land.py` lands the PR once his tap comes in.

## What you'll hate most
It lands without a second look.

## His words, step by step
- "land it" beside `land.py:1`
"""

RESEARCH = """DONE researched the store copy
Researched:
rulings: none on the copy path
exists: supervisor/copies.rs store_copy_at copies one store
open: none
"""

SHORT_ASK = """ASK How should a finished run's files be laid out so its page opens without guessing?
A finished run is about 180 small files, each with its page map at the end, so the browser guesses where each map starts.
Recommended: one snapshot file per finished run, so any finished run opens in 3 requests.
Meanwhile nothing is built on either layout.
Show me:
1 · One snapshot file per finished run, the requests its page makes:
```http
GET snapshot.ltx      map at a fixed place
GET 2 page ranges
```

2 · A map file beside each journal file, the requests its page makes:
```http
GET journal/0001.map  the map, no guessing
GET journal/0001.ltx  one page range
... 180 files, two requests each
```
- One snapshot file per finished run, written when it ends, old runs backfilled: opens in 3 requests (recommended)
- A map file beside each journal file: no guessing, but one request per file and twice the objects
- Map at the start of each file: changes the shared file format and still needs a length read first

Findings:
Root cause: none: a design choice, with no defect behind it
(1) What a journal file is: a run's journal is a SQLite database, copied to the team's bucket as LTX files.
(2) Each LTX file ends with a page map; its length sits in the last 24 bytes.
"""


@pytest.fixture
def state_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "long-running"
    monkeypatch.delenv("LONG_RUNNING_STATE", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SESSION)
    monkeypatch.delenv("LONG_RUNNING_DRIVE", raising=False)
    monkeypatch.setenv("ORCA_TERMINAL_HANDLE", "term_outer")
    monkeypatch.setenv("TMUX", "/private/tmp/tmux-501/default,1,0")
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


@pytest.fixture
def communicator(run: Callable[..., tuple[int, str]], demo: str) -> str:
    run("start", demo, "--role", "communicator")
    with state.edit(demo) as data:
        data["roles"]["coordinator"]["session"] = "elsewhere"
    return demo


@pytest.fixture(autouse=True)
def closed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    urls: list[str] = []
    monkeypatch.setattr(github, "close", urls.append)
    return urls


@pytest.fixture(autouse=True)
def drawn(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    pages: list[str] = []
    monkeypatch.setattr(view, "cards", lambda name: view.NO_CARDS)
    monkeypatch.setattr(dashboard, "tailnet", lambda: None)
    monkeypatch.setattr(page, "draw", lambda name, markdown: pages.append(markdown) or page.Drawn("https://slack.example/docs/T1/F1", False))
    return pages


@pytest.fixture(autouse=True)
def landers(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
    started: list[tuple[str, ...]] = []
    monkeypatch.setattr(land, "spawn", lambda name, keys: started.append(keys) or os.getpid())
    monkeypatch.setattr(github, "default_branch", lambda repo: "main")
    monkeypatch.setattr(github, "below", lambda key: [])
    return started


@pytest.fixture(autouse=True)
def native_stacks(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[tuple[str, str]]]:
    stacks: dict[str, list[tuple[str, str]]] = {}
    monkeypatch.setattr(github, "stacks", lambda keys: {key: stacks[key] for key in keys if key in stacks})
    return stacks


@dataclass
class Thread:
    messages: list[dict] = field(default_factory=list)
    reads: list[tuple[str, ...]] = field(default_factory=list)

    def post(self, moment: datetime, **fields: object) -> str:
        ts = f"{moment.timestamp():.6f}"
        self.messages.append({"ts": ts, "thread_ts": "1794200000.000100", **fields})
        return f"https://acme.slack.com/archives/D123/p{ts.replace('.', '')}?thread_ts=1794200000.000100"


@pytest.fixture(autouse=True)
def records(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    shown: dict[str, str] = {}
    monkeypatch.setattr(agents, "record", lambda seq: shown.get(seq, f"m{seq}"))
    return shown


@pytest.fixture(autouse=True)
def cci_records(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict]:
    kept: dict[str, dict] = {}
    monkeypatch.setattr(agents, "shown", lambda seq: kept.get(seq, {"seq": int(seq), "kind": "report", "lane": "root", "fields": {"attrs": {}}}))
    return kept


@pytest.fixture(autouse=True)
def ended_sessions(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    ended: set[str] = set()
    monkeypatch.setattr(agents, "gone", lambda session: session in ended)
    return ended


@dataclass
class Judge:
    against: list[dict] = field(default_factory=list)
    asked: list[tuple[str, dict]] = field(default_factory=list)


@pytest.fixture(autouse=True)
def judge(monkeypatch: pytest.MonkeyPatch) -> Judge:
    judging = Judge()
    monkeypatch.setattr(served, "change", lambda key: {"title": f"Fix: {key}", "head": card.drive_prs()[key][1]["head"], "diff": ""})
    monkeypatch.setattr(served, "judged", lambda name, said, changes: judging.asked.append((said, changes)) or list(judging.against))
    return judging


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
    asked: list[list[str]] = field(default_factory=list)


@pytest.fixture(autouse=True)
def checked(monkeypatch: pytest.MonkeyPatch) -> Checked:
    check = Checked()
    monkeypatch.setattr(slack, "check_post", lambda text, asked: check.texts.append(text) or check.asked.append(asked) or list(check.findings))
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


@dataclass
class Redraws:
    asked: list[str] = field(default_factory=list)
    failure: str | None = None


@pytest.fixture(autouse=True)
def redraws(monkeypatch: pytest.MonkeyPatch) -> Redraws:
    redrawn = Redraws()

    def redraw(url: str) -> int:
        redrawn.asked.append(url)
        if redrawn.failure:
            raise slack.SlackError(redrawn.failure)
        return 1

    monkeypatch.setattr(slack, "redraw_pr_cards", redraw)
    return redrawn


@pytest.fixture(autouse=True)
def agents_at_work(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(watch, "local_gap", lambda job_id, job, data, moment: None)
    monkeypatch.setattr(orca, "worker", lambda dispatch: LIVE_WORKER)
    monkeypatch.setattr(github, "open_on", lambda heads: {})


@dataclass
class WatchProcess:
    ensured: list[str] = field(default_factory=list)
    listened: list[tuple[str, ...]] = field(default_factory=list)


@pytest.fixture(autouse=True)
def posted_lanes(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    lanes: set[str] = set()
    monkeypatch.setattr(cli, "lane_posted", lambda name, lane: lane in lanes)
    return lanes


@pytest.fixture(autouse=True)
def watch_process(monkeypatch: pytest.MonkeyPatch) -> WatchProcess:
    process = WatchProcess()

    def ensure(name: str, project: str | None = None) -> str:
        process.ensured.append(f"{name} --project {project}" if project else name)
        return f"the watch runs for {name}"

    monkeypatch.setattr(watch, "ensure", ensure)
    monkeypatch.setattr(watch, "listen", lambda name, session, project=None: process.listened.append((name, session, project) if project else (name, session)))
    return process


@pytest.fixture(autouse=True)
def teammates(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    team = [dict(user) for user in TEAM]
    monkeypatch.setattr(public_check, "roster", lambda: public_check.Roster.of(team, "me@example.com"))
    return team


@pytest.fixture(autouse=True)
def visibilities(monkeypatch: pytest.MonkeyPatch) -> dict[str, str | None]:
    seen: dict[str, str | None] = {}
    monkeypatch.setattr(github, "visibility", lambda repo: seen.get(repo, "private"))
    return seen


@dataclass
class Answers:
    found: list[dict] = field(default_factory=list)
    nearest: list[dict] = field(default_factory=list)
    asked: list[str] = field(default_factory=list)
    near: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    cwds: list[str] = field(default_factory=list)
    shown: dict[str, dict] = field(default_factory=dict)


@pytest.fixture(autouse=True)
def backfilled(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    checkouts: list[str] = []
    monkeypatch.setattr(settled, "backfill", checkouts.append)
    return checkouts


@pytest.fixture(autouse=True)
def recorded(monkeypatch: pytest.MonkeyPatch) -> Answers:
    answers = Answers()

    def matched(text: str, cwd: str, *flags: str) -> list[dict]:
        answers.cwds.append(cwd)
        if "--nearest" in flags:
            answers.near.append((text, flags))
            return list(answers.nearest)
        answers.asked.append(text)
        return list(answers.found)

    monkeypatch.setattr(settled, "matched", matched)
    monkeypatch.setattr(settled, "shown", lambda answer_id, cwd: answers.shown.get(answer_id, {"id": answer_id, "title": "", "body": "", "tags": []}))
    return answers


@pytest.fixture(autouse=True)
def on_github(monkeypatch: pytest.MonkeyPatch) -> dict:
    pr = {
        "title": "Fix: land it",
        "body": CARD_BODY,
        "author": {"login": "yasyf", "is_bot": False},
        "comments": [],
        "state": "OPEN",
        "isDraft": False,
        "headRefOid": "abc",
        "baseRefName": "main",
        "headRefName": "land-it",
        "reviewDecision": "REVIEW_REQUIRED",
        "labels": [],
        "url": "https://github.com/Forge-AI/monorepo/pull/42",
    }
    monkeypatch.setattr(card, "run", lambda cmd, **_: json.dumps(pr["comments"] if cmd[1] == "api" else pr))
    return pr


FAKE_CODEX_ASK = """#!/bin/sh
run="$2"
printf '{"lane":".","state":"%s","reply_file":"%s","reply_size":1,"log_file":"%s","question":"q","question_files":1}\\n' \\
  "$(cat "$run/state")" "$(sed -n 1p "$run/meta")" "$(sed -n 2p "$run/meta")"
"""


@dataclass
class CodexAsk:
    """Run folders shaped as codex-ask writes them, read by a codex-ask whose `--collect` prints the state each folder's `state` file holds."""

    runs: Path
    made: int = 0

    def run(self, state: str, reply: str = "", log: str = "") -> Path:
        self.made += 1
        folder = self.runs / f"codex-ask.{self.made}"
        folder.mkdir()
        (folder / "state").write_text(state)
        (folder / f"codex-r-{self.made}").write_text(reply)
        (folder / f"codex-q-{self.made}.log").write_text(log)
        (folder / "meta").write_text(f"{folder / f'codex-r-{self.made}'}\n{folder / f'codex-q-{self.made}.log'}\n{{}}\n")
        return folder / f"codex-r-{self.made}"


@pytest.fixture
def codex_ask(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CodexAsk:
    plugin = tmp_path / "codex-plugin"
    (plugin / "bin").mkdir(parents=True)
    (plugin / "bin" / "codex-ask").write_text(FAKE_CODEX_ASK)
    (plugin / "bin" / "codex-ask").chmod(0o755)
    config = tmp_path / "config"
    (config / "plugins").mkdir(parents=True)
    (config / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {"codex@skills": [{"installPath": str(plugin)}]}}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    (tmp_path / "runs").mkdir()
    return CodexAsk(tmp_path / "runs")
