from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterator
from datetime import datetime
from itertools import dropwhile, takewhile
from pathlib import Path

from cc_transcript import AttachmentEvent, UserEvent
from captain_hook import Allow, BaseHookEvent, Event, FromSubagent, HookResult, Input, WorkflowState, on, workflow_state

from .compaction_handoff import CCN_TIMEOUT_SECONDS, CompactionState, rulings
from .owner_facing import REPLY_WINDOW, last_reply
from .register_gates import READ_ERRORS, failed_open, folded, read_register
from .register_injection import drive_args
from .root_context import PACIFIC, DriveActive, OwnerPrompt, message_label
from .tests.register_fixtures import ACTIVE, CWD, ROWS, readings

PROMPTS = "owner-prompts.jsonl"
STAMP = "%Y-%m-%d %-I:%M %p PT"
TRIGGERS = (
    ("what", "is"),
    ("whats",),
    ("why", "does", "it", "need", "this"),
    ("do", "we", "even", "need"),
    ("i", "dont", "understand"),
    ("i", "do", "not", "understand"),
)
TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*|[?.!,;:\n]")
ENDS = frozenset({"?", ".", "!", ",", ";", ":", "\n", "and", "or", "but", "wtf"})
LEADING = frozenset({"a", "an", "the", "this", "that", "these", "those"})
PRONOUNS = frozenset({"this", "that", "it", "these", "those", "all", "everything"})
TERM_WORDS = 6
QUOTED = re.compile(r'^>\s?(?P<line>.+)$|["“](?P<inner>[^"”\n]{12,})["”]', re.MULTILINE)
SENTENCE = re.compile(r"(?<=[.?!])\s+|\n+")
NO_BOARD = "no cc-present artifact"
MARKED = (
    'The owner asked "{text}". Blocks marked revising: {blocks}. Until its reply is posted, a reply or writer brief quotes'
    " his words, the sentence under question and the rows bearing on it ({rows}), and says from those rulings whether the"
    " thing exists at all before any code read."
)


@workflow_state("long_running_register_capture")
class PendingPrompts(WorkflowState):
    prompts: list[dict] = []


def digest_of(evt: BaseHookEvent) -> str:
    return message_label(evt).removeprefix("message:")


@on(
    Event.UserPromptSubmit,
    only_if=[DriveActive(), OwnerPrompt()],
    skip_if=[FromSubagent()],
    tests={
        Input(prompt="keep the nightly report", state=ACTIVE): Allow(),
        Input(prompt="keep the nightly report"): Allow(),
    },
)
def record_owner_prompt(evt: BaseHookEvent) -> HookResult | None:
    with PendingPrompts.mutate(evt) as pending:
        pending.prompts.append({"digest": digest_of(evt), "session": evt.session_id, "source": "prompt", "text": evt.user_prompt or ""})
    return None


def stamps(evt: BaseHookEvent) -> dict[str, datetime]:
    found: dict[str, datetime] = {}
    for event in evt.ctx.t.events:
        match event:
            case UserEvent() if not event.meta.is_meta and not event.meta.is_compact_summary and event.text:
                found[folded(event.text)] = event.meta.timestamp
            case AttachmentEvent(attachment_type="queued_command") if event.detail.prompt:
                found[folded(event.detail.prompt)] = event.meta.timestamp
    return found


def stamped(prompt: dict, at: datetime) -> dict:
    return {"digest": prompt["digest"], "at": at.astimezone(PACIFIC).strftime(STAMP), "session": prompt["session"], "source": prompt["source"], "text": prompt["text"]}


@on(
    Event.PostToolUse | Event.Stop,
    only_if=[DriveActive()],
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    tests={
        Input(command="ls", state=ACTIVE, cwd=CWD, commands=readings("quiet")): Allow(),
        Input(state=ACTIVE, cwd=CWD, commands=readings("quiet")): Allow(),
        Input(
            command="ls",
            state=[*ACTIVE, PendingPrompts(prompts=[{"digest": "629293ae14388dc9", "session": "s", "source": "prompt", "text": "keep the nightly report"}])],
            cwd=CWD,
            commands={ROWS: (1, "boom"), "cci post": "#1"},
            transcript=[{"type": "user", "message": {"role": "user", "content": "keep the nightly report"}}],
        ): Allow(),
    },
)
def stamp_owner_prompts(evt: BaseHookEvent) -> HookResult | None:
    if not PendingPrompts.load(evt).prompts or not (which := drive_args(evt)):
        return None
    try:
        with PendingPrompts.mutate(evt) as pending:
            seen = stamps(evt)
            if landed := [stamped(prompt, seen[folded(prompt["text"])]) for prompt in pending.prompts if folded(prompt["text"]) in seen]:
                with (Path(read_register(evt, which)["state_dir"]) / PROMPTS).open("a") as file:
                    file.writelines(f"{json.dumps(capture)}\n" for capture in landed)
                pending.prompts = [prompt for prompt in pending.prompts if folded(prompt["text"]) not in seen]
    except READ_ERRORS as error:
        failed_open(evt, "stamp_owner_prompts", error)
        return None
    return None


def tokens(text: str) -> list[str]:
    return TOKEN.findall(text.casefold().replace("'", "").replace("’", ""))


