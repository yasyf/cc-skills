import functools
import os
import re
import subprocess
from pathlib import Path

from drive import github, land, state

LOG = "restacks.log"
OWN_LIMIT = 60
TRUNK_SUBJECTS = 4000
SUBJECTS_KEPT = 4096
DIFFS_KEPT = 256
SQUASHED = re.compile(r" \(#\d+\)$")


class RestackError(Exception):
    pass


def restack(name: str, keys: list[str], seen: dict[str, tuple]) -> list[dict]:
    """Restack each stack of the open PRs keys names, with no model session, once a merge or rewrite below one of them left its branch carrying commits its base no longer holds: a squash merge of the PR under it, which leaves the copies of that PR's commits, and of every PR squash-merged before it, on each branch above, or a builder's restack of its own branch alone, which leaves every branch above on the head it replaced. Each branch keeps its own commits, the leading run from its head whose subjects neither its parent's branch nor its trunk's squash merges carry, replayed with git replay onto its parent's new head, or its trunk's for a PR based on it, bottom up, and is pushed with a lease on the head the drive read, all of one repository's moved branches in one atomic push, so a builder's push since moves none. A branch whose replay conflicts, or whose change's added and removed lines differ from its PR's before, stays where it is with every branch above it, so its stage reads its conflict for its builder, the one step left for a model. A stack whose heads, bases and conflicts seen holds is read no further, since a merge below it moves its bottom PR's base and GitHub's read of its conflict; what each try did, kept in the drive's restacks.log."""
    data = state.load(name)
    tries = []
    for repo in sorted({github.repo_of(key) for key in keys}):
        rows = {key: data["prs"][key] for key in keys if github.repo_of(key) == repo and data["prs"][key]["default"] and data["prs"][key]["head"]}
        for root, members in stacks(rows):
            signature = tuple((key, rows[key]["head"], rows[key]["base"], rows[key]["conflict"]) for key in members)
            if seen.get(root) == signature:
                continue
            seen[root] = signature
            if found := restacked(name, data, repo, rows, members):
                tries.append(state.append_line(state.drive_dir(name) / LOG, "restack", repo=repo, root=root, **found))
    return tries


def stacks(rows: dict[str, dict]) -> list[tuple[str, list[str]]]:
    """Each stack of rows bottom up, by the PR based on each one's branch: a root based on its repository's default branch with each PR above it, those above a base no row heads left out; a lone PR only while GitHub reads it conflicting."""
    by_branch = {pr["branch"]: key for key, pr in rows.items()}
    above: dict[str, list[str]] = {}
    for key, pr in rows.items():
        if pr["base"] in by_branch:
            above.setdefault(by_branch[pr["base"]], []).append(key)
    found = []
    for key, pr in sorted(rows.items()):
        if pr["base"] != pr["default"] or not (above.get(key) or pr["conflict"]):
            continue
        members, queue = [], [key]
        while queue:
            members.append(member := queue.pop(0))
            queue.extend(sorted(above.get(member, [])))
        found.append((key, members))
    return found


def restacked(name: str, data: dict, repo: str, rows: dict[str, dict], members: list[str]) -> dict | None:
    """Restack one stack, members bottom up, by restack()'s rule; what moved, what it kept and why, or None when every branch already sits on its parent's head and carries nothing trunk squash-merged."""
    try:
        clone = clone_of(name, data, repo)
    except github.GithubError as error:
        return {"failed": str(error)}
    git = ("git", "-C", str(clone))
    refs = f"refs/restack/{os.getpid()}"
    trunk = rows[members[0]]["default"]
    try:
        tip = github.run([*git, "ls-remote", "origin", f"refs/heads/{trunk}"]).split()[0]
        heads = {rows[key]["branch"]: rows[key]["head"] for key in members}
        github.run([*git, "fetch", "-q", "--no-tags", "--no-write-fetch-head", "--no-auto-maintenance", "origin", tip, *heads.values()], timeout=land.CHECKOUT_SECONDS)
        squashed = trunk_subjects(git, tip)
        new, moved, kept = {trunk: tip}, [], []
        for key in members:
            pr = rows[key]
            parent = pr["base"]
            if new.get(parent) is None:
                new[pr["branch"]] = None
                kept.append(f"{key}: on {parent}, which stays")
                continue
            onto, head = new[parent], heads[pr["branch"]]
            stops = squashed | (set() if parent == trunk else subjects(git, tip, heads[parent]) | subjects(git, tip, onto))
            own = own_commits(git, tip, head, stops)
            base = github.run([*git, "rev-parse", f"{head}~{own}"]).strip()
            if base == onto or (parent == trunk and ancestor(git, base, tip)):
                new[pr["branch"]] = head
                continue
            if not (replayed := replay(git, f"{refs}/{pr['branch']}", onto, base, head)):
                new[pr["branch"]] = None
                kept.append(f"{key}: its {own} commit{'' if own == 1 else 's'} conflict on {onto[:12]}")
                continue
            if changed(git, base, head) != changed(git, onto, replayed):
                new[pr["branch"]] = None
                kept.append(f"{key}: its change's lines differ on {onto[:12]}")
                continue
            new[pr["branch"]] = replayed
            moved.append((key, pr["branch"], head, replayed))
        if not (moved or kept):
            return None
        if moved:
            github.run([*git, "push", "-q", "--atomic", "--no-verify", "origin", *(f"--force-with-lease=refs/heads/{branch}:{old}" for _, branch, old, _ in moved), *(f"{sha}:refs/heads/{branch}" for _, branch, _, sha in moved)])
    except github.GithubError as error:
        return {"failed": str(error)}
    finally:
        listed = subprocess.run([*git, "for-each-ref", "--format=delete %(refname)", f"{refs}/"], capture_output=True, text=True, check=False).stdout
        subprocess.run([*git, "update-ref", "--stdin"], input=listed, capture_output=True, text=True, check=False)
    return {"moved": [f"{key} {old[:12]}..{sha[:12]}" for key, _, old, sha in moved], "kept": kept}


