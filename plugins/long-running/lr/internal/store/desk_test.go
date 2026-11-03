package store

import (
	"reflect"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
)

const stack = "lane/a"

func waiting(change string) PRState {
	return PRState{
		Repo: repo, Number: 7, Branch: "lane/a", Base: "main", Head: head, Change: change, Stack: stack,
		State: machine.StateAwaitingTap, Reason: "clean, green and approved, with no tap on this change", Mover: machine.MoverOwner,
		Deadline: t0.Add(machine.OwnerClock),
	}
}

func TestAPRWaitsOnTheOwnerOnlyWithAnOpenAskForItsChange(t *testing.T) {
	heldByOwner, heldByLane := waiting("c1"), waiting("c1")
	heldByOwner.State, heldByLane.State = "held", "held"
	heldByLane.Mover = machine.MoverLane
	for _, tc := range []struct {
		name    string
		asks    []Ask
		closed  bool
		pr      PRState
		refused bool
	}{
		{name: "awaiting the tap with no ask", pr: waiting("c1"), refused: true},
		{name: "held for the owner with no ask", pr: heldByOwner, refused: true},
		{name: "held for a lane with no ask", pr: heldByLane},
		{name: "an ask for another change", asks: []Ask{{repo, 7, "c0", head, t0}}, pr: waiting("c1"), refused: true},
		{name: "an ask for another PR", asks: []Ask{{repo, 8, "c1", head, t0}}, pr: waiting("c1"), refused: true},
		{name: "its ask already closed", asks: []Ask{{repo, 7, "c1", head, t0}}, closed: true, pr: waiting("c1"), refused: true},
		{name: "its ask opened in the same step", asks: []Ask{{repo, 7, "c1", head, t0}}, pr: waiting("c1")},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s := memory(t)
			err := s.Update(func(tx *Tx) error {
				for _, ask := range tc.asks {
					if err := tx.OpenAsk(ask); err != nil {
						return err
					}
					if tc.closed {
						if err := tx.CloseAsk(ask.Repo, ask.Number, ask.Change, t0); err != nil {
							return err
						}
					}
				}
				if err := tx.Advance("bus", 41); err != nil {
					return err
				}
				return tx.PutState(tc.pr)
			})
			if (err != nil) != tc.refused {
				t.Fatalf("refused = %v, want %v", err, tc.refused)
			}
			var stored PRState
			var found bool
			if err := s.Update(func(tx *Tx) (err error) {
				stored, found, err = tx.State(repo, 7)
				return err
			}); err != nil {
				t.Fatal(err)
			}
			cursor := must[int64](t)(s.Cursor("bus"))
			if tc.refused && (found || cursor != 0) {
				t.Fatalf("a refused step left its state %v or its cursor %d behind", found, cursor)
			}
			if !tc.refused && (!reflect.DeepEqual(stored, tc.pr) || cursor != 41) {
				t.Fatalf("stored %+v at cursor %d, want %+v at 41", stored, cursor, tc.pr)
			}
		})
	}
}

func TestANewChangeWaitsOnTheOwnerOnlyOnceItsOwnAskIsOpen(t *testing.T) {
	s := memory(t)
	if err := s.Update(func(tx *Tx) error {
		if err := tx.OpenAsk(Ask{repo, 7, "c1", head, t0}); err != nil {
			return err
		}
		return tx.PutState(waiting("c1"))
	}); err != nil {
		t.Fatal(err)
	}
	if err := s.Update(func(tx *Tx) error { return tx.PutState(waiting("c2")) }); err == nil {
		t.Fatal("a changed diff kept waiting on the owner under the old change's ask")
	}
	if err := s.Update(func(tx *Tx) error {
		if err := tx.OpenAsk(Ask{repo, 7, "c2", head, t0}); err != nil {
			return err
		}
		if err := tx.PutState(waiting("c2")); err != nil {
			return err
		}
		return tx.CloseAsk(repo, 7, "c1", t0)
	}); err != nil {
		t.Fatalf("the new change's card did not replace the old one: %v", err)
	}
}

