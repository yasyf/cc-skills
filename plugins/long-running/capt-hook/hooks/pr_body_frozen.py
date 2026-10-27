from __future__ import annotations

from captain_hook import Allow, BaseHookEvent, Event, HookResult, Input, Or, Runs, Tool, Warn, on
from captain_hook.command_schema import CommandSchema, Operand, Option

from .register_injection import drive_args

DRIVE = {"CLAUDE_LONG_RUNNING_DRIVE": "900424b6"}
EDITED = ("body", "body-file", "title")
PR_EDIT = CommandSchema(
    "gh",
    operands=(Operand("words", count="*"),),
    options=(
        Option("repo", ("-R", "--repo")),
        Option("body", ("-b", "--body")),
        Option("body-file", ("-F", "--body-file")),
        Option("title", ("-t", "--title")),
    ),
)
GH_API = CommandSchema(
    "gh",
    operands=(Operand("words", count="*"),),
    options=(Option("method", ("-X", "--method")), Option("field", ("-f", "-F", "--field", "--raw-field"))),
)
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
        if api.get("words", ())[:1] == ("api",) and "PATCH" in {method.upper() for method in api.get("method", ()) if method} and "/pulls/" in f"/{endpoint}":
            return True
    return False


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash"), Or(Runs("gh", "pr", "edit"), Runs("gh", "api")), InDrive()],
    tests={
        Input(command="gh pr edit 42758 --body-file body.md", env=DRIVE): Warn(pattern=r"gh pr comment"),
        Input(command="gh pr edit 42758 --title 'x: y'", env=DRIVE): Warn(pattern=r"stay as opened"),
        Input(command="gh api -X PATCH repos/acme/app/pulls/42758 -f body=new", env=DRIVE): Warn(pattern=r"stay as opened"),
        Input(command="gh pr edit 42758 --add-label hold", env=DRIVE): Allow(),
        Input(command="gh api repos/acme/app/pulls/42758", env=DRIVE): Allow(),
        Input(command="gh api -X PATCH repos/acme/app/issues/comments/9 -f body=x", env=DRIVE): Allow(),
        Input(command="gh pr edit 42758 --body-file body.md"): Allow(),
    },
)
def pr_body_frozen(evt: BaseHookEvent) -> HookResult | None:
    return evt.warn(FROZEN) if edits_pr_text(evt) else None
