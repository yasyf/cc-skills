"""finder.py against a fake gh, git, rulings.py, codex-ask and cci: the record a pass posts, the range it reads, and when it asks again or posts nothing."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import finder
import pytest
from conftest import gateway
from grades import FINDER_FAIL, FINDER_PASS, finder_grade, graded_head

REPO = "Forge-AI/monorepo"
HEAD = "7414944e30bfec89dee45328414dbc1016e35afa"
OLD = "a269784e49009c5aa92b2341e08d6945f7ff8e95"
BASE = "d011466aa1b2c3d4e5f60718293a4b5c6d7e8f90"
STALE_BASE = "c3a3afc0307096aabf6d24513b2436d1bb23bbe8"
OWN = "e1f2a3b4c5d6e7f8091a2b3c4d5e6f708192a3b4"
OLD_OWN = "f0e1d2c3b4a5968778695a4b3c2d1e0f9a8b7c6d"
NEWER = "5be7c0de5be7c0de5be7c0de5be7c0de5be7c0de"
ROWS = (
    f"Required rows for {REPO}#43299 at {HEAD}\n\n## Every global, pending and touched row (2)\n\n"
    "### rule/949e6be | scope | in-force | said 1 | 2026-10-22\n> it should be a ssql replica that is live streaming, not polled\n\n"
    "### prompt/1026-105052 | reserved | in-force | said 1 | 2026-10-26\n> no credential on disk\nslice-hash: 0123456789ab\n"
)
REASONS = "The resubscribe hunks keep the fetched position across a cut, and every caller passes it through unchanged. "
SWEPT = REASONS + "rows met: rule/949e6be; n/a: prompt/1026-105052; pending: "
PARENT = "0b0e1f2a3c4d5e6f708192a3b4c5d6e7f8091a2b"
EARLIER = (f"{OLD_OWN}^..{OLD}", f"{OWN}^..{HEAD}")


def pairs(*marks: str) -> str:
    """`git range-diff --no-patch` output: one parent commit only the earlier head carried, then one line per own commit."""
    lines = [f" 1:  {PARENT[:11]} <  -:  ----------- api: the parent's commit before its restack"]
    lines += [f" {index}:  {OLD_OWN[:11]} {mark}  {index - 1}:  {OWN[:11]} sand: the head's own commit" for index, mark in enumerate(marks, 2)]
    return "\n".join(lines) + "\n"



class Fake:
    def __init__(self, tmp_path: Path, replies: list[dict], state: str = "OPEN", heads: tuple[str, ...] = (HEAD,), own: dict | None = None, resolve: dict | None = None, failures: int = 0, gone: tuple[str, ...] = (), ranges: dict | None = None):
        self.tmp_path = tmp_path
        self.replies = list(replies)
        self.state = state
        self.heads = list(heads)
        self.own = {(BASE, HEAD): [OWN]} if own is None else own
        self.resolve = resolve or {}
        self.failures = failures
        self.gone = gone
        self.ranges = ranges or {}
        self.calls: list[tuple[list[str], Path | None]] = []
        self.posted: list[dict] = []

    def run(self, argv: list[str], cwd: Path | None = None, timeout: int = finder.READ_SECONDS) -> str:
        argv = gateway(argv)
        self.calls.append((argv, cwd))
        if argv[:3] == ["gh", "pr", "view"]:
            if self.failures:
                self.failures -= 1
                raise subprocess.CalledProcessError(1, argv, stderr='Post "https://api.github.com/graphql": net/http: TLS handshake timeout')
            head = self.heads.pop(0) if len(self.heads) > 1 else self.heads[0]
            return json.dumps({"state": self.state, "baseRefName": "dev", "baseRefOid": STALE_BASE, "headRefOid": head, "body": "## Summary\nA cut signal resubscribes from the position it fetched."})
        if argv[0] == "git":
            return self.git(argv[1:])
        if argv[:2] == [sys.executable, str(finder.SCRIPTS / "rulings.py")]:
            return ROWS if argv[2] == "slice" else "/plans/durable/plan/plan.md\n"
        if argv[0] == "codex-ask":
            reply = self.tmp_path / f"reply-{len(self.calls)}.json"
            reply.write_text(json.dumps(self.replies.pop(0)))
            return f"REPLY_FILE: {reply}\nLOG_FILE: {reply}.log\nAWAIT: codex-ask --await {argv[argv.index('-s') + 1]}\n"
        if argv[:2] == ["cci", "post"]:
            self.posted.append(dict(zip(argv[2::2], argv[3::2], strict=True)))
            return "#150001\n"
        raise AssertionError(f"unexpected call {argv}")

    def git(self, argv: list[str]) -> str:
        if argv[0] == "ls-remote":
            return f"{BASE}\trefs/heads/dev\n"
        if argv[0] == "fetch":
            if any(sha in self.gone for sha in argv):
                raise subprocess.CalledProcessError(128, argv, stderr="fatal: remote error: upload-pack: not our ref")
            return ""
        if argv[0] == "range-diff":
            assert argv[1:3] == ["--no-patch", "--no-color"]
            return self.ranges[tuple(argv[3:])]
        if argv[0] == "rev-parse":
            if (full := self.resolve.get(argv[-1].removesuffix("^{commit}"))) is None:
                raise subprocess.CalledProcessError(1, argv)
            return f"{full}\n"
        if argv[0] == "rev-list":
            return "".join(f"{sha}\n" for sha in self.own.get(tuple(argv[-1].split("...")), []))
        raise AssertionError(f"unexpected git {argv}")

    def asks(self) -> list[tuple[list[str], Path | None]]:
        return [(argv, cwd) for argv, cwd in self.calls if argv[0] == "codex-ask"]


def job(tmp_path: Path, **changes) -> Path:
    directory = tmp_path / "finder-desk" / f"43299-{HEAD[:12]}"
    directory.mkdir(parents=True)
    spec = {"drive": "d1", "repo": REPO, "pr": "43299", "head": HEAD, "since": "", "prior": None, "previous": "", "lane": "p8a-step9", "to": ["root"], "checkout": str(tmp_path / "checkout"), "model": "sol"}
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
    assert post["--to"] == "p8a-step9,root" and post["--id"] == f"finder:{REPO}#43299:{HEAD}" and post["--path"] == str(directory / "record.md")
    assert post["--text"] == f"finder: ok {HEAD} sol finder over dev...{HEAD[:7]}: no majors"
    assert finder_grade(post["--text"]) == FINDER_PASS and graded_head(post["--text"]) == HEAD
    question = (directory / "question-1.md").read_text()
    assert f"`git diff {OWN}^ {HEAD}`" in question and f"on base dev ({BASE})" in question and STALE_BASE not in question
    assert str(directory / "rows.md") in question and "Open `swept` with your reasoning" in question
    assert result(directory) == {"verdict": "ok", "record": post["--text"], "posted": "#150001"}


def test_a_head_whose_parent_was_restacked_reads_only_its_own_commits_against_the_base_branchs_current_tip(tmp_path):
    directory = job(tmp_path)
    shell = Fake(tmp_path, [reply()], own={(BASE, HEAD): [OWN, NEWER]})
    finder.main([str(directory)], shell)
    [rev_list] = [argv for argv, _ in shell.calls if argv[:2] == ["git", "rev-list"]]
    assert rev_list == ["git", "rev-list", "--cherry-pick", "--right-only", "--no-merges", "--reverse", f"{BASE}...{HEAD}"]
    question = (directory / "question-1.md").read_text()
    assert f"`git diff {OWN}^ {HEAD}`" in question and f"{BASE}...{HEAD}" not in question and f"{STALE_BASE}...{HEAD}" not in question


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
    assert text == f"finder: major {HEAD} 2 open: rust/crates/sandsql-journal/src/replica.rs:2947 registered mounts never record a resubscribe position; go/poll.go:9 row rule/949e6be missing: a mount polls every 5 s"
    assert finder_grade(text) == FINDER_FAIL and graded_head(text) == HEAD
    assert "a name reads oddly" not in text and "a name reads oddly" in (directory / "record.md").read_text()


def test_majors_past_ccis_limit_post_a_clipped_line_that_still_reads_major_with_the_whole_reply_on_its_path(tmp_path):
    directory = job(tmp_path)
    shell = Fake(tmp_path, [reply(*(finding("major", f"go/sand/file{index}.go:{index}", "the follower keeps a stale incarnation after a swap " * 3) for index in range(4)))])
    finder.main([str(directory)], shell)
    text = shell.posted[0]["--text"]
    assert len(text) == finder.CCI_TEXT and text.endswith("…") and finder_grade(text) == FINDER_FAIL
    assert (directory / "record.md").read_text().count("go/sand/file3.go:3") == 2


def test_a_pass_after_an_earlier_finder_reads_since_that_head_and_checks_its_majors(tmp_path):
    prior = {"seq": 149302, "lane": "p8a-step9", "head": "a269784", "text": "finder: major a269784 1 open: walk.go:9 skips the volume link"}
    directory = job(tmp_path, since=OLD, prior=prior)
    shell = Fake(tmp_path, [reply()], own={(BASE, HEAD): [OWN], (BASE, OLD): [OLD_OWN]}, resolve={OLD: OLD}, ranges={EARLIER: pairs("=", "!")})
    finder.main([str(directory)], shell)
    question = (directory / "question-1.md").read_text()
    assert "Run nothing that builds" in question and "Change no file, branch, or ref" in question
    assert f"origin {HEAD} {BASE}`" in question
    assert f"`git range-diff {OLD_OWN}^..{OLD} {OWN}^..{HEAD}`" in question and f"the range-diff shows for its pair ({OWN[:11]}); each new commit whole with `git show <sha>` (none)" in question and f"`git diff {OWN}^ {HEAD}`" in question
    assert "cci #149302 from p8a-step9 at a269784" in question and "walk.go:9 skips the volume link" in question
    assert "/plans/durable/plan/plan.md" in question and finder.SCOPED_STEP in question
    assert "rows.md" not in question and not (directory / "rows.md").exists()
    assert not [argv for argv, _ in shell.calls if argv[2:3] == ["slice"]]
    [(ask, cwd)] = shell.asks()
    assert ask[:5] == ["codex-ask", "-m", "sol", "--schema", "findings"] and ask[-1] == str(directory / "question-1.md") and cwd == tmp_path / "checkout"
    assert shell.posted[0]["--text"] == f"finder: ok {HEAD} sol finder over {OLD[:7]}..{HEAD[:7]}: no majors"


def test_a_reply_that_drops_a_row_key_is_asked_once_more_and_two_such_replies_post_nothing(tmp_path):
    dropped = reply(swept=REASONS + "rows met: rule/949e6be")
    directory = job(tmp_path)
    shell = Fake(tmp_path, [dropped, reply()])
    finder.main([str(directory)], shell)
    assert [ask[-1] for ask, _ in shell.asks()] == [str(directory / "question-1.md"), str(directory / "question-2.md")]
    assert "no verdict for prompt/1026-105052" in (directory / "question-2.md").read_text()
    assert len(shell.posted) == 1

    again = job(tmp_path / "again")
    shell = Fake(tmp_path, [dropped, dropped])
    with pytest.raises(finder.JobFailed):
        finder.main([str(again)], shell)
    assert not shell.posted
    assert result(again) == {"error": "JobFailed: two replies gave no verdict for prompt/1026-105052"}


def test_a_reply_with_no_reasoning_is_asked_once_more_and_two_such_replies_post_nothing(tmp_path):
    bare = reply(swept="ok; rows met: rule/949e6be; n/a: prompt/1026-105052; pending: ; tests: " + "; ".join(f"t{index} <- plan.md:{index}" for index in range(8)))
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


def test_a_failed_pr_read_is_retried_once_after_a_pause(tmp_path, monkeypatch):
    slept = []
    monkeypatch.setattr(finder.time, "sleep", slept.append)
    directory = job(tmp_path)
    shell = Fake(tmp_path, [reply()], failures=1)
    finder.main([str(directory)], shell)
    assert slept == [finder.RETRY_SECONDS] and len(shell.posted) == 1

    again = job(tmp_path / "again")
    with pytest.raises(subprocess.CalledProcessError):
        finder.main([str(again)], Fake(tmp_path, [], failures=2))
    assert "TLS handshake timeout" in result(again)["error"]


def test_a_replay_onto_a_moved_parent_whose_every_own_commit_pairs_equal_posts_nothing(tmp_path):
    prior = {"seq": 149302, "lane": "p8a-step9", "head": "a269784", "text": "finder: ok a269784"}
    directory = job(tmp_path, prior=prior)
    shell = Fake(tmp_path, [], own={(BASE, HEAD): [OWN], (BASE, OLD): [OLD_OWN]}, resolve={"a269784": OLD}, ranges={EARLIER: pairs("=", "=")})
    finder.main([str(directory)], shell)
    assert not shell.asks() and not shell.posted
    assert result(directory) == {"skipped": f"{REPO}#43299 at {HEAD[:12]} replays every own commit of {OLD[:12]} patch for patch"}


def test_a_head_whose_only_finder_review_names_no_head_reads_since_the_head_its_head_record_replaced(tmp_path):
    directory = job(tmp_path, previous=OLD)
    shell = Fake(tmp_path, [], own={(BASE, HEAD): [OWN], (BASE, OLD): [OLD_OWN]}, resolve={OLD: OLD}, ranges={EARLIER: pairs("=", "=")})
    finder.main([str(directory)], shell)
    assert not shell.asks() and not shell.posted
    assert result(directory) == {"skipped": f"{REPO}#43299 at {HEAD[:12]} replays every own commit of {OLD[:12]} patch for patch"}
    assert finder.previous({"text": f"HEAD #43299 {HEAD} (was {OLD}): restack onto #42751"}) == OLD and finder.previous({"text": f"HEAD #43299 {HEAD}"}) == ""


def test_a_fixed_head_named_short_by_the_last_finder_record_reads_only_its_new_commits(tmp_path):
    prior = {"seq": 149302, "lane": "p8a-step9", "head": "a269784", "text": "finder: ok a269784"}
    directory = job(tmp_path, prior=prior)
    shell = Fake(tmp_path, [reply()], own={(BASE, HEAD): [OWN], (BASE, OLD): [OLD_OWN]}, resolve={"a269784": OLD}, ranges={EARLIER: pairs("=", ">")})
    finder.main([str(directory)], shell)
    question = (directory / "question-1.md").read_text()
    assert f"for its pair (none); each new commit whole with `git show <sha>` ({OWN[:11]})" in question and finder.SCOPED_STEP in question and not (directory / "rows.md").exists()
    assert shell.posted[0]["--text"] == f"finder: ok {HEAD} sol finder over {OLD[:7]}..{HEAD[:7]}: no majors"


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
    assert finder.heads(named | {"text": "#43299 HEAD 7414944e30bf (was a269784e4900)"}, REPO) == []
    assert finder.heads({"refs": {"prs": ["yasyf/cc-skills#836"]}, "text": f"dashboard-tap-fast {HEAD}"}, REPO) == []


def test_since_names_the_full_head_the_newest_finder_record_at_another_head_read():
    older = {"seq": 10, "lane": "p8a-step9", "head": "cfe6544", "text": "finder: ok cfe6544"}
    newest = {"seq": 12, "lane": "p8a-step9-2", "head": "a269784", "text": "finder: major a269784 1 open: walk.go:9"}
    current = {"seq": 14, "lane": "p8a-step9", "head": HEAD[:7], "text": f"finder: ok {HEAD[:7]}"}
    records = [{"kind": "head", "text": f"HEAD #43299 {OLD} (was cfe6544f8d03)", "fields": {}}]
    assert finder.since([older, newest, current], records, HEAD) == (OLD, {"seq": 12, "lane": "p8a-step9-2", "head": "a269784", "text": "finder: major a269784 1 open: walk.go:9"})
    assert finder.since([newest], [], HEAD)[0] == ""
    assert finder.since([current], records, HEAD) == ("", None)


def test_a_graded_head_origin_no_longer_serves_reads_the_whole_change(tmp_path):
    prior = {"seq": 149302, "lane": "p8a-step9", "head": "a269784", "text": "finder: ok a269784"}
    directory = job(tmp_path, since=OLD, prior=prior)
    shell = Fake(tmp_path, [reply()], gone=(OLD,))
    finder.main([str(directory)], shell)
    question = (directory / "question-1.md").read_text()
    assert f"This pass reads the PR's whole change: `git diff {OWN}^ {HEAD}`." in question and (directory / "rows.md").exists()
    assert shell.posted[0]["--text"] == f"finder: ok {HEAD} sol finder over dev...{HEAD[:7]}: no majors"
