from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from captain_hook import Allow, Annotated, BaseHookEvent, Block, Event, FromSubagent, HookResult, Input, TaskCall, Tool, Warn, on
from captain_hook.util import reqenv

from .compaction_handoff import CompactionState, drive_entry, failure_of, post_blocker, register_context, register_of, rulings, script, script_module
from .nudges import once_per_hour
from .tests.register_fixtures import DRIVE_CONFIG

DRIVE_ENV = "CLAUDE_LONG_RUNNING_DRIVE"
HELPER_AGENTS = frozenset({"Explore", "Plan", "claude-code-guide", "statusline-setup", "output-style-setup", "ship-pr"})
HELPER_PLUGINS = ("cc-context:", "cc-review:", "cc-slack:", "cc-present:", "codex:", "open-pr:")
INCIDENT_NAME = re.compile(r"^(?:incident|outage)-")
INCIDENT_BRIEF = re.compile(r"\b(?:incident|outage)\b", re.IGNORECASE)
SLICE_HEADER = re.compile(r"^Register slice: lane=(\S+) step=\S+", re.MULTILINE)
INCIDENT_SLICE = "Register slice: lane={lane} step=incident"
BRIEF_FILE = re.compile(r"(?<![\w.~/-])[~/][\w./~-]*\.md\b")
LANE_FLAGS = ("--lane", "-l")
FRESH = "fresh "
UNREAD = (OSError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired)
NO_SLICE = (
    "Warning, nothing was blocked: this drive lane's brief has no `Register slice: lane=<name> step=<n>` line."
    " Add one and run `rulings.py slice --refresh {brief}` for the next spawn."
)
STALE_SLICE = "Warning, nothing was blocked: this spawn's register slice is stale or empty. Run `rulings.py slice --refresh {brief}` for the next spawn."
INCIDENT_NOTE = "The incident spawn's prompt now carries its register slice and the unclassified owner captures."
BRIEFS = Path(__file__).parent / "tests" / "fixtures" / "briefs"
SLICE = f"{sys.executable} {script('rulings.py')} slice"
FRESH_SLICE = {SLICE: "fresh /tmp/brief.md slice-hash 0123456789ab\n"}
STALE = {SLICE: "refreshed /tmp/brief.md slice-hash 0123456789ab -> 89abcdef0123\n"}
SLICED = "Lane lane-a: build the relay.\nScale: chats on one Mac, up to 1,000.\nRegister slice: lane=lane-a step=1.9\nslice-hash: 0123456789ab\n"
REGISTER = {"id": "c" * 40, "body": "# Register\n\n1. Pulumi state is the only truth.\n"}
FOUND = {f"{sys.executable} {script('rulings.py')} register": json.dumps(REGISTER)}
NONE_FOUND = {f"{sys.executable} {script('rulings.py')} register": "null"}
ACTIVE = CompactionState(active=True, slug="brook")
lane_brief = script_module("lane_brief")


def drive_args(evt: BaseHookEvent) -> list[str] | None:
    if CompactionState.load(evt).active and (entry := drive_entry(evt)):
        return ["--drive", entry["drive"]]
    if worker := reqenv.getenv(DRIVE_ENV):
        return ["--drive", worker]
    return None


def prose(call: TaskCall) -> list[str]:
    lines = (line.strip() for line in (call.raw.get("prompt") or "").splitlines())
    return [line for line in lines if line and not line.startswith("ccx:")]


def incident_spawn(call: TaskCall, lines: list[str]) -> bool:
    return bool(INCIDENT_NAME.match(call.agent_name or "") or any(INCIDENT_BRIEF.search(line) for line in lines))


def helper(agent_type: str | None) -> bool:
    return agent_type in HELPER_AGENTS or (agent_type or "").startswith(HELPER_PLUGINS)


@dataclass
class Spawn:
    lane: str
    incident: bool
    text: str
    brief: Path | None
    inline: bool
    prompt: str = ""

    @property
    def shown(self) -> str:
        return str(self.brief) if self.brief else "<brief>"

    def owes_scale(self) -> bool:
        return lane_brief.owes_scale(self.lane, f"{self.prompt}\n{self.text}", None if self.inline else self.brief)


def agent_spawn(call: TaskCall) -> Spawn | None:
    if helper(call.agent_type):
        return None
    prompt = call.raw.get("prompt") or ""
    incident = incident_spawn(call, prose(call)[:1])
    named = [path for found in BRIEF_FILE.findall(prompt) if os.path.isfile(path := Path(found).expanduser())]
    holder = next((path for path in named if SLICE_HEADER.search(path.read_text())), None)
    if SLICE_HEADER.search(prompt) or not holder:
        text, brief, inline = prompt, holder or next(iter(named), None), True
    else:
        text, brief, inline = holder.read_text(), holder, False
    sliced = SLICE_HEADER.search(text)
    return Spawn(call.agent_name or (sliced[1] if sliced else "lane"), incident, text, brief, inline, prompt)


