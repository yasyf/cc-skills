"""A rule's predicate reads the hunks: a deleted symbol is a deletion, and the no-tap classes are decided by the files the diff touches."""

from __future__ import annotations

import pytest
from lr import rules

DELETION = """\
diff --git a/dashboard/src/iris/SessionThreadFollower.tsx b/dashboard/src/iris/SessionThreadFollower.tsx
deleted file mode 100644
index 3f2a1b0..0000000
--- a/dashboard/src/iris/SessionThreadFollower.tsx
+++ /dev/null
@@ -1,4 +0,0 @@
-export function SessionThreadFollower() {
-  return null;
-}
-
diff --git a/dashboard/src/iris/App.tsx b/dashboard/src/iris/App.tsx
index 1111111..2222222 100644
--- a/dashboard/src/iris/App.tsx
+++ b/dashboard/src/iris/App.tsx
@@ -1,3 +1,2 @@
-import { SessionThreadFollower } from "./SessionThreadFollower";
 import { Shell } from "./Shell";
-export const follower = <SessionThreadFollower />;
+export const follower = null;
"""

RENAME = """\
diff --git a/x.py b/x.py
index 1..2 100644
--- a/x.py
+++ b/x.py
@@ -1,2 +1,2 @@
-def follower():
+def follower(session):
     return session
"""

TESTS_ONLY = """\
diff --git a/plugins/long-running/tests/test_lr_rules.py b/plugins/long-running/tests/test_lr_rules.py
index 1..2 100644
--- a/plugins/long-running/tests/test_lr_rules.py
+++ b/plugins/long-running/tests/test_lr_rules.py
@@ -1 +1,2 @@
 import pytest
+import rules
diff --git a/.github/workflows/long-running-ci.yml b/.github/workflows/long-running-ci.yml
index 1..2 100644
--- a/.github/workflows/long-running-ci.yml
+++ b/.github/workflows/long-running-ci.yml
@@ -1 +1 @@
-name: ci
+name: long-running CI
"""


def test_a_deleted_symbol_reads_as_deleted_never_as_left_alone():
    changes = rules.parse(DELETION)

    assert rules.touch(changes, "SessionThreadFollower") == "deletes"
    verdict = rules.check("design/s3", "symbol-kept:SessionThreadFollower", changes)
    assert (verdict.met, verdict.how) == (False, "deletes SessionThreadFollower")


def test_a_symbol_the_diff_never_names_is_untouched_and_kept():
    changes = rules.parse(DELETION)

    assert rules.touch(changes, "Shell") == "untouched"
    assert rules.check("r", "symbol-kept:Shell", changes) == rules.Check("r", True, "untouched Shell")


def test_a_symbol_edited_in_place_is_changed():
    assert rules.touch(rules.parse(RENAME), "follower") == "changes"


def test_an_added_symbol_is_added():
    assert rules.touch(rules.parse(RENAME), "session") == "adds"


def test_parse_keeps_removed_and_added_lines_per_file():
    changes = rules.parse(DELETION)

    assert [change.path for change in changes] == ["dashboard/src/iris/SessionThreadFollower.tsx", "dashboard/src/iris/App.tsx"]
    assert changes[1].removed == ('import { SessionThreadFollower } from "./SessionThreadFollower";', "export const follower = <SessionThreadFollower />;")
    assert changes[1].added == ("export const follower = null;",)


def test_tests_and_ci_only_lands_without_a_tap():
    verdict = rules.check("rule/test-only", "test-only", rules.parse(TESTS_ONLY))

    assert (verdict.met, verdict.how) == (True, "touches only tests and CI")


def test_a_source_file_among_the_tests_needs_the_tap():
    verdict = rules.check("rule/test-only", "test-only", rules.parse(TESTS_ONLY + DELETION))

    assert verdict.met is False
    assert verdict.how == "touches dashboard/src/iris/App.tsx, dashboard/src/iris/SessionThreadFollower.tsx"


def test_an_empty_diff_trues_the_branch_up_and_lands_without_a_tap():
    assert rules.check("rule/true-up", "empty-diff", rules.parse("")) == rules.Check("rule/true-up", True, "changes nothing against its base")
    assert rules.check("rule/true-up", "empty-diff", rules.parse(RENAME)).met is False


def test_an_unknown_predicate_is_a_defect_not_a_pass():
    with pytest.raises(KeyError):
        rules.check("r", "vibes", [])
