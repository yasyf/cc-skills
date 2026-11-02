package runner

import (
	"crypto/sha1"
	"encoding/hex"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/yasyf/cc-skills/plugins/long-running/desk/pyjson"
	"github.com/yasyf/cc-skills/plugins/long-running/desk/shell"
)

const (
	Trunk         = "dev"
	RulingCards   = "cards"
	RulingRounds  = "cards/rounds"
	RefreshEvery  = 2 * time.Minute
	QuotaFloor    = 1000
	QuotaUrgent   = 1500
	TopCallers    = 5
	PythonEnv     = "LONG_RUNNING_PYTHON"
	DriveEnv      = "CLAUDE_LONG_RUNNING_DRIVE"
	scriptsSubdir = "skills/long-running/scripts"
)

var quotaResources = []string{"core", "graphql"}

// Landing is the Go port of desk-runner.py's Landing; each method still to port panics naming the Python it ports.
type Landing struct {
	runner     *Runner
	shell      shell.Shell
	config     map[string]any
	repo       string
	ledger     string
	checkout   string
	statusCard bool
	ledgerRows *pyjson.Object
	rowHeads   map[string]string
	heldNow    []string
	stopped    chan struct{}
	stopping   sync.Once
	refreshing atomic.Bool
	keepers    sync.WaitGroup
}

// NewLanding is Landing.__init__.
func NewLanding(r *Runner, config map[string]any) *Landing {
	statusCard, _ := config["status_card"].(bool)
	l := &Landing{
		runner:     r,
		shell:      r.Shell,
		config:     config,
		repo:       config["repo"].(string),
		ledger:     config["ledger"].(string),
		checkout:   filepath.Clean(expand(config["checkout"].(string), r.Shell)),
		statusCard: statusCard,
		ledgerRows: pyjson.NewObject(),
		rowHeads:   map[string]string{},
		stopped:    make(chan struct{}),
	}
	check(r.Book.Ensure(r.ctx, LandingLane, LandingLane))
	return l
}

func scripts(sh shell.Shell) string {
	return filepath.Join(sh.Getenv(PluginRootEnv), scriptsSubdir)
}

func (l *Landing) ledgerPy(argv ...string) shell.Done {
	return l.shell.Run(append([]string{l.shell.Getenv(PythonEnv), filepath.Join(scripts(l.shell), "ledger.py"), "-C", l.checkout}, argv...), nil, map[string]string{DriveEnv: l.runner.Config.Drive}, "")
}

// Rows is Landing.rows: every ledger row by PR, nil when the ledger cannot be listed.
func (l *Landing) Rows() *pyjson.Object {
	done := l.ledgerPy("list", "--ledger", l.ledger, "--json")
	if done.Code != 0 {
		return nil
	}
	rows := pyjson.NewObject()
	heads := map[string]string{}
	for _, item := range must(pyjson.Loads(done.Out)).([]any) {
		row := item.(*pyjson.Object)
		pr := member(row, "pr").(string)
		rows.Set(pr, row)
		head, _ := member(row, "head").(string)
		heads[pr] = head
	}
	l.rowHeads, l.ledgerRows = heads, rows
	return rows
}

// Run is Landing.run.
func (l *Landing) Run() {
	l.askRulings()
	defer l.runner.PassIO.Phase("pass-cleanup", func() {})
	l.gatePass()
}

func (l *Landing) askRulings() {
	state := filepath.Dir(l.runner.Config.Source)
	queued := must(filepath.Glob(filepath.Join(state, RulingCards, "*.json")))
	if len(queued) == 0 {
		return
	}
	number := len(must(filepath.Glob(filepath.Join(state, RulingRounds, "round-*.json")))) + 1
	l.runner.Escalate(
		fmt.Sprintf("ruling-ask:round-%d", number),
		"RULING-ASK",
		fmt.Sprintf("round-%d", number),
		fmt.Sprintf("%d ruling confirmations queue | when the owner can take a round, run `rulings.py round --drive %s`:", len(queued), l.runner.Config.Drive)+
			fmt.Sprintf(" it puts at most ten of them on the live cc-present board as round %d, foundational rows first, never as an AskUserQuestion or a Slack card;", number)+
			" a pick or a note on a ruling there is a capture: Right ticks every line with `rulings.py confirm <key> --tick all --capture <digest>`,"+
			" and Some wrong waits for the owner's note naming the lines",
		"", "",
	)
}

