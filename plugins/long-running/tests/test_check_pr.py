from __future__ import annotations

import contextlib
import hashlib
import io
import json
import subprocess
from pathlib import Path

import ghread
import ledger
import pytest
import rulings
from conftest import gateway
from long_running import judge
from test_rulings import DIFF, REPO, Judge, RegisterShell, home, judged, package, ran, registered, seeded, state

HEAD = "cafef00d"
CORPUS = "# Tests\n\n- test_drain_on_close: the pool is empty after a run closes.\n"
CLEAN = """## Summary

The pool drains when a run closes.

## Rulings

- bbbbbbb: Drain the pool when a run closes.
"""
LANE_HEAD = {"seq": 10, "kind": "head", "lane": "lane-a", "text": f"lane-a {HEAD}"}
FINDER_OK = {"seq": 11, "kind": "review", "lane": "finder-desk", "text": f"finder: ok {HEAD[:7]}"}
CONTRACT_SEQ = 8
STEP = "1.9"
RESERVED = {
    "kind": "reserved",
    "status": "in-force",
    "global": "no",
    "words": "Nothing deletes a cloud bucket without my word. Ask first.",
    "answer": "[]",
    "ops": '["* delete-bucket*", "infra/buckets/*"]',
    "paths": "none",
    "lanes": '["all"]',
}
BUCKET_DIFF = DIFF + """diff --git a/scripts/clean.sh b/scripts/clean.sh
--- a/scripts/clean.sh
+++ b/scripts/clean.sh
@@ -1,1 +1,2 @@
 set -e
+aws s3api delete-bucket --bucket old
"""
TEST_DIFF = """diff --git a/tests/test_pool.py b/tests/test_pool.py
--- a/tests/test_pool.py
+++ b/tests/test_pool.py
@@ -1,7 +1,3 @@
 import pool
-def test_drain_on_close():
-    assert pool.drain() == []
-def test_pool_closes_cleanly():
-    assert pool.closed()
-def test_scratch():
+def test_scratch():
+    assert pool
"""
BUCKET_ANSWER = {"id": "abcdef1" + "0" * 33, "title": "May the cleanup delete the old bucket?", "body": "Yes.", "tags": ["authorizes:reserved/cloud-delete"]}


class CheckShell(RegisterShell):
    def __init__(
        self,
        rows: dict[str, dict[str, str]],
        body: str = CLEAN,
        comments: tuple[str, ...] = (),
        diff: str = DIFF,
        records: list[dict] | None = None,
        reviews: list[dict] | None = None,
        answers: list[dict] | None = None,
        failing: tuple[str, ...] = (),
        plan: Path | None = None,
        stamp: str | None = None,
    ) -> None:
        stamp = stamp or current_stamp(rows, plan)
        super().__init__(rows, answers=answers, diff=diff, records=[read_back(stamp)])
        self.body = body
        self.comments = comments
        self.pr_records = [LANE_HEAD, FINDER_OK] if records is None else records
        self.reviews = [graded(stamp)] if reviews is None else reviews
        self.failing = failing

    def run(self, argv: list[str], stdin: str | None = None, cwd: Path | None = None) -> str:
        plain = gateway(argv)
        if any(" ".join(plain).startswith(prefix) for prefix in self.failing):
            raise subprocess.CalledProcessError(1, argv)
        match plain:
            case ["gh", "pr", "view", "12", "--repo", "acme/app", "--json", "body,baseRefName,comments"]:
                return json.dumps({"body": self.body, "baseRefName": "main", "comments": [{"body": comment} for comment in self.comments]})
            case ["cci", "tail", "--drive", "brook-cci", "--since-seq", since, "--json", "--budget", _, "--pr", "acme/app#12"]:
                return "".join(json.dumps(record) + "\n" for record in self.pr_records if record["seq"] > int(since))
            case ["cci", "tail", "--drive", "brook-cci", "--since-seq", since, "--json", "--budget", _, "--kind", "review", "--kind", "answer"]:
                return "".join(json.dumps(record) + "\n" for record in self.reviews if record["seq"] > int(since) and record["kind"] in ("review", "answer"))
        return super().run(argv, stdin, cwd)


def citing(*lines: str) -> str:
    cited = "- bbbbbbb: Drain the pool when a run closes.\n"
    return CLEAN.replace(cited, cited + "".join(f"{line}\n" for line in lines))


def pinned(package: Path, corpus: str = CORPUS, pin: str | None = None) -> dict[str, dict[str, str]]:
    (package / "plan" / "B-tests.md").write_text(corpus)
    rows = seeded(package) | {"reserved/cloud-delete": dict(RESERVED)}
    rows["corpus/b-tests"]["sha256"] = pin or hashlib.sha256(corpus.encode()).hexdigest()
    return rows


def current_stamp(rows: dict[str, dict[str, str]], plan: Path | None) -> str:
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        assert rulings.main(["slice", "--program", "brook", "--repo", REPO, "--lane", "lane-a", "--step", STEP, *(["--plan", str(plan)] if plan else [])], RegisterShell(rows)) == 0
    return printed.getvalue().rstrip("\n").rpartition("slice-hash: ")[2]


def read_back(stamp: str) -> dict:
    state = Path.home() / ".claude" / "scratch" / "brook"
    body = state / "readback-lane-a.md"
    body.write_text(f"Register slice: lane=lane-a step={STEP}\nslice-hash: {stamp}\n")
    (state / "runner.json").write_text(json.dumps({"landing": {"fidelity_lane": ["fidelity-desk", "fidelity-desk-2"]}}))
    return {"seq": CONTRACT_SEQ, "kind": "contract", "lane": "lane-a", "text": "[readback] lane-a", "refs": {"path": str(body)}}


def graded(stamp: str, verdict: str = "ok", seq: int = 9, lane: str = "fidelity-desk") -> dict:
    detail = stamp if verdict == "ok" else "rule/bbbbbbb the drain line is not read back"
    return {"seq": seq, "kind": "review", "lane": lane, "re": CONTRACT_SEQ, "text": f"readback: {verdict} lane-a {detail}"}


def check(capsys: pytest.CaptureFixture[str], shell: ledger.Shell, *extra: str) -> tuple[int, list[str]]:
    code, out = ran(capsys, shell, "check-pr", "--pr", "acme/app#12", "--head", HEAD, *extra)
    return code, out.splitlines()


