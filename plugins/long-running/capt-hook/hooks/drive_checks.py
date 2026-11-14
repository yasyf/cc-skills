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
from collections.abc import Callable, Sequence
from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

from captain_hook import (
    CONFIRMED,
    UNCONFIRMED,
    Allow,
    BaseHookEvent,
    Block,
    Confirm,
    Event,
    FileFixture,
    HookResult,
    Input,
    Rewrite,
    T,
    Tool,
    Warn,
    WorkflowState,
    on,
    workflow_state,
)
from captain_hook.dispatch import SYNC_DEADLINE_MARGIN_SECONDS, collect_budget
from captain_hook.contexts import PendingToolCall
from captain_hook.prompt import Prompt, dedent_text
from captain_hook.snapshots.client import EvidenceIncomplete
from captain_hook.tickets import (
    Context,
    Decide,
    Evidence,
    Proposal,
    Undecided,
    UserAnswered,
)
from captain_hook.tickets.context import said_evidence
from captain_hook.tickets.decide import DETAIL_SHOWN, UNPERMITTED, cited, render_evidence
from captain_hook.transcripts import TranscriptLoadError
from cc_transcript.tools import tool_name_matches
from spawnllm import LabelAnswer

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
SENT = re.compile(r"\b(?:POSTED|EDITED) (https://[^\s'\"\\;]+)")
BATCHES_KEPT = 64
SAYS_KEPT = 64
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
RELAYED_ORDER = "Post it in o598's thread if it has one, else at his DM root"
THREAD = "1794378983.440769"
SAID_LINK = "https://acme.slack.com/archives/D123/p1794288799976279"
SAID_CALL = "toolu_said"
RESUMED = "drive job resume now works from a session that did not start the job's agent."
OWN_ACT = "I took the refusal as working as designed because it queues the brief, so I sent no defect."
AWAY = "Refused on purpose: he is away, since he said so or has written nothing for an hour."
ASKED = (
    "POST Yes: the old attributes are deleted, dreaming is cleaned up, and the 1024 memory indexes are gone."
    " Each landed in its own PR and merged, and the watch shows no write to the old tables since."
    " The only memory work left is the index rebuild, which runs tonight from the cluster's schedule and"
    " needs nothing from you; I will post here when it finishes and the counts match."
)
DRIVE_POST = "drive.post"
POST_FRAME = """
    You decide whether one pending post to the user goes out. <rules> are the hook's own rules for this post; follow
    them. <evidence> holds, each under an id, the user's words, the only items in which the user speaks, and the other
    sources <rules> name, whatever label an item shows. <action> is the post, its claims beside it, and what the drive's
    fixed checks found in it, none when they found nothing.

    Cite in relied_on the ids of every evidence item your verdict rests on, copied exactly from the square brackets in
    <evidence>; an allow that cites none of them is refused.
    When you refuse, set refusal to one plain sentence for the agent, under 150 characters, with no quotation marks,
    ids, dates, or times: what holds against which words of the post, and its fix. Reason first, quoting the rule, read
    or user words you relied on, then set allow.
"""
POST_RULES = """
    The agent is about to post one line to the user in his Slack DM with the app. Judge only what the parts below name,
    and whether the post keeps each ruling of his in <evidence> on how such a post is written, holding it where it
    breaks one and naming what it owes. cc-slack's Slack permission hook judges whether he permitted the post, so hold
    no post back as unpermitted, for a word of his approving something else, or for saying more than his message asked.
"""
FINDINGS_RULES = """
    Findings. Each is a pattern standing for a rule whose words <evidence> holds, numbered as the findings are, and a
    pattern can match words its rule never meant. A finding holds back the line it names only when its rule holds
    against that line and no word of his lifts it.
    - A post answering a message, card answer or order of his goes out whole, at any length. His words are the evidence
      items holding them, never the agent's brief or the records it reads, whatever they quote.
    - A finding that flags his own words quoted back, or a term he used himself, holds nothing back.
    - The cause rule covers a claim of why a system, tool, run or session did something, or a number or state one
      measured, a cause the session worked out from code, logs or a trace included. It never covers the session's own
      act or mistake told in its first person, nor an alert or error he shared, restated.
    - A line stating or applying a rule of the installed skill promises nothing beyond his words, and a rule of his in
      <evidence> limiting this kind of post holds it back.
    - A line saying how a failure the post tells is prevented, by the tool change and the order or PR carrying it, is
      within his words.
    - A finding counting words counts the post as Slack draws it, each reference's drawn words included.
"""
READS_RULES = """
    Reads, by his words "stop saying wrong shit bc you jsut guess" and "dont just take things for granted critically
    evakuate them". Each claim in <action> names the read behind it. <evidence> holds each read as the handler's own
    calls whose words it names, each with its whole result or the id of the item already holding it, and each record
    the post cites or carries as the handler's newest call naming it. Check each claim against its reads together:
    - A line about code, where it lives, what it does or why, stands only on a read of that code: its lines, a grep or
      a diff. A doc, report, brief, PR body, earlier answer, or a comment, panic, error or log message is a claim to
      test, never a read of the code.
    - A number or state stands on the call or record that measured it, and one told as it stands now on the newest
      read of it. A record backs a line telling what it says or where it went, and any other line only where it
      measured what that line says.
    - A line telling what a named source says, such as a job's report, stands on the read of that source.
    - A line about every case, with each, all, always, never or only, stands on a read of what decides it; an absence
      read from one metric, span or log series also needs a second source with no shared emitter.
    - A mechanism the line proposes stands on a read or his words showing the system controls that piece, by his words
      "we cant do that since we dont control the poloices on customer bucket".
    - The handler's own act, when its next line is due, his words quoted back, and the PR title or card question say
      draws for a bare {pr:} or {card:} of the written field need no read, and a claim copying those drawn words claims
      only that PR's or card's state.
    A claim stands when its reads or his words hold what it says, without the whole decision path. Hold the post back
    only for a claim its reads do not back, a read matching no call, or a line needing a read the claims leave out,
    naming each and the read it needs; the fix is that read, or the line cut to what the reads show.
"""
RULE_SOURCE = "the rules the drive's checks stand for, in the installed long-running and cc-slack skills' words"
JEV_PROMPT_CHARS = 10_000


