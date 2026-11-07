package landing_test

import (
	"context"
	"io"
	"log/slog"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/landing"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

func holdWatch(s *store.Store, q *queue, read func(context.Context, []int) ([]machine.Facts, error)) *landing.HoldWatch {
	return &landing.HoldWatch{
		Store: s, Drive: "d", Repo: repo, Trunk: "dev", Read: read, Queue: q,
		Now: func() time.Time { return t0 }, Log: slog.New(slog.NewTextHandler(io.Discard, nil)),
	}
}

func hold(t *testing.T, numbers ...string) client.Record {
	t.Helper()
	record, err := client.Post(context.Background(), client.Draft{Drive: "d", Lane: "root", Kind: "hold", Text: "stop this landing", PRs: numbers, Attrs: map[string]string{bus.BreaksAttr: "the cutover deletes what it reads"}})
	if err != nil {
		t.Fatal(err)
	}
	return record
}

func snapshotOf(snapshot ...machine.Facts) func(context.Context, []int) ([]machine.Facts, error) {
	return func(context.Context, []int) ([]machine.Facts, error) { return snapshot, nil }
}

func queued(f *machine.Facts) {
	f.Queue, f.QueuedHead = machine.QueueQueued, f.Head
}

func TestANewHoldRunsTheDequeueAsItsRecordArrivesWithNoPass(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	s, q := newStore(t), &queue{}
	before := hold(t, "1")
	read := make(chan []int, 1)
	w := holdWatch(s, q, func(_ context.Context, named []int) ([]machine.Facts, error) {
		read <- named
		return []machine.Facts{facts(7, func(f *machine.Facts) { queued(f); f.Hold = machine.MoverRoot })}, nil
	})
	w.After = before.Seq
	ctx, stop := context.WithCancel(context.Background())
	defer stop()
	finished := make(chan struct{})
	go func() {
		w.Run(ctx)
		close(finished)
	}()

	hold(t, "7")

	select {
	case named := <-read:
		if !slices.Equal(named, []int{7}) {
			t.Fatalf("read %v, want the PR the hold names", named)
		}
	case <-time.After(30 * time.Second):
		t.Fatal("a new hold read nothing with the clock standing still before any pass")
	}
	stop()
	<-finished
	if !slices.Equal(q.dequeued, []int{7}) {
		t.Fatalf("dequeued = %v", q.dequeued)
	}
}

func TestAHoldPullsItsHeadAndTheHeadAboveItTopFirstAndSaysSo(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	s, q := newStore(t), &queue{}
	below := facts(7, func(f *machine.Facts) { queued(f); f.Branch, f.Hold = "below", machine.MoverRoot })
	above := facts(8, func(f *machine.Facts) { queued(f); f.Branch, f.Base = "above", "below" })

	holdWatch(s, q, snapshotOf(below, above)).Handle(context.Background(), hold(t, "7"))

	if !slices.Equal(q.dequeued, []int{8, 7}) {
		t.Fatalf("dequeued = %v, want the head above first", q.dequeued)
	}
	records, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{"report"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(records) != 2 || !strings.HasPrefix(records[0].Text, "DEQUEUED #8") || !strings.HasPrefix(records[1].Text, "DEQUEUED #7") {
		t.Fatalf("records = %+v", records)
	}
}

func TestAHoldOnAHeadNotInTheQueueStartsNoActions(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	s, q := newStore(t), &queue{}
	landable := facts(7, func(f *machine.Facts) { f.Hold = machine.MoverRoot })
	queuedElsewhere := facts(9, queued)

	holdWatch(s, q, snapshotOf(landable, queuedElsewhere)).Handle(context.Background(), hold(t, "7"))

	if len(q.dequeued)+len(q.enqueued)+len(q.reentered) != 0 {
		t.Fatalf("queue moved: %+v", q)
	}
	records, err := client.Tail(context.Background(), client.Query{Drive: "d", Kinds: []string{"report", "blocker"}})
	if err != nil {
		t.Fatal(err)
	}
	if len(records) != 0 {
		t.Fatalf("records = %+v", records)
	}
	rows, err := s.PRs(repo)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 0 {
		t.Fatalf("rows written = %+v", rows)
	}
}

func TestAQueuedHeadThatIsNotHeldStaysWhateverElseIsWrongWithIt(t *testing.T) {
	t.Setenv("CCI_HOME", t.TempDir())
	s, q := newStore(t), &queue{}
	held := facts(7, func(f *machine.Facts) { queued(f); f.Hold = machine.MoverRoot })
	untapped := facts(9, func(f *machine.Facts) { queued(f); f.Tap = "" })
	movedHead := facts(10, func(f *machine.Facts) { f.Queue, f.QueuedHead = machine.QueueQueued, moved })

	holdWatch(s, q, snapshotOf(held, untapped, movedHead)).Handle(context.Background(), hold(t, "7"))

	if !slices.Equal(q.dequeued, []int{7}) {
		t.Fatalf("dequeued = %v, want only the held head; the pass judges the rest on facts it read itself", q.dequeued)
	}
}
