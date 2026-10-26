from __future__ import annotations

import re
from pathlib import Path

from captain_hook import (
    Allow,
    Annotated,
    BaseHookEvent,
    Block,
    Event,
    FromSubagent,
    HookResult,
    Input,
    Tool,
    on,
)

from .compaction_handoff import CompactionState
from .lane_rotation import DriveActive
from .owner_facing import CCI_POST, posted_text
from .tests.register_fixtures import CWD, DRIVE_CONFIG, FIXTURES

INVENTED = "The plan does not name `{mechanism}`, and this root DECIDE adds it without citing an owner row. Put it on the owner's board as a card, or cite the row that names it; `# ccx:raw` posts it as written."
SENTENCE = re.compile(r"(?<=[.;!?])\s+|\n+")
INTRODUCING = re.compile(r"\b(?:adds?|new|introduces?|creates?|gains|joins|carries|records|columns?|tables?|routes?|fields?|states?|timers?|metrics?)\b", re.IGNORECASE)
OWNER_CITE = re.compile(r"\b(?:rule|prompt|reserved|exception|design)/[\w-]+|\bplan(?: lines?)? \d+|\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b")
MARKED = re.compile(r"`([^`\n]+)`|[\"“]([^\"”\n]+)[\"”]")
IDENTIFIER = re.compile(r"(?<![\w./-])[a-z][a-z0-9]*(?:[._/][a-z0-9]+)+(?![\w/-])", re.IGNORECASE)
AFTER_VERB = re.compile(
    r"\b(?:adds?|new|introduces?|creates?|gains|joins|carries|records)\s+(?:(?:a|an|the|its|their|one|every)\s+)?([\w./-]*\w)",
    re.IGNORECASE,
)
PLAN = FIXTURES / "decide-plan.md"
PLANNED = [CompactionState(active=True, slug="brook", plan_path=str(PLAN))]
PROVENANCE = "#117984 A: the rest of a run's provenance joins progress.run_id in the unlanded format step 0008, written at mint on the row vm_run already writes"
PARK = "#126206: park = B. The park records its time on the host row"


def decide(text: str, lane: str = "root") -> Input:
    return Input(command=f'cci post --lane {lane} --kind decide --to walker --text "{text}"', state=PLANNED, cwd=CWD, env=DRIVE_CONFIG)


def mechanisms(sentence: str) -> list[str]:
    marked = [next(group for group in match.groups() if group) for match in MARKED.finditer(sentence)]
    return list(dict.fromkeys([*marked, *IDENTIFIER.findall(sentence), *AFTER_VERB.findall(sentence)]))


def invented(text: str, plan: str) -> str | None:
    for sentence in SENTENCE.split(text):
        if INTRODUCING.search(sentence) and not OWNER_CITE.search(sentence) and (unnamed := next((name for name in mechanisms(sentence) if name.lower() not in plan), None)):
            return unnamed
    return None


def root_decides(evt: BaseHookEvent) -> list[str]:
    return [
        posted_text(values)
        for call in evt.command.calls("cci")
        if (values := CCI_POST.bind(call).values).get("words", ())[:1] == ("post",)
        and "decide" in values.get("kind", ())
        and "root" in values.get("lane", ())
    ]


@on(
    Event.PreToolUse,
    only_if=[DriveActive(), Tool("Bash")],
    skip_if=[FromSubagent(), Annotated("raw")],
    tests={
        decide(PROVENANCE): Block(pattern=r"^The plan does not name `progress\.run_id`"),
        decide(PARK): Block(pattern=r"^The plan does not name `time`"),
        decide("root DECIDE: parking deleted per plan 364"): Allow(),
        decide("root re #129977: binding. Under plan 364 the launch conversion records its checkpoint as the ninth operation."): Allow(),
        decide("Under rule/4d15d92 the app adds a one-hour credential field to the journal launch."): Allow(),
        decide("The conversion records its checkpoint once."): Allow(),
        decide(PROVENANCE, lane="walker"): Allow(),
        Input(command=f'cci post --lane root --kind decide --text "{PROVENANCE}"  # ccx:raw', state=PLANNED, cwd=CWD, env=DRIVE_CONFIG): Allow(),
        Input(command=f'cci post --lane root --kind state --text "{PROVENANCE}"', state=PLANNED, cwd=CWD, env=DRIVE_CONFIG): Allow(),
    },
)
def root_decide_gate(evt: BaseHookEvent) -> HookResult | None:
    if not (texts := root_decides(evt)) or not (plan_path := CompactionState.load(evt).plan_path):
        return None
    plan = Path(plan_path).expanduser()
    if not plan.is_file():
        return None
    words = plan.read_text().lower()
    unnamed = next((found for text in texts if (found := invented(text, words))), None)
    return evt.block(INVENTED.format(mechanism=unnamed)) if unnamed else None
