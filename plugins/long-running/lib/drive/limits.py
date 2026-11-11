import json
import re
from dataclasses import dataclass

from drive import picture
from drive.render import cut, when
from drive.state import PR_KEY, PR_URL, DriveError

LINE_WORDS = 60
SUMMARY_WORDS = 120
QUESTION_WORDS = 150
OPTION_WORDS = 25
REWORDING_WORDS = 10
SUMMARY_LINES = 3
OPTION_COUNT = range(2, 5)
READING = 'the coordinator\'s reading of his Nov 9 9:33 PM words "your messages need to be way shorter rewrite this for a human"'
LINE_REST = "Say what changed for him and what happens next, in plain words; everything else stays on disk until he asks for it."
LINKS_RULING = 'his Nov 10 4:36 PM words "never mention somehting and expect me to go look it up myself"'
PLACEHOLDER_RULING = 'his Nov 10 7:35 PM words "this table just says `time,bytes,disk` for everything"'
LIBRARY_RULING = 'his Nov 10 9:58 PM words "buold out a libary of difernetg visualizaions … and then use the approporiuate one each time"'
WORDS_RULING = 'his Nov 10 10:30 PM words "these images are not useful at all, at ALL … if theres nothing to show its bette rnot to show anyhting"'
RAW_RULING = 'his Nov 10 10:52 PM words "giving me random json and terminal shit in those first 2 images is absiolkteiyl useless"'
VISUALS = (
    "a ```mermaid flow for routing or ownership, a ```compare or a markdown table for options, a ```chart for numbers,"
    " an ```arch diagram for components, the real call in a fence naming its language (```sand, ```sql, ```go) when the"
    " question is about a code or API shape, or a picture on a line of its own"
)
JSON_KINDS = ("arch", "chart", "compare")
COMPARE_SHAPE = '{"options": [{"name": …, "recommended": true, "language": …, "code": …, "points": ["+ a gain", "- a cost"]}, …]}'
SEPARATOR_CELL = re.compile(r":?-+:?")

