from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import inboxes
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/long-running/scripts/inbox-watch.py"
SESSION = "11111111-2222-3333-4444-555555555555"


def stamp(seconds_ago: float) -> str:
    return datetime.fromtimestamp(time.time() - seconds_ago, UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class Inbox:
    def __init__(self, root: Path):
        self.root = root
        self.runner = root / "runner.md"
        self.deploy = root / "deploy-go.md"
        self.runner.write_text("15:00 DECIDE old question already handled\n")
        self.deploy.write_text("- walker (2:59 PM PT) PASSED old\n")
        self.state = root / "state.json"
        self.pushes = root / "pushes.txt"
        self.home = root / "home"
        push = root / "push"
        push.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" >> {self.pushes}\n')
        push.chmod(0o755)
        self.push_command = str(push)

    def append(self, path: Path, *lines: str) -> None:
        with path.open("a") as handle:
            handle.writelines(f"{line}\n" for line in lines)

    def transcript(self, *events: dict) -> None:
        project = self.home / ".claude" / "projects" / "-drive"
        project.mkdir(parents=True, exist_ok=True)
        (project / f"{SESSION}.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))

    def run(self, *args: str) -> list[str]:
        result = subprocess.run(
            [str(SCRIPT), "--state", str(self.state), "--timeout", "0", *args, str(self.runner), str(self.deploy)],
            env={**os.environ, "HOME": str(self.home)},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.splitlines()


@pytest.fixture
def inbox(tmp_path: Path) -> Inbox:
    return Inbox(tmp_path)


def test_a_first_run_starts_at_the_end_so_history_never_replays(inbox):
    assert inbox.run() == []
    inbox.append(inbox.runner, "15:13 DECIDE msg_54ab merge-walker-r2: INCIDENT fd-use1 network delete cut demeter")
    assert inbox.run() == ["runner.md: 15:13 DECIDE msg_54ab merge-walker-r2: INCIDENT fd-use1 network delete cut demeter"]


def test_a_rearmed_watch_prints_what_landed_while_it_was_down_exactly_once(inbox):
    inbox.run()
    inbox.append(inbox.deploy, "- merge-walker (r2, 3:13 PM PT) ESCALATION root/owner: the walk cut demeter")
    inbox.append(inbox.runner, "15:14 LAUNCHED R1 lane")
    assert inbox.run() == ["deploy-go.md: - merge-walker (r2, 3:13 PM PT) ESCALATION root/owner: the walk cut demeter"]
    assert inbox.run() == []


def test_urgent_classes_always_match_and_match_adds_more(inbox):
    inbox.run()
    inbox.append(
        inbox.deploy,
        "URGENT slack-releases-e2e: monitor fell inside the watch",
        "INCIDENT (4:3x PM PT) Datadog monitor 312516332 ALERT",
        "- lane ASK root: which commit",
        "orca-desk: alert alerts-runs-1704 https://x :: page",
        "- landing-sweep-2 (3:47 PM PT) #29796 LANDED 89429c30e5",
        "routine chatter",
    )
    assert inbox.run("--match", "LANDED") == [
        "deploy-go.md: URGENT slack-releases-e2e: monitor fell inside the watch",
        "deploy-go.md: INCIDENT (4:3x PM PT) Datadog monitor 312516332 ALERT",
        "deploy-go.md: - lane ASK root: which commit",
        "deploy-go.md: - landing-sweep-2 (3:47 PM PT) #29796 LANDED 89429c30e5",
    ]


def test_a_partial_line_waits_for_its_newline(inbox):
    inbox.run()
    with inbox.runner.open("a") as handle:
        handle.write("15:20 DECIDE half")
    assert inbox.run() == []
    inbox.append(inbox.runner, " written")
    assert inbox.run() == ["runner.md: 15:20 DECIDE half written"]


def test_a_shrunk_file_resets_to_its_end_without_replaying(inbox):
    inbox.run()
    inbox.runner.write_text("DECIDE\n")
    assert inbox.run() == ["RESET runner.md: shrank from 42 to 7 bytes; resuming at its end"]
    assert inbox.run() == []


def test_a_flood_keeps_every_urgent_line_and_folds_the_rest(inbox):
    inbox.run()
    inbox.append(inbox.deploy, *[f"#{n} LANDED" for n in range(20)], "INCIDENT one", "DECIDE two")
    lines = inbox.run("--match", "LANDED", "--burst", "5")
    assert lines[:2] == ["deploy-go.md: INCIDENT one", "deploy-go.md: DECIDE two"]
    assert lines[2:5] == ["deploy-go.md: #0 LANDED", "deploy-go.md: #1 LANDED", "deploy-go.md: #2 LANDED"]
    assert lines[5].startswith("+17 more matching lines this pass")
    assert len(lines) == 6


def test_a_stale_heartbeat_reports_once_per_streak(inbox):
    beat = inbox.root / "slack-watch.beat"
    beat.write_text("")
    old = time.time() - 3600
    os.utime(beat, (old, old))
    spec = f"incident-slack-watch={beat}:600"
    first = inbox.run("--heartbeat", spec)
    assert len(first) == 1 and first[0].startswith(f"WATCH-STALE incident-slack-watch: {beat} last written")
    assert inbox.run("--heartbeat", spec) == []
    beat.touch()
    assert inbox.run("--heartbeat", spec)[0].startswith(f"WATCH-LIVE incident-slack-watch: {beat} written")
    assert inbox.run("--heartbeat", spec) == []


def test_an_urgent_line_behind_an_open_question_is_pushed_once(inbox):
    inbox.transcript(
        {"type": "assistant", "timestamp": stamp(1500), "message": {"content": [{"type": "text", "text": "working"}]}},
        {
            "type": "assistant",
            "timestamp": stamp(1200),
            "message": {"content": [{"type": "tool_use", "id": "ask1", "name": "AskUserQuestion", "input": {}}]},
        },
        {"type": "queue-operation", "timestamp": stamp(60)},
    )
    args = ("--session", SESSION, "--push-after", "0", "--push-command", inbox.push_command)
    inbox.run(*args)
    inbox.append(inbox.runner, "15:13 DECIDE msg_54ab merge-walker-r2: INCIDENT demeter")
    lines = inbox.run(*args)
    assert lines == [
        "runner.md: 15:13 DECIDE msg_54ab merge-walker-r2: INCIDENT demeter",
        "PUSHED 1 urgent line(s) to the owner (root waiting on an open question)",
    ]
    pushed = inbox.pushes.read_text()
    assert "waiting on your answer in the terminal" in pushed
    assert "runner.md: 15:13 DECIDE msg_54ab merge-walker-r2: INCIDENT demeter" in pushed
    assert inbox.run(*args) == []
    assert inbox.pushes.read_text() == pushed


def test_a_root_that_took_a_turn_since_the_line_gets_no_push(inbox):
    args = ("--session", SESSION, "--push-after", "0", "--push-command", inbox.push_command)
    inbox.transcript({"type": "assistant", "timestamp": stamp(3600), "message": {"content": []}})
    inbox.run(*args)
    inbox.append(inbox.runner, "DECIDE seen")
    inbox.transcript({"type": "assistant", "timestamp": stamp(-60), "message": {"content": []}})
    assert inbox.run(*args) == ["runner.md: DECIDE seen"]
    assert not inbox.pushes.exists()


def test_an_answered_question_is_not_named(inbox):
    inbox.transcript(
        {
            "type": "assistant",
            "timestamp": stamp(1200),
            "message": {"content": [{"type": "tool_use", "id": "ask1", "name": "AskUserQuestion", "input": {}}]},
        },
        {"type": "user", "timestamp": stamp(1100), "message": {"content": [{"type": "tool_result", "tool_use_id": "ask1"}]}},
        {"type": "assistant", "timestamp": stamp(1000), "message": {"content": []}},
    )
    args = ("--session", SESSION, "--push-after", "0", "--push-command", inbox.push_command)
    inbox.run(*args)
    inbox.append(inbox.runner, "INCIDENT new")
    assert inbox.run(*args)[-1] == "PUSHED 1 urgent line(s) to the owner"
    assert "terminal" not in inbox.pushes.read_text()


def test_a_session_without_a_transcript_refuses_to_start(inbox):
    result = subprocess.run(
        [str(SCRIPT), "--state", str(inbox.state), "--timeout", "0", "--session", SESSION, str(inbox.runner)],
        env={**os.environ, "HOME": str(inbox.home)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert f"no transcript for session {SESSION}" in result.stderr


def test_a_file_created_after_the_first_run_is_read_from_its_start(inbox):
    late = inbox.root / "orca-desk.md"
    result = subprocess.run(
        [str(SCRIPT), "--state", str(inbox.state), "--timeout", "0", str(late)], capture_output=True, text=True, env={**os.environ, "HOME": str(inbox.home)}
    )
    assert result.stdout == ""
    inbox.append(late, "INCIDENT first line of a new file")
    result = subprocess.run(
        [str(SCRIPT), "--state", str(inbox.state), "--timeout", "0", str(late)], capture_output=True, text=True, env={**os.environ, "HOME": str(inbox.home)}
    )
    assert result.stdout.splitlines() == ["orca-desk.md: INCIDENT first line of a new file"]


def test_an_urgent_word_past_the_width_still_counts_as_urgent(inbox):
    inbox.run()
    inbox.append(inbox.deploy, "x" * 50 + " INCIDENT late", *[f"#{n} LANDED" for n in range(5)])
    lines = inbox.run("--match", "LANDED", "--width", "10", "--burst", "1")
    assert lines[0] == "deploy-go.md: " + "x" * 9 + "…"
    assert lines[1].startswith("+5 more matching lines")


def test_a_failed_push_is_retried_after_the_push_window(inbox):
    inbox.transcript({"type": "assistant", "timestamp": stamp(3600), "message": {"content": []}})
    failing = inbox.root / "fail"
    failing.write_text("#!/bin/sh\necho down >&2\nexit 1\n")
    failing.chmod(0o755)
    args = ("--session", SESSION, "--push-after", "0", "--push-command", str(failing))
    inbox.run(*args)
    inbox.append(inbox.runner, "DECIDE x")
    assert inbox.run(*args)[-1] == "PUSHED 1 urgent line(s) to the owner; the push failed: down"
    assert inbox.run(*args, "--push-command", inbox.push_command)[-1] == "PUSHED 1 urgent line(s) to the owner"


def test_a_rotated_inbox_resumes_at_the_next_line_without_a_reset(inbox):
    inbox.run()
    inbox.append(inbox.runner, "15:30 DECIDE one")
    assert inbox.run() == ["runner.md: 15:30 DECIDE one"]
    rotation = inboxes.Inbox(inbox.runner)
    rotation.rotate(time.time() - 7 * 3600)
    inbox.append(inbox.runner, "15:31 DECIDE two")
    inode = inbox.runner.stat().st_ino

    rotation.rotate(time.time())

    assert inbox.runner.stat().st_ino != inode
    assert inbox.runner.read_text() == "15:31 DECIDE two\n"
    assert inbox.run() == ["runner.md: 15:31 DECIDE two"]
    inbox.append(inbox.runner, "15:32 DECIDE three")
    assert inbox.run() == ["runner.md: 15:32 DECIDE three"]


def test_one_oversized_line_costs_at_most_the_display_cap(inbox):
    inbox.run()
    inbox.append(inbox.deploy, "DECIDE " + "z" * 1500)
    assert inbox.run() == ["deploy-go.md: DECIDE " + "z" * 392 + "…"]
