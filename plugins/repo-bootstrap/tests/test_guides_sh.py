"""Guards for the shared cc-guides shell guides."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
GUIDES_SH = REPO_ROOT / "plugin" / "guides" / "sh"
RESOLVERS = ("binrun-shim.sh", "install-binary-pinned.sh", "render-descriptor.sh")
RESOLVER_RE = re.compile(
    r'^(case "\$0" in .*?esac)\n(\w+)="\$\(cd "\$d/\.\." && pwd\)"$',
    re.MULTILINE,
)


def resolver(guide: str) -> tuple[str, str]:
    match = RESOLVER_RE.search((GUIDES_SH / guide).read_text())
    assert match, f"{guide} has no recognizable plugin-root resolver"
    return match.group(1), match.group(2)


@pytest.mark.parametrize("guide", RESOLVERS)
def test_resolver_execs_nothing(guide: str):
    case, _ = resolver(guide)
    assert "dirname" not in case
    assert "$(" not in case


@pytest.mark.parametrize("guide", RESOLVERS)
def test_resolver_matches_dirname_on_every_invocation_shape(guide: str, tmp_path: Path):
    case, var = resolver(guide)
    plug = tmp_path / "plug"
    (plug / "bin").mkdir(parents=True)
    (tmp_path / "elsewhere").mkdir()
    bodies = {
        "old": f'{var}="$(cd "$(dirname "$0")/.." && pwd)"',
        "new": f'{case}\n{var}="$(cd "$d/.." && pwd)"',
    }
    for name, body in bodies.items():
        tool = plug / "bin" / name
        tool.write_text(f'#!/bin/bash\nset -eu\n{body}\necho "${var}"\n')
        tool.chmod(0o755)
        (plug / "bin" / f"{name}-link").symlink_to(tool)

    def run(argv: list[str], cwd: Path, path_prefix: bool = False) -> str:
        env = {"PATH": f"{plug / 'bin'}:/usr/bin:/bin"} if path_prefix else None
        out = subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True, check=True, env=env
        )
        return out.stdout.strip()

    here = plug / "bin"
    away = tmp_path / "elsewhere"
    shapes = {
        "absolute": lambda n: run([str(here / n)], away),
        "relative": lambda n: run([f"../plug/bin/{n}"], away),
        "symlink": lambda n: run([str(here / f"{n}-link")], away),
        "path_lookup": lambda n: run([n], away, path_prefix=True),
        "slashless_argv0": lambda n: run(
            ["/bin/bash", "--norc", "-c", f"cd {here}; {bodies[n]}; echo ${var}", n],
            away,
        ),
    }
    for shape, invoke in shapes.items():
        assert invoke("old") == invoke("new") == str(plug), shape
