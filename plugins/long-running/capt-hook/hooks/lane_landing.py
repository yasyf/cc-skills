from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Block, Call, Event, HookResult, Input, Tool, TranscriptFixture, on
from captain_hook.annotations import dispatch_prompt, header_lines, session_annotations
from captain_hook.util import reqenv

from .compaction_handoff import CCN_TIMEOUT_SECONDS, CompactionState, bus_name, drive_entry, script, script_module
from .register_injection import INCIDENT_NAME, INCIDENT_SLICE
from .session_tree import drive_lane

LANDERS = re.compile(r"^(?:desk-runner|merge-train)(?:-.+)?$|^(?:\w+-)*desk(?:-\d+)?$")
TEAMMATE_NAME = re.compile(r"^a(?P<name>.+)-[0-9a-f]{16}$")
QUEUE_LABELS = frozenset({"merge", "merge-fast"})
LABEL_FIELDS = frozenset({"labels[]", "labels"})
LABELS_ENDPOINT = re.compile(r"(?:^|/)repos/[^/]+/[^/]+/issues/\d+/labels(?:\?|$)")
READ_ONLY_ENQUEUE = frozenset({"--check", "--status", "-h", "--help"})
ENQUEUERS = frozenset({"stack-enqueue", "mq.py enqueue", "ci trunk submit", "gh stack merge"})
NOT_AN_ENQUEUE = frozenset({"--dequeue"})
QUEUE_STEERING = frozenset({"dequeue", "requeue", "priority", "recover", "hold", "lift"})
MQ_VALUE_OPTIONS = frozenset({"--config"})
GH_REPO_FLAGS = frozenset({"-R", "--repo"})
FIELD_FLAGS = ("--raw-field=", "--field=", "-f", "-F")
REPO_FLAGS = frozenset({"-R", "--repo"})
REPO_IN_ARG = re.compile(r"(?:^|/)repos/(?P<api>[\w.-]+/[\w.-]+)/|github\.com/(?P<url>[\w.-]+/[\w.-]+)/pull/")
MERGE_VALUE_FLAGS = frozenset({"--match-head-commit", "--body", "-b", "--body-file", "-F", "--subject", "-t", "--author-email", "-A"})
PULL = re.compile(r"(?:https://github\.com/[\w.-]+/[\w.-]+/pull/)?(?P<pr>\d+)/?")
CHECK_PR_CAUSES = frozenset({"RULINGS-UNCITED", "RESERVED-NO-ANSWER", "TEST-REMOVED", "SHAPE-UNTESTED", "FINDER-MAJOR", "MECHANISM-BANNED", "NAME-HIT", "RULINGS-PLAIN", "LANE-DECIDED", "CONTRACT-MISSING", "TESTS-CORPUS"})
INCIDENT_CAUSES = frozenset({"RULINGS-UNCITED", "RESERVED-NO-ANSWER", "TEST-REMOVED"})
CHECK_PR_TIMEOUT_SECONDS = 60
GREEN_EXCEPT = re.compile(r"\bgreen (?:except|apart from|other than|save for|but for)\b|\bknown[- ]red\b|\bred[- ]by[- ]design\b|\bdesign[- ]red\b", re.IGNORECASE)
RULING_KINDS = frozenset({"ready", "go", "decide", "decision"})
READY_TEXT = re.compile(r"^\W*READY\b")
FIXTURES = Path(__file__).parent / "tests" / "fixtures"
DRIVE_ENV = {"CLAUDE_LONG_RUNNING_DRIVE": "900424b6", "CLAUDE_CONFIG_DIR": str(FIXTURES / "claude-config")}
ACTIVE = [CompactionState(active=True)]
LANE = "alane-a-0123456789abcdef"
DESK = "alanding-desk-0123456789abcdef"
NOT_AN_INCIDENT = "none"
INCIDENT_BRIEF = TranscriptFixture([{"type": "user", "message": {"role": "user", "content": "ccx: incident=deadline-sweep\nFix the deadline sweep."}}])
NON_INCIDENT_BRIEF = TranscriptFixture([{"type": "user", "message": {"role": "user", "content": "ccx: incident=none\nFix the ledger."}}])
FIX_LANE = "ainc-fixruns-fix-0123456789abcdef"
EVIDENCE_LANE = "ainc-fixruns-evidence-0123456789abcdef"
PINNED_MERGE = "gh pr merge 44717 --squash --match-head-commit 0123456789abcdef0123456789abcdef01234567"
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
    "`rulings.py check-pr` names {causes} for #{pr} at {head}. These are warnings: the landing desk holds no head for them (4236611), "
    "and this hook only advises, so settle each one you can before the merge lands."
)
INCIDENT_RULINGS_REFUSAL = (
    "#{pr} {head}: RULINGS-REFUSED `rulings.py check-pr --incident` names these on the incident fix, so lane `{lane}` may not merge it until they clear (45200f2): {causes}. "
    "Fix each one and push, then merge the new head."
)
GREEN_EXCEPT_REFUSAL = (
    "`{phrase}` reads as red: a head with a failing required job is not green, however well known the job, and a red trunk is never red by design. {trunk}"
    "Report READY only once every required job on the head passes; a job that fails because dev is red waits for dev's fix."
)
TRUNK_BLOCKED = "dev's test build is red since {since}; nothing lands until {fix}. "
HAND_ENQUEUE_WARNING = (
    "A hand `stack-enqueue` is held to the landing runner's checks, and these heads fail them: {lines}. "
    "Clear each one before you enqueue, with a grade naming the head, a fidelity ok, or the owner's go."
)
HAND_ENQUEUE_TAP_REFUSAL = (
    "A hand `stack-enqueue` lands nothing the landing runner refuses for the owner's tap, and these heads have no approval on the root's board: {lines}. "
    "A root go is no tap: wait for his approval on the board row at that head, then enqueue."
)
TAP_REFUSAL = "TAP-REQUIRED"
ADMIN_MERGE_REFUSAL = (
    "Incident fixes merge by plain squash once approved and green, never past review, so lane `{lane}` may not run `gh pr merge --admin`. "
    "Wait for the approval and the required checks, then run `gh pr merge <N> --squash`."
)
INCIDENT_REFUSAL = (
    "Incident fixes land by plain squash the pass they are approved and green, never through the queue (the owner ruled it on Oct 20), so lane `{lane}` may not run `stack-enqueue`. "
    "Post `ready`, and the landing runner merges it."
)


