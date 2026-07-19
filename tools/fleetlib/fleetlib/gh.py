"""GitHub fleet helpers: repo discovery, deploy keys, actions secrets."""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

from fleetlib.proc import run, try_run

OWNER = "yasyf"


def active_repos(owner: str = OWNER) -> list[str]:
    """Every non-archived source repo the owner has on GitHub."""
    return run(
        "gh", "repo", "list", owner, "--no-archived", "--source",
        "--limit", "1000", "--json", "name", "--jq", ".[].name",
    ).stdout.split()


def has_file(repo: str, path: str, owner: str = OWNER) -> bool:
    """Whether the repo's default branch carries the file. Only a 404 counts as
    absent — any other failure (rate limit, auth, network) is fatal."""
    probe = try_run("gh", "api", f"repos/{owner}/{repo}/contents/{path}")
    if probe.returncode == 0:
        return True
    if "HTTP 404" in probe.stderr:
        return False
    sys.exit(f"FAIL probing {owner}/{repo} for {path}: {probe.stderr.strip()}")


def repos_with_file(path: str, owner: str = OWNER, workers: int = 12) -> list[str]:
    """Active repos carrying the file, sorted. Exits on an empty result — a
    wholesale probe failure is indistinguishable from an empty fleet."""
    repos = active_repos(owner)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        hits = pool.map(lambda repo: has_file(repo, path, owner), repos)
    fleet = sorted(repo for repo, hit in zip(repos, hits, strict=True) if hit)
    if not fleet:
        sys.exit(f"FAIL: no {owner} repo carries {path} — probe failure, not an empty fleet")
    return fleet


def deploy_key_ids(repo: str, title: str, owner: str = OWNER) -> list[str]:
    """Ids of the repo's deploy keys with this title."""
    keys = json.loads(run("gh", "api", f"repos/{owner}/{repo}/keys").stdout)
    return [str(key["id"]) for key in keys if key["title"] == title]


def add_deploy_key(repo: str, title: str, public_key: str, owner: str = OWNER) -> None:
    """Add a write-enabled deploy key to the repo."""
    with TemporaryDirectory() as tmp:
        pub_path = Path(tmp) / "key.pub"
        pub_path.write_text(public_key + "\n")
        run(
            "gh", "repo", "deploy-key", "add", str(pub_path),
            "-R", f"{owner}/{repo}", "--allow-write", "--title", title,
        )


def delete_deploy_keys(repo: str, key_ids: list[str], owner: str = OWNER) -> None:
    """Remove the repo's deploy keys by id."""
    for key_id in key_ids:
        run("gh", "api", "-X", "DELETE", f"repos/{owner}/{repo}/keys/{key_id}")
        print(f"  github: removed old deploy key {key_id}")


def set_secret(repo: str, name: str, value: str, owner: str = OWNER) -> None:
    """Set an actions secret on the repo."""
    run("gh", "secret", "set", name, "-R", f"{owner}/{repo}", stdin=value)
