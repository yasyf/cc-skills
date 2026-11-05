package orca

import (
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/roster"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/store"
	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/watch/screen"
)

const (
	codeUnparseable       = "unparseable"
	codeRemoteUnreachable = "remote_runtime_unavailable"
	codeAnswerConflict    = "answer_conflict"
	stateUnread           = "unread"
	inboxPage             = 100
	slowCall              = 10 * time.Second
)

var unreachableCodes = []string{"runtime_unavailable", codeRemoteUnreachable, "runtime_timeout", "invalid_runtime_response", codeUnparseable}

type orcaUnreachable struct{ Text string }

func (e *orcaUnreachable) Error() string { return e.Text }

type laneDispatch struct {
	Lane, ID, Terminal, Status, Environment string
	Live, Fenced, Missing                   bool
}

func inactive(status string) bool {
	return status == statusCompleted || status == statusFailed
}

func (l *laneDispatch) active() bool {
	return l != nil && !inactive(l.Status)
}

type orcaMessage struct {
	ID         string `json:"id"`
	Type       string `json:"type"`
	Sequence   int64  `json:"sequence"`
	FromHandle string `json:"from_handle"`
	ToHandle   string `json:"to_handle"`
	Subject    string `json:"subject"`
	Body       string `json:"body"`
	ThreadID   string `json:"thread_id"`
	Payload    string `json:"payload"`
	CreatedAt  string `json:"created_at"`
	Read       int    `json:"read"`
}

func (d *Desk) client() Client {
	return Client{Runner: d.Runner, Env: d.sender}
}

func (d *Desk) call(ctx context.Context, args ...string) Reply {
	began := d.Now()
	reply, err := d.client().Call(ctx, args...)
	if took := d.Now().Sub(began); took >= slowCall {
		environment := ""
		if at := slices.Index(args, "--environment"); at >= 0 && at+1 < len(args) {
			environment = " on " + args[at+1]
		}
		fmt.Fprintf(d.Stderr, "%s slow orca call %s%s took %ds\n", began.Format(time.RFC3339), strings.Join(args[:min(len(args), 2)], " "), environment, int(took.Seconds()))
	}
	if err != nil {
		return Reply{Error: ReplyError{Code: codeUnparseable, Message: err.Error()}}
	}
	return reply
}

func unreachable(reply Reply) *orcaUnreachable {
	if reply.OK || !slices.Contains(unreachableCodes, reply.Error.Code) {
		return nil
	}
	return &orcaUnreachable{Text: reply.Refusal()}
}

func (d *Desk) ask(ctx context.Context, args ...string) (Reply, error) {
	reply := d.call(ctx, args...)
	if down := unreachable(reply); down != nil {
		return reply, down
	}
	return reply, nil
}

func (d *Desk) receiptPath(lane, suffix string) string {
	return filepath.Join(d.Config.Receipts, lane+suffix)
}

func (d *Desk) receipt(lane string) string {
	raw, err := os.ReadFile(d.receiptPath(lane, ".json"))
	if err != nil {
		return ""
	}
	var reply struct {
		Result struct {
			Dispatch string `json:"dispatchId"`
		} `json:"result"`
	}
	if json.Unmarshal(raw, &reply) != nil {
		return ""
	}
	return reply.Result.Dispatch
}

func (d *Desk) terminal(lane string) string {
	raw, err := os.ReadFile(d.receiptPath(lane, ".terminal"))
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(raw))
}

func (d *Desk) laneNames() ([]string, error) {
	paths, err := filepath.Glob(filepath.Join(d.Config.Receipts, "*.terminal"))
	if err != nil {
		return nil, err
	}
	names := make([]string, 0, len(paths))
	for _, path := range paths {
		names = append(names, strings.TrimSuffix(filepath.Base(path), ".terminal"))
	}
	slices.Sort(names)
	return names, nil
}

func (d *Desk) laneOf(handle string) (string, error) {
	names, err := d.laneNames()
	if err != nil {
		return "", err
	}
	for _, lane := range names {
		if handle == d.terminal(lane) || handle == "dispatch:"+d.receipt(lane) {
			return lane, nil
		}
	}
	return handle, nil
}