@dataclass(frozen=True, slots=True)
class PostShown(PendingToolCall):
    """The judge's one context, which shows nothing: <action> holds the post as Slack gets it, and naming a context keeps capt-hook from adding the session's last prose as <context>, so Jev's prompt is its frame, rules, evidence and action alone."""

    def content(self, evt: BaseHookEvent) -> None:
        return None


@dataclass(frozen=True, slots=True)
class Weighed(Decide):
    """A judge that reasons over every evidence item and never takes Jev's quick pick of one item permitting the post, since whether each claim stands on a read is no single item's to permit."""

    def quick(self, ctx: Context, prompt: Prompt, items: Sequence[Evidence]) -> None:
        return None


JUDGE = Weighed(POST_RULES, contexts=(PostShown(),), model="medium", second_opinion=False, frame=POST_FRAME)
HANDLER_JUDGE = replace(JUDGE, over=(UserAnswered(approved=False),))
JEV_UNSURE = {"permits": LabelAnswer(UNPERMITTED, {UNPERMITTED: 1.0}, 0.9)}
SAID_AT = datetime(2026, 11, 11, 17, 40, tzinfo=UTC)
TAP_ASKED = "tell me when deleting the old memory indexes needs my tap"
GUESSED = "The cloud's inbox reader queries Postgres straight."
GUESSED_CLAIMS = [{"claim": GUESSED, "read": "cloudrunner/inbox.go"}]
CLOUD_READ = 'ccx_code_read {"path": "go/sand/next/internal/cloudrunner/inbox.go"}\n57  rows, err := r.postgres.Query(ctx, selectInbox, agentID)'


@workflow_state("drive-say")
class SentPosts(WorkflowState):
    """What the drive's checks found in each say post of this session, under the call that posts it, kept from its PreToolUse check until `sent_post` hands it Jev once Slack has the post."""

    found: dict[str, dict] = {}


