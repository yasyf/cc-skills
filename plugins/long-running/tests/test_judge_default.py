import json
import subprocess

import pytest

from long_running import judge
from test_desk_runner import config, runner_module, shell


@pytest.mark.parametrize("backend", [None, "", *judge.BACKENDS])
def test_the_default_is_jev_first_and_explicit_backend_choices_still_win(backend):
    env = {} if backend is None else {judge.BACKEND_ENV: backend}

    assert judge.DEFAULT_BACKEND == "jev+sonnet"
    assert judge.backend_from(env) == (backend or "jev+sonnet")


@pytest.mark.parametrize(
    ("probability", "confirmation", "expected", "commands"),
    [
        (0.1, None, "no", ["uv"]),
        (0.9, "yes", "yes", ["uv", "claude"]),
        (0.9, "no", "unsure", ["uv", "claude"]),
        (0.9, "unsure", "unsure", ["uv", "claude"]),
    ],
)
def test_default_judgments_confirm_only_flagged_jev_answers(tmp_path, monkeypatch, probability, confirmation, expected, commands):
    selected = {"HOME": str(tmp_path)}
    called = []
    monkeypatch.setattr(judge, "state_dir", lambda drive, env=None: tmp_path)

    def run(argv, *, input=None, timeout, env=None):
        called.append(argv[0])
        if argv[0] == "uv":
            return json.dumps({"p_yes": probability, "tokens": 2})
        assert argv[0] == "claude" and confirmation is not None
        return json.dumps({"structured_output": {"verdict": confirmation, "artifact_phrase": "artifact", "evidence_phrase": "words", "sentence": "Judgment."}})

    actual = judge.judge("plain", "words", "artifact", backend=judge.backend_from(selected), drive="d1", env=selected, run=run)

    assert actual.verdict == expected and called == commands
    row = json.loads((tmp_path / judge.CALLS).read_text())
    assert (row["backend"], row["verdict"], row["asks"], row["error"]) == ("jev+sonnet", expected, len(commands), "")


@pytest.mark.parametrize("leaf", ["uv", "claude"])
@pytest.mark.parametrize(
    "failure",
    [
        subprocess.TimeoutExpired("model", 7),
        subprocess.CalledProcessError(2, "model"),
        OSError("unavailable"),
        ValueError("invalid"),
        KeyError("missing"),
    ],
)
def test_default_model_errors_keep_fail_open_status_and_do_not_start_another_model(tmp_path, monkeypatch, leaf, failure):
    called = []
    monkeypatch.setattr(judge, "state_dir", lambda drive, env=None: tmp_path)

    def run(argv, *, input=None, timeout, env=None):
        called.append(argv[0])
        if argv[0] == leaf:
            raise failure
        assert argv[0] == "uv"
        return json.dumps({"p_yes": 0.9, "tokens": 2})

    actual = judge.judge("plain", "words", "artifact", backend=judge.backend_from({}), drive="d1", env={"HOME": str(tmp_path)}, run=run)

    assert actual is None and called == (["uv"] if leaf == "uv" else ["uv", "claude"])
    row = json.loads((tmp_path / judge.CALLS).read_text())
    assert (row["backend"], row["verdict"], row["asks"]) == ("jev+sonnet", "error", len(called) - 1)
    assert row["error"].startswith(type(failure).__name__)


@pytest.mark.parametrize("leaf", ["uv", "claude"])
def test_default_unexpected_model_errors_retain_their_identity(leaf):
    failure = RuntimeError("unexpected model error")
    called = []

    def run(argv, *, input=None, timeout, env=None):
        called.append(argv[0])
        if argv[0] == leaf:
            raise failure
        assert argv[0] == "uv"
        return json.dumps({"p_yes": 0.9, "tokens": 2})

    with pytest.raises(RuntimeError) as caught:
        judge.judge("plain", "words", "artifact", backend=judge.backend_from({}), run=run)

    assert caught.value is failure and called == (["uv"] if leaf == "uv" else ["uv", "claude"])


@pytest.mark.parametrize("backend", [None, *judge.BACKENDS])
def test_the_runner_latches_the_default_or_explicit_backend_at_construction(shell, config, tmp_path, monkeypatch, backend):
    if backend is None:
        monkeypatch.delenv(judge.BACKEND_ENV, raising=False)
    else:
        monkeypatch.setenv(judge.BACKEND_ENV, backend)
    runner = runner_module.Runner(shell, runner_module.Config.load(config), runner_module.actions.Store(tmp_path / "store"))
    try:
        assert runner.judge_backend == (backend or "jev+sonnet")
        monkeypatch.setenv(judge.BACKEND_ENV, "jev" if runner.judge_backend != "jev" else "sonnet")
        assert runner.judge_backend == (backend or "jev+sonnet")
    finally:
        runner.judges.shutdown(wait=True)
