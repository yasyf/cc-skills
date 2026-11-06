from __future__ import annotations

import subprocess
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Block, Event, HookResult, Input, Tool, Warn, on
from captain_hook.util import reqenv

from .call_dir import run_dir, unknown_dir
from .compaction_handoff import Bound, CompactionState, bus_name, drive_entry
from .lane_landing import DRIVE_ENV, flagged_repos, landing_verb, lane_name, merge_target
from .tests.merge_fixtures import BASE_UNREAD, BROADCAST, INHERITED, OWN, PENDING, UNREAD, own_red

BOUND = [CompactionState(active=True, coordinator=True)]

LR = Path(__file__).parents[2] / "bin" / "lr"
MERGE_SETS = {"gh pr merge": (), "ccx vcs merge": ("--below",), "gh stack merge": ("--stack",)}
LR_TIMEOUT_SECONDS = 180
OWN_LINE = "OWN-RED"
BASE_UNREAD_LINE = "BASE-UNREAD"
UNFOLLOWED = "`{verb}` runs after a `cd` this hook cannot follow, so nothing read its PR's red checks. Run `gh pr checks` on that PR."
UNREAD_REPO = "`{verb}` names a repo this hook cannot read, so nothing read its PR's red checks. Run `gh pr checks` on that PR."
UNREAD_LINE = "UNREAD"
MAIL_LINE = "MAIL"
LANE_ENV = DRIVE_ENV | {"CLAUDE_LONG_RUNNING_LANE": "fix-lane-1"}
LANE_READ = ("--drive", "release-v3", "--lane", "fix-lane-1")
UNREAD_MAIL = (
    "A lane merges only once it has read its records, and these reached `{lane}` unread. This refusal is that read and its cursor has moved past them, "
    "so act on each one, and merge again only if they still allow it:\n```\n{records}\n```"
)
MAIL = "The merge read these records for `{lane}` on its way:\n```\n{records}\n```"
OWN_RED = (
    "A merge never lands past a check its own change turned red, meaning a test failing on its PR and not on its base, or a check green on its base when its log names no test:\n```\n{findings}\n```\n"
    "Fix and push, or rerun a possible flake with `gh run rerun --job <number after /job/>`, then merge once it passes."
)
INHERITED_RED = "{checks} fail the same tests, or else the same check, on the base branch at their PR's merge base, so they are inherited and the merge goes ahead."
BASE_UNREAD_RED = "{checks} are red and the base branch's state could not be read, so nothing says whether their PR caused them. The merge goes ahead; read the same check on the base."
OWN_FINDING = r"own change turned red.*\n```\n#5 lr-go https://github\.com/acme/app/actions/runs/1/job/2\n```\n.*`gh run rerun --job "
INHERITED_NOTE = r"^`#5 lr-go` fail the same tests, or else the same check, on the base branch at their PR's merge base, so they are inherited"
BASE_UNREAD_NOTE = r"^`#5 lr-go` are red and the base branch's state could not be read"
UNREAD_RELAY = r"reached `fix-lane-1` unread.*\n```\n#360037 meta-desk-2 relay: hold #2188, do not merge\n#360040 root note: dev is green"
BROADCAST_NOTE = r"^The merge read these records for `fix-lane-1` on its way:\n```\n#360040 root note: dev is green"


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash")],
    tests={
        Input(command="gh pr merge 5 --squash --admin", commands=own_red("5", said=OWN), state=BOUND): Block(pattern=OWN_FINDING),
        Input(command="gh pr merge 5 --squash", commands=own_red("5", said=OWN), state=BOUND): Block(pattern=OWN_FINDING),
        Input(command="gh -R acme/app pr merge 5 --squash", commands=own_red("--repo", "acme/app", "5", said=OWN), state=BOUND): Block(pattern=OWN_FINDING),
        Input(command="gh stack merge 5 --yes --squash", commands=own_red("--stack", "5", said=OWN), state=BOUND): Block(pattern=OWN_FINDING),
        Input(command="gh stack merge --merge-method squash 5", commands=own_red("--stack", "5", said=OWN), state=BOUND): Block(pattern=OWN_FINDING),
        Input(command="ccx vcs merge 5 -R acme/app", commands=own_red("--below", "--repo", "acme/app", "5", said=OWN), state=BOUND): Block(pattern=OWN_FINDING),
        Input(command="gh pr merge 5 --squash --admin", commands=own_red("5", said=INHERITED), state=BOUND): Warn(pattern=INHERITED_NOTE),
        Input(command="gh pr merge 5 --squash", commands=own_red("5", said=INHERITED), state=BOUND): Warn(pattern=INHERITED_NOTE),
        Input(command="gh stack merge 5 --yes --squash", commands=own_red("--stack", "5", said=INHERITED), state=BOUND): Warn(pattern=INHERITED_NOTE),
        Input(command="ccx vcs merge 5", commands=own_red("--below", "5", said=INHERITED), state=BOUND): Warn(pattern=INHERITED_NOTE),
        Input(command="gh pr merge 5 --squash --admin", commands=own_red("5", said=BASE_UNREAD), state=BOUND): Warn(pattern=BASE_UNREAD_NOTE),
        Input(command="gh pr merge 5 --squash --admin", commands=own_red("5", said=PENDING), state=BOUND): Allow(),
        Input(command="gh pr merge --squash", commands=own_red(said=PENDING), state=BOUND): Allow(),
        Input(command="gh stack merge --yes --squash", commands=own_red("--stack", said=PENDING), state=BOUND): Allow(),
        Input(command="ccx vcs merge", commands=own_red("--below", said=PENDING), state=BOUND): Allow(),
        Input(command="gh pr merge 5 --squash --admin", env=LANE_ENV, commands=own_red(*LANE_READ, "5", said=UNREAD)): Block(pattern=UNREAD_RELAY),
        Input(command="gh stack merge 5 --yes --squash", env=LANE_ENV, commands=own_red("--stack", *LANE_READ, "5", said=UNREAD)): Block(pattern=UNREAD_RELAY),
        Input(command="ccx vcs merge 5", env=LANE_ENV, commands=own_red("--below", *LANE_READ, "5", said=UNREAD)): Block(pattern=UNREAD_RELAY),
        Input(command="gh pr merge 5 --squash --admin", env=LANE_ENV, commands=own_red(*LANE_READ, "5", said=BROADCAST)): Warn(pattern=BROADCAST_NOTE),
        Input(command="gh pr merge 5 --squash --admin", env=LANE_ENV, commands=own_red(*LANE_READ, "5", said=PENDING)): Allow(),
        Input(command="gh pr merge 5 --disable-auto", commands=own_red("5", said=OWN), state=BOUND): Allow(),
        Input(command=". /env.sh; cd $A; gh pr merge 5 --squash --admin", cwd="/", commands=own_red("5", said=OWN), state=BOUND): Warn(pattern=r"`cd` this hook cannot follow"),
        Input(command="cd /; gh pr merge 5 --squash --admin", cwd="/", commands=own_red("5", said=OWN), state=BOUND): Block(),
        Input(command="gh pr view 5 --json url", commands=own_red("5", said=OWN), state=BOUND): Allow(),
        Input(command='for r in acme/app; do gh pr merge 5 -R "$r" --squash; done', commands=own_red("--repo", "acme/app", "5", said=OWN), state=BOUND): Block(pattern=OWN_FINDING),
        Input(
            command='for r in acme/lib acme/app; do gh pr merge 5 --repo="$r" --squash; done',
            commands={**own_red("--repo", "acme/lib", "5", said=PENDING), **own_red("--repo", "acme/app", "5", said=OWN)},
            state=BOUND,
        ): Block(pattern=OWN_FINDING),
        Input(command='for r in acme/app; do gh pr merge 5 -R "$r" --squash; done', commands=own_red("--repo", "acme/app", "5", said=PENDING), state=BOUND): Allow(),
        Input(command='gh pr merge "$2" -R "acme/$1" --squash', commands=own_red("5", said=OWN), state=BOUND): Warn(pattern=r"^`gh pr merge` names a repo this hook cannot read"),
    },
)
def merge_never_lands_past_its_own_red(evt: BaseHookEvent) -> HookResult | None:
    own: list[str] = []
    inherited: list[str] = []
    base_unread: list[str] = []
    unread: list[str] = []
    mail: list[str] = []
    unfollowed: list[str] = []
    lane = lane_name(evt)
    read = ("--drive", bus_name(entry), "--lane", lane) if lane and (entry := drive_entry(evt)) else ()
    for call in evt.command.calls():
        if (merge_set := MERGE_SETS.get(landing_verb(call) or "")) is None or "--disable-auto" in call.args:
            continue
        if unknown_dir(evt, call):
            unfollowed.append(UNFOLLOWED.format(verb=landing_verb(call)))
            continue
        if (repos := flagged_repos(call)) is None:
            unfollowed.append(UNREAD_REPO.format(verb=landing_verb(call)))
            continue
        target, _ = merge_target(call)
        for repo in repos or (None,):
            argv = [str(LR), "own-red", *merge_set, *(("--repo", repo) if repo else ()), *read, *((target,) if target else ())]
            said = subprocess.run(argv, capture_output=True, text=True, timeout=LR_TIMEOUT_SECONDS, cwd=run_dir(evt, call), env=dict(reqenv.env_map()), check=True).stdout
            for line in said.splitlines():
                kind, _, finding = line.partition(" ")
                {OWN_LINE: own, BASE_UNREAD_LINE: base_unread, UNREAD_LINE: unread, MAIL_LINE: mail}.get(kind, inherited).append(finding)
    if unread:
        return evt.block(UNREAD_MAIL.format(lane=lane, records="\n".join(unread + mail)))
    if own:
        return evt.block(OWN_RED.format(findings="\n".join(own)))
    notes = [*unfollowed, *([MAIL.format(lane=lane, records="\n".join(mail))] if mail else [])]
    notes += [INHERITED_RED.format(checks=", ".join(f"`{check}`" for check in inherited))] if inherited else []
    notes += [BASE_UNREAD_RED.format(checks=", ".join(f"`{check}`" for check in base_unread))] if base_unread else []
    return evt.context("\n\n".join(notes)) if notes else None
