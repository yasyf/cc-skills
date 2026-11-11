import json
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest
from drive import dashboard, install, state

SHA = "abc123def4567890"
REPO = "yasyf/cc-skills"
SHOW_MARKETPLACE = ["show", f"{install.DEFAULT_BRANCH}:.claude-plugin/marketplace.json"]
IDLE = {"ok": True, "result": {"wait": {"condition": "tui-idle", "satisfied": True, "status": "running", "exitCode": None}}}
BUSY = {"ok": False, "error": {"code": "timeout", "message": "timeout"}}
SENT = {"ok": True, "result": {"send": {"accepted": True}}}


class FakeGit:
    def __init__(self, config: Path, refs: list[str], holding: set[str], call_seconds: float, path: str | None) -> None:
        self.config = config
        self.refs = refs
        self.path = path
        self.holding = holding
        self.call_seconds = call_seconds
        self.now = 1000.0
        self.calls: list[list[str]] = []
        self.timeouts: list[float] = []
        self.sleeps: list[float] = []
        self.orca_calls: list[list[str]] = []
        self.orca_timeouts: list[float] = []
        self.waits: dict[str, dict] = {}
        self.dashboards: dict[str, dict] = {}
        self.installed_sha: str | None = None
        self.together = threading.Barrier(1)

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def run(self, cmd: list[str], **options: object) -> subprocess.CompletedProcess:
        if cmd[0] == "orca":
            return self.orca(cmd, options["timeout"])
        self.calls.append(cmd)
        self.timeouts.append(options["timeout"])
        if self.call_seconds > options["timeout"]:
            self.now += options["timeout"]
            raise subprocess.TimeoutExpired(cmd, options["timeout"])
        self.now += self.call_seconds
        if cmd[0] == "gh":
            return subprocess.CompletedProcess(cmd, 1, "", "gh: API rate limit exceeded (HTTP 403)")
        return subprocess.CompletedProcess(cmd, 0, self.answer(cmd), "")

    def orca(self, cmd: list[str], timeout: float) -> subprocess.CompletedProcess:
        self.orca_calls.append(cmd)
        self.orca_timeouts.append(timeout)
        if cmd[2] == "wait":
            self.together.wait()
        reply = self.waits.get(cmd[4], IDLE) if cmd[2] == "wait" else SENT
        return subprocess.CompletedProcess(cmd, 0 if reply["ok"] else 1, json.dumps(reply), "")

    def typed_into(self, terminal: str) -> list[list[str]]:
        return [call for call in self.orca_calls if call[4] == terminal]

    def next_ref(self) -> str:
        return self.refs.pop(0) if len(self.refs) > 1 else self.refs[0]

    def answer(self, cmd: list[str]) -> str:
        verb = cmd[3:] if cmd[:2] == ["git", "-C"] else []
        if verb == SHOW_MARKETPLACE:
            source = self.path or {"source": "git-subdir", "url": f"https://github.com/{REPO}", "ref": self.next_ref()}
            return json.dumps({"name": "skills", "plugins": [{"name": "long-running", "source": source}]})
        if verb[:1] == ["log"]:
            return self.next_ref() + "\n"
        if verb[:2] == ["rev-list", "--count"]:
            return "0\n" if verb[3].removeprefix("^") in self.holding else "4\n"
        if cmd[:3] == ["claude", "plugin", "update"]:
            plugin = cmd[3].partition("@")[0]
            entry = {
                "scope": "user",
                "version": "1.0.3",
                "lastUpdated": "2026-11-09T10:00:00.000Z",
                "installPath": f"/cache/{plugin}/1.0.3",
                "gitCommitSha": self.installed_sha or self.refs[0],
            }
            stale = {"scope": "project", "version": "0.7.2119", "lastUpdated": "2026-11-01T10:00:00.000Z", "installPath": "/cache/old"}
            installed = {"version": 2, "plugins": {cmd[3]: [stale, entry]}}
            (self.config / "plugins" / "installed_plugins.json").write_text(json.dumps(installed))
        return ""

    def git_calls(self) -> list[list[str]]:
        return [call[3:] for call in self.calls if call[:2] == ["git", "-C"]]

    def claude_calls(self) -> list[list[str]]:
        return [call for call in self.calls if call[0] == "claude"]

    def other_calls(self) -> list[list[str]]:
        return [call for call in self.calls if call[0] not in ("git", "claude")]


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state_root: Path) -> Path:
    config = tmp_path / "claude"
    (config / "plugins").mkdir(parents=True)
    known = {
        "skills": {"source": {"source": "github", "repo": REPO}, "installLocation": str(config / "m")},
        "revdiff": {"source": {"source": "git", "url": "https://github.com/umputun/revdiff.git"}},
        "forge": {"source": {"source": "directory", "path": "/somewhere"}},
    }
    (config / "plugins" / "known_marketplaces.json").write_text(json.dumps(known))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    return config


