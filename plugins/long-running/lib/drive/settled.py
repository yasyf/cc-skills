import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from drive import limits, slack, state, superseded, view
from drive.render import cut
from drive.state import DriveError

UNKEPT_ASK = (
    "refused: an ask record names the ASK `drive job ask` kept, as the attr `ask` set to the path it printed, and the"
    " communicator draws the card from that file; keep the question with `drive job ask <j> <file>`, then post this"
    " record again with that path"
)
UNJOBBED = "refused: an ask record names the job that kept its ASK, which his answer resumes; post it again with `--attr job=<j>`, the j in the ASK's file name"
KEEP_ASKED = (
    "the communicator draws the card from that file alone. Keep this record's question with `drive job ask {job} <file>`, or"
    " `drive job ask {job}` with no file for a remote lane's ASK the watch did not keep, which reads its report;"
    " then post this record again with the path it prints as `cci post --kind ask --attr job={job} --attr ask=<path> --path <path>`"
    " and no text, which takes the kept question's first line as its text"
)
UNMARKED_INCIDENT = (
    "refused: {job} works on open incident {orders}, so this card blocks it, and the handler says so in the incident's thread"
    " the moment the card is up only when the record says it is an incident's; post it again with {flags}"
)
CANDIDATES = 8
BODY_CHARS = 1500
QUOTED_CHARS = 400
UNDECIDED = "none"
DECIDES = 0.8
DECIDE = ["uvx", "--from", "spawnllm>=0.17.7", "spawnllm", "decide", "--provider", "jev", "--timeout", "20"]
DECIDES_QUESTION = (
    "What does this one answer, in his own words, settle about the question? An answer he gave to another question settles"
    " this one when its words choose what an option says or order what this question asks about. Pick picks when it chooses"
    " one of the options; pick orders when it orders the outcome the question asks about, or who does it, so the options it"
    " rules out are out and the drive builds the rest without asking him; pick none when it settles nothing the question asks."
    " A bare option number answers the card it was typed on, so it settles nothing here."
)
SETTLES = {UNDECIDED: "It settles nothing this question asks.", "picks": "Its own words pick one option.", "orders": "Its own words order the outcome or who does it."}
ORDERED = (
    " He ordered what this question asks about, so build the option his words leave that removes the root cause, the recommended"
    " one, and say in one line that his answer {id} settled it."
)
APPROVES_QUESTION = (
    "Does this answer, in his own words, approve the action? Pick the answer when it is his order to ship, release or approve"
    " the release the action starts, or his pick on a card that asked whether to release it. Pick none when it picks an option"
    " on a card that asked anything else, such as whether to land a pull request, which design to build or whether a check"
    " holds, or when unsure."
)
APPROVAL = "kind:approval"
INCIDENT = "incident"
TAP_HEADER = "header:Tap"
TAP_QUESTION = re.compile(r"(?:PR )?(?:Taps?\b|Approv|Land\b).*?(?:#\d+|/pull/\d+)", re.IGNORECASE)
RARE = re.compile(r"[a-z][a-z0-9-]{4,}")
AMOUNT = re.compile(r"(?<![\w.,:#/$-])(\d+(?:\.\d+)?)[- ]?([a-z]+)\b", re.IGNORECASE)
UNOFFERED_RULING = 'his Nov 11 1:28 PM words "You didn\'t give me a tap for 30 day bonding how many times do I have to say this"'
COMMON = {
    "about", "above", "after", "again", "being", "below", "between", "could", "doing", "during", "every", "first", "having", "later",
    "maybe", "might", "never", "other", "ought", "right", "should", "since", "still", "their", "there", "these", "thing", "things",
    "those", "through", "today", "under", "until", "where", "which", "while", "would", "yours",
}


