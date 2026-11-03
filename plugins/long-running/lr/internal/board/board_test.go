package board

import (
	"context"
	"encoding/json"
	"fmt"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	repo   = "org/repo"
	number = 45583
	head   = "e718a7434fa01ee9835f992a9a647a825350732c"
	moved  = "aa11bb22cc33dd44ee55ff6677889900aabbccdd"
)

var (
	t0    = time.Date(2026, 10, 30, 21, 0, 0, 0, time.UTC)
	words = map[string]string{FactChange: "chats load faster", FactPlan: "nothing differed", FactRisk: "one cache key", FactBlocks: "your approval"}
)

type recorded struct {
	taps  []TapRecord
	holds []Key
	notes []string
}

func (r *recorded) hooks() Hooks {
	return Hooks{
		Tap:  func(tap TapRecord) error { r.taps = append(r.taps, tap); return nil },
		Hold: func(pr int, head string) error { r.holds = append(r.holds, Key{pr, head}); return nil },
		Note: func(lane, text string) error { r.notes = append(r.notes, lane+": "+text); return nil },
	}
}

func facts(change func(*machine.Facts)) map[int]machine.Facts {
	f := machine.Facts{Repo: repo, Number: number, Head: head, Base: "dev", Lane: "lane-7", CI: machine.CISuccess, Mergeable: machine.MergeableClean, Approved: true, Finder: machine.FinderClear, Fidelity: machine.FidelityPass}
	if change != nil {
		change(&f)
	}
	return map[int]machine.Facts{number: f}
}

func withItem(t *testing.T) *store.Store {
	t.Helper()
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.Close() })
	openTap(t, s, head, t0)
	return s
}

func withWords(t *testing.T) *store.Store {
	t.Helper()
	s := withItem(t)
	for kind, value := range words {
		put(t, s, head, kind, value)
	}
	return s
}

func openTap(t *testing.T, s *store.Store, on string, at time.Time) {
	t.Helper()
	if _, err := s.OpenItem(store.NewItem{Kind: "tap", Repo: repo, Number: number, Head: on, Mover: machine.MoverOwner, Deadline: 12 * time.Hour}, at); err != nil {
		t.Fatal(err)
	}
}

func put(t *testing.T, s *store.Store, on, kind, value string) {
	t.Helper()
	if _, err := s.PutFact(repo, number, on, kind, value, t0); err != nil {
		t.Fatal(err)
	}
}

func rows(t *testing.T, s *store.Store, snapshot map[int]machine.Facts) []Row {
	t.Helper()
	found, err := Rows(s, repo, snapshot)
	if err != nil {
		t.Fatal(err)
	}
	return found
}

func stored(t *testing.T, s *store.Store) map[string]string {
	t.Helper()
	found, err := s.Facts(repo, number, head)
	if err != nil {
		t.Fatal(err)
	}
	return found
}

func TestAnOwnerItemIsOnePRRowCarryingEveryFieldTheOwnerReads(t *testing.T) {
	block := Render(repo, rows(t, withWords(t), facts(nil)), nil)
	want := PRRow{number, head[:12], "#45583", "chats load faster", "nothing differed", "one cache key", "every other gate is met", "your approval"}
	if len(block.Groups) != 1 || !reflect.DeepEqual(block.Groups[0].PRs, []PRRow{want}) {
		t.Fatalf("block = %+v", block)
	}
	if block.ID != MergeBlock || block.Type != "pr-groups" || block.Repo != repo {
		t.Fatalf("block = %+v", block)
	}
}

func TestAHeadRecordMissingAFieldMarksItMissingNeverBlank(t *testing.T) {
	s := withItem(t)
	put(t, s, head, FactChange, "chats load faster")
	found := rows(t, s, facts(nil))
	if got, want := found[0].MissingWords(), []string{"what differed from the plan", "the risk", "what blocks it"}; !reflect.DeepEqual(got, want) {
		t.Fatalf("missing = %v", got)
	}
	row := Render(repo, found, nil).Groups[0].PRs[0]
	if row.Change != "chats load faster" || row.Risk != "missing: lane-7 owes this on its head record" {
		t.Fatalf("row = %+v", row)
	}
}

