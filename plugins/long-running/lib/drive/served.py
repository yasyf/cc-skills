import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from drive import brief, github, install, limits, refs, settled, slack, state, view
from drive.limits import REF
from drive.render import cut
from drive.state import DriveError

MODEL = "claude-opus-5-5"
EFFORT = "high"
JUDGE_SECONDS = 600
CHANGE_CHARS = 80_000
FILE_CHARS = 12_000
LEFT_OUT_CHARS = 8_000
FILE_DIFF = re.compile(r"^diff --git a/(\S+) ", re.MULTILINE)
TESTS = re.compile(r"(?:^|/)(?:tests?|e2e|__tests__|fixtures|testdata)/|[._-](?:test|spec)\.\w+$|_test\.\w+$")
CITATION_BREAK = re.compile(r"[\s,;()\"']+")
ANSWER_ID = re.compile(r"(?=[0-9a-f]*\d)[0-9a-f]{7,40}")
CITED_REFS = {"card": "card", "answer": "answer", "record": "record", "at": "record"}
READ_FIRST = ("card", "answer", "link", "record")
NAMES = "his card's waits id, its permalink, the answer record as {record:<seq>} or the cc-notes answer id"
NAMELESS = f"DESIGN: names no answer of his: beside each decision cite {NAMES}, or say DESIGN: none"
PORT = re.compile(r"^SERVES:[ \t]*port of #\d+", re.MULTILINE | re.IGNORECASE)
LOG = "checks.log"
CHECKED = "CHECKED"
NOT_READY = "NOT READY"
FAILED = "check failed"
RETRY = timedelta(minutes=10)
VERDICT = {
    "type": "object",
    "properties": {
        "changed": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"file": {"type": "string"}, "before": {"type": "string"}, "after": {"type": "string"}, "named_by": {"type": "string"}},
                "required": ["file", "before", "after", "named_by"],
            },
        },
        "against": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"order": {"type": "string"}, "words": {"type": "string"}, "file": {"type": "string"}, "change": {"type": "string"}},
                "required": ["order", "words", "file", "change"],
            },
        }
    },
    "required": ["changed", "against"],
}
RULES = """You check a change before the drive puts it on his card for his Approve. <his_words> holds the words of each order the change serves, his own words verbatim, oldest first, with the parts of each this change delivers and, when its PRs name theirs, the parts other changes deliver, and, when this change is one PR of a job that has others, those PRs: the merged ones, which already deliver their share, and the open ones, each judged on its own; then his answers that bind them, those their job's brief carries and those the PRs cite for the design decisions they make, oldest first, each the question he was asked and his answer, the option he tapped or his own words, with the options each card the drive asked him offered, numbered as his card drew them, each with its description, and his durable answers recorded on that card's subject. <change> holds each pull request's title, head and diff, source files before tests. PR bodies are left out by his ruling: "Get rid of the fucking PR body check that's insane to delay on who cares about the body".

An answer of his is his words on this change as much as an order is: where an answer settles how or when an order's words are met, such as a step it leaves to a later change, judge the change against the answer. Where his answer picks an option of a card, by its number, as 1 or 1+2, or by its words, that option's own words and description are his words: they bind over the name of a part or anyone's reading of his pick, and a change that builds the option another way, such as one copying a whole file where the option lands only the pages a read needs, goes against them. His durable answers on a card's subject bind a change on that subject as much as his pick does: a change that keeps or adds what they rule out, such as a second code path where they ask for one, goes against them; one on another subject binds nothing here.

First list in `changed` each change the diff makes to what already exists: what existing code returns, prints, stores, accepts or depends on, such as a wire, file or API shape, a default or a command's output, and each behavior it removes. List each side of one value as its own change, what it holds or how it is made apart from the form it is written, stored or sized in, since his words can name one and leave the other: a timestamp moving from local time to UTC and from seconds to an ISO string is two changes. Give each its `before` and `after` as the diff shows them, and in `named_by` quote the words of his or of a cited answer that name that side's new form or behavior, else none. Words naming the kind of value name what it holds, never the form it is written in or what else it stops doing: a convention such a word is often read with is a reader's reading, never his words naming it. New code that alters nothing existing is not a change to list.

Then list in `against` each place where the change goes against his words: it does or keeps what his words rule out, such as a flag, a switch or a page they say goes; it removes, or leaves out of every PR here, what his words say this change delivers; it makes the outcome his words name impossible; or it makes a change in `changed` that nothing of his names and the outcome his words name, the problem they describe included, does not need, by his words "stop overrecahing and changing things people didnt ask you when you are asked to make a change". Read his words for what he means, as a careful colleague would. In `order` name the order, or the answer's id or link, in `words` quote the words of his it goes against or beyond, in `file` the path of the file that does it as its `diff --git a/` line names it, or an empty string when no one file does, and in `change` name the file and the line or hunk that does it, with its before and after when it changes what exists, and one sentence on why.

Leave out:
- what his newer words on the same point overrule, an answer's included, since his newest words win;
- what his words leave to a later change or to the parts other changes deliver, unless this change builds, in the shape of one of them, what those later changes must share, so each would branch around it or undo it; or what one PR here leaves and another PR here, or another PR of its job that <his_words> names, merged or open, does;
- style, naming and tests, and new code the outcome his words name needs;
- any line the diff does not show, unless you read it at the head.

The code at each change's head, the PRs it is stacked on included, reads with `git -C {clone} show <head>:<path>` and `git -C {clone} grep -n <pattern> <head> -- <path>`, <head> the commit its `###` line in <change> names. Before you state an `after` that depends on code the diff does not show, or object, read that code at the head, and name in `change` the path:line at the head that shows the after.

An empty `against` says the change meets his words."""


