import json
import os
import re
import sys
import unicodedata
from typing import TYPE_CHECKING, TextIO

from drive import github, limits, picture, state
from drive.github import GithubError, run
from drive.render import cut

if TYPE_CHECKING:
    from drive.stage import Stage

FIELDS = "title,body,author,state,isDraft,headRefOid,baseRefName,headRefName,reviewDecision,labels,url,additions,deletions,changedFiles"
READ_AGE = "60s"
KEPT_READS = "pr-reads"
REVIEW_DECISIONS = {"approved": "APPROVED", "stale": "REVIEW_REQUIRED", "changes": "CHANGES_REQUESTED", "none": ""}
TAP_LABELS = ("Approve", "Hold")
RELEASE_FAILED = (" failed", " went red", " partly released", " stopped")
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
HATE_HEADINGS = ("what you'll hate most", "what you will hate most", "what you'll hate the most", "what you will hate the most")
PLAN_HEADINGS = ("against the plan", "differs from the plan", "plan deviations")
SHOWN_HEADINGS = ("summary", *REFRESHER_HEADINGS, "biggest surprise", *HATE_HEADINGS, "show me", *PLAN_HEADINGS, "port", STACK_HEADING)
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
HIS = re.compile(r"\bhis\b")
HIM = re.compile(r"\bhim\b")
Member = tuple[dict, dict, tuple[str, ...], tuple[str, ...]]
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


def hold_labeled(pr: dict) -> bool:
    return github.HOLD_LABEL in [label["name"] for label in pr["labels"]]


def tap_refused(pr: dict, entry: dict) -> str | None:
    """Why his card on the PR offers no Approve, by GitHub's read of it now and by state.asks_tap; None when it does."""
    if pr["state"] != "OPEN":
        return f"GitHub reads it {pr['state'].lower()}"
    if hold_labeled(pr):
        return "it carries the hold label"
    return state.tap_refused(entry)


def waits_on(pr: dict, entry: dict, held: tuple[str, ...]) -> str:
    """What his card says under the PR: merged or closed as GitHub reads it, else its stage as stage.of names it when the card draws, in his card's words, a check's objection included, which never takes his Approve off it."""
    now, approved, key = entry["now"], tap_label(entry) == "Approve", state.pr_key(pr["url"])
    match pr["state"], now.name:
        case "MERGED", _:
            return "Merged"
        case "CLOSED", _:
            return "Closed without merging"
        case _, "held" if tap_label(entry) == "Hold":
            return "Held by you"
        case _, "held" if entry["hold_label"]:
            return "Held by the drive with the hold label on GitHub"
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
            return "Lands once " + in_turn(["CI passes", *held])
        case _, "held" | "waiting" if held:
            return "Lands once " + in_turn(list(held))
        case _, "ready" | "landing" if state.queued_by_lander(key):
            return "CI passed; the drive queues it for merge"
        case _, "ready" | "landing" | "author":
            return "CI passed; the agent is landing it"
    said = HIM.sub("you", HIS.sub("your", now.why))
    return said[:1].upper() + said[1:]


def lands_after_tap(pr: dict, entry: dict, held: tuple[str, ...]) -> str:
    """What his card says the PR lands once: his Approve, CI when it has not passed, and each of its waits that still holds."""
    return in_turn(["you approve", *([] if checks(pr, entry) == "pass" else ["CI passes"]), *held])


def in_turn(steps: list[str]) -> str:
    return " and ".join(steps) if len(steps) < 3 else ", ".join(steps[:-1]) + " and " + steps[-1]


def merge_waits(url: str) -> str:
    repo, number = state.pr_key(url).split("#")
    return f"merge:{repo.replace('/', ':')}:{number}"


def stack_waits(keys: list[str]) -> str:
    repo, bottom = keys[0].split("#")
    return f"merge:{repo.replace('/', ':')}:{bottom}-{keys[-1].split('#')[1]}"


