package orca

import (
	"bytes"
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"maps"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"syscall"
)

const (
	gcParallel  = 4
	spriteReads = 12
	sweptBatch  = 40
	changed     = "changed since the first read"
	verbKeep    = "keep"
	verbSkip    = "skip"
	verbStop    = "stop"
	verbStopped = "stopped"
	verbRelease = "release"
	verbEnd     = "end"
	verbClose   = "close"
	verbClosed  = "closed"
	verbRemove  = "remove"
	verbRemoved = "removed"
	verbDrop    = "drop"
	verbDropped = "dropped"
	verbDestroy = "destroy"
	verbGone    = "destroyed"
	stopping    = "stopping"
	unknownID   = "its session's identity is unknown: "
)

type API interface {
	Workers(ctx context.Context, run string) ([]Worker, error)
	Idle(ctx context.Context, terminal, environment string) (Idleness, error)
	Presence(ctx context.Context, terminal, environment string) (string, error)
	TerminalsIn(ctx context.Context, worktree, environment string) (TerminalList, error)
	Stop(ctx context.Context, dispatch string) (string, string, error)
	Abandon(ctx context.Context, dispatch string) (string, string, error)
	Release(ctx context.Context, dispatch string) (string, string, error)
	Close(ctx context.Context, terminal, environment string) (string, error)
	Remove(ctx context.Context, worktree, environment string) (string, error)
	PairedCheckout(ctx context.Context, worktree, environment string, git Repositories) (CheckoutRead, error)
	Registered(ctx context.Context, worktree string) (bool, error)
	Forget(ctx context.Context, environment string) (string, error)
	Servers(ctx context.Context) (map[string]bool, error)
	NowMS() int64
	Holder(ctx context.Context, worktree string, panes map[string]bool) (string, error)
	Sessions(ctx context.Context) (Sessions, error)
	StillRuns(ctx context.Context, session Session) (bool, error)
}

type Live struct {
	Client
	Processes
}

func (Live) NowMS() int64 {
	return NowMS()
}

type Action struct {
	Verb        string
	Target      string
	Reason      string
	Shells      []string
	Agents      []Action
	Error       string
	Environment string
	Tree        tree
	Session     *Session
	Unproven    bool
	Unheld      bool
}

func (a Action) named() string {
	if a.Session == nil {
		return a.Target
	}
	return a.Target + " " + a.Session.said()
}

func (a Action) Line() string {
	said := a.Verb + " " + a.named()
	if a.Reason != "" {
		said += " " + a.Reason
	}
	if a.Error != "" {
		return said + "\nerror " + a.Target + " " + a.Error
	}
	return said
}

type Pause struct {
	Resumed string
}

func (p Pause) Held() bool {
	_, err := os.Stat(p.Resumed)
	return err != nil
}

func (p Pause) Skip(target, verb string) Action {
	return Action{Verb: verbSkip, Target: target, Reason: fmt.Sprintf("%s: cleanup paused until %s exists", verb, p.Resumed)}
}

type GC struct {
	Orca     API
	Git      Repositories
	Fleet    SpriteFleet
	Pause    Pause
	Lock     string
	Out      io.Writer
	DryRun   bool
	Keep     map[string]bool
	Finished map[string]bool

	writing sync.Mutex
}

func (g *GC) say(line string) {
	g.writing.Lock()
	defer g.writing.Unlock()
	fmt.Fprintln(g.Out, line)
}

func (g *GC) report(done Action) Action {
	prefix := ""
	if g.DryRun && done.Verb != verbKeep {
		prefix = "would "
	}
	g.say(prefix + done.Line())
	return done
}

func parallel[T, R any](items []T, work func(T) (R, error)) ([]R, error) {
	return bounded(gcParallel, items, work)
}

func bounded[T, R any](limit int, items []T, work func(T) (R, error)) ([]R, error) {
	results := make([]R, len(items))
	faults := make([]error, len(items))
	slots := make(chan struct{}, limit)
	var running sync.WaitGroup
	for i, item := range items {
		slots <- struct{}{}
		running.Go(func() {
			defer func() { <-slots }()
			results[i], faults[i] = work(item)
		})
	}
	running.Wait()
	return results, errors.Join(faults...)
}

func current(ctx context.Context, worker Worker, orca API, sessions Sessions) (string, *Action, error) {
	presence, err := orca.Presence(ctx, worker.Terminal, worker.Environment)
	if err != nil {
		return "", nil, err
	}
	switch presence {
	case disconnected:
		return "", nil, nil
	case CodeStaleHandle:
		if worker.Environment == "" && len(sessions[worker.Terminal]) == 0 {
			return "", nil, nil
		}
		return "", &Action{Verb: verbKeep, Target: worker.Terminal, Reason: staleHandle}, nil
	}
	return worker.Terminal, nil, nil
}

