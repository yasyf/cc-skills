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
evictions = json.load(open(os.path.join(state, "evictions.json")))
if os.path.exists(os.path.join(state, "drained")):
    queues = {n: "landed" for n in queues}
enqueued = json.load(open(os.path.join(state, "enqueued.json")))
with open(os.path.join(state, "ccx-calls"), "a") as calls:
    calls.write(" ".join(sys.argv[7:]) + "\\n")
def report(n):
    queue = queues[n]
    if queue == "closed":
        return {"number": int(n), "queue": "not queued", "state": "CLOSED"}
    r = {"number": int(n), "queue": queue, "state": "MERGED" if queue == "landed" else "OPEN"}
    if n in enqueued:
        r["enqueued"] = enqueued[n]
    if queue == "evicted":
        r["evicted"], r["evicted_at"] = evictions[n]
    return r
print(json.dumps([report(n) for n in sys.argv[7:]]))
"""

CURL = """#!/usr/bin/env python3
import json, os, sys
state = os.environ["FAKE_STATE"]
args = sys.argv[1:]
config = sys.stdin.read()
body = json.loads(args[args.index("-d") + 1])
with open(os.path.join(state, "graphite-calls"), "a") as calls:
    calls.write(json.dumps({"url": args[-1], "auth": config.strip(), "body": body}) + "\\n")
sys.exit(int(os.environ.get("FAKE_CURL_EXIT", "0")))
"""

SLEEP = """#!/bin/sh
[ "$1" = 7 ] || exit 0
[ ! -f "$FAKE_STATE/unlock" ] || rm -f "$(cat "$FAKE_STATE/unlock")"
echo x >> "$FAKE_STATE/sweeps"
cp "$FAKE_STATE/list" "$FAKE_STATE/list.$(wc -l < "$FAKE_STATE/sweeps" | tr -d ' ')"
[ ! -f "$FAKE_STATE/between" ] || { sh "$FAKE_STATE/between"; rm "$FAKE_STATE/between"; }
[ "$(wc -l < "$FAKE_STATE/sweeps")" -lt "${FAKE_SWEEPS:-2}" ] || { : > "$FAKE_STATE/list"; touch "$FAKE_STATE/drained"; }
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


class ForgeTopology:
    def __init__(self, root: Path):
        self.origin = root / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "dev", str(self.origin)], check=True)
        checkout = root / "work"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(checkout)], check=True, capture_output=True)
        git(checkout, "config", "user.email", "t@example.com")
        git(checkout, "config", "user.name", "t")
        git(checkout, "checkout", "-qb", "dev")
        commit(checkout, "a.txt", "base")
        git(checkout, "checkout", "-qb", "clean")
        self.clean = commit(checkout, "b.txt", "clean")
        git(checkout, "checkout", "-q", "dev")
        git(checkout, "checkout", "-qb", "conflict")
        self.conflict = commit(checkout, "a.txt", "theirs")
        git(checkout, "checkout", "-q", "dev")
        git(checkout, "checkout", "-q", "--orphan", "unrelated")
        git(checkout, "rm", "-rqf", ".")
        self.unrelated = commit(checkout, "d.txt", "unrelated")
        git(checkout, "checkout", "-q", "dev")
        git(checkout, "checkout", "-qb", "queued")
        self.queued = commit(checkout, "c.txt", "queued")
        git(checkout, "checkout", "-q", "dev")
        git(checkout, "checkout", "-qb", "behind-queued")
        self.behind_queued = commit(checkout, "c.txt", "behind")
        git(checkout, "checkout", "-q", "dev")
        git(checkout, "checkout", "-qb", "stacked")
        self.stacked = [commit(checkout, f"s{i}.txt", "stacked") for i in range(1, 4)]
        git(checkout, "checkout", "-q", "dev")
        commit(checkout, "a.txt", "ours")
        git(checkout, "push", "-q", "origin", "dev", "clean", "conflict", "unrelated", "queued", "behind-queued", "stacked")