WORD = re.compile(r"\S*[^\W_]\S*")
LABELLED = re.compile(r"<[^<>|]*\|([^<>]*)>")
TOKEN = re.compile(r"<[^<>]*>")
URL = re.compile(r"https?://\S+")
FEATURE = re.compile(r"^\*[^*\n]+\*[ \t]*$", re.MULTILINE)
FINDINGS = re.compile(r"^[ \t]*Findings:[ \t]*$", re.MULTILINE)
SHOW_ME = re.compile(r"^[ \t]*Show me:[ \t]*$", re.MULTILINE)
SHOWN = re.compile(r"^\*Show me\*[ \t]*$", re.MULTILINE)
FENCE = re.compile(r"^[ \t]*```")
PICTURE = re.compile(r"!\[([^\]\n]*)\]\(\s*(?:<[^<>\n]+>|[^\s()<>]+)\s*\)")
RECOMMENDED = re.compile(r"\s*\(recommended\)\s*$", re.IGNORECASE)
QUOTED = re.compile(r'"[^"\n]*"|“[^”\n]*”|`[^`\n]*`')
KINDS = r"(?:question|PR|tap|pick|design|status) cards?|pull requests?|PRs?|fix(?:es)?|releases?|builds?|alerts?|threads?|asks?"
DETERMINERS = r"the|this|that|these|those|its|his|her|their|our|your|my"
FILLER = (
    rf"(?!(?:{DETERMINERS}|will|would|can|could|should|must|may|might|to|which|who|and|or|but|not|never|next|is|are|was"
    r"|were|be|been|has|have|had|does|did|do|it|in|on|of|for|with|at|by|from|as|so|then|also)\b)[\w-]+(?:['’]s)?"
)
POSSESSIVE = r"[\w-]+['’]s"
COUNT = r"(?:\d+|one|two|three|four|five|six|both|several)"
COMPOUND = (
    r"(?![ \t]+(?:checks?|steps?|buttons?|notes?|tooling|scripts?|rules?|lanes?|queues?|times?|ids?|numbers?|names?"
    r"|branch(?:es)?|caches?|process(?:es)?|paths?|pipelines?|box(?:es)?|machines?|tools?|systems?|commands?|flows?"
    r"|trains?|gates?|press|hooks?|jobs?|targets?|modes?|flags?|settings?|channels?)\b)"
)
NAMED = (
    re.compile(
        rf"\b(?:{DETERMINERS}|{POSSESSIVE})\s+(?:{COUNT}\s+)?(?:{FILLER}\s+){{0,2}}?"
        rf"(?:(?:question|PR|tap|pick|design|status) cards?|pull requests?|PRs?|fix|release|build|alert|thread|ask)\b{COMPOUND}",
        re.IGNORECASE,
    ),
    re.compile(
        rf"\b(?:the|these|those|its|his|her|their|our|your|my|{POSSESSIVE})\s+(?:{COUNT}\s+)?(?:fixes|releases|builds|alerts|threads|asks)\b{COMPOUND}",
        re.IGNORECASE,
    ),
)
KIND = re.compile(rf"\b(?:{KINDS})\b", re.IGNORECASE)
LINK = re.compile(r"<[^<>|]+\|([^<>]*)>")
NUMBER_LABEL = re.compile(r"\s*(?:(?:PR|pull request)\s*)?#?\d+\s*", re.IGNORECASE)
MARKUP = re.compile(
    r"\[[^\]\n]*\]\([^)\s]*\)|<(?:https?://|mailto:|[@#!])[^<>\n]*>|https?://\S+|`[^`\n]+`"
    r"|(?<![\w*_~])[*_~](?=\S)[^*_~\n]*?(?<=\S)[*_~](?![\w*_~])"
)
PLAIN_TEXT_RULING = 'his Nov 10 11:21 PM words "look at the formatting you have a link in teh titkle that is just raw markfowen"'
NOT_PLAIN = (
    ("a file path", re.compile(r"(?<!\S)(?:~|\.{1,2})?/[\w.-]+/[\w./-]*")),
    ("a file name", re.compile(r"\b[\w-]+\.(?:py|go|tsx|rs|md|json|jsonl|ya?ml|sql|toml|sh|txt)\b")),
    ("a code name", re.compile(r"\b[A-Za-z0-9]+(?:_[A-Za-z0-9]+)+\b|\b\w+\(\)")),
    ("a job or order id", re.compile(r"\b[jo]\d+\b")),
)
PR_NUMBER = re.compile(r"#\d+\b")
OPTION_POINTER = re.compile(
    r"\b(?:(?:same\s+as|as\s+in|like|unlike|than|plus)\s+[#(]?[1-9]\b|options?\s+[#(]?[1-9]\b"
    r"|the\s+(?:first|second|third|fourth|last|other|previous)\s+(?:option|one|pick)\b)",
    re.IGNORECASE,
)
RELEASABLE_RULING = (
    'his Nov 10 6:44 PM words "why would you merge something that shouydlknt go out dev mustr always be releasable"'
)
STEP = r"(?:run|re-?run|fill|back-?fill|migrate|apply|execute|trigger|kick\s+off|flip|enable|patch|repair)"
SHIPPED = (
    r"(?:the\s+|it\s+|this\s+)?(?:deploy|deployed|release|released|rollout|merge|merged|lands|merges|ships"
    r"|is\s+(?:live|released|deployed|merged|out))\b"
)
LATER_STEP = re.compile(
    rf"\b(?:then|afterwards|later)\s+(?:we\s+|you\s+|it\s+)?(?:{STEP}|fix)\b"
    rf"|\b(?:after|once|when)\s+{SHIPPED},?\s+(?:(?:we|you|i|someone|then)\s+(?:must\s+|should\s+|need\s+to\s+|will\s+)?)?{STEP}\b"
    rf"|\b{STEP}\b(?:\s+[\w'-]+){{0,4}}?\s+(?:(?:after|once|when)\s+{SHIPPED}|(?:later|afterwards)\b)"
    r"|\bfix(?:ed)?\s+(?:it\s+|them\s+)?(?:up|forward|later|afterwards)\b|\bfollow[- ]?up\s+(?:pr|fix|change|step)\b",
    re.IGNORECASE,
)
LANDS = re.compile(r"\b(?:land|merge|ship|release|deploy)(?:s|ed|ing)?\b", re.IGNORECASE)
LANDING = re.compile(r"(?:land|merge)\b", re.IGNORECASE)
STACK_RULING = 'his Nov 10 10:34 PM words "we should be using the "Stack card" which shows seral PRs instasd of this"'
NEGATION = re.compile(r"\b(?:no|not|never|without|nothing|none|neither|nor)\b|n't\b", re.IGNORECASE)
CLAUSE_END = re.compile(r"[.;:,!?\n]")