func terminalAction(ctx context.Context, worker Worker, live map[string]bool, orca API, ended map[string]string, sessions Sessions) (*Action, error) {
	if worker.TerminalState == stateReleased || worker.Terminal == "" {
		return nil, nil
	}
	if detail, done := ended[worker.Dispatch]; done {
		if detail == closedByStop {
			return nil, nil
		}
		if worker.Ownership == "owned" && worker.Settled() && worker.RetainedReason == "" {
			return &Action{Verb: verbRelease, Target: worker.Dispatch}, nil
		}
		terminal, verdict, err := current(ctx, worker, orca, sessions)
		if terminal == "" || err != nil {
			return verdict, err
		}
		return &Action{Verb: verbEnd, Target: terminal, Reason: fmt.Sprintf("dispatch %s is done", worker.Dispatch), Environment: worker.Environment}, nil
	}
	if worker.RetainedReason != "" && worker.RetainedReason != external {
		return &Action{Verb: verbKeep, Target: worker.Terminal, Reason: "retained: " + worker.RetainedReason}, nil
	}
	if worker.Ownership == "owned" {
		return &Action{Verb: verbRelease, Target: worker.Dispatch}, nil
	}
	if worker.RetainedReason != external && worker.Environment == "" {
		return &Action{Verb: verbKeep, Target: worker.Terminal, Reason: "Orca records the terminal as " + cmp.Or(worker.Ownership, "unowned")}, nil
	}
	if live[worker.Terminal] {
		return &Action{Verb: verbKeep, Target: worker.Terminal, Reason: "serves an unsettled dispatch"}, nil
	}
	terminal, verdict, err := current(ctx, worker, orca, sessions)
	if terminal == "" || err != nil {
		return verdict, err
	}
	idle, err := orca.Idle(ctx, terminal, worker.Environment)
	if err != nil {
		return nil, err
	}
	switch idle {
	case IdleUnknown:
		return nil, nil
	case IdleBusy:
		return &Action{Verb: verbKeep, Target: terminal, Reason: notIdle}, nil
	}
	closable := &Action{Verb: verbClose, Target: terminal, Reason: "dispatch " + worker.Dispatch, Environment: worker.Environment, Tree: tree{worker.Environment, worker.Worktree}}
	if worker.Environment != "" {
		return closable, nil
	}
	session, unknown := sessions.agent(worker)
	if unknown != "" {
		return &Action{Verb: verbKeep, Target: terminal, Reason: unknownID + unknown}, nil
	}
	closable.Session = &session
	return closable, nil
}

func unsaved(ctx context.Context, checkout Checkout, remote Remote) (string, error) {
	if checkout.Dirty {
		return "uncommitted changes", nil
	}
	if checkout.OffOrigin > 0 {
		return fmt.Sprintf("%d commits off origin on its other branches or stashes", checkout.OffOrigin), nil
	}
	if checkout.OnTrunk || remote.Heads[checkout.Head] {
		return "", nil
	}
	landed, err := remote.Landed.Landed(ctx, checkout)
	var refused githubRefusal
	if errors.As(err, &refused) {
		return refused.Error(), nil
	}
	if err != nil || landed {
		return "", err
	}
	label := checkout.Branch
	if label == "" {
		label = checkout.Head[:min(10, len(checkout.Head))]
	}
	return "unpushed commits on " + label, nil
}

func worktreeAction(ctx context.Context, checkout Checkout, listed TerminalList, closing map[string]Action, remote Remote, nowMS int64) (Action, error) {
	if listed.Refusal != "" {
		return Action{Verb: verbKeep, Target: checkout.Path, Reason: listed.Refusal}, nil
	}
	held, err := unsaved(ctx, checkout, remote)
	if err != nil {
		return Action{}, err
	}
	if held != "" {
		return Action{Verb: verbKeep, Target: checkout.Path, Reason: held, Unproven: true}, nil
	}
	agents := 0
	for _, terminal := range listed.Terminals {
		if _, closes := closing[terminal.Handle]; terminal.Agent != "" && !closes {
			agents++
		}
	}
	if agents > 0 {
		return Action{Verb: verbKeep, Target: checkout.Path, Reason: fmt.Sprintf("%d agent terminal(s) no settled dispatch owns", agents)}, nil
	}
	for _, terminal := range listed.Terminals {
		if _, closes := closing[terminal.Handle]; !closes && !terminal.idleShell(checkout.Path, nowMS) {
			return Action{Verb: verbKeep, Target: checkout.Path, Reason: fmt.Sprintf("shell %s is in use: %s", terminal.Handle, terminal.Title)}, nil
		}
	}
	action := Action{Verb: verbRemove, Target: checkout.Path, Environment: checkout.Environment}
	for _, terminal := range listed.Terminals {
		action.Shells = append(action.Shells, terminal.Handle)
		if stop, closes := closing[terminal.Handle]; closes {
			action.Agents = append(action.Agents, stop)
		}
	}
	slices.Sort(action.Shells)
	slices.SortFunc(action.Agents, func(a, b Action) int { return strings.Compare(a.Target, b.Target) })
	if len(action.Agents) > 0 {
		targets := make([]string, len(action.Agents))
		for i, agent := range action.Agents {
			targets[i] = agent.named()
		}
		action.Reason = "stopping " + strings.Join(targets, ", ")
	}
	return action, nil
}

