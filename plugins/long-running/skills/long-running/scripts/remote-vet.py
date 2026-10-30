#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
"""Build and vet a lane's change on a Sprite, so no compiler runs on this machine.

    remote-vet.py --config PATH --lane LANE [--sprite NAME] [--base REF] [WORKTREE]

STDLIB ONLY. `--config` is the drive's desk-runner `runner.json`: `landing.checkout` names the clone path every Sprite
holds, and the first Sprite the `fidelity` or `finder` desk names runs the vet unless `--sprite` names another. The
change is everything in WORKTREE (default the current directory) that differs from its merge base with `--base`
(default the remote's default branch), committed or not, untracked files included. On the Sprite, the lane's own
worktree `<root>/vet-<lane>` beside the clone, kept between runs so later checkouts read local objects, moves to that
merge base, takes the change as one binary patch, runs `vet.prepare` from the config when it names one (the
repository's code generation), then runs `go build` and `go vet` on each Go package the change touches, from its
module's root, and `cargo check` on each Rust crate it touches, from its workspace's root, with the lane's own
`target-vet-<lane>`. Every command runs even after one fails; each prints a `PASS` or `FAIL` line with its seconds,
and the script exits 1 when any failed. One run per lane holds the worktree at a time. The GitHub token reaches the
Sprite on standard input only.

  SPRITE_VET_ROOT   directory on the Sprite holding the worktrees and targets, default /work
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path, PurePosixPath

SPRITE_SCRIPT = r"""
set -eu
IFS= read -r GH_TOKEN
export GH_TOKEN
clone=$1 work=$2 base=$3 prepare=$4 commands=$5
auth() {
  git -C "$clone" -c credential.helper= -c 'credential.helper=!f() { echo username=x-access-token; echo "password=$GH_TOKEN"; }; f' "$@"
}
mkdir -p "$(dirname "$work")"
exec 9>"$work.lock"
flock 9
git -C "$clone" cat-file -e "$base^{commit}" 2>/dev/null </dev/null || (flock 8 && auth fetch --quiet origin "$base" </dev/null) 8>"$clone.fetch.lock"
if [ -e "$work/.git" ]; then
  git -C "$work" checkout --quiet --force --detach "$base" </dev/null
  git -C "$work" clean -qfd </dev/null
else
  git -C "$clone" worktree prune </dev/null
  git -C "$clone" worktree add --quiet --force --detach "$work" "$base" </dev/null
fi
git -C "$work" apply --binary --whitespace=nowarn
cd "$work"
if [ -n "$prepare" ]; then
  start=$(date +%s)
  if ! out=$(sh -c "$prepare" 2>&1); then
    printf '%s\n' "$out" | tail -40
    echo "FAIL prepare $(( $(date +%s) - start ))s"
    exit 1
  fi
  echo "PASS prepare $(( $(date +%s) - start ))s"
fi
failed=0
while IFS= read -r line; do
  start=$(date +%s)
  if out=$(sh -c "$line" 2>&1); then
    echo "PASS $line $(( $(date +%s) - start ))s"
  else
    printf '%s\n' "$out" | tail -60
    echo "FAIL $line $(( $(date +%s) - start ))s"
    failed=1
  fi
done <<EOF
$commands
EOF
exit $failed
"""


def run(*argv: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> str:
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, check=True).stdout


def snapshot(worktree: Path) -> str:
    """The tree of everything in `worktree`, untracked files included, written through a private index so the lane's own index is untouched."""
    with tempfile.TemporaryDirectory() as scratch:
        env = os.environ | {"GIT_INDEX_FILE": str(Path(scratch) / "index")}
        run("git", "read-tree", "HEAD", cwd=worktree, env=env)
        run("git", "add", "--all", cwd=worktree, env=env)
        return run("git", "write-tree", cwd=worktree, env=env).strip()


def at(tree: str, path: PurePosixPath) -> str:
    return tree if str(path) == "." else f"{tree}:{path}"


def exists(worktree: Path, tree: str, path: PurePosixPath) -> bool:
    return subprocess.run(["git", "cat-file", "-e", at(tree, path)], cwd=worktree, capture_output=True).returncode == 0


def enclosing(worktree: Path, tree: str, path: PurePosixPath, name: str) -> PurePosixPath | None:
    for parent in path.parents:
        if exists(worktree, tree, parent / name):
            return parent
    return None


