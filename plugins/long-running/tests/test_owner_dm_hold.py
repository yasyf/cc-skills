from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest
from captain_hook.events import StopEvent, UserPromptSubmitEvent
from fire import fire
from hooks import owner_returns
from hooks.compaction_handoff import CompactionState
from test_register_capture import SESSION, root, slack_message  # noqa: F401

WHOAMI = json.dumps({"dm_channel_id": "D0B6Z"})
OWED = {"thread_ts": "1793684147.131059", "ts": "1793705742.180139", "text": "A", "permalink": "https://acme.slack.com/archives/D0B6Z/p1793705742180139"}


def install(config: Path, version: str, *flags: str) -> str:
    path = config / "plugins" / "cache" / "cc-slack" / "cc-slack" / version / "bin" / "cc-slack"
    path.parent.mkdir(parents=True)
    path.write_text("")
    for flag in flags:
        (path.parents[1] / flag).write_text("")
    return str(path)


@pytest.fixture
def shipped(tmp_path) -> str:
    return install(tmp_path / "config", "0.4.27")


def fired(root, event):  # noqa: F811
    with mock.patch.object(subprocess, "run", root.run):
        return fire(owner_returns, event)


def blockers(root):  # noqa: F811
    return [call for call in root.calls if call[:2] == ("cci", "post") and "blocker" in call]


def test_the_communicator_holds_the_owners_dm_through_the_shipped_binary(root, shipped):  # noqa: F811
    root.routes[(shipped, "whoami")] = WHOAMI

    fired(root, root.event(UserPromptSubmitEvent, prompt="where are we"))

    assert (shipped, "follow", "--channel", "D0B6Z", "--session", SESSION) in root.calls
    assert not [call for call in root.calls if call[0] == "cc-slack"]


def test_the_newest_live_install_is_the_shipped_binary(root, tmp_path):  # noqa: F811
    config = tmp_path / "config"
    install(config, "0.4.9")
    newest = install(config, "0.4.27")
    install(config, "0.4.28", ".orphaned_at")
    root.routes[(newest, "whoami")] = WHOAMI

    fired(root, root.event(UserPromptSubmitEvent, prompt="where are we"))

    assert (newest, "whoami") in root.calls


def test_the_coordinator_never_holds_the_owners_dm(root, shipped):  # noqa: F811
    root.routes[(shipped, "whoami")] = WHOAMI
    event = root.event(UserPromptSubmitEvent, prompt="where are we")
    CompactionState(active=True, coordinator=True).save(event)

    fired(root, event)

    assert not [call for call in root.calls if call[:2] == (shipped, "follow")]


def test_a_missing_plugin_passes_with_a_reason(root):  # noqa: F811
    root.typed("where are we", "2026-10-28T03:53:25.695Z")

    fired(root, root.event(StopEvent))

    [blocker] = blockers(root)
    assert "no cc-slack plugin under" in blocker[blocker.index("--text") + 1]
    assert blocker[blocker.index("--to") + 1] == "meta"


def test_a_failing_cc_slack_passes_with_a_reason(root, shipped):  # noqa: F811
    root.typed("where are we", "2026-10-28T03:53:25.695Z")
    root.routes[(shipped, "dm-owed")] = (1, "cc-slack can't reach the daemon")

    fired(root, root.event(StopEvent))

    [blocker] = blockers(root)
    assert "cc-slack dm-owed" in blocker[blocker.index("--text") + 1]


def test_a_stop_is_refused_until_the_reply_is_answered(root, shipped):  # noqa: F811
    root.typed("where are we", "2026-10-28T03:53:25.695Z")
    root.routes[(shipped, "dm-owed")] = json.dumps({"replies": [OWED], "holder_session": SESSION})

    refused = [result.message for result in fired(root, root.event(StopEvent))]
    assert any("p1793705742180139" in message for message in refused)

    root.routes[(shipped, "dm-owed")] = json.dumps({"replies": [], "holder_session": SESSION})
    allowed = [result.message for result in fired(root, root.event(StopEvent))]
    assert not any("p1793705742180139" in message for message in allowed)


def test_a_reply_is_owed_only_by_the_session_holding_the_dm(root, shipped):  # noqa: F811
    root.typed("where are we", "2026-10-28T03:53:25.695Z")
    root.routes[(shipped, "dm-owed")] = json.dumps({"replies": [OWED], "holder_session": "another-drive"})

    allowed = [result.message for result in fired(root, root.event(StopEvent))]
    assert not any("p1793705742180139" in message for message in allowed)


def test_a_dm_counts_as_the_owners_words_only_for_the_session_holding_it(root, shipped):  # noqa: F811
    root.typed("where are we", "2026-10-28T03:53:25.695Z")
    root.queued(slack_message(text="you forgot this again"), "2026-10-28T03:53:26.695Z")

    def words(holder: str) -> str:
        root.routes[(shipped, "dm-owed")] = json.dumps({"replies": [], "holder_session": holder})
        with mock.patch.object(subprocess, "run", root.run):
            return owner_returns.owner_words(root.event(StopEvent))

    assert "you forgot this again" not in words("another-drive")
    assert "you forgot this again" in words(SESSION)
