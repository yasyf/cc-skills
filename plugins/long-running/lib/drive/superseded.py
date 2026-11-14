"""What each answer or ruling of his leaves standing: the cards open, orders and parts of orders undelivered and PRs not yet live when it was recorded that it could change, until each is marked standing, settled, dropped, superseded or reopened by it, and the jobs waiting on the orders it touches, until each has heard it or is marked untouched."""

import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from drive import github, limits, served, settled, slack, state, view
from drive.render import cut, when
from drive.settled import TERMINAL
from drive.state import DriveError

OTHERS_STAND = 3
SUBJECT_TERMS = 3
GENERIC_TITLES = 10
PICK = re.compile(r"\b([0-9a-f]{7})[0-9a-f]*:")


def asking(data: dict, found: dict) -> dict[str, dict]:
    """Every card asking him, order and part undelivered and PR not yet live, by the key a mark names it with: the card of each job's open question by its waits id, and each unrefused open question no card draws yet by its waits id, so his answer settles it before the watch draws it, then each open question card cc-slack shows that no job asked, each undelivered part of an open order holding more than one by `<order>:<part>`, else the open order itself by its id, since an order's only part goes with the order, each PR waiting on his tap, then each other open PR and each merged one whose job is still at work, by its PR key."""
    shown = {view.message_key(card["url"]): card for card in found["questions"]}
    asked = {}
    for job_id, job in data["jobs"].items():
        for question in view.open_questions(job):
            if drawn := question["card"]:
                card = shown.get(view.message_key(drawn)) or {"question": question["title"], "options": [], "card": {}}
                asked[question["waits"] or card["card"].get("waits") or view.message_key(drawn)] = {
                    "kind": "question",
                    "title": question["title"] or card["question"],
                    "options": card["options"],
                    "job": job_id,
                    "order": job["order"],
                    "url": drawn,
                    "card": drawn,
                }
            elif question["waits"] and not question["refused"]:
                asked[question["waits"]] = {"kind": "question", "title": question["title"], "options": [], "job": job_id, "order": job["order"], "url": "", "card": None}
    asker = view.asked_by(data, found)
    for card in found["questions"]:
        if asker[card["url"]] is None:
            asked[card["card"].get("waits") or view.message_key(card["url"])] = {
                "kind": "question",
                "title": card["question"],
                "options": card["options"],
                "job": None,
                "order": None,
                "url": card["url"],
                "card": card["url"],
            }
    for order_id, order in data["orders"].items():
        if order["state"] in state.CLOSED_ORDER_STATES:
            continue
        undelivered = [part for part in order["parts"] if not state.delivered(data, order_id, part)]
        if len(undelivered) < 2:
            asked[order_id] = {
                "kind": "order",
                "title": view.order_title(order),
                "job": None,
                "jobs": [job_id for job_id in order["jobs"] if data["jobs"][job_id]["state"] not in state.FINISHED_JOB_STATES],
                "order": order_id,
                "url": "",
                "card": None,
            }
        for part in undelivered if len(undelivered) > 1 else []:
            asked[f"{order_id}:{part}"] = {
                "kind": "part",
                "title": part,
                "job": None,
                "jobs": [job_id for job_id, job in state.part_jobs(data, order_id, part) if job["state"] not in state.FINISHED_JOB_STATES],
                "order": order_id,
                "url": "",
                "card": None,
            }
    for key, pr in data["prs"].items():
        card = view.card_for(found["pr_cards"], pr["url"])
        if view.waits_on_tap(pr):
            asked[key] = {
                "kind": "tap",
                "title": view.pr_title(pr),
                "job": pr["job"],
                "order": pr["order"],
                "url": card["url"] if card else pr["url"],
                "card": card and card["url"],
                "pr": key,
            }
    for key, pr in data["prs"].items():
        if key not in asked and (pr["state"] == "open" or pr["state"] == "merged" and data["jobs"][pr["job"]]["state"] not in state.FINISHED_JOB_STATES):
            asked[key] = {"kind": pr["state"], "title": view.pr_title(pr), "job": pr["job"], "order": pr["order"], "url": pr["url"], "card": None, "pr": key}
    return asked


