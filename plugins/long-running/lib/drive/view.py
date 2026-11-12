"""One model of where a drive stands, which the dashboard and the Slack status page both draw.

`build` returns:

- `queue`: what waits on him, highest priority first: his open question cards, those holding up the most finished work
  first, then PRs ready for his tap, one item for each PR card however many PRs it carries, then promised times that
  passed. Each item has `kind` (`question`, `tap`, `late`), `title`, `detail`, `since` (when it started waiting, when
  known), `due`, `url` (where he acts), `links`, and for a question or a tap with a Slack card, `card` (the whole card
  cc-slack draws), `answer` (the card's permalink, which takes his answer from any surface) and `thread` (the Slack
  thread the card sits in, or starts). A question also has `holds`: how many green approved open PRs and built jobs
  wait on it, alone or beside other cards of his, `blocks`: each feature it holds up, with its `id` and `title`,
  `recommended`: the option its card recommends, or null, and `built`: each job built on its recommended option that
  waits on it, with its `job` and `branch`; those questions come first. A tap also has `pr`, its PR's link, and that
  PR's `checks`.
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

`summary` is the board each open page is sent on every change, sized by what is open now and never by the drive's age;
each screen reads the rest of what it draws on its own: `history_page` for History, `story` for Now's live story.
"""

import itertools
import json
import math
import os
import re
import subprocess
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from drive import artifacts, github, limits, slack, state
from drive.card import PARENTHETICAL, RECORD_ID, kind_and_title, without_records
from drive.render import cut, when

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
NO_CARDS = {"questions": [], "pr_cards": [], "closed": [], "team_url": ""}
UNREAD = "not read yet: the drive's watch reads them every minute"
SWEARS = re.compile(r"\b(?:fuck\w*|shit\w*|bullshit\w*|damn\w*|goddamn\w*|wtf|crap\w*|hell)\b", re.IGNORECASE)
HISTORY_EVENTS = ("tap", "refused", "said")
ZONE = ZoneInfo("America/Los_Angeles")
HISTORY_PAGE = 80
HISTORY_SHOWN = {"all": None, "prs": {"merged"}, "you": {"order", "tap"}, "said": {"said"}}
STORY_KINDS = {"order", "live", "merged", "tap"}
STORY_LINES = 6
LINK_LABEL = str.maketrans({"[": "(", "]": ")", "|": "/"})
TAP_CLAIM = re.compile(
    r"(?<!not )(?<!n't )(?<!never )\b(?:wait\w*|await\w*|needs?|owed?|pending|blocked)\b(?:(?!\b(?:no|not|never|without)\b)[^.!?\n]){0,40}?\b(?:tap|approv(?:e|al))\b",
    re.IGNORECASE,
)
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n")
TAP_CLAIM_RULING = 'his Nov 11 1:51 PM words "Again you fucking told me something waits for my tap and didn\'t give me something to tap"'


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
    """His cards as the drive last stored them, each list the installed cc-slack does not return read as empty, and `live` or why they are missing."""
    path = state.cards_path(name)
    if not path.exists():
        return NO_CARDS, UNREAD
    # TODO: read the stored file as is once long-running requires the cc-slack release that returns the closed list (cc-slack#348).
    found = {**NO_CARDS, **json.loads(path.read_text())}
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


def closed_card(found: dict, card: str) -> dict | None:
    """The newest card cc-slack keeps closed under this waits id or Slack link."""
    return next((closed for closed in reversed(found["closed"]) if closed["waits"] == card or message_key(closed["url"]) == message_key(card)), None)


def withdrawn(found: dict | None, card: str | None) -> bool:
    """Whether his card under this waits id or link was withdrawn with nothing drawn in its place, so its question asked again redraws it under the same waits id."""
    if not (found and card) or card_open(card, found):
        return False
    closed = closed_card(found, card)
    return bool(closed and closed["answer"] and closed["answer"].get("withdrawn") and not closed["replaced_by"])


def card_words(found: dict | None, source: str, card: str | None) -> str:
    """His card under this waits id or link as cc-slack's store holds it now: open since when, answered, withdrawn or redrawn, each by its link, or never drawn."""
    if found is None:
        return f"his cards could not be read ({source})"
    if not card:
        return "never drawn"
    if shown := next((shown for shown in [*found["questions"], *found["pr_cards"]] if shown["card"].get("waits") == card or message_key(shown["url"]) == message_key(card)), None):
        return f"card {shown['url']} open since {when(shown['asked'])}"
    if (closed := closed_card(found, card)) is None:
        return f"card {card}, which cc-slack no longer keeps" if card.startswith("https://") else f"card {card} never drawn"
    answer = closed["answer"] or {}
    gone = f"withdrawn at {when(answer['at'])}: {cut(answer['label'], 80)}" if answer.get("withdrawn") else ""
    if closed["replaced_by"]:
        return f"card {closed['url']} {gone + ', ' if gone else ''}redrawn as {card_words(found, source, closed['replaced_by'])}"
    if gone:
        return f"card {closed['url']} {gone}"
    if answer:
        return f'card {closed["url"]} answered "{cut(answer["label"], 80)}" at {when(answer["at"])}'
    return f"card {closed['url']} closed: {cut(closed['tapped'], 80)}"


