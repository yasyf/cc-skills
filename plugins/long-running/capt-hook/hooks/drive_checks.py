from __future__ import annotations

import json
import os
import re
import runpy
import sqlite3
import subprocess
import sys
import tempfile
import threading
from collections import OrderedDict
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from captain_hook import (
    Allow,
    BaseHookEvent,
    Block,
    Event,
    FileFixture,
    HookResult,
    Input,
    Rewrite,
    Tool,
    Warn,
    on,
)
from captain_hook.dispatch import SYNC_DEADLINE_MARGIN_SECONDS, collect_budget
from cc_transcript.tools import tool_name_matches

newest_install = runpy.run_path(str(Path(__file__).resolve().parents[2] / "hooks" / "installed.py"))["root"]
SLACK = "mcp__plugin_cc-slack_cc-slack__"
SAY = "mcp__plugin_long-running_drive__say"
POSTS = (*(SLACK + tool for tool in ("slack_dm_status", "slack_reply", "slack_send", "slack_edit")), SAY)
NO_DRIVE = "0a0a0a0a-no-drive-session"
COORDINATOR = "coordinator-fixture"
COMMUNICATOR = "communicator-fixture"
DRIVE_AGENTS = "long-running:"
HANDLER = "long-running:handler"
BUILDER = "long-running:builder"
POST = "mcp__plugin_cc-inbox_cci__post"
FILE_TOOLS = ("Read", "Write", "Edit", "Bash")
SLACK_READS = (SLACK + "slack_thread", SLACK + "slack_history")
QUESTIONS = ("AskUserQuestion", "request_access", "request_full_control", "request_teach_access")
SHELL = ("Bash",)
SEARCHES = ("Bash", "Read", "Grep")
AGENT = ("Agent",)
RECORDS = (POST,)
PRE_TOOL_CHECKS: dict[str, tuple[str, ...] | None] = {
    "no_file_tools.py": FILE_TOOLS,
    "no_slack_reads.py": SLACK_READS,
    "no_terminal_questions.py": QUESTIONS,
    "agent_brief.py": AGENT,
    "scratch_folder.py": AGENT,
    "record_lines.py": RECORDS,
    "communicator_calls.py": None,
    "message_limits.py": POSTS,
    "bash_guard.py": SEARCHES,
    "landing_guard.py": SHELL,
    "role_rules.py": None,
}
REMOTE_LANE = "LONG_RUNNING_REMOTE_LANE"
QUOTING = re.compile(r"[\"'\\]")
EXPANSION = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")
LANDING_WORDS = re.compile(r"(?<![\w.-])(?:gh|ccx|ci|cci|stack-enqueue)(?![\w.-])")
HELPER_WORDS = re.compile(r"(?<![\w.-])(?:helper|brew)(?![\w.-])")
BATCHES_KEPT = 64
RUNNER = """
import contextlib, io, json, runpy, sys, traceback
payload, scripts = sys.stdin.read(), sys.argv[1:]
for script in scripts:
    out, err, code = io.StringIO(), io.StringIO(), 0
    sys.argv, sys.stdin = [script], io.StringIO(payload)
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            runpy.run_path(script, run_name="__main__")
        except SystemExit as exited:
            if isinstance(exited.code, str):
                print(exited.code, file=sys.stderr)
            code = 1 if isinstance(exited.code, str) else exited.code or 0
        except Exception:
            traceback.print_exc()
            code = 1
    print(json.dumps({"script": script, "code": code, "stdout": out.getvalue(), "stderr": err.getvalue()}), flush=True)
"""
TURN = Path(__file__).resolve().parent / "tests" / "fixtures" / "turn.jsonl"
COMMUNICATOR_TURN = TURN.with_name("communicator.jsonl")
HANDLER_TURN = TURN.with_name("handler.jsonl")
BRIEFS = TURN.with_name("state") / "fixture" / "jobs"
HIS_DM_HOME = FileFixture(
    home=True,
    name=".claude/plugins/installed_plugins.json",
    content=json.dumps({"plugins": {"cc-slack@cc-slack": [{"installPath": str(TURN.with_name("cc-slack"))}]}}),
)
HANDLER_SESSION = "handler-fixture"
THREAD = "1794378983.440769"
ACK = "You set the staging-claim rule and want the fuzzing box back; both orders are handed off."
RESUMED = "drive job resume now works from a session that did not start the job's agent."
AWAY = "Refused on purpose: he is away, since he said so or has written nothing for an hour."
ASKED = (
    "POST Yes: the old attributes are deleted, dreaming is cleaned up, and the 1024 memory indexes are gone."
    " Each landed in its own PR and merged, and the watch shows no write to the old tables since."
    " The only memory work left is the index rebuild, which runs tonight from the cluster's schedule and"
    " needs nothing from you; I will post here when it finishes and the counts match."
)
WEIGH = "import json, sys; sys.path.insert(0, sys.argv[1]); from drive import settled; print(json.dumps(settled.weighed(json.load(sys.stdin))))"
WEIGHS = "python3 -I -c"
HELD = {WEIGHS: json.dumps({"verdict": "hold", "note": ""})}
PASSED = {WEIGHS: json.dumps({"verdict": "pass", "note": ""})}
GUESSED = "The cloud's inbox reader queries Postgres straight."
GUESSED_CLAIMS = [{"claim": GUESSED, "read": "cloudrunner/inbox.go"}]
CLOUD_READ = 'ccx_code_read {"path": "go/sand/next/internal/cloudrunner/inbox.go"}\n57  rows, err := r.postgres.Query(ctx, selectInbox, agentID)'
NOT_READ = 'none: no call of the handler\'s own holds "cloudrunner/inbox.go"'
HELD_BACK = "Jev held back this post: claim \"The cloud's inbox reader queries Postgres straight.\" does not stand on its read."
UNSURE = "message_limits: Jev is unsure of finding he is away, so the post goes out as written: if one is wrong, reword the post in place."
UNROOMED = (
    "refused: no Jev request of 10,000 characters holds these parts whole beside the post's 1,678 characters, largest first:"
    ' record:716852 "716852" beside the 5 claims naming it, 12,936.'
    " Name a grep, jq filter or line range of each in its claims, never a smaller `cci read` budget, then call say again."
)


