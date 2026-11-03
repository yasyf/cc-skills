from __future__ import annotations

import subprocess
import sys

from captain_hook import Allow, BaseHookEvent, Event, FromSubagent, HookResult, Input, Warn, on
from captain_hook.util import reqenv

from .compaction_handoff import bus_name, drive_entry, script
from .lane_rotation import DriveActive
from .owner_facing import REPLY_WINDOW, last_reply
from .tests.handoff_fixtures import reply_transcript
from .tests.register_fixtures import ACTIVE, DRIVE_CONFIG
from .window_lane import Coordinator, coordinator_tests

LONG_UPDATE = 40
CHECK_SECONDS = 60
SHOWN = 3
NAME_CHARS = 36
MISSING = "missing: "
CHECK = f"{sys.executable} {script('owner_update.py')} --drive brook-drive --check -"
UNNAMED = "This owner update leaves out {count} sections, lanes, asks or PRs the sources hold, such as {names}. Relay `owner_update.py --drive {drive} --render` as it prints, or add them."
UPDATE = "\n".join(f"- line {index}: work moved" for index in range(LONG_UPDATE + 1))


class LongReply:
    def check(self, evt: BaseHookEvent) -> bool:
        return len(last_reply(evt).splitlines()) > LONG_UPDATE


@on(
    Event.Stop,
    only_if=[Coordinator(), DriveActive(), LongReply()],
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    tests=coordinator_tests({
        Input(transcript=reply_transcript(UPDATE), state=ACTIVE, env=DRIVE_CONFIG, commands={CHECK: (1, "missing: 2\ntooling-codify-1\n#48401\n")}): Warn(pattern=r"leaves out 2 sections, lanes, asks or PRs the sources hold, such as tooling-codify-1\. Relay"),
        Input(transcript=reply_transcript(UPDATE), state=ACTIVE, env=DRIVE_CONFIG, commands={CHECK: (1, "missing: 25\n" + "".join(f"lane-{n}\n" for n in range(25)))}): Warn(pattern=r"leaves out 25 [\s\S]*such as lane-0, lane-1, lane-2\. Relay"),
        Input(transcript=reply_transcript(UPDATE), state=ACTIVE, env=DRIVE_CONFIG, commands={CHECK: ""}): Allow(),
        Input(transcript=reply_transcript("Landed #48401; nothing else moved."), state=ACTIVE, env=DRIVE_CONFIG, commands={CHECK: (1, "missing: 1\ntooling-codify-1\n")}): Allow(),
        Input(transcript=reply_transcript(UPDATE, sidechain=True), state=ACTIVE, env=DRIVE_CONFIG, commands={CHECK: (1, "missing: 1\ntooling-codify-1\n")}): Allow(),
        Input(transcript=reply_transcript(UPDATE), env=DRIVE_CONFIG, commands={CHECK: (1, "missing: 1\ntooling-codify-1\n")}): Allow(),
    }),
)
def owner_updates_name_every_lane(evt: BaseHookEvent) -> HookResult | None:
    if (entry := drive_entry(evt)) is None:
        return None
    drive = bus_name(entry)
    done = subprocess.run([sys.executable, str(script("owner_update.py")), "--drive", drive, "--check", "-"], input=last_reply(evt), capture_output=True, text=True, timeout=CHECK_SECONDS, env=dict(reqenv.env_map()))
    if done.returncode != 1 or not done.stderr.startswith(MISSING):
        return None
    named = [name[:NAME_CHARS] for name in done.stderr.splitlines()[1:] if not name.startswith("#")]
    return evt.context(UNNAMED.format(count=len(done.stderr.splitlines()) - 1, names=", ".join(named[:SHOWN]) or "its open PRs", drive=drive))
