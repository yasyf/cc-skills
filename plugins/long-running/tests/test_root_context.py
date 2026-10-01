from __future__ import annotations

from pathlib import Path

import pytest
from captain_hook.events import PostToolUseEvent, PreToolUseEvent, StopEvent, UserPromptSubmitEvent
from captain_hook.testing.helpers import build_context

from hooks import nudges, root_context
from hooks.compaction_handoff import CompactionState

LONG = "line\n" * 400


class Root:
    def __init__(self, home: Path) -> None:
        self.home = home
        self.transcript = home / "root.jsonl"
        self.transcript.write_text("")
        self.session_dir = home / "state"
        self.plan = home / ".claude" / "plans" / "brook.md"
        self.plan.parent.mkdir(parents=True)
        self.plan.write_text(LONG)
        CompactionState(active=True, plan_path=str(self.plan)).save(self.event(PostToolUseEvent, tool_name="Bash"))

    def event(self, cls, **raw):
        payload = {"session_id": "0123456789abcdef", "transcript_path": str(self.transcript), "cwd": str(self.home)}
        return cls(_raw=payload | raw, ctx=build_context(session_dir=self.session_dir))

    def file(self, relative: str, content: str = LONG) -> Path:
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def pre(self, tool: str, tool_input: dict) -> str | None:
        result = root_context.keep_lane_reads_out_of_root(self.event(PreToolUseEvent, tool_name=tool, tool_input=tool_input))
        return result.message if result else None

    def read(self, path: Path, **window) -> str | None:
        return self.pre("Read", {"file_path": str(path)} | window)

    def bash(self, command: str) -> str | None:
        return self.pre("Bash", {"command": command})

    def post(self, tool: str, tool_input: dict, response: object = "") -> str | None:
        evt = self.event(PostToolUseEvent, tool_name=tool, tool_input=tool_input, tool_response=response)
        root_context.nudge_unrecorded_standing_rule(evt)
        result = root_context.learn_oversized_mcp(evt)
        return result.message if result else None

    def say(self, prompt: str) -> None:
        root_context.nudge_unrecorded_standing_rule(self.event(UserPromptSubmitEvent, prompt=prompt))

    def stop(self) -> list[str]:
        evt = self.event(StopEvent)
        root_context.nudge_unrecorded_standing_rule(evt)
        with nudges.NudgeState.mutate(evt) as state:
            pending, state.pending = state.pending, []
        return pending


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Root:
    monkeypatch.setenv("HOME", str(tmp_path))
    return Root(tmp_path)


@pytest.mark.parametrize(
    "relative",
    ["scratch/audits/plan-deviation.md", "scratch/parity/briefs/l17.md", "scratch/handoffs/orca-desk.md", "x/tool-results/b1.txt", "p/agent-a1.jsonl"],
)
def test_lane_artifacts_block_at_any_size(root: Root, relative: str) -> None:
    message = root.read(root.file(relative, "one line\n"))

    assert message is not None and message.startswith("delegate to a lane: Explore (model: sonnet) — reading lane artifact")


def test_the_plan_and_progress_folder_read_in_full(root: Root) -> None:
    assert root.read(root.plan) is None
    assert root.read(root.file(".claude/plans/brook-progress/2026-10-01.md")) is None


def test_an_inbox_tail_passes_and_a_full_inbox_read_blocks(root: Root) -> None:
    inbox = root.file("scratch/inbox/orca-desk.md")

    assert root.read(inbox, offset=380) is None
    assert "400-line read" in (root.read(inbox) or "")


