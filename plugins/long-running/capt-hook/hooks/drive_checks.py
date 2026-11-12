from __future__ import annotations

import json
import runpy
import subprocess
from dataclasses import dataclass
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
    HookResult,
    Input,
    PendingToolCall,
    Rewrite,
    T,
    Tool,
    Warn,
    on,
)
from captain_hook.tickets import Context, Decide, Evidence, Proposal, Undecided
from captain_hook.tickets.context import said_evidence
from captain_hook.tickets.decide import UNPERMITTED, cited
from spawnllm import LabelAnswer

newest_install = runpy.run_path(str(Path(__file__).resolve().parents[2] / "hooks" / "installed.py"))["root"]
SLACK = "mcp__plugin_cc-slack_cc-slack__"
POSTS = (*(SLACK + tool for tool in ("slack_dm_status", "slack_reply", "slack_send", "slack_edit")), "mcp__plugin_long-running_drive__say")
NO_DRIVE = "0a0a0a0a-no-drive-session"
COORDINATOR = "coordinator-fixture"
COMMUNICATOR = "communicator-fixture"
HANDLER = "long-running:handler"
BUILDER = "long-running:builder"
POST = "mcp__plugin_cc-inbox_cci__post"
TURN = Path(__file__).resolve().parent / "tests" / "fixtures" / "turn.jsonl"
REMOVES_CAUSE = TURN.with_name("ask-removes-cause.md")
KEEPS_CAUSE = TURN.with_name("ask-keeps-cause.md")
MERGED_FIX = TURN.with_name("ask-merged-fix.md")
GATED_APPROVAL = TURN.with_name("ask-gated-approval.md")
COMMUNICATOR_TURN = TURN.with_name("communicator.jsonl")
THREAD = "1794378983.440769"
ASKED = (
    "POST Yes: the old attributes are deleted, dreaming is cleaned up, and the 1024 memory indexes are gone."
    " Each landed in its own PR, merged by 3:40 AM, and the watch shows no write to the old tables since."
    " The only memory work left is the index rebuild, which runs tonight from the cluster's schedule and"
    " needs nothing from you; I will post here when it finishes and the counts match."
)
DRIVE_POST = "drive.post"
JUDGED_TURNS = 12
POST_RULES = """
    The agent is about to post one line to the user in his Slack DM with the app; <action> holds the post and what
    the drive's fixed checks found in it. Each finding is a pattern standing for a rule of his, such as a length
    limit, plain words, a link where a thing is named, or a card where something waits on him, and a pattern can
    match words its rule never meant. Allow the post when his words in <evidence> show the findings do not hold
    against it, and cite them:
    - A post that answers a message, card answer or order of his goes out whole, at any length. The words it passes
      as asked are his when an evidence item holds them, however he gave them: typed in the thread, picked or typed
      on a card, or quoted in the brief the agent was started with.
    - A finding that flags his own words quoted back, or a term he used himself, holds nothing back.
    - A rule of his in <evidence> that limits this kind of post holds it back, wherever else he permits it.
    Refuse when a finding names a rule of his that the post breaks and no evidence item lifts it; the refusal names
    that finding and its fix.
"""
JUDGE = Decide(POST_RULES, second_opinion=False, transcript=JUDGED_TURNS, root_transcript=JUDGED_TURNS, tool_results=True)
DRIVE_ASK = "drive.ask"
ASK_RULES = """
    The agent is about to keep a question card for the user; <action> holds the case its block makes: the question,
    its summary, the recommended option, the other options, the root cause its findings name, the option they say
    removes it, and any words of his it quotes as settled. <evidence> holds his ruling on root causes, the rule it
    sets in its detail. Allow the card, citing that ruling, when the card keeps the rule. Refuse when it breaks it;
    the refusal names what the recommended option keeps or skips that the rule forbids, and the fix.
"""


@dataclass(frozen=True, slots=True)
class UnshownCall(PendingToolCall):
    """The pending call kept out of a judge's prompt, so its verdict rests on what the hook hands it and not on the form of the call that carries it."""

    def content(self, evt: BaseHookEvent) -> None:
        return None


