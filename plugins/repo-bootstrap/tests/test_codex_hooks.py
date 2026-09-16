"""Guards for the codex plugin's capt-hook helpers."""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
import types
from contextlib import closing
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
COMMON = REPO_ROOT / "plugins" / "codex" / "capt-hook" / "hooks" / "common.py"


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
    launcher.write_text("#!/bin/sh\n")
    launcher.chmod(0o755)
    descriptor = root / "bin" / "codex-ask.binrun"
    descriptor.write_text("{}\n")
    monkeypatch.setattr(common, "PLUGIN_ROOT", root)
    monkeypatch.setattr(common, "LAUNCHER", launcher)
    monkeypatch.setattr(common, "DESCRIPTOR", descriptor)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("DAEMONKIT_HOME", raising=False)
    return types.SimpleNamespace(root=root, launcher=launcher, descriptor=descriptor)


def executable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def test_binrun_on_path_wins(common, plugin, monkeypatch, tmp_path):
    found = executable(tmp_path / "onpath" / "binrun")
    monkeypatch.setenv("PATH", str(found.parent))
    assert common.codex_ask_argv() == [str(found), str(plugin.descriptor)]


def test_shared_daemonkit_binrun_is_used(common, plugin, monkeypatch, tmp_path):
    home = tmp_path / "dk"
    shared = executable(home / "bin" / "binrun")
    monkeypatch.setenv("DAEMONKIT_HOME", str(home))
    assert common.codex_ask_argv() == [str(shared), str(plugin.descriptor)]


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


def test_absent_store_admits_every_spawn(common, plane):
    assert common.directive_pending(plane_event()) is True
    assert common.subject_in_scope(plane_event()) is True


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


def test_plane_queries_are_the_ones_the_daemon_contract_pins(common):
    contract = (REPO_ROOT / "plugins" / "codex" / "agent_plane_test.go").read_text()
    consumer = (REPO_ROOT / "plugins" / "codex" / "consumer.go").read_text()
    assert f'pendingDirectiveSQL = "{common.PENDING_DIRECTIVE}"' in contract
    assert f'subjectInScopeSQL   = "{common.SUBJECT_IN_SCOPE}"' in contract
    assert f'appDir = "{common.APP_DIR}"' in consumer
    relative = common.state_db().relative_to(common.passwd_home() / common.APP_DIR)
    assert f'filepath.Join(home, appDir, "{relative.parent}", "{relative.name}")' in contract