func (d *Desk) show(ctx context.Context, lane, dispatch string) (*laneDispatch, error) {
	key := shownKey{lane: lane, dispatch: or(dispatch, d.receipt(lane))}
	if key.dispatch == "" {
		return nil, nil
	}
	d.reads.mu.Lock()
	kept, found := d.reads.shown[key]
	d.reads.mu.Unlock()
	if found {
		return kept.clone(), nil
	}
	shown, err := d.showNow(ctx, lane, key.dispatch)
	if err != nil {
		return nil, err
	}
	d.reads.mu.Lock()
	defer d.reads.mu.Unlock()
	if d.reads.passing {
		d.reads.shown[key] = shown.clone()
	}
	return shown, nil
}

func (l *laneDispatch) clone() *laneDispatch {
	if l == nil {
		return nil
	}
	copied := *l
	return &copied
}

func (d *Desk) showNow(ctx context.Context, lane, dispatch string) (*laneDispatch, error) {
	if dispatch == "" {
		dispatch = d.receipt(lane)
	}
	if dispatch == "" {
		return nil, nil
	}
	shown := d.call(ctx, orchestration, "worker-show", "--dispatch", dispatch)
	if !shown.OK && shown.Error.Code == codeRemoteUnreachable {
		return &laneDispatch{Lane: lane, ID: dispatch, Terminal: d.terminal(lane), Status: codeRemoteUnreachable, Live: true}, nil
	}
	if down := unreachable(shown); down != nil {
		return nil, down
	}
	if !shown.OK {
		return nil, nil
	}
	var result struct {
		Dispatch struct {
			Status  string  `json:"status"`
			Revoked *string `json:"capabilityRevokedAt"`
		} `json:"dispatch"`
		Terminal *struct {
			Handle string `json:"handle"`
		} `json:"terminal"`
		Observation *struct {
			Status string `json:"status"`
		} `json:"observation"`
		Server *struct {
			Environment string `json:"environmentId"`
		} `json:"server"`
	}
	if err := shown.Into(&result); err != nil {
		return nil, err
	}
	found := &laneDispatch{Lane: lane, ID: dispatch, Terminal: d.terminal(lane), Status: result.Dispatch.Status, Fenced: result.Dispatch.Revoked != nil && *result.Dispatch.Revoked != ""}
	if result.Terminal != nil && result.Terminal.Handle != "" {
		found.Terminal = result.Terminal.Handle
	}
	if result.Observation != nil {
		found.Live = result.Observation.Status == "live"
		found.Missing = result.Observation.Status == "missing"
	}
	if result.Server != nil {
		found.Environment = result.Server.Environment
	}
	return found, nil
}

func (d *Desk) environmentOf(ctx context.Context, lane string) (string, error) {
	shown, err := d.show(ctx, lane, "")
	if err != nil || shown == nil {
		return "", err
	}
	return shown.Environment, nil
}

func (d *Desk) fence(ctx context.Context, dispatch string) (string, string) {
	defer d.reads.forgetDispatch(dispatch)
	stopped := d.call(ctx, orchestration, "worker-stop", "--dispatch", dispatch)
	if !stopped.OK && stopped.Error.Code == CodeFenced {
		stopped = d.call(ctx, orchestration, "worker-abandon", "--dispatch", dispatch)
	}
	if !stopped.OK {
		return "", stopped.Error.Code
	}
	var fenced struct {
		State string `json:"state"`
	}
	if err := stopped.Into(&fenced); err != nil {
		return "", codeUnparseable
	}
	return fenced.State, ""
}

func messagesOf(reply Reply) ([]orcaMessage, error) {
	var result struct {
		Messages []orcaMessage `json:"messages"`
	}
	if err := json.Unmarshal(reply.Result, &result); err != nil {
		return nil, fmt.Errorf("orca messages: %w", err)
	}
	return result.Messages, nil
}

func (d *Desk) mailbox(ctx context.Context, terminal, environment string) ([]orcaMessage, bool) {
	listed := d.call(ctx, append([]string{orchestration, "check", "--terminal", terminal, "--all"}, on(environment)...)...)
	if !listed.OK {
		return nil, false
	}
	messages, err := messagesOf(listed)
	if err != nil {
		fmt.Fprintln(d.Stderr, err)
		return nil, false
	}
	return messages, true
}

