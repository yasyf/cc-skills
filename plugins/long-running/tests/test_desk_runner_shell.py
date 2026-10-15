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
