from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from long_running import ledger


class StackShell:
    def __init__(self, stacks: dict[str, list[tuple[int, str]]], refuse: str = "") -> None:
        self.stacks = stacks
        self.refuse = refuse
        self.writes: list[list[str]] = []

    def run(self, argv: list[str], stdin: str | None = None, cwd: Path | None = None) -> str:
        if "api" in argv:
            endpoint = argv[-1].removeprefix("repos/Forge-AI/monorepo/")
            kind, number = endpoint.split("/")
            if kind == "pulls":
                stack = next((name for name, members in self.stacks.items() if int(number) in dict(members)), None)
                return json.dumps({"stack": {"number": int(stack)} if stack else None})
            return json.dumps({"pull_requests": [{"number": pr, "state": state} for pr, state in self.stacks[number]]})
        self.writes.append(argv)
        if self.refuse and argv[2] == self.refuse:
            raise subprocess.CalledProcessError(1, argv, "", "gh: stack is queued")
        return ""


@pytest.mark.parametrize(
    ("members", "writes", "line"),
    [
        ([(47979, "open"), (47981, "closed")], [["gh", "stack", "unstack", "47982"]], "#47981 left native stack 47982"),
        ([(1, "open"), (47981, "closed"), (2, "open"), (3, "open")], [["gh", "stack", "unstack", "47982"], ["gh", "stack", "link", "1", "2", "3"]], "#47981 left native stack 47982; #1, #2, #3 linked again"),
    ],
)
def test_a_closed_pr_leaves_its_native_stack_and_the_open_prs_link_again(members, writes, line, tmp_path):
    shell = StackShell({"47982": members})
    assert ledger.leave_stack(shell, ledger.Github(shell, "Forge-AI/monorepo"), tmp_path, "47981") == line
    assert shell.writes == writes


def test_a_closed_pr_in_no_stack_changes_nothing(tmp_path):
    shell = StackShell({})
    assert ledger.leave_stack(shell, ledger.Github(shell, "Forge-AI/monorepo"), tmp_path, "47981") == ""
    assert shell.writes == []


def test_a_stack_github_refuses_to_change_is_named_and_kept(tmp_path):
    shell = StackShell({"47982": [(47979, "open"), (47981, "closed")]}, refuse="unstack")
    line = ledger.leave_stack(shell, ledger.Github(shell, "Forge-AI/monorepo"), tmp_path, "47981")
    assert line == "#47981 STACK-KEPT: closed, but leaving native stack 47982 failed, so its open PRs #47979 may still list it: gh: stack is queued"
