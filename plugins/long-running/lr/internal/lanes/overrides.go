package lanes

import (
	"context"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/quotes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	overrideEvery   = time.Minute
	overrideHorizon = time.Hour
)

type OverrideRefusals struct {
	Env   Env
	Store *store.Store

	read   time.Time
	quoted int64
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
	reader := quotes.NewReader(o.Env.Runner, o.Env.Config.Drive)
	for _, record := range records {
		if unread := bus.UnreadOverrides(record); len(unread) > 0 {
			text := fmt.Sprintf("OVERRIDE-REFUSED cci #%d reads `%s`, which names no `finder-override #<pr> at <sha>: <id>` or `fidelity-override #<pr> at <sha>: <id>`, so it settles no grade; post it again in that form", record.Seq, strings.Join(unread, "`, `"))
			if err := o.tell(ctx, "override:"+strconv.FormatInt(record.Seq, 10), blockerKind, text, now); err != nil {
				return err
			}
		}
		if record.Seq <= o.quoted {
			continue
		}
		verdict := reader.Override(ctx, record)
		if len(verdict.Refused) > 0 {
			text := fmt.Sprintf("OVERRIDE-REFUSED cci #%d settles no grade: %s. Read the row whole with `lr rules row <key>`, quote its exact words, and post it again", record.Seq, strings.Join(verdict.Refused, "; "))
			if err := o.tell(ctx, "override-quote:"+strconv.FormatInt(record.Seq, 10), blockerKind, text, now); err != nil {
				return err
			}
		}
		if len(verdict.Warned) > 0 {
			text := fmt.Sprintf("OVERRIDE-WARNING cci #%d still counts: %s. Read the row whole with `lr rules row <key>` and check the words say what it claims", record.Seq, strings.Join(verdict.Warned, "; "))
			if err := o.tell(ctx, "override-warning:"+strconv.FormatInt(record.Seq, 10), "note", text, now); err != nil {
				return err
			}
		}
		o.quoted = record.Seq
	}
	return nil
}

func (o *OverrideRefusals) tell(ctx context.Context, key, kind, text string, now time.Time) error {
	first, err := o.Store.Escalate(key, "override-refused", Root, now)
	if err != nil || !first {
		return err
	}
	if _, err := o.Env.Bus.Post(ctx, client.Draft{Drive: o.Env.Config.Drive, Lane: Self, Kind: kind, To: []string{Root}, ID: key, Text: text}); err != nil {
		return fmt.Errorf("%w: cci post: %w", ErrUnread, err)
	}
	return nil
}
