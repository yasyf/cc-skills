from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from captain_hook import (
    Allow,
    Annotated,
    BaseHookEvent,
    Event,
    HookResult,
    Input,
    Or,
    Runs,
    Tool,
    Warn,
    on,
)
from captain_hook.util import reqenv

from .compaction_handoff import Bound, CompactionState, rulings, script, script_module
from .pr_ledger import OPENERS, OpenedPr, opened_prs, opener_cwd, response_text
from .register_gates import fenced
from .register_injection import drive_args
from .tests.register_fixtures import CWD, DRIVE_CONFIG
from .tests.rulings_consumers import separate

WARNING = (
    "Warning, nothing was blocked: A judge reads the tests this ship adds as misreading the owner's words on {keys}."
    " Fix them, or say in the PR body which owner words they carry."
)
FINDING = '{kind}, {key}: {sentence}\nArtifact: "{artifact_phrase}"\nHis words: "{evidence_phrase}"'
FLAGGED = {
    "verdict": "yes",
    "artifact_phrase": "per destination and key",
    "evidence_phrase": "The merge refusal names no next command.",
    "sentence": "You said the refusal names no next command, but this keeps one for each destination and key, which reads you narrower.",
}
CARRIED = {**FLAGGED, "verdict": "no", "artifact_phrase": "", "evidence_phrase": "", "sentence": "It carries what you said."}
CLAUDE = "claude -p"
TESTS_DIFF = "+    assert_eq!(refusal.commands_per_key(), 1);\n"
DIFF = "git diff --unified=0"
SLICE = f"{sys.executable} {script('rulings.py')} slice --drive brook-drive --required --words --pr acme/app#12 --head cafef00d"
NO_ROWS = "Required rows for acme/app#12 at cafef00d\n\n## Every global, pending and touched row (0)\n\n## Rows the PR cites by id (0)\nslice-hash: 0123456789ab\n"
SLICED = (
    "Required rows for acme/app#12 at cafef00d\n\n## Every global, pending and touched row (2)\n\n"
    "### rule/0a1b2c3 | scope | in-force | said 1 | Oct 22 9:00 AM PT\n- Oct 22 9:00 AM PT: \"No laptop writes a job the server started, for any reason.\"\n\n"
    "### rule/8b1048a | scope | in-force | said 2 | Oct 23 3:19 PM PT\n- Oct 21 1:02 PM PT: \"The merge refusal names no next command.\"\n"
    "- Oct 23 3:19 PM PT: \"the refusal still names no next command\"\nThis row supersedes rule/7a6b5c4, which no longer binds.\n\n"
    "## Rows the PR cites by id (0)\nslice-hash: 0123456789ab\n"
)
SHIPPED = "opened PR #12 https://github.com/acme/app/pull/12\npublished cafef00d\n"
ACTIVE = [CompactionState(active=True, coordinator=True, slug="brook")]


def answer(verdict: dict) -> str:
    return json.dumps({"structured_output": verdict, "total_cost_usd": 0.01, "usage": {"input_tokens": 1000, "output_tokens": 50}})


def posted(command: str, verdict: dict = FLAGGED, commands: dict[str, str] | None = None, sliced: str = SLICED, output: str = SHIPPED) -> Input:
    return Input(
        command=command,
        output=output,
        state=ACTIVE,
        cwd=CWD,
        env=DRIVE_CONFIG,
        commands={SLICE: sliced, CLAUDE: answer(verdict), **(commands or {})},
    )


def pr_rows(evt: BaseHookEvent, drive: list[str], pr: OpenedPr) -> str:
    spec, where = f"{pr.repo}#{pr.number}" if pr.repo else pr.number, opener_cwd(evt)
    head = pr.head or subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, cwd=where).stdout.strip()
    try:
        return rulings(str(where), "slice", *drive, "--required", "--words", "--pr", spec, "--head", head, evt=evt)
    except subprocess.TimeoutExpired:
        return ""


