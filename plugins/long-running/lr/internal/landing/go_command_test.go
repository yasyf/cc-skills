package landing

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/app"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/config"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/scaletest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func TestTheGateCountsGoClassesBesideTheExemptGradesTheRunnerAdmitsAlone(t *testing.T) {
	path := filepath.Join(t.TempDir(), runnerFile)
	runner := `{"drive":"d","orca":{"briefs":{"repo":"o/r"},"desk_inbox":"i","run":"r"},"landing":{"go_classes":["class owner-pick"]}}`
	if err := os.WriteFile(path, []byte(runner), 0o600); err != nil {
		t.Fatal(err)
	}
	section, err := landingSection(path)
	if err != nil {
		t.Fatal(err)
	}
	if want := []string{"pure port, no interaction", "test-only or CI-only, no non-test code", "class owner-pick"}; !slices.Equal(section.ExemptGrades, want) {
		t.Fatalf("got %q, want %q", section.ExemptGrades, want)
	}
}

func TestTheSweepGatesOnlyAGreenApprovedHeadWithNoGoHoldOrTap(t *testing.T) {
	ready := machine.Facts{Number: 1, Head: "abc1234", CI: machine.CISuccess, Approved: true, Finder: machine.FinderClear, Fidelity: machine.FidelityPass}
	if !goReady(ready) {
		t.Fatalf("%+v should be gated", ready)
	}
	ungraded := ready
	ungraded.Finder = ""
	if !goReady(ungraded) {
		t.Fatalf("%+v should be gated: the gate reads its grades from the bus, which a grade reaches before the landing store does", ungraded)
	}
	for name, change := range map[string]func(*machine.Facts){
		"red":        func(f *machine.Facts) { f.CI = "" },
		"unapproved": func(f *machine.Facts) { f.Approved = false },
		"has a go":   func(f *machine.Facts) { f.Exempt = true },
		"held":       func(f *machine.Facts) { f.Hold = "landing" },
		"draft":      func(f *machine.Facts) { f.Draft = true },
		"closed":     func(f *machine.Facts) { f.Closed = true },
		"merged":     func(f *machine.Facts) { f.Merged = true },
		"tapped":     func(f *machine.Facts) { f.Tap = machine.TapTapped },
	} {
		t.Run(name, func(t *testing.T) {
			f := ready
			change(&f)
			if goReady(f) {
				t.Fatalf("%+v should not be gated", f)
			}
		})
	}
}

func sweepReads(t *testing.T, heads int, claimedInBody bool) (unchanged, oneChanged int) {
	t.Helper()
	t.Setenv("CCI_HOME", t.TempDir())
	ctx := context.Background()
	s, err := store.Open(filepath.Join(t.TempDir(), storeFile))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = s.Close() }()
	now := time.Now().UTC()
	post := func(draft client.Draft) {
		if _, err := client.Post(ctx, draft); err != nil {
			t.Fatal(err)
		}
	}
	post(client.Draft{Drive: "d", Lane: self, Kind: "note", Topic: gateInputsTopic, ID: "inputs", Text: "inputs", Attrs: map[string]string{gateInputsAttr: `{}`}})
	snapshot := make([]machine.Facts, heads)
	for i := range snapshot {
		number, head := i+1, fmt.Sprintf("%040x", i+1)
		attrs, body := map[string]string{fmt.Sprintf("head:%d", number): head, gates.PortOfAttr: "40810"}, ""
		if claimedInBody {
			delete(attrs, gates.PortOfAttr)
			body = "Port, old tree to new: dev #40810 (port of #40810)"
		}
		post(client.Draft{Drive: "d", Lane: "lane-1", Kind: "head", Text: fmt.Sprintf("HEAD #%d", number), PRs: []string{fmt.Sprintf("o/r#%d", number)}, Attrs: attrs})
		snapshot[i] = machine.Facts{Repo: "o/r", Number: number, Head: head, Base: "dev", Lane: "lane-1", CI: machine.CISuccess, Approved: true, Finder: machine.FinderClear, Fidelity: machine.FidelityPass, Body: body}
	}
	runner := &shelltest.Fake{}
	runner.Reply(`[{"number":40810,"state":"MERGED"}]`, "ccx", "vcs", "pr", "status")
	a := app.New(io.Discard)
	a.Runner, a.Now = runner, func() time.Time { return now }
	sweep := func() int {
		before := len(runner.Calls())
		if _, err := SweepGos(ctx, a, config.Config{Drive: "d", Repo: "o/r", Checkout: "/checkout", Trunk: "dev"}, s, snapshot, now, io.Discard); err != nil {
			t.Fatal(err)
		}
		return len(runner.Calls()) - before
	}
	if first := sweep(); first != heads {
		t.Fatalf("the first sweep made %d reads for %d new heads, want one port-source read each", first, heads)
	}
	unchanged = sweep()
	post(client.Draft{Drive: "d", Lane: "lane-1", Kind: "note", Text: "rebased onto dev", PRs: []string{"o/r#1"}})
	return unchanged, sweep()
}

func TestTheSweepReadsAsMuchForManyReadyHeadsAsForOneAndOnlyForWhatChanged(t *testing.T) {
	for name, claimedInBody := range map[string]bool{"claimed on its head record": false, "claimed by its body's port line": true} {
		t.Run(name, func(t *testing.T) {
			oneUnchanged, oneChanged := sweepReads(t, 1, claimedInBody)
			manyUnchanged, manyChanged := sweepReads(t, scaletest.N(), claimedInBody)

			if oneUnchanged != 0 || manyUnchanged != oneUnchanged {
				t.Fatalf("a sweep with nothing changed made %d reads at N=1 and %d at N=%d, want none at either", oneUnchanged, manyUnchanged, scaletest.N())
			}
			if oneChanged != 1 || manyChanged != oneChanged {
				t.Fatalf("a sweep after one PR's record made %d reads at N=1 and %d at N=%d, want that PR's one read at both", oneChanged, manyChanged, scaletest.N())
			}
		})
	}
}

