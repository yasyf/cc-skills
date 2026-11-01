"""The judge's policy over scripted model answers: a yes stands only when a second ask agrees, a failed call fails open, and every call lands in the drive's calls log."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from long_running import judge
import pytest

YES = judge.Verdict("yes", "one process per destination and key", "one upload process per Mac", "This reads him narrower.")
NO = judge.Verdict("no", "", "", "It carries what he said.")
UNSURE = judge.Verdict("unsure", "per key", "per Mac", "Cannot tell.")
ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "judge-answers.json").read_text())
PLAIN_ANSWERS = json.loads((Path(__file__).parent / "fixtures" / "judge-plain-answers.json").read_text())


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
    monkeypatch.setattr(judge, "state_dir", lambda drive, env=None: tmp_path)

    def broken(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "claude")

    monkeypatch.setattr(judge.subprocess, "run", broken)

    verdict = judge.judge("contract", judge.owner_words("rule/x", [{"at": "Oct 30 1:00 PM", "words": "one process", "answered": None}]), "per key", backend="sonnet", drive="d1")

    assert verdict is None
    [row] = judge.calls(tmp_path, datetime.now(timezone.utc) - timedelta(minutes=1))
    assert (row["kind"], row["verdict"], row["asks"]) == ("contract", "error", 0)
    assert row["error"].startswith("CalledProcessError")


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("custom_config", [False, True])
def test_stateless_claude_reuses_private_plugin_state_without_changing_auth_or_caller_environment(tmp_path, monkeypatch, explicit, custom_config):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "auth-sentinel")
    monkeypatch.setenv("CLAUDE_CODE_PLUGIN_CACHE_DIR", str(tmp_path / "ordinary-plugins"))
    if custom_config:
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "profile"))
    else:
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    inherited = dict(os.environ)
    selected = dict(inherited) if explicit else None
    config = tmp_path / "profile" if custom_config else tmp_path / "home" / ".claude"
    plugins = config / "long-running" / "judge-plugins"
    plugins.mkdir(parents=True)
    kept = plugins / "kept"
    kept.write_text("persistent helper state")
    calls = []

    def transport(argv, **kwargs):
        calls.append(argv)
        assert kwargs["env"] == inherited | {"CLAUDE_CODE_PLUGIN_CACHE_DIR": str(plugins)}
        assert kwargs["input"] == "prompt" and kwargs["timeout"] == 7
        assert kept.read_text() == "persistent helper state"
        return subprocess.CompletedProcess(argv, 0, json.dumps({"structured_output": {"verdict": "no"}}), "")

    monkeypatch.setattr(judge.subprocess, "run", transport)
    for _ in range(2):
        assert judge.claude("prompt", "system", "schema", 7, selected)[0] == {"verdict": "no"}
    assert len(calls) == 2 and calls[0] == calls[1]
    assert dict(os.environ) == inherited and (selected is None or selected == inherited)
    assert kept.read_text() == "persistent helper state"


def test_a_sonnet_yes_is_asked_twice_and_both_calls_are_counted(tmp_path, monkeypatch):
    monkeypatch.setattr(judge, "state_dir", lambda drive, env=None: tmp_path)
    answer = {"structured_output": {"verdict": "yes", "artifact_phrase": "a", "evidence_phrase": "words", "sentence": "c"}, "total_cost_usd": 0.01, "usage": {"input_tokens": 900, "output_tokens": 60}}
    argv = []

    def claude(args, **kwargs):
        argv.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(answer), stderr="")

    monkeypatch.setattr(judge.subprocess, "run", claude)

    verdict = judge.judge("relay", "his words", "the relay", backend="sonnet", drive="d1")

    assert verdict.flagged
    assert len(argv) == 2 and argv[0][argv[0].index("--system-prompt") + 1] == judge.SYSTEM
    assert all(args.count("--safe-mode") == 1 and "--bare" not in args for args in argv), "stateless review must skip plugin startup while retaining OAuth"
    [row] = judge.calls(tmp_path, datetime.now(timezone.utc) - timedelta(minutes=1))
    assert (row["asks"], row["tokens"], row["cost_usd"]) == (2, 1920, 0.02)


@pytest.mark.parametrize(
    ("quote", "flagged"),
    [
        ("\u201cone upload process\u201d \u2026 per Mac", True),
        ("hold all of these and hand them off to the other session doing fundamental fixes", False),
    ],
    ids=["his-words-with-curly-quotes-and-an-ellipsis", "words-no-record-carries"],
)
def test_a_yes_stands_only_on_a_quote_the_evidence_holds(tmp_path, monkeypatch, quote, flagged):
    monkeypatch.setattr(judge, "state_dir", lambda drive, env=None: tmp_path)
    answer = {"structured_output": {"verdict": "yes", "artifact_phrase": "one process per key", "evidence_phrase": quote, "sentence": "This reads him narrower."}, "total_cost_usd": 0.0, "usage": {}}
    monkeypatch.setattr(judge.subprocess, "run", lambda args, **kwargs: subprocess.CompletedProcess(args, 0, stdout=json.dumps(answer), stderr=""))

    verdict = judge.judge("relay", "His words, verbatim: \"One upload process for each destination, per Mac.\"", "one process per key", backend="sonnet")

    assert verdict.flagged is flagged
    assert verdict.evidence_phrase == (quote if flagged else "")


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


def test_a_batch_asks_sonnet_once_under_its_own_system_prompt_and_logs_an_answered_call(tmp_path, monkeypatch):
    monkeypatch.setattr(judge, "state_dir", lambda drive, env=None: tmp_path)
    argv = []

    def claude(args, **kwargs):
        argv.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"structured_output": {"verdicts": [{"id": "q1", "why": "w"}]}, "total_cost_usd": 0.02, "usage": {"input_tokens": 3000}}), stderr="")

    monkeypatch.setattr(judge.subprocess, "run", claude)

    assert judge.batch("stall", "Questions: q1", '{"type": "object"}', drive="d1") == {"verdicts": [{"id": "q1", "why": "w"}]}
    assert len(argv) == 1 and argv[0][argv[0].index("--system-prompt") + 1] == judge.BATCH_SYSTEM
    assert argv[0].count("--safe-mode") == 1 and "--bare" not in argv[0], "stateless review must skip plugin startup while retaining OAuth"
    [row] = judge.calls(tmp_path, datetime.now(timezone.utc) - timedelta(minutes=1))
    assert (row["kind"], row["verdict"], row["asks"], row["tokens"]) == ("stall", "answered", 1, 3000)


def test_a_failed_batch_returns_none_and_the_digest_counts_it_beside_the_answered_ones(tmp_path, monkeypatch):
    monkeypatch.setattr(judge, "state_dir", lambda drive, env=None: tmp_path)

    def broken(*args, **kwargs):
        raise subprocess.TimeoutExpired("claude", 300)

    monkeypatch.setattr(judge.subprocess, "run", broken)

    assert judge.batch("stall", "Questions: q1", '{"type": "object"}', drive="d1") is None
    rows = [*judge.calls(tmp_path, datetime.now(timezone.utc) - timedelta(minutes=1)), {"kind": "stall", "verdict": "answered", "seconds": 40.0, "cost_usd": 0.02}]
    assert judge.digest(rows) == ["judge stall: 2 calls, 1 answered, 1 errors; median 40 s, slowest 40 s; $0.02"]


def test_an_unknown_backend_in_the_environment_is_refused():
    with pytest.raises(ValueError, match="names no judge backend"):
        judge.backend_from({judge.BACKEND_ENV: "haiku"})


def replayed(backend: judge.Backend, answers: dict = ANSWERS) -> tuple[int, int, int]:
    passed = missed = false_flags = 0
    for case in answers.values():
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

    assert scores[judge.DEFAULT_BACKEND] == (43, 5, 0)
    assert all(scores[judge.DEFAULT_BACKEND][0] > passed for backend, (passed, _, _) in scores.items() if backend != judge.DEFAULT_BACKEND)


def test_the_plain_words_judge_flags_under_one_in_ten_clean_texts_on_its_recorded_answers():
    clean = sum(len(case["runs"]) for case in PLAIN_ANSWERS.values() if case["expect"] in ("no", "not-yes"))
    jargon = sum(len(case["runs"]) for case in PLAIN_ANSWERS.values() if case["expect"] == "yes")

    _, missed, false_flags = replayed(judge.DEFAULT_BACKEND, PLAIN_ANSWERS)

    assert false_flags * 10 < clean
    assert missed * 10 < jargon


def test_a_tap_carries_the_card_it_answered_and_a_typed_prompt_carries_none(tmp_path, monkeypatch):
    captured = [
        {"digest": "a" * 16, "at": "2026-10-30T04:16:16Z", "session": "s-root", "source": "board", "block": "replication-choice", "text": "1 Server shares one process"},
        {"digest": "b" * 16, "at": "2026-10-30T03:25:00Z", "session": "s-root", "source": "prompt", "text": "why was this not always the case"},
    ]
    (tmp_path / "owner-prompts.jsonl").write_text("".join(json.dumps(entry) + "\n" for entry in captured))
    row = {
        "at": "Oct 21 7:49 PM PT",
        "words": "one primary and not N secondaries",
        "proof": "transcript:5980bef2:90f0bf23",
        "repeats": json.dumps([
            {"at": "Oct 29 8:25 PM PT", "words": "why was this not always the case", "proof": f"proof:prompt:{'b' * 16}"},
            {"at": "Oct 29 9:16 PM PT", "words": "1 Server shares one process", "proof": f"proof:board:{'a' * 16}"},
        ]),
    }
    shown = []

    def present(args, **kwargs):
        shown.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="replication-choice: choice, pick one\n  One upload process per Mac: where does the fix go?\n", stderr="")

    monkeypatch.setattr(judge.subprocess, "run", present)

    said = judge.said(row, tmp_path)

    assert [item["answered"] is not None for item in said] == [False, False, True]
    assert shown == [["cc-present", "block", "show", "replication-choice", "--session", "s-root"]]
    assert "That was a tap on this card" in judge.owner_words("rule/x", said)
    assert "One upload process per Mac: where does the fix go?" in judge.owner_words("rule/x", said)


def test_each_taps_card_is_read_from_the_board_once_and_bounded(tmp_path, monkeypatch):
    (tmp_path / "owner-prompts.jsonl").write_text(json.dumps({"digest": "a" * 16, "at": "x", "session": "s", "source": "board", "block": "merge-triage"}) + "\n")
    shown = []

    def present(args, **kwargs):
        shown.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="card-merge-triage: Which pull requests may merge?\n" + "  Oct 26 picked approve\n" * 1000, stderr="")

    monkeypatch.setattr(judge.subprocess, "run", present)
    row = {"at": "Oct 26", "words": "Approve", "proof": f"proof:board:{'a' * 16}"}

    [first] = judge.said(row, tmp_path)
    [again] = judge.said(row, tmp_path)

    assert len(shown) == 1
    assert first["answered"] == again["answered"]
    assert first["answered"].startswith("card-merge-triage: Which pull requests may merge?")
    assert len(first["answered"]) < judge.CARD_CHARS + 100


def test_a_card_the_board_cannot_show_leaves_the_tap_without_one(tmp_path, monkeypatch):
    (tmp_path / "owner-prompts.jsonl").write_text(json.dumps({"digest": "a" * 16, "at": "x", "session": "s", "source": "board", "block": "gone"}) + "\n")

    def missing(args, **kwargs):
        raise FileNotFoundError("cc-present")

    monkeypatch.setattr(judge.subprocess, "run", missing)

    [item] = judge.said({"at": "Oct 29", "words": "1", "proof": f"proof:board:{'a' * 16}"}, tmp_path)

    assert item["answered"] is None
