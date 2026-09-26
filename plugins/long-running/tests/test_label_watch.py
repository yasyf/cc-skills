from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/long-running/scripts/label-watch.sh"
APPROVERS = "forge-pr-reviewer[bot],poetic-svc"

GH = """#!/usr/bin/env python3
import os, subprocess, sys
state = os.environ["FAKE_STATE"]
args, method, jq, endpoint = sys.argv[2:], "GET", None, None
while args:
    arg = args.pop(0)
    if arg == "-X":
        method = args.pop(0)
    elif arg == "--jq":
        jq = args.pop(0)
    elif arg == "-f":
        args.pop(0)
    elif arg != "--silent":
        endpoint = arg
with open(os.path.join(state, "calls"), "a") as calls:
    calls.write(f"{method} {endpoint}\\n")
if method == "POST":
    sys.exit(0)
body = open(os.path.join(state, endpoint.split("?")[0].replace("/", "_") + ".json")).read()
if jq:
    body = subprocess.run(["jq", "-r", jq], input=body, capture_output=True, text=True, check=True).stdout
sys.stdout.write(body)
"""

CCX = """#!/usr/bin/env python3
import json, os, sys
queues = json.load(open(os.path.join(os.environ["FAKE_STATE"], "queues.json")))
print(json.dumps([{"number": int(n), "queue": queues[n]} for n in sys.argv[7:]]))
"""

SLEEP = """#!/bin/sh
[ "$1" = 7 ] || exit 0
[ ! -f "$FAKE_STATE/unlock" ] || rm -f "$(cat "$FAKE_STATE/unlock")"
echo x >> "$FAKE_STATE/sweeps"
[ "$(wc -l < "$FAKE_STATE/sweeps")" -lt 2 ] || : > "$FAKE_STATE/list"
"""


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def commit(repo: Path, name: str, text: str) -> str:
    (repo / name).write_text(text)
    git(repo, "add", name)
    git(repo, "commit", "-qm", f"{name}: {text}")
    return git(repo, "rev-parse", "HEAD")


class Forge:
    def __init__(self, root: Path):
        self.state = root / "state"
        self.state.mkdir()
        bin_dir = root / "bin"
        bin_dir.mkdir()
        for name, body in {"gh": GH, "ccx": CCX, "sleep": SLEEP}.items():
            (bin_dir / name).write_text(body)
            (bin_dir / name).chmod(0o755)

        origin = root / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "dev", str(origin)], check=True)
        self.checkout = root / "work"
        subprocess.run(["git", "clone", "-q", str(origin), str(self.checkout)], check=True, capture_output=True)
        git(self.checkout, "config", "user.email", "t@example.com")
        git(self.checkout, "config", "user.name", "t")
        git(self.checkout, "checkout", "-qb", "dev")
        commit(self.checkout, "a.txt", "base")
        git(self.checkout, "checkout", "-qb", "clean")
        self.clean = commit(self.checkout, "b.txt", "clean")
        git(self.checkout, "checkout", "-q", "dev")
        git(self.checkout, "checkout", "-qb", "conflict")
        self.conflict = commit(self.checkout, "a.txt", "theirs")
        git(self.checkout, "checkout", "-q", "dev")
        commit(self.checkout, "a.txt", "ours")
        git(self.checkout, "push", "-q", "origin", "dev", "clean", "conflict")
        git(self.checkout, "remote", "set-head", "origin", "dev")

        self.queues: dict[str, str] = {}
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_STATE": str(self.state),
            "LABEL_WATCH_REPO": "o/r",
            "LABEL_WATCH_CHECKOUT": str(self.checkout),
            "LABEL_WATCH_APPROVERS": APPROVERS,
            "LABEL_WATCH_INTERVAL": "7",
        }

    def pull(self, n: int, sha: str, *, base="dev", mergeable=True, state="clean", labels=(), approvers=APPROVERS, statuses=(), runs=()):
        self.queues[str(n)] = "not queued"
        pull = {
            "head": {"sha": sha},
            "base": {"ref": base},
            "mergeable": mergeable,
            "mergeable_state": state,
            "labels": [{"name": label} for label in labels],
        }
        reviews = [{"commit_id": sha, "state": "APPROVED", "user": {"login": login}} for login in approvers.split(",") if login]
        (self.state / f"repos_o_r_pulls_{n}.json").write_text(json.dumps(pull))
        (self.state / f"repos_o_r_pulls_{n}_reviews.json").write_text(json.dumps(reviews))
        (self.state / f"repos_o_r_commits_{sha}_status.json").write_text(
            json.dumps({"statuses": [{"context": context, "state": state} for context, state in statuses]})
        )
        (self.state / f"repos_o_r_commits_{sha}_check-runs.json").write_text(
            json.dumps({"check_runs": [{"name": name, "status": status, "conclusion": conclusion} for name, status, conclusion in runs]})
        )

    def run(self, *args: str) -> list[str]:
        (self.state / "queues.json").write_text(json.dumps(self.queues))
        result = subprocess.run([str(SCRIPT), *args], env=self.env, check=True, capture_output=True, text=True)
        return result.stdout.splitlines()

    def lock(self, ref: str) -> Path:
        lock = self.checkout / ".git" / f"{ref}.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.touch()
        return lock

    @property
    def calls(self) -> list[str]:
        path = self.state / "calls"
        return path.read_text().splitlines() if path.exists() else []


@pytest.fixture
def forge(tmp_path):
    return Forge(tmp_path)


def test_once_labels_a_clean_approved_head_through_rest(forge):
    forge.pull(1, forge.clean)

    assert forge.run("once", "1") == [f"1 LABELLED {forge.clean[:10]}"]
    assert forge.calls[-1] == "POST repos/o/r/issues/1/labels"


