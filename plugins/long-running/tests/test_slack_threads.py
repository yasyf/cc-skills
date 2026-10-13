from __future__ import annotations

import json
import subprocess
from pathlib import Path

from captain_hook.context import HookContext
from captain_hook.events import PostToolUseEvent, UserPromptSubmitEvent
from captain_hook.snapshots.client import EvidenceIncomplete
from captain_hook.testing.helpers import build_context

from hooks import slack_threads
from hooks.slack_threads import CC_SLACK_REPLY, NOT_WATCHING, OWNER_LINK, WATCHING, register_posted_thread, watch_owner_links

SESSION = "900424b6-7393-480c-a26a-f1bd21da6e57"
AGENT = "aowner-links-comms-1a2b"
ON_WATCH = "C0AAAAAAAA1/1790901035.467689 is on the drive's Slack watch list at state"
FENCE = "~" * 12


def exhausted(self):
    raise EvidenceIncomplete("deadline", "foreground transcript deadline exhausted")


def test_a_lane_registers_its_post_after_the_transcript_deadline(tmp_path, monkeypatch):
    transcript = tmp_path / f"{SESSION}.jsonl"
    transcript.write_text("")
    meta = tmp_path / SESSION / "subagents" / f"agent-{AGENT}.meta.json"
    meta.parent.mkdir(parents=True)
    meta.write_text(json.dumps({"name": "owner-links-comms"}))
    raw = {
        "session_id": SESSION,
        "transcript_path": str(transcript),
        "agent_id": AGENT,
        "tool_name": "Bash",
        "tool_input": {"command": "cc-slack reply --channel C0AAAAAAAA1 --thread 1790901035.467689 --text hi"},
        "tool_response": {"stdout": CC_SLACK_REPLY, "stderr": ""},
        "cwd": str(tmp_path),
    }
    evt = PostToolUseEvent(_raw=raw, ctx=build_context(None, transcript, tmp_path, tmp_path))
    monkeypatch.setattr(HookContext, "t", property(exhausted))
    monkeypatch.delenv("CLAUDE_LONG_RUNNING_DRIVE", raising=False)
    monkeypatch.delenv("CLAUDE_LONG_RUNNING_LANE", raising=False)
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(slack_threads.subprocess, "run", run)

    assert register_posted_thread(evt) is None
    assert [argv[argv.index("--lane") + 1 :] for argv in calls] == [
        [SESSION, "--channel", "C0AAAAAAAA1", "--thread-ts", "1790901035.467689", "--posted-ts", "1790901997.422529"]
    ]


def owner_says(tmp_path: Path, prompt: str) -> UserPromptSubmitEvent:
    raw = {"session_id": SESSION, "prompt": prompt, "cwd": str(tmp_path)}
    return UserPromptSubmitEvent(_raw=raw, ctx=build_context(session_dir=tmp_path))


def drive_py(monkeypatch, returncode: int = 0, stdout: str = "", stderr: str = "") -> list[list[str]]:
    monkeypatch.delenv("CLAUDE_LONG_RUNNING_DRIVE", raising=False)
    calls = []

    def run(argv, **kwargs):
        calls.append(argv[argv.index("watch") :])
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(slack_threads.subprocess, "run", run)
    return calls


def test_an_owner_link_goes_on_the_watch_under_its_thread_root(tmp_path, monkeypatch):
    calls = drive_py(monkeypatch, stdout=f"{ON_WATCH}\n")

    result = watch_owner_links(owner_says(tmp_path, OWNER_LINK))

    assert calls == [
        ["watch", "--session", SESSION, "--lane", "owner", "--channel", "C0AAAAAAAA1", "--message", "p1790901997422529", "--thread-ts", "1790901035.467689"]
    ]
    assert result.message == "\n".join([WATCHING, FENCE, ON_WATCH, FENCE])


def test_every_link_in_an_owner_prompt_is_added_once(tmp_path, monkeypatch):
    calls = drive_py(monkeypatch)
    prompt = f"{OWNER_LINK} again {OWNER_LINK}, C0BBBBBBBB2 p1790875497353829, C0BBBBBBBB2/1790875497.353830 and https://example.slack.com/archives/C0CCCCCCCC3."

    assert watch_owner_links(owner_says(tmp_path, prompt)) is None

    assert [call[call.index("--channel") :] for call in calls] == [
        ["--channel", "C0AAAAAAAA1", "--message", "p1790901997422529", "--thread-ts", "1790901035.467689"],
        ["--channel", "C0CCCCCCCC3"],
        ["--channel", "C0BBBBBBBB2", "--message", "p1790875497353829"],
        ["--channel", "C0BBBBBBBB2", "--message", "1790875497.353830"],
    ]


def test_an_orca_worker_root_names_its_drive(tmp_path, monkeypatch):
    calls = drive_py(monkeypatch)
    monkeypatch.setenv("CLAUDE_LONG_RUNNING_DRIVE", "900424b6")

    watch_owner_links(owner_says(tmp_path, OWNER_LINK))

    assert calls[0][: calls[0].index("--channel")] == ["watch", "--session", SESSION, "--lane", "owner", "--drive", "900424b6"]


def test_a_link_that_did_not_reach_the_watch_names_the_command_to_rerun(tmp_path, monkeypatch):
    calls = drive_py(monkeypatch, returncode=1, stderr=f"no drive claims session {SESSION}\n")

    result = watch_owner_links(owner_says(tmp_path, "look at C0BBBBBBBB2 p1790875497353829"))

    notice, opened, reason, retry, closed = result.message.splitlines()
    assert (notice, opened, reason, closed) == (NOT_WATCHING, FENCE, f"no drive claims session {SESSION}", FENCE)
    assert retry.endswith(f" watch --session {SESSION} --lane owner --channel C0BBBBBBBB2 --message p1790875497353829")
    assert len(calls) == 1


def test_a_prompt_with_no_slack_link_runs_nothing(tmp_path, monkeypatch):
    calls = drive_py(monkeypatch, stdout=f"{ON_WATCH}\n")

    assert watch_owner_links(owner_says(tmp_path, "post in C0AAAAAAAA1 when https://github.com/example/repo/pull/1790901997422529 lands")) is None

    assert calls == []


def test_an_owner_link_lands_in_the_drives_watch_list_once(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("CLAUDE_LONG_RUNNING_DRIVE", raising=False)
    drives = tmp_path / "long-running" / "drives"
    drives.mkdir(parents=True)
    (drives / "900424b6.json").write_text(json.dumps({"drive": "900424b6", "sessions": [SESSION], "state_dir": str(tmp_path / "release-v3")}))
    watched = tmp_path / "release-v3" / "slack" / "watched-threads.jsonl"

    result = watch_owner_links(owner_says(tmp_path, OWNER_LINK))

    [row] = [json.loads(line) for line in watched.read_text().splitlines()]
    assert (row["channel"], row["thread_ts"], row["linked_ts"], row["lane"]) == ("C0AAAAAAAA1", "1790901035.467689", "1790901997.422529", "owner")
    assert result.message.splitlines()[2] == f"C0AAAAAAAA1/1790901035.467689 is on the drive's Slack watch list at {watched}"
    assert watch_owner_links(owner_says(tmp_path, OWNER_LINK)) is None
