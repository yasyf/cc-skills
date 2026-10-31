"""prompt_watch.py against captured Orca output on a fake clock: every Orca read, cci post, and cc-slack card is recorded on ``shell.calls``."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from long_running import drive
import prompt_watch
import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "prompt_watch"
DRIVE = "900424b6"
ROOT = "term_26c4ee5e-61f3-4fac-86aa-40bbdbc87c4e"
ROOT_SESSION = "900424b6-7393-480c-a26a-f1bd21da6e57"
SUPERVISOR = "term_7f2d70f5-ca5c-4260-ab60-129bfd2e6ec2"
LOCAL = "ctx_25f8965ea0fb"
LOCAL_TERMINAL = "term_cfce7ff3-0976-4032-9d55-22697f68d6ee"
REMOTE = "ctx_1e2e970fc485"
REMOTE_TERMINAL = "term_5cfcad9a-6928-47d6-88e3-f04749533661"
GONE = "ctx_726698aaa803"
ENVIRONMENT = "codex-hooks-proof"
RUN = "run_4104a86f0a03"
READ_ONLY = {("terminal", "show"), ("terminal", "read"), ("terminal", "wait"), ("orchestration", "worker-list"), ("orchestration", "worker-show")}
TUI_IDLE = ("terminal", "wait", "--terminal", SUPERVISOR, "--for", "tui-idle", "--timeout-ms", "1000")
DIALOG_WORDS = ("runs rm", "Do you want to proceed", "Kick it off", "Would you like to run", "rm -rf")
HOOK_TITLE = "Bash command · from the startup-speed agent"
ASKED = "Bash command · from the startup-speed agent / Time the current single-stream fetch with its SHA-256 check on the measurement Sprite as a same-host control / This shell -c script runs rm and could not be checked / Do you want to proceed? / ❯ 1. Yes / 2. No"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def show(terminal: str, environment: str = "") -> tuple[str, ...]:
    return ("terminal", "show", "--terminal", terminal, *(("--environment", environment) if environment else ()))


def read(terminal: str, environment: str = "") -> tuple[str, ...]:
    return ("terminal", "read", "--terminal", terminal, "--screen", *(("--environment", environment) if environment else ()))


def worker_show(dispatch: str) -> tuple[str, ...]:
    return ("orchestration", "worker-show", "--dispatch", dispatch)


def key(terminal: str, text: str, environment: str = "") -> tuple[str, ...]:
    return ("--terminal", terminal, "--text", text, *(("--environment", environment) if environment else ()))


class FakeShell(prompt_watch.Shell):
    def __init__(self):
        self.clock = datetime(2026, 10, 10, 10, 24, tzinfo=timezone.utc)
        self.orca: dict[tuple[str, ...], dict] = {}
        self.calls: list[list[str]] = []
        self.envs: dict[tuple[str, ...], dict | None] = {}
        self.posts: list[dict] = []
        self.refuse_posts = False
        self.cards: list[dict] = []
        self.refuse_cards = False
        self.sends: list[list[str]] = []
        self.send_reply = fixture("terminal-send.turn-started")
        self.keys: list[tuple[str, ...]] = []
        self.on_keys: dict[tuple[str, ...], dict] = {}
        self.mail: list[dict] = []

    def run(self, argv, env=None):
        self.calls.append(list(argv))
        if argv[:3] == ["orca", "terminal", "send"] and "--enter" not in argv:
            assert argv[-1] == "--json", argv
            self.keys.append(tuple(argv[3:-1]))
            self.orca |= self.on_keys
            self.on_keys = {}
            return prompt_watch.Done(0, json.dumps(fixture("terminal-send.accepted")), "")
        if argv[:3] == ["orca", "orchestration", "send"]:
            assert argv[-1] == "--json", argv
            self.mail.append(dict(zip(argv[3:-1:2], argv[4:-1:2], strict=True)))
            return prompt_watch.Done(0, json.dumps({"ok": True, "result": {"message": {"id": f"msg_{len(self.mail)}"}}}), "")
        if argv[:3] == ["orca", "terminal", "send"]:
            assert argv[3:5] == ["--terminal", SUPERVISOR] and argv[5] == "--text" and argv[7:] == ["--enter", "--wait-submit", "5", "--json"], argv
            self.sends.append(argv)
            return prompt_watch.Done(0 if self.send_reply["ok"] else 1, json.dumps(self.send_reply), "")
        if argv[0] == "orca":
            assert tuple(argv[1:3]) in READ_ONLY and argv[-1] == "--json", argv
            key = tuple(argv[1:-1])
            self.envs[key] = env
            reply = self.orca[key]
            return prompt_watch.Done(0 if reply["ok"] else 1, json.dumps(reply), "")
        if argv[:2] == ["cc-slack", "dm-status"]:
            assert argv[2:4] == ["--session", ROOT_SESSION] and argv[4] == "--card" and len(argv) == 6, argv
            if self.refuse_cards:
                return prompt_watch.Done(1, "", "cc-slack: no Slack identity")
            self.cards.append(json.loads(argv[5]))
            return prompt_watch.Done(0, "", "")
        assert argv[:2] == ["cci", "post"], argv
        if self.refuse_posts:
            return prompt_watch.Done(1, "", "cci: store locked")
        rest = [token for token in argv[2:] if token != "--json"]
        flags = dict(zip(rest[::2], rest[1::2], strict=True)) | {"seq": 45600 + len(self.posts)}
        self.posts.append(flags)
        return prompt_watch.Done(0, json.dumps({"seq": flags["seq"]}), "")

    def now(self):
        return self.clock

    def sleep(self, seconds):
        self.clock += timedelta(seconds=seconds)

    def reads(self) -> list[list[str]]:
        return [call for call in self.calls if call[:3] == ["orca", "terminal", "read"]]

    def to(self, lane: str) -> list[dict]:
        return [post for post in self.posts if post.get("--to") == lane]

    def kinds(self, topic: str) -> list[str]:
        return [post["--kind"] for post in self.posts if post["--topic"] == f"prompt:{topic}"]


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    return tmp_path


@pytest.fixture
def shell() -> FakeShell:
    return FakeShell()


def register(home: Path, **fields) -> dict:
    entry = {
        "drive": DRIVE,
        "sessions": ["2a1f0c9e-5d4b-4e3a-9c8b-7f6e5d4c3b2a", ROOT_SESSION],
        "orca_run": None,
        "started_at": "2026-10-10T07:43:54Z",
        "ledger": "48e3662f044120a992336bb63755fb9fe870fbf7",
        "repo": "acme/app",
        "git_common_dir": "/Users/dev/work/app/.git",
        "checkout": "/Users/dev/work/app",
        "state_dir": str(home / "scratch" / "release-v3"),
        "cci_drive": None,
        "root_terminal": ROOT,
        "slack_cards": True,
    } | fields
    drive.save(entry)
    return entry


def supervised(home: Path, **fields) -> dict:
    return register(home, desks={"codex-supervisor": {"terminal": SUPERVISOR, "environment": None, "supervisor": True}}, **fields)


def unbound(shell: FakeShell) -> None:
    shell.orca[("orchestration", "worker-list", "--include-remote")] = {"ok": True, "result": {"workers": [], "page": {"hasMore": False}, "scope": {"run": None, "source": "all"}}}


def coordinator(shell: FakeShell, state: str, screen: str) -> None:
    shell.orca[show(ROOT)] = fixture(f"terminal-show.coordinator.{state}")
    shell.orca[read(ROOT)] = fixture(f"screen.{screen}")


def supervisor(shell: FakeShell, screen: str = "supervisor.codex-busy", idle: bool = False) -> None:
    shell.orca[show(SUPERVISOR)] = fixture("terminal-show.coordinator.clear")
    shell.orca[read(SUPERVISOR)] = fixture(f"screen.{screen}")
    shell.orca[TUI_IDLE] = fixture("terminal-wait.idle" if idle else "error.timeout")


def poll(shell: FakeShell, times: int = 1) -> dict:
    for _ in range(times):
        assert prompt_watch.main(["run", "--drive", DRIVE, "--once"], shell) == 0
        shell.sleep(prompt_watch.POLL_SECONDS)
    return state()


def state() -> dict:
    return json.loads((prompt_watch.watch_dir(drive.find(DRIVE, None)) / prompt_watch.STATE_FILE).read_text())["subjects"]


def whole(post: dict) -> str:
    """The record's whole line: pytest's long paths push it past cci's limit, so it rides as the --path body."""
    assert len(post["--text"]) <= 400
    return Path(post["--path"]).read_text().rstrip("\n") if "--path" in post else post["--text"]


def notice(name: str, role: str, waits: str, title: str, capture: str, read_it: str, tail: str) -> str:
    return f"PROMPT {name} ({role}) waits on {waits} ({title}). {tail} Screen: {capture}. Read: {read_it}."


def answer_line(target: str, episode: str, by: str = "root") -> str:
    return f"Answer: prompt_watch.py answer {target} --episode {episode} --by {by} yes|no."