func (g *GC) oneRemoval(work func() error) error {
	if err := os.MkdirAll(filepath.Dir(g.Lock), 0o750); err != nil {
		return err
	}
	lock, err := os.OpenFile(g.Lock, os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	defer func() { _ = lock.Close() }()
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX); err != nil {
		return err
	}
	return work()
}

func (g *GC) sameSession(ctx context.Context, action Action) (bool, error) {
	if action.Session == nil {
		return true, nil
	}
	return g.Orca.StillRuns(ctx, *action.Session)
}

func (g *GC) removalBlocked(ctx context.Context, action Action) (*Action, error) {
	listed, err := g.Orca.TerminalsIn(ctx, action.Target, action.Environment)
	if err != nil {
		return nil, err
	}
	if listed.Refusal != "" {
		return &Action{Verb: verbKeep, Target: action.Target, Reason: listed.Refusal}, nil
	}
	moved := &Action{Verb: verbKeep, Target: action.Target, Reason: changed, Shells: action.Shells, Agents: action.Agents}
	stoppingTargets := map[string]bool{}
	for _, agent := range action.Agents {
		stoppingTargets[agent.Target] = true
	}
	for _, agent := range action.Agents {
		if agent.Verb != verbClose {
			continue
		}
		idle, err := g.Orca.Idle(ctx, agent.Target, agent.Environment)
		if err != nil {
			return nil, err
		}
		if idle == IdleBusy {
			return moved, nil
		}
		same, err := g.sameSession(ctx, agent)
		if err != nil {
			return nil, err
		}
		if !same {
			return moved, nil
		}
	}
	panes := map[string]bool{}
	for _, terminal := range listed.Terminals {
		if !slices.Contains(action.Shells, terminal.Handle) {
			return moved, nil
		}
		if stoppingTargets[terminal.Handle] {
			panes[terminal.pane()] = true
		} else if !terminal.idleShell(action.Target, g.Orca.NowMS()) {
			return moved, nil
		}
	}
	if action.Environment == "" {
		held, err := g.Orca.Holder(ctx, action.Target, panes)
		if err != nil {
			return nil, err
		}
		if held != "" {
			return &Action{Verb: verbKeep, Target: action.Target, Reason: held, Shells: action.Shells, Agents: action.Agents}, nil
		}
	}
	return nil, nil
}

func (g *GC) applyRemove(ctx context.Context, action Action) (Action, error) {
	blocked, err := g.removalBlocked(ctx, action)
	if err != nil {
		return Action{}, err
	}
	if blocked != nil {
		return *blocked, nil
	}
	if g.Pause.Held() {
		return g.Pause.Skip(action.Target, action.Verb), nil
	}
	if action.Environment != "" {
		refusal, err := g.Orca.Remove(ctx, action.Target, action.Environment)
		if err != nil {
			return Action{}, err
		}
		if refusal != "" {
			return Action{Verb: verbKeep, Target: action.Target, Reason: refusal}, nil
		}
		return Action{Verb: verbRemoved, Target: action.Target, Reason: action.Reason, Shells: action.Shells, Agents: action.Agents}, nil
	}
	return g.removeLocal(ctx, action)
}

func (g *GC) removeLocal(ctx context.Context, action Action) (Action, error) {
	removed := Action{Verb: verbRemoved, Target: action.Target, Reason: action.Reason, Shells: action.Shells, Agents: action.Agents}
	var refusal string
	var untracked *Action
	err := g.oneRemoval(func() error {
		var err error
		if refusal, err = g.Orca.Remove(ctx, action.Target, ""); err != nil {
			return err
		}
		if !strings.HasPrefix(refusal, CodeUnregistered) || !isDir(action.Target) {
			return nil
		}
		refused, err := g.Git.Remove(ctx, action.Target)
		if err != nil {
			return err
		}
		if refused != "" {
			untracked = &Action{Verb: verbKeep, Target: action.Target, Reason: "Orca no longer tracks it and ccx vcs worktree rm refused: " + refused}
			return nil
		}
		reasons := []string{"Orca no longer tracks it, so ccx vcs worktree rm removed it"}
		if action.Reason != "" {
			reasons = []string{action.Reason, reasons[0]}
		}
		via := removed
		via.Reason = strings.Join(reasons, ", ")
		untracked = &via
		return nil
	})
	if err != nil {
		return Action{}, err
	}
	if untracked != nil {
		return *untracked, nil
	}
	if refusal == "" {
		return removed, nil
	}
	if isDir(action.Target) {
		return Action{Verb: verbKeep, Target: action.Target, Reason: refusal}, nil
	}
	removed.Error = fmt.Sprintf("orca worktree rm answered %s, but its directory is gone", refusal)
	return removed, nil
}

