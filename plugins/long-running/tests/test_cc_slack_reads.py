import json
import subprocess
from collections.abc import Callable

import pytest

from drive import slack, talk, view

READERS = ((view, "cards", view.cards), (slack, "thread", slack.thread))
TS = "1794300000.000100"
POSTED = {"ts": TS, "text": "The lock is released.", "from_claude": True, "permalink": f"https://acme.slack.com/archives/D123/p{TS.replace('.', '')}"}
CARDS = {"team_url": "https://acme.slack.com/", "questions": [], "pr_cards": [], "closed": []}
READS = {
    "drive-cards": CARDS,
    "thread": {"channel_id": "D123", "thread_ts": TS, "messages": [POSTED], "truncated": False},
    "history": {"channel_id": "D0GROUP0001", "messages": [POSTED]},
}
LINES = {
    "drive-cards": f"demo:j1-1794300000000  2026-11-13 17:00  {POSTED['permalink']}  Which store keeps the rows?\n",
    "thread": f"thread {TS} in D123\n2026-11-13 17:00  {TS}  Ada (claude) [from_claude]    The lock is released.\n",
    "history": f"2026-11-13 17:00  {TS}  {POSTED['permalink']}  Ada (claude) [from_claude]    The lock is released.\n",
}
DEADLINE = slack.SlackError("context deadline exceeded")


@pytest.fixture
def cc_slack(demo: str, monkeypatch: pytest.MonkeyPatch) -> str:
    """cc-slack as 0.20.28 prints drive-cards, thread and history: a line per card or message, and JSON only under --json; the drive's own readers of it, which every other test stubs, run."""

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        verb = argv[1]
        return subprocess.CompletedProcess(argv, 0, json.dumps(READS[verb]) + "\n" if "--json" in argv else LINES[verb], "")

    monkeypatch.setattr(subprocess, "run", run)
    for module, reader, read in READERS:
        monkeypatch.setattr(module, reader, read)
    return demo


CALLERS: dict[str, tuple[Callable[[], object], object]] = {
    "view.cards": (lambda: view.cards("demo"), CARDS),
    "slack.thread": (lambda: slack.thread("--channel", "D123", "--thread", TS), [POSTED]),
    "slack.group_dm": (lambda: slack.group_dm("U0PRIYA0001"), "D0GROUP0001"),
    "talk.landed in a thread": (lambda: talk.landed("demo", {"thread_ts": TS}, 1794300000.0, POSTED["text"], DEADLINE)["permalink"], f"{POSTED['permalink']}?thread_ts={TS}&cid=D123"),
    "talk.landed at his DM's root": (lambda: talk.landed("demo", {}, 1794300000.0, POSTED["text"], DEADLINE)["permalink"], POSTED["permalink"]),
}


@pytest.mark.parametrize("caller", CALLERS)
def test_each_read_of_a_cc_slack_verb_that_prints_lines_by_default_parses_its_json(cc_slack: str, caller: str) -> None:
    view.refresh_cards(cc_slack)
    read, parsed = CALLERS[caller]
    assert read() == parsed
