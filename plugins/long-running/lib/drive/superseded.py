"""What each answer or ruling of his leaves standing: the cards open and PRs not yet live when it was recorded that it could change, until each is marked standing, settled, superseded or reopened by it, and the jobs waiting on the orders it touches, until each has heard it or is marked untouched."""

import re
import sys
from pathlib import Path

from drive import github, limits, settled, slack, state, view
from drive.render import cut, when
from drive.state import DriveError

OTHERS_STAND = 3
SUBJECT_TERMS = 3


def asking(data: dict, found: dict) -> dict[str, dict]:
    """Every card asking him and every PR not yet live, by the key a mark names it with: the card of each job's open question by its waits id, then each open question card cc-slack shows that no job asked, each PR waiting on his tap, then each other open PR and each merged one whose job is still at work, by its PR key."""
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


def record(data: dict, words: str, link: str, asked: dict[str, dict], changes: set[str], moment: str) -> dict:
    """A new answer of his beside the cards asking him and PRs not yet live that it could change, none marked yet: those of the job and order of the card it answers, the one he tapped or wrote under, still open or not, those its words name, and each other question card in changes, which Jev read it as deciding, ruling options out of or making moot, whatever job, order or words it shares with the card he answered; and beside each job waiting on an order any of those belongs to, none marked heard yet."""
    answered = [entry for entry in asked.values() if entry["card"] and under(link, entry["card"])]
    drawn = {job_id: job["order"] for job_id, job in data["jobs"].items() for question in job["questions"] if question["card"] and under(link, question["card"])}
    jobs = {entry["job"] for entry in answered if entry["job"]} | drawn.keys()
    orders = {entry["order"] for entry in answered if entry["order"]} | set(drawn.values())
    cards = {
        key: None
        for key, entry in asked.items()
        if not (entry["card"] and under(link, entry["card"])) and (entry["job"] in jobs or entry["order"] in orders or named(words, key, entry) or key in changes)
    }
    touched = orders | {asked[key]["order"] for key in cards if asked[key]["order"]}
    return {
        "words": words,
        "link": link,
        "at": moment,
        "said": moment,
        "cards": cards,
        "jobs": {job_id: None for job_id, job in data["jobs"].items() if job["state"] == "waiting" and touched & {job["order"], *job["carries"]}},
    }


def shared(title: str, other: str) -> list[str]:
    """The proper terms two questions share when they ask about one subject in other words, as three jobs asked about one search worker's cache disk; fewer than SUBJECT_TERMS shared is no subject in common."""
    terms = sorted(settled.rare(title) & settled.rare(other))
    return terms if len(terms) >= SUBJECT_TERMS else []


def named(words: str, key: str, entry: dict) -> bool:
    """Whether his words name this card or PR whole, by its key, its link, its card's link or its PR's link."""
    refs = (key, entry["url"], entry["card"], entry.get("pr") and state.pr_url(entry["pr"]))
    return any(ref and re.search(rf"{re.escape(ref)}(?!\w)", words) for ref in refs)


def markable(kept: dict, asked: dict[str, dict]) -> list[str]:
    """What an answer may still mark: each card or PR still asking him that it has not marked, but the card it answers."""
    return [key for key, entry in asked.items() if kept["cards"].get(key) is None and not (entry["card"] and under(kept["link"], entry["card"]))]


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
    """Close his question card in place showing his words and a link to them with no buttons, so a late tap records nothing: settled by them as his written-in answer, or withdrawn for them; every route that closes a question card goes through this one call."""
    return slack.cc_slack("card-answer", "--card", card, "--label", words, *(("--settled", "--other") if settled else ("--withdrawn",)), "--via", link)


def close_card(card: str, closed: dict) -> dict:
    """Show on his question card how the question it asks ended, his words and a link to them with no buttons: settled for his answer, withdrawn for anything else."""
    words = closed["words"] or f"settled by his answer {closed['answer'] or closed['ruling'] or closed['seq']}"
    return answer_card(card, words, closed["link"] or card, settled=closed["how"] == "answered")