func TestTheNewestGradeAttemptHoldsTheOneRow(t *testing.T) {
	s := memory(t)
	for _, g := range []Grade{
		{repo, 7, head, "", "finder", 1, "ask", "#100", t0},
		{repo, 7, head, "", "finder", 2, "clear", "#101", t0.Add(time.Minute)},
		{repo, 7, head, "", "finder", 1, "ask", "#102", t0.Add(2 * time.Minute)},
		{repo, 7, head, "", "finder", 2, "clear", "#103", t0.Add(3 * time.Minute)},
		{repo, 7, head, "", "fidelity", 1, "pass", "#104", t0.Add(4 * time.Minute)},
	} {
		if err := s.Update(func(tx *Tx) error { return tx.PutGrade(g) }); err != nil {
			t.Fatal(err)
		}
	}
	var grades map[string]Grade
	if err := s.Update(func(tx *Tx) (err error) {
		grades, err = tx.Grades(repo, 7, head)
		return err
	}); err != nil {
		t.Fatal(err)
	}
	want := map[string]Grade{
		"finder":   {repo, 7, head, "", "finder", 2, "clear", "#103", t0.Add(3 * time.Minute)},
		"fidelity": {repo, 7, head, "", "fidelity", 1, "pass", "#104", t0.Add(4 * time.Minute)},
	}
	if !reflect.DeepEqual(grades, want) {
		t.Fatalf("grades = %+v, want the late attempt-1 answer dropped and the repeat of attempt 2 kept once", grades)
	}
}

func TestAnOwedActionLapsesWhenItsStackMovesAndARepeatIsHarmless(t *testing.T) {
	s := memory(t)
	enqueue := Owed{Key: "enqueue:7:" + head, Repo: repo, Stack: stack, Action: "enqueue", Number: 7, Head: head, Created: t0}
	step := func(raise bool, owe ...Owed) error {
		return s.Update(func(tx *Tx) error {
			if raise {
				if err := tx.Raise(repo, stack); err != nil {
					return err
				}
			}
			for _, o := range owe {
				if err := tx.Owe(o); err != nil {
					return err
				}
			}
			return nil
		})
	}
	owed := func() []Owed { return must[[]Owed](t)(s.Owed()) }
	if err := step(false, enqueue); err == nil {
		t.Fatal("an action was owed on a stack with no revision")
	}
	if err := step(true, enqueue, enqueue); err != nil {
		t.Fatal(err)
	}
	if got := owed(); !reflect.DeepEqual(got, []Owed{enqueue}) {
		t.Fatalf("owed = %+v, want the enqueue once", got)
	}
	if err := step(true); err != nil {
		t.Fatal(err)
	}
	if got := owed(); len(got) != 0 {
		t.Fatalf("an action owed before its stack moved is still owed: %+v", got)
	}
	if err := step(true, enqueue); err != nil {
		t.Fatal(err)
	}
	if got := owed(); !reflect.DeepEqual(got, []Owed{enqueue}) {
		t.Fatalf("owed = %+v, want the enqueue owed again at the new revision", got)
	}
}

func TestTheTimerWakesForTheEarliestDueRow(t *testing.T) {
	s := memory(t)
	set := func(d Due) {
		t.Helper()
		if err := s.Update(func(tx *Tx) error { return tx.SetDue(d) }); err != nil {
			t.Fatal(err)
		}
	}
	next := func() (Due, bool) {
		t.Helper()
		d, found, err := s.NextDue()
		if err != nil {
			t.Fatal(err)
		}
		return d, found
	}
	hold := Due{"hold:7", repo, 7, "hold", t0.Add(10 * time.Minute)}
	mergeable := Due{"mergeable:8", repo, 8, "mergeable", t0.Add(5 * time.Minute)}
	set(hold)
	set(mergeable)
	if d, _ := next(); d != mergeable {
		t.Fatalf("next = %+v, want %+v", d, mergeable)
	}
	mergeable.At = t0.Add(20 * time.Minute)
	set(mergeable)
	if d, _ := next(); d != hold {
		t.Fatalf("a moved row still wakes the timer at its old time: %+v", d)
	}
	for _, key := range []string{hold.Key, mergeable.Key} {
		if err := s.Update(func(tx *Tx) error { return tx.ClearDue(key) }); err != nil {
			t.Fatal(err)
		}
	}
	if d, found := next(); found {
		t.Fatalf("a cleared row still wakes the timer: %+v", d)
	}
}

func TestACarryIsOneRowPerHeadAndAFidelityCarryNamesItsGrade(t *testing.T) {
	s := memory(t)
	carried := Carry{repo, 7, 7, "a1b2c3d", head, ProofSameDiff, "", t0}
	for range 2 {
		if err := s.Update(func(tx *Tx) error { return tx.Carry(carried) }); err != nil {
			t.Fatal(err)
		}
	}
	var rows int
	if err := s.db.QueryRow("SELECT count(*) FROM carries").Scan(&rows); err != nil || rows != 1 {
		t.Fatalf("a replayed carry left %d rows: %v", rows, err)
	}
	unproved := Carry{repo, 8, 8, "a1b2c3d", head, ProofFidelity, "", t0}
	if err := s.Update(func(tx *Tx) error { return tx.Carry(unproved) }); err == nil {
		t.Fatal("a fidelity carry with no grade was stored")
	}
}
