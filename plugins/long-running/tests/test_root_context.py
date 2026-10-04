from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest
from captain_hook.app import _state
from captain_hook.conditions import matches_conditions
from captain_hook.dispatch import execute_hook
from captain_hook.events import PostToolUseEvent, PreToolUseEvent, StopEvent, UserPromptSubmitEvent
from captain_hook.testing.helpers import StubbedContext, build_context

from hooks import nudges, root_context
from hooks.compaction_handoff import CompactionState
from hooks.tests.root_fixtures import LONG

RULE_NUDGE = (
    "A standing rule stated by the owner must be recorded. "
    "Run `answer_add` with `scope:durable`."
)


def fire(evt) -> list:
    return [
        result
        for entry in _state.hooks
        if Path(str(entry.source_file)) == Path(root_context.__file__)
        and evt.event in entry.spec.events
        and matches_conditions(entry.spec, evt)
        and (result := execute_hook(entry, evt))
    ]


class Root:
    def __init__(self, home: Path) -> None:
        self.home = home
        self.transcript = home / "root.jsonl"
        self.transcript.write_text("")
        self.session_dir = home / "state"
        self.plan = home / ".claude" / "plans" / "brook.md"
        self.plan.parent.mkdir(parents=True)
        self.plan.write_text(LONG)
        self.verdict: dict = {}
        CompactionState(active=True, plan_path=str(self.plan)).save(self.event(PostToolUseEvent, tool_name="Bash"))

    def event(self, cls, **raw):
        payload = {"session_id": "0123456789abcdef", "transcript_path": str(self.transcript), "cwd": str(self.home)}
        ctx = StubbedContext.wrapping(build_context(session_dir=self.session_dir), llm=self.verdict)
        return cls(_raw=payload | raw, ctx=ctx)

    def file(self, relative: str, content: str = LONG) -> Path:
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def pre(self, tool: str, tool_input: dict) -> str | None:
        results = fire(self.event(PreToolUseEvent, tool_name=tool, tool_input=tool_input))
        return results[0].message if results else None

    def read(self, path: Path, **window) -> str | None:
        return self.pre("Read", {"file_path": str(path)} | window)

    def bash(self, command: str) -> str | None:
        return self.pre("Bash", {"command": command})

    def post(self, tool: str, tool_input: dict, response: object = "") -> str | None:
        results = fire(self.event(PostToolUseEvent, tool_name=tool, tool_input=tool_input, tool_response=response))
        return results[0].message if results else None

    def say(self, prompt: str) -> str | None:
        results = fire(self.event(UserPromptSubmitEvent, prompt=prompt))
        return results[0].message if results else None

    def stop(self) -> list[str]:
        evt = self.event(StopEvent)
        fire(evt)
        with nudges.NudgeState.mutate(evt) as state:
            pending, state.pending = state.pending, []
        return pending


class Notes:
    def __init__(self) -> None:
        self.answers: list[dict] = []
        self.adds: list[list[str]] = []

    def __call__(self, cwd: str, *args: str) -> subprocess.CompletedProcess[str]:
        match args:
            case ("answer", "list", "--json", "--label", label, "--limit", "1"):
                found = [answer for answer in self.answers if label in answer["tags"]][:1]
                return subprocess.CompletedProcess(args, 0, json.dumps(found), "")
            case ("answer", "add", "--json", *flags, "--", title):
                self.adds.append(list(args))
                labels = [flags[i + 1] for i, flag in enumerate(flags) if flag == "--label"]
                body = next(flag.removeprefix("--body=") for flag in flags if flag.startswith("--body="))
                answer = {"id": f"{len(self.answers) + 1:07x}" * 2, "title": title, "body": body, "tags": labels}
                self.answers.append(answer)
                return subprocess.CompletedProcess(args, 0, json.dumps(answer), "")
        raise AssertionError(f"unexpected ccn call: {args}")


@pytest.fixture
def notes(monkeypatch: pytest.MonkeyPatch) -> Notes:
    fake = Notes()
    monkeypatch.setattr(root_context, "ccn", fake)
    return fake


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, notes: Notes) -> Root:
    monkeypatch.setenv("HOME", str(tmp_path))
    return Root(tmp_path)


