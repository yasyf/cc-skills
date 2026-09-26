from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "skills/long-running/scripts/label-watch.sh"
APPROVERS = "forge-pr-reviewer[bot],poetic-svc"

GH = """#!/usr/bin/env python3
import os, re, subprocess, sys
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
path = endpoint.split("?")[0]
names = [endpoint] if path.endswith("/pulls") else [endpoint, path]
files = [os.path.join(state, re.sub("[/?&=:]", "_", name) + ".json") for name in names]
found = [f for f in files if os.path.exists(f)]
body = open(found[0]).read() if found or not path.endswith("/pulls") else "[]"
if jq:
    body = subprocess.run(["jq", "-r", jq], input=body, capture_output=True, text=True, check=True).stdout
sys.stdout.write(body)
"""

CCX = """#!/usr/bin/env python3
import json, os, sys
state = os.environ["FAKE_STATE"]
queues = json.load(open(os.path.join(state, "queues.json")))
enqueued = json.load(open(os.path.join(state, "enqueued.json")))
with open(os.path.join(state, "ccx-calls"), "a") as calls:
    calls.write(" ".join(sys.argv[7:]) + "\\n")
print(json.dumps([
    {"number": int(n), "queue": queues[n], "state": "MERGED" if queues[n] == "landed" else "OPEN"}
    | ({"enqueued": enqueued[n]} if n in enqueued else {})
    for n in sys.argv[7:]
]))
"""

SLEEP = """#!/bin/sh
[ "$1" = 7 ] || exit 0
[ ! -f "$FAKE_STATE/unlock" ] || rm -f "$(cat "$FAKE_STATE/unlock")"
echo x >> "$FAKE_STATE/sweeps"
cp "$FAKE_STATE/list" "$FAKE_STATE/list.$(wc -l < "$FAKE_STATE/sweeps" | tr -d ' ')"
[ "$(wc -l < "$FAKE_STATE/sweeps")" -lt 2 ] || : > "$FAKE_STATE/list"
"""


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def listing(endpoint: str) -> str:
    return re.sub("[/?&=:]", "_", endpoint) + ".json"


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
        git(self.checkout, "checkout", "-q", "--orphan", "unrelated")
        git(self.checkout, "rm", "-rqf", ".")
        self.unrelated = commit(self.checkout, "d.txt", "unrelated")
        git(self.checkout, "checkout", "-q", "dev")
        git(self.checkout, "checkout", "-qb", "queued")
        self.queued = commit(self.checkout, "c.txt", "queued")
        git(self.checkout, "checkout", "-q", "dev")
        git(self.checkout, "checkout", "-qb", "behind-queued")
        self.behind_queued = commit(self.checkout, "c.txt", "behind")
        git(self.checkout, "checkout", "-q", "dev")
        git(self.checkout, "checkout", "-qb", "stacked")
        self.stacked = [commit(self.checkout, f"s{i}.txt", "stacked") for i in range(1, 4)]
        git(self.checkout, "checkout", "-q", "dev")
        commit(self.checkout, "a.txt", "ours")
        git(self.checkout, "push", "-q", "origin", "dev", "clean", "conflict", "unrelated", "queued", "behind-queued", "stacked")
        git(self.checkout, "remote", "set-head", "origin", "dev")

        self.queues: dict[str, str] = {}
        self.pulls: dict[int, dict] = {}
        self.enqueued: dict[str, str] = {}
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_STATE": str(self.state),
            "LABEL_WATCH_REPO": "o/r",
            "LABEL_WATCH_CHECKOUT": str(self.checkout),
            "LABEL_WATCH_APPROVERS": APPROVERS,
            "LABEL_WATCH_INTERVAL": "7",
        }

    def pull(self, n: int, sha: str, *, ref=None, base="dev", mergeable=True, state="clean", labels=(), approvers=APPROVERS, statuses=(), runs=()):
        self.queues[str(n)] = "not queued"
        pull = {
            "number": n,
            "head": {"sha": sha, "ref": ref or f"pr{n}"},
            "base": {"ref": base},
            "mergeable": mergeable,
            "mergeable_state": state,
            "labels": [{"name": label} for label in labels],
        }
        self.pulls[n] = pull
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
        (self.state / "enqueued.json").write_text(json.dumps(self.enqueued))
        for pull in self.pulls.values():
            ref = pull["head"]["ref"]
            (self.state / listing(f"repos/o/r/pulls?state=open&head=o:{ref}")).write_text(json.dumps([pull]))
            above = [p for p in self.pulls.values() if p["base"]["ref"] == ref]
            (self.state / listing(f"repos/o/r/pulls?state=open&base={ref}&per_page=100")).write_text(json.dumps(above))
        result = subprocess.run([str(SCRIPT), *args], env=self.env, check=True, capture_output=True, text=True)
        return result.stdout.splitlines()

    def lock(self, ref: str) -> Path:
        lock = self.checkout / ".git" / f"{ref}.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.touch()
        return lock

    def enqueue(self, n: int, sha: str):
        self.queues[str(n)] = "queued"
        self.enqueued[str(n)] = sha

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
    forge.enqueue(1, forge.clean)

    assert forge.run("once", "1") == ["1 SKIP queued"]
    assert forge.calls == []


