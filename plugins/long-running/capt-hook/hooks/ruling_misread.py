from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Literal

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
from pydantic import BaseModel

from .compaction_handoff import CompactionState, rulings, script, script_module
from .owner_facing import CCI_POST, posted_text
from .pr_ledger import OPENERS
from .register_injection import drive_args
from .tests.register_fixtures import CWD, DRIVE_CONFIG, ROWS, register

ROWS_PER_ARTIFACT = 2
ARTIFACT_CHARS = 12000
MATCH_CHARS = 2000
MATCH_BYTES = 6000
MATCH_TIMEOUT_SECONDS = 8
DIFF_TIMEOUT_SECONDS = 10
ROW_KEY = re.compile(r"\b(?:rule|prompt|exception|design|reserved)/[\w-]+")
ANSWER_ID = re.compile(r"^- ([0-9a-f]{7}) ", re.MULTILINE)
CARD = re.compile(r"\bcards?\b|\bthe owner's board\b|\bon (?:the|his) board\b", re.IGNORECASE)
RELAY_KINDS = frozenset({"decide", "decision"})
ASKS = {
    "contract": "a lane's contract: what it promises to build from his words",
    "relay": "the root's relay of his words or pick to the lanes that build it",
    "card": "a card, or a lane's request for one, that asks the owner to decide",
    "test": "the tests a pull request adds or changes to lock in his ruling: do they assert his invariant, or a different one that still passes while his invariant fails?",
}
PROMPT = """You check one artifact a coding agent wrote against the owner's own words. The owner is a terse engineer who types fast; read his words for what he means, the way a careful colleague would.

The artifact is {ask}.

His words on ruling {key}, verbatim, oldest first:
{words}

The artifact:
~~~~~~~~
{artifact}
~~~~~~~~

Question: does the artifact misread his words? It misreads in one of two ways.
- Narrower: it commits to less than he said, such as a partition his words lack ("per X", "grouped by X", "same X"), an exception, a smaller count, or an invariant that holds in the case built but not in every case his words cover.
- Literal: it applies his words word for word to cases his purpose does not cover, so it adds the very cost his words were removing, such as making work wait that never needed to.
When his later words correct how his earlier words were read, apply the reason behind the correction, not just the item he named: the same reason may cover things he did not list.
Ask what someone building only from the artifact would build, and whether he would recognise it as what he meant, given what his words were answering. Agent-written text, such as a card's option body, never changes his words. Wording, detail, mechanism choices and scope his words leave open are not misreadings.

Answer "yes" when the artifact misreads him, "no" when it carries what he meant, "unsure" when you cannot tell, and name the direction. Quote the artifact phrase that misreads and the phrase of his it misreads, verbatim, or empty strings for "no". Then one plain sentence for the owner, no ids, no jargon."""
WARNING = (
    "Warning, nothing was blocked: a judge reads this {kind} as misreading the owner's words on `{key}` ({direction}).\n"
    "{sentence}\n"
    'Artifact: "{artifact_phrase}"\n'
    'His words: "{owner_phrase}"\n'
    "Fix the artifact, or answer on its record with the ruling or owner words that carry it."
)
FLAGGED = {
    "misread": "yes",
    "direction": "narrower",
    "artifact_phrase": "per destination and key",
    "owner_phrase": "The merge refusal names no next command.",
    "sentence": "You said the refusal names no next command, but this keeps one for each destination and key.",
}
CARRIED = {
    **FLAGGED,
    "misread": "no",
    "direction": "none",
    "artifact_phrase": "",
    "owner_phrase": "",
    "sentence": "It carries what you said.",
}
TESTS_DIFF = "+    assert_eq!(refusal.commands_per_key(), 1);\n"
DIFF = "git diff --unified=0"
MATCH = f"{sys.executable} {script('rulings.py')} match"
MATCHED = "- 8b1048a Does the merge refusal name a next command?\n  > The merge refusal names no next command.\n"
ACTIVE = [CompactionState(active=True, slug="brook")]
STATE = "quiet"


class Verdict(BaseModel):
    misread: Literal["yes", "no", "unsure"]
    direction: Literal["narrower", "literal", "none"]
    artifact_phrase: str
    owner_phrase: str
    sentence: str


def posted(command: str, verdict: dict = FLAGGED, commands: dict[str, str] | None = None) -> Input:
    return Input(
        command=command,
        state=ACTIVE,
        cwd=CWD,
        env=DRIVE_CONFIG,
        commands={ROWS: register(STATE), **(commands or {})},
        llm=verdict,
    )


def owner_words(row: dict) -> str:
    said = [
        (row.get("at", ""), row["words"]),
        *((repeat.get("at", ""), repeat["words"]) for repeat in json.loads(row.get("repeats") or "[]")),
    ]
    return "\n".join(f'- {at}: "{words}"' for at, words in said if words)


