import json
import re
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

from drive import picture
from drive.render import cut, when
from drive.state import PR_KEY, PR_URL, DriveError, root

LINE_WORDS = 60
SUMMARY_WORDS = 120
QUESTION_WORDS = 150
OPTION_WORDS = 25
REWORDING_WORDS = 10
SUMMARY_LINES = 3
OPTION_COUNT = range(2, 5)
READING = 'the coordinator\'s reading of his Nov 9 9:33 PM words "your messages need to be way shorter rewrite this for a human"'
LINE_REST = "Say what changed for him and what happens next, in plain words; everything else stays on disk until he asks for it."
FROM_ASK = (
    " A question card is posted exactly as `drive job card <its ask file> --waits {waits}` prints it, never put together by hand:"
    " fix its words in the ASK block and keep it again with `drive job ask`."
)
PERMISSION_WAITS = "slack:"
LINKS_RULING = 'his Nov 10 4:36 PM words "never mention somehting and expect me to go look it up myself"'
PLACEHOLDER_RULING = 'his Nov 10 7:35 PM words "this table just says `time,bytes,disk` for everything"'
LIBRARY_RULING = 'his Nov 10 9:58 PM words "buold out a libary of difernetg visualizaions … and then use the approporiuate one each time"'
WORDS_RULING = 'his Nov 10 10:30 PM words "these images are not useful at all, at ALL … if theres nothing to show its bette rnot to show anyhting"'
RAW_RULING = 'his Nov 10 10:52 PM words "giving me random json and terminal shit in those first 2 images is absiolkteiyl useless"'
MOCK_RULING = 'his Nov 10 11:54 PM words "not a useful show me at all"'
BIG_RULING = 'his Nov 10 10:09 PM words "look at these images they are insane, they are so big"'
GALLERY_RULING = 'his Nov 10 10:55 PM words "should be shown gallery style labeled with each option (e.g 1, 2)"'
HAND_RULING = 'his Nov 10 10:30 PM words "these images are not useful at all … here we could have done a mermaid chart"'
NATIVE_RULING = 'his Nov 10 11:01 PM words "the two tables should be rendered in slack natively"'
CONSTANT_RULING = 'his Nov 10 11:03 PM words "we needed an image to show a const? use your brain show a snippet when it makes sense"'
CHART_RULING = 'his Nov 10 10:53 PM words "for the chart slack ltieraly has the native … vertsion of this why didnt youi build that for the renderer"'
PHONE_RULING = 'his Nov 11 12:21 AM words "mermaid is but is ugly"'
HAND_DRAWN = ("asks", "pictures")
CONSTANT = re.compile(
    r"^[ \t]*(?:(?:export|pub|public|private|final)[ \t]+)*(?:const|let|var|val|static)[ \t]+\w[^=\n]*=|^[ \t]*[A-Z][A-Z0-9_]+[ \t]*(?::[^=\n]+)?=(?!=)"
)
COMMENT = re.compile(r"^[ \t]*(?://|#|--|/?\*)")
VISUALS = (
    "a ```mermaid flow for routing or ownership, a ```compare or a markdown table for options, a ```chart for numbers,"
    " an ```arch diagram for components, the real call in a fence naming its language (```sand, ```sql, ```go) when the"
    " question is about a code or API shape, or a picture on a line of its own"
)
JSON_KINDS = ("arch", "chart", "compare")
COMPARE_SHAPE = '{"options": [{"name": …, "recommended": true, "language": …, "code": …, "points": ["+ a gain", "- a cost"]}, …]}'
SEPARATOR_CELL = re.compile(r":?-+:?")
CHART_MARKS = ("bar", "line", "area", "arc")
CHART_SHAPE = "a bar, line or area mark plotting one quantitative axis against one of categories, or an arc of a theta field by a color field, its rows inline in data.values"
CHART_TITLE = 50
CHART_LABEL = 20
CHART_SERIES = 12
CHART_CATEGORIES = 20
CHARTS = 2
OPTION_CAPTION = re.compile(r"([1-9]) · \S")
FLOW_NODES = 4
WIDE_FLOW = re.compile(r"^[ \t]*((?:flowchart|graph)[ \t]+(?:LR|RL))\b(.*)", re.MULTILINE | re.DOTALL)
FLOW_STATEMENT = re.compile(r"[;\n]")
FLOW_SKIP = re.compile(r"[ \t]*(?:%%|(?:subgraph|end|classDef|class|style|linkStyle|click|direction)\b)")
FLOW_LABEL = re.compile(r'"[^"\n]*"|\|[^|\n]*\||\[[^\]\n]*\]|\([^)\n]*\)|\{[^}\n]*\}')
FLOW_LINK_TEXT = re.compile(r"(?<![-=.])(?:--|==|-\.)[ \t]+[^-=.>\s][^\n]*?[ \t]+(?=-->|==>|\.->|---|===)")
FLOW_LINK = re.compile(r"\s*<?(?:-\.+-|-{2,}|={2,})[->ox]?\s*")
FLOW_NODE = re.compile(r"\s*(\w+)")