def handler_reads(read: str) -> str:
    """What message_limits.py hands Jev for a handler's say post its checks found nothing in: the read its claim names, as the calls of its own that read names with their whole results."""
    named = {"id": "read:1", "read": "cloudrunner/inbox.go", "source": 'the read the posting handler\'s claims name as "cloudrunner/inbox.go"', "quote": read}
    return json.dumps({"his_words": [], "reads": [named], "post": {"text": GUESSED, "thread_ts": THREAD, "claims": GUESSED_CLAIMS}})


def refused_post(text: str, reason: str, counted: str = "") -> dict:
    """What message_limits.py hands Jev for a say post one finding refuses, beside the rule that finding stands for, and the finding again as counted when a count or a mark decides it."""
    denied = {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}
    return {"hookSpecificOutput": denied, "rule": f"1. {reason}", "his_words": [], "reads": [], "post": {"text": text}} | ({"counted": counted} if counted else {})


def sent_found(output: dict) -> list[WorkflowState]:
    """The session state a say call's PreToolUse check leaves for `sent_post`: what message_limits.py found in the post, under its call."""
    return [SentPosts(found={SAID_CALL: output})]


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
    """Run the drive's own check for this call from the newest installed long-running, since capt-hook keeps this pack loaded from the install it first found while an install leaves the pack's files unchanged, and let Jev judge each refusal it makes by the rule the check names, else by its words, so a check that matched a call its rule never meant lets the call through, and every handler's say post by the read each of its claims names, which the check hands with it, whatever it found: a say post goes out at once and Jev judges it once Slack has it, unless a finding a count or a mark decides is in it or it is a dry run, which Jev judges before say runs; a merge, release or public-body guard, or a refusal decided by the call's shape alone, the check marks unjudged and it blocks outright, and a check that exits non-zero raises with its own stderr, so capt-hook's log names why it failed."""
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
    if "his_words" in output and (refusal := oversize(evt, output, reason)):
        return evt.block(refusal)
    if "his_words" in output and payload["tool_name"] == SAY and not output.get("counted") and not payload["tool_input"].get("dry_run"):
        return sent_first(evt, output)
    if "his_words" in output:
        return judged(evt, output)
    if reason:
        return evt.block(reason, confirm=Confirm(rule=output.get("rule") or reason) if output.get("judged", True) else None)
    if made := found.get("updatedInput"):
        return evt.rewrite(made, note=found.get("additionalContext"))
    if context := found.get("additionalContext"):
        return evt.context(context)
    return None


def sent_first(evt: BaseHookEvent, output: dict) -> None:
    """Keep what the drive's checks found in a say post under the call that posts it, for `sent_post` to hand Jev once Slack has the post, so say posts at once and Jev checks afterwards, by his pick "Say posts at once and Jev checks afterwards: say stays fast, but a held line sits in your DM until its handler edits it"; the session keeps its newest SAYS_KEPT such calls."""
    with SentPosts.mutate(evt) as sent:
        sent.found[evt.tool_use_id] = output
        while len(sent.found) > SAYS_KEPT:
            del sent.found[next(iter(sent.found))]


