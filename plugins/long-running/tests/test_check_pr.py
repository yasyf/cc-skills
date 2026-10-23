from __future__ import annotations

import contextlib
import hashlib
import io
import json
import subprocess
from pathlib import Path

import ledger
import pytest
import rulings
from test_rulings import DIFF, REPO, RegisterShell, home, package, ran, registered, seeded, state

HEAD = "cafef00d"
CORPUS = "# Tests\n\n- test_drain_on_close: the pool is empty after a run closes.\n"
CLEAN = """## Summary

The pool drains when a run closes.

## Rulings

- bbbbbbb: Drain the pool when a run closes.

### In your words

The pool empties itself when a run ends.
"""
LANE_HEAD = {"seq": 10, "kind": "head", "lane": "lane-a", "text": f"lane-a {HEAD}"}
FINDER_OK = {"seq": 11, "kind": "review", "lane": "finder-a", "text": f"finder: ok {HEAD[:7]}"}
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
        self.pr_records = [LANE_HEAD, FINDER_OK] if records is None else records
        self.reviews = [graded(stamp)] if reviews is None else reviews
        self.failing = failing

    def run(self, argv: list[str], stdin: str | None = None, cwd: Path | None = None) -> str:
        if any(" ".join(argv).startswith(prefix) for prefix in self.failing):
            raise subprocess.CalledProcessError(1, argv)
        match argv:
            case ["gh", "pr", "view", "12", "--repo", "acme/app", "--json", "body,baseRefName"]:
                return json.dumps({"body": self.body, "baseRefName": "main"})
            case ["cci", "tail", "--drive", "brook-cci", "--since", since, "--json", "--budget", _, "--pr", "acme/app#12"]:
                return "".join(json.dumps(record) + "\n" for record in self.pr_records if record["seq"] > int(since))
            case ["cci", "tail", "--drive", "brook-cci", "--since", since, "--json", "--budget", _, "--kind", "review"]:
                return "".join(json.dumps(record) + "\n" for record in self.reviews if record["seq"] > int(since))
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


def test_a_head_that_breaks_nothing_prints_nothing_and_exits_zero(package: Path, state: Path, registered: Path, capsys) -> None:
    assert check(capsys, CheckShell(pinned(package))) == (0, [])


def test_an_anchored_row_the_diff_touches_needs_a_rulings_line(package: Path, state: Path, registered: Path, capsys) -> None:
    uncited = CLEAN.replace("- bbbbbbb: Drain the pool when a run closes.", "- ccccccc: Cards quote the words they ask about.")

    assert check(capsys, CheckShell(pinned(package), body=uncited)) == (1, ["RULINGS-UNCITED rule/bbbbbbb"])
    assert check(capsys, CheckShell(pinned(package), body=uncited.replace("- ccccccc:", "- rule/bbbbbbb:"))) == (0, [])


def test_a_reserved_touch_needs_an_answer_cited_or_tapped_for_this_pr(package: Path, state: Path, registered: Path, capsys) -> None:
    refused = (1, ["RESERVED-NO-ANSWER reserved/cloud-delete\tNothing deletes a cloud bucket without my word."])
    tapped = dict(BUCKET_ANSWER, title="Merge #12?", body="Owner tapped Merge.")
    touching = [LANE_HEAD, FINDER_OK, {"seq": 12, "kind": "review", "lane": "fidelity-desk", "text": f"fidelity: missing {HEAD[:7]} touches reserved/cloud-delete"}]

    assert check(capsys, CheckShell(pinned(package), diff=BUCKET_DIFF)) == refused
    assert check(capsys, CheckShell(pinned(package), records=touching)) == refused
    assert check(capsys, CheckShell(pinned(package), diff=BUCKET_DIFF, answers=[BUCKET_ANSWER])) == refused
    assert check(capsys, CheckShell(pinned(package), diff=BUCKET_DIFF, answers=[dict(tapped, title="Merge #13?")])) == refused
    assert check(capsys, CheckShell(pinned(package), body=citing("- abcdef1: the cleanup may delete the old bucket."), diff=BUCKET_DIFF, answers=[BUCKET_ANSWER])) == (0, [])
    assert check(capsys, CheckShell(pinned(package), diff=BUCKET_DIFF, answers=[tapped])) == (0, [])


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

    code, lines = check(capsys, CheckShell(pinned(package), body=uncited, diff=DIFF + TEST_DIFF, stamp="0" * 12), "--plan", str(plan))

    assert code == 1
    assert lines[0] == "RULINGS-UNCITED rule/bbbbbbb"
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
    major = {"seq": 12, "kind": "review", "lane": "finder-a", "text": f"finder: major {HEAD[:7]} 2 open"}
    fixed = {"seq": 13, "kind": "review", "lane": "finder-a", "text": f"finder: ok {HEAD[:7]}"}
    elsewhere = {"seq": 14, "kind": "review", "lane": "finder-a", "text": "finder: major 1234567 1 open"}

    assert check(capsys, CheckShell(pinned(package), records=[LANE_HEAD, FINDER_OK, major])) == (1, [f"FINDER-MAJOR {HEAD[:7]}"])
    assert check(capsys, CheckShell(pinned(package), records=[LANE_HEAD, major, fixed, elsewhere])) == (0, [])


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