def ask_refusal(names: list[str], named: dict[str, str], text: str) -> str | None:
    """Why an ask record may not go out: the communicator draws its card from the ASK file its `ask` attr names, so that file is one `drive job ask` kept in one of these drives, checked against his answers, for the question of the job its `job` attr names that this record asks and his answer has not closed; another job's file, another question's or an answered one's draws no card for it. An ask of a job on an open incident order carries the marks that make its handler tell the incident's thread."""
    if (name := keeper(names, named.get("ask"))) is None:
        return UNKEPT_ASK
    file = Path(named["ask"]).expanduser()
    if not (job_id := named.get("job")):
        return UNJOBBED
    data = state.load(name)
    job = data["jobs"].get(job_id)
    keep = KEEP_ASKED.format(job=job_id)
    if not (question := job and state.kept_in(job, file)):
        return f"refused: {file} keeps no question of {job_id}, and {keep}"
    if " ".join(question["title"].split()).casefold() not in " ".join(text.split()).casefold():
        return f'refused: {file} keeps {job_id}\'s question "{question["title"]}", which this record does not ask, and {keep}'
    if question["refused"]:
        return f'refused: {file} keeps {job_id}\'s question "{question["title"]}" pending, since `drive job ask` refused it, so no card draws from it; act on that refusal and ask again: {question["refused"]}'
    if question["card"] or question["waits"]:
        view.refresh_cards(name)
    if not any(open_question is question for open_question in superseded.unanswered(data, job, view.live_cards(name))):
        return f'refused: his answer closed {job_id}\'s question "{question["title"]}" kept in {file}, so its card never draws again, and {keep}'
    if marks := incident_marks(names, named):
        orders = ", ".join(state.open_incidents(data, [job["order"], *job["carries"]]))
        return UNMARKED_INCIDENT.format(job=job_id, orders=orders, flags=" ".join(f"--attr {key}={value}" for key, value in marks.items()))
    return None


def incident_marks(names: list[str], named: dict[str, str]) -> dict[str, str]:
    """The attrs an ask record for a job on an open incident order lacks: incident=true, so its handler says in the incident's thread the moment the card is up that the card blocks it, and as its thread the incident's, where that line goes and `drive job set --told` looks for it."""
    if not (name := keeper(names, named.get("ask"))):
        return {}
    data = state.load(name)
    if not (job := data["jobs"].get(named.get("job") or "")) or not (incidents := state.open_incidents(data, [job["order"], *job["carries"]])):
        return {}
    threads = state.incident_threads(data, incidents)
    marks = {"incident": "true"} | ({"thread": threads[0]} if threads and named.get("thread") not in threads else {})
    return {key: value for key, value in marks.items() if named.get(key) != value}


def keeper(names: list[str], ask: str | None) -> str | None:
    """The drive among these whose asks folder holds this file, the one place `drive job ask` keeps an ASK."""
    return next((name for name in names if ask and Path(ask).expanduser().resolve().is_relative_to(state.drive_dir(name).resolve() / "asks")), None)


def matched(text: str, cwd: str, *flags: str) -> list[dict]:
    """His answers sharing rare words with the text, best first, but a tap approving a PR, which settles only whether that PR lands; `--nearest` among the flags ranks every answer sharing one rare word, without ccn's floors."""
    match = ["ccn", "answer", "match", "-", "--label", "scope:durable", "--limit", "-1", "--json", *flags]
    found = json.loads(ccn(match, text, cwd, "could not check the question against his recorded answers"))
    return [answer for answer in found if APPROVAL not in answer["tags"]]


def recorded(title: str, words: str, labels: list[str], cwd: str) -> str:
    """Record his answer in cc-notes, the question as its title and his words as its body, and return its short id."""
    add = ["ccn", "answer", "add", f"--title={title}", "--body-file", "-", *(f"--label={label}" for label in labels), "--json"]
    return json.loads(ccn(add, words, cwd, "could not record his answer in cc-notes"))["id"][:7]


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


def shown(answer_id: str, cwd: str) -> dict:
    """One recorded answer of his, its title, body and labels."""
    return json.loads(ccn(["ccn", "answer", "show", answer_id, "--json"], "", cwd, f"could not read his answer {answer_id}"))


def candidates(shown: str, whole: str, drives: list[str], cwd: str, under: list[dict], heard: list[dict], order: str) -> list[dict]:
    """What may already decide a question, each the question he answered and his words, judged by no rule here: the first CANDIDATES of his recorded answers ccn matches on the words he reads, then the first CANDIDATES of each drive's answers ccn ranks nearest to the ASK's whole block, findings included, which reach a ruling whose subject only its findings name, then his words under a card its job asked on, and his messages in the order's thread, under the order."""
    worded = matched(read(shown), cwd)[:CANDIDATES]
    nearest = [answer for name in drives for answer in matched(read(whole), cwd, "--nearest", "--label", f"drive:{name}")[:CANDIDATES]]
    found = [{"id": answer["id"][:7], "asked": answer["title"], "said": answer["body"][:BODY_CHARS]} for answer in {answer["id"]: answer for answer in [*worded, *nearest]}.values()]
    return [
        *found,
        *(answer for answer in under if answer["id"] not in {other["id"] for other in found}),
        *({"id": f"p{message['key']}", "asked": order, "said": message["text"], "link": message["link"]} for message in heard),
    ]


