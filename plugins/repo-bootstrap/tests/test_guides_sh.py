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


def plugin_cache(tmp_path: Path, versions: dict[str, bool]) -> Path:
    shim = (GUIDES_SH / "binrun-shim.sh").read_text().replace("{{binary}}", "tool")
    cache = tmp_path / "cache"
    for version, orphaned in versions.items():
        root = cache / version
        (root / "scripts").mkdir(parents=True)
        (root / "bin").mkdir()
        script = root / "scripts" / "install-binary.sh"
        script.write_text(shim)
        script.chmod(0o755)
        (root / "bin" / "tool").symlink_to("../scripts/install-binary.sh")
        if orphaned:
            (root / ".orphaned_at").write_text("1790178260989")
    runner = tmp_path / "binrun"
    runner.write_text(
        '#!/bin/bash\necho "${BINRUN_PLUGIN_ROOT##*/} ${1##*/} ${*:2}"\n'
    )
    runner.chmod(0o755)
    return cache


def run_shim(tmp_path: Path, cache: Path, version: str) -> str:
    out = subprocess.run(
        [str(cache / version / "bin" / "tool"), "vcs", "pr status"],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "/usr/bin:/bin", "BINRUN_BIN": str(tmp_path / "binrun")},
    )
    return out.stdout.strip()


def test_shim_hops_from_an_orphaned_dir_to_the_newest_live_sibling(tmp_path: Path):
    cache = plugin_cache(
        tmp_path,
        {"0.61.0": True, "0.9.0": False, "0.64.1": False, "0.70.0": True},
    )
    (cache / "0.80.0").mkdir()
    (cache / "not-a-version").mkdir()
    assert run_shim(tmp_path, cache, "0.61.0") == "0.64.1 tool.binrun vcs pr status"


def test_shim_stays_put_when_no_live_sibling_exists(tmp_path: Path):
    cache = plugin_cache(tmp_path, {"0.61.0": True, "0.70.0": True})
    assert run_shim(tmp_path, cache, "0.61.0") == "0.61.0 tool.binrun vcs pr status"


def test_shim_ignores_newer_siblings_when_its_own_dir_is_live(tmp_path: Path):
    cache = plugin_cache(tmp_path, {"0.61.0": False, "0.64.1": False})
    assert run_shim(tmp_path, cache, "0.61.0") == "0.61.0 tool.binrun vcs pr status"


def test_shim_never_hops_down(tmp_path: Path):
    cache = plugin_cache(tmp_path, {"0.61.0": False, "0.64.1": True})
    assert run_shim(tmp_path, cache, "0.64.1") == "0.64.1 tool.binrun vcs pr status"


def test_shim_stays_put_in_an_unversioned_dir(tmp_path: Path):
    cache = plugin_cache(tmp_path, {"a1b2c3d": True, "0.64.1": False})
    result = subprocess.run(
        [str(cache / "a1b2c3d" / "bin" / "tool")],
        capture_output=True,
        text=True,
        check=True,
        env={"PATH": "/usr/bin:/bin", "BINRUN_BIN": str(tmp_path / "binrun")},
    )
    assert (result.stdout.strip(), result.stderr) == ("a1b2c3d tool.binrun", "")
