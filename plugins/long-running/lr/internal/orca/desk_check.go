package orca

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-inbox/client"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/incident"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/lanes"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/livetasks"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	typeHeartbeat       = "heartbeat"
	typeQuestion        = "question"
	typeEscalation      = "escalation"
	kindBlocked         = "blocked"
	workerStopped       = "Worker stopped"
	finishedStop        = "Worker stopped without reporting"
	turnEndBlocker      = "turn-end:"
	kindHandoff         = "handoff"
	stopFact            = "worker-stop:"
	recordOpen          = "open"
	typeWorkerDone      = "worker_done"
	typeStatus          = "status"
	heartbeatFact       = "lane-heartbeat"
	milestoneFact       = "milestone-statuses"
	registryRequestKind = "decide"
)

const restReadTimeout = 30 * time.Second

var (
	ackSubject        = regexp.MustCompile(`(?s)^(started|done)\b[: ]*(.*)$`)
	runRef            = regexp.MustCompile(`R\d+|\d+`)
	relayRef          = regexp.MustCompile(`(?:^|cci#)(R?\d+)$`)
	milestoneSubjects = []string{"fix-live:", "mechanism:", "ready:"}
	askTypes          = []string{typeQuestion, typeEscalation, typeStatus}
	restingKinds      = []string{kindAsk, kindBlocker, kindBlocked}
)

func (d *Desk) check(ctx context.Context) (bool, error) {
	key, relayedKey, replayedKey := "inbox:"+d.Config.Run, "relayed:"+d.Config.Run, "replayed:"+d.Config.Run
	value, hasCursor, err := d.factOf(deskRunner, key)
	if err != nil {
		return false, err
	}
	cursor := factInt(value)
	relayedValue, _, err := d.factOf(deskRunner, relayedKey)
	if err != nil {
		return false, err
	}
	_, replayed, err := d.factOf(deskRunner, replayedKey)
	if err != nil {
		return false, err
	}
	relayed := stringList(relayedValue)
	var handed struct {
		Messages []orcaMessage `json:"messages"`
		Delivery string        `json:"deliveryId"`
	}
	if delivery := d.call(ctx, orchestration, "check", "--run", d.Config.Run); delivery.OK {
		if err := json.Unmarshal(delivery.Result, &handed); err != nil {
			return false, fmt.Errorf("orca check --run: %w", err)
		}
	}
	unrelayed := map[string]bool{}
	for _, message := range handed.Messages {
		if message.Type != typeHeartbeat && !slices.Contains(relayed, message.ID) {
			unrelayed[message.ID] = true
		}
	}
	messages, ready, err := d.runInbox(ctx, cursor, hasCursor, !replayed, unrelayed, key)
	if err != nil || messages == nil {
		return ready, err
	}
	replays := 0
	for _, message := range messages {
		if replayed || message.Sequence > cursor || unrelayed[message.ID] {
			continue
		}
		dropped, err := d.dropped(message)
		if err != nil {
			return false, err
		}
		if dropped {
			unrelayed[message.ID] = true
			replays++
		}
	}
	var beatAt int64
	for _, message := range slices.Backward(messages) {
		behind := message.Sequence <= cursor
		if behind && !unrelayed[message.ID] {
			continue
		}
		cursor = max(cursor, message.Sequence)
		if message.Type == typeHeartbeat {
			if err := d.beatLane(message); err != nil {
				return false, err
			}
			beatAt = cursor
			continue
		}
		if beatAt != 0 {
			if err := d.setFact(deskRunner, key, beatAt); err != nil {
				return false, err
			}
			beatAt = 0
		}
		if err := d.relayRunMessage(ctx, message, behind, cursor); err != nil {
			return false, err
		}
		if !slices.Contains(relayed, message.ID) {
			relayed = append(relayed, message.ID)
		}
		if err := d.store.Edit(deskRunner, func(tx *incident.Tx) error {
			if err := tx.SetFact(key, cursor); err != nil {
				return err
			}
			return tx.SetFact(relayedKey, relayed)
		}); err != nil {
			return false, err
		}
		delete(unrelayed, message.ID)
	}
	if beatAt != 0 {
		if err := d.setFact(deskRunner, key, beatAt); err != nil {
			return false, err
		}
	}
	if replays > 0 {
		if err := d.escalate(replayedKey, "REPLAYED", deskRunner, fmt.Sprintf("%d asks and reports in Run %s's mail that never reached the bus were relayed again", replays, d.Config.Run)); err != nil {
			return false, err
		}
	}
	if !replayed {
		if err := d.setFact(deskRunner, replayedKey, replays); err != nil {
			return false, err
		}
	}
	if handed.Delivery != "" && len(unrelayed) == 0 {
		d.call(ctx, orchestration, "check", "--run", d.Config.Run, "--ack", handed.Delivery, "--peek")
	}
	return true, nil
}

