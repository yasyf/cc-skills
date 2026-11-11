import json
import re
import sys
import unicodedata
from typing import TextIO
from urllib.parse import urlsplit

from drive import github, limits, picture, state
from drive.github import GithubError, run
from drive.render import cut

FIELDS = "title,body,comments,state,isDraft,headRefOid,baseRefName,headRefName,statusCheckRollup,reviewDecision,url"
TAP_LABELS = ("Approve", "Hold")
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
STACK_MOST = 4
HATE_HEADINGS = ("what you'll hate most", "what you will hate most", "what you'll hate the most", "what you will hate the most")
PLAN_HEADINGS = ("against the plan", "differs from the plan", "plan deviations")
HIS_WORDS_HEADING = "his words, step by step"
RELEASABLE_HEADING = "releasable alone"
RISK_HEADINGS = ("risk", "risk profile", "risks")
RELEASABLE_NEED = (
    'a "Releasable alone" section saying why a release cut at its merge commit is safe by itself, in any target order,'
    " with nothing run after it"
)
CONVENTIONAL_KINDS = {
    "fix": "Fix",
    "feat": "New",
    **dict.fromkeys(("refactor", "chore", "docs", "test", "ci", "build", "perf"), "Cleanup"),
}
GITMOJI_KINDS = {
    **dict.fromkeys("✨🎉🚸", "New"),
    **dict.fromkeys("🐛🚑🩹🔒💚", "Fix"),
    **dict.fromkeys("♻🔥⚡📝✅🔧🏷🚚🧹👷📌⬆", "Cleanup"),
}

KIND_WORD = re.compile(rf"({'|'.join(KINDS)}):\s*", re.IGNORECASE)
CONVENTIONAL = re.compile(r"([a-z]+)(?:\([^)]*\))?!?:\s*")
SCOPED = re.compile(r"([a-z0-9][a-z0-9_./-]*):\s*(\S)")
HEADING = re.compile(r"#{1,2}[ \t]+(.+?)[ \t#]*")
REFRESHER_HEADING = re.compile(r"^#{2,3}[ \t]+(?:Refresher|ELI5)[ \t]*$", re.MULTILINE | re.IGNORECASE)
NEXT_HEADING = re.compile(r"^#{1,3}[ \t]", re.MULTILINE)
FENCE_LINE = re.compile(r"[ \t]*```")
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
SHAREABLE_PATH = re.compile(r"/(?:demo|public)[^/]*(?:/|$)", re.IGNORECASE)
LABELLED_LINE = re.compile(r"[ \t]*(?:[-*+][ \t]+)?(?:\*\*)?(?:install|risk|prove)(?:\*\*)?[ \t]*:(?:\*\*)?.*", re.IGNORECASE)
TRAILER = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+:[ \t]+\S.*")
HTML_TAG = re.compile(r"</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?/?>")
LIST_ITEM = re.compile(r"(?:[-*+]|\d+[.)])\s")
SENTENCE_END = re.compile(r"[.!?](?=\s|$)")
SENTENCES = re.compile(r"(?<=[.!?])\s+")
PARENTHETICAL = re.compile(r"\s*\([^()\n]*\)")
NOTHING = re.compile(
    r"(?:none|nothing(?: differ(?:s|ed))?|no (?:departures?|deviations?)(?: from the plan)?|n/a)[.!]?", re.IGNORECASE
)
NO_ORDER = re.compile(r"(?:none|nothing|n/a)\b", re.IGNORECASE)
RECORD_ID = re.compile(
    r"\b(?:cc-notes|ccn|cci)\s+(?:task|note|answer|doc|plan|log|record|seq|row)s?\b"
    r"|\bcci\s+#?\d+"
    r"|\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b"
    r"|(?<![\d,.$#])\b\d{6,}\b(?![,.]\d)"
    r"|\b[ojm]\d{1,5}\b"
    r"|\brmw[0-9a-z]{4,}\b|\bcard-[0-9a-z][0-9a-z-]*"
)
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
        return GITMOJI_KINDS.get(scoped[2], "Change"), without_emoji(title[scoped.end(1) + 1 :])
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
            return clipped(first_paragraph(without_records(HTML_TAG.sub("", rest[: end.start() if end else None]))))
    return ""


def show_me_words(text: str, pictured: bool) -> str:
    """A Show me's words on his card: one short caption, and its first code block when no picture draws."""
    bare = picture.FENCE.sub("", picture.IMAGE.sub("", text))
    first = SENTENCES.split(first_paragraph(without_records(bare)), maxsplit=1)[0].strip()
    readable = first and not first.startswith("|") and "`" not in first and len(first) <= CAPTION_CHARS
    shown = [first] if readable else []
    code = next((match for match in picture.FENCE.finditer(text) if match[1].lower() not in picture.DIAGRAMS and not picture.raw(match[1], match[2])), None)
    if code and not pictured:
        shown.append("```\n" + code_clipped(code[2].strip("\n")) + "\n```")
    return "\n".join(shown)


