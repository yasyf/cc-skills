"""One model of where a drive stands, which the dashboard and the Slack status page both draw.

`build` returns:

- `queue`: what waits on him, in one rank whatever its kind: what an open incident waits on first, then what is past
  due, then what holds the most (a built job first, then the finished PRs, built jobs and tasks it holds, a tap counting
  the PRs it lands and those stacked above them), then what has waited longest. Its items are his open question cards,
  as `with_questions` reads them from the drive's open questions, PRs ready for his tap, one item for each PR card however many PRs it carries, and promised times that passed. Each
  item has `kind` (`question`, `tap`, `late`), `title`, `detail`, `since` (when it started waiting, when known), `due`,
  `url` (where he acts), `links`, `tasks` (the ids of the tasks it serves), `project` and `workstream` (where its first task is filed, null until filed), `rank_why`
  (why it ranks where it does, in his words' order of the rank), and for a question or a tap with a Slack card, `card` (the whole card
  cc-slack draws, or the one `forgotten` draws once cc-slack's store forgot it), `answer` (the card's permalink, which takes his answer from any surface) and `thread` (the Slack
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
open incident orders, each with `id`, `title` and `created`), `bug_reports` (each shown order fixing a bug someone
reported, each with `id`, `title`, `created` and `stage`, one of BUG_STAGES) and each feature's `text`, `note`,
`created` and `jobs`.
Each feature also has the `project` and `workstream` the coordinator filed its order under, each null until filed.

`summary` is the board each open page is sent on every change, sized by what is open now and never by the drive's age;
each screen reads the rest of what it draws on its own: `history_page` for History, `story` for Now's live story.
"""

import itertools
import json
import math
import os
import re
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from functools import cache, lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from drive import artifacts, limits, slack, state
from drive.card import PARENTHETICAL, RECORD_ID, kind_and_title, without_records
from drive.render import cut, when

STATES = ("blocked", "running", "built", "waiting", "merged", "live", "not started")
BUG_STAGES = ("reported", "fixing", "merged", "live", "told")
LIVE_FOR = timedelta(days=1)
SLACK_LINK = re.compile(r"<(?:[^|>]*\|)?([^>]*)>")
SLACK_TS = re.compile(r"/p(\d+)")
BOARD_PICK = re.compile(r"^Owner on board \w+, ")
LEADING_LINK = re.compile(r"^https?://\S+\s+(?=\S)")
SHARED = re.compile(r"^Shared from (.+?) in #\w+:\s*")
COMMAS = re.compile(r"\s*,(?:\s*,)*")
SPACES = re.compile(r"\s+(?=[.;:!?])|(?<=\s)\s+")
RECORD_TOKEN = re.compile(rf"[^\s()]*(?:{RECORD_ID.pattern})[^\s()]*")
TITLE_CHARS = 100
NOW_CHARS = 180
NO_CARDS = {"questions": [], "pr_cards": [], "closed": [], "team_url": ""}
UNREAD = "not read yet: the drive's watch reads them every minute"
SWEARS = re.compile(r"\b(?:fuck\w*|shit\w*|bullshit\w*|damn\w*|goddamn\w*|wtf|crap\w*|hell)\b", re.IGNORECASE)
HISTORY_EVENTS = ("tap", "refused", "said", "answered", "undone")
BOARD_EVENTS = (*HISTORY_EVENTS, "asked")
HISTORY_ONLY = {"said", "answered", "undone"}
ZONE = ZoneInfo("America/Los_Angeles")
HISTORY_PAGE = 80
HISTORY_SHOWN = {"all": None, "prs": {"merged"}, "you": {"order", "answer", "tap", "answered", "undone"}, "said": {"said"}}
STORY_KINDS = {"order", "live", "merged", "tap"}
STORY_LINES = 6
HEARD_LINES = 8
NAMED = re.compile(r"\b[oj]\d+\b")
LINK_LABEL = str.maketrans({"[": "(", "]": ")", "|": "/"})
TAP_CLAIM = re.compile(
    r"(?<!not )(?<!n't )(?<!never )\b(?:wait\w*|await\w*|needs?|owed?|pending|blocked)\b(?:(?!\b(?:no|not|never|without)\b)[^.!?\n]){0,40}?\b(?:tap|approv(?:e|al))\b",
    re.IGNORECASE,
)
SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|\n")
TAP_CLAIM_RULING = 'his Nov 11 1:51 PM words "Again you fucking told me something waits for my tap and didn\'t give me something to tap"'


def cards(name: str) -> dict:
    """His open question cards for the drive and the PR cards waiting on his tap, from cc-slack's stored state."""
    return slack.cc_slack("drive-cards", "--drive", name, "--json", timeout=30)


def refresh_cards(name: str) -> None:
    """Store his cards as cc-slack lists them now, rewriting the drive's cards file only when they changed."""
    try:
        found = {"source": "live", **cards(name)}
    except slack.SlackError as error:
        found = {"source": f"unavailable: {error}", **NO_CARDS}
    path = state.cards_path(name)
    text = json.dumps(found, sort_keys=True, separators=(",", ":")) + "\n"
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


@cache
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


def closed_in_place(found: dict, card: str) -> bool:
    """Whether cc-slack keeps his card under this link closed with no answer, neither withdrawn nor replaced: a redraw took Approve and Hold off it once no PR on it asked his tap, and only a redraw in place opens it again."""
    closed = None if card_open(card, found) else closed_card(found, card)
    return closed is not None and not closed["answer"] and not closed["replaced_by"]


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
        return f'card {closed["url"]} answered "{" ".join(answer["label"].split())}" at {when(answer["at"])}'
    return f"card {closed['url']} closed: {cut(closed['tapped'], 80)}"


def question_card_words(found: dict | None, source: str, question: dict) -> str:
    """His card for this question as cc-slack's store holds it, or, once the store forgets the card, as the drive's question stands: open since it was asked, or how it ended."""
    card = question["card"]
    if found is None or not card or card_open(card, found) or closed_card(found, card):
        return card_words(found, source, card or question["waits"])
    if (closed := question["closed"]) is None:
        return f"card {card} open since {when(question['asked'])}"
    said = f' "{" ".join(closed["words"].split())}"' if closed["words"] else ""
    return f"card {card} {closed['how']}{said} at {when(closed['at'])}"


def pr_card_words(found: dict | None, source: str, pr: dict) -> str | None:
    """His card on the PR as cc-slack's store holds it now: the open one at its head, an open one at an older head, named by the head it shows and never by its link, since a tap on it is not his tap on this head, or else the newest closed one; None when he never got one."""
    if found is None:
        return f"his cards could not be read ({source})"
    if shown := head_card(pr, found["pr_cards"]):
        return card_words(found, source, shown["url"])
    if shown := card_for(found["pr_cards"], pr["url"]):
        return f"his open card on it shows {shown_heads(shown, pr)} rather than its head {pr['head'][:7]}"
    shown = card_for(found["closed"], pr["url"])
    return shown and card_words(found, source, shown["url"])


