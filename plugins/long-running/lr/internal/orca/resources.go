package orca

import (
	"context"
	"fmt"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/watch/screen"
)

const (
	statusDispatched = "dispatched"
	statusCompleted  = "completed"
	statusFailed     = "failed"
	stateReleased    = "released"
	external         = "external_terminal"
	closedByStop     = "closed_agent_terminal"
	connected        = "connected"
	disconnected     = "disconnected"
	staleHandle      = "stale handle"
	idleProbeMS      = 1500
	quietMS          = 10 * 60 * 1000
	notIdle          = "not idle: busy, prompt typed or asking"
	screenLines      = 40
	promptWindow     = 8
	claudePrompt     = "❯"
	codexPrompt      = "› Ask Codex to do anything"
	workerPageLimit  = "100"
	terminalPageSize = "1000"
)

var (
	busyMarkers = []string{"esc to interrupt", "ctrl+c to interrupt", "Running…", "background terminal running"}
	doneMarker  = regexp.MustCompile(`^✻ .* · done `)
)

type Worker struct {
	Dispatch       string
	Status         string
	TerminalState  string
	Ownership      string
	RetainedReason string
	Owner          string
	Terminal       string
	Workspace      string
	Worktree       string
	Environment    string
	HostGone       bool
}

func (w Worker) Settled() bool {
	return w.Status == statusCompleted || w.Status == statusFailed
}

type workerRow struct {
	Dispatch string `json:"dispatchId"`
	Status   string `json:"dispatchStatus"`
	State    string `json:"terminalState"`
	Terminal string `json:"agentTerminalHandle"`
	Resource *struct {
		Worktree  string `json:"worktreeId"`
		Ownership string `json:"ownershipState"`
		Retained  string `json:"retainedReason"`
		Owner     string `json:"ownerDispatchId"`
	} `json:"resource"`
	Projection struct {
		Workspace *struct {
			ID string `json:"id"`
		} `json:"workspace"`
		Host *struct {
			Kind string `json:"kind"`
			ID   string `json:"id"`
		} `json:"host"`
		Liveness struct {
			Reason string `json:"reason"`
		} `json:"liveness"`
	} `json:"projection"`
}

func (r workerRow) worker() Worker {
	worker := Worker{Dispatch: r.Dispatch, Status: r.Status, TerminalState: r.State, Terminal: r.Terminal, HostGone: r.Projection.Liveness.Reason == hostUnavailable}
	if r.Resource != nil {
		worker.Ownership = r.Resource.Ownership
		worker.RetainedReason = r.Resource.Retained
		worker.Owner = r.Resource.Owner
		worker.Workspace = r.Resource.Worktree
	}
	if worker.Workspace == "" && r.Projection.Workspace != nil {
		worker.Workspace = r.Projection.Workspace.ID
	}
	if _, tree, found := strings.Cut(worker.Workspace, "::"); found {
		worker.Worktree = tree
	}
	if host := r.Projection.Host; host != nil && host.Kind == "remote" {
		worker.Environment = host.ID
	}
	return worker
}

type Terminal struct {
	Handle       string `json:"handle"`
	Agent        string `json:"agentIdentity"`
	Title        string `json:"title"`
	Worktree     string `json:"worktreePath"`
	LastOutputMS int64  `json:"lastOutputAt"`
	Tab          string `json:"tabId"`
	Leaf         string `json:"leafId"`
	Connected    bool   `json:"connected"`
}

func (t Terminal) pane() string {
	return t.Tab + ":" + t.Leaf
}

func (t Terminal) idleShell(worktree string, nowMS int64) bool {
	title := strings.TrimRight(t.Title, "/")
	title = title[strings.LastIndex(title, "/")+1:]
	return t.Agent == "" && (title == "" || title == filepath.Base(worktree)) && nowMS-t.LastOutputMS > quietMS
}

type Idleness int

const (
	IdleUnknown Idleness = iota
	IdleBusy
	IdleQuiet
)

type TerminalList struct {
	Terminals []Terminal
	Refusal   string
	Untracked bool
}

