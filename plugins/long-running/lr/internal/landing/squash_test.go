package landing_test

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"slices"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/landing"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	squashTool  = "/plugin/bin/rulings.py"
	squashFirst = "1111111111111111111111111111111111111111"
	squashMoved = "2222222222222222222222222222222222222222"
)

type squashFixture struct {
	t       *testing.T
	squash  *landing.Squash
	fake    *shelltest.Fake
	restack []int
	moved   string
	now     time.Time
	clocks  []string
}

func newSquash(t *testing.T) *squashFixture {
	t.Helper()
	t.Setenv("CCI_HOME", t.TempDir())
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	if _, err := s.WantLane(store.Lane{Name: "incident-api-fix", State: store.LaneDesired}, t0); err != nil {
		t.Fatal(err)
	}
	fake := &shelltest.Fake{}
	fixture := &squashFixture{t: t, fake: fake, now: t0}
	fixture.squash = &landing.Squash{
		Runner: fake, CCX: ccx.CLI{Runner: fake}, Store: s, Drive: "d", Trunk: "dev", Checkout: "/co", Rulings: squashTool,
		FixLane:    func(lane string) (bool, error) { return lane == "dd-1-fix", nil },
		LiveClocks: func() ([]string, error) { return fixture.clocks, nil },
		Restack: func(_ context.Context, number int) (string, error) {
			fixture.restack = append(fixture.restack, number)
			return fixture.moved, nil
		},
		Wait: time.Hour,
		Now:  func() time.Time { return fixture.now },
		Log:  slog.New(slog.NewTextHandler(io.Discard, nil)),
	}
	for _, head := range []string{squashFirst, squashMoved} {
		fixture.shown(t, 7, head, t0.Add(-2*time.Hour))
	}
	fake.Reply("", squashTool, "check-pr")
	fake.Reply("0\n", "ccx", "vcs", "gh", "--max-age", "0s", "--", "api", "repos/"+monorepo+"/compare/dev..."+squashFirst)
	fake.Reply("", "gh", "pr", "merge")
	return fixture
}

func (s *squashFixture) shown(t *testing.T, number int, head string, at time.Time) {
	t.Helper()
	if _, err := s.squash.Store.PutFact(monorepo, number, head, gates.ShownFact, store.Stamp(at), at); err != nil {
		t.Fatal(err)
	}
}

func fixFacts(mutate func(*machine.Facts)) machine.Facts {
	f := machine.Facts{Repo: monorepo, Number: 7, Head: squashFirst, Branch: "fix", Base: "dev", Lane: "incident-api-fix", CI: machine.CISuccess, Approved: true, Fidelity: machine.FidelityPass}
	if mutate != nil {
		mutate(&f)
	}
	return f
}

func (s *squashFixture) merges() []string {
	var merges []string
	for _, call := range s.fake.Calls() {
		if len(call.Argv) > 2 && call.Argv[0] == "gh" && call.Argv[2] == "merge" {
			merges = append(merges, strings.Join(call.Argv, " "))
		}
	}
	return merges
}

func (s *squashFixture) admit(facts ...machine.Facts) []int {
	acts := make([]pass.Act, 0, len(facts))
	for _, f := range facts {
		acts = append(acts, pass.Act{Facts: f, Verdict: machine.Verdict{Action: machine.ActionEnqueue}})
	}
	if err := s.squash.Pass(context.Background(), facts); err != nil {
		s.t.Fatal(err)
	}
	kept := make([]int, 0, len(facts))
	for _, act := range s.squash.Admit(context.Background(), acts, facts) {
		kept = append(kept, act.Facts.Number)
	}
	return kept
}

func squashPost(t *testing.T, kind, lane, text string, to []string, prs ...string) {
	t.Helper()
	if _, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: lane, Kind: kind, Text: text, To: to, PRs: prs}); err != nil {
		t.Fatal(err)
	}
}

func TestAnApprovedGreenIncidentFixSquashesOnceAndTheQueueNeverTakesIt(t *testing.T) {
	f := newSquash(t)
	normal := fixFacts(func(row *machine.Facts) { row.Number, row.Lane, row.Branch = 8, "ordinary", "other" })

	if kept := f.admit(fixFacts(nil), normal); !slices.Equal(kept, []int{8}) {
		t.Fatalf("kept %v", kept)
	}
	if kept := f.admit(fixFacts(nil), normal); !slices.Equal(kept, []int{8}) {
		t.Fatalf("kept %v", kept)
	}

	want := "gh pr merge 7 --repo " + monorepo + " --squash --match-head-commit " + squashFirst
	if merges := f.merges(); !slices.Equal(merges, []string{want}) {
		t.Fatalf("merges = %v, want one %q", merges, want)
	}
}

