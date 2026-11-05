from __future__ import annotations

import json
import tempfile
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Event, FromSubagent, HookResult, Input, Tool, Warn, WorkflowState, on
from captain_hook.util import reqenv

from .cci_mcp_posts import POST_TOOL
from .compaction_handoff import FOREGROUND_JUDGE_SECONDS, Bound, Communicator, communicator_tests, script_module
from .deadline_plans import posts
from .judge_metrics import judge_call
from .owner_answers import read
from .owner_facing import REPLY_WINDOW, SLACK_CARD, Reads, last_reply
from .owner_replies_carry_news import dm_posts
from .register_gates import fenced
from .register_injection import drive_args
from .ruling_judge import CANDIDATE_BYTES, EVIDENCE
from .tests.handoff_fixtures import reply_transcript
from .tests.register_fixtures import DRIVE_CONFIG
from .urgent_lines_name_the_harm import ACTIVE, DM_TOOLS, owner_lines

RULED = "Warning, nothing was blocked: he has ruled on what this line says. Where his answer below settles it, drop the line and stop that work."
MATCHED_RULINGS = "3"
TEXT_CAP = 6000
MEANING_CAP = 300
JUDGE = script_module("long_running/judge")
LINE = "a line a lane is about to post"
JUDGED_SCRATCH = str(Path(tempfile.gettempdir()) / "long-running-lines-name-a-ruling")
JUDGED_ENV = {"CLAUDE_CONFIG_DIR": JUDGED_SCRATCH, "LONG_RUNNING_SCRATCH_ROOT": JUDGED_SCRATCH, "CLAUDE_LONG_RUNNING_DRIVE": "line-rulings"}
FASTMODE = {"id": "ef96341aa", "title": "cc-patch's fastmode pack is repaired but not installed on this machine. Install it when the release lands?", "body": "Leave it uninstalled"}
UNRELATED = "cci post --kind landed --text 'merged, not installed: the pack reads the release once it lands'"
FORBIDDEN = "cci post --kind report --text 'I am installing the cc-patch fastmode pack on this Mac now that the release landed'"


def judged(verdict: str) -> dict[str, str]:
    answer = {"verdict": verdict, "artifact_phrase": "", "evidence_phrase": "", "sentence": "He said to leave that pack uninstalled."}
    p_yes = 0.9 if verdict == "yes" else 0.1
    return {
        "ccn -R": json.dumps([FASTMODE]),
        "uv run --no-project --quiet": json.dumps({"p_yes": p_yes, "tokens": 2}),
        "claude -p": json.dumps({"structured_output": answer, "total_cost_usd": 0.01, "usage": {}}),
    }


class RulingsShown(WorkflowState):
    ids: list[str] = []


def bears_on(evt: BaseHookEvent, reads: Reads, answer: dict, text: str) -> bool:
    if answer.get("phrase"):
        return True
    env = reqenv.env_map()
    block = f"- {answer['id'][:7]} {answer['title']}\n  > {answer['body'].strip()[:CANDIDATE_BYTES]}"
    verdict = judge_call(
        JUDGE,
        "answer",
        EVIDENCE.format(block=block),
        f"{LINE}:\n{text[:TEXT_CAP]}",
        backend=JUDGE.backend_from(env),
        drive=reads.which[1] if reads.which else None,
        timeout=reqenv.clamp_timeout(FOREGROUND_JUDGE_SECONDS),
        env=env,
    )
    return verdict is not None and verdict.flagged


def warned(evt: BaseHookEvent, reads: Reads, text: str) -> HookResult | None:
    def load() -> list[dict]:
        listed = read(["ccn", "-R", reads.repo, "answer", "match", "--json", "--label", "scope:durable", "--limit", MATCHED_RULINGS, "--", text[:TEXT_CAP]])
        return json.loads(listed) if listed else []

    answers = (reads.read("ccn answer match", load) or []) if text.strip() else []
    shown = RulingsShown.load(evt).ids
    best = next((answer for answer in answers if answer["id"] not in shown), None)
    if best is None or not bears_on(evt, reads, best, text):
        return None
    with RulingsShown.mutate(evt) as state:
        state.ids = [*state.ids, best["id"]]
    return evt.context("\n".join([RULED, fenced(f"- {best['id'][:7]} {best['title']} → {best['body'].strip()[:MEANING_CAP]}")]))


@on(
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", SLACK_CARD, POST_TOOL, *DM_TOOLS)],
    tests={
        Input(command="ls", env=DRIVE_CONFIG): Allow(),
        Input(command="cci post --kind report --text 'the api answers 500'", env=DRIVE_CONFIG, commands={"ccn -R": "[]"}): Allow(),
        Input(command=UNRELATED, env=JUDGED_ENV, commands=judged("no")): Allow(),
        Input(command=FORBIDDEN, env=JUDGED_ENV, commands=judged("yes")): Warn(pattern=rf"^{RULED}[\s\S]*ef96341 cc-patch's fastmode[\s\S]*Leave it uninstalled"),
    },
)
def lines_name_a_ruling(evt: BaseHookEvent) -> HookResult | None:
    if not (which := drive_args(evt)):
        return None
    reads = Reads(evt, which)
    posted = [text for text, *_ in posts(evt)]
    return warned(evt, reads, "\n".join([*posted, *owner_lines(evt, reads), *(text for _, text in dm_posts(evt))]))


@on(
    Event.Stop,
    only_if=[Communicator()],
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    tests=communicator_tests({
        Input(transcript=reply_transcript("The api answers 500."), state=ACTIVE, env=DRIVE_CONFIG, commands={"ccn -R": "[]"}): Allow(),
    }),
)
def replies_name_a_ruling(evt: BaseHookEvent) -> HookResult | None:
    return warned(evt, Reads(evt, drive_args(evt) or []), last_reply(evt))