UNCITED = "RULINGS-UNCITED rule/bbbbbbb\tcite them under a ## Rulings heading in one PR comment, never by editing the body (427c9c93)"


def test_a_head_that_breaks_nothing_prints_nothing_and_exits_zero(package: Path, state: Path, registered: Path, capsys) -> None:
    assert check(capsys, CheckShell(pinned(package))) == (0, [])


def test_an_incident_fixs_anchored_row_the_diff_touches_needs_a_rulings_line(package: Path, state: Path, registered: Path, capsys) -> None:
    """45200f2: the uncited row blocks an incident fix's merge; every other head leaves the citation to the PR body judge at ship."""
    uncited = CLEAN.replace("- bbbbbbb: Drain the pool when a run closes.", "- ccccccc: Cards quote the words they ask about.")

    assert check(capsys, CheckShell(pinned(package), body=uncited), "--incident") == (1, [UNCITED])
    assert check(capsys, CheckShell(pinned(package), body=uncited.replace("- ccccccc:", "- rule/bbbbbbb:")), "--incident") == (0, [])
    assert check(capsys, CheckShell(pinned(package), body=uncited)) == (0, [])


def test_a_rulings_line_in_a_pr_comment_cites_the_row(package: Path, state: Path, registered: Path, capsys) -> None:
    uncited = CLEAN.replace("- bbbbbbb: Drain the pool when a run closes.", "- ccccccc: Cards quote the words they ask about.")

    assert check(capsys, CheckShell(pinned(package), body=uncited, comments=("Second-model review: ok.", "## Rulings\n\n- bbbbbbb: Drain the pool when a run closes.")), "--incident") == (0, [])


class CachedViewShell(CheckShell):
    def run(self, argv: list[str], stdin: str | None = None, cwd: Path | None = None) -> str:
        if gateway(argv)[1:3] == ["pr", "view"] and argv[argv.index("--max-age") + 1] != ghread.FRESH:
            return json.dumps({"body": self.body, "baseRefName": "main", "comments": []})
        return super().run(argv, stdin, cwd)


def test_a_rulings_comment_posted_moments_ago_cites_the_row(package: Path, state: Path, registered: Path, capsys) -> None:
    uncited = CLEAN.replace("- bbbbbbb: Drain the pool when a run closes.", "- ccccccc: Cards quote the words they ask about.")

    assert check(capsys, CachedViewShell(pinned(package), body=uncited, comments=("## Rulings\n\n- bbbbbbb: Drain the pool when a run closes.",)), "--incident") == (0, [])


def test_a_reserved_touch_needs_an_answer_cited_or_tapped_for_this_pr(package: Path, state: Path, registered: Path, capsys) -> None:
    refused = (1, ["RESERVED-NO-ANSWER reserved/cloud-delete\tNothing deletes a cloud bucket without my word."])
    tapped = dict(BUCKET_ANSWER, title="Merge #12?", body="Owner tapped Merge.")

    assert check(capsys, CheckShell(pinned(package), diff=BUCKET_DIFF)) == refused
    assert check(capsys, CheckShell(pinned(package), diff=BUCKET_DIFF, answers=[BUCKET_ANSWER])) == refused
    assert check(capsys, CheckShell(pinned(package), diff=BUCKET_DIFF, answers=[dict(tapped, title="Merge #13?")])) == refused
    assert check(capsys, CheckShell(pinned(package), body=citing("- abcdef1: the cleanup may delete the old bucket."), diff=BUCKET_DIFF, answers=[BUCKET_ANSWER])) == (0, [])
    assert check(capsys, CheckShell(pinned(package), diff=BUCKET_DIFF, answers=[tapped])) == (0, [])


def test_a_graders_touches_tag_alone_is_no_reserved_touch(package: Path, state: Path, registered: Path, capsys) -> None:
    tagged = [LANE_HEAD, FINDER_OK, *({"seq": seq, "kind": "review", "lane": "fidelity-pipeline", "text": text} for seq, text in ((12, "touches reserved/cloud-delete"), (13, "touches rule/1375cff"), (14, f"fidelity: missing {HEAD[:7]} touches reserved/cloud-delete")))]

    assert check(capsys, CheckShell(pinned(package), records=tagged)) == (0, [])
    assert check(capsys, CheckShell(pinned(package), records=tagged, diff=BUCKET_DIFF)) == (1, ["RESERVED-NO-ANSWER reserved/cloud-delete\tNothing deletes a cloud bucket without my word."])


def test_a_test_fake_that_names_a_reserved_op_is_no_reserved_touch(package: Path, state: Path, registered: Path, capsys) -> None:
    fake = DIFF + """diff --git a/go/sand/internal/hostblobtest/faults.go b/go/sand/internal/hostblobtest/faults.go
--- a/go/sand/internal/hostblobtest/faults.go
+++ b/go/sand/internal/hostblobtest/faults.go
@@ -1,1 +1,2 @@
 package hostblobtest
+var cleanup = "aws s3api delete-bucket --bucket old"
diff --git a/go/sand/corpus_test.go b/go/sand/corpus_test.go
--- a/go/sand/corpus_test.go
+++ b/go/sand/corpus_test.go
@@ -1,1 +1,2 @@
 package sand
+var cleanup = "aws s3api delete-bucket --bucket old"
"""

    assert check(capsys, CheckShell(pinned(package), diff=fake)) == (0, [])


MAC_EVICT = dict(RESERVED, words="Your tap on the eviction release's merge card is also its authorization to delete chat files on Macs.", ops='["go/sand/cmd/*evict*", "*Remove*", "*evict*"]')


def added(path: str, line: str) -> str:
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1,1 +1,2 @@\n context\n+{line}\n"


def test_a_reserved_row_matches_its_identifiers_inside_its_paths_never_a_path_alone_test_helpers_or_prose(package: Path, state: Path, registered: Path, capsys) -> None:
    rows = pinned(package) | {"reserved/mac-evict": MAC_EVICT}
    browser_stub = DIFF + added("dashboard/src/iris/testing/dom.ts", "removeEventListener() {},") + added("dashboard/src/iris/chat.ts", "window.removeEventListener('blur', onBlur)")
    prose = DIFF + added("docs/chats.md", "Old chats are evicted and Removed from the Mac.")
    refused = (1, ["RESERVED-NO-ANSWER reserved/mac-evict\tYour tap on the eviction release's merge card is also its authorization to delete chat files on Macs."])

    assert check(capsys, CheckShell(rows, diff=browser_stub)) == (0, [])
    assert check(capsys, CheckShell(rows, diff=prose)) == (0, [])
    assert check(capsys, CheckShell(rows, diff=DIFF + added("go/sand/cmd/evict_chats.go", "return nil"))) == (0, [])
    assert check(capsys, CheckShell(rows, diff=DIFF + added("go/sand/cmd/serve.go", "os.RemoveAll(dir)"))) == (0, [])
    assert check(capsys, CheckShell(rows, diff=DIFF + added("go/sand/cmd/evict_chats.go", "os.RemoveAll(dir)"))) == refused
    assert check(capsys, CheckShell(rows, diff=DIFF + added("go/sand/cmd/evict_chats.go", "evict(session)"))) == refused