class Forge:
    def __init__(self, root: Path, topology: ForgeTopology):
        self.state = root / "state"
        self.state.mkdir()
        bin_dir = root / "bin"
        bin_dir.mkdir()
        for name, body in {"gh": GH, "ccx": CCX, "curl": CURL, "sleep": SLEEP}.items():
            (bin_dir / name).write_text(body)
            (bin_dir / name).chmod(0o755)

        origin = root / "origin.git"
        subprocess.run(["git", "clone", "-q", "--bare", "--shared", str(topology.origin), str(origin)], check=True)
        self.checkout = root / "work"
        subprocess.run(["git", "clone", "-q", "--shared", str(origin), str(self.checkout)], check=True, capture_output=True)
        git(self.checkout, "config", "user.email", "t@example.com")
        git(self.checkout, "config", "user.name", "t")
        git(self.checkout, "remote", "set-head", "origin", "dev")
        self.clean = topology.clean
        self.conflict = topology.conflict
        self.unrelated = topology.unrelated
        self.queued = topology.queued
        self.behind_queued = topology.behind_queued
        self.stacked = list(topology.stacked)

        self.queues: dict[str, str] = {}
        self.pulls: dict[int, dict] = {}
        self.enqueued: dict[str, str] = {}
        self.evictions: dict[str, tuple[str, str]] = {}
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "FAKE_STATE": str(self.state),
            "LABEL_WATCH_REPO": "o/r",
            "LABEL_WATCH_CHECKOUT": str(self.checkout),
            "LABEL_WATCH_APPROVERS": APPROVERS,
            "LABEL_WATCH_INTERVAL": "7",
            "LABEL_WATCH_GRAPHITE_API": "https://graphite.test/v1",
            "LABEL_WATCH_GRAPHITE_AUTH": str(root / "auth"),
        }
        (root / "auth").write_text(json.dumps({"authToken": "tok"}))

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
        (self.state / "evictions.json").write_text(json.dumps(self.evictions))
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

    @property
    def graphite(self) -> list[dict]:
        path = self.state / "graphite-calls"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    @property
    def enqueues(self) -> list[list[int]]:
        return [call["body"]["prNumbers"] for call in self.graphite]


@pytest.fixture(scope="session")
def forge_topology(tmp_path_factory):
    return ForgeTopology(tmp_path_factory.mktemp("label-watch-topology"))


@pytest.fixture
def forge(tmp_path, forge_topology):
    return Forge(tmp_path, forge_topology)


def test_once_enqueues_a_clean_approved_head_through_the_graphite_api(forge):
    forge.pull(1, forge.clean)

    assert forge.run("once", "1") == [f"1 ENQUEUED {forge.clean[:10]}"]
    assert forge.graphite == [
        {
            "url": "https://graphite.test/v1/graphite/merge",
            "auth": 'header = "Authorization: token tok"',
            "body": {"repoOwner": "o", "repoName": "r", "trunkBranchName": "dev", "prNumbers": [1]},
        }
    ]
    assert not any(call.startswith("POST") for call in forge.calls)


def test_a_failed_enqueue_is_an_api_failure(forge):
    forge.pull(1, forge.clean)
    forge.env["FAKE_CURL_EXIT"] = "22"

    assert forge.run("once", "1") == ["1 API-FAIL enqueue"]


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
        f"2 ENQUEUED {forge.clean[:10]}",
    ]


def test_once_names_the_conflicting_files_and_never_labels(forge):
    forge.pull(1, forge.conflict)

    assert forge.run("once", "1") == [f"1 CONFLICT {forge.conflict[:10]} a.txt"]
    assert forge.enqueues == []


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
    assert forge.enqueues == []


def test_once_allows_a_stacked_head_that_is_mergeable_but_not_clean(forge):
    forge.pull(1, forge.clean, base="parent", state="blocked")

    assert forge.run("once", "1") == [f"1 ENQUEUED {forge.clean[:10]}"]


def test_a_red_status_on_a_mergeable_unstable_head_never_labels(forge):
    forge.pull(1, forge.clean, base="parent", state="unstable", statuses=[("buildkite/test", "failure"), ("ci-timing", "success")])

    assert forge.run("once", "1") == [f"1 NOT-READY {forge.clean[:10]} red buildkite/test"]
    assert forge.enqueues == []


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

    assert forge.run("once", "1") == [f"1 ENQUEUED {forge.clean[:10]}"]


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
        f"1 ENQUEUED {forge.clean[:10]}",
        f"2 CONFLICT {forge.conflict[:10]} a.txt",
        "3 SKIP landed",
    ]
    assert (forge.state / "sweeps").read_text().count("x") == 2