func TestAFixLaneTheRunnerLaunchedSquashesLikeAnIncidentNamedLane(t *testing.T) {
	f := newSquash(t)

	f.admit(fixFacts(func(row *machine.Facts) { row.Lane = "dd-1-fix" }))

	if len(f.merges()) != 1 {
		t.Fatalf("merges = %v", f.merges())
	}
}

func TestAnotherPersonsFixAnIncidentLaneCarriesIsNeverSquashed(t *testing.T) {
	f := newSquash(t)

	f.admit(fixFacts(func(row *machine.Facts) { row.ForeignAuthor = "teammate" }))

	if len(f.merges()) != 0 {
		t.Fatalf("merges = %v", f.merges())
	}
}

func TestAnIncidentFixThatIsNotReadyStaysOutOfBothThePathAndTheQueue(t *testing.T) {
	for name, mutate := range map[string]func(*machine.Facts){
		"not approved":  func(row *machine.Facts) { row.Approved = false },
		"not green":     func(row *machine.Facts) { row.CI = machine.CIPending },
		"not graded ok": func(row *machine.Facts) { row.Fidelity = "" },
		"held":          func(row *machine.Facts) { row.Hold = machine.MoverRoot },
	} {
		t.Run(name, func(t *testing.T) {
			f := newSquash(t)
			if kept := f.admit(fixFacts(mutate)); len(kept) != 0 {
				t.Fatalf("the queue kept %v", kept)
			}
			if merges := f.merges(); len(merges) != 0 {
				t.Fatalf("merges = %v", merges)
			}
		})
	}
}

func TestATappedFixNeedsNoFidelityGrade(t *testing.T) {
	f := newSquash(t)

	f.admit(fixFacts(func(row *machine.Facts) { row.Fidelity, row.Tap = "", machine.TapTapped }))

	if len(f.merges()) != 1 {
		t.Fatalf("merges = %v", f.merges())
	}
}

