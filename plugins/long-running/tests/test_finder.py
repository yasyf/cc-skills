"""finder.py against a fake gh, git, rulings.py, codex-ask and cci: the record a pass posts, the range it reads, and when it asks again or posts nothing."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import finder
import pytest
from conftest import gateway
from long_running.grades import FINDER_FAIL, FINDER_PASS, finder_grade, graded_head

REPO = "Forge-AI/monorepo"
HEAD = "7414944e30bfec89dee45328414dbc1016e35afa"
OLD = "a269784e49009c5aa92b2341e08d6945f7ff8e95"
BASE = "d011466aa1b2c3d4e5f60718293a4b5c6d7e8f90"
STALE_BASE = "c3a3afc0307096aabf6d24513b2436d1bb23bbe8"
OWN = "e1f2a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4"
OLD_OWN = "f0e1d2c3b4a5968778695a4b3c2d1e0f9a8b7c6d"
NEWER = "5be7c0de5be7c0de5be7c0de5be7c0de5be7c0de"
OWN_PATHS = ("signal/stream.go", "signal/stream_test.go")
TRUNK = "9d1e2f3a4b5c6d7e8f9011a2b3c4d5e6f7a8b9c0"
LANDED_ON_TRUNK = "4d2ea9dfbad0c1e2f3a4b5c6d7e8f9011a2b3c4d"
PARENT_TIP = "3c5e7a9b1d2f4a6c8e0b1d3f5a7c9e1b2d4f6a8c"
MOVED = "8a6c4e2f0b9d7a5c3e1f8b6d4a2c0e9f7b5d3a1c"
ROWS = (
    f"Required rows for {REPO}#43299 at {HEAD}\n\n## Every global, pending and touched row (2)\n\n"
    "### rule/949e6be | scope | in-force | said 1 | 2026-10-22\n> it should be a ssql replica that is live streaming, not polled\n\n"
    "### prompt/1026-105052 | reserved | in-force | said 1 | 2026-10-26\n> no credential on disk\nslice-hash: 0123456789ab\n"
)
REASONS = "The resubscribe hunks keep the fetched position across a cut, and every caller passes it through unchanged. "
SWEPT = REASONS + "rows checked: 2"
PARENT = "0b0e1f2a3c4d5e6f708192a3b4c5d6e7f8091a2b"
EARLIER = (f"{OLD_OWN}^..{OLD}", f"{OWN}^..{HEAD}")


def patch(*lines: str) -> str:
    return "diff --git a/go/follow.go b/go/follow.go\nindex 1111111..2222222 100644\n--- a/go/follow.go\n+++ b/go/follow.go\n@@ -1,3 +1,3 @@\n" + "".join(f"{line}\n" for line in lines)


DIFF = "".join(
    f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -{line},0 +{line},4 @@\n+changed()\n"
    for path, line in [("go/sand/internal/replica/follow.go", 40), ("rust/crates/sandsql-journal/src/replica.rs", 2946), ("go/poll.go", 9), *((f"go/sand/file{index}.go", 1) for index in range(4))]
)
LANDED = {PARENT[:11]: "diff --git a/api/parent.go b/api/parent.go\n--- a/api/parent.go\n+++ b/api/parent.go\n@@ -0,0 +1 @@\n+landed()\n", f"{BASE}:api/parent.go": "landed()\n"}


def pairs(*marks: str) -> str:
    """`git range-diff --no-patch` output: one parent commit only the earlier head carried, then one line per own commit."""
    lines = [f" 1:  {PARENT[:11]} <  -:  ----------- api: the parent's commit before its restack"]
    lines += [f" {index}:  {OLD_OWN[:11]} {mark}  {index - 1}:  {OWN[:11]} sand: the head's own commit" for index, mark in enumerate(marks, 2)]
    return "\n".join(lines) + "\n"



class Fake:
    def __init__(self, tmp_path: Path, replies: list[dict], state: str = "OPEN", heads: tuple[str, ...] = (HEAD,), own: dict | None = None, resolve: dict | None = None, failures: int = 0, gone: tuple[str, ...] = (), ranges: dict | None = None, shows: dict | None = None, trunk: str = BASE, history: tuple[str, ...] = (), pulls: dict | None = None, races: int = 0, subjects: tuple[str, ...] = (), base_gone: bool = False, rows: str = ROWS):
        self.tmp_path = tmp_path
        self.rows = rows
        self.replies = list(replies)
        self.state = state
        self.heads = list(heads)
        self.own = {(BASE, HEAD): [OWN]} if own is None else own
        self.resolve = resolve or {}
        self.failures = failures
        self.gone = gone
        self.ranges = ranges or {}
        self.trunk = trunk
        self.history = history
        self.pulls = pulls
        self.races = races
        self.subjects = subjects
        self.base_gone = base_gone
        self.shows = LANDED | ({OLD_OWN[:11]: patch("-poll()", "+stream()"), OWN[:11]: patch("-poll()", "+stream()", "+resubscribe()")} if shows is None else shows)
        self.calls: list[tuple[list[str], Path | None]] = []
        self.comments = [{"body": "## Rulings\n- the resubscribe test pins finder #150321's fix"}, {"body": "LGTM from the review bot"}]
        self.posted: list[dict] = []
        self.timeouts: dict[str, int] = {}

    def run(self, argv: list[str], cwd: Path | None = None, timeout: int = finder.READ_SECONDS) -> str:
        argv = gateway(argv)
        self.calls.append((argv, cwd))
        if argv[:3] == ["gh", "pr", "view"]:
            if self.failures:
                self.failures -= 1
                raise subprocess.CalledProcessError(1, argv, stderr='Post "https://api.github.com/graphql": net/http: TLS handshake timeout')
            head = self.heads.pop(0) if len(self.heads) > 1 else self.heads[0]
            return json.dumps({"state": self.state, "baseRefName": "dev", "baseRefOid": STALE_BASE, "headRefOid": head, "body": "## Summary\nA cut signal resubscribes from the position it fetched.", "comments": self.comments})
        if argv[0] == "git":
            return self.git(argv[1:])
        if argv[:2] == [sys.executable, str(finder.SCRIPTS / "rulings.py")]:
            self.timeouts[argv[2]] = timeout
            return self.rows if argv[2] == "slice" else "/plans/durable/plan/plan.md\n"
        if argv[0] == "codex-ask":
            if isinstance(answer := self.replies.pop(0), BaseException):
                raise answer
            reply = self.tmp_path / f"reply-{len(self.calls)}.json"
            reply.write_text(json.dumps(answer))
            return f"REPLY_FILE: {reply}\nLOG_FILE: {reply}.log\nAWAIT: codex-ask --await {argv[argv.index('-s') + 1]}\n"
        if argv[:2] == ["cci", "post"]:
            assert "--force" not in argv
            flags = [arg for arg in argv[2:] if arg != "--json"]
            self.posted.append(dict(zip(flags[::2], flags[1::2], strict=True)))
            return json.dumps({"seq": 150001, "num": "#7@fixture"}) + "\n"
        raise AssertionError(f"unexpected call {argv}")

    def git(self, argv: list[str]) -> str:
        if argv[0] == "ls-remote" and argv[-1] == "refs/pull/*/head":
            return "".join(f"{sha}\trefs/pull/{number}/head\n" for sha, number in self.pulls.items())
        if argv[0] == "ls-remote":
            tips = {"HEAD": self.trunk, **({} if self.base_gone else {"refs/heads/dev": BASE})}
            return "".join(f"{tips[ref]}\t{ref}\n" for ref in argv[2:] if ref in tips)
        if argv[0] == "fetch":
            if self.races:
                self.races -= 1
                raise subprocess.CalledProcessError(128, argv, stderr="fatal: bad object 0000000000000000000000000000000000000000")
            if any(sha in self.gone for sha in argv):
                raise subprocess.CalledProcessError(128, argv, stderr="fatal: remote error: upload-pack: not our ref")
            return ""
        if argv[:5] == ["log", "--no-merges", "--no-renames", "--format=", "--name-only"]:
            return "".join(f"{path}\n\n" for path in OWN_PATHS) if self.own.get(tuple(argv[5].split(".."))) else ""
        if argv[0] == "log":
            assert argv[1:4] == ["--format=%s", "-n", str(finder.BASE_SUBJECTS)]
            return "".join(f"{subject}\n" for subject in self.subjects)
        if argv[0] == "range-diff":
            assert argv[1:3] == ["--no-patch", "--no-color"]
            return self.ranges[tuple(argv[3:])]
        if argv[0] == "show":
            return self.shows[argv[-1]]
        if argv[0] == "diff":
            assert argv[1:] == ["--unified=0", "--no-color", "--no-ext-diff", f"{OWN}^", HEAD]
            return DIFF
        if argv[0] == "rev-parse":
            if (full := self.resolve.get(argv[-1].removesuffix("^{commit}"))) is None:
                raise subprocess.CalledProcessError(1, argv)
            return f"{full}\n"
        if argv[0] == "merge-base":
            if self.pulls is not None:
                raise subprocess.CalledProcessError(1, argv)
            return ""
        if argv[:2] == ["rev-list", "--topo-order"]:
            assert argv[2:] == [HEAD, f"^{self.trunk}"]
            return "".join(f"{sha}\n" for sha in self.history)
        if argv[0] == "rev-list":
            return "".join(f"{sha}\n" for sha in self.own.get(tuple(argv[argv.index("--") - 1].split("...")), []))
        raise AssertionError(f"unexpected git {argv}")

    def asks(self) -> list[tuple[list[str], Path | None]]:
        return [(argv, cwd) for argv, cwd in self.calls if argv[0] == "codex-ask"]


def job(tmp_path: Path, **changes) -> Path:
    directory = tmp_path / "finder-desk" / f"43299-{HEAD[:12]}"
    directory.mkdir(parents=True)
    spec = {"drive": "d1", "repo": REPO, "pr": "43299", "head": HEAD, "since": "", "prior": None, "previous": "", "history": [], "settled": {}, "lane": "p8a-step9", "to": ["desk-runner"], "checkout": str(tmp_path / "checkout"), "model": "sol", "fast": True, "full": False}
    (directory / finder.JOB).write_text(json.dumps(spec | changes))
    return directory


def reply(*findings: dict, swept: str = SWEPT) -> dict:
    return {"verdict": "issues" if findings else "lgtm", "swept": swept, "findings": list(findings)}


def finding(severity: str, cite: str = "go/sand/internal/replica/follow.go:42", defect: str = "the resubscribe drops the held marker") -> dict:
    return {"severity": severity, "cite": cite, "defect": defect, "fix": "keep the marker", "unverified": None}


def result(directory: Path) -> dict:
    return json.loads((directory / finder.RESULT).read_text())


@pytest.mark.parametrize("state", ["OPEN", "MERGED"])
def test_a_head_with_only_minors_posts_finder_ok_over_its_whole_change_to_the_heads_lane(tmp_path, state):
    directory = job(tmp_path)
    shell = Fake(tmp_path, [reply(finding("minor"), finding("nit"))], state)
    assert finder.main([str(directory)], shell) == 0
    [post] = shell.posted
    assert post["--drive"] == "d1" and post["--lane"] == finder.LANE and post["--kind"] == "review" and post["--pr"] == f"{REPO}#43299"
    assert post["--to"] == "p8a-step9,desk-runner" and post["--id"] == f"finder:{REPO}#43299:{HEAD}" and post["--path"] == str(directory / "record.md")
    assert post["--text"] == f"finder: ok {HEAD} sol finder over whole {OWN[:7]}^..{HEAD[:7]}: no majors"
    assert finder_grade(post["--text"]) == FINDER_PASS and graded_head(post["--text"]) == HEAD
    question = (directory / "question-1.md").read_text()
    assert f"`git diff {OWN}^ {HEAD}`" in question and f"on base dev ({BASE})" in question and STALE_BASE not in question
    assert str(directory / "rows.md") in question and "Open `swept` with your reasoning" in question
    assert "## Rulings\n- the resubscribe test pins finder #150321's fix" in question and "LGTM from the review bot" not in question
    assert result(directory) == {"verdict": "ok", "record": post["--text"], "posted": "#150001"}


def test_the_plan_read_gets_five_minutes_so_a_cold_sprite_disk_does_not_fail_the_pass(tmp_path):
    """Root #220405: on a Sprite just restarted, `ccn ledger show` took 95 s and six passes failed reading the plan at the 120 s limit."""
    shell = Fake(tmp_path, [reply()])
    assert finder.main([str(job(tmp_path))], shell) == 0
    assert shell.timeouts["plan"] == 300


