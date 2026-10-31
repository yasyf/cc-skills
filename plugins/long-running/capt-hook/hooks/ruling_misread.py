from __future__ import annotations

import json
import re
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

from .compaction_handoff import CompactionState, rulings, script, script_module
from .pr_ledger import OPENERS
from .register_gates import fenced
from .register_injection import drive_args
from .ruling_judge import MATCH_TIMEOUT_SECONDS
from .tests.register_fixtures import CWD, DRIVE_CONFIG, ROWS, register

ROWS_PER_ARTIFACT = 2
MATCH_CHARS = 2000
MATCH_BYTES = 6000
ROW_KEY = re.compile(r"\b(?:rule|prompt|exception|design|reserved)/[\w-]+")
ANSWER_ID = re.compile(r"^- ([0-9a-f]{7}) ", re.MULTILINE)
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
MATCH = f"{sys.executable} {script('rulings.py')} match"
MATCHED = "- 8b1048a Does the merge refusal name a next command?\n  > The merge refusal names no next command.\n"
ACTIVE = [CompactionState(active=True, slug="brook")]
STATE = "quiet"


def answer(verdict: dict) -> str:
    return json.dumps({"structured_output": verdict, "total_cost_usd": 0.01, "usage": {"input_tokens": 1000, "output_tokens": 50}})


def posted(command: str, verdict: dict = FLAGGED, commands: dict[str, str] | None = None) -> Input:
    return Input(
        command=command,
        state=ACTIVE,
        cwd=CWD,
        env=DRIVE_CONFIG,
        commands={ROWS: register(STATE), CLAUDE: answer(verdict), **(commands or {})},
    )


def candidate_rows(evt: BaseHookEvent, drive: list[str], text: str) -> tuple[Path, dict[str, dict]]:
    registered = json.loads(rulings(str(evt.cwd), "rows", *drive))
    state, rows = Path(registered["state_dir"]), registered["rows"]
    keys = [key for key in dict.fromkeys(ROW_KEY.findall(text)) if key in rows]
    if not keys:
        try:
            matched = rulings(
                str(evt.cwd),
                "match",
                *drive,
                "-k",
                str(ROWS_PER_ARTIFACT),
                "--budget",
                str(MATCH_BYTES),
                stdin=text[:MATCH_CHARS],
                timeout=reqenv.clamp_timeout(MATCH_TIMEOUT_SECONDS),
            )
        except subprocess.TimeoutExpired:
            return state, {}
        answers = ANSWER_ID.findall(matched)
        keys = (
            [key for key, row in rows.items() if any(str(answer).startswith(tuple(answers)) for answer in json.loads(row.get("answer") or "[]"))]
            if answers
            else []
        )
    return state, {key: rows[key] for key in keys[:ROWS_PER_ARTIFACT] if rows[key].get("words")}


def shipped_tests(evt: BaseHookEvent) -> list[tuple[str, str]]:
    if not any((Path(call.name).name, *call.args)[: len(argv)] == argv for call in evt.command.calls() for argv in OPENERS):
        return []
    tests = script_module("long_running.testfiles").TEST_FILE
    try:
        base = subprocess.run(
            ["git", "merge-base", "HEAD", "origin/HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=evt.cwd,
        ).stdout.strip()
        names = subprocess.run(
            ["git", "diff", "--name-only", base],
            capture_output=True,
            text=True,
            check=True,
            cwd=evt.cwd,
        ).stdout.split()
        changed = [name for name in names if tests.search(name)]
        diff = (
            subprocess.run(
                [*DIFF.split(), base, "--", *changed],
                capture_output=True,
                text=True,
                check=True,
                cwd=evt.cwd,
            ).stdout
            if changed
            else ""
        )
    except subprocess.CalledProcessError:
        return []
    return [("test", diff)] if diff.strip() else []


@on(
    Event.PostToolUse,
    only_if=[Tool("Bash"), Or(*(Runs(*argv) for argv in OPENERS))],
    skip_if=[Annotated("raw")],
    tests={
        Input(
            command="ccx vcs ship -m 'ci: ✨ x'",
            commands={CLAUDE: answer(FLAGGED), "git merge-base HEAD origin/HEAD": "abc123\n", "git diff --name-only abc123": "src/refusal_test.go\n", f"{DIFF} abc123": TESTS_DIFF},
        ): Allow(),
        posted(
            "ccx vcs ship -m 'ci: ✨ x'",
            CARRIED,
            commands={
                MATCH: MATCHED,
                "git merge-base HEAD origin/HEAD": "abc123\n",
                "git diff --name-only abc123": "src/refusal_test.go\n",
                f"{DIFF} abc123": TESTS_DIFF,
            },
        ): Allow(),
        posted(
            "ccx vcs ship -m 'ci: ✨ x'",
            commands={
                MATCH: MATCHED,
                "git merge-base HEAD origin/HEAD": "abc123\n",
                "git diff --name-only abc123": "src/refusal_test.go\n",
                f"{DIFF} abc123": TESTS_DIFF,
            },
        ): Warn(pattern=r"test, rule/8b1048a: You said"),
        posted(
            "ccx vcs ship -m 'ci: ✨ x'",
            commands={
                "git merge-base HEAD origin/HEAD": "abc123\n",
                "git diff --name-only abc123": "src/refusal.go\n",
            },
        ): Allow(),
    },
)
def misread_rulings(evt: BaseHookEvent) -> HookResult | None:
    if not (drive := drive_args(evt)) or not (artifacts := shipped_tests(evt)):
        return None
    judge, env = script_module("long_running/judge"), reqenv.env_map()
    findings = []
    for kind, text in artifacts:
        state, rows = candidate_rows(evt, drive, text)
        for key, row in rows.items():
            verdict = judge.judge(
                kind,
                judge.owner_words(key, judge.said(row, state, env)),
                text,
                backend=judge.backend_from(env),
                drive=drive[1],
                timeout=reqenv.clamp_timeout(judge.JUDGE_SECONDS),
                env=env,
            )
            if verdict is not None and verdict.flagged:
                findings.append((key, FINDING.format(kind=kind, key=key, **asdict(verdict))))
    if not findings:
        return None
    keys = ", ".join(f"`{key}`" for key in dict.fromkeys(key for key, _ in findings))
    return evt.context("\n".join([WARNING.format(keys=keys), fenced("\n\n".join(text for _, text in findings))]))
