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
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Self

from drive import agents, codex, dashboard, github, limits, orca, page, reads, slack, state, superseded, view
from drive.install import config_dir
from drive.render import NOT_READY_CHARS, cut, subscribe, when

POLL_SECONDS = 5
READ_EVERY = timedelta(seconds=60)
FAILURES_BEFORE_SAYING = 3
FAILURE_EVERY = timedelta(minutes=10)
TICK_EVERY = timedelta(minutes=30)
DUE_EVERY = timedelta(minutes=10)
TELL_AFTER = timedelta(minutes=10)
WAKES = ("TAP ", "DUE ", "LIVE ", "TELL ", "ORPHAN ", "ANSWER ", "JOB ", "LANE ", "FREE ", "EVENT catch-up ", "EVENT unheard ", "EVENT stop ", "WATCH stopped")
OWNER_MOVES = ("merged", "checks failed", "conflicts with its base", "changes requested", "still red", "NOT READY at")
PR_EVENT = re.compile(r"PR (?P<key>\S+): (?P<change>.+) \[(?P<order>o\d+) (?P<job>j\d+)\]$")
LANE = "watch"
UNTHREADED = "the order has no thread, so its time was the drive's own and is said this once"
DRIVE = Path(__file__).resolve().parents[2] / "bin" / "drive"
OWING_STATES = ("working", "waiting")
LIVE_JOB_STATES = ("running", "waiting")
LINE_BREAKS = re.compile(r"[\r\n]+")
HANDOFF = timedelta(minutes=2)
SETTLING = ""
UNKEPT_ASK = "keep it with `drive job ask {job}`, which prints the file its ask record names, since no file holds it until then"
PAUSED = "The recommended answer to this question is built and paused on your pick: tap it and the work ships; another pick rebuilds it first."
QUIET_FOR = timedelta(minutes=30)
STUCK_FOR = timedelta(minutes=30)
UNSTAFFED_FOR = timedelta(minutes=5)
RELAUNCH = timedelta(seconds=orca.LAUNCH_SECONDS)
TAIL_BYTES = 65536
COMPACT_PAST_TOKENS = 120_000
COMPACT_WHEN_QUIET = timedelta(minutes=10)
NOT_SLUG = re.compile(r"[^A-Za-z0-9]")
PR_LINE = re.compile(r"\s*(?:READY|PR:)")
STUCK_WORDS = {
    "red": "still red",
    "card": "green and approved with no card drawn",
    "landing": "green, approved and exempt, and not landed",
}
ABOUT = re.compile(r"\[(?P<order>o\d+) (?P<job>j\d+)\]$|^(?:FREE (?:\S+ )?j\d+|ORPHAN|DUE(?: j\d+)?|LIVE|TELL|JOB j\d+|LANE j\d+) (?P<named>o\d+)\b")
OWN_MOVES = ("new head", "checks passed", "approved")


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
    agent_gaps: dict[str, str | None] = field(default_factory=dict)
    unsent: dict[str, list[str]] = field(default_factory=dict)

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
    if fresh["review"] != pr["review"] and fresh["review"] != "none":
        changes.append("approved" if fresh["review"] == "approved" else "changes requested")
    if fresh.get("conflict") and not pr["conflict"]:
        changes.append("conflicts with its base")
    if fresh["state"] != pr["state"]:
        changes.append(fresh["state"])
    pr.update(fresh, draft=read["isDraft"], merged_sha=read["mergeCommit"])
    return changes


def github_lines(watcher: Watcher, moment: datetime) -> list[str]:
    data = state.load(watcher.name)
    keys = [key for key in state.open_pr_keys(data) if hears(watcher, data, data["prs"][key]["order"])]
    if not keys:
        return []
    try:
        reads = github.fetch(keys)
    except github.GithubError as error:
        return read_failed(watcher, "github", error, moment)
    watcher.failures.pop("github", None)
    lines = []
    with state.edit(watcher.name) as data:
        for key, read in reads.items():
            pr = data["prs"][key]
            before = pr["head"]
            changes = pr_changes(pr, read)
            state.date_verdict(data, key, read["headRefOid"], read["committed"])
            if watcher.cards is not None and not moves_owed(pr, data, watcher.cards):
                changes = [change for change in changes if not change.startswith(OWN_MOVES)]
            if before and pr["head"] != before and state.owed_step(pr) == "recheck":
                changes = [change for change in changes if not change.startswith("new head")] + [recheck_owed(pr)]
            if stuck := stuck_for(pr, data, watcher.cards, moment):
                changes.append(stuck)
            pr["updated"] = state.stamp(moment)
            lines.extend(f"PR {key}: {change} {ids(pr)}" for change in changes)
    return lines


def owned(job: dict, cards: dict | None) -> bool:
    """Whether a PR's job holds it: a running builder owns its pushes and CI, and a job asking him holds its PR."""
    return job["state"] == "running" or bool(view.open_cards(job, cards))


def next_step(pr: dict, data: dict, cards: dict | None) -> bool:
    """Whether a PR waits on its next step alone: open, green and approved, not held, nothing it waits on holding it, and its job not holding it."""
    green = pr["state"] == "open" and pr["checks"] == "pass" and pr["review"] == "approved" and not held(pr)
    return green and not view.holding(data, pr["waits"], cards) and not owned(data["jobs"][pr["job"]], cards)


def moves_owed(pr: dict, data: dict, cards: dict) -> bool:
    """Whether a PR's own moves, a new head, checks passed or approved, are said: it waits on its next step alone, and that step is not his card already drawn."""
    return next_step(pr, data, cards) and not (state.owed_step(pr) == "card" and view.card_for(cards["pr_cards"], pr["url"]))


