import json
from pathlib import Path

import pytest
from drive import reads

BUILD = "https://buildkite.com/forge/test/builds/121531"
OTHER = "https://buildkite.com/forge/test/builds/121532"
ASKER = "demo j7"
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
        (f"j232 LOG {BUILD} and LOG {OTHER} are both red on #7", f"LOG {BUILD} {OTHER}"),
        (f"LOG {BUILD}\nthen LOG {OTHER} if the first is green\nLOG {BUILD}", f"LOG {BUILD} {OTHER}"),
        ("READ cci show 2065", "READ cci show 2065"),
        ("j247: READ ccn answer show 5299250c9a85\nfor his words", "READ ccn answer show 5299250c9a85"),
        (
            'j7 BREAK-GLASS --stack sandsql/plat-usw2-prod --reason "count rows, read only" -- aws sts get-caller-identity\nmore',
            'BREAK-GLASS --stack sandsql/plat-usw2-prod --reason "count rows, read only" -- aws sts get-caller-identity',
        ),
        ("READY https://x/pull/1", None),
        (f"j242 READ, two reads for #7: (1) LOG {BUILD} (2) the field facts", None),
        ("READ: please send the full text of design-run-read-api.md", None),
        ("LOG (build 1, #7 at 1234abc), verbatim", None),
        ("j242 PUSHED #7\nLOG https://buildkite.com/forge/test/builds/1", None),
    ],
)
def test_a_read_question_opens_with_its_read_past_the_job_id_and_reads_every_log_it_names(
    body: str, question: str | None
) -> None:
    assert reads.request(body) == question


@pytest.mark.parametrize(
    ("body", "question"),
    [
        (f"j242 PUSHED #7\nLOG {BUILD} is red, and LOG {OTHER} too. Which do I fix first?", f"LOG {BUILD} {OTHER}"),
        ("j242 READY https://x/pull/1", None),
    ],
)
def test_logs_names_every_log_a_question_names_once_wherever_it_names_them(body: str, question: str | None) -> None:
    assert reads.logs(body) == question


