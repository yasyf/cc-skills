import re
from datetime import UTC, datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

PACIFIC = ZoneInfo("America/Los_Angeles")
FINDING = re.compile(r"^\d+:\d+ ([\w-]+) (['\"])(.*?)\2: ")
STAMP = re.compile(r"`?\b(?:(\d{4}-\d{2}-\d{2})[T ])?(\d{1,2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(?:Z| ?\(?UTC\)?| ?GMT)(?![\w])`?")
ZONE = re.compile(r" ?\(?\b(?:UTC|GMT)\b\)?")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z*_<`\"(])")
TIMES = {"machine-time", "zone-label"}
MOST_SENTENCES = 3


def kinds(findings: list[str]) -> dict[str, list[str]]:
    """Each kind of `cc-slack check-post` finding, `<line>:<column> <kind> '<token>': <fix>`, with the tokens it names."""
    found: dict[str, list[str]] = {}
    for finding in findings:
        if parsed := FINDING.match(finding):
            found.setdefault(parsed[1], []).append(parsed[3])
    return found


def pacific(match: re.Match[str], moment: datetime) -> str:
    """A UTC stamp as the team reads it: its Pacific clock time, with its day when that is not today in Pacific."""
    day = datetime.strptime(match[1], "%Y-%m-%d").date() if match[1] else moment.astimezone(UTC).date()
    at = datetime(day.year, day.month, day.day, int(match[2]), int(match[3]), int(match[4] or 0), tzinfo=UTC).astimezone(PACIFIC)
    clock = at.strftime("%-I:%M %p")
    return clock if at.date() == moment.astimezone(PACIFIC).date() else f"{at.strftime('%b %-d')}, {clock}"


def label(url: str) -> str:
    """Words saying what a bare link opens, read from its address alone."""
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    host = parsed.netloc.removeprefix("www.")
    if host == "github.com" and len(parts) >= 4 and parts[2] in ("pull", "issues"):
        return f"{'PR' if parts[2] == 'pull' else 'issue'} #{parts[3]}"
    if host == "github.com" and len(parts) >= 4 and parts[2] == "commit":
        return f"commit {parts[3][:7]}"
    if host == "github.com" and len(parts) >= 5 and parts[2] == "actions" and parts[3] == "runs":
        return f"CI run {parts[4]}"
    if host.endswith("buildkite.com") and "builds" in parts and parts.index("builds") + 1 < len(parts):
        return f"{parts[parts.index('builds') - 1]} build {parts[parts.index('builds') + 1]}"
    if host.endswith("slack.com") and "archives" in parts:
        return "the thread"
    if host.endswith(("datadoghq.com", "sentry.io")):
        return f"the {host.split('.')[-2].capitalize()} page"
    return f"the {host} page"


def wrapped(text: str, url: str) -> str:
    """The text with each bare use of url linked on words saying what it opens, leaving one already inside a Slack link."""
    return re.sub(rf"(?<![<|]){re.escape(url)}(?![|>])", f"<{url}|{label(url)}>", text)


def split(paragraph: str) -> str:
    """A paragraph of prose cut into paragraphs of at most three sentences; a list, quote or code block stays as it is."""
    if paragraph.lstrip().startswith(("•", "-", "*", ">", "```", "|")) or "\n" in paragraph.strip():
        return paragraph
    sentences = SENTENCE_END.split(paragraph.strip())
    return "\n\n".join(" ".join(sentences[start : start + MOST_SENTENCES]) for start in range(0, len(sentences), MOST_SENTENCES))


def mended(text: str, findings: list[str], moment: datetime) -> str:
    """The post with each `cc-slack check-post` finding whose fix is mechanical made, so it goes out in one pass: a log's UTC stamp and a zone label as the Pacific time with no label, a bare link on words saying what it opens, and a paragraph over three sentences cut into parts. Every other finding is left for the writer."""
    found = kinds(findings)
    if TIMES & found.keys():
        text = ZONE.sub("", STAMP.sub(lambda match: pacific(match, moment), text))
    for url in dict.fromkeys(found.get("bare-url", [])):
        text = wrapped(text, url)
    if "wall-of-text" in found:
        text = "\n\n".join(split(paragraph) for paragraph in text.split("\n\n"))
    return text
