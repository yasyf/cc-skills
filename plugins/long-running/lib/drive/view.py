"""One model of where a drive stands, which the dashboard and the Slack status page both draw.

`build` returns:

- `queue`: what waits on him, highest priority first: his open question cards, those holding up the most finished work
  first, then PRs ready for his tap, one item for each PR card however many PRs it carries, then promised times that
  passed. Each item has `kind` (`question`, `tap`, `late`), `title`, `detail`, `since` (when it started waiting, when
  known), `due`, `url` (where he acts), `links`, and for a question or a tap with a Slack card, `card` (the whole card
  cc-slack draws), `answer` (the card's permalink, which takes his answer from any surface) and `thread` (the Slack
  thread the card sits in, or starts). A question also has `holds`: how many green approved open PRs and built jobs
  wait on it, alone or beside other cards of his, `blocks`: the title of each feature it holds up, and `built`: each
  job built on its recommended option that waits on it, with its `job` and `branch`; those questions come first.
- `status`: the drive's features (its open orders, and orders done in the last day) in groups by state:
  `blocked`, `running`, `built`, `waiting`, `merged`, `live`, `not started`. Each feature has `title`, `state`,
  `now` (its latest job line), `due`, `late`, `proof`, `updated`, `url`, `prs`, each PR with `title`, `url`,
  `state`, `checks`, `review` and `tap`, `built`, each of its jobs built on the recommended design and
  waiting on his answers before it opens a PR, with its `job`, `branch` and `waits` (each with `title` and `url`),
  and `cards`, each open question card of his it waits on, with its `title`, `url` and `asked`.
- `counts`, by queue and state; `read` adds `cards`: `live`, or why his Slack cards could not be read.

`board` adds what only the dashboard draws: `progress` (the drive's PRs by state, red ones and running jobs),
`work` (each running or waiting job of a shown feature; each job in it and in a feature's `jobs` has `files`, the files
the drive keeps for it, of `brief`, `report`, `question`, `repro` and `transcript`), `history` (every day of the drive,
newest first, his orders each with `swears`, the swear words in his words), `review` (every open PR, in `you`,
`not ready` or `decided`, with its `head`, its Slack card and that card's `thread` when it has one), `incidents` (his
open incident orders, each with `id`, `title` and `created`) and each feature's `text`, `note`, `created` and `jobs`.
Each feature also has the `project` and `workstream` the coordinator filed its order under, each null until filed.
"""

import json
import os
import re
import subprocess
from collections.abc import Iterable
from datetime import datetime, timedelta

from drive import artifacts, github, slack, state
from drive.card import PARENTHETICAL, RECORD_ID, kind_and_title, without_records
from drive.render import cut

STATES = ("blocked", "running", "built", "waiting", "merged", "live", "not started")
LIVE_FOR = timedelta(days=1)
SLACK_LINK = re.compile(r"<(?:[^|>]*\|)?([^>]*)>")
SLACK_TS = re.compile(r"/p(\d+)")
BOARD_PICK = re.compile(r"^Owner on board \w+, ")
LEADING_LINK = re.compile(r"^https?://\S+\s+(?=\S)")
SHARED = re.compile(r"^Shared from (.+?) in #\w+:\s*")
COMMAS = re.compile(r"\s*,(?:\s*,)*")
SPACES = re.compile(r"\s+(?=[.;:!?])|(?<=\s)\s+")
TITLE_CHARS = 100
NOW_CHARS = 180
NO_CARDS = {"questions": [], "pr_cards": [], "team_url": ""}
UNREAD = "not read yet: the drive's watch reads them every minute"
SWEARS = re.compile(r"\b(?:fuck\w*|shit\w*|bullshit\w*|damn\w*|goddamn\w*|wtf|crap\w*|hell)\b", re.IGNORECASE)
HISTORY_EVENTS = ("tap", "refused", "said")
LINK_LABEL = str.maketrans({"[": "(", "]": ")", "|": "/"})


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


