from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from captain_hook import Allow, Annotated, BaseHookEvent, Event, FromSubagent, HookResult, Input, Rewrite, TaskCall, Tool, Warn, WorkflowState, on
from cc_transcript import UserEvent
from captain_hook.util import reqenv

from .compaction_handoff import CompactionState, drive_entry, failure_of, judged, post_blocker, register_context, register_of, rulings, script, script_module
from .nudges import once_per_hour
from .session_tree import Subagent, drive_lane
from .tests.register_fixtures import DRIVE_CONFIG
from .tests.rulings_consumers import separate

DRIVE_ENV = "CLAUDE_LONG_RUNNING_DRIVE"
HELPER_AGENTS = frozenset({"Explore", "Plan", "claude-code-guide", "statusline-setup", "output-style-setup", "ship-pr"})
HELPER_PLUGINS = ("cc-context:", "cc-review:", "cc-slack:", "cc-present:", "codex:", "open-pr:")
LANE_AGENTS = frozenset({"long-running:lane", "long-running:lane-ship"})
SPAWNED = f"{sys.executable} {script('drive.py')} spawned"
SPAWNED_TIMEOUT_SECONDS = 20
INLINE_BRIEF = "inline-prompt"
LANE_ENV = "CLAUDE_LONG_RUNNING_LANE"
REGISTERED = "registered at its first command"
ROOT_THREAD = "root"
INCIDENT_NAME = re.compile(r"^(?:incident|outage)-")
INCIDENT_BRIEF = re.compile(r"\b(?:incident|outage)\b", re.IGNORECASE)
SLICE_HEADER = re.compile(r"^Register slice: lane=(\S+) step=\S+", re.MULTILINE)
SCALE_LINES = re.compile(r"^[ \t>*_-]*Scale:.*$", re.MULTILINE)
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
MISSCALED = "Warning, nothing was blocked: a judge reads the `Scale:` line of {brief} as missing the N its paths run at. Fix the line for the next spawn."
MISSCALED_STALE = (
    "Warning, nothing was blocked: a judge reads the `Scale:` line of {brief} as missing the N its paths run at, and its register slice is stale or empty."
    " Fix the line and run `rulings.py slice --refresh` on the brief for the next spawn."
)
INCIDENT_NOTE = "The incident spawn's prompt now carries its register slice and the unclassified owner captures."
ALERT_WORDS = re.compile(r"\b(?:alerts?|incidents?|outages?|sentry)\b|(?-i:\bmonitors?\b)", re.IGNORECASE)
TENANT_EDGE_403 = re.compile(r"\b403s?\b")
STAND_DOWN = re.compile(r"\bstand[- ]?down\b", re.IGNORECASE)
NO_403_STAND_DOWN = (
    "Warning, nothing was blocked: an alert or incident brief needs a step that stands down on tenant-edge 403s."
    " Start it from the drive's `root/incident-template-fix.md` and launch it with `desk-launch.py incident <name> <brief>`, or mark a non-incident brief `ccx: incident=none`."
)
ALERT_BRIEF = "Datadog monitor 100000001 fired on POST /api/sessions.\n1. Classify the failing requests.\n2. Fix it.\n"
STANDS_DOWN = ALERT_BRIEF.replace("2. Fix it.", "2. If they are tenant-edge 403s, post STAND-DOWN and stop.\n3. Fix it.")
BRIEFS = Path(__file__).parent / "tests" / "fixtures" / "briefs"
SLICE = f"{sys.executable} {script('rulings.py')} slice"
FRESH_SLICE = {SLICE: "fresh /tmp/brief.md slice-hash 0123456789ab\n"}
STALE = {SLICE: "refreshed /tmp/brief.md slice-hash 0123456789ab -> 89abcdef0123\n"}
SLICED = "Lane lane-a: build the relay.\nScale: chats on one Mac, up to 1,000.\nRegister slice: lane=lane-a step=1.9\nslice-hash: 0123456789ab\n"
UNSCALED = SLICED.replace("Scale: chats on one Mac, up to 1,000.\n", "")
SCALED = {"verdict": "no", "artifact_phrase": "", "evidence_phrase": "", "sentence": "It names the N."}
MISSED = {"verdict": "yes", "artifact_phrase": "chats on one Mac", "evidence_phrase": "build the relay", "sentence": "The relay runs once per team, not per chat on one Mac."}
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


