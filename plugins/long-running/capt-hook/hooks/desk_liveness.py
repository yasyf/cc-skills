from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePath
from zoneinfo import ZoneInfo

from captain_hook import Allow, BaseHookEvent, Event, HookResult, Input, Tool, Warn, on

from .dashboard import DRIVE_ENV, FIXTURES, entry_of

DESK_INBOX = "orca-desk.md"
HEARTBEAT = "orca-runner.heartbeat.json"
STALE = timedelta(seconds=30)
PACIFIC = ZoneInfo("America/Los_Angeles")
FENCE = "~" * 12
START = "nohup desk-runner.py run --config <drive>/runner.json --desk orca > <drive>/orca-runner.log 2>&1 < /dev/null &"
APPEND = "STATE=/state/release-v3; echo 'R1 orca-desk: launch inc-fix NOW incident xhigh brief=/b.md' >> \"$STATE/inbox/orca-desk.md\""
UNBOUND_ENV = {"CLAUDE_CONFIG_DIR": str(FIXTURES / "claude-config")}


def appends_to_desk(evt: BaseHookEvent) -> bool:
    return any(
        redirect.op == ">>" and PurePath(redirect.target.strip("'\"")).name == DESK_INBOX
        for call in evt.command.calls()
        for redirect in call.redirects
    )


def heartbeat(entry: dict) -> Path:
    return Path(entry["state_dir"]) / "inbox" / HEARTBEAT


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def last_pass(path: Path) -> dict | None:
    return json.loads(path.read_text()) if path.is_file() else None


def running(beat: dict | None) -> bool:
    return beat is not None and datetime.now(timezone.utc) - datetime.fromisoformat(beat["at"]) <= STALE and alive(beat["pid"])


def pacific(at: str) -> str:
    return datetime.fromisoformat(at).astimezone(PACIFIC).strftime("%-I:%M %p")


@on(
    Event.PostToolUse,
    only_if=[Tool("Bash")],
    tests={
        Input(command=APPEND, env=DRIVE_ENV): Warn(
            pattern=r"^LAUNCH-FAILED: orca desk runner not running \(last pass never\)[\s\S]*--config <drive>/runner\.json"
        ),
        Input(command=APPEND, env=UNBOUND_ENV): Allow(),
        Input(command="echo '- G12 (root) GO walker' >> /state/release-v3/inbox/deploy-go.md", env=DRIVE_ENV): Allow(),
    },
)
def desk_runner_down(evt: BaseHookEvent) -> HookResult | None:
    if not appends_to_desk(evt) or (entry := entry_of(evt)) is None:
        return None
    beat = last_pass(heartbeat(entry))
    if running(beat):
        return None
    when = pacific(beat["at"]) if beat else "never"
    restart = beat["restart"] if beat else START
    return evt.context(
        f"LAUNCH-FAILED: orca desk runner not running (last pass {when}), so nothing reads `{DESK_INBOX}`. "
        "Restart it from the Run's bound terminal; it reads the appended line from its saved cursor.",
        f"{FENCE}\n{restart}\n{FENCE}",
    )