def launch_spawn(evt: BaseHookEvent) -> Spawn | None:
    for call in evt.command.calls():
        name, args = Path(call.name).name, list(call.args)
        if not args or not os.path.isfile(brief := Path(args[-1]).expanduser()):
            continue
        if name == "orca-launch.sh" and len(args) >= 4:
            return Spawn(args[0], args[1] == "incident", brief.read_text(), brief, inline=False)
        if name == "codex-ask" and (flag := next((flag for flag in LANE_FLAGS if flag in args[:-1]), None)):
            return Spawn(args[args.index(flag) + 1].rsplit("/", 1)[-1], "--incident" in args, brief.read_text(), brief, inline=False)
    return None


def why(failed: BaseException) -> str:
    return failure_of(failed) if isinstance(failed, (subprocess.CalledProcessError, subprocess.TimeoutExpired)) else str(failed)


def refreshed(evt: BaseHookEvent, which: list[str], spawn: Spawn) -> tuple[str, str]:
    text = spawn.text if SLICE_HEADER.search(spawn.text) else f"{spawn.text.rstrip()}\n\n{INCIDENT_SLICE.format(lane=spawn.lane)}\n"
    with tempfile.TemporaryDirectory() as staging:
        copy = Path(staging) / "brief.md"
        copy.write_text(text)
        said = rulings(str(evt.cwd), "slice", "--refresh", str(copy), *which)
        return said, copy.read_text()


def spawn(name: str, prompt: str, agent_type: str = "long-running:lane", **extra: object) -> Input:
    return Input(tool="Agent", tool_input={"name": name, "prompt": prompt, "subagent_type": agent_type}, **({"state": [ACTIVE], "env": DRIVE_CONFIG} | extra))


