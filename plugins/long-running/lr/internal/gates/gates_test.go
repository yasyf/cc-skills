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
		"the same diff":                             {tapped, map[string]string{DiffFact: DiffHash(tappedDiff)}, BySameDiff},
		"fidelity judged it the same":               {tapped, judged(BehaviorSame, oldHead, "pass"), ByFidelity},
		"fidelity judged it changed":                {tapped, judged(BehaviorChanged, oldHead, "pass"), ""},
		"the same since a later carried head":       {tapped, judged(BehaviorSame, "bb22cc33dd44", "pass"), ""},
		"the same but a plan line is missing":       {tapped, judged(BehaviorSame, oldHead, "fail"), ""},
		"the same with its plan line overridden":    {tapped, map[string]string{DiffFact: "other", BehaviorFact: BehaviorSame, SinceFact: oldHead, fidelityFact: "fail", verdictFact: "accept"}, ByFidelity},
		"changed though a plan line is overridden":  {tapped, map[string]string{DiffFact: "other", BehaviorFact: BehaviorChanged, SinceFact: oldHead, fidelityFact: "fail", verdictFact: "accept"}, ""},
		"the same since a shorter form of the head": {tapped, judged(BehaviorSame, oldHead[:7], "pass"), ByFidelity},
		"the same since nothing named":              {tapped, judged(BehaviorSame, "", "pass"), ""},
		"a different diff not judged yet":           {tapped, map[string]string{DiffFact: "other", fidelityFact: "pass"}, ""},
		"no diff recorded at the tapped head":       {map[string]string{}, map[string]string{DiffFact: ""}, ""},
	} {
		t.Run(name, func(t *testing.T) {
			if got, _ := CarriedBy(oldHead, newHead, c.atTap, c.atHead, nil); got != c.want {
				t.Fatalf("carried by %q, want %q", got, c.want)
			}
		})
	}
}

func TestAHeadRecordsClassDecidesWhetherHisTapCarries(t *testing.T) {
	const middle, far = "bb22cc33dd44", "cc33dd44ee55"
	link := func(head, class, from, why string) []store.HeadFact {
		facts := []store.HeadFact{{Head: head, Kind: DeltaFromFact, Value: from}}
		if class != "" {
			facts = append(facts, store.HeadFact{Head: head, Kind: DeltaFact, Value: class}, store.HeadFact{Head: head, Kind: DeltaWhyFact, Value: why})
		}
		return facts
	}
	same, changed := map[string]string{DiffFact: "tapped"}, map[string]string{DiffFact: "other"}
	for name, c := range map[string]struct {
		atHead   map[string]string
		deltas   []store.HeadFact
		by, back string
	}{
		"a review-fix head carries":             {changed, link(newHead, DeltaReviewFix, oldHead, "the finder's nit"), "review-fix", ""},
		"a fix he ordered carries":              {changed, link(newHead, DeltaOrderedFix, oldHead, "his fix"), "ordered-fix", ""},
		"a behavior head returns saying so":     {changed, link(newHead, DeltaBehavior, oldHead, "drops archived chats"), "", "a behavior change at " + newHead[:7] + ": drops archived chats"},
		"a scope head returns saying so":        {changed, link(newHead, DeltaScope, oldHead, "adds the export"), "", "a scope change at " + newHead[:7] + ": adds the export"},
		"a rebase then a behavior head returns": {changed, slices.Concat(link(middle, DeltaRebase, oldHead, "onto dev"), link(newHead, DeltaBehavior, middle, "drops chats")), "", "a behavior change at " + newHead[:7] + ": drops chats"},
		"a behavior head then a rebase returns": {changed, slices.Concat(link(middle, DeltaBehavior, oldHead, "drops chats"), link(newHead, DeltaRebase, middle, "onto dev")), "", "a behavior change at " + middle[:7] + ": drops chats"},
		"a rebase then a review fix names both": {changed, slices.Concat(link(middle, DeltaRebase, oldHead, "onto dev"), link(newHead, DeltaReviewFix, middle, "nit")), "rebase,review-fix", ""},
		"a class on the same diff names itself": {same, link(newHead, DeltaRebase, oldHead, "onto dev"), "rebase", ""},
		"no class with the same diff carries":   {same, link(newHead, "", oldHead, ""), BySameDiff, ""},
		"a label cannot outrank the same diff":  {same, link(newHead, DeltaBehavior, oldHead, "said so"), BySameDiff, ""},
		"no class with a changed diff returns":  {changed, link(newHead, "", oldHead, ""), "", unjudged},
		"a class the rule does not know":        {changed, link(newHead, "cleanup", oldHead, "tidy"), "", unjudged},
		"a classed head with an unclassed one":  {changed, slices.Concat(link(middle, "", oldHead, ""), link(newHead, DeltaRebase, middle, "onto dev")), "", unjudged},
		"a chain that never reaches his tap":    {changed, link(newHead, DeltaReviewFix, far, "nit"), "", unjudged},
		"heads that point at each other":        {changed, slices.Concat(link(middle, DeltaRebase, newHead, "back"), link(newHead, DeltaRebase, middle, "forth")), "", unjudged},
		"a class beside fidelity judging a change": {
			map[string]string{DiffFact: "other", BehaviorFact: BehaviorChanged, SinceFact: oldHead, ChangedFact: "drops chats"},
			link(newHead, DeltaReviewFix, oldHead, "nit"), "", "fidelity judged a change: drops chats",
		},
		"a behavior class beside fidelity judging it the same": {
			map[string]string{DiffFact: "other", BehaviorFact: BehaviorSame, SinceFact: oldHead, fidelityFact: "pass"},
			link(newHead, DeltaBehavior, oldHead, "drops chats"), "", "a behavior change at " + newHead[:7] + ": drops chats",
		},
		"no class with fidelity judging it the same": {
			map[string]string{DiffFact: "other", BehaviorFact: BehaviorSame, SinceFact: oldHead, fidelityFact: "pass"},
			link(newHead, "", oldHead, ""), ByFidelity, "",
		},
		"a shorter form of each head": {changed, link(newHead[:7], DeltaRebase, oldHead[:7], "onto dev"), "rebase", ""},
	} {
		t.Run(name, func(t *testing.T) {
			by, back := CarriedBy(oldHead, newHead, same, c.atHead, c.deltas)
			if by != c.by || back != c.back {
				t.Fatalf("carried by %q, returned %q; want %q, %q", by, back, c.by, c.back)
			}
		})
	}
}