def manifest(worktree: Path, tree: str, path: PurePosixPath) -> dict:
    return tomllib.loads(run("git", "cat-file", "blob", at(tree, path), cwd=worktree))


def workspace(worktree: Path, tree: str, crate: PurePosixPath) -> PurePosixPath:
    for parent in (crate, *crate.parents):
        if exists(worktree, tree, parent / "Cargo.toml") and "workspace" in manifest(worktree, tree, parent / "Cargo.toml"):
            return parent
    return crate


def commands(worktree: Path, tree: str, changed: list[str], lane: str, root: str) -> list[str]:
    """One `go build` and `go vet` per Go module over the packages the change leaves Go files in, and one `cargo check` per Cargo workspace over the crates it touches."""
    packages: dict[PurePosixPath, set[str]] = {}
    crates: dict[PurePosixPath, set[str]] = {}
    for name in changed:
        path = PurePosixPath(name)
        if path.suffix == ".go" or path.name in ("go.mod", "go.sum"):
            module = enclosing(worktree, tree, path, "go.mod")
            if module is None:
                continue
            directory = path.parent
            listed = run("git", "ls-tree", "--name-only", at(tree, directory), cwd=worktree).splitlines() if exists(worktree, tree, directory) else []
            if path.suffix != ".go":
                packages.setdefault(module, set()).add("./...")
            elif any(entry.endswith(".go") for entry in listed):
                packages.setdefault(module, set()).add("./" + os.path.relpath(directory, module))
        elif path.suffix == ".rs" or path.name == "Cargo.toml":
            crate = enclosing(worktree, tree, path, "Cargo.toml") if path.name != "Cargo.toml" else path.parent
            if crate is None or not exists(worktree, tree, crate / "Cargo.toml"):
                continue
            if package := manifest(worktree, tree, crate / "Cargo.toml").get("package"):
                crates.setdefault(workspace(worktree, tree, crate), set()).add(package["name"])
    lines = []
    for module, names in sorted(packages.items()):
        listed = " ".join(sorted(names))
        lines.append(f"cd {shlex.quote(str(module))} && go build {listed} && go vet {listed}")
    for space, names in sorted(crates.items()):
        listed = " ".join(f"-p {shlex.quote(name)}" for name in sorted(names))
        lines.append(f"cd {shlex.quote(str(space))} && CARGO_TARGET_DIR={shlex.quote(f'{root}/target-vet-{lane}')} cargo check --quiet {listed}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--lane", required=True)
    parser.add_argument("--sprite")
    parser.add_argument("--base")
    parser.add_argument("worktree", nargs="?", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text())
    clone = config["landing"]["checkout"]
    named = [config.get(desk, {}).get("sprite", []) for desk in ("fidelity", "finder")]
    sprite = args.sprite or next(name for names in named for name in ([names] if isinstance(names, str) else names))
    worktree = Path(run("git", "rev-parse", "--show-toplevel", cwd=args.worktree).strip())
    target = args.base or run("git", "rev-parse", "--abbrev-ref", "origin/HEAD", cwd=worktree).strip()
    base = run("git", "merge-base", "HEAD", target, cwd=worktree).strip()
    tree = snapshot(worktree)
    changed = run("git", "diff", "--name-only", "--no-renames", base, tree, cwd=worktree).splitlines()
    root = os.environ.get("SPRITE_VET_ROOT", "/work")
    lines = commands(worktree, tree, changed, args.lane, root)
    if not lines:
        print(f"remote-vet: {len(changed)} changed files touch no Go package or Rust crate")
        return 0
    patch = run("git", "diff", "--binary", "--no-renames", base, tree, cwd=worktree)
    token = run("gh", "auth", "token", "--hostname", "github.com").strip()
    work = f"{root}/vet-{args.lane}"
    done = subprocess.run(["sprite", "exec", "-s", sprite, "--", "sh", "-c", SPRITE_SCRIPT, "sh", clone, work, base, config.get("vet", {}).get("prepare", ""), "\n".join(lines)], input=f"{token}\n{patch}", capture_output=True, text=True)
    print(done.stdout, end="")
    if done.returncode:
        print(f"remote-vet: {args.lane} at {base[:10]} on {sprite} failed: {done.stderr.strip()[-400:] or 'see the FAIL lines above'}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
