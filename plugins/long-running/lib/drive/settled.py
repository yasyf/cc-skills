import html
import json
import mmap
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import datetime
from functools import cache
from itertools import chain, zip_longest
from pathlib import Path
from typing import NamedTuple

from drive import github, limits, providers, slack, state, view
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
OPUS_SECONDS = 120
REPEATS_ASKED = 8
ALONE = r" (?:ships?|shipping|deploys?|deploying) (?:only|just) (?:the )?{component} | {component} (?:(?:stack|component|service) )?(?:alone|only) "
NUMBERED = re.compile(r"(?<!\S)(\d+)\)\s+")
EVERY = re.compile(r"\s*all\b(?:\s+(\d+)\b)?", re.IGNORECASE)
UNAPPROVED = re.compile(r" (?:not|t|never|no|nor)(?: \w+){0,2} approv| (?:unapprov|reject|refus|den(?:y|ied)|hold|wait|except|exclud|apart from|other than|besides|without|but not)")
ALL_BUT = re.compile(r" (?:all|everything)(?: else)? (?:but|except|other than|besides|save|apart from) (?:the )?([a-z0-9][a-z0-9-]*) ")
SCOPE_WORDS = 'his words on Nov 15, 2026, "why are you leaving stuff out? you should make sure everything [...] is working and has not broken anything and then release it all"'
OPUS_RULES = (
    "You answer a drive's questions about the JSON `state` it gives you, most of them about his words: the words of the one person whose"
    " orders, answers and rulings the drive works by. Each question in `questions` names the fields of `state` it asks about by"
    " backticked path. A question with `yes` and `no` asks yes or no: answer it with the probability, from 0 to 1, that its answer is"
    " yes, reading `yes` and `no`, where given, as what each answer means. A question with `options` asks you to pick one: answer it"
    " with exactly one option's value, reading what each means beside it. Read his words for what he means, as a careful colleague"
    " would, never by keyword and never past what they say. Answer each question on its own, from `state` alone, and where `state`"
    " leaves it open give a probability near the middle rather than a guess."
)


class Noul(NamedTuple):
    """One yes-or-no question the reader, Jev or Opus, answers with the probability of yes: what it asks of the request's state by backticked path, then what a yes and a no mean where the question alone leaves it open."""

    ask: str
    yes: str | None = None
    no: str | None = None


class Pick(NamedTuple):
    """One question the reader, Jev or Opus, answers by picking one of its options, as spawnllm's Label asks it: what it asks of the request's state by backticked path, then each option's value beside what it means, or None, the fail-safe option first, since Jev leans toward it."""

    ask: str
    options: dict[str, str | None]


UNSAID_HERE = 'A bare option number answers only the card it was typed on, and a question of his rules nothing out unless it proposes a way, as "why not just X?" orders X.'
LINE = Noul("Does `lines.{line}` go against `answers.{answer}`, as `against` says?")
AGAINST = (
    "A line goes against his answer when it says no ruling of his covers an item, puts an item up for change, removal or his pick, or asks him what `said`, his answer to `asked`, given"
    " before the line, already orders or keeps as it is. It does not when it keeps what his answer orders or keeps, cites it as standing, or speaks of an item his answer neither orders nor keeps."
    " His answer orders or keeps items only in the area it rules on, by `area`, its card, tooling and path tags, and by `asked`: a line about another area, such as other code sharing a word with it, speaks of nothing it orders or keeps. " + UNSAID_HERE
)
LINE_KEY = "line"
LINE_CANDIDATES = 4
LINE_WORDS = 5
LIST_LINE = re.compile(r"^\s*(?:[-*•]\s+|\d+[.)]\s+|\|)(.*\w.*)$")
TABLE_RULE = re.compile(r"^[\s|:-]*$")
OVERRULED = 'the line "{line}" says no ruling of his covers, puts up for change or asks him again what his answer {id}, "{asked}"{link}{area}, orders or keeps: "{said}"'
LIFTS = "name by id, on the line, his later answer that lifts it, which marks it superseded by that answer in cc-notes once Jev reads the later replacing it"
UNREAD_LINES = "Jev gave no reading of its lines against his recorded answers, so it goes on unread: {failure}"
OVERRULED_RULE = (
    "No line says no ruling of his covers, puts up for change or asks him again an item a recorded answer of his orders or keeps:"
    ' his words stand until he lifts them, by his words "Cloud tree own cap and skipping safe mode also ok I fucking said to do'
    ' those why are you undoing my work".'
)
REPLACES = Noul(
    "Does `answer.said`, his newer answer to `answer.asked`, replace `words.{slot}.said`, his earlier answer to `words.{slot}.asked`, so building to the earlier goes against the newer, as an order to design something from scratch replaces his rulings on the design it starts over?",
    no="The newer answer leaves the earlier standing beside it, or speaks to something else.",
)
DECIDING = Noul(
    "Does `answers.{answer}.said`, his answer to `answers.{answer}.asked`, choose one of `card.options` or make `card.question` moot, so the card asks him nothing his answer leaves open?",
    no='The card still asks something his answer does not settle. A bare option number answers only the card it was typed on, and a question of his answers no card, one proposing a way such as "could we X?" or "why not just X?" included.',
)
ANSWERS_NOTHING = Noul(
    "Do `his_words`, written under `card`, only ask it questions or only remark on it, so they pick none of `card.options` and order no work?",
    yes='His words "Expand on 1 and 2 and how does the replica fit in?", "Why is 1 recommended over 2?" and "could we do 1 without re-uploading?" only ask, a way proposed as a question such as "why not just X?" included.',
    no="They pick an option, approve the recommended one, rule one out, or order the work in words that ask nothing.",
)
BESIDE = Noul(
    "Do `his_words`, written under `card`, order work other than what `card.question` asks, while picking none of `card.options` and leaving what the card asks as it stands?",
    yes='His words "Why is 1 recommended over 2? Also the status page is still blank, fix it" ask the card a question and order other work beside it, so the card still waits on his pick.',
    no="They pick an option, approve the recommended one, rule one out, change what the card asks, order the card's own work, or order nothing.",
)
CHOSEN = (
    "build that option with no card, a reader returning DONE with `rulings:` quoting {id} and `build:` that option, and end a card"
    " already drawn for it on his words with drive job ask {job} --drop <the file its question is kept in> --answer {id}"
)
TOOLING_SAID = (
    "This is getting too in the weeds for me use your fucking Brain and act like a frontier model when it comes to Meta tooling"
    " don't ask me every little thing ask my high level preferences (different than non-tooling changes); apply this everywhere now"
)
PREFERENCE = Noul(
    "Do the options of `card.text`, asked on a drive tooling order, differ in what he gets: whether, where or when a post or card"
    " reaches him or what it looks like, a tap, gate or presence he owes, a cost he pays in money or in waiting on his work, a ruling"
    " of his the recommended option lifts, or the scope of work the drive takes on?",
    yes="A high-level preference of his, which by `ruling.said` he answers.",
    no="The options differ only in how the tooling does its work, such as which file, function, store, hook, verb, flag, check, judge,"
    " process or wait does it, what an agent, hook or verb is refused or made to do, how many requests or processes it makes, or which"
    " branch, pin or commit holds a change, which by `ruling.said` its job decides.",
)
UNWAITED_SAID = "It also should not wait on me: it should start building everything it can with its recommendations, and any design decisions sent to me can just steer it later"
UNWAITED = "{option}: decided by the drive under your words \"" + UNWAITED_SAID + "\"; reply here to overrule"
DECISION_LABEL = "from:drive-decision"
FRESH_SAID = "I've said so many times to do this design from the ground up and only ask me for the crucial directional decisions yet you keep bringing me old questions"
DIRECTION = Noul(
    "Do the options of `card.text`, asked by a builder of a design his words ordered from scratch, differ in the design's direction:"
    " which components run, where data lives and in what shape, how they connect, what a user sees or waits on, or a cost or risk"
    " he takes on?",
    yes="A crucial directional decision of the design, which by `ruling.said` he answers.",
    no="The options differ only in how the builder carries the design out, such as which file, function, hook, verb, flag, check,"
    " test or wait does it, or in what order its steps land, which by `ruling.said` its builder decides.",
)


class Leave(NamedTuple):
    """A ruling of his leaving some of a job's questions to the job: its id, his words, the Noul whose clear no reads a card as one of them, what such a question asks, and what his card shows once the job decides it."""

    ruling: str
    said: str
    noul: Noul
    what: str
    decided: str