def pr_card_words(found: dict | None, source: str, url: str) -> str | None:
    """His newest card carrying the PR at url as cc-slack's store holds it now, open ones first; None when he never got one."""
    if found is None:
        return f"his cards could not be read ({source})"
    shown = card_for(found["pr_cards"], url) or card_for(found["closed"], url)
    return shown and card_words(found, source, shown["url"])


def open_questions(job: dict, found: dict | None) -> list[dict]:
    """The questions a job asked him that neither his answer nor his recorded words have closed: each not drawn yet that his recorded words did not settle, a refused one staying until it is asked again or dropped, and each whose card, by its link or else its waits id, is still open."""
    return [question for question in job["questions"] if (question["refused"] or not question["settled"]) and (not (drawn := question["card"] or question["waits"]) or card_open(drawn, found))]


def open_cards(job: dict, found: dict | None) -> list[str]:
    """The cards of his a job's questions are drawn on that he has not answered, each by its link or else its waits id."""
    return [drawn for question in open_questions(job, found) if (drawn := question["card"] or question["waits"])]


def owned(job: dict, cards: dict | None) -> bool:
    """Whether a PR's job holds it: a running builder owns its pushes and CI, and a job asking him holds its PR."""
    return job["state"] == "running" or bool(open_cards(job, cards))


def unblocked(pr: dict, data: dict, cards: dict | None, reviews: tuple[str, ...] = ("approved",)) -> bool:
    """Whether a PR is open, green and approved, or in another of reviews, not held, with nothing it waits on holding it."""
    green = pr["state"] == "open" and pr["checks"] == "pass" and pr["review"] in reviews and not held(pr)
    return green and not holding(data, pr["waits"], cards)


def next_step(pr: dict, data: dict, cards: dict | None) -> bool:
    """Whether a PR waits on its next step alone: unblocked, and its job not holding it."""
    return unblocked(pr, data, cards) and not owned(data["jobs"][pr["job"]], cards)


def held(pr: dict) -> bool:
    return (pr.get("tap") or {}).get("label") == "Hold" or pr["hold_label"]


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
    return {"card": "wanted", "reworking": "reworking"}.get(state.owed_step(pr))


def pr_entry(pr: dict) -> dict:
    return {
        "title": pr_title(pr),
        "url": pr["url"],
        "state": pr["state"],
        "checks": pr["checks"],
        "review": pr["review"],
        "tap": tap_of(pr),
    }


def named_pr(prs: dict, key: str) -> dict:
    """The PR a kept event names, as the drive holds it now, or by its link alone once drop_pr stopped tracking it, since events.jsonl keeps every key it ever named."""
    return prs.get(key) or {"title": "", "url": state.pr_url(key), "order": None, "state": "dropped", "checks": None, "review": "none"}


def card_for(pr_cards: list[dict], url: str) -> dict | None:
    return next((card for card in reversed(pr_cards) if url in card["prs"]), None)


def waits_on_tap(pr: dict, card: dict | None) -> bool:
    asked = state.owed_step(pr) == "card" and not pr.get("tap") and not pr["hold_label"] and not pr["waits"] and pr["state"] == "open"
    return asked and (pr["checks"] == "pass" or card is not None)


def tap_claim_refusal(data: dict, order_id: str, text: str, pr_cards: list[dict]) -> str | None:
    """Why words say a PR waits on his tap or Approve when he has nothing to tap: each PR such a sentence names passes only while its open tap card stands at its head now and the words link that card."""
    linked = set(SLACK_TS.findall(text))
    found = []
    for sentence in SENTENCE_BREAK.split(text):
        if not TAP_CLAIM.search(SLACK_LINK.sub(r"\1", sentence)):
            continue
        for ref in dict.fromkeys(match[0] for match in state.PR_REF.finditer(sentence)):
            if not (keys := state.named_prs(data, order_id, ref)):
                found.append(f"{ref}: the drive holds no such PR, so no tap card of his is known for it")
                continue
            pr = data["prs"][keys[0]]
            card = card_for(pr_cards, pr["url"])
            if card is None or pr["head"] not in card_heads(card, pr["url"]):
                found.append(f"{keys[0]}: {limits.tap_owed_not(pr) or 'no open tap card of his stands at its head'}")
            elif message_key(card["url"]) not in linked:
                found.append(f"{keys[0]}: the words do not link its open tap card {card['url']}")
    if not found:
        return None
    return (
        f"refused: these words say a PR waits on his tap or Approve with nothing for him to tap: {'; '.join(dict.fromkeys(found))}."
        " Say a PR waits on his tap only while its open tap card stands at its head, and link that card where you say it, as <permalink|its tap card>; else say what happened to the PR"
        f" ({TAP_CLAIM_RULING})"
    )


