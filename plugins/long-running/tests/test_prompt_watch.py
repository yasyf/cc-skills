"""prompt_watch.py against captured Orca output on a fake clock: every Orca read and cci post is recorded on ``shell.calls``."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import drive
import prompt_watch
import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "prompt_watch"
DRIVE = "900424b6"
ROOT = "term_26c4ee5e-61f3-4fac-86aa-40bbdbc87c4e"
SUPERVISOR = "term_7f2d70f5-ca5c-4260-ab60-129bfd2e6ec2"
LOCAL = "ctx_25f8965ea0fb"
LOCAL_TERMINAL = "term_cfce7ff3-0976-4032-9d55-22697f68d6ee"
REMOTE = "ctx_1e2e970fc485"
REMOTE_TERMINAL = "term_5cfcad9a-6928-47d6-88e3-f04749533661"
GONE = "ctx_726698aaa803"
ENVIRONMENT = "codex-hooks-proof"
READ_ONLY = {("terminal", "show"), ("terminal", "read"), ("terminal", "wait"), ("orchestration", "worker-list"), ("orchestration", "worker-show")}
TUI_IDLE = ("terminal", "wait", "--terminal", SUPERVISOR, "--for", "tui-idle", "--timeout-ms", "1000")
DIALOG_WORDS = ("runs rm", "Do you want to proceed", "Kick it off", "Would you like to run", "rm -rf")
ASKED = "Time the current single-stream fetch with its SHA-256 check on the measurement Sprite as a same-host control / This shell -c script runs rm and could not be checked / Do you want to proceed?"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def show(terminal: str, environment: str = "") -> tuple[str, ...]:
    return ("terminal", "show", "--terminal", terminal, *(("--environment", environment) if environment else ()))


def read(terminal: str, environment: str = "") -> tuple[str, ...]:
    return ("terminal", "read", "--terminal", terminal, "--screen", *(("--environment", environment) if environment else ()))


def worker_show(dispatch: str) -> tuple[str, ...]:
    return ("orchestration", "worker-show", "--dispatch", dispatch)


class FakeShell(prompt_watch.Shell):
    def __init__(self):
        self.clock = datetime(2026, 10, 10, 10, 24, tzinfo=timezone.utc)
        self.orca: dict[tuple[str, ...], dict] = {}
        self.calls: list[list[str]] = []
        self.envs: dict[tuple[str, ...], dict | None] = {}
        self.posts: list[dict] = []
        self.refuse_posts = False
        self.sends: list[list[str]] = []
        self.send_reply = fixture("terminal-send.turn-started")

    def run(self, argv, env=None):
        self.calls.append(list(argv))
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
        "sessions": ["900424b6-7393-480c-a26a-f1bd21da6e57"],
        "orca_run": None,
        "started_at": "2026-10-10T07:43:54Z",
        "ledger": "48e3662f044120a992336bb63755fb9fe870fbf7",
        "repo": "acme/app",
        "git_common_dir": "/Users/dev/work/app/.git",
        "checkout": "/Users/dev/work/app",
        "state_dir": str(home / "scratch" / "release-v3"),
        "cci_drive": None,
        "root_terminal": ROOT,
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


def notice(name: str, role: str, waits: str, capture: str, read_it: str, tail: str) -> str:
    return f"PROMPT {name} ({role}) waits on {waits}. Screen: {capture}. Read: {read_it}. {tail}"


def shown(shell: FakeShell, capsys) -> list[str]:
    capsys.readouterr()
    assert prompt_watch.main(["show", "--drive", DRIVE], shell) == 0
    return capsys.readouterr().out.splitlines()


def test_a_hook_approval_on_the_coordinator_goes_once_to_the_supervisor_and_never_to_the_owner_card(home, shell):
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
        "coordinator", "coordinator", "an approval dialog", str(capture), f"orca terminal read --terminal {ROOT} --screen", "Answer only within what the owner already authorized, else ask the owner."
    )
    assert rows[ROOT]["detail"] == f"screen saved at {capture}; supervisor is busy"
    assert shell.sends == [] and "wake" not in rows[ROOT]


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
    everything_else = [whole(post) for post in shell.posts] + [call[6] for call in shell.sends] + shown(shell, capsys) + [json.dumps(rows)]
    assert [post["--to"] for post in shell.posts] == ["codex-supervisor", "owner"] and len(shell.sends) == 1
    assert not [text for text in everything_else for words in DIALOG_WORDS if words in text]


def test_with_no_supervisor_the_coordinators_prompt_is_one_ask_on_the_owners_card(home, shell):
    register(home)
    unbound(shell)
    coordinator(shell, "waiting", "coordinator.approval")

    poll(shell, 2)

    [post] = shell.posts
    assert post["--to"] == "owner" and post["--kind"] == "ask"
    assert whole(post).endswith(" It froze the coordinator; no supervisor desk is registered. Answer it in that terminal.")


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

    [ask] = shell.to("owner")
    assert whole(ask).endswith(f" It froze the coordinator; {why}. Answer it in that terminal.")
    assert shell.to("codex-supervisor") == []


def test_a_prompt_still_open_five_minutes_after_the_supervisor_heard_becomes_one_owner_ask(home, shell):
    supervised(home)
    unbound(shell)
    coordinator(shell, "waiting", "coordinator.approval")
    supervisor(shell)

    poll(shell, 9)
    assert [post["--to"] for post in shell.posts] == ["codex-supervisor"]
    poll(shell, 4)

    assert [post["--to"] for post in shell.posts] == ["codex-supervisor", "owner"]
    assert whole(shell.posts[1]).endswith(" It froze the coordinator; supervisor codex-supervisor stayed busy for 5m. Answer it in that terminal.")
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
    assert [post["--to"] for post in shell.posts] == ["codex-supervisor", "owner"] and len(shell.sends) == 1
    assert "supervisor codex-supervisor got the line 5m ago, its turn started, and it is still open" in whole(shell.posts[1])


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
def test_a_wake_orca_refuses_is_an_owner_ask_at_once_and_is_not_typed_again(home, shell, reply, failure):
    supervised(home)
    unbound(shell)
    coordinator(shell, "waiting", "coordinator.approval")
    supervisor(shell, "managed-server.working", idle=True)
    shell.send_reply = fixture(reply)

    rows = poll(shell, 3)

    assert rows[ROOT]["wake"] | {"at": ""} == {"at": "", "error": failure}
    assert [post["--to"] for post in shell.posts] == ["codex-supervisor", "owner"] and len(shell.sends) == 1
    assert f"the wake to supervisor codex-supervisor failed with {failure}" in whole(shell.posts[1])


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
    assert cleared["--kind"] == "unblock" and cleared["--resolves"] == str(raised["seq"]) and "--to" not in cleared
    assert cleared["--text"] == f"CLEARED coordinator (coordinator) terminal={ROOT}: the prompt left the screen"
    assert shown(shell, capsys)[1:] == [f"PROMPT-STALE coordinator coordinator 1m terminal={ROOT}: agentWait via hook is set and the input box is back"]


def test_a_stale_wait_alone_never_raises_a_record(home, shell):
    register(home)
    unbound(shell)
    coordinator(shell, "waiting", "coordinator.answered")

    assert poll(shell, 4)[ROOT]["state"] == "stale"
    assert shell.posts == []


def test_one_dialog_whose_since_orca_keeps_rewriting_stays_one_record_one_line_and_one_owner_ask(home, shell):
    supervised(home)
    unbound(shell)
    coordinator(shell, "waiting", "coordinator.question")
    supervisor(shell, "managed-server.working", idle=True)
    captures = set()

    for since in (1791631247002, 1791631259279, 1791631280162, 1791631294557, 1791631308001, 1791631321440, 1791631335912, 1791631349077, 1791631362530, 1791631376218, 1791631390644, 1791631404189):
        churned = fixture("terminal-show.coordinator.waiting")
        churned["result"]["terminal"]["agentWait"]["since"] = since
        shell.orca[show(ROOT)] = churned
        row = poll(shell)[ROOT]
        captures.add(row["capture"])
        assert row["since"] == since

    assert [post["--kind"] for post in shell.posts] == ["blocker", "ask"] and [post["--to"] for post in shell.posts] == ["codex-supervisor", "owner"]
    assert len(shell.sends) == 1 and len(captures) == 1
    assert "supervisor codex-supervisor got the line 5m ago, its turn started, and it is still open" in whole(shell.posts[1])


def test_a_second_prompt_on_the_same_terminal_is_a_second_record(home, shell):
    register(home)
    unbound(shell)
    coordinator(shell, "waiting", "coordinator.approval")
    poll(shell)
    shell.orca[read(ROOT)] = fixture("screen.coordinator.question")

    poll(shell)

    first, cleared, second = shell.posts
    assert (first["--kind"], cleared["--kind"], second["--kind"]) == ("ask", "unblock", "ask")
    assert "waits on a question dialog. Screen: " in whole(second) and state()[ROOT]["capture"] in whole(second)


def test_a_wait_with_a_screen_that_shows_no_known_prompt_is_unknown_and_reported_once_after_three_polls(home, shell, capsys):
    register(home)
    unbound(shell)
    coordinator(shell, "waiting", "coordinator.unrecognised")

    poll(shell, 2)
    assert shell.posts == []
    rows = poll(shell, 3)

    assert rows[ROOT]["state"] == "unknown"
    [report] = shell.posts
    assert report["--to"] == "root" and report["--kind"] == "report"
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


def workers(home: Path, shell: FakeShell, local: str = "clear") -> None:
    register(home, orca_run="run_4104a86f0a03")
    coordinator(shell, "clear", "coordinator.answered")
    shell.orca[("orchestration", "worker-list", "--run", "run_4104a86f0a03", "--include-remote")] = fixture("worker-list")
    shell.orca[worker_show(LOCAL)] = fixture(f"worker-show.local.{local}")
    shell.orca[worker_show(REMOTE)] = fixture("worker-show.remote")
    shell.orca[read(REMOTE_TERMINAL, ENVIRONMENT)] = fixture("screen.managed-server.working")
    shell.orca[worker_show(GONE)] = fixture("error.remote-runtime-unavailable")
    receipts = Path.home() / prompt_watch.RECEIPTS / "run_4104a86f0a03"
    receipts.mkdir(parents=True)
    (receipts / "bake.json").write_text(json.dumps({"ok": True, "result": {"taskId": "task_8c1d2e3f4a5b", "dispatchId": LOCAL}}))


def test_a_workers_question_wakes_the_coordinator_once(home, shell):
    workers(home, shell, "waiting")
    shell.orca[read(LOCAL_TERMINAL)] = fixture("screen.coordinator.question")

    rows = poll(shell, 3)

    assert rows[f"dispatch:{LOCAL}"]["state"] == "question" and rows[f"dispatch:{LOCAL}"]["name"] == "bake"
    assert shell.kinds("bake") == ["blocker"]
    post = shell.to("root")[0]
    assert whole(post) == notice("bake", "worker", "a question dialog", rows[f"dispatch:{LOCAL}"]["capture"], f"orca terminal read --terminal {LOCAL_TERMINAL} --screen", "The watch answers nothing.")
    assert shell.to("owner") == [] and shell.kinds(GONE) == ["report"]


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
        REMOTE, "worker", "an approval dialog", rows[f"dispatch:{REMOTE}"]["capture"], f"orca terminal read --terminal {REMOTE_TERMINAL} --screen --environment {ENVIRONMENT}", "The watch answers nothing."
    )


def test_an_unreachable_remote_host_is_its_own_state_reported_once_and_never_a_prompt(home, shell, capsys):
    workers(home, shell)

    poll(shell, 2)
    assert shell.posts == []
    rows = poll(shell, 3)

    assert rows[f"dispatch:{GONE}"]["state"] == "unreachable"
    [report] = shell.posts
    assert report["--to"] == "root" and report["--kind"] == "report"
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


def test_a_worker_that_settles_while_at_a_prompt_has_its_record_resolved(home, shell):
    workers(home, shell, "waiting")
    shell.orca[read(LOCAL_TERMINAL)] = fixture("screen.coordinator.approval")
    poll(shell)
    listed = fixture("worker-list")
    listed["result"]["workers"][0]["projection"]["outcome"] = "succeeded"
    shell.orca[("orchestration", "worker-list", "--run", "run_4104a86f0a03", "--include-remote")] = listed

    rows = poll(shell)

    assert f"dispatch:{LOCAL}" not in rows
    assert [(post["--kind"], post.get("--resolves")) for post in shell.posts] == [("blocker", None), ("unblock", str(shell.posts[0]["seq"]))]
    assert shell.posts[1]["--text"] == f"CLEARED bake (worker) terminal={LOCAL_TERMINAL}: the terminal left the watch"


@pytest.mark.parametrize(("page", "cursor"), [("error.remote-runtime-unavailable", ()), ("error.terminal-handle-stale", ("--cursor", "c2"))])
def test_a_worker_list_that_fails_closes_no_prompt_and_the_same_prompt_is_not_raised_again(home, shell, capsys, page, cursor):
    workers(home, shell, "waiting")
    shell.orca[read(LOCAL_TERMINAL)] = fixture("screen.coordinator.approval")
    listing = ("orchestration", "worker-list", "--run", "run_4104a86f0a03", "--include-remote")
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


def test_a_record_cci_refuses_is_posted_on_the_next_poll(home, shell):
    register(home)
    unbound(shell)
    coordinator(shell, "waiting", "coordinator.approval")
    shell.refuse_posts = True
    poll(shell)
    shell.refuse_posts = False

    poll(shell, 2)

    assert [post["--to"] for post in shell.posts] == ["owner"]


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


@pytest.mark.parametrize("holder", ["", "/plugins/cache/skills/long-running/0.7.41/skills/long-running/scripts/prompt_watch.py"])
def test_a_watch_from_another_script_names_itself_waits_for_the_lock_and_takes_over(home, shell, capsys, holder):
    entry = register(home)
    unbound(shell)
    coordinator(shell, "clear", "coordinator.answered")
    successor = prompt_watch.watch_dir(entry) / prompt_watch.SUCCESSOR_FILE
    held = held_lock(entry, holder)
    seen = []

    def release():
        seen.append(successor.read_text())
        held.close()

    shell.sleep = lambda seconds: (drive.drives_dir() / f"{DRIVE}.json").unlink()
    timer = threading.Timer(0.3, release)
    timer.start()

    assert prompt_watch.main(["run", "--drive", DRIVE], shell) == 0
    timer.join()

    assert seen == [str(prompt_watch.SCRIPT)] and not successor.exists()
    assert (prompt_watch.watch_dir(entry) / prompt_watch.LOCK_FILE).read_text() == str(prompt_watch.SCRIPT)
    assert json.loads((prompt_watch.watch_dir(entry) / prompt_watch.STATE_FILE).read_text())["subjects"][ROOT]["state"] == "clear"
    assert "waiting to take over from" in capsys.readouterr().out


def test_a_second_successor_from_the_same_script_does_not_wait(home, shell, capsys):
    entry = register(home)
    with held_lock(entry, ""):
        (prompt_watch.watch_dir(entry) / prompt_watch.SUCCESSOR_FILE).write_text(str(prompt_watch.SCRIPT))

        assert prompt_watch.main(["run", "--drive", DRIVE], shell) == 0

    assert "another watch holds or awaits" in capsys.readouterr().out


def test_a_running_watch_exits_after_the_poll_that_sees_a_successor(home, shell, capsys):
    entry = register(home)
    unbound(shell)
    coordinator(shell, "clear", "coordinator.answered")
    successor = prompt_watch.watch_dir(entry) / prompt_watch.SUCCESSOR_FILE
    shell.sleep = lambda seconds: successor.write_text("/plugins/long-running/0.7.99/scripts/prompt_watch.py")

    assert prompt_watch.main(["run", "--drive", DRIVE], shell) == 0

    assert capsys.readouterr().out == f"prompt-watch {DRIVE}: handing over to /plugins/long-running/0.7.99/scripts/prompt_watch.py\n"
    assert len([call for call in shell.calls if call[:3] == ["orca", "terminal", "show"]]) == 2 and successor.exists()


def test_start_records_the_coordinators_terminal_and_detaches_one_run(home, shell, capsys, monkeypatch):
    register(home, root_terminal=None)
    spawned = []
    monkeypatch.setattr(prompt_watch.subprocess, "Popen", lambda argv, **kw: spawned.append((argv, kw)) or type("Child", (), {"pid": 4242})())

    assert prompt_watch.main(["start", "--drive", DRIVE, "--root-terminal", ROOT], shell) == 0

    assert drive.find(DRIVE, None)["root_terminal"] == ROOT
    [(argv, kw)] = spawned
    assert argv[1:] == [str(prompt_watch.SCRIPT), "run", "--drive", DRIVE] and kw["start_new_session"] is True
    assert capsys.readouterr().out == f"prompt-watch {DRIVE} pid 4242 log {home / 'scratch' / 'release-v3' / 'prompt-watch' / 'watch.log'}\n"