def brief_of(prompt: str) -> tuple[str, Path | None, bool]:
    named = [path for found in BRIEF_FILE.findall(prompt) if os.path.isfile(path := Path(found).expanduser())]
    holder = next((path for path in named if SLICE_HEADER.search(path.read_text())), None)
    if SLICE_HEADER.search(prompt) or not holder:
        return prompt, holder or next(iter(named), None), True
    return holder.read_text(), holder, False


def agent_spawn(call: TaskCall) -> Spawn | None:
    if helper(call.agent_type):
        return None
    prompt = call.raw.get("prompt") or ""
    text, brief, inline = brief_of(prompt)
    sliced = SLICE_HEADER.search(text)
    return Spawn(call.agent_name or (sliced[1] if sliced else "lane"), incident_spawn(call, prose(call)[:1]), text, brief, inline, prompt)


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


def misscaled(which: list[str], spawn: Spawn) -> str | None:
    if not (lines := SCALE_LINES.findall(spawn.text)):
        return None
    brief = SLICE_HEADER.split(spawn.text, maxsplit=1)[0].strip()
    return judged("scale", f"The brief, verbatim:\n{brief}", "\n".join(line.strip() for line in lines), which[1])


def refreshed(evt: BaseHookEvent, which: list[str], spawn: Spawn) -> tuple[str, str]:
    text = spawn.text if SLICE_HEADER.search(spawn.text) else f"{spawn.text.rstrip()}\n\n{INCIDENT_SLICE.format(lane=spawn.lane)}\n"
    with tempfile.TemporaryDirectory() as staging:
        copy = Path(staging) / "brief.md"
        copy.write_text(text)
        said = rulings(str(evt.cwd), "slice", "--refresh", str(copy), *which, evt=evt)
        return said, copy.read_text()


def spawn(name: str, prompt: str, agent_type: str = "long-running:lane", scale: dict = SCALED, **extra: object) -> Input:
    commands = {"claude -p": judge_answer(scale)} | extra.pop("commands", {})
    return Input(tool="Agent", tool_input={"name": name, "prompt": prompt, "subagent_type": agent_type}, **({"state": [ACTIVE], "env": DRIVE_CONFIG, "commands": commands} | extra))


def judge_answer(verdict: dict) -> str:
    return json.dumps({"structured_output": verdict, "total_cost_usd": 0.01, "usage": {"input_tokens": 1000, "output_tokens": 50}})