@pytest.fixture
def fake(config: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeGit]:
    def make(refs: list[str], holding: set[str], call_seconds: float = 0, path: str | None = None) -> FakeGit:
        git = FakeGit(config, refs, holding, call_seconds, path)
        monkeypatch.setattr(install.subprocess, "run", git.run)
        monkeypatch.setattr(install, "clock", git.clock)
        monkeypatch.setattr(install, "sleep", git.sleep)
        monkeypatch.setattr(dashboard, "serving", lambda name, port: git.dashboards.get(name))
        return git

    return make


def test_a_tag_that_already_contains_the_sha_updates_and_prints_the_version(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]
) -> None:
    git = fake(["long-running--v1.0.3"], {"long-running--v1.0.3"})
    code = install.main("long-running@skills", SHA, None, 900)
    assert code == 0
    assert capsys.readouterr().out.strip() == "installed long-running@skills 1.0.3"
    assert git.sleeps == []
    assert git.git_calls() == [
        ["remote", "add", "origin", f"https://github.com/{REPO}"],
        [*install.FETCH, f"+HEAD:{install.DEFAULT_BRANCH}"],
        SHOW_MARKETPLACE,
        [*install.FETCH, "+refs/tags/long-running--v1.0.3:refs/tags/long-running--v1.0.3"],
        ["rev-list", "--count", SHA, "^long-running--v1.0.3"],
        [*install.FETCH, "long-running--v1.0.3"],
        ["rev-list", "--count", SHA, "^long-running--v1.0.3"],
    ]
    assert git.claude_calls() == [
        ["claude", "plugin", "marketplace", "update", "skills"],
        ["claude", "plugin", "update", "long-running@skills"],
    ]


def test_an_update_left_at_a_commit_without_the_sha_is_refused_naming_both(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]
) -> None:
    start_drive("demo", communicator="term_comm")
    git = fake(["long-running--v1.0.3"], {"long-running--v1.0.3"})
    git.installed_sha = "0ldc0mm1t5ha99"
    assert install.main("long-running@skills", SHA, None, 900) == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err.strip() == f"drive install: long-running@skills is still at 1.0.3 (0ldc0mm1t5ha) after the update, which does not hold {SHA[:12]}, the sha awaited"
    assert git.git_calls()[-2:] == [[*install.FETCH, "0ldc0mm1t5ha99"], ["rev-list", "--count", SHA, "^0ldc0mm1t5ha99"]]
    assert git.orca_calls == []


def test_a_github_api_rate_limit_cannot_stall_an_install(fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]) -> None:
    git = fake(["long-running--v1.0.3"], {"long-running--v1.0.3"})
    assert install.main("long-running@skills", SHA, None, 540) == 0
    assert capsys.readouterr().out.strip() == "installed long-running@skills 1.0.3"
    assert (git.sleeps, [call for call in git.calls if call[0] == "gh"]) == ([], [])


def test_a_tag_behind_the_sha_is_polled_until_a_new_tag_catches_up(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]
) -> None:
    git = fake(["long-running--v1.0.2", "long-running--v1.0.2", "long-running--v1.0.3"], {"long-running--v1.0.3"})
    code = install.main("long-running@skills", SHA, None, 900)
    assert code == 0
    assert git.sleeps == [20, 20]
    assert capsys.readouterr().out.strip() == "installed long-running@skills 1.0.3"
    assert len(git.claude_calls()) == 2


