import json
import os
import re
import subprocess
from datetime import datetime

from drive import github, settled, slack, state
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
VERDICT = {
    "type": "object",
    "properties": {
        "against": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"order": {"type": "string"}, "words": {"type": "string"}, "change": {"type": "string"}},
                "required": ["order", "words", "change"],
            },
        }
    },
    "required": ["against"],
}
RULES = """You check a change before the drive puts it on his card for his Approve. <his_words> holds the words of each order the change serves, his own words verbatim, oldest first, with the parts of each this change delivers and, when its PRs name theirs, the parts other changes deliver; then his answers the PRs cite for the design decisions they make, oldest first, each the question he was asked and his answer, the option he tapped or his own words. <change> holds each pull request's title and diff, source files before tests. PR bodies are left out by his ruling: "Get rid of the fucking PR body check that's insane to delay on who cares about the body".

An answer of his is his words on this change as much as an order is: where an answer settles how or when an order's words are met, such as a step it leaves to a later change, judge the change against the answer.

List in `against` each place where the change goes against his words: it does or keeps what his words rule out, such as a flag, a switch or a page they say goes; it removes, or leaves out of every PR here, what his words say this change delivers; or it makes the outcome his words name impossible. Read his words for what he means, as a careful colleague would. In `order` name the order, or the answer's id or link, in `words` quote the words of his it goes against, and in `change` name the file and the line or hunk that does it, with one sentence on why.

Leave out:
- what his newer words on the same point overrule, an answer's included, since his newest words win;
- what his words leave to a later change or to the parts other changes deliver, or what one PR here leaves and another PR here does;
- style, naming, tests and anything his words do not speak to;
- any line the diff does not show: judge only what it shows, an unchanged context line included.

An empty `against` says the change meets his words."""


class Against(DriveError):
    """A change judged to go against his words, which its builder fixes, as against a read or judge that failed, which runs again."""


def check(name: str, job_id: str, also: list[str], tapping: list[str], cited: list[str], setting: dict[str, dict]) -> None:
    """Judge the change of the job's PRs his card waits on, read whole from GitHub at each head, title and diff and never the body, against the words of every order they serve and each order in also, with each PR in tapping that is about to wait on his tap, and against each answer of his cited names or a PR's design cites, and keep each head checked; each order is judged for the parts its PRs name, else every part its job delivers. A PR's design and parts are those drive pr set kept, or those setting gives for a PR this call sets. Refuse, naming each of his words the change goes against, so his card draws only on a change checked against his words."""
    data = state.load(name)
    keys = list(dict.fromkeys([*state.carded_prs(data, job_id), *tapping]))
    orders = list(dict.fromkeys([*(order_id for key in keys for order_id in state.served_orders(data, data["prs"][key])), *also]))
    if all(state.checked(data, data["prs"][key]) and set(also) <= set(data["prs"][key]["checked"]["orders"]) for key in keys):
        return
    changes = {key: change(key) for key in keys}
    prs = [data["prs"][key] | setting.get(key, {}) for key in keys]
    parts = [part for pr in prs for part in pr["parts"]]
    answered = answers(data, [*cited, *(pr.get("design") or "" for pr in prs)])
    if found := judged(name, words(data, data["jobs"][job_id], orders, parts, answered), changes):
        named = "; ".join(f'{item["order"]} "{cut(item["words"], 160)}": {cut(item["change"], 240)}' for item in found)
        raise Against(
            f"{', '.join(keys)} go against his words, judged from each PR's title and diff: {named}. Change the PRs to his words,"
            " push, and return READY again; a reading his words leave open goes to him as an ASK"
        )
    with state.edit(name) as data:
        for key, read in changes.items():
            data["prs"][key]["checked"] = {"head": read["head"], "orders": orders, "patch": patch(read["diff"])}


def carry(name: str, keys: list[str]) -> None:
    """Carry each PR's check to a head that moved while its change stayed the one judged, as a restack moves it, and drop the check of one whose change moved with it, so his card draws on a restacked PR with no second judge and never on a change nobody judged."""
    data = state.load(name)
    moved = [key for key in keys if (stamp := data["prs"][key]["checked"]) and stamp["head"] != data["prs"][key]["head"]]
    if not moved:
        return
    reads = {key: change(key) for key in moved}
    with state.edit(name) as data:
        for key, read in reads.items():
            pr = data["prs"][key]
            if pr["checked"] and read["head"] == pr["head"]:
                pr["checked"] = pr["checked"] | {"head": read["head"]} if patch(read["diff"]) == pr["checked"]["patch"] else None


def patch(diff: bytes) -> str:
    """The id of the change a diff makes wherever its hunks land, by `git patch-id --stable`, so a restack onto a moved base keeps it."""
    return subprocess.run(["git", "patch-id", "--stable"], input=diff, capture_output=True, check=True).stdout.decode().split(" ")[0]


