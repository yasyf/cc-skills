import json
from pathlib import Path

import pytest
from drive import reads

BUILD = "https://buildkite.com/forge/test/builds/121531"
FAILED_JOB = "01a1c150-b658-46c9-a067-e38eebe83af2"
LOG = (
    "\x1b_bk;t=1\x07~~~ Running repository pre-command hook\r\n"
    "\x1b_bk;t=2\x07setup noise\r\n"
    "\x1b_bk;t=3\x07~~~ Running commands\r\n"
    "\x1b_bk;t=4\x07\x1b[31mERROR\x1b[39m: raises journal write to 13\r\n"
    "\x1b_bk;t=5\x07~~~ Running repository post-command hook\r\n"
    "\x1b_bk;t=6\x07ci job-time: 2.35s\r\n"
)


def build_json(*jobs: dict) -> str:
    return json.dumps({"web_url": BUILD, "state": "failed", "jobs": list(jobs)})


def job(job_id: str, state: str, kind: str = "script") -> dict:
    return {
        "id": job_id,
        "type": kind,
        "state": state,
        "name": f"step {job_id[:4]}",
        "exit_status": 1,
        "web_url": f"{BUILD}#{job_id}",
    }


def serve(monkeypatch: pytest.MonkeyPatch, build: str) -> list[list[str]]:
    calls = []

    def fake(argv: list[str], checkout: Path) -> str:
        calls.append(argv[1:])
        return build if argv[2] == "api" else LOG

    monkeypatch.setattr(reads, "run", fake)
    return calls


@pytest.mark.parametrize(
    ("body", "question"),
    [
        (f"LOG {BUILD}", f"LOG {BUILD}"),
        (f"  LOG {BUILD}\n", f"LOG {BUILD}"),
        (
            f"j242 READ: LOG {BUILD} (#7 head 1234abc), failing while it still runs. Give every failed job's name.",
            f"LOG {BUILD}",
        ),
        (f"LOG {BUILD}#{FAILED_JOB} — need: did the sand-next-tests step run\nmore", f"LOG {BUILD}#{FAILED_JOB}"),
        ("READ cci show 2065", "READ cci show 2065"),
        ("j247: READ ccn answer show 5299250c9a85\nfor his words", "READ ccn answer show 5299250c9a85"),
        ("READY https://x/pull/1", None),
        (f"j242 READ, two reads for #7: (1) LOG {BUILD} (2) the field facts", None),
        ("READ: please send the full text of design-run-read-api.md", None),
        ("LOG (build 1, #7 at 1234abc), verbatim", None),
        ("j242 PUSHED #7\nLOG https://buildkite.com/forge/test/builds/1", None),
    ],
)
def test_a_read_question_opens_with_its_read_past_the_job_id_and_ignores_prose_after_it(
    body: str, question: str | None
) -> None:
    assert reads.request(body) == question


def test_excerpt_keeps_the_command_output_without_hooks_or_markup() -> None:
    assert reads.excerpt(LOG, 1000) == "ERROR: raises journal write to 13"


def test_excerpt_keeps_the_tail_and_says_how_much_it_cut() -> None:
    assert reads.excerpt("a" * 10 + "END", 5) == "[last 5 of 13 characters]\naaEND"


