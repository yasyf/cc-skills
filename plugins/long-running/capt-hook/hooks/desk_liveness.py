from __future__ import annotations

import itertools
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import PurePath
from zoneinfo import ZoneInfo

from captain_hook import Allow, BaseHookEvent, Event, HookResult, Input, Tool, Warn, on
from captain_hook.util import reqenv

from .compaction_handoff import CCN_TIMEOUT_SECONDS, bus_name
from .prompt_watch import DRIVE_ENV, FIXTURES, entry_of

DESK = "orca-desk"
DESK_INBOX = f"{DESK}.md"
RUNNER = "desk-runner"
WATCHDOG = "drive-watchdog"
STALE = timedelta(minutes=5)
PACIFIC = ZoneInfo("America/Los_Angeles")
FENCE = "~" * 12
START = "nohup desk-runner.py run --config <drive>/runner.json --desk orca >> <drive>/orca-runner.log 2>&1 < /dev/null &"
APPEND = "STATE=/state/release-v3; echo 'R1 orca-desk: launch inc-fix NOW incident xhigh brief=/b.md' >> \"$STATE/inbox/orca-desk.md\""
POST = "cci post --lane root --kind go --to orca-desk --text 'R1 (9:00 PM) orca-desk: launch inc-fix NOW incident xhigh brief=/b.md'"
SILENT_CCI = DRIVE_ENV | {"PATH": f"{FIXTURES / 'bin'}:/usr/bin:/bin"}
UNBOUND_ENV = {"CLAUDE_CONFIG_DIR": str(FIXTURES / "claude-config"), "PATH": SILENT_CCI["PATH"]}
ADDRESS = re.compile(r"[,@]")


def appends_to_desk(evt: BaseHookEvent) -> bool:
    return any(
        redirect.op == ">>" and PurePath(redirect.target.strip("'\"")).name == DESK_INBOX
        for call in evt.command.calls()
        for redirect in call.redirects
    )


def posts_to_desk(evt: BaseHookEvent) -> bool:
    for call in evt.command.calls("cci"):
        args = list(call.args)
        if args[:1] != ["post"]:
            continue
        targets = [value for flag, value in itertools.pairwise(args) if flag == "--to"] + [arg.removeprefix("--to=") for arg in args if arg.startswith("--to=")]
        if any(DESK in ADDRESS.split(target) for target in targets):
            return True
    return False


def alive_records(entry: dict) -> list[dict] | None:
    """The newest `orca alive` record the orca desk runner posted on the drive, read from cci wherever the runner runs, as a list of at most one; None when cci does not answer."""
    drive = bus_name(entry)
    argv = ["cci", "tail", "--drive", drive, "--since", "0", "--json", "--lane", RUNNER, "--kind=state", "--topic=orca", "--to", f"{WATCHDOG}@{drive}", "--limit", "1"]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=CCN_TIMEOUT_SECONDS, env=dict(reqenv.env_map()))
    except (OSError, subprocess.TimeoutExpired):
        return None
    return None if done.returncode else [json.loads(line) for line in done.stdout.splitlines() if line.strip()]


def pacific(at: str) -> str:
    return datetime.fromisoformat(at).astimezone(PACIFIC).strftime("%-I:%M %p")


@on(
    Event.PostToolUse,
    only_if=[Tool("Bash")],
    tests={
        Input(command=APPEND, env=SILENT_CCI): Warn(pattern=r"^LAUNCH-FAILED: no orca alive record from the orca desk runner in the last five minutes \(last never\)[\s\S]*--config <drive>/runner\.json"),
        Input(command=POST, env=SILENT_CCI): Warn(pattern=r"^LAUNCH-FAILED: no orca alive record from the orca desk runner in the last five minutes \(last never\)"),
        Input(command=APPEND, env=UNBOUND_ENV): Allow(),
        Input(command="cci post --lane root --kind go --to landing-desk --text 'go #1'", env=SILENT_CCI): Allow(),
        Input(command="echo '- G12 (root) GO walker' >> /state/release-v3/inbox/deploy-go.md", env=SILENT_CCI): Allow(),
    },
)
def desk_runner_down(evt: BaseHookEvent) -> HookResult | None:
    if not (appends_to_desk(evt) or posts_to_desk(evt)) or (entry := entry_of(evt)) is None:
        return None
    if (found := alive_records(entry)) is None:
        return None
    record = found[-1] if found else None
    if record is not None and datetime.now(timezone.utc) - datetime.fromisoformat(record["at"]) < STALE:
        return None
    attrs = record["fields"].get("attrs", {}) if record else {}
    return evt.context(
        f"LAUNCH-FAILED: no orca alive record from the orca desk runner in the last five minutes (last {pacific(record['at']) if record else 'never'}), so nothing reads `{DESK}` lines. "
        "Restart it from the Run's bound terminal; it reads the line from its saved cursor.",
        f"{FENCE}\n{attrs.get('restart', START)}\n{FENCE}",
    )
