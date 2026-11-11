import re
from pathlib import Path

from drive import state

BRIEF_JOB = re.compile(r"^Drive:[^\n]*?\bjob (j\d+)\b", re.MULTILINE)
BRIEF_FILE = re.compile(r"/\S+/jobs/j\d+\.brief\.md\b")
HANDOFF = re.compile(r"^Handoff:", re.MULTILINE)
RESEARCHED = re.compile(r"^Researched:[ \t]*$", re.MULTILINE)
RESEARCHED_LINE = re.compile(r"^Researched:", re.MULTILINE)
RESEARCHED_BY = re.compile(r"^Researched:[ \t]*(j\d+)\b", re.MULTILINE)
REUSES = re.compile(r"^REUSES:[ \t]*\S", re.MULTILINE)
KEYS = ("rulings", "exists", "open")
RESEARCHED_KINDS = ("builder", "design")
OPUS_KINDS = ("reader", "builder", "design")
LIGHT_MODELS = ("sonnet", "haiku")
BLOCK = "a Researched: line and under it rulings:, exists: and open: lines"


def carried(prompt: str) -> str:
    """An agent's prompt with the brief file drive job brief wrote read in after it, when the prompt names one."""
    named = BRIEF_FILE.search(prompt)
    return f"{prompt}\n{Path(named[0]).read_text()}" if named else prompt


def researched(text: str) -> dict[str, str]:
    """The Researched: block a reader returned: its rulings, exists and open lines, by key, read up to the first line that is none of them."""
    found = RESEARCHED.search(text)
    if not found:
        return {}
    block = {}
    for line in text[found.end() :].lstrip("\n").splitlines():
        key, colon, value = line.strip().removeprefix("- ").partition(":")
        if not colon or key.strip().casefold() not in KEYS:
            break
        block[key.strip().casefold()] = value.strip()
    return block


def filled(block: dict[str, str]) -> bool:
    return all(block.get(key) for key in KEYS)


def research(name: str, data: dict, order_id: str) -> tuple[str, str] | None:
    """The newest reader of the order whose kept report carries a filled Researched: block, with that report."""
    for job_id in reversed(data["orders"][order_id]["jobs"]):
        job = data["jobs"][job_id]
        if job["kind"] == "reader" and job["order"] == order_id and filled(researched(text := state.report_text(name, job_id, job))):
            return job_id, text
    return None


def job_refusal(name: str, data: dict, order_id: str, kind: str) -> str | None:
    """Why a job of this kind may not start on the order: an answer order is one reader's, and a build order's builder or designer waits on its reader's research, and on a design lane when that research found a choice for him."""
    order = data["orders"][order_id]
    if order["kind"] == "answer" and kind != "reader":
        return (
            f"{order_id} is an answer order: one reader researches it and answers him, with no {kind} beside it; work its"
            f" answer finds to build is a build order: drive order set {order_id} --kind build"
        )
    if order["kind"] != "build" or order["incident"] or kind not in RESEARCHED_KINDS:
        return None
    found = research(name, data, order_id)
    if found is None:
        return (
            f"{order_id} is a build order, so its {kind} starts only once an Opus reader has kept {BLOCK} in its report:"
            f' drive job add reader --order {order_id} --goal "research: <what>", then a long-running:reader whose brief'
            " asks for that block; its rulings line holds his recorded answers on the order's words and area, its exists"
            " line what already does part of it, and its open line each question the order implies"
        )
    reader, text = found
    designing = any(data["jobs"][job_id]["kind"] == "design" for job_id in order["jobs"] if data["jobs"][job_id]["order"] == order_id)
    if kind == "builder" and text.lstrip().startswith("DESIGN") and not designing:
        return (
            f"{reader} returned DESIGN: the choice its research found goes to him as cards before any builder:"
            f" drive job add design --order {order_id}, then a long-running:designer briefed Researched: {reader}"
        )
    return None


def brief_refusal(name: str, data: dict, job_id: str, brief: str, model: str | None) -> str | None:
    """Why this brief may not start its job's agent: a build order's builder or designer needs the Researched: line naming its reader, an answer order's one lane does its own research, and a complex or build order's lane runs on Opus."""
    job = data["jobs"][job_id]
    order_id = job["order"]
    order = data["orders"][order_id]
    if HANDOFF.search(brief) or order["incident"] or not order["kind"]:
        return None
    light = job["kind"] in OPUS_KINDS and any(word in (model or "") for word in LIGHT_MODELS)
    if order["kind"] == "answer":
        if RESEARCHED_LINE.search(brief):
            return (
                f"{job_id} answers {order_id}, an answer order: its one lane does its own research, reading code,"
                " transcripts, records and his answers itself, so its brief carries no Researched: line"
            )
        if light and order["complex"]:
            return f"{order_id} is a complex answer order: its lane runs on Opus; pass no model, or opus"
        return None
    if light:
        return f"{order_id} is a build order: its reader, designer and builder run on Opus; pass no model, or opus"
    if job["kind"] not in RESEARCHED_KINDS:
        return None
    named = RESEARCHED_BY.search(brief)
    if named is None:
        return (
            f"{job_id} builds on {order_id}, a build order: its brief carries the line Researched: <its reader's job>,"
            " naming the reader whose kept report holds the order's rulings, what exists and its open questions"
        )
    reader = data["jobs"].get(named[1])
    if not (reader and reader["kind"] == "reader" and reader["order"] == order_id and filled(researched(state.report_text(name, named[1], reader)))):
        return f"Researched: {named[1]} names no reader of {order_id} that kept {BLOCK} in its report"
    return None


def report_refusal(name: str, data: dict, job_id: str, text: str) -> str | None:
    """Why a job's return may not be kept: a reader's DESIGN is a choice for him with its options, and a build order's READY says how it builds on what its research found."""
    job = data["jobs"][job_id]
    order = data["orders"][job["order"]]
    lines = [line.strip() for line in text.strip().splitlines()]
    if job["kind"] == "reader" and lines[0].startswith("DESIGN"):
        options = [line for line in lines if line.startswith("- ")]
        if not 2 <= len(options) <= 4 or sum(option.endswith("(recommended)") for option in options) != 1:
            return (
                "a reader's DESIGN return is a choice for him, which a design lane turns into his cards: two to four `- `"
                " option lines, exactly one ending (recommended); a finding with no choice in it returns DONE"
            )
    if not (job["kind"] == "builder" and lines[0].startswith("READY") and order["kind"] == "build") or order["incident"]:
        return None
    found = research(name, data, job["order"])
    exists = researched(found[1])["exists"] if found else "none"
    if exists.casefold().rstrip(".") == "none" or REUSES.search(text):
        return None
    return (
        f"{found[0]}'s research found what already does part of this: {exists}. A READY says, in a REUSES: line, how the"
        " PR calls or extends it rather than adding a second copy beside it"
    )