def shipped_tests(evt: BaseHookEvent) -> list[tuple[str, str]]:
    if not any((Path(call.name).name, *call.args)[: len(argv)] == argv for call in evt.command.calls() for argv in OPENERS):
        return []
    if (where := opener_cwd(evt)) is None:
        return []
    tests = script_module("long_running.testfiles").TEST_FILE
    try:
        base = subprocess.run(
            ["git", "merge-base", "HEAD", "origin/HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=where,
        ).stdout.strip()
        names = subprocess.run(
            ["git", "diff", "--name-only", base],
            capture_output=True,
            text=True,
            check=True,
            cwd=where,
        ).stdout.split()
        changed = [name for name in names if tests.search(name)]
        diff = (
            subprocess.run(
                [*DIFF.split(), base, "--", *changed],
                capture_output=True,
                text=True,
                check=True,
                cwd=where,
            ).stdout
            if changed
            else ""
        )
    except subprocess.CalledProcessError:
        return []
    return [("test", diff)] if diff.strip() else []


@on(
    Event.PostToolUse,
    only_if=[Bound(), Tool("Bash"), Or(*(Runs(*argv) for argv in OPENERS))],
    skip_if=[Annotated("raw")],
    tests=separate("ruling_misread.misread_rulings", {
        Input(
            command="ccx vcs ship -m 'ci: ✨ x'",
            output=SHIPPED,
            commands={CLAUDE: answer(FLAGGED), "git merge-base HEAD origin/HEAD": "abc123\n", "git diff --name-only abc123": "src/refusal_test.go\n", f"{DIFF} abc123": TESTS_DIFF},
        ): Allow(),
        posted(
            "ccx vcs ship -m 'ci: ✨ x'",
            CARRIED,
            commands={"git merge-base HEAD origin/HEAD": "abc123\n", "git diff --name-only abc123": "src/refusal_test.go\n", f"{DIFF} abc123": TESTS_DIFF},
        ): Allow(),
        posted(
            "ccx vcs ship -m 'ci: ✨ x'",
            commands={"git merge-base HEAD origin/HEAD": "abc123\n", "git diff --name-only abc123": "src/refusal_test.go\n", f"{DIFF} abc123": TESTS_DIFF},
        ): Warn(pattern=r"`rule/8b1048a`\.[\s\S]*test, rule/8b1048a: You said"),
        posted(
            "ccx vcs ship -m 'ci: ✨ x'",
            commands={"git merge-base HEAD origin/HEAD": "abc123\n", "git diff --name-only abc123": "src/refusal_test.go\n", f"{DIFF} abc123": TESTS_DIFF},
            sliced=NO_ROWS,
        ): Allow(),
        posted(
            "ccx vcs ship -m 'ci: ✨ x'",
            commands={"git merge-base HEAD origin/HEAD": "abc123\n", "git diff --name-only abc123": "src/refusal.go\n"},
        ): Allow(),
        posted("ccx vcs ship -m 'ci: ✨ x' --no-push", output='committed 1caa098d30 "ci: ✨ x"'): Allow(),
        posted(
            "cd /other/lane && ccx vcs ship -m 'ci: ✨ x'",
            commands={"git merge-base HEAD origin/HEAD": "abc123\n", "git diff --name-only abc123": "src/refusal_test.go\n", f"{DIFF} abc123": TESTS_DIFF},
        ): Warn(pattern=r"test, rule/8b1048a: You said"),
        posted(
            ". /env.sh; cd $A; ccx vcs ship -m 'ci: ✨ x'",
            commands={"git merge-base HEAD origin/HEAD": "abc123\n", "git diff --name-only abc123": "src/refusal_test.go\n", f"{DIFF} abc123": TESTS_DIFF},
        ): Allow(),
    }),
)
def misread_rulings(evt: BaseHookEvent) -> HookResult | None:
    if not (drive := drive_args(evt)) or not (prs := opened_prs(response_text(evt.tool_response))) or not (artifacts := shipped_tests(evt)):
        return None
    judge, env = script_module("long_running/judge"), reqenv.env_map()
    findings = {}
    for pr in prs:
        if not judge.ROW_HEADING.search(evidence := pr_rows(evt, drive, pr)):
            continue
        for kind, text in artifacts:
            verdict = judge.judge(
                kind,
                evidence,
                text,
                backend=judge.backend_from(env),
                drive=drive[1],
                timeout=reqenv.clamp_timeout(judge.JUDGE_SECONDS),
                env=env,
            )
            if verdict is not None and verdict.flagged:
                key = judge.quoted_row(evidence, verdict.evidence_phrase)
                findings[key] = FINDING.format(kind=kind, key=key, **asdict(verdict))
    if not findings:
        return None
    keys = ", ".join(f"`{key}`" for key in findings)
    return evt.context("\n".join([WARNING.format(keys=keys), fenced("\n\n".join(findings.values()))]))