def test_threshold_is_configurable(root: Root, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LONG_RUNNING_ROOT_READ_LINES", "500")

    assert root.read(root.file("notes.md")) is None


def test_reads_of_inbox_files_pass_and_repo_files_block(root: Root) -> None:
    root.file("scratch/inbox/orca-desk.md")
    root.file("src/app.py")

    assert root.bash("tail -n 20 scratch/inbox/orca-desk.md") is None
    assert root.bash("rg -n R57 scratch/inbox/orca-desk.md") is None
    assert "`cat` read" in (root.bash("cat src/app.py") or "")
    assert "`rg` search" in (root.bash("rg -n R57 scratch/inbox/orca-desk.md src") or "")
    assert root.bash("sed -i '' 's/a/b/' src/app.py") is None
    assert "`grep` read" in (root.bash("grep -i from src/app.py") or "")
    assert "`cat` read" in (root.bash("cat src/*.py") or "")


@pytest.mark.parametrize("command", ["ccx vcs status | grep src", "ccx vcs status | jq .", "echo x | sed -n 1p"])
def test_filters_over_piped_output_pass_even_when_the_pattern_names_a_path(root: Root, command: str) -> None:
    root.file("src/app.py")

    assert root.bash(command) is None


def test_oversized_mcp_response_blocks_its_next_call(root: Root) -> None:
    assert root.pre("mcp__linear__get_issue", {"id": "ENG-1"}) is None

    warning = root.post("mcp__linear__get_issue", {"id": "ENG-1"}, [{"type": "text", "text": "x" * 9000}])

    assert warning is not None and "its next call blocks" in warning
    assert "`mcp__linear__get_issue` fetch" in (root.pre("mcp__linear__get_issue", {"id": "ENG-2"}) or "")


def test_unrecorded_standing_rule_nudges_at_stop(root: Root) -> None:
    root.say("From now on, release everything as it merges.")
    root.post("Bash", {"command": "date"})

    assert root.stop() == [f"{root_context.UNRECORDED} — From now on, release everything as it merges."]
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

    assert root.stop() == [f"{root_context.UNRECORDED} — never skip review"]


def approval(*previews: str) -> dict:
    options = [{"label": f"Send {n}", "preview": preview} for n, preview in enumerate(previews)]
    return {"questions": [{"question": "Post the Slack lane's reply?", "options": [*options, {"label": "Hold"}]}]}


@pytest.mark.parametrize(
    "preview",
    ["From now on we release as merged.", "Going forward, every PR lands whole.", "we now enqueue stacks whole"],
)
def test_unrecorded_slack_commitment_nudges_at_stop(root: Root, preview: str) -> None:
    root.post("AskUserQuestion", approval(preview))

    assert root.stop() == [root_context.UNRECORDED_COMMITMENT]
    assert root.stop() == []


def test_recorded_slack_commitment_is_quiet(root: Root) -> None:
    root.post("AskUserQuestion", approval("From now on we release as merged."))
    root.post("mcp__plugin_cc-notes_cc-notes__answer_add", {"title": "Release as merged?", "body": "yes, <permalink>"})

    assert root.stop() == []


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("AskUserQuestion", approval("#28797 landed.")),
        ("AskUserQuestion", {"questions": [{"question": "We will ship l17 next?", "options": [{"label": "Yes"}]}]}),
        ("Bash", {"command": "cc-slack thread C0B/p1 # we will see"}),
    ],
)
def test_slack_posts_without_commitments_are_quiet(root: Root, tool: str, tool_input: dict) -> None:
    root.post(tool, tool_input)

    assert root.stop() == []


@pytest.mark.parametrize(
    ("tool", "tool_input", "named"),
    [
        ("mcp__slack__slack_send_message", {"channel_id": "C0B", "text": "_(Yasyf's Claude)_ On it"}, "`mcp__slack__slack_send_message`"),
        ("mcp__slack__slack_add_reaction", {"channel_id": "C0B", "timestamp": "1.2", "reaction": "eyes"}, "`mcp__slack__slack_add_reaction`"),
        ("mcp__slack__slack_remove_reaction", {"channel_id": "C0B", "timestamp": "1.2", "reaction": "eyes"}, "`mcp__slack__slack_remove_reaction`"),
        ("mcp__plugin_cc-slack_cc-slack__slack_reply", {"channel_id": "C0B", "thread_ts": "1.2", "text": "On it"}, "`mcp__plugin_cc-slack_cc-slack__slack_reply`"),
        ("mcp__plugin_cc-slack_cc-slack__slack_react", {"channel_id": "C0B", "ts": "1.2", "name": "eyes"}, "`mcp__plugin_cc-slack_cc-slack__slack_react`"),
        ("Bash", {"command": "~/.claude/plugins/cache/forge/cc-slack/0.2.11/bin/cc-slack react --url C0B/p1 --name eyes"}, "`cc-slack react`"),
        ("Bash", {"command": "cc-slack reply --url C0B/p1 --text 'On it' # root:raw"}, "`cc-slack reply`"),
    ],
)
def test_the_root_never_writes_to_slack(root: Root, tool: str, tool_input: dict, named: str) -> None:
    message = root.pre(tool, tool_input) or ""

    assert message.startswith(f"delegate to a lane: {root_context.SLACK_LANE}")
    assert named in message and "R20" in message


@pytest.mark.parametrize(
    "command",
    ["cc-slack dm-status --text 'parity wave 3 landed'", "cc-slack whoami", "cc-slack status"],
)
def test_the_root_keeps_its_own_dm_status_and_identity_checks(root: Root, command: str) -> None:
    assert root.bash(command) is None