@dataclass(frozen=True)
class Question:
    title: str
    summary: list[str]
    show_me: list[str]
    options: list[str]
    recommended: str

    def card(self, drive: str, waits: str) -> dict:
        """The question card a handler draws from this block."""
        body = card_body(self.summary, self.show_me)
        return {"title": self.title, "body": body, "options": self.options, "recommended": self.recommended, "waits": waits, "drive": drive}


def words(text: str) -> int:
    return len(WORD.findall(TOKEN.sub(" link ", LABELLED.sub(r" \1 ", text))))


def block_text(node: object) -> str:
    if isinstance(node, dict):
        return " ".join(value if key == "text" and isinstance(value, str) else block_text(value) for key, value in node.items())
    if isinstance(node, list):
        return " ".join(map(block_text, node))
    return ""


def post_words(post: dict) -> int:
    return max(words(post.get("text") or ""), words(block_text(post.get("blocks") or [])))


def post_refusal(post: dict, edited: int = 0) -> str | None:
    """Why a post to his DM is over the length a routine line, summary or card gets, or None when it fits; an edit may reword the post it edits, edited words long, at its own length and a few words more."""
    if card := post.get("card"):
        return card_refusal(card, post.get("text") or "")
    count = post_words(post)
    features = len(FEATURE.findall(post.get("text") or ""))
    limit, shape = (SUMMARY_WORDS, f"a summary of {features} features") if features >= 2 else (LINE_WORDS, "a line in his thread")
    if count <= max(limit, edited + REWORDING_WORDS if edited else 0):
        return None
    edit = f", or {REWORDING_WORDS} past the {edited} words of the post it edits" if edited else ""
    return f"refused: this post is {count} words, and {shape} is at most {limit} words{edit} ({READING}). {LINE_REST}"


def card_body(summary: list[str], show_me: list[str]) -> str:
    return "\n".join([*summary, "*Show me*", *show_me] if any(line.strip() for line in show_me) else summary)


def card_words(card: dict) -> int:
    """The words he reads on a question card, counted by `drive job ask` over the card it keeps and by the post hook over the card posted: its title, the body above its `*Show me*` line, and its options."""
    summary = SHOWN.split(card.get("body") or "", maxsplit=1)[0]
    return sum(map(words, [card.get("title") or "", *summary.splitlines(), *(card.get("options") or [])]))


def card_refusal(card: dict, beside: str) -> str | None:
    """Why a question card is over his length; its Show me, everything under the body's `*Show me*` line, is drawn whole and never counted, and a post's text beside the card is drawn atop its body unless the body holds it already, as cc-slack draws it, so a drive's question card, whose words `drive job ask` kept and counted, carries none."""
    options = card.get("options") or []
    long = [f'"{option}" is {words(option)} words' for option in options if words(option) > OPTION_WORDS]
    body = card.get("body") or ""
    drawn = beside.strip() not in body
    if drawn and card.get("drive") and card.get("waits"):
        return (
            f"refused: this post's text is {words(beside)} words cc-slack draws atop the card, and a drive's question card carries"
            " only the words of the ASK block `drive job ask` kept and counted: post the same card with no text."
        )
    count = card_words(card | {"body": f"{beside}\n{body}"} if drawn else card)
    problems = [f"each option is one line of at most {OPTION_WORDS} words, what he gets and what it costs: {', '.join(long)}"] if long else []
    if count > QUESTION_WORDS:
        problems.append(
            f"this card is {count} words, and a question with its summary and options is at most {QUESTION_WORDS} words in all;"
            " its Show me goes under a `*Show me*` line in the body, drawn whole and not counted"
        )
    if not problems:
        return None
    return f"refused: {'; '.join(problems)} ({READING})."