def verdict(kind: str, note: str) -> dict[str, str]:
    """An inline test's stub of the verdict `weighed` reads from settled.weighed: hold, warn or pass, beside its note."""
    return {WEIGHS: json.dumps({"verdict": kind, "note": note})}


def handler_reads(read: str) -> str:
    """What message_limits.py hands Jev for a handler's say post its checks found nothing in: the read its claim names, as its newest call that read names with its whole result, and the claim naming that read by its id."""
    named = {"id": "read:1", "read": "cloudrunner/inbox.go", "source": 'the read the posting handler\'s claims name as "cloudrunner/inbox.go"', "quote": read}
    return json.dumps({"his_words": [], "reads": [named], "post": {"text": GUESSED, "thread_ts": THREAD, "claims": [{"claim": GUESSED, "read": "read:1"}]}})


def refused_post(text: str, reason: str, counted: str = "") -> dict:
    """What message_limits.py hands Jev for a say post one finding refuses, beside the rule that finding stands for, and the finding again as counted when a count or a mark decides it."""
    denied = {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}
    findings = [{"rule": reason, "found": reason}]
    return {"hookSpecificOutput": denied, "rule": reason, "findings": findings, "his_words": [], "reads": [], "post": {"text": text}} | ({"counted": counted} if counted else {})


def state_root() -> Path:
    """The folder each drive's saved state lives under, as the drive's own `state.root` reads it from this process's environment."""
    if explicit := os.environ.get("LONG_RUNNING_STATE"):
        return Path(explicit)
    if xdg := os.environ.get("XDG_STATE_HOME"):
        return Path(xdg) / "long-running"
    return Path.home() / ".local" / "state" / "long-running"


BINDINGS: dict[Path, tuple[tuple[int, int, int], frozenset[str]]] = {}
BOUND_FIELDS = ("driver", "roles", "subcoordinators")


def saved_sessions(data: dict) -> frozenset[str]:
    """Every session a saved drive binds, as `state.session_drives` reads them: each role's, each sub-coordinator's and each handler's."""
    roles = {"coordinator": data["driver"]} if "driver" in data else data["roles"]
    held = [*roles.values(), *data.get("subcoordinators", {}).values(), *data.get("handlers", {}).values()]
    return frozenset(entry["session"] for entry in held)


