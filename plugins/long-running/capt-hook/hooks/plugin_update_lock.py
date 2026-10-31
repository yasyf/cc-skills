from __future__ import annotations

import sys

from captain_hook import Allow, Annotated, Input, Occurrence, PreToolUseEvent, Rewrite, rewrite_command_occurrences

LOCKER = "/usr/bin/lockf -k" if sys.platform == "darwin" else "flock"
LOCKED = f'{LOCKER} "$HOME/.claude/plugins/.update.lock"'
CACHE_WRITES = (("plugin", "update"), ("plugin", "install"), ("plugin", "marketplace", "update"))


def serialized(evt: PreToolUseEvent, occ: Occurrence) -> str | None:
    command = occ.command
    if command.executable != "claude" or not any(command.args[: len(verb)] == verb for verb in CACHE_WRITES):
        return None
    return f"{LOCKED} {command.raw}"


rewrite_command_occurrences(
    to=serialized,
    skip_if=[Annotated("raw")],
    note="Plugin installs and updates run one at a time under a lock on `~/.claude/plugins/.update.lock`, so a marketplace re-clone never interleaves with a cache copy.",
    tests={
        Input(command="claude plugin marketplace update skills; claude plugin update long-running@skills --scope user"): Rewrite(
            pattern=f"{LOCKED} claude plugin marketplace update skills; {LOCKED} claude plugin update long-running@skills --scope user"
        ),
        Input(command="claude plugin install codex@skills 2>&1 | tail -1"): Rewrite(pattern=f"{LOCKED} claude plugin install codex@skills"),
        Input(command="claude plugin list"): Allow(),
        Input(command=f"{LOCKED} claude plugin update long-running@skills"): Allow(),
        Input(command="claude plugin update long-running@skills  # ccx:raw"): Allow(),
    },
)
