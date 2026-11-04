from __future__ import annotations

import re

from captain_hook import Allow, BaseHookEvent, Block, Call, Event, HookResult, Input, Tool, Warn, on
from captain_hook.annotations import shell_comments

from .call_dir import run_dir, unknown_dir
from .compaction_handoff import Bound, CompactionState
from .lane_landing import HELP_FLAGS, LANE, incident_lane, lane_name, option, run_quietly, sliced_brief

BOUND = [CompactionState(active=True, coordinator=True)]

PUSHES = frozenset({("git", "push"), ("ccx", "vcs", "push"), ("ccx", "vcs", "ship")})
NOT_A_PUSH = HELP_FLAGS | {"--new-branch", "--create", "--no-push", "--dry-run", "-n", "--delete", "-d"}
FIX_IS_WRONG = re.compile(r"#\s*fix-is-wrong:\s*(?P<slug>\S+)\s*")
FIX_LANE = "ashared-open-fix-0123456789abcdef"
INCIDENT = sliced_brief("shared-open-fix", "incident")
HEAD = "e5ef5c82b64c06a83370146a56e6a47c3ae2d674"
OPEN = {"gh pr view": f"OPEN {HEAD}\n", "git diff --name-only": "shared-open/retry.test.ts\n"}
RUNNING = OPEN | {"gh pr checks": "pending\npass\n"}
RED = OPEN | {"gh pr checks": "fail\npass\n"}
POLISH = "ccx vcs ship -m 'shared-open: 🧪 capture the exact first retry backoff'"
ACCEPT = (
    "An incident fix accepts its reviewer findings, so no push restarts CI unless a required check failed. "
    'Run `.agents/skills/submit-pr/scripts/pr-reviews accept <slug> --why "incident: <why the fix is still right>"`, or end the push with `# fix-is-wrong: <slug>` if the finding says the fix is wrong.'
)
FIX_IS_WRONG_NOTE = "Finding `{slug}` says the incident fix itself is wrong, so this push restarts its CI. Name `{slug}` in the lane's report."


def pushes(call: Call) -> bool:
    return any(call.verb_argv[: len(verb)] == verb for verb in PUSHES) and not any(arg.split("=", 1)[0] in NOT_A_PUSH for arg in call.args)


def restarts_green_ci(evt: BaseHookEvent, call: Call) -> bool:
    if unknown_dir(evt, call):
        return False
    cwd = run_dir(evt, call)
    branch = option(call.args, "--branch")[:1]
    viewed = run_quietly(["gh", "pr", "view", *branch, "--json", "state,headRefOid", "--jq", '.state+" "+.headRefOid'], cwd)
    if viewed is None or (state := viewed.stdout.split())[:1] != ["OPEN"] or len(state) != 2:
        return False
    worktree = call.verb_argv[:3] == ("ccx", "vcs", "ship") and not branch and "--no-commit" not in call.args
    diffed = run_quietly(["git", "diff", "--name-only", state[1], *([] if worktree else branch or ["HEAD"])], cwd)
    if diffed is None or diffed.returncode or not diffed.stdout.strip():
        return False
    checks = run_quietly(["gh", "pr", "checks", *branch, "--required", "--json", "bucket", "--jq", ".[].bucket"], cwd)
    buckets = checks.stdout.split() if checks and checks.returncode == 0 else []
    return bool(buckets) and "fail" not in buckets


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash")],
    tests={
        Input(command=POLISH, agent_id=FIX_LANE, transcript=INCIDENT, commands=RUNNING, state=BOUND): Block(pattern=r"^An incident fix accepts its reviewer findings.*scripts/pr-reviews accept <slug>"),
        Input(command="git push", agent_id="aincident-api-500s-0123456789abcdef", commands=RUNNING, state=BOUND): Block(pattern=r"`# fix-is-wrong: <slug>`"),
        Input(command="ccx vcs push", agent_id="aincident-api-500s-0123456789abcdef", commands=OPEN | {"gh pr checks": "pass\n"}, state=BOUND): Block(),
        Input(command=POLISH, agent_id=FIX_LANE, transcript=INCIDENT, commands=RED, state=BOUND): Allow(),
        Input(command="ccx vcs ship --help", agent_id=FIX_LANE, transcript=INCIDENT, commands=RUNNING, state=BOUND): Allow(),
        Input(command=POLISH, agent_id=LANE, commands=RUNNING, state=BOUND): Allow(),
        Input(command=f"{POLISH}  # fix-is-wrong: retry-test-misses-first-backoff", agent_id=FIX_LANE, transcript=INCIDENT, commands=RUNNING, state=BOUND): Warn(pattern=r"^Finding `retry-test-misses-first-backoff` says"),
        Input(command="ccx vcs ship --new-branch shared-open/fix -m 'shared-open: 🐛 fix'", agent_id=FIX_LANE, transcript=INCIDENT, commands=RUNNING, state=BOUND): Allow(),
        Input(command="ccx vcs ship -m 'shared-open: 🐛 fix'", agent_id=FIX_LANE, transcript=INCIDENT, commands={"gh pr view": (1, "no pull requests found")}, state=BOUND): Allow(),
        Input(command="ccx vcs ship --tip-only --no-commit", agent_id=FIX_LANE, transcript=INCIDENT, commands=RUNNING | {"git diff --name-only": ""}, state=BOUND): Allow(),
        Input(command=POLISH, agent_id=FIX_LANE, transcript=INCIDENT, commands=OPEN | {"gh pr checks": (1, "no required checks reported")}, state=BOUND): Allow(),
        Input(command=POLISH, agent_id=FIX_LANE, transcript=INCIDENT, commands=RUNNING | {"git diff --name-only": (128, "fatal: bad object")}, state=BOUND): Allow(),
        Input(command="git push --dry-run", agent_id=FIX_LANE, transcript=INCIDENT, commands=RUNNING, state=BOUND): Allow(),
    },
)
def incident_accepts_findings(evt: BaseHookEvent) -> HookResult | None:
    if not incident_lane(evt, lane_name(evt) or ""):
        return None
    calls = [call for call in evt.cmd.calls() if pushes(call)]
    if not calls:
        return None
    if escape := next((found["slug"] for comment in shell_comments(evt.cmd.raw) if (found := FIX_IS_WRONG.fullmatch(comment))), None):
        return evt.context(FIX_IS_WRONG_NOTE.format(slug=escape))
    return evt.block(ACCEPT) if any(restarts_green_ci(evt, call) for call in calls) else None