func (g *GC) apply(ctx context.Context, action Action) (Action, error) {
	if action.Verb == verbKeep {
		return action, nil
	}
	if g.Pause.Held() {
		return g.Pause.Skip(action.Target, action.Verb), nil
	}
	closed := func() (Action, error) {
		refusal, err := g.Orca.Close(ctx, action.Target, action.Environment)
		if err != nil {
			return Action{}, err
		}
		if refusal != "" {
			return Action{Verb: verbKeep, Target: action.Target, Reason: refusal}, nil
		}
		return Action{Verb: verbClosed, Target: action.Target, Reason: action.Reason}, nil
	}
	switch action.Verb {
	case verbRelease:
		state, reason, err := g.Orca.Release(ctx, action.Target)
		if err != nil {
			return Action{}, err
		}
		if state == stateReleased || state == "already_released" {
			return Action{Verb: "released", Target: action.Target}, nil
		}
		return Action{Verb: verbKeep, Target: action.Target, Reason: strings.TrimSpace("worker-release " + state + " " + reason)}, nil
	case verbEnd:
		return closed()
	case verbClose:
		idle, err := g.Orca.Idle(ctx, action.Target, action.Environment)
		if err != nil {
			return Action{}, err
		}
		if idle != IdleQuiet {
			return Action{Verb: verbKeep, Target: action.Target, Reason: changed}, nil
		}
		same, err := g.sameSession(ctx, action)
		if err != nil {
			return Action{}, err
		}
		if !same {
			return Action{Verb: verbKeep, Target: action.Target, Reason: changed}, nil
		}
		if g.Pause.Held() {
			return g.Pause.Skip(action.Target, action.Verb), nil
		}
		return closed()
	case verbRemove:
		return g.applyRemove(ctx, action)
	case verbDrop:
		refusal, err := g.Orca.Remove(ctx, action.Target, "")
		if err != nil {
			return Action{}, err
		}
		if refusal == "" || strings.HasPrefix(refusal, CodeUnregistered) {
			return Action{Verb: verbDropped, Target: action.Target, Reason: action.Reason}, nil
		}
		return Action{Verb: verbKeep, Target: action.Target, Reason: refusal}, nil
	}
	return action, nil
}

type stopOutcome struct {
	action Action
	detail string
	ended  bool
}

func (g *GC) stop(ctx context.Context, dispatch string) (stopOutcome, error) {
	if g.DryRun {
		g.say("would stop " + dispatch)
		return stopOutcome{action: Action{Verb: verbStop, Target: dispatch}, ended: true}, nil
	}
	if g.Pause.Held() {
		skipped := g.Pause.Skip(dispatch, verbStop)
		g.say(skipped.Line())
		return stopOutcome{action: skipped}, nil
	}
	verb := "worker-stop"
	state, detail, err := g.Orca.Stop(ctx, dispatch)
	if err == nil && state == "" && (detail == CodeFenced || detail == CodeRuntimeDown) {
		verb = "worker-abandon"
		state, detail, err = g.Orca.Abandon(ctx, dispatch)
	}
	if err != nil {
		return stopOutcome{}, err
	}
	if state == "" {
		kept := Action{Verb: verbKeep, Target: dispatch, Reason: verb + " " + detail}
		g.say(kept.Line())
		return stopOutcome{action: kept}, nil
	}
	done := Action{Verb: verbStopped, Target: dispatch, Reason: state}
	g.say(done.Line())
	return stopOutcome{action: done, detail: detail, ended: true}, nil
}

func (g *GC) spriteInUse(ctx context.Context, worker Worker, sprite Sprite) (string, error) {
	idle, err := g.Orca.Idle(ctx, worker.Terminal, sprite.Environment)
	if err != nil {
		return "", err
	}
	switch idle {
	case IdleBusy:
		return fmt.Sprintf("terminal %s is %s", worker.Terminal, notIdle), nil
	case IdleUnknown:
		held, err := g.Fleet.Holder(ctx, sprite)
		if err != nil || held == "" {
			return "", err
		}
		return fmt.Sprintf("terminal %s is unreadable and %s", worker.Terminal, held), nil
	}
	return "", nil
}

