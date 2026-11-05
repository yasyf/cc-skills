package orca

import (
	"bytes"
	"context"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"testing"

	"github.com/yasyf/cc-skills/plugins/long-running/lr/internal/shell"
)

const (
	fakeNow    = int64(10_000_000_000)
	testWT     = "/w/monorepo/v3-lane-base"
	testWT2    = "/w/monorepo/v3-lane-next"
	repository = "/w/monorepo/.git"
	headA      = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	headB      = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
	headC      = "cccccccccccccccccccccccccccccccccccccccc"
	headD      = "dddddddddddddddddddddddddddddddddddddddd"
	spriteWT   = "repo_abc::/home/sprite/monorepo"
	boxEnv     = "env_box"
	boxWT      = "/home/agent/orca/workspaces/monorepo/box-lane"
)

var testSprite = Sprite{"pool-abc", "pool-abc", "/home/sprite/monorepo", "env_abc"}

type rowOption func(*Worker)

func status(s string) rowOption    { return func(w *Worker) { w.Status = s } }
func state(s string) rowOption     { return func(w *Worker) { w.TerminalState = s } }
func terminal(s string) rowOption  { return func(w *Worker) { w.Terminal = s } }
func ownership(s string) rowOption { return func(w *Worker) { w.Ownership = s } }
func retained(s string) rowOption  { return func(w *Worker) { w.RetainedReason = s } }
func in(path string) rowOption {
	return func(w *Worker) { w.Workspace, w.Worktree = "repo::"+path, path }
}
func onHost(env string) rowOption { return func(w *Worker) { w.Environment = env } }
func ownedTerminal() rowOption {
	return func(w *Worker) { w.Ownership, w.RetainedReason = "owned", "" }
}

func row(dispatch string, options ...rowOption) Worker {
	worker := Worker{
		Dispatch:       dispatch,
		Status:         statusCompleted,
		TerminalState:  "retained",
		Ownership:      "external",
		RetainedReason: external,
		Owner:          dispatch,
		Terminal:       "term_" + dispatch,
		Workspace:      "repo::" + testWT,
		Worktree:       testWT,
	}
	for _, option := range options {
		option(&worker)
	}
	return worker
}

func spriteRow(dispatch string, options ...rowOption) Worker {
	worker := Worker{Dispatch: dispatch, Status: statusCompleted, TerminalState: "retained", Terminal: "term_sprite", Workspace: spriteWT, Worktree: "/home/sprite/monorepo"}
	for _, option := range options {
		option(&worker)
	}
	return worker
}

func boxRow(dispatch string, options ...rowOption) Worker {
	worker := Worker{Dispatch: dispatch, Status: statusCompleted, TerminalState: "retained", Terminal: "term_box", Workspace: "repo::" + boxWT, Worktree: boxWT, Environment: boxEnv}
	for _, option := range options {
		option(&worker)
	}
	return worker
}

func idleShellTerminal(handle string) Terminal {
	return Terminal{Handle: handle, Title: "~/.o/w/m/m/" + filepath.Base(testWT), LastOutputMS: fakeNow - 3_600_000}
}

func agentTerminal(handle string) Terminal {
	return Terminal{Handle: handle, Agent: "claude", Title: "✳ lane"}
}

func checkout(path string, options ...func(*Checkout)) Checkout {
	found := Checkout{Path: path, Branch: "v3/lane", Head: headA, Repository: repository}
	for _, option := range options {
		option(&found)
	}
	return found
}

func withHead(head string) func(*Checkout) { return func(c *Checkout) { c.Head = head } }
func dirtyTree(c *Checkout)                { c.Dirty = true }
func onEnv(env string) func(*Checkout)     { return func(c *Checkout) { c.Environment = env } }

type fenceReply struct {
	state  string
	detail string
	after  func(*Worker)
}

type fakeOrca struct {
	mu            sync.Mutex
	rows          []Worker
	idle          map[string][]Idleness
	terminals     map[string][]Terminal
	listings      []TerminalList
	refusal       string
	closeRefusal  string
	stops         map[string]fenceReply
	abandons      map[string]fenceReply
	holders       map[string]string
	panes         map[string]map[string]bool
	stale         map[string]bool
	disconnected  map[string]bool
	known         map[string]bool
	forgotten     map[string]bool
	paired        map[string]CheckoutRead
	calls         [][]string
	onRemove      func(string)
	onStop        func()
	onHolder      func()
	beforeListing func(string)
	sessions      Sessions
	restarted     map[string]bool
}

func newOrca(rows ...Worker) *fakeOrca {
	return &fakeOrca{
		rows:         rows,
		idle:         map[string][]Idleness{},
		terminals:    map[string][]Terminal{},
		stops:        map[string]fenceReply{},
		abandons:     map[string]fenceReply{},
		holders:      map[string]string{},
		panes:        map[string]map[string]bool{},
		stale:        map[string]bool{},
		disconnected: map[string]bool{},
		known:        map[string]bool{},
		paired:       map[string]CheckoutRead{},
	}
}

