import json
import re
import sys
import unicodedata
from typing import TextIO

from drive import state
from drive.github import GithubError, run
from drive.render import cut

FIELDS = "title,body,state,isDraft,headRefOid,statusCheckRollup,reviewDecision,url"
TAP_LABELS = ("Approve", "Hold")
TITLE_CHARS = 140
FIELD_CHARS = 400
DESCRIPTION_CHARS = 2800
KINDS = ("Fix", "New", "Cleanup", "Port")
CONVENTIONAL_KINDS = {
    "fix": "Fix",
    "feat": "New",
    **dict.fromkeys(("refactor", "chore", "docs", "test", "ci", "build", "perf"), "Cleanup"),
}
FAILED = {"FAILURE", "ERROR", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}
PASSED = {"SUCCESS", "NEUTRAL", "SKIPPED"}

KIND_WORD = re.compile(rf"({'|'.join(KINDS)}):\s*", re.IGNORECASE)
CONVENTIONAL = re.compile(r"([a-z]+)(?:\([^)]*\))?!?:\s*")
HEADING = re.compile(r"^#{1,2}[ \t]+(.+?)[ \t#]*$", re.MULTILINE)
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
IMAGE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)>?[^)]*\)|<img\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*>", re.IGNORECASE)
LIST_ITEM = re.compile(r"(?:[-*+]|\d+[.)])\s")
SENTENCE_END = re.compile(r"[.!?](?=\s|$)")
NOTHING = re.compile(r"(?:none|low)(?: risk)?[.!]?", re.IGNORECASE)
EMOJI_CATEGORIES = {"So", "Cf"}
EMOJI_MARKS = {chr(0xFE0E), chr(0xFE0F), chr(0x20E3), *map(chr, range(0x1F3FB, 0x1F400))}


def is_emoji(char: str) -> bool:
    return unicodedata.category(char) in EMOJI_CATEGORIES or char in EMOJI_MARKS


def without_emoji(text: str) -> str:
    return " ".join("".join(char for char in text if not is_emoji(char)).split())


def kind_and_title(title: str) -> tuple[str, str]:
    """The card kind of a PR title (Fix, New, Cleanup, Port or Change) and the title without its prefix or emoji."""
    plain = without_emoji(title)
    if word := KIND_WORD.match(plain):
        return word[1].capitalize(), without_emoji(plain[word.end() :])
    if prefix := CONVENTIONAL.match(plain):
        return CONVENTIONAL_KINDS.get(prefix[1], "Change"), without_emoji(plain[prefix.end() :])
    return "Change", plain


def framed(opening: str, plain: str, closing: str = "") -> str:
    return opening + cut(plain, TITLE_CHARS - len(opening) - len(closing)) + closing


def sections(body: str) -> dict[str, str]:
    parts = HEADING.split(body)
    found: dict[str, str] = {}
    for heading, text in zip(parts[1::2], parts[2::2], strict=True):
        found.setdefault(heading.strip().rstrip(":").lower(), text)
    return found


def first_paragraph(text: str) -> str:
    lines: list[str] = []
    for line in text.strip().splitlines():
        if not line.strip():
            break
        lines.append(line.strip())
    listed = all(LIST_ITEM.match(line) for line in lines)
    return ("\n" if listed else " ").join(lines)


def clipped(text: str, limit: int = FIELD_CHARS) -> str:
    if len(text) <= limit:
        return text
    ends = [end.end() for end in SENTENCE_END.finditer(text[: limit + 1]) if end.end() <= limit]
    return text[: ends[-1]] if ends else text[: limit - 1].rstrip() + "…"


def section_words(found: dict[str, str], *headings: str) -> str:
    return next((clipped(first_paragraph(found[heading])) for heading in headings if heading in found), "")


def unless_nothing(words: str) -> str:
    return "" if NOTHING.fullmatch(words.strip()) else words


def pictures(found: dict[str, str]) -> list[str]:
    image = IMAGE.search(found.get("show me", ""))
    return [image[1] or image[2]] if image else []


def description(body: str) -> str:
    text = body.strip()
    return text if len(text) <= DESCRIPTION_CHARS else text[: DESCRIPTION_CHARS - 1].rstrip() + "…"


def checks(rollup: list[dict] | None) -> str:
    outcomes = {check.get("conclusion") or check.get("state") or "" for check in rollup or []}
    if not outcomes:
        return "none"
    if outcomes & FAILED:
        return "fail"
    return "pass" if outcomes <= PASSED else "pending"


def tap_label(entry: dict) -> str | None:
    return (entry.get("tap") or {}).get("label")


def offers_approval(pr: dict, entry: dict) -> bool:
    return pr["state"] == "OPEN" and entry["wants_tap"] and tap_label(entry) != "Approve" and not entry.get("exempt")


def waits_on(pr: dict, entry: dict) -> str:
    approved = tap_label(entry) == "Approve"
    match pr["state"], checks(pr["statusCheckRollup"]):
        case "MERGED", _:
            return "Merged"
        case "CLOSED", _:
            return "Closed without merging"
        case _ if tap_label(entry) == "Hold":
            return "Held by you"
        case _ if pr["isDraft"]:
            return "Still a draft; the agent is finishing it"
        case _, "fail":
            return "CI failed; the agent is fixing it"
        case _ if pr["reviewDecision"] == "CHANGES_REQUESTED":
            return "A reviewer asked for changes; the agent is making them"
        case _, "pending":
            return "Lands once CI passes" if approved else "CI is still running"
        case _ if pr["reviewDecision"] == "REVIEW_REQUIRED" and not approved:
            return "Waits on a code review"
        case _:
            return "CI passed; the agent is landing it"


