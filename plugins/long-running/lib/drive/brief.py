import re
from pathlib import Path

from drive import limits, state
from drive.render import cut

BRIEF_JOB = re.compile(r"^Drive:[^\n]*?\bjob (j\d+)\b", re.MULTILINE)
BRIEF_FILE = re.compile(r"/\S+/jobs/j\d+\.(?:brief|handoff)\.md\b")
HANDOFF = re.compile(r"^Handoff:", re.MULTILINE)
SERVES_TOOLING = re.compile(r"^Serves:[ \t]*drive tooling\b", re.MULTILINE | re.IGNORECASE)
PLANNED = re.compile(r"^(Goal|Done when|Where|Runs as|Native|Interactions|Would he ask|Plan|Incident):[ \t]*(.+)$", re.MULTILINE)
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
DESIGN_LINES = ("Root cause:", "Settled:", "Native:", "Exists:", "Change:", "Fork:", "Facts:")
EXISTS = re.compile(r"^[ \t]*Exists:[ \t]*(.*)$", re.MULTILINE)
CITED = re.compile(r"[\w./-]+\.[A-Za-z]\w*:\d+|https?://\S+")
OWNED = re.compile(r"\b(?:extended|reused|deleted) by change (\d+)\b", re.IGNORECASE)
SEARCHED = re.compile(r"\b(?:ccx|rg|grep|ccn|answer_search|search_plan)\b")
DUPLICATION_RULING = '"how _agents was allowed to get creted in the irst place sonce its a terieble duplicatgion and was clerarly not throught through"'
UNDESIGNED = ("HANDOFF", "BLOCKED")
BLOCK = "a Researched: line and under it rulings:, exists: and open: lines"
CHOICE_WORDS = r"\b(?:options?|choices?|choose|lost|picks?|decisions?|forks?)\b"
STOPS_ON_CHOICE = re.compile(rf"[^.;]*(?:\bBLOCKED\b[^.;]*{CHOICE_WORDS}|{CHOICE_WORDS}[^.;]*\bBLOCKED\b)[^.;]*")
STOPPED_RULING = '"fix ur tooling if it stopped on that it shoukd. have brought a design question card to me asap not just stopped int eh middle of the incident"'


def carried(prompt: str) -> str:
    """An agent's prompt with the brief file drive job brief wrote read in after it, when the prompt names one."""
    named = BRIEF_FILE.search(prompt)
    return f"{prompt}\n{Path(named[0]).read_text()}" if named else prompt


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


def research(name: str, data: dict, order_id: str) -> tuple[str, str] | None:
    """The newest reader of the order whose kept report carries a filled Researched: block, with that report."""
    for job_id in reversed(data["orders"][order_id]["jobs"]):
        job = data["jobs"][job_id]
        if job["kind"] == "reader" and job["order"] == order_id and filled(researched(text := state.report_text(name, job_id))):
            return job_id, text
    return None


def job_refusal(name: str, data: dict, order_id: str, kind: str, parts: list[str], lands: bool = False, waits: list[str] | None = None) -> str | None:
    """Why a job of this kind may not start on the order: an order he limited to options gets no builder or codex job until he picks, an answer order is one reader's, and a build order's builder, designer or codex lane waits on its reader's research, added at once waiting on a reader of the order still at work when --waits names it, so no part sits in no job while it reads, and a builder, when that research found a choice for him, on that choice reaching him: the reader's kept question on his card or settled by his recorded words, or a design lane. A job of any kind may wait on his card, a drive PR or a release, which check_waits reads, and on the change a builder or codex job builds, which state.change_wait_refusal reads, until its first PR takes the wait's place; any other job it waits on is the reader whose research it needs. The immediate fix of a bug hurting someone now, a red default branch's fix-forward among them, and a builder landing PRs he approved start at once past each of those, by his words: "always do the immediate fix (or rollback if prudent) in parallel to giving me options for the proper fix". A follow-up he orders to a PR gets its design job at once, doing its own research and waiting on nothing, beside landing that PR, by his words "start deisgnng the follow p right awya motherfucker dont wait"."""
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
    follow_up = kind == "design" and state.FOLLOW_UP in parts
    if follow_up and waits:
        return (
            f'--waits {", ".join(waits)}: a "{state.FOLLOW_UP}" design starts now, beside landing the PR it follows, never once that PR'
            f" lands or a reader returns, by his words {FOLLOW_UP_RULING}: add it with no --waits, and its designer does its own research"
        )
    researched = order["kind"] == "build" and not order["incident"] and kind in RESEARCHED_KINDS and not at_once and not follow_up
    on_jobs = [wait for wait in waits or [] if wait in data["jobs"] and data["jobs"][wait]["kind"] not in state.CHANGE_KINDS]
    if on_jobs and not researched:
        return (
            f"--waits {', '.join(on_jobs)}: only a build order's designer, builder or codex lane is added waiting on a reader, so this {kind}"
            f" waits only on his card, a drive PR, a release or a builder's change: add it with no --waits {', '.join(on_jobs)}"
        )
    if not researched:
        return None
    found = research(name, data, order_id)
    if found is None:
        jobs = data["jobs"]
        readers = [job_id for job_id in order["jobs"] if jobs[job_id]["order"] == order_id and jobs[job_id]["kind"] == "reader" and jobs[job_id]["state"] not in state.FINISHED_JOB_STATES]
        if on_jobs:
            if not (strays := [wait for wait in on_jobs if wait not in readers]):
                return None
            at_work = f"wait on {' or '.join(readers)}" if readers else f'none is, so add one first: drive job add reader --order {order_id} --goal "research: <what>"'
            return f"--waits {', '.join(strays)}: names no reader of {order_id} still at work; {at_work}"
        meanwhile = (
            f"; while {readers[0]} reads, add this {kind} now waiting on it with --waits {readers[0]}, so the part is never in no job,"
            " and start its agent on the watch's FREE line once the reader returns"
            if readers
            else ""
        )
        return (
            f"{order_id} is a build order, so its {kind} starts only once an Opus reader has kept {BLOCK} in its report:"
            f' drive job add reader --order {order_id} --goal "research: <what>", then a long-running:reader whose brief'
            " asks for that block; its rulings line holds his recorded answers on the order's words and area, its exists"
            f" line what already does part of it, and its open line each question the order implies{meanwhile}; {immediate}"
        )
    if on_jobs:
        return f"--waits {', '.join(on_jobs)}: {found[0]} already kept {order_id}'s research, so this {kind} starts now: add it with no --waits {', '.join(on_jobs)}"
    reader, text = found
    designing = any(data["jobs"][job_id]["kind"] == "design" for job_id in order["jobs"] if data["jobs"][job_id]["order"] == order_id)
    if kind == "builder" and text.lstrip().startswith("DESIGN") and not designing and (unasked := unasked_design(reader, data["jobs"][reader]["questions"])):
        return (
            f"{unasked}; a choice that needs a design of its own gets drive job add design --order {order_id} first, then a"
            f" long-running:designer briefed Researched: {reader}; {immediate}"
        )
    return None


