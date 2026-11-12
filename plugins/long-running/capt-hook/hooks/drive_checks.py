from __future__ import annotations

import json
import runpy
import subprocess
from dataclasses import dataclass, replace
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
from captain_hook.tickets import Context, Decide, Evidence, Proposal, Undecided, UserAnswered
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
HANDLER_TURN = TURN.with_name("handler.jsonl")
HANDLER_SESSION = "handler-fixture"
RELAYED_ORDER = "Post it in o598's thread if it has one, else at his DM root"
THREAD = "1794378983.440769"
ASKED = (
    "POST Yes: the old attributes are deleted, dreaming is cleaned up, and the 1024 memory indexes are gone."
    " Each landed in its own PR, merged by 3:40 AM, and the watch shows no write to the old tables since."
    " The only memory work left is the index rebuild, which runs tonight from the cluster's schedule and"
    " needs nothing from you; I will post here when it finishes and the counts match."
)
DRIVE_POST = "drive.post"
JUDGED_TURNS = 12
POST_FRAME = """
    You decide whether the findings the drive's fixed checks raised on one pending post hold against it. <rules> are
    the hook's own rules for this kind of post; follow them. <evidence> lists the rule each finding stands for, in the
    installed skills' words, and the user's words, each under an id: only the user's items count as the user speaking.
    <action> is the post and what the checks found in it.

    Cite in relied_on the ids of every evidence item your verdict rests on, copied exactly from the square brackets in
    <evidence>; an allow that cites none of them is refused.
    When you refuse, set refusal to one plain sentence for the agent, under 150 characters, with no quotation marks,
    ids, dates, or times: which finding holds against which words of the post, and its fix. Reason first, quoting the
    rule or user words you relied on, then set allow.
"""
POST_RULES = """
    The agent is about to post one line to the user in his Slack DM with the app; <action> holds the post and what the
    drive's fixed checks found in it. Judge only those findings: each is a pattern standing for a rule whose own words
    <evidence> holds beside his, numbered as the findings are, and a pattern can match words its rule never meant.
    Allow the post, citing the rule or his words, when no finding's rule holds against the line it names; refuse,
    naming the finding and its fix, when one does and no word of his lifts it. What the post says beyond his message
    is never a reason to refuse.
    - A post that answers a message, card answer or order of his goes out whole, at any length. The words it passes
      as asked are his when an evidence item holds them, typed in the thread or picked or typed on a card. The brief
      the agent was started with and the records it reads are other agents' words, never his, whatever they quote.
    - A finding that flags his own words quoted back, or a term he used himself, holds nothing back.
    - The cause rule covers a claim of why a system, tool, service, install, run or another session, such as the
      coordinator, a builder or a handler, did something, and a number or state one measured, however the post tells
      it. A cause the session worked out itself, from code, logs or a trace, such as a lookup raising KeyError so the
      process exited, is such a claim, however exact, whatever else the post says it did. The posting session's
      account of its own act or mistake, in its first person, such as "I took it as working because it queues the
      brief" or "you had to say it because my ack read your words as steps", claims no measured cause: no record
      holds the session's own reading, and the rule never asks for one. Nor does an alert or error he shared,
      restated in its own words, such as "the api gets FORBIDDEN because the table is read-only" from his alert: the
      alert measured it.
    - A line stating or applying a rule the installed skill states, such as a defect record for each refusal hit or an
      alert he shares going to the coordinator as an incident, promises nothing beyond his words.
    - A rule of his in <evidence> that limits this kind of post holds it back, wherever else he permits it.
    - A line saying how a failure the post tells is prevented, the tool change and the order or PR carrying it, is
      within his words whenever the post answers a question of his or tells a failure, since his ruling on failures in
      <evidence> asks for it; it is never other work outside them.
"""
RULE_SOURCE = "the rules the drive's checks stand for, in the installed long-running and cc-slack skills' words"
JUDGE = Decide(POST_RULES, frame=POST_FRAME, second_opinion=False, transcript=JUDGED_TURNS, root_transcript=JUDGED_TURNS, tool_results=True)
HANDLER_JUDGE = replace(JUDGE, over=(UserAnswered(approved=False),))
DRIVE_ASK = "drive.ask"
ASK_RULES = """
    The agent is about to keep a question card for the user; <action> holds the case its block makes: the question,
    its summary, the recommended option, the other options, the root cause its findings name, the option they say
    removes it, any words of his it quotes as settled, and a Page line for each web page the card links, holding the
    words that page shows once open. <evidence> holds his ruling on root causes and, beside a Page line, his words on
    a link that opened another view than its card described, each with the rule it sets in its detail. Allow the
    card, citing them, when it keeps every rule. Refuse when it breaks one; the refusal names what the recommended
    option keeps or skips that the root-cause rule forbids, or the link whose page lacks what the card's words say
    it shows, and the fix.
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
        return evt.rewrite(made, note=found.get("additionalContext"))
    if context := found.get("additionalContext"):
        return evt.context(context)
    return None


def judged(evt: BaseHookEvent, reason: str, rule: str, words: list[Evidence], post: dict) -> HookResult | None:
    """Jev's verdict on a post the drive's checks found something in, judged by the rule each finding stands for: Jev reads the post as Slack gets it, each of say's references linked, each finding beside its rule's words, and his messages and card answers in this thread and his answers in the transcripts, and it goes out when no finding's rule holds against the line it names, or his words lift it, and stays back with Jev's reason when one does, never for saying more than his message asked; a judge that gives no verdict, such as one that timed out, leaves it back with the findings and names why, as a check that could not finish does. A handler's own `claude --bg` session reads as a session of his to capt-hook, but every prompt it holds is the communicator's brief or a record, so its judge reads no prompt there as his words."""
    place = {"thread": str(post.get("thread_ts") or "")} | ({"channel": post["channel_id"]} if post.get("channel_id") else {})
    where = f"in thread {place['thread']} of channel {post['channel_id']}" if post.get("channel_id") else "to him in his DM"
    if post.get("channel_id") and not place["thread"]:
        where = f"in the group DM of {post['channel_id']}, him and the bot"
    ctx = Context.of(evt, DRIVE_POST, Proposal(place, {"post": post, "findings": reason}, f"post this line {where}"))
    judge = HANDLER_JUDGE if evt._raw.get("agent_type") == HANDLER and not evt.agent_id else JUDGE
    rules = [Evidence(id="rule:findings", source=RULE_SOURCE, quote=rule)] if rule else []
    if not (items := [*judge.items(ctx), *words, *rules]):
        return evt.block(f"Jev found no rule and no words of his to weigh this post against, so what the drive's checks found stands: fix each finding below, then call say again.\n```\n{reason}\n```")
    try:
        verdict = judge.verdict(ctx, items)
    except Undecided as exc:
        return evt.block(f"Jev gave no verdict ({exc.cause}), and a judge that does not answer never overrides a refusal, so what the drive's checks found stands: fix each finding below, then call say again.\n```\n{reason}\n```")
    if verdict.allow and cited(items, verdict.relied_on):
        return evt.context(f"message_limits: Jev read no finding's rule as holding against this post, so it goes out as written.\n```\nJev: {verdict.reason}\n```")
    return evt.block(f"Jev weighed each finding by its rule and upheld what the drive's checks found: fix what Jev and each finding below name, then call say again.\n```\nJev: {verdict.explained}\n{reason}\n```")


def asked(evt: BaseHookEvent, case: str, words: list[Evidence]) -> HookResult | None:
    """Jev's finding on a question card `drive job ask` keeps, judged by his ruling on root causes and his words on the view a link opens from the case its block makes, the words of each page it links included, and nothing else, which only advises, by his pick "Advisory judge: drive job ask keeps every well-shaped card and prints Jev's finding to the agent; no card is refused on merits": the card is kept whatever Jev finds, with Jev's finding printed to the agent when Jev reads it as breaking the ruling, and a note when Jev gives no verdict."""
    ctx = Context.of(evt, DRIVE_ASK, Proposal({}, {"case": case}, "keep this question card for him"))
    try:
        verdict = ASK_JUDGE.verdict(ctx, words)
    except Undecided as exc:
        return evt.context(f"landing_guard: Jev gave no verdict on this question card ({exc.cause}), so the verb keeps it as written.")
    if verdict.allow and cited(words, verdict.relied_on):
        return None
    found = [f"Jev: {verdict.explained}", *([verdict.reason] if verdict.refusal else [])]
    return evt.context(
        "landing_guard: Jev weighed this question card against his words on root causes and on the view each link opens,"
        " and finds it breaks them; Jev only advises, so the verb keeps the card as written. If the finding holds, ask again with the block rewritten and drop"
        " this one with `drive job ask <job> --drop <its path>`.\n```\n" + "\n".join([*found, case]) + "\n```"
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
        ): Warn(pattern="Jev: The recommended retry keeps the guessed range"),
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
        Input(tool="Bash", tool_input={"command": 'drive job resume j1 "go"', "run_in_background": True}, session_id=COORDINATOR): Rewrite(
            pattern="drive job resume j1", run_in_background="False", timeout="20000"
        ),
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
        Input(tool="Agent", tool_input={"subagent_type": "long-running:reader", "prompt": "Goal: x", "name": "desk"}, session_id=COORDINATOR): Rewrite(
            subagent_type="long-running:reader"
        ),
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
            tool_input={"text": "The fix merged."},
            session_id=COMMUNICATOR,
            agent_id="a1",
            transcript=COMMUNICATOR_TURN,
            root_transcript=[T.user("ship the memory fix", timestamp=SAID_AT.isoformat()), T.assistant("on it")],
            decide=JEV_UNSURE,
            llm={"error": TimeoutError("codex timed out after 19s")},
        ): Block(pattern=r"Jev gave no verdict \(TimeoutError: codex timed out after 19s\).*what the drive's checks found stands"),
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
        Input(
            tool="mcp__plugin_long-running_drive__say",
            tool_input={"text": "drive job resume now works from a session that did not start the job's agent."},
            session_id=HANDLER_SESSION,
            agent_type=HANDLER,
            transcript=HANDLER_TURN,
            decide=JEV_UNSURE,
            llm={"reason": "the user said to post it at his DM root", "allow": True, "relied_on": [RELAYED_ORDER]},
        ): Block(pattern="Jev weighed each finding by its rule and upheld"),
        Input(
            tool="mcp__plugin_long-running_drive__say",
            tool_input={"text": "I took the refusal as working as designed because it queues the brief, so I sent no defect."},
            session_id=COMMUNICATOR,
            transcript=COMMUNICATOR_TURN,
            decide=JEV_UNSURE,
            llm={"reason": "the line is the session's account of its own act, which the cause rule never asks a record for", "allow": True, "relied_on": ["rule:findings"]},
        ): Warn(pattern="Jev read no finding's rule as holding"),
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
        Input(
            tool=POST,
            tool_input={"kind": "report", "to": "communicator@fixture", "text": "POST The check that this pod still runs the agent refused an old pod.", "attrs": {"progress": "true"}},
            session_id=COORDINATOR,
            transcript=TURN,
        ): Warn(pattern="this report goes out as written"),
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