def record(data: dict, answer_id: str, words: str, link: str, asked: dict[str, dict], changes: set[str], bound: dict[str, list[str]], moment: str) -> dict:
    """Keep a new answer of his beside the cards asking him, parts undelivered and PRs not yet live that it could change, none marked yet: those of the job and order of the card it answers, each part a job of the card it answers delivers, the one he tapped or wrote under, still open or not, those its words name, an order included, each other question card in changes, which Jev read it as deciding, ruling options out of or making moot, whatever job, order or words it shares with the card he answered, and every card, part and PR of each order in bound, and the order itself, which a pick of his that card asked him to lift binds; and beside each job waiting on an order any of those belongs to, and each running builder whose own card he answered, which builds that card's recommended option while he decides, each marked heard only when the text its worker started on carries his words, as hear_start reads it. The card he tapped and the question card he wrote under are left out, since his answer ends them, but a PR whose card thread he wrote in is not, since his words end no PR card."""
    answered = [entry for entry in asked.values() if entry["card"] and under(link, entry["card"])]
    drawn = {job_id: job["order"] for job_id, job in data["jobs"].items() for question in job["questions"] if ended_at(link, question)}
    jobs = {entry["job"] for entry in answered if entry["job"]} | drawn.keys()
    orders = {entry["order"] for entry in answered if entry["order"]} | set(drawn.values())
    cards = {
        key: None
        for key, entry in asked.items()
        if not answers(link, entry)
        and (
            entry["kind"] != "order" and (entry["job"] in jobs or jobs & set(entry.get("jobs", ())) or entry["order"] in orders)
            or entry["order"] in bound
            or named(words, key, entry)
            or key in changes
        )
    }
    touched = orders | {asked[key]["order"] for key in cards if asked[key]["order"]}
    kept = data["answers"][answer_id] = {
        "words": words,
        "link": link,
        "at": moment,
        "said": moment,
        "cards": cards,
        "jobs": {
            job_id: None
            for job_id, job in data["jobs"].items()
            if job["state"] == "waiting" and touched & {job["order"], *job["carries"]} or job_id in drawn and job["kind"] == "builder" and job["state"] == "running"
        },
    }
    for job_id in kept["jobs"]:
        hear_start(data, job_id)
    return kept


def binding(data: dict, link: str, asked: dict[str, dict], cwd: str) -> dict[str, list[str]]:
    """Each open order bound by a pick of his that a question his message answers asked him to lift, as `drive job ask --unsettled "<id>: <why>"` keeps it, with the picks binding it: an order whose card, part, PR or waiting job the pick's own drive answer listed, one whose job's question the pick ended, and one whose card, part, PR or own key the pick's cc-notes answer names, as `named` reads his words. A pick ccn cannot read binds only by the drive's records."""
    picks = sorted({pick for job in data["jobs"].values() for question in job["questions"] if question["unsettled"] and ended_at(link, question) for pick in PICK.findall(question["unsettled"])})
    bound: dict[str, list[str]] = {}
    for pick in picks:
        for order_id in sorted(held_by(data, pick, asked) | noted(pick, asked, cwd)):
            if data["orders"][order_id]["state"] not in state.CLOSED_ORDER_STATES:
                bound.setdefault(order_id, []).append(pick)
    return bound


def held_by(data: dict, pick: str, asked: dict[str, dict]) -> set[str]:
    """The orders the drive's own records tie a pick of his to: each one its drive answer listed a card, part or PR of, or a waiting job on, and each one of a job whose question it ended."""
    kept = [answer for answer_id, answer in data["answers"].items() if answer_id[:7] == pick]
    listed = {asked[key]["order"] for answer in kept for key in answer["cards"] if key in asked and asked[key]["order"]}
    waiting = [data["jobs"][job_id] for answer in kept for job_id in answer["jobs"]]
    ended = [job for job in data["jobs"].values() if any((closed := question["closed"]) and pick in ((closed["answer"] or "")[:7], (closed["ruling"] or "")[:7]) for question in job["questions"])]
    return listed | {order_id for job in [*waiting, *ended] for order_id in (job["order"], *job["carries"])}


def noted(pick: str, asked: dict[str, dict], cwd: str) -> set[str]:
    """The orders whose card, part, PR or own key a pick's cc-notes answer names, its Notes included, which say what it binds."""
    try:
        body = settled.shown(pick, cwd)["body"]
    except DriveError as error:
        print(f"his pick {pick} could not be read from cc-notes, so only the drive's records say which orders it binds: {error}", file=sys.stderr)
        return set()
    return {entry["order"] for key, entry in asked.items() if entry["order"] and named(body, key, entry)}


def shared(title: str, other: str, common: set[str]) -> list[str]:
    """The proper terms two questions share when they ask about one subject in other words, as three jobs asked about one search worker's cache disk, but for the drive's common ones; fewer than SUBJECT_TERMS shared is no subject in common."""
    terms = sorted(settled.rare(title) & settled.rare(other) - common)
    return terms if len(terms) >= SUBJECT_TERMS else []


def generic(data: dict) -> set[str]:
    """The terms more than GENERIC_TITLES of the drive's questions use, such as drive, install and command on a drive about its own tooling, which name no one subject."""
    used = Counter(term for job in data["jobs"].values() for question in job["questions"] for term in settled.rare(question["title"] or ""))
    return {term for term, count in used.items() if count > GENERIC_TITLES}


def named(words: str, key: str, entry: dict) -> bool:
    """Whether his words name this card or PR whole, by its key, its link, its card's link or its PR's link."""
    refs = (key, entry["url"], entry["card"], entry.get("pr") and state.pr_url(entry["pr"]))
    return any(ref and re.search(rf"(?<!\w){re.escape(ref)}(?!\w)", words) for ref in refs)


