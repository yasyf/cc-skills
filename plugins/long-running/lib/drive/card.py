import json
import os
import re
import subprocess
import sys
import unicodedata
import uuid
from functools import lru_cache
from pathlib import Path
from typing import TextIO

from drive import github, limits, owing, picture, providers, state
from drive.github import GithubError, run
from drive.pr_graph import chains
from drive.render import cut, raised, when

FIELDS = (
    "title body author { __typename login } state isDraft headRefOid baseRefName headRefName reviewDecision labels(first: 100) { nodes { name } } url additions deletions changedFiles"
    " files(first: 100) { nodes { path } } comments(last: 100) { nodes { body } }"
)
READ_AGE = "60s"
KEPT_READS = "pr-reads"
RECORDS = "card-records"
RECORD_LOG = "cards.log"
REVIEW_DECISIONS = {"approved": "APPROVED", "stale": "REVIEW_REQUIRED", "changes": "CHANGES_REQUESTED", "none": ""}
TAP_LABELS = ("Approve", "Hold")
RELEASE_TAP_SECONDS = 90
RELEASE_UNDONE = "his {tap} on a release approve card stands: the release tool already took it, so an Undo takes nothing back; tell him to say what the release should do now"
RELEASE_WORDS = "his words on a release approve card approve and hold nothing; only his Approve or Hold tap runs the release tool, so answer his words in the card's thread"
UNDONE = "Undone: {label}. Never given; the card waits for his answer."
UNREOPENED = (
    "his Undo on {card} reopens nothing: no question drawn on that card ended answered on his tap there or his words at or"
    " under it, so no answer of his stands there to take back; nothing recorded"
)
UNRECORDED_TAPS = "unrecorded-taps"
UNRECORDED = "drive card: his answer {label!r} on {url} ({waits}) is still not recorded after cc-slack ran the card command again: {failure}; cc-slack runs it again every two minutes until it records"
TITLE_CHARS = 140
FIELD_CHARS = 400
CAPTION_CHARS = 160
CODE_CHARS = 900
DESCRIPTION_CHARS = 2800
KINDS = ("Fix", "New", "Cleanup", "Port")
STACK_KINDS = ("New", "Fix", "Port", "Cleanup")
REFRESHER_HEADINGS = ("refresher", "eli5", "in plain words", "context")
STACK_HEADING = "the stack"
STACK_CHARS = 700
LEAD_LABELS = (("why", "Why this exists"), ("what", "What changes"), ("downstream", "Downstream"))
REVIEWED = {"APPROVED": "approved", "CHANGES_REQUESTED": "asked for changes on", "COMMENTED": "commented on"}
HATE_HEADINGS = ("what you'll hate most", "what you will hate most", "what you'll hate the most", "what you will hate the most")
PLAN_HEADINGS = ("against the plan", "differs from the plan", "plan deviations")
IMPACT_HEADINGS = ("fde impact", "fde impact scan")
STORED_HEADING = re.compile(r"migrat|\bhow (?:the )?old\b|data[- ]location|stored data")
SHOWN_HEADINGS = ("summary", *IMPACT_HEADINGS, *REFRESHER_HEADINGS, "biggest surprise", *HATE_HEADINGS, "show me", *PLAN_HEADINGS, "port", STACK_HEADING)
CONVENTIONAL_KINDS = {
    "fix": "Fix",
    "feat": "New",
    **dict.fromkeys(("refactor", "chore", "docs", "test", "ci", "build", "perf"), "Cleanup"),
}
GITMOJI_KINDS = {
    **dict.fromkeys("✨🎉🚸🚀💄📈🌐💥🍱♿💬🗃🔊🏗📱🥚⚗🔍🌱🚩💫🛂👔🩺🧱💸🧵🦺✈🦖🚧🧐💩🍻", "New"),
    **dict.fromkeys("🐛🚑🩹🔒💚🚨✏⏪👽🥅", "Fix"),
    **dict.fromkeys("♻🔥⚡📝✅🔧🏷🚚🧹👷📌⬆🎨🔐🔖⬇➕➖🔨🔀📦📄💡🔇👥🤡🙈📸🗑⚰🧪🧑", "Cleanup"),
}

KIND_WORD = re.compile(rf"({'|'.join(KINDS)}):\s*", re.IGNORECASE)
CONVENTIONAL = re.compile(r"([a-z]+)(?:\([^)]*\))?!?:\s*")
SCOPED = re.compile(r"([a-z0-9][a-z0-9_./-]*):\s*(\S)")
HEADING = re.compile(r"#{1,2}[ \t]+(.+?)[ \t#]*")
REFRESHER_HEADING = re.compile(r"^#{2,3}[ \t]+(?:Refresher|ELI5)[ \t]*$", re.MULTILINE | re.IGNORECASE)
NEXT_HEADING = re.compile(r"^#{1,3}[ \t]", re.MULTILINE)
FENCE_LINE = re.compile(r"[ \t]*```")
TABLE = re.compile(r"^[ \t]*\|.*(?:\n[ \t]*\|.*)*", re.MULTILINE)
NOT_NEWLINE = re.compile(r"[^\n]")
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
CODE = re.compile(r"^[ \t]*```.*?^[ \t]*```|`[^`\n]*`|https?://\S+", re.DOTALL | re.MULTILINE)
TRAILER = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+:[ \t]+\S.*")
HTML_TAG = re.compile(
    r"</?(?:a|b|br|code|del|details|div|em|h[1-6]|hr|i|img|ins|kbd|li|ol|p|picture|pre|source|span|strong|sub|summary|sup|table|tbody|td|th|thead|tr|ul)(?:\s[^<>]*)?/?>",
    re.IGNORECASE,
)
LIST_ITEM = re.compile(r"(?:[-*+]|\d+[.)])\s")
SENTENCE_END = re.compile(r"[.!?](?=\s|$)")
SENTENCES = re.compile(r"(?<=[.!?])\s+")
PARENTHETICAL = re.compile(r"\s*\([^()\n]*\)")
NOTHING = re.compile(
    r"(?:none|nothing(?: differ(?:s|ed))?|no (?:departures?|deviations?)(?: from the plan)?|n/a)[.!]?", re.IGNORECASE
)
RECORD_ID = re.compile(
    r"\b(?:cc-notes|ccn|cci)\s+(?:task|note|answer|doc|plan|log|record|seq|row)s?\b"
    r"|\bcci\s+#?\d+"
    r"|\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b"
    r"|(?<![\d,.$#])\b\d{6,}\b(?![,.]\d)"
    r"|\b[ojm]\d{1,5}\b"
    r"|\brmw[0-9a-z]{4,}\b|\bcard-[0-9a-z][0-9a-z-]*"
)
REWORKED = re.compile(r'(?P<opening>Reworked to your words ".*?")(?P<change>: .*)', re.DOTALL)
HIS = re.compile(r"\bhis\b")
HIM = re.compile(r"\bhim\b")
Member = tuple[dict, dict, tuple[str, ...], tuple[str, ...]]
REDRAWN = ("head", "staging")
EMOJI_CATEGORIES = {"So", "Cf"}
EMOJI_MARKS = {chr(0xFE0E), chr(0xFE0F), chr(0x20E3), *map(chr, range(0x1F3FB, 0x1F400))}


class Pasted(str):
    """Words an agent wrote, such as a tester's WAITING line or a builder's CHANGE line, pasted into a row his PR card fills from the drive's state rather than the PR's body or lead."""


def plain_row(parts: tuple[str, ...]) -> str:
    """A row his PR card fills from the drive's state, its parts joined, each Pasted part only while it reads as one plain line by limits.line_problems and names no record id, so the row draws as its fixed opening alone rather than paste him words written for agents, by his words "This is incomprehensible nonsense why are you not following your strict formatting and writing rules"."""
    return "".join(part for part in parts if not isinstance(part, Pasted) or not (limits.line_problems(part) or RECORD_ID.search(part)))


def changed_row(fundamental: str) -> tuple[str, ...]:
    """What changed since his answer as the drive keeps it, by drive pr set --fundamental or a builder's READY on a PR reworked to his words: his words quoted as the fixed opening of a rework, the rest Pasted."""
    if reworked := REWORKED.fullmatch(fundamental):
        return reworked["opening"], Pasted(reworked["change"])
    return (Pasted(fundamental),)


def is_emoji(char: str) -> bool:
    return unicodedata.category(char) in EMOJI_CATEGORIES or char in EMOJI_MARKS


def without_emoji(text: str) -> str:
    return " ".join("".join(char for char in text if not is_emoji(char)).split())


@lru_cache(maxsize=4096)
def kind_and_title(title: str) -> tuple[str, str]:
    """The card kind of a PR title (Fix, New, Cleanup, Port or Change) and the title without its prefix or emoji."""
    plain = without_emoji(title)
    if word := KIND_WORD.match(plain):
        return word[1].capitalize(), without_emoji(plain[word.end() :])
    if (scoped := SCOPED.match(title)) and is_emoji(scoped[2]):
        rest = without_emoji(title[scoped.end(1) + 1 :])
        if word := KIND_WORD.match(rest):
            return word[1].capitalize(), rest[word.end() :]
        return GITMOJI_KINDS.get(scoped[2], "Change"), rest
    if prefix := CONVENTIONAL.match(plain):
        return CONVENTIONAL_KINDS.get(prefix[1], "Change"), without_emoji(plain[prefix.end() :])
    return "Change", plain


def framed(opening: str, plain: str, closing: str = "") -> str:
    return opening + cut(plain, TITLE_CHARS - len(opening) - len(closing)) + closing


def titled(card: dict, pr_title: str) -> dict:
    """The card with a title of 1 to TITLE_CHARS characters, counted in UTF-16 units as Slack counts them, since cc-slack refuses a card titled otherwise and draws none, as #53302 drew none: a longer one cut to fit, an empty one its PR's title without its kind prefix or emoji, each fix said on stderr with the title it was built with."""
    title = " ".join(card["title"].split())
    if 0 < slack_length(title) <= TITLE_CHARS:
        return card
    fixed = title or kind_and_title(pr_title)[1] or pr_title
    while slack_length(fixed := cut(fixed, TITLE_CHARS)) > TITLE_CHARS:
        fixed = fixed[:-2]
    print(f"drive card: built a title of {slack_length(title)} characters, {title!r}, outside 1 to {TITLE_CHARS}; drawn as {fixed!r}", file=sys.stderr)
    return card | {"title": fixed}


