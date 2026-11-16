import json
import re
from pathlib import Path

from drive import dashboard, limits, picture, providers, reads, render, settled, state, view
from drive import slack as slack_api
from drive.limits import REF
from drive.state import DriveError

ACK = re.compile(r"\bAck: (https://\S+)")
RAW_ANSWER = re.compile(r"^\d+:\d+ commit-sha '([0-9a-f]{7,40})'")
OPEN_LINK = re.compile(r"<((?:https?://|mailto:)[^<>|\s]*)\|[^<>]*\Z")
INNER = re.compile(r"<[^<>]*>?")
CODE = re.compile(r"`[^`\n]+`")
LINK_WORDS = str.maketrans({"<": "", ">": "", "|": "/", "[": "(", "]": ")"})
THIS_CARD = "this card"
JOB_START = "(job start)"
DOUBLED_TIME = re.compile(r"(\b(?:1[0-2]|0?[1-9]):[0-5]\d\s?[AP]\.?M\.?)\s*(?:\(\{(?:started|at):[^{}]+\}\)|\{(?:started|at):[^{}]+\})", re.IGNORECASE)
CAUSE = re.compile(
    r"\b(?:because|caus(?:ed|es|ing)|due to|culprit)\b"
    r"|\bcause(?:\s+(?:is|was)|:)\s+(?!(?:yes|still|being|unknown|unclear|pending|tbd|not yet|under (?:way|investigation|review))\b|an?\s+(?:read|reader|lane|agent)\s+(?:is|has)\b)",
    re.IGNORECASE,
)
STATE = re.compile(
    r"\b(?:cover(?:s|ed)\s+(?:it|this|that|the|his|your|both|each|every|by)|fixed|merged|landed|(?:en)?queued|shipped|deployed|resolved|rolled (?:out|back))\b"
    r"|\b(?:is|are|was|were|went|now|already)\s+live\b",
    re.IGNORECASE,
)
UNVERIFIED = re.compile(r"\bunverified\b", re.IGNORECASE)
UNSTATED = re.compile(r"\b(?:once|when|after|until|unless|if|whether|not|never|yet|will|would|to be)\b|n['\u2019]t\b", re.IGNORECASE)
FAILURE = re.compile(
    r"\b(?:refused|failed to|missed|disconnected|dropped (?:its|the|their) connection|forgot(?:ten)?|lost track|fell through|went unchecked|slipped"
    r"|(?:nobody|no one) (?:\w+ed|ran|saw|read|took|got|drew|caught|knew)"
    r"|never (?:checked|ran|reached|heard|started|drew|posted|landed|got|went out))\b",
    re.IGNORECASE,
)
DRIVE_PART = re.compile(
    r"\b(?:cards?|checks?|hooks?|builders?|handlers?|designers?|watch|coordinator|communicator|meta|drive|lanes?|skill|cci|say)\b",
    re.IGNORECASE,
)
LATE = re.compile(r"\b(?:late|overdue)\b", re.IGNORECASE)
ORDER_ID = re.compile(r"\bo\d+\b")
PR_NUMBER = re.compile(r"(\d+)\Z")
QUOTED = re.compile(r'"[^"\n]*"|\u201c[^\u201d\n]*\u201d')
CLAUSE_END = re.compile(r"[,;:.!?](?=\s|$)")
ABSENT_RECORD = re.compile(
    r"\b(?:no|nothing in (?:the|his|your|any)|not in (?:the|his|your|any)|never in (?:the|his|your|any))\s+(?:[\w-]+\s+){0,3}?"
    r"(?:plans?|rulings?|recorded answers?|answers? of his|cc-notes|records? of (?:his|him|you|your))\b"
    r"|\b(?:plan|records?|rulings?)\s+(?:does|do|did)(?:\s*not|n['’]t)\s+(?:say|name|mention|ask|cover|settle)",
    re.IGNORECASE,
)
RECORD_WORDS = 10
SELF_CITED = (
    "refused: a line naming a cause cites the read that measured it, a job's evidence, mechanism, handoff or report, and these cite a"
    " record {lane}, the lane posting them, wrote itself, which restates its own reading: cite the record of the job that measured the"
    " cause, as {{record:<seq>|<what that read measured>}}, or leave the cause off the line until a job has measured it: {lines}"
)