def open_questions(job: dict) -> list[dict]:
    """The questions a job asked him that nothing has closed: no tap, words or ruling of his answered it, nobody withdrew it and no answer of his superseded it, a refused one included until it is asked again or dropped; his card on each follows it."""
    return [question for question in job["questions"] if question["closed"] is None]


def open_cards(job: dict) -> list[str]:
    """The cards of his a job's open questions are drawn on, each by its link or else its waits id."""
    return [drawn for question in open_questions(job) if (drawn := question["card"] or question["waits"])]


def drawn_on(drive: dict, *cards: str) -> list[tuple[str, dict]]:
    """Each job's question his question card under any of these waits ids or Slack links is drawn for, by its job: a question asked again on a withdrawn card shares its waits id with the one first drawn there."""
    keys = {message_key(card) for card in cards}
    return [
        (job_id, question)
        for job_id, job in drive["jobs"].items()
        for question in job["questions"]
        if question["waits"] in cards or question["card"] and message_key(question["card"]) in keys
    ]


def stored_waits(drive: dict, waits: list[str], found: dict | None = None) -> list[str]:
    """The waits as a job or PR stores them: each once, and a card of his named by its Slack link under its waits id, the one the drive's question drawn on that link holds or else the one cc-slack keeps on its open card there, so one card is one wait whichever way it is named; every other wait as given."""
    listed = [*found["questions"], *found["pr_cards"]] if found else []
    return list(dict.fromkeys(waits_id(drive, listed, wait) for wait in waits))


def waits_id(drive: dict, listed: list[dict], wait: str) -> str:
    if not wait.startswith("https://"):
        return wait
    named = [*(question["waits"] for _, question in drawn_on(drive, wait)), *(card["card"].get("waits") for card in listed if names_card(card, wait))]
    return next(filter(None, named), wait)


def holding(drive: dict, waits: list[str], found: dict | None) -> list[str]:
    """The waits of a job or PR that still hold: each of his cards still open, each job whose work is still to come, each PR still open, each part of an open order until it is delivered, each release until the watch sees it out while its PR is not closed unmerged, and a builder slot on this Mac until the watch gives the job its turn."""
    return [wait for wait in waits if holds(drive, wait, found)]


def holds(drive: dict, wait: str, found: dict | None) -> bool:
    if wait in drive["jobs"]:
        return coming(drive, wait)
    if wait in drive["prs"]:
        return drive["prs"][wait]["state"] == "open"
    if part := state.part_wait(drive, wait):
        return drive["orders"][part[0]]["state"] not in state.CLOSED_ORDER_STATES and not state.delivered(drive, *part)
    if release := state.RELEASE_WAIT.fullmatch(wait):
        return drive["prs"][release["pr"]]["state"] != "closed"
    if drawn := drawn_on(drive, wait):
        return any(question["closed"] is None for _, question in drawn)
    return wait == state.SLOT_WAIT or card_open(wait, found)


def coming(drive: dict, job_id: str) -> bool:
    """Whether the work a waited-on job was given is still to come: the job is not finished, or it is a builder or codex job that ended other than done while a part of an open order it carries is not delivered, which its own next run or the job that takes that part on delivers, so a wait on its change never ends on a HANDOFF, a BLOCKED or a stop that handed its work to another job."""
    job = drive["jobs"][job_id]
    if job["state"] not in state.FINISHED_JOB_STATES:
        return True
    return (
        job["state"] != "done"
        and job["kind"] in state.CHANGE_KINDS
        and any(
            not state.delivered(drive, order_id, part)
            for order_id in [job["order"], *job["carries"]]
            if (order := drive["orders"][order_id])["state"] not in state.CLOSED_ORDER_STATES
            for part in order["parts"].keys() & set(state.job_parts(job, order_id))
        )
    )


def wait_words(drive: dict, wait: str, found: dict | None) -> str:
    """What a PR's wait waits for, as his card says it to him: each PR, card or build by its title, linked in the markdown his card draws as a link."""
    if wait in drive["jobs"]:
        return f"the build of {cut(drive['jobs'][wait]['goal'], TITLE_CHARS)} finishes"
    if wait in drive["prs"]:
        pr = drive["prs"][wait]
        return f"{pr_link(pr)} lands" + (f", which waits on your Approve on {link('its card', card)}" if (card := tap_card(drive, pr, found)) else "")
    if release := state.RELEASE_WAIT.fullmatch(wait):
        return f"the {release['service']} release carrying {pr_link(drive['prs'][release['pr']])} is out"
    if part := state.part_wait(drive, wait):
        return f'"{cut(part[1], TITLE_CHARS)}" of your order "{order_title(drive["orders"][part[0]])}" is done'
    listed = [*found["questions"], *found["pr_cards"]] if found else []
    if card := next((card for card in listed if card["card"].get("waits") == wait or message_key(card["url"]) == message_key(wait)), None):
        return f"you answer {link(card['card']['title'], card['url'])}"
    return f"you answer {link('your question card', wait)}" if wait.startswith("https://") else "you answer your question card"


def tap_card(drive: dict, pr: dict, found: dict | None) -> str | None:
    """The link of his card a tap on lands the PR, which every line naming his card on it reads: while the PR waits on his tap, by state.asks_tap, his open card at its head; None with his cards unread."""
    if found is None or not state.asks_tap(drive, pr):
        return None
    shown = head_card(pr, found["pr_cards"])
    return shown and shown["url"]


def standing_card(pr: dict, found: dict) -> str | None:
    """The link of his card on the PR that a redraw in place draws again: an open one at any head, or one cc-slack keeps closed_in_place(), as it does from the redraw that took Approve off it until a redraw opens it again; None once none stands, so the watch draws a fresh one."""
    if shown := card_for(found["pr_cards"], pr["url"]):
        return shown["url"]
    closed = card_for(found["closed"], pr["url"])
    return closed["url"] if closed and closed_in_place(found, closed["url"]) else None


def pr_link(pr: dict) -> str:
    return link(pr_title(pr), pr["url"])


def link(label: str, url: str) -> str:
    return f"[{cut(label, TITLE_CHARS).translate(LINK_LABEL)}]({url})"


def read(name: str, moment: datetime, project: str | None = None, workstream: str | None = None) -> dict:
    """The named drive's model from its stored state and its stored cards, of the orders `build` files under project and workstream when named."""
    found, source = stored_cards(name)
    return build(state.load(name), found, moment, project=project, workstream=workstream) | {"cards": source}


