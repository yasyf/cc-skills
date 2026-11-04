package facts

import (
	"context"
	"fmt"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/ccx"
)

const (
	OrderCursor = "tap-orders"
	OrderLane   = "tap-orders"
	OrderGrace  = time.Minute
	orderWindow = time.Hour
	tapRecord   = "lr.tap"
	openState   = "OPEN"
	fullSHA     = 40
)

var orderWatchers = []string{"meta-desk", "owner-relay"}

// WriteOrders turns each owner tap recorded on any surface into the one lr.tap record the landing pass and every desk read.
func (s Source) WriteOrders(ctx context.Context) error {
	since, err := s.Store.Cursor(OrderCursor)
	if err != nil {
		return err
	}
	now := s.Now()
	records, err := s.Bus.ReadOwner(ctx, since, now.Add(-orderWindow))
	if err != nil {
		return err
	}
	settled := true
	for _, record := range records {
		if tap, ordered := bus.OrderOf(record, s.Repo); ordered {
			age := now.Sub(record.At)
			if failed := s.writeOrder(ctx, record, tap, age); failed != nil {
				s.Log.Warn("an owner tap has no land order yet", "record", record.Name(), "pr", tap.PR, "err", failed)
				settled = settled && age >= orderWindow
				if age >= OrderGrace {
					s.escalateOrder(ctx, record, tap, failed)
				}
				continue
			}
		}
		if !settled {
			continue
		}
		if err := s.Store.Advance(OrderCursor, record.Seq); err != nil {
			return err
		}
	}
	return nil
}

func (s Source) writeOrder(ctx context.Context, record client.Record, tap bus.Tap, age time.Duration) error {
	if tap.Head == "" {
		if age >= orderWindow {
			return nil
		}
		written, err := client.Tail(ctx, client.Query{Drive: s.Drive, After: record.Seq, Kinds: []string{tapRecord}, PR: fmt.Sprintf("%s#%d", s.Repo, tap.PR)})
		if err != nil {
			return err
		}
		if len(written) > 0 {
			return nil
		}
	}
	head, open, err := s.orderHead(ctx, tap)
	if err != nil || !open {
		return err
	}
	tap.Head = head
	_, err = bus.Bus{Drive: s.Drive, Lane: OrderLane}.PostOrder(ctx, tap, record)
	return err
}

func (s Source) orderHead(ctx context.Context, tap bus.Tap) (string, bool, error) {
	rows, err := s.CCX.PRStatus(ctx, s.Repo, []int{tap.PR})
	if err != nil {
		return "", false, err
	}
	at := slices.IndexFunc(rows, func(row ccx.PRStatus) bool { return row.Number == tap.PR && row.State == openState })
	switch {
	case at < 0:
		return "", false, nil
	case len(tap.Head) == fullSHA:
		return tap.Head, true, nil
	case strings.HasPrefix(rows[at].Head, tap.Head):
		return rows[at].Head, true, nil
	}
	said, err := s.CCX.GH(ctx, ccx.MaxAgeFresh, "api", fmt.Sprintf("repos/%s/commits/%s", s.Repo, tap.Head), "--jq", ".sha")
	return strings.TrimSpace(said), true, err
}

func (s Source) escalateOrder(ctx context.Context, record client.Record, tap bus.Tap, failed error) {
	_, err := client.Post(ctx, client.Draft{
		Drive: s.Drive, Lane: OrderLane, Kind: "blocker", To: orderWatchers, ID: fmt.Sprintf("tap-order-missing:%d", record.Seq),
		PRs:  []string{fmt.Sprintf("%s#%d", s.Repo, tap.PR)},
		Text: fmt.Sprintf("Owner tap %s on #%d has no land order after %s, so nothing will land it: %v. Nothing was blocked; lr landing order %d --record %d writes it.", record.Name(), tap.PR, OrderGrace, failed, tap.PR, record.Seq),
	})
	if err != nil {
		s.Log.Warn("a missing land order could not be escalated", "record", record.Name(), "err", err)
	}
}