def test_a_head_graded_again_after_a_new_reserved_row_reads_the_row_though_its_diff_facts_are_kept(package: Path, state: Path, registered: Path, capsys) -> None:
    diff = DIFF + added("go/sand/cmd/evict_chats.go", "os.RemoveAll(dir)")
    refused = (1, ["RESERVED-NO-ANSWER reserved/mac-evict\tYour tap on the eviction release's merge card is also its authorization to delete chat files on Macs."])

    assert check(capsys, CheckShell(pinned(package), diff=diff)) == (0, [])
    assert check(capsys, CheckShell(pinned(package), diff=diff)) == (0, [])
    assert check(capsys, CheckShell(pinned(package) | {"reserved/mac-evict": MAC_EVICT}, diff=diff)) == refused


CLOUD_DELETE = dict(RESERVED, words="Nothing deletes a cloud chat without my word. Ask first.", ops='["*DeleteObject*", "*durable_at*"]')


def changed(path: str, removed: list[str], kept: list[str]) -> str:
    lines = [f"-{line}" for line in removed] + [f"+{line}" for line in kept]
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1,{len(removed)} +1,{len(kept)} @@\n" + "\n".join(lines) + "\n"


def test_a_reserved_row_matches_only_what_a_diff_adds_never_what_it_deletes_or_rewraps(package: Path, state: Path, registered: Path, capsys) -> None:
    rows = pinned(package) | {"reserved/cloud-delete": CLOUD_DELETE}
    removal = DIFF + changed(
        "go/sand/cmd/session_pass.go",
        ["sessions' threads, the runs' statuses and durable_at go through the API as", "\tclient.DeleteObject(ctx, key)"],
        ["read in their place. The sessions' threads, the runs' statuses and", "durable_at go through the API as the signed-in platform admin, whose"],
    )
    refused = (1, ["RESERVED-NO-ANSWER reserved/cloud-delete\tNothing deletes a cloud chat without my word."])

    assert check(capsys, CheckShell(rows, diff=removal)) == (0, [])
    assert check(capsys, CheckShell(rows, diff=DIFF + changed("rust/crates/sandsql-testkit/src/s3.rs", [], ["\tclient.DeleteObject(ctx, key)"]))) == (0, [])
    assert check(capsys, CheckShell(rows, diff=DIFF + changed("go/sand/cmd/session_pass.go", [], ["\tclient.DeleteObject(ctx, key)"]))) == refused


CLOUD_DELETE_MECHANISMS = dict(
    CLOUD_DELETE,
    ops='["*DeleteObject*", "*DeleteBucket*", "*delete_prefix*", "delete from iris_sessions*", "kubectl delete *", "aws s3 rm *", "gsutil rm *"]',
)


def test_a_reserved_cloud_delete_row_matches_cloud_deletion_never_a_map_delete_a_table_read_or_a_local_remove(package: Path, state: Path, registered: Path, capsys) -> None:
    """#42790: runtime-api dropped its own in-memory cached grant with a Go map delete and joined iris_sessions in a query; neither deletes anything in the cloud."""
    rows = pinned(package) | {"reserved/cloud-delete": CLOUD_DELETE_MECHANISMS}
    refused = (1, ["RESERVED-NO-ANSWER reserved/cloud-delete\tNothing deletes a cloud chat without my word."])

    for path, line in [
        ("go/sand/next/cmd/runtime-api/run_grants.go", "\t\tdelete(g.held, key)"),
        ("go/sand/next/internal/runtime-api/sessions/queries.sql", "JOIN iris_sessions s ON s.id = r.session_id"),
        ("go/sand/next/cmd/runtime-api/session_replicas.go", "os.Remove(path)"),
        ("go/sand/next/cmd/runtime-api/session_replicas_test.go", "client.DeleteObject(ctx, key)"),
    ]:
        assert check(capsys, CheckShell(rows, diff=DIFF + added(path, line))) == (0, []), line
    for path, line in [
        ("go/sand/cmd/session_pass.go", "client.DeleteObject(ctx, key)"),
        ("go/sand/next/internal/runtime-api/sessions/queries.sql", "DELETE FROM iris_sessions WHERE id = $1"),
        ("tools/cleanup.sh", "aws s3 rm s3://chats/sessions --recursive"),
    ]:
        assert check(capsys, CheckShell(rows, diff=DIFF + added(path, line))) == refused, line


def copied(source: str, path: str, hunk: str) -> str:
    return f"diff --git a/{source} b/{path}\nsimilarity index 97%\ncopy from {source}\ncopy to {path}\nindex 1111111..2222222 100644\n--- a/{source}\n+++ b/{path}\n{hunk}"


def test_a_copy_of_a_base_file_adds_only_the_lines_it_changes(package: Path, state: Path, registered: Path, capsys) -> None:
    rows = pinned(package) | {"reserved/cloud-delete": CLOUD_DELETE}
    imports = "@@ -1,3 +1,3 @@\n-import \"sand/internal/views\"\n+import \"sand/next/internal/views\"\n \tclient.DeleteObject(ctx, key)\n"
    deletes = "@@ -1,2 +1,3 @@\n \tclient.DeleteObject(ctx, key)\n+\tclient.DeleteObject(ctx, other)\n"
    refused = (1, ["RESERVED-NO-ANSWER reserved/cloud-delete\tNothing deletes a cloud chat without my word."])

    assert check(capsys, CheckShell(rows, diff=DIFF + copied("go/sand/cmd/session_pass.go", "go/sand/next/cmd/session_pass.go", imports))) == (0, [])
    assert check(capsys, CheckShell(rows, diff=DIFF + copied("go/sand/cmd/session_pass.go", "go/sand/next/cmd/session_pass.go", deletes))) == refused


