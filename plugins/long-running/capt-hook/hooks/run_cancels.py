from __future__ import annotations

import re
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Call, Event, HookResult, Input, Tool, Warn, on

from .buildkite_budget import MAX_SCRIPT_BYTES, SCRIPT_LANGS, executed_words, script_path
from .call_dir import run_dir, unknown_dir
from .compaction_handoff import Bound, CompactionState

BOUND = [CompactionState(active=True, coordinator=True)]

FIXTURES = Path(__file__).parent / "tests" / "fixtures" / "run-cancels"
CANCELS = re.compile(r"\bgh\s+run\s+cancel\b|/actions/runs/\S*/cancel\b")
SCOPED_REPO = "yasyf/cc-skills"
OTHER_REPO = re.compile(r"\byasyf/(?!cc-skills\b)(?:[\w.-]+|\$)")
PULL_REQUEST = "pull_request"
SCOPE = (
    "A run canceller cancels only yasyf/cc-skills pull_request runs, since another repository's runs and every push run grade work that already merged."
    " Name `-R yasyf/cc-skills` and keep only `pull_request` events, or drop the cancel."
)


def unscoped(text: str) -> bool:
    return bool(CANCELS.search(text)) and (SCOPED_REPO not in text or PULL_REQUEST not in text or bool(OTHER_REPO.search(text)))


def cancel_call(call: Call) -> bool:
    args = list(call.args)
    return call.name == "gh" and (args[:2] == ["run", "cancel"] or (args[:1] == ["api"] and any(arg.endswith("/cancel") for arg in args)))


def call_repo(call: Call) -> str | None:
    args = list(call.args)
    for flag in ("-R", "--repo"):
        if flag in args and args.index(flag) + 1 < len(args):
            return args[args.index(flag) + 1]
    return next(("/".join(arg.removeprefix("/").split("/")[1:3]) for arg in args if arg.removeprefix("/").startswith("repos/")), None)


def inline_unscoped(evt: BaseHookEvent) -> bool:
    calls = list(evt.command.calls())
    cancels = [call for call in calls if cancel_call(call)]
    if not cancels:
        return False
    events = {arg for call in calls if call.name == "gh" for arg in call.args}
    return any(call_repo(call) != SCOPED_REPO for call in cancels) or not any(PULL_REQUEST in arg for arg in events)


def scripts_unscoped(evt: BaseHookEvent) -> bool:
    paths = [path for call in evt.command.calls() for word in executed_words(call) if (path := script_path(word, None if unknown_dir(evt, call) else Path(run_dir(evt, call))))]
    return any(path.stat().st_size <= MAX_SCRIPT_BYTES and unscoped(path.read_text(errors="replace")) for path in paths)


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", "Write")],
    tests={
        Input(tool="Write", file="/work/every-repo.sh", content=(FIXTURES / "every-repo.sh").read_text(), state=BOUND): Warn(pattern=r"^A run canceller cancels only yasyf/cc-skills pull_request runs"),
        Input(tool="Write", file="/work/cc-skills-prs.sh", content=(FIXTURES / "cc-skills-prs.sh").read_text(), state=BOUND): Allow(),
        Input(tool="Write", file="/work/notes.md", content=(FIXTURES / "every-repo.sh").read_text(), state=BOUND): Allow(),
        Input(command="gh run cancel -R yasyf/cc-context 41435515365", state=BOUND): Warn(pattern=r"keep only `pull_request` events"),
        Input(command="gh run list -R yasyf/cc-skills --event pull_request --limit 5 && gh run cancel -R yasyf/cc-skills 41435515365", state=BOUND): Allow(),
        Input(command="gh api -X POST repos/yasyf/cc-skills/actions/runs/41435515365/cancel", state=BOUND): Warn(pattern=r"^A run canceller"),
        Input(command=f"zsh {FIXTURES / 'every-repo.sh'}", state=BOUND): Warn(pattern=r"^A run canceller"),
        Input(command=f"zsh {FIXTURES / 'cc-skills-prs.sh'}", state=BOUND): Allow(),
        Input(command="gh run view 41435515365 -R yasyf/cc-context --log-failed", state=BOUND): Allow(),
    },
)
def run_cancels_stay_on_cc_skills_prs(evt: BaseHookEvent) -> HookResult | None:
    if evt.tool_name == "Write":
        found = Path(str(evt.file.path)).suffix in SCRIPT_LANGS and unscoped(evt.content or "")
    else:
        found = inline_unscoped(evt) or scripts_unscoped(evt)
    return evt.context(SCOPE) if found else None
