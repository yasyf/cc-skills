package board

import (
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
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

func readyRow(change func(*store.ReviewRow)) store.ReviewRow {
	row := store.ReviewRow{
		Repo: repo, Number: number, Head: head, ReadyForTap: true, State: "open", Title: "sand: faster chats", ForUsers: "chats load faster",
		PlanRow: "checked against the plan", Risk: "one cache key", Description: "## Context", WaitsOn: "Only your approval.", CI: "green",
		Review: "approved by poetic-svc", Grade: "pass", GradeFindings: "none", Mergeable: "clean", Lane: "lane-7", Branch: "lane-7/fast", Parent: "none",
	}
	if change != nil {
		change(&row)
	}
	return row
}

func withLedger(t *testing.T, rows ...store.ReviewRow) *store.Store {
	t.Helper()
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { s.Close() })
	if err := s.PutReviewRows(rows); err != nil {
		t.Fatal(err)
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
	if err := Answer(s, repo, hooks.hooks(), readyRow(nil), verdictApproved, t0); err != nil {
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
	if found, err := waitingRows(s, repo, []int{number}); err != nil || len(found) != 0 {
		t.Fatalf("a tapped head still waits on him: %+v, %v", found, err)
	}
}

func TestApprovedOnARowNotReadyIsRefusedWithItsReasonAndTapsNothing(t *testing.T) {
	s, hooks := withItem(t), &recorded{}
	rowWaits(t, s, "held by root")
	if err := Answer(s, repo, hooks.hooks(), readyRow(func(r *store.ReviewRow) { r.ReadyForTap, r.WaitsOn = false, "held by root" }), verdictApproved, t0); err == nil || !strings.Contains(err.Error(), "held by root") {
		t.Fatalf("err = %v, want the refusal for the card to tell him", err)
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
	if err := Answer(s, repo, hooks.hooks(), readyRow(nil), verdictRejected, t0); err != nil {
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
	err := Answer(s, repo, hooks.hooks(), readyRow(nil), "Ship it", t0)
	if err == nil || err.Error() != `not a board answer: "Ship it"` {
		t.Fatalf("err = %v", err)
	}
}