func TestALaneGoOnAHeadTheSweepDoesNotGateGetsOneRefusalSayingWhy(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	ctx := context.Background()
	s, err := store.Open(filepath.Join(t.TempDir(), storeFile))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = s.Close() }()
	now := time.Now().UTC()
	for _, draft := range []client.Draft{
		{Drive: "d", Lane: self, Kind: "note", Topic: gateInputsTopic, ID: "inputs", Text: "inputs", Attrs: map[string]string{gateInputsAttr: `{}`}},
		{Drive: "d", Lane: "fixer", Kind: kindGo, PRs: []string{"o/r#1"}, Text: "go #1 at abc1234 on green [exempt: test-only-or-ci-only]: fidelity ok #1, finder ok #2"},
		{Drive: "d", Lane: "root", Kind: kindGo, PRs: []string{"o/r#2"}, Attrs: map[string]string{bus.ExemptClassAttr: "test-only-or-ci-only"}, Text: "go #2 at bcd2345 on green [exempt: test-only-or-ci-only]: fidelity ok #1, finder ok #2"},
		{Drive: "d", Lane: "fixer", Kind: kindGo, PRs: []string{"o/r#3"}, Text: "go #3 at 0000000 on green [exempt: test-only-or-ci-only]: fidelity ok #1, finder ok #2"},
	} {
		if _, err := client.Post(ctx, draft); err != nil {
			t.Fatal(err)
		}
	}
	red := func(number int, head string) machine.Facts {
		return machine.Facts{Repo: "o/r", Number: number, Head: head, Lane: "owner-1", CI: machine.CIPending, Approved: true}
	}
	a := app.New(io.Discard)
	a.Runner, a.Now = &shelltest.Fake{}, func() time.Time { return now }

	for range 2 {
		if _, err := SweepGos(ctx, a, config.Config{Drive: "d", Repo: "o/r", Checkout: "/checkout", Trunk: "dev"}, s, []machine.Facts{red(1, "abc1234def"), red(2, "bcd2345efa"), red(3, "cde3456fab")}, now, io.Discard); err != nil {
			t.Fatal(err)
		}
	}

	refused, err := client.Tail(ctx, client.Query{Drive: "d", Kinds: []string{bus.GoRefusedKind}})
	if err != nil {
		t.Fatal(err)
	}
	if len(refused) != 1 || !strings.HasPrefix(refused[0].Text, "no go for #1 at abc1234def: CI is not green") || !slices.Equal(refused[0].To, []string{"fixer", "owner-1"}) {
		t.Fatalf("refusals = %+v, want one for #1's lane go, to its lane and owner, naming its CI", refused)
	}
}

func TestAGoTheSweepPostsClearsItsHeadForTheRestOfThePass(t *testing.T) {
	ungraded := machine.Facts{Number: 1, Head: "abc1234", CI: machine.CISuccess, Approved: true}
	other := machine.Facts{Number: 2, Head: "bcd2345", CI: machine.CISuccess, Approved: true}

	marked := Exempted([]machine.Facts{ungraded, other}, []int{1})

	if !marked[0].Exempt || !marked[0].Cleared() {
		t.Fatalf("%+v: a head the sweep's go admitted should read exempt and cleared this pass, whatever the landing store held at the snapshot", marked[0])
	}
	if marked[1].Exempt || marked[1].Graded() {
		t.Fatalf("%+v: a head with no posted go should be unchanged", marked[1])
	}
}

func TestOnlyARefusedStandingClaimTurnsItsRowBackToHim(t *testing.T) {
	s, err := store.Open(filepath.Join(t.TempDir(), "lr.sqlite"))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = s.Close() }()
	now := time.Date(2026, 11, 4, 18, 0, 0, 0, time.UTC)
	claim := func(number int) gates.Claim { return gates.Claim{Repo: "o/r", PR: number, Head: "abc1234"} }
	refused := []string{"port: its source #9 is not a merged pull request"}
	for _, each := range []struct {
		number int
		clean  bool
		said   []string
		want   string
	}{
		{1, true, refused, refused[0]},
		{2, false, refused, ""},
		{3, true, nil, ""},
	} {
		if err := noteStandingRefused(s, claim(each.number), each.clean, each.said, now); err != nil {
			t.Fatal(err)
		}
		facts, err := s.Facts("o/r", each.number, "abc1234")
		if err != nil {
			t.Fatal(err)
		}
		if got := facts[gates.StandingRefused]; got != each.want {
			t.Fatalf("#%d: fact %q, want %q", each.number, got, each.want)
		}
	}
}

func TestAStandingRefusalClearsWhenTheGoNextAdmitsTheHead(t *testing.T) {
	s, err := store.Open(filepath.Join(t.TempDir(), storeFile))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = s.Close() }()
	now := time.Date(2026, 11, 4, 18, 0, 0, 0, time.UTC)
	claim := gates.Claim{Repo: "o/r", PR: 1, Head: "abc1234"}
	if err := noteStandingRefused(s, claim, true, []string{"port: its source #9 is not a merged pull request"}, now); err != nil {
		t.Fatal(err)
	}
	if err := noteStandingRefused(s, claim, true, nil, now.Add(time.Minute)); err != nil {
		t.Fatal(err)
	}
	facts, err := s.Facts("o/r", 1, "abc1234")
	if err != nil {
		t.Fatal(err)
	}
	if class, refused := gates.LandsOnWord(map[string]string{gates.StandingRefused: facts[gates.StandingRefused], gates.PortOfAttr: "9", gates.PortInteractsAttr: gates.PortCleanWord}); class != gates.OldTreePortClass || refused != "" {
		t.Fatalf("class %q, refused %q, want the port landing on his word", class, refused)
	}
}

func TestASweepRefusalSaysSoInOneRecordOnlyWhereSomeoneExpectsAGo(t *testing.T) {
	refused := []string{"finder: no counted decide"}
	head := "abc1234def"
	laneGo := client.Record{Seq: 1, Lane: "port-x-1", Kind: kindGo, Text: "go #7 at abc1234 on green [exempt: old-tree-port]: fidelity ok #1, finder ok #2"}
	laneGo.Fields.Attrs = map[string]string{bus.ExemptClassAttr: gates.OldTreePortClass}
	for name, c := range map[string]struct {
		standing bool
		records  []client.Record
		to       []string
		posts    bool
	}{
		"a standing word tells its owner":  {true, nil, []string{"owner-1"}, true},
		"a lane's go tells that lane too":  {false, []client.Record{laneGo}, []string{"owner-1", "port-x-1"}, true},
		"no claim and no go posts nothing": {false, nil, nil, false},
	} {
		t.Run(name, func(t *testing.T) {
			t.Setenv("CCI_HOME", t.TempDir())
			ctx := context.Background()
			cfg := config.Config{Drive: "d", Repo: "o/r"}
			claim := gates.Claim{Repo: "o/r", PR: 7, Head: head}
			for range 2 {
				if err := sayGoRefused(ctx, cfg, claim, "owner-1", c.standing, c.records, refused); err != nil {
					t.Fatal(err)
				}
			}
			posted, err := client.Tail(ctx, client.Query{Drive: "d", Kinds: []string{bus.GoRefusedKind}})
			if err != nil {
				t.Fatal(err)
			}
			if !c.posts {
				if len(posted) != 0 {
					t.Fatalf("posted %+v, want nothing", posted)
				}
				return
			}
			if len(posted) != 1 || !slices.Equal(posted[0].To, c.to) {
				t.Fatalf("posted %+v, want one record to %q", posted, c.to)
			}
			facts := bus.LegacyFacts(posted[0], "o/r", nil)
			if len(facts) != 1 || facts[0].Kind != gates.StandingRefused || facts[0].PR != 7 || facts[0].Head != head || facts[0].Value != refused[0] {
				t.Fatalf("facts = %+v, want the refusal at its head for every store's row", facts)
			}
		})
	}
}

