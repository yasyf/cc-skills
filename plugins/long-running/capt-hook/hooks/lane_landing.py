from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Block, Call, Event, HookResult, Input, Tool, TranscriptFixture, Warn, Word, on
from captain_hook.annotations import dispatch_prompt, header_lines, session_annotations
from captain_hook.bindings import Resolved
from captain_hook.util import reqenv

from .call_dir import run_dir, unknown_dir
from .compaction_handoff import CCN_TIMEOUT_SECONDS, Bound, CompactionState, bus_name, drive_entry, rulings, run_script, script, script_module
from .register_injection import INCIDENT_NAME, INCIDENT_SLICE
from .session_tree import drive_lane

LANDERS = re.compile(r"^(?:desk-runner|merge-train)(?:-.+)?$|^(?:\w+-)*desk(?:-\d+)?$")
TEAMMATE_NAME = re.compile(r"^a(?P<name>.+)-[0-9a-f]{16}$")
QUEUE_LABELS = frozenset({"merge", "merge-fast"})
LABEL_FIELDS = frozenset({"labels[]", "labels"})
LABELS_ENDPOINT = re.compile(r"(?:^|/)repos/[^/]+/[^/]+/issues/(?P<pr>\d+)/labels(?:\?|$)")
MERGE_ENDPOINT = re.compile(r"(?:^|/)repos/[^/]+/[^/]+/pulls/(?P<pr>\d+)/merge$")
PUT_METHOD = re.compile(r"(?:^|\s)(?:-X\s*|--method[=\s])PUT(?:\s|$)", re.IGNORECASE)
API_MERGE = "gh api merge"
HELP_FLAGS = frozenset({"--help", "-h"})
READ_ONLY_ENQUEUE = frozenset({"--check", "--status"}) | HELP_FLAGS
ENQUEUERS = frozenset({"stack-enqueue", "ci trunk submit", "gh stack merge"})
MERGERS = frozenset({"gh pr merge", "ccx vcs merge"})
NOT_AN_ENQUEUE = frozenset({"--dequeue"})
GH_REPO_FLAGS = frozenset({"-R", "--repo"})
FIELD_FLAGS = ("--raw-field=", "--field=", "-f", "-F")
REPO_FLAGS = frozenset({"-R", "--repo"})
REPO_IN_ARG = re.compile(r"(?:^|/)repos/(?P<api>[\w.-]+/[\w.-]+)/|github\.com/(?P<url>[\w.-]+/[\w.-]+)/pull/")
MERGE_VALUE_FLAGS = frozenset({"--match-head-commit", "--body", "-b", "--body-file", "-F", "--subject", "-t", "--author-email", "-A", "--merge-method"})
GITHUB_REMOTE = re.compile(r"github\.com[:/](?P<repo>[\w.-]+/[\w.-]+?)(?:\.git)?/?$")
LANDING_REPO_ENV = "CLAUDE_LONG_RUNNING_LANDING_REPO"
PULL = re.compile(r"(?:https://github\.com/[\w.-]+/[\w.-]+/pull/)?(?P<pr>\d+)/?")
CHECK_PR_CAUSES = frozenset({"RULINGS-UNCITED", "RESERVED-NO-ANSWER", "TEST-REMOVED", "SHAPE-UNTESTED", "FINDER-MAJOR", "MECHANISM-BANNED", "NAME-HIT", "LANE-DECIDED", "STORE-UNCOUNTED", "CONTRACT-MISSING", "TESTS-CORPUS"})
INCIDENT_CAUSES = frozenset({"RULINGS-UNCITED", "RESERVED-NO-ANSWER", "TEST-REMOVED"})
CHECK_PR_TIMEOUT_SECONDS = 60
LANDING_READ_SECONDS = 15
GREEN_EXCEPT = re.compile(r"\bgreen (?:except|apart from|other than|save for|but for)\b|\bknown[- ]red\b|\bred[- ]by[- ]design\b|\bdesign[- ]red\b", re.IGNORECASE)
RULING_KINDS = frozenset({"ready", "go", "decide", "decision"})
READY_TEXT = re.compile(r"^\W*READY\b")
FIXTURES = Path(__file__).parent / "tests" / "fixtures"
DRIVE_ENV = {"CLAUDE_LONG_RUNNING_DRIVE": "900424b6", "CLAUDE_CONFIG_DIR": str(FIXTURES / "claude-config")}
SPRITE_ENV = DRIVE_ENV | {"CLAUDE_CONFIG_DIR": str(FIXTURES / "sprite-config")}
MONOREPO = "forge-ai/monorepo"
MONOREPO_ENV = {"CLAUDE_LONG_RUNNING_DRIVE": "5c0e81aa", "CLAUDE_CONFIG_DIR": str(FIXTURES / "gate5-config")}
LANDING_ENV = {LANDING_REPO_ENV: "Forge-AI/monorepo"}
CHECKOUT = "/w/monorepo"
SLACK_CHECKOUT = "/w/cc-slack"
ORIGINS = {f"git -C {CHECKOUT} remote get-url origin": "https://github.com/Forge-AI/monorepo\n", f"git -C {SLACK_CHECKOUT} remote get-url origin": "git@github.com:yasyf/cc-slack.git\n"}
landing = partial(Input, cwd=CHECKOUT, env=LANDING_ENV, commands=ORIGINS)
MONOREPO_GATE = "lr landing gate --config /s/durable-agents-rebuild/lr.json"
UNTAPPED = "#7 0123456789 TAP-REQUIRED no approval on the owner's board at this head\n"
UNTESTED = "#7 0123456789 QUEUE-UNTESTED Trunk Merge Queue (dev) has not passed at this head\n"
MONOREPO_DIRECT_GATE = f"{MONOREPO_GATE} 7 --partial --direct"
ACTIVE = [CompactionState(active=True, coordinator=True)]
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
    "Warning, nothing was blocked: Lanes never enqueue: the landing desk is the only enqueuer, so lane `{lane}` may not run `{verb}`. "
    "Report the PR with `ledger.py report --drive <drive>`, and the landing desk enqueues it against the coordinator's holds."
)
UNREAD_REPO = (
    "Warning, nothing was blocked: This hook could not tell whether `{verb}` lands on the landing desk's repo, where lanes never enqueue. "
    "On that repo, report the PR with `ledger.py report --drive <drive>`; on any other, go ahead."
)
RULINGS_ADVICE = (
    "`rulings.py check-pr` names {causes} for #{pr} at {head}. These are warnings: the landing desk holds no head for them (4236611), "
    "and this hook only advises, so settle each one you can before the merge lands."
)
INCIDENT_RULINGS_REFUSAL = (
    "Warning, nothing was blocked: #{pr} {head}: RULINGS-REFUSED `rulings.py check-pr --incident` names these on the incident fix, so lane `{lane}` may not merge it until they clear (45200f2): {causes}. "
    "Fix each one and push, then merge the new head."
)
GREEN_EXCEPT_REFUSAL = (
    "Warning, nothing was blocked: `{phrase}` reads as red: a head with a failing required job is not green, however well known the job, and a red trunk is never red by design. {trunk}"
    "Report READY only once every required job on the head passes; a job that fails because dev is red waits for dev's fix."
)
TRUNK_BLOCKED = "dev's test build is red since {since}; nothing lands until {fix}. "
HAND_ENQUEUE_WARNING = (
    "A hand `stack-enqueue` is held to the landing runner's checks, and these heads fail them: {lines}. "
    "Clear each one before you enqueue, with a grade naming the head, a fidelity ok, or the owner's go."
)
HAND_ENQUEUE_TAP_REFUSAL = (
    "Warning, nothing was blocked: A hand `stack-enqueue` lands nothing the landing runner refuses for the owner's tap, and these heads have no approval on the coordinator's board: {lines}. "
    "A coordinator go is no tap: wait for his approval on the board row at that head, then enqueue."
)
TAP_REFUSAL = "TAP-REQUIRED"
GATE_UNREAD = "Warning, nothing was refused:"
HAND_LANDING_REFUSAL = (
    "A hand landing needs the owner's tap at its head, and the runner's read finds none: {lines}. "
    "Ask him for it with a board card at that head, or report the PR with `ledger.py report` for the runner to land."
)
QUEUE_UNTESTED = "QUEUE-UNTESTED"
HAND_MERGE_UNTESTED = (
    "Warning, nothing was blocked: A merge outside the queue lands untested, and the queue has not passed these heads: {lines}. "
    "Report the PR with `ledger.py report` for the runner to queue it."
)
TAP_UNREAD = "Warning, nothing was blocked: the owner's tap did not read for this landing, so it goes ahead unchecked for it: {failure}"
ADMIN_MERGE_REFUSAL = (
    "Warning, nothing was blocked: Incident fixes merge by plain squash once approved and green, never past review, so lane `{lane}` may not run `{verb} --admin`. "
    "Wait for the approval and the required checks, then run `{verb} <N> --squash`."
)
INCIDENT_REFUSAL = (
    "Warning, nothing was blocked: Incident fixes land by plain squash the pass they are approved and green, never through the queue (the owner ruled it on Oct 20), so lane `{lane}` may not run `stack-enqueue`. "
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
    return repo_flag(call) or next((found["api"] or found["url"] for arg in call.args if (found := REPO_IN_ARG.search(arg))), None)


def repo_flag(call: Call) -> str | None:
    args = call.args
    if repo := next((value for flag, value in zip(args, args[1:], strict=False) if flag in REPO_FLAGS), None):
        return repo
    return next((arg.split("=", 1)[1] for arg in args if arg.startswith(("--repo=", "-R="))), None)


def named_repos(call: Call) -> tuple[str, ...] | None:
    words = call.command.words[1:]
    for flag, word in zip((None, *words), words, strict=False):
        if flag is not None and flag.value in REPO_FLAGS:
            return word_values(call, word)
        if (word.value or word.raw).startswith(("--repo=", "-R=")):
            values = word_values(call, word)
            return None if values is None else tuple(value.split("=", 1)[1] for value in values)
    return next(((found["api"] or found["url"],) for arg in call.args if (found := REPO_IN_ARG.search(arg))), ())


def word_values(call: Call, word: Word) -> tuple[str, ...] | None:
    if word.value is not None:
        return (word.value,)
    resolved = call.resolve(word)
    return resolved.candidates if isinstance(resolved, Resolved) else None


def outside_drive_repo(evt: BaseHookEvent, call: Call, entry: dict | None) -> bool:
    if entry is None:
        return False
    if repo := named_repo(call):
        return repo.casefold() != entry["repo"].casefold()
    if unknown_dir(evt, call):
        return False
    done = subprocess.run(["git", "-C", run_dir(evt, call), "rev-parse", "--path-format=absolute", "--git-common-dir"], capture_output=True, text=True)
    return done.returncode == 0 and done.stdout.strip() != entry["git_common_dir"]


def on_landing_repo(evt: BaseHookEvent, call: Call, entry: dict | None) -> bool | None:
    if not (landing_repo := entry["repo"] if entry else reqenv.getenv(LANDING_REPO_ENV)):
        return True
    if (repos := named_repos(call)) is None or not repos and unknown_dir(evt, call):
        return None
    if repos:
        return any(repo.casefold() == landing_repo.casefold() for repo in repos)
    if entry:
        return not outside_drive_repo(evt, call, entry)
    return (origin := origin_repo(run_dir(evt, call))) is None or origin.casefold() == landing_repo.casefold()


def origin_repo(cwd: str) -> str | None:
    done = subprocess.run(["git", "-C", cwd, "remote", "get-url", "origin"], capture_output=True, text=True)
    return found["repo"] if done.returncode == 0 and (found := GITHUB_REMOTE.search(done.stdout.strip())) else None


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


def landing_verb(call: Call) -> str | None:
    argv = without_repo((call.name, *call.args)) if call.name == "gh" else call.verb_argv
    run = program((call.name, *call.args))
    if run and Path(run[0]).name == "stack-enqueue":
        return None if READ_ONLY_ENQUEUE & set(run[1:]) else "stack-enqueue"
    if run and Path(run[0]).name == "ci" and run[1:3] == ("trunk", "submit"):
        return "ci trunk submit"
    if argv[:3] == ("gh", "stack", "merge"):
        return "gh stack merge"
    if argv[:2] == ("gt", "merge"):
        return "gt merge"
    if argv[:3] == ("gh", "pr", "merge"):
        return "gh pr merge"
    if argv[:3] == ("ccx", "vcs", "merge"):
        return "ccx vcs merge"
    if argv[:2] == ("gh", "api") and any(MERGE_ENDPOINT.search(arg) for arg in argv[2:]) and PUT_METHOD.search(" ".join(argv)):
        return API_MERGE
    if argv[:3] in {("gh", "pr", "edit"), ("gh", "issue", "edit")} and label_args(argv[3:]) & QUEUE_LABELS:
        return f"{' '.join(argv[:3])} --add-label"
    if argv[:2] == ("gh", "api") and any(LABELS_ENDPOINT.search(arg) for arg in argv[2:]) and labelled_fields(argv[2:]) & QUEUE_LABELS:
        return "gh api labels"
    if call.name == "ledger.py" and argv[1:2] == ("label",) and "--dry-run" not in argv:
        return "ledger.py label"
    return None


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash")],
    tests={
        landing(command=ENQUEUE, agent_id=LANE, state=ACTIVE): Warn(pattern=r"^Warning, nothing was blocked: Lanes never enqueue: the landing desk is the only enqueuer, so lane `lane-a` may not run `stack-enqueue`\."),
        landing(command="stack-enqueue 36039 --fast", agent_id="aL23-dashboard-wasm-follower-14fd0a86e696c1e3", state=ACTIVE): Warn(pattern=r"`L23-dashboard-wasm-follower`"),
        landing(command="tools/ci trunk submit 36023", agent_id=LANE, state=ACTIVE): Warn(pattern=r"`ci trunk submit`"),
        landing(command="tools/ci trunk status 36023", agent_id=LANE, state=ACTIVE): Allow(),
        landing(command="gh stack merge 36023", agent_id=LANE, state=ACTIVE): Warn(pattern=r"`gh stack merge`"),
        landing(command="cd ~/wt && gt merge", agent_id=LANE, state=ACTIVE): Warn(pattern=r"`gt merge`"),
        landing(command="gh pr merge 36023 --squash", agent_id=LANE, state=ACTIVE): Warn(pattern=r"`gh pr merge`"),
        landing(command="ccx vcs merge 36023", agent_id=LANE, state=ACTIVE): Warn(pattern=r"lane `lane-a` may not run `ccx vcs merge`"),
        landing(command="gh -R Forge-AI/monorepo pr merge 36023 --squash", agent_id=LANE, state=ACTIVE): Warn(pattern=r"`gh pr merge`"),
        landing(command="python3 .agents/skills/submit-pr/scripts/stack-enqueue 36023", agent_id=LANE, state=ACTIVE): Warn(pattern=r"`stack-enqueue`"),
        landing(command="bash .agents/skills/submit-pr/scripts/stack-enqueue 36023", agent_id=LANE, state=ACTIVE): Warn(pattern=r"`stack-enqueue`"),
        landing(command="python3 -W ignore .agents/skills/submit-pr/scripts/stack-enqueue 36023", agent_id=LANE, state=ACTIVE): Warn(pattern=r"`stack-enqueue`"),
        landing(command="uv run --with requests .agents/skills/submit-pr/scripts/stack-enqueue 36023", agent_id=LANE, state=ACTIVE): Warn(pattern=r"`stack-enqueue`"),
        landing(command=".agents/skills/submit-pr/scripts/stack-enqueue --fast 36023", agent_id=LANE, state=ACTIVE): Warn(pattern=r"`stack-enqueue`"),
        landing(command="git fetch -q && stack-enqueue 36023", agent_id=LANE, state=ACTIVE): Warn(pattern=r"`stack-enqueue`"),
        landing(command="true; stack-enqueue 36023", agent_id=LANE, state=ACTIVE): Warn(pattern=r"`stack-enqueue`"),
        landing(command="true | .agents/skills/submit-pr/scripts/stack-enqueue 36023", agent_id=LANE, state=ACTIVE): Warn(pattern=r"`stack-enqueue`"),
        landing(command="echo $(stack-enqueue 36023)", agent_id=LANE, state=ACTIVE): Warn(pattern=r"`stack-enqueue`"),
        landing(command="sudo stack-enqueue 36023", agent_id=LANE, state=ACTIVE): Warn(pattern=r"`stack-enqueue`"),
        landing(command="env GH_TOKEN=x stack-enqueue 36023", agent_id=LANE, state=ACTIVE): Warn(pattern=r"`stack-enqueue`"),
        landing(command="cc-sudo run -- .agents/skills/submit-pr/scripts/stack-enqueue 36023", agent_id=LANE, state=ACTIVE): Warn(pattern=r"`stack-enqueue`"),
        landing(command="bash -c 'stack-enqueue 36023'", agent_id=LANE, state=ACTIVE): Warn(pattern=r"`stack-enqueue`"),
        landing(command="python3 -m py_compile .agents/skills/submit-pr/scripts/stack-enqueue", agent_id=LANE, state=ACTIVE): Allow(),
        landing(command="bash -n .agents/skills/submit-pr/scripts/stack-enqueue", agent_id=LANE, state=ACTIVE): Allow(),
        landing(command="python3 -c 'print(\"stack-enqueue 36023\")'", agent_id=LANE, state=ACTIVE): Allow(),
        landing(command="cci post --drive release-v3 --lane x --kind report --to root 'stack-enqueue --fast 36023 now costs one query'", agent_id=LANE, state=ACTIVE): Allow(),
        landing(command="git diff -- .agents/skills/submit-pr/scripts/stack-enqueue", agent_id=LANE, state=ACTIVE): Allow(),
        landing(command="cat .agents/skills/submit-pr/scripts/stack-enqueue", agent_id=LANE, state=ACTIVE): Allow(),
        landing(command="echo ok # stack-enqueue 36023", agent_id=LANE, state=ACTIVE): Allow(),
        landing(command="cc-sudo run -- stack-enqueue --check 36023", agent_id=LANE, state=ACTIVE): Allow(),
        landing(command="gh api repos/Forge-AI/monorepo/issues/36023/labels '--raw-field=labels[]=merge'", agent_id=LANE, state=ACTIVE): Warn(pattern=r"`gh api labels`"),
        landing(command="gh api repos/Forge-AI/monorepo/issues/36023/labels -flabels[]=merge-fast", agent_id=LANE, state=ACTIVE): Warn(),
        landing(command="gh api -X PUT repos/Forge-AI/monorepo/pulls/36023/merge -f merge_method=squash", agent_id=LANE, state=ACTIVE): Warn(pattern=r"`gh api merge`"),
        landing(command="gh api repos/Forge-AI/monorepo/pulls/36023/merge", agent_id=LANE, state=ACTIVE): Allow(),
        landing(command="gh pr edit 36023 --add-label merge", agent_id=LANE, state=ACTIVE): Warn(pattern=r"`gh pr edit --add-label`"),
        landing(command="gh pr edit 36023 --add-label=docs,MERGE-FAST", agent_id=LANE, state=ACTIVE): Warn(),
        landing(command="gh issue edit 36023 --add-label merge", agent_id=LANE, state=ACTIVE): Warn(),
        landing(command="gh api -X POST repos/Forge-AI/monorepo/issues/36023/labels -f 'labels[]=merge'", agent_id=LANE, state=ACTIVE): Warn(pattern=r"`gh api labels`"),
        landing(command="ledger.py label --ledger 1a2b --pr 36023 --expect-head abc", agent_id=LANE, state=ACTIVE): Warn(pattern=r"`ledger.py label`"),
        landing(command=ENQUEUE, env=LANDING_ENV | {"CLAUDE_LONG_RUNNING_LANE": "lane-b"}): Warn(pattern=r"`lane-b`"),
        landing(command=ENQUEUE, agent_id=DESK, state=ACTIVE): Allow(),
        landing(command=ENQUEUE, agent_id="alanding-desk-2-0123456789abcdef", state=ACTIVE): Allow(),
        landing(command=ENQUEUE, agent_id="adesk-runner-0123456789abcdef", state=ACTIVE): Allow(),
        landing(command=ENQUEUE, env=LANDING_ENV | {"CLAUDE_LONG_RUNNING_LANE": "landing-desk"}): Allow(),
        landing(command=ENQUEUE, agent_id="asandsql-replica-desk-0123456789abcdef", state=ACTIVE): Allow(),
        landing(command="ledger.py label --pr 36023 --expect-head abc", agent_id="amerge-train-release-0123456789abcdef", state=ACTIVE): Allow(),
        landing(command=ENQUEUE, agent_id="adesk-cleanup-lane-0123456789abcdef", state=ACTIVE): Warn(),
        landing(command="stack-enqueue 36050 --fast", agent_id="aincident-api-500s-0123456789abcdef", state=ACTIVE): Warn(pattern=r"plain squash"),
        landing(command="stack-enqueue 36050", agent_id="aoutage-sand-0123456789abcdef", state=ACTIVE): Warn(pattern=r"^Warning, nothing was blocked: Incident fixes land by plain squash .* lane `outage-sand` may not run `stack-enqueue`\."),
        landing(command="stack-enqueue 36050 --check", agent_id="aincident-api-500s-0123456789abcdef", state=ACTIVE): Allow(),
        landing(command="stack-enqueue 36050 --fast", agent_id="asand-5a-fix-0123456789abcdef", state=ACTIVE): Warn(pattern=r"lane `sand-5a-fix` may not run `stack-enqueue`"),
        landing(command="gh pr edit 36050 --add-label merge-fast", agent_id="asand-5a-fix-0123456789abcdef", state=ACTIVE): Warn(pattern=r"lane `sand-5a-fix` may not run `gh pr edit --add-label`"),
        landing(command="gh pr merge 36050 --squash", agent_id="aincident-api-500s-0123456789abcdef", state=ACTIVE): Allow(),
        landing(command="ccx vcs merge 36050", agent_id="aincident-api-500s-0123456789abcdef", state=ACTIVE): Allow(),
        landing(command="ccx vcs merge 36050 --admin", agent_id="aincident-api-500s-0123456789abcdef", state=ACTIVE): Warn(pattern=r"may not run `ccx vcs merge --admin`"),
        landing(command=PINNED_MERGE, agent_id=LANE, state=ACTIVE, transcript=INCIDENT_BRIEF): Allow(),
        landing(command=PINNED_MERGE, agent_id=LANE, state=ACTIVE, transcript=NON_INCIDENT_BRIEF): Warn(pattern=r"lane `lane-a` may not run `gh pr merge`"),
        landing(command="stack-enqueue 44717", agent_id=LANE, state=ACTIVE, transcript=INCIDENT_BRIEF): Warn(pattern=r"^Warning, nothing was blocked: Incident fixes land by plain squash"),
        landing(command="gh pr merge 44811 --squash", agent_id=FIX_LANE, state=ACTIVE, transcript=sliced_brief("inc-fixruns-fix", "incident")): Allow(),
        landing(command="gh pr merge 44811 --squash --admin", agent_id=FIX_LANE, state=ACTIVE, transcript=sliced_brief("inc-fixruns-fix", "incident")): Warn(pattern=r"^Warning, nothing was blocked: Incident fixes merge by plain squash .* lane `inc-fixruns-fix` may not run `gh pr merge --admin`\."),
        landing(command="gh pr merge 36050 --admin --squash", agent_id="aincident-api-500s-0123456789abcdef", state=ACTIVE): Warn(pattern=r"`gh pr merge --admin`"),
        landing(command="stack-enqueue 44811", agent_id=FIX_LANE, state=ACTIVE, transcript=sliced_brief("inc-fixruns-fix", "incident")): Warn(pattern=r"^Warning, nothing was blocked: Incident fixes land by plain squash"),
        landing(command="gh pr merge 44811 --squash", agent_id=EVIDENCE_LANE, state=ACTIVE, transcript=sliced_brief("inc-fixruns-evidence", "incident")): Warn(pattern=r"lane `inc-fixruns-evidence` may not run `gh pr merge`"),
        landing(command="gh pr merge 44811 --squash", agent_id=FIX_LANE, state=ACTIVE, transcript=sliced_brief("inc-fixruns-fix", "1.9")): Warn(pattern=r"lane `inc-fixruns-fix` may not run `gh pr merge`"),
        landing(command="gh pr merge 44811 --squash", agent_id=FIX_LANE, state=ACTIVE, transcript=sliced_brief("other-fix", "incident")): Warn(pattern=r"lane `inc-fixruns-fix` may not run `gh pr merge`"),
        landing(command=ENQUEUE, state=ACTIVE): Allow(),
        landing(command=ENQUEUE, agent_id=LANE): Allow(),
        landing(command="stack-enqueue 36023 --check --hold 30082", agent_id=LANE, state=ACTIVE): Allow(),
        landing(command="stack-enqueue --status 36023 36039", agent_id=LANE, state=ACTIVE): Allow(),
        landing(command="gh pr edit 36023 --add-label docs --remove-label merge", agent_id=LANE, state=ACTIVE): Allow(),
        landing(command="gh api -X DELETE repos/Forge-AI/monorepo/issues/36023/labels/merge", agent_id=LANE, state=ACTIVE): Allow(),
        landing(command="ledger.py label --dry-run --pr 36023", agent_id=LANE, state=ACTIVE): Allow(),
        landing(command="echo 'stack-enqueue 36023'", agent_id=LANE, state=ACTIVE): Allow(),
        landing(command="gh pr view 36023 --json mergedAt", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="gh pr merge 36023 -R o/r --squash", agent_id=LANE, env=DRIVE_ENV, state=ACTIVE): Warn(pattern=r"`gh pr merge`"),
        Input(command="gh api repos/O/R/issues/36023/labels -f 'labels[]=merge'", agent_id=LANE, env=DRIVE_ENV, state=ACTIVE): Warn(),
        Input(command="gh pr merge 81 -R yasyf/cc-inbox --squash", agent_id=LANE, env=DRIVE_ENV, state=ACTIVE): Allow(),
        Input(command="ccx vcs merge 81 -R yasyf/cc-inbox", agent_id=LANE, env=DRIVE_ENV, state=ACTIVE): Allow(),
        Input(command="gh pr merge https://github.com/yasyf/cc-skills/pull/403 --squash", agent_id=LANE, env=DRIVE_ENV, state=ACTIVE): Allow(),
        Input(command="gh api repos/yasyf/cc-skills/issues/403/labels -f 'labels[]=merge'", agent_id=LANE, env=DRIVE_ENV, state=ACTIVE): Allow(),
        landing(command="gh pr merge 111 -R yasyf/cc-slack --squash --admin", agent_id=LANE, state=ACTIVE): Allow(),
        landing(command="gh pr merge https://github.com/yasyf/cc-slack/pull/111 --squash --admin", agent_id=LANE, state=ACTIVE): Allow(),
        landing(command="gh stack merge 111 --yes --squash", agent_id=LANE, state=ACTIVE, cwd=SLACK_CHECKOUT): Allow(),
        landing(command="ccx vcs merge 111", agent_id=LANE, state=ACTIVE, cwd=SLACK_CHECKOUT): Allow(),
        landing(command="stack-enqueue 111", agent_id=LANE, state=ACTIVE, cwd=SLACK_CHECKOUT): Allow(),
        landing(command="tools/ci trunk submit 111", agent_id=LANE, state=ACTIVE, cwd=SLACK_CHECKOUT): Allow(),
        landing(command="gh api -X PUT repos/yasyf/cc-slack/pulls/111/merge -f merge_method=squash", agent_id=LANE, state=ACTIVE): Allow(),
        landing(command="gh pr merge 111 -R yasyf/cc-slack --squash --admin", agent_id="aincident-api-500s-0123456789abcdef", state=ACTIVE): Allow(),
        landing(command="gh pr merge 36023 --squash --admin", agent_id=LANE, state=ACTIVE): Warn(pattern=r"lane `lane-a` may not run `gh pr merge`"),
        landing(command="gh stack merge 36023 --yes --squash", agent_id=LANE, state=ACTIVE): Warn(pattern=r"lane `lane-a` may not run `gh stack merge`"),
        landing(command="gh pr merge 36023 --squash", agent_id=LANE, state=ACTIVE, cwd=SLACK_CHECKOUT): Allow(),
        Input(command="gh pr merge 36023 --squash", agent_id=LANE, state=ACTIVE): Warn(pattern=r"`gh pr merge`"),
        landing(command='for r in yasyf/cc-review yasyf/cc-orchestrate; do gh pr merge 5 -R "$r" --squash --admin; done', agent_id=LANE, state=ACTIVE): Allow(),
        landing(command='for r in yasyf/cc-review Forge-AI/monorepo; do gh pr merge 5 --repo="$r" --squash; done', agent_id=LANE, state=ACTIVE): Warn(pattern=r"lane `lane-a` may not run `gh pr merge`"),
        landing(command='gh pr merge "$2" -R "yasyf/$1" --squash --admin', agent_id=LANE, state=ACTIVE): Warn(pattern=r"^Warning, nothing was blocked: This hook could not tell whether `gh pr merge` lands on the landing desk's repo"),
        landing(command='cd "$D" && gh pr merge 81 --squash --admin', agent_id=LANE, state=ACTIVE): Warn(pattern=r"could not tell whether `gh pr merge`"),
        landing(command='gh pr merge "$2" -R "yasyf/$1" --squash', agent_id="aincident-api-500s-0123456789abcdef", state=ACTIVE): Allow(),
    },
)
def lanes_never_enqueue(evt: BaseHookEvent) -> HookResult | None:
    if (lane := lane_name(evt)) is None or LANDERS.match(lane):
        return None
    if evt.agent_id and not reqenv.getenv("CLAUDE_LONG_RUNNING_LANE") and not CompactionState.load(evt).active:
        return None
    entry = drive_entry(evt)
    incident = incident_lane(evt, lane)
    readings = [(call, verb, on_landing_repo(evt, call, entry)) for call in evt.cmd.calls() if (verb := landing_verb(call))]
    if refusal := landing_refusal(lane, incident, [(call, verb) for call, verb, on in readings if on]):
        return evt.context(refusal)
    unread = [(call, verb) for call, verb, on in readings if on is None]
    return evt.context(UNREAD_REPO.format(verb=unread[0][1])) if landing_refusal(lane, incident, unread) else None