func (d *Desk) peek(ctx context.Context, terminal, environment string) ([]orcaMessage, bool) {
	key := peekKey{terminal: terminal, environment: environment}
	d.reads.mu.Lock()
	kept, found := d.reads.peeked[key]
	d.reads.mu.Unlock()
	if found {
		return kept, true
	}
	peeked := d.call(ctx, append([]string{orchestration, "check", "--terminal", terminal, "--peek"}, on(environment)...)...)
	if !peeked.OK {
		return nil, false
	}
	messages, err := messagesOf(peeked)
	if err != nil {
		fmt.Fprintln(d.Stderr, err)
		return nil, false
	}
	d.reads.mu.Lock()
	defer d.reads.mu.Unlock()
	if d.reads.passing {
		d.reads.peeked[key] = messages
	}
	return messages, true
}

func (d *Desk) unreadOn(ctx context.Context, terminal, environment string) (map[string]bool, bool) {
	messages, read := d.peek(ctx, terminal, environment)
	if !read {
		return nil, false
	}
	held := map[string]bool{}
	for _, message := range messages {
		held[message.ID] = true
	}
	return held, true
}

func (d *Desk) mail(ctx context.Context, limit int) ([]orcaMessage, error) {
	reply, err := d.ask(ctx, orchestration, "inbox", "--limit", fmt.Sprint(limit))
	if err != nil {
		return nil, err
	}
	return messagesOf(reply)
}

func (d *Desk) mailSince(ctx context.Context, cursor int64, page int) ([]orcaMessage, error) {
	for {
		messages, err := d.mail(ctx, page)
		if err != nil {
			return nil, err
		}
		if len(messages) == page && messages[len(messages)-1].Sequence > cursor+1 {
			page *= 2
			continue
		}
		fresh := make([]orcaMessage, 0, len(messages))
		for _, message := range slices.Backward(messages) {
			if message.Sequence > cursor {
				fresh = append(fresh, message)
			}
		}
		return fresh, nil
	}
}

func (d *Desk) requestState(ctx context.Context, request string) string {
	shown := d.call(ctx, orchestration, "request-show", "--request", request)
	if !shown.OK {
		return stateUnread
	}
	var result struct {
		State string `json:"state"`
	}
	if json.Unmarshal(shown.Result, &result) != nil || result.State == "" {
		return "absent"
	}
	return result.State
}

func (d *Desk) readScreen(ctx context.Context, terminal, environment string) (screen.Terminal, bool) {
	read := d.call(ctx, append([]string{"terminal", "read", "--terminal", terminal, "--screen"}, on(environment)...)...)
	if !read.OK {
		return screen.Terminal{}, false
	}
	var result struct {
		Terminal screen.Terminal `json:"terminal"`
	}
	if json.Unmarshal(read.Result, &result) != nil {
		return screen.Terminal{}, false
	}
	return result.Terminal, true
}

func (d *Desk) atRest(ctx context.Context, terminal, environment string) bool {
	shown, read := d.readScreen(ctx, terminal, environment)
	return read && screen.InputState(shown) == screen.Idle
}

func (d *Desk) wake(ctx context.Context, terminal, environment, text string) (bool, error) {
	shown, read := d.readScreen(ctx, terminal, environment)
	if !read || !screen.Wakeable(shown) {
		return false, nil
	}
	_, err := d.Runner.Run(ctx, shell.Command{Argv: append([]string{"orca", "terminal", "send", "--terminal", terminal, "--text", text, "--enter", "--json"}, on(environment)...)})
	var exit *shell.ExitError
	if err != nil && !errors.As(err, &exit) {
		return false, err
	}
	return true, nil
}

type deskWorker struct {
	Dispatch string `json:"dispatchId"`
	Status   string `json:"dispatchStatus"`
	Terminal string `json:"agentTerminalHandle"`
}

func (d *Desk) workers(ctx context.Context) ([]deskWorker, error) {
	d.reads.mu.Lock()
	kept, found, paging := d.reads.workers, d.reads.listed, d.reads.paging
	d.reads.mu.Unlock()
	if found {
		return kept, nil
	}
	var rows []deskWorker
	var err error
	if paging || d.Config.Roster == "" {
		rows, err = d.listWorkers(ctx)
	} else {
		err = roster.Read(d.Config.Roster, d.Now(), &rows)
	}
	if err != nil {
		return nil, err
	}
	d.reads.mu.Lock()
	defer d.reads.mu.Unlock()
	if d.reads.passing {
		d.reads.workers, d.reads.listed = rows, true
	}
	return rows, nil
}

