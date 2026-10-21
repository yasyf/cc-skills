from __future__ import annotations

from pathlib import Path

LANE = "inc-liam-load-opus"
AGENT = f"a{LANE}-0123456789abcdef"
DRIVE = {"CLAUDE_LONG_RUNNING_DRIVE": "900424b6", "CLAUDE_CONFIG_DIR": str(Path(__file__).parent / "fixtures" / "incident-lane-config")}
STATUS = "git -C"
NO_WORKTREE = f"no worktree found for lane {LANE}\n"
DETACHED = f"- /home/lane/.claude/worktrees/monorepo/{LANE} (git worktree list): HEAD (no branch), 0 dirty files, 0 ahead of trunk, last commit 9 min ago 1a2b3c4 dev: start, not pushed, no PR\n"
PUSHED = "- /home/lane/.claude/worktrees/monorepo/inc-liam-fork-parent-grant (cci record): inc-liam-fork-parent-grant...origin/inc-liam-fork-parent-grant, 0 dirty files, 1 ahead of trunk, last commit 2 min ago 9f8e7d6 sandsql-server: fix, pushed, PR #41461 open CI pending\n"
OWED_MECHANISM = "owed: MECHANISM #107495\n"
NOT_OWED = f"not owed: no mechanism of {LANE} waits on a PR\n"