def recheck_owed(pr: dict) -> str:
    return f"new head {pr['head'][:7]} under NOT READY, check owed"


def stuck_as(pr: dict, data: dict, cards: dict | None) -> str | None:
    if pr["state"] != "open" or owned(data["jobs"][pr["job"]], cards):
        return None
    if pr["checks"] == "fail":
        return "red"
    if cards is None or not moves_owed(pr, data, cards):
        return None
    return step if (step := state.owed_step(pr)) in STUCK_WORDS else None


def held(pr: dict) -> bool:
    return (pr.get("tap") or {}).get("label") == "Hold" or pr["hold_label"]


def stuck_for(pr: dict, data: dict, cards: dict | None, moment: datetime) -> str | None:
    """Say a PR stuck red, or green and approved with his card or its exempt landing owed while nothing it waits on holds, once it has held 30 minutes with its job neither running nor asking him, then again only when what it is changes: its head, checks, review, tap, job's state, or what it waits on."""
    holds = view.holding(data, pr["waits"], cards)
    what, stuck = stuck_as(pr, data, cards), pr.get("stuck")
    if what is None or not stuck or stuck["as"] != what:
        pr["stuck"] = what and {"as": what, "since": state.stamp(moment), "said": None}
        return None
    since = state.parse_time(stuck["since"])
    seen = {
        "head": pr["head"],
        "checks": pr["checks"],
        "review": pr["review"],
        "tap": (pr.get("tap") or {}).get("label"),
        "job": data["jobs"][pr["job"]]["state"],
        "waits": holds or None,
    }
    if moment - since < STUCK_FOR or stuck["said"] and stuck.get("seen") == seen:
        return None
    pr["stuck"] = {"as": what, "since": stuck["since"], "said": state.stamp(moment), "seen": seen}
    waiting = f", waiting on {', '.join(map(bare, holds))}" if holds else ""
    return f"{STUCK_WORDS[what]} for {minutes(moment - since)} minutes{waiting}"


def bare(wait: str) -> str:
    return wait.split("?")[0]


def waits_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Drop each wait of a job or PR that ended, his card answered, the job finished or the release out, and say once, as its last wait ends, that nothing holds it, holding back the job's ORPHAN for 30 minutes; the cards a job asked him on hold it too, so it keeps its waits until he answers them. His open card for a PR whose waits ended is redrawn in place, so it names only the waits that still hold. A release that ships an open incident order's job or PR is kept on the order by the watch dropping its wait, and the watch hearing the order says once, when no live job or open PR of the order waits on a release any more, that its fix is live and the order still open, naming each release kept since."""
    data = state.load(watcher.name)
    out, lines = shipped(watcher, data, moment)
    owners = [
        *(job for job in data["jobs"].values() if heard_job(watcher, data, job) and not view.open_cards(job, watcher.cards)),
        *(pr for pr in data["prs"].values() if hears(watcher, data, pr["order"])),
    ]
    if not out and not unsaid_fixes(watcher, data) and all(view.holding(data, owner["waits"], watcher.cards) == owner["waits"] for owner in owners):
        return lines
    with state.edit(watcher.name) as data:
        for job_id, job in data["jobs"].items():
            if not heard_job(watcher, data, job) or view.open_cards(job, watcher.cards):
                continue
            fixing(data, [job["order"], *job["carries"]], job, out)
            if (ended := released(data, job, watcher.cards, out)) and job["state"] not in state.FINISHED_JOB_STATES:
                job["updated"] = state.stamp(moment)
                for order_id in [job["order"], *job["carries"]]:
                    parts = data["orders"][order_id]["parts"]
                    for part in parts.keys() & set(state.job_parts(job, order_id)):
                        parts[part]["orphan_said"] = state.stamp(moment)
                built = f", built on {job['branch']}" if job["state"] == "built" else ""
                lines.append(f"FREE {job_id} {job['order']}: {ended}, nothing else holds it{built}")
        redraws = []
        for key, pr in data["prs"].items():
            if not hears(watcher, data, pr["order"]):
                continue
            fixing(data, [pr["order"]], pr, out)
            waited = pr["waits"]
            if (ended := released(data, pr, watcher.cards, out)) and pr["state"] == "open":
                lines.append(f"FREE {key} {pr['job']} {pr['order']}: {ended}, nothing else holds it")
            if pr["waits"] != waited and pr["state"] == "open":
                redraws.append(pr["url"])
        for order_id, shipping in unsaid_fixes(watcher, data).items():
            data["orders"][order_id]["shipped"] |= dict.fromkeys(shipping, state.stamp(moment))
            lines.append(
                f"LIVE {order_id}: {', '.join(shipping)}, so its fix is live and the incident order is still open: close it with drive order set {order_id} --state done so the strip shows live incidents only"
            )
    for url in redraws:
        try:
            slack.redraw_pr_cards(url)
        except slack.SlackError as error:
            lines.append(f"WATCH his open card for {url} still names the waits that ended, since its redraw failed: {error}")
    return lines


def fixing(data: dict, order_ids: list[str], owner: dict, out: dict[str, str]) -> None:
    """Keep on each open incident order a job or PR serves each release out that ships it, read under the lock that drops its wait, so the watch hearing the order says it whichever watch dropped it."""
    for order_id in state.open_incidents(data, order_ids):
        for wait in owner["waits"]:
            if wait in out:
                data["orders"][order_id]["shipped"].setdefault(out[wait], None)


def unsaid_fixes(watcher: Watcher, data: dict) -> dict[str, list[str]]:
    """Each open incident order the watch hears with releases kept that LIVE has not said, once no live job or open PR serving it, whichever watch hears them, still waits on a release."""
    pending = {order_id for order_ids, owner in release_owners(data) if any(map(state.RELEASE_WAIT.fullmatch, owner["waits"])) for order_id in order_ids}
    return {
        order_id: unsaid
        for order_id in state.open_incidents(data, list(data["orders"]))
        if order_id not in pending
        and hears(watcher, data, order_id)
        and (unsaid := [shipping for shipping, said in data["orders"][order_id]["shipped"].items() if said is None])
    }


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


def unready_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Act on each reader's verdict on an open PR the watch hears within one read of its landing, and redraw in place, each read, his card still asking his tap on a PR under a NOT READY he has not tapped, until cc-slack shows the reader's reason with no Approve and stops listing it as asking. A NOT READY on its head names its builder's rework, or after his tap, which stands and lands, its builder's follow-up on the same order; one on an older head, a head since moved or one no reader recorded, names the coordinator's recheck of the head now, tapped or not, since only a reader's verdict on that head says whether the finding still stands. A READY redraws his card, its check row gone, and on a card whose Approve a NOT READY pulled brings Approve back in place and says so once in the card's thread. A redraw or reply that fails goes again next read."""
    if watcher.cards is None:
        return []
    data = state.load(watcher.name)
    heard = [(key, pr) for key, pr in data["prs"].items() if pr["state"] == "open" and pr["verdict"] and hears(watcher, data, pr["order"])]
    lines = []
    for key, pr in heard:
        asking = view.card_for(watcher.cards["pr_cards"], pr["url"])
        pulls = asking is not None and state.owed_step(pr) in ("rework", "recheck")
        if not pulls and pr["verdict"]["said"]:
            continue
        before = dict(pr)
        try:
            said = verdict_said(watcher.name, key, pr, asking, pulls)
        except (state.DriveError, slack.SlackError) as error:
            lines += failed(watcher, "verdict", "redraw or reply for a reader's verdict", error, moment)
            continue
        watcher.failures.pop("verdict", None)
        with state.edit(watcher.name) as data:
            stored = data["prs"][key]
            if stored["verdict"] == pr["verdict"]:
                stored["verdict"]["said"] = True
            stored.update({field: pr[field] for field in ("pulled", "ready_said") if pr[field] != before[field]})
        lines += said
    return lines


