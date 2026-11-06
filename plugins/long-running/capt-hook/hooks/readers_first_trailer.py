from __future__ import annotations

import re
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Event, HookResult, Input, Tool, Warn, on

from .compaction_handoff import Bound
from .lane_landing import DRIVE_ENV
from .refusal_kinds_need_owner_answer import body_text, request

BODIES = Path(__file__).parent / "tests" / "fixtures" / "readers-first"
PROSE, TRAILER, PLAIN = BODIES / "prose.md", BODIES / "trailer.md", BODIES / "plain.md"
SAYS_READERS_FIRST = re.compile(r"\breaders?[- ]first\b", re.IGNORECASE)
TRAILER_LINE = re.compile(r"^Readers-First:[ \t]*#\d+", re.IGNORECASE | re.MULTILINE)
WARNING = (
    "Warning, nothing was blocked: this body says reader-first in prose, but the landing pass holds a writer only on a `Readers-First: #<reader PR>` line."
    " Add that line for each reader so lr holds the writer until a live release carries the reader."
)


def ship(command: str) -> Input:
    return Input(command=command, env=DRIVE_ENV, cwd=str(BODIES))


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash")],
    tests={
        ship(f"ccx vcs ship -m 'sand-next: 🐛 persist the pfor limits' --pr-body-file {PROSE}"): Warn(
            pattern=r"^Warning, nothing was blocked: this body says reader-first in prose[\s\S]*`Readers-First: #<reader PR>`"
        ),
        ship(f"gh pr edit 51547 --body-file {PROSE}"): Warn(pattern="Readers-First: #<reader PR>"),
        ship("gh pr edit 51547 --body 'This is the writer half; it lands readers first, a release after #51542.'"): Warn(pattern="Readers-First: #<reader PR>"),
        ship(f"ccx vcs ship --pr-body-file {TRAILER}"): Allow(),
        ship(f"ccx vcs ship --pr-body-file {PLAIN}"): Allow(),
        ship(f"ccx vcs ship --no-pr --pr-body-file {PROSE}"): Allow(),
        Input(command=f"ccx vcs ship --pr-body-file {PROSE}", cwd=str(BODIES)): Allow(),
    },
)
def readers_first_trailer(evt: BaseHookEvent) -> HookResult | None:
    for call in evt.cmd.calls():
        req = request(evt, call, None)
        if req and (req.bodies or req.texts) and (body := body_text(req, {})) and SAYS_READERS_FIRST.search(body) and not TRAILER_LINE.search(body):
            return evt.context(WARNING)
    return None
