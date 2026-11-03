from __future__ import annotations

import subprocess
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Block, Event, HookResult, Input, Tool, Warn, on
from captain_hook.util import reqenv

from .lane_landing import landing_verb, merge_target, repo_flag
from .tests.merge_fixtures import INHERITED, OWN, PENDING, own_red

LR = Path(__file__).parents[2] / "bin" / "lr"
MERGE_SETS = {"gh pr merge": (), "ccx vcs merge": ("--below",), "gh stack merge": ("--stack",)}
LR_TIMEOUT_SECONDS = 180
OWN_LINE = "OWN-RED"
OWN_RED = (
    "A merge never lands past a check its own change turned red. Fix each finding below and push, then merge once those checks pass:\n"
    "```\n{findings}\n```"
)
INHERITED_RED = "{checks} fail only on files outside their PR's diff, so they are inherited from the base branch and the merge goes ahead."
OWN_FINDING = r"own change turned red.*\n```\n#5 lr-go lr/internal/lanes/sources\.go:336 Consider preallocating"
INHERITED_NOTE = r"^`#5 lr-go` fail only on files outside their PR's diff, so they are inherited"


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash")],
    tests={
        Input(command="gh pr merge 5 --squash --admin", commands=own_red("5", said=OWN)): Block(pattern=OWN_FINDING),
        Input(command="gh pr merge 5 --squash", commands=own_red("5", said=OWN)): Block(pattern=OWN_FINDING),
        Input(command="gh -R acme/app pr merge 5 --squash", commands=own_red("--repo", "acme/app", "5", said=OWN)): Block(pattern=OWN_FINDING),
        Input(command="gh stack merge 5 --yes --squash", commands=own_red("--stack", "5", said=OWN)): Block(pattern=OWN_FINDING),
        Input(command="gh stack merge --merge-method squash 5", commands=own_red("--stack", "5", said=OWN)): Block(pattern=OWN_FINDING),
        Input(command="ccx vcs merge 5 -R acme/app", commands=own_red("--below", "--repo", "acme/app", "5", said=OWN)): Block(pattern=OWN_FINDING),
        Input(command="gh pr merge 5 --squash --admin", commands=own_red("5", said=INHERITED)): Warn(pattern=INHERITED_NOTE),
        Input(command="gh pr merge 5 --squash", commands=own_red("5", said=INHERITED)): Warn(pattern=INHERITED_NOTE),
        Input(command="gh stack merge 5 --yes --squash", commands=own_red("--stack", "5", said=INHERITED)): Warn(pattern=INHERITED_NOTE),
        Input(command="ccx vcs merge 5", commands=own_red("--below", "5", said=INHERITED)): Warn(pattern=INHERITED_NOTE),
        Input(command="gh pr merge 5 --squash --admin", commands=own_red("5", said=PENDING)): Allow(),
        Input(command="gh pr merge --squash", commands=own_red(said=PENDING)): Allow(),
        Input(command="gh stack merge --yes --squash", commands=own_red("--stack", said=PENDING)): Allow(),
        Input(command="ccx vcs merge", commands=own_red("--below", said=PENDING)): Allow(),
        Input(command="gh pr merge 5 --disable-auto", commands=own_red("5", said=OWN)): Allow(),
        Input(command="gh pr view 5 --json url", commands=own_red("5", said=OWN)): Allow(),
    },
)
def merge_never_lands_past_its_own_red(evt: BaseHookEvent) -> HookResult | None:
    own: list[str] = []
    inherited: list[str] = []
    for call in evt.command.calls():
        if (merge_set := MERGE_SETS.get(landing_verb(call) or "")) is None or "--disable-auto" in call.args:
            continue
        target, _ = merge_target(call)
        repo = repo_flag(call)
        argv = [str(LR), "own-red", *merge_set, *(("--repo", repo) if repo else ()), *((target,) if target else ())]
        said = subprocess.run(argv, capture_output=True, text=True, timeout=LR_TIMEOUT_SECONDS, cwd=str(call.cwd or evt.cwd), env=dict(reqenv.env_map()), check=True).stdout
        for line in said.splitlines():
            kind, _, finding = line.partition(" ")
            (own if kind == OWN_LINE else inherited).append(finding)
    if own:
        return evt.block(OWN_RED.format(findings="\n".join(own)))
    return evt.context(INHERITED_RED.format(checks=", ".join(f"`{check}`" for check in inherited))) if inherited else None
