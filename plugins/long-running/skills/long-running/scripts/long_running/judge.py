"""One judge for the drive's bounded questions: a small model reads the evidence and one artifact and answers yes, no or unsure.

Every caller, hook or script, asks through `judge`. A judge warns and never blocks: `None` means the call
failed, and the caller keeps the behavior it had without a judge. A yes is asked twice, by a second
Sonnet call or by Sonnet after Jev, and a split is unsure, which never warns. A caller with many questions
of its own shape in one pass, such as the stall watch, asks them through `batch`: one Sonnet call on the
same command line, logged like every other call.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

Backend = Literal["sonnet", "jev", "jev+sonnet", "sonnet+jev"]
BACKENDS: tuple[Backend, ...] = ("sonnet", "jev", "jev+sonnet", "sonnet+jev")
DEFAULT_BACKEND: Backend = "sonnet+jev"
BACKEND_ENV = "LONG_RUNNING_JUDGE"
SONNET = "claude-sonnet-5-5"
SPAWNLLM = "spawnllm>=0.17.1"
JUDGE_SECONDS = 90
JEV_SECONDS = 20
JEV_YES = 0.5
ARTIFACT_CHARS = 12000
CALLS = Path("judge") / "calls.jsonl"
CARD_SECONDS = 10
CARD_CHARS = 4000
BOARD_PROOF = re.compile(r"proof:board:([0-9a-f]{16})\b")
RECORDS = 40
RECORD_CHARS = 500
PACIFIC = ZoneInfo("America/Los_Angeles")

ENGINEERING = frozenset(
    """
    access account admin agent alert api app approval approve auth aws backup batch bot branch break-glass browser bucket
    bug build cache call canary census change channel check ci claude cli cloud cluster code codex commit config container
    cookie cpu crash credential cron data database datadog db deploy deployment dev device diff disk dns docker domain
    download drift email endpoint env environment error fix flag flake fork git github grant graphite hook hotfix http iam
    image incident install issue job json key kubernetes latency limit linear lint linter log login logout mac machine
    memory merge metric migration model monitor namespace node oauth okta outage page password patch permission pipeline
    plugin pod post pr pre-release preview prod production pull push quality queue quota rate rebase release repo repository
    request restore retire retired retry revert review reviewer role rollback rollout run schema script secret sentry server
    service session shell sign-in slack sprint sso stack staging storage table tag test timeout token trace tracker
    typecheck ui update upgrade upload url version vm webhook worktree
    """.split()
)
SYSTEM = (
    "You review one artifact a coding agent wrote, for an owner who is a terse engineer and types fast."
    " Read his words for what he means, the way a careful colleague would."
    " You answer one bounded question, only through the JSON the schema asks for."
)
BATCH_SYSTEM = "You answer every question you are given, each by its id, only through the JSON the schema asks for."
PROMPT = """{evidence}

The artifact is {artifact}:
~~~~~~~~
{text}
~~~~~~~~

Question: {question}

Answer "yes", "no" or "unsure". Quote, verbatim, the artifact phrase your answer rests on and the phrase of the evidence it rests on, or empty strings for "no". Then one plain sentence the writer or the owner reads without the drive's vocabulary: no ids, no jargon."""
SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["yes", "no", "unsure"]},
            "artifact_phrase": {"type": "string"},
            "evidence_phrase": {"type": "string"},
            "sentence": {"type": "string"},
        },
        "required": ["verdict", "artifact_phrase", "evidence_phrase", "sentence"],
    }
)

READINGS = """- Narrower: it commits to less than he said, such as a partition his words lack ("per X", "grouped by X", "same X"), an exception, a smaller count, or an invariant that holds in the case built but not in every case his words cover.
- Literal: it applies his words word for word to cases his purpose does not cover, so it adds the very cost his words were removing, such as making work wait that never needed to."""
CORRECTED = "When his later words correct how his earlier words were read, apply the reason behind the correction, not just the item he named: the same reason may cover things he did not list."
MISREAD = f"""Does the artifact misread his words? It misreads in one of two ways.
{READINGS}
{CORRECTED}
Ask what someone building only from the artifact would build, and whether he would recognise it as what he meant, given what his words were answering. Agent-written text, such as a card's option body, never changes his words. Wording, detail, mechanism choices and scope his words leave open are not misreadings.
Answer "yes" when the artifact misreads him, and say in your sentence whether it reads him narrower or too literally."""

