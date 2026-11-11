"""What each answer or ruling of his leaves standing: the cards open and PRs not yet live when it was recorded that it could replace, until each is marked standing, superseded or reopened by it."""

import re

from drive import slack, state, view
from drive.render import cut, when
from drive.state import DriveError


def asking(data: dict, found: dict) -> dict[str, dict]:
    """Every card asking him and every PR not yet live, by the key a mark names it with: each open question card by its waits id, each PR waiting on his tap, then each other open PR and each merged one whose job is still at work, by its PR key."""
    drawn = {view.message_key(question["card"]): job_id for job_id, job in data["jobs"].items() for question in job["questions"] if question["card"]}
    asked = {}
    for card in found["questions"]:
        key = card["card"].get("waits") or view.message_key(card["url"])
        named = key.partition(":")[2].partition("-")[0]
        job_id = drawn.get(view.message_key(card["url"])) or (named if named in data["jobs"] else None)
        asked[key] = {
            "kind": "question",
            "title": card["question"],
            "job": job_id,
            "order": job_id and data["jobs"][job_id]["order"],
            "url": card["url"],
            "card": card["url"],
        }
    for key, pr in data["prs"].items():
        card = view.card_for(found["pr_cards"], pr["url"])
        if view.waits_on_tap(pr, card):
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


def record(words: str, link: str, asked: dict[str, dict], moment: str) -> dict:
    """A new answer of his beside the cards asking him and PRs not yet live that it could replace, none marked yet: those of the job and order of the card it answers, the one he tapped or wrote under, and those its words name."""
    answered = [entry for entry in asked.values() if entry["card"] and under(link, entry["card"])]
    jobs = {entry["job"] for entry in answered if entry["job"]}
    orders = {entry["order"] for entry in answered if entry["order"]}
    return {
        "words": words,
        "link": link,
        "at": moment,
        "said": moment,
        "cards": {
            key: None
            for key, entry in asked.items()
            if not (entry["card"] and under(link, entry["card"])) and (entry["job"] in jobs or entry["order"] in orders or named(words, key, entry))
        },
    }


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


def said_under(data: dict, job: dict) -> list[dict]:
    """Each answer of his written under a card this job asked on, as `drive job ask`'s settled check lists one."""
    return [
        {"id": answer_id, "title": f"his words under this job's card {question['card']}: {cut(answer['words'], 160)}"}
        for question in job["questions"]
        if question["card"]
        for answer_id, answer in data["answers"].items()
        if under(answer["link"], question["card"])
    ]


def redraw_refusal(data: dict, job: dict, waits: str, file: str) -> str | None:
    """Why this card may not be redrawn with another question: his words under it already answered it."""
    drawn = next((question for question in job["questions"] if question["waits"] == waits and question["card"] and question["file"] != file), None)
    said = [answer for answer in data["answers"].values() if drawn and under(answer["link"], drawn["card"])]
    if not said:
        return None
    answer = said[-1]
    return (
        f'{waits}: his words under that card answer it, {when(answer["at"])}: "{cut(answer["words"], 200)}" ({answer["link"]}).'
        " A direction he gives under a card is his answer: the card stays settled showing his words, and the work goes ahead"
        f" on them. Ask only what they leave open, on a card of its own drawn with its own ask record's waits id, never {waits}"
    )


def unmarked(kept: dict, asked: dict[str, dict]) -> list[str]:
    """The cards and PRs an answer has not marked that still ask him or are not yet live."""
    return [key for key, mark in kept["cards"].items() if mark is None and key in asked]


def counted(keys: list[str], asked: dict[str, dict]) -> str:
    """How many cards and PRs these keys name, as `2 cards and 3 PRs`."""
    cards = sum(asked[key]["kind"] == "question" for key in keys)
    prs = len(keys) - cards
    named = [f"{count} {word if count == 1 else word + 's'}" for count, word in ((cards, "card"), (prs, "PR")) if count]
    return " and ".join(named)


def listing(answer_id: str, kept: dict, keys: list[str], asked: dict[str, dict]) -> str:
    rows = [
        f"  {key}  {entry['kind']}  {entry['job'] or 'no job'} {entry['order'] or 'no order'}  {cut(entry['title'], 100)}  {entry['url']}"
        for key in keys
        for entry in [asked[key]]
    ]
    return "\n".join(
        [
            (
                f'his answer {answer_id} "{cut(kept["words"], 80)}" stays unfinished until each card asking him and each PR not yet'
                f" live when it came is marked standing, superseded or reopened by it; {counted(keys, asked)}"
                f" {'has' if len(keys) == 1 else 'have'} no mark:"
            ),
            *rows,
            (
                f"mark each: drive answer {answer_id} --stands <key>... --superseded <key>=stop|<key>=<live job to fold it into>..."
                " --reopened <PR key>=<live job that reworks it to his words>..., or --others-stand once each it supersedes or"
                " reopens is marked"
            ),
        ]
    )


def settle(card: str, kept: dict) -> None:
    """Settle a superseded card in place, showing his words and a link to them with no buttons, so a late tap records nothing."""
    result = slack.cc_slack("card-answer", "--card", card, "--label", kept["words"], "--withdrawn", "--via", kept["link"])
    if result.get("already_answered"):
        raise DriveError(f"{card} already shows his answer {result['label']!r}, so it no longer asks him and needs no mark; the drive's cards are reread now, run this again")


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


def reopen(data: dict, answer_id: str, key: str, by: str) -> list[str]:
    """Hand the PR to the job that reworks it to his answer, dropping a tap ask its card made for the change as it stands."""
    done = []
    if data["prs"][key]["wants_tap"]:
        data["prs"][key].update(wants_tap=False, updated=state.stamp())
        done.append(f"{key} no longer waits on his tap until {by} reworks it")
    return [*done, f"{key} reopened by his answer {answer_id}: {by} reworks it to his words, so brief {by} with them"]


def supersede(data: dict, answer_id: str, key: str, entry: dict, into: str | None) -> list[str]:
    """Stop the superseded card's job, or fold what it carries into a live job, and drop its PR's tap ask; what was done, a line each."""
    done = []
    if entry["kind"] == "tap":
        data["prs"][key].update(wants_tap=False, updated=state.stamp())
        done.append(f"{key} no longer waits on his tap")
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
