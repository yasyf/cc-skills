import json
import subprocess
from collections.abc import Callable

import pytest

from drive import slack, view

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
}


@pytest.mark.parametrize("caller", CALLERS)
def test_each_read_of_a_cc_slack_verb_that_prints_lines_by_default_parses_its_json(cc_slack: str, caller: str) -> None:
    view.refresh_cards(cc_slack)
    read, parsed = CALLERS[caller]
    assert read() == parsed


def test_a_card_read_cc_slack_cannot_answer_keeps_the_cards_last_stored_and_marks_only_their_source_unavailable(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    listed = {"questions": [{"waits": "demo:j1-1", "card": "https://acme.slack.com/archives/D1/p1"}], "pr_cards": [], "closed": [], "team_url": "https://acme.slack.com"}
    monkeypatch.setattr(view, "cards", lambda name: listed)
    view.refresh_cards(demo)

    def refused(name: str) -> dict:
        raise slack.SlackError("cc-slack daemon not running")

    monkeypatch.setattr(view, "cards", refused)
    view.refresh_cards(demo)

    found, source = view.stored_cards(demo)
    assert (found, source) == (listed, "unavailable: cc-slack daemon not running")
    assert view.live_cards(demo) is None


def test_a_read_slack_rate_limits_waits_the_seconds_slack_names_and_reads_again(monkeypatch: pytest.MonkeyPatch) -> None:
    tries: list[list[str]] = []
    waited: list[float] = []
    limited = "cc-slack: can't read thread 1794687682.137749 now (ratelimited): Slack limits the read for 10s more"

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        tries.append(argv)
        if len(tries) < 3:
            return subprocess.CompletedProcess(argv, 1, "", limited)
        return subprocess.CompletedProcess(argv, 0, json.dumps(READS["thread"]), "")

    monkeypatch.setattr(slack, "launcher", lambda: "cc-slack")
    monkeypatch.setattr(slack.subprocess, "run", run)
    monkeypatch.setattr(slack.time, "sleep", waited.append)
    assert slack.cc_slack("thread", "--url", POSTED["permalink"], "--json") == READS["thread"]
    assert (len(tries), waited) == (3, [11, 11])
    monkeypatch.setattr(slack.subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, "", "cc-slack: channel_not_found"))
    with pytest.raises(slack.SlackError, match="channel_not_found"):
        slack.cc_slack("thread", "--url", POSTED["permalink"], "--json")


def test_a_read_slack_keeps_rate_limiting_fails_after_two_waits_or_a_minute_of_them(monkeypatch: pytest.MonkeyPatch) -> None:
    waited: list[float] = []

    def limited(seconds: int) -> Callable[..., subprocess.CompletedProcess]:
        return lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, "", f"cc-slack: can't read thread {TS} now (ratelimited): Slack limits the read for {seconds}s more")

    monkeypatch.setattr(slack, "launcher", lambda: "cc-slack")
    monkeypatch.setattr(slack.time, "sleep", waited.append)
    monkeypatch.setattr(slack.subprocess, "run", limited(10))
    with pytest.raises(slack.SlackError, match="ratelimited"):
        slack.cc_slack("thread", "--url", POSTED["permalink"], "--json")
    assert waited == [11, 11]
    waited.clear()
    monkeypatch.setattr(slack.subprocess, "run", limited(45))
    with pytest.raises(slack.SlackError, match="ratelimited"):
        slack.cc_slack("thread", "--url", POSTED["permalink"], "--json")
    assert waited == [46]