def code_clipped(code: str) -> str:
    if len(code) <= CODE_CHARS:
        return code
    end = code.rfind("\n", 0, CODE_CHARS)
    return code[: end if end > 0 else CODE_CHARS].rstrip() + "\n…"


def description(body: str) -> str:
    text = body.strip()
    return text if len(text) <= DESCRIPTION_CHARS else text[: DESCRIPTION_CHARS - 1].rstrip() + "…"


def checks(pr: dict, entry: dict) -> str:
    return github.checks(pr["statusCheckRollup"], github.failed_before(entry, pr["headRefOid"]))


def tap_label(entry: dict) -> str | None:
    return (entry.get("tap") or {}).get("label")


def offers_approval(pr: dict, entry: dict) -> bool:
    return pr["state"] == "OPEN" and entry["wants_tap"] and tap_label(entry) != "Approve" and not entry.get("exempt")


def waits_on(pr: dict, entry: dict) -> str:
    approved = tap_label(entry) == "Approve"
    match pr["state"], checks(pr, entry):
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


def lands_after_tap(pr: dict, entry: dict) -> str:
    return "you approve" if checks(pr, entry) == "pass" else "you approve and CI passes"


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


def render(pr: dict, entry: dict, shown: tuple[str, ...] = ()) -> dict:
    """The cc-slack PR card for a `gh pr view` payload, the drive's entry for that PR, and its drawn Show me pictures."""
    kind, plain = kind_and_title(pr["title"])
    body = body_of(pr)
    found = {heading: HTML_TAG.sub("", text) for heading, text in sections(body).items()}
    row = {
        "url": pr["url"],
        "title": plain,
        "head": pr["headRefOid"],
        "changed": entry.get("fundamental") or "",
        "refresher": section_words(found, *REFRESHER_HEADINGS) or comment_refresher(pr),
        "surprise": section_words(found, "biggest surprise"),
        "hate_most": section_words(found, *HATE_HEADINGS),
        "show_me": show_me_words(found.get("show me", ""), bool(shown)),
        "pictures": list(shown),
        "plan": section_words(found, *PLAN_HEADINGS),
        "port": section_words(found, "port"),
        "blocks": "",
        "lands": "",
        "description": description(body),
    }
    card = {"status": "info", "title": framed(f"{kind}: ", plain), "prs": [row]}
    if offers_approval(pr, entry):
        row["blocks"] = "Held by you" if tap_label(entry) == "Hold" else ""
        row["lands"] = lands_after_tap(pr, entry)
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
    if shown:
        card["pictures"] = list(shown)
    return card


def names_his_order(pr: dict) -> bool:
    words = first_paragraph(sections(body_of(pr)).get(HIS_WORDS_HEADING, ""))
    return bool(words) and not NO_ORDER.match(words.lstrip("-*+ "))


def releasable_needs(pr: dict) -> list[str]:
    """What the PR's body lacks to show a release cut at its merge commit is safe alone: its "Releasable alone" section, and install, risk and prove lines that leave no step for after the release."""
    body = body_of(pr)
    found = sections(body)
    alone = first_paragraph(found.get(RELEASABLE_HEADING, ""))
    if not alone or NO_ORDER.match(alone.lstrip("-*+ ")):
        return [RELEASABLE_NEED]
    fenced, labelled = False, []
    for line in body.splitlines():
        if FENCE_LINE.match(line):
            fenced = not fenced
        elif not fenced and LABELLED_LINE.fullmatch(line):
            labelled.append(line)
    said = "\n".join([found[RELEASABLE_HEADING], *(found.get(heading, "") for heading in RISK_HEADINGS), *labelled])
    return list(dict.fromkeys(f'no step left for after its release, but it says "{step}" in "{cut(line, 120)}"' for step, line in limits.later_steps(said)))


def stack_words(pr: dict) -> tuple[str, str]:
    """The stack's one-line title and its summary, from the `## The stack` section of its bottom PR's body."""
    text = without_records(HTML_TAG.sub("", sections(body_of(pr)).get(STACK_HEADING, ""))).strip()
    title, _, rest = text.partition("\n")
    return title.strip(), clipped(" ".join(rest.split()), STACK_CHARS)


def render_stack(members: list[tuple[dict, dict, tuple[str, ...]]]) -> dict:
    """One card for a stack of PRs, bottom first: its title and summary from the bottom PR, a section per PR, and one Approve for all."""
    drawn = [render(pr, entry, shown) for pr, entry, shown in members]
    kinds = [kind_and_title(pr["title"]) for pr, _, _ in members]
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
            "waits": stack_waits([state.pr_key(pr["url"]) for pr, _, _ in members]),
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


