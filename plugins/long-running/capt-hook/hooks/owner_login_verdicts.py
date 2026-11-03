from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator

from captain_hook import Allow, BaseHookEvent, Block, Call, CommandSchema, Event, HookResult, Input, Operand, Option, Tool, ast_grep, on

from .board_text_gate import first, reply_body
from .compaction_handoff import shared_tests
from .local_builds import LANE_ENV, in_drive
from .owner_quotes import read
from .pr_body_frozen import GH_API, ran
from .tests.register_fixtures import ACTIVE, FIXTURES

BOARD_LANE = "board-watch"
CITED = re.compile(r"\bboard verdict #(\d+)\b", re.IGNORECASE)
TAP = re.compile(r"^\W*(?:(?:the\s+)?owner(?:'s)?\s+(?:tap(?:ped)?|approv(?:ed|al))\b|tapped\b)", re.IGNORECASE | re.MULTILINE)
POSTED_TO = re.compile(r"^/?repos/(?P<repo>[^/]+/[^/]+)/(?:issues|pulls)/(?P<pr>\d+)/(?:comments|reviews(?:/\d+/events)?)/?$")
PR_TEXT = {("pr", "comment"), ("pr", "review")}
GH_PR = CommandSchema(
    "gh",
    operands=(Operand("words", count="*"),),
    options=(
        Option("repo", ("-R", "--repo")),
        Option("body", ("-b", "--body")),
        Option("body-file", ("-F", "--body-file")),
        Option("approve", ("-a", "--approve"), bool),
        Option("request-changes", ("-r", "--request-changes"), bool),
        Option("comment", ("-c", "--comment"), bool),
        Option("editor", ("-e", "--editor"), bool),
        Option("web", ("-w", "--web"), bool),
        *(Option(name, (f"--{name}",), bool) for name in ("edit-last", "delete-last", "create-if-none", "yes")),
    ),
)
UNGROUNDED = (
    "An approval or tap verdict from a drive posts under the owner's GitHub login, so it must cite his board verdict at the PR's current head. "
    "Draw a board card for him on this PR and head, then post citing `board verdict #<seq>`, the `board-watch` owner record of his answer."
)
BODIES = FIXTURES / "verdict-login"
HEAD = "5d1f0e2a9b8c7d6e5f4a3b2c1d0e9f8a7b6c5d4e"
OLD = "0eab10ae6a77c2d9e1f3a5b7c9d1e3f5a7b9c1d3"
APPROVE = "gh pr review 48019 --approve -b 'Board verdict #358500.'"
VIEW = {"gh pr view 48019": f"48019 {HEAD}\n"}


def grounded(evt: BaseHookEvent, target: str, repo: str | None, text: str) -> bool:
    if not (cited := CITED.findall(text)):
        return False
    number, _, head = ran(evt, "gh", "pr", "view", *filter(None, [target]), *(["-R", repo] if repo else []), "--json", "number,headRefOid", "--jq", '"\\(.number) \\(.headRefOid)"').partition(" ")
    return any(board_verdict(evt, seq, number, head) for seq in cited)


def board_verdict(evt: BaseHookEvent, seq: str, number: str, head: str) -> bool:
    shown = ran(evt, "cci", "show", seq, "--json").splitlines()
    record = json.loads(shown[0]) if shown else {}
    attrs = (record.get("fields") or {}).get("attrs") or {}
    recorded = attrs.get("head", "")
    names_pull = re.fullmatch(rf"(?:merge-row-)?{number}(?:-[0-9a-f]{{7,40}})?", attrs.get("row", "")) is not None
    return (record.get("lane"), record.get("kind")) == (BOARD_LANE, "owner") and bool(attrs.get("verdict")) and names_pull and bool(recorded and head) and (head.startswith(recorded) or recorded.startswith(head))


def verdicts(evt: BaseHookEvent) -> Iterator[tuple[str, str | None, str]]:
    for call in evt.command.calls("gh"):
        pr = GH_PR.bind(call).values
        words = pr.get("words", ())
        if tuple(words[:2]) in PR_TEXT:
            body = "\n".join(text for text in [reply_body(evt, call, pr), *files(evt, call, pr.get("body-file", ()))] if isinstance(text, str))
            if pr.get("approve") or TAP.search(body):
                yield next(iter(words[2:]), None) or "", first(pr, "repo"), body
        api = GH_API.bind(call).values
        words = api.get("words", ())
        if words[:1] == ("api",) and (posted := POSTED_TO.match(next(iter(words[1:]), None) or "")) and "GET" not in {method.upper() for method in api.get("method", ()) if method}:
            payload = payload_of(evt, call, api)
            body = str(payload.get("body", ""))
            if str(payload.get("event", "")).upper() == "APPROVE" or TAP.search(body):
                yield posted["pr"], None if posted["repo"].startswith("{") else posted["repo"], body


def payload_of(evt: BaseHookEvent, call: Call, api: dict[str, tuple]) -> dict:
    fields = dict(field.split("=", 1) for field in api.get("field", ()) if field and "=" in field)
    if fields.get("body", "").startswith("@"):
        fields["body"] = "".join(files(evt, call, [fields["body"][1:]]))
    return fields | {key: value for text in files(evt, call, api.get("input", ())) for key, value in json.loads(text).items()}