def change(key: str) -> dict:
    """A PR's title, head and diff as GitHub has them now, the diff the bytes of GitHub's compare of the base and head just read, the diff `gh pr diff` gives with no cap on its files, so the head kept is the head judged."""
    repo, number = key.split("#")
    try:
        read = json.loads(github.read(f"{key}'s title, base and head", ["gh", "pr", "view", number, "--repo", repo, "--json", "title,baseRefOid,headRefOid"]))
        compare = f"repos/{repo}/compare/{read['baseRefOid']}...{read['headRefOid']}"
        diff = github.read(f"{key}'s diff", ["gh", "api", compare, "-H", "Accept: application/vnd.github.diff", "--allow-escape-sequences"], text=False)
    except github.GithubError as error:
        raise DriveError(f"{key}: {error}; run this again") from None
    return {"title": read["title"], "head": read["headRefOid"], "diff": diff}


def files(diff: bytes) -> list[tuple[str, str]]:
    """The path of each file a git-format diff changes with its part of the diff as text, a byte UTF-8 cannot read shown as U+FFFD."""
    text = diff.decode(errors="replace")
    found = list(FILE_DIFF.finditer(text))
    return [(match[1], text[match.start() : end]) for match, end in zip(found, [*(match.start() for match in found[1:]), len(text)][: len(found)], strict=True)]


def words(data: dict, job: dict, orders: list[str], named: list[str], answered: list[str]) -> str:
    """Each order's words, oldest first, with the parts of it the change delivers: those its PRs name, with the order's other parts left to other changes, else every part of it the job delivers; then each answer of his the PRs cite."""
    lines = []
    for order_id in sorted(orders, key=lambda order_id: data["orders"][order_id]["created"]):
        order = data["orders"][order_id]
        parts = [part for part in state.job_parts(job, order_id) if not named or part in named]
        others = [part for part in order["parts"] if part not in parts] if named else []
        lines.append(
            f'{order_id}, {order["created"]}: "{order["text"]}"'
            + (f"\n  this change delivers: {'; '.join(parts)}" if parts else "")
            + (f"\n  other changes deliver: {'; '.join(others)}" if others else "")
        )
    if answered:
        lines += ["his answers the PRs cite for the design they make:", *answered]
    return "\n".join(lines)


def answers(data: dict, cited: list[str]) -> list[str]:
    """Each answer of his the citations name, oldest first, as the judge reads it: an answer id as cc-notes keeps it, the question he was asked and his words above its notes, and a Slack permalink as the message there, which on a card he answered shows the question and the option he tapped; other words of a citation name no answer."""
    tokens = dict.fromkeys(token.rstrip(".:") for text in cited for token in CITATION_BREAK.split(text.replace("asked:", " ")) if token)
    read: dict[str, tuple[datetime, str]] = {}
    for token in tokens:
        if token.startswith("http") and slack.MESSAGE_LINK.search(token):
            try:
                message = slack.message(token)
            except slack.SlackError as error:
                raise DriveError(f"could not read his answer at {token}: {error}; run this again") from None
            at = slack.sent_at(message)
            read[token] = (at, f'{token}, {at.isoformat(timespec="seconds")}: "{message["text"]}"')
        elif ANSWER_ID.fullmatch(token):
            answer = settled.shown(token, data["roles"]["coordinator"]["cwd"])
            at = datetime.fromisoformat(answer["created_at"]).astimezone()
            said = answer["body"].partition("\nNotes:")[0].strip()
            read[answer["id"]] = (at, f'{answer["id"][:7]}, {at.isoformat(timespec="seconds")}, asked "{answer["title"]}": "{said}"')
    return [line for _, line in sorted(read.values())]


def shown(changes: dict[str, dict]) -> str:
    """Each PR's title and diff, its source files before its tests, each file cut at FILE_CHARS and the whole at CHANGE_CHARS, counting the files left out and naming them up to LEFT_OUT_CHARS."""
    left, out, omitted = CHANGE_CHARS, [], []
    for key, read in changes.items():
        out.append(f'### {key} "{read["title"]}"')
        for path, text in sorted(files(read["diff"]), key=lambda item: bool(TESTS.search(item[0]))):
            piece = text if len(text) <= FILE_CHARS else text[:FILE_CHARS] + "\n[cut]\n"
            if len(piece) > left:
                omitted.append(f"{key} {path}")
                continue
            out.append(piece)
            left -= len(piece)
    return "\n".join([*out, *([f"{len(omitted)} files left out for length: {cut(', '.join(omitted), LEFT_OUT_CHARS)}"] if omitted else [])])


def judged(name: str, said: str, changes: dict[str, dict]) -> list[dict]:
    """Where a Claude session given no tools judges the change goes against his words, by RULES; an empty list when it meets them."""
    prompt = f"<his_words>\n{said}\n</his_words>\n\n<change>\n{shown(changes)}\n</change>"
    argv = [
        "claude", "-p", "--model", MODEL, "--effort", EFFORT, "--restricted", "--strict-mcp-config", "--tools", "", "--disable-slash-commands",
        "--no-session-persistence", "--system-prompt", RULES, "--output-format", "json", "--json-schema", json.dumps(VERDICT),
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
