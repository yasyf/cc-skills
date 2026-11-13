import collections
import contextlib
import functools
import hashlib
import json
import mmap
import os
import re
import shlex
import subprocess
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Self

from drive import agents, codex, dashboard, github, land, limits, orca, page, place, reads, render, served, slack, stage, state, superseded, view
from drive.install import MERGED_DONE, PROVEN, config_dir, merged_done, merged_ready, product, proof_gap, said_on_prs, upgraded
from drive.render import cut, subscribe, when

POLL_SECONDS = 5
READ_EVERY = timedelta(seconds=60)
FAILURES_BEFORE_SAYING = 3
FAILURE_EVERY = timedelta(minutes=10)
TICK_EVERY = timedelta(minutes=30)
DUE_EVERY = timedelta(minutes=10)
QUIET_INCIDENT = timedelta(minutes=5)
TELL_AFTER = timedelta(minutes=10)
WAKES = ("TAP ", "DUE ", "TELL ", "ORPHAN ", "ANSWER ", "JOB ", "LANE ", "STUCK ", "STAGE ", "FREE ", "EVENT stop ", "WATCH stopped")
PR_EVENT = re.compile(r"PR (?P<key>\S+): (?P<change>.+) \[(?P<order>o\d+) (?P<job>j\d+)\]$")
NOT_LANDED = "not landed"
TAP_EVENT = re.compile(rf"TAP (?P<label>\S+) (?P<key>\S+)(?P<stalled> {NOT_LANDED})?")
LANE = "watch"
READS_LOG = "reads.log"
RELAUNCHES_LOG = "relaunches.log"
PROOFS_LOG = "proofs.log"
CARRY_ON = "The lane that held this job died while it worked, and the watch started you in its place: carry the job on from where that lane left it."
UNTHREADED = "the order has no thread, so its time was the drive's own and is said this once"
CUT_OFF = (
    f"its read stopped before it finished, so what it did and printed are unknown, its output in the drive's {READS_LOG}, and no read"
    " runs twice: read what a drive command changed, then ask again if it must run again"
)
DRIVE = Path(__file__).resolve().parents[2] / "bin" / "drive"
OWING_STATES = ("working", "waiting")
LIVE_JOB_STATES = ("running", "waiting")
RELEASES_AT_ONCE = 2
QUIET_LANE = timedelta(minutes=30)
LINE_BREAKS = re.compile(r"[\r\n]+")
HANDOFF = timedelta(minutes=2)
SETTLING = ""
PR_LINE = re.compile(r"\s*(?:READY|PUSHED|PR:)")
LANE_REPORT = re.compile(r"DRIVE job report\b")
UNKEPT_ASK = "keep it with `drive job ask {job}`, which prints the file its ask record names, since no file holds it until then"
ASK_FIELD = re.compile(r"^ASK:?[ \t]+([/~]\S*)[ \t]*$", re.MULTILINE)
PAUSED = "The recommended answer to this question is built and paused on your pick: tap it and the work ships; another pick rebuilds it first."
QUIET_FOR = timedelta(minutes=30)
CALL_STUCK_FOR = timedelta(minutes=5)
UNSTAFFED_FOR = timedelta(minutes=5)
UNSTARTED_FOR = timedelta(minutes=1)
RELAUNCH = timedelta(seconds=orca.LAUNCH_SECONDS)
TAIL_BYTES = 65536
COMPACT_PAST_TOKENS = 120_000
COMPACT_WHEN_QUIET = timedelta(minutes=10)
NOT_SLUG = re.compile(r"[^A-Za-z0-9]")
ABOUT = re.compile(r"\[(?P<order>o\d+) (?P<job>j\d+)\]$|^(?:FREE (?:\S+ )?j\d+|ORPHAN|DUE(?: j\d+)?|RECOVERED|TELL|JOB j\d+|LANE j\d+|STUCK j\d+) (?P<named>o\d+)\b")


@dataclass
class Watcher:
    name: str
    offset: int
    ticked_at: datetime
    project: str | None = None
    read_at: datetime | None = None
    failures: dict[str, int] = field(default_factory=dict)
    failure_said: dict[str, datetime] = field(default_factory=dict)
    page_shape: str | None = None
    cards: dict | None = None
    fresh: set[str] = field(default_factory=set)
    agent_gaps: dict[str, str | None] = field(default_factory=dict)
    made: list[str] = field(default_factory=list)
    unsent: dict[str, list[str]] = field(default_factory=dict)
    down: str | None = None
    dashboard_down: bool = False
    releasing: dict[str, subprocess.Popen] = field(default_factory=dict)
    released: set[str] = field(default_factory=set)

    @property
    def lane(self) -> str:
        """The cci lane its lines go to: root for the drive's own watch, the project's sub-coordinator's for a project's."""
        return state.project_lane(self.project) if self.project else "root"


def hears(watcher: Watcher, data: dict, order_id: str) -> bool:
    return state.hears(data, data["orders"][order_id], watcher.project)


def heard_job(watcher: Watcher, data: dict, job: dict) -> bool:
    """Whether the watch hears a job: one serving or carrying an order it hears."""
    return any(hears(watcher, data, order_id) for order_id in [job["order"], *job["carries"]])


def heard_orders(watcher: Watcher, data: dict) -> dict[str, dict]:
    return {order_id: order for order_id, order in state.open_orders(data).items() if hears(watcher, data, order_id)}


def read_failed(watcher: Watcher, source: str, error: Exception, moment: datetime) -> list[str]:
    """Retry a failed read on the next poll; say so once it fails three times running, then at most every ten minutes."""
    if watcher.failures.get(source, 0) + 1 < FAILURES_BEFORE_SAYING:
        watcher.read_at = None
    return failed(watcher, source, f"{source} read", error, moment)


def failed(watcher: Watcher, source: str, what: str, error: Exception, moment: datetime) -> list[str]:
    failures = watcher.failures[source] = watcher.failures.get(source, 0) + 1
    if failures < FAILURES_BEFORE_SAYING:
        return []
    said = watcher.failure_said.get(source)
    if said and moment - said < FAILURE_EVERY:
        return []
    watcher.failure_said[source] = moment
    return [f"WATCH {what} failed {failures} times in a row: {error}"]


def ids(pr: dict) -> str:
    return f"[{pr['order']} {pr['job']}]"


def pr_changes(pr: dict, read: dict) -> list[str]:
    fresh = github.live(read, pr)
    changes = []
    if fresh["head"] != pr["head"] and pr["head"]:
        changes.append(f"new head {fresh['head'][:7]}")
    if fresh["checks"] != pr["checks"] and fresh["checks"] in ("pass", "fail"):
        changes.append("checks passed" if fresh["checks"] == "pass" else "checks failed")
    if fresh["review"] != pr["review"] and fresh["review"] in ("approved", "changes"):
        changes.append("approved" if fresh["review"] == "approved" else "changes requested")
    if fresh.get("conflict") and not pr["conflict"]:
        changes.append("conflicts with its base")
    if fresh["state"] != pr["state"]:
        changes.append(fresh["state"])
    ejected = github.EJECTED_LABEL in read["labels"]
    if ejected and not pr["ejected"]:
        changes.append("ejected from the merge queue")
    pr.update(fresh, draft=read["isDraft"], merged_sha=read["mergeCommit"], ejected=ejected)
    return changes


def github_lines(watcher: Watcher, moment: datetime) -> list[str]:
    data = state.load(watcher.name)
    keys = [key for key in state.open_pr_keys(data) if hears(watcher, data, data["prs"][key]["order"])]
    watcher.fresh = set()
    if not keys:
        return []
    reads, refused = github.fetch(keys)
    lines = read_failed(watcher, "github", refused, moment) if refused else []
    if not refused:
        watcher.failures.pop("github", None)
    if not reads:
        return lines
    foreign = {}
    with state.edit(watcher.name) as data:
        for key, read in reads.items():
            pr = data["prs"][key]
            changes = pr_changes(pr, read)
            pr["required"]["read"] = state.stamp(moment)
            if why := state.foreign_pr(data, key):
                foreign[key] = (pr["url"], pr["author"], f"{why} {ids(pr)}")
                continue
            pr["updated"] = state.stamp(moment)
            lines.extend(f"PR {key}: {change} {ids(pr)}" for change in changes)
    watcher.fresh = set(reads) - set(foreign)
    return lines + dropped_lines(watcher, foreign, moment)


def pushed_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Settle each builder's PUSHED the watch hears once its GitHub read after the take finds the required checks of every PR it names: all green takes the READY it holds, its JOB line waking whoever acts on a READY; a red one ends the hold with a `READY refused` line per failed check, which goes back to a remote lane waiting for its next event and wakes the coordinator otherwise."""
    data = state.load(watcher.name)
    if not any(settles(watcher, data, job_id) for job_id in data["jobs"]):
        return []
    lines = []
    with state.edit(watcher.name) as data:
        for job_id, job in data["jobs"].items():
            if not (verdict := settles(watcher, data, job_id)):
                continue
            result, held = verdict
            if result == "pass":
                state.take_result(data, job_id, "READY" + job["said"].removeprefix("PUSHED"), state.report_text(watcher.name, job_id))
                lines.append(f"JOB {job_id} {job['order']} {job['said']}{stage.heads(data, job['said'], job['order'], watcher.cards)}")
                continue
            job.update(said="READY refused: " + "; ".join(f"{key}'s {why}" for key, why in held), updated=state.stamp(moment))
            lines += [f"PR {key}: READY refused, its {why} [{job['order']} {job_id}]" for key, why in held]
    return lines


def settles(watcher: Watcher, data: dict, job_id: str) -> tuple[str, list[tuple[str, str]]] | None:
    job = data["jobs"][job_id]
    if job["kind"] != "builder" or job["state"] != "waiting" or not state.PUSHED_LINE.match(job["said"] or "") or not heard_job(watcher, data, job):
        return None
    verdict = state.pushed_verdict(data, job_id)
    return verdict if verdict[0] != "pending" else None


def dropped_lines(watcher: Watcher, foreign: dict[str, tuple[str, str, str]], moment: datetime) -> list[str]:
    """Stop tracking each PR someone else opened that no order of his it serves names, with nothing done on GitHub: each open card of his carrying it withdrawn first, saying who opened it, then the PR dropped from the drive; one whose withdrawal fails stays, refused everywhere, and goes again next read."""
    lines, gone = [], []
    for key, (url, author, why) in foreign.items():
        try:
            withdrawn = slack.withdraw_pr_card(url, f"Dropped from the drive: {author} opened it, and the drive tracks no one else's PR unless your order names it")
        except slack.SlackError as error:
            lines += failed(watcher, "foreign drop", "withdrawal of a dropped PR's card", error, moment)
            continue
        watcher.failures.pop("foreign drop", None)
        gone.append(key)
        lines += [f"DROPPED {key}: {why}", *(f"CARD {link} withdrawn: {key} is dropped" for link in withdrawn)]
    if gone:
        with state.edit(watcher.name) as data:
            for key in gone:
                state.drop_pr(data, key)
    return lines


def bare(wait: str) -> str:
    return wait.split("?")[0]


def waits_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Drop each wait of a job or PR that ended, his card answered, the job finished, the release out or a builder slot on this Mac given to the earliest due builder waiting its turn, one per slot no builder holds, and say once, as its last wait ends, that nothing holds it, holding back the job's ORPHAN for 30 minutes, but for a builder's READY or PUSHED whose PRs all merged, which the watch finishes next in the same pass, or else says once as an ORPHAN naming who finishes it; a builder given its slot waits on nothing with no agent yet, its slot stamped with when its turn came, and holds that slot until its agent starts. The cards a job asked him on hold it too, so it keeps its waits until he answers them. His open card for a PR whose waits ended is redrawn in place, so it names only the waits that still hold. The release that ships the last release wait of a job or PR on an open incident's fix is kept on the incident as its fix live, by the watch dropping that wait."""
    data = state.load(watcher.name)
    out, lines = shipped(watcher, data, moment)
    turns, held = place.turns(data, moment)
    turned = {job_id for job_id in turns if heard_job(watcher, data, data["jobs"][job_id])}
    owners = [
        *(job for job in data["jobs"].values() if heard_job(watcher, data, job) and not view.open_cards(job)),
        *(pr for pr in data["prs"].values() if hears(watcher, data, pr["order"])),
    ]
    if not out and not turned and all(view.holding(data, owner["waits"], watcher.cards) == owner["waits"] for owner in owners):
        return lines
    slot = {state.SLOT_WAIT: f"its turn came as a builder slot opened on this Mac, {held} of its {place.LOCAL_BUILDERS} held"}
    with state.edit(watcher.name) as data:
        for job_id, job in data["jobs"].items():
            if not heard_job(watcher, data, job) or view.open_cards(job):
                continue
            fixing(data, [job["order"], *job["carries"]], job, job, out)
            turn = job_id in turned and job["state"] == "waiting" and state.SLOT_WAIT in job["waits"]
            if (ended := released(data, job, watcher.cards, out | slot if turn else out)) and job["state"] not in state.FINISHED_JOB_STATES and not merged_ready(data, job_id):
                job["updated"] = state.stamp(moment)
                if turn:
                    job["slot"] = state.stamp(moment)
                for order_id in [job["order"], *job["carries"]]:
                    parts = data["orders"][order_id]["parts"]
                    for part in parts.keys() & set(state.job_parts(job, order_id)):
                        parts[part]["orphan_said"] = state.stamp(moment)
                built = f", built on {job['branch']}" if job["state"] == "built" else ""
                unlaunched = f", not started yet: drive job launch {job_id} starts it" if job.get("where") == "remote" and not job.get("dispatch") else ""
                lines.append(f"FREE {job_id} {job['order']}: {ended}, nothing else holds it{built}{unlaunched}")
        redraws = []
        for key, pr in data["prs"].items():
            if not hears(watcher, data, pr["order"]):
                continue
            fixing(data, [pr["order"]], pr, data["jobs"][pr["job"]], out)
            waited = pr["waits"]
            if (ended := released(data, pr, watcher.cards, out)) and pr["state"] == "open":
                lines.append(f"FREE {key} {pr['job']} {pr['order']}: {ended}, nothing else holds it")
            if pr["waits"] != waited and pr["state"] == "open":
                redraws.append(pr["url"])
    for url in redraws:
        try:
            slack.redraw_pr_cards(url)
        except slack.SlackError as error:
            lines.append(f"WATCH his open card for {url} still names the waits that ended, since its redraw failed: {error}")
    return lines


