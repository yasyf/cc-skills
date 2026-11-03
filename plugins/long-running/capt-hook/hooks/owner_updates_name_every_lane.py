from __future__ import annotations

import subprocess
import sys

from captain_hook import Allow, BaseHookEvent, Event, FromSubagent, HookResult, Input, Warn, on

from .compaction_handoff import bus_name, drive_entry, script
from .lane_rotation import DriveActive
from .owner_facing import REPLY_WINDOW, last_reply
from .tests.handoff_fixtures import reply_transcript
from .tests.register_fixtures import ACTIVE, DRIVE_CONFIG

LONG_UPDATE = 40
CHECK_SECONDS = 30
MISSING = "missing: "
CHECK = f"{sys.executable} {script('owner_update.py')} --drive brook-drive --check -"
UNNAMED = (
    "An owner update names every live lane, open ask and open PR. Add these, or rebuild the update from `owner_update.py --drive {drive} --render`:\n```\n{names}\n```"
)
UPDATE = "\n".join(f"- line {index}: work moved" for index in range(LONG_UPDATE + 1))


class LongReply:
    def check(self, evt: BaseHookEvent) -> bool:
        return len(last_reply(evt).splitlines()) > LONG_UPDATE


@on(
    Event.Stop,
    only_if=[DriveActive(), LongReply()],
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    tests={
        Input(transcript=reply_transcript(UPDATE), state=ACTIVE, env=DRIVE_CONFIG, commands={CHECK: (1, "missing: tooling-codify-1, #48401\n")}): Warn(pattern=r"names every live lane[\s\S]*tooling-codify-1, #48401"),
        Input(transcript=reply_transcript(UPDATE), state=ACTIVE, env=DRIVE_CONFIG, commands={CHECK: ""}): Allow(),
        Input(transcript=reply_transcript("Landed #48401; nothing else moved."), state=ACTIVE, env=DRIVE_CONFIG, commands={CHECK: (1, "missing: tooling-codify-1\n")}): Allow(),
        Input(transcript=reply_transcript(UPDATE, sidechain=True), state=ACTIVE, env=DRIVE_CONFIG, commands={CHECK: (1, "missing: tooling-codify-1\n")}): Allow(),
        Input(transcript=reply_transcript(UPDATE), env=DRIVE_CONFIG, commands={CHECK: (1, "missing: tooling-codify-1\n")}): Allow(),
    },
)
def owner_updates_name_every_lane(evt: BaseHookEvent) -> HookResult | None:
    if (entry := drive_entry(evt)) is None:
        return None
    drive = bus_name(entry)
    done = subprocess.run([sys.executable, str(script("owner_update.py")), "--drive", drive, "--check", "-"], input=last_reply(evt), capture_output=True, text=True, timeout=CHECK_SECONDS)
    if done.returncode != 1 or not done.stdout.startswith(MISSING):
        return None
    return evt.context(UNNAMED.format(drive=drive, names=done.stdout.removeprefix(MISSING).strip()))
