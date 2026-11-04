package orca

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/incident"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
)

const (
	resendMark   = ":resend"
	sendAttempts = 3
	resendLimit  = 2
	resendAfter  = 30 * time.Second
	orphanedSend = 2 * time.Minute
	unfixedKey   = "unfixed:"
	cutLaunchKey = "cut:"
)

var relayedPR = regexp.MustCompile(`^(?:blocker|restack):(\d+):`)

func threadOf(container, key string) string {
	return container + "/" + key
}

func (d *Desk) deliver(ctx context.Context) error {
	d.binding = nil
	books, err := d.containers(lanePrefix)
	if err != nil {
		return err
	}
	for _, container := range books {
		lane := strings.TrimPrefix(container, lanePrefix)
		if err := d.transfer(lane); err != nil {
			return err
		}
		pending, err := d.store.Actions(container, "", incident.ActionAccepted)
		if err != nil {
			return err
		}
		var relays []incident.Action
		for _, action := range pending {
			switch action.Kind {
			case kindRelay, kindReply:
				relays = append(relays, action)
			case kindLaunch:
				if err := d.deliverLaunch(ctx, container, lane, action); err != nil {
					return err
				}
			}
		}
		owed, err := d.owedRelays(container, lane, relays)
		if err != nil {
			return err
		}
		if len(owed) > 0 {
			if err := d.deliverRelays(ctx, container, lane, owed); err != nil {
				return err
			}
		}
		if err := d.reconcileSends(ctx, container, lane); err != nil {
			return err
		}
	}
	return nil
}

func (d *Desk) deliverLaunch(ctx context.Context, container, lane string, action incident.Action) error {
	for _, gate := range []func(context.Context, string, string, incident.Action) (bool, error){d.holdsCaseLaunch, d.holdsOwnedLaunch, d.holdsCutLaunch} {
		held, err := gate(ctx, container, lane, action)
		if err != nil {
			return err
		}
		if held {
			return nil
		}
	}
	return d.launch(ctx, container, lane, action)
}

func (d *Desk) transfer(lane string) error {
	container, err := d.lane(lane)
	if err != nil {
		return err
	}
	header, err := d.store.PassHeader(container)
	if err != nil {
		return err
	}
	dispatch := d.receipt(lane)
	if dispatch == "" || dispatch == header.Owner || dispatch == string(header.PendingOwner) {
		return nil
	}
	_, err = refusedChange(d.store.Owned(container, header.OwnerGeneration, func(tx *incident.Tx) error {
		tx.Offer(dispatch)
		return nil
	}))
	return err
}

func (d *Desk) owedRelays(container, lane string, relays []incident.Action) ([]incident.Action, error) {
	if len(relays) == 0 {
		return nil, nil
	}
	sends, err := d.store.Actions(container, kindSend, incident.ActionCompleted)
	if err != nil {
		return nil, err
	}
	dispatch := d.receipt(lane)
	delivered := map[string]bool{}
	for _, send := range sends {
		if dispatch == "" || send.Target == dispatch {
			delivered[string(send.AuthorityRef)] = true
		}
	}
	return slices.DeleteFunc(relays, func(action incident.Action) bool { return delivered[action.ActionID] }), nil
}