def fixing(data: dict, order_ids: list[str], owner: dict, job: dict, out: dict[str, str]) -> None:
    """Keep on each open incident order whose fix the job carries, the PR's own job for a PR, the releases out that ship the last of the job's or PR's release waits as its fixed, the record that its fix is live, read under the lock that drops those waits, so whichever watch drops them keeps it."""
    shipping = [out[wait] for wait in owner["waits"] if wait in out]
    if not shipping or any(state.RELEASE_WAIT.fullmatch(wait) and wait not in out for wait in owner["waits"]):
        return
    for order_id in state.open_incidents(data, order_ids):
        if state.INCIDENT_FIX in state.job_parts(job, order_id):
            data["orders"][order_id]["fixed"] = ", ".join(shipping)


def monitors(data: dict, order_id: str) -> dict[str, str]:
    """The link of each Datadog monitor recorded on the orders of an order's incident, by its id."""
    links = (link for other in state.incident_orders(data, order_id) for link in data["orders"][other]["alerts"])
    return {found[1]: link for link in links if (found := state.DATADOG_MONITOR.fullmatch(link))}


def recovered_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Close each open incident the watch hears once its fix is live and every Datadog monitor recorded on it reads OK, the monitors of them all read in one search, and one with no monitor at once: what is left of it moves to a plain order, and a POST report hands the communicator the Live line for its thread, whose handler closes each order of it."""
    data = state.load(watcher.name)
    fixed = {
        head: monitors(data, head)
        for head in state.open_incidents(data, list(data["orders"]))
        if data["orders"][head]["fixed"] and not data["orders"][head]["joins"] and not data["orders"][head]["recovered"] and hears(watcher, data, head)
    }
    lines, ok = [], set()
    if ids := sorted({monitor for found in fixed.values() for monitor in found}):
        argv = ["pup", "--no-agent", "monitors", "search", "--query", f"id:({' OR '.join(ids)})", "--per-page", str(len(ids)), "--jq", ".monitors | map({id, status})"]
        try:
            read = json.loads(reads.run(argv, Path(data["roles"]["coordinator"]["cwd"])))
        except reads.ReadError as error:
            lines = read_failed(watcher, "datadog monitors", error, moment)
        else:
            watcher.failures.pop("datadog monitors", None)
            ok = {str(monitor["id"]) for monitor in read if monitor["status"] == "OK"}
    for head, found in fixed.items():
        if found.keys() <= ok:
            lines += closed(watcher, head, list(found.values()), moment)
    return lines


def close_report_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Post in one cci batch each close report drive job prove, or the watch's DONE at a merge, keeps on an open order it finished, for the communicator's handler to post in the order's thread and close it; each stays on its order until cci takes it, so a post that fails goes again next read, said from its third failure in a row, and a watch on a newer install posts it as well."""
    data = state.load(watcher.name)
    owed = {order_id: order["close_report"] for order_id, order in heard_orders(watcher, data).items() if order["close_report"]}
    if not owed:
        return []
    from drive import tools

    try:
        tools.sent(watcher.name, LANE, list(owed.values()))
    except state.DriveError as error:
        return failed(watcher, "close reports", "close report post to the communicator", error, moment)
    watcher.failures.pop("close reports", None)
    with state.edit(watcher.name) as data:
        for order_id in owed:
            data["orders"][order_id]["close_report"] = None
    return []


def closed(watcher: Watcher, head: str, links: list[str], moment: datetime) -> list[str]:
    """Close an incident that is over: its live work moves to a plain order and each of its orders keeps the recovery of its monitors, when it has any, and its fix live as its proof, then a POST report names each order for the communicator's handler to post in its thread and close; a post that fails goes again next read."""
    read_ok = f"{', '.join(links)} read OK"
    with state.edit(watcher.name) as data:
        group = state.open_incidents(data, state.incident_orders(data, head))
        incident = data["orders"][head]
        follow = follow_up(data, head, group, state.stamp(moment))
        for order_id in group:
            data["orders"][order_id]["proof"] = data["orders"][order_id]["proof"] or "; ".join([*([f"{read_ok} at {when(state.stamp(moment))}"] if links else []), incident["fixed"]])
    pr = state.PR_KEY.search(incident["fixed"])
    facts = [
        f"Incident: {incident['text']}",
        *([f"Recovered: {read_ok}"] if links else []),
        f"Fix live: {incident['fixed']}" + (f", {state.pr_url(pr[0])}" if pr else ""),
        *([f"Note: {incident['note']}"] if incident["note"] else []),
    ]
    record = {
        "kind": "report",
        "to": [f"communicator@{watcher.name}"],
        "topic": f"watch-recovered-{head}",
        "attr": {"close": ",".join(group)} | ({"thread": incident["thread"][1]} if incident["thread"] else {}),
        "text": f"POST incident {head} is over: {'its monitors read OK and ' if links else ''}its fix is live; post the Live line in its thread",
        "body": "\n".join(facts) + "\n",
    }
    from drive import tools

    try:
        tools.sent(watcher.name, LANE, [record])
    except state.DriveError as error:
        return failed(watcher, f"close of {head}", f"close report post of {head} to the communicator", error, moment)
    watcher.failures.pop(f"close of {head}", None)
    with state.edit(watcher.name) as data:
        for order_id in group:
            data["orders"][order_id]["recovered"] = state.stamp(moment)
    moved = f"; its live jobs, open PRs and parts left moved to the plain order {follow}" if follow else ""
    return [f"RECOVERED {head}: {f'{read_ok} and ' if links else ''}{incident['fixed']}, so the communicator posts its Live line and closes {', '.join(group)}{moved}"]


def follow_up(data: dict, head: str, group: list[str], created: str) -> str | None:
    """Move what is left of an incident that is over to a plain build order in its thread, so closing it leaves no work on it: each open PR of its orders and that PR's job, each part of its orders beyond the incident's own not yet delivered, and each live job carrying one; that order's id, also on the head's note, or None when nothing is left."""
    own = {state.INCIDENT_FIX, *(part for parts in state.INCIDENT_PARTS.values() for part in parts)}
    incident = data["orders"][head]
    left = {other: [part for part in data["orders"][other]["parts"] if part not in own and not state.delivered(data, other, part)] for other in group}
    prs = [pr for pr in state.open_prs(data) if pr["order"] in group]
    live = [
        job_id
        for job_id, job in data["jobs"].items()
        if job["state"] not in state.FINISHED_JOB_STATES and any(set(state.job_parts(job, other)) & set(names) for other, names in left.items())
    ]
    jobs = list(dict.fromkeys([*(pr["job"] for pr in prs), *live]))
    if not (any(left.values()) or jobs):
        return None
    order_id = state.next_id(data["orders"], "o")
    parts = {part: data["orders"][other]["parts"].pop(part) for other, names in left.items() for part in names}
    follow = data["orders"][order_id] = state.new_order(incident["text"], incident["from"], state.promise_thread(incident), parts, created) | {
        "owner": incident["owner"],
        "project": incident["project"],
        "workstream": incident["workstream"],
        "title": incident["title"],
        "note": f"follow-up of incident {head}",
    }
    for job_id in jobs:
        job = data["jobs"][job_id]
        served = list(dict.fromkeys(part for other in group for part in state.job_parts(job, other)))
        for other in group:
            job["carries"].pop(other, None)
            if job_id in data["orders"][other]["jobs"]:
                data["orders"][other]["jobs"].remove(job_id)
        if job["order"] in group:
            job["order"], job["parts"] = order_id, served
        else:
            job["carries"][order_id] = served
        follow["jobs"].append(job_id)
        follow["parts"] |= {part: state.new_part() for part in served if part not in follow["parts"]}
    for pr in prs:
        pr["order"] = order_id
    incident["note"] = "; ".join(filter(None, [incident["note"], f"follow-up {order_id}"]))
    return order_id


def settle_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Settle in place each open PR card whose every PR merged or closed, its buttons replaced by how each ended so a late tap records nothing; one that fails goes again next read. cc-slack withdraws every open card carrying the PR it is given, so a card goes through a PR no card with a PR still open carries, the one the fewest cards carry first, by the watch hearing that PR's order."""
    if watcher.cards is None:
        return []
    data = state.load(watcher.name)
    prs = {pr["url"]: (key, pr) for key, pr in data["prs"].items()}
    cards = watcher.cards["pr_cards"]
    carriers = {url: [card for card in cards if url in card["prs"]] for card in cards for url in card["prs"]}
    ended = [card for card in cards if all(url in prs and prs[url][1]["state"] != "open" for url in card["prs"])]
    routes = sorted(
        (
            (url, card)
            for card in ended
            for url in card["prs"]
            if all(other in ended for other in carriers[url]) and hears(watcher, data, prs[url][1]["order"])
        ),
        key=lambda route: len(carriers[route[0]]),
    )
    done: set[str] = set()
    lines = []
    for url, card in routes:
        if view.message_key(card["url"]) in done:
            continue
        done.add(view.message_key(card["url"]))
        reason = ", ".join(f"{prs[pr][0]} {prs[pr][1]['state']}" for pr in card["prs"])
        try:
            withdrawn = slack.withdraw_pr_card(url, reason)
        except slack.SlackError as error:
            lines += failed(watcher, "card settle", "PR card settle", error, moment)
            continue
        watcher.failures.pop("card settle", None)
        done |= {view.message_key(link) for link in withdrawn}
        lines += [f"CARD {link} settled in place: {reason}" for link in withdrawn]
    return lines


def follow_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Close in place each of his question cards still showing buttons whose every question ended, showing how the newest ended, since the drive's question decides its card: a close cc-slack missed when the question ended goes again here, and one that fails goes again next read."""
    if watcher.cards is None:
        return []
    data = state.load(watcher.name)
    lines = []
    for card in watcher.cards["questions"]:
        drawn = view.drawn_on(data, *filter(None, [card["card"].get("waits"), card["url"]]))
        if not drawn or any(question["closed"] is None for _, question in drawn):
            continue
        closed = max((question["closed"] for _, question in drawn), key=lambda closed: closed["at"])
        try:
            superseded.close_card(card["url"], closed)
        except slack.SlackError as error:
            lines += failed(watcher, "card follow", "question card follow", error, moment)
            continue
        watcher.failures.pop("card follow", None)
        lines.append(f"CARD {card['url']} closed in place: its question {closed['how']}")
    return lines


def checks(watcher: Watcher, moment: datetime) -> None:
    """Start the check against his words of each PR the watch hears that owes one, within one GitHub read of finding it unchecked and with no coordinator turn: its CHECKED line redraws his card in place, so his Approve of a changed patch lands it once it is green, and its NOT READY line goes to its builder."""
    if watcher.cards is None:
        return
    data = state.load(watcher.name)
    if owed := [key for key, pr in data["prs"].items() if hears(watcher, data, pr["order"]) and check_owed(watcher.name, key, pr, data, watcher.cards, moment)]:
        served.start(watcher.name, owed)


def check_owed(name: str, key: str, pr: dict, data: dict, cards: dict, moment: datetime) -> bool:
    """Whether a PR owes the check of its change against his words: open and waiting on his card, its change at its head not checked, its builder's report no port of an old-tree PR, which the coordinator checks against that PR, and no check of it running or done at this head since its builder last returned. It runs on every head whatever its job's state or its waits, its objection a line on his card and a wake of its builder, and his Approve lands the PR whatever it says."""
    if pr["state"] != "open" or not stage.carded(pr) or state.checked(data, pr):
        return False
    return not served.ported(state.report_text(name, pr["job"])) and not served.tried(pr, data["jobs"][pr["job"]]["returned"], moment)


def waiting_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Tell him once, in one reply in the card's own thread naming no card, the minute a card of his starts holding a job built on its recommended option. Each wait is claimed under the lock before its post with the pid and start time of the watch claiming it, so no other watch posts it too, and is said once the post goes through; a post that fails gives its claims back and goes again next read, and a claim whose watch died before saying it is taken again, its pid running another process since or none, so a watch dying between its post and saying it is the one way he is told twice."""
    if watcher.cards is None:
        return []
    from drive import talk

    if not unsaid_waits(watcher, state.load(watcher.name)):
        return []
    cards: dict[str, list[dict]] = {}
    claimer = {"pid": os.getpid(), "started": started(os.getpid())}
    with state.edit(watcher.name) as data:
        for entry in unsaid_waits(watcher, data):
            data["jobs"][entry["job"]]["waiting_claims"][entry["wait"]] = claimer
            cards.setdefault(entry["card"]["url"], []).append(entry)
    lines = []
    for url, entries in cards.items():
        try:
            reply = talk.posted(watcher.name, {"text": PAUSED, "thread_ts": view.card_thread(url)})
        except (state.DriveError, slack.SlackError) as error:
            with state.edit(watcher.name) as data:
                for entry in entries:
                    data["jobs"][entry["job"]]["waiting_claims"].pop(entry["wait"])
            lines += failed(watcher, "waiting post", "built-and-paused reply", error, moment)
            continue
        with state.edit(watcher.name) as data:
            for entry in entries:
                job = data["jobs"][entry["job"]]
                job["waiting_claims"].pop(entry["wait"])
                job["waiting_said"].append(entry["wait"])
        watcher.failures.pop("waiting post", None)
        lines += [
            f"WAITING-ON-HIM {entry['wait']} blocks {entry['job']}, built on {entry['branch']}, since {when(entry['card']['asked'])}, posted {reply['permalink']}"
            for entry in entries
        ]
    return lines


def unsaid_waits(watcher: Watcher, data: dict) -> list[dict]:
    return [
        entry
        for entry in view.waiting_on_him(data, watcher.cards)
        if hears(watcher, data, entry["order"])
        and entry["wait"] not in (job := data["jobs"][entry["job"]])["waiting_said"]
        and not ((claimer := job["waiting_claims"].get(entry["wait"])) and started(claimer["pid"]) == claimer["started"])
    ]


def started(pid: int) -> str | None:
    """When the process holding pid started, as ps prints it, which a pid the system hands to another process changes; None when no process holds it."""
    return subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True, check=False).stdout.strip() or None


def shipped(watcher: Watcher, data: dict, moment: datetime) -> tuple[dict[str, str], list[str]]:
    """Each release wait of a live job or open PR whose release is out, with what shipped it, read from GitHub: the newest release that ships the service holds the PR's merge commit."""
    waits = {
        wait
        for order_ids, owner in release_owners(data)
        if any(hears(watcher, data, order_id) for order_id in order_ids)
        for wait in owner["waits"]
        if state.RELEASE_WAIT.fullmatch(wait)
    }
    out = {}
    for wait in sorted(waits):
        try:
            shipping = view.shipped_by(data, wait)
        except github.GithubError as error:
            return {}, read_failed(watcher, "github releases", error, moment)
        if shipping:
            out[wait] = shipping
    watcher.failures.pop("github releases", None)
    return out, []


def release_owners(data: dict) -> list[tuple[list[str], dict]]:
    """Each live job and open PR with the orders it serves: a job's own and those it carries, a PR's own."""
    return [
        *(([job["order"], *job["carries"]], job) for job in data["jobs"].values() if job["state"] not in state.FINISHED_JOB_STATES),
        *(([pr["order"]], pr) for pr in state.open_prs(data)),
    ]