def question(text: str, kept: bool = False) -> Question:
    """The one question a job's ASK block puts to him, refused with every way it is out of shape; a block `drive job ask` already kept is read with no second check of its Show me's visuals, so a card it kept under older rules still redraws."""
    asked, *findings = FINDINGS.split(text, maxsplit=1)
    head, *example = SHOW_ME.split(asked, maxsplit=1)
    lines = [line.strip() for line in head.splitlines() if line.strip()]
    if not lines or not lines[0].startswith("ASK "):
        raise DriveError("an ASK block opens with `ASK <the question, one line>`")
    title, rest = lines[0].removeprefix("ASK ").strip(), lines[1:]
    first_option = next((index for index, line in enumerate(rest) if line.startswith("- ")), len(rest))
    summary, above = rest[:first_option], rest[first_option:]
    show_me, after = shown_and_options(example[0] if example else "")
    after = above + after
    options = [line.removeprefix("- ").strip() for line in after if line.startswith("- ")]
    marked = [option for option in options if RECOMMENDED.search(option)]
    options = [RECOMMENDED.sub("", option) for option in options]
    problems = []
    if not 1 <= len(summary) <= SUMMARY_LINES:
        problems.append(
            f"the summary is {len(summary)} lines; it is one to three: what was found and what is being decided,"
            " the recommendation, what happens meanwhile"
        )
    problems += show_me_problems(show_me, bool(example and above), kept)
    if strays := [line for line in after if not line.startswith("- ")]:
        problems.append(f"only `- ` option lines follow the Show me, then `Findings:`; these are neither: {strays}")
    if len(options) not in OPTION_COUNT:
        problems.append(f"{len(options)} options; an ASK has {OPTION_COUNT.start} to {OPTION_COUNT.stop - 1}, one line each")
    if len(marked) != 1:
        problems.append(f"{len(marked)} options end with (recommended); exactly one does")
    problems += [
        f'option "{option}" is {words(option)} words; each is one line of at most {OPTION_WORDS}: what he gets and what it costs'
        for option in options
        if words(option) > OPTION_WORDS
    ]
    shown = [title, *summary, *options]
    if (count := card_words({"title": title, "body": card_body(summary, show_me), "options": options})) > QUESTION_WORDS:
        problems.append(f"the question, summary and options are {count} words; at most {QUESTION_WORDS} in all, the Show me not counted")
    plain = URL.sub(" ", TOKEN.sub(" ", "\n".join(shown)))
    problems += [f'plain words only: "{found}" is {what}' for what, pattern in NOT_PLAIN for found in pattern.findall(plain)]
    problems += [
        f'"{found}" points at an option by number or position; cards renumber, so each option says its own words'
        for found in OPTION_POINTER.findall(URL.sub(" ", TOKEN.sub(" ", "\n".join([*shown, *captions(show_me)]))))
    ]
    problems += unlinked([*summary, *captions(show_me)], [title, *options])
    problems += [
        f'{where} holds "{found}", which Slack draws as raw text, since a card\'s title and buttons take no markdown or links;'
        f" say it in plain words there and put the link in the summary ({PLAIN_TEXT_RULING})"
        for where, line in [("the question", title), *((f'option "{option}"', option) for option in options)]
        for found in MARKUP.findall(line)
    ]
    problems += [
        f'option "{option}" lands first and leaves "{step}" for after: dev must always be releasable ({RELEASABLE_RULING}),'
        " so no option lands a change a release cut at its merge commit cannot carry alone; offer a split whose every part"
        " is safe to release by itself, or drop the option"
        for option in options
        if (step := lands_first(option))
    ]
    if not kept and marked and LANDING.match(title) and LANDING.match(marked[0]) and PR_URL.search("\n".join(summary)):
        problems.append(
            "it asks him to land PRs, which is his tap on their stack card and never a question card: return READY naming"
            f" every PR it lands, bottom first, and the stack card that carries them needs no Show me ({STACK_RULING})"
        )
    if not findings or not findings[0].strip():
        problems.append("no `Findings:` line with the evidence under it; it stays on disk, for the record and Explain more")
    if problems:
        raise DriveError("the ASK is out of shape, " + READING + ":\n- " + "\n- ".join(problems))
    return Question(title, summary, show_me, options, RECOMMENDED.sub("", marked[0]))


def shown_and_options(example: str) -> tuple[list[str], list[str]]:
    """The Show me's lines as written, fences and blank lines kept, and the lines from the first `- ` line outside a fence on."""
    lines, fenced = example.strip("\n").splitlines(), False
    for index, line in enumerate(lines):
        if not fenced and line.strip().startswith("- "):
            return lines[:index], [line.strip() for line in lines[index:] if line.strip()]
        if FENCE.match(line):
            fenced = not fenced
    return lines, []


def captions(show_me: list[str]) -> list[str]:
    """The Show me's lines outside its code fences."""
    kept, fenced = [], False
    for line in show_me:
        if FENCE.match(line):
            fenced = not fenced
        elif not fenced and line.strip():
            kept.append(line.strip())
    return kept


