from __future__ import annotations

import re

from cc_transcript.command import Command, parse_command_line

from captain_hook import (
    Allow,
    Block,
    BaseHookEvent,
    CustomCommandLineCondition,
    Event,
    Input,
    Or,
    Tool,
    ToolInput,
    hook,
)
from captain_hook.util.shell import normalize_executable

# Backstop for natural agent-written forms only. Launchers that detach with no shell `&` on the
# command line (screen/tmux/coproc) and wrapper scripts that hide the codex call are out of scope.

DETACH_WRAPPERS = frozenset({"nohup", "setsid"})
EXEC_SUBCOMMANDS = frozenset({"exec", "e"})  # `e` is codex's documented alias for `exec`.
# codex global flags taking a separate value token; skipped so `codex -c model=x exec …` still
# resolves `exec` as the subcommand (`-i/--image` is nargs and omitted — skipping one errs to allow).
CODEX_VALUE_FLAGS = frozenset(
    {
        "-c", "--config", "--enable", "--disable", "--remote", "--remote-auth-token-env",
        "-m", "--model", "--local-provider", "-p", "--profile", "-s", "--sandbox",
        "-C", "--cd", "--add-dir", "-a", "--ask-for-approval",
    }
)
SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh", "ash", "fish", "csh", "tcsh"})
NESTED_DEPTH = 3


def safe_parse(text: str):
    try:
        return parse_command_line(text)
    except RecursionError:
        return None


def nested_payload(program: str, args: tuple[str, ...]) -> str | None:
    """The command string from a shell ``-c``/``-…c`` cluster or ``eval …`` join, else ``None``."""
    if program in SHELLS:
        return next((args[i + 1] for i, a in enumerate(args) if i + 1 < len(args) and re.fullmatch(r"-[a-z]*c", a)), None)
    if program == "eval":
        return " ".join(args) or None
    return None


def unwrapped_argv(cmd: Command) -> tuple[str, ...]:
    """``cmd.unwrapped.argv`` (env/timeout/nohup/sudo/xargs stripped natively), also unwrapping a
    path-qualified/quoted wrapper head and ``setsid`` (which cc_transcript's .unwrapped leaves)."""
    while True:
        argv = cmd.unwrapped.argv
        if not argv:
            return argv
        head = normalize_executable(argv[0])
        if head != argv[0]:
            cmd = Command(cmd.raw, head, argv[1:])
        elif head == "setsid" and (rest := argv[1:]):
            i = next((k for k, a in enumerate(rest) if not a.startswith("-")), len(rest))
            if i == len(rest):
                return argv
            cmd = Command(cmd.raw, rest[i], rest[i + 1 :])
        else:
            return argv


def head_program(cmd: Command) -> str:
    return normalize_executable(argv[0]) if (argv := unwrapped_argv(cmd)) else ""


def walk_occurrences(cl, depth=NESTED_DEPTH):
    """Yield every command occurrence, descending into ``sh -c '…'``/``eval …`` payloads."""
    for occ in cl.occurrences:
        yield occ
        argv = unwrapped_argv(occ.command)
        if depth > 0 and argv:
            nested = nested_payload(normalize_executable(argv[0]), argv[1:])
            if nested is not None and (inner := safe_parse(nested)) is not None:
                yield from walk_occurrences(inner, depth - 1)


def is_background_amp(occ) -> bool:
    """True when a bare ``&`` (not ``&&``, not a ``2>&1``/``&>`` redirect, not a quoted arg)
    backgrounds this occurrence's command — detected in the raw byte-gap after the command's span
    up to the next command's span (or line end), so a quoted ``&`` inside the span never counts."""
    cmd = occ.command
    if cmd.span is None:
        return False
    occs = occ.line.occurrences
    nxt = occs[occ.index + 1] if occ.index + 1 < len(occs) else None
    end = nxt.command.span[0] if nxt is not None and nxt.command.span is not None else len(occ.line.raw)
    return re.search(r"(?<![>&])&(?![>&])", occ.line.raw[cmd.span[1] : end]) is not None


def codex_subcommand(args: tuple[str, ...]) -> str | None:
    tokens = iter(args)
    for token in tokens:
        if token.startswith("-"):
            if token in CODEX_VALUE_FLAGS and "=" not in token:
                next(tokens, None)
            continue
        return token
    return None


class CodexAskInvoked(CustomCommandLineCondition):
    """A ``codex-ask`` invocation sits in executable position (basename-normalized, wrappers and
    ``sh -c``/``eval`` payloads unwrapped) — so it is being run, not merely named as an argument."""

    def check_command_line(self, evt: BaseHookEvent, cl) -> bool:
        return any(head_program(occ.command) == "codex-ask" for occ in walk_occurrences(cl))


