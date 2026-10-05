from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/long-running/scripts/inbox-watch.py"
SESSION = "11111111-2222-3333-4444-555555555555"

CCI = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_CCI/calls"
if [ -f "$FAKE_CCI/fail" ]; then echo "cci: store locked" >&2; exit 1; fi
if [ -f "$FAKE_CCI/queue" ]; then cat "$FAKE_CCI/queue"; rm "$FAKE_CCI/queue"; fi
"""


def stamp(seconds_ago: float) -> str:
    return datetime.fromtimestamp(time.time() - seconds_ago, UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class Inbox:
    def __init__(self, root: Path):
        self.root = root
        self.state = root / "state.json"
        self.pushes = root / "pushes.txt"
        self.home = root / "home"
        self.cci = root / "cci"
        self.cci.mkdir()
        bin_dir = root / "bin"
        bin_dir.mkdir()
        (bin_dir / "cci").write_text(CCI)
        (bin_dir / "cci").chmod(0o755)
        self.env = {**os.environ, "HOME": str(self.home), "FAKE_CCI": str(self.cci), "PATH": f"{bin_dir}:{os.environ['PATH']}"}
        push = root / "push"
        push.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" >> {self.pushes}\n')
        push.chmod(0o755)
        self.push_command = str(push)
        self.seq = 0

    def post(self, *records: tuple[str, str, str]) -> None:
        with (self.cci / "queue").open("a") as queue:
            for kind, lane, text in records:
                self.seq += 1
                queue.write(json.dumps({"seq": self.seq, "kind": kind, "lane": lane, "text": text, "to": ["root"], "refs": {}}) + "\n")

    def transcript(self, *events: dict) -> None:
        project = self.home / ".claude" / "projects" / "-drive"
        project.mkdir(parents=True, exist_ok=True)
        (project / f"{SESSION}.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))

    def run(self, *args: str) -> list[str]:
        result = subprocess.run(
            [str(SCRIPT), "--state", str(self.state), "--drive", "d1", "--timeout", "0", *args],
            env=self.env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.splitlines()

    def calls(self) -> list[str]:
        return (self.cci / "calls").read_text().splitlines()


@pytest.fixture
def inbox(tmp_path: Path) -> Inbox:
    return Inbox(tmp_path)


def test_each_record_prints_once_from_the_roots_cursor(inbox):
    inbox.post(("decide", "desk-runner", "DECIDE msg_54ab merge-walker-r2: INCIDENT fd-use1 network delete cut demeter"))
    assert inbox.run() == ["#1 DECIDE desk-runner: DECIDE msg_54ab merge-walker-r2: INCIDENT fd-use1 network delete cut demeter"]
    assert inbox.run() == []


def test_the_read_names_the_drive_cursor_reader_and_every_urgent_kind(inbox):
    inbox.run("--kind", "landed")
    assert inbox.calls() == [
        "tail --drive d1 --cursor root-watch --reader root --json --budget 16000 --kind=incident --kind=decide --kind=ask --kind=defect --kind=blocker --kind=landed"
    ]


def test_a_flood_keeps_every_urgent_record_and_folds_the_rest(inbox):
    inbox.post(*[("landed", "landing-sweep", f"#{n} LANDED") for n in range(20)], ("incident", "desk-runner", "one"), ("decide", "desk-runner", "two"))
    lines = inbox.run("--burst", "5")
    assert lines[:2] == ["#21 INCIDENT desk-runner: one", "#22 DECIDE desk-runner: two"]
    assert lines[2:5] == ["#1 LANDED landing-sweep: #0 LANDED", "#2 LANDED landing-sweep: #1 LANDED", "#3 LANDED landing-sweep: #2 LANDED"]
    assert lines[5].startswith("+17 more records this pass; read them with cci grep --drive d1")
    assert len(lines) == 6


def test_an_urgent_word_in_a_routine_record_is_urgent(inbox):
    inbox.post(("report", "merge-walker", "ESCALATION root/owner: the walk cut demeter"), *[("landed", "l", f"#{n}") for n in range(3)])
    lines = inbox.run("--burst", "1")
    assert lines == ["#1 REPORT merge-walker: ESCALATION root/owner: the walk cut demeter", "+3 more records this pass; read them with cci grep --drive d1 --since 1h"]


def test_a_failing_read_reports_once_per_streak(inbox):
    (inbox.cci / "fail").write_text("")
    assert inbox.run() == ["CCI-FAIL cci: store locked"]
    assert inbox.run() == []
    (inbox.cci / "fail").unlink()
    inbox.post(("ask", "lane", "which commit"))
    assert inbox.run() == ["#1 ASK lane: which commit"]
    (inbox.cci / "fail").write_text("")
    assert inbox.run() == ["CCI-FAIL cci: store locked"]


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


def test_an_urgent_record_behind_an_open_question_is_pushed_once(inbox):
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
    inbox.post(("decide", "desk-runner", "DECIDE msg_54ab merge-walker-r2: INCIDENT demeter"))
    lines = inbox.run(*args)
    assert lines == [
        "#1 DECIDE desk-runner: DECIDE msg_54ab merge-walker-r2: INCIDENT demeter",
        "PUSHED 1 urgent line(s) to the owner (root waiting on an open question)",
    ]
    pushed = inbox.pushes.read_text()
    assert "waiting on your answer in the terminal" in pushed
    assert "#1 DECIDE desk-runner: DECIDE msg_54ab merge-walker-r2: INCIDENT demeter" in pushed
    assert inbox.run(*args) == []
    assert inbox.pushes.read_text() == pushed


def test_a_root_that_took_a_turn_since_the_record_gets_no_push(inbox):
    args = ("--session", SESSION, "--push-after", "0", "--push-command", inbox.push_command)
    inbox.transcript({"type": "assistant", "timestamp": stamp(3600), "message": {"content": []}})
    inbox.run(*args)
    inbox.post(("decide", "desk-runner", "seen"))
    inbox.transcript({"type": "assistant", "timestamp": stamp(-60), "message": {"content": []}})
    assert inbox.run(*args) == ["#1 DECIDE desk-runner: seen"]
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
    inbox.post(("incident", "desk-runner", "new"))
    assert inbox.run(*args)[-1] == "PUSHED 1 urgent line(s) to the owner"
    assert "terminal" not in inbox.pushes.read_text()


def test_a_session_without_a_transcript_refuses_to_start(inbox):
    result = subprocess.run(
        [str(SCRIPT), "--state", str(inbox.state), "--drive", "d1", "--timeout", "0", "--session", SESSION],
        env=inbox.env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert f"no transcript for session {SESSION}" in result.stderr


def test_a_failed_push_is_retried_after_the_push_window(inbox):
    inbox.transcript({"type": "assistant", "timestamp": stamp(3600), "message": {"content": []}})
    failing = inbox.root / "fail"
    failing.write_text("#!/bin/sh\necho down >&2\nexit 1\n")
    failing.chmod(0o755)
    args = ("--session", SESSION, "--push-after", "0", "--push-command", str(failing))
    inbox.run(*args)
    inbox.post(("decide", "desk-runner", "x"))
    assert inbox.run(*args)[-1] == "PUSHED 1 urgent line(s) to the owner; the push failed: down"
    assert inbox.run(*args, "--push-command", inbox.push_command)[-1] == "PUSHED 1 urgent line(s) to the owner"


def test_one_oversized_record_costs_at_most_the_display_cap(inbox):
    inbox.post(("decide", "desk-runner", "z" * 1500))
    assert inbox.run() == ["#1 DECIDE desk-runner: " + "z" * 376 + "…"]


def test_a_mailbox_gaining_an_unread_message_prints_one_mailbox_line(inbox):
    inboxes_dir = inbox.root / "teams" / "session-1" / "inboxes"
    inboxes_dir.mkdir(parents=True)
    mailbox = inboxes_dir / "team-lead.json"
    mailbox.write_text(json.dumps([{"text": "old", "read": False}]))
    assert inbox.run(str(mailbox)) == []
    mailbox.write_text(json.dumps([{"text": "old", "read": False}, {"text": "seen", "read": True}]))
    assert inbox.run(str(mailbox)) == []
    mailbox.write_text(json.dumps([{"text": "old", "read": False}, {"text": "seen", "read": True}, {"text": "pick", "read": False}]))
    assert inbox.run(str(mailbox)) == ["MAILBOX 2 unread"]
    assert inbox.run(str(mailbox)) == []
