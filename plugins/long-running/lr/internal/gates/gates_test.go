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

func TestHisTapCountsAtEveryLaterHeadUntilAPushChangesThePRFundamentally(t *testing.T) {
	const (
		tapped48159   = "bff7b78f1aeaa79635afa18616059d41a6b8a9df"
		restacked     = "f882f036d1c9525f67cb75a9eb82de4819aafdf8"
		testFixed     = "867b002a1fde5e2de8e40798ef1c8edb5da65bc2"
		landed48159   = "5266ad0e825321f92996008a919d4fb7752bf77d"
		beforeFloor   = "681434f62a9c498072a71a6a3b1e40c614df9c72"
		floorKeyed    = "cb60d5e91c969952464d053a11e654601605b981"
		opened52167   = "3d9f913ed6944cd9b45937e476b548fd86a49247"
		wiringDropped = "24f1286071665421b6628a2d3272b8dd6cc38cc8"
		redesigned    = "bb22cc33dd44ee55ff6677889900aabbccddeeff"
	)
	type push struct{ was, sha, class, why string }
	recorded := func(pushes ...push) []store.HeadFact {
		var facts []store.HeadFact
		for _, p := range pushes {
			facts = append(facts, store.HeadFact{Head: p.sha, Kind: DeltaFromFact, Value: p.was})
			if p.class != "" {
				facts = append(facts, store.HeadFact{Head: p.sha, Kind: DeltaFact, Value: p.class}, store.HeadFact{Head: p.sha, Kind: DeltaWhyFact, Value: p.why})
			}
		}
		return facts
	}
	same, changed := map[string]string{DiffFact: "tapped"}, map[string]string{DiffFact: "other"}
	fidelityChanged := map[string]string{DiffFact: "other", BehaviorFact: BehaviorChanged, SinceFact: beforeFloor, ChangedFact: "frees redo at the committed floor"}
	for name, c := range map[string]struct {
		tapped, head string
		atHead       map[string]string
		pushes       []push
		by, back     string
	}{
		"#48159 rebased, test-fixed and restacked (555986, 557596, 559864)": {tapped48159, landed48159, changed, []push{
			{tapped48159, restacked, "rebase", "onto dev"},
			{restacked, testFixed, "review-fix", "onto dev, plus one test fix"},
			{testFixed, landed48159, "rebase", "restacked onto dev"},
		}, ByTap, ""},
		"#48159's floor-key fix after his tap, which fidelity judged a change (546590)": {beforeFloor, floorKeyed, fidelityChanged, []push{
			{beforeFloor, floorKeyed, "behavior", "floor-key fix"},
		}, ByTap, ""},
		"#52167's test-only push with no class (560387)": {opened52167, wiringDropped, changed, []push{{opened52167, wiringDropped, "", ""}}, ByTap, ""},
		"a new head no record names":                     {opened52167, wiringDropped, changed, nil, ByTap, ""},
		"a push that changes the PR fundamentally": {opened52167, redesigned, changed, []push{
			{opened52167, wiringDropped, "review-fix", "nit"},
			{wiringDropped, redesigned, DeltaFundamental, "replaces the floor with a lease"},
		}, "", "a fundamental change at bb22cc3: replaces the floor with a lease"},
		"a fundamental change before his tap": {wiringDropped, redesigned, changed, []push{
			{opened52167, wiringDropped, DeltaFundamental, "a redesign he then tapped"},
			{wiringDropped, redesigned, "rebase", "onto dev"},
		}, ByTap, ""},
		"a label cannot outrank the same diff": {opened52167, redesigned, same, []push{{opened52167, redesigned, DeltaFundamental, "said so"}}, BySameDiff, ""},
		"heads that point at each other":       {opened52167, redesigned, changed, []push{{wiringDropped, redesigned, "", ""}, {redesigned, wiringDropped, "", ""}}, ByTap, ""},
	} {
		t.Run(name, func(t *testing.T) {
			by, back := CarriedBy(c.tapped, c.head, same, c.atHead, recorded(c.pushes...))
			if by != c.by || back != c.back {
				t.Fatalf("carried by %q, returned %q; want %q, %q", by, back, c.by, c.back)
			}
		})
	}
}

