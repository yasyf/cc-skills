import html
import json
import mmap
import re
import subprocess
import sys
import time
from collections.abc import Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from functools import cache
from itertools import chain, zip_longest
from pathlib import Path
from typing import NamedTuple

from drive import github, limits, slack, state, view
from drive.install import config_dir
from drive.render import cut
from drive.state import DriveError

HAND_ASK = (
    "refused: this ask carries his card's ASK block, a first line opening `ASK ` or an `ask` or `waits` attr, and"
    " `drive job ask <j> <file>` keeps a job's question and draws his card on it in the same call, under the"
    " waits id it mints, so no session posts one: keep the question with it, a remote lane's ASK"
    " the watch did not keep with `drive job ask <j>`, and ask an open card of the job's again in new words with"
    " `--redraws <that card's waits id>`. A question to another session's lane is a cci `ask` with neither, which goes out"
)
BODY_CHARS = 1500
TITLE_BYTES = 256
QUOTED_CHARS = 400
CANDIDATES = 8
MODEL = "jev-1.14.0"
YES = 0.7
NO = 0.3
BACKS = 0.45
REQUEST_CHARS = 10_000
DECIDE_SECONDS = 20
REFUSAL_SECONDS = 3
REPEATS_ASKED = 30


class Noul(NamedTuple):
    """One yes-or-no question Jev answers with the probability of yes: what it asks of the request's state by backticked path, then what a yes and a no mean where the question alone leaves it open."""

    ask: str
    yes: str | None = None
    no: str | None = None