def verdict_said(name: str, key: str, pr: dict, asking: dict | None, pulls: bool) -> list[str]:
    """Redraw his cards for the PR's verdict and say who owes its next step, the PR's pulled card and sent ready record updated in place."""
    from drive import talk

    verdict = pr["verdict"]
    if pulls:
        slack.redraw_pr_cards(pr["url"])
        pr["pulled"] = pr["pulled"] or asking["url"]
    if verdict["said"]:
        return []
    why = cut(verdict["why"], NOT_READY_CHARS)
    tap = pr["tap"] and f"after his {pr['tap']['label']}, which stands"
    if not verdict["ready"] and state.check_status(pr) == "recheck":
        return [f"PR {key}: NOT READY on an older head {tap}: check owed on {pr['head'][:7]}: {why} {ids(pr)}" if tap else f"PR {key}: {recheck_owed(pr)} {ids(pr)}"]
    if not verdict["ready"] and tap:
        return [f"PR {key}: NOT READY at {pr['head'][:7]} {tap}: follow-up owed on its order: {why} {ids(pr)}"]
    if not verdict["ready"]:
        return [f"PR {key}: NOT READY at {pr['head'][:7]}, rework owed: {why} {ids(pr)}"]
    if pulled := pr["pulled"]:
        pr["pulled"] = None
        if not slack.redraw_pr_cards(pr["url"]):
            return []
        pr["ready_said"] = pr["head"]
        talk.posted(name, {"text": f"Reworked at {verdict['head'][:7]} and passed the check; Approve is back", "thread_ts": view.card_thread(pulled)})
        return [f"CARD his card {pulled} for {key} has its Approve back in place: reworked at {verdict['head'][:7]} and passed the check"]
    if asking:
        slack.redraw_pr_cards(pr["url"])
    return []


def ready_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Send the communicator one ready record per order for the PRs that wait on his card alone, once a head, so `drive card` draws his card within one GitHub read of a PR going green and approved, with no coordinator turn; a record that fails to post goes again next read."""
    if watcher.cards is None:
        return []
    from drive import tools

    data = state.load(watcher.name)
    owed: dict[str, dict[str, str]] = {}
    for key, pr in data["prs"].items():
        if hears(watcher, data, pr["order"]) and card_owed(pr, data, watcher.cards):
            owed.setdefault(pr["serves"], {})[key] = pr["head"]
    lines = []
    for order_id, heads in owed.items():
        order = data["orders"][order_id]
        urls = " ".join(data["prs"][key]["url"] for key in heads)
        if order["text"].startswith(state.TOOLING):
            record = {"kind": "ready", "to": [f"communicator@{watcher.name}"], "text": f"{urls}; {order['text']}"}
        else:
            record = {"kind": "ready", "to": [f"communicator@{watcher.name}"], "text": f'{urls}; his order {order_id} "{order["text"]}"'}
            record |= {"attr": {"thread": order["thread"]}} if order["thread"] else {}
        try:
            tools.sent(watcher.name, LANE, [limits.shortened(record) or record])
        except state.DriveError as error:
            lines += failed(watcher, "ready post", "ready record post", error, moment)
            continue
        watcher.failures.pop("ready post", None)
        with state.edit(watcher.name) as data:
            for key, head in heads.items():
                data["prs"][key]["ready_said"] = head
        lines.append(f"CARD his card for {', '.join(heads)} is drawing: green and approved, ready record sent to the communicator")
    return lines


def card_owed(pr: dict, data: dict, cards: dict) -> bool:
    """Whether the watch owes a PR his card's ready record: it waits on its next step alone, that step his card while his-words check passed or runs, untapped, its order and design recorded, no card of his asking his tap on it or pulled of its Approve, and none sent for its head."""
    return (
        next_step(pr, data, cards)
        and state.owed_step(pr) == "card"
        and not pr["tap"]
        and bool(pr.get("serves") and pr.get("design"))
        and pr["ready_said"] != pr["head"]
        and not pr["pulled"]
        and view.card_for(cards["pr_cards"], pr["url"]) is None
    )


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
    owner["waits"] = holds
    if holds or not ended:
        return ""
    answered = [bare(wait) for wait in ended if wait not in out]
    return ", ".join([*([f"{', '.join(answered)} answered or done"] if answered else []), *(out[wait] for wait in ended if wait in out)])


