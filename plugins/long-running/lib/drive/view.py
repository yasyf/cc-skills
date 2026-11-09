"""One model of where a drive stands, which the dashboard and the Slack status page both draw.

`build` returns:

- `queue`: what waits on him, highest priority first: his open question cards, then PRs ready for his tap,
  then promised times that passed. Each item has `kind` (`question`, `tap`, `late`), `title`, `detail`,
  `since` (when it started waiting, when known), `due`, `url` (where he acts) and `links`.
- `status`: the drive's features (its open orders, and orders done in the last day) in groups by state:
  `blocked`, `running`, `waiting`, `merged`, `live`, `not started`. Each feature has `title`, `state`, `now`
  (its latest job line), `due`, `late`, `proof`, `updated`, `url` and `prs`, each PR with `title`, `url`,
  `state`, `checks`, `review` and `tap`.
- `counts`, by queue and state; `read` adds `cards`: `live`, or why his Slack cards could not be read.
"""

import json
import re
import subprocess
from datetime import datetime, timedelta

from drive import state
from drive.card import PARENTHETICAL, RECORD_ID, kind_and_title, without_records
from drive.render import cut

STATES = ("blocked", "running", "waiting", "merged", "live", "not started")
LIVE_FOR = timedelta(days=1)
SLACK_LINK = re.compile(r"<(?:[^|>]*\|)?([^>]*)>")
BOARD_PICK = re.compile(r"^Owner on board \w+, ")
COMMAS = re.compile(r"\s*,(?:\s*,)*")
SPACES = re.compile(r"\s+(?=[.;:!?])|(?<=\s)\s+")
TITLE_CHARS = 100
NOW_CHARS = 180
NO_CARDS = {"questions": [], "pr_cards": [], "team_url": ""}


class CardsError(Exception):
    pass


def cards(name: str) -> dict:
    """His open question cards for the drive and the PR cards waiting on his tap, from cc-slack's stored state."""
    try:
        result = subprocess.run(
            ["cc-slack", "drive-cards", "--drive", name], check=False, capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CardsError(str(error)) from None
    if result.returncode:
        raise CardsError(" ".join(result.stderr.split()) or f"cc-slack exited {result.returncode}")
    return json.loads(result.stdout)


def read(name: str, moment: datetime) -> dict:
    """The named drive's model as stored now, with his cards when cc-slack answers."""
    try:
        found, source = cards(name), "live"
    except CardsError as error:
        found, source = NO_CARDS, f"unavailable: {error}"
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
    asked = pr["wants_tap"] and not pr.get("tap") and not pr.get("exempt") and pr["state"] == "open"
    return asked and (pr["checks"] == "pass" or card is not None)


def tap_detail(pr: dict) -> str:
    checks = {"pass": "green", "fail": "red", "pending": "CI running"}.get(pr["checks"], "no CI")
    review = {"approved": "approved", "changes": "changes requested"}.get(pr["review"], "no review yet")
    changed = f"; changed since your tap: {pr['fundamental']}" if pr.get("fundamental") else ""
    return f"{checks}, {review}{changed}"


def question_item(card: dict) -> dict:
    pick = f"Recommended: {card['recommended']}" if card.get("recommended") else " / ".join(card["options"])
    return {"kind": "question", "title": card["question"], "detail": pick, "since": card["asked"], "due": None, "url": card["url"], "links": []}


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
    }


def late_item(drive: dict, order: dict, team_url: str) -> dict:
    return {
        "kind": "late",
        "title": plain(order["text"], TITLE_CHARS),
        "detail": said(order.get("note")),
        "since": order["created"],
        "due": order["due"],
        "url": thread_url(drive, team_url, order.get("thread")),
        "links": [],
    }


def is_late(order: dict, moment: datetime) -> bool:
    due = order.get("due")
    return bool(due) and order["state"] not in state.CLOSED_ORDER_STATES and state.parse_time(due) <= moment


def queue(drive: dict, found: dict, moment: datetime) -> list[dict]:
    taps = [(pr, card) for pr in drive["prs"].values() if waits_on_tap(pr, card := card_for(found["pr_cards"], pr["url"]))]
    taps.sort(key=lambda entry: (entry[1] is None, entry[1] and state.parse_time(entry[1]["asked"])))
    late = sorted((order for order in drive["orders"].values() if is_late(order, moment)), key=lambda order: state.parse_time(order["due"]))
    return [
        *map(question_item, found["questions"]),
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


def feature(drive: dict, order_id: str, order: dict, moment: datetime, team_url: str) -> dict:
    jobs = [drive["jobs"][job_id] for job_id in order["jobs"]]
    prs = [pr for pr in drive["prs"].values() if pr["order"] == order_id]
    return {
        "title": plain(order["text"], TITLE_CHARS),
        "state": feature_state(order, jobs, prs),
        "now": latest_line(order, jobs),
        "due": order.get("due"),
        "late": is_late(order, moment),
        "proof": order.get("proof"),
        "updated": order["updated"],
        "url": thread_url(drive, team_url, order.get("thread")),
        "prs": [pr_entry(pr) for pr in prs],
    }


def status(drive: dict, moment: datetime, team_url: str) -> list[dict]:
    features = [feature(drive, order_id, order, moment, team_url) for order_id, order in drive["orders"].items() if shown(order, moment)]
    features.sort(key=lambda entry: state.parse_time(entry["updated"]), reverse=True)
    features.sort(key=lambda entry: (entry["due"] is None, entry["due"] and state.parse_time(entry["due"])))
    return [{"state": name, "features": [entry for entry in features if entry["state"] == name]} for name in STATES]


def build(drive: dict, found: dict, moment: datetime) -> dict:
    """The drive's model from its stored state, his open cards as cc-slack lists them, and the time it is drawn for."""
    waiting = queue(drive, found, moment)
    groups = status(drive, moment, found["team_url"])
    counts = {"queue": len(waiting), **{group["state"]: len(group["features"]) for group in groups}}
    return {"drive": drive["name"], "at": state.stamp(moment), "counts": counts, "queue": waiting, "status": groups}