def slack_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def heading_key(heading: str) -> str:
    return heading.strip().rstrip(":").lower().replace("\u2019", "'")


def sections(body: str) -> dict[str, str]:
    """Each level-one or level-two heading of a PR body, lowercased, mapped to its text; a # inside a code fence is code."""
    found: dict[str, str] = {}
    heading: str | None = None
    lines: list[str] = []
    fenced = False
    for line in body.splitlines():
        if FENCE_LINE.match(line):
            fenced = not fenced
        elif not fenced and (match := HEADING.fullmatch(line)):
            if heading is not None:
                found.setdefault(heading, "\n".join(lines))
            heading, lines = heading_key(match[1]), []
            continue
        lines.append(line)
    if heading is not None:
        found.setdefault(heading, "\n".join(lines))
    return found


def without_records(text: str) -> str:
    """The text with every parenthesis naming a record id removed, then every sentence still naming one."""
    text = PARENTHETICAL.sub(lambda match: "" if RECORD_ID.search(match[0]) else match[0], text)
    kept = []
    for line in text.splitlines():
        if not line.strip():
            kept.append("")
        elif said := " ".join(part for part in SENTENCES.split(line) if not RECORD_ID.search(part)).strip():
            kept.append(said)
    return "\n".join(kept)


def without_html(text: str) -> str:
    """The text with each HTML tag GitHub draws removed outside its code, so a placeholder such as `agent::of("<id>")` reaches his card whole."""
    kept, at = [], 0
    for code in CODE.finditer(text):
        kept += [HTML_TAG.sub("", text[at : code.start()]), code[0]]
        at = code.end()
    return "".join(kept) + HTML_TAG.sub("", text[at:])


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
    text = next((found[heading] for heading in headings if heading in found), "")
    words = clipped(first_paragraph(without_records(text)))
    return "" if NOTHING.fullmatch(words.strip()) else words


def comment_refresher(pr: dict) -> str:
    """The Refresher, or older ELI5, section of the newest PR comment that has one, such as a reviewer bot's companion comment."""
    for comment in reversed(pr.get("comments") or []):
        body = comment.get("body") or ""
        if heading := REFRESHER_HEADING.search(body):
            rest = body[heading.end() :]
            end = NEXT_HEADING.search(rest)
            return clipped(first_paragraph(without_records(without_html(rest[: end.start() if end else None]))))
    return ""


def show_me_words(text: str, pictured: bool, charts: int) -> str:
    """A Show me's words on his card, a blank line between each: one short caption, each markdown table and at most charts ```chart fences as written, which cc-slack draws as Slack's own data table and chart, its first code block when no picture draws, and each code fence whose picture would illustrate nothing, by his pick 29865ea, as Slack's code block under the caption the picture would have carried."""
    bare = picture.FENCE.sub("", picture.IMAGE.sub("", text))
    first = SENTENCES.split(first_paragraph(without_records(bare)), maxsplit=1)[0].strip()
    readable = first and not first.startswith("|") and "`" not in first and len(first) <= CAPTION_CHARS
    shown = [first] if readable else []
    shown += natives(text, charts)
    code = [match for match in picture.CAPTIONED.finditer(text) if match["language"].lower() not in picture.DIAGRAMS and not picture.raw(match["language"], match["source"])]
    for index, match in enumerate(code):
        plain = picture.drawable(match["language"], match["source"]) and not picture.illustrative(match["language"], match["source"])
        if plain or not (index or pictured):
            caption = match["caption"] if plain and match["caption"] != first.rstrip(":") else None
            shown.append("\n".join([*filter(None, [caption]), "```", code_clipped(match["source"].strip("\n")), "```"]))
    return "\n\n".join(shown)


def natives(text: str, charts: int) -> list[str]:
    """Each markdown table outside a fence and each ```chart fence of a Show me as written, in the order they come, a chart only when Slack draws it and only the first charts of them, since cc-slack refuses a whole card over one it cannot draw or past the charts one message holds."""
    drawable = [fence for fence in picture.FENCE.finditer(text) if fence[1].lower() in picture.NATIVE and limits.json_problem("chart", fence[2]) is None and not limits.chart_limits(json.loads(fence[2]))]
    kept = [(fence.start(), fence[0]) for fence in drawable[:charts]]
    unfenced = picture.FENCE.sub(lambda fence: NOT_NEWLINE.sub(" ", fence[0]), text)
    return [block for _, block in sorted(kept + [(table.start(), table[0]) for table in TABLE.finditer(unfenced)])]


def code_clipped(code: str) -> str:
    if len(code) <= CODE_CHARS:
        return code
    end = code.rfind("\n", 0, CODE_CHARS)
    return code[: end if end > 0 else CODE_CHARS].rstrip() + "\n…"


def description(body: str) -> str:
    text = body.strip()
    return text if len(text) <= DESCRIPTION_CHARS else text[: DESCRIPTION_CHARS - 1].rstrip() + "…"


def checks(pr: dict, entry: dict) -> str:
    """The PR's checks as the drive's watch last read them, since ccx never caches a check read; pending for a head the watch has not read yet."""
    return entry["checks"] if entry["head"] == pr["headRefOid"] else "pending"


def tap_label(entry: dict) -> str | None:
    return (entry.get("tap") or {}).get("label")


def tap_refused(pr: dict, entry: dict) -> str | None:
    """Why his card on the PR offers no Approve, by GitHub's read of it now and by the entry's tap_refused, which render's callers give it from state.tap_refused; None when it does."""
    if pr["state"] == "FOLDED":
        return github.fold_line(entry)
    if pr["state"] != "OPEN":
        return f"GitHub reads it {pr['state'].lower()}"
    return entry["tap_refused"]


def standing(pr: dict, entry: dict) -> dict[str, str]:
    """The dashboard's row lines on the PR's landing: lands, what it lands once while his Approve lands it, else blocks, what holds it, his Approve first when he gave it."""
    if tap_refused(pr, entry) is None:
        return {"lands": lands_after_tap(pr, entry), "blocks": ""}
    return {"lands": "", "blocks": ("Approved by you. " if tap_label(entry) == "Approve" and pr["state"] == "OPEN" else "") + waits_on(pr, entry)}


def waits_on(pr: dict, entry: dict) -> str:
    """What the dashboard says under the PR: merged, folded into another open PR's branch or closed as read does, else its stage as stage.of names it, in his card's words; under his Hold or the hold label, which stop only its landing, that hold first, whatever its stage, then what its builder does while it is not green."""
    by = "Held by you" if tap_label(entry) == "Hold" else "Held by the hold label on GitHub" if entry["hold_label"] else ""
    if not by or pr["state"] != "OPEN":
        return doing(pr, entry)
    return by if entry["now"].name == "held" else f"{by}. {doing(pr, entry)}"


def doing(pr: dict, entry: dict) -> str:
    now, approved = entry["now"], tap_label(entry) == "Approve"
    match pr["state"], now.name:
        case "MERGED", _:
            return "Merged"
        case "FOLDED", _:
            return f"Not landed: {github.fold_line(entry)}"
        case "CLOSED", _:
            return "Closed without merging"
        case _, "red" if entry["checks"] == "fail":
            return "CI failed; the agent is fixing it"
        case _, "red" if entry["draft"]:
            return "Still a draft; the agent is finishing it"
        case _, "red" if entry["review"] == "changes":
            return "A reviewer asked for changes; the agent is making them"
        case _, "red" if not entry["conflict"]:
            return "Waits on a code review"
        case _, "grading" if not approved:
            return "CI is still running"
        case _, "grading":
            return "Lands once CI passes"
        case _, "ready" | "landing" if entry["queued"]:
            return "CI passed; the drive queues it for merge"
        case _, "ready" | "landing" | "author":
            return "CI passed; the agent is landing it"
    said = HIM.sub("you", HIS.sub("your", now.why))
    return said[:1].upper() + said[1:]


def lands_after_tap(pr: dict, entry: dict) -> str:
    """What the dashboard says the PR lands once: his Approve, lifting his Hold while he holds it, CI when it has not passed and the hold label coming off it on GitHub while it carries one, alone while each PR above it in its stack is being reworked to his words."""
    approve = f"you approve, lifting your Hold of {when(entry['tap']['at'])}" if tap_label(entry) == "Hold" else "you approve"
    lands = in_turn([approve, *([] if checks(pr, entry) == "pass" else ["CI passes"]), *(["the hold label comes off on GitHub"] if entry["hold_label"] else [])])
    above = entry["reworked_above"]
    return lands + (f", alone while {in_turn(list(above))} above it {'is' if len(above) == 1 else 'are'} reworked to your words" if above else "")


def in_turn(steps: list[str]) -> str:
    return " and ".join(steps) if len(steps) < 3 else ", ".join(steps[:-1]) + " and " + steps[-1]


def ask_waits(asked: list[tuple[dict, dict]]) -> str:
    """The waits id his PR card asks under, from the `gh pr view` payload and the drive's entry of each PR on it still asking his tap: their numbers and the hash of the heads they are asked at, each one he held or approved beside the time of that tap, an approved one asking only while land.awaits_word finds it waiting on his presence, inside the 64 characters cc-slack takes, a stack too long to name each number within them named by its bottom PR and its count beside the hash of its numbers and heads, as state.LONG_ASK_WAITS reads, so his answer to one ask never closes a later one: a push that takes his Approve back asks at a new head, and his Hold, or his Approve on a dangerous PR held while he is away, asks his Approve again, which cc-slack opens the answered card for again in place under the new id."""
    numbers = ".".join(state.pr_key(pr["url"]).split("#")[1] for pr, _ in asked)
    heads = [f"{pr['headRefOid']}@{entry['tap']['at']}" if tap_label(entry) else pr["headRefOid"] for pr, entry in asked]
    waits = f"merge:{numbers}:{state.ask_hash(' '.join(heads))}"
    return waits if len(waits) <= state.WAITS_MAX else f"merge:{numbers.split('.')[0]}-{len(asked)}:{state.ask_hash(' '.join([numbers, *heads]))}"


def body_of(pr: dict) -> str:
    body = COMMENT.sub("", pr["body"] or "").replace("\r\n", "\n").rstrip()
    rest, _, last = body.rpartition("\n\n")
    return rest if rest and all(TRAILER.fullmatch(line.strip()) for line in last.splitlines()) else body


