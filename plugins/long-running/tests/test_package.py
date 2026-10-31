from __future__ import annotations

import importlib
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "long-running" / "scripts"
MODULES = ("board_rows", "cci", "drive", "ghread", "grades", "lanes", "ledger", "overview", "rulings", "testfiles")


@pytest.mark.parametrize("name", MODULES)
def test_engine_has_one_canonical_package_identity(name):
    module = importlib.import_module(f"long_running.{name}")
    assert Path(module.__file__).resolve() == SCRIPTS / "long_running" / f"{name}.py"
    assert name not in sys.modules


@pytest.mark.parametrize("name", ("board_rows", "drive", "ledger", "rulings"))
def test_stable_cli_entrypoint_from_an_unrelated_directory(name, tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / f"{name}.py"), "--help"],
        cwd=tmp_path,
        env={**os.environ, "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert f"usage: {name}.py" in result.stdout
    assert not result.stderr


def test_relocated_paths_still_name_the_original_plugin_and_entrypoints():
    from long_running import drive, ledger

    assert drive.PLUGIN == SCRIPTS.parents[2]
    assert drive.PACK == SCRIPTS.parents[2] / "dashboard"
    assert ledger.PROMPT_WATCH == SCRIPTS / "prompt_watch.py"


def test_actual_hook_registration_resolves_relocated_script_dependencies(tmp_path):
    hooks = SCRIPTS.parents[2] / "capt-hook" / "hooks"
    code = r"""
import ast
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from captain_hook.app import _state
from captain_hook.loader import discover_pack

root = Path(sys.argv[1]).resolve()
discover_pack("long-running-packaging", root)
assert not _state.load_errors, _state.load_errors
registered = {Path(str(hook.source_file)).name for hook in _state.hooks}
assert {"lane_landing.py", "owner_facing.py", "refusal_kinds_need_owner_answer.py", "ruling_misread.py"} <= registered
modules = {Path(module.__file__).stem: module for module in list(sys.modules.values()) if getattr(module, "__file__", None) and Path(module.__file__).resolve().parent == root}
assert modules["owner_facing"].PLAIN_FOR
assert modules["refusal_kinds_need_owner_answer"].EXEMPT.search("notes.md")
with patch.object(modules["lane_landing"].subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="")):
    assert modules["lane_landing"].trunk_clause({"cci_drive": "packaging-test"}) == ""
replies = [SimpleNamespace(stdout="abc123\n"), SimpleNamespace(stdout="tests/test_refusal.py\n"), SimpleNamespace(stdout="+ assert refusal\n")]
call = SimpleNamespace(name="ccx", args=["vcs", "ship"])
evt = SimpleNamespace(cwd=root, command=SimpleNamespace(calls=lambda: [call]))
with patch.object(modules["ruling_misread"].subprocess, "run", side_effect=replies):
    assert modules["ruling_misread"].shipped_tests(evt) == [("test", "+ assert refusal\n")]
scripts = root.parents[1] / "skills" / "long-running" / "scripts"
for path in root.glob("*.py"):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "script_module" and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            assert (scripts / (node.args[0].value.replace(".", "/") + ".py")).is_file(), (path, node.lineno, node.args[0].value)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(hooks)],
        cwd=SCRIPTS,
        env={**os.environ, "HOME": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