def shown(shell: FakeShell, capsys) -> list[str]:
    capsys.readouterr()
    assert prompt_watch.main(["show", "--drive", DRIVE], shell) == 0
    return capsys.readouterr().out.splitlines()


def test_a_hook_approval_on_the_coordinator_goes_once_to_the_supervisor_and_never_to_the_owners_card(home, shell):
    supervised(home)
    unbound(shell)
    coordinator(shell, "waiting", "coordinator.approval")
    supervisor(shell)

    rows = poll(shell, 3)

    assert rows[ROOT]["state"] == "approval" and rows[ROOT]["since"] == 1791627768779
    [post] = shell.posts
    assert post["--to"] == "codex-supervisor" and post["--kind"] == "blocker" and post["--lane"] == "prompt-watch" and post["--topic"] == "prompt:coordinator"
    capture = Path(rows[ROOT]["capture"])
    assert whole(post) == notice(
        "coordinator",
        "coordinator",
        "an approval dialog",
        HOOK_TITLE,
        str(capture),
        f"orca terminal read --terminal {ROOT} --screen",
        f"Answer only within what the owner already authorized, else ask the owner. {answer_line(f'--terminal {ROOT}', rows[ROOT]['episode'], 'codex-supervisor')}",
    )
    assert rows[ROOT]["detail"] == f"screen saved at {capture}; supervisor is busy"
    assert shell.sends == [] and shell.cards == [] and "wake" not in rows[ROOT]


def test_the_dialogs_text_stays_in_an_owner_only_file_under_the_drives_state_directory(home, shell, capsys):
    supervised(home)
    unbound(shell)
    coordinator(shell, "waiting", "coordinator.approval")
    supervisor(shell, "managed-server.working", idle=True)

    rows = poll(shell, 11)

    capture = Path(rows[ROOT]["capture"])
    saved = json.loads(capture.read_text())
    assert capture.parent == home / "scratch" / "release-v3" / "prompt-watch" / "screens"
    assert capture.stat().st_mode & 0o777 == 0o600 and capture.parent.stat().st_mode & 0o777 == 0o700
    assert saved["asked"] == ASKED and " ❯ 1. Yes" in saved["tail"] and saved["terminal"] == ROOT and saved["state"] == "approval"
    everything_else = [whole(post) for post in shell.posts] + [call[6] for call in shell.sends] + [json.dumps(card) for card in shell.cards] + shown(shell, capsys) + [json.dumps(rows)]
    assert [post["--to"] for post in shell.posts] == ["codex-supervisor"] and len(shell.cards) == 1 and len(shell.sends) == 1
    assert not [text for text in everything_else for words in DIALOG_WORDS if words in text]


def test_a_drive_started_without_slack_cards_posts_no_card_for_the_coordinators_prompt(home, shell):
    register(home, slack_cards=False)
    unbound(shell)
    coordinator(shell, "waiting", "coordinator.approval")

    rows = poll(shell, 2)

    assert (shell.cards, shell.posts, rows[ROOT]["alerts"]) == ([], [], {})


def test_with_no_supervisor_the_coordinators_prompt_is_one_needs_you_card_in_the_newest_root_sessions_status_thread(home, shell):
    register(home)
    unbound(shell)
    coordinator(shell, "waiting", "coordinator.approval")

    rows = poll(shell, 2)

    assert shell.posts == []
    [card] = shell.cards
    assert (card["status"], card["title"], card["drive"]) == ("needs_you", "The coordinator terminal waits on an approval dialog", "release-v3")
    assert card["body"] == notice(
        "coordinator",
        "coordinator",
        "an approval dialog",
        HOOK_TITLE,
        rows[ROOT]["capture"],
        f"orca terminal read --terminal {ROOT} --screen",
        "It froze the coordinator; no supervisor desk is registered. Answer it in that terminal.",
    )


def test_the_owners_card_is_followed_by_one_info_card_once_the_prompt_clears(home, shell):
    register(home)
    unbound(shell)
    coordinator(shell, "waiting", "coordinator.approval")
    poll(shell)
    shell.orca[read(ROOT)] = fixture("screen.coordinator.answered")

    rows = poll(shell, 3)

    assert shell.posts == [] and rows[ROOT]["alerts"] == {}
    assert [(card["status"], card["title"]) for card in shell.cards] == [
        ("needs_you", "The coordinator terminal waits on an approval dialog"),
        ("info", "The coordinator terminal no longer waits on a prompt"),
    ]
    assert shell.cards[1]["body"] == f"CLEARED coordinator (coordinator) terminal={ROOT}: the prompt left the screen"


@pytest.mark.parametrize(
    ("show_reply", "read_reply", "why"),
    [
        ("terminal-show.coordinator.waiting", "screen.coordinator.question", "supervisor codex-supervisor is question"),
        ("error.remote-runtime-unavailable", None, "supervisor codex-supervisor is unreachable"),
        ("error.terminal-handle-stale", None, "supervisor codex-supervisor is unknown"),
    ],
)
def test_a_supervisor_that_cannot_act_sends_the_coordinators_prompt_to_the_owner(home, shell, show_reply, read_reply, why):
    supervised(home)
    unbound(shell)
    coordinator(shell, "waiting", "coordinator.approval")
    shell.orca[show(SUPERVISOR)] = fixture(show_reply)
    if read_reply:
        shell.orca[read(SUPERVISOR)] = fixture(read_reply)

    poll(shell)

    [card] = shell.cards
    assert card["status"] == "needs_you" and f" It froze the coordinator; {why}. Answer it in that terminal. Screen: " in card["body"]
    assert shell.to("codex-supervisor") == [] and shell.to("owner") == []


def test_a_prompt_still_open_five_minutes_after_the_supervisor_heard_becomes_one_owner_card(home, shell):
    supervised(home)
    unbound(shell)
    coordinator(shell, "waiting", "coordinator.approval")
    supervisor(shell)

    poll(shell, 9)
    assert [post["--to"] for post in shell.posts] == ["codex-supervisor"] and shell.cards == []
    poll(shell, 4)

    assert [post["--to"] for post in shell.posts] == ["codex-supervisor"]
    [card] = shell.cards
    assert " It froze the coordinator; supervisor codex-supervisor stayed busy for 5m. Answer it in that terminal. Screen: " in card["body"]
    assert shell.sends == []


@pytest.mark.parametrize(
    ("screen", "receipt", "request_id"),
    [
        ("managed-server.working", "terminal-send.codex-pilot", "82f8014f-c164-4856-b77b-bac9e2338ae4"),
        ("supervisor.claude-idle", "terminal-send.turn-started", "02c7edf3-c3d1-4ec3-b3ed-c1d125c8be82"),
    ],
)
def test_an_idle_supervisor_gets_the_records_line_typed_once_into_its_own_terminal(home, shell, screen, receipt, request_id):
    supervised(home)
    unbound(shell)
    coordinator(shell, "waiting", "coordinator.approval")
    supervisor(shell, screen, idle=True)
    shell.send_reply = fixture(receipt)

    rows = poll(shell, 3)

    [post] = shell.posts
    [sent] = shell.sends
    assert sent[6] == whole(post) and "\n" not in sent[6]
    assert rows[ROOT]["wake"] | {"at": ""} == {"at": "", "request": request_id, "stages": ["input_accepted", "turn_started"], "reached": "turn_started"}
    assert rows[ROOT]["detail"].endswith("; supervisor got one line and its turn started")
    poll(shell, 8)
    assert [post["--to"] for post in shell.posts] == ["codex-supervisor"] and len(shell.cards) == 1 and len(shell.sends) == 1
    assert "supervisor codex-supervisor got the line 5m ago, its turn started, and it is still open" in shell.cards[0]["body"]


def test_a_line_orca_accepted_with_no_turn_start_is_kept_as_accepted_and_never_typed_again(home, shell):
    supervised(home)
    unbound(shell)
    coordinator(shell, "waiting", "coordinator.approval")
    supervisor(shell, "managed-server.working", idle=True)
    shell.send_reply = fixture("terminal-send.accepted")

    rows = poll(shell, 4)

    assert rows[ROOT]["wake"]["reached"] == "input_accepted" and rows[ROOT]["wake"]["stages"] == ["input_accepted"] and len(shell.sends) == 1
    assert rows[ROOT]["detail"].endswith("; supervisor got one line and Orca accepted it and saw no turn start")
    assert [post["--to"] for post in shell.posts] == ["codex-supervisor"]


def test_a_working_claude_supervisor_reads_busy_and_gets_no_line_even_when_orca_calls_it_idle(home, shell):
    supervised(home)
    unbound(shell)
    coordinator(shell, "waiting", "coordinator.approval")
    supervisor(shell, "coordinator.answered", idle=True)

    rows = poll(shell, 2)

    assert shell.sends == [] and rows[ROOT]["supervisor_input"] == "busy"