def headline(entry: dict) -> str:
    """The line his card on the PR opens with, as its builder gave drive pr add, with no kind word or count, his words on such a title being "useless title, NEW, 2 PRs all useless info, and its cut off"; none for a PR kept without one."""
    return (entry.get("lead") or {}).get("headline") or ""


def lead_words(entry: dict) -> str:
    """Each teammate's GitHub review the PR waited on before his card drew, first, then why the PR is made, what it does and what it changes downstream, one labelled line each, as its builder gave drive pr add; none for a PR kept without them."""
    lead = entry.get("lead") or {}
    reviewed = [f"**Reviewed by {review['login']}:** {REVIEWED[review['state']]} {review['head'][:7]}" for review in entry["reviewed"]]
    return "\n".join([*reviewed, *(f"**{label}:** {lead[key]}" for key, label in LEAD_LABELS if lead.get(key))])


def render(pr: dict, entry: dict, shown: tuple[str, ...] = (), undrawn: tuple[str, ...] = (), charts: int = limits.CHARTS) -> dict:
    """The cc-slack PR card for a `gh pr view` payload, the drive's entry for that PR, its drawn Show me pictures, why each Show me picture left off it does not draw, and how many ```chart fences its Show me may still draw on the card. It opens with the PR's lead, its headline alone as the title and its why, what and downstream as the body above the PR's own sections; a PR kept with no lead is titled from its own title as before. A flake fix asking his tap says under its body that it fixes a flaking test at its cause, by his ruling FLAKE_RULING, its words quoted."""
    kind, plain = kind_and_title(pr["title"])
    body = body_of(pr)
    found = {heading: without_html(text) for heading, text in sections(body).items()}
    row = {
        "url": pr["url"],
        "title": plain,
        "head": pr["headRefOid"],
        "staging": plain_row(entry["staging"]),
        "changed": plain_row(changed_row(entry.get("fundamental") or "")),
        "summary": section_words(found, "summary"),
        "impact": section_words(found, *IMPACT_HEADINGS),
        "refresher": section_words(found, *REFRESHER_HEADINGS) or comment_refresher(pr),
        "stored_data": stored_words(found) or "".join(f"Not said yet: {why}. The agent is fixing it." for why in unsaid_storage(pr, entry)),
        "surprise": section_words(found, "biggest surprise"),
        "hate_most": section_words(found, *HATE_HEADINGS),
        "show_me": "\n\n".join(filter(None, [show_me_words(found.get("show me", ""), bool(shown), charts), left_off(undrawn)])),
        "pictures": list(shown),
        "plan": section_words(found, *PLAN_HEADINGS),
        "port": section_words(found, "port"),
        "description": description(body),
    }
    card = {"status": "info", "title": headline(entry) or framed(f"{kind}: ", plain), "prs": [row]}
    if lead := lead_words(entry):
        card["body"] = lead
    if tap_refused(pr, entry) is None:
        card |= {
            "status": "needs_you",
            "title": headline(entry) or framed(f"{kind}: approve ", plain, "?"),
            "options": list(TAP_LABELS),
            "recommended": "Approve",
            "waits": ask_waits([(pr, entry)]),
        }
        if entry.get("exempt") == state.FLAKE_FIX:
            card["body"] = "\n\n".join(filter(None, [card.get("body"), f'Fixes a flaking test at its cause, by your ruling "{state.FLAKE_WORDS}"']))
    else:
        card["status"] = "done" if pr["state"] == "MERGED" else "info"
    if shown:
        card["pictures"] = list(shown)
    return card


def stored_words(found: dict[str, str]) -> str:
    """What the PR body says of how the data stored before it moves and what keeps that from breaking, from its first section headed so, such as `## How old data moves`, `## How the old rows move` or `## Migration risk`."""
    return next((words for heading in found if STORED_HEADING.search(heading) and (words := section_words(found, heading))), "")


def unsaid_storage(pr: dict, entry: dict) -> tuple[str, ...]:
    """Why his card on the PR cannot say how its stored data moves: it changes a migration, schema or wire format, by land.stored over the files GitHub's read of it lists, or, for a PR changing more files than that read lists, over every file the drive's last read of its changed files found, kept on its entry by state.mark_dangerous, so no card waits on a read of each PR's files; and its body never says how the data stored before it moves; none when it says or stores nothing differently. His words under the card of a PR moving stored rows: "How will you migrate the old ones / avoid breaking anything?"."""
    from drive import land

    listed = [file["path"] for file in pr["files"]]
    if stored_words(sections(body_of(pr))) or not (paths := land.stored(listed) if pr["changedFiles"] <= len(listed) else entry["stored"]):
        return ()
    return (f"it changes {', '.join(paths)}, and no `## How old data moves` section of its body says how the data stored before it moves or what keeps that from breaking",)


def left_off(undrawn: tuple[str, ...]) -> str:
    """The one line a card's Show me names each picture left off it with, and why."""
    return f"Left off this card: {'; '.join(why.rstrip('.') for why in undrawn)}. The agent is fixing it." if undrawn else ""


def stack_words(pr: dict) -> tuple[str, str]:
    """The stack's one-line title and its summary, from the `## The stack` section of its bottom PR's body."""
    text = without_records(without_html(sections(body_of(pr)).get(STACK_HEADING, ""))).strip()
    title, _, rest = text.partition("\n")
    return title.strip(), clipped(" ".join(rest.split()), STACK_CHARS)


def render_stack(members: list[Member], siblings: bool) -> dict:
    """One card for a stack of PRs, bottom first, or for sibling PRs of one request, which never sit on each other: a stack's title and summary from its bottom PR, siblings titled as that many PRs of one request and marked siblings, so cc-slack never words them a stack, the stack's title alone or else the bottom PR's headline once the bottom PR has a lead, each PR's lead above the summary under its headline, a section per PR, and one Approve for the PRs still asking his tap, its PRs' Show me charts at most the ones one Slack message draws, since cc-slack posts the whole card as one."""
    drawn, charts = [], limits.CHARTS
    for member in members:
        drawn.append(one := render(*member, charts=charts))
        charts -= sum(fence[1].lower() in picture.NATIVE for fence in picture.FENCE.finditer(one["prs"][0]["show_me"]))
    kinds = [kind_and_title(member[0]["title"]) for member in members]
    kind = next((each for each in STACK_KINDS if any(each == found for found, _ in kinds)), "Change")
    title, summary = ("", "") if siblings else stack_words(members[0][0])
    count = f"{len(members)} PRs of one request" if siblings else f"{len(members)} PRs"
    if led := headline(members[0][1]):
        named = framed("", title or led)
    else:
        named = framed(f"{kind}: ", title) if title else framed(f"{kind}: {count}: ", kinds[0][1])
    card = {"status": "info", "title": named, "prs": [one["prs"][0] for one in drawn]}
    if siblings:
        card["siblings"] = True
    leads = [f"**{headline(entry) or plain}**\n{lead}" for (_, entry, *_), (_, plain) in zip(members, kinds, strict=True) if (lead := lead_words(entry))]
    if body := "\n\n".join([*leads, *([summary] if summary else [])]):
        card["body"] = body
    if any(one["status"] == "needs_you" for one in drawn):
        card |= {
            "status": "needs_you",
            "options": list(TAP_LABELS),
            "recommended": "Approve",
            "waits": ask_waits([(member[0], member[1]) for member, one in zip(members, drawn, strict=True) if one["status"] == "needs_you"]),
        }
    elif all(one["status"] == "done" for one in drawn):
        card["status"] = "done"
    if shown := [path for one in drawn for path in one.get("pictures", [])]:
        card["pictures"] = shown
    return card


def drive_prs() -> dict[str, tuple[str, dict]]:
    return tracked({name: state.load(name) for name in state.names()})


def tracked(drives: dict[str, dict]) -> dict[str, tuple[str, dict]]:
    """Each PR the drives track, with the drive whose entry for it was updated last, since a drive copied from another keeps the older copy of each entry."""
    known: dict[str, tuple[str, dict]] = {}
    for name, data in drives.items():
        for key, entry in data["prs"].items():
            if key not in known or state.parse_time(entry["updated"]) > state.parse_time(known[key][1]["updated"]):
                known[key] = (name, entry)
    return known


def keys_of(asks: list[dict], known: dict[str, tuple[str, dict]]) -> list[str]:
    folded = {key.casefold(): key for key in known}
    asked = [f"{ask['repo']}#{ask['number']}" for ask in asks]
    return [folded.get(key.casefold(), key) for key in asked]


def skip(key: str, reason: str) -> dict:
    print(f"drive card: {key}: {reason}", file=sys.stderr)
    repo, number = key.split("#")
    return {"repo": repo, "number": int(number), "reason": reason}


def read(key: str, entry: dict | None = None) -> dict | None:
    """One PR's card fields with every file it changes, completing large lists through github.files."""
    if not (pr := github_reads({key: entry}, fresh=True).get(key)):
        return None
    return pr | {"files": [{"path": path} for path in github.files(key)]} if pr["changedFiles"] > len(pr["files"]) else pr


def github_reads(entries: dict[str, dict | None], fresh: bool = False) -> dict[str, dict]:
    """Read card fields in repository batches, reusing each PR for READ_AGE regardless of batch composition. Undrawn pictures force fresh reads; refused reads retain the last saved card with the drive's state and head."""
    found = {}

    def read(keys: list[str]) -> dict[str, dict]:
        return github.nodes(json.loads(run(["gh", "api", "graphql", "-f", f"query={github.query(keys, FIELDS)}"], max_age="0s")), keys)

    def current(key: str, node: dict) -> bool:
        entry = entries[key]
        return not entry or (not entry.get("head") or entry["head"] == node["headRefOid"]) and (entry["state"] == github.FOLDED or entry["state"] == node["state"].lower())

    for chunk in github.by_repository(list(entries), github.PRS_PER_QUERY):
        uncached = fresh or any((entries[key] or {}).get("undrawn") for key in chunk)
        try:
            answered = github.cached_nodes(chunk, FIELDS, 0 if uncached else int(READ_AGE.removesuffix("s")), read, current)
            prs = {key: card_fields(node) for key, node in answered.items()}
        except (GithubError, json.JSONDecodeError) as error:
            print(f"drive card: {', '.join(chunk)}: {error}", file=sys.stderr)
            prs = {}
        for key in chunk:
            repo, number = key.split("#")
            kept, entry = state.root() / KEPT_READS / repo / f"{number}.json", entries[key]
            if pr := prs.get(key):
                body = json.dumps(pr)
                if not kept.is_file() or kept.read_text() != body:
                    kept.parent.mkdir(parents=True, exist_ok=True)
                    written = kept.with_name(f"{kept.name}.{os.getpid()}")
                    written.write_text(body)
                    os.replace(written, kept)
                found[key] = pr | {"state": "FOLDED"} if entry and pr["state"] == "MERGED" and entry["state"] == github.FOLDED else pr
            elif kept.is_file():
                # TODO: drop the default once every read kept before files joined FIELDS is read again.
                pr = {"files": []} | json.loads(kept.read_text())
                found[key] = pr | {"state": entry["state"].upper(), "headRefOid": entry["head"] or pr["headRefOid"]} if entry else pr
    return found


