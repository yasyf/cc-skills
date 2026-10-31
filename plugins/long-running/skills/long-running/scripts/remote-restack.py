#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
"""Restack a PR and every open PR below it on a Sprite, so no rebase runs on this machine.

    remote-restack.py --config PATH --lane LANE [--sprite NAME] PR

STDLIB ONLY. `--config` is the drive's desk-runner `runner.json`: its `landing.repo` and `landing.checkout` name the
repository and the clone path every Sprite holds, and the first Sprite the `fidelity` or `finder` desk names runs the
restack unless `--sprite` names another. The PR's chain runs down through each open PR whose head branch is the base of
the one above, to the first base no open PR heads. On the Sprite, the clone fetches that base and every head, replays
the chain's commits onto the base with `git replay --contained` under branch names of its own, with no checkout, so
each PR below moves with the top one, and pushes every moved head in one atomic push leased on the head it read, committed as this machine's git user. A chain
whose bottom already sits on the base's tip moves nothing. Each
moved PR gets a `head` record from `--lane` naming its new head, so its finder and fidelity passes queue. A conflict
pushes nothing and exits 1, printing the branch and commit that stopped the replay and the paths and messages of the conflict. The GitHub token reaches the
Sprite on standard input only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

MOVED = "MOVED"

SPRITE_SCRIPT = r"""
set -eu
IFS= read -r GH_TOKEN
IFS= read -r GIT_COMMITTER_NAME
IFS= read -r GIT_COMMITTER_EMAIL
export GH_TOKEN GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL
clone=$1 base=$2
shift 2
auth() {
  git -C "$clone" -c credential.helper= -c 'credential.helper=!f() { echo username=x-access-token; echo "password=$GH_TOKEN"; }; f' "$@"
}
refspecs="+refs/heads/$base:refs/remotes/origin/$base"
for branch in "$@"; do refspecs="$refspecs +refs/heads/$branch:refs/remotes/origin/$branch"; done
(flock 9 && auth fetch --quiet origin $refspecs) 9>"$clone.fetch.lock"
tag=restack-$$-$(date +%s)
trap 'rm -f "$clone.$tag.log"; git -C "$clone" for-each-ref --format="delete %(refname)" "refs/heads/$tag/" | git -C "$clone" update-ref --stdin' EXIT
for branch in "$@"; do git -C "$clone" update-ref "refs/heads/$tag/$branch" "refs/remotes/origin/$branch"; done
eval "top=\${$#}"
if git -C "$clone" merge-base --is-ancestor "refs/remotes/origin/$base" "refs/heads/$tag/$1"; then
  echo "BASE $base $(git -C "$clone" rev-parse "refs/remotes/origin/$base")"
  exit 0
fi
if ! moves=$(auth replay --contained --onto "refs/remotes/origin/$base" "refs/remotes/origin/$base..refs/heads/$tag/$top" 2>"$clone.$tag.log"); then
  tip=$(git -C "$clone" rev-parse "refs/remotes/origin/$base")
  for commit in $(git -C "$clone" rev-list --reverse --topo-order "refs/remotes/origin/$base..refs/heads/$tag/$top"); do
    if tree=$(git -C "$clone" merge-tree --write-tree --merge-base "$commit^" "$tip" "$commit" 2>/dev/null); then
      tip=$(git -C "$clone" -c user.name=remote-restack -c user.email=remote-restack@localhost commit-tree "$tree" -p "$tip" -m "$commit")
      continue
    fi
    for branch in "$@"; do git -C "$clone" merge-base --is-ancestor "$commit" "refs/heads/$tag/$branch" && break; done
    echo "CONFLICT replaying $branch onto $base at $(git -C "$clone" log -1 --format='%h %s' "$commit")"
    git -C "$clone" merge-tree --write-tree --name-only --merge-base "$commit^" "$tip" "$commit" | sed '1d; s/^/  /' | tail -20
    exit 1
  done
  echo "REPLAY-FAILED $top onto $base with no conflicting commit"
  sed 's/^/  /' "$clone.$tag.log" | tail -20
  exit 1
fi
printf '%s\n' "$moves" | grep "^update refs/heads/$tag/" | git -C "$clone" update-ref --stdin
pushes=""
for branch in "$@"; do
  old=$(git -C "$clone" rev-parse "refs/remotes/origin/$branch")
  new=$(git -C "$clone" rev-parse "refs/heads/$tag/$branch")
  [ "$old" = "$new" ] && continue
  pushes="$pushes --force-with-lease=refs/heads/$branch:$old refs/heads/$tag/$branch:refs/heads/$branch"
  echo "MOVED $branch $old $new"
done
[ -z "$pushes" ] || auth push --quiet --atomic origin $pushes
echo "BASE $base $(git -C "$clone" rev-parse "refs/remotes/origin/$base")"
"""


def run(*argv: str, stdin: str | None = None) -> str:
    return subprocess.run(argv, input=stdin, capture_output=True, text=True, check=True).stdout


def pull(repo: str, pr: str) -> dict:
    return json.loads(run("gh", "pr", "view", pr, "--repo", repo, "--json", "number,headRefName,baseRefName"))


def chain(repo: str, pr: str) -> tuple[str, list[dict]]:
    """The base the chain sits on and its PRs, bottom first."""
    pulls = [pull(repo, pr)]
    while below := json.loads(run("gh", "pr", "list", "--repo", repo, "--state", "open", "--head", pulls[0]["baseRefName"], "--json", "number,headRefName,baseRefName")):
        pulls.insert(0, below[0])
    return pulls[0]["baseRefName"], pulls


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--lane", required=True)
    parser.add_argument("--sprite")
    parser.add_argument("pr")
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text())
    repo, clone = config["landing"]["repo"], config["landing"]["checkout"]
    named = [config.get(desk, {}).get("sprite", []) for desk in ("fidelity", "finder")]
    sprite = args.sprite or next(name for names in named for name in ([names] if isinstance(names, str) else names))
    base, pulls = chain(repo, args.pr.lstrip("#"))
    token = run("gh", "auth", "token", "--hostname", "github.com").strip()
    committer = [run("git", "config", key).strip() for key in ("user.name", "user.email")]
    done = subprocess.run(["sprite", "exec", "-s", sprite, "--", "sh", "-c", SPRITE_SCRIPT, "sh", clone, base, *(p["headRefName"] for p in pulls)], input="".join(f"{line}\n" for line in (token, *committer)), capture_output=True, text=True)
    print(done.stdout, end="")
    if done.returncode:
        detail = done.stderr.strip()[-400:] or ("" if done.stdout.strip() else f"the sprite printed nothing and exited {done.returncode}")
        print(f"remote-restack: #{args.pr.lstrip('#')} on {sprite} pushed nothing" + (f": {detail}" if detail else ""), file=sys.stderr)
        return 1
    numbers = {p["headRefName"]: p["number"] for p in pulls}
    tip = next(line.split()[2] for line in done.stdout.splitlines() if line.startswith("BASE "))
    for line in done.stdout.splitlines():
        if line.startswith(f"{MOVED} "):
            _, branch, old, new = line.split()
            number = numbers[branch]
            print(run("cci", "post", "--drive", config["drive"], "--lane", args.lane, "--kind", "head", "--pr", str(number), "--to", "desk-runner", "--text", f"HEAD #{number} {new}: restacked onto {base} {tip[:10]} on {sprite} by remote-restack (was {old[:10]})").strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