@on(
    Event.PreToolUse,
    only_if=[Tool("Agent", "Bash")],
    skip_if=[Annotated("raw")],
    tests={
        spawn("lane-a", "Lane lane-a: build the relay."): Warn(pattern=r"has no `Register slice: lane=<name> step=<n>` line"),
        spawn("lane-a", SLICED, commands=FRESH_SLICE): Allow(),
        spawn("lane-a", SLICED.replace("Scale: chats on one Mac, up to 1,000.\n", "")): Block(pattern=r"has no `Scale:` line, so this new lane's spawn is refused\. Add one line to it: `Scale: no N`"),
        spawn("lane-a-handoff", SLICED.replace("Scale: chats on one Mac, up to 1,000.\n", ""), commands=FRESH_SLICE): Allow(),
        spawn("lane-a-2", SLICED.replace("Scale: chats on one Mac, up to 1,000.\n", ""), commands=FRESH_SLICE): Allow(),
        spawn("lane-b", "Replaces: lane-a\n" + SLICED.replace("Scale: chats on one Mac, up to 1,000.\n", ""), commands=FRESH_SLICE): Allow(),
        spawn("lane-a", SLICED, commands=STALE): Warn(pattern=r"stale or empty\. Run `rulings\.py slice --refresh <brief>`"),
        spawn("lane-a", f"Your brief is {BRIEFS / 'sliced.md'}; read it.", commands=FRESH_SLICE): Allow(),
        spawn("lane-a", f"Your brief is {BRIEFS / 'unsliced.md'}; read it."): Warn(pattern=r"--refresh \S*unsliced\.md"),
        spawn("lane-a", "Find every caller of drain.", agent_type="Explore"): Allow(),
        spawn("lane-a", "Open the PR.", agent_type="ship-pr"): Allow(),
        spawn("lane-a", "Watch the PR.", agent_type="open-pr:pr-watcher"): Allow(),
        spawn("lane-a-sub", "Lane lane-a-sub: build the relay's tests.", agent_id="a1b2c3"): Warn(pattern=r"has no `Register slice: lane=<name> step=<n>` line"),
        spawn("lane-a-sub", SLICED, agent_id="a1b2c3", commands=FRESH_SLICE): Allow(),
        spawn("lane-a-sub", "Lane lane-a-sub: build the relay's tests.", state=[], env={DRIVE_ENV: "brook-drive"}): Warn(pattern=r"has no `Register slice:"),
        Input(tool="Agent", tool_input={"name": "lane-a", "prompt": "Lane lane-a: build the relay."}): Allow(),
        Input(command=f"orca-launch.sh lane-a sol xhigh {BRIEFS / 'unsliced.md'}", state=[ACTIVE], env=DRIVE_CONFIG): Warn(pattern=r"unsliced\.md"),
        Input(command=f"orca-launch.sh lane-a sol xhigh {BRIEFS / 'sliced.md'}", state=[ACTIVE], env=DRIVE_CONFIG, commands=FRESH_SLICE): Allow(),
        Input(command=f"orca-launch.sh lane-a-sub sol xhigh {BRIEFS / 'unsliced.md'}", agent_id="a1b2c3", state=[ACTIVE], env=DRIVE_CONFIG): Warn(pattern=r"unsliced\.md"),
        Input(command=f"orca-launch.sh lane-a-sub sol xhigh {BRIEFS / 'unsliced.md'}", env={DRIVE_ENV: "brook-drive"}): Warn(pattern=r"unsliced\.md"),
        Input(command=f"codex-ask --dispatch --lane lane-a {BRIEFS / 'unsliced.md'}", state=[ACTIVE], env=DRIVE_CONFIG): Warn(pattern=r"unsliced\.md"),
        Input(command=f"codex-ask --dispatch --lane lane-a-sub {BRIEFS / 'unsliced.md'}", agent_id="a1b2c3", state=[ACTIVE], env=DRIVE_CONFIG): Warn(pattern=r"unsliced\.md"),
        Input(command=f"codex-ask -s /tmp/finder {BRIEFS / 'unsliced.md'}", state=[ACTIVE], env=DRIVE_CONFIG): Allow(),
        Input(command=f"codex-ask --dispatch --lane lane-a '{'inline brief ' * 40}'", state=[ACTIVE], env=DRIVE_CONFIG): Allow(),
        spawn("lane-a", f"Lane lane-a: read /{'x' * 300}.md. {SLICED}", commands=FRESH_SLICE): Allow(),
    },
)
def slice_on_spawn(evt: BaseHookEvent) -> HookResult | None:
    if not (which := drive_args(evt)):
        return None
    try:
        found = agent_spawn(evt.as_input(TaskCall)) if evt.tool_name == "Agent" else launch_spawn(evt)
    except UNREAD as failed:
        post_blocker(evt, f"slice_on_spawn: the spawn's brief did not read: {why(failed)}", key="slice-on-spawn:brief")
        return None
    if not found:
        return None
    if not found.incident and not SLICE_HEADER.search(found.text):
        return evt.context(NO_SLICE.format(brief=found.shown)) if once_per_hour(evt, [NO_SLICE]) else None
    if not found.incident and found.owes_scale():
        return evt.block(lane_brief.no_scale(found.shown))
    try:
        said, fresh = refreshed(evt, which, found)
        if found.incident and not found.inline:
            found.brief.write_text(fresh)
    except UNREAD as failed:
        post_blocker(evt, f"slice_on_spawn: the register slice for {found.lane} did not read: {why(failed)}", key=f"slice-on-spawn:{found.lane}")
        return None
    if not found.incident:
        return None if said.startswith(FRESH) or not once_per_hour(evt, [STALE_SLICE]) else evt.context(STALE_SLICE.format(brief=found.shown))
    return evt.rewrite(evt._tool_input | {"prompt": fresh}, note=INCIDENT_NOTE) if found.inline else None


@on(
    Event.SubagentStart,
    skip_planning_agents=False,
    tests={
        Input(agent_type="general-purpose", agent_id="a1b2c3", commands=FOUND, env=DRIVE_CONFIG, state=[ACTIVE]): Warn(
            pattern=r"^Standing rules register `ccccccc`, verbatim; it binds this session and every lane brief, and outranks any summary\.\n"
            r"~{12}\n# Register\n\n1\. Pulumi state is the only truth\.\n~{12}$"
        ),
        Input(agent_type="general-purpose", agent_id="a1b2c3", commands=NONE_FOUND, env=DRIVE_CONFIG, state=[ACTIVE]): Allow(),
        Input(agent_type="Explore", agent_id="a1b2c3", commands=FOUND, env=DRIVE_CONFIG, state=[ACTIVE]): Warn(pattern=r"^Standing rules register `ccccccc`"),
        Input(agent_type="codex:codex-wrapper", agent_id="a1b2c3", commands=FOUND, env=DRIVE_CONFIG, state=[ACTIVE]): Warn(pattern=r"^Standing rules register `ccccccc`"),
        Input(agent_type="Explore", agent_id="a1b2c3", commands=NONE_FOUND, env=DRIVE_CONFIG, state=[ACTIVE]): Allow(),
        Input(agent_type="long-running:lane", agent_id="a1b2c3", commands=FOUND, env=DRIVE_CONFIG, state=[ACTIVE]): Warn(pattern=r"^Standing rules register `ccccccc`"),
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