class Against(DriveError):
    """A change judged to go against his words, a change to what already exists that they do not need included, which its builder fixes unless he approved the PR, as against a read or judge that failed, which runs again; named holds each of his words it goes against or beyond and what in the change does, before and after, and the line carries each answer of his the check read whole, so the builder reconciles the objection with them and its own read of the code rather than with a reading relayed beside the line."""

    def __init__(self, keys: list[str], named: str, answered: list[str]):
        self.named = named
        read = f" His answers the check read, each whole: {'; '.join(answered)}." if answered else ""
        super().__init__(
            f"{', '.join(keys)} go against his words, judged from each PR's title and diff: {named}.{read} Reconcile each objection with his answers and your own"
            " read of the code, then change the PRs to his words, putting back what exists where they do not need it changed, push, and return READY again;"
            " a reading his words leave open goes to him as an ASK."
            f' A PR fixing one named flaking test at its cause is no overreach to close or empty: drive pr add <its url> --job <its job> --exempt "{state.FLAKE_FIX}"'
            f' judges it against his ruling {state.FLAKE_RULING}, "{state.FLAKE_WORDS}", in place of the orders it unblocks'
        )


def check(name: str, key: str, bound: list[str]) -> dict:
    """Judge one PR's change, read whole from GitHub at its head, title and diff and never the body, against the words of the orders it serves, for the parts it carries, and against each answer of his kept on it for the design it makes and each bound names, read by answers with the options of each card of the drive's it answers and his durable answers on that card's subject, a flake fix against his ruling FLAKE_RULING in place of any order, and return the head it checked, or the one its head already holds, for verdict to keep; every other PR its job carries, and each job it was handed from, is named as delivering its own share, the merged ones marked merged and the closed ones left out, so neither a sibling's diff nor a part an earlier PR already landed refuses it. The judge first lists each change the diff makes to what already exists, before and after, then refuses each his words do not need, by his words "stop overrecahing and changing things people didnt ask you when you are asked to make a change". Refuse, naming each of his words the change goes against or beyond, so his card draws only on a change checked against his words, leaving out each objection to a file whose added and removed lines are those of the head the check last passed, while it serves no order that head was not checked against, so a verdict holds for the same change after a restack."""
    data = state.load(name)
    pr = data["prs"][key]
    if state.checked(data, pr):
        return pr["checked"]
    orders = state.served_orders(data, pr)
    read = change(key)
    lines = digests(read["diff"])
    passed = pr["checked"]["lines"] if pr["checked"] and set(orders) <= set(pr["checked"]["orders"]) else {}
    flake = [state.FLAKE_RULING] if pr.get("exempt") == state.FLAKE_FIX else []
    answered = list(dict.fromkeys([*kept_answers(name, data, key), *answers(data, [*bound, *flake], view.live_cards(name))]))
    carried = (state.pr_key(url) for job in data["jobs"].values() if pr["url"] in job["prs"] for url in job["prs"])
    others = [other for other in dict.fromkeys(carried) if other != key and data["prs"][other]["state"] != "closed"]
    if found := [item for item in judged(name, words(data, data["jobs"][pr["job"]], orders, pr["parts"], answered, others), {key: read}) if item["file"] not in lines or passed.get(item["file"]) != lines[item["file"]]]:
        raise Against([key], "; ".join(f'{item["order"]} "{item["words"]}": {item["change"]}' for item in found), answered)
    return {"head": read["head"], "read": read["head"], "orders": orders, "lines": lines}