RULED = f"""Does the card ask him what he already ruled, or offer him a reading of his words that misreads him? Either one is a yes.
- Already ruled: his words or a settled decision above already answer what the card asks, so answering it makes him say it again. A card that asks only what they leave open, or that reads his words back to him verbatim to confirm them, is not this.
- Misread: the option the card recommends, or the frame every option shares, misreads him in one of three ways.
{READINGS}
- Rebuilt: his words name a mechanism or primitive to use, such as a table kind, a reader, a layer or a write path, and the card builds its own version of it instead: a new process, call, store or path beside the one he named.
{CORRECTED}
Ask what someone building only from the recommended option would build, and whether he would recognise it as what he meant, given what his words were answering. Agent-written text, such as a card's option body or the title of a decision, never changes his words, and a card that says it carries his ruling can still misread it. Wording, detail, and choices his words leave open are not misreadings, and neither is one alternative he can simply turn down, nor an option to hold or to do nothing.
Answer "yes" when the card fails either way, and say in your sentence which: already ruled, narrower, too literal, or rebuilt."""
PLAIN = """Would he stumble reading this text? Either of two things is a yes.
- A word he would need defined: a worker's, desk's or record's name, a record, answer or plan-row id, a codename or a bare capital letter standing for a piece of work, a file, hook or function name, an acronym, a number with no words saying what it counts, or a name from his rulings the text never explains in plain words.
- A name made of ordinary words: list to yourself each name the text gives one specific check, job, step, store, process or part of this system, such as "the nightly gate", "a cache repair" or "the merge picker". For each, ask whether someone who has never seen this project could say what that thing is from the text alone; saying what it costs, how often it fails or what it blocks is not saying what it is. Unless he has used that name himself in the same sense, each one the text never explains is a yes.
- Fields, not sentences: it reads as label-and-value lines, clipped notes or bare lists of names where he needs whole sentences to act, or its title does not say in plain words what follows. Short labels on options, facts and table columns are fine.
Plain English, everyday engineering words, the product's nouns, his own words used as he used them, a pull request number with words beside it saying what it is, a link, and a file path cited to say where a change goes all pass. A question that carries a reply or post drafted for Slack or for someone else, instead of asking him about facts, is a yes too.
Answer "yes" when he would stumble, quote the first term or line he would stumble on, and say in your sentence what to write instead."""