def test_a_busy_supervisor_is_woken_on_the_first_poll_that_finds_it_idle(home, shell):
    supervised(home)
    unbound(shell)
    coordinator(shell, "waiting", "coordinator.approval")
    supervisor(shell)
    poll(shell, 2)
    assert shell.sends == []
    shell.orca[TUI_IDLE] = fixture("terminal-wait.idle")
    poll(shell)
    assert shell.sends == []
    shell.orca[read(SUPERVISOR)] = fixture("screen.managed-server.working")

    poll(shell, 2)

    assert len(shell.sends) == 1 and len(shell.posts) == 1 and "supervisor_input" not in state()[ROOT]


@pytest.mark.parametrize(
    ("reply", "failure"),
    [
        ("error.terminal-handle-stale", "terminal_handle_stale: terminal_handle_stale"),
        ("terminal-send.refused", "input_refused: Orca gave no reason"),
    ],
)
def test_a_wake_orca_refuses_is_an_owner_card_at_once_and_is_not_typed_again(home, shell, reply, failure):
    supervised(home)
    unbound(shell)
    coordinator(shell, "waiting", "coordinator.approval")
    supervisor(shell, "managed-server.working", idle=True)
    shell.send_reply = fixture(reply)

    rows = poll(shell, 3)

    assert rows[ROOT]["wake"] | {"at": ""} == {"at": "", "error": failure}
    assert [post["--to"] for post in shell.posts] == ["codex-supervisor"] and len(shell.cards) == 1 and len(shell.sends) == 1
    assert f"the wake to supervisor codex-supervisor failed with {failure}" in shell.cards[0]["body"]


def test_no_line_is_ever_typed_into_a_terminal_that_waits_or_for_a_workers_prompt(home, shell):
    workers(home, shell, "waiting")
    shell.orca[read(LOCAL_TERMINAL)] = fixture("screen.coordinator.approval")
    shell.orca[show(ROOT)] = fixture("terminal-show.coordinator.waiting")
    shell.orca[read(ROOT)] = fixture("screen.coordinator.approval")

    poll(shell, 3)

    assert shell.sends == []
    assert [call for call in shell.calls if call[0] == "orca" and tuple(call[1:3]) not in READ_ONLY] == []


def test_an_answered_prompt_reads_stale_and_resolves_what_it_raised(home, shell, capsys):
    supervised(home)
    unbound(shell)
    coordinator(shell, "waiting", "coordinator.approval")
    supervisor(shell)
    poll(shell)
    shell.orca[read(ROOT)] = fixture("screen.coordinator.answered")

    rows = poll(shell, 2)

    assert rows[ROOT]["state"] == "stale" and rows[ROOT]["alerts"] == {}
    raised, cleared = shell.posts
    assert cleared["--kind"] == "unblock" and cleared["--resolves-seq"] == str(raised["seq"]) and "--to" not in cleared
    assert cleared["--text"] == f"CLEARED coordinator (coordinator) terminal={ROOT}: the prompt left the screen"
    assert shown(shell, capsys)[1:] == [f"PROMPT-STALE coordinator coordinator 1m terminal={ROOT}: agentWait via hook is set and the input box is back"]


def test_a_stale_wait_alone_never_raises_a_record(home, shell):
    register(home)
    unbound(shell)
    coordinator(shell, "waiting", "coordinator.answered")

    assert poll(shell, 4)[ROOT]["state"] == "stale"
    assert shell.posts == []


def test_one_dialog_whose_since_orca_keeps_rewriting_stays_one_record_one_line_and_one_owner_card(home, shell):
    supervised(home)
    unbound(shell)
    coordinator(shell, "waiting", "coordinator.approval")
    supervisor(shell, "managed-server.working", idle=True)
    captures = set()

    for since in (1791631247002, 1791631259279, 1791631280162, 1791631294557, 1791631308001, 1791631321440, 1791631335912, 1791631349077, 1791631362530, 1791631376218, 1791631390644, 1791631404189):
        churned = fixture("terminal-show.coordinator.waiting")
        churned["result"]["terminal"]["agentWait"]["since"] = since
        shell.orca[show(ROOT)] = churned
        row = poll(shell)[ROOT]
        captures.add(row["capture"])
        assert row["since"] == since

    assert [post["--kind"] for post in shell.posts] == ["blocker"] and [post["--to"] for post in shell.posts] == ["codex-supervisor"]
    assert len(shell.cards) == 1 and len(shell.sends) == 1 and len(captures) == 1
    assert json.loads(Path(captures.pop()).read_text())["since"] == 1791631247002
    assert "supervisor codex-supervisor got the line 5m ago, its turn started, and it is still open" in shell.cards[0]["body"]


def another_command() -> dict:
    screen = fixture("screen.coordinator.approval")
    screen["result"]["terminal"]["tail"] = [line.replace("single-stream fetch", "eight-stream fetch") for line in screen["result"]["terminal"]["tail"]]
    return screen


def test_a_second_prompt_on_the_same_terminal_is_a_second_card(home, shell):
    register(home)
    unbound(shell)
    coordinator(shell, "waiting", "coordinator.approval")
    earlier = poll(shell)[ROOT]["capture"]
    shell.orca[read(ROOT)] = another_command()

    poll(shell)

    first, cleared, second = shell.cards
    assert (first["status"], cleared["status"], second["status"]) == ("needs_you", "info", "needs_you") and shell.posts == []
    assert f"waits on an approval dialog ({HOOK_TITLE}). It froze the coordinator" in second["body"] and state()[ROOT]["capture"] in second["body"] and state()[ROOT]["capture"] != earlier


@pytest.mark.parametrize("screen", ["coordinator.question", "coordinator.preview-question"])
def test_the_coordinators_own_question_waits_on_the_owner_in_its_terminal_and_raises_no_record(home, shell, screen):
    supervised(home)
    unbound(shell)
    coordinator(shell, "waiting", screen)
    supervisor(shell, "managed-server.working", idle=True)

    rows = poll(shell, 12)

    assert rows[ROOT]["state"] == "question" and rows[ROOT]["alerts"] == {} and rows[ROOT]["capture"]
    assert shell.posts == [] and shell.sends == []


def test_an_approval_that_gives_way_to_the_coordinators_question_is_resolved(home, shell):
    register(home)
    unbound(shell)
    coordinator(shell, "waiting", "coordinator.approval")
    poll(shell)
    shell.orca[read(ROOT)] = fixture("screen.coordinator.question")

    rows = poll(shell, 3)

    assert [card["status"] for card in shell.cards] == ["needs_you", "info"] and rows[ROOT]["state"] == "question" and rows[ROOT]["alerts"] == {}


def test_two_terminals_at_the_same_dialog_keep_a_screen_each(home, shell):
    workers(home, shell, "waiting")
    coordinator(shell, "waiting", "coordinator.approval")
    shell.orca[read(LOCAL_TERMINAL)] = fixture("screen.coordinator.approval")

    rows = poll(shell)

    saved = {key: json.loads(Path(rows[key]["capture"]).read_text()) for key in (ROOT, f"dispatch:{LOCAL}")}
    assert saved[ROOT]["asked"] == saved[f"dispatch:{LOCAL}"]["asked"] == ASKED
    assert (saved[ROOT]["terminal"], saved[f"dispatch:{LOCAL}"]["terminal"]) == (ROOT, LOCAL_TERMINAL)


def test_a_wait_with_a_screen_that_shows_no_known_prompt_is_unknown_and_reported_once_after_three_polls(home, shell, capsys):
    register(home)
    unbound(shell)
    coordinator(shell, "waiting", "coordinator.unrecognised")

    poll(shell, 2)
    assert shell.posts == []
    rows = poll(shell, 3)

    assert rows[ROOT]["state"] == "unknown"
    [report] = shell.posts
    assert report["--to"] == "desk-runner" and report["--kind"] == "report"
    capture = home / "scratch" / "release-v3" / "prompt-watch" / "screens" / Path(rows[ROOT]["unread_capture"]).name
    assert whole(report) == (
        f"UNKNOWN coordinator (coordinator) terminal={ROOT}: agentWait via hook is set and the screen shows neither a known dialog nor an input box; "
        f"screen saved at {capture}; unread for 3 polls, so whether it waits on a prompt is not known"
    )
    assert json.loads(capture.read_text())["tail"][-2] == "   Loading conversations…" and capture.stat().st_mode & 0o777 == 0o600
    assert shown(shell, capsys)[0].endswith("0 approval, 0 question, 0 stale, 1 unknown, 0 unreachable, 0 clear")


def test_a_screen_orca_cannot_render_is_unknown_whatever_its_text_holds(home, shell):
    register(home)
    unbound(shell)
    coordinator(shell, "waiting", "unavailable")

    assert poll(shell)[ROOT] | {"state_at": ""} == {
        "role": "coordinator",
        "name": "coordinator",
        "terminal": ROOT,
        "environment": "",
        "state": "unknown",
        "detail": "no rendered screen (source screen-unavailable)",
        "since": 1791627768779,
        "alerts": {},
        "unseen": 1,
        "unseen_state": "unknown",
        "state_at": "",
    }


