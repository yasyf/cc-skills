"""prompt_watch.py against captured Orca output on a fake clock: every Orca read and cci post is recorded on ``shell.calls``."""

from __future__ import annotations

import json
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
READ_ONLY = {("terminal", "show"), ("terminal", "read"), ("orchestration", "worker-list"), ("orchestration", "worker-show")}
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

    def run(self, argv, env=None):
        self.calls.append(list(argv))
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
    shell.orca[("orchestration", "worker-list")] = {"ok": True, "result": {"workers": [], "page": {"hasMore": False}, "scope": {"run": None, "source": "all"}}}


def coordinator(shell: FakeShell, state: str, screen: str) -> None:
    shell.orca[show(ROOT)] = fixture(f"terminal-show.coordinator.{state}")
    shell.orca[read(ROOT)] = fixture(f"screen.{screen}")


def supervisor(shell: FakeShell, screen: str = "managed-server.working") -> None:
    shell.orca[show(SUPERVISOR)] = fixture("terminal-show.coordinator.clear")
    shell.orca[read(SUPERVISOR)] = fixture(f"screen.{screen}")


def poll(shell: FakeShell, times: int = 1) -> dict:
    for _ in range(times):
        assert prompt_watch.main(["run", "--drive", DRIVE, "--once"], shell) == 0
        shell.sleep(prompt_watch.POLL_SECONDS)
    return state()


def state() -> dict:
    return json.loads((prompt_watch.watch_dir(drive.find(DRIVE, None)) / prompt_watch.STATE_FILE).read_text())["subjects"]


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
    assert post["--text"].startswith(f"PROMPT coordinator (coordinator) terminal={ROOT} waits on an approval: …")
    assert post["--text"].endswith(
        f"This shell -c script runs rm and could not be checked / Do you want to proceed?. Read: orca terminal read --terminal {ROOT} --screen. "
        "Answer only within what the owner already authorized, else ask the owner."
    )
    assert len(post["--text"]) <= 400
    assert rows[ROOT]["detail"] == ASKED


def test_with_no_supervisor_the_coordinators_prompt_is_one_ask_on_the_owners_card(home, shell):
    register(home)
    unbound(shell)
    coordinator(shell, "waiting", "coordinator.approval")

    poll(shell, 2)

    [post] = shell.posts
    assert post["--to"] == "owner" and post["--kind"] == "ask"
    assert "It froze the coordinator; no supervisor desk is registered. Answer it in that terminal." in post["--text"]


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
    assert f"It froze the coordinator; {why}. Answer it in that terminal." in ask["--text"]
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
    assert "supervisor codex-supervisor was told 5m ago and it is still open" in shell.posts[1]["--text"]


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


