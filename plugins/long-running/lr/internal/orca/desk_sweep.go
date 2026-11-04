package orca

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/incident"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	staleMail      = 10 * time.Minute
	reclaimNamed   = 10
	heartbeatGrace = 30 * time.Minute
	kindWake       = "wake"
	kindReclaim    = "reclaim"
	kindEnqueue    = "enqueue"
)

var (
	zeroTime    time.Time
	redContract = regexp.MustCompile(`(?i)red[- ]by[- ]design|\bfail(?:s|ed|ing)? (?:in )?(?:run|build)\b|\bfailing (?:ci )?runs?\b`)
)

func (d *Desk) sweep(ctx context.Context) error {
	rows, err := d.workers(ctx)
	if err != nil {
		return err
	}
	byDispatch := map[string]deskWorker{}
	for _, row := range rows {
		byDispatch[row.Dispatch] = row
	}
	names, err := d.laneNames()
	if err != nil {
		return err
	}
	receipts, statuses := map[string]string{}, map[string]string{}
	for _, lane := range names {
		dispatch := d.receipt(lane)
		receipts[lane] = dispatch
		if row, found := byDispatch[dispatch]; found {
			statuses[lane] = row.Status
		}
	}
	stale, err := d.staleMail(ctx, receipts, statuses)
	if err != nil {
		return err
	}
	for _, item := range stale {
		if err := d.wakeStale(ctx, item, statuses); err != nil {
			return err
		}
	}
	if err := d.reclaimSettled(names, receipts, statuses, byDispatch); err != nil {
		return err
	}
	return d.promptWatchDown(ctx)
}

type staleItem struct{ lane, message string }

func (d *Desk) wakeStale(ctx context.Context, item staleItem, statuses map[string]string) error {
	if status := statuses[item.lane]; inactive(status) {
		return d.escalate("stale:"+item.message, "STALE-MAIL", item.lane, item.message+" unread by a "+status+" dispatch")
	}
	environment, err := d.environmentOf(ctx, item.lane)
	if err != nil {
		return err
	}
	return d.wakeOnce(ctx, item.message, item.lane, d.terminal(item.lane), environment, "unread Orca message "+item.message+"; read it now")
}

func (d *Desk) wakeOnce(ctx context.Context, message, lane, terminal, environment, text string) error {
	held, err := d.known(deskRunner, "wake:"+message)
	if err != nil || held != nil {
		return err
	}
	woke, err := d.wake(ctx, terminal, environment, text)
	if err != nil || !woke {
		return err
	}
	_, _, err = d.accept(deskRunner, "wake:"+message, kindWake, message, lane, zeroTime)
	return err
}

func (d *Desk) reclaimSettled(names []string, receipts, statuses map[string]string, rows map[string]deskWorker) error {
	named, err := d.store.Actions(deskRunner, kindReclaim)
	if err != nil {
		return err
	}
	var settled []laneDispatch
	for _, lane := range names {
		dispatch := receipts[lane]
		if !inactive(statuses[lane]) || slices.ContainsFunc(named, func(action incident.Action) bool { return action.ActionID == "reclaim:"+dispatch }) {
			continue
		}
		settled = append(settled, laneDispatch{Lane: lane, ID: dispatch, Terminal: or(rows[dispatch].Terminal, d.terminal(lane)), Status: statuses[lane]})
	}
	if len(settled) == 0 {
		return nil
	}
	return d.reclaim(settled)
}

func (d *Desk) promptWatchDown(ctx context.Context) error {
	said, code, err := d.capture(ctx, nil, d.tool("lr"), "watch", "prompts", "show", "--drive", d.Config.Drive, "--json")
	if err != nil || code != 0 {
		return err
	}
	var watch struct {
		Rows []struct {
			State  string `json:"state"`
			Detail string `json:"detail"`
		} `json:"rows"`
	}
	if json.Unmarshal([]byte(said), &watch) != nil {
		return nil
	}
	hour := store.Stamp(d.Now())[:13]
	for _, row := range watch.Rows {
		if row.State != "down" {
			continue
		}
		if err := d.escalate("prompt-watch:"+hour, "PROMPT-WATCH-DOWN", "runner", row.Detail); err != nil {
			return err
		}
	}
	return nil
}

