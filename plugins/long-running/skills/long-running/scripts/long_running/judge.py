"""One judge for the drive's bounded questions: a small model reads the evidence and one artifact and answers yes, no or unsure.

Every caller, hook or script, asks through `judge`. A judge warns and never blocks: `None` means the call
failed, and the caller keeps the behavior it had without a judge. A yes is asked twice, by a second
Sonnet call or by Sonnet after Jev, and a split is unsure, which never warns.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

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

SYSTEM = (
    "You review one artifact a coding agent wrote, for an owner who is a terse engineer and types fast."
    " Read his words for what he means, the way a careful colleague would."
    " You answer one bounded question, only through the JSON the schema asks for."
)
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

MISREAD = """Does the artifact misread his words? It misreads in one of two ways.
- Narrower: it commits to less than he said, such as a partition his words lack ("per X", "grouped by X", "same X"), an exception, a smaller count, or an invariant that holds in the case built but not in every case his words cover.
- Literal: it applies his words word for word to cases his purpose does not cover, so it adds the very cost his words were removing, such as making work wait that never needed to.
When his later words correct how his earlier words were read, apply the reason behind the correction, not just the item he named: the same reason may cover things he did not list.
Ask what someone building only from the artifact would build, and whether he would recognise it as what he meant, given what his words were answering. Agent-written text, such as a card's option body, never changes his words. Wording, detail, mechanism choices and scope his words leave open are not misreadings.
Answer "yes" when the artifact misreads him, and say in your sentence whether it reads him narrower or too literally."""

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
        record(drive, kind, backend, call, subject)
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


def sonnet(prompt: str, timeout: float, env: Mapping[str, str] | None) -> Answer:
    began = time.monotonic()
    done = subprocess.run(
        [
            "claude", "-p", "--model", SONNET, "--effort", "low", "--system-prompt", SYSTEM,
            "--tools", "", "--strict-mcp-config", "--setting-sources", "", "--disable-slash-commands",
            "--no-session-persistence", "--output-format", "json", "--json-schema", SCHEMA,
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
    answer = payload["structured_output"]
    return Answer(
        Verdict(answer["verdict"], answer["artifact_phrase"], answer["evidence_phrase"], answer["sentence"]),
        time.monotonic() - began,
        payload.get("total_cost_usd") or 0.0,
        sum(usage.get(name) or 0 for name in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")),
    )


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


def record(drive: str, kind: str, backend: Backend, call: Call, subject: str = "") -> None:
    path = state_dir(drive) / CALLS
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kind": kind,
        "subject": subject,
        "backend": backend,
        "verdict": call.verdict.verdict if call.verdict else "error",
        **call.summary(),
    }
    with path.open("a") as log:
        log.write(json.dumps(row) + "\n")


def calls(state: Path, since: datetime) -> list[dict]:
    path = state / CALLS
    if not path.is_file():
        return []
    rows = (json.loads(line) for line in path.read_text().splitlines() if line.strip())
    return [row for row in rows if datetime.fromisoformat(row["at"]) >= since]


def digest(rows: list[dict]) -> list[str]:
    """One line per judge kind: its verdict counts, its errors, and its median and slowest seconds."""
    lines = []
    for kind in sorted({row["kind"] for row in rows}):
        mine = [row for row in rows if row["kind"] == kind]
        counts = {verdict: sum(row["verdict"] == verdict for row in mine) for verdict in ("yes", "no", "unsure", "error")}
        seconds = sorted(row["seconds"] for row in mine)
        cost = sum(row.get("cost_usd") or 0 for row in mine)
        lines.append(
            f"judge {kind}: {len(mine)} calls, {counts['yes']} yes, {counts['no']} no, {counts['unsure']} unsure, {counts['error']} errors;"
            f" median {seconds[len(seconds) // 2]:g} s, slowest {seconds[-1]:g} s; ${cost:.2f}"
        )
    return lines


if __name__ == "__main__" and sys.argv[1:] == ["jev"]:
    jev_main()
