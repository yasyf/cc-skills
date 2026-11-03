import os
import subprocess
import time
import urllib.request
from pathlib import Path


def serve(env, log):
    daemon = subprocess.Popen(["cci", "daemon", "run"], env=env, stdout=log, stderr=subprocess.STDOUT)
    deadline = time.monotonic() + 30
    while True:
        try:
            urllib.request.urlopen("http://127.0.0.1:7377/v1/drives", timeout=1).close()
            return daemon
        except OSError:
            assert daemon.poll() is None and time.monotonic() < deadline, Path(log.name).read_text()
            time.sleep(0.1)


def post(env, to, text):
    subprocess.run(["cci", "post", "--drive", "ci", "--lane", "ci-poster", "--kind", "note", "--to", to, "--text", text], env=env, check=True)


def test_a_daemon_restart_past_the_stream_grace_never_ends_the_wait(tmp_path):
    env = {
        **os.environ,
        "CCI_HOME": str(tmp_path / "cci"),
        "CLAUDE_CODE_SESSION_ID": "lr-wait-daemon",
        "CODEX_THREAD_ID": "",
        "CCI_AGENT_ID": "",
        "PATH": os.environ["CCI_DAEMON_BINARY_DIR"] + os.pathsep + os.environ["PATH"],
    }
    with open(tmp_path / "daemon.log", "w") as log:
        daemon = serve(env, log)
        post(env, "bystander", "the drive exists")
        waiter = subprocess.Popen([os.environ["LR_BINARY"], "wait", "120", "cci:ci:landing-desk"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            time.sleep(35)
            daemon.terminate()
            daemon.wait(timeout=30)
            daemon = serve(env, log)
            time.sleep(3)
            assert waiter.poll() is None, waiter.communicate()
            post(env, "landing-desk", "go after the restart")
            out, err = waiter.communicate(timeout=30)
        finally:
            waiter.kill()
            daemon.terminate()
            daemon.wait(timeout=30)
    assert waiter.returncode == 0, err
    assert "go after the restart" in out, out