def body_of(pr: dict) -> str:
    body = COMMENT.sub("", pr["body"] or "").replace("\r\n", "\n").rstrip()
    rest, _, last = body.rpartition("\n\n")
    return rest if rest and all(TRAILER.fullmatch(line.strip()) for line in last.splitlines()) else body


def render(pr: dict, entry: dict, shown: tuple[str, ...] = (), held: tuple[str, ...] = (), charts: int = limits.CHARTS) -> dict:
    """The cc-slack PR card for a `gh pr view` payload, the drive's entry for that PR, its drawn Show me pictures, what each of its waits that holds waits for, in his card's words, and how many ```chart fences its Show me may still draw on the card."""
    kind, plain = kind_and_title(pr["title"])
    body = body_of(pr)
    found = {heading: without_html(text) for heading, text in sections(body).items()}
    row = {
        "url": pr["url"],
        "title": plain,
        "head": pr["headRefOid"],
        "changed": entry.get("fundamental") or "",
        "review": f"Commit {entry['head'][:7]} goes against your words: {named}. Its agent is told; your Approve still lands it" if pr["state"] == "OPEN" and (named := state.against(entry)) else "",
        "summary": section_words(found, "summary"),
        "refresher": section_words(found, *REFRESHER_HEADINGS) or comment_refresher(pr),
        "surprise": section_words(found, "biggest surprise"),
        "hate_most": section_words(found, *HATE_HEADINGS),
        "show_me": show_me_words(found.get("show me", ""), bool(shown), charts),
        "pictures": list(shown),
        "plan": section_words(found, *PLAN_HEADINGS),
        "port": section_words(found, "port"),
        "blocks": "",
        "lands": "",
        "description": description(body),
    }
    card = {"status": "info", "title": framed(f"{kind}: ", plain), "prs": [row]}
    if tap_refused(pr, entry) is None:
        row["lands"] = lands_after_tap(pr, entry, held)
        card |= {
            "status": "needs_you",
            "title": framed(f"{kind}: approve ", plain, "?"),
            "options": list(TAP_LABELS),
            "recommended": "Approve",
            "waits": merge_waits(pr["url"]),
        }
    else:
        card["status"] = "done" if pr["state"] == "MERGED" else "info"
    if not row["lands"]:
        row["blocks"] = ("Approved by you. " if tap_label(entry) == "Approve" and pr["state"] == "OPEN" else "") + waits_on(pr, entry, held)
    if shown:
        card["pictures"] = list(shown)
    return card


def stack_words(pr: dict) -> tuple[str, str]:
    """The stack's one-line title and its summary, from the `## The stack` section of its bottom PR's body."""
    text = without_records(without_html(sections(body_of(pr)).get(STACK_HEADING, ""))).strip()
    title, _, rest = text.partition("\n")
    return title.strip(), clipped(" ".join(rest.split()), STACK_CHARS)


def render_stack(members: list[Member]) -> dict:
    """One card for a stack of PRs, bottom first: its title and summary from the bottom PR, a section per PR, and one Approve for the PRs still asking his tap, each other's section saying what holds it, its PRs' Show me charts at most the ones one Slack message draws, since cc-slack posts the whole stack as one."""
    drawn, charts = [], limits.CHARTS
    for member in members:
        drawn.append(one := render(*member, charts=charts))
        charts -= sum(fence[1].lower() in picture.NATIVE for fence in picture.FENCE.finditer(one["prs"][0]["show_me"]))
    kinds = [kind_and_title(member[0]["title"]) for member in members]
    kind = next((each for each in STACK_KINDS if any(each == found for found, _ in kinds)), "Change")
    title, summary = stack_words(members[0][0])
    count = len(members)
    card = {"status": "info", "title": framed(f"{kind}: ", title) if title else framed(f"{kind}: {count} PRs: ", kinds[0][1]), "prs": [one["prs"][0] for one in drawn]}
    if summary:
        card["body"] = summary
    if any(one["status"] == "needs_you" for one in drawn):
        card |= {
            "status": "needs_you",
            "options": list(TAP_LABELS),
            "recommended": "Approve",
            "waits": stack_waits([state.pr_key(member[0]["url"]) for member in members]),
        }
    elif all(one["status"] == "done" for one in drawn):
        card["status"] = "done"
    if shown := [path for one in drawn for path in one.get("pictures", [])]:
        card["pictures"] = shown
    return card


