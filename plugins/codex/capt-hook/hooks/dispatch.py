from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from captain_hook import (
    Allow,
    Block,
    BaseHookEvent,
    CommandSchema,
    CustomCondition,
    Event,
    Input,
    Operand,
    Option,
    Or,
    Tool,
    ToolInput,
    Warn,
    hook,
)
from captain_hook.command_schema import Arguments
from captain_hook.types import TOOL_EVENTS, Command as CommandCondition

from .shell import Detached, Invokes, InvokesProseWrapper, LaneSelected, expanded_calls, head_name, runs_prose_wrapper

PLUGIN_BIN = str(Path(__file__).resolve().parents[2] / "bin" / "codex-ask")

EXEC_SUBCOMMANDS = frozenset({"exec", "e"})
HANDROLLED_FORMAT = (
    r"(?i)reply as a finding list"
    r"|verdict\s*\(\s*lgtm"
    r"|answer each with a verdict"
    r"|verdict \+ file:line"
    r"|report\s+(?:the\s+)?findings\s+as\b[^\n]{0,40}\bjson"
    r"|output format\s*:"
)
CODEX = CommandSchema(
    "codex",
    operands=(Operand("subcommand"), Operand("rest", count="*")),
    options=(
        Option(
            "value",
            (
                "-c", "--config", "--enable", "--disable", "--remote", "--remote-auth-token-env",
                "-m", "--model", "--local-provider", "-p", "--profile", "-s", "--sandbox",
                "-C", "--cd", "--add-dir", "-a", "--ask-for-approval",
            ),
        ),
        Option(
            "flag",
            (
                "--oss", "--full-auto", "--dangerously-bypass-approvals-and-sandbox", "--search", "--no-alt-screen",
                "--strict-config", "--approve-for-me", "--dangerously-bypass-hook-trust", "--worktree",
            ),
            bool,
        ),
    ),
    operands_end_options=True,
)

RETRO = "/p/incident-retro/scripts/retro.py"
DESIGN = "/p/design-doc/scripts/design.py"


@dataclass(frozen=True)
class SubcommandIn:
    names: frozenset[str]

    def __call__(self, arguments: Arguments) -> bool:
        return bool(subcommand := arguments.values["subcommand"]) and subcommand[0] in self.names


@dataclass(frozen=True)
class CodexExecDirect(CustomCondition):
    valid_events = TOOL_EVENTS

    def check(self, evt: BaseHookEvent) -> bool:
        return any(
            SubcommandIn(EXEC_SUBCOMMANDS)(CODEX.bind(call))
            for call, _ in expanded_calls(evt.command)
            if call.name == "codex"
        )


codex_ask_detached = Detached(lambda call: head_name(call) == "codex-ask")
wrapper_detached = Detached(runs_prose_wrapper)

hook(
    Event.PreToolUse,
    only_if=[
        Tool("Bash"),
        Invokes("codex-ask"),
        Or(ToolInput(run_in_background="true"), codex_ask_detached),
    ],
    message=(
        "Run `codex-ask` in the foreground, never with `&`, `nohup`, `setsid`, or `run_in_background`. "
        f"For async work run `{PLUGIN_BIN} --dispatch --owner <agent-id>` and park on the await tool."
    ),
    block=True,
    tests={
        Input(command="codex-ask x & echo launched"): Block(pattern="foreground"),
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
        Input(command="setsid nohup codex-ask review"): Block(),
        Input(command="setsid bash -c 'codex-ask x &'"): Block(),
        Input(command="codex-ask x; sleep 5 &"): Allow(),
        Input(command='echo "codex-ask &"'): Allow(),
        Input(command="codex-ask 'compare foo &'"): Allow(),
        Input(
            command="grep codex-ask notes.md",
            tool_input={"command": "grep codex-ask notes.md", "run_in_background": True},
        ): Allow(),
        Input(command="codex-ask -s /tmp/x/lane - <<'Q'\nreview this diff\nQ"): Allow(),
        Input(command="codex-ask -l r - <<'Q'\nshards per pool (`Option<&Bucket>`)\nQ"): Allow(),
        Input(command="codex-ask -l r - <<'Q'\ngrow & shed races under the mutex\nQ"): Allow(),
        Input(command="cd /tmp && codex-ask -l r - <<'Q'\ngrow & shed\nQ"): Allow(),
        Input(command="codex-ask -l r - <<'Q'\ngrow & shed\nQ\ncodex-ask -l s - <<'R'\nfoo & bar\nR"): Allow(),
        Input(command="codex-ask -l r - <<'Q'\ngrow & shed\nQ &"): Block(),
        Input(
            command="cat > /n/ask.md <<'EOF'\nSubject `stack: 🐛 <what>`\nEOF\n"
            "cd /w && codex-ask -m astra /n/ask.md 2>&1 | grep AWAIT"
        ): Allow(),
        Input(command="echo 🐛 && codex-ask -m astra /n/ask.md 2>&1 | grep AWAIT"): Allow(),
        Input(command="echo 🐛 && codex-ask -m astra /n/ask.md >log 2>&1 &"): Block(),
        Input(command="codex-ask --dispatch --owner agent-1 'summarize the diff'"): Allow(),
        Input(command="codex-ask --watch --all"): Allow(),
        Input(command="codex-ask --dispatch --owner agent-1 'summarize the diff' &"): Block(),
        Input(command="codex-ask --await /tmp/x/lane && echo done"): Allow(),
        Input(command="grep codex-ask notes.md"): Allow(),
        Input(command="sleep 5 &"): Allow(),
    },
)