def test_a_removed_test_the_corpus_or_appendix_names_needs_an_answer_naming_it(package: Path, state: Path, registered: Path, tmp_path: Path, capsys) -> None:
    plan = tmp_path / "plan.md"
    plan.write_text("# Plan\n\n## Appendix\n\n- test_pool_closes_cleanly proves close is clean.\n")
    dropped = {"kind": "pick", "status": "in-force", "words": "Drop test_drain_on_close and test_pool_closes_cleanly.", "answer": '["fffffff"]', "lanes": '["all"]', "paths": "none"}

    assert check(capsys, CheckShell(pinned(package), diff=TEST_DIFF, plan=plan), "--plan", str(plan)) == (
        1,
        ["TEST-REMOVED test_drain_on_close", "TEST-REMOVED test_pool_closes_cleanly"],
    )
    authorized = CheckShell(pinned(package) | {"rule/fffffff": dropped}, body=citing("- fffffff: drop both tests."), diff=TEST_DIFF, plan=plan)
    assert check(capsys, authorized, "--plan", str(plan)) == (0, [])


RUST_DIFF = """diff --git a/src/pool.rs b/src/pool.rs
--- a/src/pool.rs
+++ b/src/pool.rs
@@ -1,7 +1,4 @@
-fn open() -> Pool {
-    Pool::new()
-}
 #[test]
-fn drains_on_close() {
+fn drains_when_closed() {
     assert!(pool().drain().is_empty());
 }
"""


def test_a_removed_rust_test_is_one_its_test_attribute_marks_even_as_context(package: Path, state: Path, registered: Path, tmp_path: Path, capsys) -> None:
    plan = tmp_path / "plan.md"
    plan.write_text("# Plan\n\n## Appendix\n\n- drains_on_close proves the pool empties; open the pool once.\n")

    assert check(capsys, CheckShell(pinned(package), diff=RUST_DIFF, plan=plan), "--plan", str(plan)) == (1, ["TEST-REMOVED drains_on_close"])
    assert check(capsys, CheckShell(pinned(package), diff=RUST_DIFF, plan=plan), "--incident", "--plan", str(plan)) == (1, ["TEST-REMOVED drains_on_close"])


def test_an_unreadable_plan_file_is_a_blocker_beside_every_other_finding(package: Path, state: Path, registered: Path, tmp_path: Path, capsys) -> None:
    plan = tmp_path / "plan.md"
    plan.write_bytes(b"# Plan\n\n\xff\xfe not text\n")
    uncited = CLEAN.replace("- bbbbbbb: Drain the pool when a run closes.", "- ccccccc: Cards quote the words they ask about.")

    code, lines = check(capsys, CheckShell(pinned(package), body=uncited, diff=DIFF + TEST_DIFF, stamp="0" * 12), "--incident", "--plan", str(plan))

    assert code == 1
    assert lines[0] == UNCITED
    assert any(line.startswith("BLOCKER the removed tests: ") for line in lines[1:])


def test_a_corpus_that_moved_holds_the_head_and_only_blocks_an_incident_fix(package: Path, state: Path, registered: Path, capsys) -> None:
    corpus = package / "plan" / "B-tests.md"
    moved = pinned(package, pin="0" * 64)

    assert check(capsys, CheckShell(moved, diff=TEST_DIFF)) == (1, [f"TESTS-CORPUS {corpus} sha256 differs from the register's pin"])
    assert check(capsys, CheckShell(moved, diff=TEST_DIFF), "--incident") == (0, [f"BLOCKER TESTS-CORPUS {corpus} sha256 differs from the register's pin"])
    assert check(capsys, CheckShell(moved)) == (0, [])


def test_an_incident_fix_reads_only_the_appendix_for_removed_tests(package: Path, state: Path, registered: Path, tmp_path: Path, capsys) -> None:
    plan = tmp_path / "plan.md"
    plan.write_text("# Plan\n\n## Appendix\n\n- test_pool_closes_cleanly proves close is clean.\n")

    assert check(capsys, CheckShell(pinned(package), diff=TEST_DIFF), "--incident") == (0, [])
    assert check(capsys, CheckShell(pinned(package), diff=TEST_DIFF, plan=plan), "--incident", "--plan", str(plan)) == (1, ["TEST-REMOVED test_pool_closes_cleanly"])


def test_the_newest_finder_verdict_on_the_head_decides(package: Path, state: Path, registered: Path, capsys) -> None:
    major = {"seq": 12, "kind": "review", "lane": "finder-desk", "text": f"finder: major {HEAD[:7]} 2 open"}
    fixed = {"seq": 13, "kind": "review", "lane": "finder-desk", "text": f"finder: ok {HEAD[:7]}"}
    elsewhere = {"seq": 14, "kind": "review", "lane": "finder-desk", "text": "finder: major 1234567 1 open"}

    assert check(capsys, CheckShell(pinned(package), records=[LANE_HEAD, FINDER_OK, major])) == (1, [f"FINDER-MAJOR {HEAD[:7]}"])
    assert check(capsys, CheckShell(pinned(package), records=[LANE_HEAD, major, fixed, elsewhere])) == (0, [])
    assert check(capsys, CheckShell(pinned(package), records=[LANE_HEAD, FINDER_OK, major | {"seq": 15, "lane": LANE_HEAD["lane"]}])) == (0, [])


def test_an_added_scope_name_needs_a_cited_answer_whose_words_name_it(package: Path, state: Path, registered: Path, capsys) -> None:
    probe = DIFF.replace("+    self.drain();", "+    probe(relay-server);")
    parked = DIFF.replace("+    self.drain();", '+    park("parked worker");')

    assert check(capsys, CheckShell(pinned(package), diff=probe)) == (1, ["NAME-HIT rule/aaaaaaa src/relay/pool.rs:2 relay-server"])
    assert check(capsys, CheckShell(pinned(package), body=citing("- aaaaaaa: relay code stays out of the relay-server."), diff=probe)) == (
        1,
        ["NAME-HIT rule/aaaaaaa src/relay/pool.rs:2 relay-server"],
    )
    assert check(capsys, CheckShell(pinned(package), body=citing("- 9999999: the health probe stays."), diff=probe)) == (0, [])
    assert check(capsys, CheckShell(pinned(package), body=citing("- 9999999: the health probe stays."), diff=parked)) == (
        1,
        ["NAME-HIT rule/aaaaaaa src/relay/pool.rs:2 parked worker"],
    )


