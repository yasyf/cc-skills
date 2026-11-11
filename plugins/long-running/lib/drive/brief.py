import re
from pathlib import Path

from drive import card, limits, state
from drive.render import cut

BRIEF_JOB = re.compile(r"^Drive:[^\n]*?\bjob (j\d+)\b", re.MULTILINE)
BRIEF_FILE = re.compile(r"/\S+/jobs/j\d+\.brief\.md\b")
HANDOFF = re.compile(r"^Handoff:", re.MULTILINE)
RESEARCHED = re.compile(r"^Researched:[ \t]*$", re.MULTILINE)
RESEARCHED_LINE = re.compile(r"^Researched:", re.MULTILINE)
RESEARCHED_BY = re.compile(r"^Researched:[ \t]*(j\d+)\b", re.MULTILINE)
REUSES = re.compile(r"^REUSES:[ \t]*\S", re.MULTILINE)
CHECK = re.compile(r"^CHECK:(.*)$", re.MULTILINE)
NATIVE = re.compile(r"^NATIVE:[ \t]*(.*)$", re.MULTILINE)
PROOF = re.compile(r"https?://\S+|(?<![\w.])/[^\s,;)]+")
SEEN_PROOF = re.compile(r"slack\.com/archives/|\.(?:mov|mp4|webm|gif|png|jpe?g)$", re.IGNORECASE)
REPRO = re.compile(r"^REPRO:[ \t]*\S", re.MULTILINE)
LEFT = re.compile(r"^(?:NEXT|UNPROVEN):.*$", re.MULTILINE)
STEPS = r"(?:runs?|re-?runs?|fills?|back-?fills?|migrates?|applies|apply|executes?|triggers?|kicks?\s+off|flips?|enables?|patch(?:es)?|repairs?)"
LEFT_FOR = re.compile(
    r"\b(?:until|unless|once|after|when)\s+(?:the\s+)?(?:coordinator|communicator|handler|owner|someone|another\s+(?:lane|agent|job))\b"
    rf"|\b(?:coordinator|communicator|handler|someone(?:\s+else)?)\s+(?:must\s+|should\s+|needs?\s+to\s+|has\s+to\s+|will\s+|then\s+)?{STEPS}\b",
    re.IGNORECASE,
)
LIMIT = re.compile(r"\b(?:caps?|capped|quotas?|capacity|ceilings?|throttl\w*|rate[- ]limit\w*|limit\w*|max(?:imum)?)\b", re.IGNORECASE)
LEFT_STANDING = re.compile(r"\b(?:left\s+as[\s-]is|unchanged|hit(?:s|ting)?|exceed(?:s|ed|ing)?)\b", re.IGNORECASE)
CHOICE_OPENINGS = ("ASK", "DESIGN")
QUESTION = re.compile(r"(?:^|[,;?]|\band\b)\s*(?:why|who|whom|whose|when|where|what|which|how|whether)\b", re.IGNORECASE | re.MULTILINE)
DESIGN_PART = re.compile(r"DESIGN\s+\S")
KEYS = ("rulings", "exists", "open")
RESEARCHED_KINDS = ("builder", "design")
LANE_MODELS = {"reader": "opus", "builder": "opus", "design": "fable"}
LIGHT_MODELS = ("sonnet", "haiku")
OPENINGS = ("DONE", "READY", "BUILT", "WAITING", "ASK", "BLOCKED", "DESIGN", "HANDOFF")
WORDED_KINDS = ("builder", "design")
DESIGN_LINES = ("Settled:", "Native:", "Fork:", "Facts:")
UNDESIGNED = ("HANDOFF", "BLOCKED")
BLOCK = "a Researched: line and under it rulings:, exists: and open: lines"


def carried(prompt: str) -> str:
    """An agent's prompt with the brief file drive job brief wrote read in after it, when the prompt names one."""
    named = BRIEF_FILE.search(prompt)
    return f"{prompt}\n{Path(named[0]).read_text()}" if named else prompt


def block_lines(text: str) -> list[str]:
    """The lines of the Researched: block a reader returned under its Researched: line, bulleted or not, up to the first line that is none of rulings, exists and open."""
    found = RESEARCHED.search(text)
    if not found:
        return []
    taken = []
    for line in text[found.end() :].lstrip("\n").splitlines():
        key, colon, _ = line.strip().removeprefix("- ").partition(":")
        if not colon or key.strip().casefold() not in KEYS:
            break
        taken.append(line.strip())
    return taken