func (g *GC) spriteAction(ctx context.Context, worker Worker, sprite Sprite, liveWorkspaces map[string]bool, ended map[string]string) (Action, []Checkout, error) {
	keep := func(reason string) (Action, []Checkout, error) {
		return Action{Verb: verbKeep, Target: sprite.Workspace, Reason: reason}, nil, nil
	}
	if liveWorkspaces[worker.Workspace] {
		return keep("serves an unsettled dispatch")
	}
	if _, done := ended[worker.Dispatch]; !done {
		held, err := g.spriteInUse(ctx, worker, sprite)
		if err != nil {
			return Action{}, nil, err
		}
		if held != "" {
			return keep(held)
		}
	}
	read, err := g.Fleet.Checkout(ctx, sprite)
	if err != nil {
		return Action{}, nil, err
	}
	reads, paths := []CheckoutRead{read}, []string{sprite.Root}
	for _, path := range read.Recorded {
		if path == sprite.Root {
			continue
		}
		clone, err := g.Fleet.Checkout(ctx, Sprite{Workspace: sprite.Workspace, Host: sprite.Host, Root: path, Environment: sprite.Environment})
		if err != nil {
			return Action{}, nil, err
		}
		reads, paths = append(reads, clone), append(paths, path)
	}
	var saved []Checkout
	for i, read := range reads {
		held := read.Refusal
		if read.Found != nil {
			if held, err = unsaved(ctx, *read.Found, g.Fleet.Remote(sprite)); err != nil {
				return Action{}, nil, err
			}
		}
		if held != "" && i > 0 {
			held += " in " + paths[i]
		}
		if held != "" {
			return Action{Verb: verbKeep, Target: sprite.Workspace, Reason: held, Unheld: true}, nil, nil
		}
		saved = append(saved, *read.Found)
	}
	return Action{Verb: verbDestroy, Target: sprite.Workspace, Reason: "dispatch " + worker.Dispatch}, saved, nil
}

func (g *GC) reap(ctx context.Context, action Action, worker Worker, sprite Sprite, ended map[string]string, saved []Checkout) (Action, error) {
	if action.Verb != verbDestroy {
		return action, nil
	}
	if g.Pause.Held() {
		return g.Pause.Skip(action.Target, action.Verb), nil
	}
	if _, done := ended[worker.Dispatch]; !done {
		held, err := g.spriteInUse(ctx, worker, sprite)
		if err != nil {
			return Action{}, err
		}
		if held != "" {
			return Action{Verb: verbKeep, Target: sprite.Workspace, Reason: changed + ": " + held}, nil
		}
	}
	if g.Pause.Held() {
		return g.Pause.Skip(action.Target, action.Verb), nil
	}
	refusal, err := g.Fleet.Prune(ctx, saved)
	if err != nil {
		return Action{}, err
	}
	if refusal != "" {
		return Action{Verb: verbKeep, Target: sprite.Workspace, Reason: "its work refs stayed: " + refusal, Unheld: true}, nil
	}
	refusal, err = g.Fleet.Destroy(ctx, sprite)
	if err != nil {
		return Action{}, err
	}
	if refusal != "" {
		return Action{Verb: verbKeep, Target: sprite.Workspace, Reason: refusal, Unheld: true}, nil
	}
	refusal, err = g.Orca.Forget(ctx, sprite.Environment)
	if err != nil {
		return Action{}, err
	}
	if refusal != "" {
		return Action{Verb: verbGone, Target: sprite.Workspace, Reason: fmt.Sprintf("%s, Orca environment %s kept: %s", action.Reason, sprite.Environment, refusal)}, nil
	}
	return Action{Verb: verbGone, Target: sprite.Workspace, Reason: action.Reason}, nil
}

func (g *GC) release(ctx context.Context, action Action, sprite Sprite) Action {
	switch {
	case !action.Unheld:
	case g.DryRun:
		action.Reason += "; would close its tunnel"
	case g.Pause.Held():
	default:
		if refusal := g.Fleet.CloseTunnel(ctx, sprite); refusal != "" {
			action.Reason += "; its tunnel stays: " + refusal
			break
		}
		action.Reason += "; no tunnel holds it"
	}
	return action
}

func (g *GC) act(ctx context.Context, action Action) (Action, error) {
	if g.DryRun {
		if action.Verb != verbRemove {
			return g.report(action), nil
		}
		blocked, err := g.removalBlocked(ctx, action)
		if err != nil {
			return Action{}, err
		}
		if blocked != nil {
			return g.report(*blocked), nil
		}
		return g.report(action), nil
	}
	done, err := g.apply(ctx, action)
	if err != nil {
		return Action{}, err
	}
	return g.report(done), nil
}

type tree struct {
	environment string
	path        string
}

type hostedSprite struct {
	sprite Sprite
	worker Worker
}

type Tally map[string]int

func (t Tally) String() string {
	if len(t) == 0 {
		return "nothing to reclaim"
	}
	verbs := slices.Sorted(maps.Keys(t))
	said := make([]string, len(verbs))
	for i, verb := range verbs {
		said[i] = fmt.Sprintf("%s=%d", verb, t[verb])
	}
	return strings.Join(said, " ")
}