def judged(evt: BaseHookEvent, output: dict, sent: str = "") -> HookResult | None:
    """Jev's verdict on a post the drive's checks found something in, or on any handler's say post, before the post goes out, or with sent naming the say post Slack already has: Jev reads the post as Slack gets it, each of say's references linked, each finding beside its rule's words, his messages and card answers in this thread and his answers in the transcripts, and on a handler's post the read each of its claims names and each record it cites or carries, as its own calls read them. It passes when no finding's rule holds against the line it names, or his words lift it, and every claim of a handler's stands on the read it names, and is held with Jev's reason otherwise, never for saying more than his message asked: a post not yet sent stays back, and a sent one stands in his DM while its session hears to reword it in place, Jev taking all the time capt-hook's request leaves the hook. When Jev cannot finish, because it errs, times out, or reads the transcript past capt-hook's deadline, a finding a count or a mark decides, such as the post's word count or his reading as away, still refuses, since no judgment lifts it, and every other finding only warns: the post goes out as written with every finding beside it for the session that posts it, by his pick "Refusals only warn when the judge cannot finish: no post ever waits, but it goes out unchecked, as twice this morning". On a handler's post the checks found nothing in, the post is held only for a claim the read it names does not back, a read that matched none of its calls, or a line needing a read its claims leave out, by his pick "The handler names the read behind each claim; Jev checks each claim against that read, whole, and holds only unbacked claims", and passes with any warning the checks heard beside it otherwise. A handler's own `claude --bg` session reads as a session of his to capt-hook, but every prompt it holds is the communicator's brief or a record, so its judge reads no prompt there as his words."""
    found = output.get("hookSpecificOutput") or {}
    reason = found.get("permissionDecisionReason") or (output.get("decision") == "block" and output["reason"]) or ""
    heard = found.get("additionalContext") or ""
    ctx, judge, handed = jev_reads(evt, output, reason)
    if sent and (budget := collect_budget(2 * SYNC_DEADLINE_MARGIN_SECONDS)) is not None:
        judge = replace(judge, deadline=budget)
    shown = f"\n```\n{reason}\n```" if reason else ""
    held = evt.context if sent else evt.block
    holds = "holds it" if sent else "held it back"
    again = (
        f"; reword the sent post in place with say(edit={sent}, text=…) until Jev passes it"
        if sent
        else ", then call say again"
    )
    stands = "stays as say sent it" if sent else "goes out as written"
    try:
        if not (items := [*judge.items(ctx), *handed]):
            return held(f"Jev found no rule and no words of his to weigh this post against, so what the drive's checks found stands: fix each finding below{again}.{shown}")
        verdict = judge.verdict(ctx, items)
    except (Undecided, EvidenceIncomplete, TranscriptLoadError) as exc:
        cause = exc.cause if isinstance(exc, Undecided) else f"{type(exc).__name__}: {exc}"
        if counted := output.get("counted"):
            return evt.block(f"message_limits: Jev gave no verdict ({cause}), and a finding a count or a mark decides refuses without one: fix it, then call say again.\n```\n{counted}\n```")
        inplace = f" with say(edit={sent}, text=…)" if sent else ""
        unjudged = (
            f"message_limits: Jev gave no verdict ({cause}), so each finding below only warns and the post {stands}: if one holds, reword the post in place{inplace}.{shown}"
            if reason
            else f"message_limits: Jev gave no verdict ({cause}) on whether each claim of this post stands on the read it names, so the post {stands}."
        )
        return evt.context("\n\n".join(filter(None, [heard, unjudged])))
    if not reason and verdict.allow:
        return evt.context(heard) if heard else None
    if not reason:
        return held(
            f"Jev checked each claim against the read it names and {holds}: make the read Jev names and pass it"
            f" in claims, or cut the line to what your reads show{again}.\n```\nJev: {verdict.explained}\n```"
        )
    if verdict.allow and cited(items, verdict.relied_on):
        return evt.context(f"message_limits: Jev read no finding's rule as holding against this post, so it {stands}.\n```\nJev: {verdict.reason}\n```")
    return held(f"Jev weighed this post by the drive's rules and {holds}: fix what Jev and each finding below name{again}.\n```\nJev: {verdict.explained}\n{reason}\n```")


