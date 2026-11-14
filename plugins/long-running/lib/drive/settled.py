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

from drive import github, limits, slack, state, view
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
    " that part and ask the same question again, which redraws its card."
)
SAYS_QUESTION = (
    "Does this card, in its own words anywhere in it, say that its recommended option replaces, drops, changes or goes against"
    " what one of his recorded answers below says, so building it lifts those words of his? Pick that answer, or none when the"
    " card says its recommended option keeps every one of them or says nothing of the kind."
)
UNSAID = "It says none of that."
AGAIN_QUESTION = (
    "Does this card ask him again, in other words, the question this open card of his already asks, so that one answer of his"
    ' settles both? Answer no when it asks something that card leaves open, or a choice that card does not offer. That card asks: "{title}"'
)
MARKED = "Jev reads the recommended option as breaking it"
UNSETTLED = (
    'drive job ask: kept the card unsettled as "{unsettled}", asking him to lift {id}, {asked}, so it holds its PR from landing'
    ' on an incident too until he answers, by his pick "Hold the PR until you answer; the drive finds each card against your'
    ' recorded answers"; --unsettled "<id>: <why>" names other words of his it asks him to lift.'
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
ORDERS_QUESTION = (
    "Do his words order the release the action starts? They do when, read with the card they answer where it is given, they"
    " have that release run: his order to ship or release that work, or his pick on a card about that work reaching"
    " production, such as whether, when or how to release it or what the release carries or does as it runs, whose option"
    " has that release run. A pick that names a condition, such as once tests pass or after a fix merges, orders the release"
    " once it holds, which the drive sees to before it starts. A bare option number picks that option of the card he typed it"
    " on, and his words past it order only what they name, such as \"and then bring <a team> to the same\", which orders that"
    " team brought to where the picked option put the others. Pick none when the release ships more than his words order,"
    " another change, more components or other places; when his pick has no release run: his Approve or tap on a pull"
    " request, or a pick to land, merge or enqueue one, lands it and releases nothing, and so does a pick of which code or"
    " data layout to build, how to repair something, or whether a check holds; and when unsure."
)
UNORDERED = "Jev does not read it as approving this release"
ORDERED = (
    'drive job ask: his answer {id}, {asked}, orders the recommended option\'s `{release}`, which `drive job add --release'
    ' "{release} --order {id}"` starts with no card, so this card is kept unsettled on none of his words it carries out.'
)
LEADING_NUMBER = re.compile(r"\s*(\d+)\b(?![.,]\d)")
UNCHANGED = frozenset({"env", "without", "from", "order"})
TENANTS = re.compile(r"^tenants:\n((?:[ -].*(?:\n|$))*)", re.MULTILINE)
TEAM_NAME = re.compile(r"^[ -] (?:name|displayName): *(.+?) *$", re.MULTILINE)
REPEATS_QUESTION = (
    "A drive tooling order is being added in the words below. Does it ask for what one of these open drive tooling orders already"
    " asks, the same fault or the same change in other words, so it joins that order and the job building it instead of starting a"
    " second order, builder and card? Pick that order, or none when it asks for anything none of them asks, even about the same code."
)
APPROVAL = "kind:approval"
INCIDENT = "incident"
RELEASE_HOLD = "release-hold"
TAP_HEADER = "header:Tap"
TAP_QUESTION = re.compile(r"(?:PR )?(?:Taps?\b|Approv|Land\b).*?(?:#\d+|/pull/\d+)", re.IGNORECASE)
RARE = re.compile(r"[a-z][a-z0-9-]{4,}")
NOTES = "\nNotes:"
PROVISIONAL = "PROVISIONAL"
ELIDED = re.compile(r"\.\.\.|…|\[[^\[\]]*\]")
UNWORDED = re.compile(r"[\W_]+")
TERMINAL = "terminal"
HUMAN = b'"kind":"human"'
STEP = re.compile(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}")
EVENTS = "a GitHub pull request that merged or closed, a Buildkite build or the job its link names that finished, or a Slack message a teammate or another bot wrote"
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
    """Record his answer in cc-notes, the question as its title and his words as its body, and return its id."""
    add = ["ccn", "answer", "add", f"--title={title}", "--body-file", "-", *(f"--label={label}" for label in labels), "--json"]
    return json.loads(ccn(add, words, cwd, "could not record his answer in cc-notes"))["id"]


def supersede(pick: str, answer_id: str, cwd: str) -> None:
    """Record in cc-notes that his answer replaces his earlier pick, so ccn answer match, which every brief's rulings read, no longer ranks the pick as standing."""
    ccn(["ccn", "answer", "supersede", pick, "--by", answer_id], "", cwd, f"could not mark his pick {pick} superseded by his answer {answer_id} in cc-notes")


def kind(words: str) -> list[str]:
    """The kind label of his answer: a tap on a PR card, which answers only Approve or Hold, is an approval."""
    return [APPROVAL] if words in state.TAP_LABELS else []


def backfill(cwd: str) -> None:
    """Label as approvals the taps on PR cards recorded before `drive answer` labelled them, by the question alone, whatever his words: one opening with Tap, Approve or Land that names a PR, or one under a Tap header."""
    for answer in listed("scope:durable", cwd):
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
    """His words in a recorded answer's body: all of it above the drive's notes, which a Notes: line opens; none in a pick a lane made by its recommendation, which opens PROVISIONAL, since he never said it."""
    return "" if body.lstrip().startswith(PROVISIONAL) else body.partition(NOTES)[0].strip()


def heard(link: str, found: dict | None, data: dict) -> str | None:
    """His words at a Slack link: his answer on the card there, tapped or typed, as the drive's question drawn on it ended on them or else as cc-slack keeps it, or else the message there when a person wrote it; None for a card still asking him or one withdrawn, and for a message the drive or a bot posted."""
    if said := next((closed["words"] for _, question in view.drawn_on(data, link) if (closed := question["closed"]) and closed["how"] == "answered" and closed["words"]), None):
        return said
    if found and (closed := view.closed_card(found, link)) and closed["answer"] and not closed["answer"].get("withdrawn"):
        return closed["answer"]["label"]
    message = slack.message(link)
    return message["text"] if message.get("from_user") else None


def unsaid(kept: list[str], links: list[str], found: dict | None, data: dict) -> str | None:
    """Why the first of the words kept as his answer that are not his is not: no link names where he said them, what is at one is not his, his messages at several links sit in more than one thread, or what is at them lacks a piece of them, split where an ellipsis or a bracketed insertion leaves his words out; None when each piece of each is his, his messages in a row at the links read once for all of them and joined in link order. At terminal, his words are a prompt he typed into a session the drive binds that holds every piece."""
    read: list[str] = []
    for words in kept:
        if not plain(ELIDED.sub(" ", words)):
            return f"no words of his are given; {HIS_ANSWER}"
        if not links:
            return f'no permalink names where he said "{cut(words, 200)}"; {HIS_ANSWER}'
        if links == [TERMINAL]:
            if any(lacking(words, said) is None for said in typed(data)):
                continue
            return f'no prompt he typed into a terminal of the drive\'s sessions says "{cut(words, 200)}"; {HIS_ANSWER}'
        if not read:
            if apart := unthreaded(links):
                return f"{apart}; {HIS_ANSWER}"
            for link in links:
                try:
                    said = heard(link, found, data)
                except slack.SlackError as error:
                    return f"{link} could not be read for his words ({error}); {HIS_ANSWER}"
                if said is None:
                    return f"{link} is neither his own message nor a card he answered; {HIS_ANSWER}"
                read.append(said)
        if missing := lacking(words, "\n".join(read)):
            return f'his words at {", ".join(links)} do not say "{cut(missing, 200)}"; {HIS_ANSWER}'
    return None


def unthreaded(links: list[str]) -> str | None:
    """Why his messages at several links are no one answer of his: they sit in more than one thread and are not all at one channel's root, so an answer kept at the first link, which ends the cards of that link's thread alone, would leave a card under another thread asking him; None for one link."""
    if len(links) < 2:
        return None
    where = [state.CARD_LINK.search(link) for link in links]
    if all(where) and len({match[1] for match in where}) == 1 and (len(set(map(view.card_thread, links))) == 1 or not any(match[4] for match in where)):
        return None
    return f"his messages at {', '.join(links)} sit in more than one thread, and not all at one channel's root, so no one answer holds them: record each thread's messages as an answer of its own"


def unhappened(link: str) -> str | None:
    """Why no event at the link overtook a card, read by its host's reader; None for one of EVENTS. His own message withdraws a card only by his words there, and a post of the drive or of any agent, which share one bot, is no event."""
    try:
        if slack.MESSAGE_LINK.search(link):
            message = slack.message(link)
            if message.get("from_user"):
                return "it is his own message, which withdraws a card only by his words there, verbatim"
            return "it is a post of the drive or of another agent, never an event" if message.get("from_claude") else None
        if state.PR_URL.match(link):
            key = state.pr_key(link)
            read = github.nodes(github.graphql(github.query([key], "state"), f"whether {key} is still open"), [key])[key]
            return f"{key} is still open" if read["state"] == "OPEN" else None
        if build := github.BUILD.match(link):
            read = json.loads(github.run(["bk", "build", "view", build["number"], "-p", build["pipeline"], "-o", "json"]))
            step = STEP.search(link, build.end())
            ran = next((job for job in read.get("jobs", []) if job["id"] == step[0]), None) if step else read
            if ran is None:
                return f"{build['pipeline']} build {build['number']} has no job {step[0]}"
            return None if "finished_at" in ran else f"{'that job' if step else 'that build'} has not finished: it reads {ran['state']}"
    except (slack.SlackError, github.GithubError) as error:
        return f"it could not be read: {error}"
    return f"the drive reads no such event; {EVENTS} overtakes a card"


def lacking(words: str, said: str) -> str | None:
    """The first piece of the words, split where an ellipsis or a bracketed insertion leaves his words out, that what he said does not hold."""
    return next((piece.strip() for piece in ELIDED.split(words) if f" {plain(piece)} " not in f" {plain(said)} "), None)


def typed(data: dict) -> Iterator[str]:
    """Each prompt typed into a session the drive binds, read from its transcript by prompt_words; a row still being written is left for the next read."""
    for held in state.bound_sessions(data).values():
        path = next((config_dir() / "projects").glob(f"*/{held['session']}.jsonl"), None)
        if path is None or not path.stat().st_size:
            continue
        with path.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as rows:
            at = rows.find(HUMAN)
            while at != -1 and (end := rows.find(b"\n", at)) != -1:
                if (said := prompt_words(json.loads(rows[rows.rfind(b"\n", 0, at) + 1 : end]), "human")) is not None:
                    yield said
                at = rows.find(HUMAN, end)


def prompt_words(row: dict, origin: str) -> str | None:
    """The words of a transcript row that is a prompt of this origin, as cc-sync's ClassifyRecord reads human input: a user row or queued command whose origin Claude Code marks so, a human's only when it is no meta injection, while a coordinator's SendMessage comes as a queued command to an agent at work and as a meta user row to one that waits. A cci delivery's origin is its channel's and a tool result carries none, so neither is his; a prompt Orca types into a terminal is marked a human's too."""
    queued = row["type"] == "attachment" and row["attachment"].get("type") == "queued_command"
    if not queued and row["type"] != "user":
        return None
    prompt = row["attachment"] if queued else row
    if (prompt.get("origin") or {}).get("kind") != origin or origin == "human" and prompt.get("isMeta"):
        return None
    said = prompt["prompt"] if queued else row["message"]["content"]
    return said if isinstance(said, str) else " ".join(block["text"] for block in said if block.get("type") == "text")


def plain(text: str) -> str:
    """The words of a message as he wrote them, with no link markup, formatting, punctuation or case, so his words copied by hand match what Slack keeps."""
    return " ".join(UNWORDED.sub(" ", html.unescape(read(text))).casefold().split())


def shown(answer_id: str, cwd: str) -> dict:
    """One recorded answer of his, its title, body and labels."""
    return json.loads(ccn(["ccn", "answer", "show", answer_id, "--json"], "", cwd, f"could not read his answer {answer_id}"))


def listed(label: str, cwd: str) -> list[dict]:
    """His recorded answers carrying the label, newest first, each with its body and the time it was recorded; never one a later answer superseded."""
    found = json.loads(ccn(["ccn", "answer", "list", "--label", label, "--limit", "0", "--full", "--json"], "", cwd, f"could not read his answers labelled {label}"))
    return sorted(found, key=lambda answer: answer["created_at"], reverse=True)


def hold(cwd: str) -> dict | None:
    """His newest standing hold on every release, the answer labelled release-hold that no later answer of his superseded; None when none stands."""
    return next(iter(listed(RELEASE_HOLD, cwd)), None)


def whole_ids(typed: set[str], cwd: str) -> dict[str, str]:
    """The id cc-notes keeps each answer id typed here under, as `ccn answer show` resolves a unique prefix, read in one list of his answers, superseded ones included; an id naming no answer, or several, is its own."""
    every = ccn(["ccn", "answer", "list", "--limit", "0", "--include-superseded", "--json"], "", cwd, "could not read his recorded answers")
    known: dict[str, list[str]] = {}
    for answer in json.loads(every):
        known.setdefault(answer["id"][:7], []).append(answer["id"])
    return {answer_id: found[0] if len(found := [whole for whole in known.get(answer_id[:7], []) if whole.startswith(answer_id)]) == 1 else answer_id for answer_id in typed}


def matched(text: str, cwd: str) -> list[dict]:
    """The first CANDIDATES of his recorded answers ccn ranks nearest to an ASK's whole block, findings included, which reach a ruling whose subject only its findings name, such as a wait he ordered on another order, each as Jev reads his words beside the card; never a tap approving a PR, which rules only whether that PR lands, nor a provisional pick, which holds no words of his."""
    match = ["ccn", "answer", "match", "-", "--label", "scope:durable", "--nearest", "--limit", "-1", "--json"]
    found = json.loads(ccn(match, read(text), cwd, "could not check the question against his recorded answers"))
    return [{"id": answer["id"][:7], "asked": answer["title"], "said": said[:BODY_CHARS]} for answer in found if APPROVAL not in answer["tags"] and (said := his(answer["body"]))][:CANDIDATES]


def judged(case: str, asked: limits.Question, words: list[dict], cited: list[dict], cards: dict[str, str]) -> tuple[dict | None, list[str]]:
    """Print on stderr each part of a card Jev reads as going against his words, in one reading of the card against all of them at once, the recorded answers the card cites among them, and his rulings with the rule each sets beside them, at least DECIDES sure, with the words it breaks: an option his words or a ruling that speaks to every option rule out, an option of a design the card never works out to what it will look like, an amount or choice his words name that the card gives no tap for, a ruling the card itself breaks, and a cited answer the card says its recommended option replaces, drops, changes or goes against. Each finding refuses nothing, by his pick 320b7a7 "Advisory judge: drive job ask keeps every well-shaped card and prints Jev's finding to the agent; no card is refused on merits". His ruling on root causes is weighed on the card alone, never per option, since it speaks only to the option the card recommends and its Root cause line. Return the recorded answer or ruling of his Jev picks for the recommended option at least DECIDES sure, the cited answer the card says it goes against first, else one his words show it breaks, which the card then asks him to lift; None when Jev picks his words on the order, or none, and when Jev gives no reading, which leaves the card to him, said on stderr. In the same call Jev reads the card against each open card of his among `cards`, their titles by waits id, one yes or no each, and the waits ids of those it reads at least DECIDES sure as asking it again come back beside that answer, none when Jev gives no reading."""
    rules = limits.rule_words(case)
    near = {word["id"] for word in words}
    heard_words = [*words, *(answer for answer in cited if answer["id"] not in near)]
    offered = {UNDECIDED: UNBROKEN, **dict.fromkeys(word["id"] for word in [*heard_words, *rules] if word["id"] != limits.ROOT_CAUSE_WORDS["id"])}
    items = {word["id"]: word for word in [*heard_words, *rules, limits.WORKED_WORDS]}
    recommended = f"breaks{asked.options.index(asked.recommended) + 1}"
    parts = {f"{kind}{number}": f'option "{option}"' for kind in ("breaks", "worked") for number, option in enumerate(asked.options, 1)} | {"says": f'option "{asked.recommended}"', "untapped": "the card", "card": "the card"}
    questions = {
        **{
            f"breaks{number}": {"label": {"instructions": BREAKS_QUESTION.format(option=option, marked=" (recommended)" if option == asked.recommended else ""), "options": offered}}
            for number, option in enumerate(asked.options, 1)
        },
        **{
            f"worked{number}": {"label": {"instructions": WORKED_QUESTION.format(option=option), "options": {UNDECIDED: WORKED_OUT, limits.WORKED_WORDS["id"]: None}}}
            for number, option in enumerate(asked.options, 1)
        },
        **({"untapped": {"label": {"instructions": UNTAPPED_QUESTION, "options": {UNDECIDED: "The card offers a tap for each.", **dict.fromkeys(word["id"] for word in words)}}}} if words else {}),
        **({"says": {"label": {"instructions": SAYS_QUESTION, "options": {UNDECIDED: UNSAID, **dict.fromkeys(answer["id"] for answer in cited)}}}} if cited else {}),
        "card": {"label": {"instructions": CARD_QUESTION, "options": {UNDECIDED: "It breaks none of them.", **dict.fromkeys(rule["id"] for rule in rules)}}},
        **{f"again{number}": {"binary": {"instructions": AGAIN_QUESTION.format(title=title)}} for number, title in enumerate(cards.values(), 1)},
    }
    heard = [{key: item[key] for key in ("id", "asked", "said", "rule") if key in item} for item in items.values()]
    try:
        read = jev({"state": {"card": case, "his_words": heard}, "questions": questions})
    except Unheard as failure:
        print(f"drive job ask: Jev gave no reading of this question against his words, so it goes to him: {failure}", file=sys.stderr)
        return None, []
    repeated = [waits for number, waits in enumerate(cards, 1) if read.pop(f"again{number}")["p_yes"] >= DECIDES]
    for key, found in read.items():
        if found["choice"] in items and found["probabilities"][found["choice"]] >= DECIDES:
            item = items[found["choice"]]
            rule = f" Its rule: {item['rule']}" if "rule" in item else ""
            print(ADVISED.format(part=parts[key], id=item["id"], asked=cut(item["asked"], 200), said=cut(item["said"], QUOTED_CHARS), rule=rule), file=sys.stderr)
    picks = (found["choice"] for key in ("says", recommended) if (found := read.get(key)) and found["probabilities"][found["choice"]] >= DECIDES)
    return next((items[choice] for choice in picks if limits.ANSWER_ID.fullmatch(choice.removeprefix("ruling:"))), None), repeated


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


def unordered(answer: dict, release: str, data: dict) -> str | None:
    """Why his answer does not order this release, one `ci release start` or `hotfix`; None when it does, the one reading the release gates and `drive job ask` share. When the card his answer answers is kept, found through the link `drive answer` kept for it, and his leading option number or tap picks an option whose words name a release in backticks, code decides: his answer orders a release shipping the change one of those ships, every flag the same but its places, `--from` and `--order`, on no place beyond that option's but those his words past the number name, by env id or by the name or displayName its infra/envs file gives that place's team. Any other answer Jev reads once, his words beside that card, ordering only what they name, at the DECIDES bar; Unheard when Jev gave no answer."""
    from drive import brief

    said = his(answer["body"])
    asked = answered_card(data, answer["id"])
    if asked and (pick := picked_option(asked, said)):
        number, rest = pick
        option = asked.options[number - 1]
        if commands := brief.card_releases(f"{option}\n{asked.hints.get(option, '')}"):
            [flags] = brief.started(release)
            whys = [placed(flags, brief.started(command)[0], rest, data["roles"]["coordinator"]["cwd"]) for command in commands]
            if None in whys:
                return None
            return f"it picks option {number} of the card it answers, {', '.join(f'`{command}`' for command in commands)}, {whys[0]}"
    card = {"card": "\n".join([f"Question: {asked.title}", *asked.summary, *limits.offered(asked)])} if asked else {}
    found = picked(ORDERS_QUESTION, "His words order no such release.", {"action": f"start this production release: `{release}`"} | card, [{"id": answer["id"][:7], "asked": answer["title"], "said": said}])
    return None if found else UNORDERED


def answered_card(data: dict, answer_id: str) -> limits.Question | None:
    """The card his answer answers, as `drive job ask` kept it: a question drawn on the card at the link `drive answer` kept for his answer, ended answered, whose ASK file stands; None when none does."""
    link = (data["answers"].get(answer_id) or {}).get("link")
    files = [Path(question["file"]) for _, question in view.drawn_on(data, link) if question["file"] and (question["closed"] or {}).get("how") == "answered"] if link else []
    kept = next((file for file in files if file.is_file()), None)
    return limits.question_problems(limits.ask_block(kept.read_text()), kept=True)[0] if kept else None


def picked_option(asked: limits.Question, said: str) -> tuple[int, str] | None:
    """The option of the card his words pick, by its number leading them or by his tap, which keeps its words, with his words past the number; None when they pick none."""
    if (number := LEADING_NUMBER.match(said)) and 1 <= int(number[1]) <= len(asked.options):
        return int(number[1]), said[number.end() :]
    tapped = next((index for index, option in enumerate(asked.options, 1) if plain(option) == plain(said)), None)
    return (tapped, "") if tapped else None


def placed(release: dict, option: dict, rest: str, cwd: str) -> str | None:
    """Why a release does not ship what a card option's release ships, on the option's places and those his words past its number name; None when it does."""
    if {name: values for name, values in release.items() if name not in UNCHANGED} != {name: values for name, values in option.items() if name not in UNCHANGED}:
        return "and this release ships another change"
    if (added := beyond(release, option)) is None:
        return "and this release runs on every place it does not leave out, where that option names its own"
    words = f" {plain(rest)} "
    if unnamed := sorted(place for place in added if not any(f" {plain(name)} " in words for name in (place, *team_names(place, cwd)))):
        return f"and this release also runs on {', '.join(unnamed)}, which his words past its number do not name"
    return None


def beyond(release: dict, option: dict) -> set[str] | None:
    """The places a release runs on that a card option's does not, each by its env id; None when the release runs on every place but those it leaves out and the option names its own."""
    if not release.get("env"):
        return None if option.get("env") else set(option.get("without", ())) - set(release.get("without", ()))
    runs = set(release["env"]) - set(release.get("without", ()))
    return runs - (set(option["env"]) - set(option.get("without", ()))) if option.get("env") else runs & set(option.get("without", ()))


def team_names(place: str, cwd: str) -> list[str]:
    """The names of a place's team, each tenant's name and displayName in the place's infra/envs file of the drive's checkout; none for a place with no such file."""
    env = Path(cwd) / "infra" / "envs" / f"{place}.yaml"
    block = TENANTS.search(env.read_text()) if env.is_file() else None
    return TEAM_NAME.findall(block[1]) if block else []


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
