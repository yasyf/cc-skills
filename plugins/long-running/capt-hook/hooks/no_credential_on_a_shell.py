from __future__ import annotations

import re

from captain_hook import Allow, BaseHookEvent, Block, Event, HookResult, Input, on, Tool, Warn

from .compaction_handoff import Bound
from .pr_ledger import response_text

LANE = {"CLAUDE_LONG_RUNNING_LANE": "lane-a"}
TOKEN = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_\w{20,}|sk-[\w-]{20,}|xox[abprs]-[\w-]{10,}|(?:AKIA|ASIA)[0-9A-Z]{16})\b")
READER = re.compile(r"\bgh auth token\b|\bsecurity find-generic-password\b[^|;]*\s-w\b")
INTO_A_SHELL = re.compile(r"\|\s*(?:(?:sprite exec|ssh)\b[^|]*?\s)?(?:(?:/\S+/)?(?:ba|z|da)?sh)(?:\s|$)")
ON_THE_LINE = "A credential on a command line reaches the transcript and the process list. Read it from a 0600 file or an env var, and if it is real post a blocker to meta-desk and owner-relay without the value."
INTO_SHELL = "A shell runs what it reads, so a credential piped into one prints in an error. Write it to a 0600 file or send it to `curl --data-binary @-` instead."
PRINTED = "This output holds a credential, now in your transcript. Post a blocker to meta-desk and owner-relay without the value, naming which credential and where it went; rotating it is the owner's call."
FAKE = "gho_" + "a" * 36


@on(  # capt-hook: no-escape the owner ordered a refusal, and a printed credential cannot be taken back
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash")],
    tests={
        Input(command=f"curl -H 'Authorization: token {FAKE}' https://api.github.com", env=LANE): Block(pattern=r"^A credential on a command line"),
        Input(command=f"cci post --text 'the value is {FAKE}'", env=LANE): Block(),
        Input(command="{ gh auth token; cat script.sh; } | sprite exec -s desk -- bash -s", env=LANE): Block(pattern=r"^A shell runs what it reads"),
        Input(command="gh auth token | bash", env=LANE): Block(),
        Input(command="security find-generic-password -s key -w | ssh box sh", env=LANE): Block(),
        Input(command="gh auth token | sprite exec -s desk -- curl -sS --data-binary @- http://localhost/creds", env=LANE): Allow(),
        Input(command="gh auth token > creds && chmod 600 creds", env=LANE): Allow(),
        Input(command="cat script.sh | sprite exec -s desk -- bash -s", env=LANE): Allow(),
        Input(command="git log --grep gho_", env=LANE): Allow(),
        Input(command="gh auth token | bash", env={}): Allow(),
    },
)
def no_credential_on_a_shell(evt: BaseHookEvent) -> HookResult | None:
    return evt.block(why) if (why := refusal(evt.command.raw)) else None


def refusal(line: str) -> str | None:
    if TOKEN.search(line):
        return ON_THE_LINE
    return INTO_SHELL if READER.search(line) and INTO_A_SHELL.search(line) else None


@on(
    Event.PostToolUse,
    only_if=[Bound(), Tool("Bash")],
    tests={
        Input(command="bash script.sh", env=LANE, output=f"bash: line 1: {FAKE}: command not found"): Warn(pattern=r"^This output holds a credential"),
        Input(command="git status", env=LANE, output="clean"): Allow(),
    },
)
def a_printed_credential_is_reported(evt: BaseHookEvent) -> HookResult | None:
    return evt.context(PRINTED) if TOKEN.search(response_text(evt.tool_response)) else None
