import re
from datetime import datetime, timedelta

from drive import land, stage, state, view

STILL_FOR = timedelta(minutes=60)
TOLD_AFTER = timedelta(minutes=30)
SEARCH_FOR = timedelta(minutes=10)
AT_ONCE = 2
STARTS_AN_HOUR = 6
ANSWER = re.compile(r"^(WHY|WHO|NEXT|UNREAD):.*$", re.MULTILINE)


def chains(data: dict, cards: dict, on_hold: bool, moment: datetime) -> dict[str, list[tuple[str, str, bool]]]:
    """The chain of waits behind each open PR and each waiting job of the drive, from it to each end, by his pick "an automated watchdog that spawns a small agent if it detects anything truly stick": never a job or PR with a set time to come, nor a chain chain() ends at a running job, whose agent moves it or whose gap the watch says as its own line, at a step of his, which his card or answer already asks of him, or at a PR the drive does not track, which only its own author moves, on_hold true while his hold on every release stands."""
    found = {}
    for key, pr in data["prs"].items():
        if pr["state"] != "open" or not pr["stage"] or state.timed(data, data["jobs"][pr["job"]], moment):
            continue
        if (links := chain(data, cards, on_hold, key, set())) is not None:
            found[key] = links
    for job_id, job in data["jobs"].items():
        if job["state"] != "waiting" or not job["waits"] or state.timed(data, job, moment):
            continue
        if (links := chain(data, cards, on_hold, job_id, set())) is not None:
            found[job_id] = links
    return found


def chain(data: dict, cards: dict, on_hold: bool, subject: str, seen: set[str]) -> list[tuple[str, str, bool]] | None:
    """Each link from a job, PR or wait to the ends it waits on, by id, in words that change only when it moves, and whether it is an end, waiting on nothing that holds, each by stage.onward, as stage.ends walks it; None once it reaches a running job, a PR only its repository's maintainers merge, by state.upstream, as one merged outside the drive, a step of his or a PR the drive does not track: a PR whose stage he owes, as a green one his Hold or the hold label holds does, while a red one's builder owes its fix, or that sits on a PR outside the drive, whose stage someone outside it owes, his open question card, whether a wait names it, a job asked it or it holds a PR from landing, or a release while on_hold, his hold on every release, stands."""
    if subject in seen:
        return []
    seen.add(subject)
    pr, job = data["prs"].get(subject), data["jobs"].get(subject)
    if pr and (pr["stage"] and pr["stage"]["owner"] in (stage.HIM, stage.OUTSIDE) or state.upstream(pr)) or job and job["state"] == "running":
        return None
    if not pr and not job and (state.card_wait(data, subject) or on_hold and state.RELEASE_WAIT.fullmatch(subject)):
        return None
    here, nexts = link(data, cards, subject)
    links = [(subject, here, not nexts)]
    for wait in nexts:
        if (more := chain(data, cards, on_hold, wait, seen)) is None:
            return None
        links += more
    return links


def link(data: dict, cards: dict, subject: str) -> tuple[str, list[str]]:
    """A job, PR or wait in words and what it waits on that holds, by stage.onward: a PR by its stage, followed to the job answering for it while its builder owes its step or it waits, a job by its state and the waits that hold it, and any other wait as his card says it."""
    if pr := data["prs"].get(subject):
        kept = pr["stage"]
        owner = stage.owner_job(data, subject) if kept and (kept["owner"] == stage.BUILDER or kept["name"] in ("waiting", "blocked")) else None
        nexts = [*stage.onward(data, subject, cards), *([owner] if owner else [])]
        return (f"{subject} {kept['name']}, owed by {kept['owner']}: {kept['why']}" if kept else f"{subject} {pr['state']}"), nexts
    if subject in data["jobs"]:
        nexts = stage.onward(data, subject, cards)
        return f"{subject} {data['jobs'][subject]['state']}" + (f" on {', '.join(nexts)}" if nexts else ""), nexts
    entry = view.wait_entry(data, subject, cards)
    return " ".join(filter(None, (entry["title"], entry["url"]))), []