func (d *Desk) deliverRelays(ctx context.Context, container, lane string, relays []incident.Action) error {
	stoodDown, err := d.laneStoodDown(ctx, lane)
	var failure *tailFailure
	if errors.As(err, &failure) {
		return d.noteTailFailure(err)
	}
	if err != nil {
		return err
	}
	dispatch, err := d.relayable(ctx, lane)
	if err != nil {
		return err
	}
	if dispatch != nil && stoodDown == "" && dispatch.Environment != "" {
		return d.postRelaysToSprite(ctx, container, dispatch, relays)
	}
	if dispatch != nil {
		if err := d.sendRelays(ctx, container, dispatch, relays); err != nil {
			return err
		}
		if stoodDown != "" {
			for _, action := range relays {
				if err := d.record("escalation:relay:"+container+"/"+action.ActionID+":stood-down", fmt.Sprintf("RELAYED-STOOD-DOWN %s %s: stood down by %s, but its dispatch %s still runs, so the relay went to it; send it to the lane that took over too", action.ActionID, lane, stoodDown, dispatch.ID)); err != nil {
					return err
				}
			}
		}
		refused, err := d.sendRefused(lane, dispatch)
		if err != nil || !refused {
			return err
		}
	}
	if stoodDown == "" {
		launches, err := d.store.Actions(container, kindLaunch, incident.ActionAccepted, incident.ActionStarted)
		if err != nil {
			return err
		}
		if len(launches) > 0 {
			return nil
		}
	}
	reason := "stood down by " + stoodDown
	if stoodDown == "" {
		if reason, err = d.noLiveDispatch(ctx, lane); err != nil {
			return err
		}
	}
	for _, action := range relays {
		delivered, err := d.delivered(container, action)
		if err != nil {
			return err
		}
		if delivered {
			continue
		}
		if stoodDown != "" {
			if _, err := d.attempt(container, func(tx *incident.Tx) error {
				_, err := tx.Fail(action.ActionID, reason)
				return err
			}); err != nil {
				return err
			}
			if err := d.record("escalation:relay:"+container+"/"+action.ActionID, fmt.Sprintf("RELAY-FAILED %s %s: %s, so nothing was delivered; send it to the lane that took over", action.ActionID, lane, reason)); err != nil {
				return err
			}
			continue
		}
		if err := d.postRelay(ctx, container, lane, action, reason); err != nil {
			return err
		}
	}
	return nil
}

func (d *Desk) postRelaysToSprite(ctx context.Context, container string, dispatch *laneDispatch, relays []incident.Action) error {
	for _, action := range relays {
		if err := d.postRelay(ctx, container, dispatch.Lane, action, "a Sprite lane does not read its Run mailbox"); err != nil {
			return err
		}
		if _, err := d.wake(ctx, dispatch.Terminal, dispatch.Environment, fmt.Sprintf("%s: read your cci records now", action.AuthorityRef)); err != nil {
			return err
		}
	}
	return nil
}

func (d *Desk) sendRelays(ctx context.Context, container string, dispatch *laneDispatch, relays []incident.Action) error {
	for _, action := range relays {
		unread, err := d.unreadRelay(container, action)
		if err != nil {
			return err
		}
		if unread == "" {
			if err := d.send(ctx, container, dispatch, action); err != nil {
				return err
			}
			continue
		}
		response := map[string]any{"text": "folded into " + unread + ", which the lane has not answered yet", "at": d.stamp()}
		if _, err := d.attempt(container, func(tx *incident.Tx) error { return finish(tx, action.ActionID, response, dispatch.ID, d.Now()) }); err != nil {
			return err
		}
	}
	return nil
}

func finish(tx *incident.Tx, key string, response map[string]any, dispatch string, at time.Time) error {
	action, err := tx.Action(key)
	if err != nil {
		return err
	}
	if action.Status == incident.ActionAccepted {
		if _, err := tx.StartBy(key, at, time.Time{}, dispatch); err != nil {
			return err
		}
	}
	_, err = tx.Complete(key, response, dispatch)
	return err
}

func (d *Desk) relayable(ctx context.Context, lane string) (*laneDispatch, error) {
	dispatch, err := d.show(ctx, lane, "")
	if err != nil || !dispatch.active() {
		return nil, err
	}
	refused, err := d.sendRefused(lane, dispatch)
	if err != nil || refused {
		return nil, err
	}
	return dispatch, nil
}

func (d *Desk) sendRefused(lane string, dispatch *laneDispatch) (bool, error) {
	sends, err := d.laneActions(laneBook(lane), kindSend, incident.ActionFailed)
	if err != nil {
		return false, err
	}
	return slices.ContainsFunc(sends, func(send incident.Action) bool {
		return send.Target == dispatch.ID && strings.Contains(string(send.Reason), CodeFenced)
	}), nil
}

func (d *Desk) noLiveDispatch(ctx context.Context, lane string) (string, error) {
	dispatch, err := d.show(ctx, lane, "")
	if err != nil {
		return "", err
	}
	if dispatch.active() {
		return fmt.Sprintf("no live dispatch (%s is not active)", dispatch.ID), nil
	}
	if dispatch != nil {
		return fmt.Sprintf("no live dispatch (%s is %s)", dispatch.ID, dispatch.Status), nil
	}
	return "no live dispatch", nil
}