UNSAID_HERE = 'A bare option number answers only the card it was typed on, and a question of his rules nothing out unless it proposes a way, as "why not just X?" orders X.'
BREAKS = Noul("Do `words.{slot}.said` rule out `card.options[{index}]`, so building it goes against what he said?", no=UNSAID_HERE)
WORKED = Noul("Does `card.show_me` leave `card.options[{index}]` unworked, showing none of its real calls, the rows it writes, its flow or what a caller gets?")
RELEASES_ONLY = Noul("Does `card.text` ask only whether or when to release, land, run or hold something, offering no design to build?")
UNTAPPED = Noul(
    "Do `words.{slot}.said` name an amount or a choice for what `card.text` asks, such as 30 days or 64 rows, that no option or question of the card gives him a tap for?",
    no="The card taps each amount and choice they name, or the words are quoted from an alert, a log or another message.",
)
SAYS = Noul("Does `card.text` say its recommended option replaces, drops, changes or goes against `words.{slot}.said`?")
BREAKS_CARD = Noul(
    "Does `card.text` itself, apart from what any one option builds, break `words.{slot}.rule`?",
    no="A ruling on root causes speaks only to the recommended option and the Root cause line, so an option offered beside it breaks nothing.",
)
LINE = Noul("Does `lines.{line}` go against `answers.{answer}`, as `against` says?")
AGAINST = (
    "A line goes against his answer when it says no ruling of his covers an item, puts an item up for change, removal or his pick, or asks him what `said`, his answer to `asked`, given"
    " before the line, already orders or keeps as it is. It does not when it keeps what his answer orders or keeps, cites it as standing, or speaks of an item his answer neither orders nor keeps. " + UNSAID_HERE
)
LINE_KEY = "line"
LINE_CANDIDATES = 4
LINE_WORDS = 5
LIST_LINE = re.compile(r"^\s*(?:[-*•]\s+|\d+[.)]\s+|\|)(.*\w.*)$")
TABLE_RULE = re.compile(r"^[\s|:-]*$")
OVERRULED = 'the line "{line}" says no ruling of his covers, puts up for change or asks him again what his answer {id}, "{asked}"{link}, orders or keeps: "{said}"'
UNREAD_LINES = "Jev gave no reading of its lines against his recorded answers, so it goes on unread: {failure}"
OVERRULED_RULE = (
    "No line says no ruling of his covers, puts up for change or asks him again an item a recorded answer of his orders or keeps:"
    ' his words stand until he lifts them, by his words "Cloud tree own cap and skipping safe mode also ok I fucking said to do'
    ' those why are you undoing my work".'
)
AGAINST_CARD = Noul(
    "Does `card.text` itself, apart from what any one option builds, go against `words.{slot}.said`, such as asking him in the terms of a design his words set aside?",
    no="The card asks what his words leave open, or how to do what they say.",
)
REPLACES = Noul(
    "Does `answer.said`, his newer answer to `answer.asked`, replace `words.{slot}.said`, his earlier answer to `words.{slot}.asked`, so building to the earlier goes against the newer, as an order to design something from scratch replaces his rulings on the design it starts over?",
    no="The newer answer leaves the earlier standing beside it, or speaks to something else.",
)
DECIDING = Noul(
    "Does `answers.{answer}.said`, his answer to `answers.{answer}.asked`, choose one of `card.options` or make `card.question` moot, so the card asks him nothing his answer leaves open?",
    no='The card still asks something his answer does not settle. A bare option number answers only the card it was typed on, and a question of his answers no card, one proposing a way such as "could we X?" or "why not just X?" included.',
)
AGAIN = Noul("Does `card.text` ask him again, in other words, what `open.{slot}` asks, so one answer of his settles both?", no="It asks something that card leaves open, or a choice that card does not offer.")
ANSWERS_NOTHING = Noul(
    "Do `his_words`, written under `card`, only ask it questions or only remark on it, so they pick none of `card.options` and order no work?",
    yes='His words "Expand on 1 and 2 and how does the replica fit in?", "Why is 1 recommended over 2?" and "could we do 1 without re-uploading?" only ask, a way proposed as a question such as "why not just X?" included.',
    no="They pick an option, approve the recommended one, rule one out, or order the work in words that ask nothing.",
)
ADVISED = (
    "drive job ask: Jev reads {part} as breaking {id}, {asked}, \"{said}\".{rule} This finding refuses no card, by his pick"
    " 320b7a7 that drive job ask keeps every well-shaped card and prints Jev's finding to the agent: when it holds, rewrite"
    " that part and ask the same question again, which redraws its card."
)
UNSURE = (
    "drive job ask: Jev is unsure whether {part} goes against {id}, {asked}, \"{said}\": weigh it yourself, and rewrite that part when it"
    " does. This refuses no card, by his pick 320b7a7."
)
CHOSEN = (
    "build that option with no card, a reader returning DONE with `rulings:` quoting {id} and `build:` that option, and end a card"
    " already drawn for it on his words with drive job ask {job} --drop <the file its question is kept in> --answer {id}"
)
ANSWERED = (
    'drive job ask: Jev reads his answer {id} to "{asked}"{link} as already answering this card: "{said}". This refuses and skips'
    " no card, by his picks 320b7a7 and 679484. When his words choose the card's recommended option, ask nothing: " + CHOSEN + "."
    " Otherwise cite {id} in the card's summary, outside the sentence recommending an option, and ask the same question again,"
    " which redraws his card with his words there as its Already answered tap, so one tap settles it, by his pick 692284 \"Your"
    ' cited answer as a tap on the card: one tap settles it, nothing skipped unseen", and build on his words meanwhile.'
)
MARKED = "Jev reads {option} as breaking it"
TOOLING_SAID = (
    "This is getting too in the weeds for me use your fucking Brain and act like a frontier model when it comes to Meta tooling"
    " don't ask me every little thing ask my high level preferences (different than non-tooling changes); apply this everywhere now"
)
DETAIL = Noul(
    "Does `card.text`, asked on a drive tooling order, ask him only a detail of how the tooling does its work, such as which file,"
    " function, store, hook, verb, flag, check, judge, process or wait does it, or which branch, pin or commit holds a change, so by"
    " `ruling.said` its job decides it and builds the option it recommends?",
    no="Its options differ in what he gets: whether, where or when a post or card reaches him or what it looks like, a tap, gate or"
    " presence he owes, a cost he pays in money or in waiting on his work, a ruling of his the recommended option lifts, or the scope"
    " of work the drive takes on.",
)
DECIDED = "{option}: decided without you under your ruling on tooling details"
DECIDED_LINE = (
    '{file} keeps {job}\'s question decided on its recommended option, "{option}", with no card: Jev reads it as asking only a detail'
    " of drive tooling, which his ruling {ruling} leaves to the job, so build that option and open its PR as for work no card holds"
)
DECIDES = (
    'drive job ask: Jev reads this card as asking only a detail of drive tooling, which his ruling {ruling} leaves to the job, so asking'
    ' it keeps it decided on its recommended option, "{option}", with no card'
)
JEV_MARK = re.compile(re.escape(MARKED).replace(re.escape("{option}"), ".+") + r"\Z")
UNSETTLED = (
    'drive job ask: kept the card unsettled as "{unsettled}", asking him to lift {id}, {asked}, so it holds its PR from landing'
    ' on an incident too until he answers, by his pick "Hold the PR until you answer; the drive finds each card against your'
    ' recorded answers"; --unsettled "<id>: <why>" names other words of his it asks him to lift.'
)
SUPERSEDES = Noul("Does `answer.said`, his answer to a newer question of the same job, settle everything `open.{slot}` asks him?")
REPEATS = Noul(
    "Is `order` on the feature `open.{slot}` holds: the same fault or change, a slice of it, a fix to it or a step after it?",
    no="It asks for work that order is not about, even in the same code.",
)
ORDERS = Noul(
    "Do `answer.said`, read with `card`, have the release in `action` run?",
    yes=(
        "His order to ship or release that work, or his pick on a card about it reaching production whose option has that release run; a condition such as once tests pass orders it once it holds."
        " His words to get work built or tested on places that are not production, such as a sandbox or staging, order each release of it that runs on those places alone."
    ),
    no=(
        "His Approve or tap on a pull request, a pick to land, merge or enqueue one, or a pick of a design, a repair or whether a check holds releases nothing."
        " Words to get work built or tested order no release that runs on production."
    ),
)
SHIPS_MORE = Noul(
    "Does the release in `action` ship more than `answer.said` orders: another change, more components or other places?",
    no=(
        "His words past a bare option number order only what they name, such as a team brought to where the picked option put the others."
        " His words to get work built or tested on places order a release of that work there, on those places alone, whatever flags it starts with."
    ),
)
ASKS_RELEASE = Noul(
    "Does `record` ask the session it goes to, or a job or agent that session runs, to take a step of a production release itself: start, hotfix, resume, approve, rerun or roll back a release, now or once something holds?",
    yes="It asks for that step, whatever words of his it quotes.",
    no=(
        "It only reports a release that ran, is running or a teammate says they will run, states that an approval or answer of his is pending, his Approve on a"
        " pull request included, says nothing starts, quotes a command, plan or refusal, asks for a plan, a dry run or a card redrawn, or asks that session to"
        " put the release to him and start nothing until he answers. A step of drive work, such as resuming a job, rebasing a pull request or redrawing a card,"
        " is no release step, nor is a `tools/ci cluster-job` start or the unblock of the gate of the forge/release build it starts, which runs one suspended"
        " CronJob in a team's cluster and ships no change."
    ),
)
SPEAKS_TO_START = Noul(
    "Does release note `notes.{note}` speak to the release start `starts.{start}`, its rule binding a cut of the start's targets, or of a target the note says ships with them, on the places the start runs?",
    yes="It names a change such a cut must not carry, must carry first or must name as a hazard on his card, whatever commit the start cuts.",
    no="It is about other targets or places, only reports what a release did, or says its rule no longer binds.",
)
AFTER_INSTALL = Noul(
    "Does `goal`, the goal of a builder in one of his own repositories, name a step to take once its change is merged and installed, beyond proving that the installed change works, such as redrawing or posting a card, resuming or closing another job or order, or rerunning another job's proof?",
    no="Its only step after the install is that proof, or every step it names is part of the change itself.",
)
UNWORDED_RETURN = Noul(
    "Does `line`, written for a builder's or designer's brief, tell the job to end its turn or return on a line whose first word is none of DONE, READY, PUSHED, BUILT, WAITING, ASK, BLOCKED, DESIGN or HANDOFF?",
    yes="It orders one plain line with no result word, or one opening with a phrase of its own.",
    no="Every return it orders opens with one of those words, or it orders no return and only describes or quotes such a line, as a fix refusing one does.",
)
NARROWS = Noul(
    "Does `design` build less than `words.{slot}.said` name, or build part of it later, with no question in `forks` asking him about it?",
    yes="It leaves out, defers to a later change or trigger, or narrows what they name, such as building one of several strategies he approved while the others wait, or decides such a cut on its own because it is cheap to reverse.",
    no="It builds all they name, or a question in `forks` asks him about what it leaves out.",
)
CONTRADICTS = Noul(
    "Does a line of `design` keep, reuse or build on what `words.{slot}.said` set aside, or otherwise go against them, such as reusing the code that exists where his words order it built from scratch?",
    no="Each line does what his words say or what they leave open, and naming code that exists as evidence of what must keep working goes against nothing.",
)
CONTRADICTING = Noul(
    "Does `design.{line}` keep, reuse or build on what `words.said` set aside, or otherwise go against them?",
    no="The line does what his words say or what they leave open, or names code that exists only as evidence of what must keep working.",
)
STRATEGIES = Noul("Do `words` or `design` name several strategies for one job, such as the partition strategies a table picks by its usage?")
ABSTRACTION_FIRST = Noul(
    "Is the first change of `design` the one abstraction every one of its strategies goes through, walked through each strategy on paper, with each strategy's change waiting on it?",
    no="Its first change builds one strategy, or routes each strategy as its own case, before their shared abstraction.",
)
LEAVES = Noul(
    "Does `lines.{slot}`, a line of a builder's return, leave a piece of the work `goal` names unbuilt for later, to a follow-up PR, another job or builder or a later change, as a piece its PR leaves out under Against the plan does?",
    no="Each piece it names is built, ruled out or never asked for by his words, is left to this same job's next event, or is only a proof, check, review, merge, install or release still to run, or an idea beyond the plan.",
)
NAMES_PIECE = Noul(
    "Does `parts.{part}`, work a job of its own builds, name the piece `lines.{slot}` leaves unbuilt, that piece itself in any wording?",
    no="It names a larger piece of work holding that piece, or another piece.",
)
RESTATES = Noul(
    "Does `lines.{slot}`, an UNBUILT: line of a builder's later return, name the same piece of work as `earlier.{part}.line`, a piece that same builder's earlier return left unbuilt and that `earlier.{part}.builds` now builds, restated in other words?",
    yes="Building what that earlier piece's `builds` names builds what the line names.",
    no="It names a different piece, or a piece beside or inside it that the earlier piece's `builds` leaves out.",
)
STEPS = Noul(
    "Does `stories.{slot}` list steps on `surface`, or name a part to walk, with no result he must then see?",
    yes='Such as "open the run page, click a step" or "walk each part the earlier brief names".',
    no="It says what he does there, such as a click, key or hover, and what he must then see.",
)
LOOKS = Noul(
    "Do `options` differ in how a screen someone sees looks, such as where rows draw in a sidebar, a page's layout, where a control sits or how it is styled, or an icon, so that he must see each option drawn to pick one?",
    no="They differ in something he reads about rather than sees, such as where data is stored, which code runs, a schedule, a rule, a process, a message's wording or a cost, even when the work behind them ends up on a screen.",
)
ARCHITECTURES = Noul(
    "Do `options` choose between architectures, differing in which components, services, processes or stores run, where data lives or how they connect, so that he must compare a diagram of each option's components to pick one?",
    no="They share one set of components wired the same way and differ only in a value, a rule, a schedule, a message's wording, a cost, a step's order inside one component or how a screen looks.",
)
UNEXPLAINED = Noul(
    "He knows the product but not each table, column, function, file, migration or flag in its code. Does the question card in `question`, `summary`, `options` and `show_me` leave him asking what `names.{name}` is for?",
    yes=(
        "Nowhere on the card, in its question, summary, options, their descriptions or its Show me, does a clause say what it is for: what a table records, what a"
        ' column holds, what a function, file or route does, what a migration changes, what a flag turns on. A phrase that only repeats its name or kind, such as'
        ' "two run_claims columns" or "the X migration", says nothing.'
    ),
    no="Such a clause is there, even a short one, or the name is a word or product anyone reads.",
)
COINED = Noul(
    "He owns the product but has read none of its code and none of the names the drive's agents coin among themselves, and reads the question card in `question`, `summary`, `options` and `show_me` cold. Does it use any of `names` or `coined`, or another name its writer coined for a rule, check, gate, line, judge or kind of thing, as if he already knew it, so that he would have to ask what it is before he could answer?",
    yes="One is never said in everyday words on the card, or is said only through other code or coined names, its own name or kind, or one example.",
    no="Each is said so, even briefly, or is an everyday word in its ordinary sense, a product's name or a teammate's name.",
)
ILLUSTRATES = Noul(
    "Does `code`, a fence in `language` about to be drawn as a picture of code on his Slack card, illustrate something the card's words cannot say as well, such as the shape of a function, a type, a query or several statements working together?",
    no="It is trivial: a value flipped, such as write = 0 becoming write = 1, an argument or list item added or dropped, a name or path changed, or one declaration, which a sentence says as well.",
)
UNORDERED = "Jev does not read it as approving this release"
ORDERED = (
    "drive job ask: his answer {id}, {asked}, orders the recommended option's `{release}`, so his card offers his words as its"
    ' Already answered tap: his one tap settles the card on them, and `drive job add --release "{release} --order {id}"` starts that'
    " release; the card is kept unsettled on none of his words it carries out."
)
LEADING_NUMBER = re.compile(r"\s*(\d+)\b(?![.,]\d)")
UNCHANGED = frozenset({"env", "without", "from", "commit", "order"})
SOAKED = frozenset({"targets", "only", "stack"})
STARTS = frozenset({"emergency", "manual", "rollback"})
PROSE_FLAGS = frozenset({"targets", *UNCHANGED, "emergency"})
TENANTS = re.compile(r"^tenants:\n((?:[ -].*(?:\n|$))*)", re.MULTILINE)
TEAM_NAME = re.compile(r"^[ -] (?:name|displayName): *(.+?) *$", re.MULTILINE)
STAGE = re.compile(r"^stage: *(\S+) *$", re.MULTILINE)
PROD = re.compile(r"^prod: *true *$", re.MULTILINE)
STAGING = "staging"
PRODUCTION = "production, every environment except staging"
ASKS_PAGE = Noul(
    "Does `order` name a goal he wants worked toward or prioritized, such as \"let's prioritize\" a project, link or outcome or getting a set of changes merged,"
    " ask where a project, feature or body of work stands, or ask for a page or dashboard of it?",
    yes="He follows that work over days, and the drive builds a live page of it.",
    no="It orders one bounded thing built, fixed, released or answered, asking nothing of where the larger work stands.",
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
EVENTS = (
    "a GitHub pull request that merged or closed, a Buildkite build or the job its link names that finished, a Slack message a"
    " teammate or another bot wrote, or, on a card asking him for a fact or value, the done drive job, by its id, whose report names the card and the value it read"
)
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


def recorded(name: str, title: str, words: str, labels: list[str], where: str, cwd: str) -> str:
    """Record his answer on the drive in cc-notes, the question as its title as `titled` fits it, his words as its body with the whole question in its notes when its title is cut, and the drive's labels before these, superseding by it each answer of the drive's still standing under that same question, so his changed pick leaves one answer per question for ccn answer match to rank, and return its id; a standing answer cc-notes holds already under that title in those words, as a tap, a handler or an earlier call recorded it, is that answer, so its id comes back and nothing is added. A refusal names his words, the question and the card or message where he gave them."""
    handle = titled(title)
    said = (plain(handle), plain(words))
    if kept := next((answer["id"] for answer in listed("scope:durable", cwd) if (plain(answer["title"]), plain(his(answer["body"]))) == said), None):
        return kept
    body = words if handle == title else f"{words}{NOTES} the whole question: {title}"
    standing = [answer["id"] for answer in listed(f"drive:{name}", cwd) if state.same_question(answer["title"], handle)]
    add = ["ccn", "answer", "add", f"--title={handle}", "--body-file", "-", *(f"--label={label}" for label in ["scope:durable", f"drive:{name}", *kind(words), *labels]), "--json"]
    answer_id = json.loads(ccn(add, body, cwd, f'could not record in cc-notes his answer "{cut(words, 80)}" to "{cut(title, 120)}" on {where}'))["id"]
    for pick in standing:
        supersede(pick, answer_id, cwd)
    return answer_id


def titled(question: str) -> str:
    """The question as the cc-notes title of his answer to it: whole when it fits the TITLE_BYTES `ccn answer add` takes, else cut on a whole word to fit, since ccn refuses a longer title and records nothing."""
    if len(question.encode()) <= TITLE_BYTES:
        return question
    chars = TITLE_BYTES
    while len(cut(question, chars).encode()) > TITLE_BYTES:
        chars -= 1
    return cut(question, chars)


def note_cards(name: str) -> None:
    """Record in cc-notes each answer of his that ended a question before his tap recorded it, as the tap records one through `recorded`, and keep its id on the question, so `matched` ranks every answer he gave a card, never an Already answered tap, whose words cc-notes holds already; `recorded` keeps the id of one cc-notes holds already under that question in those words, so a pass cut short records none twice; marked so this runs once."""
    data = state.load(name)
    if data.get(state.CARDS_NOTED):
        return
    cwd = data["roles"]["coordinator"]["cwd"]
    noted = [
        (job_id, question, recorded(name, question["title"], closed["words"], [], closed["link"] or question["card"] or (f"his card {question['waits']}" if question["waits"] else f"record {closed['seq']}"), cwd))
        for job_id, job in data["jobs"].items()
        for question in job["questions"]
        if (closed := question["closed"]) and closed["how"] == "answered" and closed["words"] and not (closed["answer"] or closed["ruling"] or closed["noted"]) and (question["card"] or question["waits"] or closed["seq"])
        and closed["words"] != "\n".join(dict.fromkeys(answer["words"] for answer in question["recorded"]))
    ]
    with state.edit(name) as data:
        for job_id, question, answer_id in noted:
            state.question_in(data["jobs"][job_id], question)["closed"]["noted"] = answer_id
        data[state.CARDS_NOTED] = True


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


def unhappened(link: str, card: str, name: str) -> str | None:
    """Why no event at the link overtook the card, read by its host's reader; None for one of EVENTS. His own message withdraws a card only by his words there, and a post of the drive or of any agent, which share one bot, is no event. A drive job's id is one only once that job is done, its report names the card, and the card's open question asks him for a fact or value, by `limits.fact_problems`, which that job's own read takes the place of."""
    if state.JOB_ID.fullmatch(link):
        data = state.load(name)
        if (job := data["jobs"].get(link)) is None:
            return f"the drive has no job {link}"
        if job["state"] != "done":
            return f"job {link} is not done: it reads {job['state']}"
        if card not in state.report_text(name, link):
            return f"job {link}'s report never names {card}, so nothing shows its read was the value that card asks him for"
        asked = (limits.redrawn(Path(question["file"]).read_text()) for _, question in view.drawn_on(data, card) if question["closed"] is None and question["file"])
        return None if any(map(limits.fact_problems, asked)) else "the card asks him for no fact or value, the one question a job's own read overtakes"
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
            read = github.build(build["pipeline"], build["number"])
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
    """One recorded answer of his, its title, body and labels, none when cc-notes prints no tags key, as it does for an answer with no labels."""
    return {"tags": [], **json.loads(ccn(["ccn", "answer", "show", answer_id, "--json"], "", cwd, f"could not read his answer {answer_id}"))}


def listed(label: str, cwd: str) -> list[dict]:
    """His recorded answers carrying the label, newest first, each with its body and the time it was recorded; never one a later answer superseded."""
    found = json.loads(ccn(["ccn", "answer", "list", "--label", label, "--limit", "0", "--full", "--json"], "", cwd, f"could not read his answers labelled {label}"))
    return sorted(found, key=lambda answer: answer["created_at"], reverse=True)


def hold(cwd: str) -> dict | None:
    """His newest standing hold on every release, the answer labelled release-hold that no later answer of his superseded; None when none stands."""
    return next(iter(listed(RELEASE_HOLD, cwd)), None)


def every(cwd: str) -> list[dict]:
    """Every recorded answer of his, superseded ones included, each with the answers superseding it, in one list."""
    return json.loads(ccn(["ccn", "answer", "list", "--limit", "0", "--include-superseded", "--json"], "", cwd, "could not read his recorded answers"))


def whole_ids(typed: set[str], cwd: str) -> dict[str, str]:
    """The id cc-notes keeps each answer id typed here under, as `ccn answer show` resolves a unique prefix, read in one list of his answers, superseded ones included; an id naming no answer, or several, is its own."""
    known: dict[str, list[str]] = {}
    for answer in every(cwd):
        known.setdefault(answer["id"][:7], []).append(answer["id"])
    return {answer_id: found[0] if len(found := [whole for whole in known.get(answer_id[:7], []) if whole.startswith(answer_id)]) == 1 else answer_id for answer_id in typed}


def replaced(cwd: str) -> set[str]:
    """The ids of his answers cc-notes marks superseded, by his changed pick on a card or anyone's `ccn answer supersede`, read in one list of every answer of his; cc-notes leaves `superseded_by` off an answer nothing superseded."""
    return {answer["id"] for answer in every(cwd) if "superseded_by" in answer}


def matched(texts: list[str], data: dict) -> list[dict]:
    """The first CANDIDATES of his recorded answers ccn ranks nearest to each text in turn, once each, with where he gave it: for an ASK its card's question, summary and options, which reach his answer to the question it asks again in other words, then its whole block, findings included, which reach a ruling whose subject only its findings name, such as a wait he ordered on another order, each as Jev reads his words beside the card; never a tap approving a PR, which rules only whether that PR lands, nor a provisional pick, which holds no words of his."""
    match = ["ccn", "answer", "match", "-", "--label", "scope:durable", "--nearest", "--limit", "-1", "--json"]
    found: dict[str, dict] = {}
    for text in texts:
        ranked = json.loads(ccn(match, read(text), data["roles"]["coordinator"]["cwd"], "could not check the question against his recorded answers"))
        for answer in [answer for answer in ranked if APPROVAL not in answer["tags"] and his(answer["body"])][:CANDIDATES]:
            found.setdefault(answer["id"][:7], {"id": answer["id"][:7], "asked": answer["title"], "said": his(answer["body"])[:BODY_CHARS], "link": given_at(data, answer)})
    return list(found.values())


def list_lines(text: str) -> list[str]:
    """Each line of a list in the text, a bullet, a numbered item or a table row other than its rule, without its marker, once each."""
    return list(dict.fromkeys(found[1].strip(" |") for line in text.splitlines() if (found := LIST_LINE.match(line)) and not TABLE_RULE.fullmatch(line)))


def line_answers(lines: list[str], data: dict, besides: frozenset[str] = frozenset()) -> list[tuple[str, list[dict]]]:
    """Each line beside the first LINE_CANDIDATES of his recorded answers `ccn answer match` ranks nearest to it and the first LINE_CANDIDATES `ccn answer search` finds for its first LINE_WORDS words, once each, nearest first with the two rankings taken in turn, with where he gave it, never one besides names, a tap approving a PR nor a provisional pick."""
    cwd = data["roles"]["coordinator"]["cwd"]
    failure = "could not check its lines against his recorded answers"
    lined = []
    for line in lines:
        words = read(line)
        match = ccn(["ccn", "answer", "match", "-", "--label", "scope:durable", "--nearest", "--limit", "-1", "--json"], words, cwd, failure)
        search = ccn(["ccn", "answer", "search", " ".join(words.split()[:LINE_WORDS]), "--label", "scope:durable", "--limit", "0", "--json"], "", cwd, failure)
        matched, searched = ([answer for answer in json.loads(ranked) if APPROVAL not in answer["tags"] and his(answer["body"]) and answer["id"][:7] not in besides][:LINE_CANDIDATES] for ranked in (match, search))
        found: dict[str, dict] = {}
        for answer in (answer for pair in zip_longest(matched, searched) for answer in pair if answer):
            found.setdefault(answer["id"][:7], {"id": answer["id"][:7], "asked": answer["title"], "said": his(answer["body"])[:BODY_CHARS], "link": given_at(data, answer)})
        lined.append((line, list(found.values())))
    return lined


def line_items(lined: Sequence[tuple[str, list[dict]]]) -> Iterator[tuple[dict, dict[str, Noul]]]:
    """Each line beside each recorded answer of his found for it as an item of `fill`, every line's nearest answer before any line's next, the line and the answer whole with one LINE Noul keyed by the line's number and the answer's id, beside AGAINST, which the request holds once, so a request with no room for every answer still reads each line against its nearest."""
    pairs = sorted(((rank, number, line, answer) for number, (line, found) in enumerate(lined, 1) for rank, answer in enumerate(found)), key=lambda pair: pair[:2])
    for _, number, line, answer in pairs:
        yield {"against": AGAINST, "lines": {f"l{number}": line}, "answers": {answer["id"]: {"asked": answer["asked"], "said": answer["said"]}}}, {f"{LINE_KEY}{number}.{answer['id']}": asking(LINE, line=f"l{number}", answer=answer["id"])}


def line_read(lined: Sequence[tuple[str, list[dict]]]) -> list[tuple[str, dict, float]]:
    """Each line beside the recorded answer of his Jev reads likeliest as going against it or asking him again, and how sure Jev is of it, NO or more, in as many requests as batched needs of a LINE Noul for each line beside each recorded answer of his found for it; a line Jev reads under NO for every answer is left out; Unheard when Jev gives no reading."""
    read = jev_all(batched({}, {}, line_items(lined)))
    picks = []
    for number, (line, found) in enumerate(lined, 1):
        sure = {answer["id"]: read[key] for answer in found if (key := f"{LINE_KEY}{number}.{answer['id']}") in read}
        if not sure:
            continue
        best = max(sure, key=sure.__getitem__)
        if reading(sure[best]) is not False:
            picks.append((line, next(answer for answer in found if answer["id"] == best), sure[best]))
    return picks


def overruled(lines: list[str], data: dict) -> list[tuple[str, dict]]:
    """Each of these lines `line_read` reads at YES or more, against the recorded answers of his `line_answers` finds for it, as saying no ruling of his covers, putting up for change or asking him again an item that answer, given before it, orders or keeps, beside that answer; Unheard when Jev gives no reading."""
    return [(line, answer) for line, answer, sure in line_read(line_answers(lines, data)) if reading(sure)]


def overruling(picks: list[tuple[str, dict]]) -> str:
    """Each overruled line beside his answer it goes against, by its id and his words."""
    return "; ".join(
        OVERRULED.format(line=cut(line, 200), id=answer["id"], asked=cut(answer["asked"], 200), link=f" ({answer['link']})" if answer["link"] else "", said=cut(answer["said"], QUOTED_CHARS))
        for line, answer in picks
    )


def given_at(data: dict, answer: dict) -> str | None:
    """Where he gave this recorded answer of his, as cc-notes prints it with its labels: the link `drive answer` kept for it in this drive, else the card or message of the question it ended here, else the link `drive answer` kept for it in each drive on this Mac its `drive:<name>` labels name, where it recorded the answer, as `answered_card` reads them, so a ruling he gave on one drive's card links his message from every drive."""
    short = answer["id"][:7]
    labelled = (name for tag in answer["tags"] if (name := tag.removeprefix("drive:")) != tag and state.exists(name))
    return (
        next((kept["link"] for whole, kept in data["answers"].items() if whole[:7] == short), None)
        or next((closed["link"] for job in data["jobs"].values() for question in job["questions"] if (closed := question["closed"]) and closed["link"] and short in {(closed["answer"] or "")[:7], (closed["noted"] or "")[:7]}), None)
        or next((kept["link"] for name in labelled for whole, kept in state.stored(name, ("answers",)).get("answers", {}).items() if whole[:7] == short and kept["link"]), None)
    )


def judged(
    case: str,
    asked: limits.Question,
    words: list[dict],
    cited: list[dict],
    cards: dict[str, str],
    settling: frozenset[str] = frozenset(),
    lines: Sequence[tuple[str, list[dict]]] = (),
) -> tuple[tuple[dict, str] | None, list[str], dict | None]:
    """Print on stderr each part of a card Jev reads as going against his words, in as many requests as batched needs to hold the card beside every item whole, the recorded answers the card cites, his rulings `limits.rule_words` names beside them, then his other words, his spec on the feature newest first among them, by brief.spec: one Noul for each option and each of his words or rulings that could rule it out, each cited answer the card could say it goes against, each of his words or rulings the card itself, apart from any one option, could go against, since a card asking in the terms of a design his words set aside asks him to lift them, each of his words naming an amount or choice the card could leave untapped, each option the card could leave unworked, and each open card of his among `cards` it could ask again, and beside it `line_read` of each line of the card `lines` holds against the recorded answers of his `line_answers` found for it, a request of its own so the card's words never crowd its lines out. A Noul at YES or more is a finding and one in the band between NO and YES is printed as unsure, and each refuses nothing, by his pick 320b7a7 "Advisory judge: drive job ask keeps every well-shaped card and prints Jev's finding to the agent; no card is refused on merits". His ruling on root causes is weighed on the card alone, never per option, since it speaks only to the option the card recommends and its Root cause line, and an option left unworked is no finding on a card asking only whether or when to release something. Return the recorded answer or ruling of his the card asks him to lift, beside the option that breaks it, at YES or more: the cited answer the card says its recommended option goes against first, else one his words show the recommended option breaks, else the first that rules out any other option, since offering an option his words rule out asks him to lift them too, but never by another option alone an answer that already answers the card, one of `settling`, which its Already answered tap carries, or one Jev reads at YES or more as already ordering or keeping what a line of the card asks, since an option his answer did not choose asks him to lift nothing; None when Jev reads none, and when Jev gives no reading, which leaves the card to him, said on stderr. Beside it come the waits ids of the open cards Jev reads at YES or more as asked again, and last the answer of his Jev reads likeliest for a line of the card, at YES or more, as already ordering or keeping what the line asks, with where he gave it, for `drive job ask` to print with its exits, one read in the band between NO and YES printed once as unsure, refusing and skipping nothing, by his picks 320b7a7, 679484 and 692284."""
    rules = limits.rule_words(case)
    near = {word["id"] for word in words}
    cited_ids = {answer["id"] for answer in cited}
    ordered = [*cited, *rules, *(word for word in words if word["id"] not in cited_ids)]
    items = {word["id"]: word for word in [*ordered, limits.WORKED_WORDS]}
    slots = {key: f"w{number}" for number, key in enumerate(items, 1)}
    options = asked.options
    parts = {f"{kind}{number}": f'option "{option}"' for kind in ("breaks", "worked") for number, option in enumerate(options, 1)} | {"says": f'option "{asked.recommended}"', "untapped": "the card", "card": "the card"}
    base = {"card": {"text": case, "options": options, "recommended": asked.recommended, "show_me": asked.show_me}, "open": {f"a{number}": title for number, title in enumerate(cards.values(), 1)}}
    fixed = (
        {"releases": RELEASES_ONLY}
        | {f"worked{number}.{limits.WORKED_WORDS['id']}": asking(WORKED, index=number - 1) for number in range(1, len(options) + 1)}
        | {f"again{number}": asking(AGAIN, slot=f"a{number}") for number in range(1, len(cards) + 1)}
    )

    def nouls(word: dict) -> dict[str, Noul]:
        slot, key = slots[word["id"]], word["id"]
        breaks = {} if key == limits.ROOT_CAUSE_WORDS["id"] else {f"breaks{number}.{key}": asking(BREAKS, slot=slot, index=number - 1) for number in range(1, len(options) + 1)}
        return (
            breaks
            | ({f"untapped.{key}": asking(UNTAPPED, slot=slot)} if key in near else {})
            | ({f"says.{key}": asking(SAYS, slot=slot)} if key in cited_ids else {})
            | {f"card.{key}": asking(BREAKS_CARD if "rule" in word else AGAINST_CARD, slot=slot)}
        )

    weighs = (({"words": {slots[word["id"]]: {field: word[field] for field in ("asked", "said", "rule") if field in word}}}, nouls(word)) for word in ordered)
    try:
        read = jev_all(batched(base, fixed, weighs))
        picked = line_read(lines)
    except Unheard as failure:
        print(f"drive job ask: Jev gave no reading of this question against his words, so it goes to him: {failure}", file=sys.stderr)
        return None, [], None
    repeated = [waits for number, waits in enumerate(cards, 1) if read[f"again{number}"] >= YES]
    designs = reading(read["releases"]) is not True
    answers = [(answer, sure) for _, answer, sure in picked if reading(sure)]
    answered = max(answers, key=lambda pick: pick[1])[0] if answers else None
    answering = {*settling, *(answer["id"] for answer, _ in answers)}
    shown = set(answering)
    for line, answer, _ in picked:
        if answer["id"] not in shown:
            shown.add(answer["id"])
            print(UNSURE.format(id=answer["id"], asked=cut(answer["asked"], 200), said=cut(answer["said"], QUOTED_CHARS), part=f'the line "{cut(line, 200)}"'), file=sys.stderr)
    for key, sure in read.items():
        question, _, word = key.partition(".")
        if not word or question.startswith("worked") and not designs or (found := reading(sure)) is False:
            continue
        item = items[word]
        rule = f" Its rule: {item['rule']}" if "rule" in item else ""
        print((ADVISED if found else UNSURE).format(part=parts[question], id=item["id"], asked=cut(item["asked"], 200), said=cut(item["said"], QUOTED_CHARS), rule=rule), file=sys.stderr)
    recommended = f"breaks{options.index(asked.recommended) + 1}"
    marks = [("says", "the recommended option"), (recommended, "the recommended option"), *((key, parts[key]) for key in parts if key.startswith("breaks") and key != recommended)]
    picks = ((option, word) for part, option in marks for key, sure in read.items() if key.partition(".")[0] == part and sure >= YES for word in [key.partition(".")[2]] if part in ("says", recommended) or word not in answering)
    return next(((items[word], option) for option, word in picks if limits.ANSWER_ID.fullmatch(word.removeprefix("ruling:"))), None), repeated, answered


def deciding(asked: limits.Question, answers: list[dict]) -> list[dict]:
    """The recorded answers of his Jev reads, at YES or more, as deciding the card's question or making it moot, read in one request against the card's question and options, one Noul an answer keyed by its id, each answer's words filled whole in the order given; none when Jev gives no reading, said on stderr, and an answer in the band between NO and YES, or one the request has no room for, decides nothing."""
    if not answers:
        return []
    card = {"question": asked.title, "options": [option for option in asked.options if option != limits.ALREADY_ANSWERED]}
    items = (({"answers": {answer["answer"]: {"asked": answer["asked"], "said": answer["words"]}}}, {f"decides.{answer['answer']}": asking(DECIDING, answer=answer["answer"])}) for answer in answers)
    told, questions, _ = fill({"card": card}, {}, items)
    try:
        read = jev(told, questions) if questions else {}
    except Unheard as failure:
        print(f"drive: Jev gave no reading of which recorded answers of his decide this question, so its card draws no {limits.ALREADY_ANSWERED} option: {failure}", file=sys.stderr)
        return []
    return [answer for answer in answers if reading(read.get(f"decides.{answer['answer']}", 0.0))]


def detail(case: str, words: list[dict]) -> bool:
    """Whether a card on a drive tooling order asks him only a detail its job decides, by his ruling state.TOOLING_DETAILS_RULING, as Jev reads it at YES or more in one request holding the card, the ruling and, filled by whole items, his words; one Jev reads in the band between NO and YES, or gives no reading of, goes to him, the latter said on stderr."""
    items = (({"words": {f"w{number}": {field: word[field] for field in ("asked", "said", "rule") if field in word}}}, {}) for number, word in enumerate(words, 1))
    request, questions, _ = fill({"card": {"text": case}, "ruling": {"id": state.TOOLING_DETAILS_RULING, "said": TOOLING_SAID}}, {"detail": DETAIL}, items)
    try:
        read = jev(request, questions)
    except Unheard as failure:
        print(f"drive job ask: Jev gave no reading of whether this tooling card asks him only a detail, so it goes to him: {failure}", file=sys.stderr)
        return False
    return reading(read["detail"]) is True


def tooling_decided(option: str, link: str | None) -> dict:
    """How a drive tooling job's question ends when its job decides it by his ruling state.TOOLING_DETAILS_RULING: answered by that ruling on its recommended option."""
    return state.new_close("answered", DECIDED.format(option=option), link, ruling=state.TOOLING_DETAILS_RULING)


def decided(title: str, words: str, older: dict[str, dict]) -> set[str]:
    """The older questions of a job that his answer to its newer one settles whole, as Jev reads it in one request, one Noul a question, each read at YES or more; Unheard when Jev gave no reading."""
    shorts = {key.rpartition(":")[2]: key for key in older}
    items = (({"open": {short: {"asked": older[key]["title"], "options": older[key]["options"]}}}, {f"supersedes.{short}": asking(SUPERSEDES, slot=short)}) for short, key in shorts.items())
    told, questions, _ = fill({"answer": {"asked": title, "said": words[:BODY_CHARS]}}, {}, items)
    return {shorts[key.partition(".")[2]] for key, sure in jev(told, questions).items() if reading(sure)}


def repeats(data: dict, words: str, tooling: bool) -> str | None:
    """The open order whose feature these new words are on, as Jev reads them in one request against the REPEATS_ASKED open build orders on their side, drive tooling's or his work's, whose title and words share the most rare words with them, one Noul an order, the orders filled whole in that order: the one Jev reads surest, at YES or more; an incident joins only by its --joins, and an answer order is no feature. None when no such order is open or Jev reads none at YES, an order in the band between NO and YES named on stderr for --joins, refused when Jev gives no answer."""
    said = rare(words)
    orders = [
        (order_id, order)
        for order_id, order in state.open_orders(data).items()
        if order["text"].startswith(state.TOOLING) == tooling and order["kind"] == "build" and not order["incident"]
    ]
    closest = sorted(orders, key=lambda item: len(said & rare(f'{item[1]["title"] or ""} {item[1]["text"]}')), reverse=True)[:REPEATS_ASKED]
    items = (({"open": {order_id: " — ".join(filter(None, [order["title"], order["text"]]))}}, {f"repeats.{order_id}": asking(REPEATS, slot=order_id)}) for order_id, order in closest)
    state_read, questions, _ = fill({"order": words[:BODY_CHARS]}, {}, items)
    if not questions:
        return None
    try:
        read = jev(state_read, questions)
    except Unheard as failure:
        raise DriveError(
            f"no order added: Jev did not read these words against the {len(questions)} open orders sharing the most words with them ({failure}),"
            " so words on one's feature would start a second order, reader and builder beside it; run it again, or name the order it joins with --joins <o>"
        ) from None
    surest = max(read, key=read.__getitem__)
    order_id = surest.partition(".")[2]
    if reading(read[surest]) is None:
        print(f"drive order add: Jev is unsure whether these words are on the feature of {order_id}; name it with --joins {order_id} when they are", file=sys.stderr)
    return order_id if read[surest] >= YES else None


def asks_page(words: str) -> bool:
    """Whether his order's words name a goal or ask where work stands, so the drive adds a page to it, as Jev reads them at YES; refused when Jev gives no answer, since an order added unread would lose its page."""
    try:
        read = jev({"order": words[:BODY_CHARS]}, {"page": ASKS_PAGE})
    except Unheard as failure:
        raise DriveError(f"no order added: Jev did not read whether these words name a goal or ask where work stands ({failure}), so its page would go unbuilt; run it again") from None
    return read["page"] >= YES


def unordered(answer: dict, release: str, data: dict) -> str | None:
    """Why his answer does not order this release, one `ci release start` or `hotfix`; None when it does, the one reading the release gates and `drive job ask` share. When the card his answer answers is kept, found through the link `drive answer` kept for it, and his leading option number or tap picks an option whose words name a release in backticks, code decides: his answer orders a release shipping the change one of those ships, every flag the same but its places, `--from` or `--commit` and `--order`, and on staging alone under one on staging alone, as placed() reads it, its targets, `--only`, `--stack` and how it starts, on no place beyond that option's but those his words past the number name, by env id or by the name or displayName its infra/envs file gives that place's team. A picked option naming no release in backticks orders, in code too, a release its words name whole, as named() reads it. Any other answer Jev reads in one request, his words beside that card and the places the release runs on with their stages, one Noul whether his words have that release run and one whether it ships more than they order: it orders the release at YES on the first and under NO on the second, either in the band between refusing it as unsure; Unheard when Jev gave no answer."""
    from drive import brief

    said = his(answer["body"])
    asked = answered_card(data, answer)
    [flags] = brief.started(release)
    cwd = data["roles"]["coordinator"]["cwd"]
    if asked and (pick := picked_option(asked, said)):
        number, rest = pick
        option = asked.options[number - 1]
        words = f"{option}\n{asked.hints.get(option, '')}"
        if commands := brief.card_releases(words):
            whys = [placed(flags, brief.started(command)[0], rest, cwd) for command in commands]
            if None in whys:
                return None
            return f"it picks option {number} of the card it answers, {', '.join(f'`{command}`' for command in commands)}, {whys[0]}"
        if named(flags, words, rest, cwd):
            return None
    card = {"card": "\n".join([f"Question: {asked.title}", *asked.summary, *limits.offered(asked)])} if asked else {}
    read = jev({"action": f"start this release on {runs_on(flags, cwd)}: `{release}`", "answer": {"asked": answer["title"], "said": said}} | card, {f"orders.{answer['id'][:7]}": ORDERS, "more": SHIPS_MORE})
    orders, more = reading(read[f"orders.{answer['id'][:7]}"]), reading(read["more"])
    if orders and more is False:
        return None
    return UNORDERED if orders is False or more else f"{UNORDERED}: Jev is unsure"


def only_asks(title: str, options: list[str], words: str) -> bool:
    """Whether his words under a card only ask it questions, one proposing a way included, or remark on it, as Jev reads them beside its question and options at YES or more, so they answer nothing; Unheard when Jev gave no answer."""
    return reading(jev({"card": {"question": title, "options": options}, "his_words": words[:BODY_CHARS]}, {"answers_nothing": ANSWERS_NOTHING})["answers_nothing"]) is True


def answered_card(data: dict, answer: dict) -> limits.Question | None:
    """The card his answer answers, as `drive job ask` kept it: a question drawn on the card at the link `drive answer` kept for his answer, ended answered, whose ASK file stands, in this drive, else in each drive on this Mac its `drive:<name>` labels name, where `drive answer` recorded it, so an answer he gave on one drive's card reads the same in every drive; None when none does."""
    labelled = (name for tag in answer["tags"] if (name := tag.removeprefix("drive:")) not in (tag, data["name"]) and state.exists(name))
    for drive in chain([data], map(state.load, labelled)):
        link = (drive["answers"].get(answer["id"]) or {}).get("link")
        files = [Path(question["file"]) for _, question in view.drawn_on(drive, link) if question["file"] and (question["closed"] or {}).get("how") == "answered"] if link else []
        if kept := next((file for file in files if file.is_file()), None):
            return limits.question_problems(limits.ask_block(kept.read_text()), kept=True)[0]
    return None


def picked_option(asked: limits.Question, said: str) -> tuple[int, str] | None:
    """The option of the card his words pick, by its number leading them or by his tap, which keeps its words, with his words past the number; None when they pick none."""
    if (number := LEADING_NUMBER.match(said)) and 1 <= int(number[1]) <= len(asked.options):
        return int(number[1]), said[number.end() :]
    tapped = next((index for index, option in enumerate(asked.options, 1) if plain(option) == plain(said)), None)
    return (tapped, "") if tapped else None


def placed(release: dict, option: dict, rest: str, cwd: str) -> str | None:
    """Why a release does not ship what a card option's release ships, on the option's places and those his words past its number name; None when it does. A release on staging alone under an option's release on staging alone ships it with targets, `--only` and `--stack` of its own wherever the option's names none, started with `--emergency`, `--manual` or `--rollback` or not, so his standing yes 23eefb9, whose option starts `tools/ci release start --emergency --env plat-usw2-staging`, orders each soak's start and the `--manual --stack` restore or rollback that returns staging after it: "A standing yes: the drive soaks any dangerous branch on staging alone, while production keeps waiting for your word"."""
    free = UNCHANGED | (SOAKED - set(option) | STARTS if staging_only(release, cwd) and staging_only(option, cwd) else frozenset())
    if {name: values for name, values in release.items() if name not in free} != {name: values for name, values in option.items() if name not in free}:
        return "and this release ships another change"
    if (added := beyond(release, option)) is None:
        return "and this release runs on every place it does not leave out, where that option names its own"
    words = f" {plain(rest)} "
    if unnamed := sorted(place for place in added if not any(f" {plain(name)} " in words for name in (place, *team_names(place, cwd)))):
        return f"and this release also runs on {', '.join(unnamed)}, which his words past its number do not name"
    return None


def named(release: dict, words: str, rest: str, cwd: str) -> bool:
    """Whether a card option's words, naming no release in backticks, name this release whole: it carries only targets, places, `--from` or `--commit`, `--order` and `--emergency`, at least one target and one place it runs on, each target a word of the option's and each place one of the option's or of his words past its number, by env id or its team's name."""
    places = set(release_places(release)) - set(release.get("without", ()))
    if set(release) - PROSE_FLAGS or not release.get("targets") or not places:
        return False
    option, both = f" {plain(words)} ", f" {plain(f'{words} {rest}')} "
    return all(f" {plain(target)} " in option for target in release["targets"]) and all(
        any(f" {plain(name)} " in both for name in (place, *team_names(place, cwd))) for place in places
    )


def beyond(release: dict, option: dict) -> set[str] | None:
    """The places a release runs on that a card option's does not, each by its env id as release_places() reads it; None when the release runs on every place but those it leaves out and the option names its own."""
    if not (named := release_places(release)):
        return None if release_places(option) else set(option.get("without", ())) - set(release.get("without", ()))
    runs = set(named) - set(release.get("without", ()))
    return runs - (set(places) - set(option.get("without", ()))) if (places := release_places(option)) else runs & set(option.get("without", ()))


def team_names(place: str, cwd: str) -> list[str]:
    """The names of a place's team, each tenant's name and displayName in the place's infra/envs file of the drive's checkout; none for a place with no such file."""
    block = TENANTS.search(env_file(place, cwd))
    return TEAM_NAME.findall(block[1]) if block else []


def stage(place: str, cwd: str) -> str | None:
    """A place's stage, such as staging or prod, as the place's infra/envs file of the drive's checkout gives it; None for a place with no such file."""
    found = STAGE.search(env_file(place, cwd))
    return found[1] if found else None


def prod(place: str, cwd: str) -> bool:
    """Whether go/ci counts a place as production, as its Model.Prod reads it: the place's infra/envs file of the drive's checkout marks it `prod: true`, as plat-usw2-prod and each tenant's cluster are and staging, dev and sandbox are not."""
    return bool(PROD.search(env_file(place, cwd)))


def env_file(place: str, cwd: str) -> str:
    """A place's infra/envs file in the drive's checkout, empty for a place with none."""
    env = Path(cwd) / "infra" / "envs" / f"{place}.yaml"
    return env.read_text() if env.is_file() else ""


def release_places(release: dict) -> tuple[str, ...]:
    """Each place a release names to run on, by env id: the env of each `--stack <component>/<env>`, as go/ci's ParseStack splits it, since a --stack release runs on no --env place, else each --env place; none for a release naming neither, which runs on production."""
    if stacks := release.get("stack"):
        return tuple(dict.fromkeys(stack.partition("/")[2] for stack in stacks))
    return release.get("env", ())


def staging_only(release: dict, cwd: str) -> bool:
    """Whether a release runs on staging alone, by go/ci's StagingOnly: it names places, by release_places(), and each one's infra/envs file marks it `stage: staging`."""
    return bool(places := release_places(release)) and all(stage(place, cwd) == STAGING for place in places)


def off_production(release: dict, cwd: str) -> bool:
    """Whether a release runs on no production place, by go/ci's Model.Prod: it names places, by release_places(), and each one's infra/envs file gives it a stage and no `prod: true`, as staging, sandbox and dev are marked."""
    return bool(places := release_places(release)) and all(stage(place, cwd) and not prod(place, cwd) for place in places)


def runs_on(release: dict, cwd: str) -> str:
    """Where a release runs, as go/ci's release wizard says it: each place it names, by release_places(), beside the stage its infra/envs file gives it, else production, every environment except staging, and each place its --without leaves out."""
    places = ", ".join(f"{place} (stage {found})" if (found := stage(place, cwd)) else place for place in release_places(release)) or PRODUCTION
    return f"{places}, leaving out {', '.join(release['without'])}" if release.get("without") else places


class Unheard(Exception):
    """Jev gave no answer: no key, a timeout, a request Jev rejected, or Jev declining a question."""


@cache
def jev_key() -> str:
    """Jev's API key, from TYPESAFE_API_KEY or else the Keychain item `spawnllm key set jev` wrote, read once per process, so no question after the first starts a process."""
    from spawnllm import Provider
    from spawnllm.decide import api_key

    return api_key(Provider("jev", MODEL), timeout=DECIDE_SECONDS)


def jev(state: dict, questions: dict[str, Noul], seconds: float = DECIDE_SECONDS) -> dict[str, float]:
    """Jev's probability of yes for each Noul about the state, by question id, in one request to MODEL on the keep-alive client every question of the process shares, the key's read and the answer both within the seconds given, as TypeSafe's docs say to ask: named-field state, one judgment a question, the decision in code; Unheard when none came, Jev declining any of them included, since a failed call is never a no. spawnllm is imported on the first question, so a hook or verb that asks none never loads it."""
    import httpx
    from spawnllm import Binary, BinaryAnswer, DecideError, DecideKeyMissing, Provider, decide_sync

    deadline = time.monotonic() + seconds
    try:
        token = jev_key()
        decision = decide_sync(state, {key: Binary(*noul) for key, noul in questions.items()}, provider=Provider("jev", MODEL), timeout=deadline - time.monotonic(), api_key=token)
    except (TimeoutError, ValueError, DecideError, DecideKeyMissing, httpx.HTTPError) as error:
        raise Unheard(str(error)) from None
    if declined := [key for key, answer in decision.answers.items() if not isinstance(answer, BinaryAnswer)]:
        raise Unheard(f"Jev declined {', '.join(declined)}")
    return {key: answer.p_yes for key, answer in decision.answers.items()}


def reading(sure: float | None) -> bool | None:
    """A Noul's answer as code decides on it: yes at YES or more, no under NO, and None in the band between and for a question the request had no room for, which each check handles as its own row."""
    return None if sure is None else True if sure >= YES else False if sure < NO else None


def asking(noul: Noul, **slots: object) -> Noul:
    """The Noul with the state paths its question names filled in, its yes and no as they are."""
    return noul._replace(ask=noul.ask.format(**slots))


def fill(state: dict, questions: dict[str, Noul], items: Iterable[tuple[dict, dict[str, Noul]]], chars: int = REQUEST_CHARS) -> tuple[dict, dict[str, Noul], list[tuple[dict, dict[str, Noul]]]]:
    """The request holding the state and questions given, then each item's state and questions, whole, in turn, while the request stays within the characters given, REQUEST_CHARS unless less, by his words "jev should be getting like 10k chars at most for any call": an item with no room is left out whole, never cut, and a later smaller one still goes in; beside it, the items left out."""
    left: list[tuple[dict, dict[str, Noul]]] = []
    for part, asked in items:
        merged = joined(state, part)
        if size(merged, questions | asked) > chars:
            left.append((part, asked))
            continue
        state, questions = merged, questions | asked
    return state, questions, left


def batched(state: dict, questions: dict[str, Noul], items: Iterable[tuple[dict, dict[str, Noul]]], chars: int = REQUEST_CHARS) -> list[tuple[dict, dict[str, Noul]]]:
    """Requests holding every item whole, as many as they need within the characters given each: the first holds the state and questions given, as fill fills it, and each after it the state alone beside the items still left, so none goes unread for want of room; an item too big for a request of its own is said on stderr, never dropped unsaid."""
    requests = [fill(state, questions, items, chars)]
    while left := requests[-1][2]:
        if len(left) == len((request := fill(state, {}, left, chars))[2]):
            print(f"drive: {len(left)} item(s) too big for one Jev request of {chars} characters go unread: {', '.join(key for _, asked in left for key in asked) or 'context alone'}", file=sys.stderr)
            break
        requests.append(request)
    return [(request, asked) for request, asked, _ in requests]


def jev_all(requests: list[tuple[dict, dict[str, Noul]]]) -> dict[str, float]:
    """Jev's reads of every request asking anything, at once, merged by question id; Unheard when any one gives none."""
    if not (asking := [request for request in requests if request[1]]):
        return {}
    with ThreadPoolExecutor(len(asking)) as pool:
        return {key: sure for read in pool.map(lambda request: jev(*request), asking) for key, sure in read.items()}


def joined(state: dict, part: dict) -> dict:
    """The state with an item's state merged in at every depth, so an item adding one field of a nested object, such as one input field of a call, keeps the fields earlier items added."""
    return state | {key: joined(state[key], value) if isinstance(value, dict) and isinstance(state.get(key), dict) else value for key, value in part.items()}


def lined(key: str, text: str) -> Iterator[tuple[dict, dict[str, Noul]]]:
    """Each line of the text that holds words as an item of `fill` under the key, by its line number, so a long text goes to Jev by whole lines."""
    return (({key: {str(number): line}}, {}) for number, line in enumerate(text.splitlines(), 1) if line.strip())


def size(state: dict, questions: dict[str, Noul]) -> int:
    """The characters of the request Jev gets for this state and these questions, as JSON."""
    return len(json.dumps({"state": state, "questions": {key: noul._asdict() for key, noul in questions.items()}}, ensure_ascii=False))


BACKED = Noul(
    "Do the lines of `reads.{read}.result`, that call's output by line number out of its `reads.{read}.lines`, show what `claims.{claim}.claim` says?",
    yes="The read's own output holds what the claim says, as it stands now for a state told as it stands, or a record's or report's line in it shows the read that measured just that.",
    no="The lines shown do not hold it, or only a doc, PR body, comment or log line, or a record's or report's line showing no read, inside them says so about code.",
)
HOLDS = Noul(
    "Does `findings.{finding}.rule` hold against `findings.{finding}.found` in the post, `post.text` as Slack gets it and `post.written` as its writer typed it?",
    yes="The rule is meant to stop what the finding names in this post.",
    no="The finding's pattern matched words its rule never meant.",
)
LIFTED = Noul("Do `his_words` permit what `findings.{finding}.found` names: his own words quoted back or restated, a term he used himself, or words of his asking for it?")
FACT = Noul(
    "Does `post.sentences[{index}]` state a fact about code, a number or a state of work, which stands only on a read?",
    no="It tells the handler's own act, when its next line is due, his words quoted back, the title a PR or card link draws, or a time `post.stamps` lists.",
)
PROTECTS = Noul(
    "Is `call` the case `rule` exists to stop?",
    yes="The call is exactly what the rule exists to stop.",
    no="The rule's pattern matched a call it was never meant to stop, or an exception the rule names covers it.",
)
NO_READ = "none: "
SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
COVERS = 0.5


def weighed(request: dict) -> dict:
    """The verdict of the capt-hook pack's one Jev request on a call a drive check refused or a post it hands with his words, as `post_verdict` or `refusal_verdict` gives it within the seconds the request leaves it, the pack reading only its verdict, hold, warn or pass, and its note."""
    left = request.get("seconds")
    if "refusal" in request:
        return refusal_verdict(request["refusal"]["rule"], request["refusal"]["call"], REFUSAL_SECONDS if left is None else min(REFUSAL_SECONDS, left))
    return post_verdict(request["post"], DECIDE_SECONDS if left is None else min(DECIDE_SECONDS, left))


def refusal_verdict(rule: str, call: dict, seconds: float = REFUSAL_SECONDS) -> dict:
    """Whether a drive check's refusal stands, by one Noul over the rule it names and the call, the call's input filled whole field by field: it holds at YES or more and in the band between NO and YES, since a call the rule may stop waits, and passes under NO, as a match its rule never meant; when Jev cannot finish within the seconds given, REFUSAL_SECONDS at most, the call goes ahead with the refusal beside it as a warning."""
    fields = (({"call": {"input": {key: value}}}, {}) for key, value in call["input"].items())
    told, _, _ = fill({"rule": rule, "call": {"tool": call["tool"], "input": {}}}, {"protects": PROTECTS}, fields)
    try:
        protects = reading(jev(told, {"protects": PROTECTS}, seconds)["protects"])
    except Unheard as failure:
        return {"verdict": "warn", "note": f"Jev gave no verdict ({failure}) on whether this call is what the refusal's rule protects against, so the call goes ahead and the refusal only warns."}
    if protects is None:
        return {"verdict": "hold", "note": "Jev is unsure whether this call is what its rule protects against, so the refusal stands."}
    return {"verdict": "hold" if protects else "pass", "note": ""}


def post_verdict(output: dict, seconds: float = DECIDE_SECONDS) -> dict:
    """Whether a post the drive's checks hand with his words holds, by one request filled by whole items: each claim beside the call its read names, one Noul whether that read backs it, and the lines of that call's result the claims cite as `cited` gives them, a rank at a time across the claims, so every claim's best line goes in before any claim's second and one claim's lines sharing only common words with it never crowd out another's; each finding beside the rule it stands for, one Noul whether the rule holds against it and one whether his words lift it; each sentence of a handler's post no claim's words cover, one Noul whether it states a fact that stands only on a read; and his words, the checks' own picks first, then newest first. It holds for a claim whose read is under BACKS or matched none of the handler's calls, a finding that holds at YES with no lift at NO or more, or an uncovered fact at YES, by his pick f9e9302 "The handler names the read behind each claim; Jev checks each claim against that read, whole, and holds only unbacked claims"; a claim at BACKS or more stands, since every unbacked claim of the drive's recorded posts drew under it and no claim's answer only warns; a finding's or sentence's Noul in the band between, or an item with no room left, only warns, naming it. When Jev cannot finish, a finding a count or a mark decides still holds, since no judgment lifts it, and every other only warns, by his pick b80a8b1 "Refusals only warn when the judge cannot finish: no post ever waits, but it goes out unchecked, as twice this morning"."""
    post = output["post"]
    reason = (output.get("hookSpecificOutput") or {}).get("permissionDecisionReason") or ""
    findings = output.get("findings") or ([{"rule": reason, "found": reason.removeprefix("refused: ")}] if reason else [])
    reads = {item["id"]: item for item in output.get("reads") or []}
    claims = post.get("claims") or []
    base = post_base(post)
    sentences = base["post"]["sentences"]
    unread = [claim for claim in claims if unmatched(reads, claim)]
    uncovered = [index for index, sentence in enumerate(sentences) if reads and not any(covers(claim["claim"], sentence) for claim in claims)]
    words = output.get("his_words") or []
    lines = [cited(reads, claim) for claim in claims if claim not in unread]
    asked = [
        *(backed(reads, number, claim) for number, claim in enumerate(claims, 1) if claim not in unread),
        *(line for rank in zip_longest(*(citing for citing, _ in lines)) for line in rank if line),
        *(({"findings": {f"f{number}": finding}}, {f"f{number}.holds": asking(HOLDS, finding=f"f{number}"), **({f"f{number}.lifted": asking(LIFTED, finding=f"f{number}")} if words else {})}) for number, finding in enumerate(findings, 1)),
        *(({}, {f"s{index}.fact": asking(FACT, index=index)}) for index in uncovered),
        *(({"his_words": {word["id"]: word["quote"]}}, {}) for word in [*(word for word in words if not word.get("said_at")), *sorted((word for word in words if word.get("said_at")), key=lambda word: word["said_at"], reverse=True)]),
        *(line for _, rest in lines for line in rest),
    ]
    told, questions, _ = fill(base, {}, asked)
    for each in told.get("reads", {}).values():
        each["result"] = dict(sorted(each["result"].items(), key=lambda pair: int(pair[0])))
    try:
        read = jev(told, questions, seconds) if questions else {}
    except Unheard as failure:
        return unheard_post(output, reason, str(failure))
    held = [f'claim "{claim["claim"]}" names the read {json.dumps(claim["read"], ensure_ascii=False)}, which matches none of your calls' for claim in unread]
    unsure: list[str] = []
    for number, claim in enumerate(claims, 1):
        if claim in unread:
            continue
        if (sure := read.get(f"c{number}.backed")) is None:
            unsure.append(f'claim "{claim["claim"]}", which had no room in Jev\'s {REQUEST_CHARS:,} characters')
        elif sure < BACKS:
            held.append(f'claim "{claim["claim"]}" does not stand on its read: make the read that shows it and name it in claims, or cut the line to what your reads show')
    for number, finding in enumerate(findings, 1):
        holds, lifted = read.get(f"f{number}.holds"), read.get(f"f{number}.lifted", 0.0)
        if holds is None:
            unsure.append(f"finding {finding['found']}, which had no room in Jev's {REQUEST_CHARS:,} characters")
        elif reading(holds) and lifted < NO:
            held.append(f"{finding['found']}, by its rule: {finding['rule']}")
        elif (rule := reading(holds)) is None or rule and reading(lifted) is None:
            unsure.append(f"finding {finding['found']}")
    for index in uncovered:
        if (fact := reading(read.get(f"s{index}.fact", 0.0))) is True:
            held.append(f'"{sentences[index]}" states a fact no claim names: name the read behind it in claims')
        elif fact is None:
            unsure.append(f'sentence "{sentences[index]}"')
    shown = f"\n```\n{reason}\n```" if reason else ""
    if held:
        return {"verdict": "hold", "note": f"Jev held back this post: {'; '.join(held)}. So fix each, then call say again.{shown}"}
    if unsure:
        return {"verdict": "warn", "note": f"message_limits: Jev is unsure of {'; '.join(unsure)}, so the post goes out as written: if one is wrong, reword the post in place.{shown}"}
    return {"verdict": "warn" if reason else "pass", "note": f"message_limits: Jev read no finding's rule as holding against this post, so it goes out as written." if reason else ""}


def post_base(post: dict) -> dict:
    """The state every request on a post opens with: the post as Slack gets it, its words as its writer typed them with each drive reference as written, which the length rule counts, the sentences of those words by index, each that is drive references alone left out, since a reference's drawn title is no fact of its writer's, and each time say drew from the drive's stamps."""
    written = post.get("written") or post.get("text") or ""
    sentences = [sentence for sentence in SENTENCE.split(written) if limits.WORD.search(limits.REF.sub(" ", sentence))]
    return {"post": {"text": post.get("text") or "", "written": written, "sentences": sentences} | ({"stamps": post["stamps"]} if post.get("stamps") else {})}


def unmatched(reads: dict[str, dict], claim: dict) -> bool:
    """Whether the read a claim names matched none of the handler's calls."""
    return reads.get(claim["read"], {}).get("quote", NO_READ).startswith(NO_READ)


def backed(reads: dict[str, dict], number: int, claim: dict) -> tuple[dict, dict[str, Noul]]:
    """One claim's item: the claim beside the call its read names and how many lines that call's result holds, under the read the call was first given under when an earlier read named the same call, so one call goes to Jev once, and one Noul whether that read backs the claim; the result's lines go in as `cited` gives them."""
    item = reads[claim["read"]]
    held = reads.get(item.get("given") or "", item)
    call, _, result = held["quote"].partition("\n")
    return {"claims": {f"c{number}": {"claim": claim["claim"], "read": held["id"]}}, "reads": {held["id"]: {"call": call, "lines": len(result.splitlines()), "result": {}}}}, {f"c{number}.backed": asking(BACKED, read=held["id"], claim=f"c{number}")}


def cited(reads: dict[str, dict], claim: dict) -> tuple[list[tuple[dict, dict[str, Noul]]], list[tuple[dict, dict[str, Noul]]]]:
    """The lines of the result of the call a claim's read names, each a `fill` item under that read by its line number, so a read takes Jev's request by the lines its claim cites, never by its whole output: those holding the claim's words, the most of them first, which `post_verdict` takes a rank at a time across the claims, then the rest in order, which go in after every other item, so a long read never crowds out another claim, a finding or his words, and a line left out shows as a line number missing from the lines it counts."""
    item = reads[claim["read"]]
    held = reads.get(item.get("given") or "", item)
    words = set(UNWORDED.sub(" ", claim["claim"].casefold()).split())
    lines = [(number, line, len(words & set(UNWORDED.sub(" ", line.casefold()).split()))) for number, line in enumerate(held["quote"].partition("\n")[2].splitlines(), 1) if line.strip()]
    shown = [({"reads": {held["id"]: {"result": {str(number): line}}}}, {}) for number, line, _ in sorted((line for line in lines if line[2]), key=lambda line: -line[2])]
    return shown, [({"reads": {held["id"]: {"result": {str(number): line}}}}, {}) for number, line, hits in lines if not hits]


def unfit_reads(output: dict) -> list[tuple[str, int]]:
    """Each read a handler's claims name whose call Jev's request on the post has no room for beside the post and the claims before it, by the read's id and words and the characters its call adds, largest first, each read once, since its result goes in by the lines its claim cites as `cited` gives them: a claim whose read was first given under another read counts that read, never its own, so each size is the size of the call it names, and a claim left out while the request already holds its read names no read, since that read fit and `post_verdict` warns for the claim."""
    post = output["post"]
    reads = {item["id"]: item for item in output.get("reads") or []}
    claimed = [backed(reads, number, claim) for number, claim in enumerate(post.get("claims") or [], 1) if not unmatched(reads, claim)]
    told, _, left = fill(post_base(post), {}, claimed)
    sized = {f"{read} {json.dumps(reads[read]['read'], ensure_ascii=False)}": size({"reads": {read: held}}, {}) for part, _ in left for read, held in part["reads"].items() if read not in told.get("reads", {})}
    return sorted(sized.items(), key=lambda pair: -pair[1])


def unheard_post(output: dict, reason: str, failure: str) -> dict:
    """The verdict on a post Jev gave no answer on: held for a finding a count or a mark decides, since no judgment lifts it, and a warning naming every other finding otherwise, so the post goes out, by his pick b80a8b1."""
    if counted := output.get("counted"):
        return {"verdict": "hold", "note": f"message_limits: Jev gave no verdict ({failure}), and a finding a count or a mark decides refuses without one: fix it, then call say again.\n```\n{counted}\n```"}
    if reason:
        return {"verdict": "warn", "note": f"message_limits: Jev gave no verdict ({failure}), so each finding below only warns and the post goes out as written: if one holds, reword the post in place.\n```\n{reason}\n```"}
    return {"verdict": "warn", "note": f"message_limits: Jev gave no verdict ({failure}) on whether each claim of this post stands on the read it names, so the post goes out as written."}


def covers(claim: str, sentence: str) -> bool:
    """Whether a claim speaks for a sentence of the post: at least COVERS of the claim's words stand in the sentence, case aside."""
    named = set(UNWORDED.sub(" ", claim.casefold()).split())
    return bool(named) and len(named & set(UNWORDED.sub(" ", sentence.casefold()).split())) >= COVERS * len(named)


def answered(data: dict, title: str) -> dict | None:
    """His answer to this same question: the newest question of any job asked in these words, whatever their case, spacing and punctuation, that ended answered by his tap or words on or under its card, or by his words a record kept for it; a question kept answered with no card and no record, as `drive job ask` once kept one on Jev's reading of an answer he gave another question, is none."""
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