def card_heads(card: dict, url: str) -> list[str]:
    """The head each row of a PR card shows for the PR at url."""
    listed = [*(card["card"].get("prs") or []), *(row["pr"] for row in card["card"].get("rows") or [] if row.get("pr"))]
    return [pr.get("head") or "" for pr in listed if pr["url"] == url]


def tap_detail(pr: dict) -> str:
    checks = {"pass": "green", "fail": "red", "pending": "CI running"}.get(pr["checks"], "no CI")
    review = {"approved": "approved", "stale": "approved on an older head", "changes": "changes requested"}.get(pr["review"], "no review yet")
    changed = f"; changed since your tap: {pr['fundamental']}" if pr.get("fundamental") else ""
    reworking = "; being reworked to his words, card down until its builder's READY" if state.owed_step(pr) == "reworking" else ""
    return f"{checks}, {review}{changed}{reworking}"


def question_item(drive: dict, card: dict, features: list[dict], waiting: list[dict], asker: str | None) -> dict:
    pick = f"Recommended: {card['recommended']}" if card.get("recommended") else " / ".join(card["options"])
    finished = held_up(drive, card)
    return {
        "kind": "question",
        "job": asker,
        "holds": len(finished),
        "rail": {"held": finished},
        "blocks": [{"id": entry["id"], "title": entry["title"]} for entry in features if any(held["url"] == card["url"] for held in entry["cards"])],
        "built": [{"job": entry["job"], "branch": entry["branch"]} for entry in waiting if entry["card"]["url"] == card["url"]],
        "title": card["question"],
        "detail": pick,
        "recommended": card.get("recommended") or None,
        "since": card["asked"],
        "due": None,
        "url": card["url"],
        "links": [],
        "card": card["card"],
        "answer": card["url"],
        "thread": card_thread(card["url"]),
    }


def held_up(drive: dict, card: dict) -> list[dict]:
    """The green approved open PRs and built jobs that wait on his question card, each PR by its title and link and each job by its goal and branch."""
    names = {card["card"].get("waits"), message_key(card["url"])}
    finished = [pr for pr in drive["prs"].values() if pr["state"] == "open" and pr["checks"] == "pass" and pr["review"] == "approved" and any(message_key(wait) in names for wait in pr["waits"])]
    built = [job for job in drive["jobs"].values() if job["state"] == "built" and any(message_key(wait) in names for wait in job["waits"])]
    return [
        *({"kind": "pr", "feature": pr["order"], "title": pr_title(pr), "url": pr["url"]} for pr in finished),
        *({"kind": "job", "feature": job["order"], "title": cut(job["goal"], TITLE_CHARS), "branch": job["branch"]} for job in built),
    ]


def asked_by(drive: dict, found: dict) -> dict[str, str | None]:
    """The job that asked each of his open question cards, by the card's link: the job whose question drew the card, else the job its waits id names."""
    drawn = {message_key(question["card"]): job_id for job_id, job in drive["jobs"].items() for question in job["questions"] if question["card"]}
    asker = {}
    for card in found["questions"]:
        named = (card["card"].get("waits") or "").partition(":")[2].partition("-")[0]
        asker[card["url"]] = drawn.get(message_key(card["url"])) or (named if named in drive["jobs"] else None)
    return asker


def rail(drive: dict, item: dict, reports: set[str]) -> dict:
    """What the dashboard draws beside a question card beyond the card itself: what it holds up, the job that asked it with the files the drive keeps for it, and his recorded answers the drive matched to the card, newest first."""
    key = item["card"].get("waits") or message_key(item["answer"])
    answers = sorted((answer for answer in drive["answers"].values() if key in answer["cards"]), key=lambda answer: state.parse_time(answer["at"]), reverse=True)
    return item["rail"] | {
        "asker": item["job"] and job_entry(item["job"], drive["jobs"][item["job"]], reports),
        "answers": [{"words": answer["words"], "at": answer["at"], "mark": (answer["cards"][key] or {}).get("mark")} for answer in answers],
    }