def test_a_held_lock_on_the_remote_tracking_trunk_does_not_block_the_gate(forge):
    forge.pull(1, forge.clean)
    forge.lock("refs/remotes/origin/dev")

    assert forge.run("once", "1") == [f"1 ENQUEUED {forge.clean[:10]}"]


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

    assert lines == ["1 API-FAIL trunk-fetch", f"1 ENQUEUED {forge.clean[:10]}"]
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
    assert forge.enqueues == []


def test_a_pr_stacked_on_a_queued_pr_waits_for_it_to_land(forge):
    forge.pull(1, forge.queued, ref="queued")
    forge.enqueue(1, forge.queued)
    forge.pull(2, forge.behind_queued, base="queued")

    assert forge.run("once", "1", "2") == ["1 SKIP queued", f"2 NOT-READY {forge.behind_queued[:10]} downstack #1"]
    assert "GET repos/o/r/pulls?state=open&head=o:queued" in forge.calls
    assert forge.enqueues == []


def test_a_head_enqueued_earlier_in_the_sweep_is_a_conflict_base(forge):
    forge.pull(1, forge.queued)
    forge.pull(2, forge.behind_queued)

    assert forge.run("once", "1", "2") == [
        f"1 ENQUEUED {forge.queued[:10]}",
        f"2 NOT-READY {forge.behind_queued[:10]} conflicts-with #1 c.txt",
    ]


def test_watch_keeps_a_queued_pr_as_a_conflict_base_after_it_leaves_the_list(forge):
    forge.enqueue(1, forge.queued)
    forge.pull(2, forge.behind_queued)
    listing = forge.state / "list"
    listing.write_text("1\n2\n")

    lines = [line.split(" ", 1)[1] for line in forge.run("watch", str(listing))]

    assert lines == ["1 SKIP queued", f"2 NOT-READY {forge.behind_queued[:10]} conflicts-with #1 c.txt"]
    assert (forge.state / "ccx-calls").read_text().splitlines() == ["1 2", "2 1", "1"]
    assert forge.enqueues == []


def test_a_landed_pr_stops_being_tracked(forge):
    forge.enqueue(1, forge.queued)
    forge.pull(2, forge.clean)
    listing = forge.state / "list"
    listing.write_text("1\n2\n")
    forge.queues["1"] = "landed"

    lines = [line.split(" ", 1)[1] for line in forge.run("watch", str(listing))]

    assert lines == ["1 SKIP landed", f"2 ENQUEUED {forge.clean[:10]}"]
    assert (forge.state / "ccx-calls").read_text().splitlines() == ["1 2", "2", "2"]


def stack(forge, **kwargs):
    for i, sha in enumerate(forge.stacked, start=1):
        forge.pull(i, sha, base=f"pr{i - 1}" if i > 1 else "dev", state="blocked" if i > 1 else "clean", **kwargs.get(str(i), {}))


def test_the_green_bottom_prefix_of_a_stack_goes_in_while_the_top_waits(forge):
    stack(forge, **{"3": {"approvers": "poetic-svc"}})

    assert forge.run("once", "1") == [
        "1 SKIP covered-by #2",
        f"2 ENQUEUED {forge.stacked[1][:10]} prefix",
        f"3 NOT-READY {forge.stacked[2][:10]} awaiting forge-pr-reviewer[bot] restack-after #2",
    ]
    assert forge.enqueues == [[1, 2]]


def test_a_listed_mid_stack_pr_enqueues_the_whole_stack_when_it_is_green(forge):
    stack(forge)

    assert forge.run("once", "2") == [
        "1 SKIP covered-by #3",
        "2 SKIP covered-by #3",
        f"3 ENQUEUED {forge.stacked[2][:10]}",
    ]
    assert forge.enqueues == [[1, 2, 3]]


def test_a_red_downstack_pr_blocks_the_stack_above_it_without_reading_their_checks(forge):
    stack(forge, **{"1": {"statuses": [("buildkite/test", "failure")]}})

    assert forge.run("once", "3") == [
        f"1 NOT-READY {forge.stacked[0][:10]} red buildkite/test",
        f"2 NOT-READY {forge.stacked[1][:10]} downstack #1",
        f"3 NOT-READY {forge.stacked[2][:10]} downstack #1",
    ]
    assert forge.enqueues == []
    assert not any(call.startswith(("GET repos/o/r/pulls/2", "GET repos/o/r/pulls/3/")) for call in forge.calls)