def card_fields(node: dict) -> dict:
    """A PR's node in GitHub's reply to FIELDS shaped as `gh pr view --json` reads it, the shape the copy kept on disk holds."""
    return node | {"author": {"login": github.author_login(node["author"])}, "labels": node["labels"]["nodes"], "files": node["files"]["nodes"], "comments": node["comments"]["nodes"]}


def drawn_reads(known: dict[str, tuple[str, dict]], keys: list[str]) -> dict[str, dict]:
    """Each PR as its card draws it: GitHub's read, else the drive's entry as watched() gives it, a PR with neither left out."""
    found = github_reads({key: known[key][1] for key in keys})
    return {key: pr for key in keys if (pr := found.get(key) or watched(known[key][1]))}


def watched(entry: dict) -> dict | None:
    if not entry["head"]:
        return None
    return {
        "title": entry["title"],
        "body": None,
        "state": entry["state"].upper(),
        "isDraft": entry["draft"],
        "headRefOid": entry["head"],
        "headRefName": "",
        "baseRefName": "",
        "reviewDecision": REVIEW_DECISIONS[entry["review"]],
        "labels": [{"name": github.HOLD_LABEL}] if entry["hold_label"] else [],
        "url": entry["url"],
        "comments": [],
        "changedFiles": 0,
        "files": [],
    }