def resolved(name: str, text: str) -> dict[str, dict]:
    """Each reference the text holds, read from the drive: {card:<waits id>}, {pr:<number, owner/repo#number or URL>}, {order:<id>}, {record:<seq>|<what that read measured>}, {answer:<id>}, and {started:<job>} and {at:<seq>}, the time a job started, labelled as the job's start, and the time a record went out, with the words that name it, the words after a | when it has them beside its own title, and where it opens; one that names nothing, a {record:<seq>} with no words of the poster's after its |, or a reference or link inside a raw link's words, refuses the post."""
    if refusal := nested(text):
        raise DriveError(refusal)
    found = {}
    cards = None
    carded = None
    data = None
    for match in REF.finditer(text):
        if match[0] in found:
            continue
        label = (match[3] or "").strip()
        data = data or state.load(name)
        match match[1]:
            case "card":
                cards = cards or drawn_cards(name)
                found[match[0]] = card(cards, data, state.waits_of(name, match[2]))
            case "pr":
                carded = carded if carded is not None else (view.live_cards(name) or view.NO_CARDS)["pr_cards"]
                found[match[0]] = pr(data["prs"], match[2], label, carded)
            case "order":
                found[match[0]] = order(name, data, match[2])
            case "record":
                found[match[0]] = record(name, match[2], label)
            case "answer":
                found[match[0]] = answer(data, match[2])
            case "started":
                found[match[0]] = started(data, match[2], label)
            case "at":
                found[match[0]] = posted_at(name, match[2])
            case "release":
                found[match[0]] = release(data, match[2])
        if label:
            found[match[0]] |= {"own": found[match[0]]["title"], "title": label}
    return found


RELEASE_POSTS_FILE = "release-posts.json"
LOOKED: dict[tuple[str, str], str | None] = {}
PR_MENTION = re.compile(
    r"<(?P<linked>https://github\.com/[\w.-]+/[\w.-]+/pull/\d+)[^|>]*\|(?P<words>[^>]+)>"
    r"|<?(?P<bare>https://github\.com/[\w.-]+/[\w.-]+/pull/\d+)[^\s<>|)]*>?"
    r"|(?<![\w/{:#])(?P<named>[\w.-]+/[\w.-]+#\d+|#\d+)\b"
)


def carded(name: str, text: str) -> str:
    """The text with each mention of a PR the drive holds an open card for, a GitHub pull link bare or on words, owner/repo#N or #N, written as its {pr:} reference, so say links the card wherever the post goes, by his words "Those ones with cards in my DM you should have linked to"; a PR with no open card, or one the drive does not hold, stays as written."""
    if not PR_MENTION.search(text):
        return text
    data = state.load(name, ("prs",))
    open_cards = (view.live_cards(name) or view.NO_CARDS)["pr_cards"]

    def carding(match: re.Match[str]) -> str:
        ref = match["linked"] or match["bare"] or match["named"]
        try:
            key = state.tracked_pr(data["prs"], ref)
        except DriveError:
            return match[0]
        if not view.head_card(data["prs"][key], open_cards):
            return match[0]
        return f"{{pr:{key}|{match['words']}}}" if match["words"] else f"{{pr:{key}}}"

    return PR_MENTION.sub(carding, text)


def nested(text: str) -> str | None:
    """Why the text, outside its code, writes a reference, link or mention inside the words of a raw <url|words> link, which Slack draws as a literal <, a link whose words are its own URL, and a literal >."""
    plain = CODE.sub("", picture.FENCE.sub("", text))
    for inner in sorted([*REF.finditer(plain), *INNER.finditer(plain)], key=lambda found: found.start()):
        if outer := OPEN_LINK.search(plain, 0, inner.start()):
            return (
                f"{inner[0]} sits inside the words of the link to {outer[1]}, and a link's words cannot hold a link, which Slack"
                f" draws broken: give that link a few words of its own and close it with >, then put {inner[0]} after it"
            )
    return None