def tap_item(pr: dict, card: dict | None) -> dict:
    if not card:
        return {"kind": "tap", "title": pr_title(pr), "detail": f"{tap_detail(pr)}; no Slack card yet", "since": None, "due": None, "url": pr["url"], "links": [], "pr": pr["url"], "checks": pr["checks"]}
    return {
        "kind": "tap",
        "title": pr_title(pr),
        "detail": tap_detail(pr),
        "pr": pr["url"],
        "checks": pr["checks"],
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
    asker = asked_by(drive, found)
    return [
        *sorted((question_item(drive, card, features, waiting, asker[card["url"]]) for card in found["questions"]), key=lambda item: (not item["built"], -item["holds"])),
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
    """What happened on every day of the drive, newest first: his orders, features gone live, merges, failed jobs, his taps, refused cards and every message he was sent, with its visuals, a message reworded in place showing its newest words at the time it was sent."""
    prs = drive["prs"]
    happened = []
    sent: dict[str, dict] = {}
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
        if event["kind"] == "said" and event.get("edited") and (post := sent.get(message_key(event["url"]))):
            post["text"] = event["text"]
            continue
        if event["kind"] == "said":
            stack = [pr_entry(named_pr(prs, key)) for key in event.get("prs", [])]
            post = {"at": event["at"], "kind": "said", "text": event["text"], "detail": "", "url": event["url"], "feature": event["order"], "visuals": event["visuals"], "prs": stack}
            sent[message_key(event["url"])] = post
            happened.append(post)
            continue
        pr = named_pr(prs, f"{event['repo']}#{event['number']}")
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
    for item in (item for item in model["queue"] if item["kind"] == "question"):
        item["rail"] = rail(drive, item, reports)
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
    """His incident orders still open, oldest first, but one the watch found over, its monitors OK and its fix live, while its close is posted."""
    live = [(order_id, order) for order_id, order in drive["orders"].items() if order["incident"] and order["state"] not in state.CLOSED_ORDER_STATES and not order["recovered"]]
    return [{"id": order_id, "title": plain(order["text"], TITLE_CHARS), "created": order["created"]} for order_id, order in sorted(live, key=lambda item: state.parse_time(item[1]["created"]))]


def glance(card: dict) -> dict:
    """A card as a page lists it before he opens it: its question, its options and the PRs it carries."""
    return {"title": card["title"], "options": card["options"], "recommended": card.get("recommended"), "prs": [{"url": row["url"], "title": row["title"]} for row in card.get("prs") or []]}


def outline(entry: dict) -> dict:
    return {key: value for key, value in entry.items() if key not in ("text", "jobs")} | {"running": sum(job["state"] == "running" for job in entry["jobs"])}


def summary(model: dict) -> dict:
    """The board without its history and lanes, each card at a glance without its rail and each feature without its order's words and jobs."""
    rest = {key: value for key, value in model.items() if key not in ("history", "work")}
    return rest | {
        "queue": [{key: value for key, value in item.items() if key != "rail"} | {"card": glance(item["card"])} if item.get("card") else item for item in model["queue"]],
        "review": [entry | {"card": entry["card"] and glance(entry["card"])} for entry in model["review"]],
        "status": [group | {"features": [outline(entry) for entry in group["features"]]} for group in model["status"]],
    }


def day_of(at: str) -> date:
    return state.parse_time(at).astimezone(ZONE).date()


def day_counts(day: date, moments: list[dict]) -> dict:
    asked = [entry for entry in moments if entry["kind"] == "order"]
    return {"day": day.isoformat(), "merged": sum(entry["kind"] == "merged" for entry in moments), "swears": sum(entry["swears"] for entry in asked), "asked": len(asked), "moments": len(moments)}


def history_page(model: dict, day: str | None, show: str, page: int) -> dict:
    """One page of HISTORY_PAGE moments, newest first, of the Pacific day asked or else of every day, kept to the kinds show names, with each day's counts from the drive's first day to today."""
    by_day: dict[date, list[dict]] = {}
    for entry in model["history"]:
        by_day.setdefault(day_of(entry["at"]), []).append(entry)
    days = []
    if model["history"]:
        first, last = day_of(model["history"][-1]["at"]), day_of(model["at"])
        days = [day_counts(each, by_day.get(each, [])) for each in (first + timedelta(days=n) for n in range((last - first).days + 1))]
    zoomed = day if any(each["day"] == day for each in days) else None
    kinds = HISTORY_SHOWN[show]
    scope = by_day.get(date.fromisoformat(zoomed), []) if zoomed else model["history"]
    kept = [entry for entry in scope if kinds is None or entry["kind"] in kinds]
    listed = kept[(page - 1) * HISTORY_PAGE : page * HISTORY_PAGE]
    return {"days": days, "day": zoomed, "moments": listed, "page": page, "pages": max(1, math.ceil(len(kept) / HISTORY_PAGE))}


def story(model: dict, ids: set[str] | None) -> list[dict]:
    """The newest STORY_LINES orders, launches, merges and taps of the features named, or of the whole drive."""
    told = (entry for entry in model["history"] if entry["kind"] in STORY_KINDS and (ids is None or entry["feature"] in ids))
    return list(itertools.islice(told, STORY_LINES))
