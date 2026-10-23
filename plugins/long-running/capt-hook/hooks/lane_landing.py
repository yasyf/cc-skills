from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Block, Call, Event, HookResult, Input, Tool, on
from captain_hook.util import reqenv

from .compaction_handoff import CompactionState, drive_entry, script
from .owner_facing import INCIDENT_NAME
from .session_tree import nearest_name

LANDERS = re.compile(r"^(?:desk-runner|merge-train)(?:-.+)?$|^(?:\w+-)*desk(?:-\d+)?$")
TEAMMATE_NAME = re.compile(r"^a(?P<name>.+)-[0-9a-f]{16}$")
QUEUE_LABELS = frozenset({"merge", "merge-fast"})
LABEL_FIELDS = frozenset({"labels[]", "labels"})
LABELS_ENDPOINT = re.compile(r"(?:^|/)repos/[^/]+/[^/]+/issues/\d+/labels(?:\?|$)")
READ_ONLY_ENQUEUE = frozenset({"--check", "--status", "-h", "--help"})
QUEUE_STEERING = frozenset({"dequeue", "requeue", "priority", "recover", "hold", "lift"})
MQ_VALUE_OPTIONS = frozenset({"--config"})
GH_REPO_FLAGS = frozenset({"-R", "--repo"})
FIELD_FLAGS = ("--raw-field=", "--field=", "-f", "-F")
REPO_FLAGS = frozenset({"-R", "--repo"})
REPO_IN_ARG = re.compile(r"(?:^|/)repos/(?P<api>[\w.-]+/[\w.-]+)/|github\.com/(?P<url>[\w.-]+/[\w.-]+)/pull/")
MERGE_VALUE_FLAGS = frozenset({"--match-head-commit", "--body", "-b", "--body-file", "-F", "--subject", "-t", "--author-email", "-A"})
PULL = re.compile(r"(?:https://github\.com/[\w.-]+/[\w.-]+/pull/)?(?P<pr>\d+)/?")
CHECK_PR_CAUSES = frozenset({"RULINGS-UNCITED", "RESERVED-NO-ANSWER", "TEST-REMOVED", "FINDER-MAJOR", "NAME-HIT", "RULINGS-PLAIN", "LANE-DECIDED", "CONTRACT-MISSING", "TESTS-CORPUS"})
INCIDENT_CAUSES = frozenset({"RULINGS-UNCITED", "RESERVED-NO-ANSWER", "TEST-REMOVED"})
CHECK_PR_TIMEOUT_SECONDS = 60
FIXTURES = Path(__file__).parent / "tests" / "fixtures"
DRIVE_ENV = {"CLAUDE_LONG_RUNNING_DRIVE": "900424b6", "CLAUDE_CONFIG_DIR": str(FIXTURES / "claude-config")}
ACTIVE = [CompactionState(active=True)]
LANE = "alane-a-0123456789abcdef"
DESK = "alanding-desk-0123456789abcdef"
ENQUEUE = "~/monorepo/.agents/skills/submit-pr/scripts/stack-enqueue 36023 --hold 30082"
REFUSAL = (
    "Lanes never enqueue: the landing desk is the only enqueuer, so lane `{lane}` may not run `{verb}`. "
    "Report the PR with `ledger.py report --drive <drive>`, and the landing desk enqueues it against the root's holds."
)
STEERING_REFUSAL = (
    "Only the root steers the merge queue, so lane `{lane}` may not run `mq.py {verb}`. "
    "Send the root a `RULING NEEDED` line naming the PR and the verb, and read the queue with `mq.py why <n>`."
)
RULINGS_ADVICE = (
    "`rulings.py check-pr` names {causes} for #{pr} at {head}. The landing desk refuses a head on these; "
    "this hook only advises (792fdbb), so settle each one before the merge lands."
)
INCIDENT_REFUSAL = (
    "Incident fixes land by plain squash the pass they are approved and green, never through the queue (the owner ruled it on Oct 20), so lane `{lane}` may not run `stack-enqueue`. "
    "Post `ready`, and the landing runner merges it."
)