def markable(kept: dict, asked: dict[str, dict]) -> list[str]:
    """What an answer may still mark: each card, part or PR still asking him that it has not marked, but the card it answers."""
    return [key for key, entry in asked.items() if kept["cards"].get(key) is None and not answers(kept["link"], entry)]


def answers(link: str, entry: dict) -> bool:
    """Whether his message at this link answers this entry's card, so it needs no mark: his tap on the card itself, or his words under a question card, which his answer ends; his words under a PR card end nothing, so that PR still asks for one."""
    return bool(entry["card"]) and (view.message_key(link) == view.message_key(entry["card"]) or entry["kind"] == "question" and under(link, entry["card"]))


def ended_at(link: str, question: dict) -> bool:
    """Whether his message at this link answers this question: it is the question's card or sits under it, or the `record` tool ended the question with it, as it does for a question the record names by waits id, card thread or seq wherever his words sit, such as under the card of the PR the question holds; every prompt he typed into a terminal shares the one link `terminal`, so it ends no question by its link."""
    return bool(question["card"]) and under(link, question["card"]) or link != TERMINAL and (question["closed"] or {}).get("link") == link


def under(link: str, card: str) -> bool:
    """Whether his message at this link is the card itself or a reply in the card's own thread, so it answers that card."""
    return (view.card_thread(link) or "").replace(".", "") == view.message_key(card)


def answer_cards(data: dict, answer_id: str, kept: dict) -> list[tuple[str, dict]]:
    """End as answered by his answer each open question whose card his answer is on or under, in the write the caller saves; the questions it ended, for their cards to follow."""
    return [
        ended
        for job_id, job in data["jobs"].items()
        for question in view.open_questions(job)
        if question["card"] and under(kept["link"], question["card"])
        for ended in end(data, job_id, question, state.new_close("answered", kept["words"], kept["link"], answer=answer_id))
    ]


def answer_card(card: str, words: str, link: str, settled: bool) -> dict:
    """Close his question card in place showing his words and a link to them with no buttons, so a late tap records nothing: settled by them as his written-in answer, or withdrawn for them; every route that closes a question card goes through this one call. Words he typed into a terminal have no permalink, so the card links itself."""
    return slack.cc_slack("card-answer", "--card", card, "--label", words, *(("--settled", "--other") if settled else ("--withdrawn",)), "--via", card if link == TERMINAL else link)


def close_card(card: str, closed: dict) -> dict:
    """Show on his question card how the question it asks ended, his words and a link to them with no buttons: settled for his answer, withdrawn for anything else."""
    words = closed["words"] or f"settled by his answer {closed['answer'] or closed['ruling'] or closed['seq']}"
    return answer_card(card, words, closed["link"] or card, settled=closed["how"] == "answered")


def end(data: dict, job_id: str, question: dict, closed: dict) -> list[tuple[str, dict]]:
    """End a job's open question as closed records, with each other open question of the job asking it in the same words superseded by it, and each question a `Replaces:` line folded into it, of any job, ended the same way, since it asked that one too, in the write the caller saves: the one way a question stops asking him, which its card then follows. The questions it ended, by their job."""
    job = data["jobs"][job_id]
    twins = [other for other in view.open_questions(job) if other is not question and state.same_question(other["title"], question["title"])]
    question["closed"] = closed
    for twin in twins:
        twin["closed"] = superseded_by(closed, question)
    job["updated"] = state.stamp()
    replaced = [(asker, other) for asker, each in data["jobs"].items() for other in view.open_questions(each) if question["file"] and other["replaced"] == question["file"]]
    return [(job_id, question), *((job_id, twin) for twin in twins), *(ended for asker, other in replaced for ended in end(data, asker, other, closed))]


def superseded_by(closed: dict, question: dict) -> dict:
    """How a question ends that his answer to another question supersedes: his words and their link, naming the question he answered."""
    return closed | {"how": "superseded", "by": question["waits"] or question["file"]}


def decided_by(data: dict, job_id: str, question: dict, words: str, found: dict | None) -> list[dict]:
    """The job's other open questions, asked before this one, that his answer to it decides or makes moot as Jev reads it at the 0.8 bar, so his answer on a job's newer question ends the older ones it settles; none when Jev gives no reading, which leaves them asking him."""
    asked = state.parse_time(question["asked"])
    older = [other for other in view.open_questions(data["jobs"][job_id]) if other is not question and state.parse_time(other["asked"]) <= asked]
    if not older:
        return []
    shown = {card["card"].get("waits"): card["options"] for card in (found or view.NO_CARDS)["questions"]}
    entries = {f"{job_id}:q{index}": {"kind": "question", "title": other["title"] or "", "options": shown.get(other["waits"]) or []} for index, other in enumerate(older)}
    try:
        keys = settled.changed(question["title"] or "", words, entries, settled.SUPERSEDES_QUESTION, settled.DECIDES)
    except state.DriveError as error:
        print(f"his answer to {question['waits'] or question['file']} ends none of {job_id}'s older questions: {error}", file=sys.stderr)
        return []
    return [older[int(key.rpartition(":q")[2])] for key in sorted(keys)]