type unreadEntry struct {
	Terminal string `json:"terminal"`
	At       string `json:"at"`
}

type mailState struct {
	Cursor int64                  `json:"cursor"`
	Page   int                    `json:"page"`
	Unread map[string]unreadEntry `json:"unread"`
}

func (d *Desk) staleMail(ctx context.Context, receipts, statuses map[string]string) ([]staleItem, error) {
	fact := "mail:" + d.Config.Run
	value, found, err := d.factOf(deskRunner, fact)
	if err != nil {
		return nil, err
	}
	if !found {
		newest, err := d.mail(ctx, 1)
		if err != nil {
			return nil, err
		}
		var cursor int64
		for _, message := range newest {
			cursor = max(cursor, message.Sequence)
		}
		return nil, d.setFact(deskRunner, fact, mailState{Cursor: cursor, Page: inboxPage, Unread: map[string]unreadEntry{}})
	}
	var kept mailState
	raw, err := json.Marshal(value)
	if err != nil {
		return nil, err
	}
	if err := json.Unmarshal(raw, &kept); err != nil {
		return nil, err
	}
	if kept.Unread == nil {
		kept.Unread = map[string]unreadEntry{}
	}
	terminals := map[string]string{}
	for lane := range receipts {
		terminals[d.terminal(lane)] = lane
	}
	fresh, err := d.mailSince(ctx, kept.Cursor, kept.Page)
	if err != nil {
		return nil, err
	}
	for _, message := range fresh {
		if _, onLane := terminals[message.ToHandle]; onLane && message.Read == 0 {
			kept.Unread[message.ID] = unreadEntry{Terminal: message.ToHandle, At: message.CreatedAt}
		}
		kept.Cursor = max(kept.Cursor, message.Sequence)
	}
	kept.Page = max(inboxPage, 2*len(fresh))
	stale, err := d.dueMail(ctx, &kept, terminals, statuses)
	if err != nil {
		return nil, err
	}
	return stale, d.setFact(deskRunner, fact, kept)
}

func (d *Desk) dueMail(ctx context.Context, kept *mailState, terminals, statuses map[string]string) ([]staleItem, error) {
	due := map[string]bool{}
	for _, entry := range kept.Unread {
		lane, onLane := terminals[entry.Terminal]
		at, err := store.Parse(entry.At)
		if err != nil {
			return nil, err
		}
		if !onLane || d.Now().Sub(at) >= staleMail || inactive(statuses[lane]) {
			due[entry.Terminal] = true
		}
	}
	var stale []staleItem
	for _, terminal := range sortedKeys(due) {
		lane, onLane := terminals[terminal]
		held := map[string]bool{}
		if onLane {
			environment, err := d.environmentOf(ctx, lane)
			if err != nil {
				return nil, err
			}
			var read bool
			if held, read = d.unreadOn(ctx, terminal, environment); !read {
				continue
			}
		}
		settled := inactive(statuses[lane])
		ids := make([]string, 0, len(kept.Unread))
		for id, entry := range kept.Unread {
			if entry.Terminal == terminal {
				ids = append(ids, id)
			}
		}
		slices.Sort(ids)
		for _, id := range ids {
			if held[id] {
				stale = append(stale, staleItem{lane: lane, message: id})
			}
			if !held[id] || settled {
				delete(kept.Unread, id)
			}
		}
	}
	return stale, nil
}

func (d *Desk) reclaim(settled []laneDispatch) error {
	shown := settled[:min(len(settled), reclaimNamed)]
	named := make([]string, 0, len(shown))
	for _, dispatch := range shown {
		named = append(named, dispatch.Lane+"="+dispatch.ID+":"+dispatch.Terminal)
	}
	more, scope := "", ""
	if len(settled) > reclaimNamed {
		more = fmt.Sprintf(" and %d more", len(settled)-reclaimNamed)
	} else {
		for _, dispatch := range settled {
			scope += " --dispatch " + dispatch.ID
		}
	}
	worktrees := "close each other idle terminal and remove each finished worktree under R195"
	if d.Config.GC != "" {
		worktrees = d.Config.GC + " --run " + d.Config.Run + scope
	}
	if err := d.escalate("reclaim:"+settled[0].ID, "RECLAIM", "runner", fmt.Sprintf("%d settled dispatch(es) still hold their terminal: %s%s; %s", len(settled), strings.Join(named, " "), more, worktrees)); err != nil {
		return err
	}
	for _, dispatch := range settled {
		if _, _, err := d.accept(deskRunner, "reclaim:"+dispatch.ID, kindReclaim, dispatch.Terminal, dispatch.Lane, zeroTime); err != nil {
			return err
		}
	}
	return nil
}

