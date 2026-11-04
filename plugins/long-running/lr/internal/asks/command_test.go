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

func TestADashboardOrderReadsLiveInAPROrItsOwnerAndDueTime(t *testing.T) {
	landed := map[string]string{"state": "landed", "landed_at": stamp(time.Hour)}
	due := stamp(-2 * time.Hour)
	cases := []struct {
		name string
		rows map[string]map[string]string
		want string
	}{
		{"live", map[string]map[string]string{"ask/000001": ask(map[string]string{"prs": "7", "due": due}), "7": landed, "live": {"at": stamp(time.Minute)}}, "LIVE by Nov 2 10:59 AM, landed Nov 2 10:00 AM in 7 | ask/000001 lane-1: measure idle"},
		{"in a PR", map[string]map[string]string{"ask/000001": ask(map[string]string{"prs": "7", "due": due}), "7": {"head": "abc"}}, "IN-PR 7, lane-1 due Nov 2 1:00 PM | ask/000001 lane-1: measure idle"},
		{"unowned", map[string]map[string]string{"ask/000001": ask(map[string]string{"lane": "root", "due": due})}, "fresh, UNOWNED due Nov 2 1:00 PM | ask/000001 UNOWNED: measure idle"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			book := read(t, c.rows)
			graded := Graded{book.Asks[0], book.State(book.Asks[0], now)}
			if got := book.Order(graded); got != c.want {
				t.Fatalf("order = %q, want %q", got, c.want)
			}
		})
	}
}

func TestAnAskNamingADashboardTabOrPageIsADashboardOrderUnlessTheLaneSaysOtherwise(t *testing.T) {
	cases := map[NewAsk]string{
		{Text: "the Review tab lists PRs only"}:                   Dashboard,
		{Text: "this dashboard chat is terrible"}:                 Dashboard,
		{Text: "clicking the card should open the needs me page"}: Dashboard,
		{Text: "review the PR before it merges"}:                  "",
		{Text: "the board card still says it", Surface: "board"}:  "board",
		{Text: "the Chat tab drops my asks", Surface: "slack"}:    "slack",
	}
	for ask, want := range cases {
		if got := surfaceOf(ask); got != want {
			t.Errorf("%q: surface %q, want %q", ask.Text, got, want)
		}
	}
}