def refusals(answer: dict | None, under: list[dict], heard: list[dict], offered: str, why: str | None) -> str | None:
    """Every reason a question may not go to him, in one refusal, so one ask again meets them all: the one answer of his Jev reads as already deciding it, his words under its job's open card that --unsettled names, and an amount his words in the order's thread name that it offers no tap for."""
    layers = [decided_refusal(answer, why), open_card_refusal(under, why), unoffered_refusal(heard, offered)]
    return "\n".join(layer for layer in layers if layer) or None


def decided(question: str, options: list[str], found: list[dict]) -> dict | None:
    """Of his answers, the one Jev, reading each alone in its own call so none splits another's probability, is surest picks an option or orders what the question asks about, with which it does, once at least 0.8 sure; below that for every answer the question goes to him, each answer Jev gave no reading of said on stderr."""
    with ThreadPoolExecutor(max(len(found), 1)) as pool:
        readings = list(zip(found, pool.map(lambda answer: reading({"question": question, "options": options}, answer), found), strict=True))
    judged = [
        (sum(probability for label, probability in read["probabilities"].items() if label != UNDECIDED), answer, read["choice"])
        for answer, read in readings
        if not isinstance(read, Unheard)
    ]
    sure, answer, settles = max(judged, key=lambda judged: judged[0], default=(0, None, None))
    if sure >= DECIDES:
        return answer | {"settles": settles}
    for answer, read in readings:
        if isinstance(read, Unheard):
            print(f"drive job ask: Jev did not say whether his answer {answer['id']} decides this question, so it goes to him: {read}", file=sys.stderr)
    return None


def approving(action: str, answer: dict) -> bool:
    """Whether Jev reads this answer of his as his order or card pick approving the action, at the 0.8 bar; Unheard when Jev gave no answer."""
    return picked(APPROVES_QUESTION, "It approves no such action.", {"action": action}, [answer]) is not None


def picked(instructions: str, unpicked: str, asked: dict, found: list[dict]) -> dict | None:
    """The one of these answers Jev picks for what asked holds, asked as captain_hook's permits_question asks with none first and picked at its 0.8 bar; none or a lower probability picks nothing."""
    if not found:
        return None
    answers = [{key: answer[key] for key in ("id", "asked", "said")} for answer in found]
    label = {"instructions": instructions, "options": {UNDECIDED: unpicked, **dict.fromkeys(answer["id"] for answer in found)}}
    pick = jev({"state": asked | {"answers": answers}, "questions": {"decides": {"label": label}}})
    if pick["choice"] == UNDECIDED or pick["probabilities"][pick["choice"]] < DECIDES:
        return None
    return next(answer for answer in found if answer["id"] == pick["choice"])


class Unheard(Exception):
    """Jev gave no answer: no key, a timeout, a spawnllm without the verb, or Jev declining the question."""


