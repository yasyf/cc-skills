package landing

import (
	"context"
	"errors"
	"fmt"
	"slices"
	"strconv"
	"strings"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/watch"
)

const (
	landedKind    = "landed"
	rootLane      = "root"
	queueAccepted = "exit 0: "
)

func Announce(ctx context.Context, s *store.Store, drive, trunk string, landings []pass.Landing, route pass.Route) error {
	if len(landings) == 0 {
		return nil
	}
	since := landings[0].LastOpen
	for _, landing := range landings {
		if landing.LastOpen.Before(since) {
			since = landing.LastOpen
		}
	}
	records, err := client.Tail(ctx, client.Query{Drive: drive, Since: since, Kinds: []string{landedKind}})
	if err != nil {
		return err
	}
	said := watch.LandedPRs(records)
	var unposted []error
	for _, landing := range landings {
		f := landing.Facts
		number := strconv.Itoa(f.Number)
		if _, known := said[number]; known {
			continue
		}
		owner, _, err := reach(s, drive, f)
		if err != nil {
			return err
		}
		_, err = client.Post(ctx, client.Draft{
			Drive: drive,
			Lane:  self,
			Kind:  landedKind,
			To:    landedAudience(owner, route),
			ID:    landedKind + ":" + number,
			PRs:   []string{f.Repo + "#" + number},
			Text:  fmt.Sprintf("LANDED #%d at %s: merged into %s", f.Number, f.Head[:min(10, len(f.Head))], trunk),
		})
		unposted = append(unposted, err)
	}
	return errors.Join(unposted...)
}

func Settle(ctx context.Context, s *store.Store, drive string, answers []Said) error {
	unposted := make([]error, 0, len(answers))
	for _, answer := range answers {
		f := answer.Facts
		holder, unowned, err := reach(s, drive, f)
		if err != nil {
			return err
		}
		label := answer.Refused
		if strings.Contains(answer.Text, queueAccepted) {
			label, unowned = answer.Accepted, ""
		}
		to := []string{holder}
		kind, id := "report", fmt.Sprintf("settled:%d:%s:%s", f.Number, f.Head, label)
		if answer.Alarm {
			meta, err := lanes.MetaDesk(s)
			if err != nil {
				return err
			}
			if to, err = rowAudience(s, drive, f, holder, meta); err != nil {
				return err
			}
			kind, id = "blocker", ""
		}
		_, err = client.Post(ctx, client.Draft{
			Drive: drive,
			Lane:  self,
			Kind:  kind,
			To:    to,
			ID:    id,
			PRs:   []string{pullRef(f)},
			Text:  fmt.Sprintf("%s%s #%d at %s: %s", unowned, label, f.Number, shortHead(f.Head), answer.Text),
		})
		unposted = append(unposted, err)
	}
	return errors.Join(unposted...)
}

func landedAudience(owner string, route pass.Route) []string {
	to := []string{owner}
	if board := route(pass.BoardDesk); board != rootLane && !slices.Contains(to, board) {
		to = append(to, board)
	}
	return to
}