def test_a_timeout_names_the_last_ref_and_installs_nothing(fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]) -> None:
    git = fake(["long-running--v1.0.1", "long-running--v1.0.2"], set())
    code = install.main("long-running@skills", SHA, None, 60)
    assert code == 1
    assert git.sleeps == [20, 20, 20]
    assert "last ref long-running--v1.0.2" in capsys.readouterr().err
    assert git.claude_calls() == []


@pytest.mark.parametrize(
    ("path", "manifest"),
    [("./captain_hook", "captain_hook/.claude-plugin/plugin.json"), ("./", ".claude-plugin/plugin.json")],
)
def test_a_path_source_waits_for_the_manifest_commit_that_contains_the_sha(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str], path: str, manifest: str
) -> None:
    git = fake(["0ld5ync", "n3w5ync"], {"n3w5ync"}, path=path)
    assert install.main("long-running@skills", SHA, None, 900) == 0
    assert ["log", "-1", "--first-parent", "--format=%H", install.DEFAULT_BRANCH, "--", manifest] in git.git_calls()
    assert ["rev-list", "--count", SHA, "^n3w5ync"] in git.git_calls()
    assert not [call for call in git.git_calls() if "refs/tags/" in call[-1]]
    assert git.sleeps == [20]
    assert capsys.readouterr().out.strip() == "installed long-running@skills 1.0.3"
    assert len(git.claude_calls()) == 2


def test_slow_reads_count_against_the_timeout(fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]) -> None:
    git = fake(["long-running--v1.0.2"], set(), call_seconds=8)
    assert install.main("long-running@skills", SHA, None, 60) == 1
    assert git.now == 1060
    assert git.timeouts == [60, 52, 44, 36, 28, 20]
    assert git.sleeps == [12]
    assert "timed out after 60s" in capsys.readouterr().err
    assert git.claude_calls() == []


def test_a_hung_read_is_cut_at_the_deadline(fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]) -> None:
    git = fake(["long-running--v1.0.3"], {"long-running--v1.0.3"}, call_seconds=3600)
    assert install.main("long-running@skills", SHA, None, 60) == 1
    assert git.now == 1060
    assert "ran past the deadline" in capsys.readouterr().err
    assert git.claude_calls() == []


def test_each_update_gets_only_the_time_left(fake: Callable[..., FakeGit]) -> None:
    git = fake(["unused"], set(), call_seconds=5)
    assert install.main("long-running@skills", None, None, 900) == 0
    assert git.timeouts == [900, 895]


def test_without_a_sha_it_installs_what_the_marketplace_names_now(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]
) -> None:
    git = fake(["unused"], set())
    assert install.main("long-running@skills", None, None, 900) == 0
    assert [call[0] for call in git.calls] == ["claude", "claude"]
    assert capsys.readouterr().out.strip() == "installed long-running@skills 1.0.3"


def test_an_explicit_repo_wins_over_the_known_marketplace(fake: Callable[..., FakeGit]) -> None:
    git = fake(["long-running--v1.0.3"], {"long-running--v1.0.3"})
    assert install.main("long-running@skills", SHA, "someone/fork", 900) == 0
    assert git.git_calls()[0] == ["remote", "add", "origin", "https://github.com/someone/fork"]


@pytest.mark.parametrize(("marketplace", "repo"), [("skills", REPO), ("revdiff", "umputun/revdiff")])
def test_the_repo_resolves_from_known_marketplaces(config: Path, marketplace: str, repo: str) -> None:
    assert install.marketplace_repo(marketplace) == repo


def test_a_marketplace_with_no_github_repo_asks_for_repo(fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]) -> None:
    fake(["unused"], set())
    assert install.main("plugin@forge", SHA, None, 900) == 1
    assert "pass --repo" in capsys.readouterr().err