def verdict(name: str, key: str) -> str:
    """Judge one PR his card waits on at its head against his words, the one place a PR is judged, with the answers kept on it for its design and each answer of his binding its job, by brief.bound, and keep the outcome on the PR with the orders and binding answers it was judged against, so tried keeps it for that head while they stand: CHECKED, and NOT READY, also as a check event, which the watch says on the PR's line, and a refusal is a line on his card that wakes its builder through drive pr wake unless he approved the PR, holding neither his Approve nor its landing; then redraw his open card in place, so it shows the verdict. A judge or GitHub read that failed is kept alone, and the watch tries it again RETRY on."""
    data = state.load(name)
    pr = data["prs"][key]
    head, at, named, checked = pr["head"], state.stamp(), "", None
    orders, answers, bound = state.served_orders(data, pr), binds(name, data, pr), brief.bound(name, data, data["jobs"][pr["job"]])[0]
    try:
        checked = check(name, key, bound)
    except Against as error:
        outcome, named = f"{NOT_READY}: {error}", error.named
    except DriveError as error:
        outcome = f"{FAILED}: {error}"
    else:
        basis = f"the words of {', '.join(checked['orders'])}" if checked["orders"] else f"his ruling {state.FLAKE_RULING}"
        outcome = f"{CHECKED} against {basis} at {checked['head'][:10]}, so his Approve lands it once it is green"
    return kept(name, key, {"head": head, "at": at, "orders": orders, "answers": answers, "outcome": outcome, "against": named, "checked": checked, "pid": os.getpid()})


def kept(name: str, key: str, result: dict) -> str:
    """Keep a check's outcome on its PR, with the head, orders and binding answers it checked, and redraw his open card for it, as verdict describes, unless another check of it started after this one, whose outcome is the one kept; when a newer install saved the drive while the check judged, the newest install's `drive pr checked` keeps it, so no judgment runs twice."""
    outcome = result["outcome"]
    try:
        with state.edit(name) as data:
            pr = data["prs"][key]
            if (checker := pr["checker"]) and checker["pid"] != result["pid"] and state.parse_time(checker["at"]) > state.parse_time(result["at"]):
                return f"{outcome}; not kept, since another check of {key} started while this one judged"
            if result["checked"]:
                pr["checked"] = result["checked"]
            pr["checker"] = {
                "head": result["head"], "pid": result["pid"], "at": result["at"], "orders": result["orders"], "answers": result["answers"], "outcome": outcome, "against": result["against"]
            }
            if not outcome.startswith(FAILED):
                repo, number = key.split("#")
                state.append_event(name, "check", repo=repo, number=int(number), change=outcome, lane=state.heard_lane(data, pr["order"]))
    except state.NewerDrive:
        if not (newer := install.upgraded()):
            raise
        receipt = state.drive_dir(name) / "prs" / f"{key.replace('/', '-').replace('#', '-')}.{result['head'][:12]}.{result['pid']}.json"
        receipt.parent.mkdir(exist_ok=True)
        return json.loads(install.handed(newer, name, ["pr", "checked", state.pr_url(key)], receipt, result, f"{key}: {outcome}", JUDGE_SECONDS))["outcome"]
    if outcome.startswith(FAILED):
        return outcome
    try:
        slack.redraw_pr_cards(pr["url"])
    except slack.SlackError as error:
        return f"{outcome}; his open card for {key} still shows it as drawn before this check, since its redraw failed: {error}"
    return outcome


def ported(report: str) -> bool:
    """Whether a builder's report says its PR is a port of an old-tree PR, which the coordinator checks against that PR rather than his words."""
    return bool(PORT.search(report))


