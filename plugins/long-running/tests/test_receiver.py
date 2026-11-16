import contextlib
import hashlib
import hmac
import json
import os
import re
import select
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from drive import cli, dashboard, github, receiver, state

REPO = "Forge-AI/monorepo"
REAL_HOOK = receiver.hook
REAL_UNHOOKED = receiver.unhooked
OPEN = {42: {"head": "aaa", "base": "main", "branch": "fix-it"}, 43: {"head": "bbb", "base": "fix-it", "branch": "stacked"}}
SECRET = b"the webhook secret"
BUILD = "https://buildkite.com/forge/release/builds/14769"


@pytest.fixture
def home(state_root: Path) -> None:
    receiver.home().mkdir(parents=True)


def delivered(offset: int = 0) -> list[dict]:
    return [{field: value for field, value in line.items() if field not in ("kind", "at")} for line, _ in state.read_lines(receiver.deliveries_path(), offset) if line["kind"] == receiver.DELIVERY]


def builds_delivered() -> list[tuple[str, list[str] | None]]:
    return [(line["event"], line["builds"]) for line, _ in state.read_lines(receiver.deliveries_path()) if line["kind"] == receiver.BUILD_DELIVERY]


@pytest.fixture
def unread(monkeypatch: pytest.MonkeyPatch) -> None:
    def read(*_: object) -> None:
        raise AssertionError("the receiver read a drive")

    for reader in ("names", "stored", "load"):
        monkeypatch.setattr(state, reader, read)


@pytest.mark.parametrize(
    ("event", "payload", "named"),
    [
        ("pull_request", {"pull_request": {"number": 42}}, [42]),
        ("pull_request_review", {"pull_request": {"number": 7}}, []),
        ("check_run", {"check_run": {"head_sha": "zzz", "pull_requests": [{"number": 42}]}}, [42]),
        ("check_run", {"check_run": {"head_sha": "bbb", "pull_requests": []}}, [43]),
        ("status", {"sha": "aaa", "branches": []}, [42]),
        ("status", {"sha": "zzz", "branches": [{"name": "stacked"}]}, [43]),
        ("push", {"ref": "refs/heads/fix-it"}, [43]),
        ("missed", None, [42, 43]),
        ("ping", {"zen": "Keep it logically awesome."}, []),
    ],
)
def test_a_delivery_names_the_open_prs_of_a_drive_it_changes(event: str, payload: dict | None, named: list[int]) -> None:
    assert receiver.named(receiver.facts(event, payload) | {"event": event}, OPEN) == named


@pytest.mark.parametrize(
    ("event", "payload", "marks"),
    [
        ("workflow_run", {"action": "completed", "workflow_run": {"head_branch": "main"}, "repository": {"default_branch": "main"}}, ["ci"]),
        ("workflow_run", {"action": "in_progress", "workflow_run": {"head_branch": "main"}, "repository": {"default_branch": "main"}}, []),
        ("workflow_run", {"action": "completed", "workflow_run": {"head_branch": "fix-it"}, "repository": {"default_branch": "main"}}, []),
        ("release", {"action": "published"}, ["release"]),
        ("release", {"action": "edited"}, ["release"]),
        ("missed", None, ["ci", "release"]),
        ("check_run", {"check_run": {"head_sha": "aaa", "pull_requests": []}}, []),
    ],
)
def test_a_delivery_moves_the_mark_of_each_kind_of_change_a_drive_waits_on_that_it_carries(event: str, payload: dict | None, marks: list[str]) -> None:
    assert receiver.marked(event, payload) == marks


def test_a_workflow_run_finishing_on_main_moves_its_repositorys_ci_mark_and_logs_its_run_and_no_fact_naming_a_pr(home: None) -> None:
    kept({REPO: {"id": 9, "last": "g1", "events": receiver.EVENTS, "ci": "g1", "release": "g1"}})
    receiver.took(REPO, 9, "g4", "workflow_run", {"action": "completed", "workflow_run": {"id": 77, "head_sha": "ccc", "head_branch": "main"}, "repository": {"default_branch": "main"}})
    assert (delivered(), receiver.heard(REPO, "ci"), receiver.heard(REPO, "release"), receiver.heard("yasyf/cc-skills", "ci")) == (
        [{"repo": REPO, "event": "workflow_run", "guid": "g4", "run": 77, "head": "ccc", "action": "completed"}],
        "g4",
        "g1",
        None,
    )