def read(key: str) -> dict | None:
    repo, number = key.split("#")
    try:
        return json.loads(run(["gh", "pr", "view", number, "-R", repo, "--json", FIELDS]))
    except (GithubError, json.JSONDecodeError) as error:
        print(f"drive card: {key}: {error}", file=sys.stderr)
        return None


def exposure(key: str) -> str:
    """What a PR on a public repository shows that only a private place may: each picture outside a demo or public path, and each private repository's PR its body names."""
    repo = key.split("#")[0]
    if github.visibility(repo) != "public":
        return ""
    if not (pr := read(key)):
        raise state.DriveError(f"{key}: could not read its body from GitHub to check what it shows in public")
    body = pr["body"] or ""
    pictures = [url for match in picture.IMAGE.finditer(body) if not SHAREABLE_PATH.search(urlsplit(url := match[1] or match[2]).path)]
    named = {f"{owner}/{name}" for owner, name, _ in (*state.PR_URL.findall(body), *state.PR_KEY.findall(body))} - {repo}
    hidden = sorted(other for other in named if github.visibility(other) not in (None, "public"))
    found = []
    if pictures:
        found.append(f"pictures outside a demo or public path ({', '.join(pictures)})")
    if hidden:
        found.append(f"PRs of the private {', '.join(hidden)}")
    if not found:
        return ""
    return (
        f"{repo} is public, and its body shows {' and '.join(found)}. A public PR's pictures come from a demo drive or"
        " public data, kept under a demo- or public- path; a picture of the live drive, a private repository, Slack or a"
        " customer's data goes only to a private repository's PR or his DM. Edit the body, delete each earlier revision"
        " that held them from its edit history, delete the branch that holds the pictures, then add it again"
    )


def show_me_problems(pr: dict) -> list[str]:
    """Why a PR body's Show me draws nothing worth a picture on his card, by the visual rules `drive job ask` holds a question's Show me to, each image in it counted as the picture it draws."""
    show_me = picture.IMAGE.sub(lambda image: f"![]({image[1] or image[2]})", sections(body_of(pr)).get("show me", ""))
    return limits.visual_problems(show_me.splitlines(), 0) if show_me.strip() else []


def pictured(name: str, key: str, pr: dict) -> tuple[str, ...]:
    repo, number = key.split("#")
    label = f"{repo.split('/')[1]} PR {number}"
    show_me = sections(body_of(pr)).get("show me", "")
    return tuple(picture.pictures(state.drive_dir(name) / "pictures", label, show_me))


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
    """Clears the refusal each member carries, so a body fixed after a refusal reads as drawn and a later refusal is told again."""
    for key in members:
        name, entry = known[key]
        if not entry.get("refused"):
            continue
        with state.edit(name) as data:
            data["prs"][key].pop("refused", None)
        entry.pop("refused")


def drawn_card(shown: list[tuple[dict, dict, tuple[str, ...]]]) -> dict:
    return render_stack(shown) if len(shown) > 1 else render(*shown[0])


def tooling(name: str, entry: dict) -> bool:
    """Whether the PR serves a drive tooling order, a fix to a tool the drive runs on that no words of his ask for, so its card quotes that order instead."""
    order = state.load(name)["orders"].get(entry.get("serves"))
    return order is not None and order["text"].startswith(state.TOOLING)


def refusal(shown: list[tuple[dict, dict, tuple[str, ...]]], card: dict, tools: set[str]) -> str:
    if len(shown) > STACK_MOST:
        return (
            f"its stack holds {len(shown)} PRs and a stack is at most {STACK_MOST}, so his card shows each in its own section"
            " with its pictures: split it into stacks of that size, or fold PRs that carry one decision into one"
        )
    needs: dict[str, list[str]] = {}
    if len(shown) > 1 and card["status"] == "needs_you" and not stack_words(shown[0][0])[0]:
        needs[shown[0][0]["url"]] = ["a \"The stack\" section with the stack's one-line title then its summary"]
    unordered, unreleasable = [], []
    for (pr, entry, _), row in zip(shown, card["prs"], strict=True):
        if not offers_approval(pr, entry):
            continue
        if not row["hate_most"]:
            needs.setdefault(pr["url"], []).append("a \"What you'll hate most\" section")
        if shows := show_me_problems(pr):
            needs.setdefault(pr["url"], []).append(f"a Show me whose every picture shows what its words cannot ({'; '.join(shows)})")
        if not names_his_order(pr) and pr["url"] not in tools:
            needs.setdefault(pr["url"], []).append("a \"His words, step by step\" list naming his order")
            unordered.append(pr["url"])
        if releasable := releasable_needs(pr):
            needs.setdefault(pr["url"], []).extend(releasable)
            unreleasable.append(pr["url"])
    if not needs:
        return ""
    wanted = "; ".join(f"{' and '.join(each)} in the body of {url}" for url, each in needs.items())
    if unreleasable:
        wanted += (
            f". Dev must always be releasable ({limits.RELEASABLE_RULING}): a change lands only when a release cut at its"
            " merge commit is safe by itself, in any target order, with no later step; split it until each PR is, and let a"
            ' PROVE line say what to open once it is live, as "PROVE: after release, open the run page", never a step to run'
        )
    if not unordered:
        return f"its tap card needs {wanted}"
    return (
        f"its tap card needs {wanted}. No order of his names {', '.join(unordered)}: its \"His words, step by step\" list"
        " is missing, empty or says none, so it is a port, which lands with no card; drive tooling, which serves its own"
        f" order with drive pr set <url> --tap --serves <the order opening \"{state.TOOLING}\"> and lands on his Approve,"
        " or lands under an exempt class; or the drive's to drop. Never edit its body to quote his words for another change"
    )


