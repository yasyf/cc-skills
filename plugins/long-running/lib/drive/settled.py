import dataclasses
import html
import json
import mmap
import re
import subprocess
import sys
import time
from collections.abc import Iterator
from functools import cache
from pathlib import Path

from drive import limits, slack, state, view
from drive.install import config_dir
from drive.render import cut
from drive.state import DriveError

HAND_ASK = (
    "refused: `drive job ask <j> <file>` keeps a job's question and draws his card on it in the same call, under the"
    " waits id it mints, so no session posts an ask record: keep the question with it, a remote lane's ASK"
    " the watch did not keep with `drive job ask <j>`, and ask an open card of the job's again in new words with"
    " `--redraws <that card's waits id>`"
)
BODY_CHARS = 1500
QUOTED_CHARS = 400
CANDIDATES = 8
UNDECIDED = "none"
DECIDES = 0.8
DECIDE_SECONDS = 20
REPEATS_ASKED = 30
UNSAID_HERE = (
    " A bare option number, such as \"1\" or \"move to 3\", answers the card it was typed on and says nothing about this one;"
    ' a question he asked says nothing unless it proposes a way, as "why not just X?" orders X.'
)
BREAKS_QUESTION = (
    'Do his words rule out this option, so building it goes against what he said? The option: "{option}"{marked}. A standing'
    " rule for a kind of work, such as that one kind of thing is like any other, rules out an option that treats that kind as"
    " its own case. Pick the item whose words rule it out, or none when his words do not speak to what the option does." + UNSAID_HERE
)
UNTAPPED_QUESTION = (
    "Which item's words name an amount or a choice for what this card asks, such as 30 days or 64 rows, that the card gives"
    " him no tap for, as an option or a question of its own? His words after he got a card without one: \"You didn't give me"
    ' a tap for 30 day bonding how many times do I have to say this". Words he quotes from an alert, a log or another message'
    " are not his. Pick none when the card offers a tap for each amount and choice his words name." + UNSAID_HERE
)
CARD_QUESTION = (
    "Does the card itself, apart from what any one option builds, break one of his rulings below by the rule it carries: a Page"
    " line shows a page the card links lacking what the card's words say that page shows, its recommended option or its Root"
    " cause line breaks his ruling on root causes, or what it says runs meanwhile reads or writes a team's data the way his"
    " ruling on a team's data rules out? His ruling on root causes speaks only to the option the card recommends: an option"
    " offered beside it, such as the Simplest line's, which every card must offer, breaks nothing. Pick the ruling it breaks,"
    " or none."
)
ADVISED = (
    "drive job ask: Jev reads {part} as breaking {id}, {asked}, \"{said}\".{rule} This finding refuses no card, by his pick"
    " 320b7a7 that drive job ask keeps every well-shaped card and prints Jev's finding to the agent: when it holds, rewrite"
    " that part and ask the same question again, which redraws its card, or, when the card asks him to lift those words, ask"
    " it again with `--unsettled \"{id}: <why his words do not reach it>\"`, which holds its PR on an incident until he answers."
)
WORKED_QUESTION = (
    'Is this option worked out on the card to what it will look like once built, by his ruling below? The option: "{option}".'
    " Pick the ruling when the card offers it as a design to build and its Show me carries no example of this option itself,"
    " such as its real calls, the rows it writes, its flow or what a caller gets, so the card tells it only by its description,"
    " the files it touches or its cost. Pick none when the Show me works it out, or when the card asks only whether or when to"
    " release, land, run or hold something."
)
WORKED_OUT = "The card works it out, or offers no design."
UNBROKEN = "His words do not rule it out."
SUPERSEDES_QUESTION = (
    "His answer below was given to a newer question of the same job. Which of these older open questions does it decide or make"
    " moot, so that card asks him nothing his answer leaves open? Pick none when each of them still asks something his answer"
    " does not settle."
)
APPROVES_QUESTION = (
    "Does this answer, in his own words, approve the action? It does when, read with the card it answers, it has the"
    " release the action starts run: his order to ship or release that work, or his pick on a card about that work reaching"
    " production, such as whether, when or how to release it or what the release carries or does as it runs, whose option"
    " has the release run. A card asking how a release runs, such as whether it hands a service over, asks about that"
    " release and is no design to build, so his yes approves the release with that part in it. A pick that names a"
    " condition, such as once tests pass or after a fix merges, approves the release once it holds, which the drive sees to"
    " before it starts, and the drive writes the release's targets, envs and flags from his pick, so his words need not name"
    " them. Pick none when his pick has no release run: his Approve or tap on a pull request, or a pick to land, merge or"
    " enqueue one, lands it and releases nothing, and so does a pick of which code or data layout to build, how to repair"
    " something, or whether a check holds. Pick none when unsure."
)
REPEATS_QUESTION = (
    "A drive tooling order is being added in the words below. Does it ask for what one of these open drive tooling orders already"
    " asks, the same fault or the same change in other words, so it joins that order and the job building it instead of starting a"
    " second order, builder and card? Pick that order, or none when it asks for anything none of them asks, even about the same code."
)
APPROVAL = "kind:approval"
INCIDENT = "incident"
TAP_HEADER = "header:Tap"
TAP_QUESTION = re.compile(r"(?:PR )?(?:Taps?\b|Approv|Land\b).*?(?:#\d+|/pull/\d+)", re.IGNORECASE)
RARE = re.compile(r"[a-z][a-z0-9-]{4,}")
NOTES = "\nNotes:"
ELIDED = re.compile(r"\.\.\.|…|\[[^\[\]]*\]")
UNWORDED = re.compile(r"[\W_]+")
TERMINAL = "terminal"
HUMAN = b'"kind":"human"'
HIS_ANSWER = (
    "a question of his becomes a ruling only with his own answer, so only his words verbatim are recorded as his, at the"
    " permalink of his message or of the card he answered, or at terminal for a prompt he typed into a drive session's terminal,"
    " cut with … where words are left out, never a drafted answer he never saw or a reading of his words: answer a question"
    " he asked in his thread and record his reply. A drafted ruling"
    " beside his question is how a store duplicating one that existed was built, his words: \"its a terieble duplicatgion and"
    ' was clerarly not throught through"'
)
COMMON = {
    "about", "above", "after", "again", "being", "below", "between", "could", "doing", "during", "every", "first", "having", "later",
    "maybe", "might", "never", "other", "ought", "right", "should", "since", "still", "their", "there", "these", "thing", "things",
    "those", "through", "today", "under", "until", "where", "which", "while", "would", "yours",
}