func TestAHandTypedExemptGoLandsOnlyWhenTheGateFindsItsClassAtItsHead(t *testing.T) {
	const head = "73ce0deb655e60e30d22c352f7678e33ab37f8f3"
	for _, c := range []struct {
		name    string
		class   string
		grade   string
		lands   bool
		refused []string
	}{
		{name: "a head its grade puts in the class", class: "test-only", grade: "one wait", lands: true},
		{name: "a head no grade puts in an exempt class", class: gates.NoClass, grade: "stays in scope", refused: []string{
			"fidelity: no ok grade at 73ce0de names an exempt class",
			"by root names its exempt class by hand",
			"this head lands on his tap from its row's card",
		}},
	} {
		t.Run(c.name, func(t *testing.T) {
			t.Setenv("CCI_HOME", t.TempDir())
			ctx := context.Background()
			s, err := store.Open(filepath.Join(t.TempDir(), storeFile))
			if err != nil {
				t.Fatal(err)
			}
			defer func() { _ = s.Close() }()
			for _, draft := range []client.Draft{
				{Drive: "d", Lane: self, Kind: "note", Topic: gateInputsTopic, ID: "inputs", Text: "inputs", Attrs: map[string]string{gateInputsAttr: `{"exempt":["class test-only"]}`}},
				pipelineOK("o/r#1", head, c.class, c.grade),
				{Drive: "d", Lane: "finder-desk", Kind: "review", PRs: []string{"o/r#1"}, Text: "finder: ok " + head + " no majors"},
				{Drive: "d", Lane: "root", Kind: kindGo, PRs: []string{"o/r#1"}, Text: "go #1 at 73ce0de on green [exempt: test-only]: fidelity ok, finder ok"},
			} {
				if _, err := client.Post(ctx, draft); err != nil {
					t.Fatal(err)
				}
			}
			hand, err := client.Tail(ctx, client.Query{Drive: "d", Kinds: []string{kindGo}})
			if err != nil {
				t.Fatal(err)
			}
			if facts := bus.LegacyFacts(hand[0], "o/r", nil); len(facts) != 0 {
				t.Fatalf("the hand go folds to %+v, want no exempt fact before the gate checks it", facts)
			}
			if err := s.PutReviewRows([]store.ReviewRow{{Repo: "o/r", Number: 1, Head: head, ReadyForTap: true}}); err != nil {
				t.Fatal(err)
			}
			if _, err := s.PutFact("o/r", 99, head, gates.ShownFact, "1", time.Now()); err != nil {
				t.Fatal(err)
			}
			runner := &shelltest.Fake{}
			runner.Reply("[\"rust/crates/sandsql-server/tests/handoff_e2e.rs\",\"@@ +1 @@\\n+wait_log\"]\n", "ccx", "vcs", "gh", "--max-age", "0s", "--", "api", "--paginate", "repos/o/r/pulls/1/files")
			a := app.New(io.Discard)
			a.Runner, a.CCX = runner, ccx.CLI{Runner: runner}
			ready := readyFacts(1, head)
			ready.Lane = "owner-1"

			posted, err := SweepGos(ctx, a, config.Config{Drive: "d", Repo: "o/r", Checkout: "/checkout", Trunk: "dev"}, s, []machine.Facts{ready}, time.Now().UTC(), io.Discard)
			if err != nil {
				t.Fatal(err)
			}

			gos, err := client.Tail(ctx, client.Query{Drive: "d", Kinds: []string{kindGo}, Lanes: []string{bus.SweepLane}})
			if err != nil {
				t.Fatal(err)
			}
			refusals, err := client.Tail(ctx, client.Query{Drive: "d", Kinds: []string{bus.GoRefusedKind}})
			if err != nil {
				t.Fatal(err)
			}
			if c.lands {
				if !slices.Equal(posted, []int{1}) || len(gos) != 1 || len(refusals) != 0 {
					t.Fatalf("posted %v, gos %+v, refusals %+v; want the sweep's own go and no refusal", posted, gos, refusals)
				}
				if facts := bus.LegacyFacts(gos[0], "o/r", nil); len(facts) != 1 || facts[0].Value != "test-only" {
					t.Fatalf("the sweep's go folds to %+v, want the class the gate checked", facts)
				}
				return
			}
			if len(posted) != 0 || len(gos) != 0 || len(refusals) != 1 || !slices.Equal(refusals[0].To, []string{"owner-1", "root"}) {
				t.Fatalf("posted %v, gos %+v, refusals %+v; want one refusal to the PR's lane and root, and no go", posted, gos, refusals)
			}
			for _, want := range c.refused {
				if !strings.Contains(refusals[0].Text, want) {
					t.Fatalf("refusal %q, want %q", refusals[0].Text, want)
				}
			}
		})
	}
}

func TestASweepRefusingAHeadGradedExemptTellsItsLaneSoHisCardIsDrawn(t *testing.T) {
	const head = "51717aa0b7e80d25838b30153600acad437970fe"
	for name, c := range map[string]struct {
		class string
		grade string
		tells bool
	}{
		"graded test-only":          {"test-only", "adds the regression test", true},
		"graded in no exempt class": {gates.NoClass, "stays in scope", false},
	} {
		t.Run(name, func(t *testing.T) {
			t.Setenv("CCI_HOME", t.TempDir())
			ctx := context.Background()
			s, err := store.Open(filepath.Join(t.TempDir(), storeFile))
			if err != nil {
				t.Fatal(err)
			}
			defer func() { _ = s.Close() }()
			for _, draft := range []client.Draft{
				{Drive: "d", Lane: self, Kind: "note", Topic: gateInputsTopic, ID: "inputs", Text: "inputs", Attrs: map[string]string{gateInputsAttr: `{"exempt":["class test-only"],"tap_rules":{"rule/infra":["infra/*"]}}`}},
				pipelineOK("o/r#1", head, c.class, c.grade),
				{Drive: "d", Lane: "finder-desk", Kind: "review", PRs: []string{"o/r#1"}, Text: "finder: ok " + head + " no majors"},
			} {
				if _, err := client.Post(ctx, draft); err != nil {
					t.Fatal(err)
				}
			}
			if err := s.PutReviewRows([]store.ReviewRow{{Repo: "o/r", Number: 1, Head: head, ReadyForTap: true}}); err != nil {
				t.Fatal(err)
			}
			if _, err := s.PutFact("o/r", 99, head, gates.ShownFact, "1", time.Now()); err != nil {
				t.Fatal(err)
			}
			runner := &shelltest.Fake{}
			runner.Reply("[\"infra/x_test.ts\",\"@@ +1 @@\\n+expect(x)\"]\n", "ccx", "vcs", "gh", "--max-age", "0s", "--", "api", "--paginate", "repos/o/r/pulls/1/files")
			a := app.New(io.Discard)
			a.Runner, a.CCX = runner, ccx.CLI{Runner: runner}
			ready := readyFacts(1, head)
			ready.Lane = "tests-1"

			posted, err := SweepGos(ctx, a, config.Config{Drive: "d", Repo: "o/r", Checkout: "/checkout", Trunk: "dev"}, s, []machine.Facts{ready}, time.Now().UTC(), io.Discard)
			if err != nil {
				t.Fatal(err)
			}

			refusals, err := client.Tail(ctx, client.Query{Drive: "d", Kinds: []string{bus.GoRefusedKind}})
			if err != nil {
				t.Fatal(err)
			}
			if len(posted) != 0 {
				t.Fatalf("posted %v, want no go past the tap rule", posted)
			}
			if !c.tells {
				if len(refusals) != 0 {
					t.Fatalf("refusals %+v, want none: a head in no exempt class has its card already", refusals)
				}
				return
			}
			if len(refusals) != 1 || !slices.Equal(refusals[0].To, []string{"tests-1"}) || !strings.Contains(refusals[0].Text, "tap rule rule/infra names infra/x_test.ts") {
				t.Fatalf("refusals %+v, want one to the PR's lane naming the tap rule", refusals)
			}
			if facts := bus.LegacyFacts(refusals[0], "o/r", nil); len(facts) != 1 || facts[0].Kind != gates.StandingRefused || facts[0].Head != head {
				t.Fatalf("facts = %+v, want the refusal at its head, which draws his card on every store", facts)
			}
		})
	}
}