WORD = re.compile(r"\S*[^\W_]\S*")
LABELLED = re.compile(r"<[^<>|]*\|([^<>]*)>")
TOKEN = re.compile(r"<[^<>]*>")
URL = re.compile(r"https?://\S+")
FEATURE = re.compile(r"^\*[^*\n]+\*[ \t]*$", re.MULTILINE)
FINDINGS = re.compile(r"^[ \t]*Findings:[ \t]*$", re.MULTILINE)
SHOW_ME = re.compile(r"^[ \t]*Show me:[ \t]*$", re.MULTILINE)
SHOWN = re.compile(r"^\*Show me\*[ \t]*$", re.MULTILINE)
OPTIONS_HEADING = re.compile(r"options:", re.IGNORECASE)
QUOTE = ">"
REDONE = "*This question is being redone:* "
REBUILT = "Direct fix: none: rebuilt from the posted card"
POSTED_PICTURE = re.compile(r"!\[(?P<caption>[^\]]*)\]\((?P<path>/[^)\s]+)\)")
NAMELESS_CAPTION = re.compile(r"Show me(?: \(\d+\))?")
FENCE = re.compile(r"^[ \t]*```")
PICTURE = re.compile(r"!\[([^\]\n]*)\]\(\s*(?:<[^<>\n]+>|[^\s()<>]+)\s*\)")
RECOMMENDED = re.compile(r"\s*\(recommended\)\s*$", re.IGNORECASE)
DIRECT_FIX = re.compile(r"^[ \t]*Direct fix:[ \t]*(.*?)[ \t]*$", re.MULTILINE)
NO_DIRECT_FIX = re.compile(r"none[ \t]*:[ \t]*\S", re.IGNORECASE)
HEAD_END = re.compile(r"(?<!:):(?!:)(?=\s|$)")
QUOTED = re.compile(r'"[^"\n]*"|“[^”\n]*”|`[^`\n]*`')
KINDS = r"(?:question|PR|tap|pick|design|status) cards?|pull requests?|PRs?"
POST_KINDS = rf"{KINDS}|change"
DRIVE_KINDS = ("PR", "card")
DETERMINERS = r"the|these|those|its|his|her|their|our|your|my"
FILLER = (
    rf"(?!(?:{DETERMINERS}|this|that|a|an|will|would|can|could|should|must|may|might|to|which|who|and|or|but|not|never|next|is|are|was"
    r"|were|be|been|has|have|had|does|did|do|it|in|on|of|for|with|at|by|from|as|so|then|also)\b)[\w-]+(?:['’]s)?"
)
POSSESSIVE = r"[\w-]+['’]s"
COUNT = r"(?:\d+|one|two|three|four|five|six|both|several)"
COMPOUND = (
    r"(?![ \t]+(?:checks?|steps?|buttons?|notes?|tooling|scripts?|rules?|lanes?|queues?|times?|ids?|numbers?|names?"
    r"|branch(?:es)?|caches?|process(?:es)?|paths?|pipelines?|box(?:es)?|machines?|tools?|systems?|commands?|flows?"
    r"|trains?|gates?|press|hooks?|jobs?|targets?|modes?|flags?|settings?|channels?|freezes?|skills?)\b)"
)
NAMED, POST_NAMED = (
    re.compile(rf"\b(?:{DETERMINERS}|{POSSESSIVE})\s+(?:{COUNT}\s+)?(?:{FILLER}\s+){{0,2}}?(?:{kinds})(?![-\w]){COMPOUND}", re.IGNORECASE)
    for kinds in (KINDS, POST_KINDS)
)
KIND, POST_KIND = (re.compile(rf"\b(?:{kinds})(?![-\w])", re.IGNORECASE) for kinds in (KINDS, POST_KINDS))
MARKDOWN_LINK = re.compile(r"\[([^\]\n]*)\]\((https?://[^)\s]+)\)")
FENCED = re.compile(r"^[ \t]*```.*?^[ \t]*```[^\n]*$", re.MULTILINE | re.DOTALL)
MASK = "\x00"
CLAUSE_BREAK = re.compile(r"[.;:,!?]+(?=\s|$)|\n")
UNBUILT = re.compile(
    rf"\b(?:no|without(?:\s+an?)?)\s+(?:{COUNT}\s+)?(?:[\w-]+\s+)?(?:{KINDS})(?![-\w])|\bnone\b(?!\s+of)|\bgoes up\b|\b(?:is|comes|goes) next\b"
    r"|\bwill open\b|\bopens next\b|\bbeing (?:written|built)\b|\bwhen it opens\b|\bnot (?:yet )?(?:exist|open|opened|up)\b|n't (?:yet )?(?:exist|open|opened|up|gone up)\b",
    re.IGNORECASE,
)
GENERIC = re.compile(r"\b(?:every|each|any|such|all)\s+$", re.IGNORECASE)
LINK = re.compile(r"<[^<>|]+\|([^<>]*)>")
NUMBER_LABEL = re.compile(r"\s*(?:(?:PR|pull request)\s*)?#?\d+\s*", re.IGNORECASE)
MARKUP = re.compile(
    r"\[[^\]\n]*\]\([^)\s]*\)|<(?:https?://|mailto:|[@#!])[^<>\n]*>|https?://\S+|`[^`\n]+`"
    r"|(?<![\w*_~])[*_~](?=\S)[^*_~\n]*?(?<=\S)[*_~](?![\w*_~])"
)
MARKED = re.compile(
    r"\[([^\]\n]*)\]\([^)\s]*\)|<(?:https?://|mailto:|[@#!])[^<>|\n]*\|([^<>\n]*)>|`([^`\n]+)`"
    r"|(?<![\w*_~])[*_~](?=\S)([^*_~\n]*?)(?<=\S)[*_~](?![\w*_~])"
)
PLAIN_TEXT_RULING = 'his Nov 10 11:21 PM words "look at the formatting you have a link in teh titkle that is just raw markfowen"'
PROPER_TERMS_RULING = 'his Nov 11 12:36 AM words "use the proper fucking terms for things"'
DRIVE_ID = re.compile(r"\b[jo]\d+\b")
VAGUE = re.compile(
    r"\b(?:the|this|that|these|those|your|our)\s+(?:\w+\s+)?(?:change|fix|pick|copy|thing|work|issue|problem|bug|approach|code|path)s?\b"
    r"|\bthis\b(?=\s*[?:,]|$)",
    re.IGNORECASE,
)
TERM = re.compile(
    r"\b\w+_\w+\b|\b\w+\(\)|\b[A-Z][A-Z0-9]{2,}\b|\b[A-Z]{2,}[a-z]{2,}\w*\b|\b[a-z]+[A-Z]\w*\b|\b[A-Z][a-z]+[A-Z]\w*\b"
    r"|\b[\w-]+\.(?:py|go|rs|ts|tsx|js|sql|md|json|ya?ml|toml|sh)\b"
)
RETURN_WORDS = {"ASK", "DONE", "READY", "BUILT", "WAITING", "BLOCKED", "DESIGN", "CHECK", "NATIVE", "URL"}
PR_NUMBER = re.compile(r"#\d+\b")
OPTION_POINTER = re.compile(
    r"\b(?:(?:same\s+as|as\s+in|like|unlike|than|plus)\s+[#(]?[1-9]\b|options?\s+[#(]?[1-9]\b"
    r"|the\s+(?:first|second|third|fourth|last|other|previous)\s+(?:option|one|pick)\b)",
    re.IGNORECASE,
)
LANDING = re.compile(r"(?:land|merge)\b", re.IGNORECASE)
STACK_RULING = 'his Nov 10 10:34 PM words "we should be using the "Stack card" which shows seral PRs instasd of this"'
NEGATION = re.compile(r"\b(?:no|not|never|without|nothing|none|neither|nor)\b|n't\b", re.IGNORECASE)
CLAUSE_END = re.compile(r"[.;:,!?\n]")
MECHANISM_SHAPE = "what runs, on what trigger, what it reads and what reaches whom"
OPTION_SHAPE = f"{MECHANISM_SHAPE}, then what he gets and what it costs"
MECHANISM_RULING = (
    'his Nov 11 1:14 AM words "That wasn\'t clear from the card, polling is fine for seeing when the function output changed.'
    ' Get rid of all this and ask me the question again properly"'
)
MECHANISM_EXAMPLE = (
    '"the Mac reruns the check function every 30 seconds and compares its output; a change sends one inbox message that wakes the agent"'
)
MECHANISM_WORD = (
    r"poll(?:s|ed|ing)?|push(?:es|ed|ing)?|subscri(?:be|bes|bed|bing|ptions?)|notif(?:y|ies|ied|ying|ications?)"
    r"|listen(?:s|ed|ing|ers?)?|events?"
)
MECHANISM = re.compile(rf"\b(?:{MECHANISM_WORD})\b", re.IGNORECASE)
MECHANISM_LABEL = re.compile(
    rf"\b(?:{MECHANISM_WORD})(?:[ \t]+|-)(?:sources?|systems?|APIs?|based|driven|models?|modes?|layers?|mechanisms?|approach(?:es)?|style)\b",
    re.IGNORECASE,
)
MECHANISM_FILLER = re.compile(
    r"only|just|now|later|first|then|also|instead|too|again|today|always|ever|no|not|never|without|none|a|an|the|and|or"
    r"|every|each|once|per|\d+|s|secs?|seconds?|mins?|minutes?|hours?|days?",
    re.IGNORECASE,
)
MECHANISM_TERM = re.compile(rf"[^\W_]+|{MASK}+")
SAND_NAME = re.compile(r"[\w/]+::\w*")
NO_SWEEPERS = "b89209a"
SWEEPER = re.compile(
    r"\b(?:sweeper|reaper|janitor|reclaim(?:er|\s+loop))s?\b"
    r"|\b(?:sweep|reap|reclaim|garbage[- ]collect|clean(?:s|ing)?\s+up|expire|requeue|release|reset|delete)(?:s|ed|ing)?\s+(?:[\w-]+\s+){0,2}?"
    r"(?:orphan(?:ed|s)?|stale|stuck|abandoned|dangling|leaked|zombie|left-?over)\b"
    r"|\b(?:older|stale|idle|unclaimed|stuck)\s+(?:than|for|after|past)\s+(?:a|an|one|\d+)\s*(?:secs?|seconds?|mins?|minutes?|hours?|days?)\b",
    re.IGNORECASE,
)
RETRY = re.compile(
    r"\b(?:ask|tell|ping|remind|wait\s+for|check\s+with)\s+(?:[\w'’-]+\s+){1,3}?(?:to\s+)?"
    r"(?:re-?try|try\s+(?:it\s+)?again|re-?run|re-?test|reproduce|confirm|verify)\b",
    re.IGNORECASE,
)
RULED_OUT = re.compile(
    rf"(?:{NEGATION.pattern}|\b(?:remov|delet|retir|replac)(?:e|es|ed|ing)\b|\bdrop(?:s|ped|ping)?\b|\bkill(?:s|ed|ing)?\b"
    r"|\bget(?:s|ting)?\s+rid\s+of\b|\binstead\s+of\b)\s+(?:[\w'’-]+\s+){0,2}$",
    re.IGNORECASE,
)
RECORD_CHARS = 120
WHOLE_KINDS = {"owner", "answer", "incident"}


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


