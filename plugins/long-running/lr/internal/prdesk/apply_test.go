package prdesk

import (
	"fmt"
	"path/filepath"
	"slices"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	repo    = "org/repo"
	landing = "main"
	h1      = "1111111111111111111111111111111111111111"
	h2      = "2222222222222222222222222222222222222222"
)

var t0 = time.Date(2026, 11, 3, 18, 0, 0, 0, time.UTC)

func open(t *testing.T, s *store.Store) Desk {
	t.Helper()
	t.Cleanup(func() { s.Close() })
	return Desk{s, map[string]Repo{repo: {Name: repo, Landing: landing, Required: []string{"ci"}}}}
}

func memory(t *testing.T) Desk {
	t.Helper()
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	return open(t, s)
}

type clock struct{ seq int64 }

func (c *clock) at(minute int) Meta {
	c.seq++
	return Meta{Source: "bus", Seq: c.seq, Key: fmt.Sprintf("#%d", c.seq), At: t0.Add(time.Duration(minute) * time.Minute), Repo: repo}
}

func clean(c *clock, minute, number int, branch, base, head string) []Event {
	return []Event{
		PullRequest{c.at(minute), number, branch, base, head, true, false, ""},
		Check{c.at(minute), head, "ci", "success", c.seq},
		Grade{c.at(minute), number, head, "finder", 1, string(machine.FinderClear)},
		Grade{c.at(minute), number, head, "fidelity", 1, string(machine.FidelityPass)},
		Review{c.at(minute), number, "bot", "approved", head},
	}
}

func apply(t *testing.T, d Desk, events ...Event) {
	t.Helper()
	for _, e := range events {
		if _, err := d.Apply(e); err != nil {
			t.Fatal(err)
		}
	}
}

func state(t *testing.T, d Desk, number int) store.PRState {
	t.Helper()
	var row store.PRState
	if err := d.Store.Update(func(tx *store.Tx) (err error) {
		row, _, err = tx.State(repo, number)
		return err
	}); err != nil {
		t.Fatal(err)
	}
	return row
}

func owed(t *testing.T, d Desk) []string {
	t.Helper()
	rows, err := d.Store.Owed()
	if err != nil {
		t.Fatal(err)
	}
	actions := make([]string, 0, len(rows))
	for _, o := range rows {
		actions = append(actions, fmt.Sprintf("%s #%d", o.Action, o.Number))
	}
	slices.Sort(actions)
	return actions
}

func expect(t *testing.T, d Desk, number int, want machine.State) {
	t.Helper()
	if got := state(t, d, number); got.State != want {
		t.Fatalf("#%d is %s (%s), want %s", number, got.State, got.Reason, want)
	}
}

func TestAPRWaitingOnTheOwnerIsReadiedByATapOnItsHead(t *testing.T) {
	d, c := memory(t), &clock{}
	apply(t, d, clean(c, 0, 1, "lane/a", landing, h1)...)
	expect(t, d, 1, machine.StateAwaitingTap)
	apply(t, d, Tap{c.at(1), 1, h1, machine.TapTapped})
	expect(t, d, 1, machine.StateReady)
	if got := owed(t, d); !slices.Equal(got, []string{"enqueue #1"}) {
		t.Fatalf("owed = %v", got)
	}
	apply(t, d, PullRequest{c.at(2), 1, "lane/a", landing, h2, true, false, ""})
	expect(t, d, 1, machine.StateGrading)
	if got := owed(t, d); !slices.Equal(got, []string{"grade-fidelity #1", "grade-finder #1"}) {
		t.Fatalf("a new head kept its old enqueue or got no grade jobs: %v", got)
	}
}

func TestAHoldStopsAnOwedEnqueueAndDequeuesAQueuedPR(t *testing.T) {
	d, c := memory(t), &clock{}
	apply(t, d, append(clean(c, 0, 1, "lane/a", landing, h1), Tap{c.at(1), 1, h1, machine.TapTapped})...)
	apply(t, d, Hold{c.at(2), 1, machine.MoverRoot, "readers first", "coordinator"})
	expect(t, d, 1, machine.StateHeld)
	if got := owed(t, d); len(got) != 0 {
		t.Fatalf("a hold at the same head left the enqueue owed: %v", got)
	}
	apply(t, d, Lift{c.at(3), "#7"}, Queue{c.at(4), 1, "", h1, 1, QueueIn, ""})
	expect(t, d, 1, machine.StateQueued)
	apply(t, d, Hold{c.at(5), 1, machine.MoverRoot, "a second look", "coordinator"})
	if got := owed(t, d); !slices.Equal(got, []string{"dequeue #1"}) {
		t.Fatalf("a hold on a queued PR owes %v, want its dequeue", got)
	}
}

func TestAHoldUntilAPRLandsLiftsWhenItsSquashReachesTheLandingBranch(t *testing.T) {
	d, c := memory(t), &clock{}
	apply(t, d, clean(c, 0, 1, "lane/a", landing, h1)...)
	apply(t, d, append(clean(c, 0, 2, "lane/b", landing, h2), Tap{c.at(1), 2, h2, machine.TapTapped}, Hold{c.at(1), 2, machine.MoverRoot, "lands after #1", "pr-landed:#1"})...)
	expect(t, d, 2, machine.StateHeld)
	apply(t, d, PullRequest{c.at(2), 1, "lane/a", landing, h1, false, true, ""}, Push{c.at(3), []int{1}})
	expect(t, d, 1, machine.StateLanded)
	expect(t, d, 2, machine.StateReady)
	apply(t, d, Hold{c.at(4), 2, machine.MoverRoot, "lands after #1", "pr-landed:#1"})
	expect(t, d, 2, machine.StateReady)
}

