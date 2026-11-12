"""Guards for `ab --bridge`, run against stub cookiesync and agent-browser CLIs."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

AB = Path(__file__).resolve().parents[1] / "bin" / "ab"
BRIDGE_URL = "ws://127.0.0.1:9333/devtools/browser/token"

COOKIESYNC = """#!/bin/bash
echo "$*" >>"$CS_LOG"
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
  "session list" | "get title" | "close" | "snapshot" | "open "*) ;;
  *) echo "Unknown command: $1" >&2; exit 1 ;;
esac
"""


def stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text(body)
    path.chmod(0o755)


def run_ab(tmp_path: Path, *argv: str) -> tuple[subprocess.CompletedProcess[str], list[str], list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub(bin_dir, "cookiesync", COOKIESYNC)
    stub(bin_dir, "agent-browser", AGENT_BROWSER)
    log = tmp_path / "agent-browser.log"
    env = {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "AB_LOG": str(log),
        "CS_LOG": str(tmp_path / "cookiesync.log"),
        "BROWSERBASE_API_KEY": "unused-by-the-bridge",
    }
    proc = subprocess.run([str(AB), *argv], capture_output=True, text=True, env=env)
    cs_log = tmp_path / "cookiesync.log"
    return (
        proc,
        log.read_text().splitlines() if log.exists() else [],
        [c for c in cs_log.read_text().splitlines() if c != "requestor"] if cs_log.exists() else [],
    )


def test_bridge_attach_probe_uses_get_title(tmp_path: Path):
    proc, calls, _ = run_ab(tmp_path, "--bridge", "open", "https://app.slack.com/")
    assert proc.returncode == 0, proc.stderr
    probe = [c for c in calls if c.startswith(BRIDGE_URL)]
    assert len(probe) == 1
    assert probe[0].endswith(" get title")
    assert calls[-1].startswith("- --session ab-") and calls[-1].endswith(" open https://app.slack.com/")


def test_bridge_seeds_only_the_opened_url_and_each_bridge_url(tmp_path: Path):
    proc, _, opens = run_ab(
        tmp_path, "--bridge=chrome:Work", "--bridge-url=https://slack.com/api", "open", "https://app.slack.com/"
    )
    assert proc.returncode == 0, proc.stderr
    assert opens == ["bridge open --json chrome:Work -- https://app.slack.com/ https://slack.com/api"]


def test_bridge_with_no_url_opens_nothing(tmp_path: Path):
    proc, calls, opens = run_ab(tmp_path, "--bridge", "snapshot")
    assert proc.returncode == 2
    assert "start with 'ab --bridge open <url>'" in proc.stderr
    assert opens == []
    assert [c.split()[-1] for c in calls] == ["list"]


def test_bridge_close_closes_the_session(tmp_path: Path):
    proc, calls, _ = run_ab(tmp_path, "--bridge", "close")
    assert proc.returncode == 0, proc.stderr
    assert [c.split()[-1] for c in calls] == ["close"]
