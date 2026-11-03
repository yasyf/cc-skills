package gates

import (
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	repo    = "org/repo"
	oldHead = "aa11bb22cc33dd44ee55ff6677889900aabbccdd"
	newHead = "e718a7434fa01ee9835f992a9a647a825350732c"
)

var (
	t0    = time.Date(2026, 10, 30, 21, 0, 0, 0, time.UTC)
	green = machine.Facts{Repo: repo, Number: 1, Head: newHead, Base: "dev", Lane: "l", CI: machine.CISuccess, Mergeable: machine.MergeableClean, Approved: true, Finder: machine.FinderClear, Fidelity: machine.FidelityPass, Tap: machine.TapTapped}
)

func withFacts(t *testing.T, number int, facts map[string]string) *store.Store {
	t.Helper()
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.Close() })
	for kind, value := range facts {
		if _, err := s.PutFact(repo, number, oldHead, kind, value, t0); err != nil {
			t.Fatal(err)
		}
	}
	return s
}

func factsOn(t *testing.T, s *store.Store, number int, head string) map[string]string {
	t.Helper()
	facts, err := s.Facts(repo, number, head)
	if err != nil {
		t.Fatal(err)
	}
	return facts
}

const tappedDiff = `diff --git a/list.go b/list.go
index 1111111..2222222 100644
--- a/list.go
+++ b/list.go
@@ -10,6 +10,7 @@ func List() {
 	rows := read()
+	rows = live(rows, "a b")
 	return rows
`

func TestTheSameOwnDiffOnAMovedBaseHashesTheSame(t *testing.T) {
	moved := strings.NewReplacer("1111111..2222222", "3333333..4444444", "-10,6 +10,7", "-42,6 +44,7").Replace(tappedDiff)
	if DiffHash(moved) != DiffHash(tappedDiff) {
		t.Fatal("a diff that moved only its line numbers and blobs hashed differently")
	}
}

func TestAnyOtherChangeToTheDiffHashesDifferently(t *testing.T) {
	for name, changed := range map[string]string{
		"whitespace inside a line": strings.Replace(tappedDiff, `"a b"`, `"ab"`, 1),
		"an added line":            tappedDiff + "+\tlog(rows)\n",
		"its function context":     strings.Replace(tappedDiff, "func List()", "func Lists()", 1),
	} {
		t.Run(name, func(t *testing.T) {
			if DiffHash(changed) == DiffHash(tappedDiff) {
				t.Fatal("hashed the same")
			}
		})
	}
}

func TestATapCarriesOnlyWhenTheNewHeadMeansTheSameAsTheTappedOne(t *testing.T) {
	tapped := map[string]string{DiffFact: DiffHash(tappedDiff)}
	judged := func(behavior, since, fidelity string) map[string]string {
		return map[string]string{DiffFact: "other", BehaviorFact: behavior, SinceFact: since, fidelityFact: fidelity}
	}
	for name, c := range map[string]struct {
		atTap, atHead map[string]string
		want          string
	}{
		"the same diff":                       {tapped, map[string]string{DiffFact: DiffHash(tappedDiff)}, BySameDiff},
		"fidelity judged it the same":         {tapped, judged(BehaviorSame, oldHead, "pass"), ByFidelity},
		"fidelity judged it changed":          {tapped, judged(BehaviorChanged, oldHead, "pass"), ""},
		"the same since a later carried head": {tapped, judged(BehaviorSame, "bb22cc33dd44", "pass"), ""},
		"the same but a plan line is missing": {tapped, judged(BehaviorSame, oldHead, "fail"), ""},
		"a different diff not judged yet":     {tapped, map[string]string{DiffFact: "other", fidelityFact: "pass"}, ""},
		"no diff recorded at the tapped head": {map[string]string{}, map[string]string{DiffFact: ""}, ""},
	} {
		t.Run(name, func(t *testing.T) {
			if got := CarriedBy(oldHead, c.atTap, c.atHead); got != c.want {
				t.Fatalf("carried by %q, want %q", got, c.want)
			}
		})
	}
}

func TestACarriedTapAdmitsNothingUntilTheNewHeadIsGreen(t *testing.T) {
	carried := green
	carried.CI = machine.CIPending
	if got := Missing(carried); !slices.Equal(got, []string{"CI is not green"}) {
		t.Fatalf("missing %v", got)
	}
	if carried.Landable() {
		t.Fatal("a pending head must not land")
	}
}

func TestMissingNamesEveryOpenGate(t *testing.T) {
	f := machine.Facts{Repo: repo, Number: 1, Head: newHead, CI: machine.CIFailure, Mergeable: machine.MergeableConflict, Finder: machine.FinderAsk, Fidelity: machine.FidelityPass, Hold: machine.MoverRoot, Carries: []int{2}}
	want := []string{"CI is not green", "mergeability is conflict", "branch carries #2", "a grade needs a verdict", "no approving review", "no tap", "held by root"}
	if got := Missing(f); !slices.Equal(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestNothingMissingMeansLandable(t *testing.T) {
	if got := Missing(green); len(got) != 0 {
		t.Fatalf("missing %v", got)
	}
	if !green.Landable() {
		t.Fatal("a head with nothing missing must land")
	}
}

func TestMissingNamesTheConditionATapWaitsOn(t *testing.T) {
	f := green
	f.Tap, f.Condition = machine.TapConditional, "only if covered"
	if got := Missing(f); !slices.Equal(got, []string{"tap given only if covered"}) {
		t.Fatalf("missing %v", got)
	}
}

func TestAnUngradedHeadSaysSo(t *testing.T) {
	f := green
	f.Finder = ""
	if got := Missing(f); !slices.Equal(got, []string{"not graded"}) {
		t.Fatalf("missing %v", got)
	}
}