def test_once_leaves_a_held_pr_alone(forge):
    forge.pull(1, forge.clean, labels=["hold"])

    assert forge.run("once", "1") == ["1 HELD"]
    assert forge.calls == ["GET repos/o/r/pulls/1", "GET repos/o/r/pulls?state=open&base=pr1&per_page=100"]


def test_a_head_with_no_shared_history_is_a_conflict_and_the_sweep_goes_on(forge):
    forge.pull(1, forge.unrelated)
    forge.pull(2, forge.clean)

    assert forge.run("once", "1", "2") == [
        f"1 CONFLICT {forge.unrelated[:10]} no merge base with the trunk",
        f"2 LABELLED {forge.clean[:10]}",
    ]


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


def test_a_head_that_conflicts_with_a_queued_pr_waits_for_it(forge):
    forge.enqueue(1, forge.queued)
    forge.pull(2, forge.behind_queued)

    assert forge.run("once", "1", "2") == ["1 SKIP queued", f"2 NOT-READY {forge.behind_queued[:10]} conflicts-with #1 c.txt"]
    assert "POST repos/o/r/issues/2/labels" not in forge.calls


def test_a_queued_pr_in_the_heads_own_downstack_is_not_a_conflict(forge):
    forge.pull(1, forge.queued, ref="queued")
    forge.enqueue(1, forge.queued)
    forge.pull(2, forge.behind_queued, base="queued")

    assert forge.run("once", "1", "2") == ["1 SKIP queued", f"2 LABELLED {forge.behind_queued[:10]}"]
    assert "GET repos/o/r/pulls?state=open&head=o:queued" in forge.calls


def test_a_head_labelled_earlier_in_the_sweep_is_a_conflict_base(forge):
    forge.pull(1, forge.queued)
    forge.pull(2, forge.behind_queued)

    assert forge.run("once", "1", "2") == [
        f"1 LABELLED {forge.queued[:10]}",
        f"2 NOT-READY {forge.behind_queued[:10]} conflicts-with #1 c.txt",
    ]


def test_watch_keeps_a_queued_pr_as_a_conflict_base_after_it_leaves_the_list(forge):
    forge.enqueue(1, forge.queued)
    forge.pull(2, forge.behind_queued)
    listing = forge.state / "list"
    listing.write_text("1\n2\n")

    lines = [line.split(" ", 1)[1] for line in forge.run("watch", str(listing))]

    assert lines == ["1 SKIP queued", f"2 NOT-READY {forge.behind_queued[:10]} conflicts-with #1 c.txt"]
    assert (forge.state / "ccx-calls").read_text().splitlines() == ["1 2", "2 1"]
    assert "POST repos/o/r/issues/2/labels" not in forge.calls


def test_a_landed_pr_stops_being_tracked(forge):
    forge.enqueue(1, forge.queued)
    forge.pull(2, forge.clean)
    listing = forge.state / "list"
    listing.write_text("1\n2\n")
    forge.queues["1"] = "landed"

    lines = [line.split(" ", 1)[1] for line in forge.run("watch", str(listing))]

    assert lines == ["1 SKIP landed", f"2 LABELLED {forge.clean[:10]}"]
    assert (forge.state / "ccx-calls").read_text().splitlines() == ["1 2"]