def card_thread(url: str) -> str | None:
    found = state.CARD_LINK.search(url)
    return found and (found[4] or f"{found[2]}.{found[3]}")


def card_open(url: str, found: dict | None) -> bool:
    """Whether his question or PR card, by its Slack link or waits id, is still open; with his cards unread, every card counts as open."""
    if found is None:
        return True
    listed = [*found["questions"], *found["pr_cards"]]
    shown = {message_key(card["url"]) for card in listed}
    waits = {card["card"].get("waits") for card in listed}
    return message_key(url) in shown or url in waits


def open_questions(job: dict, found: dict | None) -> list[dict]:
    """The questions a job asked him that his answer has not closed: each not drawn yet, and each whose card, by its link or else its waits id, is still open."""
    return [question for question in job["questions"] if not (drawn := question["card"] or question["waits"]) or card_open(drawn, found)]


def open_cards(job: dict, found: dict | None) -> list[str]:
    """The cards of his a job's questions are drawn on that he has not answered, each by its link or else its waits id."""
    return [drawn for question in open_questions(job, found) if (drawn := question["card"] or question["waits"])]


def holding(drive: dict, waits: list[str], found: dict | None) -> list[str]:
    """The waits of a job or PR that still hold: each of his cards still open, each job not finished, each PR still open, and each release until the watch sees it out."""
    return [wait for wait in waits if holds(drive, wait, found)]


def holds(drive: dict, wait: str, found: dict | None) -> bool:
    if wait in drive["jobs"]:
        return drive["jobs"][wait]["state"] not in state.FINISHED_JOB_STATES
    if wait in drive["prs"]:
        return drive["prs"][wait]["state"] == "open"
    return bool(state.RELEASE_WAIT.fullmatch(wait)) or card_open(wait, found)


def shipped_by(drive: dict, wait: str) -> str | None:
    """The release that already ships a release wait's merged PR, read from GitHub: the newest release of its service holds the PR's merge commit."""
    found = state.RELEASE_WAIT.fullmatch(wait)
    if not (found and (merged := drive["prs"][found["pr"]]["merged_sha"])):
        return None
    release = github.release_of(found["pr"], found["service"], merged)
    return f"release {release} ships {found['pr']} on {found['service']}" if release else None


def wait_words(drive: dict, wait: str, found: dict | None) -> str:
    """What a PR's wait waits for, as his card says it to him: each PR, card or build by its title, linked in the markdown his card draws as a link."""
    if wait in drive["jobs"]:
        return f"the build of {cut(drive['jobs'][wait]['goal'], TITLE_CHARS)} finishes"
    if wait in drive["prs"]:
        return f"{pr_link(drive['prs'][wait])} lands"
    if release := state.RELEASE_WAIT.fullmatch(wait):
        return f"the {release['service']} release carrying {pr_link(drive['prs'][release['pr']])} is out"
    listed = [*found["questions"], *found["pr_cards"]] if found else []
    if card := next((card for card in listed if card["card"].get("waits") == wait or message_key(card["url"]) == message_key(wait)), None):
        return f"you answer {link(card['card']['title'], card['url'])}"
    return f"you answer {link('your question card', wait)}" if wait.startswith("https://") else "you answer your question card"


def pr_link(pr: dict) -> str:
    return link(pr_title(pr), pr["url"])


def link(label: str, url: str) -> str:
    return f"[{cut(label, TITLE_CHARS).translate(LINK_LABEL)}]({url})"


def read(name: str, moment: datetime) -> dict:
    """The named drive's model from its stored state and its stored cards."""
    found, source = stored_cards(name)
    return build(state.load(name), found, moment) | {"cards": source}


def plain(text: str, limit: int) -> str:
    """The first line of his words as a title: Slack links as their labels, with no record ids."""
    first = next((line for line in text.splitlines() if line.strip()), "")
    first = BOARD_PICK.sub("", SLACK_LINK.sub(lambda link: link[1], first))
    first = SHARED.sub(r"\1: ", LEADING_LINK.sub("", first))
    first = RECORD_ID.sub("", PARENTHETICAL.sub(lambda match: "" if RECORD_ID.search(match[0]) else match[0], first))
    return cut(SPACES.sub("", COMMAS.sub(",", first)).strip(" ,"), limit)


