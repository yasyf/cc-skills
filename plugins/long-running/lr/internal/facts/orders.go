package facts

import (
	"context"
	"errors"
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
	OrderWindow = time.Hour
	tapRecord   = "lr.tap"
	openState   = "OPEN"
	fullSHA     = 40
)

var orderWatchers = []string{"meta-desk", "owner-relay"}

var errHeadWait = errors.New(prrow.NoHeadYet)

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
	records, err := s.Bus.ReadOwner(ctx, since, now.Add(-OrderWindow))
	if err != nil {
		return err
	}
	settled := true
	for at, record := range records {
		age := now.Sub(record.At)
		if bus.UnreadPick(record) {
			s.escalate(ctx, record, fmt.Sprintf("tap-order-missing:%d", record.Seq), nil, fmt.Sprintf("Owner pick %s (%s, option %s) was recorded with no label because its board could not be read, so no land order can come from it. Nothing was blocked; read what that option said and write the order if it lands a PR.", record.Name(), record.Fields.Attrs["block"], record.Fields.Attrs["picked"]))
		}
		if bus.UnnamedLand(record, s.Repo) && !bus.CarriedEach(record, records[at+1:], s.Repo) {
			settled = settled && age >= OrderGrace
			if age >= OrderGrace {
				s.escalate(ctx, record, fmt.Sprintf("tap-order-missing:%d", record.Seq), nil, fmt.Sprintf("Owner pick %s (%s) reads as a land or hold order, but its card names no one PR, so no land order can come from it. Nothing was blocked; name the PR in a record and carry it with lr landing order.", record.Name(), record.Fields.Attrs["block"]))
			}
		}
		if tap, ordered := bus.OrderOf(record, s.Repo); ordered {
			if failed := s.writeOrder(ctx, record, tap, now); failed != nil {
				s.Log.Warn("an owner tap has no land order yet", "record", record.Name(), "pr", tap.PR, "err", failed)
				settled = settled && age >= OrderWindow
				switch {
				case errors.Is(failed, errHeadWait), errors.Is(failed, prrow.ErrRowsNotFolded):
					if age >= OrderWindow {
						s.escalateOrder(ctx, record, tap, OrderWindow, failed)
					}
				case age >= OrderGrace:
					s.escalateOrder(ctx, record, tap, OrderGrace, failed)
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

func (s Source) writeOrder(ctx context.Context, record client.Record, tap bus.Tap, now time.Time) error {
	if tap.Head == "" {
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
	orders := bus.Bus{Drive: s.Drive, Lane: OrderLane}
	switch {
	case refusal == prrow.NoHeadYet:
		if err := orders.PostPending(ctx, s.Repo, tap, record); err != nil {
			return err
		}
		return errHeadWait
	case tap.Head == "" && now.Sub(record.At) >= OrderWindow, refusal == prrow.AlreadyLanded, refusal == prrow.ClosedUnlanded:
		return nil
	}
	tap.Head = head
	if refusal != "" {
		if err := orders.PostRefusal(ctx, s.Repo, tap, refusal); err != nil {
			return err
		}
		s.escalate(ctx, record, fmt.Sprintf("tap-refused:%d", record.Seq), []string{fmt.Sprintf("%s#%d", s.Repo, tap.PR)}, fmt.Sprintf("His approval %s of #%d%s was refused and lands nothing: %s. Tell him what stops it.", record.Name(), tap.PR, bus.At(tap.Head), refusal))
		return nil
	}
	if moved, err := s.movedSinceRefusal(ctx, record, tap); err != nil || moved {
		return err
	}
	order, err := orders.PostOrder(ctx, tap, record)
	if err != nil {
		return err
	}
	if !order.At.Before(now) {
		s.Log.Info("an owner tap became a land order", "record", record.Name(), "pr", tap.PR, "head", tap.Head, "order", order.Seq)
	}
	return s.endTapHolds(ctx, tap, record)
}

func (s Source) movedSinceRefusal(ctx context.Context, word client.Record, tap bus.Tap) (bool, error) {
	refused, err := client.Tail(ctx, client.Query{Drive: s.Drive, After: word.Seq, Kinds: []string{bus.TapRefusedKind}, Lanes: []string{OrderLane}, PR: fmt.Sprintf("%s#%d", s.Repo, tap.PR)})
	if err != nil {
		return false, err
	}
	return slices.ContainsFunc(refused, func(record client.Record) bool { return bus.RefusedHead(record) != tap.Head }), nil
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
	if !errors.Is(err, prrow.ErrRowsNotFolded) {
		return head, refusal, true, err
	}
	if _, open, read := s.orderHead(ctx, tap); read != nil || !open {
		return "", "", false, read
	}
	return "", "", true, err
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

func (s Source) escalateOrder(ctx context.Context, record client.Record, tap bus.Tap, after time.Duration, failed error) {
	s.escalate(ctx, record, fmt.Sprintf("tap-order-missing:%d", record.Seq), []string{fmt.Sprintf("%s#%d", s.Repo, tap.PR)}, fmt.Sprintf("Owner tap %s on #%d has no land order after %s, so nothing will land it: %v. Nothing was blocked; lr landing order %d --record %d writes it.", record.Name(), tap.PR, after, failed, tap.PR, record.Seq))
}

func (s Source) escalate(ctx context.Context, record client.Record, id string, prs []string, text string) {
	_, err := client.Post(ctx, client.Draft{Drive: s.Drive, Lane: OrderLane, Kind: "blocker", To: orderWatchers, ID: id, PRs: prs, Text: text})
	if err != nil {
		s.Log.Warn("a missing land order could not be escalated", "record", record.Name(), "err", err)
	}
}
