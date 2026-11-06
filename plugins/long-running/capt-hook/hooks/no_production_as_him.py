from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path, PurePosixPath

from captain_hook import Allow, BaseHookEvent, Block, Cmd, Event, FileFixture, HookResult, Input, ast_grep, on, Tool
from captain_hook.annotations import dispatch_pairs
from captain_hook.transcripts import lane_transcript_path
from cc_transcript import UserEvent, parse
from captain_hook.util import reqenv

from .compaction_handoff import Bound
from .register_injection import BRIEF_FILE, SLICE_HEADER

LANE = {"CLAUDE_LONG_RUNNING_LANE": "lane-a"}
ROOT = {"CLAUDE_LONG_RUNNING_LANE": "root"}
HOSTS = {"git config": "app.example.test\n"}
HIS_TAB = '{"result": {"tabs": [{"browserPageId": "p1", "url": "https://app.example.test/"}, {"browserPageId": "p2", "url": "http://localhost:3000/"}]}}'
HIS_ACTIVE_TAB = '{"result": {"tab": {"browserPageId": "p1", "url": "https://app.example.test/"}}}'
READ_SECONDS = 5
HOSTS_READ = ["git", "config", "--global", "--get-all", "long-running.productionHost"]
BOXES_READ = ["git", "config", "--global", "--get-all", "long-running.signedInBox"]
CONFIG_READ = ["git", "config", "--global", "--list"]
PROOF_KEY = "long-running.signedinproof="
BOXES = {"git config --global --get-all long-running.signedInBox": "shape-box\nother-box\n", "git config --global --list": "user.name=lane\n"}
GRANTED = BOXES | {"git config --global --list": "user.name=lane\nlong-running.signedinproof=lane-a=release-50287\nlong-running.signedinproof=lane-b=release-50300\n"}
PERSONAL = "IRIS_TEAM=personal IRIS_RELEASE_PROOF=release-50287"
IRIS_APP = frozenset({"Iris", "Iris.app"})
SAND = frozenset({"sand", "sand-next"})
OPENS_ITS_TARGET = frozenset({"goto", "tab"})
DRIVES_A_PAGE = OPENS_ITS_TARGET | frozenset({"back", "check", "clear", "click", "dblclick", "dialog", "download", "drag", "eval", "exec", "fill", "find", "focus", "forward", "get", "highlight", "hover", "inserttext", "is", "keypress", "mouse", "reload", "screenshot", "scroll", "scrollintoview", "select", "set", "snapshot", "storage", "type", "uncheck", "upload", "wait"})
IRIS_REFUSAL = "This test Mac's Iris is signed in as the owner, so launching it calls production as him, and only a released-build proof on his Personal team that root granted this lane may. Otherwise run the check on a fresh box as a test user on staging: `ci mac-box up <name>`."
REFUSAL = "A lane never calls production as the owner or drives the owner's signed-in Orca browser, read or write, under any authority. Prove it with a test identity or a no-write probe, or ask for the owner's identity with `lr ask-owner`."
BRIEFED = re.compile(rf"\bYour brief is ({BRIEF_FILE.pattern})(?![\w.~/-])")


SESSION = Path(__file__).parent / "tests" / "fixtures" / "grant-session.jsonl"


def brief_naming(lane: str) -> FileFixture:
    return FileFixture(home=True, name="relay-brief.md", content=f"Register slice: lane={lane} step=1 topics=credential-write\nRelay the keys.\n")


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