def things(data: dict, links: dict[str, list[tuple[str, str, bool]]], claims: list[dict]) -> dict[str, dict]:
    """Each thing the chains end on, by its id, with its members, every job and PR whose chain ends on it, in the order chains() found them, each link of their chains in words, whether waits join it, and the claim of another drive it stands behind, by behind() from claims, so however many jobs wait on one PR stack, release, staging env or job they make one stall, by his words "the watch reports a stall for each job held by the same open Approve card, so three jobs waiting on one card would each send him a line": a PR stands for the card it draws on by state.card_unit, its landing stack or its repository's open PRs serving its order, named by the stack's bottom PR or the order's lowest-numbered one; a staging or release wait for its fold, by fold(), its members every wait of the chains folding there; a job or a builder slot stands for itself."""
    stacks = land.landing_stacks(data)
    units = {key: state.card_unit(key, stacks.get(key, ()), pr) for key, pr in data["prs"].items() if pr["state"] == "open"}
    members: dict[tuple, list[str]] = {}
    for key in sorted(units, key=lambda key: int(key.split("#")[1])):
        if stack := units[key][0]:
            members[units[key]] = list(stack)
        else:
            members.setdefault(units[key], []).append(key)
    folds: dict[str, list[str]] = {}
    for end in dict.fromkeys(end for chained in links.values() for end, _, last in chained if last):
        if into := fold(end):
            folds.setdefault(into, []).append(end)
    found: dict[str, dict] = {}
    for subject, chained in links.items():
        for end, _, last in chained:
            if not last:
                continue
            into = fold(end)
            named = folds[into] if into else members[units[end]] if end in units else [end]
            held = found.setdefault(into or named[0], {"members": named, "subjects": [], "chain": {}, "joins": bool(into), "behind": behind(end, claims)})
            if subject not in held["subjects"]:
                held["subjects"].append(subject)
                held["chain"] |= {link: words for link, words, _ in chained}
    return found


def fold(wait: str) -> str | None:
    """The one stall a wait stands in with every other wait on the same, by his order's words "one frozen environment produces five stall lines for one job in five minutes and twenty for two jobs": a staging wait in its env's, whichever service, commit or job awaits it there, and a release wait in its PR's on the env it names, or on every cluster when it names none, whichever service; None for any other wait."""
    if staging := state.STAGING_WAIT.fullmatch(wait):
        return f"staging:{staging['env']}"
    if release := state.RELEASE_WAIT.fullmatch(wait):
        return f"release:{release['pr']}" + (f"@{release['env']}" if release["env"] else "")
    return None


def behind(wait: str, claims: list[dict]) -> dict | None:
    """The claim of another drive on this Mac a staging wait, or a release wait naming an env, stands behind, by the order's words "when staging is frozen by another drive's claim": of claims, state.others_claims() latest end first, the first, while the wait awaits state.STAGING_ENV, the env every staging claim and the freeze it announces hold, by the repository's staging-claim rule; None for any other wait or env, or with no such claim standing."""
    found = state.STAGING_WAIT.fullmatch(wait) or state.RELEASE_WAIT.fullmatch(wait)
    return claims[0] if claims and found and found["env"] == state.STAGING_ENV else None


def words(data: dict, cards: dict, held: dict) -> str:
    """A thing in words that change only when it moves: each member as link() says it, then the claim it stands behind with that claim's end, so the claim ending or moving its end restarts the thing's hour."""
    said = [link(data, cards, member)[0] for member in held["members"]]
    if claim := held["behind"]:
        said.append(f"behind the staging claim {claim['link']} of {claim['order']} on drive {claim['drive']}, which ends {claim['until']}")
    return "; ".join(said)


def aged(kept: dict | None, printed: str, joins: bool, moment: datetime) -> dict:
    """The stall record of a thing that reads printed now: kept while it reads as it did, or, for one waits join, while each wait it read still reads so, its print then naming the waits that joined, else a fresh one from this moment, so a push, a new stage, his answer or a wait ending on it is progress and a new stall, while a job starting or ending its wait on it is neither, nor a wait joining a staging env or release that already stands."""
    if kept and (kept["print"] == printed or joins and set(kept["print"].split("; ")) <= set(printed.split("; "))):
        return kept | {"print": printed}
    return {"print": printed, "since": state.stamp(moment), "agent": None, "found": None, "said": None, "told": None, "claim": None}


def stalled(data: dict, kept: dict[str, dict], found: dict[str, dict], moment: datetime) -> list[str]:
    """The things that stood unmoved past their deadline, oldest first, never one behind another drive's claim, owed outside the drive until that claim ends, so it starts no watchdog agent, says no STUCK line and reaches him never."""
    old = [thing for thing, held in kept.items() if not found[thing]["behind"] and moment - state.parse_time(held["since"]) >= deadline(data, found[thing]["members"])]
    return sorted(old, key=lambda thing: kept[thing]["since"])


def deadline(data: dict, members: list[str]) -> timedelta:
    """How long a thing stands unmoved before it is a stall: an hour, or the longest deadline of its PRs' stages when that is longer, as a queued PR's."""
    return max([STILL_FOR, *(stage.EVERY.get(pr["stage"]["name"], STILL_FOR) for member in members if (pr := data["prs"].get(member)) and pr["stage"])])


def waiting(found: dict) -> list[str]:
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