func (f *fakeOrca) record(call ...string) {
	f.calls = append(f.calls, slices.DeleteFunc(call, func(part string) bool { return part == "" }))
}

func (f *fakeOrca) Workers(context.Context, string) ([]Worker, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return slices.Clone(f.rows), nil
}

func (f *fakeOrca) Idle(_ context.Context, terminal, _ string) (Idleness, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	reads, ok := f.idle[terminal]
	if !ok {
		return IdleQuiet, nil
	}
	if len(reads) > 1 {
		f.idle[terminal] = reads[1:]
	}
	return reads[0], nil
}

func (f *fakeOrca) Presence(_ context.Context, terminal, _ string) (string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	switch {
	case f.stale[terminal]:
		return CodeStaleHandle, nil
	case f.disconnected[terminal]:
		return disconnected, nil
	}
	return connected, nil
}

func (f *fakeOrca) TerminalsIn(_ context.Context, worktree, _ string) (TerminalList, error) {
	if f.beforeListing != nil {
		f.beforeListing(worktree)
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.listings) > 0 {
		listed := f.listings[0]
		if len(f.listings) > 1 {
			f.listings = f.listings[1:]
		}
		return listed, nil
	}
	return TerminalList{Terminals: slices.Clone(f.terminals[worktree])}, nil
}

func (f *fakeOrca) fence(verb, dispatch string, replies map[string]fenceReply) (string, string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.record(verb, dispatch)
	reply := replies[dispatch]
	if reply.after != nil {
		for i := range f.rows {
			if f.rows[i].Dispatch == dispatch {
				reply.after(&f.rows[i])
			}
		}
	}
	return reply.state, reply.detail, nil
}

func (f *fakeOrca) Stop(_ context.Context, dispatch string) (string, string, error) {
	if f.onStop != nil {
		f.onStop()
	}
	return f.fence("stop", dispatch, f.stops)
}

func (f *fakeOrca) Abandon(_ context.Context, dispatch string) (string, string, error) {
	return f.fence("abandon", dispatch, f.abandons)
}

func (f *fakeOrca) Release(_ context.Context, dispatch string) (string, string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.record("release", dispatch)
	return stateReleased, "", nil
}

func (f *fakeOrca) Close(_ context.Context, terminal, environment string) (string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.record("close", terminal, environment)
	if f.closeRefusal != "" {
		return f.closeRefusal, nil
	}
	for path, listed := range f.terminals {
		f.terminals[path] = slices.DeleteFunc(slices.Clone(listed), func(t Terminal) bool { return t.Handle == terminal })
	}
	return "", nil
}

