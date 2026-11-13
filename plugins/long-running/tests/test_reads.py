import configparser
import json
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from drive import orca, reads, state
from drive.render import clock

BUILD = "https://buildkite.com/forge/test/builds/121531"
ASKER = "demo j7"
GRANT = "https://buildkite.com/forge/break-glass/builds/1926"
PROOF = "long-running 1.0.639 from bin/../lib\nFAIL: GitHub lists no delivery the receiver answered 200 and logged\n"
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
role_session_name = machine
region = us-west-2
[profile tnt-0ddq7rb]
source_profile = tf
role_arn = arn:aws:iam::333:role/frg-tnt-gbl-0ddq7rb-admin
"""
GRANT_ENDS = "2026-11-12T12:22:25.577Z"
GRANTED = state.parse_time("2026-11-12T11:22:30Z")
ASSUME = ["aws", "sts", "assume-role", "--profile", "tf", "--role-arn", "arn:aws:iam::222:role/frg-plat-gbl-prod-admin", "--role-session-name", "demo-j7"]
APPLIED = "6c751b8b101597fbef4464a3cc7dee314ef778a1"
LIVE = [
    {"target": "accounts", "component": "infra", "env": "core-gbl-identity", "commit": "920c12e92f89952469bf863d69c1f40abf87b2c4"},
    {"target": "identity", "component": "infra", "env": "core-usw2-identity", "commit": "648dbdcf19be2f65ea3c3c05f35ef9b46b8381a2"},
    {"target": "identity", "component": "infra", "env": "core-gbl-identity", "release": "releases/2026-11-12/60", "commit": APPLIED},
    {"target": "security", "component": "infra", "env": "core-gbl-identity", "commit": "20844fd99d90"},
]


@pytest.mark.parametrize(
    ("body", "question"),
    [
        (f"LOG {BUILD}", f"LOG {BUILD}"),
        (f"j242 READ: LOG {BUILD} (#7 head 1234abc), failing while it still runs. Give every failed job's name.", f"LOG {BUILD}"),
        ("READ cci show 2065", "READ cci show 2065"),
        ("j247: READ ccn answer show 5299250c9a85\nfor his words", "READ ccn answer show 5299250c9a85"),
        (
            ' DRIVE break-glass j7 --stack sandsql/plat-usw2-prod --reason "count rows" -- aws s3 ls\n{"stdin": ""}\n',
            'DRIVE break-glass j7 --stack sandsql/plat-usw2-prod --reason "count rows" -- aws s3 ls\n{"stdin": ""}',
        ),
        ("j7 DRIVE status", None),
        ("READY https://x/pull/1", None),
        (f"j242 READ, two reads for #7: (1) LOG {BUILD} (2) the field facts", None),
        ("READ: please send the full text of design-run-read-api.md", None),
    ],
)
def test_a_read_question_opens_with_its_read_past_the_job_id(body: str, question: str | None) -> None:
    assert reads.request(body) == question


def test_a_log_question_from_a_lane_on_the_old_builder_rules_gets_the_rule_that_replaced_the_mac_read_and_reads_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(reads, "run", lambda argv, checkout: pytest.fail(f"ran {argv}"))
    question = reads.request("LOG https://buildkite.com/forge/test/builds/129091")
    said = reads.answer(question, tmp_path, "demo", "j982")
    assert said.startswith("LOG https://buildkite.com/forge/test/builds/129091: this Mac reads no build log for a lane; read it on your Sprite")
    assert "tools/ci bk log <pipeline> <build> <job>" in said
    assert "ccx vcs job-log <job> -R <owner/repo> --budget 1000000" in said
    assert said.endswith("return BLOCKED naming the read and its command.")


def test_a_record_read_runs_the_allowed_command_from_the_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = []
    monkeypatch.setattr(reads, "run", lambda argv, checkout: calls.append((argv, checkout)) or "m2065 his words\n")
    assert reads.answer('READ ccn answer search "close #52897"', tmp_path, "demo", "j7") == "m2065 his words"
    assert reads.answer("READ cci show 2065 2066", tmp_path, "demo", "j7") == "m2065 his words"
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
    ],
)
def test_a_search_read_passes_its_words_before_the_first_flag_as_one_query(
    command: str, argv: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = []
    monkeypatch.setattr(reads, "run", lambda ran, checkout: calls.append(ran) or "his words")
    reads.answer(f"READ {command}", tmp_path, "demo", "j7")
    assert calls == [argv]


def test_a_ccn_show_of_several_ids_runs_once_for_each_its_outputs_joined(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = []
    monkeypatch.setattr(reads, "run", lambda ran, checkout: calls.append(ran) or f"{ran[3]} his words\n")
    assert reads.answer("READ ccn answer show b1ce8fb 588b46b --json", tmp_path, "demo", "j7") == "b1ce8fb his words\n\n588b46b his words"
    assert calls == [["ccn", "answer", "show", "b1ce8fb", "--json"], ["ccn", "answer", "show", "588b46b", "--json"]]


@pytest.mark.parametrize(
    ("command", "argv"),
    [
        ("ccn answer search 'Where should a chat's pin be stored'", ["ccn", "answer", "search", "Where should a chat's pin be stored"]),
        ("ccn answer search the dead lane's saved branch", ["ccn", "answer", "search", "the dead lane's saved branch"]),
        ("cci grep don't \"chat's pin\" --drive d", ["cci", "grep", "don't chat's pin", "--drive", "d"]),
        ('ccn note search "unclosed quote --limit 5', ["ccn", "note", "search", '"unclosed quote', "--limit", "5"]),
    ],
)
def test_a_read_in_free_text_takes_each_apostrophe_and_stray_quote_as_a_letter_of_its_word(
    command: str, argv: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = []
    monkeypatch.setattr(reads, "run", lambda ran, checkout: calls.append(ran) or "his words")
    assert reads.answer(f"READ {command}", tmp_path, "demo", "j7") == "his words"
    assert calls == [argv]


def test_a_read_request_in_prose_is_told_the_reads_that_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(reads, "run", lambda argv, checkout: pytest.fail(f"ran {argv}"))
    with pytest.raises(reads.ReadError, match="^only these reads run here: cci show"):
        reads.answer("READ request for j1618 (a read, not a report): the dead lane's saved branch is picked up", tmp_path, "demo", "j7")


@pytest.mark.parametrize("command", ["cci post --text hi", "ccn answer add x", "ccn -R / answer search x", "rm -rf ."])
def test_a_record_read_refuses_anything_but_a_listed_read(
    command: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(reads, "run", lambda argv, checkout: pytest.fail(f"ran {argv}"))
    with pytest.raises(reads.ReadError, match="^only these reads run here: cci show"):
        reads.answer(f"READ {command}", tmp_path, "demo", "j7")


@pytest.mark.parametrize("command", ["cat {folder}/reports/j2176.proof.txt", "tail -c 3000 reports/j2176.proof.txt", "reports/j2176.proof.txt."])
def test_a_read_naming_a_drive_file_gets_it_whole_however_the_lane_words_it(
    command: str, state_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(reads, "run", lambda argv, checkout: pytest.fail(f"ran {argv}"))
    folder = state.drive_dir("demo")
    (folder / "reports").mkdir(parents=True)
    (folder / "reports" / "j2176.proof.txt").write_text(PROOF * 400)
    assert reads.answer(f"READ {command.format(folder=folder)}", tmp_path, "demo", "j2919") == f"# Drive file reports/j2176.proof.txt\n\n{PROOF * 400}"


def test_a_read_naming_several_drive_files_answers_each_and_one_the_folder_lacks_as_none(state_root: Path, tmp_path: Path) -> None:
    folder = state.drive_dir("demo")
    for name, text in {"asks/j2329-48750da6.md": "ASK When a chat's Sand code turn has no stored description?\n", "jobs/j2329.brief.md": "Order (his words): describe code turns\n"}.items():
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text(text)
    question = "READ please paste whole asks/j2329-48750da6.md, jobs/j2329.brief.md and jobs/j2643.brief.md"
    assert reads.answer(question, tmp_path, "demo", "j2894") == (
        "# Drive file asks/j2329-48750da6.md\n\nASK When a chat's Sand code turn has no stored description?\n\n\n"
        "# Drive file jobs/j2329.brief.md\n\nOrder (his words): describe code turns\n\n\n"
        "# Drive file jobs/j2643.brief.md\n\nthe drive keeps no such file inside its folder"
    )


def test_a_read_reaching_outside_the_drive_folder_gets_none_of_the_outside_file(state_root: Path, tmp_path: Path) -> None:
    folder = state.drive_dir("demo")
    outside = tmp_path / "key.pem"
    outside.write_text("PRIVATE KEY\n")
    (folder / "reports").mkdir(parents=True)
    (folder / "scratch" / "j633").mkdir(parents=True)
    (folder / "scratch" / "j633" / "key.pem").symlink_to(outside)
    escape = f"reports/{'../' * (len(folder.relative_to(tmp_path).parts) + 1)}key.pem"
    said = reads.answer(f"READ cat {escape} scratch/j633/key.pem", tmp_path, "demo", "j7")
    assert "PRIVATE KEY" not in said
    assert said.count("the drive keeps no such file inside its folder") == 2
    with pytest.raises(reads.ReadError, match="reads as READ <its path>"):
        reads.answer(f"READ cat {outside}", tmp_path, "demo", "j7")


def test_a_drive_file_that_is_not_utf8_reads_with_its_bad_bytes_replaced(state_root: Path, tmp_path: Path) -> None:
    folder = state.drive_dir("demo")
    (folder / "repro").mkdir(parents=True)
    (folder / "repro" / "out.bin").write_bytes(b"exit \xff\n")
    assert reads.answer("READ repro/out.bin", tmp_path, "demo", "j7") == "# Drive file repro/out.bin\n\nexit �\n"


def test_a_listed_read_naming_a_drive_path_still_runs_its_record_read(state_root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls = []
    monkeypatch.setattr(reads, "run", lambda ran, checkout: calls.append(ran) or "his words")
    assert reads.answer("READ ccn search asks/j2329-48750da6.md", tmp_path, "demo", "j7") == "his words"
    assert calls == [["ccn", "search", "asks/j2329-48750da6.md"]]


def test_a_long_record_read_keeps_its_head(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(reads, "BUDGET", 5)
    monkeypatch.setattr(reads, "run", lambda argv, checkout: "HEAD-and-more")
    assert reads.answer("READ ccn search x", tmp_path, "demo", "j7") == "[first 5 of 13 characters]\nHEAD-"


def test_an_empty_read_says_it_was_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(reads, "run", lambda argv, checkout: "\n")
    question = 'READ ccn answer search "cloud agent new pod start"'
    assert reads.answer(question, tmp_path, "demo", "j7") == f"{question}: empty, the read printed nothing"


class Granted:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.envs: list[dict[str, str]] = []
        self.configs: list[str] = []
        self.expiration = "2026-11-12T12:21:00+00:00"
        self.live = LIVE


def grantable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config: str, ended: str = "passed", now: datetime = GRANTED) -> Granted:
    envs = tmp_path / "infra" / "envs"
    envs.mkdir(parents=True)
    (envs / "plat-usw2-prod.yaml").write_text("name: plat-usw2-prod\ncloud:\n  account: prod\n  namespace: frg-plat-gbl\n  region: us-west-2\nrows: {}\n")
    (tmp_path / "aws").write_text(config)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "aws"))
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "a-key-of-this-mac")
    monkeypatch.setenv("AWS_DEFAULT_PROFILE", "tnt-0ddq7rb")
    monkeypatch.setattr(state, "now", lambda: now)
    granted = Granted()

    def fake(argv: list[str], checkout: Path, env: dict[str, str] | None = None, bounded: bool = True) -> str:
        granted.calls.append(argv)
        if argv == [str(tmp_path / "tools" / "ci"), "release", "live", "--json"]:
            return json.dumps(granted.live)
        if argv[:3] == ["bk", "build", "create"]:
            return f"Build created: {GRANT}\n"
        if argv[:3] == ["bk", "build", "view"]:
            return json.dumps({"state": ended, "meta_data": {"break-glass-expires-at": GRANT_ENDS}})
        if argv[:3] == ["aws", "sts", "assume-role"]:
            minted = {"AccessKeyId": "ASIAMINTED", "SecretAccessKey": "minted-secret", "SessionToken": "minted/token+=", "Expiration": granted.expiration}
            return json.dumps({"Credentials": minted})
        if argv[0] == "bk":
            return ""
        granted.envs.append(env)
        granted.configs.append(Path(env["AWS_CONFIG_FILE"]).read_text())
        return '{"Arn": "arn:aws:sts::222:assumed-role/frg-plat-gbl-prod-admin/demo-j7"}\n'

    monkeypatch.setattr(reads, "run", fake)
    return granted


def glass(checkout: Path, stack: str = "sandsql/plat-usw2-prod") -> str:
    with reads.break_glass(stack, "count rows, read only", ASKER, checkout) as granted:
        return f"{granted.link} {reads.held(granted)}\n{reads.run(['aws', 'sts', 'get-caller-identity'], checkout, granted.env).strip()}"


def test_a_break_glass_grant_runs_its_command_holding_only_the_stacks_credentials_minted_to_end_before_the_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    granted = grantable(tmp_path, monkeypatch, SSO_FIRST + MACHINE)
    said = glass(tmp_path)
    why = "demo j7: count rows, read only"
    assert granted.calls == [
        [str(tmp_path / "tools" / "ci"), "release", "live", "--json"],
        ["bk", "build", "create", "-p", "forge/break-glass", "-b", "dev", "-c", APPLIED, "-m", why]
        + ["-e", f"BREAK_GLASS_REASON={why}", "-e", "BREAK_GLASS_STACK=sandsql/plat-usw2-prod", "--no-input", "-y"],
        ["bk", "build", "watch", "1926", "-p", "forge/break-glass", "--interval", "2"],
        ["bk", "build", "view", "1926", "-p", "forge/break-glass", "-o", "json"],
        [*ASSUME, "--duration-seconds", "3535", "--output", "json"],
        ["aws", "sts", "get-caller-identity"],
    ]
    [env] = granted.envs
    assert {key: value for key, value in env.items() if key.startswith("AWS_")} == {
        "AWS_CONFIG_FILE": env["AWS_CONFIG_FILE"],
        "AWS_SHARED_CREDENTIALS_FILE": os.devnull,
        "AWS_PROFILE": "plat-prod",
    }
    isolated = configparser.RawConfigParser()
    isolated.read_string(granted.configs[0])
    assert {section: dict(isolated[section]) for section in isolated.sections()} == {
        "profile plat-prod": {
            "region": "us-west-2",
            "aws_access_key_id": "ASIAMINTED",
            "aws_secret_access_key": "minted-secret",
            "aws_session_token": "minted/token+=",
        }
    }
    assert not Path(env["AWS_CONFIG_FILE"]).exists()
    assert said == (
        f"{GRANT} holding only the plat-prod profile's credentials, which end at {clock(state.parse_time(GRANT_ENDS).astimezone())}\n"
        '{"Arn": "arn:aws:sts::222:assumed-role/frg-plat-gbl-prod-admin/demo-j7"}'
    )


@pytest.mark.parametrize(
    ("number", "created", "finished", "ends"),
    [
        (1973, "2026-11-12T22:19:53.915Z", "2026-11-12T22:22:08.711Z", "2026-11-12T23:22:00.705Z"),
        (1965, "2026-11-12T21:45:40.525Z", "2026-11-12T21:51:10.561Z", "2026-11-12T22:48:37.382Z"),
    ],
)
def test_a_grant_build_outlasting_the_read_timer_is_watched_until_it_passes(
    number: int, created: str, finished: str, ends: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replay_speedup = 100
    link = f"https://buildkite.com/forge/break-glass/builds/{number}"
    took = (state.parse_time(finished) - state.parse_time(created)).total_seconds() / replay_speedup
    viewed = json.dumps({"state": "passed", "meta_data": {"break-glass-expires-at": ends}})
    bk = tmp_path / "bin" / "bk"
    bk.parent.mkdir()
    bk.write_text(f"""#!/bin/sh