func (d *Desk) postRelay(ctx context.Context, container, lane string, action incident.Action, reason string) error {
	spec, err := relayOf(action)
	if err != nil {
		return err
	}
	kind, topic := kindNote, action.ActionID
	if spec.PR != "" {
		kind, topic = kindBlocker, spec.PR
	}
	posted, err := d.post(ctx, deskPost{Kind: kind, Text: spec.Text, To: lane, Topic: topic, Key: "relay:" + container + "/" + action.ActionID, PR: spec.PR})
	if err != nil {
		fmt.Fprintf(d.Stderr, "relay %s to %s left for the next pass; cci post: %v\n", action.ActionID, lane, err)
		return nil
	}
	if _, err := d.attempt(container, func(tx *incident.Tx) error {
		if _, err := tx.StartBy(action.ActionID, d.Now(), time.Time{}, ""); err != nil {
			return err
		}
		_, err := tx.Complete(action.ActionID, map[string]any{"posted": seqReceipt(posted.Seq), "at": d.stamp()}, "")
		return err
	}); err != nil {
		return err
	}
	if spec.PR != "" {
		return nil
	}
	return d.record("escalation:relay:"+container+"/"+action.ActionID, fmt.Sprintf("RELAYED %s %s: %s, so it went to the lane on cci as #%d", action.ActionID, lane, reason, posted.Seq))
}

func (d *Desk) unreadRelay(container string, action incident.Action) (string, error) {
	sends, err := d.store.Actions(container, kindSend)
	if err != nil {
		return "", err
	}
	sent := map[string]bool{}
	for _, send := range sends {
		if send.Status != incident.ActionFailed {
			sent[string(send.AuthorityRef)] = true
		}
	}
	about := relayedPR.FindStringSubmatch(action.ActionID)
	if sent[action.ActionID] || about == nil {
		return "", nil
	}
	earlier, err := d.store.Actions(container, action.Kind, incident.ActionAccepted)
	if err != nil {
		return "", err
	}
	for _, other := range earlier {
		named := relayedPR.FindStringSubmatch(other.ActionID)
		if other.ActionID != action.ActionID && sent[other.ActionID] && named != nil && named[0] == about[0] {
			return other.ActionID, nil
		}
	}
	return "", nil
}

func (d *Desk) delivered(container string, action incident.Action) (bool, error) {
	sends, err := d.store.Actions(container, kindSend, incident.ActionCompleted)
	if err != nil {
		return false, err
	}
	return slices.ContainsFunc(sends, func(send incident.Action) bool { return string(send.AuthorityRef) == action.ActionID }), nil
}

func (d *Desk) sendsOf(container, key, dispatch string) ([]incident.Action, error) {
	sends, err := d.store.Actions(container, kindSend)
	if err != nil {
		return nil, err
	}
	prefix := "send:" + key + ":" + dispatch
	return slices.DeleteFunc(sends, func(send incident.Action) bool {
		stem, _, _ := cutLast(send.ActionID, "#")
		return stem != prefix
	}), nil
}

func cutLast(text, separator string) (string, string, bool) {
	at := strings.LastIndex(text, separator)
	if at < 0 {
		return text, "", false
	}
	return text[:at], text[at+len(separator):], true
}

func resends(tries []incident.Action) []incident.Action {
	return slices.DeleteFunc(slices.Clone(tries), func(attempt incident.Action) bool { return !strings.Contains(attempt.ActionID, resendMark) })
}

