from __future__ import annotations

from captain_hook import Allow, Annotated, Block, Event, Input, Warn, block_command, warn_command

from .compaction_handoff import Bound
from .pr_body_frozen import DRIVE, InDrive

UNPARSED_SHELL = r"\A(?![\s\S]*<<)[\s\S]*?(?:(?:\A|[;&|(]\s*)(?:for\s+\w+\s+in\b|while\b|until\b)[^\n]*?;\s*do\b|\$\{[(=]|(?:\A|[;&|{]\s*):\s*>|\btypeset\s+-A\b)"
DOLLAR_PID_OPERAND = r"\$\$[({\[]"
BUILDKITE_EDIT = "cat > /tmp/edit.py <<'EOF'\nstep = 'KEY=$$(ci image key) bake.sh'\nEOF\npython3 /tmp/edit.py"
HEREDOC_SCRIPT = "cat > /tmp/r.zsh <<'EOF'\ntypeset -A N\nfor k in A B; do N[$k]=${(P)k}; done\n: > /tmp/out\nEOF\nzsh /tmp/r.zsh"

warn_command(
    UNPARSED_SHELL,
    message="Claude Code cannot parse a shell loop or a zsh `${(...)}`, `${=...}` or `: >` form, so the call waits on an approval dialog. Write the logic to a script file and run `zsh <file>`.",
    only_if=[Bound(), InDrive()],
    events=Event.PreToolUse,
    tests={
        Input(command="for k in A B C; do echo $k; done", env=DRIVE): Warn(pattern=r"zsh <file>"),
        Input(command="git status; while IFS== read k v; do N[$k]=$v; done < /tmp/r.txt", env=DRIVE): Warn(),
        Input(command="o=${(P)k}; echo $o", env=DRIVE): Warn(),
        Input(command="git rev-parse HEAD; : > /tmp/s8-replay.txt", env=DRIVE): Warn(),
        Input(command="git rev-parse HEAD && git log --oneline -3", env=DRIVE): Allow(),
        Input(command="zsh /tmp/s8-replay.zsh", env=DRIVE): Allow(),
        Input(command=HEREDOC_SCRIPT, env=DRIVE): Allow(),
        Input(command="cci post --kind state --text 'Status; for each PR; do not merge'", env=DRIVE): Allow(),
        Input(command="for k in A B C; do echo $k; done"): Allow(),
    },
)

block_command(
    DOLLAR_PID_OPERAND,
    reason="Claude Code reads `$$(`, `$${` or `$$[` anywhere in a Bash command, heredoc bodies and quoted strings included, as a shell divergence and holds the call on a dangerous-rm dialog",
    hint="Write the file with the Write tool, then run it with a Bash command free of them",
    only_if=[Bound(), InDrive()],
    skip_if=[Annotated("raw")],
    tests={
        Input(command=BUILDKITE_EDIT, env=DRIVE): Block(pattern=r"Write tool"),
        Input(command="echo '$${FORGE_KEY}'", env=DRIVE): Block(),
        Input(command="printf %s '$$[x]'", env=DRIVE): Block(),
        Input(command="python3 /tmp/edit.py && echo $$ \"$$\"", env=DRIVE): Allow(),
        Input(command=HEREDOC_SCRIPT, env=DRIVE): Allow(),
        Input(command=BUILDKITE_EDIT): Allow(),
    },
)