ASK_JUDGE = Decide(ASK_RULES, contexts=(UnshownCall(),), second_opinion=False)
JEV_UNSURE = {"permits": LabelAnswer(UNPERMITTED, {UNPERMITTED: 1.0}, 0.9)}
SAID_AT = datetime(2026, 11, 11, 17, 40, tzinfo=UTC)
TAP_ASKED = "tell me when deleting the old memory indexes needs my tap"


def checked(evt: BaseHookEvent, script: str) -> HookResult | None:
    """Run the drive's own check for this call from the newest installed long-running, since capt-hook keeps this pack loaded from the install it first found while an install leaves the pack's files unchanged, and let Jev judge each refusal it makes by the rule the check names, else by its words, so a check that matched a call its rule never meant lets the call through; a merge, release or public-body guard, or a refusal decided by the call's shape alone, the check marks unjudged and it blocks outright, and a check that exits non-zero raises with its own stderr, so capt-hook's log names why it failed."""
    payload = evt._raw
    ran = subprocess.run(
        ["python3", "-I", str(newest_install() / "hooks" / script)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=payload.get("cwd") or None,
    )
    if ran.returncode:
        raise RuntimeError(f"{script} exited {ran.returncode}:\n{ran.stderr}")
    if not (said := ran.stdout.strip()):
        return None
    output = json.loads(said)
    if case := output.get("ask"):
        return asked(evt, case, [Evidence.model_validate(word) for word in output["his_words"]])
    found = output.get("hookSpecificOutput") or {}
    if reason := found.get("permissionDecisionReason") or (output.get("decision") == "block" and output["reason"]):
        if (words := output.get("his_words")) is not None:
            return judged(evt, reason, output.get("rule") or "", [Evidence.model_validate(word) for word in words], output["post"])
        return evt.block(reason, confirm=Confirm(rule=output.get("rule") or reason) if output.get("judged", True) else None)
    if made := found.get("updatedInput"):
        return evt.rewrite(made)
    if context := found.get("additionalContext"):
        return evt.context(context)
    return None


def judged(evt: BaseHookEvent, reason: str, rule: str, words: list[Evidence], post: dict) -> HookResult | None:
    """Jev's verdict on a post the drive's checks found something in, judged as the Slack permission hook judges a write without a ticket: Jev reads the post as Slack gets it, each of say's references linked, the words the checks found something in, and it goes out when his words, in this thread, on a card, in the brief or in the transcripts, show the findings do not hold against it, and stays back with Jev's reason when a rule of his does; a judge that gives no verdict lets it out with the findings noted, as a check that could not finish does."""
    place = {"thread": str(post.get("thread_ts") or "")} | ({"channel": post["channel_id"]} if post.get("channel_id") else {})
    where = f"in thread {place['thread']} of channel {post['channel_id']}" if post.get("channel_id") else "to him in his DM"
    if post.get("channel_id") and not place["thread"]:
        where = f"in the group DM of {post['channel_id']}, him and the bot"
    ctx = Context.of(evt, DRIVE_POST, Proposal(place, {"post": post, "findings": reason, "rule": rule}, f"post this line {where}"))
    if not (items := [*JUDGE.items(ctx), *words]):
        return evt.block(f"Jev found no words of his to weigh this post against, so what the drive's checks found stands: fix each finding below, then call say again.\n```\n{reason}\n```")
    try:
        verdict = JUDGE.verdict(ctx, items)
    except Undecided as exc:
        return evt.context(f"message_limits: Jev gave no verdict ({exc.cause}), so the post goes out as written with what the checks found below.\n```\n{reason}\n```")
    if verdict.allow and cited(items, verdict.relied_on):
        return evt.context(f"message_limits: Jev read his words as permitting this post over what the checks found, so it goes out as written.\n```\nJev: {verdict.reason}\n```")
    return evt.block(f"Jev weighed this post against his words and upheld what the drive's checks found: fix what Jev and each finding below name, then call say again.\n```\nJev: {verdict.explained}\n{reason}\n```")


def asked(evt: BaseHookEvent, case: str, words: list[Evidence]) -> HookResult | None:
    """Jev's verdict on a question card `drive job ask` keeps, judged by his ruling on root causes from the case its block makes and nothing else, so every form handing the verb one block gets one verdict: the card is kept when Jev finds it keeps the ruling, refused with Jev's own finding when it does not, and kept with a note when Jev gives no verdict."""
    ctx = Context.of(evt, DRIVE_ASK, Proposal({}, {"case": case}, "keep this question card for him"))
    try:
        verdict = ASK_JUDGE.verdict(ctx, words)
    except Undecided as exc:
        return evt.context(f"landing_guard: Jev gave no verdict on this question card ({exc.cause}), so the verb keeps it as written.")
    if verdict.allow and cited(words, verdict.relied_on):
        return None
    found = [f"Jev: {verdict.explained}", *([verdict.reason] if verdict.refusal else [])]
    return evt.block(
        "Jev weighed this question card against his words on root causes and refused it: fix what Jev names, then run"
        " `drive job ask` again.\n```\n" + "\n".join([*found, case]) + "\n```"
    )


@on(
    Event.PreToolUse,
    only_if=[Tool("Read", "Write", "Edit", "Bash")],
    tests={Input(command="ls", session_id=NO_DRIVE): Allow()},
)
def no_file_tools(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "no_file_tools.py")


@on(
    Event.PreToolUse,
    only_if=[Tool(SLACK + "slack_thread", SLACK + "slack_history")],
    tests={Input(tool=SLACK + "slack_thread", tool_input={"channel": "D0", "ts": "1.0"}, session_id=NO_DRIVE): Allow()},
)
def no_slack_reads(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "no_slack_reads.py")


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash")],
    tests={
        Input(command="ls", session_id=NO_DRIVE): Allow(),
        Input(command='ccx vcs ship -m "Fix: x"', session_id=COORDINATOR, decide=CONFIRMED): Block(pattern="does no order's work itself"),
        Input(command="for j in j855 j966; do drive job replies $j --drive fixture | tail -5; done", session_id=COORDINATOR): Allow(),
        Input(command="orca orchestration check --help", session_id=COORDINATOR): Allow(),
        Input(
            command=f"drive job ask j1 - < {REMOVES_CAUSE}",
            session_id=COORDINATOR,
            decide=JEV_UNSURE,
            llm={"reason": "its recommended option writes the map at a fixed place, which removes the guess", "allow": True, "relied_on": ["ruling:0c59046"]},
        ): Allow(),
        Input(
            command=f"drive job ask j1 {KEEPS_CAUSE}",
            session_id=COORDINATOR,
            decide=JEV_UNSURE,
            llm={
                "reason": "the root cause is the guessed range and the recommended retry keeps guessing",
                "allow": False,
                "refusal": "The recommended retry keeps the guessed range the root cause names.",
            },
        ): Block(pattern="Jev: The recommended retry keeps the guessed range"),
        Input(
            command=f"drive job ask j1 {MERGED_FIX}",
            session_id=COORDINATOR,
            decide=JEV_UNSURE,
            llm={"reason": "its root cause line names the guessed range and links the merged PR that removed it", "allow": True, "relied_on": ["ruling:0c59046"]},
        ): Allow(),
        Input(
            command=f"drive job ask j1 {GATED_APPROVAL}",
            session_id=COORDINATOR,
            decide=JEV_UNSURE,
            llm={"reason": "no defect is behind it: it asks his approval of the exact command his rules hold for him", "allow": True, "relied_on": ["ruling:0c59046"]},
        ): Allow(),
    },
)
def landing_guard(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "landing_guard.py")