def follow(name: str, ended: list[tuple[str, dict]]) -> list[str]:
    """Show on each card drawn for a question that ended how it ended, his words and a link to them with no buttons, so it no longer asks him: settled for his answer, withdrawn for his other words, a withdraw or an answer superseding it. A card cc-slack does not close now the watch closes on its next pass. What was done, a line each."""
    found, _ = view.stored_cards(name)
    done = []
    for _, question in ended:
        closed = question["closed"]
        drawn = {question["card"], *(card["url"] for card in found["questions"] if question["waits"] and card["card"].get("waits") == question["waits"])} - {None}
        for card in drawn:
            try:
                result = close_card(card, closed)
            except slack.SlackError as error:
                done.append(f"{card}: cc-slack did not close it ({error}); the watch closes it on its next pass")
                continue
            if not result.get("already_answered"):
                done.append(f"{question['waits'] or card}: his card {card} now shows his words with no buttons, its question {closed['how']}")
    return done


def closing(data: dict, kind: str, attrs: dict, topic: str | None, answered: list[str], link: str | None) -> list[tuple[str, dict]]:
    """The job questions an owner or answer record of his words is on, open or ended: each its `waits` or `thread_card` names, the seq its waits id ends in that it answers by `re`, or its `card:<thread_ts>` topic is drawn in, and each whose card his message sits under, whatever thread the record names: his answer is on each, and his owner words only on a question whose work they are about, the drive's for a drive tooling order and his work for any other, so his feedback on the drive under a product card leaves it asking; his feedback on a post or card the drive made, a `Drive tooling: post feedback:` order, and an Undo, which reopens its card, are on none. His words never close a PR card: only his order on its thread, or a job carrying one, withdraws it."""
    if kind not in ("owner", "answer") or "undone" in attrs or feedback(data, attrs.get("order")):
        return []
    return [
        (job_id, question)
        for job_id, job in data["jobs"].items()
        if kind == "answer" or (attrs.get("about") == state.ABOUT_DRIVE) == (data["orders"][job["order"]]["owner"] == "meta")
        for question in job["questions"]
        if (waits := question["waits"]) and (waits in (attrs.get("thread_card"), attrs.get("waits")) or waits.rpartition("-")[2] in answered)
        or (card := question["card"]) and (topic == f"card:{view.card_thread(card)}" or link and under(link, card))
    ]



def feedback(data: dict, order: str | dict | None) -> bool:
    """Whether a record's order, the id of an order or the fields of the new one it adds, is his feedback on a post or card the drive made, which faults how the drive asked and never ends what it asked."""
    if isinstance(order, dict):
        return order.get("words", "").startswith(state.POST_FEEDBACK)
    return order in data["orders"] and data["orders"][order]["text"].startswith(state.POST_FEEDBACK)

def unclosed_refusal(names: list[str], kind: str, attrs: dict, topic: str | None, answered: list[str], link: str | None) -> str | None:
    """Why an owner or answer record goes only through the drive's `record` tool: his words in it end an open question of his, and only that tool ends it, and its card, in the call that sends them."""
    for name in names:
        if ended := [question for _, question in closing(state.load(name), kind, attrs, topic, answered, link) if question["closed"] is None]:
            return (
                f"refused: this {kind} record carries his words on his open question \"{cut(ended[0]['title'] or '', 80)}\", which keeps"
                " asking him until something ends it; send it with the drive's `record` tool, his message's permalink as `url`,"
                " which ends that question and closes its card in the same call. His words: \"So then why"
                ' didn\'t you withdraw this card? / Why do I have to keep telling you?"'
            )
    return None


def said_on(data: dict, card: str | None) -> list[dict]:
    """His answers recorded as his pick on this card or his words under it, oldest first."""
    return [answer for answer in data["answers"].values() if card and under(answer["link"], card)]