def test_a_labelled_downstack_pr_passes_the_label_through(forge):
    stack(forge, **{"1": {"labels": ["merge"], "statuses": [("buildkite/test", "pending")]}})

    assert forge.run("once", "2") == ["1 SKIP labelled", "2 SKIP covered-by #3", f"3 ENQUEUED {forge.stacked[2][:10]}"]
    assert forge.enqueues == [[1, 2, 3]]


def test_a_forked_stack_never_goes_in(forge):
    forge.pull(1, forge.clean)
    forge.pull(2, forge.clean, base="pr1", state="blocked")
    forge.pull(3, forge.clean, base="pr1", state="blocked")

    assert forge.run("once", "1") == [
        f"1 NOT-READY {forge.clean[:10]} fork at #1",
        f"2 NOT-READY {forge.clean[:10]} fork at #1",
        f"3 NOT-READY {forge.clean[:10]} fork at #1",
    ]
    assert forge.enqueues == []


def test_a_queued_downstack_pr_holds_the_stack_above_it(forge):
    stack(forge)
    forge.enqueue(1, forge.stacked[0])

    assert forge.run("once", "3") == [
        "1 SKIP queued",
        f"2 NOT-READY {forge.stacked[1][:10]} downstack #1",
        f"3 NOT-READY {forge.stacked[2][:10]} downstack #1",
    ]
    assert forge.enqueues == []


def test_dry_run_marks_a_prefix_enqueue(forge):
    stack(forge, **{"2": {"statuses": [("buildkite/test", "failure")]}})
    forge.env["LABEL_WATCH_DRY_RUN"] = "1"

    assert forge.run("once", "3") == [
        f"1 ENQUEUED {forge.stacked[0][:10]} prefix dry-run",
        f"2 NOT-READY {forge.stacked[1][:10]} red buildkite/test restack-after #1",
        f"3 NOT-READY {forge.stacked[2][:10]} downstack #2 restack-after #1",
    ]
    assert forge.enqueues == []


def test_dry_run_prints_the_enqueue_without_making_it(forge):
    stack(forge)
    forge.env["LABEL_WATCH_DRY_RUN"] = "1"

    assert forge.run("once", "1") == [
        "1 SKIP covered-by #3",
        "2 SKIP covered-by #3",
        f"3 ENQUEUED {forge.stacked[2][:10]} dry-run",
    ]
    assert forge.enqueues == []


def test_watch_keeps_the_rest_of_the_stack_on_the_list(forge):
    stack(forge, **{"3": {"approvers": "poetic-svc"}})
    list_file = forge.state / "list"
    list_file.write_text("1\n")

    lines = [line.split(" ", 1)[1] for line in forge.run("watch", str(list_file))]

    assert lines == [
        "1 SKIP covered-by #2",
        f"2 ENQUEUED {forge.stacked[1][:10]} prefix",
        f"3 NOT-READY {forge.stacked[2][:10]} awaiting forge-pr-reviewer[bot] restack-after #2",
    ]
    assert (forge.state / "list.1").read_text() == "3\n"


def test_a_held_pr_ends_the_prefix_below_it(forge):
    stack(forge)
    hold = forge.state / "hold"
    hold.write_text("2\n")
    forge.env["LABEL_WATCH_HOLD"] = str(hold)

    assert forge.run("once", "1") == [
        f"1 ENQUEUED {forge.stacked[0][:10]} prefix",
        f"2 NOT-READY {forge.stacked[1][:10]} held restack-after #1",
        f"3 NOT-READY {forge.stacked[2][:10]} downstack #2 restack-after #1",
    ]
    assert forge.enqueues == [[1]]
    assert not any(call.startswith(("GET repos/o/r/pulls/2", "GET repos/o/r/pulls/3/")) for call in forge.calls)


