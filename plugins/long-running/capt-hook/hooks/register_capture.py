from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from itertools import dropwhile, takewhile
from pathlib import Path

from cc_transcript import AttachmentEvent, UserEvent
from captain_hook import Allow, BaseHookEvent, Event, FromSubagent, HookResult, Input, Tool, WorkflowState, on, workflow_state

from .compaction_handoff import CCN_TIMEOUT_SECONDS, CompactionState, ccn, rulings
from .owner_facing import REPLY_WINDOW, last_reply
from .register_gates import READ_ERRORS, captures, failed_open, folded, read_register
from .register_injection import drive_args
from .root_context import DriveActive, OwnerPrompt, message_label
from .tests.register_fixtures import ACTIVE, CWD, DRIVE_CONFIG, ROWS, readings

STAMP = "%Y-%m-%dT%H:%M:%SZ"
TRIGGERS = (
    ("what", "is"),
    ("whats",),
    ("why", "does", "it", "need", "this"),
    ("do", "we", "even", "need"),
    ("i", "dont", "understand"),
    ("i", "do", "not", "understand"),
)
TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*|[?.!,;:\n]")
SLACK_MESSAGE = re.compile(r'<channel source="plugin:cc-slack:cc-slack"[^>]*type="slack\.message">\s*(?P<payload>\{.*\})\s*</channel>', re.DOTALL)
OPERATOR = "U05FTPXU8PP"
FALLBACK_POST = "_(Yasyf's Claude)_"
PASTED_IMAGE = re.compile(r"\[Image #\d+\]")
ENDS = frozenset({"?", ".", "!", ",", ";", ":", "\n", "and", "or", "but", "wtf"})
LEADING = frozenset({"a", "an", "the", "this", "that", "these", "those"})
PRONOUNS = frozenset({"this", "that", "it", "these", "those", "all", "everything"})
TERM_WORDS = 6
QUOTE_CHARS = 12
QUOTED = re.compile(rf'^>\s?(?P<line>.+)$|["“](?P<inner>[^"”\n]{{{QUOTE_CHARS},}})["”]', re.MULTILINE)
RELAY = re.compile(r'<cross-session-message\s+from="(?P<sender>[^"]*)"(?:\s+from-name="(?P<name>[^"]*)")?[^>]*>(?P<body>.*?)</cross-session-message>', re.DOTALL)
OWNER = re.compile(r"\b(?:owner|yasyf)\b", re.IGNORECASE)
RELAYED = 'From the ai-oncall root, relaying what the owner said, verbatim: "we shouldnt need a pin PR before cutover"'
SENTENCE = re.compile(r"(?<=[.?!])\s+|\n+")
CREDENTIAL = re.compile(
    r"\b(?:sk|pk|rk)-[\w-]{16,}"
    r"|\b(?:gh[pousr]|github_pat)_[\w.-]{20,}"
    r"|\bxox[abprs]-[\w-]{10,}"
    r"|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"
    r"|\b(?:api|sdk|mob)-[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b"
    r"|\beyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}"
    r"|(?<![\w/+=.-])(?=[\w+/=]*[a-z])(?=[\w+/=]*[A-Z])(?=[\w+/=]*\d)[\w+/=]{32,}(?=[.,;:!?)\]\"'`]*(?:\s|$))"
)
REDACTED = "[credential redacted]"
ANSWER_SOURCE = "answer"
ANSWER_LABEL = "source:askuserquestion"
ANSWER_LIMIT = "20"
DIGEST_CHARS = 16
ASKED_KEEP = {"questions": [{"question": "Keep it?", "options": [{"label": "Keep"}]}]}
PICKED_KEEP = json.dumps({"answers": {"Keep it?": "Keep"}})
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
        Input(prompt="keep the nightly report", env=DRIVE_CONFIG, state=ACTIVE): Allow(),
        Input(prompt="keep the nightly report"): Allow(),
    },
)
def record_owner_prompt(evt: BaseHookEvent) -> HookResult | None:
    if not drive_args(evt):
        return None
    with PendingPrompts.mutate(evt) as pending:
        pending.prompts.append({"digest": digest_of(evt), "submitted": datetime.now(UTC).strftime(STAMP), "session": evt.session_id, "source": "prompt", "text": evt.user_prompt or ""})
    return None