def show_me_problems(show_me: list[str], below_options: bool, kept: bool) -> list[str]:
    if below_options:
        return ["the options come after the Show me, never above it: summary, then `Show me:` and its example, then the options"]
    if not any(line.strip() for line in show_me):
        return []
    problems = [
        f'Show me line "{line}" is {words(line)} words; outside a code fence each is a caption of at most {OPTION_WORDS}'
        for line in captions(show_me)
        if not line.startswith("|") and words(line) > OPTION_WORDS
    ]
    problems += [
        f'Show me line "{line}" holds a picture inside a sentence or table, which cc-slack draws as text; put each picture'
        " on a line of its own, `![what it shows](/absolute/path.png)`, and cc-slack draws it on the card there"
        for line in captions(show_me)
        if PICTURE.search(line) and not PICTURE.fullmatch(line)
    ]
    problems += [
        f'the Show me table headed "{" | ".join(table[0])}" holds "{value}" in every cell: a Show me shows real contents in'
        " every row and cell, measured or worked out from the code and marked as such, never a table of what will be measured"
        f" later ({PLACEHOLDER_RULING})"
        for table in tables(show_me)
        if (value := one_value(table)) is not None
    ]
    fences = [line for line in show_me if FENCE.match(line)]
    if len(fences) % 2:
        problems.append("a ``` fence in the Show me is never closed")
    return problems if kept else problems + visual_problems(show_me)


def visual_problems(show_me: list[str]) -> list[str]:
    """Why a Show me would draw nothing worth a picture: a fence of words, a fence of raw data or tool output, a chart, compare or arch whose JSON does not draw, or no visual at all."""
    fences = list(picture.FENCE.finditer("\n".join(show_me)))
    named = [fence for fence in fences if fence[1].lower() not in picture.WORDS]
    raw = [(fence, what) for fence in named if (what := picture.raw(fence[1], fence[2]))]
    drawn = [fence for fence in named if not picture.raw(fence[1], fence[2])]
    problems = [
        f'the Show me fence holding "{cut(" ".join(fence[2].split()), 60)}" names no language, so his card would get a picture of'
        f" words: name the code's language, or put the words in a caption or a table ({WORDS_RULING})"
        for fence in fences
        if fence[1].lower() in picture.WORDS
    ]
    problems += [
        f'the Show me ```{fence[1]} fence holding "{cut(" ".join(fence[2].split()), 60)}" is {what}, raw data or tool output,'
        f" and a picture of it shows him nothing: {picture.INSTEAD} ({RAW_RULING})"
        for fence, what in raw
    ]
    problems += [f"the Show me ```{fence[1]} fence {problem}" for fence in drawn if (problem := json_problem(fence[1].lower(), fence[2]))]
    if not drawn and not raw and not tables(show_me) and not any(PICTURE.fullmatch(line) for line in captions(show_me)):
        problems.append(
            f"the Show me holds no visual, only words: add the kind that shows what the words cannot, {VISUALS}, or leave out"
            f" the `Show me:` line when nothing does ({LIBRARY_RULING}; {WORDS_RULING})"
        )
    return problems


def json_problem(kind: str, source: str) -> str | None:
    if kind not in JSON_KINDS:
        return None
    try:
        drawn = json.loads(source)
    except json.JSONDecodeError as error:
        return f"is not the JSON it draws from: {error}"
    if not isinstance(drawn, dict):
        return "holds JSON that is not an object"
    if kind == "compare" and sum(1 for option in drawn.get("options") or [] if isinstance(option, dict) and option.get("name")) < 2:
        return f"compares fewer than two named options; it holds {COMPARE_SHAPE}"
    return None


def tables(show_me: list[str]) -> list[list[list[str]]]:
    """Each markdown table of a Show me outside its code fences, as rows of cells, its header row first."""
    found, rows, fenced = [], [], False
    for line in [*show_me, ""]:
        fenced ^= bool(FENCE.match(line))
        if not fenced and line.strip().startswith("|"):
            rows.append([cell.strip() for cell in line.strip().strip("|").split("|")])
        elif rows:
            found.append(rows)
            rows = []
    return found


def one_value(table: list[list[str]]) -> str | None:
    """The one text every data cell of a table holds, its first column of row names aside, when it has at least two rows and two columns of them."""
    data = [row[1:] for row in table[1:] if not all(SEPARATOR_CELL.fullmatch(cell) for cell in row)]
    cells = {cell for row in data for cell in row}
    if len(data) < 2 or min(map(len, data)) < 2 or len(cells) != 1:
        return None
    return cells.pop()


def kind(found: str) -> str:
    word = found.lower().removesuffix("es" if found.lower().endswith("fixes") else "s")
    return "PR" if word in ("pull request", "pr", "fix") else word