def said(text: str | None) -> str:
    return cut(without_records(text or ""), NOW_CHARS)


def thread_url(drive: dict, team_url: str, order: dict) -> str | None:
    """Slack's link to the thread an order's lines go in, in the channel that thread is in."""
    if not (team_url and (where := state.promise_thread(drive, order))):
        return None
    return f"{team_url}archives/{where[0]}/p{where[1].replace('.', '')}"


def pr_title(pr: dict) -> str:
    return kind_and_title(pr["title"])[1] if pr["title"] else pr["url"]


def tap_of(pr: dict) -> str | None:
    if pr.get("tap"):
        return pr["tap"]["label"]
    if pr["state"] != "open":
        return None
    if pr["hold_label"]:
        return "labeled"
    return {"card": "wanted", "recheck": "not ready"}.get(state.owed_step(pr))


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
    asked = state.owed_step(pr) == "card" and not pr.get("tap") and not pr["hold_label"] and not pr["waits"] and pr["state"] == "open"
    return asked and (pr["checks"] == "pass" or card is not None)


def tap_detail(pr: dict) -> str:
    checks = {"pass": "green", "fail": "red", "pending": "CI running"}.get(pr["checks"], "no CI")
    review = {"approved": "approved", "changes": "changes requested"}.get(pr["review"], "no review yet")
    changed = f"; changed since your tap: {pr['fundamental']}" if pr.get("fundamental") else ""
    return f"{checks}, {review}{changed}"


def question_item(drive: dict, card: dict, features: list[dict], waiting: list[dict]) -> dict:
    pick = f"Recommended: {card['recommended']}" if card.get("recommended") else " / ".join(card["options"])
    return {
        "kind": "question",
        "holds": held_up(drive, card),
        "blocks": [entry["title"] for entry in features if any(held["url"] == card["url"] for held in entry["cards"])],
        "built": [{"job": entry["job"], "branch": entry["branch"]} for entry in waiting if entry["card"]["url"] == card["url"]],
        "title": card["question"],
        "detail": pick,
        "since": card["asked"],
        "due": None,
        "url": card["url"],
        "links": [],
        "card": card["card"],
        "answer": card["url"],
        "thread": card_thread(card["url"]),
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
        "thread": card_thread(card["url"]),
    }


def late_item(drive: dict, order: dict, team_url: str) -> dict:
    return {
        "kind": "late",
        "title": plain(order["text"], TITLE_CHARS),
        "detail": said(order.get("note")),
        "since": order["created"],
        "due": state.owed_at(order),
        "url": thread_url(drive, team_url, order),
        "links": [],
    }


def is_late(order: dict, moment: datetime) -> bool:
    return order["state"] not in state.CLOSED_ORDER_STATES and state.owed(order) and state.parse_time(state.owed_at(order)) <= moment