func (g *GC) Run(ctx context.Context, run string, dispatches, ending []string) (Tally, error) {
	tally := Tally{}
	if !g.DryRun && g.Pause.Held() {
		skipped := g.Pause.Skip(run, "gc")
		g.say(skipped.Line())
		tally[skipped.Verb]++
		return tally, nil
	}
	ended := map[string]string{}
	for _, dispatch := range slices.Sorted(slices.Values(ending)) {
		outcome, err := g.stop(ctx, dispatch)
		if err != nil {
			return nil, err
		}
		tally[outcome.action.Verb]++
		if outcome.ended {
			ended[dispatch] = outcome.detail
		}
	}
	scope := map[string]bool{}
	for _, dispatch := range slices.Concat(dispatches, ending) {
		scope[dispatch] = true
	}
	workers, err := g.Orca.Workers(ctx, run)
	if err != nil {
		return nil, err
	}
	settled := func(worker Worker) bool {
		if g.Keep[worker.Dispatch] {
			return false
		}
		_, done := ended[worker.Dispatch]
		return worker.Settled() || done
	}
	liveTerminals, liveWorkspaces, liveTrees := map[string]bool{}, map[string]bool{}, map[tree]bool{}
	var finished []Worker
	for _, worker := range workers {
		if settled(worker) {
			if len(scope) == 0 || scope[worker.Dispatch] {
				finished = append(finished, worker)
			}
			continue
		}
		if worker.Terminal != "" {
			liveTerminals[worker.Terminal] = true
		}
		liveTrees[tree{worker.Environment, worker.Worktree}] = true
		liveWorkspaces[worker.Workspace] = true
	}
	sprites := map[SpriteKey]Sprite{}
	sessions := Sessions{}
	servers := map[string]bool{}
	if len(finished) > 0 {
		if sprites, err = g.Fleet.Sprites(); err != nil {
			return nil, err
		}
		if sessions, err = g.Orca.Sessions(ctx); err != nil {
			return nil, err
		}
		if servers, err = g.Orca.Servers(ctx); err != nil {
			return nil, err
		}
	}
	hostedBy := map[Sprite]Worker{}
	for _, worker := range finished {
		if sprite, ok := sprites[SpriteKey{worker.Workspace, worker.Terminal}]; ok {
			hostedBy[sprite] = worker
		}
	}
	hosted := make([]hostedSprite, 0, len(hostedBy))
	for sprite, worker := range hostedBy {
		hosted = append(hosted, hostedSprite{sprite, worker})
	}
	slices.SortFunc(hosted, func(a, b hostedSprite) int { return strings.Compare(a.sprite.Workspace, b.sprite.Workspace) })
	finished = slices.DeleteFunc(finished, func(worker Worker) bool {
		return slices.ContainsFunc(hosted, func(h hostedSprite) bool { return h.worker == worker }) || (worker.Environment != "" && !servers[worker.Environment])
	})
	spriteCheckouts := map[string]bool{}
	for key := range sprites {
		spriteCheckouts[key.Worktree] = true
	}
	var finishedTrees []tree
	for _, worker := range finished {
		if worker.Workspace == "" {
			tally[g.report(Action{Verb: verbKeep, Target: worker.Dispatch, Reason: "Orca records no workspace for it"}).Verb]++
		}
		candidate := tree{worker.Environment, worker.Worktree}
		if worker.Worktree != "" && !spriteCheckouts[worker.Workspace] && !liveTrees[candidate] && !slices.Contains(finishedTrees, candidate) {
			finishedTrees = append(finishedTrees, candidate)
		}
	}
	verdicts, err := parallel(finished, func(worker Worker) (*Action, error) {
		return terminalAction(ctx, worker, liveTerminals, g.Orca, ended, sessions)
	})
	if err != nil {
		return nil, err
	}
	closing := map[string]Action{}
	var closingOrder []string
	for _, verdict := range verdicts {
		if verdict == nil || (verdict.Verb != verbClose && verdict.Verb != verbEnd) {
			continue
		}
		if _, seen := closing[verdict.Target]; !seen {
			closingOrder = append(closingOrder, verdict.Target)
		}
		closing[verdict.Target] = *verdict
	}
	var immediate []Action
	for _, verdict := range verdicts {
		if verdict == nil {
			continue
		}
		if _, closes := closing[verdict.Target]; !closes || verdict.Verb == verbEnd {
			immediate = append(immediate, *verdict)
		}
	}
	acted := func(action Action) (Action, error) { return g.act(ctx, action) }
	count := func(done []Action) {
		for _, action := range done {
			tally[action.Verb]++
		}
	}
	terminals, err := parallel(immediate, acted)
	if err != nil {
		return nil, err
	}
	count(terminals)
	worktrees, stoppedTerminals, err := g.reclaimTrees(ctx, finishedTrees, closing)
	if err != nil {
		return nil, err
	}
	count(worktrees.done)
	for _, done := range worktrees.done {
		if (done.Verb == verbRemove || done.Verb == verbRemoved) && len(done.Agents) > 0 {
			tally[stopping] += len(done.Agents)
		}
	}
	var closes []Action
	finishedTerminals := map[string]bool{}
	for _, worker := range finished {
		if g.Finished[worker.Dispatch] {
			finishedTerminals[worker.Terminal] = true
		}
	}
	for _, target := range closingOrder {
		action := closing[target]
		if action.Verb != verbClose || stoppedTerminals[target] {
			continue
		}
		if held, kept := worktrees.unproven[action.Tree]; kept && (!finishedTerminals[target] || !worktrees.unsaved[action.Tree]) {
			tally[g.report(Action{Verb: verbKeep, Target: target, Reason: "its worktree is kept: " + held}).Verb]++
			continue
		}
		closes = append(closes, action)
	}
	closedTerminals, err := parallel(closes, acted)
	if err != nil {
		return nil, err
	}
	count(closedTerminals)
	reaped, err := bounded(spriteReads, hosted, func(pair hostedSprite) (Action, error) {
		action, saved, err := g.spriteAction(ctx, pair.worker, pair.sprite, liveWorkspaces, ended)
		if err != nil {
			return Action{}, err
		}
		if !g.DryRun {
			if action, err = g.reap(ctx, action, pair.worker, pair.sprite, ended, saved); err != nil {
				return Action{}, err
			}
		}
		return g.report(g.release(ctx, action, pair.sprite)), nil
	})
	if err != nil {
		return nil, err
	}
	count(reaped)
	return tally, nil
}

