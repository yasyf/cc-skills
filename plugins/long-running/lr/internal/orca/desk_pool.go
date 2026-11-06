package orca

import (
	"context"
	"fmt"
	"regexp"
	"slices"
	"strconv"
	"strings"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/incident"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	kindAsk    = "ask"
	poolPrefix = "pool-"
	poolTopic  = "pool:"
	poolNoneID = "pool-none:"
)

var namedPR = regexp.MustCompile(`#(\d+)\b`)

func (d *Desk) pooled(ctx context.Context, key, lane, text, sender string) (string, error) {
	row, found, err := d.Registry.Lane(lane)
	if err != nil || !found || !strings.HasPrefix(row.Role, poolPrefix) {
		return lane, err
	}
	busyWith, err := d.startedRelay(lane, key)
	if err != nil || busyWith == "" || namesOwnPR(text, row.PRs) {
		return lane, err
	}
	dispatch, err := d.show(ctx, lane, "")
	if err != nil {
		return "", err
	}
	if dispatch.active() {
		questions, err := d.openQuestions(ctx, dispatch)
		if err != nil {
			return "", err
		}
		reply, err := d.unclaimed(key, lane, questions)
		if err != nil || reply != "" {
			return lane, err
		}
	}
	free, err := d.freeLane(ctx, row)
	if err != nil {
		return "", err
	}
	if free == "" {
		return "", d.askSpawn(ctx, key, lane, busyWith, row.Role, text, sender)
	}
	return free, d.record(escalationPref+"routed:"+key+":"+lane, fmt.Sprintf("ROUTED %s %s -> %s: %s is busy with %s", key, lane, free, lane, busyWith))
}

func (d *Desk) startedRelay(lane, except string) (string, error) {
	started, err := d.laneActions(laneBook(lane), kindRelay, incident.ActionStarted)
	if err != nil {
		return "", err
	}
	for _, action := range started {
		if action.ActionID != except {
			return action.ActionID, nil
		}
	}
	return "", nil
}

func namesOwnPR(text string, prs []int) bool {
	for _, match := range namedPR.FindAllStringSubmatch(text, -1) {
		if number, err := strconv.Atoi(match[1]); err == nil && slices.Contains(prs, number) {
			return true
		}
	}
	return false
}

func (d *Desk) freeLane(ctx context.Context, busy store.Lane) (string, error) {
	desired, err := d.Registry.Lanes(store.LaneDesired)
	if err != nil {
		return "", err
	}
	items, err := d.Registry.OpenItems("", 0)
	if err != nil {
		return "", err
	}
	running, err := d.openDispatches(ctx)
	if err != nil {
		return "", err
	}
	slices.SortFunc(desired, func(a, b store.Lane) int { return b.Heard.Compare(a.Heard) })
	for _, lane := range desired {
		if lane.Name == busy.Name || lane.Role != busy.Role || lane.Waiting != 0 || !running[d.receipt(lane.Name)] || holdsItem(lane, items) {
			continue
		}
		started, err := d.startedRelay(lane.Name, "")
		if err != nil {
			return "", err
		}
		if started == "" {
			return lane.Name, nil
		}
	}
	return "", nil
}

func holdsItem(lane store.Lane, items []store.Item) bool {
	return slices.ContainsFunc(items, func(item store.Item) bool {
		return item.Worker == lane.Name || slices.Contains(lane.PRs, item.Number)
	})
}

func (d *Desk) askSpawn(ctx context.Context, key, lane, busyWith, role, text, sender string) error {
	asked, err := d.post(ctx, deskPost{
		Kind: kindAsk, To: coordinatorLane, Topic: poolTopic + key, Key: poolTopic + key, ID: poolTopic + key, Message: text,
		Text: fmt.Sprintf("spawn %s lane for %s: %s is busy with %s and no %s lane is free; the order below is the brief body", role, key, lane, busyWith, role),
	})
	if err != nil {
		return err
	}
	_, err = d.post(ctx, deskPost{
		Kind: kindReport, To: sender, Topic: poolTopic + key, Key: poolNoneID + key, ID: poolNoneID + key,
		Text: fmt.Sprintf("POOL-NONE %s %s: busy with %s and no %s lane is free, so nothing was delivered; %s asks root to spawn one", key, lane, busyWith, role, asked.Name()),
	})
	return err
}
