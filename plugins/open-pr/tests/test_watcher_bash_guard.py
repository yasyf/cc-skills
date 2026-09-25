from __future__ import annotations

import json
import subprocess

import pytest
from conftest import PLUGIN

SCRIPT = PLUGIN / "scripts" / "watcher-bash-guard.sh"
POLL = f"bash {PLUGIN}/scripts/pr-poll.sh acme/widgets 7 /tmp/cache/pr/7.json"


def guard(command: str, agent_type: str | None) -> subprocess.CompletedProcess[str]:
    payload: dict[str, object] = {"tool_name": "Bash", "tool_input": {"command": command}}
    if agent_type is not None:
        payload["agent_type"] = agent_type
    return subprocess.run(
        ["bash", str(SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=5,
    )


@pytest.mark.parametrize(
    "command",
    [
        POLL,
        f"while true; do {POLL}; sleep 30; done",
        f"PR_POLL_STACK=2 {POLL}",
        f"timeout 600 {POLL}",
        f"cd /tmp && {POLL}",
        f"'{PLUGIN}/scripts/pr-poll.sh' acme/widgets 7 /tmp/cache/pr/7.json",
    ],
)
def test_watcher_bash_poll_is_blocked(command: str):
    result = guard(command, "open-pr:pr-watcher")
    assert result.returncode == 2
    assert "only under Monitor" in result.stderr


@pytest.mark.parametrize(
    ("command", "agent_type"),
    [
        ("gh pr view 7 --json headRefOid", "open-pr:pr-watcher"),
        (f"sed -n 1,40p {PLUGIN}/scripts/pr-poll.sh", "open-pr:pr-watcher"),
        (f"bash -n {PLUGIN}/scripts/pr-poll.sh", "open-pr:pr-watcher"),
        (f"grep -n DONE {PLUGIN}/scripts/pr-poll.sh", "open-pr:pr-watcher"),
        (POLL, None),
        (POLL, "general-purpose"),
    ],
)
def test_other_commands_and_agents_pass(command: str, agent_type: str | None):
    result = guard(command, agent_type)
    assert result.returncode == 0
    assert result.stderr == ""