def test_an_unread_terminal_reports_once_and_reads_again_only_after_three_polls_read_it(home, shell):
    register(home)
    unbound(shell)
    coordinator(shell, "waiting", "unavailable")
    poll(shell, 3)
    coordinator(shell, "clear", "coordinator.answered")
    poll(shell)
    coordinator(shell, "waiting", "unavailable")
    poll(shell, 3)
    coordinator(shell, "clear", "coordinator.answered")
    poll(shell, 2)
    assert [post["--text"].split()[0] for post in shell.posts] == ["UNKNOWN"]

    rows = poll(shell)

    [_, readable] = shell.posts
    assert readable["--to"] == "desk-runner" and readable["--kind"] == "report" and readable["--topic"] == "prompt:coordinator"
    assert whole(readable) == f"READABLE coordinator (coordinator) terminal={ROOT}: read for 3 polls in a row since the watch reported it unknown"
    assert rows[ROOT]["state"] == "clear" and "reported" not in rows[ROOT] and "readable" not in rows[ROOT]
    poll(shell, 2)
    coordinator(shell, "waiting", "unavailable")
    poll(shell, 3)
    assert [post["--text"].split()[0] for post in shell.posts] == ["UNKNOWN", "READABLE", "UNKNOWN"]


def workers(home: Path, shell: FakeShell, local: str = "clear") -> None:
    register(home, orca_run=RUN)
    coordinator(shell, "clear", "coordinator.answered")
    shell.orca[("orchestration", "worker-list", "--run", RUN, "--include-remote")] = fixture("worker-list")
    shell.orca[worker_show(LOCAL)] = fixture(f"worker-show.local.{local}")
    shell.orca[worker_show(REMOTE)] = fixture("worker-show.remote")
    shell.orca[read(REMOTE_TERMINAL, ENVIRONMENT)] = fixture("screen.managed-server.working")
    shell.orca[worker_show(GONE)] = fixture("error.remote-runtime-unavailable")
    receipts = Path.home() / prompt_watch.RECEIPTS / RUN
    receipts.mkdir(parents=True)
    (receipts / "bake.json").write_text(json.dumps({"ok": True, "result": {"taskId": "task_8c1d2e3f4a5b", "dispatchId": LOCAL}}))


def test_a_workers_question_wakes_the_coordinator_once(home, shell):
    workers(home, shell, "waiting")
    shell.orca[read(LOCAL_TERMINAL)] = fixture("screen.coordinator.question")

    rows = poll(shell, 3)

    assert rows[f"dispatch:{LOCAL}"]["state"] == "question" and rows[f"dispatch:{LOCAL}"]["name"] == "bake"
    assert shell.kinds("bake") == ["blocker"]
    post = shell.to("root")[0]
    assert whole(post) == notice("bake", "worker", "a question dialog", "Test page", rows[f"dispatch:{LOCAL}"]["capture"], f"orca terminal read --terminal {LOCAL_TERMINAL} --screen", "The watch answers nothing.")
    assert shell.cards == [] and shell.kinds(GONE) == ["report"]


def ticking(remaining: str) -> dict:
    screen = fixture("screen.coordinator.approval")
    tail = screen["result"]["terminal"]["tail"]
    at = next(index for index, line in enumerate(tail) if "Do you want to proceed?" in line)
    tail.insert(at, f" ⚠ Claude Code will automatically deny this request in {remaining}, to avoid blocking progress on an unattended session")
    return screen


def test_a_workers_dialog_whose_auto_deny_countdown_ticks_is_one_blocker_one_still_open_report_and_one_clear(home, shell):
    workers(home, shell, "waiting")

    for remaining in ("1:58", "1:26", "0:55"):
        shell.orca[read(LOCAL_TERMINAL)] = ticking(remaining)
        poll(shell)

    assert shell.kinds("bake") == ["blocker"]
    [blocker] = [post for post in shell.posts if post["--topic"] == "prompt:bake"]
    episode = state()[f"dispatch:{LOCAL}"]["episode"]
    assert f"waits on an approval dialog ({HOOK_TITLE}; auto-denies in 1:58). {answer_line(f'--dispatch {LOCAL}', episode)} Screen: " in whole(blocker)

    poll(shell, 20)

    assert shell.kinds("bake") == ["blocker", "report"]
    reminder = [post for post in shell.posts if post["--topic"] == "prompt:bake"][1]
    assert reminder["--to"] == "root" and f"Still open 10m after #{blocker['seq']}. {answer_line(f'--dispatch {LOCAL}', episode)} Screen: " in whole(reminder)

    shell.orca[worker_show(LOCAL)] = fixture("worker-show.local.clear")
    poll(shell)

    assert shell.kinds("bake") == ["blocker", "report", "unblock"]


def test_a_local_worker_with_no_wait_costs_no_screen_read_and_a_settled_one_no_show(home, shell):
    workers(home, shell)

    rows = poll(shell)

    assert rows[f"dispatch:{LOCAL}"]["state"] == "clear"
    assert [call[4] for call in shell.reads()] == [ROOT, REMOTE_TERMINAL]
    assert ["orca", *worker_show("ctx_b4e069ef5d36"), "--json"] not in shell.calls


def test_a_managed_server_worker_sets_no_wait_so_its_screen_is_read_through_its_environment(home, shell):
    workers(home, shell)

    assert poll(shell)[f"dispatch:{REMOTE}"] | {"state_at": ""} == {
        "role": "worker",
        "name": REMOTE,
        "terminal": REMOTE_TERMINAL,
        "environment": ENVIRONMENT,
        "state": "clear",
        "detail": "",
        "since": None,
        "alerts": {},
        "unseen": 0,
        "state_at": "",
    }
    shell.orca[read(REMOTE_TERMINAL, ENVIRONMENT)] = fixture("screen.managed-server.approval")

    rows = poll(shell, 2)

    assert rows[f"dispatch:{REMOTE}"]["state"] == "approval"
    assert shell.kinds(REMOTE) == ["blocker"]
    assert whole(shell.to("root")[0]) == notice(
        REMOTE,
        "worker",
        "an approval dialog",
        "• I need to clear the stale build directory before the bake can rerun.",
        rows[f"dispatch:{REMOTE}"]["capture"],
        f"orca terminal read --terminal {REMOTE_TERMINAL} --screen --environment {ENVIRONMENT}",
        answer_line(f"--dispatch {REMOTE}", rows[f"dispatch:{REMOTE}"]["episode"]),
    )


def test_an_unreachable_remote_host_is_its_own_state_reported_once_and_never_a_prompt(home, shell, capsys):
    workers(home, shell)

    poll(shell, 2)
    assert shell.posts == []
    rows = poll(shell, 3)

    assert rows[f"dispatch:{GONE}"]["state"] == "unreachable"
    [report] = shell.posts
    assert report["--to"] == "desk-runner" and report["--kind"] == "report"
    assert report["--text"] == (
        f"UNREACHABLE {GONE} (worker) terminal=unresolved: remote_runtime_unavailable: Could not connect to the remote Orca runtime.; "
        "unread for 3 polls, so whether it waits on a prompt is not known"
    )
    lines = shown(shell, capsys)
    assert lines[0].endswith("0 approval, 0 question, 0 stale, 0 unknown, 1 unreachable, 3 clear")
    assert lines[1:] == [f"PROMPT-UNREACHABLE {GONE} worker 2m terminal=unresolved: remote_runtime_unavailable: Could not connect to the remote Orca runtime."]


def test_a_remote_screen_that_stops_answering_keeps_the_open_prompt_and_raises_nothing_new(home, shell):
    workers(home, shell)
    shell.orca[read(REMOTE_TERMINAL, ENVIRONMENT)] = fixture("screen.managed-server.approval")
    poll(shell)
    shell.orca[read(REMOTE_TERMINAL, ENVIRONMENT)] = fixture("error.remote-runtime-unavailable")
    poll(shell)
    shell.orca[read(REMOTE_TERMINAL, ENVIRONMENT)] = fixture("screen.managed-server.approval")

    rows = poll(shell)

    assert rows[f"dispatch:{REMOTE}"]["state"] == "approval"
    assert shell.kinds(REMOTE) == ["blocker"]


def test_with_no_run_in_the_registry_the_workers_come_from_the_run_bound_to_the_coordinators_terminal(home, shell):
    register(home)
    coordinator(shell, "clear", "coordinator.answered")
    shell.orca[("orchestration", "worker-list", "--include-remote")] = fixture("worker-list")
    shell.orca[worker_show(LOCAL)] = fixture("worker-show.local.clear")
    shell.orca[worker_show(REMOTE)] = fixture("worker-show.remote")
    shell.orca[read(REMOTE_TERMINAL, ENVIRONMENT)] = fixture("screen.managed-server.working")
    shell.orca[worker_show(GONE)] = fixture("error.remote-runtime-unavailable")

    rows = poll(shell)

    assert shell.envs[("orchestration", "worker-list", "--include-remote")] == {"ORCA_TERMINAL_HANDLE": ROOT}
    assert sorted(rows) == sorted([ROOT, f"dispatch:{LOCAL}", f"dispatch:{REMOTE}", f"dispatch:{GONE}"])