def lands_after_tap(pr: dict) -> str:
    return "you approve" if checks(pr["statusCheckRollup"]) == "pass" else "you approve and CI passes"


def merge_waits(url: str) -> str:
    repo, number = state.pr_key(url).split("#")
    return f"merge:{repo.replace('/', ':')}:{number}"


def render(pr: dict, entry: dict) -> dict:
    """The cc-slack PR card for a `gh pr view` payload and the drive's entry for that PR."""
    kind, plain = kind_and_title(pr["title"])
    body = COMMENT.sub("", pr["body"] or "")
    found = sections(body)
    row = {
        "url": pr["url"],
        "title": pr["title"],
        "head": pr["headRefOid"],
        "change": section_words(found, "what changes for you", "summary"),
        "eli5": section_words(found, "eli5", "in plain words", "context"),
        "show_me": clipped(first_paragraph(IMAGE.sub("", found.get("show me", "")))),
        "risk": unless_nothing(section_words(found, "risk", "risk profile")),
        "plan": section_words(found, "against the plan"),
        "port": section_words(found, "port"),
        "blocks": "",
        "lands": "",
        "description": description(body),
    }
    card = {"status": "info", "title": framed(f"{kind}: ", plain), "prs": [row]}
    if offers_approval(pr, entry):
        row["blocks"] = "Held by you" if tap_label(entry) == "Hold" else ""
        row["lands"] = lands_after_tap(pr)
        card |= {
            "status": "needs_you",
            "title": framed(f"{kind}: approve ", plain, "?"),
            "options": list(TAP_LABELS),
            "recommended": "Approve",
            "waits": merge_waits(pr["url"]),
        }
    else:
        row["blocks"] = waits_on(pr, entry)
        card["status"] = "done" if pr["state"] == "MERGED" else "info"
    if shown := pictures(found):
        card["pictures"] = shown
    return card


def drive_prs() -> dict[str, tuple[str, dict]]:
    return {key: (name, entry) for name in state.names() for key, entry in state.load(name)["prs"].items()}


def key_of(ask: dict) -> str:
    return f"{ask['repo']}#{ask['number']}"


def drawn(key: str, entry: dict) -> dict | None:
    repo, number = key.split("#")
    try:
        pr = json.loads(run(["gh", "pr", "view", number, "-R", repo, "--json", FIELDS]))
    except (GithubError, json.JSONDecodeError) as error:
        print(f"drive card: {key}: {error}", file=sys.stderr)
        return None
    return render(pr, entry)


def cards(asked: list[dict]) -> dict:
    known = drive_prs()
    found = []
    for ask in asked:
        key = key_of(ask)
        if key in known and (card := drawn(key, known[key][1])):
            found.append({"repo": ask["repo"], "number": ask["number"], "card": card})
    return {"cards": found}


def numbered(numbers: list[int]) -> dict:
    wanted = set(numbers)
    matches = []
    for key in sorted(drive_prs()):
        repo, number = key.split("#")
        if int(number) in wanted:
            matches.append({"repo": repo, "number": int(number)})
    return {"prs": matches}


def noted(kind: str, asks: list[dict]) -> dict:
    known = drive_prs()
    told = 0
    for ask in asks:
        if (key := key_of(ask)) in known:
            state.append_event(known[key][0], kind, **ask)
            told += 1
    return {kind: told}


def answered(ask: dict) -> dict:
    key = key_of(ask)
    known = drive_prs()
    if key not in known:
        raise state.DriveError(f"{key} is not a drive PR")
    if ask["label"] not in TAP_LABELS:
        raise state.DriveError(f"a PR card answers Approve or Hold, not {ask['label']!r}")
    name = known[key][0]
    with state.edit(name) as data:
        entry = data["prs"][key]
        if entry["state"] == "open":
            entry["tap"] = {"label": ask["label"], "at": state.stamp(), "head": entry["head"], "source": ask["source"]}
            entry["updated"] = entry["tap"]["at"]
            state.append_event(
                name,
                "tap",
                repo=ask["repo"],
                number=ask["number"],
                label=ask["label"],
                head=entry["head"],
                source=ask["source"],
                action_ts=ask["action_ts"],
            )
    return {"ok": True}


def home_drive() -> str:
    try:
        return state.resolve()
    except state.DriveError:
        if not (drives := state.names()):
            raise
        return max(drives, key=lambda name: state.load(name)["driver"]["started"])


def unheard(ask: dict) -> dict:
    state.append_event(home_drive(), "unheard", **ask)
    return {"heard": True}


def respond(request: dict) -> dict:
    match request:
        case {"prs": asked}:
            return cards(asked)
        case {"numbers": numbers}:
            return numbered(numbers)
        case {"shown": asks}:
            return noted("shown", asks)
        case {"refused": asks}:
            return noted("refused", asks)
        case {"answer": ask}:
            return answered(ask)
        case {"unheard": ask}:
            return unheard(ask)
    raise state.DriveError(f"not a card request: {', '.join(request) or 'empty'}")


def main(stdin: TextIO, stdout: TextIO) -> int:
    """Answer one cc-slack card_command request read from stdin with one JSON reply on stdout."""
    json.dump(respond(json.load(stdin)), stdout)
    stdout.write("\n")
    return 0
