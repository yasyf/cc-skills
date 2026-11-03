from __future__ import annotations

import json
import subprocess
from unittest import mock

from captain_hook.events import StopEvent, UserPromptSubmitEvent
from fire import fire
from hooks import owner_returns
from hooks.compaction_handoff import CompactionState
from test_register_capture import SESSION, root  # noqa: F401

WHOAMI = json.dumps({"dm_channel_id": "D0B6Z"})
HOLD = ("cc-slack", "follow", "--channel", "D0B6Z", "--session", SESSION)


def fired(root, event):  # noqa: F811
    with mock.patch.object(subprocess, "run", root.run):
        return fire(owner_returns, event)


def test_the_communicator_holds_the_owners_dm_on_a_prompt(root):  # noqa: F811
    root.routes[("cc-slack", "whoami")] = WHOAMI

    fired(root, root.event(UserPromptSubmitEvent, prompt="where are we"))

    assert HOLD in root.calls


def test_the_coordinator_never_holds_the_owners_dm(root):  # noqa: F811
    root.routes[("cc-slack", "whoami")] = WHOAMI
    event = root.event(UserPromptSubmitEvent, prompt="where are we")
    CompactionState(active=True, coordinator=True).save(event)

    fired(root, event)

    assert HOLD not in root.calls


def test_no_identity_holds_nothing(root):  # noqa: F811
    root.routes[("cc-slack", "whoami")] = (1, "cc-slack can't reach the daemon")

    fired(root, root.event(UserPromptSubmitEvent, prompt="where are we"))

    assert not [call for call in root.calls if call[:2] == ("cc-slack", "follow")]


def test_a_stop_is_refused_until_the_reply_is_answered(root):  # noqa: F811
    root.typed("where are we", "2026-10-28T03:53:25.695Z")
    owed = {"thread_ts": "1793684147.131059", "ts": "1793705742.180139", "text": "A", "permalink": "https://acme.slack.com/archives/D0B6Z/p1793705742180139"}
    root.routes[("cc-slack", "dm-owed")] = json.dumps({"replies": [owed]})

    refused = [result.message for result in fired(root, root.event(StopEvent))]
    assert any("p1793705742180139" in message for message in refused)

    root.routes[("cc-slack", "dm-owed")] = json.dumps({"replies": []})
    allowed = [result.message for result in fired(root, root.event(StopEvent))]
    assert not any("p1793705742180139" in message for message in allowed)