EDGE_CASES = {
    "kind": "scope",
    "status": "in-force",
    "global": "yes",
    "words": "Do not handle weird edge cases. Make it work in the default case, with none of the refusals anyone is having.",
    "answer": '["5a5a5a5"]',
    "at": "Oct 22 9:00 AM PT",
    "said": "1",
    "names": '["edge cases", "refusals", "default case"]',
    "steps": '["all"]',
    "lanes": '["all"]',
    "paths": "none",
}
SERIES_DIFF = DIFF.replace("+    self.drain();", "+    series.Refusals = append(series.Refusals, cause);")
SERIES_HIT = "NAME-HIT rule/5a5a5a5 src/relay/pool.rs:2 refusals"


@pytest.mark.parametrize(
    ("line", "refusals"),
    [
        (None, [SERIES_HIT]),
        ("- rule/5a5a5a5: leaves out edge cases", [SERIES_HIT]),
        ('- 5a5a5a5, Oct 22: "never refuse anything at all". The series check refuses only a missing series.', [SERIES_HIT]),
        ('- 5a5a5a5, Oct 22: "Make it work in the default case".', [SERIES_HIT]),
        ('- bbbbbbb, Oct 22: "Make it work in the default case". The series check refuses only a missing series.', [SERIES_HIT]),
        ('- 5a5a5a5, Oct 22: "Make it work in the default case". The series check refuses only a missing or failing series.', []),
        ('- rule/5a5a5a5: "with none of the refusals anyone is having". A cut waits only on a missing or failing series.', []),
    ],
)
def test_a_hit_on_a_rows_own_name_clears_once_rulings_quote_that_row_and_say_how_the_pr_keeps_it(
    package: Path, state: Path, registered: Path, capsys, line: str | None, refusals: list[str]
) -> None:
    rows = pinned(package) | {"rule/5a5a5a5": dict(EDGE_CASES)}
    body = citing(line) if line else CLEAN
    assert check(capsys, CheckShell(rows, body=body, diff=SERIES_DIFF)) == (1 if refusals else 0, refusals)


SHORT_ROW = EDGE_CASES | {"words": "Yes (my pick)", "answer": '["6b6b6b6"]', "names": '["writable"]'}
WRITABLE_HIT = "NAME-HIT rule/6b6b6b6 src/relay/pool.rs:2 writable"


@pytest.mark.parametrize(
    ("line", "refusals"),
    [
        ('- 6b6b6b6, Oct 22: "Yes (my pick)". Chats a Mac started stay writable after its host goes.', []),
        ('- 6b6b6b6, Oct 22: "my pick". Chats a Mac started stay writable after its host goes.', [WRITABLE_HIT]),
    ],
)
def test_a_row_shorter_than_a_quote_clears_when_the_line_quotes_all_of_its_words(
    package: Path, state: Path, registered: Path, capsys, line: str, refusals: list[str]
) -> None:
    rows = pinned(package) | {"rule/6b6b6b6": dict(SHORT_ROW)}
    diff = DIFF.replace("+    self.drain();", "+    let writable = true;")
    assert check(capsys, CheckShell(rows, body=citing(line), diff=diff)) == (1 if refusals else 0, refusals)

def test_a_decision_in_the_body_must_cite_an_answer(package: Path, state: Path, registered: Path, capsys) -> None:
    body = CLEAN.replace(
        "The pool drains when a run closes.",
        "We decided to keep the retry loop.\nI chose the drain path (bbbbbbb).\nI picked the merge order (cafef00d).\nWe went with the slice at 0123456789ab.\nI chose the old bucket (abcdef1).",
    )

    assert check(capsys, CheckShell(pinned(package), body=body, answers=[BUCKET_ANSWER])) == (
        1,
        ["LANE-DECIDED We decided to keep the retry loop.", "LANE-DECIDED I picked the merge order (cafef00d).", "LANE-DECIDED We went with the slice at 0123456789ab."],
    )
    quoted = CLEAN.replace("The pool drains when a run closes.", "- On Oct 22 you said “Yes (my pick)”; this change leaves it alone.")
    assert check(capsys, CheckShell(pinned(package), body=quoted)) == (0, [])
    unread = CheckShell(pinned(package), body=body, answers=[BUCKET_ANSWER], failing=("ccn -R /repo answer list --limit 0",))
    code, lines = check(capsys, unread)
    assert code == 1
    assert lines[:1] == ["LANE-DECIDED We decided to keep the retry loop."]
    assert lines[1].startswith("BLOCKER the owner's answers: ") and len(lines) == 2