def drive_prs() -> dict[str, tuple[str, dict]]:
    """Each PR the drives track, with the drive whose entry for it was updated last, since a drive copied from another keeps the older copy of each entry."""
    known: dict[str, tuple[str, dict]] = {}
    for name in state.names():
        for key, entry in state.load(name)["prs"].items():
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
    """The PR's card fields through ccx's GitHub cache, which answers the same read made within READ_AGE, each read kept on disk, so a read GitHub refuses, as under its rate-limit floor, draws from the copy kept at the last read with the state and head the drive's entry holds; None only for a PR never read. Its checks come from the drive's entry, and its comments are read over REST: gh's comments field also asks for each comment's viewerDidAuthor, so ccx sends that read to his own GraphQL budget instead of the GitHub App's."""
    repo, number = key.split("#")
    kept = state.root() / KEPT_READS / repo / f"{number}.json"
    try:
        pr = json.loads(run(["gh", "pr", "view", number, "-R", repo, "--json", FIELDS], max_age=READ_AGE))
        pr["comments"] = json.loads(run(["gh", "api", "--paginate", f"repos/{repo}/issues/{number}/comments?per_page=100"], max_age=READ_AGE))
    except (GithubError, json.JSONDecodeError) as error:
        print(f"drive card: {key}: {error}", file=sys.stderr)
        if not kept.is_file():
            return None
        pr = json.loads(kept.read_text())
        return pr | {"state": entry["state"].upper(), "headRefOid": entry["head"] or pr["headRefOid"]} if entry else pr
    kept.parent.mkdir(parents=True, exist_ok=True)
    written = kept.with_name(f"{kept.name}.{os.getpid()}")
    written.write_text(json.dumps(pr))
    os.replace(written, kept)
    return pr


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
    }


def pictured(name: str, key: str, pr: dict, taken: set[str]) -> tuple[str, ...]:
    repo, number = key.split("#")
    label = f"{repo.split('/')[1]} PR {number}"
    show_me = sections(body_of(pr)).get("show me", "")
    return tuple(picture.pictures(state.drive_dir(name) / "pictures", label, show_me, taken))


def groups(known: dict[str, tuple[str, dict]], keys: list[str], reads: dict[str, dict]) -> list[list[str]]:
    """The cards the asked PRs draw on, bottom PR first. A PR in a native GitHub stack, the one stack-enqueue and his Approve land, draws with every PR of it the drives track that is not closed, split by the card each was last drawn on, so a PR added to the stack later gets its own card. Any other read PR draws on its loose_cards() card, the rule state.card_unit names: its chain by base branch among them, else the PRs among them of its repository serving its order, keeping the card it was drawn on, closed and natively stacked PRs left out, until they group into a longer card around it."""
    native = github.stacks(list(dict.fromkeys([*keys, *(mate for key in keys for mate in known[key][1].get("stack") or [] if mate in known)])))
    stacked = {member for stack in native.values() for member, _ in stack}
    units = dict.fromkeys(tuple(member for member, status in native[key] if status != "closed" and member in known) for key in keys if key in native)
    by_card: dict[tuple, list[str]] = {}
    for unit in units:
        for member in unit:
            by_card.setdefault((unit, tuple(mate for mate in known[member][1].get("stack") or [] if mate in unit)), []).append(member)
    loose = [key for key in reads if key not in stacked]
    stored = list({tuple(stack): stack for key in loose if (stack := [mate for mate in known[key][1].get("stack") or [] if mate in known and mate not in stacked and known[mate][1]["state"] != "closed"])}.values())
    grown = [group for group in loose_cards(known, loose, reads) if grows(group, stored)]
    kept = [stack for stack in stored if not any(set(stack) <= set(group) for group in grown)]
    covered = {key for group in grown + kept for key in group}
    fresh = loose_cards(known, [key for key in loose if key not in covered], reads)
    return [group for group in by_card.values() if not set(group).isdisjoint(keys)] + kept + grown + fresh