type terminalShow struct {
	Terminal struct {
		Connected    bool     `json:"connected"`
		Worktree     string   `json:"worktreePath"`
		Tab          string   `json:"tabId"`
		Tail         []string `json:"tail"`
		LastOutputMS int64    `json:"lastOutputAt"`
	} `json:"terminal"`
}

func idleScreen(tail []string) bool {
	lines := make([]string, 0, len(tail))
	for _, line := range tail {
		if screen.Running(line) {
			return false
		}
		for _, marker := range busyMarkers {
			if strings.Contains(line, marker) {
				return false
			}
		}
		if strings.TrimFunc(line, func(r rune) bool { return r == '─' || r == '━' || unicode.IsSpace(r) }) != "" {
			lines = append(lines, strings.TrimSpace(line))
		}
	}
	prompt := -1
	for index := len(lines) - 1; index >= max(len(lines)-promptWindow, 0); index-- {
		if lines[index] == claudePrompt || strings.HasPrefix(lines[index], codexPrompt) {
			prompt = index
			break
		}
	}
	if prompt < 0 {
		return false
	}
	for index := prompt - 1; index >= 0; index-- {
		if !doneMarker.MatchString(lines[index]) {
			return !strings.HasSuffix(lines[index], "?")
		}
	}
	return true
}

func (c Client) Workers(ctx context.Context, run string) ([]Worker, error) {
	var workers []Worker
	var cursor []string
	for {
		args := append([]string{orchestration, "worker-list", "--run", run, "--include-remote", "--limit", workerPageLimit}, cursor...)
		reply, err := c.Call(ctx, args...)
		if err != nil {
			return nil, err
		}
		var page struct {
			Workers []workerRow `json:"workers"`
			Page    *struct {
				Next string `json:"nextCursor"`
			} `json:"page"`
		}
		if err := reply.Into(&page); err != nil {
			return nil, err
		}
		for _, row := range page.Workers {
			workers = append(workers, row.worker())
		}
		if page.Page == nil || page.Page.Next == "" {
			return workers, nil
		}
		cursor = []string{"--cursor", page.Page.Next}
	}
}

func (c Client) show(ctx context.Context, terminal, environment string) (Reply, error) {
	return c.Call(ctx, append([]string{"terminal", "show", "--terminal", terminal}, on(environment)...)...)
}

func idleFromRefusal(reply Reply) Idleness {
	switch reply.Error.Code {
	case CodeStaleHandle, CodeRuntimeDown:
		return IdleUnknown
	}
	return IdleBusy
}

func timedOutInSilence(probe Reply, lastOutputMS int64) bool {
	return probe.Error.Code == codeTimedOut && lastOutputMS > 0 && NowMS()-lastOutputMS > quietMS
}

func (c Client) Idle(ctx context.Context, terminal, environment string) (Idleness, error) {
	reply, err := c.show(ctx, terminal, environment)
	if err != nil {
		return IdleUnknown, err
	}
	if !reply.OK {
		return idleFromRefusal(reply), nil
	}
	var shown terminalShow
	if err := reply.Into(&shown); err != nil {
		return IdleUnknown, err
	}
	if !shown.Terminal.Connected {
		return IdleUnknown, nil
	}
	probe, err := c.Call(ctx, append([]string{"terminal", "wait", "--terminal", terminal, "--for", "tui-idle", "--timeout-ms", strconv.Itoa(idleProbeMS)}, on(environment)...)...)
	if err != nil {
		return IdleUnknown, err
	}
	if !probe.OK && !timedOutInSilence(probe, shown.Terminal.LastOutputMS) {
		return idleFromRefusal(probe), nil
	}
	if probe.OK {
		var waited struct {
			Wait struct {
				Satisfied bool `json:"satisfied"`
			} `json:"wait"`
		}
		if err := probe.Into(&waited); err != nil {
			return IdleUnknown, err
		}
		if !waited.Wait.Satisfied {
			return IdleBusy, nil
		}
	}
	shownScreen, err := c.Call(ctx, append([]string{"terminal", "read", "--terminal", terminal, "--screen", "--limit", strconv.Itoa(screenLines)}, on(environment)...)...)
	if err != nil {
		return IdleUnknown, err
	}
	if !shownScreen.OK {
		return idleFromRefusal(shownScreen), nil
	}
	var read terminalShow
	if err := shownScreen.Into(&read); err != nil {
		return IdleUnknown, err
	}
	if idleScreen(read.Terminal.Tail) {
		return IdleQuiet, nil
	}
	return IdleBusy, nil
}