def test_a_plugin_the_marketplace_does_not_name_fails_at_once(fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]) -> None:
    git = fake(["long-running--v1.0.3"], {"long-running--v1.0.3"})
    assert install.main("cci@skills", SHA, None, 900) == 1
    assert "the skills marketplace names no plugin cci" in capsys.readouterr().err
    assert (git.sleeps, git.claude_calls()) == ([], [])


def git_in(repo: Path, *args: str) -> str:
    identity = ["-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
    return subprocess.run(["git", "-C", str(repo), *identity, *args], check=True, capture_output=True, text=True).stdout.strip()


def commit_files(repo: Path, message: str, files: dict[str, str]) -> str:
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    git_in(repo, "add", "-A")
    git_in(repo, "commit", "-q", "-m", message)
    return git_in(repo, "rev-parse", "HEAD")


def test_a_release_merged_into_the_default_branch_contains_its_merge_commit(tmp_path: Path) -> None:
    origin = tmp_path / "origin"
    origin.mkdir()
    git_in(origin, "init", "-q", "-b", "main")
    marketplace = {"name": "cc-inbox", "plugins": [{"name": "cc-inbox", "source": "./plugin"}]}
    manifest = "plugin/.claude-plugin/plugin.json"
    commit_files(origin, "base", {".claude-plugin/marketplace.json": json.dumps(marketplace), manifest: '{"version": "1"}'})
    commit_files(origin, "code", {"code.go": "package main"})
    git_in(origin, "checkout", "-q", "-b", "sync")
    commit_files(origin, "sync to 2", {manifest: '{"version": "2"}'})
    git_in(origin, "checkout", "-q", "main")
    git_in(origin, "merge", "-q", "--no-ff", "-m", "merge sync", "sync")
    merge = git_in(origin, "rev-parse", "HEAD")
    clone = str(tmp_path / "clone")
    deadline = install.clock() + 120
    install.run(["git", "init", "--bare", "--quiet", clone], deadline)
    install.git(clone, "remote", "add", "origin", origin.as_uri(), deadline=deadline)
    ref = install.release_ref(clone, "cc-inbox", deadline)
    assert ref == merge
    assert install.contains(clone, ref, merge, deadline)


def test_installing_cc_inbox_moves_the_cci_daemon_onto_the_new_build(fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]) -> None:
    git = fake(["unused"], set())
    assert install.main("cc-inbox@skills", None, None, 900) == 0
    assert git.other_calls() == [["/cache/cc-inbox/1.0.3/bin/cci", "serve"]]
    assert capsys.readouterr().out.strip() == "installed cc-inbox@skills 1.0.3"


def test_other_plugins_run_nothing_after_the_update(fake: Callable[..., FakeGit]) -> None:
    git = fake(["unused"], set())
    assert install.main("long-running@skills", None, None, 900) == 0
    assert git.other_calls() == []


def test_installs_run_at_once_take_turns_on_the_plugin_updates(fake: Callable[..., FakeGit], monkeypatch: pytest.MonkeyPatch) -> None:
    git = fake(["unused"], set())
    running: list[list[str]] = []
    most_at_once: list[int] = []

    def slow_claude(cmd: list[str], **options: object) -> subprocess.CompletedProcess:
        if cmd[0] != "claude":
            return git.run(cmd, **options)
        running.append(cmd)
        most_at_once.append(len(running))
        time.sleep(0.05)
        running.remove(cmd)
        return git.run(cmd, **options)

    monkeypatch.setattr(install.subprocess, "run", slow_claude)
    keys = ["long-running@skills", "cc-inbox@skills", "codex@skills"]
    with ThreadPoolExecutor() as pool:
        versions = list(pool.map(lambda key: install.install(key, None, None, git.clock() + 900, 900), keys))
    assert versions == ["1.0.3"] * 3
    assert len(git.claude_calls()) == 6
    assert max(most_at_once) == 1


def start_drive(name: str, **terminals: str | None) -> None:
    (first, terminal), *rest = terminals.items()
    data = state.new_drive(name, first, f"{name}-{first}", "/work", None, terminal)
    for role, held in rest:
        data["roles"][role] = {**data["roles"][first], "session": f"{name}-{role}", "terminal": held}
    state.create(data)