def tried(name: str, data: dict, pr: dict, moment: datetime) -> bool:
    """Whether the PR's head has its check: one running, or a verdict on that head against the orders it serves and the answers of his binding its job now, so one head keeps one verdict however often its builder returns and is judged again only once those change; one that failed, or whose process ended with no outcome, is tried again RETRY after it started."""
    checker = pr["checker"]
    if not checker or checker["head"] != pr["head"]:
        return False
    if checker["outcome"] is None and state.alive(checker["pid"]):
        return True
    if checker["outcome"] is None or checker["outcome"].startswith(FAILED):
        return moment - state.parse_time(checker["at"]) < RETRY
    return checker["orders"] == state.served_orders(data, pr) and checker["answers"] == binds(name, data, pr)


def binds(name: str, data: dict, pr: dict) -> list[str]:
    """The ids of his answers binding the PR's job, by brief.binding, those cc-notes keeps no answer under included, which a verdict keeps and tried compares on every watch pass without asking cc-notes."""
    ruled, cited, _ = brief.binding(name, data, data["jobs"][pr["job"]])
    return list(dict.fromkeys([*ruled, *cited]))


def start(name: str, keys: list[str]) -> None:
    """Start a check of each PR in keys as its own process, marking it under the drive's lock with that process and its head, so no second check starts on that head while it runs."""
    with state.edit(name) as data:
        for key in keys:
            pr = data["prs"][key]
            if not (pr["checker"] and pr["checker"]["outcome"] is None and state.alive(pr["checker"]["pid"])):
                pr["checker"] = {"head": pr["head"], "pid": spawn(name, key), "at": state.stamp(), "orders": None, "answers": None, "outcome": None, "against": ""}


def spawn(name: str, key: str) -> int:
    """Run `drive pr check` on the PR as its own process, outliving the watch pass that started it, its output appended to the drive's check log."""
    return state.detach(name, ["pr", "check", state.pr_url(key)], LOG)


def carry(name: str, keys: list[str]) -> list[str]:
    """Carry each PR's check to a head that moved while each file's added and removed lines stayed those judged, as a restack moves them, so no check stands on a change nobody judged, and mark each other moved head read, its check kept on the head it passed for the next check to read; the PRs whose check it carried."""
    data = state.load(name)
    moved = [key for key in keys if unread(data["prs"][key])]
    if not moved:
        return []
    reads = {key: change(key) for key in moved}
    carried = []
    with state.edit(name) as data:
        for key, read in reads.items():
            pr = data["prs"][key]
            if read["head"] != pr["head"] or not unread(pr):
                continue
            pr["checked"] |= {"read": pr["head"]}
            if digests(read["diff"]) == pr["checked"]["lines"]:
                pr["checked"]["head"] = pr["head"]
                carried.append(key)
    return carried


def unread(pr: dict) -> bool:
    """Whether a check stamped on another head is still to be compared with the change at the PR's head."""
    return bool(pr["checked"]) and pr["checked"]["read"] != pr["head"]


def digests(diff: bytes) -> dict[str, str]:
    """The SHA-1 of each file's added and removed lines, keyed by its path, so a restack that moves its hunks or the lines around them keeps it."""
    return {path: hashlib.sha1("\n".join(line for line in text.splitlines() if line.startswith(("+", "-"))).encode()).hexdigest() for path, text in files(diff)}


def change(key: str) -> dict:
    """A PR's title, body, head and diff as GitHub has them now, the diff the bytes of GitHub's compare of the base and head just read, the diff `gh pr diff` gives with no cap on its files, so the head kept is the head judged."""
    repo, number = key.split("#")
    try:
        read = json.loads(github.read(f"{key}'s title, body, base and head", ["gh", "pr", "view", number, "--repo", repo, "--json", "title,body,baseRefOid,headRefOid"]))
        compare = f"repos/{repo}/compare/{read['baseRefOid']}...{read['headRefOid']}"
        diff = github.read(f"{key}'s diff", ["gh", "api", compare, "-H", "Accept: application/vnd.github.diff", "--allow-escape-sequences"], text=False)
    except github.GithubError as error:
        raise DriveError(f"{key}: {error}; run this again") from None
    return {"title": read["title"], "body": read["body"], "head": read["headRefOid"], "diff": diff}


