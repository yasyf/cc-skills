package orca

import (
	"context"
	"fmt"
	"slices"
	"strings"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
)

const (
	spawnedComms = "-comms"
	noIncident   = "no incident"
)

var incidentRoles = []string{spawnedFix, spawnedEvidence, spawnedComms}

type ruling struct {
	seq   int64
	name  string
	slugs []string
	told  []string
}

func incidentOf(lane string) string {
	base := firstLane(lane)
	for _, role := range incidentRoles {
		if slug, found := strings.CutSuffix(base, role); found && slug != "" {
			return slug
		}
	}
	return ""
}

func directiveLane(record client.Record) string {
	parsed := parseDirective(strings.TrimSpace(record.Text))
	if parsed == nil {
		return ""
	}
	switch parsed.Verb {
	case verbLaunch:
		if spec := launchSpec.FindStringSubmatch(parsed.Rest); spec != nil {
			return spec[1]
		}
	case verbIncident:
		if spec := incidentSpec.FindStringSubmatch(parsed.Rest); spec != nil {
			return strings.TrimSuffix(spec[1], spawnedFix) + spawnedFix
		}
	}
	return ""
}

func (d *Desk) rulingsIn(ctx context.Context, records []client.Record) ([]ruling, error) {
	var found []ruling
	for _, record := range records {
		ruled, err := d.ruledOut(ctx, record)
		if err != nil {
			return nil, err
		}
		if len(ruled.slugs) > 0 {
			found = append(found, ruled)
		}
	}
	return found, nil
}

func (d *Desk) ruledOut(ctx context.Context, record client.Record) (ruling, error) {
	ruled := ruling{seq: record.Seq, name: record.Name()}
	if record.Lane != coordinatorLane {
		return ruled, nil
	}
	var named []string
	switch kind := string(record.Kind); {
	case kind == kindStandDown || kind == kindStopped || kind == kindNotOurs:
		named, ruled.told = record.To, record.To
	case kind == verbWithdraw && record.Re != 0:
		closed, err := d.cciRecord(ctx, record.Re)
		if err != nil || closed == nil {
			return ruled, err
		}
		named = []string{directiveLane(*closed)}
		if parsed := parseDirective(strings.TrimSpace(closed.Text)); parsed != nil && parsed.Verb == verbLaunch {
			ruled.told = named
		}
	case strings.HasPrefix(strings.ToLower(strings.TrimSpace(record.Text)), noIncident):
		named = record.Refs.Lanes
		if record.Re != 0 {
			closed, err := d.cciRecord(ctx, record.Re)
			if err != nil {
				return ruled, err
			}
			if closed != nil {
				named = slices.Concat(named, closed.Refs.Lanes, []string{directiveLane(*closed)})
			}
		}
	}
	slugs := map[string]bool{}
	for _, name := range named {
		if slug := incidentOf(name); slug != "" {
			slugs[slug] = true
		}
	}
	ruled.slugs = sortedKeys(slugs)
	return ruled, nil
}

func (d *Desk) incidentBooks(slug string) ([]string, error) {
	books, err := d.containers(lanePrefix + slug + "-")
	if err != nil {
		return nil, err
	}
	return slices.DeleteFunc(books, func(book string) bool { return incidentOf(strings.TrimPrefix(book, lanePrefix)) != slug }), nil
}

func (d *Desk) withdrawIncident(ctx context.Context, ruled ruling, slug string) error {
	books, err := d.incidentBooks(slug)
	if err != nil {
		return err
	}
	key := fmt.Sprintf("root's cci %s ruling incident %s out", ruled.name, slug)
	log := fmt.Sprintf("escalation:withdraw:incident:%s:cci#%d", slug, ruled.seq)
	for _, book := range books {
		lane := strings.TrimPrefix(book, lanePrefix)
		if lanes.Addresses(ruled.told, lane) {
			continue
		}
		held, err := d.launchesUnder(lane, false)
		if err != nil {
			return err
		}
		for _, entry := range held {
			if err := d.withdrawLane(ctx, log, key, lane, false, entry); err != nil {
				return err
			}
		}
	}
	return nil
}

func (d *Desk) ruledAfter(lane string, seq int64) string {
	slug := incidentOf(lane)
	for _, ruled := range d.rulings {
		if ruled.seq > seq && slices.Contains(ruled.slugs, slug) && !lanes.Addresses(ruled.told, lane) {
			return fmt.Sprintf("root ruled incident %s out on cci %s", slug, ruled.name)
		}
	}
	return ""
}
