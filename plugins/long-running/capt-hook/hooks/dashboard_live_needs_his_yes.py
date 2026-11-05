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
NOT_LIVE = re.compile(r"\bnot live\b", re.IGNORECASE)
PR = re.compile(r"(?P<repo>[\w.-]*(?:/[\w.-]+)?)(?:#|/pull/)(?P<number>\d{1,5})\b")
CC_SKILLS = frozenset({"", "pr", "cc-skills", "yasyf/cc-skills"})
CITE = re.compile(r"\b(?:card:(?P<card>[\w.-]+)|owner:(?P<row>m\d+))\b")
YES = re.compile(r"^\W*(?:approved|(?:picked\W+)?(?:yes|yep|yeah|lgtm|looks good|ship it))\b", re.IGNORECASE)
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
PICKED_YES = {"ccn answer list --label from:owner --label board-card:chat-keep": json.dumps([{"body": "picked “Yes, keep it”\nBoard question: Keep it?"}])}
PICKED_NO = {"ccn answer list --label from:owner --label board-card:chat-drop": json.dumps([{"body": "picked “Take it back off”\nBoard question: Keep it?"}])}
PACK_PR = {"gh pr view 2618": "plugins/long-running/capt-hook/hooks/release_cards.py\n"}
SAME_NUMBER_PR = {"gh pr view 817": f"{DASHBOARD}/app/chat.js\n"}
GO_LANE = "go-cli-fix-1"


def post(text: str, lane: str = LANE, kind: str = "landed", pr: str = "", **extra: object) -> Input:
    named = f"--pr {pr} " if pr else ""
    return Input(command=f"cci post --drive d --lane {lane} --kind {kind} {named}--to meta-desk --text \"{text}\"", env={"CLAUDE_LONG_RUNNING_LANE": lane}, **extra)


def read(evt: BaseHookEvent, *argv: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(argv, check=False, capture_output=True, text=True, timeout=READ_TIMEOUT_SECONDS, cwd=evt.cwd, env=dict(reqenv.env_map()))
    except (OSError, subprocess.SubprocessError):
        return None


def touches_dashboard(evt: BaseHookEvent, text: str) -> bool:
    if DASHBOARD in text:
        return True
    for number in dict.fromkeys(found["number"] for found in PR.finditer(text) if found["repo"].lower() in CC_SKILLS):
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
        live = "live" in kinds or bool(LIVE.search(NOT_LIVE.sub("", text)))
        if NOT_LIVE.search(text) and not live:
            continue
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
        post(f"{CLAIM}; his yes is card:chat-keep", commands=CHAT_PR | PICKED_YES): Allow(),
        post(f"{CLAIM}; per card:chat-drop", commands=CHAT_PR | PICKED_NO): Block(),
        post("Merged, not installed: #2618 sets tap_queue_url for the pack", commands=PACK_PR): Allow(),
        post("Merged, not installed: #2675 rebuilds the Chat tab", commands=CHAT_PR): Block(),
        post("Merged, not installed, not live: #2675 rebuilds the Chat tab", commands=CHAT_PR): Allow(),
        post("#2675 is not live on the checkout, but the Chat tab is now live", kind="report", commands=CHAT_PR): Block(),
        post("Not live: #2675 rebuilds the Chat tab", kind="live", commands=CHAT_PR): Block(),
        post("Merged, not installed: #2675 rebuilds the Chat tab", lane="slack-bugs-1", commands=CHAT_PR): Block(),
        post("Live: the Slack bug fix is released on build 12345", lane="slack-bugs-1", kind="live"): Allow(),
        post("Fixed the Go CLI: https://github.com/yasyf/cc-context/pull/817", lane=GO_LANE, pr="yasyf/cc-context#817", commands=SAME_NUMBER_PR): Allow(),
        post("Fixed the Go CLI", lane=GO_LANE, pr="https://github.com/yasyf/cc-context/pull/817", commands=SAME_NUMBER_PR): Allow(),
        post("Fixed the Go CLI in cc-context#817", lane=GO_LANE, commands=SAME_NUMBER_PR): Allow(),
        post("Fixed the Go CLI in yasyf/cc-context#817", lane=GO_LANE, commands=SAME_NUMBER_PR): Allow(),
        post("Fixed the Chat tab in #817", lane=GO_LANE, commands=SAME_NUMBER_PR): Block(),
        post("Fixed the Chat tab in PR#817", lane=GO_LANE, commands=SAME_NUMBER_PR): Block(),
        post("Fixed the Chat tab in cc-skills#817", lane=GO_LANE, commands=SAME_NUMBER_PR): Block(),
        post("Fixed the Chat tab in yasyf/cc-skills#817", lane=GO_LANE, commands=SAME_NUMBER_PR): Block(),
        post("Fixed the Chat tab in https://github.com/yasyf/cc-skills/pull/817", lane=GO_LANE, commands=SAME_NUMBER_PR): Block(),
        post("Fixed the Chat tab", lane=GO_LANE, pr="817", commands=SAME_NUMBER_PR): Block(),
        post("Fixed the Chat tab", lane=GO_LANE, pr="https://github.com/yasyf/cc-skills/pull/817", commands=SAME_NUMBER_PR): Block(),
        post("yasyf/cc-context#900 is out and so is #817", lane=GO_LANE, commands=SAME_NUMBER_PR): Block(),
        Input(tool=POST_TOOL, tool_input={"lane": "owner-apps-1", "kind": "live", "text": "The Review tab now lists PRs only"}, env={"CLAUDE_LONG_RUNNING_LANE": "owner-apps-1"}): Block(),
    },
)
def dashboard_live_needs_his_yes(evt: BaseHookEvent) -> HookResult | None:
    found = unseen_claims(evt)
    return evt.block(fenced(REFUSAL, found)) if found else None