def unasked_design(reader: str, questions: list[dict]) -> str | None:
    """Why a reader's DESIGN holds its order's builder: the reader's kept question is the one record of its choice, so the builder starts once that question is on his card, building the recommended option beside it until his answer closes it, or once his recorded words settle it with no card drawn."""
    if any(question["closed"] or question["card"] or question["waits"] for question in questions):
        return None
    if not questions:
        return (
            f"{reader} returned DESIGN, a choice for him its research found, and keeps it as no question of his: drive job ask {reader}"
            " keeps that DESIGN as his question, whose ask record draws his card unless his recorded words already settle it, and"
            " this builder starts once the card is up, building the recommended option beside it, or once his words settle it"
        )
    asked = questions[-1]
    if asked["refused"]:
        return (
            f'{reader} returned DESIGN, and drive job ask refused its question "{cut(asked["title"] or "", 80)}" kept in {asked["file"]},'
            f' so no card draws: {cut(asked["refused"].removeprefix("refused: "), 160)}; act on that refusal and ask again with'
            f" drive job ask {reader} {asked['file']}"
        )
    return (
        f'{reader} returned DESIGN, and its question "{cut(asked["title"] or "", 80)}" kept in {asked["file"]} is on no card of his yet:'
        " its ask record draws the card, or, when his recorded words settle it, the record tool's answer naming that ask's waits id"
        " settles it with no card; this builder starts once either is done"
    )


def brief_refusal(name: str, data: dict, job_id: str, brief: str) -> str | None:
    """Why this brief may not start its job's agent: the lines its writer plans the job with ask him for a sign-in, Touch ID or cc-sudo on an AWS action this Mac's machine profile runs, a drive tooling brief aside, whose lines name the sign-ins the drive's checks refuse; an incident's codex lane or builder needs its kind's rule on a choice, by incident_refusal; a build order's builder, designer or codex lane needs the Researched: line naming its reader, unless it ships the immediate fix or designs a follow-up he ordered, which starts at once doing its own research, and an answer order's one lane does its own research."""
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
    reader = data["jobs"].get(named[1])
    if not (reader and reader["kind"] == "reader" and reader["order"] == order_id and filled(researched(state.report_text(name, named[1])))):
        return f"Researched: {named[1]} names no reader of {order_id} that kept {BLOCK} in its report"
    return None