func (d *Desk) runInbox(ctx context.Context, cursor int64, hasCursor, whole bool, unrelayed map[string]bool, key string) ([]orcaMessage, bool, error) {
	limit := inboxPage
	for {
		reply, err := d.ask(ctx, orchestration, "inbox", "--terminal", "run:"+d.Config.Run, "--limit", fmt.Sprint(limit))
		if err != nil {
			return nil, false, err
		}
		if !reply.OK {
			encoded, err := json.Marshal(reply.Error)
			if err != nil {
				return nil, false, err
			}
			return nil, hasCursor, d.escalate("orca-inbox:"+store.Stamp(d.Now())[:15], "ORCA-INBOX", "runner", string(encoded))
		}
		messages, err := messagesOf(reply)
		if err != nil {
			return nil, false, err
		}
		if !hasCursor {
			var newest int64
			for _, message := range messages {
				newest = max(newest, message.Sequence)
			}
			return nil, true, d.setFact(deskRunner, key, newest)
		}
		if len(messages) < limit || (!whole && messages[len(messages)-1].Sequence <= cursor+1 && holdsAll(messages, unrelayed)) {
			return append([]orcaMessage{}, messages...), true, nil
		}
		limit *= 2
	}
}

func holdsAll(messages []orcaMessage, wanted map[string]bool) bool {
	for id := range wanted {
		if !slices.ContainsFunc(messages, func(message orcaMessage) bool { return message.ID == id }) {
			return false
		}
	}
	return true
}

func (d *Desk) dropped(message orcaMessage) (bool, error) {
	if !slices.Contains(askTypes, message.Type) || (ackSubject.MatchString(message.Subject) && strings.Contains(message.ThreadID, "/")) {
		return false, nil
	}
	held, err := d.known(deskRunner, escalationPref+message.ID)
	if err != nil || held != nil {
		return false, err
	}
	if message.Type != typeStatus {
		return true, nil
	}
	_, err = os.Stat(d.messageFile(message.ID))
	if errors.Is(err, os.ErrNotExist) {
		return true, nil
	}
	return false, err
}

func (d *Desk) relayRunMessage(ctx context.Context, message orcaMessage, behind bool, cursor int64) error {
	lane, err := d.laneOf(message.FromHandle)
	if err != nil {
		return err
	}
	relayed, err := d.message(ctx, message, lane)
	if err != nil || relayed || !behind {
		return err
	}
	if err := d.keep(message); err != nil {
		return err
	}
	return d.escalate(message.ID, "UNRELAYED", lane, fmt.Sprintf("%s from %s at sequence %d, behind cursor %d, has no relay record and its type has no relay: %s", message.Type, message.FromHandle, message.Sequence, cursor, message.Subject))
}

