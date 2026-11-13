import re
import sys
from collections.abc import Iterator
from pathlib import Path

from drive import limits, settled, state
from drive.render import cut
from drive.shell import simple_commands

BRIEF_JOB = re.compile(r"^Drive:[^\n]*?\bjob (j\d+)\b", re.MULTILINE)
BRIEF_FILE = re.compile(r"^Read your brief first:[ \t]*(\S+)|(/\S+/orca/tasks/[^/\s]+/brief\.md)\b", re.MULTILINE)
HANDOFF = re.compile(r"^Handoff:", re.MULTILINE)
SERVES_TOOLING = re.compile(r"^Serves:[ \t]*drive tooling\b", re.MULTILINE | re.IGNORECASE)
PLANNED = re.compile(r"^(Goal|Done when|Where|Runs as|Native|Interactions|Would he ask|Plan|Incident):[ \t]*(.+)$", re.MULTILINE)
FIELD = re.compile(
    r"^(Order \(his words\)|Serves|Goal|Done when|Where|Runs as|Release|Mac|Handoff|Decisions that bind|Research|Researched|Answer"
    r"|Native|Interactions|Would he ask|Plan|Incident|Scratch|Transcripts|Drive):",
    re.MULTILINE,
)
RESEARCHED = re.compile(r"^Researched:[ \t]*$", re.MULTILINE)
RESEARCHED_LINE = re.compile(r"^Researched:", re.MULTILINE)
RESEARCHED_BY = re.compile(r"^Researched:[ \t]*(j\d+)\b", re.MULTILINE)
REUSES = re.compile(r"^REUSES:[ \t]*\S", re.MULTILINE)
CHECK = re.compile(r"^CHECK:(.*)$", re.MULTILINE)
NATIVE = re.compile(r"^NATIVE:[ \t]*(.*)$", re.MULTILINE)
PROOF = re.compile(r"https?://\S+|(?<![\w.])/[^\s,;)]+")
SEEN_PROOF = re.compile(r"slack\.com/archives/|\.(?:mov|mp4|webm|gif|png|jpe?g)$", re.IGNORECASE)
SEEN = "a picture or recording of it, or the live post's link its proof file reads back"
NO_SURFACE = re.compile(r"\s*none\b", re.IGNORECASE)
STORIES_RULING = (
    '"you\'re supposed to proactively view and click through every user story and have strict acceptance critera for yourself and'
    ' instad are just doing the bare minumum based on what I say"'
)
STORY_QUESTION = (
    'Is "{story}" a user story on the surface below: what he does there, such as a click, key or hover, and what he must then see?'
    " Pick none when it says both. Pick steps when it lists steps or names a part to walk with no result he must see, such as"
    ' "open the run page, click a step" or "walk each part the earlier brief names".'
)
STORY_OPTIONS = {"none": "It says what he does and what he must then see.", "steps": "It lists steps or a part to walk, with no result he must see."}
PROOF_QUESTION = (
    'Does the evidence below prove the user story "{story}" on the installed surface? Pick none when the proof file states strict'
    " acceptance criteria for it, what he sees first, the result of each click, key or hover, and its empty and error states, and"
    f" asserts each on the installed surface, and the check names {SEEN}. Pick bare when it only opens a page"
    " or checks that something drew, states no criteria for this story, or names none of these."
)
PROOF_OPTIONS = {"none": "Its criteria are stated, asserted on the installed surface and seen in a picture, recording or live post.", "bare": "It stops at the bare minimum for this story."}
CRITERIA_CHARS = 6000
REPRO = re.compile(r"^REPRO:[ \t]*\S", re.MULTILINE)
LEFT = re.compile(r"^(?:NEXT|UNPROVEN):.*$", re.MULTILINE)
STEPS = r"(?:runs?|re-?runs?|fills?|back-?fills?|migrates?|applies|apply|executes?|triggers?|kicks?\s+off|flips?|enables?|patch(?:es)?|repairs?)"
LEFT_FOR = re.compile(
    r"\b(?:until|unless|once|after|when)\s+(?:the\s+)?(?:coordinator|communicator|meta\s+session|handler|owner|someone|another\s+(?:lane|agent|job))\b"
    rf"|\b(?:coordinator|communicator|meta\s+session|handler|someone(?:\s+else)?)\s+(?:must\s+|should\s+|needs?\s+to\s+|has\s+to\s+|will\s+|then\s+)?{STEPS}\b",
    re.IGNORECASE,
)
LIMIT = re.compile(r"\b(?:caps?|capped|quotas?|capacity|ceilings?|throttl\w*|rate[- ]limit\w*|limit\w*|max(?:imum)?)\b", re.IGNORECASE)
LEFT_STANDING = re.compile(r"\b(?:left\s+as[\s-]is|unchanged|hit(?:s|ting)?|exceed(?:s|ed|ing)?)\b", re.IGNORECASE)
STATEMENT = re.compile(r"(?:(?![.;:!?]+(?=\s|$)).)+")
CHOICE_OPENINGS = ("ASK", "DESIGN")
READY_OPENINGS = ("READY", "PUSHED")
DESIGN_PART = re.compile(r"DESIGN\s+\S")
KEYS = ("rulings", "exists", "open")
RESEARCHED_KINDS = ("builder", "design", "codex")
FOLLOW_UP_RULING = '"start deisgnng the follow p right awya motherfucker dont wait"'
LANE_KINDS = ("reader", "builder", "design")
LIGHT_MODELS = ("sonnet", "haiku")
OPENINGS = ("DONE", "READY", "PUSHED", "BUILT", "WAITING", "ASK", "BLOCKED", "DESIGN", "HANDOFF")
WORDED_KINDS = ("builder", "design")
DESIGN_LINES = ("Root cause:", "Settled:", "Native:", "Exists:", "Change:", "Facts:")
EXISTS = re.compile(r"^[ \t]*Exists:[ \t]*(.*)$", re.MULTILINE)
CITED = re.compile(r"[\w.~-]*[/.][\w./-]*:\d+|https?://\S+")
OWNED = re.compile(r"\b(?:extended|reused|deleted) by change (\d+)\b", re.IGNORECASE)
SEARCHED = re.compile(r"\b(?:ccx|rg|grep|ccn|answer_search|search_plan)\b")
DUPLICATION_RULING = '"how _agents was allowed to get creted in the irst place sonce its a terieble duplicatgion and was clerarly not throught through"'
UNDESIGNED = ("HANDOFF", "BLOCKED")
BLOCK = "a Researched: line and under it rulings:, exists: and open: lines"
CHOICE_WORDS = r"\b(?:options?|choices?|choose|lost|picks?|decisions?|forks?)\b"
STOPS_ON_CHOICE = re.compile(rf"[^.;]*(?:(?<!never )\bBLOCKED\b[^.;]*{CHOICE_WORDS}|{CHOICE_WORDS}[^.;]*(?<!never )\bBLOCKED\b)[^.;]*")
STOPPED_RULING = '"fix ur tooling if it stopped on that it shoukd. have brought a design question card to me asap not just stopped int eh middle of the incident"'
RELEASE = re.compile(r"^Release:[ \t]*(.+)$", re.MULTILINE)
MAC = re.compile(r"^Mac:[ \t]*(.+)$", re.MULTILINE)
INCIDENT = re.compile(r"^Incident:", re.MULTILINE)
CI_BINARIES = frozenset({"ci", "ci.dev"})
RELEASE_STARTS = ("start", "hotfix")
RELEASE_VALUED = frozenset({"groups", "targets", "hotfix", "landed", "exclude", "only", "without", "env", "from", "order"})
RELEASE_ASIDE = frozenset({"json"})
PLAN_ASIDE = frozenset({"order"})
COMMIT_PICKS = frozenset({"from", "green"})
RELEASE_APPROVAL = (
    "A release starts only on words of his ordering it, whatever card or thread they answer, or on an incident's order, the"
    " answer labelled incident, whose words approve the incident's own cut. Name in `--order` the answer `drive answer`"
    " recorded for those words, or ask him with `drive job ask` whether to release"
)
PULL_WORDS = re.compile(r"(?:#?\d+,?)+")
NAMES = re.compile(r"[,\s]+")
ABSTRACTION_RULING = '"we should buiold the abstraction first, make sure it works by imaginign, then implement the strategies"'
NARROWS_QUESTION = (
    "Does this design build less than these words of his name, or build part of it later, with no fork below asking him about it:"
    " does it leave out, defer to a later change or trigger, or narrow something his words or answers name, such as building one"
    " of several strategies he approved while the others wait, or deciding such a cut on its own because it is cheap to reverse?"
    " Pick the item whose words it narrows, or none when it builds all they name or a fork below asks him about what it leaves out."
)
NARROWS_OPTIONS = {settled.UNDECIDED: "It builds all his words name, or a fork asks him about what it leaves out."}
SHARED_QUESTION = (
    "Do his words or this design name several strategies for one job, such as the partition strategies a table picks by its usage,"
    " while the design's first change is not the one abstraction every strategy goes through, walked through each strategy on"
    " paper, with each strategy's change waiting on it? Pick first when its first change builds one strategy, or routes each"
    " strategy as its own case, before their shared abstraction; none when it names no such strategies or its first change is"
    " that abstraction."
)
SHARED_OPTIONS = {settled.UNDECIDED: "It names no such strategies, or its first change is their shared abstraction.", "first": "A strategy comes before their shared abstraction."}