def test_in_your_words_must_be_there_and_read_plainly(package: Path, state: Path, registered: Path, capsys) -> None:
    jargon = CLEAN.replace(
        "The pool empties itself when a run ends.", 'The lane moves `Pool.drain` into src/relay/pool.rs (bbbbbbb) and "keep the relay-server small".'
    )

    assert check(capsys, CheckShell(pinned(package), body=CLEAN.split("### In your words")[0])) == (1, ["RULINGS-PLAIN no ### In your words section"])
    assert check(capsys, CheckShell(pinned(package), body=jargon)) == (
        1,
        ['RULINGS-PLAIN a file path "src/relay/pool.rs"', 'RULINGS-PLAIN a code symbol "`Pool.drain`"', 'RULINGS-PLAIN a drive word "lane"'],
    )


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
    in_words = CLEAN.replace("The pool empties itself when a run ends.", "We decided to keep the retry loop.")
    assert check(capsys, CheckShell(pinned(package), body=in_words)) == (1, ["LANE-DECIDED We decided to keep the retry loop."])
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


def test_a_pr_in_another_checkout_is_graded_against_the_drive_register(package: Path, state: Path, registered: Path, capsys) -> None:
    uncited = CLEAN.replace("- bbbbbbb: Drain the pool when a run closes.", "- ccccccc: Cards quote the words they ask about.")

    assert check(capsys, CheckShell(pinned(package), body=uncited), "--repo", "/second-repo") == (1, ["RULINGS-UNCITED rule/bbbbbbb"])


def test_no_drive_running_the_program_passes_with_one_blocker(package: Path, state: Path, capsys) -> None:
    shell = CheckShell(pinned(package), stamp="0" * 12)

    assert check(capsys, shell) == (0, ["BLOCKER the register: no drive registry entry runs brook"])
    assert not [argv for argv in shell.calls if argv[:2] == ["ccn", "-R"]]


def test_refusals_print_in_the_contracts_order_and_an_incident_fix_meets_only_the_first_three(package: Path, state: Path, registered: Path, capsys) -> None:
    body = "## Summary\n\nWe decided to park the worker.\n"
    diff = BUCKET_DIFF.replace("+    self.drain();", '+    park("parked worker");')
    major = {"seq": 12, "kind": "review", "lane": "finder-a", "text": f"finder: major {HEAD[:7]} 1 open"}
    shell = CheckShell(pinned(package), body=body, diff=diff, records=[LANE_HEAD, major], reviews=[])

    assert check(capsys, shell) == (
        1,
        [
            "RULINGS-UNCITED rule/bbbbbbb",
            "RESERVED-NO-ANSWER reserved/cloud-delete\tNothing deletes a cloud bucket without my word.",
            f"FINDER-MAJOR {HEAD[:7]}",
            "NAME-HIT rule/aaaaaaa src/relay/pool.rs:2 parked worker",
            "RULINGS-PLAIN no ### In your words section",
            "LANE-DECIDED We decided to park the worker.",
            "CONTRACT-MISSING lane-a: no fidelity desk has graded a read-back",
        ],
    )
    assert check(capsys, shell, "--incident") == (
        1,
        ["RULINGS-UNCITED rule/bbbbbbb", "RESERVED-NO-ANSWER reserved/cloud-delete\tNothing deletes a cloud bucket without my word."],
    )


@pytest.mark.parametrize(
    ("failing", "blocker"),
    [
        ("gh pr view", "BLOCKER acme/app#12: "),
        ("gh api", "BLOCKER the compare of acme/app#12: "),
        ("ccn -R /repo ledger", "BLOCKER the register: "),
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
