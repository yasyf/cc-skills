package rules

import (
	"reflect"
	"testing"
)

const (
	deletion = `diff --git a/dashboard/src/iris/SessionThreadFollower.tsx b/dashboard/src/iris/SessionThreadFollower.tsx
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
`
	rename = `diff --git a/x.py b/x.py
index 1..2 100644
--- a/x.py
+++ b/x.py
@@ -1,2 +1,2 @@
-def follower():
+def follower(session):
     return session
`
	testsOnly = `diff --git a/plugins/long-running/tests/test_lr_rules.py b/plugins/long-running/tests/test_lr_rules.py
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
`
)

func evaluate(t *testing.T, rule, ref, diff string) Check {
	t.Helper()
	check, err := Evaluate(rule, ref, Parse(diff))
	if err != nil {
		t.Fatal(err)
	}
	return check
}

func TestADeletedSymbolReadsAsDeletedNeverAsLeftAlone(t *testing.T) {
	if touch := TouchOf(Parse(deletion), "SessionThreadFollower"); touch != Deletes {
		t.Fatalf("touch = %s", touch)
	}
	want := Check{"design/s3", false, "deletes SessionThreadFollower"}
	if got := evaluate(t, "design/s3", "symbol-kept:SessionThreadFollower", deletion); got != want {
		t.Fatalf("got %+v, want %+v", got, want)
	}
}

func TestASymbolTheDiffNeverNamesIsUntouchedAndKept(t *testing.T) {
	want := Check{"r", true, "untouched Shell"}
	if got := evaluate(t, "r", "symbol-kept:Shell", deletion); got != want {
		t.Fatalf("got %+v, want %+v", got, want)
	}
}

func TestASymbolEditedInPlaceIsChangedAndAnAddedOneIsAdded(t *testing.T) {
	if touch := TouchOf(Parse(rename), "follower"); touch != Changes {
		t.Fatalf("follower = %s", touch)
	}
	if touch := TouchOf(Parse(rename), "session"); touch != Adds {
		t.Fatalf("session = %s", touch)
	}
}

func TestAWordInsideALongerNameIsNotTheSymbol(t *testing.T) {
	if touch := TouchOf(Parse(deletion), "Thread"); touch != Untouched {
		t.Fatalf("touch = %s", touch)
	}
}

func TestParseKeepsRemovedAndAddedLinesPerFile(t *testing.T) {
	changes := Parse(deletion)
	if len(changes) != 2 || changes[0].Path != "dashboard/src/iris/SessionThreadFollower.tsx" || changes[1].Path != "dashboard/src/iris/App.tsx" {
		t.Fatalf("changes = %+v", changes)
	}
	removed := []string{`import { SessionThreadFollower } from "./SessionThreadFollower";`, "export const follower = <SessionThreadFollower />;"}
	if !reflect.DeepEqual(changes[1].Removed, removed) {
		t.Fatalf("removed = %q", changes[1].Removed)
	}
	if !reflect.DeepEqual(changes[1].Added, []string{"export const follower = null;"}) {
		t.Fatalf("added = %q", changes[1].Added)
	}
}

func TestTestsAndCIOnlyLandsWithoutATap(t *testing.T) {
	want := Check{"rule/test-only", true, "touches only tests and CI"}
	if got := evaluate(t, "rule/test-only", "test-only", testsOnly); got != want {
		t.Fatalf("got %+v, want %+v", got, want)
	}
}

func TestASourceFileAmongTheTestsNeedsTheTap(t *testing.T) {
	want := Check{"rule/test-only", false, "touches dashboard/src/iris/App.tsx, dashboard/src/iris/SessionThreadFollower.tsx"}
	if got := evaluate(t, "rule/test-only", "test-only", testsOnly+deletion); got != want {
		t.Fatalf("got %+v, want %+v", got, want)
	}
}

func TestAnEmptyDiffTruesTheBranchUpAndLandsWithoutATap(t *testing.T) {
	want := Check{"rule/true-up", true, "changes nothing against its base"}
	if got := evaluate(t, "rule/true-up", "empty-diff", ""); got != want {
		t.Fatalf("got %+v, want %+v", got, want)
	}
	if evaluate(t, "rule/true-up", "empty-diff", rename).Met {
		t.Fatal("a diff with hunks reads as empty")
	}
}

func TestAnUnknownPredicateIsADefectNotAPass(t *testing.T) {
	if _, err := Evaluate("r", "vibes", nil); err == nil {
		t.Fatal("an unknown predicate passed")
	}
	if _, err := Evaluate("r", "symbol-kept", nil); err == nil {
		t.Fatal("symbol-kept with no symbol passed")
	}
}