@on(  # capt-hook: no-escape the owner ordered a refusal, and a call made under his identity cannot be taken back
    Event.PreToolUse,
    only_if=[Bound(), Tool("Bash")],
    tests={
        Input(command="ci mac-box ssh shape-box -- open -a Iris", env=LANE, commands=BOXES): Block(pattern=r"^This test Mac's Iris is signed in as the owner"),
        Input(command="""AWS_PROFILE="$DEVBOX_AWS_PROFILE" tools/ci mac-box ssh other-box -- 'open -a "Iris"'""", env=LANE, commands=BOXES): Block(),
        Input(command="ci mac-box ssh shape-box -- open /Applications/Iris.app", env=LANE, commands=BOXES): Block(),
        Input(command="ci mac-box ssh shape-box -- '/Applications/Iris.app/Contents/MacOS/Iris &'", env=LANE, commands=BOXES): Block(),
        Input(command="ci mac-box ssh shape-box -- ~/rel/sand iris exec --prompt hi", env=LANE, commands=BOXES): Block(),
        Input(command="ci mac-box ssh shape-box <<'EOF'\nopen -a Iris\nEOF", env=LANE, commands=BOXES): Block(),
        Input(command="ssh -i ~/.config/mac-box/shape-box/id_ed25519 ec2-user@10.0.0.4 'open -a Iris'", env=LANE, commands=BOXES): Block(),
        Input(command="ci mac-box ssh shape-box -- IRIS_PLANE=staging sand iris exec --prompt hi", env=LANE, commands=BOXES): Allow(),
        Input(command="ci mac-box ssh fresh-box -- open -a Iris", env=LANE, commands=BOXES): Allow(),
        Input(command="ci mac-box ssh shape-box-2 -- open -a Iris", env=LANE, commands=BOXES): Allow(),
        Input(command="ci mac-box ssh shape-box -- ls ~/rel ~/.iris", env=LANE, commands=BOXES): Allow(),
        Input(command="ci mac-box ssh shape-box -- pkill -x Iris", env=LANE, commands=BOXES): Allow(),
        Input(command="ci mac-box ssh shape-box -- ls /Applications/Iris.app/Contents/MacOS/", env=LANE, commands=BOXES): Allow(),
        Input(command="ci mac-box ssh shape-box -- 'echo open -a Iris'", env=LANE, commands=BOXES): Allow(),
        Input(command="ci mac-box list", env=LANE, commands=BOXES): Allow(),
        Input(command="open -a Iris", env=LANE, commands=BOXES): Allow(),
        Input(command="ci mac-box ssh shape-box -- open -a Iris", env=LANE, commands={"git config": (1, "")}): Allow(),
        Input(command="ci mac-box ssh shape-box -- open -a Iris", env={}, commands=BOXES): Allow(),
        Input(command=f"ci mac-box ssh shape-box -- {PERSONAL} open -a Iris", env=LANE, commands=GRANTED): Allow(),
        Input(command=f"ci mac-box ssh shape-box -- {PERSONAL} ~/rel/sand iris exec --prompt hi", env=LANE, commands=GRANTED): Allow(),
        Input(command="ci mac-box ssh shape-box -- IRIS_TEAM=acme IRIS_RELEASE_PROOF=release-50287 open -a Iris", env=LANE, commands=GRANTED): Block(),
        Input(command="ci mac-box ssh shape-box -- IRIS_RELEASE_PROOF=release-50287 open -a Iris", env=LANE, commands=GRANTED): Block(),
        Input(command="ci mac-box ssh shape-box -- IRIS_TEAM=personal open -a Iris", env=LANE, commands=GRANTED): Block(),
        Input(command="ci mac-box ssh shape-box -- IRIS_TEAM=personal IRIS_RELEASE_PROOF=release-50300 open -a Iris", env=LANE, commands=GRANTED): Block(),
        Input(command=f"ci mac-box ssh shape-box -- {PERSONAL} open -a Iris", env={"CLAUDE_LONG_RUNNING_LANE": "lane-c"}, commands=GRANTED): Block(),
        Input(command=f"ci mac-box ssh shape-box -- {PERSONAL} open -a Iris", env=LANE, commands=BOXES): Block(),
        Input(command=f"ci mac-box ssh shape-box -- 'open -a Iris; {PERSONAL} open -a Iris'", env=LANE, commands=GRANTED): Block(),
        Input(command=f"ci mac-box ssh shape-box -- {PERSONAL} open -a Iris", env=ROOT, agent_id="a1", transcript=SESSION, commands=GRANTED): Allow(),
        Input(command=f"ci mac-box ssh shape-box -- {PERSONAL} open -a Iris", env=ROOT, agent_id="a2", transcript=SESSION, commands=GRANTED): Block(),
        Input(command=f"ci mac-box ssh shape-box -- {PERSONAL} open -a Iris", env=LANE, agent_id="a3", transcript=SESSION, commands=GRANTED): Block(),
        Input(command=f"ci mac-box ssh shape-box -- {PERSONAL} open -a Iris", env=ROOT, agent_id="a4", transcript=SESSION, commands=GRANTED): Block(),
        Input(command=f"ci mac-box ssh shape-box -- {PERSONAL} open -a Iris", env=ROOT, agent_id="a5", transcript=SESSION, file=brief_naming("lane-a"), commands=GRANTED): Allow(),
        Input(command=f"ci mac-box ssh shape-box -- {PERSONAL} open -a Iris", env=ROOT, agent_id="a5", transcript=SESSION, file=brief_naming("lane-c"), commands=GRANTED): Block(),
        Input(command=f"ci mac-box ssh shape-box -- {PERSONAL} open -a Iris", env=LANE, agent_id="a9", transcript=SESSION, commands=GRANTED): Block(),
        Input(command=f"ci mac-box ssh shape-box -- {PERSONAL} open -a Iris", env={"CLAUDE_LONG_RUNNING_LANE": "", "CLAUDE_LONG_RUNNING_DRIVE": "d"}, commands=BOXES | {"git config --global --list": "long-running.signedinproof==release-50287\n"}): Block(),
        Input(command=f"ci mac-box ssh shape-box -- {PERSONAL} open -a Iris  # ccx:lane=lane-a", env=ROOT, agent_id="a2", transcript=SESSION, commands=GRANTED): Block(),
        Input(command=f"ci mac-box ssh shape-box -- {PERSONAL} open -a Iris", env=ROOT, commands=GRANTED): Block(),
        Input(command=f"ci mac-box ssh shape-box -- {PERSONAL} open -a Iris", env=ROOT, commands=GRANTED | {"git config --global --list": "long-running.signedinproof=root=release-50287\n"}): Allow(),
        Input(command=f"ci mac-box ssh shape-box -- {PERSONAL} open -a Iris", env=ROOT, agent_id="a1", transcript=SESSION, commands=GRANTED | {"git config --global --list": "long-running.signedinproof=root=release-50287\n"}): Block(),
    },
)
def no_iris_as_him_on_a_test_mac(evt: BaseHookEvent) -> HookResult | None:
    reaching = [call.args[2:] for call in evt.command.calls("ci") if call.args[:2] == ("mac-box", "ssh")] + [call.args for call in evt.command.calls("ssh")]
    if not reaching or not any(iris_launches(cmd) for cmd in remote_commands(reaching, evt.command.raw) if cmd) or not (boxes := set(read(BOXES_READ, evt.cwd).split())):
        return None
    if not any(boxes & set(PurePosixPath(word).parts) for args in reaching for word in args):
        return None
    if not (config := read(CONFIG_READ, evt.cwd)):
        return None
    grants = (line.removeprefix(PROOF_KEY).partition("=") for line in config.splitlines() if line.startswith(PROOF_KEY))
    proofs = {proof for lane, _, proof in grants if lane == granted_lane(evt)}
    launches = [call for cmd in remote_commands(reaching, evt.command.raw) if cmd for call in iris_launches(cmd)]
    return None if all(proves_a_release(call, proofs) for call in launches) else evt.block(IRIS_REFUSAL)