func TestAFailedRulingCheckBlocksTheMergeAndNeverOnlyWarns(t *testing.T) {
	f := newSquash(t)
	refusing := &shelltest.Fake{}
	refusing.FailOut(1, "RESERVED-NO-ANSWER reserved/cloud-delete\tNo cloud deletion.\n", squashTool, "check-pr")
	refusing.Reply("0\n", "ccx", "vcs", "gh")
	refusing.Reply("", "gh", "pr", "merge")
	f.fake = refusing
	f.squash.Runner, f.squash.CCX = refusing, ccx.CLI{Runner: refusing}

	f.admit(fixFacts(nil))

	if merges := f.merges(); len(merges) != 0 {
		t.Fatalf("merged past a failed check: %v", merges)
	}
	blockers, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{"blocker"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(blockers) != 1 || !strings.Contains(blockers[0].Text, "RULINGS-REFUSED") || !strings.Contains(blockers[0].Text, "reserved/cloud-delete") || !slices.Equal(blockers[0].To, []string{"incident-api-fix", "root"}) {
		t.Fatalf("blockers = %+v", blockers)
	}
}

func TestAnIncidentFixFromARetiredLaneTellsTheLaneThatSucceededIt(t *testing.T) {
	f := newSquash(t)
	refusing := &shelltest.Fake{}
	refusing.FailOut(1, "RESERVED-NO-ANSWER reserved/cloud-delete\tNo cloud deletion.\n", squashTool, "check-pr")
	refusing.Reply("0\n", "ccx", "vcs", "gh")
	f.squash.Runner, f.squash.CCX = refusing, ccx.CLI{Runner: refusing}
	retire(t, f.squash.Store, "incident-api-fix", "incident-api-fix-2")

	f.admit(fixFacts(nil))

	blockers, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{"blocker"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(blockers) != 1 || !slices.Equal(blockers[0].To, []string{"incident-api-fix-2", "root"}) {
		t.Fatalf("blockers = %+v", blockers)
	}
}

func TestAHeldFixMergesOnlyWhileAnOpenIncidentNamesItAndSaysSo(t *testing.T) {
	f := newSquash(t)
	squashPost(t, "incident", "root", "api is down", []string{"incident-api-fix"})

	f.admit(fixFacts(func(row *machine.Facts) { row.Hold = machine.MoverRoot }))

	if len(f.merges()) != 1 {
		t.Fatalf("merges = %v", f.merges())
	}
	reports, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{"report"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(reports) != 1 || !strings.HasPrefix(reports[0].Text, "INCIDENT-HOLD-WARNED #7") {
		t.Fatalf("reports = %+v", reports)
	}
}

func TestAClosedIncidentOrARootClearingTakesTheFixOffThePath(t *testing.T) {
	for name, post := range map[string]func(*testing.T){
		"closed": func(t *testing.T) {
			squashPost(t, "decide", "root", "INCIDENT api CLOSED", []string{"incident-api-fix"})
		},
		"cleared": func(t *testing.T) {
			squashPost(t, "decide", "root", "#7 is not an incident fix", []string{"incident-api-fix"}, monorepo+"#"+strconv.Itoa(7))
		},
	} {
		t.Run(name, func(t *testing.T) {
			f := newSquash(t)
			post(t)
			if kept := f.admit(fixFacts(nil)); !slices.Equal(kept, []int{7}) {
				t.Fatalf("the queue lost it: %v", kept)
			}
			if merges := f.merges(); len(merges) != 0 {
				t.Fatalf("merges = %v", merges)
			}
		})
	}
}

func TestAFixBehindTrunkRebasesThenMergesAtItsNewHeadOnlyOnceCIIsGreenThere(t *testing.T) {
	f := newSquash(t)
	behind := &shelltest.Fake{}
	behind.Reply("", squashTool, "check-pr")
	behind.Reply("3\n", "ccx", "vcs", "gh", "--max-age", "0s", "--", "api", "repos/"+monorepo+"/compare/dev..."+squashFirst)
	behind.Reply("0\n", "ccx", "vcs", "gh", "--max-age", "0s", "--", "api", "repos/"+monorepo+"/compare/dev..."+squashMoved)
	behind.Reply("", "gh", "pr", "merge")
	f.fake = behind
	f.squash.Runner, f.squash.CCX = behind, ccx.CLI{Runner: behind}
	f.moved = "BASE dev tip\nMOVED fix " + squashFirst + " " + squashMoved + "\n"

	f.admit(fixFacts(nil))

	if !slices.Equal(f.restack, []int{7}) {
		t.Fatalf("restacks = %v", f.restack)
	}
	if merges := f.merges(); len(merges) != 0 {
		t.Fatalf("merged at the old head: %v", merges)
	}

	f.admit(fixFacts(func(row *machine.Facts) { row.Head, row.CI, row.Fidelity = squashMoved, machine.CIPending, "" }))

	if merges := f.merges(); len(merges) != 0 {
		t.Fatalf("merged the new head before its CI ran: %v", merges)
	}

	f.admit(fixFacts(func(row *machine.Facts) { row.Head, row.Fidelity = squashMoved, "" }))

	want := "gh pr merge 7 --repo " + monorepo + " --squash --match-head-commit " + squashMoved
	if merges := f.merges(); !slices.Equal(merges, []string{want}) {
		t.Fatalf("merges = %v", merges)
	}
}

func TestAnUntappedIncidentFixInTheQueueIsLeftToTheSquashOnlyWhenTheSquashWouldMergeIt(t *testing.T) {
	f := newSquash(t)
	queued := func(row *machine.Facts) { row.Queue, row.QueuedHead = machine.QueueQueued, row.Head }
	fix := fixFacts(queued)
	ungraded := fixFacts(func(row *machine.Facts) { queued(row); row.Number, row.Branch, row.Fidelity = 9, "ungraded", "" })
	ordinary := fixFacts(func(row *machine.Facts) { queued(row); row.Number, row.Lane, row.Branch = 8, "ordinary", "other" })
	snapshot := []machine.Facts{fix, ungraded, ordinary}
	acts := make([]pass.Act, 0, len(snapshot))
	for _, row := range snapshot {
		acts = append(acts, pass.Act{Facts: row, Verdict: machine.Classify(row)})
	}

	if err := f.squash.Pass(context.Background(), snapshot); err != nil {
		t.Fatal(err)
	}
	kept := f.squash.Admit(context.Background(), acts, snapshot)

	numbers := make([]int, 0, len(kept))
	for _, act := range kept {
		numbers = append(numbers, act.Facts.Number)
	}
	if machine.Classify(fix).Action != machine.ActionDequeue || !slices.Equal(numbers, []int{9, 8}) {
		t.Fatalf("kept = %+v", kept)
	}
	if len(f.merges()) != 1 {
		t.Fatalf("merges = %v", f.merges())
	}
}

func TestARebasedFixIsRebasedAgainOnlyWhenTrunkTouchedItsFiles(t *testing.T) {
	for name, landedFiles := range map[string]string{"another file": "other.go\n", "its own file": "fix.go\n"} {
		t.Run(name, func(t *testing.T) {
			f := newSquash(t)
			if _, err := f.squash.Store.PutFact(monorepo, 7, squashFirst, "rebased-direct", "old", t0); err != nil {
				t.Fatal(err)
			}
			behind := &shelltest.Fake{}
			behind.Reply("", squashTool, "check-pr")
			behind.Reply("1\n", "ccx", "vcs", "gh")
			behind.Reply("tip\trefs/heads/dev\n", "git", "-C", "/co", "ls-remote")
			behind.Reply("", "git", "-C", "/co", "fetch")
			behind.Reply("base\n", "git", "-C", "/co", "merge-base")
			behind.Reply(landedFiles, "git", "-C", "/co", "diff", "--name-only", "base", "tip")
			behind.Reply("fix.go\n", "git", "-C", "/co", "diff", "--name-only", "base", squashFirst)
			behind.Reply("", "gh", "pr", "merge")
			f.fake = behind
			f.squash.Runner, f.squash.CCX = behind, ccx.CLI{Runner: behind}
			f.moved = "MOVED fix " + squashFirst + " " + squashMoved + "\n"

			f.admit(fixFacts(nil))

			touched := strings.Contains(landedFiles, "fix.go")
			if touched != (len(f.restack) == 1) || touched == (len(f.merges()) == 1) {
				t.Fatalf("restacks = %v, merges = %v", f.restack, f.merges())
			}
		})
	}
}

func TestAFixRootTookOffTheIncidentPathTellsItsLaneOnce(t *testing.T) {
	s := newSquash(t)
	squashPost(t, "decide", "root", "#7 is not an incident fix", []string{"incident-api-fix"}, monorepo+"#7")

	s.admit(fixFacts(nil))
	s.admit(fixFacts(nil))

	records, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{"report"}, Lanes: []string{"supervisor"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(records) != 1 || !strings.HasPrefix(records[0].Text, "INCIDENT-TAG-CLEARED #7") || !slices.Contains(records[0].To, "incident-api-fix") {
		t.Fatalf("records = %+v", records)
	}
}

func TestANativeStackMemberIncidentFixLandsThroughGhStackMerge(t *testing.T) {
	f := newSquash(t)
	stacked := &shelltest.Fake{}
	stacked.Reply("", squashTool, "check-pr")
	stacked.Reply("0\n", "ccx", "vcs", "gh")
	stacked.Fail(1, "GraphQL: Pull request is part of a stack and must be merged using the asynchronous merge REST API", "gh", "pr", "merge")
	stacked.Reply("", "gh", "stack", "merge")
	f.fake = stacked
	f.squash.Runner, f.squash.CCX = stacked, ccx.CLI{Runner: stacked}

	f.admit(fixFacts(nil))

	want := []string{"gh pr merge 7 --repo " + monorepo + " --squash --match-head-commit " + squashFirst, "gh stack merge 7 --yes --squash"}
	if merges := f.merges(); !slices.Equal(merges, want) {
		t.Fatalf("merges = %v, want %v", merges, want)
	}
	if calls := stacked.Calls(); !slices.Contains(calls[len(calls)-1].Env, "GH_REPO="+monorepo) {
		t.Fatalf("gh stack merge env = %v", calls[len(calls)-1].Env)
	}
	blockers, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{"blocker"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(blockers) != 0 {
		t.Fatalf("blockers = %+v", blockers)
	}
}

func TestAnAlertFixLandsItselfAnHourAfterItsPostReachedHimAndNotBefore(t *testing.T) {
	f := newSquash(t)
	fix := fixFacts(func(row *machine.Facts) { row.Number, row.Branch = 9, "nine" })
	f.shown(t, 9, squashFirst, t0.Add(-30*time.Minute))

	if kept := f.admit(fix); len(kept) != 0 {
		t.Fatalf("the queue kept %v while its hour runs", kept)
	}
	if merges := f.merges(); len(merges) != 0 {
		t.Fatalf("merged before its hour: %v", merges)
	}
	stored, err := f.squash.Store.Facts(monorepo, 9, squashFirst)
	if err != nil || stored[gates.MergesAtFact] != store.Stamp(t0.Add(30*time.Minute)) {
		t.Fatalf("merges at %q, %v, want %s", stored[gates.MergesAtFact], err, store.Stamp(t0.Add(30*time.Minute)))
	}

	f.now = t0.Add(30 * time.Minute)
	f.admit(fix)

	want := "gh pr merge 9 --repo " + monorepo + " --squash --match-head-commit " + squashFirst
	if merges := f.merges(); !slices.Equal(merges, []string{want}) {
		t.Fatalf("merges = %v, want %q once its hour passed", merges, want)
	}
}

func TestAnAlertFixWhosePostHasNotReachedHimOrThatHeHeldNeverLandsOnTheClock(t *testing.T) {
	for name, fix := range map[string]machine.Facts{
		"post not shown": fixFacts(func(row *machine.Facts) { row.Number, row.Branch = 9, "nine" }),
		"hold tapped":    fixFacts(func(row *machine.Facts) { row.Tap = machine.TapWithdrawn }),
		"held":           fixFacts(func(row *machine.Facts) { row.Hold = machine.MoverRoot }),
	} {
		t.Run(name, func(t *testing.T) {
			f := newSquash(t)
			if kept := f.admit(fix); len(kept) != 0 {
				t.Fatalf("the queue kept %v", kept)
			}
			if merges := f.merges(); len(merges) != 0 {
				t.Fatalf("merges = %v", merges)
			}
		})
	}
}

func TestHisApproveOrAnOpenIncidentLandsAnAlertFixWithoutWaitingItsHour(t *testing.T) {
	for name, tc := range map[string]struct {
		post   func(*testing.T)
		mutate func(*machine.Facts)
		clocks []string
	}{
		"approved":       {mutate: func(row *machine.Facts) { row.Tap = machine.TapTapped }},
		"open incident":  {post: func(t *testing.T) { squashPost(t, "incident", "root", "api is down", []string{"incident-api-fix"}) }},
		"incident clock": {clocks: []string{"incident-api-fix"}},
	} {
		t.Run(name, func(t *testing.T) {
			f := newSquash(t)
			f.clocks = tc.clocks
			if tc.post != nil {
				tc.post(t)
			}
			f.admit(fixFacts(func(row *machine.Facts) {
				row.Number, row.Branch = 9, "nine"
				if tc.mutate != nil {
					tc.mutate(row)
				}
			}))
			if merges := f.merges(); len(merges) != 1 {
				t.Fatalf("merges = %v, want one at once", merges)
			}
		})
	}
}

func TestTheLandingHostsAdmitKeepsAnIncidentFixFromTheQueueButNeverMergesOrPosts(t *testing.T) {
	f := newSquash(t)
	squashPost(t, "decide", "root", "#8 is not an incident fix", []string{"incident-api-fix"}, monorepo+"#8")
	cleared := fixFacts(func(row *machine.Facts) { row.Number, row.Branch = 8, "eight" })
	acts := []pass.Act{{Facts: fixFacts(nil), Verdict: machine.Verdict{Action: machine.ActionEnqueue}}, {Facts: cleared, Verdict: machine.Verdict{Action: machine.ActionEnqueue}}}

	kept := f.squash.Admit(context.Background(), acts, []machine.Facts{fixFacts(nil), cleared})

	if len(kept) != 1 || kept[0].Facts.Number != 8 {
		t.Fatalf("kept %+v, want only the cleared #8", kept)
	}
	if merges := f.merges(); len(merges) != 0 {
		t.Fatalf("the landing host merged %v", merges)
	}
	records, err := client.Tail(context.Background(), client.Query{Drive: "d", Lanes: []string{"supervisor"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(records) != 0 {
		t.Fatalf("the landing host posted %+v", records)
	}
}

func devRedFix(mutate func(*machine.Facts)) machine.Facts {
	return fixFacts(func(row *machine.Facts) {
		row.Number, row.Branch, row.Lane = 51688, "yasyf/upload-rs-park-closed-unmirrored", "dev-red-5a02b5902d-fix"
		row.Mergeable, row.Finder, row.FinderOverride = machine.MergeableClean, machine.FinderAsk, true
		if mutate != nil {
			mutate(row)
		}
	})
}

func TestADevRedFixWithNoTapNeverSquashesThoughAnIncidentOrARootGoNamesItAndItsHourPassed(t *testing.T) {
	for name, tc := range map[string]struct {
		fix  machine.Facts
		post func(*testing.T)
	}{
		"#51688 at a1ffe9f, root's incident 524795 naming it": {
			fix: devRedFix(nil),
			post: func(t *testing.T) {
				squashPost(t, "incident", "root", "dev-red-2651ca7ef1 is dev-red-5a02b5902d: dev-red-5a02b5902d-fix owns it; #51688 at a1ffe9f is the fix, green, approved, every grade cleared, waiting only on his Approve.", []string{"dev-red-5a02b5902d-fix", "desk-runner", "supervisor"})
			},
		},
		"#51764 at 7c14588, root's go 523326 naming it": {
			fix: devRedFix(func(row *machine.Facts) {
				row.Number, row.Branch, row.Lane, row.Finder, row.FinderOverride = 51764, "yasyf/restore-horizon-wake", "dev-red-5a02b5902d-fix-case1", "", false
			}),
			post: func(t *testing.T) {
				squashPost(t, "go", "root", "GO for the restore::lazy class: dev-red-5a02b5902d-fix-case1 owns it with #51764 at 7c14588 (horizon case passes, 523278). New-tree product code, and dev is green, so it lands on his tap with its options line (dbeef4d); post the tap card.", []string{"dev-red-5a02b5902d-fix-case1", "supervisor"}, monorepo+"#51764")
			},
		},
	} {
		t.Run(name, func(t *testing.T) {
			f := newSquash(t)
			f.squash.FixLane = func(lane string) (bool, error) { return strings.HasPrefix(lane, "dev-red-"), nil }
			f.clocks = []string{tc.fix.Lane}
			f.shown(t, tc.fix.Number, tc.fix.Head, t0.Add(-2*time.Hour))
			tc.post(t)

			if kept := f.admit(tc.fix); !slices.Equal(kept, []int{tc.fix.Number}) {
				t.Fatalf("the squash took %v from the gate that says no tap, kept %v", tc.fix.Number, kept)
			}
			if merges := f.merges(); len(merges) != 0 {
				t.Fatalf("a dev-red fix with gates-missing [\"no tap\"] merged: %v", merges)
			}
		})
	}
}

func TestAnAlertFixItsIncidentScopedOutWaitsOnHisTapAndAnActiveOneStillLandsAtOnce(t *testing.T) {
	const fix, caseLane = "sentry-7832068463-fix", "sentry-7832068463-fix-case1"
	for name, tc := range map[string]struct {
		said   [][2]string
		merges int
	}{
		"#51766, its case lane scoped active no under root 524587": {[][2]string{{fix, "SCOPE " + fix + ": old tree only. active yes: production Memories requests failed 3 times"}, {caseLane, "SCOPE " + caseLane + ": active no: only three calls from one user curl script"}}, 0},
		"root's newest verdict reads active no":                    {[][2]string{{fix, "SCOPE " + fix + ": active yes: three failed calls"}, {"root", "Scoped out, active no: one user's curl script, no customer run failing."}}, 0},
		"its newest scope reads active yes":                        {[][2]string{{caseLane, "SCOPE " + caseLane + ": active no: one script"}, {fix, "SCOPE " + fix + ": active yes: customer runs fail"}}, 1},
	} {
		t.Run(name, func(t *testing.T) {
			f := newSquash(t)
			f.squash.FixLane = func(lane string) (bool, error) { return strings.HasPrefix(lane, "sentry-"), nil }
			for _, said := range tc.said {
				to := []string{"root"}
				if said[0] == "root" {
					to = []string{fix}
				}
				squashPost(t, "incident", said[0], said[1], to)
			}
			squashPost(t, "incident", "root", "Ratified: incident sentry-7832068463. "+fix+" owns it with forward-fix authority.", []string{fix})

			f.admit(fixFacts(func(row *machine.Facts) { row.Number, row.Branch, row.Lane = 51766, "memories", fix }))

			if merges := f.merges(); len(merges) != tc.merges {
				t.Fatalf("merges = %v, want %d", merges, tc.merges)
			}
		})
	}
}

func TestADevRedFixSquashesOnceHisTapSatisfiesItsGate(t *testing.T) {
	f := newSquash(t)
	f.squash.FixLane = func(lane string) (bool, error) { return strings.HasPrefix(lane, "dev-red-"), nil }

	if kept := f.admit(devRedFix(func(row *machine.Facts) { row.Tap = machine.TapTapped })); len(kept) != 0 {
		t.Fatalf("the queue kept %v", kept)
	}

	want := "gh pr merge 51688 --repo " + monorepo + " --squash --match-head-commit " + squashFirst
	if merges := f.merges(); !slices.Equal(merges, []string{want}) {
		t.Fatalf("merges = %v, want %q", merges, want)
	}
}

func TestASquashMergePostsOneLandedRecordSoTheRowReadsLanded(t *testing.T) {
	f := newSquash(t)

	f.admit(fixFacts(nil))
	f.admit(fixFacts(nil))

	landed, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{"landed"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(landed) != 1 || !strings.HasPrefix(landed[0].Text, "LANDED #7 at "+squashFirst[:10]) || !slices.Contains(landed[0].To, "incident-api-fix") {
		t.Fatalf("landed = %+v", landed)
	}
}

func TestAFixAnotherHostAlreadyMergedIsNeitherRetriedNorReportedAsAFailedSquash(t *testing.T) {
	f := newSquash(t)
	raced := &shelltest.Fake{}
	raced.Reply("", squashTool, "check-pr")
	raced.Reply("MERGED\n", "ccx", "vcs", "gh", "--max-age", "0s", "--", "pr", "view", "7")
	raced.Reply("0\n", "ccx", "vcs", "gh")
	raced.Fail(1, "GraphQL: Pull Request is not mergeable", "gh", "pr", "merge")
	f.fake = raced
	f.squash.Runner, f.squash.CCX = raced, ccx.CLI{Runner: raced}

	f.admit(fixFacts(nil))
	f.admit(fixFacts(nil))

	if merges := f.merges(); len(merges) != 1 {
		t.Fatalf("merges = %v, want the one refused try", merges)
	}
	records, err := client.Tail(context.Background(), client.Query{Drive: "d", Lanes: []string{"supervisor"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(records) != 0 {
		t.Fatalf("posted %+v after another host merged it", records)
	}
}

func TestARestackRefusedBecauseThePRMergedPostsNoBlockerAndReadsItsStateOnce(t *testing.T) {
	for state, blocked := range map[string]bool{"MERGED": false, "CLOSED": true} {
		t.Run(state, func(t *testing.T) {
			f := newSquash(t)
			gone := &shelltest.Fake{}
			gone.Reply("", squashTool, "check-pr")
			gone.Reply(state+"\n", "ccx", "vcs", "gh", "--max-age", "0s", "--", "pr", "view", "7")
			gone.Reply("3\n", "ccx", "vcs", "gh", "--max-age", "0s", "--", "api", "repos/"+monorepo+"/compare/dev..."+squashFirst)
			f.fake = gone
			f.squash.Runner, f.squash.CCX = gone, ccx.CLI{Runner: gone}
			f.squash.Restack = func(context.Context, int) (string, error) {
				return "", errors.New("remote-restack: #7 is not an open pull request")
			}

			f.admit(fixFacts(nil))
			f.admit(fixFacts(nil))

			if merges := f.merges(); len(merges) != 0 {
				t.Fatalf("merges = %v", merges)
			}
			records, err := client.Tail(context.Background(), client.Query{Drive: "d", Lanes: []string{"supervisor"}, Kinds: []string{"blocker"}})
			if err != nil {
				t.Fatal(err)
			}
			if (len(records) > 0) != blocked {
				t.Fatalf("blockers = %+v, want blocked %v", records, blocked)
			}
			if blocked {
				return
			}
			var reads int
			for _, call := range gone.Calls() {
				if slices.Contains(call.Argv, "view") {
					reads++
				}
			}
			if reads != 1 {
				t.Fatalf("read the merged PR's state %d times, want once", reads)
			}
		})
	}
}