def test_once_skips_a_queued_pr_without_reading_github(forge):
    forge.pull(1, forge.clean)
    forge.queues["1"] = "queued"

    assert forge.run("once", "1") == ["1 SKIP queued"]
    assert forge.calls == []


def test_once_leaves_a_held_pr_alone(forge):
    forge.pull(1, forge.clean, labels=["hold"])

    assert forge.run("once", "1") == ["1 HELD"]
    assert forge.calls == ["GET repos/o/r/pulls/1"]


def test_once_names_the_conflicting_files_and_never_labels(forge):
    forge.pull(1, forge.conflict)

    assert forge.run("once", "1") == [f"1 CONFLICT {forge.conflict[:10]} a.txt"]
    assert "POST repos/o/r/issues/1/labels" not in forge.calls


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"base": "graphite-base/1"}, "base graphite-base/1"),
        ({"mergeable": None}, "mergeable null"),
        ({"state": "blocked"}, "blocked"),
        ({"approvers": "forge-pr-reviewer[bot]"}, "awaiting poetic-svc"),
    ],
)
def test_once_refuses_a_head_that_is_not_ready(forge, kwargs, reason):
    forge.pull(1, forge.clean, **kwargs)

    assert forge.run("once", "1") == [f"1 NOT-READY {forge.clean[:10]} {reason}"]
    assert "POST repos/o/r/issues/1/labels" not in forge.calls


def test_once_allows_a_stacked_head_that_is_mergeable_but_not_clean(forge):
    forge.pull(1, forge.clean, base="parent", state="blocked")

    assert forge.run("once", "1") == [f"1 LABELLED {forge.clean[:10]}"]


def test_a_red_status_on_a_mergeable_unstable_head_never_labels(forge):
    forge.pull(1, forge.clean, base="parent", state="unstable", statuses=[("buildkite/test", "failure"), ("ci-timing", "success")])

    assert forge.run("once", "1") == [f"1 NOT-READY {forge.clean[:10]} red buildkite/test"]
    assert not any(call.startswith("POST") for call in forge.calls)


def test_a_failed_check_run_outranks_a_pending_one(forge):
    forge.pull(1, forge.clean, runs=[("lint", "in_progress", None), ("unit tests", "completed", "cancelled")])

    assert forge.run("once", "1") == [f"1 NOT-READY {forge.clean[:10]} red unit tests"]


def test_a_pending_check_waits(forge):
    forge.pull(1, forge.clean, statuses=[("buildkite/test", "pending")])

    assert forge.run("once", "1") == [f"1 NOT-READY {forge.clean[:10]} pending buildkite/test"]


def test_graphite_mergeability_and_skipped_runs_do_not_block(forge):
    forge.pull(
        1,
        forge.clean,
        statuses=[("buildkite/test", "success")],
        runs=[("Graphite / mergeability_check", "in_progress", None), ("docs", "completed", "skipped")],
    )

    assert forge.run("once", "1") == [f"1 LABELLED {forge.clean[:10]}"]


def test_approval_on_an_older_head_does_not_count(forge):
    forge.pull(1, forge.clean)
    reviews = forge.state / "repos_o_r_pulls_1_reviews.json"
    reviews.write_text(reviews.read_text().replace(forge.clean, "0" * 40))

    assert forge.run("once", "1") == [f"1 NOT-READY {forge.clean[:10]} awaiting {APPROVERS}"]


def test_watch_drops_settled_entries_and_prints_only_changes(forge):
    forge.pull(1, forge.clean)
    forge.pull(2, forge.conflict)
    forge.pull(3, forge.clean)
    forge.queues["3"] = "landed"
    listing = forge.state / "list"
    listing.write_text("1\n2\n3\n")

    lines = [line.split(" ", 1)[1] for line in forge.run("watch", str(listing))]

    assert lines == [
        f"1 LABELLED {forge.clean[:10]}",
        f"2 CONFLICT {forge.conflict[:10]} a.txt",
        "3 SKIP landed",
    ]
    assert (forge.state / "sweeps").read_text().count("x") == 2


def test_a_held_lock_on_the_remote_tracking_trunk_does_not_block_the_gate(forge):
    forge.pull(1, forge.clean)
    forge.lock("refs/remotes/origin/dev")

    assert forge.run("once", "1") == [f"1 LABELLED {forge.clean[:10]}"]


def test_a_failed_trunk_fetch_labels_nothing(forge):
    forge.pull(1, forge.clean)
    forge.lock("refs/label-watch/dev")

    assert forge.run("once", "1") == ["1 API-FAIL trunk-fetch"]
    assert forge.calls == []


def test_watch_survives_a_failed_trunk_fetch(forge):
    forge.pull(1, forge.clean)
    (forge.state / "unlock").write_text(str(forge.lock("refs/label-watch/dev")))
    listing = forge.state / "list"
    listing.write_text("1\n")

    lines = [line.split(" ", 1)[1] for line in forge.run("watch", str(listing))]

    assert lines == ["1 API-FAIL trunk-fetch", f"1 LABELLED {forge.clean[:10]}"]
    assert listing.read_text() == ""


def test_a_pruning_fetch_config_keeps_the_private_trunk_ref(forge):
    forge.pull(2, forge.conflict)
    subprocess.run(["git", "config", "fetch.prune", "true"], cwd=forge.checkout, check=True)
    subprocess.run(["git", "config", "fetch.pruneTags", "true"], cwd=forge.checkout, check=True)

    assert forge.run("once", "2") == forge.run("once", "2") == [f"2 CONFLICT {forge.conflict[:10]} a.txt"]
