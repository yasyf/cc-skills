package board

import (
	"reflect"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/gates"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	repo   = "org/repo"
	number = 45583
	head   = "e718a7434fa01ee9835f992a9a647a825350732c"
	moved  = "aa11bb22cc33dd44ee55ff6677889900aabbccdd"
)

var t0 = time.Date(2026, 10, 30, 21, 0, 0, 0, time.UTC)

type recorded struct {
	taps  []TapRecord
	holds []Key
}

func (r *recorded) hooks() Hooks {
	return Hooks{
		Tap:  func(tap TapRecord) error { r.taps = append(r.taps, tap); return nil },
		Hold: func(pr int, head string) error { r.holds = append(r.holds, Key{pr, head}); return nil },
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

func TestAnOwnerItemIsOneRowKeyedByPRAndHead(t *testing.T) {
	found := rows(t, withItem(t), facts(nil))
	want := []Row{{number, head, "tap", []string{"no tap"}, ""}}
	if !reflect.DeepEqual(found, want) {
		t.Fatalf("rows = %+v", found)
	}
	block := Block{"tap-45583-" + head[:12], "choice", "#45583 at " + head[:12] + ": tap", "every other gate is met", []string{"Merge", "Hold", "Explain more"}}
	if blocks := Render(found, nil); !reflect.DeepEqual(blocks, []Block{block}) {
		t.Fatalf("blocks = %+v", blocks)
	}
}

func TestAnAnsweredKeyNeverRendersAgain(t *testing.T) {
	found := rows(t, withItem(t), facts(nil))
	if blocks := Render(found, map[Key]bool{{number, head}: true}); len(blocks) != 0 {
		t.Fatalf("blocks = %+v", blocks)
	}
}

func TestMergeBecomesATapFactAndATapRecord(t *testing.T) {
	s, hooks := withItem(t), &recorded{}
	if err := Answer(s, repo, hooks.hooks(), rows(t, s, facts(nil))[0], Merge, t0); err != nil {
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

func TestHoldGoesToTheHoldHookAndTapsNothing(t *testing.T) {
	s, hooks := withItem(t), &recorded{}
	if err := Answer(s, repo, hooks.hooks(), rows(t, s, facts(nil))[0], Hold, t0); err != nil {
		t.Fatal(err)
	}
	if want := []Key{{number, head}}; !reflect.DeepEqual(hooks.holds, want) {
		t.Fatalf("holds = %+v", hooks.holds)
	}
	if len(hooks.taps) != 0 || len(stored(t, s)) != 0 {
		t.Fatalf("taps = %+v, facts = %v", hooks.taps, stored(t, s))
	}
}

func TestExplainMoreChangesNothing(t *testing.T) {
	s, hooks := withItem(t), &recorded{}
	if err := Answer(s, repo, hooks.hooks(), rows(t, s, facts(nil))[0], Explain, t0); err != nil {
		t.Fatal(err)
	}
	if len(hooks.taps) != 0 || len(hooks.holds) != 0 || len(stored(t, s)) != 0 {
		t.Fatalf("taps = %+v, holds = %+v, facts = %v", hooks.taps, hooks.holds, stored(t, s))
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
	block := Render([]Row{{number, moved, "tap", []string{"no tap"}, head}}, nil)[0]
	if want := "every other gate is met; rebased from " + head[:12] + ", same patch"; block.Body != want {
		t.Fatalf("body = %q", block.Body)
	}
}

func TestARowNamesEveryOtherGateTheHeadStillFails(t *testing.T) {
	found := rows(t, withItem(t), facts(func(f *machine.Facts) { f.CI = machine.CIPending }))
	if body := Render(found, nil)[0].Body; body != "CI is not green" {
		t.Fatalf("body = %q", body)
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
	if body := Render(found, nil)[0].Body; body != "tap given only if the finding is superseded" {
		t.Fatalf("body = %q", body)
	}
}
