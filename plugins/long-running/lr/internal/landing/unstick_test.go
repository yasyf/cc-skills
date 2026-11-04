package landing_test

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/landing"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell/shelltest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const movedHead = "MOVED branch-%d " + head + " " + moved + "\n"

type stuckRig struct {
	s      *store.Store
	runner *shelltest.Fake
	reads  [][]int
	moves  []int
	move   func(number int) (string, error)
	now    time.Time
}

func newStuckRig(t *testing.T, s *store.Store) *stuckRig {
	t.Helper()
	t.Setenv("CCI_HOME", t.TempDir())
	runner := &shelltest.Fake{}
	runner.Reply("{}", "gh", "api", "-X", "PATCH")
	return &stuckRig{s: s, runner: runner, now: t0, move: func(number int) (string, error) {
		return fmt.Sprintf(movedHead, number), nil
	}}
}

func (r *stuckRig) pass(t *testing.T, snapshot ...machine.Facts) {
	t.Helper()
	if err := r.unstick().Pass(context.Background(), snapshot); err != nil {
		t.Fatal(err)
	}
}

func (r *stuckRig) unstick() landing.Unstick {
	return landing.Unstick{
		Runner: r.runner,
		Read: func(_ context.Context, numbers []int) (map[int]string, error) {
			r.reads = append(r.reads, numbers)
			read := make(map[int]string, len(numbers))
			for _, number := range numbers {
				read[number] = "MERGEABLE CLEAN at " + head
			}
			return read, nil
		},
		Move: func(_ context.Context, number int) (string, error) {
			r.moves = append(r.moves, number)
			return r.move(number)
		},
		Store: r.s, Drive: "d", Trunk: "dev", Now: func() time.Time { return r.now }, Log: slog.New(slog.DiscardHandler),
	}
}

func (r *stuckRig) patches() []string {
	var patched []string
	for _, call := range r.runner.Calls() {
		patched = append(patched, strings.Join(call.Argv, " "))
	}
	return patched
}

func stuck(number int, changes ...func(*machine.Facts)) machine.Facts {
	return facts(number, func(f *machine.Facts) {
		f.Branch, f.Mergeable, f.Uncomputed, f.Tap = "branch-"+strconv.Itoa(number), machine.MergeableUnknown, true, ""
		for _, change := range changes {
			change(f)
		}
	})
}

func unknownSince(t *testing.T, s *store.Store, at time.Time, numbers ...int) {
	t.Helper()
	for _, number := range numbers {
		if _, err := s.PutFact(repo, number, head, prrow.FactUnknownSince, at.Format(time.RFC3339), at); err != nil {
			t.Fatal(err)
		}
	}
}

func threeBumpsDone(t *testing.T, s *store.Store, numbers ...int) {
	t.Helper()
	for _, number := range numbers {
		if _, err := s.PutFact(repo, number, head, prrow.FactBumps, "3", t0); err != nil {
			t.Fatal(err)
		}
	}
}

func tapped(f *machine.Facts)  { f.Tap = machine.TapTapped }
func held(f *machine.Facts)    { f.Hold = machine.MoverRoot }
func exempt(f *machine.Facts)  { f.Exempt = true }
func inQueue(f *machine.Facts) { f.Queue, f.QueuedHead = machine.QueueQueued, f.Head }
func draft(f *machine.Facts)   { f.Draft = true }
func foreign(f *machine.Facts) { f.Lane, f.ForeignAuthor = "", "a-teammate" }
func withdrawn(f *machine.Facts) {
	f.Tap = machine.TapWithdrawn
}

func TestAStuckRowIsNotPatchedBeforeTenMinutes(t *testing.T) {
	s := newStore(t)
	r := newStuckRig(t, s)
	unknownSince(t, s, t0, 1)
	r.now = t0.Add(10*time.Minute - time.Second)

	r.pass(t, stuck(1))

	if got := r.patches(); len(got) != 0 {
		t.Fatalf("patched before ten minutes: %v", got)
	}
}

func TestARowWithNoUnknownSinceIsNotPatched(t *testing.T) {
	r := newStuckRig(t, newStore(t))

	r.pass(t, stuck(1))

	if got := r.patches(); len(got) != 0 {
		t.Fatalf("patched a row nothing stamped: %v", got)
	}
}