def waiting(known: dict[str, tuple[str, dict]], members: list[str]) -> str:
    """Why no Approve is drawn for the members yet: each design card of his still open, or job not finished, one of them waits on."""
    from drive import view

    held = []
    for key in members:
        name, entry = known[key]
        held += view.holding(state.load(name), entry["waits"], view.live_cards(name))
    if not held:
        return ""
    return f"it waits on {', '.join(dict.fromkeys(held))}, so it draws no Approve until each is answered or done"


def tap_refusal(known: dict[str, tuple[str, dict]], members: list[str]) -> str:
    shown, tools = [], set()
    for key in members:
        if not (pr := read(key)):
            raise state.DriveError(f"{key}: could not read it from GitHub to check its tap card")
        shown.append((pr, known[key][1], ()))
        if tooling(*known[key]):
            tools.add(pr["url"])
    return refusal(shown, drawn_card(shown), tools)


def cards(asked: list[dict]) -> dict:
    """One card per stack the asked PRs form, bottom PR first; a PR drawn on a stack card keeps that stack on every redraw, until the asked PRs chain into a longer stack around it."""
    known = drive_prs()
    asked_keys = keys_of(asked, known)
    for key in asked_keys:
        if key not in known:
            print(f"drive card: {key} is in no drive", file=sys.stderr)
    keys = [key for key in asked_keys if key in known]
    reads = {key: pr for key in keys if (pr := read(key))}
    skipped = [skip(key, "drive card could not read it from GitHub") for key in keys if key not in reads]
    stored = list({tuple(stack): stack for key in reads if (stack := known[key][1].get("stack"))}.values())
    grown = [chain for chain in chains(list(reads), reads) if grows(chain, stored)]
    kept = [stack for stack in stored if not any(set(stack) <= set(chain) for chain in grown)]
    covered = {key for group in grown + kept for key in group}
    fresh = chains([key for key in reads if key not in covered], reads)
    found = []
    for group in kept + grown + fresh:
        reads |= {key: pr for key in group if key not in reads and (pr := read(key))}
        members = [key for key in group if key in reads and key in known]
        if not members:
            continue
        if len(group) > 1 and group in fresh:
            remember(known, group)
        shown = [(reads[key], known[key][1], pictured(known[key][0], key, reads[key])) for key in members]
        card = drawn_card(shown)
        if reason := waiting(known, members) or refusal(shown, card, {reads[key]["url"] for key in members if tooling(*known[key])}):
            refusals = [skip(key, reason) for key in members]
            refused(refusals)
            skipped += refusals
            continue
        if group in grown:
            remember(known, group)
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
        state.append_event(name, "refused", **ask)
        told += 1
    return {"refused": told}


def answered(ask: dict) -> dict:
    known = drive_prs()
    key = keys_of([ask], known)[0]
    if key not in known:
        raise state.DriveError(f"{key} is not a drive PR")
    if ask["label"] not in TAP_LABELS:
        raise state.DriveError(f"a PR card answers Approve or Hold, not {ask['label']!r}")
    name, held = known[key]
    stack = held.get("stack") or []
    members = stack if stack and ask["waits"] == stack_waits(stack) else [key]
    with state.edit(name) as data:
        for member in members:
            entry = data["prs"][member]
            if entry["state"] != "open":
                continue
            entry["tap"] = {"label": ask["label"], "at": state.stamp(), "head": entry["head"], "source": ask["source"]}
            entry["fundamental"] = None
            entry["updated"] = entry["tap"]["at"]
            repo, number = member.split("#")
            state.append_event(
                name,
                "tap",
                repo=repo,
                number=int(number),
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
        return max(drives, key=lambda name: state.last_started(state.load(name)))


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
            return shown(asks)
        case {"refused": asks}:
            return refused(asks)
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