def sliced_brief(lane: str, step: str) -> TranscriptFixture:
    return TranscriptFixture([{"type": "user", "message": {"role": "user", "content": f"Fix the failing runs.\nRegister slice: lane={lane} step={step}\nslice-hash: c97262b93f65"}}])


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
    if lane := drive_lane(evt):
        return lane
    if evt.agent_id:
        return match["name"] if (match := TEAMMATE_NAME.match(evt.agent_id)) else ""
    return None


def incident_lane(evt: BaseHookEvent, lane: str) -> bool:
    return bool(INCIDENT_NAME.match(lane)) or bool(lane) and (session_annotations(evt).get("incident") not in (None, NOT_AN_INCIDENT) or incident_fix_slice(evt, lane))


def incident_fix_slice(evt: BaseHookEvent, lane: str) -> bool:
    return lane.endswith("-fix") and INCIDENT_SLICE.format(lane=lane) in header_lines(dispatch_prompt(evt))


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


def mq_subcommand(call: Call) -> str:
    run = program((call.name, *call.args))
    if not run or Path(run[0]).name != "mq.py":
        return ""
    args = run[1:]
    index = 0
    while index < len(args) and args[index].startswith("-"):
        index += 2 if args[index] in MQ_VALUE_OPTIONS else 1
    return args[index] if index < len(args) else ""


def mq_verb(call: Call) -> str | None:
    verb = mq_subcommand(call)
    return verb if verb in QUEUE_STEERING else None