def rebuilt(card: dict) -> str:
    """The ASK block a posted question card draws again, rebuilt from the card as the drive stored it for a question whose job kept no file: its question, summary and options in the words he read, the recommended one marked, and each Show me picture as the fence its source.json keeps, under its caption, or as the picture's line where none is kept."""
    lines = card["body"].split("\n")
    shown = lines.index("*Show me*") if "*Show me*" in lines else len(lines)
    show_me = []
    for line in lines[shown + 1 :]:
        posted = POSTED_PICTURE.fullmatch(line.strip())
        kept = posted and Path(posted["path"]).with_name("source.json")
        if not (kept and kept.is_file() and (source := json.loads(kept.read_text()))["kind"] != "image"):
            show_me.append(line)
            continue
        if not NAMELESS_CAPTION.fullmatch(posted["caption"]):
            show_me.append(posted["caption"])
        show_me += [f"```{source['kind'].removeprefix('code:')}", source["source"].rstrip("\n"), "```"]
    return "\n".join(
        [
            f"ASK {card['title']}",
            *(line for line in lines[:shown] if not line.startswith(REDONE)),
            *(["Show me:", *show_me] if show_me else []),
            *(f"- {option}{' (recommended)' if option == card['recommended'] else ''}" for option in card["options"]),
            "Findings:",
            REBUILT,
            "",
        ]
    )


def card_words(card: dict) -> int:
    """The words he reads on a question card, counted by `drive job ask` over the card it keeps and by the post hook over the card posted: its title, the body above its `*Show me*` line but the line saying the question is being redone, and its options."""
    summary = [line for line in SHOWN.split(card.get("body") or "", maxsplit=1)[0].splitlines() if not line.startswith(REDONE)]
    return sum(map(words, [card.get("title") or "", *summary, *(card.get("options") or [])]))