func TestCarryWhyWordsEachClassPlainly(t *testing.T) {
	if got, want := CarryWhy("rebase,review-fix"), "every change since the tapped head is a rebase or a review fix"; got != want {
		t.Fatalf("got %q, want %q", got, want)
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

const (
	pr46018 = "Nothing reads or writes the columns once #46014 ships, and they make the `teams` row look like it configures executor scaling when each pool does.\n\n" +
		"- **Availability:** an api pod still on an image before #46014 selects all four columns and would fail its `teams` queries, so this lands only after #46014 is live on every cluster.\n"
	pr48995 = "This PR is the API half; the clients switch to the number in the PRs above it, one release after this one is live.\n\n" +
		"- **Availability:** additive only. No client selects or sends `chatVersion` in this PR, so every released build keeps working; the next and old clients that select it ship one release after this API is live.\n"
	pr2436 = "- `lr board draw` posts a card change on the bus and `lr board link` on the Mac applies it through cc-present in order, resuming from a cursor.\n\n" +
		"Nothing calls either verb yet, so nothing live changes.\n"
)

func TestABodyThatWaitsToGoLiveAndNamesNoWayItIsSafeOnDevDoesNotLand(t *testing.T) {
	for name, c := range map[string]struct{ body, wait string }{
		"a wait to go live":         {"## Risk\n\nThis change goes live only after his pick on the card.\n", "This change goes live only after his pick on the card"},
		"#48995 waiting on a card":  {pr48995 + "\nThis PR waits on the owner's card before it goes live.\n", "This PR waits on the owner's card before it goes live"},
		"a hold until a release":    {"## Risk\n\n**Hold this PR until that release is live.**\n", "Hold this PR until that release is live"},
		"a stack held by the owner": {"> Held by the owner until Dec 2, 2026; needs evidence that old clients upgraded\n", "Held by the owner until Dec 2, 2026; needs evidence that old clients upgraded"},
	} {
		f := green
		f.UnsafeWait = UnsafeWait(c.body)
		if f.UnsafeWait != c.wait {
			t.Errorf("%s: wait = %q, want %q", name, f.UnsafeWait, c.wait)
		}
		if got := Missing(f); !slices.Equal(got, []string{UnsafeOnDev(c.wait)}) || f.Landable() {
			t.Errorf("%s: missing %v, landable %v", name, got, f.Landable())
		}
	}
}

func TestABodyThatNamesHowItIsSafeOnDevOrWaitsOnNothingLands(t *testing.T) {
	for name, body := range map[string]string{
		"#46018 as merged":            pr46018,
		"#48034 reader-first order":   "- This lands one release after #48176 deploys, so every api and dashboard that meets a txid-only position already reads it.\n",
		"#48995 as merged":            pr48995,
		"#2436 as merged":             pr2436,
		"#2436 waiting on his tap":    pr2436 + "\nIt waits for his tap before it goes live.\n",
		"#46018 behind a flag":        pr46018 + "\nThe drop sits behind a feature flag that stays off until #46014 is live.\n",
		"a wait for a page to settle": "It waits for the chat to open, about 10 s at the burst's p50.\n",
	} {
		if wait := UnsafeWait(body); wait != "" {
			t.Errorf("%s: refused for %q", name, wait)
		}
	}
}