@pytest.mark.parametrize(
    "relative",
    ["scratch/audits/plan-deviation.md", "scratch/parity/briefs/l17.md", "scratch/handoffs/orca-desk.md", "x/tool-results/b1.txt", "p/agent-a1.jsonl"],
)
def test_lane_artifacts_block_at_any_size(root: Root, relative: str) -> None:
    message = root.read(root.file(relative, "one line\n"))

    assert message is not None and message.startswith("Lane artifacts are read by a lane")


def test_the_plan_and_progress_folder_read_in_full(root: Root) -> None:
    assert root.read(root.plan) is None
    assert root.read(root.file(".claude/plans/brook-progress/2026-10-01.md")) is None


def test_an_inbox_tail_passes_and_a_full_inbox_read_blocks(root: Root) -> None:
    inbox = root.file("scratch/inbox/orca-desk.md")

    assert root.read(inbox, offset=380) is None
    assert "A read this large" in (root.read(inbox) or "")


def test_threshold_is_configurable(root: Root, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LONG_RUNNING_ROOT_READ_LINES", "500")

    assert root.read(root.file("notes.md")) is None


def test_reads_of_inbox_files_pass_and_repo_files_block(root: Root) -> None:
    root.file("scratch/inbox/orca-desk.md")
    root.file("src/app.py")

    assert root.bash("tail -n 20 scratch/inbox/orca-desk.md") is None
    assert root.bash("rg -n R57 scratch/inbox/orca-desk.md") is None
    assert "File reads" in (root.bash("cat src/app.py") or "")
    assert "Searches" in (root.bash("rg -n R57 scratch/inbox/orca-desk.md src") or "")
    assert root.bash("sed -i '' 's/a/b/' src/app.py") is None
    assert "File reads" in (root.bash("grep -i from src/app.py") or "")
    assert "File reads" in (root.bash("cat src/*.py") or "")


OUTSIDE_THE_RULE = "allowed, the model found the call outside the rule"


@pytest.mark.parametrize(
    ("files", "command"),
    [
        (
            ["scratch/release-v3/handoffs/incident-slack-watch-8.md"],
            "head -c 1200 ~/scratch/release-v3/handoffs/incident-slack-watch-8.md; echo; echo ---; "
            "ls ~/scratch/release-v3/slack-watch* ~/scratch/release-v3/*/slack-watch-cursor.json 2>/dev/null",
        ),
        (
            ["plugins/cache/skills/long-running/0.6.59/skills/long-running/SKILL.md"],
            "ls -d {home}/plugins/cache/skills/long-running/0.6.59/bin && grep -c 'lands whole, at green' "
            "{home}/plugins/cache/skills/long-running/0.6.59/skills/long-running/SKILL.md",
        ),
        (
            ["scratch/release-v3/briefs/l17.md", "scratch/release-v3/audits/root-shortcomings-deep-dive.md"],
            "cd ~/scratch/release-v3 && ls -t briefs | head -n 8 && "
            "grep -n -i -E '^#+ .*(brief|launch)' audits/root-shortcomings-deep-dive.md | head -n 12",
        ),
    ],
)
def test_logged_bounded_single_file_reads_pass_without_the_model(root: Root, files: list[str], command: str) -> None:
    for relative in files:
        root.file(relative)

    assert root.bash(command.format(home=root.home)) is None


@pytest.mark.parametrize(
    ("message", "tool", "tool_input"),
    [
        (
            "Git history reads",
            "Bash",
            {
                "command": "git fetch -q origin dev 2>/dev/null; git show origin/dev:AGENTS.md | "
                "grep -n -i -B2 -A6 'green bottom prefix\\|never lands alone' ; echo ---; "
                "git log origin/dev --oneline -i --grep='#28601' -n 3 --format='%h %an %s'"
            },
        ),
        ("Slack reads", "mcp__slack__slack_get_thread", {"channel_id": "C0BQSADC7SS", "thread_ts": "1790875497.353829"}),
        ("Slack reads", "mcp__slack__slack_get_thread", {"channel_id": "C09G3N98YM6", "thread_ts": "1790890875.742979"}),
        (
            "File reads",
            "Bash",
            {
                "command": "ccn doc list --label progress:release-v3 2>&1 | head -5; echo ---; "
                "grep -E 'msg_0b1fc02709f3|msg_e7192ebb5a23' ~/scratch/release-v3/orca-waiter/batches.jsonl | "
                "python3 -c 'import sys; print(sys.stdin.read())'"
            },
        ),
    ],
)
@pytest.mark.parametrize("block", [True, False])
def test_logged_control_plane_reads_go_to_the_model(
    root: Root, message: str, tool: str, tool_input: dict, block: bool
) -> None:
    root.file("scratch/release-v3/orca-waiter/batches.jsonl", '{"from": "orca"}\n')
    root.verdict["block"] = block

    result = root.pre(tool, tool_input) or ""

    assert result.startswith(message) if block else OUTSIDE_THE_RULE in result


@pytest.mark.parametrize("command", ["ls -d {home}/scratch", "ls {home}/scratch", "ls -la {home}/scratch"])
def test_a_directory_listing_passes(root: Root, command: str) -> None:
    root.file("scratch/inbox/orca-desk.md")

    assert root.bash(command.format(home=root.home)) is None


@pytest.mark.parametrize(
    "command",
    [
        "head -c 4001 src/app.py",
        "head -n 41 src/app.py",
        "tail -n +2 src/app.py",
        "grep -m 21 import src/app.py",
        "grep -n import src/app.py",
        "head -n 5 src/app.py; head -n 5 src/app.py",
        "head -n 5 src/*.py",
    ],
)
def test_an_unbounded_or_second_read_still_blocks(root: Root, command: str) -> None:
    root.file("src/app.py")
    root.file("src/cli.py")

    assert (root.bash(command) or "").startswith("File reads")


@pytest.mark.parametrize("command", ["ccx vcs status | grep src", "ccx vcs status | jq .", "echo x | sed -n 1p"])
def test_filters_over_piped_output_pass_even_when_the_pattern_names_a_path(root: Root, command: str) -> None:
    root.file("src/app.py")

    assert root.bash(command) is None


def test_oversized_mcp_response_blocks_its_next_call(root: Root) -> None:
    assert root.pre("mcp__linear__get_issue", {"id": "ENG-1"}) is None

    warning = root.post("mcp__linear__get_issue", {"id": "ENG-1"}, [{"type": "text", "text": "x" * 9000}])

    assert warning is not None and "overflowed the drive root" in warning
    assert "fetch is too large" in (root.pre("mcp__linear__get_issue", {"id": "ENG-2"}) or "")


def test_unrecorded_standing_rule_nudges_at_stop(root: Root, notes: Notes) -> None:
    assert root.say("From now on, release everything as it merges.") is None
    root.post("Bash", {"command": "date"})

    assert notes.adds == []
    assert root.stop() == [RULE_NUDGE]
    assert root.stop() == []


def test_a_titled_standing_rule_records_itself(root: Root, notes: Notes) -> None:
    root.verdict["title"] = "When does a merged PR get released?"
    prompt = "From now on, release everything as it merges.\n  Every target."

    message = root.say(prompt)

    [answer] = notes.answers
    assert message == f"Recorded this standing rule as cc-notes answer `{answer['id'][:7]}`; never record it again by hand."
    assert answer["title"] == "When does a merged PR get released?"
    assert {"scope:durable", "owner-ruling"} <= set(answer["tags"])
    assert re.fullmatch(
        re.escape(prompt) + r"\n\nOwner, \d{4}-\d{2}-\d{2} \d{1,2}:\d{2} [AP]M PT, session 0123456789abcdef",
        answer["body"],
    )
    assert root.stop() == []


def test_a_rerun_of_one_message_records_one_answer(root: Root, notes: Notes) -> None:
    root.verdict["title"] = "When does a merged PR get released?"

    first = root.say("from now on, release everything as it merges")
    second = root.say("from now on, release everything as it merges")
    root.say("never skip review")

    assert first == second
    assert len(notes.adds) == 2
    assert root.stop() == []


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("mcp__plugin_cc-notes_cc-notes__answer_add", {"title": "Release as merged?", "body": "yes"}),
        ("mcp__plugin_cc-notes_cc-notes__answer_edit", {"id": "4ffc9a5", "body": "yes"}),
        ("Bash", {"command": "ccn answer add 'Release as merged?' --body yes --label scope:durable"}),
        ("Bash", {"command": "ccn -R ~/Code/monorepo answer edit 4ffc9a5 --body yes"}),
    ],
)
def test_recorded_standing_rule_is_quiet(root: Root, tool: str, tool_input: dict) -> None:
    root.say("I told you: the plan is to release as merged")
    root.post(tool, tool_input)

    assert root.stop() == []