func (d *Desk) beatLane(message orcaMessage) error {
	lane, err := d.laneOf(message.FromHandle)
	if err != nil {
		return err
	}
	container := laneBook(lane)
	value, found, err := d.factOf(container, heartbeatFact)
	if err != nil {
		return err
	}
	exists, err := d.store.Exists(container)
	if err != nil || !exists {
		return err
	}
	held, _ := value.(string)
	if found && message.CreatedAt <= held {
		return nil
	}
	return d.setFact(container, heartbeatFact, message.CreatedAt)
}

func (d *Desk) keep(message orcaMessage) error {
	path := d.messageFile(message.ID)
	if err := os.MkdirAll(filepath.Dir(path), 0o750); err != nil {
		return err
	}
	return os.WriteFile(path, []byte(strings.TrimSpace(message.Subject+"\n\n"+message.Body)+"\n"), 0o600)
}

func (d *Desk) message(ctx context.Context, message orcaMessage, lane string) (bool, error) {
	var payload struct {
		Dispatch string `json:"dispatchId"`
		Outcome  string `json:"outcome"`
	}
	if message.Payload != "" {
		_ = json.Unmarshal([]byte(message.Payload), &payload)
	}
	sender := payload.Dispatch
	if sender == "" && d.terminal(lane) == message.FromHandle {
		sender = d.receipt(lane)
	}
	acked := ackSubject.FindStringSubmatch(message.Subject)
	if acked != nil && strings.Contains(message.ThreadID, "/") {
		container, key, _ := strings.Cut(message.ThreadID, "/")
		result := strings.TrimSpace(strings.TrimLeft(strings.TrimPrefix(acked[2], key), ": "))
		return true, d.acknowledge(ctx, container, key, sender, acked[1], or(result, strings.TrimSpace(message.Body)), message.ID)
	}
	switch {
	case message.Type == typeQuestion || message.Type == typeEscalation:
		return true, d.relayQuestion(ctx, message, lane)
	case message.Type == typeWorkerDone:
		return true, d.relayDone(ctx, message, lane, sender, payload.Outcome)
	case message.Type == typeStatus && slices.ContainsFunc(milestoneSubjects, func(prefix string) bool { return strings.HasPrefix(strings.ToLower(message.Subject), prefix) }):
		return true, d.relayMilestone(ctx, message, lane)
	case message.Type == typeStatus:
		key, err := d.relayNamed(lane, message.Subject+"\n"+message.Body)
		if err != nil {
			return false, err
		}
		if err := d.keep(message); err != nil {
			return false, err
		}
		if key == "" {
			return true, d.relayToDesk(ctx, message, lane, "REPORT")
		}
		verb, result := "done", message.Subject
		if acked != nil {
			verb, result = acked[1], strings.TrimSpace(acked[2])
		}
		return true, d.acknowledgeOnRun(ctx, laneBook(lane), key, sender, verb, or(result, strings.TrimSpace(message.Body)), message.ID)
	case message.Type == "decision_gate" || message.Type == "handoff":
		if err := d.keep(message); err != nil {
			return false, err
		}
		return true, d.escalate(message.ID, strings.ToUpper(message.Type), lane, fmt.Sprintf("%s: %s", message.Subject, clipRunes(message.Body, 200)))
	}
	return false, nil
}

func (d *Desk) relayQuestion(ctx context.Context, message orcaMessage, lane string) error {
	if err := d.keep(message); err != nil {
		return err
	}
	moot, err := d.withdrawnAsk(ctx, lane, message)
	if err != nil || moot {
		return err
	}
	if message.Type == typeEscalation {
		parked, err := d.parkedOn(ctx, lane)
		if err != nil || parked != 0 {
			return err
		}
	}
	if message.Type == typeEscalation && strings.HasPrefix(message.Subject, workerStopped) {
		return d.relayStop(ctx, message, lane)
	}
	return d.relayToDesk(ctx, message, lane, "DECIDE")
}