case "$1 $2" in
  "build create") echo "Build created: {link}" ;;
  "build watch") exec sleep {took} ;;
  "build view") echo '{viewed}' ;;
esac
""")
    bk.chmod(0o755)
    ci = tmp_path / "tools" / "ci"
    ci.parent.mkdir()
    ci.write_text(f"#!/bin/sh\necho '{json.dumps(LIVE)}'\n")
    ci.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bk.parent}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(reads, "READ_SECONDS", reads.READ_SECONDS / replay_speedup)
    assert reads.grant("sandsql/plat-usw2-prod", "count rows, read only", tmp_path) == (link, state.parse_time(ends))


@pytest.mark.parametrize("now",[state.parse_time(GRANT_ENDS) - timedelta(minutes=15, seconds=59), state.parse_time(GRANT_ENDS) + timedelta(seconds=1)])
def test_a_grant_ending_before_the_shortest_credentials_sts_mints_runs_nothing(now: datetime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    granted = grantable(tmp_path, monkeypatch, MACHINE, now=now)
    with pytest.raises(reads.ReadError, match=f"^the break-glass grant {GRANT} ends at .*, too soon for the 15 minutes the shortest AWS credentials last, so nothing ran"):
        glass(tmp_path)
    assert [Path(argv[0]).name for argv in granted.calls] == ["ci", "bk", "bk", "bk"]


def test_credentials_sts_stamps_past_the_grants_end_are_never_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    granted = grantable(tmp_path, monkeypatch, MACHINE)
    granted.expiration = "2026-11-12T12:22:26+00:00"
    with pytest.raises(reads.ReadError, match=f"^STS minted credentials ending at 2026-11-12T12:22:26\\+00:00, past the end of the break-glass grant {GRANT}"):
        glass(tmp_path)
    assert granted.calls[-1][:3] == ["aws", "sts", "assume-role"]
    assert granted.envs == []


def test_a_grant_whose_grantor_stack_lists_no_applied_commit_posts_no_grant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    granted = grantable(tmp_path, monkeypatch, MACHINE)
    granted.live = [each for each in LIVE if (each["target"], each["env"]) != ("identity", "core-gbl-identity")]
    with pytest.raises(reads.ReadError) as refused:
        glass(tmp_path)
    assert str(refused.value) == (
        "tools/ci release live lists no commit identity was applied at on core-gbl-identity, whose IAM the grantor role holds, so no grant started"
    )
    assert [Path(argv[0]).name for argv in granted.calls] == ["ci"]


def test_a_break_glass_grant_that_does_not_pass_runs_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    granted = grantable(tmp_path, monkeypatch, MACHINE, ended="failed")
    with pytest.raises(reads.ReadError, match=f"^the break-glass grant {GRANT} failed, so nothing ran$"):
        glass(tmp_path)
    assert [Path(argv[0]).name for argv in granted.calls] == ["ci", "bk", "bk", "bk"]


@pytest.mark.parametrize(
    ("stack", "why"),
    [
        ("plat-usw2-prod", '^"plat-usw2-prod" is no stack: name it as <domain>/<env>, such as sandsql/plat-usw2-prod$'),
        ("sandsql/plat-usw2-dev", "^plat-usw2-dev is no env .* declares in infra/envs$"),
    ],
)
def test_a_break_glass_grant_naming_no_stack_or_env_is_refused_before_any_grant(stack: str, why: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    granted = grantable(tmp_path, monkeypatch, MACHINE)
    with pytest.raises(reads.ReadError, match=why):
        glass(tmp_path, stack)
    assert granted.calls == []


def test_an_env_reached_only_through_an_sso_sign_in_has_no_machine_profile_and_posts_no_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    granted = grantable(tmp_path, monkeypatch, SSO_FIRST)
    with pytest.raises(reads.ReadError) as refused:
        glass(tmp_path)
    assert str(refused.value) == (
        f"no profile in {tmp_path / 'aws'} assumes frg-plat-gbl-prod-admin through source_profile from a credential_process,"
        " so plat-usw2-prod has no machine profile"
    )
    assert granted.calls == []


FAKE_DRIVE = """#!/bin/sh
printf '%s\\n' "$@"
printf 'drive=%s session=%s cwd=%s\\n' "$LONG_RUNNING_DRIVE" "${CLAUDE_CODE_SESSION_ID:-none}" "$(pwd)"
for word in "$@"; do [ -f "$word" ] && { [ -x "$word" ] && echo executable; cat "$word"; }; done
cat
echo failed >&2
exit 3
"""


@pytest.fixture
def fake_drive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    script = tmp_path / "bin" / "drive"
    script.parent.mkdir()
    script.write_text(FAKE_DRIVE)
    script.chmod(0o755)
    monkeypatch.setattr(reads, "DRIVE_BIN", script)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "this-macs-session")
    return tmp_path / "checkout"


def test_a_drive_question_runs_the_lanes_verb_here_with_its_files_and_stdin_and_answers_its_exit_and_output(fake_drive: Path) -> None:
    fake_drive.mkdir()
    proof = "/home/sprite/scratch/j7/proof.sh"
    sent = {"stdin": "DONE proven\n", "files": {proof: {"text": "#!/bin/sh\nexit 0\n", "executable": True}}}
    said = reads.answer(f"DRIVE job set j7 --repro {proof}\n{json.dumps(sent)}", fake_drive, "demo", "j7")
    assert said == "\n".join(
        [
            "drive exited 3",
            "job",
            "set",
            "j7",
            "--repro",
            proof,
            f"drive=demo session=none cwd={fake_drive.resolve()}",
            "executable",
            "#!/bin/sh",
            "exit 0",
            "DONE proven",
            "failed",
        ]
    )


@pytest.mark.parametrize("question", ['DRIVE job report j7 -\n{"stdin": ', 'DRIVE job report "j7 -'])
def test_a_drive_question_the_watch_cannot_parse_exits_2_naming_why(question: str, fake_drive: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reads.subprocess, "run", lambda *args, **kwargs: pytest.fail(f"ran {args}"))
    assert reads.answer(question, fake_drive, "demo", "j7").startswith(f"drive exited 2\n{question.partition(chr(10))[0]}: ")


def test_a_drive_question_runs_its_verb_as_the_lanes_job_marked_as_a_sprite_lane_so_drive_refuses_its_writes_to_any_other(fake_drive: Path) -> None:
    fake_drive.mkdir()
    reads.DRIVE_BIN.write_text(f'#!/bin/sh\necho "${reads.SPRITE_LANE} ${state.OWN_JOB_ENV}"\n')
    assert reads.answer("DRIVE status", fake_drive, "demo", "j7") == "drive exited 0\n1 j7"


def test_a_drive_answer_carries_all_its_verb_printed_as_a_mac_lane_reads_it(fake_drive: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_drive.mkdir()
    monkeypatch.setattr(reads, "BUDGET", 5)
    reads.DRIVE_BIN.write_text("#!/bin/sh\necho 'every return j2051 took'\n")
    assert reads.answer("DRIVE job report j2051", fake_drive, "demo", "j7") == "drive exited 0\nevery return j2051 took"


def test_a_drive_question_whose_block_shows_a_picture_on_its_sprite_runs_on_a_copy_kept_in_the_jobs_scratch_here_and_refuses_when_it_cannot_copy(
    fake_drive: Path, state_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_drive.mkdir()
    sprite = tmp_path / "sprite"
    shot = "/home/sprite/.local/state/long-running/demo/scratch/j7/option-1-chapters-shown.png"
    (sprite / shot.lstrip("/")).parent.mkdir(parents=True)
    (sprite / shot.lstrip("/")).write_bytes(b"png bytes")
    ssh = []

    def on_sprite(workspace: str, command: str, given: bytes, seconds: float) -> subprocess.CompletedProcess:
        ssh.append(workspace)
        return subprocess.run(command.replace("-C /", f"-C {sprite}", 1), shell=True, capture_output=True, check=False)

    monkeypatch.setattr(orca, "on_sprite", on_sprite)
    monkeypatch.setattr(state, "load", lambda name: {"jobs": {"j7": {"workspace": "pool-one"}}})
    reads.DRIVE_BIN.write_text("#!/bin/sh\nshot=$(sed -n 's/^!\\[.*\\](\\(.*\\))$/\\1/p' \"$4\")\necho \"$shot\"\ncat \"$shot\"\n")
    block = "/home/sprite/.local/state/long-running/demo/scratch/j7/ask.md"
    sent = {"files": {block: {"text": f"Show me:\n1 · Chapters shown\n![1 · Chapters shown]({shot})\n", "executable": False}}}
    assert reads.answer(f"DRIVE job ask --check {block}\n{json.dumps(sent)}", fake_drive, "demo", "j7") == f"drive exited 0\n{shot}\npng bytes"
    assert (state_root / "demo" / "scratch" / "j7" / "sprite" / shot.lstrip("/")).read_bytes() == b"png bytes"
    assert ssh == ["pool-one"]
    gone = shot.replace("option-1", "option-2")
    sent["files"][block]["text"] += f"![2 · Chapters hidden]({gone})\n"
    refused = reads.answer(f"DRIVE job ask --check {block}\n{json.dumps(sent)}", fake_drive, "demo", "j7")
    assert refused.startswith(f"drive exited 2\ndrive job ask: refused, nothing ran: the pictures it shows did not copy off this Sprite: copying {shot}, {gone} off pool-one exited ")
    assert f"tar: {gone.lstrip('/')}" in refused


def launched_once_written(marker: Path, monkeypatch: pytest.MonkeyPatch, then: Callable[[], None] = lambda: None) -> list[datetime]:
    launch = subprocess.Popen
    ready_at: list[datetime] = []

    def ready(*args: object, **kwargs: object) -> subprocess.Popen:
        monkeypatch.setattr(subprocess, "Popen", launch)
        started = launch(*args, **kwargs)
        deadline = time.monotonic() + 10
        while not (marker.exists() and marker.read_text().strip()):
            if time.monotonic() > deadline:
                started.kill()
                pytest.fail(f"{args[0]} never wrote {marker}")
            time.sleep(0.02)
        ready_at.append(state.now())
        then()
        return started

    monkeypatch.setattr(subprocess, "Popen", ready)
    return ready_at


def test_a_drive_question_running_past_its_time_is_stopped_and_exits_124(fake_drive: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def slow(argv: list[str], **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(argv, reads.DRIVE_SECONDS)

    monkeypatch.setattr(reads.subprocess, "run", slow)
    assert reads.answer("DRIVE job report j7 -", fake_drive, "demo", "j7") == (
        f"drive exited 124\ndrive job report ran past {reads.DRIVE_SECONDS} seconds, stopped here; what it changed before then stays"
    )


def ended(pid: int) -> bool:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False).stdout.strip()
        if not status or status.startswith("Z"):
            return True
        time.sleep(0.05)
    return False


def test_a_command_still_running_at_its_grants_end_is_killed_by_then_with_every_process_it_started_even_ignoring_sigterm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(reads, "STOP_GRACE", 1)
    child = tmp_path / "child"
    ready_at = launched_once_written(child, monkeypatch)
    with reads.stop_signals() as stops:
        granted = reads.Grant(GRANT, "plat-prod", state.now() + timedelta(seconds=3), dict(os.environ), stops)
        status = reads.run_granted(["sh", "-c", f"trap '' TERM; sleep 60 & echo $! > {child}; wait"], granted)
    assert status == reads.GRANT_ENDED
    assert state.now() < max(granted.ends, *ready_at) + timedelta(seconds=1)
    assert ended(int(child.read_text()))
    assert capsys.readouterr().err == (
        f"the break-glass grant {GRANT} ended at {clock(granted.ends.astimezone(), seconds=True)}, so its command was stopped with every process left in its group\n"
    )


def test_a_command_that_exits_under_its_grant_exits_as_it_did_and_leaves_no_process_it_started_running(tmp_path: Path) -> None:
    child = tmp_path / "child"
    with reads.stop_signals() as stops:
        granted = reads.Grant(GRANT, "plat-prod", state.now() + timedelta(minutes=50), dict(os.environ), stops)
        assert reads.run_granted(["sh", "-c", f"sleep 60 & echo $! > {child}; exit 3"], granted) == 3
    assert ended(int(child.read_text()))


def test_a_command_told_to_stop_with_less_than_its_grace_left_on_its_grant_is_killed_by_the_grants_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    child = tmp_path / "child"
    with reads.stop_signals() as stops:
        ready_at = launched_once_written(child, monkeypatch, lambda: stops.signals.append(signal.SIGHUP))
        granted = reads.Grant(GRANT, "plat-prod", state.now() + timedelta(seconds=3), dict(os.environ), stops)
        status = reads.run_granted(["sh", "-c", f"trap '' TERM; sleep 60 & echo $! > {child}; wait"], granted)
    assert status == 128 + signal.SIGHUP
    assert state.now() < max(granted.ends, *ready_at) + timedelta(seconds=1)
    assert ended(int(child.read_text()))
    assert capsys.readouterr().err == "break-glass stopped by SIGHUP, so its command was stopped with every process left in its group\n"


def test_a_command_whose_grant_ended_before_it_starts_never_starts(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(reads.subprocess, "Popen", lambda *args, **kwargs: pytest.fail(f"ran {args}"))
    granted = reads.Grant(GRANT, "plat-prod", state.now() - timedelta(seconds=1), dict(os.environ), reads.Stops(-1, []))
    assert reads.run_granted(["aws", "s3", "ls"], granted) == reads.GRANT_ENDED
    assert capsys.readouterr().err == (
        f"the break-glass grant {GRANT} ended at {clock(granted.ends.astimezone(), seconds=True)} before its command started, so nothing ran\n"
    )


def test_a_drive_told_to_stop_before_its_command_starts_never_starts_it(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(reads.subprocess, "Popen", lambda *args, **kwargs: pytest.fail(f"ran {args}"))
    granted = reads.Grant(GRANT, "plat-prod", state.now() + timedelta(minutes=50), dict(os.environ), reads.Stops(-1, [signal.SIGHUP]))
    assert reads.run_granted(["aws", "s3", "ls"], granted) == 128 + signal.SIGHUP
    assert capsys.readouterr().err == "break-glass stopped by SIGHUP before its command started, so nothing ran\n"


def test_ending_a_command_whose_group_holds_only_an_unreaped_exit_returns_its_status() -> None:
    group = subprocess.Popen(["sh", "-c", "exit 5"], start_new_session=True)
    assert ended(group.pid)
    reads.end(group, time.monotonic())
    assert group.returncode == 5


def test_a_break_glass_told_to_stop_and_stopped_again_while_it_ends_its_command_ends_every_process_and_deletes_its_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    grantable(tmp_path, monkeypatch, MACHINE)
    child = tmp_path / "child"

    def stop_once_started() -> None:
        deadline = time.monotonic() + 5
        while not (child.exists() and child.read_text().strip()):
            if time.monotonic() > deadline:
                return
            time.sleep(0.02)
        os.kill(os.getpid(), signal.SIGTERM)

    stopper = threading.Thread(target=stop_once_started)
    with reads.break_glass("sandsql/plat-usw2-prod", "count rows, read only", ASKER, tmp_path) as granted:
        config = Path(granted.env["AWS_CONFIG_FILE"])
        stopper.start()
        status = reads.run_granted(["sh", "-c", f"trap 'kill -TERM $PPID; exit 0' TERM; sleep 60 & echo $! > {child}; wait"], granted)
    stopper.join()
    assert status == 128 + signal.SIGTERM
    assert granted.stops.signals == [signal.SIGTERM, signal.SIGTERM]
    assert not config.exists()
    assert ended(int(child.read_text()))
    assert capsys.readouterr().err == "break-glass stopped by SIGTERM, so its command was stopped with every process left in its group\n"
