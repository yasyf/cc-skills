from __future__ import annotations

from captain_hook import Allow, BaseHookEvent, Event, HookResult, Input, Or, Runs, Tool, Warn, on

from .compaction_handoff import Bound, shared_tests
from .pr_body_frozen import DRIVE, InDrive
from .tests.register_fixtures import ACTIVE, DRIVE_CONFIG

MAKES = (("create",), ("extend",), ("instance", "create"), ("instance", "extend-duration"))
NUDGE = (
    "Warning, nothing was blocked: Namespace belongs to CI, so a box for testing is an EC2 one: `ci devbox`, or `ci mac-box` for a Mac."
    " Extend one a lane still holds with `lr nsc extend <id> --drive <drive> --lane <lane> -- <flags>`, so the supervisor can renew it."
)


def makes_a_box(evt: BaseHookEvent) -> bool:
    return any(call.args[: len(verb)] == verb for call in evt.command.calls("nsc") for verb in MAKES) or any(call.args[:2] == ("nsc", "create") for call in evt.command.calls("lr"))


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash"), Or(Runs("nsc"), Runs("lr", "nsc")), InDrive()],
    tests=shared_tests({
        Input(command="nsc create --duration 8h --machine_type macos/arm64:6x14", env=DRIVE): Warn(pattern=r"`ci devbox`, or `ci mac-box`"),
        Input(command="nsc extend sh4pma4mccmt4 --duration 2h", env=DRIVE): Warn(),
        Input(command="nsc instance create --duration 1h", env=DRIVE): Warn(),
        Input(command="nsc create --duration 1h", env=DRIVE_CONFIG, state=ACTIVE): Warn(),
        Input(command="nsc list -o json", env=DRIVE): Allow(),
        Input(command="nsc ssh sh4pma4mccmt4", env=DRIVE): Allow(),
        Input(command="lr nsc create --drive d --lane l -- --duration 8h", env=DRIVE): Warn(),
        Input(command="lr nsc extend sh4 --drive d --lane l -- --duration 2h", env=DRIVE): Allow(),
        Input(command="nsc create --duration 1h"): Allow(),
    }),
)
def nsc_boxes_go_through_lr(evt: BaseHookEvent) -> HookResult | None:
    return evt.context(NUDGE) if makes_a_box(evt) else None
