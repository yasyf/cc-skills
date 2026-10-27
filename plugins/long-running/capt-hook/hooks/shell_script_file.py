from __future__ import annotations

from captain_hook import Allow, Event, Input, Warn, warn_command

from .pr_body_frozen import DRIVE, InDrive

UNPARSED_SHELL = r"(?:^|[;&|(]\s*)(?:for|while|until)\b[^\n]*?;\s*do\b|\$\{[(=]|(?:^|[;&|{]\s*):\s*>|\btypeset\s+-A\b"

warn_command(
    UNPARSED_SHELL,
    message="Claude Code cannot parse a shell loop or a zsh `${(...)}`, `${=...}` or `: >` form, so the call waits on an approval dialog. Write the logic to a script file and run `zsh <file>`.",
    only_if=[InDrive()],
    events=Event.PreToolUse,
    tests={
        Input(command="for k in A B C; do echo $k; done", env=DRIVE): Warn(pattern=r"zsh <file>"),
        Input(command="git status; while IFS== read k v; do N[$k]=$v; done < /tmp/r.txt", env=DRIVE): Warn(),
        Input(command="o=${(P)k}; echo $o", env=DRIVE): Warn(),
        Input(command="git rev-parse HEAD; : > /tmp/s8-replay.txt", env=DRIVE): Warn(),
        Input(command="git rev-parse HEAD && git log --oneline -3", env=DRIVE): Allow(),
        Input(command="zsh /tmp/s8-replay.zsh", env=DRIVE): Allow(),
        Input(command="for k in A B C; do echo $k; done"): Allow(),
    },
)
