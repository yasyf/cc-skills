from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from long_running import desk_result

SCRIPT = Path(desk_result.__file__)
ENDINGS = ["normal", "crash", "dropped", "unreachable", "empty", "wrapper-gone"]


def end_job(directory: Path, ending: str) -> None:
    directory.mkdir(parents=True)
    if ending == "normal":
        desk_result.write(directory, json.dumps({"verdict": "ok"}) + "\n")
    elif ending == "empty":
        (directory / desk_result.RESULT).write_text("")
    elif ending in ("crash", "dropped", "unreachable"):
        desk_result.lost(directory, f"the job on Sprite s1 ended with status 255 and no result, as when it {ending}")


@pytest.mark.parametrize("jobs", [1, 144])
@pytest.mark.parametrize("ending", ENDINGS)
def test_every_job_ending_reads_as_one_verdict_the_moment_its_wrapper_is_gone(tmp_path: Path, jobs: int, ending: str) -> None:
    directories = [tmp_path / f"job-{n}" for n in range(jobs)]
    for directory in directories:
        end_job(directory, ending)

    outcomes = [desk_result.read(directory, lambda: True) for directory in directories]

    assert all(outcome is not None for outcome in outcomes)
    if ending == "normal":
        assert outcomes == [{"verdict": "ok"}] * jobs
    else:
        assert all("error" in outcome and outcome[desk_result.LOST] for outcome in outcomes)
    assert [sorted(path.name for path in directory.iterdir()) for directory in directories] == [[desk_result.RESULT] if ending != "wrapper-gone" else []] * jobs


def test_a_job_whose_wrapper_still_runs_and_wrote_nothing_reads_as_pending(tmp_path: Path) -> None:
    assert desk_result.read(tmp_path, lambda: False) is None


def test_the_wrapper_is_asked_before_the_record_is_read_so_a_record_written_as_it_exits_is_never_missed(tmp_path: Path) -> None:
    def exits_after_writing() -> bool:
        desk_result.write(tmp_path, json.dumps({"verdict": "ok"}) + "\n")
        return True

    assert desk_result.read(tmp_path, exits_after_writing) == {"verdict": "ok"}


def test_a_lost_failure_never_replaces_the_record_the_job_wrote(tmp_path: Path) -> None:
    desk_result.write(tmp_path, json.dumps({"verdict": "ok"}) + "\n")

    desk_result.lost(tmp_path, "the wrapper ended with status 1")

    assert desk_result.read(tmp_path, lambda: True) == {"verdict": "ok"}
    assert [path.name for path in tmp_path.iterdir()] == [desk_result.RESULT]


@pytest.mark.parametrize("text", ["", '{"verdict": ', "[]", "7"], ids=["empty", "cut", "list", "number"])
def test_a_record_the_sprite_kept_empty_or_cut_is_never_adopted(tmp_path: Path, text: str) -> None:
    run = subprocess.run([sys.executable, "-I", str(SCRIPT), "adopt", str(tmp_path)], input=text, text=True, capture_output=True)

    assert run.returncode == 0
    assert list(tmp_path.iterdir()) == []


def test_a_whole_record_is_adopted_and_a_lost_failure_is_written_by_the_command_line(tmp_path: Path) -> None:
    adopted, lost = tmp_path / "adopted", tmp_path / "lost"
    adopted.mkdir()
    lost.mkdir()

    subprocess.run([sys.executable, "-I", str(SCRIPT), "adopt", str(adopted)], input='{"verdict": "ok"}\n', text=True, check=True)
    subprocess.run([sys.executable, "-I", str(SCRIPT), "lost", str(lost), "the job ended with status 7 and no result"], check=True)

    assert desk_result.read(adopted, lambda: True) == {"verdict": "ok"}
    assert desk_result.read(lost, lambda: True) == {"error": "the job ended with status 7 and no result", desk_result.LOST: True}
