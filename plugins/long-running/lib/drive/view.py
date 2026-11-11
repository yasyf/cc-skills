"""One model of where a drive stands, which the dashboard and the Slack status page both draw.

`build` returns:

- `queue`: what waits on him, highest priority first: his open question cards, those holding up the most finished
  work first, then PRs ready for his tap, then promised times that passed. Each item has `kind` (`question`,
  `tap`, `late`), `title`, `detail`,
  `since` (when it started waiting, when known), `due`, `url` (where he acts), `links`, and for a question or a
  tap with a Slack card, `card` (the whole card cc-slack draws) and `answer` (the card's permalink, which takes his
  answer from any surface). A question also has `holds`: how many green approved open PRs and built jobs wait on
  it, alone or beside other cards of his, and `blocks`: the title of each feature it holds up.
- `status`: the drive's features (its open orders, and orders done in the last day) in groups by state:
  `blocked`, `running`, `built`, `waiting`, `merged`, `live`, `not started`. Each feature has `title`, `state`,
  `now` (its latest job line), `due`, `late`, `proof`, `updated`, `url`, `prs`, each PR with `title`, `url`,
  `state`, `checks`, `review` and `tap`, `built`, each of its jobs built on the recommended design and
  waiting on his answers before it opens a PR, with its `job`, `branch` and `waits` (each with `title` and `url`),
  and `cards`, each open question card of his it waits on, with its `title`, `url` and `asked`.
- `counts`, by queue and state; `read` adds `cards`: `live`, or why his Slack cards could not be read.

`board` adds what only the dashboard draws: `progress` (the drive's PRs by state, red ones and running jobs),
`work` (each running or waiting job of a shown feature), `finished` (jobs done, failed or stopped in the last day,
newest first), `history` (the last day, newest first), `asks` (every order of his, newest first, with the Slack
thread it came in, where it has one, so the page can draw that thread), `review` (every open PR, in `you`,
`not ready` or `decided`, with its Slack card and the `thread` under that card when it has one), `incidents` (his
open incident orders, each with `id`, `title` and `created`), `milestones` and each feature's `text`, `note`,
`created` and `jobs`.

Each of `milestones` is a name the coordinator gave some of his orders, in the order its first order came in, with
its `state` rolled up from those orders' features, the `features` themselves, its `parts` (each part of those orders,
with `state` `done` or the most urgent of the nodes before it, `blocks` and `doing`), and the chart of what stands
in its way: `nodes`, each open part and each job, PR or card of his before one, with `kind`, `state` (`bad`, `you`,
`doing`, `waiting`, or a part's `not started`), `text`, `how`, `order`, `url` and `since`, and `edges`, each an
arrow `from` one node `to` the node it holds up, a part's arrow going to `milestone`.
"""

import json
import os
import re
import subprocess
from collections.abc import Iterable
from datetime import datetime, timedelta

from drive import slack, state
from drive.card import PARENTHETICAL, RECORD_ID, kind_and_title, without_records
from drive.render import cut

STATES = ("blocked", "running", "built", "waiting", "merged", "live", "not started")
LIVE_FOR = timedelta(days=1)
SLACK_LINK = re.compile(r"<(?:[^|>]*\|)?([^>]*)>")
SLACK_TS = re.compile(r"/p(\d+)")
BOARD_PICK = re.compile(r"^Owner on board \w+, ")
COMMAS = re.compile(r"\s*,(?:\s*,)*")
SPACES = re.compile(r"\s+(?=[.;:!?])|(?<=\s)\s+")
TITLE_CHARS = 100
NOW_CHARS = 180
NO_CARDS = {"questions": [], "pr_cards": [], "team_url": ""}
UNREAD = "not read yet: the drive's watch reads them every minute"
HISTORY_FOR = timedelta(days=1)
HISTORY_EVENTS = ("tap", "refused")
NODE_STATES = ("bad", "you", "doing", "waiting")
JOB_TONES = {"running": "doing", "waiting": "waiting", "built": "waiting", "failed": "bad"}
BLOCKER_WORDS = {("card", "you"): "Your answer", ("pr", "you"): "Your tap", ("pr", "bad"): "Tests fail", ("job", "bad"): "Failed"}


class CardsError(Exception):
    pass