def files(diff: bytes) -> list[tuple[str, str]]:
    """The path of each file a git-format diff changes with its part of the diff as text, a byte UTF-8 cannot read shown as U+FFFD."""
    text = diff.decode(errors="replace")
    found = list(FILE_DIFF.finditer(text))
    return [(match[1], text[match.start() : end]) for match, end in zip(found, [*(match.start() for match in found[1:]), len(text)][: len(found)], strict=True)]


def words(data: dict, job: dict, orders: list[str], named: list[str], answered: list[str], others: list[str]) -> str:
    """Each order's words, oldest first, with the parts of it the change carries, those its PR names, else every part of it the job delivers, shared with the job's other PRs when it has any, and the order's other parts left to other changes, so a PR is never judged on a part it does not carry; an order the PR carries no part of is read whole. Then the job's merged PRs, which already deliver their share, and its other open PRs, each judged on its own; then each answer of his binding the PR or that it cites, as answers reads them."""
    lines = []
    delivers = "this change and its job's other PRs deliver" if others and not named else "this change delivers"
    for order_id in sorted(orders, key=lambda order_id: data["orders"][order_id]["created"]):
        order = data["orders"][order_id]
        parts = [part for part in state.job_parts(job, order_id) if not named or part in named]
        rest = [part for part in order["parts"] if part not in parts] if named or parts else []
        lines.append(
            f'{order_id}, {order["created"]}: "{order["text"]}"'
            + (f"\n  {delivers}: {'; '.join(parts)}" if parts else "")
            + (f"\n  other changes deliver: {'; '.join(rest)}" if rest else "")
        )
    for state_of, said in (("merged", "its job's merged PRs already deliver their share of these orders: "), ("open", "its job's other open PRs, each judged on its own, deliver their share of these orders: ")):
        if held := [key for key in others if data["prs"][key]["state"] == state_of]:
            lines.append(said + "; ".join(f'{key} "{data["prs"][key]["title"]}"' if data["prs"][key]["title"] else key for key in held))
    if answered:
        lines += ["his answers that bind the PRs, those their brief carries and those they cite for the design they make:", *answered]
    return "\n".join(lines)


def kept_answers(name: str, data: dict, key: str) -> list[str]:
    """The answers of his the PR cites for its design, as the judge reads them: the list kept on it, else its citation read once and kept, so no later check proves a card link again against cc-slack's live cards, which drop a card once its thread goes quiet; a citation replaced while it was read keeps nothing."""
    pr = data["prs"][key]
    if pr["answers"] is not None:
        return pr["answers"]
    read = answers(data, [pr["design"] or ""], view.live_cards(name))
    with state.edit(name) as data:
        if data["prs"][key]["design"] == pr["design"]:
            data["prs"][key]["answers"] = read
    return read


