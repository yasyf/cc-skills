import dataclasses
import html
import json
import mmap
import re
import subprocess
import time
from collections.abc import Iterator
from functools import cache
from pathlib import Path

from drive import limits, slack, state, view
from drive.install import config_dir
from drive.render import cut
from drive.state import DriveError

UNKEPT_ASK = (
    "refused: an ask record names the ASK `drive job ask` kept, as the attr `ask` set to the path it printed, and the"
    " communicator draws the card from that file; keep the question with `drive job ask <j> <file>`, then post this"
    " record again with that path"
)
UNJOBBED = "refused: an ask record names the job that kept its ASK, which his answer resumes; post it again with `job` in its attrs, the j in the ASK's file name"
KEEP_ASKED = (
    "the communicator draws the card from that file alone. Keep this record's question with `drive job ask {job} <file>`, or"
    " `drive job ask {job}` with no file for a remote lane's ASK the watch did not keep, which reads its report;"
    " then post this record again on the cci `post` tool with the path it prints, as `kind: ask`, `attrs: {{job: {job}, ask: <path>}}`,"
    " `path: <path>` and no text, which takes the kept question's first line as its text"
)
UNMARKED_INCIDENT = (
    "refused: {job} works on open incident {orders}, so this card blocks it, and the handler says so in the incident's thread"
    " the moment the card is up only when the record says it is an incident's; post it again with {flags}"
)
BODY_CHARS = 1500
QUOTED_CHARS = 400
UNDECIDED = "none"
DECIDES = 0.8
DECIDE_SECONDS = 20
CHANGES = 0.15
CHANGES_QUESTION = (
    "His answer below was given to another question. Which of these open questions he has not answered does it decide, rule out"
    " options of, or make moot, so that card must change or stop asking him? His answer may be a standing rule for a kind of work,"
    " worded with always, every or never, such as how every bug, landing or release is handled: it changes each open question about"
    " work of that kind whatever its subject, when the rule rules out an option or decides part of what it asks. Pick none when it"
    " changes none of them."
)
SUPERSEDES_QUESTION = (
    "His answer below was given to a newer question of the same job. Which of these older open questions does it decide or make"
    " moot, so that card asks him nothing his answer leaves open? Pick none when each of them still asks something his answer"
    " does not settle."
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
    if question["closed"]:
        return f'refused: {job_id}\'s question "{question["title"]}" kept in {file} ended {question["closed"]["how"]}, so its card never draws again, and {keep}'
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


def his(body: str) -> str:
    """His words in a recorded answer's body: all of it above the drive's notes, which a Notes: line opens."""
    return body.partition(NOTES)[0].strip()


def heard(link: str, found: dict | None) -> str | None:
    """His words at a Slack link: his answer on the card there, tapped or typed, or else the message there when a person wrote it; None for a card still asking him or one withdrawn, and for a message the drive or a bot posted."""
    if found and (closed := view.closed_card(found, link)) and closed["answer"] and not closed["answer"].get("withdrawn"):
        return closed["answer"]["label"]
    message = slack.message(link)
    return message["text"] if message.get("from_user") else None


def unsaid(words: str, link: str | None, found: dict | None, data: dict) -> str | None:
    """Why words kept as his answer are not his: no link names where he said them, what is there is not his, or it lacks a piece of them, split where an ellipsis or a bracketed insertion leaves his words out; None when each piece is his. At terminal, his words are a prompt he typed into a session the drive binds that holds every piece."""
    if not plain(ELIDED.sub(" ", words)):
        return f"no words of his are given; {HIS_ANSWER}"
    if not link:
        return f'no permalink names where he said "{cut(words, 200)}"; {HIS_ANSWER}'
    if link == TERMINAL:
        if any(lacking(words, said) is None for said in typed(data)):
            return None
        return f'no prompt he typed into a terminal of the drive\'s sessions says "{cut(words, 200)}"; {HIS_ANSWER}'
    try:
        said = heard(link, found)
    except slack.SlackError as error:
        return f"{link} could not be read for his words ({error}); {HIS_ANSWER}"
    if said is None:
        return f"{link} is neither his own message nor a card he answered; {HIS_ANSWER}"
    if missing := lacking(words, said):
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


def changed(title: str, words: str, asked: dict[str, dict], instructions: str = CHANGES_QUESTION, bar: float = CHANGES) -> set[str]:
    """The question cards still asking him that a new answer of his decides, rules options out of or makes moot, as Jev reads it against all of them in one call, so each card is weighed beside the others: each Jev gives at least CHANGES, since a card held wrongly costs one --stands mark and one missed keeps asking him what he already decided; asked instead which older questions of the job an answer decides, each at the bar given."""
    cards = {key.rpartition(":")[2]: key for key, entry in asked.items() if entry["kind"] == "question"}
    if not cards:
        return set()
    options = {short: f'{asked[key]["title"]} Options: {" | ".join(asked[key]["options"])}' for short, key in cards.items()}
    label = {"instructions": instructions, "options": {UNDECIDED: "It changes none of them.", **options}}
    try:
        read = jev({"state": {"answer": {"asked": title, "said": words[:BODY_CHARS]}}, "questions": {"changes": {"label": label}}})["changes"]
    except Unheard as failure:
        raise DriveError(
            f"nothing recorded in the drive: Jev did not read his answer against his {len(cards)} open {'card' if len(cards) == 1 else 'cards'} ({failure}), so each"
            " card it settles would keep asking him; run this drive answer again"
        ) from None
    return {cards[short] for short, probability in read["probabilities"].items() if short in cards and probability >= bar}


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


def own_words(data: dict, order_id: str, question: str) -> list[dict]:
    """His words in the order, as limits.his_words reads them, when they share a rare word with the question."""
    text = limits.his_words(data["orders"][order_id])
    return [{"link": f"{order_id}'s own words", "text": text, "shared": shared}] if (shared := sorted(rare(question) & rare(text))) else []


def thread_words(data: dict, order_id: str, team_url: str, question: str) -> list[dict]:
    """His messages about the order in its own thread past its root, as state.message_order reads each, never one about another order sharing that thread, sharing a rare word with the question, each with its permalink and the words shared."""
    if not (where := state.promise_thread(data["orders"][order_id])):
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
        if message.get("from_user")
        and message["ts"] != where[1]
        and (shared := sorted(asked & rare(message["text"])))
        and state.message_order(data, *where, message["ts"]) == order_id
    ]


def unoffered_refusal(said: list[dict], offered: str) -> str | None:
    """Why a question may not go to him: an amount a clause of the order's own words or of his message about it in its thread names, as "30 day bonding" names 30 days, is in neither its question nor an option, so he has no tap for what he named. Only a clause sharing a rare word with the question counts, since one message can name a cap of another subject beside a word the question uses."""
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
        "refused: his words on this order name an amount this question offers no tap for. Give each its own option,"
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
