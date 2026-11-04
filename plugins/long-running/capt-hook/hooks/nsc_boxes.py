from __future__ import annotations

from typing import Any

from captain_hook import Allow, BaseHookEvent, Event, HookResult, Input, Or, Runs, Tool, Warn, on

from .compaction_handoff import Bound, shared_tests
from .pr_body_frozen import DRIVE, InDrive
from .tests.register_fixtures import ACTIVE, DRIVE_CONFIG

MAKES = (("create",), ("extend",), ("instance", "create"), ("instance", "extend-duration"))
NUDGE = (
    "Warning, nothing was blocked: make and extend Namespace boxes with `lr nsc create --drive <drive> --lane <lane> -- <flags>`"
    " and `lr nsc extend <id> --drive <drive> --lane <lane> -- <flags>`, which label the box and record its deadline so the supervisor can renew it."
)
PLAIN_MAC = (
    "Warning, nothing was blocked: a plain Namespace Mac counts against CI's limit."
    " Use a Namespace devbox, Linux or Mac, with `lr devbox create --drive <drive> --lane <lane> -- --platform macos/arm64 --size m`, or an EC2 Mac from `ci mac-box`."
)
DEVBOX_NUDGE = (
    "Warning, nothing was blocked: make a Namespace devbox with `lr devbox create --drive <drive> --lane <lane> -- <flags>`,"
    " which names it for the lane and records who holds it."
)


def makes_a_devbox(evt: BaseHookEvent) -> bool:
    return any(call.args[:1] == ("create",) for call in evt.command.calls("devbox"))


def makes_a_box(evt: BaseHookEvent) -> bool:
    return any(call.args[: len(verb)] == verb for call in evt.command.calls("nsc") for verb in MAKES)


def asks_for_a_mac(call: Any) -> bool:
    return any("macos" in arg for arg in call.args)


def makes_a_plain_mac(evt: BaseHookEvent) -> bool:
    through_lr = any(call.args[:2] == ("nsc", "create") and asks_for_a_mac(call) for call in evt.command.calls("lr"))
    return through_lr or (makes_a_box(evt) and any(asks_for_a_mac(call) for call in evt.command.calls("nsc")))


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash"), Or(Runs("nsc"), Runs("devbox"), Runs("lr", "nsc")), InDrive()],
    tests=shared_tests({
        Input(command="nsc create --duration 8h --machine_type macos/arm64:6x14", env=DRIVE): Warn(pattern=r"plain Namespace Mac"),
        Input(command="nsc create --duration 8h --machine_type linux/amd64:4x8", env=DRIVE): Warn(pattern=r"lr nsc create --drive <drive> --lane <lane>"),
        Input(command="nsc devbox create --machine_type macos/arm64:6x14", env=DRIVE): Allow(),
        Input(command="devbox create --platform macos/arm64 --size m", env=DRIVE): Warn(pattern=r"lr devbox create --drive <drive> --lane <lane>"),
        Input(command="devbox list -o json", env=DRIVE): Allow(),
        Input(command="lr devbox create --drive d --lane l -- --platform macos/arm64 --size m", env=DRIVE): Allow(),
        Input(command="nsc extend sh4pma4mccmt4 --duration 2h", env=DRIVE): Warn(),
        Input(command="nsc instance create --duration 1h", env=DRIVE): Warn(),
        Input(command="nsc create --duration 1h", env=DRIVE_CONFIG, state=ACTIVE): Warn(),
        Input(command="nsc list -o json", env=DRIVE): Allow(),
        Input(command="nsc ssh sh4pma4mccmt4", env=DRIVE): Allow(),
        Input(command="lr nsc create --drive d --lane l -- --duration 8h", env=DRIVE): Allow(),
        Input(command="lr nsc create --drive d --lane l -- --machine_type macos/arm64:6x14", env=DRIVE): Warn(pattern=r"plain Namespace Mac"),
        Input(command="lr nsc extend sh4 --drive d --lane l -- --duration 2h", env=DRIVE): Allow(),
        Input(command="nsc create --duration 1h"): Allow(),
    }),
)
def nsc_boxes_go_through_lr(evt: BaseHookEvent) -> HookResult | None:
    if makes_a_plain_mac(evt):
        return evt.context(PLAIN_MAC)
    if makes_a_devbox(evt):
        return evt.context(DEVBOX_NUDGE)
    return evt.context(NUDGE) if makes_a_box(evt) else None
