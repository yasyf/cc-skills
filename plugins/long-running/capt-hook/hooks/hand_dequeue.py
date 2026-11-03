from __future__ import annotations

from captain_hook import Allow, Event, Input, Warn, warn_command

from .pr_body_frozen import DRIVE, InDrive

HAND_DEQUEUE = r"(?:^|[\s;&|(/])stack-enqueue\b[^\n;&|]*\s--dequeue\b"

warn_command(
    HAND_DEQUEUE,
    message="Warning, nothing was blocked: a hand `stack-enqueue --dequeue` leaves no record, so the supervisor reads it as a Trunk eviction and resubmits the head. Post `cci post --kind hold --pr <pr>` instead: the next pass takes a held head out of the queue, and resolving the hold lets it back in.",
    only_if=[InDrive()],
    events=Event.PreToolUse,
    tests={
        Input(command=".agents/skills/submit-pr/scripts/stack-enqueue 46478 --dequeue", env=DRIVE): Warn(pattern=r"cci post --kind hold --pr"),
        Input(command="cd ~/monorepo && stack-enqueue --dequeue 46464", env=DRIVE): Warn(),
        Input(command=".agents/skills/submit-pr/scripts/stack-enqueue 46478 --check", env=DRIVE): Allow(),
        Input(command="cci post --kind hold --pr 46478 'restack'", env=DRIVE): Allow(),
        Input(command="stack-enqueue 46478 --dequeue"): Allow(),
    },
)
