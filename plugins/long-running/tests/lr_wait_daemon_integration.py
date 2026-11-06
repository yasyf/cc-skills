import os
import subprocess
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def bus(tmp_path):
    env = {
        **os.environ,
        "CCI_HOME": str(tmp_path / "cci"),
        "CLAUDE_CODE_SESSION_ID": "lr-wait-daemon",
        "CODEX_THREAD_ID": "",
        "CCI_AGENT_ID": "",
        "PATH": os.environ["CCI_DAEMON_BINARY_DIR"] + os.pathsep + os.environ["PATH"],
    }
    log = tmp_path / "daemon.log"
    daemons = []

    def serve():
        daemon = subprocess.Popen(["cci", "daemon", "run"], env=env, stdout=log.open("a"), stderr=subprocess.STDOUT)
        daemons.append(daemon)
        deadline = time.monotonic() + 30
        while True:
            try:
                urllib.request.urlopen("http://127.0.0.1:7377/v1/drives", timeout=1).close()
                return
            except OSError:
                assert daemon.poll() is None and time.monotonic() < deadline, log.read_text()
                time.sleep(0.1)

    def stop():
        daemons[-1].terminate()
        daemons[-1].wait(timeout=30)

    def post(to, text):
        subprocess.run(["cci", "post", "--drive", "ci", "--lane", "ci-poster", "--kind", "note", "--to", to, "--text", text], env=env, check=True)

    def wait(seconds, lane):
        return subprocess.Popen([os.environ["LR_BINARY"], "wait", str(seconds), f"cci:ci:{lane}"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    serve()
    post("bystander", "the drive exists")
    try:
        yield SimpleNamespace(env=env, serve=serve, stop=stop, post=post, wait=wait)
    finally:
        for daemon in daemons:
            daemon.terminate()
            daemon.wait(timeout=30)


def test_a_daemon_restart_past_the_stream_grace_never_ends_the_wait(bus):
    waiter = bus.wait(120, "landing-desk")
    try:
        time.sleep(35)
        bus.stop()
        bus.serve()
        time.sleep(3)
        assert waiter.poll() is None, waiter.communicate()
        bus.post("landing-desk", "go after the restart")
        out, err = waiter.communicate(timeout=30)
    finally:
        waiter.kill()
    assert waiter.returncode == 0, err
    assert "go after the restart" in out, out


def test_a_daemon_down_past_the_old_grace_keeps_the_wait_and_says_when_it_is_back(bus):
    waiter = bus.wait(180, "landing-desk")
    try:
        time.sleep(2)
        bus.stop()
        time.sleep(40)
        assert waiter.poll() is None, waiter.communicate()
        bus.serve()
        bus.post("landing-desk", "go after the outage")
        out, err = waiter.communicate(timeout=60)
    finally:
        waiter.kill()
    assert waiter.returncode == 0, err
    assert "go after the outage" in out, out
    assert err.count("the cci daemon stream for landing-desk is back after") == 1, err


def test_a_first_wait_starts_where_the_lane_has_read_and_a_second_repeats_nothing(bus):
    bus.post("landing-desk", "already read")
    read = subprocess.run(["cci", "tail", "--drive", "ci", "--cursor", "landing-desk", "--to", "landing-desk"], env=bus.env, capture_output=True, text=True, check=True)
    assert "already read" in read.stdout, read
    bus.post("landing-desk", "the assignment")

    out, err = bus.wait(30, "landing-desk").communicate(timeout=60)
    assert "the assignment" in out and "already read" not in out, (out, err)

    out, err = bus.wait(2, "landing-desk").communicate(timeout=60)
    assert out.startswith("QUIET"), (out, err)


def test_a_lane_with_no_cursor_sees_nothing_older_than_its_first_wait(bus):
    bus.post("fresh-desk", "history")
    out, err = bus.wait(2, "fresh-desk").communicate(timeout=60)
    assert out.startswith("QUIET"), (out, err)

    bus.post("fresh-desk", "after the first wait")
    out, err = bus.wait(30, "fresh-desk").communicate(timeout=60)
    assert "after the first wait" in out and "history" not in out, (out, err)