def cards(name: str) -> dict:
    """His open question cards for the drive and the PR cards waiting on his tap, from cc-slack's stored state."""
    try:
        result = subprocess.run(
            [slack.launcher(), "drive-cards", "--drive", name], check=False, capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CardsError(str(error)) from None
    if result.returncode:
        raise CardsError(" ".join(result.stderr.split()) or f"cc-slack exited {result.returncode}")
    return json.loads(result.stdout)


def refresh_cards(name: str) -> None:
    """Store his cards as cc-slack lists them now, rewriting the drive's cards file only when they changed."""
    try:
        found = {"source": "live", **cards(name)}
    except CardsError as error:
        found = {"source": f"unavailable: {error}", **NO_CARDS}
    path = state.cards_path(name)
    text = json.dumps(found, indent=2, sort_keys=True) + "\n"
    if path.exists() and path.read_text() == text:
        return
    tmp = path.with_name(f"cards.json.{os.getpid()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def stored_cards(name: str) -> tuple[dict, str]:
    """His cards as the drive last stored them, and `live` or why they are missing."""
    path = state.cards_path(name)
    if not path.exists():
        return NO_CARDS, UNREAD
    found = json.loads(path.read_text())
    return found, found.pop("source")


def live_cards(name: str) -> dict | None:
    """His cards as last stored, or None when they could not be read."""
    found, source = stored_cards(name)
    return found if source == "live" else None


def message_key(url: str) -> str:
    match = SLACK_TS.search(url)
    return match[1] if match else url


def card_thread(url: str) -> str:
    key = message_key(url)
    return f"{key[:-6]}.{key[-6:]}"


def card_open(url: str, found: dict | None) -> bool:
    """Whether his card, by its Slack link or waits id, is still open; with his cards unread, every card counts as open."""
    if found is None:
        return True
    shown = {message_key(card["url"]) for card in [*found["questions"], *found["pr_cards"]]}
    waits = {card["card"].get("waits") for card in found["questions"]}
    return message_key(url) in shown or url in waits


def holding(drive: dict, waits: list[str], found: dict | None) -> list[str]:
    """The waits of a job or PR that still hold: each of his cards still open, each job not finished."""
    jobs = drive["jobs"]
    return [wait for wait in waits if (jobs[wait]["state"] not in state.FINISHED_JOB_STATES if wait in jobs else card_open(wait, found))]


def read(name: str, moment: datetime) -> dict:
    """The named drive's model from its stored state and its stored cards."""
    found, source = stored_cards(name)
    return build(state.load(name), found, moment) | {"cards": source}


def plain(text: str, limit: int) -> str:
    """The first line of his words as a title: Slack links as their labels, with no record ids."""
    first = next((line for line in text.splitlines() if line.strip()), "")
    first = BOARD_PICK.sub("", SLACK_LINK.sub(lambda link: link[1], first))
    first = RECORD_ID.sub("", PARENTHETICAL.sub(lambda match: "" if RECORD_ID.search(match[0]) else match[0], first))
    return cut(SPACES.sub("", COMMAS.sub(",", first)).strip(" ,"), limit)


def said(text: str | None) -> str:
    return cut(without_records(text or ""), NOW_CHARS)


def thread_url(drive: dict, team_url: str, thread: str | None) -> str | None:
    channel = drive["slack"]["channel"]
    if not (team_url and channel and thread):
        return None
    return f"{team_url}archives/{channel}/p{thread.replace('.', '')}"


def pr_title(pr: dict) -> str:
    return kind_and_title(pr["title"])[1] if pr["title"] else pr["url"]


def tap_of(pr: dict) -> str | None:
    if pr.get("tap"):
        return pr["tap"]["label"]
    return "wanted" if pr["wants_tap"] and pr["state"] == "open" else None


def pr_entry(pr: dict) -> dict:
    return {
        "title": pr_title(pr),
        "url": pr["url"],
        "state": pr["state"],
        "checks": pr["checks"],
        "review": pr["review"],
        "tap": tap_of(pr),
    }


def card_for(pr_cards: list[dict], url: str) -> dict | None:
    return next((card for card in reversed(pr_cards) if url in card["prs"]), None)


def waits_on_tap(pr: dict, card: dict | None) -> bool:
    asked = pr["wants_tap"] and not pr.get("tap") and not pr.get("exempt") and not pr["waits"] and pr["state"] == "open"
    return asked and (pr["checks"] == "pass" or card is not None)


def tap_detail(pr: dict) -> str:
    checks = {"pass": "green", "fail": "red", "pending": "CI running"}.get(pr["checks"], "no CI")
    review = {"approved": "approved", "changes": "changes requested"}.get(pr["review"], "no review yet")
    changed = f"; changed since your tap: {pr['fundamental']}" if pr.get("fundamental") else ""
    return f"{checks}, {review}{changed}"


def question_item(drive: dict, card: dict, features: list[dict]) -> dict:
    pick = f"Recommended: {card['recommended']}" if card.get("recommended") else " / ".join(card["options"])
    return {
        "kind": "question",
        "holds": held_up(drive, card),
        "blocks": [entry["title"] for entry in features if any(held["url"] == card["url"] for held in entry["cards"])],
        "title": card["question"],
        "detail": pick,
        "since": card["asked"],
        "due": None,
        "url": card["url"],
        "links": [],
        "card": card["card"],
        "answer": card["url"],
    }


def held_up(drive: dict, card: dict) -> int:
    """How many green approved open PRs and built jobs wait on his question card."""
    names = {card["card"].get("waits"), message_key(card["url"])}
    finished = [pr for pr in drive["prs"].values() if pr["state"] == "open" and pr["checks"] == "pass" and pr["review"] == "approved"]
    built = [job for job in drive["jobs"].values() if job["state"] == "built"]
    return sum(any(message_key(wait) in names for wait in owner["waits"]) for owner in [*finished, *built])


def tap_item(pr: dict, card: dict | None) -> dict:
    if not card:
        return {"kind": "tap", "title": pr_title(pr), "detail": f"{tap_detail(pr)}; no Slack card yet", "since": None, "due": None, "url": pr["url"], "links": []}
    return {
        "kind": "tap",
        "title": pr_title(pr),
        "detail": tap_detail(pr),
        "since": card["asked"],
        "due": None,
        "url": card["url"],
        "links": [{"label": "Pull request", "url": pr["url"]}],
        "card": card["card"],
        "answer": card["url"],
    }


def late_item(drive: dict, order: dict, team_url: str) -> dict:
    return {
        "kind": "late",
        "title": plain(order["text"], TITLE_CHARS),
        "detail": said(order.get("note")),
        "since": order["created"],
        "due": state.owed_at(order),
        "url": thread_url(drive, team_url, order.get("thread")),
        "links": [],
    }


def is_late(order: dict, moment: datetime) -> bool:
    return order["state"] not in state.CLOSED_ORDER_STATES and state.owed(order) and state.parse_time(state.owed_at(order)) <= moment


def queue(drive: dict, found: dict, moment: datetime, features: list[dict]) -> list[dict]:
    taps = [(pr, card) for pr in drive["prs"].values() if waits_on_tap(pr, card := card_for(found["pr_cards"], pr["url"]))]
    taps.sort(key=lambda entry: (entry[1] is None, entry[1] and state.parse_time(entry[1]["asked"])))
    late = sorted((order for order in drive["orders"].values() if is_late(order, moment)), key=lambda order: state.parse_time(state.owed_at(order)))
    return [
        *sorted((question_item(drive, card, features) for card in found["questions"]), key=lambda item: -item["holds"]),
        *(tap_item(pr, card) for pr, card in taps),
        *(late_item(drive, order, found["team_url"]) for order in late),
    ]


def feature_state(order: dict, jobs: list[dict], prs: list[dict]) -> str:
    if order["state"] == "done":
        return "live"
    running = any(job["state"] == "running" for job in jobs)
    red = any(pr["state"] == "open" and pr["checks"] == "fail" for pr in prs)
    if any(job["state"] == "failed" for job in jobs) or red and not running:
        return "blocked"
    if running:
        return "running"
    if any(job["state"] == "built" for job in jobs):
        return "built"
    if any(pr["state"] == "merged" for pr in prs) and all(pr["state"] != "open" for pr in prs):
        return "merged"
    if order["state"] == "waiting" or any(job["state"] == "waiting" for job in jobs):
        return "waiting"
    return "not started"


def latest_line(order: dict, jobs: list[dict]) -> str:
    if order["state"] == "done" and order.get("proof"):
        return said(order["proof"])
    if lines := [job for job in jobs if job.get("said")]:
        return said(max(lines, key=lambda job: state.parse_time(job["updated"]))["said"])
    return said(order.get("note"))


def shown(order: dict, moment: datetime) -> bool:
    if order["state"] == "done":
        return moment - state.parse_time(order["updated"]) <= LIVE_FOR
    return order["state"] != "dropped"


def asked_card(found: dict, wait: str) -> dict | None:
    return next((card for card in found["questions"] if card["card"].get("waits") == wait or message_key(card["url"]) == message_key(wait)), None)


def wait_entry(drive: dict, wait: str, found: dict) -> dict:
    if wait in drive["jobs"]:
        return {"title": f"{wait}: {cut(drive['jobs'][wait]['goal'], TITLE_CHARS)}", "url": None}
    if card := asked_card(found, wait):
        return {"title": card["question"], "url": card["url"]}
    return {"title": "his question card", "url": wait if wait.startswith("https://") else None}


def built_entry(drive: dict, job_id: str, found: dict) -> dict:
    job = drive["jobs"][job_id]
    return {"job": job_id, "branch": job["branch"], "waits": [wait_entry(drive, wait, found) for wait in job["waits"]]}


def blocking(drive: dict, jobs: list[dict], prs: list[dict], found: dict) -> list[dict]:
    """His open question cards a feature waits on: each its jobs and open PRs wait on, and each a job of it asked."""
    owners = [*jobs, *(pr for pr in prs if pr["state"] == "open")]
    named = {message_key(wait) for owner in owners for wait in owner["waits"] if wait not in drive["jobs"]}
    named |= {message_key(job["card"]) for job in jobs if job.get("card")}
    held = (card for card in found["questions"] if {message_key(card["url"]), card["card"].get("waits")} & named)
    return [{"title": card["question"], "url": card["url"], "asked": card["asked"]} for card in held]


def feature(drive: dict, order_id: str, order: dict, moment: datetime, found: dict) -> dict:
    jobs = [drive["jobs"][job_id] for job_id in order["jobs"]]
    prs = [pr for pr in drive["prs"].values() if pr["order"] == order_id]
    return {
        "id": order_id,
        "title": plain(order["text"], TITLE_CHARS),
        "state": feature_state(order, jobs, prs),
        "now": latest_line(order, jobs),
        "milestone": order["milestone"],
        "due": order.get("due"),
        "late": is_late(order, moment),
        "proof": order.get("proof"),
        "updated": order["updated"],
        "url": thread_url(drive, found["team_url"], order.get("thread")),
        "prs": [pr_entry(pr) for pr in prs],
        "built": [built_entry(drive, job_id, found) for job_id in order["jobs"] if drive["jobs"][job_id]["state"] == "built"],
        "cards": blocking(drive, jobs, prs, found),
    }


def status(drive: dict, moment: datetime, found: dict) -> list[dict]:
    features = [feature(drive, order_id, order, moment, found) for order_id, order in drive["orders"].items() if shown(order, moment)]
    features.sort(key=lambda entry: state.parse_time(entry["updated"]), reverse=True)
    features.sort(key=lambda entry: (entry["due"] is None, entry["due"] and state.parse_time(entry["due"])))
    return [{"state": name, "features": [entry for entry in features if entry["state"] == name]} for name in STATES]


def milestones(drive: dict, found: dict, moment: datetime) -> list[dict]:
    """His orders under the milestones the coordinator named, each milestone in the order its first order came in; dropped orders leave it."""
    named: dict[str, list[str]] = {}
    for order_id, order in sorted(drive["orders"].items(), key=lambda item: state.parse_time(item[1]["created"])):
        if order["milestone"] and order["state"] != "dropped":
            named.setdefault(order["milestone"], []).append(order_id)
    prs: dict[str, list[tuple[str, dict]]] = {}
    for key, pr in drive["prs"].items():
        prs.setdefault(pr["job"], []).append((key, pr))
    return [milestone(Chart(drive, found, prs), name, order_ids, moment) for name, order_ids in named.items()]


def milestone(chart: "Chart", name: str, order_ids: list[str], moment: datetime) -> dict:
    orders = chart.drive["orders"]
    features = [feature(chart.drive, order_id, orders[order_id], moment, chart.found) for order_id in order_ids]
    return {
        "name": name,
        "state": rolled_up({entry["state"] for entry in features}),
        "features": features,
        "parts": [chart.part(order_id, part) for order_id in order_ids for part in orders[order_id]["parts"]],
        "nodes": list(chart.nodes.values()),
        "edges": [{"from": source, "to": target} for target, sources in chart.into.items() for source in sources],
    }


def rolled_up(states: set[str]) -> str:
    """Live once every order is live; otherwise the first state its other orders are in, blocked before running and so on."""
    return next((name for name in STATES if name != "live" and name in states), "live")


class Chart:
    """What stands in one milestone's way: each open part of its orders, and before each the jobs, PRs and cards of his it waits on."""

    def __init__(self, drive: dict, found: dict, prs: dict[str, list[tuple[str, dict]]]) -> None:
        self.drive, self.found, self.prs = drive, found, prs
        self.nodes: dict[str, dict] = {}
        self.into: dict[str, list[str]] = {}

    def arrow(self, source: str, target: str) -> None:
        sources = self.into.setdefault(target, [])
        if source not in sources:
            sources.append(source)

    def node(self, node_id: str, kind: str, tone: str, text: str, how: str, order: str | None, url: str | None, since: str | None) -> None:
        self.nodes[node_id] = {"id": node_id, "kind": kind, "state": tone, "text": text, "how": how, "order": order, "url": url, "since": since}

    def part(self, order_id: str, part: str) -> dict:
        order, jobs = self.drive["orders"][order_id], self.drive["jobs"]
        mine = [job_id for job_id in order["jobs"] if part in state.job_parts(jobs[job_id], order_id)]
        title = plain(order["text"], TITLE_CHARS) if len(order["parts"]) == 1 else part
        entry = {"id": f"part:{order_id}:{part}", "order": order_id, "title": title, "state": "done", "blocks": "", "doing": ""}
        shipped = order["parts"][part]["posted"] or any(jobs[job_id]["state"] == "done" and jobs[job_id]["kind"] == "builder" for job_id in mine)
        if order["state"] == "done" or shipped and all(jobs[job_id]["state"] in state.FINISHED_JOB_STATES for job_id in mine):
            return entry
        self.node(entry["id"], "part", "waiting", title, "", order_id, None, order["updated"])
        self.arrow(entry["id"], "milestone")
        for job_id in mine:
            if jobs[job_id]["state"] not in state.FINISHED_JOB_STATES or jobs[job_id]["state"] == "failed" and job_id == mine[-1]:
                self.arrow(self.job(job_id), entry["id"])
        for key, pr in (owned for job_id in mine for owned in self.prs.get(job_id, [])):
            if pr["state"] == "open":
                self.arrow(self.pr(key, pr), entry["id"])
        before = [self.nodes[node_id] for node_id in self.upstream(entry["id"])]
        tone = next((name for name in NODE_STATES if any(node["state"] == name for node in before)), "waiting" if mine else "not started")
        self.nodes[entry["id"]]["state"] = tone
        stuck = next((node for name in ("bad", "you") for node in before if node["state"] == name), None)
        running = next((node["how"] for node in before if node["kind"] == "job" and node["state"] == "doing" and node["how"]), "")
        blocks = f"{BLOCKER_WORDS[stuck['kind'], stuck['state']]}: {stuck['text']}" if stuck else ""
        return entry | {"state": tone, "blocks": blocks, "doing": running}

    def upstream(self, node_id: str) -> list[str]:
        seen: list[str] = []
        ahead = [node_id]
        while ahead:
            for source in self.into.get(ahead.pop(), []):
                if source not in seen:
                    seen.append(source)
                    ahead.append(source)
        return seen

    def job(self, job_id: str) -> str:
        node_id = f"job:{job_id}"
        if node_id not in self.nodes:
            job = self.drive["jobs"][job_id]
            self.node(node_id, "job", JOB_TONES[job["state"]], cut(job["goal"], TITLE_CHARS), said(job["said"]), job["order"], None, job["updated"])
            self.waits(job["waits"], node_id)
            if job.get("card") and card_open(job["card"], self.found):
                self.arrow(self.card(job["card"]), node_id)
        return node_id

    def pr(self, key: str, pr: dict) -> str:
        node_id = f"pr:{key}"
        if node_id not in self.nodes:
            tapped = waits_on_tap(pr, card_for(self.found["pr_cards"], pr["url"]))
            tone = "bad" if pr["checks"] == "fail" else "you" if tapped else "doing" if pr["checks"] == "pending" else "waiting"
            self.node(node_id, "pr", tone, pr_title(pr), tap_detail(pr), pr["order"], pr["url"], pr["updated"])
            self.waits(pr["waits"], node_id)
        return node_id

    def waits(self, waits: list[str], node_id: str) -> None:
        for wait in holding(self.drive, waits, self.found):
            self.arrow(self.job(wait) if wait in self.drive["jobs"] else self.card(wait), node_id)

    def card(self, wait: str) -> str:
        node_id = f"card:{message_key(wait)}"
        if node_id not in self.nodes:
            asked = asked_card(self.found, wait)
            self.node(node_id, "card", "you", asked["question"] if asked else "his question card", "", None, asked and asked["url"], asked and asked["asked"])
        return node_id


def build(drive: dict, found: dict, moment: datetime) -> dict:
    """The drive's model from its stored state, his open cards as cc-slack lists them, and the time it is drawn for."""
    groups = status(drive, moment, found)
    waiting = queue(drive, found, moment, [entry for group in groups for entry in group["features"]])
    counts = {"queue": len(waiting), **{group["state"]: len(group["features"]) for group in groups}}
    return {"drive": drive["name"], "at": state.stamp(moment), "counts": counts, "queue": waiting, "status": groups}


def job_entry(job_id: str, job: dict) -> dict:
    return {
        "id": job_id,
        "feature": job["order"],
        "kind": job["kind"],
        "goal": job["goal"],
        "state": job["state"],
        "where": job.get("where") or "local",
        "said": said(job["said"]),
        "updated": job["updated"],
        "prs": len(job["prs"]),
        "branch": job["branch"],
        "waits": len(job["waits"]),
    }


def progress(drive: dict) -> dict:
    prs = drive["prs"].values()
    return {
        "prs": len(prs),
        "merged": sum(pr["state"] == "merged" for pr in prs),
        "open": sum(pr["state"] == "open" for pr in prs),
        "red": sum(pr["state"] == "open" and pr["checks"] == "fail" for pr in prs),
        "running": sum(job["state"] == "running" for job in drive["jobs"].values()),
    }


def history(drive: dict, events: Iterable[dict], moment: datetime, team_url: str) -> list[dict]:
    """What happened in the last day, newest first: his orders, features gone live, merges, failed jobs, his taps and refused cards."""
    since = moment - HISTORY_FOR
    prs = drive["prs"]
    happened = []
    for order_id, order in drive["orders"].items():
        url = thread_url(drive, team_url, order.get("thread"))
        happened.append({"at": order["created"], "kind": "order", "text": plain(order["text"], TITLE_CHARS), "detail": "", "url": url, "feature": order_id})
        if order["state"] == "done":
            happened.append({"at": order["updated"], "kind": "live", "text": plain(order["text"], TITLE_CHARS), "detail": said(order.get("proof")), "url": url, "feature": order_id})
    for pr in prs.values():
        if pr["state"] == "merged":
            happened.append({"at": pr["updated"], "kind": "merged", "text": pr_title(pr), "detail": "", "url": pr["url"], "feature": pr["order"]})
    for job in drive["jobs"].values():
        if job["state"] == "failed":
            happened.append({"at": job["updated"], "kind": "failed", "text": job["goal"], "detail": said(job["said"]), "url": None, "feature": job["order"]})
    for event in (event for event in events if event["kind"] in HISTORY_EVENTS):
        pr = prs.get(f"{event['repo']}#{event['number']}") or {"title": "", "url": state.pr_url(f"{event['repo']}#{event['number']}"), "order": None}
        detail = event["label"] if event["kind"] == "tap" else said(event["reason"])
        happened.append({"at": event["at"], "kind": event["kind"], "text": pr_title(pr), "detail": detail, "url": pr["url"], "feature": pr["order"]})
    recent = [entry for entry in happened if state.parse_time(entry["at"]) > since]
    return sorted(recent, key=lambda entry: state.parse_time(entry["at"]), reverse=True)


def review_entry(key: str, pr: dict, drive: dict, found: dict) -> dict:
    card = card_for(found["pr_cards"], pr["url"])
    order = drive["orders"].get(pr["order"]) or {}
    group = "decided" if pr.get("tap") else "you" if waits_on_tap(pr, card) else "not ready"
    return {
        "key": key,
        "title": pr_title(pr),
        "url": pr["url"],
        "group": group,
        "feature": pr["order"],
        "feature_title": plain(order.get("text", ""), TITLE_CHARS),
        "detail": tap_detail(pr),
        "checks": pr["checks"],
        "review": pr["review"],
        "tap": tap_of(pr),
        "stuck": pr.get("stuck"),
        "since": card["asked"] if card else None,
        "updated": pr["updated"],
        "card": card["card"] if card else None,
        "answer": card["url"] if card else None,
        "thread": card_thread(card["url"]) if card else None,
    }


def review(drive: dict, found: dict) -> list[dict]:
    entries = [review_entry(key, pr, drive, found) for key, pr in drive["prs"].items() if pr["state"] == "open"]
    entries.sort(key=lambda entry: state.parse_time(entry["updated"]), reverse=True)
    entries.sort(key=lambda entry: (entry["since"] is None, entry["since"] and state.parse_time(entry["since"])))
    return entries


def ask_entry(order_id: str, order: dict, moment: datetime, url: str | None) -> dict:
    return {
        "id": order_id,
        "title": plain(order["text"], TITLE_CHARS),
        "text": order["text"],
        "state": order["state"],
        "created": order["created"],
        "updated": order["updated"],
        "due": order.get("due"),
        "late": is_late(order, moment),
        "proof": order.get("proof"),
        "thread": order.get("thread"),
        "url": url,
        "jobs": len(order["jobs"]),
    }


def asks(drive: dict, moment: datetime, team_url: str) -> list[dict]:
    entries = [ask_entry(order_id, order, moment, thread_url(drive, team_url, order.get("thread"))) for order_id, order in drive["orders"].items()]
    return sorted(entries, key=lambda entry: state.parse_time(entry["created"]), reverse=True)


def board(drive: dict, found: dict, source: str, events: Iterable[dict], moment: datetime) -> dict:
    """The dashboard's model: `build`'s, with progress, work, history and each shown feature's full order and jobs."""
    model = build(drive, found, moment) | {"cards": source}
    plan = milestones(drive, found, moment)
    shown_ids = {entry["id"] for group in model["status"] for entry in group["features"]}
    for entry in [*(entry for group in model["status"] for entry in group["features"]), *(entry for stop in plan for entry in stop["features"])]:
        order = drive["orders"][entry["id"]]
        entry |= {
            "text": order["text"],
            "note": said(order.get("note")),
            "created": order["created"],
            "jobs": [job_entry(job_id, drive["jobs"][job_id]) for job_id in order["jobs"]],
        }
    active = [job_entry(job_id, job) for job_id, job in drive["jobs"].items() if job["order"] in shown_ids and job["state"] not in state.FINISHED_JOB_STATES]
    active.sort(key=lambda entry: (entry["state"] != "running", state.parse_time(entry["updated"])))
    finished = [
        job_entry(job_id, job)
        for job_id, job in drive["jobs"].items()
        if job["state"] in state.FINISHED_JOB_STATES and moment - state.parse_time(job["updated"]) <= HISTORY_FOR
    ]
    finished.sort(key=lambda entry: state.parse_time(entry["updated"]), reverse=True)
    return model | {
        "progress": progress(drive),
        "work": active,
        "finished": finished,
        "history": history(drive, events, moment, found["team_url"]),
        "review": review(drive, found),
        "asks": asks(drive, moment, found["team_url"]),
        "incidents": incidents(drive),
        "milestones": plan,
        "channel": drive["slack"]["channel"],
    }


def incidents(drive: dict) -> list[dict]:
    """His incident orders still open, oldest first."""
    live = [(order_id, order) for order_id, order in drive["orders"].items() if order["incident"] and order["state"] not in state.CLOSED_ORDER_STATES]
    return [{"id": order_id, "title": plain(order["text"], TITLE_CHARS), "created": order["created"]} for order_id, order in sorted(live, key=lambda item: state.parse_time(item[1]["created"]))]