def card_refusal(card: dict, beside: str) -> str | None:
    """Why a question card is over his length; its Show me, everything under the body's `*Show me*` line, is drawn whole and never counted, and a post's text beside the card is drawn atop its body unless the body holds it already, as cc-slack draws it, so a drive's question card, whose words `drive job ask` kept and counted, carries none. A cc-slack permission card, its waits `slack:…`, holds the whole post it asks to send and keeps no limit."""
    if (card.get("waits") or "").startswith(PERMISSION_WAITS):
        return None
    options = card.get("options") or []
    long = [f'"{option}" is {words(option)} words' for option in options if words(option) > OPTION_WORDS]
    body = card.get("body") or ""
    drawn = beside.strip() not in body
    if drawn and card.get("drive") and card.get("waits"):
        return (
            f"refused: this post's text is {words(beside)} words cc-slack draws atop the card, and a drive's question card carries"
            " only the words of the ASK block `drive job ask` kept and counted: post the same card with no text." + from_ask(card)
        )
    count = card_words(card | {"body": f"{beside}\n{body}"} if drawn else card)
    problems = [f"each option is one line of at most {OPTION_WORDS} words, {OPTION_SHAPE}: {', '.join(long)}"] if long else []
    if count > QUESTION_WORDS:
        problems.append(
            f"this card is {count} words, and a question with its summary and options is at most {QUESTION_WORDS} words in all;"
            " its Show me goes under a `*Show me*` line in the body, drawn whole and not counted"
        )
    if not problems:
        return None
    return f"refused: {'; '.join(problems)} ({READING})." + from_ask(card)


def from_ask(card: dict) -> str:
    """How a drive's question card, one with a waits id that is no cc-slack permission card's, is redrawn once its words are fixed."""
    waits = card.get("waits") or ""
    return FROM_ASK.format(waits=waits) if waits and not waits.startswith(PERMISSION_WAITS) else ""


def question(text: str, kept: bool = False, unsettled: str | None = None) -> Question:
    """The one question a job's ASK block puts to him, refused with every way it is out of shape; a block `drive job ask` already kept is read with no second check of its Show me's visuals or its options' content, so a card it kept under older rules still redraws, and an option that cleans up a bad state passes only when `unsettled` names his ruling against it and why."""
    asked, *findings = FINDINGS.split(text, maxsplit=1)
    head, *example = SHOW_ME.split(asked, maxsplit=1)
    lines = [line.strip() for line in head.splitlines() if line.strip()]
    if not lines or not lines[0].startswith("ASK "):
        raise DriveError("an ASK block opens with `ASK <the question, one line>`")
    title, rest = lines[0].removeprefix("ASK ").strip(), lines[1:]
    first_option = options_start(rest)
    summary, above = rest[:first_option], rest[first_option:]
    show_me, after = shown_and_options(example[0] if example else "")
    headings = [line for line in above + after if OPTIONS_HEADING.fullmatch(line)]
    after = [line for line in above + after if not OPTIONS_HEADING.fullmatch(line)]
    options = [line.removeprefix("- ").strip() for line in after if line.startswith("- ")]
    marked = [option for option in options if RECOMMENDED.search(option)]
    options = [RECOMMENDED.sub("", option) for option in options]
    problems = []
    if not 1 <= len(summary) <= SUMMARY_LINES:
        problems.append(
            f"the summary is {len(summary)} lines; it is one to three: what was found and what is being decided,"
            " the recommendation, what happens meanwhile"
        )
    problems += show_me_problems(show_me, bool(example and above), kept, len(options))
    if headings and not kept:
        problems.append(f'"{headings[0]}" is not a line of an ASK block: the `- ` options follow the Show me directly, with no heading')
    if strays := [line for line in after if not line.startswith("- ")]:
        problems.append(f"only `- ` option lines follow the Show me, then `Findings:`; these are neither: {strays}")
    if len(options) not in OPTION_COUNT:
        problems.append(f"{len(options)} options; an ASK has {OPTION_COUNT.start} to {OPTION_COUNT.stop - 1}, one line each")
    if len(marked) != 1:
        problems.append(f"{len(marked)} options end with (recommended); exactly one does")
    elif not kept and (recommended := RECOMMENDED.sub("", marked[0])) != options[0]:
        problems.append(f'the recommended option "{recommended}" is not first: put it first, as the card draws it')
    problems += [
        f'option "{option}" is {words(option)} words; each is one line of at most {OPTION_WORDS}: {OPTION_SHAPE}'
        for option in options
        if words(option) > OPTION_WORDS
    ]
    shown = [title, *summary, *options]
    if (count := card_words({"title": title, "body": card_body(summary, show_me), "options": options})) > QUESTION_WORDS:
        problems.append(f"the question, summary and options are {count} words; at most {QUESTION_WORDS} in all, the Show me not counted")
    plain = URL.sub(" ", TOKEN.sub(" ", "\n".join(shown)))
    problems += [
        f'"{found}" is a drive job or order id, which he cannot open: say what that work is; a function, syscall or file keeps'
        f" its proper name ({PROPER_TERMS_RULING})"
        for found in DRIVE_ID.findall(plain)
    ]
    problems += [
        f'"{found}" points at an option by number or position; cards renumber, so each option says its own words'
        for found in OPTION_POINTER.findall(URL.sub(" ", TOKEN.sub(" ", "\n".join([*shown, *captions(show_me)]))))
    ]
    problems += unlinked([*summary, *captions(show_me)], [title, *options], DRIVE_KINDS)
    problems += [
        f'{where} holds "{found}", which Slack draws as raw text, since a card\'s title and buttons take no markdown or links;'
        f" write it there without the markup, a term by its proper name, and put any link in the summary ({PLAIN_TEXT_RULING};"
        f" {PROPER_TERMS_RULING})"
        for where, line in [("the question", title), *((f'option "{option}"', option) for option in options)]
        for found in MARKUP.findall(line)
    ]
    if not kept:
        problems += [
            f'{where} names a mechanism by a bare word in "{found}", which leaves him to guess what runs, what it watches and what'
            f" reaches whom: say {MECHANISM_SHAPE}, such as {MECHANISM_EXAMPLE} ({MECHANISM_RULING})"
            for where, line in [("the question", title), *((f'option "{option}"', option) for option in options)]
            for found in bare_mechanisms(line)
        ]
        problems += option_problems(options, unsettled)
    if not kept and marked and LANDING.match(title) and LANDING.match(marked[0]) and PR_URL.search("\n".join(summary)):
        problems.append(
            "it asks him to land PRs, which is his tap on their stack card and never a question card: return READY naming"
            f" every PR it lands, bottom first, and the stack card that carries them needs no Show me ({STACK_RULING})"
        )
    if not kept:
        problems += vague_problems(title, [*summary, *(findings[:1])])
    if not findings or not findings[0].strip():
        problems.append("no `Findings:` line with the evidence under it; it stays on disk, for the record and Explain more")
    elif not kept and len(marked) == 1:
        problems += direct_fix_problems(findings[0], options, RECOMMENDED.sub("", marked[0]), summary)
    if problems:
        raise DriveError("the ASK is out of shape, " + READING + ":\n- " + "\n- ".join(problems))
    return Question(title, summary, show_me, options, RECOMMENDED.sub("", marked[0]))