def redrawn(data: dict, job_id: str, waits: str) -> list[dict]:
    """The questions `drive job ask <job> --redraws <waits>` takes the place of: each holding the card under that waits id, of the job or of any other job on one of its orders, but one a `Replaces:` line folded into another, open, or withdrawn with no answer of his under the card, so a job carrying on another's work asks again in new words on that job's card, or on a card his words withdrew, and redraws it where it sits. Refused naming why for a card whose question his answer ended, or any answer of his superseded, and for a waits id no question of those orders holds."""
    job = data["jobs"][job_id]
    orders = [job["order"], *job["carries"]]
    held = [(each["order"], question) for each in data["jobs"].values() for question in each["questions"] if question["waits"] == waits and not question["replaced"]]
    if not (mine := [question for order_id, question in held if order_id in orders]):
        other = f", which asks for {', '.join(sorted({order_id for order_id, _ in held}))}" if held else ""
        raise DriveError(
            f"--redraws {waits}: no question of {', '.join(orders)} holds that card{other}, so {job_id} takes over none:"
            f" drive status {orders[0]} lists each question of its jobs with its card's waits id"
        )
    if ended := next((question for question in mine if question["closed"] and question["closed"]["how"] != "withdrawn"), None):
        closed = ended["closed"]
        raise DriveError(
            f'--redraws {waits}: its question there ended {closed["how"]} {when(closed["at"])}: "{cut(closed["words"] or "", 200)}" ({closed["link"] or ended["card"]}),'
            " so that card asks him nothing more: ask what is still open on a card of its own, under its own waits id"
        )
    if said := next((answer for question in mine if question["closed"] for answer in said_on(data, question["card"])), None):
        raise DriveError(
            f'--redraws {waits}: his words under that card answered it: "{cut(said["words"], 200)}" ({said["link"]}), so that card asks him'
            " nothing more: ask what they leave open on a card of its own, under its own waits id"
        )
    return mine


def redraw_refusal(data: dict, job: dict, waits: str, file: str) -> str | None:
    """Why this card may not be redrawn with another question: his tap, words or answer on it already answered the question drawn there."""
    drawn = next((question for question in job["questions"] if question["waits"] == waits and question["card"] and question["file"] != file), None)
    if not drawn or (closed := drawn["closed"] or {}).get("how") != "answered":
        return None
    return (
        f'{waits}: his answer on that card closed its question, {when(closed["at"])}: "{cut(closed["words"] or "", 200)}" ({closed["link"] or drawn["card"]}).'
        " A direction he gives under a card is his answer: the card stays settled showing his words, and the work goes ahead"
        f" on them. Ask only what they leave open, on a card of its own drawn under its own waits id, never {waits}"
    )


def unmarked(kept: dict, asked: dict[str, dict]) -> list[str]:
    """The cards, parts and PRs an answer has not marked that still ask him, are undelivered or are not yet live."""
    return [key for key, mark in kept["cards"].items() if mark is None and key in asked]


def unheard(data: dict, kept: dict) -> list[str]:
    """The jobs waiting on an order this answer touches when it came, still live, with an agent or a lane his words can reach, that are not marked sent his words or untouched by them; a job with neither hears them from the launch that starts it, which carries them."""
    return [
        job_id
        for job_id, mark in kept["jobs"].items()
        if mark is None and (job := data["jobs"][job_id])["state"] not in state.FINISHED_JOB_STATES and (job.get("agent") or job.get("dispatch"))
    ]


def delivery(data: dict, job_id: str, job: dict) -> str:
    """How his words reach the job by a delivery that marks it sent them: a SendMessage from the coordinator session whose agent runs it on this Mac, which drive answer --sent records since the drive cannot see it; drive job resume for a job any other worker runs or ran, which carries them to its lane or its next worker; drive job launch for a job no worker runs yet, which carries them to its first worker."""
    if lane := state.agent_lane(data, job):
        return f"SendMessage it his words verbatim from {lane}@{data['name']}, the session {job['session']} that started its agent {job['agent']}"
    if job.get("agent") or job.get("dispatch"):
        return f"drive job resume {job_id} with his words verbatim"
    return f"drive job launch {job_id} on its FREE line, which carries his words to its first worker"


def hear(data: dict, job_id: str, text: str, written: datetime) -> None:
    """Mark the job sent each answer of his it has not heard whose words the text delivered to its worker holds, a message sent to it or the brief, handoff or launch text it started on, written once he gave that answer: a text written before it, such as a brief quoting a card's option ahead of his tap, carried words he had not said yet."""
    for answer_id, kept in owed(data, job_id):
        if carries(text, kept["words"]) and given(data, answer_id) <= written:
            heard(data, job_id, kept, "sent")


def hear_start(data: dict, job_id: str) -> None:
    """Hear the text the job's worker started on, as it was written then: the handoff its relaunch or takeover kept, else the brief its launch or agent started on; a job no agent or lane started hears his words from the launch that starts it."""
    job = data["jobs"][job_id]
    if (job.get("agent") or job.get("dispatch")) and (path := job.get("handoff") or job.get("brief")):
        started = Path(path)
        hear(data, job_id, started.read_text(), datetime.fromtimestamp(started.stat().st_mtime).astimezone())


def heard(data: dict, job_id: str, kept: dict, mark: str) -> None:
    """Mark the job sent this answer of his or untouched by it, taking the copy of it the job holds off: the one way a job stops owing his words."""
    kept["jobs"][job_id] = {"mark": mark}
    unhold(data["jobs"][job_id], kept["words"])


