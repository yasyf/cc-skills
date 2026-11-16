"""Guards for the codex plugin's capt-hook helpers."""

from __future__ import annotations

import importlib.util
import json
import os
import pwd
import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
COMMON = REPO_ROOT / "plugins" / "codex" / "capt-hook" / "hooks" / "common.py"
INSTALLER = REPO_ROOT / "plugins" / "codex" / "scripts" / "install-binary.sh"
SHIM = REPO_ROOT / "plugin" / "guides" / "sh" / "binrun-shim.sh"
ARM_ONE = "\n# Arm 1:"
RUNNER_TAG = re.compile(r'^RUNNER_TAG="([^"]+)"$', re.MULTILINE)


@pytest.fixture
def common(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(
        sys.modules, "captain_hook", types.SimpleNamespace(BaseHookEvent=object)
    )
    spec = importlib.util.spec_from_file_location("codex_hooks_common", COMMON)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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



def test_installer_runner_arms_are_ordered():
    shim = INSTALLER.read_text()
    assert re.fullmatch(r"v\d+\.\d+\.\d+", RUNNER_TAG.search(shim)[1])
    arms = [shim.index(arm) for arm in ('exec "$BINRUN_BIN"', 'exec "$RUNNER_BIN"', "command -v binrun")]
    assert arms == sorted(arms)


def test_shim_runner_path_is_pinned_under_daemonkit_home(tmp_path):
    tag = RUNNER_TAG.search(SHIM.read_text())[1]
    passwd = Path(pwd.getpwuid(os.getuid()).pw_dir)
    assert shim_runner_bin(PATH="/usr/bin:/bin", HOME=str(tmp_path / "redirected")) == passwd / ".daemonkit" / "binrun" / tag / "binrun"
    override = tmp_path / "dk"
    assert shim_runner_bin(PATH="/usr/bin:/bin", DAEMONKIT_HOME=str(override)) == override / ".daemonkit" / "binrun" / tag / "binrun"


def test_shim_refuses_an_unresolvable_passwd_home(tmp_path):
    done = run_shim_prologue(PATH=str(tmp_path / "empty"), HOME=str(tmp_path / "redirected"))
    assert done.returncode != 0
    assert str(tmp_path / "redirected") not in done.stdout



def plane_event(**raw: str | None) -> types.SimpleNamespace:
    return types.SimpleNamespace(_raw={"session_id": "s", "cwd": "/repo", **raw})


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


def test_missing_meta_is_not_a_teammate(common, session):
    assert common.in_process_teammate(lane_event(session, "anever")) is False


def test_torn_meta_faults(common, session):
    with pytest.raises(json.JSONDecodeError):
        common.in_process_teammate(lane_event(session, "atorn"))


def test_main_thread_is_never_a_teammate(common, session):
    assert common.in_process_teammate(lane_event(session, None)) is False
    assert common.in_process_teammate(plane_event(agent_id="amate-1a2b")) is False
