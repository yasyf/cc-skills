from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

import pytest
from cc_transcript.query import Session
from captain_hook.events import PostToolUseEvent, PreToolUseEvent, StopEvent
from captain_hook.testing.helpers import StubbedContext, build_context, stubbed_commands

from fire import fire
from hooks import nudges, owner_facing
from hooks.compaction_handoff import CompactionState
from hooks.tests.board_fixtures import ANSWERS, GLOSSARY, ROWS
from hooks.tests.incident_fixtures import OUTAGE_TWO, opened
from long_running import actions

MISFIRES = {
    "artifact-retry-repush": (
        "Lane artifact-retry-repush (release-v3). Effort xhigh. Authority: stack writes on branch "
        "yasyf/v3-incident-api-1n80-sandsql-fetch-fix (PR #29016, SandSQL fetch retry, incident fix ahead of parity) only."
    ),
    "incident-runner": (
        "Lane incident-runner (release-v3 tooling track). Effort xhigh. Owner go: implement brief 1 of the astra deep "
        "dive, a durable incident executor in the long-running plugin."
    ),
    "desk-runner": (
        "Lane desk-runner (release-v3 tooling track). Effort xhigh. Owner go: implement brief 3 of the astra deep dive. "
        "Scope: incident relay plus one landing workflow."
    ),
}
ROLES = {"artifact-retry-repush": "ship", "incident-runner": "tooling", "desk-runner": "tooling"}