def owe(data: dict, answer_id: str, job_id: str) -> None:
    """List the job as waiting to hear his answer, unless the text its worker started on already carried it."""
    if job_id not in (kept := data["answers"][answer_id])["jobs"]:
        kept["jobs"][job_id] = None
        hear_start(data, job_id)


def given(data: dict, answer_id: str) -> datetime:
    """When he gave this answer, as the PR check dates it: by the close of the drive's question it answered, his tap's own time for a tap, else when the drive recorded it."""
    return served.given(served.drawn(data, "answer", answer_id), state.parse_time(data["answers"][answer_id]["at"]))


def unhold(job: dict, words: str) -> None:
    """Take off the job each text it holds quoting these words of his verbatim, once it heard them or his Undo took them back."""
    job["undelivered"] = [kept for kept in job["undelivered"] if not carries(kept["words"], f'verbatim: "{words}"')]


def owed(data: dict, job_id: str) -> list[tuple[str, dict]]:
    """Each answer of his that lists the job as waiting to hear it, unmarked, by its id."""
    return [(answer_id, kept) for answer_id, kept in data["answers"].items() if job_id in kept["jobs"] and kept["jobs"][job_id] is None]


def carries(text: str, words: str) -> bool:
    """Whether the text holds his words verbatim, spacing aside."""
    return " ".join(words.split()) in " ".join(text.split())


def counted(keys: list[str], asked: dict[str, dict]) -> str:
    """How many cards, orders, parts and PRs these keys name, as `2 cards and 3 PRs` or `1 card, 1 order, 2 parts and 3 PRs`."""
    kinds = Counter("card" if asked[key]["kind"] == "question" else asked[key]["kind"] if asked[key]["kind"] in ("order", "part") else "PR" for key in keys)
    named = [f"{count} {word if count == 1 else word + 's'}" for word in ("card", "order", "part", "PR") if (count := kinds[word])]
    return f"{', '.join(named[:-1])} and {named[-1]}" if len(named) > 1 else "".join(named)


def listing(answer_id: str, kept: dict, keys: list[str], asked: dict[str, dict], data: dict) -> str:
    rows = [
        f"  {key}  {entry['kind']}  {entry['job'] or ', '.join(entry.get('jobs', ())) or 'no job'} {entry['order'] or 'no order'}  {cut(entry['title'], 100)}  {entry['url']}".rstrip()
        for key in keys
        for entry in [asked[key]]
    ]
    cards = [
        (
            f'his answer {answer_id} "{cut(kept["words"], 80)}" stays unfinished until each card asking him, part undelivered and PR not yet'
            f" live when it came is marked standing, settled, dropped, superseded or reopened by it; {counted(keys, asked)}"
            f" {'has' if len(keys) == 1 else 'have'} no mark:"
        ),
        *rows,
        (
            f"mark each: drive answer {answer_id} --stands <key>... --dropped <key>... for a card, order, part or open PR his words remove, which"
            " withdraws a card without stopping its job, drops an order and stops each live job whose order it is, takes a part off its order"
            " and jobs, and closes a PR, --settled <card key>... for a card it"
            " decides, or <card key>=<the ASK its job keeps with drive job ask --redraws <card key> asking only what it leaves open>... for one"
            " it decides in part, --superseded"
            " <key>=stop|<key>=<live job to fold it into>... --reopened <PR key>=<live job that reworks it to his words>..., or"
            f" --others-stand once each it drops, settles, supersedes or reopens is marked and at most {OTHERS_STAND} are left"
        ),
    ]
    jobs = [
        f"  {job_id} waits on {', '.join([job['order'], *job['carries']])} and has not heard {answer_id}: "
        + delivery(data, job_id, job)
        + ", or mark it untouched"
        for job_id in unheard(data, kept)
        for job in [data["jobs"][job_id]]
    ]
    told = [
        f'his answer {answer_id} "{cut(kept["words"], 80)}" stays unfinished until each job waiting on an order it touches has heard it:',
        *jobs,
        (
            f"mark each: drive answer {answer_id} --sent <job>... once SendMessage gave it his words verbatim, or --untouched"
            " <job>... for one his words leave as it is; any other job is marked sent by the drive job resume, brief or handoff that"
            " carries his words verbatim to its worker, and by nothing else"
        ),
    ]
    return "\n".join([*(cards if keys else []), *(told if jobs else [])])


def others_refusal(answer_id: str, kept: dict, asked: dict[str, dict], named: list[str], data: dict) -> str | None:
    """Why --others-stand may not mark the rest standing: more are left than anyone reads against his words in one call."""
    if len(rest := [key for key in unmarked(kept, asked) if key not in named]) <= OTHERS_STAND:
        return None
    return (
        f"--others-stand would mark {counted(rest, asked)} standing at once, unread; it marks at most {OTHERS_STAND}. Read each"
        " card's question, part and PR's body against his words and mark each by its key: --stands <key>... for one his words"
        " leave as it is, --dropped for one they remove, --superseded or --reopened for one they replace or change\n"
        f"{listing(answer_id, kept, rest, asked, data)}"
    )