def landing_verb(call: Call) -> str | None:
    argv = without_repo((call.name, *call.args)) if call.name == "gh" else call.verb_argv
    run = program((call.name, *call.args))
    if run and Path(run[0]).name == "stack-enqueue":
        return None if READ_ONLY_ENQUEUE & set(run[1:]) else "stack-enqueue"
    if mq_subcommand(call) == "enqueue":
        return "mq.py enqueue"
    if run and Path(run[0]).name == "ci" and run[1:3] == ("trunk", "submit"):
        return "ci trunk submit"
    if argv[:3] == ("gh", "stack", "merge"):
        return "gh stack merge"
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
        Input(command="tools/ci trunk submit 36023", agent_id=LANE, state=ACTIVE): Block(pattern=r"`ci trunk submit`"),
        Input(command="tools/ci trunk status 36023", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="gh stack merge 36023", agent_id=LANE, state=ACTIVE): Block(pattern=r"`gh stack merge`"),
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
        Input(command="mq.py --config r.json enqueue 36023 --partial", agent_id=LANE, state=ACTIVE): Block(pattern=r"lane `lane-a` may not run `mq.py enqueue`"),
        Input(command="mq.py --config r.json enqueue 36023 --partial", agent_id=DESK, state=ACTIVE): Allow(),
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
        Input(command=PINNED_MERGE, agent_id=LANE, state=ACTIVE, transcript=INCIDENT_BRIEF): Allow(),
        Input(command=PINNED_MERGE, agent_id=LANE, state=ACTIVE, transcript=NON_INCIDENT_BRIEF): Block(pattern=r"lane `lane-a` may not run `gh pr merge`"),
        Input(command="stack-enqueue 44717", agent_id=LANE, state=ACTIVE, transcript=INCIDENT_BRIEF): Block(pattern=r"^Incident fixes land by plain squash"),
        Input(command="gh pr merge 44811 --squash", agent_id=FIX_LANE, state=ACTIVE, transcript=sliced_brief("inc-fixruns-fix", "incident")): Allow(),
        Input(command="gh pr merge 44811 --squash --admin", agent_id=FIX_LANE, state=ACTIVE, transcript=sliced_brief("inc-fixruns-fix", "incident")): Block(pattern=r"^Incident fixes merge by plain squash .* lane `inc-fixruns-fix` may not run `gh pr merge --admin`\."),
        Input(command="gh pr merge 36050 --admin --squash", agent_id="aincident-api-500s-0123456789abcdef", state=ACTIVE): Block(pattern=r"`gh pr merge --admin`"),
        Input(command="stack-enqueue 44811", agent_id=FIX_LANE, state=ACTIVE, transcript=sliced_brief("inc-fixruns-fix", "incident")): Block(pattern=r"^Incident fixes land by plain squash"),
        Input(command="gh pr merge 44811 --squash", agent_id=EVIDENCE_LANE, state=ACTIVE, transcript=sliced_brief("inc-fixruns-evidence", "incident")): Block(pattern=r"lane `inc-fixruns-evidence` may not run `gh pr merge`"),
        Input(command="gh pr merge 44811 --squash", agent_id=FIX_LANE, state=ACTIVE, transcript=sliced_brief("inc-fixruns-fix", "1.9")): Block(pattern=r"lane `inc-fixruns-fix` may not run `gh pr merge`"),
        Input(command="gh pr merge 44811 --squash", agent_id=FIX_LANE, state=ACTIVE, transcript=sliced_brief("other-fix", "incident")): Block(pattern=r"lane `inc-fixruns-fix` may not run `gh pr merge`"),
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
    landings = [(call, verb) for call in evt.cmd.calls() if (verb := landing_verb(call)) and (verb == "mq.py enqueue" or not outside_drive_repo(evt, call, entry))]
    if not landings:
        return None
    verbs = [verb for _, verb in landings]
    incident = incident_lane(evt, lane)
    if incident and any(verb == "gh pr merge" and "--admin" in call.args for call, verb in landings):
        return evt.block(ADMIN_MERGE_REFUSAL.format(lane=lane))
    verb = next((verb for verb in verbs if verb in ENQUEUERS or not incident), None)
    if verb and incident:
        return evt.block(INCIDENT_REFUSAL.format(lane=lane))
    return evt.block(REFUSAL.format(lane=lane or "unnamed", verb=verb)) if verb else None