def test_a_review_addressed_to_a_worker_no_lane_registered_still_posts_and_the_pass_succeeds(tmp_path):
    directory = job(tmp_path, lane="p8a7-send-latency", to=["desk-runner", "p8a-step7"])
    shell = Fake(tmp_path, [reply(finding("major"))])
    assert finder.main([str(directory)], shell) == 0
    [post] = shell.posted
    assert post["--to"] == "p8a7-send-latency,desk-runner,p8a-step7" and finder_grade(post["--text"]) == FINDER_FAIL
    assert result(directory)["posted"] == "#150001"

@pytest.mark.parametrize(("fast", "tier"), [(True, ["--incident"]), (False, [])], ids=["fast", "standard"])
def test_a_finder_pass_asks_codex_at_the_tier_its_job_names(tmp_path, fast, tier):
    directory = job(tmp_path, fast=fast)
    shell = Fake(tmp_path, [reply()])
    finder.main([str(directory)], shell)
    [(ask, _)] = shell.asks()
    assert ask[: 5 + len(tier)] == ["codex-ask", "-m", "sol", *tier, "--schema", "findings"]


def test_a_head_whose_parent_was_restacked_reads_only_its_own_commits_against_the_base_branchs_current_tip(tmp_path):
    directory = job(tmp_path)
    shell = Fake(tmp_path, [reply()], own={(BASE, HEAD): [OWN, NEWER]})
    finder.main([str(directory)], shell)
    [rev_list] = [argv for argv, _ in shell.calls if argv[:2] == ["git", "rev-list"]]
    assert rev_list[:8] == ["git", "rev-list", "--cherry-pick", "--right-only", "--no-merges", "--reverse", f"{BASE}...{HEAD}"]
    question = (directory / "question-1.md").read_text()
    assert f"`git diff {OWN}^ {HEAD}`" in question and f"{BASE}...{HEAD}" not in question and f"{STALE_BASE}...{HEAD}" not in question


