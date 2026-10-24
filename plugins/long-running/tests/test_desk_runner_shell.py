from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "skills/long-running/scripts/desk-runner.py"
spec = importlib.util.spec_from_file_location("desk_runner_shell", SCRIPT)
runner_module = importlib.util.module_from_spec(spec)
sys.modules["desk_runner_shell"] = runner_module
spec.loader.exec_module(runner_module)


def test_an_input_larger_than_any_pipe_buffer_comes_back_whole():
    payload = "".join(f"{n:040x}\n" for n in range(40000))

    done = runner_module.Shell().run(["cat"], stdin=payload)

    assert (done.code, done.out) == (0, payload)


def test_a_child_past_the_timeout_is_killed_and_fails_its_pass(monkeypatch):
    monkeypatch.setattr(runner_module, "COMMAND_TIMEOUT_SECONDS", 0.2)

    done = runner_module.Shell().run(["sleep", "5"])

    assert done == runner_module.Done(runner_module.TIMED_OUT, "", "sleep 5 timed out after 0.2s and was killed")


def test_a_diff_holding_a_byte_that_is_not_utf8_reads_with_the_byte_replaced():
    done = runner_module.Shell().run(["printf", "+caf\\351 \\265s\\n"])

    assert (done.code, done.out) == (0, "+caf\ufffd \ufffds\n")


def test_a_check_past_its_minute_is_killed_and_reads_as_a_failed_command(monkeypatch):
    monkeypatch.setattr(runner_module, "QUICK_SECONDS", 0.2)

    done = runner_module.Shell().run(["sh", "-c", "sleep 5", "--check"])

    assert done == runner_module.Done(runner_module.TIMED_OUT, "", "sh -c sleep 5 --check timed out after 0.2s and was killed")


def test_the_command_in_flight_names_the_script_and_its_arguments_without_the_interpreter_or_its_paths():
    argv = ["/opt/python3.13/bin/python3", "/plugins/long-running/scripts/rulings.py", "check-pr", "--repo", "/worktrees/monorepo/runner", "--pr", "Forge-AI/monorepo#42590"]

    assert runner_module.command_name(argv) == "rulings.py check-pr --repo runner --pr Forge-AI/monorepo#42590"


def test_check_pr_gets_its_own_bound_and_every_other_read_keeps_its_own():
    rulings = ["/usr/bin/python3", "/plugins/long-running/scripts/rulings.py"]

    assert runner_module.time_bound([*rulings, "check-pr", "--pr", "Forge-AI/monorepo#42706"]) == runner_module.CHECK_PR_SECONDS
    assert runner_module.time_bound([*rulings, "slice", "--lane", "a"]) == runner_module.COMMAND_TIMEOUT_SECONDS
    assert runner_module.time_bound(["gh", "pr", "view", "1"]) == runner_module.QUICK_SECONDS
