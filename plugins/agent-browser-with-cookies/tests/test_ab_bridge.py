"""Guards for `ab --bridge`, run against stub cookiesync and agent-browser CLIs."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

AB = Path(__file__).resolve().parents[1] / "bin" / "ab"
BRIDGE_URL = "ws://127.0.0.1:9333/devtools/browser/token"

COOKIESYNC = """#!/bin/bash
case "$1 $2" in
  "requestor "*) echo test-requestor ;;
  "bridge open") echo '{"url":"%s"}' ;;
  "bridge stop") ;;
  *) exit 64 ;;
esac
""" % BRIDGE_URL

AGENT_BROWSER = """#!/bin/bash
echo "${AGENT_BROWSER_CDP:--} $*" >>"$AB_LOG"
[ "$1" = --session ] && shift 2
case "$*" in
  "session list" | "get title" | "close" | "snapshot") ;;
  *) echo "Unknown command: $1" >&2; exit 1 ;;
esac
"""


def stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(body)
    path.chmod(0o755)


def run_ab(tmp_path: Path, *argv: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub(bin_dir, "cookiesync", COOKIESYNC)
    stub(bin_dir, "agent-browser", AGENT_BROWSER)
    log = tmp_path / "agent-browser.log"
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "AB_LOG": str(log),
        "BROWSERBASE_API_KEY": "unused-by-the-bridge",
    }
    proc = subprocess.run([str(AB), *argv], capture_output=True, text=True, env=env)
    return proc, log.read_text().splitlines() if log.exists() else []


def test_bridge_attach_probe_uses_get_title(tmp_path: Path):
    proc, calls = run_ab(tmp_path, "--bridge", "snapshot")
    assert proc.returncode == 0, proc.stderr
    probe = [c for c in calls if c.startswith(BRIDGE_URL)]
    assert len(probe) == 1
    assert probe[0].endswith(" get title")
    assert calls[-1].startswith("- --session ab-") and calls[-1].endswith(" snapshot")


def test_bridge_close_closes_the_session(tmp_path: Path):
    proc, calls = run_ab(tmp_path, "--bridge", "close")
    assert proc.returncode == 0, proc.stderr
    assert [c.split()[-1] for c in calls] == ["close"]
