from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import ledger
import pytest
from test_rulings import DIFF, RegisterShell, home, package, ran, registered, seeded, state

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
READBACK = {"seq": 9, "kind": "review", "lane": "fidelity-desk", "text": "readback: ok lane-a 0123456789ab"}
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
    ) -> None:
        super().__init__(rows, answers=answers, diff=diff)
        self.body = body
        self.pr_records = [LANE_HEAD, FINDER_OK] if records is None else records
        self.reviews = [READBACK] if reviews is None else reviews
        self.failing = failing

    def run(self, argv: list[str], stdin: str | None = None, cwd: Path | None = None) -> str:
        if any(" ".join(argv).startswith(prefix) for prefix in self.failing):
            raise subprocess.CalledProcessError(1, argv)
        match argv:
            case ["gh", "pr", "view", "12", "--repo", "acme/app", "--json", "body,baseRefName"]:
                return json.dumps({"body": self.body, "baseRefName": "main"})
            case ["cci", "tail", "--drive", "brook-cci", "--since", "168h", "--json", "--budget", _, "--pr", "acme/app#12"]:
                return "".join(json.dumps(record) + "\n" for record in self.pr_records)
            case ["cci", "tail", "--drive", "brook-cci", "--since", "168h", "--json", "--budget", _, "--kind", "review"]:
                return "".join(json.dumps(record) + "\n" for record in self.reviews)
        return super().run(argv, stdin, cwd)


def citing(*lines: str) -> str:
    cited = "- bbbbbbb: Drain the pool when a run closes.\n"
    return CLEAN.replace(cited, cited + "".join(f"{line}\n" for line in lines))


def pinned(package: Path, corpus: str = CORPUS, pin: str | None = None) -> dict[str, dict[str, str]]:
    (package / "plan" / "B-tests.md").write_text(corpus)
    rows = seeded(package) | {"reserved/cloud-delete": dict(RESERVED)}
    rows["corpus/b-tests"]["sha256"] = pin or hashlib.sha256(corpus.encode()).hexdigest()
    return rows


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

    assert check(capsys, CheckShell(pinned(package), diff=TEST_DIFF), "--plan", str(plan)) == (
        1,
        ["TEST-REMOVED test_drain_on_close", "TEST-REMOVED test_pool_closes_cleanly"],
    )
    authorized = CheckShell(pinned(package) | {"rule/fffffff": dropped}, body=citing("- fffffff: drop both tests."), diff=TEST_DIFF)
    assert check(capsys, authorized, "--plan", str(plan)) == (0, [])


def test_a_corpus_that_moved_holds_the_head_and_only_blocks_an_incident_fix(package: Path, state: Path, registered: Path, capsys) -> None:
    corpus = package / "plan" / "B-tests.md"
    moved = pinned(package, pin="0" * 64)

    assert check(capsys, CheckShell(moved, diff=TEST_DIFF)) == (1, [f"TESTS-CORPUS {corpus} sha256 differs from the register's pin"])
    assert check(capsys, CheckShell(moved, diff=TEST_DIFF), "--incident") == (0, [f"BLOCKER TESTS-CORPUS {corpus} sha256 differs from the register's pin"])
    assert check(capsys, CheckShell(pinned(package), diff=TEST_DIFF), "--incident") == (1, ["TEST-REMOVED test_drain_on_close"])
    assert check(capsys, CheckShell(moved)) == (0, [])


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
    body = CLEAN.replace("The pool drains when a run closes.", "We decided to keep the retry loop.\nI chose the drain path (bbbbbbb).")

    assert check(capsys, CheckShell(pinned(package), body=body)) == (1, ["LANE-DECIDED We decided to keep the retry loop."])


def test_the_lane_of_the_head_needs_an_accepted_read_back(package: Path, state: Path, registered: Path, capsys) -> None:
    opened = {"seq": 15, "kind": "opened", "lane": "a0123456789abcdef", "text": "opened acme/app#12"}

    assert check(capsys, CheckShell(pinned(package), reviews=[])) == (1, ["CONTRACT-MISSING lane-a"])
    assert check(capsys, CheckShell(pinned(package), reviews=[dict(READBACK, text="readback: ok lane-b 0123456789ab")])) == (1, ["CONTRACT-MISSING lane-a"])
    assert check(capsys, CheckShell(pinned(package), records=[LANE_HEAD, FINDER_OK, opened])) == (0, [])
    assert check(capsys, CheckShell(pinned(package), records=[FINDER_OK])) == (0, ["BLOCKER no cci record names the lane of acme/app#12"])


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
            "CONTRACT-MISSING lane-a",
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
