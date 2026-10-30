"""The judge's policy over scripted model answers: a yes stands only when a second ask agrees, a failed call fails open, and every call lands in the drive's calls log."""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from long_running import judge
import pytest

YES = judge.Verdict("yes", "one process per destination and key", "one upload process per Mac", "This reads him narrower.")
NO = judge.Verdict("no", "", "", "It carries what he said.")
UNSURE = judge.Verdict("unsure", "per key", "per Mac", "Cannot tell.")
ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "judge-answers.json").read_text())


def scripted(*verdicts: judge.Verdict):
    asked = iter(verdicts)
    calls = []

    def ask() -> judge.Verdict:
        calls.append(verdict := next(asked))
        return verdict

    return ask, calls


@pytest.mark.parametrize(
    ("backend", "sonnet", "jev", "expected", "asks"),
    [
        ("sonnet", [YES, YES], [], "yes", (2, 0)),
        ("sonnet", [YES, NO], [], "unsure", (2, 0)),
        ("sonnet", [NO], [], "no", (1, 0)),
        ("sonnet", [UNSURE], [], "unsure", (1, 0)),
        ("jev", [], [YES], "yes", (0, 1)),
        ("jev+sonnet", [], [NO], "no", (0, 1)),
        ("jev+sonnet", [YES], [YES], "yes", (1, 1)),
        ("jev+sonnet", [NO], [YES], "unsure", (1, 1)),
        ("sonnet+jev", [UNSURE], [YES], "yes", (1, 1)),
        ("sonnet+jev", [UNSURE], [NO], "unsure", (1, 1)),
        ("sonnet+jev", [NO], [], "no", (1, 0)),
        ("sonnet+jev", [YES, NO], [], "unsure", (2, 0)),
    ],
)
def test_each_backend_flags_only_what_a_second_ask_or_model_agrees_with(backend, sonnet, jev, expected, asks):
    sonnet_ask, sonnet_calls = scripted(*sonnet)
    jev_ask, jev_calls = scripted(*jev)

    verdict = judge.decide(backend, sonnet_ask, jev_ask)

    assert verdict.verdict == expected
    assert (len(sonnet_calls), len(jev_calls)) == asks


def test_a_split_keeps_the_first_answers_phrases_so_a_dispute_can_quote_them():
    sonnet_ask, _ = scripted(YES, NO)

    verdict = judge.decide("sonnet", sonnet_ask, scripted()[0])

    assert (verdict.verdict, verdict.artifact_phrase) == ("unsure", YES.artifact_phrase)


def test_a_failed_call_fails_open_and_is_logged_as_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(judge, "state_dir", lambda drive: tmp_path)

    def broken(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "claude")

    monkeypatch.setattr(judge.subprocess, "run", broken)

    verdict = judge.judge("contract", judge.owner_words("rule/x", [("Oct 30 1:00 PM", "one process")]), "per key", backend="sonnet", drive="d1")

    assert verdict is None
    [row] = judge.calls(tmp_path, datetime.now(timezone.utc) - timedelta(minutes=1))
    assert (row["kind"], row["verdict"], row["asks"]) == ("contract", "error", 0)
    assert row["error"].startswith("CalledProcessError")


def test_a_sonnet_yes_is_asked_twice_and_both_calls_are_counted(tmp_path, monkeypatch):
    monkeypatch.setattr(judge, "state_dir", lambda drive: tmp_path)
    answer = {"structured_output": {"verdict": "yes", "artifact_phrase": "a", "evidence_phrase": "b", "sentence": "c"}, "total_cost_usd": 0.01, "usage": {"input_tokens": 900, "output_tokens": 60}}
    argv = []

    def claude(args, **kwargs):
        argv.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(answer), stderr="")

    monkeypatch.setattr(judge.subprocess, "run", claude)

    verdict = judge.judge("relay", "his words", "the relay", backend="sonnet", drive="d1")

    assert verdict.flagged
    assert len(argv) == 2 and argv[0][argv[0].index("--system-prompt") + 1] == judge.SYSTEM
    [row] = judge.calls(tmp_path, datetime.now(timezone.utc) - timedelta(minutes=1))
    assert (row["asks"], row["tokens"], row["cost_usd"]) == (2, 1920, 0.02)


def test_the_digest_counts_each_kinds_verdicts_errors_and_latency():
    rows = [
        {"kind": "contract", "verdict": "yes", "seconds": 20.0, "cost_usd": 0.02},
        {"kind": "contract", "verdict": "no", "seconds": 10.0, "cost_usd": 0.01},
        {"kind": "contract", "verdict": "error", "seconds": 0.0, "cost_usd": 0.0},
        {"kind": "card", "verdict": "unsure", "seconds": 30.0, "cost_usd": 0.01},
    ]

    assert judge.digest(rows) == [
        "judge card: 1 calls, 0 yes, 0 no, 1 unsure, 0 errors; median 30 s, slowest 30 s; $0.01",
        "judge contract: 3 calls, 1 yes, 1 no, 0 unsure, 1 errors; median 10 s, slowest 20 s; $0.03",
    ]


def test_an_unknown_backend_in_the_environment_is_refused():
    with pytest.raises(ValueError, match="names no judge backend"):
        judge.backend_from({judge.BACKEND_ENV: "haiku"})


def replayed(backend: judge.Backend) -> tuple[int, int, int]:
    passed = missed = false_flags = 0
    for case in ANSWERS.values():
        for run in case["runs"]:
            sonnet = iter(judge.Verdict(verdict, "", "", "") for verdict in run["sonnet"])
            jev = iter(judge.Verdict("yes" if p_yes >= judge.JEV_YES else "no", "", "", "") for p_yes in run["jev"])
            flagged = judge.decide(backend, lambda: next(sonnet), lambda: next(jev)).flagged
            missed += case["expect"] == "yes" and not flagged
            false_flags += case["expect"] in ("no", "not-yes") and flagged
            passed += flagged if case["expect"] == "yes" else not flagged if case["expect"] in ("no", "not-yes") else True
    return passed, missed, false_flags


def test_the_default_backend_scores_best_on_the_recorded_eval_answers():
    scores = {backend: replayed(backend) for backend in judge.BACKENDS}

    assert scores[judge.DEFAULT_BACKEND] == (45, 3, 0)
    assert all(scores[judge.DEFAULT_BACKEND][0] > passed for backend, (passed, _, _) in scores.items() if backend != judge.DEFAULT_BACKEND)