def carried(prompt: str) -> str:
    """An agent's prompt with its brief file read in after it: the one its `Read your brief first:` line names, wherever it is kept, or the Orca task brief a Sprite lane's first message names."""
    named = BRIEF_FILE.search(prompt)
    return f"{prompt}\n{Path(named[1] or named[2]).expanduser().read_text()}" if named else prompt


def fields(text: str) -> list[tuple[str, str]]:
    """Each field of a brief drive job brief wrote, its label and its text up to the next field's label."""
    starts = list(FIELD.finditer(text))
    ends = [found.start() for found in starts[1:]] + [len(text)]
    return [(found[1], text[found.end() : end]) for found, end in zip(starts, ends, strict=True)]


def release_flags(words: list[str]) -> dict[str, tuple[str, ...]] | None:
    """What a `ci release start`, `hotfix` or `plan` ships and from where, as `ci release` parses its words, a hotfix's pull requests as its --hotfix and bare targets as --targets; None for any other command."""
    if Path(words[0]).name not in CI_BINARIES or words[1:2] != ["release"] or words[2:3] not in (["start"], ["hotfix"], ["plan"]):
        return None
    flags: dict[str, list[str]] = {}
    rest = iter(words[3:])
    for word in rest:
        name, given, value = word.removeprefix("--").partition("=")
        if not word.startswith("--"):
            name, value = "hotfix" if words[2] == "hotfix" and PULL_WORDS.fullmatch(word) else "targets", word
        elif not given:
            value = next(rest, "") if name in RELEASE_VALUED else "true"
        flags.setdefault("targets" if name == "groups" else name, []).extend(filter(None, NAMES.split(value.replace("#", ""))))
    return {name: tuple(sorted(values)) for name, values in flags.items() if name not in RELEASE_ASIDE}


