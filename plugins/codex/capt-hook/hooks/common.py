from __future__ import annotations

import json
import shutil
from pathlib import Path

from captain_hook import BaseHookEvent

__capt_hook_skip__ = True

# parents[2] is the plugin root; the released binary rides bin/codex-ask, else PATH.
BUNDLED = Path(__file__).resolve().parents[2] / "bin" / "codex-ask"


def codex_ask_bin() -> str | None:
    if BUNDLED.exists():
        return str(BUNDLED)
    return shutil.which("codex-ask")


def call_bin(evt: BaseHookEvent, sub: str, *, timeout: int = 10) -> str | None:
    binary = codex_ask_bin()
    if binary is None:
        return None
    try:
        return evt.ctx.call_cli(
            [binary, sub],
            input=json.dumps(evt._raw),
            timeout=timeout,
            throw=False,
        )
    except UnicodeDecodeError:
        return None