def jev(request: dict) -> dict:
    """Jev's answer to the label question `spawnllm decide` asks it; Unheard when none came, Jev declining the question included, which `spawnllm decide` prints as an answer of type refused with no choice."""
    try:
        done = subprocess.run(DECIDE, input=json.dumps(request), capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Unheard(str(error)) from None
    if done.returncode:
        raise Unheard(done.stderr.strip().rpartition("\n")[2])
    if (answer := json.loads(done.stdout)["answers"]["decides"])["type"] == "refused":
        raise Unheard("Jev declined the question")
    return answer


def reading(asked: dict, answer: dict) -> dict | Unheard:
    """Jev's reading of what this one answer of his settles about the question: none, picks or orders; the Unheard when Jev gave none."""
    label = {"instructions": DECIDES_QUESTION, "options": SETTLES}
    try:
        return jev({"state": asked | {"answers": [{key: answer[key] for key in ("id", "asked", "said")}]}, "questions": {"decides": {"label": label}}})
    except Unheard as failure:
        return failure


def decided_refusal(answer: dict | None, why: str | None) -> str | None:
    """Why a question may not go to him: Jev reads this answer of his as already deciding it, and no reason names it and says why it does not."""
    if not answer or answer["id"] in (why or ""):
        return None
    where = f"his message in this order's thread {answer['link']}" if "link" in answer else f"his recorded answer {answer['id']}"
    return (
        f'refused: {where} already decides this question. Asked "{cut(answer["asked"], 200)}", he said "{cut(answer["said"], QUOTED_CHARS)}".'
        + (ORDERED.format(id=answer["id"]) if answer["settles"] == "orders" else " Go ahead on it.")
        + f' If his words decide something else, ask again with --unsettled "{answer["id"]}: <why>".'
    )


def open_card_refusal(said: list[dict], why: str | None) -> str | None:
    """Why --unsettled may not name his words under this job's card while that card still asks him: they answer it, so the card shows them first."""
    if not (named := [answer for answer in said if answer["id"][:7] in (why or "")]):
        return None
    listed = "\n".join(f'- {answer["id"][:7]} under {answer["card"]}: "{cut(answer["words"], 200)}" ({answer["link"]})' for answer in named)
    commands = ", then ".join(f"`drive answer {answer['id'][:7]}`" for answer in named)
    return (
        "refused: --unsettled names his words under this job's own card, and that card still asks him. His words under a card"
        f" answer it: settle the card with {commands}, which shows his words on it, and the work goes ahead on them."
        f" Then ask only what they leave open, naming each id below with why it does not settle the new question.\n{listed}"
    )


def thread_words(data: dict, order: dict, team_url: str, question: str) -> list[dict]:
    """His messages in the order's own thread past the order itself sharing a rare word with the question, each with its permalink and the words shared."""
    if not (where := state.promise_thread(data, order)):
        return []
    try:
        messages = slack.thread("--channel", where[0], "--thread", where[1], "--limit", str(slack.WHOLE_THREAD))
    except slack.SlackError as error:
        raise DriveError(f"could not read the order's thread {where[1]} for his words that may settle this question: {error}") from None
    asked = rare(question)
    return [
        {"link": f"{team_url}archives/{where[0]}/p{key}?thread_ts={where[1]}", "key": key, "text": message["text"], "shared": shared}
        for message in messages
        for key in [message["ts"].replace(".", "")]
        if message.get("from_user") and message["ts"] != where[1] and (shared := sorted(asked & rare(message["text"])))
    ]


def unoffered_refusal(said: list[dict], offered: str) -> str | None:
    """Why a question may not go to him: an amount a clause of his words in the order's thread names, as "30 day bonding" names 30 days, is in neither its question nor an option, so he has no tap for what he named. Only a clause sharing a rare word with the question counts, since one message can name a cap of another subject beside a word the question uses."""
    have = amounts(offered)
    missing = {
        amount: message
        for message in said
        for part in limits.CLAUSE_BREAK.split(read(message["text"]))
        if rare(part) & set(message["shared"])
        for amount in sorted(amounts(part) - have)
    }
    if not missing:
        return None
    listed = "\n".join(f'- {amount}: {message["link"]} "{cut(message["text"], 200)}"' for amount, message in missing.items())
    return (
        "refused: his words in this order's thread name an amount this question offers no tap for. Give each its own option,"
        f" in his words, or ask about it in the question ({UNOFFERED_RULING}).\n{listed}"
    )


def amounts(text: str) -> set[str]:
    """Each amount the words name, as "30 days" and "30-day" both name 30 day."""
    return {
        f"{number} {unit.removesuffix('s') if unit in limits.MEASURES else unit}"
        for number, word in AMOUNT.findall(read(text))
        for unit in [word.casefold()]
        if unit in limits.MEASURES or f"{unit}s" in limits.MEASURES
    }


def rare(text: str) -> set[str]:
    return {word for word in RARE.findall(read(text).lower()) if word not in COMMON}


def read(text: str) -> str:
    """The words of a question as he reads them: a Slack link as its label, other links and bare URLs left out, since every PR link shares github, pull and monorepo with each old answer that names a PR."""
    return limits.URL.sub(" ", limits.TOKEN.sub(" ", limits.LABELLED.sub(r" \1 ", text)))