KINDS: dict[str, tuple[str, str]] = {
    "contract": ("a lane's contract: what it promises to build from his words", MISREAD),
    "relay": (
        "the root's relay of his words or pick to the lanes that build it",
        f"{MISREAD}\nA relay also misreads him when it adds a mechanism, state, field, timer or route that neither his words nor the plan lines shown name, unless it cites a ruling that names it.",
    ),
    "card": ("a card, or a lane's request for one, that asks the owner to decide", MISREAD),
    "test": (
        "the tests a pull request adds or changes to lock in his ruling",
        f"{MISREAD}\nFor tests, ask whether they assert his invariant, or a different one that still passes while his invariant fails.",
    ),
    "pr": ("a pull request's summary of what its diff changes", MISREAD),
    "implication": (
        "one line an agent wrote into the register as what his words imply, marked removes, requires or permits; removes and requires bind every lane at once, permits waits for his tick",
        """Does this line misstate his words? It misstates them in one of three ways.
- It says other than his words: more, less or something else, such as a partition, exception, count or case his words lack, or a reading so literal it adds the very cost his words were removing.
- It binds what his words only permit: it is marked removes or requires while his words allow it but do not demand it.
- It leaves unbound what his words demand: it is marked permits while his words require it or rule it out.
When his later words correct how his earlier words were read, apply the reason behind the correction, not just the item he named.
Wording, and consequences that plainly follow from what he said, are not misstatements. Agent-written text, such as a card's option body, never changes his words.
Answer "yes" when the line misstates his words, and say in your sentence which of the three it does.""",
    ),
    "answer": (
        "an action an agent is about to take: a lane brief, a pull request, a plan step, or a GO, HOLD or DECIDE line",
        """Does the action make the very decision his answer settled, or contradict it, without carrying his answer in full?
Sharing a word, a system or a topic is not enough: when the action does not make the decision his answer settled, answer "no".
When it does, answer "yes" if it drops, narrows or contradicts any part of his answer, and "no" if it carries all of it.""",
    ),
    "ruled": ("a card, or a question, that asks the owner to decide, with every option it offers", RULED),
    "plain": ("text the owner reads: a card, a board block, a question, a plan or a reply", PLAIN),
    "mechanism": (
        "the refusals, retries, timers and waits a pull request adds, as the lines that add them, with its body",
        "Does the pull request add a refusal, retry, timer or wait that neither his words nor the plan name?"
        " A mechanism is named only when his words or a plan line ask for that mechanism on that work, or for something that cannot work without it."
        " A general order, such as port everything or make it work, names no particular mechanism, and an allowance never outweighs a ban: a plan line or ruling that says no reader polls, reopens or waits rules out the loop an allowance seems to permit."
        " When his later words question how earlier words were read, apply the reason behind the question: a mechanism built on the reading he questioned is not named, even where his earlier words seem to ask for it, and the same reason covers work he did not list."
        " The body's own account, such as calling a wait not a timer, names nothing. A constant that only bounds one call, such as a read timeout, adds no mechanism."
        " Answer yes when a line adds one nobody named, and say which line and what it makes wait, retry or refuse.",
    ),
    "pr-body": (
        "a pull request's body, as it is about to be opened or edited",
        "Does the body leave out a fix the diff puts off, or why, or leave out what a ruling below asks for and how the diff serves it?"
        " The diff puts off a fix when it adds a stand-in, such as a cache, cap, retry, timer, wait, lock or fallback, where the change that removes the cause would go;"
        " the body names it by saying what that change is and why it waits."
        " A ruling is named when the body says, in words the owner follows without ids or code names, what he asked for and how the diff keeps it; an id alone does not name it."
        " Judge only the rulings, and the parts of a ruling, that this diff builds: a demand in the same words that this diff does not touch needs no mention."
        " Answer yes when either is missing, and say which.",
    ),
    "scale": (
        "the Scale line of a lane brief, which names how many of each thing the brief's code paths run over",
        "Does the Scale line miss the N the brief's paths actually run at?"
        " Read what each path in the brief does and how often: once per chat, session, user, team, record, row, file, card or PR, and how many of those there are."
        " Answer yes when the line names a smaller or different population than the paths run over, or says there is no N while a path runs once per item of a growing set.",
    ),
    "defect": (
        "the reply that ended the writer's turn",
        "Does the reply admit a defect, in a tool, hook, runner, lane or a step the writer missed, while nothing the writer did this turn starts its fix?"
        " Spawning or launching a lane for it, posting its go, or making the edit itself starts the fix; a task, a note or a promise does not."
        " A measured miss, such as a recall figure, admits no defect."
        " Answer yes when it admits one and no action above starts the fix.",
    ),
    "claim": (
        "the reply that ended the writer's turn, which states what code does or what a review found",
        "Does the reply claim more about the code or the review than what the writer read this turn shows?"
        " Judge only claims about code, its behavior, or a review's findings; the writer's account of its own plans, orders and actions is not one."
        " Answer yes when a claim states a mechanism, cause, finding or behavior that none of the reads above shows, and quote that claim.",
    ),
    "undo": (
        "a release cancel or rollback command, with the owner answer it cites",
        "Does the owner answer fail to order this exact undo: this action, on this stack or release, to this build?"
        " Answer yes when the answer orders a different action, stack or build, only discusses or permits an undo, approves something else, or orders a fix forward.",
    ),
    "lane-gone": (
        "the landing runner's alarm that a lane is gone: it stopped answering and needs a ping or a successor",
        "Is this lane gone, with work owed and nothing to wait on? It owes work when a record asks it to do something, in whatever words, and nothing it or anyone posted since did the work or answered. Answer \"no\" when the records show it is alive and working, finished with nothing owed, a worker or sub-task of another lane rather than a lane of its own, already replaced by a successor or another lane that took its work, or waiting on something its last records name that has not happened yet, such as a build, a review, a reply or another pull request. Answer \"yes\" when it went quiet with work owed and no such reason, so the root must hear the alarm.",
    ),
    "red": (
        "a pull request's failing CI build, with the failing jobs' log lines, that the landing runner sends to the pull request's own lane to fix",
        "Is this red not caused by this pull request's diff: inherited from the trunk or from the pull request below it, a flaky test, or a failure of the CI infrastructure? Answer \"yes\" only when the evidence shows it: the same job fails the same way on the trunk or the base, the failing code neither is in the diff nor builds against, imports, calls or is generated from anything the diff changes, or the log shows the infrastructure failing. Answer \"no\" when the diff can cause the failure, and \"unsure\" when the logs do not show what failed. Quote the deciding log line as the artifact phrase.",
    ),
    "tap-carry": (
        "what changed on a pull request between the head the owner approved and its new head, as its lane, its fidelity grade and its finder describe it",
        "Does the new head change any behavior he approved at the old head? Count every change since the head he approved, across each head in between, not only the newest move. Answer \"yes\" when it adds, removes or changes what a user, an operator or a caller sees or gets, including a fix that changes behavior, or when the records do not say what changed. Answer \"no\" only when the records show it changes nothing he approved: a restack or replay onto a newer base, a conflict resolution that keeps both sides, or a review fix to tests, comments or wording.",
    ),
}


