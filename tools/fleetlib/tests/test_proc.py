import pytest

from fleetlib import proc


def test_run_returns_completed_process():
    assert proc.run("true").returncode == 0


def test_run_exits_on_failure():
    with pytest.raises(SystemExit, match="FAIL"):
        proc.run("false")


def test_run_passes_stdin():
    assert proc.run("cat", stdin="hello").stdout == "hello"


def test_try_run_reports_failure():
    assert proc.try_run("false").returncode == 1
