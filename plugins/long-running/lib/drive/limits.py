import re
from dataclasses import dataclass

from drive.state import DriveError

LINE_WORDS = 60
SUMMARY_WORDS = 120
QUESTION_WORDS = 150
OPTION_WORDS = 25
SUMMARY_LINES = 3
OPTION_COUNT = range(2, 5)
READING = 'the coordinator\'s reading of his Nov 9 9:33 PM words "your messages need to be way shorter rewrite this for a human"'
LINE_REST = "Say what changed for him and what happens next, in plain words; everything else stays on disk until he asks for it."
ANSWER_REST = (
    "Answer what he asked in plain words; the rest goes behind the card's Explain more or into a write-up attached as a file,"
    " never a second line."
)
CONFIRMATION_REST = "List each rule now in the installed skill in plain words, one short line each; everything else stays on disk."

WORD = re.compile(r"\S*[^\W_]\S*")
LABELLED = re.compile(r"<[^<>|]*\|([^<>]*)>")
TOKEN = re.compile(r"<[^<>]*>")
URL = re.compile(r"https?://\S+")
FEATURE = re.compile(r"^\*[^*\n]+\*[ \t]*$", re.MULTILINE)
FINDINGS = re.compile(r"^[ \t]*Findings:[ \t]*$", re.MULTILINE)
RECOMMENDED = re.compile(r"\s*\(recommended\)\s*$", re.IGNORECASE)
ASKS_FOR_MORE = re.compile(
    r"\b(?:explain\w*|details?|detailed|why|show me|more|elaborate|expand|walk me through|in full"
    r"|compar\w*|differ\w*|vs|versus|pros|cons|trade-?offs?)\b",
    re.IGNORECASE,
)
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


def post_refusal(post: dict, lift: str = "") -> str | None:
    """Why a post to his DM is over the length he reads, or None when it fits; a post answering his words asking for more, or confirming his ruling is installed, has a card's length."""
    if card := post.get("card"):
        return card_refusal(card, post.get("text") or "")
    text = post.get("text") or ""
    count = max(words(text), words(block_text(post.get("blocks") or [])))
    features = len(FEATURE.findall(text))
    if lift == "asked":
        limit, shape, rest = QUESTION_WORDS, "an answer to his own words asking for an explanation or a comparison", ANSWER_REST
    elif lift == "installed":
        limit, shape, rest = QUESTION_WORDS, "the installed confirmation of a ruling of his", CONFIRMATION_REST
    elif features >= 2:
        limit, shape, rest = SUMMARY_WORDS, f"a summary of {features} features", LINE_REST
    else:
        limit, shape, rest = LINE_WORDS, "a line in his thread", LINE_REST
    if count <= limit:
        return None
    return f"refused: this post is {count} words, and {shape} is at most {limit} words ({READING}). {rest}"


def card_refusal(card: dict, beside: str) -> str | None:
    options = card.get("options") or []
    long = [f'"{option}" is {words(option)} words' for option in options if words(option) > OPTION_WORDS]
    count = words(card.get("title") or "") + words(card.get("body") or "") + words(beside) + sum(map(words, options))
    problems = [f"each option is one line of at most {OPTION_WORDS} words, what he gets and what it costs: {', '.join(long)}"] if long else []
    if count > QUESTION_WORDS:
        problems.append(f"this card is {count} words, and a question with its options is at most {QUESTION_WORDS} words in all")
    if not problems:
        return None
    return f"refused: {'; '.join(problems)} ({READING})."


def question(text: str) -> Question:
    """The one question a job's ASK block puts to him, refused with every way it is out of shape."""
    asked, *findings = FINDINGS.split(text, maxsplit=1)
    lines = [line.strip() for line in asked.splitlines() if line.strip()]
    if not lines or not lines[0].startswith("ASK "):
        raise DriveError("an ASK block opens with `ASK <the question, one line>`")
    title, rest = lines[0].removeprefix("ASK ").strip(), lines[1:]
    first_option = next((index for index, line in enumerate(rest) if line.startswith("- ")), len(rest))
    summary, after = rest[:first_option], rest[first_option:]
    options = [line.removeprefix("- ").strip() for line in after if line.startswith("- ")]
    marked = [option for option in options if RECOMMENDED.search(option)]
    options = [RECOMMENDED.sub("", option) for option in options]
    problems = []
    if not 1 <= len(summary) <= SUMMARY_LINES:
        problems.append(
            f"the summary is {len(summary)} lines; it is one to three: what was found and what is being decided,"
            " the recommendation, what happens meanwhile"
        )
    if strays := [line for line in after if not line.startswith("- ")]:
        problems.append(f"only `- ` option lines follow the summary, then `Findings:`; these are neither: {strays}")
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
        problems.append(f"the question, summary and options are {count} words; at most {QUESTION_WORDS} in all")
    plain = URL.sub(" ", TOKEN.sub(" ", "\n".join(shown)))
    problems += [f'plain words only: "{found}" is {what}' for what, pattern in NOT_PLAIN for found in pattern.findall(plain)]
    problems += [
        f'"{found}" points at an option by number or position; cards renumber, so each option says its own words'
        for found in OPTION_POINTER.findall(plain)
    ]
    if len(prs := set(PR_NUMBER.findall(plain))) > 1:
        problems.append(f"it names {len(prs)} pull requests; name only the one being decided, by what it does")
    if not findings or not findings[0].strip():
        problems.append("no `Findings:` line with the evidence under it; it stays on disk, for the record and Explain more")
    if problems:
        raise DriveError("the ASK is out of shape, " + READING + ":\n- " + "\n- ".join(problems))
    return Question(title, summary, options, RECOMMENDED.sub("", marked[0]))