def near(word: str, target: str) -> bool:
    if word == target or len(target) < 4:
        return word == target
    if len(word) == len(target):
        diffs = [index for index, (have, want) in enumerate(zip(word, target, strict=True)) if have != want]
        return len(diffs) == 2 and diffs[1] == diffs[0] + 1 and word[diffs[0]] == target[diffs[1]] and word[diffs[1]] == target[diffs[0]]
    shorter, longer = sorted((word, target), key=len)
    return len(longer) - len(shorter) == 1 and any(longer[:index] + longer[index + 1 :] == shorter for index in range(len(longer)))


def trigger(words: list[str]) -> int | None:
    for start in range(len(words)):
        for phrase in TRIGGERS:
            span = words[start : start + len(phrase)]
            if len(span) == len(phrase) and all(near(have, want) if len(phrase) > 1 else have == want for have, want in zip(span, phrase, strict=True)):
                return start + len(phrase)
    return None


def term_after(words: list[str], end: int) -> str:
    named = list(dropwhile(lambda word: word in LEADING, takewhile(lambda word: word not in ENDS, words[end:])))[:TERM_WORDS]
    return "" if set(named) <= PRONOUNS else " ".join(named)


def quoted(prompt: str) -> str:
    found = list(QUOTED.finditer(prompt))
    if lines := [match["line"].strip() for match in found if match["line"]]:
        return " ".join(lines)
    return next((match["inner"].lstrip(" .") for match in found if match["inner"]), "")


def strings(node: object) -> Iterator[str]:
    match node:
        case str():
            yield node
        case dict():
            for value in node.values():
                yield from strings(value)
        case list():
            for value in node:
                yield from strings(value)


def block_text(block: dict) -> str:
    return "\n".join(strings(block))


def sentence_with(text: str, needle: str) -> str:
    return next((part.strip() for part in SENTENCE.split(text) if needle and folded(needle) in folded(part)), "")


def board(evt: BaseHookEvent) -> list[dict]:
    done = subprocess.run(["cc-present", "outcomes", "--session", evt.session_id], capture_output=True, text=True, timeout=CCN_TIMEOUT_SECONDS)
    if done.returncode and NO_BOARD in done.stderr:
        return []
    done.check_returncode()
    outcomes = json.loads(done.stdout)
    return [] if outcomes["interactions"]["closed"]["value"] else outcomes["doc"]["blocks"]


def bearing(rows: dict, term: str, sentence: str) -> list[str]:
    haystack = folded(f"{term} {sentence}")
    return [
        key
        for key, row in rows.items()
        if any(len(name) > 2 and folded(name) in haystack for name in json.loads(row.get("names") or "[]"))
        or (term and folded(term) in folded(row.get("words") or ""))
    ]


def revise(evt: BaseHookEvent, blocks: list[str]) -> None:
    argv = ["cc-present", "revising", *blocks, "--note", evt.user_prompt or "", "--session", evt.session_id]
    subprocess.run(argv, capture_output=True, text=True, timeout=CCN_TIMEOUT_SECONDS, check=True)


@on(
    Event.UserPromptSubmit,
    only_if=[DriveActive(), OwnerPrompt()],
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    tests={
        Input(prompt="that is fine, ship it", state=ACTIVE, cwd=CWD, commands=readings("quiet")): Allow(),
        Input(prompt="waht is a relay slot? why is this here?", state=ACTIVE, cwd=CWD, commands={ROWS: (1, "boom"), "cci post": "#1"}): Allow(),
        Input(prompt="what is a relay slot?", state=[CompactionState(active=True)], cwd=CWD): Allow(),
        Input(prompt="what is a relay slot?", cwd=CWD, commands=readings("quiet")): Allow(),
    },
)
def mark_owner_question(evt: BaseHookEvent) -> HookResult | None:
    words = tokens(evt.user_prompt or "")
    if (end := trigger(words)) is None or not (which := drive_args(evt)):
        return None
    term, quote = term_after(words, end), quoted(evt.user_prompt or "")
    try:
        register = read_register(evt, which)
        needles = [needle for needle in (quote, term) if needle]
        marked = [block for block in board(evt) if any(folded(needle) in folded(block_text(block)) for needle in needles)]
        sentence = quote or next((found for block in marked if (found := sentence_with(block_text(block), term))), "") or sentence_with(last_reply(evt), term)
        rows = bearing(register["rows"], term, sentence)
        blocks = [block["id"] for block in marked]
        question = {"digest": digest_of(evt), "text": evt.user_prompt or "", "term": term, "sentence": sentence, "rows": rows, "blocks": blocks}
        path = Path(register["state_dir"]) / "register" / f"question-{question['digest']}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(question))
        if blocks:
            revise(evt, blocks)
        hits = [rulings(str(evt.cwd), "sweep", key, *which) for key in rows]
    except READ_ERRORS as error:
        failed_open(evt, "mark_owner_question", error)
        return None
    summary = MARKED.format(text=question["text"], blocks=", ".join(blocks) or "none", rows=", ".join(rows) or "none in the register")
    return evt.context(summary, *(hit.strip() for hit in hits if hit.strip()))