def saved_bindings(folder: Path) -> dict:
    """A drive's bindings as its database saved them, read as `state.saved` reads them: its roles, sub-coordinators and handlers rows."""
    fields = ", ".join("?" * len(BOUND_FIELDS))
    with closing(sqlite3.connect(folder / "drive.db")) as db:
        rows = db.execute(f"SELECT collection, key, body FROM rows WHERE collection = '' AND key IN ({fields}) OR collection = 'handlers'", BOUND_FIELDS).fetchall()
    return {key: json.loads(body) for collection, key, body in rows if not collection} | {"handlers": {key: json.loads(body) for collection, key, body in rows if collection}}


def bound_sessions() -> frozenset[str]:
    """The sessions any drive on this Mac binds: each drive's binding rows read from its database at each call, a short read in one transaction, and a drive still on drive.json parsed again only once that file changes, so a call from a session no drive binds costs no parse."""
    root = state_root()
    if not root.is_dir():
        return frozenset()
    bound: set[str] = set()
    for entry in os.scandir(root):
        folder = Path(entry.path)
        if (folder / "drive.db").is_file():
            bound |= saved_sessions(saved_bindings(folder))
            continue
        path = folder / "drive.json"
        try:
            seen = path.stat()
        except (FileNotFoundError, NotADirectoryError):
            continue
        stamp = (seen.st_mtime_ns, seen.st_size, seen.st_ino)
        if (kept := BINDINGS.get(path)) is None or kept[0] != stamp:
            kept = BINDINGS[path] = (stamp, saved_sessions(json.loads(path.read_text())))
        bound |= kept[1]
    return frozenset(bound)


def unquoted_command(payload: dict) -> str:
    """A Bash call's command without its quotes, each variable this process's environment sets expanded, as the drive's checks expand it."""
    if payload.get("tool_name") != "Bash":
        return ""
    return EXPANSION.sub(lambda named: os.environ.get(named[1], named[0]), QUOTING.sub("", payload["tool_input"].get("command") or ""))


def lands_or_posts(payload: dict) -> bool:
    """Whether landing_guard may refuse this call from a session no drive binds: a Bash command naming gh, ccx, ci, cci or stack-enqueue, which it reads for a landing on a guarded repo or a cci post the drive routes."""
    return bool(LANDING_WORDS.search(unquoted_command(payload)))


def restarts_helper_or_ships_remote(payload: dict) -> bool:
    """Whether bash_guard may refuse this call from a session no drive binds: a Bash command naming helper or brew, which may restart the hook helper, or any Bash command on a Sprite lane, whose ship asks `drive job held`."""
    return payload.get("tool_name") == "Bash" and (bool(os.environ.get(REMOTE_LANE)) or bool(HELPER_WORDS.search(unquoted_command(payload))))


def starts_drive_agent(payload: dict) -> bool:
    """Whether scratch_folder acts on this call from a session no drive binds: an Agent call starting a drive agent, whose Scratch folder it makes."""
    return str(payload["tool_input"].get("subagent_type") or "").startswith(DRIVE_AGENTS)


UNBOUND_CHECKS: dict[str, Callable[[dict], bool]] = {
    "landing_guard.py": lands_or_posts,
    "bash_guard.py": restarts_helper_or_ships_remote,
    "scratch_folder.py": starts_drive_agent,
}


def checks_due(evt: BaseHookEvent, script: str) -> tuple[str, ...]:
    """The drive checks this hook event runs: on a PreToolUse call, this one and each other check matching its tool, else this one alone; a check runs for a drive agent or a session a drive binds, and for any other session only where it can act on one no drive binds."""
    payload = evt._raw
    tool = payload.get("tool_name")
    pending = evt.event_name == Event.PreToolUse and payload.get("tool_use_id")
    matching = [check for check, tools in PRE_TOOL_CHECKS.items() if check == script or tools is None or tool_name_matches(tool, "|".join(tools))] if pending else [script]
    if (payload.get("agent_type") or "").startswith(DRIVE_AGENTS):
        return tuple(matching)
    unbound = [check for check in matching if check in UNBOUND_CHECKS and UNBOUND_CHECKS[check](payload)]
    if len(unbound) < len(matching) and (payload.get("session_id") or "") in bound_sessions():
        return tuple(matching)
    return tuple(unbound)


@dataclass(slots=True)
class Batch:
    due: tuple[str, ...]
    waiting: set[str]
    settled: threading.Condition = field(default_factory=threading.Condition)
    ran: dict[str, dict] = field(default_factory=dict)
    failed: str = ""
    done: bool = False


