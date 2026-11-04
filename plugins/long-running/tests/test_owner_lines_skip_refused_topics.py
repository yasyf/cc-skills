from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from captain_hook import Allow, Block, Input, Warn
from captain_hook.app import _state
from captain_hook.testing import helpers

from hooks.compaction_handoff import CompactionState
from hooks.owner_facing import SLACK_CARD
from hooks.owner_lines_skip_refused_topics import REFUSED
from hooks.owner_replies_carry_news import HIS_DM
from hooks.owner_returns import CC_SLACK_CACHE
from hooks.tests.handoff_fixtures import reply_transcript
from hooks.tests.register_fixtures import DRIVE_CONFIG
from hooks.urgent_lines_name_the_harm import DM_TOOLS

DRIVE = "brook-drive"
LISTED = {"orca": {"refused_briefs": [{"match": "(?i)edge\\.example\\.test", "except": "(?i)never", "why": "never chased"}]}}
MATCHING = "A 403 from edge.example.test on the tenant."
SPARED = "edge.example.test 403s are never chased."
NEIGHBOUR = "The api answers 500 on the tenant."
ACTIVE = [CompactionState(active=True, communicator=True)]
REFUSAL = r"^The owner ruled this class is never reported[\s\S]*edge\.example\.test"
UNREAD = r"^Warning, nothing was blocked"
DM = {"channel_id": "D0B6Z", "thread_ts": "1.2"}
SOMEONE = json.dumps({"channel_id": "D0B6Z", "peer": "U0SOMEONE", "peer_name": "dana"})


def drive_home(tmp_path: Path, runner: str | None) -> dict[str, str]:
    state = tmp_path / "state"
    state.mkdir()
    if runner is not None:
        (state / "runner.json").write_text(runner)
    drives = tmp_path / "long-running" / "drives"
    drives.mkdir(parents=True)
    (drives / f"{DRIVE}.json").write_text(json.dumps({"drive": DRIVE, "sessions": [], "state_dir": str(state), "checkout": "/repo"}))
    shutil.copytree(Path(DRIVE_CONFIG["CLAUDE_CONFIG_DIR"]) / "plugins", tmp_path / "plugins")
    return {"CLAUDE_CONFIG_DIR": str(tmp_path), "CLAUDE_LONG_RUNNING_DRIVE": DRIVE}


def peer_read(env: dict[str, str], peer: str | tuple) -> dict[str, str | tuple]:
    return {f"{Path(env['CLAUDE_CONFIG_DIR']) / CC_SLACK_CACHE / '0.4.27' / 'bin' / 'cc-slack'} dm-peer": peer}


def expect(handler: str, inp: Input, expected: object) -> None:
    entry = next(entry for entry in _state.hooks if entry.handler and entry.handler.__name__ == handler)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(_state, "hooks", [replace(entry, spec=replace(entry.spec, tests={inp: expected}))])
        helpers.run_inline_tests()


def card(env: dict[str, str], body: str) -> Input:
    return Input(tool=SLACK_CARD, tool_input={"card": {"title": "Why is it failing?", "body": body}}, env=env)


def board(env: dict[str, str], body: str) -> Input:
    return Input(command=f"cc-present reply --block skew --body '{body}'", env=env)


def communicator_dm(env: dict[str, str], text: str) -> Input:
    return Input(tool=DM_TOOLS[0], tool_input={**DM, "text": text}, state=ACTIVE, env=env)


def lane_dm(env: dict[str, str], text: str, peer: str | tuple = HIS_DM) -> Input:
    return Input(tool=DM_TOOLS[1], tool_input={**DM, "text": text}, env=env | {"CLAUDE_LONG_RUNNING_LANE": "release-5"}, commands=peer_read(env, peer))


def cli_dm(env: dict[str, str], text: str) -> Input:
    return Input(command=f"cc-slack reply --channel D0B6Z --text '{text}'", env=env | {"CLAUDE_LONG_RUNNING_LANE": "release-5"}, commands=peer_read(env, HIS_DM))


@pytest.mark.parametrize("path", [card, board, communicator_dm, lane_dm, cli_dm])
def test_every_owner_path_refuses_a_listed_line_and_passes_its_neighbour(tmp_path: Path, path):
    env = drive_home(tmp_path, json.dumps(LISTED))
    expect("owner_lines_skip_refused_topics", path(env, MATCHING), Block(pattern=REFUSAL))
    expect("owner_lines_skip_refused_topics", path(env, NEIGHBOUR), Allow())
    expect("owner_lines_skip_refused_topics", path(env, SPARED), Allow())


def test_a_lane_dm_to_someone_else_passes(tmp_path: Path):
    env = drive_home(tmp_path, json.dumps(LISTED))
    expect("owner_lines_skip_refused_topics", lane_dm(env, MATCHING, SOMEONE), Allow())


def test_a_lane_dm_whose_peer_did_not_read_warns_and_blocks_nothing(tmp_path: Path):
    env = drive_home(tmp_path, json.dumps(LISTED))
    expect("owner_lines_skip_refused_topics", lane_dm(env, MATCHING, (1, "cc-slack can't reach the daemon")), Warn(pattern=UNREAD))


@pytest.mark.parametrize(
    ("text", "expected"),
    [(MATCHING, Block(pattern=REFUSAL)), (f"Two things.\n{MATCHING}", Block(pattern=REFUSAL)), (NEIGHBOUR, Allow()), (SPARED, Allow())],
)
def test_a_communicator_reply_is_refused_and_its_neighbour_passes(tmp_path: Path, text: str, expected: object):
    env = drive_home(tmp_path, json.dumps(LISTED))
    expect("owner_replies_skip_refused_topics", Input(transcript=reply_transcript(text), state=ACTIVE, env=env), expected)


@pytest.mark.parametrize("runner", [None, "{}", '{"orca": {}}', '{"orca": {"refused_briefs": []}}'])
def test_a_drive_with_no_list_changes_nothing(tmp_path: Path, runner: str | None):
    env = drive_home(tmp_path, runner)
    expect("owner_lines_skip_refused_topics", card(env, MATCHING), Allow())
    expect("owner_replies_skip_refused_topics", Input(transcript=reply_transcript(MATCHING), state=ACTIVE, env=env), Allow())


@pytest.mark.parametrize("runner", ["{not json", '{"orca": {"refused_briefs": [{"match": "("}]}}'])
def test_an_unreadable_list_warns_and_blocks_nothing(tmp_path: Path, runner: str):
    env = drive_home(tmp_path, runner)
    expect("owner_lines_skip_refused_topics", card(env, MATCHING), Warn(pattern=rf"{UNREAD}[\s\S]*refused_briefs"))


def test_the_refusal_stays_inside_the_packs_message_bar():
    assert len(REFUSED) <= 300
    assert len(REFUSED.rstrip(".").split(". ")) <= 2
