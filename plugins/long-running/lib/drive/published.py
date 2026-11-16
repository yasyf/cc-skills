"""The files a repository's GitHub Pages site published, served on this Mac, so a proof clicks a page of a private site that only a browser signed in to GitHub can open, a sign-in no lane holds."""
import contextlib
import hashlib
import io
import json
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path

from drive import github, picture, receiver
from drive.state import DriveError

ENVIRONMENT = "github-pages"
ARTIFACT = "github-pages"
BUNDLE = "artifact.tar"
FAILED = ("failure", "error")
DEPLOYING = ("queued", "pending", "in_progress")
SERVED_ENV = "CLAUDE_LONG_RUNNING_PUBLISHED"
DOWNLOAD_SECONDS = 600


@contextlib.contextmanager
def served(key: str) -> Iterator[tuple[str, list[str]]]:
    """The files the newest github-pages deployment of a merged PR's repository published, once it holds the PR's merge, by deployment, served on a loopback port while the block runs, as the URL of their root beside the lines saying what they are: the deployment, the Actions run that deployed it, the artifact and the digest its download matched; the files deleted once the block ends, since a site can publish keys its pages read."""
    repo = key.split("#")[0]
    if not (merge := github.merge_commit(key)):
        raise DriveError(f"{key} is not merged, so no Pages deployment of {repo} holds it")
    found, status = deployment(repo, merge)
    run = github.RUN_URL.search(status["log_url"])["run"]
    artifact = bundle(repo, run)
    zipped = github.read(f"{repo}'s Pages artifact {artifact['id']}", ["gh", "api", f"repos/{repo}/actions/artifacts/{artifact['id']}/zip"], text=False, timeout=DOWNLOAD_SECONDS)
    digest = f"sha256:{hashlib.sha256(zipped).hexdigest()}"
    if digest != artifact["digest"]:
        raise DriveError(f"{repo}'s Pages artifact {artifact['id']} downloaded as {digest}, not the {artifact['digest']} GitHub keeps for it")
    with tempfile.TemporaryDirectory(prefix="drive-published-") as folder:
        with zipfile.ZipFile(io.BytesIO(zipped)) as outer, outer.open(BUNDLE) as inner, tarfile.open(fileobj=inner, mode="r|") as files:
            files.extractall(folder, filter="data")
        with picture.served(Path(folder)) as root:
            yield root, [
                f"{key} merged at {merge[:12]}; {repo}'s github-pages deployment {found['id']} at {found['sha'][:12]} holds it and read success at {status['created_at']},"
                f" deployed by https://github.com/{repo}/actions/runs/{run}",
                f"its artifact {ARTIFACT} {artifact['id']}, {artifact['size_in_bytes']:,} bytes, downloaded with {digest}, the digest GitHub keeps for it",
                f"serving those published files, {status['environment_url']} as it serves them, at {root}",
            ]


def deployment(repo: str, merge: str) -> tuple[dict, dict]:
    """The repository's newest github-pages deployment and its newest status once that status reads success and its commit holds merge; one still deploying that holds merge is read once more after receiver.waited hears the Actions run deploying it complete, as drive wait follows a run, so nothing polls GitHub; refused once it failed holding merge, still deploys after that wait, or holds an older commit."""
    found, status = newest(repo)
    if status["state"] in DEPLOYING and github.holds(repo, found["sha"], merge):
        print(f"drive pr published: {repo}'s github-pages deployment {found['id']} at {found['sha'][:12]} reads {status['state']}; following {status['log_url']} until it completes", file=sys.stderr, flush=True)
        receiver.waited(receiver.wanted(status["log_url"], {}), receiver.WAIT_SECONDS_MOST)
        found, status = newest(repo)
    holds = github.holds(repo, found["sha"], merge)
    if holds and status["state"] == "success":
        return found, status
    if holds and status["state"] in FAILED:
        raise DriveError(f"{repo}'s github-pages deployment {found['id']} at {found['sha'][:12]}, holding {merge[:12]}, reads {status['state']}: {status['log_url']}")
    raise DriveError(
        f"no github-pages deployment of {repo} has published {merge[:12]}: its newest, {found['id']} at {found['sha'][:12]}, reads {status['state']}"
        f"{'' if holds else f' and does not hold {merge[:12]}'}"
    )


def newest(repo: str) -> tuple[dict, dict]:
    """The repository's newest github-pages deployment and that deployment's newest status."""
    [found] = json.loads(github.read(f"{repo}'s newest Pages deployment", ["gh", "api", f"repos/{repo}/deployments?environment={ENVIRONMENT}&per_page=1"]))
    [status] = json.loads(github.read(f"{repo}'s Pages deployment {found['id']}", ["gh", "api", f"repos/{repo}/deployments/{found['id']}/statuses?per_page=1"]))
    return found, status


def bundle(repo: str, run: str) -> dict:
    """The github-pages artifact an Actions run uploaded and deployed, as GitHub lists it with its sha256 digest; refused once it expired, since a Pages workflow keeps it a day or less."""
    listed = json.loads(github.read(f"the {ARTIFACT} artifact of {repo}'s run {run}", ["gh", "api", f"repos/{repo}/actions/runs/{run}/artifacts?name={ARTIFACT}"]))["artifacts"]
    if not listed:
        raise DriveError(f"{repo}'s run {run} keeps no {ARTIFACT} artifact")
    if (artifact := listed[0])["expired"]:
        raise DriveError(
            f"{repo}'s {ARTIFACT} artifact {artifact['id']} of run {run} expired at {artifact['expires_at']}: `gh run rerun {run} -R {repo}`"
            " publishes the same files again with a fresh one"
        )
    if not artifact.get("digest"):
        raise DriveError(f"GitHub keeps no digest for {repo}'s {ARTIFACT} artifact {artifact['id']}, so its download cannot be checked")
    return artifact