def settle(card: str, kept: dict, settled: bool = False) -> None:
    """Close a card in place, showing his words and a link to them with no buttons, so a late tap records nothing: withdrawn for a superseded card, settled for one his answer decides."""
    if (result := answer_card(card, kept["words"], kept["link"], settled=settled)).get("already_answered"):
        raise DriveError(f"{card} already shows his answer {result['label']!r}, so it no longer asks him and needs no mark; the drive's cards are reread now, run this again")


def settled_refusal(data: dict, kept: dict, key: str, entry: dict, file: Path | None) -> str | None:
    """Why his answer may not settle this card: it is no question card a job asked, or the ASK it is redrawn to is not one its job keeps drawn on no card, unrefused and unsettled, linking his answer where the card shows it."""
    if entry["kind"] != "question" or not entry["job"]:
        return f"{key} is no question card a job of this drive asked, so --settled cannot mark it; --superseded {key}=stop withdraws it"
    if file is None:
        return None
    job_id = entry["job"]
    question = state.kept_in(data["jobs"][job_id], file)
    if not question or question["refused"] or question["closed"] or question["waits"] != key:
        return (
            f"{file} keeps no open question of {job_id} redrawing {key}: keep what his answer leaves {key} asking with"
            f" drive job ask {job_id} <file> --redraws {key}, then mark {key}=<the path it prints>"
        )
    if f"/p{view.message_key(kept['link'])}" not in limits.FINDINGS.split(file.read_text())[0]:
        return (
            f"{file} does not link his answer {kept['link']} above its Findings:, so the redrawn card would not say which words"
            f" of his narrowed it: link them in its summary, keep it again with drive job ask {job_id} <file> --redraws {key}, and mark {key}=<the path it prints>"
        )
    return None


def stack_refusal(asked: dict[str, dict], keys: list[str]) -> str | None:
    """Why superseding or dropping these cards would settle a stack card under a PR left out of them."""
    for key in keys:
        if not (card := asked[key]["card"]):
            continue
        if left := [other for other, entry in asked.items() if entry["card"] == card and other not in keys]:
            return f"{key} shares its card {card} with {', '.join(left)}, and settling it settles theirs: mark them superseded or dropped together"
    return None


def dropped_refusal(asked: dict[str, dict], keys: list[str]) -> str | None:
    """Why his answer may not drop these, before anything is dropped: a merged PR comes out only by a revert, which lands on his tap alone."""
    if merged := [key for key in keys if asked[key]["kind"] == "merged"]:
        return (
            f"{', '.join(merged)} merged, so taking it out is a revert, which lands only on his tap on a card stating the bug in it:"
            " mark it --stands, or --reopened <key>=<the live job that reworks it>"
        )
    return None


def whole(data: dict, asked: dict[str, dict], keys: list[str]) -> dict[str, list[str]]:
    """The orders these --dropped keys drop whole, each with its keys among them: an order named by its own key, and one whose every part they name."""
    held: dict[str, list[str]] = {}
    for key in keys:
        if asked[key]["kind"] in ("order", "part"):
            held.setdefault(asked[key]["order"], []).append(key)
    return {
        order_id: listed
        for order_id, listed in held.items()
        if asked[listed[0]]["kind"] == "order" or set(data["orders"][order_id]["parts"]) <= {asked[key]["title"] for key in listed}
    }


def part_refusal(asked: dict[str, dict], keys: list[str]) -> str | None:
    """Why an order or part may not be superseded or reopened: it stands or is dropped."""
    if held := [key for key in keys if asked[key]["kind"] in ("order", "part")]:
        return f"{', '.join(held)}: an order or part stands or is dropped, so mark it --stands or --dropped"
    return None


def fold_refusal(data: dict, key: str, entry: dict, into: str) -> str | None:
    """Why the superseded card's job cannot fold into the job named, before anything is settled."""
    if not entry["job"]:
        return f"{key} has no job to fold into {into}; mark it {key}=stop"
    if into == entry["job"]:
        return f"{key} is {into}'s own card, so {into} cannot carry it on; name another live job, or mark it {key}=stop"
    if into not in data["jobs"] or data["jobs"][into]["state"] in state.FINISHED_JOB_STATES:
        return f"{into} is not a live job of this drive; name one that is running, waiting or built"
    return None


def reopen_refusal(data: dict, key: str, entry: dict, by: str) -> str | None:
    """Why this cannot be reopened for the job named to rework, before anything is settled."""
    if entry["kind"] == "question":
        return f"{key} is a question card, which stands or is superseded; only a PR is reopened"
    if by not in data["jobs"] or data["jobs"][by]["state"] in state.FINISHED_JOB_STATES:
        return f"{by} is not a live job of this drive; start the job that reworks {key} to his words, then name it"
    return None