func (d *Desk) relayStop(ctx context.Context, message orcaMessage, lane string) error {
	open, err := d.stopOpen(ctx, message.FromHandle)
	if err != nil || open {
		return err
	}
	subject := message.Subject
	if subject == finishedStop {
		rest, err := d.restOf(ctx, lane, message.FromHandle)
		if err != nil {
			return err
		}
		if len(rest.Tasks) > 0 {
			return d.record("escalation:resting:"+message.ID, fmt.Sprintf("RESTING %s %s: ended its turn with %s", message.ID, lane, strings.Join(rest.Tasks, "; ")))
		}
		subject += "; nothing armed found, " + rest.Read
	}
	by, err := d.laneBy(lane)
	if err != nil {
		return err
	}
	if by == lanes.OwnerRelay {
		by = coordinatorLane
	}
	if err := d.setFact(deskRunner, stopFact+message.FromHandle, escalationPref+message.ID); err != nil {
		return err
	}
	return d.escalateTo(ctx, message.ID, "DECIDE", lane, fmt.Sprintf("%s: %s", subject, clipRunes(message.Body, 300)), by)
}

func (d *Desk) restOf(ctx context.Context, lane, terminal string) (livetasks.Rest, error) {
	records, err := d.laneRecords(ctx, lane)
	if err != nil {
		return livetasks.Rest{}, err
	}
	records = slices.DeleteFunc(records, func(record client.Record) bool { return string(record.Kind) == kindHandoff })
	if len(records) > 0 {
		newest := records[len(records)-1]
		if slices.Contains(restingKinds, string(newest.Kind)) && newest.Status == recordOpen && !strings.HasPrefix(newest.ID, turnEndBlocker) {
			return livetasks.Rest{Tasks: []string{fmt.Sprintf("its open %s #%d", strings.ToUpper(string(newest.Kind)), newest.Seq)}}, nil
		}
	}
	sprites, err := CCRemote{Runner: d.Runner, State: d.Config.CCRemote}.Sprites()
	if err != nil {
		return livetasks.Rest{}, err
	}
	argv, host := []string{d.tool("lr"), "handoff", "live-tasks", "--terminal", terminal}, "this host"
	for key, sprite := range sprites {
		if key.Terminal == terminal {
			argv, host = spriteExec(sprite, "sh", "-c", `"$HOME/.local/bin/lr" handoff live-tasks --terminal "$1"`, "sh", terminal), "Sprite "+sprite.Host
		}
	}
	result, err := d.Runner.Run(ctx, shell.Command{Argv: argv, Timeout: restReadTimeout})
	var exit *shell.ExitError
	if errors.As(err, &exit) || errors.Is(err, context.DeadlineExceeded) {
		return livetasks.Rest{Read: fmt.Sprintf("lr handoff live-tasks failed on %s: %s", host, describeExit(result.Stderr, "%v", err))}, nil
	}
	if err != nil {
		return livetasks.Rest{}, err
	}
	var rest livetasks.Rest
	if err := json.Unmarshal([]byte(result.Stdout), &rest); err != nil {
		return livetasks.Rest{}, fmt.Errorf("lr handoff live-tasks on %s: %w", host, err)
	}
	rest.Read = "on " + host + ": " + rest.Read
	return rest, nil
}

func (d *Desk) stopOpen(ctx context.Context, terminal string) (bool, error) {
	key, found, err := d.factOf(deskRunner, stopFact+terminal)
	if err != nil || !found {
		return false, err
	}
	action, err := d.known(deskRunner, fmt.Sprint(key))
	if err != nil || action == nil {
		return false, err
	}
	seq := receiptSeq(fmt.Sprint(action.Response["posted"]))
	if seq == 0 {
		return true, nil
	}
	stop, err := d.cciRecord(ctx, seq)
	return stop != nil && stop.Status == recordOpen, err
}

