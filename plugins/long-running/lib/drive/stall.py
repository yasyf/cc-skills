import re
from collections.abc import Callable
from datetime import datetime, timedelta

from drive import stage, state, view

STILL_FOR = timedelta(minutes=60)
TOLD_AFTER = timedelta(minutes=30)
SEARCH_FOR = timedelta(minutes=10)
AT_ONCE = 2
STARTS_AN_HOUR = 6
FOLLOWED = (stage.BUILDER, stage.WAITED)
ANSWER = re.compile(r"^(WHY|WHO|NEXT|UNREAD):.*$", re.MULTILINE)


def chains(data: dict, cards: dict, moment: datetime, heard: Callable[[str], bool]) -> dict[str, list[tuple[str, str]]]:
    """The chain of waits behind each open PR and each waiting job with waits of the orders heard, from it to each end, by his pick "an automated watchdog that spawns a small agent if it detects anything truly stick": never a PR his Hold or the hold label stops or whose stage asks him, which his card shows, nor a job or PR with a set time to come, nor a chain reaching a running job, whose agent moves it or whose gap the watch says as its own line."""
    found = {}
    for key, pr in data["prs"].items():
        kept = pr["stage"]
        if pr["state"] != "open" or not kept or kept["name"] in stage.ASKS_HIM or stage.stopped(pr) or not heard(pr["order"]) or state.timed(data, data["jobs"][pr["job"]], moment):
            continue
        if (links := chain(data, cards, key, set())) is not None:
            found[key] = links
    for job_id, job in data["jobs"].items():
        if job["state"] != "waiting" or not job["waits"] or not heard(job["order"]) or state.timed(data, job, moment):
            continue
        if (links := chain(data, cards, job_id, set())) is not None:
            found[job_id] = links
    return found


def chain(data: dict, cards: dict, subject: str, seen: set[str]) -> list[tuple[str, str]] | None:
    """Each link from a job, PR or wait to the ends it waits on, by id and in words that change only when it moves: a PR by its stage, followed to the job answering for it while its builder or what it waits on owes its step, a job by its state and the waits that hold it, and any other wait as his card says it; None once it reaches a running job."""
    if subject in seen:
        return []
    seen.add(subject)
    if pr := data["prs"].get(subject):
        kept = pr["stage"]
        owner = stage.owner_job(data, subject) if kept and kept["owner"] in FOLLOWED else None
        here = f"{subject} {kept['name']}, owed by {kept['owner']}: {kept['why']}" if kept else f"{subject} {pr['state']}"
        nexts = [*view.holding(data, pr["waits"], cards), *([owner] if owner else [])]
    elif job := data["jobs"].get(subject):
        if job["state"] == "running":
            return None
        nexts = view.holding(data, job["waits"], cards) if job["state"] in ("waiting", "built") else []
        here = f"{subject} {job['state']}" + (f" on {', '.join(nexts)}" if nexts else "")
    else:
        entry = view.wait_entry(data, subject, cards)
        here, nexts = " ".join(filter(None, (entry["title"], entry["url"]))), []
    links = [(subject, here)]
    for wait in nexts:
        if (more := chain(data, cards, wait, seen)) is None:
            return None
        links += more
    return links


def aged(kept: dict | None, links: list[tuple[str, str]], moment: datetime) -> dict:
    """The stall record of a job or PR whose chain reads links now: kept while its chain reads as it did, else a fresh one from this moment, so a push, a new stage, a wait ending or any link moving is progress and a new stall."""
    printed = "; ".join(words for _, words in links)
    if kept and kept["print"] == printed:
        return kept
    return {"print": printed, "since": state.stamp(moment), "agent": None, "found": None, "said": None, "told": None}


def stalled(data: dict, kept: dict[str, dict], links: dict[str, list[tuple[str, str]]], moment: datetime) -> list[str]:
    """The jobs and PRs whose chain stood unmoved past its deadline, oldest first, but one inside the chain of another of them, which names it."""
    old = [subject for subject, held in kept.items() if moment - state.parse_time(held["since"]) >= deadline(data, subject)]
    inner = {link for subject in old for link, _ in links[subject][1:]}
    return sorted((subject for subject in old if subject not in inner), key=lambda subject: kept[subject]["since"])


def deadline(data: dict, subject: str) -> timedelta:
    """How long a chain stands unmoved before it is a stall: an hour, or a PR's stage's own deadline when that is longer, as a queued PR's."""
    if subject not in data["prs"]:
        return STILL_FOR
    return max(STILL_FOR, stage.EVERY.get(data["prs"][subject]["stage"]["name"], STILL_FOR))


def owner(data: dict, subject: str) -> tuple[str, str]:
    """The job answering for a stalled job or PR, and its order."""
    if subject in data["jobs"]:
        return subject, data["jobs"][subject]["order"]
    pr = data["prs"][subject]
    return stage.owner_job(data, subject) or pr["job"], pr["order"]


def starts(data: dict, moment: datetime) -> tuple[int, int]:
    """How many watchdog agents run now, each started under SEARCH_FOR ago on a stall not said yet, and how many started in the last hour, over every job and PR of the drive."""
    held = [kept for row in (*data["jobs"].values(), *data["prs"].values()) if (kept := row["stall"]) and kept["agent"]]
    running = sum(1 for kept in held if not kept["said"] and moment - state.parse_time(kept["agent"]["at"]) < SEARCH_FOR)
    return running, sum(1 for kept in held if moment - state.parse_time(kept["agent"]["at"]) < timedelta(hours=1))


def answer(text: str) -> dict[str, str]:
    """The WHY, WHO, NEXT and UNREAD lines a watchdog agent answered, by label."""
    return {found[1]: found[0].removeprefix(f"{found[1]}:").strip() for found in ANSWER.finditer(text)}