func (l *Landing) gatePass() {
	var advanced, quotaOff bool
	l.runner.PassIO.Phase("gate-setup", func() {
		if l.quotaLow() {
			quotaOff = true
			if rows := l.Rows(); rows != nil && rows.Len() > 0 {
				panic("TODO: port gate_pass's quota-off settlement (admission, close_settlement QUOTA_OFF)")
			}
			return
		}
		result := make(chan outcome, 1)
		go func() {
			result <- attempt(func() { advanced = l.advance() })
		}()
		if !l.refreshing.Load() {
			l.refresh()
		}
		if raised := <-result; raised.failure != nil {
			panic(raised.failure)
		}
	})
	if quotaOff {
		return
	}
	l.runner.Checkpoint()
	l.runner.PassIO.Phase("rows-and-gating", func() {
		rows := l.Rows()
		read := l.watchTrunk()
		if rows == nil {
			return
		}
		if l.statusCard {
			panic("TODO: port Landing.draw_card")
		}
		if rows.Len() == 0 {
			return
		}
		l.gateRows(rows, advanced, read)
	})
}

func (l *Landing) gateRows(rows *pyjson.Object, advanced, read bool) {
	panic(fmt.Sprintf("TODO: port gate_pass over %d ledger rows (advanced %t, trunk read %t): row_records, trunk_queue, carry_taps, held, admission, gate_rows, close_settlement, overdue", rows.Len(), advanced, read))
}

func (l *Landing) landingConfig() map[string]any {
	landing, _ := plain(readJSON(l.runner.Config.Source)).(map[string]any)["landing"].(map[string]any)
	return landing
}

func (l *Landing) watchTrunk() bool {
	if !truthy(l.landingConfig()["trunk_check"]) {
		return true
	}
	panic("TODO: port Landing.watch_trunk over landing.trunk_check")
}

type pool struct {
	name      string
	limit     int64
	remaining int64
	reset     time.Time
	callers   []any
}

func (p pool) key() string {
	return fmt.Sprintf("%s:%d", strings.ReplaceAll(p.name, " ", "-"), p.reset.Unix())
}

func quotaPools(identities ...*pyjson.Object) []pool {
	order := []string{}
	pools := map[string]pool{}
	for _, identity := range identities {
		for _, item := range member(identity, "pools").([]any) {
			entry := item.(*pyjson.Object)
			resource := member(entry, "resource").(string)
			if resource != quotaResources[0] && resource != quotaResources[1] {
				continue
			}
			name := member(entry, "identity").(string) + " " + resource
			if _, seen := pools[name]; !seen {
				order = append(order, name)
			}
			pools[name] = pool{name, member(entry, "limit").(int64), member(entry, "remaining").(int64), must(time.Parse(time.RFC3339, member(entry, "resetAt").(string))), member(entry, "callers").([]any)}
		}
	}
	listed := make([]pool, len(order))
	for i, name := range order {
		listed[i] = pools[name]
	}
	return listed
}

func kitchen(moment time.Time) string { return moment.In(pacific).Format("3:04 PM") }

func (l *Landing) quotaLow() bool {
	done := l.shell.Run([]string{"ccx", "vcs", "auth", "status", l.repo, "--json"}, nil, nil, "")
	if done.Code != 0 {
		lines := shell.SplitLines(strings.TrimSpace(or(done.Err, done.Out)))
		last := fmt.Sprintf("exit %d", done.Code)
		if len(lines) > 0 {
			last = lines[len(lines)-1]
		}
		l.runner.Escalate("quota-read:"+l.runner.Now().Format("2006010215"), "QUOTA-READ-FAILED", "landing", fmt.Sprintf("the GitHub quota read failed: %s; the pass runs without it", last), "", "")
		return false
	}
	listed := must(pyjson.Loads(done.Out)).([]any)
	if len(listed) != 1 {
		panic(fmt.Sprintf("ValueError: expected 1 auth status, got %d", len(listed)))
	}
	auth := listed[0].(*pyjson.Object)
	reads, writes := member(auth, "reads").(*pyjson.Object), member(auth, "writes").(*pyjson.Object)
	for _, pool := range quotaPools(reads, writes) {
		if pool.remaining >= QuotaUrgent {
			continue
		}
		var named []string
		for _, item := range pool.callers[:min(TopCallers, len(pool.callers))] {
			caller := item.(*pyjson.Object)
			named = append(named, fmt.Sprintf("%v %v", member(caller, "caller"), member(caller, "requests")))
		}
		l.runner.Escalate("github-quota:"+pool.key(), "GITHUB-QUOTA-LOW", "github", fmt.Sprintf("the %s quota has %d of %d calls left and resets at %s; top callers: %s", pool.name, pool.remaining, pool.limit, kitchen(pool.reset), or(strings.Join(named, ", "), "none recorded")), "", "")
	}
	low := false
	for _, pool := range quotaPools(reads) {
		if pool.remaining >= QuotaFloor {
			continue
		}
		low = true
		l.runner.Escalate("github-backoff:"+pool.key(), "GITHUB-BACKOFF", "landing", fmt.Sprintf("GitHub's %s quota has %d of %d calls left, under ccx's %d-call floor; the desk reads nothing from GitHub until it resets at %s, and accepted enqueues and root go records run after the reset", pool.name, pool.remaining, pool.limit, QuotaFloor, kitchen(pool.reset)), "", "")
	}
	return low
}