def test_a_second_prompt_on_the_same_terminal_is_a_second_record(home, shell):
    register(home)
    unbound(shell)
    coordinator(shell, "waiting", "coordinator.approval")
    poll(shell)
    later = fixture("terminal-show.coordinator.waiting")
    later["result"]["terminal"]["agentWait"]["since"] = 1791627999000
    shell.orca[show(ROOT)] = later
    shell.orca[read(ROOT)] = fixture("screen.coordinator.question")

    poll(shell)

    first, cleared, second = shell.posts
    assert (first["--kind"], cleared["--kind"], second["--kind"]) == ("ask", "unblock", "ask")
    assert "waits on a question: Test page / Should I kick off the end-to-end test page now?" in second["--text"]


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
    assert report["--text"] == (
        f"UNKNOWN coordinator (coordinator) terminal={ROOT}: agentWait via hook is set and the screen shows neither a known dialog nor an input box; "
        "unread for 3 polls, so whether it waits on a prompt is not known"
    )
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
    shell.orca[("orchestration", "worker-list", "--run", "run_4104a86f0a03")] = fixture("worker-list")
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
    assert post["--text"] == (
        f"PROMPT bake (worker) terminal={LOCAL_TERMINAL} waits on a question: Test page / Should I kick off the end-to-end test page now?. "
        f"Read: orca terminal read --terminal {LOCAL_TERMINAL} --screen. The watch answers nothing."
    )
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
    assert shell.to("root")[0]["--text"] == (
        f"PROMPT {REMOTE} (worker) terminal={REMOTE_TERMINAL} waits on an approval: Would you like to run the following command? / Reason: the bake left a partial output directory / $ rm -rf build/out. "
        f"Read: orca terminal read --terminal {REMOTE_TERMINAL} --screen --environment {ENVIRONMENT}. The watch answers nothing."
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
    shell.orca[("orchestration", "worker-list")] = fixture("worker-list")
    shell.orca[worker_show(LOCAL)] = fixture("worker-show.local.clear")
    shell.orca[worker_show(REMOTE)] = fixture("worker-show.remote")
    shell.orca[read(REMOTE_TERMINAL, ENVIRONMENT)] = fixture("screen.managed-server.working")
    shell.orca[worker_show(GONE)] = fixture("error.remote-runtime-unavailable")

    rows = poll(shell)

    assert shell.envs[("orchestration", "worker-list")] == {"ORCA_TERMINAL_HANDLE": ROOT}
    assert sorted(rows) == sorted([ROOT, f"dispatch:{LOCAL}", f"dispatch:{REMOTE}", f"dispatch:{GONE}"])


def test_an_unbound_terminal_lists_every_run_so_none_of_those_workers_are_taken(home, shell):
    register(home)
    coordinator(shell, "clear", "coordinator.answered")
    listed = fixture("worker-list")
    listed["result"]["scope"] = {"run": None, "source": "all"}
    shell.orca[("orchestration", "worker-list")] = listed

    assert sorted(poll(shell)) == [ROOT]


def test_a_worker_that_settles_while_at_a_prompt_has_its_record_resolved(home, shell):
    workers(home, shell, "waiting")
    shell.orca[read(LOCAL_TERMINAL)] = fixture("screen.coordinator.approval")
    poll(shell)
    listed = fixture("worker-list")
    listed["result"]["workers"][0]["projection"]["outcome"] = "succeeded"
    shell.orca[("orchestration", "worker-list", "--run", "run_4104a86f0a03")] = listed

    rows = poll(shell)

    assert f"dispatch:{LOCAL}" not in rows
    assert [(post["--kind"], post.get("--resolves")) for post in shell.posts] == [("blocker", None), ("unblock", str(shell.posts[0]["seq"]))]


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
    assert lines[2] == f"WAITING-ON-PROMPT coordinator coordinator approval 4m terminal={ROOT}: {ASKED}"


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


def test_a_second_watch_on_the_same_drive_exits_at_once(home, shell, capsys):
    entry = register(home)
    prompt_watch.watch_dir(entry).mkdir(parents=True)
    with (prompt_watch.watch_dir(entry) / prompt_watch.LOCK_FILE).open("w") as held:
        prompt_watch.fcntl.flock(held, prompt_watch.fcntl.LOCK_EX)

        assert prompt_watch.main(["run", "--drive", DRIVE], shell) == 0

    assert "another watch holds" in capsys.readouterr().out
    assert shell.calls == []


def test_start_records_the_coordinators_terminal_and_detaches_one_run(home, shell, capsys, monkeypatch):
    register(home, root_terminal=None)
    spawned = []
    monkeypatch.setattr(prompt_watch.subprocess, "Popen", lambda argv, **kw: spawned.append((argv, kw)) or type("Child", (), {"pid": 4242})())

    assert prompt_watch.main(["start", "--drive", DRIVE, "--root-terminal", ROOT], shell) == 0

    assert drive.find(DRIVE, None)["root_terminal"] == ROOT
    [(argv, kw)] = spawned
    assert argv[1:] == [str(prompt_watch.SCRIPT), "run", "--drive", DRIVE] and kw["start_new_session"] is True
    assert capsys.readouterr().out == f"prompt-watch {DRIVE} pid 4242 log {home / 'scratch' / 'release-v3' / 'prompt-watch' / 'watch.log'}\n"