def causes(text: str) -> list[str]:
    """Each line of the text, outside a fence, a quote and a quoted line, that states a cause: a because, due to, caused by, culprit or the cause is, never a line naming the cause as the question or as what a read is on."""
    return [line.strip() for line in picture.FENCE.sub("", text).splitlines() if not line.lstrip().startswith(">") and CAUSE.search(QUOTED.sub("", line))]


def unbacked(text: str) -> list[str]:
    """Each line of the text stating a cause, as causes finds them, with no {record:…} on it."""
    return [line for line in causes(text) if "{record:" not in line]


def self_cited(text: str, found: dict[str, dict], lane: str | None) -> list[str]:
    """Each line of the text stating a cause, as causes finds them, whose {record:…} names a record lane wrote, the lane whose words the line carries, which restates that lane's own reading rather than a job's read that measured the cause; found holds each record reference the text makes, as resolved or records reads it."""
    return [line for line in causes(text) if any(found[match[0]]["writer"] == lane for match in REF.finditer(line) if match[1] == "record")]


def unread_states(text: str) -> list[str]:
    """Each line of the text, outside a fence, a quote and a quoted line, that tells him a state of work as true now with no {record:…} on it: fixed, covering his case, merged, landed, queued, shipped, deployed, resolved, rolled out or back, or live, never in a reference's own words, a line saying it is unverified, a question, or a clause hedging it before the word as future, conditional or negated."""
    return [
        line.strip()
        for line in picture.FENCE.sub("", text).splitlines()
        if not line.lstrip().startswith(">") and "{record:" not in line and not UNVERIFIED.search(line) and stated(QUOTED.sub("", REF.sub("", line)))
    ]


def stated(line: str) -> bool:
    for found in STATE.finditer(line):
        clause = max((end.end() for end in CLAUSE_END.finditer(line, 0, found.start())), default=0)
        closing = CLAUSE_END.search(line, found.end())
        if not UNSTATED.search(line, clause, found.start()) and not (closing and closing[0] == "?"):
            return True
    return False


def unprevented(text: str, found: dict[str, dict], subject: set[str]) -> list[str]:
    """Each line of the text, outside a fence and a quoted line, whose writer's own words name a failure, refusal or gap of the drive's own, a card, check, hook, lane or session role of it, or a line of the drive late or overdue, or that cites the defect record the drive filed for one, when the text outside its fences names no order or PR carrying the tool change that prevents it, never counting subject, the orders and PRs the text reports on, which say what was late, not what keeps it from being late again; found holds each reference the text makes, as resolved reads it."""
    told = picture.FENCE.sub("", text)
    if named(told) - subject:
        return []
    return [
        line.strip()
        for line in told.splitlines()
        if not line.lstrip().startswith(">")
        and (drive_failure(own_words(line)) or any(found[match[0]].get("defect") for match in REF.finditer(line) if match[1] == "record"))
    ]


def drive_failure(line: str) -> bool:
    return bool(FAILURE.search(line) and DRIVE_PART.search(line) or LATE.search(line))


def own_words(line: str) -> str:
    """The line with each reference, link, URL, quote and code name blanked, as limits.masked blanks them, so only the words its writer typed remain: a {pr:…|…} title, a test's name or a quoted refusal says nothing of the drive's own."""
    return limits.masked(REF.sub(lambda found: limits.MASK * len(found[0]), line))


def named(text: str) -> set[str]:
    """Each order and PR the text names: an order by its id, in {order:…} or bare, and a PR as #<its number>, in {pr:…} or a GitHub link."""
    prs = [found[1] for match in REF.finditer(text) if match[1] == "pr" and (found := PR_NUMBER.search(match[2]))]
    return {*ORDER_ID.findall(text), *(f"#{number}" for number in [*prs, *(link[3] for link in state.PR_URL.finditer(text))])}