func (l *Landing) refresh() {
	l.ledgerPy("refresh", "--repo", l.repo, "--ledger", l.ledger)
	l.ledgerPy("reconcile", "--repo", l.repo, "--ledger", l.ledger, "--checkout", l.checkout)
}

// KeepFresh is Landing.keep_fresh.
func (l *Landing) KeepFresh() {
	l.refreshing.Store(true)
	l.keepers.Add(1)
	go func() {
		defer l.keepers.Done()
		defer l.refreshing.Store(false)
		for !l.closed() {
			started := l.shell.Monotonic()
			l.refresh()
			l.shell.Wait(l.stopped, max(0, RefreshEvery.Seconds()-(l.shell.Monotonic()-started)))
		}
	}()
}

func (l *Landing) closed() bool {
	select {
	case <-l.stopped:
		return true
	default:
		return false
	}
}

func realpath(path string) string {
	absolute := must(filepath.Abs(path))
	if resolved, err := filepath.EvalSymlinks(absolute); err == nil {
		return resolved
	}
	return absolute
}

func samefile(a, b string) bool {
	left, err := os.Stat(a)
	if err != nil {
		return false
	}
	right, err := os.Stat(b)
	return err == nil && os.SameFile(left, right)
}

func (l *Landing) advance() bool {
	git := []string{"git", "-C", l.checkout}
	run := func(argv ...string) shell.Done { return l.shell.Run(append(append([]string{}, git...), argv...), nil, nil, "") }
	done := run("fetch", "-q", "--no-tags", "--refmap=", "origin", "refs/heads/"+Trunk)
	if done.Code == 0 {
		done = run("worktree", "list", "--porcelain")
	}
	if done.Code == 0 {
		var mine map[string]string
		for _, entry := range strings.Split(strings.TrimSpace(done.Out), "\n\n") {
			tree := map[string]string{}
			for _, line := range shell.SplitLines(entry) {
				name, value, _ := strings.Cut(line, " ")
				tree[name] = value
			}
			path := tree["worktree"]
			_, checkoutErr := os.Stat(l.checkout)
			_, pathErr := os.Stat(path)
			if realpath(path) == realpath(l.checkout) || (pathErr == nil && checkoutErr == nil && samefile(path, l.checkout)) {
				mine = tree
				break
			}
		}
		if mine == nil || strings.Trim(mine["HEAD"], "0") == "" {
			panic("TODO: port Landing.rebuild for a checkout its repository does not list or one on a branch with no commit")
		}
		done = run("merge", "-q", "--ff-only", "FETCH_HEAD")
	}
	if done.Code != 0 {
		digest := sha1.Sum([]byte(done.Err))
		l.runner.Escalate("checkout:"+hex.EncodeToString(digest[:])[:8], "CHECKOUT-STALE", "landing", fmt.Sprintf("%s did not fast-forward to origin/%s, so this pass gated and enqueued nothing: %s", l.checkout, Trunk, strings.TrimSpace(done.Err)), "", "")
	}
	return done.Code == 0
}

// Drain is Landing.drain: with no stack worker, side work or worktree removal ported, there is nothing to wait for.
func (l *Landing) Drain() {}

// PostState is Landing.post_state.
func (l *Landing) PostState(took float64) {
	open := 0
	for _, pr := range l.ledgerRows.Keys() {
		state, has := member(member(l.ledgerRows, pr).(*pyjson.Object), "state").(string)
		if !has || state == "open" {
			open++
		}
	}
	if open > 0 {
		panic("TODO: port post_state over open rows: refused_members, the queued and waiting counts, the oldest step")
	}
	held := map[string]bool{}
	for _, pr := range l.heldNow {
		held[pr] = true
	}
	text := fmt.Sprintf("LANDING queued %d, waiting %d, held %d, refused %d, in flight %d; pass %.0fs", 0, 0, len(held), 0, 0, took)
	l.runner.CciPost("state", text, l.runner.Watchdog(), "landing-state", "landing-state:"+l.runner.Book.Stamp(), "", "", "", nil, "", "")
}

// Close is Landing.close.
func (l *Landing) Close() {
	l.stopping.Do(func() { close(l.stopped) })
	l.keepers.Wait()
}

// KeepOverflowing is Landing.keep_overflowing.
func (l *Landing) KeepOverflowing() {
	panic("TODO: port Landing.keep_overflowing and Landing.overflow")
}
