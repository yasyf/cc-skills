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
CARRY_BACK = f"{LR} carry-back"
CARRIED = "/home/sprite/drive/carried/"
REFUSAL = "A lane that owns an open PR never posts done, since done retires it for good. Post the blocked record below and wait for each PR to land or close:\n```\nopen: #13\ncci post --lane conflicts-2 --kind blocked --pr 13 --to <your desk> --text '<what lands it>'\n```"
ON_SPRITE = "overdue-sweep-2 runs on a Sprite, so nobody on the Mac can read ~/.vcache/scratch/x.md once the Sprite stops: put each file in cc-notes with `ccn doc add --body-file <path>` and cite the doc id, or attach it to the record with `--path <path>`."
LEFT_ON_SPRITE = "overdue-picks-h-1 runs on a Sprite and the Mac did not get /home/sprite/drive/carried/Users/me/.claude/scratch/overdue-picks-h (its copy from Sprite pool-1 failed): put each file in cc-notes with `ccn doc add --body-file <path>` and cite the doc id, or attach it to the record with `--path <path>`."
REPORTED = "22 picks judged; wrote root-posts.zsh (on this Sprite at /home/sprite/drive/carried/Users/me/.claude/scratch/overdue-picks-h/)"
NOT_DONE = "Warning, nothing was blocked: {refusal}"
LANE_ENV = DRIVE_CONFIG | {"CLAUDE_LONG_RUNNING_LANE": "conflicts-2"}


def record_checks(evt: BaseHookEvent) -> list[list[str]]:
    checks = []
    for post in post_records(evt):
        if not (lane := post.lane or lane_name(evt)):
            continue
        said = [f"--text={post.text}", *(f"--attr={key}={value}" for key, value in post.attrs.items())]
        if any(CARRIED in words for words in said):
            checks.append(["carry-back", lane, *said])
        if "done" in post.kind.split(","):
            checks.append(["done-check", lane, *said])
    return checks


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
        Input(tool=POST_TOOL, tool_input={"lane": "overdue-picks-h-1", "kind": "report", "to": ["root"], "text": REPORTED}, env=DRIVE_CONFIG, commands={f"{CARRY_BACK} overdue-picks-h-1": LEFT_ON_SPRITE}): Warn(pattern=r"the Mac did not get /home/sprite/drive/carried/[\s\S]*`ccn doc add --body-file <path>`"),
        Input(command=f"cci post --lane overdue-picks-h-1 --kind done --to root --text '{REPORTED}'", env=DRIVE_CONFIG, commands={f"{CARRY_BACK} overdue-picks-h-1": "", f"{DONE_CHECK} overdue-picks-h-1": REFUSAL}): Warn(pattern=r"open PR"),
        Input(tool=POST_TOOL, tool_input={"lane": "overdue-picks-h-1", "kind": "report", "to": ["root"], "text": REPORTED}, env=DRIVE_CONFIG, commands={f"{CARRY_BACK} overdue-picks-h-1": ""}): Allow(),
        Input(command="cci post --lane conflicts-2 --kind done --to landing-desk --text 'all merged'", env=DRIVE_CONFIG, commands={f"{DONE_CHECK} conflicts-2": ""}): Allow(),
        Input(command="cci post --lane conflicts-2 --kind blocked --pr 13 --to landing-desk --text 'waits on CI'", env=DRIVE_CONFIG, commands={f"{DONE_CHECK} conflicts-2": REFUSAL}): Allow(),
        Input(command="cci post --lane conflicts-2 --kind done --to landing-desk --text 'all merged'  # ccx:raw", env=DRIVE_CONFIG, commands={f"{DONE_CHECK} conflicts-2": REFUSAL}): Allow(),
        Input(command="cci post --lane conflicts-2 --kind done --to landing-desk --text 'all merged'"): Allow(),
    },
)
def records_pass_lr_record_checks(evt: BaseHookEvent) -> HookResult | None:
    if not (entry := drive_entry(evt)):
        return None
    config = Path(entry["state_dir"]) / "lr.json"
    for check in record_checks(evt):
        argv = [str(LR), *check, "--config", str(config)]
        if refusal := subprocess.run(argv, capture_output=True, text=True, timeout=DONE_CHECK_TIMEOUT_SECONDS, env=dict(reqenv.env_map()), check=True).stdout.strip():
            return evt.context(NOT_DONE.format(refusal=refusal))
    return None
