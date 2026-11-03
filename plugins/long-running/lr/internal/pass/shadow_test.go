package pass_test

import (
	"reflect"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func shadow(t *testing.T, s *store.Store, r *recorder, now time.Time, snapshot ...machine.Facts) []pass.Diff {
	t.Helper()
	diffs, err := pass.Shadow(s, snapshot, now, r.post)
	if err != nil {
		t.Fatal(err)
	}
	return diffs
}

func TestAHeadReadyForAWholeRunnerPassAndStillUnqueuedIsNamedOnce(t *testing.T) {
	s, r := newStore(t), &recorder{}

	first := shadow(t, s, r, t0, facts(1, nil))
	early := shadow(t, s, r, t0.Add(4*time.Minute), facts(1, nil))
	due := shadow(t, s, r, t0.Add(5*time.Minute), facts(1, nil))
	later := shadow(t, s, r, t0.Add(10*time.Minute), facts(1, nil))

	want := []pass.Diff{{Subject: "#1 at " + head[:12], Cause: "v2 would have queued it at 21:00Z; the runner has not"}}
	if len(first) != 0 || len(early) != 0 || !reflect.DeepEqual(due, want) || len(later) != 0 {
		t.Fatalf("first = %+v, early = %+v, due = %+v, later = %+v", first, early, due, later)
	}
}

func TestAHeadTheRunnerQueuedThatV2WouldHoldNamesTheGatesItFails(t *testing.T) {
	s, r := newStore(t), &recorder{}
	held := facts(2, func(f *machine.Facts) {
		f.Queue, f.QueuedHead, f.Tap, f.Approved = machine.QueueQueued, head, "", false
	})

	diffs := shadow(t, s, r, t0, held)

	if len(diffs) != 1 || diffs[0].Cause != "the runner queued it; v2 holds it: no approving review; no tap" {
		t.Fatalf("diffs = %+v", diffs)
	}
}

func TestAgreementIsSilent(t *testing.T) {
	s, r := newStore(t), &recorder{}
	snapshot := []machine.Facts{
		facts(3, func(f *machine.Facts) { f.Queue, f.QueuedHead = machine.QueueQueued, head }),
		facts(4, noTap),
		facts(5, func(f *machine.Facts) { f.Finder, f.Fidelity = "", "" }),
	}

	first := shadow(t, s, r, t0, snapshot...)
	later := shadow(t, s, r, t0.Add(time.Hour), snapshot...)

	if len(first) != 0 || len(later) != 0 || len(r.posts) != 0 {
		t.Fatalf("first = %+v, later = %+v, posts = %+v", first, later, r.posts)
	}
}

func TestTheShadowPostsOneNoteForEachNewDisagreementAndNothingElse(t *testing.T) {
	s, r := newStore(t), &recorder{}
	held := facts(2, func(f *machine.Facts) { f.Queue, f.QueuedHead, f.Tap = machine.QueueQueued, head, "" })

	shadow(t, s, r, t0, held)
	shadow(t, s, r, t0.Add(3*time.Minute), held)

	want := []posted{{[]string{"supervisor"}, bus.Note{
		Text: "shadow #2 at " + head[:12] + ": the runner queued it; v2 holds it: no tap",
	}}}
	if !reflect.DeepEqual(r.posts, want) {
		t.Fatalf("posts = %+v", r.posts)
	}
}