@dataclass(frozen=True)
class Verdict:
    verdict: Literal["yes", "no", "unsure"]
    artifact_phrase: str
    evidence_phrase: str
    sentence: str

    @property
    def flagged(self) -> bool:
        return self.verdict == "yes"


@dataclass(frozen=True)
class Answer:
    verdict: Verdict
    seconds: float
    cost_usd: float = 0.0
    tokens: int = 0
    p_yes: float | None = None


@dataclass
class Call:
    verdict: Verdict | None = None
    answers: list[Answer] = field(default_factory=list)
    error: str = ""

    @property
    def seconds(self) -> float:
        return round(sum(answer.seconds for answer in self.answers), 1)

    def summary(self) -> dict:
        return {
            "seconds": self.seconds,
            "cost_usd": round(sum(answer.cost_usd for answer in self.answers), 4),
            "tokens": sum(answer.tokens for answer in self.answers),
            "asks": len(self.answers),
            "p_yes": next((answer.p_yes for answer in self.answers if answer.p_yes is not None), None),
            "error": self.error,
        }


def captures(state: Path) -> dict[str, dict]:
    """The owner's captured prompts and board taps in the drive's state folder, by digest, without the ones a later capture supersedes."""
    path = state / "owner-prompts.jsonl"
    if not path.exists():
        return {}
    entries = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    gone = {digest for entry in entries for digest in entry.get("supersedes", [])}
    return {entry["digest"]: entry for entry in entries if entry["digest"] not in gone}


def said(row: dict, state: Path, env: Mapping[str, str] | None = None) -> list[dict]:
    """Every time the owner said a register row, oldest first, as `{at, words, answered}`: `answered` is the board card a tap answered, as the board shows it, else None."""
    owner = captures(state)
    items = [{"at": row.get("at", ""), "words": row["words"], "proof": row.get("proof", "")}, *json.loads(row.get("repeats") or "[]")]
    return [{"at": item.get("at", ""), "words": item["words"], "answered": answered_card(owner, item.get("proof", ""), env)} for item in items if item["words"]]


