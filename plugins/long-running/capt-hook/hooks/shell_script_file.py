from __future__ import annotations

import re

from captain_hook import Allow, BaseHookEvent, Event, HookResult, Input, Tool, Warn, on

from .pr_body_frozen import DRIVE, InDrive

LITERAL = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?^\s*\2\s*$|'[^']*'|\"(?:\\.|[^\"\\])*\"", re.DOTALL | re.MULTILINE)
UNPARSED_SHELL = re.compile(r"(?:^|[;&|(]\s*)(?:for|while|until)\b[^\n]*?;\s*do\b|\$\{[(=]|(?:^|[;&|{]\s*):\s*>|\btypeset\s+-A\b", re.MULTILINE)
SCRIPT_FILE = "Claude Code cannot parse a shell loop or a zsh `${(...)}`, `${=...}` or `: >` form, so the call waits on an approval dialog. Write the logic to a script file and run `zsh <file>`."
HEREDOC_SCRIPT = "cat > /tmp/r.zsh <<'EOF'\nfor k in A B; do echo $k; done\nEOF\nzsh /tmp/r.zsh"


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash"), InDrive()],
    tests={
        Input(command="for k in A B C; do echo $k; done", env=DRIVE): Warn(pattern=r"zsh <file>"),
        Input(command="git status; while IFS== read k v; do N[$k]=$v; done < /tmp/r.txt", env=DRIVE): Warn(),
        Input(command="o=${(P)k}; echo $o", env=DRIVE): Warn(),
        Input(command="git rev-parse HEAD; : > /tmp/s8-replay.txt", env=DRIVE): Warn(),
        Input(command="git rev-parse HEAD && git log --oneline -3", env=DRIVE): Allow(),
        Input(command="zsh /tmp/s8-replay.zsh", env=DRIVE): Allow(),
        Input(command=HEREDOC_SCRIPT, env=DRIVE): Allow(),
        Input(command="cci post --kind state --text 'Status; for each PR; do not merge'", env=DRIVE): Allow(),
        Input(command="printf '%s\\n' '${(P)k}'", env=DRIVE): Allow(),
        Input(command="for k in A B C; do echo $k; done"): Allow(),
    },
)
def shell_script_file(evt: BaseHookEvent) -> HookResult | None:
    return evt.warn(SCRIPT_FILE) if UNPARSED_SHELL.search(LITERAL.sub("''", evt.input.raw.get("command") or "")) else None