func (f *fakeOrca) Remove(_ context.Context, worktree, environment string) (string, error) {
	if f.onRemove != nil {
		f.onRemove(worktree)
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	f.record("remove", worktree, environment)
	return f.refusal, nil
}

func (f *fakeOrca) PairedCheckout(_ context.Context, worktree, _ string, _ Repositories) (CheckoutRead, error) {
	return f.paired[worktree], nil
}

func (f *fakeOrca) Registered(_ context.Context, worktree string) (bool, error) {
	return f.known[worktree], nil
}

func (f *fakeOrca) Forget(_ context.Context, environment string) (string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.record("forget", environment)
	return "", nil
}

func (f *fakeOrca) Servers(context.Context) (map[string]bool, error) {
	saved := map[string]bool{}
	for _, worker := range f.rows {
		saved[worker.Environment] = !f.forgotten[worker.Environment]
	}
	return saved, nil
}

func (f *fakeOrca) NowMS() int64 { return fakeNow }

func (f *fakeOrca) Holder(_ context.Context, worktree string, panes map[string]bool) (string, error) {
	if f.onHolder != nil {
		f.onHolder()
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	f.panes[worktree] = panes
	return f.holders[worktree], nil
}

func liveSession(worker Worker) []Session {
	env := " ORCA_WORKTREE_ID=" + worker.Workspace
	return []Session{
		{PID: "100", PPID: "90", PGID: "100", Start: "Mon Nov 2 23:49:37 2026", Command: "-/opt/homebrew/bin/fish -l" + env},
		{PID: "101", PPID: "100", PGID: "101", Start: "Mon Nov 2 23:49:38 2026", Command: "claude --allow-dangerously-skip-permissions" + env},
	}
}

func (f *fakeOrca) Sessions(context.Context) (Sessions, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.sessions != nil {
		return f.sessions, nil
	}
	index := Sessions{}
	for _, worker := range f.rows {
		index[worker.Terminal] = liveSession(worker)
	}
	return index, nil
}

func (f *fakeOrca) StillRuns(_ context.Context, session Session) (bool, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	return !f.restarted[session.PID], nil
}

func (f *fakeOrca) count(call ...string) int {
	return len(slices.DeleteFunc(slices.Clone(f.calls), func(got []string) bool { return !slices.Equal(got, call) }))
}

type landedSet map[string]bool

func (l landedSet) Landed(_ context.Context, checkout Checkout) (bool, error) {
	return l[checkout.Head], nil
}

func defaultRemote() Remote {
	return Remote{Heads: map[string]bool{headA: true}, Landed: landedSet{}}
}

type fakeGit struct {
	mu       sync.Mutex
	reads    map[string]CheckoutRead
	remotes  map[string]Remote
	refusal  string
	removed  []string
	delegate func(context.Context, string) (CheckoutRead, bool, error)
}

func newGit(checkouts ...Checkout) *fakeGit {
	git := &fakeGit{reads: map[string]CheckoutRead{}, remotes: map[string]Remote{repository: defaultRemote()}}
	for _, found := range checkouts {
		git.reads[found.Path] = CheckoutRead{Found: &found}
	}
	return git
}

func (g *fakeGit) Checkout(ctx context.Context, path string) (CheckoutRead, error) {
	if g.delegate != nil {
		if read, handled, err := g.delegate(ctx, path); handled {
			return read, err
		}
	}
	return g.reads[path], nil
}

func (*fakeGit) Repository(context.Context) (string, error) { return repository, nil }

func (*fakeGit) OnTrunk(context.Context, string, string) (bool, error) { return false, nil }

func (g *fakeGit) Remote(_ context.Context, name string) (Remote, error) { return g.remotes[name], nil }

func (g *fakeGit) Remove(_ context.Context, worktree string) (string, error) {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.removed = append(g.removed, worktree)
	return g.refusal, nil
}

type fakeFleet struct {
	mu        sync.Mutex
	hosted    map[SpriteKey]Sprite
	reads     map[string]CheckoutRead
	holders   []string
	refusal   string
	destroyed []string
	unheld    []string
	tunnel    string
}

func (f *fakeFleet) CloseTunnel(_ context.Context, sprite Sprite) string {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.unheld = append(f.unheld, sprite.Workspace)
	return f.tunnel
}

func (f *fakeFleet) Sprites() (map[SpriteKey]Sprite, error) {
	if f.hosted == nil {
		return map[SpriteKey]Sprite{}, nil
	}
	return f.hosted, nil
}

func (*fakeFleet) Remote(Sprite) Remote { return defaultRemote() }

func (f *fakeFleet) Checkout(_ context.Context, sprite Sprite) (CheckoutRead, error) {
	if read, ok := f.reads[sprite.Workspace]; ok {
		return read, nil
	}
	found := checkout(sprite.Root)
	return CheckoutRead{Found: &found}, nil
}

func (f *fakeFleet) Holder(context.Context, Sprite) (string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.holders) == 0 {
		return "", nil
	}
	held := f.holders[0]
	f.holders = f.holders[1:]
	return held, nil
}

func (f *fakeFleet) Destroy(_ context.Context, sprite Sprite) (string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.destroyed = append(f.destroyed, sprite.Workspace)
	return f.refusal, nil
}

type gcOptions struct {
	dryRun     bool
	dispatches []string
	done       []string
	fleet      *fakeFleet
	pause      *Pause
	keep       map[string]bool
	finished   map[string]bool
}

func unpaused(t *testing.T) Pause {
	t.Helper()
	resumed := filepath.Join(t.TempDir(), "resumed")
	if err := os.WriteFile(resumed, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	return Pause{Resumed: resumed}
}

func runGC(t *testing.T, orca API, git Repositories, options gcOptions) (Tally, string) {
	t.Helper()
	var fleet SpriteFleet = &fakeFleet{}
	if options.fleet != nil {
		fleet = options.fleet
	}
	pause := unpaused(t)
	if options.pause != nil {
		pause = *options.pause
	}
	var out bytes.Buffer
	gc := &GC{Orca: orca, Git: git, Fleet: fleet, Pause: pause, Lock: filepath.Join(t.TempDir(), "remove.lock"), Out: &out, DryRun: options.dryRun, Keep: options.keep, Finished: options.finished}
	tally, err := gc.Run(context.Background(), "run_x", options.dispatches, options.done)
	if err != nil {
		t.Fatal(err)
	}
	return tally, out.String()
}

type scripted struct {
	mu    sync.Mutex
	calls [][]string
	envs  [][]string
	reply func(argv []string) (string, string, int)
}

func (s *scripted) Run(_ context.Context, c shell.Command) (shell.Result, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.calls = append(s.calls, c.Argv)
	s.envs = append(s.envs, c.Env)
	stdout, stderr, code := s.reply(c.Argv)
	result := shell.Result{Code: code, Stdout: stdout, Stderr: stderr}
	if code != 0 {
		return result, &shell.ExitError{Argv: c.Argv, Code: code, Stderr: stderr}
	}
	return result, nil
}

func joined(argv []string) string {
	return strings.Join(argv, " ")
}