func TestRowsGroupByTheFeatureTheirHeadRecordNamesElseTheirLane(t *testing.T) {
	named := map[string]string{FactGroup: "Chat speed", FactSummary: "Both make chats load faster."}
	block := Render(repo, []Row{{PR: 1, Head: "a", Lane: "l1", Words: named}, {PR: 2, Head: "b", Lane: "l2", Words: named}, {PR: 3, Head: "c", Lane: "l3"}}, nil)
	if len(block.Groups) != 2 || len(block.Groups[0].PRs) != 2 || block.Groups[0].Summary != "Both make chats load faster." {
		t.Fatalf("groups = %+v", block.Groups)
	}
	if block.Groups[1].Title != "l3" || block.Groups[1].Summary != "From lane l3." {
		t.Fatalf("group = %+v", block.Groups[1])
	}
}

func TestAnAnsweredKeyNeverRendersAgain(t *testing.T) {
	block := Render(repo, rows(t, withItem(t), facts(nil)), map[Key]bool{{number, head}: true})
	if len(block.Groups) != 0 {
		t.Fatalf("groups = %+v", block.Groups)
	}
}

func TestApprovedBecomesATapFactAndATapRecord(t *testing.T) {
	s, hooks := withItem(t), &recorded{}
	if err := Answer(s, repo, hooks.hooks(), rows(t, s, facts(nil))[0], verdictApproved, t0); err != nil {
		t.Fatal(err)
	}
	if stored(t, s)["tap"] != "tapped" {
		t.Fatalf("facts = %v", stored(t, s))
	}
	if want := []TapRecord{{number, head, "owner", machine.TapTapped}}; !reflect.DeepEqual(hooks.taps, want) {
		t.Fatalf("taps = %+v", hooks.taps)
	}
	if len(hooks.holds) != 0 {
		t.Fatalf("holds = %+v", hooks.holds)
	}
	if found := rows(t, s, facts(func(f *machine.Facts) { f.Tap = machine.TapTapped })); len(found) != 0 {
		t.Fatalf("a tapped head keeps its row: %+v", found)
	}
}

func TestRejectedGoesToTheHoldHookAndTapsNothing(t *testing.T) {
	s, hooks := withItem(t), &recorded{}
	if err := Answer(s, repo, hooks.hooks(), rows(t, s, facts(nil))[0], verdictRejected, t0); err != nil {
		t.Fatal(err)
	}
	if want := []Key{{number, head}}; !reflect.DeepEqual(hooks.holds, want) {
		t.Fatalf("holds = %+v", hooks.holds)
	}
	if len(hooks.taps) != 0 || len(stored(t, s)) != 0 {
		t.Fatalf("taps = %+v, facts = %v", hooks.taps, stored(t, s))
	}
}

func TestAnUnknownAnswerIsRefused(t *testing.T) {
	s, hooks := withItem(t), &recorded{}
	err := Answer(s, repo, hooks.hooks(), rows(t, s, facts(nil))[0], "Ship it", t0)
	if err == nil || err.Error() != `not a board answer: "Ship it"` {
		t.Fatalf("err = %v", err)
	}
}

func TestAPatchIdenticalRebaseCarriesTheTapAndAsksNothing(t *testing.T) {
	s := withItem(t)
	put(t, s, head, "tap", "tapped")
	later := t0.Add(time.Hour)
	openTap(t, s, moved, later)
	if _, err := gates.CarryTap(s, repo, number, head, moved, map[string]string{head: "p1", moved: "p1"}, later); err != nil {
		t.Fatal(err)
	}
	snapshot := facts(func(f *machine.Facts) { f.Head, f.Tap = moved, machine.TapTapped })
	if found := rows(t, s, snapshot); len(found) != 0 {
		t.Fatalf("rows = %+v", found)
	}
}

func TestARebaseThatChangedThePatchAsksAgainNamingNoCarry(t *testing.T) {
	s := withItem(t)
	put(t, s, head, "tap", "tapped")
	openTap(t, s, moved, t0.Add(time.Hour))
	found := rows(t, s, facts(func(f *machine.Facts) { f.Head = moved }))
	if len(found) != 1 || found[0].Head != moved || found[0].CarriedFrom != "" {
		t.Fatalf("rows = %+v", found)
	}
}

func TestACarriedRowSaysWhichHeadItCameFrom(t *testing.T) {
	block := Render(repo, []Row{{PR: number, Head: moved, Lane: "l", Gates: []string{"no tap"}, CarriedFrom: head}}, nil)
	if want := "every other gate is met; rebased from " + head[:12] + ", same patch"; block.Groups[0].PRs[0].Review != want {
		t.Fatalf("review = %q", block.Groups[0].PRs[0].Review)
	}
}

