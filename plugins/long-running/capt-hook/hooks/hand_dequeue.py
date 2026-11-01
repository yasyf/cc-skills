from __future__ import annotations

from captain_hook import Allow, Event, Input, Warn, warn_command

from .pr_body_frozen import DRIVE, InDrive

HAND_DEQUEUE = r"(?:^|[\s;&|(/])stack-enqueue\b[^\n;&|]*\s--dequeue\b"

warn_command(
    HAND_DEQUEUE,
    message="Warning, nothing was blocked: a hand `stack-enqueue --dequeue` leaves no record, so the landing runner and every eviction reader take the cancel for a Trunk eviction. Dequeue with `mq.py --config <runner.json> dequeue <pr> --reason '<why>'`, which records it.",
    only_if=[InDrive()],
    events=Event.PreToolUse,
    tests={
        Input(command=".agents/skills/submit-pr/scripts/stack-enqueue 46478 --dequeue", env=DRIVE): Warn(pattern=r"mq\.py --config <runner\.json> dequeue"),
        Input(command="cd ~/monorepo && stack-enqueue --dequeue 46464", env=DRIVE): Warn(),
        Input(command=".agents/skills/submit-pr/scripts/stack-enqueue 46478 --check", env=DRIVE): Allow(),
        Input(command="mq.py --config runner.json dequeue 46478 --reason 'restack'", env=DRIVE): Allow(),
        Input(command="stack-enqueue 46478 --dequeue"): Allow(),
    },
)