@dataclass(frozen=True)
class Launcher:
    value_letters: str = ""
    stop_letters: str = ""
    value_options: frozenset[str] = frozenset()

    def operand(self, args: tuple[str, ...]) -> tuple[str, ...]:
        index = 0
        while index < len(args):
            arg = args[index]
            if arg == "--":
                return args[index + 1 :]
            if arg in self.value_options:
                index += 2
                continue
            if arg.startswith("--"):
                index += 1
                continue
            if not arg.startswith(("-", "+")) or arg in {"-", "+"}:
                return args[index:]
            for position, letter in enumerate(arg[1:], start=2):
                if letter in self.stop_letters:
                    return ()
                if letter in self.value_letters:
                    index += position == len(arg)
                    break
            index += 1
        return ()


PYTHON = Launcher(value_letters="WX", stop_letters="mc", value_options=frozenset({"--check-hash-based-pycs"}))
SHELL = Launcher(value_letters="oO", stop_letters="cn", value_options=frozenset({"--rcfile", "--init-file"}))
UV_RUN = Launcher(
    value_letters="wp",
    value_options=frozenset({"--with", "--with-editable", "--with-requirements", "--python", "--project", "--directory", "--env-file", "--extra", "--group", "--package", "--index"}),
)
LAUNCHERS = {
    ("python",): PYTHON,
    ("python3",): PYTHON,
    ("bash",): SHELL,
    ("sh",): SHELL,
    ("zsh",): SHELL,
    ("uv", "run"): UV_RUN,
    ("cc-sudo", "run"): Launcher(),
}


def program(argv: tuple[str, ...]) -> tuple[str, ...]:
    for prefix, launcher in LAUNCHERS.items():
        if argv and (Path(argv[0]).name, *argv[1 : len(prefix)]) == prefix:
            return program(launcher.operand(argv[len(prefix) :]))
    return argv


def lane_name(evt: BaseHookEvent) -> str | None:
    if evt.agent_id:
        if match := TEAMMATE_NAME.match(evt.agent_id):
            return match["name"]
        return nearest_name(evt) or reqenv.getenv("CLAUDE_LONG_RUNNING_LANE") or ""
    return reqenv.getenv("CLAUDE_LONG_RUNNING_LANE")


def named_repo(call: Call) -> str | None:
    args = call.args
    if repo := next((value for flag, value in zip(args, args[1:], strict=False) if flag in REPO_FLAGS), None):
        return repo
    if repo := next((arg.split("=", 1)[1] for arg in args if arg.startswith(("--repo=", "-R="))), None):
        return repo
    return next((found["api"] or found["url"] for arg in args if (found := REPO_IN_ARG.search(arg))), None)


def outside_drive_repo(evt: BaseHookEvent, call: Call, entry: dict | None) -> bool:
    if entry is None:
        return False
    if repo := named_repo(call):
        return repo.casefold() != entry["repo"].casefold()
    cwd = call.cwd or evt.cwd
    done = subprocess.run(["git", "-C", str(cwd), "rev-parse", "--path-format=absolute", "--git-common-dir"], capture_output=True, text=True)
    return done.returncode == 0 and done.stdout.strip() != entry["git_common_dir"]


def label_args(args: tuple[str, ...]) -> set[str]:
    values = [
        value
        for flag, value in zip(("", *args), args, strict=False)
        if flag == "--add-label"
    ] + [arg.removeprefix("--add-label=") for arg in args if arg.startswith("--add-label=")]
    return {label.strip().casefold() for value in values for label in value.split(",")}


def field(arg: str) -> str:
    return next((arg.removeprefix(flag) for flag in FIELD_FLAGS if arg.startswith(flag) and len(arg) > len(flag)), arg)


def labelled_fields(args: tuple[str, ...]) -> set[str]:
    return {value.casefold() for arg in args if (pair := field(arg).split("=", 1))[0] in LABEL_FIELDS for value in pair[1:]}


def without_repo(argv: tuple[str, ...]) -> tuple[str, ...]:
    kept: list[str] = []
    skip = False
    for arg in argv:
        if skip:
            skip = False
        elif arg in GH_REPO_FLAGS:
            skip = True
        elif not arg.startswith(("--repo=", "-R=")):
            kept.append(arg)
    return tuple(kept)