def subject(data: dict, order_id: str | None) -> set[str]:
    """What a post or report on an order reports on, as named writes it: the orders of its incident, or the order alone, and each PR of theirs, but a drive tooling order's, whose PRs are the tool changes preventing what it tells."""
    if order_id is None:
        return set()
    orders = [each for each in state.feature_orders(data, order_id) if not data["orders"][each]["text"].startswith(state.TOOLING)]
    return {*orders, *(f"#{key.rsplit('#', 1)[1]}" for key, pr in data["prs"].items() if state.order_served(pr) in orders)}


def prevented(text: str) -> bool:
    """Whether a line of the text, outside a fence and a quoted line, names a cause or a failure of the drive's own in its writer's own words while a line outside its fences and quotes carries the order or PR preventing it, the post his ruling on failures asks for."""
    told = [line for line in picture.FENCE.sub("", text).splitlines() if not line.lstrip().startswith(">")]
    return any(named(line) for line in told) and any(CAUSE.search(plain := own_words(line)) or drive_failure(plain) for line in told)


def record_excuses(text: str) -> list[str]:
    """Each line of the text, outside a fence, a quote and a quoted line, that answers him by a plan, record or answer of his that is missing."""
    return [
        line.strip()
        for line in picture.FENCE.sub("", text).splitlines()
        if not line.lstrip().startswith(">") and ABSENT_RECORD.search(QUOTED.sub("", line))
    ]


def drawn_cards(name: str) -> dict:
    try:
        return view.cards(name)
    except slack_api.SlackError as error:
        raise DriveError(f"a {{card:…}} reference needs the drive's cards, which cc-slack could not list: {error}") from None


def card(cards: dict, data: dict, waits: str) -> dict:
    """A card by its waits id: Slack draws it under a link naming it, and the dashboard opens that card in the queue it waits in. A question card he answered or the drive withdrew keeps its permalink, so it is found among the questions the drive drew, and the dashboard opens the task it was asked for; any other card he answered or cc-slack closed is found among the closed cards cc-slack keeps, and the dashboard opens it in Slack."""
    for entry in reversed([*cards["questions"], *cards["pr_cards"]]):
        if entry["card"].get("waits") == waits:
            queue = "tap" if waits.startswith("merge:") else "question"
            ts = view.message_key(entry["url"])
            return {"kind": "card", "title": entry["card"]["title"], "url": entry["url"], "board": f"#/queue/{queue}/{ts[:-6]}.{ts[-6:]}"}
    for job in data["jobs"].values():
        for question in reversed(job["questions"]):
            if question["waits"] == waits and question["card"]:
                return {"kind": "card", "title": question["title"] or "his question card", "url": question["card"], "board": f"#/feature/{job['order']}"}
    if closed := view.closed_card(cards, waits):
        return {"kind": "card", "title": closed["title"], "url": closed["url"], "board": closed["url"]}
    raise DriveError(
        f"{{card:{waits}}} names no card the drive drew, open or settled; `drive status <job or order>` prints the waits id of each question card its jobs asked on its `asks him:` line,"
        " and `cc-slack drive-cards --drive <drive> | grep -i '<words of its title>'` each open PR card's waits id, permalink and title on one line, and with `--closed` each settled card's"
    )


def pr(prs: dict, ref: str, label: str, pr_cards: list[dict]) -> dict:
    """A PR the drive holds, named by what it does, beside his open card at its head when it has one, which a line asking his tap links in its place, or any other PR named by its owner/repo#number or URL, which has no title of its own here, under the words the post gives it."""
    try:
        key = state.tracked_pr(prs, ref, untracked=True)
    except DriveError as error:
        raise DriveError(f"{{pr:{ref}}}: {error}") from None
    if key in prs:
        found = prs[key]
        shown = view.head_card(found, pr_cards)
        return {"kind": "pr", "title": view.pr_title(found) if found["title"] else key, "url": found["url"], "board": found["url"], "card": shown["url"] if shown else None}
    if label:
        url = state.pr_url(key)
        return {"kind": "pr", "title": "", "url": url, "board": url}
    raise DriveError(
        f"{{pr:{ref}}} names no PR of the drive's, which drive status lists; name any other PR as"
        " {pr:<owner/repo#number>|<words saying what it does>}"
    )