def loose_cards(known: dict[str, tuple[str, dict]], keys: list[str], reads: dict[str, dict]) -> list[list[str]]:
    """The cards PRs in no native GitHub stack draw on, by state.card_unit: each chain of them by base branch on its own card, bottom first, and the rest one card per repository and order they serve, however many PRs each holds."""
    units: dict[tuple[str, tuple[tuple[str, ...], str, str]], list[str]] = {}
    for chain in chains(keys, reads):
        stack = tuple(chain) if len(chain) > 1 else ()
        for key in chain:
            units.setdefault((known[key][0], state.card_unit(key, stack, known[key][1])), []).append(key)
    return list(units.values())


def group_of(known: dict[str, tuple[str, dict]], key: str) -> list[str]:
    """The PRs his card on the PR draws it with, bottom first."""
    reads = {key: pr} if (pr := read(key, known[key][1]) or watched(known[key][1])) else {}
    return next((group for group in groups(known, [key], reads) if key in group), [key])


def chains(keys: list[str], reads: dict[str, dict]) -> list[list[str]]:
    """The asked PRs grouped into stacks, bottom first: a PR sits on another when its base branch is the other's head."""
    by_head = {(state.pr_key(reads[key]["url"]).split("#")[0], reads[key]["headRefName"]): key for key in keys}
    below = {key: by_head.get((key.split("#")[0], reads[key]["baseRefName"])) for key in keys}
    above = {under: key for key, under in below.items() if under}
    found: list[list[str]] = []
    for key in keys:
        if below[key]:
            continue
        chain = [key]
        while (nxt := above.get(chain[-1])) and nxt not in chain:
            chain.append(nxt)
        found.append(chain)
    placed = {key for chain in found for key in chain}
    return found + [[key] for key in keys if key not in placed]


def grows(chain: list[str], stored: list[list[str]]) -> bool:
    """Whether the chain takes in a stored stack and more, and every stored stack it touches whole, so no PR lands on two cards."""
    touched = [stack for stack in stored if set(stack) & set(chain)]
    return any(set(stack) < set(chain) for stack in touched) and all(set(stack) <= set(chain) for stack in touched)


def remember(known: dict[str, tuple[str, dict]], stack: list[str]) -> None:
    for key in stack:
        name = known[key][0]
        with state.edit(name) as data:
            data["prs"][key]["stack"] = stack
        known[key][1]["stack"] = stack


def drawn(known: dict[str, tuple[str, dict]], members: list[str]) -> None:
    """Clears the refusal each member carries, so a PR drawn after a refusal reads as drawn and a later refusal is told again."""
    for key in members:
        name, entry = known[key]
        if not entry.get("refused"):
            continue
        with state.edit(name) as data:
            data["prs"][key].pop("refused", None)
        entry.pop("refused")


def drawn_card(shown: list[Member]) -> dict:
    return render_stack(shown) if len(shown) > 1 else render(*shown[0])


def holding(known: dict[str, tuple[str, dict]], key: str) -> dict[str, str]:
    """Each wait of the PR that still holds, with what it waits for in his card's words."""
    from drive import view

    name, entry = known[key]
    data, found = state.load(name), view.live_cards(name)
    return {wait: view.wait_words(data, wait, found) for wait in view.holding(data, entry["waits"], found)}


def untapped(known: dict[str, tuple[str, dict]], reads: dict[str, dict], members: list[str]) -> str:
    """Why no fresh card is drawn for the members: none of them asks his tap, so the card would offer him no Approve, and the post linking them keeps its plain link, as an exempt PR gets no PR card."""
    refusals = {key: tap_refused(reads[key], known[key][1]) for key in members}
    if None in refusals.values():
        return ""
    return "; ".join(f"{key}: {why}" if len(members) > 1 else why for key, why in refusals.items()) + ", so no PR card draws, since it would offer him no Approve"


