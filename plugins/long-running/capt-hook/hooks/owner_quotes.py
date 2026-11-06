from __future__ import annotations

import re
from collections.abc import Iterable, Iterator

from captain_hook import Allow, BaseHookEvent, Call, Event, HookResult, Input, Or, Runs, Tool, Warn, on

from .board_text_gate import reply_body
from .call_dir import known_path, unknown_dir
from .compaction_handoff import Bound, shared_tests
from .lane_landing import run_quietly
from .pr_body_frozen import DRIVE, PR_EDIT, SHIP, InDrive
from .tests.register_fixtures import ACTIVE, DRIVE_CONFIG, FIXTURES

QUOTED = re.compile(r"\b(?:[01]?\d|2[0-3]):[0-5]\d\b[^\"“\n]{0,80}[\"“]\s*(?:[^\s\"“”]+\s+){7,}[^\s\"“”]+\s*[\"”]|\bverbatim\b|\bhis words\b", re.IGNORECASE)
PR_TEXT = {("pr", "create"), ("pr", "edit"), ("pr", "comment")}
BODIES = FIXTURES / "owner-quotes"
QUOTE = BODIES / "clock-quote.md"
HEREDOC = "gh pr comment 12 --body \"$(cat <<'EOF'\nKept his words as given.\nEOF\n)\""
PRIVATE_REPO = "forge-ai/monorepo"
ORIGIN = "git remote get-url origin"
PUBLIC = {ORIGIN: "git@github.com:yasyf/cc-skills.git\n"}
PRIVATE = {ORIGIN: "https://github.com/Forge-AI/monorepo.git\n"}


def bodies(evt: BaseHookEvent) -> Iterator[str]:
    return (body for _, body in call_bodies(evt))


def call_bodies(evt: BaseHookEvent) -> Iterator[tuple[Call, str]]:
    for call in evt.command.calls("gh"):
        values = PR_EDIT.bind(call).values
        if tuple(values.get("words", ())[:2]) in PR_TEXT:
            if isinstance(body := reply_body(evt, call, values), str):
                yield call, body
            yield from ((call, text) for text in read(evt, call, values.get("body-file", ())))
    for call in evt.command.calls("ccx"):
        values = SHIP.bind(call).values
        if values.get("words", ())[:2] == ("vcs", "ship"):
            yield from ((call, text) for text in read(evt, call, (value.partition("=")[2] or value for value in values.get("pr-body-file", ()) if value)))


def read(evt: BaseHookEvent, call: Call, paths: Iterable[str | None]) -> Iterator[str]:
    for path in paths:
        if path and path != "-" and (file := known_path(evt, call, path)) and file.exists():
            yield file.read_text()


def private_target(evt: BaseHookEvent, call: Call) -> bool:
    named = PR_EDIT.bind(call).values.get("repo", ()) if call.name == "gh" else ()
    if named:
        return names_private(str(named[0]))
    return not unknown_dir(evt, call) and bool(origin := run_quietly(ORIGIN.split(), call.cwd or evt.cwd)) and not origin.returncode and names_private(origin.stdout)


def names_private(repo: str) -> bool:
    return repo.strip().removesuffix(".git").lower().endswith(PRIVATE_REPO)


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash"), Or(Runs("gh"), Runs("ccx", "vcs", "ship")), InDrive()],
    tests=shared_tests({
        Input(command=f"gh pr create --title 'x: y' --body-file {QUOTE}", env=DRIVE, commands=PUBLIC): Warn(pattern=r"paraphrase"),
        Input(command=f"gh pr create --title 'x: y' --body-file {QUOTE}", env=DRIVE, commands=PRIVATE): Allow(),
        Input(command=f"gh -R Forge-AI/monorepo pr edit 12 -F {QUOTE}", env=DRIVE, commands=PUBLIC): Allow(),
        Input(command=f"gh -R yasyf/cc-skills pr edit 12 -F {QUOTE}", env=DRIVE, commands=PRIVATE): Warn(),
        Input(command=f"ccx vcs ship -m 'x: y' --pr-body-file {QUOTE}", env=DRIVE, commands=PRIVATE): Allow(),
        Input(command=f"gh pr create --body-file {QUOTE}", env=DRIVE, commands={ORIGIN: (1, "no origin")}): Warn(),
        Input(command=f"gh -R acme/app pr edit 12 -F {QUOTE}", env=DRIVE): Warn(),
        Input(command=f"gh pr edit 12 -F {QUOTE.name}", cwd=str(QUOTE.parent), env=DRIVE, commands=PUBLIC): Warn(),
        Input(command=f". /env.sh; cd $A; gh pr edit 12 -F {QUOTE.name}", cwd=str(QUOTE.parent), env=DRIVE): Allow(),
        Input(command="gh pr edit 12 --body 'Quotes the order verbatim.'", env=DRIVE, commands=PUBLIC): Warn(),
        Input(command=HEREDOC, env=DRIVE, commands=PUBLIC): Warn(),
        Input(command=f"ccx vcs ship -m 'x: y' --pr-body-file {QUOTE}", env=DRIVE, commands=PUBLIC): Warn(),
        Input(command=f"ccx vcs ship --pr-body-file lane/fix={QUOTE}", env=DRIVE, commands=PUBLIC): Warn(),
        Input(command=f"ccx vcs ship --draft --pr-title-file t --pr-body-file {QUOTE}", env=DRIVE, commands=PUBLIC): Warn(),
        Input(command=f"gh pr create --body-file {QUOTE}", env=DRIVE_CONFIG, state=ACTIVE, commands=PUBLIC): Warn(),
        Input(command=f"gh pr create --body-file {BODIES / 'log-line.md'}", env=DRIVE): Allow(),
        Input(command=f"gh pr comment 12 --body-file {BODIES / 'paraphrase.md'}", env=DRIVE): Allow(),
        Input(command="gh pr comment 12 --body 'CI failed at 06:18 with \"exit status 1\"'", env=DRIVE): Allow(),
        Input(command=f"gh pr view 12 --body-file {QUOTE}", env=DRIVE): Allow(),
        Input(command=f"gh pr create --body-file {QUOTE}"): Allow(),
    }),
)
def pr_text_paraphrases_the_owner(evt: BaseHookEvent) -> HookResult | None:
    return evt.warn("Warning, nothing was refused: this PR text quotes the owner word for word. Rewrite that passage as a paraphrase and keep the owner's words in `cci` and `cc-notes`.") if any(QUOTED.search(body) and not private_target(evt, call) for call, body in call_bodies(evt)) else None