def mq_verb(call: Call) -> str | None:
    run = program((call.name, *call.args))
    if not run or Path(run[0]).name != "mq.py":
        return None
    args = run[1:]
    index = 0
    while index < len(args) and args[index].startswith("-"):
        index += 2 if args[index] in MQ_VALUE_OPTIONS else 1
    verb = args[index] if index < len(args) else ""
    return verb if verb in QUEUE_STEERING else None


def landing_verb(call: Call) -> str | None:
    argv = without_repo((call.name, *call.args)) if call.name == "gh" else call.verb_argv
    run = program((call.name, *call.args))
    if run and Path(run[0]).name == "stack-enqueue":
        return None if READ_ONLY_ENQUEUE & set(run[1:]) else "stack-enqueue"
    if argv[:2] == ("gt", "merge"):
        return "gt merge"
    if argv[:3] == ("gh", "pr", "merge"):
        return "gh pr merge"
    if argv[:3] in {("gh", "pr", "edit"), ("gh", "issue", "edit")} and label_args(argv[3:]) & QUEUE_LABELS:
        return f"{' '.join(argv[:3])} --add-label"
    if argv[:2] == ("gh", "api") and any(LABELS_ENDPOINT.search(arg) for arg in argv[2:]) and labelled_fields(argv[2:]) & QUEUE_LABELS:
        return "gh api labels"
    if call.name == "ledger.py" and argv[1:2] == ("label",) and "--dry-run" not in argv:
        return "ledger.py label"
    return None


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash")],
    tests={
        Input(command=ENQUEUE, agent_id=LANE, state=ACTIVE): Block(pattern=r"^Lanes never enqueue: the landing desk is the only enqueuer, so lane `lane-a` may not run `stack-enqueue`\."),
        Input(command="stack-enqueue 36039 --fast", agent_id="aL23-dashboard-wasm-follower-14fd0a86e696c1e3", state=ACTIVE): Block(pattern=r"`L23-dashboard-wasm-follower`"),
        Input(command="cd ~/wt && gt merge", agent_id=LANE, state=ACTIVE): Block(pattern=r"`gt merge`"),
        Input(command="gh pr merge 36023 --squash", agent_id=LANE, state=ACTIVE): Block(pattern=r"`gh pr merge`"),
        Input(command="gh -R Forge-AI/monorepo pr merge 36023 --squash", agent_id=LANE, state=ACTIVE): Block(pattern=r"`gh pr merge`"),
        Input(command="python3 .agents/skills/submit-pr/scripts/stack-enqueue 36023", agent_id=LANE, state=ACTIVE): Block(pattern=r"`stack-enqueue`"),
        Input(command="bash .agents/skills/submit-pr/scripts/stack-enqueue 36023", agent_id=LANE, state=ACTIVE): Block(pattern=r"`stack-enqueue`"),
        Input(command="python3 -W ignore .agents/skills/submit-pr/scripts/stack-enqueue 36023", agent_id=LANE, state=ACTIVE): Block(pattern=r"`stack-enqueue`"),
        Input(command="uv run --with requests .agents/skills/submit-pr/scripts/stack-enqueue 36023", agent_id=LANE, state=ACTIVE): Block(pattern=r"`stack-enqueue`"),
        Input(command=".agents/skills/submit-pr/scripts/stack-enqueue --fast 36023", agent_id=LANE, state=ACTIVE): Block(pattern=r"`stack-enqueue`"),
        Input(command="git fetch -q && stack-enqueue 36023", agent_id=LANE, state=ACTIVE): Block(pattern=r"`stack-enqueue`"),
        Input(command="true; stack-enqueue 36023", agent_id=LANE, state=ACTIVE): Block(pattern=r"`stack-enqueue`"),
        Input(command="true | .agents/skills/submit-pr/scripts/stack-enqueue 36023", agent_id=LANE, state=ACTIVE): Block(pattern=r"`stack-enqueue`"),
        Input(command="echo $(stack-enqueue 36023)", agent_id=LANE, state=ACTIVE): Block(pattern=r"`stack-enqueue`"),
        Input(command="sudo stack-enqueue 36023", agent_id=LANE, state=ACTIVE): Block(pattern=r"`stack-enqueue`"),
        Input(command="env GH_TOKEN=x stack-enqueue 36023", agent_id=LANE, state=ACTIVE): Block(pattern=r"`stack-enqueue`"),
        Input(command="cc-sudo run -- .agents/skills/submit-pr/scripts/stack-enqueue 36023", agent_id=LANE, state=ACTIVE): Block(pattern=r"`stack-enqueue`"),
        Input(command="bash -c 'stack-enqueue 36023'", agent_id=LANE, state=ACTIVE): Block(pattern=r"`stack-enqueue`"),
        Input(command="python3 -m py_compile .agents/skills/submit-pr/scripts/stack-enqueue", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="bash -n .agents/skills/submit-pr/scripts/stack-enqueue", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="python3 -c 'print(\"stack-enqueue 36023\")'", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="cci post --drive release-v3 --lane x --kind report --to root 'stack-enqueue --fast 36023 now costs one query'", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="git diff -- .agents/skills/submit-pr/scripts/stack-enqueue", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="cat .agents/skills/submit-pr/scripts/stack-enqueue", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="echo ok # stack-enqueue 36023", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="cc-sudo run -- stack-enqueue --check 36023", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="gh api repos/Forge-AI/monorepo/issues/36023/labels '--raw-field=labels[]=merge'", agent_id=LANE, state=ACTIVE): Block(pattern=r"`gh api labels`"),
        Input(command="gh api repos/Forge-AI/monorepo/issues/36023/labels -flabels[]=merge-fast", agent_id=LANE, state=ACTIVE): Block(),
        Input(command="gh pr edit 36023 --add-label merge", agent_id=LANE, state=ACTIVE): Block(pattern=r"`gh pr edit --add-label`"),
        Input(command="gh pr edit 36023 --add-label=docs,MERGE-FAST", agent_id=LANE, state=ACTIVE): Block(),
        Input(command="gh issue edit 36023 --add-label merge", agent_id=LANE, state=ACTIVE): Block(),
        Input(command="gh api -X POST repos/Forge-AI/monorepo/issues/36023/labels -f 'labels[]=merge'", agent_id=LANE, state=ACTIVE): Block(pattern=r"`gh api labels`"),
        Input(command="ledger.py label --ledger 1a2b --pr 36023 --expect-head abc", agent_id=LANE, state=ACTIVE): Block(pattern=r"`ledger.py label`"),
        Input(command=ENQUEUE, env={"CLAUDE_LONG_RUNNING_LANE": "lane-b"}): Block(pattern=r"`lane-b`"),
        Input(command=ENQUEUE, agent_id=DESK, state=ACTIVE): Allow(),
        Input(command=ENQUEUE, agent_id="alanding-desk-2-0123456789abcdef", state=ACTIVE): Allow(),
        Input(command=ENQUEUE, agent_id="adesk-runner-0123456789abcdef", state=ACTIVE): Allow(),
        Input(command=ENQUEUE, env={"CLAUDE_LONG_RUNNING_LANE": "landing-desk"}): Allow(),
        Input(command=ENQUEUE, agent_id="asandsql-replica-desk-0123456789abcdef", state=ACTIVE): Allow(),
        Input(command="ledger.py label --pr 36023 --expect-head abc", agent_id="amerge-train-release-0123456789abcdef", state=ACTIVE): Allow(),
        Input(command=ENQUEUE, agent_id="adesk-cleanup-lane-0123456789abcdef", state=ACTIVE): Block(),
        Input(command="stack-enqueue 36050 --fast", agent_id="aincident-api-500s-0123456789abcdef", state=ACTIVE): Block(pattern=r"plain squash"),
        Input(command="stack-enqueue 36050", agent_id="aoutage-sand-0123456789abcdef", state=ACTIVE): Block(pattern=r"^Incident fixes land by plain squash .* lane `outage-sand` may not run `stack-enqueue`\."),
        Input(command="stack-enqueue 36050 --check", agent_id="aincident-api-500s-0123456789abcdef", state=ACTIVE): Allow(),
        Input(command="stack-enqueue 36050 --fast", agent_id="asand-5a-fix-0123456789abcdef", state=ACTIVE): Block(pattern=r"lane `sand-5a-fix` may not run `stack-enqueue`"),
        Input(command="gh pr edit 36050 --add-label merge-fast", agent_id="asand-5a-fix-0123456789abcdef", state=ACTIVE): Block(pattern=r"lane `sand-5a-fix` may not run `gh pr edit --add-label`"),
        Input(command="gh pr merge 36050 --squash", agent_id="aincident-api-500s-0123456789abcdef", state=ACTIVE): Allow(),
        Input(command=ENQUEUE, state=ACTIVE): Allow(),
        Input(command=ENQUEUE, agent_id=LANE): Allow(),
        Input(command="stack-enqueue 36023 --check --hold 30082", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="stack-enqueue --status 36023 36039", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="gh pr edit 36023 --add-label docs --remove-label merge", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="gh api -X DELETE repos/Forge-AI/monorepo/issues/36023/labels/merge", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="ledger.py label --dry-run --pr 36023", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="echo 'stack-enqueue 36023'", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="gh pr view 36023 --json mergedAt", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="gh pr merge 36023 -R o/r --squash", agent_id=LANE, env=DRIVE_ENV, state=ACTIVE): Block(pattern=r"`gh pr merge`"),
        Input(command="gh api repos/O/R/issues/36023/labels -f 'labels[]=merge'", agent_id=LANE, env=DRIVE_ENV, state=ACTIVE): Block(),
        Input(command="gh pr merge 81 -R yasyf/cc-inbox --squash", agent_id=LANE, env=DRIVE_ENV, state=ACTIVE): Allow(),
        Input(command="gh pr merge https://github.com/yasyf/cc-skills/pull/403 --squash", agent_id=LANE, env=DRIVE_ENV, state=ACTIVE): Allow(),
        Input(command="gh api repos/yasyf/cc-skills/issues/403/labels -f 'labels[]=merge'", agent_id=LANE, env=DRIVE_ENV, state=ACTIVE): Allow(),
    },
)
def lanes_never_enqueue(evt: BaseHookEvent) -> HookResult | None:
    if (lane := lane_name(evt)) is None or LANDERS.match(lane):
        return None
    if evt.agent_id and not reqenv.getenv("CLAUDE_LONG_RUNNING_LANE") and not CompactionState.load(evt).active:
        return None
    entry = drive_entry(evt)
    incident = bool(INCIDENT_NAME.match(lane))
    verb = next((verb for call in evt.cmd.calls() if (verb := landing_verb(call)) and (verb == "stack-enqueue" or not incident) and not outside_drive_repo(evt, call, entry)), None)
    if verb and incident:
        return evt.block(INCIDENT_REFUSAL.format(lane=lane))
    return evt.block(REFUSAL.format(lane=lane or "unnamed", verb=verb)) if verb else None


