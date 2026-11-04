from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from hooks.design_picks import touched
from hooks.vet_before_push import branch_diff


def test_a_mnemonic_prefix_config_still_yields_the_changed_paths(tmp_path: Path):
    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)

    git("init", "-q")
    git("config", "diff.mnemonicPrefix", "true")
    changed = tmp_path / "a.py"
    changed.write_text("old\n")
    git("add", "a.py")
    git("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "base")
    git("update-ref", "refs/remotes/origin/HEAD", "HEAD")
    changed.write_text("new\n")

    top, diff = branch_diff(SimpleNamespace(cwd=str(tmp_path)), SimpleNamespace(cwd=tmp_path), "-U0")

    assert top == tmp_path.resolve()
    assert touched(diff) == {"a.py": ["old", "new"]}
