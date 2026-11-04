package install

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

var t0 = time.Date(2026, 10, 30, 21, 0, 0, 0, time.UTC)

type posted struct {
	to   []string
	body bus.Body
}

type world struct {
	t       *testing.T
	store   *store.Store
	faults  []string
	now     time.Time
	swapped []string
	posts   []posted
	takes   bool
}

func newWorld(t *testing.T) *world {
	t.Helper()
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	if _, err := s.AcquireLease("d", "0.8.0", 101, t0); err != nil {
		t.Fatal(err)
	}
	return &world{t: t, store: s, now: t0, takes: true}
}

func (w *world) steps() Steps {
	return Steps{
		Preflight: func(context.Context) []string { return w.faults },
		Swap: func(_ context.Context, version string) error {
			w.swapped = append(w.swapped, version)
			return nil
		},
		Post: func(_ context.Context, to []string, body bus.Body) error {
			w.posts = append(w.posts, posted{to, body})
			return nil
		},
		Now: func() time.Time { return w.now },
		Sleep: func(d time.Duration) {
			w.now = w.now.Add(d)
			if w.takes && len(w.swapped) > 0 {
				if _, err := w.store.AcquireLease("d", w.swapped[len(w.swapped)-1], 4242, w.now); err != nil {
					w.t.Fatal(err)
				}
			}
		},
	}
}

func TestAFailedPreflightRefusesBeforeAnythingIsSwapped(t *testing.T) {
	w := newWorld(t)
	w.faults = []string{"GitHub token missing", "sprite credentials expired"}

	_, err := Run(context.Background(), w.store, "d", "0.8.1", w.steps())

	var refused *Refused
	if !errors.As(err, &refused) || err.Error() != "install 0.8.1 refused before the swap: GitHub token missing; sprite credentials expired" {
		t.Fatalf("got %v", err)
	}
	if len(w.swapped)+len(w.posts) != 0 {
		t.Fatalf("a refusal acted: %v %v", w.swapped, w.posts)
	}
	lease, err := w.store.Lease("d")
	if err != nil || lease.Generation != 1 {
		t.Fatalf("lease moved: %+v %v", lease, err)
	}
}

func TestInstallAnnouncesBeforeAndAfterAndReturnsTheNewGeneration(t *testing.T) {
	w := newWorld(t)

	generation, err := Run(context.Background(), w.store, "d", "0.8.1", w.steps())
	if err != nil {
		t.Fatal(err)
	}

	if generation != 2 {
		t.Fatalf("generation %d", generation)
	}
	if !reflect.DeepEqual(w.swapped, []string{"0.8.1"}) {
		t.Fatalf("swapped %v", w.swapped)
	}
	want := []posted{
		{[]string{"supervisor"}, bus.State{Lane: "install", Text: "installing 0.8.1"}},
		{[]string{"supervisor"}, bus.State{Lane: "install", Text: "serving 0.8.1 on generation 2"}},
	}
	if !reflect.DeepEqual(w.posts, want) {
		t.Fatalf("posts %+v", w.posts)
	}
}

func TestASuccessorThatNeverTakesTheLeaseEscalatesWithinThreeMinutes(t *testing.T) {
	w := newWorld(t)
	w.takes = false

	_, err := Run(context.Background(), w.store, "d", "0.8.1", w.steps())

	if err == nil || !strings.Contains(err.Error(), "did not take the lease within 15 minutes; generation 1 still serves") {
		t.Fatalf("got %v", err)
	}
	if !w.now.Equal(t0.Add(Handoff)) {
		t.Fatalf("waited until %v", w.now)
	}
	last := w.posts[len(w.posts)-1]
	if !reflect.DeepEqual(last, posted{[]string{"root"}, bus.Escalation{Subject: "install:0.8.1", Cause: "no successor within 15 minutes"}}) {
		t.Fatalf("last post %+v", last)
	}
	rows, err := w.store.Escalations("install:0.8.1")
	if err != nil || len(rows) != 1 || rows[0].Cause != "no successor within 15 minutes" {
		t.Fatalf("escalations %+v %v", rows, err)
	}
}

func TestAVersionAlreadyServingNeedsNoNewGeneration(t *testing.T) {
	w := newWorld(t)
	w.takes = false

	generation, err := Run(context.Background(), w.store, "d", "0.8.0", w.steps())

	if err != nil || generation != 1 {
		t.Fatalf("got %d %v", generation, err)
	}
}

func TestAnotherInstallThatGotPastThisVersionEndsTheWait(t *testing.T) {
	w := newWorld(t)
	w.takes = false
	if _, err := w.store.AcquireLease("d", "0.8.10", 4242, t0); err != nil {
		t.Fatal(err)
	}

	generation, err := Run(context.Background(), w.store, "d", "0.8.9", w.steps())

	if err != nil || generation != 2 {
		t.Fatalf("got %d %v", generation, err)
	}
	last := w.posts[len(w.posts)-1]
	if !reflect.DeepEqual(last, posted{[]string{"supervisor"}, bus.State{Lane: "install", Text: "serving 0.8.10 on generation 2"}}) {
		t.Fatalf("last post %+v", last)
	}
}
