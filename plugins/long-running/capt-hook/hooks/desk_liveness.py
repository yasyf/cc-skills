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

from .compaction_handoff import CCN_TIMEOUT_SECONDS, Bound, bus_name, drive_entry
from .prompt_watch import DRIVE_ENV, FIXTURES

DESK = "orca-desk"
DESK_INBOX = f"{DESK}.md"
RUNNER = "desk-runner"
WATCHDOG = "drive-watchdog"
STALE = timedelta(minutes=5)
PACIFIC = ZoneInfo("America/Los_Angeles")
FENCE = "~" * 12
READ_WHY = "tail <drive>/supervisor.log <drive>/lr-relay.log"
APPEND = "STATE=/state/release-v3; echo 'R1 orca-desk: launch inc-fix NOW incident xhigh brief=/b.md' >> \"$STATE/inbox/orca-desk.md\""
POST = "cci post --lane root --kind go --to orca-desk --text 'orca-desk: launch inc-fix NOW incident xhigh brief=/b.md'"
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
    """The newest `orca alive` record the relay posted on the drive, read from cci wherever the runner runs, as a list of at most one; None when cci does not answer."""
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
    only_if=[Bound(), Tool("Bash")],
    tests={
        Input(command=APPEND, env=SILENT_CCI): Warn(pattern=r"^UNREAD: the relay reads only cci records addressed to `orca-desk`[\s\S]*cci post --lane <your lane> --kind go --to orca-desk --text"),
        Input(command=POST, env=SILENT_CCI): Warn(pattern=r"^LAUNCH-FAILED: no orca alive record from the relay in the last five minutes \(last never\)[\s\S]*supervisor\.log"),
        Input(command=APPEND, env=UNBOUND_ENV): Allow(),
        Input(command="cci post --lane root --kind go --to landing-desk --text 'go #1'", env=SILENT_CCI): Allow(),
        Input(command="echo '- G12 (the coordinator) GO walker' >> /state/release-v3/inbox/deploy-go.md", env=SILENT_CCI): Allow(),
    },
)
def desk_runner_down(evt: BaseHookEvent) -> HookResult | None:
    if not (appends_to_desk(evt) or posts_to_desk(evt)) or (entry := drive_entry(evt)) is None:
        return None
    if appends_to_desk(evt):
        return evt.context(
            f"UNREAD: the relay reads only cci records addressed to `{DESK}`, never `{DESK_INBOX}`, so this line launches, relays and alerts nothing. Post it instead:",
            f"{FENCE}\ncci post --lane <your lane> --kind go --to {DESK} --text '<the {DESK}: line>'\n{FENCE}",
        )
    if (found := alive_records(entry)) is None:
        return None
    record = found[-1] if found else None
    if record is not None and datetime.now(timezone.utc) - datetime.fromisoformat(record["at"]) < STALE:
        return None
    return evt.context(
        f"LAUNCH-FAILED: no orca alive record from the relay in the last five minutes (last {pacific(record['at']) if record else 'never'}), so nothing reads `{DESK}` lines. "
        "The supervisor restarts it within a minute and it reads the line from its saved cursor; if it stays down, read why.",
        f"{FENCE}\n{READ_WHY}\n{FENCE}",
    )