func (d *Desk) send(ctx context.Context, container string, dispatch *laneDispatch, action incident.Action) error {
	tries, err := d.sendsOf(container, action.ActionID, dispatch.ID)
	if err != nil {
		return err
	}
	if slices.ContainsFunc(tries, func(attempt incident.Action) bool { return attempt.Status != incident.ActionFailed }) {
		return nil
	}
	if len(tries) >= sendAttempts || len(resends(tries)) > 0 {
		last := tries[len(tries)-1]
		said := ""
		if stderr, _ := last.Response["stderr"].(string); strings.TrimSpace(stderr) != "" {
			lines := strings.Split(strings.TrimSpace(stderr), "\n")
			said = "; stderr: " + lines[len(lines)-1]
		}
		return d.escalate(container+"/send:"+action.ActionID+":"+dispatch.ID, "SEND-FAILED", dispatch.Lane, fmt.Sprintf("%s to %s failed %d times: %s%s; each try's request id, caller, error, and stderr are on its send action", action.AuthorityRef, dispatch.ID, len(tries), last.Reason, said))
	}
	sendID := fmt.Sprintf("send:%s:%s#%d", action.ActionID, dispatch.ID, len(tries)+1)
	if _, _, err := d.accept(container, sendID, kindSend, dispatch.ID, action.ActionID, time.Time{}); err != nil {
		return err
	}
	started, err := d.attempt(container, func(tx *incident.Tx) error {
		_, err := tx.StartBy(sendID, d.Now(), time.Time{}, dispatch.ID)
		return err
	})
	if err != nil || !started {
		return err
	}
	return d.postSend(ctx, container, dispatch, action, sendID, "")
}

func (d *Desk) sendRecord(reply Reply) map[string]any {
	return map[string]any{"request_id": reply.Error.Data.RequestID, "caller": d.caller(), "error": reply.Error, "stderr": reply.Stderr}
}

func (d *Desk) caller() string {
	for _, pair := range d.sender {
		if handle, found := strings.CutPrefix(pair, "ORCA_TERMINAL_HANDLE="); found {
			return handle
		}
	}
	return d.Env("ORCA_TERMINAL_HANDLE")
}

func (d *Desk) answeredElsewhere(container string, action incident.Action, dispatch *laneDispatch) (bool, error) {
	tries, err := d.sendsOf(container, action.ActionID, dispatch.ID)
	if err != nil {
		return false, err
	}
	return slices.ContainsFunc(tries, func(attempt incident.Action) bool {
		return attempt.Status == incident.ActionFailed && strings.HasPrefix(string(attempt.Reason), codeAnswerConflict)
	}), nil
}

func (d *Desk) postSend(ctx context.Context, container string, dispatch *laneDispatch, action incident.Action, sendID, retry string) error {
	spec, err := relayOf(action)
	if err != nil {
		return err
	}
	thread := threadOf(container, action.ActionID)
	answers := action.Kind == kindReply
	if answers {
		elsewhere, err := d.answeredElsewhere(container, action, dispatch)
		if err != nil {
			return err
		}
		answers = !elsewhere
	}
	payload, err := compactJSON(map[string]string{"action": thread})
	if err != nil {
		return err
	}
	var argv []string
	switch {
	case answers:
		argv = []string{orchestration, "reply", "--id", spec.ReplyTo, "--body", fmt.Sprintf("%s\n\n(%s)", spec.Text, action.AuthorityRef)}
	case action.Kind == kindReply:
		argv = []string{orchestration, "send", "--to", "dispatch:" + dispatch.ID, "--type", "dispatch", "--subject", fmt.Sprintf("%s: on answered question %s", action.AuthorityRef, spec.ReplyTo), "--body", fmt.Sprintf("%s\n\n(%s)", spec.Text, action.AuthorityRef), "--thread-id", thread, "--payload", payload}
	default:
		body := fmt.Sprintf("%s\n\nReply on thread %s: subject `started %s` before acting, then `done %s: <result>`.", spec.Text, thread, action.ActionID, action.ActionID)
		argv = []string{orchestration, "send", "--to", "dispatch:" + dispatch.ID, "--type", "dispatch", "--subject", fmt.Sprintf("%s: act %s", action.AuthorityRef, action.ActionID), "--body", body, "--thread-id", thread, "--payload", payload}
	}
	if retry != "" {
		argv = append(argv, "--retry-request", retry)
	}
	reply := d.call(ctx, argv...)
	if reply.OK {
		return d.settleSend(ctx, container, dispatch, action, sendID, thread, answers, reply)
	}
	record := d.sendRecord(reply)
	switch {
	case answers && reply.Error.Code == codeAnswerConflict:
		if _, err := d.attempt(container, func(tx *incident.Tx) error {
			return failSend(tx, sendID, clipRunes(reply.Error.Code+": "+reply.Error.Message, 300), record)
		}); err != nil {
			return err
		}
		return d.send(ctx, container, dispatch, action)
	case slices.Contains(unreachableCodes, reply.Error.Code):
		_, err := d.attempt(container, func(tx *incident.Tx) error {
			if _, err := tx.Lose(sendID); err != nil {
				return err
			}
			_, err := tx.Respond(sendID, record)
			return err
		})
		return err
	}
	encoded, err := json.Marshal(reply.Error)
	if err != nil {
		return err
	}
	_, err = d.attempt(container, func(tx *incident.Tx) error { return failSend(tx, sendID, clipRunes(string(encoded), 300), record) })
	return err
}