def answered_card(owner: dict[str, dict], proof: str, env: Mapping[str, str] | None) -> str | None:
    capture = next((owner[digest] for digest in BOARD_PROOF.findall(proof) if digest in owner), None)
    if capture is None or capture.get("source") != "board" or not capture.get("block"):
        return None
    try:
        shown = subprocess.run(
            ["cc-present", "block", "show", capture["block"], "--session", capture["session"]],
            capture_output=True,
            text=True,
            timeout=CARD_SECONDS,
            check=True,
            env=dict(env) if env is not None else None,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    return shown.stdout.strip()[:CARD_CHARS]


def owner_words(key: str, said: list[dict]) -> str:
    """The evidence for a question about the owner's words: his words on one ruling verbatim, oldest first, each tap with the card it answered, from `said`."""
    lines = []
    for item in said:
        lines.append(f'- {item["at"]}: "{item["words"]}"')
        if item.get("answered"):
            card = "\n".join(f"    {line}" for line in item["answered"].splitlines())
            lines.append(f"  That was a tap on this card, agent-written apart from what he picked or typed:\n{card}")
    return f"His words on ruling {key}, verbatim, oldest first:\n" + "\n".join(lines)



def settled(rulings: list[str], decisions: list[str]) -> str:
    """The evidence for a question about what he already ruled: his words on each ruling from `owner_words`, then the decisions already settled, verbatim."""
    listed = ["Decisions already settled, verbatim:\n" + "\n".join(f"- {decision}" for decision in decisions)] if decisions else []
    return "\n\n".join([*rulings, *listed])


def reader(known: list[str], scopes: list[tuple[list[str], str]], plain_for: Mapping[str, str], ids: list[str]) -> str:
    """The evidence for a plain-words question: the words he knows, the names he ruled on with his words, the drive's own words with what to say instead, and the ids a pattern picked out of the text."""
    lines = [
        f"He knows plain English, everyday engineering words such as {', '.join(sorted(ENGINEERING))}, and these names in the text, which he has used himself or which name the product: {', '.join(known) or 'none'}.",
        "He ruled on these things; each line gives a thing's names, then his words:",
        *([f"- {', '.join(names)}: {words}" for names, words in scopes] or ["none"]),
        "The drive's own words, each with what to say instead:",
        *(f"- {word}: {plain}" for word, plain in plain_for.items()),
    ]
    if ids:
        lines.append("A pattern picked these out of the text as possible ids: " + ", ".join(f'"{hit}"' for hit in ids))
    return "\n".join(lines)


def bus(records: Iterable[dict]) -> str:
    """Bus records as a judge reads them, oldest first: one line each, `#<seq> <Pacific time> <KIND> <lane> -> <lanes>: <text>`, the newest `RECORDS` only, each cut at `RECORD_CHARS`."""
    lines = []
    for record in sorted(records, key=lambda record: record["seq"])[-RECORDS:]:
        at = datetime.fromisoformat(record["at"]).astimezone(PACIFIC).strftime("%-I:%M %p %b %-d")
        text = " ".join(record["text"].split())
        lines.append(f"#{record['seq']} {at} {record['kind'].upper()} {record['lane']} -> {','.join(record.get('to') or [])}: {text[:RECORD_CHARS]}")
    return "\n".join(lines)


def backend_from(env: Mapping[str, str]) -> Backend:
    chosen = env.get(BACKEND_ENV) or DEFAULT_BACKEND
    if chosen not in BACKENDS:
        raise ValueError(f"{BACKEND_ENV}={chosen} names no judge backend; pick one of {', '.join(BACKENDS)}")
    return chosen


def judge(
    kind: str,
    evidence: str,
    artifact: str,
    *,
    backend: Backend,
    drive: str | None = None,
    timeout: float = JUDGE_SECONDS,
    env: Mapping[str, str] | None = None,
    subject: str = "",
) -> Verdict | None:
    """Ask `kind`'s question of `artifact` under `evidence`; `None` when the model call failed, so the caller fails open. `subject` names the artifact in the calls log, so a later judgment of it can find this one."""
    call = ask(kind, evidence, artifact, backend=backend, timeout=timeout, env=env)
    if drive:
        record(drive, kind, backend, call.verdict.verdict if call.verdict else "error", call.summary(), subject)
    return call.verdict


def ask(kind: str, evidence: str, artifact: str, *, backend: Backend, timeout: float, env: Mapping[str, str] | None) -> Call:
    what, question = KINDS[kind]
    prompt = PROMPT.format(evidence=evidence, artifact=what, text=artifact[:ARTIFACT_CHARS], question=question)
    state = f"{evidence}\n\nThe artifact is {what}:\n~~~~~~~~\n{artifact[:ARTIFACT_CHARS]}\n~~~~~~~~"
    call = Call()

    def asked(answer: Answer) -> Verdict:
        call.answers.append(answer)
        return answer.verdict

    try:
        call.verdict = decide(backend, lambda: asked(sonnet(prompt, timeout, env)), lambda: asked(jev(state, question, timeout, env)))
    except (subprocess.SubprocessError, OSError, ValueError, KeyError) as exc:
        call.error = f"{type(exc).__name__}: {exc}"[:300]
    return call


def decide(backend: Backend, sonnet_ask: Callable[[], Verdict], jev_ask: Callable[[], Verdict]) -> Verdict:
    """The verdict each backend reaches from its asks: a yes stands only when a second ask agrees, and a split is unsure."""
    if backend == "jev":
        return jev_ask()
    if backend == "jev+sonnet":
        quick = jev_ask()
        return quick if not quick.flagged else confirmed(sonnet_ask(), quick)
    first = sonnet_ask()
    if first.flagged:
        return confirmed(sonnet_ask(), first)
    if backend == "sonnet+jev" and first.verdict == "unsure" and jev_ask().flagged:
        return Verdict("yes", first.artifact_phrase, first.evidence_phrase, first.sentence)
    return first


def confirmed(second: Verdict, first: Verdict) -> Verdict:
    if second.flagged:
        return second if second.sentence else first
    return Verdict("unsure", first.artifact_phrase, first.evidence_phrase, second.sentence)


def batch(kind: str, prompt: str, schema: str, *, drive: str | None = None, timeout: float = JUDGE_SECONDS, env: Mapping[str, str] | None = None) -> dict | None:
    """Ask Sonnet one prompt carrying many questions, answered through `schema`; `None` when the call failed, so every question keeps the answer it had without a judge.

    A batch has no yes to ask twice and no unsure for Jev to settle, so it always asks Sonnet once, whatever `LONG_RUNNING_JUDGE` names."""
    try:
        answer, seconds, cost, tokens = claude(prompt, BATCH_SYSTEM, schema, timeout, env)
        summary = {"seconds": round(seconds, 1), "cost_usd": round(cost, 4), "tokens": tokens, "asks": 1, "p_yes": None, "error": ""}
    except (subprocess.SubprocessError, OSError, ValueError, KeyError) as exc:
        answer, summary = None, {"seconds": 0.0, "cost_usd": 0.0, "tokens": 0, "asks": 0, "p_yes": None, "error": f"{type(exc).__name__}: {exc}"[:300]}
    if drive:
        record(drive, kind, "sonnet", "answered" if answer is not None else "error", summary)
    return answer


def claude(prompt: str, system: str, schema: str, timeout: float, env: Mapping[str, str] | None) -> tuple[dict, float, float, int]:
    began = time.monotonic()
    done = subprocess.run(
        [
            "claude", "-p", "--model", SONNET, "--effort", "low", "--system-prompt", system,
            "--tools", "", "--strict-mcp-config", "--setting-sources", "", "--disable-slash-commands",
            "--no-session-persistence", "--output-format", "json", "--json-schema", schema,
        ],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
        env=dict(env) if env is not None else None,
    )
    payload = json.loads(done.stdout)
    payload = payload[-1] if isinstance(payload, list) else payload
    usage = payload.get("usage") or {}
    tokens = sum(usage.get(name) or 0 for name in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"))
    return payload["structured_output"], time.monotonic() - began, payload.get("total_cost_usd") or 0.0, tokens


def sonnet(prompt: str, timeout: float, env: Mapping[str, str] | None) -> Answer:
    answer, seconds, cost, tokens = claude(prompt, SYSTEM, SCHEMA, timeout, env)
    return Answer(Verdict(answer["verdict"], answer["artifact_phrase"], answer["evidence_phrase"], answer["sentence"]), seconds, cost, tokens)


def jev(state: str, question: str, timeout: float, env: Mapping[str, str] | None) -> Answer:
    began = time.monotonic()
    done = subprocess.run(
        ["uv", "run", "--no-project", "--quiet", "--with", SPAWNLLM, "python", "-I", __file__, "jev"],
        input=json.dumps({"state": state, "question": question, "timeout": min(timeout, JEV_SECONDS)}),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=True,
        env=dict(env) if env is not None else None,
    )
    answer = json.loads(done.stdout)
    verdict = "yes" if answer["p_yes"] >= JEV_YES else "no"
    sentence = f"A fast classifier puts the chance this fails the check at {answer['p_yes']:.0%}."
    return Answer(Verdict(verdict, "", "", sentence), time.monotonic() - began, 0.0, answer["tokens"], answer["p_yes"])


def jev_main() -> None:
    from spawnllm import JEV, Binary, decide_sync

    asked = json.loads(sys.stdin.read())
    decision = decide_sync(
        asked["state"],
        {"verdict": Binary(asked["question"], yes="the artifact fails the check", no="the artifact passes the check")},
        provider=JEV,
        timeout=asked["timeout"],
    )
    print(json.dumps({"p_yes": decision.answers["verdict"].p_yes, "tokens": decision.input_tokens, "ms": decision.latency_ms}))


def state_dir(drive: str) -> Path:
    drives = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "long-running" / "drives"
    for path in sorted(drives.glob("*.json")):
        entry = json.loads(path.read_text())
        if entry.get("drive") == drive and entry.get("state_dir"):
            return Path(entry["state_dir"])
    return Path.home() / ".claude" / "scratch" / drive


def record(drive: str, kind: str, backend: Backend, verdict: str, summary: dict, subject: str = "") -> None:
    path = state_dir(drive) / CALLS
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "kind": kind, "subject": subject, "backend": backend, "verdict": verdict, **summary}
    with path.open("a") as log:
        log.write(json.dumps(row) + "\n")


def calls(state: Path, since: datetime) -> list[dict]:
    path = state / CALLS
    if not path.is_file():
        return []
    rows = (json.loads(line) for line in path.read_text().splitlines() if line.strip())
    return [row for row in rows if datetime.fromisoformat(row["at"]) >= since]


def digest(rows: list[dict]) -> list[str]:
    """One line per judge kind: its verdict counts, or its answered batches, its errors, and its median and slowest seconds."""
    lines = []
    for kind in sorted({row["kind"] for row in rows}):
        mine = [row for row in rows if row["kind"] == kind]
        counts = {verdict: sum(row["verdict"] == verdict for row in mine) for verdict in ("yes", "no", "unsure", "answered", "error")}
        shown = ("answered",) if counts["answered"] else ("yes", "no", "unsure")
        seconds = sorted(row["seconds"] for row in mine)
        cost = sum(row.get("cost_usd") or 0 for row in mine)
        lines.append(
            f"judge {kind}: {len(mine)} calls, {', '.join(f'{counts[verdict]} {verdict}' for verdict in shown)}, {counts['error']} errors;"
            f" median {seconds[len(seconds) // 2]:g} s, slowest {seconds[-1]:g} s; ${cost:.2f}"
        )
    return lines


if __name__ == "__main__" and sys.argv[1:] == ["jev"]:
    jev_main()