@on(
    Event.UserPromptSubmit,
    only_if=[DriveActive()],
    skip_if=[FromSubagent()],
    tests={
        Input(prompt=f'<cross-session-message from="uds:/tmp/s.sock" from-name="ai-oncall">{RELAYED}</cross-session-message>', env=DRIVE_CONFIG, state=ACTIVE): Allow(),
        Input(prompt='<cross-session-message from="uds:/tmp/s.sock">p3 DONE: note 635dab7</cross-session-message>', env=DRIVE_CONFIG, state=ACTIVE): Allow(),
    },
)
def record_relayed_quote(evt: BaseHookEvent) -> HookResult | None:
    if not (relay := RELAY.search(evt.user_prompt or "")) or not OWNER.search(relay["body"]) or not drive_args(evt):
        return None
    with PendingPrompts.mutate(evt) as pending:
        pending.prompts += [
            {
                "digest": hashlib.sha256(f"{evt.session_id}\0{quote}".encode()).hexdigest()[:16],
                "session": evt.session_id,
                "source": "relay",
                "relayed_from": relay["name"] or relay["sender"],
                "stamp": evt.user_prompt or "",
                "text": quote,
            }
            for found in QUOTED.finditer(relay["body"])
            if (quote := found["line"] or found["inner"])
        ]
    return None


def picks(evt: BaseHookEvent) -> list[tuple[str, str, str]]:
    response = json.loads(evt.tool_response) if isinstance(evt.tool_response, str) else evt.tool_response
    answers, notes = response.get("answers") or {}, response.get("annotations") or {}
    return [
        (asked["question"], answers[asked["question"]], ((notes.get(asked["question"]) or {}).get("notes") or "").strip())
        for asked in evt.input.raw.get("questions", [])
        if isinstance(answers.get(asked["question"]), str)
    ]


def picked_text(question: str, picked: str, note: str) -> str:
    return "\n".join([question, f"Picked: {picked}", *([f"Note: {note}"] if note else [])])


@on(
    Event.PostToolUse,
    only_if=[DriveActive(), Tool("AskUserQuestion")],
    skip_if=[FromSubagent()],
    tests={
        Input(tool="AskUserQuestion", tool_input=ASKED_KEEP, output=PICKED_KEEP, env=DRIVE_CONFIG, state=ACTIVE): Allow(),
        Input(tool="AskUserQuestion", tool_input=ASKED_KEEP, output=PICKED_KEEP): Allow(),
    },
)
def record_owner_picks(evt: BaseHookEvent) -> HookResult | None:
    if not drive_args(evt):
        return None
    at = datetime.now(UTC).strftime(STAMP)
    with PendingPrompts.mutate(evt) as pending:
        pending.prompts += [
            {
                "digest": hashlib.sha256(f"{evt.session_id}\0{picked_text(question, picked, note)}".encode()).hexdigest()[:DIGEST_CHARS],
                "at": at,
                "session": evt.session_id,
                "source": ANSWER_SOURCE,
                "question": question,
                "picked": picked,
                "text": picked_text(question, picked, note),
            }
            for question, picked, note in picks(evt)
        ]
    return None


def recorded_answer(evt: BaseHookEvent, prompt: dict) -> str | None:
    listed = ccn(str(evt.cwd), "answer", "list", "--json", "--label", ANSWER_LABEL, "--limit", ANSWER_LIMIT)
    listed.check_returncode()
    return next(
        (
            answer["id"]
            for answer in json.loads(listed.stdout)
            if (answer["title"] == prompt["question"] or f"\nQuestion: {prompt['question']}\n" in f"{answer['body']}\n")
            and answer["body"].split("\n", 1)[0] == prompt["picked"]
        ),
        None,
    )


def with_answer(evt: BaseHookEvent, prompt: dict) -> dict:
    if prompt["source"] != ANSWER_SOURCE or "answer" in prompt or not (found := recorded_answer(evt, prompt)):
        return prompt
    return prompt | {"digest": found[:DIGEST_CHARS], "answer": found}


def landing(evt: BaseHookEvent, prompt: dict) -> bool:
    return "at" in prompt and (prompt["source"] != ANSWER_SOURCE or "answer" in prompt or evt.event == Event.Stop)


def overlaps(left: str, right: str) -> bool:
    shorter, longer = sorted((left, right), key=len)
    return len(shorter) >= QUOTE_CHARS and shorter in longer


def repeated(capture: dict, relays: list[dict]) -> list[str]:
    typed = folded(capture["text"])
    return [relay["digest"] for relay in relays if overlaps(folded(relay["text"]), typed)]


def stamps(evt: BaseHookEvent) -> dict[str, datetime]:
    found: dict[str, datetime] = {}
    for event in evt.ctx.t.events:
        match event:
            case UserEvent() if not event.meta.is_meta and not event.meta.is_compact_summary and event.text:
                found[folded(event.text)] = event.meta.timestamp
            case AttachmentEvent(attachment_type="queued_command") if event.detail.prompt:
                found[folded(event.detail.prompt)] = event.meta.timestamp
    return found