def test_an_unbound_terminal_lists_every_run_so_none_of_those_workers_are_taken(home, shell):
    register(home)
    coordinator(shell, "clear", "coordinator.answered")
    listed = fixture("worker-list")
    listed["result"]["scope"] = {"run": None, "source": "all"}
    shell.orca[("orchestration", "worker-list", "--include-remote")] = listed

    assert sorted(poll(shell)) == [ROOT]


def worker_start(dispatch: str) -> str:
    return json.dumps({"ok": True, "result": {"taskId": "task_8c1d2e3f4a5b", "dispatchId": dispatch}})


def launched(name: str, dispatch: str) -> Path:
    path = Path.home() / prompt_watch.RECEIPTS / RUN / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(worker_start(dispatch))
    return path


def touched(path: Path) -> None:
    info = path.stat()
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))


def swapped(path: Path, text: str) -> None:
    original = path.stat()
    swap = path.with_name(f"{path.name}.new")
    swap.write_text(text)
    os.utime(swap, ns=(original.st_atime_ns, original.st_mtime_ns))
    swap.replace(path)
    replaced = path.stat()
    assert ((replaced.st_size, replaced.st_mtime_ns), replaced.st_ino != original.st_ino) == ((original.st_size, original.st_mtime_ns), True)


def overwritten(path: Path, text: str) -> None:
    original = path.stat()
    while (rewritten := path.stat()).st_ctime_ns == original.st_ctime_ns:
        path.write_text(text)
        os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert (rewritten.st_dev, rewritten.st_ino, rewritten.st_size, rewritten.st_mtime_ns) == (original.st_dev, original.st_ino, original.st_size, original.st_mtime_ns)


@pytest.fixture
def opened(monkeypatch) -> list[str]:
    names: list[str] = []
    read_text = Path.read_text

    def counted(path: Path, *args, **kwargs) -> str:
        if path.parent.name == RUN:
            names.append(path.name)
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted)
    return names


def test_the_lane_names_match_a_cold_read_across_added_rewritten_replaced_deleted_and_untouched_receipts(home, shell):
    watch = prompt_watch.Watch(shell, DRIVE)

    def polled() -> dict[str, str]:
        warm = watch.lanes(RUN)
        assert warm == prompt_watch.Watch(shell, DRIVE).lanes(RUN)
        return warm

    deleting, adding, rewriting, replacing = "ctx_9f8e7d6c5b4a", "ctx_0a1b2c3d4e5f", "ctx_f5e4d3c2b1a0", "ctx_1a2b3c4d5e6f"
    launched("kept", LOCAL)
    rewritten = launched("rewritten", REMOTE)
    replaced = launched("replaced", GONE)
    deleted = launched("deleted", deleting)
    polls = [polled()]
    launched("added", adding)
    polls.append(polled())
    before = rewritten.stat()
    launched("rewritten", rewriting)
    touched(rewritten)
    polls.append(polled())
    swapped(replaced, worker_start(replacing))
    polls.append(polled())
    deleted.unlink()
    polls.append(polled())
    after = rewritten.stat()

    assert ((after.st_ino, after.st_size), after.st_mtime_ns != before.st_mtime_ns) == ((before.st_ino, before.st_size), True)
    assert polls == [
        {LOCAL: "kept", REMOTE: "rewritten", GONE: "replaced", deleting: "deleted"},
        {LOCAL: "kept", REMOTE: "rewritten", GONE: "replaced", deleting: "deleted", adding: "added"},
        {LOCAL: "kept", rewriting: "rewritten", GONE: "replaced", deleting: "deleted", adding: "added"},
        {LOCAL: "kept", rewriting: "rewritten", replacing: "replaced", deleting: "deleted", adding: "added"},
        {LOCAL: "kept", rewriting: "rewritten", replacing: "replaced", adding: "added"},
    ]


def test_a_receipt_unchanged_since_the_last_poll_is_not_opened_again(home, shell, opened):
    watch = prompt_watch.Watch(shell, DRIVE)
    launched("bake", LOCAL)
    shipped = launched("ship", REMOTE)

    first, second = watch.lanes(RUN), watch.lanes(RUN)
    reads = list(opened)
    launched("ship", GONE)
    touched(shipped)
    third = watch.lanes(RUN)

    assert (first, second, reads) == ({LOCAL: "bake", REMOTE: "ship"}, {LOCAL: "bake", REMOTE: "ship"}, ["bake.json", "ship.json"])
    assert (third, opened[len(reads) :]) == ({LOCAL: "bake", GONE: "ship"}, ["ship.json"])


def test_a_symlinked_receipt_is_read_again_when_its_target_is_replaced(home, shell, opened):
    watch = prompt_watch.Watch(shell, DRIVE)
    target = home / "launches" / "bake.json"
    target.parent.mkdir()
    target.write_text(worker_start(LOCAL))
    link = Path.home() / prompt_watch.RECEIPTS / RUN / "bake.json"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)

    first, second = watch.lanes(RUN), watch.lanes(RUN)
    swapped(target, worker_start(REMOTE))
    third = watch.lanes(RUN)

    assert (first, second, third, opened) == ({LOCAL: "bake"}, {LOCAL: "bake"}, {REMOTE: "bake"}, ["bake.json", "bake.json"])


def test_a_receipt_rewritten_in_place_at_the_same_size_with_its_mtime_restored_is_read_again(home, shell, opened):
    watch = prompt_watch.Watch(shell, DRIVE)
    bake = launched("bake", LOCAL)

    first, second = watch.lanes(RUN), watch.lanes(RUN)
    overwritten(bake, worker_start(REMOTE))
    third = watch.lanes(RUN)

    assert (first, second, third, opened) == ({LOCAL: "bake"}, {LOCAL: "bake"}, {REMOTE: "bake"}, ["bake.json", "bake.json"])


@pytest.mark.parametrize(
    "artifact",
    [
        "[]",
        f"[{worker_start('ctx_25f8965ea0fb')}]",
        '{"ok": false, "error": {"code": "worker_start_failed", "message": "no agent"}}',
        '{"ok": true, "result": {"worktree": {"path": "/work/app-bake"}}}',
        '{"ok": true, "result": {"dispatchId": ["ctx_25f8965ea0fb"]}}',
    ],
    ids=["array", "array-of-receipts", "error-reply", "worktree-reply", "unhashable-dispatch"],
)
def test_a_valid_json_side_artifact_names_no_lane_and_is_opened_once_across_polls(home, shell, opened, artifact):
    watch = prompt_watch.Watch(shell, DRIVE)
    launched("bake", LOCAL)
    (Path.home() / prompt_watch.RECEIPTS / RUN / "lint.json").write_text(artifact)

    first, second = watch.lanes(RUN), watch.lanes(RUN)
    reads = list(opened)

    assert (first, second, list(watch.receipts.values()), reads) == ({LOCAL: "bake"}, {LOCAL: "bake"}, [{LOCAL: "bake"}, {}], ["bake.json", "lint.json"])
    assert second == prompt_watch.Watch(shell, DRIVE).lanes(RUN)


