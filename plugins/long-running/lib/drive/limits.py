import re
from dataclasses import dataclass

from drive.state import DriveError

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
SHOW_ME_RULING = (
    'his Nov 10 4:38 PM words "bring me the card while /after flehsing out more of the dfesign and then the optins'
    ' … the shw show-0me cvould be code snipeptrs"'
)

WORD = re.compile(r"\S*[^\W_]\S*")
LABELLED = re.compile(r"<[^<>|]*\|([^<>]*)>")
TOKEN = re.compile(r"<[^<>]*>")
URL = re.compile(r"https?://\S+")
FEATURE = re.compile(r"^\*[^*\n]+\*[ \t]*$", re.MULTILINE)
FINDINGS = re.compile(r"^[ \t]*Findings:[ \t]*$", re.MULTILINE)
SHOW_ME = re.compile(r"^[ \t]*Show me:[ \t]*$", re.MULTILINE)
SHOWN = re.compile(r"^\*Show me\*[ \t]*$", re.MULTILINE)
FENCE = re.compile(r"^[ \t]*```")
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


@dataclass(frozen=True)
class Question:
    title: str
    summary: list[str]
    show_me: list[str]
    options: list[str]
    recommended: str


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


def card_refusal(card: dict, beside: str) -> str | None:
    """Why a question card is over his length; its Show me, everything under the body's `*Show me*` line, is drawn whole and never counted."""
    options = card.get("options") or []
    long = [f'"{option}" is {words(option)} words' for option in options if words(option) > OPTION_WORDS]
    summary = SHOWN.split(card.get("body") or "", maxsplit=1)[0]
    count = words(card.get("title") or "") + words(summary) + words(beside) + sum(map(words, options))
    problems = [f"each option is one line of at most {OPTION_WORDS} words, what he gets and what it costs: {', '.join(long)}"] if long else []
    if count > QUESTION_WORDS:
        problems.append(
            f"this card is {count} words, and a question with its summary and options is at most {QUESTION_WORDS} words in all;"
            " its Show me goes under a `*Show me*` line in the body, drawn whole and not counted"
        )
    if not problems:
        return None
    return f"refused: {'; '.join(problems)} ({READING})."


def question(text: str) -> Question:
    """The one question a job's ASK block puts to him, refused with every way it is out of shape."""
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
    problems += show_me_problems(show_me, bool(example and above))
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
    if (count := sum(map(words, shown))) > QUESTION_WORDS:
        problems.append(f"the question, summary and options are {count} words; at most {QUESTION_WORDS} in all, the Show me not counted")
    plain = URL.sub(" ", TOKEN.sub(" ", "\n".join(shown)))
    problems += [f'plain words only: "{found}" is {what}' for what, pattern in NOT_PLAIN for found in pattern.findall(plain)]
    problems += [
        f'"{found}" points at an option by number or position; cards renumber, so each option says its own words'
        for found in OPTION_POINTER.findall(URL.sub(" ", TOKEN.sub(" ", "\n".join([*shown, *captions(show_me)]))))
    ]
    problems += unlinked([*summary, *captions(show_me)], [title, *options])
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


def show_me_problems(show_me: list[str], below_options: bool) -> list[str]:
    if below_options:
        return ["the options come after the Show me, never above it: summary, then `Show me:` and its example, then the options"]
    if not any(line.strip() for line in show_me):
        return [
            (
                "no `Show me:` line after the summary with a worked example under it: the real code, call, query or text,"
                f" before and after, code in ``` fences ({SHOW_ME_RULING})"
            )
        ]
    problems = [
        f'Show me line "{line}" is {words(line)} words; outside a code fence each is a caption of at most {OPTION_WORDS}'
        for line in captions(show_me)
        if words(line) > OPTION_WORDS
    ]
    fences = [line.strip() for line in show_me if FENCE.match(line)]
    problems += [
        f'"{fence}" names a language, which Slack draws as the first line of the code; open the fence with ``` alone'
        for fence in fences[::2]
        if fence != "```"
    ]
    if len(fences) % 2:
        problems.append("a ``` fence in the Show me is never closed")
    return problems


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