def answers(data: dict, cited: list[str], found: dict | None) -> list[str]:
    """Each answer of his the citations name, oldest first, as the judge reads it, once for each card however many ways it is named: a card's waits id as the drive keeps his answer on its question, the question and his words, else as the cc-notes answer or card permalink it keeps; an answer id as cc-notes keeps it, the question he was asked and his words above its notes; a Slack permalink as the message there, which on a card he answered shows the question and the option he tapped; and an answer record as cci keeps his words, any other record naming no answer. Each card the drive asked him that one of them answers, by its waits id, recorded answer or permalink, adds the options its ASK file kept, as offered draws them, so a number in his answer reads as that option's own words rather than anyone's reading of it, and his durable answers settled.matched finds nearest the card's question, options and descriptions, each read as an answer id is, since his words on the card's subject bind the change as much as his pick. A card he has not answered, or a message he did not write, is refused, since a question of his becomes a ruling only with his own answer."""
    read: dict[str, tuple[datetime, str]] = {}
    asked: dict[str, dict] = {}
    cwd = data["roles"]["coordinator"]["cwd"]
    for kind, name in sorted(dict.fromkeys(each for text in cited for each in named(text)), key=lambda each: READ_FIRST.index(each[0])):
        question = None
        if kind == "card":
            question = answered(data, name)
            if not question["closed"]["words"]:
                kind, name = ("answer", question["closed"]["answer"]) if question["closed"]["answer"] else ("link", question["card"])
            elif (key := question["card"] or name) not in read:
                at = state.parse_time(question["closed"]["at"]).astimezone()
                read[key] = (at, f'{name}, {at.isoformat(timespec="seconds")}, asked "{question["title"]}": "{question["closed"]["words"]}"')
        if kind == "link" and name not in read:
            try:
                said = settled.heard(name, found)
                message = slack.message(name)
            except slack.SlackError as error:
                raise DriveError(f"could not read his answer at {name}: {error}; run this again") from None
            if said is None:
                raise DriveError(f"{name} is not his answer: it is neither his own message nor a card he answered; {settled.HIS_ANSWER}")
            at = slack.sent_at(message)
            read[name] = (at, f'{name}, {at.isoformat(timespec="seconds")}: "{message["text"]}"')
            question = question or drawn(data, "link", name)
        elif kind == "answer":
            answer = settled.shown(name, cwd)
            read[answer["id"]] = recorded(answer)
            question = question or drawn(data, "answer", answer["id"])
        elif kind == "record" and (record := refs.shown(data["name"], "record", name))["kind"] == "answer" and (key := record["refs"].get("url") or name) not in read:
            at = state.parse_time(record["at"]).astimezone()
            read[key] = (at, f'{key}, {at.isoformat(timespec="seconds")}: "{record["text"]}"')
        if question and question["file"]:
            asked[question["file"]] = question
    own = {question["closed"]["answer"][:7] for question in asked.values() if question["closed"]["answer"]}
    for file, question in asked.items():
        card = limits.redrawn(Path(file).read_text())
        at = state.parse_time(question["closed"]["at"]).astimezone()
        read[file] = (at, f'the options his card "{card.title}" offered, numbered as it drew them: {"; ".join(limits.offered(card))}')
        for match in settled.matched("\n".join([card.title, *card.options, *card.hints.values()]), cwd):
            if match["id"] not in own and (answer := settled.shown(match["id"], cwd))["id"] not in read:
                read[answer["id"]] = recorded(answer, f', recorded on the subject of "{card.title}"')
    return [line for _, line in sorted(read.values())]


def recorded(answer: dict, about: str = "") -> tuple[datetime, str]:
    """An answer of his as cc-notes keeps it, when he gave it and the line the judge reads: the question he was asked and his words above its notes."""
    at = datetime.fromisoformat(answer["created_at"]).astimezone()
    return at, f'{answer["id"][:7]}, {at.isoformat(timespec="seconds")}{about}, asked "{answer["title"]}": "{settled.his(answer["body"])}"'


def drawn(data: dict, kind: str, name: str) -> dict | None:
    """The question the drive asked him that this answer of his answered, by the cc-notes answer recorded for it or its card's permalink, the one a later question folded into it left out; None for an answer of his to no question of the drive's."""
    return next(
        (
            question
            for job in data["jobs"].values()
            for question in job["questions"]
            if (closed := question["closed"])
            and closed["how"] == "answered"
            and not question["replaced"]
            and (closed["answer"] and closed["answer"][:7] == name[:7] if kind == "answer" else question["card"] and view.message_key(question["card"]) == view.message_key(name))
        ),
        None,
    )


def named(text: str) -> list[tuple[str, str]]:
    """Each name of an answer of his a citation holds, by its kind: a Slack permalink as a link, a cc-notes id bare or as {answer:<id>}, a card's waits id bare or as {card:<waits id>}, and a cci record as {record:<seq>} or {at:<seq>}; its other words name none."""
    found = [(CITED_REFS[match[1]], match[2]) for match in REF.finditer(text) if match[1] in CITED_REFS]
    for token in CITATION_BREAK.split(REF.sub(" ", text).replace("asked:", " ")):
        token = token.rstrip(".:")
        if token.startswith("http") and slack.MESSAGE_LINK.search(token):
            found.append(("link", token))
        elif ANSWER_ID.fullmatch(token):
            found.append(("answer", token))
        elif state.WAITS_ID.fullmatch(token):
            found.append(("card", token))
    return list(dict.fromkeys(found))