def test_the_patch_match_against_the_trunk_reads_only_the_paths_the_heads_own_commits_touch(tmp_path):
    """remote-desks-5 #259406: in a partial clone, `rev-list --cherry-pick` over every trunk commit since the merge-base fetched blobs one at a time for 12 minutes; limiting it to the head's own paths bounds that fetch by the PR's files."""
    directory = job(tmp_path)
    shell = Fake(tmp_path, [reply()])
    finder.main([str(directory)], shell)
    [rev_list] = [argv for argv, _ in shell.calls if argv[:3] == ["git", "rev-list", "--cherry-pick"]]
    assert rev_list[7:] == ["--", *OWN_PATHS]


def test_a_head_rebased_past_its_stale_base_branch_onto_the_trunk_reads_and_scopes_only_its_own_commits(tmp_path):
    prior = {"seq": 161820, "lane": "finder-desk", "head": "a269784", "text": "finder: ok a269784"}
    directory = job(tmp_path, since=OLD, prior=prior)
    own = {(BASE, HEAD): [LANDED_ON_TRUNK, OWN], (TRUNK, HEAD): [OWN], (BASE, OLD): [OLD_OWN], (TRUNK, OLD): [OLD_OWN]}
    shell = Fake(tmp_path, [reply(finding("major", "go/sand/internal/replica/follow.go:41"))], own=own, resolve={OLD: OLD}, ranges={EARLIER: pairs("!")}, trunk=TRUNK)
    finder.main([str(directory)], shell)
    question = (directory / "question-1.md").read_text()
    assert f"`git range-diff {OLD_OWN}^..{OLD} {OWN}^..{HEAD}`" in question and f"`git diff {OWN}^ {HEAD}`" in question
    assert f"the range-diff shows for its pair ({OWN[:11]}); each new commit whole with `git show <sha>` (none)" in question and LANDED_ON_TRUNK[:11] not in question
    assert [argv for argv, _ in shell.calls if argv[:2] == ["git", "diff"]] == [["git", "diff", "--unified=0", "--no-color", "--no-ext-diff", f"{OWN}^", HEAD]]
    assert shell.posted[0]["--text"].startswith(f"finder: major {HEAD} 1 open")


def test_a_replay_whose_parent_was_restacked_past_its_stale_graphite_base_carries_the_graded_verdict_and_reads_none_of_the_parents_commits(tmp_path):
    prior = {"seq": 165658, "lane": "finder-desk", "head": "a269784", "text": "finder: ok a269784 sol finder over delta 6ec2c60..a269784: no majors"}
    directory = job(tmp_path, since=OLD, prior=prior)
    own = {(BASE, HEAD): [MOVED, OWN], (PARENT_TIP, HEAD): [OWN], (TRUNK, HEAD): [MOVED, OWN], (BASE, OLD): [OLD_OWN], (PARENT_TIP, OLD): [OLD_OWN], (TRUNK, OLD): [OLD_OWN]}
    restacked = f" -:  ----------- >  1:  {MOVED[:11]} runs: the cloud-runs batch the parent was restacked onto\n 1:  {OLD_OWN[:11]} =  2:  {OWN[:11]} sand: the head's own commit\n"
    ranges = {EARLIER: f" 1:  {OLD_OWN[:11]} =  1:  {OWN[:11]} sand: the head's own commit\n", (f"{OLD_OWN}^..{OLD}", f"{MOVED}^..{HEAD}"): restacked}
    pulls = {HEAD: 43299, PARENT_TIP: 43736, NEWER: 44038}
    shell = Fake(tmp_path, [reply(finding("major", "go/runs/batch.go:12", "the batch drops a run"))], own=own, resolve={OLD: OLD}, ranges=ranges, trunk=TRUNK, history=(HEAD, OWN, PARENT_TIP, MOVED), pulls=pulls)
    finder.main([str(directory)], shell)
    assert not shell.asks() and [post["--text"] for post in shell.posted] == [f"finder: ok {HEAD[:7]} carried from #165658"]
    assert not [argv for argv, _ in shell.calls if argv[:2] == ["git", "diff"]]


def test_a_requested_full_pass_at_a_head_a_delta_record_graded_reads_the_whole_change_at_the_live_head_and_says_so(tmp_path):
    prior = {"seq": 149302, "lane": "finder-desk", "head": "a269784", "text": f"finder: ok {HEAD} sol finder over delta a269784..{HEAD[:7]}: no majors"}
    directory = job(tmp_path, head="", history=[prior | {"path": ""}], full=True)
    shell = Fake(tmp_path, [reply()])
    finder.main([str(directory)], shell)
    question = (directory / "question-1.md").read_text()
    assert f"This pass reads the PR's whole change: `git diff {OWN}^ {HEAD}`." in question and (directory / "rows.md").exists()
    [post] = shell.posted
    assert post["--text"] == f"finder: ok {HEAD} sol finder over whole {OWN[:7]}^..{HEAD[:7]}: no majors" and post["--id"] == f"finder:{REPO}#43299:{HEAD}:whole"
    assert graded_head(post["--text"]) == HEAD and finder_grade(post["--text"]) == FINDER_PASS


