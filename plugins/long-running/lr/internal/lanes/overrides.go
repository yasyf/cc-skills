package lanes

import (
	"context"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	overrideEvery   = time.Minute
	overrideHorizon = time.Hour
)

type OverrideRefusals struct {
	Env   Env
	Store *store.Store

	read time.Time
}

func (o *OverrideRefusals) Check(ctx context.Context, now time.Time) error {
	if now.Sub(o.read) < overrideEvery {
		return nil
	}
	o.read = now
	records, err := o.Env.Bus.Tail(ctx, client.Query{Drive: o.Env.Config.Drive, Kinds: []string{"decide"}, Lanes: []string{Root}, Since: now.Add(-overrideHorizon)})
	if err != nil {
		return fmt.Errorf("%w: cci tail decide: %w", ErrUnread, err)
	}
	for _, record := range records {
		unread := bus.UnreadOverrides(record)
		if len(unread) == 0 {
			continue
		}
		key := "override:" + strconv.FormatInt(record.Seq, 10)
		first, err := o.Store.Escalate(key, "override-refused", Root, now)
		if err != nil {
			return err
		}
		if !first {
			continue
		}
		text := fmt.Sprintf("OVERRIDE-REFUSED cci #%d reads `%s`, which names no `finder-override #<pr> at <sha>: <id>` or `fidelity-override #<pr> at <sha>: <id>`, so it settles no grade; post it again in that form", record.Seq, strings.Join(unread, "`, `"))
		if _, err := o.Env.Bus.Post(ctx, client.Draft{Drive: o.Env.Config.Drive, Lane: Self, Kind: blockerKind, To: []string{Root}, ID: key, Text: text}); err != nil {
			return fmt.Errorf("%w: cci post: %w", ErrUnread, err)
		}
	}
	return nil
}
