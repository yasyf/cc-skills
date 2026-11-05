from __future__ import annotations

import json
import re
import subprocess

from captain_hook import Allow, BaseHookEvent, Block, Event, HookResult, Input, on, Tool
from captain_hook.util import reqenv

from .compaction_handoff import Bound

LANE = {"CLAUDE_LONG_RUNNING_LANE": "lane-a"}
HOSTS = {"git config": "app.example.test\n"}
HIS_TAB = '{"result": {"tabs": [{"browserPageId": "p1", "url": "https://app.example.test/"}, {"browserPageId": "p2", "url": "http://localhost:3000/"}]}}'
HIS_ACTIVE_TAB = '{"result": {"tab": {"browserPageId": "p1", "url": "https://app.example.test/"}}}'
READ_SECONDS = 5
HOSTS_READ = ["git", "config", "--global", "--get-all", "long-running.productionHost"]
OPENS_ITS_TARGET = frozenset({"goto", "tab"})
DRIVES_A_PAGE = OPENS_ITS_TARGET | frozenset({"back", "check", "clear", "click", "dblclick", "dialog", "download", "drag", "eval", "exec", "fill", "find", "focus", "forward", "get", "highlight", "hover", "inserttext", "is", "keypress", "mouse", "reload", "screenshot", "scroll", "scrollintoview", "select", "set", "snapshot", "storage", "type", "uncheck", "upload", "wait"})
REFUSAL = "A lane never calls production as the owner or drives the owner's signed-in Orca browser, read or write, under any authority. Prove it with a test identity or a no-write probe, or ask for the owner's identity with `lr ask-owner`."


@on(  # capt-hook: no-escape the owner ordered a refusal, and a call made under his identity cannot be taken back
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash")],
    tests={
        Input(command="orca tab create --url https://app.example.test/ --json", env=LANE, commands=HOSTS): Block(pattern=r"^A lane never calls production"),
        Input(command="orca goto --url https://app.example.test/settings", env=LANE, commands=HOSTS): Block(),
        Input(command="""orca eval --page p1 --expression '(async()=>{const r=await fetch("/api/graphql/Viewer",{method:"POST"});return r.json()})()' --json""", env=LANE, commands=HOSTS | {"orca tab": HIS_TAB}): Block(),
        Input(command="orca eval --page=p1 --expression 'document.title'", env=LANE, commands=HOSTS | {"orca tab": HIS_TAB}): Block(),
        Input(command="orca exec --page p1 --command 'click e8' --json", env=LANE, commands=HOSTS | {"orca tab": HIS_TAB}): Block(),
        Input(command="orca snapshot --page p1 --interactive --json", env=LANE, commands=HOSTS | {"orca tab": HIS_TAB}): Block(),
        Input(command="orca eval --expression 'document.title'", env=LANE, commands=HOSTS | {"orca tab": HIS_ACTIVE_TAB}): Block(),
        Input(command="""orca eval --page p2 --expression 'fetch("https://app.example.test/api/graphql/Viewer")'""", env=LANE, commands=HOSTS | {"orca tab": HIS_TAB}): Block(),
        Input(command="orca exec --page p2 --command 'open https://app.example.test/'", env=LANE, commands=HOSTS | {"orca tab": HIS_TAB}): Block(),
        Input(command="orca tab create --url http://localhost:3000/ --json", env=LANE, commands=HOSTS): Allow(),
        Input(command="orca tab create --url https://b-1a2b.app.example.test/ --json", env=LANE, commands=HOSTS): Allow(),
        Input(command="orca goto --url https://app.example.test.preview.dev/", env=LANE, commands=HOSTS): Allow(),
        Input(command="orca eval --page p2 --expression 'document.title'", env=LANE, commands=HOSTS | {"orca tab": HIS_TAB}): Allow(),
        Input(command="orca tab list --json", env=LANE, commands=HOSTS): Allow(),
        Input(command="orca tab close --page p1 --json", env=LANE, commands=HOSTS): Allow(),
        Input(command="orca terminal list --json", env=LANE, commands=HOSTS): Allow(),
        Input(command="agent-browser open https://app.example.test/", env=LANE, commands=HOSTS): Allow(),
        Input(command="ci mac-box ssh box-1 -- iris status", env=LANE, commands=HOSTS): Allow(),
        Input(command="echo 'orca tab create --url https://app.example.test/'", env=LANE, commands=HOSTS): Allow(),
        Input(command="orca tab create --url https://app.example.test/", env=LANE, commands={"git config": (1, "")}): Allow(),
        Input(command="orca tab create --url https://app.example.test/", env={}, commands=HOSTS): Allow(),
    },
)
def no_production_as_him(evt: BaseHookEvent) -> HookResult | None:
    driving = [call.args for call in evt.command.calls("orca") if call.args and drives_a_page(call.args)]
    if not driving or not (hosts := read(HOSTS_READ, evt.cwd).split()):
        return None
    production = re.compile(rf"(?<![\w.-])(?:{'|'.join(map(re.escape, hosts))})(?![\w-]|\.\w)", re.IGNORECASE)
    return evt.block(REFUSAL) if any(production.search(url) for args in driving for url in (*args, *page_urls(args, evt.cwd))) else None


def drives_a_page(args: tuple[str, ...]) -> bool:
    return args[0] in DRIVES_A_PAGE and (args[0] != "tab" or args[1:2] == ("create",))


def page_urls(args: tuple[str, ...], cwd: str) -> list[str]:
    if args[0] in OPENS_ITS_TARGET:
        return []
    page = next((word.removeprefix("--page=") if word != "--page" else args[i + 1] for i, word in enumerate(args) if word == "--page" or word.startswith("--page=")), None)
    said = json.loads(read(["orca", "tab", "list" if page else "current", "--json"], cwd) or "{}").get("result", {})
    return [tab.get("url", "") for tab in said.get("tabs", [said.get("tab", {})]) if page in (None, tab.get("browserPageId"))]


def read(argv: list[str], cwd: str) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, cwd=cwd, timeout=reqenv.clamp_timeout(READ_SECONDS), env=dict(reqenv.env_map())).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""