def granted_lane(evt: BaseHookEvent) -> str | None:
    if not evt.agent_id:
        return reqenv.env_map().get("CLAUDE_LONG_RUNNING_LANE") or None
    if not (parent := evt._raw.get("transcript_path")) or not (own := lane_transcript_path(parent, evt.agent_id)).is_file():
        return None
    dispatch = next((event.text for event in parse(own).events if isinstance(event, UserEvent)), "")
    pairs = dict(dispatch_pairs(dispatch))
    return pairs["lane"] if "lane" in pairs else briefed_lane(dispatch)


def briefed_lane(dispatch: str) -> str | None:
    if not (named := BRIEFED.search(dispatch)):
        return None
    try:
        brief = Path(named[1]).expanduser().read_text(errors="replace")
    except (OSError, RuntimeError):
        return None
    return sliced[1] if (sliced := SLICE_HEADER.search(brief)) else None


def remote_commands(reaching: list[tuple[str, ...]], raw: str) -> list[Cmd | None]:
    tails = [args[args.index("--") + 1 :] if "--" in args else args[1:] for args in reaching]
    quoted = [word for args in reaching for word in args if " " in word]
    heredocs = [node.text for node in ast_grep.find_kinds(raw, "bash", frozenset({"heredoc_body"}))]
    return [Cmd.parse(text) for text in (*(" ".join(tail) for tail in tails), *quoted, *heredocs)]


def iris_launches(cmd: Cmd) -> list:
    return [
        call
        for call in cmd.calls()
        if (call.name == "open" and any(PurePosixPath(arg).name in IRIS_APP for arg in call.args))
        or "Iris.app" in PurePosixPath(call.command.executable).parts
        or (call.name in SAND and call.args[:1] == ("iris",) and dict(call.source.env).get("IRIS_PLANE") != "staging")
    ]


def proves_a_release(call, proofs: set[str]) -> bool:
    env = dict(call.source.env)
    return env.get("IRIS_TEAM") == "personal" and env.get("IRIS_RELEASE_PROOF") in proofs


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