@pytest.mark.parametrize(
    ("kind", "to", "text", "prs"),
    [
        ("ask", ["finder-desk", "p8b-step10"], "finder-desk: finder: whole on #44114 over its own commits", ["44114"]),
        ("ask", ["finder-desk"], "Finder: whole #44114, please", ["44114"]),
        ("ask", ["finder-desk"], "finder: whole on #46075 decdc34, #46155 58c7d42, #45799 5066a43, #46075 decdc34", ["46075", "46155", "45799"]),
        ("ask", ["finder-desk"], "finder-desk: run #44114 at 7d13a98 again", []),
        ("ask", ["finder-desk"], "finder-desk: one whole-diff pass on #44114", []),
        ("decide", ["finder-desk"], "re #182756: the carried major on #44764 8b4019d is settled", []),
        ("decide", ["finder-desk"], "finder: whole #44114", []),
        ("ask", ["p8b-step10"], "finder: whole #44114", []),
        ("ask", ["finder-desk"], f"finder: whole pr:{REPO}#77 after yasyf/cc-skills#9940", ["77"]),
        ("ask", ["finder-desk"], "finder: whole yasyf/cc-skills#9940", []),
    ],
)
def test_only_an_ask_to_the_finder_desk_saying_finder_whole_requests_a_full_pass(kind, to, text, prs):
    assert finder.requested({"kind": kind, "to": to, "text": text, "refs": {}}, REPO) == prs


def test_a_head_that_carries_no_commit_the_base_lacks_runs_no_finder(tmp_path):
    directory = job(tmp_path)
    shell = Fake(tmp_path, [], own={})
    finder.main([str(directory)], shell)
    assert not shell.asks() and not shell.posted
    assert result(directory) == {"skipped": f"{REPO}#43299 at {HEAD[:12]} carries no commit dev lacks"}


def test_blockers_and_majors_post_finder_major_one_clause_each_and_minors_stay_off_the_record(tmp_path):
    directory = job(tmp_path)
    majors = [finding("blocker", "rust/crates/sandsql-journal/src/replica.rs:2947", "registered mounts never record a resubscribe position"), finding("major", "go/poll.go:9", "row rule/949e6be missing: a mount\npolls every 5 s")]
    shell = Fake(tmp_path, [reply(*majors, finding("minor", "a.go:1", "a name reads oddly"))])
    finder.main([str(directory)], shell)
    text = shell.posted[0]["--text"]
    assert text == f"finder: major {HEAD} 2 open over whole {OWN[:7]}^..{HEAD[:7]}: rust/crates/sandsql-journal/src/replica.rs:2947 registered mounts never record a resubscribe position; go/poll.go:9 row rule/949e6be missing: a mount polls every 5 s"
    assert finder_grade(text) == FINDER_FAIL and graded_head(text) == HEAD
    assert "a name reads oddly" not in text and "a name reads oddly" in (directory / "record.md").read_text()


def test_the_finder_asks_what_runs_per_item_at_a_thousand_and_a_per_chat_read_posts_as_a_major(tmp_path):
    directory = job(tmp_path)
    slow = finding("major", "go/sand/internal/replica/follow.go:42", "slow at N: the chat list reads each chat's settings one at a time, 1,000 reads at 1,000 chats")
    shell = Fake(tmp_path, [reply(slow)])
    finder.main([str(directory)], shell)
    question = " ".join((directory / "question-1.md").read_text().split())
    assert "Ask what the change runs per chat, team, session, or run when there are 1,000 of them." in question
    assert "a loop over chats, teams, sessions, runs, files, or rows that awaits I/O per item" in question
    assert "a cache, cap, timer, retry, or idle wait whose PR body names no fundamental fix it stands in for" in question
    text = shell.posted[0]["--text"]
    assert text == f"finder: major {HEAD} 1 open over whole {OWN[:7]}^..{HEAD[:7]}: go/sand/internal/replica/follow.go:42 slow at N: the chat list reads each chat's settings one at a time, 1,000 reads at 1,000 chats"
    assert finder_grade(text) == FINDER_FAIL


def test_a_major_whose_cites_name_no_changed_line_and_a_census_finding_go_to_the_census_file_with_one_note_to_the_lane_never_root(tmp_path):
    directory = job(tmp_path)
    stands = finding("blocker", "go/x.go:3, go/sand/internal/replica/follow.go:41-44", "the marker is dropped")
    dev_only = finding("major", "go/sand/internal/replica/follow.go:12", "the drain loop skips a closed mount")
    marked = finding("minor", "go/poll.go:9", "census: the poll interval predates this PR")
    shell = Fake(tmp_path, [reply(stands, dev_only, marked)])
    finder.main([str(directory)], shell)
    review, note = shell.posted
    assert review["--text"] == f"finder: major {HEAD} 1 open over whole {OWN[:7]}^..{HEAD[:7]}: go/x.go:3, go/sand/internal/replica/follow.go:41-44 the marker is dropped"
    census = tmp_path / "finder-desk" / f"census-{time.strftime('%Y-%m-%d')}.md"
    assert note["--kind"] == "note" and note["--lane"] == finder.LANE and note["--to"] == "p8a-step9,desk-runner" and note["--id"] == f"finder-census:{REPO}#43299:{HEAD}"
    assert note["--text"] == f"finder census: 2 dev-only findings on #43299 appended to {census}"
    assert census.read_text() == f"- {REPO}#43299 {HEAD[:12]} go/sand/internal/replica/follow.go:12: the drain loop skips a closed mount\n- {REPO}#43299 {HEAD[:12]} go/poll.go:9: the poll interval predates this PR\n"
    assert "- go/sand/internal/replica/follow.go: 40-43\n" in (directory / "question-1.md").read_text()
    assert result(directory)["census"] == "#150001"


def test_hunks_read_new_side_ranges_a_bare_count_as_one_line_and_a_deletion_as_the_line_it_follows():
    diff = "diff --git a/a.go b/a.go\n--- a/a.go\n+++ b/a.go\n@@ -3 +3 @@\n-x\n+y\n@@ -9,2 +8,0 @@\n-p\n-q\n@@ -20,0 +21,4 @@\n+n\n"
    assert finder.hunks(diff) == {"a.go": [(3, 3), (8, 8), (21, 24)]}


def test_majors_past_ccis_limit_post_a_clipped_line_that_still_reads_major_with_the_whole_reply_on_its_path(tmp_path):
    directory = job(tmp_path)
    shell = Fake(tmp_path, [reply(*(finding("major", f"go/sand/file{index}.go:{index + 1}", "the follower keeps a stale incarnation after a swap " * 3) for index in range(4)))])
    finder.main([str(directory)], shell)
    text = shell.posted[0]["--text"]
    assert len(text) == finder.CCI_TEXT and text.endswith("…") and finder_grade(text) == FINDER_FAIL
    assert (directory / "record.md").read_text().count("go/sand/file3.go:4") == 2


