from __future__ import annotations

import subprocess
from pathlib import Path

from captain_hook import Allow, Annotated, BaseHookEvent, Event, HookResult, Input, Runs, Tool, Warn, on
from captain_hook.util import reqenv

from .compaction_handoff import Bound, drive_entry
from .lane_landing import lane_name
from .owner_facing import post_values
from .tests.register_fixtures import DRIVE_CONFIG

LR = Path(__file__).parents[2] / "bin" / "lr"
DONE_CHECK_TIMEOUT_SECONDS = 60
DONE_CHECK = f"{LR} done-check"
REFUSAL = "A lane that owns an open PR never posts done, since done retires it for good. Post the blocked record below and wait for each PR to land or close:\n```\nopen: #13\ncci post --lane conflicts-2 --kind blocked --pr 13 --to <your desk> --text '<what lands it>'\n```"
OPEN_PR = "Warning, nothing was blocked: {refusal}"
LANE_ENV = DRIVE_CONFIG | {"CLAUDE_LONG_RUNNING_LANE": "conflicts-2"}


def done_lanes(evt: BaseHookEvent) -> list[str]:
    posts = [values for call in evt.command.calls("cci") if (values := post_values(evt, call)).get("words", ())[:1] == ("post",) and "done" in values.get("kind", ())]
    return [lane for values in posts if (lane := str((values.get("lane") or (lane_name(evt),))[0] or ""))]


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash"), Runs("cci", "post")],
    skip_if=[Annotated("raw")],
    tests={
        Input(command="cci post --lane conflicts-2 --kind done --to landing-desk --text 'all merged'", env=DRIVE_CONFIG, commands={f"{DONE_CHECK} conflicts-2": REFUSAL}): Warn(pattern=r"A lane that owns an open PR never posts done[\s\S]*open: #13\ncci post --lane conflicts-2 --kind blocked --pr 13 "),
        Input(command="cci post --kind done --to landing-desk --text 'all merged'", env=LANE_ENV, commands={f"{DONE_CHECK} conflicts-2": REFUSAL}): Warn(),
        Input(command="cci post --lane conflicts-2 --kind done --to landing-desk --text 'all merged'", env=DRIVE_CONFIG, commands={f"{DONE_CHECK} conflicts-2": ""}): Allow(),
        Input(command="cci post --lane conflicts-2 --kind blocked --pr 13 --to landing-desk --text 'waits on CI'", env=DRIVE_CONFIG, commands={f"{DONE_CHECK} conflicts-2": REFUSAL}): Allow(),
        Input(command="cci post --lane conflicts-2 --kind done --to landing-desk --text 'all merged'  # ccx:raw", env=DRIVE_CONFIG, commands={f"{DONE_CHECK} conflicts-2": REFUSAL}): Allow(),
        Input(command="cci post --lane conflicts-2 --kind done --to landing-desk --text 'all merged'"): Allow(),
    },
)
def done_waits_for_the_lanes_open_prs(evt: BaseHookEvent) -> HookResult | None:
    if not (entry := drive_entry(evt)):
        return None
    config = Path(entry["state_dir"]) / "lr.json"
    for lane in done_lanes(evt):
        argv = [str(LR), "done-check", lane, "--config", str(config)]
        if refusal := subprocess.run(argv, capture_output=True, text=True, timeout=DONE_CHECK_TIMEOUT_SECONDS, env=dict(reqenv.env_map()), check=True).stdout.strip():
            return evt.context(OPEN_PR.format(refusal=refusal))
    return None