def landing_refusal(lane: str, incident: bool, landings: list[tuple[Call, str]]) -> str | None:
    if incident and (admin := next((verb for call, verb in landings if verb in MERGERS and "--admin" in call.args), None)):
        return ADMIN_MERGE_REFUSAL.format(lane=lane, verb=admin)
    verb = next((verb for _, verb in landings if verb in ENQUEUERS or not incident), None)
    if verb and incident:
        return INCIDENT_REFUSAL.format(lane=lane)
    return REFUSAL.format(lane=lane or "unnamed", verb=verb) if verb else None


def gate_args(call: Call) -> list[str]:
    """The `lr landing gate` operands for a `stack-enqueue` call: its PRs with `--partial`, since it lands them and what is below them and never a PR above, or none for a call that enqueues nothing."""
    args = program((call.name, *call.args))[1:]
    prs = [arg for arg in args if arg.isdigit()]
    return [*prs, "--partial"] if prs and not NOT_AN_ENQUEUE & set(args) else []


def landed_prs(call: Call, verb: str, repo: str, cwd: str) -> list[str] | None:
    """The `lr landing gate` operands for one hand landing: the PRs it lands, with `--partial` and `--direct` when it merges outside the queue; None when the PR it lands does not read."""
    if verb == "stack-enqueue":
        return gate_args(call)
    if verb in MERGERS or verb == "gh stack merge":
        found = PULL.fullmatch(merge_target(call)[0])
        merged = (found["pr"], "") if found else merged_pr(call, repo, cwd)
        return None if merged is None else [merged[0], "--partial", "--direct"]
    if verb == API_MERGE:
        return [found["pr"] for arg in call.args if (found := MERGE_ENDPOINT.search(arg))] + ["--partial", "--direct"]
    if verb == "gh api labels":
        prs = [found["pr"] for arg in call.args if (found := LABELS_ENDPOINT.search(arg))]
    else:
        prs = [arg for arg in program((call.name, *call.args))[1:] if arg.isdigit()]
    return [*prs, "--partial"] if prs else None


