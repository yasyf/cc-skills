from __future__ import annotations

import json
import re
import subprocess

from captain_hook import Allow, BaseHookEvent, Block, Event, HookResult, Input, Tool, on
from captain_hook.util import reqenv

from .cci_mcp_posts import POST_TOOL
from .compaction_handoff import Bound
from .meta_records import posts
from .owner_facing import fenced

DASHBOARD = "plugins/long-running/dashboard"
UI_LANE = re.compile(r"^(?:dashboard|tap-queue|owner-apps|owner-board|chat-design)\b")
LIVE = re.compile(r"(?:^|[.;:]\s*)live\b|\b(?:is|are|went|now|all)\s+live\b|\blive on\b|\bserved (?:at|on)\b", re.IGNORECASE)
NOT_INSTALLED = re.compile(r"\bmerged,? not installed\b", re.IGNORECASE)
PR = re.compile(r"(?:cc-skills#|/pull/|#)(\d{1,5})\b")
CITE = re.compile(r"\b(?:card:(?P<card>[\w.-]+)|owner:(?P<row>m\d+))\b")
YES = re.compile(r"^\W*(?:approved|yes|yep|yeah|lgtm|looks good|ship it)\b", re.IGNORECASE)
READ_TIMEOUT_SECONDS = 20
REFUSAL = (
    "A dashboard or board change is live only once the owner saw a real screenshot of the new render on a card and said yes:"
    " put the screenshot on a card, wait for his yes, and post again citing `card:<card id>` or the `owner:<m-id>` row of that yes."
)
LANE = "tap-queue-1"
CLAIM = "Live: the one Chat tab is on the dashboard and Your asks is gone (#2675), served at ab539fc33"
CHAT_PR = {"gh pr view 2675": f"{DASHBOARD}/app/chat.js\n"}
APPROVED = {"ccn answer list --label from:owner --label board-card:chat-mock": json.dumps([{"body": "approved “ship this render” | Card chat-mock"}])}
REJECTED = {"ccn answer list --label from:owner --label owner:m480": json.dumps([{"body": "this is not at all what I wanted"}])}
PACK_PR = {"gh pr view 2618": "plugins/long-running/capt-hook/hooks/release_cards.py\n"}


def post(text: str, lane: str = LANE, kind: str = "landed", **extra: object) -> Input:
    return Input(command=f"cci post --drive d --lane {lane} --kind {kind} --to meta-desk --text \"{text}\"", env={"CLAUDE_LONG_RUNNING_LANE": lane}, **extra)


def read(evt: BaseHookEvent, *argv: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(argv, check=False, capture_output=True, text=True, timeout=READ_TIMEOUT_SECONDS, cwd=evt.cwd, env=dict(reqenv.env_map()))
    except (OSError, subprocess.SubprocessError):
        return None


def touches_dashboard(evt: BaseHookEvent, text: str) -> bool:
    if DASHBOARD in text:
        return True
    for number in dict.fromkeys(PR.findall(text)):
        files = read(evt, "gh", "pr", "view", number, "-R", "yasyf/cc-skills", "--json", "files", "--jq", ".files[].path")
        if files and files.returncode == 0 and DASHBOARD in files.stdout:
            return True
    return False


def his_yes(evt: BaseHookEvent, text: str) -> bool:
    if not (cited := CITE.search(text)):
        return False
    label = f"board-card:{cited['card']}" if cited["card"] else f"owner:{cited['row']}"
    listed = read(evt, "ccn", "answer", "list", "--label", "from:owner", "--label", label, "--limit", "0", "--json", "--full")
    if not listed or listed.returncode != 0:
        return True
    return any(YES.search(answer.get("body") or "") for answer in json.loads(listed.stdout or "[]"))


def unseen_claims(evt: BaseHookEvent) -> list[str]:
    found = []
    for lane, _, kind, text, _, _ in posts(evt):
        kinds = set(kind.lower().split(","))
        live = "live" in kinds or bool(LIVE.search(text))
        if not (live or "landed" in kinds):
            continue
        ui = touches_dashboard(evt, text)
        if not (ui or UI_LANE.match(lane)) or not (live or ui or not NOT_INSTALLED.search(text)):
            continue
        if not his_yes(evt, text):
            found.append(text)
    return found


@on(  # capt-hook: no-escape the owner ordered that a dashboard or board change is live only once he saw its render on a card and said yes
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash", POST_TOOL)],
    tests={
        post(CLAIM, kind="report", commands=CHAT_PR): Block(pattern=r"^A dashboard or board change is live only once the owner saw[\s\S]*Your asks is gone"),
        post(f"{CLAIM}; his yes is card:chat-mock", commands=CHAT_PR | APPROVED): Allow(),
        post(f"{CLAIM}; per owner:m480", commands=CHAT_PR | REJECTED): Block(),
        post("Merged, not installed: #2618 sets tap_queue_url for the pack", commands=PACK_PR): Allow(),
        post("Merged, not installed: #2675 rebuilds the Chat tab", commands=CHAT_PR): Block(),
        post("Merged, not installed: #2675 rebuilds the Chat tab", lane="slack-bugs-1", commands=CHAT_PR): Block(),
        post("Live: the Slack bug fix is released on build 12345", lane="slack-bugs-1", kind="live"): Allow(),
        Input(tool=POST_TOOL, tool_input={"lane": "owner-apps-1", "kind": "live", "text": "The Review tab now lists PRs only"}, env={"CLAUDE_LONG_RUNNING_LANE": "owner-apps-1"}): Block(),
    },
)
def dashboard_live_needs_his_yes(evt: BaseHookEvent) -> HookResult | None:
    found = unseen_claims(evt)
    return evt.block(fenced(REFUSAL, found)) if found else None
