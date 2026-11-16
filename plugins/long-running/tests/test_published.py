import hashlib
import io
import json
import tarfile
import urllib.request
import zipfile
from pathlib import Path

import pytest
from drive import github, published, receiver
from drive.state import DriveError

REPO = "Forge-AI/design-docs"
MERGE = "967f1e30" + "0" * 32
OLDER = "5a1b2c3d" + "0" * 32
RUN = "43688267543"
PAGE = b"<h1>Partition strategies</h1>"


def site() -> bytes:
    tarred = io.BytesIO()
    with tarfile.open(fileobj=tarred, mode="w") as files:
        for name, data in {"./index.html": PAGE, "./ai.json": b'{"key": "sk-held"}'}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            files.addfile(info, io.BytesIO(data))
    zipped = io.BytesIO()
    with zipfile.ZipFile(zipped, "w") as outer:
        outer.writestr(published.BUNDLE, tarred.getvalue())
    return zipped.getvalue()


def pages(monkeypatch: pytest.MonkeyPatch, deployed: list[tuple[str, str]], zipped: bytes, digest: str, expired: bool = False) -> list[tuple[dict, float]]:
    reads = iter(deployed)
    newest: dict[str, str] = {}
    followed: list[tuple[dict, float]] = []

    def read(what: str, cmd: list[str], max_age: str = "0s", text: bool = True, timeout: int = 120) -> str | bytes:
        if cmd[:3] == ["gh", "pr", "view"]:
            return MERGE + "\n"
        path = cmd[2]
        if path == f"repos/{REPO}/deployments?environment=github-pages&per_page=1":
            newest["sha"], newest["stage"] = next(reads)
            return json.dumps([{"id": 7799974655, "sha": newest["sha"]}])
        if path == f"repos/{REPO}/deployments/7799974655/statuses?per_page=1":
            log = f"https://github.com/{REPO}/actions/runs/{RUN}/job/1"
            return json.dumps([{"state": newest["stage"], "log_url": log, "created_at": "2026-11-15T07:04:38Z", "environment_url": "https://docs.poetic.design/"}])
        if path == f"repos/{REPO}/actions/runs/{RUN}/artifacts?name=github-pages":
            return json.dumps({"artifacts": [{"id": 14151476282, "size_in_bytes": len(zipped), "digest": digest, "expired": expired, "expires_at": "2026-11-16T07:04:38Z"}]})
        assert (path, text, timeout) == (f"repos/{REPO}/actions/artifacts/14151476282/zip", False, published.DOWNLOAD_SECONDS)
        return zipped

    monkeypatch.setattr(github, "read", read)
    monkeypatch.setattr(github, "holds", lambda repo, ref, commit: ref == commit)
    monkeypatch.setattr(receiver, "waited", lambda want, seconds: followed.append((want, seconds)) or (None, 0))
    return followed


def test_a_merged_prs_published_files_are_served_once_the_run_deploying_them_completes_and_deleted_after(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(published.tempfile, "tempdir", str(tmp_path))
    zipped = site()
    followed = pages(monkeypatch, [(MERGE, "in_progress"), (MERGE, "success")], zipped, f"sha256:{hashlib.sha256(zipped).hexdigest()}")
    with published.served(f"{REPO}#363") as (root, lines):
        assert urllib.request.urlopen(f"{root}index.html", timeout=5).read() == PAGE
        [folder] = tmp_path.glob("drive-published-*")
        assert (folder / "ai.json").exists()
    assert not folder.exists()
    assert followed == [({"repo": REPO, "run": int(RUN)}, receiver.WAIT_SECONDS_MOST)]
    assert capsys.readouterr().err.splitlines() == [
        f"drive pr published: {REPO}'s github-pages deployment 7799974655 at {MERGE[:12]} reads in_progress;"
        f" following https://github.com/{REPO}/actions/runs/{RUN}/job/1 until it completes",
    ]
    assert f"deployed by https://github.com/{REPO}/actions/runs/{RUN}" in lines[0]
    assert lines[1] == f"its artifact github-pages 14151476282, {len(zipped):,} bytes, downloaded with sha256:{hashlib.sha256(zipped).hexdigest()}, the digest GitHub keeps for it"
    assert lines[2] == f"serving those published files, https://docs.poetic.design/ as it serves them, at {root}"


def test_a_download_whose_sha256_differs_from_the_digest_github_keeps_is_refused_before_anything_is_served(monkeypatch: pytest.MonkeyPatch) -> None:
    pages(monkeypatch, [(MERGE, "success")], site(), "sha256:" + "0" * 64)
    with pytest.raises(DriveError, match=f"downloaded as sha256:[0-9a-f]{{64}}, not the sha256:{'0' * 64} GitHub keeps for it"), published.served(f"{REPO}#363"):
        pass


def test_a_deployment_that_failed_holding_the_merge_still_deploys_after_its_run_or_holds_an_older_commit_and_an_expired_artifact_are_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    followed = pages(monkeypatch, [(MERGE, "failure"), (MERGE, "queued"), (MERGE, "in_progress"), (OLDER, "in_progress")], site(), "sha256:", expired=True)
    with pytest.raises(DriveError, match=f"holding {MERGE[:12]}, reads failure: https://github.com/{REPO}/actions/runs/{RUN}/job/1"):
        published.deployment(REPO, MERGE)
    with pytest.raises(DriveError, match=f"has published {MERGE[:12]}: its newest, 7799974655 at {MERGE[:12]}, reads in_progress$"):
        published.deployment(REPO, MERGE)
    with pytest.raises(DriveError, match=f"reads in_progress and does not hold {MERGE[:12]}$"):
        published.deployment(REPO, MERGE)
    assert len(followed) == 1
    with pytest.raises(DriveError, match=f"expired at 2026-11-16T07:04:38Z: `gh run rerun {RUN} -R {REPO}`"):
        published.bundle(REPO, RUN)