def test_a_pass_after_an_earlier_finder_reads_since_that_head_and_checks_its_majors(tmp_path):
    prior = {"seq": 149302, "lane": "p8a-step9", "head": "a269784", "text": "finder: major a269784 1 open: walk.go:9 skips the volume link"}
    answer = {"seq": 149310, "lane": "p8a-step9", "text": "finder: ok a269784. walk.go:9 is rebutted: the volume link is walked by its caller", "path": "/notes/answer.md"}
    directory = job(tmp_path, since=OLD, prior=prior, history=[prior | {"path": ""}, answer])
    shell = Fake(tmp_path, [reply()], own={(BASE, HEAD): [OWN], (BASE, OLD): [OLD_OWN]}, resolve={OLD: OLD}, ranges={EARLIER: pairs("=", "!")})
    finder.main([str(directory)], shell)
    question = (directory / "question-1.md").read_text()
    assert "Run nothing that builds" in question and "Change no file, branch, or ref" in question
    assert f"origin {HEAD} {BASE}`" in question
    assert f"`git range-diff {OLD_OWN}^..{OLD} {OWN}^..{HEAD}`" in question and f"the range-diff shows for its pair ({OWN[:11]}); each new commit whole with `git show <sha>` (none)" in question and f"`git diff {OWN}^ {HEAD}`" in question
    assert "- cci #149302 from p8a-step9: finder: major a269784 1 open: walk.go:9 skips the volume link\n" in question
    assert "- cci #149310 from p8a-step9: finder: ok a269784. walk.go:9 is rebutted: the volume link is walked by its caller (whole record: /notes/answer.md)\n" in question
    assert "never raise a closed finding again" in question
    assert "/plans/durable/plan/plan.md" in question and finder.SCOPED_STEP in question
    assert "rows.md" not in question and not (directory / "rows.md").exists()
    assert not [argv for argv, _ in shell.calls if argv[2:3] == ["slice"]]
    [(ask, cwd)] = shell.asks()
    assert ask[:6] == ["codex-ask", "-m", "sol", "--incident", "--schema", "findings"] and ask[-1] == str(directory / "question-1.md") and cwd == tmp_path / "checkout"
    assert shell.posted[0]["--text"] == f"finder: ok {HEAD} sol finder over delta {OLD[:7]}..{HEAD[:7]}: no majors"


def test_a_reply_that_miscounts_the_rows_is_asked_once_more_and_two_such_replies_post_nothing(tmp_path):
    """Root #238844: the reply names a row only in a missing finding and ends `swept` with one count, so a big slice never exhausts the model's output; a count that is not the slice's row count is asked again."""
    miscounted = reply(swept=REASONS + "rows checked: 1")
    directory = job(tmp_path)
    shell = Fake(tmp_path, [miscounted, reply()])
    finder.main([str(directory)], shell)
    assert [ask[-1] for ask, _ in shell.asks()] == [str(directory / "question-1.md"), str(directory / "question-2.md")]
    assert "`rows checked: 1` where the rows file holds 2 rows" in (directory / "question-2.md").read_text()
    assert "end `swept` with `rows checked: 2`" in (directory / "question-2.md").read_text()
    assert len(shell.posted) == 1

    again = job(tmp_path / "again")
    shell = Fake(tmp_path, [miscounted, miscounted])
    with pytest.raises(finder.JobFailed):
        finder.main([str(again)], shell)
    assert not shell.posted
    assert result(again) == {"error": "JobFailed: two replies gave `rows checked: 1` where the rows file holds 2 rows"}


def test_a_slice_over_the_big_slice_bound_runs_on_sol_whatever_model_the_job_names(tmp_path):
    """Root #238844: luna ran out of output tokens on #46111's 290-row slice; slices over 150 rows route to sol, and luna keeps the rest."""
    rows = "".join(f"### rule/{index:07x} | scope | in-force | said 1 | 2026-10-22\n> row {index}\n\n" for index in range(finder.BIG_SLICE + 1))
    directory = job(tmp_path, model="luna")
    shell = Fake(tmp_path, [reply(swept=REASONS + f"rows checked: {finder.BIG_SLICE + 1}")], rows=rows)
    finder.main([str(directory)], shell)
    [(ask, _)] = shell.asks()
    assert ask[:3] == ["codex-ask", "-m", "sol"]
    assert len(shell.posted) == 1

    small = job(tmp_path / "small", model="luna")
    shell = Fake(tmp_path, [reply()])
    finder.main([str(small)], shell)
    [(ask, _)] = shell.asks()
    assert ask[:3] == ["codex-ask", "-m", "luna"]


def test_a_reply_with_no_reasoning_is_asked_once_more_and_two_such_replies_post_nothing(tmp_path):
    bare = reply(swept="ok; rows checked: 2; tests: " + "; ".join(f"t{index} <- plan.md:{index}" for index in range(8)))
    directory = job(tmp_path)
    shell = Fake(tmp_path, [bare, reply()])
    finder.main([str(directory)], shell)
    assert finder.REASONLESS in (directory / "question-2.md").read_text()
    assert len(shell.posted) == 1

    again = job(tmp_path / "again")
    shell = Fake(tmp_path, [bare, bare])
    with pytest.raises(finder.JobFailed):
        finder.main([str(again)], shell)
    assert not shell.posted
    assert result(again) == {"error": f"JobFailed: two replies gave {finder.REASONLESS}"}


def test_a_head_the_pr_no_longer_carries_runs_no_finder(tmp_path):
    directory = job(tmp_path)
    shell = Fake(tmp_path, [], heads=(NEWER,))
    finder.main([str(directory)], shell)
    assert not shell.asks() and not shell.posted
    assert result(directory) == {"skipped": f"{REPO}#43299 moved to {NEWER[:12]}"}


def test_a_head_superseded_while_codex_read_it_posts_nothing(tmp_path):
    directory = job(tmp_path)
    shell = Fake(tmp_path, [reply(finding("major"))], heads=(HEAD, NEWER))
    finder.main([str(directory)], shell)
    assert len(shell.asks()) == 1 and not shell.posted
    assert result(directory) == {"skipped": f"{REPO}#43299 moved to {NEWER[:12]} while codex read {HEAD[:12]}"}


def test_a_job_the_runner_cancelled_for_a_newer_head_asks_codex_nothing(tmp_path):
    directory = job(tmp_path)
    (directory / finder.CANCELLED).write_text(f"{NEWER}\n")
    shell = Fake(tmp_path, [])
    assert finder.main([str(directory)], shell) == 0
    assert not shell.asks() and not shell.posted
    assert result(directory) == {"skipped": f"{REPO}#43299 at {HEAD[:12]} was superseded by {NEWER[:12]} before codex read it"}