func (d *Desk) deadlines(ctx context.Context) error {
	books, err := d.containers(lanePrefix)
	if err != nil {
		return err
	}
	for _, container := range books {
		if err := d.overdue(ctx, container, strings.TrimPrefix(container, lanePrefix)); err != nil {
			return err
		}
	}
	return nil
}

func (d *Desk) overdue(ctx context.Context, container, about string) error {
	moment := d.Now()
	pending, err := d.store.Actions(container, "", incident.ActionAccepted, incident.ActionStarted)
	if err != nil {
		return err
	}
	for _, action := range pending {
		if !slices.Contains([]string{kindRelay, kindReply, kindLaunch, kindEnqueue}, action.Kind) {
			continue
		}
		late, err := action.Overdue(moment)
		if err != nil {
			return err
		}
		if !late {
			continue
		}
		unread, err := d.reportUnread(ctx, container, about, action)
		if err != nil {
			return err
		}
		if unread {
			continue
		}
		excused, err := d.deadlineExcused(ctx, container, about, action, moment)
		if err != nil {
			return err
		}
		if excused {
			continue
		}
		missing, err := d.missingStep(container, action)
		if err != nil {
			return err
		}
		accepted, err := store.Parse(action.AcceptedAt)
		if err != nil {
			return err
		}
		if err := d.escalate(fmt.Sprintf("deadline:%s/%s:%s", container, action.ActionID, action.Status), "DEADLINE", about, fmt.Sprintf("%s %s: %s; accepted %d min ago", action.AuthorityRef, action.Kind, missing, int(d.Now().Sub(accepted).Minutes()))); err != nil {
			return err
		}
	}
	return nil
}

func (d *Desk) reportUnread(ctx context.Context, container, about string, action incident.Action) (bool, error) {
	dispatch, sent, err := d.sentTo(ctx, container, about, action)
	if err != nil || !sent {
		return false, err
	}
	rows, read := d.peek(ctx, dispatch.Terminal, dispatch.Environment)
	if !read {
		return false, nil
	}
	report, err := notDelivered(rows, container, action, dispatch.ID, d.Now())
	if err != nil || report == "" {
		return false, err
	}
	spec, err := relayOf(action)
	if err != nil {
		return false, err
	}
	thread := threadOf(container, action.ActionID)
	if err := d.wakeOnce(ctx, thread, about, dispatch.Terminal, dispatch.Environment, fmt.Sprintf("%s: read Orca message on thread %s now", action.AuthorityRef, thread)); err != nil {
		return false, err
	}
	return true, d.escalateTo(ctx, fmt.Sprintf("undelivered:%s/%s", container, action.ActionID), "NOT-DELIVERED", about, report, verdictTo(spec.From))
}

func (d *Desk) sentTo(ctx context.Context, container, about string, action incident.Action) (*laneDispatch, bool, error) {
	if action.Kind != kindRelay || action.Status != incident.ActionAccepted {
		return nil, false, nil
	}
	delivered, err := d.delivered(container, action)
	if err != nil || !delivered {
		return nil, false, err
	}
	dispatch, err := d.show(ctx, about, "")
	if err != nil || !dispatch.active() {
		return nil, false, err
	}
	return dispatch, true, nil
}

func notDelivered(rows []orcaMessage, container string, action incident.Action, dispatch string, now time.Time) (string, error) {
	for _, row := range rows {
		found, err := delivers(row, container, action)
		if err != nil {
			return "", err
		}
		if !found {
			continue
		}
		accepted, err := store.Parse(action.AcceptedAt)
		if err != nil {
			return "", err
		}
		return fmt.Sprintf("%s relay not delivered: %s is still unread in the mailbox of %s; accepted %d min ago", action.AuthorityRef, row.ID, dispatch, int(now.Sub(accepted).Minutes())), nil
	}
	return "", nil
}

