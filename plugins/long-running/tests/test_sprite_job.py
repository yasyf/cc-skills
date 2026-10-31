from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/long-running/scripts/sprite-job.sh"

SPRITE = """#!/usr/bin/env python3
import os, subprocess, sys
args = sys.argv[4:]
stdin = None
if args[0] == "--no-stdin":
    args, stdin = args[1:], subprocess.DEVNULL
mac, sprite = os.environ["MAC_ROOT"], os.environ["SPRITE_ROOT"]
sys.exit(subprocess.run([sprite + arg if arg.startswith(mac) else arg for arg in args[1:]], stdin=stdin).returncode)
"""

RESTARTED_JOB = """import json, pathlib, sys
job = pathlib.Path(sys.argv[1])
(job / "job.json").write_text("")
(job / "pid").write_text("")
(job / "result.json").write_text(json.dumps({"verdict": "ok"}))
"""


def executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


@pytest.mark.parametrize("relay", ["#!/bin/sh\n", "#!/bin/sh\necho 'cci: database is locked (5) (SQLITE_BUSY)' >&2\nexit 1\n"], ids=["relay-pushes", "relay-locked"])
def test_a_job_keeps_its_own_job_and_pid_files_and_drops_the_empty_refs_a_sprite_restart_left(tmp_path: Path, relay: str) -> None:
    """Root #220372, #220831: a Sprite restart emptied its copies of a job's files and three loose refs, the copy back emptied job.json and pid here, and every fetch on the Sprite read the zero object. Root #222905: a relay push that failed on SQLITE_BUSY exited before result.json came back, so the runner never settled the pass."""
    mac, sprite = tmp_path / "mac", tmp_path / "sprite"
    checkout, directory, config = mac / "checkout", mac / "finder-desk" / "45682-c051a2816b25", mac / "config"
    script = mac / "plugin" / "skills" / "long-running" / "scripts" / "finder.py"
    executable(script, RESTARTED_JOB)
    executable(script.parent / "rulings.py", "")
    executable(mac / "bin" / "sprite", SPRITE)
    executable(mac / "bin" / "cci", relay)
    executable(mac / "bin" / "sleep", "#!/bin/sh\n")
    directory.mkdir(parents=True)
    job = json.dumps({"drive": "d1", "checkout": str(checkout)})
    (directory / "job.json").write_text(job)
    (directory / "pid").write_text("4242\n")
    (config / "long-running" / "drives").mkdir(parents=True)
    (config / "long-running" / "drives" / "d1.json").write_text(json.dumps({"state_dir": str(mac / "state")}))
    remote = Path(f"{sprite}{checkout}")
    subprocess.run(["git", "init", "-q", str(remote)], check=True)
    emptied = remote / ".git" / "refs" / "remotes" / "origin" / "resume-first-turn-1"
    emptied.parent.mkdir(parents=True)
    emptied.write_text("")

    env = {**os.environ, "PATH": f"{mac / 'bin'}:{os.environ['PATH']}", "MAC_ROOT": str(mac), "SPRITE_ROOT": str(sprite), "CLAUDE_CONFIG_DIR": str(config)}
    subprocess.run(["sh", str(SCRIPT), "vet4", str(script), str(directory)], env=env, check=True, capture_output=True, timeout=60)

    assert (directory / "job.json").read_text() == job
    assert (directory / "pid").read_text() == "4242\n"
    assert json.loads((directory / "result.json").read_text()) == {"verdict": "ok"}
    assert not emptied.exists()


def test_the_sprite_reads_the_register_from_the_jobs_checkout_never_the_launching_machines(tmp_path: Path) -> None:
    """Root #226668: fleet sprites failed every rulings slice for three minutes because the registry they were sent named this machine's checkout, a path no Sprite holds. Owner 5:19 PM Oct 30 through root #228532: the register itself ships with the job as a snapshot for that checkout, so no Sprite rebuilds it cold."""
    mac, sprite = tmp_path / "mac", tmp_path / "sprite"
    checkout, directory, config = mac / "checkout", mac / "fidelity-pipeline" / "46097-48963ab63767-226305", mac / "config"
    script = mac / "plugin" / "skills" / "long-running" / "scripts" / "fidelity.py"
    executable(script, RESTARTED_JOB)
    executable(script.parent / "rulings.py", "")
    executable(mac / "bin" / "sprite", SPRITE)
    executable(mac / "bin" / "cci", "#!/bin/sh\n")
    directory.mkdir(parents=True)
    (directory / "job.json").write_text(json.dumps({"drive": "d1", "checkout": str(checkout)}))
    registry = config / "long-running" / "drives" / "d1.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({"checkout": str(mac / "workspaces" / "root-checkout"), "state_dir": str(mac / "state")}))
    subprocess.run(["git", "init", "-q", f"{sprite}{checkout}"], check=True)
    kept = {"repo": str(mac / "workspaces" / "root-checkout"), "tips": "refs/cc-notes/ledgers/r 1111", "ledger": "r", "rows": {"rule/a": {"words": "Relay code lives in relay-journal."}}}
    (mac / "state").mkdir()
    (mac / "state" / "register-state.json").write_text(json.dumps(kept))
    (mac / "state" / "register-seed.answers.json").write_text("[]")

    env = {**os.environ, "PATH": f"{mac / 'bin'}:{os.environ['PATH']}", "MAC_ROOT": str(mac), "SPRITE_ROOT": str(sprite), "CLAUDE_CONFIG_DIR": str(config)}
    subprocess.run(["sh", str(SCRIPT), "fleet2", str(script), str(directory)], env=env, check=True, capture_output=True, timeout=60)

    assert json.loads(Path(f"{sprite}{mac}/state/register-state.json").read_text()) == {**kept, "repo": str(checkout), "snapshot": True}
    assert json.loads((mac / "state" / "register-state.json").read_text()) == kept
    assert not Path(f"{sprite}{mac}/state/register-seed.answers.json").exists()

    assert json.loads(Path(f"{sprite}{registry}").read_text()) == {"checkout": str(checkout), "state_dir": str(mac / "state")}
    assert json.loads(registry.read_text())["checkout"] == str(mac / "workspaces" / "root-checkout")