func (c Client) Presence(ctx context.Context, terminal, environment string) (string, error) {
	reply, err := c.show(ctx, terminal, environment)
	if err != nil {
		return "", err
	}
	if !reply.OK {
		return reply.Error.Code, nil
	}
	var shown terminalShow
	if err := reply.Into(&shown); err != nil {
		return "", err
	}
	if shown.Terminal.Connected {
		return connected, nil
	}
	return disconnected, nil
}

func (c Client) Terminals(ctx context.Context) ([]Terminal, error) {
	reply, err := c.Call(ctx, "terminal", "list", "--limit", terminalPageSize)
	if err != nil {
		return nil, err
	}
	var listed struct {
		Terminals []Terminal `json:"terminals"`
		Truncated bool       `json:"truncated"`
	}
	if err := reply.Into(&listed); err != nil {
		return nil, err
	}
	if listed.Truncated {
		return nil, fmt.Errorf("orca terminal list stopped at %s terminals, so it proves no terminal absent", terminalPageSize)
	}
	return listed.Terminals, nil
}

func (c Client) Runs(ctx context.Context) ([]Run, error) {
	var runs []Run
	var cursor []string
	for {
		reply, err := c.Call(ctx, append([]string{orchestration, "run-list", "--limit", workerPageLimit}, cursor...)...)
		if err != nil {
			return nil, err
		}
		var page struct {
			Runs []Run  `json:"runs"`
			Next string `json:"nextCursor"`
		}
		if err := reply.Into(&page); err != nil {
			return nil, err
		}
		runs = append(runs, page.Runs...)
		if page.Next == "" {
			return runs, nil
		}
		cursor = []string{"--cursor", page.Next}
	}
}

func (c Client) TerminalsIn(ctx context.Context, worktree, environment string) (TerminalList, error) {
	reply, err := c.Call(ctx, append([]string{"terminal", "list", "--worktree", "path:" + worktree, "--limit", terminalPageSize}, on(environment)...)...)
	if err != nil {
		return TerminalList{}, err
	}
	if !reply.OK {
		if reply.Error.Code == CodeUnregistered {
			return TerminalList{Untracked: true}, nil
		}
		return TerminalList{Refusal: "orca terminal list " + reply.Error.Code}, nil
	}
	var listed struct {
		Terminals []Terminal `json:"terminals"`
	}
	if err := reply.Into(&listed); err != nil {
		return TerminalList{}, err
	}
	live := make([]Terminal, 0, len(listed.Terminals))
	for _, terminal := range listed.Terminals {
		if terminal.Connected {
			live = append(live, terminal)
		}
	}
	return TerminalList{Terminals: live}, nil
}

func (c Client) fence(ctx context.Context, verb, dispatch string) (string, string, error) {
	reply, err := c.Call(ctx, orchestration, verb, "--dispatch", dispatch)
	if err != nil {
		return "", "", err
	}
	if !reply.OK {
		return "", reply.Error.Code, nil
	}
	var fenced struct {
		State   string `json:"state"`
		Process string `json:"processAction"`
	}
	if err := reply.Into(&fenced); err != nil {
		return "", "", err
	}
	return fenced.State, fenced.Process, nil
}

func (c Client) Stop(ctx context.Context, dispatch string) (string, string, error) {
	return c.fence(ctx, "worker-stop", dispatch)
}

func (c Client) Abandon(ctx context.Context, dispatch string) (string, string, error) {
	return c.fence(ctx, "worker-abandon", dispatch)
}

func (c Client) Release(ctx context.Context, dispatch string) (string, string, error) {
	reply, err := c.Call(ctx, orchestration, "worker-release", "--dispatch", dispatch)
	if err != nil {
		return "", "", err
	}
	var released struct {
		State  string `json:"state"`
		Reason string `json:"reason"`
	}
	if err := reply.Into(&released); err != nil {
		return "", "", err
	}
	return released.State, released.Reason, nil
}