def release_scope(flags: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    """What a release's flags ship, to which envs and on which answer of his, leaving out the commit it picks."""
    return {name: values for name, values in flags.items() if name not in COMMIT_PICKS}


def plan_flags(flags: dict[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
    """A release's flags as the plan before its start is matched on them, leaving out the --order its start names."""
    return {name: values for name, values in flags.items() if name not in PLAN_ASIDE}


def started(command: str) -> list[dict[str, tuple[str, ...]]]:
    """The flags of each `ci release start` or `hotfix` a command runs."""
    return [flags for words in simple_commands(command) if words[2:3] and words[2] in RELEASE_STARTS and (flags := release_flags(words)) is not None]


def release_refusal(command: str) -> str | None:
    """Why a brief's Release line names no release a job may start: it is not one `ci release start` or `hotfix`."""
    named = started(command)
    if len(named) != 1:
        return f"--release names {len(named)} releases: name the one `tools/ci release start` or `tools/ci release hotfix` the job may run, with its flags as it runs them"
    return None


def approval_refusal(command: str, cwd: str) -> str | None:
    """Why the one release a command starts may not start on the answer of his its --order names: it is no incident's order, and Jev does not read it as his order or pick to release, or gave no answer; None for a release naming no --order."""
    [flags] = started(command)
    if not (orders := flags.get("order")):
        return None
    answer_id = orders[-1]
    try:
        answer = settled.shown(answer_id, cwd)
        if settled.INCIDENT in answer["tags"] or settled.approving(f"start this production release: `{command}`", {"id": answer_id, "asked": answer["title"], "said": answer["body"]}):
            return None
    except state.DriveError as error:
        return f"refused: `--order {answer_id}` names no answer of his that could be read: {error}. {RELEASE_APPROVAL}"
    except settled.Unheard as failure:
        return f"refused: Jev did not say whether his answer {answer_id} approves this release ({failure}); run it again once Jev answers. {RELEASE_APPROVAL}"
    asked, said = cut(answer["title"], 200), cut(answer["body"], settled.QUOTED_CHARS)
    return f'refused: `--order {answer_id}` names his answer to "{asked}", "{said}", and Jev does not read it as approving this release. {RELEASE_APPROVAL}'


def gives_tooling(job_id: str, order_id: str) -> str:
    """The verb that gives an order to the meta session: a brief whose builder serves drive tooling, which only the meta session starts."""
    return f'`drive job brief {job_id} --serves "drive tooling"` gives {order_id} to the meta session'


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


def researches(name: str, data: dict, order_id: str, parts: list[str]) -> Iterator[tuple[str, str]]:
    """Each reader of these parts of the order whose kept report carries a filled Researched: block, newest first, with that report."""
    for job_id in reversed(readers(data, order_id, parts)):
        if filled(researched(text := state.report_text(name, job_id))):
            yield job_id, text


def research(name: str, data: dict, order_id: str, parts: list[str]) -> tuple[str, str] | None:
    """The newest reader of these parts of the order whose kept report carries a filled Researched: block, with that report."""
    return next(researches(name, data, order_id, parts), None)


def bound(name: str, data: dict, job: dict) -> tuple[list[str], list[str]]:
    """What binds the job beside his words on its orders: the id of each answer of his its brief's rulings name, each answer of his the rulings: line of every reader of its parts of its order and of each order it carries names by id, since a newer reader on another question of the same part holds none of an older one's, read as served.named reads a citation whatever words and punctuation surround it, each id cc-notes keeps no answer under, such as a commit or a session, left out, and each that settled a question of such a reader; then his words on each other question of theirs he answered with no answer recorded."""
    from drive import refs

    ruled, cited, heard = binding(name, data, job)
    return list(dict.fromkeys([*ruled, *(answer_id for answer_id in cited if refs.answered(answer_id, data))])), heard


def binding(name: str, data: dict, job: dict) -> tuple[list[str], list[str], list[str]]:
    """What bound reads before it asks cc-notes which ids keep an answer, so a watch pass reads it with no subprocess: the ids the job's brief's rulings and its readers' settled questions name, the ids their rulings: lines cite, and his words on each question of theirs he answered with no answer recorded."""
    from drive import served

    found = [held for order_id in [job["order"], *job["carries"]] for held in researches(name, data, order_id, state.job_parts(job, order_id))]
    closed = [answer for reader_id, _ in found for question in data["jobs"][reader_id]["questions"] if (answer := question["closed"]) and answer["how"] != "withdrawn"]
    cited = list(dict.fromkeys(answer_id for _, text in found for kind, answer_id in served.named(researched(text)["rulings"]) if kind == "answer"))
    ruled = [*((job.get("brief_flags") or {}).get("ruling") or []), *(answer["ruling"] for answer in closed if answer["ruling"])]
    heard = [f'his answer "{answer["words"]}"' + (f" ({answer['link']})" if answer["link"] else "") for answer in closed if not answer["ruling"] and answer["words"]]
    return ruled, cited, list(dict.fromkeys(heard))


def readers(data: dict, order_id: str, parts: list[str]) -> list[str]:
    """The order's readers, oldest first, that read one of these parts, a change a design listed counting as that design's own parts."""
    order = data["orders"][order_id]
    designs = {held["design"] for part, held in order["parts"].items() if part in parts and held["design"]}
    read = {*parts, *(part for design in designs for part in data["jobs"][design]["parts"])}
    return [job_id for job_id in order["jobs"] if (job := data["jobs"][job_id])["kind"] == "reader" and job["order"] == order_id and read & set(job["parts"])]


def waits_on_research(order: dict, kind: str, parts: list[str], lands: bool = False) -> bool:
    """Whether a job of this kind on these parts starts only on its reader's research: a build order's builder, designer or codex lane, but on an incident, on the immediate fix, as a builder landing PRs he approved, or as a follow-up's design, each of which starts at once."""
    return (
        order["kind"] == "build"
        and not order["incident"]
        and kind in RESEARCHED_KINDS
        and not (state.IMMEDIATE_FIX in parts or lands)
        and not (kind == "design" and state.FOLLOW_UP in parts)
    )


def job_refusal(name: str, data: dict, order_id: str, kind: str, parts: list[str], lands: bool = False, waits: list[str] | None = None) -> str | None:
    """Why a job of this kind may not start on the order: an order he limited to options gets no builder or codex job until he picks, an answer order is one reader's, and a build order's builder, designer or codex lane waits on the research of a reader of its own part, a change a design listed counting as that design's part, added at once waiting on such a reader still at work when --waits names it, so no part sits in no job while it reads,. A job of any kind may wait on his card, a drive PR or a release, which check_waits reads, and on any job still at work, a reader that just started included, which state.job_wait_refusal reads, stored as that job's newest open PR, each PR it opens taking the wait's place. The immediate fix of a bug hurting someone now, a red default branch's fix-forward among them, and a builder landing PRs he approved start at once past each of those, by his words: "always do the immediate fix (or rollback if prudent) in parallel to giving me options for the proper fix". A follow-up he orders to a PR gets its design job at once, doing its own research and waiting on nothing, beside landing that PR, by his words "start deisgnng the follow p right awya motherfucker dont wait"."""
    order = data["orders"][order_id]
    at_once = state.IMMEDIATE_FIX in parts or lands
    listed = " ".join(f'"{part}"' for part in dict.fromkeys([*order["parts"], state.IMMEDIATE_FIX]))
    immediate = (
        f'the immediate fix of a bug hurting someone now, or a red default branch\'s fix-forward, starts at once on its own "{state.IMMEDIATE_FIX}"'
        f" part, added with drive order set {order_id} --parts {listed}, and a builder landing PRs he approved with --lands <each PR>"
    )
    if order["options_only"] and kind in ("builder", "codex") and not at_once:
        return (
            f"{order_id} is limited to options: he picks one before the proper fix is built, so no {kind} starts on it; start a reader or a designer"
            f" until he picks, drive job add design --order {order_id}, and once he picks, drive order set {order_id} --no-options-only; {immediate}"
        )
    if order["kind"] == "answer" and kind != "reader":
        return (
            f"{order_id} is an answer order: one reader researches it and answers him, with no {kind} beside it; work its"
            f" answer finds to build is a build order: drive order set {order_id} --kind build"
        )
    if kind == "design" and state.FOLLOW_UP in parts and waits:
        return (
            f'--waits {", ".join(waits)}: a "{state.FOLLOW_UP}" design starts now, beside landing the PR it follows, never once that PR'
            f" lands or a reader returns, by his words {FOLLOW_UP_RULING}: add it with no --waits, and its designer does its own research"
        )
    if not waits_on_research(order, kind, parts, lands):
        return None
    if research(name, data, order_id, parts) is None:
        reading = [job_id for job_id in readers(data, order_id, parts) if data["jobs"][job_id]["state"] not in state.FINISHED_JOB_STATES]
        if set(reading) & set(waits or []):
            return None
        meanwhile = (
            f"; while {reading[0]} reads, add this {kind} now waiting on it with --waits {reading[0]}, so the part is never in no job,"
            " and start its agent on the watch's FREE line once the reader returns"
            if reading
            else ""
        )
        named = " ".join(f'--part "{part}"' for part in parts)
        return (
            f"{order_id} is a build order, so its {kind} starts only once an Opus reader of its part, a change a design listed counting"
            f" as that design's part, has kept {BLOCK} in its report: drive job add reader --order {order_id} {named}"
            ' --goal "research: <what>", then a long-running:reader whose brief'
            " asks for that block; its rulings line holds his recorded answers on the order's words and area, its exists"
            f" line what already does part of it, and its open line each question the order implies{meanwhile}; {immediate}"
        )
    return None


def brief_refusal(name: str, data: dict, job_id: str, brief: str) -> str | None:
    """Why this brief may not start its job's agent: the lines its writer plans the job with ask him for a sign-in, Touch ID or cc-sudo on an AWS action this Mac's machine profile runs, a drive tooling brief aside, whose lines name the sign-ins the drive's checks refuse; an incident's codex lane or builder needs its kind's rule on a choice, by incident_refusal; a build order's builder, designer or codex lane needs the Researched: line naming a reader of its own part, unless it ships the immediate fix or designs a follow-up he ordered, which starts at once doing its own research, and an answer order's one lane does its own research."""
    if not SERVES_TOOLING.search(brief) and (asked := limits.sign_in_problems([(f"its {found[1]} line", found[2]) for found in PLANNED.finditer(brief)])):
        return f"{job_id}'s brief plans a sign-in of his: {asked[0]}"
    job = data["jobs"][job_id]
    order_id = job["order"]
    order = data["orders"][order_id]
    if order["incident"] and (rule := state.INCIDENT_CHOICE.get(job["kind"])):
        return incident_refusal(job_id, order_id, brief, rule)
    if HANDOFF.search(brief) or order["incident"] or not order["kind"] or state.IMMEDIATE_FIX in job["parts"]:
        return None
    if job["kind"] == "design" and state.FOLLOW_UP in job["parts"]:
        return None
    if order["kind"] == "answer":
        if RESEARCHED_LINE.search(brief):
            return (
                f"{job_id} answers {order_id}, an answer order: its one lane does its own research, reading code,"
                " transcripts, records and his answers itself, so its brief carries no Researched: line"
            )
        return None
    if job["kind"] not in RESEARCHED_KINDS:
        return None
    named = RESEARCHED_BY.search(brief)
    if named is None:
        return (
            f"{job_id} builds on {order_id}, a build order: its brief carries the line Researched: <its reader's job>,"
            " naming the reader whose kept report holds the order's rulings, what exists and its open questions"
        )
    if not (named[1] in readers(data, order_id, job["parts"]) and filled(researched(state.report_text(name, named[1])))):
        return f"Researched: {named[1]} names no reader of {job_id}'s part of {order_id} that kept {BLOCK} in its report"
    return None


def incident_refusal(job_id: str, order_id: str, brief: str, rule: str) -> str | None:
    """Why an incident lane's brief may not start it: a choice never stops an incident, so its brief carries the rule its kind follows on one, word for word, and no line of it orders a BLOCKED return on a loss or a choice."""
    if rule not in " ".join(brief.split()):
        return f'{job_id} works on {order_id}, an incident, which a choice never stops, by his words {STOPPED_RULING}: its brief\'s Incident: line carries, word for word, "{rule}"'
    if stops := next((found for line in brief.splitlines() if (found := STOPS_ON_CHOICE.search(" ".join(line.split())))), None):
        return f'{job_id}\'s brief stops it on a choice, "{cut(stops[0].strip(), 160)}", and an incident never stops on one, by his words {STOPPED_RULING}: drop that line, since its Incident: line says what it does instead'
    return None


def heavier(data: dict, job_id: str, brief: str, model: str | None) -> bool:
    """Whether a Sonnet or Haiku passed for the job's lane gives way to its agent definition's own model: a build order's reader, builder and designer run on Opus or Fable, and so does an answer order's lane when the communicator marked the order complex."""
    job = data["jobs"][job_id]
    order = data["orders"][job["order"]]
    if HANDOFF.search(brief) or order["incident"] or not order["kind"] or job["kind"] not in LANE_KINDS or not any(word in (model or "") for word in LIGHT_MODELS):
        return False
    if order["kind"] != "answer":
        return True
    return bool(order["complex"])


def report_refusal(name: str, data: dict, job_id: str, text: str) -> str | None:
    """Why a job's return may not be kept: a builder's or designer's opens with a result word, a REPRO: file is kept on the job first, a report naming a limit in its own words as a finding left standing is an ASK or DESIGN, a design lane's is the design in its shape, building all his words name and the abstraction several strategies share before any of them, by narrowed_design, a research reader's carries its Researched: block, a reader's DESIGN is a choice for him with its options, starting from the root cause and recommending the option that removes it, a DONE, READY or PUSHED leaves no step for someone else, a builder's DONE names its proof, one picture, recording or live post of each user story its brief names when he sees the result, and a builder's READY or PUSHED names PRs whose tap card draws, saying how his words are met on each reworked to them, and says how it builds on what its research found."""
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
            f'it reports a limit as a finding left standing: "{finding}". A limit or capacity finding is his'
            " choice, so return ASK or DESIGN with two to four options for it, one recommended, in place of this report"
        )
    if job["kind"] == "design" and opening not in UNDESIGNED and (missing := design_missing(lines)):
        return (
            "a design lane's report is the design in its shape, which the coordinator reads for its forks, and this one"
            f" lacks {', '.join(missing)}: DESIGN <the part, in a few words>, then Root cause: (the structure that lets the"
            " defect happen, or none: <why no defect is behind it>), Settled:, Native:, Exists:, a Change: line per PR its"
            " builders open and Facts:"
        )
    if job["kind"] == "design" and opening == "DESIGN" and (unowned := unowned_exists(name, data, job["order"], job["parts"], text)):
        return unowned
    if job["kind"] == "design" and opening == "DESIGN" and (unkeyed := unquotable_change(text)):
        return (
            f'its Change: line names its change as "{cut(unkeyed, 120)}": a change is named in at most {state.PART_CHARS} characters'
            " with no backtick, double quote, $ or backslash, since those words become the order's part, which the coordinator"
            ' types as drive job add builder --part "<the change>"; its detail goes on the Design: line'
        )
    if job["kind"] == "design" and opening == "DESIGN" and (stray := unlisted_waits(text)):
        return (
            f'its Change: line "{cut(stray, 80)}" waits on a change its list does not hold: a change waits only on earlier changes'
            " of its list, as (waits on change <its number, counting Change: lines from 1>) or (waits on change 1 and change 2)"
        )
    if job["kind"] == "design" and opening == "DESIGN" and (narrowed := narrowed_design(name, data, job, text)):
        return narrowed
    if job["kind"] == "reader" and (refused := reader_refusal(data, job_id, job, order, text, lines)):
        return refused
    if opening in ("DONE", *READY_OPENINGS) and (left := left_steps(text)):
        return (
            f'a {opening} leaves a step for someone else: "{cut(left, 120)}". Do that step yourself before you return,'
            " or return ASK when it needs his choice"
        )
    if job["kind"] == "builder" and opening == "DONE":
        return unproven(text, job_id, job)
    if job["kind"] == "builder" and opening in READY_OPENINGS and not state.REPORT_CHANGE.search(text):
        if reworked := [key for key in state.named_prs(data, job["order"], lines[0]) if data["prs"][key]["rework"]]:
            return f"{', '.join(reworked)}: being reworked to his words, so its {opening} says in a CHANGE: line how they are met, which its fresh card shows him"
    if not (job["kind"] == "builder" and opening in READY_OPENINGS and order["kind"] == "build") or order["incident"]:
        return None
    found = research(name, data, job["order"], job["parts"])
    exists = researched(found[1])["exists"] if found else "none"
    if exists.casefold().rstrip(".") == "none" or REUSES.search(text):
        return None
    return (
        f"{found[0]}'s research found what already does part of this: {exists}. A {opening} says, in a REUSES: line, how the"
        " PR calls or extends it rather than adding a second copy beside it"
    )


def reader_refusal(data: dict, job_id: str, job: dict, order: dict, text: str, lines: list[str]) -> str | None:
    """Why a reader's return may not be kept: research for a build order carries its filled Researched: block, and a DESIGN is a choice for him of two to four options, one recommended, starting from the root cause its evidence finds and offering the option that removes it, and on a bug hurting someone now names the immediate fix shipping while he picks."""
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
            "a reader's DESIGN return is a choice for him, kept as his card as it stands: two to four `- `"
            " option lines, exactly one ending (recommended); a finding with no choice in it returns DONE"
        )
    options = [limits.RECOMMENDED.sub("", option) for option in offered]
    if problems := limits.root_cause_problems(text, options):
        return f"a reader's DESIGN names its simplest option, starts from the root cause and names the option that removes it: {problems[0]}"
    if state.hurts_now(data, order) and (problems := limits.interim_fix_problems(text)):
        return f"a reader's DESIGN on a bug hurting someone now names its immediate fix: {problems[0]}"
    return None


def design_missing(lines: list[str]) -> list[str]:
    missing = [] if DESIGN_PART.match(lines[0]) else ["a first line DESIGN <the part>"]
    return missing + [f"{'an' if label[0] in 'AEIOU' else 'a'} {label} line" for label in DESIGN_LINES if not any(line.startswith(label) for line in lines)]


def unowned_exists(name: str, data: dict, order_id: str, parts: list[str], text: str) -> str | None:
    """Why a design's Exists: line does not show what already does or holds what it touches, so it would add a second owner of one fact beside the first: an entry citing no path:line or link, or not naming the change of its list that extends, reuses or deletes it; or none, naming no search that found nothing, or while a reader of its part found something."""
    listed = EXISTS.search(text)[1].strip()
    shape = (
        "a design names, before it adds any table, column, store, file, process, mechanism, check or verb, each thing that"
        " already does or holds what it touches, one entry each separated by ;, citing its path:line or link and ending"
        " extended by change N, reused by change N or deleted by change N, N counting its Change: lines from 1, so one thing"
        f' owns each fact; or none: <each search that found nothing, such as ccx code search "<words>">. His words: {DUPLICATION_RULING}'
    )
    if listed.casefold().startswith("none"):
        found = research(name, data, order_id, parts)
        exists = researched(found[1])["exists"] if found else "none"
        if found and exists.casefold().rstrip(".") != "none":
            return f'its Exists: line says none, but {found[0]}\'s research found what already does part of this: "{cut(exists, 200)}"; {shape}'
        return None if SEARCHED.search(listed) else f'its Exists: line "{cut(listed, 120)}" names no search that found nothing; {shape}'
    changes = len(state.CHANGE_LINE.findall(text))
    entries = [entry.strip() for entry in listed.split(";") if entry.strip()]
    unowned = next((entry for entry in entries if not CITED.search(entry) or not (owned := OWNED.findall(entry)) or any(not 1 <= int(n) <= changes for n in owned)), None)
    return unowned and f'its Exists: entry "{cut(unowned, 120)}" does not cite what exists and which change of its list extends, reuses or deletes it; {shape}'


def unquotable_change(text: str) -> str | None:
    """The first change a Change: line names in words that cannot be its part's name: longer than a part's name, or holding a character double quotes do not keep as typed."""
    names = (" ".join(line["change"].split()) for line in state.CHANGE_LINE.finditer(text))
    return next((name for name in names if len(name) > state.PART_CHARS or re.search(r'[`"$\\]', name)), None)


def unlisted_waits(text: str) -> str | None:
    """The first Change: line waiting on a change that is not an earlier one of the list."""
    found = list(state.CHANGE_LINE.finditer(text))
    return next((line[0] for number, line in enumerate(found, start=1) if any(not 1 <= wait < number for wait in state.change_waits(line))), None)


def narrowed_design(name: str, data: dict, job: dict, text: str) -> str | None:
    """Why a design may not be kept, as Jev reads it in one call against his words on its order and each order its job carries, each answer of his binding the job and his answer to each of its cards, beside the forks it asks him, at least settled.DECIDES sure: it leaves out, defers or narrows what they name with no fork asking him, or, where several strategies do one job, builds one before the abstraction they share. Jev giving no reading keeps it, said on stderr."""
    cwd = data["roles"]["coordinator"]["cwd"]
    ids, _ = bound(name, data, job)
    words = [
        *(word for order_id in [job["order"], *job["carries"]] for word in settled.order_words(data, order_id)),
        *({"id": answer["id"][:7], "asked": answer["title"], "said": settled.his(answer["body"])[: settled.BODY_CHARS]} for answer in (settled.shown(answer_id, cwd) for answer_id in ids)),
        *(
            {"id": f"q{number}", "asked": question["title"], "said": closed["words"]}
            for number, question in enumerate(job["questions"], 1)
            if (closed := question["closed"]) and closed["how"] == "answered" and closed["words"]
        ),
    ]
    items = {word["id"]: word for word in words}
    forks = [question["title"] for question in job["questions"] if not question["closed"]]
    questions = {
        "shared": {"label": {"instructions": SHARED_QUESTION, "options": SHARED_OPTIONS}},
        **({"narrows": {"label": {"instructions": NARROWS_QUESTION, "options": NARROWS_OPTIONS | dict.fromkeys(items)}}} if items else {}),
    }
    try:
        read = settled.jev({"state": {"design": text, "forks": forks, "his_words": words}, "questions": questions})
    except settled.Unheard as failure:
        print(f"drive job report: Jev did not read the design against his words, so it is kept unchecked: {failure}", file=sys.stderr)
        return None
    narrows, shared = read.get("narrows"), read["shared"]
    if narrows and narrows["choice"] in items and narrows["probabilities"][narrows["choice"]] >= settled.DECIDES:
        said = items[narrows["choice"]]
        return (
            f'Jev reads this design as building less than his words "{cut(said["said"].strip('"“”'), settled.QUOTED_CHARS)}" ({said["asked"]}) name, or later, with no fork asking him:'
            " building less than his words or an answer of his name is a fork whatever it costs to switch, so build it in, or keep it as a"
            " fork with drive job ask"
        )
    if shared["choice"] == "first" and shared["probabilities"]["first"] >= settled.DECIDES:
        return (
            "Jev reads this design as building a strategy before the one abstraction its strategies share: design that abstraction first,"
            " walk every strategy through it on paper with a worked example of each operation, make it change 1 and each strategy a later"
            f" Change: line waiting on it, by his words {ABSTRACTION_RULING}"
        )
    return None


def limit_finding(lines: list[str]) -> str | None:
    """The first statement, as written, naming a limit beside a number, in one clause, as a finding left standing: left as is, unchanged, hit or exceeded, a word ruling that out just before it, as "never hit" does, aside. Only the report's own words count, so a quote, code, file path, identifier, link or URL it cites as evidence reads as blank, as limits.py names no limit, except on an UNPROVEN: line, where a quoted refusal is the reason it gives for leaving the step unproven."""
    for line in lines:
        for found in STATEMENT.finditer(limits.masked(line)):
            statement = line[found.start() : found.end()]
            read = statement if line.startswith("UNPROVEN:") else found[0]
            if limited(read) and left_standing(read):
                return statement.strip()
    return None


def limited(statement: str) -> bool:
    return any(LIMIT.search(part) and re.search(r"\d", part) for part in limits.CLAUSE_BREAK.split(statement))


def left_standing(statement: str) -> bool:
    return any(not limits.RULED_OUT.search(statement, 0, found.start()) for found in LEFT_STANDING.finditer(statement))


def left_steps(text: str) -> str | None:
    """The first NEXT: or UNPROVEN: line that leaves a step for another session to run."""
    return next((line.strip() for line in LEFT.findall(text) if left_for(line)), None)


def left_for(line: str) -> bool:
    """Whether the line leaves a step for another session to run, a step its clause rules out, as "no coordinator runs it" does, aside."""
    return any(not limits.NEGATION.search(limits.CLAUSE_END.split(line[: found.start()])[-1]) for found in LEFT_FOR.finditer(line))


def he_sees(text: str) -> bool:
    """Whether a builder's report builds something he sees, by its NATIVE: line naming that surface rather than none."""
    native = NATIVE.search(text)
    return native is not None and not native[1].casefold().startswith("none")


def surface(job: dict) -> str | None:
    """Where he sees or uses a job's result, by its own brief's Native line; None when it names no surface."""
    native = (job.get("brief_flags") or {}).get("native")
    return native if native and not NO_SURFACE.match(native) else None


def stories(interactions: str | None) -> list[str]:
    """The user stories a brief's Interactions line names, one per `;`: each what he does on its surface and what he must then see."""
    return [story.strip() for story in (interactions or "").split(";") if story.strip()]


def unstoried(native: str, interactions: str | None) -> str | None:
    """Why a brief for a surface he sees may not go out: its Interactions line names no user story, or one Jev reads as steps with no result he must see, at least settled.DECIDES sure, all read in one call; Jev giving no reading lets it out, said on stderr."""
    from drive import settled

    shape = (
        f"name each user story on {native} with --interactions, one per `;`, each what he does there and what he must then see,"
        ' such as "press j on the queue; the next card opens with its question as its first line", which its job writes strict'
        f" acceptance criteria for before building and proves each on the installed surface with {SEEN}, by his words {STORIES_RULING}"
    )
    if not (told := stories(interactions)):
        return shape
    questions = {f"story{number}": {"label": {"instructions": STORY_QUESTION.format(story=story), "options": STORY_OPTIONS}} for number, story in enumerate(told, 1)}
    try:
        read = settled.jev({"state": {"surface": native, "stories": told}, "questions": questions})
    except settled.Unheard as failure:
        print(f"drive job brief: Jev did not read --interactions as user stories, so the brief goes out unchecked: {failure}", file=sys.stderr)
        return None
    steps = [story for number, story in enumerate(told, 1) if (found := read[f"story{number}"])["choice"] == "steps" and found["probabilities"]["steps"] >= settled.DECIDES]
    return f'--interactions names "{cut(steps[0], 120)}", steps with no result he must see: {shape}' if steps else None


def proofs(text: str) -> list[str]:
    """Each link and absolute path the text names, the punctuation that closes its clause cut off, such as the colon after a picture's path."""
    return [re.sub(r"[^\w/]+$", "", found) for found in PROOF.findall(text)]


def held(proof: str) -> bool:
    """Whether a proof is a link, or the path of a folder or of a file holding something, since an empty file proves nothing."""
    path = Path(proof).expanduser()
    return proof.startswith("http") or path.is_dir() or path.is_file() and path.stat().st_size > 0


def unproven(text: str, job_id: str, job: dict) -> str | None:
    """Why a builder's or codex job's DONE may not be kept: its CHECK: line names no link and no file that exists and is not empty, as held reads it, or, for what he sees, by its NATIVE: line or its brief's Native, falls short of story_gap."""
    real = [proof for line in CHECK.findall(text) for proof in proofs(line) if held(proof)]
    if not real:
        return (
            "a DONE names its proof on a CHECK: line, a link or the absolute path of a file that exists and is not empty, such as the"
            " live post, the recording of the click, or the saved output of the installed command"
        )
    if not (he_sees(text) or surface(job)):
        return None
    return story_gap(job_id, job, real, text)


def story_gap(job_id: str, job: dict, named: list[str], evidence: str) -> str | None:
    """Why the proof of a result he sees falls short: it names no recording, picture or live post of that surface, or, for a brief naming user stories, fewer pictures, recordings or live posts than stories, no proof file holding their acceptance criteria, or one Jev reads with the evidence as stopping at the bare minimum for a story, at least settled.DECIDES sure, every story read in one call; Jev giving no reading leaves it proven, said on stderr."""
    from drive import settled

    seen = [proof for proof in named if SEEN_PROOF.search(proof)]
    if not (told := stories((job.get("brief_flags") or {}).get("interactions"))):
        return None if seen else (
            "this builds something he sees, by its NATIVE: line or its brief's Native, so its proof is from that surface: record the"
            " click through it (agent-browser's recording, or screencapture -v of the window) or link the live post, and name the"
            " file or link on CHECK:; data fetched from an API or a command's output does not show what he sees"
        )
    if len(seen) < len(told):
        return (
            f"its brief names {len(told)} user stories and its proof shows {len(seen)} of them on the installed surface:"
            f" its proof file clicks through each story and its check names, for each, {SEEN}, by his words " + STORIES_RULING
        )
    if not (job.get("repro") and Path(job["repro"]).is_file()):
        return (
            f"{job_id}'s brief names user stories and it keeps no proof file holding their acceptance criteria: write them before"
            f" building into the script that clicks each story on the installed surface, and keep it with drive job set {job_id} --repro <file>"
        )
    criteria = Path(job["repro"]).read_text(errors="replace")[:CRITERIA_CHARS]
    questions = {f"proof{number}": {"label": {"instructions": PROOF_QUESTION.format(story=story), "options": PROOF_OPTIONS}} for number, story in enumerate(told, 1)}
    try:
        read = settled.jev({"state": {"proof_file": criteria, "check": evidence[-CRITERIA_CHARS:]}, "questions": questions})
    except settled.Unheard as failure:
        print(f"drive: Jev did not read the proof against its user stories, so it stands unchecked: {failure}", file=sys.stderr)
        return None
    bare = [story for number, story in enumerate(told, 1) if (found := read[f"proof{number}"])["choice"] == "bare" and found["probabilities"]["bare"] >= settled.DECIDES]
    if not bare:
        return None
    return (
        f'its proof stops at the bare minimum for the story "{cut(bare[0], 120)}": its proof file states that story\'s strict acceptance'
        " criteria, what he sees first, each click, key and hover's result and its empty and error states, asserts each on the installed"
        f" surface, and its check names {SEEN}, by his words " + STORIES_RULING
    )