def end(data: dict, job_id: str, question: dict, closed: dict) -> list[tuple[str, dict]]:
    """End a job's open question as closed records, with each other open question of the job asking it in the same words superseded by it, in the write the caller saves: the one way a question stops asking him, which its card then follows. The questions it ended, by their job."""
    job = data["jobs"][job_id]
    twins = [other for other in view.open_questions(job) if other is not question and state.same_question(other["title"], question["title"])]
    question["closed"] = closed
    for twin in twins:
        twin["closed"] = superseded_by(closed, question)
    job["updated"] = closed["at"]
    return [(job_id, question), *((job_id, twin) for twin in twins)]


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
    """The job questions an owner or answer record of his words is on, open or ended: each its `waits` or `thread_card` names, the ask seq it answers names or its `card:<thread_ts>` topic is drawn in, and each whose card his message sits under, whatever thread the record names: his answer is on each, and his owner words only on a question whose work they are about, the drive's for a drive tooling order and his work for any other, so his feedback on the drive under a product card leaves it asking; an Undo, which reopens its card, is on none. His words never close a PR card: only his order on its thread, or a job carrying one, withdraws it."""
    if kind not in ("owner", "answer") or "undone" in attrs:
        return []
    return [
        (job_id, question)
        for job_id, job in data["jobs"].items()
        if kind == "answer" or (attrs.get("about") == state.ABOUT_DRIVE) == (data["orders"][job["order"]]["owner"] == "meta")
        for question in job["questions"]
        if (waits := question["waits"]) and (waits == attrs.get("thread_card") or waits.rpartition("-")[2] in answered)
        or (card := question["card"]) and (topic == f"card:{view.card_thread(card)}" or link and under(link, card))
    ]


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


def redrawable(data: dict, title: str) -> list[dict]:
    """Each job's question asking what this title asks that was withdrawn from a card with no answer of his under it: asking it again on any job redraws that card under its waits id."""
    return [
        question
        for job in data["jobs"].values()
        for question in job["questions"]
        if state.same_question(question["title"], title)
        and (question["card"] or question["waits"])
        and (question["closed"] or {}).get("how") == "withdrawn"
        and not said_on(data, question["card"])
    ]


def redraw_refusal(data: dict, job: dict, waits: str, file: str) -> str | None:
    """Why this card may not be redrawn with another question: his tap, words or answer on it already answered the question drawn there."""
    drawn = next((question for question in job["questions"] if question["waits"] == waits and question["card"] and question["file"] != file), None)
    if not drawn or (closed := drawn["closed"] or {}).get("how") != "answered":
        return None
    return (
        f'{waits}: his answer on that card closed its question, {when(closed["at"])}: "{cut(closed["words"] or "", 200)}" ({closed["link"] or drawn["card"]}).'
        " A direction he gives under a card is his answer: the card stays settled showing his words, and the work goes ahead"
        f" on them. Ask only what they leave open, on a card of its own drawn with its own ask record's waits id, never {waits}"
    )


def unmarked(kept: dict, asked: dict[str, dict]) -> list[str]:
    """The cards and PRs an answer has not marked that still ask him or are not yet live."""
    return [key for key, mark in kept["cards"].items() if mark is None and key in asked]


def unheard(data: dict, kept: dict) -> list[str]:
    """The jobs waiting on an order this answer touches when it came, still live, that are not marked sent his words or untouched by them."""
    return [job_id for job_id, mark in kept["jobs"].items() if mark is None and data["jobs"][job_id]["state"] not in state.FINISHED_JOB_STATES]


def counted(keys: list[str], asked: dict[str, dict]) -> str:
    """How many cards and PRs these keys name, as `2 cards and 3 PRs`."""
    cards = sum(asked[key]["kind"] == "question" for key in keys)
    prs = len(keys) - cards
    named = [f"{count} {word if count == 1 else word + 's'}" for count, word in ((cards, "card"), (prs, "PR")) if count]
    return " and ".join(named)