func (c Client) Close(ctx context.Context, terminal, environment string) (string, error) {
	reply, err := c.show(ctx, terminal, environment)
	if err != nil {
		return "", err
	}
	var shown terminalShow
	if err := reply.Into(&shown); err != nil {
		return "", err
	}
	listed, err := c.TerminalsIn(ctx, shown.Terminal.Worktree, environment)
	if err != nil {
		return "", err
	}
	if listed.Refusal != "" {
		return listed.Refusal, nil
	}
	shared := listed.Untracked
	for _, other := range listed.Terminals {
		shared = shared || (other.Tab == shown.Terminal.Tab && other.Handle != terminal)
	}
	args := []string{"terminal", "close", "--terminal", terminal}
	if !shared {
		args = append(args, "--tab")
	}
	done, err := c.Call(ctx, append(args, on(environment)...)...)
	if err != nil {
		return "", err
	}
	if done.OK {
		return "", nil
	}
	if done.Error.Code == CodeStaleHandle {
		presence, err := c.Presence(ctx, terminal, environment)
		if err != nil {
			return "", err
		}
		if presence == disconnected {
			return "", nil
		}
	}
	return done.Error.Code, nil
}

func (c Client) refused(ctx context.Context, args ...string) (string, error) {
	done, err := c.Call(ctx, args...)
	if err != nil {
		return "", err
	}
	if done.OK {
		return "", nil
	}
	return done.Refusal(), nil
}

func (c Client) Remove(ctx context.Context, worktree, environment string) (string, error) {
	return c.refused(ctx, append([]string{"worktree", "rm", "--worktree", "path:" + worktree}, on(environment)...)...)
}

func (c Client) Forget(ctx context.Context, environment string) (string, error) {
	return c.refused(ctx, "environment", "rm", "--environment", environment)
}

func (c Client) Servers(ctx context.Context) (map[string]bool, error) {
	reply, err := c.Call(ctx, "environment", "list")
	if err != nil {
		return nil, err
	}
	var listed struct {
		Environments []struct {
			ID string `json:"id"`
		} `json:"environments"`
	}
	if err := reply.Into(&listed); err != nil {
		return nil, err
	}
	saved := make(map[string]bool, len(listed.Environments))
	for _, environment := range listed.Environments {
		saved[environment.ID] = true
	}
	return saved, nil
}

func (c Client) Registered(ctx context.Context, worktree string) (bool, error) {
	shown, err := c.Call(ctx, "worktree", "show", "--worktree", "path:"+worktree)
	if err != nil {
		return false, err
	}
	return shown.OK || shown.Error.Code != CodeUnregistered, nil
}

func (c Client) PairedCheckout(ctx context.Context, worktree, environment string, git Repositories) (CheckoutRead, error) {
	shown, err := c.Call(ctx, "worktree", "show", "--worktree", "path:"+worktree, "--environment", environment)
	if err != nil {
		return CheckoutRead{}, err
	}
	if !shown.OK {
		if shown.Error.Code == CodeUnregistered {
			return CheckoutRead{}, nil
		}
		return CheckoutRead{Refusal: "orca worktree show " + shown.Error.Code}, nil
	}
	var tree struct {
		Worktree struct {
			Head   string `json:"head"`
			Branch string `json:"branch"`
		} `json:"worktree"`
	}
	if err := shown.Into(&tree); err != nil {
		return CheckoutRead{}, err
	}
	repository, err := git.Repository(ctx)
	if err != nil {
		return CheckoutRead{}, err
	}
	onTrunk, err := git.OnTrunk(ctx, tree.Worktree.Head, repository)
	if err != nil {
		return CheckoutRead{}, err
	}
	return CheckoutRead{Found: &Checkout{
		Path:        worktree,
		Branch:      strings.TrimPrefix(tree.Worktree.Branch, "refs/heads/"),
		Head:        tree.Worktree.Head,
		OnTrunk:     onTrunk,
		Repository:  repository,
		Environment: environment,
	}}, nil
}

func NowMS() int64 {
	return time.Now().UnixMilli()
}

func describeExit(stderr string, fallback string, args ...any) string {
	lines := strings.Split(strings.TrimSpace(stderr), "\n")
	if last := lines[len(lines)-1]; last != "" {
		return last
	}
	return fmt.Sprintf(fallback, args...)
}
