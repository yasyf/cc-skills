import json
import re
import subprocess
from pathlib import Path

from drive import limits, slack, state
from drive.render import cut
from drive.state import DriveError

MATCHES = 3
WINDOW = 20
APPROVAL = "kind:approval"
APPROVED = re.compile(r"Approve\b")
NAMES_PR = re.compile(r"#\d+|/pull/\d+")
RARE = re.compile(r"[a-z][a-z0-9-]{4,}")
COMMON = {
    "about", "above", "after", "again", "being", "below", "between", "could", "doing", "during", "every", "first", "having", "later",
    "maybe", "might", "never", "other", "ought", "right", "should", "since", "still", "their", "there", "these", "thing", "things",
    "those", "through", "today", "under", "until", "where", "which", "while", "would", "yours",
}


def kept(names: list[str], ask: str | None) -> bool:
    """An ASK that `drive job ask` kept in one of these drives, which it checked against his answers before keeping."""
    return bool(ask) and any(Path(ask).expanduser().resolve().is_relative_to(state.drive_dir(name).resolve() / "asks") for name in names)


def answers(card: str, whole: str, drives: list[str], cwd: str) -> list[dict]:
    """His durable answers that may already settle a question: those the part he reads names. TODO: once a ccn release with `answer match --nearest` is installed, add each drive's nearest to `whole` from `drives`."""
    return matched(card, cwd)


def matched(text: str, cwd: str, *flags: str) -> list[dict]:
    """His answers sharing rare words with the text, best first, but a tap approving a PR, which settles only whether that PR lands."""
    match = ["ccn", "answer", "match", "-", "--label", "scope:durable", *flags, "--limit", str(WINDOW), "--json"]
    found = json.loads(ccn(match, text, cwd, "could not check the question against his recorded answers"))
    return [answer for answer in found if APPROVAL not in answer["tags"]][:MATCHES]


def recorded(title: str, words: str, labels: list[str], cwd: str) -> str:
    """Record his answer in cc-notes, the question as its title and his words as its body, and return its short id."""
    add = ["ccn", "answer", "add", f"--title={title}", "--body-file", "-", *(f"--label={label}" for label in labels), "--json"]
    return json.loads(ccn(add, words, cwd, "could not record his answer in cc-notes"))["id"][:7]


def kind(words: str) -> list[str]:
    """The kind label of his answer: a tap on a PR card, which answers only Approve or Hold, is an approval."""
    return [APPROVAL] if words in state.TAP_LABELS else []


def backfill(cwd: str) -> None:
    """Label as approvals the taps on PR cards recorded before `drive answer` labelled them: his words open with Approve and the question names a PR."""
    listed = ccn(["ccn", "answer", "list", "--label", "scope:durable", "--limit", "0", "--full", "--json"], "", cwd, "could not read his recorded answers")
    for answer in json.loads(listed):
        if APPROVAL not in answer["tags"] and APPROVED.match(answer["body"].lstrip()) and NAMES_PR.search(answer["title"]):
            ccn(["ccn", "answer", "edit", answer["id"], "--add-label", APPROVAL], "", cwd, f"could not label his answer {answer['id'][:7]} an approval")


def ccn(argv: list[str], text: str, cwd: str, failure: str) -> str:
    try:
        done = subprocess.run(argv, input=text, cwd=cwd, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DriveError(f"{failure}: {error}") from None
    if done.returncode:
        raise DriveError(f"{failure}: {' '.join(done.stderr.split())}")
    return done.stdout


def matching(card: str, whole: str, drives: list[str], cwd: str, under: list[dict]) -> list[dict]:
    """His recorded answers that may already settle a question: those its words match, and each written under a card its job asked on."""
    found = answers(read(card), read(whole), drives, cwd)
    return found + [answer for answer in under if answer["id"][:7] not in {other["id"][:7] for other in found}]


def refusal(found: list[dict], why: str | None, override: str) -> str | None:
    """Why a question may not go to him: one of these recorded answers of his may settle it, and no reason names that answer and says why it does not."""
    ids = [answer["id"][:7] for answer in found]
    if not (unnamed := [short for short in ids if short not in (why or "")]):
        return None
    listed = "\n".join(f"- {answer['id'][:7]} {answer['title'].splitlines()[0]}" for answer in found)
    example = "; ".join(f"{short}: <why it does not settle this>" for short in ids)
    left_out = f" The reason given leaves out {', '.join(unnamed)}." if why else ""
    return (
        "refused: these recorded answers of his may already settle this question. Read each with `ccn answer show <id>`:"
        " a question one settles is never asked again, in any wording, and the work goes ahead on that answer. One that"
        " already names what you recommend, such as a service he said to build, settles what that thing does: build it."
        f' Only when none does, ask again with {override} one string that names every id below, such as {override} "{example}".'
        f"{left_out}\n{listed}"
    )


def open_card_refusal(said: list[dict], why: str | None) -> str | None:
    """Why --unsettled may not name his words under this job's card while that card still asks him: they answer it, so the card shows them first."""
    if not (named := [answer for answer in said if answer["id"][:7] in (why or "")]):
        return None
    listed = "\n".join(f'- {answer["id"][:7]} under {answer["card"]}: "{cut(answer["words"], 200)}" ({answer["link"]})' for answer in named)
    ids = " ".join(answer["id"][:7] for answer in named)
    return (
        "refused: --unsettled names his words under this job's own card, and that card still asks him. His words under a card"
        f" answer it: settle the card with `drive answer {ids}`, which shows his words on it, and the work goes ahead on them."
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


def thread_refusal(said: list[dict], why: str | None) -> str | None:
    """Why a question may not go to him: his words in the order's thread may settle it, and no reason names each of those messages by its permalink."""
    if not (unnamed := [message for message in said if f"p{message['key']}" not in (why or "")]):
        return None
    listed = "\n".join(f'- {message["link"]} "{cut(message["text"], 200)}" (shares {", ".join(message["shared"])})' for message in said)
    example = "; ".join(f"{message['link']}: <why it does not settle this>" for message in unnamed)
    return (
        "refused: his words in this order's thread may already settle this question. Read each: a question his words settle is"
        " never asked, and the work goes ahead on them. Only when none does, ask again with --unsettled naming each permalink"
        f' below and why it does not settle this, such as --unsettled "{example}".\n{listed}'
    )


def rare(text: str) -> set[str]:
    return {word for word in RARE.findall(read(text).lower()) if word not in COMMON}


def read(text: str) -> str:
    """The words of a question as he reads them: a Slack link as its label, other links and bare URLs left out, since every PR link shares github, pull and monorepo with each old answer that names a PR."""
    return limits.URL.sub(" ", limits.TOKEN.sub(" ", limits.LABELLED.sub(r" \1 ", text)))
