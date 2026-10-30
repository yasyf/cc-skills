from __future__ import annotations

import re
import subprocess

from captain_hook import Allow, BaseHookEvent, Call, Event, HookResult, Input, Or, Runs, Tool, Warn, on
from captain_hook.command_schema import CommandSchema, Operand, Option
from captain_hook.util import reqenv

from .register_injection import drive_args

DRIVE = {"CLAUDE_LONG_RUNNING_DRIVE": "900424b6"}
EDITED = ("body", "body-file", "title")
PULL = re.compile(r"^/?repos/[^/]+/[^/]+/pulls/\d+/?$")
PR_EDIT = CommandSchema(
    "gh",
    operands=(Operand("words", count="*"),),
    options=(
        Option("repo", ("-R", "--repo")),
        Option("body", ("-b", "--body")),
        Option("body-file", ("-F", "--body-file")),
        Option("title", ("-t", "--title")),
        Option("base", ("-B", "--base")),
        Option("milestone", ("-m", "--milestone")),
        *(Option(name, (f"--{name}",)) for name in ("add-assignee", "add-label", "add-project", "add-reviewer", "remove-assignee", "remove-label", "remove-project", "remove-reviewer", "remove-milestone")),
    ),
)
GH_API = CommandSchema(
    "gh",
    operands=(Operand("words", count="*"),),
    options=(
        Option("method", ("-X", "--method")),
        Option("field", ("-f", "-F", "--field", "--raw-field")),
        Option("header", ("-H", "--header")),
        Option("jq", ("-q", "--jq")),
        Option("template", ("-t", "--template")),
        Option("preview", ("-p", "--preview")),
        *(Option(name, (f"--{name}",)) for name in ("cache", "hostname", "input")),
        Option("include", ("-i", "--include"), bool),
        *(Option(name, (f"--{name}",), bool) for name in ("paginate", "silent", "slurp", "verbose")),
    ),
)
SHIP = CommandSchema(
    "ccx",
    operands=(Operand("words", count="*"),),
    options=(
        Option("message", ("-m", "--message")),
        Option("branch", ("--branch",)),
        Option("new-branch", ("--new-branch",)),
        Option("parent", ("--parent",)),
        Option("pr-title", ("--pr-title",)),
        Option("pr-body-file", ("--pr-body-file",)),
        *(Option(name, (f"--{name}",), bool) for name in ("tip-only", "amend", "no-commit", "no-push", "no-watch", "reviews")),
    ),
)
OPEN = {"git rev-parse --abbrev-ref HEAD": "lane/fix\n", "gh pr view": "OPEN\n"}
FROZEN = "A PR's title and body stay as opened. Post the change as a PR comment with `gh pr comment <n> --body-file <file>` instead."


class InDrive:
    def check(self, evt: BaseHookEvent) -> bool:
        return drive_args(evt) is not None


def edits_pr_text(evt: BaseHookEvent) -> bool:
    for call in evt.command.calls("gh"):
        edit = PR_EDIT.bind(call).values
        if edit.get("words", ())[:2] == ("pr", "edit") and any(edit.get(name) for name in EDITED):
            return True
        api = GH_API.bind(call).values
        endpoint = next((word for word in api.get("words", ())[1:] if word), "")
        fields = {field.split("=", 1)[0] for field in api.get("field", ()) if field}
        if api.get("words", ())[:1] == ("api",) and "PATCH" in {method.upper() for method in api.get("method", ()) if method} and PULL.match(endpoint) and (fields & {"body", "title"} or api.get("input")):
            return True
    return any(is_open(evt, branch) for call in evt.command.calls("ccx") for branch in restated_branches(evt, call))


def restated_branches(evt: BaseHookEvent, call: Call) -> set[str]:
    ship = SHIP.bind(call).values
    if ship.get("words", ())[:2] != ("vcs", "ship") or ship.get("new-branch"):
        return set()
    scoped = [value for name in ("pr-title", "pr-body-file") for value in ship.get(name, ()) if value]
    tip = next((value for value in ship.get("branch", ()) if value), None)
    return {value.partition("=")[0] if "=" in value.partition(" ")[0] else tip or ran(evt, "git", "rev-parse", "--abbrev-ref", "HEAD") for value in scoped}


def is_open(evt: BaseHookEvent, branch: str) -> bool:
    return ran(evt, "gh", "pr", "view", branch, "--json", "state", "--jq", ".state") == "OPEN"


def ran(evt: BaseHookEvent, *argv: str) -> str:
    return subprocess.run(argv, capture_output=True, text=True, timeout=20, cwd=evt.cwd, env=dict(reqenv.env_map())).stdout.strip()


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash"), Or(Runs("gh"), Runs("ccx", "vcs", "ship")), InDrive()],
    tests={
        Input(command="gh pr edit 42758 --body-file body.md", env=DRIVE): Warn(pattern=r"gh pr comment"),
        Input(command="gh pr edit 42758 --title 'x: y'", env=DRIVE): Warn(pattern=r"stay as opened"),
        Input(command="gh api -X PATCH repos/acme/app/pulls/42758 -f body=new", env=DRIVE): Warn(pattern=r"stay as opened"),
        Input(command="gh -R acme/app pr edit 42758 --add-label hold --title 'x: y'", env=DRIVE): Warn(pattern=r"stay as opened"),
        Input(command="gh api --hostname github.com --method PATCH repos/acme/app/pulls/42758 -f body=x", env=DRIVE): Warn(pattern=r"stay as opened"),
        Input(command="gh pr edit 42758 --add-label hold", env=DRIVE): Allow(),
        Input(command="gh api repos/acme/app/pulls/42758 -X PATCH -f base=main", env=DRIVE): Allow(),
        Input(command="gh api repos/acme/app/pulls/42758 -X PATCH -F title=x", env=DRIVE): Warn(pattern=r"stay as opened"),
        Input(command="gh api repos/acme/app/pulls/42758 -X PATCH --input payload.json", env=DRIVE): Warn(pattern=r"stay as opened"),
        Input(command="gh api -X PATCH repos/acme/pulls/issues/comments/9 -f body=x", env=DRIVE): Allow(),
        Input(command="gh api repos/acme/app/pulls/42758", env=DRIVE): Allow(),
        Input(command="gh api -X PATCH repos/acme/app/issues/comments/9 -f body=x", env=DRIVE): Allow(),
        Input(command="gh pr edit 42758 --body-file body.md"): Allow(),
        Input(command="ccx vcs ship --tip-only --pr-body-file body.md", env=DRIVE, commands=OPEN): Warn(pattern=r"stay as opened"),
        Input(command="ccx vcs ship --pr-title 'lane/fix=api: 🐛 fix the timer'", env=DRIVE, commands=OPEN): Warn(pattern=r"stay as opened"),
        Input(command="ccx vcs ship --pr-body-file body.md", env=DRIVE, commands={**OPEN, "gh pr view": ""}): Allow(),
        Input(command="ccx vcs ship --new-branch lane/next --pr-body-file body.md", env=DRIVE, commands=OPEN): Allow(),
        Input(command="ccx vcs ship -m 'api: 🐛 fix the timer'", env=DRIVE, commands=OPEN): Allow(),
    },
)
def pr_body_frozen(evt: BaseHookEvent) -> HookResult | None:
    return evt.warn(FROZEN) if edits_pr_text(evt) else None
