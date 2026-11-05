from __future__ import annotations

from pathlib import Path

import pytest
from captain_hook.cmd import Cmd
from hooks import shell_waits

POLLS_INSIDE = "while :; do\n  gh api x\n  sleep 120\ndone\n"


def script(directory: Path, name: str, text: str) -> Path:
    path = directory / name
    path.write_text(text)
    return path


def runs(command: str) -> bool:
    return shell_waits.runs_a_wait(Cmd.parse(command))


@pytest.mark.parametrize(
    "text",
    [
        "#!/bin/zsh\nsleep $(( target - $(date +%s) ))\necho wake\n",
        "sleep 3600\ncci post --kind note wake\n",
        "until grep -q done out.txt; do\n  sleep 5\ndone\n",
        "while true; do\n  gh pr checks 12\n  sleep 30\ndone\n",
    ],
)
@pytest.mark.parametrize("launch", ["zsh {}", "timeout 240 zsh {}", "nohup bash {} > /dev/null 2>&1 &", "{}"])
def test_a_sleeper_script_outside_a_checkout_is_a_wait(tmp_path: Path, launch: str, text: str):
    assert runs(launch.format(script(tmp_path, "wake-1015.zsh", text)))


@pytest.mark.parametrize(
    "text",
    [
        "while [ $# -gt 0 ]; do\n  echo $1\n  shift\ndone\n",
        "while getopts ab flag; do\n  echo $flag\ndone\n",
        "for f in a b; do\n  gh api $f\n  sleep 2\ndone\n",
        "cat notes | while read -r line; do\n  echo $line\ndone\n",
        "sleep 5\ncat out.txt\n",
    ],
)
def test_a_script_that_does_real_work_is_no_wait(tmp_path: Path, text: str):
    assert not runs(f"zsh {script(tmp_path, 'work.zsh', text)}")


def test_a_checked_in_script_keeps_its_own_short_retry(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "tools").mkdir()
    assert not runs(f"zsh {script(tmp_path / 'tools', 'warm.zsh', 'until test -e lock; do sleep 1; done')}")


@pytest.mark.parametrize("name", ["pr-poll.sh", "pr-watch-armed.sh"])
@pytest.mark.parametrize("launch", ["PR_POLL_WINDOW=40 bash {} acme/widgets 7 /c/7.json", "{} acme/widgets 7 /c/7.json"])
def test_the_pr_watchers_installed_scripts_are_no_wait(tmp_path: Path, launch: str, name: str):
    assert not runs(launch.format(script(tmp_path, name, POLLS_INSIDE)))


@pytest.mark.parametrize(
    "launch",
    ["while true; do bash {}; sleep 30; done", "until bash {}; do sleep 5; done", "sleep 600 && bash {}"],
)
def test_a_wait_written_around_the_poll_script_is_still_a_wait(tmp_path: Path, launch: str):
    assert runs(launch.format(script(tmp_path, "pr-poll.sh", POLLS_INSIDE)))


def test_the_same_loop_under_another_name_is_still_a_wait(tmp_path: Path):
    assert runs(f"bash {script(tmp_path, 'my-poll.sh', POLLS_INSIDE)}")


def test_a_script_that_is_not_there_is_no_wait(tmp_path: Path):
    assert not runs(f"zsh {tmp_path / 'gone.zsh'}")
