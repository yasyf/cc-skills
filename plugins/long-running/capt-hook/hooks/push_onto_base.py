from __future__ import annotations

import json
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Call, Event, HookResult, Input, Tool, Warn, on

from .call_dir import unknown_dir
from .compaction_handoff import Bound
from .lane_landing import run_quietly
from .vet_before_push import NOT_A_PUSH

PUSH_VALUE_FLAGS = frozenset({"-o", "--push-option", "--repo", "--receive-pack", "--exec"})
OPERATIONS = {"rebase-merge": "rebase", "rebase-apply": "rebase", "MERGE_HEAD": "merge", "CHERRY_PICK_HEAD": "cherry-pick"}
GIT_PATHS = ["git", "rev-parse", "--path-format=absolute", *[arg for name in OPERATIONS for arg in ("--git-path", name)]]
MID_OPERATION = "Warning, nothing was refused: a git {operation} is in progress here, so `HEAD` names the base it replays onto and this push can set the branch to that base, which closes its pull request. Finish or abort the {operation}, then push with `ccx vcs push`."
INTO_BASE = "Warning, nothing was refused: this push sets `{branch}` to a commit its base `{base}` already holds, so its open pull request carries no commits and GitHub closes it. Push the branch's own commits with `ccx vcs push`."
FIXTURE = Path(__file__).parent / "tests" / "fixtures" / "push-onto-base"
LANE_ENV = {"CLAUDE_LONG_RUNNING_LANE": "lane-a"}
HEAD = "a" * 40
DEV_TIP = "d" * 40
REBASING = {" ".join(GIT_PATHS): "".join(f"{FIXTURE / name}\n" for name in OPERATIONS)}
IDLE = {" ".join(GIT_PATHS): "".join(f"{FIXTURE / 'idle' / name}\n" for name in OPERATIONS)}
OPEN_PR = '{"baseRefName": "dev", "state": "OPEN"}'
AT_DEV_TIP = IDLE | {"git symbolic-ref --short -q HEAD": "feat\n", "ccx vcs gh -- pr view feat": OPEN_PR, "git rev-parse --verify -q": f"{DEV_TIP}\n", "git merge-base": f"{DEV_TIP}\n"}
OWN_WORK = AT_DEV_TIP | {"git rev-parse --verify -q": f"{HEAD}\n"}


def operands(call: Call) -> list[str]:
    words = call.verb_argv[2:]
    return [word for before, word in zip(("", *words), words, strict=False) if not word.startswith("-") and before not in PUSH_VALUE_FLAGS]


def in_progress(where: str) -> str | None:
    listed = run_quietly(GIT_PATHS, where)
    if not listed or listed.returncode:
        return None
    return next((OPERATIONS[name] for name, path in zip(OPERATIONS, listed.stdout.splitlines(), strict=False) if Path(path).exists()), None)


def current_branch(where: str) -> str | None:
    named = run_quietly(["git", "symbolic-ref", "--short", "-q", "HEAD"], where)
    return named.stdout.strip() if named and not named.returncode and named.stdout.strip() else None


def into_base(where: str, remote: str, refspec: str) -> str | None:
    source, _, target = refspec.removeprefix("+").partition(":")
    branch = current_branch(where) if (target or source) == "HEAD" else target or source
    if not branch:
        return None
    viewed = run_quietly(["ccx", "vcs", "gh", "--", "pr", "view", branch, "--json", "baseRefName,state"], where)
    if not viewed or viewed.returncode or (pr := json.loads(viewed.stdout))["state"] != "OPEN":
        return None
    head = run_quietly(["git", "rev-parse", "--verify", "-q", source or "HEAD"], where)
    if not head or head.returncode:
        return None
    joined = run_quietly(["git", "merge-base", head.stdout.strip(), f"refs/remotes/{remote}/{pr['baseRefName']}"], where)
    if not joined or joined.returncode or joined.stdout.strip() != head.stdout.strip():
        return None
    return INTO_BASE.format(branch=branch, base=pr["baseRefName"])


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash")],
    tests={
        Input(command="git push --force-with-lease origin feat", env=LANE_ENV, cwd=str(FIXTURE), commands=REBASING): Warn(pattern=r"^Warning, nothing was refused: a git rebase is in progress here[\s\S]*`ccx vcs push`\.$"),
        Input(command="git push -f origin HEAD:feat", env=LANE_ENV, cwd=str(FIXTURE), commands=AT_DEV_TIP): Warn(pattern=r"sets `feat` to a commit its base `dev` already holds, so its open pull request carries no commits"),
        Input(command="git push --force-with-lease origin feat", env=LANE_ENV, cwd=str(FIXTURE), commands=OWN_WORK): Allow(),
        Input(command="git push --dry-run origin feat", env=LANE_ENV, cwd=str(FIXTURE)): Allow(),
        Input(command="git status", env=LANE_ENV, cwd=str(FIXTURE)): Allow(),
    },
)
def pushes_that_close_their_pull_request(evt: BaseHookEvent) -> HookResult | None:
    for call in evt.command.calls():
        if call.verb_argv[:2] != ("git", "push") or unknown_dir(evt, call) or any(arg.split("=", 1)[0] in NOT_A_PUSH for arg in call.args):
            continue
        where = str(call.cwd or evt.cwd)
        if operation := in_progress(where):
            return evt.context(MID_OPERATION.format(operation=operation))
        remote, *refspecs = operands(call) or ["origin"]
        for refspec in refspecs or ["HEAD"]:
            if found := into_base(where, remote, refspec):
                return evt.context(found)
    return None
