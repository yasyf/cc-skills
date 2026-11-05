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

func sweepReads(t *testing.T, heads int) (unchanged, oneChanged int) {
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
		post(client.Draft{Drive: "d", Lane: "lane-1", Kind: "head", Text: fmt.Sprintf("HEAD #%d", number), PRs: []string{fmt.Sprintf("o/r#%d", number)}, Attrs: map[string]string{fmt.Sprintf("head:%d", number): head, gates.PortOfAttr: "40810"}})
		snapshot[i] = machine.Facts{Repo: "o/r", Number: number, Head: head, Base: "dev", Lane: "lane-1", CI: machine.CISuccess, Approved: true, Finder: machine.FinderClear, Fidelity: machine.FidelityPass}
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

func TestTheSweepReadsAsMuchForAThousandReadyHeadsAsForOneAndOnlyForWhatChanged(t *testing.T) {
	oneUnchanged, oneChanged := sweepReads(t, 1)
	manyUnchanged, manyChanged := sweepReads(t, 1000)

	if oneUnchanged != 0 || manyUnchanged != oneUnchanged {
		t.Fatalf("a sweep with nothing changed made %d reads at N=1 and %d at N=1000, want none at either", oneUnchanged, manyUnchanged)
	}
	if oneChanged != 1 || manyChanged != oneChanged {
		t.Fatalf("a sweep after one PR's record made %d reads at N=1 and %d at N=1000, want that PR's one read at both", oneChanged, manyChanged)
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
		{Drive: "d", Lane: "fix-1", Kind: kindGo, PRs: []string{"o/r#1"}, Text: "go #1 at abc1234 on green [exempt: test-only-or-ci-only]: fidelity ok #1, finder ok #2"},
		{Drive: "d", Lane: "root", Kind: kindGo, PRs: []string{"o/r#2"}, Text: "go #2 at bcd2345 on green [exempt: test-only-or-ci-only]: fidelity ok #1, finder ok #2"},
		{Drive: "d", Lane: "fix-1", Kind: kindGo, PRs: []string{"o/r#3"}, Text: "go #3 at 0000000 on green [exempt: test-only-or-ci-only]: fidelity ok #1, finder ok #2"},
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
	if len(refused) != 1 || !strings.HasPrefix(refused[0].Text, "no go for #1 at abc1234def: CI is not green") || !slices.Equal(refused[0].To, []string{"fix-1", "owner-1"}) {
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
				{Drive: "d", Lane: "fidelity-desk", Kind: "review", PRs: []string{ref}, Text: "fidelity: ok " + c.head + " class test-only: one wait"},
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
