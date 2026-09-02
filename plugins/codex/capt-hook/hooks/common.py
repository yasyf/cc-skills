from __future__ import annotations

import json
import os
import pwd
import shutil
from pathlib import Path

from captain_hook import BaseHookEvent

__capt_hook_skip__ = True

# parents[2] is the plugin root; bin/codex-ask is the launcher symlink, else PATH.
PLUGIN_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = PLUGIN_ROOT / "bin" / "codex-ask"
DESCRIPTOR = PLUGIN_ROOT / "bin" / "codex-ask.binrun"
SERVICE_LABEL = "com.yasyf.codex-ask"


def daemon_socket() -> Path:
    # daemonkit paths.Agent(label), whose home is the passwd entry, not $HOME.
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    return home / ".daemonkit" / "a" / SERVICE_LABEL / "daemon.sock"


def daemon_is_down() -> bool:
    # An absent state dir is an unreadable layout, not a stopped daemon: spawn anyway.
    socket = daemon_socket()
    return socket.parent.is_dir() and not socket.exists()


def binrun_bin() -> str | None:
    found = shutil.which("binrun")
    if found:
        return found
    home = os.environ.get("DAEMONKIT_HOME") or Path.home() / ".daemonkit"
    shared = Path(home) / "bin" / "binrun"
    return str(shared) if os.access(shared, os.X_OK) else None


def codex_ask_argv() -> list[str] | None:
    runner = binrun_bin()
    if runner and DESCRIPTOR.is_file():
        return [runner, str(DESCRIPTOR)]
    if LAUNCHER.exists():
        return [str(LAUNCHER)]
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