func TestCarryWhyWordsEachReasonPlainly(t *testing.T) {
	for by, want := range map[string]string{
		BySameDiff: "the PR's own diff is the same as at the tapped head",
		ByTap:      "his tap counts once for the PR, and no push since changed it fundamentally",
	} {
		if got := CarryWhy(by); got != want {
			t.Fatalf("CarryWhy(%q) = %q, want %q", by, got, want)
		}
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
	want := []string{"CI is not green", "mergeability is conflict", "branch carries #2", "a grade needs a verdict: finder ask and no counted finder-override at e718a74", "no approving review", "no tap", "held by root"}
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

func TestATapGivenWhileCIRunsLandsNothingUntilChecksAndGradesPass(t *testing.T) {
	early := green
	early.CI, early.Finder = machine.CIPending, ""
	if got := Missing(early); !slices.Equal(got, []string{"CI is not green", "not graded"}) || early.Landable() {
		t.Fatalf("missing %v, landable %v; want his tap to wait on CI and the grade", got, early.Landable())
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
	pr51861Risk   = "## Risk profile\n\n- **Availability:** client error text only. The server's 403 body is unchanged.\n\n"
	pr51861Design = "## Design\n\n" +
		"- Kept the door as it is: the machine profile reaches the developer role through `identity-devops`, which its trust already names (#49216).\n" +
		"- Rejected a server-side hint in the 403 body: it waits on a controller release, and the client already knows the role it needs.\n\n" +
		"<details>\n<summary>Full design</summary>\n\n`ssoSignedOut` takes an optional alternative.\n\n</details>\n\n" +
		"## Changes\n\n- `devboxRefusal` adds the admitted role to a `not_a_developer` answer.\n"
	ownWaitRisk = "## Risk profile\n\n- **Availability:** it waits on the controller release before it goes live.\n\n"
)

func TestABodyThatWaitsToGoLiveAndNamesNoWayItIsSafeOnDevDoesNotLand(t *testing.T) {
	for name, c := range map[string]struct{ body, wait string }{
		"a wait to go live":         {"## Risk\n\nThis change goes live only after his pick on the card.\n", "This change goes live only after his pick on the card"},
		"#48995 waiting on a card":  {pr48995 + "\nThis PR waits on the owner's card before it goes live.\n", "This PR waits on the owner's card before it goes live"},
		"a hold until a release":    {"## Risk\n\n**Hold this PR until that release is live.**\n", "Hold this PR until that release is live"},
		"a stack held by the owner": {"> Held by the owner until Dec 2, 2026; needs evidence that old clients upgraded\n", "Held by the owner until Dec 2, 2026; needs evidence that old clients upgraded"},
		"a wait on a named pick":    {"## Risk\n\nThis PR waits on the cutover pick before it goes live.\n", "This PR waits on the cutover pick before it goes live"},
		"a wait after a denied one": {"## Risk\n\nThis PR waits on no pick. It waits for his go before it lands.\n", "It waits for his go before it lands"},
		"#51861 own Risk wait":      {ownWaitRisk + pr51861Design, "it waits on the controller release before it goes live"},
		"a flag Design rejects":     {ownWaitRisk + "## Design\n\n- Rejected a feature flag: nothing reads the field until #1 ships.\n", "it waits on the controller release before it goes live"},
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
		"#49951 denying a wait":       "## Risk\n\nThis PR waits on no pick, and nothing about it changes once it merges.\n",
		"#51861 rejected hint":        pr51861Risk + pr51861Design,
	} {
		if wait := UnsafeWait(body); wait != "" {
			t.Errorf("%s: refused for %q", name, wait)
		}
	}
}

func TestAHoldHisTapEndsMissesOnlyHisTap(t *testing.T) {
	f := machine.Facts{CI: machine.CISuccess, Mergeable: machine.MergeableClean, Finder: machine.FinderClear, Fidelity: machine.FidelityPass, Approved: true, Tap: machine.TapTapped, Hold: machine.MoverRoot, HoldAwaitsTap: true}
	if got := Missing(f); !slices.Equal(got, []string{NoTap}) {
		t.Fatalf("Missing = %v", got)
	}
}

func TestCarriedOverrides(t *testing.T) {
	overridden := map[string]string{DiffFact: "d1", "finder": "ask", "fidelity": "pass", machine.OverrideFact("finder"): "9"}
	for name, tc := range map[string]struct {
		from, head map[string]string
		want       []string
	}{
		"the same diff, regraded the same": {overridden, map[string]string{DiffFact: "d1", "finder": "ask", "fidelity": "pass"}, []string{"finder"}},
		"the same diff, not graded yet":    {overridden, map[string]string{DiffFact: "d1"}, []string{"finder"}},
		"another diff":                     {overridden, map[string]string{DiffFact: "d2", "finder": "ask", "fidelity": "pass"}, nil},
		"a grader nothing overrode":        {overridden, map[string]string{DiffFact: "d1", "finder": "ask", "fidelity": "fail"}, nil},
		"already counted at this head":     {overridden, map[string]string{DiffFact: "d1", "finder": "ask", "fidelity": "pass", machine.OverrideFact("finder"): "9"}, nil},
		"the diff at the old head unread":  {map[string]string{"finder": "ask", machine.OverrideFact("finder"): "9"}, map[string]string{DiffFact: "d1"}, nil},
	} {
		t.Run(name, func(t *testing.T) {
			if got := CarriedOverrides(tc.from, tc.head); !slices.Equal(got, tc.want) {
				t.Fatalf("CarriedOverrides = %v, want %v", got, tc.want)
			}
		})
	}
}

func TestACountedFinderOverrideClearsItsGradeAndTheVerdictWaitNamesTheGradeLeft(t *testing.T) {
	f := green
	f.Finder, f.FinderOverride = machine.FinderAsk, true
	if got := Missing(f); len(got) != 0 {
		t.Fatalf("a counted finder-override at the head left %v", got)
	}
	f.Fidelity = machine.FidelityFail
	want := []string{"a grade needs a verdict: fidelity fail and no counted fidelity-override at e718a74"}
	if got := Missing(f); !slices.Equal(got, want) {
		t.Fatalf("got %v, want %v", got, want)
	}
}
