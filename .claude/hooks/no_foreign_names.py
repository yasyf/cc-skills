from __future__ import annotations

import re
from pathlib import Path

from captain_hook import Allow, BaseHookEvent, Block, Event, FilePath, Input, TestFile, Tool, hook

PATTERNS = [re.compile(line) for line in (Path(__file__).resolve().parents[2] / "plugins/long-running/tools/foreign-names.patterns").read_text().splitlines() if line]


class AddsForeignName:
    def check(self, evt: BaseHookEvent) -> bool:
        kept = set((evt.old if evt.old is not None else (evt.file.path.read_text() if evt.file and evt.file.path.is_file() else "")).splitlines())
        return any(pattern.search(line) for line in (evt.content or "").splitlines() if line not in kept for pattern in PATTERNS)


hook(
    Event.PreToolUse,
    only_if=[
        Tool.EditTools,
        FilePath("plugins/long-running/lib/*", "plugins/long-running/hooks/*", "plugins/long-running/agents/*", "plugins/long-running/skills/*", "plugins/long-running/lane-rules/*", "plugins/long-running/capt-hook/hooks/*"),
        AddsForeignName(),
    ],
    skip_if=[TestFile(), FilePath("plugins/long-running/lib/drive/site/vendor/*")],
    block=True,
    message="long-running names no one repository's org, paths, release targets, tool verbs or channel ids. Read them from that repository's `.claude/long-running.toml` instead.",
    tests={
        Input(tool="Edit", file="plugins/long-running/lib/drive/brief.py", old="x = 1", content='STATUS = "tools/ci release status"'): Block(pattern="long-running.toml"),
        Input(tool="Edit", file="plugins/long-running/lib/drive/brief.py", old="x = 1", content='STATUS = repo_config(data)["release"]["status"]'): Allow(),
        Input(tool="Edit", file="plugins/long-running/tests/test_brief.py", old="x = 1", content='STATUS = "tools/ci release status"'): Allow(),
        Input(tool="Edit", file="plugins/long-running/lib/drive/brief.py", old='A = "tools/ci"\nx = 1', content='A = "tools/ci"\nx = 2'): Allow(),
    },
)