func clipRunes(text string, limit int) string {
	runes := []rune(text)
	if len(runes) <= limit {
		return text
	}
	return string(runes[:limit])
}

func failSend(tx *incident.Tx, key, reason string, record map[string]any) error {
	if _, err := tx.Fail(key, reason); err != nil {
		return err
	}
	_, err := tx.Respond(key, record)
	return err
}

func (d *Desk) settleSend(ctx context.Context, container string, dispatch *laneDispatch, action incident.Action, sendID, thread string, answers bool, reply Reply) error {
	var result struct {
		Message struct {
			ID string `json:"id"`
		} `json:"message"`
	}
	_ = json.Unmarshal(reply.Result, &result)
	message := or(result.Message.ID, "sent")
	receipt := map[string]any{"message": message, "at": d.stamp()}
	if _, err := d.attempt(container, func(tx *incident.Tx) error {
		_, err := tx.Complete(sendID, receipt, "")
		return err
	}); err != nil {
		return err
	}
	if action.Kind == kindReply {
		if _, err := d.attempt(container, func(tx *incident.Tx) error {
			if _, err := tx.StartBy(action.ActionID, d.Now(), time.Time{}, ""); err != nil {
				return err
			}
			_, err := tx.Complete(action.ActionID, receipt, "")
			return err
		}); err != nil {
			return err
		}
	}
	if answers {
		return nil
	}
	_, err := d.wake(ctx, dispatch.Terminal, dispatch.Environment, fmt.Sprintf("%s: read Orca message on thread %s now", action.AuthorityRef, thread))
	return err
}

func delivers(message orcaMessage, container string, action incident.Action) (bool, error) {
	thread := threadOf(container, action.ActionID)
	if action.Kind != kindReply {
		return message.ThreadID == thread, nil
	}
	spec, err := relayOf(action)
	if err != nil {
		return false, err
	}
	onThread := message.ThreadID == spec.ReplyTo || message.ThreadID == thread
	return onThread && strings.HasSuffix(strings.TrimRight(message.Body, " \t\r\n"), "("+string(action.AuthorityRef)+")"), nil
}

func (d *Desk) reconcileSends(ctx context.Context, container, lane string) error {
	cutoff := d.Now().Add(-orphanedSend)
	started, err := d.store.Actions(container, kindSend, incident.ActionStarted)
	if err != nil {
		return err
	}
	for _, send := range started {
		at, err := store.Parse(string(send.StartedAt))
		if err != nil {
			return err
		}
		if !at.Before(cutoff) {
			continue
		}
		if _, err := d.attempt(container, func(tx *incident.Tx) error {
			_, err := tx.Lose(send.ActionID)
			return err
		}); err != nil {
			return err
		}
	}
	lost, err := d.store.Actions(container, kindSend, incident.ActionUnverifiable)
	if err != nil {
		return err
	}
	for _, send := range lost {
		if err := d.reconcileSend(ctx, container, lane, send); err != nil {
			return err
		}
	}
	return nil
}