func (d *Desk) listWorkers(ctx context.Context) ([]deskWorker, error) {
	var rows []deskWorker
	var cursor []string
	for {
		reply, err := d.ask(ctx, append([]string{orchestration, "worker-list", "--run", d.Config.Run, "--include-remote", "--limit", "100"}, cursor...)...)
		if err != nil {
			return nil, err
		}
		var page struct {
			Workers []deskWorker `json:"workers"`
			Page    *struct {
				Next string `json:"nextCursor"`
			} `json:"page"`
		}
		if err := reply.Into(&page); err != nil {
			return nil, err
		}
		rows = append(rows, page.Workers...)
		if page.Page == nil || page.Page.Next == "" {
			return rows, nil
		}
		cursor = []string{"--cursor", page.Page.Next}
	}
}

type bindingState struct {
	Terminal   string `json:"terminal"`
	Run        string `json:"run"`
	Bound      bool   `json:"bound"`
	Generation any    `json:"generation"`
	At         string `json:"at"`
	Why        string `json:"why,omitempty"`
	Via        string `json:"via,omitempty"`
}

func (d *Desk) followCoordinator(ctx context.Context) (Reply, string, any) {
	d.sender = nil
	shown := d.call(ctx, orchestration, "run-show", "--id", d.Config.Run)
	var result struct {
		Run struct {
			Coordinator string `json:"coordinator_handle"`
			Generation  any    `json:"consumer_generation"`
		} `json:"run"`
	}
	_ = json.Unmarshal(shown.Result, &result)
	if result.Run.Coordinator != "" {
		d.sender = []string{"ORCA_TERMINAL_HANDLE=" + result.Run.Coordinator, "ORCA_PANE_KEY="}
	}
	return shown, result.Run.Coordinator, result.Run.Generation
}

func (d *Desk) readBinding(ctx context.Context) (*bindingState, error) {
	shown, terminal, generation := d.followCoordinator(ctx)
	state := &bindingState{Terminal: terminal, Run: d.Config.Run, Generation: generation, At: store.Stamp(d.Now())}
	coordinates := ""
	current := Reply{}
	if terminal != "" {
		current = d.call(ctx, orchestration, "run-current")
		var result struct {
			Run struct {
				ID string `json:"id"`
			} `json:"run"`
		}
		_ = json.Unmarshal(current.Result, &result)
		coordinates = result.Run.ID
	}
	state.Bound = terminal != "" && coordinates == d.Config.Run
	switch {
	case !shown.OK:
		state.Why = "orca orchestration run-show failed: " + shown.Refusal()
	case terminal == "":
		state.Why = "Orca binds " + d.Config.Run + " to no terminal"
	case !current.OK:
		state.Why = "orca orchestration run-current as coordinator " + terminal + " failed: " + current.Refusal()
	case !state.Bound:
		state.Why = fmt.Sprintf("coordinator %s coordinates %s, not %s", terminal, cmp.Or(coordinates, "no Run"), d.Config.Run)
	}
	if state.Bound {
		state.Via = fmt.Sprintf("coordinator read from Orca at generation %v", generation)
	}
	return state, d.setFact(deskRunner, "binding", state)
}

func (d *Desk) rebindCommand() string {
	return "lr relay rebind --config " + filepath.Join(filepath.Dir(d.Config.Source), "lr.json")
}

func (d *Desk) unboundText(state *bindingState) string {
	return fmt.Sprintf("the orca relay cannot start workers on %s: %s. From the coordinator's Orca terminal run `%s`; the relay follows the new coordinator on its next pass, so it needs no restart", d.Config.Run, state.Why, d.rebindCommand())
}

func (d *Desk) boundForLaunch(ctx context.Context) (*bindingState, error) {
	if d.binding != nil {
		return d.binding, nil
	}
	state, err := d.readBinding(ctx)
	if err != nil {
		return nil, err
	}
	d.binding = state
	if !state.Bound {
		key := fmt.Sprintf("unbound:%s:%v", state.Terminal, state.Generation)
		if err := d.escalate(key, "UNBOUND", "runner", d.unboundText(state)+"; each launch fails with LAUNCH-FAILED until it is bound"); err != nil {
			return nil, err
		}
	}
	return state, nil
}
