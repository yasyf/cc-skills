package landing

import (
	"context"
	"errors"
	"fmt"
	"slices"
	"strconv"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/pass"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/watch"
)

const (
	landedKind = "landed"
	rootLane   = "root"
)

// Announce posts one landed record per newly merged PR no record since its last
// open read already names, whichever lane or runner merged it.
func Announce(ctx context.Context, drive, trunk string, landings []pass.Landing, route pass.Route) error {
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
		_, err := client.Post(ctx, client.Draft{
			Drive: drive,
			Lane:  self,
			Kind:  landedKind,
			To:    landedAudience(f.Lane, route),
			ID:    landedKind + ":" + number,
			PRs:   []string{f.Repo + "#" + number},
			Text:  fmt.Sprintf("LANDED #%d at %s: merged into %s", f.Number, f.Head[:min(10, len(f.Head))], trunk),
		})
		unposted = append(unposted, err)
	}
	return errors.Join(unposted...)
}

func landedAudience(owner string, route pass.Route) []string {
	if owner == "" {
		owner = rootLane
	}
	to := []string{owner}
	if board := route(pass.BoardDesk); board != rootLane && !slices.Contains(to, board) {
		to = append(to, board)
	}
	return to
}