def waited(terminal: str, wait_ms: str) -> list[str]:
    return ["orca", "terminal", "wait", "--terminal", terminal, "--for", "tui-idle", "--timeout-ms", wait_ms, "--json"]


def sent(terminal: str) -> list[str]:
    return ["orca", "terminal", "send", "--terminal", terminal, "--text", "/reload-plugins", "--enter", "--wait-submit", "10", "--json"]


def by_hand(terminal: str) -> str:
    return f"send it with: orca terminal send --terminal {terminal} --text /reload-plugins --enter"


def test_an_install_types_reload_plugins_into_both_drive_sessions_once_each_is_idle(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]
) -> None:
    start_drive("demo", communicator="term_comm", coordinator="term_coord")
    git = fake(["unused"], set())
    git.together = threading.Barrier(2, timeout=5)
    assert install.main("long-running@skills", None, None, 900) == 0
    assert git.typed_into("term_comm") == [waited("term_comm", "30000"), sent("term_comm")]
    assert git.typed_into("term_coord") == [waited("term_coord", "30000"), sent("term_coord")]
    assert capsys.readouterr().out.splitlines() == [
        "installed long-running@skills 1.0.3",
        "demo communicator: reloaded in term_comm",
        "demo coordinator: reloaded in term_coord",
    ]


def test_a_session_still_busy_after_the_wait_gets_reload_plugins_queued_behind_its_turn(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]
) -> None:
    start_drive("demo", communicator="term_comm", coordinator="term_coord")
    git = fake(["unused"], set())
    git.together = threading.Barrier(2, timeout=5)
    git.waits["term_coord"] = BUSY
    assert install.main("long-running@skills", None, None, 900) == 0
    assert git.typed_into("term_comm") == [waited("term_comm", "30000"), sent("term_comm")]
    assert git.typed_into("term_coord") == [waited("term_coord", "30000"), sent("term_coord")]
    assert capsys.readouterr().out.splitlines()[1:] == [
        "demo communicator: reloaded in term_comm",
        "demo coordinator: busy for 30s, so /reload-plugins is queued in term_coord and runs when its turn ends",
    ]


def test_an_install_near_its_deadline_waits_only_the_time_left(fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]) -> None:
    start_drive("demo", communicator="term_comm")
    git = fake(["unused"], set(), call_seconds=5)
    git.waits["term_comm"] = BUSY
    assert install.main("long-running@skills", None, None, 30) == 0
    assert git.typed_into("term_comm") == [waited("term_comm", "10000"), sent("term_comm")]
    assert sorted(git.orca_timeouts) == [60, 70]
    assert "demo communicator: busy for 10s" in capsys.readouterr().out


def test_drive_reload_reloads_every_drive_session_an_install_already_reloaded(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]
) -> None:
    start_drive("demo", communicator="term_comm", coordinator="term_coord")
    earlier = {"at": state.stamp(state.now() - timedelta(minutes=5)), "version": "1.0.2"}
    (state.drive_dir("demo") / install.RELOADS).write_text(json.dumps({"communicator": earlier, "coordinator": earlier}))
    git = fake(["unused"], set())
    git.together = threading.Barrier(2, timeout=5)
    assert install.reload_main() == 0
    assert git.typed_into("term_comm") == [waited("term_comm", "30000"), sent("term_comm")]
    assert git.typed_into("term_coord") == [waited("term_coord", "30000"), sent("term_coord")]
    assert git.calls == []
    assert capsys.readouterr().out.splitlines() == ["demo communicator: reloaded in term_comm", "demo coordinator: reloaded in term_coord"]