def researched(text: str) -> dict[str, str]:
    """The Researched: block a reader returned: its rulings, exists and open lines, by key."""
    block = {}
    for line in block_lines(text):
        key, _, value = line.removeprefix("- ").partition(":")
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
    """Why a job of this kind may not start on the order: an order he limited to options gets no builder or codex job until he picks, an answer order is one reader's, and a build order's builder or designer waits on its reader's research, and on a design lane when that research found a choice for him."""
    order = data["orders"][order_id]
    if order["options_only"] and kind in ("builder", "codex"):
        return (
            f"{order_id} is limited to options: he picks one before anything is built, so no {kind} starts on it; start a reader or a designer"
            f" until he picks, drive job add design --order {order_id}, and once he picks, drive order set {order_id} --no-options-only"
        )
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
    """Why this brief may not start its job's agent: a build order's builder or designer needs the Researched: line naming its reader, an answer order's one lane does its own research, a complex or build order's lane runs on its own model, and a light lane waits on an answer order asking several questions until it is marked complex or not."""
    job = data["jobs"][job_id]
    order_id = job["order"]
    order = data["orders"][order_id]
    if HANDOFF.search(brief) or order["incident"] or not order["kind"]:
        return None
    light = job["kind"] in LANE_MODELS and any(word in (model or "") for word in LIGHT_MODELS)
    if order["kind"] == "answer":
        if RESEARCHED_LINE.search(brief):
            return (
                f"{job_id} answers {order_id}, an answer order: its one lane does its own research, reading code,"
                " transcripts, records and his answers itself, so its brief carries no Researched: line"
            )
        if light and order["complex"]:
            return f"{order_id} is a complex answer order: its lane runs on Opus; pass no model, or opus"
        if light and order["complex"] is None and (asked := max(questions(order["text"]), questions(job["goal"]))) > 1:
            return (
                f"{order_id} is an answer order asking {asked} questions with no complex mark, and a {model} lane answers only"
                f" a one-fact lookup: mark it drive order set {order_id} --complex and pass no model, or drive order set"
                f" {order_id} --no-complex when one lookup answers them all"
            )
        return None
    if light:
        return (
            f"{order_id} is a build order: its reader and builder run on Opus and its designer on Fable; pass no model,"
            f" or {LANE_MODELS[job['kind']]}"
        )
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


def questions(text: str) -> int:
    """How many questions the words ask, each a clause opening with why, who, when, where, what, which, how or whether."""
    return len(QUESTION.findall(text))


def report_refusal(name: str, data: dict, job_id: str, text: str) -> str | None:
    """Why a job's return may not be kept: a builder's or designer's opens with a result word, a REPRO: file is kept on the job first, a report naming a limit as a finding left standing is an ASK or DESIGN, a design lane's is the design in its shape, a research reader's carries its Researched: block, a reader's DESIGN is a choice for him with its options and the fix its evidence points at, a DONE or READY leaves no step for someone else, a builder's DONE names its proof, and a builder's READY names PRs whose tap card draws and says how it builds on what its research found."""
    job = data["jobs"][job_id]
    order = data["orders"][job["order"]]
    lines = [line.strip() for line in text.strip().splitlines()]
    opening = re.match(r"[A-Z]*", lines[0])[0]
    if job["kind"] in WORDED_KINDS and opening not in OPENINGS:
        return (
            f'its first line opens "{cut(lines[0], 60)}", and a {job["kind"]}\'s report opens with {", ".join(OPENINGS[:-1])}'
            f" or {OPENINGS[-1]}, the word that sets the job's state; start it with the one that says where the job is"
        )
    if REPRO.search(text) and not job.get("repro"):
        return (
            f"it names a REPRO: file, but {job_id} keeps none in the drive: run drive job set {job_id} --repro <path>"
            " first, which keeps a copy where drive status lists it, then keep this report"
        )
    if opening not in CHOICE_OPENINGS and (finding := limit_finding(lines)):
        return (
            f'it reports a limit as a finding left standing: "{cut(finding, 120)}". A limit or capacity finding is his'
            " choice, so return ASK or DESIGN with two to four options for it, one recommended, in place of this report"
        )
    if job["kind"] == "design" and opening not in UNDESIGNED and (missing := design_missing(lines)):
        return (
            "a design lane's report is the design in its shape, which the coordinator reads for its forks, and this one"
            f" lacks {', '.join(missing)}: DESIGN <the part, in a few words>, then Settled:, Native:, a Fork: line per"
            " fork and Facts:"
        )
    if job["kind"] == "reader" and (refused := reader_refusal(job_id, job, order, text, lines)):
        return refused
    if opening in ("DONE", "READY") and (left := left_steps(text)):
        return (
            f'a {opening} leaves a step for someone else: "{cut(left, 120)}". Do that step yourself before you return,'
            " or return ASK when it needs his choice"
        )
    if job["kind"] == "builder" and opening == "DONE":
        return unproven(text)
    if job["kind"] == "builder" and opening == "READY" and (sections := untappable(lines[0])):
        return f"the PRs it names would draw no tap card: {sections}"
    if not (job["kind"] == "builder" and opening == "READY" and order["kind"] == "build") or order["incident"]:
        return None
    found = research(name, data, job["order"])
    exists = researched(found[1])["exists"] if found else "none"
    if exists.casefold().rstrip(".") == "none" or REUSES.search(text):
        return None
    return (
        f"{found[0]}'s research found what already does part of this: {exists}. A READY says, in a REUSES: line, how the"
        " PR calls or extends it rather than adding a second copy beside it"
    )