def gate_args(call: Call) -> list[str]:
    """The `mq.py gate` operands for a `stack-enqueue` call: its PRs and its `--partial`, or none for a call that enqueues nothing."""
    args = program((call.name, *call.args))[1:]
    prs = [arg for arg in args if arg.isdigit()]
    return [*prs, *(["--partial"] if "--partial" in args else [])] if prs and not NOT_AN_ENQUEUE & set(args) else []


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash")],
    tests={
        Input(command=ENQUEUE, agent_id=DESK, env=DRIVE_ENV, state=ACTIVE): Allow(),
        Input(command="stack-enqueue 36023 --check", env=DRIVE_ENV): Allow(),
        Input(command="stack-enqueue 36023 --dequeue", env=DRIVE_ENV): Allow(),
        Input(command=ENQUEUE): Allow(),
    },
)
def hand_enqueue_meets_the_runner_checks(evt: BaseHookEvent) -> HookResult | None:
    if (entry := drive_entry(evt)) is None:
        return None
    gates = [args for call in evt.cmd.calls() if landing_verb(call) == "stack-enqueue" and not outside_drive_repo(evt, call, entry) and (args := gate_args(call))]
    if not gates or not (config := Path(entry["state_dir"]) / "runner.json").is_file():
        return None
    runs = [run_quietly([sys.executable, str(script("mq.py")), "--config", str(config), "gate", *args], str(evt.cwd)) for args in gates]
    failing = [line for done in runs if done is not None and done.returncode == 1 for line in done.stdout.splitlines() if line.strip()]
    if untapped := [line for line in failing if TAP_REFUSAL in line]:
        return evt.block(HAND_ENQUEUE_TAP_REFUSAL.format(lines="; ".join(untapped)))
    return evt.context(HAND_ENQUEUE_WARNING.format(lines="; ".join(failing))) if failing else None


def merge_target(call: Call) -> tuple[str, str]:
    """The selector a `gh pr merge` names, a number, URL, or branch, empty for the current branch's PR, and the head its `--match-head-commit` pins, or empty."""
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
    return target, head


def merged_pr(call: Call, repo: str, cwd: str) -> tuple[str, str] | None:
    """The number and head of the PR a `gh pr merge` call merges: a number or URL with a pinned head reads nothing, and any other selector asks `gh pr view`; None when that does not read."""
    target, head = merge_target(call)
    if (found := PULL.fullmatch(target)) and head:
        return found["pr"], head
    selector = [target, "--repo", repo] if target else []
    viewed = run_quietly(["gh", "pr", "view", *selector, "--json", "number,headRefOid", "--jq", '"\\(.number) \\(.headRefOid)"'], cwd)
    if viewed is None or viewed.returncode or len(fields := viewed.stdout.split()) != 2:
        return None
    return fields[0], head or fields[1]


def run_quietly(argv: list[str], cwd: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=CHECK_PR_TIMEOUT_SECONDS, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired):
        return None


def merge_causes(call: Call, entry: dict, cwd: str, incident: bool) -> tuple[str, str, str] | None:
    """The PR, head and joined refusals `rulings.py check-pr` prints for one `gh pr merge` call; None when it prints none or anything does not read."""
    repo = named_repo(call) or entry["repo"]
    if (merged := merged_pr(call, repo, cwd)) is None:
        return None
    pr, head = merged
    done = run_quietly([sys.executable, str(script("rulings.py")), "check-pr", "--repo", cwd, "--drive", entry["drive"], "--pr", f"{repo}#{pr}", "--head", head, *(["--incident"] if incident else [])], cwd)
    if done is None or done.returncode not in (0, 1):
        return None
    lines = [line.strip() for line in done.stdout.splitlines() if line.strip()]
    named = [line for line in lines if line.split(" ", 1)[0] in CHECK_PR_CAUSES]
    if any(line.split(" ", 1)[0] not in CHECK_PR_CAUSES | {"BLOCKER"} for line in lines) or (done.returncode == 1) != bool(named):
        return None
    causes = [line for line in named if line.split(" ", 1)[0] in (INCIDENT_CAUSES if incident else CHECK_PR_CAUSES)]
    return (pr, head[:10], "; ".join(causes)) if causes else None


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash")],
    tests={
        Input(command="gh pr merge 36023 --squash"): Allow(),
        Input(command="gh pr merge 81 -R yasyf/cc-inbox --squash", env=DRIVE_ENV): Allow(),
        Input(command="gh pr view 36023 -R o/r --json mergedAt", env=DRIVE_ENV): Allow(),
    },
)
def rulings_at_merge(evt: BaseHookEvent) -> HookResult | None:
    if (entry := drive_entry(evt)) is None:
        return None
    merges = [call for call in evt.cmd.calls() if landing_verb(call) == "gh pr merge" and not outside_drive_repo(evt, call, entry)]
    if not merges:
        return None
    lane = lane_name(evt) or ""
    incident = incident_lane(evt, lane)
    found = [checked for call in merges if (checked := merge_causes(call, entry, str(call.cwd or evt.cwd), incident))]
    if incident and found:
        return evt.block("\n".join(INCIDENT_RULINGS_REFUSAL.format(pr=pr, head=head, lane=lane, causes=causes) for pr, head, causes in found))
    advice = [RULINGS_ADVICE.format(causes=causes, pr=pr, head=head) for pr, head, causes in found]
    return evt.context("\n".join(advice)) if advice else None


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
    if verb:
        return evt.block(STEERING_REFUSAL.format(lane=lane or "unnamed", verb=verb))
    for call in evt.cmd.calls():
        run = program((call.name, *call.args))
        if len(run) > 2 and Path(run[0]).name == "ci" and run[1] == "trunk" and run[2] in ("dequeue", "restart", "prioritize"):
            return evt.block(f"Only the root steers the merge queue, so lane `{lane or 'unnamed'}` may not run `ci trunk {run[2]}`. Ask the root and read the queue with `mq.py why <n>`.")
    return None