def test_a_log_read_naming_two_builds_reads_both_in_half_the_budget_each(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    serve(monkeypatch, build_json(job(FAILED_JOB, "failed")))
    limits = []
    monkeypatch.setattr(reads, "excerpt", lambda log, limit: limits.append(limit) or "cut")
    said = f"== step 01a1: failed, exit 1, {BUILD}#{FAILED_JOB}\ncut"
    assert reads.answer(f"LOG {BUILD} {OTHER}", tmp_path, ASKER) == f"{said}\n\n{said}"
    assert limits == [reads.BUDGET // 2] * 2


def test_excerpt_keeps_the_command_output_without_hooks_or_markup() -> None:
    assert reads.excerpt(LOG, 1000) == "ERROR: raises journal write to 13"


def test_excerpt_with_no_failure_line_keeps_the_tail_and_says_how_much_it_cut() -> None:
    assert reads.excerpt("a" * 10 + "END", 5) == "[no failure lines matched]\n[last 5 of 13 characters]\naaEND"


def own_output(share: int) -> str:
    return (
        "[each failure's own output: a Go test's lines from its === RUN, else the failure line and the 2 after it,"
        f" each cut to its last {share} characters]"
    )


@pytest.mark.parametrize(
    "failure",
    [
        "--- FAIL: TestChat (2.89s)",
        "    --- FAIL: TestChat/one_subagent (0.07s)",
        "[sand] --- FAIL: TestChat (2.89s)",
        "FAIL\texample.com/app/cmd\t12.183s",
        "        FAIL [   0.070s] core store::tests::a_copy_keeps_every_write",
        "thread 'store::tests::a_copy_keeps_every_write' (148890) panicked at crates/core/src/store/tests.rs:12508:61:",
        "src/app.ts(4,7): error TS2322: Type 'string' is not assignable to type 'number'.",
        "  ✕ renders the composer (12 ms)",
        "  ● composer › renders",
    ],
)
def test_excerpt_leads_with_a_failure_far_before_the_tail(failure: str) -> None:
    output = "\n".join(["ok"] * 50 + [failure, "", "the message", "after", "past it"] + ["x" * 99] * 10)
    block = f"{failure}\nthe message\nafter"
    rest = 400 - len(failure) - len(block)
    assert reads.excerpt(output, 400) == (
        f"[failure lines, 1 matched]\n{failure}\n{own_output(400 - len(failure))}\n{block}\n"
        f"[last {rest} of {len(output)} characters]\n{output[-rest:]}"
    )


def test_excerpt_leads_with_every_go_fail_and_its_tests_own_lines_before_passing_tests_logs() -> None:
    passing = [f"    life_test.go:{n}: offline step answered in 94ms" for n in range(100)]
    failing = ["=== RUN   TestCounts", "    ssql_test.go:162: subqueries are not supported", "[sand] --- FAIL: TestCounts (0.76s)"]
    output = "\n".join(
        ["=== RUN   TestLife", *passing, "--- PASS: TestLife (9.00s)", *failing, "FAIL\texample.com/app/eval\t6.778s", *passing]
    )
    said = reads.excerpt(output, 1000)
    assert said.startswith(
        "[failure lines, 2 matched]\n[sand] --- FAIL: TestCounts (0.76s)\nFAIL\texample.com/app/eval\t6.778s\n"
    )
    assert "\n".join(failing) in said
    assert said.index("ssql_test.go:162") < said.index("life_test.go")


def test_excerpt_joins_overlapping_failures_and_cuts_what_overflows() -> None:
    output = "\n".join(["--- FAIL: A", "a1", "--- FAIL: B", "b1", "b2", "skip", "--- FAIL: C", "c1", "c2", "x" * 999])
    named = "--- FAIL: A\n--- FAIL: B\n--- FAIL: C"
    own = ["--- FAIL: A\na1\n--- FAIL: B\nb1\nb2", "--- FAIL: C\nc1\nc2"]
    assert reads.excerpt(output, 200).startswith(f"[failure lines, 3 matched]\n{named}\n{own_output(82)}\n{own[0]}\n--\n{own[1]}\n[last ")
    assert reads.excerpt(output, 60) == (
        f"[failure lines, 3 matched]\n{named}\n{own_output(12)}\n{own[0][-12:]}\n--\n{own[1][-12:]}\n"
        f"[last 1 of {len(output)} characters]\nx"
    )
    assert reads.excerpt(output, 20) == f"[first 20 of {len(named)} characters of the 3 failure lines]\n{named[:20]}"


def test_a_build_url_reads_every_failed_script_job(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = serve(
        monkeypatch,
        build_json(
            job(FAILED_JOB, "failed"), job("0" * 8 + FAILED_JOB[8:], "passed"), job("1" * 36, "failed", "trigger")
        ),
    )
    answer = reads.answer(f"LOG {BUILD}", tmp_path, ASKER)
    assert calls == [["bk", "api", "/pipelines/test/builds/121531"], ["bk", "log", "test", "121531", FAILED_JOB]]
    assert answer == f"== step 01a1: failed, exit 1, {BUILD}#{FAILED_JOB}\nERROR: raises journal write to 13"


@pytest.mark.parametrize("suffix", [f"#{FAILED_JOB}", f"/canvas?jid={FAILED_JOB}"])
def test_a_job_url_reads_that_job_alone(suffix: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    other = "0" * 8 + FAILED_JOB[8:]
    calls = serve(monkeypatch, build_json(job(other, "failed"), job(FAILED_JOB, "passed")))
    reads.answer(f"LOG {BUILD}{suffix}", tmp_path, ASKER)
    assert calls[1] == ["bk", "log", "test", "121531", FAILED_JOB]


def test_a_green_build_says_it_has_no_failed_job(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    serve(monkeypatch, build_json(job(FAILED_JOB, "passed")))
    assert reads.answer(f"LOG {BUILD}", tmp_path, ASKER) == f"{BUILD} is failed with no failed job"


def test_an_actions_job_url_reads_through_ccx(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = []
    monkeypatch.setattr(reads, "run", lambda argv, checkout: calls.append(argv) or "tail")
    url = "https://github.com/yasyf/cc-skills/actions/runs/19/job/126958155452"
    assert reads.answer(f"LOG {url}", tmp_path, ASKER) == "tail"
    assert calls == [["ccx", "vcs", "job-log", "126958155452", "-R", "yasyf/cc-skills", "--budget", "3000"]]


def test_a_failed_read_or_unknown_url_raises_why(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def refuse(argv: list[str], checkout: Path) -> str:
        raise reads.ReadError("tools/ci bk api exited 1: 404 Not Found")

    monkeypatch.setattr(reads, "run", refuse)
    with pytest.raises(reads.ReadError, match="^tools/ci bk api exited 1: 404 Not Found$"):
        reads.answer(f"LOG {BUILD}", tmp_path, ASKER)
    with pytest.raises(reads.ReadError, match="or a GitHub Actions job URL$"):
        reads.answer("LOG https://example.com/x", tmp_path, ASKER)


def test_a_record_read_runs_the_allowed_command_from_the_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = []
    monkeypatch.setattr(reads, "run", lambda argv, checkout: calls.append((argv, checkout)) or "m2065 his words\n")
    assert reads.answer('READ ccn answer search "close #52897"', tmp_path, ASKER) == "m2065 his words"
    assert reads.answer("READ cci show 2065 2066", tmp_path, ASKER) == "m2065 his words"
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
    reads.answer(f"READ {command}", tmp_path, ASKER)
    assert calls == [argv]


@pytest.mark.parametrize("command", ["cci post --text hi", "ccn answer add x", "ccn -R / answer search x", "rm -rf ."])
def test_a_record_read_refuses_anything_but_a_listed_read(
    command: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(reads, "run", lambda argv, checkout: pytest.fail(f"ran {argv}"))
    with pytest.raises(reads.ReadError, match="^only these reads run here: cci show"):
        reads.answer(f"READ {command}", tmp_path, ASKER)


def test_a_long_record_read_keeps_its_head(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(reads, "BUDGET", 5)
    monkeypatch.setattr(reads, "run", lambda argv, checkout: "HEAD-and-more")
    assert reads.answer("READ ccn search x", tmp_path, ASKER) == "[first 5 of 13 characters]\nHEAD-"


def test_an_empty_read_says_it_was_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(reads, "run", lambda argv, checkout: "\n")
    question = 'READ ccn answer search "cloud agent new pod start"'
    assert reads.answer(question, tmp_path, ASKER) == f"{question}: empty, the read printed nothing"


GRANT = "https://buildkite.com/forge/break-glass/builds/1926"
SSO_FIRST = """[profile identity]
sso_session = forge
[profile plat-prod-human]
source_profile = identity
role_arn = arn:aws:iam::222:role/frg-plat-gbl-prod-admin
"""
MACHINE = """[profile machine]
credential_process = /usr/local/bin/machine-credentials
[profile tf]
source_profile = machine
role_arn = arn:aws:iam::111:role/frg-core-gbl-identity-devops
[profile plat-prod]
source_profile = tf
role_arn = arn:aws:iam::222:role/frg-plat-gbl-prod-admin
"""


def grantable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config: str, ended: str = "passed") -> list[tuple[list[str], str | None]]:
    envs = tmp_path / "infra" / "envs"
    envs.mkdir(parents=True)
    (envs / "plat-usw2-prod.yaml").write_text("name: plat-usw2-prod\ncloud:\n  account: prod\n  namespace: frg-plat-gbl\n  region: us-west-2\nrows: {}\n")
    (tmp_path / "aws").write_text(config)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "aws"))
    calls = []

    def fake(argv: list[str], checkout: Path, env: dict[str, str] | None = None) -> str:
        calls.append((argv, env and env["AWS_PROFILE"]))
        if argv[:3] == ["bk", "build", "create"]:
            return f"Build created: {GRANT}\n"
        if argv[:3] == ["bk", "build", "view"]:
            return json.dumps({"state": ended})
        return "" if argv[0] == "bk" else '{"Arn": "arn:aws:sts::222:assumed-role/frg-plat-gbl-prod-admin/machine"}\n'

    monkeypatch.setattr(reads, "run", fake)
    return calls


def test_a_break_glass_read_grants_in_the_askers_name_then_runs_its_command_under_the_machine_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = grantable(tmp_path, monkeypatch, SSO_FIRST + MACHINE)
    said = reads.answer('BREAK-GLASS --stack sandsql/plat-usw2-prod --reason "count rows, read only" -- aws sts get-caller-identity', tmp_path, ASKER)
    why = "demo j7: count rows, read only"
    assert calls == [
        (
            ["bk", "build", "create", "-p", "forge/break-glass", "-b", "dev", "-m", why]
            + ["-e", f"BREAK_GLASS_REASON={why}", "-e", "BREAK_GLASS_STACK=sandsql/plat-usw2-prod", "--no-input", "-y"],
            None,
        ),
        (["bk", "build", "watch", "1926", "-p", "forge/break-glass", "--interval", "2"], None),
        (["bk", "build", "view", "1926", "-p", "forge/break-glass", "-o", "json"], None),
        (["aws", "sts", "get-caller-identity"], "plat-prod"),
    ]
    assert said == (
        f"break-glass grant {GRANT} passed; aws ran under the plat-prod profile\n"
        '{"Arn": "arn:aws:sts::222:assumed-role/frg-plat-gbl-prod-admin/machine"}'
    )


def test_a_break_glass_grant_that_does_not_pass_runs_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = grantable(tmp_path, monkeypatch, MACHINE, ended="failed")
    with pytest.raises(reads.ReadError, match=f"^the break-glass grant {GRANT} failed, so nothing ran$"):
        reads.answer('BREAK-GLASS --stack sandsql/plat-usw2-prod --reason "count rows" -- aws sts get-caller-identity', tmp_path, ASKER)
    assert [argv[0] for argv, _ in calls] == ["bk", "bk", "bk"]


@pytest.mark.parametrize(
    ("words", "why"),
    [
        ('--stack plat-usw2-prod --reason "x" -- aws s3 ls', '^"plat-usw2-prod" is no stack: name it as <domain>/<env>, such as sandsql/plat-usw2-prod$'),
        ('--stack sandsql/plat-usw2-dev --reason "x" -- aws s3 ls', "^plat-usw2-dev is no env .* declares in infra/envs$"),
        ('--stack sandsql/plat-usw2-prod -- aws s3 ls', '^BREAK-GLASS --stack <domain>/<env> --reason "<why>" -- <command>: the following arguments are required: --reason$'),
        ('--stack sandsql/plat-usw2-prod --reason "x" --profile admin -- aws s3 ls', "unrecognized arguments: --profile$"),
    ],
)
def test_a_break_glass_read_naming_no_stack_env_or_reason_is_refused_before_any_grant(
    words: str, why: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = grantable(tmp_path, monkeypatch, MACHINE)
    with pytest.raises(reads.ReadError, match=why):
        reads.answer(f"BREAK-GLASS {words}", tmp_path, ASKER)
    assert calls == []


def test_an_env_reached_only_through_an_sso_sign_in_has_no_machine_profile_and_posts_no_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = grantable(tmp_path, monkeypatch, SSO_FIRST)
    with pytest.raises(reads.ReadError) as refused:
        reads.answer('BREAK-GLASS --stack sandsql/plat-usw2-prod --reason "x" -- aws s3 ls', tmp_path, ASKER)
    assert str(refused.value) == (
        f"no profile in {tmp_path / 'aws'} assumes frg-plat-gbl-prod-admin through source_profile from a credential_process,"
        " so plat-usw2-prod has no machine profile"
    )
    assert calls == []
