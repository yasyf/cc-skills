from __future__ import annotations

import subprocess
from pathlib import Path

from captain_hook import Allow, And, Annotated, BaseHookEvent, Event, HookResult, Input, Or, Runs, Tool, Warn, on
from captain_hook.util import reqenv

from .cci_mcp_posts import POST_TOOL
from .compaction_handoff import Bound, drive_entry
from .lane_landing import lane_name
from .meta_records import post_records
from .tests.register_fixtures import DRIVE_CONFIG

LR = Path(__file__).parents[2] / "bin" / "lr"
DONE_CHECK_TIMEOUT_SECONDS = 60
DONE_CHECK = f"{LR} done-check"
REFUSAL = "A lane that owns an open PR never posts done, since done retires it for good. Post the blocked record below and wait for each PR to land or close:\n```\nopen: #13\ncci post --lane conflicts-2 --kind blocked --pr 13 --to <your desk> --text '<what lands it>'\n```"
ON_SPRITE = "overdue-sweep-2 runs on a Sprite, so nobody on the Mac can read ~/.vcache/scratch/x.md once the Sprite stops: put each file in cc-notes with `ccn doc add --body-file <path>` and cite the doc id, or attach it to the record with `--path <path>`."
NOT_DONE = "Warning, nothing was blocked: {refusal}"
LANE_ENV = DRIVE_CONFIG | {"CLAUDE_LONG_RUNNING_LANE": "conflicts-2"}


def done_checks(evt: BaseHookEvent) -> list[list[str]]:
    return [
        [lane, f"--text={post.text}", *(f"--attr={key}={value}" for key, value in post.attrs.items())]
        for post in post_records(evt)
        if "done" in post.kind.split(",") and (lane := post.lane or lane_name(evt))
    ]


@on(
    Event.PreToolUse,
    only_if=[Bound(), Or(Tool(POST_TOOL), And(Tool("Bash"), Runs("cci", "post")))],
    skip_if=[Annotated("raw")],
    tests={
        Input(command="cci post --lane conflicts-2 --kind done --to landing-desk --text 'all merged'", env=DRIVE_CONFIG, commands={f"{DONE_CHECK} conflicts-2": REFUSAL}): Warn(pattern=r"A lane that owns an open PR never posts done[\s\S]*open: #13\ncci post --lane conflicts-2 --kind blocked --pr 13 "),
        Input(command="cci post --kind done --to landing-desk --text 'all merged'", env=LANE_ENV, commands={f"{DONE_CHECK} conflicts-2": REFUSAL}): Warn(),
        Input(tool=POST_TOOL, tool_input={"lane": "conflicts-2", "kind": "done", "to": ["landing-desk"], "text": "all merged"}, env=DRIVE_CONFIG, commands={f"{DONE_CHECK} conflicts-2": REFUSAL}): Warn(),
        Input(command="cci post --lane overdue-sweep-2 --kind done --to root --text '~/.vcache/scratch/x.md'", env=DRIVE_CONFIG, commands={f"{DONE_CHECK} overdue-sweep-2 --text=~/.vcache/scratch/x.md": ON_SPRITE}): Warn(pattern=r"runs on a Sprite[\s\S]*`ccn doc add --body-file <path>`"),
        Input(tool=POST_TOOL, tool_input={"lane": "overdue-sweep-2", "kind": "done", "to": ["root"], "text": "unmet", "attrs": {"table": "~/.vcache/scratch/x.md"}}, env=DRIVE_CONFIG, commands={f"{DONE_CHECK} overdue-sweep-2 --text=unmet --attr=table=~/.vcache/scratch/x.md": ON_SPRITE}): Warn(),
        Input(command="cci post --lane conflicts-2 --kind done --to landing-desk --text 'all merged'", env=DRIVE_CONFIG, commands={f"{DONE_CHECK} conflicts-2": ""}): Allow(),
        Input(command="cci post --lane conflicts-2 --kind blocked --pr 13 --to landing-desk --text 'waits on CI'", env=DRIVE_CONFIG, commands={f"{DONE_CHECK} conflicts-2": REFUSAL}): Allow(),
        Input(command="cci post --lane conflicts-2 --kind done --to landing-desk --text 'all merged'  # ccx:raw", env=DRIVE_CONFIG, commands={f"{DONE_CHECK} conflicts-2": REFUSAL}): Allow(),
        Input(command="cci post --lane conflicts-2 --kind done --to landing-desk --text 'all merged'"): Allow(),
    },
)
def done_waits_for_lr_done_check(evt: BaseHookEvent) -> HookResult | None:
    if not (entry := drive_entry(evt)):
        return None
    config = Path(entry["state_dir"]) / "lr.json"
    for check in done_checks(evt):
        argv = [str(LR), "done-check", *check, "--config", str(config)]
        if refusal := subprocess.run(argv, capture_output=True, text=True, timeout=DONE_CHECK_TIMEOUT_SECONDS, env=dict(reqenv.env_map()), check=True).stdout.strip():
            return evt.context(NOT_DONE.format(refusal=refusal))
    return None