func TestATimeHoldLiftsAtOnceWhenPastAndOnItsTimerWhenNot(t *testing.T) {
	d, c := memory(t), &clock{}
	apply(t, d, append(clean(c, 0, 1, "lane/a", landing, h1), Tap{c.at(1), 1, h1, machine.TapTapped})...)
	apply(t, d, Hold{c.at(10), 1, machine.MoverRoot, "after the window", "time:" + t0.Add(5*time.Minute).Format(time.RFC3339)})
	expect(t, d, 1, machine.StateReady)
	apply(t, d, Hold{c.at(11), 1, machine.MoverRoot, "after the window", "time:" + t0.Add(30*time.Minute).Format(time.RFC3339)})
	expect(t, d, 1, machine.StateHeld)
	due, found, err := d.Store.NextDue()
	if err != nil || !found || !due.At.Equal(t0.Add(30*time.Minute)) {
		t.Fatalf("next due = %+v, %v, %v", due, found, err)
	}
	apply(t, d, Timer{c.at(30), due})
	expect(t, d, 1, machine.StateReady)
}

func TestAnOldGreenAfterARedRerunLeavesTheBlocker(t *testing.T) {
	d, c := memory(t), &clock{}
	apply(t, d, append(clean(c, 0, 1, "lane/a", landing, h1), Tap{c.at(1), 1, h1, machine.TapTapped})...)
	apply(t, d, Check{c.at(5), h1, "ci", "failure", 50}, Check{c.at(2), h1, "ci", "success", 20})
	expect(t, d, 1, machine.StateNeedsFix)
	apply(t, d, Check{c.at(6), h1, "ci", "success", 60})
	expect(t, d, 1, machine.StateReady)
}

func TestATrunkRecordFromAnEarlierAttemptLeavesTheCurrentOne(t *testing.T) {
	d, c := memory(t), &clock{}
	apply(t, d, append(clean(c, 0, 1, "lane/a", landing, h1), Tap{c.at(1), 1, h1, machine.TapTapped})...)
	apply(t, d, Queue{c.at(2), 1, "", h1, 2, QueueOut, machine.EvictionBatchRed}, Queue{c.at(3), 1, "", h1, 1, QueueIn, ""})
	expect(t, d, 1, machine.StateEvicted)
	if got := owed(t, d); !slices.Equal(got, []string{"reenter #1"}) {
		t.Fatalf("owed = %v", got)
	}
}

func TestAnEventOnTheBottomOfAStackPlacesThePRsAboveIt(t *testing.T) {
	d, c := memory(t), &clock{}
	apply(t, d, clean(c, 0, 1, "lane/a", landing, h1)...)
	apply(t, d, append(clean(c, 0, 2, "lane/b", "lane/a", h2), Tap{c.at(1), 2, h2, machine.TapTapped})...)
	expect(t, d, 1, machine.StateAwaitingTap)
	expect(t, d, 2, machine.StateBelow)
	apply(t, d, Tap{c.at(2), 1, h1, machine.TapTapped})
	expect(t, d, 1, machine.StateReady)
	expect(t, d, 2, machine.StateReady)
}

func TestShuffledEventsReachTheSameStates(t *testing.T) {
	c := &clock{}
	events := slices.Concat(clean(c, 0, 1, "lane/a", landing, h1), clean(c, 1, 2, "lane/b", "lane/a", h2), []Event{
		Tap{c.at(2), 2, h2, machine.TapTapped},
		Check{c.at(3), h1, "ci", "failure", 99},
		PullRequest{c.at(4), 1, "lane/a", landing, h2, true, false, ""},
		Hold{c.at(5), 2, machine.MoverRoot, "readers first", "coordinator"},
	})
	inOrder, shuffled := memory(t), memory(t)
	apply(t, inOrder, events...)
	reversed := slices.Clone(events)
	slices.Reverse(reversed)
	apply(t, shuffled, reversed...)
	for _, number := range []int{1, 2} {
		want, got := state(t, inOrder, number), state(t, shuffled, number)
		if want.State != got.State || want.Mover != got.Mover || want.Reason != got.Reason || want.Head != got.Head {
			t.Fatalf("#%d in order is %+v, shuffled is %+v", number, want, got)
		}
	}
}

func TestAnEnqueueOwedBeforeACrashIsOwedOnceAfterTheRestart(t *testing.T) {
	path := filepath.Join(t.TempDir(), "desk.sqlite")
	s, err := store.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	c := &clock{}
	tap := Tap{c.at(1), 1, h1, machine.TapTapped}
	apply(t, Desk{s, map[string]Repo{repo: {Name: repo, Landing: landing, Required: []string{"ci"}}}}, append(clean(c, 0, 1, "lane/a", landing, h1), tap)...)
	s.Close()
	reopened, err := store.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	d := open(t, reopened)
	apply(t, d, tap)
	if got := owed(t, d); !slices.Equal(got, []string{"enqueue #1"}) {
		t.Fatalf("after the restart owed = %v, want the one enqueue", got)
	}
}