func TestATaplessClassLandsOnTheSweepsGoPastItsShownTapCardUnlessHeAnsweredIt(t *testing.T) {
	const head = "51717bb0b7e80d25838b30153600acad437970fe"
	const testFile = "[\"go/x/x_test.go\",\"@@ +1 @@\\n+\\tif got != want {\"]\n"
	portClaim := client.Draft{Drive: "d", Lane: "port-1", Kind: "head", Text: "HEAD #1", PRs: []string{"o/r#1"}, Attrs: map[string]string{"head:1": head, gates.PortOfAttr: "40810", gates.PortInteractsAttr: gates.PortCleanWord}}
	flakeClaim := client.Draft{Drive: "d", Lane: "flake-1", Kind: "head", Text: "HEAD #1", PRs: []string{"o/r#1"}, Attrs: map[string]string{"head:1": head, gates.FlakeFixAttr: "TestServeHoldsItsLease"}}
	for name, c := range map[string]struct {
		class   string
		grade   string
		files   string
		claims  []client.Draft
		tap     string
		refused string
		tapRow  bool
	}{
		"test-only with its card shown and open":       {class: "test-only", grade: "adds the regression test", files: testFile},
		"ci-tooling with its card shown and open":      {class: "ci-tooling", grade: "bumps the shard script", files: "[\".buildkite/v2/scripts/test-shard.sh\",\"@@ +1 @@\\n+set -e\"]\n"},
		"a claimed pure port with its card shown":      {class: gates.ClassOf(gates.PortGrade), grade: "compared by file", files: "[\"go/sand/next/a.go\",\"@@ +1 @@\\n+\\treturn nil\"]\n", claims: []client.Draft{portClaim}},
		"a claimed flake fix with its card shown":      {class: gates.NoClass, grade: "reads right", files: testFile, claims: []client.Draft{flakeClaim}},
		"a claimed flake fix that he held on its card": {class: gates.NoClass, grade: "reads right", files: testFile, claims: []client.Draft{flakeClaim}, tap: string(machine.TapWithdrawn), refused: "he answered its tap card at 51717bb, so it waits on his tap"},
		"test-only that he held on its card":           {class: "test-only", grade: "adds the regression test", files: testFile, tap: string(machine.TapWithdrawn), refused: "he answered its tap card at 51717bb, so it waits on his tap"},
		"owner-pick, which no ruling lands untapped":   {class: "owner-pick", grade: "his pick", files: testFile, tapRow: true},
	} {
		t.Run(name, func(t *testing.T) {
			t.Setenv("CCI_HOME", t.TempDir())
			ctx := context.Background()
			s, err := store.Open(filepath.Join(t.TempDir(), storeFile))
			if err != nil {
				t.Fatal(err)
			}
			defer func() { _ = s.Close() }()
			for _, draft := range []client.Draft{
				{Drive: "d", Lane: self, Kind: "note", Topic: gateInputsTopic, ID: "inputs", Text: "inputs", Attrs: map[string]string{gateInputsAttr: `{"exempt":["pure port, no interaction","class test-only","class ci-tooling","class owner-pick"],"cards":[{"id":"card-tap-1-51717bb","title":"Tap: #1, adds the regression test"}]}`}},
				pipelineOK("o/r#1", head, c.class, c.grade),
				{Drive: "d", Lane: "finder-desk", Kind: "review", PRs: []string{"o/r#1"}, Text: "finder: ok " + head + " no majors"},
			} {
				if _, err := client.Post(ctx, draft); err != nil {
					t.Fatal(err)
				}
			}
			for _, claim := range c.claims {
				if _, err := client.Post(ctx, claim); err != nil {
					t.Fatal(err)
				}
			}
			if err := s.PutReviewRows([]store.ReviewRow{{Repo: "o/r", Number: 1, Head: head, ReadyForTap: true}}); err != nil {
				t.Fatal(err)
			}
			stored := map[string]string{gates.ShownFact: "1"}
			if c.tap != "" {
				stored["tap"] = c.tap
			}
			for kind, value := range stored {
				if _, err := s.PutFact("o/r", 1, head, kind, value, time.Now()); err != nil {
					t.Fatal(err)
				}
			}
			runner := &shelltest.Fake{}
			runner.Reply(c.files, "ccx", "vcs", "gh", "--max-age", "0s", "--", "api", "--paginate", "repos/o/r/pulls/1/files")
			runner.Reply(`[{"number":40810,"state":"MERGED"}]`, "ccx", "vcs", "pr", "status")
			a := app.New(io.Discard)
			a.Runner, a.CCX = runner, ccx.CLI{Runner: runner}
			ready := readyFacts(1, head)
			ready.Lane = "tests-1"

			posted, err := SweepGos(ctx, a, config.Config{Drive: "d", Repo: "o/r", Checkout: "/checkout", Trunk: "dev"}, s, []machine.Facts{ready}, time.Now().UTC(), io.Discard)
			if err != nil {
				t.Fatal(err)
			}

			refusals, err := client.Tail(ctx, client.Query{Drive: "d", Kinds: []string{bus.GoRefusedKind}})
			if err != nil {
				t.Fatal(err)
			}
			if c.tapRow {
				if len(posted) != 0 || len(refusals) != 0 {
					t.Fatalf("posted %v, refusals %+v; want no go and no refusal, as a tap row", posted, refusals)
				}
				return
			}
			if c.refused == "" {
				if !slices.Equal(posted, []int{1}) || len(refusals) != 0 {
					t.Fatalf("posted %v, refusals %+v; want the sweep's go past the shown and open card", posted, refusals)
				}
				return
			}
			if len(posted) != 0 || len(refusals) != 1 || !strings.Contains(refusals[0].Text, c.refused) {
				t.Fatalf("posted %v, refusals %+v; want no go and one refusal saying %q", posted, refusals, c.refused)
			}
		})
	}
}