@on(
    Event.PreToolUse,
    only_if=[Tool("Bash", "Read", "Grep")],
    tests={
        Input(command="ls", session_id=NO_DRIVE): Allow(),
        Input(
            command="drive job report j955 --drive fixture; drive status j955 --drive fixture",
            session_id=COMMUNICATOR,
            agent_id="a1",
            agent_type=HANDLER,
        ): Allow(),
    },
)
def bash_guard(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "bash_guard.py")


@on(
    Event.PreToolUse,
    only_if=[Tool("Agent")],
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
            tool_input={"subagent_type": BUILDER, "prompt": "Serves: his order\nGoal: backfill each team's missing memory rows"},
            session_id=COORDINATOR,
            decide=CONFIRMED,
        ): Block(pattern="reads as a pass over a team's data"),
        Input(
            tool="Agent",
            tool_input={"subagent_type": BUILDER, "prompt": "Serves: his order\nGoal: backfill: ship the script, run later from the cluster's schedule"},
            session_id=COORDINATOR,
            decide=UNCONFIRMED,
        ): Allow(),
    },
)
def agent_brief(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "agent_brief.py")


@on(
    Event.PreToolUse,
    only_if=[Tool("Agent")],
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
            root_transcript=[T.user("ship the memory fix", timestamp=SAID_AT.isoformat()), T.assistant("on it")],
            decide=JEV_UNSURE,
            llm={"reason": "he is away and this is news, not an answer", "allow": False, "refusal": "His order asks for the fix, not news of it while he is away."},
        ): Block(pattern="Jev: His order asks for the fix"),
        Input(
            tool=SLACK + "slack_dm_status",
            tool_input={"text": "Deleting the old memory indexes still waits on your tap: https://acme.slack.com/archives/D123/p1794288799976279"},
            session_id=COMMUNICATOR,
            agent_id="a1",
            transcript=COMMUNICATOR_TURN,
            root_transcript=[T.user(TAP_ASKED, timestamp=SAID_AT.isoformat()), T.assistant("will do")],
            decide=JEV_UNSURE,
            llm={"reason": "he asked to hear when it waits on his tap", "allow": True, "relied_on": [said_evidence(TAP_ASKED, SAID_AT, "prompt").id]},
        ): Warn(pattern="Jev: he asked to hear when it waits on his tap"),
    },
)
def message_limits(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "message_limits.py")


