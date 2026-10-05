from __future__ import annotations

import json
import sys

from captain_hook import Allow, BaseHookEvent, Event, FromSubagent, HookResult, Input, Warn, on
from captain_hook.util import reqenv

from .compaction_handoff import RULINGS, CompactionState, register_context, register_of

DRIVE_ENV = "CLAUDE_LONG_RUNNING_DRIVE"
REGISTER = {"id": "c" * 40, "body": "# Register\n\n1. Pulumi state is the only truth.\n"}
FOUND = {f"{sys.executable} {RULINGS} register": json.dumps(REGISTER)}
NONE_FOUND = {f"{sys.executable} {RULINGS} register": "null"}
ACTIVE = CompactionState(active=True, slug="brook")


def drive_args(evt: BaseHookEvent) -> list[str] | None:
    if (drive := CompactionState.load(evt)).active and drive.slug:
        return ["--program", drive.slug]
    if worker := reqenv.getenv(DRIVE_ENV):
        return ["--drive", worker]
    return None


@on(
    Event.SubagentStart,
    skip_planning_agents=False,
    tests={
        Input(agent_type="general-purpose", agent_id="a1b2c3", commands=FOUND, state=[ACTIVE]): Warn(
            pattern=r"^Standing rules register `ccccccc`, verbatim; it binds this session and every lane brief, and outranks any summary\.\n"
            r"~{12}\n# Register\n\n1\. Pulumi state is the only truth\.\n~{12}$"
        ),
        Input(agent_type="general-purpose", agent_id="a1b2c3", commands=NONE_FOUND, state=[ACTIVE]): Allow(),
        Input(agent_type="general-purpose", agent_id="a1b2c3"): Allow(),
    },
)
def brief_subagent(evt: BaseHookEvent) -> HookResult | None:
    if not (which := drive_args(evt)):
        return None
    register = register_of(str(evt.cwd), *which)
    return register_context(evt, register) if register else None


@on(
    Event.SessionStart,
    skip_if=[FromSubagent()],
    tests={
        Input(source="startup", env={DRIVE_ENV: "900424b6"}, commands=FOUND): Warn(pattern=r"^Standing rules register `ccccccc`"),
        Input(source="startup", env={DRIVE_ENV: "900424b6"}, commands=NONE_FOUND): Allow(),
        Input(source="startup"): Allow(),
        Input(source="compact", env={DRIVE_ENV: "900424b6"}, commands=FOUND, state=[ACTIVE]): Allow(),
    },
)
def brief_drive_worker(evt: BaseHookEvent) -> HookResult | None:
    if CompactionState.load(evt).active or not (worker := reqenv.getenv(DRIVE_ENV)):
        return None
    register = register_of(str(evt.cwd), "--drive", worker)
    return register_context(evt, register) if register else None
