"""finder.py against a fake gh, rulings.py, codex-ask and cci: the record a pass posts, the range it reads, and when it asks again or posts nothing."""

from __future__ import annotations

import json
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
STALE = "c3a3afc0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e6"
NEWER = "9285cd1e2f3a4b5c6d7e8f90a1b2c3d4e5f60718"
ROWS = (
    f"Required rows for {REPO}#43299 at {HEAD}\n\n## Every global, pending and touched row (2)\n\n"
    "### rule/949e6be | scope | in-force | said 1 | 2026-10-22\n> it should be a ssql replica that is live streaming, not polled\n\n"
    "### prompt/1026-105052 | reserved | in-force | said 1 | 2026-10-26\n> no credential on disk\nslice-hash: 0123456789ab\n"
)
SWEPT = "rows met: rule/949e6be; n/a: prompt/1026-105052; pending: "


class Fake:
    def __init__(self, tmp_path: Path, replies: list[dict], state: str = "OPEN", heads: tuple[str, ...] = (HEAD,)):
        self.tmp_path = tmp_path
        self.replies = list(replies)
        self.state = state
        self.heads = list(heads)
        self.calls: list[tuple[list[str], Path | None]] = []
        self.posted: list[dict] = []

    def run(self, argv: list[str], cwd: Path | None = None, timeout: int = finder.READ_SECONDS) -> str:
        argv = gateway(argv)
        self.calls.append((argv, cwd))
        if argv[:3] == ["gh", "pr", "view"]:
            head = self.heads.pop(0) if len(self.heads) > 1 else self.heads[0]
            return json.dumps({"state": self.state, "baseRefName": "dev", "baseRefOid": STALE, "headRefOid": head, "body": "## Summary\nA cut signal resubscribes from the position it fetched."})
        if argv[:3] == ["git", "ls-remote", "origin"]:
            assert argv[3] == "refs/heads/dev"
            return f"{BASE}\trefs/heads/dev\n"
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

    def asks(self) -> list[tuple[list[str], Path | None]]:
        return [(argv, cwd) for argv, cwd in self.calls if argv[0] == "codex-ask"]


def job(tmp_path: Path, **changes) -> Path:
    directory = tmp_path / "finder-desk" / f"43299-{HEAD[:12]}"
    directory.mkdir(parents=True)
    spec = {"drive": "d1", "repo": REPO, "pr": "43299", "head": HEAD, "since": "", "prior": None, "lane": "p8a-step9", "to": ["root"], "checkout": str(tmp_path / "checkout"), "model": "sol"}
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
    assert f"git diff {BASE}...{HEAD}" in (directory / "question-1.md").read_text() and STALE not in (directory / "question-1.md").read_text()
    assert result(directory) == {"verdict": "ok", "record": post["--text"], "posted": "#150001"}


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
    shell = Fake(tmp_path, [reply()])
    finder.main([str(directory)], shell)
    question = (directory / "question-1.md").read_text()
    assert "Run nothing that builds" in question and "Change no file, branch, or ref" in question
    assert f"origin {HEAD} {BASE} {OLD}`" in question
    assert f"`git diff {OLD} {HEAD}`" in question and f"`git range-diff {BASE} {OLD} {HEAD}`" in question
    assert "cci #149302 from p8a-step9 at a269784" in question and "walk.go:9 skips the volume link" in question
    assert "/plans/durable/plan/plan.md" in question and str(directory / "rows.md") in question
    assert (directory / "rows.md").read_text().startswith(f"No rows: the finder read every required row at {OLD[:10]}")
    assert not [argv for argv, _ in shell.calls if argv[2:3] == ["slice"]]
    [(ask, cwd)] = shell.asks()
    assert ask[:5] == ["codex-ask", "-m", "sol", "--schema", "findings"] and ask[-1] == str(directory / "question-1.md") and cwd == tmp_path / "checkout"
    assert shell.posted[0]["--text"] == f"finder: ok {HEAD} sol finder over {OLD[:7]}..{HEAD[:7]}: no majors"


def test_a_reply_that_drops_a_row_key_is_asked_once_more_and_two_such_replies_post_nothing(tmp_path):
    dropped = reply(swept="rows met: rule/949e6be")
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
    assert result(again) == {"error": "JobFailed: two replies named no verdict for prompt/1026-105052"}


def test_a_closed_pr_runs_no_finder_and_posts_nothing(tmp_path):
    directory = job(tmp_path)
    shell = Fake(tmp_path, [], "CLOSED")
    finder.main([str(directory)], shell)
    assert not shell.asks() and not shell.posted
    assert result(directory) == {"skipped": f"{REPO}#43299 is closed"}


@pytest.mark.parametrize(("heads", "asked"), [((NEWER,), 0), ((HEAD, NEWER), 1)])
def test_a_head_the_pr_moved_past_before_or_during_the_pass_posts_nothing(tmp_path, heads, asked):
    directory = job(tmp_path)
    shell = Fake(tmp_path, [reply(finding("major"))], heads=heads)
    finder.main([str(directory)], shell)
    assert len(shell.asks()) == asked and not shell.posted
    assert result(directory) == {"skipped": f"{REPO}#43299 moved past {HEAD[:10]} to {NEWER[:10]}"}


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
