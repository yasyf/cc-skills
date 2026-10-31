from __future__ import annotations

import os
import subprocess
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
SHIM = PLUGIN / "scripts" / "bin-shim.sh"


def plugin_cache(tmp_path: Path, versions: dict[str, bool]) -> Path:
    cache = tmp_path / "cache"
    for version, orphaned in versions.items():
        root = cache / version
        scripts = root / "skills" / "long-running" / "scripts"
        scripts.mkdir(parents=True)
        (root / "scripts").mkdir()
        (root / "bin").mkdir()
        shim = root / "scripts" / "bin-shim.sh"
        shim.write_text(SHIM.read_text())
        shim.chmod(0o755)
        for tool in ("tool.py", "tool.sh"):
            (root / "bin" / tool).symlink_to("../scripts/bin-shim.sh")
        (scripts / "tool.py").write_text(f'import sys\nprint("{version} py", *sys.argv[1:])\n')
        sh = scripts / "tool.sh"
        sh.write_text(f'#!/bin/sh\necho "{version} sh $*"\n')
        sh.chmod(0o755)
        if orphaned:
            (root / ".orphaned_at").write_text("1790178260989")
    return cache


def run(cache: Path, version: str, tool: str = "tool.py") -> str:
    out = subprocess.run(
        [str(cache / version / "bin" / tool), "a", "b c"],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": os.environ["PATH"]},
    )
    return out.stdout.strip()


def test_runs_its_own_script_with_python_or_directly(tmp_path: Path):
    cache = plugin_cache(tmp_path, {"0.7.44": False})
    assert run(cache, "0.7.44") == "0.7.44 py a b c"
    assert run(cache, "0.7.44", "tool.sh") == "0.7.44 sh a b c"


def test_hops_from_a_live_dir_to_the_newest_live_sibling(tmp_path: Path):
    cache = plugin_cache(
        tmp_path,
        {"0.7.44": False, "0.7.9": False, "0.7.57": False, "0.8.0": True},
    )
    (cache / "0.9.0").mkdir()
    (cache / "not-a-version").mkdir()
    assert run(cache, "0.7.44") == "0.7.57 py a b c"
    assert run(cache, "0.7.44", "tool.sh") == "0.7.57 sh a b c"


def test_hops_from_an_orphaned_dir(tmp_path: Path):
    cache = plugin_cache(tmp_path, {"0.7.44": True, "0.7.57": False})
    assert run(cache, "0.7.44") == "0.7.57 py a b c"


def test_skips_a_newer_sibling_that_lacks_the_tool(tmp_path: Path):
    cache = plugin_cache(tmp_path, {"0.7.44": False, "0.7.57": False})
    (cache / "0.7.57" / "bin" / "tool.py").unlink()
    assert run(cache, "0.7.44") == "0.7.44 py a b c"


def test_never_hops_down(tmp_path: Path):
    cache = plugin_cache(tmp_path, {"0.7.44": False, "0.7.57": True})
    assert run(cache, "0.7.57") == "0.7.57 py a b c"


def test_stays_put_in_an_unversioned_dir(tmp_path: Path):
    cache = plugin_cache(tmp_path, {"long-running": False, "0.7.57": False})
    assert run(cache, "long-running") == "long-running py a b c"


def test_prefers_a_versioned_python_over_an_older_bare_python3(tmp_path: Path):
    cache = plugin_cache(tmp_path, {"0.7.44": False})
    pythons = tmp_path / "pythons"
    pythons.mkdir()
    for name in ("python3", "python3.13"):
        fake = pythons / name
        fake.write_text(f'#!/bin/sh\necho "{name}"\n')
        fake.chmod(0o755)
    out = subprocess.run([str(cache / "0.7.44" / "bin" / "tool.py")], capture_output=True, text=True, check=True, env={"PATH": str(pythons)})
    assert out.stdout.strip() == "python3.13"


def test_skips_a_newer_sibling_whose_install_has_its_bin_but_not_yet_its_scripts(tmp_path: Path):
    cache = plugin_cache(tmp_path, {"0.7.518": False, "0.7.519": False})
    for tool in ("tool.py", "tool.sh"):
        (cache / "0.7.519" / "skills" / "long-running" / "scripts" / tool).unlink()
    assert run(cache, "0.7.518") == "0.7.518 py a b c"
    assert run(cache, "0.7.518", "tool.sh") == "0.7.518 sh a b c"


def test_runs_the_newest_whole_orphaned_copy_while_the_newest_install_is_half_written(tmp_path: Path):
    """go-sweep-7 #234218: 0.7.701 was mid-install and 0.7.699 and 0.7.700 were orphaned, so a lane's 0.7.512 shim ran its own 0.7.512 scripts, which misread the newer mailbox cursor."""
    cache = plugin_cache(tmp_path, {"0.7.512": True, "0.7.699": True, "0.7.700": True, "0.7.701": False})
    for tool in ("tool.py", "tool.sh"):
        (cache / "0.7.701" / "skills" / "long-running" / "scripts" / tool).unlink()
    assert run(cache, "0.7.512") == "0.7.700 py a b c"
    assert run(cache, "0.7.512", "tool.sh") == "0.7.700 sh a b c"



def test_stays_on_the_version_long_running_pin_names_and_hops_from_any_other(tmp_path: Path):
    cache = plugin_cache(tmp_path, {"0.7.44": False, "0.7.57": False})
    pinned = {"PATH": os.environ["PATH"], "LONG_RUNNING_PIN": "0.7.44"}
    assert subprocess.run([str(cache / "0.7.44" / "bin" / "tool.py"), "a"], capture_output=True, text=True, check=True, env=pinned).stdout.strip() == "0.7.44 py a"
    assert subprocess.run([str(cache / "0.7.44" / "bin" / "tool.py"), "a"], capture_output=True, text=True, check=True, env=pinned | {"LONG_RUNNING_PIN": "0.7.9"}).stdout.strip() == "0.7.57 py a"
