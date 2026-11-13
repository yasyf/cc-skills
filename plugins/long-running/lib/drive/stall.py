import re
from datetime import datetime, timedelta

from drive import land, stage, state, view

STILL_FOR = timedelta(minutes=60)
TOLD_AFTER = timedelta(minutes=30)
SEARCH_FOR = timedelta(minutes=10)
AT_ONCE = 2
STARTS_AN_HOUR = 6
FOLLOWED = (stage.BUILDER, stage.WAITED)
ANSWER = re.compile(r"^(WHY|WHO|NEXT|UNREAD):.*$", re.MULTILINE)


def chains(data: dict, cards: dict, moment: datetime) -> dict[str, list[tuple[str, str, bool]]]:
    """The chain of waits behind each open PR and each waiting job of the drive, from it to each end, by his pick "an automated watchdog that spawns a small agent if it detects anything truly stick": never a PR his Hold or the hold label stops or whose stage asks him, which his card shows, nor a job or PR with a set time to come, nor a chain reaching a running job, whose agent moves it or whose gap the watch says as its own line."""
    found = {}
    for key, pr in data["prs"].items():
        kept = pr["stage"]
        if pr["state"] != "open" or not kept or kept["name"] in stage.ASKS_HIM or stage.stopped(pr) or state.timed(data, data["jobs"][pr["job"]], moment):
            continue
        if (links := chain(data, cards, key, set())) is not None:
            found[key] = links
    for job_id, job in data["jobs"].items():
        if job["state"] != "waiting" or not job["waits"] or state.timed(data, job, moment):
            continue
        if (links := chain(data, cards, job_id, set())) is not None:
            found[job_id] = links
    return found


def chain(data: dict, cards: dict, subject: str, seen: set[str]) -> list[tuple[str, str, bool]] | None:
    """Each link from a job, PR or wait to the ends it waits on, by id, in words that change only when it moves, and whether it is an end, waiting on nothing that holds; None once it reaches a running job."""
    if subject in seen:
        return []
    seen.add(subject)
    if (job := data["jobs"].get(subject)) and job["state"] == "running":
        return None
    here, nexts = link(data, cards, subject)
    links = [(subject, here, not nexts)]
    for wait in nexts:
        if (more := chain(data, cards, wait, seen)) is None:
            return None
        links += more
    return links


def link(data: dict, cards: dict, subject: str) -> tuple[str, list[str]]:
    """A job, PR or wait in words and what it waits on that holds: a PR by its stage, followed to the job answering for it while its builder or what it waits on owes its step, a job by its state and the waits that hold it, and any other wait as his card says it."""
    if pr := data["prs"].get(subject):
        kept = pr["stage"]
        owner = stage.owner_job(data, subject) if kept and kept["owner"] in FOLLOWED else None
        nexts = [*view.holding(data, pr["waits"], cards), *([owner] if owner else [])]
        return (f"{subject} {kept['name']}, owed by {kept['owner']}: {kept['why']}" if kept else f"{subject} {pr['state']}"), nexts
    if job := data["jobs"].get(subject):
        nexts = view.holding(data, job["waits"], cards) if job["state"] in ("waiting", "built") else []
        return f"{subject} {job['state']}" + (f" on {', '.join(nexts)}" if nexts else ""), nexts
    entry = view.wait_entry(data, subject, cards)
    return " ".join(filter(None, (entry["title"], entry["url"]))), []