def test_a_failed_pr_read_is_retried_on_a_short_backoff_and_fails_the_job_once_every_pause_is_spent(tmp_path, monkeypatch):
    slept = []
    monkeypatch.setattr(finder.time, "sleep", slept.append)
    directory = job(tmp_path)
    shell = Fake(tmp_path, [reply()], failures=2)
    finder.main([str(directory)], shell)
    assert slept == [2, 5] and len(shell.posted) == 1

    again = job(tmp_path / "again")
    with pytest.raises(subprocess.CalledProcessError):
        finder.main([str(again)], Fake(tmp_path, [], failures=len(finder.RETRY_BACKOFF) + 1))
    assert "TLS handshake timeout" in result(again)["error"]


def test_a_fetch_git_killed_on_another_lanes_zero_worktree_ref_is_retried_on_a_short_backoff(tmp_path, monkeypatch):
    slept = []
    monkeypatch.setattr(finder.time, "sleep", slept.append)
    directory = job(tmp_path)
    shell = Fake(tmp_path, [reply()], races=2)
    finder.main([str(directory)], shell)
    assert slept == [2, 5] and len(shell.posted) == 1
    assert [argv for argv, _ in shell.calls if argv[:2] == ["git", "fetch"]] == [["git", "fetch", "--no-write-fetch-head", "-q", "origin", HEAD, BASE, BASE]] * 3

    again = job(tmp_path / "again")
    with pytest.raises(subprocess.CalledProcessError):
        finder.main([str(again)], Fake(tmp_path, [], races=len(finder.RETRY_BACKOFF) + 1))
    assert "bad object 0000000000000000000000000000000000000000" in result(again)["error"]


def test_a_whole_pass_reads_both_tips_in_one_ls_remote_and_runs_the_rulings_slice_and_plan_while_the_head_fetches(tmp_path):
    started = {"slice": threading.Event(), "plan": threading.Event()}
    overlapped = []

    class Slow(Fake):
        def run(self, argv: list[str], cwd: Path | None = None, timeout: int = finder.READ_SECONDS) -> str:
            if argv[:2] == [sys.executable, str(finder.SCRIPTS / "rulings.py")]:
                started[argv[2]].set()
            return super().run(argv, cwd, timeout)

        def git(self, argv: list[str]) -> str:
            if argv[0] == "fetch":
                overlapped.append(all(event.wait(5) for event in started.values()))
            return super().git(argv)

    directory = job(tmp_path)
    shell = Slow(tmp_path, [reply()])
    finder.main([str(directory)], shell)
    assert overlapped == [True] and len(shell.posted) == 1
    assert [argv for argv, _ in shell.calls if argv[:2] == ["git", "ls-remote"]] == [["git", "ls-remote", "origin", "HEAD", "refs/heads/dev"]]


@pytest.mark.parametrize(
    ("graded", "settled", "carried"),
    [
        ("finder: major a269784 1 open: walk.go:9 skips the volume link", {"149302": 149400}, "finder: ok {head} carried from #149302, settled by root #149400"),
        ("finder: major a269784 carried from #149100", {"149100": 149150}, "finder: ok {head} carried from #149302, settled by root #149150"),
        ("finder: major a269784 1 open: walk.go:9 skips the volume link", {"149100": 149150}, "finder: major {head} carried from #149302"),
    ],
    ids=["settled", "settled-upchain", "unsettled"],
)
def test_a_carried_major_root_settled_carries_as_ok_citing_the_root_decide(tmp_path, graded, settled, carried):
    prior = {"seq": 149302, "lane": "finder-desk", "head": "a269784", "text": graded}
    history = [{"seq": 149100, "lane": "finder-desk", "text": "finder: major 9f00e11 1 open: walk.go:9 skips the volume link", "path": ""}, prior | {"path": ""}]
    directory = job(tmp_path, prior=prior, history=history, settled=settled)
    shell = Fake(tmp_path, [], own={(BASE, HEAD): [OWN], (BASE, OLD): [OLD_OWN]}, resolve={"a269784": OLD}, ranges={EARLIER: pairs("=", "=")})
    finder.main([str(directory)], shell)
    [post] = shell.posted
    assert not shell.asks() and post["--text"] == carried.format(head=HEAD[:7])


@pytest.mark.parametrize("graded", ["finder: ok a269784 sol finder over dev...a269784: no majors", "finder: major a269784 1 open: walk.go:9 skips the volume link"])
def test_a_replay_onto_a_moved_parent_whose_every_own_commit_pairs_equal_posts_the_graded_heads_verdict_as_carried(tmp_path, graded):
    prior = {"seq": 149302, "lane": "finder-desk", "head": "a269784", "text": graded}
    directory = job(tmp_path, prior=prior)
    shell = Fake(tmp_path, [], own={(BASE, HEAD): [OWN], (BASE, OLD): [OLD_OWN]}, resolve={"a269784": OLD}, ranges={EARLIER: pairs("=", "=")})
    finder.main([str(directory)], shell)
    verdict = finder_grade(graded)
    [post] = shell.posted
    assert not shell.asks() and post["--text"] == f"finder: {verdict} {HEAD[:7]} carried from #149302"
    assert post["--kind"] == "review" and post["--pr"] == f"{REPO}#43299" and post["--to"] == "p8a-step9,desk-runner" and post["--id"] == f"finder:{REPO}#43299:{HEAD}"
    assert finder_grade(post["--text"]) == verdict and graded_head(post["--text"]) == HEAD[:7]
    assert graded in (directory / "record.md").read_text()
    assert result(directory) == {"verdict": verdict, "record": post["--text"], "posted": "#150001"}


def test_a_head_whose_only_finder_review_names_no_head_reads_since_the_head_its_head_record_replaced(tmp_path):
    directory = job(tmp_path, previous=OLD)
    shell = Fake(tmp_path, [], own={(BASE, HEAD): [OWN], (BASE, OLD): [OLD_OWN]}, resolve={OLD: OLD}, ranges={EARLIER: pairs("=", "=")})
    finder.main([str(directory)], shell)
    assert not shell.asks() and not shell.posted
    assert result(directory) == {"skipped": f"{REPO}#43299 at {HEAD[:12]} replays every own commit of {OLD[:12]}, patch for patch or dropping only hunks dev carries", "ungraded": True}
    assert finder.previous({"text": f"HEAD #43299 {HEAD} (was {OLD}): restack onto #42751"}, REPO, "43299") == OLD and finder.previous({"text": f"HEAD #43299 {HEAD}"}, REPO, "43299") == ""


