from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from captain_hook import Allow, BaseHookEvent, Block, Event, Input, Tool, ast_grep, hook
from captain_hook.ast_grep import SyntaxNode
from captain_hook.cmd import Cmd

from .compaction_handoff import Bound, CompactionState
from .pr_body_frozen import DRIVE

if TYPE_CHECKING:
    from captain_hook.cmd import Call

LOOPS = frozenset({"while_statement", "for_statement", "c_style_for_statement"})
SHELLS = frozenset({"sh", "bash", "zsh", "dash"})
SCRIPTS = frozenset({".sh", ".bash", ".zsh"})
STREAM_READERS = frozenset({"read", "getopts"})
SLEEP = re.compile(r"(\d+(?:\.\d+)?)([smhd]?)")
SECONDS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
LONG_SLEEP = 60
PAYLOAD_FLAG = re.compile(r"-[a-z]*c")
PR_WATCH_SCRIPTS = frozenset({"pr-poll.sh", "pr-watch-armed.sh"})
POLL = "PR_POLL_WINDOW=40 bash /p/open-pr/0.6.8/scripts/pr-poll.sh acme/widgets 7 /c/pr/7.json"
UNTIL = "until grep -q done out.txt; do sleep 5; done"
ACTIVE = [CompactionState(active=True, communicator=True)]


def command_names(nodes: list[SyntaxNode]) -> set[str]:
    return {below.text for node in nodes for below in node.descendants() if below.kind == "command_name"}


def counts_up(loop: SyntaxNode, head: list[SyntaxNode]) -> bool:
    items = [node.text for node in head[3:] if node.kind != ";"]
    return loop.kind == "c_style_for_statement" or bool(items) and all(item.isdigit() or item.startswith("{") or "seq" in item for item in items)


def polls(loop: SyntaxNode) -> bool:
    children = [SyntaxNode(child) for child in loop.raw.children()]
    head = [node for node in children if node.kind != "do_group"]
    body = [node for node in children if node.kind == "do_group"]
    if children[0].kind == "until":
        return True
    if loop.kind == "while_statement":
        return not command_names(head) & STREAM_READERS and "shift" not in command_names(body)
    return "sleep" in command_names(body) and counts_up(loop, head)


def sleeps_long(call: Call) -> bool:
    return not call.args or any(not (length := SLEEP.fullmatch(arg)) or float(length[1]) * SECONDS[length[2]] >= LONG_SLEEP for arg in call.args)


def waits(source: str) -> bool:
    parsed = Cmd.parse(source)
    if parsed is None:
        return False
    return any(sleeps_long(call) for call in parsed.calls("sleep")) or any(node.kind in LOOPS and polls(node) for node in ast_grep.parse(source, "bash").descendants())


def script_text(call: Call) -> str | None:
    flag = next((index for index, arg in enumerate(call.args) if PAYLOAD_FLAG.fullmatch(arg)), None)
    if call.name in SHELLS and flag is not None:
        return call.args[flag + 1]
    operand = next((arg for arg in call.args if not arg.startswith("-")), None) if call.name in SHELLS else call.command.executable
    path = (call.cwd or Path()) / operand if operand else None
    if path is None or path.name in PR_WATCH_SCRIPTS or not path.is_file() or any((parent / ".git").exists() for parent in path.resolve().parents):
        return None
    return path.read_text()


def runs_a_wait(command: Cmd) -> bool:
    shells = [call for call in command.calls() if call.name in SHELLS or Path(call.name).suffix in SCRIPTS]
    written = [text for text in map(script_text, shells) if text]
    stdin = [node.text for node in ast_grep.parse(command.raw, "bash").descendants() if node.kind == "heredoc_body"] if shells else []
    return any(waits(source) for source in (command.raw, *written, *stdin))


class ShellWait:
    def check(self, evt: BaseHookEvent) -> bool:
        return runs_a_wait(evt.command)


hook(  # capt-hook: no-escape the owner asked for a block on every shell wait, and a Monitor command is the route that stays open
    Event.PreToolUse,
    message="A Bash call never waits in a shell loop, sleeper or script. End the turn on the Agent or `Monitor` notification, run the wait as a `Monitor` command for an event stream, or use `CronCreate` for a clock.",
    only_if=[Tool("Bash"), Bound(), ShellWait()],
    block=True,
    tests={
        Input(command=UNTIL, env=DRIVE): Block(pattern=r"CronCreate"),
        Input(tool="Bash", tool_input={"command": UNTIL, "run_in_background": True}, env=DRIVE): Block(),
        Input(command=UNTIL, state=ACTIVE): Block(),
        Input(command=UNTIL, agent_id="a1b2c3", env=DRIVE): Block(),
        Input(command="while true; do gh pr checks 12; sleep 30; done", env=DRIVE): Block(),
        Input(command="while ! test -e /drive/done; do :; done", env=DRIVE): Block(),
        Input(command="deadline=$((SECONDS+540)); while (( SECONDS < deadline )); do\n  s=$(q)\ndone", env=DRIVE): Block(),
        Input(command="for i in 1 2 3 4 5 6; do gh pr view 12 && break; sleep 30; done", env=DRIVE): Block(),
        Input(command="for i in $(seq 30); do curl -s x && break; sleep 10; done", env=DRIVE): Block(),
        Input(command="for ((i=0; i<9; i++)); do curl -s x; sleep 5; done", env=DRIVE): Block(),
        Input(command="sleep 600 && echo wake", env=DRIVE): Block(),
        Input(command="sleep 5m; cci tail --drive d", env=DRIVE): Block(),
        Input(command="sleep $((target - now)); echo wake", env=DRIVE): Block(),
        Input(command="zsh -c 'until grep -q x y; do sleep 5; done'", env=DRIVE): Block(),
        Input(command="cat > /tmp/wake-1015.zsh <<'EOF'\nsleep 3600\nEOF\nzsh /tmp/wake-1015.zsh", env=DRIVE): Block(),
        Input(command=f"while true; do {POLL}; sleep 30; done", agent_id="a1b2c3", env=DRIVE): Block(),
        Input(command=f"sleep 600 && {POLL}", agent_id="a1b2c3", env=DRIVE): Block(),
        Input(command=POLL, agent_id="a1b2c3", env=DRIVE): Allow(),
        Input(command="sleep 5; cat x", env=DRIVE): Allow(),
        Input(command="for f in a b; do cat $f; done", env=DRIVE): Allow(),
        Input(command="for b in 1 2 3; do bk build view $b; done", env=DRIVE): Allow(),
        Input(command="for f in a b; do gh api $f; sleep 2; done", env=DRIVE): Allow(),
        Input(command="while IFS== read k v; do echo $k; done < /tmp/r.txt", env=DRIVE): Allow(),
        Input(command="while [ $# -gt 0 ]; do echo $1; shift; done", env=DRIVE): Allow(),
        Input(command="cci post --kind note --text 'until grep; do sleep 600; done'", env=DRIVE): Allow(),
        Input(command="git commit -m 'while true; do sleep 600; done'", env=DRIVE): Allow(),
        Input(command="cat > loop.txt <<'EOF'\nwhile true; do sleep 600; done\nEOF", env=DRIVE): Allow(),
        Input(command="lr wait 540 cci:d:lane -- bk build watch 12", env=DRIVE): Allow(),
        Input(command="lr coordinator next --drive d --cursor /c", env=DRIVE): Allow(),
        Input(tool="Monitor", tool_input={"description": "ci", "timeout_ms": 1800000, "command": UNTIL}, env=DRIVE): Allow(),
        Input(command=UNTIL): Allow(),
    },
)