def answered(data: dict, waits: str) -> dict:
    """The question a card's waits id names, once he answered it; one the drive never asked, or whose card still asks him or ended otherwise, is refused as no answer of his."""
    job_id = state.named_job(data, {"waits": waits})
    question = next((question for question in reversed(data["jobs"][job_id]["questions"]) if question["waits"] == waits), None) if job_id else None
    if question is None:
        raise DriveError(f"{waits} is not his answer: it names no card the drive asked him; {settled.HIS_ANSWER}")
    if (closed := question["closed"]) is None or closed["how"] != "answered":
        raise DriveError(f"{waits} is not his answer: its card {'was ' + closed['how'] if closed else 'still asks him'}; {settled.HIS_ANSWER}")
    return question


def shown(changes: dict[str, dict]) -> str:
    """Each PR's title, head and diff, its source files before its tests, each file cut at FILE_CHARS and the whole at CHANGE_CHARS, counting the files left out and naming them up to LEFT_OUT_CHARS."""
    left, out, omitted = CHANGE_CHARS, [], []
    for key, read in changes.items():
        out.append(f'### {key} "{read["title"]}" at {read["head"]}')
        for path, text in sorted(files(read["diff"]), key=lambda item: bool(TESTS.search(item[0]))):
            piece = text if len(text) <= FILE_CHARS else text[:FILE_CHARS] + "\n[cut]\n"
            if len(piece) > left:
                omitted.append(f"{key} {path}")
                continue
            out.append(piece)
            left -= len(piece)
    return "\n".join([*out, *([f"{len(omitted)} files left out for length: {cut(', '.join(omitted), LEFT_OUT_CHARS)}"] if omitted else [])])


def judged(name: str, said: str, changes: dict[str, dict]) -> list[dict]:
    """Where a Claude session allowed only git show and git grep in the coordinator's clone, at each change's head fetched there, judges the change goes against his words, by RULES, once it has listed each change to what already exists, before and after, reading at the head the code a hunk relies on that its diff does not show, such as a parent PR's lines under a stacked child; an empty list when it meets them."""
    clone = state.load(name)["roles"]["coordinator"]["cwd"]
    for key, read in changes.items():
        fetched(clone, key, read["head"])
    prompt = f"<his_words>\n{said}\n</his_words>\n\n<change>\n{shown(changes)}\n</change>"
    argv = [
        "claude", "-p", "--model", MODEL, "--effort", EFFORT, "--restricted", "--strict-mcp-config", "--tools", "Bash", "--disable-slash-commands",
        "--no-session-persistence", "--system-prompt", RULES.format(clone=clone), "--output-format", "json", "--json-schema", json.dumps(VERDICT),
        "--permission-mode", "dontAsk", "--allowedTools", f"Bash(git -C {clone} show:*)", f"Bash(git -C {clone} grep:*)",
    ]
    try:
        ran = subprocess.run(
            argv, input=prompt, cwd=state.drive_dir(name), env=os.environ | {"DISABLE_AUTOUPDATER": "1"}, capture_output=True, text=True, timeout=JUDGE_SECONDS, check=False
        )
    except subprocess.TimeoutExpired:
        raise DriveError(f"judging {', '.join(changes)} against his words ran past {JUDGE_SECONDS} seconds; run this again") from None
    try:
        result = json.loads(ran.stdout)
    except json.JSONDecodeError:
        result = {"result": ran.stderr or ran.stdout}
    if ran.returncode or result.get("is_error") or "structured_output" not in result:
        raise DriveError(f"judging {', '.join(changes)} against his words failed: {cut(str(result.get('result')), 300)}; run this again")
    return result["structured_output"]["against"]


def fetched(clone: str, key: str, head: str) -> None:
    """Fetch a PR's head commit into the clone's objects by its sha, writing no ref, as github.fetched fetches a branch's: from origin for a PR of the clone's own repository, else from the PR's repository at origin's host and protocol."""
    repo = key.split("#")[0]
    git = ["git", "-C", clone]
    try:
        url = github.run([*git, "remote", "get-url", "origin"]).strip()
        home = github.repo_name(url)
        github.run([*git, "fetch", "-q", "--no-tags", "--no-write-fetch-head", "origin" if home == repo else url.replace(home, repo), head])
    except github.GithubError as error:
        raise DriveError(f"{key}: its head {head[:12]} could not be fetched into {clone} for the judge to read: {error}; run this again") from None
