from __future__ import annotations

import os
import subprocess
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]
SHIM = PLUGIN / "scripts" / "bin-shim.sh"
RUNNER = PLUGIN / "scripts" / "binrun.sh"


def plugin_cache(tmp_path: Path, *versions: str) -> Path:
    cache = tmp_path / "cache"
    for version in versions:
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
    return cache


def name(cache: Path, version: str) -> None:
    link = cache.parent / "config" / "long-running" / "current"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.unlink(missing_ok=True)
    link.symlink_to(cache / version)


def run(cache: Path, version: str, tool: str = "tool.py") -> str:
    out = subprocess.run(
        [str(cache / version / "bin" / tool), "a", "b c"],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": os.environ["PATH"], "CLAUDE_CONFIG_DIR": str(cache.parent / "config")},
    )
    return out.stdout.strip()


def test_runs_its_own_script_with_python_or_directly(tmp_path: Path):
    cache = plugin_cache(tmp_path, "0.7.44")
    assert run(cache, "0.7.44") == "0.7.44 py a b c"
    assert run(cache, "0.7.44", "tool.sh") == "0.7.44 sh a b c"


def test_a_script_imports_its_sibling_package_when_python_drops_the_script_directory(tmp_path: Path):
    cache = plugin_cache(tmp_path, "0.7.44")
    scripts = cache / "0.7.44" / "skills" / "long-running" / "scripts"
    (scripts / "sibling.py").write_text('NAME = "sibling"\n')
    (scripts / "tool.py").write_text("import sibling\nprint(sibling.NAME)\n")
    out = subprocess.run(
        [str(cache / "0.7.44" / "bin" / "tool.py")],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": os.environ["PATH"], "CLAUDE_CONFIG_DIR": str(cache.parent / "config"), "PYTHONSAFEPATH": "1"},
    )
    assert out.stdout.strip() == "sibling"


def test_hops_to_the_version_the_install_link_names(tmp_path: Path):
    cache = plugin_cache(tmp_path, "0.7.44", "0.7.9", "0.7.57", "0.8.0")
    name(cache, "0.7.57")
    assert run(cache, "0.7.44") == "0.7.57 py a b c"
    assert run(cache, "0.7.44", "tool.sh") == "0.7.57 sh a b c"


def test_runs_its_own_scripts_while_the_install_link_names_none(tmp_path: Path):
    cache = plugin_cache(tmp_path, "0.7.44", "0.7.57")
    assert run(cache, "0.7.44") == "0.7.44 py a b c"


def test_runs_its_own_scripts_while_the_install_link_names_a_directory_that_is_gone(tmp_path: Path):
    cache = plugin_cache(tmp_path, "0.7.44")
    name(cache, "0.7.44")
    (cache / "0.7.57").mkdir()
    name(cache, "0.7.57")
    (cache / "0.7.57").rmdir()
    assert run(cache, "0.7.44") == "0.7.44 py a b c"


def test_skips_a_named_version_that_lacks_the_tool(tmp_path: Path):
    cache = plugin_cache(tmp_path, "0.7.44", "0.7.57")
    (cache / "0.7.57" / "bin" / "tool.py").unlink()
    name(cache, "0.7.57")
    assert run(cache, "0.7.44") == "0.7.44 py a b c"


def test_never_hops_down(tmp_path: Path):
    cache = plugin_cache(tmp_path, "0.7.44", "0.7.57")
    name(cache, "0.7.44")
    assert run(cache, "0.7.57") == "0.7.57 py a b c"


def test_stays_put_in_an_unversioned_dir(tmp_path: Path):
    cache = plugin_cache(tmp_path, "long-running", "0.7.57")
    name(cache, "0.7.57")
    assert run(cache, "long-running") == "long-running py a b c"


def test_prefers_a_versioned_python_over_an_older_bare_python3(tmp_path: Path):
    cache = plugin_cache(tmp_path, "0.7.44")
    pythons = tmp_path / "pythons"
    pythons.mkdir()
    for name in ("python3", "python3.13"):
        fake = pythons / name
        fake.write_text(f'#!/bin/sh\necho "{name}"\n')
        fake.chmod(0o755)
    out = subprocess.run([str(cache / "0.7.44" / "bin" / "tool.py")], capture_output=True, text=True, check=True, env={"PATH": str(pythons)})
    assert out.stdout.strip() == "python3.13"


def test_stays_on_the_version_long_running_pin_names_and_hops_from_any_other(tmp_path: Path):
    cache = plugin_cache(tmp_path, "0.7.44", "0.7.57")
    name(cache, "0.7.57")
    pinned = {"PATH": os.environ["PATH"], "CLAUDE_CONFIG_DIR": str(cache.parent / "config"), "LONG_RUNNING_PIN": "0.7.44"}
    assert subprocess.run([str(cache / "0.7.44" / "bin" / "tool.py"), "a"], capture_output=True, text=True, check=True, env=pinned).stdout.strip() == "0.7.44 py a"
    assert subprocess.run([str(cache / "0.7.44" / "bin" / "tool.py"), "a"], capture_output=True, text=True, check=True, env=pinned | {"LONG_RUNNING_PIN": "0.7.9"}).stdout.strip() == "0.7.57 py a"


def lr_cache(tmp_path: Path, *versions: str) -> Path:
    cache = plugin_cache(tmp_path, *versions)
    for version in versions:
        root = cache / version
        runner = root / "scripts" / "binrun.sh"
        runner.write_text(RUNNER.read_text())
        runner.chmod(0o755)
        (root / "bin" / "lr").symlink_to("../scripts/bin-shim.sh")
    fake = tmp_path / "binrun"
    fake.write_text('#!/bin/sh\necho "$BINRUN_PLUGIN_ROOT $*"\n')
    fake.chmod(0o755)
    return cache


def run_lr(tmp_path: Path, cache: Path, version: str, **env: str) -> str:
    return subprocess.run(
        [str(cache / version / "bin" / "lr"), "landing", "pass"],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": os.environ["PATH"], "CLAUDE_CONFIG_DIR": str(cache.parent / "config"), "BINRUN_BIN": str(tmp_path / "binrun")} | env,
    ).stdout.strip()


def test_lr_runs_the_descriptor_of_its_own_version_and_names_its_plugin_root(tmp_path: Path):
    cache = lr_cache(tmp_path, "0.7.44")
    root = (cache / "0.7.44").resolve()
    assert run_lr(tmp_path, cache, "0.7.44") == f"{root} {root}/bin/lr.binrun landing pass"


def test_lr_hops_to_the_install_link_unless_pinned(tmp_path: Path):
    cache = lr_cache(tmp_path, "0.7.44", "0.7.57")
    name(cache, "0.7.57")
    newer, older = (cache / "0.7.57").resolve(), (cache / "0.7.44").resolve()
    assert run_lr(tmp_path, cache, "0.7.44") == f"{newer} {newer}/bin/lr.binrun landing pass"
    assert run_lr(tmp_path, cache, "0.7.44", LONG_RUNNING_PIN="0.7.44") == f"{older} {older}/bin/lr.binrun landing pass"