func TestAStuckRowIsPatchedOntoItsOwnBaseAtTenMinutesThenReadOnce(t *testing.T) {
	s := newStore(t)
	r := newStuckRig(t, s)
	unknownSince(t, s, t0, 1)
	r.now = t0.Add(10 * time.Minute)

	r.pass(t, stuck(1, func(f *machine.Facts) { f.Base = "parent-branch" }, func(f *machine.Facts) { f.Tap = machine.TapTapped }))

	want := []string{"gh api -X PATCH repos/org/repo/pulls/1 -f base=parent-branch"}
	if got := r.patches(); !slices.Equal(got, want) {
		t.Fatalf("patches = %v, want %v", got, want)
	}
	if len(r.reads) != 1 || !slices.Equal(r.reads[0], []int{1}) {
		t.Fatalf("reads = %v, want one fresh read of #1", r.reads)
	}
	if got := fact(t, s, 1, landing.BumpReadFact); got != "MERGEABLE CLEAN at "+head {
		t.Fatalf("the read after the PATCH = %q", got)
	}
	if got := fact(t, s, 1, prrow.FactBumps); got != "1" {
		t.Fatalf("bumps = %q, want 1", got)
	}
}

func TestAPatchWaitsTenMinutesEvenAcrossARestart(t *testing.T) {
	path := filepath.Join(t.TempDir(), "lr.sqlite")
	first, err := store.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	r := newStuckRig(t, first)
	unknownSince(t, first, t0, 1)
	r.now = t0.Add(10 * time.Minute)
	r.pass(t, stuck(1))
	if err := first.Close(); err != nil {
		t.Fatal(err)
	}

	second, err := store.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = second.Close() })
	r.s = second
	r.now = t0.Add(19 * time.Minute)
	r.pass(t, stuck(1))
	if got := r.patches(); len(got) != 1 {
		t.Fatalf("%d PATCHes nine minutes apart across a restart, want 1", len(got))
	}

	r.now = t0.Add(20 * time.Minute)
	r.pass(t, stuck(1))
	if got := r.patches(); len(got) != 2 {
		t.Fatalf("%d PATCHes at twenty minutes, want 2", len(got))
	}
}