def reader_refusal(job_id: str, job: dict, order: dict, text: str, lines: list[str]) -> str | None:
    """Why a reader's return may not be kept: research for a build order carries its filled Researched: block, and a DESIGN is a choice for him of two to four options, one recommended, among them the fix its evidence points at."""
    block = researched(text)
    if order["kind"] == "build" and not order["incident"] and job["goal"].casefold().startswith("research") and (
        missing := [f"{key}:" for key in KEYS if not block.get(key)]
    ):
        return (
            f"{job_id} researches a build order, so its report carries {BLOCK}, each filled or none, which its builder's"
            f" brief names; it lacks {', '.join(missing)}"
        )
    if not lines[0].startswith("DESIGN"):
        return None
    inside = set(block_lines(text))
    offered = [line.removeprefix("- ").strip() for line in lines if line.startswith("- ") and line not in inside]
    marked = [option for option in offered if limits.RECOMMENDED.search(option)]
    if not 2 <= len(offered) <= 4 or len(marked) != 1:
        return (
            "a reader's DESIGN return is a choice for him, which a design lane turns into his cards: two to four `- `"
            " option lines, exactly one ending (recommended); a finding with no choice in it returns DONE"
        )
    options = [limits.RECOMMENDED.sub("", option) for option in offered]
    summary = [line for line in lines if not line.startswith("- ") and not limits.DIRECT_FIX.match(line)]
    if problems := limits.direct_fix_problems(text, options, limits.RECOMMENDED.sub("", marked[0]), summary):
        return f"a reader's DESIGN names among its options the fix its evidence points at: {problems[0]}"
    return None


def design_missing(lines: list[str]) -> list[str]:
    missing = [] if DESIGN_PART.match(lines[0]) else ["a first line DESIGN <the part>"]
    return missing + [f"a {label} line" for label in DESIGN_LINES if not any(line.startswith(label) for line in lines)]


def limit_finding(lines: list[str]) -> str | None:
    """The first line naming a limit beside a number, in one clause, as a finding left standing: left as is, unchanged, hit or exceeded, a clause ruling that out, as "never hit" does, aside."""
    return next((line for line in lines if limited(line) and left_standing(line)), None)


def limited(line: str) -> bool:
    return any(LIMIT.search(part) and re.search(r"\d", part) for part in limits.CLAUSE_BREAK.split(line))


def left_standing(line: str) -> bool:
    return any(not limits.NEGATION.search(limits.CLAUSE_END.split(line[: found.start()])[-1]) for found in LEFT_STANDING.finditer(line))


def left_steps(text: str) -> str | None:
    """The first NEXT: or UNPROVEN: line that leaves a step for another session to run."""
    return next((line.strip() for line in LEFT.findall(text) if left_for(line)), None)


def left_for(line: str) -> bool:
    """Whether the line leaves a step for another session to run, a step its clause rules out, as "no coordinator runs it" does, aside."""
    return any(not limits.NEGATION.search(limits.CLAUSE_END.split(line[: found.start()])[-1]) for found in LEFT_FOR.finditer(line))


def untappable(first: str) -> str:
    """Why the tap cards of the drive PRs a READY line names, those waiting on his tap, would be refused, each stack they form checked on its own as drive card groups them, or "" when each draws."""
    known = card.drive_prs()
    keys = [key for key in dict.fromkeys(state.pr_key(found[0]) for found in state.PR_URL.finditer(first)) if key in known and known[key][1]["wants_tap"]]
    reads = card.read_all(keys)
    return "; ".join(reason for chain in card.chains(keys, reads) if (reason := card.tap_refusal(known, chain, reads)))


def unproven(text: str) -> str | None:
    """Why a builder's DONE may not be kept: its CHECK: line names no proof that exists, or, for what he sees, no recording, picture or live post of it."""
    proofs = [found.rstrip(".") for line in CHECK.findall(text) for found in PROOF.findall(line)]
    real = [proof for proof in proofs if proof.startswith("http") or Path(proof).expanduser().exists()]
    if not real:
        return (
            "a DONE names its proof on a CHECK: line, a link or the absolute path of a file that exists, such as the"
            " live post, the recording of the click, or the saved output of the installed command"
        )
    native = NATIVE.search(text)
    if native is None or native[1].casefold().startswith("none") or any(SEEN_PROOF.search(proof) for proof in real):
        return None
    return (
        "this builds something he sees, by its NATIVE: line, so its proof is from that surface: record the click through"
        " it (agent-browser's recording, or screencapture -v of the window) or link the live post, and name the file or"
        " link on CHECK:; data fetched from an API or a command's output does not show what he sees"
    )