@on(
    Event.PreToolUse,
    only_if=[Tool("Agent", "Bash")],
    skip_if=[Annotated("raw")],
    tests=separate("register_injection.slice_on_spawn", {
        spawn("lane-a", "Lane lane-a: build the relay."): Warn(pattern=r"has no `Register slice: lane=<name> step=<n>` line"),
        spawn("lane-a", SLICED, commands=FRESH_SLICE): Allow(),
        spawn("lane-a", UNSCALED, commands=FRESH_SLICE): Allow(),
        spawn("lane-a", SLICED, commands=STALE): Warn(pattern=r"stale or empty\. Run `rulings\.py slice --refresh <brief>`"),
        spawn("lane-a", SLICED, scale=MISSED, commands=FRESH_SLICE): Warn(
            pattern=r"^Warning, nothing was blocked: a judge reads the `Scale:` line of <brief> as missing the N[\s\S]*once per team"
        ),
        spawn("lane-a", SLICED, scale=MISSED, commands=FRESH_SLICE | {"claude -p": (1, "model unavailable")}): Allow(),
        spawn("lane-a", SLICED, scale=MISSED, commands=STALE): Warn(pattern=r"as missing the N its paths run at, and its register slice is stale or empty[\s\S]*once per team"),
        Input(command=f"orca-launch.sh lane-a sol xhigh {BRIEFS / 'sliced.md'}", state=[ACTIVE], env=DRIVE_CONFIG, commands=FRESH_SLICE | {"claude -p": judge_answer(MISSED)}): Warn(
            pattern=r"sliced\.md as missing the N"
        ),
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
        Input(command=f"orca-launch.sh lane-a sol xhigh {BRIEFS / 'sliced.md'}", state=[ACTIVE], env=DRIVE_CONFIG, commands=FRESH_SLICE | {"claude -p": judge_answer(SCALED)}): Allow(),
        Input(command=f"orca-launch.sh lane-a-sub sol xhigh {BRIEFS / 'unsliced.md'}", agent_id="a1b2c3", state=[ACTIVE], env=DRIVE_CONFIG): Warn(pattern=r"unsliced\.md"),
        Input(command=f"orca-launch.sh lane-a-sub sol xhigh {BRIEFS / 'unsliced.md'}", env={DRIVE_ENV: "brook-drive"}): Warn(pattern=r"unsliced\.md"),
        Input(command=f"codex-ask --dispatch --lane lane-a {BRIEFS / 'unsliced.md'}", state=[ACTIVE], env=DRIVE_CONFIG): Warn(pattern=r"unsliced\.md"),
        Input(command=f"codex-ask --dispatch --lane lane-a-sub {BRIEFS / 'unsliced.md'}", agent_id="a1b2c3", state=[ACTIVE], env=DRIVE_CONFIG): Warn(pattern=r"unsliced\.md"),
        Input(command=f"codex-ask -s /tmp/finder {BRIEFS / 'unsliced.md'}", state=[ACTIVE], env=DRIVE_CONFIG): Allow(),
        Input(command=f"codex-ask --dispatch --lane lane-a '{'inline brief ' * 40}'", state=[ACTIVE], env=DRIVE_CONFIG): Allow(),
        spawn("lane-a", f"Lane lane-a: read /{'x' * 300}.md. {SLICED}", commands=FRESH_SLICE): Allow(),
    }),
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
    try:
        said, fresh = refreshed(evt, which, found)
        if found.incident and not found.inline:
            found.brief.write_text(fresh)
    except UNREAD as failed:
        post_blocker(evt, f"slice_on_spawn: the register slice for {found.lane} did not read: {why(failed)}", key=f"slice-on-spawn:{found.lane}")
        return None
    if not found.incident:
        stale = not said.startswith(FRESH) and once_per_hour(evt, [STALE_SLICE])
        if judged_scale := misscaled(which, found):
            return evt.context(f"{(MISSCALED_STALE if stale else MISSCALED).format(brief=found.shown)}\n{judged_scale}")
        return evt.context(STALE_SLICE.format(brief=found.shown)) if stale else None
    return evt.rewrite(evt._tool_input | {"prompt": fresh}, note=INCIDENT_NOTE) if found.inline else None


@on(
    Event.PreToolUse,
    only_if=[Tool("Agent", "Bash")],
    skip_if=[Annotated("raw")],
    tests=separate("register_injection.warn_unscaled_spawn", {
        spawn("lane-a", UNSCALED): Warn(pattern=r"^Warning, nothing was blocked: brief <brief> has no `Scale:` line; add one line to it: `Scale: no N`"),
        spawn("lane-a", SLICED): Allow(),
        spawn("lane-a-handoff", UNSCALED): Allow(),
        spawn("lane-a-2", UNSCALED): Allow(),
        spawn("lane-b", f"Replaces: lane-a\n{UNSCALED}"): Allow(),
        spawn("lane-a", "Lane lane-a: build the relay."): Allow(),
        spawn("incident-api-fix", "Fix the api outage."): Allow(),
        Input(command=f"orca-launch.sh lane-a sol xhigh {BRIEFS / 'sliced.md'}", state=[ACTIVE], env=DRIVE_CONFIG): Allow(),
    }),
)
def warn_unscaled_spawn(evt: BaseHookEvent) -> HookResult | None:
    if not drive_args(evt):
        return None
    try:
        found = agent_spawn(evt.as_input(TaskCall)) if evt.tool_name == "Agent" else launch_spawn(evt)
    except UNREAD:
        return None
    if not found or found.incident or not SLICE_HEADER.search(found.text) or not found.owes_scale():
        return None
    return evt.context(f"Warning, nothing was blocked: {lane_brief.no_scale(found.shown)}")


@on(
    Event.PreToolUse,
    only_if=[Tool("Agent")],
    skip_if=[Annotated("raw"), Annotated("incident", "none")],
    tests={
        spawn("api-sessions-fix", ALERT_BRIEF): Warn(pattern=r"^Warning, nothing was blocked: an alert or incident brief needs a step that stands down on tenant-edge 403s[\s\S]*desk-launch\.py incident <name> <brief>"),
        spawn("api-sessions-fix", STANDS_DOWN): Allow(),
        spawn("lane-a", SLICED): Allow(),
        spawn("incident-api-fix", "Fix it."): Warn(),
        spawn("api-fix", "Owner link: <https://x.slack.com/archives/C0|#alerts-api>. Fix it."): Warn(),
        spawn("api-fix", "Sentry issue API-1N80 spiked. Fix it."): Warn(),
        spawn("lane-a", "Lane lane-a: build the relay. Arm a Monitor on the PR."): Allow(),
        spawn("lane-a", f"Your brief is {BRIEFS / 'quoted-rulings.md'}; read it."): Allow(),
        spawn("api-sessions-fix", f"Your brief is {BRIEFS / 'alert.md'}; read it."): Warn(pattern=r"incident-template-fix\.md"),
        spawn("api-sessions-fix", f"ccx: incident=none\n{ALERT_BRIEF}"): Allow(),
        Input(tool="Agent", tool_input={"name": "incident-api-fix", "prompt": "Fix it."}): Allow(),
    },
)
def warn_incident_brief_without_403_stand_down(evt: BaseHookEvent) -> HookResult | None:
    if not drive_args(evt):
        return None
    try:
        found = agent_spawn(evt.as_input(TaskCall))
        texts = [found.prompt, found.brief.read_text() if found.brief else ""] if found else []
    except UNREAD:
        return None
    if not found:
        return None
    brief = "\n".join(SLICE_HEADER.split(text, maxsplit=1)[0] for text in texts)
    if not ALERT_WORDS.search(f"{found.lane}\n{found.brief.name if found.brief else ''}\n{brief}"):
        return None
    return None if TENANT_EDGE_403.search(brief) and STAND_DOWN.search(brief) else evt.context(NO_403_STAND_DOWN)


@on(
    Event.PreToolUse,
    only_if=[Tool("Agent")],
    skip_if=[Annotated("raw")],
    tests=separate("register_injection.record_spawn", {
        spawn("lane-a", SLICED, commands={SPAWNED: ""}): Allow(),
        spawn("lane-a", "Lane lane-a: build the relay.", agent_type="general-purpose"): Allow(),
        spawn("lane-a", UNSCALED, commands={SPAWNED: ""}): Allow(),
        spawn("lane-a", "Find every caller of drain.", agent_type="Explore"): Allow(),
        Input(tool="Agent", tool_input={"name": "lane-a", "prompt": SLICED}): Allow(),
    }),
)
def record_spawn(evt: BaseHookEvent) -> HookResult | None:
    if not drive_args(evt):
        return None
    call = evt.as_input(TaskCall)
    try:
        found = agent_spawn(call)
    except UNREAD:
        return None
    if not found or not (found.incident or SLICE_HEADER.search(found.text) or call.agent_type in LANE_AGENTS):
        return None
    argv = [sys.executable, str(script("drive.py")), "spawned", "--lane", found.lane, "--brief", str(found.brief or INLINE_BRIEF), "--by", drive_lane(evt) or "root", "--session", evt.session_id]
    argv += ["--model", model] if (model := call.raw.get("model")) else []
    argv += ["--drive", drive] if (drive := reqenv.getenv(DRIVE_ENV)) else []
    subprocess.run(argv, capture_output=True, text=True, timeout=SPAWNED_TIMEOUT_SECONDS)
    return None


class LaneRegistrations(WorkflowState):
    threads: list[str] = []
    pending: dict[str, str] = {}


def own_lane(evt: BaseHookEvent) -> str | None:
    if evt.agent_id is None:
        return reqenv.getenv(LANE_ENV)
    transcript = Path(evt._raw["transcript_path"]).with_suffix("") / "subagents" / f"agent-{evt.agent_id}.jsonl"
    agent = Subagent(evt.agent_id, json.loads(transcript.with_suffix(".meta.json").read_text()), transcript)
    if helper(agent.meta.get("agentType")):
        return None
    prompt = next((event.text for event in agent.read_events() if isinstance(event, UserEvent)), "")
    sliced = SLICE_HEADER.search(brief_of(prompt)[0])
    lane = sliced[1] if sliced else agent.name if agent.meta.get("agentType") in LANE_AGENTS else None
    return None if lane and lane != agent.name and lane == drive_lane(evt) else lane


def registration(lane: str) -> str:
    return f"cci post --lane {shlex.quote(lane)} --kind state --stamp {shlex.quote(f'{lane}: {REGISTERED}')} >/dev/null"


def registers(command: str, lane: str) -> bool:
    return f"cci post --lane {shlex.quote(lane)} " in command


ORCA_LANE = {DRIVE_ENV: "brook-drive", LANE_ENV: "lane-a"}


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash")],
    skip_if=[Annotated("raw")],
    tests=separate("register_injection.register_lane_at_first_command", {
        Input(command="git status", env=ORCA_LANE): Rewrite("cci post --lane lane-a --kind state --stamp 'lane-a: registered at its first command' >/dev/null\ngit status"),
        Input(command="cci post --lane lane-a --kind state 'lane-a: started'", env=ORCA_LANE): Allow(),
        Input(command="git status", env=ORCA_LANE, state=[LaneRegistrations(threads=[ROOT_THREAD])]): Allow(),
        Input(command="git status", env={DRIVE_ENV: "brook-drive"}): Allow(),
        Input(command="git status", env={LANE_ENV: "lane-a"}): Allow(),
    }),
)
def register_lane_at_first_command(evt: BaseHookEvent) -> HookResult | None:
    thread = evt.agent_id or ROOT_THREAD
    if not drive_args(evt) or thread in LaneRegistrations.load(evt).threads:
        return None
    try:
        lane = own_lane(evt)
    except UNREAD:
        lane = None
    with LaneRegistrations.mutate(evt) as state:
        if lane:
            state.pending[thread] = lane
        else:
            state.threads = [*state.threads, thread]
    command = evt._tool_input["command"]
    if not lane or registers(command, lane):
        return None
    return evt.rewrite_command(f"{registration(lane)}\n{command}", note=f"This thread registered with cci as lane `{lane}` before its first command.")