def test_a_build_url_reads_every_failed_script_job(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = serve(
        monkeypatch,
        build_json(
            job(FAILED_JOB, "failed"), job("0" * 8 + FAILED_JOB[8:], "passed"), job("1" * 36, "failed", "trigger")
        ),
    )
    answer = reads.answer(f"LOG {BUILD}", tmp_path)
    assert calls == [["bk", "api", "/pipelines/test/builds/121531"], ["bk", "log", "test", "121531", FAILED_JOB]]
    assert answer == f"== step 01a1: failed, exit 1, {BUILD}#{FAILED_JOB}\nERROR: raises journal write to 13"


@pytest.mark.parametrize("suffix", [f"#{FAILED_JOB}", f"/canvas?jid={FAILED_JOB}"])
def test_a_job_url_reads_that_job_alone(suffix: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    other = "0" * 8 + FAILED_JOB[8:]
    calls = serve(monkeypatch, build_json(job(other, "failed"), job(FAILED_JOB, "passed")))
    reads.answer(f"LOG {BUILD}{suffix}", tmp_path)
    assert calls[1] == ["bk", "log", "test", "121531", FAILED_JOB]


def test_a_green_build_says_it_has_no_failed_job(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    serve(monkeypatch, build_json(job(FAILED_JOB, "passed")))
    assert reads.answer(f"LOG {BUILD}", tmp_path) == f"{BUILD} is failed with no failed job"


def test_an_actions_job_url_reads_through_ccx(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = []
    monkeypatch.setattr(reads, "run", lambda argv, checkout: calls.append(argv) or "tail")
    url = "https://github.com/yasyf/cc-skills/actions/runs/19/job/126958155452"
    assert reads.answer(f"LOG {url}", tmp_path) == "tail"
    assert calls == [["ccx", "vcs", "job-log", "126958155452", "-R", "yasyf/cc-skills", "--budget", "3000"]]


def test_a_failed_read_or_unknown_url_raises_why(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def refuse(argv: list[str], checkout: Path) -> str:
        raise reads.ReadError("tools/ci bk api exited 1: 404 Not Found")

    monkeypatch.setattr(reads, "run", refuse)
    with pytest.raises(reads.ReadError, match="^tools/ci bk api exited 1: 404 Not Found$"):
        reads.answer(f"LOG {BUILD}", tmp_path)
    with pytest.raises(reads.ReadError, match="or a GitHub Actions job URL$"):
        reads.answer("LOG https://example.com/x", tmp_path)


def test_a_record_read_runs_the_allowed_command_from_the_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = []
    monkeypatch.setattr(reads, "run", lambda argv, checkout: calls.append((argv, checkout)) or "m2065 his words\n")
    assert reads.answer('READ ccn answer search "close #52897"', tmp_path) == "m2065 his words"
    assert reads.answer("READ cci show 2065 2066", tmp_path) == "m2065 his words"
    assert calls == [
        (["ccn", "answer", "search", "close #52897"], tmp_path),
        (["cci", "show", "2065", "2066"], tmp_path),
    ]


@pytest.mark.parametrize(
    ("command", "argv"),
    [
        ("ccn answer search sandsql shrink", ["ccn", "answer", "search", "sandsql shrink"]),
        ('ccn answer search "sandsql shrink"', ["ccn", "answer", "search", "sandsql shrink"]),
        ("ccn note search sandsql evict --limit 5", ["ccn", "note", "search", "sandsql evict", "--limit", "5"]),
        ("cci grep sandsql restore --drive d", ["cci", "grep", "sandsql restore", "--drive", "d"]),
        ("ccn search --limit 5 x", ["ccn", "search", "--limit", "5", "x"]),
        ("ccn answer show 5299250c9a85 abc", ["ccn", "answer", "show", "5299250c9a85", "abc"]),
    ],
)
def test_a_search_read_passes_its_words_before_the_first_flag_as_one_query(
    command: str, argv: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = []
    monkeypatch.setattr(reads, "run", lambda ran, checkout: calls.append(ran) or "his words")
    reads.answer(f"READ {command}", tmp_path)
    assert calls == [argv]


@pytest.mark.parametrize("command", ["cci post --text hi", "ccn answer add x", "ccn -R / answer search x", "rm -rf ."])
def test_a_record_read_refuses_anything_but_a_listed_read(
    command: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(reads, "run", lambda argv, checkout: pytest.fail(f"ran {argv}"))
    with pytest.raises(reads.ReadError, match="^only these reads run here: cci show"):
        reads.answer(f"READ {command}", tmp_path)


def test_a_long_record_read_keeps_its_head(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(reads, "BUDGET", 5)
    monkeypatch.setattr(reads, "run", lambda argv, checkout: "HEAD-and-more")
    assert reads.answer("READ ccn search x", tmp_path) == "[first 5 of 13 characters]\nHEAD-"


def test_an_empty_read_says_it_was_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(reads, "run", lambda argv, checkout: "\n")
    question = 'READ ccn answer search "cloud agent new pod start"'
    assert reads.answer(question, tmp_path) == f"{question}: empty, the read printed nothing"