def minutes(span: timedelta) -> int:
    return int(span.total_seconds() // 60)


def first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def mail_line(data: dict, job_id: str, message: dict, moment: datetime) -> str | None:
    """Record one message from a remote lane on its job, a final report setting its state and ending any question the lane asked before it, a question opening with a result other than ASK being that result; the event line it wakes the coordinator with, if any, which for a question naming `LOG <url>`s says drive job resume reads them ahead of the answer, and for an `ASK` that `drive job ask` keeps it, since a file of the job's already under `asks/` holds an earlier question."""
    job = data["jobs"][job_id]
    kind = message["type"]
    body = message["body"] or ""
    job["updated"] = state.stamp(moment)
    if kind == "escalation":
        last = first_line(body.split("Last output:")[-1])
        return f"JOB {job_id} {job['order']} STOPPED without a report, last output: {cut(last, 160)}"
    job["said"] = message["subject"] if kind == "worker_done" else first_line(body) or message["subject"]
    if kind == "status":
        return None
    job["report"] = body
    if kind == "question" and not state.reported(job["said"]):
        job.update(ask=message["id"], ask_at=job["updated"], state="waiting")
    elif kind in ("question", "worker_done"):
        if job.get("ask"):
            job.update(ask=None, state="running")
        state.take_result(data, job_id, job["said"])
    line = f"JOB {job_id} {job['order']} {job['said']}"
    if job.get("ask") != message["id"]:
        return line
    if asked := reads.logs(body):
        return f"{line}; drive job resume reads {asked} ahead of your answer"
    return f"{line}; {UNKEPT_ASK.format(job=job_id)}" if reads.asks(body) else line


def lane_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Each live remote lane's mail, from every worker of its job and not only the newest, read from its own Run and kept in its job's history: reports become JOB lines, read questions and refused ASKs queue on their job to be answered here, an open question kept before the watch read it as a read included. Each job keeps the sequence of the newest of its messages taken, judged and moved under the lock, so a watch reads a Run back to the oldest mark of the jobs it hears there, takes only their mail and passes over the rest, and a job's mail no watch has taken waits for whichever watch hears it next. A message its job's history already keeps is never taken again, so a watch whose mark another install's save lost replays nothing."""
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
    refusals, said = ask_refusals(watcher, data, lanes, messages, kept, moment)
    lines += said
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
                if message["type"] == "question" and (question := reads.request(message["body"] or "") or refusals.get(message["id"])):
                    data["jobs"][job_id].setdefault("reads", {})[message["id"]] = question
                    continue
                lines.append(mail_line(data, job_id, message, moment))
                track_lane_prs(data, job_id, message["body"] or "")
    return [line for line in lines + read_lines(watcher, data, moment) if line]


def ask_refusals(
    watcher: Watcher, data: dict, lanes: dict[str, dict[str | None, str]], messages: list[tuple[str, dict]], kept: Callable[[str], set[str]], moment: datetime
) -> tuple[dict[str, str], list[str]]:
    """The reply sending each new `ASK` a remote lane asked back to it, by its message id, when `drive job ask` would refuse it: its words and its card are checked here as it arrives, so an `ASK` out of shape never wakes the coordinator, who checks one in shape against his recorded answers alone. One cc-slack could not check goes on to the coordinator, whose `drive job ask <j>` checks it again."""
    refusals, lines = {}, []
    for run, message in messages:
        job_id = sender(lanes, run, message)
        if not job_id or message["sequence"] <= data["jobs"][job_id]["mail_seen"] or message["type"] != "question" or not reads.asks(message["body"] or "") or message["id"] in kept(job_id):
            continue
        try:
            limits.shape(message["body"], watcher.name, job_id)
        except state.DriveError as error:
            refusals[message["id"]] = f"{reads.REFUSED} {error}"
        except slack.SlackError as error:
            lines += failed(watcher, "ASK check", "remote ASK check", error, moment)
            continue
        watcher.failures.pop("ASK check", None)
    return refusals, lines


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


def track_lane_prs(data: dict, job_id: str, body: str) -> None:
    job = data["jobs"][job_id]
    for line in body.splitlines():
        if not PR_LINE.match(line):
            continue
        for match in state.PR_URL.finditer(line):
            key = f"{match[1]}/{match[2]}#{match[3]}"
            url = state.pr_url(key)
            data["prs"].setdefault(key, state.new_pr(url, job_id, job["order"]) | {"wants_tap": True})
            if url not in job["prs"]:
                job["prs"].append(url)


def read_lines(watcher: Watcher, data: dict, moment: datetime) -> list[str]:
    """Answer each `LOG` or `READ` question a live lane queued, here, since a Sprite holds no CI credentials or drive records; one the read cannot answer gets why as its answer and a `JOB` line saying so. A question leaves its job's queue only once its answer goes through, or, with no line since nothing is owed on it, Orca says it already holds another answer, since a lane whose wait ends waits again on the same question: a refused reply or a watch stopped mid-answer only delays it."""
    checkout = Path(data["roles"]["coordinator"]["cwd"])
    lines = []
    for job_id, job in live_lanes(watcher, data).items():
        for message_id, question in job.get("reads", {}).items():
            source = f"the answer to {job_id}'s"
            try:
                said, why = reads.answer(question, checkout), None
            except reads.ReadError as error:
                said = why = f"{question}: {error}"
            try:
                orca.reply(watcher.name, job["run"], message_id, said)
            except orca.OrcaError as error:
                if error.code != orca.ANSWERED_ELSEWHERE:
                    lines += read_failed(watcher, source, error, moment)
                    continue
            else:
                lines += [f"JOB {job_id} {job['order']} READ unanswered, its lane was told why: {why}"] if why else []
            watcher.failures.pop(source, None)
            with state.edit(watcher.name) as fresh:
                fresh["jobs"][job_id]["reads"].pop(message_id, None)
    return lines


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
    details = " ".join(f"{name}={value}" for name, value in event.items() if name not in ("kind", "at", "lane"))
    return f"EVENT {event['kind']} {details}".rstrip() + suffix


def event_lines(watcher: Watcher) -> list[str]:
    """Each new event the watch hears: one is written with the lane of the coordinator session hearing its order then, so a binding or a refiling after it moves it to no other watch, and one written with no lane is the drive's own watch's."""
    events = list(state.read_events(watcher.name, watcher.offset))
    if not events:
        return []
    watcher.offset = events[-1][1]
    prs = state.load(watcher.name)["prs"]
    return [event_line(event, prs) for event, _ in events if event["kind"] != "said" and event.get("lane", "root") == watcher.lane]


def overdue(order: dict, moment: datetime, away: bool) -> bool:
    if order["state"] in state.CLOSED_ORDER_STATES or not state.owed(order) or state.parse_time(state.owed_at(order)) > moment:
        return False
    said = order["due_said"]
    return said is None or not away and moment - state.parse_time(said) >= DUE_EVERY


def due_lines(watcher: Watcher, moment: datetime) -> list[tuple[str, str]]:
    """Each promised time that passed with no drive post in its order's thread or one of its cards' since it was promised, read from Slack, again every ten minutes until one is there; only the first while he is away. An order with no thread has no line to read, so its time is the drive's own, said once and kept by being said."""
    data = state.load(watcher.name)
    away = state.owner_away(data, moment)
    threads = {
        order_id: slack.order_posts(data, order_id, order) if state.promise_thread(data, order) else None
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


def answer_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Each answer of his that leaves a card still asking him or a PR not yet live with no mark, standing, superseded or reopened by it, again every ten minutes until each has one."""
    if watcher.cards is None or watcher.project:
        return []
    data = state.load(watcher.name)
    asked = superseded.asking(data, watcher.cards)
    owed = {
        answer_id: keys
        for answer_id, kept in data["answers"].items()
        if moment - state.parse_time(kept["said"]) >= DUE_EVERY and (keys := superseded.unmarked(kept, asked))
    }
    if not owed:
        return []
    with state.edit(watcher.name) as data:
        for answer_id in owed:
            data["answers"][answer_id]["said"] = state.stamp(moment)
    return [
        f'ANSWER {answer_id} "{cut(data["answers"][answer_id]["words"], 80)}": {superseded.counted(keys, asked)} with no mark,'
        f" standing, superseded or reopened by it; drive answer {answer_id} lists them"
        for answer_id, keys in owed.items()
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


def said_last(entry: dict) -> str:
    texts = [block["text"] for block in entry["message"]["content"] if block.get("type") == "text"]
    return first_line(texts[-1]) if texts else "nothing"


class Unrecorded(str):
    """A running local job's gap when its agent ended its turn on a result line its job never took, carrying that line."""

    line: str

    def __new__(cls, job_id: str, line: str) -> Self:
        made = super().__new__(cls, f'{job_id}\'s agent finished and its result is not recorded: "{cut(line, 100)}"')
        made.line = line
        return made

    def take(self, name: str, data: dict, job_id: str) -> None:
        state.take_result(data, job_id, self.line)


class Answered(str):
    """A running codex job's gap when its relay ended its turn after the codex-ask run it asked ended, carrying that run, which the watch takes as the job's result."""

    run: codex.Run

    def __new__(cls, job_id: str, run: codex.Run) -> Self:
        made = super().__new__(cls, f"{job_id}'s codex-ask run {run.state} and its result is not recorded")
        made.run = run
        return made

    def take(self, name: str, data: dict, job_id: str) -> None:
        codex.take(name, data, job_id, self.run)


def local_gap(job_id: str, job: dict, data: dict, moment: datetime) -> str | None:
    session = job.get("session")
    holder = next((held for held in state.bound_sessions(data).values() if session and held["session"] == session), None)
    if holder is None:
        return f"{job_id}'s agent ended with an earlier session"
    if not job.get("agent"):
        return f"{job_id} has no agent recorded"
    path = transcript(holder, job["agent"])
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
            return Unrecorded(job_id, line)
        if quiet >= QUIET_FOR:
            missing = "codex-ask run" if job["kind"] == "codex" else "result line"
            return f'{job_id}\'s agent ended its turn {minutes(quiet)} minutes ago on "{cut(line, 100)}" with no {missing}: SendMessage wakes it'
        return None
    if quiet >= QUIET_FOR:
        return f"{job_id}'s agent has written nothing for {minutes(quiet)} minutes"
    return None


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
    """A running job whose lane died or finished and was not resumed in the time a relaunch takes."""
    gone = moment - state.parse_time(ended["at"])
    if gone < RELAUNCH:
        return None
    return f"{job_id}'s lane ended {minutes(gone)} minutes ago ({cut(ended['why'], 60)}), was not resumed and its result is not recorded"


def launching(job: dict, moment: datetime) -> bool:
    """Whether the job's lane launch began within the time one takes, so a job with no dispatch yet is starting, not stuck."""
    return bool(job.get("launching")) and moment - state.parse_time(job["launching"]) < RELAUNCH


def agent_lines(watcher: Watcher, moment: datetime) -> list[str]:
    """Each running job's gap from its agent or lane, with each PR it has open, and a remote lane, running or waiting, whose worker died; a lane recorded by its dispatch alone gets its Run, Task and terminal from the worker-show read for its liveness. A local agent that ended its turn on a result line the drive already keeps, an `ASK <path>` naming the block `drive job ask` kept or its kept report's first line, has that line taken as the job's result, as its stop hook takes it, so a stop that skipped the take leaves no gap; a codex job's relay that ended its turn has its codex-ask run taken once that run ended, so a relay that ended before codex answered needs no wake."""
    data = state.load(watcher.name)
    gaps, deaths, heads, found, taken, lines = {}, {}, {}, {}, {}, []
    for order in heard_orders(watcher, data).values():
        for job_id in order["jobs"]:
            job = data["jobs"][job_id]
            running = job["state"] == "running"
            remote = job.get("where") == "remote"
            if running and job.get("branch") and (job.get("repo") or remote):
                heads.setdefault(job_id, set()).add((job.get("repo"), job["branch"]))
            if not remote:
                gap = running and local_gap(job_id, job, data, moment)
                if isinstance(gap, Answered) or isinstance(gap, Unrecorded) and state.kept_return(watcher.name, job_id, job, gap.line):
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
            if ended := next((death for death in job.get("deaths", []) if death["dispatch"] == job["dispatch"]), None):
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
            if not job.get("run"):
                found[job_id] = {"run": worker["dispatch"]["runId"], "task": worker["dispatch"]["taskId"], "terminal": worker["terminal"]["handle"]}
            if why := orca.death(worker):
                deaths[job_id] = (job["dispatch"], why)
            elif running:
                gaps[job_id] = remote_gap(job_id, worker, moment)
                if branch := ((worker.get("terminal") or {}).get("branch") or "").removeprefix("refs/heads/"):
                    heads.setdefault(job_id, set()).add((job.get("repo"), branch))
    if found or taken:
        with state.edit(watcher.name) as data:
            for job_id, lane in found.items():
                data["jobs"][job_id].update(lane)
            for job_id, gap in taken.items():
                gap.take(watcher.name, data, job_id)
    unlisted, failed = untracked_prs(watcher, data, heads, moment)
    watcher.agent_gaps = {job_id: stranded(job_id, data, gaps.get(job_id), unlisted.get(job_id, {})) for job_id in gaps.keys() | heads.keys()}
    return lines + failed + death_lines(watcher.name, deaths, moment)


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


def death_lines(name: str, deaths: dict[str, tuple[str, str]], moment: datetime) -> list[str]:
    """Record each remote lane's death on its job and say it once, with what the coordinator's next resume does."""
    if not deaths:
        return []
    lines = []
    with state.edit(name) as data:
        for job_id, (dispatch, why) in deaths.items():
            job = data["jobs"][job_id]
            if not state.lane_died(job, dispatch, why, moment):
                continue
            doing = "waited on" if job["state"] == "waiting" else "worked on"
            lines.append(
                f'LANE {job_id} {job["order"]} died {orca.place(job)}: {why}, while it {doing} '
                f'"{cut(job["said"] or job["goal"], 60)}". A message sent to it reaches no one; drive job resume '
                f'{job_id} "<its next step, or his answer>" relaunches it '
                f'{"in a fresh worktree " + orca.ON_MAC if state.on_mac(job) else "on a fresh Sprite"} with a handoff'
            )
    return lines


def can_wake(job_id: str, job: dict, data: dict, cards: dict | None, moment: datetime) -> bool:
    """Whether something can wake a waiting or built job: its lane's question, his open card, a wait the watch has not freed yet, an open PR of its, or a time to come, its own clock from drive job set --due or its orders' due times."""
    if job.get("ask") or view.open_cards(job, cards) or job["waits"]:
        return True
    if any(pr["job"] == job_id and pr["state"] == "open" for pr in data["prs"].values()):
        return True
    dues = [job["due"], *(data["orders"][order_id]["due"] for order_id in [job["order"], *job["carries"]])]
    return any(due and state.parse_time(due) > moment for due in dues)


def unanswered(job_id: str, job: dict, cards: dict | None, moment: datetime) -> str | None:
    """A lane's question nothing has answered for 30 minutes, whatever card or PR it has: only his card drawn for it since it came answers it, since an older card asks him something else."""
    if not job.get("ask"):
        return None
    asked = state.parse_time(job["ask_at"])
    drawn = (question for question in view.open_questions(job, cards) if question["card"] or question["waits"])
    if moment - asked < QUIET_FOR or any(state.parse_time(question["asked"]) >= asked for question in drawn):
        return None
    return f'{job_id}\'s lane asked "{cut(job["said"] or "", 80)}" {minutes(moment - asked)} minutes ago and nothing answered it: drive job resume {job_id} "<the answer>" answers it'


def job_gap(watcher: Watcher, job_id: str, job: dict, data: dict, moment: datetime) -> str | None:
    """Why the job leaves its parts with no one, None when it carries them, or SETTLING while a change in the last two minutes is still being handed over."""
    if moment - state.parse_time(job["updated"]) < HANDOFF:
        return SETTLING
    if job["state"] == "running":
        return watcher.agent_gaps.get(job_id)
    if job["state"] in ("waiting", "built"):
        if asked := unanswered(job_id, job, watcher.cards, moment):
            return asked
        if can_wake(job_id, job, data, watcher.cards, moment):
            return None
        waiting = f"is built on {job['branch']}" if job["state"] == "built" else f'waits on "{cut(job["said"] or "nothing named", 80)}"'
        return f"{job_id} {waiting} and no open PR, card or due time can wake it"
    if job["state"] == "done":
        drawn = [question for question in job["questions"] if question["card"]]
        if not drawn:
            return f"{job_id} answered and the answer is not posted to him"
        answered = "card is" if len(drawn) == 1 else "cards are"
        return None if view.open_cards(job, watcher.cards) else f"{job_id}'s {answered} answered and no job carries his answer"
    return f"{job_id} {job['state']}"


def part_gap(watcher: Watcher, data: dict, order_id: str, part: str, moment: datetime) -> str | None:
    order = data["orders"][order_id]
    jobs = [(job_id, data["jobs"][job_id]) for job_id in order["jobs"] if part in state.job_parts(data["jobs"][job_id], order_id)]
    gaps = [job_gap(watcher, job_id, job, data, moment) for job_id, job in jobs]
    if stuck := [gap for (_, job), gap in zip(jobs, gaps, strict=True) if gap and (job["state"] == "running" or job["state"] == "waiting" and job.get("ask"))]:
        return stuck[-1]
    if order["parts"][part]["posted"] or any(job["state"] == "done" and job["kind"] == "builder" for _, job in jobs):
        return None
    if not jobs:
        return "no job" if moment - state.parse_time(order["updated"]) >= UNSTAFFED_FOR else None
    return None if None in gaps else SETTLING if SETTLING in gaps else gaps[-1]


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
    if key[0] in due or not said or moment - state.parse_time(said) >= TICK_EVERY:
        return "say"
    return None


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
    return lines


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
    data = state.load(watcher.name)
    if state.owner_away(data, moment):
        return []
    owing = [
        key
        for key, order in data["orders"].items()
        if hears(watcher, data, key) and not state.communicators(order) and owes_line(order, data["jobs"], moment)
    ]
    return [f"TICK {' '.join(owing)}"] if owing else []


def run_pass(watcher: Watcher, moment: datetime) -> list[str]:
    """One loop of the watch: his cards, GitHub, his PR cards whose PRs all ended, the waits his answers or a job's end release, his answers that leave cards unmarked, lane mail, agents, the communicator's compaction and the status page every 60 s, GitHub again next poll after a failure, new events, promised times still owed a line, job clocks run out, fixes proven live whose bug report has not heard, parts nothing carries, a tick every 30 minutes while he is here."""
    lines = []
    if watcher.read_at is None or moment - watcher.read_at >= READ_EVERY:
        watcher.read_at = moment
        view.refresh_cards(watcher.name)
        found, source = view.stored_cards(watcher.name)
        watcher.cards = found if source == "live" else None
        lines += github_lines(watcher, moment)
        lines += settle_lines(watcher, moment)
        lines += unready_lines(watcher, moment)
        lines += waits_lines(watcher, moment)
        lines += ready_lines(watcher, moment)
        lines += waiting_lines(watcher, moment)
        lines += answer_lines(watcher, moment)
        lines += lane_lines(watcher, moment)
        lines += agent_lines(watcher, moment)
        lines += compact_lines(watcher, moment)
        lines += page_lines(watcher, moment)
    lines += event_lines(watcher)
    due = due_lines(watcher, moment)
    lines += [line for _, line in due]
    lines += clock_lines(watcher, moment)
    lines += tell_lines(watcher, moment)
    lines += orphan_lines(watcher, moment, {order_id for order_id, _ in due})
    if moment - watcher.ticked_at >= TICK_EVERY:
        watcher.ticked_at = moment
        lines += tick_lines(watcher, moment)
    return handed(watcher, [LINE_BREAKS.sub(" ", line) for line in lines], moment)


def communicators_line(line: str, data: dict) -> bool:
    found = ABOUT.search(line)
    order = found and data["orders"].get(found["order"] or found["named"])
    return bool(order) and state.communicators(order)


def needs_step(line: str, data: dict) -> bool:
    """Whether a line asks for a step now: any line but a PR's, and a PR's when its job is not running to take it, its new head, checks passed or approval only when the coordinator owes it a step: its exempt landing, a reader on its new head under a NOT READY, or the order and design his card quotes; never his card itself, which the watch draws, nor when its author or its own builder lands it."""
    found = PR_EVENT.match(line)
    if not found:
        return not line.startswith("PR ")
    pr = data["prs"][found["key"]]
    owes = {"landing": True, "recheck": True, "card": not pr.get("serves")}.get(state.owed_step(pr), False)
    return data["jobs"][found["job"]]["state"] != "running" and (owes or not found["change"].startswith(OWN_MOVES))


def lane_move(line: str, data: dict) -> str | None:
    """The remote lane a PR's move goes to: its job's, when that lane returned and waits for its next event."""
    found = PR_EVENT.match(line)
    if not found or not found["change"].startswith(OWNER_MOVES):
        return None
    job = data["jobs"][found["job"]]
    return found["job"] if job.get("where") == "remote" and job.get("dispatch") and job["state"] == "waiting" else None


def wakes(line: str, data: dict) -> bool:
    """Whether the coordinator acts on the line now: his tap, a promised time, an orphan, his answer, a lane's report or death, his return, a stopped watch, or a PR's line that asks for a step, such as one going green and approved that waits on his card or its exempt landing."""
    return line.startswith(WAKES) or line.startswith("PR ") and needs_step(line, data)


def handed(watcher: Watcher, lines: list[str], moment: datetime) -> list[str]:
    """Hand each line to the one who acts on it: an order's the communicator holds to the communicator, a PR's move to its job's remote lane waiting for it, and the rest, returned, to the coordinator, with a lane's lines when its resume fails."""
    if not lines and not watcher.unsent.get("communicator"):
        return []
    data = state.load(watcher.name)
    tooled = [line for line in lines if communicators_line(line, data) and needs_step(line, data)]
    moves: dict[str, list[str]] = {}
    kept = []
    for line in lines:
        if communicators_line(line, data):
            continue
        if job_id := lane_move(line, data):
            moves.setdefault(job_id, []).append(line)
        else:
            kept.append(line)
    for job_id, said in moves.items():
        try:
            reads.run([str(DRIVE), "job", "resume", job_id, "\n".join(said), "--drive", watcher.name], Path(data["roles"]["coordinator"]["cwd"]))
        except reads.ReadError as error:
            kept += [f"{line}; drive job resume {job_id} failed: {error}" for line in said]
    return kept + sent(watcher, "communicator", tooled, moment)


def sent(watcher: Watcher, lane: str, lines: list[str], moment: datetime) -> list[str]:
    """Post each line, after any that failed to post before, to the lane over cci: a coordinator's line it acts on now wakes it, its others reach it together at most every five minutes, and a line that fails goes again next pass, said from its third failure in a row."""
    owed = watcher.unsent.pop(lane, []) + lines
    if not owed:
        return []
    data = state.load(watcher.name)
    refusal, unsent = None, []
    for line in owed:
        kind = "handoff" if lane == "communicator" or wakes(line, data) else "evidence"
        topic = f"watch-{hashlib.sha1(line.encode()).hexdigest()[:10]}"
        argv = ["cci", "post", "--drive", watcher.name, "--lane", LANE, "--kind", kind, "--to", f"{lane}@{watcher.name}", "--topic", topic, "--text", line]
        try:
            reads.run(argv, Path(data["roles"]["coordinator"]["cwd"]))
        except reads.ReadError as error:
            unsent.append(line)
            refusal = error
    what = {"communicator": "handoff post to the communicator", "root": "post to the coordinator"}.get(lane, f"post to the sub-coordinator for {watcher.project}")
    if refusal is None:
        watcher.failures.pop(what, None)
        return []
    watcher.unsent[lane] = unsent
    return failed(watcher, what, what, refusal, moment)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def holder(path: Path) -> int | None:
    held = path.read_text().strip() if path.exists() else ""
    return int(held) if held.isdigit() and alive(int(held)) else None


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
    """Start the drive's watch, or a project's, as its own process unless one runs, its output in its own log; the line saying which. A project's watch first reads the events from where the log ended as its sub-coordinator was bound."""
    watcher = f"the watch for {project}" if project else "the watch"
    reader = f"the sub-coordinator for {project}" if project else "the coordinator"
    if pid := holder(state.pid_path(name, project)):
        return f"{watcher} runs as pid {pid} and posts its lines to {reader} over cci; arm no Monitor for it"
    env = {key: value for key, value in os.environ.items() if key != "CLAUDE_CODE_SESSION_ID"} | {"CLAUDE_LONG_RUNNING_LANE": LANE}
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


def upgraded() -> Path | None:
    """The drive of the newest installed long-running, when the watch runs from the plugin cache and a newer one is installed."""
    if not DRIVE.is_relative_to((config_dir() / "plugins" / "cache").resolve()):
        return None
    plugins = json.loads((config_dir() / "plugins" / "installed_plugins.json").read_text())["plugins"]
    installs = [entry for key, entries in plugins.items() if key.partition("@")[0] == "long-running" for entry in entries]
    newest = Path(max(installs, key=lambda entry: entry.get("lastUpdated") or entry.get("installedAt") or "")["installPath"]) / "bin" / "drive"
    return None if newest.resolve() == DRIVE else newest


def stopped(watcher: Watcher, error: Exception) -> None:
    again = shlex.join(["drive", "watch", *(["--project", watcher.project] if watcher.project else [])])
    line = f"WATCH stopped: {cut(str(error) or type(error).__name__, 200)}; {again} starts it again"
    with contextlib.suppress(reads.ReadError):
        sent(watcher, watcher.lane, [line], state.now())


def main(drive_name: str | None = None, project: str | None = None) -> int:
    """Run the drive's watch, or a project's, until it stops, once a watch an older install ran under a Monitor has ended."""
    name = state.resolve(drive_name)
    if not claim(name, project):
        return 0
    try:
        while holder(state.monitor_pid_path(name)):
            time.sleep(POLL_SECONDS)
        held = {} if project else {"root": state.take_held_lines(name)}
        watching(Watcher(name, offset=state.watch_cursor(name, project), ticked_at=state.now(), project=project, unsent=held))
    finally:
        release(name, project)
    return 0


def watching(watcher: Watcher) -> None:
    """Hand each pass's lines to whoever acts on them, and let a newer install take over in place before a pass, so no pass runs older code on a drive a newer install saved; a crash wakes the coordinator session it posts to first."""
    state.save_watch_cursor(watcher.name, watcher.offset, watcher.project)
    try:
        while True:
            if (newer := upgraded()) and not any(watcher.unsent.values()):
                os.execv(newer, loop_argv(newer, watcher.name, watcher.project))
            read_from, moment = watcher.offset, state.now()
            for line in sent(watcher, watcher.lane, run_pass(watcher, moment), moment):
                print(line, flush=True)
            if watcher.offset != read_from:
                state.save_watch_cursor(watcher.name, watcher.offset, watcher.project)
            time.sleep(POLL_SECONDS)
    except Exception as error:
        stopped(watcher, error)
        raise