func (d *Desk) parkedOn(ctx context.Context, lane string) (int64, error) {
	row, _, err := d.Registry.Lane(lane)
	if err != nil {
		return 0, err
	}
	records, err := d.Bus.Tail(ctx, client.Query{Drive: d.Config.Drive, Lanes: []string{lane}, Since: row.Answered})
	if err != nil {
		return 0, d.noteTailFailure(&tailFailure{cause: err})
	}
	if len(records) == 0 {
		return 0, nil
	}
	newest := records[len(records)-1]
	if string(newest.Kind) != kindBlocked && string(newest.Kind) != kindAsk || !newest.At.After(row.Answered) {
		return 0, nil
	}
	return newest.Seq, nil
}

func (d *Desk) parkedReply(ctx context.Context, at, lane string, closed int64) (bool, error) {
	parked, err := d.parkedOn(ctx, lane)
	if err != nil || closed >= parked {
		return false, err
	}
	return true, d.record("escalation:parked:"+at, fmt.Sprintf("PARKED %s %s: parked on #%d, so the reply to #%d relays nothing; the lane wakes when what #%d names happens", at, lane, parked, closed, parked))
}

func (d *Desk) relayToDesk(ctx context.Context, message orcaMessage, lane, kind string) error {
	by, err := d.laneBy(lane)
	if err != nil {
		return err
	}
	return d.escalateTo(ctx, message.ID, kind, lane, fmt.Sprintf("%s: %s", message.Subject, clipRunes(message.Body, 300)), by)
}

func (d *Desk) relayDone(ctx context.Context, message orcaMessage, lane, sender, outcome string) error {
	if err := d.keep(message); err != nil {
		return err
	}
	if err := d.escalate(message.ID, "OUTCOME", lane, fmt.Sprintf("worker_done %s dispatch=%s: %s", or(outcome, "?"), sender, message.Subject)); err != nil {
		return err
	}
	if outcome != "succeeded" {
		return nil
	}
	id := "done:" + message.ID
	if _, err := d.post(ctx, deskPost{Kind: registryRequestKind, Text: "registry request: done " + lane + ", its Orca worker finished", To: supervisorLane, Topic: "done:" + lane, Key: id, ID: id, Attrs: map[string]string{"verb": "done", "lane": lane}}); err != nil {
		fmt.Fprintf(d.Stderr, "registry request for %s left unposted: %v\n", lane, err)
	}
	return nil
}

func (d *Desk) relayMilestone(ctx context.Context, message orcaMessage, lane string) error {
	name, _, _ := strings.Cut(message.Subject, ":")
	kind := strings.ToUpper(name)
	if err := d.keep(message); err != nil {
		return err
	}
	if err := d.escalate(message.ID, kind, lane, fmt.Sprintf("%s: %s", message.Subject, clipRunes(collapsed(message.Body), 300))); err != nil {
		return err
	}
	if err := d.stampStatus(lane, message.ID, strings.ToLower(kind)); err != nil {
		return err
	}
	if kind == "MECHANISM" {
		return nil
	}
	return d.unfixed(ctx, lane, message)
}

func (d *Desk) relayNamed(lane, text string) (string, error) {
	actions, err := d.laneActions(laneBook(lane), "", incident.ActionAccepted, incident.ActionStarted)
	if err != nil {
		return "", err
	}
	named := runRef.FindAllString(text, -1)
	for _, action := range actions {
		if action.Kind != kindRelay && action.Kind != kindReply {
			continue
		}
		ref := relayRef.FindStringSubmatch(action.ActionID)
		if ref != nil && slices.Contains(named, ref[1]) {
			return action.ActionID, nil
		}
	}
	return "", nil
}

func (d *Desk) acknowledgeOnRun(ctx context.Context, container, key, sender, verb, text, message string) error {
	receipt := map[string]any{"message": message, "at": d.stamp(), "acknowledged": true}
	sends, err := d.store.Actions(container, kindSend, incident.ActionStarted, incident.ActionUnverifiable)
	if err != nil {
		return err
	}
	for _, send := range sends {
		if string(send.AuthorityRef) != key {
			continue
		}
		if _, err := d.attempt(container, func(tx *incident.Tx) error {
			_, err := tx.Complete(send.ActionID, receipt, "")
			return err
		}); err != nil {
			return err
		}
	}
	return d.acknowledge(ctx, container, key, sender, verb, text, message)
}