def clone_of(name: str, data: dict, repo: str) -> Path:
    """The clone a stack of repo restacks in: the coordinator's checkout when its origin is repo, whose objects already hold nearly all of them, else the drive's own blobless clone of it."""
    checkout = data["roles"]["coordinator"]["cwd"]
    origin = subprocess.run(["git", "-C", checkout, "remote", "get-url", "origin"], capture_output=True, text=True, check=False)
    if origin.returncode == 0 and github.repo_name(origin.stdout).lower() == repo.lower():
        return Path(checkout)
    return land.cloned(name, repo)


@functools.lru_cache(maxsize=SUBJECTS_KEPT)
def trunk_subjects(git: tuple[str, ...], tip: str) -> frozenset[str]:
    """The subjects of trunk's newest TRUNK_SUBJECTS commits from tip, each squash merge's PR number cut off, read once per tip for every stack a pass restacks."""
    return frozenset(SQUASHED.sub("", subject) for subject in github.run([*git, "log", "--format=%s", f"-{TRUNK_SUBJECTS}", tip]).splitlines())


@functools.lru_cache(maxsize=SUBJECTS_KEPT)
def subjects(git: tuple[str, ...], tip: str, head: str) -> frozenset[str]:
    """The subjects of tip..head, remembered by the commits named, which no push changes, since each PR of a stack reads its parent's and a parent left in place is read again as the head it moves onto."""
    return frozenset(github.run([*git, "log", "--format=%s", f"{tip}..{head}"]).splitlines())


def own_commits(git: tuple[str, ...], tip: str, head: str, stops: set[str]) -> int:
    """How many commits from head down its first parents are its own: the leading run trunk's tip does not hold, up to OWN_LIMIT, whose subjects stops does not name."""
    listed = github.run([*git, "log", "--first-parent", "--format=%s", f"-{OWN_LIMIT}", head, f"^{tip}"]).splitlines()
    return next((index for index, subject in enumerate(listed) if subject in stops), len(listed))


def ancestor(git: tuple[str, ...], commit: str, of: str) -> bool:
    return subprocess.run([*git, "merge-base", "--is-ancestor", commit, of], capture_output=True, check=False).returncode == 0


def replay(git: tuple[str, ...], ref: str, onto: str, base: str, head: str) -> str | None:
    """The head git replay makes of base..head onto onto, kept on ref; None on a conflict."""
    github.run([*git, "update-ref", ref, head])
    done = subprocess.run([*git, "replay", f"--onto={onto}", f"--ref={ref}", "--ref-action=print", f"{base}..{head}"], capture_output=True, text=True, check=False)
    if done.returncode:
        return None
    return done.stdout.split()[2]


@functools.lru_cache(maxsize=DIFFS_KEPT)
def changed(git: tuple[str, ...], base: str, head: str) -> tuple[str, ...]:
    """The added and removed lines of base..head's diff, in order, so a replay that moves its hunks or the lines around them reads the same."""
    return tuple(line for line in github.run([*git, "diff", "-U0", "--no-color", base, head]).splitlines() if line[:1] in "+-" and not line.startswith(("+++ ", "--- ")))