def gate_failure(done: subprocess.CompletedProcess[str] | None) -> str | None:
    if done is None:
        return "`lr landing gate` did not run or timed out"
    if done.returncode not in (0, 1):
        return f"`lr landing gate` exited {done.returncode}: {done.stderr.strip()}"
    return done.stderr.strip() if done.stderr.startswith(GATE_UNREAD) else None


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash")],
    tests={
        Input(command=ENQUEUE, agent_id=DESK, env=DRIVE_ENV, state=ACTIVE, commands={"lr landing gate": ""}): Allow(),
        Input(command="stack-enqueue 7 --check", env=DRIVE_ENV): Allow(),
        Input(command="stack-enqueue 7 --dequeue", env=DRIVE_ENV): Allow(),
        Input(command=ENQUEUE): Allow(),
        Input(command="gh pr merge 7 -R Forge-AI/monorepo --squash", agent_id=LANE, env=MONOREPO_ENV, commands={f"{MONOREPO_GATE} 7 --partial": UNTAPPED}): Block(
            pattern=r"^A hand landing needs the owner's tap at its head.*: #7 0123456789 TAP-REQUIRED .*\. Ask him for it with a board card at that head, or report the PR"
        ),
        Input(command="gh pr merge 7 -R Forge-AI/monorepo --squash", env=MONOREPO_ENV, commands={f"{MONOREPO_GATE} 7 --partial": UNTAPPED}): Block(),
        Input(command="stack-enqueue 7", agent_id=LANE, env=MONOREPO_ENV, commands={f"{MONOREPO_GATE} 7 --partial": ""}): Allow(),
        Input(command="stack-enqueue 7", agent_id=LANE, env=MONOREPO_ENV, commands={f"{MONOREPO_GATE} 7 --partial": UNTAPPED}): Block(pattern=r"#7 0123456789 TAP-REQUIRED"),
        Input(command="gh api repos/Forge-AI/monorepo/issues/7/labels -f 'labels[]=merge'", agent_id=LANE, env=MONOREPO_ENV, commands={f"{MONOREPO_GATE} 7 --partial": UNTAPPED}): Block(),
        Input(command="gh pr merge 7 -R Forge-AI/monorepo --squash", agent_id=LANE, env=MONOREPO_ENV, commands={f"{MONOREPO_GATE} 7 --partial": ""}): Allow(),
        Input(
            command="gh pr merge https://github.com/Forge-AI/monorepo/pull/7 --squash --match-head-commit fedcba9876543210fedcba9876543210fedcba98",
            agent_id=LANE,
            env=MONOREPO_ENV,
            commands={f"{MONOREPO_GATE} 7 --partial": ""},
        ): Allow(),
        Input(command="gh pr merge 44811 -R Forge-AI/monorepo --squash", agent_id=FIX_LANE, env=MONOREPO_ENV, transcript=sliced_brief("inc-fixruns-fix", "incident"), commands={MONOREPO_GATE: UNTAPPED}): Allow(),
        Input(command="gh pr merge 81 -R yasyf/cc-inbox --squash --admin", agent_id=LANE, env=MONOREPO_ENV, commands={MONOREPO_GATE: UNTAPPED}): Allow(),
        Input(command="gh pr merge 7 -R Forge-AI/monorepo --squash", agent_id="adesk-runner-0123456789abcdef", env=MONOREPO_ENV, commands={MONOREPO_GATE: UNTAPPED}): Allow(),
        Input(command="gh pr merge 7 -R Forge-AI/monorepo --squash", agent_id=LANE, env=MONOREPO_ENV, commands={MONOREPO_GATE: (2, "lr: the store is locked")}): Warn(
            pattern=r"^Warning, nothing was blocked: the owner's tap did not read for this landing, .*: `lr landing gate` exited 2: lr: the store is locked"
        ),
        Input(command="gh pr merge 7 -R Forge-AI/monorepo --squash", commands={MONOREPO_GATE: UNTAPPED}): Allow(),
        Input(command="gh pr merge 7 -R Forge-AI/monorepo --squash", agent_id=LANE, env=MONOREPO_ENV, commands={MONOREPO_DIRECT_GATE: UNTESTED}): Warn(pattern=r"^Warning, nothing was blocked: A merge outside the queue lands untested.*: #7 0123456789 QUEUE-UNTESTED "),
        Input(command="gh stack merge 7 -R Forge-AI/monorepo --squash", agent_id=LANE, env=MONOREPO_ENV, commands={MONOREPO_DIRECT_GATE: UNTESTED}): Warn(pattern=r"QUEUE-UNTESTED"),
        Input(command="gh api -X PUT repos/Forge-AI/monorepo/pulls/7/merge", agent_id=LANE, env=MONOREPO_ENV, commands={MONOREPO_DIRECT_GATE: UNTESTED}): Warn(pattern=r"QUEUE-UNTESTED"),
        Input(command="gh pr merge 7 -R Forge-AI/monorepo --squash", agent_id=LANE, env=MONOREPO_ENV, commands={MONOREPO_DIRECT_GATE: ""}): Allow(),
        Input(command="gh api -X PUT repos/Forge-AI/monorepo/pulls/44811/merge", agent_id=FIX_LANE, env=MONOREPO_ENV, transcript=sliced_brief("inc-fixruns-fix", "incident"), commands={MONOREPO_GATE: UNTESTED}): Allow(),
        Input(command="gh pr merge 44811 -R Forge-AI/monorepo --squash", agent_id=FIX_LANE, env=MONOREPO_ENV, transcript=sliced_brief("inc-fixruns-fix", "incident"), commands={MONOREPO_GATE: UNTESTED}): Allow(),
        Input(command="gh pr merge 7 -R Forge-AI/monorepo --squash", agent_id="adesk-runner-0123456789abcdef", env=MONOREPO_ENV, commands={MONOREPO_GATE: UNTESTED}): Allow(),
    },
)
def hand_landings_meet_the_runner_checks(evt: BaseHookEvent) -> HookResult | None:
    if (entry := drive_entry(evt)) is None:
        return None
    lander = bool(LANDERS.match(lane := lane_name(evt) or ""))
    incident = incident_lane(evt, lane)
    monorepo = entry["repo"].casefold() == MONOREPO
    blocking = monorepo and not lander
    config = str(Path(entry["state_dir"]) / "lr.json")
    untapped: list[str] = []
    untested: list[str] = []
    failing: list[str] = []
    unread: list[str] = []
    for call in evt.cmd.calls():
        if not (verb := landing_verb(call)) or outside_drive_repo(evt, call, entry):
            continue
        if verb != "stack-enqueue" and (lander or not monorepo or incident and (verb in MERGERS or verb == API_MERGE)):
            continue
        if unknown_dir(evt, call):
            unread.append(f"`{verb}` runs after a `cd` this hook cannot follow")
            continue
        cwd = run_dir(evt, call)
        if (args := landed_prs(call, verb, named_repo(call) or entry["repo"], cwd)) is None:
            unread.append(f"`{verb}` names no PR that reads")
            continue
        if not args:
            continue
        done = run_quietly(["lr", "landing", "gate", "--config", config, *args], cwd)
        if failure := gate_failure(done):
            unread.append(failure)
            continue
        lines = [line.strip() for line in done.stdout.splitlines() if line.strip()]
        untapped += [line for line in lines if TAP_REFUSAL in line]
        untested += [line for line in lines if QUEUE_UNTESTED in line]
        failing += [line for line in lines if TAP_REFUSAL not in line and verb == "stack-enqueue"]
    if untapped and blocking:
        return evt.block(HAND_LANDING_REFUSAL.format(lines="; ".join(untapped)))
    if untapped:
        return evt.context(HAND_ENQUEUE_TAP_REFUSAL.format(lines="; ".join(untapped)))
    notes = [TAP_UNREAD.format(failure=failure) for failure in unread] if blocking else []
    notes += [HAND_MERGE_UNTESTED.format(lines="; ".join(untested))] if untested and blocking else []
    notes += [HAND_ENQUEUE_WARNING.format(lines="; ".join(failing))] if failing else []
    return evt.context("\n".join(notes)) if notes else None


