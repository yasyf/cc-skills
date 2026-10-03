"""Guards for `abwc-seed --local`, run against stub cookiesync, agent-browser, and timeout CLIs."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

SEED = Path(__file__).resolve().parents[1] / "bin" / "abwc-seed"

COOKIESYNC = """#!/bin/bash
case "$1" in
  requestor) echo test-requestor ;;
  self) echo me@studio ;;
  browser) printf 'me@studio:arc:Default\\nme@studio:chrome:Default\\npeer@far:chrome:Default\\n' ;;
  cookies)
    case "$3" in
      playwright) echo '{"cookies":[],"origins":[]}' ;;
      webstorage) echo '{"origins":[]}' ;;
    esac
    ;;
  *) exit 64 ;;
esac
"""

AGENT_BROWSER = """#!/bin/bash
[ "$1" = --session ] && shift 2
echo "$*" >>"$AB_LOG"
case "$1 $2" in
  "state load")
    if [ -n "${READER_READY:-}" ]; then
      cat "$3" >/dev/null &
      echo $! >"$READER_PID"
      touch "$READER_READY"
      exec sleep 30
    fi
    cat "$3" >/dev/null
    ;;
  "open "*) ;;
  "get url") echo "$LANDED" ;;
  *) echo "Unknown command: $1" >&2; exit 1 ;;
esac
"""

SCUTIL = """#!/bin/bash
echo "Test Mac"
"""

WRITER_TIMES_OUT = """#!/bin/bash
while [[ "$1" == -* ]]; do
  [[ "$1" == -k ]] && shift
  shift
done
shift
if [[ "$*" == *seedwriter* ]]; then
  until [ -e "$READER_READY" ]; do sleep 0.05; done
  sleep 0.3
  exit 124
fi
exec "$@"
"""


def stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(body)
    path.chmod(0o755)


def seed(tmp_path: Path, landed: str, *, wedge: bool = False) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub(bin_dir, "cookiesync", COOKIESYNC)
    stub(bin_dir, "agent-browser", AGENT_BROWSER)
    stub(bin_dir, "scutil", SCUTIL)
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "AB_LOG": str(tmp_path / "agent-browser.log"),
        "LANDED": landed,
    }
    if wedge:
        stub(bin_dir, "timeout", WRITER_TIMES_OUT)
        env["READER_READY"] = str(tmp_path / "reader-ready")
        env["READER_PID"] = str(tmp_path / "reader.pid")
    return subprocess.run(
        [str(SEED), "--local", "https://app.example.com/workflow"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def test_login_landing_names_where_to_sign_in(tmp_path: Path):
    proc = seed(tmp_path, "https://app.example.com/account/login?next=%2Fworkflow&token=hidden")
    assert proc.returncode == 3, proc.stderr
    assert "no session for app.example.com in any reachable browser" in proc.stderr
    assert "landed on https://app.example.com/account/login)" in proc.stderr
    assert "sign in at https://app.example.com in Chrome or Arc on Test Mac (arc:Default, chrome:Default)" in proc.stderr
    assert "hidden" not in proc.stderr
    calls = (tmp_path / "agent-browser.log").read_text().splitlines()
    assert calls[-2:] == ["open https://app.example.com/workflow", "get url"]


def test_app_landing_succeeds(tmp_path: Path):
    proc = seed(tmp_path, "https://app.example.com/workflow")
    assert proc.returncode == 0, proc.stderr
    assert "no session" not in proc.stderr


def test_exit_releases_a_reader_blocked_on_the_fifo(tmp_path: Path):
    proc = seed(tmp_path, "unused", wedge=True)
    assert proc.returncode == 1
    assert "timed out (status 124)" in proc.stderr
    reader = int((tmp_path / "reader.pid").read_text())
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(reader, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)
    os.kill(reader, signal.SIGKILL)
    raise AssertionError("the FIFO reader was still blocked 5s after abwc-seed exited")