class CodexAskDetached(CustomCommandLineCondition):
    """A ``codex-ask`` invocation is backgrounded by a bare ``&`` associated with its own span, or
    wrapped in a ``nohup``/``setsid`` detacher (``& disown`` is caught by the ``&``)."""

    def check_command_line(self, evt: BaseHookEvent, cl) -> bool:
        return any(
            head_program(occ.command) == "codex-ask"
            and (is_background_amp(occ) or normalize_executable(occ.command.executable) in DETACH_WRAPPERS)
            for occ in walk_occurrences(cl)
        )


class CodexExecDirect(CustomCommandLineCondition):
    """The ``codex`` CLI runs an ``exec``-style dispatch directly (not ``codex-ask``): basename
    normalized, global value-flags skipped to reach the subcommand, wrappers and ``sh -c``/``eval``
    payloads unwrapped. ``codex-ask``'s program basename is ``codex-ask`` (≠ ``codex``), so it is
    excluded; ``codex login``/``resume``/``--version`` resolve to a non-exec subcommand or none."""

    def check_command_line(self, evt: BaseHookEvent, cl) -> bool:
        return any(
            head_program(occ.command) == "codex" and codex_subcommand(unwrapped_argv(occ.command)[1:]) in EXEC_SUBCOMMANDS
            for occ in walk_occurrences(cl)
        )


hook(
    Event.PreToolUse,
    only_if=[
        Tool("Bash"),
        CodexAskInvoked(),
        Or(ToolInput(run_in_background="true"), CodexAskDetached()),
    ],
    message=(
        "codex-ask must run in the FOREGROUND. It already survives a Bash-tool timeout — the "
        "AWAIT: line prints first, so on a slow run rerun that exact line foreground with "
        "timeout: 600000 to recover. Backgrounding it (run_in_background, a trailing &, or "
        "nohup/setsid/disown) strands the finished reply on disk: background Bash completion "
        "never wakes an in-process subagent (anthropics/claude-code#78782). Parallelism comes "
        "from parallel wrapper agents or a workflow fan-out, never from backgrounding."
    ),
    block=True,
    tests={
        Input(command="codex-ask x & echo launched"): Block(pattern="FOREGROUND"),
        Input(command="(codex-ask x >log 2>&1 &)"): Block(),
        Input(
            command="codex-ask -s /tmp/x/lane review",
            tool_input={"command": "codex-ask -s /tmp/x/lane review", "run_in_background": True},
        ): Block(),
        Input(command="nohup codex-ask -s /tmp/x/lane review"): Block(),
        Input(command="setsid codex-ask -s /tmp/x/lane review"): Block(),
        Input(command="codex-ask -s /tmp/x/lane review & disown"): Block(),
        Input(command="codex-ask -s /tmp/x/lane review &"): Block(),
        Input(command="codex-ask -s /tmp/x/lane - <<'Q'\nreview this diff\nQ &"): Block(),
        Input(command="bash -c 'codex-ask x &'"): Block(),
        Input(command="codex-ask x; sleep 5 &"): Allow(),
        Input(command='echo "codex-ask &"'): Allow(),
        Input(command="codex-ask 'compare foo &'"): Allow(),
        Input(
            command="grep codex-ask notes.md",
            tool_input={"command": "grep codex-ask notes.md", "run_in_background": True},
        ): Allow(),
        Input(command="codex-ask -s /tmp/x/lane - <<'Q'\nreview this diff\nQ"): Allow(),
        Input(command="codex-ask --await /tmp/x/lane && echo done"): Allow(),
        Input(command="grep codex-ask notes.md"): Allow(),
        Input(command="sleep 5 &"): Allow(),
    },
)

hook(
    Event.PreToolUse,
    only_if=[Tool("Bash"), CodexExecDirect()],
    message=(
        "Don't call `codex exec` directly — route every codex dispatch through codex-ask. It "
        "pins the model, reasoning effort, service tier, and OAuth auth, feeds "
        "developer_instructions from the plugin AGENTS.md, disables MCP server mounts, and owns "
        "the disk protocol (absolute scratch, staged reply on rc 0, and --await/--collect "
        "recovery). Rerun as `codex-ask [-s <lane>] - <<'Q' … Q` in the foreground."
    ),
    block=True,
    tests={
        Input(command="codex exec -c model=gpt-5.6-sol -c model_reasoning_effort=xhigh review"): Block(
            pattern="codex-ask"
        ),
        Input(command="codex -c model=y exec review"): Block(),
        Input(command="/opt/homebrew/bin/codex exec review"): Block(),
        Input(command="env X=1 codex exec review"): Block(),
        Input(command="timeout 600 codex exec review"): Block(),
        Input(command="bash -c 'codex exec review'"): Block(),
        Input(command="codex login status"): Allow(),
        Input(command="codex resume"): Allow(),
        Input(command="codex --version"): Allow(),
        Input(command="codex-ask -s /tmp/x/lane - <<'Q'\nreview\nQ"): Allow(),
        Input(command="git log -S 'codex exec'"): Allow(),
        Input(command="grep codex-ask notes.md"): Allow(),
    },
)
