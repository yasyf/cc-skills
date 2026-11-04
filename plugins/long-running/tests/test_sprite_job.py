from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/long-running/scripts"
LR_PRELUDE = "#!/usr/bin/env python3\nimport sys\nsys.argv = sys.argv[2:]\n"

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
(job / "job.log").write_text("")
(job / "result.json").write_text(json.dumps({"verdict": "ok"}))
"""


def executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


def plugin_with_job(mac: Path, job: str) -> Path:
    """A plugin copy whose `lr grade <grader> <dir>` runs `job` with the job's directory as its one argument; returns its sprite-job.sh."""
    scripts = mac / "plugin" / "skills" / "long-running" / "scripts"
    executable(scripts / "sprite-job.sh", (SCRIPTS / "sprite-job.sh").read_text())
    executable(scripts / "long_running" / "desk_result.py", (SCRIPTS / "long_running" / "desk_result.py").read_text())
    executable(scripts / "rulings.py", "")
    for tool in ("fresh-token.sh", "gh", "ccx"):
        executable(scripts / "sprite-fresh-token" / tool, (SCRIPTS / "sprite-fresh-token" / tool).read_text())
    executable(mac / "plugin" / "bin" / "lr", LR_PRELUDE + job)
    return scripts / "sprite-job.sh"


def test_a_finished_job_returns_without_waiting_out_the_tender_sleep(tmp_path: Path) -> None:
    mac, sprite = tmp_path / "mac", tmp_path / "sprite"
    checkout, directory, config = mac / "checkout", mac / "finder-desk" / "45682-c051a2816b25", mac / "config"
    job_script, grader = plugin_with_job(mac, RESTARTED_JOB), "finder"
    executable(mac / "bin" / "sprite", SPRITE)
    executable(mac / "bin" / "cci", "#!/bin/sh\n")
    executable(mac / "bin" / "sleep", '#!/bin/sh\n[ "$1" = 60 ] && exec /bin/sleep 300\nexec /bin/sleep 0.05\n')
    directory.mkdir(parents=True)
    (directory / "job.json").write_text(json.dumps({"drive": "d1", "checkout": str(checkout)}))
    (config / "long-running" / "drives").mkdir(parents=True)
    (config / "long-running" / "drives" / "d1.json").write_text(json.dumps({"state_dir": str(mac / "state")}))
    subprocess.run(["git", "init", "-q", f"{sprite}{checkout}"], check=True)

    env = {**os.environ, "PATH": f"{mac / 'bin'}:{os.environ['PATH']}", "MAC_ROOT": str(mac), "SPRITE_ROOT": str(sprite), "CLAUDE_CONFIG_DIR": str(config)}
    subprocess.run(["sh", str(job_script), "vet4", grader, str(directory)], env=env, check=True, capture_output=True, timeout=60)

    assert json.loads((directory / "result.json").read_text()) == {"verdict": "ok"}


@pytest.mark.parametrize("relay", ["#!/bin/sh\n", "#!/bin/sh\necho 'cci: database is locked (5) (SQLITE_BUSY)' >&2\nexit 1\n"], ids=["relay-pushes", "relay-locked"])
def test_a_job_keeps_its_own_job_pid_and_log_files_and_drops_the_empty_or_nul_filled_refs_a_sprite_restart_left(tmp_path: Path, relay: str) -> None:
    """Coordinator #220372, #220831: a Sprite restart emptied its copies of a job's files and three loose refs, the copy back emptied job.json and pid here, and every fetch on the Sprite read the zero object. Coordinator #222905: a relay push that failed on SQLITE_BUSY exited before result.json came back, so the runner never settled the pass."""
    mac, sprite = tmp_path / "mac", tmp_path / "sprite"
    checkout, directory, config = mac / "checkout", mac / "finder-desk" / "45682-c051a2816b25", mac / "config"
    job_script, grader = plugin_with_job(mac, RESTARTED_JOB), "finder"
    executable(mac / "bin" / "sprite", SPRITE)
    executable(mac / "bin" / "cci", relay)
    executable(mac / "bin" / "sleep", "#!/bin/sh\n")
    directory.mkdir(parents=True)
    job = json.dumps({"drive": "d1", "checkout": str(checkout)})
    (directory / "job.json").write_text(job)
    (directory / "pid").write_text("4242\n")
    (directory / "job.log").write_text("sprite-job: the plugin copy took 3 s\n")
    (config / "long-running" / "drives").mkdir(parents=True)
    (config / "long-running" / "drives" / "d1.json").write_text(json.dumps({"state_dir": str(mac / "state")}))
    remote = Path(f"{sprite}{checkout}")
    subprocess.run(["git", "init", "-q", str(remote)], check=True)
    emptied = remote / ".git" / "refs" / "remotes" / "origin" / "resume-first-turn-1"
    nulled = remote / ".git" / "refs" / "tags" / "v1"
    kept = remote / ".git" / "refs" / "remotes" / "origin" / "dev"
    emptied.parent.mkdir(parents=True)
    emptied.write_text("")
    nulled.write_bytes(b"\0" * 123)
    kept.write_text("0b0e1f2a3c4d5e6f708192a3b4c5d6e7f8091a2b\n")

    env = {**os.environ, "PATH": f"{mac / 'bin'}:{os.environ['PATH']}", "MAC_ROOT": str(mac), "SPRITE_ROOT": str(sprite), "CLAUDE_CONFIG_DIR": str(config)}
    ran = subprocess.run(["sh", str(job_script), "vet4", grader, str(directory)], env=env, check=True, capture_output=True, text=True, timeout=60)

    assert (directory / "job.json").read_text() == job
    assert (directory / "pid").read_text() == "4242\n"
    assert (directory / "job.log").read_text() == "sprite-job: the plugin copy took 3 s\n"
    assert "sprite-job: git fetch --all took " in ran.stderr
    assert json.loads((directory / "result.json").read_text()) == {"verdict": "ok"}
    assert not emptied.exists()
    assert not nulled.exists()
    assert kept.read_text() == "0b0e1f2a3c4d5e6f708192a3b4c5d6e7f8091a2b\n"