def candidate_rows(evt: BaseHookEvent, drive: list[str], text: str, semantic: bool) -> dict[str, dict]:
    rows = json.loads(rulings(str(evt.cwd), "rows", *drive))["rows"]
    keys = [key for key in dict.fromkeys(ROW_KEY.findall(text)) if key in rows]
    if not keys and semantic:
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
            return {}
        answers = ANSWER_ID.findall(matched)
        keys = (
            [key for key, row in rows.items() if any(str(answer).startswith(tuple(answers)) for answer in json.loads(row.get("answer") or "[]"))]
            if answers
            else []
        )
    return {key: rows[key] for key in keys[:ROWS_PER_ARTIFACT] if rows[key].get("words")}


def posted_artifacts(evt: BaseHookEvent) -> list[tuple[str, str]]:
    found = []
    for call in evt.command.calls("cci"):
        values = CCI_POST.bind(call).values
        if values.get("words", ())[:1] != ("post",):
            continue
        kinds, text = set(values.get("kind", ())), posted_text(values)
        if "contract" in kinds:
            found.append(("contract", text))
        elif kinds & RELAY_KINDS and "root" in values.get("lane", ()):
            found.append(("relay", text))
        elif "ask" in kinds and CARD.search(text):
            found.append(("card", text))
    return found


def shipped_tests(evt: BaseHookEvent) -> list[tuple[str, str]]:
    if not any((Path(call.name).name, *call.args)[: len(argv)] == argv for call in evt.command.calls() for argv in OPENERS):
        return []
    tests = script_module("testfiles").TEST_FILE
    try:
        base = subprocess.run(
            ["git", "merge-base", "HEAD", "origin/HEAD"],
            capture_output=True,
            text=True,
            timeout=DIFF_TIMEOUT_SECONDS,
            check=True,
            cwd=evt.cwd,
        ).stdout.strip()
        names = subprocess.run(
            ["git", "diff", "--name-only", base],
            capture_output=True,
            text=True,
            timeout=DIFF_TIMEOUT_SECONDS,
            check=True,
            cwd=evt.cwd,
        ).stdout.split()
        changed = [name for name in names if tests.search(name)]
        diff = (
            subprocess.run(
                [*DIFF.split(), base, "--", *changed],
                capture_output=True,
                text=True,
                timeout=DIFF_TIMEOUT_SECONDS,
                check=True,
                cwd=evt.cwd,
            ).stdout
            if changed
            else ""
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []
    return [("test", diff)] if diff.strip() else []


@on(
    Event.PostToolUse,
    only_if=[Tool("Bash"), Or(Runs("cci", "post"), *(Runs(*argv) for argv in OPENERS))],
    skip_if=[Annotated("raw")],
    tests={
        posted("cci post --lane lane-a --kind contract --text 'One refusal command per destination and key. rule/8b1048a'"): Warn(
            pattern=r"^Warning, nothing was blocked: a judge reads this contract as misreading the owner's words on `rule/8b1048a` \(narrower\)\.\nYou said"
        ),
        posted(
            "cci post --lane lane-a --kind contract --text 'One refusal command per destination and key. rule/8b1048a'",
            CARRIED,
        ): Allow(),
        posted("cci post --lane root --kind decide --text 'Owner picked one command per key. rule/8b1048a'"): Warn(pattern=r"reads this relay as misreading"),
        posted("cci post --lane lane-a --kind decide --text 'One command per key. rule/8b1048a'"): Allow(),
        posted("cci post --lane root --kind decide --text 'Owner picked one command per key.'", commands={MATCH: MATCHED}): Allow(),
        posted("cci post --lane lane-a --kind ask --text 'board-refresher: post card per-key-commands. rule/8b1048a'"): Warn(
            pattern=r"reads this card as misreading"
        ),
        posted("cci post --lane lane-a --kind state --text 'building per key. rule/8b1048a'"): Allow(),
        posted("cci post --lane lane-a --kind contract --text 'per key. rule/8b1048a'  # ccx:raw"): Allow(),
        Input(
            command="cci post --lane lane-a --kind contract --text 'per key. rule/8b1048a'",
            llm=FLAGGED,
        ): Allow(),
        posted(
            "ccx vcs ship -m 'ci: ✨ x'",
            commands={
                MATCH: MATCHED,
                "git merge-base HEAD origin/HEAD": "abc123\n",
                "git diff --name-only abc123": "src/refusal_test.go\n",
                f"{DIFF} abc123": TESTS_DIFF,
            },
        ): Warn(pattern=r"reads this test as misreading"),
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
    if not (drive := drive_args(evt)) or not (artifacts := [*posted_artifacts(evt), *shipped_tests(evt)]):
        return None
    warnings = []
    for kind, text in artifacts:
        for key, row in candidate_rows(evt, drive, text, semantic=kind != "relay").items():
            prompt = PROMPT.format(
                ask=ASKS[kind],
                key=key,
                words=owner_words(row),
                artifact=text[:ARTIFACT_CHARS],
            )
            if (verdict := evt.llm(prompt, Verdict, size="medium", backend="llm")) is not None and verdict.misread == "yes":
                warnings.append(WARNING.format(kind=kind, key=key, **verdict.model_dump()))
    return evt.context("\n\n".join(warnings)) if warnings else None