def released(data: dict, owner: dict, cards: dict | None, out: dict[str, str]) -> str:
    """Drop the waits of a job or PR that ended; what ended, when nothing else holds it."""
    holds = [wait for wait in view.holding(data, owner["waits"], cards) if wait not in out]
    ended = [wait for wait in owner["waits"] if wait not in holds]
    state.rewait(owner, holds)
    if holds or not ended:
        return ""
    answered = [bare(wait) for wait in ended if wait not in out]
    return ", ".join([*([f"{', '.join(answered)} answered or done"] if answered else []), *(out[wait] for wait in ended if wait in out)])


def minutes(span: timedelta) -> int:
    return int(span.total_seconds() // 60)


def first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def mail_line(name: str, data: dict, job_id: str, message: dict, moment: datetime, kept: Path | None, taken: bool) -> str | None:
    """Record one message from a remote lane on its job, a final report ending any question the lane asked before it and setting its state unless state.result_refusal refuses it or it types a clock time for work already done, which the event line names, and a question opening with a result other than ASK being that result; the event line it wakes the coordinator with, if any, which for a question naming `LOG <url>`s says drive job resume reads them ahead of the answer, for an `ASK <file>` naming the block `drive job ask` kept is that line, as a job's own return is, and for any other `ASK` says that `drive job ask` keeps it. A report `drive job report` already took, as a lane keeps it before its worker_done ends its turn, only ends that question, since its line went with that command's answer; a report the watch takes itself names each PR it links that the drive does not track, since only `drive pr add` registers one."""
    job = data["jobs"][job_id]
    kind = message["type"]
    body = message["body"] or ""
    job["updated"] = state.stamp(moment)
    if kind == "escalation":
        last = first_line(body.split("Last output:")[-1])
        return f"JOB {job_id} {job['order']} STOPPED without a report, last output: {cut(last, 160)}"
    if taken:
        job.update(report=body, ask=None)
        return None
    if not kept:
        job["said"] = message["subject"] if kind == "worker_done" else first_line(body) or message["subject"]
    if kind == "status":
        return None
    job["report"] = body
    if kind == "question" and (kept or not state.reported(job["said"])):
        job.update(ask=message["id"], ask_at=job["updated"], state="waiting")
    elif kind in ("question", "worker_done"):
        if job.get("ask"):
            job.update(ask=None, state="running")
        if job["kind"] == "builder":
            job["said"] = re.sub(r"^READY\b", "PUSHED", job["said"])
        if refused := state.result_refusal(data, job_id, job["said"], body) or limits.typed_time(body, data):
            return f'JOB {job_id} {job["order"]} {job["said"]}; refused, its state unset: {refused}; drive job resume {job_id} "<the refusal>" sends it back'
        state.take_result(data, job_id, job["said"], body)
        state.add_changes(data, job_id, body)
        if untracked := [url for url in dict.fromkeys(found[0] for line in body.splitlines() if PR_LINE.match(line) for found in state.PR_URL.finditer(line)) if state.pr_key(url) not in data["prs"]]:
            return f"JOB {job_id} {job['order']} {job['said']}; the drive tracks no {', '.join(untracked)}: drive pr add <url> --job {job_id} registers each"
        if state.PUSHED_LINE.match(job["said"]):
            return None
    if kept:
        return f"JOB {job_id} {job['order']} ASK {kept}"
    line = f"JOB {job_id} {job['order']} {job['said']}"
    if job.get("ask") != message["id"]:
        return line
    if asked := reads.logs(body):
        return f"{line}; drive job resume reads {asked} ahead of your answer"
    return f"{line}; {UNKEPT_ASK.format(job=job_id)}" if reads.asks(body) else line


def lane_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Each live remote lane's mail, from every worker of its job and not only the newest, read from its own Run and kept in its job's history: reports become JOB lines, read and `DRIVE` questions queue on their job to be answered here, an open question kept before the watch read it as a read included. Each job keeps the sequence of the newest of its messages taken, judged and moved under the lock, so a watch reads a Run back to the oldest mark of the jobs it hears there, takes only their mail and passes over the rest, and a job's mail no watch has taken waits for whichever watch hears it next. A message its job's history already keeps is never taken again, so a watch whose mark another install's save lost replays nothing."""
    queue_kept_reads(watcher)
    data = state.load(watcher.name)
    lanes = lane_handles(watcher, data)
    lines, messages = [], []
    kept = functools.cache(lambda job_id: {reply["id"] for reply in state.replies(watcher.name, job_id)})
    for run, handles in lanes.items():
        source = f"lane mail of {run}"
        try:
            messages += [(run, message) for message in orca.mail(run, min(data["jobs"][job_id]["mail_seen"] for job_id in handles.values()))]
        except orca.OrcaError as error:
            lines += read_failed(watcher, source, error, moment)
            continue
        watcher.failures.pop(source, None)
    asks = kept_asks(data, lanes, messages, kept)
    reported = set()
    if messages:
        with state.edit(watcher.name) as data:
            heard = lane_handles(watcher, data)
            for run, message in messages:
                job_id = sender(heard, run, message)
                if not job_id or message["sequence"] <= data["jobs"][job_id]["mail_seen"]:
                    continue
                data["jobs"][job_id]["mail_seen"] = message["sequence"]
                if message["type"] == "heartbeat" or message["id"] in kept(job_id):
                    continue
                state.keep_reply(watcher.name, job_id, message)
                if message["type"] == "question" and (question := reads.request(message["body"] or "")):
                    data["jobs"][job_id].setdefault("reads", {})[message["id"]] = question
                    continue
                taken = message["type"] == "worker_done" and kept_return(watcher.name, job_id, message["body"] or "")
                lines.append(mail_line(watcher.name, data, job_id, message, moment, asks.get(message["id"]), taken))
                if message["type"] in ("worker_done", "question") and not taken:
                    reported.add(job_id)
    watcher.made += [line for line in lines if line]
    return [line for line in merge_lane_prs(watcher, reported) + read_lines(watcher, data, moment) if line]


def kept_asks(data: dict, lanes: dict[str, dict[str | None, str]], messages: list[tuple[str, dict]], kept: Callable[[str], set[str]]) -> dict[str, Path]:
    """The file of each new `ASK <file>` question a remote lane asked naming a block `drive job ask` kept for its job, by its message id, as a job on this Mac returns `ASK <file>`; any other ASK goes on to the coordinator, whose `drive job ask <j>` keeps it."""
    asks = {}
    for run, message in messages:
        job_id = sender(lanes, run, message)
        if not job_id or message["sequence"] <= data["jobs"][job_id]["mail_seen"] or message["type"] != "question" or message["id"] in kept(job_id):
            continue
        if (asked := state.ASK_LINE.fullmatch(reads.opening(message["body"] or ""))) and state.kept_in(data["jobs"][job_id], Path(asked["file"]).expanduser()):
            asks[message["id"]] = Path(asked["file"]).expanduser()
    return asks


def kept_return(name: str, job_id: str, body: str) -> bool:
    """Whether this report is the newest `drive job report` took for the job, as a lane on a Sprite keeps it before its worker_done carries it."""
    returns = [reply["body"] for reply in state.replies(name, job_id) if reply["id"].startswith(state.REPORT_REPLY)]
    return bool(returns) and returns[-1].strip() == body.strip()


def queue_kept_reads(watcher: Watcher) -> None:
    """Queue each live lane's open question that asks a read, so a question kept as the coordinator's, such as one the watch read before it took a `LOG` with words after its URL, is answered here and its lane runs again."""
    data = state.load(watcher.name)
    if not any(kept_read(job) for job in live_lanes(watcher, data).values()):
        return
    with state.edit(watcher.name) as data:
        for job in live_lanes(watcher, data).values():
            if question := kept_read(job):
                job.setdefault("reads", {})[job["ask"]] = question
                job.update(ask=None, state="running")


def kept_read(job: dict) -> str | None:
    return reads.request(job["report"] or "") if job.get("ask") else None


def live_lanes(watcher: Watcher, data: dict) -> dict[str, dict]:
    return {
        job_id: job
        for job_id, job in data["jobs"].items()
        if job.get("where") == "remote" and job.get("dispatch") and job.get("run") and job["state"] in LIVE_JOB_STATES and heard_job(watcher, data, job)
    }


def lane_handles(watcher: Watcher, data: dict) -> dict[str, dict[str | None, str]]:
    """Whose each live lane's Run's mail is: a Run the drive keeps for one job alone maps None to that job, which takes the mail of every worker there, one the job never recorded included, such as the first of two workers two racing resumes started; a Run several jobs share, as drives kept lanes before each job got its own, maps each dispatch handle, the ones a job replaced included, to its job."""
    holders = collections.Counter(job.get("run") for job in data["jobs"].values())
    lanes: dict[str, dict[str | None, str]] = {}
    for job_id, job in live_lanes(watcher, data).items():
        if holders[job["run"]] == 1:
            lanes[job["run"]] = {None: job_id}
            continue
        for dispatch in [job["dispatch"], *(ended["dispatch"] for ended in job.get("deaths", []))]:
            lanes.setdefault(job["run"], {})[f"dispatch:{dispatch}"] = job_id
    return lanes


def sender(lanes: dict[str, dict[str | None, str]], run: str, message: dict) -> str | None:
    handles = lanes.get(run, {})
    return handles.get(None) or handles.get(message["from_handle"])


def merge_lane_prs(watcher: Watcher, job_ids: set[str]) -> list[str]:
    """Start the lander on each yasyf/* PR the reports taken from these lanes name, as `drive job report` does on this Mac, once their proof files are kept; a lander that does not start is the lane's JOB line, naming why."""
    from drive import cli

    data = state.load(watcher.name)
    return [
        f"JOB {job_id} {data['jobs'][job_id]['order']} PR {state.pr_url(key)}: {said}"
        for job_id in sorted(job_ids)
        for key, said in cli.merge_reported(watcher.name, job_id)
        if said.startswith(land.NOT_MERGING)
    ]


def read_lines(watcher: Watcher, data: dict, moment: datetime) -> list[str]:
    """Send each `LOG`, `READ` or `DRIVE` question a lane queued the answer `drive job read` keeps for it here, since a Sprite holds no CI credentials or drive records, starting that read as its own process for a live lane's question with neither an answer nor a reader still running, so a drive command that runs for minutes holds up no pass; one the read cannot answer gets why as its answer and a `JOB` line saying so. A lane's `drive job report` that took its return wakes the coordinator with its `JOB` line as its answer goes, as a job's own return does on this Mac, a `PUSHED` aside, which waits on its checks; its job may have left the live states by then, so a kept answer goes whatever its job's state. An answer goes, and a reader starts, only on what drive.json held once its reader was seen ended, so the cut-off answer a reader keeps before it runs never goes ahead of the one it finishes with, no reader that finished since the watch last read drive.json starts again, and a refused reply sends the kept answer again and never reruns the read. A question leaves its job's queue only once its answer goes through; with no line since nothing is owed on it, once Orca says it already holds another answer, since a lane whose wait ends waits again on the same question; or with one `JOB` line once Orca says the question's dispatch closed, since no lane waits on it then: any other refused reply only delays it."""
    running, data = ended_reads(watcher.name, data)
    for job_id, job in live_lanes(watcher, data).items():
        for message_id in job.get("reads", {}):
            if message_id not in job.get("read_answers", {}) and (job_id, message_id) not in running:
                reader = job.setdefault("readers", {})[message_id] = spawn_reader(watcher.name, job_id, message_id)
                with state.edit(watcher.name) as fresh:
                    fresh["jobs"][job_id].setdefault("readers", {})[message_id] = reader
    lines = []
    running, data = ended_reads(watcher.name, data)
    for job_id, job in data["jobs"].items():
        for message_id, question in job.get("reads", {}).items() if heard_job(watcher, data, job) else ():
            if (job_id, message_id) in running or not (kept := job.get("read_answers", {}).get(message_id)):
                continue
            said, why = kept
            source = f"the answer to {job_id}'s"
            try:
                orca.reply(watcher.name, job["run"], message_id, said)
            except orca.OrcaError as error:
                if error.code == orca.DISPATCH_CLOSED:
                    lines.append(f"JOB {job_id} {job['order']} READ dropped, its dispatch closed before its answer went: {error}")
                elif error.code != orca.ANSWERED_ELSEWHERE:
                    lines += read_failed(watcher, source, error, moment)
                    continue
            else:
                lines += [f"JOB {job_id} {job['order']} READ unanswered, its lane was told why: {why}"] if why else []
            if LANE_REPORT.match(question) and said.startswith(f"{reads.DRIVE_EXIT} 0\n") and job["said"] and not state.PUSHED_LINE.match(job["said"]):
                lines.append(f"JOB {job_id} {job['order']} {job['said']}")
            watcher.failures.pop(source, None)
            with state.edit(watcher.name) as fresh:
                for held in ("reads", "read_answers", "readers"):
                    fresh["jobs"][job_id].get(held, {}).pop(message_id, None)
    return lines


def ended_reads(name: str, data: dict) -> tuple[set[tuple[str, str]], dict]:
    """The reads in data a reader still runs on, by job and message id, then the drive read again after that check, so each other read's answer there is the one its reader finished with, since a reader keeps its final answer before it exits; data names every reader started so far."""
    running = {(job_id, message_id) for job_id, job in data["jobs"].items() for message_id in job.get("reads", {}) if reading(job, message_id)}
    return running, state.load(name)


def reading(job: dict, message_id: str) -> bool:
    """Whether a reader still runs on this question: its answer is not final yet and its process lives, so a reader the watch has not reaped yet holds no answer it already kept."""
    kept = job.get("read_answers", {}).get(message_id)
    return (not kept or kept[1] == CUT_OFF) and bool((reader := job.get("readers", {}).get(message_id)) and state.alive(reader))


def spawn_reader(name: str, job_id: str, message_id: str) -> int:
    """Run `drive job read` on one question a lane queued as its own process, outliving the pass that started it, its output appended to the drive's reads log."""
    return state.detach(name, ["job", "read", job_id, message_id], READS_LOG)


def answer_read(name: str, job_id: str, message_id: str) -> list[str | None]:
    """Run a lane's queued read here and keep its answer, and why it is no answer when it failed, under its message id, for the watch to send. It first keeps the answer a reader stopped mid-run leaves, so no read runs twice, a `drive break-glass` in production included, and none that crashes starts again."""
    data = state.load(name)
    question = data["jobs"][job_id]["reads"][message_id]
    keep_answer(name, job_id, message_id, [reads.lane_exit(1, CUT_OFF) if reads.DRIVE.match(question) else f"{question}: {CUT_OFF}", CUT_OFF])
    try:
        kept = [orca.sprite_answer(reads.answer(question, Path(data["roles"]["coordinator"]["cwd"]), name, job_id), data["jobs"][job_id], message_id), None]
    except reads.ReadError as error:
        kept = [f"{question}: {error}"] * 2
    keep_answer(name, job_id, message_id, kept)
    return kept


def keep_answer(name: str, job_id: str, message_id: str, kept: list[str | None]) -> None:
    with state.edit(name) as data:
        data["jobs"][job_id].setdefault("read_answers", {})[message_id] = kept


def page_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Redraw the drive's Slack status page when what it shows changed since the last drawing, its "Updated" line aside; say once, kept in the drive across watch restarts, when Slack starts refusing so the page he sees is stale, once when it takes a redraw again, and with the link whenever the page moved to a new tab."""
    if watcher.project:
        return []
    markdown = page.render(view.read(watcher.name, moment), dashboard.link(watcher.name))
    shape = markdown.split("\n", 1)[1]
    if shape == watcher.page_shape:
        return []
    stale = state.load(watcher.name)["slack"].get("page_stale")
    try:
        drawn = page.draw(watcher.name, markdown)
    except page.PageError as error:
        if stale:
            return []
        with state.edit(watcher.name) as data:
            data["slack"]["page_stale"] = state.stamp(moment)
        return [f"WATCH status page stale since {when(state.stamp(moment))}: {error}"]
    watcher.page_shape = shape
    if stale:
        with state.edit(watcher.name) as data:
            data["slack"].pop("page_stale")
    if drawn.new_tab:
        return [f"WATCH status page redrawn on a new tab: {drawn.url}"]
    if stale:
        return [f"WATCH status page redrawn: Slack takes its redraws again, after refusing them since {when(stale)}"]
    return []


def event_line(event: dict, prs: dict) -> str:
    key = f"{event.get('repo')}#{event.get('number')}"
    suffix = f" {ids(prs[key])}" if key in prs else ""
    if event["kind"] == "tap":
        return f"TAP {event['label']} {key}{suffix}"
    if event["kind"] in ("landing", "check", "proof"):
        return f"PR {key}: {event['change']}{suffix}"
    details = " ".join(f"{name}={value}" for name, value in event.items() if name not in ("kind", "at", "lane"))
    return f"EVENT {event['kind']} {details}".rstrip() + suffix


def event_lines(watcher: Watcher) -> list[str]:
    """Each new event the watch hears: one is written with the lane of the coordinator session hearing its order then, so a binding or a refiling after it moves it to no other watch, and one written with no lane is the drive's own watch's."""
    events = list(state.read_events(watcher.name, watcher.offset))
    if not events:
        return []
    prs = state.load(watcher.name)["prs"]
    said = [event_line(event, prs) for event, _ in events if event["kind"] not in view.HISTORY_ONLY and event.get("lane", "root") == watcher.lane]
    watcher.offset = events[-1][1]
    return said


def overdue(order: dict, moment: datetime, away: bool) -> bool:
    if order["state"] in state.CLOSED_ORDER_STATES or not state.owed(order) or state.parse_time(state.owed_at(order)) > moment:
        return False
    said = order["due_said"]
    return said is None or not away and moment - state.parse_time(said) >= DUE_EVERY


def due_lines(watcher: Watcher, moment: datetime) -> list[tuple[str, str]]:
    """Each promised time that passed with no drive post in its order's thread or one of its cards' since it was promised, read from Slack, again every ten minutes until one is there; only the first while he is away. An incident promises none, since quiet_lines owes its next line five quiet minutes after its last. An order with no thread has no line to read, so its time is the drive's own, said once and kept by being said."""
    data = state.load(watcher.name)
    away = state.owner_away(data, moment)
    threads = {
        order_id: slack.order_posts(data, order_id, order) if state.promise_thread(order) else None
        for order_id, order in heard_orders(watcher, data).items()
        if overdue(order, moment, away)
    }
    if not threads:
        return []
    lines = []
    with state.edit(watcher.name) as data:
        for order_id, read in threads.items():
            order = data["orders"][order_id]
            if read and overdue(order, moment, away):
                state.keep(order, read[0])
            if overdue(order, moment, away):
                order["due_said"] = state.stamp(moment)
                if read is None:
                    order["kept"] = order["due_said"]
                why = UNTHREADED if read is None else read[1] or f"no line in its thread since {when(order['promised'])}"
                lines.append((order_id, f'DUE {order_id} "{cut(order["text"], 80)}" was due {when(state.owed_at(order))}: {why}'))
    return lines


def quiet_since(data: dict, head: str) -> datetime:
    """When an open incident last had a line: the newest drive post on any order of it, else when it was opened, or when the watch's last report on it reached a handler, whichever is newer."""
    group = [data["orders"][order_id] for order_id in state.incident_orders(data, head)]
    owed = data["orders"][head]["quiet"]
    said = [order["last_line"] for order in group if order["last_line"]] or [data["orders"][head]["created"]]
    return max(map(state.parse_time, [*said, *([owed["at"]] if owed and owed["at"] else [])]))


def quiet_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Owe him a line in each thread of open incidents the watch hears whose fix is not live yet and that had none in five minutes, whatever its last line said and while he is away too, since he is in incident until it is fixed, by his pick "Watch-owned clock: no 10-minute silence again, a line every five quiet minutes from a handler reading the jobs; costs one handler session each": one report naming every such incident in the thread with its orders and jobs, written once, and one handler the watch starts on it, which reads the jobs and posts where each incident stands in that thread, so incidents sharing a thread get one line, not one each. A handler already at work in that thread takes the report at its next step. The clock restarts once a handler has the report, so one that cannot post owes him the next five minutes on; a failed write or start is said as a failed read is and tried again next pass with the same report."""
    data = state.load(watcher.name)
    threads: dict[tuple[str, str] | str, list[str]] = {}
    for head, order in heard_orders(watcher, data).items():
        if order["incident"] and not order["joins"] and not order["fixed"]:
            threads.setdefault(state.promise_thread(order) or head, []).append(head)
    lines = []
    for heads in threads.values():
        since = max(quiet_since(data, head) for head in heads)
        if moment - since < QUIET_INCIDENT:
            continue
        source = f"quiet {heads[0]}"
        owed = data["orders"][heads[0]]["quiet"]
        try:
            seq = owed["record"] if owed and not owed["at"] else quiet_report(watcher, data, heads, since, moment)
            with state.edit(watcher.name) as data:
                for head in heads:
                    data["orders"][head]["quiet"] = {"record": seq, "at": None, "handler": None}
            try:
                handler, _ = agents.handle(watcher.name, f"Record: {seq} Drive: {watcher.name} Scratch: {state.drive_dir(watcher.name) / 'scratch' / f'quiet-{seq}'}")
            except agents.Busy:
                handler = None
        except (reads.ReadError, state.DriveError) as error:
            lines += failed(watcher, source, f"line owed on quiet incident {', '.join(heads)}", error, moment)
            continue
        watcher.failures.pop(source, None)
        with state.edit(watcher.name) as data:
            for head in heads:
                data["orders"][head]["quiet"] = {"record": seq, "at": state.stamp(moment), "handler": handler}
    return lines


def quiet_report(watcher: Watcher, data: dict, heads: list[str], since: datetime, moment: datetime) -> str:
    """Write the report owing him a line on the quiet incidents of one thread, for the handler that posts it there; its seq."""
    minutes = int((moment - since).total_seconds() // 60)
    one = len(heads) == 1
    groups = {head: state.incident_orders(data, head) for head in heads}
    jobs = [f"- {render.job_line(job_id, data['jobs'][job_id])}" for group in groups.values() for order_id in group for job_id in data["orders"][order_id]["jobs"]]
    facts = [
        *(
            line
            for head, group in groups.items()
            for line in (f"Incident {head}, {', '.join(group)}: {data['orders'][head]['text']}", f"Evidence record: {data['orders'][head]['note'] or 'none'}")
        ),
        f"No line to him since {when(state.stamp(since))}, {minutes} minutes.",
        f"Read each job below, its kept report with drive job report <j> and the transcript of one still at work, then post where {'the' if one else 'each'} incident stands now in its thread: what each job found or is doing, a card of his that blocks it with the option built meanwhile, and what ships next.",
        f"Jobs: {len(jobs)}",
        *jobs,
    ]
    head = heads[0]
    incident = data["orders"][head]
    named = f"incident {head} has" if one else f"incidents {', '.join(heads)} have"
    record = {
        "kind": "report",
        "topic": f"watch-quiet-{head}",
        "attr": {"order": head, "progress": "true", "quiet": "true"} | ({"thread": incident["thread"][1]} if incident["thread"] else {}),
        "text": f"POST {named} had no line to him in {minutes} minutes: post where {'it stands' if one else 'each stands'} now in its thread",
        "body": "\n".join(facts) + "\n",
    }
    from drive import tools

    [seq] = tools.sent(watcher.name, LANE, [record])
    return seq


def owes_tell(order: dict, moment: datetime) -> bool:
    """Whether the bug report an order fixes is owed its fix-live note and the watch has not said so: the order keeps its proof, nothing moved on it for ten minutes, and no note is recorded."""
    reporter = order["reporter"]
    return bool(reporter and order["proof"] and not reporter["told"] and not reporter["tell_said"] and moment - state.parse_time(order["updated"]) >= TELL_AFTER)


def tell_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Each open order proven live whose bug report's thread has not heard it is fixed ten minutes on, said once."""
    data = state.load(watcher.name)
    if not (owing := [order_id for order_id, order in heard_orders(watcher, data).items() if owes_tell(order, moment)]):
        return []
    lines = []
    with state.edit(watcher.name) as data:
        for order_id in owing:
            reporter = data["orders"][order_id]["reporter"]
            reporter["tell_said"] = state.stamp(moment)
            lines.append(
                f"TELL {order_id}: its fix is proven live and nothing moved on it for 10 minutes, but {reporter['link']}, the thread its bug"
                f" was reported in, has not heard it is fixed: send its report with --attr close={order_id}, whose handler posts the fix-live"
                f" note there and records it with drive order told {order_id} <its permalink>; done refuses until then"
            )
    return lines


def ran_out(job: dict, moment: datetime) -> bool:
    return bool(job["due"]) and state.parse_time(job["due"]) <= moment


def clock_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Each job clock that ran out while its job still runs, said once; a job that returned before it owes none, and either way the clock ends."""
    data = state.load(watcher.name)
    if not any(ran_out(job, moment) and heard_job(watcher, data, job) for job in data["jobs"].values()):
        return []
    lines = []
    with state.edit(watcher.name) as data:
        for job_id, job in data["jobs"].items():
            if not ran_out(job, moment) or not heard_job(watcher, data, job):
                continue
            if job["state"] == "running":
                lines.append(
                    f'DUE {job_id} {job["order"]} "{cut(job["goal"], 80)}" was due {when(job["due"])} and still runs: read its transcript,'
                    " and on a miss start a second job on its brief and set its clock (incidents: Clocks)"
                )
            job["due"] = None
    return lines


def undelivered_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Each answer of his kept on a job not resumed since it came, ten minutes after it came and every ten minutes until drive job resume carries it."""
    if not any(owed_answers(watcher, state.load(watcher.name), moment)):
        return []
    lines = []
    with state.edit(watcher.name) as data:
        for job_id, answer in owed_answers(watcher, data, moment):
            answer["said"] = state.stamp(moment)
            lines.append(f'ANSWER undelivered {job_id}: {unreached(job_id, answer)} [{data["jobs"][job_id]["order"]} {job_id}]')
    return lines


def unreached(job_id: str, answer: dict) -> str:
    return f'his answer {answer["seq"]} "{cut(answer["words"], 80)}" of {when(answer["at"])} has not reached {job_id}; drive job resume {job_id} "<his answer>" carries it'


def owed_answers(watcher: Watcher, data: dict, moment: datetime) -> list[tuple[str, dict]]:
    """Each answer of his a live job holds that was last said ten minutes ago or more; a finished job's answer is its ORPHAN's, since no resume carries it."""
    return [
        (job_id, answer)
        for job_id, job in data["jobs"].items()
        if job["state"] not in state.FINISHED_JOB_STATES and heard_job(watcher, data, job)
        for answer in job["undelivered"]
        if moment - state.parse_time(answer["said"] or answer["at"]) >= DUE_EVERY
    ]


def answer_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Each answer of his that leaves a card still asking him or a PR not yet live with no mark, standing, settled, superseded or reopened by it, or a live job it touches not yet sent his words, again every ten minutes until each has one."""
    if watcher.cards is None or watcher.project:
        return []
    data = state.load(watcher.name)
    asked = superseded.asking(data, watcher.cards)
    owed = {
        answer_id: (superseded.unmarked(kept, asked), superseded.unheard(data, kept))
        for answer_id, kept in data["answers"].items()
        if moment - state.parse_time(kept["said"]) >= DUE_EVERY
    }
    owed = {answer_id: left for answer_id, left in owed.items() if any(left)}
    if not owed:
        return []
    with state.edit(watcher.name) as data:
        for answer_id in owed:
            data["answers"][answer_id]["said"] = state.stamp(moment)
    return [
        f'ANSWER {answer_id} "{cut(data["answers"][answer_id]["words"], 80)}": '
        + "; ".join(
            [
                *([f"{superseded.counted(keys, asked)} with no mark, standing, settled, superseded or reopened by it"] if keys else []),
                *([f"{', '.join(jobs)} not sent his words"] if jobs else []),
            ]
        )
        + f"; drive answer {answer_id} lists them"
        for answer_id, (keys, jobs) in owed.items()
    ]


def transcript(holder: dict, agent: str) -> Path:
    project = NOT_SLUG.sub("-", holder["cwd"])
    return config_dir() / "projects" / project / holder["session"] / "subagents" / f"agent-{agent}.jsonl"


def last_turn(path: Path) -> dict | None:
    with open(path, "rb") as handle:
        end = handle.seek(0, os.SEEK_END)
        span = TAIL_BYTES
        while True:
            start = max(0, end - span)
            handle.seek(start)
            for line in reversed(handle.read().splitlines()[1 if start else 0 :]):
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if entry.get("type") in ("user", "assistant"):
                    return entry
            if not start:
                return None
            span *= 4


def running_build(path: Path) -> tuple[str | None, str | None]:
    """The Claude Code version the session's newest API request reports, and the model its newest turn ran on."""
    version = model = None
    with open(path, "rb") as handle:
        end = handle.seek(0, os.SEEK_END)
        span = TAIL_BYTES
        while True:
            start = max(0, end - span)
            handle.seek(start)
            for line in reversed(handle.read().splitlines()[1 if start else 0 :]):
                if version is None and b'"api-request-shape"' in line:
                    version = json.loads(line).get("version")
                elif model is None and b'"type":"assistant"' in line.replace(b" ", b""):
                    model = json.loads(line)["message"].get("model")
                if version and model:
                    return version, model
            if not start:
                return version, model
            span *= 4


def unknown_model(version: str | None, model: str | None, builds: Path) -> str | None:
    """What his line says when the session's Claude Code build has no entry for its model, so it runs it at a default window."""
    binary = builds / version if version else None
    if not model or not binary or not binary.is_file():
        return None
    with open(binary, "rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as image:
        if image.find(model.encode()) >= 0:
            return None
    return f"runs {model} on Claude Code {version}, which has no entry for that model and runs it at a default context window: restart it on the installed build on his word"


def context_tokens(entry: dict) -> int:
    usage = entry["message"].get("usage") or {}
    return sum(usage.get(key, 0) for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))


def compact_due(data: dict, moment: datetime) -> str | None:
    """Why the communicator should compact now: past the mark, and both it and he quiet, so no message lands mid-compaction."""
    held = data["roles"].get("communicator")
    if not held or not held.get("terminal"):
        return None
    path = config_dir() / "projects" / NOT_SLUG.sub("-", held["cwd"]) / f"{held['session']}.jsonl"
    if not path.exists() or moment - datetime.fromtimestamp(path.stat().st_mtime).astimezone() < COMPACT_WHEN_QUIET:
        return None
    heard = (data.get("owner") or {}).get("heard")
    if heard and moment - state.parse_time(heard) < COMPACT_WHEN_QUIET:
        return None
    turn = last_turn(path)
    if not turn or not agents.ended(turn):
        return None
    tokens = context_tokens(turn)
    return f"at {tokens} tokens of context, quiet 10 minutes" if tokens >= COMPACT_PAST_TOKENS else None


def compact_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Type /compact into the communicator's Orca terminal at a quiet moment, never at its context limit mid-conversation; the drive's own watch alone does."""
    data = state.load(watcher.name)
    if watcher.project or not (why := compact_due(data, moment)):
        return []
    try:
        terminal = data["roles"]["communicator"]["terminal"]
        orca.request("terminal", "send", "--terminal", terminal, "--text", "/compact", "--enter")
    except orca.OrcaError as error:
        return read_failed(watcher, "communicator compaction", error, moment)
    watcher.failures.pop("communicator compaction", None)
    return [f"WATCH compacted the communicator {why}"]


def coordinator(data: dict, project: str | None) -> dict:
    """The session a watch posts to and speaks to Orca as: a project's sub-coordinator, or the coordinator."""
    return data["subcoordinators"][project] if project else data["roles"]["coordinator"]


def down_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Tell the communicator, once each time it exits, that the coordinator this watch posts to runs no Claude Code process, so nothing hears the drive's records: the communicator restarts it that minute, resuming its session, and never reports it to him as down."""
    held = coordinator(state.load(watcher.name), watcher.project)
    if orca.live_pid(held["session"]) is not None:
        watcher.down = None
        return []
    if watcher.down == held["session"]:
        return []
    watcher.down = held["session"]
    role = f"sub-coordinator for {watcher.project}" if watcher.project else "coordinator"
    line = (
        f"DOWN the {role}'s Claude Code process has exited, so nothing hears the drive's records: restart it now, with no word from"
        f" him, by steps 5 to 7 of drive manual migration, Into an Orca terminal, creating the terminal resuming {held['session']}"
        f" in {held['cwd']}, and tell him it was restarted, never that it is down"
    )
    return sent(watcher, "communicator", [line], moment)


def dashboard_lines(watcher: Watcher) -> list[str]:
    """Hand the drive's dashboard to its launch agent when no agent keeps it, as one started before the agent existed, telling the coordinator once each time it then does not start, a defect of the drive's code; a proof's drive and a project's watch leave it alone."""
    if watcher.project or state.scratch() or dashboard.kept(watcher.name):
        watcher.dashboard_down = False
        return []
    try:
        dashboard.start(watcher.name, dashboard.port_of(watcher.name))
    except state.DriveError as error:
        said, watcher.dashboard_down = watcher.dashboard_down, True
        return [] if said else [f"WATCH no launch agent kept the dashboard, and it did not start under one: {error}; its log is {state.drive_dir(watcher.name) / 'dashboard.log'}"]
    watcher.dashboard_down = False
    return []


def said_last(entry: dict) -> str:
    texts = [block["text"] for block in entry["message"]["content"] if block.get("type") == "text"]
    return first_line(texts[-1]) if texts else "nothing"


class Answered(str):
    """A running codex job's gap when its relay ended its turn after the codex-ask run it asked ended, carrying that run, which the watch takes as the job's result."""

    run: codex.Run

    def __new__(cls, job_id: str, run: codex.Run) -> Self:
        made = super().__new__(cls, f"{job_id}'s codex-ask run {run.state} and its result is not recorded")
        made.run = run
        return made

    def take(self, name: str, data: dict, job_id: str) -> None:
        codex.take(name, data, job_id, self.run)


def codex_asks(name: str, job_id: str) -> list[str]:
    """Keep the ASK block a codex job's reply names on its `ASK: <file>` line, or on an `ASK <file>` line it returned, as `drive job ask` keeps a builder's, in the pass its run is taken, so his card goes up as the lane returns while the recommended option it built ships, since a codex run is never resumed mid-run and an incident never stops on a choice; the JOB line waking the coordinator to send its ask record, the settled line when he already answered that same question, or why it was not kept."""
    from drive import cli

    job = state.load(name)["jobs"][job_id]
    if not (newest := state.returns(name, job_id)) or not (named := ASK_FIELD.search(newest[0]["body"])):
        return []
    head = f"JOB {job_id} {job['order']} {job['said']}"
    try:
        kept, _, _, decided = cli.keep_ask(name, job_id, Path(named[1]).expanduser().read_text(), None)
    except (OSError, state.DriveError, slack.SlackError) as error:
        return [f"{head}; its ASK: {named[1]} is not kept: {error}; keep it with drive job ask {job_id} {named[1]}"]
    if decided:
        return [f"{head}; {decided}"]
    return [f"{head}; its ASK is kept: send its ask record, path {kept}, and the recommended option it built ships meanwhile"]


def session_of(job: dict, data: dict) -> dict | None:
    """The bound session whose agent runs a local job."""
    session = job.get("session")
    return next((held for held in state.bound_sessions(data).values() if session and held["session"] == session), None)


def held_call(job_id: str, job: dict, data: dict, moment: datetime) -> tuple[str, str] | None:
    """A running local job's tool call that its transcript ends on with no result five minutes on and the watch has not said yet, with its STUCK line: Claude Code moves a Bash command still running at three minutes to the background, so a call that has no result by five never started, held by a permission prompt in a terminal nobody watches."""
    holder = session_of(job, data)
    if holder is None or not job.get("agent") or not (path := transcript(holder, job["agent"])).exists():
        return None
    turn = last_turn(path)
    calls = [block for block in turn["message"]["content"] if block.get("type") == "tool_use"] if turn and turn["type"] == "assistant" else []
    if not calls or calls[-1]["id"] == job.get("stuck_call") or (held := moment - state.parse_time(turn["timestamp"])) < CALL_STUCK_FOR:
        return None
    call = calls[-1]
    asked = first_line(call["input"].get("command") or call["input"].get("description") or json.dumps(call["input"]))
    return call["id"], (
        f'STUCK {job_id} {job["order"]}: its agent\'s {call["name"]} call "{cut(asked, 80)}" has had no result for {minutes(held)} minutes,'
        f" so a permission prompt nobody sees holds it: TaskStop {job['agent']}, then hand the job to a fresh agent whose brief runs that call as a scratch script"
    )


def local_gap(job_id: str, job: dict, data: dict, moment: datetime) -> str | None:
    holder = session_of(job, data)
    if holder is None:
        return f"{job_id}'s agent ended with an earlier session"
    if not job.get("agent"):
        return f"{job_id} has no agent recorded"
    path = agent_transcript(holder, job, data)
    if not path.exists():
        return f"{job_id}'s agent {job['agent']} has no transcript"
    quiet = moment - datetime.fromtimestamp(path.stat().st_mtime).astimezone()
    if quiet < HANDOFF:
        return None
    turn = last_turn(path)
    if turn and agents.ended(turn):
        line = said_last(turn)
        if job["kind"] == "codex":
            try:
                run = codex.newest_run(path)
            except codex.CodexError as error:
                return f"{job_id}'s codex-ask run could not be read: {cut(str(error), 100)}"
            if run:
                return Answered(job_id, run) if run.ended else None
        elif state.result_state(job, line):
            return (
                f'{job_id}\'s agent finished and its result is not recorded: "{cut(line, 100)}"; only drive job report {job_id} - sets it,'
                " or names why it refuses, so SendMessage from the session that started it wakes it to pipe its return there"
            )
        if quiet >= QUIET_FOR and agents.died(turn):
            return f'{job_id}\'s agent stopped {minutes(quiet)} minutes ago on {turn["message"]["stop_reason"]}: "{cut(line, 100)}"; drive job resume {job_id} <the event> relaunches it on a Sprite with a handoff'
        if quiet >= QUIET_FOR:
            missing = "codex-ask run" if job["kind"] == "codex" else "result line"
            return f'{job_id}\'s agent ended its turn {minutes(quiet)} minutes ago on "{cut(line, 100)}" with no {missing}: SendMessage from the session that started it wakes it, and drive job resume {job_id} <the event> from any other'
        return None
    if quiet >= QUIET_FOR:
        return f"{job_id}'s agent has written nothing for {minutes(quiet)} minutes"
    return None


def agent_death(job: dict, data: dict, moment: datetime) -> str | None:
    """Why a local job's agent takes no message: its last response ended on an API error or a refusal, and its transcript has been quiet the time a handoff takes; None while it works, after a turn it ended itself, or with no transcript to read."""
    holder = session_of(job, data)
    if holder is None or not job.get("agent") or not (path := agent_transcript(holder, job, data)).exists():
        return None
    if moment - datetime.fromtimestamp(path.stat().st_mtime).astimezone() < HANDOFF or not (turn := last_turn(path)) or not agents.died(turn):
        return None
    return f'its last response ended on {turn["message"]["stop_reason"]} at {when(turn["timestamp"])}: "{cut(said_last(turn), 100)}"'


def agent_transcript(holder: dict, job: dict, data: dict) -> Path:
    """A local job's agent's transcript: its handler's own session's, else the one beside the session holding it."""
    return Path(own) if (own := state.own_transcript(data, job)) else transcript(holder, job["agent"])


def last_write(job: dict, data: dict) -> tuple[datetime, dict | None]:
    """When a local job's agent last wrote its transcript, and the turn it wrote last."""
    path = agent_transcript(session_of(job, data), job, data)
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone(), last_turn(path)


def remote_gap(job_id: str, worker: dict, moment: datetime) -> str | None:
    projection = worker["projection"]
    if projection["outcome"] != "in_progress":
        if moment - state.parse_time(worker["dispatch"]["completedAt"]) < HANDOFF:
            return None
        return f"{job_id}'s lane {projection['outcome']} and its result is not recorded"
    output = (worker.get("terminal") or {}).get("lastOutputAt")
    quiet = output and moment - datetime.fromtimestamp(output / 1000).astimezone()
    if "input" in projection["attention"]["categories"] and quiet and quiet >= QUIET_FOR:
        return f"{job_id}'s lane has sat at its prompt for {minutes(quiet)} minutes"
    return None


def ended_gap(job_id: str, ended: dict, moment: datetime) -> str | None:
    """A running job whose lane died or finished and no next lane replaced in the time a relaunch takes: none while the watch's relaunch of it runs, and once that relaunch ended or overran, the line names it and its log."""
    gone = moment - state.parse_time(ended["at"])
    relaunch = ended.get("relaunch")
    if relaunch and state.alive(relaunch["pid"]) and moment - state.parse_time(relaunch["at"]) < RELAUNCH:
        return None
    if not relaunch:
        return None if gone < RELAUNCH else f"{job_id}'s lane ended {minutes(gone)} minutes ago ({cut(ended['why'], 60)}), was not resumed and its result is not recorded"
    return (
        f"{job_id}'s lane ended {minutes(gone)} minutes ago ({cut(ended['why'], 60)}) and the watch's drive job resume at"
        f" {when(relaunch['at'])} started no next lane, its output in the drive's {RELAUNCHES_LOG}"
    )


def launching(job: dict, moment: datetime) -> bool:
    """Whether the job's lane launch began within the time one takes, so a job with no dispatch yet is starting, not stuck."""
    return bool(job.get("launching")) and moment - state.parse_time(job["launching"]) < RELAUNCH


def agent_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Each running job's gap from its agent or lane, with each PR it has open, and a remote lane, running or waiting, whose worker died; a lane recorded by its dispatch alone gets its Run, Task and terminal from the worker-show read for its liveness, and a lane filed on this Mac whose worker-show names a Sprite is filed on that Sprite. A local agent that ended its turn on a result line its job never took is a gap, since only `drive job report` sets a job's state from its return; a codex job's relay that ended its turn has its codex-ask run taken once that run ended, so a relay that ended before codex answered needs no wake."""
    data = state.load(watcher.name)
    gaps, deaths, heads, found, taken, held, lines = {}, {}, {}, {}, {}, {}, []
    for order in heard_orders(watcher, data).values():
        for job_id in order["jobs"]:
            job = data["jobs"][job_id]
            running = job["state"] == "running"
            remote = job.get("where") == "remote"
            if running and job.get("branch") and (job.get("repo") or remote):
                heads.setdefault(job_id, set()).add((job.get("repo"), job["branch"]))
            if not remote:
                if running and (call := held_call(job_id, job, data, moment)):
                    held[job_id] = call
                gap = running and local_gap(job_id, job, data, moment)
                if isinstance(gap, Answered):
                    taken[job_id] = gap
                    heads.pop(job_id, None)
                elif running:
                    gaps[job_id] = gap
                continue
            if not job.get("dispatch"):
                if running:
                    gaps[job_id] = None if launching(job, moment) else f"{job_id} never started {orca.ON_MAC if state.on_mac(job) else 'on its Sprite'}"
                continue
            if job["state"] not in LIVE_JOB_STATES:
                continue
            if ended := state.lane_death(job):
                if running:
                    gaps[job_id] = ended_gap(job_id, ended, moment)
                continue
            source = f"lane of {job_id}"
            try:
                worker = orca.worker(job["dispatch"])
            except orca.OrcaError as error:
                if why := orca.gone(error):
                    deaths[job_id] = (job["dispatch"], why)
                    continue
                lines += read_failed(watcher, source, error, moment)
                continue
            watcher.failures.pop(source, None)
            lane = orca.placement(worker) if state.on_mac(job) else {}
            if not job.get("run"):
                lane |= orca.run_of(worker)
            if any(job.get(field) != value for field, value in lane.items()):
                found[job_id] = lane
            if why := orca.death(worker):
                deaths[job_id] = (job["dispatch"], why)
            elif running:
                gaps[job_id] = remote_gap(job_id, worker, moment)
                if branch := ((worker.get("terminal") or {}).get("branch") or "").removeprefix("refs/heads/"):
                    heads.setdefault(job_id, set()).add((job.get("repo"), branch))
    if found or taken or held:
        with state.edit(watcher.name) as data:
            for job_id, lane in found.items():
                data["jobs"][job_id].update(lane)
            for job_id, gap in taken.items():
                gap.take(watcher.name, data, job_id)
            for job_id, (call, line) in held.items():
                data["jobs"][job_id]["stuck_call"] = call
                lines.append(line)
    lines += [line for job_id in taken for line in codex_asks(watcher.name, job_id)]
    watcher.made += lines
    unlisted, failed = untracked_prs(watcher, data, heads, moment)
    watcher.agent_gaps = {job_id: stranded(job_id, data, gaps.get(job_id), unlisted.get(job_id, {})) for job_id in gaps.keys() | heads.keys()}
    return failed + death_lines(watcher.name, deaths, moment)


def untracked_prs(watcher: Watcher, data: dict, heads: dict[str, set], moment: datetime) -> tuple[dict[str, dict], list[str]]:
    """For each running job, the open PRs on its recorded branch or its lane's that the drive does not track, read from GitHub in one batch; a head with no repo is in the repo of the checkout remote lanes clone."""
    if not heads:
        return {}, []
    try:
        home = github.checkout_repo(data["roles"]["coordinator"]["cwd"]) if any(repo is None for both in heads.values() for repo, _ in both) else None
        resolved = {job_id: {(repo or home, branch) for repo, branch in both} for job_id, both in heads.items()}
        found = github.open_on(sorted(set().union(*resolved.values())))
    except github.GithubError as error:
        return {}, read_failed(watcher, "github branches", error, moment)
    watcher.failures.pop("github branches", None)
    return {
        job_id: {key: pr for key, pr in found.items() if key not in data["prs"] and (github.repo_of(key), pr["branch"]) in both}
        for job_id, both in resolved.items()
    }, []


def stranded(job_id: str, data: dict, gap: str | None, unlisted: dict[str, dict]) -> str | None:
    """A running job's gap from its agent or lane, or an open PR of its the drive does not track, with the checks and review of every PR it has open."""
    if not gap and not unlisted:
        return None
    tracked = [
        f"its PR {key}: checks {pr['checks']}, review {pr['review']}"
        for key in map(state.pr_key, data["jobs"][job_id]["prs"])
        if (pr := data["prs"].get(key)) and pr["state"] == "open"
    ]
    untracked = [f"{key} on its branch {pr['branch']} is not in the drive: checks {pr['checks']}, review {pr['review']}" for key, pr in unlisted.items()]
    return "; ".join([gap or f"{job_id} is running", *tracked, *untracked])


def release_lanes(watcher: Watcher, moment: datetime) -> None:
    """Free what each job it hears is done with still holds, never what an unfinished job holds: each Sprite cc-remote still records that a done, failed or stopped job ran on, or that an earlier lane of any job died on, such as the Sprite a relaunch moved off, destroyed with its Orca environment by --destroy once it holds nothing on no remote; and the dispatch of a done, failed or stopped lane on this Mac once its job has been quiet for QUIET_LANE, as lr's gc --done waited after a lane's last word, whose terminal --run --done closes and whose worktree it removes once clean and pushed, the reclaimer skipping whatever its pauses hold. Each goes with the reclaimer in a process of its own run from the drive's checkout, moved to its default branch's head first, at most two at once and each once a watch; what each run printed goes to the watch log, and a pass whose move fails frees nothing, saying why there."""
    if state.scratch():
        return
    watcher.releasing = {held: run for held, run in watcher.releasing.items() if run.poll() is None}
    data = state.load(watcher.name)
    live = {job.get(key) for job in data["jobs"].values() if job["state"] not in state.FINISHED_JOB_STATES for key in ("workspace", "run", "dispatch")} - {None}
    for job in data["jobs"].values():
        if not heard_job(watcher, data, job):
            continue
        for held in sorted(lane_holds(job, moment, live) - watcher.released - live):
            if len(watcher.releasing) >= RELEASES_AT_ONCE:
                return
            with state.watch_log_path(watcher.name, watcher.project).open("a") as log:
                try:
                    checkout = orca.checkout(watcher.name, data)
                except state.DriveError as error:
                    log.write(f"{held} is kept until a later pass: {error}\n")
                    return
                watcher.released.add(held)
                watcher.releasing[held] = orca.reclaim_detached(job["run"], held, checkout, log) if state.on_mac(job) else orca.release_detached(held, checkout, log)


def lane_holds(job: dict, moment: datetime, live: set[str]) -> set[str]:
    """What the job's lanes left for the reclaimer: on this Mac, the dispatch of a done, failed or stopped job quiet for QUIET_LANE whose Run no unfinished job shares; on a Sprite, each Sprite cc-remote still records that a lane of its died on, and its own once it is done, failed or stopped."""
    if state.on_mac(job):
        quiet = job["state"] in state.FINISHED_JOB_STATES and job.get("dispatch") and job.get("run") not in live | {None} and moment - state.parse_time(job["updated"]) >= QUIET_LANE
        return {job["dispatch"]} if quiet else set()
    left = {death.get("workspace") for death in job.get("deaths", [])}
    if job["state"] in state.FINISHED_JOB_STATES:
        left.add(job.get("workspace"))
    return {workspace for workspace in left - {None} if orca.holds_sprite({**job, "workspace": workspace})}


def death_lines(name: str, deaths: dict[str, tuple[str, str]], moment: datetime) -> list[str]:
    """Record each remote lane's death on its job and say it once. A running job's lane is relaunched that pass by `drive job resume` as a process of its own, its pid kept on the death so no later pass starts a second; a waiting job's Sprite is freed by `drive job end` in a process of its own, and the job waits for the coordinator's resume with his answer; a waiting job's lane on this Mac just waits."""
    if not deaths:
        return []
    lines = []
    with state.edit(name) as data:
        for job_id, (dispatch, why) in deaths.items():
            job = data["jobs"][job_id]
            if not state.lane_died(job, dispatch, why, moment):
                continue
            waiting = job["state"] == "waiting"
            died = f'LANE {job_id} {job["order"]} died {orca.place(job)}: {why}, while it {"waited on" if waiting else "worked on"} "{cut(job["said"] or job["goal"], 60)}". '
            fresh = f'{"in a fresh worktree " + orca.ON_MAC if state.on_mac(job) else "on a fresh Sprite"} with a handoff'
            if waiting and state.on_mac(job):
                lines.append(f'{died}A message sent to it reaches no one; drive job resume {job_id} "<his answer>" relaunches it {fresh}')
                continue
            if waiting:
                job["deaths"][-1]["ended"] = {"pid": end_lane(name, job_id), "at": state.stamp(moment)}
                lines.append(
                    f"{died}The watch ran drive job end {job_id}, which saves to GitHub what its Sprite holds on no remote and destroys it, its output in the drive's {RELAUNCHES_LOG};"
                    f' drive job resume {job_id} "<his answer>" relaunches it {fresh}'
                )
                continue
            job["deaths"][-1]["relaunch"] = {"pid": relaunch(name, job_id), "at": state.stamp(moment)}
            lines.append(f"{died}The watch ran drive job resume {job_id}, which relaunches it {fresh}, its output in the drive's {RELAUNCHES_LOG}")
    return lines


def relaunch(name: str, job_id: str) -> int:
    """Run `drive job resume` on a running job whose lane died as its own process, outliving the pass that started it, its output appended to the drive's relaunches log."""
    return state.detach(name, ["job", "resume", job_id, CARRY_ON], RELAUNCHES_LOG)


def end_lane(name: str, job_id: str) -> int:
    """Run `drive job end` on a waiting job whose lane on a Sprite died as its own process, so that idle Sprite, its forward and its Orca environment go once what it holds is saved, and the job's next event starts its next lane on a fresh one; its output appended to the drive's relaunches log."""
    return state.detach(name, ["job", "end", job_id], RELAUNCHES_LOG)


def can_wake(job_id: str, job: dict, data: dict, moment: datetime) -> bool:
    """Whether something can wake a waiting or built job: its lane's question, his open card, a wait the watch has not freed yet, an open PR of its, its proof to come, or a time to come, its own clock from drive job set --due or its orders' due times."""
    if job.get("ask") or view.open_cards(job) or job["waits"] or proving(data, job_id):
        return True
    if any(pr["job"] == job_id and pr["state"] == "open" for pr in data["prs"].values()):
        return True
    dues = [job["due"], *(data["orders"][order_id]["due"] for order_id in [job["order"], *job["carries"]])]
    return any(due and state.parse_time(due) > moment for due in dues)


def unanswered(job_id: str, job: dict, moment: datetime) -> str | None:
    """A lane's question no resume has answered for 30 minutes, whatever card or PR it has: only his card drawn for it since it came answers it, since an older card asks him something else; his answer since it came that no resume carried leads the line."""
    if not job.get("ask"):
        return None
    asked = state.parse_time(job["ask_at"])
    drawn = (question for question in view.open_questions(job) if question["card"] or question["waits"])
    if moment - asked < QUIET_FOR or any(state.parse_time(question["asked"]) >= asked for question in drawn):
        return None
    question = f'"{cut(job["said"] or "", 80)}" {minutes(moment - asked)} minutes ago'
    if answers := [answer for answer in job["undelivered"] if state.parse_time(answer["at"]) >= asked]:
        return f"{unreached(job_id, answers[-1])}, answering its lane's question {question}"
    return f'{job_id}\'s lane asked {question} and nothing answered it: drive job resume {job_id} "<the answer>" answers it'


def job_gap(watcher: Watcher, job_id: str, job: dict, data: dict, moment: datetime) -> str | None:
    """Why the job leaves its parts with no one, None when it carries them or is a design its builder carries, or SETTLING while a change in the last two minutes is still being handed over; a local job no agent has taken settles one minute only, since its agent's first step records it within seconds of starting; a builder's READY or PUSHED whose yasyf/* PRs merged with no proof file to run is said once on its line, since only the proof file drive job set --repro keeps wakes it."""
    if moment - state.parse_time(job["updated"]) < (UNSTARTED_FOR if unstarted(job) else HANDOFF):
        return SETTLING
    if job["state"] == "running":
        return watcher.agent_gaps.get(job_id)
    if job["state"] in ("waiting", "built"):
        if refused := next((question for question in view.open_questions(job) if question["refused"]), None):
            return (
                f'{job_id} waits on a card never posted: drive job ask refused its question "{cut(refused["title"], 80)}" kept in {refused["file"]}:'
                f" {cut(refused['refused'].removeprefix('refused: '), 160)}"
            )
        if asked := unanswered(job_id, job, moment):
            return asked
        if can_wake(job_id, job, data, moment):
            return None
        if merged_ready(data, job_id):
            return None if unproven(data, job_id) and job.get("unproven_said") == job["said"] else merged_gap(job_id, data)
        waiting = f"is built on {job['branch']}" if job["state"] == "built" else f'waits on "{cut(job["said"] or "nothing named", 80)}"'
        return f"{job_id} {waiting} and no open PR, card or due time can wake it"
    if job["state"] == "done":
        if state.unposted_answer(data, job_id):
            return f"{job_id} answered and the answer is not posted to him"
        if state.design_builder(data, job_id):
            return None
        drawn = [question for question in job["questions"] if question["card"]]
        answered = "card is" if len(drawn) == 1 else "cards are"
        return None if view.open_cards(job) else f"{job_id}'s {answered} answered and no job carries his answer"
    return f"{job_id} {job['state']}"


def merged_gap(job_id: str, data: dict) -> str:
    """Who proves live a builder's READY or PUSHED whose PRs all merged that the watch neither took DONE on at its merge nor proves: who proof_gap names, else a run again of the proof that ended with no result."""
    if why := proof_gap(data, job_id):
        return f"{job_id}'s PRs all merged and nothing proves them live: {why}"
    proof = data["jobs"][job_id]["proof"]
    return f"{job_id}'s proof, drive job prove pid {proof['pid']}, ended with no result, its output in the drive's {PROOFS_LOG}: drive job prove {job_id} runs it again"


def unproven(data: dict, job_id: str) -> bool:
    """Whether a builder's READY or PUSHED whose PRs all merged names a yasyf/* PR and keeps no executable proof file, so it waits on the proof file drive job set --repro keeps, which the watch then runs, and nothing else wakes it."""
    return merged_ready(data, job_id) and not product(data, job_id) and bool(proof_gap(data, job_id))


def at_merge(data: dict, job_id: str) -> bool:
    """Whether the watch takes DONE at its merge on a builder waiting on a READY or PUSHED whose PRs all merged outside yasyf/*, a product change, with nothing else holding it, unless it is an open incident's fix, which is live only once its release is out."""
    job = data["jobs"][job_id]
    if not merged_ready(data, job_id) or job["waits"] or not product(data, job_id):
        return False
    return not any(state.INCIDENT_FIX in state.job_parts(job, order_id) for order_id in state.open_incidents(data, [job["order"], *job["carries"]]))


def proof_due(data: dict, job_id: str) -> bool:
    """Whether the watch starts the job's proof: a builder still waiting on a READY or PUSHED whose PRs all merged, a yasyf/* one among them, none a PR a lander still merges or installs, keeping an executable proof file, and no proof started on that line, or one that ended with no result before a newer long-running's watch read it."""
    job = data["jobs"][job_id]
    if not merged_ready(data, job_id) or proof_gap(data, job_id) or any(land.running(data["prs"][state.pr_key(url)]) for url in job["prs"]):
        return False
    proof = job.get("proof")
    return not proof or proof["said"] != job["said"] or proof["version"] != state.VERSION and not state.alive(proof["pid"])


def proving(data: dict, job_id: str) -> bool:
    """Whether a merged job's proof is to come: a lander still merging or installing one of its PRs, a proof running on its line, or one the watch starts on its next read."""
    job = data["jobs"][job_id]
    if not merged_ready(data, job_id) or proof_gap(data, job_id):
        return False
    proof = job.get("proof")
    return proof_due(data, job_id) or any(land.running(data["prs"][state.pr_key(url)]) for url in job["prs"]) or proof["said"] == job["said"] and state.alive(proof["pid"])


def finished(watcher: Watcher, moment: datetime) -> None:
    """Finish each builder the watch hears whose READY or PUSHED's PRs all merged: one whose PRs all merged outside yasyf/* takes DONE at its merge, and one naming a yasyf/* PR and keeping an executable proof file gets drive job prove, which takes its DONE or BLOCKED once the installed plugins hold its yasyf/* merges, marked under the drive's lock so no second proof starts on its line; each says it on the job's PRs' lines."""
    data = state.load(watcher.name)
    if not any(hears(watcher, data, job["order"]) and (at_merge(data, job_id) or proof_due(data, job_id)) for job_id, job in data["jobs"].items()):
        return
    said = {}
    with state.edit(watcher.name) as data:
        for job_id, job in data["jobs"].items():
            if not hears(watcher, data, job["order"]):
                continue
            if at_merge(data, job_id):
                said[job_id] = merged_done(watcher.name, data, job_id)
            elif proof_due(data, job_id):
                job["proof"] = {"pid": prove(watcher.name, job_id), "at": state.stamp(moment), "said": job["said"], "version": state.VERSION}
    for job_id, change in said.items():
        said_on_prs(watcher.name, data, job_id, change)


def prove(name: str, job_id: str) -> int:
    """Run `drive job prove` on the job as its own process, outliving the pass that started it, its output appended to the drive's proofs log."""
    return state.detach(name, ["job", "prove", job_id], PROOFS_LOG)


def unstarted(job: dict) -> bool:
    return job["state"] == "running" and job.get("where") != "remote" and not job.get("agent")


def part_gap(watcher: Watcher, data: dict, order_id: str, part: str, moment: datetime) -> str | None:
    """Why a part of the order has no one carrying it, None when it is carried, delivered or waits on something named: a change a design lists waits on each earlier change it builds on, and on his pick while the order asks for options only; a job on the order's immediate fix part carries the order's other parts too until it is done; an open incident's fix is carried only by a live job, an open PR of a job on it or its fix recorded live, a builder's DONE or the release that shipped it, never by a post, so once its jobs end otherwise the gap says it has no fix job and names the second one to start at once."""
    order = data["orders"][order_id]
    jobs = state.part_jobs(data, order_id, part)
    gaps = [job_gap(watcher, job_id, job, data, moment) for job_id, job in jobs]
    if stuck := [gap for (_, job), gap in zip(jobs, gaps, strict=True) if gap and (job["state"] == "running" or job["state"] == "waiting" and job.get("ask"))]:
        return stuck[-1]
    if (
        jobs
        and part == state.INCIDENT_FIX
        and state.open_incidents(data, [order_id])
        and not order["fixed"]
        and not any(job["state"] not in state.FINISHED_JOB_STATES or job_id in {pr["job"] for pr in state.open_prs(data)} for job_id, job in jobs)
    ):
        last, job = jobs[-1]
        return (
            f"no fix job: {last} ended {job['state']} with no open PR; start the second fix job now, an Opus 5.5 long-running:builder on {last}'s brief:"
            f" drive job add builder --order {order_id} --part {state.INCIDENT_FIX} --goal {shlex.quote(job['goal'])} (incidents: Clocks)"
        )
    if state.delivered(data, order_id, part):
        return None
    if part != state.IMMEDIATE_FIX and any(job["state"] in ("running", "waiting", "built") for _, job in state.part_jobs(data, order_id, state.IMMEDIATE_FIX)):
        return None
    if jobs:
        return None if None in gaps else SETTLING if SETTLING in gaps else gaps[-1]
    if moment - state.parse_time(order["updated"]) < UNSTAFFED_FOR:
        return None
    if not (design := order["parts"][part]["design"]):
        return "no job"
    if order["options_only"] or any(after in order["parts"] and not state.delivered(data, order_id, after) for after in order["parts"][part]["after"]):
        return None
    return f"{design}'s design lists this change and no builder carries it: start one with drive job add builder --part, or add it to its builder with drive job set --part"


def part_gaps(watcher: Watcher, data: dict, moment: datetime) -> dict[tuple[str, str], str | None]:
    return {
        (order_id, part): part_gap(watcher, data, order_id, part, moment)
        for order_id, order in heard_orders(watcher, data).items()
        for part in order["parts"]
    }


def orphan_change(data: dict, key: tuple[str, str], gap: str | None, due: set[str], moment: datetime) -> str | None:
    said = data["orders"][key[0]]["parts"][key[1]]["orphan_said"]
    if gap is None:
        return "heal" if said else None
    if gap == SETTLING:
        return None
    if key[0] in due or not said or moment - state.parse_time(said) >= TICK_EVERY or added_since(data, key, state.parse_time(said)):
        return "say"
    return None


def added_since(data: dict, key: tuple[str, str], said: datetime) -> bool:
    """Whether a local job no agent has taken was added or resumed on the part since the part was last said, news its 30-minute repeat would hold back."""
    return any(unstarted(job) and state.parse_time(job["updated"]) > said for _, job in state.part_jobs(data, *key))


def orphan_lines(watcher: Watcher, moment: datetime, due: set[str]) -> list[str]:
    data = state.load(watcher.name)
    if not any(orphan_change(data, key, gap, due, moment) for key, gap in part_gaps(watcher, data, moment).items()):
        return []
    lines = []
    with state.edit(watcher.name) as data:
        for key, gap in part_gaps(watcher, data, moment).items():
            change = orphan_change(data, key, gap, due, moment)
            if change:
                data["orders"][key[0]]["parts"][key[1]]["orphan_said"] = None if change == "heal" else state.stamp(moment)
            if change == "say":
                lines.append(f'ORPHAN {key[0]} "{cut(key[1], 80)}": {gap}')
                for job_id, job in state.part_jobs(data, *key):
                    if unproven(data, job_id) and gap == merged_gap(job_id, data):
                        job["unproven_said"] = job["said"]
    return lines


def hear(name: str, moment: datetime, away: bool = False) -> dict:
    """Record him heard at moment, by his words or his tap on a card, or away with away; when he was away, add each order that changed since he was last heard, and each a report held for his catch-up is on, to the catch-up the drive owes him, which keeps the time of the first such absence it covers, and which the watch's next pass posts."""
    with state.edit(name) as data:
        back = state.owner_away(data, moment)
        since = (data.get("owner") or {}).get("heard")
        data["owner"] = {"heard": state.stamp(moment), "away": away}
        news = []
        held = list(data["held"]) if back and since else []
        if back and since:
            for key, order in data["orders"].items():
                last = max(state.parse_time(order["last_line"] or order["created"]), state.parse_time(since))
                if changed_since(order, data["jobs"], last) or any(mark["order"] == key for mark in data["held"].values()):
                    news.append(key)
        if news or held:
            owed = data["catch_up"] or state.new_catch_up(since, [])
            data["catch_up"] = owed | {"orders": list(dict.fromkeys([*owed["orders"], *news])), "record": None}
        owed = data["catch_up"]
    return {"away": away, "back": back, "since": since, "catch_up": owed and owed["orders"], "held": held}


def catch_up_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Post the catch-up the drive owes him while he is here, once for each time he is heard: a report of it the watch writes once, its body naming each report held for it with each correction cci holds re one and each order with news, for a handler the watch starts to post at his DM root, unless the handler started last is still at work on it; a failed write or start is said as a failed read is and tried again next pass."""
    data = state.load(watcher.name)
    owed, heard = data["catch_up"], data["owner"]["heard"]
    if watcher.project or owed is None or owed["tried"] == heard or state.owner_away(data, moment):
        return []
    if (entry := data["handlers"].get(owed["handler"] or "")) and not agents.gone(entry["session"]):
        with state.edit(watcher.name) as data:
            data["catch_up"]["tried"] = heard
        return []
    try:
        seq = owed["record"] or catch_up_report(watcher, data)
        with state.edit(watcher.name) as data:
            data["catch_up"]["record"] = seq
        handler, _ = agents.handle(watcher.name, f"Record: {seq} Drive: {watcher.name} Scratch: {state.drive_dir(watcher.name) / 'scratch' / f'catch-up-{seq}'}")
    except (reads.ReadError, state.DriveError) as error:
        return failed(watcher, "catch-up", "post of his catch-up", error, moment)
    watcher.failures.pop("catch-up", None)
    with state.edit(watcher.name) as data:
        data["catch_up"] |= {"handler": handler, "tried": heard}
    return []


def catch_up_report(watcher: Watcher, data: dict) -> str:
    """Write the report of his catch-up for the handler that posts it; its seq."""
    owed = data["catch_up"]
    corrected = agents.corrections(watcher.name, data["held"])
    held = [f"- report {seq} on {mark['order'] or 'no order'}{render.corrected_by(corrected.get(seq, []))}" for seq, mark in data["held"].items()]
    orders = [f"- {render.order_line(order_id, data['orders'][order_id])}" for order_id in owed["orders"]]
    facts = [
        f"His catch-up, owed since he was last heard at {when(owed['since'])}. Read each held report and each correction with cci read: no held report reached him, and a correction's facts replace those it corrects.",
        f"Held for it: {len(held)}",
        *held,
        f"Orders with news since then: {len(orders)}",
        *orders,
    ]
    text = f"POST his catch-up since {when(owed['since'])}: {len(orders)} orders with news, {len(held)} reports held for it"
    from drive import tools

    [seq] = tools.sent(watcher.name, LANE, [{"kind": "report", "attr": {"catch_up": "true"}, "text": text, "body": "\n".join(facts) + "\n"}])
    return seq


def changed_since(order: dict, jobs: dict, last: datetime) -> bool:
    stamps = [order["updated"], *(jobs[job_id]["updated"] for job_id in order["jobs"])]
    return any(state.parse_time(stamp) > last for stamp in stamps)


def due_soon(order: dict, moment: datetime) -> bool:
    return bool(order["due"]) and moment <= state.parse_time(order["due"]) < moment + TICK_EVERY


def owes_line(order: dict, jobs: dict, moment: datetime) -> bool:
    last = state.parse_time(order["last_line"] or order["created"])
    if moment - last < TICK_EVERY:
        return False
    if order["state"] == "done":
        return changed_since(order, jobs, max(last, moment - TICK_EVERY))
    if order["state"] not in OWING_STATES:
        return False
    return changed_since(order, jobs, last) or due_soon(order, moment)


def tick_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """The orders owed a line, then each answer of his a live job of theirs holds that no resume carried, so a report on them never says they wait on him."""
    data = state.load(watcher.name)
    if state.owner_away(data, moment):
        return []
    owing = [
        key
        for key, order in data["orders"].items()
        if state.runs(data, order, watcher.project) and owes_line(order, data["jobs"], moment)
    ]
    if not owing:
        return []
    answers = [
        unreached(job_id, answer)
        for job_id, job in data["jobs"].items()
        if job["state"] not in state.FINISHED_JOB_STATES and {job["order"], *job["carries"]} & set(owing)
        for answer in job["undelivered"]
    ]
    return [", and ".join([f"TICK {' '.join(owing)}", *answers])]


def run_pass(watcher: Watcher, moment: datetime) -> list[str]:
    """One loop of the watch: his cards, GitHub, a lander on each PR his Approve covers that qualifies, the finish of each builder whose READY's PRs all merged, DONE at the merge of a product change or the proof of a yasyf/* one, the check against his words of each PR whose builder's READY owes one, his PR cards whose PRs all ended, the waits his answers or a job's end release, a port's READY the coordinator has not checked in five minutes, his answers that leave cards unmarked, lane mail, each builder's PUSHED GitHub's read settles, agents, the communicator's compaction, the status page, a coordinator whose process exited and a dashboard whose server exited every 60 s, GitHub again next poll after a failure, new events, the catch-up owed him, promised times still owed a line, job clocks run out, his answers no resume has carried to their jobs, fixes proven live whose bug report has not heard, parts nothing carries, a tick every 30 minutes while he is here. Each stage's lines join the watcher's made lines as it returns, or as it saves when it keeps working after its save, so a pass that ends early, in a crash or on a drive a newer install saved, leaves them to the watch process after it."""
    lines = watcher.made
    if watcher.read_at is None or moment - watcher.read_at >= READ_EVERY:
        watcher.read_at = moment
        view.refresh_cards(watcher.name)
        found, source = view.stored_cards(watcher.name)
        watcher.cards = found if source == "live" else None
        lines += github_lines(watcher, moment)
        lines += carried_lines(watcher, moment)
        checks(watcher, moment)
        lines += stage_lines(watcher, moment)
        landed(watcher)
        lines += settle_lines(watcher, moment)
        lines += follow_lines(watcher, moment)
        lines += waits_lines(watcher, moment)
        finished(watcher, moment)
        lines += recovered_lines(watcher, moment)
        lines += close_report_lines(watcher, moment)
        lines += waiting_lines(watcher, moment)
        lines += answer_lines(watcher, moment)
        lines += lane_lines(watcher, moment)
        release_lanes(watcher, moment)
        lines += pushed_lines(watcher, moment)
        lines += agent_lines(watcher, moment)
        lines += compact_lines(watcher, moment)
        lines += page_lines(watcher, moment)
        lines += down_lines(watcher, moment)
        lines += dashboard_lines(watcher)
    lines += event_lines(watcher)
    lines += catch_up_lines(watcher, moment)
    due = due_lines(watcher, moment)
    lines += [line for _, line in due]
    lines += quiet_lines(watcher, moment)
    lines += clock_lines(watcher, moment)
    lines += undelivered_lines(watcher, moment)
    lines += tell_lines(watcher, moment)
    lines += orphan_lines(watcher, moment, {order_id for order_id, _ in due})
    if moment - watcher.ticked_at >= TICK_EVERY:
        watcher.ticked_at = moment
        lines += tick_lines(watcher, moment)
    return handed(watcher, lines, moment)


def carried_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Carry each heard PR's check against his words, and his Approve, to a head a restack moved, its change the one judged or approved, so his Approve lands it at green with no second judge, then redraw in place his open card carrying it; a diff read that fails is retried and said as any GitHub read is."""
    data = state.load(watcher.name)
    try:
        carried = served.carry(watcher.name, [key for key in state.open_pr_keys(data) if hears(watcher, data, data["prs"][key]["order"])])
    except state.DriveError as error:
        return read_failed(watcher, "moved heads' diffs", error, moment)
    watcher.failures.pop("moved heads' diffs", None)
    lines = []
    for key in carried:
        try:
            slack.redraw_pr_cards(state.pr_url(key))
        except slack.SlackError as error:
            lines.append(f"WATCH his open card for {key} still shows its old head, since its redraw failed: {error}")
    return lines


def stage_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Keep each heard open PR's stage, as stage.of names it from the approval, checks and head GitHub answered in this pass's read, and act once on each that entered a stage or passed its deadline, leaving a PR whose read failed as it stands until a read answers for it: draw his card on each uncarded PR, one card for the PRs of one card unit as drive card draws them, redraw his card in place on every other change of stage so its line shows it, wake the builder answering for a PR rework or red owe, ask forge-pr-reviewer once for a review of a head grading an hour and then wake its builder, wake the session holding its order on one unlanded, or landing or ready past its deadline, and post his card again on one that waits on him while it fixes a live bug. A draw that fails keeps the PR uncarded for the next read, and is said to the meta session from its third failure in a row."""
    if watcher.cards is None:
        return []
    with state.edit(watcher.name) as data:
        stage.settle(data, watcher.cards)
        stacks = land.landing_stacks(data)
        units = {key: state.card_unit(key, stacks.get(key, ()), pr) for key, pr in data["prs"].items()}
        acts = []
        for key in state.open_pr_keys(data):
            pr = data["prs"][key]
            if key not in watcher.fresh or not hears(watcher, data, pr["order"]):
                continue
            now = stage.of(data, key, watcher.cards)
            if stage.entered(pr["stage"], now):
                stage.stamp(pr, now, moment)
                acts.append((key, now, True, stage.line(key, pr, now) + f" {ids(pr)}"))
            elif stage.overdue(data, pr, now, moment):
                stage.kept(pr, now)
                pr["stage"]["woke"] = state.stamp(moment)
                acts.append((key, now, False, stage.line(key, pr, now) + f" {ids(pr)}"))
            else:
                stage.kept(pr, now)
    lines: list[str] = []
    uncarded = {}
    for key, now, entered, line in acts:
        if now.name == "uncarded":
            uncarded.setdefault(units[key], []).append(key)
            continue
        lines += acted(watcher, key, now, entered, line, moment)
    from drive import talk

    for keys in uncarded.values():
        try:
            talk.draw(watcher.name, keys)
        except (slack.SlackError, state.DriveError) as error:
            watcher.unsent.setdefault("meta", []).extend(failed(watcher, "card draw", f"draw of his card on {', '.join(keys)}", error, moment))
            continue
        watcher.failures.pop("card draw", None)
    return lines


def acted(watcher: Watcher, key: str, now: stage.Stage, entered: bool, line: str, moment: datetime) -> list[str]:
    """What the watch does for a PR that entered its stage, entered, or passed that stage's deadline, and the lines it says: his card, when one is drawn, redrawn on entry, or withdrawn from a PR reworked to his words that still has one, then the act its stage owes, none for a PR behind another, whose owner that PR's stage wakes."""
    from drive import talk

    said = []
    card = state.load(watcher.name)["prs"][key]["card"]
    if now.name == "rework":
        if card:
            try:
                talk.withdraw(watcher.name, key)
            except slack.SlackError as error:
                said += failed(watcher, "rework withdraw", f"withdrawal of his card on {key}, reworked to his words", error, moment)
    elif entered and card:
        try:
            slack.redraw_pr_cards(state.pr_url(key))
        except slack.SlackError as error:
            said += failed(watcher, "card redraw", f"redraw of his card on {key}", error, moment)
    if now.name in ("behind", "author"):
        return said
    if now.name in ("rework", "red"):
        stage.wake(watcher.name, key, line)
    elif now.name == "grading" and not entered:
        pr = state.load(watcher.name)["prs"][key]
        repo, number = key.split("#")
        if pr["review_asked"] == pr["head"]:
            stage.wake(watcher.name, key, line)
        elif refused := land.ask_review(watcher.name, repo, {number: pr["head"]}):
            said.append(f"STAGE {refused} {ids(pr)}")
    elif now.owner == stage.ORDER or now.name in ("landing", "ready") and not entered:
        said.append(f"STAGE {line}")
    elif now.owner == stage.HIM and not entered:
        try:
            talk.draw(watcher.name, [key])
        except (slack.SlackError, state.DriveError) as error:
            said += failed(watcher, "card ask", f"ask again of his card on {key}", error, moment)
    return said


def landed(watcher: Watcher) -> None:
    """Start the drive's lander on each PR the watch hears that his Approve or an exempt class clears once it qualifies, within one GitHub read of it going green and approved, and on each yasyf/* one whose head moved past the one its lander tried, so a branch its builder or the lander brought onto its base merges with no builder registering it again."""
    data = state.load(watcher.name)
    keys = [key for key in state.open_pr_keys(data) if hears(watcher, data, data["prs"][key]["order"])]
    land.start(watcher.name, keys, watcher.cards)
    land.follow(watcher.name, keys)


def holder_line(line: str, data: dict) -> str | None:
    """The lane of the session holding the order a line is about, when that is the communicator or the meta session rather than a coordinator."""
    found = ABOUT.search(line)
    order = found and data["orders"].get(found["order"] or found["named"])
    return state.holder_lane(order) if order else None


def needs_step(line: str, data: dict) -> bool:
    """Whether a line asks for a step now: any line but a PR's, his tap but an Approve, which the drive's lander lands in every repository once its stage reads ready, a PR's merged line only when nobody else says it, never GitHub's merged line for a PR the lander merges and says so itself or whose job took DONE at its merge, nor the lander's merged line on a job already proved or whose proof the watch runs, whose proof line says so, and a line on a PR no longer open while its job does not run to take it, such as its proof failing or its close, but its proof passing or its job's DONE at its merge. An open PR's moves wake nobody, since its stage wakes its owner."""
    if (tapped := TAP_EVENT.match(line)) and tapped["label"] == "Approve":
        return False
    found = PR_EVENT.match(line)
    if not found:
        return not line.startswith("PR ")
    pr = data["prs"][found["key"]]
    if found["change"] == "merged":
        return not (land.reported(found["key"], pr) or data["jobs"][found["job"]]["state"] == "done")
    if found["change"].startswith("merged at"):
        return not (data["jobs"][found["job"]]["state"] in state.FINISHED_JOB_STATES or proving(data, found["job"]))
    if pr["state"] == "open" or found["change"].startswith((PROVEN, MERGED_DONE)):
        return False
    return data["jobs"][found["job"]]["state"] != "running"


def wakes(line: str, data: dict) -> bool:
    """Whether the coordinator acts on the line now: his tap but an Approve the drive's lander acts on, a promised time, an orphan, his answer, a lane's report or death, his return, a stopped watch, or a PR's line that asks for a step, such as its exempt landing or a failure of its landing."""
    return (line.startswith(WAKES) or line.startswith("PR ")) and needs_step(line, data)


def handed(watcher: Watcher, lines: list[str], moment: datetime) -> list[str]:
    """Hand each line to the one who acts on it: an order's the communicator or the meta session holds to that session, a check's NOT READY whole to the builder answering for its PR through drive pr wake, and the rest, returned, to the coordinator. Until it is handed, each line stays on the watcher, the coordinator's among its made lines and a holder's among its unsent, so a newer install that takes the watch over meanwhile gets it."""
    lines = [LINE_BREAKS.sub(" ", line) for line in lines]
    if not lines and not any(watcher.unsent.get(lane) for lane in state.HOLDERS):
        return []
    data = state.load(watcher.name)
    kept = []
    for line in lines:
        if (found := PR_EVENT.match(line)) and found["change"].startswith(served.NOT_READY):
            stage.wake(watcher.name, found["key"], line)
        elif holder := holder_line(line, data):
            if needs_step(line, data):
                watcher.unsent.setdefault(holder, []).append(line)
        else:
            kept.append(line)
    watcher.made = kept
    failures = [failure for lane in state.HOLDERS for failure in sent(watcher, lane, [], moment)]
    watcher.made = []
    return kept + failures


def sent(watcher: Watcher, lane: str, lines: list[str], moment: datetime) -> list[str]:
    """Post each line, after any that failed to post before, to the lane over cci: a coordinator's line it acts on now wakes it, its others reach it together at most every five minutes, and a line that fails goes again next pass, said from its third failure in a row. The lines stay among the watcher's unsent until each posts, so a newer install that takes the watch over meanwhile gets them."""
    owed = watcher.unsent[lane] = watcher.unsent.get(lane, []) + lines
    if not owed:
        return []
    data = state.load(watcher.name)
    refusal, unsent = None, []
    for line in owed:
        kind = "handoff" if lane in state.HOLDERS or wakes(line, data) else "evidence"
        topic = f"watch-{hashlib.sha1(line.encode()).hexdigest()[:10]}"
        argv = ["cci", "post", "--drive", watcher.name, "--lane", LANE, "--kind", kind, "--to", f"{lane}@{watcher.name}", "--topic", topic, "--text", line]
        try:
            reads.run(argv, Path(data["roles"]["coordinator"]["cwd"]))
        except reads.ReadError as error:
            unsent.append(line)
            refusal = error
    watcher.unsent[lane] = unsent
    what = {"communicator": "handoff post to the communicator", "meta": "handoff post to the meta session", "root": "post to the coordinator"}.get(lane, f"post to the sub-coordinator for {watcher.project}")
    if refusal is None:
        watcher.failures.pop(what, None)
        return []
    return failed(watcher, what, what, refusal, moment)


def holder(path: Path) -> int | None:
    held = path.read_text().strip() if path.exists() else ""
    return int(held) if held.isdigit() and state.alive(int(held)) else None


def claim(name: str, project: str | None = None) -> bool:
    path = state.pid_path(name, project)
    with state.locked(name):
        if holder(path) not in (None, os.getpid()):
            return False
        path.write_text(f"{os.getpid()}\n")
    return True


def release(name: str, project: str | None = None) -> None:
    path = state.pid_path(name, project)
    with state.locked(name):
        if path.exists() and path.read_text().strip() == str(os.getpid()):
            path.unlink()


def loop_argv(drive: Path, name: str, project: str | None) -> list[str]:
    return [str(drive), "watch", "--loop", "--drive", name, *(["--project", project] if project else [])]


def ensure(name: str, project: str | None = None) -> str:
    """Start the drive's watch, or a project's, as its own process unless one runs or the drive sits under LONG_RUNNING_STATE, its output in its own log and its environment naming the drive, which lets the drive's sessions end it unasked; the line saying which. A project's watch first reads the events from where the log ended as its sub-coordinator was bound."""
    watcher = f"the watch for {project}" if project else "the watch"
    reader = f"the sub-coordinator for {project}" if project else "the coordinator"
    if state.scratch():
        return f"{watcher} does not start for a drive under LONG_RUNNING_STATE={state.root()}; a proof runs its passes in-process with watch.run_pass(watch.Watcher(...), moment)"
    if pid := holder(state.pid_path(name, project)):
        return f"{watcher} runs as pid {pid} and posts its lines to {reader} over cci; arm no Monitor for it"
    env = {key: value for key, value in os.environ.items() if key != "CLAUDE_CODE_SESSION_ID"} | {"CLAUDE_LONG_RUNNING_LANE": LANE, state.DRIVE_ENV: name}
    with state.watch_log_path(name, project).open("a") as log:
        started = subprocess.Popen(
            loop_argv(DRIVE, name, project),
            cwd=state.drive_dir(name),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return f"started {watcher} as pid {started.pid}; it posts its lines to {reader} over cci, so arm no Monitor for it"


def listen(name: str, session: str, project: str | None = None) -> None:
    """Point the cci channel of the coordinator session, or a project's sub-coordinator, at its watch's records: a line it acts on now wakes it, and the rest arrive together at most every five minutes."""
    argv = [*shlex.split(subscribe("coordinator", project)), "--drive", name]
    reads.run(argv, Path(state.load(name)["roles"]["coordinator"]["cwd"]), os.environ | {"CLAUDE_CODE_SESSION_ID": session})


def stopped(watcher: Watcher, error: Exception) -> None:
    """Say the watch stopped, naming its version and where the error was raised, the innermost frame of this package's code, so the line alone says which line to read."""
    frame = next(frame for frame in reversed(traceback.extract_tb(error.__traceback__)) if Path(frame.filename).resolve().parent == Path(__file__).resolve().parent)
    where = f"{type(error).__name__} at {Path(frame.filename).name}:{frame.lineno} in {frame.name}"
    said = f"{where}: {cut(str(error), 200)}" if str(error) else where
    again = shlex.join(["drive", "watch", *(["--project", watcher.project] if watcher.project else [])])
    line = f"WATCH stopped on long-running {state.VERSION}: {said}; its traceback is in {state.watch_log_path(watcher.name, watcher.project)}; {again} starts it again"
    with contextlib.suppress(reads.ReadError):
        sent(watcher, watcher.lane, [line], state.now())


def main(drive_name: str | None = None, project: str | None = None) -> int:
    """Run the drive's watch, or a project's, until it stops, once a watch an older install ran under a Monitor has ended; on a drive under LONG_RUNNING_STATE, end at once, so a watch an older install started there ends at its next re-exec."""
    if state.scratch():
        print(f"no watch runs for a drive under LONG_RUNNING_STATE={state.root()}", flush=True)
        return 0
    name = state.resolve(drive_name)
    if not claim(name, project):
        return 0
    try:
        while holder(state.monitor_pid_path(name)):
            state.let_go_install()
            time.sleep(POLL_SECONDS)
        watching(Watcher(name, offset=state.watch_cursor(name, project), ticked_at=state.now(), project=project, made=state.take_held_lines(name, project)))
    finally:
        release(name, project)
    return 0


def watching(watcher: Watcher) -> None:
    """Hand each pass's lines to whoever acts on them, first any a watch before this one held for it, and let a newer install take over in place before a pass, or the moment a pass finds the drive saved by one, so no pass runs older code on a drive a newer install saved; a crash wakes the coordinator session it posts to first. Every line made and not yet posted is held for the watch after this one, which hands it on before its first pass, so the watch holds no install's lock: a pass that reads GitHub and each Sprite for minutes, or waits on a drive process it started, never holds a newer install's verbs."""
    state.save_watch_cursor(watcher.name, watcher.offset, watcher.project)
    state.let_go_install()
    try:
        while True:
            if newer := upgraded():
                handed_over(watcher, newer)
            read_from, moment = watcher.offset, state.now()
            orca.speak_as(coordinator(state.load(watcher.name), watcher.project)["terminal"])
            said = handed(watcher, watcher.made, moment) if watcher.made else run_pass(watcher, moment)
            for line in sent(watcher, watcher.lane, said, moment):
                print(line, flush=True)
            if watcher.offset != read_from:
                state.save_watch_cursor(watcher.name, watcher.offset, watcher.project)
            time.sleep(POLL_SECONDS)
    except Exception as error:
        if isinstance(error, state.NewerDrive) and (newer := upgraded()):
            handed_over(watcher, newer)
        keep_unposted(watcher)
        stopped(watcher, error)
        raise


def handed_over(watcher: Watcher, newer: Path) -> None:
    keep_unposted(watcher)
    os.execv(newer, loop_argv(newer, watcher.name, watcher.project))


def keep_unposted(watcher: Watcher) -> None:
    """Move each line the watch made and has not posted, routed or not, to the watch process after it, which hands them on before its first pass and reads events from past the last one they were made from."""
    state.save_watch_cursor(watcher.name, watcher.offset, watcher.project)
    state.hold_lines(watcher.name, watcher.project, [LINE_BREAKS.sub(" ", line) for line in [*watcher.made, *(line for lines in watcher.unsent.values() for line in lines)]])
    watcher.made, watcher.unsent = [], {}