def test_a_job_named_by_script_path_runs_that_script_for_the_judge(tmp_path: Path) -> None:
    mac, sprite = tmp_path / "mac", tmp_path / "sprite"
    checkout, directory, config = mac / "checkout", mac / "judge" / "a1b2c3", mac / "config"
    job_script = plugin_with_job(mac, "raise SystemExit('lr ran instead of the script')\n")
    judge_job = job_script.parent / "judge_job.py"
    executable(judge_job, RESTARTED_JOB)
    executable(mac / "bin" / "sprite", SPRITE)
    executable(mac / "bin" / "cci", "#!/bin/sh\n")
    executable(mac / "bin" / "sleep", "#!/bin/sh\nexec /bin/sleep 0.05\n")
    directory.mkdir(parents=True)
    (directory / "job.json").write_text(json.dumps({"drive": "d1", "checkout": str(checkout)}))
    (config / "long-running" / "drives").mkdir(parents=True)
    (config / "long-running" / "drives" / "d1.json").write_text(json.dumps({"state_dir": str(mac / "state")}))
    subprocess.run(["git", "init", "-q", f"{sprite}{checkout}"], check=True)

    env = {**os.environ, "PATH": f"{mac / 'bin'}:{os.environ['PATH']}", "MAC_ROOT": str(mac), "SPRITE_ROOT": str(sprite), "CLAUDE_CONFIG_DIR": str(config)}
    subprocess.run(["sh", str(job_script), "vet4", str(judge_job), str(directory)], env=env, check=True, capture_output=True, timeout=60)

    assert json.loads((directory / "result.json").read_text()) == {"verdict": "ok"}


def test_the_sprite_reads_the_register_from_the_jobs_checkout_never_the_launching_machines(tmp_path: Path) -> None:
    """Coordinator #226668: fleet sprites failed every rulings slice for three minutes because the registry they were sent named this machine's checkout, a path no Sprite holds. Owner 5:19 PM Oct 30 through coordinator #228532: the register itself ships with the job as a snapshot for that checkout, so no Sprite rebuilds it cold."""
    mac, sprite = tmp_path / "mac", tmp_path / "sprite"
    checkout, directory, config = mac / "checkout", mac / "fidelity-pipeline" / "46097-48963ab63767-226305", mac / "config"
    job_script, grader = plugin_with_job(mac, RESTARTED_JOB), "fidelity"
    executable(mac / "bin" / "sprite", SPRITE)
    executable(mac / "bin" / "cci", "#!/bin/sh\n")
    executable(mac / "bin" / "sleep", "#!/bin/sh\nexec /bin/sleep 0.05\n")
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
    subprocess.run(["sh", str(job_script), "fleet2", grader, str(directory)], env=env, check=True, capture_output=True, timeout=60)

    assert json.loads(Path(f"{sprite}{mac}/state/register-state.json").read_text()) == {**kept, "repo": str(checkout), "snapshot": True}
    assert json.loads((mac / "state" / "register-state.json").read_text()) == kept
    assert not Path(f"{sprite}{mac}/state/register-seed.answers.json").exists()

    assert json.loads(Path(f"{sprite}{registry}").read_text()) == {"checkout": str(checkout), "state_dir": str(mac / "state")}
    assert json.loads(registry.read_text())["checkout"] == str(mac / "workspaces" / "root-checkout")


