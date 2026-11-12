import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

CLIENT = Path(__file__).resolve().parents[1] / "lib" / "sprite" / "drive"
FAKE_ORCA = """#!/usr/bin/env python3
import json, pathlib, sys
here = pathlib.Path(__file__).parent
with (here / "asked.jsonl").open("a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
answers = json.loads((here / "answers.json").read_text())
print(json.dumps({"result": answers[len((here / "asked.jsonl").read_text().splitlines()) - 1]}))
"""


def drive(tmp_path: Path, answers: list[dict], *argv: str, stdin: str = "") -> tuple[subprocess.CompletedProcess, list[list[str]]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "orca").write_text(FAKE_ORCA)
    (bin_dir / "orca").chmod(0o755)
    (bin_dir / "answers.json").write_text(json.dumps(answers))
    done = subprocess.run(
        [sys.executable, str(CLIENT), *argv],
        cwd=tmp_path,
        env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"},
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )
    asked = bin_dir / "asked.jsonl"
    return done, [json.loads(line) for line in asked.read_text().splitlines()] if asked.exists() else []


def test_a_verb_is_asked_of_the_mac_with_its_stdin_and_files_and_exits_with_the_status_its_answer_names(tmp_path: Path) -> None:
    proof = tmp_path / "proof.sh"
    proof.write_text("#!/bin/sh\nexit 0\n")
    proof.chmod(0o755)
    done, asked = drive(tmp_path, [{"answer": "drive exited 3\nrefused: x"}], "job", "report", "j7", "-", "--repro", str(proof), stdin="DONE proven\n")
    assert (done.returncode, done.stdout) == (3, "refused: x\n")
    [words] = asked
    assert (words[:3], words[4:]) == (["orchestration", "ask", "--question"], ["--wait", "--timeout-ms", "600000", "--json"])
    line, _, sent = words[3].partition("\n")
    assert line == f"DRIVE {shlex.join(['job', 'report', 'j7', '-', '--repro', str(proof)])}"
    assert json.loads(sent) == {"stdin": "DONE proven\n", "files": {str(proof): {"text": "#!/bin/sh\nexit 0\n", "executable": True}}}


def test_a_wait_that_times_out_resumes_the_same_question_until_it_is_answered(tmp_path: Path) -> None:
    done, asked = drive(tmp_path, [{"timedOut": True, "messageId": "msg_1"}, {"timedOut": True, "messageId": "msg_1"}, {"answer": "drive exited 0\nkept"}], "status")
    assert (done.returncode, done.stdout) == (0, "kept\n")
    assert [words[2:4] for words in asked] == [["--question", "DRIVE status\n" + json.dumps({"files": {}})], ["--resume", "msg_1"], ["--resume", "msg_1"]]


@pytest.mark.parametrize("answer", ["", "no exit line"])
def test_an_answer_naming_no_exit_status_exits_1_printing_it(answer: str, tmp_path: Path) -> None:
    done, _ = drive(tmp_path, [{"answer": answer}], "status")
    assert (done.returncode, done.stdout) == (1, f"{answer}\n" if answer else "")


def test_a_question_past_what_an_orca_argument_carries_is_refused_and_sends_nothing(tmp_path: Path) -> None:
    done, asked = drive(tmp_path, [], "job", "report", "j7", "-", stdin="x" * 130_000)
    assert (done.returncode, asked) == (2, [])
    assert done.stderr.startswith("drive: refused, nothing sent: its words, stdin and files come to ")