def test_a_terminal_orca_cannot_find_or_that_shows_a_prompt_is_typed_nothing(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]
) -> None:
    start_drive("demo", communicator="term_gone", coordinator="term_menu")
    git = fake(["unused"], set())
    git.together = threading.Barrier(2, timeout=5)
    git.waits["term_gone"] = {"ok": False, "error": {"code": "terminal_not_found", "message": "no terminal term_gone"}}
    git.waits["term_menu"] = {"ok": True, "result": {"wait": {"satisfied": False, "blockedReason": "agent-trust-workspace"}}}
    assert install.main("long-running@skills", None, None, 900) == 0
    assert [call[2] for call in git.orca_calls] == ["wait", "wait"]
    assert capsys.readouterr().out.splitlines()[1:] == [
        f"demo communicator: not reloaded (orca terminal wait: terminal_not_found: no terminal term_gone); {by_hand('term_gone')}",
        f"demo coordinator: not reloaded (orca terminal wait: it shows agent-trust-workspace); {by_hand('term_menu')}",
    ]


def test_a_drive_session_with_no_terminal_recorded_is_skipped_with_a_line(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]
) -> None:
    start_drive("solo", coordinator=None)
    git = fake(["unused"], set())
    assert install.main("long-running@skills", None, None, 900) == 0
    assert git.orca_calls == []
    assert capsys.readouterr().out.splitlines() == [
        "installed long-running@skills 1.0.3",
        "solo communicator: no terminal recorded, skipped",
        "solo coordinator: no terminal recorded, skipped",
    ]


def test_an_install_restarts_each_dashboard_serving_other_code_on_the_new_build(fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]) -> None:
    start_drive("demo", communicator=None)
    start_drive("current", communicator=None)
    start_drive("quiet", communicator=None)
    git = fake(["unused"], set())
    git.dashboards = {"demo": {"code": "/cache/long-running/1.0.2/lib/drive"}, "current": {"code": str(Path("/cache/long-running/1.0.3/lib/drive").resolve())}}
    assert install.main("long-running@skills", None, None, 900) == 0
    assert git.other_calls() == [["/cache/long-running/1.0.3/bin/drive", "dashboard", "--drive", "demo"]]
    assert "demo dashboard: restarted onto 1.0.3" in capsys.readouterr().out.splitlines()


def test_installing_another_plugin_restarts_no_dashboard(fake: Callable[..., FakeGit]) -> None:
    start_drive("demo", communicator=None)
    git = fake(["unused"], set())
    git.dashboards = {"demo": {"code": "/cache/long-running/1.0.2/lib/drive"}}
    assert install.main("cc-inbox@skills", None, None, 900) == 0
    assert git.other_calls() == [["/cache/cc-inbox/1.0.3/bin/cci", "serve"]]


def test_a_session_another_install_reloaded_after_this_update_is_not_reloaded_twice(
    fake: Callable[..., FakeGit], capsys: pytest.CaptureFixture[str]
) -> None:
    start_drive("demo", communicator="term_comm", coordinator="term_coord")
    reloads = state.drive_dir("demo") / install.RELOADS
    stamps = {
        "communicator": {"at": state.stamp(state.now() + timedelta(minutes=1)), "version": "1.0.3"},
        "coordinator": {"at": state.stamp(state.now() - timedelta(hours=1)), "version": "1.0.2"},
    }
    reloads.write_text(json.dumps(stamps))
    git = fake(["unused"], set())
    assert install.main("long-running@skills", None, None, 900) == 0
    assert git.typed_into("term_comm") == []
    assert git.typed_into("term_coord") == [waited("term_coord", "30000"), sent("term_coord")]
    assert capsys.readouterr().out.splitlines() == [
        "installed long-running@skills 1.0.3",
        "demo communicator: already reloaded onto 1.0.3",
        "demo coordinator: reloaded in term_coord",
    ]
    assert json.loads(reloads.read_text())["coordinator"]["version"] == "1.0.3"


def test_a_reload_never_sent_leaves_no_stamp_so_the_next_install_sends_it(fake: Callable[..., FakeGit]) -> None:
    start_drive("demo", communicator="term_comm")
    git = fake(["unused"], set())
    git.waits["term_comm"] = {"ok": False, "error": {"code": "terminal_not_found", "message": "no terminal term_comm"}}
    assert install.main("long-running@skills", None, None, 900) == 0
    assert "communicator" not in json.loads((state.drive_dir("demo") / install.RELOADS).read_text())