def things(data: dict, links: dict[str, list[tuple[str, str, bool]]]) -> dict[str, dict[str, list[str]]]:
    """Each thing the chains end on, by its id, with its members and every job and PR whose chain ends on it, in the order chains() found them, so however many jobs wait on one card they make one stall, by his words "the watch reports a stall for each job held by the same open Approve card, so three jobs waiting on one card would each send him a line": a PR stands for the card it draws on by state.card_unit, its landing stack or its repository's open PRs serving its order, named by the stack's bottom PR or the order's lowest-numbered one; a question card, a release, a job or a builder slot stands for itself."""
    stacks = land.landing_stacks(data)
    units = {key: state.card_unit(key, stacks.get(key, ()), pr) for key, pr in data["prs"].items() if pr["state"] == "open"}
    members: dict[tuple, list[str]] = {}
    for key in sorted(units, key=lambda key: int(key.split("#")[1])):
        if stack := units[key][0]:
            members[units[key]] = list(stack)
        else:
            members.setdefault(units[key], []).append(key)
    found: dict[str, dict[str, list[str]]] = {}
    for subject, chained in links.items():
        for end, _, last in chained:
            if not last:
                continue
            named = members[units[end]] if end in units else [end]
            held = found.setdefault(named[0], {"members": named, "subjects": []})["subjects"]
            held += [] if subject in held else [subject]
    return found


def words(data: dict, cards: dict, members: list[str]) -> str:
    """A thing in words that change only when it moves: each member as link() says it."""
    return "; ".join(link(data, cards, member)[0] for member in members)


def aged(kept: dict | None, printed: str, moment: datetime) -> dict:
    """The stall record of a thing that reads printed now: kept while it reads as it did, else a fresh one from this moment, so a push, a new stage, his answer or a wait ending on it is progress and a new stall, while a job starting or ending its wait on it is neither."""
    if kept and kept["print"] == printed:
        return kept
    return {"print": printed, "since": state.stamp(moment), "agent": None, "found": None, "said": None, "told": None, "claim": None}


def stalled(data: dict, kept: dict[str, dict], found: dict[str, dict[str, list[str]]], moment: datetime) -> list[str]:
    """The things that stood unmoved past their deadline, oldest first."""
    old = [thing for thing, held in kept.items() if moment - state.parse_time(held["since"]) >= deadline(data, found[thing]["members"])]
    return sorted(old, key=lambda thing: kept[thing]["since"])


def deadline(data: dict, members: list[str]) -> timedelta:
    """How long a thing stands unmoved before it is a stall: an hour, or the longest deadline of its PRs' stages when that is longer, as a queued PR's."""
    return max([STILL_FOR, *(stage.EVERY.get(pr["stage"]["name"], STILL_FOR) for member in members if (pr := data["prs"].get(member)) and pr["stage"])])


def waiting(found: dict[str, list[str]]) -> list[str]:
    """The jobs and PRs waiting on a thing, its own PRs aside."""
    return [subject for subject in found["subjects"] if subject not in found["members"]]


def step(held: dict, moment: datetime, away: bool) -> str | None:
    """The step a stall owes now: its watchdog agent, its STUCK line once that agent answered or SEARCH_FOR passed, or his report once that line stood TOLD_AFTER while he is here; None while it owes none."""
    if not held["agent"]:
        return "agent"
    if not held["said"]:
        return None if state.alive(held["agent"]["pid"]) and moment - state.parse_time(held["agent"]["at"]) < SEARCH_FOR else "said"
    if held["told"] or away or moment - state.parse_time(held["said"]) < TOLD_AFTER:
        return None
    return "told"


def owner(data: dict, subject: str) -> tuple[str, str]:
    """The job answering for a stalled thing's first job or PR, and its order."""
    if subject in data["jobs"]:
        return subject, data["jobs"][subject]["order"]
    pr = data["prs"][subject]
    return pr["job"], pr["order"]


def starts(stalls: dict[str, dict], moment: datetime) -> tuple[int, int]:
    """How many watchdog agents run now, each started under SEARCH_FOR ago on a stall not said yet, and how many started in the last hour, over every stall of the drive."""
    held = [kept for kept in stalls.values() if kept["agent"]]
    running = sum(1 for kept in held if not kept["said"] and moment - state.parse_time(kept["agent"]["at"]) < SEARCH_FOR)
    return running, sum(1 for kept in held if moment - state.parse_time(kept["agent"]["at"]) < timedelta(hours=1))


def answer(text: str) -> dict[str, str]:
    """The WHY, WHO, NEXT and UNREAD lines a watchdog agent answered, by label."""
    return {found[1]: found[0].removeprefix(f"{found[1]}:").strip() for found in ANSWER.finditer(text)}