def option_problems(options: list[str], unsettled: str | None) -> list[str]:
    """Why an option is no fix he can pick: it asks someone to retry or confirm, or cleans up a bad state with a sweeper, time rule or reclaim loop, which his ruling refuses unless `unsettled` names it and says why; an option that rules either out, as "never ask Rowan to retry" and "remove the sweeper" do, proposes neither."""
    problems = [
        f'option "{option}" asks someone to retry or confirm "{found[0]}", which fixes nothing: offer the fix itself, the change or'
        " repair that stops the failure, and the drive proves it"
        for option in options
        if (found := proposed(RETRY, option))
    ]
    if NO_SWEEPERS in (unsettled or ""):
        return problems
    return problems + [
        f'option "{option}" cleans up a bad state with "{found[0]}", a sweeper, time rule or reclaim loop: design the state out so it'
        f' cannot exist, such as one owner holding it until it ends, or keep the option with --unsettled "{NO_SWEEPERS}: <why the'
        f' state cannot be designed out>" (his ruling {NO_SWEEPERS}, "No sweepers")'
        for option in options
        if (found := proposed(SWEEPER, option))
    ]


def proposed(pattern: re.Pattern[str], option: str) -> re.Match[str] | None:
    """The first of the pattern's matches that the option proposes rather than rules out: one negated or removed by the words just before it, as in "never ask Rowan to retry" or "remove the sweeper", or negated within it, as in "ask Rowan not to retry", is none."""
    return next((found for found in pattern.finditer(option) if not RULED_OUT.search(option, 0, found.start()) and not NEGATION.search(found[0])), None)


def redrawn(text: str) -> Question:
    """The question a kept block draws again: read as kept, with the markdown in its question and options, which Slack draws raw on a card's title and buttons, taken down to the words it marks, so a block kept before that rule redraws with the words he read; its Show me's fences and its findings stay as kept."""
    lines, fenced, ended = [], False, False
    for line in text.splitlines(keepends=True):
        ended = ended or bool(FINDINGS.match(line))
        fenced ^= bool(FENCE.match(line))
        plain = not (ended or fenced) and line.lstrip().startswith(("ASK ", "- "))
        lines.append(MARKED.sub(lambda found: next(words for words in found.groups() if words is not None), line) if plain else line)
    return question("".join(lines), kept=True)


def shown_and_options(example: str) -> tuple[list[str], list[str]]:
    """The Show me's lines as written, fences and blank lines kept, and the lines where the options start on."""
    lines = example.strip("\n").splitlines()
    start = options_start(lines)
    return lines[:start], [line.strip() for line in lines[start:] if line.strip()]


def options_start(lines: list[str]) -> int:
    """Where the options start: the first `- ` line outside a code fence, or a bare `Options:` line just above it, which no ASK block holds and a block kept before the drive refused one still may."""
    fenced = False
    for index, line in enumerate(lines):
        if not fenced and line.strip().startswith("- "):
            above = [at for at in range(index) if lines[at].strip()]
            return above[-1] if above and OPTIONS_HEADING.fullmatch(lines[above[-1]].strip()) else index
        if FENCE.match(line):
            fenced = not fenced
    return len(lines)


def captions(show_me: list[str]) -> list[str]:
    """The Show me's lines outside its code fences."""
    kept, fenced = [], False
    for line in show_me:
        if FENCE.match(line):
            fenced = not fenced
        elif not fenced and line.strip():
            kept.append(line.strip())
    return kept


def direct_fix_problems(findings: str, options: list[str], recommended: str, summary: list[str]) -> list[str]:
    """Whether the findings name the option that is the fix the job's own diagnosis points at, which every question offers and recommends unless its summary says why not, or say no diagnosis points at one."""
    named = DIRECT_FIX.search(findings)
    if not named:
        missing = (
            "no `Direct fix:` line under `Findings:`: name the option that is the fix its diagnosis points at, by its words,"
            " or write `Direct fix: none: <why>` when no diagnosis points at one; that fix is always an option, and the"
            " recommended one unless the summary says why not"
        )
        return [missing]
    if NO_DIRECT_FIX.match(named[1]):
        return []
    fix = named[1].casefold()
    option = next((option for option in options if fix in (option.strip().casefold(), option_head(option).casefold())), None)
    if option is None:
        unnamed = (
            f"`Direct fix: {named[1]}` names no option: the fix its diagnosis points at is always an option, so add it, or"
            " repeat the words of the option it is, whole or up to the colon that ends its first part"
        )
        return [unnamed]
    if option == recommended or option_head(option).casefold() in " ".join(summary).casefold():
        return []
    unexplained = (
        f'the direct fix "{option_head(option)}" is an option but not the recommended one, and the summary never says why not:'
        " recommend it, or add a summary sentence that names it by those words and says why another option is better"
    )
    return [unexplained]


def vague_problems(title: str, evidence: list[str]) -> list[str]:
    """Whether the question points at its object with a vague noun while its summary or findings name that object's proper terms."""
    if not (vague := VAGUE.search(title)):
        return []
    terms = list(dict.fromkeys(term for term in TERM.findall(URL.sub(" ", "\n".join(evidence))) if term not in RETURN_WORDS))
    if not terms or TERM.search(title):
        return []
    problem = (
        f'the question names its object only as "{vague[0]}", while its summary and findings name {", ".join(terms[:4])}:'
        f" name it by its proper term in the question itself ({PROPER_TERMS_RULING})"
    )
    return [problem]


def option_head(option: str) -> str:
    """An option's words up to the colon that ends its first part, a colon followed by a space; `blob::Blob` and a URL's colon stay in it."""
    return HEAD_END.split(option, maxsplit=1)[0].strip()