def card_refusal(job_id: str, question: dict, file: Path) -> str | None:
    """Why the job's question kept in this file draws no card, whatever waits id it is drawn under: it ended, answered by his tap or words, withdrawn or superseded, so its card never draws again, naming the words it ended on, whatever refusal an older install left on it, `drive job ask` or `drive job card` refused it, so it waits on that refusal, or a `Replaces:` line folded it into a later question, whose card asks it."""
    if closed := question["closed"]:
        words = f': "{cut(closed["words"], QUOTED_CHARS)}"' if closed["words"] else ""
        link = f' ({closed["link"]})' if closed["link"] else ""
        return f'refused: {job_id}\'s question "{question["title"]}" kept in {file} ended {closed["how"]}, so its card never draws again{words}{link}'
    if question["refused"]:
        return f'refused: {file} keeps {job_id}\'s question "{question["title"]}" pending, since `{state.refused_by(question)}` refused it, so no card draws from it; act on that refusal and ask again: {question["refused"]}'
    if question["replaced"]:
        return f'refused: {job_id}\'s question "{question["title"]}" kept in {file} is asked on his card as part of the question kept in {question["replaced"]}, so it draws no card of its own: draw that file'
    return None


def recorded(title: str, words: str, labels: list[str], cwd: str) -> str:
    """Record his answer in cc-notes, the question as its title and his words as its body, and return its short id."""
    add = ["ccn", "answer", "add", f"--title={title}", "--body-file", "-", *(f"--label={label}" for label in labels), "--json"]
    return json.loads(ccn(add, words, cwd, "could not record his answer in cc-notes"))["id"][:7]


def supersede(pick: str, answer_id: str, cwd: str) -> None:
    """Record in cc-notes that his answer replaces his earlier pick, so ccn answer match, which every brief's rulings read, no longer ranks the pick as standing."""
    ccn(["ccn", "answer", "supersede", pick, "--by", answer_id], "", cwd, f"could not mark his pick {pick} superseded by his answer {answer_id} in cc-notes")


