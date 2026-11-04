from __future__ import annotations

from pathlib import Path

from captain_hook import BaseHookEvent, Call


class UnknownDirectory(OSError):
    def __init__(self, call: Call) -> None:
        super().__init__(f"`{call.name}` runs after a `cd` this hook cannot follow")


def unknown_dir(evt: BaseHookEvent, call: Call) -> bool:
    return evt.cwd is not None and call.cwd is None


def run_dir(evt: BaseHookEvent, call: Call) -> str:
    if unknown_dir(evt, call):
        raise UnknownDirectory(call)
    return str(call.cwd or evt.cwd)


def known_path(evt: BaseHookEvent, call: Call, raw: str) -> Path | None:
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    return None if unknown_dir(evt, call) else Path(str(call.cwd or evt.cwd), path)


def located(evt: BaseHookEvent, call: Call, raw: str) -> Path:
    if (path := known_path(evt, call, raw)) is None:
        raise UnknownDirectory(call)
    return path