def show_me_problems(show_me: list[str], below_options: bool, kept: bool, options: int) -> list[str]:
    if below_options:
        return ["the options come after the Show me, never above it: summary, then `Show me:` and its example, then the options"]
    if not any(line.strip() for line in show_me):
        return []
    problems = [
        f'Show me line "{line}" is {words(line)} words; outside a code fence each is a caption of at most {OPTION_WORDS}'
        for line in captions(show_me)
        if not line.startswith(("|", QUOTE)) and words(line) > OPTION_WORDS
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
    return problems if kept else [*problems, *visual_problems(show_me, options), *chart_problems(show_me), *gallery_problems(show_me, options), *flow_problems(show_me)]


def visual_problems(show_me: list[str], options: int) -> list[str]:
    """Why a Show me would draw nothing worth a picture: a fence of words or a mock, a fence of raw data or tool output, a constant, a code picture per option, a picture drawn by hand, PRs listed by number, a chart, compare or arch whose JSON does not draw, or no visual at all. A draft message or a quote, as `> ` lines or a fence of sentences, is none of these: his card shows it as a quote of its text."""
    text = "\n".join(show_me)
    every = list(picture.FENCE.finditer(text))
    fences = [fence for fence in every if not picture.quoted(fence[1], fence[2])]
    named = [fence for fence in fences if fence[1].lower() not in picture.WORDS]
    raw = [(fence, what) for fence in named if (what := picture.raw(fence[1], fence[2]))]
    drawn = [fence for fence in named if not picture.raw(fence[1], fence[2])]
    code = [fence for fence in drawn if fence[1].lower() not in picture.DIAGRAMS]
    problems = [
        f'the Show me fence holding "{cut(" ".join(fence[2].split()), 60)}" names no language, so his card would get a mock or words'
        " in a code block: name the language of real code, write a message or quote as plain sentences, which his card shows as a"
        " quote, put real rows in a markdown table, draw the flow in a ```mermaid or ```arch fence, or leave the Show me out"
        f" ({WORDS_RULING}; {MOCK_RULING})"
        for fence in fences
        if fence[1].lower() in picture.WORDS
    ]
    problems += [
        f'the Show me ```{fence[1]} fence holding "{cut(" ".join(fence[2].split()), 60)}" is {what}, raw data or tool output,'
        f" and a picture of it shows him nothing: {picture.INSTEAD} ({RAW_RULING})"
        for fence, what in raw
    ]
    problems += [
        f'the Show me ```{fence[1]} fence holding "{cut(" ".join(fence[2].split()), 60)}" shows a constant, which a sentence says'
        " as well as a picture: put the value in the summary or an option, show a real snippet of several lines when its shape"
        f" matters, or leave the Show me out ({CONSTANT_RULING})"
        for fence in code
        if constant(fence[2])
    ]
    if len(code) >= max(2, options):
        problems.append(
            f"the Show me draws {len(code)} code fences as {len(code)} pictures, one per option and each full size: carry them in"
            f" one ```compare whose options name each, so he sees them side by side, labeled ({BIG_RULING}; {GALLERY_RULING})"
        )
    problems += [
        f'the Show me picture "{shown[1]}" is a file drawn by hand for the card, its words and tables baked into pixels in no style of'
        " the drive's: carry its source in a fence the drive draws, a ```mermaid or ```arch diagram or a ```compare labeled per"
        f" option, and its tables as markdown, which Slack shows natively, or leave the Show me out ({HAND_RULING}; {NATIVE_RULING})"
        for line in captions(show_me)
        if (shown := PICTURE.fullmatch(line)) and drawn_by_hand(picture.IMAGE.search(line)[1])
    ]
    if len(prs := dict.fromkeys(PR_NUMBER.findall(TOKEN.sub(" ", text)))) >= 2:
        problems.append(
            f"the Show me lists {len(prs)} PRs by number, {', '.join(prs)}: a stack card shows several PRs, each with its checks and his tap,"
            f" so post them as one PR card through say's tap_prs and leave the Show me out ({STACK_RULING})"
        )
    problems += [f"the Show me ```{fence[1]} fence {problem}" for fence in drawn if (problem := json_problem(fence[1].lower(), fence[2]))]
    quoted = len(fences) < len(every) or any(line.startswith(QUOTE) for line in captions(show_me))
    if not quoted and not drawn and not raw and not tables(show_me) and not any(PICTURE.fullmatch(line) for line in captions(show_me)):
        problems.append(
            f"the Show me holds no visual, only words: add the kind that shows what the words cannot, {VISUALS}, or leave out"
            f" the `Show me:` line when nothing does ({LIBRARY_RULING}; {WORDS_RULING})"
        )
    return problems


def chart_problems(show_me: list[str]) -> list[str]:
    """Why Slack's own chart block, which cc-slack draws each ```chart of a card as, cannot hold the Show me's charts: more of them than a card holds, or one whose title, labels, series or categories run past its limits."""
    charts = [fence[2] for fence in picture.FENCE.finditer("\n".join(show_me)) if fence[1].lower() == "chart"]
    problems = [
        f"the Show me ```chart fence {problem}, so Slack cannot draw it as its own chart: fit it to Slack's limits ({CHART_RULING})"
        for source in charts
        if json_problem("chart", source) is None
        for problem in chart_limits(json.loads(source))
    ]
    if len(charts) > CHARTS:
        problems.append(
            f"the Show me holds {len(charts)} ```chart fences, and Slack draws at most {CHARTS} charts on a card: keep the {CHARTS} the"
            f" choice turns on, or put the other numbers in a markdown table ({CHART_RULING})"
        )
    return problems


def chart_limits(spec: dict) -> list[str]:
    """Each limit of Slack's chart block a Vega-Lite spec runs past, read as cc-slack reads it: titled by its title or "<value field> by <category field>", one label for each category, one series for each color value or else one named for its value axis, and an arc's slices labeled by color."""
    encoding, rows, mark = spec.get("encoding") or {}, (spec.get("data") or {}).get("values") or [], spec_text(spec.get("mark"), "type")
    pie = mark == "arc"
    axes = (encoding.get("theta") or {}, encoding.get("color") or {}) if pie else (encoding.get("x") or {}, encoding.get("y") or {})
    value, category = axes if pie else sorted(axes, key=lambda axis: axis.get("type") != "quantitative")
    readable = pie or (value.get("type") == "quantitative" and category.get("type") != "quantitative")
    if mark not in CHART_MARKS or not rows or not value.get("field") or not category.get("field") or not readable:
        return [f"is no chart Slack draws; it holds {CHART_SHAPE}"]
    title = spec_text(spec.get("title"), "text") or f"{value['field']} by {category['field']}"
    labels = list(dict.fromkeys(chart_label(row.get(category["field"])) for row in rows))
    if pie:
        series = labels
    elif color := (encoding.get("color") or {}).get("field"):
        series = list(dict.fromkeys(chart_label(row.get(color)) for row in rows))
    else:
        series = [spec_text(value.get("title"), "text") or value["field"]]
    problems = [f'is titled "{title}", {len(title)} characters, and a Slack chart\'s title holds at most {CHART_TITLE}'] if len(title) > CHART_TITLE else []
    problems += [
        f'has the label "{label}", {len(label)} characters, and each label holds 1 to {CHART_LABEL}'
        for label in dict.fromkeys([*labels, *series])
        if not 0 < len(label) <= CHART_LABEL
    ]
    if len(series) > CHART_SERIES:
        problems.append(f"draws {len(series)} {'slices' if pie else 'series'}, and a Slack chart holds at most {CHART_SERIES}")
    if not pie and len(labels) > CHART_CATEGORIES:
        problems.append(f"has {len(labels)} categories, and a Slack chart holds at most {CHART_CATEGORIES}")
    return problems


def spec_text(raw: object, key: str) -> str:
    if isinstance(raw, dict):
        return spec_text(raw.get(key), key)
    return raw if isinstance(raw, str) else ""


def chart_label(value: object) -> str:
    return value if isinstance(value, str) else json.dumps(value)


def gallery_problems(show_me: list[str], options: int) -> list[str]:
    """Why a Show me drawing two or more pictures, which Slack shows side by side as one gallery titled by their captions, does not label each by its option: each caption opens with the number his card gives that option and a middle dot, as `1 · …`."""
    text = "\n".join(show_me)
    shown = [fence["caption"] or "" for fence in picture.CAPTIONED.finditer(text) if picture.pictured(fence["language"], fence["source"])]
    shown += [found[1] for line in captions(show_me) if (found := PICTURE.fullmatch(line))]
    if len(shown) < 2:
        return []
    unlabeled = [f'the caption "{caption}"' if caption else "a picture with no caption line above its fence" for caption in shown if not ((number := OPTION_CAPTION.match(caption)) and int(number[1]) <= options)]
    return [
        f"the Show me draws {len(shown)} pictures, which Slack shows side by side as one gallery titled by their captions, and {where}"
        ' names no option: open each caption with the number his card gives its option and a middle dot, as "1 · ", the'
        f" recommended option 1 and the rest in their order ({GALLERY_RULING})"
        for where in unlabeled
    ]


def flow_problems(show_me: list[str]) -> list[str]:
    """Why a Show me's Mermaid flow drawn across would not read on his phone: one chain of more than four nodes left to right, which shrinks its words to about 5 px there."""
    return [
        f"the Show me ```mermaid `{flow[1]}` runs a chain of {chain} nodes across, which shrinks its words to about 5 px on his"
        f" phone: draw it top to bottom, `flowchart TD`, so it reads there ({PHONE_RULING})"
        for fence in picture.FENCE.finditer("\n".join(show_me))
        if fence[1].lower() == "mermaid" and (flow := WIDE_FLOW.search(fence[2])) and (chain := flow_chain(flow[2])) > FLOW_NODES
    ]


def flow_chain(source: str) -> int:
    """The most nodes one path through a Mermaid flowchart's links runs, a link back to a node already on the path left out."""
    after: dict[str, set[str]] = {}
    for statement in FLOW_STATEMENT.split(source):
        if FLOW_SKIP.match(statement):
            continue
        ranks = [
            [node[1] for part in segment.split("&") if (node := FLOW_NODE.match(part))]
            for segment in FLOW_LINK.split(FLOW_LINK_TEXT.sub("", FLOW_LABEL.sub("", statement)))
        ]
        for left, right in pairwise(ranks):
            for node in left:
                after.setdefault(node, set()).update(right)
    longest: dict[str, int] = {}

    def walk(node: str, path: frozenset[str]) -> int:
        if node not in longest:
            longest[node] = 1 + max((walk(each, path | {node}) for each in after.get(node, ()) if each not in path | {node}), default=0)
        return longest[node]

    return max((walk(node, frozenset()) for node in after), default=0)


def constant(source: str) -> bool:
    """Whether a code fence's code, its comments aside, is one or two lines declaring a value."""
    lines = [line for line in source.splitlines() if line.strip() and not COMMENT.match(line)]
    return 1 <= len(lines) <= 2 and all(CONSTANT.match(line) for line in lines)


def drawn_by_hand(path: str) -> bool:
    """Whether a picture is a file an agent drew for a card and kept in a drive's asks or pictures folder, rather than one the drive draws from a fence."""
    found = Path(path).expanduser()
    return found.is_relative_to(root()) and len(parts := found.relative_to(root()).parts) > 2 and parts[1] in HAND_DRAWN


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
    return "card" if found.lower().endswith(("card", "cards")) else "PR"


def held(data: dict, order: str | None) -> tuple[str, ...]:
    """The kinds of thing the drive opens that a post on the order can name: a PR it holds on that order, or on any order when the post names none, and a card it put up."""
    prs = [pr for pr in data["prs"].values() if order in (None, pr["order"])]
    cards = [question for job in data["jobs"].values() for question in job["questions"] if question["card"]]
    return tuple(kind for kind, found in (("PR", prs), ("card", cards)) if found)


def unlinked(linked: list[str], plain: list[str], kinds: tuple[str, ...], mentions: tuple[re.Pattern[str], re.Pattern[str]] = (NAMED, KIND)) -> list[str]:
    """Each PR or card of the drive's, of the kinds it holds, that these lines name with no link at that spot, each problem once: `linked` lines draw `<url|label>` links, `plain` lines, a card's title and options, draw plain text, and a link anywhere in the `linked` lines whose words name a kind of thing covers every other mention of that kind. A plain noun, such as a thread, a fix or a release, names nothing the drive opens, and neither does a mention inside a code fence, a generic one such as every engineer's PR, or one in a clause saying it does not exist yet, such as "its PR goes up first" or "no PR yet"; one that exists keeps its link whatever its state, "has not merged" or "waits on your tap" included. `mentions` reads what names such a thing: a card's words name a PR or a card, a post's words "the change" too."""
    named_at, kind_of = mentions
    labels = [label for line in linked for label in LINK.findall(line)]
    linked_kinds = {kind(found) for label in labels for found in kind_of.findall(label)}
    markdown = [found for line in linked for found in MARKDOWN_LINK.finditer(FENCED.sub(" ", line))]
    problems = [f'"{found[0]}" is a markdown link, and Slack draws no [label](url) link: write <{found[2]}|{found[1]}>' for found in markdown]
    problems += [
        f'the link <…|{label}> says only a number; its text says what it is, such as what the change does'
        for label in [*labels, *(found[1] for found in markdown)]
        if NUMBER_LABEL.fullmatch(label)
    ]
    for lines, is_plain in ((linked, False), (plain, True)):
        for line in lines:
            bare = masked(line)
            problems += [
                f'"{number}" is a number he cannot open; link it by what it is, such as <https://github.com/…/pull/53618|the fix that adds the update button>'
                for number in PR_NUMBER.findall(bare)
            ]
            for match in named_at.finditer(bare):
                found, named = match[0], kind_of.findall(match[0])[-1]
                if kind(named) not in kinds or kind(named) in linked_kinds or GENERIC.search(bare, 0, match.start()):
                    continue
                read, written = clause(line, bare, match.start(), match.end())
                if UNBUILT.search(read):
                    continue
                if is_plain:
                    problems.append(
                        f'"{found}" in the question or an option names something he could open, and those draw no links; link it in the summary'
                        f" or a Show me caption, its link text naming the {named}, or say in that clause it does not exist yet ({LINKS_RULING})"
                    )
                else:
                    where = f', in "{written}"' if written != found else ""
                    problems.append(
                        f'"{found}" names something he could open with no link there{where}; put those words in its link, <url|{found}>, so he'
                        f' opens it where it is named, or say in that clause it does not exist yet, as "its PR goes up next" does ({LINKS_RULING})'
                    )
    return list(dict.fromkeys(problems))


def masked(line: str) -> str:
    """The line with each code fence, link, URL and quote blanked to as many marks, so no phrase runs across one and every other word keeps its place."""
    for pattern in (FENCED, MARKDOWN_LINK, TOKEN, URL, QUOTED):
        line = pattern.sub(lambda found: MASK * len(found[0]), line)
    return line


def clause(line: str, bare: str, start: int, end: int) -> tuple[str, str]:
    """The clause around a span of the line: its masked words, which the checks read, and its words as written, which a refusal quotes."""
    begin = max((found.end() for found in CLAUSE_BREAK.finditer(bare, 0, start)), default=0)
    stop = next((found.start() for found in CLAUSE_BREAK.finditer(bare, end)), len(bare))
    return bare[begin:stop], " ".join(line[begin:stop].split())


def bare_mechanisms(line: str) -> list[str]:
    """Each clause of a card's title or option that names a mechanism, such as polling, a push or an event, as a bare label: a clause of nothing but the mechanism and filler, as "Polls only" is, or the mechanism naming a kind of thing, as "a Poll source" and "the event system" do, so he cannot tell what runs, what it watches or what reaches whom. A Sand name such as `event::on` is its proper term, and a clause that says what the mechanism acts on, as "the Mac polls the check function" does, passes."""
    bare = SAND_NAME.sub(lambda found: MASK * len(found[0]), masked(line))
    found = []
    for match in MECHANISM.finditer(bare):
        read, written = clause(line, bare, match.start(), match.end())
        filler = all(MECHANISM.fullmatch(term) or MECHANISM_FILLER.fullmatch(term) for term in MECHANISM_TERM.findall(read))
        if filler or MECHANISM_LABEL.search(read):
            found.append(written)
    return list(dict.fromkeys(found))


def tap_owed_not(pr: dict) -> str:
    """Why no tap of his is owed on a drive PR, or "" when one is: he tapped it, it merged or closed, or it lands with no tap."""
    tap = pr.get("tap")
    reasons = [
        f"he tapped {tap['label']} at {when(tap['at'])}" if tap else "",
        pr["state"] if pr["state"] in ("merged", "closed") else "",
        f"it lands with no tap as {pr['exempt']}" if pr.get("exempt") else "",
        "the drive holds it with the hold label" if pr["hold_label"] else "",
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


def link_refusal(post: dict, kinds: tuple[str, ...]) -> str | None:
    """Why a handler's post to his DM names a PR or card of the kinds the drive holds without its link: a line's `text`, or a card's summary and Show me captions, carry each link where the thing is named, and its title and options, drawn as plain text, name only what the body links."""
    if card := post.get("card"):
        summary, *example = SHOWN.split(card.get("body") or "", maxsplit=1)
        problems = unlinked([summary, *captions(example[0].splitlines() if example else []), post.get("text") or ""], [card.get("title") or "", *(card.get("options") or [])], kinds)
    else:
        problems = unlinked([post.get("text") or ""], [], kinds, (POST_NAMED, POST_KIND))
    if not problems:
        return None
    return "refused: " + "; ".join(problems) + "." + (from_ask(card) if card else "")


def first_line(text: str) -> str:
    """A record's line as the other session sees it: its first line, cut at a word to the record limit."""
    line = text.strip().split("\n", 1)[0].strip()
    if len(line) <= RECORD_CHARS:
        return line
    return line[: RECORD_CHARS - 1].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def shortened(record: dict) -> dict | None:
    """The record with its text cut to one line and the whole text at the top of its body, or for a record that attaches a file as its body, which holds the rest, the line alone; his words and incidents stay whole."""
    text = (record.get("text") or "").strip()
    if (record.get("kind") or "").lower() in WHOLE_KINDS or (line := first_line(text)) == text:
        return None
    if record.get("path"):
        return record | {"text": line}
    body = record.get("body")
    return record | {"text": line, "body": f"{text}\n\n{body}" if body else text}