def pictured(name: str, pr: dict, jobs: list[str], taken: set[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    from drive import reads

    shown, undrawn = picture.pictures(state.drive_dir(name) / "pictures", reads.shown_off_sprite(name, jobs, sections(body_of(pr)).get("show me", "")), taken)
    return tuple(shown), tuple(undrawn)


def groups(known: dict[str, tuple[str, dict]], keys: list[str], reads: dict[str, dict], redraw: bool = False) -> list[list[str]]:
    """The cards the asked PRs draw on, bottom PR first. A PR in a native GitHub stack, the one the repository's merge queue and his Approve land, draws with every PR of it the drives track that is not closed, so a PR added to the stack later joins its stack's card, but a redraw keeps the card each was last drawn on, since cc-slack redraws only the one card in place and the card the watch draws for a stack split across cards goes in place over the newest of them, which cc-slack redraws under the new PR set, and replaces the rest. Any other read PR draws on its loose_cards() card, the rule state.card_unit names: its chain by base branch, by chained(), every open PR of its drive on that chain with it, so a PR added to the chain later joins its chain's card, a redraw keeping the PRs asked, else the PRs among them of its repository serving its order, keeping the card it was drawn on, closed and natively stacked PRs left out, until they group into a longer card around it."""
    native = github.stacks(list(dict.fromkeys([*keys, *(mate for key in keys for mate in known[key][1].get("stack") or [] if mate in known)])))
    stacked = {member for stack in native.values() for member, _ in stack}
    units = dict.fromkeys(tuple(member for member, status in native[key] if status != "closed" and member in known) for key in keys if key in native)
    by_card: dict[tuple, list[str]] = {}
    for unit in units:
        for member in unit:
            drawn_on = tuple(mate for mate in known[member][1].get("stack") or [] if mate in unit) if redraw else ()
            by_card.setdefault((unit, drawn_on), []).append(member)
    loose = [key for key in reads if key not in stacked]
    if not redraw:
        loose = list(dict.fromkeys(mate for key in loose for mate in chained(known, key, reads) if mate not in stacked))
    branches = ends(known, loose, reads)
    stored = list({tuple(stack): stack for key in loose if (stack := [mate for mate in known[key][1].get("stack") or [] if mate in known and mate not in stacked and known[mate][1]["state"] != "closed"])}.values())
    grown = [group for group in loose_cards(known, loose, branches) if grows(group, stored)]
    kept = [stack for stack in stored if not any(set(stack) <= set(group) for group in grown)]
    covered = {key for group in grown + kept for key in group}
    fresh = loose_cards(known, [key for key in loose if key not in covered], branches)
    return [group for group in by_card.values() if not set(group).isdisjoint(keys)] + kept + grown + fresh


def loose_cards(known: dict[str, tuple[str, dict]], keys: list[str], branches: dict[str, tuple[str, str]]) -> list[list[str]]:
    """The cards PRs in no native GitHub stack draw on, by state.card_unit: each chain of them by base branch on its own card, bottom first, and the rest one card per repository and order they serve, however many PRs each holds."""
    units: dict[tuple[str, tuple[tuple[str, ...], str, str]], list[str]] = {}
    for chain in chains(keys, branches):
        stack = tuple(chain) if len(chain) > 1 else ()
        for key in chain:
            units.setdefault((known[key][0], state.card_unit(key, stack, known[key][1])), []).append(key)
    return list(units.values())


def chained(known: dict[str, tuple[str, dict]], key: str, reads: dict[str, dict]) -> list[str]:
    """The PRs a PR in no native GitHub stack draws on one card with, bottom first, which PrGraph.stack() reads too: its chain by base branch, by chains(), among the open PRs of its drive in no native GitHub stack."""
    pool = [mate for mate, (name, entry) in known.items() if mate == key or name == known[key][0] and entry["state"] == "open" and not entry["native"]]
    return next(chain for chain in chains(pool, ends(known, pool, reads)) if key in chain)


def ends(known: dict[str, tuple[str, dict]], keys: list[str], reads: dict[str, dict]) -> dict[str, tuple[str, str]]:
    """Each PR's head and base branches: as GitHub read them for a PR read now, else as the watch last read them."""
    return {
        key: (read["headRefName"], read["baseRefName"]) if (read := reads.get(key)) and read["headRefName"] else (known[key][1]["branch"] or "", known[key][1]["base"] or "")
        for key in keys
    }


def grows(chain: list[str], stored: list[list[str]]) -> bool:
    """Whether the chain takes in a stored stack and more, and every stored stack it touches whole, so no PR lands on two cards."""
    touched = [stack for stack in stored if set(stack) & set(chain)]
    return any(set(stack) < set(chain) for stack in touched) and all(set(stack) <= set(chain) for stack in touched)


def remember(known: dict[str, tuple[str, dict]], stack: list[str], changes: dict[str, dict]) -> None:
    for key in stack:
        if known[key][1].get("stack") != stack:
            changes.setdefault(key, {})["stack"] = known[key][1]["stack"] = stack


def drawn(known: dict[str, tuple[str, dict]], undrawn: dict[str, tuple[str, ...]], at: str, changes: dict[str, dict]) -> None:
    """Keeps on each drawn PR why each Show me picture left off its card does not draw and when its body was read for it, at, and why the card cannot say how its stored data moves, by unsaid_storage, which record_kept tells once a reason to the session hearing its order, so a builder fixes the body and the watch redraws the card once GitHub reads the body edited since."""
    for key, left in undrawn.items():
        entry = known[key][1]
        if not (left or entry["undrawn"]):
            continue
        repo, number = key.split("#")
        why = f"{repo.split('/')[1]} PR {number}: {'; '.join(left)}, so his card went out without it" if left else None
        changes.setdefault(key, {})["undrawn"] = entry["undrawn"] = {"why": why, "at": at} if why else None


def refusing(known: dict[str, tuple[str, dict]], keys: list[str], reason: str, changes: dict[str, dict]) -> None:
    for key in keys:
        if (entry := known[key][1]).get("refused") != reason:
            changes.setdefault(key, {})["refused"] = entry["refused"] = reason


def kept_later(known: dict[str, tuple[str, dict]], changes: dict[str, dict]) -> None:
    """Keep what a card request changed on its PRs' entries in a receipt per drive, which `drive card-record` applies in a process of its own that waits out the drive's database, so a card never waits on a write however long the watch or another verb holds the database; a request that changed nothing starts none."""
    by_drive: dict[str, dict[str, dict]] = {}
    for key, change in changes.items():
        by_drive.setdefault(known[key][0], {})[key] = change
    for name, changed in by_drive.items():
        kept = state.drive_dir(name) / RECORDS / f"{uuid.uuid4().hex}.json"
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_text(json.dumps(changed))
        state.detach(name, ["card-record", str(kept)], RECORD_LOG)


def record_kept(name: str, kept: Path) -> None:
    """Apply a receipt kept_later kept to the drive in one write that is never refused and waits out the drive's database however long another write holds it, since the card is already drawn: each PR's stack, its undrawn pictures and its refusal, telling the session hearing its order a reason it was not told before, so a refusal cc-slack gives again after every draw is told once until its reason changes; a PR the drive stopped tracking since is passed over. The receipt is removed once applied."""
    changes = json.loads(kept.read_text())

    def applied(data: dict) -> list[tuple[str, dict]]:
        events = []
        for key, change in changes.items():
            if not (pr := data["prs"].get(key)):
                continue
            repo, number = key.split("#")
            told = {"repo": repo, "number": int(number), "lane": state.heard_lane(data, pr["order"])}
            if "stack" in change:
                pr["stack"] = change["stack"]
            if "undrawn" in change:
                if (why := (change["undrawn"] or {}).get("why")) and why != (pr["undrawn"] or {}).get("why"):
                    events.append(("undrawn", told | {"reason": why}))
                pr["undrawn"] = change["undrawn"]
            if "refused" in change and pr.get("refused") != change["refused"]:
                pr["refused"] = change["refused"]
                events.append(("refused", told | {"reason": change["refused"]}))
        return events

    events = state.write(name, applied, patient=True)
    for kind, fields in events:
        state.append_event(name, kind, **fields)
    kept.unlink()


def drawn_card(shown: list[Member], siblings: bool) -> dict:
    return render_stack(shown, siblings) if len(shown) > 1 else render(*shown[0])


def siblings(known: dict[str, tuple[str, dict]], members: list[str], reads: dict[str, dict]) -> bool:
    """Whether a card's PRs are sibling PRs of one request rather than a stack: none in a native GitHub stack and none on another's head branch, so each lands alone though his one Approve taps them all."""
    return not any(known[key][1]["native"] for key in members) and len(chains(members, ends(known, members, reads))) == len(members)


def picks(data: dict, entry: dict, found: dict | None) -> dict[str, dict]:
    """Each card holding the PR by land.pr_cards, the one rule by which a card holds a PR, so never a card that only ends its waits, drawn for an open question of the drive whose card recommends an option, the one the PR's builder builds: its waits id, its link, its title and that option, by the card cc-slack lists or else the one the drive keeps; none, reading no card, when no card holds it. His Approve on the PR keeps these on its tap, and land.start answers each still open with that option, so a PR built on his card's recommendation, an incident's repair included, never waits on a card his Approve already answered, by his e55b1cd "The repair lands on the recommendation"."""
    from drive import land, view

    if not (holding := land.pr_cards(data, entry)):
        return {}
    return {
        wait: {"waits": asked[-1]["waits"], "url": drawn["url"], "title": drawn["card"]["title"], "option": option}
        for wait in view.stored_waits(data, holding, found)
        if (asked := [question for _, question in view.drawn_on(data, wait) if question["closed"] is None and question["waits"] and question["card"]])
        and (option := (drawn := found and view.asked_card(found, wait) or view.forgotten(data["name"], asked[-1])).get("recommended"))
    }


def untapped(drives: dict[str, dict], known: dict[str, tuple[str, dict]], reads: dict[str, dict], members: list[str]) -> str:
    """Why no fresh card is drawn for the members: none of them asks his tap, so the card would offer him no Approve, and the post linking them keeps its plain link, as an exempt PR or one his open question card holds gets no PR card."""
    refusals = {key: tap_refused(reads[key], known[key][1] | {"tap_refused": state.tap_refused(drives[known[key][0]], known[key][1])}) for key in members}
    if None in refusals.values():
        return ""
    return "; ".join(f"{key}: {why}" if len(members) > 1 else why for key, why in refusals.items()) + ", so no PR card draws, since it would offer him no Approve"


def member(data: dict, found: dict | None, pr: dict, entry: dict, drawing: tuple[tuple[str, ...], tuple[str, ...]] = ((), ())) -> Member:
    """The PR as his card draws it from the drive and his cards its caller read: GitHub's read of it, the drive's entry as drawn_entry gives it, its drawn Show me pictures and why each left off does not draw."""
    return (pr, drawn_entry(data, entry, found), *drawing)


def drawn_entry(data: dict, entry: dict, found: dict | None) -> dict:
    """The drive's entry with what render reads beside it, the one place that adds it, for his card and the dashboard's row alike: its stage and staging line now, why his card offers no Approve by state.tap_refused, whether the drive's lander queues it by state.queued_by_lander, and a link to each open PR above it in its stack, by PrGraph.stack(), being reworked to his words, which cards() leaves off its card with every PR above it so his Approve lands it alone."""
    from drive import stage, view

    key = state.pr_key(entry["url"])
    stack = [mate for mate in state.pr_graph(data).stack(key) if mate in data["prs"] and data["prs"][mate]["state"] == "open"]
    above = stack[stack.index(key) + 1 :] if key in stack else []
    return entry | {"now": stage.on_card(data, key, found), "staging": view.staging_row(data, key), "reworked_above": tuple(view.pr_link(data["prs"][mate]) for mate in above if data["prs"][mate]["rework"]), "tap_refused": state.tap_refused(data, entry), "queued": state.queued_by_lander(data, key)}


def drawn_lines(data: dict, key: str, found: dict | None) -> dict[str, str]:
    """The REDRAWN lines his card on the PR draws now from the drive's entry and his cards as its caller read them, no GitHub read and no read of the drive or his cards, which the watch holds against the card cc-slack last drew."""
    entry = data["prs"][key]
    row = render(*member(data, found, watched(entry), entry))["prs"][0]
    return {line: row[line] for line in REDRAWN}


def reworked(known: dict[str, tuple[str, dict]], members: list[str]) -> str:
    """Why no card is drawn for the members, a redraw of one cc-slack posted included: one of them is being reworked to his words, so its card stays down until its builder's READY says how they are met; cards() draws the PRs below the lowest reworked one alone."""
    return "; ".join(
        f"{key if len(members) > 1 else 'it'} is being reworked to his words \"{entry['rework']['words']}\", so no card asks his tap until its builder's READY says how they are met"
        for key in members
        if (entry := known[key][1])["state"] == "open" and entry["rework"]
    )


def held_by_rework(known: dict[str, tuple[str, dict]], members: list[str], reads: dict[str, dict]) -> list[str]:
    """The members a rework keeps off his card: each PR being reworked to his words, and each PR above it on its branch, whose base is a held PR's head, since none of those lands without it; never a PR that only shares its card by repository and order, by meta's fix of 735990, where #57649's rework refused his cards on three PRs it held nothing of."""
    branches = ends(known, members, reads)
    held: list[str] = []
    for key in members:
        if reworked(known, [key]) or any(branches[key][1] and branches[key][1] == branches[below][0] for below in held):
            held.append(key)
    return held


HELD_BY_ANSWER = (
    "Does one of `answers`, his own recorded words, hold back PR {key}, `prs.{number}`, from his Approve now, such as by saying"
    " not now or not until a later step like a cutover, while no later answer among them lifts that?"
)
ANSWERS_WEIGHED = 25
ANSWER_TERM = re.compile(r"[a-z0-9]{4,}")
ANSWER_SECONDS = 8


def held_by_him(drives: dict[str, dict], known: dict[str, tuple[str, dict]], members: list[str], reads: dict[str, dict]) -> dict[str, str]:
    """Each member his Hold tap or a recorded answer of his keeps, and each on its branch above it, which none of his Approves may carry, with why: his Hold and his answers are his word, never a drive gate, so the card draws the rest, by his words "give me approves to get next on v3 but not delete the old one til cutover I've said this like 5 times" (answer 1439da5); a PR alone in its stack is kept too, since a held PR on another base, as #54428 sits on run-page-old-v3, groups alone and drew its own Approve card."""
    answered = held_by_answers(drives, known, members, reads)
    branches = ends(known, members, reads)
    kept: dict[str, str] = {}
    for key in members:
        if tap_label(known[key][1]) == "Hold":
            tap = known[key][1]["tap"]
            kept[key] = f"{key} is under your Hold of {when(tap['at'])}, your word, so no Approve carries it until you lift it" + (f" ({tap['source']})" if str(tap.get("source") or "").startswith("https://") else "")
        elif key in answered:
            kept[key] = answered[key]
        elif below := next((held for held in kept if branches[key][1] and branches[key][1] == branches[held][0]), None):
            kept[key] = f"{key} is left out: it sits on {below}, which your word keeps"
    return kept


def weighed_answers(data: dict, keys: list[str], reads: dict[str, dict]) -> list[dict]:
    """The recorded answers of his that the one Jev read weighs a card's PRs against: those on the jobs, cards or threads of the PRs' orders, and those sharing words with the PRs' titles and branches, newest first, at most ANSWERS_WEIGHED; the read, not these words, judges which hold a PR."""
    orders = {data["prs"][key]["order"] for key in keys}
    jobs = {job_id for job_id, job in data["jobs"].items() if job["order"] in orders}
    threads = {tuple(data["orders"][order]["thread"] or ()) for order in orders if order in data["orders"]}
    terms = set(ANSWER_TERM.findall(" ".join(f"{reads[key].get('title', '')} {reads[key].get('headRefName', '')}".lower().replace("-", " ") for key in keys)))
    picked = [
        {"answer": answer_id[:7], "words": answer["words"], "at": answer["at"], "link": answer["link"]}
        for answer_id, answer in data["answers"].items()
        if jobs & set(answer.get("jobs") or {})
        or any(card.split(":")[-1].split("-")[0] in jobs for card in answer.get("cards") or {})
        or (found := state.slack_message(answer.get("link") or "")) and (found[0], found[2] or found[1]) in threads
        or len(terms & set(ANSWER_TERM.findall(answer["words"].lower()))) >= 2
    ]
    return sorted(picked, key=lambda answer: answer["at"], reverse=True)[:ANSWERS_WEIGHED]


def held_by_answers(drives: dict[str, dict], known: dict[str, tuple[str, dict]], members: list[str], reads: dict[str, dict]) -> dict[str, str]:
    """Each member a recorded answer of his holds back from his Approve, as one Jev read of his answers judges, never a match of his words; none when the read gives no answer, so a card still draws."""
    from drive import settled

    by_drive: dict[str, list[str]] = {}
    for key in members:
        by_drive.setdefault(known[key][0], []).append(key)
    held: dict[str, str] = {}
    for name, keys in by_drive.items():
        if not (answers := weighed_answers(drives[name], keys, reads)):
            continue
        prs = {key.rpartition("#")[2]: {"pr": key, "title": reads[key].get("title"), "branch": reads[key].get("headRefName")} for key in keys}
        asked = {f"held.{number}": settled.Noul(HELD_BY_ANSWER.format(key=pr["pr"], number=number)) for number, pr in prs.items()}
        try:
            said = settled.jev({"answers": answers, "prs": prs}, asked, ANSWER_SECONDS, "card-held")
        except settled.Unheard:
            continue
        cited = ", ".join(f"{answer['answer']} ({answer['link']})" for answer in answers[:2])
        held |= {prs[key.partition(".")[2]]["pr"]: f"{prs[key.partition('.')[2]]['pr']} is held by your recorded words, such as {cited}, so no Approve carries it until you lift them" for key, chance in said.items() if chance >= settled.YES}
    return held


def foreign(drives: dict[str, dict], known: dict[str, tuple[str, dict]], members: list[str]) -> str:
    """Why no card is drawn for the members: one of them is someone else's PR that no order of his it serves names, which the drive never cards."""
    return "; ".join(why for key in members if (why := state.foreign_pr(drives[known[key][0]], key)))


def on_his_card(known: dict[str, tuple[str, dict]], founds: dict[str, dict | None], reads: dict[str, dict], members: list[str]) -> bool:
    """Whether an open card of his already carries one of the PRs: a wait redraws that card in place, Approve and Hold kept, and never draws a fresh one."""
    from drive import view

    listed = ((founds[known[key][0]], reads[key]["url"]) for key in members)
    return any(found is not None and view.card_for(found["pr_cards"], url) is not None for found, url in listed)


LANE_UNTAPPED = "it waits on no tap of his in its lane now: approved, held, or not yet green and reviewed, so no PR card draws, since it would offer him no Approve"
LANE_REFUSED = "it waits on no tap of his in its lane at this head"


def cards(asked: list[dict], redraw: bool = False) -> dict:
    """Each asked PR's card: one a live lane drive holds by lane_cards, ahead of any older drive's entry for it, as lane_tapped takes his tap on it first, and every other by drive_cards."""
    held = lane_held(asked)
    rest = [ask for ask in asked if f"{ask['repo']}#{ask['number']}".casefold() not in held]
    lanes = lane_cards(held, redraw) if held else {"cards": [], "skipped": []}
    drawn = drive_cards(rest, redraw) if rest else {"cards": [], "skipped": []}
    return {"cards": lanes["cards"] + drawn["cards"], "skipped": lanes["skipped"] + drawn["skipped"]}


def lane_held(asked: list[dict]) -> dict[str, tuple[str, str, dict]]:
    """Each asked PR a lane of a live lane drive carries, by its key folded, with the key as the lane spells it, that drive and that lane."""
    from lane import store as lanes

    wanted = {f"{ask['repo']}#{ask['number']}".casefold() for ask in asked}
    held: dict[str, tuple[str, str, dict]] = {}
    root = lanes.root()
    for drive in sorted(folder.name for folder in root.iterdir() if (folder / lanes.LIVE).exists()) if root.is_dir() else []:
        for lane in lanes.load_all(drive).values():
            for row in lane["prs"]:
                if (key := f"{row['repo']}#{row['number']}").casefold() in wanted:
                    held.setdefault(key.casefold(), (key, drive, lane))
    return held


def lane_cards(held: dict[str, tuple[str, str, dict]], redraw: bool = False) -> dict:
    """The PR-group cards of the asked PRs live lane drives hold, the cards approve.draw asks cc-slack for: one per ready stack of the lane holding each, by approve.ready_stacks, bottom first, drawn by render or render_stack as the old drive's cards are, titled whole by the lane's name for the stack, or its bottom PR's GitHub title while it has none, by approve.card_title, its body led by what the stack changes for a user and what waits on his tap, lane_why, with Approve and Hold while its PRs ask his tap at the heads GitHub reads. A PR in no ready stack draws on a redraw as it stands now, with no Approve, on the stack the lane's card last asked it in, and is skipped on a fresh ask, since its card would offer him no Approve."""
    from lane import approve, page

    found, skipped = [], []
    by_lane: dict[tuple[str, str], tuple[dict, list[str]]] = {}
    for key, drive, lane in held.values():
        by_lane.setdefault((drive, lane["id"]), (lane, []))[1].append(key)
    for (drive, _), (lane, keys) in by_lane.items():
        ready = approve.ready_stacks(lane, page.view(drive)["prs"])
        asked_on = [list(heads) for card in reversed(lane.get("cards", [])) for heads in card["stacks"]]
        stacks: list[list[str]] = []
        for key in keys:
            if any(key in stack for stack in stacks):
                continue
            if (stack := next((list(heads) for heads in ready if key in heads), None)) is None and not redraw:
                skipped.append(skip(key, LANE_UNTAPPED))
                continue
            stacks.append(stack or next((mates for mates in asked_on if key in mates), [key]))
        reads = github_reads(dict.fromkeys(key for stack in stacks for key in stack))
        tappable = {key: head for heads in ready for key, head in heads.items()}
        rows = {f"{row['repo']}#{row['number']}": row for row in lane["prs"]}
        for stack in stacks:
            skipped += [skip(key, "drive card could not read it from GitHub") for key in stack if key not in reads and key in keys]
            if not (members := [key for key in stack if key in reads]):
                continue
            card = drawn_card([(reads[key], lane_entry(key, rows[key], reads[key], tappable), (), ()) for key in members], False)
            card["title"] = approve.card_title(lane, dict.fromkeys(members), page.view(drive)["prs"])
            card["body"] = "\n\n".join(filter(None, [lane_why(drive, lane, members, page.view(drive)["prs"], reads), card.get("body")]))
            repo, number = members[0].split("#")
            found.append({"repo": repo, "number": int(number), "card": card})
    return {"cards": found, "skipped": skipped}


def lane_why(drive: str, lane: dict, members: list[str], seen: dict, reads: dict[str, dict]) -> str:
    """The card's first line: what the stack changes for a user and what waits on his tap, in the owner's words or a small model's from the PR bodies, by cardline.written, then the approved PRs under it that his tap lands it on."""
    from lane import cardline, page

    branches = {(key.rpartition("#")[0], pull.get("branch")): key for key, pull in seen.items() if pull["state"] == "OPEN"}
    under, key = [], members[0]
    while (key := branches.get((key.rpartition("#")[0], (seen.get(key) or {}).get("base")))) and key in page.approved(lane) and key not in under:
        under.append(key)
    under = [f"#{key.rpartition('#')[2]}" for key in under]
    return " ".join(filter(None, [cardline.written(drive, lane, members, reads), f"It lands on {', '.join(under)}, which you approved." if under else ""]))


def lane_entry(key: str, row: dict, pr: dict, tappable: dict[str, str]) -> dict:
    """A lane's PR row as render reads a drive's entry: asking his Approve while the PR sits in a ready stack of its lane at the head GitHub reads, headed by the row's own words."""
    return {
        "lead": {"headline": row.get("words") or ""},
        "reviewed": [],
        "staging": (),
        "stored": [],
        "tap": None,
        "tap_refused": None if tappable.get(key) == pr["headRefOid"] else LANE_REFUSED,
    }


def drive_cards(asked: list[dict], redraw: bool = False) -> dict:
    """One card per stack the asked PRs form, by groups(), bottom PR first, with each PR reworked to his words and each PR on its branch above it refused by reworked(), since none of those lands without it, and the rest of the card drawn, each card drawn kept on its PRs and asking under ask_waits, so Approve lands exactly the PRs it asks, and a fresh one only when tap_refused finds one of them asking his Approve, the same check that puts Approve on a card. A redraw of a card cc-slack already posted draws it as the PRs stand now, a wait that holds back a fresh Approve shown on it rather than refusing it. A PR body's sections are drawn as they stand and never hold a card, a Show me picture that does not draw included, such as an arch diagram whose store node names a medium the drawing does not know: it is left off, the card's Show me saying why in one line, which `drawn` tells the session hearing its order. The reply skips only PRs it was asked for, since cc-slack refuses a whole draw or redraw whose reply skips any PR, so a stack mate refused beside an asked PR, such as the reworked PR above it, never keeps the asked PR's card from drawing."""
    from drive import view

    at, drives = state.stamp(), {name: state.load(name) for name in state.names()}
    known = tracked(drives)
    asked_keys = keys_of(asked, known)
    for key in asked_keys:
        if key not in known:
            print(f"drive card: {key} is in no drive", file=sys.stderr)
    keys = [key for key in asked_keys if key in known]
    reads = drawn_reads(known, keys)
    skipped = [skip(key, "drive card could not read it from GitHub") for key in keys if key not in reads]
    try:
        stacks = groups(known, list(reads), reads, redraw)
    except GithubError as error:
        return {"cards": [], "skipped": skipped + [skip(key, str(error)) for key in reads]}
    reads |= drawn_reads(known, list(dict.fromkeys(key for group in stacks for key in group if key not in reads)))
    found, changes = [], {}
    for group in stacks:
        members = [key for key in group if key in reads]
        if not members:
            continue
        founds = {name: view.live_cards(name) for name in {known[key][0] for key in members}}
        reason = foreign(drives, known, members)
        held = held_by_rework(known, members, reads)
        if not reason and held:
            why = reworked(known, members)
            skipped += [skip(key, why) for key in held]
            refusing(known, held, why, changes)
            members = [key for key in members if key not in held]
            if not members:
                continue
        if kept := held_by_him(drives, known, members, reads):
            skipped += [skip(key, why) for key, why in kept.items()]
            members = [key for key in members if key not in kept]
            if not members:
                continue
        fresh = not redraw and not on_his_card(known, founds, reads, members)
        if fresh and not reason and (off := untapped(drives, known, reads, members)):
            skipped += [skip(key, off) for key in members]
            continue
        if reason:
            skipped += [skip(key, reason) for key in members]
            refusing(known, members, reason, changes)
            continue
        taken: set[str] = set()
        drawings = {key: pictured(known[key][0], reads[key], state.pr_jobs(drives[known[key][0]], known[key][1]), taken) for key in members}
        card = drawn_card(
            [member(drives[known[key][0]], founds[known[key][0]], reads[key], known[key][1], drawings[key]) for key in members],
            siblings(known, members, reads),
        )
        if kept:
            card |= {"title": framed("", card["title"], f" · left out, held by your word: {', '.join('#' + key.rpartition('#')[2] for key in kept)}")}
        card = titled(card, reads[members[0]]["title"])
        remember(known, members, changes)
        drawn(known, {key: (*undrawn, *unsaid_storage(reads[key], known[key][1])) for key, (_, undrawn) in drawings.items()}, at, changes)
        repo, number = members[0].split("#")
        found.append({"repo": repo, "number": int(number), "card": card})
    kept_later(known, changes)
    return {"cards": found, "skipped": [each for each in skipped if f"{each['repo']}#{each['number']}" in asked_keys]}


def numbered(numbers: list[int]) -> dict:
    wanted = set(numbers)
    matches = []
    for key in sorted(drive_prs()):
        repo, number = key.split("#")
        if int(number) in wanted:
            matches.append({"repo": repo, "number": int(number)})
    return {"prs": matches}


def shown(asks: list[dict]) -> dict:
    known = drive_prs()
    return {"shown": sum(1 for key in keys_of(asks, known) if key in known)}


def refused(asks: list[dict]) -> dict:
    known, changes = drive_prs(), {}
    for ask, key in zip(asks, keys_of(asks, known), strict=True):
        if key in known:
            refusing(known, [key], ask["reason"], changes)
    kept_later(known, changes)
    return {"refused": len(changes)}


def answered(ask: dict) -> dict:
    """Keep his tap on a PR card on each open PR it covers, one tap event each, his Approve with the cards it picks the recommended option of, each card picks() finds holding the PR, and on his Approve run land.start, the one path that answers each of those cards and starts the lander, which every watch pass runs again, or on his Hold stop each landing of them by land.halt; a card's tap covers the PRs its waits id asks, by ask_waits, or for a stack too long for its id to name them, each PR the tapped card lists, by its heads, that still asks his tap, and an answer whose waits id names no ask the PR it names; on a card of several PRs a member he already approved keeps that Approve through a later tap on the card, while one he held takes the tap, so one Approve on a stack card lifts each member's Hold and lands the card whole, by his pick eb69825, and cc-slack applies a tap again when its first card command did not finish, so a member already holding this tap's Slack action keeps it and gets no second event."""
    from drive import cli, land, view

    if "repo" not in ask:
        return question_answered(ask)
    if lane_tapped(ask):
        return {"ok": True}
    known = drive_prs()
    key = keys_of([ask], known)[0]
    if key not in known:
        raise state.DriveError(f"{key} is not a drive PR")
    if ask["label"] not in TAP_LABELS:
        raise state.DriveError(f"a PR card answers Approve or Hold, not {ask['label']!r}")
    name = known[key][0]
    hear_tap(name)
    asked = state.ASK_WAITS.fullmatch(ask["waits"])
    stacked = asked or state.LONG_ASK_WAITS.fullmatch(ask["waits"])
    numbers = asked[1].split(".") if asked else [member.rpartition("#")[2] for member in ask["heads"]]
    members = [member for member in keys_of([{"repo": ask["repo"], "number": number} for number in numbers], known) if member in known] if stacked else []
    members = members or [key]
    found = view.live_cards(name)

    lifted: list[str] = []

    def kept_taps(data: dict) -> None:
        events = []
        for member in members:
            entry = data["prs"][member]
            if entry["state"] != "open" or len(members) > 1 and land.approved(entry) or not asked and len(members) > 1 and not state.asks_tap(data, entry) or ask["action_ts"] and (entry["tap"] or {}).get("action_ts") == ask["action_ts"]:
                continue
            picked = {wait: pick | {"seq": None} for wait, pick in picks(data, entry, found).items()} if ask["label"] == "Approve" else {}
            if ask["label"] == "Hold":
                state.end_approvals(data, member, entry["tap"])
            elif (entry["tap"] or {}).get("label") == "Hold":
                lifted.append(member)
            entry["tap"] = state.new_tap(ask["label"], ask["heads"].get(member, entry["head"]), ask["source"], ask["action_ts"], picks=picked, card=stacked and entry.get("stack") or [member])
            entry["fundamental"] = None
            entry["updated"] = entry["tap"]["at"]
            repo, number = member.split("#")
            events.append({"repo": repo, "number": int(number), "label": ask["label"], "head": entry["tap"]["head"], "source": ask["source"], "action_ts": ask["action_ts"], "lane": state.heard_lane(data, entry["order"])})
        if events:
            state.after(lambda: [state.append_event(name, "tap", **event) for event in events] and [])

    state.write(name, kept_taps)
    if ask["label"] == "Approve":
        land.start(name, members, view.live_cards(name))
        for member in lifted:
            cli.lifted_hold(name, member)
    else:
        land.halt(name, members, view.live_cards(name))
    return {"ok": True}


def lane_tapped(ask: dict) -> bool:
    """Write his tap on each PR it covers, the tapped one and each member of its stack card by its heads, on each live lane drive carrying it, through lane's own verb, which wakes its lane, keeps his presence and leaves the landing to lane-land, and mark the lane's card asking about it at that head tapped; false when no live lane drive carries any of them, so the old drive takes it."""
    from lane import approve
    from lane import verbs as lanes

    tapped = f"{ask['repo']}#{int(ask['number'])}"
    members = ask["heads"] if tapped in ask["heads"] else {tapped: next(iter(ask["heads"].values()), None), **ask["heads"]}
    landed = False
    for member, head in members.items():
        repo, _, number = member.rpartition("#")
        for drive in lanes.live_drives_holding(repo, int(number)):
            if not head:
                raise state.DriveError(f"his {ask['label']} on {member} names no head")
            lanes.tapped(drive, repo, int(number), ask["label"], head, ask["source"], ask["action_ts"], named=member == tapped and bool(ask["action_ts"]))
            approve.answered(drive, member, head, ask["label"], ask["action_ts"])
            landed = True
    return landed


def question_answered(ask: dict) -> dict:
    """End the question his tap on a drive's question card answers, by a button, its number typed in its thread or his words in its Other box, in the tap's first write, dated by his tap as its record id carries it however late the drive takes the tap, with each older open question of its job his answer decides, as Jev reads it, superseded, so nothing refused after it leaves his answered card holding the builders waiting on it; then post it as the one answer record the coordinator gets for it, its topic `card:<thread_ts>`, idempotent by his Slack action, so cc-slack runs this again until the record stores and shows the tap recorded only then, and stamp the record's seq on each question the tap ended; each other card drawn for an ended question follows, and cc-slack closes the tapped card itself. A tap on Already answered answers it with the words of each recorded answer of his the card drew under that option, so its job builds them. His answer, unless it is the Already answered tap, whose words cc-notes holds already, is recorded in cc-notes as `drive answer` records one, the card's question as its title and his words as its body, once, its id kept on each question it ended and on the answer record, so `settled.matched` ranks it beside every answer he gave and a card asking it again in other words is told. Recording it supersedes each answer of the drive's still standing under that question, so his changed pick leaves one, and each answer of his the card asked him to lift that Jev reads his answer replacing, by settled.lift. An Undo reopens each question his answer on the card ended, by answered_at, his tap there or his words at or under it, which cc-slack redraws, records the card's question answered UNDONE, which supersedes the answer it takes back, takes back each supersede that answer wrote on an answer the card asked him to lift, by settled.unlift, and takes the answer that ended it off every job holding it, by its record's seq, since the card may have let go of the jobs that waited on it; an Undo reopening none is refused and records nothing. Held verbatim on every job it frees that was made by his tap until the watch carries it there; History keeps it, or its undo, as his decision, once: a run again after one the card command's two minutes cut short finds his answer recorded by its record and adds nothing. His words in its Other box that only ask the card questions or remark on it, as tools.under_card reads them, end nothing and record nothing: `ok` false and `asks` true, so cc-slack draws the card's options again and hands his words to the session it reached to answer in the card's thread, and Jev giving no reading refuses the tap, so cc-slack runs this again. A waits id naming no drive is not the drive's to record: `ok` false, so cc-slack hands the tap to the session it reached. A tap that raises on a run after one that raised, cc-slack's retry, tells the meta session by unrecorded, the failed run kept as a file under UNRECORDED_TAPS until a run records it."""
    name, _, _ = ask["waits"].partition(":")
    if name not in state.names():
        return {"ok": False, "why": f"{ask['waits']} is no drive's question card, so his tap on it stays with the session it reached"}
    if released := release_answered(name, ask):
        return released
    hear_tap(name)
    tried = state.drive_dir(name) / UNRECORDED_TAPS / f"{ask['waits']}:{ask.get('action_ts')}{':undone' if ask.get('undone') else ''}"
    try:
        answer = answer_question(name, ask)
    except Exception as failure:
        if tried.exists():
            unrecorded(name, ask, tried.name, failure)
        tried.parent.mkdir(parents=True, exist_ok=True)
        tried.touch()
        raise
    tried.unlink(missing_ok=True)
    return answer


def release_answered(name: str, ask: dict) -> dict | None:
    """Take his tap on an approve card the watch drew for a release build's approve step, by watch.release_card_lines, the card a job keeps under the tap's waits id; None for any other card. His Approve runs the release provider's approve_command with the release filled, and his Hold its hold_command, or records the hold when it names none, each as him in the drive's checkout; his tap is kept before the command runs and the result after, so cc-slack's run again of the same tap, or a later tap, runs nothing twice, and one record tells the job and the coordinator what his tap did. The watch shows the result on the card in place of his tap. An Undo takes back nothing the release tool already did, and his words in the card's Other box approve nothing: both answer `ok` false, so cc-slack hands them to the session it reached."""
    from drive import watch

    data = state.load(name)
    if not (found := next(((job_id, key) for job_id, job in data["jobs"].items() for key, entry in job["release_cards"].items() if entry["waits"] == ask["waits"]), None)):
        return None
    hear_tap(name)
    job_id, key = found
    entry = data["jobs"][job_id]["release_cards"][key]
    if ask.get("undone"):
        return {"ok": False, "why": RELEASE_UNDONE.format(tap=(entry["tap"] or {}).get("label") or ask["label"])}
    if ask.get("other") or ask["label"] not in TAP_LABELS:
        return {"ok": False, "why": RELEASE_WORDS}
    if entry["tap"]:
        return {"ok": True, "result": entry["result"]}
    tap = {"label": ask["label"], "action_ts": ask.get("action_ts"), "at": state.tap_time(f"tap:{ask['waits']}:{ask.get('action_ts')}") or state.stamp()}
    def release_tap_write(data: dict):
        data["jobs"][job_id]["release_cards"][key]["tap"] = tap

    state.write(name, release_tap_write)
    result = release_tapped(data, entry, ask["label"])
    def release_result_write(data: dict):
        data["jobs"][job_id]["release_cards"][key]["result"] = result

    state.write(name, release_result_write)
    text = f"RELEASE {job_id} {data['jobs'][job_id]['order']}: he chose {ask['label']} on his approve card for release {entry['release']} {entry['build']} at \"{entry['step']}\": {result}"
    seq = watch.release_post(name, state.load(name), job_id, text, entry["url"] or entry["build"], f"release-tap-{ask['waits']}")
    return {"ok": True, "seq": seq}


def release_tapped(data: dict, entry: dict, label: str) -> str:
    """What his tap on a release approve card did: Approved or Held once the release provider's command for it exits 0, Held when it names no hold_command, else the tap failed with the command's last line of output, or its time running out."""
    release = providers.of(data).release
    if not (command := release.approve_command if label == TAP_LABELS[0] else release.hold_command):
        return "Held"
    filled = release.filled(command, release=entry["release"])
    try:
        done = subprocess.run(filled, check=False, shell=True, cwd=release.checkout, capture_output=True, text=True, timeout=RELEASE_TAP_SECONDS)
    except subprocess.TimeoutExpired:
        return f"{label} failed: `{filled}` ran past {RELEASE_TAP_SECONDS} s"
    if done.returncode == 0:
        return "Approved" if label == TAP_LABELS[0] else "Held"
    said = next((line.strip() for line in reversed(f"{done.stdout}\n{done.stderr}".splitlines()) if line.strip()), f"exit {done.returncode}")
    return f"{label} failed: {said}"


def unrecorded(name: str, ask: dict, tap: str, failure: Exception) -> None:
    """Tell the meta session, as a defect, that his tap failed again on cc-slack's retry, once per tap by its record id, since cc-slack only logs each failure and retries every two minutes."""
    from drive import tools

    text = UNRECORDED.format(label=ask["label"], url=ask["url"], waits=ask["waits"], failure=raised(failure))
    try:
        tools.sent(name, "communicator", [{"kind": "defect", "to": [f"meta@{name}"], "text": text, "id": f"unrecorded:{tap}"}])
    except state.DriveError as error:
        print(f"drive card: the meta session was not told: {error}", file=sys.stderr)


def answer_question(name: str, ask: dict) -> dict:
    """What question_answered does once it heard his tap, which land.start runs for each card his Approve on a PR picks an option on, that Approve heard already, with its Slack action, which dates the answer record and keeps it idempotent; his tap ends each question on the card view.tappable lets it, in place of whatever answer of his ended it before the tap, whose cc-notes answer recording the tap supersedes, and the question's own card follows when he tapped another copy of it."""
    from drive import agents, settled, superseded, tools, view

    undone = bool(ask.get("undone"))
    data = state.load(name)
    recorded = [answer for _, question in view.drawn_on(data, ask["waits"], ask["url"]) for answer in question["recorded"]] if ask["label"] == limits.ALREADY_ANSWERED else []
    said = "\n".join(dict.fromkeys(answer["words"] for answer in recorded)) or ask["label"]
    item = {"kind": "answer", "text": said, "url": ask["url"], "undone": undone}
    item |= {"waits": ask["waits"]} if state.WAITS_ID.fullmatch(ask["waits"]) else {"topic": f"card:{view.card_thread(ask['url'])}"}
    item |= {"attrs": {"maps": "none"}} if ask.get("other") else {}
    item |= {"id": f"tap:{ask['waits']}:{ask['action_ts']}{':undone' if undone else ''}"} if ask.get("action_ts") else {}
    if ask.get("other") and not undone:
        state.keep_heard(name, {"type": "card.other", "waits": ask["waits"], "url": ask["url"], "text": ask["label"], "action_ts": ask.get("action_ts")})
        opened = [(job_id, question) for job_id, question in view.drawn_on(data, ask["waits"], ask["url"]) if view.tappable(question, ask.get("action_ts"))]
        try:
            asks, _ = tools.under_card(view.stored_cards(name)[0], item, opened)
        except settled.Unheard as failure:
            raise state.DriveError(tools.UNHEARD.format(failure=failure)) from failure
        if asks:
            thread = view.card_thread(ask["url"])
            agents.handle(name, f"Message (his words): {ask['label']}\nLink: {ask['url']}\nThread: {state.slack_message(ask['url'])[0]} {thread}\nDrive: {name}\nScratch: {state.drive_dir(name) / 'scratch' / f'other-{thread}'}\nContext: his words in the Other box of the question card {ask['waits']}, which they answer nothing on")
            return {"ok": False, "asks": True, "routed": True, "why": asks}
    closed = state.new_close("answered", said, ask["url"], at=state.tap_time(item.get("id")), tap=ask.get("action_ts"))
    if undone and (drawn := view.drawn_on(data, ask["waits"], ask["url"])):
        if not (taken := answered_at(data, drawn, ask["url"])):
            raise state.DriveError(UNREOPENED.format(card=ask["url"]))
        cwd = data["roles"]["coordinator"]["cwd"]
        settled.recorded(name, card_question(taken, ask["waits"])["title"], UNDONE.format(label=ask["label"]), [], ask["url"], cwd)
        for _, question in taken:
            if taken_back := question["closed"]["noted"] or question["closed"]["answer"]:
                settled.unlift(taken_back, settled.lifting([question]), cwd)

    def closed_on_card(data: dict) -> tuple[dict, list, list]:
        on_card = view.drawn_on(data, ask["waits"], ask["url"])
        if undone:
            fresh = answered_at(data, on_card, ask["url"])
            given = {question["closed"]["seq"] for _, question in fresh}
            for job in data["jobs"].values():
                job["undelivered"] = [held for held in job["undelivered"] if held["seq"] not in given]
            moment = state.now()
            for job_id, question in fresh:
                owing.reverted(data, question["closed"]["answer"] or ask["waits"], ask["url"], moment + owing.SPAN_FLOOR, moment, [job_id])
                question["closed"] = None
                data["jobs"][job_id]["updated"] = state.stamp()
        else:
            fresh = [each for job_id, question in on_card if view.tappable(question, ask.get("action_ts")) for each in superseded.end(data, job_id, question, closed | {"replaces": question["closed"]})]
        return data, on_card, fresh

    data, on_card, fresh = state.write(name, closed_on_card, patient=True)
    shown = view.stored_cards(name)[0]
    card = view.asked_card(shown, ask["url"])
    if fresh or not on_card:
        state.append_event(name, "undone" if undone else "answered", url=ask["url"], label=ask["label"], question=card and card["question"], undo_until=ask.get("undo_until"))
    if not undone:
        data = state.load(name)
        tapped = [(job_id, question) for job_id, question in view.drawn_on(data, ask["waits"], ask["url"]) if (kept := question["closed"]) and (kept["how"], kept["words"], kept["link"]) == ("answered", said, ask["url"])]
        if decided := [(job_id, older, question) for job_id, question in tapped for older in superseded.decided_by(data, job_id, question, said, shown)]:

            def superseded_older(data: dict) -> dict:
                for job_id, older, question in decided:
                    if (kept := state.question_in(data["jobs"][job_id], older))["closed"] is None:
                        superseded.end(data, job_id, kept, superseded.superseded_by(closed, question))
                return data

            data = state.write(name, superseded_older)
        if tapped and not recorded:
            answer_id = next((question["closed"]["noted"] for _, question in tapped if question["closed"]["noted"]), None)
            if answer_id is None:
                answer_id = settled.recorded(name, card_question(tapped, ask["waits"])["title"], said, [], ask["url"], data["roles"]["coordinator"]["cwd"], settled.lifting(question for _, question in tapped))

                def noted_answer(data: dict) -> dict:
                    for job_id, question in tapped:
                        state.question_in(data["jobs"][job_id], question)["closed"]["noted"] = answer_id
                    return data

                data = state.write(name, noted_answer, patient=True)
            item["attrs"] = item.get("attrs", {}) | {"noted": answer_id}
    made = tools.post(item, data, "root", (data["roles"].get("communicator") or {}).get("session"))
    [seq] = tools.sent(name, "communicator", [made])
    if undone:
        tools.keep_undelivered(name, [made], [seq], [fresh], shown, closed["at"])
        return {"ok": True, "seq": seq}
    ended = [(job_id, question) for _, job_id, question in tools.stamped(name, [made], [seq])]
    tools.keep_undelivered(name, [made], [seq], [ended], shown, closed["at"])
    tapped_key = view.message_key(ask["url"])
    superseded.follow(name, [(job_id, question) for job_id, question in ended if (view.message_key(question["card"]) != tapped_key if question["card"] else question["waits"] != ask["waits"])])
    return {"ok": True, "seq": seq}


def answered_at(data: dict, drawn: list[tuple[str, dict]], url: str) -> list[tuple[str, dict]]:
    """Each question his answer on the card at this link ended, the ones his Undo there takes back: each drawn on it that ended answered by his tap on it or his words at or under it, whichever link the close stored, and each question of any job answered on the same record, by its seq, such as a twin or a copy `drive job ask` settled on it, while an older question it superseded keeps the card that closed showing his words."""
    from drive import view

    taken = [
        (job_id, question)
        for job_id, question in drawn
        if (closed := question["closed"]) and closed["how"] == "answered" and closed["link"] and (view.message_key(closed["link"]) == view.message_key(url) or view.under(closed["link"], url))
    ]
    seqs = {question["closed"]["seq"] for _, question in taken} - {None}
    return [
        *taken,
        *(
            (job_id, question)
            for job_id, job in data["jobs"].items()
            for question in job["questions"]
            if (closed := question["closed"]) and closed["how"] == "answered" and closed["seq"] in seqs and not any(question is each for _, each in taken)
        ),
    ]


def card_question(drawn: list[tuple[str, dict]], waits: str) -> dict:
    """The question a card asks among those drawn on it, one under its waits id first and, among those, one no other question replaced, the title his answer there is recorded under."""
    return min((question for _, question in drawn), key=lambda question: (question["waits"] != waits, bool(question["replaced"])))


def hear_tap(name: str) -> None:
    """Record him heard on the drive his tap reached, since a tap is word from him as a message is."""
    from drive import watch

    watch.hear(name, state.now())


def respond(request: dict) -> dict:
    match request:
        case {"prs": asked}:
            return cards(asked, request.get("redraw", False))
        case {"numbers": numbers}:
            return numbered(numbers)
        case {"shown": asks}:
            return shown(asks)
        case {"refused": asks}:
            return refused(asks)
        case {"answer": ask}:
            return answered(ask)
    raise state.DriveError(f"not a card request: {', '.join(request) or 'empty'}")


def main(stdin: TextIO, stdout: TextIO) -> int:
    """Answer one cc-slack card_command request read from stdin with one JSON reply on stdout."""
    text = stdin.read()
    try:
        request = json.loads(text)
    except json.JSONDecodeError:
        held = "nothing" if not text.strip() else "no JSON"
        raise state.DriveError(f"drive card answers cc-slack's card_command: pipe it one request JSON on stdin, such as {{\"prs\": [...]}}; stdin held {held}") from None
    json.dump(respond(request), stdout)
    stdout.write("\n")
    return 0
