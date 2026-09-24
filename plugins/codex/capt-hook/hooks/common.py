from __future__ import annotations

import json
import os
import pwd
import re
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from captain_hook import BaseHookEvent

__capt_hook_skip__ = True

# parents[2] is the plugin root; bin/codex-ask is the launcher symlink, else PATH.
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = PLUGIN_ROOT / "bin" / "codex-ask"
DESCRIPTOR = PLUGIN_ROOT / "bin" / "codex-ask.binrun"
SERVICE_LABEL = "com.yasyf.codex-ask"
RUNNER_TAG = re.compile(r'^RUNNER_TAG="([^"]+)"$', re.MULTILINE)
APP_DIR = ".cc-codex-ask"
PENDING_DIRECTIVE = (
    "SELECT EXISTS(SELECT 1 FROM directives JOIN subjects ON subjects.id = directives.subject_id "
    "WHERE directives.agent_id = ? AND directives.delivered_at IS NULL AND subjects.scope = ?)"
)
SUBJECT_IN_SCOPE = "SELECT EXISTS(SELECT 1 FROM subjects WHERE scope = ?)"
TEAMMATE = "in_process_teammate"


def real_home() -> Path:
    return Path(os.environ.get("DAEMONKIT_HOME") or pwd.getpwuid(os.getuid()).pw_dir)


def daemon_socket() -> Path:
    return real_home() / ".daemonkit" / "a" / SERVICE_LABEL / "daemon.sock"


def state_db() -> Path:
    return real_home() / APP_DIR / "cc-interact-v1" / "state.db"


def daemon_is_down() -> bool:
    # An absent state dir is an unreadable layout, not a stopped daemon: spawn anyway.
    socket = daemon_socket()
    return socket.parent.is_dir() and not socket.exists()


def plane_may_match(query: str, *params: str) -> bool:
    try:
        with closing(sqlite3.connect(f"{state_db().as_uri()}?mode=ro", uri=True, timeout=0)) as conn:
            return bool(conn.execute(query, params).fetchone()[0])
    except sqlite3.DatabaseError:
        return True


def scope(evt: BaseHookEvent) -> str:
    return evt._raw.get("cwd") or ""


def directive_pending(evt: BaseHookEvent) -> bool:
    return plane_may_match(PENDING_DIRECTIVE, evt._raw.get("agent_id") or "", scope(evt))


def subject_in_scope(evt: BaseHookEvent) -> bool:
    return plane_may_match(SUBJECT_IN_SCOPE, scope(evt))


def in_process_teammate(evt: BaseHookEvent) -> bool:
    agent_id, transcript = evt._raw.get("agent_id"), evt._raw.get("transcript_path")
    if not agent_id or not transcript:
        return False
    meta = Path(transcript).with_suffix("") / "subagents" / f"agent-{agent_id}.meta.json"
    try:
        return json.loads(meta.read_text()).get("taskKind") == TEAMMATE
    except (OSError, json.JSONDecodeError):
        return False


def runner_home() -> Path:
    return real_home() / ".daemonkit"


def binrun_bin() -> str | None:
    if chosen := os.environ.get("BINRUN_BIN"):
        return chosen
    pinned = runner_home() / "binrun" / RUNNER_TAG.search(LAUNCHER.read_text())[1] / "binrun"
    if os.access(pinned, os.X_OK):
        return str(pinned)
    return shutil.which("binrun")


def codex_ask_argv() -> list[str] | None:
    if LAUNCHER.exists():
        runner = binrun_bin()
        return [runner, str(DESCRIPTOR)] if runner and DESCRIPTOR.is_file() else [str(LAUNCHER)]
    found = shutil.which("codex-ask")
    return [found] if found else None


def call_bin(evt: BaseHookEvent, sub: str, *, timeout: int = 10) -> str | None:
    if daemon_is_down():
        return None
    argv = codex_ask_argv()
    if argv is None:
        return None
    try:
        return evt.ctx.call_cli(
            [*argv, sub],
            input=json.dumps(evt._raw),
            # Under binrun the binary runs from a cache shard (dispatch.go pluginRoots).
            env={"BINRUN_PLUGIN_ROOT": str(PLUGIN_ROOT)},
            timeout=timeout,
            throw=False,
        )
    except UnicodeDecodeError:
        return None