def listing(answer_id: str, kept: dict, keys: list[str], asked: dict[str, dict], data: dict) -> str:
    rows = [
        f"  {key}  {entry['kind']}  {entry['job'] or 'no job'} {entry['order'] or 'no order'}  {cut(entry['title'], 100)}  {entry['url']}"
        for key in keys
        for entry in [asked[key]]
    ]
    cards = [
        (
            f'his answer {answer_id} "{cut(kept["words"], 80)}" stays unfinished until each card asking him and each PR not yet'
            f" live when it came is marked standing, settled, superseded or reopened by it; {counted(keys, asked)}"
            f" {'has' if len(keys) == 1 else 'have'} no mark:"
        ),
        *rows,
        (
            f"mark each: drive answer {answer_id} --stands <key>... --settled <card key>... for a card it decides, or <card key>=<the"
            " ASK its job keeps with drive job ask asking only what it leaves open>... for one it decides in part, --superseded"
            " <key>=stop|<key>=<live job to fold it into>... --reopened <PR key>=<live job that reworks it to his words>..., or"
            f" --others-stand once each it settles, supersedes or reopens is marked and at most {OTHERS_STAND} are left"
        ),
    ]
    jobs = [
        f"  {job_id} waits on {', '.join([job['order'], *job['carries']])} and has not heard {answer_id}: SendMessage it his words verbatim, or mark it untouched"
        for job_id in unheard(data, kept)
        for job in [data["jobs"][job_id]]
    ]
    told = [
        f'his answer {answer_id} "{cut(kept["words"], 80)}" stays unfinished until each job waiting on an order it touches has heard it:',
        *jobs,
        (
            f"mark each: drive answer {answer_id} --sent <job>... once SendMessage gave it his words verbatim, drive job resume for a"
            " remote lane, or --untouched <job>... for one his words leave as it is"
        ),
    ]
    return "\n".join([*(cards if keys else []), *(told if jobs else [])])


def others_refusal(answer_id: str, kept: dict, asked: dict[str, dict], named: list[str], data: dict) -> str | None:
    """Why --others-stand may not mark the rest standing: more are left than anyone reads against his words in one call."""
    if len(rest := [key for key in unmarked(kept, asked) if key not in named]) <= OTHERS_STAND:
        return None
    return (
        f"--others-stand would mark {counted(rest, asked)} standing at once, unread; it marks at most {OTHERS_STAND}. Read each"
        " card's question and each PR's body against his words and mark each by its key: --stands <key>... for one his words"
        " leave as it is, --superseded or --reopened for one they replace or change\n"
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
    if not question or question["refused"] or question["closed"] or question["card"] or question["waits"]:
        return (
            f"{file} keeps no question of {job_id} drawn on no card yet: keep what his answer leaves {key} asking with"
            f" drive job ask {job_id} <file>, then mark {key}=<the path it prints>"
        )
    if f"/p{view.message_key(kept['link'])}" not in limits.FINDINGS.split(file.read_text())[0]:
        return (
            f"{file} does not link his answer {kept['link']} above its Findings:, so the redrawn card would not say which words"
            f" of his narrowed it: link them in its summary, keep it again with drive job ask {job_id} <file>, and mark {key}=<the path it prints>"
        )
    return None


def stack_refusal(asked: dict[str, dict], keys: list[str]) -> str | None:
    """Why superseding these cards would settle a stack card under a PR left out of them."""
    for key in keys:
        if not (card := asked[key]["card"]):
            continue
        if left := [other for other, entry in asked.items() if entry["card"] == card and other not in keys]:
            return f"{key} shares its card {card} with {', '.join(left)}, and settling it settles theirs: mark them superseded together"
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


def close(key: str) -> None:
    """Close on GitHub a PR his answer superseded outright."""
    try:
        github.close(state.pr_url(key))
    except github.GithubError as error:
        raise DriveError(f"{key}: could not close it on GitHub, so it is not marked superseded yet: {error}") from None


def supersede(data: dict, answer_id: str, key: str, entry: dict, into: str | None) -> list[str]:
    """Stop the superseded card's job, or fold what it carries into a live job, drop its PR's tap ask, and keep an open PR stopped outright as closed, once closed on GitHub; what was done, a line each."""
    done = []
    if entry["kind"] == "tap":
        data["prs"][key].update(wants_tap=False, updated=state.stamp())
        done.append(f"{key} no longer waits on his tap")
    if stops_open_pr(data, key, into):
        data["prs"][key].update(state="closed", updated=state.stamp())
        done.append(f"{key} closed on GitHub")
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
    if job["state"] not in state.FINISHED_JOB_STATES:
        job.update(state="stopped", said=f"superseded by his answer {answer_id}" + (f", folded into {into}" if into else ""), updated=state.stamp())
        state.drop_carries(data, job_id)
        done.append(f"{job_id} stopped: end its agent, or its lane with drive job end {job_id}")
    return done