def kind(words: str) -> list[str]:
    """The kind label of his answer: a tap on a PR card, which answers only Approve or Hold, is an approval."""
    return [APPROVAL] if words in state.TAP_LABELS else []


def backfill(cwd: str) -> None:
    """Label as approvals the taps on PR cards recorded before `drive answer` labelled them, by the question alone, whatever his words: one opening with Tap, Approve or Land that names a PR, or one under a Tap header."""
    listed = ccn(["ccn", "answer", "list", "--label", "scope:durable", "--limit", "0", "--full", "--json"], "", cwd, "could not read his recorded answers")
    for answer in json.loads(listed):
        if APPROVAL not in answer["tags"] and (TAP_QUESTION.match(answer["title"]) or TAP_HEADER in answer["tags"]):
            ccn(["ccn", "answer", "edit", answer["id"], "--add-label", APPROVAL], "", cwd, f"could not label his answer {answer['id'][:7]} an approval")


def ccn(argv: list[str], text: str, cwd: str, failure: str) -> str:
    try:
        done = subprocess.run(argv, input=text, cwd=cwd, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DriveError(f"{failure}: {error}") from None
    if done.returncode:
        raise DriveError(f"{failure}: {' '.join(done.stderr.split())}")
    return done.stdout


def his(body: str) -> str:
    """His words in a recorded answer's body: all of it above the drive's notes, which a Notes: line opens."""
    return body.partition(NOTES)[0].strip()


def heard(link: str, found: dict | None, data: dict) -> str | None:
    """His words at a Slack link: his answer on the card there, tapped or typed, as the drive's question drawn on it ended on them or else as cc-slack keeps it, or else the message there when a person wrote it; None for a card still asking him or one withdrawn, and for a message the drive or a bot posted."""
    if said := next((closed["words"] for _, question in view.drawn_on(data, link) if (closed := question["closed"]) and closed["how"] == "answered" and closed["words"]), None):
        return said
    if found and (closed := view.closed_card(found, link)) and closed["answer"] and not closed["answer"].get("withdrawn"):
        return closed["answer"]["label"]
    message = slack.message(link)
    return message["text"] if message.get("from_user") else None


def unsaid(kept: list[str], link: str | None, found: dict | None, data: dict) -> str | None:
    """Why the first of the words kept as his answer that are not his is not: no link names where he said them, what is there is not his, or it lacks a piece of them, split where an ellipsis or a bracketed insertion leaves his words out; None when each piece of each is his, what is at the link read once for all of them. At terminal, his words are a prompt he typed into a session the drive binds that holds every piece."""
    read: list[str | None] = []
    for words in kept:
        if not plain(ELIDED.sub(" ", words)):
            return f"no words of his are given; {HIS_ANSWER}"
        if not link:
            return f'no permalink names where he said "{cut(words, 200)}"; {HIS_ANSWER}'
        if link == TERMINAL:
            if any(lacking(words, said) is None for said in typed(data)):
                continue
            return f'no prompt he typed into a terminal of the drive\'s sessions says "{cut(words, 200)}"; {HIS_ANSWER}'
        if not read:
            try:
                read.append(heard(link, found, data))
            except slack.SlackError as error:
                return f"{link} could not be read for his words ({error}); {HIS_ANSWER}"
        if read[0] is None:
            return f"{link} is neither his own message nor a card he answered; {HIS_ANSWER}"
        if missing := lacking(words, read[0]):
            return f'his words at {link} do not say "{cut(missing, 200)}"; {HIS_ANSWER}'
    return None


def lacking(words: str, said: str) -> str | None:
    """The first piece of the words, split where an ellipsis or a bracketed insertion leaves his words out, that what he said does not hold."""
    return next((piece.strip() for piece in ELIDED.split(words) if f" {plain(piece)} " not in f" {plain(said)} "), None)


def typed(data: dict) -> Iterator[str]:
    """Each prompt typed into a session the drive binds, read from its transcript by human_words; a row still being written is left for the next read."""
    for held in state.bound_sessions(data).values():
        path = next((config_dir() / "projects").glob(f"*/{held['session']}.jsonl"), None)
        if path is None or not path.stat().st_size:
            continue
        with path.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as rows:
            at = rows.find(HUMAN)
            while at != -1 and (end := rows.find(b"\n", at)) != -1:
                if (said := human_words(json.loads(rows[rows.rfind(b"\n", 0, at) + 1 : end]))) is not None:
                    yield said
                at = rows.find(HUMAN, end)


def human_words(row: dict) -> str | None:
    """The words of a transcript row that is a prompt a person typed, as cc-sync's ClassifyRecord reads human input: a user row or queued command whose origin Claude Code marks a human's and that is no meta injection. A cci delivery's origin is its channel's and a tool result carries none, so neither is his; a prompt Orca types into a terminal is marked a human's too."""
    queued = row["type"] == "attachment" and row["attachment"].get("type") == "queued_command"
    if not queued and row["type"] != "user":
        return None
    prompt = row["attachment"] if queued else row
    if (prompt.get("origin") or {}).get("kind") != "human" or prompt.get("isMeta"):
        return None
    said = prompt["prompt"] if queued else row["message"]["content"]
    return said if isinstance(said, str) else " ".join(block["text"] for block in said if block.get("type") == "text")


def plain(text: str) -> str:
    """The words of a message as he wrote them, with no link markup, formatting, punctuation or case, so his words copied by hand match what Slack keeps."""
    return " ".join(UNWORDED.sub(" ", html.unescape(read(text))).casefold().split())


def shown(answer_id: str, cwd: str) -> dict:
    """One recorded answer of his, its title, body and labels."""
    return json.loads(ccn(["ccn", "answer", "show", answer_id, "--json"], "", cwd, f"could not read his answer {answer_id}"))


def matched(text: str, cwd: str) -> list[dict]:
    """The first CANDIDATES of his recorded answers ccn ranks nearest to an ASK's whole block, findings included, which reach a ruling whose subject only its findings name, such as a wait he ordered on another order, each as Jev reads his words beside the card; never a tap approving a PR, which rules only whether that PR lands."""
    match = ["ccn", "answer", "match", "-", "--label", "scope:durable", "--nearest", "--limit", "-1", "--json"]
    found = json.loads(ccn(match, read(text), cwd, "could not check the question against his recorded answers"))
    return [{"id": answer["id"][:7], "asked": answer["title"], "said": his(answer["body"])[:BODY_CHARS]} for answer in found if APPROVAL not in answer["tags"]][:CANDIDATES]


def judged(case: str, asked: limits.Question, words: list[dict]) -> None:
    """Print on stderr each part of a card Jev reads as going against his words, in one reading of the card against all of them at once and his rulings with the rule each sets beside them, at least DECIDES sure, with the words it breaks: an option his words or a ruling that speaks to every option rule out, an option of a design the card never works out to what it will look like, an amount or choice his words name that the card gives no tap for, and a ruling the card itself breaks. Each finding refuses nothing, by his pick 320b7a7 "Advisory judge: drive job ask keeps every well-shaped card and prints Jev's finding to the agent; no card is refused on merits". His ruling on root causes is weighed on the card alone, never per option, since it speaks only to the option the card recommends and its Root cause line. Jev giving no reading leaves the card to him, said on stderr."""
    rules = limits.rule_words(case)
    cited = {UNDECIDED: UNBROKEN, **dict.fromkeys(word["id"] for word in [*words, *rules] if word["id"] != limits.ROOT_CAUSE_WORDS["id"])}
    items = {word["id"]: word for word in [*words, *rules, limits.WORKED_WORDS]}
    parts = {f"{kind}{number}": f'option "{option}"' for kind in ("breaks", "worked") for number, option in enumerate(asked.options, 1)} | {"untapped": "the card", "card": "the card"}
    questions = {
        **{
            f"breaks{number}": {"label": {"instructions": BREAKS_QUESTION.format(option=option, marked=" (recommended)" if option == asked.recommended else ""), "options": cited}}
            for number, option in enumerate(asked.options, 1)
        },
        **{
            f"worked{number}": {"label": {"instructions": WORKED_QUESTION.format(option=option), "options": {UNDECIDED: WORKED_OUT, limits.WORKED_WORDS["id"]: None}}}
            for number, option in enumerate(asked.options, 1)
        },
        **({"untapped": {"label": {"instructions": UNTAPPED_QUESTION, "options": {UNDECIDED: "The card offers a tap for each.", **dict.fromkeys(word["id"] for word in words)}}}} if words else {}),
        "card": {"label": {"instructions": CARD_QUESTION, "options": {UNDECIDED: "It breaks none of them.", **dict.fromkeys(rule["id"] for rule in rules)}}},
    }
    heard = [{key: item[key] for key in ("id", "asked", "said", "rule") if key in item} for item in items.values()]
    try:
        read = jev({"state": {"card": case, "his_words": heard}, "questions": questions})
    except Unheard as failure:
        print(f"drive job ask: Jev gave no reading of this question against his words, so it goes to him: {failure}", file=sys.stderr)
        return
    for key, found in read.items():
        if found["choice"] in items and found["probabilities"][found["choice"]] >= DECIDES:
            item = items[found["choice"]]
            rule = f" Its rule: {item['rule']}" if "rule" in item else ""
            print(ADVISED.format(part=parts[key], id=item["id"], asked=cut(item["asked"], 200), said=cut(item["said"], QUOTED_CHARS), rule=rule), file=sys.stderr)


def decided(title: str, words: str, older: dict[str, dict]) -> set[str]:
    """The older questions of a job that his answer to its newer one decides or makes moot, as Jev reads it against all of them in one call, so each is weighed beside the others, each at the DECIDES bar; Unheard when Jev gave no reading."""
    options = {key.rpartition(":")[2]: f'{entry["title"]} Options: {" | ".join(entry["options"])}' for key, entry in older.items()}
    label = {"instructions": SUPERSEDES_QUESTION, "options": {UNDECIDED: "It decides none of them.", **options}}
    read = jev({"state": {"answer": {"asked": title, "said": words[:BODY_CHARS]}}, "questions": {"supersedes": {"label": label}}})["supersedes"]
    return {key for key in older if read["probabilities"].get(key.rpartition(":")[2], 0) >= DECIDES}


def repeats(data: dict, words: str) -> str | None:
    """The open drive tooling order these new tooling words ask for again, as Jev reads them in one call against the REPEATS_ASKED open tooling orders whose title and words share the most rare words with them, each order's title and words an option, picked at the DECIDES bar, since one label question takes at most 255 options however many tooling orders are open; None when no tooling order is open or Jev picks none, refused when Jev gives no answer."""
    said = rare(words)
    orders = [(order_id, order) for order_id, order in state.open_orders(data).items() if order["text"].startswith(state.TOOLING)]
    closest = sorted(orders, key=lambda item: len(said & rare(f'{item[1]["title"] or ""} {item[1]["text"]}')), reverse=True)[:REPEATS_ASKED]
    if not closest:
        return None
    options = {order_id: " — ".join(filter(None, [order["title"], cut(order["text"], QUOTED_CHARS)])) for order_id, order in closest}
    label = {"instructions": REPEATS_QUESTION, "options": {UNDECIDED: "It repeats none of them.", **options}}
    try:
        pick = jev({"state": {"order": words[:BODY_CHARS]}, "questions": {"repeats": {"label": label}}})["repeats"]
    except Unheard as failure:
        raise DriveError(
            f"no order added: Jev did not read these words against the {len(options)} open drive tooling orders sharing the most words with them ({failure}),"
            " so a repeat of one would start a second order, builder and card on it; run it again, or name the order it repeats with --joins <o>"
        ) from None
    return pick["choice"] if pick["choice"] in options and pick["probabilities"][pick["choice"]] >= DECIDES else None


def approving(action: str, answer: dict) -> bool:
    """Whether Jev reads this answer of his as his order or card pick approving the action, at the 0.8 bar; Unheard when Jev gave no answer."""
    return picked(APPROVES_QUESTION, "It approves no such action.", {"action": action}, [answer]) is not None


def picked(instructions: str, unpicked: str, asked: dict, found: list[dict]) -> dict | None:
    """The one of these answers Jev picks for what asked holds, asked as captain_hook's permits_question asks with none first and picked at its 0.8 bar; none or a lower probability picks nothing."""
    if not found:
        return None
    answers = [{key: answer[key] for key in ("id", "asked", "said")} for answer in found]
    label = {"instructions": instructions, "options": {UNDECIDED: unpicked, **dict.fromkeys(answer["id"] for answer in found)}}
    pick = jev({"state": asked | {"answers": answers}, "questions": {"decides": {"label": label}}})["decides"]
    if pick["choice"] == UNDECIDED or pick["probabilities"][pick["choice"]] < DECIDES:
        return None
    return next(answer for answer in found if answer["id"] == pick["choice"])


class Unheard(Exception):
    """Jev gave no answer: no key, a timeout, a request Jev rejected, or Jev declining the question."""


@cache
def jev_key() -> str:
    """Jev's API key, from TYPESAFE_API_KEY or else the Keychain item `spawnllm key set jev` wrote, read once per process, so no question after the first starts a process."""
    from spawnllm.decide import JEV, api_key

    return api_key(JEV, timeout=DECIDE_SECONDS)


def jev(request: dict) -> dict:
    """Jev's answers to the questions the request asks of its state, each `{"binary": {...}}` or `{"label": {...}}` holding that question's fields, by question id, as spawnllm's `decide_sync` gives them in this process on the one keep-alive client every question of the process shares, the key's read and the answer both within DECIDE_SECONDS; Unheard when none came, Jev declining any of them included. spawnllm is imported on the first question, so a hook or verb that asks none never loads it."""
    import httpx
    from spawnllm import (
        JEV,
        Binary,
        DecideError,
        DecideKeyMissing,
        Label,
        Refused,
        decide_sync,
    )

    kinds = {"binary": Binary, "label": Label}
    questions = {key: kinds[kind](**fields) for key, spec in request["questions"].items() for kind, fields in spec.items()}
    deadline = time.monotonic() + DECIDE_SECONDS
    try:
        token = jev_key()
        decision = decide_sync(request["state"], questions, provider=JEV, timeout=deadline - time.monotonic(), api_key=token)
    except (TimeoutError, ValueError, DecideError, DecideKeyMissing, httpx.HTTPError) as error:
        raise Unheard(str(error)) from None
    if any(isinstance(answer, Refused) for answer in decision.answers.values()):
        raise Unheard("Jev declined the question")
    return {key: dataclasses.asdict(answer) for key, answer in decision.answers.items()}


def answered(data: dict, title: str) -> dict | None:
    """His answer to this same question: the newest question of any job asked in these words, whatever their case and spacing, that ended answered by his tap or words on or under its card, or by his words a record kept for it; a question kept answered with no card and no record, as `drive job ask` once kept one on Jev's reading of an answer he gave another question, is none."""
    return max(
        (
            question
            for job in data["jobs"].values()
            for question in job["questions"]
            if (closed := question["closed"])
            and closed["how"] == "answered"
            and (question["card"] or question["waits"] or closed["seq"])
            and state.same_question(question["title"], title)
        ),
        key=lambda question: state.parse_time(question["closed"]["at"]),
        default=None,
    )


def order_words(data: dict, order_id: str) -> list[dict]:
    """His words on the order, each without the lines he quoted with ">", which are another's: the words of his the drive quotes in an order not added from his own Slack message, as limits.his_words reads them, then each of his messages about it in its thread, the one that started it included, as state.message_order reads each, never one about another order sharing that thread, each by its message's ts, since an order's stored text can lose the line break between his words and the lines he quoted; a thread that cannot be read fails as a Slack error, never as a refusal of the question."""
    order = data["orders"][order_id]
    own = [] if limits.from_his_message(order) or not (said := limits.his_words(order)) else [{"id": order_id, "asked": "his words the order quotes", "said": said[:BODY_CHARS]}]
    if not (where := order["thread"]):
        return own
    try:
        messages = slack.thread("--channel", where[0], "--thread", where[1], "--limit", str(slack.WHOLE_THREAD))
    except slack.SlackError as error:
        raise slack.SlackError(f"could not read the order's thread {where[1]} for his words this question is judged against, so nothing is kept; run it again: {error}") from None
    return own + [
        {"id": f"p{message['ts'].replace('.', '')}", "asked": "his message in the order's thread", "said": said[:BODY_CHARS]}
        for message in messages
        for said in [unquoted(html.unescape(message["text"]))]
        if message.get("from_user") and said and state.message_order(data, *where, message["ts"]) == order_id
    ]


def unquoted(text: str) -> str:
    """His lines of a message, without those he quoted with ">", which are another's words."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(">")).strip()


def rare(text: str) -> set[str]:
    return {word for word in RARE.findall(read(text).lower()) if word not in COMMON}


def read(text: str) -> str:
    """The words of a question as he reads them: a Slack link as its label, other links and bare URLs left out, since every PR link shares github, pull and monorepo with each old answer that names a PR."""
    return limits.URL.sub(" ", limits.TOKEN.sub(" ", limits.LABELLED.sub(r" \1 ", text)))