def test_watch_never_appends_a_held_pr(forge):
    stack(forge)
    hold = forge.state / "hold"
    hold.write_text("1\n")
    forge.env["LABEL_WATCH_HOLD"] = str(hold)
    list_file = forge.state / "list"
    list_file.write_text("3\n")

    lines = [line.split(" ", 1)[1] for line in forge.run("watch", str(list_file))]

    assert lines == [
        f"1 NOT-READY {forge.stacked[0][:10]} held",
        f"2 NOT-READY {forge.stacked[1][:10]} downstack #1",
        f"3 NOT-READY {forge.stacked[2][:10]} downstack #1",
    ]
    assert forge.enqueues == []
    assert "1" not in (forge.state / "list.1").read_text().split()



def between(forge, script: str):
    (forge.state / "between").write_text(script)


def land(forge, n: int) -> str:
    return f'git -C "{forge.checkout}" commit -q --allow-empty -m "landed (#{n})" && git -C "{forge.checkout}" push -q origin dev\n'


def test_watch_reports_a_graphite_eviction_once_and_gates_the_pr_again(forge):
    forge.pull(1, forge.conflict, labels=["merge"])
    forge.enqueue(1, forge.conflict)
    pull = forge.pulls[1] | {"mergeable": False, "mergeable_state": "dirty", "labels": []}
    between(
        forge,
        f"""cat > "$FAKE_STATE/queues.json" <<'EOF'
{json.dumps({"1": "evicted"})}
EOF
cat > "$FAKE_STATE/evictions.json" <<'EOF'
{json.dumps({"1": ["it had merge conflicts", "Sep 28, 3:32 PM UTC"]})}
EOF
cat > "$FAKE_STATE/repos_o_r_pulls_1.json" <<'EOF'
{json.dumps(pull)}
EOF
""",
    )
    forge.env["FAKE_SWEEPS"] = "3"
    list_file = forge.state / "list"
    list_file.write_text("1\n")

    lines = [line.split(" ", 1)[1] for line in forge.run("watch", str(list_file))]

    assert lines == [
        "1 SKIP queued",
        f"1 EVICTED {forge.conflict[:10]} it had merge conflicts Sep 28, 3:32 PM UTC",
        f"1 CONFLICT {forge.conflict[:10]} a.txt",
    ]
    assert forge.calls.count("GET repos/o/r/pulls/1") == 2
    assert not any("timeline" in call for call in forge.calls)
    assert not forge.enqueues


def test_a_pr_whose_squash_is_on_the_trunk_costs_no_read(forge):
    forge.pull(1, forge.clean)
    forge.queues["1"] = "queued"
    git(forge.checkout, "commit", "-q", "--allow-empty", "-m", "landed (#1)")
    git(forge.checkout, "push", "-q", "origin", "dev")

    assert forge.run("once", "1") == ["1 SKIP landed"]
    assert forge.calls == []
    assert not (forge.state / "ccx-calls").exists()


def test_watch_drops_a_tracked_pr_once_its_squash_reaches_the_trunk(forge):
    forge.pull(1, forge.clean)
    between(forge, land(forge, 1))
    forge.env["FAKE_SWEEPS"] = "9"
    list_file = forge.state / "list"
    list_file.write_text("1\n")

    lines = [line.split(" ", 1)[1] for line in forge.run("watch", str(list_file))]

    assert lines == [f"1 ENQUEUED {forge.clean[:10]}"]
    assert (forge.state / "ccx-calls").read_text().splitlines() == ["1"]
    assert (forge.state / "sweeps").read_text().count("x") == 1


def test_a_closed_pr_leaves_the_list_without_a_github_read(forge):
    forge.pull(1, forge.clean)
    forge.queues["1"] = "closed"
    list_file = forge.state / "list"
    list_file.write_text("1\n")

    lines = [line.split(" ", 1)[1] for line in forge.run("watch", str(list_file))]

    assert lines == ["1 SKIP closed"]
    assert forge.calls == []
    assert list_file.read_text() == ""


def test_watch_reads_a_pr_listed_twice_once(forge):
    forge.pull(1, forge.conflict)
    list_file = forge.state / "list"
    list_file.write_text("1\n1\n\n1\n")

    lines = [line.split(" ", 1)[1] for line in forge.run("watch", str(list_file))]

    assert lines == [f"1 CONFLICT {forge.conflict[:10]} a.txt"]
    assert (forge.state / "ccx-calls").read_text().splitlines() == ["1", "1"]
