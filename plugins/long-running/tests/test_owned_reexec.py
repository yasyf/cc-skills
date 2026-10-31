from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import board_watch


PROBE = r'''
import json, os, signal, subprocess, sys, threading, time
from pathlib import Path
from types import SimpleNamespace
from long_running import drive

root = Path(os.environ['WATCH_PROBE_ROOT'])
mode = sys.argv[1]

def identity(pid):
    return subprocess.run(['ps', '-ww', '-p', str(pid), '-o', 'pid=,ppid=,pgid=,lstart=,command='], capture_output=True, text=True).stdout.strip()

if os.environ.get('WATCH_PROBE_PHASE') == 'new':
    drive.run_installed(__file__)
    before = json.loads((root / 'before.json').read_text())
    pid = before['protected_pid']
    try:
        after = identity(pid)
        assert after == before['protected_identity'], 'inherited protected child was terminated or changed'
        boundary = json.loads((root / 'exec-boundary.json').read_text())
        assert not any(boundary['owned_alive']), 'owned watch was not closed before exec'
        assert all(boundary['owned_reaped']), 'owned watch was not reaped before exec'
        assert boundary['pid'] == os.getpid(), 'reexec changed the owning PID'
        for owned in boundary['owned_pids']:
            try:
                os.waitpid(owned, os.WNOHANG)
            except ChildProcessError:
                pass
            else:
                raise AssertionError('owned watch was not reaped before exec')
        print(json.dumps({'mode': mode, 'same_pid': True, 'owned_streams': len(boundary['owned_pids']), 'protected_survived': True}), flush=True)
    finally:
        if identity(pid) == before['protected_identity']:
            os.kill(pid, signal.SIGTERM)
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass
    raise SystemExit(0)

protected = subprocess.Popen([sys.executable, str(root / 'protected.py')], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
deadline = time.monotonic() + 5
while not (root / 'protected.pid').exists():
    assert time.monotonic() < deadline, 'protected fixture never started'
    time.sleep(0.01)
(root / 'before.json').write_text(json.dumps({'protected_pid': protected.pid, 'protected_identity': identity(protected.pid)}))
os.environ['WATCH_PROBE_PHASE'] = 'new'
actual_exec = drive.reexec

def recorded_exec(argv):
    pids = [int(p.read_text()) for p in (root / 'cc-present.pid', root / 'cci.pid') if p.exists()]
    alive = [str(root) in identity(pid) for pid in pids]
    reaped = []
    for pid in pids:
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            reaped.append(True)
        else:
            reaped.append(False)
    (root / 'exec-boundary.json').write_text(json.dumps({'pid': os.getpid(), 'owned_pids': pids, 'owned_alive': alive, 'owned_reaped': reaped}))
    actual_exec(argv)

drive.reexec = recorded_exec
fresh = root / 'fresh' / 'skills' / 'long-running' / 'scripts' / 'board_watch.py'
if mode == 'direct':
    drive.reexec([sys.executable, str(fresh), mode])
else:
    import board_watch
    drive.plugin_manifest = lambda: {'version': '0.0.0'}
    drive.newest_install = lambda: {'installPath': str(root / 'fresh'), 'version': '9999.0.0'}
    board_watch.SNAPSHOT_SECONDS = 0.01
    class Shell(board_watch.Shell):
        def stale(self):
            return (root / 'cc-present.pid').exists() and (root / 'cci.pid').exists()
        def root_session(self, name):
            return None
        def outcomes(self, session):
            return None
    if mode == 'unchanged':
        shell = Shell()
        threads = [threading.Thread(target=lambda: list(shell.watch('probe'))), threading.Thread(target=lambda: list(shell.writes('probe', 'cursor')))]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + 5
        while not shell.stale():
            assert time.monotonic() < deadline, 'owned streams never started'
            time.sleep(0.01)
        drive.newest_install = lambda: {'installPath': str(root / 'fresh'), 'version': '0.0.0'}
        shell.reexec()
        assert not shell._closing and len(shell._watches) == 2
        assert all(child.poll() is None for child in shell._watches)
        assert identity(protected.pid) == json.loads((root / 'before.json').read_text())['protected_identity']
        with shell._watch_lock:
            shell._end_watches_locked()
        for thread in threads:
            thread.join(timeout=5)
            assert not thread.is_alive()
        protected.terminate()
        protected.wait()
        print(json.dumps({'mode': mode, 'streams_preserved_without_update': True, 'protected_survived': True}), flush=True)
        raise SystemExit(0)
    args = SimpleNamespace(state_dir=str(root / 'state'), session='owned-probe', program='probe')
    raise SystemExit(board_watch.cmd_start(args, Shell()))
'''