def merge_target(call: Call) -> tuple[str, str]:
    """The selector a `gh pr merge`, `gh stack merge` or `ccx vcs merge` names, a number, URL, or branch, empty for the current branch's PR, and the head its `--match-head-commit` pins, or empty."""
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
    """The number and head of the PR a `gh pr merge` or `ccx vcs merge` call merges: a number or URL with a pinned head reads nothing, and any other selector asks `gh pr view`; None when that does not read."""
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
        return run_script(argv, LANDING_READ_SECONDS, cwd)
    except (OSError, subprocess.TimeoutExpired):
        return None


def merge_causes(call: Call, entry: dict, cwd: str, incident: bool, *, evt: BaseHookEvent) -> tuple[str, str, str] | None:
    """The PR, head and joined refusals `rulings.py check-pr` prints for one `gh pr merge` or `ccx vcs merge` call; None when it prints none or anything does not read."""
    repo = named_repo(call) or entry["repo"]
    if (merged := merged_pr(call, repo, cwd)) is None:
        return None
    pr, head = merged
    args = ["check-pr", "--drive", entry["drive"], "--pr", f"{repo}#{pr}", "--head", head, *(["--incident"] if incident else [])]
    try:
        said = rulings(cwd, *args, evt=evt, timeout=CHECK_PR_TIMEOUT_SECONDS)
        done = subprocess.CompletedProcess([sys.executable, str(script("rulings.py")), *args, "--repo", cwd], 0, said, "")
    except subprocess.CalledProcessError as refused:
        done = subprocess.CompletedProcess(refused.cmd, refused.returncode, refused.stdout, refused.stderr)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode not in (0, 1):
        return None
    lines = [line.strip() for line in done.stdout.splitlines() if line.strip()]
    named = [line for line in lines if line.split(" ", 1)[0] in CHECK_PR_CAUSES]
    if any(line.split(" ", 1)[0] not in CHECK_PR_CAUSES | {"BLOCKER"} for line in lines) or (done.returncode == 1) != bool(named):
        return None
    causes = [line for line in named if line.split(" ", 1)[0] in (INCIDENT_CAUSES if incident else CHECK_PR_CAUSES)]
    return (pr, head[:10], "; ".join(causes)) if causes else None


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash")],
    tests={
        Input(command="gh pr merge 36023 --squash"): Allow(),
        Input(command="gh pr merge 81 -R yasyf/cc-inbox --squash", env=DRIVE_ENV): Allow(),
        Input(command="ccx vcs merge 81 -R yasyf/cc-inbox", env=DRIVE_ENV): Allow(),
        Input(command="gh pr view 36023 -R o/r --json mergedAt", env=DRIVE_ENV): Allow(),
    },
)
def rulings_at_merge(evt: BaseHookEvent) -> HookResult | None:
    if (entry := drive_entry(evt)) is None:
        return None
    merges = [call for call in evt.cmd.calls() if landing_verb(call) in MERGERS and not outside_drive_repo(evt, call, entry)]
    if not merges:
        return None
    lane = lane_name(evt) or ""
    incident = incident_lane(evt, lane)
    found = [checked for call in merges if not unknown_dir(evt, call) and (checked := merge_causes(call, entry, run_dir(evt, call), incident, evt=evt))]
    if incident and found:
        return evt.context("\n".join(INCIDENT_RULINGS_REFUSAL.format(pr=pr, head=head, lane=lane, causes=causes) for pr, head, causes in found))
    advice = [RULINGS_ADVICE.format(causes=causes, pr=pr, head=head) for pr, head, causes in found]
    return evt.context("\n".join(advice)) if advice else None


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash")],
    tests={
        Input(command="tools/ci trunk dequeue 36023", agent_id=LANE, state=ACTIVE): Warn(pattern=r"lane `lane-a` may not run `ci trunk dequeue`"),
        Input(command="tools/ci trunk restart 36023", agent_id=DESK, state=ACTIVE): Warn(pattern=r"lane `landing-desk` may not run `ci trunk restart`"),
        Input(command="tools/ci trunk status 36023", agent_id=LANE, state=ACTIVE): Allow(),
        Input(command="tools/ci trunk dequeue 36023", state=ACTIVE): Allow(),
    },
)
def only_the_root_steers_the_queue(evt: BaseHookEvent) -> HookResult | None:
    if (lane := lane_name(evt)) is None:
        return None
    if evt.agent_id and not reqenv.getenv("CLAUDE_LONG_RUNNING_LANE") and not CompactionState.load(evt).active:
        return None
    for call in evt.cmd.calls():
        run = program((call.name, *call.args))
        if len(run) > 2 and Path(run[0]).name == "ci" and run[1] == "trunk" and run[2] in ("dequeue", "restart", "prioritize"):
            return evt.context(f"Only the coordinator steers the merge queue, so lane `{lane or 'unnamed'}` may not run `ci trunk {run[2]}`. Ask the coordinator, and read what a PR waits on with `lr board waits --pr <n>`.")
    return None