func TestAPortWhoseBodyNamesItsSourceLandsOnTheSweepsGoPastItsCardWhenFidelityGradesItPure(t *testing.T) {
	const head = "345c6838d6e53f330efe065b56535bdb5df7eeac"
	const body = "Port, old tree to new: dev #52007 (port of #52007)\n\n## Context"
	for name, c := range map[string]struct {
		class string
		grade string
		lands bool
	}{
		"#52183, which fidelity graded a pure port": {gates.ClassOf(gates.PortGrade), "pure port, no interaction: Faithfully ports #52007's Go changes: OAuth providers are team-filtered, and guidance drops Google examples.", true},
		"a port fidelity graded only as his pick":   {"owner-pick", "his pick", false},
	} {
		t.Run(name, func(t *testing.T) {
			t.Setenv("CCI_HOME", t.TempDir())
			ctx := context.Background()
			s, err := store.Open(filepath.Join(t.TempDir(), storeFile))
			if err != nil {
				t.Fatal(err)
			}
			defer func() { _ = s.Close() }()
			for _, draft := range []client.Draft{
				{Drive: "d", Lane: self, Kind: "note", Topic: gateInputsTopic, ID: "inputs", Text: "inputs", Attrs: map[string]string{gateInputsAttr: `{"exempt":["pure port, no interaction","class owner-pick"],"cards":[{"id":"card-tap-52183-345c683","title":"Tap: #52183, sand-next: Google is a connected app only in a team the google-connected-app-enabled flag names"}]}`}},
				pipelineOK("o/r#52183", head, c.class, c.grade),
				{Drive: "d", Lane: "finder-desk", Kind: "review", PRs: []string{"o/r#52183"}, Text: "finder: ok " + head + " luna finder over whole 345c683^..345c683: no majors"},
				{Drive: "d", Lane: "old-tree-port-desk-15", Kind: "head", Text: "HEAD #52183 345c683", PRs: []string{"o/r#52183"}, Attrs: map[string]string{"head:52183": head}},
			} {
				if _, err := client.Post(ctx, draft); err != nil {
					t.Fatal(err)
				}
			}
			if err := s.PutReviewRows([]store.ReviewRow{{Repo: "o/r", Number: 52183, Head: head, ReadyForTap: true}}); err != nil {
				t.Fatal(err)
			}
			if _, err := s.PutFact("o/r", 52183, head, gates.ShownFact, "1", time.Now()); err != nil {
				t.Fatal(err)
			}
			runner := &shelltest.Fake{}
			runner.Reply("[\"go/sand/next/internal/platformapi/oauth.go\",\"@@ +1 @@\\n+\\treturn providers\"]\n", "ccx", "vcs", "gh", "--max-age", "0s", "--", "api", "--paginate", "repos/o/r/pulls/52183/files")
			runner.Reply(`[{"number":52007,"state":"MERGED"}]`, "ccx", "vcs", "pr", "status")
			a := app.New(io.Discard)
			a.Runner, a.CCX = runner, ccx.CLI{Runner: runner}
			ready := readyFacts(52183, head)
			ready.Lane, ready.Body = "old-tree-port-desk-15", body
			var out bytes.Buffer

			posted, err := SweepGos(ctx, a, config.Config{Drive: "d", Repo: "o/r", Checkout: "/checkout", Trunk: "dev"}, s, []machine.Facts{ready}, time.Now().UTC(), &out)
			if err != nil {
				t.Fatal(err)
			}

			if !c.lands {
				if len(posted) != 0 || !strings.Contains(out.String(), "REFUSED #52183 at 345c683: fidelity: no ok grade at 345c683 names an exempt class") {
					t.Fatalf("posted %v, printed %q; want no go and the refusal naming its class", posted, out.String())
				}
				return
			}
			if !slices.Equal(posted, []int{52183}) {
				t.Fatalf("posted %v, printed %q; want the sweep's go past his shown and open card", posted, out.String())
			}
			stored, err := s.Facts("o/r", 52183, head)
			if err != nil {
				t.Fatal(err)
			}
			if class, refused := gates.LandsOnWord(stored); class != gates.OldTreePortClass || refused != "" {
				t.Fatalf("its row reads class %q, refused %q from %v; want it to land on his standing word, so no card asks him", class, refused, stored)
			}
		})
	}
}

func TestAHoldALiftClosedLandsEvenWhenTheLiftNamesNoPR(t *testing.T) {
	const head = "51563cc0b7e80d25838b30153600acad437970fe"
	for name, lifted := range map[string]bool{"lifted by a record naming no PR": true, "never lifted": false} {
		t.Run(name, func(t *testing.T) {
			t.Setenv("CCI_HOME", t.TempDir())
			ctx := context.Background()
			s, err := store.Open(filepath.Join(t.TempDir(), storeFile))
			if err != nil {
				t.Fatal(err)
			}
			defer func() { _ = s.Close() }()
			post := func(draft client.Draft) client.Record {
				posted, err := client.Post(ctx, draft)
				if err != nil {
					t.Fatal(err)
				}
				return posted
			}
			post(client.Draft{Drive: "d", Lane: self, Kind: "note", Topic: gateInputsTopic, ID: "inputs", Text: "inputs", Attrs: map[string]string{gateInputsAttr: `{"exempt":["class test-only"]}`}})
			post(pipelineOK("o/r#1", head, "test-only", "adds the regression test"))
			post(client.Draft{Drive: "d", Lane: "finder-desk", Kind: "review", PRs: []string{"o/r#1"}, Text: "finder: ok " + head + " no majors"})
			hold := post(client.Draft{Drive: "d", Lane: "owner", Kind: "hold", PRs: []string{"o/r#1"}, Text: "hold #1 at 51563cc: the owner rejected this head on the board"})
			if lifted {
				post(client.Draft{Drive: "d", Lane: "owner-relay", Kind: "lift", Resolves: hold.Seq, Text: "Hold on #1 lifted: the new head carries his order"})
			}
			if err := s.PutReviewRows([]store.ReviewRow{{Repo: "o/r", Number: 1, Head: head, ReadyForTap: true}}); err != nil {
				t.Fatal(err)
			}
			if _, err := s.PutFact("o/r", 99, head, gates.ShownFact, "1", time.Now()); err != nil {
				t.Fatal(err)
			}
			runner := &shelltest.Fake{}
			runner.Reply("[\"go/x/x_test.go\",\"@@ +1 @@\\n+\\tif got != want {\"]\n", "ccx", "vcs", "gh", "--max-age", "0s", "--", "api", "--paginate", "repos/o/r/pulls/1/files")
			a := app.New(io.Discard)
			a.Runner, a.CCX = runner, ccx.CLI{Runner: runner}
			ready := readyFacts(1, head)
			ready.Lane = "update-modal-cancel"

			posted, err := SweepGos(ctx, a, config.Config{Drive: "d", Repo: "o/r", Checkout: "/checkout", Trunk: "dev"}, s, []machine.Facts{ready}, time.Now().UTC(), io.Discard)
			if err != nil {
				t.Fatal(err)
			}

			refusals, err := client.Tail(ctx, client.Query{Drive: "d", Kinds: []string{bus.GoRefusedKind}})
			if err != nil {
				t.Fatal(err)
			}
			if lifted {
				if !slices.Equal(posted, []int{1}) || len(refusals) != 0 {
					t.Fatalf("posted %v, refusals %+v; want the go once the lift closed the hold", posted, refusals)
				}
				return
			}
			if len(posted) != 0 || len(refusals) != 1 || !strings.Contains(refusals[0].Text, "hold "+hold.Name()+" by owner is open") {
				t.Fatalf("posted %v, refusals %+v; want the open hold to refuse the go", posted, refusals)
			}
		})
	}
}

