import re
import shlex
from datetime import datetime, timedelta

from drive import land, stage, state, view

STILL_FOR = timedelta(minutes=60)
SEARCH_FOR = timedelta(minutes=10)
AT_ONCE = 2
STARTS_AN_HOUR = 6
ANSWER = re.compile(r"^(WHY|WHO|NEXT|UNREAD):(.*(?:\n(?!(?:WHY|WHO|NEXT|UNREAD):).*)*)", re.MULTILINE)


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
    """Each link from a job, PR or wait to the ends it waits on, by id, in words that change only when it moves, and whether it is an end, waiting on nothing that holds, each by stage.onward, as stage.ends walks it, a wait on a stopped job walked on to each job that took its part, by moved, so a stopped job whose parts all have a successor is never an end; None once it reaches a running job, a PR only its repository's maintainers merge, by state.upstream, as one merged outside the drive, a step of his or a PR the drive does not track: a PR whose stage he owes, as a green one his Hold or the hold label holds does, while a red one's builder owes its fix, or that sits on a PR outside the drive, whose stage someone outside it owes, his open question card, whether a wait names it, a job asked it or it holds a PR from landing, or a release while on_hold, his hold on every release, stands."""
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
    """A job, PR or wait in words and what it waits on that holds, by stage.onward, each wait on a stopped job read as one on the jobs that took its part, by moved: a PR by its stage, followed to the job answering for it while its builder owes its step or it waits, a job by its state and the waits that hold it, and any other wait as his card says it."""
    if pr := data["prs"].get(subject):
        kept = pr["stage"]
        owner = stage.owner_job(data, subject) if kept and (kept["owner"] == stage.BUILDER or kept["name"] in ("waiting", "blocked")) else None
        nexts = moved(data, [*stage.onward(data, subject, cards), *([owner] if owner else [])])
        return (f"{subject} {kept['name']}, owed by {kept['owner']}: {kept['why']}" if kept else f"{subject} {pr['state']}"), nexts
    if subject in data["jobs"]:
        nexts = moved(data, stage.onward(data, subject, cards))
        return f"{subject} {data['jobs'][subject]['state']}" + (f" on {', '.join(nexts)}" if nexts else ""), nexts
    entry = view.wait_entry(data, subject, cards)
    return " ".join(filter(None, (entry["title"], entry["url"]))), []


def moved(data: dict, waits: list[str]) -> list[str]:
    """The waits as the stall code reads them, each once: a wait on a stopped job moved to each job that took a part it leaves owed, by successors, so the jobs and PRs waiting on it count as waiting on those jobs and their NEXT is those jobs' step; every other wait as given."""
    return list(dict.fromkeys(taken for wait in waits for taken in successors(data, wait)))


def successors(data: dict, wait: str) -> list[str]:
    """The jobs a wait stands for: for a stopped job, each other job carrying a part it leaves owed, by state.carriers, the drive's one reading of who carries a part, as its ORPHAN line, drive status and the dashboard read it, of those only each whose work is still to come, by view.coming, and the stopped job itself while one of those parts has no carrier, by uncarried, so a stop whose parts all went to other jobs leaves nothing waiting on it; any other wait stands for itself."""
    job = data["jobs"].get(wait)
    if not job or job["state"] != "stopped":
        return [wait]
    taken = [other for order_id, part in owed_parts(data, wait) for other, _ in state.carriers(data, order_id, part) if other != wait and view.coming(data, other)]
    return list(dict.fromkeys([*taken, *([wait] if uncarried(data, wait) else [])]))


def owed_parts(data: dict, job_id: str) -> list[tuple[str, str]]:
    """The parts a job leaves owed, as view.coming reads a stopped job's: each of an open order, its own or one it carries, not delivered, by state.delivered, as its order and part."""
    job = data["jobs"][job_id]
    return [
        (order_id, part)
        for order_id in [job["order"], *job["carries"]]
        if (order := data["orders"][order_id])["state"] not in state.CLOSED_ORDER_STATES
        for part in state.job_parts(job, order_id)
        if part in order["parts"] and not state.delivered(data, order_id, part)
    ]


def uncarried(data: dict, job_id: str) -> list[tuple[str, str]]:
    """The parts a stopped job leaves owed that no other job carries, by state.carriers, which only a new job on them delivers; none for a stopped builder a PR the drive does not track carries, by state.untracked_carriers, whose PR carries its parts."""
    if state.untracked_carriers(data, data["jobs"][job_id]):
        return []
    return [(order_id, part) for order_id, part in owed_parts(data, job_id) if not any(other != job_id for other, _ in state.carriers(data, order_id, part))]


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
    return {"print": printed, "since": state.stamp(moment), "agent": None, "found": None, "said": None, "claim": None}


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


def step(held: dict, moment: datetime) -> str | None:
    """The step a stall owes now: its watchdog agent, then its STUCK line once that agent answered or SEARCH_FOR passed; None while it owes none."""
    if not held["agent"]:
        return "agent"
    if held["said"]:
        return None
    return None if searching(held, moment) else "said"