def unlinked(linked: list[str], plain: list[str]) -> list[str]:
    """Each thing he could open that these lines name with no link at that spot: `linked` lines draw `<url|label>` links, and what `plain` lines name, a card's title and options, which draw as plain text, is linked in a `linked` line by words naming the same kind of thing."""
    labels = [label for line in linked for label in LINK.findall(line)]
    linked_kinds = {kind(found) for label in labels for found in KIND.findall(label)}
    problems = [
        f'the link <…|{label}> says only a number; its text says what it is, such as what the change does' for label in labels if NUMBER_LABEL.fullmatch(label)
    ]
    for lines, is_plain in ((linked, False), (plain, True)):
        bare = [QUOTED.sub(" ", URL.sub(" ", TOKEN.sub(" ", line))) for line in lines]
        problems += [
            f'"{number}" is a number he cannot open; link it by what it is, such as <https://github.com/…/pull/53618|the fix that adds the update button>'
            for line in bare
            for number in PR_NUMBER.findall(line)
        ]
        for line in bare:
            for found in dict.fromkeys(match.group() for pattern in NAMED for match in pattern.finditer(line)):
                named = KIND.findall(found)[-1]
                if not is_plain:
                    problems.append(f'"{found}" names something he could open with no link there; put those words in its link, <url|{found}>, so he opens it where it is named ({LINKS_RULING})')
                elif kind(named) not in linked_kinds:
                    problems.append(
                        f'"{found}" in the question or an option names something he could open, and those draw no links; link it in the summary'
                        f" or a Show me caption, its link text naming the {named} ({LINKS_RULING})"
                    )
    return problems


def later_steps(text: str) -> list[tuple[str, str]]:
    """Each step the text schedules for after a release, such as "then run" or "backfill once it ships", beside the line naming it; a feature merely named, such as a backfills page or a pass box, is none, and so is a step the text rules out, as "nothing has to run once it ships" does."""
    found = []
    for match in LATER_STEP.finditer(text):
        if NEGATION.search(CLAUSE_END.split(text[: match.start()])[-1]):
            continue
        end = text.find("\n", match.end())
        found.append((match[0], text[text.rfind("\n", 0, match.start()) + 1 : end if end >= 0 else None].strip()))
    return found


def lands_first(option: str) -> str | None:
    """The step an option leaves until after it lands, as in "land it now, then backfill", or None when it lands nothing that needs one."""
    if not (landed := LANDS.search(option)):
        return None
    return next((step for step, _ in later_steps(option[landed.end() :])), None)


def tap_owed_not(pr: dict) -> str:
    """Why no tap of his is owed on a drive PR, or "" when one is: he tapped it, it merged or closed, or it lands with no tap."""
    tap = pr.get("tap")
    reasons = [
        f"he tapped {tap['label']} at {when(tap['at'])}" if tap else "",
        pr["state"] if pr["state"] in ("merged", "closed") else "",
        f"it lands with no tap as {pr['exempt']}" if pr.get("exempt") else "",
    ]
    return ", ".join(reason for reason in reasons if reason)


def tap_refusal(text: str, prs: dict[str, dict]) -> str | None:
    """Why a PR card or a ready record, which asks him to tap each PR it names by what it is, names one the drive records as owing no tap."""
    folded = {key.casefold(): (key, pr) for key, pr in prs.items()}
    named = dict.fromkeys(f"{owner}/{name}#{number}".casefold() for owner, name, number in [*PR_URL.findall(text), *PR_KEY.findall(text)])
    found = [f"{key}: {why}" for each in named if each in folded for key, pr in [folded[each]] if (why := tap_owed_not(pr))]
    if not found:
        return None
    return (
        f"refused: this asks him to tap, approve or hold a PR the drive owes no tap on: {'; '.join(found)}. A tap is asked"
        " for only when owed: read drive status <pr> before a card or line names a tap, and say what happened to it instead."
    )


def link_refusal(post: dict) -> str | None:
    """Why a handler's post to his DM names something he could open without its link: a line's `text`, or a card's summary and Show me captions, carry each link where the thing is named, and its title and options, drawn as plain text, name only what the body links."""
    if card := post.get("card"):
        summary, *example = SHOWN.split(card.get("body") or "", maxsplit=1)
        problems = unlinked([summary, *captions(example[0].splitlines() if example else []), post.get("text") or ""], [card.get("title") or "", *(card.get("options") or [])])
    else:
        problems = unlinked([post.get("text") or ""], [])
    if not problems:
        return None
    return "refused: " + "; ".join(problems) + "."