def queue(drive: dict, found: dict, moment: datetime, features: list[dict]) -> list[dict]:
    taps = [(pr, card) for pr in drive["prs"].values() if waits_on_tap(pr, card := card_for(found["pr_cards"], pr["url"]))]
    taps = list({card["url"] if card else pr["url"]: (pr, card) for pr, card in taps}.values())
    taps.sort(key=lambda entry: (entry[1] is None, entry[1] and state.parse_time(entry[1]["asked"])))
    late = sorted((order for order in drive["orders"].values() if is_late(order, moment)), key=lambda order: state.parse_time(state.owed_at(order)))
    waiting = waiting_on_him(drive, found)
    return [
        *sorted((question_item(drive, card, features, waiting) for card in found["questions"]), key=lambda item: (not item["built"], -item["holds"])),
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
    if wait in drive["prs"]:
        return {"title": f"{wait} merging: {cut(pr_title(drive['prs'][wait]), TITLE_CHARS)}", "url": state.pr_url(wait)}
    if release := state.RELEASE_WAIT.fullmatch(wait):
        return {"title": f"the release of {release['service']} carrying {release['pr']}", "url": state.pr_url(release["pr"])}
    if card := asked_card(found, wait):
        return {"title": card["question"], "url": card["url"]}
    return {"title": "his question card", "url": wait if wait.startswith("https://") else None}


def waiting_on_him(drive: dict, found: dict) -> list[dict]:
    """Each open card of his that holds a job built on its recommended option, oldest card first, with the wait that names it, the job, its order and its branch."""
    held = [
        {"card": card, "wait": wait, "job": job_id, "order": job["order"], "branch": job["branch"]}
        for job_id, job in drive["jobs"].items()
        if job["state"] == "built"
        for wait in job["waits"]
        if wait not in drive["jobs"] and (card := asked_card(found, wait))
    ]
    return sorted(held, key=lambda entry: state.parse_time(entry["card"]["asked"]))


def built_entry(drive: dict, job_id: str, found: dict) -> dict:
    job = drive["jobs"][job_id]
    return {"job": job_id, "branch": job["branch"], "waits": [wait_entry(drive, wait, found) for wait in job["waits"]]}


def blocking(drive: dict, jobs: list[dict], prs: list[dict], found: dict) -> list[dict]:
    """His open question cards a feature waits on: each its jobs and open PRs wait on, and each a job of it asked."""
    owners = [*jobs, *(pr for pr in prs if pr["state"] == "open")]
    named = {message_key(wait) for owner in owners for wait in owner["waits"] if wait not in drive["jobs"]}
    named |= {message_key(question["card"]) for job in jobs for question in job["questions"] if question["card"]}
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
        "project": order["project"],
        "workstream": order["workstream"],
        "due": order.get("due"),
        "late": is_late(order, moment),
        "proof": order.get("proof"),
        "updated": order["updated"],
        "url": thread_url(drive, found["team_url"], order),
        "prs": [pr_entry(pr) for pr in prs],
        "built": [built_entry(drive, job_id, found) for job_id in order["jobs"] if drive["jobs"][job_id]["state"] == "built"],
        "cards": blocking(drive, jobs, prs, found),
    }


def status(drive: dict, moment: datetime, found: dict) -> list[dict]:
    features = [feature(drive, order_id, order, moment, found) for order_id, order in drive["orders"].items() if shown(order, moment)]
    features.sort(key=lambda entry: state.parse_time(entry["updated"]), reverse=True)
    features.sort(key=lambda entry: (entry["due"] is None, entry["due"] and state.parse_time(entry["due"])))
    return [{"state": name, "features": [entry for entry in features if entry["state"] == name]} for name in STATES]


def build(drive: dict, found: dict, moment: datetime) -> dict:
    """The drive's model from its stored state, his open cards as cc-slack lists them, and the time it is drawn for."""
    groups = status(drive, moment, found)
    waiting = queue(drive, found, moment, [entry for group in groups for entry in group["features"]])
    counts = {"queue": len(waiting), **{group["state"]: len(group["features"]) for group in groups}}
    return {"drive": drive["name"], "at": state.stamp(moment), "counts": counts, "queue": waiting, "status": groups}


def job_entry(job_id: str, job: dict, reports: set[str]) -> dict:
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
        "files": artifacts.kept(job_id, job, reports),
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