@pytest.mark.parametrize(("marker", "queued"), [("", False), ("1", False), ("300", False), ("50", True), ("300", True)], ids=["no-prior-fetch", "an-older-fetch", "a-newer-fetch-served-it", "waited-for-an-older-fetch", "waited-for-a-newer-fetch"])
def test_a_job_keeps_fetch_start_freshness_without_running_pack_maintenance(tmp_path: Path, marker: str, queued: bool) -> None:
    mac, sprite = tmp_path / "mac", tmp_path / "sprite"
    checkout, directory, config = mac / "checkout", mac / "finder-desk" / "46095-c2cf0bfb3661", mac / "config"
    job_script, grader = plugin_with_job(mac, RESTARTED_JOB), "finder"
    executable(mac / "bin" / "sprite", SPRITE)
    executable(mac / "bin" / "cci", "#!/bin/sh\n")
    executable(mac / "bin" / "sleep", "#!/bin/sh\nexec /bin/sleep 0.05\n")
    log, clock = tmp_path / "git.log", tmp_path / "clock"
    executable(mac / "bin" / "date", f"#!/usr/bin/env python3\nimport os, sys\nfrom pathlib import Path\nif sys.argv[1:] != ['+%s%N']: os.execv({shutil.which('date')!r}, sys.argv)\np = Path({str(clock)!r})\nnow = 100 * (len(p.read_text().splitlines()) + 1) if p.exists() else 100\nwith p.open('a') as stream: stream.write(f'{{now}}\\n')\nprint(now)\n")
    executable(mac / "bin" / "git", f"#!/bin/sh\necho \"$*\" >> {log}\ncase \" $* \" in *' fetch '*) date +%s%N >/dev/null;; esac\nexec {shutil.which('git')} \"$@\"\n")
    directory.mkdir(parents=True)
    (directory / "job.json").write_text(json.dumps({"drive": "d1", "checkout": str(checkout)}))
    (config / "long-running" / "drives").mkdir(parents=True)
    (config / "long-running" / "drives" / "d1.json").write_text(json.dumps({"state_dir": str(mac / "state")}))
    subprocess.run([shutil.which("git"), "init", "-q", f"{sprite}{checkout}"], check=True)
    fetched = Path(f"{sprite}{checkout}.fetched")
    if marker and not queued:
        fetched.write_text(marker)

    env = {**os.environ, "PATH": f"{mac / 'bin'}:{os.environ['PATH']}", "MAC_ROOT": str(mac), "SPRITE_ROOT": str(sprite), "CLAUDE_CONFIG_DIR": str(config)}
    command = ["sh", str(job_script), "vet4", grader, str(directory)]
    if queued:
        with Path(f"{sprite}{checkout}.fetch.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            with subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
                try:
                    for _ in range(600):
                        if clock.exists() or process.poll() is not None:
                            break
                        time.sleep(0.01)
                    assert clock.exists(), "job did not reach the fetch-start barrier"
                    fetched.write_text(marker)
                finally:
                    fcntl.flock(lock, fcntl.LOCK_UN)
                _, error = process.communicate(timeout=60)
                assert process.returncode == 0, error
    else:
        subprocess.run(command, env=env, check=True, capture_output=True, timeout=60)

    ran = ["fetch" if " fetch " in line else "repack" for line in log.read_text().splitlines() if " fetch " in line or " repack " in line]
    assert ran == ([] if marker == "300" else ["fetch"]), "Sprite startup ran explicit pack maintenance"
    assert fetched.read_text() == ("300" if marker == "300" else "200")
    assert clock.read_text().splitlines() == (["100"] if marker == "300" else ["100", "200", "300"])
    assert json.loads((directory / "result.json").read_text()) == {"verdict": "ok"}


def test_a_sprite_whose_plugin_copy_is_missing_files_gets_the_whole_copy_again(tmp_path: Path) -> None:
    """Coordinator #234076, tooling-misc-3 #234214: long-running 0.7.701 installed truncated on this machine from 8:04 to 8:14 PM Oct 30, and a Sprite copied the plugin only when it lacked the version's directory, so a Sprite that copied the truncated install kept it after the reinstall."""
    mac, sprite = tmp_path / "mac", tmp_path / "sprite"
    checkout, directory, config = mac / "checkout", mac / "finder-desk" / "46075-decdc3400000", mac / "config"
    job_script, grader = plugin_with_job(mac, RESTARTED_JOB), "finder"
    (job_script.parent / "__pycache__").mkdir()
    (job_script.parent / "__pycache__" / "finder.cpython-313.pyc").write_text("")
    (mac / "plugin" / ".in_use").mkdir()
    (mac / "plugin" / ".in_use" / "4242").write_text("")
    executable(Path(f"{sprite}{job_script.parent}") / "rulings.py", "")
    executable(mac / "bin" / "sprite", SPRITE)
    executable(mac / "bin" / "cci", "#!/bin/sh\n")
    executable(mac / "bin" / "sleep", "#!/bin/sh\nexec /bin/sleep 0.05\n")
    directory.mkdir(parents=True)
    (directory / "job.json").write_text(json.dumps({"drive": "d1", "checkout": str(checkout)}))
    (config / "long-running" / "drives").mkdir(parents=True)
    (config / "long-running" / "drives" / "d1.json").write_text(json.dumps({"state_dir": str(mac / "state")}))
    subprocess.run(["git", "init", "-q", f"{sprite}{checkout}"], check=True)

    env = {**os.environ, "PATH": f"{mac / 'bin'}:{os.environ['PATH']}", "MAC_ROOT": str(mac), "SPRITE_ROOT": str(sprite), "CLAUDE_CONFIG_DIR": str(config)}
    subprocess.run(["sh", str(job_script), "vet4", grader, str(directory)], env=env, check=True, capture_output=True, timeout=60)

    assert Path(f"{sprite}{mac}/plugin/bin/lr").read_text() == LR_PRELUDE + RESTARTED_JOB
    assert not Path(f"{sprite}{job_script.parent}/__pycache__").exists()
    assert json.loads((directory / "result.json").read_text()) == {"verdict": "ok"}
    shipped = Path(f"{sprite}{mac}/plugin.digest").read_text()
    (mac / "plugin" / ".in_use" / "4343").write_text("")
    subprocess.run(["sh", str(job_script), "vet4", grader, str(directory)], env=env, check=True, capture_output=True, timeout=60)
    assert Path(f"{sprite}{mac}/plugin.digest").read_text() == shipped and not Path(f"{sprite}{mac}/plugin/.in_use").exists()


def test_a_sprite_names_a_new_plugin_copy_by_one_rename_and_deletes_an_older_copy_only_once_no_job_holds_it(tmp_path: Path) -> None:
    """tooling-misc-3 #234214, cc-skills#1342: the swap removed the plugin directory before it moved the new one in, so a job that started in between ran from nothing, and it deleted the copy under a job still running from it."""
    mac, sprite = tmp_path / "mac", tmp_path / "sprite"
    checkout, directory, config = mac / "checkout", mac / "finder-desk" / "46075-decdc3400000", mac / "config"
    job_script, grader = plugin_with_job(mac, RESTARTED_JOB), "finder"
    executable(mac / "bin" / "sprite", SPRITE)
    executable(mac / "bin" / "cci", "#!/bin/sh\n")
    executable(mac / "bin" / "sleep", "#!/bin/sh\nexec /bin/sleep 0.05\n")
    directory.mkdir(parents=True)
    (directory / "job.json").write_text(json.dumps({"drive": "d1", "checkout": str(checkout)}))
    (config / "long-running" / "drives").mkdir(parents=True)
    (config / "long-running" / "drives" / "d1.json").write_text(json.dumps({"state_dir": str(mac / "state")}))
    subprocess.run(["git", "init", "-q", f"{sprite}{checkout}"], check=True)
    env = {**os.environ, "PATH": f"{mac / 'bin'}:{os.environ['PATH']}", "MAC_ROOT": str(mac), "SPRITE_ROOT": str(sprite), "CLAUDE_CONFIG_DIR": str(config)}

    def job(marker: str) -> Path:
        (mac / "plugin" / "marker.txt").write_text(marker)
        subprocess.run(["sh", str(job_script), "vet4", grader, str(directory)], env=env, check=True, capture_output=True, timeout=60)
        link = Path(f"{sprite}{mac}/plugin")
        assert link.is_symlink() and (link / "marker.txt").read_text() == marker
        return Path(os.readlink(link))

    first = job("one")
    held = os.open(f"{first}.lock", os.O_CREAT | os.O_RDWR)
    fcntl.flock(held, fcntl.LOCK_SH)
    second = job("two")
    assert second != first and (first / "marker.txt").read_text() == "one"
    os.close(held)
    third = job("three")
    assert not first.exists() and not second.exists() and (third / "marker.txt").read_text() == "three"


def test_a_job_that_dies_with_its_sprite_leaves_an_error_result_so_the_runner_fails_it_at_once(tmp_path: Path) -> None:
    """remote-desks-5, 9:41 PM Oct 30: dar-fleet-4 restarted at about 9:20 PM mid-pass, sprite-job.sh exited with no result, and the runner counted fidelity #46330 801543b as running for 24 minutes, since a fidelity pass fails only on an error result or its 60-minute deadline."""
    mac, sprite = tmp_path / "mac", tmp_path / "sprite"
    checkout, directory, config = mac / "checkout", mac / "fidelity-pipeline" / "46330-801543be814f-236200", mac / "config"
    job_script, grader = plugin_with_job(mac, "import shutil, sys\nshutil.rmtree(sys.argv[1])\nsys.exit(137)\n"), "fidelity"
    executable(mac / "bin" / "sprite", SPRITE)
    executable(mac / "bin" / "cci", "#!/bin/sh\n")
    executable(mac / "bin" / "sleep", "#!/bin/sh\n")
    directory.mkdir(parents=True)
    (directory / "job.json").write_text(json.dumps({"drive": "d1", "checkout": str(checkout)}))
    (config / "long-running" / "drives").mkdir(parents=True)
    (config / "long-running" / "drives" / "d1.json").write_text(json.dumps({"state_dir": str(mac / "state")}))
    subprocess.run(["git", "init", "-q", f"{sprite}{checkout}"], check=True)

    env = {**os.environ, "PATH": f"{mac / 'bin'}:{os.environ['PATH']}", "MAC_ROOT": str(mac), "SPRITE_ROOT": str(sprite), "CLAUDE_CONFIG_DIR": str(config)}
    done = subprocess.run(["sh", str(job_script), "dar-fleet-4", grader, str(directory)], env=env, capture_output=True, timeout=60)

    assert done.returncode == 137
    assert json.loads((directory / "result.json").read_text()) == {"error": "the job on Sprite dar-fleet-4 ended with status 137 and no result, as when the Sprite restarts mid-pass or cannot be reached", "no_result": True}



@pytest.mark.parametrize("kept", ["", '{"verdict": '], ids=["empty", "cut"])
def test_a_result_the_sprite_kept_empty_or_cut_leaves_an_error_result_so_the_runner_requeues_the_pass(tmp_path: Path, kept: str) -> None:
    """Coordinator #262758, 12:03 PM Oct 31: fidelity #46811 c9e543f wrote result.json on dar-fleet-10, the Sprite hibernated before the write reached its disk, the copy back brought an empty file, and every orca runner pass from 12:02 raised JSONDecodeError reading it."""
    mac, sprite = tmp_path / "mac", tmp_path / "sprite"
    checkout, directory, config = mac / "checkout", mac / "fidelity-pipeline" / "46811-c9e543fabd46-262490", mac / "config"
    job_script, grader = plugin_with_job(mac, f"import pathlib, sys\n(pathlib.Path(sys.argv[1]) / 'record.md').write_text('graded')\n(pathlib.Path(sys.argv[1]) / 'result.json').write_text({kept!r})\n"), "fidelity"
    executable(mac / "bin" / "sprite", SPRITE)
    executable(mac / "bin" / "cci", "#!/bin/sh\n")
    executable(mac / "bin" / "sleep", "#!/bin/sh\n")
    directory.mkdir(parents=True)
    (directory / "job.json").write_text(json.dumps({"drive": "d1", "checkout": str(checkout)}))
    (config / "long-running" / "drives").mkdir(parents=True)
    (config / "long-running" / "drives" / "d1.json").write_text(json.dumps({"state_dir": str(mac / "state")}))
    subprocess.run(["git", "init", "-q", f"{sprite}{checkout}"], check=True)

    env = {**os.environ, "PATH": f"{mac / 'bin'}:{os.environ['PATH']}", "MAC_ROOT": str(mac), "SPRITE_ROOT": str(sprite), "CLAUDE_CONFIG_DIR": str(config)}
    done = subprocess.run(["sh", str(job_script), "dar-fleet-10", grader, str(directory)], env=env, capture_output=True, timeout=60)

    assert done.returncode == 0
    assert (directory / "record.md").read_text() == "graded"
    assert json.loads((directory / "result.json").read_text()) == {"error": "the job on Sprite dar-fleet-10 ended with status 0 and no result, as when the Sprite restarts mid-pass or cannot be reached", "no_result": True}
    assert not (directory / ".result.json").exists()

HELPER_JOB = """import json, pathlib, subprocess, sys
job = pathlib.Path(sys.argv[1])
helpers = subprocess.run(["git", "config", "--get-all", "credential.helper"], capture_output=True, text=True).stdout.splitlines()
(job / "result.json").write_text(json.dumps({"helpers": helpers}))
"""



def test_a_job_whose_sprite_refuses_the_copy_leaves_an_error_result(tmp_path: Path) -> None:
    """remote-desks-5, 2:53 AM Oct 31: fidelity #46590 and #45410 got "failed to connect ... operation timed out" copying their job to dar-fleet-8 and dar-fleet-6, sprite-job.sh exited under set -e before it wrote a result, and the runner counted both passes as running."""
    mac = tmp_path / "mac"
    checkout, directory, config = mac / "checkout", mac / "fidelity-pipeline" / "46590-cbd6b2a5115f-247230", mac / "config"
    job_script, grader = plugin_with_job(mac, ""), "fidelity"
    executable(mac / "bin" / "sprite", "#!/bin/sh\necho 'Error: failed to start sprite command: failed to connect: read: operation timed out' >&2\nexit 1\n")
    directory.mkdir(parents=True)
    (directory / "job.json").write_text(json.dumps({"drive": "d1", "checkout": str(checkout)}))
    (config / "long-running" / "drives").mkdir(parents=True)
    (config / "long-running" / "drives" / "d1.json").write_text(json.dumps({"state_dir": str(mac / "state")}))

    env = {**os.environ, "PATH": f"{mac / 'bin'}:{os.environ['PATH']}", "CLAUDE_CONFIG_DIR": str(config)}
    done = subprocess.run(["sh", str(job_script), "dar-fleet-8", grader, str(directory)], env=env, capture_output=True, timeout=60)

    assert done.returncode == 1
    assert json.loads((directory / "result.json").read_text()) == {"error": "the job on Sprite dar-fleet-8 ended with status 1 and no result, as when the Sprite restarts mid-pass or cannot be reached", "no_result": True}

def test_every_git_command_a_job_runs_authenticates_through_gh(tmp_path: Path) -> None:
    """desk-runner GRADER-TOOLING #238341: a finder's `git rev-list --cherry-pick` lazily fetched a missing object from the partial clone with no credential helper and died on "could not read Username"; the job's own git commands now carry the helper the fetch uses."""
    mac, sprite = tmp_path / "mac", tmp_path / "sprite"
    checkout, directory, config = mac / "checkout", mac / "finder-desk" / "45681-9c366845e1d4", mac / "config"
    job_script, grader = plugin_with_job(mac, HELPER_JOB), "finder"
    executable(mac / "bin" / "sprite", SPRITE)
    executable(mac / "bin" / "cci", "#!/bin/sh\n")
    executable(mac / "bin" / "sleep", "#!/bin/sh\n")
    directory.mkdir(parents=True)
    (directory / "job.json").write_text(json.dumps({"drive": "d1", "checkout": str(checkout)}))
    (config / "long-running" / "drives").mkdir(parents=True)
    (config / "long-running" / "drives" / "d1.json").write_text(json.dumps({"state_dir": str(mac / "state")}))
    subprocess.run(["git", "init", "-q", f"{sprite}{checkout}"], check=True)

    env = {**os.environ, "PATH": f"{mac / 'bin'}:{os.environ['PATH']}", "MAC_ROOT": str(mac), "SPRITE_ROOT": str(sprite), "CLAUDE_CONFIG_DIR": str(config), "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1"}
    subprocess.run(["sh", str(job_script), "vet4", grader, str(directory)], env=env, check=True, capture_output=True, timeout=60)

    assert json.loads((directory / "result.json").read_text())["helpers"] == ["", "!gh auth git-credential"]


def test_a_key_command_that_prints_nothing_stops_the_job_before_the_sprite(tmp_path: Path) -> None:
    """desk-runner GRADER-TOOLING #240605: the GH_TOKEN command printed nothing, the job ran with an empty token, and the finder died later on "no oauth token found"; the job now stops at launch with an error result naming the key."""
    mac, sprite = tmp_path / "mac", tmp_path / "sprite"
    checkout, directory, config = mac / "checkout", mac / "finder-desk" / "45784-0ee9abb91343", mac / "config"
    job_script, grader = plugin_with_job(mac, HELPER_JOB), "finder"
    executable(mac / "bin" / "sprite", SPRITE)
    executable(mac / "bin" / "cci", "#!/bin/sh\n")
    directory.mkdir(parents=True)
    (directory / "job.json").write_text(json.dumps({"drive": "d1", "checkout": str(checkout)}))
    (config / "long-running" / "drives").mkdir(parents=True)
    (config / "long-running" / "drives" / "d1.json").write_text(json.dumps({"state_dir": str(mac / "state")}))
    subprocess.run(["git", "init", "-q", f"{sprite}{checkout}"], check=True)

    env = {**os.environ, "PATH": f"{mac / 'bin'}:{os.environ['PATH']}", "MAC_ROOT": str(mac), "SPRITE_ROOT": str(sprite), "CLAUDE_CONFIG_DIR": str(config), "SPRITE_JOB_KEYS": json.dumps({"GH_TOKEN": ["true"]})}
    done = subprocess.run(["sh", str(job_script), "vet4", grader, str(directory)], env=env, capture_output=True, text=True, timeout=60, check=False)

    assert done.returncode == 1
    assert json.loads((directory / "result.json").read_text()) == {"error": "the GH_TOKEN command printed nothing, so the job would run without GH_TOKEN"}
    assert not Path(f"{sprite}{directory}/result.json").exists()


LONG_PASS_JOB = """import json, pathlib, subprocess, sys, time
for _ in range(200):
    token = subprocess.run(["ccx"], capture_output=True, text=True).stdout.strip()
    if token == "ghs_second":
        break
    time.sleep(0.05)
(pathlib.Path(sys.argv[1]) / "result.json").write_text(json.dumps({"token": token}))
"""

CREDENTIAL_GIT = """#!/bin/sh
if [ "$1" = credential-cache ]; then
  for arg do last=$arg; done
  case $last in
    store) sed -n "s/^password=//p" >"$TOKEN_CACHE" ;;
    get) [ ! -e "$TOKEN_CACHE" ] || printf "password=%s\\n" "$(cat "$TOKEN_CACHE")" ;;
  esac
  exit 0
fi
exec "$REAL_GIT" "$@"
"""


def test_a_pass_that_outlives_its_token_reads_github_on_the_refreshed_one(tmp_path: Path) -> None:
    """finder-401-1, 378341: #48330's codex ran 58 minutes on one GH_TOKEN, and its next `ccx vcs gh` died on "GET /rate_limit: 401 Bad credentials"; a later ccx in the same job now reads the token the launching machine refreshed."""
    mac, sprite = tmp_path / "mac", tmp_path / "sprite"
    checkout, directory, config = mac / "checkout", mac / "finder-desk" / "48330-67b798e4c8ae", mac / "config"
    job_script, grader = plugin_with_job(mac, LONG_PASS_JOB), "finder"
    executable(mac / "bin" / "sprite", SPRITE)
    executable(mac / "bin" / "cci", "#!/bin/sh\n")
    executable(mac / "bin" / "git", CREDENTIAL_GIT)
    executable(mac / "bin" / "ccx", "#!/bin/sh\nprintf %s \"$GH_TOKEN\"\n")
    executable(mac / "bin" / "mint", "#!/bin/sh\nif [ -e \"$MINTED\" ]; then echo ghs_second; else : >\"$MINTED\"; echo ghs_first; fi\n")
    directory.mkdir(parents=True)
    (mac / "state").mkdir()
    (directory / "job.json").write_text(json.dumps({"drive": "d1", "checkout": str(checkout)}))
    (config / "long-running" / "drives").mkdir(parents=True)
    (config / "long-running" / "drives" / "d1.json").write_text(json.dumps({"state_dir": str(mac / "state")}))
    real_git = shutil.which("git")
    assert real_git
    subprocess.run([real_git, "init", "-q", f"{sprite}{checkout}"], check=True)

    env = {**os.environ, "PATH": f"{mac / 'bin'}:{os.environ['PATH']}", "MAC_ROOT": str(mac), "SPRITE_ROOT": str(sprite), "CLAUDE_CONFIG_DIR": str(config), "REAL_GIT": real_git, "TOKEN_CACHE": str(tmp_path / "token-cache"), "MINTED": str(tmp_path / "minted"), "SPRITE_JOB_KEYS": json.dumps({"GH_TOKEN": ["mint"]})}
    subprocess.run(["sh", str(job_script), "dar-fleet-8", grader, str(directory)], env=env, check=True, capture_output=True, timeout=60)

    assert json.loads((directory / "result.json").read_text()) == {"token": "ghs_second"}


DROPPING_SPRITE = """#!/usr/bin/env python3
import os, subprocess, sys
args = sys.argv[4:]
stdin, attached = None, True
if args[0] == "--no-stdin":
    args, stdin, attached = args[1:], subprocess.DEVNULL, False
mac, sprite = os.environ["MAC_ROOT"], os.environ["SPRITE_ROOT"]
child = subprocess.Popen([sprite + arg if arg.startswith(mac) else arg for arg in args[1:]], stdin=stdin)
try:
    sys.exit(child.wait(timeout=2 if attached else None))
except subprocess.TimeoutExpired:
    child.kill()
    sys.stderr.write("Error: connection to exec session was lost while stdin was attached; refusing unsafe automatic reconnect because input delivery is uncertain\\n")
    sys.exit(1)
"""

KEYED_JOB = """import json, os, pathlib, sys, time
time.sleep(3)
(pathlib.Path(sys.argv[1]) / "result.json").write_text(json.dumps({"key": os.environ.get("OPENAI_API_KEY")}))
"""


def test_a_job_outlives_a_dropped_connection_since_only_the_short_key_exec_forwards_stdin(tmp_path: Path) -> None:
    """desk-runner GRADER-TOOLING #251279: finder #45029 died a minute in on "connection to exec session 12580 was lost while stdin was attached; refusing unsafe automatic reconnect", since the exec that ran the whole job also carried the keys on stdin."""
    mac, sprite = tmp_path / "mac", tmp_path / "sprite"
    checkout, directory, config = mac / "checkout", mac / "finder-desk" / "45029-08cb4928b659", mac / "config"
    job_script, grader = plugin_with_job(mac, KEYED_JOB), "finder"
    executable(mac / "bin" / "sprite", DROPPING_SPRITE)
    executable(mac / "bin" / "cci", "#!/bin/sh\n")
    executable(mac / "bin" / "sleep", "#!/bin/sh\nexec /bin/sleep 0.05\n")
    directory.mkdir(parents=True)
    (directory / "job.json").write_text(json.dumps({"drive": "d1", "checkout": str(checkout)}))
    (config / "long-running" / "drives").mkdir(parents=True)
    (config / "long-running" / "drives" / "d1.json").write_text(json.dumps({"state_dir": str(mac / "state")}))
    subprocess.run(["git", "init", "-q", f"{sprite}{checkout}"], check=True)

    env = {**os.environ, "PATH": f"{mac / 'bin'}:{os.environ['PATH']}", "MAC_ROOT": str(mac), "SPRITE_ROOT": str(sprite), "CLAUDE_CONFIG_DIR": str(config), "SPRITE_JOB_KEYS": json.dumps({"OPENAI_API_KEY": ["echo", "sk-test"]})}
    subprocess.run(["sh", str(job_script), "dar-fleet-11", grader, str(directory)], env=env, check=True, capture_output=True, timeout=60)

    assert json.loads((directory / "result.json").read_text()) == {"key": "sk-test"}
    assert not Path(f"{sprite}{directory}.keys").exists()


FLAKY_SPRITE = """#!/bin/sh
if [ "$WAY" = unreachable ]; then
  echo "Error: failed to start sprite command: failed to connect: read: operation timed out" >&2
  exit 255
fi
shift 3
if [ "$1" = --no-stdin ]; then
  exec </dev/null
  shift
fi
dropped= flock=
for arg do
  case $arg in *"exec flock -s"*) flock=1 ;; esac
  [ "$WAY" = dropped ] && [ -z "$dropped" ] || continue
  case $arg in
    */finder-desk/job-[0-9]*)
      rest=${arg##*/finder-desk/job-}
      dropped=${arg%"/finder-desk/job-$rest"}/finder-desk/job-${rest%%[!0-9]*}.dropped ;;
  esac
done
if [ -n "$dropped" ] && [ -e "$dropped" ]; then
  echo "Error: failed to connect" >&2
  exit 255
fi
shift
for arg do
  shift
  case $arg in
    "$MAC_ROOT"*) set -- "$@" "$SPRITE_ROOT$arg" ;;
    *) set -- "$@" "$arg" ;;
  esac
done
"$@"
code=$?
if [ -n "$dropped" ] && [ -n "$flock" ]; then
  touch "$dropped"
  exit 255
fi
exit "$code"
"""

JOBS = {
    "normal": "import json, pathlib, sys\n(pathlib.Path(sys.argv[1]) / 'result.json').write_text(json.dumps({'verdict': 'ok'}))\n",
    "crash": "import sys\nsys.exit(1)\n",
    "dropped": "import json, pathlib, sys\n(pathlib.Path(sys.argv[1]) / 'result.json').write_text(json.dumps({'verdict': 'ok'}))\n",
    "unreachable": "",
    "empty": "import pathlib, sys\n(pathlib.Path(sys.argv[1]) / 'result.json').write_text('')\n",
}

QUICK_SLEEP = """#!/bin/sh
[ "$1" = 0.2 ] && exec /bin/sleep 0.2
exec /bin/sleep 0.05
"""


@pytest.mark.parametrize("jobs", [1, 144])
@pytest.mark.parametrize("way", list(JOBS))
def test_every_job_leaves_exactly_one_whole_result_record_however_it_ends(tmp_path: Path, jobs: int, way: str) -> None:
    """T5: a Sprite job that crashed, lost its connection, could not reach its Sprite or wrote an empty result left no usable result, fixed one case at a time in #1352, #1406, #1428, #1470 and #1477."""
    mac, sprite = tmp_path / "mac", tmp_path / "sprite"
    checkout, config = mac / "checkout", mac / "config"
    job_script, grader = plugin_with_job(mac, JOBS[way]), "finder"
    executable(mac / "bin" / "sprite", FLAKY_SPRITE)
    executable(mac / "bin" / "cci", "#!/bin/sh\n")
    executable(mac / "bin" / "sleep", QUICK_SLEEP)
    (config / "long-running" / "drives").mkdir(parents=True)
    (config / "long-running" / "drives" / "d1.json").write_text(json.dumps({"state_dir": str(mac / "state")}))
    subprocess.run(["git", "init", "-q", f"{sprite}{checkout}"], check=True)
    directories = [mac / "finder-desk" / f"job-{n}" for n in range(jobs)]
    for directory in directories:
        directory.mkdir(parents=True)
        (directory / "job.json").write_text(json.dumps({"drive": "d1", "checkout": str(checkout)}))
    env = {**os.environ, "PATH": f"{mac / 'bin'}:{os.environ['PATH']}", "MAC_ROOT": str(mac), "SPRITE_ROOT": str(sprite), "CLAUDE_CONFIG_DIR": str(config), "WAY": way}

    with ThreadPoolExecutor(16) as pool:
        runs = list(pool.map(lambda directory: subprocess.run(["sh", str(job_script), "dar-fleet-4", grader, str(directory)], env=env, capture_output=True, timeout=300), directories))

    for directory, run in zip(directories, runs):
        record = json.loads((directory / "result.json").read_text())
        if way == "normal":
            assert record == {"verdict": "ok"}, run.stderr
        else:
            assert record["no_result"] is True and record["error"].startswith("the job on Sprite dar-fleet-4 ended with status"), run.stderr
        assert [path.name for path in directory.iterdir() if path.name.startswith(".result.json")] == []