@lru_cache(maxsize=4096)
def plain(text: str, limit: int) -> str:
    """The first line of his words as a title: Slack links as their labels, with no record ids, each word holding one dropped whole, so a waits id leaves no stray drive name or dash."""
    first = next((line for line in text.splitlines() if line.strip()), "")
    first = BOARD_PICK.sub("", SLACK_LINK.sub(lambda link: link[1], first))
    first = SHARED.sub(r"\1: ", LEADING_LINK.sub("", first))
    first = RECORD_TOKEN.sub("", PARENTHETICAL.sub(lambda match: "" if RECORD_ID.search(match[0]) else match[0], first))
    return cut(SPACES.sub("", COMMAS.sub(",", first)).strip(" ,"), limit)


@lru_cache(maxsize=4096)
def said(text: str | None) -> str:
    """A line as a row shows it: its record ids taken out, or as written when every sentence names one, so a line that says something never shows blank."""
    kept = without_records(text or "")
    return cut(kept if kept.strip() else text or "", NOW_CHARS + 1)


def order_title(order: dict) -> str:
    return order["title"] or plain(order["text"], TITLE_CHARS)


def thread_url(drive: dict, team_url: str, order: dict) -> str | None:
    """Slack's link to the thread an order's lines go in, in the channel that thread is in."""
    if not (team_url and (where := state.promise_thread(order))):
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
    return {"card": "wanted", "uncarded": "wanted", "rework": "reworking"}.get(stage_name(pr))


def stage_name(pr: dict) -> str | None:
    """The PR's stage as its row holds it: the one stage.of names now once stage.shown put it on, as build does for every surface it draws."""
    return (pr["stage"] or {}).get("name")


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


def head_card(pr: dict, pr_cards: list[dict]) -> dict | None:
    """His newest open card carrying the PR whose row shows its head."""
    return next((card for card in reversed(pr_cards) if pr["url"] in card["prs"] and pr["head"] in card_heads(card, pr["url"])), None)


def shown_heads(card: dict, pr: dict) -> str:
    return ", ".join(head[:7] or "no head" for head in card_heads(card, pr["url"])) or "no head"


def waits_on_tap(pr: dict) -> bool:
    """Whether the PR waits on his tap alone, as its stage on its row says: green and unapproved."""
    return pr["state"] == "open" and stage_name(pr) == "card"


def tap_claim_refusal(data: dict, order_id: str, text: str, pr_cards: list[dict]) -> str | None:
    """Why words say something waits on his tap or Approve when he has nothing to tap. A sentence saying so is judged by each open tap card it links, passing while each PR on it the sentence claims stands open at the head the card shows, those it names owing his tap, else those it names, else every PR on the card, so a PR that merged off the card is never blamed; and by each PR it names that owes his tap, passing while the words link its open tap card at its head, by head_card(), so a PR the sentence says shipped is never blamed; one linking no card while none of the PRs it names owes his tap refuses each with why."""
    linked = set(SLACK_TS.findall(text))
    found = []
    for sentence in SENTENCE_BREAK.split(text):
        if not TAP_CLAIM.search(SLACK_LINK.sub(r"\1", sentence)):
            continue
        cited = set(SLACK_TS.findall(sentence))
        cards = [card for card in pr_cards if message_key(card["url"]) in cited]
        named = state.named_prs(data, order_id, sentence)
        owed = [key for key in named if not limits.tap_owed_not(data["prs"][key])]
        for card in cards:
            carried = state.named_prs(data, order_id, " ".join(card["prs"]))
            for key in [key for key in carried if key in (owed or named)] or carried:
                pr = data["prs"][key]
                if pr["state"] != "open" or pr["head"] not in card_heads(card, pr["url"]):
                    found.append(f"{key}: its tap card {card['url']}, which the words link, no longer stands: {limits.tap_owed_not(pr) or 'it moved past the head the card shows'}")
        if not owed and cards:
            continue
        if not owed:
            for ref in dict.fromkeys(match[0] for match in state.PR_REF.finditer(sentence)):
                keys = state.named_prs(data, order_id, ref)
                found.append(f"{keys[0]}: {limits.tap_owed_not(data['prs'][keys[0]])}" if keys else f"{ref}: the drive holds no such PR, so no tap card of his is known for it")
            continue
        for key in owed:
            pr = data["prs"][key]
            if (card := head_card(pr, pr_cards)) is None:
                found.append(f"{key}: no open tap card of his stands at its head")
            elif message_key(card["url"]) not in linked:
                found.append(f"{key}: the words do not link its open tap card {card['url']}")
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
    now = f"; {pr['stage']['name']}, owed by {pr['stage']['owner']}: {pr['stage']['why']}" if pr["stage"] and pr["stage"].get("why") else ""
    return f"{checks}, {review}{changed}{now}"