def option(args: tuple[str, ...], name: str) -> list[str]:
    return [value for flag, value in zip(args, args[1:], strict=False) if flag == name] + [arg.split("=", 1)[1] for arg in args if arg.startswith(f"{name}=")]


def green_except(call: Call) -> str | None:
    """The phrase in a lane's READY report or a coordinator ruling that grades a red head green: `ledger.py report`'s text, a `cci post` report whose text opens READY, or any `cci post` text of a kind that rules on a head."""
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
    cci = script_module("long_running.cci")

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
    only_if=[Bound(), Tool("Bash")],
    tests={
        Input(command="ledger.py report --pr 43163 --head abc --verdict clean --text 'READY CI green except the known red'", env=DRIVE_ENV): Warn(pattern=r"^Warning, nothing was blocked: `green except` reads as red"),
        Input(command="python3 ledger.py -C ~/m report --pr 43163 --text 'READY: green apart from the known publish job'", env=DRIVE_ENV): Warn(pattern=r"`green apart from`"),
        Input(command="cci post --drive d --lane root --kind decide --text 'dev is design red; land on'", env=DRIVE_ENV): Warn(pattern=r"`design red`"),
        Input(command="cci post --drive d --lane x --kind=report --text='READY #43163: known-red test job only'", env=DRIVE_ENV): Warn(pattern=r"`known-red`"),
        Input(command="cci post --drive d --lane x --kind report 'READY #43163: green except the publish job'", env=DRIVE_ENV): Warn(pattern=r"`green except`"),
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
    return evt.context(GREEN_EXCEPT_REFUSAL.format(phrase=phrase, trunk=trunk_clause(entry))) if phrase else None
