package gates

import (
	"reflect"
	"slices"
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
	same  = map[string]string{oldHead: "p1", newHead: "p1"}
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

func TestATapCarriesToAPatchIdenticalHeadAndSaysWhereItCameFrom(t *testing.T) {
	s := withFacts(t, 45744, map[string]string{"tap": "tapped"})
	carried, err := CarryTap(s, repo, 45744, oldHead, newHead, same, t0)
	if err != nil || !carried {
		t.Fatalf("carried %v, err %v", carried, err)
	}
	want := map[string]string{"tap": "tapped", CarriedFact: oldHead}
	if got := factsOn(t, s, 45744, newHead); !reflect.DeepEqual(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}

func TestATapStaysBehind(t *testing.T) {
	cases := []struct {
		name  string
		facts map[string]string
		ids   map[string]string
	}{
		{"when the patch changed", map[string]string{"tap": "tapped"}, map[string]string{oldHead: "p1", newHead: "p2"}},
		{"when it was withdrawn", map[string]string{"tap": "withdrawn"}, same},
		{"when a patch id is unknown", map[string]string{"tap": "tapped"}, map[string]string{newHead: "p1"}},
		{"when it is conditional", map[string]string{"tap": "conditional", "condition": "only if covered"}, same},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			s := withFacts(t, 45470, c.facts)
			carried, err := CarryTap(s, repo, 45470, oldHead, newHead, c.ids, t0)
			if err != nil || carried {
				t.Fatalf("carried %v, err %v", carried, err)
			}
			if got := factsOn(t, s, 45470, newHead); len(got) != 0 {
				t.Fatalf("the new head gained %v", got)
			}
		})
	}
}

func TestAnExemptionCarriesWithItsTap(t *testing.T) {
	s := withFacts(t, 45744, map[string]string{"tap": "tapped", "exempt": "test-only"})
	if _, err := CarryTap(s, repo, 45744, oldHead, newHead, same, t0); err != nil {
		t.Fatal(err)
	}
	if got := factsOn(t, s, 45744, newHead)["exempt"]; got != "test-only" {
		t.Fatalf("exempt %q", got)
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