@pytest.mark.parametrize(
    "malformed",
    [
        '{"ok": true, "result": {"taskId": "task_8c1d2e3f4a5b", "dispatchId": "ctx_',
        "setup: installing\n{}",
    ],
    ids=["truncated", "not-json"],
)
def test_a_receipt_that_is_not_valid_json_names_no_lane_and_is_opened_on_every_poll_and_a_partly_written_one_is_named_once_it_is_whole(home, shell, opened, malformed):
    watch = prompt_watch.Watch(shell, DRIVE)
    launched("bake", LOCAL)
    partial = Path.home() / prompt_watch.RECEIPTS / RUN / "ship.json"
    written = worker_start(REMOTE)
    partial.write_text(written[: len(written) // 2])
    (partial.parent / "lint.json").write_text(malformed)

    first = watch.lanes(RUN)
    with partial.open("a") as rest:
        rest.write(written[len(written) // 2 :])
    second, third = watch.lanes(RUN), watch.lanes(RUN)
    reads = list(opened)

    assert (first, second, third, list(watch.receipts.values())) == ({LOCAL: "bake"}, {LOCAL: "bake", REMOTE: "ship"}, {LOCAL: "bake", REMOTE: "ship"}, [{LOCAL: "bake"}, {REMOTE: "ship"}])
    assert reads == ["bake.json", "lint.json", "ship.json", "lint.json", "ship.json", "lint.json"]
    assert second == prompt_watch.Watch(shell, DRIVE).lanes(RUN)


def test_a_side_artifact_rewritten_in_place_into_a_receipt_with_only_its_ctime_changed_is_read_again_and_named(home, shell, opened):
    watch = prompt_watch.Watch(shell, DRIVE)
    receipt = worker_start(LOCAL)
    bake = Path.home() / prompt_watch.RECEIPTS / RUN / "bake.json"
    bake.parent.mkdir(parents=True)
    bake.write_text(json.dumps({"ok": True, "result": {"taskId": "task_8c1d2e3f4a5b"}}).ljust(len(receipt)))

    first, second = watch.lanes(RUN), watch.lanes(RUN)
    overwritten(bake, receipt)
    third, fourth = watch.lanes(RUN), watch.lanes(RUN)

    assert (first, second, third, fourth, opened) == ({}, {}, {LOCAL: "bake"}, {LOCAL: "bake"}, ["bake.json", "bake.json"])


def test_a_receipt_rewritten_into_invalid_json_drops_the_lane_it_named(home, shell, opened):
    watch = prompt_watch.Watch(shell, DRIVE)
    bake = launched("bake", LOCAL)
    first = watch.lanes(RUN)
    bake.write_text(worker_start(LOCAL)[:-1] + " ")
    touched(bake)

    second, third = watch.lanes(RUN), watch.lanes(RUN)

    assert (first, second, third, watch.receipts, opened) == ({LOCAL: "bake"}, {}, {}, {}, ["bake.json", "bake.json", "bake.json"])


def test_a_run_with_no_worker_in_progress_lists_and_reads_no_receipt(home, shell, opened, monkeypatch):
    workers(home, shell)
    listed = fixture("worker-list")
    for worker in listed["result"]["workers"]:
        worker["projection"]["outcome"] = "succeeded"
    shell.orca[("orchestration", "worker-list", "--run", RUN, "--include-remote")] = listed
    globbed: list[str] = []
    glob = Path.glob

    def counted(path: Path, pattern: str, **kwargs):
        if path.name == RUN:
            globbed.append(pattern)
        return glob(path, pattern, **kwargs)

    monkeypatch.setattr(Path, "glob", counted)

    rows = poll(shell)

    assert (sorted(rows), globbed, opened) == ([ROOT], [], [])


def test_a_worker_that_settles_while_at_a_prompt_has_its_record_resolved(home, shell):
    workers(home, shell, "waiting")
    shell.orca[read(LOCAL_TERMINAL)] = fixture("screen.coordinator.approval")
    poll(shell)
    listed = fixture("worker-list")
    listed["result"]["workers"][0]["projection"]["outcome"] = "succeeded"
    shell.orca[("orchestration", "worker-list", "--run", RUN, "--include-remote")] = listed

    rows = poll(shell)

    assert f"dispatch:{LOCAL}" not in rows
    assert [(post["--kind"], post.get("--resolves-seq")) for post in shell.posts] == [("blocker", None), ("unblock", str(shell.posts[0]["seq"]))]
    assert shell.posts[1]["--text"] == f"CLEARED bake (worker) terminal={LOCAL_TERMINAL}: the terminal left the watch"


@pytest.mark.parametrize(("page", "cursor"), [("error.remote-runtime-unavailable", ()), ("error.terminal-handle-stale", ("--cursor", "c2"))])
def test_a_worker_list_that_fails_closes_no_prompt_and_the_same_prompt_is_not_raised_again(home, shell, capsys, page, cursor):
    workers(home, shell, "waiting")
    shell.orca[read(LOCAL_TERMINAL)] = fixture("screen.coordinator.approval")
    listing = ("orchestration", "worker-list", "--run", RUN, "--include-remote")
    poll(shell)
    if cursor:
        paged = fixture("worker-list")
        paged["result"]["page"] |= {"hasMore": True, "nextCursor": "c2"}
        shell.orca[listing] = paged
    shell.orca[(*listing, *cursor)] = fixture(page)

    rows = poll(shell, 2)

    state_name = "unreachable" if page == "error.remote-runtime-unavailable" else "unknown"
    assert rows[f"dispatch:{LOCAL}"]["state"] == state_name and rows[f"dispatch:{LOCAL}"]["detail"].startswith("last read approval; worker-list failed with ")
    assert rows[f"dispatch:{LOCAL}"]["alerts"] == {"root": shell.posts[0]["seq"]} and rows[f"dispatch:{REMOTE}"]["detail"].startswith("last read clear; ")
    assert rows["workers"]["state"] == state_name
    assert [post["--kind"] for post in shell.posts] == ["blocker"]
    shell.orca[listing] = fixture("worker-list")

    rows = poll(shell)

    assert rows[f"dispatch:{LOCAL}"]["state"] == "approval" and "workers" not in rows
    assert shell.kinds("bake") == ["blocker"]


def test_a_worker_list_that_flaps_reports_once_and_reads_again_after_three_answered_polls(home, shell):
    workers(home, shell)
    listing = ("orchestration", "worker-list", "--run", RUN, "--include-remote")
    for page in ("error.timeout", "worker-list", "error.timeout"):
        shell.orca[listing] = fixture(page)
        poll(shell, 1 if page == "worker-list" else 3)
    shell.orca[listing] = fixture("worker-list")
    rows = poll(shell, 2)
    assert rows["workers"]["state"] == "clear" and rows["workers"]["reported"] == "unknown"

    rows = poll(shell)

    assert "workers" not in rows
    assert [whole(post) for post in shell.posts if post["--topic"] == "prompt:worker-list"] == [
        "UNKNOWN worker-list (workers) terminal=unresolved: timeout: timeout; unread for 3 polls, so whether it waits on a prompt is not known",
        "READABLE worker-list (workers) terminal=unresolved: read for 3 polls in a row since the watch reported it unknown",
    ]


def orca_down(shell: FakeShell, listing: tuple[str, ...]) -> None:
    shell.orca[show(ROOT)] = fixture("error.runtime-unavailable")
    shell.orca[listing] = fixture("error.timeout")


def test_an_orca_app_that_flaps_with_no_worker_in_progress_posts_nothing(home, shell):
    register(home)
    for _ in range(2):
        orca_down(shell, ("orchestration", "worker-list", "--include-remote"))
        rows = poll(shell, 4)
        assert rows[ROOT]["state"] == "unknown" and rows["workers"]["state"] == "unknown" and "runtime" not in rows
        unbound(shell)
        coordinator(shell, "clear", "coordinator.answered")
        rows = poll(shell, 4)
        assert set(rows) == {ROOT} and rows[ROOT]["state"] == "clear"

    assert shell.posts == []


def test_an_orca_app_down_while_workers_are_in_progress_is_one_report_and_one_readable(home, shell):
    workers(home, shell)
    shell.orca[worker_show(GONE)] = fixture("worker-show.local.clear")
    listing = ("orchestration", "worker-list", "--run", RUN, "--include-remote")
    poll(shell)
    orca_down(shell, listing)
    poll(shell, 5)
    shell.orca[listing] = fixture("worker-list")
    coordinator(shell, "clear", "coordinator.answered")

    rows = poll(shell, 3)

    assert "runtime" not in rows and rows[f"dispatch:{LOCAL}"]["state"] == "clear"
    assert [(post["--to"], post["--kind"], post["--topic"], whole(post)) for post in shell.posts] == [
        ("desk-runner", "report", "prompt:orca", f"UNKNOWN orca (runtime) terminal=unresolved: Orca answers no watched terminal (runtime_unavailable, timeout); bake, {REMOTE}, {GONE} were in progress at the last worker-list and may wait on a prompt; unread for 3 polls, so whether it waits on a prompt is not known"),
        ("desk-runner", "report", "prompt:orca", "READABLE orca (runtime) terminal=unresolved: read for 3 polls in a row since the watch reported it unknown"),
    ]


def test_a_record_cci_refuses_is_posted_on_the_next_poll(home, shell):
    supervised(home)
    unbound(shell)
    coordinator(shell, "waiting", "coordinator.approval")
    supervisor(shell)
    shell.refuse_posts = True
    poll(shell)
    shell.refuse_posts = False

    poll(shell, 2)

    assert [post["--to"] for post in shell.posts] == ["codex-supervisor"]


def test_a_card_cc_slack_refuses_is_posted_on_the_next_poll(home, shell):
    register(home)
    unbound(shell)
    coordinator(shell, "waiting", "coordinator.approval")
    shell.refuse_cards = True
    poll(shell)
    assert state()[ROOT]["alerts"] == {}
    shell.refuse_cards = False

    poll(shell, 2)

    assert [card["status"] for card in shell.cards] == ["needs_you"] and shell.posts == []


def test_show_names_a_watch_that_never_polled_and_one_that_stopped(home, shell, capsys):
    register(home)
    assert shown(shell, capsys) == [
        f"prompt-watch {DRIVE} has never polled",
        f"PROMPT-WATCH-DOWN {DRIVE}: it has never polled; start it with `{prompt_watch.SCRIPT} start --drive {DRIVE}`",
    ]
    unbound(shell)
    coordinator(shell, "waiting", "coordinator.approval")
    poll(shell)
    shell.sleep(240)

    lines = shown(shell, capsys)

    assert lines[0] == f"prompt-watch {DRIVE} polled 270s ago: 1 approval, 0 question, 0 stale, 0 unknown, 0 unreachable, 0 clear"
    assert lines[1] == f"PROMPT-WATCH-DOWN {DRIVE}: last poll 4m ago, so the states below are old; start it with `{prompt_watch.SCRIPT} start --drive {DRIVE}`"
    assert lines[2] == f"WAITING-ON-PROMPT coordinator coordinator approval 4m terminal={ROOT}: screen saved at {state()[ROOT]['capture']}"


def test_show_finds_the_drive_by_its_cci_name_and_prints_nothing_for_a_stranger(home, shell, capsys):
    register(home)
    assert prompt_watch.main(["show", "--cci-drive", "release-v3", "--json"], shell) == 0
    assert json.loads(capsys.readouterr().out)["rows"][0]["state"] == "down"
    assert prompt_watch.main(["show", "--cci-drive", "some-other-drive", "--json"], shell) == 0
    assert capsys.readouterr().out == "{}\n"


def test_the_watch_ends_with_the_drive(home, shell, capsys):
    register(home)
    unbound(shell)
    coordinator(shell, "clear", "coordinator.answered")
    ticks = iter(range(3))

    def sleep(seconds):
        if next(ticks) == 1:
            (drive.drives_dir() / f"{DRIVE}.json").unlink()

    shell.sleep = sleep

    assert prompt_watch.main(["run", "--drive", DRIVE], shell) == 0
    assert capsys.readouterr().out == f"prompt-watch {DRIVE}: the drive ended\n"
    assert len([call for call in shell.calls if call[:3] == ["orca", "terminal", "show"]]) == 2


def test_a_reply_in_a_shape_the_watch_does_not_know_makes_that_terminal_unknown_and_the_rest_still_read(home, shell):
    workers(home, shell)
    shell.orca[worker_show(LOCAL)] = {"ok": True, "result": {"terminal": {"handle": LOCAL_TERMINAL}}}

    rows = poll(shell)

    assert rows[f"dispatch:{LOCAL}"]["state"] == "unknown" and rows[f"dispatch:{LOCAL}"]["detail"] == "unreadable Orca reply (KeyError: 'observation')"
    assert rows[ROOT]["state"] == "clear" and rows[f"dispatch:{REMOTE}"]["state"] == "clear"


def test_a_poll_that_raises_is_logged_and_the_next_one_runs(home, shell, capsys):
    register(home)
    unbound(shell)
    coordinator(shell, "clear", "coordinator.answered")
    (prompt_watch.watch_dir(drive.find(DRIVE, None))).mkdir(parents=True)
    state_file = prompt_watch.watch_dir(drive.find(DRIVE, None)) / prompt_watch.STATE_FILE
    state_file.write_text("{")
    ticks = iter(range(3))

    def sleep(seconds):
        if next(ticks) == 0:
            state_file.unlink()
        else:
            (drive.drives_dir() / f"{DRIVE}.json").unlink()

    shell.sleep = sleep

    assert prompt_watch.main(["run", "--drive", DRIVE], shell) == 0
    assert "JSONDecodeError" in capsys.readouterr().err
    assert json.loads(state_file.read_text())["subjects"][ROOT]["state"] == "clear"


def held_lock(entry: dict, holder: str):
    prompt_watch.watch_dir(entry).mkdir(parents=True, exist_ok=True)
    held = (prompt_watch.watch_dir(entry) / prompt_watch.LOCK_FILE).open("a+")
    prompt_watch.fcntl.flock(held, prompt_watch.fcntl.LOCK_EX)
    held.write(holder)
    held.flush()
    return held


def test_a_second_watch_from_the_same_script_exits_at_once(home, shell, capsys):
    with held_lock(register(home), str(prompt_watch.SCRIPT)):
        assert prompt_watch.main(["run", "--drive", DRIVE], shell) == 0

    assert "another watch holds or awaits" in capsys.readouterr().out
    assert shell.calls == []


def successor(entry: dict):
    prompt_watch.watch_dir(entry).mkdir(parents=True, exist_ok=True)
    return (prompt_watch.watch_dir(entry) / prompt_watch.SUCCESSOR_FILE).open("a+")


def waited_on(entry: dict) -> bool:
    with successor(entry) as queue:
        return not prompt_watch.free(queue)


@pytest.mark.parametrize("holder", ["", "/plugins/cache/skills/long-running/0.7.42/skills/long-running/scripts/prompt_watch.py"])
def test_a_watch_from_another_script_locks_the_successor_file_waits_and_takes_over(home, shell, capsys, holder):
    entry = register(home)
    unbound(shell)
    coordinator(shell, "clear", "coordinator.answered")
    held = held_lock(entry, holder)
    seen = []

    def release():
        seen.append(waited_on(entry))
        held.close()

    shell.sleep = lambda seconds: (drive.drives_dir() / f"{DRIVE}.json").unlink()
    timer = threading.Timer(0.3, release)
    timer.start()

    assert prompt_watch.main(["run", "--drive", DRIVE], shell) == 0
    timer.join()

    assert seen == [True] and not waited_on(entry)
    assert (prompt_watch.watch_dir(entry) / prompt_watch.LOCK_FILE).read_text() == str(prompt_watch.SCRIPT)
    assert json.loads((prompt_watch.watch_dir(entry) / prompt_watch.STATE_FILE).read_text())["subjects"][ROOT]["state"] == "clear"
    assert "waiting to take over from" in capsys.readouterr().out


def test_a_second_watch_does_not_wait_behind_one_that_already_waits(home, shell, capsys):
    entry = register(home)
    with held_lock(entry, ""), successor(entry) as waiting:
        prompt_watch.fcntl.flock(waiting, prompt_watch.fcntl.LOCK_EX)

        assert prompt_watch.main(["run", "--drive", DRIVE], shell) == 0

    assert "another watch holds or awaits" in capsys.readouterr().out


def test_a_running_watch_exits_after_the_poll_that_finds_another_waiting(home, shell, capsys):
    entry = register(home)
    unbound(shell)
    coordinator(shell, "clear", "coordinator.answered")
    waiting = successor(entry)
    shell.sleep = lambda seconds: prompt_watch.fcntl.flock(waiting, prompt_watch.fcntl.LOCK_EX)

    assert prompt_watch.main(["run", "--drive", DRIVE], shell) == 0
    waiting.close()

    assert capsys.readouterr().out == f"prompt-watch {DRIVE}: handing over to the watch that holds {prompt_watch.watch_dir(entry) / prompt_watch.SUCCESSOR_FILE}\n"
    assert len([call for call in shell.calls if call[:3] == ["orca", "terminal", "show"]]) == 2


def test_a_waiting_watch_that_died_leaves_nothing_the_running_one_hands_over_to(home, shell, capsys):
    entry = register(home)
    unbound(shell)
    coordinator(shell, "clear", "coordinator.answered")
    with successor(entry) as died:
        prompt_watch.fcntl.flock(died, prompt_watch.fcntl.LOCK_EX)
    ticks = iter(range(2))
    shell.sleep = lambda seconds: next(ticks) and (drive.drives_dir() / f"{DRIVE}.json").unlink()

    assert prompt_watch.main(["run", "--drive", DRIVE], shell) == 0

    assert capsys.readouterr().out == f"prompt-watch {DRIVE}: the drive ended\n"
    assert len([call for call in shell.calls if call[:3] == ["orca", "terminal", "show"]]) == 2


def test_start_records_the_coordinators_terminal_and_detaches_one_run(home, shell, capsys, monkeypatch):
    register(home, root_terminal=None)
    spawned = []
    monkeypatch.setattr(prompt_watch.subprocess, "Popen", lambda argv, **kw: spawned.append((argv, kw)) or type("Child", (), {"pid": 4242})())

    assert prompt_watch.main(["start", "--drive", DRIVE, "--root-terminal", ROOT], shell) == 0

    assert drive.find(DRIVE, None)["root_terminal"] == ROOT
    [(argv, kw)] = spawned
    assert argv[1:] == [str(prompt_watch.SCRIPT), "run", "--drive", DRIVE] and kw["start_new_session"] is True
    assert capsys.readouterr().out == f"prompt-watch {DRIVE} pid 4242 log {home / 'scratch' / 'release-v3' / 'prompt-watch' / 'watch.log'}\n"



INSTEAD = "Leave the fetch alone and report what you measured so far."
CODEX = "screen.managed-server.approval"
WORKER_LIST = ("orchestration", "worker-list", "--include-remote")


def codex_idle() -> dict:
    screen = fixture("screen.managed-server.working")
    screen["result"]["terminal"]["tail"] = ["• Left build/out in place.", "", "› "]
    return screen


def at_dialog(shell: FakeShell, screen: dict | None = None, after: dict | None = None) -> None:
    shell.orca[worker_show(LOCAL)] = fixture("worker-show.local.waiting")
    shell.orca[read(LOCAL_TERMINAL)] = screen or fixture("screen.coordinator.approval")
    shell.orca[worker_show(REMOTE)] = fixture("worker-show.remote")
    shell.orca[read(REMOTE_TERMINAL, ENVIRONMENT)] = fixture(CODEX)
    shell.orca[show(LOCAL_TERMINAL)] = fixture("terminal-show.coordinator.waiting")
    shell.on_keys = {read(LOCAL_TERMINAL): after or fixture("screen.supervisor.claude-idle"), read(REMOTE_TERMINAL, ENVIRONMENT): codex_idle()}


def answer(shell: FakeShell, *argv: str) -> int:
    return prompt_watch.main(["answer", *argv], shell)


def listed(source: str, run: str | None = None, workers: bool = True) -> dict:
    reply = fixture("worker-list")
    reply["result"]["scope"] = {"run": run, "source": source}
    reply["result"]["workers"] = reply["result"]["workers"] if workers else []
    return reply


def with_rows(screen: dict, before: str, *rows: str) -> dict:
    tail = screen["result"]["terminal"]["tail"]
    at = next(index for index, line in enumerate(tail) if before in line)
    tail[at:at] = list(rows)
    return screen


def trust_dialog() -> dict:
    screen = fixture("screen.coordinator.approval")
    screen["result"]["terminal"]["tail"] = ["─" * 80, " Quick safety check: Is this a project you created or one you trust?", " ❯ No, exit", "   Yes, I trust this folder", " Enter to confirm · Esc to cancel"]
    return screen


def test_yes_types_the_yes_options_number_as_raw_keys_and_the_workers_run_mail_names_who_chose_what(shell, capsys):
    at_dialog(shell)

    assert answer(shell, "--dispatch", LOCAL, "--by", "root", "yes") == 0

    assert shell.keys == [key(LOCAL_TERMINAL, "1")]
    chose = f'root chose "1. Yes" for {LOCAL} on {LOCAL_TERMINAL}; the dialog closed'
    [mail] = shell.mail
    assert (mail["--to"], mail["--dispatch-id"], mail["--type"], mail["--subject"]) == (f"run:{RUN}", LOCAL, "status", f"root answered yes: {HOOK_TITLE}")
    assert mail["--body"].startswith(f"{chose}. Prompt episode ") and "single-stream fetch" not in mail["--body"]
    assert capsys.readouterr().out == f"ANSWERED {chose}; recorded in run:{RUN}\n"


def test_no_types_no_then_submits_what_to_do_instead_so_orca_takes_prompts_again(shell):
    at_dialog(shell)

    assert answer(shell, "--dispatch", LOCAL, "--by", "root", "no", "--instead", INSTEAD) == 0

    assert shell.keys == [key(LOCAL_TERMINAL, "2"), key(LOCAL_TERMINAL, INSTEAD), key(LOCAL_TERMINAL, "\r")]
    assert f'the dialog closed; told it instead: "{INSTEAD}". Prompt episode ' in shell.mail[0]["--body"]


@pytest.mark.parametrize(("choice", "typed"), [("yes", "1"), ("no", "2")])
def test_numbered_lines_in_the_command_above_the_options_never_choose_the_answer(shell, choice, typed):
    at_dialog(shell, with_rows(fixture("screen.coordinator.approval"), "Do you want to proceed?", " │ 1. No", " │ 2. Yes"))

    answer(shell, "--dispatch", LOCAL, "--by", "root", choice, *(("--instead", INSTEAD) if choice == "no" else ()))

    assert shell.keys[0] == key(LOCAL_TERMINAL, typed)


@pytest.mark.parametrize(("choice", "extra", "typed"), [("yes", (), "1"), ("no", ("--instead", INSTEAD), "3")])
def test_a_remote_codex_dialog_is_answered_through_its_server_with_its_own_numbering(shell, choice, extra, typed):
    at_dialog(shell)

    assert answer(shell, "--dispatch", REMOTE, "--by", "root", choice, *extra) == 0

    assert shell.keys[0] == key(REMOTE_TERMINAL, typed, ENVIRONMENT)
    assert shell.mail[0]["--to"] == f"run:{RUN}" and shell.mail[0]["--dispatch-id"] == REMOTE


@pytest.mark.parametrize(
    ("scope", "dispatch"),
    [(listed("all"), LOCAL), (listed("bound", RUN), LOCAL), (listed("bound", RUN, workers=False), None)],
    ids=["a worker's terminal names its dispatch", "a worker that also coordinates keeps its dispatch", "a coordinator's terminal names its bound Run"],
)
def test_a_terminal_handle_finds_the_run_whose_mail_records_the_answer_before_reading_the_dialog(shell, scope, dispatch):
    at_dialog(shell)
    shell.orca[WORKER_LIST] = scope

    assert answer(shell, "--terminal", LOCAL_TERMINAL, "--by", "root", "yes") == 0

    assert shell.envs[WORKER_LIST] == {prompt_watch.TERMINAL_ENV: LOCAL_TERMINAL}
    assert shell.calls.index(["orca", *WORKER_LIST, "--json"]) < shell.calls.index(["orca", *read(LOCAL_TERMINAL), "--json"])
    assert shell.mail[0]["--to"] == f"run:{RUN}" and shell.mail[0].get("--dispatch-id") == dispatch


@pytest.mark.parametrize(
    ("screen", "argv", "refusal"),
    [
        (fixture("screen.coordinator.answered"), ("yes",), "shows no approval dialog: it reads stale"),
        (fixture("screen.coordinator.question"), ("yes",), "shows no approval dialog: it reads question"),
        (fixture("screen.coordinator.approval"), ("--episode", "000000000000", "yes"), "not 000000000000"),
        (fixture("screen.coordinator.approval"), ("no",), "--instead goes with no and only with no"),
        (fixture("screen.coordinator.approval"), ("--instead", INSTEAD, "yes"), "--instead goes with no and only with no"),
        (trust_dialog(), ("yes",), "no numbered option"),
    ],
    ids=["no dialog", "a question picker", "another dialog than the record's", "a no that says nothing to do instead", "a yes with text to type", "options without numbers"],
)
def test_a_terminal_that_shows_no_answerable_dialog_gets_no_keys_and_no_mail(shell, screen, argv, refusal):
    at_dialog(shell, screen)

    with pytest.raises(SystemExit, match=refusal):
        answer(shell, "--dispatch", LOCAL, "--by", "root", *argv)

    assert shell.keys == [] and shell.mail == []


def test_a_terminal_in_no_run_and_owned_by_no_worker_gets_no_keys(shell):
    at_dialog(shell)
    shell.orca[WORKER_LIST] = listed("all", workers=False)

    with pytest.raises(SystemExit, match="coordinates no Run"):
        answer(shell, "--terminal", LOCAL_TERMINAL, "--by", "root", "yes")

    assert shell.keys == [] and shell.mail == []


@pytest.mark.parametrize(
    ("after", "reads"),
    [(fixture("screen.coordinator.approval"), "approval"), (another_command(), "approval"), (fixture("screen.unavailable"), "unknown")],
    ids=["the same dialog", "another dialog", "an unreadable screen"],
)
def test_keys_that_leave_no_idle_input_box_are_recorded_and_type_nothing_more(shell, after, reads):
    at_dialog(shell, after=after)

    assert answer(shell, "--dispatch", LOCAL, "--by", "root", "no", "--instead", INSTEAD) == 1

    assert shell.keys == [key(LOCAL_TERMINAL, "2")]
    assert shell.mail[0]["--body"].startswith(f'root chose "2. No" for {LOCAL} on {LOCAL_TERMINAL}; after the keys the terminal reads {reads}')


def test_a_closed_dialog_over_a_busy_input_box_gets_no_text_to_do_instead(shell):
    at_dialog(shell, after=fixture("screen.coordinator.answered"))

    assert answer(shell, "--dispatch", LOCAL, "--by", "root", "no", "--instead", INSTEAD) == 1

    assert shell.keys == [key(LOCAL_TERMINAL, "2")]
    assert "its input box is not empty and idle, so nothing was typed instead" in shell.mail[0]["--body"]


def test_the_command_in_a_workers_blocker_answers_that_dialog(home, shell):
    workers(home, shell, "waiting")
    shell.orca[read(LOCAL_TERMINAL)] = fixture("screen.coordinator.approval")
    poll(shell)
    command = whole(shell.to("root")[0]).split("Answer: ", 1)[1].split(" yes|no.", 1)[0]
    at_dialog(shell)

    assert answer(shell, *command.split()[2:], "yes") == 0

    assert shell.keys == [key(LOCAL_TERMINAL, "1")] and shell.mail[0]["--subject"].startswith("root answered yes")