def history(drive: dict, events: Iterable[dict], team_url: str) -> list[dict]:
    """What happened on every day of the drive, newest first: his orders, features gone live, merges, failed jobs, his taps, refused cards and every message he was sent, with its visuals."""
    prs = drive["prs"]
    happened = []
    for order_id, order in drive["orders"].items():
        url = thread_url(drive, team_url, order)
        swears = len(SWEARS.findall(order["text"]))
        happened.append({"at": order["created"], "kind": "order", "text": plain(order["text"], TITLE_CHARS), "detail": "", "url": url, "feature": order_id, "swears": swears})
        if order["state"] == "done":
            happened.append({"at": order["updated"], "kind": "live", "text": plain(order["text"], TITLE_CHARS), "detail": said(order.get("proof")), "url": url, "feature": order_id})
    for pr in prs.values():
        if pr["state"] == "merged":
            happened.append({"at": pr["updated"], "kind": "merged", "text": pr_title(pr), "detail": "", "url": pr["url"], "feature": pr["order"]})
    for job in drive["jobs"].values():
        if job["state"] == "failed":
            happened.append({"at": job["updated"], "kind": "failed", "text": job["goal"], "detail": said(job["said"]), "url": None, "feature": job["order"]})
    for event in (event for event in events if event["kind"] in HISTORY_EVENTS):
        if event["kind"] == "said":
            stack = [pr_entry(prs[key]) for key in event.get("prs", [])]
            happened.append(
                {"at": event["at"], "kind": "said", "text": event["text"], "detail": "", "url": event["url"], "feature": event["order"], "visuals": event["visuals"], "prs": stack}
            )
            continue
        pr = prs.get(f"{event['repo']}#{event['number']}") or {"title": "", "url": state.pr_url(f"{event['repo']}#{event['number']}"), "order": None}
        detail = event["label"] if event["kind"] == "tap" else said(event["reason"])
        happened.append({"at": event["at"], "kind": event["kind"], "text": pr_title(pr), "detail": detail, "url": pr["url"], "feature": pr["order"]})
    return sorted(happened, key=lambda entry: state.parse_time(entry["at"]), reverse=True)


def review_entry(key: str, pr: dict, drive: dict, found: dict) -> dict:
    card = card_for(found["pr_cards"], pr["url"])
    order = drive["orders"].get(pr["order"]) or {}
    group = "decided" if pr.get("tap") else "you" if waits_on_tap(pr, card) else "not ready"
    return {
        "key": key,
        "title": pr_title(pr),
        "url": pr["url"],
        "head": pr["head"],
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
        "thread": card and card_thread(card["url"]),
    }


def review(drive: dict, found: dict) -> list[dict]:
    entries = [review_entry(key, pr, drive, found) for key, pr in drive["prs"].items() if pr["state"] == "open"]
    entries.sort(key=lambda entry: state.parse_time(entry["updated"]), reverse=True)
    entries.sort(key=lambda entry: (entry["since"] is None, entry["since"] and state.parse_time(entry["since"])))
    return entries


def board(drive: dict, found: dict, source: str, events: Iterable[dict], moment: datetime, reports: set[str]) -> dict:
    """The dashboard's model: `build`'s, with progress, work, history and each shown feature's full order and jobs; reports names the jobs whose whole return the drive keeps."""
    model = build(drive, found, moment) | {"cards": source}
    shown_ids = {entry["id"] for group in model["status"] for entry in group["features"]}
    for entry in (entry for group in model["status"] for entry in group["features"]):
        order = drive["orders"][entry["id"]]
        entry |= {
            "text": order["text"],
            "note": said(order.get("note")),
            "created": order["created"],
            "jobs": [job_entry(job_id, drive["jobs"][job_id], reports) for job_id in order["jobs"]],
        }
    active = [job_entry(job_id, job, reports) for job_id, job in drive["jobs"].items() if job["order"] in shown_ids and job["state"] not in state.FINISHED_JOB_STATES]
    active.sort(key=lambda entry: (entry["state"] != "running", state.parse_time(entry["updated"])))
    return model | {
        "progress": progress(drive),
        "work": active,
        "history": history(drive, events, found["team_url"]),
        "review": review(drive, found),
        "incidents": incidents(drive),
        "channel": drive["slack"]["channel"],
    }


def incidents(drive: dict) -> list[dict]:
    """His incident orders still open, oldest first."""
    live = [(order_id, order) for order_id, order in drive["orders"].items() if order["incident"] and order["state"] not in state.CLOSED_ORDER_STATES]
    return [{"id": order_id, "title": plain(order["text"], TITLE_CHARS), "created": order["created"]} for order_id, order in sorted(live, key=lambda item: state.parse_time(item[1]["created"]))]