def searching(held: dict, moment: datetime) -> bool:
    """Whether the stall's watchdog agent may still be reading it: one started under SEARCH_FOR ago whose process lives; a cause the drive's rows named, by cause, ran none."""
    return bool(held["agent"]["pid"]) and moment - state.parse_time(held["agent"]["at"]) < SEARCH_FOR and state.alive(held["agent"]["pid"])


def owner(data: dict, subject: str) -> tuple[str, str]:
    """The job answering for a stalled thing's first job or PR, and its order."""
    if subject in data["jobs"]:
        return subject, data["jobs"][subject]["order"]
    pr = data["prs"][subject]
    return pr["job"], pr["order"]


def starts(stalls: dict[str, dict], moment: datetime) -> tuple[int, int]:
    """How many watchdog agents run now, each started under SEARCH_FOR ago on a stall whose STUCK line has not taken its answer yet, and how many started in the last hour, over every stall of the drive."""
    held = [kept for kept in stalls.values() if kept["agent"] and kept["agent"]["pid"]]
    running = sum(1 for kept in held if moment - state.parse_time(kept["agent"]["at"]) < SEARCH_FOR and not (kept["said"] and state.parse_time(kept["said"]) >= state.parse_time(kept["agent"]["at"])))
    return running, sum(1 for kept in held if moment - state.parse_time(kept["agent"]["at"]) < timedelta(hours=1))


def taker_add(data: dict, job_id: str, parts: list[tuple[str, str]]) -> str:
    """The drive job add that starts the job taking the parts of the first order a stopped job leaves no other job carrying, by uncarried, since drive job resume refuses a stopped job."""
    job, order_id = data["jobs"][job_id], parts[0][0]
    flags = " ".join(f"--part {shlex.quote(part)}" for held_order, part in parts if held_order == order_id)
    return f"drive job add {job['kind']} --order {order_id} --goal {shlex.quote(job['goal'])} {flags}"


def cause(data: dict, thing: str, held: dict) -> dict[str, str] | None:
    """The WHY, WHO and NEXT lines of a stall whose cause the drive's own rows name, so no watchdog agent is started to read it: a builder or Sprite turn this Mac's caps hold, a stopped job leaving a part no other job carries, by uncarried, which drive job resume refuses, so its NEXT names the drive job add of that part's order, kind and goal that takes it on, a job whose Sprite lane died, which drive job resume restarts on a fresh one, an open card of his a member is or a job member waits on, or an open PR of a job member asking his Approve; None for any other, a staging env or release whose wait may never free included, which only an agent reads."""
    if turns := [member for member in held["members"] if member in state.TURN_WAITS]:
        return {"WHY": f"waits its turn on {', '.join(turns)}, which this Mac's caps hold", "WHO": "the watch, which frees the earliest due job as a slot opens", "NEXT": "nothing to run: its FREE line starts it"}
    jobs = [data["jobs"][member] for member in held["members"] if member in data["jobs"]]
    if stopped := [(member, parts) for member in held["members"] if member in data["jobs"] and data["jobs"][member]["state"] == "stopped" and (parts := uncarried(data, member))]:
        job_id, parts = stopped[0]
        return {
            "WHY": f"{job_id} stopped, and no job carries its {'parts' if len(parts) > 1 else 'part'} {'; '.join(f'{part!r} of {held_order}' for held_order, part in parts)}",
            "WHO": "the session holding its order",
            "NEXT": f"{taker_add(data, job_id, parts)} takes it on",
        }
    if dead := [member for member in held["members"] if member in data["jobs"] and data["jobs"][member].get("where") == "remote" and state.lane_death(data["jobs"][member], "dispatch")]:
        return {"WHY": f"{', '.join(dead)}'s Sprite lane died", "WHO": "the session holding its order", "NEXT": f"drive job resume {dead[0]} restarts it on a fresh Sprite"}
    if cards := [wait for wait in [*held["members"], *(wait for job in jobs for wait in job["waits"])] if wait not in data["jobs"] and state.card_wait(data, wait)]:
        return {"WHY": f"waits on his card {', '.join(dict.fromkeys(cards))}", "WHO": "him", "NEXT": "nothing to run: his answer frees it"}
    if taps := [key for job in jobs for key in job["prs"] if (pr := data["prs"].get(key)) and pr["state"] == "open" and state.asks_tap(data, pr)]:
        return {"WHY": f"{', '.join(taps)} waits on his Approve", "WHO": "him", "NEXT": "nothing to run: his tap lands it"}
    return None


def answer(text: str) -> dict[str, str]:
    """The WHY, WHO, NEXT and UNREAD answers a watchdog agent wrote, by label, each the label's line and every line below it up to the next label, as one line."""
    return {found[1]: " ".join(found[2].split()) for found in ANSWER.finditer(text)}
