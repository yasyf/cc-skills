from __future__ import annotations

import re
from collections.abc import Iterable, Iterator

from captain_hook import Allow, BaseHookEvent, Call, Event, HookResult, Input, Or, Runs, Tool, Warn, on

from .board_text_gate import reply_body
from .call_dir import known_path
from .compaction_handoff import Bound, shared_tests
from .pr_body_frozen import DRIVE, PR_EDIT, SHIP, InDrive
from .tests.register_fixtures import ACTIVE, DRIVE_CONFIG, FIXTURES

QUOTED = re.compile(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b[^\"“\n]{0,80}[\"“]\s*(?:[^\s\"“”]+\s+){7,}[^\s\"“”]+\s*[\"”]|\bverbatim\b|\bhis words\b", re.IGNORECASE)
PR_TEXT = {("pr", "create"), ("pr", "edit"), ("pr", "comment")}
BODIES = FIXTURES / "owner-quotes"
QUOTE = BODIES / "clock-quote.md"
HEREDOC = "gh pr comment 12 --body \"$(cat <<'EOF'\nKept his words as given.\nEOF\n)\""


def bodies(evt: BaseHookEvent) -> Iterator[str]:
    for call in evt.command.calls("gh"):
        values = PR_EDIT.bind(call).values
        if tuple(values.get("words", ())[:2]) in PR_TEXT:
            if isinstance(body := reply_body(evt, call, values), str):
                yield body
            yield from read(evt, call, values.get("body-file", ()))
    for call in evt.command.calls("ccx"):
        values = SHIP.bind(call).values
        if values.get("words", ())[:2] == ("vcs", "ship"):
            yield from read(evt, call, (value.partition("=")[2] or value for value in values.get("pr-body-file", ()) if value))


def read(evt: BaseHookEvent, call: Call, paths: Iterable[str | None]) -> Iterator[str]:
    for path in paths:
        if path and path != "-" and (file := known_path(evt, call, path)) and file.exists():
            yield file.read_text()


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash"), Or(Runs("gh"), Runs("ccx", "vcs", "ship")), InDrive()],
    tests=shared_tests({
        Input(command=f"gh pr create --title 'x: y' --body-file {QUOTE}", env=DRIVE): Warn(pattern=r"paraphrase"),
        Input(command=f"gh -R acme/app pr edit 12 -F {QUOTE}", env=DRIVE): Warn(),
        Input(command=f"gh pr edit 12 -F {QUOTE.name}", cwd=str(QUOTE.parent), env=DRIVE): Warn(),
        Input(command=f". /env.sh; cd $A; gh pr edit 12 -F {QUOTE.name}", cwd=str(QUOTE.parent), env=DRIVE): Allow(),
        Input(command="gh pr edit 12 --body 'Quotes the order verbatim.'", env=DRIVE): Warn(),
        Input(command=HEREDOC, env=DRIVE): Warn(),
        Input(command=f"ccx vcs ship -m 'x: y' --pr-body-file {QUOTE}", env=DRIVE): Warn(),
        Input(command=f"ccx vcs ship --pr-body-file lane/fix={QUOTE}", env=DRIVE): Warn(),
        Input(command=f"gh pr create --body-file {QUOTE}", env=DRIVE_CONFIG, state=ACTIVE): Warn(),
        Input(command=f"gh pr create --body-file {BODIES / 'log-line.md'}", env=DRIVE): Allow(),
        Input(command=f"gh pr comment 12 --body-file {BODIES / 'paraphrase.md'}", env=DRIVE): Allow(),
        Input(command="gh pr comment 12 --body 'CI failed at 06:18 with \"exit status 1\"'", env=DRIVE): Allow(),
        Input(command=f"gh pr view 12 --body-file {QUOTE}", env=DRIVE): Allow(),
        Input(command=f"gh pr create --body-file {QUOTE}"): Allow(),
    }),
)
def pr_text_paraphrases_the_owner(evt: BaseHookEvent) -> HookResult | None:
    return evt.warn("Warning, nothing was refused: this PR text quotes the owner word for word. Rewrite that passage as a paraphrase and keep the owner's words in `cci` and `cc-notes`.") if any(QUOTED.search(body) for body in bodies(evt)) else None