@on(
    Event.PostToolUse,
    only_if=[Tool("Bash")],
    tests=separate("register_injection.settle_lane_registration", {
        Input(command=f"{registration('lane-a')}\ngit status", env=ORCA_LANE, state=[LaneRegistrations(pending={ROOT_THREAD: "lane-a"})]): Allow(),
        Input(command="git status", env=ORCA_LANE): Allow(),
    }),
)
def settle_lane_registration(evt: BaseHookEvent) -> HookResult | None:
    thread = evt.agent_id or ROOT_THREAD
    if (lane := LaneRegistrations.load(evt).pending.get(thread)) and registers(evt._tool_input.get("command") or "", lane):
        with LaneRegistrations.mutate(evt) as state:
            state.pending.pop(thread, None)
            state.threads = [*state.threads, thread]
    return None


@on(
    Event.SubagentStart,
    skip_planning_agents=False,
    tests=separate("register_injection.brief_subagent", {
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
    }),
)
def brief_subagent(evt: BaseHookEvent) -> HookResult | None:
    if not (which := drive_args(evt)):
        return None
    register = register_of(str(evt.cwd), *which, evt=evt)
    return register_context(evt, register) if register else None


@on(
    Event.SessionStart,
    skip_if=[FromSubagent()],
    tests=separate("register_injection.brief_drive_worker", {
        Input(source="startup", env={DRIVE_ENV: "900424b6"}, commands=FOUND): Warn(pattern=r"^Standing rules register `ccccccc`"),
        Input(source="startup", env={DRIVE_ENV: "900424b6"}, commands=NONE_FOUND): Allow(),
        Input(source="startup"): Allow(),
        Input(source="compact", env={DRIVE_ENV: "900424b6"}, commands=FOUND, state=[ACTIVE]): Allow(),
    }),
)
def brief_drive_worker(evt: BaseHookEvent) -> HookResult | None:
    if CompactionState.load(evt).active or not (worker := reqenv.getenv(DRIVE_ENV)):
        return None
    register = register_of(str(evt.cwd), "--drive", worker, evt=evt)
    return register_context(evt, register) if register else None