def test_the_lane_of_the_head_needs_a_desks_newest_grade_ok_for_its_current_slice(package: Path, state: Path, registered: Path, capsys) -> None:
    rows = pinned(package)
    stamp = current_stamp(rows, None)
    opened = {"seq": 15, "kind": "opened", "lane": "a0123456789abcdef", "text": "opened acme/app#12"}
    missing_after = [graded(stamp), graded(stamp, "missing", seq=10, lane="fidelity-desk-2")]
    ok_after = [graded(stamp, "missing", seq=9), graded(stamp, seq=10, lane="fidelity-desk-2")]

    assert check(capsys, CheckShell(rows)) == (0, [])
    assert check(capsys, CheckShell(rows, reviews=ok_after)) == (0, [])
    assert check(capsys, CheckShell(rows, reviews=[])) == (1, ["CONTRACT-MISSING lane-a: no fidelity desk has graded a read-back"])
    assert check(capsys, CheckShell(rows, reviews=[graded(stamp, lane="lane-b")])) == (1, ["CONTRACT-MISSING lane-a: no fidelity desk has graded a read-back"])
    assert check(capsys, CheckShell(rows, reviews=[dict(graded(stamp), text=f"readback: ok lane-b {stamp}")])) == (1, ["CONTRACT-MISSING lane-a: no fidelity desk has graded a read-back"])
    assert check(capsys, CheckShell(rows, reviews=[dict(graded(stamp), text=f"contract read-back #{CONTRACT_SEQ} (lane-a, the drain path): ok against owner answer bbbbbbb. {stamp}")])) == (0, [])
    assert check(capsys, CheckShell(rows, reviews=missing_after)) == (
        1,
        ["CONTRACT-MISSING lane-a: the newest grade, #10, is missing: rule/bbbbbbb the drain line is not read back"],
    )
    assert check(capsys, CheckShell(rows, stamp="0" * 12)) == (0, [])
    assert check(capsys, CheckShell(rows, records=[LANE_HEAD, FINDER_OK, opened])) == (0, [])
    assert check(capsys, CheckShell(rows, records=[FINDER_OK])) == (0, ["BLOCKER no cci record names the lane of acme/app#12"])
    assert check(capsys, CheckShell(rows, records=[FINDER_OK, opened])) == (0, ["BLOCKER no cci record names the lane of acme/app#12"])
    reported = {"seq": 16, "kind": "report", "lane": "lane-a", "text": "clean READY acme/app#12"}
    assert check(capsys, CheckShell(rows, records=[FINDER_OK, opened, reported])) == (0, [])
    assert check(capsys, CheckShell(rows, records=[FINDER_OK, opened, reported], reviews=[])) == (1, ["CONTRACT-MISSING lane-a: no fidelity desk has graded a read-back"])
    status = {"seq": 17, "kind": "report", "lane": "root", "text": "CI is still red on acme/app#12"}
    assert check(capsys, CheckShell(rows, records=[FINDER_OK, opened, reported, status])) == (0, [])
    assert check(capsys, CheckShell(rows, records=[FINDER_OK, opened, status])) == (0, ["BLOCKER no cci record names the lane of acme/app#12"])


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("readback: ok 7bed615 lane-a #12->#13 (replaces my not at #10). All three items match the code.", (0, [])),
        ("readback: ok lane-a", (0, [])),
        ("readback: ok 7bed615 lane-b all three items match", (1, ["CONTRACT-MISSING lane-a: no fidelity desk has graded a read-back"])),
        ("readback: missing 7bed615 lane-a", (1, ["CONTRACT-MISSING lane-a: the newest grade, #9, is missing: no reason given"])),
        ("readback ok lane-a: row prompt/1030-212507 now says all search lives in the new tree. Every other row carries #235609.", (0, [])),
    ],
    ids=["head-then-lane", "lane-without-stamp", "head-then-another-lane", "missing-without-reason", "no-colon-after-readback"],
)
def test_a_desks_grade_names_the_lane_after_an_optional_head_and_before_an_optional_stamp(package: Path, state: Path, registered: Path, capsys, text: str, expected: tuple[int, list[str]]) -> None:
    rows = pinned(package)
    assert check(capsys, CheckShell(rows, reviews=[dict(graded(current_stamp(rows, None)), text=text)])) == expected


@pytest.mark.parametrize(
    ("kind", "text", "expected"),
    [
        ("answer", "read-back lane-a: ok. It matches the record.", (0, [])),
        ("answer", "readback: ok lane-a", (0, [])),
        ("answer", "readback: ok 1f15356497f7 lane-a. Contract #179317 graded ok at #179327", (0, [])),
        ("review", "read-back lane-a: ok", (0, [])),
        ("answer", "read-back lane-a: missing\nrule/bbbbbbb the drain line", (1, ["CONTRACT-MISSING lane-a: the newest grade, #9, is missing: rule/bbbbbbb the drain line"])),
        ("answer", "read-back lane-b: ok. It matches the record.", (1, ["CONTRACT-MISSING lane-a: no fidelity desk has graded a read-back"])),
        ("note", "readback: ok lane-a", (1, ["CONTRACT-MISSING lane-a: no fidelity desk has graded a read-back"])),
    ],
    ids=["answer-said-ok", "answer-canonical-ok", "answer-canonical-ok-lane-ends-a-sentence", "review-said-ok", "answer-said-missing", "answer-another-lane", "note-is-no-grade"],
)
def test_a_desks_read_back_grade_counts_as_an_answer_or_a_review_in_either_phrasing(package: Path, state: Path, registered: Path, capsys, kind: str, text: str, expected: tuple[int, list[str]]) -> None:
    rows = pinned(package)
    assert check(capsys, CheckShell(rows, reviews=[dict(graded(current_stamp(rows, None)), kind=kind, text=text)])) == expected


def test_a_desks_ok_stands_until_a_newer_grade_whatever_the_register_gains_after_it(package: Path, state: Path, registered: Path, capsys) -> None:
    rows = pinned(package)
    stamp = current_stamp(rows, None)
    added = rows | {"rule/fffffff": {"kind": "pick", "status": "in-force", "global": "no", "words": "Name the lane on every card.", "answer": '["fffffff"]', "steps": f'["{STEP}"]', "lanes": '["all"]', "paths": "none"}}
    reworded = pinned(package)
    reworded["rule/ccccccc"]["words"] = "Cards quote the owner's words they ask about."

    assert current_stamp(added, None) != stamp
    assert check(capsys, CheckShell(added, stamp=stamp)) == (0, [])
    assert ran(capsys, CheckShell(added, stamp=stamp), "accepted", "--lane", "lane-a") == (0, "accepted lane-a\n")
    assert check(capsys, CheckShell(reworded, stamp=stamp)) == (0, [])


def test_accepted_names_a_lane_only_once_a_desk_graded_its_current_slice_ok(package: Path, state: Path, registered: Path, capsys) -> None:
    rows = pinned(package)

    assert ran(capsys, CheckShell(rows), "accepted", "--lane", "lane-a") == (0, "accepted lane-a\n")
    assert ran(capsys, CheckShell(rows, reviews=[]), "accepted", "--lane", "lane-a") == (1, "CONTRACT-MISSING lane-a: no fidelity desk has graded a read-back\n")


def test_a_numbered_successor_keeps_the_read_back_a_desk_accepted_for_the_lane_it_replaced(package: Path, state: Path, registered: Path, capsys) -> None:
    """Root #241130: fidelity-desk-11 graded fuzzy-match-4's read-back ok as "readback ok fuzzy-match-4: ...", which no grade form read, and fuzzy-match-4 was rotating to fuzzy-match-5."""
    rows = pinned(package)
    grade = dict(graded(current_stamp(rows, None)), text="readback ok lane-a: every row carries the graded contract")

    assert ran(capsys, CheckShell(rows, reviews=[grade]), "accepted", "--lane", "lane-a-2") == (0, "accepted lane-a-2\n")
    assert ran(capsys, CheckShell(rows, reviews=[grade]), "accepted", "--lane", "lane-b-2") == (1, "CONTRACT-MISSING lane-b-2: no fidelity desk has graded a read-back\n")