def merge_target(call: Call) -> tuple[str, str] | None:
    """The PR number a `gh pr merge` names, by number or URL, and the head its `--match-head-commit` pins, or empty; None when it names the PR by branch."""
    rest = without_repo((call.name, *call.args))[3:]
    target, head, index = "", "", 0
    while index < len(rest):
        arg = rest[index]
        if arg in MERGE_VALUE_FLAGS:
            head = rest[index + 1] if arg == "--match-head-commit" and index + 1 < len(rest) else head
            index += 2
            continue
        if arg.startswith("--match-head-commit="):
            head = arg.split("=", 1)[1]
        elif not arg.startswith("-"):
            target = target or arg
        index += 1
    found = PULL.fullmatch(target)
    return (found["pr"], head) if found else None


def run_quietly(argv: list[str], cwd: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=CHECK_PR_TIMEOUT_SECONDS, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired):
        return None


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash")],
    tests={
        Input(command="gh pr merge 36023 --squash"): Allow(),
        Input(command="gh pr merge 81 -R yasyf/cc-inbox --squash", env=DRIVE_ENV): Allow(),
        Input(command="gh pr merge fix/sand-read -R o/r --squash", env=DRIVE_ENV): Allow(),
        Input(command="gh pr view 36023 -R o/r --json mergedAt", env=DRIVE_ENV): Allow(),
    },
)
def rulings_merge_advice(evt: BaseHookEvent) -> HookResult | None:
    if (entry := drive_entry(evt)) is None:
        return None
    merging = [(call, target) for call in evt.cmd.calls() if landing_verb(call) == "gh pr merge" and not outside_drive_repo(evt, call, entry) and (target := merge_target(call))]
    if not merging:
        return None
    call, (pr, head) = merging[0]
    repo, cwd = named_repo(call) or entry["repo"], str(call.cwd or evt.cwd)
    if not head:
        viewed = run_quietly(["gh", "pr", "view", pr, "--repo", repo, "--json", "headRefOid", "--jq", ".headRefOid"], cwd)
        if viewed is None or viewed.returncode or not (head := viewed.stdout.strip()):
            return None
    incident = bool(INCIDENT_NAME.match(lane_name(evt) or ""))
    done = run_quietly([sys.executable, str(script("rulings.py")), "check-pr", "--repo", cwd, "--drive", entry["drive"], "--pr", f"{repo}#{pr}", "--head", head, *(["--incident"] if incident else [])], cwd)
    if done is None or done.returncode not in (0, 1):
        return None
    lines = [line.strip() for line in done.stdout.splitlines() if line.strip()]
    if any(line.split(" ", 1)[0] not in CHECK_PR_CAUSES | {"BLOCKER"} for line in lines):
        return None
    causes = [line for line in lines if line.split(" ", 1)[0] in (INCIDENT_CAUSES if incident else CHECK_PR_CAUSES)]
    return evt.context(RULINGS_ADVICE.format(causes="; ".join(causes), pr=pr, head=head[:10])) if causes else None


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash")],
    tests={
        Input(command="mq.py --config ~/d/runner.json dequeue 36023 --reason pulled", agent_id=LANE, state=ACTIVE): Block(pattern=r"^Only the root steers the merge queue, so lane `lane-a` may not run `mq.py dequeue`\."),
        Input(command="python3 skills/long-running/scripts/mq.py --config=r.json requeue 36023", agent_id=LANE, state=ACTIVE): Block(pattern=r"`mq.py requeue`"),
        Input(command="mq.py --config r.json priority 36023 0", agent_id=DESK, state=ACTIVE): Block(pattern=r"lane `landing-desk` may not run `mq.py priority`"),
        Input(command="mq.py --config r.json recover 36023", agent_id="adesk-runner-0123456789abcdef", state=ACTIVE): Block(pattern=r"`mq.py recover`"),
        Input(command="mq.py --config r.json hold 36023 --reason wait", agent_id="aincident-api-500s-0123456789abcdef", state=ACTIVE): Block(pattern=r"`mq.py hold`"),
        Input(command="git fetch -q && mq.py --config r.json lift 36023", env={"CLAUDE_LONG_RUNNING_LANE": "lane-b"}): Block(pattern=r"lane `lane-b` may not run `mq.py lift`"),
        Input(command="mq.py --config r.json why 36023", agent_id=DESK, state=ACTIVE): Allow(),
        Input(command="mq.py --config r.json status", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="mq.py --config r.json checks", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="mq.py --help", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="cat skills/long-running/scripts/mq.py", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="echo 'mq.py --config r.json dequeue 36023'", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="mq.py --config r.json dequeue 36023", state=ACTIVE): Allow(),
        Input(command="mq.py --config r.json dequeue 36023", agent_id=LANE): Allow(),
    },
)
def only_the_root_steers_the_queue(evt: BaseHookEvent) -> HookResult | None:
    if (lane := lane_name(evt)) is None:
        return None
    if evt.agent_id and not reqenv.getenv("CLAUDE_LONG_RUNNING_LANE") and not CompactionState.load(evt).active:
        return None
    verb = next((verb for call in evt.cmd.calls() if (verb := mq_verb(call))), None)
    return evt.block(STEERING_REFUSAL.format(lane=lane or "unnamed", verb=verb)) if verb else None
