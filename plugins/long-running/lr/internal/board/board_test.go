package board

import (
	"context"
	"fmt"
	"reflect"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
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
	taps    []TapRecord
	refused []string
	holds   []Key
	notes   []string
}

func (r *recorded) hooks() Hooks {
	return Hooks{
		Tap:    func(tap TapRecord) error { r.taps = append(r.taps, tap); return nil },
		Refuse: func(_ TapRecord, reason string) error { r.refused = append(r.refused, reason); return nil },
		Hold:   func(pr int, head string) error { r.holds = append(r.holds, Key{pr, head}); return nil },
		Note:   func(lane, text string) error { r.notes = append(r.notes, lane+": "+text); return nil },
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
	if err := s.PutReviewRows([]store.ReviewRow{{Repo: repo, Number: number, Head: on, ReadyForTap: true}}); err != nil {
		t.Fatal(err)
	}
}

func rowWaits(t *testing.T, s *store.Store, waits string) {
	t.Helper()
	if err := s.PutReviewRows([]store.ReviewRow{{Repo: repo, Number: number, Head: head, WaitsOn: waits}}); err != nil {
		t.Fatal(err)
	}
}

func put(t *testing.T, s *store.Store, on, kind, value string) {
	t.Helper()
	if _, err := s.PutFact(repo, number, on, kind, value, t0); err != nil {
		t.Fatal(err)
	}
}

type Key struct {
	PR   int
	Head string
}

func rows(t *testing.T, s *store.Store, snapshot map[int]machine.Facts) []Row {
	t.Helper()
	items, err := s.OpenItems(repo, 0)
	if err != nil {
		t.Fatal(err)
	}
	var found []Row
	for _, item := range items {
		f, open := snapshot[item.Number]
		if item.Mover != machine.MoverOwner || !open || !f.Ours() || f.Head != item.Head || f.Tapped() || f.Hold != "" {
			continue
		}
		words, err := s.Facts(repo, item.Number, item.Head)
		if err != nil {
			t.Fatal(err)
		}
		if words[FactWithdrawn] == "" {
			found = append(found, Row{item.Number, item.Head, item.Kind, f.Lane, gates.Missing(f), words})
		}
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

func TestApprovedOnARowNotReadyIsRefusedWithItsReasonAndTapsNothing(t *testing.T) {
	s, hooks := withItem(t), &recorded{}
	rowWaits(t, s, "held by root")
	if err := Answer(s, repo, hooks.hooks(), rows(t, s, facts(nil))[0], verdictApproved, t0); err != nil {
		t.Fatal(err)
	}
	if len(hooks.taps) != 0 || stored(t, s)["tap"] != "" {
		t.Fatalf("taps = %+v, facts = %v", hooks.taps, stored(t, s))
	}
	if want := []string{"held by root"}; !reflect.DeepEqual(hooks.refused, want) || stored(t, s)[bus.TapRefusedFact] != "held by root" {
		t.Fatalf("refused = %+v, facts = %v", hooks.refused, stored(t, s))
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

func TestACarriedTapAsksNothing(t *testing.T) {
	s := withItem(t)
	put(t, s, head, "tap", "tapped")
	openTap(t, s, moved, t0.Add(time.Hour))
	snapshot := facts(func(f *machine.Facts) {
		f.Head, f.Tap, f.TapFrom, f.CarriedBy = moved, machine.TapTapped, head, gates.BySameDiff
	})
	if found := rows(t, s, snapshot); len(found) != 0 {
		t.Fatalf("rows = %+v", found)
	}
}

func TestAStaleItemForAnOlderHeadRendersNothing(t *testing.T) {
	if found := rows(t, withItem(t), facts(func(f *machine.Facts) { f.Head = moved })); len(found) != 0 {
		t.Fatalf("rows = %+v", found)
	}
}

func TestAnOwnerItemOnAnotherPersonsPRALaneCarriesDrawsNoRow(t *testing.T) {
	if found := rows(t, withItem(t), facts(func(f *machine.Facts) { f.ForeignAuthor = "teammate" })); len(found) != 0 {
		t.Fatalf("rows = %+v", found)
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
