import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

LAUNCHER = Path(__file__).resolve().parent.parent / "hooks" / "installed.py"
PROBE = "import os, sys\nprint(os.path.basename(os.environ['CLAUDE_PLUGIN_ROOT']), sys.stdin.read())\n"


def plugin(root: Path, probe: bool) -> Path:
    (root / "hooks").mkdir(parents=True)
    shutil.copy(LAUNCHER, root / "hooks" / "installed.py")
    if probe:
        (root / "hooks" / "probe.py").write_text(PROBE)
    return root


def launch(config: Path, root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(root / "hooks" / "installed.py"), "probe.py"],
        input='{"tool_name": "Bash"}',
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "CLAUDE_CONFIG_DIR": str(config), "CLAUDE_PLUGIN_ROOT": str(root)},
    )


def cached(config: Path, newest_has_probe: bool = True) -> Path:
    cache = config / "plugins" / "cache" / "skills" / "long-running"
    loaded = plugin(cache / "1.0.74", probe=True)
    newest = plugin(cache / "1.0.77", probe=newest_has_probe)
    entries = [
        {"scope": "user", "installPath": str(newest), "version": "1.0.77", "lastUpdated": "2026-11-10T10:38:45Z"},
        {"scope": "project", "installPath": str(loaded), "version": "1.0.74", "lastUpdated": "2026-11-10T10:00:29Z"},
    ]
    (config / "plugins" / "installed_plugins.json").write_text(json.dumps({"version": 2, "plugins": {"long-running@skills": entries}}))
    return loaded


def test_a_session_that_loaded_an_older_version_runs_the_hook_installed_now(tmp_path: Path) -> None:
    config = tmp_path / "claude"
    loaded = cached(config)
    assert launch(config, loaded).stdout == '1.0.77 {"tool_name": "Bash"}\n'


def test_a_hook_the_installed_version_dropped_does_nothing(tmp_path: Path) -> None:
    config = tmp_path / "claude"
    loaded = cached(config, newest_has_probe=False)
    assert launch(config, loaded).stdout == ""


def test_a_checkout_runs_its_own_hooks(tmp_path: Path) -> None:
    config = tmp_path / "claude"
    cached(config)
    checkout = plugin(tmp_path / "checkout", probe=True)
    assert launch(config, checkout).stdout == 'checkout {"tool_name": "Bash"}\n'
