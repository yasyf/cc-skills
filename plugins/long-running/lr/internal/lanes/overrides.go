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
		if err := o.tellOwners(ctx, record, now); err != nil {
			return err
		}
		if warnings := reader.Override(ctx, record); len(warnings) > 0 {
			text := fmt.Sprintf("OVERRIDE-WARNING cci #%d still counts: %s. Read the row whole with `lr rules row <key>`, where an answer's key is `answer <id>`, and check its words say what the override says they say", record.Seq, strings.Join(warnings, "; "))
			if err := o.tell(ctx, "override-warning:"+strconv.FormatInt(record.Seq, 10), "note", text, now); err != nil {
				return err
			}
		}
		o.quoted = record.Seq
	}
	return nil
}

func (o *OverrideRefusals) tellOwners(ctx context.Context, record client.Record, now time.Time) error {
	for _, override := range bus.Overrides(record) {
		owners, err := o.Store.OwnerOf([]int{override.PR})
		if err != nil {
			return err
		}
		owner := owners[override.PR]
		if owner == "" {
			continue
		}
		key := fmt.Sprintf("override-owner:%d:%d", record.Seq, override.PR)
		first, err := o.Store.Escalate(key, "override-owner", owner, now)
		if err != nil {
			return err
		}
		if !first {
			continue
		}
		text := fmt.Sprintf("Root overrode a grade on your #%d at %s in cci #%d, so that finding is settled at this head: read the record before pushing a fix for it, since a push loses the override.", override.PR, override.Head, record.Seq)
		if _, err := o.Env.Bus.Post(ctx, client.Draft{Drive: o.Env.Config.Drive, Lane: Self, Kind: "note", To: []string{owner}, ID: key, Text: text}); err != nil {
			return fmt.Errorf("%w: cci post: %w", ErrUnread, err)
		}
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
