"""Guards for the codex plugin's capt-hook helpers."""

from __future__ import annotations

import importlib.util
import os
import pwd
import re
import sqlite3
import subprocess
import sys
import time
import types
from contextlib import closing
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
COMMON = REPO_ROOT / "plugins" / "codex" / "capt-hook" / "hooks" / "common.py"
INSTALLER = REPO_ROOT / "plugins" / "codex" / "scripts" / "install-binary.sh"
SHIM = REPO_ROOT / "plugin" / "guides" / "sh" / "binrun-shim.sh"
ARM_ONE = "\n# Arm 1:"


@pytest.fixture
def common(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(
        sys.modules, "captain_hook", types.SimpleNamespace(BaseHookEvent=object)
    )
    spec = importlib.util.spec_from_file_location("codex_hooks_common", COMMON)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def plugin(common, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    root = tmp_path / "plugin"
    (root / "bin").mkdir(parents=True)
    launcher = root / "bin" / "codex-ask"
    launcher.write_text('#!/bin/bash\nRUNNER_TAG="v9.9.9"\n')
    launcher.chmod(0o755)
    descriptor = root / "bin" / "codex-ask.binrun"
    descriptor.write_text("{}\n")
    home = tmp_path / "home"
    monkeypatch.setattr(common, "PLUGIN_ROOT", root)
    monkeypatch.setattr(common, "LAUNCHER", launcher)
    monkeypatch.setattr(common, "DESCRIPTOR", descriptor)
    monkeypatch.setattr(common.pwd, "getpwuid", lambda _: types.SimpleNamespace(pw_dir=str(home)))
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setenv("HOME", str(tmp_path / "redirected"))
    monkeypatch.delenv("DAEMONKIT_HOME", raising=False)
    monkeypatch.delenv("BINRUN_BIN", raising=False)
    return types.SimpleNamespace(root=root, launcher=launcher, descriptor=descriptor, home=home)


def executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def test_explicit_binrun_bin_wins(common, plugin, monkeypatch, tmp_path):
    executable(plugin.home / ".daemonkit" / "binrun" / "v9.9.9" / "binrun")
    monkeypatch.setenv("PATH", str(executable(tmp_path / "onpath" / "binrun").parent))
    monkeypatch.setenv("BINRUN_BIN", "/dev/binrun")
    assert common.codex_ask_argv() == ["/dev/binrun", str(plugin.descriptor)]


def test_pinned_runner_under_home_daemonkit(common, plugin, monkeypatch, tmp_path):
    pinned = executable(plugin.home / ".daemonkit" / "binrun" / "v9.9.9" / "binrun")
    monkeypatch.setenv("PATH", str(executable(tmp_path / "onpath" / "binrun").parent))
    assert common.codex_ask_argv() == [str(pinned), str(plugin.descriptor)]


def test_pinned_runner_under_daemonkit_home_override(common, plugin, monkeypatch, tmp_path):
    override = tmp_path / "dk"
    executable(plugin.home / ".daemonkit" / "binrun" / "v9.9.9" / "binrun")
    pinned = executable(override / ".daemonkit" / "binrun" / "v9.9.9" / "binrun")
    monkeypatch.setenv("DAEMONKIT_HOME", str(override))
    assert common.codex_ask_argv() == [str(pinned), str(plugin.descriptor)]


def test_other_tags_runner_is_never_chosen(common, plugin):
    executable(plugin.home / ".daemonkit" / "binrun" / "v0.0.1" / "binrun")
    assert common.codex_ask_argv() == [str(plugin.launcher)]


def test_stale_shared_runner_is_never_chosen(common, plugin, monkeypatch, tmp_path):
    executable(plugin.home / ".daemonkit" / "bin" / "binrun")
    assert common.codex_ask_argv() == [str(plugin.launcher)]
    override = tmp_path / "dk"
    executable(override / "bin" / "binrun")
    executable(override / "binrun" / "v9.9.9" / "binrun")
    monkeypatch.setenv("DAEMONKIT_HOME", str(override))
    assert common.codex_ask_argv() == [str(plugin.launcher)]


def test_path_binrun_without_pinned_runner(common, plugin, monkeypatch, tmp_path):
    found = executable(tmp_path / "onpath" / "binrun")
    monkeypatch.setenv("PATH", str(found.parent))
    assert common.codex_ask_argv() == [str(found), str(plugin.descriptor)]


def shim_prologue() -> str:
    # The fragment, not the rendered launcher: CI renders only after merge.
    body = SHIM.read_text()
    return body[: body.index(ARM_ONE)]


def run_shim_prologue(**env: str) -> subprocess.CompletedProcess[str]:
    argv = ["/bin/bash", "-c", f'{shim_prologue()}\nprintf %s "$RUNNER_BIN"']
    return subprocess.run(argv, capture_output=True, text=True, env=env)


def shim_runner_bin(**env: str) -> Path:
    done = run_shim_prologue(**env)
    assert done.returncode == 0, done.stderr
    return Path(done.stdout)


def test_runner_lookup_mirrors_the_launcher(common):
    shim = INSTALLER.read_text()
    assert common.LAUNCHER.resolve() == INSTALLER.resolve()
    assert re.fullmatch(r"v\d+\.\d+\.\d+", common.RUNNER_TAG.search(shim)[1])
    arms = [shim.index(arm) for arm in ('exec "$BINRUN_BIN"', 'exec "$RUNNER_BIN"', "command -v binrun")]
    assert arms == sorted(arms)


def test_runner_path_matches_the_shim(common, monkeypatch, tmp_path):
    tag = common.RUNNER_TAG.search(SHIM.read_text())[1]

    def pinned() -> Path:
        return common.runner_home() / "binrun" / tag / "binrun"

    monkeypatch.delenv("DAEMONKIT_HOME", raising=False)
    assert common.runner_home() == Path(pwd.getpwuid(os.getuid()).pw_dir) / ".daemonkit"
    assert shim_runner_bin(PATH="/usr/bin:/bin", HOME=str(tmp_path / "redirected")) == pinned()

    override = tmp_path / "dk"
    monkeypatch.setenv("DAEMONKIT_HOME", str(override))
    assert common.runner_home() == override / ".daemonkit"
    assert shim_runner_bin(PATH="/usr/bin:/bin", DAEMONKIT_HOME=str(override)) == pinned()


def test_shim_refuses_an_unresolvable_passwd_home(tmp_path):
    done = run_shim_prologue(PATH=str(tmp_path / "empty"), HOME=str(tmp_path / "redirected"))
    assert done.returncode != 0
    assert str(tmp_path / "redirected") not in done.stdout


def test_falls_back_to_launcher_without_binrun(common, plugin):
    assert common.codex_ask_argv() == [str(plugin.launcher)]


def test_falls_back_to_launcher_without_descriptor(common, plugin, monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(executable(tmp_path / "onpath" / "binrun").parent))
    plugin.descriptor.unlink()
    assert common.codex_ask_argv() == [str(plugin.launcher)]


def test_falls_back_to_path_without_launcher(common, plugin, monkeypatch, tmp_path):
    plugin.launcher.unlink()
    found = executable(tmp_path / "onpath" / "codex-ask")
    monkeypatch.setenv("PATH", str(found.parent))
    assert common.codex_ask_argv() == [str(found)]


def test_no_binary_anywhere(common, plugin):
    plugin.launcher.unlink()
    assert common.codex_ask_argv() is None


def test_service_label_matches_the_daemon_spec(common):
    runtime = (REPO_ROOT / "plugins" / "codex" / "daemon_runtime.go").read_text()
    assert f'codexServiceLabel      = "{common.SERVICE_LABEL}"' in runtime


@pytest.fixture
def state_dir(common, monkeypatch, tmp_path: Path):
    socket = tmp_path / "a" / common.SERVICE_LABEL / "daemon.sock"
    monkeypatch.setattr(common, "daemon_socket", lambda: socket)
    return socket


def test_daemon_down_when_state_dir_has_no_socket(common, state_dir):
    state_dir.parent.mkdir(parents=True)
    assert common.daemon_is_down() is True


def test_daemon_up_when_socket_exists(common, state_dir):
    state_dir.parent.mkdir(parents=True)
    state_dir.touch()
    assert common.daemon_is_down() is False


def test_absent_state_dir_does_not_count_as_down(common, state_dir):
    assert common.daemon_is_down() is False


def test_call_bin_skips_the_spawn_when_the_daemon_is_down(common, plugin, state_dir):
    state_dir.parent.mkdir(parents=True)

    class Ctx:
        def call_cli(self, argv, **kwargs):
            raise AssertionError("spawned with no daemon listening")

    evt = types.SimpleNamespace(ctx=Ctx(), _raw={})
    assert common.call_bin(evt, "agent-inject") is None


def test_call_bin_exports_the_plugin_root(common, plugin, state_dir):
    state_dir.parent.mkdir(parents=True)
    state_dir.touch()
    captured = {}

    class Ctx:
        def call_cli(self, argv, **kwargs):
            captured["argv"] = argv
            captured["env"] = kwargs["env"]
            return "out"

    evt = types.SimpleNamespace(ctx=Ctx(), _raw={"session_id": "s"})
    assert common.call_bin(evt, "agent-inject") == "out"
    assert captured["argv"] == [str(plugin.launcher), "agent-inject"]
    assert captured["env"] == {"BINRUN_PLUGIN_ROOT": str(plugin.root)}


PLANE_DDL = """
CREATE TABLE subjects (id TEXT PRIMARY KEY, session_id TEXT, scope TEXT NOT NULL);
CREATE TABLE directives (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  subject_id TEXT NOT NULL REFERENCES subjects(id),
  agent_id TEXT NOT NULL,
  delivered_at INTEGER
);
"""


@pytest.fixture
def plane(common, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db = tmp_path / "cc-interact-v1" / "state.db"
    monkeypatch.setattr(common, "state_db", lambda: db)
    return db


def seed_plane(db: Path, *statements: str) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.executescript(PLANE_DDL + ";".join(statements))


def plane_event(**raw: str | None) -> types.SimpleNamespace:
    return types.SimpleNamespace(_raw={"session_id": "s", "cwd": "/repo", **raw})


def assert_admits(common) -> None:
    assert common.directive_pending(plane_event()) is True
    assert common.subject_in_scope(plane_event()) is True


def test_absent_store_admits_every_spawn(common, plane):
    assert_admits(common)


def test_store_without_tables_admits_every_spawn(common, plane):
    plane.parent.mkdir(parents=True)
    plane.touch()
    assert_admits(common)


def test_torn_store_admits_every_spawn(common, plane):
    plane.parent.mkdir(parents=True)
    plane.write_bytes(b"SQLite format 3\x00" + b"\xff" * 84)
    assert_admits(common)


def test_unreadable_store_admits_every_spawn(common, plane):
    seed_plane(plane)
    plane.chmod(0)
    try:
        assert_admits(common)
    finally:
        plane.chmod(0o600)


@pytest.mark.parametrize("journal", ["DELETE", "WAL"])
def test_locked_store_admits_without_waiting(common, plane, journal):
    seed_plane(plane)
    with closing(sqlite3.connect(plane, isolation_level=None)) as holder:
        holder.execute(f"PRAGMA journal_mode={journal}")
        holder.execute("PRAGMA locking_mode=EXCLUSIVE")
        holder.execute("BEGIN EXCLUSIVE")
        holder.execute("INSERT INTO subjects VALUES ('held', 's', '/elsewhere')")
        started = time.monotonic()
        assert_admits(common)
        assert time.monotonic() - started < 5
        holder.execute("ROLLBACK")


def test_real_home_is_daemonkits(common, monkeypatch, tmp_path):
    passwd = Path(pwd.getpwuid(os.getuid()).pw_dir)
    monkeypatch.delenv("DAEMONKIT_HOME", raising=False)
    assert common.real_home() == passwd
    monkeypatch.setenv("DAEMONKIT_HOME", "")
    assert common.real_home() == passwd
    monkeypatch.setenv("DAEMONKIT_HOME", str(tmp_path))
    assert common.real_home() == tmp_path
    assert common.daemon_socket() == tmp_path / ".daemonkit" / "a" / common.SERVICE_LABEL / "daemon.sock"
    assert common.state_db() == tmp_path / common.APP_DIR / "cc-interact-v1" / "state.db"


def test_empty_store_admits_nothing(common, plane):
    seed_plane(plane)
    assert common.directive_pending(plane_event()) is False
    assert common.directive_pending(plane_event(agent_id="a1")) is False
    assert common.subject_in_scope(plane_event()) is False


def test_subject_in_scope_matches_scope_only(common, plane):
    seed_plane(plane, "INSERT INTO subjects VALUES ('sub', 'other-session', '/repo')")
    assert common.subject_in_scope(plane_event()) is True
    assert common.subject_in_scope(plane_event(cwd="/elsewhere")) is False


def test_pending_directive_matches_agent_and_scope(common, plane):
    seed_plane(
        plane,
        "INSERT INTO subjects VALUES ('sub', 'other-session', '/repo')",
        "INSERT INTO directives(subject_id, agent_id) VALUES ('sub', 'a1')",
    )
    assert common.directive_pending(plane_event(agent_id="a1")) is True
    assert common.directive_pending(plane_event(agent_id="a2")) is False
    assert common.directive_pending(plane_event()) is False
    assert common.directive_pending(plane_event(agent_id="a1", cwd="/elsewhere")) is False


def test_top_level_agent_reads_the_empty_agent_id(common, plane):
    seed_plane(
        plane,
        "INSERT INTO subjects VALUES ('sub', 's', '/repo')",
        "INSERT INTO directives(subject_id, agent_id) VALUES ('sub', '')",
    )
    assert common.directive_pending(plane_event()) is True
    assert common.directive_pending(plane_event(agent_id=None)) is True
    assert common.directive_pending(plane_event(agent_id="")) is True


def test_delivered_directive_is_not_pending(common, plane):
    seed_plane(
        plane,
        "INSERT INTO subjects VALUES ('sub', 's', '/repo')",
        "INSERT INTO directives(subject_id, agent_id, delivered_at) VALUES ('sub', 'a1', 1)",
    )
    assert common.directive_pending(plane_event(agent_id="a1")) is False


@pytest.fixture
def session(tmp_path: Path) -> Path:
    transcript = tmp_path / "sess.jsonl"
    transcript.write_text("{}\n")
    lanes = tmp_path / "sess" / "subagents"
    lanes.mkdir(parents=True)
    (lanes / "agent-amate-1a2b.meta.json").write_text('{"name": "mate", "taskKind": "in_process_teammate"}')
    (lanes / "agent-a266d86820f8d3efd.meta.json").write_text('{"agentType": "open-pr:pr-watcher"}')
    (lanes / "agent-atorn.meta.json").write_text('{"taskKind": "in_proc')
    return transcript


def lane_event(session: Path, agent_id: str | None) -> types.SimpleNamespace:
    return plane_event(transcript_path=str(session), agent_id=agent_id)


def test_named_teammate_is_an_in_process_teammate(common, session):
    assert common.in_process_teammate(lane_event(session, "amate-1a2b")) is True


def test_plain_background_subagent_is_not_a_teammate(common, session):
    assert common.in_process_teammate(lane_event(session, "a266d86820f8d3efd")) is False


def test_missing_or_torn_meta_fails_open(common, session):
    assert common.in_process_teammate(lane_event(session, "anever")) is False
    assert common.in_process_teammate(lane_event(session, "atorn")) is False


def test_main_thread_is_never_a_teammate(common, session):
    assert common.in_process_teammate(lane_event(session, None)) is False
    assert common.in_process_teammate(plane_event(agent_id="amate-1a2b")) is False
