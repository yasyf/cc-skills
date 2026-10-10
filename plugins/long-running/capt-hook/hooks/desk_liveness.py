from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from captain_hook import Allow, BaseHookEvent, Event, FileFixture, HookResult, Input, Tool, Warn, on

DESK_INBOX = "orca-desk.md"
HEARTBEAT = "orca-runner.heartbeat.json"
STALE = timedelta(seconds=30)
PACIFIC = ZoneInfo("America/Los_Angeles")
FENCE = "~" * 12
START = "nohup desk-runner.py run --config <drive>/runner.json --desk orca > <drive>/orca-runner.log 2>&1 < /dev/null &"
APPEND = "echo 'R1 orca-desk: launch inc-fix NOW incident xhigh brief=/b.md' >> ~/orca-desk.md"
LIVE = json.dumps({"pid": 1, "at": "2999-01-01T00:00:00+00:00", "restart": "nohup desk-runner.py run --config /d/runner.json --desk orca &"})
STOPPED = json.dumps({"pid": 1, "at": "2026-10-10T20:59:00+00:00", "restart": "nohup desk-runner.py run --config /d/runner.json --desk orca &"})


def desk_inboxes(evt: BaseHookEvent) -> Iterator[Path]:
    for call in evt.command.calls():
        for redirect in call.redirects:
            if redirect.op == ">>" and (path := Path(redirect.target).expanduser()).name == DESK_INBOX:
                yield path


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def last_pass(heartbeat: Path) -> dict | None:
    return json.loads(heartbeat.read_text()) if heartbeat.is_file() else None


def running(beat: dict | None) -> bool:
    return beat is not None and datetime.now(timezone.utc) - datetime.fromisoformat(beat["at"]) <= STALE and alive(beat["pid"])


def pacific(at: str) -> str:
    return datetime.fromisoformat(at).astimezone(PACIFIC).strftime("%-I:%M %p")


@on(
    Event.PostToolUse,
    only_if=[Tool("Bash")],
    tests={
        Input(command=APPEND): Warn(pattern=r"^LAUNCH-FAILED: orca desk runner not running \(last pass never\)"),
        Input(command=APPEND, file=FileFixture(home=True, name=HEARTBEAT, content=STOPPED)): Warn(
            pattern=r"\(last pass 1:59 PM\)[\s\S]*--config /d/runner\.json"
        ),
        Input(command=APPEND, file=FileFixture(home=True, name=HEARTBEAT, content=LIVE)): Allow(),
        Input(command="echo '- G12 (root) GO walker' >> ~/deploy-go.md"): Allow(),
    },
)
def desk_runner_down(evt: BaseHookEvent) -> HookResult | None:
    for inbox in desk_inboxes(evt):
        beat = last_pass(inbox.parent / HEARTBEAT)
        if running(beat):
            continue
        when = pacific(beat["at"]) if beat else "never"
        restart = beat["restart"] if beat else START
        return evt.context(
            f"LAUNCH-FAILED: orca desk runner not running (last pass {when}), so nothing reads `{inbox.name}`. "
            "Restart it from the Run's bound terminal; it reads the appended line from its saved cursor.",
            f"{FENCE}\n{restart}\n{FENCE}",
        )
    return None
