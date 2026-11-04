from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest
from captain_hook import Allow, Input, Warn
from captain_hook.app import _state
from captain_hook.testing import helpers

from hooks.compaction_handoff import CompactionState
from hooks.lines_name_a_ruling import RULED
from hooks.owner_facing import SLACK_CARD
from hooks.tests.handoff_fixtures import reply_transcript
from hooks.tests.register_fixtures import DRIVE_CONFIG
from hooks.urgent_lines_name_the_harm import DM_TOOLS

DRIVE = "brook-drive"
ACTIVE = [CompactionState(active=True, communicator=True)]
THE_CARD = "The old cloud runner's journal calls began failing with timeouts and 403s at the GSK edge."
AKAMAI = {
    "id": "ff7fca14cc8d31efc64b4eb588b5b168d4a86534",
    "title": "Do we chase Akamai edge 403s (GSK or any tenant proxy) at all, ever?",
    "body": "No. Never a lane, a read, a report, a line to him.",
    "phrase": "gsk edge",
}
MATCH = "ccn -R /repo answer match"
HIT = {MATCH: json.dumps([AKAMAI])}
MISS = {MATCH: "[]"}
DOWN = {MATCH: (1, "unknown command")}
SHOWN = rf"^{RULED}[\s\S]*ff7fca1 Do we chase Akamai edge 403s[\s\S]*Never a lane"


def drive_home(tmp_path: Path) -> dict[str, str]:
    state = tmp_path / "state"
    state.mkdir()
    drives = tmp_path / "long-running" / "drives"
    drives.mkdir(parents=True)
    (drives / f"{DRIVE}.json").write_text(json.dumps({"drive": DRIVE, "sessions": [], "state_dir": str(state), "checkout": "/repo"}))
    shutil.copytree(Path(DRIVE_CONFIG["CLAUDE_CONFIG_DIR"]) / "plugins", tmp_path / "plugins")
    return {"CLAUDE_CONFIG_DIR": str(tmp_path), "CLAUDE_LONG_RUNNING_DRIVE": DRIVE}


def expect(handler: str, inp: Input, expected: object) -> None:
    entry = next(entry for entry in _state.hooks if entry.handler and entry.handler.__name__ == handler)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(_state, "hooks", [replace(entry, spec=replace(entry.spec, tests={inp: expected}))])
        helpers.run_inline_tests()


def record(env: dict[str, str], commands: dict) -> Input:
    return Input(command=f"cci post --kind incident --text '{THE_CARD}'", env=env, commands=commands)


def card(env: dict[str, str], commands: dict) -> Input:
    return Input(tool=SLACK_CARD, tool_input={"card": {"title": "Incident", "body": THE_CARD}}, env=env, commands=commands)


def dm(env: dict[str, str], commands: dict) -> Input:
    return Input(tool=DM_TOOLS[0], tool_input={"channel_id": "D0B6Z", "thread_ts": "1.2", "text": THE_CARD}, state=ACTIVE, env=env, commands=commands)


@pytest.mark.parametrize("line", [record, card, dm])
def test_a_line_naming_the_ruled_class_warns_with_the_ruling_and_blocks_nothing(tmp_path: Path, line):
    expect("lines_name_a_ruling", line(drive_home(tmp_path), HIT), Warn(pattern=SHOWN))


@pytest.mark.parametrize("line", [record, card, dm])
def test_a_line_no_ruling_matches_says_nothing(tmp_path: Path, line):
    expect("lines_name_a_ruling", line(drive_home(tmp_path), MISS), Allow())


def test_a_matcher_that_does_not_answer_says_nothing(tmp_path: Path):
    expect("lines_name_a_ruling", record(drive_home(tmp_path), DOWN), Allow())


def test_a_communicator_reply_naming_the_ruled_class_warns(tmp_path: Path):
    env = drive_home(tmp_path)
    expect("replies_name_a_ruling", Input(transcript=reply_transcript(THE_CARD), state=ACTIVE, env=env, commands=HIT), Warn(pattern=SHOWN))


def test_the_warning_stays_inside_the_packs_message_bar():
    assert len(RULED) <= 300