func TestTheSweepLandsAClaimedOneTestFileFlakeFixPastItsTapRuleAndTellsAnUnclaimedOneHow(t *testing.T) {
	const head = "aab8fd2b7e80d25838b30153600acad437970fed"
	const test = "go/sand/next/cmd/iris_serve_idle_test.go"
	testFile := fmt.Sprintf("[%q,\"@@ +1 @@\\n+\\tchild := exec.Command(os.Args[0])\"]\n", test)
	for _, c := range []struct {
		name     string
		files    string
		class    string
		grade    string
		told     string
		refused  string
		landsNow bool
	}{
		{
			name: "one test file", files: testFile, class: "test-only", grade: "one test file", landsNow: true,
			told: "claim it: cci post --drive d --lane <your lane> --kind head --head o/r#1=" + head + " --attr flake-fix=<the test that flaked>",
		},
		{
			name: "the same fix touching one product file", files: testFile + "[\"go/sand/next/cmd/iris_serve.go\",\"@@ +1 @@\\n+\\tgc()\"]\n", class: gates.NoClass, grade: "reads right",
			refused: "flake fix: go/sand/next/cmd/iris_serve.go is not test or fixture code, so it lands on his tap",
		},
	} {
		t.Run(c.name, func(t *testing.T) {
			t.Setenv("CCI_HOME", t.TempDir())
			ctx := context.Background()
			s, err := store.Open(filepath.Join(t.TempDir(), storeFile))
			if err != nil {
				t.Fatal(err)
			}
			defer func() { _ = s.Close() }()
			post := func(draft client.Draft) {
				if _, err := client.Post(ctx, draft); err != nil {
					t.Fatal(err)
				}
			}
			post(client.Draft{Drive: "d", Lane: self, Kind: "note", Topic: gateInputsTopic, ID: "inputs", Text: "inputs", Attrs: map[string]string{gateInputsAttr: `{"exempt":["class test-only"],"tap_rules":{"rule/82317b0":["go/sand/next/*"]}}`}})
			post(pipelineOK("o/r#1", head, c.class, c.grade))
			post(client.Draft{Drive: "d", Lane: "finder-desk", Kind: "review", PRs: []string{"o/r#1"}, Text: "finder: ok " + head + " no majors"})
			if err := s.PutReviewRows([]store.ReviewRow{{Repo: "o/r", Number: 1, Head: head, ReadyForTap: true}}); err != nil {
				t.Fatal(err)
			}
			if _, err := s.PutFact("o/r", 99, head, gates.ShownFact, "1", time.Now()); err != nil {
				t.Fatal(err)
			}
			runner := &shelltest.Fake{}
			runner.Reply(c.files, "ccx", "vcs", "gh", "--max-age", "0s", "--", "api", "--paginate", "repos/o/r/pulls/1/files")
			a := app.New(io.Discard)
			a.Runner, a.CCX = runner, ccx.CLI{Runner: runner}
			ready := readyFacts(1, head)
			ready.Lane = "flake-1"
			sweep := func() []int {
				posted, err := SweepGos(ctx, a, config.Config{Drive: "d", Repo: "o/r", Checkout: "/checkout", Trunk: "dev"}, s, []machine.Facts{ready}, time.Now().UTC(), io.Discard)
				if err != nil {
					t.Fatal(err)
				}
				return posted
			}
			refusals := func() []client.Record {
				records, err := client.Tail(ctx, client.Query{Drive: "d", Kinds: []string{bus.GoRefusedKind}})
				if err != nil {
					t.Fatal(err)
				}
				return records
			}

			for range 2 {
				if posted := sweep(); len(posted) != 0 {
					t.Fatalf("posted %v with no flake-fix record at its head, want it waiting on his tap", posted)
				}
			}
			notes, err := client.Tail(ctx, client.Query{Drive: "d", Kinds: []string{"note"}, Lanes: []string{bus.SweepLane}})
			if err != nil {
				t.Fatal(err)
			}
			told := slices.DeleteFunc(notes, func(record client.Record) bool {
				return !strings.HasPrefix(record.Text, "FLAKE-UNCLAIMED #1 at aab8fd2")
			})
			if c.told == "" && len(told) != 0 {
				t.Fatalf("notes %+v, want none: a claim would not land it", told)
			}
			if c.told != "" && (len(told) != 1 || !slices.Equal(told[0].To, []string{"flake-1"}) || !strings.Contains(told[0].Text, c.told)) {
				t.Fatalf("notes %+v, want one across both passes, to the PR's lane, saying %q", told, c.told)
			}

			post(client.Draft{Drive: "d", Lane: "flake-1", Kind: "head", PRs: []string{"o/r#1"}, Text: "HEAD #1", Attrs: map[string]string{"head:1": head, gates.FlakeFixAttr: "TestServeHoldingWhatAPreludePerChatLeftCollectsAboveItsLiveHeapInsideItsFootprint"}})
			posted := sweep()
			gos, err := client.Tail(ctx, client.Query{Drive: "d", Kinds: []string{kindGo}, Lanes: []string{bus.SweepLane}})
			if err != nil {
				t.Fatal(err)
			}
			if c.landsNow {
				if !slices.Equal(posted, []int{1}) || len(gos) != 1 || !strings.Contains(gos[0].Text, "[exempt: flake-fix]") {
					t.Fatalf("posted %v, gos %+v; want the sweep's flake-fix go once the head record claims it", posted, gos)
				}
				return
			}
			if len(posted) != 0 || len(gos) != 0 || !slices.ContainsFunc(refusals(), func(record client.Record) bool { return strings.Contains(record.Text, c.refused) }) {
				t.Fatalf("posted %v, gos %+v, refusals %+v; want no go and a refusal saying %q", posted, gos, refusals(), c.refused)
			}
		})
	}
}

func TestAPortSourceLandedWhenGitHubSaysMergedOrACommitNamingItIsOnTrunk(t *testing.T) {
	const source = 40810
	for name, c := range map[string]struct {
		state string
		log   string
		want  bool
	}{
		"merged on GitHub":                            {"MERGED", "", true},
		"closed by the queue, its commit on trunk":    {"CLOSED", "sand: publish a thread's task outline (#40810)\n", true},
		"closed with no commit on trunk":              {"CLOSED", "", false},
		"closed, and only a later commit mentions it": {"CLOSED", "sand: port of the outline (#40810) again (#50303)\n", false},
		"closed, and only a revert names it":          {"CLOSED", "Revert \"sand: outline (#40810)\" (#41000)\n", false},
	} {
		t.Run(name, func(t *testing.T) {
			runner := &shelltest.Fake{}
			runner.Reply(`[{"number":40810,"state":"`+c.state+`"}]`, "ccx", "vcs", "pr", "status")
			runner.Reply("", "git", "-C", "/checkout", "fetch")
			runner.Reply(c.log, "git", "-C", "/checkout", "log")
			a := app.New(io.Discard)
			a.Runner = runner
			got, err := sourceLanded(context.Background(), a, config.Config{Repo: "o/r", Checkout: "/checkout", Trunk: "dev"}, source)
			if err != nil {
				t.Fatal(err)
			}
			if got != c.want {
				t.Fatalf("sourceLanded = %v, want %v", got, c.want)
			}
		})
	}
}

