package lanes

import (
	"context"
	"fmt"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/bus"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/machine"
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
	records, err := o.Env.Bus.Tail(ctx, client.Query{Drive: o.Env.Config.Drive, Kinds: []string{"decide"}, Since: now.Add(-overrideHorizon)})
	if err != nil {
		return fmt.Errorf("%w: cci tail decide: %w", ErrUnread, err)
	}
	reader := quotes.NewReader(o.Env.Runner, o.Env.Config.Drive)
	for _, record := range records {
		if record.Lane != Root && !bus.Appeal(record) {
			continue
		}
		if unread := bus.UnreadOverrides(record); len(unread) > 0 {
			if err := o.refuse(ctx, record, unreadRefusal(record.Name(), unread), now); err != nil {
				return err
			}
		}
		if record.Seq <= o.quoted {
			continue
		}
		live, err := o.refuseWithdrawn(ctx, record, now)
		if err != nil {
			return err
		}
		if record.Lane != Root {
			grounded, err := reader.Grounded(ctx, record)
			if err != nil {
				return fmt.Errorf("%w: the owner's rows: %w", ErrUnread, err)
			}
			if !grounded {
				text := fmt.Sprintf("OVERRIDE-REFUSED cci %s cites no owner row that exists, so it counts for nothing; an override citing no owner row goes to root", record.Name())
				if err := o.refuse(ctx, record, text, now); err != nil {
					return err
				}
				o.quoted = record.Seq
				continue
			}
			if err := o.count(ctx, record, live, now); err != nil {
				return err
			}
		}
		if err := o.tellOwners(ctx, record, live, now); err != nil {
			return err
		}
		if warnings := reader.Override(ctx, record); len(warnings) > 0 {
			text := fmt.Sprintf("OVERRIDE-WARNING cci %s still counts: %s. Read the row whole with `lr rules row <key>`, where an answer's key is `answer <id>`, and check its words say what the override says they say", record.Name(), strings.Join(warnings, "; "))
			key := "override-warning:" + strconv.FormatInt(record.Seq, 10)
			first, err := o.Store.Notice(key, now)
			if err != nil {
				return err
			}
			if first {
				if _, err := o.Env.Bus.Post(ctx, client.Draft{Drive: o.Env.Config.Drive, Lane: Self, Kind: "note", To: slices.Compact([]string{Root, record.Lane}), ID: key, Text: text}); err != nil {
					return fmt.Errorf("%w: cci post: %w", ErrUnread, err)
				}
			}
		}
		o.quoted = record.Seq
	}
	return nil
}

func unreadRefusal(name string, unread []bus.UnreadOverride) string {
	read := make([]string, 0, len(unread))
	var fixes []string
	for _, override := range unread {
		read = append(read, override.Text)
		if override.Fix != "" {
			fixes = append(fixes, override.Fix)
		}
	}
	text := fmt.Sprintf("OVERRIDE-REFUSED cci %s reads `%s`, which names no `finder-override #<pr> at <sha>: <id>` or `fidelity-override #<pr> at <sha>: <id>`, so it settles no grade; ", name, strings.Join(read, "`, `"))
	switch len(fixes) {
	case 0:
		return text + "post it again in that form"
	case len(unread):
		return text + "post `" + strings.Join(fixes, "`, `") + "` instead"
	}
	return text + "post `" + strings.Join(fixes, "`, `") + "` instead, and the rest again in that form"
}

func (o *OverrideRefusals) refuseWithdrawn(ctx context.Context, record client.Record, now time.Time) ([]bus.Override, error) {
	var live []bus.Override
	var withdrawn []string
	for _, override := range bus.Overrides(record) {
		stored, err := o.Store.Facts(o.Env.Config.Repo, override.PR, override.Head)
		if err != nil {
			return nil, err
		}
		if !machine.OverrideWithdrawn(stored, override.Grader, override.Value()) {
			live = append(live, override)
			continue
		}
		withdrawn = append(withdrawn, strings.TrimSuffix(override.Line(""), ". "))
	}
	if len(withdrawn) == 0 {
		return live, nil
	}
	text := fmt.Sprintf("OVERRIDE-REFUSED cci %s reads `%s`, which a withdraw took back at that head, so no copy of it counts; post the override again citing another record", record.Name(), strings.Join(withdrawn, "`, `"))
	return live, o.refuse(ctx, record, text, now)
}

func (o *OverrideRefusals) count(ctx context.Context, record client.Record, live []bus.Override, now time.Time) error {
	for _, override := range live {
		key := fmt.Sprintf("appeal-counted:%d:%s:%d", record.Seq, override.Grader, override.PR)
		first, err := o.Store.Notice(key, now)
		if err != nil {
			return err
		}
		if !first {
			continue
		}
		text := override.Line(fmt.Sprintf("Counted from %s's cci %s, whose owner rows exist.", record.Lane, record.Name()))
		if _, err := o.Env.Bus.Post(ctx, client.Draft{Drive: o.Env.Config.Drive, Lane: Self, Kind: "decide", ID: key, PRs: []string{strconv.Itoa(override.PR)}, Text: text}); err != nil {
			return fmt.Errorf("%w: cci post: %w", ErrUnread, err)
		}
	}
	return nil
}

func (o *OverrideRefusals) tellOwners(ctx context.Context, record client.Record, live []bus.Override, now time.Time) error {
	for _, override := range live {
		owners, err := o.Store.OwnerOf([]int{override.PR})
		if err != nil {
			return err
		}
		owner := owners[override.PR]
		if owner == "" {
			continue
		}
		key := fmt.Sprintf("override-owner:%d:%d", record.Seq, override.PR)
		holder, err := Running(o.Store, o.Env.Config.Drive, owner)
		if err != nil {
			return err
		}
		first, err := o.Store.Notice(key, now)
		if err != nil {
			return err
		}
		if !first {
			continue
		}
		overrider := "Root"
		if record.Lane != Root {
			overrider = record.Lane
		}
		text := fmt.Sprintf("%s overrode a grade on your #%d at %s in cci %s, so that finding is settled at this head: read the record before pushing a fix for it, since a push that changes the diff loses the override.", overrider, override.PR, override.Head, record.Name())
		if holder == Root {
			text = "UNOWNED, " + owner + " is not running: " + text
		}
		if _, err := o.Env.Bus.Post(ctx, client.Draft{Drive: o.Env.Config.Drive, Lane: Self, Kind: "note", To: []string{holder}, ID: key, Text: text}); err != nil {
			return fmt.Errorf("%w: cci post: %w", ErrUnread, err)
		}
	}
	return nil
}

func (o *OverrideRefusals) refuse(ctx context.Context, record client.Record, text string, now time.Time) error {
	key := "override:" + strconv.FormatInt(record.Seq, 10)
	first, err := o.Store.Escalate(key, "override-refused", Root, now)
	if err != nil || !first {
		return err
	}
	if _, err := o.Env.Bus.Post(ctx, client.Draft{Drive: o.Env.Config.Drive, Lane: Self, Kind: blockerKind, To: []string{record.Lane}, ID: key, Text: text}); err != nil {
		return fmt.Errorf("%w: cci post: %w", ErrUnread, err)
	}
	return nil
}