def files(evt: BaseHookEvent, call: Call, paths: Iterable[str | None]) -> Iterator[str]:
    for path in filter(None, paths):
        yield from stdin(evt, call) if path == "-" else read(evt, call, [path])


def stdin(evt: BaseHookEvent, call: Call) -> Iterator[str]:
    yield from (node.text for node in ast_grep.find_kinds(evt.command.raw, "bash", frozenset({"heredoc_body"})))
    yield from read(evt, call, [redirect.target for redirect in call.redirects if redirect.op == "<"])


def record(head: str, lane: str = BOARD_LANE, row: str = "48019") -> dict[str, str]:
    shown = {"seq": 358500, "lane": lane, "kind": "owner", "text": "Owner on board 1a2b3c4d, merge-prs: 48019 approved", "fields": {"attrs": {"event": "triage.decided", "block": "merge-prs", "row": row, "verdict": "approved", "head": head}}}
    return VIEW | {"cci show 358500": json.dumps(shown)}


@on(  # capt-hook: no-escape an approval or tap under the owner's login stands only on his board verdict at the head
    Event.PreToolUse,
    only_if=[Tool("Bash")],
    tests=shared_tests({
        Input(command="gh pr review 48019 --approve", env=LANE_ENV): Block(pattern=r"^An approval or tap verdict from a drive posts under the owner's GitHub login[\s\S]*`board verdict #<seq>`"),
        Input(command="gh pr review 48019 --approve", state=ACTIVE): Block(),
        Input(command=APPROVE, env=LANE_ENV, commands=record(HEAD)): Allow(),
        Input(command=APPROVE, env=LANE_ENV, commands=record(HEAD[:12])): Allow(),
        Input(command=APPROVE, env=LANE_ENV, commands=record(HEAD, row="merge-row-48019")): Allow(),
        Input(command=APPROVE, env=LANE_ENV, commands=record(OLD)): Block(),
        Input(command=APPROVE, env=LANE_ENV, commands=record(HEAD, row="48020")): Block(),
        Input(command=APPROVE, env=LANE_ENV, commands=record(HEAD, lane="root")): Block(),
        Input(command="gh api -X POST repos/{owner}/{repo}/pulls/48019/reviews -f event=APPROVE", env=LANE_ENV): Block(),
        Input(command=f"gh api repos/Forge-AI/monorepo/pulls/48019/reviews --input {BODIES / 'approve.json'}", env=LANE_ENV): Block(),
        Input(command="gh api repos/Forge-AI/monorepo/pulls/48019/reviews -f event=APPROVE -f body='board verdict #358500'", env=LANE_ENV, commands=record(HEAD)): Allow(),
        Input(command="gh pr comment 48019 --body 'Owner tapped this head on his board.'", env=LANE_ENV): Block(),
        Input(command=f"gh pr comment 48019 --body-file {BODIES / 'tap.md'}", env=LANE_ENV): Block(),
        Input(command=f"gh pr comment 48019 --body-file - < {BODIES / 'tap.md'}", env=LANE_ENV): Block(),
        Input(command="gh pr comment 48019 --body-file - <<'EOF'\nTapped: lands on green.\nEOF", env=LANE_ENV): Block(),
        Input(command="gh pr comment 48019 --body \"$(cat <<'EOF'\nThe owner approved this head.\nEOF\n)\"", env=LANE_ENV): Block(),
        Input(command="gh api repos/Forge-AI/monorepo/issues/48019/comments -f body='Owner tapped at the new head.'", env=LANE_ENV): Block(),
        Input(command=f"gh pr comment 48019 -F {BODIES / 'tap-cited.md'}", env=LANE_ENV, commands=record(HEAD)): Allow(),
        Input(command="gh pr comment 48019 --body '@forge-pr-reviewer accept stale-publisher-overwrites-restarted-head\n\nAccepted under the coordinator ruling; the overwrite is idempotent.'", env=LANE_ENV): Allow(),
        Input(command="gh api repos/Forge-AI/monorepo/issues/48019/comments -f body='@forge-pr-reviewer accept risk'", env=LANE_ENV): Allow(),
        Input(command="pr-reviews accept risk --why 'The overwrite is idempotent.' --pr 48019", env=LANE_ENV): Allow(),
        Input(command="gh pr review 48019 --request-changes -b 'Needs the head pinned.'", env=LANE_ENV): Allow(),
        Input(command="gh pr comment 48019 --body 'CI is green at the new head; the owner has not tapped it yet.'", env=LANE_ENV): Allow(),
        Input(command="gh pr comment 48019 --body '@forge-pr-reviewer review'", env=LANE_ENV): Allow(),
        Input(command="gh api repos/Forge-AI/monorepo/pulls/48019/reviews", env=LANE_ENV): Allow(),
        Input(command="gh pr review 48019 --approve"): Allow(),
    }),
)
def owner_login_verdicts_cite_the_board(evt: BaseHookEvent) -> HookResult | None:
    if not in_drive(evt):
        return None
    return evt.block(UNGROUNDED) if any(not grounded(evt, target, repo, f"{evt.command.raw}\n{body}") for target, repo, body in verdicts(evt)) else None