func TestChangedFilesKeepsAFileGitHubGaveNoPatchFor(t *testing.T) {
	files, patches, err := changedFiles(`["a_test.go","+x"]` + "\n" + `["big_test.go",null]` + "\n")
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(files, []string{"a_test.go", "big_test.go"}) || *patches["a_test.go"] != "+x" || patches["big_test.go"] != nil {
		t.Fatalf("files %q, patches %v", files, patches)
	}
}

func TestAHeadWhoseReadFailsIsSkippedWithOneRecordWhileTheSweepGatesTheRest(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	ctx := context.Background()
	s, err := store.Open(filepath.Join(t.TempDir(), storeFile))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = s.Close() }()
	now := time.Now().UTC()
	const port, plain = "abc1234def", "fed4321cba"
	for _, draft := range []client.Draft{
		{Drive: "d", Lane: self, Kind: "note", Topic: gateInputsTopic, ID: "inputs", Text: "inputs", Attrs: map[string]string{gateInputsAttr: `{}`}},
		{Drive: "d", Lane: "port-1", Kind: "head", Text: "HEAD #1", PRs: []string{"o/r#1"}, Attrs: map[string]string{"head:1": port, gates.PortOfAttr: "40810", gates.PortInteractsAttr: gates.PortCleanWord}},
	} {
		if _, err := client.Post(ctx, draft); err != nil {
			t.Fatal(err)
		}
	}
	runner := &shelltest.Fake{}
	runner.Fail(1, "ccx: pr status: /state is not a checkout", "ccx", "vcs", "pr", "status")
	a := app.New(io.Discard)
	a.Runner, a.Now = runner, func() time.Time { return now }
	ready := func(number int, head string) machine.Facts {
		return machine.Facts{Repo: "o/r", Number: number, Head: head, Lane: "lane-1", CI: machine.CISuccess, Approved: true, Finder: machine.FinderClear, Fidelity: machine.FidelityPass}
	}
	var out strings.Builder

	for range 2 {
		if _, err := SweepGos(ctx, a, config.Config{Drive: "d", Repo: "o/r", Checkout: "/checkout", Trunk: "dev"}, s, []machine.Facts{ready(1, port), ready(2, plain)}, now, &out); err != nil {
			t.Fatalf("the sweep stopped on one head's failed read: %v", err)
		}
	}

	if !strings.Contains(out.String(), "UNREAD #1 at abc1234") || !strings.Contains(out.String(), "REFUSED #2 at fed4321") {
		t.Fatalf("sweep said %q, want #1 skipped as unread and #2 still gated", out.String())
	}
	notes, err := client.Tail(ctx, client.Query{Drive: "d", Kinds: []string{"note"}, Lanes: []string{bus.SweepLane}})
	if err != nil {
		t.Fatal(err)
	}
	said := slices.DeleteFunc(notes, func(record client.Record) bool { return record.Topic == gateInputsTopic })
	if len(said) != 1 || !strings.HasPrefix(said[0].Text, "GO-UNREAD #1 at abc1234") {
		t.Fatalf("records = %+v, want one GO-UNREAD for #1 across both passes", said)
	}
}

func TestTheHandGateAndTheGoAnswerTheSameOnEachShape(t *testing.T) {
	const flake, wait = "79ac14b5aebaabd819bf339ee7c197cf657f8ab3", "73ce0deb655e60e30d22c352f7678e33ab37f8f3"
	for _, c := range []struct {
		name    string
		number  int
		head    string
		file    string
		rootGo  bool
		goLine  string
		tapLine []string
	}{
		{
			name: "a tap rule's path, with a root go in prose", number: 50715, head: flake, file: "go/sand/next/cmd/iris_serve_scale_test.go", rootGo: true,
			goLine:  "REFUSED #50715 at 79ac14b: tap rule rule/82317b0 names go/sand/next/cmd/iris_serve_scale_test.go, so it lands on his tap\n",
			tapLine: []string{"TAP-REQUIRED no approval on the owner's board at this head, and the go sweep refuses it: tap rule rule/82317b0 names go/sand/next/cmd/iris_serve_scale_test.go, so it lands on his tap", "by root is not counted at this head"},
		},
		{
			name: "a test file no tap rule names", number: 50728, head: wait, file: "rust/crates/sandsql-server/tests/handoff_e2e.rs",
			goLine: "GO #50728 at 73ce0de (dry)", tapLine: []string{gates.NoGo},
		},
	} {
		t.Run(c.name, func(t *testing.T) {
			t.Setenv("CCI_HOME", t.TempDir())
			ctx := context.Background()
			s, err := store.Open(filepath.Join(t.TempDir(), storeFile))
			if err != nil {
				t.Fatal(err)
			}
			defer func() { _ = s.Close() }()
			ref := fmt.Sprintf("o/r#%d", c.number)
			drafts := []client.Draft{
				{Drive: "d", Lane: self, Kind: "note", Topic: gateInputsTopic, ID: "inputs", Text: "inputs", Attrs: map[string]string{gateInputsAttr: `{"exempt":["class test-only"],"tap_rules":{"rule/82317b0":["go/sand/next/*"]}}`}},
				pipelineOK(ref, c.head, "test-only", "one wait"),
				{Drive: "d", Lane: "finder-desk", Kind: "review", PRs: []string{ref}, Text: "finder: ok " + c.head + " no majors"},
			}
			if c.rootGo {
				drafts = append(drafts, client.Draft{Drive: "d", Lane: "root", Kind: "go", PRs: []string{ref}, Text: fmt.Sprintf("#%d lands on his word", c.number)})
			}
			for _, draft := range drafts {
				if _, err := client.Post(ctx, draft); err != nil {
					t.Fatal(err)
				}
			}
			if err := s.PutReviewRows([]store.ReviewRow{{Repo: "o/r", Number: c.number, Head: c.head, ReadyForTap: true}}); err != nil {
				t.Fatal(err)
			}
			if _, err := s.PutFact("o/r", 99, c.head, gates.ShownFact, "1", time.Now()); err != nil {
				t.Fatal(err)
			}
			runner := &shelltest.Fake{}
			runner.Reply(fmt.Sprintf("[%q,\"@@ +1 @@\\n+wait_log\"]\n", c.file), "ccx", "vcs", "gh", "--max-age", "0s", "--", "api", "--paginate", fmt.Sprintf("repos/o/r/pulls/%d/files", c.number))
			runner.Reply("", "ccx", "vcs", "gh", "--max-age", "0s", "--", "pr", "view")
			a := app.New(io.Discard)
			a.Runner, a.CCX = runner, ccx.CLI{Runner: runner}
			cfg := config.Config{Drive: "d", Repo: "o/r", Trunk: "dev"}
			inputs, err := readGateInputs(ctx, "d", a.Now())
			if err != nil {
				t.Fatal(err)
			}
			var dry bytes.Buffer
			if _, _, err := (goGate{a: a, cfg: cfg, s: s, inputs: inputs, stdout: &dry}).one(ctx, c.number, c.head, goFlags{}); err != nil {
				t.Fatal(err)
			}
			if !strings.HasPrefix(dry.String(), c.goLine) {
				t.Fatalf("lr landing go said %q, want %q", dry.String(), c.goLine)
			}
			hand := (&Gates{GoAnswer: sweepAnswer(ctx, a, cfg, s), Stderr: io.Discard}).tapLines(ctx, readyFacts(c.number, c.head))
			if len(hand) != 1 {
				t.Fatalf("lr landing gate said %q, want one line", hand)
			}
			for _, want := range c.tapLine {
				if !strings.Contains(hand[0], want) {
					t.Fatalf("lr landing gate said %q, want %q", hand[0], want)
				}
			}
		})
	}
}