def test_a_pr_citing_a_shape_ruling_adds_a_test_naming_it_or_the_row_records_one(package: Path, state: Path, registered: Path, capsys) -> None:
    rows = pinned(package)
    rows["rule/bbbbbbb"]["words"] = "One pool process drains every run when it closes."
    named = DIFF + """diff --git a/tests/test_pool_count.py b/tests/test_pool_count.py
--- /dev/null
+++ b/tests/test_pool_count.py
@@ -0,0 +1,2 @@
+def test_one_pool_process_for_every_run():
+    \"\"\"rule/bbbbbbb: one pool process drains every run.\"\"\"
"""

    code, lines = check(capsys, CheckShell(rows))
    assert code == 1 and [line.split("\t")[0] for line in lines] == ["SHAPE-UNTESTED rule/bbbbbbb"]
    assert check(capsys, CheckShell(rows, diff=named)) == (0, [])
    rows["rule/bbbbbbb"]["shape_tests"] = '["acme/app#11 tests/test_pool_count.py::test_one_pool_process_for_every_run"]'
    assert check(capsys, CheckShell(rows)) == (0, [])


def test_a_test_naming_a_shape_ruling_that_the_judge_reads_as_another_invariant_warns_once_per_row_and_test(package: Path, state: Path, registered: Path, capsys, judged: Judge) -> None:
    """Oct 29: the test that named the upload ruling asserted two processes for two keys, while the owner's words fixed one process for every chat."""
    rows = pinned(package)
    rows["rule/bbbbbbb"]["words"] = "One pool process drains every run when it closes."
    per_run = DIFF + """diff --git a/tests/test_pool_count.py b/tests/test_pool_count.py
--- /dev/null
+++ b/tests/test_pool_count.py
@@ -0,0 +1,3 @@
+def test_one_pool_process_per_run():
+    \"\"\"rule/bbbbbbb: one pool process drains every run.\"\"\"
+    assert pool_processes(runs=2) == 2
"""
    other = rulings.judge.Verdict("yes", "pool_processes(runs=2) == 2", "One pool process drains every run", "He said one pool process for every run, and this test passes with one process per run.")
    judged.verdict = lambda kind, artifact: other if kind == "test" else None

    code, lines = check(capsys, CheckShell(rows, diff=per_run))

    assert code == 1 and lines == [
        "SHAPE-UNTESTED rule/bbbbbbb\ta judge reads the test naming rule/bbbbbbb as asserting a different invariant than his words:"
        " He said one pool process for every run, and this test passes with one process per run."
        ' The test: "pool_processes(runs=2) == 2"; his words: "One pool process drains every run". Make it fail whenever his invariant fails, or answer this warning with the owner words it carries'
    ]
    [(kind, evidence, artifact, kwargs)] = judged.asked
    assert (kind, kwargs["drive"]) == ("test", "brook-drive")
    assert '"One pool process drains every run when it closes."' in evidence
    assert artifact == 'tests/test_pool_count.py\ndef test_one_pool_process_per_run():\n    """rule/bbbbbbb: one pool process drains every run."""\n    assert pool_processes(runs=2) == 2'
    assert check(capsys, CheckShell(rows, diff=per_run)) == (1, lines)
    assert len(judged.asked) == 1


def test_a_pr_in_another_checkout_is_graded_against_the_drive_register(package: Path, state: Path, registered: Path, capsys) -> None:
    uncited = CLEAN.replace("- bbbbbbb: Drain the pool when a run closes.", "- ccccccc: Cards quote the words they ask about.")

    assert check(capsys, CheckShell(pinned(package), body=uncited), "--incident", "--repo", "/second-repo") == (1, [UNCITED])


def test_no_drive_running_the_program_passes_with_one_blocker(package: Path, state: Path, capsys) -> None:
    shell = CheckShell(pinned(package), stamp="0" * 12)

    assert check(capsys, shell) == (0, ["BLOCKER the register: no drive registry entry runs brook"])
    assert not [argv for argv in shell.calls if argv[:2] == ["ccn", "-R"]]


def test_refusals_print_in_the_contracts_order_and_an_incident_fix_meets_only_the_first_three(package: Path, state: Path, registered: Path, capsys) -> None:
    body = "## Summary\n\nWe decided to park the worker.\n"
    diff = BUCKET_DIFF.replace("+    self.drain();", '+    park("parked worker");')
    major = {"seq": 12, "kind": "review", "lane": "finder-desk", "text": f"finder: major {HEAD[:7]} 1 open"}
    shell = CheckShell(pinned(package), body=body, diff=diff, records=[LANE_HEAD, major], reviews=[])

    assert check(capsys, shell) == (
        1,
        [
            "RESERVED-NO-ANSWER reserved/cloud-delete\tNothing deletes a cloud bucket without my word.",
            f"FINDER-MAJOR {HEAD[:7]}",
            "NAME-HIT rule/aaaaaaa src/relay/pool.rs:2 parked worker",
            "LANE-DECIDED We decided to park the worker.",
            "CONTRACT-MISSING lane-a: no fidelity desk has graded a read-back",
        ],
    )
    assert check(capsys, shell, "--incident") == (
        1,
        [UNCITED, "RESERVED-NO-ANSWER reserved/cloud-delete\tNothing deletes a cloud bucket without my word."],
    )


@pytest.mark.parametrize(
    ("failing", "blocker"),
    [
        ("gh pr view", "BLOCKER acme/app#12: "),
        ("git -C /repo diff", "BLOCKER the diff of acme/app#12: "),
        ("git -C /repo for-each-ref", "BLOCKER the register: "),
        ("cci tail", "BLOCKER the PR's cci records: "),
    ],
)
def test_a_failed_read_passes_with_one_blocker_line(package: Path, state: Path, registered: Path, capsys, failing: str, blocker: str) -> None:
    code, lines = check(capsys, CheckShell(pinned(package), failing=(failing,)))

    assert code == 0
    assert len(lines) == 1
    assert lines[0].startswith(blocker)


def test_a_pr_that_names_no_number_is_a_usage_error(package: Path, state: Path, registered: Path, capsys) -> None:
    assert ran(capsys, CheckShell(pinned(package)), "check-pr", "--pr", "acme/app#x", "--head", HEAD) == (2, "")