def question_item(drive: dict, card: dict, features: list[dict], waiting: list[dict]) -> dict:
    pick = f"Recommended: {card['recommended']}" if card.get("recommended") else " / ".join(card["options"])
    finished = held_up(drive, card)
    tasks = [entry["id"] for entry in features if any(held["url"] == card["url"] for held in entry["cards"])]
    asker = card["job"]
    return {
        "kind": "question",
        "tasks": list(dict.fromkeys([*tasks, *([drive["jobs"][asker]["order"]] if asker else [])])),
        "job": asker,
        "holds": len(finished),
        "rail": {"held": finished},
        "blocks": [{"id": entry["id"], "title": entry["title"]} for entry in features if entry["id"] in tasks],
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
    """The green approved open PRs his question card holds from landing, by land.pr_cards, and the built jobs that wait on it, each PR by its title and link and each job by its goal and branch."""
    from drive import land

    names = {card["card"].get("waits"), message_key(card["url"])}
    finished = [pr for pr in drive["prs"].values() if pr["state"] == "open" and pr["checks"] == "pass" and pr["review"] == "approved" and any(message_key(held) in names for held in land.pr_cards(drive, pr))]
    built = [job for job in drive["jobs"].values() if job["state"] == "built" and any(message_key(wait) in names for wait in job["waits"])]
    return [
        *({"kind": "pr", "feature": pr["order"], "title": pr_title(pr), "url": pr["url"]} for pr in finished),
        *({"kind": "job", "feature": job["order"], "title": cut(job["goal"], TITLE_CHARS), "branch": job["branch"]} for job in built),
    ]


def asked_by(drive: dict, found: dict) -> dict[str, str | None]:
    """The job that asked each of his open question cards, by the card's link: the job whose question drew the card, never one folded into another's, which asks him only through that one, else the job whose question holds its waits id, else the job that waits id names."""
    drawn = {message_key(question["card"]): job_id for job_id, job in drive["jobs"].items() for question in job["questions"] if question["card"] and not question["replaced"]}
    asker = {}
    for card in found["questions"]:
        named = state.waits_job(drive, card["card"].get("waits") or "")
        asker[card["url"]] = drawn.get(message_key(card["url"])) or (named if named in drive["jobs"] else None)
    return asker


def with_questions(drive: dict, found: dict) -> dict:
    """His cards with his question cards as the drive's open questions decide them, each with the job that asked it: each card cc-slack lists, in its order, unless questions of the drive hold its waits id or are drawn on it and none of them still asks him, whatever cc-slack still lists, then, once per waits id, each open question drawn on a card cc-slack's store forgot, as `forgotten` draws it; a question a `Replaces:` line folded into another asks him only through the other."""
    asked = [(job_id, question) for job_id, job in drive["jobs"].items() for question in open_questions(job) if not question["replaced"]]
    asking = {key for _, question in asked for key in (question["waits"], question["card"] and message_key(question["card"])) if key}
    known = {key for job in drive["jobs"].values() for question in job["questions"] for key in (question["waits"], question["card"] and message_key(question["card"])) if key}
    asker = asked_by(drive, found)
    listed = [card | {"job": asker[card["url"]]} for card in found["questions"] if {card["card"].get("waits"), message_key(card["url"])} & asking or not {card["card"].get("waits"), message_key(card["url"])} & known]
    shown = {key for card in listed for key in (card["card"].get("waits"), message_key(card["url"]))}
    lost = {question["waits"]: forgotten(drive["name"], question) | {"job": job_id} for job_id, question in asked if question["card"] and not {question["waits"], message_key(question["card"])} & shown}
    return found | {"questions": [*listed, *lost.values()]}


def forgotten(name: str, question: dict) -> dict:
    """His card on an open question as the drive keeps it once cc-slack's store forgot it: drawn from the ASK file the question is kept in, as `drive cards redraw` draws it before filling its references and pictures, or from the question alone when it is kept in none, or in a file that is gone or no longer draws, as `drive cards redraw` refuses it."""
    drawn = kept_card(name, question) or {"title": question["title"] or "his question card", "body": "", "options": [], "recommended": "", "waits": question["waits"], "drive": name}
    return {"question": drawn["title"], "options": drawn["options"], "recommended": drawn["recommended"], "asked": question["asked"], "url": question["card"], "card": drawn}


def kept_card(name: str, question: dict) -> dict | None:
    if not question["file"]:
        return None
    try:
        return limits.redrawn(Path(question["file"]).read_text()).card(name, question["waits"])
    except (state.DriveError, FileNotFoundError):
        return None


def rail(drive: dict, item: dict, reports: set[str], heard: dict[str, list[dict]]) -> dict:
    """What the dashboard draws beside a question card beyond the card itself: what it holds up, the job that asked it with the files the drive keeps for it, and his recorded answers the drive matched to the card, newest first."""
    key = item["card"].get("waits") or message_key(item["answer"])
    answers = sorted((answer for answer in drive["answers"].values() if key in answer["cards"]), key=lambda answer: state.parse_time(answer["at"]), reverse=True)
    return item["rail"] | {
        "asker": item["job"] and job_entry(item["job"], drive["jobs"][item["job"]], reports, heard),
        "answers": [{"words": answer["words"], "at": answer["at"], "mark": (answer["cards"][key] or {}).get("mark")} for answer in answers],
    }


def tap_item(drive: dict, pr: dict, card: dict | None) -> dict:
    """A PR card in his queue, with each of its PRs as the drive holds it now, in the card's order, since a stacked PR can merge while the card still waits on him: titled as the card is once it carries several, and naming as its PR the first still open, the one its pane opens."""
    if not card:
        return {"kind": "tap", "tasks": [pr["order"]], "title": pr_title(pr), "detail": f"{tap_detail(pr)}; no Slack card yet", "since": None, "due": None, "url": pr["url"], "links": [], "pr": pr["url"], "checks": pr["checks"], "prs": [pr_entry(pr)]}
    rows = [named_pr(drive["prs"], state.pr_key(row["url"])) for row in card["card"].get("prs") or []]
    opened = next((row for row in rows if row["state"] == "open"), pr)
    return {
        "kind": "tap",
        "tasks": list(dict.fromkeys(row["order"] for row in rows if row["order"])) or [pr["order"]],
        "title": kind_and_title(card["card"]["title"])[1] if len(rows) > 1 else pr_title(pr),
        "detail": tap_detail(opened),
        "pr": opened["url"],
        "checks": opened["checks"],
        "since": card["asked"],
        "due": None,
        "url": card["url"],
        "links": [{"label": "Pull request", "url": opened["url"]}],
        "card": card["card"],
        "answer": card["url"],
        "thread": card_thread(card["url"]),
        "prs": [pr_entry(row) for row in rows],
    }


def late_item(drive: dict, order_id: str, order: dict, team_url: str) -> dict:
    return {
        "kind": "late",
        "tasks": [order_id],
        "title": order_title(order),
        "detail": said(order.get("note")),
        "since": order["created"],
        "due": state.owed_at(order),
        "url": thread_url(drive, team_url, order),
        "links": [],
    }


def is_late(order: dict, moment: datetime) -> bool:
    return order["state"] not in state.CLOSED_ORDER_STATES and state.owed(order) and state.parse_time(state.owed_at(order)) <= moment


def queue(drive: dict, found: dict, moment: datetime, features: list[dict], answering: dict[str, str]) -> list[dict]:
    taps = [(pr, head_card(pr, found["pr_cards"])) for pr in drive["prs"].values() if waits_on_tap(pr)]
    taps = list({card["url"] if card else pr["url"]: (pr, card) for pr, card in taps}.values())
    waiting = waiting_on_him(drive, found)
    items = [
        *(question_item(drive, card, features, waiting) for card in found["questions"]),
        *(tap_item(drive, pr, card) for pr, card in taps),
        *(late_item(drive, order_id, order, found["team_url"]) for order_id, order in drive["orders"].items() if is_late(order, moment)),
    ]
    filed = {entry["id"]: entry for entry in features}
    urgent = {entry["id"]: entry["title"] for entry in incidents(drive)}
    ranked = []
    for item in items:
        home = next((filed.get(order_id) or drive["orders"][order_id] for order_id in item["tasks"] if order_id in drive["orders"]), None)
        item |= {"project": home and home["project"], "workstream": home and home["workstream"]}
        rank, item["rank_why"] = ranking(drive, item, item["tasks"], urgent, moment, answering.get(item.get("thread")))
        ranked.append((rank, item))
    return [
        item | {"priority": {"rank": position, "answering": rank[0], "incident": not rank[1], "overdue": not rank[2], "weight": -rank[4]}}
        for position, (rank, item) in enumerate(sorted(ranked, key=lambda entry: entry[0]), 1)
    ]


def ranking(drive: dict, item: dict, orders: list[str], urgent: dict[str, str], moment: datetime, asked: str | None) -> tuple[tuple, str]:
    """Where an item ranks in his queue, and why in plain words: below every other while the drive answers words of his typed under its card, then an open incident it serves, past due, a built job it holds, how much it holds, then how long it has waited."""
    incident = next((urgent[order_id] for order_id in orders if order_id in urgent), None)
    late = item["due"] or next((state.owed_at(drive["orders"][order_id]) for order_id in orders if order_id in drive["orders"] and is_late(drive["orders"][order_id], moment)), None)
    built = len(item.get("built") or [])
    if item["kind"] == "question":
        weight = item["holds"] + len(item["blocks"])
        holding = f"holds {counted(item['holds'], 'finished PR or built job', 'finished PRs or built jobs')} and {counted(len(item['blocks']), 'task')}" if weight else ""
    elif item["kind"] == "tap":
        lands = [pr for pr in item["prs"] if pr["state"] == "open"]
        above = stacked_above(drive, [state.pr_key(pr["url"]) for pr in lands])
        weight = len(lands) + len(above) + len(orders)
        holding = f"lands {counted(len(lands), 'PR')}" + (f", and {counted(len(above), 'PR')} stacked above {'it waits' if len(above) == 1 else 'them wait'} on it" if above else "")
    else:
        weight, holding = 0, ""
    since = item["since"] or (drive["prs"][state.pr_key(item["pr"])]["updated"] if item.get("pr") else None)
    why = [
        f"sent down while the drive answers your words of {when(asked)}" if asked else "",
        f"incident: {cut(incident, TITLE_CHARS)}" if incident else "",
        f"past due since {when(late)}" if late else "",
        f"{counted(built, 'built job')} {'waits' if built == 1 else 'wait'} on it" if built else "",
        holding,
        f"waiting since {when(since)}" if since and not late else "",
    ]
    rank = (asked is not None, incident is None, late is None, not built, -weight, state.parse_time(late or since) if late or since else datetime.max.replace(tzinfo=moment.tzinfo))
    return rank, "; ".join(part for part in why if part)


def answering(events: Iterable[dict]) -> dict[str, str]:
    """When his newest words typed on the dashboard under a card went out, by that card's thread, for each thread the drive has posted nothing new in since: what waits on the drive's answer there rather than on him."""
    waiting: dict[str, str] = {}
    for event in events:
        if event["kind"] == "asked":
            waiting[card_thread(event["card"])] = event["at"]
        elif waiting and event["kind"] == "said" and not event.get("edited"):
            waiting.pop(event["thread"], None)
    return waiting


def stacked_above(drive: dict, keys: list[str]) -> list[str]:
    """The open PRs stacked above any of keys that keys do not already name, each by its key."""
    above = []
    for key in keys:
        stack = drive["prs"][key].get("stack") or []
        above += stack[stack.index(key) + 1 :] if key in stack else []
    return [member for member in dict.fromkeys(above) if member not in keys and drive["prs"].get(member, {}).get("state") == "open"]


def counted(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else many or one + 's'}"


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
    if all_merged(prs):
        return "merged"
    if order["state"] == "waiting" or any(job["state"] == "waiting" for job in jobs):
        return "waiting"
    return "not started"


def all_merged(prs: list[dict]) -> bool:
    return any(pr["state"] == "merged" for pr in prs) and all(pr["state"] != "open" for pr in prs)


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
    return next((card for card in found["questions"] if names_card(card, wait)), None)


def names_card(card: dict, wait: str) -> bool:
    """Whether a wait names his card, by its waits id or its Slack link."""
    return card["card"].get("waits") == wait or message_key(card["url"]) == message_key(wait)


def wait_entry(drive: dict, wait: str, found: dict) -> dict:
    if wait in drive["jobs"]:
        return {"title": f"{wait}: {cut(drive['jobs'][wait]['goal'], TITLE_CHARS)}", "url": None}
    if wait in drive["prs"]:
        return {"title": f"{wait} merging: {cut(pr_title(drive['prs'][wait]), TITLE_CHARS)}", "url": state.pr_url(wait)}
    if release := state.RELEASE_WAIT.fullmatch(wait):
        return {"title": f"the release of {release['service']} carrying {release['pr']}", "url": state.pr_url(release["pr"])}
    if part := state.part_wait(drive, wait):
        return {"title": f'{part[0]} delivering "{cut(part[1], TITLE_CHARS)}"', "url": None}
    if wait == state.SLOT_WAIT:
        return {"title": "a builder slot on this Mac", "url": None}
    if card := asked_card(found, wait):
        return {"title": card["question"], "url": card["url"]}
    return {"title": "his question card", "url": wait if wait.startswith("https://") else None}


def waiting_on_him(drive: dict, found: dict) -> list[dict]:
    """Each open card of his that holds a job built on its recommended option, once per job and card however many of the job's waits name it, oldest card first, with the card's waits id, else its link, as the wait, the job, its order and its branch."""
    held = {
        (job_id, card["url"]): {"card": card, "wait": card["card"].get("waits") or card["url"], "job": job_id, "order": job["order"], "branch": job["branch"]}
        for job_id, job in drive["jobs"].items()
        if job["state"] == "built"
        for wait in job["waits"]
        if wait not in drive["jobs"] and (card := asked_card(found, wait))
    }
    return sorted(held.values(), key=lambda entry: state.parse_time(entry["card"]["asked"]))


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


def served_prs(drive: dict) -> dict[str, dict[str, dict]]:
    """Each order's PRs by key: its jobs' own, each `drive pr set --serves` points at it, and each PR of a job that took it over or carries the order, the orders `state.served_orders` checks a PR against, in one pass over the jobs."""
    keys = {pr["url"]: key for key, pr in drive["prs"].items()}
    served: dict[str, dict[str, dict]] = {}
    for key, pr in drive["prs"].items():
        for order_id in filter(None, (pr["order"], pr.get("serves"))):
            served.setdefault(order_id, {})[key] = pr
    for job in drive["jobs"].values():
        for url in filter(keys.__contains__, job["prs"]):
            for order_id in (job["order"], *job["carries"]):
                served.setdefault(order_id, {})[keys[url]] = drive["prs"][keys[url]]
    return served


def feature(drive: dict, order_id: str, order: dict, moment: datetime, found: dict, prs: list[dict]) -> dict:
    jobs = [drive["jobs"][job_id] for job_id in order["jobs"]]
    return {
        "id": order_id,
        "title": order_title(order),
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
    served = served_prs(drive)
    features = [feature(drive, order_id, order, moment, found, list(served.get(order_id, {}).values())) for order_id, order in drive["orders"].items() if shown(order, moment)]
    features.sort(key=lambda entry: state.parse_time(entry["updated"]), reverse=True)
    features.sort(key=lambda entry: (entry["due"] is None, entry["due"] and state.parse_time(entry["due"])))
    spellings = {}
    for entry in features:
        for place in ("project", "workstream"):
            if entry[place]:
                entry[place] = spellings.setdefault((place, entry[place].casefold()), entry[place])
    return [{"state": name, "features": [entry for entry in features if entry["state"] == name]} for name in STATES]


def build(drive: dict, found: dict, moment: datetime, events: Iterable[dict] = (), project: str | None = None, workstream: str | None = None) -> dict:
    """The drive's model from its stored state with each open PR at the stage stage.of names now, never one the watch kept a pass ago, his open cards as cc-slack lists them, each question card as `with_questions` reads it from the drive's open questions, the events naming his words typed under a card and the drive's posts since, and the time it is drawn for; naming a project, and a workstream of it, only the features and queue items filed there, ignoring case as `status` merges spellings."""
    from drive import stage

    stage.shown(drive, found, state.open_pr_keys(drive))
    found = with_questions(drive, found)
    groups = status(drive, moment, found)
    waiting = queue(drive, found, moment, [entry for group in groups for entry in group["features"]], answering(events))
    if project:
        groups = [group | {"features": [entry for entry in group["features"] if filed_under(entry, project, workstream)]} for group in groups]
        waiting = [item for item in waiting if filed_under(item, project, workstream)]
    counts = {"queue": len(waiting), **{group["state"]: len(group["features"]) for group in groups}}
    return {"drive": drive["name"], "at": state.stamp(moment), "counts": counts, "queue": waiting, "status": groups}


def filed_under(entry: dict, project: str, workstream: str | None) -> bool:
    return (entry["project"] or "").casefold() == project.casefold() and (workstream is None or (entry["workstream"] or "").casefold() == workstream.casefold())


def job_entry(job_id: str, job: dict, reports: set[str], heard: dict[str, list[dict]]) -> dict:
    return {
        "id": job_id,
        "feature": job["order"],
        "kind": job["kind"],
        "goal": job["goal"],
        "state": job["state"],
        "where": job.get("where") or "local",
        "said": said(job["said"]),
        "updated": job["updated"],
        "word": newest_word(job_id, job, heard),
        "prs": len(job["prs"]),
        "branch": job["branch"],
        "waits": len(job["waits"]),
        "files": artifacts.kept(job_id, job, reports),
        "agent": job.get("agent") or job.get("dispatch"),
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
    """What happened on every day of the drive, newest first: his orders, his recorded answers, features gone live, merges, failed jobs, his taps and answers on cards and their undos, a dashboard answer offering its Undo until cc-slack's window closes or a later undo or answer of its card, refused cards and every message he was sent, with its visuals, a message reworded in place showing its newest words at the time it was sent; the swears counted are those in his own words, his Slack orders and recorded answers."""
    prs = drive["prs"]
    happened = []
    sent: dict[str, dict] = {}
    undoable: dict[str, dict] = {}
    for order_id, order in drive["orders"].items():
        url = thread_url(drive, team_url, order)
        swears = len(SWEARS.findall(limits.his_words(order)))
        happened.append({"at": order["created"], "kind": "order", "text": order_title(order), "detail": "", "url": url, "feature": order_id, "swears": swears})
        if order["state"] == "done":
            happened.append({"at": order["updated"], "kind": "live", "text": order_title(order), "detail": said(order.get("proof")), "url": url, "feature": order_id})
    for pr in prs.values():
        if pr["state"] == "merged":
            happened.append({"at": pr["updated"], "kind": "merged", "text": pr_title(pr), "detail": "", "url": pr["url"], "feature": pr["order"]})
    for job in drive["jobs"].values():
        if job["state"] == "failed":
            happened.append({"at": job["updated"], "kind": "failed", "text": job["goal"], "detail": said(job["said"]), "url": None, "feature": job["order"]})
    for answer in drive["answers"].values():
        happened.append({"at": answer["at"], "kind": "answer", "text": plain(answer["words"], TITLE_CHARS), "detail": "", "url": answer["link"], "feature": None, "swears": len(SWEARS.findall(answer["words"]))})
    recorded = {(card_thread(answer["link"]) or "").replace(".", "") for answer in drive["answers"].values()}
    for event in (event for event in events if event["kind"] in HISTORY_EVENTS):
        if event["kind"] == "answered" and message_key(event["url"]) in recorded:
            continue
        if event["kind"] in ("answered", "undone"):
            if taken := undoable.pop(message_key(event["url"]), None):
                taken["undo_until"] = None
            decided = {"at": event["at"], "kind": event["kind"], "text": event["question"] or "a card", "detail": event["label"], "url": event["url"], "feature": None, "undo_until": event.get("undo_until")}
            if decided["undo_until"]:
                undoable[message_key(event["url"])] = decided
            happened.append(decided)
            continue
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
    card = head_card(pr, found["pr_cards"])
    order = drive["orders"].get(pr["order"]) or {}
    job = drive["jobs"].get(pr["job"])
    group = "decided" if pr.get("tap") else "you" if waits_on_tap(pr) else "not ready"
    return {
        "key": key,
        "title": pr_title(pr),
        "url": pr["url"],
        "head": pr["head"],
        "group": group,
        "feature": pr["order"],
        "feature_title": order_title(order) if order else "",
        "detail": tap_detail(pr),
        "checks": pr["checks"],
        "review": pr["review"],
        "tap": tap_of(pr),
        "stage": pr["stage"],
        "failed": pr["failed_checks"],
        "conflict": pr["conflict"],
        "draft": pr["draft"],
        "branch": pr["branch"],
        "base": pr["base"],
        "stack": pr.get("stack") or [],
        "landing": landing_words(pr),
        "approve_waits": approve_waits(drive, key, pr, found),
        "parts": pr["parts"],
        "lead": pr["lead"],
        "job": pr["job"],
        "job_said": said(job["said"]) if job else "",
        "job_at": job["updated"] if job else None,
        "since": card["asked"] if card else None,
        "updated": pr["updated"],
        "card": card["card"] if card else None,
        "answer": card["url"] if card else None,
        "thread": card and card_thread(card["url"]),
    }


def approve_waits(drive: dict, key: str, pr: dict, found: dict) -> str | None:
    """What still holds the PR once he approves it, as its lander reads it: empty when his Approve queues it at once, None for a repository no lander queues."""
    from drive import land

    if not state.queued_by_lander(key):
        return None
    return land.unready(drive | {"prs": drive["prs"] | {key: pr | {"tap": state.new_tap("Approve", pr["head"], "dashboard")}}}, key, found) or ""


def landing_words(pr: dict) -> str | None:
    """What the drive's lander did with a PR, as its card says it: the outcome of its last try, or that one runs now on its head."""
    lander = pr["lander"]
    if not lander:
        return None
    return cut(lander["outcome"], NOW_CHARS) if lander.get("outcome") else f"a lander started on {lander['head'][:7]} at {when(lander['at'])}"


def review(drive: dict, found: dict) -> list[dict]:
    entries = [review_entry(key, pr, drive, found) for key, pr in drive["prs"].items() if pr["state"] == "open"]
    entries.sort(key=lambda entry: state.parse_time(entry["updated"]), reverse=True)
    entries.sort(key=lambda entry: (entry["since"] is None, entry["since"] and state.parse_time(entry["since"])))
    return entries


def board(drive: dict, found: dict, source: str, events: Iterable[dict], moment: datetime, reports: set[str], heard: dict[str, list[dict]]) -> dict:
    """The dashboard's model: `build`'s, with progress, work, history and each shown feature's full order, jobs and the newest records naming them; reports names the jobs whose history the drive keeps, which holds every return each took, and heard holds the newest records naming each order and job, newest first."""
    model = build(drive, found, moment, events) | {"cards": source}
    served = served_prs(drive)
    shown_ids = {entry["id"] for group in model["status"] for entry in group["features"]}
    for entry in (entry for group in model["status"] for entry in group["features"]):
        order = drive["orders"][entry["id"]]
        entry |= {
            "text": order["text"],
            "note": said(order.get("note")),
            "created": order["created"],
            "jobs": [job_entry(job_id, drive["jobs"][job_id], reports, heard) for job_id in order["jobs"]],
            "parts": [part_entry(drive, entry["id"], part) for part in order["parts"]],
            "where": where_it_is(drive, order),
            "holdup": holdup(drive, entry, found, served, heard),
            "heard": heard_of(heard, [entry["id"], *order["jobs"]]),
        }
    for item in (item for item in model["queue"] if item["kind"] == "question"):
        item["rail"] = rail(drive, item, reports, heard)
    active = [job_entry(job_id, job, reports, heard) for job_id, job in drive["jobs"].items() if job["order"] in shown_ids and job["state"] not in state.FINISHED_JOB_STATES]
    active.sort(key=lambda entry: (entry["state"] != "running", state.parse_time(entry["updated"])))
    told = history(drive, events, found["team_url"])
    today = day_of(model["at"])
    return model | {
        "counts": model["counts"] | {"jobs": sum(job["state"] == "running" for job in drive["jobs"].values()), "lanes": len(active), "merged_today": sum(entry["kind"] == "merged" and day_of(entry["at"]) == today for entry in told)},
        "progress": progress(drive),
        "work": active,
        "history": told,
        "review": review(drive, found),
        "incidents": [entry | {"orphaned": orphaned(heard, entry["id"])} for entry in incidents(drive)],
        "bug_reports": [entry | {"orphaned": orphaned(heard, entry["id"])} for entry in bug_reports(drive, moment, served)],
        "channel": slack.dm(),
    }


def part_entry(drive: dict, order_id: str, part: str) -> dict:
    """One part of an order as its task lists it: whether it is the whole order, delivered or not, the post that answered it when it is words he reads, and the newest job carrying it, by state.carriers, with that job's kind, state and when it last moved."""
    order = drive["orders"][order_id]
    newest = max(state.carriers(drive, order_id, part), key=lambda pair: state.parse_time(pair[1]["updated"]), default=None)
    return {
        "name": part,
        "whole": part in (order["title"], state.whole_order(order["text"])),
        "delivered": state.delivered(drive, order_id, part),
        "posted": None if state.work(drive, order_id, part) else order["parts"][part]["posted"],
        "job": newest and newest[0],
        "kind": newest and newest[1]["kind"],
        "state": newest and newest[1]["state"],
        "at": newest and newest[1]["updated"],
    }


def where_it_is(drive: dict, order: dict) -> dict | None:
    """The line Where it is draws whole: a done order's proof, else the newest job line with that job and its state now, so a report word the job has moved past reads as its last report, else the order's note."""
    if order["state"] == "done" and order.get("proof"):
        return where_line(order["proof"], None, None, order["updated"])
    lines = [(job_id, drive["jobs"][job_id]) for job_id in order["jobs"] if drive["jobs"][job_id].get("said")]
    if lines:
        job_id, job = max(lines, key=lambda pair: state.parse_time(pair[1]["updated"]))
        return where_line(job["said"], job_id, job["state"], job["updated"])
    return where_line(order["note"], None, None, order["updated"]) if order.get("note") else None


def where_line(text: str, job_id: str | None, job_state: str | None, at: str) -> dict:
    """A Where it is line on one line with its record ids taken out, or as written when every sentence of it names one, so a line that says something never draws blank."""
    return {"text": " ".join(without_records(text).split()) or " ".join(text.split()), "job": job_id, "state": job_state, "at": at}


def heard_line(record: dict) -> dict:
    """A cci record as a page tells it: its seq, when, the lane that posted it, its kind and its words as a line."""
    return {"seq": record["seq"], "at": state.stamp(state.parse_time(record["at"])), "lane": record["lane"], "kind": record["kind"], "text": said(record["text"])}


def heard_of(heard: dict[str, list[dict]], ids: list[str]) -> list[dict]:
    """The newest HEARD_LINES records naming any of ids, newest first, each once."""
    lines = {line["seq"]: line for key in ids for line in heard.get(key, [])}
    return sorted(lines.values(), key=lambda line: line["seq"], reverse=True)[:HEARD_LINES]


def orphaned(heard: dict[str, list[dict]], order_id: str) -> str | None:
    """When the watch last said nothing works on the order, from the newest records naming it."""
    return next((line["at"] for line in heard.get(order_id, []) if line["text"].startswith("ORPHAN")), None)


def newest_word(job_id: str, job: dict, heard: dict[str, list[dict]]) -> dict | None:
    """The newest thing a job said, as its text and time: its newest record, or its last report when that came later."""
    lines = [{"text": line["text"], "at": line["at"]} for line in heard.get(job_id, [])[:1]]
    if job["said"]:
        lines.append({"text": said(job["said"]), "at": job["updated"]})
    return max(lines, key=lambda line: state.parse_time(line["at"]), default=None)


def holdup(drive: dict, entry: dict, found: dict, served: dict[str, dict[str, dict]], heard: dict[str, list[dict]]) -> dict | None:
    """What a task waits on, agreeing with the state it shows: the failed job or red PR blocking it, the job on it, his answer or tap, a PR's checks or wait, the job it waits behind, or its proof once merged; card and pr name what the phrase opens."""
    order_id = entry["id"]
    jobs = sorted(((job_id, drive["jobs"][job_id]) for job_id in drive["orders"][order_id]["jobs"]), key=lambda pair: state.parse_time(pair[1]["updated"]), reverse=True)
    prs = [(key, pr) for key, pr in served.get(order_id, {}).items() if pr["state"] == "open"]
    match entry["state"]:
        case "live":
            return None
        case "blocked":
            if failed := newest_job(jobs, "failed"):
                return waits_on(f"{failed[1]['kind']} {failed[0]} failed: {cut(failed[1]['goal'], TITLE_CHARS)}")
            key, pr = next((key, pr) for key, pr in prs if pr["checks"] == "fail")
            return waits_on(f"red checks on #{key.partition('#')[2]}", pr=key, url=pr["url"])
        case "running":
            job_id, job = newest_job(jobs, "running")
            if word := newest_word(job_id, job, heard):
                return waits_on(f"{job['kind']} {job_id}: {word['text']}", at=word["at"])
            return waits_on(f"{job['kind']} {job_id} is on it: {cut(job['goal'], TITLE_CHARS)}")
        case "merged":
            return waits_on("waits on proof it works where you use it")
        case "not started":
            return waits_on("no job has started on it yet")
    if entry["cards"]:
        return waits_on(f"waits on your answer to {entry['cards'][0]['title']}", card=entry["cards"][0]["url"])
    if tap := next(((key, pr) for key, pr in prs if waits_on_tap(pr)), None):
        return waits_on(f"waits on your tap on #{tap[0].partition('#')[2]}", pr=tap[0], url=tap[1]["url"])
    if checking := next(((key, pr) for key, pr in prs if pr["checks"] == "pending"), None):
        return waits_on(f"waits on #{checking[0].partition('#')[2]}'s checks", pr=checking[0], url=checking[1]["url"])
    if wait := next((wait for _, owner in [*jobs, *prs] if owner["state"] in ("built", "waiting", "open") for wait in owner["waits"]), None):
        named = wait_entry(drive, wait, found)
        return waits_on(f"waits on {named['title']}", url=named["url"])
    if waiting := newest_job(jobs, "built", "waiting"):
        return waits_on(f"waits on {waiting[1]['kind']} {waiting[0]}: {cut(waiting[1]['goal'], TITLE_CHARS)}")
    if prs:
        return waits_on(f"waits on #{prs[0][0].partition('#')[2]}", pr=prs[0][0], url=prs[0][1]["url"])
    return waits_on("waits on nothing the drive tracks; its note says why")


def newest_job(jobs: list[tuple[str, dict]], *states: str) -> tuple[str, dict] | None:
    return next(((job_id, job) for job_id, job in jobs if job["state"] in states), None)


def waits_on(text: str, card: str | None = None, pr: str | None = None, url: str | None = None, at: str | None = None) -> dict:
    return {"text": text, "card": card, "pr": pr, "url": url, "at": at}


def incidents(drive: dict) -> list[dict]:
    """His incident orders still open, oldest first, but one the watch found over, its fix live and any monitor of it OK, while its close is posted."""
    live = [(order_id, order) for order_id, order in drive["orders"].items() if order["incident"] and order["state"] not in state.CLOSED_ORDER_STATES and not order["recovered"]]
    return [{"id": order_id, "title": order_title(order), "created": order["created"]} for order_id, order in sorted(live, key=lambda item: state.parse_time(item[1]["created"]))]


def bug_reports(drive: dict, moment: datetime, served: dict[str, dict[str, dict]]) -> list[dict]:
    """Each shown order fixing a bug someone reported, oldest report first, at the stage it reached on the way to its reporters hearing the fix is live."""
    reported = sorted(((order_id, order) for order_id, order in drive["orders"].items() if state.reported_bug(order) and shown(order, moment)), key=lambda item: state.parse_time(item[1]["created"]))
    return [{"id": order_id, "title": order_title(order), "created": order["created"], "stage": bug_stage(order, list(served.get(order_id, {}).values()))} for order_id, order in reported]


def bug_stage(order: dict, prs: list[dict]) -> str:
    """Told once drive order told records every reporter's fix-live note, live once the order keeps its proof, merged once a PR on it merged and none is open, fixing while a job or PR is on it, else reported."""
    if not state.untold_reporters(order):
        return "told"
    if order["proof"]:
        return "live"
    if all_merged(prs):
        return "merged"
    return "fixing" if order["jobs"] or prs else "reported"


def glance(card: dict) -> dict:
    """A card as a page lists it before he opens it: its question, its options, the PRs it carries and whether they are sibling PRs of one request, so a PR page never calls them a stack."""
    return {"title": card["title"], "options": card["options"], "recommended": card.get("recommended"), "prs": [{"url": row["url"], "title": row["title"]} for row in card.get("prs") or []], "siblings": card.get("siblings", False)}


def outline(entry: dict) -> dict:
    return {key: value for key, value in entry.items() if key not in ("text", "jobs", "parts", "where", "holdup", "heard")} | {"running": sum(job["state"] == "running" for job in entry["jobs"])}


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


def counts(moments: list[dict]) -> dict:
    return {
        "merged": sum(entry["kind"] == "merged" for entry in moments),
        "swears": sum(entry["swears"] for entry in moments if entry["kind"] in ("order", "answer")),
        "asked": sum(entry["kind"] == "order" for entry in moments),
        "moments": len(moments),
    }


def history_page(model: dict, day: str | None, show: str, page: int) -> dict:
    """One page of HISTORY_PAGE moments, newest first, of the Pacific day asked or else of every day, kept to the kinds show names, with each day's counts from the drive's first day to today and, for the day asked, each Pacific hour's."""
    by_day: dict[date, list[dict]] = {}
    for entry in model["history"]:
        by_day.setdefault(day_of(entry["at"]), []).append(entry)
    days = []
    if model["history"]:
        first, last = day_of(model["history"][-1]["at"]), day_of(model["at"])
        days = [{"day": each.isoformat()} | counts(by_day.get(each, [])) for each in (first + timedelta(days=n) for n in range((last - first).days + 1))]
    zoomed = day if any(each["day"] == day for each in days) else None
    kinds = HISTORY_SHOWN[show]
    scope = by_day.get(date.fromisoformat(zoomed), []) if zoomed else model["history"]
    hours = []
    if zoomed:
        by_hour: list[list[dict]] = [[] for _ in range(24)]
        for entry in scope:
            by_hour[state.parse_time(entry["at"]).astimezone(ZONE).hour].append(entry)
        hours = [{"hour": hour} | counts(moments) for hour, moments in enumerate(by_hour)]
    kept = [entry for entry in scope if kinds is None or entry["kind"] in kinds]
    listed = kept[(page - 1) * HISTORY_PAGE : page * HISTORY_PAGE]
    return {"days": days, "hours": hours, "day": zoomed, "moments": listed, "page": page, "pages": max(1, math.ceil(len(kept) / HISTORY_PAGE))}


def story(model: dict, ids: set[str] | None) -> list[dict]:
    """The newest STORY_LINES moments of the features named, of every kind and with each record naming them, or the whole drive's newest orders, launches, merges and taps."""
    if ids is None:
        return list(itertools.islice((entry for entry in model["history"] if entry["kind"] in STORY_KINDS), STORY_LINES))
    heard = [{"at": line["at"], "kind": "heard", "text": line["text"], "detail": line["lane"], "url": None, "feature": entry["id"]} for group in model["status"] for entry in group["features"] if entry["id"] in ids for line in entry["heard"]]
    told = [entry for entry in model["history"] if entry["feature"] in ids]
    return sorted([*told, *heard], key=lambda entry: state.parse_time(entry["at"]), reverse=True)[:STORY_LINES]