func TestRootsOverridesThatNameNoPRCountAtTheGateAndReopenItsRefusedHead(t *testing.T) {
	const repo, number, head = "Forge-AI/monorepo", 51859, "89ed729e838c099fb6c40d0de165cfd239a29589"
	t.Setenv("CCI_HOME", t.TempDir())
	ctx := context.Background()
	s, err := store.Open(filepath.Join(t.TempDir(), storeFile))
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = s.Close() }()
	ref := fmt.Sprintf("%s#%d", repo, number)
	post := func(draft client.Draft) client.Record {
		posted, err := client.Post(ctx, draft)
		if err != nil {
			t.Fatal(err)
		}
		return posted
	}
	post(client.Draft{Drive: "d", Lane: self, Kind: "note", Topic: gateInputsTopic, ID: "inputs", Text: "inputs", Attrs: map[string]string{gateInputsAttr: `{"exempt":["class ci-only"]}`}})
	post(client.Draft{Drive: "d", Lane: gates.PipelineLane, Kind: "review", PRs: []string{ref}, Text: "fidelity: missing " + head + " AGENTS.md:93,AGENTS.md:98 go/ci/prune.go:61-62 adds one awaited GetItem per lock row", Attrs: map[string]string{gates.FidelityClassAttr: "ci-only"}})
	fidelity := post(client.Draft{Drive: "d", Lane: "fidelity-desk-31", Kind: "review", PRs: []string{ref}, Text: "fidelity: ok " + head + " class ci-only: desk grade for root 533189, in root's ci-tooling class."})
	finder := post(client.Draft{Drive: "d", Lane: "finder-desk", Kind: "review", PRs: []string{ref}, Text: "finder: major " + head + " 1 open over whole 89ed729^..89ed729: go/ci/prune.go:61 slow at N"})
	if err := s.PutReviewRows([]store.ReviewRow{{Repo: repo, Number: number, Head: head, ReadyForTap: true}}); err != nil {
		t.Fatal(err)
	}
	if _, err := s.PutFact(repo, number, head, gates.ShownFact, "1", time.Now()); err != nil {
		t.Fatal(err)
	}
	runner := &shelltest.Fake{}
	runner.Reply("[\"go/ci/prune.go\",\"@@ +1 @@\\n+get\"]\n", "ccx", "vcs", "gh", "--max-age", "0s", "--", "api", "--paginate", fmt.Sprintf("repos/%s/pulls/%d/files", repo, number))
	a := app.New(io.Discard)
	a.Runner, a.CCX = runner, ccx.CLI{Runner: runner}
	cfg := config.Config{Drive: "d", Repo: repo, Checkout: "/checkout", Trunk: "dev"}
	ready := readyFacts(number, head)
	ready.Repo, ready.Lane = repo, "release-start-fix"
	sweep := func() []int {
		posted, err := SweepGos(ctx, a, cfg, s, []machine.Facts{ready}, time.Now().UTC(), io.Discard)
		if err != nil {
			t.Fatal(err)
		}
		return posted
	}
	if posted := sweep(); len(posted) != 0 {
		t.Fatalf("posted %v before root overrode either grade, want a refusal", posted)
	}

	post(client.Draft{Drive: "d", Lane: "root", Kind: "decide", Re: fidelity.Seq, Text: fmt.Sprintf("fidelity-override #%d at 89ed729: %d", number, fidelity.Seq)})
	post(client.Draft{Drive: "d", Lane: "root", Kind: "decide", Re: finder.Seq, Text: fmt.Sprintf("finder-override #%d at 89ed729: %d", number, finder.Seq)})

	if posted := sweep(); !slices.Equal(posted, []int{number}) {
		t.Fatalf("posted %v after root's two overrides that name no PR, want the sweep to gate #%d again and post its go", posted, number)
	}
	gos, err := client.Tail(ctx, client.Query{Drive: "d", Kinds: []string{kindGo}, Lanes: []string{bus.SweepLane}})
	if err != nil {
		t.Fatal(err)
	}
	if len(gos) != 1 || !strings.Contains(gos[0].Text, "finder-override #") {
		t.Fatalf("gos %+v, want one go citing root's finder override", gos)
	}
}

func pipelineOK(ref, head, class, says string) client.Draft {
	return client.Draft{Drive: "d", Lane: gates.PipelineLane, Kind: "review", PRs: []string{ref}, Text: "fidelity: ok " + head + " " + says, Attrs: map[string]string{gates.FidelityClassAttr: class}}
}

func readyFacts(number int, head string) machine.Facts {
	return machine.Facts{Repo: "o/r", Number: number, Head: head, Base: "dev", CI: machine.CISuccess, Mergeable: machine.MergeableClean, Approved: true, Finder: machine.FinderClear, Fidelity: machine.FidelityPass}
}

func TestAHeadTheSweepWouldNotGateKeepsItsTapLineAndAsksNothing(t *testing.T) {
	asked := func(context.Context, machine.Facts) ([]string, error) {
		t.Fatal("asked the sweep about a head it does not gate")
		return nil, nil
	}
	red := readyFacts(1, "abc1234def")
	red.CI = machine.CIFailure
	got := (&Gates{GoAnswer: asked}).tapLines(context.Background(), red)
	if want := []string{"#1 abc1234def CI is not green", "#1 abc1234def " + tapRequired}; !slices.Equal(got, want) {
		t.Fatalf("lines = %q, want %q", got, want)
	}
}

func TestAGoAnswerThatDoesNotReadRefusesNothingAndSaysSo(t *testing.T) {
	var stderr bytes.Buffer
	unread := func(context.Context, machine.Facts) ([]string, error) { return nil, errors.New("no inputs") }
	got := (&Gates{GoAnswer: unread, Stderr: &stderr}).tapLines(context.Background(), readyFacts(1, "abc1234def"))
	if len(got) != 0 {
		t.Fatalf("lines = %q, want none", got)
	}
	if !strings.HasPrefix(stderr.String(), gateUnread) {
		t.Fatalf("stderr = %q, want it to open %q", stderr.String(), gateUnread)
	}
}