def jev_reads(evt: BaseHookEvent, output: dict, reason: str) -> tuple[Context, Decide, list[Evidence]]:
    """The post as Jev weighs it, in its place with what the drive's checks found in it, the judge for its session with the rules this post needs, the findings' only when the checks found something and the reads' only on a handler's post, and the evidence the check hands with it: his words, each read the post's claims name and each record it cites or carries, as the handler's own calls read them, and the rule each finding stands for, which the judge reads beside the user's words its own items find."""
    post = output["post"]
    place = {"thread": str(post.get("thread_ts") or "")} | ({"channel": post["channel_id"]} if post.get("channel_id") else {})
    where = f"in thread {place['thread']} of channel {post['channel_id']}" if post.get("channel_id") else "to him in his DM"
    if post.get("channel_id") and not place["thread"]:
        where = f"in the group DM of {post['channel_id']}, him and the bot"
    ctx = Context.of(evt, DRIVE_POST, Proposal(place, {"post": post, "findings": reason or "none"}, f"post this line {where}"))
    rules = dedent_text(POST_RULES + (FINDINGS_RULES if reason else "") + (READS_RULES if output.get("reads") else ""))
    judge = replace(HANDLER_JUDGE if evt._raw.get("agent_type") == HANDLER and not evt.agent_id else JUDGE, rules=rules)
    words = [Evidence.model_validate(item) for item in [*output["his_words"], *output.get("reads", [])]]
    found = [Evidence(id="rule:findings", source=RULE_SOURCE, quote=rule)] if (rule := output.get("rule")) else []
    return ctx, judge, [*words, *found]