def test_a_fixed_head_named_short_by_the_last_finder_record_reads_only_its_new_commits(tmp_path):
    prior = {"seq": 149302, "lane": "p8a-step9", "head": "a269784", "text": "finder: ok a269784"}
    directory = job(tmp_path, prior=prior)
    shell = Fake(tmp_path, [reply()], own={(BASE, HEAD): [OWN], (BASE, OLD): [OLD_OWN]}, resolve={"a269784": OLD}, ranges={EARLIER: pairs("=", ">")})
    finder.main([str(directory)], shell)
    question = (directory / "question-1.md").read_text()
    assert f"for its pair (none); each new commit whole with `git show <sha>` ({OWN[:11]})" in question and finder.SCOPED_STEP in question and not (directory / "rows.md").exists()
    assert shell.posted[0]["--text"] == f"finder: ok {HEAD} sol finder over delta {OLD[:7]}..{HEAD[:7]}: no majors"


def test_a_merged_pr_whose_base_branch_the_remote_deleted_still_gets_its_pass_over_the_trunk(tmp_path):
    """#219204: a fidelity pass on #45398, merged into its stack base and that base since deleted, died on KeyError: 'refs/heads/<base>'."""
    directory = job(tmp_path)
    shell = Fake(tmp_path, [reply(finding("minor"))], "MERGED", base_gone=True)
    assert finder.main([str(directory)], shell) == 0
    [post] = shell.posted
    assert finder_grade(post["--text"]) == FINDER_PASS


def test_a_closed_pr_runs_no_finder_and_posts_nothing(tmp_path):
    directory = job(tmp_path)
    shell = Fake(tmp_path, [], "CLOSED")
    finder.main([str(directory)], shell)
    assert not shell.asks() and not shell.posted
    assert result(directory) == {"skipped": f"{REPO}#43299 is closed"}


def test_a_head_record_names_its_commit_field_else_its_first_full_sha_no_was_precedes():
    named = {"refs": {"prs": [f"{REPO}#43299"]}}
    assert finder.heads(named | {"text": f"HEAD #43299 {HEAD} (was {OLD})", "fields": {"commit": HEAD}}, REPO) == [("43299", HEAD)]
    assert finder.heads({"refs": {"prs": [43299]}, "text": f"#43299 HEAD (was {OLD}), now {HEAD}", "fields": {}}, REPO) == [("43299", HEAD)]
    assert finder.heads(named | {"text": "#43299 HEAD 7414944e30bf (was a269784e4900)"}, REPO) == [("43299", "7414944e30bf")]
    assert finder.heads(named | {"text": "#43299 HEAD restacked onto 4329900 (was a269784e4900)"}, REPO) == []
    assert finder.heads({"refs": {"prs": ["yasyf/cc-skills#836"]}, "text": f"dashboard-tap-fast {HEAD}"}, REPO) == []


@pytest.mark.parametrize(
    ("refs", "text", "expected"),
    [
        ({}, f"dar-p5-api-cred-503 {HEAD} #43832 (was 814e3cd): test-only review fix", [("43832", HEAD)]),
        ({"refs": {}}, f"finder pass wanted at {HEAD} pr:{REPO}#43923 after #43900 landed", [("43923", HEAD)]),
        ({}, f"{HEAD} yasyf/cc-skills#836 then #43929", [("43929", HEAD)]),
        ({"refs": {"prs": ["yasyf/cc-skills#836"]}}, f"{HEAD} #43929", []),
        ({}, f"HEAD {HEAD} restacked onto dev", []),
    ],
)
def test_a_head_record_without_pr_refs_takes_the_pr_its_text_names(refs, text, expected):
    assert finder.heads(refs | {"text": text}, REPO) == expected


def test_since_names_the_full_head_the_newest_finder_record_at_another_head_read():
    older = {"seq": 10, "lane": "p8a-step9", "head": "cfe6544", "text": "finder: ok cfe6544"}
    newest = {"seq": 12, "lane": "p8a-step9-2", "head": "a269784", "text": "finder: major a269784 1 open: walk.go:9"}
    current = {"seq": 14, "lane": "p8a-step9", "head": HEAD[:7], "text": f"finder: ok {HEAD[:7]}"}
    records = [{"kind": "head", "text": f"HEAD #43299 {OLD} (was cfe6544f8d03)", "fields": {}}]
    assert finder.since([older, newest, current], records, HEAD, REPO) == (OLD, {"seq": 12, "lane": "p8a-step9-2", "head": "a269784", "text": "finder: major a269784 1 open: walk.go:9"})
    assert finder.since([newest], [], HEAD, REPO)[0] == ""
    assert finder.since([current], records, HEAD, REPO) == ("", None)


def test_a_graded_head_origin_no_longer_serves_reads_the_whole_change(tmp_path):
    prior = {"seq": 149302, "lane": "p8a-step9", "head": "a269784", "text": "finder: ok a269784"}
    directory = job(tmp_path, since=OLD, prior=prior)
    shell = Fake(tmp_path, [reply()], gone=(OLD,))
    finder.main([str(directory)], shell)
    question = (directory / "question-1.md").read_text()
    assert f"This pass reads the PR's whole change: `git diff {OWN}^ {HEAD}`." in question and (directory / "rows.md").exists()
    assert shell.posted[0]["--text"] == f"finder: ok {HEAD} sol finder over whole {OWN[:7]}^..{HEAD[:7]}: no majors"


def test_a_graded_head_with_no_own_commit_past_the_base_reads_the_whole_change_with_no_range_diff(tmp_path):
    prior = {"seq": 149302, "lane": "p8a-step9", "head": "a269784", "text": "finder: ok a269784"}
    directory = job(tmp_path, since=OLD, prior=prior)
    shell = Fake(tmp_path, [reply()], own={(BASE, HEAD): [OWN]}, resolve={OLD: OLD})
    finder.main([str(directory)], shell)
    question = (directory / "question-1.md").read_text()
    assert f"This pass reads the PR's whole change: `git diff {OWN}^ {HEAD}`." in question and (directory / "rows.md").exists()
    assert not [argv for argv, _ in shell.calls if argv[1:2] == ["range-diff"]]
    assert shell.posted[0]["--text"] == f"finder: ok {HEAD} sol finder over whole {OWN[:7]}^..{HEAD[:7]}: no majors"


def test_an_amended_commit_that_only_drops_a_hunk_the_base_now_carries_is_a_carry(tmp_path):
    prior = {"seq": 149302, "lane": "p8a-step9", "head": "a269784", "text": "finder: ok a269784"}
    directory = job(tmp_path, since=OLD, prior=prior)
    shows = {OLD_OWN[:11]: patch("-poll()", "+stream()", "+azurePrefix()"), OWN[:11]: patch("-poll()", "+stream()"), f"{BASE}:go/follow.go": "func f() {\n\tazurePrefix()\n}\n"}
    shell = Fake(tmp_path, [], own={(BASE, HEAD): [OWN], (BASE, OLD): [OLD_OWN]}, resolve={OLD: OLD}, ranges={EARLIER: pairs("=", "!")}, shows=shows)
    finder.main([str(directory)], shell)
    assert not shell.asks() and [post["--text"] for post in shell.posted] == [f"finder: ok {HEAD[:7]} carried from #149302"]