func (d *Desk) withdrawnAsk(ctx context.Context, lane string, message orcaMessage) (bool, error) {
	by, err := d.withdrawnBy(lane)
	if err != nil {
		return false, err
	}
	if by == "" && isCaseLane(lane) {
		dispatch, err := d.show(ctx, lane, "")
		if err != nil {
			return false, err
		}
		if dispatch != nil && dispatch.Fenced {
			by = "a stop of dispatch " + dispatch.ID
			container, err := d.lane(lane)
			if err != nil {
				return false, err
			}
			if err := d.setFact(container, withdrawnFact, map[string]any{"dispatch": dispatch.ID, "by": by}); err != nil {
				return false, err
			}
		}
	}
	if by == "" {
		return false, nil
	}
	return true, d.escalate(message.ID, "WITHDRAWN", lane, fmt.Sprintf("withdrawn by %s, so it is not dispatched again and its question needs no answer: %s", by, message.Subject))
}

func (d *Desk) stampStatus(lane, message, name string) error {
	container := laneBook(lane)
	exists, err := d.store.Exists(container)
	if err != nil || !exists {
		return err
	}
	return d.store.Edit(container, func(tx *incident.Tx) error {
		value, _, err := tx.Fact(milestoneFact)
		if err != nil {
			return err
		}
		statuses, isTable := value.(map[string]any)
		if !isTable {
			statuses = map[string]any{}
		}
		if _, held := statuses[message]; held {
			return nil
		}
		statuses[message] = map[string]any{"name": name, "at": d.stamp()}
		return tx.SetFact(milestoneFact, statuses)
	})
}

func (d *Desk) acknowledge(ctx context.Context, container, key, sender, verb, text, message string) error {
	held, err := d.known(container, key)
	if err != nil || held == nil {
		return err
	}
	header, err := d.store.Header(container)
	if err != nil {
		return err
	}
	if sender != "" && sender == string(header.PendingOwner) {
		if err := d.store.Edit(container, func(tx *incident.Tx) error { return tx.TakeOver(sender) }); err != nil {
			return err
		}
	}
	moved := false
	switch verb {
	case "started":
		moved, err = d.asOwner(container, sender, func(tx *incident.Tx) error {
			_, err := tx.StartBy(key, d.Now(), zeroTime, sender)
			return err
		})
	case "done":
		response := map[string]any{"text": clipRunes(text, 500), "message": message, "at": d.stamp()}
		moved, err = d.asOwner(container, sender, func(tx *incident.Tx) error { return finish(tx, key, response, sender, d.Now()) })
	}
	if err != nil {
		return err
	}
	if moved || sender == "" {
		return nil
	}
	current, err := d.store.Header(container)
	if err != nil {
		return err
	}
	if sender == current.Owner {
		return nil
	}
	return d.standDown(ctx, container, key, sender, message, current)
}

func (d *Desk) standDown(ctx context.Context, container, key, sender, message string, header incident.Header) error {
	notice := "stand-down:" + key + ":" + sender
	text := fmt.Sprintf("Do not execute %s: dispatch %s owns %s at generation %d.", key, header.Owner, container, header.OwnerGeneration)
	target, err := compactJSON(relayTarget{Text: text, ReplyTo: message})
	if err != nil {
		return err
	}
	action, created, err := d.accept(container, notice, kindReply, target, notice, zeroTime)
	if err != nil || !created {
		return err
	}
	lane := strings.TrimPrefix(container, lanePrefix)
	dispatch, err := d.show(ctx, lane, "")
	if err != nil || dispatch == nil {
		return err
	}
	return d.send(ctx, container, &laneDispatch{Lane: dispatch.Lane, ID: sender, Terminal: dispatch.Terminal, Status: dispatch.Status, Live: dispatch.Live}, action)
}