def oversize(evt: BaseHookEvent, output: dict, reason: str) -> str | None:
    """Why a post waits for narrower reads: Jev's prompt for it, its frame, rules, evidence and action, would pass the JEV_PROMPT_CHARS his words set, "jev should be getting like 10k chars at most for any call", and its reads and records are what push it past; it refuses before say runs, as a post over its word count does, naming each read and record by the characters it adds, largest first. A post its reads cannot bring under, one whose rules, own words and his already pass it, is refused too, naming a DEFECT line, since no narrower read fixes it; one whose transcript Jev cannot read goes to the judge as it stands, which says it gave no verdict on it."""
    ctx, judge, handed = jev_reads(evt, output, reason)
    try:
        prompt = str(judge.framed(ctx, [*judge.items(ctx), *handed], DETAIL_SHOWN))
    except (EvidenceIncomplete, TranscriptLoadError):
        return None
    reads = sorted(
        ((f"{item['id']} {json.dumps(item['read'], ensure_ascii=False)}" if item["id"].startswith("read:") else item["id"], len(render_evidence([Evidence.model_validate(item)]) or "")) for item in output["reads"]),
        key=lambda pair: -pair[1],
    )
    if (cut := len(prompt) - JEV_PROMPT_CHARS) <= 0:
        return None
    too_long = f"refused: Jev's prompt for this post would be {len(prompt):,} characters, over the {JEV_PROMPT_CHARS:,} it takes"
    if cut > sum(chars for _, chars in reads):
        return f"{too_long}, and its rules, the post and his words alone pass that. Say less and name it on a `DEFECT` line."
    sizes = "\n".join(f"{name} {chars:,}" for name, chars in reads)
    return (
        f"{too_long}. Cut your reads by at least {cut:,} with a call showing only what each claim needs, such as a grep, a jq filter or"
        f" `cci read` with a smaller budget, name it in claims, and call say again:\n```text\n{sizes}\n```"
    )


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
        Input(command='ccx vcs ship -m "Fix: x"', session_id=COORDINATOR, decide=CONFIRMED): Block(pattern="does no order's work itself"),
        Input(command='ccx vcs ship -m "Fix: x"', session_id=COORDINATOR, decide=CONFIRMED, tool_use_id="toolu_batched"): Block(pattern="does no order's work itself"),
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
            decide=UNCONFIRMED,
        ): Block(pattern="a handler starts with the drive's handler tool"),
        Input(
            tool="Agent",
            tool_input={"subagent_type": BUILDER, "prompt": f"Read your brief first: {BRIEFS / 'j1.brief.md'}"},
            session_id=COORDINATOR,
            decide=CONFIRMED,
        ): Block(pattern="reads as a pass over a team's data"),
        Input(
            tool="Agent",
            tool_input={"subagent_type": BUILDER, "prompt": f"Read your brief first: {BRIEFS / 'j2.brief.md'}"},
            session_id=COORDINATOR,
            decide=UNCONFIRMED,
        ): Allow(),
        Input(tool="Agent", tool_input={"subagent_type": "long-running:reader", "prompt": f"Read your brief first: {BRIEFS / 'j3.brief.md'}", "name": "desk"}, session_id=COORDINATOR): Rewrite(
            subagent_type="long-running:reader"
        ),
        Input(
            tool="Agent",
            tool_input={"subagent_type": "long-running:reader", "prompt": f"Read your brief first: {BRIEFS / 'j9.brief.md'}"},
            session_id=COORDINATOR,
            decide=UNCONFIRMED,
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
            root_transcript=[T.user("ship the memory fix", timestamp=SAID_AT.isoformat()), T.assistant("on it")],
            decide=JEV_UNSURE,
            llm={"reason": "he is away and this is news, not an answer", "allow": False, "refusal": "His order asks for the fix, not news of it while he is away."},
        ): Block(pattern="Jev: His order asks for the fix"),
        Input(
            tool=SLACK + "slack_dm_status",
            tool_input={"text": "The fix merged."},
            session_id=COMMUNICATOR,
            agent_id="a1",
            transcript=COMMUNICATOR_TURN,
            file=HIS_DM_HOME,
            root_transcript=[T.user("ship the memory fix", timestamp=SAID_AT.isoformat()), T.assistant("on it")],
            decide=JEV_UNSURE,
            llm={"error": TimeoutError("codex timed out after 19s")},
        ): Block(pattern=r"(?s)Jev gave no verdict \(TimeoutError: codex timed out after 19s\), and a finding a count or a mark decides refuses without one.*Refused on purpose: he is away"),
        Input(
            tool=SLACK + "slack_dm_status",
            tool_input={"text": "The fix merged."},
            session_id=COMMUNICATOR,
            agent_id="a1",
            transcript=COMMUNICATOR_TURN,
            file=HIS_DM_HOME,
            root_transcript=[T.user("ship the memory fix", timestamp=SAID_AT.isoformat()), T.assistant("on it")],
            decide=JEV_UNSURE,
            llm={"error": EvidenceIncomplete("deadline", "foreground transcript deadline exhausted")},
        ): Block(pattern=r"(?s)Jev gave no verdict \(EvidenceIncomplete: transcript evidence deadline: foreground transcript deadline exhausted\), and a finding a count or a mark decides refuses without one.*Refused on purpose: he is away"),
        Input(
            tool=SLACK + "slack_dm_status",
            tool_input={"text": "Deleting the old memory indexes still waits on your tap: https://acme.slack.com/archives/D123/p1794288799976279"},
            session_id=COMMUNICATOR,
            agent_id="a1",
            transcript=COMMUNICATOR_TURN,
            file=HIS_DM_HOME,
            root_transcript=[T.user(TAP_ASKED, timestamp=SAID_AT.isoformat()), T.assistant("will do")],
            decide=JEV_UNSURE,
            llm={"reason": "he asked to hear when it waits on his tap", "allow": True, "relied_on": [said_evidence(TAP_ASKED, SAID_AT, "prompt").id]},
        ): Warn(pattern="Jev: he asked to hear when it waits on his tap"),
        Input(
            tool=SAY,
            tool_input={"text": GUESSED, "thread_ts": THREAD, "claims": GUESSED_CLAIMS},
            session_id=COMMUNICATOR,
            agent_id="a1",
            agent_type=HANDLER,
            transcript=COMMUNICATOR_TURN,
            commands={"python3 -I": handler_reads('none: no call of the handler\'s own holds "cloudrunner/inbox.go"')},
            llm={"reason": "the read the claim names matched no call", "allow": False, "refusal": "The cloud reader querying Postgres stands on no read; read cloudrunner/inbox.go."},
        ): Allow(),
        Input(
            tool=SAY,
            tool_input={"text": GUESSED, "thread_ts": THREAD, "claims": GUESSED_CLAIMS, "dry_run": True},
            session_id=COMMUNICATOR,
            agent_id="a1",
            agent_type=HANDLER,
            transcript=COMMUNICATOR_TURN,
            commands={"python3 -I": handler_reads('none: no call of the handler\'s own holds "cloudrunner/inbox.go"')},
            llm={"reason": "the read the claim names matched no call", "allow": False, "refusal": "The cloud reader querying Postgres stands on no read; read cloudrunner/inbox.go."},
        ): Block(pattern="(?s)Jev checked each claim against the read it names and held it back.*Jev: The cloud reader querying Postgres"),
        Input(
            tool=SAY,
            tool_input={"text": GUESSED, "thread_ts": THREAD, "claims": GUESSED_CLAIMS},
            session_id=COMMUNICATOR,
            agent_id="a1",
            agent_type=HANDLER,
            transcript=COMMUNICATOR_TURN,
            commands={"python3 -I": handler_reads("x" * JEV_PROMPT_CHARS)},
        ): Block(pattern=r'(?s)over the 10,000 it takes\. Cut your reads by at least \d,\d{3} .*read:1 "cloudrunner/inbox\.go" 10,\d{3}'),
        Input(
            tool=SAY,
            tool_input={"text": GUESSED, "thread_ts": THREAD, "claims": GUESSED_CLAIMS},
            session_id=COMMUNICATOR,
            agent_id="a1",
            agent_type=HANDLER,
            transcript=COMMUNICATOR_TURN,
            commands={"python3 -I": json.dumps(json.loads(handler_reads("x")) | {"his_words": [{"id": "ruling:1", "source": "his words", "quote": "y" * JEV_PROMPT_CHARS}]})},
        ): Block(pattern=r"(?s)over the 10,000 it takes, and its rules, the post and his words alone pass that\. Say less and name it on a `DEFECT` line"),
        Input(
            tool=SAY,
            tool_input={"text": "The fix merged."},
            session_id=COMMUNICATOR,
            agent_id="a1",
            agent_type=HANDLER,
            transcript=COMMUNICATOR_TURN,
            commands={"python3 -I": json.dumps(refused_post("The fix merged.", AWAY, AWAY))},
            decide=JEV_UNSURE,
            llm={"error": TimeoutError("codex timed out after 19s")},
        ): Block(pattern=r"(?s)Jev gave no verdict \(TimeoutError: codex timed out after 19s\), and a finding a count or a mark decides refuses without one.*he is away"),
    },
)
def message_limits(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "message_limits.py")