type reclaimed struct {
	done     []Action
	unproven map[tree]string
	unsaved  map[tree]bool
}

func (g *GC) reclaimTrees(ctx context.Context, candidates []tree, closing map[string]Action) (reclaimed, map[string]bool, error) {
	var local, paired []tree
	for _, candidate := range candidates {
		if candidate.environment == "" {
			local = append(local, candidate)
		} else {
			paired = append(paired, candidate)
		}
	}
	byPath := func(a, b tree) int {
		return cmp.Or(strings.Compare(a.environment, b.environment), strings.Compare(a.path, b.path))
	}
	slices.SortFunc(local, byPath)
	slices.SortFunc(paired, byPath)
	read, err := parallel(local, func(candidate tree) (CheckoutRead, error) { return g.Git.Checkout(ctx, candidate.path) })
	if err != nil {
		return reclaimed{}, nil, err
	}
	reached, err := parallel(paired, func(candidate tree) (CheckoutRead, error) {
		return g.Orca.PairedCheckout(ctx, candidate.path, candidate.environment, g.Git)
	})
	if err != nil {
		return reclaimed{}, nil, err
	}
	result := reclaimed{unproven: map[tree]string{}, unsaved: map[tree]bool{}}
	var checkouts []Checkout
	var missing []string
	for i, found := range read {
		switch {
		case found.Found != nil:
			checkouts = append(checkouts, *found.Found)
		case found.Refusal != "":
			result.unproven[local[i]] = found.Refusal
			result.done = append(result.done, g.report(Action{Verb: verbKeep, Target: local[i].path, Reason: found.Refusal}))
		default:
			missing = append(missing, local[i].path)
		}
	}
	for i, found := range reached {
		switch {
		case found.Found != nil:
			checkouts = append(checkouts, *found.Found)
		case found.Refusal != "":
			result.unproven[paired[i]] = found.Refusal
			result.done = append(result.done, g.report(Action{Verb: verbKeep, Target: paired[i].path, Reason: found.Refusal}))
		}
	}
	registered, err := parallel(missing, func(path string) (bool, error) { return g.Orca.Registered(ctx, path) })
	if err != nil {
		return reclaimed{}, nil, err
	}
	var drops []Action
	for i, known := range registered {
		if known {
			drops = append(drops, Action{Verb: verbDrop, Target: missing[i], Reason: "its directory is gone"})
		}
	}
	dropped, err := parallel(drops, func(action Action) (Action, error) { return g.act(ctx, action) })
	if err != nil {
		return reclaimed{}, nil, err
	}
	result.done = append(result.done, dropped...)
	repositories := map[string]bool{}
	for _, checkout := range checkouts {
		repositories[checkout.Repository] = true
	}
	names := sortedKeys(repositories)
	remotes, err := parallel(names, func(repository string) (Remote, error) { return g.Git.Remote(ctx, repository) })
	if err != nil {
		return reclaimed{}, nil, err
	}
	remoteOf := make(map[string]Remote, len(names))
	for i, name := range names {
		remoteOf[name] = remotes[i]
	}
	actions, err := parallel(checkouts, func(checkout Checkout) (Action, error) {
		listed, err := g.Orca.TerminalsIn(ctx, checkout.Path, checkout.Environment)
		if err != nil {
			return Action{}, err
		}
		action, err := worktreeAction(ctx, checkout, listed, closing, remoteOf[checkout.Repository], g.Orca.NowMS())
		if err != nil {
			return Action{}, err
		}
		return g.act(ctx, action)
	})
	if err != nil {
		return reclaimed{}, nil, err
	}
	result.done = append(result.done, actions...)
	for i, done := range actions {
		if done.Unproven {
			result.unproven[tree{checkouts[i].Environment, checkouts[i].Path}] = done.Reason
			result.unsaved[tree{checkouts[i].Environment, checkouts[i].Path}] = true
		}
	}
	stopped := map[string]bool{}
	for _, done := range result.done {
		if done.Verb == verbKeep && done.Reason == heldOpenInside {
			continue
		}
		for _, agent := range done.Agents {
			stopped[agent.Target] = true
		}
	}
	return result, stopped, nil
}