def stamped(prompt: dict, seen: dict[str, datetime], ending: bool) -> dict:
    if "at" in prompt:
        return prompt
    if (key := folded(prompt.get("stamp", prompt["text"]))) in seen:
        return prompt | {"at": seen[key].astimezone(UTC).strftime(STAMP)}
    if "submitted" in prompt and (ending or PASTED_IMAGE.search(prompt["text"])):
        return prompt | {"at": prompt["submitted"]}
    return prompt


def slack_dms(evt: BaseHookEvent) -> list[dict]:
    messages = (
        json.loads(found["payload"])
        for event in evt.ctx.t.events
        if isinstance(event, AttachmentEvent) and event.attachment_type == "queued_command" and event.detail.prompt and (found := SLACK_MESSAGE.search(event.detail.prompt))
    )
    return [
        {
            "digest": hashlib.sha256(f"{evt.session_id}\0{message['ts']}".encode()).hexdigest()[:DIGEST_CHARS],
            "at": datetime.fromtimestamp(float(message["ts"]), UTC).strftime(STAMP),
            "session": evt.session_id,
            "source": "slack",
            "text": message["text"],
        }
        for message in messages
        if message["kind"] == "dm" and message["user"] == OPERATOR and not message["from_claude"] and not message["text"].startswith(FALLBACK_POST)
    ]


def capture(prompt: dict) -> dict:
    kept = {key: prompt[key] for key in ("relayed_from", "answer") if key in prompt}
    return {"digest": prompt["digest"], "at": prompt["at"], "session": prompt["session"], "source": prompt["source"], "text": CREDENTIAL.sub(REDACTED, prompt["text"])} | kept


@on(
    Event.PostToolUse | Event.Stop,
    only_if=[DriveActive()],
    skip_if=[FromSubagent()],
    transcript_events=REPLY_WINDOW,
    tests={
        Input(command="ls", env=DRIVE_CONFIG, state=ACTIVE, cwd=CWD, commands=readings("quiet")): Allow(),
        Input(env=DRIVE_CONFIG, state=ACTIVE, cwd=CWD, commands=readings("quiet")): Allow(),
        Input(
            command="ls",
            env=DRIVE_CONFIG, state=[*ACTIVE, PendingPrompts(prompts=[{"digest": "629293ae14388dc9", "session": "s", "source": "prompt", "text": "keep the nightly report"}])],
            cwd=CWD,
            commands={ROWS: (1, "boom"), "cci post": "#1"},
            transcript=[{"type": "user", "message": {"role": "user", "content": "keep the nightly report"}}],
        ): Allow(),
    },
)
def stamp_owner_prompts(evt: BaseHookEvent) -> HookResult | None:
    dms = slack_dms(evt)
    if not (PendingPrompts.load(evt).prompts or dms) or not (which := drive_args(evt)):
        return None
    with PendingPrompts.mutate(evt) as pending:
        seen = stamps(evt)
        pending.prompts = [stamped(prompt, seen, evt.event == Event.Stop) for prompt in pending.prompts]
        try:
            pending.prompts = [with_answer(evt, prompt) for prompt in pending.prompts]
        except READ_ERRORS as error:
            failed_open(evt, "stamp_owner_prompts", error)
        landed = [capture(prompt) for prompt in pending.prompts if landing(evt, prompt)]
        if not (landed or dms):
            return None
        try:
            path = captures(read_register(evt, which))
            earlier = [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.is_file() else []
            landed += [capture(dm) for dm in dms if dm["digest"] not in {entry["digest"] for entry in earlier}]
            if not landed:
                return None
            relays = [entry for entry in [*earlier, *landed] if entry["source"] == "relay"]
            landed = [line | ({"supersedes": names} if line["source"] == "prompt" and (names := repeated(line, relays)) else {}) for line in landed]
            with path.open("a") as file:
                file.writelines(f"{json.dumps(line)}\n" for line in landed)
        except READ_ERRORS as error:
            failed_open(evt, "stamp_owner_prompts", error)
            return None
        pending.prompts = [prompt for prompt in pending.prompts if not landing(evt, prompt)]
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


def innermost(node: object, hit: Callable[[dict], bool]) -> list[dict]:
    match node:
        case dict():
            inner = [found for value in node.values() for found in innermost(value, hit)]
            return inner or ([node] if {"id", "type"} <= node.keys() and hit(node) else [])
        case list():
            return [found for value in node for found in innermost(value, hit)]
    return []


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
        Input(prompt="that is fine, ship it", env=DRIVE_CONFIG, state=ACTIVE, cwd=CWD, commands=readings("quiet")): Allow(),
        Input(prompt="waht is a relay slot? why is this here?", env=DRIVE_CONFIG, state=ACTIVE, cwd=CWD, commands={ROWS: (1, "boom"), "cci post": "#1"}): Allow(),
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
        marked = innermost(board(evt), lambda block: any(folded(needle) in folded(block_text(block)) for needle in needles))
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