def standing(known: dict[str, tuple[str, dict]], key: str) -> "Stage":
    """The PR's stage as his card on it draws, which that card shows under it."""
    from drive import stage, view

    name = known[key][0]
    return stage.on_card(state.load(name), key, view.live_cards(name))


def reworked(known: dict[str, tuple[str, dict]], members: list[str]) -> str:
    """Why no card is drawn for the members, a redraw of one cc-slack posted included: one of them is being reworked to his words, so its card stays down until its builder's READY says how they are met."""
    return "; ".join(
        f"{key if len(members) > 1 else 'it'} is being reworked to his words \"{entry['rework']['words']}\", so no card asks his tap until its builder's READY says how they are met"
        for key in members
        if (entry := known[key][1])["state"] == "open" and entry["rework"]
    )


def foreign(known: dict[str, tuple[str, dict]], members: list[str]) -> str:
    """Why no card is drawn for the members: one of them is someone else's PR that no order of his it serves names, which the drive never cards."""
    return "; ".join(why for key in members if (why := state.foreign_pr(state.load(known[key][0]), key)))


def on_his_card(known: dict[str, tuple[str, dict]], reads: dict[str, dict], members: list[str]) -> bool:
    """Whether an open card of his already carries one of the PRs: a wait redraws that card in place, Approve and Hold kept, and never draws a fresh one."""
    from drive import view

    listed = ((view.live_cards(known[key][0]), reads[key]["url"]) for key in members)
    return any(found is not None and view.card_for(found["pr_cards"], url) is not None for found, url in listed)


def cards(asked: list[dict], redraw: bool = False) -> dict:
    """One card per stack the asked PRs form, by groups(), bottom PR first, each card drawn kept on its PRs so Approve lands exactly those, and a fresh one only when tap_refused finds one of them asking his Approve, the same check that puts Approve on a card. A redraw of a card cc-slack already posted draws it as the PRs stand now, a wait that holds back a fresh Approve shown on it rather than refusing it. A PR body's sections are drawn as they stand and never hold a card, but a Show me picture that does not draw, such as a Mermaid diagram Mermaid cannot parse, refuses its stack's card with why, which `refused` tells the session hearing its order, since a card without the picture shows him nothing for it."""
    known = drive_prs()
    asked_keys = keys_of(asked, known)
    for key in asked_keys:
        if key not in known:
            print(f"drive card: {key} is in no drive", file=sys.stderr)
    keys = [key for key in asked_keys if key in known]
    reads = {key: pr for key in keys if (pr := read(key, known[key][1]) or watched(known[key][1]))}
    skipped = [skip(key, "drive card could not read it from GitHub") for key in keys if key not in reads]
    try:
        stacks = groups(known, list(reads), reads)
    except GithubError as error:
        return {"cards": [], "skipped": skipped + [skip(key, str(error)) for key in reads]}
    found = []
    for group in stacks:
        reads |= {key: pr for key in group if key not in reads and (pr := read(key, known[key][1]) or watched(known[key][1]))}
        members = [key for key in group if key in reads]
        if not members:
            continue
        fresh = not redraw and not on_his_card(known, reads, members)
        reason = foreign(known, members) or reworked(known, members)
        if fresh and not reason and (off := untapped(known, reads, members)):
            skipped += [skip(key, off) for key in members]
            continue
        if not reason:
            taken: set[str] = set()
            try:
                card = drawn_card(
                    [(reads[key], known[key][1] | {"now": standing(known, key)}, pictured(known[key][0], key, reads[key], taken), tuple(holding(known, key).values())) for key in members]
                )
            except state.DriveError as error:
                reason = str(error)
        if reason:
            refusals = [skip(key, reason) for key in members]
            refused(refusals)
            skipped += refusals
            continue
        remember(known, members)
        drawn(known, members)
        repo, number = members[0].split("#")
        found.append({"repo": repo, "number": int(number), "card": card})
    return {"cards": found, "skipped": skipped}


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
    known = drive_prs()
    told = 0
    for ask, key in zip(asks, keys_of(asks, known), strict=True):
        if key not in known:
            continue
        name = known[key][0]
        with state.edit(name) as data:
            entry = data["prs"][key]
            if entry.get("refused") == ask["reason"]:
                continue
            entry["refused"] = ask["reason"]
            lane = state.heard_lane(data, entry["order"])
        state.append_event(name, "refused", **ask, lane=lane)
        told += 1
    return {"refused": told}