func (d *Desk) reconcileSend(ctx context.Context, container, lane string, send incident.Action) error {
	action, err := d.store.Action(container, string(send.AuthorityRef))
	if err != nil {
		return err
	}
	request, _ := send.Response["request_id"].(string)
	state := "absent"
	if request != "" {
		state = d.requestState(ctx, request)
	}
	if state == "pending" || state == stateUnread {
		return nil
	}
	if _, err := d.attempt(container, func(tx *incident.Tx) error {
		response := map[string]any{"receipt": state}
		for key, value := range send.Response {
			if key != "receipt" {
				response[key] = value
			}
		}
		_, err := tx.Respond(send.ActionID, response)
		return err
	}); err != nil {
		return err
	}
	dispatch, err := d.show(ctx, lane, "")
	if err != nil {
		return err
	}
	current := dispatch
	if dispatch != nil && dispatch.ID != send.Target {
		current = nil
	}
	if state == statusCompleted && current != nil {
		return d.postSend(ctx, container, current, action, send.ActionID, request)
	}
	var mailbox []orcaMessage
	if current != nil {
		var read bool
		if mailbox, read = d.mailbox(ctx, current.Terminal, current.Environment); !read {
			return nil
		}
	}
	for _, message := range mailbox {
		found, err := delivers(message, container, action)
		if err != nil {
			return err
		}
		if found {
			return d.settleFound(container, send, action, message)
		}
	}
	if current == nil || inactive(current.Status) {
		return d.escalate(container+"/"+send.ActionID, "UNVERIFIABLE", lane, fmt.Sprintf("%s send to %s lost its response; Orca holds no receipt and no message on its thread; it was not resent", action.AuthorityRef, send.Target))
	}
	startedAt, err := store.Parse(string(send.StartedAt))
	if err != nil {
		return err
	}
	if d.Now().Sub(startedAt) < resendAfter {
		return nil
	}
	tries, err := d.sendsOf(container, action.ActionID, current.ID)
	if err != nil {
		return err
	}
	again := len(resends(tries))
	if again >= resendLimit {
		return d.reportUnacked(container, lane, action, send, again)
	}
	return d.resend(ctx, container, current, action, send, again+1)
}

func (d *Desk) settleFound(container string, send, action incident.Action, message orcaMessage) error {
	receipt := map[string]any{"message": message.ID, "at": or(message.CreatedAt, d.stamp()), "reconciled": true}
	if _, err := d.attempt(container, func(tx *incident.Tx) error {
		_, err := tx.Complete(send.ActionID, receipt, "")
		return err
	}); err != nil {
		return err
	}
	if action.Kind != kindReply {
		return nil
	}
	_, err := d.attempt(container, func(tx *incident.Tx) error {
		if _, err := tx.StartBy(action.ActionID, d.Now(), time.Time{}, ""); err != nil {
			return err
		}
		_, err := tx.Complete(action.ActionID, receipt, "")
		return err
	})
	return err
}

func (d *Desk) reportUnacked(container, lane string, action, send incident.Action, sentAgain int) error {
	log := "escalation:unacked:" + container + "/" + send.ActionID
	held, err := d.known(deskRunner, log)
	if err != nil || held != nil {
		return err
	}
	spec, err := relayOf(action)
	if err != nil {
		return err
	}
	if err := d.keep(orcaMessage{ID: action.ActionID, Body: spec.Text}); err != nil {
		return err
	}
	return d.record(log, fmt.Sprintf("UNACKED %s %s: the send to %s and %d resends lost their response, and the lane has not named %s on its thread or the Run; Orca holds no receipt and no message on its thread; nothing more is sent; check the lane's Run messages before delivering the relay text at this record's path by hand", action.ActionID, lane, send.Target, sentAgain, action.ActionID))
}

func (d *Desk) resend(ctx context.Context, container string, dispatch *laneDispatch, action, send incident.Action, number int) error {
	stem, _, _ := strings.Cut(send.ActionID, resendMark)
	sendID := fmt.Sprintf("%s%s%d", stem, resendMark, number)
	moved, err := d.attempt(container, func(tx *incident.Tx) error {
		if _, err := tx.Fail(send.ActionID, "response lost with no receipt and no message on its thread; resent as "+sendID); err != nil {
			return err
		}
		if _, err := tx.Accept(sendID, kindSend, dispatch.ID, action.ActionID, d.Now()); err != nil {
			return err
		}
		_, err := tx.StartBy(sendID, d.Now(), time.Time{}, dispatch.ID)
		return err
	})
	if err != nil || !moved {
		return err
	}
	return d.postSend(ctx, container, dispatch, action, sendID, "")
}