def reexec_probe(tmp_path: Path, mode: str) -> dict:
    root = tmp_path / mode
    tools = root / 'bin'
    tools.mkdir(parents=True)
    protected = root / 'protected.py'
    protected.write_text("import os, time\nfrom pathlib import Path\nPath(__file__).with_suffix('.pid').write_text(str(os.getpid()))\ntime.sleep(600)\n")
    utility = f"#!{sys.executable}\nimport os, time\nfrom pathlib import Path\nroot = Path(os.environ['WATCH_PROBE_ROOT'])\n(root / (Path(__file__).name + '.pid')).write_text(str(os.getpid()))\ntime.sleep(600)\n"
    for name in ('cc-present', 'cci'):
        path = tools / name
        path.write_text(utility)
        path.chmod(0o700)
    old = root / 'probe.py'
    old.write_text(PROBE)
    fresh = root / 'fresh'
    (fresh / '.claude-plugin').mkdir(parents=True)
    (fresh / '.claude-plugin' / 'plugin.json').write_text(json.dumps({'version': '9999.0.0'}))
    target = fresh / 'skills' / 'long-running' / 'scripts' / 'board_watch.py'
    target.parent.mkdir(parents=True)
    target.write_text(PROBE)
    env = os.environ | {'WATCH_PROBE_ROOT': str(root), 'PATH': f"{tools}{os.pathsep}{os.environ['PATH']}", 'PYTHONPATH': str(Path(board_watch.__file__).parent), 'HOME': str(root / 'home'), 'CLAUDE_CONFIG_DIR': str(root / '.claude'), board_watch.drive.PIN_ENV: board_watch.drive.plugin_manifest()['version']}
    env.pop('WATCH_PROBE_PHASE', None)
    env.pop(board_watch.drive.DEADLINE_ENV, None)
    process = subprocess.Popen([sys.executable, str(old), mode], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        stdout, stderr = process.communicate(timeout=20)
        assert process.returncode == 0, stderr
        return json.loads(stdout)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        for path in (root / 'protected.pid', root / 'cc-present.pid', root / 'cci.pid'):
            if path.exists():
                pid = int(path.read_text())
                current = subprocess.run(['ps', '-ww', '-p', str(pid), '-o', 'command='], capture_output=True, text=True).stdout
                if str(root) in current:
                    os.kill(pid, signal.SIGKILL)


def board_reexec(tmp_path: Path) -> None:
    assert reexec_probe(tmp_path, 'board') == {'mode': 'board', 'same_pid': True, 'owned_streams': 2, 'protected_survived': True}


def test_reexec_does_not_signal_unregistered_inherited_children(tmp_path: Path) -> None:
    assert reexec_probe(tmp_path, 'direct') == {'mode': 'direct', 'same_pid': True, 'owned_streams': 0, 'protected_survived': True}


def test_an_unchanged_install_leaves_owned_streams_and_other_children_running(tmp_path: Path) -> None:
    assert reexec_probe(tmp_path, 'unchanged') == {'mode': 'unchanged', 'streams_preserved_without_update': True, 'protected_survived': True}