def stops_open_pr(data: dict, key: str, into: str | None) -> bool:
    """Whether superseding this stops an open PR outright, folding it into no live job, so it closes on GitHub."""
    return not into and key in data["prs"] and data["prs"][key]["state"] == "open"


def drop(data: dict, answer_id: str, order_id: str, part: str) -> list[str]:
    """Take a part his answer removes off its order, every job delivering it and every PR naming it, stopping each live job it leaves with no part and keeping each other live job it trims waiting to hear his words; what was done, a line each."""
    del data["orders"][order_id]["parts"][part]
    data["orders"][order_id]["updated"] = state.stamp()
    trimmed, stopped = [], []
    for job_id, job in data["jobs"].items():
        if part not in (held := state.job_parts(job, order_id)):
            continue
        held.remove(part)
        if job["order"] != order_id and not held:
            del job["carries"][order_id]
        job["updated"] = state.stamp()
        trimmed.append(job_id)
        if job["state"] in state.FINISHED_JOB_STATES:
            continue
        if job["parts"] or job["carries"]:
            owe(data, answer_id, job_id)
            continue
        job.update(state="stopped", said=f"his answer {answer_id} dropped every part it delivered")
        stopped.append(f"{job_id} stopped, left with no part: end its agent, or its lane with drive job end {job_id}")
    for pr in data["prs"].values():
        if pr["order"] == order_id and part in pr["parts"]:
            pr["parts"].remove(part)
    return [f"{order_id}:{part} dropped from {order_id}" + (f" and {', '.join(trimmed)}" if trimmed else ""), *stopped]


def drop_order(data: dict, answer_id: str, order_id: str) -> list[str]:
    """Drop an order his answer removes whole, as drive order set <o> --state dropped --withdrawn <his link> does, stopping each live job whose order it is and taking it off each live job carrying it, which keeps waiting to hear his words; what was done, a line each."""
    kept = data["answers"][answer_id]
    order = data["orders"][order_id]
    order.update(state="dropped", withdrawn=kept["link"], updated=state.stamp())
    done = [f"{order_id} dropped by his answer {answer_id}"]
    for job_id in order["jobs"]:
        job = data["jobs"][job_id]
        if job["state"] in state.FINISHED_JOB_STATES:
            continue
        if job["order"] != order_id:
            if order_id in job["carries"]:
                del job["carries"][order_id]
                job["updated"] = state.stamp()
                owe(data, answer_id, job_id)
            continue
        job.update(state="stopped", said=f"his answer {answer_id} dropped its order {order_id}", updated=state.stamp())
        done.append(f"{job_id} stopped with its order: end its agent, or its lane with drive job end {job_id}")
    return done


def close(key: str) -> None:
    """Close on GitHub a PR his answer superseded outright."""
    try:
        github.close(state.pr_url(key))
    except github.GithubError as error:
        raise DriveError(f"{key}: could not close it on GitHub, so it is not marked superseded yet: {error}") from None


def unasked(data: dict, key: str, entry: dict, into: str | None = None) -> list[str]:
    """Drop a superseded or dropped PR's tap ask, and keep an open PR stopped outright as closed, once closed on GitHub; what was done, a line each."""
    done = []
    if entry["kind"] == "tap":
        data["prs"][key].update(wants_tap=False, updated=state.stamp())
        done.append(f"{key} no longer waits on his tap")
    if stops_open_pr(data, key, into):
        data["prs"][key].update(state="closed", updated=state.stamp())
        done.append(f"{key} closed on GitHub")
    return done


def supersede(data: dict, answer_id: str, key: str, entry: dict, into: str | None) -> list[str]:
    """Stop the superseded card's job, or fold what it carries and each open PR it answers for into a live job, so every wake, check and lander of those PRs reaches that job's lane and none relaunches the one stopped; drop its PR's tap ask, and keep an open PR stopped outright as closed, once closed on GitHub; what was done, a line each."""
    done = unasked(data, key, entry, into)
    if not (job_id := entry["job"]):
        return done
    job = data["jobs"][job_id]
    if into:
        target = data["jobs"][into]
        for order_id, parts in {job["order"]: job["parts"], **job["carries"]}.items():
            if order_id == target["order"]:
                target["parts"] = list(dict.fromkeys([*target["parts"], *parts]))
            else:
                state.carry(data, into, order_id, parts)
        done.append(f"{job_id}'s parts now ride on {into}")
        for moved in [held for held, pr in data["prs"].items() if pr["job"] == job_id and pr["state"] == "open"]:
            state.take_pr(data, into, moved)
            done.append(f"{moved} now answers to {into}, which its wakes, checks and lander reach")
    if job["state"] not in state.FINISHED_JOB_STATES:
        job.update(state="stopped", said=f"superseded by his answer {answer_id}" + (f", folded into {into}" if into else ""), updated=state.stamp())
        done.append(f"{job_id} stopped: end its agent, or its lane with drive job end {job_id}")
    return done