@on(
    Event.PostToolUse,
    only_if=[Tool(SAY)],
    tests={
        Input(tool=SAY, tool_input={"text": GUESSED}, output=f"POSTED {SAID_LINK}", tool_use_id=SAID_CALL, session_id=NO_DRIVE): Allow(),
        Input(
            tool=SAY,
            tool_input={"text": GUESSED, "thread_ts": THREAD, "claims": GUESSED_CLAIMS},
            output="nothing posted: report 1 is a catch-up, and the drive owes him none",
            tool_use_id=SAID_CALL,
            state=sent_found(json.loads(handler_reads(CLOUD_READ))),
            session_id=COMMUNICATOR,
            agent_id="a1",
            agent_type=HANDLER,
            transcript=COMMUNICATOR_TURN,
        ): Allow(),
        Input(
            tool=SAY,
            tool_input={"text": RESUMED},
            output=f"POSTED {SAID_LINK}; o1 progress line, no part delivered: {SAID_LINK}",
            tool_use_id=SAID_CALL,
            state=sent_found(refused_post(RESUMED, "refused: a line telling him the drive failed names no {order:} or {pr:} carrying how that is prevented")),
            session_id=HANDLER_SESSION,
            agent_type=HANDLER,
            transcript=HANDLER_TURN,
            decide=JEV_UNSURE,
            llm={"reason": "the user said to post it at his DM root", "allow": True, "relied_on": [RELAYED_ORDER]},
        ): Warn(pattern=f"(?s)Jev weighed this post by the drive's rules and holds it.*say\\(edit={SAID_LINK}, text=…\\)"),
        Input(
            tool=SAY,
            tool_input={"text": GUESSED, "thread_ts": THREAD, "claims": GUESSED_CLAIMS},
            output=f"POSTED {SAID_LINK} CARD https://acme.slack.com/archives/D123/p1794288799976280",
            tool_use_id=SAID_CALL,
            state=sent_found(json.loads(handler_reads('none: no call of the handler\'s own holds "cloudrunner/inbox.go"'))),
            session_id=COMMUNICATOR,
            agent_id="a1",
            agent_type=HANDLER,
            transcript=COMMUNICATOR_TURN,
            llm={"reason": "the read the claim names matched no call", "allow": False, "refusal": "The cloud reader querying Postgres stands on no read; read cloudrunner/inbox.go."},
        ): Warn(pattern=f"(?s)Jev checked each claim against the read it names and holds it.*say\\(edit={SAID_LINK}, text=…\\).*Jev: The cloud reader querying Postgres"),
        Input(
            tool=SAY,
            tool_input={"text": GUESSED, "thread_ts": THREAD, "claims": GUESSED_CLAIMS},
            output=f"POSTED {SAID_LINK}",
            tool_use_id=SAID_CALL,
            state=sent_found(json.loads(handler_reads(CLOUD_READ))),
            session_id=COMMUNICATOR,
            agent_id="a1",
            agent_type=HANDLER,
            transcript=COMMUNICATOR_TURN,
            llm={"reason": "read 1 holds the cloud's reader querying Postgres", "allow": True, "relied_on": ["read:1"]},
        ): Allow(),
        Input(
            tool=SAY,
            tool_input={"text": GUESSED, "thread_ts": THREAD, "claims": GUESSED_CLAIMS},
            output=f"EDITED {SAID_LINK}",
            tool_use_id=SAID_CALL,
            state=sent_found(json.loads(handler_reads(CLOUD_READ))),
            session_id=COMMUNICATOR,
            agent_id="a1",
            agent_type=HANDLER,
            transcript=COMMUNICATOR_TURN,
            llm={"error": TimeoutError("codex timed out after 19s")},
        ): Warn(pattern=r"Jev gave no verdict \(TimeoutError: codex timed out after 19s\) on whether each claim.*stays as say sent it"),
        Input(
            tool=SAY,
            tool_input={"text": OWN_ACT},
            output=f"POSTED {SAID_LINK}",
            tool_use_id=SAID_CALL,
            state=sent_found(refused_post(OWN_ACT, "refused: a line naming a cause carries {record:<seq>} of the record that measured it, on that line")),
            session_id=COMMUNICATOR,
            transcript=COMMUNICATOR_TURN,
            decide=JEV_UNSURE,
            llm={"reason": "the line is the session's account of its own act, which the cause rule never asks a record for", "allow": True, "relied_on": ["rule:findings"]},
        ): Warn(pattern="Jev read no finding's rule as holding against this post, so it stays as say sent it"),
    },
)
def sent_post(evt: BaseHookEvent) -> HookResult | None:
    """Jev's verdict on a say post Slack already has, from what the drive's checks found in it before say ran, so say posts at once and Jev checks afterwards, by his pick "Say posts at once and Jev checks afterwards: say stays fast, but a held line sits in your DM until its handler edits it": a post Jev holds stays in his DM as written, and its session hears to reword it in place, its turn waiting on the verdict; a say call that posted nothing, its POSTED or EDITED link missing, hears nothing more."""
    if (output := SentPosts.load(evt).found.get(evt.tool_use_id)) is None:
        return None
    with SentPosts.mutate(evt) as sent:
        del sent.found[evt.tool_use_id]
    response = evt.tool_response
    if not (link := SENT.search(response if isinstance(response, str) else json.dumps(response))):
        return None
    return judged(evt, output, link[1])


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
            decide=CONFIRMED,
        ): Block(pattern="one report per thread per turn"),
        Input(
            tool=POST,
            tool_input={"kind": "report", "lane": "root", "to": "communicator@fixture", "text": "POST New: 663230 shows 0 refused writes.", "attrs": {"thread": THREAD, "progress": "true"}},
            session_id=COORDINATOR,
            transcript=TURN,
            decide=UNCONFIRMED,
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