def incident_refusal(job_id: str, order_id: str, brief: str, rule: str) -> str | None:
    """Why an incident lane's brief may not start it: a choice never stops an incident, so its brief carries the rule its kind follows on one, word for word, and no line of it orders a BLOCKED return on a loss or a choice."""
    if rule not in " ".join(brief.split()):
        return f'{job_id} works on {order_id}, an incident, which a choice never stops, by his words {STOPPED_RULING}: its brief\'s Incident: line carries, word for word, "{rule}"'
    if stops := next((found for line in brief.splitlines() if (found := STOPS_ON_CHOICE.search(" ".join(line.split()).replace(rule, "")))), None):
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
    """Why a job's return may not be kept: a builder's or designer's opens with a result word, a REPRO: file is kept on the job first, a report naming a limit in its own words as a finding left standing is an ASK or DESIGN, a design lane's is the design in its shape, a research reader's carries its Researched: block, a reader's DESIGN is a choice for him with its options, starting from the root cause and recommending the option that removes it, a DONE, READY or PUSHED leaves no step for someone else, a builder's DONE names its proof, and a builder's READY or PUSHED names PRs whose tap card draws, saying how his words are met on each reworked to them, with a before and an after screenshot when its NATIVE: line names what he sees, and says how it builds on what its research found."""
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
            " builders open, a Fork: line per fork and Facts:"
        )
    if job["kind"] == "design" and opening == "DESIGN" and (unowned := unowned_exists(name, data, job["order"], text)):
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
    if job["kind"] == "reader" and (refused := reader_refusal(job_id, job, order, text, lines)):
        return refused
    if opening in ("DONE", *READY_OPENINGS) and (left := left_steps(text)):
        return (
            f'a {opening} leaves a step for someone else: "{cut(left, 120)}". Do that step yourself before you return,'
            " or return ASK when it needs his choice"
        )
    if job["kind"] == "builder" and opening == "DONE":
        return unproven(text)
    if job["kind"] == "builder" and opening in READY_OPENINGS and not state.REPORT_CHANGE.search(text):
        if reworked := [key for key in state.named_prs(data, job["order"], lines[0]) if data["prs"][key]["rework"]]:
            return f"{', '.join(reworked)}: being reworked to his words, so its {opening} says in a CHANGE: line how they are met, which its fresh card shows him"
    if not (job["kind"] == "builder" and opening in READY_OPENINGS and order["kind"] == "build") or order["incident"]:
        return None
    found = research(name, data, job["order"])
    exists = researched(found[1])["exists"] if found else "none"
    if exists.casefold().rstrip(".") == "none" or REUSES.search(text):
        return None
    return (
        f"{found[0]}'s research found what already does part of this: {exists}. A {opening} says, in a REUSES: line, how the"
        " PR calls or extends it rather than adding a second copy beside it"
    )


def reader_refusal(job_id: str, job: dict, order: dict, text: str, lines: list[str]) -> str | None:
    """Why a reader's return may not be kept: research for a build order carries its filled Researched: block, and a DESIGN is a choice for him of two to four options, one recommended, starting from the root cause its evidence finds and recommending the option that removes it, and on a bug hurting someone now names the immediate fix shipping while he picks."""
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
    if problems := limits.root_cause_problems(text, options, limits.RECOMMENDED.sub("", marked[0])):
        return f"a reader's DESIGN starts from the root cause and recommends the option that removes it: {problems[0]}"
    if state.hurts_now(order) and (problems := limits.interim_fix_problems(text)):
        return f"a reader's DESIGN on a bug hurting someone now names its immediate fix: {problems[0]}"
    return None


def design_missing(lines: list[str]) -> list[str]:
    missing = [] if DESIGN_PART.match(lines[0]) else ["a first line DESIGN <the part>"]
    return missing + [f"{'an' if label[0] in 'AEIOU' else 'a'} {label} line" for label in DESIGN_LINES if not any(line.startswith(label) for line in lines)]


def unowned_exists(name: str, data: dict, order_id: str, text: str) -> str | None:
    """Why a design's Exists: line does not show what already does or holds what it touches, so it would add a second owner of one fact beside the first: an entry citing no path:line or link, or not naming the change of its list that extends, reuses or deletes it; or none, naming no search that found nothing, or while its order's reader found something."""
    listed = EXISTS.search(text)[1].strip()
    shape = (
        "a design names, before it adds any table, column, store, file, process, mechanism, check or verb, each thing that"
        " already does or holds what it touches, one entry each separated by ;, citing its path:line or link and ending"
        " extended by change N, reused by change N or deleted by change N, N counting its Change: lines from 1, so one thing"
        f' owns each fact; or none: <each search that found nothing, such as ccx code search "<words>">. His words: {DUPLICATION_RULING}'
    )
    if listed.casefold().startswith("none"):
        found = research(name, data, order_id)
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


def proofs(text: str) -> list[str]:
    """Each link and absolute path the text names, the punctuation that closes its clause cut off, such as the colon after a picture's path."""
    return [re.sub(r"[^\w/]+$", "", found) for found in PROOF.findall(text)]


def unproven(text: str) -> str | None:
    """Why a builder's DONE may not be kept: its CHECK: line names no proof that exists, or, for what he sees, no recording, picture or live post of it."""
    real = [proof for line in CHECK.findall(text) for proof in proofs(line) if proof.startswith("http") or Path(proof).expanduser().exists()]
    if not real:
        return (
            "a DONE names its proof on a CHECK: line, a link or the absolute path of a file that exists, such as the"
            " live post, the recording of the click, or the saved output of the installed command"
        )
    if not he_sees(text) or any(SEEN_PROOF.search(proof) for proof in real):
        return None
    return (
        "this builds something he sees, by its NATIVE: line, so its proof is from that surface: record the click through"
        " it (agent-browser's recording, or screencapture -v of the window) or link the live post, and name the file or"
        " link on CHECK:; data fetched from an API or a command's output does not show what he sees"
    )