GRANT_TICKER = changed("go/sand/cmd/runner_grant.go", [], ["func renew(path string) {", "\tticker := time.NewTicker(15 * time.Minute)", "\tdefer ticker.Stop()", "}"])
BANNED_CELLS = {
    "go ticker": (GRANT_TICKER, ("timer",), "go/sand/cmd/runner_grant.go", 2),
    "tokio interval": (changed("rust/src/renew.rs", [], ["let mut tick = tokio::time::interval(Duration::from_secs(900));"]), ("timer",), "rust/src/renew.rs", 1),
    "setInterval in a test": (changed("api/src/grant.test.ts", [], ["const handle = setInterval(rewriteGrant, 15 * 60_000);"]), ("timer",), "api/src/grant.test.ts", 1),
    "sleep in a for loop": (changed("go/sand/cmd/wait.go", [], ["func waitReady() {", "\tfor {", "\t\tif ready() {", "\t\t\treturn", "\t\t}", "\t\ttime.Sleep(time.Second)", "\t}", "}"]), ("poll loop",), "go/sand/cmd/wait.go", 6),
    "setTimeout rescheduling itself": (changed("dashboard/src/poll.ts", [], ["function refresh() {", "  load();", "  setTimeout(refresh, 5000);", "}"]), ("poll loop",), "dashboard/src/poll.ts", 3),
    "grant written by Go": (changed("go/sand/cmd/runner_grant.go", [], ["if err := os.WriteFile(grantPath, []byte(grant), 0o600); err != nil {"]), ("credential written to a file",), "go/sand/cmd/runner_grant.go", 1),
    "signing key over three lines": (changed("rust/src/keys.rs", [], ["fs::write(", "    &path,", "    signing_key.to_bytes(),", ")?;"]), ("credential written to a file",), "rust/src/keys.rs", 1),
    "grant opened for writing": (changed("go/sand/cmd/runner_grant.go", [], ["f, err := os.OpenFile(grantPath, os.O_CREATE|os.O_WRONLY, 0o600)"]), ("credential written to a file",), "go/sand/cmd/runner_grant.go", 1),
    "timer that writes a token": (changed("api/src/renew.ts", [], ["setInterval(() => writeFileSync(tokenPath, token), 1000);"]), ("credential written to a file", "timer"), "api/src/renew.ts", 1),
    "cargo test from a tool script": (changed("tools/check.sh", [], ["cargo test -p sandsql-server"]), ("local exec",), "tools/check.sh", 1),
    "go test from a hook": (changed(".claude/hooks/verify.py", [], ['subprocess.run(["go", "test", "./..."], check=True)']), ("local exec",), ".claude/hooks/verify.py", 1),
}
BENIGN_CELLS = {
    "one-shot sleep with its reason": changed("go/sand/cmd/dial.go", [], ["func dial() {", "\t// the socket binds a beat after listen returns", "\ttime.Sleep(50 * time.Millisecond)", "\tconnect()", "}"]),
    "timer deletion": changed("go/sand/cmd/runner_grant.go", ["\tticker := time.NewTicker(15 * time.Minute)"], ["\t<-done"]),
    "one-shot promise delay": changed("dashboard/src/wait.ts", [], ["async function settle(ms: number) {", "  await new Promise((resolve) => setTimeout(resolve, ms));", "}"]),
    "token opened to read": changed("go/sand/cmd/runner_grant.go", [], ["f, err := os.OpenFile(tokenPath, os.O_RDONLY, 0)"]),
    "state file write": changed("go/sand/cmd/state.go", [], ["os.WriteFile(statePath, data, 0o644)"]),
    "cache key file write": changed("api/src/cache.ts", [], ["writeFileSync(cacheKeyPath, digest);"]),
    "cargo test in CI config": changed(".buildkite/pipeline.yml", [], ["  - cargo test -p sandsql-server"]),
    "cargo test in a doc": changed("docs/testing.md", [], ["Run `cargo test -p sandsql-server` in CI."]),
    "go test named in product code": changed("go/sand/internal/build/plan.go", [], ['steps = append(steps, "go test ./...")']),
}
TIMER_ANSWER_ROW = {"kind": "pick", "status": "in-force", "words": "The signal keeps its keep-alive timer every 15 s.", "answer": '["abcabca"]', "lanes": '["all"]', "paths": "none"}


def answering(judged: Judge, verdict: str | None) -> None:
    judged.verdict = lambda kind, artifact: None if verdict is None else judge.Verdict(verdict, "time.NewTicker", "", "Nothing he said asks for a renewal timer.")


@pytest.mark.parametrize(("diff", "shapes", "path", "line"), list(BANNED_CELLS.values()), ids=list(BANNED_CELLS))
def test_a_banned_mechanism_on_an_added_line_warns_with_its_rule_and_line(
    package: Path, state: Path, registered: Path, capsys, judged: Judge, diff: str, shapes: tuple[str, ...], path: str, line: int
) -> None:
    answering(judged, "yes")
    code, lines = check(capsys, CheckShell(pinned(package), diff=DIFF + diff))

    assert code == 1
    assert [found.startswith(f"MECHANISM-BANNED {shape} {path}:{line} ") for found, shape in zip(lines, shapes, strict=True)] == [True] * len(shapes)
@pytest.mark.parametrize("diff", list(BENIGN_CELLS.values()), ids=list(BENIGN_CELLS))
def test_a_neighbour_of_a_banned_mechanism_warns_nothing(package: Path, state: Path, registered: Path, capsys, diff: str) -> None:
    assert check(capsys, CheckShell(pinned(package), diff=DIFF + diff)) == (0, [])


def test_a_timer_warns_only_when_the_judge_reads_it_as_named_by_neither_his_words_nor_the_plan(
    package: Path, state: Path, registered: Path, capsys, judged: Judge
) -> None:
    rows = pinned(package) | {"rule/abcabca": TIMER_ANSWER_ROW}
    shell = CheckShell(rows, diff=DIFF + GRANT_TICKER, body=citing("- abcabca: the signal keeps its keep-alive timer."))

    answering(judged, "no")
    assert check(capsys, shell) == (0, [])
    [(_, evidence, artifact, _)] = [asked for asked in judged.asked if asked[0] == "mechanism"]
    assert TIMER_ANSWER_ROW["words"] in evidence
    assert "go/sand/cmd/runner_grant.go:2 timer: ticker := time.NewTicker(15 * time.Minute)" in artifact
    answering(judged, None)
    assert check(capsys, shell) == (0, [])
    answering(judged, "yes")
    assert check(capsys, shell) == (
        1,
        ["MECHANISM-BANNED timer go/sand/cmd/runner_grant.go:2 `time.NewTicker`\tNothing he said asks for a renewal timer. Remove it, or bring the owner the case for it."],
    )