def test_an_amended_commit_that_drops_a_hunk_the_base_lacks_is_read(tmp_path):
    prior = {"seq": 149302, "lane": "p8a-step9", "head": "a269784", "text": "finder: ok a269784"}
    directory = job(tmp_path, since=OLD, prior=prior)
    shows = {OLD_OWN[:11]: patch("-poll()", "+stream()", "+azurePrefix()"), OWN[:11]: patch("-poll()", "+stream()"), f"{BASE}:go/follow.go": "func f() {\n\tpoll()\n}\n"}
    shell = Fake(tmp_path, [reply()], own={(BASE, HEAD): [OWN], (BASE, OLD): [OLD_OWN]}, resolve={OLD: OLD}, ranges={EARLIER: pairs("=", "!")}, shows=shows)
    finder.main([str(directory)], shell)
    assert f"the range-diff shows for its pair ({OWN[:11]})" in (directory / "question-1.md").read_text()
    assert len(shell.posted) == 1


def test_a_replay_that_drops_an_earlier_commit_the_base_lacks_reads_its_removal_and_carries_nothing(tmp_path):
    prior = {"seq": 149302, "lane": "p8a-step9", "head": "a269784", "text": "finder: ok a269784"}
    directory = job(tmp_path, since=OLD, prior=prior)
    shell = Fake(tmp_path, [reply()], own={(BASE, HEAD): [OWN], (BASE, OLD): [OLD_OWN]}, resolve={OLD: OLD}, ranges={EARLIER: pairs("=", "=")}, shows={f"{BASE}:api/parent.go": "pending()\n"})
    finder.main([str(directory)], shell)
    assert f"the removal of each dropped commit the base lacks, read with `git show <sha>` ({PARENT[:11]})" in (directory / "question-1.md").read_text()
    assert shell.posted[0]["--text"] == f"finder: ok {HEAD} sol finder over delta {OLD[:7]}..{HEAD[:7]}: no majors"


def test_a_replay_whose_dropped_commit_moved_into_the_base_amended_carries_the_graded_verdict(tmp_path):
    """drive-watchdog-2 #212163: #45396 at c74435c (14 equal and 2 context-only pairs) and #45533 at 427be00 waited hours for a full pass, because the commits each dropped had moved into the parent PR and been amended there, so the base no longer carried their lines word for word."""
    prior = {"seq": 149302, "lane": "p8a-step9", "head": "a269784", "text": "finder: ok a269784"}
    directory = job(tmp_path, since=OLD, prior=prior)
    shell = Fake(tmp_path, [], own={(BASE, HEAD): [OWN], (BASE, OLD): [OLD_OWN]}, resolve={OLD: OLD}, ranges={EARLIER: pairs("=", "=")}, shows={f"{BASE}:api/parent.go": "pending()\n"}, subjects=("api: the parent's commit before its restack",))
    finder.main([str(directory)], shell)
    assert not shell.asks() and [post["--text"] for post in shell.posted] == [f"finder: ok {HEAD[:7]} carried from #149302"]


def test_a_failed_command_reports_the_end_of_its_traceback_where_the_exception_is_named():
    trace = "Traceback (most recent call last):\n" + '  File "rulings.py", line 2832, in <module>\n    raise SystemExit(main())\n' * 20 + "subprocess.TimeoutExpired: ccn ledger show timed out\n"
    error = subprocess.CalledProcessError(1, [sys.executable, "rulings.py", "plan", "--repo", "/repo"], stderr=trace)

    reported = finder.failure(error)

    assert reported.endswith("subprocess.TimeoutExpired: ccn ledger show timed out")
    assert "rulings.py plan --repo exited 1: " in reported


def test_a_multi_head_record_names_each_prs_own_head_and_the_head_it_replaced():
    record = {
        "refs": {"prs": [f"{REPO}#43299", 43300, "yasyf/cc-skills#836"]},
        "fields": {"attrs": {f"head:{REPO}#43299": f"{OLD}..{HEAD} restack", "head:43300": NEWER, "head:yasyf/cc-skills#836": MOVED, "actor": "lane"}},
        "text": f"HEAD 3 PRs: #43299 {HEAD[:7]}, #43300 {NEWER[:7]}, #836 {MOVED[:7]}: restack onto dev",
    }
    assert finder.heads(record, REPO) == [("43299", HEAD), ("43300", NEWER)]
    assert (finder.previous(record, REPO, "43299"), finder.previous(record, REPO, "43300")) == (OLD, "")


def test_every_command_a_pass_runs_logs_its_duration(capsys):
    """remote-desks-5 #253850: two passes on separate Sprites both waited 16 minutes before codex, and job.log was empty, so nothing named the stage that waited."""
    finder.Shell().run([sys.executable, "-c", "import time; time.sleep(0.2)"])
    with pytest.raises(subprocess.CalledProcessError):
        finder.Shell().run(["false"])
    lines = capsys.readouterr().err.splitlines()
    assert [line.split(" took ")[0] for line in lines] == [f"finder: {Path(sys.executable).name} -c import time; time.sleep(0.2)", "finder: false"]
    assert float(lines[0].split(" took ")[1].rstrip("s")) >= 0.2


def test_a_reply_cut_off_at_the_models_output_cap_is_asked_once_more_on_sol(tmp_path):
    """desk-runner #257303: luna ran out of output tokens on #46731 and the pass died; 35 passes had failed the same way."""
    capped = subprocess.CalledProcessError(1, ["codex-ask"], output='{"type":"error","message":"Incomplete response returned, reason: max_output_tokens"}')
    directory = job(tmp_path, model="luna")
    shell = Fake(tmp_path, [capped, reply()])
    finder.main([str(directory)], shell)
    assert [(ask[2], ask[ask.index("-s") + 1]) for ask, _ in shell.asks()] == [("luna", str(directory / "codex-1")), ("sol", str(directory / "codex-1-sol"))]
    assert len(shell.posted) == 1

    other = job(tmp_path / "other", model="luna")
    shell = Fake(tmp_path, [subprocess.CalledProcessError(1, ["codex-ask"], output="codex: rate limited")])
    with pytest.raises(subprocess.CalledProcessError):
        finder.main([str(other)], shell)
    assert [ask[2] for ask, _ in shell.asks()] == ["luna"]