def answered(ask: dict) -> dict:
    """Keep his tap on a PR card on each open PR it covers, one tap event each, and start the lander on his Approve; a stack card's tap covers only the PRs on it holding no tap yet, as its Approve and Hold ask only for those, so his words approving or holding one PR keep it through a later tap on the card, and cc-slack applies a tap again when its first card command did not finish, so a member already holding this tap's Slack action keeps it and gets no second event."""
    if "repo" not in ask:
        return question_answered(ask)
    known = drive_prs()
    key = keys_of([ask], known)[0]
    if key not in known:
        raise state.DriveError(f"{key} is not a drive PR")
    if ask["label"] not in TAP_LABELS:
        raise state.DriveError(f"a PR card answers Approve or Hold, not {ask['label']!r}")
    name, held = known[key]
    hear_tap(name)
    stack = held.get("stack") or []
    members = stack if stack and ask["waits"] == stack_waits(stack) else [key]
    with state.edit(name) as data:
        for member in members:
            entry = data["prs"][member]
            if entry["state"] != "open" or len(members) > 1 and entry["tap"] or ask["action_ts"] and (entry["tap"] or {}).get("action_ts") == ask["action_ts"]:
                continue
            entry["tap"] = state.new_tap(ask["label"], ask["heads"].get(member, entry["head"]), ask["source"], ask["action_ts"])
            entry["fundamental"] = None
            entry["updated"] = entry["tap"]["at"]
            repo, number = member.split("#")
            state.append_event(
                name,
                "tap",
                repo=repo,
                number=int(number),
                label=ask["label"],
                head=entry["tap"]["head"],
                source=ask["source"],
                action_ts=ask["action_ts"],
                lane=state.heard_lane(data, entry["order"]),
            )
        from drive import stage

        members = [*members, *stage.adopt(data)]
    if ask["label"] == "Approve":
        from drive import land, view

        land.start(name, members, view.live_cards(name))
    return {"ok": True}