TOOLING_DETAILS = Leave(state.TOOLING_DETAILS_RULING, TOOLING_SAID, PREFERENCE, "a detail of drive tooling", "{option}: decided without you under your ruling on tooling details")
FRESH_DETAILS = Leave(
    state.FRESH_DETAILS_RULING,
    FRESH_SAID,
    DIRECTION,
    "a detail of how to build a design he ordered from scratch",
    "{option}: decided without you under your ruling to bring you only a from-scratch design's crucial directional decisions",
)
DECIDED_LINE = (
    '{file} keeps {job}\'s question decided on its recommended option, "{option}", with no card: Opus reads it as asking only {what},'
    " which his ruling {ruling} leaves to the job, so build that option and open its PR as for work no card holds"
)
DECIDES = (
    'drive job ask: Opus reads this card as asking only {what}, which his ruling {ruling} leaves to the job, so asking'
    ' it keeps it decided on its recommended option, "{option}", with no card'
)
REHEARSED = "{option}: decided without you, since a release on staging or sandbox alone needs no word of yours"
REHEARSED_LINE = (
    '{file} keeps {job}\'s question decided on its recommended option, "{option}", with no card: every release it names runs on the rehearsal envs'
    " of the release provider alone, which his ruling {ruling} leaves to the drive, so the top coordinator starts it with `drive job add --release` and no --order"
)
REHEARSES = (
    "drive job ask: every release this card's recommended option names runs on the release provider's rehearsal envs alone, which his ruling {ruling}"
    ' leaves to the drive, so asking it keeps it decided on that option, "{option}", with no card'
)
RULED = {
    **{leave.ruling: (leave.decided, DECIDED_LINE.replace("{what}", leave.what), DECIDES.replace("{what}", leave.what)) for leave in (TOOLING_DETAILS, FRESH_DETAILS)},
    state.REHEARSAL_RULING: (REHEARSED, REHEARSED_LINE, REHEARSES),
}
RESTARTS = Noul(
    "Do `words` order the design of the work they are on started over from scratch, from the ground up or regardless of what is built"
    " today, as \"design it from scratch\" or \"don't base it on what's in dev\" do?",
    yes="His words restart that design, so every question asked of him on the old one ends and the new one asks again.",
    no="They pick, refine or correct a step of the design as it stands, or only quote such words, as a self-retro on them does.",
)
REMOVES = Noul(
    "Do `ruling.said`, his answer to `ruling.asked` and his ruling on the whole project or workstream the work is filed under, remove the work `parts.{part}`"
    " names, as work a design he named replaces, work he stopped or work he ruled out, so starting it goes against what he said?",
    yes="Starting that work goes against his ruling, so no job starts on it.",
    no="The work stands beside his ruling, or his ruling only shapes how it is done.",
)
REMOVED = 'his ruling {id} on all of {area}, "{said}", removes "{part}", so no job starts on it'
APPROVED = "{option}: decided without you under your approval {answer}"
APPROVED_LINE = (
    '{file} keeps {job}\'s question decided on its recommended option, "{option}", with no card: his standing approval {answer} covers it,'
    " so build that option and open its PR as for work no card holds"
)
APPROVES = 'drive job ask: his standing approval {answer} covers this card, so asking it keeps it decided on its recommended option, "{option}", with no card'
INSIDE = Noul(
    "Is the work in `work` inside what `approvals.{slot}.said`, his answer to `approvals.{slot}.asked` with its Notes, approves going ahead without him?",
    yes="Work of the kind his words approve, such as more of the tooling or fixes they name.",
    no="Work his words do not reach, even in the same code.",
)
READ_MARK = re.compile(r"(?:Jev|Opus) reads .+ as breaking it\Z")
SUPERSEDES = Noul("Does `answer.said`, his answer to a newer question of the same job, settle everything `open.{slot}` asks him?")
REPEATS = Noul(
    "Does `order` ask again for what `open.{slot}` asks: the same defect or change?",
    no="It asks for another defect, even in the same code, a slice of its feature, a fix to its fix or a step after it.",
)
FEATURE = Noul(
    "Is `order` on the feature `open.{slot}` holds: the same fault or change, a slice of it, a fix to it or a step after it?",
    no="It asks for work that order is not about, even in the same code.",
)
SAME_CHANGE = Noul(
    "Does `open.{slot}` already make the change `part.name` names, a part of `part.order`: the same fix or change, or one that leaves nothing of it to build?",
    no="It is other work, even in the same code or on the same feature, or a step before or after it.",
)
ORDERS = Noul(
    "Do `answer.said`, read with `answer.notes`, `card` and his `earlier` answers in that thread, have the release in `action` run?",
    yes=(
        "His order to ship or release that work, or his pick on a card about it reaching production whose option has that release run; a condition such as once tests pass orders it once it holds."
        " His words to get work built or tested on places that are not production, such as a sandbox or staging, order each release of it that runs on those places alone."
        " Words pointing at releases, such as that one or all 3 of these, order each release the notes or his earlier answers say they point at."
    ),
    no=(
        "His Approve or tap on a pull request, a pick to land, merge or enqueue one, or a pick of a design, a repair or whether a check holds releases nothing."
        " Words to get work built or tested order no release that runs on production. The notes and his earlier answers only say what `answer.said` points at and order nothing themselves."
    ),
)
SHIPS_MORE = Noul(
    "Does the release in `action` ship more than `answer.said` orders, read with what `answer.notes` and his `earlier` answers say it points at: another change, more components or other places?",
    no=(
        "His words past a bare option number order only what they name, such as a team brought to where the picked option put the others."
        " His words to get work built or tested on places order a release of that work there, on those places alone, whatever flags it starts with."
    ),
)
JOB_ORDERS = Noul(
    "Do `answers.{slot}.said`, read with `answers.{slot}.card`, the rest of `answers` and his hold on every release in `hold`, have the Job in `job` start on `job.cluster` at `job.now`?",
    yes=(
        "His order, or his pick on a card, to run that CronJob's pass, dry run or repair on that cluster, his exception to the hold, with each time or condition"
        " his words set holding at `job.now`, such as tonight once it is evening there, and no other answer of his in `answers` holding it back at that time."
    ),
    no=(
        "Words that run it only on other clusters, such as staging, or once a time or condition that does not hold at `job.now`, such as after his hold lifts or later"
        " that day; his Approve or tap on a pull request, a pick of a design, or words about another Job or a release order no Job."
    ),
)
JOB_ABOUT = Noul(
    "Do `answers.{slot}.said` speak to the Job in `job`: order, time or hold back that CronJob's pass, dry run or repair, on `job.cluster` alone or with other clusters?",
    no="They are about other work, such as another pass, a release, a pull request or a design.",
)
ANSWER_REF = re.compile(r"\b(?=[0-9a-f]*[0-9])(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b")
PICK = re.compile(r"\b([0-9a-f]{7})[0-9a-f]*:")
SPEAKS_TO_START = Noul(
    "Does release note `note.body`, under its title `note.title`, speak to the release start `starts.{start}`, its rule binding a cut of the start's targets, or of a target the note says ships with them, on the places the start runs?",
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
PUTS_OFF = Noul(
    "Does `section.text`, the section of a PR body his card draws under `section.heading`, say that a step it reports, one its writer runs before the PR opens, such as the FDE impact scan, has not run and that its result comes later, once a build, tool, fix or someone else gets to it?",
    yes='Such as "its report lands here once the first build of this head renders its contract" or "the scan runs once the tool is fixed".',
    no="It reports the step's result, says why the step does not apply, or names only what runs after the PR opens, such as CI, CI's screenshots, review, a merge, install, release or proof, or a piece of the plan a follow-up builds, which Against the plan lists.",
)
STEPS = Noul(
    "Does `story` list steps on `surface`, or name a part to walk, with no result he must then see?",
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
UNORDERED = "Opus does not read it as approving this release"
TESTED = (
    "a staging release marked --for-production goes through every staging freeze, a teammate's included, so it starts only on his"
    " exception for the production release it tests, `{release}`, and {why}"
)
UNTESTED = "and that release runs on no production place"
ORDERED = (
    "drive job ask: his answer {id}, {asked}, orders the recommended option's `{release}`, so his card offers his words as its"
    ' Already answered tap: his one tap settles the card on them, and `drive job add --release "{release} --order {id}"` starts that'
    " release; the card is kept unsettled on none of his words it carries out."
)
LEADING_NUMBER = re.compile(r"\s*(\d+)\b(?![.,]\d)")
UNCHANGED = frozenset({"env", "without", "from", "order"})
PROSE_FLAGS = frozenset({"targets", *UNCHANGED, "emergency"})
FOR_PRODUCTION = "for-production"
SPOKEN_FLAGS = frozenset({*PROSE_FLAGS, "only", "exclude", FOR_PRODUCTION})
STAGING = "staging"
PRODUCTION = "production, every environment except staging"
ASKS_PAGE = Noul(
    "Does `order` name a goal he wants worked toward or prioritized, such as \"let's prioritize\" a project, link or outcome or getting a set of changes merged,"
    " ask where a project, feature or body of work stands, or ask for a page or dashboard of it?",
    yes="He follows that work over days, and the drive builds a live page of it.",
    no="It orders one bounded thing built, fixed, released or answered, asking nothing of where the larger work stands.",
)
CADENCE = re.compile(r"\bevery\s+(?:(?P<minutes>\d+)\s*(?:m|mins?|minutes?)\b|(?P<half>half\s+(?:an\s+)?hour)\b|(?P<hour>hour)\b)", re.IGNORECASE)
PACED = "paced"
ASKS_CADENCE = Noul(
    "Do `words` ask that he get a line, update or status from the drive every `minutes` minutes, as \"give me updates every 15 mins\" or \"ur supposed to give updates every 15 mins\" do?",
    yes="He wants a line in this thread that many minutes after the last one until the work is done.",
    no="The words name those minutes for something else, such as how often a tool, alert, job or session repeats or runs.",
)
APPROVAL = "kind:approval"
INCIDENT = "incident"
RELEASE_HOLD = "release-hold"
TAP_HEADER = "header:Tap"
TAP_QUESTION = re.compile(r"(?:PR )?(?:Taps?\b|Approv|Land\b).*?(?:#\d+|/pull/\d+)", re.IGNORECASE)
RARE = re.compile(r"[a-z][a-z0-9-]{4,}")
NOTES = "\nNotes:"
WHOLE = " the whole question: "
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
    "his words are read at the permalink of his message or of the card he answered, or at terminal for a prompt he typed into a drive"
    " session's terminal, cut with … where words are left out"
)
PARAPHRASE_LABEL = "from:paraphrase"
PARAPHRASED = " the session's paraphrase, which he never said: "
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


def recorded(name: str, title: str, words: str, labels: list[str], where: str, cwd: str, lifts: Iterable[str] = ()) -> str:
    """Record his answer on the drive in cc-notes, the question as its title as `titled` fits it, his words as its body with the whole question in its notes when its title is cut, and the drive's labels before these, a paraphrase, labelled PARAPHRASE_LABEL, kept off `scope:durable` so no read of his rulings ranks it, superseding and lifting nothing; else superseding by it each answer of the drive's still standing under that same question, so his changed pick leaves one answer per question for ccn answer match to rank, and each answer of his the question's card asked him to lift, by `lifting` its unsettled mark, that `lift` reads his answer replacing, and return its id; a standing answer cc-notes holds already under that title in those words, as a tap, a handler or an earlier call recorded it, is that answer, so its id comes back and nothing is added. A refusal names his words, the question and the card or message where he gave them."""
    handle = titled(title)
    said = (plain(handle), plain(words))
    paraphrase = PARAPHRASE_LABEL in labels
    if not paraphrase and (kept := next((answer for answer in listed("scope:durable", cwd) if (plain(answer["title"]), plain(his(answer["body"]))) == said), None)):
        labelled(kept, labels, cwd)
        return kept["id"]
    body = words if handle == title else f"{words}{NOTES}{WHOLE}{title}"
    standing = [answer["id"] for answer in listed(f"drive:{name}", cwd) if state.same_question(answer["title"], handle)]
    add = ["ccn", "answer", "add", f"--title={handle}", "--body-file", "-", *(f"--label={label}" for label in ([f"drive:{name}", *labels] if paraphrase else ["scope:durable", f"drive:{name}", *kind(words), *labels])), "--json"]
    answer_id = json.loads(ccn(add, body, cwd, f'could not record in cc-notes his answer "{cut(words, 80)}" to "{cut(title, 120)}" on {where}'))["id"]
    if paraphrase:
        return answer_id
    for pick in standing:
        supersede(pick, answer_id, cwd)
    lift(answer_id, title, words, list(lifts), cwd)
    return answer_id


def retitled(answer: dict, title: str, cwd: str) -> str:
    """Reword in cc-notes the question his kept answer answers to title, as `titled` fits it, the whole question in its notes when its title is cut as recorded() keeps it, so the release gate and every later read take the question as it reads now; the title it keeps."""
    handle = titled(title)
    kept = answer["body"].partition(WHOLE)[0].removesuffix(NOTES)
    body = kept if handle == title else f"{kept}{'' if NOTES in kept else NOTES}{WHOLE}{title}"
    edit = ["ccn", "answer", "edit", answer["id"], f"--title={handle}", *(["--body-file", "-"] if body != answer["body"] else [])]
    ccn(edit, body, cwd, f'could not reword the question of his answer {answer["id"][:7]} to "{cut(title, 120)}" in cc-notes')
    return handle


def lifting(questions: Iterable[dict]) -> list[str]:
    """The answers of his these questions' cards asked him to lift, by the id each `<id>: <why>` of an unsettled mark `drive job ask` kept opens on, once each."""
    return list(dict.fromkeys(pick for question in questions if question["unsettled"] for pick in PICK.findall(question["unsettled"])))


def lift(answer_id: str, asked: str, said: str, picks: list[str], cwd: str) -> None:
    """Mark in cc-notes each of these answers of his, still standing, superseded by his answer `said` to `asked` when Jev reads it replacing that one, by `replacing` on that pair alone, at YES or more, so an answer he lifted on the card that asked him to lift it never binds again, while one he kept stands; no reading leaves each standing, said on stderr."""
    older = [{"id": pick, "asked": answer["title"], "said": his(answer["body"])[:BODY_CHARS]} for pick in picks if "superseded_by" not in (answer := shown(pick, cwd)) and his(answer["body"])]
    if not older:
        return
    try:
        read = replacing(asked, said, older)
    except Unheard as failure:
        print(f"drive: Jev did not read whether his answer {answer_id[:7]} lifts {', '.join(entry['id'] for entry in older)}, which his card asked him to lift, so each stands: {failure}", file=sys.stderr)
        return
    for pick, sure in read.items():
        if reading(sure):
            supersede(pick, answer_id, cwd)


def unlift(answer_id: str, picks: list[str], cwd: str) -> None:
    """Take out of cc-notes each supersede of these answers of his by his answer, as `lift` wrote it, since his Undo takes that answer back, so each answer he had lifted on its card stands again."""
    for pick in picks:
        if answer_id in shown(pick, cwd).get("superseded_by", []):
            ccn(["ccn", "answer", "supersede", pick, "--by", answer_id, "--clear"], "", cwd, f"could not mark his answer {pick} standing again once his Undo took back {answer_id[:7]}")


def replacing(asked: str, said: str, older: list[dict]) -> dict[str, float]:
    """How sure Opus is that his answer `said` to `asked` replaces each of these older answers of his, by its id, its `asked` and `said` beside it, each pair its own REPLACES Noul, in one request; Unheard when Opus gives no reading."""
    slots = {entry["id"]: f"w{number}" for number, entry in enumerate(older, 1)}
    items = (({"words": {slots[entry["id"]]: {"asked": entry["asked"], "said": entry["said"]}}}, {f"replaces.{entry['id']}": asking(REPLACES, slot=slots[entry["id"]])}) for entry in older)
    read = opus(*together([({"answer": {"asked": asked, "said": said}, "words": {}}, {}), *items]))
    return {key.partition(".")[2]: sure for key, sure in read.items()}


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
        (job_id, question, recorded(name, question["title"], closed["words"], [], closed["link"] or question["card"] or (f"his card {question['waits']}" if question["waits"] else f"record {closed['seq']}"), cwd, lifting([question])))
        for job_id, job in data["jobs"].items()
        for question in job["questions"]
        if (closed := question["closed"]) and closed["how"] == "answered" and closed["words"] and not (closed["answer"] or closed["ruling"] or closed["noted"]) and (question["card"] or question["waits"] or closed["seq"])
        and closed["words"] != "\n".join(dict.fromkeys(answer["words"] for answer in question["recorded"]))
    ]
    def note_cards_write(data: dict):
        for job_id, question, answer_id in noted:
            state.question_in(data["jobs"][job_id], question)["closed"]["noted"] = answer_id
        data[state.CARDS_NOTED] = True

    state.write(name, note_cards_write)


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