def order(name: str, data: dict, order_id: str) -> dict:
    """An order of his under its title, as every surface drawing an order names it: Slack links the thread its lines go in, or its dashboard page when it has none, and the dashboard opens its page."""
    if order_id not in data["orders"]:
        raise DriveError(f"{{order:{order_id}}} names no order of the drive's; drive status lists them")
    board = f"#/feature/{order_id}"
    url = view.thread_url(data, view.stored_cards(name)[0]["team_url"], data["orders"][order_id]) or dashboard.link(name) + board
    return {"kind": "order", "title": view.order_title(data["orders"][order_id]), "url": url, "board": board}


def release(data: dict, key: str) -> dict:
    """A release under its name, linked to the Slack post his repository's release provider names as its own, by its post method, never its build page, by his words "still using bk links instead of slack links for releases"; a post once found is kept in RELEASE_POSTS_FILE and a lookup is made once a process, since a provider's lookup reads Slack thread by thread and ran 15 to 25 minutes a report when every reference of every post looked again, by meta's read of 736144; none found yet goes out as its words unlinked with a warning, since a gate blocks at most once."""
    path = state.drive_dir(data["name"]) / RELEASE_POSTS_FILE
    kept = json.loads(path.read_text()) if path.exists() else {}
    if (url := kept.get(key)) is None and (url := LOOKED.get((str(path), key), "")) == "":
        LOOKED[(str(path), key)] = url = providers.of(data).release.post(key)
        if url:
            path.write_text(json.dumps(kept | {key: url}))
    if url:
        return {"kind": "release", "title": key, "url": url, "board": url}
    return {"kind": "release", "title": key, "url": None, "board": None, "warning": f"{{release:{key}}} went out as its words, unlinked: the repository's release provider found no release post by that name or build number; edit the post to link it once its post is up"}


def record(name: str, seq: str, label: str) -> dict:
    """A cci record under the poster's words after its |, saying what that read measured, linked to what the record measured, its url, which report_refusals keeps off a report naming his message, or those words alone when it links nothing; a bare {record:<seq>} would draw the record's own opening, words its poster never wrote, such as a bare PR number check-post refuses, and refuses the post naming them."""
    found = shown(name, "record", seq)
    if not label:
        raise DriveError(
            f"{{record:{seq}}} would draw that record's own words, \"{opening(found['text'])}\", which you never wrote: say what that read"
            f" measured after a |, as {{record:{seq}|<what that read measured>}}"
        )
    url = found["refs"].get("url")
    return {"kind": "record", "title": label, "url": url, "board": url, "defect": found["kind"] == "defect", "writer": found["lane"]}


def records(name: str, text: str) -> dict[str, dict]:
    """Whether each {record:<seq>} reference the text holds cites a defect record, and the lane that wrote it, read from cci."""
    read = {match[0]: shown(name, "record", match[2]) for match in REF.finditer(text) if match[1] == "record"}
    return {reference: {"defect": found["kind"] == "defect", "writer": found["lane"]} for reference, found in read.items()}


def answer(data: dict, answer_id: str) -> dict:
    """A ruling of his by the id cc-notes keeps it under, which means nothing to him: his words under the time he said them, linked to the ack of them in his DM."""
    found = settled.shown(answer_id, data["roles"]["coordinator"]["cwd"])
    said = render.when(state.parse_time(found["created_at"]).astimezone().isoformat())
    url = ack[1] if (ack := ACK.search(found["body"])) else None
    return {"kind": "answer", "title": f'his {said} ruling "{opening(settled.his(found["body"]).partition("\n")[0])}"', "url": url, "board": url}


def answer_ids(findings: list[str], data: dict) -> list[str]:
    """The findings but each commit-sha one on the id of a recorded answer of his, which `ccn answer show` reads and is no commit."""
    return [finding for finding in findings if not ((raw := RAW_ANSWER.match(finding)) and answered(raw[1], data))]


def answered(answer_id: str, data: dict) -> bool:
    try:
        settled.shown(answer_id, data["roles"]["coordinator"]["cwd"])
    except DriveError:
        return False
    return True


