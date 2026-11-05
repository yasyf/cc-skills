from __future__ import annotations

from captain_hook import Allow, Annotated, Input, Occurrence, PreToolUseEvent, Rewrite, rewrite_command_occurrences
from captain_hook.bindings import Literal, Ref, segments

from .compaction_handoff import Bound
from .pr_body_frozen import DRIVE

REMOVERS = frozenset({"rm", "rmdir"})
NORMALLY_SET = frozenset(
    {"HOME", "PWD", "OLDPWD", "TMPDIR", "TMP", "TEMP", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR"}
)


def guarded(raw: str) -> str | None:
    match segments(raw):
        case [Ref(name=name, quoted=quoted), Literal(text="/"), *_] if name.isidentifier() and name.upper() not in NORMALLY_SET:
            body = raw.removeprefix('"') if quoted else raw
            spelling = f"${{{name}}}" if body.startswith("${") else f"${name}"
            if not body.startswith(spelling):
                return None
            rest = body.removeprefix(spelling)
            return f'"${{{name}:?}}{rest}' if quoted else f'"${{{name}:?}}"{rest}'
        case _:
            return None


def guard_variables(evt: PreToolUseEvent, occ: Occurrence) -> str | None:
    command = occ.command
    if command.unwrapped.program not in REMOVERS:
        return None
    edits = [(word.span, new) for word in command.unwrapped.words[1:] if word.span is not None and (new := guarded(word.raw)) is not None]
    if not edits:
        return None
    start, end = command.span
    source = occ.line.raw.encode()[start:end]
    for (left, right), new in reversed(edits):
        source = source[: left - start] + new.encode() + source[right - start :]
    return source.decode()


rewrite_command_occurrences(
    to=guard_variables,
    only_if=[Bound()],
    skip_if=[Annotated("raw")],
    note='Rewrote each `rm` path that starts with a variable to `"${VAR:?}"/...`, so Claude Code runs it without its dangerous-rm dialog and an empty variable stops the shell instead of removing from `/`.',
    tests={
        Input(command="D=/tmp/x; rm -f $D/*", env=DRIVE): Rewrite(pattern='D=/tmp/x; rm -f "${D:?}"/*'),
        Input(command="rm -rf ${OUT}/build $LOG/ && ls", env=DRIVE): Rewrite(pattern='rm -rf "${OUT:?}"/build "${LOG:?}"/ && ls'),
        Input(command='rm -f "$D/a b.txt" "$D"/*.log', env=DRIVE): Rewrite(pattern='rm -f "${D:?}/a b.txt" "${D:?}"/*.log'),
        Input(command="sudo rmdir $D/empty", env=DRIVE): Rewrite(pattern='sudo rmdir "${D:?}"/empty'),
        Input(command='rm -f "${D:?}"/*', env=DRIVE): Allow(),
        Input(command="rm -rf ${D:-/tmp/x}/*", env=DRIVE): Allow(),
        Input(command="rm -rf $HOME/*", env=DRIVE): Allow(),
        Input(command="rm -f $1/* /tmp/x/$D", env=DRIVE): Allow(),
        Input(command="ls $D/*; echo rm $D/*", env=DRIVE): Allow(),
        Input(command="rm -f $D/*  # ccx:raw", env=DRIVE): Allow(),
        Input(command="rm -f $D/*"): Allow(),
    },
)