def option(args: tuple[str, ...], name: str) -> list[str]:
    return [value for flag, value in zip(args, args[1:], strict=False) if flag == name] + [arg.split("=", 1)[1] for arg in args if arg.startswith(f"{name}=")]


def green_except(call: Call) -> str | None:
    """The phrase in a lane's READY report or a root ruling that grades a red head green: `ledger.py report`'s text, a `cci post` report whose text opens READY, or any `cci post` text of a kind that rules on a head."""
    run = program((call.name, *call.args))
    if not run:
        return None
    name, args = Path(run[0]).name, run[1:]
    kinds = set(option(args, "--kind"))
    ruling = name == "cci" and args[:1] == ("post",) and bool(kinds & RULING_KINDS or ("report" in kinds and any(READY_TEXT.match(arg.removeprefix("--text=")) for arg in args)))
    if not (name == "ledger.py" and "report" in args) and not ruling:
        return None
    return next((found[0] for text in args if (found := GREEN_EXCEPT.search(text))), None)


def trunk_clause(entry: dict) -> str:
    """The sentence naming dev's red build and its fix while the landing runner's newest `dev-red` record bars landing; empty when it does not, or the records do not read."""
    cci = script_module("cci")

    def tail(argv: list[str]) -> str:
        try:
            done = subprocess.run(argv, capture_output=True, text=True, timeout=CCN_TIMEOUT_SECONDS, env=dict(reqenv.env_map()))
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return done.stdout if done.returncode == 0 else ""

    trunk = cci.trunk(cci.records(tail, bus_name(entry), f"--topic={cci.TRUNK_TOPIC}"))
    return TRUNK_BLOCKED.format(since=trunk.since, fix=f"#{trunk.fix} fixes it" if trunk.fix else "its fix lands") if trunk.blocked else ""


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash")],
    tests={
        Input(command="ledger.py report --pr 43163 --head abc --verdict clean --text 'READY CI green except the known red'", env=DRIVE_ENV): Block(pattern=r"^`green except` reads as red"),
        Input(command="python3 ledger.py -C ~/m report --pr 43163 --text 'READY: green apart from the known publish job'", env=DRIVE_ENV): Block(pattern=r"`green apart from`"),
        Input(command="cci post --drive d --lane root --kind decide --text 'dev is design red; land on'", env=DRIVE_ENV): Block(pattern=r"`design red`"),
        Input(command="cci post --drive d --lane x --kind=report --text='READY #43163: known-red test job only'", env=DRIVE_ENV): Block(pattern=r"`known-red`"),
        Input(command="cci post --drive d --lane x --kind report 'READY #43163: green except the publish job'", env=DRIVE_ENV): Block(pattern=r"`green except`"),
        Input(command="ledger.py report --pr 43163 --head abc --verdict clean --text 'READY CI green on build 82560'", env=DRIVE_ENV): Allow(),
        Input(command="cci post --drive d --lane x --kind note --text 'retro: lanes graded green except the known red by hand'", env=DRIVE_ENV): Allow(),
        Input(command="cci post --drive d --lane x --kind report --text 'DONE: the hook now refuses green except and known red claims'", env=DRIVE_ENV): Allow(),
        Input(command="ledger.py report --pr 43163 --text 'READY CI green except the known red'"): Allow(),
    },
)
def green_except_is_red(evt: BaseHookEvent) -> HookResult | None:
    if (entry := drive_entry(evt)) is None:
        return None
    phrase = next((found for call in evt.cmd.calls() if (found := green_except(call))), None)
    return evt.block(GREEN_EXCEPT_REFUSAL.format(phrase=phrase, trunk=trunk_clause(entry))) if phrase else None