def question_answered(ask: dict) -> dict:
    """End the question his tap on a drive's question card answers, by a button, its number typed in its thread or his words in its Other box, and post it as the one answer record the coordinator gets for it: `--re` its ask when the waits id names one, else `topic: card:<thread_ts>`, idempotent by his Slack action, so cc-slack runs this again until the drive takes it and shows the tap recorded only then. The question ends in the write after the post, each older open question of its job his answer decides, as Jev reads it, ends superseded, and each other card drawn for an ended question follows; cc-slack closes the tapped card itself. An Undo reopens each question on the card his tap answered, which cc-slack redraws. Kept on the job until drive job resume carries it there; History keeps it, or its undo, as his decision, once: a run again after one the card command's two minutes cut short finds his answer recorded by its record and adds nothing. A waits id naming no drive is not the drive's to record: `ok` false, so cc-slack hands the tap to the session it reached."""
    from drive import superseded, tools, view

    name, _, _ = ask["waits"].partition(":")
    if name not in state.names():
        return {"ok": False, "why": f"{ask['waits']} is no drive's question card, so his tap on it stays with the session it reached"}
    hear_tap(name)
    undone = bool(ask.get("undone"))
    item = {"kind": "answer", "text": ask["label"], "url": ask["url"], "undone": undone}
    item |= {"waits": ask["waits"]} if state.WAITS_ID.fullmatch(ask["waits"]) else {"topic": f"card:{view.card_thread(ask['url'])}"}
    item |= {"attrs": {"maps": "none"}} if ask.get("other") else {}
    item |= {"id": f"tap:{ask['waits']}:{ask['action_ts']}{':undone' if undone else ''}"} if ask.get("action_ts") else {}
    data = state.load(name)
    made = tools.post(item, data, "root", (data["roles"].get("communicator") or {}).get("session"))
    [seq] = tools.sent(name, "communicator", [made])
    closed = state.new_close("answered", ask["label"], ask["url"], seq=seq)
    with state.edit(name) as data:
        on_card = view.drawn_on(data, ask["waits"], ask["url"])
        again = [(job_id, question) for job_id, question in on_card if (question["closed"] or {}).get("seq") == seq]
        if undone:
            fresh = [(job_id, question) for job_id, question in on_card if (question["closed"] or {}).get("how") == "answered" and question["closed"]["link"] == ask["url"]]
            for job_id, question in fresh:
                question["closed"] = None
                data["jobs"][job_id]["updated"] = state.stamp()
        else:
            fresh = [each for job_id, question in on_card if question["closed"] is None for each in superseded.end(data, job_id, question, closed)]
    ended = again + fresh
    tools.keep_undelivered(name, [made], [seq], [ended])
    shown = view.stored_cards(name)[0]
    card = view.asked_card(shown, ask["url"])
    if fresh or not on_card:
        state.append_event(name, "undone" if undone else "answered", url=ask["url"], label=ask["label"], question=card and card["question"], undo_until=ask.get("undo_until"))
    if undone:
        return {"ok": True, "seq": seq}
    data = state.load(name)
    tapped = [(job_id, state.question_in(data["jobs"][job_id], question)) for job_id, question in ended if question["closed"]["how"] == "answered"]
    decided = [(job_id, older, question) for job_id, question in tapped for older in superseded.decided_by(data, job_id, question, ask["label"], shown)]
    if decided:
        with state.edit(name) as data:
            ended += [
                each
                for job_id, older, question in decided
                if (kept := state.question_in(data["jobs"][job_id], older))["closed"] is None
                for each in superseded.end(data, job_id, kept, superseded.superseded_by(closed, question))
            ]
    tapped_key = view.message_key(ask["url"])
    superseded.follow(name, [(job_id, question) for job_id, question in ended if question["waits"] != ask["waits"] and not (question["card"] and view.message_key(question["card"]) == tapped_key)])
    return {"ok": True, "seq": seq}


def hear_tap(name: str) -> None:
    """Record him heard on the drive his tap reached, since a tap is word from him as a message is."""
    from drive import watch

    watch.hear(name, state.now())


def home_drive() -> str:
    try:
        return state.resolve()
    except state.DriveError:
        if not (drives := state.names()):
            raise
        return max(drives, key=lambda name: state.last_started(state.load(name)))


def released(release: dict) -> dict:
    """Reopen the stop of each open incident whose job named this release, the first time cc-slack hands it failing a step, with a job to get it live or the running stop job to resume."""
    if not any(part.endswith(RELEASE_FAILED) for part in release["line"].split(" · ")):
        return {"reopened": []}
    name, created, reopened = home_drive(), state.stamp(), {}
    with state.edit(name) as data:
        for order_id in state.open_incidents(data, list(data["orders"])):
            order = data["orders"][order_id]
            if (named := order["releases"].get(release["name"])) is None or named["failed"]:
                continue
            named["failed"] = created
            order["parts"][state.INCIDENT_STOP] = state.new_part()
            order["updated"] = created
            running = state.running_stops(data, order_id)
            reopened[order_id] = {"resume": running[0]} if running else {"job": state.incident_job(data, name, order_id, state.INCIDENT_STOP, created).split()[0]}
            reopened[order_id]["lane"] = state.heard_lane(data, order_id)
    for order_id, owner in reopened.items():
        state.append_event(name, "stop", order=order_id, release=release["name"], link=release["link"], **owner)
    return {"reopened": list(reopened)}


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
        case {"release": release}:
            return released(release)
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