@pytest.mark.parametrize(
    "prompt",
    [
        "what is the status of l17?",
        '<teammate-message teammate_id="orca-desk">\nwe always reread the inbox\n</teammate-message>',
        "<command-name>/compact</command-name> never mind",
    ],
)
def test_non_owner_or_non_rule_prompts_are_quiet(root: Root, prompt: str) -> None:
    root.say(prompt)

    assert root.stop() == []


def test_an_answer_from_an_interrupted_turn_does_not_cover_the_next_rule(root: Root) -> None:
    root.say("from now on, release as merged")
    root.post("mcp__plugin_cc-notes_cc-notes__answer_add", {"title": "Release as merged?", "body": "yes"})
    root.say("never skip review")

    assert root.stop() == [RULE_NUDGE]


def approval(*previews: str) -> dict:
    options = [{"label": f"Send {n}", "preview": preview} for n, preview in enumerate(previews)]
    return {"questions": [{"question": "Post the Slack lane's reply?", "options": [*options, {"label": "Hold"}]}]}


@pytest.mark.parametrize(
    "preview",
    ["From now on we release as merged.", "Going forward, every PR lands whole.", "we now enqueue stacks whole", "#28797 landed."],
)
def test_an_approved_slack_preview_never_nudges(root: Root, preview: str) -> None:
    root.post("AskUserQuestion", approval(preview))

    assert root.stop() == []


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("mcp__slack__slack_send_message", {"channel_id": "C0B", "text": "_(Yasyf's Claude)_ On it"}),
        ("mcp__slack__slack_add_reaction", {"channel_id": "C0B", "timestamp": "1.2", "reaction": "eyes"}),
        ("mcp__slack__slack_remove_reaction", {"channel_id": "C0B", "timestamp": "1.2", "reaction": "eyes"}),
        ("mcp__plugin_cc-slack_cc-slack__slack_reply", {"channel_id": "C0B", "thread_ts": "1.2", "text": "On it"}),
        ("mcp__plugin_cc-slack_cc-slack__slack_react", {"channel_id": "C0B", "ts": "1.2", "name": "eyes"}),
        ("Bash", {"command": "~/.claude/plugins/cache/forge/cc-slack/0.2.11/bin/cc-slack react --url C0B/p1 --name eyes"}),
        ("Bash", {"command": "cc-slack reply --url C0B/p1 --text 'On it' # ccx:raw"}),
    ],
)
def test_the_root_never_writes_to_slack(root: Root, tool: str, tool_input: dict) -> None:
    message = root.pre(tool, tool_input) or ""

    assert message.startswith("The drive root never writes to Slack.")
    assert "long-running:lane-ship" in message


@pytest.mark.parametrize(
    "command",
    ["cc-slack dm-status --text 'parity wave 3 landed'", "cc-slack whoami", "cc-slack status"],
)
def test_the_root_keeps_its_own_dm_status_and_identity_checks(root: Root, command: str) -> None:
    assert root.bash(command) is None


def test_an_approved_slack_preview_does_not_record_a_typed_standing_rule(root: Root) -> None:
    root.post("mcp__plugin_cc-notes_cc-notes__answer_add", {"title": "Earlier", "body": "yes"})
    root.say("from now on, release as merged")
    root.post("AskUserQuestion", approval("From now on we release as merged."))

    assert root.stop() == [RULE_NUDGE]