def started(data: dict, job_id: str, label: str) -> dict:
    """When a job started, the stamp the drive wrote as it added the job, so no session types the time, labelled as the job's start so it never reads as when anything the job did started, such as a release, beside what it stamps for Jev; words after a | would replace that label, and refuse the post."""
    if job_id not in data["jobs"]:
        raise DriveError(f"{{started:{job_id}}} names no job of the drive's; drive status lists them")
    if label:
        raise DriveError(
            f"{{started:{job_id}|{label}}} would draw its words in place of the job's start: {{started:{job_id}}} draws only when that job"
            " started, labelled so, and takes no words after a |"
        )
    return {"kind": "started", "title": f"{render.when(data['jobs'][job_id]['created'])} {JOB_START}", "url": None, "board": None, "stamps": f"when the drive added job {job_id}"}


def posted_at(name: str, seq: str) -> dict:
    """When a cci record went out, its own stamp read in local time, so no session types the time, beside what it stamps for Jev."""
    moment = state.parse_time(shown(name, "at", seq)["at"]).astimezone()
    return {"kind": "at", "title": render.when(state.stamp(moment)), "url": None, "board": None, "stamps": f"when cci record {seq} went out"}


def shown(name: str, kind: str, seq: str) -> dict:
    try:
        return json.loads(reads.run(["cci", "show", seq, "--json", "--drive", name], Path.cwd()))
    except reads.ReadError as error:
        raise DriveError(f"{{{kind}:{seq}}} could not be read: {error}") from None


def opening(text: str) -> str:
    """The first clause of a record's first line, at most ten words, the words a bare {record:<seq>} would draw."""
    words = CLAUSE_END.split(view.plain(text, len(text)), maxsplit=1)[0].split()
    return " ".join(words[:RECORD_WORDS]) + ("…" if len(words) > RECORD_WORDS else "")


def slack(text: str, found: dict[str, dict], thread: str | None) -> str:
    """The text as Slack shows it in this thread of his DM, None for its root: each reference a link under the words that name it, which Slack draws a card under, or a record that links nothing as its words; a card named in its own thread reads as the words after its | or as "this card", with no link, since a link there draws the card again under the post; a PR waiting on his tap, on a line asking his tap or Approve, links his card on it, so the line carries the button it asks him to press and a line only naming the PR draws no card. His words: "why are you showing me a card in the thread of the same post that this is a thread of"."""

    def shown(match: re.Match[str]) -> str:
        ref = found[match[0]]
        if ref["kind"] == "card" and thread and view.message_key(ref["url"]) == thread.replace(".", ""):
            return (match[3] or THIS_CARD).strip().translate(LINK_WORDS)
        if ref["kind"] == "pr" and ref.get("card"):
            return linked(ref, ref["card"], "<{url}|{words}>")
        return linked(ref, ref["url"], "<{url}|{words}>")

    return REF.sub(shown, undoubled(text))


def undoubled(text: str) -> str:
    """The text with a {started:} or {at:} right after a clock time it types dropped, so the typed time is the post's one time and say draws no second one beside it, never "3:23 AM (3:20 AM (job start))"."""
    return DOUBLED_TIME.sub(lambda match: match[1], text)


def typed(text: str, found: dict[str, dict], thread: str | None) -> str:
    """The text as slack draws it with each reference under only the words its writer typed after its |, or the one word limits.UNTYPED for a bare one, never the title or time say draws for it: the words every check of a say post reads, so a drawn PR title, card question, order title or ruling refuses nothing its writer never typed."""
    return slack(text, {key: ref | {"title": ref["title"] if "own" in ref else limits.UNTYPED} for key, ref in found.items()}, thread)


def board(text: str, found: dict[str, dict]) -> str:
    """The text as the dashboard shows it: each reference a link to the page or PR it names, or a record that links nothing as its words."""
    return REF.sub(lambda match: linked(found[match[0]], found[match[0]]["board"], "[{words}]({url})"), text)


def linked(ref: dict, url: str | None, form: str) -> str:
    words = ref["title"].translate(LINK_WORDS)
    return form.format(url=url, words=words) if url else words