def run_checks(payload: dict, batch: Batch) -> None:
    """Run each due script from the newest installed long-running in one interpreter, this worker's own, which imports spawnllm as every drive check needs, each on the payload as its own stdin, settling each one's exit code, stdout and stderr in the batch the moment it finishes, so a check's verdict waits only on the checks ahead of it."""
    hooks = newest_install() / "hooks"
    try:
        with tempfile.TemporaryFile("w+") as errors, subprocess.Popen(
            [sys.executable, "-I", "-c", RUNNER, *(str(hooks / script) for script in batch.due)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=errors,
            text=True,
            cwd=payload.get("cwd") or None,
        ) as child:
            child.stdin.write(json.dumps(payload))
            child.stdin.close()
            for line in child.stdout:
                result = json.loads(line)
                with batch.settled:
                    batch.ran[Path(result.pop("script")).name] = result
                    batch.settled.notify_all()
            if child.wait():
                errors.seek(0)
                batch.failed = f"{', '.join(batch.due)} exited {child.returncode}:\n{errors.read()}"
    finally:
        with batch.settled:
            batch.done = True
            batch.settled.notify_all()


def result_of(batch: Batch, script: str) -> dict:
    with batch.settled:
        batch.settled.wait_for(lambda: script in batch.ran or batch.done)
        if script not in batch.ran:
            raise RuntimeError(batch.failed or f"{script} gave its batched run no result")
        return batch.ran[script]


BATCHES: OrderedDict[tuple[str, str], Batch] = OrderedDict()
BATCHING = threading.Lock()


def batched(payload: dict, script: str, due: tuple[str, ...]) -> dict:
    """This check's result from the one interpreter every due check of a tool call shares: the first check to arrive starts it, and each takes its own result as soon as its script finishes."""
    key = (payload.get("session_id") or "", payload["tool_use_id"])
    with BATCHING:
        if starts := (batch := BATCHES.get(key)) is None:
            batch = BATCHES[key] = Batch(due, set(due))
            while len(BATCHES) > BATCHES_KEPT:
                BATCHES.popitem(last=False)
    if script not in batch.due:
        return ran_alone(payload, script)
    if starts:
        threading.Thread(target=run_checks, args=(payload, batch), name=f"drive-checks-{key[1]}", daemon=True).start()
    try:
        return result_of(batch, script)
    finally:
        with BATCHING:
            batch.waiting.discard(script)
            if not batch.waiting and BATCHES.get(key) is batch:
                del BATCHES[key]


def ran_alone(payload: dict, script: str) -> dict:
    """One check run on its own, as `python3 -I <script>` launched on this worker's own interpreter, which imports spawnllm, since no other check's verdict waits behind it."""
    ran = subprocess.run(
        ["python3", "-I", str(newest_install() / "hooks" / script)],
        executable=sys.executable,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=payload.get("cwd") or None,
    )
    return {"code": ran.returncode, "stdout": ran.stdout, "stderr": ran.stderr}


def checked(evt: BaseHookEvent, script: str) -> HookResult | None:
    """Run the drive's own check for this call from the newest installed long-running, since capt-hook keeps this pack loaded from the install it first found while an install leaves the pack's files unchanged, and let Jev weigh each refusal it makes by the rule the check names, else by its words, so a check that matched a call its rule never meant lets the call through, and every post the check hands with his words before it goes out; a merge, release or public-body guard, or a refusal decided by the call's shape alone, the check marks unjudged and it blocks outright, and a check that exits non-zero raises with its own stderr, so capt-hook's log names why it failed."""
    payload = evt._raw
    due = checks_due(evt, script)
    if script not in due:
        return None
    ran = batched(payload, script, due) if len(due) > 1 else ran_alone(payload, script)
    if ran["code"]:
        raise RuntimeError(f"{script} exited {ran['code']}:\n{ran['stderr']}")
    if not (said := ran["stdout"].strip()):
        return None
    output = json.loads(said)
    found = output.get("hookSpecificOutput") or {}
    reason = found.get("permissionDecisionReason") or (output.get("decision") == "block" and output["reason"]) or ""
    if "his_words" in output:
        return judged(evt, output)
    if reason and output.get("judged", True):
        call = {"tool": payload.get("tool_name") or payload.get("hook_event_name") or "", "input": payload.get("tool_input") or {"message": payload.get("last_assistant_message") or ""}}
        return refused(evt, reason, weighed(evt, {"refusal": {"rule": output.get("rule") or reason, "call": call}}))
    if reason:
        return evt.block(reason)
    if made := found.get("updatedInput"):
        return evt.rewrite(made, note=found.get("additionalContext"))
    if context := found.get("additionalContext"):
        return evt.context(context)
    return None


def weighed(evt: BaseHookEvent, request: dict) -> dict:
    """Jev's verdict, hold, warn or pass, and its note, from the newest installed long-running's settled.weighed, in a process of its own on this worker's interpreter, which imports spawnllm, as each drive check runs, so a release changing how the drive asks Jev reaches this pack without a restart; the request carries what is left of capt-hook's deadline, so Jev's answer or its warning comes before the hook's time runs out."""
    ran = subprocess.run(
        ["python3", "-I", "-c", WEIGH, str(newest_install() / "lib")],
        executable=sys.executable,
        input=json.dumps(request | {"seconds": collect_budget(2 * SYNC_DEADLINE_MARGIN_SECONDS)}),
        capture_output=True,
        text=True,
        cwd=evt._raw.get("cwd") or None,
    )
    if ran.returncode:
        raise RuntimeError(f"settled.weighed exited {ran.returncode}:\n{ran.stderr}")
    return json.loads(ran.stdout)


def refused(evt: BaseHookEvent, reason: str, verdict: dict) -> HookResult | None:
    """A judged refusal as Jev weighed it: the block, beside Jev's note when it is unsure, a warning when Jev gave no verdict in time, and the call let through when Jev reads it as a match its rule never meant."""
    match verdict["verdict"]:
        case "hold":
            return evt.block("\n\n".join(filter(None, [reason, verdict["note"]])))
        case "warn":
            return evt.context(f"{verdict['note']}\n```\n{reason}\n```")
    return None


def judged(evt: BaseHookEvent, output: dict) -> HookResult | None:
    """Jev's verdict on a post the drive's checks hand with his words, as settled.post_verdict weighs it, before the post goes out: a held post stays back; a warning, such as a verdict Jev could not finish, goes out beside the post with what the checks heard."""
    heard = (output.get("hookSpecificOutput") or {}).get("additionalContext") or ""
    verdict = weighed(evt, {"post": output})
    if verdict["verdict"] == "hold":
        return evt.block(verdict["note"])
    if told := "\n\n".join(filter(None, [heard, verdict["note"]])):
        return evt.context(told)
    return None


@on(
    Event.PreToolUse,
    only_if=[Tool(*FILE_TOOLS)],
    tests={Input(command="ls", session_id=NO_DRIVE): Allow()},
)
def no_file_tools(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "no_file_tools.py")


@on(
    Event.PreToolUse,
    only_if=[Tool(*SLACK_READS)],
    tests={Input(tool=SLACK + "slack_thread", tool_input={"channel": "D0", "ts": "1.0"}, session_id=NO_DRIVE): Allow()},
)
def no_slack_reads(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "no_slack_reads.py")


@on(
    Event.PreToolUse,
    only_if=[Tool(*QUESTIONS)],
    tests={
        Input(tool="AskUserQuestion", tool_input={"questions": [{"question": "Ship it?"}]}, session_id=NO_DRIVE): Allow(),
        Input(tool="AskUserQuestion", tool_input={"questions": [{"question": "Ship it?"}]}, session_id=COORDINATOR): Block(pattern="no drive session asks in its terminal"),
        Input(tool="AskUserQuestion", tool_input={"questions": [{"question": "Ship it?"}]}, session_id=COMMUNICATOR, agent_id="a1", agent_type=BUILDER): Block(
            pattern="return it as your `ASK` or `DESIGN`"
        ),
        Input(tool="AskUserQuestion", tool_input={"questions": [{"question": "Ship it?"}]}, session_id=HANDLER_SESSION, agent_type=HANDLER): Block(
            pattern="which draws his card on it for fixture"
        ),
        Input(tool="mcp__computer-use__request_access", tool_input={"apps": ["Slack"], "reason": "x"}, session_id=NO_DRIVE): Allow(),
        Input(tool="mcp__computer-use__request_access", tool_input={"apps": ["Slack"], "reason": "x"}, session_id=COMMUNICATOR, agent_id="a1", agent_type=BUILDER): Block(
            pattern="return a check they cannot settle as `UNPROVEN: <the check>: <the proof that settles it>`"
        ),
    },
)
def no_terminal_questions(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "no_terminal_questions.py")


@on(
    Event.PreToolUse,
    only_if=[Tool(*SHELL)],
    tests={
        Input(command="ls", session_id=NO_DRIVE): Allow(),
        Input(command="gh pr view 1", session_id=NO_DRIVE, tool_use_id="toolu_unbound"): Allow(),
        Input(command='ccx vcs ship -m "Fix: x"', session_id=COORDINATOR, commands=HELD): Block(pattern="does no order's work itself"),
        Input(command='ccx vcs ship -m "Fix: x"', session_id=COORDINATOR, commands=HELD, tool_use_id="toolu_batched"): Block(pattern="does no order's work itself"),
        Input(command="for j in j855 j966; do drive job replies $j --drive fixture | tail -5; done", session_id=COORDINATOR): Allow(),
        Input(command="orca orchestration check --help", session_id=COORDINATOR): Allow(),
    },
)
def landing_guard(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "landing_guard.py")


@on(
    Event.PreToolUse,
    only_if=[Tool(*SEARCHES)],
    tests={
        Input(command="ls", session_id=NO_DRIVE): Allow(),
        Input(
            command="drive job report j955 --drive fixture; drive status j955 --drive fixture",
            session_id=COMMUNICATOR,
            agent_id="a1",
            agent_type=HANDLER,
        ): Allow(),
        Input(tool="Bash", tool_input={"command": 'drive job resume j1 "go"', "run_in_background": True}, session_id=COORDINATOR): Rewrite(
            pattern="drive job resume j1", run_in_background="False", timeout="20000"
        ),
    },
)
def bash_guard(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "bash_guard.py")


@on(
    Event.PreToolUse,
    only_if=[Tool(*AGENT)],
    tests={
        Input(tool="Agent", tool_input={"subagent_type": HANDLER, "prompt": "Record: 1", "model": "sonnet"}, session_id=NO_DRIVE): Allow(),
        Input(
            tool="Agent",
            tool_input={"subagent_type": HANDLER, "prompt": "Record: 1"},
            session_id=COMMUNICATOR,
            commands=PASSED,
        ): Block(pattern="a handler starts with the drive's handler tool"),
        Input(
            tool="Agent",
            tool_input={"subagent_type": BUILDER, "prompt": f"Read your brief first: {BRIEFS / 'j1.brief.md'}"},
            session_id=COORDINATOR,
            commands=HELD,
        ): Block(pattern="reads as a pass over a team's data"),
        Input(
            tool="Agent",
            tool_input={"subagent_type": BUILDER, "prompt": f"Read your brief first: {BRIEFS / 'j2.brief.md'}"},
            session_id=COORDINATOR,
            commands=PASSED,
        ): Allow(),
        Input(tool="Agent", tool_input={"subagent_type": "long-running:reader", "prompt": f"Read your brief first: {BRIEFS / 'j3.brief.md'}", "name": "desk"}, session_id=COORDINATOR): Rewrite(
            subagent_type="long-running:reader"
        ),
        Input(
            tool="Agent",
            tool_input={"subagent_type": "long-running:reader", "prompt": f"Read your brief first: {BRIEFS / 'j9.brief.md'}"},
            session_id=COORDINATOR,
            commands=PASSED,
        ): Block(pattern="j9.brief.md does not exist"),
    },
)
def agent_brief(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "agent_brief.py")


@on(
    Event.PreToolUse,
    only_if=[Tool(*AGENT)],
    tests={Input(tool="Agent", tool_input={"subagent_type": "long-running:builder", "prompt": "Goal: x"}, session_id=NO_DRIVE): Allow()},
)
def scratch_folder(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "scratch_folder.py")


@on(
    Event.PreToolUse,
    only_if=[Tool(*POSTS)],
    tests={
        Input(tool=SLACK + "slack_dm_status", tool_input={"text": "On it. Then more."}, session_id=NO_DRIVE): Allow(),
        Input(
            tool=SLACK + "slack_dm_status",
            tool_input={"text": "The fix merged."},
            session_id=COMMUNICATOR,
            agent_id="a1",
            transcript=COMMUNICATOR_TURN,
            file=HIS_DM_HOME,
            commands=verdict("hold", HELD_BACK),
        ): Block(pattern="Jev held back this post"),
        Input(
            tool=SLACK + "slack_dm_status",
            tool_input={"text": "The fix merged."},
            session_id=COMMUNICATOR,
            agent_id="a1",
            transcript=COMMUNICATOR_TURN,
            file=HIS_DM_HOME,
            commands=verdict("warn", UNSURE),
        ): Warn(pattern="Jev is unsure of finding he is away"),
        Input(
            tool=SAY,
            tool_input={"text": GUESSED, "thread_ts": THREAD, "claims": GUESSED_CLAIMS},
            session_id=COMMUNICATOR,
            agent_id="a1",
            agent_type=HANDLER,
            transcript=COMMUNICATOR_TURN,
            commands={"python3 -I": handler_reads(NOT_READ), **verdict("hold", HELD_BACK)},
        ): Block(pattern="Jev held back this post"),
        Input(
            tool=SAY,
            tool_input={"text": GUESSED, "thread_ts": THREAD, "claims": GUESSED_CLAIMS},
            session_id=COMMUNICATOR,
            agent_id="a1",
            agent_type=HANDLER,
            transcript=COMMUNICATOR_TURN,
            commands={"python3 -I": json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": UNROOMED}, "judged": False})},
        ): Block(pattern="largest first: record:716852"),
        Input(
            tool=SAY,
            tool_input={"text": ACK, "thread_ts": THREAD, "dry_run": True},
            session_id=COMMUNICATOR,
            transcript=COMMUNICATOR_TURN,
            commands={"python3 -I": json.dumps(refused_post(ACK, AWAY)), **PASSED},
        ): Allow(),
        Input(
            tool=SAY,
            tool_input={"text": "The fix merged."},
            session_id=COMMUNICATOR,
            agent_id="a1",
            agent_type=HANDLER,
            transcript=COMMUNICATOR_TURN,
            commands={"python3 -I": json.dumps(refused_post("The fix merged.", AWAY, AWAY)), **verdict("hold", AWAY)},
        ): Block(pattern="he is away"),
    },
)
def message_limits(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "message_limits.py")


@on(
    Event.PreToolUse,
    only_if=[Tool(*RECORDS)],
    tests={
        Input(tool=POST, tool_input={"kind": "report", "text": "x"}, session_id=NO_DRIVE): Allow(),
        Input(
            tool=POST,
            tool_input={"kind": "report", "lane": "root", "to": "communicator@fixture", "text": "POST The fix is still building.", "attrs": {"thread": THREAD, "progress": "true"}},
            session_id=COORDINATOR,
            transcript=TURN,
            commands=HELD,
        ): Block(pattern="one report per thread per turn"),
        Input(
            tool=POST,
            tool_input={"kind": "report", "lane": "root", "to": "communicator@fixture", "text": "POST New: 663230 shows 0 refused writes.", "attrs": {"thread": THREAD, "progress": "true"}},
            session_id=COORDINATOR,
            transcript=TURN,
            commands=PASSED,
        ): Allow(),
        Input(
            tool=POST,
            tool_input={"kind": "report", "lane": "root", "to": "communicator@fixture", "text": "POST The check that this pod still runs the agent refused an old pod.", "attrs": {"progress": "true"}},
            session_id=COORDINATOR,
            transcript=TURN,
        ): Warn(pattern="this report goes out as written"),
        Input(tool=POST, tool_input={"kind": "report", "lane": "root", "to": "communicator@fixture", "text": ASKED, "attrs": {"progress": "true"}}, session_id=COORDINATOR): Rewrite(
            body="the counts match"
        ),
    },
)
def record_lines(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "record_lines.py")


@on(Event.PreToolUse, tests={Input(tool="WebFetch", tool_input={"url": "https://example.com"}, session_id=NO_DRIVE): Allow()})
def communicator_calls(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "communicator_calls.py")


@on(Event.PreToolUse, advisory_on_deny=True, tests={Input(tool="WebFetch", tool_input={"url": "https://example.com"}, session_id=NO_DRIVE): Allow()})
def role_rules(evt: BaseHookEvent) -> HookResult | None:
    """The rules an install changed since the session's block drew them, beside its next call, kept beside a deny another check gives that call, since the check keeps them as held once it hands them."""
    return checked(evt, "role_rules.py")


@on(Event.Stop, tests={Input(session_id=NO_DRIVE): Allow()})
def quiet_turn(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "quiet_turn.py")


@on(Event.SubagentStop, tests={Input(session_id=NO_DRIVE): Allow()})
def one_line_result(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "one_line_result.py")