func TestARowNamesEveryOtherGateTheHeadStillFails(t *testing.T) {
	found := rows(t, withItem(t), facts(func(f *machine.Facts) { f.CI = machine.CIPending }))
	if review := Render(repo, found, nil).Groups[0].PRs[0].Review; review != "CI is not green" {
		t.Fatalf("review = %q", review)
	}
}

func TestAStaleItemForAnOlderHeadRendersNothing(t *testing.T) {
	if found := rows(t, withItem(t), facts(func(f *machine.Facts) { f.Head = moved })); len(found) != 0 {
		t.Fatalf("rows = %+v", found)
	}
}

func TestAConditionalTapKeepsTheRowAndShowsTheCondition(t *testing.T) {
	s := withItem(t)
	put(t, s, head, "tap", "conditional")
	put(t, s, head, "condition", "only if the finding is superseded")
	found := rows(t, s, facts(func(f *machine.Facts) {
		f.Tap, f.Condition = machine.TapConditional, "only if the finding is superseded"
	}))
	if review := Render(repo, found, nil).Groups[0].PRs[0].Review; review != "tap given only if the finding is superseded" {
		t.Fatalf("review = %q", review)
	}
}

type passShell struct {
	outcomes string
	pushed   []string
}

func (p *passShell) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	switch c.Argv[1] {
	case "outcomes":
		return shell.Result{Stdout: p.outcomes}, nil
	case "update-block":
		p.pushed = append(p.pushed, c.Stdin)
		return shell.Result{}, nil
	}
	return shell.Result{}, fmt.Errorf("unexpected call %v", c.Argv)
}

func TestAPassAppliesEachVerdictAtItsHeadAndPushesOnlyWhatStillAsks(t *testing.T) {
	s, hooks := withWords(t), &recorded{}
	on := &passShell{outcomes: fmt.Sprintf(`{"doc":{},"interactions":{"triage":{%q:{"%d":{"verdict":"approved","head":"%s","at":"2026-10-30T21:00:00Z"}}}}}`, MergeBlock, number, head[:7])}
	pass := Pass{Store: s, Repo: repo, Runner: on, Session: "s1", Hooks: hooks.hooks(), Now: func() time.Time { return t0 }}
	block, err := pass.Run(context.Background(), facts(nil))
	if err != nil {
		t.Fatal(err)
	}
	if len(block.Groups) != 0 || len(hooks.taps) != 1 || stored(t, s)["tap"] != "tapped" {
		t.Fatalf("block = %+v, taps = %+v", block, hooks.taps)
	}
	var pushed Block
	if err := json.Unmarshal([]byte(on.pushed[0]), &pushed); err != nil || pushed.ID != MergeBlock {
		t.Fatalf("pushed = %q, %v", on.pushed[0], err)
	}
}

func TestAVerdictAtAnOlderHeadStillAsksAtTheNewOne(t *testing.T) {
	s, hooks := withWords(t), &recorded{}
	on := &passShell{outcomes: fmt.Sprintf(`{"doc":{},"interactions":{"triage":{%q:{"%d":{"verdict":"approved","head":"aaaaaaa","at":"2026-10-30T21:00:00Z"}}}}}`, MergeBlock, number)}
	pass := Pass{Store: s, Repo: repo, Runner: on, Session: "s1", Hooks: hooks.hooks(), Now: func() time.Time { return t0 }}
	block, err := pass.Run(context.Background(), facts(nil))
	if err != nil {
		t.Fatal(err)
	}
	if len(block.Groups) != 1 || len(hooks.taps) != 0 {
		t.Fatalf("block = %+v, taps = %+v", block, hooks.taps)
	}
}

func TestALaneHearsOnceAboutTheFieldsItsRowLacks(t *testing.T) {
	s, hooks := withItem(t), &recorded{}
	on := &passShell{outcomes: `{"doc":{},"interactions":{}}`}
	pass := Pass{Store: s, Repo: repo, Runner: on, Session: "s1", Hooks: hooks.hooks(), Now: func() time.Time { return t0 }}
	for range 2 {
		if _, err := pass.Run(context.Background(), facts(nil)); err != nil {
			t.Fatal(err)
		}
	}
	if len(hooks.notes) != 1 || !strings.HasPrefix(hooks.notes[0], "lane-7: #45583 at "+head[:12]+" is on the owner's board without what it changes for a user") {
		t.Fatalf("notes = %q", hooks.notes)
	}
}