@on(
    Event.PreToolUse,
    only_if=[Tool(POST)],
    tests={
        Input(tool=POST, tool_input={"kind": "report", "text": "x"}, session_id=NO_DRIVE): Allow(),
        Input(
            tool=POST,
            tool_input={"kind": "report", "to": "communicator@fixture", "text": "POST The fix is still building.", "attrs": {"thread": THREAD, "progress": "true"}},
            session_id=COORDINATOR,
            transcript=TURN,
            decide=CONFIRMED,
        ): Block(pattern="one report per thread per turn"),
        Input(
            tool=POST,
            tool_input={"kind": "report", "to": "communicator@fixture", "text": "POST New: 663230 shows 0 refused writes.", "attrs": {"thread": THREAD, "progress": "true"}},
            session_id=COORDINATOR,
            transcript=TURN,
            decide=UNCONFIRMED,
        ): Allow(),
        Input(tool=POST, tool_input={"kind": "report", "to": "communicator@fixture", "text": ASKED, "attrs": {"progress": "true"}}, session_id=COORDINATOR): Rewrite(
            body="the counts match"
        ),
    },
)
def record_lines(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "record_lines.py")


@on(Event.PreToolUse, tests={Input(tool="WebFetch", tool_input={"url": "https://example.com"}, session_id=NO_DRIVE): Allow()})
def communicator_calls(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "communicator_calls.py")


@on(Event.Stop, tests={Input(session_id=NO_DRIVE): Allow()})
def quiet_turn(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "quiet_turn.py")


@on(Event.SubagentStop, tests={Input(session_id=NO_DRIVE): Allow()})
def one_line_result(evt: BaseHookEvent) -> HookResult | None:
    return checked(evt, "one_line_result.py")
