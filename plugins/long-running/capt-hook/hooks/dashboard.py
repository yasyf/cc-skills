from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Event, FromSubagent, HookResult, Input, Tool, Warn, on
from captain_hook.util import reqenv

from .compaction_handoff import CompactionState
from .installed import script
from .lane_rotation import DriveActive

FIXTURES = Path(__file__).parent / "tests" / "fixtures"
LIVE_DASHBOARD = "live-dashboard@skills"
DRIVE_ENV = {"CLAUDE_LONG_RUNNING_DRIVE": "900424b6", "CLAUDE_CONFIG_DIR": str(FIXTURES / "claude-config")}
LONG_PATHS_ENV = {**DRIVE_ENV, "CLAUDE_CONFIG_DIR": str(FIXTURES / "claude-config-long")}
SHARED = (
    r"^Give the owner the dashboard link now: the command below prints it \(`start` in place of `url` if it prints nothing\)\. "
    r"Then tailor layout\.yaml in its --dir per /live-dashboard picking\.md before the first milestone report\.\n"
    r"```sh\n/plugins/cache/skills/live-dashboard/0\.1\.0/bin/live-dashboard url --dir /state/release-v3/dashboard\n```$"
)


def config_dir() -> Path:
    return Path(reqenv.getenv("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def entries() -> list[dict]:
    return [json.loads(path.read_text()) for path in sorted((config_dir() / "long-running" / "drives").glob("*.json"))]


def entry_of(evt: BaseHookEvent) -> dict | None:
    named = reqenv.getenv("CLAUDE_LONG_RUNNING_DRIVE")
    return next((entry for entry in entries() if entry["drive"] == named or (not named and evt.session_id in entry["sessions"])), None)


def dashboard_bin() -> Path:
    installed = json.loads((config_dir() / "plugins" / "installed_plugins.json").read_text())
    return Path(installed["plugins"][LIVE_DASHBOARD][0]["installPath"]) / "bin" / "live-dashboard"


def dashboard_dir(entry: dict) -> Path:
    return Path(entry["state_dir"]) / "dashboard"


def serve(entry: dict) -> None:
    cli, directory = shlex.quote(str(dashboard_bin())), dashboard_dir(entry)
    quoted = shlex.quote(str(directory))
    script = (
        f"python3 {shlex.quote(str(script("drive.py")))} context --drive {shlex.quote(entry['drive'])}"
        f" && {{ [ -e {shlex.quote(str(directory / 'layout.yaml'))} ] || {cli} init --dir {quoted} --preset drive; }}"
        f" && exec {cli} start --dir {quoted}"
    )
    subprocess.Popen(["sh", "-c", script], env=dict(reqenv.env_map()), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def watch_prompts(evt: BaseHookEvent, entry: dict) -> None:
    argv = ["python3", str(script("prompt_watch.py")), "start", "--drive", entry["drive"]]
    if evt.session_id in entry["sessions"] and (handle := reqenv.getenv("ORCA_TERMINAL_HANDLE")):
        argv += ["--root-terminal", handle]
    subprocess.Popen(argv, env=dict(reqenv.env_map()), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def share_link(evt: BaseHookEvent, entry: dict) -> HookResult:
    command = shlex.join([str(dashboard_bin()), "url", "--dir", str(dashboard_dir(entry))])
    return evt.context(
        "Give the owner the dashboard link now: the command below prints it (`start` in place of `url` if it prints nothing). "
        f"Then tailor layout.yaml in its --dir per /live-dashboard picking.md before the first milestone report.\n```sh\n{command}\n```"
    )


def starts_drive(evt: BaseHookEvent) -> bool:
    for call in evt.cmd.calls():
        argv = (Path(call.name).name, *call.args)
        if argv[0].startswith("python") and len(argv) > 1:
            argv = (Path(argv[1]).name, *argv[2:])
        if argv[:2] == ("drive.py", "start"):
            return True
    return False


@on(
    Event.SessionStart,
    only_if=[DriveActive()],
    skip_if=[FromSubagent()],
    tests={
        Input(
            source="resume",
            session_id="900424b6-0000",
            state=[CompactionState(active=True)],
        ): Allow(),
        Input(source="startup"): Allow(),
    },
)
def serve_on_session_start(evt: BaseHookEvent) -> HookResult | None:
    if entry := entry_of(evt):
        serve(entry)
        watch_prompts(evt, entry)
    return None


@on(
    Event.PostToolUse,
    only_if=[Tool("Bash")],
    skip_if=[FromSubagent()],
    tests={
        Input(
            command="drive.py start --ledger 1a2b3c4d --orca-run run_1",
            output="drive 900424b6 on ledger 1a2b3c4d",
            session_id="900424b6-0000",
        ): Allow(),
        Input(command="drive.py list", output="900424b6 ledger=1a2b3c4d"): Allow(),
    },
)
def serve_on_drive_start(evt: BaseHookEvent) -> HookResult | None:
    if starts_drive(evt) and (entry := entry_of(evt)):
        serve(entry)
        watch_prompts(evt, entry)
    return None


@on(
    Event.SessionStart,
    only_if=[DriveActive()],
    skip_if=[FromSubagent()],
    tests={
        Input(source="compact", env=DRIVE_ENV, state=[CompactionState(active=True)]): Warn(pattern=SHARED),
        Input(source="resume", env=DRIVE_ENV, state=[CompactionState(active=True)]): Warn(pattern=r"--dir /state/release-v3/dashboard\n```$"),
        Input(source="compact", env=LONG_PATHS_ENV, state=[CompactionState(active=True)]): Warn(pattern=r"past-any-reasonable-length/scratch/release-v3/dashboard\n```$"),
        Input(source="compact", env=DRIVE_ENV): Allow(),
        Input(source="compact", env=DRIVE_ENV, agent_id="a1b2c3", state=[CompactionState(active=True)]): Allow(),
    },
)
def share_on_session_start(evt: BaseHookEvent) -> HookResult | None:
    return share_link(evt, entry) if (entry := entry_of(evt)) else None


@on(
    Event.PostToolUse,
    only_if=[Tool("Bash")],
    skip_if=[FromSubagent()],
    tests={
        Input(command="drive.py start --ledger 1a2b3c4d", output="drive 900424b6 on ledger 1a2b3c4d", env=DRIVE_ENV): Warn(
            pattern=r"^Give the owner the dashboard link now: (?s:.*)live-dashboard url --dir /state/release-v3/dashboard\n```$"
        ),
        Input(command="drive.py start --ledger 1a2b3c4d", output="drive 900424b6 on ledger 1a2b3c4d", env=LONG_PATHS_ENV): Warn(
            pattern=r"past-any-reasonable-length/scratch/release-v3/dashboard\n```$"
        ),
        Input(command="drive.py list", output="900424b6 ledger=1a2b3c4d", env=DRIVE_ENV): Allow(),
    },
)
def share_on_drive_start(evt: BaseHookEvent) -> HookResult | None:
    return share_link(evt, entry) if starts_drive(evt) and (entry := entry_of(evt)) else None