func (d *Desk) deadlineExcused(ctx context.Context, container, about string, action incident.Action, moment time.Time) (bool, error) {
	if action.Kind == kindRelay || action.Kind == kindReply {
		if action.Status == incident.ActionStarted {
			return true, nil
		}
		beating, err := d.beating(container, action, moment)
		if err != nil || beating {
			return beating, err
		}
	}
	moved, err := d.laneMoved(ctx, about, action)
	if err != nil {
		return false, err
	}
	if moved {
		return true, nil
	}
	return d.relayedRed(ctx, about, action)
}

func (d *Desk) missingStep(container string, action incident.Action) (string, error) {
	sends, err := d.store.Actions(container, kindSend)
	if err != nil {
		return "", err
	}
	sends = slices.DeleteFunc(sends, func(send incident.Action) bool { return string(send.AuthorityRef) != action.ActionID })
	switch action.Kind {
	case kindRelay, kindReply:
		if len(sends) == 0 {
			return "never delivered: no live dispatch", nil
		}
		last := sends[len(sends)-1]
		if action.Kind == kindRelay && last.Status == incident.ActionCompleted {
			return "sent to " + last.Target + ", no started reply", nil
		}
		return "send " + string(last.Status), nil
	case kindLaunch:
		if action.Status == incident.ActionStarted {
			return "launch never finished", nil
		}
		return "launch never started", nil
	}
	if action.Status == incident.ActionStarted {
		return "the enqueue, or the direct merge with its wait on checks, never returned", nil
	}
	return "enqueue never ran", nil
}

func (d *Desk) beating(container string, action incident.Action, moment time.Time) (bool, error) {
	value, found, err := d.factOf(container, heartbeatFact)
	if err != nil || !found {
		return false, err
	}
	stamp, _ := value.(string)
	beat, err := store.Parse(stamp)
	if err != nil {
		return false, err
	}
	accepted, err := store.Parse(action.AcceptedAt)
	if err != nil {
		return false, err
	}
	return beat.After(accepted) || (accepted.Sub(beat) < heartbeatGrace && moment.Sub(accepted) < heartbeatGrace), nil
}

func (d *Desk) laneRecords(ctx context.Context, lane string, kinds ...string) ([]client.Record, error) {
	return d.laneRecordsSince(ctx, lane, time.Time{}, kinds...)
}

func (d *Desk) laneRecordsSince(ctx context.Context, lane string, since time.Time, kinds ...string) ([]client.Record, error) {
	names, err := d.names(lane)
	if err != nil {
		return nil, err
	}
	return d.passTail(ctx, client.Query{Lanes: names, Kinds: kinds, Since: since})
}

func prNamed(record client.Record, pr string) bool {
	for _, ref := range record.Refs.PRs {
		if refNames(ref.String(), pr) {
			return true
		}
	}
	return false
}

func refNames(ref, pr string) bool {
	if strings.Contains(pr, "#") {
		return ref == pr
	}
	return ref == pr || strings.HasSuffix(ref, "#"+pr)
}

func (d *Desk) laneMoved(ctx context.Context, lane string, action incident.Action) (bool, error) {
	relayed := relayedPR.FindStringSubmatch(action.ActionID)
	if action.Kind != kindRelay || relayed == nil {
		return false, nil
	}
	since, err := store.Parse(action.AcceptedAt)
	if err != nil {
		return false, err
	}
	posted, err := d.laneRecords(ctx, lane, "head", "review")
	if err != nil {
		return true, d.noteTailFailure(err)
	}
	return slices.ContainsFunc(posted, func(record client.Record) bool { return prNamed(record, relayed[1]) && !record.At.Before(since) }), nil
}

func (d *Desk) relayedRed(ctx context.Context, lane string, action incident.Action) (bool, error) {
	relayed := relayedPR.FindStringSubmatch(action.ActionID)
	if action.Kind != kindRelay || relayed == nil {
		return false, nil
	}
	posted, err := d.laneRecords(ctx, lane, "contract")
	if err != nil {
		return true, d.noteTailFailure(err)
	}
	var newest *client.Record
	for i := range posted {
		if prNamed(posted[i], relayed[1]) && (newest == nil || posted[i].Seq > newest.Seq) {
			newest = &posted[i]
		}
	}
	return newest != nil && redContract.MatchString(newest.Text), nil
}