hook(
    Event.PreToolUse,
    only_if=[
        Tool("Bash"),
        InvokesProseWrapper(),
        Or(ToolInput(run_in_background="true"), wrapper_detached),
    ],
    message=(
        "Run `retro.py prose` and `design.py plainify` in the foreground, never with `&`, `nohup`, "
        "`setsid`, or `run_in_background`. For a long prose run use `retro.py prose <dir> --detach`, "
        "then rerun `retro.py prose <dir> --await` in the foreground."
    ),
    block=True,
    tests={
        Input(
            command=f"R={RETRO}; D=/r/x; python3 $R prose $D --stale > log 2>&1; echo EXIT $?",
            tool_input={
                "command": f"R={RETRO}; D=/r/x; python3 $R prose $D --stale > log 2>&1; echo EXIT $?",
                "run_in_background": True,
            },
        ): Block(pattern="--detach"),
        Input(command=f'RETRO="python3 {RETRO}"; $RETRO prose /r/x &'): Block(),
        Input(command=f"D=/r/x R={RETRO}; python3 ${{R}} prose $D &"): Block(),
        Input(command=f"nohup python3 {RETRO} prose /r/x"): Block(),
        Input(command=f"python3 {DESIGN} plainify /d/x &"): Block(),
        Input(command=f"bash -c 'python3 {RETRO} prose /r/x &'"): Block(),
        Input(command=f"{RETRO} prose /r/x --detach &"): Block(),
        Input(command=f"python3 {RETRO} prose /r/x --detach"): Allow(),
        Input(command=f"python3 {RETRO} prose /r/x --await"): Allow(),
        Input(command=f"python3 {RETRO} prose /r/x --stale"): Allow(),
        Input(
            command=f"python3 {RETRO} check /r/x",
            tool_input={"command": f"python3 {RETRO} check /r/x", "run_in_background": True},
        ): Allow(),
        Input(command=f"grep -n prose {RETRO} &"): Allow(),
        Input(command=f"sed -n 1,40p {RETRO} prose &"): Allow(),
        Input(command=f"python3 {DESIGN} render /d/x &"): Allow(),
        Input(
            command=f"uv run {DESIGN} plainify /d/x",
            tool_input={"command": f"uv run {DESIGN} plainify /d/x", "run_in_background": True},
        ): Block(),
        Input(command="python3 $SKILL/scripts/retro.py prose /r/x > log 2>&1 &"): Block(),
        Input(
            command='UV_NO_CACHE=1 timeout 600 "$H" package-install > /tmp/x.log 2>&1',
            tool_input={
                "command": 'UV_NO_CACHE=1 timeout 600 "$H" package-install > /tmp/x.log 2>&1',
                "run_in_background": True,
            },
        ): Allow(),
        Input(command='(UV_NO_CACHE=1 timeout 600 "$H" package-install > /tmp/x.log 2>&1) &'): Allow(),
        Input(
            command="uv run pytest -q > /s/pytest.log 2>&1; echo EXIT=$? >> /s/pytest.log",
            tool_input={
                "command": "uv run pytest -q > /s/pytest.log 2>&1; echo EXIT=$? >> /s/pytest.log",
                "run_in_background": True,
            },
        ): Allow(),
        Input(command='(uv run pytest -q > $LOG 2>&1; echo "EXIT=$?" >> $LOG) & echo started'): Allow(),
    },
)

hook(
    Event.PreToolUse,
    only_if=[Tool("Bash"), CodexExecDirect()],
    message=(
        "Route every codex dispatch through `codex-ask`, never `codex exec`. "
        f"Rerun as `{PLUGIN_BIN} [-s <lane>] - <<'Q'` in the foreground."
    ),
    block=True,
    tests={
        Input(command="codex exec -c model=gpt-6-astra -c model_reasoning_effort=xhigh review"): Block(
            pattern="codex-ask"
        ),
        Input(command="codex -c model=y exec review"): Block(),
        Input(command="codex --oss e review"): Block(),
        Input(command="codex --strict-config exec review"): Block(),
        Input(command="setsid codex exec review"): Block(),
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

hook(
    Event.PreToolUse,
    only_if=[Tool("Bash"), Invokes("codex-ask"), CommandCondition(HANDROLLED_FORMAT)],
    skip_if=[LaneSelected()],
    message=(
        "This prompt restates a reply format the plugin already ships. "
        "Pass `--lane <name>` (review, refute, security, diagnose, implement, recon) to sharpen it."
    ),
    tests={
        Input(
            command="codex-ask -s /tmp/x/lane - <<'Q'\nReview this diff. Reply as a finding list: "
            "VERDICT (LGTM / ISSUE) per question, each issue with file:line and a concrete fix.\nQ"
        ): Warn(pattern="--lane"),
        Input(
            command="codex-ask -s /tmp/x/lane - <<'Q'\nQuestions (answer each with a verdict + "
            "file:line evidence): does the retry loop terminate?\nQ"
        ): Warn(),
        Input(command="codex-ask -s /tmp/x/lane - <<'Q'\nOutput format: one line per symbol.\nQ"): Warn(),
        Input(
            command="codex-ask --lane review -s /tmp/x/lane - <<'Q'\nReply as a finding list: "
            "VERDICT (LGTM / ISSUE) per question.\nQ"
        ): Allow(),
        Input(command="codex-ask -s /tmp/x/lane - <<'Q'\nreview this diff\nQ"): Allow(),
        Input(command="codex-ask 'why does the retry loop hang?'"): Allow(),
        Input(command="grep 'output format:' notes.md"): Allow(),
        Input(
            command="codex-ask -s /tmp/x/lane - <<'Q'\nFix the parser. Reply with ONLY the "
            "edited function.\nQ"
        ): Allow(),
    },
)