func TestTheThirdPatchIsFollowedByOneRebaseAndItsLaneIsTold(t *testing.T) {
	s := newStore(t)
	r := newStuckRig(t, s)
	unknownSince(t, s, t0, 1)
	for minute := 10; minute <= 30; minute += 10 {
		r.now = t0.Add(time.Duration(minute) * time.Minute)
		r.pass(t, stuck(1))
		if len(r.moves) != 0 {
			t.Fatalf("rebased at minute %d, with %d PATCHes done", minute, len(r.patches()))
		}
	}
	if got := r.patches(); len(got) != 3 {
		t.Fatalf("%d PATCHes before the rebase, want 3", len(got))
	}

	r.now = t0.Add(39 * time.Minute)
	r.pass(t, stuck(1))
	if len(r.moves) != 0 {
		t.Fatal("rebased inside ten minutes of the third PATCH")
	}
	r.now = t0.Add(40 * time.Minute)
	r.pass(t, stuck(1))
	r.now = t0.Add(50 * time.Minute)
	r.pass(t, stuck(1))

	if !slices.Equal(r.moves, []int{1}) {
		t.Fatalf("moves = %v, want one rebase of #1", r.moves)
	}
	if got := r.patches(); len(got) != 3 {
		t.Fatalf("%d PATCHes after the rebase, want still 3", len(got))
	}
	if got := fact(t, s, 1, landing.BumpMoveFact); got != "moved" {
		t.Fatalf("move fact = %q, want moved", got)
	}
	posted, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{"report"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(posted) != 1 || !slices.Equal(posted[0].To, []string{"lane-7"}) {
		t.Fatalf("records = %+v, want one to the owning lane", posted)
	}
	for _, words := range []string{"#1", "3 base PATCHes", "branch-1 e718a7434f..aa11bb22cc", "CI and grades run again"} {
		if !strings.Contains(posted[0].Text, words) {
			t.Fatalf("record %q does not say %q", posted[0].Text, words)
		}
	}
}

func TestARowThatMayNotMoveIsNeverMovedAndKeepsItsPatches(t *testing.T) {
	for name, change := range map[string]func(*machine.Facts){
		"tapped": tapped, "conditional": func(f *machine.Facts) { f.Tap = machine.TapConditional }, "withdrawn tap": withdrawn,
		"exempt": exempt, "held": held, "queued": inQueue, "draft": draft, "another author's": foreign,
	} {
		t.Run(name, func(t *testing.T) {
			s := newStore(t)
			r := newStuckRig(t, s)
			unknownSince(t, s, t0, 1)
			threeBumpsDone(t, s, 1)
			r.now = t0.Add(time.Hour)

			r.pass(t, stuck(1, change))

			if len(r.moves) != 0 {
				t.Fatalf("moved a row with %s", name)
			}
			if got := r.patches(); len(got) != 1 {
				t.Fatalf("%d PATCHes, want the head-keeping one to go on", len(got))
			}
		})
	}
}

func TestARowIsNotMovedWhenARowBelowItMayNotMove(t *testing.T) {
	for name, below := range map[string]func(*machine.Facts){"tapped": tapped, "held": held, "queued": inQueue} {
		t.Run(name, func(t *testing.T) {
			s := newStore(t)
			r := newStuckRig(t, s)
			unknownSince(t, s, t0, 2)
			threeBumpsDone(t, s, 2)
			r.now = t0.Add(time.Hour)
			parent := stuck(1, below, func(f *machine.Facts) { f.Uncomputed = false })

			r.pass(t, parent, stuck(2, func(f *machine.Facts) { f.Base = parent.Branch }))

			if len(r.moves) != 0 {
				t.Fatalf("moved #2 and with it a %s #1", name)
			}
		})
	}
}

func TestARowOnABranchNoOpenRowHoldsIsNotMoved(t *testing.T) {
	s := newStore(t)
	r := newStuckRig(t, s)
	unknownSince(t, s, t0, 1)
	threeBumpsDone(t, s, 1)
	r.now = t0.Add(time.Hour)

	r.pass(t, stuck(1, func(f *machine.Facts) { f.Base = "someone-elses-branch" }))

	if len(r.moves) != 0 {
		t.Fatal("moved a chain whose lower PRs no row vouches for")
	}
}

func TestAMoveOfAStackTellsEveryLaneInIt(t *testing.T) {
	s := newStore(t)
	r := newStuckRig(t, s)
	unknownSince(t, s, t0, 2)
	threeBumpsDone(t, s, 2)
	r.now = t0.Add(time.Hour)
	parent := stuck(1, func(f *machine.Facts) { f.Uncomputed, f.Lane = false, "lane-below" })

	r.pass(t, parent, stuck(2, func(f *machine.Facts) { f.Base = parent.Branch }))

	if !slices.Equal(r.moves, []int{2}) {
		t.Fatalf("moves = %v", r.moves)
	}
	posted, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{"report"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(posted) != 1 || !slices.Equal(posted[0].To, []string{"lane-7", "lane-below"}) {
		t.Fatalf("records = %+v, want both lanes", posted)
	}
}

func TestARowThatIsNotStuckOnGitHubIsNeverTouched(t *testing.T) {
	for name, change := range map[string]func(*machine.Facts){
		"conflict":      func(f *machine.Facts) { f.Mergeable, f.Uncomputed = machine.MergeableConflict, false },
		"behind":        func(f *machine.Facts) { f.Mergeable, f.Uncomputed = machine.MergeableBehind, false },
		"clean":         func(f *machine.Facts) { f.Mergeable, f.Uncomputed = machine.MergeableClean, false },
		"queue unknown": func(f *machine.Facts) { f.Uncomputed = false },
		"closed":        func(f *machine.Facts) { f.Closed = true },
	} {
		t.Run(name, func(t *testing.T) {
			s := newStore(t)
			r := newStuckRig(t, s)
			unknownSince(t, s, t0, 1)
			threeBumpsDone(t, s, 1)
			r.now = t0.Add(time.Hour)

			r.pass(t, stuck(1, change))

			if got := r.patches(); len(got) != 0 || len(r.moves) != 0 || len(r.reads) != 0 {
				t.Fatalf("patches %v, moves %v, reads %v, want nothing", got, r.moves, r.reads)
			}
		})
	}
}

func TestARebaseThatConflictsIsTriedOnceAndGoesToTheLane(t *testing.T) {
	s := newStore(t)
	r := newStuckRig(t, s)
	r.move = func(int) (string, error) {
		return "CONFLICT replaying branch-1 onto dev at abc123 the subject\n  a.go\n", fmt.Errorf("pushed nothing: %w", landing.ErrConflict)
	}
	unknownSince(t, s, t0, 1)
	threeBumpsDone(t, s, 1)
	r.now = t0.Add(time.Hour)

	r.pass(t, stuck(1))
	r.now = t0.Add(2 * time.Hour)
	r.pass(t, stuck(1))

	if !slices.Equal(r.moves, []int{1}) {
		t.Fatalf("moves = %v, want one try", r.moves)
	}
	if got := fact(t, s, 1, landing.BumpMoveFact); got != "conflict" {
		t.Fatalf("move fact = %q", got)
	}
	posted, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{"report"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(posted) != 1 || !strings.Contains(posted[0].Text, "CONFLICT replaying branch-1 onto dev at abc123 the subject") {
		t.Fatalf("records = %+v, want the conflict named once", posted)
	}
}

func TestARebaseWithNothingToMoveSaysSoOnce(t *testing.T) {
	s := newStore(t)
	r := newStuckRig(t, s)
	r.move = func(int) (string, error) { return "BASE dev " + head + "\n", nil }
	unknownSince(t, s, t0, 1)
	threeBumpsDone(t, s, 1)
	r.now = t0.Add(time.Hour)

	r.pass(t, stuck(1))

	posted, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{"report"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(posted) != 1 || !strings.Contains(posted[0].Text, "nothing moved") {
		t.Fatalf("records = %+v, want one saying nothing moved", posted)
	}
}

func TestARebaseThatFailsIsRetriedAfterTenMinutes(t *testing.T) {
	s := newStore(t)
	r := newStuckRig(t, s)
	r.move = func(int) (string, error) { return "", errors.New("the clone is busy") }
	unknownSince(t, s, t0, 1)
	threeBumpsDone(t, s, 1)
	r.now = t0.Add(time.Hour)

	if err := r.unstick().Pass(context.Background(), []machine.Facts{stuck(1)}); err == nil {
		t.Fatal("a failed rebase said nothing")
	}
	if got := fact(t, s, 1, landing.BumpMoveFact); got != "" {
		t.Fatalf("move fact = %q, want none so it retries", got)
	}
	r.now = t0.Add(time.Hour + 9*time.Minute)
	_ = r.unstick().Pass(context.Background(), []machine.Facts{stuck(1)})
	if len(r.moves) != 1 {
		t.Fatalf("moves = %v, want no retry inside ten minutes", r.moves)
	}
	r.now = t0.Add(time.Hour + 10*time.Minute)
	_ = r.unstick().Pass(context.Background(), []machine.Facts{stuck(1)})
	if len(r.moves) != 2 {
		t.Fatalf("moves = %v, want a retry at ten minutes", r.moves)
	}
}

func TestAPassMovesAtMostTwoAndTheRestWait(t *testing.T) {
	s := newStore(t)
	r := newStuckRig(t, s)
	unknownSince(t, s, t0, 1, 2, 3)
	threeBumpsDone(t, s, 1, 2, 3)
	r.now = t0.Add(time.Hour)
	snapshot := []machine.Facts{stuck(1), stuck(2), stuck(3)}

	r.pass(t, snapshot...)
	if !slices.Equal(r.moves, []int{1, 2}) {
		t.Fatalf("moves = %v, want two", r.moves)
	}
	if got := r.patches(); len(got) != 0 {
		t.Fatalf("patches = %v, want the third to wait without another PATCH", got)
	}

	r.pass(t, snapshot...)
	if !slices.Equal(r.moves, []int{1, 2, 3}) {
		t.Fatalf("moves = %v, want the third on the next pass", r.moves)
	}
}

func TestANativeStacksRefusedPatchCountsTowardTheRebaseAndCostsNoRead(t *testing.T) {
	s := newStore(t)
	r := newStuckRig(t, s)
	r.runner = &shelltest.Fake{}
	r.runner.Fail(1, "gh: Cannot change the base branch because the pull request is part of a stack (HTTP 422)", "gh", "api", "-X", "PATCH")
	unknownSince(t, s, t0, 1)
	r.now = t0.Add(10 * time.Minute)

	r.pass(t, stuck(1))

	if got := fact(t, s, 1, prrow.FactBumps); got != "1" {
		t.Fatalf("bumps = %q, want the refusal counted", got)
	}
	if len(r.reads) != 0 {
		t.Fatalf("reads = %v, want none after a refused PATCH", r.reads)
	}
}

func TestAHeldStackMemberStopsPatchingAfterItsThirdRefusalAndMovesOnceReleased(t *testing.T) {
	s := newStore(t)
	r := newStuckRig(t, s)
	r.runner = &shelltest.Fake{}
	r.runner.Fail(1, "gh: Cannot change the base branch because the pull request is part of a stack (HTTP 422)", "gh", "api", "-X", "PATCH")
	unknownSince(t, s, t0, 1)
	for minute := 10; minute <= 60; minute += 10 {
		r.now = t0.Add(time.Duration(minute) * time.Minute)
		r.pass(t, stuck(1, held))
	}
	if got := r.patches(); len(got) != 3 {
		t.Fatalf("%d PATCHes on a stack that refuses each one, want 3", len(got))
	}
	if len(r.moves) != 0 {
		t.Fatalf("moved a held row: %v", r.moves)
	}

	r.now = t0.Add(70 * time.Minute)
	r.pass(t, stuck(1))

	if !slices.Equal(r.moves, []int{1}) {
		t.Fatalf("moves = %v, want the rebase once the hold lifts", r.moves)
	}
}

func TestAFailedPatchDoesNotCountButStillWaitsTenMinutes(t *testing.T) {
	s := newStore(t)
	r := newStuckRig(t, s)
	r.runner = &shelltest.Fake{}
	r.runner.Fail(1, "gh: Bad Gateway (HTTP 502)", "gh", "api", "-X", "PATCH")
	unknownSince(t, s, t0, 1)
	r.now = t0.Add(10 * time.Minute)

	if err := r.unstick().Pass(context.Background(), []machine.Facts{stuck(1)}); err == nil {
		t.Fatal("a failed PATCH said nothing")
	}
	if got := fact(t, s, 1, prrow.FactBumps); got != "" {
		t.Fatalf("bumps = %q, want a 502 uncounted", got)
	}
	r.now = t0.Add(19 * time.Minute)
	_ = r.unstick().Pass(context.Background(), []machine.Facts{stuck(1)})
	if got := r.patches(); len(got) != 1 {
		t.Fatalf("%d PATCHes inside ten minutes of a failed one, want 1", len(got))
	}
}

func TestGitHubCallsGrowOnlyWithStuckRowsAndTheReadIsBatched(t *testing.T) {
	calls := func(t *testing.T, live, stuckRows int) (patches, reads int) {
		t.Helper()
		s := newStore(t)
		r := newStuckRig(t, s)
		snapshot := make([]machine.Facts, 0, live)
		for number := 1; number <= live; number++ {
			if number <= stuckRows {
				unknownSince(t, s, t0, number)
				snapshot = append(snapshot, stuck(number))
				continue
			}
			snapshot = append(snapshot, facts(number, nil))
		}
		r.now = t0.Add(10 * time.Minute)
		r.pass(t, snapshot...)
		return len(r.patches()), len(r.reads)
	}

	for _, live := range []int{1, 1000} {
		if patches, reads := calls(t, live, 0); patches != 0 || reads != 0 {
			t.Fatalf("N=%d with none stuck: %d PATCHes, %d reads, want none", live, patches, reads)
		}
		if patches, reads := calls(t, live, 1); patches != 1 || reads != 1 {
			t.Fatalf("N=%d with one stuck: %d PATCHes, %d reads, want one of each", live, patches, reads)
		}
	}
	if patches, reads := calls(t, 1000, 30); patches != 30 || reads != 1 {
		t.Fatalf("N=1000 with 30 stuck: %d PATCHes, %d reads, want 30 and one batched read", patches, reads)
	}
}