def labelled(answer: dict, labels: list[str], cwd: str) -> None:
    """Add to his recorded answer each of these labels it lacks, so a label such as `area:<project>` named once the answer was recorded, by a handler or an earlier call, still reaches it."""
    for label in labels:
        if label not in answer["tags"]:
            ccn(["ccn", "answer", "edit", answer["id"], "--add-label", label], "", cwd, f"could not label his answer {answer['id'][:7]} {label}")


class NotFound(DriveError):
    """A cc-notes read naming a record cc-notes keeps none of, which reading it again finds no more than the first time did."""


NOT_FOUND = "not-found:"


def ccn(argv: list[str], text: str, cwd: str, failure: str) -> str:
    try:
        done = subprocess.run(argv, input=text, cwd=cwd, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DriveError(f"{failure}: {error}") from None
    if done.returncode:
        said = " ".join(done.stderr.split())
        raise (NotFound if said.startswith(NOT_FOUND) else DriveError)(f"{failure}: {said}")
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


def his_at(body: str, links: list[str], found: dict | None, data: dict) -> tuple[str, str | None]:
    """His words in a recorded answer's body as his messages at the links hold them, with why they are not his: its text above the drive's notes, by `his`, when `unsaid` finds each piece of it his there; else, for a text that holds more than his words, such as an agent's attribution before his quote, his message or card answer at each link joined with … in link order, when each sits whole in plain words inside that text and each piece of it, split where an ellipsis or a bracketed insertion leaves words out, sits inside them or holds one whole, so a text quoting a message of his no link names is refused as before; else that text, with `unsaid`'s refusal of it. At terminal his words are only ever the text."""
    words = his(body)
    if (refusal := unsaid([words], links, found, data)) is None:
        return words, None
    if links == [TERMINAL] or unthreaded(links):
        return words, refusal
    try:
        said = [heard(link, found, data) for link in links]
    except slack.SlackError:
        return words, refusal
    if not said or None in said:
        return words, refusal
    messages = [f" {plain(message)} " for message in said]
    if any(message.isspace() or message not in f" {plain(words)} " for message in messages):
        return words, refusal
    if any(f" {plain(piece)} " not in "".join(messages) and not any(message in f" {plain(piece)} " for message in messages) for piece in ELIDED.split(words)):
        return words, refusal
    return state.MESSAGES_JOINED.join(said), None


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


def paraphrased(answer: dict) -> bool:
    """Whether a recorded answer is a session's paraphrase, labelled PARAPHRASE_LABEL by `drive answer` when the words it was given are not his at a link, so no card, order or release gate reads it as his words."""
    return PARAPHRASE_LABEL in answer.get("tags", ())


def heard_only(answer: dict) -> dict:
    """A recorded answer as every gate reads it: a paraphrased() one with all of its body under its notes, so his() reads no words of his in it."""
    return {**answer, "body": f"{NOTES}{PARAPHRASED}{answer['body']}"} if paraphrased(answer) else answer


def shown(answer_id: str, cwd: str) -> dict:
    """One recorded answer of his, its title, body and labels, none when cc-notes prints no tags key, as it does for an answer with no labels; a paraphrased() one as heard_only() reads it."""
    return heard_only({"tags": [], **json.loads(ccn(["ccn", "answer", "show", answer_id, "--json"], "", cwd, f"could not read his answer {answer_id}"))})


def listed(label: str, cwd: str) -> list[dict]:
    """His recorded answers carrying the label, newest first, each with its body and the time it was recorded; never one a later answer superseded."""
    found = json.loads(ccn(["ccn", "answer", "list", "--label", label, "--limit", "0", "--full", "--json"], "", cwd, f"could not read his answers labelled {label}"))
    return sorted((answer for answer in found if not paraphrased(answer)), key=lambda answer: answer["created_at"], reverse=True)


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
        for answer in [answer for answer in ranked if APPROVAL not in answer["tags"] and not paraphrased(answer) and his(answer["body"])][:CANDIDATES]:
            found.setdefault(answer["id"][:7], {"id": answer["id"][:7], "asked": answer["title"], "said": his(answer["body"])[:BODY_CHARS], "link": given_at(data, answer)})
    return list(found.values())


def area_rulings(data: dict, order_ids: list[str]) -> list[dict]:
    """His standing answers on the whole project or workstream any of these orders is filed under, by the labels state.area_labels names, each once with where he gave it and the area its label names, so a ruling such as "every SandSQL card" binds every order filed there whatever feature each is on."""
    cwd = data["roles"]["coordinator"]["cwd"]
    found = {
        answer["id"][:7]: {"id": answer["id"][:7], "asked": answer["title"], "said": his(answer["body"])[:BODY_CHARS], "link": given_at(data, answer), "area": label.removeprefix(state.AREA_LABEL)}
        for label in state.area_labels(data, order_ids)
        for answer in listed(label, cwd)
    }
    return list(found.values())


def removed(parts: list[str], rulings: list[dict]) -> dict[str, dict]:
    """Each of these parts Jev reads at YES as work one of these rulings of his on its whole area removes, beside the first such ruling, one request a ruling holding only its `asked` and `said` beside the names of the parts; none when Jev gives no reading, said on stderr, so no work waits on Jev."""
    named = {f"p{number}": part for number, part in enumerate(parts, 1)}
    try:
        read = jev_all([({"ruling": {"asked": ruling["asked"], "said": ruling["said"]}, "parts": named}, {f"removes.{slot}.{ruling['id']}": asking(REMOVES, part=slot) for slot in named}) for ruling in rulings])
    except Unheard as failure:
        print(f"drive: Jev gave no reading of whether his rulings on their whole area remove {', '.join(parts)}, so work on them starts: {failure}", file=sys.stderr)
        return {}
    return {part: ruling for number, part in enumerate(parts, 1) if (ruling := next((ruling for ruling in rulings if reading(read.get(f"removes.p{number}.{ruling['id']}")) is True), None))}


def list_lines(text: str) -> list[str]:
    """Each line of a list in the text, a bullet, a numbered item or a table row other than its rule, without its marker, once each."""
    return list(dict.fromkeys(found[1].strip(" |") for line in text.splitlines() if (found := LIST_LINE.match(line)) and not TABLE_RULE.fullmatch(line)))


def none_only(words: str) -> bool:
    """Whether these words are only none, as a line says it holds nothing: casefold, a trailing period dropped, so `None.` is and `none: the copy path has no ruling` is not."""
    return words.strip().casefold().rstrip(".") == "none"


def line_answers(lines: list[str], data: dict, besides: frozenset[str] = frozenset(), floored: bool = False) -> list[tuple[str, list[dict]]]:
    """Each line beside the first LINE_CANDIDATES of his recorded answers `ccn answer match` ranks nearest to it and the first LINE_CANDIDATES `ccn answer search` finds for its first LINE_WORDS words, once each, nearest first with the two rankings taken in turn, with where he gave it and the tags naming the area it rules on, never one besides names, a tap approving a PR nor a provisional pick; floored, only the first LINE_CANDIDATES `ccn answer match` finds with its floors, two of the line's rare words in an answer's question or three anywhere, so a line sharing one word with an answer on another subject, such as cut, reaches none."""
    cwd = data["roles"]["coordinator"]["cwd"]
    failure = "could not check its lines against his recorded answers"
    lined = []
    for line in lines:
        words = read(line)
        match = ccn(["ccn", "answer", "match", "-", "--label", "scope:durable", *([] if floored else ["--nearest"]), "--limit", "-1", "--json"], words, cwd, failure)
        search = "[]" if floored else ccn(["ccn", "answer", "search", "--label", "scope:durable", "--limit", "0", "--json", "--", " ".join(words.split()[:LINE_WORDS])], "", cwd, failure)
        matched, searched = ([answer for answer in json.loads(ranked) if APPROVAL not in answer["tags"] and not paraphrased(answer) and his(answer["body"]) and answer["id"][:7] not in besides][:LINE_CANDIDATES] for ranked in (match, search))
        found: dict[str, dict] = {}
        for answer in (answer for pair in zip_longest(matched, searched) for answer in pair if answer):
            found.setdefault(answer["id"][:7], {"id": answer["id"][:7], "asked": answer["title"], "said": his(answer["body"])[:BODY_CHARS], "link": given_at(data, answer), "area": [tag for tag in answer["tags"] if not tag.startswith(("scope:", "kind:", "drive:"))]})
        lined.append((line, list(found.values())))
    return lined


def line_items(lined: Sequence[tuple[str, list[dict]]]) -> Iterator[tuple[dict, dict[str, Noul]]]:
    """Each line beside each recorded answer of his found for it as an item of `fill`, every line's nearest answer before any line's next, the line and the answer whole with one LINE Noul keyed by the line's number and the answer's id, beside AGAINST, which the request holds once, so a request with no room for every answer still reads each line against its nearest."""
    pairs = sorted(((rank, number, line, answer) for number, (line, found) in enumerate(lined, 1) for rank, answer in enumerate(found)), key=lambda pair: pair[:2])
    for _, number, line, answer in pairs:
        yield {"against": AGAINST, "lines": {f"l{number}": line}, "answers": {answer["id"]: {"asked": answer["asked"], "said": answer["said"], "area": answer["area"]}}}, {f"{LINE_KEY}{number}.{answer['id']}": asking(LINE, line=f"l{number}", answer=answer["id"])}


def line_read(lined: Sequence[tuple[str, list[dict]]], read: dict[str, float]) -> list[tuple[str, dict, float]]:
    """Each line beside the recorded answer of his the reader reads likeliest as going against it or asking him again, and how sure it is of it, NO or more, from `read`, its answers to the LINE Noul line_items asks for each line beside each recorded answer of his found for it; a line it reads under NO for every answer is left out."""
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
    """Each of these lines Jev reads, by `line_read` in one request an answer holding only `against`, that answer and each line it was found for, so no answer goes to Jev twice, at YES or more, against the recorded answers of his `line_answers` finds for it floored, as saying no ruling of his covers, putting up for change or asking him again an item that answer, given before it, orders or keeps, beside that answer, but a line naming by id a later answer of his that lifts that one, by `lifted_in`; Unheard when Jev gives no reading. A line whose words after its first colon, or whole words when it has none, are only none, as a bare `open: none` or a `none` list item, names no item to go against, so it reaches neither ccn nor Jev, which would otherwise read it against whatever answer its key word ranks first."""
    lined = line_answers([line for line in lines if not none_only(line.partition(":")[2] or line)], data, floored=True)
    grouped: dict[str, list[tuple[dict, dict[str, Noul]]]] = {}
    for item in line_items(lined):
        grouped.setdefault(next(iter(item[0]["answers"])), []).append(item)
    return [(line, answer) for line, answer, sure in line_read(lined, jev_all([together(items) for items in grouped.values()])) if reading(sure) and not lifted_in(line, answer, data)]


def lifted_in(line: str, answer: dict, data: dict) -> bool:
    """Whether the line names by id a later answer of his, still standing, that Opus reads replacing this older one, by `replacing` on that pair alone, at YES or more, which then marks the older superseded by it in cc-notes, so no brief or judge binds it again and the line keeps his newer words; a line naming no other id reads nothing; Unheard when Opus gives no reading."""
    if not (typed := [each for each in dict.fromkeys(ANSWER_REF.findall(line)) if each[:7] != answer["id"][:7]]):
        return False
    cwd = data["roles"]["coordinator"]["cwd"]
    named = [found for each in typed if (found := answer_named(each, cwd)) and "superseded_by" not in found and his(found["body"])]
    if not named:
        return False
    given = state.parse_time(shown(answer["id"], cwd)["created_at"])
    for newer in named:
        if state.parse_time(newer["created_at"]) > given and reading(replacing(newer["title"], his(newer["body"])[:BODY_CHARS], [answer]).get(answer["id"])):
            supersede(answer["id"], newer["id"], cwd)
            return True
    return False


def overruling(picks: list[tuple[str, dict]]) -> str:
    """Each overruled line beside his answer it goes against, by its id, the area it rules on and his words."""
    return "; ".join(
        OVERRULED.format(line=cut(line, 200), id=answer["id"], asked=cut(answer["asked"], 200), link=f" ({answer['link']})" if answer["link"] else "", area=f", ruling on {', '.join(answer['area'])}" if answer["area"] else "", said=cut(answer["said"], QUOTED_CHARS))
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


def deciding(asked: limits.Question, answers: list[dict]) -> list[dict]:
    """The recorded answers of his Opus reads, at YES or more, as deciding the card's question or making it moot, read in one request against the card's question and options, one Noul an answer keyed by its id, each answer's words whole; none when Opus gives no reading, said on stderr, and an answer in the band between NO and YES decides nothing."""
    if not answers:
        return []
    card = {"question": asked.title, "options": [option for option in asked.options if option != limits.ALREADY_ANSWERED]}
    items = (({"answers": {answer["answer"]: {"asked": answer["asked"], "said": answer["words"]}}}, {f"decides.{answer['answer']}": asking(DECIDING, answer=answer["answer"])}) for answer in answers)
    try:
        read = opus(*together([({"card": card}, {}), *items]))
    except Unheard as failure:
        print(f"drive: Opus gave no reading of which recorded answers of his decide this question, so its card draws no {limits.ALREADY_ANSWERED} option: {failure}", file=sys.stderr)
        return []
    return [answer for answer in answers if reading(read[f"decides.{answer['answer']}"])]


def leaving(data: dict, job: dict) -> Leave | None:
    """The ruling of his that leaves some of this job's questions to it: on a drive tooling order his ruling on tooling details, and for a builder or codex lane on a feature his words ordered designed from scratch, by state.fresh, his ruling that only the design's crucial directional decisions reach him; None for any other job, whose every question is his."""
    if state.tooling(data["orders"][job["order"]]):
        return TOOLING_DETAILS
    return FRESH_DETAILS if job["kind"] in ("builder", "codex") and state.fresh(data, job) else None


def detail(case: str, words: list[dict], leave: Leave) -> bool:
    """Whether a card asks him only what this ruling of his leaves to its job: Opus reads its options under NO by the ruling's Noul, in one request holding the card, the ruling and each of his words whole; one Opus reads in the band between NO and YES, or gives no reading of, goes to him, the latter said on stderr."""
    items = (({"words": {f"w{number}": {field: word[field] for field in ("asked", "said", "rule") if field in word}}}, {}) for number, word in enumerate(words, 1))
    try:
        read = opus(*together([({"card": {"text": case}, "ruling": {"id": leave.ruling, "said": leave.said}}, {"preference": leave.noul}), *items]))
    except Unheard as failure:
        print(f"drive job ask: Opus gave no reading of whether this card asks him only {leave.what}, so it goes to him: {failure}", file=sys.stderr)
        return False
    return reading(read["preference"]) is False


def ruled_close(option: str, link: str | None, ruling: str) -> dict:
    """How a job's question ends when a ruling of his decides it on its recommended option with no card: answered by that ruling, the one `leaving` names for a card asking him only what it leaves to the job, else state.REHEARSAL_RULING for a release on staging or sandbox alone."""
    return state.new_close("answered", RULED[ruling][0].format(option=option), link, ruling=ruling)


def approved(option: str, answer_id: str, link: str) -> dict:
    """How a question his standing approval covers ends: answered by that approval on its recommended option."""
    return state.new_close("answered", APPROVED.format(option=option, answer=answer_id[:7]), link, answer=answer_id)


def approvals(data: dict) -> dict[str, dict]:
    """His standing approvals by answer id, as `drive answer --approves` kept them: each with an order it names, or an order of a card, part or PR it names, still open, no Hold of his on a PR it covers, and no newer answer of his superseding it in cc-notes, read in one list only when one is kept."""
    kept = {
        answer_id: approves
        for answer_id, answer in data["answers"].items()
        if (approves := answer["approves"]) and not approves["held"] and any(data["orders"][order_id]["state"] not in state.CLOSED_ORDER_STATES for order_id in approves["on"])
    }
    if not kept:
        return {}
    gone = replaced(data["roles"]["coordinator"]["cwd"])
    return {answer_id: approves for answer_id, approves in kept.items() if answer_id not in gone}


def approved_by(data: dict, key: str | None, order_id: str, work: dict) -> str | None:
    """The id of his standing approval work on an order goes ahead under with no card: one naming its key or order, else the first whose words and Notes Opus reads at YES as holding the work, in one request, as ordered_by reads his words for a release, by his words 1edd6dd "make sure opus or fable is making proper decisions here"; None when none does, Opus unheard said on stderr."""
    standing = approvals(data)
    if named := next((answer_id for answer_id, approves in standing.items() if state.approval_covers(approves, key, order_id)), None):
        return named
    if not standing:
        return None
    slots = {answer_id: f"a{number}" for number, answer_id in enumerate(standing, 1)}
    told = {"work": work, "approvals": {slots[answer_id]: {"asked": approves["asked"], "said": approves["said"]} for answer_id, approves in standing.items()}}
    try:
        read = opus(told, {f"inside.{slot}": asking(INSIDE, slot=slot) for slot in slots.values()})
    except Unheard as failure:
        print(f"drive: Opus did not read whether a standing approval of his covers {key or order_id}, so it asks him: {failure}", file=sys.stderr)
        return None
    return next((answer_id for answer_id, slot in slots.items() if reading(read[f"inside.{slot}"])), None)


def restarts(words: str) -> bool:
    """Whether his words order the design of the work they are on started over from scratch, as Jev reads them at YES; no, said on stderr with the command that marks it, when Jev gives no reading."""
    try:
        read = jev({"words": words[:BODY_CHARS]}, {"restarts": RESTARTS})
    except Unheard as failure:
        print(f"drive: Jev did not read whether his words restart a design from scratch, so nothing is marked fresh; when they do, run drive order set <o> --fresh: {failure}", file=sys.stderr)
        return False
    return read["restarts"] >= YES


def decided(title: str, words: str, older: dict[str, dict]) -> set[str]:
    """The older questions of a job that his answer to its newer one settles whole, as Opus reads it in one request, one Noul a question, each read at YES or more; Unheard when Opus gave no reading."""
    shorts = {key.rpartition(":")[2]: key for key in older}
    items = (({"open": {short: {"asked": older[key]["title"], "options": older[key]["options"]}}}, {f"supersedes.{short}": asking(SUPERSEDES, slot=short)}) for short, key in shorts.items())
    return {shorts[key.partition(".")[2]] for key, sure in opus(*together([({"answer": {"asked": title, "said": words[:BODY_CHARS]}}, {}), *items])).items() if reading(sure)}


def repeats(data: dict, words: str, tooling: bool) -> str | None:
    """The open order new drive tooling words join unasked, by his pick 3caa184 "Jev joins a repeat to its open order: one card per fault": the open tooling build order Jev reads them as asking for again, the same defect or change, by REPEATS, surest at YES or more, in one request holding only his words and the REPEATS_ASKED open build orders on their side whose title and words share the most rare words with them, each order's title and words whole, one Noul an order, refused when Jev gives no answer. His order joins none but by its --joins, so for it Jev reads the open build orders of his work by FEATURE and names on stderr the surest at NO or more for --joins, and a read Jev gives none of is said there and adds the order alone; on either side an order in the band between NO and YES is named for --joins. An incident joins only by its --joins, and an answer order is no feature."""
    said = rare(words)
    orders = [
        (order_id, order)
        for order_id, order in state.open_orders(data).items()
        if order["text"].startswith(state.TOOLING) == tooling and order["kind"] == "build" and not order["incident"]
    ]
    closest = sorted(orders, key=lambda item: len(said & rare(f'{item[1]["title"] or ""} {item[1]["text"]}')), reverse=True)[:REPEATS_ASKED]
    question = REPEATS if tooling else FEATURE
    if not closest:
        return None
    questions = {f"repeats.{order_id}": asking(question, slot=order_id) for order_id, _ in closest}
    try:
        read = jev({"order": words[:BODY_CHARS], "open": {order_id: " — ".join(filter(None, [order["title"], order["text"]])) for order_id, order in closest}}, questions)
    except Unheard as failure:
        if not tooling:
            print(f"drive order add: Jev did not read these words against the {len(questions)} open orders of his work sharing the most words with them ({failure}); name the order they are on with --joins <o> when one is", file=sys.stderr)
            return None
        raise DriveError(
            f"no order added: Jev did not read these words against the {len(questions)} open drive tooling orders sharing the most words with them ({failure}),"
            " so a repeat of one would start a second order, builder and card on it; run it again, or name the order it repeats with --joins <o>"
        ) from None
    surest = max(read, key=read.__getitem__)
    order_id = surest.partition(".")[2]
    found = reading(read[surest])
    if tooling and found:
        return order_id
    if tooling and found is None:
        print(f"drive order add: Jev is unsure whether these words repeat {order_id}; name it with --joins {order_id} when they do", file=sys.stderr)
    if not tooling and found is not False:
        print(
            f"drive order add: Jev {'reads these words as' if found else 'is unsure whether these words are'} on the feature of {order_id}, which an order of his joins only by --joins {order_id}; name it when they are",
            file=sys.stderr,
        )
    return None


def same_changes(data: dict, order_id: str, part: str) -> list[str]:
    """Each undelivered part of another open order, as `<order>:<part>`, and each open drive PR, by its key, that Jev reads at YES or more as already making the change a new part of the order names, against the REPEATS_ASKED of them whose part name with its order's title, or PR title, shares the most rare words with the part's name, none sharing none, one Noul each, all in one request holding only the part's name, its order's title and words and each of them; Unheard when Jev gives no reading."""
    said = rare(part)
    open_work = [
        *(
            (f"{other_id}:{other_part}", f'"{other_part}", a part of {other["title"] or cut(other["text"], 200)}')
            for other_id, other in state.open_orders(data).items()
            if other_id != order_id
            for other_part in other["parts"]
            if not state.delivered(data, other_id, other_part)
        ),
        *((key, f'pull request "{pr["title"]}"') for key, pr in data["prs"].items() if pr["state"] == "open" and pr["title"]),
    ]
    closest = sorted((item for item in open_work if said & rare(item[1])), key=lambda item: len(said & rare(item[1])), reverse=True)[:REPEATS_ASKED]
    slots = {f"c{number}": key for number, (key, _) in enumerate(closest, 1)}
    if not closest:
        return []
    order = data["orders"][order_id]
    read = jev(
        {"part": {"name": part, "order": " — ".join(filter(None, [order["title"], order["text"][:BODY_CHARS]]))}, "open": {slot: text for slot, (_, text) in zip(slots, closest, strict=True)}},
        {f"same.{slot}": asking(SAME_CHANGE, slot=slot) for slot in slots},
    )
    return [slots[key.partition(".")[2]] for key, sure in read.items() if sure >= YES]


def asks_page(words: str) -> bool:
    """Whether his order's words name a goal or ask where work stands, so the drive adds a page to it, as Jev reads them at YES; refused when Jev gives no answer, since an order added unread would lose its page."""
    try:
        read = jev({"order": words[:BODY_CHARS]}, {"page": ASKS_PAGE})
    except Unheard as failure:
        raise DriveError(f"no order added: Jev did not read whether these words name a goal or ask where work stands ({failure}), so its page would go unbuilt; run it again") from None
    return read["page"] >= YES


def cadence(words: str) -> int:
    """The minutes his words ask a line every, as "every 15 mins", "every half hour" or "every hour" name them, when Jev reads them as asking that line at YES, so the drive sets the cadence itself when he says it; 0 for words naming no such minutes, which Jev is not asked, for words naming them of something else, such as how often a tool repeats, and when Jev gives no answer, which a note on stderr says so --every can set it."""
    if not (named := CADENCE.search(words)):
        return 0
    minutes = int(named["minutes"]) if named["minutes"] else 30 if named["half"] else 60
    try:
        read = jev({"words": words[max(0, named.start() - BODY_CHARS // 2) :][:BODY_CHARS], "minutes": minutes}, {PACED: ASKS_CADENCE})
    except Unheard as failure:
        print(f"drive order add: Jev did not read whether these words ask a line every {minutes} minutes ({failure}), so the order has no cadence; --every {minutes} sets one", file=sys.stderr)
        return 0
    return minutes if read[PACED] >= YES else 0


def unordered(answer: dict, release: str, data: dict) -> str | None:
    """Why his answer does not order this release, one `ci release start` or `hotfix`; None when it does, the one reading the release gates and `drive job ask` share, by ordered_by(). A staging release marked --for-production, which the release tool starts on every staging env through every staging freeze, a teammate's included, is ordered only by an answer ordering the production release it tests, by tests(), never by his words to test on staging or his standing yes to soak there."""
    from drive import brief

    [flags] = brief.started(release, data)
    if FOR_PRODUCTION not in flags:
        return ordered_by(answer, release, flags, placed, data)
    tested = brief.tested_release(release, data)
    return (why := ordered_by(answer, tested, flags, tests, data)) and TESTED.format(release=tested, why=why)


def ordered_by(answer: dict, release: str, flags: dict, matches: Callable[[dict, dict, str, str], str | None], data: dict) -> str | None:
    """Why his answer does not order a release, its flags as the release provider's start() reads them and the start Opus reads; None when it does. When the card his answer answers is kept, found by answered_card(), and his leading option number or tap picks an option whose words name a release in backticks, code decides: his answer orders the release when matches, placed() or tests(), reads it shipping what one of those ships, its `--only` left out by narrowed() where the card says those components ship alone, placed() every flag the same but its places, `--from` and `--order`, on no place beyond that option's but those his words past the number name, by env id or by a name the release provider gives that place's team. A picked option naming no release in backticks orders, in code too, a release its words name whole, as named() reads it. An answer no kept card holds, to a question listing releases as numbered items, his own or one the drive recorded the same message of his under, by message_questions(), orders in code a release one item it picks orders, by listed_order(). Any other answer Opus reads in one request, his words and the drive's notes on them beside that card, his answers recorded before it in its thread, by threaded(), each whole, and the places the release runs on with their stages and the components its `--only` ships alone, by runs_on(), one Noul whether his words have that release run and one whether it ships more than they order: it orders the release at YES on the first and under NO on the second; Unsure when either reads in the band between, and Unheard when Opus gave no answer."""
    from drive import brief

    said = his(answer["body"])
    asked = answered_card(data, answer)
    cwd = data["roles"]["coordinator"]["cwd"]
    if asked and (pick := picked_option(asked, said)):
        number, rest = pick
        option = asked.options[number - 1]
        words = f"{option}\n{asked.hints.get(option, '')}"
        if commands := brief.card_releases(words, data):
            carded = "\n".join([*asked.summary, words])
            whys = [matches(narrowed(flags, ordered, carded), ordered, rest, cwd) for ordered in (brief.started(command, data)[0] for command in commands)]
            if None in whys:
                return None
            return f"it picks option {number} of the card it answers, {', '.join(f'`{command}`' for command in commands)}, {whys[0]}"
        if named(flags, words, rest, cwd):
            return None
    if not asked and any(listed_order(said, question, flags, matches, data) for question in message_questions(answer, said, data)):
        return None
    card = {"card": card_text(asked)} if asked else {}
    notes = {"notes": answer["body"].partition(NOTES)[2].strip()[:BODY_CHARS]} if said and NOTES in answer["body"] else {}
    context = {"action": f"start this release on {runs_on(brief.started(release, data)[0], cwd)}: `{release}`", "answer": {"asked": answer["title"], "said": said} | notes} | card
    read = opus(*together([(context, {f"orders.{answer['id'][:7]}": ORDERS, "more": SHIPS_MORE}), *(({"earlier": {earlier["id"][:7]: earlier_words(earlier)}}, {}) for earlier in threaded(answer, data))]))
    orders, more = reading(read[f"orders.{answer['id'][:7]}"]), reading(read["more"])
    if orders and more is False:
        return None
    if orders is False or more:
        return UNORDERED
    raise Unsure(f"Opus is unsure whether his answer {answer['id'][:7]} approves this release")


def listed_order(said: str, question: str, flags: dict, matches: Callable[[dict, dict, str, str], str | None], data: dict) -> bool:
    """Whether his words, answering a question that lists releases as numbered items, `1) … 2) …`, order this release in code: they lead with all, alone or with the number of items, as "All 3 of these are approved" does, and hold no word UNAPPROVED reads as turning one down or holding it; and one item orders it, an item naming releases in backticks as a card option does, by matches() with narrowed(), and any other item by spoken()."""
    from drive import brief

    cwd = data["roles"]["coordinator"]["cwd"]
    items = numbered(question)
    every = EVERY.match(said)
    if not items or not every or (every[1] and int(every[1]) != len(items)) or UNAPPROVED.search(f" {plain(said)} "):
        return False
    for item in items:
        if commands := brief.card_releases(item, data):
            if any(matches(narrowed(flags, ordered, item), ordered, "", cwd) is None for ordered in (brief.started(command, data)[0] for command in commands)):
                return True
        elif spoken(flags, item, cwd):
            return True
    return False


def message_questions(answer: dict, said: str, data: dict) -> Iterator[str]:
    """The questions his one message answers: his answer's own, then each other answer's the drive kept at the same link with the same words, as `drive answer` keeps one message recorded under a second question, read with shown()."""
    yield answer["title"]
    link = (data["answers"].get(answer["id"]) or {}).get("link")
    for answer_id, kept in data["answers"].items():
        if link and answer_id != answer["id"] and kept.get("link") == link and plain(kept.get("words", "")) == plain(said):
            yield shown(answer_id, data["roles"]["coordinator"]["cwd"])["title"]


def numbered(question: str) -> list[str]:
    """The items a question lists as `1) … 2) …`, numbered 1 on in turn; none when it lists none."""
    parts = NUMBERED.split(question)
    if [int(number) for number in parts[1::2]] != list(range(1, len(parts) // 2 + 1)):
        return []
    return [item.strip().rstrip(";,.").strip() for item in parts[2::2]]


def spoken(release: dict, words: str, cwd: str) -> bool:
    """Whether words naming a release in prose, with no command, name this release, as the release provider's start() reads it: a start of targets the release targets file names, with no flags but targets, places, `--from`, `--order`, `--emergency`, `--only`, `--exclude` and `--for-production`, shipping one component or more by shipped_parts(); words leaving one target or component out, as "all but SandSQL" does, name it when it ships none of that one's components, and any other words when each target it names is one of their words, as "SandSQL's HA release" names `sandsql`."""
    if set(release) - SPOKEN_FLAGS or not release.get("targets") or not (ships := shipped_parts(release, cwd)):
        return False
    said = f" {plain(words)} "
    if left := ALL_BUT.search(said):
        parts = target_parts(cwd)
        out = set(parts.get(left[1], ()))
        if not out and not any(left[1] in named for named in parts.values()):
            return False
        return not ships & (out or {left[1]})
    return all(f" {plain(target)} " in said for target in release["targets"])


def shipped_parts(release: dict, cwd: str) -> set[str]:
    """The components a release ships, as the release tool reads its flags: those of each target it names, by target_parts(), less each `--exclude` names and each component `--without` names whole, and only those `--only` names when it names any; none when it names a target the targets file does not."""
    parts = target_parts(cwd)
    if any(target not in parts for target in release["targets"]):
        return set()
    ships = {part for target in release["targets"] for part in parts[target]} - {*release.get("exclude", ()), *release.get("without", ())}
    return ships & {only.partition("/")[0] for only in release["only"]} if release.get("only") else ships


def target_parts(cwd: str) -> dict[str, tuple[str, ...]]:
    """Each release target's components, as the release provider of the repository cloned at the drive's checkout lists them."""
    return providers.at(Path(cwd)).release.parts()


def platform_targets(data: dict) -> list[str]:
    """The targets a release of the whole platform ships, as the targets of the repository's release provider name them; none without a release provider."""
    return providers.of(data).release.targets()


def unexcused(carried: set[str], whole: list[str], said: str, rulings: list[dict]) -> list[str]:
    """Each target of whole, the platform's targets by platform_targets(), that a release carrying the targets in carried leaves out and that said neither records as `<target> fails: <the failing test>` nor as `<target> left out by his <id>: "<his words>"` quoting one of rulings, each an answer of his as {answer, words}, by his words SCOPE_WORDS; none for a release carrying half the platform's targets or fewer, one his words name target by target."""
    dropped = [target for target in whole if target not in carried]
    if (len(whole) - len(dropped)) * 2 <= len(whole):
        return []
    return [target for target in dropped if not excused(target, said, rulings)]


def excused(target: str, said: str, rulings: list[dict]) -> bool:
    if re.search(rf"(?<![\w-]){re.escape(target)} fails: \S", said):
        return True
    cited = re.search(rf"(?<![\w-]){re.escape(target)} left out by his (\w+): \"([^\"]+)\"", said)
    return bool(cited) and any(ruling["answer"][:7] == cited[1][:7] and plain(cited[2]) in plain(ruling["words"]) for ruling in rulings)


def threaded(answer: dict, data: dict) -> list[dict]:
    """His answers this drive recorded before this one in the Slack thread where he gave it, newest first, as listed() reads them in one call, so words of his such as all 3 of these read as what his earlier words there named; none when the drive kept no thread link for it."""
    links = {whole[:7]: kept["link"] for whole, kept in data["answers"].items() if kept.get("link")}
    if not (thread := view.card_thread(links.get(answer["id"][:7], ""))):
        return []
    earlier = listed(f"drive:{data['name']}", data["roles"]["coordinator"]["cwd"])
    return [other for other in earlier if other["created_at"] < answer["created_at"] and view.card_thread(links.get(other["id"][:7], "")) == thread and his(other["body"])]


def earlier_words(answer: dict) -> dict:
    """One earlier answer of his as Opus reads it beside a later one in its thread: its question, his words and the drive's notes on them."""
    return {"asked": answer["title"], "said": his(answer["body"])[:BODY_CHARS], "notes": answer["body"].partition(NOTES)[2].strip()[:BODY_CHARS]}


def card_text(asked: limits.Question) -> str:
    """The card his answer answers as Opus reads it beside his words: its question, summary and options."""
    return "\n".join([f"Question: {asked.title}", *asked.summary, *limits.offered(asked)])


def job_unordered(job: dict, hold: dict, data: dict, moment: datetime) -> str | None:
    """Why no answer of his recorded after his hold on every release orders this Job, a `tools/ci cluster-job` start of one CronJob on one cluster, at this moment; None when one does, the reading unordered() makes of a release for a Job, whose only words are its reason: each answer of his its reason names, as a release names its --order, and each `matched` ranks nearest the Job, read whole with shown(), those recorded after the hold Opus reads in one request, each with its card, beside the others, the hold and this moment in Pacific, ordering it when one JOB_ORDERS Noul reads YES. The why names each answer its reason names, each one recorded before the hold, and each Opus reads as speaking to the Job by JOB_ABOUT or is unsure of, with his words, raised as Unsure when Opus reads none at YES and one in the band between. Unheard when Opus gave no answer."""
    cwd = data["roles"]["coordinator"]["cwd"]
    action = f"start the {job['cronJob']} Job from its suspended CronJob on {job['cluster']}: {job['reason']}"
    nearest = [shown(answer["id"], cwd) for answer in matched([action], data)]
    named = [answer for typed in dict.fromkeys(ANSWER_REF.findall(job["reason"])) if (answer := answer_named(typed, cwd))]
    named_ids = {answer["id"][:7] for answer in named}
    read = {answer["id"][:7]: answer for answer in [*named, *nearest] if his(answer["body"])}
    since = {slot: answer for slot, answer in read.items() if answer["created_at"] > hold["created_at"]}
    said: dict[str, float] = {}
    if since:
        context = {"job": {"cronjob": job["cronJob"], "cluster": job["cluster"], "reason": job["reason"], "now": pacific(moment)}, "hold": {"said": his(hold["body"])[:BODY_CHARS], "at": pacific(state.parse_time(hold["created_at"]))}}
        items = [({"answers": {slot: answer_words(answer, data)}}, {f"orders.{slot}": asking(JOB_ORDERS, slot=slot), f"about.{slot}": asking(JOB_ABOUT, slot=slot)}) for slot, answer in since.items()]
        said = opus(*together([(context, {}), *items]))
        if any(reading(said.get(f"orders.{slot}")) for slot in since):
            return None
    early = [f'its reason names his answer {slot} to "{cut(answer["title"], 200)}", recorded before the hold' for slot, answer in read.items() if slot in named_ids and slot not in since]
    spoken = [
        f'his answer {slot} to "{cut(answer["title"], 200)}", "{cut(his(answer["body"]), QUOTED_CHARS)}", {"which Opus does not read as ordering it now" if reading(said[f"orders.{slot}"]) is False else "which Opus is unsure orders it now"}'
        for slot, answer in since.items()
        if slot in named_ids or reading(said[f"about.{slot}"]) is not False
    ]
    why = "; ".join([*early, *spoken]) or "its reason names no answer of his, and Opus reads none of his answers recorded since that are nearest it as speaking to it"
    if any(reading(said[f"orders.{slot}"]) is None for slot in since):
        raise Unsure(why)
    return why


def answer_named(typed: str, cwd: str) -> dict | None:
    """His answer a word of a Job's reason names by its id or a prefix of it, as shown() reads it; None when it names no answer, as a commit sha in the reason does."""
    with suppress(DriveError):
        return shown(typed, cwd)
    return None


def answer_words(answer: dict, data: dict) -> dict:
    """One answer of his as Opus reads it beside a Job: its id, question, his words, when he gave them in Pacific and the card it answers, by answered_card()."""
    asked = answered_card(data, answer)
    return {"id": answer["id"][:7], "asked": answer["title"], "said": his(answer["body"])[:BODY_CHARS], "at": pacific(state.parse_time(answer["created_at"])), "card": card_text(asked) if asked else ""}


def pacific(moment: datetime) -> str:
    """A moment as he reads it, in Pacific with its weekday, so Opus can tell whether a time his words set, such as tonight, holds."""
    return moment.astimezone(view.ZONE).strftime("%A %b %-d %Y, %-I:%M %p Pacific")


def read_under(title: str, options: list[str], words: str) -> dict[str, bool | None]:
    """How Jev reads his words under a card beside its question and options, in one request with no `claude` process, so the card command needs none on its PATH: `answers_nothing`, whether they only ask it questions, one proposing a way included, or remark on it, so they answer nothing, and `beside`, whether they order other work beside it, leaving what it asks as it stands; Unheard when Jev gave no answer."""
    answers = jev({"card": {"question": title, "options": options}, "his_words": words[:BODY_CHARS]}, {"answers_nothing": ANSWERS_NOTHING, "beside": BESIDE})
    return {key: reading(sure) for key, sure in answers.items()}


def answered_card(data: dict, answer: dict) -> limits.Question | None:
    """The card his answer answers, as `drive job ask` kept it: a question no newer question replaced, drawn on the card at the link `drive answer` kept for his answer or ended by his answer, as his tap on a card ends each question drawn there and keeps his answer's id on it, ended answered, whose ASK file stands, in this drive, else in each drive on this Mac its `drive:<name>` labels name, where `drive answer` recorded it, so an answer he gave on one drive's card reads the same in every drive; None when none does."""
    labelled = (name for tag in answer["tags"] if (name := tag.removeprefix("drive:")) not in (tag, data["name"]) and state.exists(name))
    for drive in chain([data], map(state.load, labelled)):
        link = (drive["answers"].get(answer["id"]) or {}).get("link")
        drawn = [question for _, question in view.drawn_on(drive, link)] if link else []
        ended = [question for job in drive["jobs"].values() for question in job["questions"] if question["closed"] and answer["id"] in (question["closed"]["answer"], question["closed"]["noted"])]
        files = [Path(question["file"]) for question in [*drawn, *ended] if question["file"] and not question["replaced"] and (question["closed"] or {}).get("how") == "answered"]
        if kept := next((file for file in files if file.is_file()), None):
            return limits.question_problems(limits.ask_block(kept.read_text()), kept=True)[0]
    return None


def picked_option(asked: limits.Question, said: str) -> tuple[int, str] | None:
    """The option of the card his words pick, by its number leading them or by his tap, which keeps its words, with his words past the number; None when they pick none."""
    if (number := LEADING_NUMBER.match(said)) and 1 <= int(number[1]) <= len(asked.options):
        return int(number[1]), said[number.end() :]
    tapped = next((index for index, option in enumerate(asked.options, 1) if plain(option) == plain(said)), None)
    return (tapped, "") if tapped else None


def narrowed(release: dict, option: dict, words: str) -> dict:
    """A release as the card option's release it narrows: its `--only` left out when the option's release names none and the card's summary, option or description says each component that `--only` names ships alone, as "The hotfix ships the api alone" or "it ships only the api" does, and "changes only the api" or a clause holding a negation, as "it does not ship the api alone", does not, since the release tool then ships those components of what the option's release ships and nothing past them; else the release as it is."""
    if "only" in option or "only" not in release:
        return release
    said = [f" {plain(clause)} " for clause in limits.CLAUSE_END.split(words) if not limits.NEGATION.search(clause)]
    if all(any(re.search(ALONE.format(component=re.escape(plain(component))), clause) for clause in said) for component in release["only"]):
        return {name: values for name, values in release.items() if name != "only"}
    return release


def placed(release: dict, option: dict, rest: str, cwd: str) -> str | None:
    """Why a release does not ship what a card option's release ships, each read by the release provider's start(), on the option's places and those his words past its number name; None when it does."""
    if {name: values for name, values in release.items() if name not in UNCHANGED} != {name: values for name, values in option.items() if name not in UNCHANGED}:
        return "and this release ships another change"
    if (added := beyond(release, option)) is None:
        return "and this release runs on every place it does not leave out, where that option names its own"
    words = f" {plain(rest)} "
    if unnamed := sorted(place for place in added if not any(f" {plain(name)} " in words for name in (place, *team_names(place, cwd)))):
        return f"and this release also runs on {', '.join(unnamed)}, which his words past its number do not name"
    return None


def tests(release: dict, option: dict, rest: str, cwd: str) -> str | None:
    """Why a staging release marked --for-production does not test the production release a card option's release ships, each read by the release provider's start(); None when it does: the option's release runs on a production place, by off_production(), and placed() reads the staging release shipping its change wherever it runs, every flag the same but --for-production and the places, its --env and each place its --without names that the release provider knows, which it takes from the option."""
    if off_production(option, cwd):
        return UNTESTED
    left = sorted({*(name for name in release.get("without", ()) if not known(name, cwd)), *(name for name in option.get("without", ()) if known(name, cwd))})
    change = {name: values for name, values in release.items() if name not in (FOR_PRODUCTION, "env", "without")}
    return placed(change | {name: values for name, values in (("env", option.get("env")), ("without", tuple(left))) if values}, option, rest, cwd)


def named(release: dict, words: str, rest: str, cwd: str) -> bool:
    """Whether a card option's words, naming no release in backticks, name this release whole, as the release provider's start() reads it: it carries only targets, places, `--from`, `--order` and `--emergency`, at least one target and one place it runs on, each target a word of the option's and each place one of the option's or of his words past its number, by env id or its team's name."""
    places = set(release.get("env", ())) - set(release.get("without", ()))
    if set(release) - PROSE_FLAGS or not release.get("targets") or not places:
        return False
    option, both = f" {plain(words)} ", f" {plain(f'{words} {rest}')} "
    return all(f" {plain(target)} " in option for target in release["targets"]) and all(
        any(f" {plain(name)} " in both for name in (place, *team_names(place, cwd))) for place in places
    )


def beyond(release: dict, option: dict) -> set[str] | None:
    """The places a release runs on that a card option's does not, each by its env id as the release provider's start() reads it; None when the release runs on every place but those it leaves out and the option names its own."""
    if not release.get("env"):
        return None if option.get("env") else set(option.get("without", ())) - set(release.get("without", ()))
    runs = set(release["env"]) - set(release.get("without", ()))
    return runs - (set(option["env"]) - set(option.get("without", ()))) if option.get("env") else runs & set(option.get("without", ()))


def team_names(place: str, cwd: str) -> list[str]:
    """The names of a place's team, as the release provider of the repository cloned at the drive's checkout reads them; none for a place it does not know."""
    return list(providers.at(Path(cwd)).release.place(place).teams)


def known(place: str, cwd: str) -> bool:
    """Whether the release provider of the repository cloned at the drive's checkout knows a place, giving it a stage or a team."""
    found = providers.at(Path(cwd)).release.place(place)
    return found.stage is not None or bool(found.teams)


def stage(place: str, cwd: str) -> str | None:
    """A place's stage, such as staging or prod, as the release provider of the repository cloned at the drive's checkout gives it; None for a place it does not know."""
    return providers.at(Path(cwd)).release.place(place).stage


def prod(place: str, cwd: str) -> bool:
    """Whether the release provider of the repository cloned at the drive's checkout counts a place as production."""
    return providers.at(Path(cwd)).release.place(place).prod


def off_production(release: dict, cwd: str) -> bool:
    """Whether a release, as the release provider's start() reads it, runs on no production place, by the release provider: it names places and the provider gives each a stage and counts none production."""
    return bool(places := release.get("env")) and all(stage(place, cwd) and not prod(place, cwd) for place in places)


def runs_on(release: dict, cwd: str) -> str:
    """Where a release, as the release provider's start() reads it, runs, as the release tool says it: each place it names beside the stage the release provider gives it, else production, every environment except staging, each place its --without leaves out, and the components its --only ships alone, so Opus's read of whether it ships more than his words order sees them."""
    places = ", ".join(f"{place} (stage {found})" if (found := stage(place, cwd)) else place for place in release.get("env", ())) or PRODUCTION
    where = f"{places}, leaving out {', '.join(release['without'])}" if release.get("without") else places
    return f"{where}, shipping only {', '.join(release['only'])}" if release.get("only") else where


class Unheard(Exception):
    """The reader gave no answer: Jev with no key, a timeout, a request it rejected or a question it declined, or an Opus run that failed or ran out of time."""


class Unsure(Unheard):
    """Opus answered, but in the band between NO and YES on whether his words order a release or a Job, which the release gates send to the top coordinator's own reading, never to him."""


@cache
def jev_key() -> str:
    """Jev's API key, from TYPESAFE_API_KEY or else the Keychain item `spawnllm key set jev` wrote, read once per process, so no question after the first starts a process."""
    from spawnllm import Provider
    from spawnllm.decide import api_key

    return api_key(Provider("jev", MODEL), timeout=DECIDE_SECONDS)


def jev(state: dict, questions: dict[str, Noul | Pick], seconds: float = DECIDE_SECONDS, caller: str | None = None) -> dict[str, float | str]:
    """Jev's probability of yes for each Noul about the state, and the option it picks for each Pick, by question id, in one request to MODEL on the keep-alive client every question of the process shares, the key's read and the answer both within the seconds given, as TypeSafe's docs say to ask: named-field state, one judgment a question, the decision in code; Unheard when none came, Jev declining any of them included, since a failed call is never a no. spawnllm is imported on the first question, so a hook or verb that asks none never loads it."""
    import httpx
    from spawnllm import Binary, BinaryAnswer, DecideError, DecideKeyMissing, Label, LabelAnswer, Provider, decide_sync

    began = time.monotonic()
    deadline = began + seconds
    named = caller or sys._getframe(1).f_code.co_name
    sized(state, questions, named)
    try:
        token = jev_key()
        decision = decide_sync(state, {key: Label(*question) if isinstance(question, Pick) else Binary(*question) for key, question in questions.items()}, provider=Provider("jev", MODEL), timeout=deadline - time.monotonic(), api_key=token)
    except (TimeoutError, ValueError, DecideError, DecideKeyMissing, httpx.HTTPError) as error:
        timed(named, questions, time.monotonic() - began, False)
        raise Unheard(str(error)) from None
    timed(named, questions, time.monotonic() - began, True)
    if declined := [key for key, answer in decision.answers.items() if not isinstance(answer, BinaryAnswer | LabelAnswer)]:
        raise Unheard(f"Jev declined {', '.join(declined)}")
    return {key: answer.choice if isinstance(answer, LabelAnswer) else answer.p_yes for key, answer in decision.answers.items()}


JEV_SIZES = "jev-sizes.jsonl"


def timed(caller: str, questions: dict[str, Noul | Pick], seconds: float, answered: bool) -> None:
    """Keep how long one Jev request took, beside the call site and its question keys, so a slow call, such as drive order add's join read, names itself."""
    with (state.root() / JEV_SIZES).open("a") as out:
        out.write(json.dumps({"at": state.stamp(), "caller": caller, "seconds": round(seconds, 2), "answered": answered, "keys": sorted(questions)[:6]}) + "\n")


def sized(request: dict, questions: dict[str, Noul | Pick], caller: str) -> None:
    """Keep the size of one Jev request, in characters, beside the call site it serves and its question keys, so each call's input can be read as p50 and max against its contract."""
    with (state.root() / JEV_SIZES).open("a") as out:
        out.write(json.dumps({"at": state.stamp(), "caller": caller, "chars": size(request, questions), "keys": sorted(questions)[:6]}) + "\n")


def opus(told: dict, questions: dict[str, Noul | Pick], seconds: float = OPUS_SECONDS) -> dict[str, float | str]:
    """Opus's probability of yes for each Noul about the state told, and the option it picks for each Pick, by question id, as jev() answers them, in one request holding the whole state, by served.structured with no tools, so each read that can refuse, hold or card his release path is a frontier model's, by his words 1edd6dd "make sure opus or fable is making proper decisions here"; Unheard when none came, since a failed run is never a no. Its `claude -p` only reads, by state.reads_only, so a dry run lets it run and state.remembered answers a repeated one from its memo."""
    from drive import served

    if not questions:
        return {}
    slots = {f"q{number}": key for number, key in enumerate(questions, 1)}
    schema = {
        "type": "object",
        "properties": {slot: {"type": "string", "enum": list(question.options)} if isinstance(question := questions[key], Pick) else {"type": "number", "minimum": 0, "maximum": 1} for slot, key in slots.items()},
        "required": list(slots),
        "additionalProperties": False,
    }
    prompt = json.dumps({"state": told, "questions": {slot: questions[key]._asdict() for slot, key in slots.items()}}, ensure_ascii=False, indent=1)
    try:
        read = served.structured(state.root(), OPUS_RULES, prompt, schema, seconds, "Opus's read")
    except DriveError as error:
        raise Unheard(str(error)) from None
    return {key: read[slot] for slot, key in slots.items()}


def reading(sure: float | None) -> bool | None:
    """A Noul's answer as code decides on it: yes at YES or more, no under NO, and None in the band between and for a question the request had no room for, which each check handles as its own row."""
    return None if sure is None else True if sure >= YES else False if sure < NO else None


def asking(noul: Noul, **slots: object) -> Noul:
    """The Noul with the state paths its question names filled in, its yes and no as they are."""
    return noul._replace(ask=noul.ask.format(**slots))


def jev_all(requests: list[tuple[dict, dict[str, Noul]]]) -> dict[str, float]:
    """Jev's reads of every request asking anything, at once, merged by question id; Unheard when any one gives none."""
    if not (asking := [request for request in requests if request[1]]):
        return {}
    with ThreadPoolExecutor(len(asking)) as pool:
        caller = sys._getframe(1).f_code.co_name
        return {key: sure for read in pool.map(lambda request: jev(*request, caller=caller), asking) for key, sure in read.items()}


def joined(state: dict, part: dict) -> dict:
    """The state with an item's state merged in at every depth, so an item adding one field of a nested object, such as one input field of a call, keeps the fields earlier items added."""
    return state | {key: joined(state[key], value) if isinstance(value, dict) and isinstance(state.get(key), dict) else value for key, value in part.items()}


def size(state: dict, questions: dict[str, Noul]) -> int:
    """The characters of the request Jev gets for this state and these questions, as JSON."""
    return len(json.dumps({"state": state, "questions": {key: noul._asdict() for key, noul in questions.items()}}, ensure_ascii=False))


BACKED = Noul(
    "Does `reads.{read}.result`, that call's whole output, show what `claims.{claim}.claim` says?",
    yes="The read's own output holds what the claim says, in its words or nearly, as it stands now for a state told as it stands, a record's or report's line in it showing the read behind it holding what that line states, such as whose a red is to fix, and a PR's body what that PR changes.",
    no="The read does not hold it, or only a doc, comment or log line, or a record's or report's line showing no read, inside it says so about code.",
)
BACKED_LINES = Noul(
    "Do the lines of `reads.{read}.result`, one part of that call's output by line number out of its `reads.{read}.lines`, show what `claims.{claim}.claim` says?",
    yes=BACKED.yes,
    no="These lines do not hold it, or only a doc, comment or log line, or a record's or report's line showing no read, inside them says so about code.",
)
SPANS = Noul(
    "Does `claims.{claim}.claim` say only, every, all, no, nobody or never of a set, such as the routes minting a key or the callers of a function, while `reads.{read}.call` reads one member of that set rather than what decides every member?",
    yes="The claim speaks for a whole set, and the call reads one member, such as a sed of one route's lines, so nothing in it shows the other members.",
    no="The claim speaks of the one thing the call reads; or the call lists or searches every member, such as a grep across the code, a database query's rows or a status listing; or the call reads the rule every member follows, its own words naming that set, such as a manual's rule for each kind of post; or its words of a set quote a title, an order or a name rather than speak for that set.",
)
HOLDS = Noul(
    "Does `findings.{finding}.rule` hold against `findings.{finding}.found` in the post, `post.written` as its writer typed it?",
    yes="The rule is meant to stop what the finding names in this post.",
    no="The finding's pattern matched words its rule never meant.",
)
LIFTED = Noul("Do `his_words` permit what `findings.{finding}.found` names: his own words quoted back or restated, a term he used himself, or words of his asking for it?")
FACT = Noul(
    "Does `post.sentences.{index}` state a fact about code, a number or a state of work, which stands only on a read, beyond what `claims` say, such as an only, every or never no claim says?",
    no="It tells the handler's own act, when its next line is due, his words quoted back, the title a PR or card link draws, or a time `post.stamps` lists; or each fact in it is one `claims` say, such as a lead line summing up the claims under it.",
)
SCOPE = Noul(
    "`post.written` goes in a thread of his DM about one piece of his work. Does it tell him of the whole drive's work, such as how many question cards, Approve cards or taps are open across his DM, his whole queue, or other projects' work?",
    yes="It counts, lists or ranks what waits on him across the drive, his DM or the dashboard's queue, such as 143 question cards open in his DM or the queue's first four cards.",
    no="It tells of one piece of work: its own PRs, cards, jobs, merges and staging tests, however many it names, and the release or PR they wait on, such as two Approve cards of this work and nothing else of it waiting on him.",
)
WIDER = Noul(
    "Do his words `thread.asked` name a subject wider than the work of the thread they are in, such as a project, all his cards or PRs, or the whole drive?",
    yes="His words name that wider subject, such as everything in the drive, all his open PRs or the SandSQL work.",
    no="His words ask what is left, what else waits on him, whether it all merged or where things stand, naming no wider subject: in a work's thread, everything, all the questions or all the taps mean that work's own, by his words \"we're talking about the work this thread is about\".",
)
PROTECTS = Noul(
    "Is `call` the case `rule` exists to stop?",
    yes="The call is exactly what the rule exists to stop.",
    no="The rule's pattern matched a call it was never meant to stop, or an exception the rule names covers it.",
)
NO_READ = "none: "
GOES_OUT = "message_limits: this post goes out as written;"
SET_WORDS = re.compile(r"\b(?:only|every|each|all|no|none|nobody|nothing|never)\b", re.IGNORECASE)


def weighed(request: dict) -> dict:
    """The verdict of the capt-hook pack's one Jev request on a call a drive check refused or a post it hands with his words, as `post_verdict` or `refusal_verdict` gives it within the seconds the request leaves it, the pack reading only its verdict, hold, warn or pass, and its note; a refusal from a pack capt-hook loaded before the check's output rode beside it names no findings, since capt-hook keeps the pack it first found until it restarts."""
    left = request.get("seconds")
    if "refusal" in request:
        refusal = request["refusal"]
        return refusal_verdict(refusal["rule"], refusal["call"], check_findings(refusal.get("output", {})), REFUSAL_SECONDS if left is None else min(REFUSAL_SECONDS, left))
    return post_verdict(request["post"], DECIDE_SECONDS if left is None else min(DECIDE_SECONDS, left))


def refusal_verdict(rule: str, call: dict, findings: list[dict], seconds: float = REFUSAL_SECONDS) -> dict:
    """Whether a drive check's refusal stands, by one Noul per finding over the rule it names, the call's tool and what the check found in it, each request a few hundred characters by design and never the call's whole input: it holds at YES or more and in the band between NO and YES, since a call the rule may stop waits, and passes under NO, as a match its rule never meant; when Jev cannot finish within the seconds given, REFUSAL_SECONDS at most, the call goes ahead with a warning saying so and naming each of the check's findings, never the refusal's words, which would tell the session the call never ran."""
    found = [finding["found"] for finding in findings] or [rule]
    asks = [({"rule": rule, "call": {"tool": call["tool"], "found": cut(each, QUOTED_CHARS)}}, {f"f{number}.protects": PROTECTS}) for number, each in enumerate(found, 1)]
    try:
        with ThreadPoolExecutor(len(asks)) as pool:
            reads = {key: sure for read in pool.map(lambda request: jev(*request, seconds, caller="refusal_verdict"), asks) for key, sure in read.items()}
    except Unheard as failure:
        return {"verdict": "warn", "note": f"This call goes ahead: Jev gave no verdict ({failure}) on whether it is what the refusal's rule protects against, so what the check found only warns:{warned(findings)}"}
    protects = [reading(sure) for sure in reads.values()]
    if True in protects:
        return {"verdict": "hold", "note": ""}
    if None in protects:
        return {"verdict": "hold", "note": "Jev is unsure whether this call is what its rule protects against, so the refusal stands."}
    return {"verdict": "pass", "note": ""}


def post_verdict(output: dict, seconds: float = DECIDE_SECONDS) -> dict:
    """Whether a post the drive's checks hand with his words goes out, by the requests `requests` packs, asked of Jev at once, every item Jev cannot weigh the post without whole in one of them, a read too long for one request by whole lines across several: it holds only for a claim whose read matched none of the handler's calls, which needs no Jev, since a read the handler never made backs nothing; every other post goes out as written, beside a warning naming what Jev reads as wrong in it, to reword in place: a claim whose read, or each part of it, and the drive's line for each PR it names are all under BACKS, a claim saying only, every, no or never of a set whose read reads one member rather than what decides every member at YES, a sentence no claim holds whole that states a fact beyond the claims at YES, a post in a thread of his DM holding orders that tells him of the whole drive's work at YES, and a finding Jev reads neither as a match its rule never meant, under NO, nor as lifted by his words, at YES, or that had no room among them; any Noul in the band between is named as unsure, as is an order, PR or card `cli.thread_strays` finds outside that thread's work. A warning says first that the post went out, never in a refusal's words. When Jev cannot finish, the post goes out with the checks' findings as warnings, by `unheard_post`."""
    reason = refusal_reason(output)
    reads = {item["id"]: item for item in output.get("reads") or []}
    claims = output["post"].get("claims") or []
    asks = requests(output)
    try:
        with ThreadPoolExecutor(len(asks) or 1) as pool:
            read = {key: sure for answer in pool.map(lambda request: jev(*request, seconds, caller="post_verdict"), asks) for key, sure in answer.items()}
    except Unheard as failure:
        return unheard_post(output, str(failure))
    if held := unmatched_claims(output):
        return held_back(held, reason)
    found: list[str] = []
    unsure: list[str] = []
    for number, claim in enumerate(claims, 1):
        if unmatched(reads, claim):
            continue
        if max(sure for key, sure in read.items() if key.split(".")[:2] == [f"c{number}", "backed"]) < BACKS:
            found.append(f'claim "{claim["claim"]}" does not stand on its read')
        if (spans := reading(read.get(f"c{number}.spans", 0.0))) is True:
            found.append(f'claim "{claim["claim"]}" speaks for a whole set while its read reads one member of it')
        elif spans is None:
            unsure.append(f'claim "{claim["claim"]}" speaking for more of a set than its read reads')
    for number, finding in enumerate(findings := check_findings(output), 1):
        holds, lifted = read.get(f"f{number}.holds"), read.get(f"f{number}.lifted", 0.0)
        if holds is None:
            found.append(f"{finding['found']}, which had no room in Jev's {REQUEST_CHARS:,} characters beside his words")
        elif reading(holds) is not False and reading(lifted) is not True:
            found.append(finding["found"])
    for index, sentence in uncovered(output):
        if (fact := reading(read[f"s{index}.fact"])) is True:
            found.append(f'"{sentence}" states a fact no claim holds whole')
        elif fact is None:
            unsure.append(f'sentence "{sentence}"')
    if (scope := output.get("thread_scope")) and reading(read.get("wider")) is not True:
        if scope["strays"]:
            unsure.append(f"whether {', '.join(scope['strays'])}, which the thread's work as the drive draws it does not hold, belongs in this thread")
        if (wide := reading(read.get("scope"))) is True:
            found.append("it tells him of the whole drive's work, which his words in this thread never asked about")
        elif wide is None:
            unsure.append("whether it tells him of the whole drive's work")
    notes = [*([f"Jev finds {'; '.join(found)}: reword the post in place"] if found else []), *([f"Jev is unsure of {'; '.join(unsure)}: if one is wrong, reword the post in place"] if unsure else [])]
    if notes:
        return {"verdict": "warn", "note": f"{GOES_OUT} {'. '.join(notes)}."}
    return {"verdict": "warn", "note": f"{GOES_OUT} Jev read none of these findings' rules as holding against it:{warned(findings)}"} if reason else {"verdict": "pass", "note": ""}


def unmatched_claims(output: dict) -> list[str]:
    """Each claim of a post whose read matched none of the handler's calls, as the hold names it."""
    reads = {item["id"]: item for item in output.get("reads") or []}
    return [f'claim "{claim["claim"]}" names the read {json.dumps(claim["read"], ensure_ascii=False)}, which matches none of your calls' for claim in output["post"].get("claims") or [] if unmatched(reads, claim)]


def held_back(held: list[str], reason: str) -> dict:
    """The hold on a post with a claim naming a read the handler never made, the checks' refusal beside it."""
    shown = f"\n```\n{reason}\n```" if reason else ""
    return {"verdict": "hold", "note": f"Jev held back this post: {'; '.join(held)}. So fix each, then call say again.{shown}"}


def asked(output: dict) -> tuple[dict, list[tuple[str, tuple[dict, dict[str, Noul]]]], list[tuple[dict, dict[str, Noul]]]]:
    """What Jev's requests on a post hold, the one builder `requests` packs and `unfit` sizes, so a post that passes `unfit` reaches Jev with every item it must weigh whole and each request holds only what its Nouls name: the post's typed words once, since Slack's copy of them repeats each word beside its links, which the findings request opens with and the card and thread items carry, the only Nouls naming them; each item Jev cannot weigh the post without, beside the words naming it, each read whole beside every claim naming it as `backed` gives each, and the drive's line for each PR a claim names beside that claim as `staged` gives it, one item, or one item a part of it when `parted` finds it too long for a request, since no claim is weighed without its read and a read goes to Jev once, each sentence no claim holds whole beside the post's claims, each time say drew from the drive's stamps and one Noul whether it states a fact standing only on a read beyond what they say, and the first line of the card his question in that thread is under, beside his question and one Noul whether the post answering it leaves a term of that line unexplained, and his words in the thread of his DM holding orders it goes in, beside one Noul whether the post tells him of the whole drive's work and one whether those words name a wider subject, neither reading the thread's work, whose strays code finds, so a thread holding much work still has its posts judged, when they fit a request beside the post, else left out and only warned of; then the items that go in as room allows, in a request of their own, each finding beside the rule it stands for, one Noul whether the rule holds and one whether his words lift it, and his words, the checks' own picks first, then newest first."""
    post = output["post"]
    reads = {item["id"]: item for item in output.get("reads") or []}
    words = output.get("his_words") or []
    base = {"post": {"written": post.get("written") or post.get("text") or ""}}
    stamps = {"stamps": post["stamps"]} if post.get("stamps") else {}
    shared: dict[str, list[tuple[dict, dict[str, Noul]]]] = {}
    scope = output.get("thread_scope")
    scoped = (base | {"thread": {"asked": scope["asked"]}}, {"scope": SCOPE, "wider": WIDER}) if scope else None
    for number, claim in enumerate(post.get("claims") or [], 1):
        if not unmatched(reads, claim):
            for item in [backed(reads, number, claim), *(staged(stage, number, claim) for stage in reads.values() if number in stage.get("claims", ()))]:
                shared.setdefault(next(iter(item[0]["reads"])), []).append(item)
    weighs = [
        *(
            (f'{read} {json.dumps(reads[read]["read"], ensure_ascii=False)} beside {"the claim" if len(items) == 1 else f"the {len(items)} claims"} naming it', part)
            for read, items in shared.items()
            for part in parted(together(items))
        ),
        *(
            (f'sentence "{sentence}"', ({"post": {"sentences": {str(index): sentence}} | stamps, "claims": {f"c{number}": {"claim": claim["claim"]} for number, claim in enumerate(post.get("claims") or [], 1)}}, {f"s{index}.fact": asking(FACT, index=index)}))
            for index, sentence in uncovered(output)
        ),
        *([("his words in the thread it goes in", scoped)] if scoped and size(*scoped) <= REQUEST_CHARS else []),
    ]
    said = {word["id"]: word["quote"] for word in [*(word for word in words if not word.get("said_at")), *sorted((word for word in words if word.get("said_at")), key=lambda word: word["said_at"], reverse=True)]}
    rest = [
        ({"findings": {f"f{number}": finding}} | ({"his_words": said} if said else {}), {f"f{number}.holds": asking(HOLDS, finding=f"f{number}"), **({f"f{number}.lifted": asking(LIFTED, finding=f"f{number}")} if said else {})})
        for number, finding in enumerate(check_findings(output), 1)
    ]
    return base, weighs, rest


def requests(output: dict) -> list[tuple[dict, dict[str, Noul]]]:
    """Jev's requests on a post, each within REQUEST_CHARS by his words "jev should be getting like 10k chars at most for any call": every item Jev cannot weigh the post without, whole, in the first request with room for it, so reads too long for one request together go to Jev in several, and one too long for a request alone in the parts `parted` cuts it into, never dropped or read as unbacked, then the findings beside his words in one request as room allows; only a request asking Jev something."""
    base, weighs, rest = asked(output)
    packed: list[tuple[dict, dict[str, Noul]]] = []
    for _, item in weighs:
        for at, request in enumerate(packed):
            if size(*(merged := together([request, item]))) <= REQUEST_CHARS:
                packed[at] = merged
                break
        else:
            packed.append(item)
    return [*packed, *(request for part, asked in rest if size(*(request := (joined(base, part), asked))) <= REQUEST_CHARS)]


def refusal_reason(output: dict) -> str:
    """The refusal a drive check made of a post or call, as its hook output names it, a Stop hook's block reason included, or none."""
    return (output.get("hookSpecificOutput") or {}).get("permissionDecisionReason") or (output.get("decision") == "block" and output.get("reason")) or ""


def check_findings(output: dict) -> list[dict]:
    """Each finding a drive check hands beside a post or a refused call, or its refusal as one finding when it names none."""
    reason = refusal_reason(output)
    return output.get("findings") or ([{"rule": reason, "found": reason.removeprefix("refused: ")}] if reason else [])


def warned(findings: list[dict]) -> str:
    """Each finding a warning names on a post or call that goes ahead, a numbered line each by what its check found, so the session reads what to reword in place and never a refusal's words."""
    return "".join(f"\n{number}. {finding['found']}" for number, finding in enumerate(findings, 1))


def unmatched(reads: dict[str, dict], claim: dict) -> bool:
    """Whether the read a claim names matched none of the handler's calls."""
    return reads.get(claim["read"], {}).get("quote", NO_READ).startswith(NO_READ)


def backed(reads: dict[str, dict], number: int, claim: dict) -> tuple[dict, dict[str, Noul]]:
    """One claim's item: the claim beside the call its read names and that call's whole output, under the read the call was first given under when an earlier read named the same call, so one call goes to Jev once however many claims stand on it, one Noul whether that read backs the claim, and for a claim saying only, every, no or never, one whether it speaks for a whole set while that read reads one member of it."""
    item = reads[claim["read"]]
    held = reads.get(item.get("given") or "", item)
    call, _, result = held["quote"].partition("\n")
    key = f"c{number}"
    spans = {f"{key}.spans": asking(SPANS, read=held["id"], claim=key)} if SET_WORDS.search(claim["claim"]) else {}
    return {"claims": {key: {"claim": claim["claim"]}}, "reads": {held["id"]: {"call": call, "result": result}}}, {f"{key}.backed": asking(BACKED, read=held["id"], claim=key), **spans}


def staged(stage: dict, number: int, claim: dict) -> tuple[dict, dict[str, Noul]]:
    """One claim naming a drive PR beside the drive's own line for it, as `drive status <PR>` prints it, and one Noul whether that line backs the claim, so a claim on where that PR stands or who owes its fix stands on the drive, which owns both, whatever read the handler names, and the claim keeps the read it names."""
    call, _, result = stage["quote"].partition("\n")
    key = f"c{number}"
    return {"claims": {key: {"claim": claim["claim"]}}, "reads": {stage["id"]: {"call": call, "result": result}}}, {f"{key}.backed.{stage['id']}": asking(BACKED, read=stage["id"], claim=key)}


def parted(item: tuple[dict, dict[str, Noul]]) -> list[tuple[dict, dict[str, Noul]]]:
    """A read's item, its claims beside it, as Jev's requests take it: whole when it fits one request, else in parts by whole lines in order, each as many lines as a request holds beside the claims, the read's call and how many lines its output holds, each line under its number, blank lines aside, and each claim asked BACKED_LINES of each part, so a read too long for one request, such as a report's whole body, reaches Jev whole across several, by his words "jev should be getting like 10k chars at most for any call" and his pick f9e9302 "Jev checks each claim against that read, whole", and a claim stands on the part backing it most; a question on the call alone, such as SPANS, goes with the first part."""
    if size(*item) <= REQUEST_CHARS:
        return [item]
    state, questions = item
    [(read, held)] = state["reads"].items()
    lines = held["result"].splitlines()

    def part(index: int, numbered: dict[str, str]) -> tuple[dict, dict[str, Noul]]:
        return (
            {"claims": state["claims"], "reads": {read: {"call": held["call"], "lines": len(lines), "result": numbered}}},
            {f"{key}.{index}": asking(BACKED_LINES, read=read, claim=key.partition(".")[0]) for key in questions if key.endswith(".backed")}
            | ({key: noul for key, noul in questions.items() if not key.endswith(".backed")} if index == 1 else {}),
        )

    parts: list[dict[str, str]] = [{}]
    room = REQUEST_CHARS - size(*part(1, {}))
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        entry = len(json.dumps({str(number): line}, ensure_ascii=False))
        if parts[-1] and entry > room:
            parts.append({})
            room = REQUEST_CHARS - size(*part(len(parts), {}))
        parts[-1][str(number)] = line
        room -= entry
    return [part(index, numbered) for index, numbered in enumerate(parts, 1)]


def unfit(output: dict) -> tuple[int, list[tuple[str, int]]]:
    """The characters the post's own words take in the requests naming them, and each item `asked` names that no request holds whole within REQUEST_CHARS, by his words "jev should be getting like 10k chars at most for any call", beside the words naming it and the characters the request holding it alone takes, largest first, such as a part of a read holding one line longer than a request, so the refusal names what to narrow; none when each fits, so no claim, sentence or card is ever read as unbacked for want of room."""
    base, weighs, _ = asked(output)
    alone = ((named, size(*item)) for named, item in weighs)
    return size(base, {}), sorted(((named, chars) for named, chars in alone if chars > REQUEST_CHARS), key=lambda pair: -pair[1])


def together(items: list[tuple[dict, dict[str, Noul]]]) -> tuple[dict, dict[str, Noul]]:
    """Several `fill` items as one, their states merged at every depth and their questions beside each other, so they go into a request or stay out of it together."""
    state: dict = {}
    questions: dict[str, Noul] = {}
    for part, nouls in items:
        state, questions = joined(state, part), questions | nouls
    return state, questions


def unheard_post(output: dict, failure: str) -> dict:
    """The verdict on a post Jev weighed none of, as when Jev gave no answer or a read had no room in its requests: held only for a claim whose read matched none of the handler's calls, which needs no Jev; any other goes out, naming what the checks found as warnings, since a gate that cannot finish fails open."""
    if held := unmatched_claims(output):
        return held_back(held, refusal_reason(output))
    if findings := check_findings(output):
        return {"verdict": "warn", "note": f"{GOES_OUT} Jev gave no verdict ({failure}), so what the checks found only warns:{warned(findings)}"}
    return {"verdict": "warn", "note": f"{GOES_OUT} Jev gave no verdict ({failure}) on whether each claim of it stands on the read it names."}


def uncovered(output: dict) -> list[tuple[int, str]]:
    """Each sentence of a handler's post no claim holds whole, as `covers` reads it, by its index among the post's sentences as the handler wrote them, each reference as written, so a title or a time say draws is never a word a claim must hold; none for any other post, which hands Jev no reads."""
    if not output.get("reads"):
        return []
    post = output["post"]
    claims = [claim["claim"] for claim in post.get("claims") or []]
    sentences = [sentence for sentence in limits.SENTENCE_BREAK.split(post.get("written") or post.get("text") or "") if limits.WORD.search(limits.REF.sub(" ", sentence))]
    return [(index, sentence) for index, sentence in enumerate(sentences) if not covers(claims, sentence)]


def covers(claims: list[str], sentence: str) -> bool:
    """Whether a post's claims speak for one of its sentences whole: together they hold every word of it, case, punctuation, links and drive references aside, so a claim on part of a sentence never speaks for the rest of it, a sentence whose parts stand on several reads is the claims of its parts, and a sentence of links alone needs none."""
    return worded(sentence) <= set().union(*map(worded, claims))


def worded(text: str) -> set[str]:
    """A text's words as `covers` compares them: links, drive references and Slack tokens blanked, case and punctuation aside."""
    return set(UNWORDED.sub(" ", limits.unmarked(text).casefold()).split())


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
    """His words on the order, each without the lines he quoted with ">", which are another's: the words of his the drive quotes in an order not added from his own Slack message, as limits.his_words reads them, then each of his messages about it in its thread, the one that started it included, as state.message_order reads each, never one about another order sharing that thread, each by its message's ts and each with when he gave it, since an order's stored text can lose the line break between his words and the lines he quoted; a thread that cannot be read fails as a Slack error, never as a refusal of the question."""
    order = data["orders"][order_id]
    own = [] if limits.from_his_message(order) or not (said := limits.his_words(order)) else [{"id": order_id, "asked": "his words the order quotes", "said": said[:BODY_CHARS], "at": order["created"]}]
    if not (where := order["thread"]):
        return own
    try:
        messages = slack.thread("--channel", where[0], "--thread", where[1], "--limit", str(slack.WHOLE_THREAD))
    except slack.SlackError as error:
        raise slack.SlackError(f"could not read the order's thread {where[1]} for his words this question is judged against, so nothing is kept; run it again: {error}") from None
    return own + [
        {"id": f"p{message['ts'].replace('.', '')}", "asked": "his message in the order's thread", "said": said[:BODY_CHARS], "at": state.stamp(slack.sent_at(message))}
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