def entry(kind: str, text: str, *, sidechain: bool = False) -> dict:
    message = {"role": kind, "content": [{"type": "text", "text": text}]}
    if kind == "assistant":
        usage = {"input_tokens": 2, "output_tokens": 1, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
        message |= {"model": "claude-opus-5-5", "usage": usage}
    return {
        "type": kind,
        "isSidechain": sidechain,
        "timestamp": "2026-10-01T12:00:00.000Z",
        "uuid": str(uuid.uuid4()),
        "parentUuid": None,
        "sessionId": "0123456789abcdef",
        "message": message,
    }


class Root:
    def __init__(self, home: Path, *, active: bool = True) -> None:
        self.transcript = home / "root.jsonl"
        self.session_dir = home / "state"
        seed = PostToolUseEvent(
            _raw={"session_id": "0123456789abcdef", "tool_name": "Bash"}, ctx=build_context(session_dir=self.session_dir)
        )
        CompactionState(active=active).save(seed)

    def event(self, cls, **raw):
        payload = {"session_id": "0123456789abcdef", "transcript_path": str(self.transcript), "cwd": str(self.transcript.parent)}
        transcript = Session.from_path(self.transcript)
        return cls(_raw=payload | raw, ctx=build_context(transcript=transcript, session_dir=self.session_dir))

    def spawn(self, name: str, prompt: str) -> str | None:
        raw = {"session_id": "0123456789abcdef", "cwd": str(self.transcript.parent)}
        ctx = StubbedContext.wrapping(build_context(session_dir=self.session_dir))
        tool_input = {"name": name, "prompt": prompt, "subagent_type": "long-running:lane-ship"}
        evt = PreToolUseEvent(_raw=raw | {"tool_name": "Agent", "tool_input": tool_input}, ctx=ctx)
        results = fire(owner_facing, evt)
        return results[0].message if results else None

    def tool(self, name: str, tool_input: dict, *posted: dict, **raw: str) -> list[str]:
        payload = {"session_id": "0123456789abcdef", "cwd": str(self.transcript.parent), "tool_name": name, "tool_input": tool_input}
        ctx = StubbedContext.wrapping(build_context(session_dir=self.session_dir))
        evt = PreToolUseEvent(_raw=payload | raw, ctx=ctx)
        with stubbed_commands({" ".join(owner_facing.INCIDENT_DIGEST): json.dumps({"open_incidents": list(posted)})}):
            return [result.message for result in fire(owner_facing, evt)]

    def reply(self, *entries: dict) -> list[str]:
        self.transcript.write_text("".join(json.dumps(item) + "\n" for item in entries))
        evt = self.event(StopEvent)
        owner_facing.nudge_utc_in_owner_replies(evt)
        with nudges.NudgeState.mutate(evt) as state:
            pending, state.pending = state.pending, []
        return pending

    def plain_reply(self, text: str, terms: list[str] | None = None) -> list[str]:
        self.transcript.write_text(json.dumps(entry("user", "status?")) + "\n" + json.dumps(entry("assistant", text)) + "\n")
        payload = {"session_id": "0123456789abcdef", "transcript_path": str(self.transcript), "cwd": str(self.transcript.parent)}
        context = build_context(transcript=Session.from_path(self.transcript), session_dir=self.session_dir)
        evt = StopEvent(_raw=payload, ctx=StubbedContext.wrapping(context, llm={"terms": terms} if terms else None))
        with stubbed_commands(PLAIN_READS):
            assert owner_facing.plain_words_on_reply(evt) is None
        with nudges.NudgeState.mutate(evt) as state:
            pending, state.pending = state.pending, []
        return pending

    def publish(self, text: str) -> str | None:
        self.transcript.write_text(json.dumps(entry("user", "status?")) + "\n" + json.dumps(entry("assistant", text)) + "\n")
        with stubbed_commands({" ".join(owner_facing.INCIDENT_DIGEST): json.dumps({"open_incidents": []})}):
            result = owner_facing.publish_lines_quote_the_released_record(self.event(StopEvent))
        return result.message if result else None


@pytest.fixture
def refused(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    lanes: list[str] = []
    monkeypatch.setattr(owner_facing, "refused_on_load", lambda evt, lane: lane in lanes)
    return lanes


@pytest.fixture
def root(tmp_path: Path, refused: list[str]) -> Root:
    return Root(tmp_path)


@pytest.fixture
def incidents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path / ".claude" / "long-running" / "incidents"


def incident(incidents: Path, name: str, status: str, *, hours_ago: float = 1, **facts: str) -> None:
    record = {
        "incident_id": name,
        "owner": f"incident-{name}",
        "owner_generation": 1,
        "opened_at": opened(hours_ago),
        "facts": facts,
        "status": status,
        "actions": {},
    }
    actions.Store(incidents).create(actions.Incident.load(record))


def question(text: str) -> dict:
    return {"questions": [{"question": text, "header": "Ask", "options": [{"label": "Go"}, {"label": "Hold"}], "multiSelect": False}]}


@pytest.mark.parametrize(
    "text",
    ["Fix lane spawned at 17:35Z; mechanism by 17:45Z.", "Owner asks landed 17:3xZ.", "Red since 16:52 UTC."],
)
def test_utc_times_in_the_last_reply_nudge(root: Root, text: str) -> None:
    assert root.reply(entry("user", "status?"), entry("assistant", text)) == [owner_facing.UTC_IN_REPLY]


def test_the_utc_nudge_fires_at_most_once_an_hour(root: Root) -> None:
    said = (entry("user", "status?"), entry("assistant", "Red since 16:52 UTC."))

    assert root.reply(*said) == [owner_facing.UTC_IN_REPLY]
    assert root.reply(*said) == []


@pytest.mark.parametrize(
    ("status", "text", "warned"),
    [
        ("open", "sand 1437 is published", True),
        ("open", "sand 1437 is published (#103935)", False),
        ("closed", "sand 1437 is published", False),
    ],
    ids=["open-incident-from-memory", "quotes-the-released-record", "closed-incident"],
)
def test_a_publish_line_during_an_open_incident_quotes_the_released_record(root: Root, incidents: Path, status: str, text: str, warned: bool) -> None:
    incident(incidents, "0820", status, target="sand")

    assert root.publish(text) == (owner_facing.PUBLISH_FROM_MEMORY if warned else None)


@pytest.mark.parametrize("text", ["Fix lane spawned at 10:35am; mechanism by 10:45.", "Build 72755 is red."])
def test_pacific_or_timeless_replies_are_quiet(root: Root, text: str) -> None:
    assert root.reply(entry("assistant", text)) == []


def test_only_the_root_reply_counts(root: Root) -> None:
    assert root.reply(entry("assistant", "Pacific 10:35am."), entry("assistant", "lane at 17:35Z", sidechain=True)) == []


def test_outside_a_drive_is_quiet(tmp_path: Path) -> None:
    assert Root(tmp_path, active=False).reply(entry("assistant", "at 17:35Z")) == []


@pytest.mark.parametrize("name", MISFIRES)
def test_logged_misfires_pass_once_their_role_is_declared(root: Root, name: str) -> None:
    header = f"ccx: role={ROLES[name]}" + (f" tooling-lane={name}" if ROLES[name] == "tooling" else "")

    assert root.spawn(name, f"{header}\n{MISFIRES[name]}") is None


@pytest.mark.parametrize("name", MISFIRES)
def test_logged_misfires_without_a_role_warn(root: Root, name: str) -> None:
    assert root.spawn(name, MISFIRES[name]) == owner_facing.INCIDENT_ROUTE


def test_a_fix_lane_with_incident_turn_authority_warns(root: Root) -> None:
    prompt = "ccx: role=fix\nYou are api-1n80-fix, fixing the outage behind API-1N80.\nAuthority: Incident Turn."

    assert root.spawn("api-1n80-fix", prompt) == owner_facing.INCIDENT_ROUTE


@pytest.mark.parametrize(
    ("name", "prompt"),
    [
        ("incident-api-1n80-evidence", "ccx: role=evidence\nRead telemetry for the incident."),
        ("ledger-fix", "ccx: role=fix\nFix the ledger's quota probe."),
        ("incident-api-1n80-watch", "ccx: role=watch\nWatch the incident thread."),
    ],
)
def test_declared_support_and_non_incident_fix_lanes_pass(root: Root, name: str, prompt: str) -> None:
    assert root.spawn(name, prompt) is None


def test_a_fix_lane_the_desk_refused_on_load_spawns_in_process(root: Root, refused: list[str]) -> None:
    prompt = "ccx: role=fix\nYou are api-1n80-fix, fixing the outage behind API-1N80.\nAuthority: Incident Turn."
    refused.append("api-1n80-fix")

    assert root.spawn("api-1n80-fix", prompt) is None
    assert root.spawn("api-1n81-fix", prompt) == owner_facing.INCIDENT_ROUTE


@pytest.mark.parametrize("tool", ["EnterPlanMode", "ExitPlanMode"])
def test_plan_mode_warns_while_a_drive_incident_is_open(root: Root, incidents: Path, tool: str) -> None:
    incident(incidents, "0820", "fix-pending", target="executor")

    assert root.tool(tool, {}) == [owner_facing.PLAN_MODE_IN_INCIDENT]


def test_a_posted_incident_record_warns_on_plan_mode(root: Root, incidents: Path) -> None:
    assert root.tool("EnterPlanMode", {}, OUTAGE_TWO) == [owner_facing.PLAN_MODE_IN_INCIDENT]


@pytest.mark.parametrize(("status", "hours_ago"), [("closed", 1), ("recovered", 1), ("fix-pending", 7)])
def test_plan_mode_passes_once_the_incident_closes_or_ages_out(root: Root, incidents: Path, status: str, hours_ago: float) -> None:
    incident(incidents, "0820", status, hours_ago=hours_ago, target="executor")

    assert root.tool("EnterPlanMode", {}) == []


def test_plan_mode_outside_a_drive_passes(tmp_path: Path, incidents: Path, refused: list[str]) -> None:
    incident(incidents, "0820", "fix-pending", target="executor")

    assert Root(tmp_path, active=False).tool("EnterPlanMode", {}) == []


def test_an_owner_question_on_other_work_warns_while_the_alert_is_active(root: Root, incidents: Path) -> None:
    incident(incidents, "sand-not-migrated", "open", target="sand")

    [message] = root.tool("AskUserQuestion", question("Should I extend the backlog deadline to 11:30?"))

    assert message.startswith("Warning, nothing was blocked: the alert is active") and "sand-not-migrated" in message


def test_the_incident_own_question_passes(root: Root, incidents: Path) -> None:
    incident(incidents, "sand-not-migrated", "open", target="sand")

    assert root.tool("AskUserQuestion", question("sand-not-migrated: approve the 0-delete apply on plat?")) == []


def test_an_owner_question_after_the_incident_closes_passes(root: Root, incidents: Path) -> None:
    incident(incidents, "sand-not-migrated", "closed", target="sand")

    assert root.tool("AskUserQuestion", question("Should I extend the backlog deadline to 11:30?")) == []


def test_a_root_probe_warns_while_the_alert_is_active(root: Root, incidents: Path) -> None:
    incident(incidents, "sand-not-migrated", "open", target="sand")

    assert root.tool("Bash", {"command": "kubectl -n plat get deploy api"}) == [owner_facing.ROOT_PROBE]


@pytest.mark.parametrize(
    ("command", "raw"),
    [
        ("kubectl -n plat get deploy api", {"agent_id": "aevidence-0123456789abcdef"}),
        ("cci post --kind decision --lane root --to inc-sand-fix 'ship the fix'", {}),
    ],
)
def test_lane_probes_and_root_routing_pass(root: Root, incidents: Path, command: str, raw: dict) -> None:
    incident(incidents, "sand-not-migrated", "open", target="sand")

    assert root.tool("Bash", {"command": command}, **raw) == []


PLAIN_READS = {f"{owner_facing.RULINGS} rows": ROWS, f"{owner_facing.RULINGS} glossary": GLOSSARY, "ccn -R": ANSWERS}


@pytest.fixture
def drive_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Root:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.delenv("CLAUDE_LONG_RUNNING_DRIVE", raising=False)
    drives = tmp_path / "config" / "long-running" / "drives"
    drives.mkdir(parents=True)
    entry = {"drive": "brook", "sessions": ["0123456789abcdef"], "program": "brook", "state_dir": str(tmp_path / "scratch")}
    (drives / "brook.json").write_text(json.dumps(entry))
    root = Root(tmp_path)
    seed = PostToolUseEvent(_raw={"session_id": "0123456789abcdef", "tool_name": "Bash"}, ctx=build_context(session_dir=root.session_dir))
    CompactionState(active=True, slug="brook").save(seed)
    return root


def test_a_status_card_in_drive_words_is_warned_and_its_pr_options_pass(drive_root: Root) -> None:
    card = {"status": "needs_you", "title": "Merge the slice?", "body": "The desk graded it.", "options": ["Merge #42301", "Hold"]}
    payload = {"session_id": "0123456789abcdef", "cwd": str(drive_root.transcript.parent), "tool_name": owner_facing.SLACK_CARD, "tool_input": {"card": card}}
    evt = PreToolUseEvent(_raw=payload, ctx=StubbedContext.wrapping(build_context(session_dir=drive_root.session_dir)))

    with stubbed_commands(PLAIN_READS):
        message = owner_facing.plain_words_in_owner_questions(evt).message

    assert message.startswith(owner_facing.PLAIN_WORDS)
    assert 'card title has a drive word, "slice"' in message
    assert 'card body has a drive word, "desk"' in message
    assert "#42301" not in message


def test_the_root_reply_in_drive_words_is_nudged_never_blocked(drive_root: Root) -> None:
    [nudge] = drive_root.plain_reply("The slice for lane p1a is done.")

    assert nudge.startswith(owner_facing.REPLY_IN_PLAIN_WORDS)
    assert 'your reply has a drive word, "slice"' in nudge


def test_a_term_already_flagged_this_hour_is_not_nudged_again(drive_root: Root) -> None:
    assert len(drive_root.plain_reply("The slice for lane p1a is done.")) == 1
    assert drive_root.plain_reply("The slice for lane p1a is done.") == []


def test_the_root_reply_is_not_nudged_for_terms_the_reader_names_that_the_reply_does_not_contain(drive_root: Root) -> None:
    [nudge] = drive_root.plain_reply("The chat-list fix is live for every team.", ["REPLY_IN_PLAIN_WORDS", "claude-haiku-5-5", "chat-list"])

    assert 'the owner would need "chat-list" defined' in nudge
    assert "REPLY_IN_PLAIN_WORDS" not in nudge and "claude-haiku" not in nudge


def test_the_root_reply_is_nudged_for_a_lane_name_and_never_for_engineering_words(drive_root: Root) -> None:
    reply = "Break-glass is granted on the stack; the drift preview and the pre-release check passed, and p1a-land is merged."
    terms = ["Break-glass", "stack", "drift preview", "pre-release check", "p1a-land"]

    [nudge] = drive_root.plain_reply(reply, terms)

    assert 'the owner would need "p1a-land" defined' in nudge
    assert [term for term in terms[:-1] if f'"{term}"' in nudge] == []


def test_a_ruling_card_quoting_the_owner_verbatim_passes_and_an_invented_quote_is_warned(drive_root: Root, tmp_path: Path) -> None:
    state = tmp_path / "scratch"
    state.mkdir()
    capture = {"digest": "a" * 16, "at": "2026-01-02T03:04:05Z", "session": "s-owner", "source": "prompt", "text": "keep the desk copy on the laptop. never on the phone"}
    (state / "owner-prompts.jsonl").write_text(json.dumps(capture) + "\n")
    reads = PLAIN_READS | {f"{owner_facing.RULINGS} rows": json.dumps(json.loads(ROWS) | {"state_dir": str(state)})}

    def card(title: str):
        card = {"status": "needs_you", "title": title, "body": "This also means:\n1. Nothing copies a note back to the phone.", "options": ["Right", "Some wrong", "Open board"]}
        payload = {"session_id": "0123456789abcdef", "cwd": str(drive_root.transcript.parent), "tool_name": owner_facing.SLACK_CARD, "tool_input": {"card": card}}
        with stubbed_commands(reads):
            return owner_facing.plain_words_in_owner_questions(PreToolUseEvent(_raw=payload, ctx=StubbedContext.wrapping(build_context(session_dir=drive_root.session_dir))))

    assert card("“keep the desk copy on the laptop.”") is None
    assert 'card title has a drive word, "desk"' in card("“keep the desk copy on the phone.”").message


def test_the_closeout_grep_passes_its_dash_led_pattern_after_a_double_dash():
    argv = owner_facing.INCIDENT_CLOSEOUT
    pattern = next(arg for arg in argv if "FIX-LIVE" in arg)
    assert pattern.startswith("-") and argv[argv.index(pattern) - 1] == "--"


def test_a_failed_read_names_the_exception_a_traceback_ends_on() -> None:
    stderr = 'Traceback (most recent call last):\n  File "rulings.py", line 2648, in <module>\n    raise SystemExit(main())\nsubprocess.CalledProcessError: Command \'ccn ledger list\' returned non-zero exit status 128.\n'
    failure = subprocess.CalledProcessError(1, ["rulings.py", "glossary"], stderr=stderr)
    assert owner_facing.reason(failure) == "exit 1: subprocess.CalledProcessError: Command 'ccn ledger list' returned non-zero exit status 128."
