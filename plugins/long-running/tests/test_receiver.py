import hashlib
import hmac
import json
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from drive import dashboard, github, receiver, state

REPO = "Forge-AI/monorepo"
KEY, OTHER, MERGED = f"{REPO}#42", f"{REPO}#43", f"{REPO}#41"
REAL_HOOK = receiver.hook
REAL_UNHOOKED = receiver.unhooked
OPEN = {42: {"head": "aaa", "base": "main", "branch": "fix-it"}, 43: {"head": "bbb", "base": "fix-it", "branch": "stacked"}}
SECRET = b"the webhook secret"


@pytest.fixture
def demo(state_root: Path) -> str:
    data = state.new_drive("demo", "coordinator", "session-under-test", "/work")
    for key, head, shown in ((KEY, "aaa", "open"), (OTHER, "bbb", "open"), (MERGED, "aaa", "merged")):
        data["prs"][key] = state.new_pr(state.pr_url(key), "j1", "o1") | {"head": head, "state": shown}
    state.create(data)
    state.create(state.new_drive("quiet", "coordinator", "session-under-test", "/work"))
    return "demo"


def delivered(name: str, offset: int = 0) -> list[dict]:
    return [{field: event[field] for field in ("repo", "numbers", "event", "guid")} for event, _ in state.read_events(name, offset) if event["kind"] == receiver.DELIVERY]


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
    assert receiver.named(event, payload, OPEN) == named


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


def test_a_workflow_run_finishing_on_main_moves_its_repositorys_ci_mark_and_writes_no_drive_a_line(demo: str) -> None:
    kept({REPO: {"id": 9, "last": "g1", "events": receiver.EVENTS, "ci": "g1", "release": "g1"}})
    receiver.took(REPO, 9, "g4", "workflow_run", {"action": "completed", "workflow_run": {"head_branch": "main"}, "repository": {"default_branch": "main"}})
    assert (delivered(demo), receiver.heard(REPO, "ci"), receiver.heard(REPO, "release"), receiver.heard("yasyf/cc-skills", "ci")) == ([], "g4", "g1", None)


def test_a_hook_kept_from_before_it_sent_every_event_is_hooked_again(state_root: Path) -> None:
    kept({REPO: {"id": 9, "last": "g1"}, "yasyf/cc-skills": {"id": 10, "last": "g7", "events": receiver.EVENTS}})
    assert REAL_UNHOOKED({REPO, "yasyf/cc-skills", "yasyf/cc-slack"}) == [REPO, "yasyf/cc-slack"]


@pytest.mark.parametrize(("code", "restarts"), [(None, False), ("same", False), ("other", True)])
def test_the_receiver_restarts_only_for_an_install_whose_receiver_differs_from_its_own(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: str | None, restarts: bool) -> None:
    newest = tmp_path / "long-running" / "1.0.699"
    (newest / "lib" / "drive").mkdir(parents=True)
    own = Path(receiver.__file__).read_bytes()
    (newest / "lib" / "drive" / "receiver.py").write_bytes(own if code == "same" else own + b"\n")
    monkeypatch.setattr(receiver, "upgraded", lambda: code and newest / "bin" / "drive")
    assert receiver.rewritten() is restarts


def test_a_delivery_is_written_only_to_each_drive_tracking_an_unmerged_pr_it_names_and_kept_as_its_hooks_last(demo: str, capsys: pytest.CaptureFixture[str]) -> None:
    receiver.took(REPO, 9, "g1", "check_run", {"check_run": {"head_sha": "aaa", "pull_requests": []}})
    receiver.took(REPO, 9, "g2", "ping", {"zen": "Keep it logically awesome."})
    assert (delivered(demo), delivered("quiet"), receiver.hooks()) == (
        [{"repo": REPO, "numbers": [42], "event": "check_run", "guid": "g1"}],
        [],
        {REPO: {"id": 9, "last": "g2"}},
    )
    assert [line.split(" ", 1)[1] for line in capsys.readouterr().out.splitlines()] == [f"check_run g1 {REPO}: demo #42", f"ping g2 {REPO}: no unmerged PR a drive tracks"]



def test_the_reopen_of_a_pr_a_drive_holds_closed_is_written_to_that_drive_while_a_merged_ones_delivery_is_written_nowhere(demo: str) -> None:
    with state.edit(demo) as data:
        data["prs"][KEY]["state"] = "closed"
    receiver.took(REPO, 9, "g1", "pull_request", {"action": "reopened", "pull_request": {"number": 42}})
    receiver.took(REPO, 9, "g2", "pull_request", {"action": "edited", "pull_request": {"number": 41}})
    assert delivered(demo) == [{"repo": REPO, "numbers": [42], "event": "pull_request", "guid": "g1"}]

@pytest.fixture
def listening(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, list[tuple]]]:
    taken: list[tuple] = []
    monkeypatch.setattr(receiver, "took", lambda *delivery: taken.append(delivery))
    server = ThreadingHTTPServer((dashboard.LOOPBACK, 0), receiver.handler(SECRET))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://{dashboard.LOOPBACK}:{server.server_address[1]}/", taken
    server.shutdown()
    server.server_close()


def post(address: str, body: bytes, signature: str | None) -> int:
    headers = {"X-GitHub-Event": "pull_request", "X-GitHub-Delivery": "g1", "X-GitHub-Hook-ID": "9", "Content-Type": "application/json"}
    request = urllib.request.Request(address, body, headers | ({"X-Hub-Signature-256": signature} if signature else {}), method="POST")
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
    assert [post(address, body, signature) for signature in (None, forged, signed)] == [401, 401, 200]
    assert taken == [(REPO, 9, "g1", "pull_request", payload)]


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
    demo: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept({REPO: {"id": 9, "last": "g1"}, "yasyf/cc-skills": {"id": 10, "last": "g7"}})
    answers(monkeypatch, {f"repos/{REPO}/hooks/9/deliveries?per_page=1": [{"guid": "g3"}], "repos/yasyf/cc-skills/hooks/10/deliveries?per_page=1": [{"guid": "g7"}]})
    receiver.caught_up()
    assert (delivered(demo), receiver.hooks()) == (
        [{"repo": REPO, "numbers": [42, 43], "event": "missed", "guid": "g3"}],
        {REPO: {"id": 9, "last": "g3", "ci": "g3", "release": "g3"}, "yasyf/cc-skills": {"id": 10, "last": "g7"}},
    )


def test_a_hook_whose_deliveries_github_does_not_list_is_forgotten_for_the_watch_to_make_again(demo: str, monkeypatch: pytest.MonkeyPatch) -> None:
    kept({REPO: {"id": 9, "last": "g1"}})
    answers(monkeypatch, {f"repos/{REPO}/hooks/9/deliveries?per_page=1": github.GithubError("gh: Not Found (HTTP 404)")})
    receiver.caught_up()
    assert (delivered(demo), receiver.hooks()) == ([], {})


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