def test_a_hook_kept_from_before_it_sent_every_event_is_hooked_again(state_root: Path) -> None:
    kept({REPO: {"id": 9, "last": "g1"}, "yasyf/cc-skills": {"id": 10, "last": "g7", "events": receiver.EVENTS}})
    assert REAL_UNHOOKED({REPO, "yasyf/cc-skills", "yasyf/cc-slack"}) == [REPO, "yasyf/cc-slack"]


def test_a_thread_making_the_secret_while_another_thread_of_its_process_makes_it_reads_the_one_secret_and_leaves_no_file_behind(home: None, monkeypatch: pytest.MonkeyPatch) -> None:
    link, made_meanwhile = os.link, []

    def another_thread_made_it_first(made: str, path: Path) -> None:
        monkeypatch.setattr(receiver.os, "link", link)
        made_meanwhile.append(receiver.secret())
        link(made, path)

    monkeypatch.setattr(receiver.os, "link", another_thread_made_it_first)
    assert receiver.secret() == made_meanwhile[0]
    assert [path.name for path in receiver.home().iterdir()] == ["secret"]


def test_a_newer_install_takes_the_receivers_socket_once_it_took_each_delivery_it_accepted_and_asks_github_nothing(
    state_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    newer = state_root / "cache" / "9.9.9" / "bin" / "drive"
    landed, done = threading.Event(), threading.Event()
    seen: list[str] = []
    started: list[str] = []
    handed: list[list[str]] = []
    answered: list[int] = []
    delivering: list[threading.Thread] = []
    reopened: list[threading.Thread] = []

    class Handed(Exception):
        pass

    class Stopped(Exception):
        pass

    def upgraded() -> Path | None:
        if done.is_set():
            raise Stopped
        if landed.is_set() and not handed:
            return newer
        return None

    def took(repo: str, hook_id: int, guid: str, event: str, payload: dict | None) -> None:
        if guid == "g1":
            landed.set()
        seen.append(guid)
        if guid == "g2":
            done.set()

    def deliver(guid: str) -> None:
        body = json.dumps({"repository": {"full_name": REPO}, "pull_request": {"number": 42}}).encode()
        signed = "sha256=" + hmac.new(receiver.secret().encode(), body, hashlib.sha256).hexdigest()
        answered.append(post(f"http://{dashboard.LOOPBACK}:{port}/", body, {"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": guid, "X-GitHub-Hook-ID": "9", "X-Hub-Signature-256": signed}))

    def delivered_from(guid: str) -> None:
        delivering.append(threading.Thread(target=deliver, args=(guid,), daemon=True))
        delivering[-1].start()

    def reopen(argv: list[str]) -> None:
        with contextlib.suppress(Stopped):
            cli.main(argv)

    def execv(executable: str, argv: list[str]) -> None:
        handed.append(argv)
        seen.append("handed")
        inherited = [f"--listening={os.dup(int(arg.removeprefix('--listening=')))}" if arg.startswith("--listening=") else arg for arg in argv]
        delivered_from("g2")
        reopened.append(threading.Thread(target=reopen, args=(inherited[3:],), daemon=True))
        reopened[0].start()
        raise Handed

    def install_lands() -> None:
        while not receiver.answering():
            time.sleep(0.05)
        delivered_from("g1")

    with socket.create_server((dashboard.LOOPBACK, 0)) as probe:
        port = probe.getsockname()[1]
    receiver.home().mkdir(parents=True)
    state.append_line(receiver.deliveries_path(), receiver.DELIVERY, repo=REPO, event="ping", guid="g0")
    monkeypatch.setattr(receiver, "PORT", port)
    monkeypatch.setattr(receiver, "WAIT_SECONDS", 0.05)
    monkeypatch.setattr(receiver, "upgraded", upgraded)
    monkeypatch.setattr(receiver, "took", took)
    monkeypatch.setattr(receiver, "funnel", lambda: started.append("funnel"))
    monkeypatch.setattr(receiver, "caught_up", lambda: started.append("caught up"))
    monkeypatch.setattr(receiver, "took_builds", lambda event, links: started.append(f"builds {event}"))
    monkeypatch.setattr(receiver.launchd, "session_env", lambda: {})
    monkeypatch.setattr(dashboard, "tailnet", lambda: dashboard.Tailnet("mac", "mac.tail.ts.net", ["100.64.0.1"], "me@example.com", True))
    monkeypatch.setattr(receiver.os, "execv", execv)
    threading.Thread(target=install_lands, daemon=True).start()
    with pytest.raises(Handed):
        receiver.main(None)
    for thread in [*reopened, *delivering]:
        thread.join(10)
    [argv] = handed
    assert argv[:-1] == [sys.executable, "-I", str(newer), "receive"]
    assert re.fullmatch(r"--listening=\d+", argv[-1])
    assert (seen, answered, started) == (["g1", "handed", "g2"], [200, 200], ["funnel", "caught up", "builds missed"])


def test_a_receiver_handed_the_socket_by_an_install_that_kept_no_deliveries_log_asks_github_what_it_missed(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[str] = []

    class Stopped(Exception):
        pass

    def upgraded() -> None:
        raise Stopped

    monkeypatch.setattr(receiver, "upgraded", upgraded)
    monkeypatch.setattr(receiver, "funnel", lambda: started.append("funnel"))
    monkeypatch.setattr(receiver, "caught_up", lambda: started.append("caught up"))
    monkeypatch.setattr(receiver, "took_builds", lambda event, links: started.append(f"builds {event}"))
    monkeypatch.setattr(receiver.launchd, "session_env", lambda: {})
    monkeypatch.setattr(dashboard, "tailnet", lambda: dashboard.Tailnet("mac", "mac.tail.ts.net", ["100.64.0.1"], "me@example.com", True))
    with socket.create_server((dashboard.LOOPBACK, 0)) as listening, pytest.raises(Stopped):
        receiver.main(os.dup(listening.fileno()))
    assert started == ["caught up", "builds missed"]


def test_a_delivery_is_logged_once_as_its_own_facts_and_kept_as_its_hooks_last_reading_no_drive(home: None, unread: None, capsys: pytest.CaptureFixture[str]) -> None:
    receiver.took(REPO, 9, "g1", "check_run", {"check_run": {"head_sha": "aaa", "pull_requests": [{"number": 42}]}})
    receiver.took(REPO, 9, "g2", "status", {"sha": "bbb", "branches": [{"name": "stacked"}], "repository": {"default_branch": "main"}})
    receiver.took(REPO, 9, "g3", "push", {"ref": "refs/heads/fix-it"})
    receiver.took(REPO, 9, "g4", "ping", {"zen": "Keep it logically awesome."})
    assert (delivered(), receiver.hooks()) == (
        [
            {"repo": REPO, "event": "check_run", "guid": "g1", "numbers": [42], "head": "aaa"},
            {"repo": REPO, "event": "status", "guid": "g2", "head": "bbb", "branches": ["stacked"]},
            {"repo": REPO, "event": "push", "guid": "g3", "base": "fix-it"},
            {"repo": REPO, "event": "ping", "guid": "g4"},
        ],
        {REPO: {"id": 9, "last": "g4"}},
    )
    assert [line.split(" ", 1)[1] for line in capsys.readouterr().out.splitlines()] == [f"check_run g1 {REPO}", f"status g2 {REPO}", f"push g3 {REPO}", f"ping g4 {REPO}"]


@pytest.fixture
def listening(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, list[tuple]]]:
    taken: list[tuple] = []
    monkeypatch.setattr(receiver, "took", lambda *delivery: taken.append(delivery))
    server = receiver.serving(socket.create_server((dashboard.LOOPBACK, 0)), SECRET)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://{dashboard.LOOPBACK}:{server.server_address[1]}/", taken
    server.shutdown()
    server.server_close()


def post(address: str, body: bytes, headers: dict[str, str]) -> int:
    request = urllib.request.Request(address, body, headers | {"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as answer:
            return answer.status
    except urllib.error.HTTPError as refused:
        return refused.code


def test_the_receiver_takes_only_deliveries_signed_with_the_webhook_secret(listening: tuple[str, list[tuple]]) -> None:
    address, taken = listening
    payload = {"repository": {"full_name": REPO}, "pull_request": {"number": 42}}
    body = json.dumps(payload).encode()
    signed = "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()
    forged = "sha256=" + hmac.new(b"another secret", body, hashlib.sha256).hexdigest()
    github_headers = {"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "g1", "X-GitHub-Hook-ID": "9"}
    assert [post(address, body, github_headers | ({"X-Hub-Signature-256": signature} if signature else {})) for signature in (None, forged, signed)] == [401, 401, 200]
    assert taken == [(REPO, 9, "g1", "pull_request", payload)]


@pytest.mark.parametrize("burst", [1, 100])
def test_a_burst_of_deliveries_is_taken_on_the_one_thread_that_accepts_them_and_each_answered(
    listening: tuple[str, list[tuple]], monkeypatch: pytest.MonkeyPatch, burst: int
) -> None:
    address, _ = listening
    threads: list[int] = []
    monkeypatch.setattr(receiver, "took", lambda *delivery: threads.append(threading.get_ident()))
    body = json.dumps({"repository": {"full_name": REPO}, "pull_request": {"number": 42}}).encode()
    signed = "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()

    def deliver(guid: int) -> int:
        return post(address, body, {"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": f"g{guid}", "X-GitHub-Hook-ID": "9", "X-Hub-Signature-256": signed})

    with ThreadPoolExecutor(burst) as pool:
        answered = list(pool.map(deliver, range(burst)))
    assert (answered, len(threads), len(set(threads))) == ([200] * burst, burst, 1)


def test_the_receiver_takes_only_buildkite_deliveries_signed_with_the_webhook_secret_over_their_timestamp_and_body(
    listening: tuple[str, list[tuple]], monkeypatch: pytest.MonkeyPatch
) -> None:
    address, _ = listening
    taken: list[tuple] = []
    monkeypatch.setattr(receiver, "took_builds", lambda event, links: taken.append((event, links)))
    body = json.dumps({"event": "build.finished", "build": {"web_url": BUILD, "state": "blocked"}}).encode()
    made = hmac.new(SECRET, b"1794640000." + body, hashlib.sha256).hexdigest()
    forged = hmac.new(b"another secret", b"1794640000." + body, hashlib.sha256).hexdigest()
    signatures = [None, f"timestamp=1794640000,signature={forged}", f"timestamp=1794649999,signature={made}", f"timestamp=1794640000,signature={made}"]
    assert [post(address, body, {"X-Buildkite-Event": "build.finished"} | ({"X-Buildkite-Signature": signature} if signature else {})) for signature in signatures] == [401, 401, 401, 200]
    assert taken == [("build.finished", [BUILD])]


def test_a_buildkite_delivery_is_logged_once_naming_its_builds_reading_no_drive_and_a_missed_one_names_every_build(home: None, unread: None, capsys: pytest.CaptureFixture[str]) -> None:
    receiver.took_builds("missed", None)
    assert receiver.buildkite_silent()
    receiver.took_builds("build.finished", [BUILD])
    assert not receiver.buildkite_silent()
    receiver.took_builds("missed", None)
    assert builds_delivered() == [("missed", None), ("build.finished", [BUILD]), ("missed", None)]
    assert [line.split(" ", 1)[1] for line in capsys.readouterr().out.splitlines()] == ["buildkite missed: every build a drive holds", f"buildkite build.finished: {BUILD}", "buildkite missed: every build a drive holds"]


def answers(monkeypatch: pytest.MonkeyPatch, answered: dict[str, object]) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(cmd: list[str], stdin: str | None = None, **_: object) -> str:
        calls.append(cmd if stdin is None else [*cmd, stdin])
        if isinstance(found := next(answer for path, answer in answered.items() if path in cmd), Exception):
            raise found
        return json.dumps(found)

    monkeypatch.setattr(github, "run", run)
    return calls


def kept(hooks: dict[str, dict]) -> None:
    with receiver.hooks_kept() as hooked:
        hooked.update(hooks)


def test_a_receiver_starting_takes_every_open_pr_of_a_repository_whose_hook_delivered_since_its_last_as_delivered(
    home: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept({REPO: {"id": 9, "last": "g1"}, "yasyf/cc-skills": {"id": 10, "last": "g7"}})
    receiver.took_builds("missed", None)
    answers(monkeypatch, {f"repos/{REPO}/hooks/9/deliveries?per_page=1": [{"guid": "g3"}], "repos/yasyf/cc-skills/hooks/10/deliveries?per_page=1": [{"guid": "g7"}]})
    receiver.caught_up()
    assert (delivered(), receiver.hooks()) == (
        [{"repo": REPO, "event": "missed", "guid": "g3"}],
        {REPO: {"id": 9, "last": "g3", "ci": "g3", "release": "g3"}, "yasyf/cc-skills": {"id": 10, "last": "g7"}},
    )


def test_a_receiver_starting_on_an_empty_deliveries_log_takes_every_hooks_open_prs_as_delivered_since_its_predecessor_logged_into_each_drive(
    home: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept({REPO: {"id": 9, "last": "g3"}})
    answers(monkeypatch, {f"repos/{REPO}/hooks/9/deliveries?per_page=1": [{"guid": "g3"}]})
    receiver.caught_up()
    assert delivered() == [{"repo": REPO, "event": "missed", "guid": "g3"}]


def test_a_hook_whose_deliveries_github_does_not_list_is_forgotten_for_the_watch_to_make_again(home: None, monkeypatch: pytest.MonkeyPatch) -> None:
    kept({REPO: {"id": 9, "last": "g1"}})
    answers(monkeypatch, {f"repos/{REPO}/hooks/9/deliveries?per_page=1": github.GithubError("gh: Not Found (HTTP 404)")})
    receiver.caught_up()
    assert (delivered(), receiver.hooks()) == ([], {})


@pytest.mark.parametrize(("listed", "method", "path"), [([{"id": 9, "config": {"url": "https://mac.tail.ts.net:8443/github"}}], "PATCH", f"repos/{REPO}/hooks/9"), ([], "POST", f"repos/{REPO}/hooks")])
def test_a_repository_is_hooked_once_at_the_receivers_address_signed_with_its_secret(
    state_root: Path, monkeypatch: pytest.MonkeyPatch, listed: list[dict], method: str, path: str
) -> None:
    monkeypatch.setattr(dashboard, "tailnet", lambda: dashboard.Tailnet("mac", "mac.tail.ts.net", ["100.64.0.1"], "me@example.com", True))
    monkeypatch.setattr(receiver, "moment", lambda: "2026-11-13T01:00:00.000-08:00")
    kept({REPO: {"id": 9, "last": "g1"}})
    calls = answers(monkeypatch, {"--input": {"id": 9}, f"repos/{REPO}/hooks?per_page=100": listed + [{"id": 5, "config": {"url": "https://ci.example.com/hook"}}], f"repos/{REPO}": True})
    hooked = REAL_HOOK(REPO)
    [_, _, [*made, config]] = calls
    assert (hooked, made[-5:], json.loads(config), receiver.hooks()) == (
        True,
        ["-X", method, path, "--input", "-"],
        {"active": True, "events": receiver.EVENTS, "config": {"url": "https://mac.tail.ts.net:8443/github", "content_type": "json", "secret": receiver.secret(), "insecure_ssl": "0"}},
        {REPO: {"id": 9, "last": "g1", "events": receiver.EVENTS, "ci": "2026-11-13T01:00:00.000-08:00", "release": "2026-11-13T01:00:00.000-08:00"}},
    )
    assert (receiver.home() / "secret").stat().st_mode & 0o777 == 0o600


def test_a_repository_github_lets_this_account_no_admin_of_gets_no_hook(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = answers(monkeypatch, {"repos/stablyai/orca": False})
    assert (REAL_HOOK("stablyai/orca"), calls, receiver.hooks()) == (False, [["gh", "api", "repos/stablyai/orca", "--jq", ".permissions.admin"]], {})


def test_a_hook_github_refuses_names_the_gh_call_it_refused(state_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dashboard, "tailnet", lambda: dashboard.Tailnet("mac", "mac.tail.ts.net", ["100.64.0.1"], "me@example.com", True))
    answers(monkeypatch, {f"repos/{REPO}/hooks?per_page=100": github.GithubError("gh: Not Found (HTTP 404)"), f"repos/{REPO}": True})
    with pytest.raises(github.GithubError) as refused:
        REAL_HOOK(REPO)
    assert str(refused.value) == f"gh api 'repos/{REPO}/hooks?per_page=100': gh: Not Found (HTTP 404)"


RUN_LINK = f"https://github.com/{REPO}/actions/runs/77"


@pytest.mark.parametrize(
    ("ref", "event", "payload", "settles"),
    [
        (f"{REPO}#42", "pull_request", {"pull_request": {"number": 42}}, True),
        (f"{REPO}#42", "pull_request", {"pull_request": {"number": 43}}, False),
        (f"https://github.com/{REPO}/pull/42", "check_run", {"check_run": {"head_sha": "zzz", "pull_requests": [{"number": 42}]}}, True),
        (f"{REPO}#42", "status", {"sha": "aaa", "branches": [], "repository": {"default_branch": "main"}}, True),
        (f"{REPO}#43", "status", {"sha": "aaa", "branches": [], "repository": {"default_branch": "main"}}, False),
        (f"{REPO}#42", "missed", None, True),
        (RUN_LINK, "workflow_run", {"action": "in_progress", "workflow_run": {"id": 77, "head_sha": "ccc", "head_branch": "fix-it"}, "repository": {"default_branch": "main"}}, False),
        (RUN_LINK, "workflow_run", {"action": "completed", "workflow_run": {"id": 78, "head_sha": "ccc", "head_branch": "fix-it"}, "repository": {"default_branch": "main"}}, False),
        (RUN_LINK, "workflow_run", {"action": "completed", "workflow_run": {"id": 77, "head_sha": "ccc", "head_branch": "fix-it"}, "repository": {"default_branch": "main"}}, True),
    ],
)
def test_drive_wait_ends_on_a_delivery_naming_its_pr_by_number_or_tracked_head_or_its_run_completing(home: None, ref: str, event: str, payload: dict | None, settles: bool) -> None:
    want = receiver.wanted(ref, {f"{REPO}#42": "aaa"})
    receiver.took(REPO, 9, "g5", event, payload)
    [delivery] = [line for line, _ in state.read_lines(receiver.deliveries_path()) if line["kind"] == receiver.DELIVERY]
    assert receiver.settles(want, delivery) == settles


@pytest.mark.skipif(not hasattr(select, "kqueue"), reason="drive wait blocks on kqueue, which macOS alone has")
def test_drive_wait_blocks_until_the_receiver_appends_the_delivery_it_waits_on_and_gives_up_after_its_timeout(home: None) -> None:
    receiver.took(REPO, 9, "g0", "pull_request", {"pull_request": {"number": 42}})
    want = receiver.wanted(f"{REPO}#42", {})
    started = time.monotonic()
    assert receiver.waited(want, 0.2) == (None, state.lines_end(receiver.deliveries_path()))
    assert time.monotonic() - started >= 0.2
    later = threading.Timer(0.2, lambda: [receiver.took(REPO, 9, guid, "pull_request", {"pull_request": {"number": number}}) for guid, number in (("g1", 43), ("g2", 42))])
    later.start()
    try:
        assert receiver.waited(want, 5)[0]["guid"] == "g2"
    finally:
        later.join()


@pytest.mark.skipif(not hasattr(select, "kqueue"), reason="drive wait blocks on kqueue, which macOS alone has")
def test_drive_wait_given_the_offset_the_last_one_ended_at_ends_on_a_delivery_that_came_between_them(home: None) -> None:
    receiver.took(REPO, 9, "g0", "pull_request", {"pull_request": {"number": 43}})
    ended = state.lines_end(receiver.deliveries_path())
    receiver.took(REPO, 9, "g1", "pull_request", {"pull_request": {"number": 42}})
    delivery, offset = receiver.waited(receiver.wanted(f"{REPO}#42", {}), 0, ended)
    assert (delivery["guid"], offset) == ("g1", state.lines_end(receiver.deliveries_path()))