def stack(forge, **kwargs):
    for i, sha in enumerate(forge.stacked, start=1):
        forge.pull(i, sha, base=f"pr{i - 1}" if i > 1 else "dev", state="blocked" if i > 1 else "clean", **kwargs.get(str(i), {}))


def posts(forge) -> list[str]:
    return [call for call in forge.calls if call.startswith("POST")]


def test_the_highest_green_pr_of_a_stack_takes_the_label_for_its_downstack(forge):
    stack(forge, **{"3": {"approvers": "poetic-svc"}})

    assert forge.run("once", "1") == [
        "1 SKIP covered-by #2",
        f"2 LABELLED {forge.stacked[1][:10]}",
        f"3 NOT-READY {forge.stacked[2][:10]} awaiting forge-pr-reviewer[bot]",
    ]
    assert posts(forge) == ["POST repos/o/r/issues/2/labels"]


def test_a_listed_mid_stack_pr_labels_the_top_when_the_whole_stack_is_green(forge):
    stack(forge)

    assert forge.run("once", "2") == [
        "1 SKIP covered-by #3",
        "2 SKIP covered-by #3",
        f"3 LABELLED {forge.stacked[2][:10]}",
    ]
    assert posts(forge) == ["POST repos/o/r/issues/3/labels"]


def test_a_red_downstack_pr_blocks_the_stack_above_it_without_reading_their_checks(forge):
    stack(forge, **{"1": {"statuses": [("buildkite/test", "failure")]}})

    assert forge.run("once", "3") == [
        f"1 NOT-READY {forge.stacked[0][:10]} red buildkite/test",
        f"2 NOT-READY {forge.stacked[1][:10]} downstack #1",
        f"3 NOT-READY {forge.stacked[2][:10]} downstack #1",
    ]
    assert posts(forge) == []
    assert not any(call.startswith(("GET repos/o/r/pulls/2", "GET repos/o/r/pulls/3/")) for call in forge.calls)


def test_a_labelled_downstack_pr_passes_the_label_through(forge):
    stack(forge, **{"1": {"labels": ["merge"], "statuses": [("buildkite/test", "pending")]}})

    assert forge.run("once", "2") == ["1 SKIP labelled", "2 SKIP covered-by #3", f"3 LABELLED {forge.stacked[2][:10]}"]
    assert posts(forge) == ["POST repos/o/r/issues/3/labels"]


def test_each_green_fork_top_takes_the_label(forge):
    forge.pull(1, forge.clean)
    forge.pull(2, forge.clean, base="pr1", state="blocked")
    forge.pull(3, forge.clean, base="pr1", state="blocked")

    assert forge.run("once", "1") == [
        "1 SKIP covered-by #2",
        f"2 LABELLED {forge.clean[:10]}",
        f"3 LABELLED {forge.clean[:10]}",
    ]


def test_dry_run_prints_the_label_without_adding_it(forge):
    stack(forge)
    forge.env["LABEL_WATCH_DRY_RUN"] = "1"

    assert forge.run("once", "1") == [
        "1 SKIP covered-by #3",
        "2 SKIP covered-by #3",
        f"3 LABELLED {forge.stacked[2][:10]} dry-run",
    ]
    assert posts(forge) == []


def test_watch_keeps_the_rest_of_the_stack_on_the_list(forge):
    stack(forge, **{"3": {"approvers": "poetic-svc"}})
    list_file = forge.state / "list"
    list_file.write_text("1\n")

    lines = [line.split(" ", 1)[1] for line in forge.run("watch", str(list_file))]

    assert lines == [
        "1 SKIP covered-by #2",
        f"2 LABELLED {forge.stacked[1][:10]}",
        f"3 NOT-READY {forge.stacked[2][:10]} awaiting forge-pr-reviewer[bot]",
    ]
    assert (forge.state / "list.1").read_text() == "3\n"
