#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
"""Build and vet a lane's change on a Sprite, so no compiler runs on this machine.

    remote-vet.py --config PATH --lane LANE [--sprite NAME] [--base REF] [--timeout SECONDS] [WORKTREE]

STDLIB ONLY. `--config` is the drive's desk-runner `runner.json`: `landing.checkout` names the clone path every Sprite
holds, and the vet runs on `--sprite` or else the least loaded Sprite `vet.sprite` lists, Sprites of their own so a build
never starves the finder and fidelity passes on a grader Sprite. When every one is over load 16 the script exits 3
without vetting, so the lane pushes and CI grades it. The change is everything in WORKTREE (default the current
directory) that differs from its merge base with `--base`, default the branch below it in its ccx stack, else the
remote's default branch, so a stacked lane vets only its own commits; committed or not, untracked files included. On the Sprite, the lane's own
worktree `<root>/vet-<lane>` beside the clone, kept between runs so later checkouts read local objects, moves to that
merge base, takes the change as one binary patch, runs `vet.prepare` from the config when it names one (the
repository's code generation), then runs `go build` and `go vet` on each Go package the change touches, from its
module's root, and `cargo check` on each Rust crate it touches, from its workspace's root, with the lane's own
`target-vet-<lane>`, with no `RUSTC_WRAPPER` and with `--tests`, so a test-only change compiles. Every command runs
even after one fails; each prints a `PASS` or `FAIL` line with its seconds, and the script exits 1 when any failed.
Each stage prints a `START` line as it begins, streamed as it happens, and a run with no result after `--timeout`
seconds (default 1800) stops and names the stage it was in. One run per lane holds the worktree at a time. The GitHub
token reaches the Sprite on standard input only, and every git command there, the partial clone's lazy object fetches
included, authenticates with it.

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
import threading
import tomllib
from pathlib import Path, PurePosixPath

SPRITE_SCRIPT = r"""
set -eu
IFS= read -r GH_TOKEN
export GH_TOKEN GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_0=credential.helper GIT_CONFIG_VALUE_0= GIT_CONFIG_KEY_1=credential.helper
export GIT_CONFIG_VALUE_1='!f() { echo username=x-access-token; echo "password=$GH_TOKEN"; }; f'
clone=$1 work=$2 base=$3 prepare=$4 commands=$5
mkdir -p "$(dirname "$work")"
echo "START lock"
exec 9>"$work.lock"
flock 9
echo "START fetch"
git -C "$clone" cat-file -e "$base^{commit}" 2>/dev/null </dev/null || (flock 8 && git -C "$clone" fetch --quiet origin "$base" </dev/null) 8>"$clone.fetch.lock"
echo "START checkout"
if [ -e "$work/.git" ]; then
  git -C "$work" checkout --quiet --force --detach "$base" </dev/null
  git -C "$work" clean -qfd </dev/null
else
  git -C "$clone" worktree prune </dev/null
  git -C "$clone" worktree add --quiet --force --detach "$work" "$base" </dev/null
fi
echo "START apply"
git -C "$work" apply --binary --whitespace=nowarn
cd "$work"
if [ -n "$prepare" ]; then
  echo "START prepare"
  start=$(date +%s)
  if ! out=$(sh -c "$prepare" 2>&1 </dev/null); then
    printf '%s\n' "$out" | tail -40
    echo "FAIL prepare $(( $(date +%s) - start ))s"
    exit 1
  fi
  echo "PASS prepare $(( $(date +%s) - start ))s"
fi
failed=0
while IFS= read -r line; do
  echo "START $line"
  start=$(date +%s)
  if out=$(sh -c "$line" 2>&1 </dev/null); then
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
        lines.append(f"cd {shlex.quote(str(space))} && RUSTC_WRAPPER= CARGO_TARGET_DIR={shlex.quote(f'{root}/target-vet-{lane}')} cargo check --quiet --tests {listed}")
    return lines


MAX_LOAD = 16.0


def parent(worktree: Path) -> str | None:
    branches = [entry["branch"] for entry in json.loads(run("ccx", "vcs", "stack", "list", "--json", cwd=worktree))["branches"]]
    at = branches.index(run("git", "branch", "--show-current", cwd=worktree).strip())
    return branches[at - 1] if at else None


def idlest(sprites: list[str]) -> tuple[str, float]:
    loads = {name: float(run("sprite", "exec", "-s", name, "--no-stdin", "--", "cut", "-d", " ", "-f1", "/proc/loadavg")) for name in sprites}
    name = min(loads, key=loads.__getitem__)
    return name, loads[name]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--lane", required=True)
    parser.add_argument("--sprite")
    parser.add_argument("--base")
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("worktree", nargs="?", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text())
    clone = config["landing"]["checkout"]
    worktree = Path(run("git", "rev-parse", "--show-toplevel", cwd=args.worktree).strip())
    sprite, load = (args.sprite, 0.0) if args.sprite else idlest(config["vet"]["sprite"])
    if load > MAX_LOAD:
        print(f"remote-vet: every vet Sprite is over load {MAX_LOAD:g} ({sprite} at {load:g}); push and let CI grade {args.lane}")
        return 3
    target = args.base or parent(worktree) or run("git", "rev-parse", "--abbrev-ref", "origin/HEAD", cwd=worktree).strip()
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
    sprited = subprocess.Popen(["sprite", "exec", "-s", sprite, "--", "sh", "-c", SPRITE_SCRIPT, "sh", clone, work, base, config.get("vet", {}).get("prepare", ""), "\n".join(lines)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    stage = ["connect"]
    errors: list[str] = []
    readers = [threading.Thread(target=relay, args=(sprited.stdout, stage), daemon=True), threading.Thread(target=errors.extend, args=(sprited.stderr,), daemon=True)]
    for reader in readers:
        reader.start()
    sprited.stdin.write(f"{token}\n{patch}")
    sprited.stdin.close()
    try:
        sprited.wait(args.timeout)
    except subprocess.TimeoutExpired:
        sprited.kill()
        print(f"remote-vet: {args.lane} at {base[:10]} on {sprite} gave no result within {args.timeout:.0f}s, still in stage {stage[0]}", file=sys.stderr)
        return 1
    for reader in readers:
        reader.join()
    if sprited.returncode:
        print(f"remote-vet: {args.lane} at {base[:10]} on {sprite} failed in stage {stage[0]}: {''.join(errors).strip()[-400:] or 'see the FAIL lines above'}", file=sys.stderr)
        return 1
    return 0


def relay(stream, stage: list[str]) -> None:
    for line in stream:
        print(line, end="", flush=True)
        if line.startswith("START "):
            stage[0] = line.removeprefix("START ").strip()

if __name__ == "__main__":
    sys.exit(main())
