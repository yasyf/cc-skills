package asks

import (
	"context"
	"testing"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ledger/ledgertest"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

type counting struct {
	*ledgertest.Fake
	reads int
}

func (c *counting) Rows(ctx context.Context, id string) ([]ledger.Row, error) {
	c.reads++
	return c.Fake.Rows(ctx, id)
}

func TestAFreshMirrorAnswersWithNoLedgerReadAndAStaleOneResyncsFirst(t *testing.T) {
	ctx := context.Background()
	source := &counting{Fake: &ledgertest.Fake{}}
	if err := source.SetRow(ctx, "l1", "ask/000001", map[string]string{"lane": "a", "text": "first", "asked_at": now.Format(time.RFC3339)}); err != nil {
		t.Fatal(err)
	}
	s, err := store.Memory()
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	clock := now
	mirror := Mirror{source, s, "l1", func() time.Time { return clock }}
	book, err := mirror.Book(ctx)
	if err != nil || len(book.Asks) != 1 || source.reads != 1 {
		t.Fatalf("first read: %+v, %v, %d reads", book, err, source.reads)
	}
	if err := source.SetRow(ctx, "l1", "ask/000002", map[string]string{"lane": "a", "text": "second", "asked_at": now.Format(time.RFC3339)}); err != nil {
		t.Fatal(err)
	}
	clock = now.Add(mirrorFresh - time.Second)
	if book, err = mirror.Book(ctx); err != nil || len(book.Asks) != 1 || source.reads != 1 {
		t.Fatalf("fresh read: %+v, %v, %d reads", book, err, source.reads)
	}
	clock = now.Add(mirrorFresh)
	if book, err = mirror.Book(ctx); err != nil || len(book.Asks) != 2 || source.reads != 2 {
		t.Fatalf("stale read: %+v, %v, %d reads", book, err, source.reads)
	}
}

func TestAGradedLineLeadsWithItsStateAndNamesItsProgress(t *testing.T) {
	graded := Graded{Ask{Key: "ask/000001", Lane: "a", Text: "t", Newest: &Progress{At: now.Add(-3 * time.Minute), Note: "halfway"}}, InProgress}
	if line := graded.Status(now); line != "IN-PROGRESS ask/000001 a: t | progress 3m: halfway" {
		t.Fatalf("line = %q", line)
	}
}
