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
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/prrow"
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

// WriteOrders turns each owner tap recorded on any surface into the one lr.tap record the landing pass and every desk read, or, on a row not ready at that head, one tap.refused naming why.
func (s Source) WriteOrders(ctx context.Context) error {
	if !s.Reads {
		return nil
	}
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
		if bus.UnreadPick(record) {
			s.escalate(ctx, record, fmt.Sprintf("tap-order-missing:%d", record.Seq), nil, fmt.Sprintf("Owner pick %s (%s, option %s) was recorded with no label because its board could not be read, so no land order can come from it. Nothing was blocked; read what that option said and write the order if it lands a PR.", record.Name(), record.Fields.Attrs["block"], record.Fields.Attrs["picked"]))
		}
		if bus.UnnamedLand(record, s.Repo) {
			s.escalate(ctx, record, fmt.Sprintf("tap-order-missing:%d", record.Seq), nil, fmt.Sprintf("Owner pick %s (%s) reads as a land or hold order, but its card names no one PR, so no land order can come from it. Nothing was blocked; name the PR in a record and carry it with lr landing order.", record.Name(), record.Fields.Attrs["block"]))
		}
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
			return s.endTapHolds(ctx, tap, record)
		}
	}
	head, refusal, open, err := s.orderAt(ctx, tap)
	if err != nil || !open {
		return err
	}
	tap.Head = head
	orders := bus.Bus{Drive: s.Drive, Lane: OrderLane}
	if refusal != "" {
		if err := orders.PostRefusal(ctx, s.Repo, tap, refusal); err != nil {
			return err
		}
		s.escalate(ctx, record, fmt.Sprintf("tap-refused:%d", record.Seq), []string{fmt.Sprintf("%s#%d", s.Repo, tap.PR)}, fmt.Sprintf("His approval %s of #%d at %s was refused and lands nothing: %s. Tell him what stops it; once that is lifted, his approval needs a new tap.", record.Name(), tap.PR, tap.Head[:min(12, len(tap.Head))], refusal))
		return nil
	}
	if _, err := orders.PostOrder(ctx, tap, record); err != nil {
		return err
	}
	return s.endTapHolds(ctx, tap, record)
}

func (s Source) endTapHolds(ctx context.Context, tap bus.Tap, word client.Record) error {
	if tap.Value != string(machine.TapTapped) {
		return nil
	}
	row, found, err := s.Store.ReviewRow(s.Repo, tap.PR)
	if err != nil || !found || !strings.HasPrefix(row.Head, tap.Head) {
		return err
	}
	holds, err := client.Open(ctx, s.Drive, "hold")
	if err != nil {
		return err
	}
	for _, held := range holds {
		if !bus.HoldsUntilTap(held) || !slices.Contains(bus.HeldBy(held, s.Repo), tap.PR) || !held.At.Before(word.At) {
			continue
		}
		if err := (bus.Bus{Drive: s.Drive, Lane: OrderLane}).PostHoldEnded(ctx, s.Repo, tap.PR, held); err != nil {
			return err
		}
	}
	return nil
}

func (s Source) orderAt(ctx context.Context, tap bus.Tap) (head, refusal string, open bool, err error) {
	if tap.Value != string(machine.TapTapped) {
		head, open, err = s.orderHead(ctx, tap)
		return head, "", open, err
	}
	head, refusal, err = prrow.TapAt(s.Store, s.Repo, tap)
	return head, refusal, true, err
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
	s.escalate(ctx, record, fmt.Sprintf("tap-order-missing:%d", record.Seq), []string{fmt.Sprintf("%s#%d", s.Repo, tap.PR)}, fmt.Sprintf("Owner tap %s on #%d has no land order after %s, so nothing will land it: %v. Nothing was blocked; lr landing order %d --record %d writes it.", record.Name(), tap.PR, OrderGrace, failed, tap.PR, record.Seq))
}

func (s Source) escalate(ctx context.Context, record client.Record, id string, prs []string, text string) {
	_, err := client.Post(ctx, client.Draft{Drive: s.Drive, Lane: OrderLane, Kind: "blocker", To: orderWatchers, ID: id, PRs: prs, Text: text})
	if err != nil {
		s.Log.Warn("a missing land order could not be escalated", "record", record.Name(), "err", err)
	}
}
