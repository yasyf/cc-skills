from __future__ import annotations

from captain_hook import Allow, BaseHookEvent, Event, HookResult, Input, Runs, Tool, Warn, on

from .compaction_handoff import shared_tests
from .pr_body_frozen import DRIVE, InDrive
from .tests.register_fixtures import ACTIVE, DRIVE_CONFIG

MAKES = (("create",), ("extend",), ("instance", "create"), ("instance", "extend-duration"))
NUDGE = (
    "Warning, nothing was blocked: a drive makes and extends Namespace boxes with"
    " `lr nsc create --drive <drive> --lane <lane> -- <nsc create flags>` and"
    " `lr nsc extend <id> --drive <drive> --lane <lane> -- <nsc extend flags>`."
    " They label the box with its lane and record its deadline on the bus, so the supervisor warns that lane an hour before the box dies."
)


def makes_a_box(evt: BaseHookEvent) -> bool:
    return any(call.args[: len(verb)] == verb for call in evt.command.calls("nsc") for verb in MAKES)


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash"), Runs("nsc"), InDrive()],
    tests=shared_tests({
        Input(command="nsc create --duration 8h --machine_type macos/arm64:6x14", env=DRIVE): Warn(pattern=r"lr nsc create --drive <drive> --lane <lane>"),
        Input(command="nsc extend sh4pma4mccmt4 --duration 2h", env=DRIVE): Warn(),
        Input(command="nsc instance create --duration 1h", env=DRIVE): Warn(),
        Input(command="nsc create --duration 1h", env=DRIVE_CONFIG, state=ACTIVE): Warn(),
        Input(command="nsc list -o json", env=DRIVE): Allow(),
        Input(command="nsc ssh sh4pma4mccmt4", env=DRIVE): Allow(),
        Input(command="lr nsc create --drive d --lane l -- --duration 8h", env=DRIVE): Allow(),
        Input(command="nsc create --duration 1h"): Allow(),
    }),
)
def nsc_boxes_go_through_lr(evt: BaseHookEvent) -> HookResult | None:
    return evt.context(NUDGE) if makes_a_box(evt) else None