type Swept struct {
	Path string
}

func (s Swept) read() ([]string, error) {
	raw, err := os.ReadFile(s.Path)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var dispatches []string
	if err := json.Unmarshal(raw, &dispatches); err != nil {
		return nil, fmt.Errorf("%s: %w", s.Path, err)
	}
	return dispatches, nil
}

func (s Swept) record(dispatches []string) error {
	order, err := s.read()
	if err != nil {
		return err
	}
	order = slices.DeleteFunc(order, func(dispatch string) bool { return slices.Contains(dispatches, dispatch) })
	raw, err := json.Marshal(append(order, dispatches...))
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(s.Path), 0o750); err != nil {
		return err
	}
	staged := s.Path + ".tmp"
	if err := os.WriteFile(staged, raw, 0o600); err != nil {
		return err
	}
	return os.Rename(staged, s.Path)
}

func (g *GC) due(ctx context.Context, run string, ledger Swept) ([]string, error) {
	swept, err := ledger.read()
	if err != nil {
		return nil, err
	}
	workers, err := g.Orca.Workers(ctx, run)
	if err != nil {
		return nil, err
	}
	settled := map[string]bool{}
	var due []string
	for _, worker := range workers {
		if !worker.Settled() || g.Keep[worker.Dispatch] {
			continue
		}
		settled[worker.Dispatch] = true
		if !slices.Contains(swept, worker.Dispatch) {
			due = append(due, worker.Dispatch)
		}
	}
	for _, dispatch := range swept {
		if settled[dispatch] {
			due = append(due, dispatch)
		}
	}
	return due[:min(len(due), sweptBatch)], nil
}

func (g *GC) proveStale(ctx context.Context, run string, stale []string) ([]string, error) {
	if len(stale) == 0 {
		return nil, nil
	}
	workers, err := g.Orca.Workers(ctx, run)
	if err != nil {
		return nil, err
	}
	sessions, err := g.Orca.Sessions(ctx)
	if err != nil {
		return nil, err
	}
	candidates := slices.DeleteFunc(workers, func(worker Worker) bool {
		return !slices.Contains(stale, worker.Dispatch) || worker.Settled() || (worker.Environment != "" && !worker.HostGone)
	})
	proofs, err := parallel(candidates, func(worker Worker) (string, error) {
		if worker.HostGone {
			return worker.Dispatch, nil
		}
		held, err := g.staleHeld(ctx, worker, sessions)
		if err != nil {
			return "", err
		}
		if held != "" {
			g.report(Action{Verb: verbKeep, Target: worker.Dispatch, Reason: "stale but " + held})
			return "", nil
		}
		return worker.Dispatch, nil
	})
	if err != nil {
		return nil, err
	}
	return slices.DeleteFunc(proofs, func(dispatch string) bool { return dispatch == "" }), nil
}

func (g *GC) staleHeld(ctx context.Context, worker Worker, sessions Sessions) (string, error) {
	idle, err := g.Orca.Idle(ctx, worker.Terminal, "")
	if err != nil {
		return "", err
	}
	if idle != IdleQuiet {
		return "its screen does not read as an empty prompt", nil
	}
	session, unknown := sessions.agent(worker)
	if unknown != "" {
		return unknownID + unknown, nil
	}
	read, err := g.Git.Checkout(ctx, worker.Worktree)
	if err != nil {
		return "", err
	}
	if read.Found == nil {
		return "its worktree cannot be read", nil
	}
	if !g.Finished[worker.Dispatch] {
		remote, err := g.Git.Remote(ctx, read.Found.Repository)
		if err != nil {
			return "", err
		}
		held, err := unsaved(ctx, *read.Found, remote)
		if err != nil || held != "" {
			return held, err
		}
	}
	same, err := g.Orca.StillRuns(ctx, session)
	if err != nil {
		return "", err
	}
	if !same {
		return changed, nil
	}
	return "", nil
}

func Drift(planned, replanned string) []string {
	would := func(text string) map[string]bool {
		lines := map[string]bool{}
		for line := range strings.SplitSeq(text, "\n") {
			if strings.HasPrefix(line, "would ") {
				lines[line] = true
			}
		}
		return lines
	}
	before, after := would(planned), would(replanned)
	var moved []string
	for _, line := range sortedKeys(before) {
		if !after[line] {
			moved = append(moved, "- "+line)
		}
	}
	for _, line := range sortedKeys(after) {
		if !before[line] {
			moved = append(moved, "+ "+line)
		}
	}
	return moved
}

func (g *GC) Replan(ctx context.Context, run string, dispatches, ending []string) (string, error) {
	var out bytes.Buffer
	dry := &GC{Orca: g.Orca, Git: g.Git, Fleet: g.Fleet, Pause: g.Pause, Lock: g.Lock, Out: &out, DryRun: true, Keep: g.Keep, Finished: g.Finished}
	if _, err := dry.Run(ctx, run, dispatches, ending); err != nil {
		return "", err
	}
	return out.String(), nil
}
